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

By default, the cell must be unchanged within an absolute component tolerance of
1e-9 in its reported unit. Use `--separate-cell` for explicit changed-cell decomposition. Mixed Cartesian units are rejected. Missing or partial
geometries, missing/singular cells and unrecognized latest programs are rejected.
The final snapshot requires an explicit final-coordinate block; an intermediate
proposal is never silently substituted. A final block does not prove convergence.

Periodic wrapping is off by default. `--periodic ab` wraps along lattice vectors
a and b, preserving the third direction. This first version requires mutually
orthogonal lattice vectors for wrapping. Half-cell ties use [-1/2, 1/2).
Skew cells remain supported for direct unwrapped displacement. Elastic strain tensors, triclinic minimum images, atom remapping, alignment,
layer grouping and constraint analysis remain planned.

## Validation: 15 October 2026

All 108 local tests pass. Nine analytical structure tests cover Cartesian/fractional
motion, explicit boundary wrapping, zero displacement, alat scaling, CLI JSON and
rejection of skew periodic cells, changed cells, species mismatches and missing
final coordinates. A local eligibility check over 35 unique real logs finds one
supported explicit-final comparison and rejects 34 cases under the declared rules.
This is a smoke check, not independent real-structure displacement validation.
Broader reference structures are still required before marking structural analysis complete.

## Changed-cell decomposition

```sh
qetools structure vc-relax.out --separate-cell
qetools structure reference.out candidate.out --from final --to final --separate-cell --json
```

For reference cell A, candidate cell B and reference fractional position f,
the mapped reference position is f B. The command reports:

- Total motion: candidate Cartesian position minus reference Cartesian position.
- Affine cell motion: f B minus f A.
- Internal motion: candidate Cartesian position minus f B.

These vectors add exactly apart from floating-point rounding. JSON schema 1.1
includes per-atom affine/internal vectors and internal norms, maximum/RMS internal
displacement, cell volumes and volume ratio. Volume units are the cube of the
coordinate unit. This is a geometric decomposition, not an elastic strain or
rigid-body alignment analysis. Both cells must use consistent units and handedness.

Coordinates must be unwrapped consistently. `--periodic` and `--separate-cell`
cannot be combined in this version. Skew cells are supported for this unwrapped
decomposition; ill-conditioned cells and complex remappings need further validation.

All 112 tests pass. Four added analytical tests cover pure expansion, expansion
plus internal motion, skew reference cells and rejection of periodic decomposition.
Independent real variable-cell reference validation remains open.
