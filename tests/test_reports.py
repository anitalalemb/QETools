import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from qetools import cli
from qetools.commands import analyze, main

ROOT = Path(__file__).resolve().parents[1]
CONVERGED = (ROOT / 'tests/sample_converged.out').read_text()
STOPPED = (ROOT / 'tests/sample_stopped.out').read_text()

class Reports(unittest.TestCase):
    def test_stopped_job_done_is_not_convergence(self):
        r = analyze(ROOT / 'tests/sample_stopped.out')
        self.assertEqual(r['execution'], 'STOPPED')
        self.assertEqual(r['status'], 'STOPPED')

    def test_appended_run_does_not_inherit_success_or_errors(self):
        for old in (CONVERGED, CONVERGED + '\nError in routine old_error\n'):
            self.assertEqual(cli.summary_status(old + '\n' + STOPPED, 'SCF'), 'STOPPED')
            self.assertEqual(cli.output_core(old + '\n' + STOPPED)['energies'], [])
        self.assertEqual(cli.summary_status(STOPPED + '\n' + CONVERGED, 'SCF'), 'CONVERGED')

    def test_scf_requires_completion_as_well_as_convergence(self):
        self.assertNotEqual(cli.summary_status(CONVERGED.replace('JOB DONE.', ''), 'SCF'), 'CONVERGED')

    def test_relaxation_end_marker_does_not_prove_convergence(self):
        text = CONVERGED + '\nEnd of BFGS Geometry Optimization\n'
        self.assertEqual(cli.ionic_status(text, 'RELAX'), 'UNPROVEN')
        self.assertNotEqual(cli.summary_status(text, 'RELAX'), 'RELAX CONV')

    def test_final_electronic_cycle_required_for_relaxation(self):
        text = CONVERGED + '\nbfgs converged in 2 scf cycles and 1 bfgs steps\n'
        self.assertEqual(cli.summary_status(text, 'RELAX'), 'RELAX CONV')
        text += '\niteration # 1\n'
        self.assertNotEqual(cli.summary_status(text, 'RELAX'), 'RELAX CONV')

    def test_inline_settings_and_commented_values(self):
        settings = cli.parse_qe_input_settings("&control calculation='scf', prefix='a!b', outdir='/tmp/qe' ! prefix='wrong'\n/\n&system nat=2, ntyp=1, ecutwfc=40 /\n! nat=99")
        self.assertEqual(settings['nat'], '2')
        self.assertEqual(settings['ntyp'], '1')
        self.assertEqual(settings['outdir'], '/tmp/qe')
        self.assertEqual(settings['prefix'], 'a!b')

    def test_json_and_file_discovery(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'calc.log').write_text(CONVERGED)
            Path(tmp, 'noise.log').write_text('not a QE log')
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(['scf', tmp, '--json'])
            r = json.loads(buf.getvalue())
            self.assertEqual(r['status'], 'CONVERGED')
            self.assertTrue(r['file'].endswith('calc.log'))

    def test_best_uses_final_residual_not_historical_minimum(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, 'a.out').write_text(CONVERGED.replace('1.0E-03', '1.0E-12').replace('1.0E-08', '1.0E-05'))
            Path(tmp, 'b.out').write_text(CONVERGED)
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                main(['scf', tmp, '--best', '--json'])
            self.assertTrue(json.loads(buf.getvalue())['file'].endswith('b.out'))

    def test_wrapper_preserves_options_from_other_directory(self):
        result = subprocess.run(['sh', str(ROOT/'bin/qescf'), str(ROOT/'tests/sample_converged.out'), '--json'], cwd='/tmp', text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'CONVERGED')

    def test_local_and_mpi_launches_do_not_require_slurm_modules(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'H.UPF').write_text('fixture only')
            (root/'calc.in').write_text("&control /\n&system ibrav=1, nat=1, ntyp=1, ecutwfc=40 /\n&electrons /\nATOMIC_SPECIES\nH 1.0 H.UPF\nATOMIC_POSITIONS angstrom\nH 0 0 0\nK_POINTS gamma\n")
            (root/'calc.out').write_text(CONVERGED.replace('= 100', '= 1'))
            for launch in (None, 'pw.x -in calc.in > calc.out', 'mpirun -np 2 pw.x -in calc.in > calc.out', 'mpiexec -n 2 /opt/qe/pw.x < calc.in > calc.out', 'srun -n 2 pw.x -in calc.in > calc.out'):
                script = root/'run.sh'
                if launch is not None:
                    script.write_text(launch)
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    main(['check', tmp, '--verbose'])
                output = buf.getvalue()
                self.assertIn('Total failures                : 0', output)
                self.assertNotIn('no module', output)
                self.assertNotIn("no 'module reset'", output)
                self.assertNotIn('no srun', output)
                self.assertNotIn('calculation is missing', output)

    def test_relaxation_history_compare_and_verbose_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp, 'relax.out')
            file.write_text(CONVERGED + '\nbfgs converged in 2 scf cycles and 1 bfgs steps\n')
            for args in (['history', tmp], ['relax', str(file), '--verbose'], ['compare', str(file), str(file)]):
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    main(args)
                self.assertIn('CONV', buf.getvalue())

if __name__ == '__main__':
    unittest.main()
