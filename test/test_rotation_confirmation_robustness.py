"""Rotation confirmation (#752) robustness: per-market build, committed packets preferred, push retry,
no post-session bars in record-only features, byte-identical replay of committed packets."""
from __future__ import annotations

import contextlib
import gzip
import importlib.util
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OL = load_module("rotation_opportunity_ledger_for_robustness", ROOT / "rotation" / "rotation_opportunity_ledger.py")
RC = OL.RC
POLICY = RC.load_policy()
CONFIG = OL.load_config()
US = POLICY["markets"]["US"]["entities"]


def copy_contracts(root: Path) -> None:
    for relative in (
        RC.POLICY_RELATIVE_PATH, OL.CONFIG_RELATIVE_PATH, RC.STATE_MAPPING_RELATIVE_PATH,
        POLICY["ratification_record"]["repo_path"], CONFIG["entry_rule"]["repo_path"],
        POLICY["markets"]["KR"]["source"]["sector_policy_path"],
    ):
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, root / relative)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def us_manifest(root: Path, capture: str, session: str, ranking: list) -> None:
    order = list(ranking) + [s for s in US if s not in ranking]
    etfs = [{"symbol": s, "as_of_session_date": session, "available_session_count": 60,
             "relative_to_spy_pct": {"20_session_pct": str(20 - i)}} for i, s in enumerate(order)]
    write_json(root / "evidence/free_market_data/derived" / capture / "manifest.json",
               {"us_market_reference": {"sector_etfs": etfs, "payload_sha256": "0" * 64}})


