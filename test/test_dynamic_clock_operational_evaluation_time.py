#!/usr/bin/env python3
"""P8-12 exact operational evaluation-time contract regressions.

The exact timestamp says only when the live process evaluated the candidate.
It must never be backfilled into the historical date-only trigger fields or
used to unlock candidate freshness, Risk Capacity, P8-13, or trading.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock import run_dynamic_clock as rdc
from clock.candidate_validity_observation import (
    CONTRACT_VERSION,
    FRESHNESS_STATUS,
    LEGACY_CONTRACT_VERSION,
    build_observation,
)
from clock.dynamic_clock import ClockEvent, build_episode_history
from clock.review_candidate import (
    AUTHORITY_ALL_FALSE,
    ReviewCandidateError,
    build_subject_review_candidate,
    validate_review_candidate,
)
from replay.opportunity_trigger import payload_sha256


EXACT_EVALUATED_AT = "2026-08-25T10:30:00Z"
LEGACY_REPORT_PATH = (
    ROOT / "evidence" / "operational" / "dynamic_clock" / "dynamic_clock_report.json"
)
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "p8-12-dynamic-clock.yml"


def _episode(*, captured_at: str = "2026-08-20T03:00:00Z") -> dict:
    event = ClockEvent(
        detected_at="2026-08-20",
        evidence_available_at="2026-08-19",
        evidence_hash="a" * 64,
        source="evidence/example.json",
        strength=0.8,
        evidence_captured_at=captured_at,
        evidence_capture_time_precision="TIMESTAMP",
    )
    return build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [event])[0]


def _rehash(candidate: dict) -> dict:
    value = copy.deepcopy(candidate)
    value["record_hash"] = payload_sha256(
        {key: child for key, child in value.items() if key != "record_hash"}
    )
    return value


class OperationalEvaluationReportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = rdc.run(
            "2026-08-25", evaluation_at_utc=EXACT_EVALUATED_AT
        )

    def test_exact_caller_timestamp_is_bound_at_report_market_and_candidate(self):
        expected = {
            "status": "EXACT_CALLER_SUPPLIED_OPERATIONAL_RUN_TIMESTAMP",
            "evaluated_at_utc": EXACT_EVALUATED_AT,
            "time_precision": "TIMESTAMP",
        }
        self.assertEqual(self.report["operational_evaluation"], expected)
        seen = 0
        for market in self.report["by_market"].values():
            self.assertEqual(market["operational_evaluation"], expected)
            for candidate in market["review_queue"]:
                seen += 1
                self.assertEqual(candidate["operational_evaluation"], expected)
                self.assertEqual(
                    candidate["timing_precision"]["operational_evaluated_at"],
                    "TIMESTAMP",
                )
                self.assertEqual(candidate["time_precision"], "DATE_ONLY")
                self.assertRegex(candidate["trigger_observed_at"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertEqual(candidate["authority"], AUTHORITY_ALL_FALSE)
        self.assertGreater(seen, 0)

    def test_v3_shadow_observation_records_evaluation_but_keeps_every_lock(self):
        observation = build_observation(self.report)
        self.assertEqual(observation["contract_version"], CONTRACT_VERSION)
        self.assertEqual(
            observation["operational_evaluation"],
            self.report["operational_evaluation"],
        )
        candidates = [
            row
            for market in observation["by_market"].values()
            for row in market["candidates"]
        ]
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(
                candidate["operational_evaluation_timestamp_status"],
                "EXACT_CURRENT_RUN_EVALUATION_ONLY",
            )
            self.assertEqual(candidate["candidate_freshness_status"], FRESHNESS_STATUS)
            self.assertEqual(candidate["risk_capacity_status"], FRESHNESS_STATUS)
            self.assertEqual(candidate["p8_13_entry_proposal_status"], "LOCKED_NOT_STARTED")

    def test_repeated_evaluation_of_unchanged_evidence_cannot_inflate_sample_basis(self):
        later_report = rdc.run(
            "2026-08-25", evaluation_at_utc="2026-08-25T10:31:00Z"
        )
        first = build_observation(self.report)
        later = build_observation(later_report)
        self.assertNotEqual(
            first["source_dynamic_clock"]["report_sha256"],
            later["source_dynamic_clock"]["report_sha256"],
        )
        self.assertEqual(
            first["source_dynamic_clock"]["evaluation_invariant_report_sha256"],
            later["source_dynamic_clock"]["evaluation_invariant_report_sha256"],
        )
        self.assertNotEqual(first["observation_sha256"], later["observation_sha256"])

    def test_artifact_reproduction_has_explicitly_unavailable_timestamp(self):
        report = rdc.run()
        self.assertEqual(
            report["operational_evaluation"],
            {
                "status": "NOT_AVAILABLE_ARTIFACT_REPRODUCTION",
                "evaluated_at_utc": None,
                "time_precision": "NOT_AVAILABLE",
            },
        )


class EvaluationTimestampFailClosedTests(unittest.TestCase):
    def test_historical_replay_cannot_receive_a_current_operational_timestamp(self):
        with self.assertRaisesRegex(
            rdc.DynamicClockOrchestratorError,
            "FORBIDDEN_IN_HISTORICAL_REPLAY",
        ):
            rdc.run(
                "2026-08-20",
                rdc.MODE_HISTORICAL_REPLAY,
                evaluation_at_utc="2026-08-20T04:00:00Z",
            )

    def test_evaluation_timestamp_requires_decision_date(self):
        with self.assertRaisesRegex(
            rdc.DynamicClockOrchestratorError,
            "REQUIRES_DECISION_AT",
        ):
            rdc.run(evaluation_at_utc=EXACT_EVALUATED_AT)

    def test_noncanonical_or_timezone_naive_timestamp_is_rejected(self):
        for value in (
            "2026-08-25T10:30:00",
            "2026-08-25T10:30:00.123456Z",
            "2026-08-25T19:30:00+09:00",
        ):
            with self.subTest(value=value):
                with self.assertRaises(rdc.DynamicClockOrchestratorError):
                    rdc.run("2026-08-25", evaluation_at_utc=value)

    def test_kst_decision_date_must_come_from_same_instant(self):
        with self.assertRaisesRegex(
            rdc.DynamicClockOrchestratorError,
            "KST_DATE_MISMATCH",
        ):
            rdc.run("2026-08-24", evaluation_at_utc=EXACT_EVALUATED_AT)

    def test_same_day_capture_after_evaluation_is_rejected(self):
        with self.assertRaisesRegex(
            ReviewCandidateError,
            "EVIDENCE_CAPTURED_AT_AFTER_OPERATIONAL_EVALUATED_AT",
        ):
            build_subject_review_candidate(
                "BTC",
                "BTC",
                [_episode(captured_at="2026-08-20T03:00:00Z")],
                pit_eligibility_status="PASS",
                decision_at="2026-08-20",
                operational_evaluated_at="2026-08-20T02:59:59Z",
            )

    def test_resigned_evaluation_context_tamper_is_independently_rejected(self):
        candidate = build_subject_review_candidate(
            "BTC",
            "BTC",
            [_episode()],
            pit_eligibility_status="PASS",
            decision_at="2026-08-20",
            operational_evaluated_at="2026-08-20T04:00:00Z",
        )
        candidate["operational_evaluation"]["status"] = "MADE_UP"
        with self.assertRaisesRegex(
            ReviewCandidateError,
            "OPERATIONAL_EVALUATION_CONTEXT_MISMATCH",
        ):
            validate_review_candidate(_rehash(candidate))

    def test_exact_evaluation_never_changes_tier_or_authority(self):
        without = build_subject_review_candidate(
            "BTC", "BTC", [_episode()], pit_eligibility_status="PASS",
            decision_at="2026-08-20",
        )
        with_exact = build_subject_review_candidate(
            "BTC", "BTC", [_episode()], pit_eligibility_status="PASS",
            decision_at="2026-08-20",
            operational_evaluated_at="2026-08-20T04:00:00Z",
        )
        self.assertEqual(without["tier"], with_exact["tier"])
        self.assertEqual(without["authority"], with_exact["authority"])
        self.assertEqual(with_exact["time_precision"], "DATE_ONLY")


class BackwardCompatibilityAndWiringTests(unittest.TestCase):
    def test_retained_pre_contract_source_still_builds_v2_byte_semantics(self):
        legacy_report = json.loads(LEGACY_REPORT_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("operational_evaluation", legacy_report)
        observation = build_observation(legacy_report)
        self.assertEqual(observation["contract_version"], LEGACY_CONTRACT_VERSION)
        self.assertNotIn("operational_evaluation", observation)
        self.assertNotIn(
            "evaluation_invariant_report_sha256",
            observation["source_dynamic_clock"],
        )

    def test_workflow_derives_both_fields_from_one_epoch_and_passes_exact_timestamp(self):
        source = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("EPOCH_SECONDS=$(date +%s)", source)
        self.assertIn('date -d "@$EPOCH_SECONDS"', source)
        self.assertIn('date -u -d "@$EPOCH_SECONDS"', source)
        self.assertIn("--evaluation-at-utc", source)
        self.assertIn("$EVALUATION_AT_UTC", source)

    def test_workflow_summary_step_executes_with_nested_report_fields(self):
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        step = next(
            item
            for item in workflow["jobs"]["refresh"]["steps"]
            if str(item.get("name", "")).startswith(
                "Report actual evidence dates found"
            )
        )
        report = rdc.run(
            "2026-08-25", evaluation_at_utc=EXACT_EVALUATED_AT
        )
        with tempfile.TemporaryDirectory() as raw_temp:
            temp = Path(raw_temp)
            report_path = (
                temp
                / "evidence"
                / "operational"
                / "dynamic_clock"
                / "dynamic_clock_report.json"
            )
            report_path.parent.mkdir(parents=True)
            report_path.write_text(json.dumps(report), encoding="utf-8")
            summary = temp / "step-summary.md"
            env = dict(os.environ, GITHUB_STEP_SUMMARY=str(summary))
            completed = subprocess.run(
                ["bash", "-e", "-c", step["run"]],
                cwd=temp,
                env=env,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            rendered = summary.read_text(encoding="utf-8")
            self.assertIn(f"operational_evaluated_at={EXACT_EVALUATED_AT}", rendered)
            for market in ("BTC", "KOREA", "CRYPTO"):
                self.assertIn(f"- {market}: evidence_as_of=", rendered)

    def test_python_operational_modules_never_read_wall_clock(self):
        for path in (
            ROOT / "clock" / "run_dynamic_clock.py",
            ROOT / "clock" / "review_candidate.py",
        ):
            source = path.read_text(encoding="utf-8")
            tree = ast.parse(source)
            forbidden_calls = []
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr in {"now", "utcnow"}:
                    forbidden_calls.append(node.func.attr)
                if (
                    node.func.attr == "time"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "time"
                ):
                    forbidden_calls.append("time.time")
            self.assertEqual(forbidden_calls, [], path)


if __name__ == "__main__":
    unittest.main()
