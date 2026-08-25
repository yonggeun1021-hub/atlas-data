#!/usr/bin/env python3
"""P8-12 structured provider-identity lineage regressions.

The bridge transports adapter facts only.  It never resolves an identity,
changes candidate tiering, or infers a provider from a market/path/ticker.
"""
from __future__ import annotations

import copy
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock import operational_scan as scan  # noqa: E402
from clock.dynamic_clock import (  # noqa: E402
    ClockEvent,
    DynamicClockError,
    build_episode_history,
)
from clock.review_candidate import (  # noqa: E402
    SOURCE_IDENTITY_AVAILABLE,
    SOURCE_IDENTITY_MISSING,
    ReviewCandidateError,
    build_raw_trigger_record,
    build_subject_review_candidate,
    validate_review_candidate,
)
from clock.run_dynamic_clock import MODE_OPERATIONAL, run  # noqa: E402
from replay.opportunity_trigger import payload_sha256  # noqa: E402


REAL_DECISION_DATE = max(
    value
    for market in scan.MARKET_SCANNERS
    for value in scan.all_evidence_dates(market)
)


def event(*, source_name="kraken_spot_ohlc", source_asset_id="BTC/USD",
          source="evidence/crypto/btc/raw/day/file.gz", detected_at="2026-08-20",
          evidence_hash="a" * 64):
    return ClockEvent(
        detected_at=detected_at,
        evidence_available_at=detected_at,
        evidence_hash=evidence_hash,
        source=source,
        strength=0.8,
        source_name=source_name,
        source_asset_id=source_asset_id,
    )


def episode(*, trigger_type="PRICE_CONFIRMATION", ev=None):
    return build_episode_history("BTC", "BTC", trigger_type, [ev or event()])[0]


def candidate(episodes):
    return build_subject_review_candidate(
        "BTC", "BTC", episodes, pit_eligibility_status="PASS",
        decision_at="2026-08-20",
    )


def resign(value):
    value["record_hash"] = payload_sha256(
        {k: v for k, v in value.items() if k != "record_hash"}
    )
    return value


class SourceIdentityLineageContractTests(unittest.TestCase):
    def test_complete_provider_pair_reaches_raw_and_subject_records(self):
        ep = episode()
        raw = build_raw_trigger_record(ep)
        self.assertEqual(raw["source_name"], "kraken_spot_ohlc")
        self.assertEqual(raw["source_asset_id"], "BTC/USD")
        self.assertEqual(raw["source_identity_lineage"]["status"], SOURCE_IDENTITY_AVAILABLE)
        built = candidate([ep])
        self.assertEqual(built["source_identity_lineage"], {
            "status": SOURCE_IDENTITY_AVAILABLE,
            "source_pairs": [{
                "source_name": "kraken_spot_ohlc",
                "source_asset_id": "BTC/USD",
            }],
        })
        validate_review_candidate(built)

    def test_legacy_missing_pair_is_not_inferred_from_path_subject_or_market(self):
        legacy = episode(ev=event(source_name=None, source_asset_id=None,
                                  source="evidence/crypto/btc/raw/looks-obvious-BTC.gz"))
        built = candidate([legacy])
        self.assertEqual(built["source_identity_lineage"], {
            "status": SOURCE_IDENTITY_MISSING,
            "source_pairs": [],
        })
        self.assertEqual(built["tier"], "WATCH_REVIEW")

    def test_multiple_provider_pairs_are_preserved_not_arbitrarily_selected(self):
        first = episode(trigger_type="PRICE_CONFIRMATION")
        second = episode(
            trigger_type="INVALIDATION_TRIGGER",
            ev=event(source_name="SECOND_SOURCE", source_asset_id="SECOND_ASSET"),
        )
        lineage = candidate([first, second])["source_identity_lineage"]
        self.assertEqual(lineage["status"], SOURCE_IDENTITY_AVAILABLE)
        self.assertEqual(lineage["source_pairs"], [
            {"source_name": "SECOND_SOURCE", "source_asset_id": "SECOND_ASSET"},
            {"source_name": "kraken_spot_ohlc", "source_asset_id": "BTC/USD"},
        ])

    def test_provider_change_inside_one_renewed_episode_preserves_both_pairs(self):
        events = [
            event(),
            event(
                source_name="SECOND_SOURCE", source_asset_id="SECOND_ASSET",
                detected_at="2026-08-21", evidence_hash="b" * 64,
            ),
        ]
        renewed = build_episode_history(
            "BTC", "BTC", "PRICE_CONFIRMATION", events
        )[0]
        raw = build_raw_trigger_record(renewed)
        self.assertEqual(raw["source_identity_lineage"]["source_pairs"], [
            {"source_name": "SECOND_SOURCE", "source_asset_id": "SECOND_ASSET"},
            {"source_name": "kraken_spot_ohlc", "source_asset_id": "BTC/USD"},
        ])

    def test_identity_lineage_does_not_change_tier_or_authority(self):
        with_lineage = candidate([episode()])
        without_lineage = candidate([
            episode(ev=event(source_name=None, source_asset_id=None))
        ])
        self.assertEqual(with_lineage["tier"], without_lineage["tier"])
        self.assertEqual(with_lineage["authority"], without_lineage["authority"])
        self.assertTrue(all(
            value in (False, None, 0)
            for value in with_lineage["authority"].values()
        ))

    def test_partial_pair_fails_in_state_machine_and_direct_raw_builder(self):
        with self.assertRaisesRegex(DynamicClockError, "SOURCE_IDENTITY_LINEAGE_PARTIAL"):
            episode(ev=event(source_name="kraken_spot_ohlc", source_asset_id=None))
        ep = episode()
        ep["evidence_trail"][0]["source_asset_id"] = None
        with self.assertRaisesRegex(ReviewCandidateError, "SOURCE_ASSET_ID_INVALID"):
            build_raw_trigger_record(ep)

    def test_resigned_available_without_pairs_is_semantically_rejected(self):
        built = candidate([episode()])
        built["source_identity_lineage"] = {
            "status": SOURCE_IDENTITY_AVAILABLE, "source_pairs": [],
        }
        resign(built)
        with self.assertRaisesRegex(ReviewCandidateError, "AVAILABLE_WITHOUT_PAIRS"):
            validate_review_candidate(built)

    def test_resigned_duplicate_or_unsorted_pairs_are_rejected(self):
        built = candidate([episode()])
        pair = built["source_identity_lineage"]["source_pairs"][0]
        built["source_identity_lineage"]["source_pairs"] = [copy.deepcopy(pair), pair]
        resign(built)
        with self.assertRaisesRegex(ReviewCandidateError, "PAIRS_NOT_CANONICAL"):
            validate_review_candidate(built)

    def test_resigned_extra_pair_field_is_rejected(self):
        built = candidate([episode()])
        built["source_identity_lineage"]["source_pairs"][0]["market"] = "BTC"
        resign(built)
        with self.assertRaisesRegex(ReviewCandidateError, "PAIR_SCHEMA_INVALID"):
            validate_review_candidate(built)


