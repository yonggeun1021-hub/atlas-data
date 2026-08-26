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
from decision import shadow_entry_review as shadow
from replay.opportunity_trigger import payload_sha256


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            _contains_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


class EntryPolicyReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(readiness.DEFAULT_REPORT.read_text())
        cls.identity = json.loads(readiness.DEFAULT_IDENTITY.read_text())
        cls.shadow_contract = json.loads(
            readiness.DEFAULT_SHADOW_CONTRACT.read_text()
        )
        cls.shadow_packet = json.loads(readiness.DEFAULT_SHADOW_PACKET.read_text())
        cls.contract = readiness.load_contract()
        cls.trigger_kind = cls.shadow_packet["source"]["trigger_kind"]
        cls.packet = readiness.build_packet(
            cls.contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            trigger_kind=cls.trigger_kind,
        )

    def _build(self, **overrides):
        values = {
            "contract": self.contract,
            "shadow_packet": self.shadow_packet,
            "report": self.report,
            "identity_packet": self.identity,
            "shadow_contract": self.shadow_contract,
            "trigger_kind": self.trigger_kind,
        }
        values.update(overrides)
        return readiness.build_packet(**values)

    def test_real_population_is_preserved_but_nothing_is_executable(self):
        self.assertEqual(69, self.packet["summary"]["candidate_count"])
        self.assertEqual(3, self.packet["summary"]["diagnostic_reviewable_count"])
        self.assertEqual(1, self.packet["summary"]["probe_review_diagnostic_count"])
        self.assertEqual(0, self.packet["summary"]["execution_eligible_count"])
        self.assertEqual(0, self.packet["summary"]["entry_proposal_count"])
        self.assertEqual(0, self.packet["summary"]["order_intent_count"])

    def test_real_review_states_do_not_become_portfolio_states(self):
        rows = {row["subject"]: row for row in self.packet["candidates"]}
        self.assertEqual("PROBE_REVIEW", rows["005930"]["diagnostic_participation_state"])
        self.assertEqual("RADAR", rows["BTC"]["diagnostic_participation_state"])
        self.assertEqual("RADAR", rows["000660"]["diagnostic_participation_state"])
        for row in rows.values():
            self.assertEqual("LOCKED_POLICY_UNRATIFIED", row["execution_status"])
            self.assertEqual("LOCKED_NOT_STARTED", row["p8_13_entry_proposal"])

    def test_every_candidate_keeps_zero_money_and_all_authority_false(self):
        self.assertEqual(readiness.AUTHORITY_ALL_FALSE, self.packet["authority"])
        for row in self.packet["candidates"]:
            self.assertEqual(0, row["capital"])
            self.assertIsNone(row["trade_proposal"])
            self.assertIsNone(row["quantity"])
            self.assertEqual("NONE", row["action"])
            self.assertEqual(readiness.AUTHORITY_ALL_FALSE, row["authority"])

    def test_four_policy_axes_are_explicit_and_have_no_parameters(self):
        self.assertEqual(readiness.EXPECTED_AXES, self.packet["policy_axes"])
        sizing = self.packet["policy_axes"]["position_size"]
        self.assertIsNone(sizing["risk_budget_pct"])
        self.assertIsNone(sizing["stop_distance_pct"])
        self.assertIsNone(sizing["max_loss"])
        self.assertIsNone(sizing["quantity"])

    def test_downstream_decision_is_locked_not_a_recommendation(self):
        decision = self.packet["decision"]
        self.assertEqual("LOCKED_POLICY_UNRATIFIED", decision["status"])
        self.assertEqual("LOCKED_NOT_STARTED", decision["p8_13_entry_proposal"])
        self.assertEqual("NONE", decision["action"])
        self.assertIsNone(decision["trade_proposal"])
        self.assertEqual(0, decision["capital"])

    def test_operational_packet_contains_no_post_hoc_outcomes(self):
        self.assertFalse(_contains_key(self.packet, {
            "forward_return",
            "forward_return_pct",
            "mfe",
            "mae",
            "post_hoc_audit_note",
            "reference_forward_metrics",
            "recommended_quantity",
        }))

    def test_contract_rejects_invented_risk_budget(self):
        contract = copy.deepcopy(self.contract)
        contract["policy_axes"]["position_size"]["risk_budget_pct"] = 0.5
        with self.assertRaisesRegex(
            readiness.EntryPolicyReadinessError,
            "POLICY_AXES_DRIFT|NUMERIC_POLICY_PARAMETER_FORBIDDEN",
        ):
            readiness.validate_contract(contract)

    def test_contract_rejects_invented_stop_distance(self):
        contract = copy.deepcopy(self.contract)
        contract["policy_axes"]["position_size"]["stop_distance_pct"] = 7
        with self.assertRaisesRegex(
            readiness.EntryPolicyReadinessError,
            "POLICY_AXES_DRIFT|NUMERIC_POLICY_PARAMETER_FORBIDDEN",
        ):
            readiness.validate_contract(contract)

    def test_contract_cannot_make_probe_review_executable(self):
        contract = copy.deepcopy(self.contract)
        contract["diagnostic_participation"]["executable_states"] = ["PROBE_REVIEW"]
        with self.assertRaisesRegex(
            readiness.EntryPolicyReadinessError,
            "DIAGNOSTIC_PARTICIPATION_DRIFT",
        ):
            readiness.validate_contract(contract)

    def test_contract_cannot_open_p8_13(self):
        contract = copy.deepcopy(self.contract)
        contract["downstream_boundary"]["p8_13_entry_proposal"] = "OPEN"
        with self.assertRaisesRegex(
            readiness.EntryPolicyReadinessError,
            "DOWNSTREAM_BOUNDARY_DRIFT",
        ):
            readiness.validate_contract(contract)

    def test_contract_cannot_turn_on_buy_or_trading_authority(self):
        for field in ("buy_authority", "trading_authority"):
            contract = copy.deepcopy(self.contract)
            contract["authority"][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                readiness.EntryPolicyReadinessError,
                "CONTRACT_AUTHORITY_ESCALATION",
            ):
                readiness.validate_contract(contract)

    def test_resigned_shadow_review_tamper_is_rejected(self):
        upstream = copy.deepcopy(self.shadow_packet)
        upstream["review_items"][0]["review_state"] = "MOMENTUM_PROBE_REVIEW"
        upstream["review_items"][0]["row_sha256"] = payload_sha256(
            {k: v for k, v in upstream["review_items"][0].items() if k != "row_sha256"}
        )
        upstream["packet_sha256"] = payload_sha256(
            {k: v for k, v in upstream.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            shadow.ShadowEntryReviewError,
            "SHADOW_ENTRY_REVIEW_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            self._build(shadow_packet=upstream)

    def test_resigned_readiness_tamper_is_rejected_by_semantic_rebuild(self):
        tampered = copy.deepcopy(self.packet)
        tampered["summary"]["execution_eligible_count"] = 1
        tampered["packet_sha256"] = payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        # The class setup already passed the real upstream semantic validator.
        # This test isolates the new boundary's independent output rebuild so
        # the operational workflow does not repeat expensive git-provenance
        # scans for every downstream mutation case.
        with mock.patch.object(
            shadow,
            "validate_packet",
            return_value=copy.deepcopy(self.shadow_packet),
        ):
            with self.assertRaisesRegex(
                readiness.EntryPolicyReadinessError,
                "ENTRY_POLICY_READINESS_SEMANTIC_TAMPER_OR_DRIFT",
            ):
                readiness.validate_packet(
                    tampered,
                    self.contract,
                    self.shadow_packet,
                    self.report,
                    self.identity,
                    self.shadow_contract,
                    trigger_kind=self.trigger_kind,
                )

    def test_history_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "latest.json"
            history = root / "history"
            first = readiness.write_outputs(
                self.packet, output=output, history_root=history
            )
            first_bytes = first.read_bytes()
            second = readiness.write_outputs(
                self.packet, output=output, history_root=history
            )
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(1, len(list(history.rglob("readiness-*.json"))))

    def test_packet_is_deterministic_and_validator_returns_exact_copy(self):
        with mock.patch.object(
            shadow,
            "validate_packet",
            return_value=copy.deepcopy(self.shadow_packet),
        ):
            rebuilt = self._build()
            self.assertEqual(self.packet, rebuilt)
            validated = readiness.validate_packet(
                self.packet,
                self.contract,
                self.shadow_packet,
                self.report,
                self.identity,
                self.shadow_contract,
                trigger_kind=self.trigger_kind,
            )
        self.assertEqual(self.packet, validated)
        self.assertIsNot(self.packet, validated)


if __name__ == "__main__":
    unittest.main()
