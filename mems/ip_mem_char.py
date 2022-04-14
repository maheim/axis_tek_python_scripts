#!/usr/bin/env python3

from pathlib import Path
import re
import yaml
import sys
import argparse
import copy

from mem_compiler import MemInstance

from gf12_mem_compiler_types import *
from gf22fdx_mem_compiler_types import *
from sbatch_helpers import *
import sbatch_helpers

from run_memory_compiler import mem_compiler_classes

gf12_compilers = mem_compiler_classes('gf12_mem_compiler_types') # pass in module name

gf22_compilers = mem_compiler_classes('gf22fdx_mem_compiler_types') # pass in module name

all_compilers = {'gf12' : gf12_compilers,
                 'gf22' : gf22_compilers}

# ------------------------------- Corners ------------------------------------------------

ALL_SHORTHAND_CORNERS = {'<removed>' : ['TT', 0.7, 0.8, 25],
                         '<removed>' : ['TT', 0.6, 0.8, 25]}

def show_all_shorthand_corner_info():
    print('All shorthand corners (Format is [process, Vdd, optional Vcs, Temp]):')
    for corner_name, corner_list in ALL_SHORTHAND_CORNERS.items():
        print(f'{corner_name} : {corner_list}')


# ------------------------------ Argument Parser ------------------------------------------

