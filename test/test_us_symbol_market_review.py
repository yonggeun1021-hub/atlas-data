#!/usr/bin/env python3
"""Current US evidence to per-symbol review bridge regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("us_symbol_market_review", ROOT / "decision" / "us_symbol_market_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REVIEW)


def current_inputs():
    market = json.loads((ROOT / "data" / "latest_free_market_data.json").read_text(encoding="utf-8"))
    stages = json.loads((ROOT / "data" / "stage_history.json").read_text(encoding="utf-8"))
    return market, stages


class CurrentEvidenceTests(unittest.TestCase):
    def test_current_packet_connects_only_observed_facts_and_never_orders(self):
        market, stages = current_inputs()
        result = REVIEW.build_review(market, stages)
        self.assertEqual(REVIEW.validate_output(result), result)
        self.assertEqual(result["five_axis"]["ratio"], "3/5")
        self.assertEqual(result["five_axis"]["missing_axes"], ["BREADTH", "LEADERSHIP"])
        self.assertEqual(result["five_axis"]["aggregate_regime"], "UNKNOWN")
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        self.assertEqual(by_symbol["TSM"]["pipeline_stage"], "Ready")
        self.assertEqual(by_symbol["TSM"]["price_context"]["status"], "OBSERVED")
        self.assertEqual(by_symbol["TSM"]["entry_review"]["state"], "WAIT")
        self.assertEqual(by_symbol["SNDK"]["pipeline_stage"], "Discovery")
        self.assertEqual(by_symbol["SNDK"]["price_context"]["status"], "UNAVAILABLE")
        self.assertEqual(by_symbol["SNDK"]["entry_review"]["state"], "BLOCKED")
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_rehashing_tampered_source_cannot_change_output(self):
        market, stages = current_inputs()
        packet = REVIEW.build_review(market, stages)
        tampered = copy.deepcopy(packet)
        tampered["source"]["symbol_daily_bars"]["TSM"][-1]["close"] = "9999"
        tampered["packet_sha256"] = REVIEW.payload_sha256({key: value for key, value in tampered.items() if key != "packet_sha256"})
        with self.assertRaisesRegex(REVIEW.UsSymbolMarketReviewError, "OUTPUT_DERIVATION_MISMATCH"):
            REVIEW.validate_output(tampered)

    def test_populate_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="us_symbol_review_") as tmp:
            first = REVIEW.populate(output_root=Path(tmp) / "evidence", latest_path=Path(tmp) / "latest.json")
            second = REVIEW.populate(output_root=Path(tmp) / "evidence", latest_path=Path(tmp) / "latest.json")
        self.assertEqual(first["outcome"], "populated")
        self.assertEqual(second["outcome"], "verified_existing")
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])


if __name__ == "__main__":
    unittest.main()
