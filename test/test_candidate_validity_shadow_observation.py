#!/usr/bin/env python3
"""P8-12 Candidate Validity Shadow Observation contract regressions."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from replay.opportunity_trigger import payload_sha256

from clock.candidate_validity_observation import (
    FRESHNESS_STATUS,
    NO_LIVE_SAMPLE_TRIGGER_TYPES,
    OBSERVATION_MODE,
    PIT_DATE_ORDER_VALID,
    TIME_PRECISION_NOT_COMPUTABLE,
    TRIGGER_MANUAL_WORKFLOW_DISPATCH,
    TRIGGER_UPSTREAM_WORKFLOW_RUN,
    CandidateValidityObservationError,
    build_observation,
    validate_observation,
    write_observation,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE


REPORT_PATH = ROOT / "evidence" / "operational" / "dynamic_clock" / "dynamic_clock_report.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "p8-12-dynamic-clock.yml"


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def _rehash_candidate(candidate: dict) -> None:
    candidate["record_hash"] = payload_sha256({
        key: value for key, value in candidate.items() if key != "record_hash"
    })


def _all_candidates(observation: dict) -> list[dict]:
    return [
        candidate
        for market in observation["by_market"].values()
        for candidate in market["candidates"]
    ]


def _walk_keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


class RealReportShadowObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = _report()
        cls.observation = build_observation(cls.report)

    def test_real_report_builds_and_independently_validates(self):
        self.assertEqual(
            validate_observation(self.observation, self.report),
            self.observation,
        )
        self.assertEqual(self.observation["observation_mode"], OBSERVATION_MODE)
        self.assertGreater(self.observation["candidate_count"], 0)

    def test_every_candidate_remains_freshness_not_computable(self):
        candidates = _all_candidates(self.observation)
        self.assertEqual(len(candidates), self.observation["candidate_count"])
        self.assertTrue(candidates)
        for candidate in candidates:
            self.assertEqual(candidate["candidate_freshness_status"], FRESHNESS_STATUS)
            self.assertEqual(candidate["risk_capacity_status"], FRESHNESS_STATUS)
            self.assertEqual(candidate["p8_13_entry_proposal_status"], "LOCKED_NOT_STARTED")

    def test_date_order_and_timestamp_precision_are_separate(self):
        for candidate in _all_candidates(self.observation):
            self.assertEqual(candidate["pit_date_order_status"], PIT_DATE_ORDER_VALID)
            self.assertEqual(candidate["timestamp_order_status"], TIME_PRECISION_NOT_COMPUTABLE)
            self.assertEqual(candidate["time_precision"], "DATE_ONLY")

    def test_all_authority_is_false_and_no_capital_is_allocated(self):
        self.assertEqual(self.observation["authority"], AUTHORITY_ALL_FALSE)
        for candidate in _all_candidates(self.observation):
            self.assertEqual(candidate["authority"], AUTHORITY_ALL_FALSE)
        locks = self.observation["downstream_locks"]
        for name in ("stage", "buy", "action", "order", "production", "trading"):
            self.assertIs(locks[name], False)

    def test_no_outcome_or_sizing_data_enters_observation(self):
        forbidden = {
            "forward_return", "mfe", "mae", "position_size", "quantity",
            "expected_return", "cash", "nav", "order_intent",
        }
        keys = {key.lower() for key in _walk_keys(self.observation)}
        self.assertTrue(forbidden.isdisjoint(keys), forbidden & keys)

    def test_no_live_sample_trigger_families_stay_explicitly_unvalidated(self):
        by_type = {
            item["trigger_type"]: item
            for item in self.observation["trigger_type_observations"]
        }
        for trigger_type in NO_LIVE_SAMPLE_TRIGGER_TYPES:
            item = by_type[trigger_type]
            if item["candidate_observation_count"] == 0:
                self.assertEqual(item["validity_evidence_status"], "UNVALIDATED_NO_LIVE_SAMPLE")
            else:
                self.assertEqual(
                    item["validity_evidence_status"],
                    "PROVISIONAL_SHADOW_SAMPLE_ONLY",
                )

    def test_build_is_deterministic_and_has_no_wall_clock_input(self):
        first = build_observation(copy.deepcopy(self.report))
        second = build_observation(copy.deepcopy(self.report))
        self.assertEqual(first, second)

    def test_local_reproduction_is_not_mislabeled_as_natural_sample(self):
        self.assertEqual(
            self.observation["source_run"]["sample_qualification"],
            "LOCAL_REPRODUCTION_NOT_OPERATIONAL_SAMPLE",
        )


class SourceFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.report = _report()

    def _first_candidate(self) -> dict:
        for market in ("BTC", "CRYPTO", "KOREA"):
            queue = self.report["by_market"][market]["review_queue"]
            if queue:
                return queue[0]
        self.fail("fixture report has no candidate")

    def test_future_or_reversed_candidate_timing_is_rejected_even_when_rehashed(self):
        candidate = self._first_candidate()
        candidate["candidate_updated_at"] = "2099-01-01"
        _rehash_candidate(candidate)
        with self.assertRaisesRegex(Exception, "AFTER_DECISION_AT|DATE_ORDER_INVALID|TIMING_INVARIANT_VIOLATED"):
            build_observation(self.report)

    def test_market_label_mismatch_is_rejected_even_when_rehashed(self):
        candidate = self._first_candidate()
        candidate["market"] = "WRONG"
        _rehash_candidate(candidate)
        with self.assertRaisesRegex(CandidateValidityObservationError, "CANDIDATE_MARKET_MISMATCH"):
            build_observation(self.report)

    def test_review_queue_count_tamper_is_rejected(self):
        self.report["by_market"]["BTC"]["review_queue_subject_count"] += 1
        with self.assertRaisesRegex(CandidateValidityObservationError, "REVIEW_QUEUE_COUNT_MISMATCH"):
            build_observation(self.report)

    def test_tier_count_tamper_is_rejected(self):
        self.report["by_market"]["BTC"]["tier_counts"]["WATCH_REVIEW"] += 1
        with self.assertRaisesRegex(CandidateValidityObservationError, "TIER_COUNTS_MISMATCH"):
            build_observation(self.report)

    def test_duplicate_candidate_id_across_markets_is_rejected(self):
        btc = self.report["by_market"]["BTC"]["review_queue"][0]
        crypto = self.report["by_market"]["CRYPTO"]["review_queue"][0]
        crypto["candidate_id"] = btc["candidate_id"]
        _rehash_candidate(crypto)
        with self.assertRaisesRegex(CandidateValidityObservationError, "CANDIDATE_ID_DUPLICATE"):
            build_observation(self.report)

    def test_unknown_trigger_type_is_rejected_even_when_candidate_rehashed(self):
        candidate = self._first_candidate()
        candidate["trigger_types"] = ["MADE_UP_TRIGGER"]
        candidate["confirmation_count"] = 1
        _rehash_candidate(candidate)
        with self.assertRaisesRegex(CandidateValidityObservationError, "TRIGGER_TYPE_NOT_IN_POLICY"):
            build_observation(self.report)

    def test_source_policy_status_change_requires_contract_review(self):
        self.report["policy_approval_status"] = "RATIFIED"
        with self.assertRaisesRegex(CandidateValidityObservationError, "POLICY_STATUS_UNEXPECTED"):
            build_observation(self.report)


class PersistedArtifactTests(unittest.TestCase):
    def setUp(self):
        self.report = _report()

    def test_identical_run_is_byte_identical_noop(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = write_observation(self.report, output_root=root)
            before = first.read_bytes()
            second = write_observation(copy.deepcopy(self.report), output_root=root)
            self.assertEqual(first, second)
            self.assertEqual(before, second.read_bytes())
            self.assertEqual(len(list(root.rglob("observation-*.json"))), 1)

    def test_two_different_same_day_reports_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = write_observation(self.report, output_root=root)
            changed = copy.deepcopy(self.report)
            changed["non_semantic_source_revision_marker"] = "second-run"
            second = write_observation(changed, output_root=root)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, second.parent)
            self.assertEqual(len(list(root.rglob("observation-*.json"))), 2)

    def test_same_report_manual_and_natural_runs_are_distinct_append_only_samples(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manual = write_observation(
                self.report,
                output_root=root,
                trigger_kind=TRIGGER_MANUAL_WORKFLOW_DISPATCH,
            )
            natural = write_observation(
                self.report,
                output_root=root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            self.assertNotEqual(manual, natural)
            manual_doc = json.loads(manual.read_text(encoding="utf-8"))
            natural_doc = json.loads(natural.read_text(encoding="utf-8"))
            self.assertEqual(
                manual_doc["source_run"]["sample_qualification"],
                "MANUAL_OPERATIONAL_SAMPLE",
            )
            self.assertEqual(
                natural_doc["source_run"]["sample_qualification"],
                "NATURAL_OPERATIONAL_SAMPLE",
            )

    def test_existing_content_addressed_file_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            path = write_observation(self.report, output_root=root)
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "APPEND_ONLY_EXISTING_OBSERVATION_TAMPERED",
            ):
                write_observation(self.report, output_root=root)

    def test_resigned_observation_tamper_is_rejected_by_rebuild(self):
        observation = build_observation(self.report)
        observation["downstream_locks"]["p8_13_entry_proposal"] = "OPEN"
        observation["observation_sha256"] = payload_sha256({
            key: value for key, value in observation.items() if key != "observation_sha256"
        })
        with self.assertRaisesRegex(
            CandidateValidityObservationError,
            "OBSERVATION_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            validate_observation(observation, self.report)


class WiringContractTests(unittest.TestCase):
    def test_workflow_runs_shadow_observation_regression(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("test/test_candidate_validity_shadow_observation.py", workflow)

    def test_runtime_writer_is_wired_and_workflow_commits_under_dynamic_clock_only(self):
        source = (ROOT / "clock" / "run_dynamic_clock.py").read_text(encoding="utf-8")
        self.assertIn("write_validity_observation(", source)
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("git add evidence/operational/dynamic_clock", workflow)
        self.assertIn("UPSTREAM_WORKFLOW_RUN", workflow)
        self.assertIn("MANUAL_WORKFLOW_DISPATCH", workflow)
        self.assertNotIn("schedule:", workflow)


if __name__ == "__main__":
    unittest.main()
