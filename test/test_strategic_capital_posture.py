#!/usr/bin/env python3
"""P7-12 fail-closed Strategic Capital Posture readiness regression."""

import ast
import base64
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "strategic_capital_posture.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("strategic_capital_posture", SOURCE)
CONTRACT = MODULE.load_contract()
P606_TEST = load_module(
    "p712_p606_fixture", ROOT / "test" / "test_defensive_action_decision.py"
)
CONCENTRATION_TEST = load_module(
    "p712_concentration_fixture",
    ROOT / "test" / "test_concentration_correlation_guard.py",
)
MARKET_THEME_TEST = load_module(
    "p712_market_theme_fixture",
    ROOT / "test" / "test_market_theme_exposure_budget.py",
)
CRYPTO_LIMIT_TEST = load_module(
    "p712_crypto_limit_fixture", ROOT / "test" / "test_crypto_exposure_limit.py"
)
PLANNED_LOSS_TEST = load_module(
    "p712_planned_loss_fixture", ROOT / "test" / "test_planned_loss_budget.py"
)
CURRENCY_TEST = load_module(
    "p712_currency_fixture", ROOT / "test" / "test_currency_exposure.py"
)


# P7-12's own baseline shares the P6-06 fixture's (dynamic) day and lands an
# hour after its fixed 02:00 generated_at, exactly like the original pinned
# "2026-08-21" convention -- see test_defensive_action_decision.py for why
# that day is derived from real evidence rather than hardcoded.
AS_OF_DATE = P606_TEST.AS_OF_DATE
GENERATED_AT = AS_OF_DATE + "T03:00:00Z"
FUTURE_AS_OF_DATE = (
    dt.date.fromisoformat(AS_OF_DATE) + dt.timedelta(days=1)
).isoformat()
FUTURE_GENERATED_AT = FUTURE_AS_OF_DATE + "T02:00:00Z"

# The real, currently-committed P2-COM-02 flow reference -- the same object
# P6-06 consumes as its P2_FLOW_ENGINE source, reused here rather than rebuilt
# so both consumers are pinned to one producer run.  Its generated_at is
# derived from real market-data evidence, so the "before the producer existed"
# baseline below is derived from it too instead of hardcoded.
FLOW_PACKET = P606_TEST.P2_FLOW_ENGINE_PACKET
FLOW_EVIDENCE_DATE = FLOW_PACKET["generated_at"][:10]
BEFORE_FLOW_AS_OF_DATE = (
    dt.date.fromisoformat(FLOW_EVIDENCE_DATE) - dt.timedelta(days=1)
).isoformat()
BEFORE_FLOW_GENERATED_AT = BEFORE_FLOW_AS_OF_DATE + "T00:00:00Z"

# The real production time geometry, reused from the merged P6-06 fixture so
# both consumers of the same scheduled run stay pinned to one definition:
# DECISION_DATE is `TZ=Asia/Seoul date +%F` (a KST business date) while
# GENERATED_AT is `date -u` (a UTC instant).  The weekday morning cron is
# `5 22 * * 0-4`, so 22:05Z is 07:05 of the *next* KST day and the run's
# generated_at is structurally one UTC calendar day behind its as_of_date.
# 15:00:00Z is exactly 00:00:00 KST of MORNING_AS_OF_DATE; one second earlier
# is still 23:59:59 KST of AS_OF_DATE.  The evening cron `30 9 * * 1-5` lands
# on the same UTC and KST day.
MORNING_AS_OF_DATE = P606_TEST.MORNING_AS_OF_DATE
MORNING_GENERATED_AT = P606_TEST.MORNING_GENERATED_AT
KST_MIDNIGHT_GENERATED_AT = P606_TEST.KST_MIDNIGHT_GENERATED_AT
LAST_KST_INSTANT_BEFORE_MIDNIGHT = P606_TEST.LAST_KST_INSTANT_BEFORE_MIDNIGHT
EVENING_GENERATED_AT = P606_TEST.EVENING_GENERATED_AT
# The P8-06 consumer chain of one morning run: the unified packet is generated
# a few minutes before the P6/P7 packets and the summary a few minutes after,
# all inside the same 22:00Z hour of the previous UTC day.
UNIFIED_MORNING_GENERATED_AT = AS_OF_DATE + "T22:00:00Z"
SUMMARY_MORNING_GENERATED_AT = AS_OF_DATE + "T22:35:00Z"


def flow_packet():
    return copy.deepcopy(FLOW_PACKET)


def defensive_packet(as_of=P606_TEST.AS_OF_DATE, generated_at=P606_TEST.GENERATED_AT):
    packets, reasons = P606_TEST.bundle()
    return P606_TEST.MODULE.build_packet(
        packets,
        reasons,
        as_of,
        generated_at,
        contract=P606_TEST.CONTRACT,
    )


def p7_source_packet(name):
    if name == "P2_CROSS_MARKET_FLOW":
        return flow_packet()
    if name == "P7_CONCENTRATION_GUARD":
        return CONCENTRATION_TEST.MODULE.build_packet(
            CONCENTRATION_TEST.input_packet(),
            CONCENTRATION_TEST.policy(),
            "2026-08-21",
            CONCENTRATION_TEST.CONTRACT,
        )
    if name == "P7_MARKET_THEME_BUDGET":
        return MARKET_THEME_TEST.MODULE.build_packet(
            MARKET_THEME_TEST.input_packet(),
            MARKET_THEME_TEST.policy(),
            "2026-08-21",
            MARKET_THEME_TEST.CONTRACT,
        )
    if name == "P7_CRYPTO_EXPOSURE_LIMIT":
        return CRYPTO_LIMIT_TEST.MODULE.build_packet(
            CRYPTO_LIMIT_TEST.input_packet(),
            CRYPTO_LIMIT_TEST.policy(),
            "2026-08-21",
            CRYPTO_LIMIT_TEST.CONTRACT,
        )
    if name == "P7_PLANNED_LOSS_BUDGET":
        return PLANNED_LOSS_TEST.MODULE.build_packet(
            PLANNED_LOSS_TEST.input_packet(),
            PLANNED_LOSS_TEST.constitution(),
            "2026-08-21",
            PLANNED_LOSS_TEST.CONTRACT,
        )
    if name == "P7_CURRENCY_EXPOSURE":
        return CURRENCY_TEST.MODULE.build_packet(
            CURRENCY_TEST.asset_master(),
            CURRENCY_TEST.snapshot(),
            CURRENCY_TEST.CONTRACT,
        )
    raise AssertionError(name)


def bundle(*, defensive_available=True, flow_available=False):
    packets = {}
    reasons = {}
    for name in CONTRACT["source_order"]:
        if name == "P6_DEFENSIVE_ACTION" and defensive_available:
            packets[name] = defensive_packet()
            reasons[name] = []
        elif name == "P2_CROSS_MARKET_FLOW" and flow_available:
            packets[name] = flow_packet()
            reasons[name] = []
        else:
            packets[name] = None
            reasons[name] = [f"{name}_NOT_CONNECTED_OR_UNRATIFIED"]
    return packets, reasons


def bundle_with_all_supported_sources():
    packets, reasons = bundle()
    for name in CONTRACT["source_specs"]:
        if name == "P6_DEFENSIVE_ACTION":
            continue
        packets[name] = p7_source_packet(name)
        reasons[name] = []
    return packets, reasons


