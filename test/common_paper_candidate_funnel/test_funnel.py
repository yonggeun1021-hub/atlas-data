#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "decision" / "common_paper_candidate_funnel.py"
SPEC = importlib.util.spec_from_file_location("common_paper_candidate_funnel", MODULE_PATH)
FUNNEL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(FUNNEL)
FIXTURES = ROOT / "test" / "fixtures" / "common_paper_candidate_funnel"


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ContractAndSchemaTests(unittest.TestCase):
    def test_contract_and_json_schema_are_locked(self):
        contract = FUNNEL.load_contract()
        schema = FUNNEL.load_schema()
        self.assertEqual(contract["funnel"]["sequence"], [
            "UNIVERSE", "TOP10", "TOP3", "CANDIDATE", "READY", "PAPER_BUY_ELIGIBLE",
        ])
        self.assertEqual((60, 70, 75), (
            contract["funnel"]["candidate_min_score"],
            contract["funnel"]["ready_min_score"],
            contract["funnel"]["paper_buy_eligible_min_score"],
        ))
        self.assertEqual(schema["$defs"]["input"]["properties"]["schemaVersion"]["const"], FUNNEL.INPUT_SCHEMA_VERSION)
        self.assertEqual(schema["$defs"]["output"]["properties"]["schemaVersion"]["const"], FUNNEL.OUTPUT_SCHEMA_VERSION)
        self.assertFalse(schema["$defs"]["candidateOutput"]["additionalProperties"])
        self.assertEqual(set(FUNNEL.HARD_GATES), set(schema["$defs"]["effectiveGates"]["required"]))

    def test_authority_contract_is_internal_virtual_only(self):
        contract = FUNNEL.load_contract()
        self.assertTrue(contract["paper_internal_authority"]["PAPER_INTERNAL_AUTO"])
        self.assertFalse(contract["paper_internal_authority"]["humanApprovalRequired"])
        self.assertFalse(contract["paper_internal_authority"]["userReceiptRequired"])
        self.assertEqual(contract["paper_internal_authority"]["broker_mock_post_count"], 0)
        self.assertTrue(all(value is False for value in contract["permanent_false_authority"].values()))

    def test_us_paper_transport_is_explicit_zero_call_and_no_fallback(self):
        transport = FUNNEL.load_contract()["us_paper_transport_boundary"]
        self.assertEqual(
            [{"id": "ALPACA_PAPER", "priority": 1}, {"id": "KIS_US_PAPER", "priority": 2}],
            transport["profiles"],
        )
        self.assertTrue(transport["explicit_selector_required"])
        self.assertFalse(transport["automatic_fallback_authorized"])
        self.assertFalse(transport["internal_virtual_paper_grants_external_transport"])
        self.assertEqual("NETWORK_GET_POST_ZERO", transport["missing_credentials_or_admission_policy"])
        self.assertEqual((0, 0, 0), (
            transport["network_call_count"], transport["get_call_count"], transport["post_call_count"],
        ))


