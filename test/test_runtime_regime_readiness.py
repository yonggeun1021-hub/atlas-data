#!/usr/bin/env python3
"""Runtime Regime readiness regression.

The subject conveys *unavailability* of a runtime Regime decision.  These
tests therefore assert the opposite of a normal happy path: that nothing here
can ever produce a Regime, a direction, a confidence, a signed axis direction,
or a runtime-ready market -- including when coverage is genuinely 5/5.
"""

import ast
import copy
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "regime" / "runtime_regime_readiness.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("runtime_regime_readiness", SOURCE)
OUTPUT = MODULE.OUTPUT
AUTHORITY = MODULE.AUTHORITY
MARKETS = list(OUTPUT.load_contract()["markets"])
AXES = list(OUTPUT.load_contract()["required_axes"])

GENERATED_AT = "2026-08-21T12:00:00Z"
OBSERVATION_DATE = "2026-08-20"
AVAILABLE_AT = "2026-08-21T00:00:00Z"


def defined_axis_spec(axis):
    """A structurally valid DEFINED axis.

    This proves axis PRESENCE only, which is exactly what the ratified
    evidence-only contract permits; it is not, and cannot become, an
    interpreted axis value.
    """
    return {
        "status": "DEFINED",
        "observation_date": OBSERVATION_DATE,
        "available_at": AVAILABLE_AT,
        "transform_version": "runtime_regime_readiness_test/v1",
        "evidence": {
            "uri": f"test://runtime-regime-readiness/{axis.lower()}",
            "sha256": "0" * 64,
        },
        "warnings": [],
    }


def regime_outputs(*, full_coverage_markets=()):
    outputs = {}
    for market in MARKETS:
        factors = (
            {axis: defined_axis_spec(axis) for axis in AXES}
            if market in full_coverage_markets
            else None
        )
        outputs[market] = OUTPUT.build_unknown_output(market, GENERATED_AT, factors)
    return outputs


