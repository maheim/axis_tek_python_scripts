#!/usr/bin/env python3

import subprocess
import argparse
from mem_compiler import MemCompile
from mem_compiler import find_options, print_tbl_hdr
from pathlib import Path
import inspect
import sys
from gf22fdx_mem_compiler_types import *
from gf12_mem_compiler_types import *
import importlib
import sys
import copy
from sbatch_helpers import *
import sbatch_helpers

class mem_compiler_classes:
    def __init__(self, module_name):
        self.class_module_name = module_name
        self.module = importlib.import_module(self.class_module_name)

    def get_all_names(self):
        '''Get all defined class names in mem compile types, filtered by module name'''
        return [name for name, obj in inspect.getmembers(sys.modules[__name__], inspect.isclass)
                if self.class_module_name in inspect.getfile(obj)]

    def get_compiler_from_name(self, name, iobits, **kw_attrs):
        '''From input name, get defined class in file where memory compiler types are defined.
        Return an instance of the defined class.'''

        try:
            return getattr(self.module, name)(name, iobits, **kw_attrs)
        except AttributeError:
            return None

# -------------------------- Get Compilers -----------------------------------------

gf22_compilers = mem_compiler_classes('gf22fdx_mem_compiler_types') # pass in module name

GF22_ALL_MEM_TYPES = gf22_compilers.get_all_names()

gf12_compilers = mem_compiler_classes('gf12_mem_compiler_types') # pass in module name

GF12_ALL_MEM_TYPES = gf12_compilers.get_all_names()

all_compiler_options = {'gf22' : GF22_ALL_MEM_TYPES,
                        'gf12' : GF12_ALL_MEM_TYPES}

all_compiler_objs = {'gf22' : gf22_compilers,
                     'gf12' : gf12_compilers}

# ------------------------- Argument Parser ---------------------------------------