class DeterministicReducerTests(unittest.TestCase):
    def test_thresholds_top_lists_ttl_and_score_breakdown(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        self.assertEqual([78, 76, 74, 59], [row["score"] for row in result["universe"]])
        self.assertEqual(4, len(result["top10"]))
        self.assertEqual(3, len(result["top3"]))
        self.assertEqual(3, len(result["candidates"]))
        self.assertEqual(3, len(result["ready"]))
        self.assertEqual(2, len(result["paperBuyEligible"]))
        self.assertEqual("2026-08-31T06:10:00Z", result["universe"][2]["expiresAt"])
        self.assertIn("SCORE_BELOW_CANDIDATE:59<60", result["universe"][3]["reasons"])
        self.assertEqual(100, sum(row["maxPoints"] for row in result["universe"][0]["scoreBreakdown"]))

    def test_same_input_is_byte_deterministic_and_order_independent(self):
        source = fixture("all_pass.json")
        first = FUNNEL.reduce_funnel(source)
        second = FUNNEL.reduce_funnel(copy.deepcopy(source))
        reversed_source = copy.deepcopy(source)
        reversed_source["candidates"].reverse()
        third = FUNNEL.reduce_funnel(reversed_source)
        self.assertEqual(FUNNEL.canonical_json(first), FUNNEL.canonical_json(second))
        self.assertEqual([row["candidateId"] for row in first["universe"]], [row["candidateId"] for row in third["universe"]])
        self.assertEqual(first["payloadSha256"], second["payloadSha256"])
        self.assertEqual(first["evaluationId"], third["evaluationId"])

    def test_top10_and_top3_are_present_for_empty_universe(self):
        payload = fixture("all_pass.json")
        payload["candidates"] = []
        result = FUNNEL.reduce_funnel(payload)
        self.assertEqual([], result["top10"])
        self.assertEqual([], result["top3"])
        self.assertEqual("UNIVERSE_HAS_ONLY_0_ROWS", result["summary"]["top10UnderfilledReason"])
        self.assertEqual("UNIVERSE_HAS_ONLY_0_ROWS", result["summary"]["top3UnderfilledReason"])


class FailClosedGateTests(unittest.TestCase):
    def test_stale_null_safety_and_risk_limits_block_high_score(self):
        result = FUNNEL.reduce_funnel(fixture("fail_closed.json"))
        row = result["universe"][0]
        self.assertEqual(90, row["score"])
        self.assertTrue(row["funnelFlags"]["ready"])
        self.assertFalse(row["funnelFlags"]["paperBuyEligible"])
        self.assertEqual("FAIL", row["hardGates"]["FRESHNESS"]["status"])
        self.assertEqual("SOURCE_TTL_EXPIRED", row["hardGates"]["FRESHNESS"]["reason"])
        self.assertEqual("FAIL", row["hardGates"]["RISK_BUDGET"]["status"])
        self.assertIsNone(row["hardGates"]["MARKET_SPECIFIC_SAFETY"]["status"])

    def test_missing_gate_is_preserved_as_null_and_blocks(self):
        payload = fixture("all_pass.json")
        del payload["candidates"][0]["hardGates"]["LEDGER_INTEGRITY"]
        result = FUNNEL.reduce_funnel(payload)
        row = next(item for item in result["universe"] if item["candidateId"].startswith("KR-"))
        self.assertIsNone(row["hardGates"]["LEDGER_INTEGRITY"]["status"])
        self.assertFalse(row["funnelFlags"]["paperBuyEligible"])

    def test_duplicate_identity_marks_all_duplicates_fail_closed(self):
        payload = fixture("all_pass.json")
        payload["candidates"].append(copy.deepcopy(payload["candidates"][0]))
        result = FUNNEL.reduce_funnel(payload)
        duplicates = [row for row in result["universe"] if row["candidateId"] == "KR-005930-20260831"]
        self.assertEqual(2, len(duplicates))
        self.assertTrue(all(row["hardGates"]["DUPLICATE_IDEMPOTENCY"]["status"] == "FAIL" for row in duplicates))
        self.assertTrue(all(not row["funnelFlags"]["paperBuyEligible"] for row in duplicates))

    def test_completed_bar_gate_cannot_be_forged_by_gate_status_alone(self):
        payload = fixture("all_pass.json")
        payload["candidates"][0]["completedBarTrigger"] = {"status": "PASS", "barId": None, "completedAt": None}
        result = FUNNEL.reduce_funnel(payload)
        row = next(item for item in result["universe"] if item["candidateId"].startswith("KR-"))
        self.assertFalse(row["funnelFlags"]["paperBuyEligible"])
        self.assertEqual("FAIL", row["hardGates"]["COMPLETED_BAR"]["status"])

    def test_future_source_timestamp_fails_freshness(self):
        payload = fixture("all_pass.json")
        payload["candidates"][0]["sourceTimestamp"] = "2026-08-31T06:01:00Z"
        result = FUNNEL.reduce_funnel(payload)
        row = next(item for item in result["universe"] if item["candidateId"].startswith("KR-"))
        self.assertEqual("FAIL", row["hardGates"]["FRESHNESS"]["status"])
        self.assertEqual("SOURCE_TIMESTAMP_FUTURE_DATED", row["hardGates"]["FRESHNESS"]["reason"])
        self.assertFalse(row["funnelFlags"]["paperBuyEligible"])

    def test_score_max_must_equal_100(self):
        payload = fixture("all_pass.json")
        payload["candidates"][0]["scoreBreakdown"][0]["maxPoints"] = 49
        with self.assertRaisesRegex(FUNNEL.CommonPaperCandidateFunnelError, "SCORE_MAX_TOTAL_NOT_100"):
            FUNNEL.reduce_funnel(payload)

    def test_tampered_output_and_authority_expansion_are_rejected(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        result["authority"]["real"] = True
        result["payloadSha256"] = FUNNEL.payload_sha256({key: value for key, value in result.items() if key != "payloadSha256"})
        with self.assertRaisesRegex(FUNNEL.CommonPaperCandidateFunnelError, "OUTPUT_PERMANENT_AUTHORITY_NOT_FALSE"):
            FUNNEL.validate_output(result)

    def test_rehashed_row_authority_expansion_is_rejected(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        result["universe"][0]["authority"]["brokerNetworkPost"] = True
        for key in ("top10", "top3", "candidates", "ready", "paperBuyEligible"):
            result[key] = [row for row in result["universe"] if (
                row["funnelFlags"]["top10"] if key == "top10" else
                row["funnelFlags"]["top3"] if key == "top3" else
                row["funnelFlags"]["candidate"] if key == "candidates" else
                row["funnelFlags"]["ready"] if key == "ready" else
                row["funnelFlags"]["paperBuyEligible"]
            )]
        result["payloadSha256"] = FUNNEL.payload_sha256({key: value for key, value in result.items() if key != "payloadSha256"})
        with self.assertRaisesRegex(FUNNEL.CommonPaperCandidateFunnelError, "OUTPUT_ROW_DERIVATION_MISMATCH"):
            FUNNEL.validate_output(result)

    def test_rehashed_exposure_netting_and_summary_tamper_are_rejected(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        hedge = next(row for row in result["universe"] if row["candidateLane"] == "DEFENSIVE_ACTION")
        hedge["risk"]["exposureAccounting"]["nettedMarketExposureNavFraction"] = "0.03"
        result["payloadSha256"] = FUNNEL.payload_sha256({key: value for key, value in result.items() if key != "payloadSha256"})
        with self.assertRaisesRegex(FUNNEL.CommonPaperCandidateFunnelError, "OUTPUT_EXPOSURE_ACCOUNTING_INVALID"):
            FUNNEL.validate_output(result)

        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        result["summary"]["paperBuyEligibleCount"] = 99
        result["payloadSha256"] = FUNNEL.payload_sha256({key: value for key, value in result.items() if key != "payloadSha256"})
        with self.assertRaisesRegex(FUNNEL.CommonPaperCandidateFunnelError, "OUTPUT_SUMMARY_DERIVATION_MISMATCH"):
            FUNNEL.validate_output(result)


class LaneAndZeroCallTests(unittest.TestCase):
    def test_canary_and_investment_performance_are_separate(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        lanes = result["summary"]["lanes"]
        self.assertEqual("SYSTEM_CANARY", lanes["SYSTEM_CANARY"]["performanceCohort"])
        self.assertEqual("INVESTMENT_PAPER", lanes["INVESTMENT_PAPER"]["performanceCohort"])
        self.assertEqual("SYSTEM_HEDGE_CANARY", lanes["SYSTEM_HEDGE_CANARY"]["performanceCohort"])
        self.assertEqual(1, lanes["SYSTEM_HEDGE_CANARY"]["paperBuyEligibleCount"])
        self.assertFalse(result["summary"]["combinedPerformanceAuthorized"])
        self.assertFalse(result["summary"]["systemHedgeCanaryInvestmentPerformanceAuthorized"])
        self.assertEqual(0, result["summary"]["brokerMockPostCount"])
        self.assertEqual(0, result["summary"]["externalSystemCallCount"])
        self.assertEqual(0, result["summary"]["externalNetworkGetCount"])
        self.assertEqual(0, result["summary"]["externalNetworkPostCount"])

    def test_approval_fields_are_output_policy_not_input_gates(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        eligible = result["paperBuyEligible"][0]
        self.assertTrue(eligible["authority"]["PAPER_INTERNAL_AUTO"])
        self.assertFalse(eligible["authority"]["humanApprovalRequired"])
        self.assertFalse(eligible["authority"]["userReceiptRequired"])
        self.assertTrue(eligible["authority"]["internalVirtualLedgerMutationEligible"])
        for key in FUNNEL.load_contract()["permanent_false_authority"]:
            self.assertFalse(eligible["authority"][key])

    def test_hedge_exposure_is_a_separate_non_netted_bucket(self):
        result = FUNNEL.reduce_funnel(fixture("all_pass.json"))
        hedge = next(row for row in result["universe"] if row["candidateLane"] == "DEFENSIVE_ACTION")
        accounting = hedge["risk"]["exposureAccounting"]
        self.assertEqual("0.05", accounting["longExposureBucketNavFraction"])
        self.assertEqual("0.02", accounting["hedgeExposureBucketNavFraction"])
        self.assertEqual(1, accounting["longPositionCount"])
        self.assertEqual(0, accounting["hedgePositionCount"])
        self.assertIsNone(accounting["nettedMarketExposureNavFraction"])
        self.assertTrue(accounting["hedgeExcludedFromLongExposure"])
        self.assertTrue(accounting["hedgePositionExcludedFromLongPositionCount"])
        self.assertFalse(accounting["longHedgeCrossBucketNettingAuthorized"])

    def test_hedge_above_half_long_exposure_fails_risk_gate(self):
        payload = fixture("all_pass.json")
        hedge = next(row for row in payload["candidates"] if row["candidateLane"] == "DEFENSIVE_ACTION")
        hedge["risk"]["hedgeMarketExposureNavFraction"] = "0.03"
        result = FUNNEL.reduce_funnel(payload)
        row = next(row for row in result["universe"] if row["candidateLane"] == "DEFENSIVE_ACTION")
        self.assertEqual("FAIL", row["hardGates"]["RISK_BUDGET"]["status"])
        self.assertFalse(row["funnelFlags"]["paperBuyEligible"])


if __name__ == "__main__":
    unittest.main()
