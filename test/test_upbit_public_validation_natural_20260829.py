"""Immutable natural Upbit public transport sample captured on 2026-08-29."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / ".github" / "scripts" / "upbit_realtime_capture.py"
SAMPLE_PATH = (
    ROOT
    / "evidence"
    / "crypto"
    / "upbit"
    / "realtime_validation"
    / "2026-08-29"
    / "run_001.json"
)


def load_module():
    spec = importlib.util.spec_from_file_location("upbit_natural_validation_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


C = load_module()


class NaturalPublicValidationSampleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = json.loads(SAMPLE_PATH.read_text(encoding="utf-8"))
        cls.run_record = cls.packet["run"]

    def test_sample_is_regular_hash_bound_public_only_evidence(self):
        self.assertTrue(SAMPLE_PATH.is_file())
        self.assertFalse(SAMPLE_PATH.is_symlink())
        self.assertEqual(self.packet["source_sha256"], C.payload_sha256(self.run_record))
        self.assertFalse(self.packet["auth_required"])
        self.assertFalse(self.packet["private_channel_subscribed"])
        self.assertFalse(self.packet["order_or_withdrawal_endpoints_called"])

    def test_natural_capture_observed_every_declared_public_channel(self):
        self.assertEqual(self.run_record["capture_mode"], C.PUBLIC_VALIDATION_MODE)
        self.assertEqual(self.run_record["markets"], ["KRW-BTC", "KRW-ETH"])
        self.assertGreater(self.run_record["status"]["counts"]["accepted"], 0)
        self.assertEqual(self.run_record["status"]["reconnect_count"], 0)
        summary = self.run_record["transport_validation"]
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertEqual(summary["missing_public_channel_keys"], [])
        self.assertEqual(
            summary["observed_public_channel_keys"],
            summary["expected_public_channel_keys"],
        )

    def test_retained_bytes_parse_and_hash_as_public_messages(self):
        observed_types = set()
        for key, retained in self.run_record["latest_public_messages"].items():
            parsed = C.GATE.parse_message(retained["raw"])
            timeframe = parsed["timeframe"] or "-"
            self.assertEqual(key, f"{parsed['kind']}|{timeframe}|{parsed['market']}")
            self.assertEqual(retained["source_sha256"], parsed["payload_sha256"])
            self.assertNotIn(retained["raw"]["type"], C.GATE.PRIVATE_WS_TYPES_FORBIDDEN)
            observed_types.add(retained["raw"]["type"])
        self.assertEqual(
            observed_types,
            {"ticker", "trade", "orderbook", "candle.15m", "candle.60m", "candle.240m"},
        )

    def test_every_authority_remains_false(self):
        for field, value in self.run_record["status"]["authority"].items():
            self.assertIs(value, False, field)
        for field, value in self.run_record["transport_validation"].items():
            if field.endswith("authorized") or field == "decision_eligible":
                self.assertIs(value, False, field)


if __name__ == "__main__":
    unittest.main()
