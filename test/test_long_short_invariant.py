#!/usr/bin/env python3
"""P6-04 Long FAIL != Short PASS invariant regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "long_short_invariant.py"
EVALUATOR_TEST = ROOT / "test" / "test_deterministic_rule_evaluator.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("long_short_invariant", SOURCE)
EVALUATOR_FIXTURE = load_module("long_short_evaluator_fixture", EVALUATOR_TEST)
CONTRACT = MODULE.load_contract()


def upstream_packet():
    return EVALUATOR_FIXTURE.MODULE.build_packet(
        EVALUATOR_FIXTURE.available_binding_packet(),
        EVALUATOR_FIXTURE.RULES,
        EVALUATOR_FIXTURE.CONTRACT,
    )


def refresh_packet(value):
    result = copy.deepcopy(value)
    result.pop("packet_sha256", None)
    result["packet_sha256"] = MODULE.payload_sha256(result)
    return result


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


class LongShortInvariantTests(unittest.TestCase):
    def test_contract_is_exact_and_has_no_short_or_trading_authority(self):
        self.assertEqual(CONTRACT["invariant"], "LONG_FAIL_NEVER_IMPLIES_SHORT_PASS")
        self.assertEqual(CONTRACT["accepted_long_results"], [
            "PASS", "FAIL", "UNKNOWN", "UNDEFINED",
        ])
        self.assertEqual(CONTRACT["derived_short_evaluation_status"], "NOT_EVALUATED")
        self.assertEqual(CONTRACT["independent_prerequisites"], [
            "HEDGE_INSTRUMENT_ELIGIBILITY_RATIFIED",
            "BEAR_HEDGE_RISK_BUDGET_RATIFIED",
            "INDEPENDENT_SHORT_RULE_EVALUATION",
        ])
        self.assertTrue(CONTRACT["authority"]["invariant_enforcement_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "invariant_enforcement_only":
                self.assertFalse(value, key)

    def test_long_fail_is_directly_classified_without_short_pass(self):
        result = MODULE.classify_long_result("FAIL", CONTRACT)
        self.assertEqual(result["long_result"], "FAIL")
        self.assertIsNone(result["short_result"])
        self.assertEqual(result["short_evaluation_status"], "NOT_EVALUATED")
        self.assertEqual(result["invariant_status"], "ENFORCED")
        self.assertIn("LONG_FAIL_DOES_NOT_IMPLY_SHORT_PASS", result["reasons"])

    def test_no_long_result_can_derive_any_short_result(self):
        for long_result in CONTRACT["accepted_long_results"]:
            with self.subTest(long_result=long_result):
                boundary = MODULE.classify_long_result(long_result, CONTRACT)
                self.assertIsNone(boundary["short_result"])
                with self.assertRaisesRegex(
                    MODULE.LongShortInvariantError,
                    "DERIVED_SHORT_RESULT_FORBIDDEN",
                ):
                    MODULE.assert_short_result_not_derived(long_result, "PASS")
                MODULE.assert_short_result_not_derived(long_result, None)

    def test_current_rule_evaluator_packet_creates_zero_short_results(self):
        source = upstream_packet()
        result = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(result["status"], "INVARIANT_ENFORCED_SHORT_NOT_EVALUATED")
        self.assertEqual(result["summary"]["total_rules"], 25)
        self.assertEqual(result["summary"]["short_results_created"], 0)
        self.assertEqual(result["summary"]["short_pass"], 0)
        self.assertEqual(result["summary"]["short_not_evaluated"], 25)
        self.assertEqual({row["short_result"] for row in result["rules"]}, {None})
        self.assertEqual(
            {row["short_evaluation_status"] for row in result["rules"]},
            {"NOT_EVALUATED"},
        )
        self.assertFalse(result["authority"]["short_pass_authorized"])
        self.assertFalse(result["authority"]["order_authorized"])

    def test_output_preserves_upstream_rule_and_packet_lineage(self):
        source = upstream_packet()
        result = MODULE.build_packet(source, CONTRACT)
        source_rows = {row["rule_id"]: row for row in source["rules"]}
        for row in result["rules"]:
            upstream = source_rows[row["rule_id"]]
            self.assertEqual(row["subject"], upstream["subject"])
            self.assertEqual(row["condition_text_sha256"], upstream["condition_text_sha256"])
            self.assertEqual(row["long_result"], upstream["result"])
        self.assertEqual(
            result["lineage"]["upstream_evaluator_packet_sha256"],
            source["packet_sha256"],
        )
        digest = result.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(result))

    def test_tampered_upstream_digest_fails_closed(self):
        source = upstream_packet()
        source["rules"][0]["subject"] = "TAMPERED"
        with self.assertRaisesRegex(
            MODULE.LongShortInvariantError,
            "UPSTREAM_PACKET_SHA_MISMATCH",
        ):
            MODULE.build_packet(source, CONTRACT)

    def test_rehashed_upstream_authority_expansion_fails_closed(self):
        source = upstream_packet()
        source["authority"]["pass_fail_authorized"] = True
        source = refresh_packet(source)
        with self.assertRaisesRegex(
            MODULE.LongShortInvariantError,
            "UPSTREAM_PACKET_IDENTITY_INVALID",
        ):
            MODULE.build_packet(source, CONTRACT)

    def test_pass_or_fail_smuggled_into_no_authority_packet_is_rejected(self):
        for smuggled in ("PASS", "FAIL"):
            with self.subTest(smuggled=smuggled):
                source = upstream_packet()
                row = source["rules"][0]
                old = row["result"]
                row["result"] = smuggled
                source["summary"][old] -= 1
                source["summary"][smuggled] += 1
                source = refresh_packet(source)
                with self.assertRaisesRegex(
                    MODULE.LongShortInvariantError,
                    "UPSTREAM_PASS_FAIL_WITHOUT_AUTHORITY",
                ):
                    MODULE.build_packet(source, CONTRACT)

    def test_duplicate_rule_and_summary_drift_fail_closed(self):
        duplicate = upstream_packet()
        duplicate["rules"][1]["rule_id"] = duplicate["rules"][0]["rule_id"]
        duplicate = refresh_packet(duplicate)
        with self.assertRaisesRegex(
            MODULE.LongShortInvariantError,
            "UPSTREAM_RULE_INVALID",
        ):
            MODULE.build_packet(duplicate, CONTRACT)

        summary = upstream_packet()
        summary["summary"]["UNKNOWN"] += 1
        summary = refresh_packet(summary)
        with self.assertRaisesRegex(
            MODULE.LongShortInvariantError,
            "UPSTREAM_SUMMARY_MISMATCH",
        ):
            MODULE.build_packet(summary, CONTRACT)

    def test_contract_tamper_fails_closed(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["short_pass_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.LongShortInvariantError,
            "CONTRACT_FIELD_MISMATCH:authority",
        ):
            MODULE.build_packet(upstream_packet(), contract)

    def test_output_is_deterministic_and_input_is_not_mutated(self):
        source = upstream_packet()
        before = MODULE.canonical_json(source)
        first = MODULE.build_packet(source, CONTRACT)
        second = MODULE.build_packet(source, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)

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
            source_path = write_json(tmp / "source.json", upstream_packet())
            output_path = tmp / "nested" / "boundary.json"
            self.assertEqual(MODULE.run(source_path, output_path), 0)
            self.assertEqual(
                json.loads(output_path.read_text(encoding="utf-8"))["summary"]["short_pass"],
                0,
            )
            self.assertEqual(list(output_path.parent.glob(".boundary.json.*")), [])

            forbidden = ROOT / "data" / "long_short_invariant_test.json"
            self.assertEqual(MODULE.run(source_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