def morning_bundle(*, all_supported=False):
    """The production morning geometry: the P6-06 source is built by the same
    run, so it carries the same KST as_of_date and the same UTC instant."""
    packets, reasons = (
        bundle_with_all_supported_sources() if all_supported else bundle()
    )
    packets["P6_DEFENSIVE_ACTION"] = defensive_packet(
        MORNING_AS_OF_DATE, MORNING_GENERATED_AT
    )
    return packets, reasons


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class StrategicCapitalPostureTests(unittest.TestCase):
    def build(self, *, defensive_available=True, flow_available=False):
        packets, reasons = bundle(
            defensive_available=defensive_available,
            flow_available=flow_available,
        )
        return MODULE.build_packet(
            packets,
            reasons,
            AS_OF_DATE,
            GENERATED_AT,
            contract=CONTRACT,
        )

    def test_contract_is_zero_capital_and_execution_authority_is_closed(self):
        self.assertEqual(CONTRACT["scope"], "ZERO_CAPITAL_CROSS_MARKET_BUDGET_READINESS")
        self.assertEqual(CONTRACT["runtime_status"], "BLOCKED")
        self.assertTrue(CONTRACT["authority"]["readiness_inventory_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_current_dependencies_are_blocked_and_missing_is_not_zero(self):
        packet = self.build()
        self.assertEqual(packet["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["summary"]["available_source_count"], 1)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 8)
        self.assertEqual(
            packet["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "cash_reserve", "hedge_budget", "max_gross_risk", "max_net_risk",
            "theme_headroom", "risk_posture", "allocation_proposal",
            "target_exposures", "position_sizes",
        ):
            self.assertIsNone(packet[key], key)
        self.assertEqual(packet["order_intents"], [])
        self.assertNotIn(0, packet["market_budget"].values())

    def test_sum_overlap_and_currency_are_visible_but_not_evaluated(self):
        packet = self.build()
        checks = {row["check"]: row for row in packet["constraint_checks"]}
        self.assertEqual(set(checks), {"ALLOCATION_SUM", "CURRENCY_BOUNDARY", "OVERLAP_EXPOSURE"})
        for row in checks.values():
            self.assertEqual(row["evaluation_status"], "NOT_EVALUATED")
            self.assertIsNone(row["result"])
            self.assertIsNone(row["observed"])
        self.assertEqual(packet["summary"]["evaluated_constraint_count"], 0)
        self.assertEqual(packet["summary"]["numeric_budget_field_count"], 0)

    def test_all_supported_p6_p7_packets_are_revalidated_but_do_not_create_budget(self):
        packets, reasons = bundle_with_all_supported_sources()
        packet = MODULE.build_packet(
            packets,
            reasons,
            AS_OF_DATE,
            GENERATED_AT,
            contract=CONTRACT,
        )
        self.assertEqual(packet["summary"]["available_source_count"], 7)
        self.assertEqual(
            packet["summary"]["unavailable_sources"],
            ["P1_REGIME_DECISION", "P2_ROTATION_STATE"],
        )
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertTrue(all(value is None for value in packet["market_budget"].values()))
        self.assertIsNone(packet["allocation_proposal"])

    def test_real_p2_flow_producer_packet_binds_with_its_exact_hash(self):
        baseline = self.build()
        packet = MODULE.build_packet(
            *bundle(flow_available=True),
            AS_OF_DATE,
            GENERATED_AT,
            contract=CONTRACT,
        )
        rows = {source["name"]: source for source in packet["sources"]}
        row = rows["P2_CROSS_MARKET_FLOW"]

        # Bound to the real producer packet, by its own identity field.
        self.assertEqual(row["availability"], "AVAILABLE")
        self.assertEqual(row["source_packet_sha256"], FLOW_PACKET["payload_sha256"])
        self.assertEqual(row["source_status"], FLOW_PACKET["status"])
        self.assertIn(
            row["source_status"],
            CONTRACT["source_specs"]["P2_CROSS_MARKET_FLOW"]["statuses"],
        )
        self.assertEqual(row["evidence_date"], FLOW_EVIDENCE_DATE)
        self.assertEqual(row["unavailable_reasons"], [])
        self.assertEqual(
            packet["lineage"]["source_packet_sha256"]["P2_CROSS_MARKET_FLOW"],
            FLOW_PACKET["payload_sha256"],
        )
        self.assertEqual(packet["summary"]["available_source_count"], 2)
        self.assertEqual(
            packet["summary"]["unavailable_sources"],
            [name for name in baseline["summary"]["unavailable_sources"]
             if name != "P2_CROSS_MARKET_FLOW"],
        )
        self.assertNotIn(
            "SOURCE_UNAVAILABLE:P2_CROSS_MARKET_FLOW", packet["binding_reasons"]
        )

        # Binding a source unlocks no budget, action, or authority.
        self.assertEqual(packet["status"], baseline["status"])
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(packet["market_budget"], baseline["market_budget"])
        self.assertEqual(packet["authority"], baseline["authority"])
        self.assertEqual(packet["authority"], CONTRACT["authority"])
        self.assertEqual(packet["constraint_checks"], baseline["constraint_checks"])
        self.assertEqual(packet["summary"]["numeric_budget_field_count"], 0)
        self.assertEqual(packet["summary"]["evaluated_constraint_count"], 0)
        for key in (
            "cash_reserve", "hedge_budget", "max_gross_risk", "max_net_risk",
            "theme_headroom", "risk_posture", "allocation_proposal",
            "target_exposures", "position_sizes",
        ):
            self.assertIsNone(packet[key], key)
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

    def test_orchestrator_consumes_actual_flow_row_without_opening_authority(self):
        orchestrator = load_module("p712_orchestrator_binding", ROOT / "briefing" / "daily_orchestrator.py")
        names = ("P2_FLOW_ENGINE", "DEFENSIVE_ACTION_DECISION", "CONCENTRATION_GUARD",
                 "MARKET_THEME_BUDGET", "CRYPTO_EXPOSURE_LIMIT", "PLANNED_LOSS_BUDGET", "PORTFOLIO_CURRENCY")
        rows = {name: orchestrator.component_row(name, "POLICY_BLOCKED", "INPUT_ABSENT") for name in names}
        rows["P2_FLOW_ENGINE"] = orchestrator.component_row(
            "P2_FLOW_ENGINE", "READY", "VALIDATED_REFERENCE", packet=FLOW_PACKET,
            validated=True, source_packet_sha256=FLOW_PACKET["payload_sha256"])
        row = orchestrator.build_strategic_capital_posture(rows, AS_OF_DATE, GENERATED_AT)
        self.assertTrue(row["validated"])
        flow = next(s for s in row["packet"]["sources"] if s["name"] == "P2_CROSS_MARKET_FLOW")
        self.assertEqual(flow["availability"], "AVAILABLE")
        self.assertEqual(flow["source_packet_sha256"], FLOW_PACKET["payload_sha256"])
        self.assertEqual(row["packet"]["authority"], CONTRACT["authority"])
        self.assertEqual(row["packet"]["decision_status"], "BLOCKED")
        self.assertEqual(row["packet"]["order_intents"], [])
        rows["P2_FLOW_ENGINE"] = orchestrator.component_row("P2_FLOW_ENGINE", "POLICY_BLOCKED", "INPUT_ABSENT")
        missing = orchestrator.build_strategic_capital_posture(rows, AS_OF_DATE, GENERATED_AT)
        self.assertIn("P2_CROSS_MARKET_FLOW", missing["packet"]["summary"]["unavailable_sources"])

    def test_unavailable_only_boundaries_track_the_contract_not_a_fixed_list(self):
        packet = self.build(flow_available=True)
        self.assertEqual(
            CONTRACT["unavailable_only_source_slots"],
            ["P1_REGIME_DECISION", "P2_ROTATION_STATE"],
        )
        for name in CONTRACT["unavailable_only_source_slots"]:
            self.assertIn(f"{name}_UNAVAILABLE", packet["unresolved_boundaries"])
        # A now-supported slot is a runtime availability fact, never a
        # structural "no production contract" boundary.
        self.assertNotIn(
            "P2_CROSS_MARKET_FLOW_UNAVAILABLE", packet["unresolved_boundaries"]
        )
        self.assertEqual(
            sorted(
                name
                for name in CONTRACT["source_order"]
                if f"{name}_UNAVAILABLE" in packet["unresolved_boundaries"]
            ),
            sorted(CONTRACT["unavailable_only_source_slots"]),
        )

    def test_missing_flow_source_stays_truthfully_unavailable_and_blocked(self):
        packet = self.build()
        rows = {source["name"]: source for source in packet["sources"]}
        row = rows["P2_CROSS_MARKET_FLOW"]
        self.assertEqual(row["availability"], "UNAVAILABLE")
        self.assertIsNone(row["source_packet_sha256"])
        self.assertIsNone(row["source_status"])
        self.assertEqual(
            row["unavailable_reasons"],
            ["P2_CROSS_MARKET_FLOW_NOT_CONNECTED_OR_UNRATIFIED"],
        )
        self.assertIn(
            "SOURCE_UNAVAILABLE:P2_CROSS_MARKET_FLOW", packet["binding_reasons"]
        )
        self.assertIn("P2_CROSS_MARKET_FLOW", packet["summary"]["unavailable_sources"])
        self.assertIsNone(
            packet["lineage"]["source_packet_sha256"]["P2_CROSS_MARKET_FLOW"]
        )
        # Missing input is never zero budget and never an action.
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertTrue(all(value is None for value in packet["market_budget"].values()))
        self.assertEqual(packet["order_intents"], [])

    def test_forged_flow_source_is_rejected_by_the_producer_validator(self):
        packets, reasons = bundle(flow_available=True)
        source = packets["P2_CROSS_MARKET_FLOW"]
        source["cross_market_flow"]["comparable_market_count"] += 1
        source["payload_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_SEMANTIC_INVALID:P2_CROSS_MARKET_FLOW:REFERENCE_REDERIVATION_MISMATCH",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT, contract=CONTRACT
            )

    def test_foreign_packet_cannot_occupy_the_flow_slot(self):
        packets, reasons = bundle(flow_available=True)
        packets["P2_CROSS_MARKET_FLOW"] = copy.deepcopy(
            packets["P6_DEFENSIVE_ACTION"]
        )
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_IDENTITY_INVALID:P2_CROSS_MARKET_FLOW",
        ):
            MODULE.build_packet(
                packets, reasons, AS_OF_DATE, GENERATED_AT, contract=CONTRACT
            )

    def test_flow_source_from_the_future_fails_closed(self):
        packets, reasons = bundle(defensive_available=False, flow_available=True)
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_FROM_FUTURE:P2_CROSS_MARKET_FLOW",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                BEFORE_FLOW_AS_OF_DATE,
                BEFORE_FLOW_GENERATED_AT,
                contract=CONTRACT,
            )

    def test_self_rehashed_p7_source_semantic_tamper_fails_closed(self):
        packets, reasons = bundle_with_all_supported_sources()
        source = packets["P7_CURRENCY_EXPOSURE"]
        source["summary"]["position_count"] += 1
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_SEMANTIC_INVALID:P7_CURRENCY_EXPOSURE",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_p6_packet_is_semantically_revalidated_at_consumption_boundary(self):
        packets, reasons = bundle()
        source = packets["P6_DEFENSIVE_ACTION"]
        source["summary"]["available_source_count"] += 1
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_SEMANTIC_INVALID:P6_DEFENSIVE_ACTION",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_source_slot_substitution_fails_closed(self):
        packets, reasons = bundle()
        packets["P7_CONCENTRATION_GUARD"] = copy.deepcopy(
            packets["P6_DEFENSIVE_ACTION"]
        )
        reasons["P7_CONCENTRATION_GUARD"] = []
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_IDENTITY_INVALID:P7_CONCENTRATION_GUARD",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_p1_or_p2_packet_cannot_bypass_missing_production_contract(self):
        for name in CONTRACT["unavailable_only_source_slots"]:
            with self.subTest(name=name):
                packets, reasons = bundle()
                packets[name] = {"packet_sha256": "0" * 64}
                reasons[name] = []
                with self.assertRaisesRegex(
                    MODULE.StrategicCapitalPostureError,
                    f"SOURCE_PACKET_NOT_YET_SUPPORTED:{name}",
                ):
                    MODULE.build_packet(
                        packets,
                        reasons,
                        AS_OF_DATE,
                        GENERATED_AT,
                        contract=CONTRACT,
                    )

    def test_future_source_fails_closed(self):
        packets, reasons = bundle()
        packets["P6_DEFENSIVE_ACTION"] = defensive_packet(
            FUTURE_AS_OF_DATE, FUTURE_GENERATED_AT
        )
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_FROM_FUTURE:P6_DEFENSIVE_ACTION",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                contract=CONTRACT,
            )

    def test_future_effective_availability_in_each_p7_source_fails_closed(self):
        for name in (
            "P7_CONCENTRATION_GUARD",
            "P7_MARKET_THEME_BUDGET",
            "P7_CRYPTO_EXPOSURE_LIMIT",
            "P7_PLANNED_LOSS_BUDGET",
            "P7_CURRENCY_EXPOSURE",
        ):
            with self.subTest(name=name):
                packets, reasons = bundle(defensive_available=False)
                packets[name] = p7_source_packet(name)
                reasons[name] = []
                with self.assertRaisesRegex(
                    MODULE.StrategicCapitalPostureError,
                    f"SOURCE_FROM_FUTURE:{name}",
                ):
                    MODULE.build_packet(
                        packets,
                        reasons,
                        "2026-08-21",
                        "2026-08-21T00:00:00Z",
                        contract=CONTRACT,
                    )

    def test_unratified_policy_packet_is_rejected(self):
        packets, reasons = bundle()
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "UNRATIFIED_POLICY_PACKET_FORBIDDEN",
        ):
            MODULE.build_packet(
                packets,
                reasons,
                AS_OF_DATE,
                GENERATED_AT,
                policy_packet={"status": "RATIFIED"},
                contract=CONTRACT,
            )

    def test_self_rehashed_output_budget_or_order_tamper_fails_closed(self):
        packet = self.build()
        packet["market_budget"]["US"] = 0.5
        packet["order_intents"] = [{"side": "BUY"}]
        packet["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in packet.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_output_is_deterministic_and_preserves_exact_lineage(self):
        first = self.build()
        second = self.build()
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.validate_packet(first, CONTRACT), first)
        self.assertEqual(
            first["lineage"]["source_packet_sha256"],
            {row["name"]: row["source_packet_sha256"] for row in first["sources"]},
        )

    def test_all_sources_unavailable_remains_blocked_without_fabrication(self):
        packet = self.build(defensive_available=False)
        self.assertEqual(packet["summary"]["available_source_count"], 0)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 9)
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIsNone(packet["risk_posture"])
        self.assertEqual(packet["order_intents"], [])

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

        with tempfile.TemporaryDirectory() as name:
            temp = Path(name)
            packets, reasons = bundle()
            bundle_path = write_json(temp / "bundle.json", {
                "source_packets": packets,
                "unavailable_reasons": reasons,
                "policy_packet": None,
            })
            output_path = temp / "nested" / "readiness.json"
            self.assertEqual(
                MODULE.run(
                    bundle_path,
                    AS_OF_DATE,
                    GENERATED_AT,
                    output_path,
                ),
                0,
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["decision_status"], "BLOCKED")
            self.assertEqual(list(output_path.parent.glob(".readiness.json.*")), [])

            forbidden = ROOT / "data" / "strategic_capital_posture_test.json"
            self.assertEqual(
                MODULE.run(
                    bundle_path,
                    AS_OF_DATE,
                    GENERATED_AT,
                    forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


class StrategicCapitalPostureKstBusinessDateTests(unittest.TestCase):
    """``as_of_date`` is a KST business date; ``generated_at`` is a UTC instant.

    The guard in ``_assemble`` must compare the two on the same basis, mirroring
    the merged P6-06 convention.  These pin the real scheduled-briefing
    geometry, which the same-UTC-day fixtures above never exercise.
    """

    def build(self, as_of_date, generated_at, *, packets=None, reasons=None):
        if packets is None:
            packets, reasons = bundle()
        return MODULE.build_packet(
            packets, reasons, as_of_date, generated_at, contract=CONTRACT
        )

    def semantic_body(self, packet):
        """The packet minus its own time keys and identity hash."""
        return MODULE.canonical_json({
            key: value for key, value in packet.items()
            if key not in {"as_of_date", "generated_at", "packet_sha256"}
        })

    def assert_readiness_only(self, packet):
        self.assertEqual(
            packet["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED"
        )
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(
            packet["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "cash_reserve", "hedge_budget", "max_gross_risk", "max_net_risk",
            "theme_headroom", "risk_posture", "allocation_proposal",
            "target_exposures", "position_sizes", "policy_packet",
        ):
            self.assertIsNone(packet[key], key)
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["summary"]["numeric_budget_field_count"], 0)
        self.assertEqual(packet["summary"]["evaluated_constraint_count"], 0)
        self.assertIn(
            "STRATEGIC_CAPITAL_POSTURE_POLICY_NOT_RATIFIED",
            packet["binding_reasons"],
        )
        self.assertIn(
            "NUMERIC_BUDGET_VALUES_NOT_AUTHORIZED", packet["binding_reasons"]
        )
        self.assertTrue(packet["authority"]["readiness_inventory_only"])
        for key, value in packet["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)
        self.assertEqual(packet["authority"], CONTRACT["authority"])
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

    def test_kst_morning_generation_window_builds_instead_of_failing_closed(self):
        # The exact production defect: 22:05Z is the next KST business day, so
        # the run's generated_at is a UTC calendar day behind its as_of_date.
        self.assertLess(MORNING_GENERATED_AT[:10], MORNING_AS_OF_DATE)
        self.assertEqual(
            MODULE._kst_business_date(MORNING_GENERATED_AT), MORNING_AS_OF_DATE
        )

        packets, reasons = morning_bundle()
        packet = self.build(
            MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            packets=packets, reasons=reasons,
        )
        self.assertEqual(packet["as_of_date"], MORNING_AS_OF_DATE)
        self.assertEqual(packet["generated_at"], MORNING_GENERATED_AT)
        # The P6-06 source of the same morning run binds with its own KST
        # business date, not a UTC-truncated one.
        row = {
            source["name"]: source for source in packet["sources"]
        }["P6_DEFENSIVE_ACTION"]
        self.assertEqual(row["availability"], "AVAILABLE")
        self.assertEqual(row["evidence_date"], MORNING_AS_OF_DATE)
        self.assertEqual(
            row["source_packet_sha256"],
            packets["P6_DEFENSIVE_ACTION"]["packet_sha256"],
        )
        self.assertEqual(packet["summary"]["available_source_count"], 1)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 8)
        # Building on the morning geometry must not add any authority.
        self.assert_readiness_only(packet)

    def test_morning_geometry_binds_every_supported_source_without_budget(self):
        packets, reasons = morning_bundle(all_supported=True)
        packet = self.build(
            MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            packets=packets, reasons=reasons,
        )
        self.assertEqual(packet["summary"]["available_source_count"], 7)
        self.assertEqual(
            packet["summary"]["unavailable_sources"],
            ["P1_REGIME_DECISION", "P2_ROTATION_STATE"],
        )
        self.assert_readiness_only(packet)

    def test_morning_and_evening_geometry_emit_the_same_readiness_body(self):
        morning = self.build(MORNING_AS_OF_DATE, MORNING_GENERATED_AT)
        evening = self.build(AS_OF_DATE, EVENING_GENERATED_AT)
        self.assertEqual(MODULE._kst_business_date(EVENING_GENERATED_AT), AS_OF_DATE)
        # Restoring the morning run changes only its own time keys: every
        # source row, constraint row, reason, invariant and authority flag is
        # byte-identical to the evening packet the run already emitted.
        self.assertEqual(self.semantic_body(morning), self.semantic_body(evening))

    def test_kst_midnight_is_the_exact_accept_reject_boundary(self):
        packet = self.build(MORNING_AS_OF_DATE, KST_MIDNIGHT_GENERATED_AT)
        self.assertEqual(packet["generated_at"], KST_MIDNIGHT_GENERATED_AT)
        self.assertEqual(
            MODULE._kst_business_date(KST_MIDNIGHT_GENERATED_AT), MORNING_AS_OF_DATE
        )
        self.assert_readiness_only(packet)

        # One second earlier is still the previous KST business day.
        self.assertEqual(
            MODULE._kst_business_date(LAST_KST_INSTANT_BEFORE_MIDNIGHT), AS_OF_DATE
        )
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError, "GENERATED_BEFORE_AS_OF_DATE"
        ):
            self.build(MORNING_AS_OF_DATE, LAST_KST_INSTANT_BEFORE_MIDNIGHT)
        # ... and is accepted for its own KST business day.
        self.assert_readiness_only(
            self.build(AS_OF_DATE, LAST_KST_INSTANT_BEFORE_MIDNIGHT)
        )

    def test_business_date_is_offset_aware_not_string_truncation(self):
        for instant in (
            GENERATED_AT,
            EVENING_GENERATED_AT,
            LAST_KST_INSTANT_BEFORE_MIDNIGHT,
            KST_MIDNIGHT_GENERATED_AT,
            MORNING_GENERATED_AT,
        ):
            aware = dt.datetime.strptime(instant, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=dt.timezone.utc
            )
            expected = aware.astimezone(
                dt.timezone(dt.timedelta(hours=9))
            ).date().isoformat()
            derived = MODULE._kst_business_date(instant)
            self.assertEqual(derived, expected)
            # Monotone relaxation: the KST business date is never earlier than
            # the UTC calendar date, so no input that used to pass can now fail.
            self.assertGreaterEqual(derived, instant[:10])

    def test_as_of_date_after_kst_business_date_of_generated_at_still_fails_closed(self):
        # The guard is corrected, not removed: a genuinely earlier KST day is
        # still rejected with the exact same error code.
        for later in (2, 3, 30):
            as_of = (
                dt.date.fromisoformat(AS_OF_DATE) + dt.timedelta(days=later)
            ).isoformat()
            with self.assertRaisesRegex(
                MODULE.StrategicCapitalPostureError, "GENERATED_BEFORE_AS_OF_DATE"
            ):
                self.build(as_of, MORNING_GENERATED_AT)

    def test_source_instant_and_date_guards_are_unchanged(self):
        # A source generated after the run instant is still refused on the
        # morning geometry: only the as_of/generated basis moved to KST.
        packets, reasons = morning_bundle()
        packets["P6_DEFENSIVE_ACTION"] = defensive_packet(
            MORNING_AS_OF_DATE, MORNING_AS_OF_DATE + "T01:00:00Z"
        )
        self.assertGreater(
            packets["P6_DEFENSIVE_ACTION"]["generated_at"], MORNING_GENERATED_AT
        )
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_FROM_FUTURE:P6_DEFENSIVE_ACTION",
        ):
            self.build(
                MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
                packets=packets, reasons=reasons,
            )

        # A source whose own evidence date is a later KST business day than the
        # decision date still fails closed, even though the relaxed guard now
        # accepts this run instant for the earlier date.
        packets, reasons = morning_bundle()
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_AFTER_AS_OF_DATE:P6_DEFENSIVE_ACTION",
        ):
            self.build(
                AS_OF_DATE, MORNING_GENERATED_AT,
                packets=packets, reasons=reasons,
            )

    def test_only_canonical_z_instants_and_iso_dates_are_accepted(self):
        # Invalid or non-canonical timestamps still fail on their own codes,
        # never inside the KST conversion.  No offset form is newly accepted.
        for generated_at in (
            MORNING_AS_OF_DATE,
            AS_OF_DATE + "T22:05:00",
            AS_OF_DATE + " 22:05:00Z",
            AS_OF_DATE + "T22:05:00+09:00",
            AS_OF_DATE + "T22:05:00.000Z",
            AS_OF_DATE + "T25:05:00Z",
            None,
        ):
            with self.assertRaisesRegex(
                MODULE.StrategicCapitalPostureError, "GENERATED_AT_INVALID"
            ):
                self.build(MORNING_AS_OF_DATE, generated_at)
        for as_of_date in (MORNING_GENERATED_AT, "2026-9-5", None):
            with self.assertRaisesRegex(
                MODULE.StrategicCapitalPostureError, "AS_OF_DATE_INVALID"
            ):
                self.build(as_of_date, MORNING_GENERATED_AT)

    def test_standalone_validator_reassembles_morning_packet_and_rejects_tamper(self):
        packets, reasons = morning_bundle()
        packet = self.build(
            MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            packets=packets, reasons=reasons,
        )
        # Reassembly: the standalone validator rebuilds the morning packet from
        # its own recorded inputs, with the contract loaded either way.
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)
        self.assertEqual(MODULE.validate_packet(copy.deepcopy(packet)), packet)

        tampered = copy.deepcopy(packet)
        tampered["market_budget"]["US"] = 0.5
        tampered["order_intents"] = [{"side": "BUY"}]
        tampered["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

        # Backdating the recorded instant past the KST boundary fails inside
        # reassembly, on the same corrected guard.
        backdated = copy.deepcopy(packet)
        backdated["generated_at"] = LAST_KST_INSTANT_BEFORE_MIDNIGHT
        backdated["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in backdated.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError, "GENERATED_BEFORE_AS_OF_DATE"
        ):
            MODULE.validate_packet(backdated, CONTRACT)

        # An untouched body with a substituted identity hash still fails.
        rehashed = copy.deepcopy(packet)
        rehashed["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError, "OUTPUT_PACKET_SHA_MISMATCH"
        ):
            MODULE.validate_packet(rehashed, CONTRACT)

    def test_previously_accepted_same_utc_day_packets_are_byte_identical(self):
        # Archived packets are revalidated by re-running _assemble, so every
        # geometry that already passed must still build the same bytes.
        for as_of_date, generated_at in (
            (AS_OF_DATE, GENERATED_AT),
            (AS_OF_DATE, EVENING_GENERATED_AT),
            (AS_OF_DATE, LAST_KST_INSTANT_BEFORE_MIDNIGHT),
        ):
            with self.subTest(generated_at=generated_at):
                first = self.build(as_of_date, generated_at)
                second = self.build(as_of_date, generated_at)
                self.assertEqual(
                    MODULE.canonical_json(first), MODULE.canonical_json(second)
                )
                self.assertEqual(first["as_of_date"], as_of_date)
                self.assertEqual(first["generated_at"], generated_at)
                self.assertEqual(
                    first["packet_sha256"],
                    MODULE.payload_sha256({
                        key: value for key, value in first.items()
                        if key != "packet_sha256"
                    }),
                )
                self.assert_readiness_only(first)

    def test_morning_p6_to_p7_to_action_risk_summary_consumer_path(self):
        # The actual downstream consumer of one morning run, with no fabricated
        # source: real P6-06 and P7-12 packets flow into P8-06, which
        # revalidates both through their own standalone validators.
        summary = load_module(
            "p712_action_risk_summary_consumer",
            ROOT / "briefing" / "action_risk_portfolio_summary.py",
        )
        summary_contract = summary.load_contract()
        unified_contract = summary.UNIFIED.load_contract()
        unified_packet = summary.UNIFIED.build_packet(
            components={name: None for name in unified_contract["component_order"]},
            unavailable_reasons={
                name: ["MORNING_CONSUMER_PATH_COMPONENT_NOT_CONNECTED"]
                for name in unified_contract["component_order"]
            },
            decision_date=MORNING_AS_OF_DATE,
            slot="morning",
            generated_at=UNIFIED_MORNING_GENERATED_AT,
        )

        packets, reasons = morning_bundle(all_supported=True)
        defensive = packets["P6_DEFENSIVE_ACTION"]
        posture = self.build(
            MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            packets=packets, reasons=reasons,
        )
        # The geometry that used to fail closed at P7-12.
        self.assertLess(posture["generated_at"][:10], posture["as_of_date"])
        self.assertEqual(defensive["as_of_date"], unified_packet["decision_date"])
        self.assertEqual(posture["as_of_date"], unified_packet["decision_date"])

        available = {
            "UNIFIED_DECISION": unified_packet,
            "DEFENSIVE_ACTION_DECISION": defensive,
            "STRATEGIC_CAPITAL_POSTURE": posture,
        }
        source_packets = {}
        unavailable_reasons = {}
        for name in summary_contract["source_order"]:
            source_packets[name] = available.get(name)
            unavailable_reasons[name] = (
                [] if name in available
                else ["MORNING_CONSUMER_PATH_SOURCE_NOT_CONNECTED"]
            )
        self.assertEqual(
            sorted(available), sorted(summary_contract["required_sources"])
        )

        result = summary.build_summary(
            source_packets,
            unavailable_reasons,
            SUMMARY_MORNING_GENERATED_AT,
            summary_contract,
        )
        self.assertEqual(result["decision_date"], MORNING_AS_OF_DATE)
        self.assertEqual(result["slot"], "morning")
        rows = {row["name"]: row for row in result["sources"]}
        for name in summary_contract["required_sources"]:
            self.assertEqual(rows[name]["availability"], "AVAILABLE", name)
            self.assertEqual(
                rows[name]["source_packet_sha256"],
                available[name]["packet_sha256"],
                name,
            )
        self.assertEqual(
            rows["STRATEGIC_CAPITAL_POSTURE"]["source_status"],
            "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED",
        )

        # Restoring the morning read model fabricates no action or authority:
        # unconnected sources stay explicitly unavailable.
        for row in result["sources"]:
            if row["name"] not in available:
                self.assertEqual(row["availability"], "UNAVAILABLE", row["name"])
                self.assertEqual(
                    row["unavailable_reasons"],
                    ["MORNING_CONSUMER_PATH_SOURCE_NOT_CONNECTED"],
                )
        for row in result["actions"]:
            self.assertEqual(row["evaluation_status"], "NOT_EVALUATED")
            self.assertIsNone(row["action"])
        self.assertIsNone(result["summary"]["nothing_action"])
        self.assertEqual(result["summary"]["evaluated_action_count"], 0)
        self.assertTrue(result["authority"]["briefing_read_model_only"])
        for key, value in result["authority"].items():
            if key != "briefing_read_model_only":
                self.assertFalse(value, key)
        self.assert_readiness_only(posture)


_UNSET = object()


class StrategicCapitalPostureRuntimeBlockerBindingTests(unittest.TestCase):
    """The unavailable-only ``P1_REGIME_DECISION`` slot may name the real gaps.

    Daily derivation version 2 forwards ``runtime_regime_readiness/v1``'s
    exact, independently re-derived and sorted blocker list into this module's
    P1 slot, exactly as the already-merged P6-06 consumer does.  The slot
    stays UNAVAILABLE, the packet stays BLOCKED, and this module keeps
    validating whatever it is handed: only the honesty of the reason list
    changes, never availability, budgets or authority.
    """

    def exact_reasons(self):
        """The real blocker list, taken through the same two validators the
        orchestrator uses: the readiness packet is re-derived and byte-compared
        before a single reason is forwarded."""
        return P606_TEST.MODULE.p1_regime_decision_unavailable_reasons(
            P606_TEST.runtime_regime_readiness()
        )

    def build(self, p1_reasons=_UNSET):
        packets, reasons = morning_bundle()
        if p1_reasons is not _UNSET:
            reasons["P1_REGIME_DECISION"] = p1_reasons
        return MODULE.build_packet(
            packets, reasons, MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            contract=CONTRACT,
        )

    def assert_grants_nothing(self, packet):
        self.assertEqual(
            packet["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED"
        )
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(
            packet["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "risk_posture", "cash_reserve", "hedge_budget", "max_gross_risk",
            "max_net_risk", "theme_headroom", "allocation_proposal",
            "target_exposures", "position_sizes", "policy_packet",
        ):
            self.assertIsNone(packet[key], key)
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["summary"]["numeric_budget_field_count"], 0)
        self.assertEqual(packet["summary"]["evaluated_constraint_count"], 0)
        self.assertEqual(packet["authority"], CONTRACT["authority"])
        for key, value in packet["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_exact_runtime_blockers_bind_without_creating_availability(self):
        exact = self.exact_reasons()
        self.assertIn("P1_REGIME_DECISION_NOT_RUNTIME_WIRED", exact)
        self.assertEqual(exact, sorted(set(exact)))

        packet = self.build(exact)
        self.assertEqual(packet["unavailable_reasons"]["P1_REGIME_DECISION"], exact)
        row = {
            source["name"]: source for source in packet["sources"]
        }["P1_REGIME_DECISION"]
        self.assertEqual(row["availability"], "UNAVAILABLE")
        self.assertEqual(row["unavailable_reasons"], exact)
        self.assertIsNone(row["source_status"])
        self.assertIsNone(row["evidence_date"])
        self.assertIsNone(row["source_packet_sha256"])
        self.assertIsNone(
            packet["lineage"]["source_packet_sha256"]["P1_REGIME_DECISION"]
        )
        self.assertIn(
            "P1_REGIME_DECISION", packet["summary"]["unavailable_sources"]
        )
        self.assertIn(
            "SOURCE_UNAVAILABLE:P1_REGIME_DECISION", packet["binding_reasons"]
        )
        self.assertIn(
            "P1_REGIME_DECISION_UNAVAILABLE", packet["unresolved_boundaries"]
        )
        # Independently reassembled from its own recorded inputs, and still
        # readiness-only.
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)
        self.assert_grants_nothing(packet)

    def test_only_sorted_reason_codes_travel_never_invocation_identity(self):
        readiness = P606_TEST.runtime_regime_readiness()
        exact = P606_TEST.MODULE.p1_regime_decision_unavailable_reasons(readiness)
        self.assertFalse(any("SHA256" in reason for reason in exact), exact)

        packet = self.build(exact)
        body = MODULE.canonical_json(packet)
        # The readiness packet's own identity hash covers regime_output/v1
        # envelopes that embed the caller's invocation generated_at, so it
        # must not reach this packet -- it would inject invocation noise into
        # every consumer that fingerprints this packet's semantic content.
        self.assertNotIn(readiness["packet_sha256"], body)
        self.assertNotIn(readiness["generated_at"], MODULE.canonical_json(exact))

    def test_tampered_readiness_cannot_shorten_the_forwarded_blockers(self):
        readiness = P606_TEST.runtime_regime_readiness()
        shortened = copy.deepcopy(readiness)
        shortened["p1_regime_decision_unavailable_reasons"] = [
            "P1_REGIME_DECISION_NOT_RUNTIME_WIRED"
        ]
        shortened["packet_sha256"] = P606_TEST.MODULE.payload_sha256({
            key: value for key, value in shortened.items() if key != "packet_sha256"
        })
        with self.assertRaises(ValueError):
            P606_TEST.MODULE.p1_regime_decision_unavailable_reasons(shortened)

        promoted = copy.deepcopy(readiness)
        promoted["runtime_decision_available"] = True
        promoted["packet_sha256"] = P606_TEST.MODULE.payload_sha256({
            key: value for key, value in promoted.items() if key != "packet_sha256"
        })
        with self.assertRaises(ValueError):
            P606_TEST.MODULE.p1_regime_decision_unavailable_reasons(promoted)

    def test_malformed_blocker_lists_fail_closed_at_this_boundary(self):
        for bad in ([], ["lowercase"], ["B_REASON", "A_REASON"],
                    ["A_REASON", "A_REASON"], [None], "A_REASON", None):
            with self.subTest(reasons=bad):
                with self.assertRaisesRegex(
                    MODULE.StrategicCapitalPostureError,
                    "UNAVAILABLE_REASONS_INVALID:P1_REGIME_DECISION",
                ):
                    self.build(bad)

    def test_the_generic_form_is_preserved_and_the_two_stay_distinguishable(self):
        # No blanket rollback: the legacy generic-reason packet still builds
        # exactly the same bytes it always did.
        generic = self.build()
        self.assertEqual(
            MODULE.canonical_json(generic), MODULE.canonical_json(self.build())
        )
        self.assertEqual(MODULE.validate_packet(generic, CONTRACT), generic)

        exact = self.build(self.exact_reasons())
        self.assertNotEqual(generic["packet_sha256"], exact["packet_sha256"])
        # The reason list and the source row that carries it are the ONLY
        # difference: no count, budget, constraint, lineage, invariant or
        # authority field moves with it.
        ignored = {"unavailable_reasons", "sources", "packet_sha256"}
        self.assertEqual(
            {k: v for k, v in generic.items() if k not in ignored},
            {k: v for k, v in exact.items() if k not in ignored},
        )
        generic_rows = {row["name"]: row for row in generic["sources"]}
        exact_rows = {row["name"]: row for row in exact["sources"]}
        for name in generic_rows:
            if name == "P1_REGIME_DECISION":
                continue
            self.assertEqual(generic_rows[name], exact_rows[name], name)
        self.assert_grants_nothing(exact)

    def test_exact_blockers_survive_the_p8_06_summary_consumer_path(self):
        summary = load_module(
            "p712_runtime_blocker_summary_consumer",
            ROOT / "briefing" / "action_risk_portfolio_summary.py",
        )
        summary_contract = summary.load_contract()
        unified_contract = summary.UNIFIED.load_contract()
        unified_packet = summary.UNIFIED.build_packet(
            components={name: None for name in unified_contract["component_order"]},
            unavailable_reasons={
                name: ["MORNING_CONSUMER_PATH_COMPONENT_NOT_CONNECTED"]
                for name in unified_contract["component_order"]
            },
            decision_date=MORNING_AS_OF_DATE,
            slot="morning",
            generated_at=UNIFIED_MORNING_GENERATED_AT,
        )
        packets, reasons = morning_bundle()
        reasons["P1_REGIME_DECISION"] = self.exact_reasons()
        posture = MODULE.build_packet(
            packets, reasons, MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            contract=CONTRACT,
        )
        available = {
            "UNIFIED_DECISION": unified_packet,
            "DEFENSIVE_ACTION_DECISION": packets["P6_DEFENSIVE_ACTION"],
            "STRATEGIC_CAPITAL_POSTURE": posture,
        }
        source_packets = {}
        unavailable_reasons = {}
        for name in summary_contract["source_order"]:
            source_packets[name] = available.get(name)
            unavailable_reasons[name] = (
                [] if name in available
                else ["MORNING_CONSUMER_PATH_SOURCE_NOT_CONNECTED"]
            )
        result = summary.build_summary(
            source_packets,
            unavailable_reasons,
            SUMMARY_MORNING_GENERATED_AT,
            summary_contract,
        )
        # P8-06 revalidates this packet through this module's own validator
        # and retains its exact hash, so the exact blockers reach the briefing
        # read model with their lineage intact.
        rows = {row["name"]: row for row in result["sources"]}
        self.assertEqual(
            rows["STRATEGIC_CAPITAL_POSTURE"]["source_packet_sha256"],
            posture["packet_sha256"],
        )
        embedded = result["source_packets"]["STRATEGIC_CAPITAL_POSTURE"]
        self.assertEqual(
            embedded["unavailable_reasons"]["P1_REGIME_DECISION"],
            self.exact_reasons(),
        )
        self.assertEqual(result["decision_date"], MORNING_AS_OF_DATE)
        self.assertEqual(
            rows["STRATEGIC_CAPITAL_POSTURE"]["source_status"],
            "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED",
        )
        for row in result["actions"]:
            self.assertEqual(row["evaluation_status"], "NOT_EVALUATED")
            self.assertIsNone(row["action"])
        self.assertIsNone(result["summary"]["nothing_action"])
        for key, value in result["authority"].items():
            if key != "briefing_read_model_only":
                self.assertFalse(value, key)
        self.assert_grants_nothing(posture)


# ---------------------------------------------------------------------------
# P2_ROTATION_STATE diagnostic blockers
# ---------------------------------------------------------------------------

READINESS = MODULE.ROTATION_STATE_READINESS
FROZEN_PATHS = READINESS.FROZEN_INPUT_PATHS
KOREA_POINTER_REL = READINESS.KOREA_ROTATION_POINTER_REL
READINESS_CONTRACT_REL = READINESS.READINESS_CONTRACT_REL
PROVENANCE_ERROR = READINESS.RotationStateLedgerReadinessProvenanceError
SEMANTIC_ERROR = READINESS.RotationStateLedgerReadinessSemanticError
# The live producer's own error type. It is a ValueError subclass and the
# provenance type is a RuntimeError subclass, so the two failure classes stay
# distinguishable in the assertions below rather than collapsing into one.
READINESS_ERROR = READINESS.RotationStateLedgerReadinessError
P2_EXACT_REASONS = [
    "P2_ROTATION_STATE:CRYPTO:APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    "P2_ROTATION_STATE:CRYPTO:EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "P2_ROTATION_STATE:CRYPTO:FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "P2_ROTATION_STATE:KOREA:APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    "P2_ROTATION_STATE:KOREA:EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "P2_ROTATION_STATE:KOREA:FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "P2_ROTATION_STATE:US:APPEND_ONLY_OPERATIONAL_LEDGER_EVIDENCE_MISSING",
    "P2_ROTATION_STATE:US:EXTERNAL_RATIFIED_STATE_POLICY_MISSING",
    "P2_ROTATION_STATE:US:FULL_PRODUCTION_ROTATION_PACKET_MISSING",
    "P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE",
]
P2_INVALID_REASONS = [
    "P2_ROTATION_READINESS_INVALID:VALIDATION_FAILED",
    "P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE",
]


def git(root, *args):
    completed = subprocess.run(
        ["git", *args],
        cwd=str(root),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return completed.stdout.decode("utf-8")


def real_source_bytes():
    """The three required inputs exactly as this repository committed them."""
    return {relative: (ROOT / relative).read_bytes() for relative in FROZEN_PATHS}


def build_source_repo(directory, sources=None):
    """A real, throwaway Git repository holding the three required inputs.

    Real objects, real trees, real ancestry -- the provenance checks below are
    exercised against git itself rather than against a mock of it. A value of
    None means the path is genuinely never committed.
    """
    root = Path(directory)
    sources = real_source_bytes() if sources is None else sources
    git(root, "init", "-q")
    git(root, "config", "user.email", "atlas-test@example.invalid")
    git(root, "config", "user.name", "Atlas Test")
    git(root, "config", "commit.gpgsign", "false")
    # An unrelated per-fixture marker, so two fixtures created in the same
    # second with identical inputs cannot produce the same commit oid. It is
    # not one of the three pinned paths and is never read.
    (root / ".atlas-fixture").write_text(str(root), encoding="utf-8")
    for relative, data in sources.items():
        if data is None:
            continue
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    # -f so a user-level excludes file cannot silently leave a required input
    # uncommitted and turn every case below into an unrelated ABSENT.
    git(root, "add", "-A", "-f")
    git(root, "commit", "-q", "-m", "atlas frozen input fixture")
    return root


def resigned_korea_pointer(data: bytes, as_of_date="1999-01-01") -> bytes:
    """Valid-looking Korea pointer bytes with the INNER self-hash recomputed.

    The tampered file is internally consistent -- it would pass the producer's
    own field/self-hash/semantic validation if it were ever reached -- so the
    only thing that can reject it is the pinned commit tree and blob identity.
    """
    pointer = json.loads(data.decode("utf-8"))
    pointer["as_of_date"] = as_of_date
    pointer.pop("payload_sha256")
    pointer["payload_sha256"] = READINESS.payload_sha256(pointer)
    return json.dumps(pointer, ensure_ascii=False, sort_keys=True).encode("utf-8")


class P2RotationStateFrozenInputProvenanceTests(unittest.TestCase):
    """Immutable, Git-backed replay of P2-05's three committed inputs.

    Provenance is proved BEFORE semantics: repository boundary, trusted
    ancestry, commit tree, blob oid, recomputed blob hash and raw bytes. Every
    failure in that stage is a hard failure -- it can never surface as the
    semantic-invalid diagnostic, because "we could not prove this input" and
    "we proved this input and it is invalid" are different facts.
    """

    def repo(self, sources=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return build_source_repo(directory.name, sources)

    def envelope(self, root):
        return READINESS.capture_readiness_inputs(root)

    def head(self, root):
        return git(root, "rev-parse", "HEAD").strip()

    # -- capture -------------------------------------------------------------

    def test_capture_pins_the_exact_committed_blobs(self):
        root = self.repo()
        envelope = self.envelope(root)
        self.assertEqual(
            set(envelope), {"schema_version", "source_commit", "files"}
        )
        self.assertEqual(
            envelope["schema_version"], "p2_rotation_readiness_inputs/1"
        )
        self.assertEqual(envelope["source_commit"], self.head(root))
        self.assertEqual(set(envelope["files"]), set(FROZEN_PATHS))
        for relative, entry in envelope["files"].items():
            with self.subTest(relative=relative):
                self.assertEqual(entry["state"], "PRESENT")
                self.assertEqual(
                    entry["blob_oid"],
                    git(root, "rev-parse", f"HEAD:{relative}").strip(),
                )
                self.assertEqual(
                    base64.b64decode(entry["content_base64"], validate=True),
                    (root / relative).read_bytes(),
                )
        # Deterministic at one HEAD.
        self.assertEqual(envelope, self.envelope(root))

    def test_capture_refuses_a_dirty_or_uncommitted_source(self):
        root = self.repo()
        pointer = root / KOREA_POINTER_REL
        pointer.write_bytes(resigned_korea_pointer(pointer.read_bytes()))
        with self.assertRaisesRegex(PROVENANCE_ERROR, "EVIDENCE_WORKTREE_DIRTY"):
            self.envelope(root)

    def test_capture_outside_a_repository_boundary_fails_closed(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        with self.assertRaises(PROVENANCE_ERROR):
            READINESS.capture_readiness_inputs(directory.name)

    # -- tamper --------------------------------------------------------------

    def test_resigned_source_bytes_still_mismatch_the_pinned_blob(self):
        root = self.repo()
        envelope = self.envelope(root)
        original = envelope["files"][KOREA_POINTER_REL]
        forged_bytes = resigned_korea_pointer(
            base64.b64decode(original["content_base64"], validate=True)
        )
        self.assertNotEqual(
            forged_bytes,
            base64.b64decode(original["content_base64"], validate=True),
        )

        # Bytes swapped, stored blob_oid left alone: the recomputed blob hash
        # no longer matches the oid the tree pins.
        swapped = copy.deepcopy(envelope)
        swapped["files"][KOREA_POINTER_REL]["content_base64"] = base64.b64encode(
            forged_bytes
        ).decode("ascii")
        with self.assertRaisesRegex(PROVENANCE_ERROR, "FROZEN_INPUT_BLOB_HASH_MISMATCH"):
            READINESS.evaluate_frozen_readiness_inputs(swapped, root)

        # Bytes AND stored blob_oid both re-signed to agree with each other:
        # the commit tree still pins the original oid.
        resigned = copy.deepcopy(swapped)
        resigned["files"][KOREA_POINTER_REL]["blob_oid"] = READINESS._git_blob_oid(
            forged_bytes
        )
        with self.assertRaisesRegex(PROVENANCE_ERROR, "FROZEN_INPUT_BLOB_OID_MISMATCH"):
            READINESS.evaluate_frozen_readiness_inputs(resigned, root)

    def test_resigned_contract_bytes_are_rejected_the_same_way(self):
        root = self.repo()
        envelope = self.envelope(root)
        contract = json.loads(
            base64.b64decode(
                envelope["files"][READINESS_CONTRACT_REL]["content_base64"],
                validate=True,
            ).decode("utf-8")
        )
        contract["markets"] = ["US"]
        forged_bytes = json.dumps(contract, sort_keys=True).encode("utf-8")
        forged = copy.deepcopy(envelope)
        forged["files"][READINESS_CONTRACT_REL] = {
            "state": "PRESENT",
            "blob_oid": READINESS._git_blob_oid(forged_bytes),
            "content_base64": base64.b64encode(forged_bytes).decode("ascii"),
        }
        with self.assertRaisesRegex(PROVENANCE_ERROR, "FROZEN_INPUT_BLOB_OID_MISMATCH"):
            READINESS.evaluate_frozen_readiness_inputs(forged, root)

    def test_an_absent_tag_cannot_hide_a_committed_entry(self):
        root = self.repo()
        envelope = self.envelope(root)
        hidden = copy.deepcopy(envelope)
        hidden["files"][KOREA_POINTER_REL] = {
            "state": "ABSENT",
            "blob_oid": None,
            "content_base64": None,
        }
        with self.assertRaisesRegex(
            PROVENANCE_ERROR, "FROZEN_INPUT_ABSENT_HIDES_COMMITTED_ENTRY"
        ):
            READINESS.evaluate_frozen_readiness_inputs(hidden, root)

    def test_a_present_tag_cannot_invent_an_uncommitted_entry(self):
        sources = real_source_bytes()
        real_pointer = sources[KOREA_POINTER_REL]
        sources[KOREA_POINTER_REL] = None
        root = self.repo(sources)
        envelope = self.envelope(root)
        self.assertEqual(envelope["files"][KOREA_POINTER_REL]["state"], "ABSENT")

        invented = copy.deepcopy(envelope)
        invented["files"][KOREA_POINTER_REL] = {
            "state": "PRESENT",
            "blob_oid": READINESS._git_blob_oid(real_pointer),
            "content_base64": base64.b64encode(real_pointer).decode("ascii"),
        }
        with self.assertRaisesRegex(
            PROVENANCE_ERROR, "FROZEN_INPUT_PRESENT_NOT_IN_COMMIT_TREE"
        ):
            READINESS.evaluate_frozen_readiness_inputs(invented, root)

    def test_an_untrusted_orphan_commit_is_rejected_by_ancestry(self):
        root = self.repo()
        envelope = self.envelope(root)
        # A real, locally fabricated commit object carrying the SAME tree, so
        # every tree/blob/byte check would pass -- and it is still refused,
        # because it is not reachable from the trusted validation HEAD.
        tree = git(root, "rev-parse", "HEAD^{tree}").strip()
        orphan = git(root, "commit-tree", tree, "-m", "unreachable").strip()
        self.assertNotEqual(orphan, envelope["source_commit"])
        forged = dict(envelope, source_commit=orphan)
        with self.assertRaisesRegex(
            PROVENANCE_ERROR, "FROZEN_INPUT_SOURCE_COMMIT_NOT_TRUSTED_ANCESTOR"
        ):
            READINESS.evaluate_frozen_readiness_inputs(forged, root)

    def test_a_missing_object_is_a_hard_failure_not_a_fetch_or_a_fallback(self):
        root = self.repo()
        envelope = self.envelope(root)
        forged = dict(envelope, source_commit="b" * 40)
        with self.assertRaisesRegex(
            PROVENANCE_ERROR, "FROZEN_INPUT_SOURCE_COMMIT_OBJECT_MISSING"
        ):
            READINESS.evaluate_frozen_readiness_inputs(forged, root)
        # The packet cannot nominate a repository, ref or remote to resolve it
        # from either: the only anchor is the caller's trusted local root.
        self.assertEqual(set(envelope), {"schema_version", "source_commit", "files"})

    def test_envelope_shape_and_base64_are_hard_boundaries(self):
        root = self.repo()
        envelope = self.envelope(root)
        cases = {
            "not_an_object": ["not", "an", "object"],
            "extra_key": dict(envelope, extra="x"),
            "missing_key": {
                key: value for key, value in envelope.items() if key != "files"
            },
            "wrong_schema": dict(envelope, schema_version="p2_rotation/2"),
            "short_commit": dict(envelope, source_commit="abc123"),
            "uppercase_commit": dict(
                envelope, source_commit=envelope["source_commit"].upper()
            ),
            "extra_path": dict(
                envelope, files={**envelope["files"], "data/other.json": {}}
            ),
            "missing_path": dict(
                envelope,
                files={
                    key: value
                    for key, value in envelope["files"].items()
                    if key != KOREA_POINTER_REL
                },
            ),
        }
        for name, forged in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(PROVENANCE_ERROR):
                    READINESS.evaluate_frozen_readiness_inputs(forged, root)

        bad_base64 = copy.deepcopy(envelope)
        bad_base64["files"][KOREA_POINTER_REL]["content_base64"] = "not base64!"
        with self.assertRaisesRegex(
            PROVENANCE_ERROR, "FROZEN_INPUT_CONTENT_BASE64_INVALID"
        ):
            READINESS.evaluate_frozen_readiness_inputs(bad_base64, root)

    # -- replay stability ----------------------------------------------------

    def test_replay_is_stable_across_a_moved_head_and_a_changed_live_pointer(self):
        root = self.repo()
        envelope = self.envelope(root)
        before = READINESS.evaluate_frozen_readiness_inputs(envelope, root)

        pointer = root / KOREA_POINTER_REL
        pointer.write_bytes(resigned_korea_pointer(pointer.read_bytes()))
        git(root, "add", "-A")
        git(root, "commit", "-q", "-m", "later revision of the rolling pointer")
        moved_head = self.head(root)
        self.assertNotEqual(moved_head, envelope["source_commit"])
        # A newer capture is a different envelope; the older one still replays
        # to the same inventory from the earlier trusted commit, and the later
        # HEAD contributes nothing to the derived values.
        self.assertNotEqual(self.envelope(root), envelope)
        self.assertEqual(
            READINESS.evaluate_frozen_readiness_inputs(envelope, root), before
        )
        self.assertEqual(
            MODULE.p2_rotation_state_unavailable_reasons(envelope, root),
            P2_EXACT_REASONS,
        )

    def test_a_no_longer_trusted_history_fails_closed_instead_of_recapturing(self):
        root = self.repo()
        envelope = self.envelope(root)
        other = self.repo()
        # A different, equally real repository: the pinned commit simply is not
        # part of its history, so the replay refuses rather than silently
        # re-freezing whatever that repository happens to hold today.
        with self.assertRaises(PROVENANCE_ERROR):
            READINESS.evaluate_frozen_readiness_inputs(envelope, other)

    # -- semantics over authenticated bytes ---------------------------------

    def test_committed_malformed_json_is_the_only_semantic_fallback(self):
        sources = real_source_bytes()
        sources[KOREA_POINTER_REL] = b"{ not json at all"
        root = self.repo(sources)
        envelope = self.envelope(root)
        # Provenance passes: these really are the committed bytes.
        self.assertEqual(
            base64.b64decode(
                envelope["files"][KOREA_POINTER_REL]["content_base64"], validate=True
            ),
            b"{ not json at all",
        )
        READINESS.verify_readiness_inputs(envelope, root)
        with self.assertRaises(SEMANTIC_ERROR):
            READINESS.evaluate_frozen_readiness_inputs(envelope, root)
        # ...and only that recomputed semantic failure reaches the fixed
        # generic + VALIDATION_FAILED diagnostic.
        self.assertEqual(
            MODULE.p2_rotation_state_unavailable_reasons(envelope, root),
            P2_INVALID_REASONS,
        )

    def test_a_committed_absent_pointer_reproduces_the_same_diagnostic(self):
        sources = real_source_bytes()
        sources[KOREA_POINTER_REL] = None
        root = self.repo(sources)
        envelope = self.envelope(root)
        self.assertEqual(
            envelope["files"][KOREA_POINTER_REL],
            {"state": "ABSENT", "blob_oid": None, "content_base64": None},
        )
        READINESS.verify_readiness_inputs(envelope, root)
        with self.assertRaises(SEMANTIC_ERROR):
            READINESS.evaluate_frozen_readiness_inputs(envelope, root)
        # ...and the ADAPTER turns exactly that recomputed semantic failure into
        # the fixed pair of reasons -- the generic production-contract blocker is
        # preserved beside VALIDATION_FAILED, and nothing else is emitted. The
        # expected value is spelled out here rather than only referenced through
        # the module constant, so a change to that constant cannot silently
        # redefine what this proves.
        reasons = MODULE.p2_rotation_state_unavailable_reasons(envelope, root)
        self.assertEqual(
            reasons,
            [
                "P2_ROTATION_READINESS_INVALID:VALIDATION_FAILED",
                "P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE",
            ],
        )
        self.assertEqual(reasons, P2_INVALID_REASONS)
        self.assertEqual(len(reasons), 2)
        # An absent committed input is a missing prerequisite, never an exact
        # blocker inventory: none of the finite success codes may appear.
        self.assertTrue(set(reasons).isdisjoint(P2_EXACT_REASONS[:-1]), reasons)

    def test_a_semantically_invalid_committed_pointer_is_not_a_provenance_error(self):
        sources = real_source_bytes()
        pointer = json.loads(sources[KOREA_POINTER_REL].decode("utf-8"))
        pointer["run_status"] = "DEGRADED"
        pointer.pop("payload_sha256")
        pointer["payload_sha256"] = READINESS.payload_sha256(pointer)
        sources[KOREA_POINTER_REL] = json.dumps(pointer, sort_keys=True).encode("utf-8")
        root = self.repo(sources)
        envelope = self.envelope(root)
        with self.assertRaises(SEMANTIC_ERROR):
            READINESS.evaluate_frozen_readiness_inputs(envelope, root)

    def test_the_inventory_carries_semantics_only(self):
        root = self.repo()
        envelope = self.envelope(root)
        inventory = READINESS.evaluate_frozen_readiness_inputs(envelope, root)
        self.assertEqual(
            [row["market"] for row in inventory["markets"]], ["US", "KOREA", "CRYPTO"]
        )
        for row in inventory["markets"]:
            self.assertEqual(set(row), {"market", "blockers"})
            self.assertEqual(row["blockers"], list(READINESS.MARKET_BLOCKERS))
        self.assertTrue(inventory["authority"]["readiness_inventory_only"])
        for key, value in inventory["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)
        body = json.dumps(inventory, sort_keys=True)
        self.assertNotIn(envelope["source_commit"], body)
        for entry in envelope["files"].values():
            self.assertNotIn(entry["blob_oid"], body)

    def test_the_live_producer_is_unchanged_at_the_same_head(self):
        # The existing command still reads today's working tree, enforces its
        # committed-HEAD checks and re-derives its own full packet. The frozen
        # path is an addition, not a replacement.
        packet = READINESS.build_readiness()
        self.assertEqual(READINESS.validate_readiness(packet), packet)
        self.assertEqual(
            packet["overall_status"],
            "BLOCKED_NO_MARKET_HAS_OPERATIONAL_STATE_HISTORY",
        )
        for row in packet["markets"]:
            self.assertEqual(row["blockers"], list(READINESS.MARKET_BLOCKERS))
            self.assertEqual(row["readiness_status"], "NOT_READY")

    def test_the_existing_live_producer_still_rejects_a_dirty_committed_source(self):
        """`build_readiness(root)` keeps its own working-tree check.

        The frozen-input path is an ADDITION, so the compatibility fact that
        matters is that the original entry point was not quietly relaxed: it
        must still refuse to derive a packet from a source file that differs
        from the commit it claims to come from. Exercised through the public
        `build_readiness`/`validate_readiness` signatures on a real throwaway
        repository, so nothing here depends on this checkout being dirty.
        """
        root = self.repo()
        clean = READINESS.build_readiness(root)
        self.assertEqual(READINESS.validate_readiness(clean, root), clean)

        pointer = root / KOREA_POINTER_REL
        committed = pointer.read_bytes()
        # Internally consistent tampered bytes: the pointer would pass the
        # producer's own field/self-hash/semantic validation if the dirty-source
        # check ever let it through, so only that check can reject it.
        pointer.write_bytes(resigned_korea_pointer(committed))
        self.assertNotEqual(pointer.read_bytes(), committed)
        self.assertTrue(git(root, "status", "--porcelain", "--", KOREA_POINTER_REL).strip())

        with self.assertRaisesRegex(READINESS_ERROR, "EVIDENCE_WORKTREE_DIRTY"):
            READINESS.build_readiness(root)
        # ...and so does the validating entry point, which re-derives through it.
        with self.assertRaisesRegex(READINESS_ERROR, "EVIDENCE_WORKTREE_DIRTY"):
            READINESS.validate_readiness(clean, root)
        # The new capture refuses the same state as a hard provenance failure,
        # and a dirty source is never a semantic diagnostic on either path.
        with self.assertRaisesRegex(PROVENANCE_ERROR, "EVIDENCE_WORKTREE_DIRTY"):
            READINESS.capture_readiness_inputs(root)
        self.assertFalse(issubclass(PROVENANCE_ERROR, READINESS_ERROR))
        self.assertFalse(issubclass(READINESS_ERROR, PROVENANCE_ERROR))

        # Restoring the committed bytes restores the original packet exactly:
        # the refusal was about the working tree, not about the producer having
        # changed its output.
        pointer.write_bytes(committed)
        self.assertEqual(READINESS.build_readiness(root), clean)


class P2RotationStateBlockerMappingTests(unittest.TestCase):
    """Only exact, validated markets and blockers become reason codes."""

    def repo(self, sources=None):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return build_source_repo(directory.name, sources)

    def inventory(self):
        root = self.repo()
        return READINESS.evaluate_frozen_readiness_inputs(
            READINESS.capture_readiness_inputs(root), root
        )

    def convert(self, inventory):
        return MODULE._p2_rotation_state_reasons_from_inventory(inventory)

    def test_the_finite_mapping_is_exact(self):
        self.assertEqual(self.convert(self.inventory()), P2_EXACT_REASONS)
        self.assertEqual(P2_EXACT_REASONS, sorted(set(P2_EXACT_REASONS)))

    def test_unknown_forged_or_shortened_inventories_are_rejected(self):
        valid = self.inventory()
        unknown_market = copy.deepcopy(valid)
        unknown_market["markets"][0]["market"] = "JAPAN"
        unknown_blocker = copy.deepcopy(valid)
        unknown_blocker["markets"][0]["blockers"][0] = "ROTATION_READY"
        shortened_blockers = copy.deepcopy(valid)
        shortened_blockers["markets"][0]["blockers"] = [
            "FULL_PRODUCTION_ROTATION_PACKET_MISSING"
        ]
        shortened_markets = copy.deepcopy(valid)
        shortened_markets["markets"] = shortened_markets["markets"][:2]
        duplicated = copy.deepcopy(valid)
        duplicated["markets"].append(copy.deepcopy(duplicated["markets"][0]))
        wrong_identity = copy.deepcopy(valid)
        wrong_identity["schema_version"] = "something_else/1"
        extra_row_key = copy.deepcopy(valid)
        extra_row_key["markets"][0]["ready"] = True
        expanded_authority = copy.deepcopy(valid)
        expanded_authority["authority"]["production_authorized"] = True
        cases = {
            "unknown_market": unknown_market,
            "unknown_blocker": unknown_blocker,
            "shortened_blockers": shortened_blockers,
            "shortened_markets": shortened_markets,
            "duplicated_market": duplicated,
            "wrong_identity": wrong_identity,
            "extra_row_key": extra_row_key,
            "expanded_authority": expanded_authority,
            "not_an_object": ["US"],
            "empty": {},
        }
        for name, forged in cases.items():
            with self.subTest(case=name):
                with self.assertRaises(MODULE.StrategicCapitalPostureError):
                    self.convert(forged)

    def test_no_caller_supplied_invalid_representation_is_accepted(self):
        # There is no stored validity flag, reason list or error code to hand
        # in: the only argument is the frozen envelope, and anything that is
        # not one fails closed rather than being forwarded.
        for forged in (
            None,
            {},
            {"unavailable_reasons": P2_INVALID_REASONS},
            {"valid": False},
            "P2_ROTATION_READINESS_INVALID:VALIDATION_FAILED",
        ):
            with self.subTest(forged=repr(forged)[:40]):
                with self.assertRaises(PROVENANCE_ERROR):
                    MODULE.p2_rotation_state_unavailable_reasons(forged)


class P2RotationStatePostureBindingTests(unittest.TestCase):
    """The bound reasons change the honesty of the row and nothing else."""

    def repo(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return build_source_repo(directory.name)

    def build(self, p2_reasons=_UNSET):
        packets, reasons = morning_bundle()
        if p2_reasons is not _UNSET:
            reasons["P2_ROTATION_STATE"] = p2_reasons
        return MODULE.build_packet(
            packets, reasons, MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
            contract=CONTRACT,
        )

    def assert_grants_nothing(self, packet):
        self.assertEqual(
            packet["status"], "STRATEGIC_CAPITAL_POSTURE_READINESS_BLOCKED"
        )
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertEqual(
            packet["market_budget"], {"CRYPTO": None, "KOREA": None, "US": None}
        )
        for key in (
            "risk_posture", "cash_reserve", "hedge_budget", "max_gross_risk",
            "max_net_risk", "theme_headroom", "allocation_proposal",
            "target_exposures", "position_sizes", "policy_packet",
        ):
            self.assertIsNone(packet[key], key)
        self.assertEqual(packet["order_intents"], [])
        self.assertEqual(packet["summary"]["numeric_budget_field_count"], 0)
        self.assertEqual(packet["summary"]["evaluated_constraint_count"], 0)
        self.assertEqual(packet["authority"], CONTRACT["authority"])
        for key, value in packet["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_exact_blockers_bind_without_creating_availability(self):
        root = self.repo()
        reasons = MODULE.p2_rotation_state_unavailable_reasons(
            READINESS.capture_readiness_inputs(root), root
        )
        self.assertEqual(reasons, P2_EXACT_REASONS)

        packet = self.build(reasons)
        self.assertEqual(packet["unavailable_reasons"]["P2_ROTATION_STATE"], reasons)
        row = {
            source["name"]: source for source in packet["sources"]
        }["P2_ROTATION_STATE"]
        self.assertEqual(row["availability"], "UNAVAILABLE")
        self.assertEqual(row["unavailable_reasons"], reasons)
        self.assertIsNone(row["source_status"])
        self.assertIsNone(row["evidence_date"])
        self.assertIsNone(row["source_packet_sha256"])
        self.assertIsNone(
            packet["lineage"]["source_packet_sha256"]["P2_ROTATION_STATE"]
        )
        self.assertIn("P2_ROTATION_STATE", packet["summary"]["unavailable_sources"])
        self.assertIn(
            "SOURCE_UNAVAILABLE:P2_ROTATION_STATE", packet["binding_reasons"]
        )
        self.assertIn(
            "P2_ROTATION_STATE_UNAVAILABLE", packet["unresolved_boundaries"]
        )
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)
        self.assert_grants_nothing(packet)

    def test_a_supplied_rotation_state_packet_is_still_refused(self):
        # Diagnostic conveyance only: naming the gaps does not turn this slot
        # into one that accepts a production packet.
        packets, reasons = morning_bundle()
        packets["P2_ROTATION_STATE"] = {"status": "READY"}
        reasons["P2_ROTATION_STATE"] = []
        with self.assertRaisesRegex(
            MODULE.StrategicCapitalPostureError,
            "SOURCE_PACKET_NOT_YET_SUPPORTED:P2_ROTATION_STATE",
        ):
            MODULE.build_packet(
                packets, reasons, MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
                contract=CONTRACT,
            )

    def test_the_generic_and_invalid_forms_stay_distinguishable(self):
        # The three forms an absent/1/2, a diagnostic-invalid and a
        # diagnostic-valid derivation put in this slot.
        generic = self.build(["P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE"])
        invalid = self.build(P2_INVALID_REASONS)
        exact = self.build(P2_EXACT_REASONS)
        hashes = {
            packet["packet_sha256"] for packet in (generic, invalid, exact)
        }
        self.assertEqual(len(hashes), 3)
        for packet in (generic, invalid, exact):
            # Every form preserves the generic production-contract blocker.
            self.assertIn(
                "P2_ROTATION_STATE_PRODUCTION_CONTRACT_UNAVAILABLE",
                packet["unavailable_reasons"]["P2_ROTATION_STATE"],
            )
            self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)
            self.assert_grants_nothing(packet)
        ignored = {"unavailable_reasons", "sources", "packet_sha256"}
        self.assertEqual(
            {k: v for k, v in generic.items() if k not in ignored},
            {k: v for k, v in exact.items() if k not in ignored},
        )

    def test_malformed_blocker_lists_fail_closed_at_this_boundary(self):
        for bad in ([], ["lowercase"], ["B_REASON", "A_REASON"],
                    ["A_REASON", "A_REASON"], [None], "A_REASON", None):
            with self.subTest(reasons=bad):
                with self.assertRaisesRegex(
                    MODULE.StrategicCapitalPostureError,
                    "UNAVAILABLE_REASONS_INVALID:P2_ROTATION_STATE",
                ):
                    self.build(bad)


if __name__ == "__main__":
    unittest.main()
