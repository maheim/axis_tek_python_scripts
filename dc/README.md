## DC (Design Compiler) Scripts

The two DC scripts we have are 1) `autorun_dc.py`, a script to automate running DC and post-processing the results, and 2) `dc_warnings.py`, a script to collect and organize the warnings reported by DC.

The `autorun_dc.py` script contains the following functions:
* Runs DC on one or more chosen sub-blocks.
* Copies DC output logfiles and reports to a common location.
* Has support to run DC multiple times with a cross-product of constraints, such as multiple corners.
* Has support to parse the DC output logfiles, organizing PPA (performance, power, area) data into a table for multiple runs. This step may be run on its own, and the user would point the script to a directory with multiple run output directories.
* Has support to automatically email run results through AWS, displaying information on whether the run passed or failed, what errors there were if any, and a summary of the PPA data.