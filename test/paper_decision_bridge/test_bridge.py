#!/usr/bin/env python3
"""PAPER 12-1 Flow-First decision bridge focused regression."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "decision" / "paper_decision_bridge.py"
FIXTURE = ROOT / "test" / "fixtures" / "paper_decision_bridge" / "all_unknown.json"
FUNNEL_FIXTURE = ROOT / "test" / "fixtures" / "common_paper_candidate_funnel" / "all_pass.json"


def load_module():
    spec = importlib.util.spec_from_file_location("paper_decision_bridge_test", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()
CONTRACT = MODULE.load_contract()


def source_for(market: str) -> dict:
    names = {"KRX": "krx", "US": "us", "CRYPTO": "crypto"}
    path = ROOT / "test" / "fixtures" / "paper_decision_bridge" / f"{names[market]}_source.json"
    return {"ref": str(path.relative_to(ROOT)), "sha256": MODULE.file_sha256(path)}


def unknown_input() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def all_pass_gate(market: str) -> dict:
    return {"status": "PASS", "reason": "FIXTURE_LITERAL_PASS", "sources": [source_for(market)]}


def candidate_for(market: str, candidate_id: str, score_source_index: int = 0) -> dict:
    source_rows = json.loads(FUNNEL_FIXTURE.read_text(encoding="utf-8"))["candidates"]
    base = copy.deepcopy(source_rows[score_source_index])
    base["market"] = CONTRACT["funnel_market_map"][market]
    base["candidateId"] = candidate_id
    base["symbol"] = candidate_id.split("-")[-1]
    base["sourceTimestamp"] = "2026-08-31T08:55:00Z"
    base["ttlSeconds"] = 900
    base["completedBarTrigger"]["completedAt"] = "2026-08-31T08:45:00Z"
    return {
        "displayName": f"Fixture {candidate_id}",
        "tickerCode": base["symbol"],
        "upstreamAction": "BUY",
        "tradePlan": {
            "entryPrice": "100", "stopPrice": "95", "takeProfitPrice": "110",
            "quantity": "1", "expiresAt": "2026-08-31T09:10:00Z",
        },
        "tradePlanGate": all_pass_gate(market),
        "exactSources": [source_for(market)],
        "funnelCandidate": base,
    }


def attach_valid_unknown_header(value: dict, directory: Path) -> None:
    regime = MODULE.REGIME_HEADER.regime_output
    sources = [regime.build_unknown_output(market, "2026-08-31T08:40:00Z") for market in ("US", "KR", "CRYPTO")]
    header = MODULE.REGIME_HEADER.build_header(sources, "morning", "2026-08-31T08:50:00Z")
    path = directory / "regime-header.json"
    path.write_text(json.dumps(header, sort_keys=True) + "\n", encoding="utf-8")
    value["regimeHeader"] = header
    value["regimeHeaderSource"] = {"ref": str(path), "sha256": MODULE.file_sha256(path)}


class PaperDecisionBridgeTests(unittest.TestCase):
    def test_contract_preserves_three_markets_and_within_market_top3(self):
        self.assertEqual(CONTRACT["markets"], ["KRX", "US", "CRYPTO"])
        self.assertEqual(CONTRACT["ranking_scope"], "INDEPENDENT_WITHIN_EACH_MARKET_ONLY")
        self.assertIn("NOT_THREE_MARKETS", CONTRACT["top3_semantics"])
        self.assertEqual(CONTRACT["thresholds"], {"candidate": 60, "ready": 70, "paper_buy_eligible": 75})

    def test_leadership_policy_is_exact_and_observation_only(self):
        self.assertEqual(CONTRACT["leadership_policy"]["KRX"]["approval_status"], "RATIFIED")
        self.assertEqual(CONTRACT["leadership_policy"]["US"]["approval_status"], "UNRATIFIED")
        self.assertEqual(CONTRACT["leadership_policy"]["CRYPTO"]["approval_status"], "RATIFIED")
        self.assertEqual(CONTRACT["leadership_policy"]["CRYPTO"]["group_coverage_status"], "UNRATIFIED")
        self.assertIn("OBSERVATION_ONLY", CONTRACT["leadership_semantics"])

    def test_all_unknown_is_action_null_wait_and_crypto_observed_ranking_survives(self):
        receipt = MODULE.build_receipt(unknown_input())
        self.assertTrue(receipt["summary"]["allMarketsUnknown"])
        self.assertIsNone(receipt["summary"]["action"])
        self.assertEqual(receipt["summary"]["recommendation"], "WAIT")
        self.assertEqual(receipt["summary"]["paperTransitionCount"], 0)
        crypto = next(row for row in receipt["markets"] if row["market"] == "CRYPTO")
        self.assertEqual(crypto["rankings"]["observed"]["top3"], ["KRW-BTC", "KRW-ETH", "KRW-LINK"])
        self.assertEqual(crypto["rankings"]["computedWithinMarket"]["top3"], [])
        self.assertFalse(crypto["rankings"]["crossMarketRankingAuthorized"])

    def test_flow_first_trace_has_every_exact_stage_and_disconnection_reasons(self):
        receipt = MODULE.build_receipt(unknown_input())
        for market in receipt["markets"]:
            self.assertEqual([row["stage"] for row in market["trace"]], CONTRACT["trace_order"])
            self.assertFalse(any(row["paperTransitioned"] for row in market["trace"]))
            self.assertTrue(any("UPSTREAM_TRACE_DISCONNECTED" in row["reasons"] for row in market["trace"][1:]))

    def test_candidate_fields_are_complete_but_fixture_never_promotes(self):
        value = unknown_input()
        with tempfile.TemporaryDirectory() as directory:
            attach_valid_unknown_header(value, Path(directory))
            krx = value["markets"][0]
            krx["candidates"] = [candidate_for("KRX", "KRX-FIXTURE-A")]
            krx["observedRanking"] = {"universe": ["KRX-FIXTURE-A"], "top10": ["KRX-FIXTURE-A"], "top3": ["KRX-FIXTURE-A"]}
            krx["lifecycleGates"] = {gate: all_pass_gate("KRX") for gate in CONTRACT["market_lifecycle_gates"]}
            krx["traceStages"] = {stage: all_pass_gate("KRX") for stage in CONTRACT["trace_order"]}
            krx["riskPacket"] = {
                "status": "PASS", "cashAction": "NO_CHANGE", "exposureAction": "NO_CHANGE",
                "inverseAction": None, "hedgeAction": None, "reason": "FIXTURE_LITERAL_PASS",
                "sources": [source_for("KRX")],
            }
            result = MODULE.build_receipt(value)["markets"][0]["results"][0]
        self.assertEqual(result["score"], 78)
        self.assertEqual(result["funnel"]["highestStage"], "PAPER_BUY_ELIGIBLE")
        self.assertEqual(result["display_name"], "Fixture KRX-FIXTURE-A")
        self.assertEqual(result["market"], "KRX")
        self.assertEqual(result["tradePlan"]["entryPrice"], "100")
        self.assertIsNone(result["action"])
        self.assertEqual(result["recommendation"], "WAIT")
        self.assertIn("FIXTURE_NOT_PROMOTABLE", result["reasons"])
        self.assertIn("PINNED_COMPONENT_AUTHORITY_BLOCKS_PAPER_TRANSITION", result["reasons"])

    def test_rank_is_independent_per_market_not_cross_market(self):
        value = unknown_input()
        value["markets"][0]["candidates"] = [candidate_for("KRX", "KRX-A", 0)]
        value["markets"][1]["candidates"] = [candidate_for("US", "US-A", 1)]
        receipt = MODULE.build_receipt(value)
        krx = receipt["markets"][0]["results"][0]
        us = receipt["markets"][1]["results"][0]
        self.assertEqual(krx["rankWithinMarket"], 1)
        self.assertEqual(us["rankWithinMarket"], 1)
        self.assertNotEqual(krx["score"], us["score"])

    def test_hash_mismatch_and_stale_ttl_fail_closed(self):
        value = unknown_input()
        value["markets"][0]["exactSources"][0]["sha256"] = "0" * 64
        value["markets"][0]["sourceTimestamp"] = "2026-08-31T01:00:00Z"
        market = MODULE.build_receipt(value)["markets"][0]
        self.assertFalse(market["sourceFresh"])
        self.assertEqual(market["lifecycleGates"]["FRESHNESS"]["status"], "FAIL")
        self.assertIsNone(market["action"])
        self.assertEqual(market["recommendation"], "WAIT")

    def test_wave10_adapter_preserves_missing_ttl_and_existing_crypto_ranking(self):
        reports = {
            "krx": {
                "schemaVersion": "krx_paper_natural_scheduled_gate_canonical_report/1",
                "generatedAtUtc": "2026-08-31T08:40:00Z",
                "admissionReceipt": {"gateAudit": {}},
            },
            "us": {
                "schemaVersion": "us_paper_10_4_natural_scheduled_gate_report/1",
                "evaluatedAtUtc": "2026-08-31T08:36:04Z",
            },
            "crypto": {
                "schemaVersion": "crypto_spot_paper_10_2_natural_canary_preparation_report/1",
                "natural": {
                    "universe": ["KRW-BTC", "KRW-ETH"],
                    "top10": ["KRW-BTC", "KRW-ETH"],
                    "top3": ["KRW-BTC"],
                    "reconciliation": "MATCHED",
                },
            },
        }
        with tempfile.TemporaryDirectory() as directory:
            paths = {}
            for name, report in reports.items():
                path = Path(directory) / f"{name}.json"
                path.write_text(json.dumps(report) + "\n", encoding="utf-8")
                paths[name] = path
            value = MODULE.build_wave10_natural_input(
                paths["krx"], paths["us"], paths["crypto"], "2026-08-31T09:00:00Z"
            )
            receipt = MODULE.build_receipt(value)
        self.assertEqual(value["evidenceClass"], "NATURAL_READ_ONLY")
        self.assertTrue(all(market["ttlSeconds"] is None for market in value["markets"]))
        self.assertEqual(receipt["markets"][2]["rankings"]["observed"]["top3"], ["KRW-BTC"])
        self.assertFalse(any(market["sourceFresh"] for market in receipt["markets"]))
        self.assertIsNone(receipt["summary"]["action"])
        self.assertEqual(receipt["summary"]["recommendation"], "WAIT")

    def test_unverified_trade_plan_values_are_replaced_with_null(self):
        value = unknown_input()
        candidate = candidate_for("KRX", "KRX-PLAN-BAD")
        candidate["exactSources"][0]["sha256"] = "f" * 64
        value["markets"][0]["candidates"] = [candidate]
        result = MODULE.build_receipt(value)["markets"][0]["results"][0]
        self.assertTrue(all(item is None for item in result["tradePlan"].values()))
        self.assertEqual(result["tradePlanGate"]["reason"], "UNVERIFIED_TRADE_PLAN_VALUES_DROPPED")

    def test_receipt_is_immutable_and_same_identity_is_no_change(self):
        receipt = MODULE.build_receipt(unknown_input())
        with tempfile.TemporaryDirectory() as directory:
            first_path, first = MODULE.persist_immutable_receipt(receipt, Path(directory))
            second_path, second = MODULE.persist_immutable_receipt(receipt, Path(directory))
            self.assertEqual(first, "CREATED")
            self.assertEqual(second, "NO_CHANGE")
            self.assertEqual(first_path, second_path)
            self.assertEqual(json.loads(first_path.read_text())["receiptSha256"], receipt["receiptSha256"])

    def test_receipt_tamper_is_rejected(self):
        receipt = MODULE.build_receipt(unknown_input())
        receipt["summary"]["recommendation"] = "BUY"
        with self.assertRaisesRegex(MODULE.PaperDecisionBridgeError, "RECEIPT_SHA_MISMATCH"):
            MODULE.validate_receipt(receipt)

    def test_cli_writes_no_change_on_identical_second_run(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = root / "first.json"
            second = root / "second.json"
            receipts = root / "receipts"
            self.assertEqual(MODULE.run(FIXTURE, first, receipts), 0)
            self.assertEqual(MODULE.run(FIXTURE, second, receipts), 0)
            self.assertEqual(json.loads(first.read_text())["disposition"], "CREATED")
            self.assertEqual(json.loads(second.read_text())["disposition"], "NO_CHANGE")

    def test_static_zero_transport_and_zero_order_boundary(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = {alias.name.split(".")[0] for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
        self.assertTrue(imports.isdisjoint({"requests", "httpx", "urllib", "socket", "subprocess", "ccxt"}))
        receipt = MODULE.build_receipt(unknown_input())
        self.assertTrue(all(value is False for value in receipt["authority"].values()))
        self.assertEqual(receipt["summary"]["ledgerMutationCount"], 0)


if __name__ == "__main__":
    unittest.main()
