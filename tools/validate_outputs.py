"""Compare parsers with an independent line-based reference on supplied logs.

Raw research outputs are read locally and are not copied into this repository.
This checks selected reported values/status, not the physical validity of a study.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qetools import cli


def numeric(token):
    return float(token.replace('D', 'E').replace('d', 'e'))


def reference(raw):
    """Walk literal QE lines independently; save evidence line numbers."""
    state = {}
    for lineno, line in enumerate(raw.splitlines(), 1):
        s = line.strip()
        lower = s.lower()
        if s.startswith('Program PWSCF'):
            state = {'version': s.split()[2].removeprefix('v.'), 'electronic': 'UNKNOWN',
                     'done': False, 'stop': False, 'error': False, 'bfgs': False,
                     'energy': None, 'accuracy': None, 'force': None, 'negative_rho_max': None,
                     'evidence': {}}
        if not state:
            continue
        evidence = state['evidence']
        if s.startswith('iteration #'):
            state['electronic'] = 'INCOMPLETE'; evidence['last_iteration'] = lineno
        if lower.startswith('estimated scf accuracy') and '<' in s:
            state['accuracy'] = numeric(s.split('<', 1)[1].split()[0]); evidence['accuracy'] = lineno
        energy_line = s.lstrip('!').strip()
        if energy_line.startswith('total energy') and '=' in energy_line:
            state['energy'] = numeric(energy_line.split('=', 1)[1].split()[0]); evidence['energy'] = lineno
        if s.startswith('Total force') and '=' in s:
            state['force'] = numeric(s.split('=', 1)[1].split()[0]); evidence['force'] = lineno
        if lower.startswith('negative rho') and ':' in s:
            numbers = [abs(numeric(t)) for t in s.split(':', 1)[1].split()]
            value = max(numbers)
            state['negative_rho_max'] = max(value, state['negative_rho_max'] or 0)
        if lower.startswith('convergence has been achieved'):
            state['electronic'] = 'CONVERGED'; evidence['electronic'] = lineno
        if lower.startswith('convergence not achieved'):
            state['electronic'] = 'NOT CONVERGED'; evidence['electronic'] = lineno
        if 'Error in routine' in s:
            state['error'] = True; evidence['error'] = lineno
        if lower.startswith('bfgs converged'):
            state['bfgs'] = True; evidence['ionic'] = lineno
        if 'Maximum CPU time exceeded' in s or 'Calculation stopped' in s:
            state['stop'] = True; evidence['stop'] = lineno
        if s == 'JOB DONE.':
            state['done'] = True; evidence['job_done'] = lineno
    return state


def load_baseline(ref):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp, 'baseline.py')
        path.write_bytes(subprocess.check_output(['git', 'show', ref+':qetools/cli.py'], cwd=Path(__file__).resolve().parents[1]))
        spec = importlib.util.spec_from_file_location('baseline_cli', path)
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        return module


def expected_success(ref, kind):
    healthy = ref['done'] and not ref['error'] and not ref['stop'] and ref['electronic'] == 'CONVERGED'
    return healthy and (ref['bfgs'] if kind in ('RELAX', 'VC-RELAX') else True)


def audit(path, modules):
    raw = path.read_text(errors='replace')
    expected = reference(raw)
    if not expected:
        return None
    # This corpus is explicitly SCF or fixed-cell BFGS relaxation.
    # Do not claim independent generic classification from filenames.
    kind = 'RELAX' if 'relax' in path.name else 'SCF'
    record = {'file': str(path.resolve()), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
              'known_corpus_type': kind, 'reference': expected, 'parsers': {}}
    for name, module in modules.items():
        text = module.read(path); values = module.output_core(text)
        observed = {'energy': values['energies'][-1] if values['energies'] else None,
                    'accuracy': values['acc'][-1] if values['acc'] else None,
                    'force': values['forces'][-1] if values['forces'] else None,
                    'negative_rho_max': max(values['neg']) if values['neg'] else None,
                    'electronic': module.latest_electronic_status(text),
                    'status': module.summary_status(text, kind)}
        expected_electronic = 'ERROR' if expected['error'] else expected['electronic']
        failures = []
        for key in ('energy', 'accuracy', 'force', 'negative_rho_max'):
            if observed[key] != expected[key]:
                failures.append(key)
        if observed['electronic'] != expected_electronic:
            failures.append('electronic')
        if (observed['status'] in ('CONVERGED', 'RELAX CONV')) != expected_success(expected, kind):
            failures.append('success_verdict')
        record['parsers'][name] = {'observed': observed, 'failed_checks': failures}
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('paths', nargs='+', type=Path)
    parser.add_argument('--baseline', default='0a7bed5a8af0406002a4d57c1d423ac0d8a0073d')
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    files = set()
    for path in args.paths:
        files.update(path.rglob('*.out') if path.is_dir() else [path])
    modules = {'baseline': load_baseline(args.baseline), 'candidate': cli}
    rows = [r for p in sorted(files) if (r := audit(p, modules))]
    unique = {r['sha256']: r for r in rows}
    summary = {'files': len(rows), 'unique_content': len(unique),
               'versions': dict(Counter(r['reference']['version'] for r in unique.values())),
               'corpus_types': dict(Counter(r['known_corpus_type'] for r in unique.values())),
               'baseline_ref': args.baseline,
               'candidate_cli_sha256': hashlib.sha256(Path(cli.__file__).read_bytes()).hexdigest(),
               'failed_checks': {name: dict(Counter(check for r in unique.values() for check in r['parsers'][name]['failed_checks'])) for name in modules},
               'files_with_failed_checks': {name: sum(bool(r['parsers'][name]['failed_checks']) for r in unique.values()) for name in modules}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({'summary': summary, 'records': rows}, indent=2))
    print(json.dumps(summary, indent=2))

if __name__ == '__main__':
    main()
