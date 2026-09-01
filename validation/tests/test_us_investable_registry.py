#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "us_investable_registry", ROOT / "universe" / "us_investable_registry.py"
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

SHA = "1" * 64


def fact(status: str, available_at: str = "2026-08-28T20:05:00Z") -> dict:
    return {
        "status": status,
        "observed_at": "2026-08-28T20:00:00Z",
        "available_at": available_at,
        "source_ref": "official://fact",
        "source_sha256": SHA,
    }


def policy() -> dict:
    value = {
        "schema_version": "us_liquidity_policy/1",
        "policy_id": "US.PAPER.LIQUIDITY.TEST",
        "approval_status": "RATIFIED",
        "ratified_by": "SYNTHETIC TEST FIXTURE ONLY",
        "ratified_at": "2026-01-01T00:00:00Z",
        "effective_from": "2026-01-02T00:00:00Z",
        "effective_to": "2027-01-01T00:00:00Z",
        "min_median_daily_dollar_volume": "1000000",
        "min_median_daily_trade_count": "100",
        "max_median_spread_bps": "50",
        "min_observed_session_count": 20,
    }
    value["packet_sha256"] = MOD.payload_sha256(value)
    return value


def record(
    symbol: str = "ACME", instrument_type: str = "COMMON_STOCK", venue: str = "NASDAQ"
) -> dict:
    type_fact = fact("CONFIRMED")
    type_fact["source_kind"] = (
        "NASDAQ_SYMBOL_DIRECTORY" if instrument_type == "ETF" else "OFFICIAL_SECURITY_MASTER"
    )
    return {
        "asset_id": f"US.{venue}.{symbol}",
        "symbol": symbol,
        "listing_venue": venue,
        "instrument_type": instrument_type,
        "type_evidence": type_fact,
        "etf_indicator": "Y" if instrument_type == "ETF" else "N",
        "test_issue": False,
        "financial_status": "NORMAL",
        "listing": fact("ACTIVE"),
        "trading_halt": fact("NOT_HALTED"),
        "scheduled_delisting": fact("NONE_SCHEDULED"),
        "corporate_action_state": fact("CLEAR"),
        "liquidity": {
            "window_end": "2026-08-28",
            "observed_at": "2026-08-28T20:00:00Z",
            "available_at": "2026-08-28T20:05:00Z",
            "source_ref": "provider://derived-liquidity",
            "source_sha256": SHA,
            "median_daily_dollar_volume": "2500000",
            "median_daily_trade_count": "500",
            "median_spread_bps": "12.5",
            "observed_session_count": 20,
        },
    }


def packet(records: list[dict]) -> dict:
    contract = MOD.load_contract()
    return {
        "schema_version": "us_investable_snapshot/1",
        "decision_at": "2026-08-28T20:10:00Z",
        "source_coverage": {
            "snapshot_date": "2026-08-28",
            "source_id": "NASDAQ.TRADER.SYMBOL.DIRECTORY",
            "source_ref": "https://www.nasdaqtrader.com/dynamic/SymDir/",
            "source_sha256": SHA,
            "observed_at": "2026-08-28T20:00:00Z",
            "available_at": "2026-08-28T20:01:00Z",
            "coverage_scope": "CURRENT_FORWARD_ONLY",
            "redistribution_status": "UNKNOWN",
        },
        "records": records,
        "liquidity_policy": policy(),
        "authority": contract["authority"],
    }


