#!/usr/bin/env python3
"""KRX PAPER market-isolated lifecycle gate regression."""
from __future__ import annotations

import copy
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "shadow/krx_paper_gate.py"
SPEC = importlib.util.spec_from_file_location("krx_paper_gate", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

COMMON, MARKET = MODULE.load_contracts()
INPUT_PATH = ROOT / "evidence/krx_paper_gate/2026-08-30/evidence_input.json"
ASSESSMENT_PATH = ROOT / "evidence/krx_paper_gate/2026-08-30/assessment.json"


def current_input() -> dict:
    return json.loads(INPUT_PATH.read_text(encoding="utf-8"))


def set_checks(value: dict, check_ids: list[str], status: str) -> None:
    for check_id in check_ids:
        row = value["krx_checks"][check_id]
        row["status"] = status
        row["evidence_refs"] = [f"evidence/test/{check_id}.json"]
        row["approval_refs"] = (
            [f"approval/test/{check_id}.json"] if status == "PASS" else []
        )


def set_common_pass(value: dict) -> None:
    for check_id, row in value["common_safety_checks"].items():
        row["status"] = "PASS"
        row["evidence_refs"] = [f"evidence/test/{check_id}.json"]
        row["approval_refs"] = [f"approval/test/{check_id}.json"]


def gate_checks(gate_id: str) -> list[str]:
    return next(
        gate["required_checks"] for gate in MARKET["gates"] if gate["id"] == gate_id
    )


class CurrentAssessmentTests(unittest.TestCase):
    def test_current_evidence_is_locked_by_unknown_common_compatibility(self):
        result = MODULE.evaluate(current_input(), COMMON, MARKET)

        self.assertEqual(result["current_state"], "LOCKED")
        self.assertEqual(result["paper_substage"], "NONE")
        self.assertEqual(result["next_gate"], "COMMON_SAFETY")
        statuses = {row["gate_id"]: row["status"] for row in result["gate_results"]}
        own = {row["gate_id"]: row["own_status"] for row in result["gate_results"]}
        self.assertEqual(statuses["COMMON_SAFETY"], "UNKNOWN")
        self.assertEqual(statuses["KRX_SHADOW"], "UNKNOWN")
        self.assertEqual(own["KRX_SHADOW"], "PASS")
        self.assertEqual(statuses["KRX_PAPER_CANARY_START"], "FAIL")
        self.assertIn(
            "COMMON_BROKER_PROTOCOL_AND_ACCOUNT_COMPATIBILITY_EVIDENCE_UNKNOWN",
            result["blocking_reasons"],
        )
        self.assertEqual(
            result["authority"],
            {
                "internal_virtual_ledger_paper_authorized": False,
                "kis_mock_account_auto_order_authorized": False,
                **MODULE.PERMANENT_AUTHORITY,
            },
        )

    def test_committed_assessment_is_exactly_rederived(self):
        assessment = json.loads(ASSESSMENT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(
            MODULE.validate_assessment(
                assessment,
                current_input(),
                COMMON,
                MARKET,
            ),
            assessment,
        )

    def test_crypto_failure_and_us_unknown_do_not_change_krx_result(self):
        baseline = current_input()
        first = MODULE.evaluate(baseline, COMMON, MARKET)
        changed = copy.deepcopy(baseline)
        changed["other_market_context"] = {"CRYPTO": "UNKNOWN", "US": "FAIL"}
        second = MODULE.evaluate(changed, COMMON, MARKET)

        self.assertEqual(first["current_state"], second["current_state"])
        self.assertEqual(first["next_gate"], second["next_gate"])
        self.assertNotEqual(
            first["evidence_input_sha256"],
            second["evidence_input_sha256"],
        )
        self.assertTrue(second["cross_market_isolation_applied"])


class StateTransitionTests(unittest.TestCase):
    def test_common_safety_fail_always_locks_krx(self):
        value = current_input()
        value["common_safety_checks"]["COMMON_KILL_SWITCH_FAIL_CLOSED"]["status"] = "FAIL"
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "LOCKED")
        self.assertEqual(result["gate_results"][0]["status"], "FAIL")
        self.assertFalse(result["authority"]["internal_virtual_ledger_paper_authorized"])
        self.assertFalse(result["authority"]["kis_mock_account_auto_order_authorized"])

    def test_canary_start_is_separate_from_30_natural_days(self):
        value = current_input()
        set_common_pass(value)
        set_checks(value, gate_checks("KRX_PAPER_CANARY_START"), "PASS")
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "PAPER_CANARY")
        self.assertEqual(result["paper_substage"], "INTERNAL_VIRTUAL_LEDGER_PAPER")
        self.assertTrue(result["authority"]["internal_virtual_ledger_paper_authorized"])
        self.assertFalse(result["authority"]["kis_mock_account_auto_order_authorized"])
        validated = next(
            row for row in result["gate_results"] if row["gate_id"] == "KRX_PAPER_VALIDATED_30D"
        )
        self.assertEqual(validated["own_status"], "FAIL")
        self.assertIn("KRX_30_NATURAL_CALENDAR_DAYS_NOT_COMPLETE", validated["reasons"])

    def test_kis_mock_auto_order_requires_separate_paper_active_gate(self):
        value = current_input()
        set_common_pass(value)
        set_checks(value, gate_checks("KRX_PAPER_CANARY_START"), "PASS")
        set_checks(value, gate_checks("KRX_PAPER_ACTIVE"), "PASS")
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "PAPER_ACTIVE")
        self.assertEqual(result["paper_substage"], "KIS_MOCK_ACCOUNT_AUTO_ORDER")
        self.assertTrue(result["authority"]["internal_virtual_ledger_paper_authorized"])
        self.assertTrue(result["authority"]["kis_mock_account_auto_order_authorized"])
        self.assertFalse(result["authority"]["real_capital_authorized"])

    def test_every_market_gate_passes_only_to_live_review_never_live_authority(self):
        value = current_input()
        set_common_pass(value)
        for row in MARKET["check_definitions"]:
            set_checks(value, [row["id"]], "PASS")
        result = MODULE.evaluate(value, COMMON, MARKET)

        self.assertEqual(result["current_state"], "LIVE_REVIEW")
        self.assertIsNone(result["next_gate"])
        self.assertTrue(
            all(row["status"] == "PASS" for row in result["gate_results"])
        )
        for authority in MODULE.PERMANENT_AUTHORITY:
            self.assertFalse(result["authority"][authority])


