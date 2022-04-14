#!/usr/bin/env python3


# (mheim) 04_11_2022 reflection:
# this script was created in July of 2021. This script had some good ideas,
# but could have been written better. I showed some improvement in my script
# writing when comparing with the DC scripts, for example, which were written
# a couple months later in October 2021. 



################################################################################
#
# spyglass_run_blocks.py
#   - Responsible for running Spyglass on all applicable HW blocks.
#   - Generates a common report
#
#  This script will look through each block in <removed> for Makefiles
#  that Spyglass can use. Any Makefile that begins with "Makefile_" and
#  ends with "_rtl" in it will be run as a sub-block.
#  There may be multiple runs, or sub-blocks, within a block.
#  This script looks at the number of fatals, errors, and warnings
#  that are found by Spyglass. It then groups the types of errors and warnings
#  found for each sub-block in two tables.
#
#  The final report (in Markdown) for the summary, errors and warnings
#  will be located in all_block_report.log.
#
#  It should be noted that this script generates a logfile for each block
#  in their own rtl directories (<sub-block>.out.log). This will show the exact
#  meaning of each error and warning.
#
#  If you think a block should be added, feel free to add to the default
#  -b, --blocks argument because that is the known full set.
#
#
################################################################################


import subprocess
import os
from pathlib import Path
import time
import pandas as pd
import numpy as np
import re

import logging
logging.basicConfig(format=('%(asctime)s [%(levelname)s]'
                            ' %(filename)s:%(lineno)d: %(message)s'),
                    datefmt='[%H:%M:%S]',
                    level=logging.INFO,
                    handlers=[
                        logging.FileHandler("spyglass_run_blocks.log", mode='w'),
                        logging.StreamHandler()
                    ])
logger = logging.getLogger(__name__)

import argparse
parser = argparse.ArgumentParser(description='Run Spyglass on all applicable HW blocks',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-b', '--blocks', nargs='*', default=['<removed>'],
                    help='Run one or more HW blocks - specify the RTL directory name. Defaults to running all applicable HW blocks.')
parser.add_argument('-sb', '--sub-blocks', nargs='*', default=[],
                    help='Optionally run one or more exclusive HW sub-blocks - specify the name between Makefile_ and _rtl.')
parser.add_argument('-l', '--list', action='store_true',
                    help='List block and sub-block options. No running')
parser.add_argument('-v', '--verbose', action='store_true',
                    help='Print the make output to stdout')
parser.add_argument('--lic_wait', type=int, default=30,
                    help='Wait for the vc_static license (in minutes)')
parser.add_argument('--no-make', action='store_true',
                    help='Disable running make, do everything else. Debug aid')
parser.add_argument('--force-report', action='store_true',
                    help='Force making a new report without all blocks. Debug aid')

class one_spyglass_record():
    def __init__(self):
        self.fatal_count = 0
        self.error_count = 0
        self.warning_count = 0

        self.fatal_dict = {} # format [Tag : Count]
        self.error_dict = {}
        self.warning_dict = {}

