#!/usr/bin/env python3
"""P9-03 ENTRY / EXIT trigger eligibility regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "execution" / "entry_exit_trigger_eligibility.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("entry_exit_trigger_eligibility", SOURCE)
UNIFIED = load_module(
    "p903_unified_fixture", ROOT / "test" / "test_unified_decision_contract.py"
)
FRESHNESS = load_module(
    "p903_freshness_fixture", ROOT / "test" / "test_intraday_freshness.py"
)
CONTRACT = MODULE.load_contract()


def unified(action_available=True):
    components = UNIFIED.components()
    reasons = UNIFIED.reasons()
    if not action_available:
        components["ACTION_BOUNDARY"] = None
        reasons["ACTION_BOUNDARY"] = ["SOURCE_PACKET_NOT_PROVIDED"]
    return UNIFIED.MODULE.build_packet(
        components,
        reasons,
        "2026-08-21",
        "morning",
        "2026-08-21T02:10:00Z",
        UNIFIED.CONTRACT,
    )


def freshness(rows=None, observed_at="2026-08-21T02:11:00Z"):
    if rows is None:
        rows = [
            FRESHNESS.quote(
                "US:XNAS:TSM",
                "US",
                "2026-08-21T02:10:30Z",
                "2026-08-21T02:10:35Z",
            )
        ]
    return FRESHNESS.MODULE.evaluate_freshness(
        FRESHNESS.batch(rows, observed_at), FRESHNESS.policy(), FRESHNESS.CONTRACT
    )


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class EntryExitTriggerEligibilityTests(unittest.TestCase):
    def test_contract_is_structural_only_and_closes_all_execution_authority(self):
        self.assertEqual(CONTRACT["repository_default_trigger_policy"], "ABSENT")
        self.assertEqual(CONTRACT["trigger_kinds"], ["ENTRY", "EXIT"])
        self.assertTrue(CONTRACT["authority"]["structural_eligibility_audit_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "structural_eligibility_audit_only":
                self.assertFalse(value, key)

    def test_ready_present_signal_and_fresh_quote_remain_not_evaluated(self):
        packet = MODULE.build_packet(
            unified(), freshness(), "2026-08-21T02:12:00Z", CONTRACT
        )
        row = packet["subjects"][0]
        self.assertEqual(row["ready_status"], "READY")
        self.assertEqual(row["signal_status"], "PRESENT")
        self.assertEqual(row["intraday_freshness_status"], "FRESH")
        for kind in ("entry", "exit"):
            self.assertEqual(row[kind]["evaluation_status"], "NOT_EVALUATED")
            self.assertIsNone(row[kind]["eligible"])
            self.assertIsNone(row[kind]["trigger"])
        self.assertIn(
            "GENERIC_SIGNAL_KIND_UNRESOLVED", row["entry"]["blocking_reasons"]
        )
        self.assertIn("POSITION_STATE_NOT_AVAILABLE", row["exit"]["blocking_reasons"])
        self.assertIsNone(row["action"])
        self.assertIsNone(row["position_size"])
        self.assertIsNone(row["order_intent"])
        self.assertEqual(packet["summary"]["entry_eligible_count"], 0)
        self.assertEqual(packet["summary"]["exit_eligible_count"], 0)

    def test_stale_missing_and_wrong_market_quotes_are_explicit(self):
        stale = FRESHNESS.quote(
            "US:XNAS:TSM",
            "US",
            "2026-08-21T02:09:00Z",
            "2026-08-21T02:09:05Z",
        )
        row = MODULE.build_packet(
            unified(), freshness([stale]), "2026-08-21T02:12:00Z", CONTRACT
        )["subjects"][0]
        self.assertIn("INTRADAY_QUOTE_STALE", row["entry"]["blocking_reasons"])

        missing = MODULE.build_packet(
            unified(), freshness([]), "2026-08-21T02:12:00Z", CONTRACT
        )["subjects"][0]
        self.assertIn(
            "INTRADAY_QUOTE_UNAVAILABLE", missing["entry"]["blocking_reasons"]
        )
        self.assertIsNone(missing["fresh_for_intraday_consumption"])

        wrong_market = FRESHNESS.quote(
            "US:XNAS:TSM",
            "KOREA",
            "2026-08-21T02:10:30Z",
            "2026-08-21T02:10:35Z",
        )
        mismatch = MODULE.build_packet(
            unified(), freshness([wrong_market]), "2026-08-21T02:12:00Z", CONTRACT
        )["subjects"][0]
        self.assertIn("QUOTE_MARKET_MISMATCH", mismatch["entry"]["blocking_reasons"])

    def test_unavailable_action_boundary_is_not_zero_subject_success(self):
        packet = MODULE.build_packet(
            unified(False), freshness(), "2026-08-21T02:12:00Z", CONTRACT
        )
        self.assertEqual(
            packet["status"],
            "TRIGGER_ELIGIBILITY_UNAVAILABLE_ACTION_BOUNDARY_MISSING",
        )
        self.assertEqual(packet["subjects"], [])
        self.assertIn(
            "ACTION_BOUNDARY_SOURCE_UNAVAILABLE", packet["unresolved_boundaries"]
        )
        self.assertIsNone(packet["lineage"]["action_boundary_packet_sha256"])

    def test_source_validation_and_temporal_order_fail_closed(self):
        tampered = freshness()
        tampered["results"][0]["action"] = {"type": "BUY"}
        tampered["packet_sha256"] = FRESHNESS.MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.EntryExitTriggerEligibilityError,
            "SOURCE_VALIDATION_FAILED:INTRADAY_FRESHNESS",
        ):
            MODULE.build_packet(
                unified(), tampered, "2026-08-21T02:12:00Z", CONTRACT
            )

        with self.assertRaisesRegex(
            MODULE.EntryExitTriggerEligibilityError,
            "INTRADAY_FRESHNESS_FROM_FUTURE",
        ):
            MODULE.build_packet(
                unified(), freshness(), "2026-08-21T02:10:30Z", CONTRACT
            )

    def test_contract_and_output_authority_tamper_fail_closed(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["entry_eligibility_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.EntryExitTriggerEligibilityError,
            "CONTRACT_FIELD_MISMATCH:authority",
        ):
            MODULE.build_packet(
                unified(), freshness(), "2026-08-21T02:12:00Z", contract
            )

        packet = MODULE.build_packet(
            unified(), freshness(), "2026-08-21T02:12:00Z", CONTRACT
        )
        packet["subjects"][0]["entry"]["eligible"] = True
        packet["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in packet.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.EntryExitTriggerEligibilityError, "OUTPUT_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_full_source_sha_lineage_is_preserved(self):
        source_unified = unified()
        source_freshness = freshness()
        packet = MODULE.build_packet(
            source_unified,
            source_freshness,
            "2026-08-21T02:12:00Z",
            CONTRACT,
        )
        self.assertEqual(packet["source_packets"]["UNIFIED_DECISION"], source_unified)
        self.assertEqual(
            packet["source_packets"]["INTRADAY_FRESHNESS"], source_freshness
        )
        self.assertEqual(
            packet["lineage"]["unified_decision_packet_sha256"],
            source_unified["packet_sha256"],
        )
        self.assertEqual(
            packet["lineage"]["intraday_freshness_packet_sha256"],
            source_freshness["packet_sha256"],
        )

    def test_deterministic_and_inputs_immutable(self):
        source_unified = unified()
        source_freshness = freshness()
        before = MODULE.canonical_json([source_unified, source_freshness])
        first = MODULE.build_packet(
            source_unified,
            source_freshness,
            "2026-08-21T02:12:00Z",
            CONTRACT,
        )
        second = MODULE.build_packet(
            source_unified,
            source_freshness,
            "2026-08-21T02:12:00Z",
            CONTRACT,
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json([source_unified, source_freshness]), before)

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
            unified_path = write_json(temp / "unified.json", unified())
            freshness_path = write_json(temp / "freshness.json", freshness())
            output = temp / "nested" / "eligibility.json"
            self.assertEqual(
                MODULE.run(
                    unified_path,
                    freshness_path,
                    "2026-08-21T02:12:00Z",
                    output,
                ),
                0,
            )
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "entry_exit_trigger_eligibility_test.json"
            self.assertEqual(
                MODULE.run(
                    unified_path,
                    freshness_path,
                    "2026-08-21T02:12:00Z",
                    forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