parser = argparse.ArgumentParser(description='Create a CSV table of PPA data for memories in the IP',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('-r', '--run-compiler',
                    action='store_true',
                    help='Run each memory compiler if it has not ran already. ' \
                    'It is recommended to run with the --sbatch switch for parallel running.')

parser.add_argument('-t', '--tech',
                    type=str.lower,
                    choices=('gf12', 'gf22'),
                    default='gf12',
                    help='Choose GF process node.')

parser.add_argument('-m', '--out-mem-dir',
                    type=str,
                    default='out',
                    help='Specify the path to the directory where the memory compiler outputs are.' \
                    'This is useful for pointing the script to a place with memory compiler data ' \
                    'so that you do not have to run all the compilers, which saves a lot of time.')

parser.add_argument('-o', '--output-table',
                    type=str,
                    default='mem_ip_characterization.csv',
                    help='Specify a file name for the output memory PPA table.')

parser.add_argument('-y', '--yaml-file',
                    type=str,
                    nargs='+',
                    default=['<removed>'],
                    help='Specify a path to one or more YAML configuration files with IP memory needs. ' \
                    'Keep in mind that the order matters. An application-specific YAML file should ' \
                    'be loaded after a first YAML file with more generic memory properties. ' \
                    'The default application is <removed>.')

parser.add_argument('--no-remove',
                    action='store_true',
                    help='Do not remove existing data table. Append data to existing table instead.')

parser.add_argument('-c', '--corner',
                    default='TT',
                    nargs='*',
                    help='Specify a corner (process) when creating the data table. You can use ' \
                    'a list format (space separated) or use a shorthand string. You can also use the traditional ' \
                    '"TT" (typical) or "SS" (slow) options. If there is an error in getting data for a particular ' \
                    'corner, you probably need to run the compilers (-r) with that corner.')

parser.add_argument('-lc', '--list-shorthand-corners',
                    action='store_true',
                    help='Show available shorthand corner options and corresponding values.')

parser.add_argument('-b', '--block',
                    type=str.upper,
                    nargs='*',
                    default=['ALL'],
                    help='Filter by block')

parser.add_argument('--sbatch',
                    action='store_true',
                    help='Run memory compilers in parallel rather than sequentially')

parser.add_argument('-f', '--freq',
                    type=float,
                    default=1.0,
                    help='Application frequency in GHz')

if __name__ == "__main__":

    args = parser.parse_args()

    print(args)

    if args.list_shorthand_corners:
        # show corner info and then exit normally
        show_all_shorthand_corner_info()
        sys.exit(0)

    # ---------------------------------- Corners ------------------------------------

    # figure out corner - either TT/SS, shorthand string, or list
    # if list, can have 3 or 4 values.

    corner = args.corner
    if len(corner) == 1:
        corner = corner[0]

    # check if this is a legal corner option
    is_legal_corner = False
    is_legal_corner |= type(corner) == str and corner in ['TT', 'SS'] # NOTE: no FF here
    is_legal_corner |= type(corner) == str and corner in ALL_SHORTHAND_CORNERS.keys()
    is_legal_corner |= type(corner) == list and len(corner) in [3, 4]
    assert is_legal_corner, f'Corner is not legal: "{corner}"'

    # fix SS corner argument - if gf12 is used, slow corner is SSPG
    # It seems that for GF22, it's SSG
    if args.tech == 'gf12' and corner == 'SS':
        corner = 'SSPG'
    elif args.tech == 'gf22' and corner == 'SS':
        corner = 'SSG'
    else:
        if type(corner) == str and corner in ALL_SHORTHAND_CORNERS.keys():
            corner = ALL_SHORTHAND_CORNERS[corner]

        if type(corner) == list:
            corner[1:-1] = [float(i) for i in corner[1:-1]]

            # temp must be an int. If it has a decimal point, corner will be invalid unfortunately.
            corner[-1] = int(corner[-1])

    # ------------------------------------------------------------------------------

    # get mem out dir - as str
    out_mem_dir = str(Path(args.out_mem_dir).resolve())

    # criteria_opts = ['area', 'speed', 'leakage'] # criteria unused currently
    criteria = 'area'

    out_file_str = args.output_table
    if not out_file_str.endswith('.csv'):
        out_file_str += '.csv'

    out_file = Path(out_file_str).resolve()

    # if table already exists, remove it
    if not args.no_remove and out_file.is_file():
        print(f'Removing existing table file: {str(out_file)}')
        out_file.unlink()

    yaml_config = {}

    # -------------------------- Reading YAML File(s) -----------------------------

    for yaml_name in args.yaml_file:

        yaml_file_path = Path(yaml_name).resolve()

        with open(yaml_file_path, 'r') as yaml_file:
            print(f'Getting data from YAML file "{yaml_name}"...')
            try:
                this_yaml_config = yaml.safe_load(yaml_file)
                # add to yaml_config dict
                for name, properties in this_yaml_config.items():
                    # make empty dict if needed (necessary for first YAML file)
                    if name not in yaml_config.keys():
                        yaml_config[name] = {}

                    for key, val in properties.items():
                        # it's ok if we override a property. It means that the YAML argument ordering matters.
                        if key in yaml_config[name].keys():
                            print(f'Name "{name}": existing property "{key}" overrided from value "{yaml_config[name][key]}" to value "{val}"')
                        yaml_config[name][key] = val

            except yaml.YAMLError as yaml_error:
                print(yaml_error)
                sys.exit(1)

    # ---------------------------------------------------------------------------

    # don't print header for --no-remove
    if not args.no_remove:
        MemInstance.print_header('csv', out_file=out_file)

    # ----------------------- Sbatch (Parallel) Process ----------------

    sbatch_job_count = 0
    # keep track of the objects so we can print the info after all jobs completed
    mem_instances_and_compilers = [] # list of (mem instance object, mem compiler object)
    compilers_to_generate = []
    if args.sbatch and args.run_compiler:
        # do a check that the user is not already running an sbatch "wrap" job.
        # If they are, don't run anything
        wrap_jobids = get_squeue_jobids(get_username(), 'wrap')
        assert not len(wrap_jobids), 'Cannot run the compilers with sbatch because we ' \
            'assume that all current wrap jobs for the user are from running this script. ' \
            f'Current wrap jobids: {wrap_jobids}'
        # assumption is cleared
        sbatch_helpers.USER_EXIT_PROMPT = False

        # remove all slurm*.out files - only used for being able to look through
        # the resulting slurm.out files for build errors.
        remove_slurm_out_files()

    # ------------------------------------------------------------------

    control_values = {}

    # Technically, a YAML file does not need to have control_values
    try:
        control_values = yaml_config.pop('control_values')
    except KeyError:
        pass

    for name, properties in yaml_config.items():
        # block filter
        assert 'top' in properties, f'Missing "top" property in YAML entry "{name}"'
        if args.block == ['ALL'] or properties['top'] in args.block:
            print(f'Working on {name}...')

            # add constant frequency to properties
            properties['freq'] = args.freq

            mem = MemInstance(name, properties, control_values)
            # MemInstance object will have mtype, depth, iobits, ports, gf12_macro (family), mux, subarrays.
            # We need to get the memory compiler object using those fields.

            mem_macro = mem.gf12_macro if args.tech == 'gf12' else mem.gf22_macro # only two choices

            compiler_obj = all_compilers[args.tech].get_compiler_from_name(mem_macro, mem.iobits)
            # if no compiler object found, assert
            assert compiler_obj is not None, f'Macro not found for process node {args.tech}: {mem_macro}'
            # set fields
            # if MemInstance already has a property, don't change it. If not, set a default.
            # The way this is done is going through the YAML properties, seeing if each one
            # applies to a memory compiler object, and if it does, setting that property.

            # Note: vt default is set to 'L' now.

            # some properties may be different between GF12 and GF22
            # For example, with the "mux" property, always take the property that
            # matches the process node, such as when using GF12, take "gf12_mux".
            # If that specific property does not exist, then take the general "mux" property.
            # Also apply this logic to all properties to allow changes in the future.

            prop_names_already_set = []

            for prop_name, prop_value in properties.items():
                # have to turn "gf12_mux" into "mux" for the memory compiler. Same for gf22
                # then, ignore other "mux" properties so we don't override
                orig_prop_name = prop_name
                if re.match(f'^{args.tech}', prop_name):
                    prop_name = re.sub(f'{args.tech}_', '', prop_name)

                if prop_name in compiler_obj.args.keys():
                    # once we figure out the name, if we already had a value for it, error.
                    # For example, this could happen if "mux" is set after "gf12_mux"
                    # (and vice versa), and we're using GF12.
                    # Could skip this current property, but throw an error instead.
                    if prop_name in prop_names_already_set:
                        print(f'ERROR: property "{orig_prop_name}" would override property "{prop_name}"')
                        sys.exit(1)

                    print(f'{prop_name} -> {prop_value}')
                    compiler_obj.args[prop_name] = prop_value

                    prop_names_already_set.append(prop_name)

            # ASSUMPTION if mtype is <removed>, use bit_write. Otherwise, no bit_write. This may change in the future.
            # only use bit_write with GF12
            if args.tech == 'gf12' and mem.mtype == '<removed>':
                compiler_obj.args['bit_write'] = '<removed>'

            # then set the full name

            compiler_obj.set_full_name()

            # also set the corner

            compiler_obj.set_corner(corner)

            # set application frequency for the memory compiler as well
            compiler_obj.args['freq'] = args.freq

            # also set nop_clk_gate_pct. It gets passed into MemInstance object, but
            # not the MemCompile object. Would rather do this here than somewhere in MemCompile
            # class. If there are more features like this, will need a way to generalize better.
            compiler_obj.nop_clk_gate_pct = mem.nop_clk_gate_pct

            # (mheim) run the compiler - this is because I don't want to add every memory entry
            # to the mem_requirements.yaml config file.
            # Now we have a switch to control this.

            # For using sbatch: Don't run the compiler if we are already running this particular
            # compiler in parallel!
            if args.run_compiler and not compiler_obj.get_full_name() in compilers_to_generate:
                # if using sbatch, the command will be "dispatched" and this
                # script will continue on. Parse the CSVs after all the jobs are completed.
                compiler_obj.run_compiler(dest_dir=out_mem_dir,
                                          argp=None,
                                          sbatch=args.sbatch)
            else:
                print('Not running any memory compilers.')
                compiler_obj.dest_dir = out_mem_dir

            # Regardless of if we are using sbatch or not, keep track of the objects
            # because later, we might want to tell the user which macros have not been generated.
            mem_instances_and_compilers.append((copy.deepcopy(mem),
                                                copy.deepcopy(compiler_obj)))

            # check if the compiler needs to be generated or not
            if not compiler_obj.get_full_name() in compilers_to_generate \
               and not compiler_obj.does_logfile_exist(out_mem_dir):
                sbatch_job_count += 1
                compilers_to_generate.append(compiler_obj.get_full_name())

        else:
            print(f'(block filter) skipping {name}')

    # -------------------------------------------------------------------------------------

    # done parsing the YAML data

    # restructure:
    # - always keep track of the objects and parse CSVs after collecting all of them
    # - if sbatch, job_count > 0 and run_compiler, wait for sbatch completion and check for build errors
    # - if not run_compiler and job_count > 0, show what needs to be generated. Regardless of sbatch switch

    if args.sbatch and sbatch_job_count > 0 and args.run_compiler:
        wait_for_sbatch_completion(1)
        sbatch_check_build_errors()

    if sbatch_job_count > 0 and not args.run_compiler:
        # if there are compilers that need to be generated, meminstance print_info()
        # will fail because there's no CSV for this memory compiler.
        # So we will quit out early to show what needs to be generated
        print(f'Number of compilers that need to be generated: {sbatch_job_count}')
        print('Macros not generated:')
        _ = [print(f'- {i}') for i in compilers_to_generate]
        sys.exit('Please either run the memory compilers (-r) or point the script to a ' \
                 'directory where memory compilers have been generated. Exiting')

    # ------------ Parse CSVs ------------
    for element in mem_instances_and_compilers:
        mem_obj = element[0]
        compiler_obj = element[1]

        compiler_obj.parse_all_csvs(args=None)
        mem_obj.find_macro([compiler_obj])
        mem_obj.print_info('csv', criteria, out_file=out_file, corner=corner)

    print('done.')
