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

from replay.opportunity_trigger import canonical_json, payload_sha256

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
    load_and_validate_observation,
    validate_observation,
    write_observation,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE


REPORT_PATH = ROOT / "evidence" / "operational" / "dynamic_clock" / "dynamic_clock_report.json"
FIXTURE_REPORT_PATH = (
    ROOT / "evidence" / "operational" / "dynamic_clock"
    / "candidate_validity_source_reports"
    / "report-8dce78ebbbd43fb241afd77270ef80e67e8ab6ca2d89184302421707c4271512.json"
)
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "p8-12-dynamic-clock.yml"


def _report() -> dict:
    return json.loads(REPORT_PATH.read_text(encoding="utf-8"))


def _candidate_fixture_report() -> dict:
    """A content-addressed source report with BTC and CRYPTO candidates.

    The rolling operational report is allowed to contain zero candidates, so
    mutation tests must not borrow candidates from it implicitly.
    """
    return json.loads(FIXTURE_REPORT_PATH.read_text(encoding="utf-8"))


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


def _persist_roots(temp_dir: str) -> tuple[Path, Path]:
    dynamic_root = Path(temp_dir)
    return dynamic_root, dynamic_root / "candidate_validity_observations"


def _write_canonical(path: Path, document: dict) -> None:
    path.write_text(canonical_json(document) + "\n", encoding="utf-8")


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
        self.assertEqual(
            self.observation["candidate_count"],
            sum(
                len(market["review_queue"])
                for market in self.report["by_market"].values()
            ),
        )

    def test_every_candidate_remains_freshness_not_computable(self):
        candidates = _all_candidates(self.observation)
        self.assertEqual(len(candidates), self.observation["candidate_count"])
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

    def test_zero_candidate_population_is_a_valid_observation(self):
        report = copy.deepcopy(self.report)
        for market in report["by_market"].values():
            market["review_queue"] = []
            market["review_queue_subject_count"] = 0
            market["tier_counts"] = {
                "IMMEDIATE_REVIEW": 0,
                "WATCH_REVIEW": 0,
                "OBSERVATION_ONLY": 0,
            }
        observation = build_observation(report)
        self.assertEqual(observation["candidate_count"], 0)
        self.assertEqual(_all_candidates(observation), [])
        self.assertEqual(validate_observation(observation, report), observation)


