"""Literal history expectations for completed and interrupted electronic cycles."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from qetools.commands import main

HEADER = 'Program PWSCF v.7.5\n'
CYCLE = 'iteration # 1\nestimated scf accuracy < 1.0e-6 Ry\nconvergence has been achieved in 1 iterations\n'

class HistoryTests(unittest.TestCase):
    def report(self, path):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(['history', str(path)])
        return out.getvalue()

    def row(self, text):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'scf.out'); p.write_text(text); os.utime(p, (0, 0))
            report = self.report(p)
            return next(x for x in report.splitlines() if x.startswith('1970-')), report

    def test_unfinished_cycle_has_no_ended_cycle(self):
        row, report = self.row(HEADER + 'iteration # 1\n')
        self.assertIn('NONE', row)
        self.assertIn('INCOMPLETE', row)
        self.assertIn('—', row)
        self.assertIn('\nQE CALCULATION HISTORY\n', report)
        self.assertIn('1970-01-01 00:00:00', row)
        self.assertNotIn('RESEARCH-GRADE', report)

    def test_failed_cycle_is_last_ended_even_after_success(self):
        row, _ = self.row(HEADER + CYCLE + 'iteration # 1\nconvergence NOT achieved\n')
        self.assertEqual(row.count('NOT CONVERGED'), 2)

    def test_earlier_success_and_unfinished_latest_are_separate(self):
        row, report = self.row(HEADER + CYCLE + 'iteration # 1\n')
        self.assertIn('CONVERGED', row)
        self.assertIn('INCOMPLETE', row)
        self.assertIn('LAST EST may belong to an earlier cycle', report)

    def test_zero_estimate_is_not_missing(self):
        row, _ = self.row(HEADER + 'iteration # 1\nestimated scf accuracy < 0 Ry\n')
        self.assertEqual(row.count('0.000e+00'), 3)

    def test_aliases_forward_path_with_spaces_and_help(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='qe history ') as tmp:
            p = Path(tmp, 'scf test.out'); p.write_text(HEADER + CYCLE + 'JOB DONE.\n')
            inp = Path(tmp, 'scf input.in'); inp.write_text("&CONTROL calculation='scf' /\n")
            for args in ([str(p)], [tmp], ['--help'], [str(p), '--input', str(inp)], [str(p), '--input', str(inp), '--json']):
                results = [subprocess.run(['sh', str(root/'bin'/name), *args], capture_output=True, text=True, check=True).stdout for name in ('qehis', 'qehistory')]
                self.assertEqual(results[0], results[1])

    def test_timestamp_ties_are_sorted_by_filename(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ('b.out', 'a.out'):
                p = Path(tmp, name); p.write_text(HEADER + CYCLE); os.utime(p, (0, 0))
            rows = [x for x in self.report(tmp).splitlines() if x.startswith('1970-')]
            self.assertTrue(rows[0].endswith('a.out'))
            self.assertTrue(rows[1].endswith('b.out'))

    def test_estimate_does_not_leak_into_unfinished_new_cycle(self):
        from qetools.history import evidence
        details = evidence(HEADER + CYCLE + 'iteration # 1\n')
        self.assertEqual(details['last_estimate']['cycle_id'], 1)
        self.assertEqual(details['latest_cycle_id'], 2)
        self.assertIsNone(details['latest_cycle_estimate'])

    def test_error_does_not_erase_previous_convergence_outcome(self):
        from qetools.history import evidence
        details = evidence(HEADER + CYCLE + 'Error in routine test\n')
        self.assertEqual(details['last_ended_cycle']['status'], 'CONVERGED')
        self.assertEqual(details['latest_cycle_status'], 'ERROR')

    def test_appended_runs_preserve_original_evidence_lines(self):
        from qetools.history import evidence
        raw = HEADER + CYCLE + HEADER + 'iteration # 1\nestimated scf accuracy < 2e-5 Ry\n'
        details = evidence(raw)
        self.assertEqual(details['minimum_estimate']['value_ry'], 2e-5)
        self.assertEqual(details['last_estimate']['source_line'], len(raw.splitlines()))
        self.assertIsNone(details['last_ended_cycle'])

    def test_runtime_error_precedes_unproven_exchange(self):
        from qetools.history import overall_status
        text = HEADER + CYCLE + 'Fock energy (test)\nError in routine test\n'
        self.assertEqual(overall_status(text, 'RELAX'), 'ERROR')

    def test_stop_prevents_success_with_completed_cycle(self):
        from qetools.history import overall_status
        for kind in ('SCF', 'RELAX', 'NSCF', 'UNKNOWN'):
            self.assertIn(overall_status(HEADER + CYCLE + 'Calculation stopped\nJOB DONE.\n', kind), ('STOPPED', 'RELAX STOP'))

    def test_unassessed_types_cannot_inherit_scf_success(self):
        from qetools.history import overall_status
        for kind in ('NSCF', 'BANDS', 'MD', 'VC-MD', 'PHONON', 'UNKNOWN'):
            self.assertEqual(overall_status(HEADER + CYCLE + 'JOB DONE.\n', kind), 'NOT ASSESSED')

    def test_ionic_completion_requires_normal_execution_and_latest_convergence(self):
        from qetools.history import overall_status
        text = HEADER + CYCLE + 'bfgs converged\n'
        self.assertEqual(overall_status(text, 'RELAX'), 'UNTERMINATED')
        self.assertEqual(overall_status(text + 'JOB DONE.\n', 'RELAX'), 'RELAX CONV')
        self.assertNotEqual(overall_status(text + 'iteration # 1\nJOB DONE.\n', 'RELAX'), 'RELAX CONV')

    def test_history_exposes_filename_classification_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'relax.out'); p.write_text(HEADER + CYCLE)
            self.assertIn('filename fallback', self.report(p))

    def test_explicit_input_and_json_provenance(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'scheduler.out'); p.write_text(HEADER + CYCLE + 'JOB DONE.\n')
            inp = Path(tmp, 'calculation.in'); inp.write_text("&CONTROL calculation='nscf' /\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(['history', str(p), '--input', str(inp), '--json'])
            row = json.loads(out.getvalue())['records'][0]
            self.assertEqual(row['report']['calculation'], 'NSCF')
            self.assertEqual(row['history_status'], 'NOT ASSESSED')
            self.assertEqual(row['history_evidence']['last_estimate']['source_line'], 3)
            self.assertEqual(row['report']['provenance']['input']['file'], str(inp.resolve()))
            self.assertTrue(row['modified_utc'].endswith('+00:00'))

    def test_input_directory_association_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                main(['history', tmp, '--input', str(Path(tmp, 'input.in'))])

    def test_declared_input_conflict_remains_unassessed(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'relax.out'); p.write_text(HEADER + CYCLE + 'bfgs converged\nJOB DONE.\n')
            inp = Path(tmp, 'scf.in'); inp.write_text("&CONTROL calculation='scf' /\n")
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(['history', str(p), '--input', str(inp), '--json'])
            row = json.loads(out.getvalue())['records'][0]
            self.assertEqual(row['report']['calculation'], 'UNKNOWN')
            self.assertEqual(row['history_status'], 'NOT ASSESSED')
            self.assertIn('conflict', row['report']['classification_source'])

    def test_non_pwscf_text_cannot_supply_scf_estimates(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'phonon.out'); p.write_text('Program PHONON v.7.5\n' + CYCLE + 'JOB DONE.\n')
            report = self.report(p)
            row = next(x for x in report.splitlines() if x.endswith('phonon.out') and 'PHONON' in x)
            self.assertNotIn('1.000e-06', row)
            self.assertIn('NOT ASSESSED', row)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                main(['history', str(p), '--json'])
            self.assertIsNone(json.loads(out.getvalue())['records'][0]['history_evidence'])

    def test_unrecognized_explicit_file_is_rejected_in_both_formats(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'notes.out'); p.write_text('not a calculation\n')
            for extra in ([], ['--json']):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                    main(['history', str(p), *extra])

    def test_table_and_json_share_failed_and_incomplete_states(self):
        import json
        cases = [HEADER + CYCLE + 'Error in routine test\n',
                 HEADER + CYCLE + 'iteration # 1\n',
                 HEADER + CYCLE + 'Calculation stopped\nJOB DONE.\n']
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp, 'scf.out')
            for raw in cases:
                p.write_text(raw)
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    main(['history', str(p), '--json'])
                data = json.loads(out.getvalue())['records'][0]
                row = next(x for x in self.report(p).splitlines() if x.endswith('scf.out') and not x.startswith(' '))
                self.assertIn(data['history_status'], row)
                self.assertIn(data['history_evidence']['latest_cycle_status'], row)
                ended = data['history_evidence']['last_ended_cycle']
                self.assertIn(ended['status'] if ended else 'NONE', row)
