"""Common report schema for PWSCF and conservative program inspection."""
import hashlib
import math
from functools import lru_cache
import subprocess
from pathlib import Path
import re

from . import __version__, cli
from .calculations import identify
from .inputs import parse_namelists
from .records import latest_run, number, parse_cycles


@lru_cache(maxsize=1)
def source_revision():
    root = Path(__file__).resolve().parents[1]
    if not (root / '.git').exists():
        return {'commit': None, 'source_dirty': None}
    try:
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True, timeout=1).strip()
        dirty = bool(subprocess.check_output(['git', 'status', '--porcelain', '--', 'qetools'], cwd=root, text=True, timeout=1).strip())
        return {'commit': commit, 'source_dirty': dirty}
    except (OSError, subprocess.SubprocessError):
        return {'commit': None, 'source_dirty': None}


def threshold_check(cycles, namelists):
    requested, source = None, 'no supplied input'
    if namelists is not None:
        value = namelists.get('electrons', {}).get('conv_thr', '1.0d-6')
        try:
            requested = number(value)
            if not math.isfinite(requested) or requested <= 0:
                raise ValueError
            source = 'supplied input value' if 'conv_thr' in namelists.get('electrons', {}) else 'QE default for supplied input'
        except ValueError:
            requested = None
            source = 'unparseable or nonpositive input threshold'
    cycle = cycles[-1] if cycles else None
    estimate = cycle['last_scf_accuracy_ry'] if cycle else None
    active = cycle['active_conv_thr_ry'] if cycle else None
    comparison = 'UNKNOWN'
    if requested is not None and estimate is not None:
        comparison = 'AT OR BELOW INPUT TARGET' if estimate <= requested else 'ABOVE INPUT TARGET (review)'
    return {'requested_conv_thr_ry': requested, 'source': source,
            'last_cycle_conv_thr_ry': active, 'last_cycle_accuracy_estimate_ry': estimate,
            'comparison': comparison, 'cycle_id': cycle['cycle_id'] if cycle else None,
            'interpretation': 'Comparison of the rounded QE printed estimate/bound; not an independent true-error measurement.'}


def cycle_summary(cycle):
    if cycle is None:
        return None
    forces, stress = cycle['forces'], cycle['stress']
    return {key: cycle.get(key) for key in ('cycle_id', 'status', 'last_energy_ry', 'last_scf_accuracy_ry', 'active_conv_thr_ry', 'evidence')} | {
        'iteration_records': len(cycle['iterations']),
        'geometry_source_line': (cycle['geometry_at_start'] or {}).get('source_line'),
        'force_block_complete': forces['complete'] if forces else None,
        'max_force_component_ry_bohr': forces['max_abs_component_ry_bohr'] if forces else None,
        'max_force_norm_ry_bohr': forces['max_norm_ry_bohr'] if forces else None,
        'stress_block_complete': stress['complete'] if stress else None,
        'pressure_kbar': stress['pressure_kbar'] if stress else None}


