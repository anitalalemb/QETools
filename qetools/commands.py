"""Portable command entry points and concise, evidence-based reports."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
from pathlib import Path
from . import __version__
from . import cli


def analyze(path, input_path=None, include_cycles=False):
    from .reporting import analyze as parse
    return parse(path, input_path=input_path, include_cycles=include_cycles)


def show(report):
    print(f"QETools | {report['calculation']} | {report['status']}")
    print(f"File: {report['file']}")
    if 'selection_reason' in report:
        print(f"Selection: {report['selection_reason']}")
    print(f"Type evidence: {report.get('classification_source', 'unknown')}")
    print(f"Execution: {report['execution']}   Electronic: {report['electronic']}")
    for label, key in (('Last ended electronic cycle', 'last_ended_cycle'),
                       ('Last converged electronic cycle', 'last_converged_cycle')):
        cycle = report.get(key)
        print(f"{label}: " + (f"{cycle['cycle_id']} | outcome: {cycle['electronic_outcome']} | latest recorded state: {cycle['status']}" if cycle else 'none found'))
    if report.get('exchange_convergence') not in (None, 'N/A', 'NOT ASSESSED'):
        print(f"EXX convergence: {report['exchange_convergence']}")
    if report['ionic'] != 'N/A':
        print(f"Ionic relaxation: {report['ionic']}")
    for label, key, unit in (
        ('Last total energy', 'last_energy_ry', 'Ry'),
        ('Last SCF accuracy estimate', 'last_scf_accuracy_ry', 'Ry'),
        ('QE printed total-force diagnostic', 'last_total_force_ry_bohr', 'Ry/Bohr'),
    ):
        value = report[key]
        if value is not None:
            print(f"{label}: {value:.10g} {unit}")
    threshold = report.get('threshold_check', {})
    if threshold.get('requested_conv_thr_ry') is not None:
        print(f"Input SCF target: {threshold['requested_conv_thr_ry']:.3g} Ry | {threshold['comparison']}")
    force = report.get('last_force_record')
    if force:
        scope = 'complete block' if force['complete'] else 'partial block; observed atoms only'
        print(f"Atomic forces: cycle {force['cycle_id']} | {scope} | {force['observed_atoms']} atoms")
        if force['max_norm_ry_bohr'] is not None:
            atoms = ', '.join(str(i) for i in force['max_norm_atom_ids'])
            print(f"Largest atomic force norm: {force['max_norm_ry_bohr']:.10g} Ry/Bohr | atom IDs: {atoms}")
            print(f"Largest absolute force component: {force['max_abs_component_ry_bohr']:.10g} Ry/Bohr")
        if report.get('current_cycle') and report['current_cycle']['cycle_id'] != force['cycle_id']:
            print('These forces belong to an earlier electronic cycle.')
    stress = report.get('last_stress_record')
    if stress:
        scope = 'complete tensor' if stress['complete'] else 'partial tensor'
        print(f"Stress: cycle {stress['cycle_id']} | {scope}")
        if stress['pressure_kbar'] is not None:
            print(f"Printed pressure: {stress['pressure_kbar']:.10g} kbar | relaxation target not assessed")
        if report.get('current_cycle') and report['current_cycle']['cycle_id'] != stress['cycle_id']:
            print('This stress belongs to an earlier electronic cycle.')
    for cycle in report.get('cycle_records', {}).get('cycles', []):
        forces = cycle['forces'] or {}
        stress = cycle['stress'] or {}
        print(f"Cycle {cycle['cycle_id']}: {cycle['status']} | E={cycle['last_energy_ry']} Ry | "
              f"max |F component|={forces.get('max_abs_component_ry_bohr')} Ry/Bohr | P={stress.get('pressure_kbar')} kbar")
    for warning in report['warnings']:
        print(f"Warning: {warning}")
    print(f"Next: {report['next_step']}")


def legacy(command, args):
    old = sys.argv
    sys.argv = ['qetools', command, *args]
    try:
        cli.legacy_main()
    finally:
        sys.argv = old


def output_command(command, argv):
    parser = argparse.ArgumentParser(prog='qetools inspect' if command == 'inspect' else 'qe'+command,
        description=('Inspect the latest QE program and execution evidence; advanced workflow science is not assessed.'
                     if command == 'inspect' else 'Summarize the latest PWSCF run with separate completion and convergence evidence.'))
    parser.add_argument('path', nargs='?', default='.', help='output file or directory (default: current directory)')
    parser.add_argument('--input', type=Path, help='explicit PWSCF input association; requires one output file')
    parser.add_argument('--steps', action='store_true', help='include electronic-cycle geometry/force/stress records')
    selection = parser.add_mutually_exclusive_group()
    if command != 'inspect':
        selection.add_argument('--best', action='store_true', help='prefer completed converged outputs, then lowest final residual')
    else:
        parser.set_defaults(best=False)
    selection.add_argument('-l', '--list', action='store_true', help='report all matching files')
    formatting = parser.add_mutually_exclusive_group()
    formatting.add_argument('--json', action='store_true', help='machine-readable report with explicit units in keys')
    formatting.add_argument('-v', '--verbose', action='store_true', help='show the existing detailed diagnostics')
    args = parser.parse_args(argv)
    if command == 'inspect' and args.best:
        parser.error('--best is for SCF/relaxation ranking; inspect does not rank different workflows')
    target = Path(args.path)
    if not target.exists():
        parser.error(f'path does not exist: {target}')
    files = cli.discover_outputs(target)
    if args.input and target.is_dir():
        parser.error('--input requires one explicit output file, not a directory')
    expected = ('SCF',) if command == 'scf' else ('RELAX', 'VC-RELAX')
    if target.is_dir() and command != 'inspect':
        files = [f for f in files if cli.classify_output(f) in expected]
    if not files:
        parser.error(f'no {command} outputs found; pass an explicit output filename')
    reports = [analyze(f, input_path=args.input, include_cycles=args.steps or args.verbose and bool(args.input or command == 'inspect')) for f in files]
    if command != 'inspect' and target.is_file() and reports[0]['calculation'] not in expected:
        parser.error(f"file is classified as {reports[0]['calculation']}; use the appropriate command or inspect it manually")
    if args.best:
        reports.sort(key=lambda r: (
            r['status'] not in ('CONVERGED', 'RELAX CONV'),
            r['last_scf_accuracy_ry'] if r['last_scf_accuracy_ry'] is not None else float('inf'),
            -Path(r['file']).stat().st_mtime, r['file']))
    chosen = reports if args.list else reports[:1]
    if args.list:
        mode, reason = 'list', 'all matching outputs'
    elif target.is_file():
        mode, reason = 'explicit', 'exact file supplied by the user'
    elif args.best:
        mode = 'best'
        successful = any(r['status'] in ('CONVERGED', 'RELAX CONV') for r in reports)
        reason = ('completed converged candidate, ranked by final SCF accuracy estimate'
                  if successful else 'no completed converged candidate; ranked by final SCF accuracy estimate only')
        if not successful:
            chosen[0]['warnings'].append('No completed converged candidate was found. This selection is not a final converged result.')
    else:
        mode, reason = 'latest', 'newest matching output by file modification time'
    for report in chosen:
        report.update(selection_mode=mode, selection_reason=reason, candidate_count=len(reports))
    if args.verbose and (args.input or args.steps or command == 'inspect'):
        for report in chosen:
            show(report)
            print(f"Evidence: {report['provenance']['line_number_convention']} | Output SHA256: {report['provenance']['output_sha256']}")
        return
    if args.verbose:
        print(f'Selection: {reason}')
        legacy(command, (['--list', str(target)] if args.list else [chosen[0]['file']]))
    elif args.json:
        print(json.dumps(chosen if args.list else chosen[0], indent=2, allow_nan=False))
    else:
        for i, report in enumerate(chosen):
            if i:
                print()
            show(report)


def history_command(argv):
    parser = argparse.ArgumentParser(prog='qehis', description='QE calculation history with explicit type and cycle evidence.')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--input', type=Path, help='explicit PWSCF input association; requires one output file')
    parser.add_argument('--json', action='store_true', help='history records with source lines, hashes and units')
    args = parser.parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        parser.error(f'path does not exist: {target}')
    if args.input and not target.is_file():
        parser.error('--input requires one explicit output file')
    if args.json:
        from .history import build_record
        files = sorted(cli.discover_outputs(target), key=lambda f: (f.stat().st_mtime, str(f)))
        rows = [build_record(path, input_path=args.input) for path in files]
        print(json.dumps({'schema_version': '1.0', 'report_kind': 'history', 'records': rows}, indent=2, allow_nan=False))
        return
    # Validate the declared association before printing the table.
    if args.input:
        analyze(target, input_path=args.input)
    old = sys.argv
    sys.argv = ['qehis']
    try:
        cli.history(target, input_path=args.input)
    finally:
        sys.argv = old


def check_command(argv):
    parser = argparse.ArgumentParser(prog='qecheck', description='Check input, files and optional launch scripts.')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('--all', action='store_true', help='show detailed evidence including passed checks')
    args = parser.parse_args(argv)
    legacy_args = (['--all'] if args.all else []) + [args.path]
    if args.verbose or args.all:
        legacy('check', legacy_args)
        return
    buffer = StringIO()
    with redirect_stdout(buffer):
        legacy('check', legacy_args)
    print('QETools | Input and file integrity')
    for line in buffer.getvalue().splitlines():
        if (cli.re.match(r"^\[\d+/\d+\] INPUT:", line)
                or line.strip().startswith(('[FAIL]', '[WARN]', 'Launch validation:'))
                or line.startswith(('INTEGRITY VERDICT', 'OVERALL INTEGRITY', 'Inputs inspected', 'Total warnings', 'Total failures'))):
            print(line.strip())
    print('Next: use qecheck --verbose for paths, matches and evidence.')


def structure_command(argv):
    parser = argparse.ArgumentParser(prog='qetools structure', description='Compare explicit initial/final geometries with confirmed atom-index matching.')
    parser.add_argument('reference', type=Path)
    parser.add_argument('candidate', nargs='?', type=Path)
    parser.add_argument('--from', dest='first', choices=('initial','final'), default='initial')
    parser.add_argument('--to', dest='second', choices=('initial','final'), default='final')
    parser.add_argument('--periodic', choices=('none','a','b','c','ab','ac','bc','abc'), default='none', help='lattice directions to wrap; requires an orthogonal cell')
    parser.add_argument('--separate-cell', action='store_true', help='separate affine cell motion from internal motion; requires unwrapped coordinates')
    parser.add_argument('--json', action='store_true')
    args=parser.parse_args(argv)
    from .structure import compare
    report=compare(args.reference,args.candidate,args.first,args.second,
                   tuple('abc'.index(c) for c in args.periodic) if args.periodic!='none' else (), separate_cell=args.separate_cell)
    if args.json:
        print(json.dumps(report,indent=2,allow_nan=False)); return
    print('QE STRUCTURE DISPLACEMENTS')
    print(f"Units: {report['units']} | matching: atom index + species | alignment: none")
    print(f"Periodic lattice axes: {args.periodic}")
    print(f"{'ATOM':<8}{'SPECIES':<12}{'DX':>14}{'DY':>14}{'DZ':>14}{'DISTANCE':>14}")
    for row in report['atoms']:
        print(f"{row['atom_id']:<8}{row['species']:<12}{row['dx']:>14.6g}{row['dy']:>14.6g}{row['dz']:>14.6g}{row['displacement']:>14.6g}")
    print(f"Maximum displacement: {report['maximum_displacement']:.6g} {report['units']}")
    print(f"RMS displacement: {report['rms_displacement']:.6g} {report['units']}")
    if report['cell_decomposition']:
        print('Internal motion after mapping reference fractional positions through the candidate cell:')
        print(f"{'ATOM':<8}{'INTERNAL DX':>14}{'INTERNAL DY':>14}{'INTERNAL DZ':>14}{'DISTANCE':>14}")
        for row in report['atoms']:
            x,y,z=row['internal_displacement_vector']
            print(f"{row['atom_id']:<8}{x:>14.6g}{y:>14.6g}{z:>14.6g}{row['internal_displacement']:>14.6g}")
        print(f"Cell volume ratio: {report['cell_decomposition']['volume_ratio']:.8g}")
        print(f"Maximum internal displacement: {report['cell_decomposition']['maximum_internal_displacement']:.6g} {report['units']}")
    for limit in report['limits']: print(f'Limit: {limit}')


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='qetools', description='Quantum ESPRESSO output analysis on workstations and clusters.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('command', choices=('scf', 'relax', 'inspect', 'history', 'compare', 'check', 'structure'))
    if not args:
        parser.print_help()
        return
    if args and args[0] in ('scf', 'relax', 'inspect', 'history', 'compare', 'check', 'structure'):
        parsed = argparse.Namespace(command=args[0])
        rest = args[1:]
    else:
        parsed, rest = parser.parse_known_args(args)
    try:
        if parsed.command in ('scf', 'relax', 'inspect'):
            output_command(parsed.command, rest)
        elif parsed.command == 'structure':
            structure_command(rest)
        elif parsed.command == 'history':
            history_command(rest)
        elif parsed.command == 'check':
            check_command(rest)
        elif parsed.command == 'compare':
            compare_parser = argparse.ArgumentParser(prog='qecompare', description='Compare two PWSCF outputs and their available setup evidence.')
            compare_parser.add_argument('first')
            compare_parser.add_argument('second')
            pair = compare_parser.parse_args(rest)
            legacy('compare', [pair.first, pair.second])
        else:
            legacy(parsed.command, rest)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


def scf():
    main(['scf', *sys.argv[1:]])


def relax():
    main(['relax', *sys.argv[1:]])


def history():
    main(['history', *sys.argv[1:]])


def check():
    main(['check', *sys.argv[1:]])


def compare():
    main(['compare', *sys.argv[1:]])
