#!/usr/bin/env python3
"""US PAPER runtime publication over committed free-market-data evidence and
the scheduled producer workflow.  Offline; every evaluation time is fixed."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest import mock

import yaml

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import us_paper_runtime as RUNTIME  # noqa: E402
from regime import us_paper_runtime_publication as PUBLICATION  # noqa: E402

CODE = "1" * 40
EVAL = "2026-09-14T08:00:00Z"
PRODUCER = ROOT / ".github" / "workflows" / "us-paper-runtime.yml"
COLLECTOR = ROOT / ".github" / "workflows" / "free-market-data.yml"
PINNED_CHECKOUT = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
PINNED_PYTHON = "actions/setup-python@5fda3b95a4ea91299a34e894583c3862153e4b97"

_spec = importlib.util.spec_from_file_location("us_runtime_fixtures", ROOT / "test" / "test_us_paper_runtime.py")
FIXTURES = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(FIXTURES)


class CommittedEvidenceTest(unittest.TestCase):
    def test_committed_captures_rederive_from_retained_raw_bytes(self):
        index = PUBLICATION.capture_index(ROOT)
        paths = {row["path"] for row in index if row["error"] is None}
        path = ("evidence/free_market_data/derived/2026-09-11/"
                "56c6b9829014ea66c02987b349ea3a3743267d9d73f73fdced6c93d4e802d01a/manifest.json")
        self.assertIn(path, paths)
        record = PUBLICATION._record(ROOT, path)
        self.assertEqual(record["raw_rederivation"], "ALPACA_DAILY_AND_FRED_VIX_RAW_REDERIVED")
        self.assertEqual(record["session_date"], "2026-09-11")
        self.assertEqual(record["reference_input"]["fred"]["value"],
                         record["vix_evidence"]["observation"]["value"])

    def test_session_window_takes_latest_capture_for_that_session_only(self):
        index = PUBLICATION.capture_index(ROOT)
        instant = RUNTIME.instant
        friday = {"date": RUNTIME.day("2026-09-04", "x"), "close_at": instant("2026-09-04T20:00:00Z", "x"),
                  "expires_at": instant("2026-09-08T20:00:00Z", "x")}   # Labor Day 2026-09-07 closed
        record = PUBLICATION.select_session_record(ROOT, index, friday, instant("2026-09-12T00:00:00Z", "x"))
        self.assertEqual(record["observed_at_utc"], "2026-09-07T21:43:23Z")
        early = PUBLICATION.select_session_record(ROOT, index, friday, instant("2026-09-04T21:00:00Z", "x"))
        self.assertEqual(early, {"error": "SESSION_SOURCE_MISSING"})

    def test_unverifiable_latest_capture_blocks_instead_of_older_capture(self):
        index = PUBLICATION.capture_index(ROOT)
        index.append({"path": "evidence/free_market_data/derived/2026-09-11/broken/manifest.json",
                      "observed_at": None, "session_date": None, "error": "PACKET_SHA256_MISMATCH"})
        instant = RUNTIME.instant
        session = {"date": RUNTIME.day("2026-09-11", "x"), "close_at": instant("2026-09-11T20:00:00Z", "x"),
                   "expires_at": instant("2026-09-14T20:00:00Z", "x")}
        record = PUBLICATION.select_session_record(ROOT, index, session, instant("2026-09-12T00:00:00Z", "x"))
        self.assertEqual(record["error"], "SESSION_SOURCE_LATEST_CAPTURE_UNVERIFIABLE")


class PublicationTest(unittest.TestCase):
    def setUp(self):
        self.fixture = FIXTURES.Fixture(write_adoption=False)

    def tearDown(self):
        self.fixture.close()

    def build(self, evaluation_at=EVAL, root=None):
        return PUBLICATION.build_decision(evaluation_at=evaluation_at, code_revision=CODE,
                                          root=root or self.fixture.root, evidence_root=ROOT)

    def test_without_adoption_publication_is_unknown_with_closed_authority(self):
        packet = self.build()
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertFalse(packet["runtime_decision_available"])
        self.assertEqual(packet["reasons"], ["EVIDENCE_CLASS_NOT_LIVE_NATURAL",
                                             "US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT",
                                             "US_PIT_ACCEPTED_RECORD_UNBOUND",
                                             "US_OFFICIAL_SESSION_CALENDAR_UNBOUND"])
        self.assertEqual(packet["authority"], RUNTIME.AUTHORITY_CLOSED)
        self.assertEqual(RUNTIME.publication_key(packet), "calendar-unknown-2026-09-14")
        contract = json.loads((ROOT / RUNTIME.CONTRACT_RELATIVE).read_bytes())
        self.assertEqual(sorted(packet), sorted(contract["decision"]["required_fields"]))

    def test_repository_without_active_adoption_publishes_unknown(self):
        if (ROOT / RUNTIME.ADOPTION_RELATIVE).is_file():
            self.skipTest("an adoption identity is committed; covered by the gate tests")
        packet = PUBLICATION.build_decision(evaluation_at=EVAL, code_revision=CODE)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("US_PAPER_RUNTIME_ADOPTION_IDENTITY_ABSENT", packet["reasons"])
        self.assertIn("US_PIT_ACCEPTED_RECORD_UNBOUND", packet["reasons"])
        self.assertEqual(packet["authority"], RUNTIME.AUTHORITY_CLOSED)

    def test_fixture_root_is_never_live_natural(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(PUBLICATION.evidence_class(Path(directory)), "SYNTHETIC_OFFLINE_FIXTURE")

    def test_authority_escalation_is_refused(self):
        forged = self.build()
        forged["authority"]["order_authorized"] = True
        with mock.patch.object(PUBLICATION.RUNTIME, "evaluate_us_paper_runtime", return_value=forged):
            with self.assertRaisesRegex(PUBLICATION.UsPaperRuntimePublicationError, "AUTHORITY_ESCALATION"):
                self.build()

    def test_nondeterministic_rebuild_is_refused(self):
        first, second = self.build(), self.build()
        second["reasons"] = ["X"]
        with mock.patch.object(PUBLICATION.RUNTIME, "evaluate_us_paper_runtime", side_effect=[first, second]):
            with self.assertRaisesRegex(PUBLICATION.UsPaperRuntimePublicationError, "DETERMINISTIC_REBUILD_MISMATCH"):
                self.build()

    def cli(self, *extra):
        return ["--evaluation-at", EVAL, "--code-revision", CODE, "--root", str(self.fixture.root), *extra]

    def test_published_bytes_are_stable_checkable_and_retry_skips_only_unchanged(self):
        target = self.fixture.root / "decision.json"
        real_build = PUBLICATION.build_decision

        def fixture_build(**kwargs):
            return real_build(**kwargs, evidence_root=ROOT)

        with mock.patch.object(PUBLICATION, "build_decision", side_effect=fixture_build), \
                mock.patch("builtins.print"):
            self.assertEqual(PUBLICATION.main(self.cli("--output", str(target))), 0)
            first = target.read_bytes()
            self.assertEqual(PUBLICATION.main(self.cli("--output", str(target), "--check")), 0)
            # Same session key and basis at a later retry time: skip.
            self.assertTrue(PUBLICATION.published_for_current_session(target, "2026-09-14T09:40:00Z",
                                                                      self.fixture.root))
            self.assertEqual(PUBLICATION.main(
                ["--evaluation-at", "2026-09-14T09:40:00Z", "--code-revision", "2" * 40,
                 "--root", str(self.fixture.root), "--output", str(target), "--published-for-current-session"]), 0)
            tampered = first.replace(b'"UNKNOWN"', b'"RISK_ON"', 1)
            target.write_bytes(tampered)
            with self.assertRaisesRegex(PUBLICATION.UsPaperRuntimePublicationError, "PUBLISHED_DECISION_BYTES_MISMATCH"):
                PUBLICATION.main(self.cli("--output", str(target), "--check"))
            self.assertFalse(PUBLICATION.published_for_current_session(target, EVAL, self.fixture.root))

    def test_retry_republishes_when_basis_changes(self):
        target = self.fixture.root / "decision.json"
        target.write_bytes(RUNTIME.pretty_bytes(self.build("2026-09-11T22:00:00Z")))
        real_build = PUBLICATION.build_decision
        with mock.patch.object(PUBLICATION, "build_decision",
                               side_effect=lambda **kw: real_build(**kw, evidence_root=ROOT)):
            # 2026-09-11T22:00Z already saw the 21:41Z capture; a 2026-09-13 run
            # adds a newer capture, so the diagnostic basis changes.
            self.assertTrue(PUBLICATION.published_for_current_session(target, "2026-09-11T23:40:00Z",
                                                                      self.fixture.root))
            self.assertFalse(PUBLICATION.published_for_current_session(target, "2026-09-13T23:40:00Z",
                                                                       self.fixture.root))


    def test_retry_republishes_when_the_key_is_unchanged_but_the_basis_changed(self):
        # Two committed captures on 2026-09-13 (07:17Z and 21:42Z): a decision
        # published at 08:00Z and a retry at 23:40Z share the publication key,
        # so only the basis comparison can tell that the source advanced.
        target = self.fixture.root / "decision.json"
        first = self.build("2026-09-13T08:00:00Z")
        target.write_bytes(RUNTIME.pretty_bytes(first))
        later = self.build("2026-09-13T23:40:00Z")
        self.assertEqual(RUNTIME.publication_key(first), RUNTIME.publication_key(later))
        self.assertNotEqual(first["basis_sha256"], later["basis_sha256"])
        real_build = PUBLICATION.build_decision
        with mock.patch.object(PUBLICATION, "build_decision",
                               side_effect=lambda **kw: real_build(**kw, evidence_root=ROOT)):
            self.assertTrue(PUBLICATION.published_for_current_session(target, "2026-09-13T09:00:00Z",
                                                                      self.fixture.root))
            self.assertFalse(PUBLICATION.published_for_current_session(target, "2026-09-13T23:40:00Z",
                                                                       self.fixture.root))


class ImplementationBindingTest(unittest.TestCase):
    def test_replay_population_and_identity_are_bound_implementation_paths(self):
        contract = json.loads((ROOT / RUNTIME.CONTRACT_RELATIVE).read_bytes())
        paths = contract["adoption_identity"]["implementation_paths"]
        self.assertEqual(paths, list(RUNTIME.IMPLEMENTATION_PATHS))
        for path in ("regime/us_historical_replay_population.py",
                     "config/us_historical_pit_replay_identity_v1.json"):
            self.assertIn(path, paths)
        self.assertEqual(RUNTIME.load_contract()["adoption_identity"]["implementation_paths"], paths)
        bound = RUNTIME.implementation_sha256()
        self.assertEqual(sorted(bound), sorted(paths))
        for path, digest in bound.items():
            if (ROOT / path).is_file():
                self.assertEqual(digest, RUNTIME.sha256((ROOT / path).read_bytes()), path)
            else:
                self.assertIn(path, RUNTIME.IMPLEMENTATION_OPTIONAL_PATHS)
                self.assertEqual(digest, RUNTIME.IMPLEMENTATION_PATH_ABSENT)

    def test_only_the_optional_identity_may_be_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(RUNTIME, "ROOT", Path(directory)):
                with self.assertRaises(FileNotFoundError):
                    RUNTIME.implementation_sha256()
            fake = Path(directory)
            for path in RUNTIME.IMPLEMENTATION_PATHS:
                if path in RUNTIME.IMPLEMENTATION_OPTIONAL_PATHS:
                    continue
                (fake / path).parent.mkdir(parents=True, exist_ok=True)
                (fake / path).write_bytes(b"x")
            with mock.patch.object(RUNTIME, "ROOT", fake):
                absent = RUNTIME.implementation_sha256()
                identity = fake / "config/us_historical_pit_replay_identity_v1.json"
                identity.parent.mkdir(parents=True, exist_ok=True)
                identity.write_bytes(b"{}")
                present = RUNTIME.implementation_sha256()
            key = "config/us_historical_pit_replay_identity_v1.json"
            self.assertEqual(absent[key], RUNTIME.IMPLEMENTATION_PATH_ABSENT)
            self.assertEqual(present[key], RUNTIME.sha256(b"{}"))
            self.assertNotEqual(absent, present)


class ScheduleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.producer = yaml.safe_load(PRODUCER.read_text(encoding="utf-8"))
        cls.collector = yaml.safe_load(COLLECTOR.read_text(encoding="utf-8"))
        cls.steps = cls.producer["jobs"]["publish"]["steps"]
        cls.script = "\n".join(step.get("run", "") for step in cls.steps)

    @staticmethod
    def crons(workflow):
        triggers = workflow.get("on", workflow.get(True)) or {}
        return [item["cron"] for item in triggers["schedule"]]

    def test_slots_follow_the_collector_on_the_same_weekdays(self):
        collector = self.crons(self.collector)
        self.assertEqual(collector, ["35 21 * * 0-5"])
        minutes = []
        for cron in self.crons(self.producer):
            minute, hour, dom, month, weekday = cron.split()
            self.assertEqual((dom, month, weekday), ("*", "*", "0-5"))
            minutes.append(int(hour) * 60 + int(minute))
        self.assertTrue(all(value >= 21 * 60 + 45 for value in minutes))
        self.assertTrue(all(value < 24 * 60 for value in minutes))
        self.assertEqual(len(minutes), 2)
        triggers = self.producer.get("on", self.producer.get(True))
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("workflow_run", triggers)

    def test_least_privilege_concurrency_and_pinned_actions(self):
        self.assertEqual(self.producer["permissions"], {"contents": "write"})
        self.assertEqual(self.producer["concurrency"],
                         {"group": "atlas-us-paper-runtime", "cancel-in-progress": False})
        uses = [step["uses"] for step in self.steps if "uses" in step]
        self.assertEqual(uses, [PINNED_CHECKOUT, PINNED_PYTHON])
        self.assertNotIn("secrets.", PRODUCER.read_text(encoding="utf-8"))

    def test_check_before_commit_retry_skip_and_no_force_push(self):
        script = self.script
        self.assertIn("--published-for-current-session", script)
        self.assertLess(script.index("--published-for-current-session"), script.index('--output "$LATEST"\n'))
        self.assertLess(script.index("--check"), script.index("git commit"))
        self.assertIn('git add "$LATEST" "$EVIDENCE"', script)
        self.assertNotRegex(script, r"git add (-A|\.|--all)")
        self.assertNotRegex(script, r"push[^\n]*(--force|-f\b|\+HEAD)")
        self.assertIn("evidence/regime/us_paper_runtime/$KEY/$DIGEST.json", script)
        self.assertIn("python3 test/test_us_paper_runtime.py", script)
        self.assertIsNone(re.search(r"curl|wget|api\.stlouisfed|alpaca", script))


if __name__ == "__main__":
    unittest.main()