def analyze(path, input_path=None, include_cycles=False):
    path = Path(path)
    raw_bytes = path.read_bytes(); raw = raw_bytes.decode(errors='replace')
    text, _, run_count = latest_run(raw)
    if not re.search(r'(?im)^\s*Program\s+\S+', text) and not cli.is_qe_output(path):
        raise ValueError(f'No recognizable Quantum ESPRESSO output in {path}')
    namelists, settings, input_provenance = None, None, None
    if input_path is not None:
        input_file = Path(input_path)
        data = input_file.read_bytes()
        namelists = parse_namelists(data.decode(errors='replace'))
        if 'control' not in namelists:
            raise ValueError('Supplied PWSCF input has no &CONTROL namelist.')
        settings = namelists['control']
        input_provenance = {'file': str(input_file.resolve()), 'sha256': hashlib.sha256(data).hexdigest(),
                            'association': 'explicitly supplied by user; not independently proven'}
    identity = identify(path, text, settings)
    kind, program = identity['calculation'], identity['program']
    flags = cli.termination_flags(text)
    execution = ('ERROR' if flags['error'] else 'STOPPED' if flags['wall_stop'] or flags['stopped']
                 else 'COMPLETED' if flags['jobdone'] else 'UNTERMINATED')
    warnings = list(identity['warnings'])
    first_threshold = threshold_check([], namelists)['requested_conv_thr_ry']
    records = parse_cycles(raw, first_threshold) if program in (None, 'PWSCF') else {'cycles': [], 'run_count': run_count}
    cycles = records['cycles']
    core = cli.output_core(text) if program in (None, 'PWSCF') else {'energies': [], 'acc': [], 'forces': [], 'neg': []}
    electronic = cli.latest_electronic_status(text) if program in (None, 'PWSCF') else 'NOT ASSESSED'
    ionic = cli.ionic_status(text, kind)
    status = cli.summary_status(text, kind) if program in (None, 'PWSCF') else 'NOT ASSESSED'
    if kind in ('NSCF', 'BANDS', 'NSCF/BANDS'):
        electronic = 'N/A (non-self-consistent)'; status = 'SCIENTIFIC STATUS NOT ASSESSED'
    if kind in ('MD', 'VC-MD'):
        status = 'TRAJECTORY STATUS NOT ASSESSED'; ionic = 'N/A'
    if any(value > 0 for value in core['neg']):
        warnings.append('Negative charge density reported; inspect magnitude and persistence.')
    if re.search(r'eigenvalues? not converged', text, re.I):
        warnings.append('Eigenvalue convergence warnings reported; inspect the final cycle.')
    if re.search(r'charge is wrong', text, re.I):
        warnings.append("QE reports 'charge is wrong'; inspect charge consistency before using the result.")
    if ionic == 'CONVERGED' and status != 'RELAX CONV':
        warnings.append('Ionic marker exists, but a completed final electronic cycle is unproven.')
    thresholds = threshold_check(cycles, namelists)
    if thresholds['comparison'].startswith('ABOVE'):
        warnings.append("The current cycle's printed SCF estimate exceeds the supplied input target. Check input association and adaptive/EXX thresholds.")
    if namelists:
        declared_nat = namelists.get('system', {}).get('nat')
        if declared_nat and records.get('expected_atoms'):
            try:
                if int(declared_nat) != records['expected_atoms']:
                    warnings.append('Input/output atom counts differ; the supplied input association is inconsistent.')
            except ValueError:
                warnings.append('Input nat is not a plain integer; atom-count consistency was not assessed.')
    for cycle in cycles:
        if cycle['forces'] and not cycle['forces']['complete']:
            warnings.append(f"Cycle {cycle['cycle_id']}: per-atom force block is partial or its expected count is unknown.")
        if cycle['stress'] and not cycle['stress']['complete']:
            warnings.append(f"Cycle {cycle['cycle_id']}: stress tensor is partial.")
    if execution == 'ERROR':
        action = 'Inspect the QE error and correct its cause before rerunning.'
    elif execution == 'STOPPED':
        action = 'Check restart files and the stopping reason before continuing.'
    elif status in ('CONVERGED', 'RELAX CONV'):
        action = 'Numerical convergence markers are established; review the threshold check and physical settings.'
    elif program not in (None, 'PWSCF') or kind in ('NSCF', 'BANDS', 'NSCF/BANDS', 'MD', 'VC-MD'):
        action = 'Execution evidence is reported; this workflow needs its own scientific convergence checks.'
    else:
        action = 'Inspect the final output section; final convergence is not established.'
    completed = [cycle for cycle in cycles if cycle['status'] == 'CONVERGED']
    last_energy = next((cycle for cycle in reversed(cycles) if cycle['last_energy_ry'] is not None), None)
    last_accuracy = next((cycle for cycle in reversed(cycles) if cycle['last_scf_accuracy_ry'] is not None), None)
    last_total_force = next((cycle for cycle in reversed(cycles) if 'total_force_diagnostic_ry_bohr' in cycle), None)
    last_force = next((cycle for cycle in reversed(cycles) if cycle['forces']), None)
    last_stress = next((cycle for cycle in reversed(cycles) if cycle['stress']), None)
    report = {
        'schema_version': '1.0', 'file': str(path.resolve()), 'calculation': kind,
        'classification_source': identity['source'], 'program': program,
        'status': status, 'execution': execution, 'electronic': electronic, 'ionic': ionic,
        'last_energy_ry': cli.last_or(core['energies']), 'last_scf_accuracy_ry': cli.last_or(core['acc']),
        'last_total_force_ry_bohr': cli.last_or(core['forces']), 'completed_scf_cycles': len(completed),
        'threshold_check': thresholds if kind not in ('NSCF', 'BANDS', 'NSCF/BANDS') else {'comparison': 'N/A (non-self-consistent)', 'requested_conv_thr_ry': None},
        'current_cycle': cycle_summary(cycles[-1] if cycles else None),
        'last_completed_cycle': cycle_summary(completed[-1] if completed else None),
        'last_energy_cycle_id': last_energy['cycle_id'] if last_energy else None,
        'last_accuracy_cycle_id': last_accuracy['cycle_id'] if last_accuracy else None,
        'last_total_force_cycle_id': last_total_force['cycle_id'] if last_total_force else None,
        'exchange_convergence': cli.exchange_status(text) if program in (None, 'PWSCF') else 'NOT ASSESSED',
        'last_force_cycle_id': last_force['cycle_id'] if last_force else None,
        'last_stress_cycle_id': last_stress['cycle_id'] if last_stress else None,
        'warnings': warnings, 'next_step': action,
        'provenance': {'output_sha256': hashlib.sha256(raw_bytes).hexdigest(), 'input': input_provenance,
                       'toolkit_version': __version__, 'report_parser_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       'cycle_parser_sha256': hashlib.sha256(Path(__file__).with_name('records.py').read_bytes()).hexdigest(),
                       'source_sha256': {name: hashlib.sha256(Path(__file__).with_name(name+'.py').read_bytes()).hexdigest()
                                         for name in ('cli', 'records', 'reporting', 'inputs', 'calculations')},
                       'toolkit_commit': source_revision()['commit'],
                       'toolkit_source_dirty': source_revision()['source_dirty'],
                       'run_count': run_count, 'line_number_convention': '1-based original output file lines'}
    }
    if include_cycles:
        report['cycle_records'] = records
    return report
