# Calculation history

Use `qehis DIRECTORY` or `qehis FILE.out`. `qehistory` is an equivalent alias.

The heading is **QE CALCULATION HISTORY**.

| Column | Meaning |
| --- | --- |
| MODIFIED UTC | File modification time in UTC, including seconds. This is not the calculation start or end time. |
| TYPE | Calculation type inferred from the available output evidence. |
| MIN EST (Ry) | Minimum printed SCF accuracy estimate across the latest run in the file. This does not rank physical results. |
| LAST EST (Ry) | Last printed estimate in that run. It may belong to an earlier electronic cycle. |
| EST CYCLE | Electronic cycle containing LAST EST. A dash means association is unavailable. |
| LATEST EST | Last printed accuracy estimate of the latest electronic cycle, in Ry. A dash stays empty even if an earlier cycle printed a value. |
| LAST ENDED | Last electronic cycle ending with CONVERGED or NOT CONVERGED. NONE means neither outcome was found. |
| LATEST CYCLE | Status of the last parsed electronic cycle. This does not establish whether a process is running. |
| OVERALL | Combined execution/electronic/ionic/EXX assessment. Electronic convergence alone cannot certify a relaxed structure. |

A dash means no printed value; zero remains zero. Appended files are scoped to the latest program run. Files are ordered by modification time, then filename for ties. Copying files can change their order.

The selected converged SCF uses final printed accuracy, then newest modification time, then filename. It follows the `qescf --best` rule. It is not a comparison of physical accuracy across different settings.

## Validation: 14 October 2026

All 93 local tests pass. Twenty dedicated history tests cover the exact heading, incomplete/failed/restarted cycles, zero versus missing estimates, UTC timestamps, filename ties, and identical alias output for files/directories with spaces and help. Added cases cover missing estimates in a new cycle, convergence followed by a runtime error, and original source lines in appended runs. Existing scientific tests cover SCF selection agreement and EXX relaxation guards.

An independent line reader (`tools/validate_history.py`) matches all 280 electronic-evidence and overall-verdict checks on 35 unique local PWSCF logs. It checks minimum/last/latest-cycle estimates with source lines and cycle IDs, last convergence outcome, latest cycle ID/state and converged-cycle counts. It requires explicit cycle banners. Overall verdict checks are bounded to the declared SCF/fixed-cell BFGS corpus. It does not independently validate generic classification or file dates. Broader calculation types and hybrid/runtime edge cases remain open. The supplied theta18 table cannot be independently verified without its original output files and metadata. The NERSC v0.7 installation has not been changed or inspected.

## Type and failure limits

History exposes the classification source and warnings when filenames or incomplete SCF evidence determine the type. For an explicit input association, use `qetools inspect FILE.out --input FILE.in`. For a single output, history also accepts `qehis FILE.out --input FILE.in`. Directory input mappings remain planned. Identical energies or estimates do not establish duplicate files or equivalent settings.

Runtime errors take priority over EXX assessment. Explicit stops remain stopped even after electronic convergence or JOB DONE. Types outside SCF/RELAX/VC-RELAX are NOT ASSESSED unless an explicit error or stop is found. BFGS convergence alone cannot certify completion without a converged latest electronic state and normal execution ending.

Five additional tests cover failure priority, stops, unsupported types, incomplete ionic completion and exposed filename uncertainty. Original theta18 rows, real hybrid/magnetic cases and generic type validation remain open.

## Explicit input and JSON reports

```sh
qehis calculation.out --input calculation.in
qehis calculation.out --input calculation.in --json
qehis . --json
```

Input association is declared by the user, not independently proven. Conflicts remain UNKNOWN. Directory-wide `--input` is rejected to prevent one input from being silently attached to unrelated outputs.

JSON schema 1.0 contains `records`, each with the common `report`, `history_evidence`, `history_status`, `modified_utc` and modification-time description. History evidence preserves original source lines, minimum/last/latest-cycle estimates in Ry and convergence outcomes. Unsupported programs have no PWSCF history evidence. The common report retains execution and scientific status separately; `history_status` is the table verdict.

Three added tests check explicit input provenance, conflicting types and rejected directory associations. Alias tests also cover input paths with spaces and JSON output.

## Interface consistency: 15 October 2026

Table and JSON now use the same validated history record. The table no longer interprets SCF-like text from non-PWSCF programs as electronic evidence. Unrecognized explicit files fail in both formats. Three regression tests cover unsupported programs, unrecognized files and agreement for errors/stops/incomplete cycles.

The independent 280-check real-log audit still passes. A separate interface comparison matches 245 displayed values/states against shared records for all 35 unique logs. This latter check establishes presentation consistency, not independent scientific correctness.
