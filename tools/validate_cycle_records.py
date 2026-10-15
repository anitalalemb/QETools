"""Independent line/block audit for the known local SCF/BFGS corpus.

Checks cycle segmentation and source association, not converted geometries,
optimizer constraints or physical stress/force convergence.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qetools.records import parse_cycles


def num(value):
    return float(value.replace('D', 'E').replace('d', 'e'))


def expected(raw):
    lines = raw.splitlines()
    headers = [i for i, line in enumerate(lines) if line.strip().startswith('Program PWSCF')]
    if not headers:
        return None
    start = headers[-1]
    banners = [i for i in range(start, len(lines)) if lines[i].strip().lower() == 'self-consistent calculation']
    rows = []
    for n, first in enumerate(banners):
        end = banners[n + 1] if n + 1 < len(banners) else len(lines)
        before = [(i+1, line.strip()) for i,line in enumerate(lines[start:first], start)]
        geometries = [i for i,line in before if line.startswith('ATOMIC_POSITIONS') or 'positions (alat units)' in line and 'site n.' in line]
        cells = [i for i,line in before if line.startswith('CELL_PARAMETERS') or 'crystal axes:' in line]
        row = {'geometry_source_line': geometries[-1] if geometries else None,
               'cell_source_line': cells[-1] if cells else None,
               'electronic_outcome': None, 'energy': None, 'accuracy': None, 'forces': [], 'stress_kbar': None,
               'force_source_line': None, 'stress_source_line': None}
        capturing_force = False
        for i in range(first, end):
            s = lines[i].strip()
            if s.startswith('convergence has been achieved'):
                row['electronic_outcome'] = 'CONVERGED'
            if s.startswith('convergence NOT achieved'):
                row['electronic_outcome'] = 'NOT CONVERGED'
            if s.lstrip('!').strip().startswith('total energy'):
                row['energy'] = num(s.split('=',1)[1].split()[0])
            if s.startswith('estimated scf accuracy'):
                row['accuracy'] = num(s.split('<',1)[1].split()[0])
            if s.startswith('Forces acting on atoms'):
                row['force_source_line'] = i+1
                capturing_force = True
                continue
            if capturing_force and s.startswith('atom ') and ' force =' in s:
                left, right = s.split('force =',1)
                row['forces'].append({'atom_id': int(left.split()[1]), 'vector': [num(v) for v in right.split()[:3]]})
            elif s:
                capturing_force = False
            if s.startswith('total') and 'stress' in s and 'Ry/bohr**3' in s:
                row['stress_source_line'] = i+1
                row['stress_kbar'] = [[num(t) for t in line.split()[3:6]] for line in lines[i+1:i+4]]
        rows.append(row)
    return rows


def audit(path):
    data = path.read_bytes(); raw = data.decode(errors='replace')
    refs = expected(raw)
    if refs is None:
        return None
    parsed = parse_cycles(raw)
    failures = []
    if len(refs) != len(parsed['cycles']):
        failures.append({'field': 'cycle_count', 'expected': len(refs), 'observed': len(parsed['cycles'])})
    checks, force_blocks, stress_blocks = 1, 0, 0
    for i, (ref, cycle) in enumerate(zip(refs, parsed['cycles']), 1):
        observed = {
            'electronic_outcome': cycle['electronic_outcome'],
            'geometry_source_line': (cycle['geometry_at_start'] or {}).get('source_line'),
            'cell_source_line': (cycle['cell_at_start'] or {}).get('source_line'),
            'energy': cycle['last_energy_ry'], 'accuracy': cycle['last_scf_accuracy_ry'],
            'forces': [{'atom_id': a['atom_id'], 'vector': a['vector_ry_bohr']} for a in (cycle['forces'] or {}).get('atoms', [])],
            'stress_kbar': (cycle['stress'] or {}).get('tensor_kbar'),
            'force_source_line': (cycle['forces'] or {}).get('source_line'),
            'stress_source_line': (cycle['stress'] or {}).get('source_line')}
        for key in observed:
            checks += 1
            if ref[key] != observed[key]:
                failures.append({'cycle': i, 'field': key, 'expected': ref[key], 'observed': observed[key]})
        if ref['forces']:
            force_blocks += 1
            maxima = max(abs(v) for a in ref['forces'] for v in a['vector'])
            norms = max(math.sqrt(sum(v*v for v in a['vector'])) for a in ref['forces'])
            for key, expected_value in [('max_abs_component_ry_bohr', maxima), ('max_norm_ry_bohr', norms)]:
                checks += 1
                if cycle['forces'][key] != expected_value:
                    failures.append({'cycle':i, 'field':key})
        if ref['stress_kbar'] is not None:
            stress_blocks += 1
    version = raw[raw.find('Program PWSCF'):].splitlines()[0].split()[2].removeprefix('v.')
    return {'file':str(path.resolve()), 'sha256':hashlib.sha256(data).hexdigest(), 'qe_version':version,
            'cycles':len(refs), 'force_blocks':force_blocks, 'stress_blocks':stress_blocks,
            'checks':checks, 'failures':failures}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('paths', nargs='+', type=Path)
    p.add_argument('--output', required=True, type=Path)
    args=p.parse_args()
    files=set()
    for path in args.paths:
        files.update(path.rglob('*.out') if path.is_dir() else [path])
    rows=[r for path in sorted(files) if (r:=audit(path))]
    unique={r['sha256']:r for r in rows}
    summary={'files':len(rows), 'unique_content':len(unique),
             'versions':dict(Counter(r['qe_version'] for r in unique.values())),
             'cycles':sum(r['cycles'] for r in unique.values()),
             'force_blocks':sum(r['force_blocks'] for r in unique.values()),
             'stress_blocks':sum(r['stress_blocks'] for r in unique.values()),
             'checks':sum(r['checks'] for r in unique.values()),
             'failed_checks':sum(len(r['failures']) for r in unique.values())}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps({'summary':summary,'records':rows},indent=2))
    print(json.dumps(summary,indent=2))
    for row in unique.values():
        if row['failures']:
            print(Path(row['file']).name, [{'cycle':f.get('cycle'),'field':f['field']} for f in row['failures'][:2]])
    if summary['failed_checks']:
        raise SystemExit(1)

if __name__=='__main__':
    main()
