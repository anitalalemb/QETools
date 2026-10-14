"""Calculation identity with explicit evidence and conservative conflicts."""
from pathlib import Path
import re

SUPPORTED_INPUT_TYPES = {'scf', 'nscf', 'bands', 'relax', 'vc-relax', 'md', 'vc-md'}


def identify(path, text, settings=None):
    name, low = Path(path).name.lower(), text.lower()
    program = re.search(r'(?im)^\s*Program\s+(\S+)', text)
    program = program.group(1).upper() if program else None
    warnings = []
    observed, evidence = None, None
    variable = 'new unit-cell volume' in low or 'variable cell' in low
    if program and program != 'PWSCF':
        observed, evidence = program, 'program header'
    elif 'bfgs geometry optimization' in low or 'bfgs converged' in low or 'number of bfgs steps' in low:
        observed, evidence = ('VC-RELAX' if variable else 'RELAX'), 'BFGS output'
    elif 'fire minimization' in low or re.search(r'(?im)^\s*FIRE\s*:', text):
        observed, evidence = ('VC-RELAX' if variable else 'RELAX'), 'FIRE output'
    elif 'molecular dynamics calculation' in low:
        observed, evidence = 'IONIC-DYNAMICS', 'dynamics output (MD/damped-relaxation context unproven)'
    elif 'band structure calculation' in low:
        observed, evidence = 'NSCF/BANDS', 'non-self-consistent output banner'
    declared = (settings or {}).get('calculation', 'scf').lower() if settings is not None else None
    if declared and declared not in SUPPORTED_INPUT_TYPES:
        warnings.append(f'Unsupported calculation value in supplied input: {declared}.')
        return {'calculation': 'UNKNOWN', 'source': 'unsupported supplied input', 'program': program, 'warnings': warnings}
    if declared:
        desired = declared.upper()
        compatible = (observed is None or observed == desired
                      or observed == 'NSCF/BANDS' and desired in ('NSCF', 'BANDS')
                      or observed == 'IONIC-DYNAMICS' and desired in ('RELAX', 'VC-RELAX', 'MD', 'VC-MD')
                      or observed in ('RELAX', 'VC-RELAX') and desired in ('RELAX', 'VC-RELAX') and not variable
                      or observed in ('MD', 'VC-MD') and desired in ('MD', 'VC-MD') and not variable)
        if not compatible:
            warnings.append(f'Supplied input declares {desired}, but output evidence indicates {observed}; verify the association.')
            return {'calculation': 'UNKNOWN', 'source': 'input/output conflict', 'program': program, 'warnings': warnings}
        return {'calculation': desired, 'source': 'supplied input (association declared by user)', 'program': program, 'warnings': warnings}
    if observed:
        return {'calculation': observed, 'source': evidence, 'program': program, 'warnings': warnings}
    for token, kind in [('vc-relax', 'VC-RELAX'), ('vc-md', 'VC-MD'), ('relax', 'RELAX'), ('rx', 'RELAX'), ('nscf', 'NSCF'), ('bands?', 'BANDS'), ('md', 'MD')]:
        if re.search(r'(^|[_\-.])' + token + r'([_\-.]|$)', name):
            warnings.append('Calculation type relies on a filename token; supply --input to confirm the intended calculation.')
            return {'calculation': kind, 'source': 'filename fallback', 'program': program, 'warnings': warnings}
    if program == 'PWSCF' and ('estimated scf accuracy' in low or 'convergence has been achieved' in low):
        warnings.append('Electronic SCF evidence is present; an early relaxation or MD run may look the same. Supply --input if ambiguous.')
        return {'calculation': 'SCF', 'source': 'electronic SCF evidence (ionic context unproven)', 'program': program, 'warnings': warnings}
    return {'calculation': 'UNKNOWN', 'source': 'insufficient output evidence', 'program': program, 'warnings': ['Calculation type is unproven; supply the intended input with --input.']}
