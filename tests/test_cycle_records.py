import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest

from qetools import cli
from qetools.calculations import identify
from qetools.commands import main
from qetools.inputs import parse_namelists
from qetools.records import parse_cycles
from qetools.reporting import analyze

HEADER = '''Program PWSCF v.7.5
number of atoms/cell = 2
lattice parameter (alat) = 10.0 a.u.
crystal axes: (cart. coord. in units of alat)
a(1) = (1.0 0.0 0.0)
a(2) = (0.0 1.0 0.0)
a(3) = (0.0 0.0 1.0)
site n. atom positions (alat units)
1 Si tau(1) = (0.0 0.0 0.0)
2 Si tau(2) = (0.5 0.5 0.5)
'''
CYCLE = '''Self-consistent Calculation
iteration # 1
estimated scf accuracy < 1.0D-8 Ry
convergence has been achieved in 1 iterations
! total energy = -10.0 Ry
Forces acting on atoms (cartesian axes, Ry/au):

atom 1 type 1 force = 0.003 0.004 0.0
atom 2 type 1 force = 0.0 -0.002 0.0
Total force = 0.005385
 total stress (Ry/bohr**3) (kbar) P= -2.0
1.0D-5 0.0 0.0 -1.0 0.0 0.0
0.0 2.0D-5 0.0 0.0 -2.0 0.0
0.0 0.0 3.0D-5 0.0 0.0 -3.0
'''
PROPOSAL = '''ATOMIC_POSITIONS (crystal)
Si 0.1 0.0 0.0 0 0 1
Si 0.5 0.5 0.6 0 0 1
'''


class InputAndIdentity(unittest.TestCase):
    def test_quoted_title_is_not_an_assignment(self):
        n = parse_namelists("&CONTROL title='calculation=relax, conv_thr=0', calculation='scf' /\n&ELECTRONS conv_thr=1d-8 /")
        self.assertEqual(n['control']['calculation'], 'scf')
        self.assertEqual(n['electrons']['conv_thr'], '1d-8')

    def test_quotes_comments_and_absolute_paths(self):
        n = parse_namelists("&CONTROL prefix='a''b!c', outdir='/scratch/x' ! prefix='wrong'\n/\n&SYSTEM nat=2, nspin=2, starting_magnetization(1)=0.5 /")
        self.assertEqual(n['control']['prefix'], "a'b!c")
        self.assertEqual(n['control']['outdir'], '/scratch/x')
        self.assertEqual(n['system']['starting_magnetization(1)'], '0.5')

    def test_supplied_input_overrides_filename(self):
        r = identify('nscf.out', HEADER+CYCLE, {'calculation':'scf'})
        self.assertEqual(r['calculation'], 'SCF')
        self.assertIn('supplied input', r['source'])

    def test_supplied_input_disambiguates_nscf_and_bands(self):
        text = HEADER+'Band Structure Calculation\nEnd of band structure calculation\n'
        self.assertEqual(identify('generic.out',text,{'calculation':'nscf'})['calculation'],'NSCF')
        self.assertEqual(identify('generic.out',text,{'calculation':'bands'})['calculation'],'BANDS')

    def test_conflicting_input_and_output_is_unknown(self):
        r=identify('run.out',HEADER+'Band Structure Calculation\n',{'calculation':'scf'})
        self.assertEqual(r['calculation'],'UNKNOWN')
        self.assertEqual(r['source'],'input/output conflict')

    def test_ionic_and_cell_types(self):
        for calculation in ('relax','vc-relax','md','vc-md'):
            self.assertEqual(identify('generic.out',HEADER+CYCLE,{'calculation':calculation})['calculation'],calculation.upper())

    def test_dynamics_banner_does_not_conflict_with_damped_relaxation(self):
        text=HEADER+CYCLE+'End of molecular dynamics calculation\n'
        self.assertEqual(identify('run.out',text,{'calculation':'relax'})['calculation'],'RELAX')
        self.assertEqual(identify('run.out',text)['calculation'],'IONIC-DYNAMICS')

    def test_filename_fallback_is_marked(self):
        r=identify('relax.out',HEADER)
        self.assertEqual(r['source'],'filename fallback')
        self.assertTrue(r['warnings'])

    def test_unknown_header_does_not_prove_scf(self):
        self.assertEqual(identify('run.out',HEADER)['calculation'],'UNKNOWN')


