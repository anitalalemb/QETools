# Calculation history

Use `qehis DIRECTORY` or `qehis FILE.out`. `qehistory` is an equivalent alias.

The heading is **QE CALCULATION HISTORY**.

| Column | Meaning |
| --- | --- |
| MODIFIED UTC | File modification time in UTC, including seconds. This is not the calculation start or end time. |
| TYPE | Calculation type inferred from the available output evidence. |
| MIN EST (Ry) | Minimum printed SCF accuracy estimate across the latest run in the file. This does not rank physical results. |
| LAST EST (Ry) | Last printed estimate in that run. It may belong to an earlier electronic cycle. |
| LAST ENDED | Last electronic cycle ending with CONVERGED or NOT CONVERGED. NONE means neither outcome was found. |
| LATEST CYCLE | Status of the last parsed electronic cycle. This does not establish whether a process is running. |
| OVERALL | Combined execution/electronic/ionic/EXX assessment. Electronic convergence alone cannot certify a relaxed structure. |

A dash means no printed value; zero remains zero. Appended files are scoped to the latest program run. Files are ordered by modification time, then filename for ties. Copying files can change their order.

The selected converged SCF uses final printed accuracy, then newest modification time, then filename. It follows the `qescf --best` rule. It is not a comparison of physical accuracy across different settings.

## Validation: 14 October 2026

All 79 local tests pass. Six dedicated history tests cover the exact heading, incomplete/failed/restarted cycles, zero versus missing estimates, UTC timestamps, filename ties, and identical alias output for files/directories with spaces and help. Existing scientific tests cover SCF selection agreement and EXX relaxation guards.

This is a bounded interface correction. A full independent history audit across real logs, cycle identifiers for printed estimates, broader calculation types, and runtime-error edge cases remain open. The supplied theta18 table cannot be independently verified without its original output files and metadata. The NERSC v0.7 installation has not been changed or inspected.
