#!/usr/bin/env python3


# (mheim) 04_12_2022 reflection:
# Written in October 2021. This script has a lot of functionality and I really
# like how the style came out. I tried to rely heavily on functions being called
# from the main script function. I had a bit of fun at times too - for example,
# creating the user input timeout class. Like that example, I experimented with some 
# things in Python that I hadn't tried before which was a good learning exercise.


################################################################################
#
# autorun_dc.py
#   - Responsible for automatically running DC with multiple hardware blocks
#   - Runs all combinations of constraints given as command-line arguments
#   - Automatically stores results by git commit and input constraints
#   - Emails summary of results for each job and after all jobs are finished
#   - Creates a table of PPA results in a CSV file
#
#
################################################################################

import subprocess
from pathlib import Path
import re
import os
import shutil
from datetime import datetime
import signal
import sys
import boto3
from botocore.exceptions import ClientError
import pwd
from random import random
import pandas as pd
import itertools

################ CONTROL VALUES ################################################
HW_DES_DIR = f'<removed>'
IP_DIR= f'<removed>'
ALL_TRACKS = ['7P5T', '8T', '9T', '12T']
VALID_GF12_TRACKS = ['7P5T', '9T']
VALID_GF22FDX_TRACKS = ['8T', '9T', '12T']
ALL_CORNERS = ['TT', 'SS'] # FF not supported yet
ALL_OPT_FLOWS = ['hplp', 'hc', 'rtm_exp'] # high perf. low power, high connect., runtime exploration
# don't have permission to mkdir in <dir>, so default to proj_root
RESULT_BASE_DIR = f'<removed>'
SCRIPT_DIR = f'<removed>'
################################################################################

import logging
log_handlers = [logging.StreamHandler()]
if __name__ == '__main__':
    # if script is imported, no need to create a 0-byte logfile
    log_handlers += [logging.FileHandler(f"{SCRIPT_DIR}/dc_autorun.log", mode='w')]
logging.basicConfig(format=('%(asctime)s [%(levelname)s]'
                            ' %(filename)s:%(lineno)d: %(message)s'),
                    datefmt='[%H:%M:%S]',
                    level=logging.INFO,
                    handlers=log_handlers)
logger = logging.getLogger(__name__)

# If dc_warnings are imported before this logger is set up,
# the dc_warnings logger gets setup first and all the logs go there.
# Now, if that script is imported, which it is here, no logfile is created.
import dc_warnings

def get_username():
    return pwd.getpwuid(os.getuid()).pw_name

import argparse
parser = argparse.ArgumentParser(description='Run DC with varying blocks and configuration settings. This program may ask for user input, ' \
                                 'such as confirming the commands to be run. Use the -f switch to disable that behavior.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('-l', '--list-blocks',
                    action='store_true',
                    help='List blocks and sub-blocks. Each block is a directory under <dir>. Any sub-block may be ' \
                    'used under <dir> directory if its Makefile follows the naming convention of Makefile_<subblock>_rtl.')

parser.add_argument('-b', '--block',
                    nargs='*',
                    default=[],
                    help='Run one or more RTL blocks - specify the RTL directory name. Specify "all" to run all blocks. ' \
                    'To run <block>, specify "top".')

parser.add_argument('-sb', '--sub-block',
                    nargs='*',
                    default=[],
                    help='Run one or more RTL sub-blocks - specify the name in between Makefile_ and _rtl. ' \
                    'If blocks are also specified, adds sub-blocks to run. ' \
                    'You can run <block> with "<block>".')

# Maybe add switch to run both gf22fdx and gf12 if it's useful to compare results between the two
parser.add_argument('--tech',
                    type=str,
                    choices=('gf12', 'gf22'),
                    default='gf12',
                    help='Run Make process with either gf22fdx or gf12.')

parser.add_argument('--stdcell-track',
                    choices=ALL_TRACKS + ['all'],
                    nargs='*',
                    default=['9T'],
                    help='Choose one or more stdcell tracks, or all. ' \
                    f'Valid GF12 tracks: {", ".join(VALID_GF12_TRACKS)}, ' \
                    f'Valid GF22FDX tracks: {", ".join(VALID_GF22FDX_TRACKS)}')

parser.add_argument('--corner',
                    choices=ALL_CORNERS + ['all'],
                    nargs='*',
                    default=['TT'],
                    help='Choose one or more corners, or all.')

parser.add_argument('--freq',
                    type=float,
                    default=[1.0],
                    nargs='*',
                    help='Choose one or more target freqs (GHz)')

parser.add_argument('--enable-boundary-optimization',
                    choices=('True', 'False', 'both'),
                    nargs=1,
                    default=['True'],
                    help='By default, boundary optimization is enabled. You can choose to run with both on and off.')

parser.add_argument('--enable-retime',
                    choices=('True', 'False', 'both'),
                    nargs=1,
                    default=['True'],
                    help='Enable or disable the -retime option. Both options may be chosen.')

parser.add_argument('--optimization-flow',
                    choices=ALL_OPT_FLOWS + ['all'],
                    nargs='*',
                    default=['hplp'],
                    help='Choose one or more DC optimization flow(s): either high performance low power, high connectivity, or ' \
                    'runtime exploration.')

parser.add_argument('--reduced-effort',
                    choices=('True', 'False', 'both'),
                    nargs='?',
                    default=['False'],
                    const=['True'],
                    help='Choose to run DC with reduced effort optimization flow.')

parser.add_argument('-t', '--enable-topo-mode',
                    choices=('True', 'False', 'both'),
                    nargs=1,
                    default=['False'],
                    help='By default, topo mode is disabled. You can choose to run with both on and off.')

parser.add_argument('-r', '--result-dir',
                    type=str,
                    default=RESULT_BASE_DIR,
                    help='Store the results in some base directory. Inside the base dir will be the git commit and sub-block name as a sub-directory.')

parser.add_argument('-d', '--dry-run',
                    action='store_true',
                    help='Display commands to be run, and do not run make.')

parser.add_argument('-f', '--force-run',
                    action='store_true',
                    help='Disable all user prompts.')

parser.add_argument('-e', '--email-address',
                    type=str,
                    default=f'{get_username()}@<domain>',
                    help='Specify your email address. Defaults to user\'s email address. To enable email, use the --email-when-done switch.')

parser.add_argument('--email-when-done',
                    choices=('none', 'all', 'each', 'both'),
                    nargs=1,
                    default=['none'],
                    help='Send email either after each Make job is finished, or when all runs are finished. ' \
                    'You may choose both options. For "each" option, the email will tell you if a run failed to complete. ' \
                    'For "all" option, an email is sent after all jobs are finished giving information on what runs succeeded and failed. ' \
                    'Note that you may need to click the "..." in Gmail to view trimmed content.')

parser.add_argument('--only-make-table',
                    action='store_true',
                    help='Instead of running make, only run post-processing to get CSV table of PPA results. Use -r to specify the base result ' \
                    'directory to look through for DC reports. Useful for regenerating PPA table after multiple runs.')

parser.add_argument('-o', '--out-table',
                    type=str,
                    help='Specify a path or name for the output CSV table of PPA results. Defaults to putting the CSV table in the ' \
                    'base result directory. Useful for when results are write-protected.')

parser.add_argument('--debug-mode',
                    type=int,
                    help='Intended for testing script functionality. Instead of running Make, return a 0 return code some percent of the time.')