class RealScannerAdapterFactsTests(unittest.TestCase):
    def test_btc_adapter_emits_ratified_provider_pair(self):
        result = scan.scan_btc(REAL_DECISION_DATE)
        events = [e for buckets in result["subjects"].values() for values in buckets.values() for e in values]
        self.assertTrue(events)
        self.assertEqual({(e.source_name, e.source_asset_id) for e in events}, {
            ("kraken_spot_ohlc", "BTC/USD"),
        })

    def test_korea_adapter_uses_exact_code_as_source_asset_id(self):
        result = scan.scan_korea(REAL_DECISION_DATE)
        for subject, buckets in result["subjects"].items():
            for values in buckets.values():
                for ev in values:
                    self.assertEqual(ev.source_name, "krx_open_api_stock_daily")
                    self.assertEqual(ev.source_asset_id, subject)

    def test_crypto_adapter_uses_exact_pair_id(self):
        result = scan.scan_crypto(REAL_DECISION_DATE)
        for subject, buckets in result["subjects"].items():
            for values in buckets.values():
                for ev in values:
                    self.assertEqual(ev.source_name, "kraken_spot_ohlc")
                    self.assertEqual(ev.source_asset_id, subject)

    def test_real_report_candidate_lineage_equals_its_raw_episode_union(self):
        report = run(REAL_DECISION_DATE, MODE_OPERATIONAL)
        observed_count = sum(
            len(value["review_queue"]) for value in report["by_market"].values()
        )
        self.assertGreater(observed_count, 0)
        self.assertEqual(
            observed_count,
            sum(value["review_queue_subject_count"] for value in report["by_market"].values()),
        )
        for market_result in report["by_market"].values():
            raw_by_episode = {
                row["candidate_id"]: {
                    (pair["source_name"], pair["source_asset_id"])
                    for pair in row["source_identity_lineage"]["source_pairs"]
                }
                for row in market_result["raw_trigger_ledger"]
            }
            for candidate_row in market_result["review_queue"]:
                expected_pairs = sorted(set().union(*(
                    raw_by_episode[episode_id] for episode_id in candidate_row["episode_ids"]
                )))
                self.assertEqual(
                    candidate_row["source_identity_lineage"],
                    {
                        "status": SOURCE_IDENTITY_AVAILABLE,
                        "source_pairs": [
                            {"source_name": source_name, "source_asset_id": source_asset_id}
                            for source_name, source_asset_id in expected_pairs
                        ],
                    },
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
