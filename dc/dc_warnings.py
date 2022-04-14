#!/usr/bin/env python3


# (mheim) 04_12_2022 reflection:
# written in October 2021. This script was fun to write, especially the part where
# we condense the set of meanings for one tag to the non-unique components. It ended
# up working perfectly, where if there was 1 meaning, the whole specific meaning
# would be preserved. Whereas if there were 100 meanings for a given tag, for example,
# you would see a lot more of a generic meaning. An example of a generic meaning would
# be something like "in design X, input port X is unconnected". 

################################################################################
#
# dc_warnings.py
#   - Responsible for organizing DC warnings generated in an RTL block directory,
#     or any arbitrary directory that the user enters.
#
#   - Generates a table formatted in markdown for each warning tag with its occurences
#
################################################################################

from pathlib import Path
import pandas as pd
import os
import re

import argparse
parser = argparse.ArgumentParser(description='Organize DC warnings for all reports for one RTL block.' \
                                 ' Looks through all files within RTL dir that has .log or rpt in the filename.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-b', '--block', help='Supply an RTL block directory name (ex. "<subblock>")')
parser.add_argument('-p', '--path', type=str, help='Optionally use any path to a directory to look through for warnings. ' \
                    'No need to supply an RTL block name.')

import logging
log_handlers = [logging.StreamHandler()]
if __name__ == '__main__':
    # if script is imported, no need to create a 0-byte logfile
    log_handlers += [logging.FileHandler("dc_warnings.log", mode='w')]
logging.basicConfig(format=('%(asctime)s [%(levelname)s]'
                            ' %(filename)s:%(lineno)d: %(message)s'),
                    datefmt='[%H:%M:%S]',
                    level=logging.INFO,
                    handlers=log_handlers)
logger = logging.getLogger(__name__)