class SourceFailClosedTests(unittest.TestCase):
    def setUp(self):
        self.report = _candidate_fixture_report()

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
            _dynamic_root, root = _persist_roots(td)
            first = write_observation(self.report, output_root=root)
            before = first.read_bytes()
            second = write_observation(copy.deepcopy(self.report), output_root=root)
            self.assertEqual(first, second)
            self.assertEqual(before, second.read_bytes())
            self.assertEqual(len(list(root.rglob("observation-*.json"))), 1)

    def test_two_different_same_day_reports_are_both_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            first = write_observation(self.report, output_root=root)
            changed = copy.deepcopy(self.report)
            changed["non_semantic_source_revision_marker"] = "second-run"
            second = write_observation(changed, output_root=root)
            self.assertNotEqual(first, second)
            self.assertEqual(first.parent, second.parent)
            self.assertEqual(len(list(root.rglob("observation-*.json"))), 2)
            self.assertEqual(
                len(list((dynamic_root / "candidate_validity_source_reports").glob("report-*.json"))),
                2,
            )

    def test_same_report_manual_and_natural_runs_are_distinct_append_only_samples(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
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
            self.assertEqual(
                len(list((dynamic_root / "candidate_validity_source_reports").glob("report-*.json"))),
                1,
            )

    def test_existing_content_addressed_file_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            _dynamic_root, root = _persist_roots(td)
            path = write_observation(self.report, output_root=root)
            path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "APPEND_ONLY_EXISTING_OBSERVATION_TAMPERED",
            ):
                write_observation(self.report, output_root=root)

    def test_existing_content_addressed_source_tamper_is_rejected_on_rewrite(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            observation_path = write_observation(self.report, output_root=root)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            source_path = dynamic_root / observation["source_dynamic_clock"]["retained_report"]["path"]
            source_path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "APPEND_ONLY_EXISTING_SOURCE_REPORT_TAMPERED",
            ):
                write_observation(self.report, output_root=root)

    def test_source_cannot_be_silently_written_outside_contract_directory(self):
        with tempfile.TemporaryDirectory() as td:
            _dynamic_root, root = _persist_roots(td)
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "SOURCE_OUTPUT_ROOT_MISROUTED",
            ):
                write_observation(
                    self.report,
                    output_root=root,
                    source_output_root=Path(td) / "elsewhere",
                )

    def test_retained_source_is_exact_content_addressed_canonical_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            observation_path = write_observation(self.report, output_root=root)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            source_sha = payload_sha256(self.report)
            retained = observation["source_dynamic_clock"]["retained_report"]
            self.assertEqual(
                retained["path"],
                f"candidate_validity_source_reports/report-{source_sha}.json",
            )
            source_path = dynamic_root / retained["path"]
            self.assertEqual(
                source_path.read_bytes(),
                (canonical_json(self.report) + "\n").encode("utf-8"),
            )
            self.assertEqual(
                load_and_validate_observation(observation_path, dynamic_root),
                observation,
            )

    def test_old_observation_survives_rolling_report_overwrite(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            old_path = write_observation(self.report, output_root=root)
            old_observation = json.loads(old_path.read_text(encoding="utf-8"))

            changed = copy.deepcopy(self.report)
            changed["non_semantic_source_revision_marker"] = "later-run"
            write_observation(changed, output_root=root)
            # Simulate the real rolling source file being replaced by the
            # later run.  Validation must not read this rolling path.
            _write_canonical(dynamic_root / "dynamic_clock_report.json", changed)

            self.assertEqual(
                load_and_validate_observation(old_path, dynamic_root),
                old_observation,
            )

    def test_missing_retained_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            observation_path = write_observation(self.report, output_root=root)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            (dynamic_root / observation["source_dynamic_clock"]["retained_report"]["path"]).unlink()
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "RETAINED_SOURCE_REPORT_MISSING",
            ):
                load_and_validate_observation(observation_path, dynamic_root)

    def test_whitespace_only_source_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            observation_path = write_observation(self.report, output_root=root)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            source_path = dynamic_root / observation["source_dynamic_clock"]["retained_report"]["path"]
            source_path.write_text(json.dumps(self.report, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "RETAINED_SOURCE_BYTES_NOT_CANONICAL",
            ):
                load_and_validate_observation(observation_path, dynamic_root)

    def test_semantic_source_tamper_is_rejected_even_when_json_is_canonical(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            observation_path = write_observation(self.report, output_root=root)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            source_path = dynamic_root / observation["source_dynamic_clock"]["retained_report"]["path"]
            changed = copy.deepcopy(self.report)
            changed["tampered"] = True
            _write_canonical(source_path, changed)
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "RETAINED_SOURCE_HASH_MISMATCH",
            ):
                load_and_validate_observation(observation_path, dynamic_root)

    def test_retained_source_path_traversal_or_wrong_hash_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            dynamic_root, root = _persist_roots(td)
            observation_path = write_observation(self.report, output_root=root)
            observation = json.loads(observation_path.read_text(encoding="utf-8"))
            observation["source_dynamic_clock"]["retained_report"]["path"] = "../dynamic_clock_report.json"
            observation["observation_sha256"] = payload_sha256({
                key: value for key, value in observation.items() if key != "observation_sha256"
            })
            forged = observation_path.with_name(
                f"observation-{observation['observation_sha256']}.json"
            )
            _write_canonical(forged, observation)
            with self.assertRaisesRegex(
                CandidateValidityObservationError,
                "SOURCE_RETENTION_METADATA_INVALID",
            ):
                load_and_validate_observation(forged, dynamic_root)

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
        self.assertIn("source_output_root=VALIDITY_SOURCE_REPORTS_DIR", source)
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("git add evidence/operational/dynamic_clock", workflow)
        self.assertIn("UPSTREAM_WORKFLOW_RUN", workflow)
        self.assertIn("MANUAL_WORKFLOW_DISPATCH", workflow)
        self.assertNotIn("schedule:", workflow)


if __name__ == "__main__":
    unittest.main()
