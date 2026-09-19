#!/usr/bin/env python3
"""Focused P10-06 durable-store regressions."""
from __future__ import annotations

import copy
import importlib.util
import multiprocessing
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


LEDGER = load("p10_06_store_ledger", ROOT / "shadow" / "investment_review_shadow_ledger.py")
STORE = load("p10_06_store", ROOT / "shadow" / "investment_review_shadow_store.py")
P8_FIXTURE = load("p10_06_store_p8_fixture", ROOT / "test" / "test_investment_decision_review.py")


def review():
    rules = P8_FIXTURE.rule_packet()
    return P8_FIXTURE.MODULE.build_packet(P8_FIXTURE.thesis(rules), rules, "2026-08-24T00:01:00Z")


def record(sequence, previous=None):
    return LEDGER.build_record(review(), f"2026-08-{23 + sequence:02d}T00:02:00Z", sequence, previous)


def append_in_process(path_text, value, queue):
    try:
        STORE.append(Path(path_text), value)
        queue.put("APPENDED")
    except Exception as exc:
        queue.put(type(exc).__name__ + ":" + str(exc))


class InvestmentReviewShadowStoreTests(unittest.TestCase):
    def test_replay_of_missing_path_creates_no_directory_or_lock_file(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "missing" / "review.jsonl"
            self.assertEqual(STORE.replay(path), [])
            self.assertFalse(path.parent.exists())
            self.assertFalse(path.with_name(".review.jsonl.lock").exists())

    def test_append_and_replay_validate_genesis_and_chain(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "review.jsonl"
            first = record(1)
            second = record(2, first["record_sha256"])
            self.assertEqual(STORE.append(path, first), first)
            self.assertEqual(STORE.append(path, second), second)
            self.assertEqual(STORE.replay(path), [first, second])

    def test_invalid_input_does_not_create_or_change_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "review.jsonl"
            invalid = record(1)
            invalid["capital"] = {"authorized": True, "amount": 1}
            with self.assertRaisesRegex(STORE.InvestmentReviewShadowStoreError, "STORE_APPEND_RECORD_INVALID"):
                STORE.append(path, invalid)
            self.assertFalse(path.exists())
            first = record(1)
            STORE.append(path, first)
            before = path.read_bytes()
            stale = record(1)
            with self.assertRaisesRegex(STORE.InvestmentReviewShadowStoreError, "STORE_DUPLICATE_RECORD"):
                STORE.append(path, stale)
            self.assertEqual(path.read_bytes(), before)

    def test_precommit_sync_failure_preserves_existing_ledger(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "review.jsonl"
            first = record(1)
            STORE.append(path, first)
            before = path.read_bytes()
            second = record(2, first["record_sha256"])
            with mock.patch.object(STORE.os, "fsync", side_effect=OSError("forced sync failure")):
                with self.assertRaisesRegex(STORE.InvestmentReviewShadowStoreError, "STORE_APPEND_WRITE_FAILED"):
                    STORE.append(path, second)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(STORE.replay(path), [first])

    def test_postcommit_directory_sync_failure_is_not_reported_as_rollback(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "review.jsonl"
            first = record(1)
            real_fsync = STORE.os.fsync
            calls = 0

            def fail_directory_sync(descriptor):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("forced directory sync failure")
                return real_fsync(descriptor)

            with mock.patch.object(STORE.os, "fsync", side_effect=fail_directory_sync):
                with self.assertRaisesRegex(
                    STORE.InvestmentReviewShadowStoreError,
                    "STORE_APPEND_COMMITTED_DURABILITY_UNCERTAIN",
                ):
                    STORE.append(path, first)
            self.assertEqual(STORE.replay(path), [first])

    def test_replay_rejects_tamper_sequence_and_link_gaps(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "review.jsonl"
            first = record(1)
            second = record(2, first["record_sha256"])
            STORE.append(path, first)
            STORE.append(path, second)
            cases = []
            tampered = copy.deepcopy(second)
            tampered["subject"] = "TAMPERED"
            cases.append((tampered, "STORE_RECORD_REJECTED:2"))
            sequence_gap = copy.deepcopy(second)
            sequence_gap["sequence"] = 3
            sequence_gap["record_sha256"] = LEDGER.payload_sha256({key: value for key, value in sequence_gap.items() if key != "record_sha256"})
            cases.append((sequence_gap, "STORE_SEQUENCE_GAP:2"))
            broken_link = copy.deepcopy(second)
            broken_link["lineage"]["previous_record_sha256"] = "a" * 64
            broken_link["record_sha256"] = LEDGER.payload_sha256({key: value for key, value in broken_link.items() if key != "record_sha256"})
            cases.append((broken_link, "STORE_CHAIN_LINK_INVALID:2"))
            for altered, code in cases:
                with self.subTest(code=code):
                    path.write_text(LEDGER.canonical_json(first) + "\n" + LEDGER.canonical_json(altered) + "\n", encoding="utf-8")
                    with self.assertRaisesRegex(STORE.InvestmentReviewShadowStoreError, code):
                        STORE.replay(path)

    def test_concurrent_stale_append_preserves_one_valid_chain(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "review.jsonl"
            first = record(1)
            candidate = record(2, first["record_sha256"])
            STORE.append(path, first)
            queue = multiprocessing.Queue()
            processes = [
                multiprocessing.Process(target=append_in_process, args=(str(path), candidate, queue))
                for _ in range(2)
            ]
            for process in processes:
                process.start()
            for process in processes:
                process.join(10)
                self.assertEqual(process.exitcode, 0)
            outcomes = sorted(queue.get(timeout=2) for _ in processes)
            self.assertEqual(outcomes[0], "APPENDED")
            self.assertIn("STORE_DUPLICATE_RECORD", outcomes[1])
            self.assertEqual([row["sequence"] for row in STORE.replay(path)], [1, 2])


if __name__ == "__main__":
    unittest.main()
