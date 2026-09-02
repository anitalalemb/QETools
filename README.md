
# qetools v0.2

Research-oriented Quantum ESPRESSO analysis toolkit for short, memorable NERSC commands.

## Current commands

- `qescf` —  SCF analyzer
- `qerelax` — basic relaxation analyzer; research-grade upgrade next
- `qehistory` — summarize repeated/restarted outputs
- `qecheck` — basic directory/input integrity
- `qecompare` — compare two QE outputs through `python -m qetools.cli compare A B`

## Scientific principle

`JOB DONE.` is **not** treated as equivalent to SCF convergence.

`qescf` separately reports:
1. execution integrity,
2. electronic convergence,
3. SCF residual/energy behaviour,
4. negative-rho and eigenvector warnings,
5. timing,
6. a final research-use decision.

## Install on NERSC

After uploading/unzipping:

```bash
cd qetools
python -m pip install --user -e .
```

For the very short commands, add the included `bin` directory to PATH:

```bash
echo 'export PATH="$HOME/qetools/bin:$PATH"' >> ~/.bashrc
source ~/.bashrc
```

If you install the repo somewhere else, replace `$HOME/qetools` with the real path.

Then:

```bash
qescf .
qescf scf_theta16m7_b0005_smearing_plain_4node.out
qerelax relax.out
qehistory .
qecheck .
```

## Planned roadmap

v0.3
- research-grade `qerelax`
- input ↔ output ↔ save-dir ↔ Slurm consistency checks

v0.4
- validated cell/PBC-aware `qexy`, `qez`, `qeti`, `qestruct`

later
- `qepdos`, `qebands`
- `qetau`, `qescan`
- `qeall`
