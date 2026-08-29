"""Public-only natural transport validation boundary for P9-06."""
from __future__ import annotations

import asyncio
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = ROOT / ".github" / "scripts" / "upbit_realtime_capture.py"
ANCHOR_PATH = ROOT / "config" / "upbit_public_validation_anchor_contract.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "upbit-realtime-capture.yml"
UTC = dt.timezone.utc


def load_module():
    spec = importlib.util.spec_from_file_location("upbit_realtime_capture_validation_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


C = load_module()


class ValidationAnchorContractTests(unittest.TestCase):
    def test_reference_markets_are_public_transport_only(self):
        contract = C.load_validation_anchor_contract(ANCHOR_PATH)
        self.assertEqual(contract["capture_mode"], C.PUBLIC_VALIDATION_MODE)
        self.assertEqual(contract["markets"], ["KRW-BTC", "KRW-ETH"])
        for field in (
            "feeds_tradeable_universe",
            "feeds_candidate_promotion",
            "feeds_buy_decision",
            "feeds_briefing_decision",
            "entry_eligibility_authorized",
            "action_generation_authorized",
            "order_authorized",
            "production_authorized",
            "trading_authorized",
            "auth_required",
            "private_channel_subscribed",
            "order_or_withdrawal_endpoints_called",
        ):
            self.assertIs(contract[field], False, field)

    def test_any_authority_expansion_fails_closed(self):
        original = json.loads(ANCHOR_PATH.read_text(encoding="utf-8"))
        for field in ("feeds_buy_decision", "order_authorized", "private_channel_subscribed"):
            with self.subTest(field=field), tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False
            ) as handle:
                tampered = dict(original)
                tampered[field] = True
                json.dump(tampered, handle)
                path = Path(handle.name)
            try:
                with self.assertRaises(C.RealtimeCaptureError):
                    C.load_validation_anchor_contract(path)
            finally:
                path.unlink()

    def test_market_list_must_be_unique_sorted_krw(self):
        original = json.loads(ANCHOR_PATH.read_text(encoding="utf-8"))
        for markets in (["KRW-ETH", "KRW-BTC"], ["KRW-BTC", "KRW-BTC"], ["BTC/USD"]):
            with self.subTest(markets=markets), tempfile.NamedTemporaryFile(
                "w", suffix=".json", delete=False
            ) as handle:
                tampered = dict(original)
                tampered["markets"] = markets
                json.dump(tampered, handle)
                path = Path(handle.name)
            try:
                with self.assertRaises(C.RealtimeCaptureError):
                    C.load_validation_anchor_contract(path)
            finally:
                path.unlink()

    def test_validation_and_decision_evidence_roots_cannot_mix(self):
        C.validate_evidence_root(C.PUBLIC_VALIDATION_MODE, Path("/tmp/realtime_validation"))
        C.validate_evidence_root(C.ELIGIBLE_UNIVERSE_MODE, Path("/tmp/realtime"))
        with self.assertRaises(C.RealtimeCaptureError):
            C.validate_evidence_root(C.PUBLIC_VALIDATION_MODE, Path("/tmp/realtime"))
        with self.assertRaises(C.RealtimeCaptureError):
            C.validate_evidence_root(C.ELIGIBLE_UNIVERSE_MODE, Path("/tmp/realtime_validation"))


class ValidationEvidenceTests(unittest.TestCase):
    def test_channel_coverage_is_exact_and_non_authoritative(self):
        markets = ["KRW-BTC", "KRW-ETH"]
        latest = {}
        for market in markets:
            for key in (
                f"ticker|-|{market}",
                f"trade|-|{market}",
                f"orderbook|-|{market}",
                f"candle|15m|{market}",
                f"candle|1h|{market}",
                f"candle|4h|{market}",
            ):
                latest[key] = {"raw": {}}
        summary = C.public_transport_validation_summary(markets, latest)
        self.assertEqual(summary["status"], "COMPLETE")
        self.assertEqual(summary["missing_public_channel_keys"], [])
        for field, value in summary.items():
            if field.endswith("authorized") or field == "decision_eligible":
                self.assertIs(value, False, field)

    def test_missing_channel_is_explicit_not_silently_complete(self):
        summary = C.public_transport_validation_summary(["KRW-BTC"], {})
        self.assertEqual(summary["status"], "INCOMPLETE")
        self.assertEqual(len(summary["missing_public_channel_keys"]), 6)

    def test_validation_mode_is_recorded_even_without_network(self):
        contract = C.GATE.load_contract()
        streamed = {
            "message_log": [],
            "latest_public_messages_schema_version": C.LATEST_PUBLIC_MESSAGES_SCHEMA_VERSION,
            "latest_public_messages": {},
        }
        with mock.patch.object(C, "_connect_and_stream", new=mock.AsyncMock(return_value=streamed)):
            run = asyncio.run(
                C.run_capture_async(
                    ["KRW-BTC"],
                    contract,
                    duration_seconds=0.001,
                    capture_mode=C.PUBLIC_VALIDATION_MODE,
                )
            )
        self.assertEqual(run["capture_mode"], C.PUBLIC_VALIDATION_MODE)
        self.assertEqual(run["transport_validation"]["status"], "INCOMPLETE")
        self.assertFalse(run["transport_validation"]["decision_eligible"])

    def test_validation_mode_rejects_empty_anchor_set(self):
        with self.assertRaises(C.RealtimeCaptureError):
            asyncio.run(
                C.run_capture_async(
                    [],
                    C.GATE.load_contract(),
                    duration_seconds=0.001,
                    capture_mode=C.PUBLIC_VALIDATION_MODE,
                )
            )

    def test_append_only_snapshot_hash_binds_run(self):
        run = {
            "capture_mode": C.PUBLIC_VALIDATION_MODE,
            "markets": ["KRW-BTC", "KRW-ETH"],
            "transport_validation": {"status": "INCOMPLETE"},
        }
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            first = C.write_evidence_snapshot(root, dt.date(2026, 8, 29), run)
            second = C.write_evidence_snapshot(root, dt.date(2026, 8, 29), run)
            self.assertEqual(first.name, "run_001.json")
            self.assertEqual(second.name, "run_002.json")
            packet = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(packet["source_sha256"], C.payload_sha256(run))
            self.assertFalse(packet["auth_required"])
            self.assertFalse(packet["private_channel_subscribed"])
            self.assertFalse(packet["order_or_withdrawal_endpoints_called"])


class DecisionIsolationTests(unittest.TestCase):
    def test_validation_root_is_not_a_decision_source(self):
        decision_source = (ROOT / "decision" / "crypto_paper_decision_snapshot.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("realtime_validation", decision_source)
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        decision_step = workflow.split(
            "- name: Crypto PAPER decision snapshot", 1
        )[1].split("- name: P8-16 Crypto funnel", 1)[0]
        self.assertNotIn("realtime_validation", decision_step)
        self.assertIn("evidence/crypto/upbit/realtime_validation", workflow)

    def test_realtime_gate_never_imports_anchor_contract(self):
        gate_source = (ROOT / "realtime" / "upbit_realtime_gate.py").read_text(encoding="utf-8")
        self.assertNotIn("upbit_public_validation_anchor_contract", gate_source)


if __name__ == "__main__":
    unittest.main()
