#!/usr/bin/env python3
"""P6-06 fail-closed Defensive Action Decision readiness regression."""

import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "defensive_action_decision.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("defensive_action_decision", SOURCE)
CONTRACT = MODULE.load_contract()
CASH = load_module("p606_cash_fixture", ROOT / "test" / "test_cash_exposure_action.py")
HEDGE = load_module(
    "p606_hedge_fixture", ROOT / "test" / "test_hedge_instrument_eligibility.py"
)
BEAR = load_module(
    "p606_bear_fixture", ROOT / "test" / "test_bear_hedge_risk_budget.py"
)
LONG_SHORT = load_module(
    "p606_long_short_fixture", ROOT / "test" / "test_long_short_invariant.py"
)
INVERSE = load_module(
    "p606_inverse_fixture", ROOT / "test" / "test_regime_inverse_invariant.py"
)
CAPITAL_FLOW_ENGINE = load_module(
    "p606_capital_flow_engine_fixture",
    ROOT / "portfolio" / "capital_flow_posture_reference.py",
)

# The real, currently-committed P2-COM-02 packet.  Its own generated_at is
# derived deterministically from the real US/Korea/Crypto market-data files'
# generated_at fields (see regime/paper_regime_reference.py), never wall-clock
# "now" -- so it is stable for a given commit.  The P6-06 bundle baseline is
# pinned to the calendar day *after* that real evidence date (at a fixed
# hour), read dynamically instead of a hardcoded literal, so this test never
# goes stale as the daily crons advance that evidence (the same staleness
# class documented for test_daily_orchestrator.py) and never has to reason
# about what time of day the real evidence itself landed at -- it is always
# a full day earlier than the bundle baseline, regardless.
P2_FLOW_ENGINE_PACKET = CAPITAL_FLOW_ENGINE.build_reference()
_REAL_EVIDENCE_DATE = P2_FLOW_ENGINE_PACKET["generated_at"][:10]
AS_OF_DATE = (
    dt.date.fromisoformat(_REAL_EVIDENCE_DATE) + dt.timedelta(days=1)
).isoformat()
GENERATED_AT = AS_OF_DATE + "T02:00:00Z"
FUTURE_GENERATED_AT = (
    dt.date.fromisoformat(AS_OF_DATE) + dt.timedelta(days=1)
).isoformat() + "T01:00:00Z"


def source_packet(name):
    cash_markets = {
        "CASH_EXPOSURE_US": "US",
        "CASH_EXPOSURE_KOREA": "KR",
        "CASH_EXPOSURE_CRYPTO": "CRYPTO",
    }
    inverse_markets = {
        "INVERSE_US": "US",
        "INVERSE_KOREA": "KR",
        "INVERSE_CRYPTO": "CRYPTO",
    }
    if name in cash_markets:
        return CASH.MODULE.build_packet(
            CASH.upstream_output(cash_markets[name]), CASH.CONTRACT
        )
    if name in inverse_markets:
        upstream = INVERSE.REGIME.build_unknown_output(
            inverse_markets[name], "2026-08-21T01:00:00Z"
        )
        return INVERSE.MODULE.build_packet(upstream, INVERSE.CONTRACT)
    if name == "HEDGE_ELIGIBILITY":
        return HEDGE.MODULE.build_packet(
            HEDGE.registry(), "2026-08-21", HEDGE.CONTRACT
        )
    if name == "BEAR_HEDGE_BUDGET":
        return BEAR.MODULE.build_packet(
            BEAR.budget_set(), "2026-08-21", BEAR.CONTRACT
        )
    if name == "LONG_SHORT_INVARIANT":
        return LONG_SHORT.MODULE.build_packet(
            LONG_SHORT.upstream_packet(), LONG_SHORT.CONTRACT
        )
    if name == "P2_FLOW_ENGINE":
        return copy.deepcopy(P2_FLOW_ENGINE_PACKET)
    raise AssertionError(name)


READINESS = MODULE.RUNTIME_REGIME_READINESS


