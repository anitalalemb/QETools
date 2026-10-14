# Command validation

Audit date: 14 October 2026.

The existing commands need evidence before broader scientific use.
This report distinguishes the published baseline from this development candidate.

## Results

| Check | Published baseline | Development candidate |
| --- | --- | --- |
| 19 core behavioral contracts | 11 passed; 8 failed | All 19 passed |
| Full candidate suite | Not applicable | 38 passed |
| Selected values/verdicts on 33 unique real logs | 198 checks matched | 198 checks matched |

The baseline is commit `0a7bed5a8af0406002a4d57c1d423ac0d8a0073d`.
The later public README commit changes documentation only.
The candidate suite includes the original 11 checks, 19 core contracts and 8 interface checks.

The real corpus contains 34 files with 33 unique contents. All use QE 7.5 and STO structures:
16 SCF logs and 17 fixed-cell relaxation logs. One duplicate is counted once.
It includes completed SCFs, SCFs that did not converge, incomplete relaxations and completed BFGS relaxations.

An independent line-based reader checked six fields per unique file:
last total energy, last SCF accuracy estimate, last total force, maximum negative-density diagnostic,
latest electronic state and the completed/converged verdict. Absent values were compared as absent.
No unit conversion or physical interpretation was inferred from these comparisons.

Both parsers matched these real cases. The synthetic edge cases exposed failures that this corpus did not trigger.

## Failures found in the baseline

1. A converged SCF marker could produce a successful verdict without a completed execution.
2. Values from an earlier appended run could remain attached to an unfinished later run.
3. An old runtime error could contaminate a later successful run in the same file.
4. `--best` ranked by a transient minimum rather than the final accuracy estimate.
5. Ending BFGS at its step limit could be interpreted as ionic convergence.
6. Ionic success could be reported without final electronic convergence.
7. Multiple assignments on one input line were not all parsed.
8. The negative-density parser ignored the second spin channel.

The candidate repairs these cases. It also makes history's SCF ranking agree with `qescf --best`,
uses deterministic filename tie-breaking and identifies a non-SCF banner before treating a generic log as SCF.
The last change has a synthetic regression check; full NSCF/bands validation is still open.

## qescf versus qescf --best

Assume a directory contains:

| File | Timestamp | Evidence |
| --- | --- | --- |
| `older.out` | Earlier | Completed SCF; final accuracy estimate `1e-8 Ry` |
| `newer.out` | Later | Stopped SCF; final accuracy estimate `1e-5 Ry` |

`qescf .` selects `newer.out`. It answers “What happened in the newest matching file?”

`qescf --best` selects `older.out`. It answers “Which completed, converged candidate ranks first by its final accuracy estimate?”

The two forms select the same file when the newest file also ranks first.
An explicit filename always selects that file. If none converged, `--best` selects by estimate and reports that no completed converged candidate exists.

This selection is about numerical evidence. It does not establish equal geometries, pseudopotentials,
Hamiltonians, requested thresholds or physical accuracy. Avoid ranking unrelated systems as if they were comparable.

## How to reproduce

Run the automated suite:

```sh
python3 -m unittest discover -s tests -v
```

Check the published core against the same 19 contracts:

```sh
QETOOLS_TEST_BASELINE=1 PYTHONPATH=tests python3 -m unittest test_scientific_contracts.ScientificContracts -v
```

Use the independent reader on local outputs:

```sh
python3 tools/validate_outputs.py /path/to/scf-and-relax-logs \
  --output /path/to/local-audit.json
```

The audit tool records file hashes, evidence line numbers, observed values and mismatches.
Its reference classification is restricted to the known SCF/fixed-cell relaxation corpus named in the script.
It is not a generic ground-truth classifier. Inspect its assumptions before using a different dataset.
Private research logs and their full audit records are not included in this repository.

## Still open

- Validate other QE versions and materials, including semiconductor, magnetic and diamond-defect cases.
- Verify classification against explicitly associated inputs, including NSCF, bands, MD and unusual filenames.
- Compare parsed estimates with the configured thresholds. Handle hybrid/EXX outer-loop convergence.
- Validate scalar/vector spin quantities and real spin-polarized negative-density cases.
- Associate energy, force, stress, cell and coordinates with the same completed ionic step.
- Check per-atom forces, constraints, optimizer-specific criteria and variable-cell pressure convergence.
- Validate input/output provenance, pseudopotential hashes, XC/Hubbard setup and restart compatibility.
- Validate full history, comparison and integrity reports beyond their current smoke/contract coverage.
- Validate timing and exported CSV/JSON schemas, error behavior and platform support.

Passing these checks does not certify a calculation's physical model or convergence with cutoffs, k-points and supercell size.
The toolkit should state exactly which evidence it has checked.

## Scientific references

- [PWSCF inputs and convergence thresholds](https://www.quantum-espresso.org/Doc/INPUT_PW.html)
- [QE ionic-movement source](https://github.com/QEF/q-e/blob/qe-7.5/PW/src/move_ions.f90)
- [QE non-SCF output banner](https://github.com/QEF/q-e/blob/qe-7.5/PW/src/non_scf.f90)