class TamperAndContractTests(unittest.TestCase):
    def test_self_rehashed_assessment_tamper_is_rejected(self):
        evidence = current_input()
        assessment = MODULE.evaluate(evidence, COMMON, MARKET)
        assessment["current_state"] = "PAPER_ACTIVE"
        assessment["authority"]["kis_mock_account_auto_order_authorized"] = True
        assessment["assessment_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in assessment.items() if k != "assessment_sha256"}
        )

        with self.assertRaisesRegex(
            MODULE.KrxPaperGateError,
            "ASSESSMENT_DERIVATION_MISMATCH",
        ):
            MODULE.validate_assessment(assessment, evidence, COMMON, MARKET)

    def test_common_and_market_contracts_are_separate_and_complete(self):
        common_ids = {row["id"] for row in COMMON["checks"]}
        market_ids = {row["id"] for row in MARKET["check_definitions"]}

        self.assertTrue(common_ids)
        self.assertTrue(market_ids)
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

    def test_market_contract_cannot_open_real_authority(self):
        changed = copy.deepcopy(MARKET)
        changed["permanent_authority_boundary"]["real_capital_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.KrxPaperGateError,
            "MARKET_CONTRACT_INVALID",
        ):
            MODULE.validate_market_contract(changed)

    def test_cli_evaluate_and_validate_in_temporary_output(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "assessment.json"
            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = MODULE.main(
                    ["evaluate", str(INPUT_PATH), "--out", str(output)]
                )
            self.assertEqual(evaluate_exit, 0)
            self.assertTrue(output.exists())
            with contextlib.redirect_stdout(io.StringIO()):
                validate_exit = MODULE.main(
                    [
                        "validate",
                        str(output),
                        "--evidence-input",
                        str(INPUT_PATH),
                    ]
                )
            self.assertEqual(validate_exit, 0)


if __name__ == "__main__":
    unittest.main()
