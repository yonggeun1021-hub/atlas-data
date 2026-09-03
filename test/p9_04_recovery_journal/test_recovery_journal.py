#!/usr/bin/env python3
"""P9-04 evidence-only recovery journal regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "decision" / "action_order_recovery_journal.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("action_order_recovery_journal", SOURCE)
GUARD = MODULE.GUARD
GUARD_CONTRACT = GUARD.load_contract()
JOURNAL_ID = "P9.04.RECOVERY.TEST"


def record(
    key="IDEMPOTENCY.TSM.1",
    event_id="EVENT.TSM.1",
    action_id="ACTION.TSM.1",
    order_id="ORDER.TSM.1",
    marker="a",
):
    return {
        "idempotency_key": key,
        "event_id": event_id,
        "action_id": action_id,
        "order_id": order_id,
        "market": "US",
        "intent_sha256": marker * 64,
        "first_seen_at": "2026-09-03T01:00:00Z",
        "source_ref": f"fixture://p9-04/{key}",
        "source_sha256": "f" * 64,
    }


def attempt(
    key="IDEMPOTENCY.TSM.1",
    event_id="EVENT.TSM.1",
    action_id="ACTION.TSM.1",
    order_id="ORDER.TSM.1",
    marker="a",
    attempted_at="2026-09-03T01:00:00Z",
):
    value = record(key, event_id, action_id, order_id, marker)
    value["attempted_at"] = attempted_at
    value.pop("first_seen_at")
    return value


def ledger(rows=None):
    value = {
        "schema_version": "action_order_idempotency_ledger/1",
        "contract_version": "action_order_idempotency_guard/1",
        "ledger_id": "P9-04-RECOVERY-TEST-LEDGER",
        "records": [] if rows is None else rows,
        "authority": copy.deepcopy(GUARD_CONTRACT["ledger_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["records"] = sorted(
        normalized["records"],
        key=lambda row: (row["first_seen_at"], row["idempotency_key"]),
    )
    value["packet_sha256"] = GUARD.payload_sha256(normalized)
    return value


def batch(rows=None, batch_id="P9.04.RECOVERY.BATCH.1", observed_at="2026-09-03T01:10:00Z"):
    value = {
        "schema_version": "action_order_attempt_batch/1",
        "contract_version": "action_order_idempotency_guard/1",
        "batch_id": batch_id,
        "observed_at": observed_at,
        "attempts": [attempt()] if rows is None else rows,
        "authority": copy.deepcopy(GUARD_CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["attempts"] = sorted(
        normalized["attempts"],
        key=lambda row: (row["attempted_at"], row["idempotency_key"]),
    )
    value["packet_sha256"] = GUARD.payload_sha256(normalized)
    return value


def rehash(value, field):
    changed = copy.deepcopy(value)
    changed.pop(field)
    changed[field] = MODULE.payload_sha256(changed)
    return changed


class ActionOrderRecoveryJournalTests(unittest.TestCase):
    def test_contract_is_exact_type_checked_and_all_real_authority_is_false(self):
        contract = MODULE.load_contract()
        self.assertTrue(contract["authority"]["simulation_shadow_evidence_only"])
        for key, value in contract["authority"].items():
            if key != "simulation_shadow_evidence_only":
                self.assertFalse(value, key)
        changed = copy.deepcopy(contract)
        changed["schema_version"] = True
        with self.assertRaisesRegex(
            MODULE.ActionOrderRecoveryJournalError, "CONTRACT_FIELD_MISMATCH"
        ):
            MODULE._validate_contract(changed)

    def test_commit_restart_and_exact_retry_are_zero_write_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "journal"
            initialized = MODULE.initialize_journal(root, JOURNAL_ID, ledger())
            self.assertEqual(initialized["action"], "INITIALIZED")
            committed = MODULE.apply_attempt_batch(root, JOURNAL_ID, batch())
            self.assertEqual(committed["action"], "COMMITTED_EVIDENCE_ONLY")
            self.assertEqual(committed["head"]["revision"], 1)
            self.assertEqual(
                committed["receipt"]["summary"],
                {
                    "attempt_count": 1,
                    "novel_recorded_count": 1,
                    "duplicate_blocked_count": 0,
                    "orders_created": 0,
                    "orders_submitted": 0,
                },
            )
            before = {
                path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.rglob("*.json")
            }
            recovered = MODULE.recover_journal(root, JOURNAL_ID)
            self.assertEqual(
                MODULE.canonical_json(recovered["head"]),
                MODULE.canonical_json(committed["head"]),
            )
            retry = MODULE.apply_attempt_batch(root, JOURNAL_ID, batch())
            self.assertEqual(retry["action"], "EXACT_RETRY_NOOP")
            after = {
                path.relative_to(root).as_posix(): (path.read_bytes(), path.stat().st_mtime_ns)
                for path in root.rglob("*.json")
            }
            self.assertEqual(before, after)

    def test_crash_before_head_leaves_last_commit_recoverable_then_retry_commits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "journal"
            MODULE.initialize_journal(root, JOURNAL_ID, ledger())
            original_write_head = MODULE._write_head_atomic
            with mock.patch.object(
                MODULE, "_write_head_atomic", side_effect=RuntimeError("simulated crash")
            ):
                with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                    MODULE.apply_attempt_batch(root, JOURNAL_ID, batch())
            recovered = MODULE.recover_journal(root, JOURNAL_ID)
            self.assertEqual(recovered["head"]["revision"], 0)
            self.assertEqual(recovered["ledger"]["records"], [])
            with mock.patch.object(MODULE, "_write_head_atomic", wraps=original_write_head):
                committed = MODULE.apply_attempt_batch(root, JOURNAL_ID, batch())
            self.assertEqual(committed["head"]["revision"], 1)
            self.assertEqual(len(committed["ledger"]["records"]), 1)
            repeated_init = MODULE.initialize_journal(root, JOURNAL_ID, ledger())
            self.assertEqual(repeated_init["action"], "ALREADY_INITIALIZED_NOOP")
            self.assertEqual(repeated_init["head"]["revision"], 1)

    def test_two_commits_preserve_chain_and_later_duplicate_is_blocked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "journal"
            MODULE.initialize_journal(root, JOURNAL_ID, ledger())
            MODULE.apply_attempt_batch(root, JOURNAL_ID, batch())
            second_attempt = attempt(
                key="IDEMPOTENCY.TSM.2",
                action_id="ACTION.TSM.2",
                order_id="ORDER.TSM.2",
                marker="b",
                attempted_at="2026-09-03T01:20:00Z",
            )
            second = batch(
                [second_attempt],
                batch_id="P9.04.RECOVERY.BATCH.2",
                observed_at="2026-09-03T01:30:00Z",
            )
            MODULE.apply_attempt_batch(root, JOURNAL_ID, second)
            duplicate = batch(
                [attempt(attempted_at="2026-09-03T01:40:00Z")],
                batch_id="P9.04.RECOVERY.BATCH.3",
                observed_at="2026-09-03T01:50:00Z",
            )
            result = MODULE.apply_attempt_batch(root, JOURNAL_ID, duplicate)
            self.assertEqual(result["head"]["revision"], 3)
            self.assertEqual(
                result["receipt"]["decisions"][0]["result"],
                "DUPLICATE_RETRY_BLOCKED",
            )
            self.assertEqual(len(result["ledger"]["records"]), 2)
            recovered = MODULE.recover_journal(root, JOURNAL_ID)
            self.assertEqual(recovered["head"]["revision"], 3)

    def test_recovery_rejects_type_digest_chain_and_blob_derivation_tamper(self):
        variants = []
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "journal"
            MODULE.initialize_journal(root, JOURNAL_ID, ledger())
            committed = MODULE.apply_attempt_batch(root, JOURNAL_ID, batch())
            head_path = root / "head.json"
            original = json.loads(head_path.read_text(encoding="utf-8"))

            changed = copy.deepcopy(original)
            changed["revision"] = True
            variants.append((changed, "HEAD_REVISION_TYPE_INVALID"))

            changed = copy.deepcopy(original)
            changed["authority"]["order_execution_authorized"] = 0
            changed = rehash(changed, "packet_sha256")
            variants.append((changed, "HEAD_IDENTITY_INVALID"))

            changed = copy.deepcopy(original)
            changed["commits"][0]["prior_ledger_sha256"] = "e" * 64
            changed["commits"][0] = rehash(changed["commits"][0], "commit_sha256")
            changed = rehash(changed, "packet_sha256")
            variants.append((changed, "COMMIT_CHAIN_DERIVATION_MISMATCH"))

            for changed, code in variants:
                with self.subTest(code=code):
                    head_path.write_text(json.dumps(changed), encoding="utf-8")
                    with self.assertRaisesRegex(
                        MODULE.ActionOrderRecoveryJournalError, code
                    ):
                        MODULE.recover_journal(root, JOURNAL_ID)
            head_path.write_text(json.dumps(original), encoding="utf-8")

            ledger_path = MODULE._ledger_blob_path(
                root, committed["head"]["current_ledger_sha256"]
            )
            changed_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
            changed_ledger["records"][0]["source_ref"] = "fixture://tampered"
            unsigned = copy.deepcopy(changed_ledger)
            unsigned.pop("packet_sha256")
            changed_ledger["packet_sha256"] = GUARD.payload_sha256(unsigned)
            ledger_path.write_text(json.dumps(changed_ledger), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.ActionOrderRecoveryJournalError,
                "LEDGER_BLOB_NAME_MISMATCH",
            ):
                MODULE.recover_journal(root, JOURNAL_ID)

    def test_guard_collision_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "journal"
            MODULE.initialize_journal(root, JOURNAL_ID, ledger([record()]))
            changed = batch([attempt(marker="b")])
            before = (root / "head.json").read_bytes()
            with self.assertRaisesRegex(
                MODULE.ActionOrderRecoveryJournalError,
                "IDEMPOTENCY_KEY_PAYLOAD_COLLISION",
            ):
                MODULE.apply_attempt_batch(root, JOURNAL_ID, changed)
            self.assertEqual((root / "head.json").read_bytes(), before)
            self.assertEqual(MODULE.recover_journal(root, JOURNAL_ID)["head"]["revision"], 0)

    def test_repository_storage_and_network_or_process_imports_are_forbidden(self):
        with self.assertRaisesRegex(
            MODULE.ActionOrderRecoveryJournalError,
            "TRACKED_JOURNAL_ROOT_FORBIDDEN",
        ):
            MODULE.initialize_journal(ROOT / "data" / "p9-04-journal", JOURNAL_ID, ledger())
        self.assertFalse((ROOT / "data" / "p9-04-journal").exists())

        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)


if __name__ == "__main__":
    unittest.main(verbosity=2)