class one_dc_logfile_record():
    def __init__(self, block_name=None, path=None):
        assert(block_name is not None or path is not None), 'Need a place to look for report files.'

        self.block_name = block_name

        self.warning_dict = {} # format {Tag, Count}

        self.warning_count = 0

        self.files_with_warn_type = {} # format {Tag, Files (set)}

        # tag meaning: the idea here is to collect all the meanings
        # we can find, and then condense it down to one string with
        # only the non-unique parts.
        # The description we get will be the text in between 'Warning:'
        # and the tag in parentheses.
        self.all_tag_meanings = {} # format {Tag, set of descriptions}

        self.condensed_tag_meanings = {} # format {Tag, condensed description}

        self.condense_substitute = 'X' # string to put in for differing parts

        # put report in same directory as -p path
        if path is not None:
            self.report_name = Path(f'{path}/dc_warnings_report.log').resolve()

        if path is not None:
            # if some path arg is specified, use that
            self.block_dir = Path(path).resolve()
        else:
            # if path arg is not specified, use the block argument for reports dir
            self.find_reports_dir()


    def find_reports_dir(self):
        '''If RTL block is specified, find the reports/ directory'''

        self.hw_dir = f'<removed>'
        self.block_dir = Path(f'{self.hw_dir}{args.block}/rtl')
        report_dir = Path(f'{self.block_dir}/reports')
        # assert that we have an existing reports/ dir
        assert(report_dir.is_dir()), \
            f'No report directory found at: {report_dir}'

        logger.info(f'block report dir = {report_dir}')

        self.report_name = f'{self.block_name}_dc_warnings_report.log'

    def organize_warnings(self):
        '''
        get every file in RTL dir, or path, that has 'Warning' in it. Keep track of
        the warning count for each warning tag and store each description found of that
        warning tag.
        Return how many warnings were found.
        '''

        files_with_warnings = []

        all_files = [file for file in self.block_dir.glob('**/*') \
                     if not file.is_dir()]

        # only include .log files and files with 'rpt' in the name
        files_with_warnings = [file for file in all_files \
                               if 'Warning' in open(file, encoding = "ISO-8859-1").read() \
                               and ('.log' in str(file) or 'rpt' in str(file))]

        logger.info('Files with warnings:')
        for file in files_with_warnings:
            logger.info(file)

        # open each file and add each warning line to warning table
        logger.info('Finding warnings...')
        for logfile in files_with_warnings:
            logger.info(f'Looking through {str(logfile)}')
            with logfile.open() as fhandle:
                for line in fhandle:
                    # first group is description, second group is warning code (I call it 'tag')
                    warning_search = re.search('Warning: (.*)\((.*)\)$', line)
                    if warning_search:
                        self.warning_count += 1
                        meaning = warning_search.group(1)
                        tag = warning_search.group(2)
                        if tag not in self.warning_dict.keys():
                            self.warning_dict[tag] = 1
                            logger.info(f'\t\tNew tag found: {tag}.')
                        else:
                            self.warning_dict[tag] += 1
                        if tag not in self.files_with_warn_type.keys():
                            self.files_with_warn_type[tag] = {str(logfile)}
                        else:
                            if (str(logfile) not in self.files_with_warn_type[tag]):
                                logger.info(f'\t\tFor tag {tag}, file {str(logfile)} added.')
                            self.files_with_warn_type[tag].add(str(logfile))
                        if tag not in self.all_tag_meanings.keys():
                            self.all_tag_meanings[tag] = {meaning}
                        else:
                            self.all_tag_meanings[tag].add(meaning)

        logger.info(f'Total Warnings Found: {self.warning_count}')
        return self.warning_count

    def condense_warning_meanings(self):
        '''take all tag meanings dict and fill the condensed tag meanings dict'''

        logger.info('Condensing warning descriptions...')
        for tag in self.all_tag_meanings.keys():
            list_of_lists_of_words = [sentence.split(' ') for sentence in self.all_tag_meanings[tag]]

            # remove empty strings, space-only strings
            for sentence in list_of_lists_of_words:
                sentence[:] = [word for word in sentence if word.strip()]

            # just start with the first meaning and condense down from there.
            # Doesn't need to start with the longest meaning.
            start_words = list_of_lists_of_words[0]
            words_set = set(start_words)
            for meaning in list_of_lists_of_words:
                words_set &= set(meaning)

            final_meaning = sorted(words_set, key = lambda k : start_words.index(k))

            # if there was a word that came up more than once in the start_words,
            # it will be removed because of the conversion to a set. We need to add
            # back those duplicate words. The first occurrence will remain,
            # and subsequent ones will be removed.
            saw_dup_word = False
            word_places = {} # dict format {Word, list of places}
            for place, word in enumerate(start_words):
                if word not in word_places.keys():
                    word_places[word] = [place]
                else:
                    saw_dup_word = True
                    word_places[word].append(place)

            # display dup words, keep track of which indices to check
            indices_to_replace_words = set()
            if saw_dup_word:
                for word, places in word_places.items():
                    if len(places) > 1:
                        # indices to replace are all except the first one
                        for place in places[1:]:
                            indices_to_replace_words.add(place)

            for i in range(len(start_words)):
                if i >= len(final_meaning):
                    # some word(s) were removed from the start words, so add generic substitute.
                    # But, if this was an index meant to add back a duplicate word, do that instead.
                    if i in indices_to_replace_words:
                        final_meaning.insert(i, start_words[i])
                    else:
                        final_meaning.insert(i, self.condense_substitute)
                else:
                    if i in indices_to_replace_words:
                        # replace removed duplicate word so initial word matches final word in this place
                        final_meaning.insert(i, start_words[i])

                    elif start_words[i] != final_meaning[i]:
                        final_meaning.insert(i, self.condense_substitute)

            # now replace all consecutive generic substitutions with one generic part
            final_meaning = [word for place, word in enumerate(final_meaning) if place == 0 or word != final_meaning[place - 1]]

            final_meaning = ' '.join(final_meaning)
            logger.info(f'TAG: {tag}: Final Generic Meaning = {final_meaning}')

            self.condensed_tag_meanings[tag] = final_meaning


    def report_warnings(self):
        '''
        Take data of warning count and description for each warning tag and make a table in Markdown.
        Also make a table for which files were involved with a certain tag for each tag found
        '''

        warning_df = pd.DataFrame(columns=['Warning Count', 'Generic Description'], index=sorted(self.warning_dict.keys()))
        warning_df.index.name = 'Warning Tag'
        for tag, count in self.warning_dict.items():
            meaning = self.condensed_tag_meanings[tag]
            warning_df.loc[tag] = [count, meaning]

        files_df = pd.DataFrame(columns=['Files with Warning Type'], index=sorted(self.files_with_warn_type.keys()))
        files_df.index.name = warning_df.index.name
        for tag, files in self.files_with_warn_type.items():
            if len(files) > 1:
                # make list of filenames
                files_df.loc[tag] = ''.join([file+'<br/>' for file in files])
            else:
                # only one filename
                files_df.loc[tag] = list(files)[0]

        with open(self.report_name, 'w') as f:
            print(f'Directory: {self.block_dir}\n', file=f)
            if self.block_name is not None:
                print(f'RTL Block Used: {self.block_name}', file=f)
            print(f'\nTotal Warning Count: {self.warning_count}', file=f)
            print('\n***\n', file=f)
            print('\nWarning Table:\n', file=f)
            print(warning_df.to_markdown(), file=f)
            print('\n***\n', file=f)
            print(f'Files with Warnings:\n', file=f)
            print(files_df.to_markdown(), file=f)

        logger.info(f'Report made: {str(self.report_name)}')

if __name__ == '__main__':
    args = parser.parse_args()

    logfile_record = one_dc_logfile_record(args.block, args.path)

    _ = logfile_record.organize_warnings() # returns warning count

    logfile_record.condense_warning_meanings()

    logfile_record.report_warnings()

    logger.info('done.')
