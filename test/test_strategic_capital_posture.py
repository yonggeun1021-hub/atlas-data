#!/usr/bin/env python3
"""P7-12 fail-closed Strategic Capital Posture readiness regression."""

import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
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


if __name__ == "__main__":
    unittest.main()
