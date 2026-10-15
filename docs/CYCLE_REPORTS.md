# Input and cycle records

The development candidate adds a common JSON report for `qescf`, `qerelax` and
`qetools inspect`. Its schema version is `1.1`.

## Give the intended input explicitly

```sh
qescf scf.out --input scf.in
qerelax relax.out --input relax.in --steps
qerelax relax.out --input relax.in --steps --json
qetools inspect calculation.out --input calculation.in --json
qetools inspect phonon.out --json
```

`--input` requires one exact output file. The association is declared by the user;
it is not inferred from a filename and is not independently proven.
Conflicting input and output evidence produces an unknown classification and a warning.

The input can disambiguate SCF, NSCF, bands, relax, vc-relax, MD and vc-md.
Without it, strong output evidence is used first. Filename fallbacks are marked.
An electronic SCF loop alone cannot prove that a run is a standalone SCF rather than an early ionic calculation.

`inspect` recognizes program headers and reports execution evidence. It does not
certify scientific convergence for NSCF, bands, MD or post-processing programs.
A dynamics banner can also occur in damped relaxation; its MD/relaxation context remains unproven without the intended input.
Those workflows still need their own validated analyzers. `--best` is restricted to SCF/relaxation reports.

## Threshold check

A supplied input supplies `conv_thr`, or its documented QE default of `1e-6 Ry`.
Without an input, the requested threshold is unknown.

The report compares the current cycle's rounded printed accuracy estimate/bound
with the supplied input target. An estimate above that target triggers a review warning.
It does not prove that the true error exceeds the threshold. Check input association,
adaptive relaxation thresholds and EXX settings before interpreting a mismatch.
Printed `new conv_thr` and EXX threshold changes are attached to the relevant next/current cycle.

A converged inner SCF loop does not establish EXX self-consistency. The report
also requires the EXX success marker when EXX output evidence is present.
This has synthetic regression coverage; broader real hybrid cases remain needed.

## What belongs to a cycle

Each electronic cycle can contain:

- Status, iteration records, energy and SCF accuracy estimate.
- The cell and geometry present when the cycle started.
- Per-atom force vectors in Ry/Bohr, component maxima and vector-norm maxima.
- The printed total-force diagnostic, kept separate from those maxima.
- Stress tensors in the printed Ry/Bohr³ and kbar columns, plus printed pressure.
- Evidence line numbers from the original output file.

The current cycle, last ended cycle and last converged cycle are separate records.
`electronic_outcome` records CONVERGED or NOT CONVERGED when QE prints that outcome.
It remains available if a later runtime error changes the cycle's `status` to ERROR.
`ended_electronic_cycles` counts both convergence outcomes; `converged_electronic_cycles`
counts successful electronic outcomes. These counts do not prove normal execution or ionic convergence.

For compatibility, `last_completed_cycle` and `completed_scf_cycles` retain their old
meaning: cycles whose final recorded status is CONVERGED. They are deprecated;
use the explicit fields above. Schema 1.1 records this distinction in `deprecated_fields`.
An unfinished new cycle does not inherit the preceding cycle's energy or forces.
The summary identifies which cycle supplied its last energy, accuracy and force values.

Coordinates printed after a force calculation may propose the next geometry.
They are recorded separately and become the next cycle's starting geometry.
Final-coordinate blocks are also kept separate. Cell changes follow the same rule.

Only the total per-atom force block is read. Verbose force decomposition tables
are not combined with, or substituted for, the total force vectors.

## Completeness and identity

Force blocks require the expected output atom count and unique sequential atom IDs
before they are marked complete. Stress blocks require all three tensor rows.
Partial blocks remain visible but are flagged.

QE output row/atom identities are used within one run. Matching atoms between independent
structures is not implemented. Geometry units are preserved, not converted or subtracted.
Periodic displacement, cell deformation, constraints and optimizer acceptance still need dedicated checks.
Electronic cycles are not automatically equated with accepted ionic steps, especially for EXX.

## Provenance

Reports contain output/input SHA256 hashes, toolkit version, parser-source hashes,
run count and original-file line numbers. When run from a Git checkout, they also
record the commit and whether toolkit source files have local changes.
Installed packages without Git metadata report an unknown commit and retain source hashes.

Full cycle data is included only with `--steps`, or detailed input/inspection reports.
History, comparison and integrity commands still use their older detailed formats.
They have not yet been migrated to the common schema.

## Outcome validation: 15 October 2026

All 96 local tests pass. Added cases cover a failed cycle after a converged cycle,
a runtime error after convergence, and an unfinished cycle with no outcome.
The independent cycle audit now checks the printed electronic outcome in each
of the 236 cycles in the 35 unique local logs, alongside its existing geometry,
force and stress checks. Broader hybrid/MD/optimizer validation remains open.
