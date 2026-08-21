#!/usr/bin/env python3
"""P8-03 READY != ENTRY / Signal != Order regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "ready_signal_order_boundary.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("ready_signal_order_boundary", SOURCE)
CONTRACT = MODULE.load_contract()


def subject(
    subject_id="US:XNAS:TSM",
    market="US",
    ready_status="READY",
    signal_status="PRESENT",
):
    ready_observed = ready_status != "NOT_EVALUATED"
    signal_observed = signal_status != "NOT_EVALUATED"
    return {
        "subject_id": subject_id,
        "market": market,
        "ready_status": ready_status,
        "ready_source_ref": "test://ready/TSM" if ready_observed else None,
        "ready_source_sha256": "a" * 64 if ready_observed else None,
        "signal_status": signal_status,
        "signal_id": "SIGNAL_TSM_1" if signal_status == "PRESENT" else None,
        "signal_source_ref": "test://signal/TSM" if signal_observed else None,
        "signal_source_sha256": "b" * 64 if signal_observed else None,
    }


def input_packet(rows=None):
    value = {
        "schema_version": "ready_signal_observation_packet/1",
        "contract_version": "ready_signal_order_boundary/1",
        "packet_id": "TEST-READY-SIGNAL-2026-08-21",
        "as_of_utc": "2026-08-21T01:00:00Z",
        "subjects": [subject()] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["subjects"] = sorted(
        normalized["subjects"], key=lambda row: row["subject_id"]
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class ReadySignalOrderBoundaryTests(unittest.TestCase):
    def test_contract_pins_both_invariants_and_closes_action_authority(self):
        self.assertEqual(CONTRACT["invariants"], [
            "READY_NEVER_IMPLIES_ENTRY_TRIGGER",
            "SIGNAL_NEVER_IMPLIES_ORDER",
        ])
        self.assertEqual(
            CONTRACT["decision_contract_status"],
            "P8_02_CONTRACT_AVAILABLE_NO_ACTION_AUTHORITY",
        )
        self.assertTrue(CONTRACT["authority"]["boundary_enforcement_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "boundary_enforcement_only":
                self.assertFalse(value, key)

    def test_ready_with_present_signal_still_creates_no_entry_or_order(self):
        result = MODULE.build_packet(input_packet(), CONTRACT)
        row = result["subjects"][0]
        self.assertEqual(row["ready_status"], "READY")
        self.assertEqual(row["signal_status"], "PRESENT")
        self.assertIsNone(row["entry_trigger"])
        self.assertEqual(row["entry_trigger_status"], "NOT_EVALUATED")
        self.assertIsNone(row["order_intent"])
        self.assertEqual(row["order_status"], "NOT_EVALUATED")
        self.assertEqual(result["summary"]["ready_count"], 1)
        self.assertEqual(result["summary"]["signal_present_count"], 1)
        self.assertEqual(result["summary"]["entry_trigger_count"], 0)
        self.assertEqual(result["summary"]["order_intent_count"], 0)

    def test_every_ready_signal_combination_remains_actionless(self):
        for ready in CONTRACT["ready_statuses"]:
            for signal in CONTRACT["signal_statuses"]:
                with self.subTest(ready=ready, signal=signal):
                    result = MODULE.build_packet(
                        input_packet([subject(ready_status=ready, signal_status=signal)]),
                        CONTRACT,
                    )["subjects"][0]
                    self.assertIsNone(result["entry_trigger"])
                    self.assertIsNone(result["order_intent"])

    def test_direct_guards_reject_derived_entry_and_order(self):
        with self.assertRaisesRegex(
            MODULE.ReadySignalOrderBoundaryError,
            "DERIVED_ENTRY_TRIGGER_FORBIDDEN",
        ):
            MODULE.assert_entry_not_derived("READY", {"trigger": "UNAUTHORIZED"})
        with self.assertRaisesRegex(
            MODULE.ReadySignalOrderBoundaryError,
            "DERIVED_ORDER_FORBIDDEN",
        ):
            MODULE.assert_order_not_derived("PRESENT", {"order": "UNAUTHORIZED"})
        MODULE.assert_entry_not_derived("READY", None)
        MODULE.assert_order_not_derived("PRESENT", None)

    def test_ready_and_signal_lineage_rules_fail_closed(self):
        cases = []
        ready = subject()
        ready["ready_source_sha256"] = None
        cases.append((ready, "READY_LINEAGE_INCOMPLETE"))
        hidden_ready = subject(ready_status="NOT_EVALUATED")
        hidden_ready["ready_source_ref"] = "test://hidden"
        cases.append((hidden_ready, "READY_NOT_EVALUATED_HAS_LINEAGE"))
        signal = subject()
        signal["signal_source_sha256"] = None
        cases.append((signal, "SIGNAL_LINEAGE_INCOMPLETE"))
        absent = subject(signal_status="ABSENT")
        absent["signal_source_ref"] = None
        cases.append((absent, "SIGNAL_ABSENCE_LINEAGE_INVALID"))
        hidden_signal = subject(signal_status="NOT_EVALUATED")
        hidden_signal["signal_id"] = "HIDDEN_SIGNAL"
        cases.append((hidden_signal, "SIGNAL_NOT_EVALUATED_HAS_LINEAGE"))
        for row, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.ReadySignalOrderBoundaryError, error
            ):
                MODULE.build_packet(input_packet([row]), CONTRACT)

    def test_duplicate_subject_authority_and_digest_drift_fail_closed(self):
        duplicate = input_packet([subject(), copy.deepcopy(subject())])
        with self.assertRaisesRegex(
            MODULE.ReadySignalOrderBoundaryError,
            "SUBJECT_ID_DUPLICATE",
        ):
            MODULE.build_packet(duplicate, CONTRACT)

        authority = input_packet()
        authority["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.ReadySignalOrderBoundaryError,
            "INPUT_IDENTITY_INVALID",
        ):
            MODULE.build_packet(authority, CONTRACT)

        digest = input_packet()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.ReadySignalOrderBoundaryError,
            "INPUT_PACKET_SHA_MISMATCH",
        ):
            MODULE.build_packet(digest, CONTRACT)

    def test_contract_authority_tamper_fails_closed(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["order_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.ReadySignalOrderBoundaryError,
            "CONTRACT_FIELD_MISMATCH:authority",
        ):
            MODULE.build_packet(input_packet(), contract)

    def test_output_is_deterministic_permutation_safe_and_input_immutable(self):
        rows = [
            subject(),
            subject(
                subject_id="CRYPTO:KRAKEN:BTC",
                market="CRYPTO",
                ready_status="NOT_READY",
                signal_status="ABSENT",
            ),
        ]
        value = input_packet(rows)
        before = MODULE.canonical_json(value)
        first = MODULE.build_packet(value, CONTRACT)
        second = MODULE.build_packet(input_packet(list(reversed(rows))), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(value), before)
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

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
            input_path = write_json(tmp / "input.json", input_packet())
            output_path = tmp / "nested" / "boundary.json"
            self.assertEqual(MODULE.run(input_path, output_path), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["order_intent_count"], 0)
            self.assertEqual(list(output_path.parent.glob(".boundary.json.*")), [])

            forbidden = ROOT / "data" / "ready_signal_order_boundary_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
