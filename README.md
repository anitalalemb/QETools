# QETools

Short commands. Clear reports. Evidence from Quantum ESPRESSO.

QETools helps you inspect calculation outputs, understand convergence, and compare runs.
The goal is a reusable toolkit for workstations and clusters, with optional tools for moiré materials and defects.

**Status:** early development. Scientific validation is in progress.
The code on `main` and the [draft upgrade](https://github.com/anitalalemb/QETools/pull/1) are different. Planned features are listed separately below.

The first audit found 8 failures in 19 targeted checks of the published code.
The proposed fixes pass 38 automated tests. See the [validation report](https://github.com/anitalalemb/QETools/blob/improve-portable-reports/docs/VALIDATION.md) for evidence and remaining limits.

## Start here

Python 3 is required. From the repository:

```sh
git clone https://github.com/anitalalemb/QETools.git
cd QETools

python3 -m qetools.cli scf scf.out
python3 -m qetools.cli relax relax.out
python3 -m qetools.cli history .
python3 -m qetools.cli check .
python3 -m qetools.cli compare A.out B.out
```

These commands read existing files. They do not submit calculations.
The current `main` has an empty `pyproject.toml`. Installation through pip is being repaired in the draft upgrade.
For now, use the module commands above from the repository. They preserve all arguments.

## Available analysis

| Command | What it reports |
| --- | --- |
| `qescf` | Electronic convergence, residuals, energies, warnings and timing |
| `qerelax` | Electronic cycles, BFGS relaxation evidence, energies and forces |
| `qehistory` / `qehis` | Attempts and restarts in chronological order |
| `qecheck` | Input, path, pseudopotential, restart-file and launch-script checks |
| `qecompare` | Settings, status and numerical differences between two outputs |

The included `bin/` shortcuts are optional. Some older shortcuts forward only the first argument.
Use `python3 -m qetools.cli COMMAND ...` when supplying a directory or several options.

## Which SCF file is selected?

| Form | Selection |
| --- | --- |
| `qescf FILE.out` | Exactly that file |
| `qescf .` | Newest detected SCF output, by file modification time |
| `qescf -l` | List detected SCF outputs |
| `qescf --best` | Prefer a converged candidate, then rank by residual |

The default and `--best` can select the same file.
“Newest” means the file timestamp. It does not prove that the file is the final calculation.

**Current main:** `--best` uses the smallest residual anywhere in each run.
**Draft upgrade:** it uses the final residual and requires completion evidence.
Neither ranking proves that different structures or numerical settings are physically comparable.
If no candidate converged, a selected file remains unconverged.

Use the full module forms to keep every argument:

```sh
python3 -m qetools.cli scf --list DIRECTORY
python3 -m qetools.cli scf --best DIRECTORY
```

## Examples

The original command examples remain useful:

```sh
qescf .
qescf scf_theta16m7_b0005_smearing_plain_4node.out
qerelax relax.out
qehistory .
qecheck .
qecompare A.out B.out
```

## What the result means

Execution, electronic convergence and ionic convergence are separate checks.
`JOB DONE.` alone does not establish convergence.
A converged electronic cycle inside a relaxation does not prove that the structure finished relaxing.

A raw energy difference is a numerical comparison.
Its physical meaning also depends on the structures, pseudopotentials, Hamiltonian and numerical setup.
Missing evidence should be reported as unknown.

The current checker contains Slurm and module assumptions. General launch support is being developed.
Restart-file presence does not prove that a restart is usable.
Calculation classification and input/output matching still need broader validation.

## Next work

1. Validate the existing commands against raw QE outputs and independent expected results.
2. Repair installation and option forwarding. Make reports concise and portable.
3. Validate forces, stress, `relax` and `vc-relax`, including different optimizers and constraints.
4. Automate atom displacements, cell changes, bond changes and layer analysis.
5. Add NSCF, bands and DOS/PDOS analysis.
6. Add spin, magnetization, Hubbard and phonon analysis.
7. Add general bilayer τ generation, batch scans and moiré-specific analysis.
8. Combine validated modules into `qeall`.

These are planned capabilities, not released commands.
Older wrappers such as `qexy`, `qez`, `qepdos` and `qeall` are placeholders.

## Project plan

[Tasks, ideas and examples in Notion](https://app.notion.com/p/3c368ec9620681d9b3bae2dce4ce9ae3)

Development follows the [QE input reference](https://www.quantum-espresso.org/Doc/INPUT_PW.html).
Validation must record the toolkit commit, QE version, input files, output evidence and expected values.
