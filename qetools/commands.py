"""Portable command entry points and concise, evidence-based reports."""
import argparse
from contextlib import redirect_stdout
from io import StringIO
import json
import sys
from pathlib import Path
from . import __version__
from . import cli


def analyze(path):
    text = cli.read(path)
    if not cli.is_qe_output(path):
        raise ValueError(f"No recognizable Quantum ESPRESSO output in {path}")
    core = cli.output_core(text)
    kind = cli.classify_output(path, text)
    flags = cli.termination_flags(text)
    electronic = cli.latest_electronic_status(text)
    ionic = cli.ionic_status(text, kind)
    execution = ("ERROR" if flags['error'] else "STOPPED" if flags['wall_stop'] or flags['stopped']
                 else "COMPLETED" if flags['jobdone'] else "UNTERMINATED")
    status = cli.summary_status(text, kind)
    warnings = []
    if any(value > 0 for value in core['neg']):
        warnings.append("Negative charge density reported; inspect magnitude and persistence.")
    if cli.re.search(r"eigenvalues? not converged", text, cli.re.I):
        warnings.append("Eigenvalue convergence warnings reported; inspect the final cycle.")
    if cli.re.search(r'charge is wrong', text, cli.re.I):
        warnings.append("QE reports 'charge is wrong'; inspect charge consistency before using the result.")
    if ionic == "CONVERGED" and status != "RELAX CONV":
        warnings.append("Ionic convergence marker exists, but a completed final electronic cycle is unproven.")
    if kind in ('NSCF', 'BANDS', 'NSCF/BANDS'):
        electronic = 'N/A (non-self-consistent)'
    if execution == 'ERROR':
        action = "Inspect the QE error and correct its cause before rerunning."
    elif execution == 'STOPPED':
        action = "Check restart files and the stopping reason before continuing."
    elif status in ('CONVERGED', 'RELAX CONV'):
        action = "Numerical convergence is established; verify physical settings for your study."
    else:
        action = "Inspect the final output section; final convergence is not established."
    return {
        'file': str(Path(path).resolve()), 'calculation': kind,
        'status': status, 'execution': execution, 'electronic': electronic, 'ionic': ionic,
        'last_energy_ry': cli.last_or(core['energies']),
        'last_scf_accuracy_ry': cli.last_or(core['acc']),
        'last_total_force_ry_bohr': cli.last_or(core['forces']),
        'completed_scf_cycles': len(cli.re.findall(r'convergence has been achieved in\s+\d+\s+iterations', text, cli.re.I)),
        'warnings': warnings, 'next_step': action,
    }


def show(report):
    print(f"QETools | {report['calculation']} | {report['status']}")
    print(f"File: {report['file']}")
    if 'selection_reason' in report:
        print(f"Selection: {report['selection_reason']}")
    print(f"Execution: {report['execution']}   Electronic: {report['electronic']}")
    if report['ionic'] != 'N/A':
        print(f"Ionic relaxation: {report['ionic']}")
    for label, key, unit in (
        ('Last total energy', 'last_energy_ry', 'Ry'),
        ('Last SCF accuracy estimate', 'last_scf_accuracy_ry', 'Ry'),
        ('Last total force', 'last_total_force_ry_bohr', 'Ry/Bohr'),
    ):
        value = report[key]
        if value is not None:
            print(f"{label}: {value:.10g} {unit}")
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
    parser = argparse.ArgumentParser(prog='qe'+command,
        description='Summarize the latest PWSCF run with separate completion and convergence evidence.')
    parser.add_argument('path', nargs='?', default='.', help='output file or directory (default: current directory)')
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument('--best', action='store_true', help='prefer completed converged outputs, then lowest final residual')
    selection.add_argument('-l', '--list', action='store_true', help='report all matching files')
    formatting = parser.add_mutually_exclusive_group()
    formatting.add_argument('--json', action='store_true', help='machine-readable report with explicit units in keys')
    formatting.add_argument('-v', '--verbose', action='store_true', help='show the existing detailed diagnostics')
    args = parser.parse_args(argv)
    target = Path(args.path)
    if not target.exists():
        parser.error(f'path does not exist: {target}')
    files = cli.discover_outputs(target)
    expected = ('SCF',) if command == 'scf' else ('RELAX', 'VC-RELAX')
    if target.is_dir():
        files = [f for f in files if cli.classify_output(f) in expected]
    if not files:
        parser.error(f'no {command} outputs found; pass an explicit output filename')
    reports = [analyze(f) for f in files]
    if target.is_file() and reports[0]['calculation'] not in expected:
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


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog='qetools', description='Quantum ESPRESSO output analysis on workstations and clusters.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument('command', choices=('scf', 'relax', 'history', 'compare', 'check'))
    if not args:
        parser.print_help()
        return
    if args and args[0] in ('scf', 'relax', 'history', 'compare', 'check'):
        parsed = argparse.Namespace(command=args[0])
        rest = args[1:]
    else:
        parsed, rest = parser.parse_known_args(args)
    try:
        if parsed.command in ('scf', 'relax'):
            output_command(parsed.command, rest)
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
