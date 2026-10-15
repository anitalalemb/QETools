"""History evidence with original source lines and explicit cycle association."""
import re
from .records import FLOAT, latest_run, number, parse_cycles


def evidence(raw):
    scoped, offset, _ = latest_run(raw)
    cycles = parse_cycles(raw)['cycles']
    samples = []
    for i, line in enumerate(scoped.splitlines(), offset + 1):
        match = re.search(r'estimated scf accuracy\s*<\s*(' + FLOAT + r')\s*Ry', line, re.I)
        if match:
            cycle = next((c for c in cycles if c['start_line'] <= i <= c['end_line']), None)
            samples.append({'value_ry': number(match.group(1)), 'source_line': i,
                            'cycle_id': cycle['cycle_id'] if cycle else None})
    # Outcome evidence survives a subsequent runtime error in the same cycle.
    ended = []
    lines = raw.splitlines()
    for cycle in cycles:
        line = cycle['evidence'].get('convergence')
        if line:
            ended.append({'cycle_id': cycle['cycle_id'], 'source_line': line,
                          'status': 'NOT CONVERGED' if 'convergence NOT achieved' in lines[line-1] else 'CONVERGED'})
    latest = cycles[-1] if cycles else None
    return {'minimum_estimate': min(samples, key=lambda s: s['value_ry']) if samples else None,
            'last_estimate': samples[-1] if samples else None,
            'latest_cycle_estimate': next((s for s in reversed(samples) if latest and s['cycle_id'] == latest['cycle_id']), None),
            'last_ended_cycle': ended[-1] if ended else None,
            'latest_cycle_id': latest['cycle_id'] if latest else None,
            'latest_cycle_status': latest['status'] if latest else 'UNKNOWN',
            'converged_cycles': sum(c['status'] == 'CONVERGED' for c in ended)}


def overall_status(text, calculation):
    """History verdicts retain explicit failures before applying scope limits."""
    from . import cli
    flags = cli.termination_flags(text)
    if flags['error']:
        return 'ERROR'
    if flags['wall_stop'] or flags['stopped']:
        return 'RELAX STOP' if calculation in ('RELAX', 'VC-RELAX') else 'STOPPED'
    if calculation not in ('SCF', 'RELAX', 'VC-RELAX'):
        return 'NOT ASSESSED'
    combined = cli.summary_status(text, calculation)
    if calculation == 'SCF' or combined.startswith('EXX') or combined == 'RELAX CONV':
        return combined
    if flags['jobdone']:
        return 'RELAX INCOMP'
    return 'UNTERMINATED' if cli.output_core(text)['iters'] else 'UNKNOWN'


def build_record(path, input_path=None):
    """One validated record shared by the table and JSON interfaces."""
    from datetime import datetime, timezone
    from pathlib import Path
    from .reporting import analyze
    path = Path(path)
    report = analyze(path, input_path=input_path)
    raw = path.read_text(errors='replace')
    return {'report': report,
            'history_evidence': evidence(raw) if report['program'] in (None, 'PWSCF') else None,
            'history_status': overall_status(raw, report['calculation']),
            'modified_utc': datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
            'modified_time_source': 'filesystem modification time; not calculation time'}
