#!/usr/bin/env python3
"""P8-12 collector-timestamp lineage without validity-policy ratification."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock import operational_scan as scan
from clock.dynamic_clock import ClockEvent, DynamicClockError, build_episode_history
from clock.review_candidate import (
    AUTHORITY_ALL_FALSE,
    ReviewCandidateError,
    build_raw_trigger_record,
    build_subject_review_candidate,
    validate_review_candidate,
)
from replay import evidence_index as ei
from replay.opportunity_trigger import build_trigger_event, payload_sha256


def _rehash(record: dict) -> dict:
    out = copy.deepcopy(record)
    out["record_hash"] = payload_sha256({k: v for k, v in out.items() if k != "record_hash"})
    return out


def _episode(*, captured_at: str | None = "2026-08-20T03:00:00Z",
             precision: str = "TIMESTAMP") -> dict:
    event = ClockEvent(
        detected_at="2026-08-20",
        evidence_available_at="2026-08-19",
        evidence_hash="a" * 64,
        source="evidence/example.json",
        strength=0.8,
        evidence_captured_at=captured_at,
        evidence_capture_time_precision=precision,
    )
    return build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [event])[0]


class CommittedSnapshotTimestampTests(unittest.TestCase):
    def test_all_three_real_snapshot_types_expose_exact_timestamp(self):
        snapshots = [
            ei.find_btc_snapshots()[-1],
            ei.find_breadth_snapshots()[-1],
            ei.find_krx_snapshots()[-1],
        ]
        for snapshot in snapshots:
            self.assertEqual(snapshot.capture_time_precision, "TIMESTAMP")
            self.assertRegex(snapshot.captured_at, r"^\d{4}-\d{2}-\d{2}T.*Z$")

    def test_timestamp_is_source_derived_not_wall_clock(self):
        btc = ei.find_btc_snapshots()[-1]
        manifest = json.loads((btc.dir / "_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(btc.captured_at, manifest["fetched_at_utc"])

        breadth = ei.find_breadth_snapshots()[-1]
        manifest = json.loads((breadth.dir / "_manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(breadth.captured_at, manifest["fetched_at_utc"])

        krx = ei.find_krx_snapshots()[-1]
        source = json.loads(krx.path.read_text(encoding="utf-8"))
        self.assertEqual(krx.captured_at, source["collected_at_utc"].replace("+00:00", "Z"))

    def test_timezone_naive_snapshot_timestamp_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "krx.json"
            path.write_text(json.dumps({
                "collected_for_kst_date": "2026-08-20",
                "collected_at_utc": "2026-08-20T01:00:00",
                "stocks": {},
            }), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "TIMEZONE_REQUIRED"):
                ei.KrxSnapshot(path)


class OperationalScanTimestampLineageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = {market: scanner("2026-08-25") for market, scanner in scan.MARKET_SCANNERS.items()}

    def test_missing_source_timestamp_lineage_is_rejected_not_invented(self):
        trigger = build_trigger_event(
            "PRICE_CONFIRMATION", "BTC", "2026-08-19", "2026-08-20",
            "evidence/missing.json", "b" * 64, 0.7, confirmed_at="2026-08-19",
        )
        with self.assertRaisesRegex(ValueError, "EVIDENCE_CAPTURE_TIMESTAMP_LINEAGE_MISSING"):
            scan._to_clock_event(trigger, {})

    def test_every_real_scanned_event_carries_exact_collector_timestamp(self):
        seen = 0
        for result in self.results.values():
            for by_type in result["subjects"].values():
                for events in by_type.values():
                    for event in events:
                        seen += 1
                        self.assertEqual(event.evidence_capture_time_precision, "TIMESTAMP")
                        self.assertRegex(event.evidence_captured_at, r"Z$")
        self.assertGreater(seen, 0)

    def test_exact_timestamp_survives_event_episode_and_raw_record(self):
        episode = _episode()
        self.assertEqual(episode["evidence_trail"][0]["evidence_captured_at"], "2026-08-20T03:00:00Z")
        raw = build_raw_trigger_record(episode)
        self.assertEqual(raw["evidence_captured_at"], "2026-08-20T03:00:00Z")
        self.assertEqual(raw["first_evidence_captured_at"], "2026-08-20T03:00:00Z")
        self.assertEqual(raw["evidence_capture_time_precision"], "TIMESTAMP")

    def test_first_and_latest_capture_timestamps_survive_renewal(self):
        events = [
            ClockEvent(
                detected_at="2026-08-20", evidence_available_at="2026-08-19",
                evidence_hash="1" * 64, source="one", strength=0.7,
                evidence_captured_at="2026-08-20T01:00:00Z",
                evidence_capture_time_precision="TIMESTAMP",
            ),
            ClockEvent(
                detected_at="2026-08-21", evidence_available_at="2026-08-20",
                evidence_hash="2" * 64, source="two", strength=0.8,
                evidence_captured_at="2026-08-21T02:00:00Z",
                evidence_capture_time_precision="TIMESTAMP",
            ),
        ]
        episode = build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", events)[0]
        candidate = build_subject_review_candidate(
            "BTC", "BTC", [episode], pit_eligibility_status="PASS", decision_at="2026-08-21"
        )
        self.assertEqual(candidate["first_evidence_captured_at"], "2026-08-20T01:00:00Z")
        self.assertEqual(candidate["evidence_captured_at"], "2026-08-21T02:00:00Z")

    def test_capture_on_later_calendar_date_is_rejected(self):
        event = ClockEvent(
            detected_at="2026-08-20", evidence_available_at="2026-08-19",
            evidence_hash="c" * 64, source="x", strength=0.5,
            evidence_captured_at="2026-08-21T00:00:00Z",
            evidence_capture_time_precision="TIMESTAMP",
        )
        with self.assertRaisesRegex(DynamicClockError, "AFTER_DATE_ONLY_DETECTED_AT"):
            build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [event])

    def test_capture_timestamp_requires_timezone(self):
        event = ClockEvent(
            detected_at="2026-08-20", evidence_available_at="2026-08-19",
            evidence_hash="d" * 64, source="x", strength=0.5,
            evidence_captured_at="2026-08-20T03:00:00",
            evidence_capture_time_precision="TIMESTAMP",
        )
        with self.assertRaisesRegex(DynamicClockError, "TIMEZONE_REQUIRED"):
            build_episode_history("BTC", "BTC", "PRICE_CONFIRMATION", [event])


class CandidatePrecisionContractTests(unittest.TestCase):
    def setUp(self):
        self.candidate = build_subject_review_candidate(
            "BTC", "BTC", [_episode()], pit_eligibility_status="PASS",
            decision_at="2026-08-20",
        )

    def test_exact_capture_is_exposed_but_aggregate_remains_date_only(self):
        self.assertEqual(self.candidate["evidence_captured_at"], "2026-08-20T03:00:00Z")
        self.assertEqual(self.candidate["evidence_capture_time_precision"], "TIMESTAMP")
        self.assertEqual(self.candidate["timing_precision"]["evidence_captured_at"], "TIMESTAMP")
        self.assertEqual(self.candidate["timing_precision"]["trigger_observed_at"], "DATE_ONLY")
        self.assertEqual(self.candidate["timing_precision"]["decision_at"], "DATE_ONLY")
        self.assertEqual(self.candidate["time_precision"], "DATE_ONLY")

    def test_exact_capture_does_not_change_tier_or_authority(self):
        without = build_subject_review_candidate(
            "BTC", "BTC", [_episode(captured_at=None, precision="NOT_AVAILABLE")],
            pit_eligibility_status="PASS", decision_at="2026-08-20",
        )
        self.assertEqual(self.candidate["tier"], without["tier"])
        self.assertEqual(self.candidate["authority"], AUTHORITY_ALL_FALSE)
        self.assertEqual(without["authority"], AUTHORITY_ALL_FALSE)

    def test_direct_episode_precision_mismatch_fails_closed(self):
        episode = _episode()
        episode["evidence_trail"][-1]["evidence_capture_time_precision"] = "DATE_ONLY"
        with self.assertRaisesRegex(ReviewCandidateError, "EVIDENCE_CAPTURE_TIME_PRECISION_MISMATCH"):
            build_subject_review_candidate(
                "BTC", "BTC", [episode],
                pit_eligibility_status="PASS", decision_at="2026-08-20",
            )

    def test_rehashed_precision_map_tamper_is_rejected(self):
        bad = copy.deepcopy(self.candidate)
        bad["timing_precision"]["decision_at"] = "TIMESTAMP"
        with self.assertRaisesRegex(ReviewCandidateError, "TIMING_PRECISION_CONTRACT_MISMATCH"):
            validate_review_candidate(_rehash(bad))

    def test_rehashed_aggregate_timestamp_promotion_is_rejected(self):
        bad = copy.deepcopy(self.candidate)
        bad["time_precision"] = "TIMESTAMP"
        with self.assertRaisesRegex(ReviewCandidateError, "MUST_REMAIN_DATE_ONLY"):
            validate_review_candidate(_rehash(bad))

    def test_rehashed_future_capture_is_rejected(self):
        bad = copy.deepcopy(self.candidate)
        bad["evidence_captured_at"] = "2026-08-21T00:00:00Z"
        with self.assertRaisesRegex(ReviewCandidateError, "AFTER_DATE_ONLY_DECISION_AT"):
            validate_review_candidate(_rehash(bad))

    def test_rehashed_capture_precision_tamper_is_rejected(self):
        bad = copy.deepcopy(self.candidate)
        bad["evidence_capture_time_precision"] = "DATE_ONLY"
        with self.assertRaisesRegex(ReviewCandidateError, "EVIDENCE_CAPTURE_TIME_PRECISION_MISMATCH"):
            validate_review_candidate(_rehash(bad))

    def test_rehashed_first_capture_after_latest_is_rejected(self):
        bad = copy.deepcopy(self.candidate)
        bad["first_evidence_captured_at"] = "2026-08-20T04:00:00Z"
        with self.assertRaisesRegex(ReviewCandidateError, "FIRST_EVIDENCE_CAPTURED_AT_AFTER_LATEST"):
            validate_review_candidate(_rehash(bad))

    def test_validator_accepts_untampered_candidate(self):
        self.assertEqual(validate_review_candidate(self.candidate), self.candidate)


if __name__ == "__main__":
    unittest.main()
