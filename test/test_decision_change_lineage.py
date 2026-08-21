#!/usr/bin/env python3
"""P10-04 Decision change lineage regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "decision_change_lineage.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("decision_change_lineage", SOURCE)
CONTRACT = MODULE.load_contract()


def snapshot(marker="a", decided_at="2026-08-21T01:00:00Z"):
    return {
        "schema_version": "decision_snapshot_reference/1",
        "decision_key": "DECISION.US.MSFT",
        "market": "US",
        "subject_id": "US.XNAS.MSFT",
        "decided_at": decided_at,
        "decision_sha256": marker * 64,
        "source_ref": f"test://decision/{marker}",
        "source_sha256": marker * 64,
    }


def evidence(evidence_id="EVIDENCE.MSFT.1", available_at="2026-08-21T00:59:00Z"):
    return {
        "evidence_id": evidence_id,
        "uri": f"test://evidence/{evidence_id}",
        "available_at": available_at,
        "source_sha256": "e" * 64,
    }


def claim(prior=None, current=None, when="2026-08-21T01:10:00Z", reasons=None, proof=None):
    return {
        "decision_key": "DECISION.US.MSFT",
        "market": "US",
        "subject_id": "US.XNAS.MSFT",
        "change_observed_at": when,
        "prior_snapshot": prior,
        "current_snapshot": current,
        "reason_codes": ["NEW_EVIDENCE"] if reasons is None else reasons,
        "evidence": [evidence()] if proof is None else proof,
    }


def batch(rows=None):
    value = {
        "schema_version": "decision_change_claim_batch/1",
        "contract_version": "decision_change_lineage/1",
        "batch_id": "DECISION.LINEAGE.TEST.20260821",
        "observed_at": "2026-08-21T02:00:00Z",
        "claims": [claim(snapshot("a"), snapshot("b"))] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class DecisionChangeLineageTests(unittest.TestCase):
    def test_contract_is_opaque_and_closes_decision_action_authority(self):
        self.assertEqual(CONTRACT["repository_decision_contract"], "ABSENT")
        self.assertEqual(CONTRACT["decision_payload_binding"], "OPAQUE_SHA256_ONLY")
        self.assertTrue(CONTRACT["authority"]["lineage_recording_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "lineage_recording_only":
                self.assertFalse(value, key)

    def test_changed_entry_preserves_reason_evidence_time_and_hashes(self):
        result = MODULE.build_lineage(batch(), CONTRACT)
        entry = result["entries"][0]
        self.assertEqual(entry["change_type"], "CHANGED")
        self.assertEqual(entry["reason_codes"], ["NEW_EVIDENCE"])
        self.assertEqual(entry["evidence"][0]["evidence_id"], "EVIDENCE.MSFT.1")
        self.assertEqual(entry["change_observed_at"], "2026-08-21T01:10:00Z")
        self.assertEqual(entry["prior_snapshot"]["decision_sha256"], "a" * 64)
        self.assertEqual(entry["current_snapshot"]["decision_sha256"], "b" * 64)
        self.assertIsNone(entry["decision_payload"])
        self.assertIsNone(entry["decision_interpretation"])
        self.assertIsNone(entry["action"])

    def test_created_unchanged_and_retired_are_derived_not_claimed(self):
        created = claim(None, snapshot("a"), when="2026-08-21T01:00:00Z")
        unchanged = claim(
            snapshot("a"), snapshot("a", "2026-08-21T01:10:00Z"),
            when="2026-08-21T01:10:00Z", reasons=[], proof=[],
        )
        retired = claim(
            snapshot("a", "2026-08-21T01:10:00Z"), None,
            when="2026-08-21T01:20:00Z",
        )
        result = MODULE.build_lineage(batch([retired, unchanged, created]), CONTRACT)
        self.assertEqual(
            [entry["change_type"] for entry in result["entries"]],
            ["CREATED", "UNCHANGED", "RETIRED"],
        )
        self.assertEqual(result["summary"]["created_count"], 1)
        self.assertEqual(result["summary"]["unchanged_count"], 1)
        self.assertEqual(result["summary"]["retired_count"], 1)
        self.assertEqual(result["summary"]["decisions_created"], 0)
        self.assertEqual(result["summary"]["decisions_changed"], 0)

    def test_changed_requires_reason_and_evidence_unchanged_forbids_both(self):
        variants = [
            (claim(snapshot("a"), snapshot("b"), reasons=[]), "CHANGE_REASON_EVIDENCE_REQUIRED"),
            (claim(snapshot("a"), snapshot("b"), proof=[]), "CHANGE_REASON_EVIDENCE_REQUIRED"),
            (claim(snapshot("a"), snapshot("a"), reasons=["NO_CHANGE"], proof=[evidence()]), "UNCHANGED_HAS_REASON_OR_EVIDENCE"),
        ]
        for row, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.DecisionChangeLineageError, error
            ):
                MODULE.build_lineage(batch([row]), CONTRACT)

    def test_snapshot_identity_future_evidence_and_change_time_fail_closed(self):
        wrong = snapshot("b")
        wrong["subject_id"] = "US.XNAS.NVDA"
        variants = [
            (claim(snapshot("a"), wrong), "SNAPSHOT_IDENTITY_MISMATCH"),
            (claim(snapshot("a"), snapshot("b"), proof=[evidence(available_at="2026-08-21T01:10:01Z")]), "EVIDENCE_FROM_FUTURE"),
            (claim(snapshot("a"), snapshot("b"), when="2026-08-21T02:00:01Z"), "CHANGE_FROM_FUTURE"),
        ]
        for row, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.DecisionChangeLineageError, error
            ):
                MODULE.build_lineage(batch([row]), CONTRACT)

    def test_chain_break_and_duplicate_identity_fail_closed(self):
        first = claim(None, snapshot("a"), when="2026-08-21T01:00:00Z")
        broken = claim(snapshot("b"), snapshot("c"), when="2026-08-21T01:10:00Z")
        with self.assertRaisesRegex(MODULE.DecisionChangeLineageError, "CLAIM_CHAIN_BROKEN"):
            MODULE.build_lineage(batch([first, broken]), CONTRACT)
        with self.assertRaisesRegex(MODULE.DecisionChangeLineageError, "CLAIM_IDENTITY_DUPLICATE"):
            MODULE.build_lineage(batch([first, copy.deepcopy(first)]), CONTRACT)

    def test_digest_authority_and_reason_order_fail_closed(self):
        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.DecisionChangeLineageError, "BATCH_SHA_INVALID_MISMATCH"):
            MODULE.build_lineage(digest, CONTRACT)
        authority = batch()
        authority["authority"]["decision_change_authorized"] = True
        authority["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in authority.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.DecisionChangeLineageError, "BATCH_IDENTITY_INVALID"):
            MODULE.build_lineage(authority, CONTRACT)
        unordered = claim(snapshot("a"), snapshot("b"), reasons=["Z_REASON", "A_REASON"])
        with self.assertRaisesRegex(MODULE.DecisionChangeLineageError, "REASONS_INVALID"):
            MODULE.build_lineage(batch([unordered]), CONTRACT)

    def test_deterministic_permutation_safe_and_inputs_immutable(self):
        created = claim(None, snapshot("a"), when="2026-08-21T01:00:00Z")
        changed = claim(snapshot("a"), snapshot("b"), when="2026-08-21T01:10:00Z")
        first_batch = batch([changed, created])
        before = MODULE.canonical_json(first_batch)
        first = MODULE.build_lineage(first_batch, CONTRACT)
        second = MODULE.build_lineage(batch([created, changed]), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(first_batch), before)

    def test_output_type_chain_authority_and_digest_tamper_fail_closed(self):
        original = MODULE.build_lineage(batch(), CONTRACT)
        variants = []
        changed_type = copy.deepcopy(original)
        changed_type["entries"][0]["change_type"] = "UNCHANGED"
        variants.append((changed_type, "OUTPUT_CHANGE_TYPE_INVALID"))
        reversed_time = copy.deepcopy(original)
        reversed_time["entries"][0]["prior_snapshot"]["decided_at"] = (
            "2026-08-21T01:01:00Z"
        )
        reversed_time["entries"][0]["current_snapshot"]["decided_at"] = (
            "2026-08-21T01:00:00Z"
        )
        variants.append((reversed_time, "OUTPUT_SNAPSHOT_TIME_REVERSED"))
        payload = copy.deepcopy(original)
        payload["entries"][0]["decision_payload"] = {"secret": "decision"}
        variants.append((payload, "OUTPUT_AUTHORITY_EXPANSION"))
        summary = copy.deepcopy(original)
        summary["summary"]["decisions_changed"] = 1
        variants.append((summary, "OUTPUT_SUMMARY_INVALID"))
        authority = copy.deepcopy(original)
        authority["authority"]["decision_change_authorized"] = True
        variants.append((authority, "OUTPUT_IDENTITY_INVALID"))
        digest = copy.deepcopy(original)
        digest["packet_sha256"] = "0" * 64
        variants.append((digest, "OUTPUT_SHA_MISMATCH"))
        for packet, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.DecisionChangeLineageError, error
            ):
                MODULE.validate_output(packet, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            batch_path = temp / "batch.json"
            batch_path.write_text(json.dumps(batch()), encoding="utf-8")
            output = temp / "out" / "lineage.json"
            self.assertEqual(MODULE.run(batch_path, output), 0)
            forbidden = ROOT / "data" / "decision_change_lineage_test.json"
            self.assertEqual(MODULE.run(batch_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
