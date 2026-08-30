#!/usr/bin/env python3
"""Korea five-axis to staged-symbol review bridge regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("korea_symbol_market_review", ROOT / "decision" / "korea_symbol_market_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REVIEW)


def current_inputs():
    market = json.loads((ROOT / "data" / "latest_korea_market_signals.json").read_text(encoding="utf-8"))
    stages = json.loads((ROOT / "data" / "stage_history.json").read_text(encoding="utf-8"))
    return market, stages


class CurrentEvidenceTests(unittest.TestCase):
    def test_current_packet_connects_five_axes_price_and_flow_without_orders(self):
        market, stages = current_inputs()
        result = REVIEW.build_review(market, stages)
        self.assertEqual(REVIEW.validate_output(result), result)
        self.assertEqual(result["five_axis"]["ratio"], "5/5")
        self.assertEqual(result["five_axis"]["aggregate_regime"], "UNKNOWN")
        self.assertEqual(result["five_axis"]["final_policy"], "PENDING_POLICY_RATIFICATION")
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        self.assertEqual(set(by_symbol), {"012450", "298040", "329180"})
        self.assertEqual(by_symbol["298040"]["pipeline_stage"], "Candidate")
        self.assertEqual(by_symbol["298040"]["price_context"]["close_krw"], 3079000)
        self.assertEqual(by_symbol["298040"]["entry_review"]["state"], "WAIT")
        self.assertIn("FOREIGN_AND_INSTITUTION_NET_BUY", by_symbol["298040"]["observed_facts"])
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_rehashing_tampered_source_cannot_change_output(self):
        market, stages = current_inputs()
        packet = REVIEW.build_review(market, stages)
        tampered = copy.deepcopy(packet)
        tampered["source"]["stage_snapshot"]["subjects"]["298040"]["latest_confirmed_row"]["close"] = 1
        unsigned = {key: value for key, value in tampered.items() if key != "packet_sha256"}
        tampered["packet_sha256"] = REVIEW.payload_sha256(unsigned)
        with self.assertRaisesRegex(REVIEW.KoreaSymbolMarketReviewError, "OUTPUT_DERIVATION_MISMATCH"):
            REVIEW.validate_output(tampered)

    def test_open_authority_fails_closed(self):
        market, stages = current_inputs()
        contract = REVIEW.load_contract()
        contract["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(REVIEW.KoreaSymbolMarketReviewError, "AUTHORITY_INVALID"):
            REVIEW.build_review(market, stages, contract=contract)

    def test_populate_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="korea_symbol_review_") as tmp:
            first = REVIEW.populate(output_root=Path(tmp) / "evidence", latest_path=Path(tmp) / "latest.json")
            second = REVIEW.populate(output_root=Path(tmp) / "evidence", latest_path=Path(tmp) / "latest.json")
        self.assertEqual(first["outcome"], "populated")
        self.assertEqual(second["outcome"], "verified_existing")
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])


if __name__ == "__main__":
    unittest.main()
