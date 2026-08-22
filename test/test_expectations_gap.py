#!/usr/bin/env python3
"""P8-09 Expectations Gap regression."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "expectations_gap.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("expectations_gap", SOURCE)
CONTRACT = MODULE.load_contract()


def base_input(**overrides):
    value = {
        "subject": "TSM",
        "decision_date": "2026-08-20",
        "generated_at": "2026-08-22T00:00:00Z",
    }
    value.update(overrides)
    return value


def category(direction="POSITIVE", note="synthetic evidence"):
    return {"direction": direction, "evidence_note": note}


class ExpectationsGapTests(unittest.TestCase):
    # ── authority ────────────────────────────────────────────────────
    def test_authority_dict_exact_values(self):
        self.assertEqual(CONTRACT["authority"], {
            "expectations_gap_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })
        packet = MODULE.build_packet(base_input(), CONTRACT)
        self.assertEqual(packet["authority"], CONTRACT["authority"])

    # ── the hard rule: absence of paid consensus feed never blocks ────
    def test_absent_public_estimates_never_blocks_and_is_not_consensus(self):
        packet = MODULE.build_packet(
            base_input(
                guidance_changes=category("POSITIVE"),
                backlog_or_new_orders=category("POSITIVE"),
            ),
            CONTRACT,
        )
        gap = packet["expectations_gap"]
        self.assertNotEqual(gap["market_expectation_basis"]["basis_type"], "CONSENSUS")
        self.assertEqual(gap["market_expectation_basis"]["basis_type"], "PROXY")
        self.assertIn("public_estimates", gap["missing_inputs"])

    def test_public_estimates_supplied_sets_consensus_basis(self):
        packet = MODULE.build_packet(
            base_input(public_estimates=category("POSITIVE")), CONTRACT
        )
        gap = packet["expectations_gap"]
        self.assertEqual(gap["market_expectation_basis"]["basis_type"], "CONSENSUS")
        self.assertNotIn("public_estimates", gap["missing_inputs"])

    # ── zero-input packet still builds, maximally honest ───────────────
    def test_zero_inputs_builds_valid_unknown_packet_not_an_error(self):
        packet = MODULE.build_packet(base_input(), CONTRACT)
        gap = packet["expectations_gap"]
        self.assertEqual(gap["status"], "UNKNOWN")
        self.assertEqual(gap["market_expectation_basis"]["basis_type"], "UNKNOWN")
        self.assertEqual(gap["confidence"], "LOW")
        self.assertEqual(gap["magnitude"], "UNKNOWN")
        self.assertEqual(sorted(gap["missing_inputs"]), sorted(CONTRACT["input_categories"]))
        MODULE.validate_packet(packet, CONTRACT)

    # ── status=UNKNOWN implies confidence=LOW (never HIGH/MEDIUM) ──────
    def test_unknown_status_requires_low_confidence(self):
        tampered = MODULE.build_packet(base_input(), CONTRACT)
        tampered = copy.deepcopy(tampered)
        tampered["expectations_gap"]["confidence"] = "HIGH"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ExpectationsGapError, "OUTPUT_UNKNOWN_STATUS_REQUIRES_LOW_CONFIDENCE"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── closed enums reject out-of-vocabulary values ────────────────────
    def test_direction_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.ExpectationsGapError, "CATEGORY_DIRECTION_INVALID"):
            MODULE.build_packet(
                base_input(guidance_changes=category("BULLISH")), CONTRACT
            )

    def test_output_status_enum_is_closed(self):
        packet = MODULE.build_packet(
            base_input(guidance_changes=category("POSITIVE")), CONTRACT
        )
        tampered = copy.deepcopy(packet)
        tampered["expectations_gap"]["status"] = "SUPER_BULLISH"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.ExpectationsGapError, "OUTPUT_STATUS_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_output_magnitude_and_confidence_and_basis_type_enums_closed(self):
        packet = MODULE.build_packet(
            base_input(guidance_changes=category("POSITIVE")), CONTRACT
        )
        for path, bad_value, code in (
            (("expectations_gap", "magnitude"), "HUGE", "OUTPUT_MAGNITUDE_INVALID"),
            (("expectations_gap", "confidence"), "VERY_HIGH", "OUTPUT_CONFIDENCE_INVALID"),
        ):
            tampered = copy.deepcopy(packet)
            node = tampered
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = bad_value
            tampered["packet_sha256"] = MODULE.payload_sha256(
                {k: v for k, v in tampered.items() if k != "packet_sha256"}
            )
            with self.assertRaisesRegex(MODULE.ExpectationsGapError, code):
                MODULE.validate_packet(tampered, CONTRACT)
        tampered = copy.deepcopy(packet)
        tampered["expectations_gap"]["market_expectation_basis"]["basis_type"] = "PAID_FEED"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.ExpectationsGapError, "OUTPUT_BASIS_TYPE_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── anti-lookahead ───────────────────────────────────────────────
    def test_earnings_reaction_future_event_date_rejected(self):
        with self.assertRaisesRegex(
            MODULE.ExpectationsGapError, "EARNINGS_REACTION_EVENT_DATE_IN_FUTURE"
        ):
            MODULE.build_packet(
                base_input(earnings_reaction={
                    "event_date": "2026-08-25",
                    "direction": "POSITIVE",
                    "evidence_note": "post-earnings pop",
                }),
                CONTRACT,
            )

    def test_earnings_reaction_past_event_date_accepted(self):
        packet = MODULE.build_packet(
            base_input(earnings_reaction={
                "event_date": "2026-08-14",
                "direction": "POSITIVE",
                "evidence_note": "post-earnings pop",
            }),
            CONTRACT,
        )
        self.assertIn("earnings_reaction:POSITIVE", packet["expectations_gap"]["gap_reasons"])

    # ── never invent LARGE/status from thin evidence ────────────────────
    def test_thin_single_input_never_produces_large_magnitude(self):
        packet = MODULE.build_packet(
            base_input(guidance_changes=category("POSITIVE")), CONTRACT
        )
        self.assertNotEqual(packet["expectations_gap"]["magnitude"], "LARGE")

    def test_broad_agreement_can_produce_large_magnitude_and_high_confidence(self):
        packet = MODULE.build_packet(
            base_input(
                guidance_changes=category("POSITIVE"),
                backlog_or_new_orders=category("POSITIVE"),
                capex_or_expansion=category("POSITIVE"),
                pricing_or_lead_time=category("POSITIVE"),
            ),
            CONTRACT,
        )
        gap = packet["expectations_gap"]
        self.assertEqual(gap["status"], "POSITIVE")
        self.assertEqual(gap["magnitude"], "LARGE")
        self.assertEqual(gap["confidence"], "HIGH")

    # ── minimal valid packet builds and passes validate_packet ─────────
    def test_minimal_packet_builds_and_validates(self):
        packet = MODULE.build_packet(base_input(), CONTRACT)
        MODULE.validate_packet(packet, CONTRACT)

    # ── determinism + tamper detection ──────────────────────────────────
    def test_deterministic_and_tamper_evident(self):
        value = base_input(
            guidance_changes=category("POSITIVE"),
            revenue_margin_trend=category("NEGATIVE"),
        )
        first = MODULE.build_packet(copy.deepcopy(value), CONTRACT)
        second = MODULE.build_packet(copy.deepcopy(value), CONTRACT)
        self.assertEqual(first, second)

        tampered = copy.deepcopy(first)
        tampered["expectations_gap"]["gap_reasons"].append("INJECTED")
        with self.assertRaisesRegex(MODULE.ExpectationsGapError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_output_fields_are_exactly_the_specified_set(self):
        packet = MODULE.build_packet(base_input(), CONTRACT)
        self.assertEqual(set(packet), {
            "schema_version", "contract_version", "generated_at", "subject",
            "decision_date", "expectations_gap", "authority", "packet_sha256",
        })
        self.assertEqual(packet["schema_version"], "expectations_gap_packet/1")
        self.assertEqual(packet["contract_version"], "expectations_gap/1")
        self.assertEqual(set(packet["expectations_gap"]), {
            "status", "magnitude", "confidence", "market_expectation_basis",
            "atlas_forward_basis", "gap_reasons", "missing_inputs",
        })

    # ── CLI is offline and write-outside-repo only ──────────────────────
    def test_cli_is_offline_and_writes_only_outside_repository(self):
        import ast
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps(base_input(guidance_changes=category("POSITIVE"))), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "expectations_gap_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