def get_all_makefiles():
    '''Return a list of makefiles found within the <dir> dir. Sub-block name will
    be between Makefile_ and _rtl'''

    hw_des_path = Path(HW_DES_DIR)
    makefiles = [file for file in hw_des_path.glob('**/*')
                 if not file.is_dir() and re.match('.*/Makefile_\w+_rtl$', str(file))]
    return makefiles

def get_makefiles_in_blocks(block_list):
    '''Return list of Paths to makefiles from list of HW RTL dirs. Include special case for <top> block.'''
    makefiles = []
    for block in block_list:
        rtl_dir = Path(f'{IP_DIR}/{block}/rtl')
        makefiles += [file for file in rtl_dir.glob('*') if re.match('.*/Makefile_\w+_rtl$', str(file))]
    return makefiles

def get_makefiles_in_sub_blocks(sub_block_list):
    '''Return list of Paths to makefiles from list of sub-block names'''
    all_makefiles = get_all_makefiles()
    sub_block_makefiles = []
    for sub_block in sub_block_list:
        for makefile in all_makefiles:
            if re.match(f'.*/Makefile_{sub_block}_rtl$', str(makefile)):
                sub_block_makefiles.append(makefile)
    return sub_block_makefiles

def get_one_sub_block_name(makefile):
    '''get one sub block name from input makefile path'''
    sub_block_search = re.search('.*/Makefile_(\w+)_rtl$', str(makefile))
    if sub_block_search:
        return sub_block_search.group(1)
    else:
        # some error behavior here
        return None

def get_sub_block_names(makefiles):
    '''Return a set of all sub-block names given a list of makefile paths.'''
    return set([get_one_sub_block_name(makefile) for makefile in makefiles])

def get_all_blocks():
    '''Return a list of all HW directories within <top> dir.'''
    stella_path = Path(IP_DIR)
    hw_dirs = [str(file).split('/')[-1] for file in stella_path.glob('*')
               if file.is_dir()]

    return hw_dirs

def get_block_dir_from_makefile(makefile):
    '''Return block directory name from input makefile path'''
    # ex. input is <path> -> return <block>
    return str(makefile).split('/')[-3]

def get_sub_block_from_result_dir(result_dir):
    '''Return sub_block name from result_dir Path object'''
    # result_dir will look something like this:
    # <removed>

    # go through each sub_block and see if it matches the result directory
    # sort by length because otherwise we might match <subblock> when we want <longer_subblock_name>, for ex.
    all_sub_blocks = sorted(get_sub_block_names(get_all_makefiles()), key=len)
    sub_block_match = None
    for sub_block in all_sub_blocks:
        if sub_block in str(result_dir).split('/')[-1]:
            sub_block_match = sub_block
            # don't break, might get a match with something more specific

    return sub_block_match

def gen_tcl_file(makefile_path,
                 stdcell_arg,
                 corner_arg,
                 en_gf12,
                 freq_ghz_arg,
                 en_boundary_opt_arg,
                 optimization_flow,
                 reduced_effort_arg,
                 en_retime_arg,
                 dry_run=False):
    '''Create top block tcl file with constraints. Called by run_make primarily for <top>.'''

    # get sub-block name
    top_name = get_one_sub_block_name(makefile_path)

    # we could have one string with newlines, but I'd like to write each line out for readability

    # --- control ---
    stdcell = stdcell_arg
    corner = corner_arg
    freq_ghz = str(freq_ghz_arg)

    # figure out extra_ultra_args
    extra_ultra_args_list = []
    if not en_retime_arg:
        extra_ultra_args_list += ['-retime']
    if not en_boundary_opt_arg:
        extra_ultra_args_list += ['-no_boundary_optimization -no_autoungroup']

    # combine extra_ultra_args list elements -> string
    extra_ultra_args = " ".join(extra_ultra_args_list)

    # display control values
    logger.info(f'{top_name} control values:')
    logger.info(f'\tSTDCELL_VAR = {stdcell}')
    logger.info(f'\tAX_CORNER = {corner}')
    logger.info(f'\tCORE_FREQ_GHZ = {freq_ghz}')
    if en_gf12:
        logger.info('\tGF12 = 1')
    logger.info(f'\tDC_OPTIMIZATION_FLOW = {optimization_flow}')
    if reduced_effort_arg:
        logger.info('\tDC_REDUCED_EFFORT = 1')

    logger.info(f'EXTRA_ULTRA_ARGS = "{extra_ultra_args}"')

    parent_dir = '/'.join(str(makefile_path).split('/')[:-1])
    tcl_file = f'{parent_dir}/{top_name}.tcl'

    # --- set lines to write ---
    top_line = f'set TOP "{top_name}"'
    pkg_srcs_line = 'set PKG_SOURCES "<removed>"'
    src_common_tcl_line = 'source "<removed>"'
    freq_line = f'set CORE_FREQ_GHZ {freq_ghz}'
    stdcell_line = f'set STDCELL_VAR {stdcell}'
    corner_line = f'set AX_CORNER {corner}'
    gf12_line = 'set GF12 1'
    opt_flow_line = f'set DC_OPTIMIZATION_FLOW {optimization_flow}'
    reduced_effort_line = f'set DC_REDUCED_EFFORT = {int(reduced_effort_arg)}'

    ref_line = f'source "<removed>"'

    # By default, boundary optimization enabled. Add line if disabled.

    # set EXTRA_ULTRA_ARGS (not appending to it)
    extra_ultra_args_line = f'set EXTRA_ULTRA_ARGS "{extra_ultra_args}"'

    if not dry_run:
        # --- write lines to file ---
        with open(tcl_file, 'w') as f:
            print(top_line, file=f)
            print(pkg_srcs_line, file=f)
            print(src_common_tcl_line, file=f)
            print(freq_line, file=f)
            print(stdcell_line, file=f)
            print(corner_line, file=f)
            if en_gf12:
                print(gf12_line, file=f)
            print(opt_flow_line, file=f)
            if reduced_effort_arg:
                print(reduced_effort_line, file=f)
            # if extra_ultra_args is empty, don't put anything. The run will fail otherwise, I think
            if len(extra_ultra_args_list):
                print(extra_ultra_args_line, file=f)
            print(ref_line, file=f)
        logger.info(f'Wrote to {str(tcl_file)}')

