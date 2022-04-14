#!/usr/bin/env python3

import subprocess
from pathlib import Path
import argparse
import re
import time
import sys
import yaml
import itertools
import copy

from mem_compiler import print_tbl_hdr
from sbatch_helpers import *
import sbatch_helpers

from run_memory_compiler import mem_compiler_classes

# --------------------- Get Compilers ----------------------------------

gf12_compilers = mem_compiler_classes('gf12_mem_compiler_types') # pass in module name
gf22_compilers = mem_compiler_classes('gf22fdx_mem_compiler_types') # pass in module name

ALL_COMPILERS = {'gf22' : gf22_compilers,
                 'gf12' : gf12_compilers}

# --------------------------------------------------------------------

class mem_compiler_list_container():
    '''This class holds a list of MemCompile objects and their full macro names'''
    def __init__(self):
        self.compilers = []
        self.macro_names = []

    def add_compiler(self, compiler_obj):
        self.compilers.append(compiler_obj)
        # add to macro names
        self.macro_names.append(compiler_obj.get_full_name())

    def run_compilers(self, dest_dir, args, use_sbatch):
        for compiler in self.compilers:
            # make sure to set the dest_dir
            compiler.dest_dir = dest_dir
            # passing in args only for --overwrite
            compiler.run_compiler(dest_dir, args, use_sbatch)

    def add_data_to_csv(self, out_file):
        for compiler in self.compilers:
            compiler.parse_all_csvs(None)
            compiler.print_summary('csv', out_file=out_file)


# ------------------------- Argument Parser ---------------------------------------

parser = argparse.ArgumentParser(description='Run memory characterization, generating a PPA table with data from running each valid memory compiler option. ' \
                                 'This script is meant for running many memory compiler options at once with different combinations of dimensions.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('-d', '-dry', '--dry-run',
                    action='store_true',
                    help='Pass dry run to run_memory_compiler script')

parser.add_argument('--sbatch',
                    action='store_true',
                    help='Use sbatch (parallel) instead of srun (sequential). Be aware that using ' \
                    'this switch assumes that all sbatch "wrap" jobs belong to this running process. ' \
                    'If you Ctrl-C while the memory compilers are running, ALL "wrap" jobs will be cancelled!')

parser.add_argument('--overwrite',
                    action='store_true',
                    help='Overwrite previously generated memory compiler outputs. By default, the memory compilers will not re-run if they were run before.')

parser.add_argument('--interval',
                    type=int,
                    default=1,
                    help='How often (in seconds) to display job running status')

parser.add_argument('-y', '--yaml-file',
                    type=str,
                    default='<removed>',
                    help='Specify a path to a yaml file to load a configuration for memory requirements.')

parser.add_argument('-o', '--output-table',
                    type=str,
                    default='mem_summaries.csv',
                    help='Specify a file name for the output memory PPA table')

parser.add_argument('--mem-out-dir',
                    type=str,
                    default='out',
                    help='Optionally specify an alternate location of the memory compiler output files')

