#!/usr/bin/env python3
"""Candidate canonical-identity observation contract regressions."""
from __future__ import annotations

import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci  # noqa: E402
from identity.candidate_identity_observation import (  # noqa: E402
    AUTHORITY_ALL_FALSE,
    CandidateIdentityObservationError,
    build_observation,
    validate_observation,
)
from replay.opportunity_trigger import payload_sha256  # noqa: E402


def resign(candidate: dict) -> None:
    candidate["record_hash"] = payload_sha256({
        key: value for key, value in candidate.items() if key != "record_hash"
    })


class CandidateIdentityObservationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        full = json.loads((ROOT / "evidence/operational/dynamic_clock/dynamic_clock_report.json").read_text())
        wanted = {"BTC", "005930", "ETH/USD"}
        by_market = {}
        for market, result in full["by_market"].items():
            rows = [row for row in result["review_queue"] if row["subject"] in wanted]
            if rows:
                by_market[market] = {"review_queue": rows}
        cls.report = {"decision_date": full["decision_date"], "by_market": by_market}
        cls.authority = ci.load_authority()
        cls.scope_authority = ci.load_scope_authority()
        cls.packet = build_observation(cls.report, cls.authority, cls.scope_authority)

    def row(self, subject: str) -> dict:
        return next(row for row in self.packet["observations"] if row["subject"] == subject)

    def test_real_btc_and_samsung_resolve_to_ratified_instruments(self):
        self.assertEqual(self.row("BTC")["identity"]["canonical_instrument_id"], "CRYPTO:BTC")
        self.assertEqual(self.row("005930")["identity"]["canonical_instrument_id"], "KRX:005930:COMMON")

    def test_unratified_crypto_pair_remains_not_computable(self):
        row = self.row("ETH/USD")
        self.assertNotEqual(row["identity"]["status"], ci.RESOLVED)
        self.assertIsNone(row["identity"]["canonical_instrument_id"])

    def test_scope_observation_is_separate_from_instrument_identity(self):
        row = self.row("ETH/USD")
        self.assertEqual(row["account_scope"], {"status": ci.RESOLVED, "account_scope": "CRYPTO"})
        self.assertNotEqual(row["identity"]["status"], ci.RESOLVED)

    def test_exact_operational_timestamp_is_used_without_upgrading_candidate_precision(self):
        source = next(row for result in self.report["by_market"].values() for row in result["review_queue"] if row["subject"] == "BTC")
        observed = self.row("BTC")
        self.assertEqual(observed["operational_evaluated_at"], source["operational_evaluation"]["evaluated_at_utc"])
        self.assertEqual(source["time_precision"], "DATE_ONLY")
        self.assertEqual(observed["candidate_validity_status"], "NOT_EVALUATED_BY_THIS_CONTRACT")

    def test_all_authority_remains_false(self):
        self.assertEqual(self.packet["authority"], AUTHORITY_ALL_FALSE)
        for row in self.packet["observations"]:
            self.assertEqual(row["authority"], AUTHORITY_ALL_FALSE)
            self.assertTrue(all(value is False for value in row["authority"].values()))

    def test_packet_is_deterministic_and_validator_rebuilds_independently(self):
        rebuilt = build_observation(self.report, self.authority, self.scope_authority)
        self.assertEqual(self.packet, rebuilt)
        self.assertEqual(validate_observation(self.packet, self.report, self.authority, self.scope_authority), self.packet)

    def test_resigned_output_tamper_is_rejected(self):
        tampered = copy.deepcopy(self.packet)
        tampered["observations"][0]["identity"]["canonical_instrument_id"] = "FAKE"
        tampered["packet_sha256"] = payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(CandidateIdentityObservationError, "OBSERVATION_MISMATCH"):
            validate_observation(tampered, self.report, self.authority, self.scope_authority)

    def test_candidate_market_mismatch_is_rejected(self):
        report = copy.deepcopy(self.report)
        market = next(iter(report["by_market"]))
        report["by_market"][market]["review_queue"][0]["market"] = "WRONG"
        resign(report["by_market"][market]["review_queue"][0])
        with self.assertRaisesRegex(CandidateIdentityObservationError, "CANDIDATE_MARKET_MISMATCH"):
            build_observation(report, self.authority, self.scope_authority)

    def test_missing_exact_operational_time_fails_closed(self):
        report = copy.deepcopy(self.report)
        candidate = next(iter(report["by_market"].values()))["review_queue"][0]
        candidate["operational_evaluation"] = {
            "status": "NOT_AVAILABLE_ARTIFACT_REPRODUCTION",
            "evaluated_at_utc": None,
            "time_precision": "NOT_AVAILABLE",
        }
        candidate["timing_precision"]["operational_evaluated_at"] = "NOT_AVAILABLE"
        resign(candidate)
        with self.assertRaisesRegex(CandidateIdentityObservationError, "OPERATIONAL_EVALUATION_NOT_EXACT"):
            build_observation(report, self.authority, self.scope_authority)

    def test_source_pair_change_cannot_silently_keep_resolved_identity(self):
        report = copy.deepcopy(self.report)
        candidate = next(row for result in report["by_market"].values() for row in result["review_queue"] if row["subject"] == "BTC")
        candidate["source_identity_lineage"]["source_pairs"][0]["source_asset_id"] = "ETH/USD"
        resign(candidate)
        packet = build_observation(report, self.authority, self.scope_authority)
        row = next(row for row in packet["observations"] if row["subject"] == "BTC")
        self.assertNotEqual(row["identity"]["status"], ci.RESOLVED)
        self.assertIsNone(row["identity"]["canonical_instrument_id"])

    def test_summary_is_exactly_reconciled(self):
        summary = self.packet["summary"]
        self.assertEqual(summary["candidate_count"], len(self.packet["observations"]))
        self.assertEqual(summary["identity_resolved_count"], 2)
        self.assertEqual(summary["scope_resolved_count"], 3)


if __name__ == "__main__":
    unittest.main(verbosity=2)
