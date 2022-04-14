#!/usr/bin/env python3


# (mheim) 04_12_2022 reflection:
# This was written in March 2022. This script had some challenges to work through that were
# really interesting. This script shows improvement in its style compared to scripts
# written earlier in the job.

################################################################################
#
# parse_power_report.py
#   - Responsible for parsing a single power report file
#   - Generates a CSV containing hierarchy information and power numbers
#
################################################################################

# TODO_mheim not accounting for top-level logic in graphs (most relevant to pie charts, even if it
# is a small percentage of the power).

from pathlib import Path
import re
import pandas as pd
from matplotlib import pyplot as plt

import argparse
parser = argparse.ArgumentParser(description='This script parses a hierarchical power report file ' \
                                 'and converts the data to a CSV containing the hierarchy ' \
                                 'information and power data.',
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
parser.add_argument('path',
                    type=str,
                    help='Specify a path to the power logfile to parse.')

parser.add_argument('-o', '--output-table',
                    type=str,
                    default='hier_power.csv',
                    help='Specify a name for the output CSV table.')

parser.add_argument('-v', '--verbose',
                    action='store_true',
                    help='Prints some extra messages')

parser.add_argument('-d', '--max-depth',
                    type=int,
                    help='Specify a maximum hierarchy depth. Depth 0 is top level, usually "<block>"')

parser.add_argument('-p', '--path-combine',
                    action='store_true',
                    help='Instead of putting hierarch leves in separate columns, create one ' \
                    'column with a full dot-separated hierarchy')

parser.add_argument('-g', '--graph',
                    action='store_true',
                    help='Enable graphing')

parser.add_argument('-ng', '--no-graph',
                    action='store_true',
                    help='Force preventing generation of graph. Helpful if you are interested in ' \
                    'the filtered dataset and there are a lot of entries, which is slow to plot.')

parser.add_argument('-gt', '--graph-top',
                    type=str,
                    nargs='*',
                    help='Regex string to search for in a dot-separated (or "path-combine") hierarchy. Data is filtered ' \
                    'and only matching entries are included. Make sure to use "$" if you don\'t want to include children data.' \
                    'Note: The script will add the ax_stella prefix automatically. You can also use multiple filters that ' \
                    'are ORed together.')

parser.add_argument('-gc', '--graph-col',
                    type=str,
                    default='Pct of Total Power',
                    help='Column of CSV table to plot.')

parser.add_argument('-gk', '--graph-kind',
                    type=str,
                    default='pie',
                    help='Type of chart to create, such as pie or barh.')

parser.add_argument('-ge', '--graph-entries',
                    type=int,
                    default=20,
                    help='Max number of entries to show in graph. Helps if there are many entries.')

parser.add_argument('-s', '--save-plot',
                    action='store_true',
                    help='Instead of showing plot, save to an image file.')

parser.add_argument('-sn', '--save-name',
                    type=str,
                    default='hier_power_plot.png',
                    help='Optional alternate name or path for saved power plot.')

parser.add_argument('-gcsv', '--graph-csv',
                    # confusing switch name but filtering was done for the graphing process
                    type=str,
                    nargs='?',
                    const='filt_df.csv',
                    help='Filtering is done to get the data to visualize. This switch allows for ' \
                    'saving the filtered dataset to a CSV with any path/name.')

class one_power_group():
    def __init__(self,
                 logfile_format,
                 instance_name,
                 type_name,
                 switching_power,
                 internal_power,
                 leakage_power,
                 total_power,
                 pct,
                 glitch_power,
                 x_tran_power):

        self.logfile_format = logfile_format
        self.instance_name = instance_name
        self.type_name = type_name
        self.switching_power = switching_power
        self.internal_power = internal_power
        self.leakage_power = leakage_power
        self.total_power = total_power
        self.pct = pct
        self.glitch_power = glitch_power
        self.x_tran_power = x_tran_power

    def get_data(self):
        if self.logfile_format == 'Power Compiler':
            return (self.switching_power,
                    self.internal_power,
                    self.leakage_power,
                    self.total_power,
                    self.pct,
                    self.type_name)
        else:
            # prime power
            return (self.internal_power,
                    self.switching_power,
                    self.leakage_power,
                    self.glitch_power,
                    self.x_tran_power,
                    self.total_power,
                    self.pct,
                    self.type_name)

    @staticmethod
    def get_header(logfile_format='Power Compiler'):
        if logfile_format == 'Power Compiler':
            power_num_cols = ['Switching Power (mW)',
                              'Internal Power (mW)',
                              'Leakage Power (uW)',
                              'Total Power (mW)',
                              'Pct of Total Power',
                              'Module Type Name']
        else:
            power_num_cols = ['Internal Power (W)',
                              'Switching Power (W)',
                              'Leakage Power (W)',
                              'Glitch Power (W)',
                              'X-tran Power (W)',
                              'Total Power (W)',
                              'Pct of Total Power',
                              'Module Type Name']

        return power_num_cols

    def __repr__(self):
        if self.logfile_format == 'Power Compiler':
            return f'Switching Power: {self.switching_power} mW\t' \
                f'Internal Power: {self.internal_power} mW\t' \
                f'Leakage Power: {self.leakage_power} uW\t' \
                f'Total Power: {self.total_power} mW\t' \
                f'Pct of Total: {self.pct}'
        else:
            return f'Internal Power: {self.internal_power} W\t' \
                f'Switching Power: {self.switching_power} W\t' \
                f'Leakage Power: {self.leakage_power} W\t' \
                f'Glitch Power: {self.glitch_power} W\t' \
                f'X-tran Power: {self.x_tran_power} W\t' \
                f'Total Power: {self.total_power} W\t' \
                f'Pct of Total: {self.pct}'

class power_hierarchy():
    def __init__(self,
                 verbose=False,
                 max_depth=None,
                 path_combine=False):

        # Stored data: nested dictionary, but each key has its own power group.
        # This data is not very readable so it's recommended to get a 2-D list of
        # the data using get_2d_hierarchy_data() after filling this dictionary
        self.data = {}

        self.power_num_count = None

        # args
        self.verbose = verbose
        self.max_depth = max_depth
        self.path_combine = path_combine

        # logfile format: default choice is Power Compiler. If we see
        # distinguishing features of Prime Power, we will choose that instead.
        self.logfile_format = 'Power Compiler'

    def iterate_data(self, in_dict):
        for key, val in in_dict.items():
            if isinstance(val, dict):
                for pair in self.iterate_data(val):
                    yield (key, *pair)

            elif isinstance(val, one_power_group):
                yield val.get_data()

    def get_2d_hierarchy_data(self):
        # now that we've read the logfile, make sure to set the count of power numbers
        self.power_num_count = len(one_power_group.get_header(self.logfile_format))

        flat_data = []
        for pair in self.iterate_data(self.data):
            flat_data.append(list(pair))

        # pad data between hierarchy entries and power numbers
        max_hier_entries = max([len(i) for i in flat_data])
        pad_index = -1 * self.power_num_count
        for entry in flat_data:
            entry_length = len(entry)
            # last entries are the 5 power numbers: s_p, i_p, l_p, t_p, %
            # different amount of numbers for prime power format

            # path_combine process: if using path_combine, condense hierarchy into
            # one dot-separated hierarchy of instance names.
            # Otherwise, pad with None as needed.

            if self.path_combine:
                hier_level_ind = entry_length - self.power_num_count
                hier_str = ['.'.join(entry[:hier_level_ind])]
                entry[:hier_level_ind] = hier_str
            else:
                entry[pad_index:pad_index] = [None] * (max_hier_entries - entry_length)

        return flat_data

    def get_filtered_dataframe(self, top_name, plot_col, graph_kind, max_entries):

        # new filtering behavior:
        # use the path-combine dot notation and filter by top_name being at
        # the beginning of the dot-notated hierarchy. But don't include the entry
        # for exactly the top_name, we want to look at the data for the children one level down.

        # We may have multiple filters now (top_name is a list)

        data_2d = []
        for pair in self.iterate_data(self.data):
            dot_notated_hier = list(pair)
            hier_level_ind = len(dot_notated_hier) - self.power_num_count
            hier_str = ['.'.join(dot_notated_hier[:hier_level_ind])]
            dot_notated_hier[:hier_level_ind] = hier_str
            data_2d.append(dot_notated_hier)

        filt_str_list = top_name
        for i in range(len(filt_str_list)):
            if not re.search('\^*<top block>', filt_str_list[i]):
                filt_str_list[i] = '^<top block>\.' + filt_str_list[i]

            if self.verbose:
                print(fr'Filter string {i}: "{filt_str_list[i]}"')

        filters_used = {}
        for filt in filt_str_list:
            filters_used[filt] = False

        filtered_data_2d = []

        for entry in data_2d:
            # go through each filter and see if there's a match. If there is, move on to next entry.
            for filt_str in filt_str_list:
                if re.search(filt_str, entry[0]):
                    filtered_data_2d.append(entry)
                    filters_used[filt_str] |= True
                    break # next iteration of outer loop

        # check that each filter was used
        all_filters_used = True
        unused_filters = ''
        for filt in filt_str_list:
            if not filters_used[filt]:
                unused_filters += fr'"{filt}", '

            all_filters_used &= filters_used[filt]

        # This isn't absolutely necessary, but figured it would be nice to know if a filter wasn't used.
        assert all_filters_used, fr'Filter(s) did not collect any data: {unused_filters}'

        # --- create dataframe

        assert len(filtered_data_2d), f'No filtered data was collected. Check -gt argument'

        filt_df = pd.DataFrame(filtered_data_2d)

        # make first column the index
        filt_df.set_index(0, inplace=True)

        # delete first row, it's empty and comes from the index of the top_level column
        filt_df.index.name = 'Hierarchy Element'

        filt_df.columns = one_power_group.get_header(self.logfile_format)

        # sort values - useful for bar graphs in particular
        filt_df.sort_values(plot_col, ascending=False, inplace=True)

        # cut entries if we are past max df length - plot looks ugly if there are too many entries
        # for pie chart, need to make an 'other' category for entries that are cutoff to retain
        # the whole picture.
        if len(filt_df) > max_entries:
            other_range = list(range(max_entries, len(filt_df)))
            if self.verbose:
                print(f'Condensing rows {other_range[0]} to {other_range[-1]} to "Other" category due to {max_entries} max graph entries')

            other_series = filt_df.iloc[other_range].sum()
            other_series['Module Type Name'] = 'Other'
            other_series.name = 'Other'
            filt_df.drop(filt_df.index[other_range], inplace=True)
            filt_df = filt_df.append(other_series)

            # sort the values again - this was due to adjacent similar colors
            if graph_kind == 'pie':
                filt_df.sort_values(plot_col, ascending=False, inplace=True)

        return filt_df

    def read_logfile(self, logfile_path):

        # there may be a number like 3.7858e-02 or 68.9913
        sci_num = '\d+\.\d+e?[-+]?\d*'

        look_for_power_nums = False

        # logfile_path is a Path object, so we can open that
        with open(logfile_path) as logfile:
            try:

                hier_list = []
                prev_level = -1

                for line_no, line in enumerate(logfile):
                    if not look_for_power_nums:
                        if re.search('Hierarchy', line):
                            look_for_power_nums = True
                            # skip this line and the all-dash line that follows. This is done
                            # to suppress the warning that no match is found for finding power numbers.
                            next(logfile)
                            continue

                        elif re.search('Glitch|X-tran', line):
                            # looking for logfile format:
                            # distinguishing factors for prime power format is Glitch Power and X-tran Power.
                            self.logfile_format = 'Prime Power'

                    # 2 spaces will precede the first level of hierarchy.
                    # 2 spaces are added for each subsequent level of hierarchy.
                    # Keep track of where we are in the hierarchy as we add data
                    # to the dictionary.
                    else:
                        # try to get power numbers. If there is a format mismatch,
                        # just skip the line

                        # first name is instance_name, second one is type_name

                        # for Power Compiler:
                        # instance_name, type_name, switch_power, internal_power, leakage_power, total_power, pct

                        # for Prime Power:
                        # instance_name, type_name, internal_power, switch_power, leakage_power, peak power (ignore),
                        # peak_time (ignore), glitch_power, x_tran_power, total_power, pct
                        power_group_search = None
                        glitch_power = None
                        x_tran_power = None

                        if self.logfile_format == 'Power Compiler':
                            power_group_search = re.search(f'(\s*)(\w+)\s+\(*(\w*)\)*\s+({sci_num})\s+({sci_num})\s+({sci_num})\s+({sci_num})\s+({sci_num})\s+', line)
                            if power_group_search:
                                switch_power = float(power_group_search.group(4))
                                internal_power = float(power_group_search.group(5))
                                total_power = float(power_group_search.group(7))
                                pct = float(power_group_search.group(8))
                        else:
                            # prime power
                            power_group_search = re.search(f'(\s*)(\w+)\s+\(*(\w*)\)*\s+({sci_num})\s+({sci_num})\s+({sci_num})\s+{sci_num}\s+{sci_num}-{sci_num}\s+' \
                                                           f'({sci_num})\s+({sci_num})\s+({sci_num})\s+({sci_num})', line)
                            if power_group_search:
                                internal_power = float(power_group_search.group(4))
                                switch_power = float(power_group_search.group(5))
                                glitch_power = float(power_group_search.group(7))
                                x_tran_power = float(power_group_search.group(8))
                                total_power = float(power_group_search.group(9))
                                pct = float(power_group_search.group(10))

                        if power_group_search:
                            # each level corresponds to 2 whitespace characters,
                            # meaning how deep in the hierarchy we are
                            level = len(power_group_search.group(1)) // 2

                            # max depth process: skip if exceeding max_depth
                            # This is easier than getting all the data and chopping data
                            # with a hierarchy larger than max_depth.
                            if self.max_depth is not None and level > self.max_depth:
                                continue

                            instance_name = power_group_search.group(2)
                            type_name = power_group_search.group(3)
                            # leakage power is in same place in group search between Power Compiler and Prime Power
                            leakage_power = float(power_group_search.group(6))

                            power_group_obj = one_power_group(self.logfile_format,
                                                              instance_name,
                                                              type_name,
                                                              switch_power,
                                                              internal_power,
                                                              leakage_power,
                                                              total_power,
                                                              pct,
                                                              glitch_power,
                                                              x_tran_power)

                            # add to data
                            if level > prev_level:
                                hier_list.append(instance_name)
                            elif level < prev_level:
                                # remove last element, might be multiple times
                                _ = [hier_list.pop() for i in range(prev_level - level)]

                            hier_list[-1] = instance_name

                            # what we do here is index a dictionary a variable amount of
                            # times. Start with the outer dictionary, then index by each
                            # element in the hierarchy. Then, once we are in the dictionary
                            # we want to be in, we can index once to set the new power group
                            # data. This only works because once we index once, the whole
                            # dictionary actually will update. If this were not the case,
                            # the tricky step would be putting the updated inner dictionary
                            # back into the outer dictionary.
                            data = self.data
                            for hier_elem in hier_list:
                                if hier_elem not in data.keys():
                                    data[hier_elem] = {}
                                data = data[hier_elem]
                                if isinstance(data, one_power_group):
                                    # debug assistance - script will fail on following line anyway
                                    print(f'HIERARCHY: {hier_list}')

                            # self.data will update
                            data[instance_name] = power_group_obj

                            # set previous fields
                            prev_level = level
                        elif self.verbose:
                            # no match found and verbose - show warning
                            print(f'Warning: no match. Line {line_no}: "{line.rstrip()}"')

            except Exception as e:
                print(f'In file: "{logfile_path}". Line number: {line_no}')
                print(f'Line: "{line.rstrip()}"')
                raise


if __name__ == "__main__":
    args = parser.parse_args()

    # figure out path argument. If directory used,
    # find a logfile with 'power.rpt' in the name.
    # Make sure there is only one of those.
    path = Path(args.path).resolve()
    assert path.is_file(), f'Input path is not a file'

    pwr = power_hierarchy(verbose=args.verbose,
                          max_depth=args.max_depth,
                          path_combine=args.path_combine)

    print(f'Parsing logfile: {path} ...')

    pwr.read_logfile(path)

    assert len(pwr.data), 'No data was collected.'

    data_2d = pwr.get_2d_hierarchy_data()

    # arrange columns
    max_hier_entries = max([len(i) for i in data_2d])

    power_num_cols = one_power_group.get_header(pwr.logfile_format)

    cols = ['Hierarchy'] * (max_hier_entries - pwr.power_num_count) + power_num_cols

    df = pd.DataFrame(data_2d, columns=cols)

    out_table_path = Path(args.output_table).resolve()
    if out_table_path.is_file():
        out_table_path.unlink()

    df.to_csv(out_table_path)

    print(f'Table written to {out_table_path}')

    # graphs
    if args.graph or args.graph_top is not None:

        graph_top = ['^<block>\.\w+$'] if args.graph_top is None else args.graph_top

        assert args.graph_col in cols, f'Column not found: "{args.graph_col}"'

        filt_df = pwr.get_filtered_dataframe(graph_top,
                                             args.graph_col,
                                             args.graph_kind,
                                             args.graph_entries)

        if args.verbose:
            print('Filtered dataframe:')
            print(filt_df)

        # optionally save filtered dataset
        if args.graph_csv:
            # if table exists, remove it
            filt_csv_path = Path(args.graph_csv).resolve()
            if filt_csv_path.is_file():
                print(f'Removing existing table file at {filt_csv_path}')
                filt_csv_path.unlink()

            filt_df.to_csv(filt_csv_path)
            print(f'Saved filtered dataset to {filt_csv_path}')

        if args.no_graph:
            if args.verbose:
                print('Not creating a graph')
        else:
            # have to escape '$' chars in title string. Otherwise, matplotlib
            # interprets them as mathtex expressions
            filters = ', '.join([fr'"{i}"' for i in graph_top]).replace('$', '\$')
            title = fr'{args.graph_col} from Filters: {filters}'

            plot_kwargs = {'kind' : args.graph_kind,
                           'y' : args.graph_col,
                           'figsize' : (20, 15),
                           'legend' : False,
                           'title' : title
                           }

            if args.graph_kind == 'pie':
                plot_kwargs['ylabel'] = ''
                plot_kwargs['autopct'] = '%1.1f%%'
                plot_kwargs['normalize'] = True
            else:
                plot_kwargs['ylabel'] = args.graph_col
                plot_kwargs['colormap'] = 'jet'

            filt_df.plot(**plot_kwargs)
            plt.tight_layout()
            if args.save_plot:
                save_path = Path(args.save_name).resolve()
                assert save_path.parents[0].is_dir(), \
                    f'Could not find save directory: {save_path.parents[0]}'
                plt.savefig(save_path)
            else:
                plt.show()

    print('done.')
