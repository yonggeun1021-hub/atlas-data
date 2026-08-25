#!/usr/bin/env python3
"""P8-12 forward-only candidate lifecycle observation regressions."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock import run_dynamic_clock as rdc
from clock.candidate_lifecycle_observation import (
    BASELINE_PREEXISTING,
    CHAIN_MANUAL,
    CHAIN_NATURAL,
    CONTINUING_CHANGED,
    CONTINUING_UNCHANGED,
    FIRST_ABSENCE,
    FIRST_SEEN_EXACT,
    FIRST_SEEN_FORWARD,
    FIRST_SEEN_UNKNOWN,
    MANUAL_SAMPLE,
    NATURAL_SAMPLE,
    REAPPEARED,
    STILL_ABSENT,
    CandidateLifecycleObservationError,
    build_lifecycle_observation,
    load_and_validate_lifecycle_observation,
    write_lifecycle_observation,
)
from clock.candidate_validity_observation import (
    FRESHNESS_STATUS,
    TRIGGER_LOCAL_REPRODUCTION,
    TRIGGER_MANUAL_WORKFLOW_DISPATCH,
    TRIGGER_UPSTREAM_WORKFLOW_RUN,
    write_observation as write_validity_observation,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE
from replay.opportunity_trigger import payload_sha256


LOCKS = {
    "candidate_validity": FRESHNESS_STATUS,
    "risk_capacity": FRESHNESS_STATUS,
    "p8_13_entry_proposal": "LOCKED_NOT_STARTED",
    "stage": False,
    "buy": False,
    "action": False,
    "order": False,
    "production": False,
    "trading": False,
}

SOURCE_LOCKS = {
    key: value for key, value in LOCKS.items() if key != "candidate_validity"
}


def candidate(subject: str, market: str, *, trigger: str = "PRICE_CONFIRMATION",
              updated: str = "2026-08-25") -> dict:
    row = {
        "candidate_id": payload_sha256({"subject": subject, "market": market, "updated": updated}),
        "candidate_record_hash": payload_sha256({"source": subject, "updated": updated}),
        "subject": subject,
        "market": market,
        "tier_observed": "WATCH_REVIEW",
        "trigger_types": [trigger],
        "evidence_as_of": "2026-08-24",
        "trigger_observed_at": updated,
        "candidate_created_at": "2026-08-24",
        "candidate_updated_at": updated,
        "decision_at": "2026-08-25",
        "expiry": "2026-08-27",
        "time_precision": "DATE_ONLY",
        "timing_precision": {
            "evidence_as_of": "DATE_ONLY",
            "first_evidence_captured_at": "TIMESTAMP",
            "evidence_captured_at": "TIMESTAMP",
            "trigger_observed_at": "DATE_ONLY",
            "decision_at": "DATE_ONLY",
            "price_as_of": "NOT_AVAILABLE",
            "candidate_created_at": "DATE_ONLY",
            "candidate_updated_at": "DATE_ONLY",
            "operational_evaluated_at": "TIMESTAMP",
        },
        "candidate_freshness_status": FRESHNESS_STATUS,
        "risk_capacity_status": FRESHNESS_STATUS,
        "p8_13_entry_proposal_status": "LOCKED_NOT_STARTED",
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    return row


def observation(evaluated_at: str, rows: list[dict], *, qualification: str = NATURAL_SAMPLE,
                invariant: str = "1" * 64) -> dict:
    by_market = {}
    for market in ("BTC", "CRYPTO", "KOREA"):
        values = [copy.deepcopy(row) for row in rows if row["market"] == market]
        by_market[market] = {"market": market, "candidate_count": len(values), "candidates": values}
    trigger_kind = {
        NATURAL_SAMPLE: "UPSTREAM_WORKFLOW_RUN",
        MANUAL_SAMPLE: "MANUAL_WORKFLOW_DISPATCH",
    }.get(qualification, "LOCAL_REPRODUCTION")
    doc = {
        "contract_version": "candidate_validity_shadow_observation/4",
        "observation_date": "2026-08-25",
        "source_run": {"trigger_kind": trigger_kind, "sample_qualification": qualification},
        "operational_evaluation": {
            "status": "EXACT_CALLER_SUPPLIED_OPERATIONAL_RUN_TIMESTAMP",
            "evaluated_at_utc": evaluated_at,
            "time_precision": "TIMESTAMP",
        },
        "source_dynamic_clock": {
            "evaluation_invariant_report_sha256": invariant,
        },
        "candidate_count": len(rows),
        "by_market": by_market,
        "downstream_locks": copy.deepcopy(SOURCE_LOCKS),
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }
    doc["observation_sha256"] = payload_sha256(doc)
    return doc


def build(source: dict, prior: dict | None = None) -> dict:
    return build_lifecycle_observation(
        source,
        source_observation_path=(
            "candidate_validity_observations/2026-08-25/"
            f"observation-{source['observation_sha256']}.json"
        ),
        prior_lifecycle=prior,
        prior_lifecycle_path=(
            None if prior is None else
            "candidate_lifecycle_observations/2026-08-25/"
            f"lifecycle-{prior['lifecycle_observation_sha256']}.json"
        ),
    )


def state(document: dict, subject: str, market: str = "BTC") -> dict:
    return next(row for row in document["state_records"]
                if row["subject"] == subject and row["market"] == market)


class BaselineAndForwardOnlyTests(unittest.TestCase):
    def test_first_natural_sample_never_backfills_existing_candidate(self):
        result = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        row = state(result, "BTC")
        self.assertEqual(result["chain_qualification"], CHAIN_NATURAL)
        self.assertTrue(result["chain_advancing"])
        self.assertEqual(row["lifecycle_event"], BASELINE_PREEXISTING)
        self.assertIsNone(row["atlas_first_operational_observed_at_utc"])
        self.assertEqual(row["first_seen_status"], FIRST_SEEN_UNKNOWN)
        self.assertEqual(row["historical_trigger_time_status"], "HISTORICAL_TRIGGER_TIME_NOT_RECONSTRUCTED")

    def test_new_subject_after_baseline_gets_exact_atlas_observation_only(self):
        prior = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        current = build(observation(
            "2026-08-25T10:10:00Z",
            [candidate("BTC", "BTC"), candidate("ETH/USD", "CRYPTO")],
            invariant="2" * 64,
        ), prior)
        row = state(current, "ETH/USD", "CRYPTO")
        self.assertEqual(row["lifecycle_event"], FIRST_SEEN_EXACT)
        self.assertEqual(row["first_seen_status"], FIRST_SEEN_FORWARD)
        self.assertEqual(row["atlas_first_operational_observed_at_utc"], "2026-08-25T10:10:00Z")
        self.assertEqual(row["historical_trigger_time_status"], "ATLAS_FORWARD_OBSERVATION_NOT_SOURCE_EVENT_TIME")

    def test_existing_baseline_candidate_remains_unknown_on_later_runs(self):
        prior = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        current = build(observation("2026-08-25T10:10:00Z", [candidate("BTC", "BTC")]), prior)
        row = state(current, "BTC")
        self.assertEqual(row["lifecycle_event"], CONTINUING_UNCHANGED)
        self.assertIsNone(row["atlas_first_operational_observed_at_utc"])
        self.assertIsNone(row["last_changed_observed_at_utc"])

    def test_semantic_change_is_stamped_without_changing_first_seen_boundary(self):
        prior = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        changed = candidate("BTC", "BTC", trigger="INVALIDATION_TRIGGER")
        current = build(observation("2026-08-25T10:10:00Z", [changed], invariant="3" * 64), prior)
        row = state(current, "BTC")
        self.assertEqual(row["lifecycle_event"], CONTINUING_CHANGED)
        self.assertEqual(row["last_changed_observed_at_utc"], "2026-08-25T10:10:00Z")
        self.assertIsNone(row["atlas_first_operational_observed_at_utc"])

    def test_absence_and_reappearance_are_observation_times_not_claimed_expiry_times(self):
        baseline = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        absent = build(observation("2026-08-25T10:10:00Z", [], invariant="2" * 64), baseline)
        absent_row = state(absent, "BTC")
        self.assertEqual(absent_row["lifecycle_event"], FIRST_ABSENCE)
        self.assertEqual(absent_row["first_absent_observed_at_utc"], "2026-08-25T10:10:00Z")
        still = build(observation("2026-08-25T10:20:00Z", [], invariant="2" * 64), absent)
        self.assertEqual(state(still, "BTC")["lifecycle_event"], STILL_ABSENT)
        reappeared = build(observation(
            "2026-08-25T10:30:00Z", [candidate("BTC", "BTC")], invariant="3" * 64
        ), still)
        row = state(reappeared, "BTC")
        self.assertEqual(row["lifecycle_event"], REAPPEARED)
        self.assertEqual(row["last_reappeared_observed_at_utc"], "2026-08-25T10:30:00Z")
        self.assertIsNone(row["atlas_first_operational_observed_at_utc"])

    def test_same_subject_in_two_markets_never_collides(self):
        result = build(observation("2026-08-25T10:00:00Z", [
            candidate("BTC", "BTC"), candidate("BTC", "CRYPTO")
        ]))
        self.assertEqual(result["state_counts"]["active"], 2)
        self.assertEqual(len({row["stable_candidate_key"] for row in result["state_records"]}), 2)


class ChainAndAuthorityTests(unittest.TestCase):
    def test_manual_sample_is_standalone_and_cannot_consume_natural_state(self):
        natural = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        manual_source = observation(
            "2026-08-25T10:10:00Z", [candidate("BTC", "BTC")], qualification=MANUAL_SAMPLE
        )
        manual = build(manual_source)
        self.assertEqual(manual["chain_qualification"], CHAIN_MANUAL)
        self.assertFalse(manual["chain_advancing"])
        with self.assertRaisesRegex(CandidateLifecycleObservationError, "DIAGNOSTIC_SAMPLE_CANNOT_CONSUME"):
            build(manual_source, natural)

    def test_natural_chain_requires_strictly_increasing_evaluation_time(self):
        prior = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        with self.assertRaisesRegex(CandidateLifecycleObservationError, "NOT_STRICTLY_INCREASING"):
            build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]), prior)

    def test_duplicate_evidence_basis_is_explicit_and_not_counted_as_distinct(self):
        prior = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        current = build(observation("2026-08-25T10:10:00Z", [candidate("BTC", "BTC")]), prior)
        self.assertEqual(current["evidence_basis_status"], "DUPLICATE_EVIDENCE_BASIS_EVALUATION_ONLY")
        self.assertFalse(current["distinct_evidence_basis_from_previous"])

    def test_all_authority_and_downstream_locks_remain_closed(self):
        result = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        self.assertEqual(result["authority"], AUTHORITY_ALL_FALSE)
        self.assertEqual(result["downstream_locks"], LOCKS)
        self.assertTrue(all(row["authority"] == AUTHORITY_ALL_FALSE for row in result["state_records"]))
        self.assertIn("P8_13_ENTRY_PROPOSAL", result["prohibited_use"])

    def test_rehashed_source_authority_tamper_is_rejected(self):
        source = observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")])
        source["authority"]["buy_authority"] = True
        source["observation_sha256"] = payload_sha256({k: v for k, v in source.items() if k != "observation_sha256"})
        with self.assertRaisesRegex(CandidateLifecycleObservationError, "SOURCE_AUTHORITY_NOT_ALL_FALSE"):
            build(source)

    def test_rehashed_prior_state_tamper_is_rejected(self):
        prior = build(observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]))
        prior["state_records"][0]["atlas_first_operational_observed_at_utc"] = "2020-01-01T00:00:00Z"
        with self.assertRaisesRegex(CandidateLifecycleObservationError, "PRIOR_LIFECYCLE_HASH_MISMATCH"):
            build(observation("2026-08-25T10:10:00Z", [candidate("BTC", "BTC")]), prior)

    def test_rehashed_prior_authority_or_lock_tamper_is_rejected_independently(self):
        for field, value, reason in (
            ("authority", {**AUTHORITY_ALL_FALSE, "buy_authority": True}, "PRIOR_AUTHORITY"),
            ("downstream_locks", {**LOCKS, "risk_capacity": "COMPUTABLE"}, "PRIOR_DOWNSTREAM"),
        ):
            with self.subTest(field=field):
                prior = build(observation(
                    "2026-08-25T10:00:00Z", [candidate("BTC", "BTC")]
                ))
                prior[field] = value
                prior["lifecycle_observation_sha256"] = payload_sha256({
                    key: item for key, item in prior.items()
                    if key != "lifecycle_observation_sha256"
                })
                with self.assertRaisesRegex(CandidateLifecycleObservationError, reason):
                    build(observation(
                        "2026-08-25T10:10:00Z", [candidate("BTC", "BTC")]
                    ), prior)

    def test_inputs_are_not_mutated(self):
        source = observation("2026-08-25T10:00:00Z", [candidate("BTC", "BTC")])
        before = copy.deepcopy(source)
        build(source)
        self.assertEqual(source, before)


class PersistedRoundTripTests(unittest.TestCase):
    def test_real_v4_reports_form_an_independently_rebuildable_natural_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dynamic_clock"
            validity_root = root / "candidate_validity_observations"
            source_root = root / "candidate_validity_source_reports"
            lifecycle_root = root / "candidate_lifecycle_observations"

            report1 = rdc.run("2026-08-25", evaluation_at_utc="2026-08-25T10:00:00Z")
            validity1 = write_validity_observation(
                report1, output_root=validity_root, source_output_root=source_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            lifecycle1 = write_lifecycle_observation(
                validity1, dynamic_clock_root=root, output_root=lifecycle_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            first = load_and_validate_lifecycle_observation(lifecycle1, dynamic_clock_root=root)
            self.assertEqual(first["chain_qualification"], CHAIN_NATURAL)
            self.assertTrue(all(
                row["first_seen_status"] == FIRST_SEEN_UNKNOWN
                for row in first["state_records"] if row["state"] == "ACTIVE"
            ))

            report2 = rdc.run("2026-08-25", evaluation_at_utc="2026-08-25T10:01:00Z")
            validity2 = write_validity_observation(
                report2, output_root=validity_root, source_output_root=source_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            lifecycle2 = write_lifecycle_observation(
                validity2, dynamic_clock_root=root, output_root=lifecycle_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            second = load_and_validate_lifecycle_observation(lifecycle2, dynamic_clock_root=root)
            self.assertEqual(second["prior_lifecycle"]["lifecycle_observation_sha256"], first["lifecycle_observation_sha256"])
            self.assertEqual(second["evidence_basis_status"], "DUPLICATE_EVIDENCE_BASIS_EVALUATION_ONLY")
            self.assertEqual(write_lifecycle_observation(
                validity2, dynamic_clock_root=root, output_root=lifecycle_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            ), lifecycle2)

    def test_persisted_bytes_or_filename_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dynamic_clock"
            validity_root = root / "candidate_validity_observations"
            source_root = root / "candidate_validity_source_reports"
            lifecycle_root = root / "candidate_lifecycle_observations"
            report = rdc.run("2026-08-25", evaluation_at_utc="2026-08-25T10:00:00Z")
            validity = write_validity_observation(
                report, output_root=validity_root, source_output_root=source_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            lifecycle = write_lifecycle_observation(
                validity, dynamic_clock_root=root, output_root=lifecycle_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            tampered = json.loads(lifecycle.read_text())
            tampered["state_counts"]["active"] += 1
            lifecycle.write_text(json.dumps(tampered, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
            with self.assertRaisesRegex(CandidateLifecycleObservationError, "LIFECYCLE_SEMANTIC|FILENAME_HASH"):
                load_and_validate_lifecycle_observation(lifecycle, dynamic_clock_root=root)

    def test_output_root_cannot_escape_the_dynamic_clock_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dynamic_clock"
            validity_root = root / "candidate_validity_observations"
            source_root = root / "candidate_validity_source_reports"
            report = rdc.run("2026-08-25", evaluation_at_utc="2026-08-25T10:00:00Z")
            validity = write_validity_observation(
                report, output_root=validity_root, source_output_root=source_root,
                trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
            )
            with self.assertRaisesRegex(CandidateLifecycleObservationError, "OUTPUT_ROOT_MISROUTED"):
                write_lifecycle_observation(
                    validity, dynamic_clock_root=root,
                    output_root=Path(tmp) / "wrong-place",
                    trigger_kind=TRIGGER_UPSTREAM_WORKFLOW_RUN,
                )


class WiringTests(unittest.TestCase):
    def test_artifact_reproduction_has_no_lifecycle_timestamp_write(self):
        source = (ROOT / "clock" / "run_dynamic_clock.py").read_text()
        self.assertIn("if evaluation_at_utc is not None:", source)
        self.assertIn("write_lifecycle_observation", source)

    def test_real_workflow_runs_this_regression(self):
        workflow = (ROOT / ".github" / "workflows" / "p8-12-dynamic-clock.yml").read_text()
        self.assertIn("test/test_candidate_lifecycle_observation.py", workflow)

    def test_write_report_persists_manual_lifecycle_but_never_advances_natural_chain(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dynamic_clock"
            patches = {
                "OUT_DIR": root,
                "REPORT_PATH": root / "dynamic_clock_report.json",
                "AUDIT_DIAGNOSTICS_PATH": root / "audit_diagnostics.json",
                "VALIDITY_OBSERVATIONS_DIR": root / "candidate_validity_observations",
                "VALIDITY_SOURCE_REPORTS_DIR": root / "candidate_validity_source_reports",
                "LIFECYCLE_OBSERVATIONS_DIR": root / "candidate_lifecycle_observations",
            }
            with mock.patch.multiple(rdc, **patches):
                rdc.write_report(
                    "2026-08-25",
                    observation_trigger_kind=TRIGGER_MANUAL_WORKFLOW_DISPATCH,
                    evaluation_at_utc="2026-08-25T10:00:00Z",
                )
            paths = list((root / "candidate_lifecycle_observations").glob("*/lifecycle-*.json"))
            self.assertEqual(len(paths), 1)
            persisted = load_and_validate_lifecycle_observation(paths[0], dynamic_clock_root=root)
            self.assertEqual(persisted["chain_qualification"], CHAIN_MANUAL)
            self.assertFalse(persisted["chain_advancing"])
            self.assertIsNone(persisted["prior_lifecycle"])

    def test_write_report_artifact_reproduction_creates_no_lifecycle_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "dynamic_clock"
            patches = {
                "OUT_DIR": root,
                "REPORT_PATH": root / "dynamic_clock_report.json",
                "AUDIT_DIAGNOSTICS_PATH": root / "audit_diagnostics.json",
                "VALIDITY_OBSERVATIONS_DIR": root / "candidate_validity_observations",
                "VALIDITY_SOURCE_REPORTS_DIR": root / "candidate_validity_source_reports",
                "LIFECYCLE_OBSERVATIONS_DIR": root / "candidate_lifecycle_observations",
            }
            with mock.patch.multiple(rdc, **patches):
                rdc.write_report(
                    "2026-08-25",
                    observation_trigger_kind=TRIGGER_LOCAL_REPRODUCTION,
                    evaluation_at_utc=None,
                )
            self.assertFalse((root / "candidate_lifecycle_observations").exists())


if __name__ == "__main__":
    unittest.main()
