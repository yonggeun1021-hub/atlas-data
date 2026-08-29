#!/usr/bin/env python3
"""Current Crypto reference regression over immutable committed evidence."""

import importlib.util
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_recent_reference.py"
SNAPSHOT = ROOT / "evidence" / "crypto" / "breadth" / "raw" / "2026-08-29"
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-breadth-capture.yml"
SOURCE_COMMIT = "f97b27389c51c8fd5e5842c5b369f940a41c36f0"

SPEC = importlib.util.spec_from_file_location("crypto_recent_reference", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CryptoRecentReferenceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = MODULE.build_reference(
            SNAPSHOT,
            "2026-08-29T12:00:00Z",
            SOURCE_COMMIT,
        )

    def test_current_reference_is_complete_and_read_only(self):
        value = self.payload
        self.assertEqual(
            value["mode"], "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY"
        )
        self.assertEqual(value["price_as_of_date"], "2026-08-28")
        self.assertEqual(value["selection"]["selected_asset_count"], 100)
        self.assertEqual(
            value["selection"]["taxonomy_unknown_before_cutoff_count"], 0
        )
        self.assertTrue(value["authority"]["reference_only"])
        for key, allowed in value["authority"].items():
            if key != "reference_only":
                self.assertFalse(allowed, key)

    def test_windows_are_exact_and_include_btc_eth(self):
        seven = self.payload["windows"]["7d"]
        thirty = self.payload["windows"]["30d"]
        self.assertEqual((seven["start_date"], seven["end_date"]), (
            "2026-08-21", "2026-08-28"
        ))
        self.assertEqual((thirty["start_date"], thirty["end_date"]), (
            "2026-07-29", "2026-08-28"
        ))
        self.assertEqual(thirty["observed_asset_count"], 100)
        self.assertEqual(thirty["missing_asset_count"], 0)
        members = {
            item["canonical_asset_id"] for item in self.payload["member_returns"]
        }
        self.assertIn("BTC", members)
        self.assertIn("ETH", members)
        self.assertEqual(len(self.payload["top_30d_strength"]), 5)

    def test_generated_before_capture_fails_closed(self):
        with self.assertRaisesRegex(Exception, "GENERATED_BEFORE_CAPTURE"):
            MODULE.build_reference(
                SNAPSHOT,
                "2026-08-28T23:59:59Z",
                SOURCE_COMMIT,
            )

    def test_output_is_byte_stable(self):
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "first.json"
            second = Path(tmp) / "second.json"
            first_sha = MODULE.write_output(self.payload, first)
            second_sha = MODULE.write_output(self.payload, second)
            self.assertEqual(first_sha, second_sha)
            self.assertEqual(first.read_bytes(), second.read_bytes())

    def test_daily_capture_builds_and_commits_reference(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("Build current Crypto 7d/30d user reference", text)
        self.assertIn("crypto_recent_reference.py", text)
        self.assertIn("data/observations/crypto_recent_reference", text)


if __name__ == "__main__":
    unittest.main()