def run_cli(module, argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = module.run(argv)
    return code, out.getvalue(), err.getvalue()


class CommittedPacketsPreferredTests(unittest.TestCase):
    def test_selection_rule(self):
        obs = [{"as_of_date": d} for d in ("2026-09-01", "2026-09-02", "2026-09-03", "2026-09-05")]
        kept, late = RC.select_append_observations(obs, set())
        self.assertEqual((len(kept), late), (4, []))  # fresh root: nothing excluded
        kept, late = RC.select_append_observations(obs, {"2026-09-01", "2026-09-03"})
        self.assertEqual(([o["as_of_date"] for o in kept], late), (["2026-09-01", "2026-09-03", "2026-09-05"], ["2026-09-02"]))

    def test_late_older_evidence_does_not_break_append_only_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_contracts(root)
            us_manifest(root, "2026-09-01", "2026-09-01", ["XLK"])
            us_manifest(root, "2026-09-02", "2026-09-02", ["XLK"])
            us_manifest(root, "2026-09-04", "2026-09-04", ["XLE"])
            first = RC.build_market("US", root, POLICY)
            RC.write_market("US", first, root)
            committed = {p.parent.name: p.read_bytes() for p in (root / RC.EVIDENCE_RELATIVE_ROOT / "US").glob("*/packet.json")}
            # older session evidence committed late (09-03); a full replay would change 09-04 and conflict
            us_manifest(root, "2026-09-05", "2026-09-03", ["XLE"])
            full = RC.build_market_packets(POLICY, "US", RC.EXTRACTORS["US"](POLICY, root), RC.load_state_mapping(root))
            with self.assertRaisesRegex(RC.RotationConfirmationError, "APPEND_ONLY_EVIDENCE_CONFLICT"):
                RC.write_market("US", full, root)
            self.assertEqual(RC.late_older_evidence_dates("US", root, POLICY), ["2026-09-03"])
            preferred = RC.build_market("US", root, POLICY)
            self.assertEqual([p["as_of_date"] for p in preferred], ["2026-09-01", "2026-09-02", "2026-09-04"])
            RC.write_market("US", preferred, root)
            self.assertEqual({p.parent.name: p.read_bytes() for p in (root / RC.EVIDENCE_RELATIVE_ROOT / "US").glob("*/packet.json")}, committed)
            self.assertEqual(RC.verify_market("US", preferred, root), [])
            # the exclusion is persisted as a notice file (no wall-clock field, rewritten identically)
            code, _out, err = run_cli(RC, ["build", "--market", "US", "--write", "--no-portal", "--root", str(root)])
            self.assertEqual(code, 0)
            self.assertIn("::warning::late older US evidence not replayed: 2026-09-03", err)
            notice_path = RC.late_evidence_notice_path(root, "US")
            notice = json.loads(notice_path.read_text(encoding="utf-8"))
            self.assertEqual(notice["schema_version"], "rotation_confirmation_late_older_evidence/1")
            self.assertEqual([(i["as_of_date"], i["sources"][0]["path"]) for i in notice["late_older_evidence_not_replayed"]],
                             [("2026-09-03", "evidence/free_market_data/derived/2026-09-05/manifest.json")])
            first_bytes = notice_path.read_bytes()
            run_cli(RC, ["build", "--market", "US", "--write", "--no-portal", "--root", str(root)])
            self.assertEqual(notice_path.read_bytes(), first_bytes)
            self.assertFalse((root / RC.PORTAL_RELATIVE_PATH).exists())  # --no-portal
            # newer evidence still appends on top of the committed chain
            us_manifest(root, "2026-09-08", "2026-09-08", ["XLE"])
            appended = RC.build_market("US", root, POLICY)
            self.assertEqual([p["as_of_date"] for p in appended], ["2026-09-01", "2026-09-02", "2026-09-04", "2026-09-08"])
            self.assertEqual([RC.render_json(p) for p in appended[:3]], list(committed[d] for d in sorted(committed)))

    def test_cli_build_isolates_a_failing_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            copy_contracts(root)
            us_manifest(root, "2026-09-01", "2026-09-01", ["XLK"])
            us_manifest(root, "2026-09-02", "2026-09-02", ["XLK"])
            # a malformed crypto source makes the CRYPTO build fail closed
            write_json(root / "data/observations/crypto_leadership/2026-09-02/packet.json", {"as_of_date": "2026-09-01"})
            code, out, err = run_cli(RC, ["build", "--market", "CRYPTO", "--market", "US", "--market", "KR", "--write", "--root", str(root)])
            self.assertEqual(code, 3)
            self.assertIn("Rotation confirmation failed for CRYPTO: CRYPTO_SOURCE_DATE_MISMATCH", err)
            self.assertTrue((root / RC.EVIDENCE_RELATIVE_ROOT / "US/2026-09-02/packet.json").exists())
            self.assertTrue((root / RC.PORTAL_RELATIVE_PATH).exists())
            self.assertIn('"late_older_evidence_not_replayed": []', out)
            self.assertEqual(run_cli(RC, ["verify", "--market", "US", "--root", str(root)])[0], 0)
            self.assertEqual(run_cli(RC, ["verify", "--market", "CRYPTO", "--root", str(root)])[0], 3)
            self.assertEqual(run_cli(RC, ["portal", "--root", str(root)])[0], 0)
            code, _out, err = run_cli(OL, ["verify", "--market", "US", "--market", "CRYPTO", "--root", str(root)])
            self.assertEqual(code, 3)
            self.assertIn("Opportunity ledger failed for CRYPTO", err)


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.text = (ROOT / ".github/workflows/rotation-confirmation.yml").read_text(encoding="utf-8")
        self.workflow = yaml.safe_load(self.text)
        self.run = "\n".join(step.get("run", "") for step in self.workflow["jobs"]["build"]["steps"])

    def test_per_market_build_and_push_retry(self):
        self.assertIn("for market in US KR CRYPTO; do", self.run)
        for command in ("rotation/rotation_confirmation.py build --market \"$market\" --write --no-portal",
                        "rotation/rotation_confirmation.py verify --market \"$market\"",
                        "rotation/rotation_opportunity_ledger.py build --market \"$market\" --write",
                        "rotation/rotation_opportunity_ledger.py verify --market \"$market\"",
                        "rotation/rotation_confirmation.py portal --write"):
            self.assertIn(command, self.run)
        self.assertIn("for attempt in 1 2 3 4 5; do", self.run)
        self.assertIn("git pull --rebase origin main && git push origin HEAD:main", self.run)
        self.assertIn("git rebase --abort", self.run)
        self.assertLess(self.run.index("git push origin HEAD:main"), self.run.index('if [ -n "$failed" ]'))

    def test_portal_failure_does_not_block_market_commit_and_notices_are_kept(self):
        portal = self.run.index("if python3 rotation/rotation_confirmation.py portal --write; then")
        self.assertIn("portal_failed=1", self.run)
        self.assertLess(portal, self.run.index('git commit -m "data: rotation confirmation'))
        self.assertLess(self.run.index("git push origin HEAD:main"), self.run.index('if [ "$portal_failed" -ne 0 ]'))
        self.assertIn('notice="data/rotation_confirmation_late_older_evidence_$lower.json"', self.run)
        self.assertLess(self.run.index('git add -- "$notice"'), self.run.index('git commit -m "data: rotation confirmation'))
        # the per-market clean-up of a failed market never touches the notice file
        loop = self.run[self.run.index("for market in US KR CRYPTO; do"):self.run.index("done")]
        self.assertNotIn("late_older_evidence", loop)

    def test_no_cron_no_secret_and_pinned_sources_untouched(self):
        triggers = self.workflow.get("on", self.workflow.get(True))
        self.assertNotIn("schedule", triggers)
        self.assertNotIn("secrets.", self.text)
        for name in ("free-market-data.yml", "crypto-breadth-capture.yml", "korea-leadership-live-proof.yml"):
            self.assertNotIn("rotation_confirmation", (ROOT / ".github/workflows" / name).read_text(encoding="utf-8"))


class PostSessionBarLeakTests(unittest.TestCase):
    """Record-only features for session d never read a bar dated after d, even from the same capture file."""

    def _capture(self, root: Path, capture: str, sessions: list, extreme_after: bool):
        responses = {}
        for symbol in US:
            bars = [{"t": f"{d}T04:00:00Z", "o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100 + i, "v": 1}
                    for i, d in enumerate(sessions)]
            if extreme_after:
                bars.append({"t": "2026-09-03T04:00:00Z", "o": 1, "h": 100000, "l": 0.5, "c": 99999, "v": 1})
            responses[symbol] = {"symbol": symbol, "bars": bars}
        path = root / "evidence/free_market_data/raw" / capture / "alpaca_iex_daily_bars.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        with gzip.open(path, "wt", encoding="utf-8") as handle:
            json.dump({"responses": responses}, handle)

    def test_features_ignore_post_session_bars_in_capture(self):
        import datetime as dt
        sessions = [(dt.date(2026, 9, 2) - dt.timedelta(days=29 - i)).isoformat() for i in range(30)]
        results = {}
        for leak in (False, True):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                copy_contracts(root)
                self._capture(root, "2026-09-03", sessions, extreme_after=leak)
                features = OL.USBars(OL.load_config(root), root).features("XLK", "2026-09-02")
                features.pop("source")
                results[leak] = features
        self.assertEqual(results[False]["status"], "OBSERVED")
        self.assertEqual(results[True], results[False])
        self.assertEqual(results[True]["bars_used"], 30)


class ReplayByteIdentityTests(unittest.TestCase):
    def test_committed_packets_and_portal_replay_byte_identical(self):
        for market in RC.MARKETS:
            self.assertEqual(RC.late_older_evidence_dates(market, ROOT, POLICY), [], market)
            packets = RC.build_market(market, ROOT, POLICY)
            self.assertEqual(RC.verify_market(market, packets, ROOT), [], market)
            days = OL.build_market_days(market, ROOT, CONFIG, POLICY)
            self.assertEqual(OL.verify_market_days(market, days, ROOT), [], market)
        self.assertEqual(run_cli(RC, ["portal", "--root", str(ROOT)])[0], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
