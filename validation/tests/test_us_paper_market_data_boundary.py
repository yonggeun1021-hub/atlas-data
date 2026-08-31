#!/usr/bin/env python3
from __future__ import annotations

import ast
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCES = [
    ROOT / "universe" / "us_investable_registry.py",
    ROOT / "market_data" / "us_session_bars.py",
]
CONTRACTS = [
    ROOT / "config" / "us_investable_registry_contract.json",
    ROOT / "config" / "us_completed_market_data_contract.json",
    ROOT / "config" / "us_paper_funnel_handoff_contract.json",
]
WORKFLOW = ROOT / ".github" / "workflows" / "us-paper-market-data-contract.yml"


class BoundaryTests(unittest.TestCase):
    def test_no_network_broker_oauth_or_order_client_imports(self):
        forbidden = {"requests", "httpx", "urllib", "socket", "websocket", "alpaca", "boto3"}
        for path in SOURCES:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            self.assertFalse(imported & forbidden, (path, imported & forbidden))
            text = path.read_text(encoding="utf-8").lower()
            for marker in ("/orders", "oauth2/token", "client_secret", "account_number"):
                self.assertNotIn(marker, text)

    def test_all_authority_flags_remain_closed(self):
        for path in CONTRACTS:
            contract = json.loads(path.read_text(encoding="utf-8"))
            authority = contract["authority"]
            true_keys = [key for key, value in authority.items() if value is True]
            self.assertEqual(len(true_keys), 1)
            self.assertTrue(true_keys[0].endswith("_only"))
            for key in (
                "broker_oauth_authorized", "broker_order_post_authorized",
                "real_account_authorized", "real_capital_authorized",
                "production_authorized", "trading_authorized",
            ):
                self.assertIs(authority[key], False)

    def test_public_rights_and_threshold_defaults_are_closed(self):
        bars = json.loads(CONTRACTS[1].read_text(encoding="utf-8"))
        universe = json.loads(CONTRACTS[0].read_text(encoding="utf-8"))
        self.assertFalse(bars["source_rights"]["public_raw_persistence_authorized"])
        self.assertFalse(bars["source_rights"]["public_redistribution_authorized"])
        self.assertEqual(bars["freshness"]["repository_default_policy"], "ABSENT")
        self.assertEqual(universe["liquidity"]["repository_default_policy"], "ABSENT")
        self.assertFalse(universe["source_coverage_is_investability"])

    def test_paper_9_3_handoff_freezes_funnel_without_duplicate_integration(self):
        handoff = json.loads(CONTRACTS[2].read_text(encoding="utf-8"))
        funnel = handoff["canonical_funnel"]
        self.assertEqual(funnel["always_output"], ["TOP10", "TOP3"])
        self.assertEqual(funnel["candidate_min_score"], 60)
        self.assertEqual(funnel["ready_min_score"], 70)
        self.assertEqual(funnel["paper_buy_eligible_min_score"], 75)
        self.assertEqual(
            funnel["paper_buy_additional_requirements"],
            ["HARD_GATE_PASS", "COMPLETED_BAR_PASS"],
        )
        self.assertFalse(handoff["integration_implemented_here"])
        self.assertEqual(handoff["consumer_owner"], "PAPER_9_3")

    def test_focused_workflow_is_read_only_and_secret_free(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("contents: read", text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("permissions:\n  contents: write", text)
        self.assertIn("python3 run_all.py", text)
        self.assertIn("git diff --exit-code", text)


if __name__ == "__main__":
    unittest.main()
