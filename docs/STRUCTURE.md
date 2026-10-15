# Structural displacements: first implementation

Compare the first electronic cycle's starting geometry with the explicit final
coordinate block in the latest PWSCF run:

```sh
qetools structure relax.out
qetools structure relax.out --periodic ab
qetools structure relax.out --json
```

Compare selected snapshots from two outputs:

```sh
qetools structure reference.out candidate.out --from final --to final
```

Each row gives atom ID, species, Δx, Δy, Δz and displacement magnitude.
Maximum and RMS displacement follow. Coordinates and cell vectors are converted
to Cartesian coordinates in a consistent native unit: Bohr for alat/Bohr cells,
Å for angstrom cells. JSON includes units, original source lines and file hashes.

## Required assumptions

Atoms must have the same count, index order and species. Users must confirm
physical identity; swapping atoms of the same species cannot be detected.
No translation, rotation or other alignment is removed.

The cell must be unchanged within an absolute component tolerance of 1e-9 in
its reported unit. Mixed Cartesian units are rejected. Missing or partial
geometries, missing/singular cells and unrecognized latest programs are rejected.
The final snapshot requires an explicit final-coordinate block; an intermediate
proposal is never silently substituted. A final block does not prove convergence.

Periodic wrapping is off by default. `--periodic ab` wraps along lattice vectors
a and b, preserving the third direction. This first version requires mutually
orthogonal lattice vectors for wrapping. Half-cell ties use [-1/2, 1/2).
Skew cells remain supported for direct unwrapped displacement. Changed-cell strain,
triclinic minimum images, atom remapping, alignment, layer grouping and constraint
analysis remain planned.

## Validation: 15 October 2026

All 108 local tests pass. Nine analytical structure tests cover Cartesian/fractional
motion, explicit boundary wrapping, zero displacement, alat scaling, CLI JSON and
rejection of skew periodic cells, changed cells, species mismatches and missing
final coordinates. A local eligibility check over 35 unique real logs finds one
supported explicit-final comparison and rejects 34 cases under the declared rules.
This is a smoke check, not independent real-structure displacement validation.
Broader reference structures are still required before marking structural analysis complete.
