#!/usr/bin/env python3
"""US evidence to per-symbol review bridge regression.

The bridge is exercised on the frozen input snapshot
(test/rolling_pointer_snapshot.py), not on the live rolling pointers.
``data/latest_free_market_data.json`` (free-market-data.yml) and
``data/stage_history.json`` (one appended snapshot per day, sourced from
the Notion watchlist) move on operational clocks of their own: a stage tag
flipping or a symbol entering the watchlist is a normal day, not a code
change, so exact assertions against today's values were testing the
watchlist rather than the projection.

Pinning the input keeps every expected value exact -- what is asserted is
that the review re-labels the evidence it was given.  What must still
track the live tree is asserted in ``LiveRollingPointerTests`` as
properties (contract subjects are labelled with their own watchlist
stage, from the known stage vocabulary, and never with an order).
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "test") not in sys.path:
    sys.path.insert(0, str(ROOT / "test"))

import rolling_pointer_snapshot as SNAPSHOT  # noqa: E402

SPEC = importlib.util.spec_from_file_location("us_symbol_market_review", ROOT / "decision" / "us_symbol_market_review.py")
REVIEW = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(REVIEW)
MARKET_SPEC = importlib.util.spec_from_file_location(
    "free_market_data_for_us_review",
    ROOT / "collectors" / "free_market_data.py",
)
MARKET = importlib.util.module_from_spec(MARKET_SPEC)
assert MARKET_SPEC.loader is not None
MARKET_SPEC.loader.exec_module(MARKET)


def pinned_inputs():
    market = json.loads(SNAPSHOT.fixture_bytes("data/latest_free_market_data.json"))
    stages = json.loads(SNAPSHOT.fixture_bytes("data/stage_history.json"))
    return market, stages


def live_inputs():
    market = json.loads((ROOT / "data" / "latest_free_market_data.json").read_text(encoding="utf-8"))
    stages = json.loads((ROOT / "data" / "stage_history.json").read_text(encoding="utf-8"))
    return market, stages


class PinnedEvidenceTests(unittest.TestCase):
    """Exact expectations, held against one frozen consistent input."""

    def test_pinned_packet_connects_only_observed_facts_and_never_orders(self):
        market, stages = pinned_inputs()
        result = REVIEW.build_review(market, stages)
        self.assertEqual(REVIEW.validate_output(result), result)
        self.assertEqual(result["five_axis"]["ratio"], "5/5")
        self.assertEqual(result["five_axis"]["missing_axes"], [])
        self.assertEqual(result["five_axis"]["aggregate_regime"], "UNKNOWN")
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        latest_stage = stages[sorted(stages)[-1]]
        self.assertEqual(set(by_symbol), {"TSM", "SNDK"})
        self.assertEqual(by_symbol["TSM"]["pipeline_stage"], "Ready")
        self.assertEqual(by_symbol["TSM"]["price_context"]["status"], "OBSERVED")
        self.assertEqual(by_symbol["TSM"]["entry_review"]["state"], "WAIT")
        self.assertEqual(
            [row["symbol"] for row in by_symbol["TSM"]["market_context"]["leadership_proxies"]],
            ["SMH", "XLK"],
        )
        self.assertEqual(by_symbol["SNDK"]["pipeline_stage"], latest_stage["SNDK"]["stage"])
        self.assertEqual(by_symbol["SNDK"]["price_context"]["status"], "OBSERVED")
        self.assertEqual(by_symbol["SNDK"]["entry_review"]["state"], "WAIT")
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_restaged_subject_is_relabelled_not_absorbed(self):
        # The projection must report whatever stage the watchlist carries --
        # a stage change moves the label and nothing else, and a subject the
        # watchlist stops staging is refused rather than guessed.
        market, stages = pinned_inputs()
        as_of = sorted(stages)[-1]

        restaged = copy.deepcopy(stages)
        restaged[as_of]["SNDK"]["stage"] = "Candidate"
        moved = REVIEW.build_review(market, restaged)
        self.assertEqual(REVIEW.validate_output(moved), moved)
        moved_rows = {row["symbol"]: row for row in moved["symbols"]}
        self.assertEqual(set(moved_rows), {"TSM", "SNDK"})
        self.assertEqual(moved_rows["SNDK"]["pipeline_stage"], "Candidate")
        self.assertEqual(moved_rows["SNDK"]["entry_review"]["state"], "WAIT")
        self.assertEqual(moved_rows["TSM"]["pipeline_stage"], "Ready")
        self.assertEqual(moved["summary"]["automatic_entry_count"], 0)
        self.assertTrue(all(value is False for value in moved["authority"].values()))

        dropped = copy.deepcopy(stages)
        dropped[as_of]["SNDK"]["stage"] = None
        with self.assertRaisesRegex(REVIEW.UsSymbolMarketReviewError, "PIPELINE_SUBJECT_MISSING:SNDK"):
            REVIEW.build_review(market, dropped)

    def test_missing_symbol_price_remains_blocked(self):
        market, stages = pinned_inputs()
        market["alpaca"]["daily_bars"] = [
            row for row in market["alpaca"]["daily_bars"] if row.get("symbol") != "SNDK"
        ]
        unsigned = {key: value for key, value in market.items() if key != "packet_sha256"}
        market["packet_sha256"] = REVIEW.payload_sha256(unsigned)

        result = REVIEW.build_review(market, stages)
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        self.assertEqual(by_symbol["SNDK"]["price_context"]["status"], "UNAVAILABLE")
        self.assertEqual(by_symbol["SNDK"]["entry_review"]["state"], "BLOCKED")
        self.assertFalse(by_symbol["SNDK"]["entry_review"]["automatic_entry_generated"])
        self.assertIsNone(by_symbol["SNDK"]["entry_review"]["order_draft"])

    def test_v2_reference_connects_five_axes_and_symbol_leadership_context(self):
        market, stages = pinned_inputs()
        market["us_market_reference"] = MARKET.derive_us_market_reference(
            market["alpaca"]["daily_bars"], MARKET.load_contract()
        )
        unsigned = {key: value for key, value in market.items() if key != "packet_sha256"}
        market["packet_sha256"] = REVIEW.payload_sha256(unsigned)
        result = REVIEW.build_review(market, stages)
        self.assertEqual(result["five_axis"]["ratio"], "5/5")
        self.assertEqual(result["five_axis"]["missing_axes"], [])
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        self.assertEqual(
            [row["symbol"] for row in by_symbol["TSM"]["market_context"]["leadership_proxies"]],
            ["SMH", "XLK"],
        )
        self.assertIn(
            "FIVE_AXIS_CURRENT_REFERENCE_CONNECTED",
            by_symbol["TSM"]["entry_review"]["reasons"],
        )
        self.assertEqual(result["five_axis"]["aggregate_regime"], "UNKNOWN")
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_rehashing_tampered_source_cannot_change_output(self):
        market, stages = pinned_inputs()
        packet = REVIEW.build_review(market, stages)
        tampered = copy.deepcopy(packet)
        tampered["source"]["symbol_daily_bars"]["TSM"][-1]["close"] = "9999"
        tampered["packet_sha256"] = REVIEW.payload_sha256({key: value for key, value in tampered.items() if key != "packet_sha256"})
        with self.assertRaisesRegex(REVIEW.UsSymbolMarketReviewError, "OUTPUT_DERIVATION_MISMATCH"):
            REVIEW.validate_output(tampered)

    def test_populate_is_idempotent(self):
        with tempfile.TemporaryDirectory(prefix="us_symbol_review_") as tmp:
            snapshot = SNAPSHOT.materialize(Path(tmp) / "snapshot")
            common = {
                "market_path": snapshot / "data" / "latest_free_market_data.json",
                "stage_path": snapshot / "data" / "stage_history.json",
                "output_root": Path(tmp) / "evidence",
                "latest_path": Path(tmp) / "latest.json",
            }
            first = REVIEW.populate(**common)
            second = REVIEW.populate(**common)
        self.assertEqual(first["outcome"], "populated")
        self.assertEqual(second["outcome"], "verified_existing")
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])


class LiveRollingPointerTests(unittest.TestCase):
    """What the live pointers must satisfy, stated as properties.

    No expected value here is a copy of today's watchlist: adding a symbol,
    removing one or moving a stage tag must leave these green, while a
    projection that invents a stage, drops a staged subject or emits an
    order must turn them red.
    """

    def test_live_stage_tags_come_from_the_known_vocabulary(self):
        _, stages = live_inputs()
        self.assertTrue(stages)
        vocabulary = set(SNAPSHOT.valid_stages())
        for day, rows in stages.items():
            self.assertIsInstance(rows, dict, msg=day)
            for symbol, row in rows.items():
                stage = row.get("stage")
                if stage is not None:
                    self.assertIn(stage, vocabulary, msg=f"{day}:{symbol}")

    def test_live_review_labels_every_staged_contract_subject_and_orders_nothing(self):
        market, stages = live_inputs()
        subjects = REVIEW.load_contract()["supported_pipeline_subjects"]
        latest = stages[sorted(stages)[-1]]
        unstaged = [
            symbol
            for symbol in subjects
            if not isinstance(latest.get(symbol), dict) or not isinstance(latest[symbol].get("stage"), str)
        ]
        if unstaged:
            # The watchlist stopped staging a bounded subject. The bridge owes
            # a refusal naming it, never a projected row with a guessed stage.
            with self.assertRaisesRegex(
                REVIEW.UsSymbolMarketReviewError, f"PIPELINE_SUBJECT_MISSING:{unstaged[0]}"
            ):
                REVIEW.build_review(market, stages)
            return

        result = REVIEW.build_review(market, stages)
        self.assertEqual(REVIEW.validate_output(result), result)
        by_symbol = {row["symbol"]: row for row in result["symbols"]}
        self.assertEqual(set(by_symbol), set(subjects))
        self.assertEqual(result["source"]["stage_snapshot"]["as_of"], sorted(stages)[-1])
        vocabulary = set(SNAPSHOT.valid_stages())
        for symbol, row in by_symbol.items():
            self.assertEqual(row["pipeline_stage"], latest[symbol]["stage"], msg=symbol)
            self.assertIn(row["pipeline_stage"], vocabulary, msg=symbol)
            self.assertFalse(row["entry_review"]["automatic_entry_generated"], msg=symbol)
            self.assertIsNone(row["entry_review"]["order_draft"], msg=symbol)
        self.assertEqual(result["summary"]["automatic_entry_count"], 0)
        self.assertEqual(result["summary"]["automatic_exit_count"], 0)
        self.assertTrue(all(value is False for value in result["authority"].values()))


if __name__ == "__main__":
    unittest.main()
