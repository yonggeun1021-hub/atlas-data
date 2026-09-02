#!/usr/bin/env python3
"""Production wiring invariants for Claude -> Codex -> Portal -> Notion."""
from __future__ import annotations

import json
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]


class ValidationFirstWiring(unittest.TestCase):
    def setUp(self):
        self.workflow = yaml.safe_load(
            (ROOT / ".github/workflows/daily-briefing.yml").read_text(encoding="utf-8"))
        self.steps = self.workflow["jobs"]["briefing"]["steps"]

    def _step(self, name):
        return next(step for step in self.steps if step.get("name") == name)

    def test_natural_producer_does_not_write_final_notion_or_deliver(self):
        self.assertEqual(
            self._step("Project canonical briefing to Notion SSOT")["if"],
            "steps.resolve.outputs.mode == 'drain'",
        )
        self.assertEqual(
            self._step("Ingest verdicts and deliver")["if"],
            "steps.resolve.outputs.mode == 'drain'",
        )

    def test_manual_drain_is_scoped_to_the_resolved_slot(self):
        run = self._step("Ingest verdicts and deliver")["run"]
        self.assertIn('--slot "${{ steps.resolve.outputs.slot }}"', run)
        self.assertIn('--decision-date "${{ steps.resolve.outputs.decision_date }}"', run)

    def test_semantic_validation_is_expected_and_timeout_holds(self):
        policy = json.loads(
            (ROOT / "config/atlas_semantic_validator.json").read_text(encoding="utf-8"))
        self.assertIs(policy["expected"], True)
        self.assertEqual(policy["timeout_action"], "HOLD")
        self.assertEqual(policy["caller"], "Codex heartbeat atlas-274-am-pm")

    def test_activation_requires_portal_before_final_notion_and_delivery(self):
        activation = json.loads(
            (ROOT / "config/atlas_finalization_activation.json").read_text(encoding="utf-8"))
        self.assertEqual(activation["active_from_kst_date"], "2026-08-29")
        self.assertEqual(activation["active_from_slot"], "morning")
        self.assertIs(activation["portal_before_delivery"], True)
        self.assertIs(activation["notion_final_after_portal"], True)


if __name__ == "__main__":
    unittest.main()
