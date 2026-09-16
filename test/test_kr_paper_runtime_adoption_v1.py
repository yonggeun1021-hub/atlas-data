#!/usr/bin/env python3
"""Offline regressions for KR_PAPER_RUNTIME_ADOPTION_V1 daily publication.

Chain tests use SYNTHETIC relabeled copies of the committed 09-10/09-11
provider bodies (test/kr_paper_runtime_synthetic_bundle.py) inside a temporary
evidence root; nothing is written to the repository.  Git first-seen commit
times are pinned to an old instant so fixed evaluation instants stay valid
after this branch is merged; exact committed-bytes checks still run.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

try:
    import yaml
except ImportError:  # the publisher workflow runs without PyYAML
    yaml = None

ROOT = Path(__file__).resolve().parents[1]
for entry in (ROOT, ROOT / "test"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import kr_paper_runtime_synthetic_bundle as SYNTHETIC
from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import kr_paper_runtime_adoption_v1 as ADOPTION
from rotation import theme_taxonomy_authority as TTA


ROOT_DAY = "evidence/regime/kr_information_system/2026-09-11"
LEGACY_QUALIFICATION = ROOT / "evidence/authority/kr_information_system_runtime_qualification_candidate_20260913.json"
WORKFLOW = ROOT / ".github/workflows/kr-paper-runtime-daily-publish.yml"
OLD_FIRST_SEEN = "2026-09-09T00:00:00Z"


def provenance(current: str, **changes) -> bytes:
    value = {
        "schema_version": "kr_paper_runtime_bundle_provenance/1",
        "capture_mode": "SAME_RUN_CAPTURE",
        "repository": "yonggeun1021-hub/atlas-data",
        "workflow_path": ".github/workflows/kr-paper-runtime-daily-publish.yml",
        "producer_script": ".github/scripts/korea_market_signals_pykrx_candidate.py",
        "run_id": 1, "run_attempt": 1, "publisher_run_id": 1,
        "head_sha": "0" * 40, "event": "schedule", "manual_edits": False,
        "source_attempts": 1,
    } | changes
    return BRIDGE.pretty_bytes(value)


class AdoptionRecordTests(unittest.TestCase):
    def test_committed_record_pins_equal_ratified_qualification(self):
        record, _ = ADOPTION.load_adoption()
        legacy = json.loads(LEGACY_QUALIFICATION.read_bytes())
        for key, value in record["pinned_bindings"].items():
            self.assertEqual(legacy["bindings"][key], value, key)
        self.assertEqual(record["authority"], legacy["authority"])
        self.assertEqual(record["supersedes_date_pinned_qualification"]["sha256"],
                         ADOPTION.sha256(LEGACY_QUALIFICATION.read_bytes()))
        self.assertFalse(record["bundle_producer"]["raw_provider_rows_committed"])
        # Rolling extension stays gated until an explicit CIO confirmation.
        self.assertIn(record["rolling_history_extension"]["status"],
                      {"PROPOSED_PENDING_CIO_CONFIRMATION", "CIO_CONFIRMED"})
        self.assertEqual(ADOPTION.rolling_confirmed(record),
                         record["rolling_history_extension"]["status"] == "CIO_CONFIRMED")
        rolling = record["rolling_history_extension"]
        if rolling["status"] == "CIO_CONFIRMED":
            self.assertEqual(rolling["confirmation_ref"],
                             ADOPTION.sha256((ROOT / rolling["confirmation_record_path"]).read_bytes()))

    def test_pin_drift_authority_and_status_fail_closed(self):
        raw = (ROOT / ADOPTION.ADOPTION_PATH).read_bytes()
        drifted = ADOPTION.current_pins()
        drifted["implementation_sha256"]["regime/kr_paper_runtime.py"] = "f" * 64
        with mock.patch.object(ADOPTION, "current_pins", return_value=drifted):
            with self.assertRaisesRegex(ADOPTION.AdoptionError, "PIN_DRIFT_REQUALIFICATION_REQUIRED"):
                ADOPTION.load_adoption(raw)
        for change, code in (
            ({"status": "PROPOSED"}, "ADOPTION_NOT_ADOPTED"),
            ({"authority": {"paper_runtime_display_authorized": True, "order_authorized": True}},
             "ADOPTION_AUTHORITY_ESCALATION"),
        ):
            value = json.loads(raw) | change
            with self.assertRaisesRegex(ADOPTION.AdoptionError, code):
                ADOPTION.load_adoption(BRIDGE.pretty_bytes(value))


class SessionTests(unittest.TestCase):
    def pair(self, now):
        previous, current = ADOPTION.last_completed_pair(dt.datetime.fromisoformat(now))
        return previous.isoformat(), current.isoformat()

    def test_last_completed_pair_is_point_in_time(self):
        self.assertEqual(self.pair("2026-09-14T09:40:00+00:00"), ("2026-09-11", "2026-09-14"))
        self.assertEqual(self.pair("2026-09-15T00:45:00+00:00"), ("2026-09-11", "2026-09-14"))
        self.assertEqual(self.pair("2026-09-14T06:29:59+00:00"), ("2026-09-10", "2026-09-11"))
        # Chuseok 09-24/09-25 closed, weekend after.
        self.assertEqual(self.pair("2026-09-27T12:00:00+00:00"), ("2026-09-22", "2026-09-23"))
        self.assertFalse(dt.datetime.fromisoformat("2026-09-14T08:59:00+00:00")
                         >= ADOPTION.display_floor(dt.date(2026, 9, 14)))

    def test_execution_session_skips_holidays(self):
        self.assertEqual(ADOPTION.next_open_session(dt.date(2026, 9, 11)), dt.date(2026, 9, 14))
        self.assertEqual(ADOPTION.next_open_session(dt.date(2026, 9, 23)), dt.date(2026, 9, 28))
        boundary = ADOPTION.session_boundary(dt.date(2026, 9, 23), dt.date(2026, 9, 28), "a" * 40)
        self.assertEqual(len(boundary["session_calendar_packet_paths"]), 6)
        self.assertEqual(boundary["execution_session_close_at"], "2026-09-28T06:30:00Z")
        with self.assertRaisesRegex(Exception, "CALENDAR_PACKET_MISSING"):
            ADOPTION.next_open_session(dt.date(2026, 12, 30))


class PublishTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.tmp = Path(self.directory.name)
        self.root = self.tmp / "repo"
        # Never depend on live state: the rolling gate is set explicitly in the
        # temp record and the pointer is seeded from the frozen #696 bytes.
        (self.root / "data").mkdir(parents=True)
        shutil.copyfile(ROOT / ROOT_DAY / "decision.json", self.root / ADOPTION.OUTPUT_PATH)
        (self.root / ADOPTION.ADOPTION_PATH).parent.mkdir(parents=True)
        record = json.loads((ROOT / ADOPTION.ADOPTION_PATH).read_bytes())
        record["rolling_history_extension"]["status"] = "PROPOSED_PENDING_CIO_CONFIRMATION"
        record["rolling_history_extension"]["confirmation_ref"] = None
        (self.root / ADOPTION.ADOPTION_PATH).write_bytes(BRIDGE.pretty_bytes(record))
        shutil.copytree(ROOT / ADOPTION.PACKETS.CALENDAR_ROOT, self.root / ADOPTION.PACKETS.CALENDAR_ROOT)
        day = self.root / ROOT_DAY
        (day / "source-capture").mkdir(parents=True)
        (day / "history").mkdir()
        for name in ("KR_PAPER_REFERENCE_CANDIDATE.json", "source-capture/manifest.json",
                     "history/common-v1-replay-through-2026-09-10.json", "history/final-receipt.json",
                     "decision.json"):
            shutil.copyfile(ROOT / ROOT_DAY / name, day / name)
        patcher = mock.patch.object(TTA, "_commit_time", return_value=OLD_FIRST_SEEN)
        patcher.start()
        self.addCleanup(patcher.stop)

    def confirm_rolling(self):
        path = self.root / ADOPTION.ADOPTION_PATH
        record = json.loads(path.read_bytes())
        record["rolling_history_extension"]["status"] = "CIO_CONFIRMED"
        record["rolling_history_extension"]["confirmation_ref"] = "c" * 64
        path.write_bytes(BRIDGE.pretty_bytes(record))

    def bundle(self, previous, current, completed_at):
        self.counter = getattr(self, "counter", 0) + 1
        return SYNTHETIC.relabeled_bundle(
            self.tmp / f"bundle-{self.counter}-{current}", previous, current, completed_at
        )

    def publish(self, bundle, evaluation_at, **provenance_changes):
        current = json.loads((bundle / "source-capture/manifest.json").read_bytes())["dates"][1]
        return ADOPTION.publish(
            bundle_dir=bundle, provenance_raw=provenance(current, **provenance_changes),
            evaluation_at=evaluation_at, code_revision=self.code_revision, root=self.root,
        )

    def dated(self, day):
        return self.root / ADOPTION.EVIDENCE_BASE / day

    def test_root_bundle_rederives_ratified_decision_and_bindings(self):
        (self.root / ROOT_DAY / "decision.json").unlink()
        result = ADOPTION.publish(
            bundle_dir=ROOT / ROOT_DAY, provenance_raw=provenance("2026-09-11"),
            evaluation_at="2026-09-14T06:00:00Z", code_revision=self.code_revision, root=self.root,
        )
        self.assertEqual(result["status"], "PUBLISHED_KR_PAPER_DISPLAY_ONLY")
        self.assertEqual(result["execution_session_date"], "2026-09-14")
        qualification = json.loads((self.dated("2026-09-11") / "qualification.json").read_bytes())
        legacy = json.loads(LEGACY_QUALIFICATION.read_bytes())
        self.assertEqual(qualification["bindings"], legacy["bindings"])
        self.assertEqual(qualification["status"], legacy["status"])
        decision = json.loads((self.dated("2026-09-11") / "decision.json").read_bytes())
        ratified = json.loads((ROOT / ROOT_DAY / "decision.json").read_bytes())
        for key in ("runtime_regime", "direction", "confidence", "current_observation", "aggregation",
                    "source_sha256", "historical_replay_sha256", "historical_acceptance_sha256"):
            self.assertEqual(decision[key], ratified[key], key)
        self.assertFalse((self.dated("2026-09-11") / "source-capture/responses").exists())
        self.assertEqual(result["latest_pointer_updated"], False)  # not newer than #696 context

    def test_rolling_gate_records_evidence_but_keeps_runtime_unknown(self):
        before = (self.root / ADOPTION.OUTPUT_PATH).read_bytes()
        bundle = self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z")
        result = self.publish(bundle, "2026-09-14T22:00:00Z")
        self.assertEqual(result["status"], "EVIDENCE_RECORDED_RUNTIME_UNKNOWN")
        self.assertEqual(result["reason"], "ADOPTION_ROLLING_HISTORY_NOT_CONFIRMED")
        self.assertIsNone(result["decision_sha256"])
        day = self.dated("2026-09-14")
        self.assertEqual(sorted(p.name for p in day.iterdir()),
                         ["KR_PAPER_REFERENCE_CANDIDATE.json", "provenance.json", "publication.json",
                          "source-manifest.json", "validation.json"])
        # No */source-capture/manifest.json without retained bodies (raw-row consumers glob it).
        self.assertFalse((day / "source-capture").exists())
        self.assertEqual((self.root / ADOPTION.OUTPUT_PATH).read_bytes(), before)
        self.assertEqual(self.publish(bundle, "2026-09-14T22:05:00Z")["status"], "SKIPPED_EXISTING")

    def test_confirmed_chain_publishes_two_sessions_and_latest_pointer(self):
        self.confirm_rolling()
        first = self.publish(self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z"),
                             "2026-09-14T22:00:00Z")
        self.assertEqual(first["status"], "PUBLISHED_KR_PAPER_DISPLAY_ONLY", first)
        self.assertEqual(first["execution_session_date"], "2026-09-15")
        self.assertTrue(first["latest_pointer_updated"])
        day = self.dated("2026-09-14")
        receipt = json.loads((day / "history/rolling-extension-receipt.json").read_bytes())
        self.assertEqual(receipt["range"], {"start": "2026-08-04", "end": "2026-09-11"})
        self.assertEqual(ADOPTION.check_latest(self.root)["execution_session_date"], "2026-09-15")
        latest = json.loads((self.root / ADOPTION.OUTPUT_PATH).read_bytes())
        self.assertEqual(latest["schema_version"], "kr_paper_runtime_decision/5")
        self.assertEqual(latest["session_boundary_freshness"]["execution_session_date"], "2026-09-15")

        second = self.publish(self.bundle("20260914", "20260915", "2026-09-15T09:40:05Z"),
                              "2026-09-15T22:00:00Z")
        self.assertEqual(second["status"], "PUBLISHED_KR_PAPER_DISPLAY_ONLY", second)
        receipt = json.loads((self.dated("2026-09-15") / "history/rolling-extension-receipt.json").read_bytes())
        self.assertEqual(receipt["range"], {"start": "2026-08-05", "end": "2026-09-14"})
        self.assertEqual(receipt["derivation"]["appended_observation"]["validation_sha256"],
                         ADOPTION.sha256((day / "validation.json").read_bytes()))
        self.assertEqual(ADOPTION.check_latest(self.root)["context_session_date"], "2026-09-15")

        # An older session published later never regresses the pointer.
        pointer = (self.root / ADOPTION.OUTPUT_PATH).read_bytes()
        shutil.rmtree(day)
        again = self.publish(self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z"),
                             "2026-09-14T22:00:00Z")
        self.assertFalse(again["latest_pointer_updated"])
        self.assertEqual((self.root / ADOPTION.OUTPUT_PATH).read_bytes(), pointer)

    def test_missing_calendar_packet_records_failure(self):
        self.confirm_rolling()
        for day in ("2026-09-15", "2026-09-16"):
            for path in ADOPTION.PACKETS.packet_paths(dt.date.fromisoformat(day)):
                (self.root / path).unlink()
        before = (self.root / ADOPTION.OUTPUT_PATH).read_bytes()
        result = self.publish(self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z"),
                              "2026-09-14T22:00:00Z")
        self.assertEqual(result["status"], "EVIDENCE_RECORDED_RUNTIME_UNKNOWN")
        self.assertEqual(result["reason"], "CALENDAR_PACKET_MISSING:2026-09-15")
        self.assertIsNone(result["execution_session_date"])
        self.assertTrue((self.dated("2026-09-14") / "validation.json").is_file())
        self.assertEqual((self.root / ADOPTION.OUTPUT_PATH).read_bytes(), before)

    def test_frozen_pointer_matches_dated_decision(self):
        self.assertEqual(ADOPTION.check_latest(self.root)["status"], "LATEST_MATCHES_DATED_DECISION")

    def test_missing_session_breaks_chain_closed(self):
        self.confirm_rolling()
        result = self.publish(self.bundle("20260915", "20260916", "2026-09-16T09:40:05Z"),
                              "2026-09-16T22:00:00Z")
        self.assertEqual(result["status"], "EVIDENCE_RECORDED_RUNTIME_UNKNOWN")
        self.assertEqual(result["reason"], "HISTORY_CHAIN_GAP:2026-09-15")

    def test_stale_evaluation_is_recorded_without_pointer(self):
        self.confirm_rolling()
        before = (self.root / ADOPTION.OUTPUT_PATH).read_bytes()
        result = self.publish(self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z"),
                              "2026-09-15T06:30:00Z")
        self.assertEqual(result["status"], "EVIDENCE_RECORDED_RUNTIME_UNKNOWN")
        self.assertIn(result["reason"], {"LATEST_SOURCE_STALE", "SESSION_BOUNDARY_EXPIRED"})
        self.assertEqual((self.root / ADOPTION.OUTPUT_PATH).read_bytes(), before)

    def test_bundle_admission_fails_closed_without_writing(self):
        good = self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z")
        cases = []
        early = self.bundle("20260911", "20260914", "2026-09-14T08:40:05Z")  # 17:40 KST
        cases.append((early, {}, "BUNDLE_BEFORE_DISPLAY_FLOOR", "2026-09-14T22:00:00Z"))
        cases.append((good, {"manual_edits": True}, "PROVENANCE_MANUAL_EDIT", "2026-09-14T22:00:00Z"))
        cases.append((good, {"workflow_path": ".github/workflows/other.yml"}, "PROVENANCE_PRODUCER_INVALID",
                      "2026-09-14T22:00:00Z"))
        cases.append((good, {}, "BUNDLE_AFTER_EVALUATION", "2026-09-14T09:00:00Z"))
        holiday = self.bundle("20260923", "20260924", "2026-09-24T09:40:05Z")
        cases.append((holiday, {}, "SOURCE_PAIR_NOT_ADJACENT_OPEN_SESSIONS", "2026-09-24T22:00:00Z"))
        skipped = self.bundle("20260910", "20260914", "2026-09-14T09:40:05Z")
        cases.append((skipped, {}, "SOURCE_PAIR_NOT_ADJACENT_OPEN_SESSIONS", "2026-09-14T22:00:00Z"))
        for bundle, changes, code, evaluation in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(ADOPTION.AdoptionError, code):
                    self.publish(bundle, evaluation, **changes)
        tampered = self.tmp / "tampered"
        shutil.copytree(good, tampered)
        response = sorted((tampered / "source-capture/responses").glob("*-KOSPI-stock.json"))[0]
        response.write_bytes(response.read_bytes() + b" ")
        with self.assertRaisesRegex(ADOPTION.AdoptionError, "BUNDLE_REJECTED:SOURCE_RESPONSE"):
            self.publish(tampered, "2026-09-14T22:00:00Z")
        mismatch = self.tmp / "mismatch"
        shutil.copytree(good, mismatch)
        wrapper = json.loads((mismatch / "KR_PAPER_REFERENCE_CANDIDATE.json").read_bytes())
        wrapper["source_packet"]["as_of_date"] = "2026-09-15"
        (mismatch / "KR_PAPER_REFERENCE_CANDIDATE.json").write_bytes(BRIDGE.pretty_bytes(wrapper))
        with self.assertRaisesRegex(ADOPTION.AdoptionError, "BUNDLE_REJECTED"):
            self.publish(mismatch, "2026-09-14T22:00:00Z")
        self.assertFalse(self.dated("2026-09-14").exists())
        self.assertFalse(self.dated("2026-09-24").exists())

    def test_chain_rejects_edited_committed_observation(self):
        self.confirm_rolling()
        self.publish(self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z"), "2026-09-14T22:00:00Z")
        reference = self.dated("2026-09-14") / "KR_PAPER_REFERENCE_CANDIDATE.json"
        reference.write_bytes(reference.read_bytes() + b"\n")
        result = self.publish(self.bundle("20260914", "20260915", "2026-09-15T09:40:05Z"),
                              "2026-09-15T22:00:00Z")
        self.assertEqual(result["reason"], "HISTORY_CHAIN_BYTES_MISMATCH:2026-09-14")

    def test_bot_author_requirement_for_chain(self):
        self.confirm_rolling()
        self.publish(self.bundle("20260911", "20260914", "2026-09-14T09:40:05Z"), "2026-09-14T22:00:00Z")
        record, _ = ADOPTION.load_adoption(root=self.root)
        with mock.patch.object(ADOPTION, "_committed_by_bot", return_value=False):
            with self.assertRaisesRegex(ADOPTION.AdoptionError, "NOT_SCHEDULED_PRODUCER:2026-09-14"):
                ADOPTION.history_through("2026-09-14", record, self.root, require_bot=True)
        with mock.patch.object(ADOPTION, "_committed_by_bot", return_value=True):
            _, _, rolled = ADOPTION.history_through("2026-09-14", record, self.root, require_bot=True)
        self.assertTrue(rolled)


class CommittedPointerAndWorkflowTests(unittest.TestCase):
    @unittest.skipIf(yaml is None, "PyYAML not installed")
    def test_workflow_is_scheduled_morning_capture_and_bounded(self):
        raw = WORKFLOW.read_text(encoding="utf-8")
        workflow = yaml.safe_load(raw)
        triggers = workflow.get("on", workflow.get(True))
        self.assertEqual(set(triggers), {"schedule", "workflow_dispatch"})
        # Next-morning capture only (same-evening rows were provisional on 09-14).
        self.assertEqual([c["cron"] for c in triggers["schedule"]], ["40 23 * * 0-4", "45 0 * * 1-5"])
        # Scheduled runs carry no inputs, so every mode check must default to capture.
        self.assertNotRegex(raw, r"inputs\.mode(?! \|\| 'capture')")
        self.assertEqual(workflow["permissions"], {"contents": "write", "actions": "read"})
        steps = workflow["jobs"]["publish"]["steps"]
        runs = "\n".join(step.get("run", "") for step in steps)
        self.assertIn('MAX_ATTEMPTS: "3"', raw)
        publish_step = next(step for step in steps if step.get("name") == "Publish dated packet and latest pointer")
        self.assertIn("set -o pipefail", publish_step["run"])
        self.assertIn("RESPONSE_ROW_SCHEMA_INVALID", runs)
        # Raw KRX rows are private-only: never uploaded as a public artifact.
        self.assertFalse([step for step in steps if "upload-artifact" in step.get("uses", "")])
        failure = [step for step in steps if "failure()" in str(step.get("if", ""))]
        self.assertEqual(len(failure), 1)
        self.assertIn("GITHUB_STEP_SUMMARY", failure[0]["run"])
        self.assertIn("non-transient capture failure", runs)
        self.assertIn('assert "/responses/" not in path', runs)
        # Every dependency verification must write outside the checkout, or the
        # commit-boundary step refuses artifact/dependency-verification.json.
        verifications = re.findall(
            r"verify_kr_paper_source_dependencies\.py(?:[^\n]*\\\n)*[^\n]*", runs)
        self.assertTrue(verifications)
        for call in verifications:
            self.assertIn('--out "$RUNNER_TEMP/', call)
        self.assertNotIn("echo \"$KRX", runs)
        self.assertNotIn("set -x", runs)
        secret_steps = [step["name"] for step in steps if "secrets." in json.dumps(step.get("env", {}))]
        self.assertEqual(secret_steps, ["Capture with bounded retry (capture mode)"])
        self.assertNotIn("korea-market-signals.yml", [
            step.get("uses", "") for step in steps
        ])

    def test_korea_market_signals_workflow_pin_k2_unchanged(self):
        registry = json.loads((ROOT / "config/regime_source_owner_registry_v2.json").read_bytes())
        owner = registry["markets"]["KRX"]["source_owner"]
        self.assertEqual(owner["workflow_sha256"], ADOPTION.sha256((ROOT / owner["workflow_path"]).read_bytes()))


if __name__ == "__main__":
    unittest.main()
