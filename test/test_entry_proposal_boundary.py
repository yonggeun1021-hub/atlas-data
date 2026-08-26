#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import entry_policy_readiness as readiness
from decision import entry_proposal_boundary as boundary
from replay.opportunity_trigger import payload_sha256


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            _contains_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


class EntryProposalBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(boundary.DEFAULT_REPORT.read_text())
        cls.identity = json.loads(boundary.DEFAULT_IDENTITY.read_text())
        cls.shadow_contract = json.loads(boundary.DEFAULT_SHADOW_CONTRACT.read_text())
        cls.shadow_packet = json.loads(boundary.DEFAULT_SHADOW_PACKET.read_text())
        cls.readiness_contract = json.loads(
            boundary.DEFAULT_READINESS_CONTRACT.read_text()
        )
        cls.readiness_packet = readiness.build_packet(
            cls.readiness_contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            trigger_kind=cls.shadow_packet["source"]["trigger_kind"],
        )
        cls.contract = boundary.load_contract()
        cls.trigger_kind = cls.readiness_packet["source"]["trigger_kind"]
        cls.packet = boundary.build_packet(
            cls.contract,
            cls.readiness_packet,
            cls.readiness_contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            trigger_kind=cls.trigger_kind,
        )

    def _build(self, **overrides):
        values = {
            "contract": self.contract,
            "readiness_packet": self.readiness_packet,
            "readiness_contract": self.readiness_contract,
            "shadow_packet": self.shadow_packet,
            "report": self.report,
            "identity_packet": self.identity,
            "shadow_contract": self.shadow_contract,
            "trigger_kind": self.trigger_kind,
        }
        values.update(overrides)
        return boundary.build_packet(**values)

    def test_real_population_becomes_three_review_materials_and_zero_proposals(self):
        summary = self.packet["summary"]
        self.assertEqual(69, summary["observed_candidate_count"])
        self.assertEqual(3, summary["human_review_material_count"])
        self.assertEqual(66, summary["observation_only_count"])
        self.assertEqual(0, summary["actionable_proposal_count"])
        self.assertEqual(0, summary["entry_proposal_count"])
        self.assertEqual(0, summary["order_intent_count"])

    def test_every_review_material_is_non_executable(self):
        self.assertEqual(3, len(self.packet["human_review_material"]))
        for row in self.packet["human_review_material"]:
            self.assertEqual("DIAGNOSTIC_REVIEW_MATERIAL_ONLY", row["material_status"])
            self.assertEqual("LOCKED_POLICY_UNRATIFIED", row["proposal_status"])
            self.assertEqual("IMPLEMENTED_FAIL_CLOSED", row["p8_13_boundary"])
            self.assertEqual("NONE", row["proposed_action"])
            for field in (
                "entry_zone", "invalidation", "risk_budget_pct", "max_loss",
                "position_size", "quantity", "trade_proposal", "order_intent",
            ):
                self.assertIsNone(row[field])
            self.assertEqual(0, row["capital"])
            self.assertEqual(boundary.AUTHORITY_ALL_FALSE, row["authority"])

    def test_decision_is_an_implemented_fail_closed_boundary(self):
        self.assertEqual(boundary.EXPECTED_PROPOSAL_BOUNDARY, self.packet["decision"])
        self.assertEqual(boundary.AUTHORITY_ALL_FALSE, self.packet["authority"])

    def test_only_upstream_diagnostic_reviewable_rows_are_selected(self):
        expected = {
            row["candidate_id"]
            for row in self.readiness_packet["candidates"]
            if row["diagnostic_reviewable"]
        }
        actual = {row["candidate_id"] for row in self.packet["human_review_material"]}
        self.assertEqual(expected, actual)

    def test_operational_packet_has_no_post_hoc_outcome_fields(self):
        self.assertFalse(_contains_key(self.packet, {
            "forward_return", "forward_return_pct", "mfe", "mae",
            "post_hoc_audit_note", "reference_forward_metrics",
            "recommended_quantity", "target_price", "expected_return",
        }))

    def test_contract_cannot_inject_entry_zone_or_risk_budget(self):
        for field, value in (("entry_zone", [100, 110]), ("risk_budget_pct", 1)):
            contract = copy.deepcopy(self.contract)
            contract["proposal_boundary"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(
                boundary.EntryProposalBoundaryError,
                "PROPOSAL_BOUNDARY_DRIFT",
            ):
                boundary.validate_contract(contract)

    def test_contract_cannot_authorize_a_proposal_or_trade(self):
        for field in ("proposal_draft_authorized", "buy_authority", "trading_authority"):
            contract = copy.deepcopy(self.contract)
            contract["authority"][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                boundary.EntryProposalBoundaryError,
                "CONTRACT_AUTHORITY_ESCALATION",
            ):
                boundary.validate_contract(contract)

    def test_contract_cannot_create_trade_or_order_objects(self):
        for field in ("trade_proposal", "order_intent"):
            contract = copy.deepcopy(self.contract)
            contract["proposal_boundary"][field] = {"side": "BUY"}
            with self.subTest(field=field), self.assertRaisesRegex(
                boundary.EntryProposalBoundaryError,
                "PROPOSAL_BOUNDARY_DRIFT",
            ):
                boundary.validate_contract(contract)

    def test_non_reviewable_row_cannot_be_selected(self):
        row = next(
            value for value in self.readiness_packet["candidates"]
            if not value["diagnostic_reviewable"]
        )
        with self.assertRaisesRegex(
            boundary.EntryProposalBoundaryError,
            "NON_REVIEWABLE_ROW_SELECTED",
        ):
            boundary._review_material(row)

    def test_upstream_resigned_execution_escalation_is_rejected(self):
        tampered = copy.deepcopy(self.readiness_packet)
        tampered["summary"]["execution_eligible_count"] = 1
        tampered["packet_sha256"] = payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            readiness.EntryPolicyReadinessError,
            "ENTRY_POLICY_READINESS_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            self._build(readiness_packet=tampered)

    def test_resigned_boundary_output_tamper_is_rejected(self):
        tampered = copy.deepcopy(self.packet)
        tampered["decision"]["proposed_action"] = "BUY"
        tampered["packet_sha256"] = payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with mock.patch.object(
            readiness,
            "validate_packet",
            return_value=copy.deepcopy(self.readiness_packet),
        ):
            with self.assertRaisesRegex(
                boundary.EntryProposalBoundaryError,
                "ENTRY_PROPOSAL_BOUNDARY_SEMANTIC_TAMPER_OR_DRIFT",
            ):
                boundary.validate_packet(
                    tampered,
                    self.contract,
                    self.readiness_packet,
                    self.readiness_contract,
                    self.shadow_packet,
                    self.report,
                    self.identity,
                    self.shadow_contract,
                    trigger_kind=self.trigger_kind,
                )

    def test_history_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            output = root / "latest.json"
            history = root / "history"
            first = boundary.write_outputs(self.packet, output=output, history_root=history)
            first_bytes = first.read_bytes()
            second = boundary.write_outputs(self.packet, output=output, history_root=history)
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(1, len(list(history.rglob("boundary-*.json"))))

    def test_packet_is_deterministic_and_validates_exactly(self):
        with mock.patch.object(
            readiness,
            "validate_packet",
            return_value=copy.deepcopy(self.readiness_packet),
        ):
            rebuilt = self._build()
            self.assertEqual(self.packet, rebuilt)
            self.assertEqual(
                self.packet,
                boundary.validate_packet(
                    self.packet,
                    self.contract,
                    self.readiness_packet,
                    self.readiness_contract,
                    self.shadow_packet,
                    self.report,
                    self.identity,
                    self.shadow_contract,
                    trigger_kind=self.trigger_kind,
                ),
            )


if __name__ == "__main__":
    unittest.main()
