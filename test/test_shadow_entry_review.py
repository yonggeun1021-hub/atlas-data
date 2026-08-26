#!/usr/bin/env python3
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
from decision import shadow_entry_review as review


def _contains_key(value, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            _contains_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


class ShadowEntryReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(review.DEFAULT_REPORT.read_text())
        cls.identity = json.loads(review.DEFAULT_IDENTITY.read_text())
        cls.contract = json.loads(review.DEFAULT_CONTRACT.read_text())
        cls.packet = review.build_packet(cls.report, cls.identity, cls.contract)
        cls.by_subject = {row["subject"]: row for row in cls.packet["review_items"]}

    def test_real_samsung_candidate_reaches_zero_capital_reversal_review(self):
        row = self.by_subject["005930"]
        self.assertEqual("REVERSAL_PROBE_REVIEW", row["review_state"])
        self.assertEqual("PROBE_REVIEW", row["participation_state"])
        self.assertEqual("ZERO_CAPITAL_HUMAN_REVIEW_ITEM", row["p8_13_review_surface"])
        self.assertEqual(2, row["confirmation_count"])
        self.assertEqual("KRX:005930:COMMON", row["canonical_instrument_id"])

    def test_real_btc_and_hynix_are_not_misrepresented_as_entries(self):
        self.assertEqual("WAIT_FOR_PULLBACK_REVIEW", self.by_subject["BTC"]["review_state"])
        self.assertEqual("WATCH_REVIEW", self.by_subject["000660"]["review_state"])
        self.assertEqual("RADAR", self.by_subject["BTC"]["participation_state"])
        self.assertEqual("RADAR", self.by_subject["000660"]["participation_state"])

    def test_unresolved_identity_remains_not_reviewable(self):
        row = self.by_subject["034020"]
        self.assertEqual("NOT_REVIEWABLE", row["review_state"])
        self.assertEqual("IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD", row["review_reason"])
        self.assertIsNone(row["canonical_instrument_id"])

    def test_every_row_and_packet_keep_all_money_authority_closed(self):
        self.assertEqual(review.AUTHORITY_ZERO_CAPITAL, self.packet["authority"])
        for row in self.packet["review_items"]:
            boundary = row["money_boundary"]
            self.assertEqual(review.AUTHORITY_ZERO_CAPITAL["trade_proposal"], boundary["trade_proposal"])
            self.assertEqual(0, boundary["capital"])
            self.assertIsNone(boundary["quantity"])
            self.assertIsNone(boundary["entry_zone"])
            self.assertIsNone(boundary["invalidation"])
            self.assertIsNone(boundary["max_loss"])
            for field in (
                "stage_promotion_authority", "buy_authority", "action_authority",
                "order_authority", "production_authority", "trading_authority",
            ):
                self.assertIs(boundary[field], False)

    def test_operational_packet_physically_contains_no_post_hoc_return_fields(self):
        self.assertFalse(_contains_key(self.packet, {
            "forward_return", "forward_return_pct", "mfe", "mae",
            "post_hoc_audit_note", "reference_forward_metrics",
        }))

    def test_validator_independently_rebuilds_and_rejects_resigned_output(self):
        tampered = copy.deepcopy(self.packet)
        tampered["review_items"][0]["review_state"] = "MOMENTUM_PROBE_REVIEW"
        tampered["review_items"][0]["row_sha256"] = payload_sha256(
            {k: v for k, v in tampered["review_items"][0].items() if k != "row_sha256"}
        )
        tampered["packet_sha256"] = payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            review.ShadowEntryReviewError,
            "SHADOW_ENTRY_REVIEW_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            review.validate_packet(tampered, self.report, self.identity, self.contract)

    def test_contract_cannot_turn_on_money_authority(self):
        contract = copy.deepcopy(self.contract)
        contract["boundary"]["buy_authority"] = True
        with self.assertRaisesRegex(review.ShadowEntryReviewError, "MONEY_BOUNDARY_TAMPERED"):
            review.build_packet(self.report, self.identity, contract)

    def test_contract_cannot_silently_move_unvalidated_trigger_into_supported_set(self):
        contract = copy.deepcopy(self.contract)
        contract["supported_trigger_types"].append("FUNDAMENTAL_REVISION")
        contract["supported_trigger_types"].sort()
        contract["unsupported_without_live_sample"].remove("FUNDAMENTAL_REVISION")
        with self.assertRaisesRegex(review.ShadowEntryReviewError, "TRIGGER_TYPES_CONTRACT_CHANGED"):
            review.build_packet(self.report, self.identity, contract)

    def test_unsupported_trigger_family_cannot_open_review(self):
        candidate = copy.deepcopy(next(
            c for market in self.report["by_market"].values()
            for c in market["review_queue"] if c["subject"] == "005930"
        ))
        identity = copy.deepcopy(next(
            row for row in self.identity["observations"] if row["subject"] == "005930"
        ))
        candidate["trigger_types"] = ["FUNDAMENTAL_REVISION"]
        state, participation, reason = review._classify(candidate, identity, self.contract)
        self.assertEqual("NOT_REVIEWABLE", state)
        self.assertEqual("RADAR", participation)
        self.assertEqual("TRIGGER_FAMILY_UNVALIDATED_NO_LIVE_SAMPLE", reason)

    def test_expired_candidate_cannot_open_review(self):
        candidate = copy.deepcopy(next(
            c for market in self.report["by_market"].values()
            for c in market["review_queue"] if c["subject"] == "005930"
        ))
        identity = copy.deepcopy(next(
            row for row in self.identity["observations"] if row["subject"] == "005930"
        ))
        candidate["expiry"] = "2026-08-25"
        state, _, reason = review._classify(candidate, identity, self.contract)
        self.assertEqual("NOT_REVIEWABLE", state)
        self.assertEqual("CANDIDATE_EXPIRED_FOR_THIS_OPERATIONAL_RUN", reason)

    def test_reflection_authority_leak_is_a_hard_error(self):
        candidate = copy.deepcopy(next(
            c for market in self.report["by_market"].values()
            for c in market["review_queue"] if c["subject"] == "005930"
        ))
        identity = copy.deepcopy(next(
            row for row in self.identity["observations"] if row["subject"] == "005930"
        ))
        candidate["price_reflection_status"]["reflection_status"] = "FULLY_REFLECTED"
        with self.assertRaisesRegex(review.ShadowEntryReviewError, "MUST_REMAIN_UNKNOWN"):
            review._classify(candidate, identity, self.contract)

    def test_content_addressed_history_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "latest.json"
            history = root / "history"
            first = review.write_outputs(self.packet, output=output, history_root=history)
            first_bytes = first.read_bytes()
            second = review.write_outputs(self.packet, output=output, history_root=history)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(1, len(list(history.rglob("review-*.json"))))

    def test_current_packet_is_deterministic_and_semantically_valid(self):
        rebuilt = review.build_packet(self.report, self.identity, self.contract)
        self.assertEqual(self.packet, rebuilt)
        self.assertEqual(
            self.packet,
            review.validate_packet(self.packet, self.report, self.identity, self.contract),
        )


if __name__ == "__main__":
    unittest.main()
