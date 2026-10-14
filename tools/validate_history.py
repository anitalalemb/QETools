"""Independent history evidence audit for PWSCF files with explicit cycle banners."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from qetools.history import evidence, overall_status
from validate_outputs import reference


def audit(path):
    data = path.read_bytes(); lines = data.decode(errors='replace').splitlines()
    headers = [i for i, s in enumerate(lines) if s.strip().startswith('Program PWSCF')]
    if not headers:
        return None
    start = headers[-1]
    samples = []; ended = []; cycle = 0; status = 'UNKNOWN'
    for i in range(start, len(lines)):
        s = lines[i].strip()
        if s.lower() == 'self-consistent calculation':
            cycle += 1; status = 'INCOMPLETE'
        if s.startswith('estimated scf accuracy'):
            samples.append({'value_ry': float(s.split('<')[1].split()[0].replace('D','E').replace('d','e')), 'source_line': i+1, 'cycle_id': cycle or None})
        if s.startswith('convergence has been achieved') or s.startswith('convergence NOT achieved'):
            status = 'NOT CONVERGED' if 'NOT achieved' in s else 'CONVERGED'
            ended.append({'cycle_id': cycle, 'source_line': i+1, 'status': status})
        if 'Error in routine' in s:
            status = 'ERROR'
    if not cycle:
        return None
    expected = {'minimum_estimate': min(samples,key=lambda x:x['value_ry']) if samples else None,
                'last_estimate': samples[-1] if samples else None,
                'latest_cycle_estimate': next((s for s in reversed(samples) if s['cycle_id'] == cycle), None),
                'last_ended_cycle': ended[-1] if ended else None,
                'latest_cycle_id': cycle, 'latest_cycle_status': status,
                'converged_cycles': sum(c['status']=='CONVERGED' for c in ended)}
    observed = evidence(data.decode(errors='replace'))
    # Declared scope of this local corpus: SCF or fixed-cell BFGS relaxation.
    kind = 'RELAX' if 'relax' in path.name else 'SCF'
    state = reference(data.decode(errors='replace'))
    if state['error']:
        verdict = 'ERROR'
    elif state['stop']:
        verdict = 'RELAX STOP' if kind == 'RELAX' else 'STOPPED'
    elif kind == 'RELAX':
        verdict = ('RELAX CONV' if state['bfgs'] and state['done'] and state['electronic']=='CONVERGED'
                   else 'RELAX INCOMP' if state['done'] else 'UNTERMINATED')
    else:
        verdict = ('CONVERGED' if state['done'] and state['electronic']=='CONVERGED'
                   else 'NOT CONV' if state['electronic']=='NOT CONVERGED'
                   else 'INCOMPLETE' if state['electronic']=='INCOMPLETE' else 'UNKNOWN')
    expected['overall'] = verdict
    observed['overall'] = overall_status(data.decode(errors='replace'), kind)
    return {'file':str(path.resolve()), 'sha256':hashlib.sha256(data).hexdigest(), 'checks':len(expected),
            'failures':[{'field':k,'expected':v,'observed':observed[k]} for k,v in expected.items() if v != observed[k]]}

if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('directories',nargs='+'); args=parser.parse_args()
    rows=[]; seen=set()
    for directory in args.directories:
        for path in sorted(Path(directory).rglob('*.out')):
            row=audit(path)
            if row and row['sha256'] not in seen:
                seen.add(row['sha256']); rows.append(row)
    result={'scope':'History electronic evidence only; explicit PWSCF cycle banners required. Overall verdicts checked within declared SCF/fixed-cell BFGS corpus. Not an independent generic type/date audit.',
            'unique_files':len(rows),'checks':sum(r['checks'] for r in rows),'failures':sum(len(r['failures']) for r in rows),'results':rows}
    print(json.dumps(result,indent=2)); sys.exit(bool(result['failures']))