def run_make(makefile_path,
             stdcell_arg,
             corner_arg,
             en_gf12,
             freq_arg,
             en_boundary_opt_arg,
             optimization_flow,
             en_reduced_effort_arg,
             en_topo_arg,
             en_retime_arg,
             debug_mode,
             debug_pass_pct,
             dry_run=False):
    '''
    Run make on input Makefile path. Also generate TCL file with constraints for <top> block.
    Return a tuple of (return code (int), make_cmd (string))
    '''

    # chdir
    parent_dir = '/'.join(str(makefile_path).split('/')[:-1])
    cwd = os.getcwd()
    if (parent_dir != cwd):
        os.chdir(parent_dir)
        logger.info(f'chdir {str(parent_dir)}')

    # run
    makefile_name = str(makefile_path).split('/')[-1]
    make_cmd = ['module load dc;', f'make -f {makefile_name}']

    # for 'top' block, generate tcl file.
    if get_block_dir_from_makefile(makefile_path) == 'top':
        gen_tcl_file(makefile_path,
                     stdcell_arg,
                     corner_arg,
                     en_gf12,
                     freq_arg,
                     en_boundary_opt_arg,
                     optimization_flow,
                     en_reduced_effort_arg,
                     en_retime_arg,
                     dry_run=dry_run)

    # for all other blocks, override cmd line args.
    # Uses <common Makefile> control values
    else:
        make_cmd += [f'DC_STDCELL_VAR={stdcell_arg}']
        make_cmd += [f'DC_AX_CORNER={corner_arg}']
        make_cmd += [f'DC_CORE_FREQ_GHZ={freq_arg}']

        # extra ultra args
        # By default, both retime and boundary optim. are enabled
        # always override DC_EXTRA_ULTRA_ARGS, it's easier to deal with
        extra_ultra_args = []

        if en_retime_arg:
            # if enabled, add -retime. If disabled, don't add anything
            extra_ultra_args += ['-retime']

        if not en_boundary_opt_arg:
            # if disabled, add argument. If enabled, don't add anything
            extra_ultra_args += ['-no_boundary_optimization -no_autoungroup']

        # add extra ultra args to make_cmd
        # this variable should not be overriden in makefile
        # But, what if this is empty? As in, no retime and boundary optim. enabled. Will it work?
        # (mheim) 04_12_2022 reflection: seems like this did not have any problems
        make_cmd += [f'DC_EXTRA_ULTRA_ARGS="{" ".join(extra_ultra_args)}"']

        if en_gf12:
            make_cmd += ['GF12=1']
        make_cmd += [f'DC_OPTIMIZATION_FLOW={optimization_flow}']
        make_cmd += [f'DC_REDUCED_EFFORT={int(en_reduced_effort_arg)}']

    # NOTE: tcl file doesn't have topo mode. Specify on cmd line
    if en_topo_arg:
        make_cmd += ['DC_USE_TOPO=1']
        # default is disabled, no extra args needed

    # then add dc target at end
    make_cmd += ['dc']

    # convert list to string with spaces
    make_cmd_str = ' '.join(make_cmd)

    if dry_run:
        ### Dry Run
        logger.info(f'cmd: {make_cmd_str}')
        rc = 0
    elif debug_mode:
        ### Debug Mode
        num = int(random() * 100)
        if num <= debug_pass_pct:
            rc = 0
        else:
            rc = 1
    else:
        ### Real Make Run
        rc = subprocess.run(make_cmd_str, shell=True)
        rc = rc.returncode
    return (rc, make_cmd_str)

def get_git_commit(makefile_path):
    '''
    Return git commit string from input makefile path. Found after DC run.
    Intended to be run once to get a git commit for the whole set of DC runs.
    '''
    parent_dir = '/'.join(str(makefile_path).split('/')[:-1])
    # search for <sub-block>.dc.stdout.log or <sub-block>.dc.out.log
    sub_block_name = get_one_sub_block_name(makefile_path)

    # it's ok if we can't find a git commit, return no_commit if that is the case
    git_commit = 'no_git_commit'

    logfiles = [file for file in Path(parent_dir).glob('*')
                if re.match(f'.*/{sub_block_name}\.dc.*\.log', str(file))]

    if len(logfiles):
        # take first matching logfile we find. Should only be one.
        with open(logfiles[0], 'r') as f:
            for line in f:
                if re.search('GIT', line):
                    # git line looks like this:
                    # GIT version hash is <git hash>
                    # remove single quotes, newline
                    git_commit = re.sub("'", "", line.split(' ')[-1].rstrip())
                    break

    return git_commit

class input_timeout:
    def __init__(self, timeout=30):
        self.TIMEOUT = int(timeout) # seconds
        if self.TIMEOUT < 1:
            self.TIMEOUT = 1

    def input_interrupted(self, signum, frame):
        '''Called when user input times out'''
        logger.info('\nInput timeout reached. Continuing...')
        raise Exception

    def user_input_exit_handler(self, signum, frame):
        logger.info('\nExiting')
        # do not call sys.exit
        os._exit(1)

    def user_input_timeout(self, prompt):
        try:
            ans = input(f'{prompt} ({self.TIMEOUT} second timeout) ')
            return ans
        except:
            return None

    def run_input_timer(self, prompt=''):
        signal.signal(signal.SIGALRM, self.input_interrupted)
        signal.signal(signal.SIGINT, self.user_input_exit_handler)
        signal.alarm(self.TIMEOUT)
        ans = self.user_input_timeout(prompt)
        # disable alarm signal
        signal.alarm(0)
        return ans

def copy_dc_results(makefile_path,
                    result_dir):

    '''Copy DC results into result directory. Assume result directory was already created.'''

    sub_block_name = get_one_sub_block_name(makefile_path)
    parent_dir = '/'.join(str(makefile_path).split('/')[:-1])

    # try to store results

    # copy tcl file if it exists - <sub-block>.tcl

    # sub block tcl file looks like this:
    # <removed>

    # result_dir looks something like this:
    # <removed>

    if sub_block_name == '<top>':
        # tcl file will always be '<top>.tcl'
        tcl_file_name = '<top>.tcl'
    else:
        # freq convert 500 MHz -> 0.5GHz
        freq_search = re.search('_(\d+)([M|G]Hz)', str(result_dir))
        if freq_search:
            freq = freq_search.group(1)
            if freq_search.group(2) == 'MHz':
                freq = f'{int(freq)/1000}GHz'
            elif freq_search.group(2) == 'GHz':
                freq = f'{int(freq)/1}GHz'

        # corner, track search
        c_t_search = re.search(f'{sub_block_name}_\w+_(\w+)_(\w+)_\d+[M|G]Hz', str(result_dir))
        if c_t_search:
            track = c_t_search.group(1)
            corner = c_t_search.group(2)
        # put together
        logger.info(f'sub_block {sub_block_name}, freq {freq}, track {track}, corner {corner}')
        tcl_file_name = f'{sub_block_name}_{freq}_{track}_{corner}'
        if 'GF12' in str(result_dir):
            tcl_file_name += '_GF12LP'
        tcl_file_name += '.dc.tcl'

    logger.info(f'Looking for tcl file: {tcl_file_name}')

    try:
        tcl_files = [file for file in Path(parent_dir).glob('*')
                     if re.match(f'.*/{tcl_file_name}$', str(file))]
        # just take first match - really should only be one here
        if len(tcl_files):
            tcl_file = tcl_files[0]
            shutil.copy2(tcl_file, result_dir)
            logger.info(f'Copied tcl file {str(tcl_file)} to {str(result_dir)}')
        else:
            logger.warning('Did not find any tcl files.')
    except Exception as e:
        logger.warning(f'Could not copy {sub_block_name} tcl files: {e}')

    # copy logfiles
    try:
        logfiles = [file for file in Path(parent_dir).glob('*')
                    if re.match('.*/.*\.log', str(file))]
        for logfile in logfiles:
            shutil.copy2(logfile, result_dir)
            logger.info(f'Copied logfile {str(logfile)} to {str(result_dir)}')
    except Exception as e:
        logger.warning(f'Could not copy {sub_block_name} logfiles: {e}')

    # copy dc_src
    try:
        dc_src_dir = Path(f'{parent_dir}/dc_src')
        res_dc_src_dir = Path(f'{result_dir}/dc_src')
        if res_dc_src_dir.is_dir():
            shutil.rmtree(res_dc_src_dir)
            logger.info(f'Removed {str(res_dc_src_dir)}')
        shutil.copytree(dc_src_dir, res_dc_src_dir)
        logger.info(f'Copied dc_src dir {str(dc_src_dir)} to {str(result_dir)}/dc_src')
    except Exception as e:
        logger.warning(f'Could not copy {sub_block_name} dc_src directory: {e}')

    # copy reports/ dir
    try:
        reports_dir = Path(f'{parent_dir}/reports')
        res_reports_dir = Path(f'{result_dir}/reports')
        if res_reports_dir.is_dir():
            shutil.rmtree(res_reports_dir)
            logger.info(f'Removed {str(res_reports_dir)}')
        shutil.copytree(reports_dir, res_reports_dir)
        logger.info(f'Copied reports dir {str(reports_dir)} to {str(result_dir)}/reports')
    except Exception as e:
        logger.warning(f'Could not copy {sub_block_name} reports directory: {e}')

    # copy results/ dir
    try:
        run_results_dir = Path(f'{parent_dir}/results')
        res_run_results_dir = Path(f'{result_dir}/results')
        if res_run_results_dir.is_dir():
            shutil.rmtree(res_run_results_dir)
            logger.info(f'Removed {str(res_run_results_dir)}')
        shutil.copytree(run_results_dir, res_run_results_dir)
        logger.info(f'Copied results dir {str(run_results_dir)} to {str(result_dir)}/results')
    except Exception as e:
        logger.warning(f'Could not copy {sub_block_name} results directory: {e}')