class CycleAssociation(unittest.TestCase):
    def test_force_and_stress_units_and_values(self):
        c=parse_cycles(HEADER+CYCLE)['cycles'][0]
        self.assertTrue(c['forces']['complete'])
        self.assertAlmostEqual(c['forces']['max_abs_component_ry_bohr'],0.004)
        self.assertAlmostEqual(c['forces']['max_norm_ry_bohr'],0.005)
        self.assertEqual(c['stress']['tensor_kbar'],[[-1.0,0.0,0.0],[0.0,-2.0,0.0],[0.0,0.0,-3.0]])
        self.assertEqual(c['stress']['pressure_kbar'],-2.0)

    def test_proposal_does_not_replace_force_geometry(self):
        cycles=parse_cycles(HEADER+CYCLE+PROPOSAL+CYCLE)['cycles']
        self.assertEqual(cycles[0]['geometry_at_start']['atoms'][0]['coordinates'],[0.0,0.0,0.0])
        self.assertEqual(cycles[1]['geometry_at_start']['atoms'][0]['coordinates'],[0.1,0.0,0.0])
        self.assertEqual(cycles[0]['geometry_at_start']['units'],'alat')
        self.assertEqual(cycles[1]['geometry_at_start']['units'],'crystal')

    def test_cell_change_applies_to_next_cycle(self):
        cell='CELL_PARAMETERS (angstrom)\n2 0 0\n0 2 0\n0 0 3\n'
        cycles=parse_cycles(HEADER+CYCLE+cell+PROPOSAL+CYCLE)['cycles']
        self.assertEqual(cycles[0]['cell_at_start']['units'],'alat')
        self.assertEqual(cycles[1]['cell_at_start']['units'],'angstrom')
        self.assertEqual(cycles[1]['cell_at_start']['vectors'][2],[0.0,0.0,3.0])

    def test_force_decomposition_does_not_replace_total_vectors(self):
        extra='The non-local contribution to forces\natom 1 type 1 force = 99 99 99\natom 2 type 1 force = 99 99 99\n'
        c=parse_cycles(HEADER+CYCLE+extra)['cycles'][0]
        self.assertEqual(c['forces']['atoms'][0]['vector_ry_bohr'],[0.003,0.004,0.0])
        self.assertAlmostEqual(c['forces']['max_norm_ry_bohr'],0.005)

    def test_partial_force_block_is_not_complete(self):
        text=HEADER+CYCLE.replace('atom 2 type 1 force = 0.0 -0.002 0.0\n','')
        self.assertFalse(parse_cycles(text)['cycles'][0]['forces']['complete'])

    def test_partial_stress_block_is_not_complete(self):
        text=HEADER+CYCLE.replace('0.0 0.0 3.0D-5 0.0 0.0 -3.0\n','')
        self.assertFalse(parse_cycles(text)['cycles'][0]['stress']['complete'])

    def test_duplicate_atom_ids_do_not_pass_force_count(self):
        text=HEADER+CYCLE.replace('atom 2 type 1','atom 1 type 1')
        self.assertFalse(parse_cycles(text)['cycles'][0]['forces']['complete'])

    def test_incomplete_new_cycle_does_not_inherit_forces(self):
        cycles=parse_cycles(HEADER+CYCLE+PROPOSAL+'Self-consistent Calculation\niteration # 1\n')['cycles']
        self.assertEqual(len(cycles),2)
        self.assertIsNone(cycles[-1]['forces'])
        self.assertIsNone(cycles[-1]['last_energy_ry'])
        self.assertEqual(cycles[-1]['status'],'INCOMPLETE')

    def test_source_line_numbers_survive_appended_runs(self):
        prefix=HEADER+CYCLE+'JOB DONE.\n\n'
        records=parse_cycles(prefix+HEADER+CYCLE)
        line=records['cycles'][0]['evidence']['energy']
        self.assertEqual((prefix+HEADER+CYCLE).splitlines()[line-1],'! total energy = -10.0 Ry')
        self.assertEqual(records['run_count'],2)
        self.assertEqual(len(records['cycles']),1)

    def test_final_coordinates_remain_separate(self):
        r=parse_cycles(HEADER+CYCLE+'Begin final coordinates\n'+PROPOSAL+'End final coordinates\n')
        self.assertEqual(r['final_geometry']['atoms'][0]['coordinates'],[0.1,0.0,0.0])
        self.assertEqual(r['cycles'][0]['geometry_at_start']['atoms'][0]['coordinates'],[0.0,0.0,0.0])

    def test_updated_threshold_belongs_to_next_cycle(self):
        cycles=parse_cycles(HEADER+CYCLE+'new conv_thr = 1.0D-10 Ry\n'+CYCLE,1e-6)['cycles']
        self.assertEqual(cycles[0]['active_conv_thr_ry'],1e-6)
        self.assertEqual(cycles[1]['active_conv_thr_ry'],1e-10)


