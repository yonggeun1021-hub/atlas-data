#!/usr/bin/env python3
"""Regression contract for Atlas session continuity and authority boundaries."""
from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / "AGENTS.md"
BOOTSTRAP = ROOT / "docs" / "ATLAS_SESSION_BOOTSTRAP.md"
README = ROOT / "README.md"


class SessionBootstrapContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.agents = AGENTS.read_text()
        cls.bootstrap = BOOTSTRAP.read_text()
        cls.readme = README.read_text()

    def test_root_entry_contract_routes_every_session_to_bootstrap(self):
        self.assertIn("docs/ATLAS_SESSION_BOOTSTRAP.md", self.agents)
        self.assertIn("mandatory entry point", self.agents)
        self.assertIn("live Notion", self.agents)

    def test_both_repository_truths_and_private_boundary_are_mandatory(self):
        for text in (self.agents, self.bootstrap):
            self.assertIn("atlas-data", text)
            self.assertIn("atlas-private-evidence", text)
        self.assertIn("never private account facts", self.agents)
        self.assertIn("Private evidence must pull an immutable approved public commit", self.agents)

    def test_three_investment_layers_and_delivery_order_remain_visible(self):
        for phrase in (
            "Opportunity and entry",
            "Position and profit management",
            "Capital recycling",
            "P8-10 Price Reflection",
            "P8-12 Opportunity Trigger",
            "P5-06 Probe Entry Rule",
            "P7-08 portfolio position/thesis state",
            "P8-13 executable Entry Proposal",
            "P7-11 Profit Harvesting",
            "P7-10 Capital Reallocation",
        ):
            self.assertIn(phrase, self.bootstrap)

    def test_conditional_portal_parallel_gate_is_durable(self):
        for text in (self.agents, self.bootstrap):
            for phrase in (
                "P5-06",
                "P7-08",
                "P8-13",
                "Portal A",
                "integration closure has begun",
                "same accountable PM",
                "read-only",
                "trading authority",
            ):
                self.assertIn(phrase, text)

        self.assertIn("3c59f2d7-3c84-8134-96ba-ed9b95421e39", self.bootstrap)
        self.assertIn("3c79f2d7-3c84-8118-8edb-e75cd8a9e0da", self.bootstrap)
        self.assertIn("A renamed row, status-only edit, isolated stub", self.bootstrap)
        self.assertIn("Frontend broker credentials", self.bootstrap)

    def test_start_and_handoff_contracts_include_future_operational_ownership(self):
        for phrase in (
            "scheduled workflows and Codex automations",
            "scheduled/automation follow-ups",
        ):
            self.assertIn(phrase, self.agents)
        self.assertIn("scheduled workflow and automation obligations", self.bootstrap)
        self.assertIn("who will audit and synchronize the result", self.bootstrap)

    def test_authority_and_wbs_boundaries_are_explicit(self):
        for phrase in (
            "Point-in-time integrity",
            "No implied completion",
            "No invented policy",
            "Tracker -> Cockpit -> Master Map",
        ):
            self.assertIn(phrase, self.agents)
        self.assertIn("Stage, Buy, Action, Order, Production, and trading authority", self.bootstrap)

    def test_bootstrap_is_routing_not_frozen_status(self):
        self.assertIn("routing document, not a frozen status report", self.bootstrap)
        for status in ("🟡 개발중", "🔵 검증대기", "🟣 관측중", "✅ 완료"):
            self.assertNotIn(status, self.bootstrap)

    def test_readme_exposes_the_entry_contract(self):
        self.assertIn("AGENTS.md", self.readme)
        self.assertIn("docs/ATLAS_SESSION_BOOTSTRAP.md", self.readme)


if __name__ == "__main__":
    unittest.main()