def get_dc_error_info(makefile_path):
    '''
    Get the DC stdout logfile and grep for errors. Return lines (list of strings) where
    errors are found.

    We could look in the sub-block's RTL dir or the result dir, since we can assume
    that the results were already copied. We choose the former (RTL dir) here.
    '''
    sub_block_name = get_one_sub_block_name(makefile_path)

    # logfile looks like: <removed>
    logfiles = [file for file in Path(parent_dir).glob('*')
                if not file.is_dir() and re.match(f'.*/{sub_block_name}\.dc\.stdout\.log$', str(file))]

    error_info = []

    # should only be one logfile, take the first one
    if len(logfiles):
        stdout_logfile = logfiles[0]
        with open(stdout_logfile) as dc_logfile:
            try:
                for line in dc_logfile:
                    error_search = re.search('(^Error:.*)$', line)
                    if error_search:
                        error_line = error_search.group(1)
                        logger.info(f'Found error line: "{error_line}"')
                        error_info.append(error_line)
            except Exception as e:
                logger.error(f'ERROR: {e}')
    else:
        logger.warning('Did not find stdout logfile from DC.')

    return error_info

def email_results(sub_block_name,
                  status_str,
                  recipient_email,
                  body_text_additions=[]):
    '''
    Send an email through AWS SES SDK to some recipient email address,
    notifying them that their job is finished running. Input sub-block name (string),
    status_str (string) for appending to subject,
    result directory (string), receiver email (string),
    and optional additions to body text (list of strings).
    '''

    sender = 'autorun_dc.py <autorun_dc.py@<domain>>'

    aws_region = '<region>'

    subject = f'{sub_block_name} DC Run Finish Status: {status_str}'

    body_text = []
    if len(body_text_additions):
        body_text += body_text_additions
    body_text = '\n\n'.join(body_text)

    char_set = 'UTF-8'

    client = boto3.client('ses', region_name=aws_region)

    try:
        #Provide the contents of the email.
        response = client.send_email(
            Destination={
                'ToAddresses': [
                    recipient_email,
                ],
        },
        Message={
            'Body': {
                'Text': {
                    'Charset': char_set,
                    'Data': body_text,
                },
            },
            'Subject': {
                'Charset': char_set,
                'Data': subject,
            },
        },
        Source=sender)
    # Display an error if something goes wrong.
    except ClientError as e:
        logger.info(e.response['Error']['Message'])
    else:
        logger.info("Email sent! Message ID:")
        logger.info(response['MessageId'])

class one_power_group():
    '''
    This class holds 3 power values for Internal Power,
    Switching Power and Leakage Power. Keep track of units too
    '''
    def __init__(self, name, internal_power=0, switching_power=0, leakage_power=0):
        self.name = name

        self.internal_power = internal_power
        self.internal_power_unit = None

        self.switching_power = switching_power
        self.switching_power_unit = None

        self.leakage_power = leakage_power
        self.leakage_power_unit = None

    def __repr__(self):
        return f'Group: {self.name}\tInternal Power: {self.internal_power} {self.internal_power_unit}\t' \
        f'Switching Power: {self.switching_power} {self.switching_power_unit}\t' \
        f'Leakage Power: {self.leakage_power} {self.leakage_power_unit}'

    def unit_multiplier(self, unit, common_unit):
        '''Used by use_common_unit() to get the number to multiply or divide by to use the common unit'''
        # handle nW, uW, mW, W
        # this is really not necessary, but doesn't hurt
        if unit == 'nW' and common_unit == 'uW':
            return 1/1000
        elif unit == 'nW' and common_unit == 'mW':
            return 1/(1000*1000)
        elif unit == 'nW' and common_unit == 'W':
            return 1/(1000*1000*1000)
        elif unit == 'uW' and common_unit == 'nW':
            return 1000
        elif unit == 'uW' and common_unit == 'mW':
            return 1/1000
        elif unit == 'uW' and common_unit == 'W':
            return 1/(1000*1000)
        elif unit == 'mW' and common_unit == 'nW':
            return 1000*1000
        elif unit == 'mW' and common_unit == 'uW':
            return 1000
        elif unit == 'mW' and common_unit == 'W':
            return 1/1000
        elif unit == 'W' and common_unit == 'nW':
            return 1000*1000*1000
        elif unit == 'W' and common_unit == 'uW':
            return 1000*1000
        elif unit == 'W' and common_unit == 'mW':
            return 1000

    def use_common_unit(self, common_unit='mW'):
        '''For each power type, if unit does not match common one, change the number to match the common unit'''
        if self.internal_power_unit != common_unit:
            self.internal_power *= self.unit_multiplier(self.internal_power_unit, common_unit)
            self.internal_power_unit = common_unit
            logger.info(f'unit conversion: now internal_power = {self.internal_power} {self.internal_power_unit}')
        if self.switching_power_unit != common_unit:
            self.switching_power *= self.unit_multiplier(self.switching_power_unit, common_unit)
            self.switching_power_unit = common_unit
            logger.info(f'unit conversion: now switching_power = {self.switching_power} {self.switching_power_unit}')
        if self.leakage_power_unit != common_unit:
            self.leakage_power *= self.unit_multiplier(self.leakage_power_unit, common_unit)
            self.leakage_power_unit = common_unit
            logger.info(f'unit conversion: now leakage_power = {self.leakage_power} {self.leakage_power_unit}')

