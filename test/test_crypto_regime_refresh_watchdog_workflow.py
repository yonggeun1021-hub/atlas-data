#!/usr/bin/env python3
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-regime-refresh-watchdog.yml"


class CryptoRegimeRefreshWatchdogWorkflowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = WORKFLOW.read_text(encoding="utf-8")

    def test_delay_is_recorded_without_turning_expected_wait_into_failed_run(self):
        self.assertIn("continue-on-error: true", self.text)
        self.assertIn("Open or update one delay issue", self.text)
        self.assertIn("Keep the delay as an explicit safe WAIT", self.text)
        self.assertNotIn("Fail the watchdog run after recording the incident", self.text)
        self.assertNotIn("run: exit 1", self.text)

    def test_wait_message_keeps_order_and_trading_authority_closed(self):
        self.assertIn("WAIT", self.text)
        self.assertIn("주문·매매 권한은 열리지 않았습니다", self.text)


if __name__ == "__main__":
    unittest.main()
