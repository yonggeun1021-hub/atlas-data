#!/usr/bin/env python3
"""P9-04 duplicate Action / Order guard regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "action_order_idempotency.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("action_order_idempotency", SOURCE)
CONTRACT = MODULE.load_contract()


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
        "first_seen_at": "2026-08-21T01:00:00Z",
        "source_ref": f"test://intent/{key}",
        "source_sha256": "f" * 64,
    }


def attempt(
    key="IDEMPOTENCY.TSM.1",
    event_id="EVENT.TSM.1",
    action_id="ACTION.TSM.1",
    order_id="ORDER.TSM.1",
    marker="a",
    attempted_at="2026-08-21T01:00:00Z",
):
    value = record(key, event_id, action_id, order_id, marker)
    value["attempted_at"] = attempted_at
    value.pop("first_seen_at")
    return value


def ledger(rows=None):
    value = {
        "schema_version": "action_order_idempotency_ledger/1",
        "contract_version": "action_order_idempotency_guard/1",
        "ledger_id": "TEST-IDEMPOTENCY-LEDGER",
        "records": [] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["ledger_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["records"] = sorted(
        normalized["records"],
        key=lambda row: (row["first_seen_at"], row["idempotency_key"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def batch(rows=None):
    value = {
        "schema_version": "action_order_attempt_batch/1",
        "contract_version": "action_order_idempotency_guard/1",
        "batch_id": "TEST-ATTEMPTS-2026-08-21",
        "observed_at": "2026-08-21T01:10:00Z",
        "attempts": [attempt()] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["attempts"] = sorted(
        normalized["attempts"],
        key=lambda row: (row["attempted_at"], row["idempotency_key"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def rehash_result(value):
    changed = copy.deepcopy(value)
    changed.pop("packet_sha256", None)
    changed["packet_sha256"] = MODULE.payload_sha256(changed)
    return changed


def rehash_source(value):
    changed = copy.deepcopy(value)
    changed.pop("packet_sha256", None)
    changed["packet_sha256"] = MODULE.payload_sha256(changed)
    return changed


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class ActionOrderIdempotencyTests(unittest.TestCase):
    def test_contract_closes_creation_execution_broker_and_trading_authority(self):
        self.assertEqual(CONTRACT["duplicate_result"], "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(
            CONTRACT["novel_result"],
            "NOVEL_RECORDED_EXECUTION_NOT_AUTHORIZED",
        )
        self.assertEqual(CONTRACT["collision_policy"], "HARD_FAIL")
        self.assertTrue(CONTRACT["authority"]["duplicate_guard_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "duplicate_guard_only":
                self.assertFalse(value, key)

    def test_novel_attempt_is_recorded_but_never_created_or_submitted(self):
        result = MODULE.build_result(ledger(), batch(), CONTRACT)
        decision = result["decisions"][0]
        self.assertEqual(
            decision["result"],
            "NOVEL_RECORDED_EXECUTION_NOT_AUTHORIZED",
        )
        self.assertFalse(decision["execution_authorized"])
        self.assertIsNone(decision["broker_submission"])
        self.assertEqual(result["summary"]["novel_recorded_count"], 1)
        self.assertEqual(result["summary"]["orders_created"], 0)
        self.assertEqual(result["summary"]["orders_submitted"], 0)
        self.assertEqual(len(result["updated_ledger_candidate"]["records"]), 1)

    def test_retry_against_prior_ledger_is_blocked_without_append(self):
        result = MODULE.build_result(ledger([record()]), batch(), CONTRACT)
        self.assertEqual(result["decisions"][0]["result"], "DUPLICATE_RETRY_BLOCKED")
        self.assertEqual(result["summary"]["duplicate_blocked_count"], 1)
        self.assertEqual(result["summary"]["novel_recorded_count"], 0)
        self.assertEqual(len(result["updated_ledger_candidate"]["records"]), 1)

    def test_duplicate_inside_same_batch_is_blocked_after_first_novel(self):
        first = attempt(attempted_at="2026-08-21T01:00:00Z")
        retry = attempt(attempted_at="2026-08-21T01:00:01Z")
        result = MODULE.build_result(ledger(), batch([retry, first]), CONTRACT)
        self.assertEqual(
            [row["result"] for row in result["decisions"]],
            [
                "NOVEL_RECORDED_EXECUTION_NOT_AUTHORIZED",
                "DUPLICATE_RETRY_BLOCKED",
            ],
        )
        self.assertEqual(len(result["updated_ledger_candidate"]["records"]), 1)

    def test_same_key_with_changed_payload_or_identity_is_hard_collision(self):
        cases = []
        changed_intent = attempt(marker="b")
        cases.append(changed_intent)
        changed_order = attempt(order_id="ORDER.TSM.2")
        cases.append(changed_order)
        for value in cases:
            with self.subTest(value=value), self.assertRaisesRegex(
                MODULE.ActionOrderIdempotencyError,
                "IDEMPOTENCY_KEY_PAYLOAD_COLLISION",
            ):
                MODULE.build_result(ledger([record()]), batch([value]), CONTRACT)

    def test_action_or_order_id_reused_under_new_key_is_hard_collision(self):
        action = attempt(key="IDEMPOTENCY.TSM.2", order_id="ORDER.TSM.2")
        with self.assertRaisesRegex(
            MODULE.ActionOrderIdempotencyError,
            "ACTION_ID_KEY_COLLISION",
        ):
            MODULE.build_result(ledger([record()]), batch([action]), CONTRACT)

        order = attempt(key="IDEMPOTENCY.TSM.2", action_id="ACTION.TSM.2")
        with self.assertRaisesRegex(
            MODULE.ActionOrderIdempotencyError,
            "ORDER_ID_KEY_COLLISION",
        ):
            MODULE.build_result(ledger([record()]), batch([order]), CONTRACT)

    def test_same_event_may_have_distinct_explicit_orders(self):
        rows = [
            attempt(),
            attempt(
                key="IDEMPOTENCY.TSM.2",
                action_id="ACTION.TSM.2",
                order_id="ORDER.TSM.2",
                marker="b",
                attempted_at="2026-08-21T01:00:01Z",
            ),
        ]
        result = MODULE.build_result(ledger(), batch(rows), CONTRACT)
        self.assertEqual(result["summary"]["novel_recorded_count"], 2)
        self.assertEqual(len(result["updated_ledger_candidate"]["records"]), 2)
        self.assertEqual(result["summary"]["orders_created"], 0)

    def test_ledger_duplicate_digest_and_authority_drift_fail_closed(self):
        duplicate = ledger([record(), copy.deepcopy(record())])
        with self.assertRaisesRegex(
            MODULE.ActionOrderIdempotencyError,
            "LEDGER_IDEMPOTENCY_KEY_DUPLICATE",
        ):
            MODULE.build_result(duplicate, batch([]), CONTRACT)

        digest = ledger()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ActionOrderIdempotencyError, "LEDGER_SHA_MISMATCH"):
            MODULE.build_result(digest, batch([]), CONTRACT)

        authority = ledger()
        authority["authority"]["order_execution_authorized"] = True
        with self.assertRaisesRegex(MODULE.ActionOrderIdempotencyError, "LEDGER_IDENTITY_INVALID"):
            MODULE.build_result(authority, batch([]), CONTRACT)

    def test_batch_future_time_digest_and_authority_drift_fail_closed(self):
        future = batch([attempt(attempted_at="2026-08-21T01:10:01Z")])
        with self.assertRaisesRegex(MODULE.ActionOrderIdempotencyError, "ATTEMPT_FROM_FUTURE"):
            MODULE.build_result(ledger(), future, CONTRACT)

        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ActionOrderIdempotencyError, "BATCH_SHA_MISMATCH"):
            MODULE.build_result(ledger(), digest, CONTRACT)

        authority = batch()
        authority["authority"]["order_execution_authorized"] = True
        with self.assertRaisesRegex(MODULE.ActionOrderIdempotencyError, "BATCH_IDENTITY_INVALID"):
            MODULE.build_result(ledger(), authority, CONTRACT)

    def test_output_is_deterministic_permutation_safe_and_inputs_immutable(self):
        rows = [
            attempt(),
            attempt(
                key="IDEMPOTENCY.BTC.1",
                event_id="EVENT.BTC.1",
                action_id="ACTION.BTC.1",
                order_id="ORDER.BTC.1",
                marker="b",
                attempted_at="2026-08-21T01:00:01Z",
            ),
        ]
        first_ledger = ledger()
        first_batch = batch(rows)
        before_ledger = MODULE.canonical_json(first_ledger)
        before_batch = MODULE.canonical_json(first_batch)
        first = MODULE.build_result(first_ledger, first_batch, CONTRACT)
        second = MODULE.build_result(ledger(), batch(list(reversed(rows))), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(first_ledger), before_ledger)
        self.assertEqual(MODULE.canonical_json(first_batch), before_batch)
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_result_embeds_exact_sources_and_round_trips_production_validator(self):
        prior = ledger([record()])
        attempts = batch()
        result = MODULE.build_result(prior, attempts, CONTRACT)
        self.assertEqual(
            result["schema_version"], "action_order_idempotency_result/2"
        )
        self.assertEqual(result["source_packets"]["prior_ledger"], prior)
        self.assertEqual(result["source_packets"]["attempt_batch"], attempts)
        self.assertEqual(
            MODULE.canonical_json(MODULE.validate_result(result, CONTRACT)),
            MODULE.canonical_json(result),
        )

    def test_self_rehashed_result_semantic_drift_fails_closed(self):
        result = MODULE.build_result(ledger(), batch(), CONTRACT)
        variants = []

        changed = copy.deepcopy(result)
        changed["decisions"][0]["result"] = "DUPLICATE_RETRY_BLOCKED"
        changed["summary"]["novel_recorded_count"] = 0
        changed["summary"]["duplicate_blocked_count"] = 1
        variants.append(changed)

        changed = copy.deepcopy(result)
        changed["updated_ledger_candidate"]["records"] = []
        nested = copy.deepcopy(changed["updated_ledger_candidate"])
        nested.pop("packet_sha256")
        changed["updated_ledger_candidate"]["packet_sha256"] = (
            MODULE.payload_sha256(nested)
        )
        variants.append(changed)

        changed = copy.deepcopy(result)
        changed["authority"]["order_execution_authorized"] = True
        variants.append(changed)

        for changed in variants:
            with self.subTest(changed=changed):
                with self.assertRaisesRegex(
                    MODULE.ActionOrderIdempotencyError,
                    "OUTPUT_(IDENTITY_INVALID|DERIVATION_MISMATCH)",
                ):
                    MODULE.validate_result(rehash_result(changed), CONTRACT)

    def test_embedded_source_semantics_are_revalidated_before_derivation(self):
        result = MODULE.build_result(ledger(), batch(), CONTRACT)

        authority = copy.deepcopy(result)
        source = authority["source_packets"]["attempt_batch"]
        source["authority"]["order_execution_authorized"] = True
        authority["source_packets"]["attempt_batch"] = rehash_source(source)
        with self.assertRaisesRegex(
            MODULE.ActionOrderIdempotencyError, "BATCH_IDENTITY_INVALID"
        ):
            MODULE.validate_result(rehash_result(authority), CONTRACT)

        substitution = copy.deepcopy(result)
        source = substitution["source_packets"]["attempt_batch"]
        source["attempts"][0]["source_ref"] = "test://intent/substituted"
        substitution["source_packets"]["attempt_batch"] = rehash_source(source)
        with self.assertRaisesRegex(
            MODULE.ActionOrderIdempotencyError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_result(rehash_result(substitution), CONTRACT)

    def test_source_is_offline_and_cli_writes_only_outside_repository(self):
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
            tmp = Path(tmp)
            ledger_path = write_json(tmp / "ledger.json", ledger())
            batch_path = write_json(tmp / "batch.json", batch())
            output_path = tmp / "nested" / "guard.json"
            self.assertEqual(MODULE.run(ledger_path, batch_path, output_path), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["orders_created"], 0)
            self.assertEqual(list(output_path.parent.glob(".guard.json.*")), [])

            forbidden = ROOT / "data" / "action_order_idempotency_test.json"
            self.assertEqual(MODULE.run(ledger_path, batch_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
