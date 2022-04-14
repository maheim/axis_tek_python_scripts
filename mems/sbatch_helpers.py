#!/usr/bin/env python3

import subprocess
from pathlib import Path
import re
import time
import signal
import os
import pwd
import sys

# (mheim) Trying to make sure that we don't cancel all wrap jobs without the user knowing
# exactly what's going to be cancelled. Scripts that use wait_for_sbatch_completion can
# disable the exit prompt if they clear the assumption that no sbatch "wrap" jobs are
# running before they start kicking off memory compiler runs.
USER_EXIT_PROMPT = True

def get_username():
    return pwd.getpwuid(os.getuid()).pw_name

def get_squeue_jobids(username, job_name):
    '''Run squeue -u <username> --name <job_name>.
    Then, get the jobids from the output and return the jobids.'''

    cmd = f'squeue -u {username} --name {job_name}'
    out = subprocess.run(cmd,
                         shell=True,
                         check=True,
                         universal_newlines=True,
                         stdout=subprocess.PIPE)

    # get jobids from output:
    # remove space characters
    # then, each jobid is the first word after each newline
    out_str = out.stdout.split(' ')
    out_str = [i for i in out_str if len(i)]
    jobids = set()
    for place, word in enumerate(out_str[:-1]):
        if word.endswith('\n'):
            # make sure it's a number
            jobids.add(int(out_str[place + 1]))

    return jobids

def user_exit_handler(signum, frame):
    user = get_username()

    # show what jobs will be cancelled
    jobs_to_cancel = get_squeue_jobids(user, "wrap")

    if len(jobs_to_cancel):
        print('\nJobids that will be cancelled:')
        _ = [print(i) for i in jobs_to_cancel]

    # if scripts that use this function follow the assumption that all wrap jobids
    # belong to the current script run, then we don't need this prompt.

    # Scripts that use this function MUST make sure there are no wrap jobs
    # running before the user starts kicking off jobs.
    if USER_EXIT_PROMPT:
        _ = input(f'Are you sure you wish to cancel all (sbatch) wrap jobs for user "{user}"? ' \
                  f'Please make sure no other wrap jobs are running. Press enter to continue:')

    print('Exiting. Cancelling jobs...')
    # scancel all the job ids
    for jobid in jobs_to_cancel:
        _ = subprocess.run(f'scancel {jobid}', shell=True, check=True)
        print(f'Cancelled jobid {jobid}')
    # do not call sys.exit
    os._exit(1)

def grab_slurm_out_files():
    '''Return Path objects of slurm-*.out files in the current working directory.'''
    return [o_file for o_file in Path('.').resolve().glob('*') if re.match('.*/slurm.*\.out$', str(o_file))]


def remove_slurm_out_files():
    '''Remove all the slurm*.out files in this working directory, if any'''
    slurm_out_files_list = grab_slurm_out_files()
    for o_file in slurm_out_files_list:
        print(f'Removing slurm file {str(o_file)}')
        o_file.unlink()


def wait_for_sbatch_completion(interval):
    '''
    Sleep for some interval and check on the user's current sbatch wrap jobs.
    This function will run all the way through and then return nothing.

    ASSUMPTION: big assumption that all "wrap" jobs in squeue "belong" to this memory running
    process.
    '''

    # SIGINT handler
    signal.signal(signal.SIGINT, user_exit_handler)

    # dots used for printing one line repeatedly with job count status
    dots = '.'
    max_dots = 10

    done_waiting = False

    while not done_waiting:
        time.sleep(interval)
        dots += '.'
        if len(dots) > max_dots:
            dots = '.'
        spaces = ' ' * (max_dots - len(dots))

        num_wrap_jobs_found = len(get_squeue_jobids(get_username(), 'wrap'))

        print(f'Number of jobs currently running: {num_wrap_jobs_found}{dots}{spaces}', end='\r')

        if num_wrap_jobs_found == 0:
            done_waiting = True

    print('\nAll jobs completed.')


def sbatch_check_build_errors():
    '''Check through each slurm-*.out file in the current working directory, looking for
    memory compiler errors. An error will start with (E) '''

    slurm_out_files = grab_slurm_out_files()

    macro_names_and_build_errors = {} # dict: full macro name -> list of build errors

    for o_file in slurm_out_files:
        with open(o_file) as logfile:
            # search for the macro name. It will be on a line like this:
            ##    -macro <MACRO_NAME>
            macro_name = 'macro_name_not_found'
            found_macro_name = False

            for line in logfile:
                if not found_macro_name:
                    macro_name_search = re.search('-macro (\w*)$', line)
                    if macro_name_search:
                        macro_name = macro_name_search.group(1)
                        found_macro_name = True
                        macro_names_and_build_errors[macro_name] = []

                build_err_search = re.search(r'\(E\)\s*(.*)', line.strip())
                if build_err_search:
                    macro_names_and_build_errors[macro_name].append(line.strip())

    for macro_name, build_errors in macro_names_and_build_errors.items():
        if len(build_errors):
            print(f'RAM {macro_name} compile: ERROR')
            for err in build_errors:
                print(f'\t{err}')
        else:
            print(f'RAM {macro_name} compile: SUCCESS')