class sub_block_dc_results():
    def __init__(self, result_dir):
        # result_dir is Path object
        self.result_dir = result_dir

        self.sub_block = get_sub_block_from_result_dir(self.result_dir)
        logger.info(f'result sub_block = {self.sub_block}')

        # slack time for each path group: FEEDTHROUGH, REGIN, REGOUT, clk
        self.timing_by_path_group = {}

        # area by type: sequential, combinational, etc.
        self.area_by_type = {}

        # power by group: memory, clock_network, register, etc.
        self.power_by_group = {}

    def read_timing_file(self, timing_file):
        '''From input timing_file Path object, fill timing_by_path_group dict with
        timing data for each path group'''
        logger.info(f'Reading timing file {str(timing_file)}...')
        path_group = 'None'
        with open(timing_file) as t_file:
            try:
                for line in t_file:
                    # we can assume that the path group is found before the slack number
                    path_group_search = re.search('Path Group: (\w+)$', line)
                    if path_group_search:
                        path_group = path_group_search.group(1)
                    # number must be preceeded by a whitespace char for this to work
                    slack_search = re.search('slack.*\s(-*\d+\.\d+)$', line)
                    if slack_search:
                        # get timing value
                        logger.info(f'slack for path group {path_group} is {slack_search.group(1)}')
                        self.timing_by_path_group[path_group] = float(slack_search.group(1))
            except Exception as e:
                logger.error(f'ERROR: {e}')

    def read_area_file(self, area_file):
        '''From input area_file Path object, fill area_by_type dict'''
        logger.info(f'Reading area file {str(area_file)}...')
        # Look for these area types:
        # Combinational area:
        # Buf/Inv area:
        # Noncombinational area:
        # Macro/Black Box area:
        # Net Interconnect area:
        area_types = ['Combinational area',
                      'Buf/Inv area',
                      'Noncombinational area',
                      'Macro/Black Box area',
                      'Net Interconnect area']
        with open(area_file) as a_file:
            try:
                for line in a_file:
                    # check if there is area type in line
                    area_line = False
                    area_type = None
                    for a_type in area_types:
                        if a_type in line:
                            area_line = True
                            area_type = a_type
                    if area_line:
                        # get area number
                        # assume whitespace char preceeding
                        if 'undefined' in line:
                            # non-topo mode has undefined Net Interconnect (no wire load)
                            area_num = 'undefined'
                        else:
                            area_num = re.search('\s(\d+\.\d+)$', line).group(1)

                        logger.info(f'Area for type {area_type} is {area_num}')
                        # assume unit is square micrometers (um^2)
                        self.area_by_type[area_type] = area_num
            except Exception as e:
                logger.error(f'ERROR: {e}')

    def get_sum_area(self):
        ''' Return sum of all area types, skipping any undefined values.
        Also skip Buf/Inv area. DC does not include that in total cell area. '''
        sum_area = 0
        for key, val in self.area_by_type.items():
            if val != 'undefined' and key != 'Buf/Inv area':
                sum_area += float(val)
        return sum_area

    def get_mem_area(self):
        '''Return macro/black-box area'''
        mem_area = 0
        try:
            mem_area = float(self.area_by_type['Macro/Black Box area'])
        except KeyError:
            pass # return 0
        return mem_area

    def read_power_file(self, power_file):
        '''From input power_file Path object, fill power_by_group dict with data for
        internal power, switching power, and leakage power for each power group.'''
        logger.info(f'Reading power file {str(power_file)}...')
        # look for Power Group line, then there is line of only dash
        # then, order of numbers is internal, switching, leakage, total (don't need), % (don't need)
        # then another line of only dash. Done when here

        # assuming power numbers are between lines that consist only of dashes! Might want to improve this...

        # there may be a number like 3.7858e-02 or 68.9913
        sci_num = '\d+\.\d+e?[-|+]?\d*'

        with open(power_file) as p_file:
            done_looking_for_power_groups = False
            try:
                for line in p_file:
                    # look for a line starting with 'Power Group'
                    if re.match('^Power Group', line):

                        # first, get past the line of only dashes
                        next(p_file)

                        while not done_looking_for_power_groups:
                            try:
                                next_line = next(p_file)
                                # power group is first word(s), up until whitespace
                                # get first, second, third numbers
                                power_group_num_search = re.search(f'^(\w+)\s+({sci_num})\s+({sci_num})\s+({sci_num})', next_line)
                                if power_group_num_search:
                                    power_group = power_group_num_search.group(1)
                                    # float() should convert e numbers to decimal
                                    internal_power = float(power_group_num_search.group(2))
                                    switching_power = float(power_group_num_search.group(3))
                                    leakage_power = float(power_group_num_search.group(4))
                                    power_group_obj = one_power_group(power_group,
                                                                      internal_power=internal_power,
                                                                      switching_power=switching_power,
                                                                      leakage_power=leakage_power)
                                    self.power_by_group[power_group] = power_group_obj
                                elif re.match('^-+$', next_line):
                                    # get units on next line
                                    next_line = next(p_file)
                                    # get the characters after the first, second, and third decimal numbers
                                    unit_search = re.search(f'Total\s+{sci_num}\s(\w+)\s+{sci_num}\s(\w+)\s+{sci_num}\s(\w+)', next_line)
                                    if unit_search:
                                        # need to set units for each power group object
                                        for power_group_key in self.power_by_group.keys():
                                            self.power_by_group[power_group_key].internal_power_unit = unit_search.group(1)
                                            self.power_by_group[power_group_key].switching_power_unit = unit_search.group(2)
                                            self.power_by_group[power_group_key].leakage_power_unit = unit_search.group(3)
                                            # standardize units
                                            self.power_by_group[power_group_key].use_common_unit()

                                            logger.info(self.power_by_group[power_group_key])
                                    else:
                                        logger.error('ERROR: Could not find power units')

                                    # done
                                    done_looking_for_power_groups = True
                                else:
                                    logger.warning(f'Was expecting to find some power numbers. Line: "{next_line.rstrip()}"')
                            except StopIteration:
                                logger.warning('unexpectedly hit end of file')
                                done_looking_for_power_groups = True # should not happen
            except Exception as e:
                logger.error(f'ERROR: {e}')

    def get_total_mem_leakage_power(self):
        '''Return total memory leakage power'''
        return self.power_by_group['memory'].leakage_power

    def get_total_mem_dynamic_power(self):
        '''Return total memory dynamic power: internal + switching'''
        return self.power_by_group['memory'].internal_power + self.power_by_group['memory'].switching_power

    def get_total_mem_power(self):
        '''Return total memory power: dynamic + leakage'''
        return self.get_total_mem_dynamic_power() + self.get_total_mem_leakage_power()

    def get_total_non_mem_leakage_power(self):
        '''Return sum of non-memory leakage power'''
        non_mem_leak_power = 0
        for key, power_group_obj in self.power_by_group.items():
            if key != 'memory':
                non_mem_leak_power += power_group_obj.leakage_power
        return non_mem_leak_power

    def get_total_non_mem_dynamic_power(self):
        '''Return sum of non-memory dynamic power: internal + switching'''
        non_mem_dyn_power = 0
        for key, power_group_obj in self.power_by_group.items():
            if key != 'memory':
                non_mem_dyn_power += power_group_obj.internal_power
                non_mem_dyn_power += power_group_obj.switching_power
        return non_mem_dyn_power

    def get_total_leakage_power(self):
        '''Return total leakage power: memory leakage + non-memory leakage'''
        return self.get_total_mem_leakage_power() + self.get_total_non_mem_leakage_power()

    def get_total_dynamic_power(self):
        '''Return total dynamic power: memory dynamic + non-memory dynamic'''
        return self.get_total_mem_dynamic_power() + self.get_total_non_mem_dynamic_power()

    def get_total_power(self):
        '''Return total power: total dynamic power + total leakage power'''
        # Return sum of all power (mW), taking into account the unit (mW or uW usually)
        return self.get_total_dynamic_power() + self.get_total_leakage_power()

    def read_all_result_files(self):
        '''Find timing, area, and power report files and get results'''
        all_result_files = [file for file in self.result_dir.glob('**/*') if not file.is_dir()]
        # take first match we find for files

        # if sub_block is None (couldn't figure out which we were working with), then
        # be more lenient and look at anything with .mapped.timing.rpt, .mapped.area.rpt, .mapped.power.rpt. Kind of hacky
        sub_block_str = self.sub_block if self.sub_block is not None else '.*'

        # find timing file - <subblock>.mapped.timing.rpt
        timing_files = [file for file in all_result_files if re.match(f'.*/{sub_block_str}\.mapped\.timing\.rpt$', str(file))]
        if len(timing_files):
            timing_file = timing_files[0]
            self.read_timing_file(timing_file)
        else:
            logger.warning('No timing files to read')

        # find area file - <subblock>.mapped.area.rpt
        area_files = [file for file in all_result_files if re.match(f'.*/{sub_block_str}\.mapped\.area\.rpt$', str(file))]
        if len(area_files):
            area_file = area_files[0]
            self.read_area_file(area_file)
        else:
            logger.warning('No area files to read')

        # find power file - <subblock>.mapped.power.rpt
        power_files = [file for file in all_result_files if re.match(f'.*/{sub_block_str}\.mapped\.power\.rpt$', str(file))]
        if len(power_files):
            power_file = power_files[0]
            self.read_power_file(power_file)
        else:
            logger.warning('No power files to read')

    def get_summary_lines(self):
        '''Return list of DC result lines: timing, area, power'''
        summary = [f'Sub-block: {self.sub_block}',
                   f'Slack clk group (ps): {self.timing_by_path_group["clk"]}',
                   f'Total Area (um^2): {self.get_sum_area()}',
                   f'Memory Area (um^2): {self.get_mem_area()}',
                   f'Total Power (mW): {self.get_total_power()}',
                   f'Leakage Power (mW): {self.get_total_leakage_power()}',
                   f'Memory Power (mW): {self.get_total_mem_power()}',
                   f'Memory Leakage Power (mW): {self.get_total_mem_leakage_power()}',
                   f'Non-mem Dynamic Power (mW): {self.get_total_non_mem_dynamic_power()}',
                   f'Non-mem Leakage Power (mW): {self.get_total_non_mem_leakage_power()}']
        return summary


