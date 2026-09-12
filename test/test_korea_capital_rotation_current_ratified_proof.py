#!/usr/bin/env python3
"""Focused integration for the opt-in current P2-03 ratified proof path.

These tests use only already-committed historical source evidence.  They do
not claim or fabricate the first post-2026-09-14 natural pair.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py"
SPEC = importlib.util.spec_from_file_location(
    "korea_capital_rotation_current_ratified_proof", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

PRIOR = "2026-08-13"
CURRENT = "2026-08-14"


class CurrentRatifiedArtifactConsumptionTests(unittest.TestCase):
    def test_exact_committed_ratified_binding_and_policy_are_consumed(self):
        value, policy = MODULE.build_current_ratified_price_side(PRIOR, CURRENT)
        binding = value["taxonomy_binding"]
        self.assertEqual(
            binding["taxonomy_contract_version"],
            "korea_sector_identity_binding/1",
        )
        self.assertEqual(
            binding["taxonomy_decision_sha256"],
            "09e2db653c04592298c0068745c2393ed4ee754282a5289ca08d77869ba8e8e3",
        )
        self.assertEqual(
            binding["taxonomy_packet_sha256"],
            "6027e89b70766599bac4a242aef5bf608f7979628a6e8b5cbaf23219cab287e3",
        )
        self.assertEqual(
            policy["policy_id"],
            "POLICY.P2_03.KOREA_OWN_BENCHMARK_EXTREMES.RATIFIED.V1",
        )
        self.assertEqual(policy["effective_from"], "2026-09-14")
        self.assertTrue(
            all(
                scope["top_count"] == 3 and scope["bottom_count"] == 3
                for scope in policy["benchmark_scopes"]
            )
        )

    def test_committed_artifact_drift_fails_closed(self):
        original = MODULE.RATIFIED.load_committed_policy()
        tampered = copy.deepcopy(original)
        tampered["maximum_calendar_gap_days"] = 99
        with mock.patch.object(
            MODULE.RATIFIED, "load_committed_policy", return_value=tampered
        ):
            with self.assertRaisesRegex(
                RuntimeError, "RATIFIED_ARTIFACT_REDERIVATION_MISMATCH"
            ):
                MODULE.load_current_ratified_artifacts()

    def test_pre_effective_real_pair_remains_inert(self):
        packet = MODULE.build_current_ratified_packet(PRIOR, CURRENT)
        self.assertEqual(packet["schema_version"], "korea_capital_rotation_packet/4")
        self.assertEqual(packet["contract_version"], "korea_capital_rotation/4")
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertEqual(packet["observation_pair"]["prior_date"], PRIOR)
        self.assertEqual(packet["observation_pair"]["current_date"], CURRENT)
        checked = MODULE.KCR.validate_packet(copy.deepcopy(packet))
        self.assertEqual(checked, packet)


class ExternalPacketBoundaryTests(unittest.TestCase):
    def test_external_write_preserves_pointer_and_revalidates(self):
        pointer_path = ROOT / "data" / "latest_korea_rotation.json"
        pointer_before = pointer_path.read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "korea-rotation.json"
            result = MODULE.run_current_ratified(PRIOR, CURRENT, out)
            self.assertEqual(result["packet_out"], out.resolve())
            persisted = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(
                MODULE.KCR.validate_packet(copy.deepcopy(persisted)), persisted
            )
        self.assertEqual(pointer_path.read_bytes(), pointer_before)

    def test_relative_and_repository_outputs_are_forbidden(self):
        packet = MODULE.build_current_ratified_packet(PRIOR, CURRENT)
        with self.assertRaisesRegex(
            RuntimeError, "RATIFIED_PACKET_OUTPUT_MUST_BE_ABSOLUTE"
        ):
            MODULE.write_external_ratified_packet(
                Path("korea-rotation.json"), packet
            )
        with self.assertRaisesRegex(
            RuntimeError, "RATIFIED_PACKET_TRACKED_OUTPUT_FORBIDDEN"
        ):
            MODULE.write_external_ratified_packet(
                ROOT / "data" / "forbidden-korea-rotation.json", packet
            )

    def test_no_p2_05_ledger_or_state_policy_path_is_added(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("rotation_state_ledger", source)
        self.assertNotIn("rotation_state_policy_ratification", source)
        self.assertNotIn("--ledger", source)


class LegacyProofPreservationTests(unittest.TestCase):
    def test_legacy_default_function_signature_and_policy_are_unchanged(self):
        value, policy = MODULE.build_real_price_side(PRIOR, CURRENT)
        self.assertEqual(
            policy["policy_id"], "POLICY.P2.03.KOREA_OWN_BENCHMARK_EXTREMES_V1"
        )
        self.assertEqual(policy["effective_from"], "2026-08-01")
        self.assertEqual(value["taxonomy_binding"]["taxonomy_packet_sha256"], "0" * 64)
        self.assertTrue(
            all(
                scope["top_count"] == 1 and scope["bottom_count"] == 1
                for scope in policy["benchmark_scopes"]
            )
        )


if __name__ == "__main__":
    unittest.main()
