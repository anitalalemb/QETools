"""Explicit, index-matched geometry comparison from PWSCF output snapshots.

This first version rejects cell changes and skew-cell periodic wrapping.
No atom assignment, rigid alignment or relaxed-structure certification is implied.
"""
import hashlib
import math
import re
from pathlib import Path
from .records import parse_cycles, latest_run


def snapshot(path, which):
    data = Path(path).read_bytes()
    raw = data.decode(errors='replace')
    scoped = latest_run(raw)[0]
    if not re.search(r'(?im)^\s*Program\s+PWSCF(?:\s|$)', scoped):
        raise ValueError('Structure comparison requires a recognizable latest PWSCF output run')
    records = parse_cycles(raw)
    if which == 'initial':
        cycles = records['cycles']
        geometry = cycles[0]['geometry_at_start'] if cycles else None
        cell = cycles[0]['cell_at_start'] if cycles else None
    else:
        geometry, cell = records['final_geometry'], records['final_cell']
        # QE fixed-cell final coordinates often omit a repeated cell block.
        if cell is None and records['cycles']:
            cell = records['cycles'][-1]['cell_at_start']
    if not geometry or not geometry['complete']:
        raise ValueError(f'{path}: no complete {which} geometry; final requires an explicit final-coordinate block')
    if not cell or len(cell['vectors']) != 3 or cell.get('complete') is False:
        raise ValueError(f'{path}: no complete cell associated with {which} geometry')
    cell_units = cell['units']
    if cell_units == 'alat':
        scale = cell.get('alat_bohr')
        if not scale or scale <= 0:
            raise ValueError('Cell uses alat but no positive lattice parameter was found')
        vectors = [[v * scale for v in row] for row in cell['vectors']]; units = 'bohr'
    elif cell_units in ('bohr', 'angstrom'):
        vectors = cell['vectors']; units = cell_units
    else:
        raise ValueError(f'Unsupported cell units: {cell_units}')
    coordinates = []
    for atom in geometry['atoms']:
        xyz = atom['coordinates']
        if geometry['units'] == 'crystal':
            xyz = [sum(xyz[i]*vectors[i][j] for i in range(3)) for j in range(3)]
        elif geometry['units'] == 'alat' and units == 'bohr':
            scale = geometry.get('alat_bohr')
            if not scale or scale <= 0:
                raise ValueError('Positions use alat without a positive lattice parameter')
            xyz = [v * scale for v in xyz]
        elif geometry['units'] != units:
            raise ValueError('Mixed Cartesian units are not supported; use consistent output units')
        coordinates.append(xyz)
    if not all(math.isfinite(v) for row in vectors+coordinates for v in row):
        raise ValueError('Non-finite geometry values')
    a,b,c = vectors
    volume = a[0]*(b[1]*c[2]-b[2]*c[1])-a[1]*(b[0]*c[2]-b[2]*c[0])+a[2]*(b[0]*c[1]-b[1]*c[0])
    if abs(volume) < 1e-12:
        raise ValueError('Singular cell')
    return {'file':str(Path(path).resolve()), 'sha256':hashlib.sha256(data).hexdigest(),
            'snapshot':which, 'geometry_source_line':geometry['source_line'],
            'cell_source_line':cell['source_line'], 'units':units, 'cell':vectors,
            'atoms':geometry['atoms'], 'coordinates':coordinates}


def compare(reference, candidate=None, first='initial', second='final', periodic=()):
    a = snapshot(reference, first); b = snapshot(candidate or reference, second)
    if a['units'] != b['units'] or any(abs(x-y)>1e-9 for ra,rb in zip(a['cell'],b['cell']) for x,y in zip(ra,rb)):
        raise ValueError('Cell changed or units differ; strain/internal-motion separation is not implemented')
    if len(a['atoms']) != len(b['atoms']) or any((x['atom_id'],x['species']) != (y['atom_id'],y['species']) for x,y in zip(a['atoms'],b['atoms'])):
        raise ValueError('Atom count, order or species differ; provide structures with confirmed index correspondence')
    cell = a['cell']
    if periodic and any(abs(sum(cell[i][k]*cell[j][k] for k in range(3))) > 1e-10*math.sqrt(sum(v*v for v in cell[i])*sum(v*v for v in cell[j])) for i in range(3) for j in range(i)):
        raise ValueError('Periodic minimum-image comparison currently requires orthogonal lattice vectors')
    rows=[]
    for atom, start, end in zip(a['atoms'],a['coordinates'],b['coordinates']):
        delta=[y-x for x,y in zip(start,end)]
        for axis in periodic:
            vector=cell[axis]; norm2=sum(v*v for v in vector)
            fraction=sum(d*v for d,v in zip(delta,vector))/norm2
            # Deterministic half-cell convention: [-1/2, 1/2).
            shift=math.floor(fraction+0.5)
            delta=[d-shift*v for d,v in zip(delta,vector)]
        rows.append({'atom_id':atom['atom_id'],'species':atom['species'],
                     'dx':delta[0],'dy':delta[1],'dz':delta[2],
                     'displacement':math.sqrt(sum(v*v for v in delta))})
    return {'schema_version':'1.0','units':a['units'],'reference':a,'candidate':b,
            'matching':'same atom index and species; user must confirm physical identity',
            'periodic_lattice_axes':list(periodic),'alignment':'none', 'atoms':rows,
            'maximum_displacement':max(r['displacement'] for r in rows),
            'rms_displacement':math.sqrt(sum(r['displacement']**2 for r in rows)/len(rows)),
            'limits':['Cell must be unchanged. No rigid translation or rotation removed.',
                      'An explicit final-coordinate block does not prove ionic convergence.',
                      'Periodic wrapping requires an orthogonal cell; axes refer to lattice vectors.']}
