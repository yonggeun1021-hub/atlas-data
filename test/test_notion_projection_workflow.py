from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/daily-briefing.yml"


class NotionProjectionWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")
        cls.doc = yaml.safe_load(cls.text)
        cls.steps = cls.doc["jobs"]["briefing"]["steps"]

    @classmethod
    def named(cls, name):
        return next(step for step in cls.steps if step.get("name") == name)

    def test_projection_is_a_pre_delivery_gate(self):
        names = [step.get("name") for step in self.steps]
        self.assertLess(names.index("Publish validator verdict"),
                        names.index("Project canonical briefing to Notion SSOT"))
        self.assertLess(names.index("Project canonical briefing to Notion SSOT"),
                        names.index("Ingest verdicts and deliver"))
        step = self.named("Project canonical briefing to Notion SSOT")
        self.assertNotIn("continue-on-error", step)
        self.assertNotIn("if", step)

    def test_projection_uses_ci_secret_and_readback_adapter(self):
        step = self.named("Project canonical briefing to Notion SSOT")
        self.assertEqual(step["env"]["NOTION_TOKEN"], "${{ secrets.NOTION_TOKEN }}")
        self.assertIn("notion_projection_adapter.py", step["run"])
        self.assertIn("--sync-all", step["run"])
        self.assertIn("--live-canary", step["run"])

    def test_canary_is_manual_only(self):
        on = self.doc[True]
        canary = on["workflow_dispatch"]["inputs"]["portal_canary"]
        self.assertEqual(canary["type"], "boolean")
        self.assertFalse(canary["default"])
        run = self.named("Project canonical briefing to Notion SSOT")["run"]
        self.assertIn('[ "$EVENT_NAME" = "workflow_dispatch" ]', run)
        self.assertIn("portal canary is manual-only", run)

    def test_receipts_are_committed_by_the_existing_finalization_step(self):
        commit = self.named("Commit finalization artifacts")["run"]
        self.assertIn("git add data/briefing/finalization", commit)
        self.assertIn("[ -d data/briefing/finalization ]", commit)

    def test_no_schedule_or_action_dependency_was_added(self):
        self.assertEqual(self.text.count("- cron:"), 2)
        self.assertEqual(self.text.count("uses:"), 4)


if __name__ == "__main__":
    unittest.main()
