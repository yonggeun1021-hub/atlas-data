#!/usr/bin/env python3
"""P10-12 crypto strategy re-review trigger readiness regression."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "audit/crypto_strategy_rereview_trigger_readiness.py"
SPEC = importlib.util.spec_from_file_location("crypto_rereview_readiness", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CryptoStrategyRereviewTriggerReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = MODULE.build_readiness()

    def test_current_canonical_trigger_state_is_closed(self):
        self.assertEqual(self.packet["summary"], {
            "trigger_count": 4,
            "proven_trigger_count": 0,
            "rereview_gate": "CLOSED_NO_CANONICAL_TRIGGER_PROVEN",
            "candidate": "NONE",
            "live_engine_count": 0,
        })
        states = {
            item["trigger_id"]: (item["result"], item["evidence_status"])
            for item in self.packet["triggers"]
        }
        self.assertEqual(states, {
            "CAPITAL_AT_LEAST_10000_USD": ("NOT_PROVEN", "NOT_COMPUTABLE"),
            "RATIFIED_MARKET_REGIME_CHANGE": ("NOT_PROVEN", "NOT_COMPUTABLE"),
            "GENUINELY_NEW_MEASUREMENT_SOURCE": ("NOT_PROVEN", "FAIL"),
            "MATERIAL_EXCHANGE_POLICY_OR_COST_CHANGE": ("NOT_PROVEN", "FAIL"),
        })

    def test_regime_observation_does_not_invent_change(self):
        regime = next(
            item for item in self.packet["triggers"]
            if item["trigger_id"] == "RATIFIED_MARKET_REGIME_CHANGE"
        )
        self.assertEqual(regime["observed"]["official_runtime_regime"], "UNKNOWN")
        self.assertIsNone(regime["observed"]["ratified_baseline_regime"])
        self.assertEqual(regime["result"], "NOT_PROVEN")

    def test_qualification_and_event_study_are_not_entered(self):
        qualification = self.packet["mechanism_qualification"]
        self.assertEqual(qualification["status"], "NOT_EVALUATED_TRIGGER_NOT_PROVEN")
        self.assertEqual(qualification["answered_count"], 0)
        self.assertEqual(len(qualification["answers"]), 4)
        self.assertTrue(all(value is None for value in qualification["answers"].values()))
        self.assertEqual(self.packet["event_study"], {
            "status": "NOT_EVALUATED_TRIGGER_NOT_PROVEN",
            "passed_gate_count": 0,
            "required_pass_count": 7,
            "candidate_count": 0,
        })

    def test_every_downstream_authority_remains_false(self):
        self.assertTrue(self.packet["authority"]["readiness_observation_only"])
        for key, value in self.packet["authority"].items():
            if key != "readiness_observation_only":
                self.assertFalse(value, key)

    def test_packet_is_deterministic_and_rehashed_tamper_fails(self):
        self.assertEqual(self.packet, MODULE.build_readiness())
        unsigned = copy.deepcopy(self.packet)
        claimed = unsigned.pop("packet_sha256")
        self.assertEqual(claimed, MODULE.payload_sha256(unsigned))
        self.assertEqual(MODULE.validate_readiness(self.packet), self.packet)

        tampered = copy.deepcopy(self.packet)
        tampered["summary"]["proven_trigger_count"] = 1
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("packet_sha256")
        tampered["packet_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.CryptoRereviewReadinessError,
            "READINESS_REDERIVATION_MISMATCH",
        ):
            MODULE.validate_readiness(tampered)

    def test_regime_authority_escalation_and_measurement_invention_fail_closed(self):
        regime = json.loads((ROOT / "data/latest_crypto_regime_refresh_status.json").read_text())
        regime["authority"]["trading_authorized"] = True
        unsigned = copy.deepcopy(regime)
        unsigned.pop("payload_sha256")
        regime["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.CryptoRereviewReadinessError,
            "REGIME_AUTHORITY_ESCALATION",
        ):
            MODULE.build_readiness(regime_status=regime)

        inventory = json.loads((ROOT / "config/data_coverage_registry.json").read_text())
        inventory["strategy_measurement_inventory"] = []
        with self.assertRaisesRegex(
            MODULE.CryptoRereviewReadinessError,
            "UNRATIFIED_MEASUREMENT_BASELINE_FIELD_PRESENT",
        ):
            MODULE.build_readiness(source_inventory=inventory)

    def test_cli_writes_only_outside_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "readiness.json"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--out", str(target)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            packet = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(packet["summary"]["proven_trigger_count"], 0)

        forbidden = ROOT / "crypto-rereview-readiness-should-not-exist.json"
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--out", str(forbidden)],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertIn("TRACKED_OUTPUT_FORBIDDEN", completed.stderr)
        self.assertFalse(forbidden.exists())

    def test_module_has_no_network_provider_or_strategy_runtime(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("api.bitget", source)
        self.assertNotIn("backtest", source.lower().replace("backtest_authorized", ""))


if __name__ == "__main__":
    unittest.main()