class spyglass_run_all_blocks():
    def __init__(self, block_set, exclusive_sub_block_list, no_make, no_report, verbose):


        self.blocks = block_set # names of HW dirs we will use

        self.exclusive_sub_block_set = set(exclusive_sub_block_list) # optional exclusivity

        self.records = {} # format [Block (str) : Record (one_spyglass_record)]

        self.hw_dir = f'<removed>'

        self.exclude_sub_block_names = {'<removed>'} # sub-blocks that we intend to skip.

        self.block_name_list = [] # list of block entries based on what Makefiles we find

        self.script_dir = f'<removed>' # where we run

        self.report_file_name = 'all_block_report.log' # name of report file

        self.error_tag_meaning = {} # format [Tag(str) : Description (str)]

        self.warning_tag_meaning = {} # format [Tag(str) : Description (str)]

        self.log_file_paths = []

        self.make_files = {} # format [RTL Dir (str) : Set of Makefile names (set of str)]

        self.filenames_dict = {} # format [FileName : [Error count, Warning count] (list of int)]

        # command-line args
        self.no_make = no_make
        self.no_report = no_report
        self.verbose = verbose

    def pick_up_makefiles(self):
        logger.info('---------- Picking up makefiles... ----------')
        for block in self.blocks:
            rtl_dir = f'{self.hw_dir}' + f'{block}/rtl'
            self.make_files[rtl_dir] = set() # init make file set
            os.chdir(rtl_dir)

            # get list of makefiles in dir
            # a block may have one or more makefiles
            # also, remove all the logfiles from the dir
            for file in os.listdir(rtl_dir):
                # exclude the .common makefiles
                # do not add trailing ~ files!
                if re.match('Makefile_\w+_rtl$', file):
                    sub_block_name = '_'.join(str(file).split('_')[1:-1])
                    # if we are specifying exclusive sub-blocks and this sub-block is not in that set,
                    # exclude it
                    if len(self.exclusive_sub_block_set) and sub_block_name not in self.exclusive_sub_block_set:
                        # add to exclude set
                        self.exclude_sub_block_names.add(sub_block_name)

                    if sub_block_name not in self.exclude_sub_block_names:
                        logger.info(f'Add sub-block\t\t{sub_block_name}')
                        self.make_files[rtl_dir].add(str(file))
                    else:
                        logger.info(f'Exclude sub-block\t\t{sub_block_name}')

    def run(self):
        # go through each rtl dir and run each makefile
        # run make -f <Makefile> vc_static on each sub-block
        logger.info(f'Preparing to run blocks: {self.blocks}')
        for rtl_dir in self.make_files.keys():
            os.chdir(rtl_dir)
            # if there is a session.lock file, remove it
            lock_file = Path(f'{rtl_dir}/vcst_rtdb/session.lock')
            if lock_file.is_file():
                logger.info('removing session.lock file')
                lock_file.unlink()
            for make_file in self.make_files[rtl_dir]:
                # use the makefile's block name, there might be multiple for one block
                make_file_block_name = '_'.join(make_file.split('_')[1:-1])

                # if this name is one that we should exclude, skip
                if (make_file_block_name in self.exclude_sub_block_names):
                    logger.info(f'Sub-block {make_file_block_name} specified to be excluded. Skip')
                    continue
                else:
                    self.block_name_list.append(make_file_block_name)

                if not self.no_make:
                    logger.info(f'Running make process {make_file} ...')
                    if self.verbose:
                        stdout = None
                        stderr = None
                    else:
                        # (mheim) not sure why PIPE doesn't print out, but that's fine I guess.
                        # could be due to the cwd being different.
                        # use -v verbose flag if you care about the output

                        # (mheim) 04_11_2022 reflection:
                        # this part could be fixed. I didn't completely understand subprocess stdout argument at the time
                        # of writing this script. That being said, doing it this way does suppress the output. There is
                        # a more standard way of suppressing the output that should be used here.

                        stdout = subprocess.PIPE
                        stderr = subprocess.PIPE
                    result = subprocess.run(f'module load vc_static; make -f {make_file} LIC_WAIT={args.lic_wait} vc_static',
                                            shell=True,
                                            stdout=stdout,
                                            stderr=stderr,
                                            universal_newlines=True,
                                            cwd=rtl_dir,
                                            check=True)
                else:
                    logger.info('skipping the make process')

                # okay, this might be dumb, but wait until the logfile shows up.
                # do I have to do this? probably not...

                # (mheim) 04_12_2022 reflection:
                # it's not like we're running Spyglass in the background. The logfile will exist after the subprocess call
                # completes. But this isn't an issue and didn't take long to write. I like to take extra precautions when possible.

                no_see_logfile = True
                timeout = 10 # seconds
                sleep_interval = 0.5
                time_waiting = 0
                log_file = Path(f'{rtl_dir}/{make_file_block_name}.out.log')
                while no_see_logfile:
                    # look for the makefile's log output, it's not for the block
                    if log_file.is_file():
                        logger.info(f'found the {make_file_block_name} logfile')
                        no_see_logfile = False
                    else:
                        logger.info(f'waiting for the logfile ({str(log_file)}) ...')
                        time_waiting += sleep_interval
                        if time_waiting >= timeout:
                            logger.error('Took too long to find the logfile. Exiting')
                            exit(1)
                        time.sleep(sleep_interval)

                # make a record class for the block
                self.records[make_file_block_name] = one_spyglass_record()

                # now, we have the logfile
                self.log_file_paths.append(log_file)
                if not self.no_report:
                    logger.info(f'opening logfile {log_file}')
                    with log_file.open() as fhandle:
                        # run through the summary part of the <block>.out.log file,
                        # populate each block's record class
                        in_summary_of_report = False
                        look_for_tag_meaning = False
                        total_count = 0
                        this_file_error_tags_set = set()
                        this_file_warning_tags_set = set()
                        done_with_file = False
                        no_more_error_filenames = False
                        for line in fhandle:
                            hit_first_total_line = False
                            if 'Management Summary' in line:
                                in_summary_of_report = True
                            if in_summary_of_report and 'Total' in line:
                                total_count += 1
                                hit_first_total_line = True
                            if total_count == 2 and in_summary_of_report:
                                # stop looking for F/E/W counts once we get to second 'Total'
                                in_summary_of_report = False
                                look_for_tag_meaning = True
                                logger.info('Done with summary, looking for descriptions')
                            if in_summary_of_report:
                                if hit_first_total_line:
                                    # if we are at first 'Total' line, put the numbers in the block's record
                                    totals_line_split = line.split()
                                    self.records[make_file_block_name].fatal_count = totals_line_split[1]
                                    self.records[make_file_block_name].error_count = totals_line_split[2]
                                    self.records[make_file_block_name].warning_count = totals_line_split[3]
                                    logger.info(f'({make_file_block_name}) Fatal count: {totals_line_split[1]}')
                                    logger.info(f'({make_file_block_name}) Error count: {totals_line_split[2]}')
                                    logger.info(f'({make_file_block_name}) Warning count: {totals_line_split[3]}')
                                if total_count == 1:
                                    # if we are below the first 'Total' line, we need to look for lines
                                    # that start with either 'error' or 'warning'
                                    line_split = line.split()
                                    if (len(line_split) >= 4):
                                        tag = line_split[2]
                                        count = line_split[3]
                                        if line_split[0] == 'fatal':
                                            self.records[make_file_block_name].fatal_dict[tag] = count
                                            logger.info(f'({make_file_block_name} Fatal) {tag} count: {count}')
                                            # not dealing with fatal descriptions for now
                                        elif line_split[0] == 'error':
                                            self.records[make_file_block_name].error_dict[tag] = count
                                            logger.info(f'({make_file_block_name} Error) {tag} count: {count}')
                                            this_file_error_tags_set.add(tag)
                                        elif line_split[0] == 'warning':
                                            self.records[make_file_block_name].warning_dict[tag] = count
                                            logger.info(f'({make_file_block_name} Warning) {tag} count: {count}')
                                            this_file_warning_tags_set.add(tag)
                            elif look_for_tag_meaning:
                                # if we see a line with a tag matching one in the set, get its description
                                # once we cover one tag, we can stop looking for that one
                                # if the key is already in tag_meaning, just skip it
                                # lazily go through each unique error and warning tag, there's not that many

                                # we also want to look for 'FileName', and count the number of errors and warnings
                                # for each entry we find, lookup if this tag is a warning or error
                                # increment error/warning count for this filename

                                # if we see 'warnings', should be done looking for errors
                                if 'warnings' in line:
                                    no_more_error_filenames = True
                                if not no_more_error_filenames:
                                    for tag in this_file_error_tags_set:

                                        is_tag_line = tag in line and 'Tag' in line

                                        # getting error description
                                        if is_tag_line and not tag in self.error_tag_meaning.keys():
                                            description = ' '.join(next(fhandle).split()[2:])
                                            # if we see the SignalUsageReport.rpt, it's not a valid description
                                            if 'SignalUsageReport.rpt' in description:
                                                continue
                                            # otherwise, add this description to match this tag
                                            self.error_tag_meaning[tag] = description

                                        # getting error filename - 4 lines after description
                                        if is_tag_line:
                                            no_more_error_filenames = False
                                            file_line = ''
                                            while 'FileName' not in file_line:
                                                file_line = next(fhandle)
                                                # Need to be careful of StopIteration here!
                                                # If we see BBox Tag, won't have a FileName! so move on to warnings
                                                if 'warnings' in file_line or 'BBox' in file_line:
                                                    # (mheim) not sure if this triggers correctly all the time,
                                                    # but just be careful
                                                    no_more_error_filenames = True
                                                    break
                                            if not no_more_error_filenames:
                                                filename = file_line.split()[2]
                                                # if we see SignalUsageReport.rpt, skip
                                                if 'SignalUsageReport.rpt' in filename:
                                                    continue
                                                if filename not in self.filenames_dict:
                                                    self.filenames_dict[filename] = [0, 0]
                                                # then increment error count
                                                self.filenames_dict[filename][0] += 1

                                # same deal as with the errors, but with warnings.
                                for tag in this_file_warning_tags_set:
                                    is_tag_line = tag in line and 'Tag' in line

                                    # getting warning description
                                    if is_tag_line and not tag in self.warning_tag_meaning.keys():
                                        description = ' '.join(next(fhandle).split()[2:])
                                        # if we see the SignalUsageReport.rpt, it's not a valid description
                                        if 'SignalUsageReport.rpt' in description:
                                            continue
                                        # otherwise, add this description to match this tag
                                        self.warning_tag_meaning[tag] = description

                                    # getting warning filename - 4 lines after description
                                    if is_tag_line:
                                        file_line = ''
                                        while 'FileName' not in file_line:
                                            file_line = next(fhandle)
                                            # if we ever see 'info' we're done looking
                                            # also, if we see a BBox Tag, won't have a FileName!
                                            # have to be careful with StopIteration
                                            if 'info' in file_line or 'BBox' in file_line:
                                                done_with_file = True
                                                break
                                        if not done_with_file:
                                            filename = file_line.split()[2]
                                            # if we see SignalUsageReport.rpt, skip
                                            if 'SignalUsageReport.rpt' in filename:
                                                continue
                                            if filename not in self.filenames_dict:
                                                self.filenames_dict[filename] = [0, 0]
                                            # then increment warning count
                                            self.filenames_dict[filename][1] += 1

                            # if we're done looking, exit this logfile
                            if done_with_file:
                                break

                    logger.info(f'Done with {make_file_block_name} logfile')

        if not self.no_report:
            # put the data together

            # summary table showing counts for each block
            summary_cols = ['Fatal Count', 'Error Count', 'Warning Count']
            summary_df = pd.DataFrame(columns=summary_cols, index=sorted(self.block_name_list))
            summary_df.index.name = 'Block'
            for block in self.block_name_list:
                data = {'Fatal Count': self.records[block].fatal_count,
                        'Error Count': self.records[block].error_count,
                        'Warning Count': self.records[block].warning_count}
                summary_df.loc[block] = data

            # error table: get list of all tags found, cols are tag, row is block, value is count

            # first, get unique list of tags and set those as the columns
            uniq_error_tags = set()
            for block in self.block_name_list:
                for key in self.records[block].error_dict.keys():
                    uniq_error_tags.add(key)

            error_df = pd.DataFrame(columns=sorted(uniq_error_tags), index=sorted(self.block_name_list))
            error_df.index.name = 'Block'

            for block in self.block_name_list:
                error_df.loc[block] = self.records[block].error_dict

            # change NaN to zeros - and convert all string numbers to int
            for col in error_df.columns:
                error_df[col] = pd.to_numeric(error_df[col], errors='coerce').fillna(0).astype(np.int64)

            # if there is a column with all 0s, remove it
            error_df = error_df.loc[:, (error_df != 0).any(axis=0)]

            # warning table: list of all tags, cols are tag, row is block, value is count
            uniq_warning_tags = set()
            for block in self.block_name_list:
                for key in self.records[block].warning_dict.keys():
                    uniq_warning_tags.add(key)

            warning_df = pd.DataFrame(columns=sorted(uniq_warning_tags), index=sorted(self.block_name_list))
            warning_df.index.name = 'Block'

            for block in self.block_name_list:
                warning_df.loc[block] = self.records[block].warning_dict

            # change NaN to zeros - and convert all string numbers to int
            for col in warning_df.columns:
                warning_df[col] = pd.to_numeric(warning_df[col], errors='coerce').fillna(0).astype(np.int64)
            warning_df.fillna(0, inplace=True)

            # if there is a column with all 0s, remove it
            warning_df = warning_df.loc[:, (warning_df != 0).any(axis=0)]

            # get the meaning of each tag

            error_tag_desc_df = pd.DataFrame(columns=['Description'], index=sorted(self.error_tag_meaning.keys()))
            error_tag_desc_df.index.name = 'Tag'
            for key, desc in self.error_tag_meaning.items():
                error_tag_desc_df.loc[key] = desc

            # warning description table
            warning_tag_desc_df = pd.DataFrame(columns=['Description'], index=sorted(self.warning_tag_meaning.keys()))
            warning_tag_desc_df.index.name = 'Tag'
            for key, desc in self.warning_tag_meaning.items():
                warning_tag_desc_df.loc[key] = desc

            # filename table - rows are filenames, columns are error count and warning count
            filename_df = pd.DataFrame(columns=['Error Count', 'Warning Count'], index=sorted(self.filenames_dict.keys()))
            filename_df.index.name = 'Filename'
            for key, counts in self.filenames_dict.items():
                filename_df.loc[key] = counts

            # save tables, change back to vc_static dir
            os.chdir(self.script_dir)

            logger.info('generating new report...')
            # if there is a report already, delete it
            report_file = Path(f'{self.script_dir}/{self.report_file_name}')
            if report_file.is_file():
                report_file.unlink()

            with open(self.report_file_name, 'a') as f:
                print('Summary Table:\n', file=f)
                print(summary_df.to_markdown(), file=f)
                print('\n Error Table:\n', file=f)
                print(error_df.to_markdown(), file=f)
                print('\n Warning Table:\n', file=f)
                print(warning_df.to_markdown(), file=f)
                print('\n\nError Tag Descriptions: \n', file=f)
                print(error_tag_desc_df.to_markdown(), file=f)
                print('\nWarning Tag Descriptions: \n', file=f)
                print(warning_tag_desc_df.to_markdown(), file=f)
                print('\n\nErrors and Warnings by File:\n', file=f)
                print(filename_df.to_markdown(), file=f)
            logger.info(f'Done creating report: {self.report_file_name}')

        else:
            logger.info('Not creating a report unless all blocks are specified')

        if not self.no_make:
            logger.info('Done. Here are the paths to the created logfiles:')
            for logfile in self.log_file_paths:
                logger.info(str(logfile))

if __name__ == "__main__":
    args = parser.parse_args()
    no_make = args.no_make or args.list
    # condense command-line specified blocks to set for safety
    block_set = set(args.blocks)

    full_block_set = set(parser.get_default('blocks'))

    # assert that we are running a block that exists in the full set
    assert(block_set.issubset(full_block_set)), \
        f'You specified a block that was not in the full set: {full_block_set}'

    # if we are not running the full block set, don't make a new report
    no_report = block_set != full_block_set

    if args.force_report:
        no_report = False

    test = spyglass_run_all_blocks(block_set, args.sub_blocks, no_make, no_report, args.verbose)

    if args.list:
        logger.info('---------- List of Blocks: ----------')
        for block in block_set:
            logger.info(f'\t\t\t\t{block}')

    test.pick_up_makefiles()

    if not args.list:
        test.run()
