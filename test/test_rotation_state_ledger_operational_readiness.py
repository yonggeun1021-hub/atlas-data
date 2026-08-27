#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "rotation" / "rotation_state_ledger_operational_readiness.py"
SPEC = importlib.util.spec_from_file_location(
    "rotation_state_ledger_operational_readiness", MODULE_PATH
)
READINESS = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(READINESS)
PROOF_PATH = ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py"


class RotationStateLedgerOperationalReadinessTests(unittest.TestCase):
    def test_repository_truth_is_zero_of_three_ready(self):
        packet = READINESS.build_readiness()
        self.assertEqual(
            packet["overall_status"],
            "BLOCKED_NO_MARKET_HAS_OPERATIONAL_STATE_HISTORY",
        )
        self.assertEqual(packet["ready_market_count"], 0)
        self.assertEqual(packet["required_market_count"], 3)
        by_market = {row["market"]: row for row in packet["markets"]}
        self.assertEqual(
            by_market["KOREA"]["upstream_rotation_evidence_status"],
            "POINTER_ONLY_FULL_ROTATION_PACKET_NOT_COMMITTED",
        )
        self.assertRegex(by_market["KOREA"]["upstream_pointer_commit"], r"^[0-9a-f]{40}$")
        self.assertIsNotNone(by_market["KOREA"]["upstream_rotation_packet_sha256"])
        for market in ("US", "CRYPTO"):
            self.assertEqual(
                by_market[market]["upstream_rotation_evidence_status"],
                "ROTATION_EVIDENCE_NOT_COMMITTED",
            )
            self.assertIsNone(by_market[market]["upstream_pointer_commit"])
        for row in packet["markets"]:
            self.assertEqual(row["state_policy_status"], "ABSENT_BY_REPOSITORY_CONTRACT")
            self.assertEqual(row["ledger_evidence_status"], "ABSENT")
            self.assertEqual(row["ledger_record_count"], 0)
            self.assertEqual(row["readiness_status"], "NOT_READY")

    def test_every_authority_stays_closed_except_inventory_marker(self):
        authority = READINESS.build_readiness()["authority"]
        self.assertTrue(authority["readiness_inventory_only"])
        for name, value in authority.items():
            if name != "readiness_inventory_only":
                self.assertFalse(value, name)

    def test_rerun_is_deterministic_and_validator_rederives(self):
        first = READINESS.build_readiness()
        second = READINESS.build_readiness()
        self.assertEqual(first, second)
        self.assertEqual(READINESS.validate_readiness(copy.deepcopy(first)), first)

    def test_self_rehashed_output_tamper_is_rejected(self):
        packet = READINESS.build_readiness()
        packet["markets"][1]["ledger_record_count"] = 46
        packet["payload_sha256"] = READINESS.payload_sha256(
            {key: value for key, value in packet.items() if key != "payload_sha256"}
        )
        with self.assertRaisesRegex(
            READINESS.RotationStateLedgerReadinessError,
            "READINESS_REDERIVATION_MISMATCH",
        ):
            READINESS.validate_readiness(packet)

    def test_contract_tamper_fails_before_readiness_is_built(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config").mkdir()
            for name in (
                "rotation_state_ledger_operational_readiness_contract.json",
                "rotation_state_ledger_contract.json",
            ):
                (root / "config" / name).write_bytes((ROOT / "config" / name).read_bytes())
            path = root / "config" / "rotation_state_ledger_operational_readiness_contract.json"
            value = json.loads(path.read_text(encoding="utf-8"))
            value["repository_default_state_policy"] = "RATIFIED"
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaisesRegex(
                READINESS.RotationStateLedgerReadinessError,
                "READINESS_CONTRACT_MISMATCH",
            ):
                READINESS.build_readiness(root)

    def test_production_korea_proof_cannot_manufacture_p2_05_policy_or_ledger(self):
        source = PROOF_PATH.read_text(encoding="utf-8")
        for forbidden in (
            "POLICY.P2.05.PROOF",
            "--ledger-out",
            "LEDGER.apply_rotation",
            "build_state_policy",
        ):
            self.assertNotIn(forbidden, source)

    def test_cli_writes_only_to_explicit_untracked_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "readiness.json"
            subprocess.run(
                ["python3", str(MODULE_PATH), "--out", str(out)],
                cwd=ROOT,
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            self.assertEqual(
                READINESS.validate_readiness(json.loads(out.read_text(encoding="utf-8"))),
                READINESS.build_readiness(),
            )
        with self.assertRaisesRegex(
            READINESS.RotationStateLedgerReadinessError,
            "TRACKED_OUTPUT_FORBIDDEN",
        ):
            READINESS.write_json_atomic(ROOT / "forbidden-readiness.json", {})

    def test_module_has_no_provider_or_policy_injection_surface(self):
        source = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertEqual(list(inspect.signature(READINESS.build_readiness).parameters), ["root"])
        for token in ("ratified_by", "state_by_bucket_transition", "maximum_ledger_gap_days"):
            self.assertNotIn(token, source)


if __name__ == "__main__":
    unittest.main()
