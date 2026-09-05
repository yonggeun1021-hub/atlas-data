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

# The real production time geometry, from test/fixture_daily_briefing_live.yml:
# DECISION_DATE is `TZ=Asia/Seoul date +%F` (a KST business date) while
# GENERATED_AT is `date -u` (a UTC instant).  The weekday morning cron is
# `5 22 * * 0-4`, so 22:05Z is 07:05 of the *next* KST day and the run's
# generated_at is structurally one UTC calendar day behind its as_of_date.
# The evening cron `30 9 * * 1-5` lands on the same UTC and KST day.
#
# The morning baseline is one day later than AS_OF_DATE so that the real
# committed P2_FLOW_ENGINE evidence stays a full calendar day earlier than
# the run instant, for the same no-staleness reason documented above.
MORNING_AS_OF_DATE = (
    dt.date.fromisoformat(AS_OF_DATE) + dt.timedelta(days=1)
).isoformat()
MORNING_GENERATED_AT = AS_OF_DATE + "T22:05:00Z"
# 15:00:00Z is exactly 00:00:00 KST of MORNING_AS_OF_DATE; one second earlier
# is still 23:59:59 KST of AS_OF_DATE.
KST_MIDNIGHT_GENERATED_AT = AS_OF_DATE + "T15:00:00Z"
LAST_KST_INSTANT_BEFORE_MIDNIGHT = AS_OF_DATE + "T14:59:59Z"
EVENING_GENERATED_AT = AS_OF_DATE + "T09:40:16Z"


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