class ReportsAndExchange(unittest.TestCase):
    def report(self,text,input_text=None,steps=False):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'generic.out');p.write_text(text)
            inp=None
            if input_text is not None:
                inp=Path(tmp,'test.in');inp.write_text(input_text)
            return analyze(p,inp,steps)

    def test_explicit_threshold_is_compared(self):
        r=self.report(HEADER+CYCLE+'JOB DONE.\n',"&CONTROL calculation='scf' /\n&ELECTRONS conv_thr=1d-9 /")
        self.assertEqual(r['threshold_check']['requested_conv_thr_ry'],1e-9)
        self.assertEqual(r['threshold_check']['comparison'],'ABOVE INPUT TARGET (review)')
        self.assertTrue(any('exceeds' in w for w in r['warnings']))

    def test_default_requires_a_supplied_input(self):
        r=self.report(HEADER+CYCLE+'JOB DONE.\n')
        self.assertIsNone(r['threshold_check']['requested_conv_thr_ry'])
        r=self.report(HEADER+CYCLE+'JOB DONE.\n','&CONTROL /\n&ELECTRONS /')
        self.assertEqual(r['threshold_check']['requested_conv_thr_ry'],1e-6)

    def test_invalid_threshold_is_unknown(self):
        for value in ('nan','-1d-8','variable'):
            r=self.report(HEADER+CYCLE+'JOB DONE.\n',f'&CONTROL /\n&ELECTRONS conv_thr={value} /')
            self.assertIsNone(r['threshold_check']['requested_conv_thr_ry'])

    def test_nscf_not_evaluated_as_scf(self):
        r=self.report(HEADER+'Band Structure Calculation\nJOB DONE.\n',"&CONTROL calculation='nscf' /")
        self.assertEqual(r['electronic'],'N/A (non-self-consistent)')
        self.assertIn('NOT ASSESSED',r['status'])
        self.assertIn('N/A',r['threshold_check']['comparison'])

    def test_postprocessor_execution_does_not_certify_science(self):
        r=self.report('Program PHONON v.7.5\nJOB DONE.\n')
        self.assertEqual(r['execution'],'COMPLETED')
        self.assertEqual(r['status'],'NOT ASSESSED')
        self.assertEqual(r['program'],'PHONON')

    def test_inner_scf_does_not_prove_exx(self):
        text=HEADER+'Calculation (EXX) restarted from iteration # 1\n'+CYCLE+'JOB DONE.\n'
        r=self.report(text)
        self.assertEqual(r['exchange_convergence'],'UNPROVEN')
        self.assertEqual(r['status'],'EXX UNPROVEN')

    def test_exx_success_and_failure_markers(self):
        text=HEADER+'Calculation (EXX) restarted from iteration # 1\n'+CYCLE
        self.assertEqual(cli.summary_status(text+'EXX self-consistency reached\nJOB DONE.\n','SCF'),'CONVERGED')
        self.assertEqual(cli.summary_status(text+'EXX convergence NOT achieved after 2 iterations: stopping\nJOB DONE.\n','SCF'),'EXX NOT CONV')

    def test_last_force_and_current_cycle_are_distinct(self):
        r=self.report(HEADER+CYCLE+PROPOSAL+'Self-consistent Calculation\niteration # 1\n',steps=True)
        self.assertEqual(r['current_cycle']['cycle_id'],2)
        self.assertIsNone(r['current_cycle']['max_force_norm_ry_bohr'])
        self.assertEqual(r['last_force_cycle_id'],1)
        self.assertEqual(r['last_completed_cycle']['cycle_id'],1)
        self.assertEqual(r['last_energy_cycle_id'],1)

    def test_failed_ended_cycle_is_not_last_converged_cycle(self):
        text = HEADER+CYCLE+'Self-consistent Calculation\niteration # 1\nconvergence NOT achieved\n'
        r = self.report(text)
        self.assertEqual(r['last_ended_cycle']['cycle_id'], 2)
        self.assertEqual(r['last_ended_cycle']['electronic_outcome'], 'NOT CONVERGED')
        self.assertEqual(r['last_converged_cycle']['cycle_id'], 1)
        self.assertEqual(r['ended_electronic_cycles'], 2)
        self.assertEqual(r['converged_electronic_cycles'], 1)
        self.assertEqual(r['last_completed_cycle']['cycle_id'], 1)

    def test_runtime_error_retains_electronic_outcome(self):
        r = self.report(HEADER+CYCLE+'Error in routine test\n')
        self.assertEqual(r['last_converged_cycle']['status'], 'ERROR')
        self.assertEqual(r['last_converged_cycle']['electronic_outcome'], 'CONVERGED')
        self.assertEqual(r['execution'], 'ERROR')
        self.assertEqual(r['status'], 'ERROR')
        self.assertEqual(r['converged_electronic_cycles'], 1)
        self.assertEqual(r['completed_scf_cycles'], 0)

    def test_unfinished_cycle_has_no_convergence_outcome(self):
        r = self.report(HEADER+'Self-consistent Calculation\niteration # 1\n')
        self.assertIsNone(r['last_ended_cycle'])
        self.assertIsNone(r['last_converged_cycle'])
        self.assertIsNone(r['current_cycle']['electronic_outcome'])
        self.assertEqual(r['ended_electronic_cycles'], 0)

    def test_force_summary_keeps_norm_component_and_total_distinct(self):
        r = self.report(HEADER+CYCLE)
        f = r['last_force_record']
        self.assertEqual(f['max_norm_ry_bohr'], 0.005)
        self.assertEqual(f['max_abs_component_ry_bohr'], 0.004)
        self.assertEqual(f['max_norm_atom_ids'], [1])
        self.assertEqual(r['last_total_force_ry_bohr'], 0.005385)
        self.assertTrue(f['complete'])
        self.assertEqual(r['last_stress_record']['pressure_kbar'], -2.0)
        self.assertEqual(r['last_stress_record']['tensor_kbar'][1][1], -2.0)

    def test_partial_forces_are_labeled_observed_only(self):
        r = self.report(HEADER+CYCLE.replace('atom 2 type 1 force = 0.0 -0.002 0.0\n', ''))
        f = r['last_force_record']
        self.assertFalse(f['complete'])
        self.assertEqual(f['scope'], 'observed atoms only')
        self.assertEqual(f['observed_atoms'], 1)
        self.assertEqual(f['expected_atoms'], 2)

    def test_force_and_stress_display_identify_earlier_cycle(self):
        from qetools.commands import show
        r = self.report(HEADER+CYCLE+'Self-consistent Calculation\niteration # 1\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            show(r)
        self.assertIn('Largest atomic force norm: 0.005', out.getvalue())
        self.assertIn('forces belong to an earlier', out.getvalue())
        self.assertIn('stress belongs to an earlier', out.getvalue())
        self.assertIsNone(r['current_cycle']['pressure_kbar'])
        self.assertEqual(r['last_force_record']['cycle_id'], 1)
        self.assertEqual(r['last_stress_record']['cycle_id'], 1)

    def test_hashes_and_schema_are_recorded(self):
        text=HEADER+CYCLE+'JOB DONE.\n'
        r=self.report(text)
        self.assertEqual(r['provenance']['output_sha256'],hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual(r['schema_version'],'1.1')
        self.assertIn('inputs',r['provenance']['source_sha256'])

    def test_input_directory_association_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'run.in');p.write_text('&CONTROL /')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['scf',tmp,'--input',str(p)])

    def test_inspect_and_steps_are_available_through_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'run.out');p.write_text(HEADER+CYCLE)
            buf=io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(['inspect',str(p),'--steps','--json'])
            r=json.loads(buf.getvalue())
            self.assertEqual(len(r['cycle_records']['cycles']),1)

    def test_appended_postprocessor_does_not_inherit_scf_identity(self):
        r=self.report(HEADER+CYCLE+'JOB DONE.\nProgram PHONON v.7.5\n')
        self.assertEqual(r['program'],'PHONON')
        self.assertEqual(r['execution'],'UNTERMINATED')
        self.assertIsNone(r['last_energy_ry'])

    def test_input_verbose_report_keeps_declared_type(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'nscf.out');p.write_text(HEADER+CYCLE+'JOB DONE.\n')
            inp=Path(tmp,'run.in');inp.write_text("&CONTROL calculation='scf' /\n&ELECTRONS conv_thr=1d-6 /")
            buf=io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(['scf',str(p),'--input',str(inp),'--verbose'])
            self.assertIn('QETools | SCF',buf.getvalue())
            self.assertIn('Input SCF target:',buf.getvalue())

    def test_history_does_not_certify_relaxation_with_unproven_exx(self):
        text=HEADER+'BFGS Geometry Optimization\nCalculation (EXX) restarted from iteration # 1\n'+CYCLE+'bfgs converged in 1 scf cycles and 1 bfgs steps\nJOB DONE.\n'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'relax.out');p.write_text(text)
            b=io.StringIO()
            with contextlib.redirect_stdout(b):main(['history',str(p)])
            self.assertIn('EXX UNPROVEN',b.getvalue())

    def test_verbose_relaxation_does_not_certify_unproven_exx(self):
        text=HEADER+'BFGS Geometry Optimization\nCalculation (EXX) restarted from iteration # 1\n'+CYCLE+'bfgs converged in 1 scf cycles and 1 bfgs steps\nJOB DONE.\n'
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'relax.out');p.write_text(text)
            b=io.StringIO()
            with contextlib.redirect_stdout(b):main(['relax',str(p),'--verbose'])
            self.assertNotIn('RELAXATION CONVERGED —',b.getvalue())

if __name__=='__main__':
    unittest.main()
