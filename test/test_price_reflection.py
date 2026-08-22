#!/usr/bin/env python3
"""P8-10 Price Reflection regression."""

import ast
import copy
import importlib.util
import inspect
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "price_reflection.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("price_reflection", SOURCE)
CONTRACT = MODULE.load_contract()


def base_kwargs(**overrides):
    value = {
        "subject": "TSM",
        "decision_date": "2026-08-22",
        "generated_at": "2026-08-22T00:00:00Z",
        "contract": CONTRACT,
    }
    value.update(overrides)
    return value


class PriceReflectionTests(unittest.TestCase):
    # ── authority ────────────────────────────────────────────────────
    def test_authority_dict_exact_values(self):
        self.assertEqual(CONTRACT["authority"], {
            "price_reflection_assembly_only": True,
            "rule_authority_substitution_authorized": False,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })
        packet = MODULE.build_packet(**base_kwargs())
        self.assertEqual(packet["authority"], CONTRACT["authority"])

    # ── Rule 1: staleness forces UNKNOWN unconditionally ────────────────
    def test_stale_price_as_of_forces_unknown_despite_strong_positive_inputs(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-01T19:59:00Z",  # 21 days before decision_date
            recent_return_windows={"1m": "25", "3m": "30", "6m": "40"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            valuation_context={"position_in_range": "HIGH"},
            event_reaction={"event_date": "2026-07-20", "direction": "POSITIVE", "reaction_magnitude_pct": "10"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["status"], "UNKNOWN")
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertTrue(any("STALE" in reason for reason in pr["reasons"]))

    def test_missing_price_as_of_forces_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            recent_return_windows={"1m": "25"},
            relative_strength={"vs_market": "20"},
        ))
        pr = packet["price_reflection"]
        self.assertEqual(pr["status"], "UNKNOWN")
        self.assertEqual(pr["confidence"], "UNKNOWN")
        self.assertIn("price_as_of", pr["missing_inputs"])

    def test_fresh_price_within_ceiling_is_not_forced_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",  # 1 day before decision_date
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertNotEqual(packet["price_reflection"]["status"], "UNKNOWN")

    def test_price_as_of_in_future_is_rejected(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "PRICE_AS_OF_IN_FUTURE"):
            MODULE.build_packet(**base_kwargs(price_as_of="2026-08-23T00:00:00Z"))

    def test_custom_freshness_ceiling_is_honored(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-01T19:59:00Z",
            freshness_ceiling_days=30,
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertNotEqual(packet["price_reflection"]["status"], "UNKNOWN")

    # ── Rule 2: structurally no thesis/fundamental parameter ───────────
    def test_builder_signature_has_no_thesis_or_fundamental_parameter(self):
        params = list(inspect.signature(MODULE.build_packet).parameters)
        for bad in MODULE.FORBIDDEN_PARAMETER_SUBSTRINGS:
            self.assertFalse(
                any(bad in name.lower() for name in params),
                f"forbidden substring {bad!r} found in builder parameters {params}",
            )
        MODULE.assert_no_fundamental_parameters()

    # ── Rule 3: sharp rally alone is OVEREXTENDED, never REJECTED ──────
    def test_sharp_rally_near_high_is_overextended_not_rejected(self):
        self.assertNotIn("REJECTED", CONTRACT["allowed_status"])
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "25"},
            relative_strength={"vs_market": "20", "position_vs_recent_high_pct": "1"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["status"], "OVEREXTENDED")

    # ── Rule 4: OVEREXTENDED documented as timing risk, not "bad company" ─
    def test_overextended_documented_as_timing_not_business_quality(self):
        doc = (ROOT / "docs" / "price_reflection_contract.md").read_text(encoding="utf-8")
        self.assertIn("does not mean the", doc.lower())
        self.assertIn("entry-timing risk", doc.lower())

    # ── Rule 5: never a P5 Rule PASS/FAIL-shaped result ────────────────
    def test_no_rule_verdict_shaped_field(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        flat = json.dumps(packet)
        for token in ('"PASS"', '"FAIL"', '"result":', '"pass_fail"'):
            self.assertNotIn(token, flat)

    # ── Rule 6 (part of 3): enum has no REJECTED-like value ─────────────
    def test_status_vocabulary_has_no_rejected_value(self):
        self.assertEqual(sorted(CONTRACT["allowed_status"]), sorted([
            "UNDER_REFLECTED", "PARTIALLY_REFLECTED", "FULLY_REFLECTED",
            "OVEREXTENDED", "UNKNOWN",
        ]))

    # ── Rule 7: insufficient Korea data forces UNKNOWN ──────────────────
    def test_korea_partial_fields_forces_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            subject="298040",
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},  # missing vs_market / position_vs_recent_high
            data_source_scope="KRX_OFFICIAL",
        ))
        self.assertEqual(packet["price_reflection"]["status"], "UNKNOWN")
        self.assertEqual(packet["price_reflection"]["confidence"], "UNKNOWN")

    def test_korea_complete_fields_is_not_forced_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            subject="298040",
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "2", "position_vs_recent_high_pct": "10"},
            data_source_scope="KRX_OFFICIAL",
        ))
        self.assertNotEqual(packet["price_reflection"]["status"], "UNKNOWN")

    def test_data_source_scope_propagates_verbatim(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        ))
        self.assertEqual(packet["price_reflection"]["data_source_scope"], "IEX_ONLY_PARTIAL_US_MARKET")

    def test_default_data_source_scope_is_unknown(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(packet["price_reflection"]["data_source_scope"], "UNKNOWN")

    # ── closed enums reject out-of-vocabulary values ────────────────────
    def test_status_enum_is_closed(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        tampered = copy.deepcopy(packet)
        tampered["price_reflection"]["status"] = "MOONING"
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_STATUS_INVALID"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_data_source_scope_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "DATA_SOURCE_SCOPE_INVALID"):
            MODULE.build_packet(**base_kwargs(data_source_scope="BLOOMBERG_TERMINAL"))

    def test_valuation_position_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "VALUATION_CONTEXT_POSITION_INVALID"):
            MODULE.build_packet(**base_kwargs(valuation_context={"position_in_range": "SKY_HIGH"}))

    def test_event_reaction_direction_enum_is_closed(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_DIRECTION_INVALID"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "event_date": "2026-08-01", "direction": "MOONSHOT", "reaction_magnitude_pct": "5",
            }))

    def test_event_reaction_future_event_date_rejected(self):
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "EVENT_REACTION_EVENT_DATE_IN_FUTURE"):
            MODULE.build_packet(**base_kwargs(event_reaction={
                "event_date": "2026-08-25", "direction": "POSITIVE", "reaction_magnitude_pct": "5",
            }))

    # ── minimal packet builds and validates ─────────────────────────────
    def test_minimal_packet_builds_and_validates(self):
        packet = MODULE.build_packet(**base_kwargs())
        MODULE.validate_packet(packet, CONTRACT)
        self.assertEqual(packet["price_reflection"]["status"], "UNKNOWN")

    def test_output_fields_are_exactly_the_specified_set(self):
        packet = MODULE.build_packet(**base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        ))
        self.assertEqual(set(packet), {
            "schema_version", "contract_version", "generated_at", "subject",
            "decision_date", "price_reflection", "authority", "packet_sha256",
        })
        self.assertEqual(packet["schema_version"], "price_reflection_packet/1")
        self.assertEqual(packet["contract_version"], "price_reflection/1")
        self.assertEqual(set(packet["price_reflection"]), {
            "status", "confidence", "price_as_of", "relative_strength",
            "recent_return_windows", "event_reaction", "valuation_context",
            "reasons", "missing_inputs", "data_source_scope",
        })

    # ── determinism + tamper detection ──────────────────────────────────
    def test_deterministic_and_tamper_evident(self):
        kwargs = base_kwargs(
            price_as_of="2026-08-21T19:59:00Z",
            recent_return_windows={"1m": "5", "3m": "8"},
            relative_strength={"vs_market": "3", "position_vs_recent_high_pct": "12"},
            valuation_context={"position_in_range": "MID"},
            data_source_scope="IEX_ONLY_PARTIAL_US_MARKET",
        )
        first = MODULE.build_packet(**kwargs)
        second = MODULE.build_packet(**kwargs)
        self.assertEqual(first, second)

        tampered = copy.deepcopy(first)
        tampered["price_reflection"]["reasons"].append("INJECTED")
        with self.assertRaisesRegex(MODULE.PriceReflectionError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    # ── CLI is offline and write-outside-repo only ──────────────────────
    def test_cli_is_offline_and_writes_only_outside_repository(self):
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
            input_path.write_text(json.dumps({
                "subject": "TSM", "decision_date": "2026-08-22",
                "generated_at": "2026-08-22T00:00:00Z",
                "price_as_of": "2026-08-21T19:59:00Z",
                "recent_return_windows": {"1m": "5"},
                "relative_strength": {"vs_market": "3"},
            }), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "price_reflection_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
