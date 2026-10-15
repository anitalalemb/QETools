import tempfile
from pathlib import Path
import unittest
from qetools.structure import compare

TEXT='''Program PWSCF v.7.5
number of atoms/cell = 1
CELL_PARAMETERS (angstrom)
10 0 0
0 10 0
0 0 10
ATOMIC_POSITIONS (crystal)
Si 0.95 0.2 0.3
Self-consistent Calculation
iteration # 1
Begin final coordinates
ATOMIC_POSITIONS (crystal)
Si 0.05 0.4 0.3
End final coordinates
'''

class StructureTests(unittest.TestCase):
    def result(self,text=TEXT,**kwargs):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'relax.out');p.write_text(text)
            return compare(p,**kwargs)

    def test_cartesian_displacement(self):
        r=self.result(); a=r['atoms'][0]
        self.assertAlmostEqual(a['dx'],-9)
        self.assertAlmostEqual(a['dy'],2)
        self.assertAlmostEqual(a['displacement'],85**0.5)
        self.assertEqual(r['units'],'angstrom')
        self.assertEqual(r['reference']['geometry_source_line'],7)

    def test_periodic_axes_are_explicit(self):
        r=self.result(periodic=(0,))
        self.assertAlmostEqual(r['atoms'][0]['dx'],1)
        self.assertAlmostEqual(r['maximum_displacement'],5**0.5)

    def test_skew_periodic_cell_rejected(self):
        with self.assertRaisesRegex(ValueError,'orthogonal'):
            self.result(TEXT.replace('0 10 0','2 10 0'),periodic=(0,))

    def test_missing_final_is_not_replaced_with_proposal(self):
        with self.assertRaisesRegex(ValueError,'final geometry'):
            self.result(TEXT.replace('Begin final coordinates','').replace('End final coordinates',''))

    def test_species_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,'species'):
            self.result(TEXT.replace('Si 0.05','C 0.05'))

    def test_changed_cell_rejected(self):
        with self.assertRaisesRegex(ValueError,'Cell changed'):
            self.result(TEXT.replace('Begin final coordinates','Begin final coordinates\nCELL_PARAMETERS (angstrom)\n11 0 0\n0 10 0\n0 0 10'))

    def test_zero_displacement(self):
        self.assertEqual(self.result(TEXT.replace('Si 0.05 0.4','Si 0.95 0.2'))['maximum_displacement'],0)

    def test_alat_coordinates_and_cell_convert_to_bohr(self):
        text=TEXT.replace('number of atoms/cell = 1','number of atoms/cell = 1\nlattice parameter (alat) = 2.0 a.u.').replace('CELL_PARAMETERS (angstrom)','CELL_PARAMETERS (alat)').replace('ATOMIC_POSITIONS (crystal)','ATOMIC_POSITIONS (alat)')
        r=self.result(text)
        self.assertEqual(r['units'],'bohr')
        self.assertAlmostEqual(r['atoms'][0]['dx'],-1.8)
        self.assertAlmostEqual(r['atoms'][0]['dy'],0.4)

    def test_cli_json(self):
        import contextlib, io, json
        from qetools.commands import main
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'relax.out');p.write_text(TEXT);out=io.StringIO()
            with contextlib.redirect_stdout(out):main(['structure',str(p),'--periodic','a','--json'])
            self.assertAlmostEqual(json.loads(out.getvalue())['atoms'][0]['dx'],1)

    def changed(self, final_atom='Si 0.95 0.2 0.3'):
        return TEXT.replace('Begin final coordinates','Begin final coordinates\nCELL_PARAMETERS (angstrom)\n20 0 0\n0 10 0\n0 0 10').replace('Si 0.05 0.4 0.3', final_atom)

    def test_pure_cell_expansion_has_no_internal_motion(self):
        r=self.result(self.changed(),separate_cell=True)
        self.assertAlmostEqual(r['atoms'][0]['dx'],9.5)
        self.assertAlmostEqual(r['atoms'][0]['internal_displacement'],0)
        self.assertAlmostEqual(r['cell_decomposition']['volume_ratio'],2)

    def test_cell_and_internal_motion_reconstruct_total(self):
        r=self.result(self.changed('Si 0.975 0.2 0.3'),separate_cell=True)
        atom=r['atoms'][0]
        self.assertAlmostEqual(atom['internal_displacement_vector'][0],0.5)
        for k,key in enumerate(('dx','dy','dz')):
            self.assertAlmostEqual(atom['affine_displacement_vector'][k]+atom['internal_displacement_vector'][k],atom[key])

    def test_decomposition_in_skew_reference_cell(self):
        text=self.changed().replace('0 10 0','2 10 0')
        r=self.result(text,separate_cell=True)
        self.assertAlmostEqual(r['atoms'][0]['internal_displacement'],0)

    def test_periodic_decomposition_requires_explicit_unwrapped_data(self):
        with self.assertRaisesRegex(ValueError,'unwrapped'):
            self.result(self.changed(),separate_cell=True,periodic=(0,))