if __name__ == '__main__':

    args = parser.parse_args()

    mem_reqs = []

    # ----------------------------------- Memory Configurations To Run ------------------------------

    # use yaml file to load mem requirements
    yaml_file_path = Path(args.yaml_file).resolve()

    with open(yaml_file_path, 'r') as yaml_file:
        try:
            yaml_config = yaml.safe_load(yaml_file)
        except yaml.YAMLError as yaml_error:
            print(yaml_error)
            sys.exit(1)

    compilers_container = mem_compiler_list_container()

    for _, properties in yaml_config.items():

        print(properties)
        families = properties['family'].split(', ')

        # each element is (depth, width)
        dimension_combos = []

        if 'dimensions' in properties.keys():
            dimension_combos = [dim.split(', ') for dim in properties['dimensions']]
        else:
            depths = properties['depth']
            if type(depths) == str:
                depths = depths.split(', ')
            else:
                # if int, put into list
                depths = [depths]

            widths = properties['width']
            if type(widths) == str:
                widths = widths.split(', ')
            else:
                # if int, put into list
                widths = [widths]

            # get cartesian product of depths and widths
            dimension_combos = list(itertools.product(depths, widths))

        print(f'families: {families}')
        print(f'dimension_combos: {dimension_combos}')

        # going to refer to both banks and subarrays as banks
        features = {'mux' : None,
                    'bank' : None}
        for prop_name, prop_value in properties.items():
            if prop_name not in ['family', 'depth', 'width', 'dimensions', 'tech']:
                if prop_name == 'subarrays':
                    features['bank'] = prop_value
                else:
                    features[prop_name] = prop_value

        tech = properties['tech']

        # go through each family and each depth/width combination to create
        # mem_req objects

        for family in families:
            for (depth, width) in dimension_combos:

                this_config_features = features

                # depth, width, mux, and bank/subarray must be ints, not strings,
                # for base memory compiler set full name function
                this_config_features['words'] = int(depth)
                width = int(width)

                compiler_obj = ALL_COMPILERS[tech].get_compiler_from_name(family, width, **this_config_features)

                # going to refer to both banks and subarrays as banks

                muxes = [this_config_features['mux']]
                banks = [this_config_features['bank']]
                if None in muxes:
                    # all
                    muxes = compiler_obj.get_all_mux_options()
                if None in banks:
                    # all
                    banks = compiler_obj.get_all_bank_subarrays()

                for mux in muxes:
                    for bank in banks:

                        this_config_features['mux'] = int(mux)
                        if tech == 'gf12':
                            this_config_features['subarrays'] = int(bank)
                        else:
                            this_config_features['bank'] = int(bank)

                        compiler_obj = ALL_COMPILERS[tech].get_compiler_from_name(family, width, **this_config_features)
                        compiler_obj.set_full_name()

                        if compiler_obj.get_full_name() not in compilers_container.macro_names:
                            print(f'Adding {compiler_obj.get_full_name()}')
                            compilers_container.add_compiler(copy.deepcopy(compiler_obj))


    # ---------------------------------------------------------------------------------------------

    # Run the compilers

    mem_out_dir = Path(args.mem_out_dir).resolve()
    mem_out_dir_str = str(mem_out_dir)

    job_count = 0
    macros_to_be_generated = []
    # job count = number of unique macros names that do NOT have a logfile already
    # this needs to be done BEFORE running the memory compilers
    for compiler in compilers_container.compilers:
        # if --overwrite used, compilers will run
        if (not args.overwrite and not compiler.does_logfile_exist(mem_out_dir_str)) \
           or args.overwrite:
            job_count += 1
            macros_to_be_generated.append(compiler.get_full_name())

    if args.dry_run:
        # show what macros would be run.
        print(f'Number of macros to be generated: {job_count}')
        if job_count:
            print('Macros to be generated:')
            _ = [print(f'- {i}') for i in macros_to_be_generated]
    else:
        # csv output file
        # create csv table file to fill with data from CSVs generated by running each compiler
        table_out_str = args.output_table
        if not table_out_str.endswith('.csv'):
            table_out_str += '.csv'
        table_out_path = Path(table_out_str).resolve()

        # file cleanup
        if not args.dry_run and table_out_path.is_file():
            if table_out_path.is_file():
                print(f'Removing existing table file {str(table_out_path)}')
                table_out_path.unlink()

        # print the header once to the table file
        print_tbl_hdr(gen_csv=True, out_file=table_out_path)

        # ----- sbatch checking when done and checking for build errors -----
        if args.sbatch:
            # do a check that the user is not already running an sbatch "wrap" job.
            # If they are, don't run anything
            wrap_jobids = get_squeue_jobids(get_username(), 'wrap')
            assert not len(wrap_jobids), 'Cannot run the compilers with sbatch because we ' \
                'assume that all current wrap jobs for the user are from running this script. ' \
                f'Current wrap jobids: {wrap_jobids}'
            # assumption is cleared
            sbatch_helpers.USER_EXIT_PROMPT = False

            # slurm.out files need to be removed because we use the resulting ones
            # to check for build errors
            remove_slurm_out_files()

        # ----------- run the compilers ------------
        compilers_container.run_compilers(mem_out_dir_str, args, args.sbatch)

        if job_count:
            wait_for_sbatch_completion(args.interval)
            # Note: this function will check for build errors in the slurm.out files.
            # We removed all of them before running this process, so we can safely assume
            # all the slurm.out files in this directory belong to this current run.
            sbatch_check_build_errors()

        # when done waiting for the jobs to finish, put the data into the spreadsheet
        for compiler in compilers_container.compilers:

            compiler.parse_all_csvs(None)

            compiler.print_summary('csv', out_file=table_out_path)

            print(f'Added data for {compiler.get_full_name()} to {str(table_out_path)}')

    print('done.')
