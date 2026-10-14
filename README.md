# QETools

Short commands. Clear reports. Evidence from Quantum ESPRESSO.

QETools reads calculation files, explains convergence, and compares runs.
It works on workstations and clusters. No NERSC account or Slurm installation is required.

**This branch is a development candidate.** The [validation report](docs/VALIDATION.md)
records what was checked and what still needs validation.

## Install

Python 3.9 or newer is required:

```sh
git clone https://github.com/anitalalemb/QETools.git
cd QETools
git switch improve-portable-reports
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
```

Installed commands work from any directory in the activated environment.
On Windows, activate with `.venv\Scripts\Activate.ps1`.
The toolkit has no third-party runtime dependencies.

## Choose an analysis

| Command | Purpose |
| --- | --- |
| `qescf FILE` | SCF completion, convergence, energy and accuracy estimate |
| `qerelax FILE` | Electronic and ionic/BFGS convergence, energy and total force |
| `qehis DIRECTORY` | Attempts and restarts in chronological order |
| `qecheck INPUT.in` | Input, path, pseudopotential and restart-file checks |
| `qecompare A.out B.out` | Settings and numerical differences between two outputs |

`qehis` is the recommended short command; `qehistory` remains an equivalent alias. The same commands are available through
`qetools scf`, `qetools relax`, `qetools history`, `qetools check` and `qetools compare`.
Use `--help` for options.

## Understand qescf selection

| Form | What happens |
| --- | --- |
| `qescf FILE.out` | Analyze exactly that file |
| `qescf .` | Select the newest matching SCF output by modification time |
| `qescf -l` | Report all matching SCF outputs |
| `qescf --best` | Prefer completed, converged outputs; rank by final SCF accuracy estimate |

Timestamp ties use filename order. `--best` uses modification time, then filename,
to break equal accuracy estimates. If no candidate converged, the report says so.
Default and `--best` may select the same file. A ranking does not prove physical comparability.

## Examples

```sh
qescf .
qescf scf_theta16m7_b0005_smearing_plain_4node.out
qescf DIRECTORY --best
qescf scf.out --verbose
qescf scf.out --json
qescf scf.out --input scf.in
qerelax relax.out
qerelax relax.out --input relax.in --steps --json
qetools inspect calculation.out --input calculation.in --json
qehis .
qecheck .
qecheck . --verbose
qecompare A.out B.out
```

SCF and relaxation reports are concise by default. `--verbose` shows detailed evidence.
`--json` exports the same parsed values, units and selection reason. `--list --json` returns an array.
Directory discovery reads `.out`, `.log` and `.stdout` files without recursion.
Use `--input` to declare an exact input/output association and check the requested SCF target.
Use `--steps` for cycle records with geometry, forces, stress and source lines.
See the [cycle report guide](docs/CYCLE_REPORTS.md) for meanings and limits.

To use the source without installing, run `python3 -m qetools.cli COMMAND ...` from this repository.

## Interpret the result

Execution, electronic convergence and ionic convergence are separate.
`JOB DONE.` alone does not establish convergence. Ending BFGS alone does not prove successful optimization.
Appended output files are analyzed using the latest PWSCF run.

The SCF accuracy is QE's printed estimate. It is not a separate measurement of the true energy error.
A total-force diagnostic is not the optimizer's component-wise force criterion.
A raw energy difference requires a consistent physical setup before interpretation.

The checked scope is SCF and BFGS relaxation in the available STO corpus (QE 7.3.1 and 7.5). Input matching and restart checks are heuristic.
Relative paths assume the input directory was the launch directory. Restart-file presence does not prove usability.
FIRE/damped methods, variable-cell stress/geometry, magnetic/Hubbard cases and phonons need dedicated validation.

## Roadmap and development

See the [roadmap](docs/ROADMAP.md) and the [Notion task tracker](https://app.notion.com/p/3f968ec96206815a8f1dd92d2faeb319).
Bands, PDOS, structural/τ tools and `qeall` are planned. Their older wrappers are placeholders.

```sh
python3 -m unittest discover -s tests -v
```
