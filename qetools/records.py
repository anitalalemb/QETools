"""Electronic-cycle records with original file line numbers and unconverted units.

Output positions after a cycle are retained as proposed/final geometry. They
are not retroactively treated as the geometry used for that cycle's forces.
"""
from copy import deepcopy
import math
import re

FLOAT = r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?'


def number(value):
    return float(value.replace('D', 'E').replace('d', 'e'))


def latest_run(raw):
    starts = list(re.finditer(r'(?im)^[ \t]*Program[ \t]+\S+', raw))
    start = starts[-1].start() if starts else 0
    # Match leading whitespace without moving the first evidence line backwards.
    content_start = raw.find('Program', start) if starts else 0
    return raw[content_start:], raw[:content_start].count('\n'), len(starts)


def parse_cycles(raw, starting_threshold=None):
    text, offset, run_count = latest_run(raw)
    lines = text.splitlines()
    cycles, active = [], None
    geometry, cell = None, None
    alat, nat = None, None
    final_coordinates = False
    final_geometry, final_cell = None, None
    threshold = starting_threshold

    def begin(line):
        return {'cycle_id': len(cycles) + 1, 'start_line': line, 'status': 'INCOMPLETE',
                'electronic_outcome': None, 'iterations': [], 'last_energy_ry': None, 'last_scf_accuracy_ry': None,
                'active_conv_thr_ry': threshold, 'geometry_at_start': deepcopy(geometry),
                'cell_at_start': deepcopy(cell), 'forces': None, 'stress': None, 'evidence': {}}

    def finish(line):
        if active:
            active['end_line'] = line
            cycles.append(active)

    i = 0
    while i < len(lines):
        s = lines[i].strip(); lineno = i + 1 + offset
        m = re.search(r'lattice parameter \(alat\)\s*=\s*('+FLOAT+')', s, re.I)
        if m:
            alat = number(m.group(1))
        m = re.search(r'number of atoms/cell\s*=\s*(\d+)', s, re.I)
        if m:
            nat = int(m.group(1))
        m = re.search(r'(?:new conv_thr|scf convergence threshold)\s*=\s*('+FLOAT+')', s, re.I)
        if m:
            threshold = number(m.group(1))
            if active and not active['iterations']:
                active['active_conv_thr_ry'] = threshold
        if s.lower().startswith('begin final coordinates'):
            final_coordinates = True
        if s.lower().startswith('end final coordinates'):
            final_coordinates = False
        if 'crystal axes:' in s.lower():
            vectors = []
            for row in lines[i + 1:i + 4]:
                m = re.search(r'=\s*\(\s*('+FLOAT+r')\s+('+FLOAT+r')\s+('+FLOAT+r')\s*\)', row)
                if m:
                    vectors.append([number(v) for v in m.groups()])
            if len(vectors) == 3:
                cell = {'vectors': vectors, 'units': 'alat', 'alat_bohr': alat, 'source_line': lineno}
        if s.startswith('CELL_PARAMETERS'):
            vectors = []
            for row in lines[i + 1:i + 4]:
                tokens = row.split()
                if len(tokens) >= 3 and all(re.fullmatch(FLOAT, t) for t in tokens[:3]):
                    vectors.append([number(v) for v in tokens[:3]])
            unit = re.search(r'\((.*?)\)|\{(.*?)\}', s)
            descriptor = next(g for g in unit.groups() if g is not None) if unit else 'alat'
            explicit_alat = re.search(r'alat\s*=\s*('+FLOAT+')', descriptor)
            if explicit_alat:
                alat = number(explicit_alat.group(1)); descriptor = 'alat'
            cell = {'vectors': vectors, 'units': descriptor.lower(), 'alat_bohr': alat,
                    'complete': len(vectors) == 3, 'source_line': lineno}
            if final_coordinates:
                final_cell = deepcopy(cell)
        is_sites = 'site n.' in s.lower() and 'positions (alat units)' in s.lower()
        if is_sites or s.startswith('ATOMIC_POSITIONS'):
            atoms = []
            unit = re.search(r'\((.*?)\)|\{(.*?)\}', s)
            units = 'alat' if is_sites else next((g for g in unit.groups() if g is not None), 'alat') if unit else 'alat'
            for row in lines[i + 1:]:
                if is_sites:
                    m = re.search(r'^\s*(\d+)\s+(\S+)\s+tau\(\s*\d+\)\s*=\s*\(\s*('+FLOAT+r')\s+('+FLOAT+r')\s+('+FLOAT+')', row)
                    if not m:
                        break
                    atoms.append({'atom_id': int(m.group(1)), 'species': m.group(2), 'coordinates': [number(v) for v in m.groups()[2:]]})
                else:
                    tokens = row.split()
                    if len(tokens) < 4 or not all(re.fullmatch(FLOAT, t) for t in tokens[1:4]):
                        break
                    atoms.append({'atom_id': len(atoms) + 1, 'species': tokens[0], 'coordinates': [number(v) for v in tokens[1:4]],
                                  'free_directions': [int(t) for t in tokens[4:7]] if len(tokens) >= 7 and all(t in ('0', '1') for t in tokens[4:7]) else None})
            geometry = {'atoms': atoms, 'units': units.lower(), 'alat_bohr': alat,
                        'complete': bool(atoms) and (nat is None or len(atoms) == nat), 'source_line': lineno}
            if final_coordinates:
                final_geometry = deepcopy(geometry)
            elif active and not is_sites:
                active['proposed_geometry_after_cycle'] = deepcopy(geometry)
        start = s.lower() == 'self-consistent calculation'
        miter = re.search(r'^iteration #\s*(\d+)', s, re.I)
        if start or miter and (active is None or int(miter.group(1)) == 1 and active['iterations']):
            finish(lineno - 1)
            active = begin(lineno)
        if active:
            if miter:
                active['iterations'].append(int(miter.group(1))); active['evidence']['last_iteration'] = lineno
            if re.search(r'convergence has been achieved in\s+\d+\s+iterations', s, re.I):
                active['status'] = 'CONVERGED'; active['electronic_outcome'] = 'CONVERGED'; active['evidence']['convergence'] = lineno
            if re.search(r'convergence NOT achieved', s, re.I):
                active['status'] = 'NOT CONVERGED'; active['electronic_outcome'] = 'NOT CONVERGED'; active['evidence']['convergence'] = lineno
            if 'Error in routine' in s:
                active['status'] = 'ERROR'; active['evidence']['error'] = lineno
            m = re.search(r'^!?\s*total energy\s*=\s*('+FLOAT+r')\s*Ry', s, re.I)
            if m:
                active['last_energy_ry'] = number(m.group(1)); active['evidence']['energy'] = lineno
            m = re.search(r'estimated scf accuracy\s*<\s*('+FLOAT+r')\s*Ry', s, re.I)
            if m:
                active['last_scf_accuracy_ry'] = number(m.group(1)); active['evidence']['accuracy'] = lineno
            if s.lower().startswith('forces acting on atoms'):
                vectors = []; j = i + 1
                while j < len(lines):
                    row = lines[j].strip()
                    if not row:
                        j += 1
                        continue
                    m = re.match(r'atom\s+(\d+)\s+type\s+(\d+)\s+force\s*=\s*('+FLOAT+r')\s+('+FLOAT+r')\s+('+FLOAT+')', row, re.I)
                    if not m:
                        break
                    vectors.append({'atom_id': int(m.group(1)), 'type_id': int(m.group(2)),
                                    'vector_ry_bohr': [number(v) for v in m.groups()[2:]], 'source_line': j + 1 + offset})
                    j += 1
                expected_ids = list(range(1, nat + 1)) if nat else None
                active['forces'] = {'atoms': vectors, 'expected_atoms': nat,
                                    'complete': bool(vectors) and (expected_ids is not None and [a['atom_id'] for a in vectors] == expected_ids),
                                    'max_abs_component_ry_bohr': max((abs(v) for a in vectors for v in a['vector_ry_bohr']), default=None),
                                    'max_norm_ry_bohr': max((math.sqrt(sum(v*v for v in a['vector_ry_bohr'])) for a in vectors), default=None),
                                    'source_line': lineno}
            m = re.search(r'^Total force\s*=\s*('+FLOAT+')', s, re.I)
            if m:
                active['total_force_diagnostic_ry_bohr'] = number(m.group(1)); active['evidence']['total_force'] = lineno
            if re.search(r'^total\s+stress\s+\(Ry/bohr\*\*3\)', s, re.I):
                rows = []
                for row in lines[i + 1:i + 4]:
                    tokens = row.split()
                    if len(tokens) >= 6 and all(re.fullmatch(FLOAT, t) for t in tokens[:6]):
                        rows.append([number(v) for v in tokens[:6]])
                pressure = re.search(r'P\s*=\s*('+FLOAT+')', s)
                active['stress'] = {'tensor_ry_bohr3': [r[:3] for r in rows], 'tensor_kbar': [r[3:] for r in rows],
                                    'pressure_kbar': number(pressure.group(1)) if pressure else None,
                                    'complete': len(rows) == 3, 'source_line': lineno}
        i += 1
    finish(len(lines) + offset)
    return {'run_count': run_count, 'latest_run_start_line': offset + 1, 'expected_atoms': nat,
            'cycles': cycles, 'final_geometry': final_geometry, 'final_cell': final_cell,
            'line_number_convention': '1-based original output file lines'}