parser = argparse.ArgumentParser(description='Run memory compiler for particular memory types. ' \
                                 'This script is meant for looking at available options for each ' \
                                 'memory compiler and being able to run each one with particular ' \
                                 'configurations.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)

parser.add_argument('-l', '--list',
                    action='store_true',
                    help='List the memory compiler options. If -mc mem-compile-types switch is specified, the valid options for each memory compiler will be listed.')

parser.add_argument('-mc', '--mem-compile-types',
                    type=str.upper,
                    nargs='+',
                    choices=GF22_ALL_MEM_TYPES + GF12_ALL_MEM_TYPES,
                    help='Choose one or more memory types to compile. The choices listed are for gf22 and gf12, but only one tech may be chosen. The choices are the class names of the memory compilers.')

parser.add_argument('-t', '--tech',
                    type=str.lower,
                    choices=('gf22', 'gf12'),
                    help='Choose one technology. You can also use the --gf12 or --gf22 switch.')

# note: these options need to match all of the chosen memory compilers

parser.add_argument('-d', '--depth',
                    type=int,
                    help='Choose a particular depth (words).')

# width is same as iobits
parser.add_argument('-w', '--width',
                    type=int,
                    help='Choose a particular width (iobits).')

parser.add_argument('-m', '--mux',
                    help='Choose a particular mux option. Use "all" option to run all available choices.')

parser.add_argument('-b', '--bank',
                    '-s', '--subarrays',
                    help='Choose a particular bank/subarray option. GF22 uses banks, GF12 uses subarrays, and they mean the same thing for this script. Use "all" option to run all available choices.')

parser.add_argument('-v', '--vt',
                    type=str.upper,
                    nargs='?',
                    const='',
                    help='Choose a particular vt option. If not specified, all vt choices will be added as valid options.')

parser.add_argument('-r', '--redundancy',
                    type=str.upper,
                    # No default. if not specified, use the memory compiler's default redundancy
                    nargs='?',
                    const=None,
                    help='Choose a particular redundancy option.')

parser.add_argument('-c', '--cells-per-bitline',
                    type=int,
                    help='Choose a cells-per-bitline option. This only applies to GF22.')

parser.add_argument('-vs', '--voltage-supply',
                    type=str.upper,
                    nargs='?',
                    const='',
                    default='',
                    help='Choose a voltage supply option. GF12 feature value.')

parser.add_argument('-bw', '--bit-write',
                    type=str.upper,
                    nargs='?',
                    const = '',
                    default='',
                    help='Choose a bit-write option. GF12 feature value.')

parser.add_argument('-tm', '--test-mux',
                    type=str.upper,
                    nargs='?',
                    const='',
                    default='',
                    help='Choose a test-mux option. GF12 feature value.')

parser.add_argument('-pg', '--power-gating',
                    type=str.upper,
                    nargs='?',
                    const='',
                    default='',
                    help='Choose a power-gating option. Applies to GF22 and is a GF12 feature value.')

parser.add_argument('-ro', '--read-option',
                    type=str.upper,
                    nargs='?',
                    const='',
                    default='',
                    help='Choose a read-option option. GF12 feature value.')

parser.add_argument('-wa', '--write-assist',
                    type=str.upper,
                    nargs='?',
                    const='',
                    default='',
                    help='Choose a write-assist option. GF12 feature value.')

# ---- other arguments ----

parser.add_argument('-o', '--out-dir',
                    type=str,
                    default='out',
                    help='Directory to put memory compiler outputs relative to current directory.')

parser.add_argument('-dry', '--dry-run',
                    action='store_true',
                    help='Do not run the memory compiler.')

parser.add_argument('-f', '--force',
                    action='store_true',
                    help='Disable user prompts.')

parser.add_argument('--sbatch',
                    choices=('True', 'False'),
                    default='True',
                    help='Whether or not to use sbatch as opposed to srun. ' \
                    'Be aware that using ' \
                    'this switch assumes that all sbatch "wrap" jobs belong to this running process. ' \
                    'If you Ctrl-C while the memory compilers are running, ALL "wrap" jobs will be cancelled!')

parser.add_argument('--extra-args',
                    nargs='*',
                    help='Pass extra arguments to the command line (<removed> program) of each memory compiler. ' \
                    'Do not specify any dashes and use the format Key=Value. One dash will be added to the key, ' \
                    'or switch name. If the Value is True, the switch will be specified without the Value, ' \
                    'otherwise the Value is added to the switch.')

parser.add_argument('--help-gf22',
                    action='store_true',
                    help='Show the GF22 <removed> program help menu')

# The GF12 help menu looks very similar to the GF22 one, but will leave this as a distinct option.
parser.add_argument('--help-gf12',
                    action='store_true',
                    help='Show the GF12 <removed> program help menu')

parser.add_argument('--table',
                    type=str,
                    help='Pass in a filepath to put the memory macro PPA data in a CSV table.')

parser.add_argument('--new-table',
                    action='store_true',
                    help='Make a new table file and print the header to it instead of adding ' \
                    'data to an existing table.')

# ---------- matching args from gen_mem_test to specify to MemCompile.run_compiler() -------------

# quiet
parser.add_argument('--quiet',
                    action='store_true')

# overwrite
parser.add_argument('--overwrite',
                    action='store_true')

# verbose
parser.add_argument('--verbose',
                    action='store_true')

if __name__ == "__main__":

    args = parser.parse_args()

    print(f'args: {args}')

    # ------------------------------- Extra Help Menus ------------------------

    if args.help_gf22:
        # show the help menu for one of the GF22 memory compilers, then exit
        help_cmd = '<removed> -help'
        subprocess.run(help_cmd, shell=True)
        sys.exit(0)
    elif args.help_gf12:
        # show the help menu for one of the GF12 memory compilers, then exit
        help_cmd = '<removed> -help'
        subprocess.run(help_cmd, shell=True)
        sys.exit(0)


    dry_run = args.dry_run

    # check tech arg - everything except for listing all compilers
    if not (args.list and args.mem_compile_types is None):
        assert(args.tech is not None), 'Must specify technology argument: either gf22 or gf12.'
        tech = args.tech

    # ------------------------------- List Memory Compilers ------------------------


    # If -l list argument specified, list the memory compilers (space-separated) and then exit
    # Listing all memory compiler options is for when args.mem_compile_types is None
    if args.list and args.mem_compile_types is None:
        print(f'GF22 memory compiler options: {" ".join(all_compiler_options["gf22"])}')
        print(f'GF12 memory compiler options: {" ".join(all_compiler_options["gf12"])}')
        sys.exit(0)

    # We can also list the valid options for specific memory compilers. List those options and then exit. Need a technology argument.

    # --------------- List Valid Options for Each Memory Compiler -----------------

    # gf12 feature values
    gf12_feature_values = ['voltage_supply', 'bit_write', 'test_mux', 'power_gating', 'read_option', 'write_assist']

    if args.list and args.mem_compile_types is not None:

        for name in args.mem_compile_types:
            compiler_obj = all_compiler_objs[tech].get_compiler_from_name(name, 'dummy') # name, iobits
            # we want to know the valid options for cells-per-bitline, mux, bank, depth range, width range
            print(f'\nOptions for {name} compiler:')
            for cells_per_bitline in compiler_obj.cbl.keys():
                print(f'\tCells-per-bitline option: {cells_per_bitline}')
                for mux in compiler_obj.cbl[cells_per_bitline]:
                    print(f'\t\tMux option: {mux}')
                    for bank in compiler_obj.cbl[cells_per_bitline][mux]:
                        if tech == 'gf12':
                            # instead of banks we have subarrays.
                            print(f'\t\t\tSubarrays option: {bank}')
                        else:
                            print(f'\t\t\tBank option: {bank}')

                        min_depth = compiler_obj.cbl[cells_per_bitline][mux][bank][0][0]
                        max_depth = compiler_obj.cbl[cells_per_bitline][mux][bank][0][1]
                        stride_depth = compiler_obj.cbl[cells_per_bitline][mux][bank][0][2]

                        min_width = compiler_obj.cbl[cells_per_bitline][mux][bank][1][0]
                        max_width = compiler_obj.cbl[cells_per_bitline][mux][bank][1][1]
                        stride_width = compiler_obj.cbl[cells_per_bitline][mux][bank][1][2]

                        print(f'\t\t\t\tDepth range: {min_depth} to {max_depth} with stride of {stride_depth}')
                        print(f'\t\t\t\tWidth range: {min_width} to {max_width} with stride of {stride_width}')

            # print other options
            print(f'\tVt options: {compiler_obj.vt}')
            print(f'\tRedundancy options: {compiler_obj.redundancy}')

            # gf12-specific options
            for feature in gf12_feature_values:
                if hasattr(compiler_obj, feature):
                    print(f'\t{feature} options: {getattr(compiler_obj, feature)}')

        sys.exit(0)

    # ---------------------- Set Up Each Memory Compiler and Do a Dry Run -------------------------------------

    # extra args to <removed> program
    extra_args_str = ''
    if args.extra_args is not None:
        for extra_arg in args.extra_args:
            (switch, value) = extra_arg.split('=')
            extra_args_str += f' -{switch}'
            if value != 'True':
                if type(value) == str:
                    # wrap with quotes
                    value = f'"{value}"'

                extra_args_str += f' {value}'

    # check depth and width arg. They're required for now, despite being listed as optional.
    assert not (args.depth is None or args.width is None), 'Depth and width args are required at the moment'

    # whether we use sbatch or not (srun)
    use_sbatch = args.sbatch == 'True'

    # get instances of compiler names
    compiler_names = args.mem_compile_types

    valid_compiler_options = all_compiler_options[tech]

    assert len(compiler_names), f'No compilers were found. Options were: {valid_compiler_options}'

    compilers = []

    compilers_to_be_generated = []

    compiler_args = {} # kw_attrs for memory compiler object init
    for feature in gf12_feature_values:
        compiler_args[feature] = vars(args)[feature]

    for name in compiler_names:

        compiler_obj = all_compiler_objs[tech].get_compiler_from_name(name, args.width, **compiler_args)

        print(f'Compiler Table (cells-per-bitline -> mux -> bank -> depth range, width range):\n{compiler_obj.cbl}\n')

        # using find_options()

        # For now, if redundancy is not specified, we will use the memory compiler's default redundancy option,
        # not all possible options.

        mux_match = list(compiler_obj.cbl[args.cells_per_bitline]) if args.mux == 'all' else int(args.mux)

        # get the keys from the first mux option. each mux option has every bank/subarray option.
        first_mux_option = list(compiler_obj.cbl[args.cells_per_bitline])[0]
        bank_match = list(compiler_obj.cbl[args.cells_per_bitline][first_mux_option]) if args.bank == 'all' else int(args.bank)

        print(f'Finding options for {name}')

        # if vt not specified, use all vt options for this compiler
        vt_match = args.vt if args.vt != '' else None

        valid_options = find_options(args.depth,
                                     args.width,
                                     compiler_obj.cbl,
                                     compiler_obj.vt, # pass in all available vt choices.
                                     compiler_obj.default_redundancy, # pass in default redundancy
                                     mux_match=mux_match,
                                     bank_match=bank_match, # subarray for gf12
                                     vt_match=vt_match,
                                     redundancy_match=args.redundancy,
                                     cells_per_bitline_match=args.cells_per_bitline)

        print(f'Found {len(valid_options)} valid options for {name}.')

        if len(valid_options):
            for option in valid_options:
                print(f'\nValid option: name {name}, depth (words) {args.depth}, width (iobits) {args.width}, ' \
                      f'mux {option["mux"]}, cells_per_bitline {option["cells_per_bitline"]}, bank/subarray {option["bank"]}, ' \
                      f'vt {option["vt"]}, redundancy "{option["redundancy"]}"')

                compiler_obj.args['cells_per_bitline'] = option['cells_per_bitline']
                compiler_obj.args['mux'] = option['mux']
                compiler_obj.args['words'] = option['words']

                if tech == 'gf22':
                    compiler_obj.args['bank'] = option['bank']
                else:
                    # again, unfortunately the name is bank but we mean subarrays
                    compiler_obj.args['subarrays'] = option['bank']

                compiler_obj.args['iobits'] = option['iobits']
                compiler_obj.args['vt'] = option['vt']
                compiler_obj.args['redundancy'] = option['redundancy']

                # GF12 feature values from arguments
                # (mheim) I would rather not blow up the find_options() function to take into account the GF12
                # feature values, so we will check for valid feature values here, and use them if valid.

                compiler_obj.set_full_name()

                # hack dry run to be true, then confirm, then turn off dry_run, then run_compiler
                # This is unfortunate, but I need to pass the dry_run arg to run_compiler().
                args.dry_run = True

                compiler_obj.run_compiler(args.out_dir, args, sbatch=use_sbatch, extra_args=extra_args_str)

                # append a deep copy of the compiler object. If we do not do this,
                # we end up running the same compiler multiple times with the same options.
                compilers.append(copy.deepcopy(compiler_obj))

                # if --overwrite used, compilers will run
                # Otherwise, the compiler will NOT run if there is a logfile existing in the out_dir
                # Also, we want to avoid running the same macro multiple times
                if (not args.overwrite and not compiler_obj.does_logfile_exist(args.out_dir) \
                    and not compiler_obj.get_full_name() in compilers_to_be_generated) \
                    or args.overwrite:
                    compilers_to_be_generated.append(compiler_obj.get_full_name())

    print(f'\nTotal number of valid options: {len(compilers)}')
    print(f'Total number of memories to be generated: {len(compilers_to_be_generated)}')
    if len(compilers_to_be_generated):
        print('Macros to be generated:')
        _ = [print(i) for i in compilers_to_be_generated]

    # ------------------------ Real Run of Each Memory Compiler ------------------------


    # The dry_run variable was what the user originally specified.
    if not dry_run:
        if not args.force:
            _ = input('Please review what will be run. Press enter to continue: ')

        # check that the user is not already running an sbatch "wrap" job
        if use_sbatch:
            # do a check that the user is not already running an sbatch "wrap" job.
            # If they are, don't run anything
            wrap_jobids = get_squeue_jobids(get_username(), 'wrap')
            assert not len(wrap_jobids), 'Cannot run the compilers with sbatch because we ' \
                'assume that all current wrap jobs for the user are from running this script. ' \
                f'Current wrap jobids: {wrap_jobids}'
            # assumption is cleared
            sbatch_helpers.USER_EXIT_PROMPT = False

        # set dry run back to False since we're really running the compilers this time
        args.dry_run = False

        for compiler in compilers:
            compiler.run_compiler(args.out_dir, args, sbatch=use_sbatch, extra_args=extra_args_str)

        if use_sbatch:
            wait_for_sbatch_completion(1)

        # CSV table to append to
        table_out_file = None
        if args.table is not None:
            table_out_file = Path(args.table).resolve()

        if table_out_file is not None:

            # if user wants a new table, remove existing table if there is one
            # and print the header to the new table.
            if args.new_table:
                if table_out_file.is_file():
                    print(f'Removing existing table file {str(table_out_file)}')
                    table_out_file.unlink()

                print_tbl_hdr(gen_csv=True, out_file=table_out_file)

            print('Parsing CSVs ...')

            for compiler in compilers:
                    compiler.parse_all_csvs(args)

                    compiler.print_summary('csv', out_file=table_out_file)

                    print(f'Added {compiler.name} summary to table file {str(table_out_file)}')

    print('done.')