class all_ppa_results():
    def __init__(self, result_dir_list):
        # result_dir_list is list of Path objects

        self.result_dirs = result_dir_list

        logger.info('Result directories:')
        for res_dir in self.result_dirs:
            logger.info(str(res_dir))

        self.sub_block_result_objs = [] # list of sub_block_dc_results type

    def process_all_results(self):
        for result_dir in self.result_dirs:
            logger.info(f'Processing results for result directory {str(result_dir)}')
            self.process_one_sub_block_results(result_dir)

    def process_one_sub_block_results(self, result_dir):
        sb_results = sub_block_dc_results(result_dir)
        sb_results.read_all_result_files()
        self.sub_block_result_objs.append(sb_results)

    def result_dir_to_table_row(self, result_dir):
        '''From input result_dir Path object, return string of sub_block with constraints'''
        # result dir looks something like this:
        # <removed>
        sub_block = get_sub_block_from_result_dir(result_dir)
        if 'no_git_commit' in str(result_dir):
            con_search = re.search('no_git_commit_(\w+)', str(result_dir))
        else:
            con_search = re.search('[a-f|0-9]{7,}_(\w+)', str(result_dir))
        if con_search:
            constraints = con_search.group(1)
        else:
            # if we can't figure it out, just use the result_dir itself
            # and don't use the block name, likely is redundant
            constraints = str(result_dir).split('/')[-1]
            sub_block = None

        if sub_block is None:
            return constraints
        else:
            return sub_block + '_' + constraints

    def result_dir_to_sub_block_obj(self, result_dir):
        '''From input result_dir Path object, return a sub_block_dc_results type object.
        There may be multiple sub_block objects with different constraints, so we need to
        take those constraints into account. Look for a matching sub block result dir.'''
        for sub_block_obj in self.sub_block_result_objs:
            if sub_block_obj.result_dir == result_dir:
                return sub_block_obj

    def results_to_csv(self, out_table_path_str):
        '''
        Make CSV file in base results dir with data for block with constraints,
        timing, area, power numbers. This is intended to be added to the emails as an attachment.
        Return path to generated table.
        '''
        table_cols = ['Slack (ps)',
                      'Area (um^2)',
                      'Memory Area (um^2)',
                      'Total Power (mW)',
                      'Leakage (mW)',
                      'Memory Power (mW)',
                      'Memory Leakage (mW)',
                      'Non-Memory Dynamic Power (mW)',
                      'Non-Memory Leakage Power (mW)']


        assert len(self.result_dirs), 'No result directories to work with'

        # get base from first result dir
        res_base_dir = '/'.join(str(self.result_dirs[0]).split('/')[:-1])

        try:
            # get list of sub_blocks with constraints as rows
            table_rows = [self.result_dir_to_table_row(result_dir) for result_dir in self.result_dirs]

            result_table = pd.DataFrame(columns=table_cols, index=table_rows)
            result_table.index.name = 'Block'

            # fill table
            for result_dir in self.result_dirs:
                table_row = self.result_dir_to_table_row(result_dir)
                sub_block_obj = self.result_dir_to_sub_block_obj(result_dir)
                # if sub_block was None (couldn't figure it out), use result directory as column
                if sub_block_obj.sub_block is None and result_table.index.name == 'Block':
                    result_table.index.name = 'Result Directory'

                # place in list of numbers. order matters
                result_table.loc[table_row] = [sub_block_obj.timing_by_path_group['clk'],
                                               sub_block_obj.get_sum_area(),
                                               sub_block_obj.get_mem_area(),
                                               sub_block_obj.get_total_power(),
                                               sub_block_obj.get_total_leakage_power(),
                                               sub_block_obj.get_total_mem_power(),
                                               sub_block_obj.get_total_mem_leakage_power(),
                                               sub_block_obj.get_total_non_mem_dynamic_power(),
                                               sub_block_obj.get_total_non_mem_leakage_power()]

            # pandas to_csv
            # out_table_path_str will come from args.out_table
            # by default, this will be None, so put in base result dir
            if out_table_path_str is None:
                out_table_path_str = f'{res_base_dir}/ppa_results.csv'
            # (mheim) little hack - if no .csv mentioned, add on /ppa_results.csv. This is so
            # I can say to use '.' as shorthand, for example.
            elif not out_table_path_str.endswith('.csv'):
                out_table_path_str += f'/ppa_results.csv'

            result_table.to_csv(out_table_path_str)
            logger.info(f'Wrote PPA results table to {res_base_dir}/ppa_results.csv')

        except KeyError as e:
            logger.warning(f'Key does not exist: {e}')
            return 'N/A'
        except:
            logger.error(f'ERROR: Received unexpected error')
            raise

        return f'{res_base_dir}/ppa_results.csv'

    def print_all_results(self):
        # print out all the results
        try:
            for result_dir in self.result_dirs:
                sub_block_obj = self.result_dir_to_sub_block_obj(result_dir)
                logger.info(f'Result Directory: {str(result_dir)}')

                for line in sub_block_obj.get_summary_lines():
                    logger.info(line)

        except KeyError as e:
            logger.warning(f'Key does not exist: {e}')
        except:
            logger.error('ERROR: Received unexpected error')
            raise

    def get_all_summaries(self):
        # return a list of strings of the results, just like the print function above
        res = ['\nPPA Summary\n']
        try:
            for result_dir in self.result_dirs:
                sub_block_obj = self.result_dir_to_sub_block_obj(result_dir)
                res += [f'\n\nResult Directory: {str(result_dir)}']

                res += sub_block_obj.get_summary_lines()
        except KeyError as e:
            logger.warning(f'Key does not exist: {e}')
            res += ['There was a problem while generating PPA results.']
        except:
            logger.error('ERROR: Received unexpected error')
            raise

        return res

