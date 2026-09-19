import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from audit.ic_weekly_producer import build_report, save_report, SOURCES
from audit.ic_weekly_control import validate

START = '2026-09-12T00:00:00Z'
CUT = '2026-09-19T14:00:00Z'
AT = '2026-09-20T00:00:00Z'


class ProducerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name) / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('config', 'user.email', 'fixture@example.invalid')
        self.git('config', 'user.name', 'Synthetic Fixture')
        self.rows = {}
        for key, (path, field, status, allowed) in SOURCES.items():
            row = {field: '2026-09-19T07:45:00Z'} if field else {}
            if status:
                row[status] = sorted(allowed)[0]
            row.update(holdings='PRIVATE_SENTINEL', quantity=987654321,
                       cash=123456789, pnl='SECRET_SENTINEL')
            self.rows[key] = row
            self.write(key, row)
        self.commit()

    def git(self, *args):
        env = dict(os.environ, GIT_AUTHOR_DATE='2026-09-19T08:00:00Z',
                   GIT_COMMITTER_DATE='2026-09-19T08:00:00Z')
        return subprocess.check_output(['git', '-C', str(self.repo), *args], env=env,
                                       stderr=subprocess.DEVNULL).decode().strip()

    def write(self, key, row):
        p = self.repo / SOURCES[key][0]
        p.parent.mkdir(exist_ok=True, parents=True)
        p.write_text(json.dumps(row))

    def commit(self):
        self.git('add', '.')
        self.git('commit', '-qm', 'synthetic snapshot')
        self.sha = self.git('rev-parse', 'HEAD')

    def report(self, **kw):
        args = dict(repo=self.repo, commit=self.sha, start=START, cutoff=CUT, generated_at=AT)
        args.update(kw)
        return build_report(**args)

    def test_public_projection_never_copies_financial_fields(self):
        r = self.report(); s = json.dumps(r)
        for v in ('PRIVATE_SENTINEL', 'SECRET_SENTINEL', '987654321', '123456789'):
            self.assertNotIn(v, s)
        self.assertEqual(r['in_period_source_count'], 3)
        self.assertEqual(r['sources'][1]['status'], 'SOURCE_TIME_MISSING')
        self.assertTrue(all(x['source_available_at'] is None for x in r['sources']))
        validate(r['packet'])

    def test_unknown_status_cannot_leak_arbitrary_text(self):
        self.rows['crypto_runtime']['decision_status'] = 'PRIVATE_SENTINEL'
        self.write('crypto_runtime', self.rows['crypto_runtime']); self.commit()
        self.assertIsNone(self.report()['sources'][0]['reported_status'])

    def test_new_evidence_changes_key_no_static_seed(self):
        a = self.report()
        self.rows['crypto_runtime']['decision_status'] = 'BLOCKED'
        self.write('crypto_runtime', self.rows['crypto_runtime']); self.commit()
        b = self.report()
        self.assertNotEqual(a['report_key'], b['report_key'])
        self.assertEqual(b['sources'][0]['reported_status'], 'BLOCKED')

    def test_no_kpi_baseline_invention_or_delivery_claim(self):
        r = self.report()
        for value in r['packet']['kpi_deltas'].values():
            self.assertIsNone(value['delta']); self.assertEqual(value['status'], 'NOT_COMPUTABLE')
        self.assertEqual(r['analysis_status'], 'AWAITING_CIO_ANALYSIS')
        self.assertEqual(r['delivery_status'], 'NOT_DELIVERED')
        self.assertEqual(r['packet']['results']['routing_result'], 'ROUTING_EVIDENCE_MISSING')
        self.assertEqual(r['packet']['natural_probes'], [])
        self.assertFalse(any(r['packet']['authority'].values()))

    def test_missing_source_not_zero_observations(self):
        (self.repo / SOURCES['crypto_runtime'][0]).unlink(); self.commit()
        self.assertEqual(self.report()['sources'][0]['status'], 'MISSING')

    def test_missing_git_commit_is_error_not_missing_source(self):
        with self.assertRaises(subprocess.CalledProcessError):
            self.report(commit='f' * 40)

    def test_dirty_worktree_cannot_change_report(self):
        a = self.report()
        self.write('crypto_runtime', {'decision_status': 'AVAILABLE'})
        self.assertEqual(a, self.report())

    def test_stale_and_future_not_weekly_observations(self):
        for stamp, status in [('2026-09-11T23:59:59Z', 'PRIOR_PERIOD'),
                              ('2026-09-19T14:00:01Z', 'AFTER_CUTOFF')]:
            self.rows['crypto_runtime']['evaluation_at'] = stamp
            self.write('crypto_runtime', self.rows['crypto_runtime']); self.commit()
            r = self.report()
            self.assertEqual(r['sources'][0]['status'], status)
            self.assertEqual(r['in_period_source_count'], 2)

    def test_cutoff_boundary(self):
        self.rows['crypto_runtime']['evaluation_at'] = CUT
        self.write('crypto_runtime', self.rows['crypto_runtime']); self.commit()
        self.assertEqual(self.report()['sources'][0]['status'], 'IN_PERIOD_SNAPSHOT')

    def test_invalid_source_is_explicit_without_error_body(self):
        p = self.repo / SOURCES['crypto_runtime'][0]
        p.write_text('SECRET_SENTINEL'); self.commit()
        r = self.report()
        self.assertEqual(r['sources'][0]['status'], 'INVALID_SOURCE')
        self.assertNotIn('SECRET_SENTINEL', json.dumps(r))

    def test_source_symlink_not_followed(self):
        p = self.repo / SOURCES['crypto_runtime'][0]
        p.unlink(); p.symlink_to('/etc/passwd'); self.commit()
        self.assertEqual(self.report()['sources'][0]['status'], 'INVALID_SOURCE')

    def test_timezone_commit_and_period_guards(self):
        for kw in ({'commit': 'HEAD'}, {'start': CUT}, {'cutoff': '2026-09-18T00:00:00Z'},
                   {'generated_at': '2026-09-18T00:00:00Z'}, {'start': '2026-09-12T00:00:00'}):
            with self.assertRaises(ValueError): self.report(**kw)

    def test_repeat_reuses_original_generation_without_write(self):
        root = Path(self.tmp.name) / 'reports'
        path, status = save_report(self.report(), root)
        before = path.read_bytes(); modified = path.stat().st_mtime_ns
        path2, status2 = save_report(self.report(generated_at='2026-09-21T00:00:00Z'), root)
        self.assertEqual(status2, 'VERIFIED_EXISTING')
        self.assertEqual(path, path2); self.assertEqual(before, path.read_bytes())
        self.assertEqual(modified, path.stat().st_mtime_ns)

    def test_existing_tampered_output_not_overwritten(self):
        root = Path(self.tmp.name) / 'reports'
        path, _ = save_report(self.report(), root)
        data = json.loads(path.read_text()); data['delivery_status'] = 'DELIVERED'
        path.write_text(json.dumps(data)); before = path.read_bytes()
        with self.assertRaises(ValueError): save_report(self.report(), root)
        self.assertEqual(before, path.read_bytes())

    def test_fresh_process_cli(self):
        out = Path(self.tmp.name) / 'cli'
        cmd = [sys.executable, '-m', 'audit.ic_weekly_producer', '--repo', str(self.repo),
               '--source-commit', self.sha, '--period-start', START, '--cutoff', CUT,
               '--output-dir', str(out)]
        a = json.loads(subprocess.check_output(cmd, cwd=ROOT))
        b = json.loads(subprocess.check_output(cmd, cwd=ROOT))
        self.assertEqual(a['sha256'], b['sha256'])
        self.assertEqual(b['status'], 'VERIFIED_EXISTING')

    def test_workflow_separation_and_no_schedule_or_repo_write(self):
        s = (ROOT / '.github/workflows/ic-weekly-producer.yml').read_text()
        self.assertNotIn('  schedule:', s)
        self.assertNotIn('contents: write', s)
        self.assertIn("if: github.event_name == 'workflow_dispatch'", s)
        self.assertIn('python3 -m audit.ic_weekly_producer', s)
        old = (ROOT / '.github/workflows/ic-weekly-control.yml').read_text()
        self.assertNotIn('python3 -m audit.ic_weekly_producer', old)


if __name__ == '__main__':
    unittest.main()