class DefensiveActionKstBusinessDateTests(unittest.TestCase):
    """``as_of_date`` is a KST business date; ``generated_at`` is a UTC instant.

    The guard in ``_assemble`` must compare the two on the same basis.  These
    pin the real scheduled-briefing geometry, which the same-UTC-day fixtures
    above never exercise.
    """

    def build(self, as_of_date, generated_at):
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
        self.assertEqual(packet["status"], "DEFENSIVE_ACTION_READINESS_BLOCKED")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertIsNone(packet["selected_action"])
        self.assertIsNone(packet["risk_budget_allocation"])
        self.assertIsNone(packet["target_exposures"])
        self.assertIsNone(packet["selected_instrument"])
        self.assertIsNone(packet["position_size"])
        self.assertIsNone(packet["action_proposal"])
        self.assertEqual(packet["order_intents"], [])
        self.assertIsNone(packet["policy_packet"])
        self.assertIsNone(packet["summary"]["no_action"])
        self.assertTrue(packet["authority"]["readiness_inventory_only"])
        for key, value in packet["authority"].items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)
        self.assertIn(
            "DEFENSIVE_ACTION_POLICY_NOT_RATIFIED", packet["unresolved_boundaries"]
        )
        self.assertIn("P1_REGIME_DECISION_UNAVAILABLE", packet["unresolved_boundaries"])
        self.assertIn("P2_FLOW_LEDGER_UNAVAILABLE", packet["unresolved_boundaries"])
        self.assertTrue(
            all(
                "DEFENSIVE_ACTION_POLICY_NOT_RATIFIED" in row["reasons"]
                for row in packet["decisions"]
            )
        )
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

    def test_kst_morning_generation_window_builds_instead_of_failing_closed(self):
        # The exact production defect: 22:05Z is the next KST business day, so
        # the run's generated_at is a UTC calendar day behind its as_of_date.
        self.assertLess(MORNING_GENERATED_AT[:10], MORNING_AS_OF_DATE)
        self.assertEqual(
            MODULE._kst_business_date(MORNING_GENERATED_AT), MORNING_AS_OF_DATE
        )

        packet = self.build(MORNING_AS_OF_DATE, MORNING_GENERATED_AT)
        self.assertEqual(packet["as_of_date"], MORNING_AS_OF_DATE)
        self.assertEqual(packet["generated_at"], MORNING_GENERATED_AT)
        self.assertEqual(packet["summary"]["available_source_count"], 10)
        self.assertEqual(packet["summary"]["unavailable_source_count"], 2)
        # Building on the morning geometry must not add any authority.
        self.assert_readiness_only(packet)

    def test_morning_and_evening_geometry_emit_the_same_readiness_body(self):
        morning = self.build(MORNING_AS_OF_DATE, MORNING_GENERATED_AT)
        evening = self.build(AS_OF_DATE, EVENING_GENERATED_AT)
        self.assertEqual(MODULE._kst_business_date(EVENING_GENERATED_AT), AS_OF_DATE)
        # Restoring the morning run changes only its own time keys: every
        # source row, decision row, reason, invariant and authority flag is
        # byte-identical to the evening packet the run already emitted.
        self.assertEqual(self.semantic_body(morning), self.semantic_body(evening))

    def test_kst_midnight_is_the_exact_accept_reject_boundary(self):
        packet = self.build(MORNING_AS_OF_DATE, KST_MIDNIGHT_GENERATED_AT)
        self.assertEqual(packet["generated_at"], KST_MIDNIGHT_GENERATED_AT)
        self.assert_readiness_only(packet)

        # One second earlier is still the previous KST business day.
        self.assertEqual(
            MODULE._kst_business_date(LAST_KST_INSTANT_BEFORE_MIDNIGHT), AS_OF_DATE
        )
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError, "GENERATED_BEFORE_AS_OF_DATE"
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
                MODULE.DefensiveActionDecisionError, "GENERATED_BEFORE_AS_OF_DATE"
            ):
                self.build(as_of, MORNING_GENERATED_AT)

    def test_source_instant_and_timestamp_guards_are_unchanged(self):
        # A future source is still refused on the morning geometry: only the
        # as_of/generated comparison basis moved to KST.
        packets, reasons = bundle()
        packets["CASH_EXPOSURE_US"] = CASH.MODULE.build_packet(
            CASH.REGIME.build_unknown_output("US", FUTURE_GENERATED_AT), CASH.CONTRACT
        )
        self.assertGreater(FUTURE_GENERATED_AT, MORNING_GENERATED_AT)
        with self.assertRaisesRegex(
            MODULE.DefensiveActionDecisionError, "SOURCE_FROM_FUTURE:CASH_EXPOSURE_US"
        ):
            MODULE.build_packet(
                packets, reasons, MORNING_AS_OF_DATE, MORNING_GENERATED_AT,
                contract=CONTRACT,
            )

        # Invalid timestamps still fail on their own codes, never inside the
        # KST conversion.
        for generated_at in (
            MORNING_AS_OF_DATE,
            AS_OF_DATE + "T22:05:00",
            AS_OF_DATE + " 22:05:00Z",
            AS_OF_DATE + "T22:05:00+09:00",
            AS_OF_DATE + "T25:05:00Z",
            None,
        ):
            with self.assertRaisesRegex(
                MODULE.DefensiveActionDecisionError, "GENERATED_AT_INVALID"
            ):
                self.build(MORNING_AS_OF_DATE, generated_at)
        for as_of_date in (MORNING_GENERATED_AT, "2026-9-5", None):
            with self.assertRaisesRegex(
                MODULE.DefensiveActionDecisionError, "AS_OF_DATE_INVALID"
            ):
                self.build(as_of_date, MORNING_GENERATED_AT)

    def test_previously_accepted_same_utc_day_inputs_still_revalidate(self):
        # Archived packets are revalidated by re-running _assemble, so the
        # already-passing evening geometry must build and validate unchanged.
        for as_of_date, generated_at in (
            (AS_OF_DATE, GENERATED_AT),
            (AS_OF_DATE, EVENING_GENERATED_AT),
            (AS_OF_DATE, LAST_KST_INSTANT_BEFORE_MIDNIGHT),
        ):
            first = self.build(as_of_date, generated_at)
            second = self.build(as_of_date, generated_at)
            self.assertEqual(
                MODULE.canonical_json(first), MODULE.canonical_json(second)
            )
            self.assertEqual(first["as_of_date"], as_of_date)
            self.assertEqual(first["generated_at"], generated_at)
            self.assert_readiness_only(first)


if __name__ == "__main__":
    unittest.main()