class dc_run_info():
    '''This class stores information that we want to report for one DC run.
    This is intended to help with sending success and failure info over email'''
    def __init__(self, sub_block, constraints, make_cmd, make_rc, result_dir, error_info):
        self.sub_block = sub_block
        self.constraints = constraints # one string
        self.make_cmd = make_cmd # string
        self.make_rc = make_rc
        self.result_dir = result_dir # string
        self.error_info = error_info # list of strings

        self.status = 'SUCCESS' if (self.make_rc == 0 and not len(self.error_info)) else 'FAILURE'

    def run_succeeded(self):
        return self.status == 'SUCCESS'

    def get_error_count(self):
        return len(self.error_info)

    def get_status_line(self):
        '''Return a string with status and return code, intended for email subject'''
        return f'Makefile for {self.sub_block}: {self.status} (Return Code: {self.make_rc})'

    def get_info_list(self):
        info = []
        info += [f'{"*"*30} Run Information for Sub-Block: {self.sub_block} {"*"*30}']
        info += [f'Constraints: {self.constraints}']
        info += [f'Result Directory: {self.result_dir}']
        info += [f'Make command: {self.make_cmd}']
        info += [f'Make Return Code: {self.make_rc}']
        info += [f'Run Finish Status: {self.status}']
        if len(self.error_info):
            info += ['Error Information:']
            info += self.error_info

        return info

