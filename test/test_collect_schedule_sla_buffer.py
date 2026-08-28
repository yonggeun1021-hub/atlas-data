#!/usr/bin/env python3
"""Regression for Atlas Daily Collect pre-gate schedule buffers."""

import importlib.util
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"
SCRIPT = ROOT / ".github" / "scripts" / "record_collect_run.py"
SPEC = importlib.util.spec_from_file_location("record_collect_run", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

CURRENT_CRONS = (
    ("55 20 * * 0-4", "primary_0555_kst"),
    ("15 21 * * 0-4", "backup_0615_kst"),
    ("35 21 * * 0-4", "final_0635_kst"),
)
LEGACY_CRONS = (
    "5 21 * * 0-4",
    "25 21 * * 0-4",
    "45 21 * * 0-4",
)


class CollectScheduleSlaBufferTest(unittest.TestCase):
    def test_workflow_uses_only_current_pre_gate_slots(self):
        raw = WORKFLOW.read_text(encoding="utf-8")
        scheduled = tuple(
            re.findall(r"^\s*-\s*cron:\s*'([^']+)'", raw, re.MULTILINE)
        )
        self.assertEqual(scheduled, tuple(cron for cron, _ in CURRENT_CRONS))

    def test_current_slots_have_telemetry_identities(self):
        for cron, slot_id in CURRENT_CRONS:
            with self.subTest(cron=cron):
                self.assertIn(cron, MODULE.SCHEDULE_SLOTS)
                self.assertEqual(MODULE.SCHEDULE_SLOTS[cron][0], slot_id)

    def test_historical_slots_remain_rebuildable(self):
        for cron in LEGACY_CRONS:
            with self.subTest(cron=cron):
                self.assertIn(cron, MODULE.SCHEDULE_SLOTS)

    def test_nominal_buffers_before_0655_are_60_40_20_minutes(self):
        kst_minutes = []
        for cron, _ in CURRENT_CRONS:
            minute, hour, *_ = cron.split()
            total = (int(hour) * 60 + int(minute) + 9 * 60) % (24 * 60)
            kst_minutes.append(total)

        checkpoint = 6 * 60 + 55
        self.assertEqual([checkpoint - minute for minute in kst_minutes], [60, 40, 20])


if __name__ == "__main__":
    unittest.main()
