#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import krx_paper_canary_eligibility as selector


def _fixture() -> dict:
    return json.loads(selector.DEFAULT_INPUT_PATH.read_text(encoding="utf-8"))


def _resign(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["packet_sha256"] = selector.payload_sha256({
        key: item for key, item in result.items() if key != "packet_sha256"
    })
    return result


class KrxPaperCanaryPolicyProposalTests(unittest.TestCase):
    def test_existing_ratification_packet_remains_unratified_and_separate(self):
        existing = json.loads(
            (ROOT / "config" / "krx_paper_policy_ratification_packet.json").read_text(
                encoding="utf-8"
            )
        )
        proposal = selector.load_proposal()
        self.assertEqual("UNRATIFIED_EVIDENCE_INCOMPLETE", existing["decision_status"])
        self.assertFalse(existing["ratifies_strategy_policy"])
        self.assertEqual("UNRATIFIED_PROPOSAL_ONLY", proposal["proposal_status"])
        self.assertFalse(proposal["ratified"])
        self.assertIsNone(proposal["effective_at_utc"])
        self.assertTrue(all(value is False for value in proposal["authority"].values()))

    def test_only_existing_contract_numbers_are_recommended_with_source_and_date(self):
        proposal = selector.load_proposal()
        recommended = proposal["recommended_from_existing_authoritative_contracts"]
        self.assertEqual(1, recommended["scope"]["maximum_symbols"]["value"])
        self.assertEqual(1, recommended["scope"]["maximum_open_positions"]["value"])
        self.assertEqual(["15m", "1h", "1d"], recommended["completed_bar_intervals"]["value"])
        for row in (
            recommended["scope"]["maximum_symbols"],
            recommended["scope"]["maximum_open_positions"],
            recommended["completed_bar_intervals"],
        ):
            self.assertIn("source", row)
            self.assertEqual("2026-08-30", row["source_effective_date"])

    def test_every_unsupported_numeric_policy_axis_stays_null_and_unratified(self):
        axes = selector.load_proposal()["unratified_policy_axes"]
        self.assertTrue(all(row["status"] == "UNRATIFIED" for row in axes.values()))
        expected_nulls = {
            "virtual_nav_scope": ["virtual_nav_krw"],
            "planned_loss_per_trade": [
                "maximum_planned_loss_krw", "maximum_planned_loss_bps_of_virtual_nav"
            ],
            "single_symbol_and_gross_exposure": [
                "maximum_single_symbol_notional_krw",
                "maximum_single_symbol_bps_of_virtual_nav",
                "maximum_gross_exposure_krw",
                "maximum_gross_exposure_bps_of_virtual_nav",
            ],
            "entry": ["rule", "parameters"],
            "stop": ["rule", "parameters"],
            "time_expiry": ["value"],
            "daily_loss_stop": ["maximum_daily_loss_krw", "maximum_daily_loss_bps_of_virtual_nav"],
        }
        for axis, fields in expected_nulls.items():
            for field in fields:
                self.assertIsNone(axes[axis][field], f"{axis}.{field}")
        self.assertTrue(all(len(row["choices"]) in {2, 3} for row in axes.values()))

    def test_proposal_cannot_be_resigned_into_effective_authority(self):
        proposal = selector.load_proposal()
        for mutation in ("effective", "authority"):
            tampered = copy.deepcopy(proposal)
            if mutation == "effective":
                tampered["proposal_status"] = "RATIFIED"
                tampered["ratified"] = True
                tampered["effective_at_utc"] = "2026-08-31T00:00:00Z"
            else:
                tampered["authority"]["eligibility_authority"] = True
            with self.subTest(mutation=mutation), self.assertRaises(
                selector.KrxPaperCanaryEligibilityError
            ):
                selector.validate_proposal(tampered)


class KrxPaperCanaryEligibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = selector.load_contract()
        cls.proposal = selector.load_proposal()
        cls.input_packet = _fixture()
        cls.packet = selector.build_packet(cls.input_packet, cls.contract, cls.proposal)

    def _build(self, value: dict) -> dict:
        return selector.build_packet(_resign(value), self.contract, self.proposal)

    def test_synthetic_fixture_exercises_selection_but_authority_remains_none_locked(self):
        self.assertEqual("LOCKED", self.packet["status"])
        self.assertEqual("NONE", self.packet["symbol"])
        self.assertEqual("005930", self.packet["diagnostic_selected_symbol"])
        self.assertEqual(0, self.packet["summary"]["authoritative_selected_count"])
        self.assertIn("POLICY_PROPOSAL_UNRATIFIED", self.packet["reason_codes"])
        self.assertTrue(all(value is False for value in self.packet["authority"].values()))
        self.assertIsNone(self.packet["order_boundary"]["order_draft"])
        self.assertIsNone(self.packet["order_boundary"]["broker_submission"])

    def test_candidate_order_does_not_change_deterministic_winner_or_hash_after_normalization(self):
        reversed_input = copy.deepcopy(self.input_packet)
        reversed_input["candidates"].reverse()
        rebuilt = self._build(reversed_input)
        self.assertEqual("005930", rebuilt["diagnostic_selected_symbol"])
        self.assertEqual(self.packet["candidate_audit"], rebuilt["candidate_audit"])

    def test_missing_natural_candidate_set_returns_none_locked(self):
        natural = copy.deepcopy(self.input_packet)
        natural["evidence_mode"] = "NATURAL"
        natural["candidates"] = []
        natural["gate_assessment"] = {
            "common_safety": "UNKNOWN",
            "effective_krx_shadow": "UNKNOWN",
            "current_state": "LOCKED",
            "source_sha256": "a" * 64,
        }
        natural["policy"]["status"] = "UNRATIFIED"
        natural["policy"]["thresholds"] = {key: None for key in selector.THRESHOLD_FIELDS}
        packet = self._build(natural)
        self.assertEqual("LOCKED", packet["status"])
        self.assertEqual("NONE", packet["symbol"])
        self.assertIsNone(packet["diagnostic_selected_symbol"])
        self.assertIn("NATURAL_CANDIDATE_SET_EMPTY", packet["reason_codes"])
        self.assertIn("NO_CANDIDATE_WITH_COMPLETE_ELIGIBLE_EVIDENCE", packet["reason_codes"])

    def test_missing_depth_spread_or_slippage_blocks_each_synthetic_candidate(self):
        field_map = {
            "depth": "DEPTH_NOT_AVAILABLE",
            "spread": "SPREAD_NOT_AVAILABLE",
            "slippage": "SLIPPAGE_NOT_AVAILABLE",
        }
        for field, reason in field_map.items():
            value = copy.deepcopy(self.input_packet)
            for candidate in value["candidates"]:
                candidate[field]["status"] = "NOT_AVAILABLE"
            packet = self._build(value)
            self.assertIsNone(packet["diagnostic_selected_symbol"])
            self.assertTrue(all(reason in row["reason_codes"] for row in packet["candidate_audit"]))

    def test_incomplete_or_nonlatest_bar_blocks_candidate(self):
        value = copy.deepcopy(self.input_packet)
        value["candidates"][1]["bars"]["15m"]["completed"] = False
        value["candidates"][1]["bars"]["1h"]["latest_completed_slot_exact"] = False
        packet = self._build(value)
        row = next(item for item in packet["candidate_audit"] if item["symbol"] == "005930")
        self.assertFalse(row["diagnostic_eligible"])
        self.assertIn("BAR_NOT_COMPLETED:15m", row["reason_codes"])
        self.assertIn("BAR_NOT_LATEST_COMPLETED_SLOT:1h", row["reason_codes"])
        self.assertEqual("000660", packet["diagnostic_selected_symbol"])

    def test_stale_measurement_blocks_candidate(self):
        value = copy.deepcopy(self.input_packet)
        for candidate in value["candidates"]:
            candidate["spread"]["available_at_utc"] = "2026-08-31T00:50:00Z"
            candidate["spread"]["valid_until_utc"] = "2026-08-31T00:55:00Z"
        packet = self._build(value)
        self.assertIsNone(packet["diagnostic_selected_symbol"])
        self.assertTrue(all("SPREAD_STALE" in row["reason_codes"] for row in packet["candidate_audit"]))

    def test_duplicate_selection_key_blocks_retry_and_falls_to_next_candidate(self):
        selected_key = self.packet["diagnostic_selection_key"]
        value = copy.deepcopy(self.input_packet)
        value["prior_selection_keys"] = [selected_key]
        packet = self._build(value)
        blocked = next(item for item in packet["candidate_audit"] if item["symbol"] == "005930")
        self.assertIn("DUPLICATE_SELECTION_KEY", blocked["reason_codes"])
        self.assertEqual("000660", packet["diagnostic_selected_symbol"])

    def test_threshold_and_position_limit_fail_closed(self):
        value = copy.deepcopy(self.input_packet)
        value["policy"]["thresholds"]["minimum_turnover_krw"] = 20_000
        value["open_position_count"] = 1
        packet = self._build(value)
        self.assertIsNone(packet["diagnostic_selected_symbol"])
        self.assertIn("PAPER_CANARY_POSITION_LIMIT_REACHED", packet["reason_codes"])
        self.assertTrue(all("TURNOVER_BELOW_MINIMUM" in row["reason_codes"] for row in packet["candidate_audit"]))
        self.assertEqual("NONE", packet["symbol"])

    def test_source_kind_mismatch_cannot_masquerade_as_natural(self):
        value = copy.deepcopy(self.input_packet)
        value["evidence_mode"] = "NATURAL"
        value["policy"]["status"] = "UNRATIFIED"
        packet = self._build(value)
        self.assertIsNone(packet["diagnostic_selected_symbol"])
        self.assertTrue(all("CANDIDATE_SOURCE_KIND_MISMATCH" in row["reason_codes"] for row in packet["candidate_audit"]))
        self.assertEqual("NONE", packet["symbol"])

    def test_resigned_output_tamper_is_rejected_by_full_rederivation(self):
        tampered = copy.deepcopy(self.packet)
        tampered["diagnostic_selected_symbol"] = "000660"
        tampered["packet_sha256"] = selector.payload_sha256({
            key: item for key, item in tampered.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            selector.KrxPaperCanaryEligibilityError,
            "OUTPUT_DERIVATION_MISMATCH",
        ):
            selector.validate_packet(tampered)

    def test_packet_validates_and_module_contains_no_network_or_broker_import(self):
        self.assertEqual(self.packet, selector.validate_packet(self.packet))
        source = (ROOT / "decision" / "krx_paper_canary_eligibility.py").read_text(
            encoding="utf-8"
        )
        for forbidden in ("import requests", "import httpx", "import ccxt", "koreainvestment"):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main()