if __name__ == "__main__":
    args = parser.parse_args()


    # only run PPA post-processing if used says only make table.
    # we can actually skip basically the whole script and get to just the PPA processing.
    # result_dir_list will be all the result dirs within the base result dir the user specified with
    # -r arg.
    result_dir_list = []
    result_base_dir_arg = str(Path(args.result_dir).resolve())

    # get all sub-block names
    all_makefiles = get_all_makefiles()
    all_sub_block_names = sorted(get_sub_block_names(all_makefiles))

    # get all block names
    all_blocks = sorted(list(set(get_all_blocks())))

    # if we are only making a new table, skip the run process.
    if args.only_make_table:
        # get all result dirs in base result dir
        result_dir_list = [r_dir for r_dir in Path(result_base_dir_arg).glob('*')
                           if r_dir.is_dir()]
    elif args.list_blocks:
        # just list the blocks and sub_blocks and then exit.
        print('All Blocks:')
        for i in all_blocks:
            print('\t', i)
        print('All Sub-Block Names:')
        for i in all_sub_block_names:
            print('\t', i)
        sys.exit(0)
    else:
        logger.info(f'Args: {args}')
        # a user may specify one or more blocks, and/or one or more sub-blocks,
        # or they may use 'all' for blocks or sub-blocks.

        # make sure something was specified
        assert(len(args.block) or len(args.sub_block)), 'No block or sub-block was specified'

        if len(args.block):
            assert(set(args.block).issubset(all_blocks)), 'Did not understand block arguments'
        if len(args.sub_block):
            assert(set(args.sub_block).issubset(all_sub_block_names)), 'Did not understand sub-block arguments'

        debug_mode = args.debug_mode is not None
        debug_mode_pass_pct = None # just to catch myself
        if debug_mode:
            debug_mode_pass_pct = args.debug_mode

        # get constraint arguments
        stdcell_tracks = ALL_TRACKS if args.stdcell_track == ['all'] else set(args.stdcell_track)

        use_gf12 = args.tech == 'gf12'
        if use_gf12:
            stdcell_tracks = set(stdcell_tracks) & set(VALID_GF12_TRACKS)
        else:
            stdcell_tracks = set(stdcell_tracks) & set(VALID_GF22FDX_TRACKS)

        assert(len(stdcell_tracks)), 'No valid stdcell tracks found. Check --gf12 argument'
        logger.info(f'stdcell tracks: {stdcell_tracks}')

        # if nargs is 1 or *, both/all option will be in list format.
        # If nargs is ?, it's just a string.
        corners = ALL_CORNERS if args.corner == ['all'] else set(args.corner)
        freqs = set(args.freq)
        boundary_opts = ['True', 'False'] if args.enable_boundary_optimization == ['both'] else args.enable_boundary_optimization
        topo_opts = ['True', 'False'] if args.enable_topo_mode == ['both'] else args.enable_topo_mode
        opt_flows = ALL_OPT_FLOWS if args.optimization_flow == ['all'] else set(args.optimization_flow)
        reduced_effort_args = ['True', 'False'] if args.reduced_effort == 'both' else [args.reduced_effort]
        retime_args = ['True', 'False'] if args.enable_retime == ['both'] else args.enable_retime

        # begin run setup
        makefile_run_set = set()

        # 'all' behavior
        if args.block == 'all' or args.sub_block == 'all':
            makefile_run_set = set(all_makefiles)
        else:
            # from blocks and sub_blocks args, find set of makefiles to run
            if len(args.block):
                block_makefiles = get_makefiles_in_blocks(args.block)
                for makefile in block_makefiles:
                    makefile_run_set.add(makefile)
            if len(args.sub_block):
                sub_block_makefiles = get_makefiles_in_sub_blocks(args.sub_block)
                for makefile in sub_block_makefiles:
                    makefile_run_set.add(makefile)

        # we may use one or more:
        # - for each stdcell track
        # - for each corner
        # - for each freq
        # - for each boundary optimization option (on and/or off)
        # - for each optimization flow
        # - for each reduced effort option (on and/or off)
        # - for each topo mode option (on and/or off)
        # - for each retime option (on and/or off)

        # run make on set of makefiles
        makefile_run_list = sorted(list(makefile_run_set))
        assert(len(makefile_run_list)), 'There are no Makefiles to run.'

        total_fails = 0
        total_successes = 0
        git_commit = None # expected to be overriden

        # run info - list of objects of dc_run_info class
        all_run_info = []

        # convert some of the enable and/or disable options from strings to bools

        boundary_opts = [True if bound_opt == 'True' else False for bound_opt in boundary_opts]

        topo_opts = [True if topo_opt == 'True' else False for topo_opt in topo_opts]

        reduced_effort_args = [True if reduced_effort_arg == 'True' else False for reduced_effort_arg in reduced_effort_args]

        retime_args = [True if retime_arg == 'True' else False for retime_arg in retime_args]

        # create product of all run configurations

        run_configs = itertools.product(makefile_run_list,
                                        stdcell_tracks,
                                        corners,
                                        freqs,
                                        boundary_opts,
                                        opt_flows,
                                        reduced_effort_args,
                                        topo_opts,
                                        retime_args)

        for run_config in run_configs:
            (makefile, stdcell, corner, freq, bound_opt_bool, opt_flow, reduced_effort_bool, topo_opt_bool, retime_arg_bool) = run_config

            (rc, make_cmd) = run_make(makefile,
                                      stdcell,
                                      corner,
                                      use_gf12,
                                      freq,
                                      bound_opt_bool,
                                      opt_flow,
                                      reduced_effort_bool,
                                      topo_opt_bool,
                                      retime_arg_bool,
                                      debug_mode,
                                      debug_mode_pass_pct,
                                      dry_run=args.dry_run)

            if not args.dry_run:
                logger.info(f'Received return code {rc}')

            sub_block_name = get_one_sub_block_name(makefile)

            constraints_str = f'GF12 = {int(use_gf12)}, STDCELL = {stdcell}, CORNER = {corner}, FREQ = {freq}, BOUNDARY_OPTIMIZATION = {bound_opt_bool}, TOPO_MODE = {topo_opt_bool}, OPTIMIZATION_FLOW = {opt_flow}, REDUCED_EFFORT = {reduced_effort_bool}, RETIME = {retime_arg_bool}'

            # if the run completes, we will look for errors in the stdout logfile.
            # The run is failed if we find errors
            dc_error_info = []

            # POST-PROCESSING - after each run
            if rc == 0:
                # If we didn't get a git commit yet, get one.
                # Otherwise, retain the one we started with.
                # We don't want it to change during the runs (I think).
                if git_commit is None:
                    git_commit = get_git_commit(makefile)

                parent_dir = '/'.join(str(makefile).split('/')[:-1])

                # try to make results dir

                freq_str = ''
                if freq == 1.0:
                    freq_str = '1GHz'
                elif freq > 0.0 and freq != 1.0:
                    freq_str = str(round(freq*1000)) + 'MHz'

                constraints = [stdcell, corner, freq_str, opt_flow]
                if not bound_opt_bool:
                    constraints += ['NO_HIER']

                if topo_opt_bool:
                    constraints += ['TOPO_MODE']

                if reduced_effort_bool:
                    constraints += ['REDUCED_EFFORT']

                if not retime_arg_bool:
                    constraints += ['NO_RETIME']

                run_name = "_".join(constraints)

                result_dir_str = f'{result_base_dir_arg}/{sub_block_name}_{git_commit}_{run_name}'
                # because gf12 is either on or off for the set of runs, include it in the
                # dir name with the sub block and git commit.
                if use_gf12:
                    result_dir_str += '_GF12'
                else:
                    result_dir_str += '_GF22FDX'

                if debug_mode:
                    result_dir_str += '_DEBUG_MODE'

                result_dir = Path(result_dir_str)

                # check if result dir (sub-block + git commit + GF12) exists
                if args.dry_run:
                    # dry-run behavior: print the result dir, but don't actually copy files over

                    logger.info(f'result directory would be {str(result_dir)}')

                    if result_dir.is_dir():
                        logger.info('Note: This result directory already exists')
                else:
                    if result_dir.is_dir():
                        # (mheim) It might be nice to always put the datetime as a suffix to the directory name.
                        # Right now, it only puts that to not overwrite an existing directory.

                        u_prompt = f'Are you sure you want to overwrite {str(result_dir)}? Press enter to overwrite, ' \
                                   'or enter a suffix to differentiate result directory:'

                        # for a force run, don't ask for user input
                        suffix = None
                        if not args.force_run:
                            u_timer_obj = input_timeout(timeout=30) # seconds
                            suffix = u_timer_obj.run_input_timer(u_prompt)
                            # returns None if timeout reached. If that is the case, make datetime suffix

                        if (suffix is None or args.force_run) and not debug_mode:
                            now = datetime.now()
                            # override suffix
                            now_str = f'{now.hour}-{now.minute}_{now.day}-{now.month}-{now.year}'
                            logger.info(f'Adding datetime {now_str} to directory name')
                            suffix = now_str

                        if suffix is not None and len(suffix):
                            result_dir_str += f'_{suffix}'
                            result_dir = Path(f'{str(result_dir)}_{suffix}')
                            logger.info(f'Now, result dir is {str(result_dir)}')
                    elif result_dir.is_file():
                        # mkdir will error out if dir is file somehow. remove
                        result_dir.unlink()

                    result_dir.mkdir(parents=True, exist_ok=True)

                    result_dir_str = str(result_dir)

                    copy_dc_results(makefile, result_dir)

                    dc_error_info = get_dc_error_info(makefile) # returns list of error lines

                    # create run info object
                    this_run_info = dc_run_info(sub_block_name,
                                                constraints_str,
                                                make_cmd,
                                                rc,
                                                result_dir_str,
                                                dc_error_info)

                    # add to all_run_info
                    all_run_info.append(this_run_info)

                    # if no errors, then we have a success
                    if this_run_info.run_succeeded():
                        # ---------------------------
                        # ------- SUCCESS -----------
                        # ---------------------------
                        logger.info(f'Makefile for {sub_block_name}: SUCCESS')
                        total_successes += 1

                    result_dir_list.append(result_dir)

                    # run DC warnings organization script
                    logfile_record = dc_warnings.one_dc_logfile_record(None, result_dir) # block, path
                    warn_count = logfile_record.organize_warnings()
                    if warn_count > 0:
                        logfile_record.condense_warning_meanings()
                        logfile_record.report_warnings()

                    # we may have found an error in the logfiles
                    # or got a nonzero return code
                    if not this_run_info.run_succeeded():
                        # ---------------------------
                        # ------- FAILURE -----------
                        # ---------------------------
                        total_fails += 1
                        logger.error(f'Makefile for {sub_block_name}: FAILURE: {this_run_info.get_error_count()} DC Errors Found')

                    # email for 'each' mode
                    if args.email_when_done == ['each'] or args.email_when_done == ['both']:
                        if rc != 0:
                            ppa_summary_this_run = []
                        else:
                            # get PPA results for this run
                            # kind of inefficient but make a ppa results obj for just this run
                            ppa_this_run = all_ppa_results([result_dir])
                            ppa_this_run.process_all_results()
                            ppa_summary_this_run = ppa_this_run.get_all_summaries()

                        status_str = this_run_info.get_status_line()

                        email_body = this_run_info.get_info_list()
                        email_results(sub_block_name,
                                      status_str,
                                      args.email_address,
                                      body_text_additions=email_body+ppa_summary_this_run)


    # POST-PROCESSING - after all runs completed

    if not args.dry_run:
        # PPA
        ppa_process_obj = all_ppa_results(result_dir_list)
        ppa_process_obj.process_all_results()
        ppa_process_obj.print_all_results()
        csv_path = ppa_process_obj.results_to_csv(args.out_table)

        # don't bother with summary email if we only want a PPA table
        if args.only_make_table:
            if csv_path != 'N/A':
                logger.info(f'PPA table written to {csv_path}')
            else:
                logger.error('No PPA table was created.')
        else:
            os.chdir(SCRIPT_DIR)
            # Failure information is both printed and emailed because we definitely don't want any DC runs
            # to fail for some reason.
            total_run_info = []
            if not args.dry_run:
                # print all run info, showing what succceeded and failed
                for run_info in all_run_info:
                    for line in run_info.get_info_list():
                        # still want to report errors as errors
                        if 'Error:' in line:
                            logger.error(line)
                        else:
                            logger.info(line)

                        total_run_info.append(line)

                # print fails, successes
                logger.info(f'Total Successes: {total_successes} out of {total_fails + total_successes} runs')

                # email for 'all' mode
                if args.email_when_done == ['all'] or args.email_when_done == ['both']:
                    status_str = f'{total_successes}/{total_fails + total_successes} Jobs Succeeded'

                    ppa_summary = ppa_process_obj.get_all_summaries()

                    csv_path_line = [f'Path to table with PPA results: {csv_path}']
                    csv_path_line += [f'Base Result Directory: {result_base_dir_arg}']

                    email_results('[All Jobs]',
                                  status_str,
                                  args.email_address,
                                  body_text_additions=csv_path_line+total_run_info+ppa_summary)
    logger.info(f'Logfile written to {SCRIPT_DIR}/dc_autorun.log')

    logger.info('done.')
