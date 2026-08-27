#!/usr/bin/env python3
"""P8-04 Korea Breadth aggregate-retention regression."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/korea_breadth_aggregate_populate.py"
SPEC = importlib.util.spec_from_file_location("korea_breadth_aggregate_populate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

REAL_DIR = (
    ROOT / "data/observations/korea_breadth_aggregate/2026-08-26"
    / "run-33049365069-attempt-1"
)
P1_WORKFLOW = ROOT / ".github/workflows/p1-kr05-korea-breadth-live.yml"
P2_WORKFLOW = ROOT / ".github/workflows/p2-03-korea-observation-pair.yml"


def retained_derived_copy(root: Path) -> Path:
    derived = root / "derived"
    derived.mkdir(parents=True)
    for name in MODULE.PACKET_NAMES:
        (derived / name).write_bytes((REAL_DIR / name).read_bytes())
    return derived


def metadata(run_id: str = "123") -> dict:
    return {
        "workflow_run_id": run_id,
        "run_attempt": 1,
        "source_head_sha": "a" * 40,
        "artifact_id": "456",
        "artifact_name": f"p1-kr05-derived-outputs-{run_id}-1",
        "artifact_digest": f"sha256:{'b' * 64}",
    }


class KoreaBreadthAggregateRetentionTest(unittest.TestCase):
    def test_real_actions_artifact_is_retained_with_exact_lineage(self):
        manifest = MODULE.validate_retained_dir(REAL_DIR)
        self.assertEqual(manifest["as_of_date"], "2026-08-26")
        self.assertEqual(manifest["source"]["workflow_run_id"], "33049365069")
        self.assertEqual(manifest["source"]["source_head_sha"], "32c40a781dd7a9294bef23cb77e3f766d5d8bb50")
        self.assertEqual(manifest["source"]["artifact_id"], "9636936926")
        self.assertEqual(
            manifest["source"]["artifact_digest"],
            "sha256:b45b0e842e9bb44e0b9c4c4d9f3a5e6ad15dbb858adc13130f81be8ac8eded82",
        )
        self.assertEqual(manifest["source"]["artifact_expires_at"], "2026-11-25T07:21:14Z")
        self.assertEqual(
            {name: row["sha256"] for name, row in manifest["source_files"].items()},
            {
                "korea-breadth-historical-kospi.json": "1a1336d25303a35a37993aabdd769e994b00bd48192d422fa26371fe35dc1b38",
                "korea-breadth-historical-kosdaq.json": "2d0585cb6dd673f9f520f58df801640fb59f79ca089a85d7e66b4c33fc78f40d",
                "korea-breadth-recent-kospi.json": "d1002392c91fde2853691b06e06fd58b7ac305000f2a6ded0dd13a57eeb2f6db",
                "korea-breadth-recent-kosdaq.json": "1de3c858ebaaf4bf5441201a635038014e2e37f09211727b9c7bb268785424ef",
            },
        )

    def test_retention_boundary_never_promotes_aggregate_to_axis_or_action(self):
        manifest = MODULE.validate_retained_dir(REAL_DIR)
        self.assertTrue(manifest["retention_boundary"]["exact_aggregate_packet_bytes_retained"])
        self.assertFalse(manifest["retention_boundary"]["raw_response_bodies_retained"])
        self.assertFalse(manifest["retention_boundary"]["per_symbol_identity_and_price_retained"])
        self.assertFalse(manifest["retention_boundary"]["independent_source_replay_available"])
        self.assertFalse(manifest["retention_boundary"]["axis_evidence_eligible"])
        self.assertTrue(all(value is False for value in manifest["authority"].values()))
        for name in MODULE.PACKET_NAMES:
            packet = json.loads((REAL_DIR / name).read_text(encoding="utf-8"))
            self.assertEqual(packet["participation"]["classification"], "UNDEFINED")
            for key in (
                "breadth_classification_authorized", "threshold_authorized",
                "regime_score_authorized", "production_wiring_authorized",
                "trading_action_authorized",
            ):
                self.assertFalse(packet[key], f"{name}:{key}")

    def test_populate_is_atomic_append_only_and_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            derived = retained_derived_copy(Path(tmp))
            first = MODULE.populate(derived, root=root, **metadata())
            target = Path(first["path"])
            before = {path.name: path.read_bytes() for path in target.iterdir()}
            second = MODULE.populate(derived, root=root, **metadata())
            after = {path.name: path.read_bytes() for path in target.iterdir()}
            self.assertEqual(first["outcome"], "populated")
            self.assertEqual(second["outcome"], "verified_existing")
            self.assertEqual(before, after)
            self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_partial_market_artifact_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived = retained_derived_copy(Path(tmp))
            (derived / "korea-breadth-recent-kosdaq.json").unlink()
            with self.assertRaisesRegex(MODULE.AggregateRetentionError, "SOURCE_PACKET_MISSING"):
                MODULE.populate(derived, root=Path(tmp) / "repo", **metadata())

    def test_self_rehashed_count_tamper_fails_independent_arithmetic(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived = retained_derived_copy(Path(tmp))
            path = derived / "korea-breadth-recent-kospi.json"
            packet = json.loads(path.read_text(encoding="utf-8"))
            packet["participation"]["advancing_count"] += 1
            unsigned = {key: value for key, value in packet.items() if key != "payload_sha256"}
            packet["payload_sha256"] = MODULE.payload_sha256(unsigned)
            path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.AggregateRetentionError, "PARTICIPATION_ARITHMETIC_INVALID"
            ):
                MODULE.populate(derived, root=Path(tmp) / "repo", **metadata())

    def test_self_rehashed_authority_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived = retained_derived_copy(Path(tmp))
            path = derived / "korea-breadth-recent-kosdaq.json"
            packet = json.loads(path.read_text(encoding="utf-8"))
            packet["trading_action_authorized"] = True
            unsigned = {key: value for key, value in packet.items() if key != "payload_sha256"}
            packet["payload_sha256"] = MODULE.payload_sha256(unsigned)
            path.write_text(json.dumps(packet, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.AggregateRetentionError, "SOURCE_PACKET_AUTHORITY_INVALID"
            ):
                MODULE.populate(derived, root=Path(tmp) / "repo", **metadata())

    def test_existing_bundle_tamper_is_never_repaired_or_overwritten(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            derived = retained_derived_copy(Path(tmp))
            result = MODULE.populate(derived, root=root, **metadata())
            target_file = Path(result["path"]) / "korea-breadth-recent-kospi.json"
            target_file.write_bytes(target_file.read_bytes() + b" ")
            with self.assertRaises(MODULE.AggregateRetentionError):
                MODULE.populate(derived, root=root, **metadata())

    def test_both_live_capture_workflows_bind_artifact_lineage_and_stage_aggregate(self):
        for workflow, expected_path in (
            (P1_WORKFLOW, ".github/workflows/p1-kr05-korea-breadth-live.yml"),
            (P2_WORKFLOW, ".github/workflows/p2-03-korea-observation-pair.yml"),
        ):
            text = workflow.read_text(encoding="utf-8")
            self.assertIn("id: upload_derived", text)
            self.assertIn("artifact_id: ${{ steps.upload_derived.outputs.artifact-id }}", text)
            self.assertIn("artifact_digest: ${{ steps.upload_derived.outputs.artifact-digest }}", text)
            self.assertIn("korea_breadth_aggregate_populate.py", text)
            self.assertIn("--source-head-sha \"${{ github.sha }}\"", text)
            self.assertIn("--artifact-id \"${{ needs.korea-breadth-live-proof.outputs.artifact_id }}\"", text)
            self.assertIn("--artifact-digest \"$ARTIFACT_DIGEST\"", text)
            self.assertIn("data/observations/korea_breadth_aggregate", text)
            if workflow == P2_WORKFLOW:
                self.assertIn(f'--workflow-path "{expected_path}"', text)
            else:
                self.assertNotIn("--workflow-path", text)

    def test_only_approved_capture_workflows_are_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            derived = retained_derived_copy(Path(tmp))
            kwargs = metadata()
            kwargs["workflow_path"] = ".github/workflows/unapproved.yml"
            with self.assertRaisesRegex(
                MODULE.AggregateRetentionError, "SOURCE_WORKFLOW_INVALID"
            ):
                MODULE.populate(derived, root=Path(tmp) / "repo", **kwargs)


if __name__ == "__main__":
    unittest.main()