class RuntimeRegimeReadinessTests(unittest.TestCase):
    def build(self, *, full_coverage_markets=()):
        return MODULE.build_readiness(
            regime_outputs(full_coverage_markets=full_coverage_markets),
            GENERATED_AT,
        )

    def test_contract_is_readiness_only_and_opens_no_authority(self):
        packet = self.build()
        self.assertEqual(packet["contract_version"], "runtime_regime_readiness/v1")
        self.assertEqual(
            packet["contract_mode"], "RUNTIME_READINESS_ONLY_NO_REGIME_DECISION"
        )
        authority = packet["authority"]
        self.assertTrue(authority["readiness_inventory_only"])
        for key, value in authority.items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_runtime_decision_is_unavailable_and_regime_stays_unknown(self):
        packet = self.build()
        self.assertFalse(packet["runtime_decision_available"])
        self.assertEqual(packet["status"], "RUNTIME_REGIME_DECISION_UNAVAILABLE")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertEqual(packet["direction"], "UNKNOWN")
        self.assertIsNone(packet["confidence"])
        self.assertIsNone(packet["final_decision"])
        self.assertEqual(packet["summary"]["runtime_ready_market_count"], 0)
        self.assertEqual(
            packet["summary"]["signed_normalization_ratified_market_count"], 0
        )
        for row in packet["markets"]:
            self.assertFalse(row["runtime_decision_available"], row["market"])
            self.assertEqual(row["regime"], "UNKNOWN", row["market"])
            self.assertEqual(row["direction"], "UNKNOWN", row["market"])
            self.assertIsNone(row["confidence"], row["market"])

    def test_replay_completeness_is_never_marked_runtime_ready(self):
        packet = self.build()
        self.assertTrue(packet["historical_replay_is_not_runtime_ready"])
        self.assertEqual(
            packet["common_v1_binding"]["contract_mode"],
            "SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED",
        )
        self.assertEqual(
            packet["common_v1_binding"]["pit_replay_acceptance"], "NOT_ACCEPTED"
        )
        self.assertIn(
            "COMMON_V1_REPLAY_MODE:SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED",
            packet["p1_regime_decision_unavailable_reasons"],
        )

    def test_blockers_name_the_exact_markets_axes_and_policy_components(self):
        packet = self.build()
        reasons = packet["p1_regime_decision_unavailable_reasons"]
        self.assertIn("P1_REGIME_DECISION_NOT_RUNTIME_WIRED", reasons)
        for component in AUTHORITY.load_contract()["required_policy_components"]:
            self.assertIn(f"REGIME_POLICY_COMPONENT_MISSING:{component}", reasons)
        for market in MARKETS:
            self.assertIn(
                f"SIGNED_NORMALIZATION_POLICY_UNRATIFIED:{market}", reasons
            )
            self.assertIn(f"PIT_REPLAY_NOT_ACCEPTED:{market}", reasons)
            self.assertIn(f"MINIMUM_COVERAGE_NOT_MET:{market}", reasons)
            for axis in AXES:
                self.assertIn(f"AXIS_UNDEFINED:{market}:{axis}", reasons)

    def test_full_coverage_still_produces_no_signed_direction_or_regime(self):
        market = MARKETS[0]
        packet = self.build(full_coverage_markets=(market,))
        row = next(item for item in packet["markets"] if item["market"] == market)
        self.assertTrue(row["coverage"]["minimum_coverage_met"])
        self.assertEqual(row["coverage"]["ratio"], f"{len(AXES)}/{len(AXES)}")
        self.assertEqual(row["coverage"]["gate_result"], "COVERAGE_MET")
        # 5/5 coverage is the ONLY thing that changes.  Direction is still
        # unassignable because no market has a ratified signed-normalization
        # policy, so the boundary stays blocked and the Regime stays UNKNOWN.
        self.assertEqual(
            row["signed_axis_gate"]["normalization_status"],
            "BLOCKED_SIGNED_NORMALIZATION_UNRATIFIED",
        )
        self.assertEqual(
            row["signed_axis_gate"]["signed_normalization_policy_status"],
            "UNRATIFIED_ABSENT",
        )
        self.assertFalse(row["signed_axis_gate"]["replay_step_emitted"])
        self.assertTrue(
            all(
                value is None
                for value in row["signed_axis_gate"]["signed_directions"].values()
            )
        )
        self.assertEqual(
            row["decision_gate"]["decision_status"], "BLOCKED_POLICY_UNRATIFIED"
        )
        self.assertEqual(row["regime"], "UNKNOWN")
        self.assertFalse(row["runtime_decision_available"])
        self.assertEqual(packet["summary"]["coverage_met_markets"], [market])
        self.assertEqual(packet["summary"]["runtime_ready_market_count"], 0)
        reasons = packet["p1_regime_decision_unavailable_reasons"]
        self.assertNotIn(f"MINIMUM_COVERAGE_NOT_MET:{market}", reasons)
        self.assertIn(f"SIGNED_NORMALIZATION_POLICY_UNRATIFIED:{market}", reasons)
        self.assertIn(
            f"DECISION_AUTHORITY_BLOCKED:{market}:BLOCKED_POLICY_UNRATIFIED", reasons
        )

    def test_registry_acceptance_state_is_reported_verbatim(self):
        packet = self.build()
        expected = {
            "US": "BLOCKED_FINISHED_SESSION_TTL_PIT_REPLAY",
            "KR": "BLOCKED_SIGNED_NORMALIZATION_TTL_PIT_REPLAY",
            "CRYPTO": "BLOCKED_OVERALL_FRESHNESS_PIT_REPLAY",
        }
        registry_market = {"US": "US", "KR": "KRX", "CRYPTO": "CRYPTO"}
        reasons = packet["p1_regime_decision_unavailable_reasons"]
        for row in packet["markets"]:
            market = row["market"]
            self.assertEqual(row["registry_market"], registry_market[market])
            self.assertEqual(
                row["signed_axis_gate"]["acceptance_status"], expected[market]
            )
            self.assertEqual(
                row["signed_axis_gate"]["pit_replay_acceptance"], "NOT_ACCEPTED"
            )
            self.assertIn(
                f"MARKET_ACCEPTANCE_BLOCKED:{market}:{expected[market]}", reasons
            )

    def test_output_is_deterministic_and_round_trips_through_its_validator(self):
        first = self.build()
        second = self.build()
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.validate_readiness(first), first)

    def test_self_rehashed_availability_or_regime_tamper_fails_closed(self):
        for field, value in (
            ("runtime_decision_available", True),
            ("regime", "RISK_ON"),
            ("direction", "IMPROVING"),
            ("confidence", "1"),
            ("historical_replay_is_not_runtime_ready", False),
            ("decision_status", "READY"),
        ):
            with self.subTest(field=field):
                packet = self.build()
                packet[field] = value
                packet["packet_sha256"] = MODULE.payload_sha256({
                    key: item for key, item in packet.items()
                    if key != "packet_sha256"
                })
                with self.assertRaisesRegex(
                    MODULE.RuntimeRegimeReadinessError, "OUTPUT_DERIVATION_MISMATCH"
                ):
                    MODULE.validate_readiness(packet)

    def test_supplied_coverage_never_claims_source_authentication(self):
        packet = self.build(full_coverage_markets=MARKETS)
        self.assertEqual(packet["source_validation_scope"], "STRUCTURAL_ENVELOPE_ONLY")
        self.assertIs(packet["source_evidence_bytes_verified"], False)
        self.assertEqual(packet["summary"]["coverage_met_market_count"], 3)
        self.assertIs(packet["runtime_decision_available"], False)
        packet["source_evidence_bytes_verified"] = True
        packet["packet_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in packet.items() if k != "packet_sha256"}
        )
        with self.assertRaises(MODULE.RuntimeRegimeReadinessError):
            MODULE.validate_readiness(packet)

    def test_boolean_numeric_alias_with_original_hash_is_rejected(self):
        packet = self.build()
        packet["authority"]["order_authorized"] = 0
        with self.assertRaises(MODULE.RuntimeRegimeReadinessError):
            MODULE.validate_readiness(packet)

    def test_self_rehashed_blocker_removal_fails_closed(self):
        packet = self.build()
        packet["p1_regime_decision_unavailable_reasons"] = [
            "P1_REGIME_DECISION_NOT_RUNTIME_WIRED"
        ]
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: item for key, item in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RuntimeRegimeReadinessError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_readiness(packet)

    def test_injected_signed_direction_in_a_market_row_fails_closed(self):
        packet = self.build()
        packet["markets"][0]["signed_axis_gate"]["signed_directions"]["TREND"] = (
            "POSITIVE"
        )
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: item for key, item in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RuntimeRegimeReadinessError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_readiness(packet)

    def test_market_substitution_and_generated_at_drift_fail_closed(self):
        outputs = regime_outputs()
        outputs["US"] = outputs["KR"]
        with self.assertRaisesRegex(
            MODULE.RuntimeRegimeReadinessError, "REGIME_OUTPUT_MARKET_MISMATCH"
        ):
            MODULE.build_readiness(outputs, GENERATED_AT)

        outputs = regime_outputs()
        outputs["CRYPTO"] = OUTPUT.build_unknown_output(
            "CRYPTO", "2026-08-21T13:00:00Z"
        )
        with self.assertRaisesRegex(
            MODULE.RuntimeRegimeReadinessError,
            "REGIME_OUTPUT_GENERATED_AT_MISMATCH",
        ):
            MODULE.build_readiness(outputs, GENERATED_AT)

    def test_missing_market_fails_closed(self):
        outputs = regime_outputs()
        outputs.pop("CRYPTO")
        with self.assertRaisesRegex(
            MODULE.RuntimeRegimeReadinessError, "REGIME_OUTPUT_KEYS_MISMATCH"
        ):
            MODULE.build_readiness(outputs, GENERATED_AT)

    def test_unavailable_reasons_are_downstream_reason_code_shaped(self):
        packet = self.build()
        reasons = MODULE.unavailable_reasons(packet)
        self.assertEqual(reasons, sorted(set(reasons)))
        self.assertTrue(reasons)
        for reason in reasons:
            self.assertRegex(reason, r"^[A-Z0-9][A-Z0-9_.:-]{2,159}$")

    def test_blockers_are_independent_of_the_invocation_timestamp(self):
        """The blocker list is semantic; the packet hash is not.

        `packet_sha256` covers regime_output/v1 envelopes, which embed the
        caller's generated_at (and per-axis age_seconds), so it necessarily
        changes per invocation.  Downstream consumers fingerprint semantic
        content, so only the blockers may be forwarded -- this pins that
        distinction.
        """
        later = "2026-08-21T13:00:00Z"
        first = self.build()
        second = MODULE.build_readiness(
            {
                market: OUTPUT.build_unknown_output(market, later)
                for market in MARKETS
            },
            later,
        )
        self.assertEqual(
            MODULE.unavailable_reasons(first), MODULE.unavailable_reasons(second)
        )
        self.assertNotEqual(first["packet_sha256"], second["packet_sha256"])
        for reason in MODULE.unavailable_reasons(first):
            self.assertNotIn("SHA256", reason)

    def test_unavailable_reasons_reject_an_availability_claim(self):
        packet = self.build()
        tampered = copy.deepcopy(packet)
        tampered["runtime_decision_available"] = True
        with self.assertRaises(MODULE.RuntimeRegimeReadinessError):
            MODULE.unavailable_reasons(tampered)

    def test_module_is_offline_and_writes_nothing(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        source = SOURCE.read_text(encoding="utf-8")
        for prohibited in ("write_text", "open(", "mkdir"):
            self.assertNotIn(prohibited, source)


if __name__ == "__main__":
    unittest.main()
