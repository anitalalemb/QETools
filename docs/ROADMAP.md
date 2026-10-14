# QETools roadmap

Build a general toolkit with optional material profiles. Validate the existing commands first.
Track tasks in [Notion](https://app.notion.com/p/3f968ec96206815a8f1dd92d2faeb319).
Keep detailed ideas on the [future steps page](https://app.notion.com/p/3f968ec962068100b1dfd9aa934e9949).

## 1. Validate the foundation

Check SCF selection, convergence and values against raw QE output.
Validate relaxation, restart history, input checks and comparisons.
Record source files, hashes, QE version, toolkit commit, expected results and remaining limits.

## 2. Lattice, relaxation and structural change

Support `relax`, `vc-relax`, optimizer types and constraints with their own criteria.
Track lattice vectors, lengths, angles, volume, forces, stress and pressure.

Compare initial/final structures using verified atom matching, correct cells and periodic boundaries.
Export per-atom displacement vectors and magnitudes. Separate cell deformation, rigid motion and internal displacement.
Let users select species, atom IDs, layers and periodic directions.

Add bond changes, coordination, interlayer distances, corrugation, rumpling and local environments.
STO-specific Ti–O off-centering should use the same general structure engine.

## 3. Electronic structure

Add parent-SCF checks for NSCF and bands workflows.
Plot bands and DOS/PDOS with declared energy references and spin/orbital labels.
Report direct/indirect gaps within the sampled k-points. A band path can miss the global extrema.
Check integrated PDOS weights and projection completeness.

## 4. Spin, Hubbard and phonons

Distinguish collinear/noncollinear spin, SOC, total/absolute/local moments and magnetic constraints.
Parse Hubbard syntax, projector definitions, U/J/V and occupation matrices.
Track `hp.x` results and version-specific support.

For phonons, check q-points, response convergence, frequencies, eigenvectors and acoustic sum rules.
Preserve raw and corrected results. Review imaginary modes with numerical convergence evidence.
Add diamond-defect profiles for charge/spin, supercell effects and localized modes.

## 5. τ, moiré and batch workflows

τ generation requires a declared interface, relative translation, movable group and lattice basis.
Generate structures and inputs with a manifest that retains shifts and constraints.
Check missing/failed points, symmetry relations, energy comparability and force/stress maps.

Add twist-angle scans, local stacking, layer analysis and Wannier/DFT comparisons.
Later collect τ-dependent electronic quantities for `H(τ)`.

## 6. Combined reports

Build `qeall` from modules that passed their individual checks.
Use short terminal summaries, detailed evidence and consistent CSV/JSON data.

Names such as `qestruct`, `qeforce`, `qestress`, `qebands`, `qepdos`, `qespin`,
`qehubbard`, `qephonon`, `qetau` and `qescan` are provisional planned commands.