def runtime_regime_readiness():
    """The real P1 runtime readiness packet, over evidence-free envelopes.

    Axis evidence presence varies day to day; the blockers this asserts on are
    the structural ones that do not, so this stays a contract regression
    rather than a snapshot of today's coverage.
    """
    outputs = {
        market: READINESS.OUTPUT.build_unknown_output(market, GENERATED_AT)
        for market in READINESS.OUTPUT.load_contract()["markets"]
    }
    return READINESS.build_readiness(outputs, GENERATED_AT)


def bundle(*, p6_available=True, p1_regime_reasons=None):
    packets = {}
    reasons = {}
    unsupported = set(CONTRACT["unavailable_only_source_slots"])
    for name in CONTRACT["source_order"]:
        if name in unsupported:
            packets[name] = None
            if name == "P1_REGIME_DECISION" and p1_regime_reasons is not None:
                reasons[name] = list(p1_regime_reasons)
            else:
                reasons[name] = [f"{name}_PRODUCTION_CONTRACT_UNAVAILABLE"]
        elif p6_available:
            packets[name] = source_packet(name)
            reasons[name] = []
        else:
            packets[name] = None
            reasons[name] = [f"{name}_NOT_CONNECTED"]
    return packets, reasons


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class DefensiveActionDecisionTests(unittest.TestCase):
    def build(self, *, p6_available=True):
        packets, reasons = bundle(p6_available=p6_available)
        return MODULE.build_packet(
            packets,
            reasons,
            AS_OF_DATE,
            GENERATED_AT,
            contract=CONTRACT,
        )

    def test_contract_is_zero_capital_and_has_no_decision_or_order_authority(self):
        self.assertEqual(CONTRACT["scope"], "ZERO_CAPITAL_DECISION_REVIEW")
        self.assertEqual(CONTRACT["runtime_decision_status"], "BLOCKED")
        self.assertEqual(CONTRACT["decision_vocabulary"], [
            "CASH_PRIORITY", "REDUCE_REVIEW", "HEDGE_REVIEW",
            "INVERSE_REVIEW", "NO_ACTION",
        ])
        self.assertTrue(CONTRACT["authority"]["readiness_inventory_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_current_upstream_gaps_are_blocked_not_no_action(self):
        packet = self.build()
        self.assertEqual(packet["status"], "DEFENSIVE_ACTION_READINESS_BLOCKED")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["summary"]["available_source_count"], 10)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 2)
        self.assertEqual(packet["summary"]["no_action"], None)
        self.assertTrue(all(row["eligible"] is None for row in packet["decisions"]))
        self.assertTrue(all(row["review_proposal"] is None for row in packet["decisions"]))
        no_action = next(
            row for row in packet["decisions"] if row["decision"] == "NO_ACTION"
        )
        self.assertIn(
            "MISSING_OR_UNEVALUATED_INPUT_IS_NOT_NO_ACTION", no_action["reasons"]
        )

    def test_p6_sources_are_semantically_validated_and_only_supply_evidence(self):
        packet = self.build()
        rows = {row["name"]: row for row in packet["sources"]}
        self.assertEqual(rows["CASH_EXPOSURE_US"]["source_market"], "US")
        self.assertEqual(rows["INVERSE_KOREA"]["source_market"], "KR")
        hedge = next(
            row for row in packet["decisions"] if row["decision"] == "HEDGE_REVIEW"
        )
        self.assertGreater(len(hedge["evidence_packet_sha256"]), 0)
        self.assertIsNone(hedge["eligible"])
        self.assertIsNone(packet["selected_instrument"])
        self.assertIsNone(packet["action_proposal"])
        self.assertEqual(packet["order_intents"], [])

    def test_p2_flow_engine_is_connected_while_regime_and_ledger_stay_unavailable(self):
        packet = self.build()
        rows = {row["name"]: row for row in packet["sources"]}
        self.assertEqual(rows["P2_FLOW_ENGINE"]["availability"], "AVAILABLE")
        self.assertEqual(
            rows["P2_FLOW_ENGINE"]["source_packet_sha256"],
            P2_FLOW_ENGINE_PACKET["payload_sha256"],
        )
        self.assertEqual(rows["P1_REGIME_DECISION"]["availability"], "UNAVAILABLE")
        self.assertEqual(rows["P2_FLOW_LEDGER"]["availability"], "UNAVAILABLE")
        # connecting a source must not itself unlock any decision or authority
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIsNone(packet["selected_action"])
        self.assertNotIn("P2_FLOW_ENGINE_UNAVAILABLE", packet["unresolved_boundaries"])
        self.assertIn("P1_REGIME_DECISION_UNAVAILABLE", packet["unresolved_boundaries"])
        self.assertIn("P2_FLOW_LEDGER_UNAVAILABLE", packet["unresolved_boundaries"])

    def test_p1_regime_readiness_supplies_exact_unavailable_blockers(self):
        readiness = runtime_regime_readiness()
        derived = MODULE.p1_regime_decision_unavailable_reasons(readiness)
        packets, reasons = bundle(p1_regime_reasons=derived)
        packet = MODULE.build_packet(
            packets, reasons, AS_OF_DATE, GENERATED_AT, contract=CONTRACT
        )
        rows = {row["name"]: row for row in packet["sources"]}

        # Exact blockers replace the opaque placeholder ...
        stored = packet["unavailable_reasons"]["P1_REGIME_DECISION"]
        self.assertEqual(stored, derived)
        self.assertNotIn("P1_REGIME_DECISION_PRODUCTION_CONTRACT_UNAVAILABLE", stored)
        self.assertIn("P1_REGIME_DECISION_NOT_RUNTIME_WIRED", stored)
        self.assertIn(
            "COMMON_V1_REPLAY_MODE:SHADOW_PIT_REPLAY_ONLY_RUNTIME_NOT_WIRED", stored
        )
        # The readiness packet_sha256 is deliberately NOT carried here: it
        # covers generated_at-tainted envelopes, and consumers fingerprint
        # this packet's semantic content.
        self.assertFalse(
            any("SHA256" in reason for reason in stored), stored
        )
        self.assertTrue(
            any(
                reason.startswith("SIGNED_NORMALIZATION_POLICY_UNRATIFIED:")
                for reason in stored
            )
        )

        # ... and change nothing about availability or authority.
        self.assertEqual(rows["P1_REGIME_DECISION"]["availability"], "UNAVAILABLE")
        self.assertIsNone(rows["P1_REGIME_DECISION"]["source_packet_sha256"])
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIsNone(packet["selected_action"])
        self.assertEqual(packet["order_intents"], [])
        self.assertIn("P1_REGIME_DECISION_UNAVAILABLE", packet["unresolved_boundaries"])
        self.assertEqual(packet["summary"]["unavailable_source_count"], 2)
        self.assertTrue(
            all(
                "SOURCE_UNAVAILABLE:P1_REGIME_DECISION" in row["reasons"]
                for row in packet["decisions"]
                if row["decision"] != "NO_ACTION"
            )
        )
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

    def test_p1_regime_readiness_tamper_cannot_soften_the_blockers(self):
        readiness = runtime_regime_readiness()
        claimed_available = copy.deepcopy(readiness)
        claimed_available["runtime_decision_available"] = True
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError, "P1_REGIME_READINESS_INVALID"
        ):
            MODULE.p1_regime_decision_unavailable_reasons(claimed_available)

        shortened = copy.deepcopy(readiness)
        shortened["p1_regime_decision_unavailable_reasons"] = [
            "P1_REGIME_DECISION_NOT_RUNTIME_WIRED"
        ]
        shortened["packet_sha256"] = READINESS.payload_sha256({
            key: value for key, value in shortened.items()
            if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError, "P1_REGIME_READINESS_INVALID"
        ):
            MODULE.p1_regime_decision_unavailable_reasons(shortened)

    def test_p1_regime_slot_still_refuses_a_readiness_packet_as_a_source(self):
        # Readiness is a blocker report, never an upstream decision packet.
        packets, reasons = bundle()
        packets["P1_REGIME_DECISION"] = runtime_regime_readiness()
        reasons["P1_REGIME_DECISION"] = []
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "SOURCE_PACKET_NOT_YET_SUPPORTED:P1_REGIME_DECISION",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT, contract=CONTRACT
            )

    def test_p2_flow_engine_semantic_tamper_fails_closed(self):
        packets, reasons = bundle()
        tampered = copy.deepcopy(P2_FLOW_ENGINE_PACKET)
        tampered["total_exposure_review"]["invested_target_pct"] = 80
        unsigned = {k: v for k, v in tampered.items() if k != "payload_sha256"}
        tampered["payload_sha256"] = MODULE.CAPITAL_FLOW_ENGINE.payload_sha256(unsigned)
        packets["P2_FLOW_ENGINE"] = tampered
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "SOURCE_SEMANTIC_INVALID:P2_FLOW_ENGINE",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT,
                contract=CONTRACT,
            )

    def test_p1_or_p2_packet_cannot_bypass_missing_production_contract(self):
        packets, reasons = bundle()
        packets["P1_REGIME_DECISION"] = {"packet_sha256": "0" * 64}
        reasons["P1_REGIME_DECISION"] = []
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "SOURCE_PACKET_NOT_YET_SUPPORTED:P1_REGIME_DECISION",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT,
                contract=CONTRACT,
            )

    def test_self_rehashed_source_semantic_tamper_fails_closed(self):
        packets, reasons = bundle()
        source = packets["CASH_EXPOSURE_US"]
        source["reasons"][0] = "TAMPERED_REASON"
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "SOURCE_SEMANTIC_INVALID:CASH_EXPOSURE_US",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT,
                contract=CONTRACT,
            )

    def test_market_slot_substitution_fails_closed(self):
        packets, reasons = bundle()
        packets["CASH_EXPOSURE_KOREA"] = source_packet("CASH_EXPOSURE_US")
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "SOURCE_MARKET_MISMATCH:CASH_EXPOSURE_KOREA",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT,
                contract=CONTRACT,
            )

    def test_future_source_fails_closed(self):
        packets, reasons = bundle()
        future = CASH.REGIME.build_unknown_output("US", FUTURE_GENERATED_AT)
        packets["CASH_EXPOSURE_US"] = CASH.MODULE.build_packet(
            future, CASH.CONTRACT
        )
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "SOURCE_FROM_FUTURE:CASH_EXPOSURE_US",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT,
                contract=CONTRACT,
            )

    def test_unratified_policy_packet_is_rejected(self):
        packets, reasons = bundle()
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError,
            "UNRATIFIED_POLICY_PACKET_FORBIDDEN",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT,
                policy_packet={"status": "RATIFIED"}, contract=CONTRACT,
            )

    def test_self_rehashed_output_action_or_no_action_tamper_fails_closed(self):
        packet = self.build()
        packet["decisions"][-1]["eligible"] = True
        packet["summary"]["no_action"] = True
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_output_is_deterministic_and_preserves_exact_lineage(self):
        first = self.build()
        second = self.build()
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.validate_packet(first, CONTRACT), first)
        self.assertEqual(
            first["lineage"]["source_packet_sha256"],
            {
                row["name"]: row["source_packet_sha256"]
                for row in first["sources"]
            },
        )

    def test_all_p6_unavailable_remains_blocked_without_fabrication(self):
        packet = self.build(p6_available=False)
        self.assertEqual(packet["summary"]["available_source_count"], 0)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 12)
        self.assertEqual(packet["summary"]["evaluated_decision_count"], 0)
        self.assertIsNone(packet["selected_action"])
        self.assertIsNone(packet["risk_budget_allocation"])
        self.assertIsNone(packet["target_exposures"])
        self.assertIsNone(packet["position_size"])

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            packets, reasons = bundle()
            bundle_path = write_json(tmp / "bundle.json", {
                "source_packets": packets,
                "unavailable_reasons": reasons,
                "policy_packet": None,
            })
            output_path = tmp / "nested" / "readiness.json"
            self.assertEqual(
                MODULE.run(
                    bundle_path, AS_OF_DATE, GENERATED_AT, output_path
                ),
                0,
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["decision_status"], "BLOCKED")
            self.assertEqual(list(output_path.parent.glob(".readiness.json.*")), [])

            forbidden = ROOT / "data" / "defensive_action_readiness_test.json"
            self.assertEqual(
                MODULE.run(
                    bundle_path, AS_OF_DATE, GENERATED_AT, forbidden
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
