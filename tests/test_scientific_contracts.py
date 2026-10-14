"""Behavioral contracts from QE output meaning, not implementation details."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from qetools import cli as candidate
from qetools.commands import main

BASELINE = '0a7bed5a8af0406002a4d57c1d423ac0d8a0073d'
SCF = '''Program PWSCF v.7.5
iteration # 1
estimated scf accuracy < 1.0D-03 Ry
total energy = -10.0D+00 Ry
iteration # 2
estimated scf accuracy < 1.0D-08 Ry
! total energy = -10.1D+00 Ry
convergence has been achieved in 2 iterations
JOB DONE.
'''
INCOMPLETE = '''Program PWSCF v.7.5
iteration # 1
estimated scf accuracy < 1.0D-05 Ry
'''
STOP = INCOMPLETE + 'Maximum CPU time exceeded\nCalculation stopped in scf loop at iteration # 1\nJOB DONE.\n'


def load_baseline():
    with tempfile.TemporaryDirectory() as tmp:
        p = Path(tmp, 'baseline.py')
        p.write_bytes(subprocess.check_output(['git', 'show', BASELINE+':qetools/cli.py']))
        spec = importlib.util.spec_from_file_location('qetools_published_baseline', p)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


CORE = load_baseline() if os.environ.get('QETOOLS_TEST_BASELINE') == '1' else candidate


class ScientificContracts(unittest.TestCase):
    def test_scf_completion_requires_job_done(self):
        self.assertNotEqual(CORE.summary_status(SCF.replace('JOB DONE.', ''), 'SCF'), 'CONVERGED')

    def test_small_residual_without_marker_is_not_convergence(self):
        text = INCOMPLETE.replace('1.0D-05', '1.0D-14')+'JOB DONE.\n'
        self.assertNotEqual(CORE.summary_status(text, 'SCF'), 'CONVERGED')

    def test_wall_stop_takes_priority_over_job_done(self):
        self.assertEqual(CORE.summary_status(STOP, 'SCF'), 'STOPPED')

    def test_runtime_error_takes_priority_over_success_markers(self):
        self.assertEqual(CORE.summary_status(SCF+'Error in routine electrons\n', 'SCF'), 'ERROR')

    def test_explicit_nonconvergence(self):
        text = INCOMPLETE+'convergence NOT achieved after 1 iterations: stopping\nJOB DONE.\n'
        self.assertEqual(CORE.summary_status(text, 'SCF'), 'NOT CONV')

    def test_appended_run_has_its_own_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'scf.out'); p.write_text(SCF+INCOMPLETE)
            self.assertEqual(CORE.output_core(CORE.read(p))['energies'], [])

    def test_appended_run_does_not_inherit_runtime_error(self):
        text = 'Program PWSCF v.7.5\nError in routine old_failure\n'+SCF
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'scf.out'); p.write_text(text)
            self.assertEqual(CORE.summary_status(CORE.read(p), 'SCF'), 'CONVERGED')

    def test_fortran_exponents_and_units(self):
        values = CORE.output_core(SCF)
        self.assertAlmostEqual(values['energies'][-1], -10.1)
        self.assertAlmostEqual(values['acc'][-1], 1e-8)
        self.assertEqual(values['iters'], [1, 2])

    def test_spin_down_negative_density_is_not_lost(self):
        text = SCF+'negative rho (up, down): 0.000E+00 2.000E-02\n'
        self.assertEqual(max(CORE.output_core(text)['neg']), 0.02)

    def test_end_bfgs_step_limit_is_not_ionic_convergence(self):
        text = SCF+'The maximum number of steps has been reached.\nEnd of BFGS Geometry Optimization\n'
        self.assertNotEqual(CORE.ionic_status(text, 'RELAX'), 'CONVERGED')

    def test_relaxation_requires_final_electronic_convergence(self):
        text = SCF+'bfgs converged in 2 scf cycles and 1 bfgs steps\niteration # 1\n'
        self.assertNotEqual(CORE.summary_status(text, 'RELAX'), 'RELAX CONV')

    def test_newest_and_best_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, latest = Path(tmp,'good.out'), Path(tmp,'latest.out')
            good.write_text(SCF); latest.write_text(STOP)
            os.utime(good,(100,100)); os.utime(latest,(200,200))
            self.assertEqual(CORE.select_scf_file(tmp,'latest').name,'latest.out')
            self.assertEqual(CORE.select_scf_file(tmp,'best').name,'good.out')

    def test_explicit_file_overrides_best(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'exact.out'); p.write_text(STOP)
            self.assertEqual(CORE.select_scf_file(p,'best'),p)

    def test_best_prefers_convergence_over_tiny_incomplete_residual(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'good.out').write_text(SCF)
            Path(tmp,'unfinished.out').write_text(INCOMPLETE.replace('1.0D-05','1.0D-15'))
            self.assertEqual(CORE.select_scf_file(tmp,'best').name,'good.out')

    def test_best_uses_final_not_transient_minimum(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'transient.out').write_text(SCF.replace('1.0D-03','1.0D-15').replace('1.0D-08','1.0D-05'))
            Path(tmp,'steady.out').write_text(SCF)
            self.assertEqual(CORE.select_scf_file(tmp,'best').name,'steady.out')

    def test_best_equal_residual_uses_newest_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp,'a.out'),Path(tmp,'b.out')
            a.write_text(SCF);b.write_text(SCF)
            os.utime(a,(100,100));os.utime(b,(200,200))
            self.assertEqual(CORE.select_scf_file(tmp,'best').name,'b.out')

    def test_best_with_no_success_does_not_claim_convergence(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'a.out').write_text(STOP)
            p=CORE.select_scf_file(tmp,'best')
            self.assertNotEqual(CORE.summary_status(CORE.read(p),'SCF'),'CONVERGED')

    def test_relaxation_excluded_from_scf_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'good.out').write_text(SCF)
            Path(tmp,'relax.out').write_text(SCF+'BFGS Geometry Optimization\n')
            self.assertEqual([p.name for p in CORE.scf_candidates(tmp)],['good.out'])

    def test_inline_input_settings(self):
        settings=CORE.parse_qe_input_settings("&CONTROL calculation='scf', prefix='test' /\n&SYSTEM nat=2, ntyp=1 /")
        self.assertEqual(settings.get('calculation'),'scf')
        self.assertEqual(settings.get('ntyp'),'1')


class InterfaceContracts(unittest.TestCase):
    def report(self,args):
        b=io.StringIO()
        with contextlib.redirect_stdout(b):
            main(args)
        return json.loads(b.getvalue())

    def test_json_and_text_use_same_selected_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'scf.out');p.write_text(SCF)
            r=self.report(['scf',tmp,'--best','--json'])
            b=io.StringIO()
            with contextlib.redirect_stdout(b):
                main(['scf',tmp,'--best'])
            self.assertIn(r['file'],b.getvalue())
            self.assertIn(r['status'],b.getvalue())

    def test_list_returns_all_scf_candidates(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'one.out').write_text(SCF)
            Path(tmp,'two.out').write_text(STOP)
            Path(tmp,'relax.out').write_text(SCF+'BFGS Geometry Optimization\n')
            reports=self.report(['scf',tmp,'--list','--json'])
            self.assertEqual({Path(r['file']).name for r in reports},{'one.out','two.out'})

    def test_latest_and_best_explain_different_selections(self):
        with tempfile.TemporaryDirectory() as tmp:
            good, latest=Path(tmp,'good.out'),Path(tmp,'latest.out')
            good.write_text(SCF);latest.write_text(STOP)
            os.utime(good,(100,100));os.utime(latest,(200,200))
            default=self.report(['scf',tmp,'--json'])
            best=self.report(['scf',tmp,'--best','--json'])
            self.assertEqual(Path(default['file']).name,'latest.out')
            self.assertEqual(Path(best['file']).name,'good.out')
            self.assertEqual(default['selection_mode'],'latest')
            self.assertEqual(best['selection_mode'],'best')

    def test_best_fallback_is_explicit_when_no_success_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'a.out').write_text(STOP)
            report=self.report(['scf',tmp,'--best','--json'])
            self.assertIn('no completed converged candidate', report['selection_reason'])
            self.assertTrue(any('not a final converged result' in w for w in report['warnings']))

    def test_zero_negative_density_does_not_create_warning(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp,'scf.out');p.write_text(SCF+'negative rho (up, down): 0.000E+00 0.000E+00\n')
            report=self.report(['scf',str(p),'--json'])
            self.assertFalse(any('Negative charge' in w for w in report['warnings']))

    def test_history_best_agrees_with_scf_best(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'transient.out').write_text(SCF.replace('1.0D-03','1.0D-15').replace('1.0D-08','1.0D-05'))
            Path(tmp,'steady.out').write_text(SCF)
            selected=self.report(['scf',tmp,'--best','--json'])
            buf=io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(['history',tmp])
            match=next(line for line in buf.getvalue().splitlines() if line.startswith('Best converged SCF'))
            self.assertIn(Path(selected['file']).name,match)

    def test_identical_residual_and_timestamp_use_filename_tie_break(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('b.out','a.out'):
                p=Path(tmp,name);p.write_text(SCF);os.utime(p,(100,100))
            self.assertEqual(Path(self.report(['scf',tmp,'--best','--json'])['file']).name,'a.out')
            self.assertEqual(Path(self.report(['scf',tmp,'--json'])['file']).name,'a.out')

    def test_generic_non_scf_log_is_excluded_from_scf_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp,'good.out').write_text(SCF)
            Path(tmp,'generic.out').write_text('Program PWSCF v.7.5\nnumber of Kohn-Sham states=4\nBand Structure Calculation\nEnd of band structure calculation\nJOB DONE.\n')
            reports=self.report(['scf',tmp,'--list','--json'])
            self.assertEqual([Path(r['file']).name for r in reports],['good.out'])

if __name__=='__main__':
    unittest.main()
