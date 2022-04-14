## parse_power_report.py

This script parses a hierarchical power report in either the Power Compiler or Prime Power format. The main function of the script is to convert the text report to a CSV table containing power data for each element of the hierarchy, and showing the full hierarchy for every entry. The CSV table is much easier to analyze and to use for data visualization than the text report from these tools. The motivation for this script was the effort to reduce the power consumption in the chip.

An enhancement to the script was to automatically graph data for the user. To do this, the user would specify a filter (a regex string), and the script would then use this filter on the data collected from the input text report. Matching entries would be shown on a graph, and there were some additional arguments to control the graph properties. These were the type of graph (ex. pie, bar, barh), the metric to graph, the maximum number of entries to show on a graph, and an additional switch to save the filtered dataset to another CSV file.

To get notes on how to use the script's switches, check out the help menu with:
`./parse_power_report.py -h`

A note on filtering:

The hierarchy of each entry is dot-separated. An example entry is `<removed>`. To see this "flattened" hierarchy notation in the output CSV table instead of a column for each hierarchy element, use the --path-combine switch.

Keep in mind that the -gt (graph-top) argument really is a regex filter. Previously, it was a string of what the top entry was to graph, and the data for the children would be graphed. Now, since we use a pure filter, only matching entries are included in the graph, and there is no notion of graphing 'children' of the parent entry. So, if you want to graph the data for the children of sub-block `X`, you would need to use the filter `.*\.X\.\w+$` (start with any characters, then a literal dot, then `X`, then another literal dot to go another level deeper in the hierarchy, then some non-dot characters, terminating if there is another literal dot).

A note on graph entries:

By default, 20 entries are included in a graph. If filtering results in more entries than this maximum number of entries, the "extra" entries (after sorting) are consolidated into one "Other" category. This was done to preserve information when using a pie chart, but is useful in general when there are a lot of entries. This number can be changed with the -ge (graph-entries) switch.


## Examples


#### Pie chart for children of <subblock> Total Power data (plot shown, not saved), show 15 entries:

`./parse_power_report.py example_prime_power.report -o hier_power.csv -gt '<subblock>\.<subblock>\.\w+$' -gk pie -gc 'Total Power (W)' -ge 15`

***

#### Horizontal bar graph for a <subblock> node's total power data, showing 50 entries because the default number of entries is 20 which leads to a big 'Other' category:

`./parse_power_report.py example_prime_power.report -o hier_power.csv -gt '.*\.<subblock>\.\w+$' -gk barh -gc 'Total Power (W)' -ge 50`

***

#### Vertical bar graph for Total Power in <subblock>:

`./parse_power_report.py example_prime_power.report -o hier_power.csv -gt '<subblock>' -gk bar -gc 'Total Power (W)'`