class ContractTests(unittest.TestCase):
    def test_authority_and_no_default_liquidity_policy_are_closed(self):
        contract = MOD.load_contract()
        self.assertEqual(contract["liquidity"]["repository_default_policy"], "ABSENT")
        self.assertFalse(contract["authority"]["broker_order_post_authorized"])
        self.assertFalse(contract["authority"]["real_capital_authorized"])
        self.assertFalse(contract["authority"]["production_authorized"])
        self.assertFalse(contract["authority"]["trading_authorized"])

    def test_common_stock_and_etf_are_separate_and_eligible_only_with_proof(self):
        common = record()
        etf = record("FUND", "ETF", "NYSE_ARCA")
        result = MOD.evaluate_registry(packet([common, etf]))
        self.assertEqual(result["eligible_count"], 2)
        self.assertEqual(result["eligible_common_stock_count"], 1)
        self.assertEqual(result["eligible_etf_count"], 1)
        self.assertFalse(result["public_raw_retention_authorized"])
        MOD.validate_result(result)

    def test_etf_no_does_not_prove_common_stock(self):
        row = record()
        row["type_evidence"]["source_kind"] = "NASDAQ_SYMBOL_DIRECTORY"
        result = MOD.evaluate_registry(packet([row]))
        self.assertFalse(result["records"][0]["eligible"])
        self.assertIn("COMMON_STOCK_REQUIRES_SECURITY_MASTER", result["records"][0]["reasons"])

    def test_otc_halt_delisting_abnormal_action_and_low_liquidity_fail_closed(self):
        cases = []
        otc = record("OTCX", venue="OTC")
        cases.append(otc)
        halted = record("HALT")
        halted["trading_halt"]["status"] = "HALTED"
        cases.append(halted)
        delisting = record("DELIST")
        delisting["scheduled_delisting"]["status"] = "SCHEDULED"
        cases.append(delisting)
        action = record("ACTION")
        action["corporate_action_state"]["status"] = "UNRESOLVED"
        cases.append(action)
        illiquid = record("ILLIQ")
        illiquid["liquidity"]["median_daily_dollar_volume"] = "999999"
        cases.append(illiquid)
        result = MOD.evaluate_registry(packet(cases))
        self.assertEqual(result["eligible_count"], 0)
        reasons = {row["symbol"]: row["reasons"] for row in result["records"]}
        self.assertIn("OTC_OR_UNSUPPORTED_VENUE", reasons["OTCX"])
        self.assertIn("HALT_OR_UNKNOWN", reasons["HALT"])
        self.assertIn("DELISTING_OR_UNKNOWN", reasons["DELIST"])
        self.assertIn("CORPORATE_ACTION_UNRESOLVED", reasons["ACTION"])
        self.assertIn("LOW_LIQUIDITY", reasons["ILLIQ"])

    def test_unknown_statuses_and_non_normal_directory_flags_are_excluded(self):
        row = record("RISKY")
        row["test_issue"] = None
        row["financial_status"] = "DEFICIENT"
        row["listing"]["status"] = "UNKNOWN"
        result = MOD.evaluate_registry(packet([row]))
        self.assertEqual(result["records"][0]["eligibility"], "EXCLUDED_FAIL_CLOSED")
        self.assertIn("TEST_ISSUE_OR_UNKNOWN", result["records"][0]["reasons"])
        self.assertIn("FINANCIAL_STATUS_NOT_NORMAL", result["records"][0]["reasons"])

    def test_policy_absent_unratified_and_future_fact_raise(self):
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "RECORDS_NOT_LIST"):
            MOD.evaluate_registry(packet([]))
        value = packet([record()])
        value["liquidity_policy"] = None
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "LIQUIDITY_POLICY_FIELDS_INVALID"):
            MOD.evaluate_registry(value)
        value = packet([record()])
        value["liquidity_policy"]["approval_status"] = "DRAFT"
        value["liquidity_policy"]["packet_sha256"] = MOD.payload_sha256(
            {k: v for k, v in value["liquidity_policy"].items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "NOT_RATIFIED"):
            MOD.evaluate_registry(value)
        value = packet([record()])
        value["records"][0]["trading_halt"]["available_at"] = "2026-08-28T20:11:00Z"
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "HALT_TIME_ORDER_INVALID"):
            MOD.evaluate_registry(value)

    def test_duplicate_and_result_tamper_fail(self):
        row = record()
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "DUPLICATE"):
            MOD.evaluate_registry(packet([row, copy.deepcopy(row)]))
        duplicate_asset = record("SECOND")
        duplicate_asset["asset_id"] = row["asset_id"]
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "DUPLICATE"):
            MOD.evaluate_registry(packet([row, duplicate_asset]))
        duplicate_symbol = record("ACME", venue="NYSE")
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "DUPLICATE"):
            MOD.evaluate_registry(packet([row, duplicate_symbol]))
        result = MOD.evaluate_registry(packet([row]))
        result["eligible_count"] = 0
        with self.assertRaisesRegex(MOD.UsInvestableRegistryError, "RESULT_SHA_INVALID_MISMATCH"):
            MOD.validate_result(result)

    def test_deterministic_under_record_order(self):
        one = record("AAA")
        two = record("BBB", "ETF", "NYSE_ARCA")
        left = MOD.evaluate_registry(packet([one, two]))
        right = MOD.evaluate_registry(packet([two, one]))
        self.assertEqual(left, right)


if __name__ == "__main__":
    unittest.main()
