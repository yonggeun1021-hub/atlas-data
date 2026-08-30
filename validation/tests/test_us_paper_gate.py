#!/usr/bin/env python3
"""US PAPER market-isolated lifecycle Gate regression."""
from __future__ import annotations

import ast
import contextlib
import copy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "shadow/us_paper_gate.py"
SPEC = importlib.util.spec_from_file_location("us_paper_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

COMMON, MARKET = MODULE.load_contracts()
INPUT_PATH = ROOT / "evidence/us_paper_gate/2026-08-31/evidence_input.json"
ASSESSMENT_PATH = ROOT / "evidence/us_paper_gate/2026-08-31/assessment.json"
DOC_PATH = ROOT / "docs/us_paper_gate_contract.md"
WORKFLOW_PATH = ROOT / ".github/workflows/us-paper-gate.yml"


def current_input() -> dict:
    return json.loads(INPUT_PATH.read_text(encoding="utf-8"))


def set_rows(rows: dict, check_ids: list[str], status: str) -> None:
    for check_id in check_ids:
        row = rows[check_id]
        row["status"] = status
        row["evidence_refs"] = [f"evidence/test/{check_id}.json"]
        row["approval_refs"] = (
            [f"approval/test/{check_id}.json"] if status == "PASS" else []
        )


def set_all_common_pass(value: dict) -> None:
    set_rows(
        value["common_safety_checks"],
        list(value["common_safety_checks"]),
        "PASS",
    )


def gate_checks(gate_id: str) -> list[str]:
    return next(
        gate["required_checks"] for gate in MARKET["gates"] if gate["id"] == gate_id
    )


class CurrentAssessmentTests(unittest.TestCase):
    def test_current_evidence_is_locked_by_unknown_common_runtime_evidence(self):
        result = MODULE.evaluate(current_input(), COMMON, MARKET)

        self.assertEqual(result["current_state"], "LOCKED")
        self.assertEqual(result["paper_substage"], "NONE")
        self.assertEqual(result["next_gate"], "COMMON_SAFETY")
        statuses = {row["gate_id"]: row["status"] for row in result["gate_results"]}
        own = {row["gate_id"]: row["own_status"] for row in result["gate_results"]}
        self.assertEqual(statuses["COMMON_SAFETY"], "UNKNOWN")
        self.assertEqual(statuses["US_SHADOW"], "UNKNOWN")
        self.assertEqual(own["US_SHADOW"], "PASS")
        self.assertEqual(statuses["US_PAPER_CANARY_START"], "FAIL")
        self.assertIn(
            "COMMON_KILL_SWITCH_FAIL_CLOSED_EVIDENCE_UNKNOWN",
            result["blocking_reasons"],
        )

    def test_current_boundary_is_paper_only_broker_post_zero_and_real_false(self):
        result = MODULE.evaluate(current_input(), COMMON, MARKET)
        authority = result["authority"]

        self.assertTrue(authority["paper_only"])
        self.assertFalse(authority["broker_post_authorized"])
        self.assertEqual(authority["broker_post_count"], 0)
        for key in (
            "real_capital_authorized",
            "live_account_order_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertFalse(authority[key])
        self.assertEqual(
            result["internal_virtual_us_paper_policy"],
            {
                "humanApprovalRequired": False,
                "userReceiptRequired": False,
                "hardGateNullPolicy": "FAIL_CLOSED",
                "automaticTransitionRequiresEveryHardGatePass": True,
                "brokerPostCount": 0,
                "realCapitalAuthorized": False,
                "liveAccountAuthorized": False,
            },
        )

    def test_committed_assessment_is_exactly_rederived(self):
        assessment = json.loads(ASSESSMENT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            MODULE.validate_assessment(assessment, current_input(), COMMON, MARKET),
            assessment,
        )

    def test_source_revisions_are_exact_baseline_heads(self):
        revisions = MODULE.evaluate(current_input(), COMMON, MARKET)["source_revisions"]
        self.assertEqual(
            revisions,
            {
                "atlas_data_main": "7e6021fcb866027b3b6caa28405dd0d9b3e90875",
                "atlas_private_evidence_main": "6b28629beab066912c71c572c303bd51d581f893",
            },
        )


class StateTransitionTests(unittest.TestCase):
    def test_common_safety_fail_always_locks_us(self):
        value = current_input()
        value["common_safety_checks"]["COMMON_KILL_SWITCH_FAIL_CLOSED"] = {
            "status": "FAIL",
            "evidence_refs": ["evidence/test/kill-failed.json"],
            "approval_refs": [],
            "note": "fixture",
        }
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "LOCKED")
        self.assertEqual(result["gate_results"][0]["status"], "FAIL")
        self.assertFalse(result["authority"]["internal_paper_ledger_authorized"])

    def test_canary_start_is_separate_from_active_and_30_days(self):
        value = current_input()
        set_all_common_pass(value)
        set_rows(value["us_checks"], gate_checks("US_PAPER_CANARY_START"), "PASS")
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "PAPER_CANARY")
        self.assertEqual(result["paper_substage"], "BOUNDED_INTERNAL_US_PAPER_LEDGER")
        self.assertTrue(result["authority"]["internal_paper_ledger_authorized"])
        self.assertFalse(result["authority"]["scheduled_internal_paper_authorized"])
        self.assertFalse(result["authority"]["broker_post_authorized"])
        validated = next(
            row
            for row in result["gate_results"]
            if row["gate_id"] == "US_PAPER_VALIDATED_30D"
        )
        self.assertEqual(validated["own_status"], "FAIL")

    def test_active_authorizes_only_scheduled_internal_paper(self):
        value = current_input()
        set_all_common_pass(value)
        set_rows(value["us_checks"], gate_checks("US_PAPER_CANARY_START"), "PASS")
        set_rows(value["us_checks"], gate_checks("US_PAPER_ACTIVE"), "PASS")
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "PAPER_ACTIVE")
        self.assertTrue(result["authority"]["scheduled_internal_paper_authorized"])
        self.assertFalse(result["authority"]["broker_post_authorized"])
        self.assertEqual(result["authority"]["broker_post_count"], 0)

    def test_all_gates_pass_only_to_live_review_never_live_authority(self):
        value = current_input()
        set_all_common_pass(value)
        set_rows(value["us_checks"], list(value["us_checks"]), "PASS")
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "LIVE_REVIEW")
        self.assertIsNone(result["next_gate"])
        self.assertTrue(all(row["status"] == "PASS" for row in result["gate_results"]))
        self.assertFalse(result["authority"]["broker_post_authorized"])
        self.assertEqual(result["authority"]["broker_post_count"], 0)
        for key in (
            "real_capital_authorized",
            "live_account_order_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertFalse(result["authority"][key])

    def test_krx_and_crypto_context_cannot_change_us_state(self):
        baseline = current_input()
        first = MODULE.evaluate(baseline, COMMON, MARKET)
        changed = copy.deepcopy(baseline)
        changed["other_market_context"] = {"KOREA": "PASS", "CRYPTO": "FAIL"}
        second = MODULE.evaluate(changed, COMMON, MARKET)

        self.assertEqual(first["current_state"], second["current_state"])
        self.assertEqual(first["next_gate"], second["next_gate"])
        self.assertNotEqual(first["evidence_input_sha256"], second["evidence_input_sha256"])
        self.assertTrue(second["cross_market_isolation_applied"])


class TamperAndContractTests(unittest.TestCase):
    def test_self_rehashed_assessment_tamper_is_rejected(self):
        evidence = current_input()
        assessment = MODULE.evaluate(evidence, COMMON, MARKET)
        assessment["current_state"] = "PAPER_ACTIVE"
        assessment["authority"]["broker_post_authorized"] = True
        assessment["assessment_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in assessment.items() if key != "assessment_sha256"}
        )

        with self.assertRaisesRegex(
            MODULE.UsPaperGateError,
            "ASSESSMENT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_assessment(assessment, evidence, COMMON, MARKET)

    def test_market_contract_cannot_enable_broker_post(self):
        changed = copy.deepcopy(MARKET)
        changed["authority_by_state"]["PAPER_ACTIVE"]["broker_post_authorized"] = True
        with self.assertRaisesRegex(MODULE.UsPaperGateError, "MARKET_CONTRACT_INVALID"):
            MODULE.validate_market_contract(changed)

    def test_market_contract_cannot_enable_real_capital(self):
        changed = copy.deepcopy(MARKET)
        changed["permanent_authority_boundary"]["real_capital_authorized"] = True
        with self.assertRaisesRegex(MODULE.UsPaperGateError, "MARKET_CONTRACT_INVALID"):
            MODULE.validate_market_contract(changed)

    def test_ci_cannot_be_reclassified_as_operational_approval(self):
        changed = copy.deepcopy(MARKET)
        changed["ci_semantics"]["ci_may_advance_operational_state"] = True
        with self.assertRaisesRegex(MODULE.UsPaperGateError, "MARKET_CONTRACT_INVALID"):
            MODULE.validate_market_contract(changed)

    def test_internal_virtual_paper_cannot_require_human_or_user_receipt(self):
        changed = copy.deepcopy(MARKET)
        changed["internal_virtual_us_paper_policy"]["humanApprovalRequired"] = True
        with self.assertRaisesRegex(MODULE.UsPaperGateError, "MARKET_CONTRACT_INVALID"):
            MODULE.validate_market_contract(changed)

        changed = copy.deepcopy(MARKET)
        changed["internal_virtual_us_paper_policy"]["userReceiptRequired"] = True
        with self.assertRaisesRegex(MODULE.UsPaperGateError, "MARKET_CONTRACT_INVALID"):
            MODULE.validate_market_contract(changed)

    def test_hard_gate_null_is_rejected_fail_closed(self):
        changed = current_input()
        changed["us_checks"]["US_ENTRY_POLICY_RATIFIED"]["status"] = None
        with self.assertRaisesRegex(MODULE.UsPaperGateError, "EVIDENCE_INPUT_INVALID"):
            MODULE.evaluate(changed, COMMON, MARKET)

    def test_contracts_are_separate_complete_and_market_isolated(self):
        common_ids = {row["id"] for row in COMMON["checks"]}
        market_ids = {row["id"] for row in MARKET["check_definitions"]}

        self.assertTrue(common_ids.isdisjoint(market_ids))
        self.assertEqual(
            {check for gate in MARKET["gates"] for check in gate["required_checks"]},
            market_ids,
        )
        self.assertTrue(
            MARKET["cross_market_isolation"][
                "other_market_gate_results_are_diagnostic_only"
            ]
        )

    def test_public_evaluator_has_no_network_or_broker_client_import(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        imports: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        self.assertTrue(imports.isdisjoint({"requests", "httpx", "urllib", "socket"}))

    def test_docs_and_focused_workflow_preserve_ci_nonauthority(self):
        doc = DOC_PATH.read_text(encoding="utf-8")
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        self.assertIn("전체 저장소 CI는 회귀검사일 뿐 운용승인이 아니다", doc)
        self.assertIn("python3 validation/tests/test_us_paper_gate.py", workflow)
        self.assertIn("github.event.pull_request.head.sha || github.sha", workflow)
        self.assertNotIn("pull_request_target", workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertNotIn("actions/checkout@", workflow)
        self.assertNotIn("actions/setup-python@", workflow)

    def test_cli_evaluate_and_validate_in_temporary_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "assessment.json"
            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = MODULE.main(["evaluate", str(INPUT_PATH), "--out", str(output)])
            self.assertEqual(evaluate_exit, 0)
            with contextlib.redirect_stdout(io.StringIO()):
                validate_exit = MODULE.main(
                    ["validate", str(output), "--evidence-input", str(INPUT_PATH)]
                )
            self.assertEqual(validate_exit, 0)


if __name__ == "__main__":
    unittest.main()
