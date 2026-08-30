#!/usr/bin/env python3
"""Focused KRX public completed-bar -> Shadow -> P8-13 regressions."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from orchestration import krx_paper_public_pipeline as PIPELINE
from decision import krx_paper_proposal_bridge as PROPOSAL
from decision import krx_shadow_strategy as SHADOW


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MARKET_FIXTURE = load_module(
    "krx_market_data_fixture_for_public_pipeline",
    ROOT / "validation" / "tests" / "test_krx_market_data.py",
)
GATE_ASSESSMENT = json.loads(
    (ROOT / "evidence" / "krx_paper_gate" / "2026-08-30" / "assessment.json").read_text()
)
GATE_EVIDENCE = json.loads(
    (ROOT / "evidence" / "krx_paper_gate" / "2026-08-30" / "evidence_input.json").read_text()
)
BRIDGE_CONTRACT = PROPOSAL.load_contract()


def sign(value: dict) -> dict:
    value.pop("payload_sha256", None)
    value["payload_sha256"] = PIPELINE.payload_sha256(value)
    return value


def resign_input(value: dict) -> dict:
    value["packet_sha256"] = PIPELINE.payload_sha256(
        {key: item for key, item in value.items() if key != "packet_sha256"}
    )
    return value


def utc_text(value: str) -> str:
    parsed = dt.datetime.fromisoformat(value)
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def natural_market_input() -> dict:
    decision = dt.datetime.fromisoformat("2026-08-28T15:31:00+09:00")
    packet = {
        "schema_version": "krx_market_data_input/1",
        "decision_at": decision.isoformat(timespec="seconds"),
        "calendar": copy.deepcopy(MARKET_FIXTURE.CALENDARS["regular_open"]),
        "series": [
            MARKET_FIXTURE.series(interval, decision)
            for interval in ("15m", "1h", "1d")
        ],
        "market_state": MARKET_FIXTURE.market_state(decision),
        "freshness_policy": copy.deepcopy(MARKET_FIXTURE.FRESHNESS_POLICY),
        "authority": copy.deepcopy(MARKET_FIXTURE.CONTRACT["authority"]),
    }
    packet["calendar"]["source_ref"] = "evidence://kis/CTCA0903R/2026-08-28"
    packet["freshness_policy"]["policy_id"] = "KRX.P9_01.CANONICAL.V1"
    packet["freshness_policy"]["ratified_by"] = "CIO_POLICY_RECORD"
    packet["freshness_policy"]["packet_sha256"] = PIPELINE.MARKET_DATA.P9_FRESHNESS.payload_sha256(
        {
            key: value
            for key, value in packet["freshness_policy"].items()
            if key != "packet_sha256"
        }
    )
    for series in packet["series"]:
        for index, bar in enumerate(series["bars"]):
            bar.update({
                "open": "99000", "high": "110000", "low": "98000",
                "close": "105000", "volume": "1000",
            })
            bar["source"]["snapshot_ref"] = (
                f"evidence://kis/{series['timeframe']}/2026-08-28/{index}"
            )
    packet["market_state"]["source_ref"] = "evidence://kis/market-state/2026-08-28"
    return packet


def latest_by_interval(result: dict) -> dict[str, dict]:
    return {row["timeframe"]: row["bars"][-1] for row in result["series"]}


def make_shadow_input(
    universe: dict, market_result: dict, execution: dict, policy: dict
) -> dict:
    available = "2026-08-28T06:30:30Z"
    valid_until = "2026-08-28T07:30:00Z"
    window = {"available_at": available, "valid_until": valid_until}
    latest = latest_by_interval(market_result)
    bars = {}
    for interval, source in latest.items():
        bars[interval] = {
            "interval": interval,
            "completed": True,
            "opened_at": utc_text(source["open_at"]),
            "closed_at": utc_text(source["close_at"]),
            "available_at": utc_text(source["source"]["available_at"]),
            "valid_until": valid_until,
            "open": int(source["open"]),
            "high": int(source["high"]),
            "low": int(source["low"]),
            "close": int(source["close"]),
            "source_sha256": source["source"]["snapshot_sha256"],
        }
    candidate = {
        "candidate_id": "KRX-PUBLIC-005930",
        "symbol": universe["symbol"],
        "briefing_rank": 1,
        "identity": {
            "status": "RESOLVED", "symbol": universe["symbol"],
            "canonical_instrument_id": universe["shadow_canonical_instrument_id"], **window,
            "source_sha256": universe["payload_sha256"],
        },
        "eligibility": {
            "status": universe["decision_eligibility"],
            "authority_status": "RATIFIED_SHADOW_ONLY", **window,
            "source_sha256": "c" * 64,
        },
        "market_context": {
            "status": "AVAILABLE", "authority_status": "RATIFIED_SHADOW_ONLY",
            "entry_allowed": True, "hold_allowed": True, **window,
            "source_sha256": market_result["packet_sha256"],
        },
        "relative_strength": {
            "status": "AVAILABLE", "authority_status": "RATIFIED_SHADOW_ONLY",
            "entry_confirmed": True, "hold_confirmed": True, **window,
            "source_sha256": "e" * 64,
        },
        "liquidity": {
            "status": "AVAILABLE", "authority_status": "RATIFIED_SHADOW_ONLY",
            "eligible": True, "max_shadow_quantity": 1, **window,
            "source_sha256": execution["payload_sha256"],
        },
        "bars": bars,
        "quote": {
            "status": "AVAILABLE", "observed_at": "2026-08-28T06:30:59Z",
            "available_at": "2026-08-28T06:30:59Z", "valid_until": valid_until,
            "last": 101000, "bid": 100900, "ask": 101000,
            "source_sha256": "1" * 64,
        },
        "position": {
            "status": "OPEN", "entry_price": 100000, "quantity": 1,
            "opened_at": "2026-08-28T00:30:00Z", "take_profit_1_done": False, **window,
            "source_sha256": "5" * 64,
        },
        "trade_plan": {
            "policy_id": policy["policy_id"], "status": "RATIFIED_SHADOW_ONLY",
            "entry_reference_price": 110000, "max_entry_price": 112000,
            "stop_price": 98000, "take_profit_1_price": 116000,
            "final_take_profit_price": 120000, "take_profit_1_fraction_bps": 5000,
            "expires_at": valid_until, "invalidation_triggered": False,
            "exit_on_regime_block": True, "exit_on_relative_strength_break": True,
            "tick_size": 100, "entry_fee_bps": 10, "exit_fee_bps": 10,
            "stop_slippage_bps": 20, "entry_after_kst": "09:15:00",
            "session_ends_kst": "15:20:00", "quote_max_age_seconds": 30,
            "max_spread_bps": 25, **window,
            "source_sha256": policy["policy_source_sha256"],
        },
        "risk_budget": {
            "status": "RATIFIED_SHADOW_ONLY", "allocation_id": "ALLOC-005930",
            "allocation_scope": "PER_CANDIDATE_PREALLOCATED_FROM_ACCOUNT_RISK_BUDGET",
            "account_risk_budget_id": "ACCOUNT-RISK-PUBLIC-READINESS",
            "account_risk_budget_total_krw": 100000,
            "account_committed_risk_krw": 50000, "risk_budget_krw": 50000,
            "account_capacity_quantity": 1, "current_open_positions": 1,
            **window, "source_sha256": "3" * 64,
        },
        "source_sha256": "4" * 64,
    }
    source = {
        "schema_version": "krx_shadow_strategy_input/1",
        "contract_version": "krx_shadow_strategy/1",
        "decision_batch_id": "KRX-PUBLIC-20260828-1531",
        "evaluated_at": "2026-08-28T06:31:00Z",
        "business_date": "2026-08-28",
        "mode": "PAPER_CANARY",
        "prior_decision_keys": [],
        "candidates": [candidate],
        "authority": copy.deepcopy(SHADOW.AUTHORITY_BOUNDARY),
    }
    source["packet_sha256"] = SHADOW.payload_sha256(source)
    return source


def make_proposal_input(
    universe: dict, market_result: dict, shadow_packet: dict, policy: dict
) -> dict:
    latest = latest_by_interval(market_result)
    decision = shadow_packet["decisions"][0]
    valid_until = "2026-08-28T07:30:00Z"
    bars = {
        interval: {
            "completed": True,
            "opened_at_utc": utc_text(bar["open_at"]),
            "closed_at_utc": utc_text(bar["close_at"]),
            "available_at_utc": utc_text(bar["source"]["available_at"]),
            "valid_until_utc": valid_until,
            "source_sha256": bar["source"]["snapshot_sha256"],
        }
        for interval, bar in latest.items()
    }
    gate_authority = copy.deepcopy(GATE_ASSESSMENT["authority"])
    source = {
        "schema_version": "krx_paper_proposal_bridge_input/1",
        "evaluated_at_utc": "2026-08-28T06:31:00Z",
        "proposal_expires_at_utc": valid_until,
        "prior_proposal_keys": [],
        "briefing": {
            "symbol": universe["symbol"], "rank": 1,
            "summary": "Natural KRX public readiness candidate.",
            "source_sha256": "a" * 64,
        },
        "universe": {
            "repository": "yonggeun1021-hub/atlas-data",
            "source_commit": BRIDGE_CONTRACT["source_requirements"]["universe"]["exact_head"],
            "repository_state": "MERGED_TO_PUBLIC_MAIN",
            "contract_version": "krx_investable_registry/1",
            "symbol": universe["symbol"], "security_id": universe["security_id"],
            "decision_eligibility": universe["decision_eligibility"],
            "available_at_utc": "2026-08-28T06:30:30Z",
            "valid_until_utc": valid_until,
            "source_sha256": universe["payload_sha256"],
            "packet_sha256": universe["registry_packet_sha256"],
        },
        "shadow": {
            "repository": "yonggeun1021-hub/atlas-data",
            "source_commit": BRIDGE_CONTRACT["source_requirements"]["shadow"]["exact_head"],
            "repository_state": "MERGED_TO_PUBLIC_MAIN",
            "contract_version": "krx_shadow_strategy/1",
            "decision_key": decision["decision_key"], "symbol": universe["symbol"],
            "action": decision["action"],
            "diagnostic_action": decision["diagnostic_action"],
            "source_sha256": shadow_packet["packet_sha256"],
            "packet_sha256": shadow_packet["packet_sha256"],
        },
        "bars": bars,
        "position": {
            "symbol": universe["symbol"], "status": "OPEN",
            "current_open_positions": 1,
            "available_at_utc": "2026-08-28T06:30:30Z",
            "valid_until_utc": valid_until, "source_sha256": "4" * 64,
        },
        "policy": {
            "policy_id": policy["policy_id"], "status": "RATIFIED",
            "symbol": universe["symbol"],
            "entry_zone": {"minimum_price_units": 110000, "maximum_price_units": 112000},
            "stop_price_units": 98000, "first_take_profit_price_units": 116000,
            "final_take_profit_price_units": 120000, "expires_at_utc": valid_until,
            "planned_loss_units": 1000, "account_risk_budget_units": 10000,
            "account_committed_risk_units": 0,
            "available_at_utc": "2026-08-28T06:30:30Z",
            "valid_until_utc": valid_until,
            "source_sha256": policy["policy_source_sha256"],
        },
        "gate_assessment": {
            "schema_version": "krx_paper_gate_assessment/1",
            "assessment_sha256": GATE_ASSESSMENT["assessment_sha256"],
            "current_state": GATE_ASSESSMENT["current_state"],
            "common_safety": "UNKNOWN", "krx_shadow": "UNKNOWN",
            "krx_paper_canary_start": "FAIL", "authority": gate_authority,
        },
        "authority": copy.deepcopy(PROPOSAL.AUTHORITY_ALL_FALSE),
    }
    source["packet_sha256"] = PROPOSAL.payload_sha256(source)
    return source


def complete_input() -> dict:
    universe = sign({
        "schema_version": "krx_public_universe_identity/1",
        "evidence_kind": "NATURAL",
        "source_commit": PIPELINE.SOURCE_PIN_EXPECTATIONS["execution_measurement"][0],
        "business_date": "2026-08-28", "symbol": "005930",
        "security_id": "KR:XKRX:KR7005930003",
        "shadow_canonical_instrument_id": "KRX:005930:COMMON",
        "identity_snapshot_sha256": "b" * 64,
        "registry_packet_sha256": "c" * 64,
        "decision_eligibility": "ELIGIBLE", "authority_status": "RATIFIED_EFFECTIVE",
        "available_at_utc": "2026-08-28T06:30:30Z",
        "valid_until_utc": "2026-08-28T07:30:00Z",
        "source_ref": "evidence://krx/universe/2026-08-28",
    })
    market_input = natural_market_input()
    for series in market_input["series"]:
        series["asset_id"] = universe["security_id"]
    market_input["market_state"]["asset_id"] = universe["security_id"]
    market_result = PIPELINE.MARKET_DATA.evaluate_packet(market_input)
    interval = sign({
        "schema_version": "kis_krx_interval_semantics/1", "evidence_kind": "NATURAL",
        "source_commit": PIPELINE.SOURCE_PIN_EXPECTATIONS["completed_bars"][0],
        "status": "RATIFIED_EFFECTIVE", "provider_id": "KIS_OPEN_API",
        "endpoint_ids": ["FHKST03010200", "FHKST03010230"],
        "raw_timestamp_field": "stck_cntg_hour",
        "semantics": "INTERVAL_START_RATIFIED", "ratified_by": "CIO_POLICY_RECORD",
        "ratified_at_utc": "2026-08-27T00:00:00Z",
        "effective_from_utc": "2026-08-28T00:00:00Z",
        "effective_to_utc": "2026-09-30T00:00:00Z",
        "source_ref": "evidence://kis/interval-semantics/ratification",
        "source_sha256": "d" * 64,
    })
    execution = sign({
        "schema_version": "krx_execution_measurement_readiness/1",
        "evidence_kind": "NATURAL",
        "source_commit": PIPELINE.SOURCE_PIN_EXPECTATIONS["execution_measurement"][0],
        "business_date": "2026-08-28", "status": "CAPTURE_COMPLETED_READ_ONLY",
        "identity_snapshot_sha256": universe["identity_snapshot_sha256"],
        "captured_at_utc": "2026-08-28T06:30:30Z", "http_method": "GET",
        "coverage": {"turnover": 6, "depth": 6, "spread": 6, "slippage": 6},
        "public_packet_sha256": "e" * 64, "broker_post_count": 0,
        "authority": {"broker_post": False, "order": False, "trading": False},
    })
    policy = sign({
        "schema_version": "krx_policy_authority/1", "status": "RATIFIED_EFFECTIVE",
        "policy_id": "KRX_MULTITIMEFRAME_BREAKOUT_CANDIDATE",
        "policy_source_sha256": "5" * 64, "ratified_by": "CIO_POLICY_RECORD",
        "ratified_at_utc": "2026-08-27T00:00:00Z",
        "effective_from_utc": "2026-08-28T00:00:00Z",
        "effective_to_utc": "2026-09-30T00:00:00Z",
        "strategy_policy_ratified": True, "entry_policy_ratified": True,
        "hold_exit_policy_ratified": True, "position_size_policy_ratified": True,
    })
    shadow_input = make_shadow_input(universe, market_result, execution, policy)
    shadow_packet = SHADOW.build_packet(shadow_input)
    proposal_input = make_proposal_input(universe, market_result, shadow_packet, policy)
    value = {
        "schema_version": "krx_paper_public_pipeline_input/1",
        "run_id": "KRX-PUBLIC-PIPELINE-20260828-1531",
        "business_date": "2026-08-28", "evaluated_at_utc": "2026-08-28T06:31:00Z",
        "universe_identity": universe,
        "market_data": {
            "evidence_kind": "NATURAL", "input": market_input,
            "expected_result_sha256": market_result["packet_sha256"],
        },
        "interval_semantics": interval, "execution_measurement": execution,
        "policy_authority": policy, "gate_assessment": copy.deepcopy(GATE_ASSESSMENT),
        "gate_evidence_input": copy.deepcopy(GATE_EVIDENCE),
        "shadow_input": shadow_input, "proposal_input": proposal_input,
        "prior_receipts": [], "authority": copy.deepcopy(PIPELINE.AUTHORITY),
    }
    return resign_input(value)


class KrxPaperPublicPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = PIPELINE.load_contract()
        cls.source = complete_input()

    def build(self, source: dict | None = None) -> dict:
        return PIPELINE.build_packet(source or copy.deepcopy(self.source), self.contract)

    def test_exact_merged_pins_are_ancestors_and_contract_bytes_match(self):
        verified = PIPELINE.verify_source_pins(self.contract)
        self.assertEqual(set(verified), {"completed_bars", "execution_measurement", "shadow", "proposal"})
        for name, expected in PIPELINE.SOURCE_PIN_EXPECTATIONS.items():
            self.assertEqual(expected[0], verified[name]["merge_commit"])
            self.assertEqual(expected[3], verified[name]["contract_file_sha256"])

    def test_current_canonical_bindings_lock_symbol_and_proposal(self):
        packet = self.build()
        self.assertEqual("LOCKED_FAIL_CLOSED", packet["status"])
        self.assertEqual("LOCKED_FAIL_CLOSED", packet["readiness"]["status"])
        self.assertEqual("NONE", packet["readiness"]["symbol"])
        self.assertEqual("NONE", packet["proposal"]["status"])
        self.assertIn("RATIFIED_POLICY_BINDING_ABSENT", packet["readiness"]["blockers"])
        self.assertIn("COMMON_SAFETY_NOT_PASS", packet["readiness"]["blockers"])
        self.assertIn("EFFECTIVE_KRX_SHADOW_NOT_PASS", packet["readiness"]["blockers"])
        self.assertIsNotNone(packet["lineage"]["shadow_packet_sha256"])
        self.assertIsNotNone(packet["lineage"]["p8_13_packet_sha256"])
        self.assertEqual(packet, PIPELINE.validate_packet(packet, self.source, self.contract))

    def test_every_required_natural_or_authority_stage_missing_is_locked(self):
        fields = {
            "universe_identity": "UNIVERSE",
            "market_data": "MARKET_DATA",
            "interval_semantics": "INTERVAL_SEMANTICS",
            "execution_measurement": "EXECUTION_MEASUREMENT",
            "policy_authority": "POLICY",
            "gate_assessment": "GATE",
            "gate_evidence_input": "GATE",
            "shadow_input": "SHADOW",
            "proposal_input": "PROPOSAL",
        }
        for field, prefix in fields.items():
            with self.subTest(field=field):
                source = copy.deepcopy(self.source)
                source.pop(field)
                resign_input(source)
                packet = self.build(source)
                self.assertEqual("LOCKED_FAIL_CLOSED", packet["readiness"]["status"])
                self.assertEqual("NONE", packet["readiness"]["symbol"])
                self.assertTrue(any(item.startswith(prefix + ":") for item in packet["readiness"]["blockers"]))

    def test_named_prerequisite_negative_values_are_locked(self):
        mutations = []
        not_eligible = copy.deepcopy(self.source)
        not_eligible["universe_identity"]["decision_eligibility"] = "UNKNOWN"
        not_eligible["universe_identity"]["authority_status"] = "UNRATIFIED"
        sign(not_eligible["universe_identity"])
        mutations.append((not_eligible, "AUTHORITY_BEARING_ELIGIBILITY_MISSING"))

        no_interval = copy.deepcopy(self.source)
        no_interval["interval_semantics"]["status"] = "UNKNOWN"
        sign(no_interval["interval_semantics"])
        mutations.append((no_interval, "INTERVAL_SEMANTICS:KIS_INTERVAL_START_SEMANTICS_MISSING"))

        no_p9 = copy.deepcopy(self.source)
        policy = no_p9["market_data"]["input"]["freshness_policy"]
        policy["approval_status"] = "DRAFT"
        policy["packet_sha256"] = PIPELINE.MARKET_DATA.P9_FRESHNESS.payload_sha256(
            {key: item for key, item in policy.items() if key != "packet_sha256"}
        )
        mutations.append((no_p9, "MARKET_DATA:P9_01_RATIFIED_KOREA_POLICY_MISSING"))

        no_open_day = copy.deepcopy(self.source)
        no_open_day["market_data"]["input"]["calendar"]["status"] = "UNKNOWN"
        no_open_day["market_data"]["input"]["calendar"]["open_at"] = None
        no_open_day["market_data"]["input"]["calendar"]["close_at"] = None
        mutations.append((no_open_day, "MARKET_DATA:NATURAL_OPEN_DAY_SNAPSHOT_MISSING"))

        no_policy = copy.deepcopy(self.source)
        no_policy["policy_authority"]["status"] = "UNRATIFIED"
        sign(no_policy["policy_authority"])
        mutations.append((no_policy, "POLICY:RATIFIED_EFFECTIVE_POLICY_MISSING"))

        for source, blocker in mutations:
            with self.subTest(blocker=blocker):
                resign_input(source)
                packet = self.build(source)
                self.assertEqual("LOCKED_FAIL_CLOSED", packet["readiness"]["status"])
                self.assertEqual("NONE", packet["readiness"]["symbol"])
                self.assertIn(blocker, packet["readiness"]["blockers"])

    def test_stale_mixed_cross_date_and_incomplete_bar_fail_closed(self):
        mutations = []
        stale = copy.deepcopy(self.source)
        stale["universe_identity"]["valid_until_utc"] = stale["evaluated_at_utc"]
        sign(stale["universe_identity"])
        mutations.append((stale, "UNIVERSE:UNIVERSE_STALE_OR_LOOKAHEAD"))

        mixed = copy.deepcopy(self.source)
        mixed["proposal_input"]["universe"]["symbol"] = "000660"
        mixed["proposal_input"]["packet_sha256"] = PROPOSAL.payload_sha256(
            {key: item for key, item in mixed["proposal_input"].items() if key != "packet_sha256"}
        )
        mutations.append((mixed, "PROPOSAL:PROPOSAL_UPSTREAM_LINEAGE_MISMATCH"))

        cross_date = copy.deepcopy(self.source)
        cross_date["business_date"] = "2026-08-27"
        mutations.append((cross_date, "UNIVERSE:UNIVERSE_IDENTITY_OR_DATE_INVALID"))

        incomplete = copy.deepcopy(self.source)
        incomplete["market_data"]["input"]["series"][0]["bars"].pop()
        result = PIPELINE.MARKET_DATA.evaluate_packet(incomplete["market_data"]["input"])
        incomplete["market_data"]["expected_result_sha256"] = result["packet_sha256"]
        mutations.append((incomplete, "MARKET_DATA:COMPLETED_BARS_OR_FRESHNESS_NOT_PASS"))

        for source, blocker in mutations:
            with self.subTest(blocker=blocker):
                resign_input(source)
                packet = self.build(source)
                self.assertEqual("LOCKED_FAIL_CLOSED", packet["readiness"]["status"])
                self.assertTrue(any(
                    item == blocker or item.startswith(blocker + ":")
                    for item in packet["readiness"]["blockers"]
                ))

    def test_exact_and_conflicting_bar_duplicates_both_fail_closed(self):
        exact = copy.deepcopy(self.source)
        exact["market_data"]["input"]["series"][0]["bars"].append(
            copy.deepcopy(exact["market_data"]["input"]["series"][0]["bars"][-1])
        )
        exact_result = PIPELINE.MARKET_DATA.evaluate_packet(exact["market_data"]["input"])
        exact["market_data"]["expected_result_sha256"] = exact_result["packet_sha256"]
        resign_input(exact)
        packet = self.build(exact)
        self.assertIn(
            "MARKET_DATA:COMPLETED_BARS_OR_FRESHNESS_NOT_PASS",
            packet["readiness"]["blockers"],
        )

        conflict = copy.deepcopy(self.source)
        duplicate = copy.deepcopy(conflict["market_data"]["input"]["series"][0]["bars"][-1])
        duplicate["close"] = "106000"
        conflict["market_data"]["input"]["series"][0]["bars"].append(duplicate)
        conflict_result = PIPELINE.MARKET_DATA.evaluate_packet(conflict["market_data"]["input"])
        conflict["market_data"]["expected_result_sha256"] = conflict_result["packet_sha256"]
        resign_input(conflict)
        packet = self.build(conflict)
        self.assertEqual("LOCKED_FAIL_CLOSED", packet["readiness"]["status"])
        self.assertTrue(any("CONFLICTING_DUPLICATE" in item for item in packet["readiness"]["blockers"]))

    def test_same_identity_is_no_change_but_duplicate_or_conflict_locks(self):
        first = self.build()
        receipt = {
            "identity_sha256": first["replay"]["identity_sha256"],
            "proposal_sha256": first["replay"]["proposal_sha256"],
        }
        replay = copy.deepcopy(self.source)
        replay["prior_receipts"] = [receipt]
        resign_input(replay)
        self.assertEqual("NO_CHANGE", self.build(replay)["status"])

        duplicate = copy.deepcopy(replay)
        duplicate["prior_receipts"].append(copy.deepcopy(receipt))
        resign_input(duplicate)
        packet = self.build(duplicate)
        self.assertEqual("LOCKED_FAIL_CLOSED", packet["status"])
        self.assertIn("DUPLICATE_PROPOSAL_IDENTITY", packet["readiness"]["blockers"])

        conflict = copy.deepcopy(replay)
        conflict["prior_receipts"][0]["proposal_sha256"] = "f" * 64
        resign_input(conflict)
        packet = self.build(conflict)
        self.assertEqual("LOCKED_FAIL_CLOSED", packet["status"])
        self.assertIn("CONFLICTING_PROPOSAL_FOR_IDENTITY", packet["readiness"]["blockers"])

    def test_output_is_public_sanitized_and_contains_no_order_surface(self):
        packet = self.build()
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("005930", serialized)
        self.assertEqual(0, packet["proposal"]["quantity"])
        self.assertIsNone(packet["proposal"]["order_draft"])
        self.assertIsNone(packet["proposal"]["broker_route"])
        self.assertIsNone(packet["proposal"]["kis_submission"])
        self.assertEqual(0, packet["proposal"]["broker_post_count"])
        self.assertEqual(0, packet["proposal"]["kis_post_count"])
        self.assertTrue(packet["authority"]["public_readiness_only"])
        self.assertTrue(all(
            value is False for key, value in packet["authority"].items()
            if key != "public_readiness_only"
        ))

    def test_extra_private_input_or_authority_claim_is_rejected_and_not_echoed(self):
        source = copy.deepcopy(self.source)
        source["private_account_id"] = "SECRET-ACCOUNT"
        source["authority"]["paper_order_write"] = True
        resign_input(source)
        packet = self.build(source)
        self.assertIn("INPUT_FIELDS_INVALID", packet["readiness"]["blockers"])
        self.assertIn("INPUT_AUTHORITY_INVALID", packet["readiness"]["blockers"])
        self.assertNotIn("SECRET-ACCOUNT", json.dumps(packet))

    def test_resigned_output_tamper_is_rejected(self):
        packet = self.build()
        tampered = copy.deepcopy(packet)
        tampered["proposal"]["quantity"] = 1
        tampered["packet_sha256"] = PIPELINE.payload_sha256(
            {key: item for key, item in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(PIPELINE.KrxPaperPublicPipelineError, "OUTPUT_DERIVATION_MISMATCH"):
            PIPELINE.validate_packet(tampered, self.source, self.contract)

    def test_pipeline_has_no_network_broker_or_private_ledger_runtime(self):
        source = (ROOT / "orchestration" / "krx_paper_public_pipeline.py").read_text()
        for forbidden in (
            "import requests", "from requests", "urllib.request", "import socket",
            "socket.socket(", "requests.post(", "KIS_PAPER_APP_KEY", "private_evidence",
        ):
            self.assertNotIn(forbidden, source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
