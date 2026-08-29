#!/usr/bin/env python3
"""P1-COM-05 B+C calibration-readiness regression."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "policy_calibration_readiness.py"
SPEC = importlib.util.spec_from_file_location(
    "regime_policy_calibration_readiness_tested", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class RegimePolicyCalibrationReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = MODULE.build_readiness()

    def test_actual_retained_evidence_reproduces_three_market_readiness(self):
        markets = {row["market"]: row for row in self.packet["markets"]}
        korea_retained = (
            ROOT / "data" / "observations" / "korea_market_signals"
        ).is_dir()
        self.assertEqual(
            self.packet["status"],
            "NOT_READY_POLICY_CANDIDATE" if korea_retained else "NOT_READY_AXIS_COVERAGE",
        )
        self.assertEqual(markets["US"]["coverage"]["ratio"], "1/5")
        self.assertEqual(markets["KR"]["coverage"]["ratio"], "5/5" if korea_retained else "0/5")
        self.assertEqual(markets["CRYPTO"]["coverage"]["ratio"], "4/5")
        self.assertEqual(markets["US"]["coverage"]["defined_axes"], ["RISK_VOL"])
        self.assertEqual(
            markets["CRYPTO"]["coverage"]["defined_axes"],
            ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY"],
        )
        self.assertEqual(
            self.packet["summary"]["current_readiness_order_not_market_ranking"],
            ["KR", "CRYPTO", "US"] if korea_retained else ["CRYPTO", "US", "KR"],
        )

    def test_histories_are_independently_replayed_from_retained_raw_bytes(self):
        axes = {
            row["qualified_axis"]: row
            for market in self.packet["markets"] for row in market["axes"]
        }
        for qualified in (
            "US/RISK_VOL", "CRYPTO/TREND", "CRYPTO/RISK_VOL",
            "CRYPTO/LIQUIDITY", "CRYPTO/BREADTH",
        ):
            history = axes[qualified]["history"]
            self.assertEqual(history["status"], "VALIDATED_RETAINED")
            self.assertGreater(history["retained_revision_count"], 0)
            self.assertGreater(history["distinct_observation_count"], 0)
            self.assertGreater(history["history_span_calendar_days"], 0)
            for record in history["records"]:
                self.assertRegex(record["evidence_sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(
                    record["evidence_uri"].startswith("atlas-raw-response://")
                )
        if (ROOT / "data/observations/korea_market_signals").is_dir():
            for axis in ("TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"):
                history = axes[f"KR/{axis}"]["history"]
                self.assertEqual(history["status"], "VALIDATED_RETAINED")
                self.assertTrue(all(
                    row["evidence_uri"].startswith("atlas-observation://")
                    for row in history["records"]
                ))

    def test_missing_axes_preserve_source_specific_blockers(self):
        axes = {
            row["qualified_axis"]: row
            for market in self.packet["markets"] for row in market["axes"]
        }
        self.assertEqual(
            axes["KR/BREADTH"]["blocker"],
            None
            if (ROOT / "data/observations/korea_market_signals").is_dir()
            else "VALIDATED_RETAINED_EVIDENCE_MISSING",
        )
        self.assertEqual(
            axes["US/BREADTH"]["blocker"], "NO_RATIFIED_LIVE_AXIS_BINDING"
        )
        # P1-CR-08: CRYPTO/LEADERSHIP is now a real, bound axis -- its
        # blocker is that no history has been VALIDATED_RETAINED yet
        # (dual-window methodology needs more committed evidence than
        # currently exists), not that no binding exists at all.
        self.assertEqual(
            axes["CRYPTO/LEADERSHIP"]["blocker"],
            "VALIDATED_RETAINED_EVIDENCE_MISSING",
        )
        self.assertIsNone(axes["CRYPTO/BREADTH"]["blocker"])

    def test_methodology_is_ratified_without_generating_policy_values(self):
        self.assertEqual(
            self.packet["methodology"]["status"],
            "RATIFIED_PROCESS_B_MARKET_ROLLOUT_C",
        )
        candidate = self.packet["policy_candidate"]
        self.assertEqual(candidate["candidate_status"], "CANDIDATE_BLOCKED")
        self.assertEqual(candidate["supported_components"], ["MINIMUM_COVERAGE"])
        self.assertEqual(len(candidate["blocked_components"]), 8)
        for key in (
            "generated_policy_value_count", "selected_candidate_count",
            "recommended_candidate_count", "ratified_candidate_count",
        ):
            self.assertEqual(candidate[key], 0)
        self.assertEqual(self.packet["summary"]["shadow_candidate_count"], 0)
        self.assertEqual(self.packet["summary"]["replay_case_count"], 0)
        self.assertFalse(self.packet["summary"]["historical_outcome_evaluated"])
        self.assertEqual(
            MODULE._overall_status(["CRYPTO"], "CANDIDATE_BLOCKED"),
            "NOT_READY_POLICY_CANDIDATE",
        )
        self.assertEqual(
            MODULE._overall_status(["CRYPTO"], "CANDIDATE_READY"),
            "READY_FOR_SEPARATE_SHADOW_CASE_DESIGN",
        )

    def test_korea_combined_packet_is_replayed_for_each_axis_without_classification(self):
        source = MODULE.LIVE_AXIS.KOREA_MARKET_SIGNALS
        contract = source.load_contract()
        packet = {
            "schema_version": source.SCHEMA_VERSION,
            "contract_version": contract["contract_version"],
            "status": "OBSERVED_UNCLASSIFIED",
            "market": "KOREA",
            "market_timezone": "Asia/Seoul",
            "previous_date": "2026-08-27",
            "as_of_date": "2026-08-28",
            "generated_at": "2026-08-28T09:20:00Z",
            "available_at": "2026-08-28T09:20:00Z",
            "source": {"raw_persistence": 0, "per_symbol_persistence": 0},
            "axes": {
                axis: {"status": "OBSERVED", "measurement": {"fixture": axis}}
                for axis in contract["required_axes"]
            },
            "coverage": {
                "required_axes": list(contract["required_axes"]),
                "observed_axes": list(contract["required_axes"]),
                "observed_count": 5,
                "required_count": 5,
                "ratio": "5/5",
            },
            "authority": contract["authority"],
        }
        packet["payload_sha256"] = source.payload_sha256(packet)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "data/observations/korea_market_signals/2026-08-28/packet.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(packet, ensure_ascii=False), encoding="utf-8")
            for axis in contract["required_axes"]:
                records = MODULE._scan_korea_market_signals(Path(raw), axis)
                self.assertEqual(len(records), 1)
                self.assertEqual(records[0]["observation_date"], "2026-08-28")
                self.assertTrue(records[0]["evidence_uri"].startswith("atlas-observation://"))
                self.assertEqual(records[0]["evidence_sha256"], packet["payload_sha256"])

    def test_authority_is_inventory_only_and_all_downstream_false(self):
        authority = self.packet["authority"]
        self.assertTrue(authority["readiness_inventory_only"])
        for key, value in authority.items():
            if key != "readiness_inventory_only":
                self.assertFalse(value, key)

    def test_output_is_deterministic_and_resigned_tamper_is_rejected(self):
        second = MODULE.build_readiness()
        self.assertEqual(self.packet, second)
        unsigned = copy.deepcopy(self.packet)
        claimed = unsigned.pop("payload_sha256")
        self.assertEqual(claimed, MODULE.payload_sha256(unsigned))
        self.assertEqual(MODULE.validate_readiness(self.packet), self.packet)

        tampered = copy.deepcopy(self.packet)
        tampered["markets"][0]["coverage"]["defined_count"] = 5
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(
            MODULE.PolicyCalibrationReadinessError,
            "READINESS_REDERIVATION_MISMATCH",
        ):
            MODULE.validate_readiness(tampered)

    def test_contract_drift_and_raw_tamper_fail_closed(self):
        contract = json.loads(
            (
                ROOT / "config/regime_policy_calibration_readiness_contract.json"
            ).read_text()
        )
        contract["minimum_defined_axes"] = 3
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.PolicyCalibrationReadinessError,
                "READINESS_CONTRACT_MISMATCH",
            ):
                MODULE.load_contract(path)

        source = ROOT / "evidence/crypto/btc/raw/2026-08-26"
        with tempfile.TemporaryDirectory() as raw:
            copied = Path(raw) / "evidence/crypto/btc/raw/2026-08-26"
            copied.parent.mkdir(parents=True)
            shutil.copytree(source, copied)
            (copied / "kraken_ohlc_xbtusd.json.gz").write_bytes(b"tampered")
            with self.assertRaisesRegex(
                MODULE.PolicyCalibrationReadinessError,
                "SOURCE_EVIDENCE_INVALID:CRYPTO/TREND",
            ):
                MODULE._scan_btc(Path(raw), "TREND")

    def test_cli_writes_only_outside_repository(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "readiness.json"
            completed = subprocess.run(
                [sys.executable, str(SCRIPT), "--out", str(target)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            value = json.loads(target.read_text(encoding="utf-8"))
            self.assertEqual(MODULE.validate_readiness(value), value)

        forbidden = ROOT / "calibration-readiness-should-not-exist.json"
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

    def test_module_adds_no_provider_workflow_or_policy_defaults(self):
        source = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted((ROOT / ".github/workflows").glob("*.yml"))
        )
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("policy_calibration_readiness.py", workflows)
        self.assertNotIn("shadow_candidate_generation_authorized\": True", source)
        self.assertNotIn("regime_classification_authorized\": True", source)


if __name__ == "__main__":
    unittest.main()
