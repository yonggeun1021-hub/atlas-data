#!/usr/bin/env python3
"""P8-05 Rotation / Discovery briefing regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock import run_dynamic_clock
SOURCE = ROOT / "briefing" / "rotation_discovery.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("rotation_discovery_briefing", SOURCE)
ROTATION_FIXTURE = load_module(
    "rotation_discovery_rotation_fixture", ROOT / "test" / "test_rotation_state_ledger.py"
)
DISCOVERY_FIXTURE = load_module(
    "rotation_discovery_event_fixture", ROOT / "test" / "test_event_discovery_case.py"
)
CONTRACT = MODULE.load_contract()


def empty_ledger():
    return MODULE.ROTATION.empty_ledger()


def observed_ledger():
    packet = ROTATION_FIXTURE.us_packet()
    return MODULE.ROTATION.apply_rotation(
        packet, ROTATION_FIXTURE.policy_for(packet)
    )


def records():
    return [DISCOVERY_FIXTURE.d1_record()]


def bindings():
    return DISCOVERY_FIXTURE.bindings()


class RotationDiscoveryBriefingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        latest = run_dynamic_clock.run()
        cls.dynamic_report = run_dynamic_clock.run(
            decision_date=latest["report_asof_evidence_date"]
        )
        cls.dynamic_generated_at = f"{cls.dynamic_report['decision_date']}T23:59:59Z"

    def test_contract_is_read_model_only_and_closes_promotion_action_authority(self):
        self.assertTrue(CONTRACT["authority"]["briefing_read_model_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "briefing_read_model_only":
                self.assertFalse(value, key)

    def test_empty_rotation_and_unresolved_case_are_explicit_not_promoted(self):
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        self.assertEqual(result["rotation"]["ledger_status"], "EMPTY")
        self.assertEqual(result["rotation"]["latest_changes"], [])
        self.assertEqual(result["discovery"]["case_count"], 1)
        self.assertEqual(
            result["discovery"]["cases"][0]["evidence_status"],
            "EVIDENCE_UNRESOLVED",
        )
        self.assertEqual(result["discovery"]["new_candidates"], [])
        self.assertEqual(result["discovery"]["existing_candidate_changes"], [])

    def test_observed_rotation_latest_change_is_preserved_without_ranking(self):
        ledger = observed_ledger()
        result = MODULE.build_briefing(
            ledger, records(), bindings(),
            "evening", "2026-08-21T02:00:00Z", CONTRACT,
        )
        self.assertEqual(
            result["rotation"]["latest_change_count"], len(ledger["records"])
        )
        first = result["rotation"]["latest_changes"][0]
        source = ledger["records"][0]
        self.assertEqual(first["current_state"], source["current_p2_state"])
        self.assertEqual(first["state_transition"], source["state_transition"])
        self.assertEqual(first["record_sha256"], source["record_sha256"])
        self.assertIsNone(result["summary"]["ranked_candidate"])
        self.assertIsNone(result["summary"]["action"])

    def test_explicit_discovery_evidence_lineage_is_preserved(self):
        proof = DISCOVERY_FIXTURE.evidence()
        binding_doc = DISCOVERY_FIXTURE.bindings(
            DISCOVERY_FIXTURE.binding(proof=proof)
        )
        result = MODULE.build_briefing(
            empty_ledger(), records(), binding_doc,
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        case = result["discovery"]["cases"][0]
        self.assertEqual(case["evidence_status"], "EVIDENCE_LINKED")
        self.assertEqual(case["evidence_lineage"]["source_sha256"], "a" * 64)
        self.assertEqual(case["promotion_status"], "PROMOTION_NOT_AUTHORIZED")
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["investment_action"])

    def test_real_dynamic_signals_are_visible_without_candidate_promotion(self):
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", self.dynamic_generated_at, CONTRACT,
            dynamic_report=self.dynamic_report,
        )
        signal = result["signal_observations"]
        expected = sum(
            len(market["review_queue"])
            for market in self.dynamic_report["by_market"].values()
        )
        self.assertGreater(expected, 0)
        self.assertEqual(signal["observation_count"], expected)
        self.assertEqual(result["summary"]["signal_observation_count"], expected)
        self.assertEqual(result["summary"]["new_candidate_count"], 0)
        self.assertEqual(result["summary"]["ready_count"], 0)
        self.assertEqual(result["summary"]["entry_trigger_count"], 0)
        self.assertEqual(result["discovery"]["new_candidates"], [])
        self.assertTrue(all(
            row["ready_status"] == "NOT_EVALUATED"
            and row["promotion_status"] == "PROMOTION_NOT_AUTHORIZED"
            and row["action"] is None
            for row in signal["observations"]
        ))

    def test_signal_observation_tamper_and_resign_fails_closed(self):
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", self.dynamic_generated_at, CONTRACT,
            dynamic_report=self.dynamic_report,
        )
        result["signal_observations"]["observations"][0]["ready_status"] = "READY"
        result["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in result.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError,
            "BRIEFING_SIGNAL_ROW_VALUE_INVALID",
        ):
            MODULE.validate_briefing(result, CONTRACT)

    def test_tampered_rotation_ledger_and_invalid_discovery_fail_closed(self):
        ledger = observed_ledger()
        ledger["records"][0]["current_p2_state"] = "STRONG"
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "ROTATION_LEDGER_INVALID"
        ):
            MODULE.build_briefing(
                ledger, records(), bindings(),
                "morning", "2026-08-21T02:00:00Z", CONTRACT,
            )
        bad = records()
        bad[0]["taxonomy_version"] = "2.0"
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "DISCOVERY_INPUT_INVALID"
        ):
            MODULE.build_briefing(
                empty_ledger(), bad, bindings(),
                "morning", "2026-08-21T02:00:00Z", CONTRACT,
            )

    def test_future_rotation_and_discovery_evidence_fail_closed(self):
        ledger = observed_ledger()
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "ROTATION_FROM_FUTURE"
        ):
            MODULE.build_briefing(
                ledger, records(), bindings(),
                "morning", "2026-08-19T23:59:59Z", CONTRACT,
            )
        future_proof = DISCOVERY_FIXTURE.evidence(
            source_identity={
                **DISCOVERY_FIXTURE.evidence()["source_identity"],
                "retrieved_at_utc": "2026-08-22T00:00:00Z",
            }
        )
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "DISCOVERY_EVIDENCE_FROM_FUTURE"
        ):
            MODULE.build_briefing(
                empty_ledger(), records(),
                DISCOVERY_FIXTURE.bindings(DISCOVERY_FIXTURE.binding(proof=future_proof)),
                "morning", "2026-08-21T02:00:00Z", CONTRACT,
            )

    def test_output_is_deterministic_and_inputs_are_immutable(self):
        ledger = observed_ledger()
        source_records = records()
        source_bindings = bindings()
        before = MODULE.canonical_json([ledger, source_records, source_bindings])
        first = MODULE.build_briefing(
            ledger, source_records, source_bindings,
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        second = MODULE.build_briefing(
            ledger, list(reversed(source_records)), source_bindings,
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(
            MODULE.canonical_json([ledger, source_records, source_bindings]), before
        )

    def test_summary_authority_and_digest_tamper_fail_closed(self):
        original = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        variants = []
        summary = copy.deepcopy(original)
        summary["summary"]["new_candidate_count"] = 1
        variants.append((summary, "BRIEFING_SUMMARY_INVALID"))
        promoted = copy.deepcopy(original)
        promoted["discovery"]["new_candidates"] = ["SNDK"]
        variants.append((promoted, "BRIEFING_DISCOVERY_SUMMARY_INVALID"))
        state = copy.deepcopy(original)
        state["rotation"]["latest_changes"] = [{
            "market": "US", "scope_id": "SCOPE", "entity_id": "ENTITY",
            "as_of_date": "2026-08-20", "structural_bucket_transition": "TOP_TO_TOP",
            "prior_state": None, "current_state": "INVENTED",
            "state_transition": "UNINITIALIZED_TO_INVENTED",
            "record_sha256": "a" * 64, "source_packet_sha256": "b" * 64,
        }]
        state["rotation"]["latest_change_count"] = 1
        state["rotation"]["state_counts"] = {
            "EMERGING": 0, "STRONG": 0, "WEAKENING": 1
        }
        variants.append((state, "BRIEFING_ROTATION_ROW_VALUE_INVALID"))
        case = copy.deepcopy(original)
        case["discovery"]["cases"][0]["promotion_status"] = "PROMOTED"
        variants.append((case, "BRIEFING_DISCOVERY_CASE_VALUE_INVALID"))
        authority = copy.deepcopy(original)
        authority["authority"]["stage_promotion_authorized"] = True
        variants.append((authority, "BRIEFING_IDENTITY_INVALID"))
        digest = copy.deepcopy(original)
        digest["packet_sha256"] = "0" * 64
        variants.append((digest, "BRIEFING_SHA_MISMATCH"))
        for packet, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationDiscoveryBriefingError, error
            ):
                MODULE.validate_briefing(packet, CONTRACT)

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
            ledger_path = temp / "ledger.json"
            records_path = temp / "records.jsonl"
            bindings_path = temp / "bindings.json"
            ledger_path.write_text(json.dumps(empty_ledger()), encoding="utf-8")
            records_path.write_text(
                "\n".join(json.dumps(row) for row in records()) + "\n",
                encoding="utf-8",
            )
            bindings_path.write_text(json.dumps(bindings()), encoding="utf-8")
            output = temp / "out" / "briefing.json"
            self.assertEqual(
                MODULE.run(
                    ledger_path, records_path, bindings_path,
                    "morning", "2026-08-21T02:00:00Z", output,
                ),
                0,
            )
            forbidden = ROOT / "data" / "rotation_discovery_briefing_test.json"
            self.assertEqual(
                MODULE.run(
                    ledger_path, records_path, bindings_path,
                    "morning", "2026-08-21T02:00:00Z", forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
