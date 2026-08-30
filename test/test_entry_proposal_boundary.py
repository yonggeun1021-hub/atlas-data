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
from decision import krx_paper_proposal_bridge as krx_bridge
from replay.opportunity_trigger import payload_sha256


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return bool(set(value) & forbidden) or any(
            _contains_key(child, forbidden) for child in value.values()
        )
    if isinstance(value, list):
        return any(_contains_key(child, forbidden) for child in value)
    return False


def _resign_krx_bridge(value: dict) -> dict:
    result = copy.deepcopy(value)
    result["packet_sha256"] = krx_bridge.payload_sha256(
        {key: item for key, item in result.items() if key != "packet_sha256"}
    )
    return result


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

    def test_real_population_becomes_current_review_materials_and_zero_proposals(self):
        summary = self.packet["summary"]
        candidate_count = self.readiness_packet["summary"]["candidate_count"]
        reviewable_count = self.readiness_packet["summary"]["diagnostic_reviewable_count"]
        self.assertEqual(candidate_count, summary["observed_candidate_count"])
        self.assertEqual(reviewable_count, summary["human_review_material_count"])
        self.assertEqual(
            candidate_count - reviewable_count, summary["observation_only_count"]
        )
        self.assertEqual(0, summary["actionable_proposal_count"])
        self.assertEqual(0, summary["entry_proposal_count"])
        self.assertEqual(0, summary["order_intent_count"])

    def test_every_review_material_is_non_executable(self):
        self.assertEqual(
            self.packet["summary"]["human_review_material_count"],
            len(self.packet["human_review_material"]),
        )
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


class KrxPaperProposalBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = krx_bridge.load_contract()
        cls.policy_packet = krx_bridge.load_policy_packet()
        cls.source = json.loads(
            krx_bridge.DEFAULT_INPUT_PATH.read_text(encoding="utf-8")
        )
        cls.packet = krx_bridge.build_packet(
            cls.source, cls.contract, cls.policy_packet
        )

    def _build(self, source: dict | None = None) -> dict:
        return krx_bridge.build_packet(
            source if source is not None else self.source,
            self.contract,
            self.policy_packet,
        )

    def test_merged_sources_and_unratified_policy_keep_none(self):
        proposal = self.packet["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertEqual("NONE", proposal["action"])
        self.assertEqual("ENTER", proposal["diagnostic_action"])
        self.assertNotIn("UNIVERSE_SOURCE_UNMERGED", proposal["blockers"])
        self.assertNotIn("SHADOW_SOURCE_UNMERGED", proposal["blockers"])
        self.assertIn("STRATEGY_POLICY_UNRATIFIED", proposal["blockers"])
        self.assertIn("COMMON_SAFETY_NOT_PASS", proposal["blockers"])
        self.assertIn("KRX_SHADOW_NOT_PASS", proposal["blockers"])
        self.assertIsNone(proposal["internal_virtual_ledger_draft"])
        self.assertIsNone(proposal["broker_order_draft"])

    def test_candidate_plan_binds_requested_facts_without_authority(self):
        proposal = self.packet["machine_proposal"]
        self.assertEqual("005930", proposal["symbol"])
        self.assertEqual("UNKNOWN", proposal["eligibility"]["status"])
        self.assertEqual(["15m", "1h", "1d"], list(proposal["completed_bars"]))
        self.assertTrue(
            all(row["completed"] for row in proposal["completed_bars"].values())
        )
        plan = proposal["candidate_plan_not_authority"]
        self.assertEqual(
            {"minimum_price_units": 100000, "maximum_price_units": 102000},
            plan["entry_zone"],
        )
        self.assertEqual(98000, plan["stop_price_units"])
        self.assertEqual(106000, plan["first_take_profit_price_units"])
        self.assertEqual(110000, plan["final_take_profit_price_units"])
        self.assertEqual(1000, plan["planned_loss_units"])
        self.assertEqual(10000, plan["account_risk_budget_units"])
        self.assertEqual(krx_bridge.AUTHORITY_ALL_FALSE, proposal["authority"])

    def test_human_and_machine_share_one_evidence_basis_hash(self):
        digest = krx_bridge.payload_sha256(self.packet["evidence_basis"])
        self.assertEqual(digest, self.packet["evidence_basis_sha256"])
        self.assertEqual(
            digest, self.packet["human_briefing"]["evidence_basis_sha256"]
        )
        self.assertEqual(
            digest, self.packet["machine_proposal"]["evidence_basis_sha256"]
        )
        self.assertEqual(
            self.packet["human_briefing"]["proposal_key"],
            self.packet["machine_proposal"]["proposal_key"],
        )

    def test_candidate_selection_and_order_authorities_remain_false(self):
        authority = self.packet["authority"]
        self.assertEqual(krx_bridge.AUTHORITY_ALL_FALSE, authority)
        for field in (
            "briefing_candidate_selection_authority",
            "kis_submission_compatible",
            "exchange_authority",
            "order_authority",
            "paper_order_write",
            "real_capital_authority",
            "production_authority",
            "trading_authority",
        ):
            self.assertFalse(authority[field])

    def test_policy_packet_has_no_invented_threshold_or_result(self):
        self.assertFalse(self.policy_packet["ratifies_strategy_policy"])
        self.assertTrue(
            all(value is None for value in self.policy_packet["decision_metrics"].values())
        )
        for row in self.policy_packet["required_evidence"].values():
            self.assertEqual({"status": "NOT_AVAILABLE", "result": None}, row)
        self.assertTrue(
            all(value is False for value in self.policy_packet["authority"].values())
        )

    def test_duplicate_decision_key_fails_closed(self):
        source = copy.deepcopy(self.source)
        source["prior_proposal_keys"] = [source["shadow"]["decision_key"]]
        proposal = self._build(_resign_krx_bridge(source))["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertIn("DUPLICATE_SHADOW_DECISION_KEY", proposal["blockers"])

    def test_stale_input_fails_closed(self):
        source = copy.deepcopy(self.source)
        source["position"]["valid_until_utc"] = source["evaluated_at_utc"]
        proposal = self._build(_resign_krx_bridge(source))["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertIn("POSITION_STALE", proposal["blockers"])

    def test_identity_mismatch_fails_closed(self):
        source = copy.deepcopy(self.source)
        source["position"]["symbol"] = "000660"
        proposal = self._build(_resign_krx_bridge(source))["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertIn("IDENTITY_SYMBOL_MISMATCH", proposal["blockers"])

    def test_exit_without_position_fails_closed(self):
        source = copy.deepcopy(self.source)
        source["shadow"]["diagnostic_action"] = "EXIT"
        proposal = self._build(_resign_krx_bridge(source))["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertIn("NO_POSITION_EXIT", proposal["blockers"])

    def test_enter_with_existing_position_fails_closed(self):
        source = copy.deepcopy(self.source)
        source["position"]["status"] = "OPEN"
        source["position"]["current_open_positions"] = 1
        proposal = self._build(_resign_krx_bridge(source))["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertIn("ENTER_REQUIRES_FLAT_POSITION", proposal["blockers"])
        self.assertIn("ENTER_REQUIRES_ZERO_OPEN_POSITIONS", proposal["blockers"])

    def test_incomplete_bar_fails_closed(self):
        source = copy.deepcopy(self.source)
        source["bars"]["15m"]["completed"] = False
        proposal = self._build(_resign_krx_bridge(source))["machine_proposal"]
        self.assertEqual("NONE", proposal["status"])
        self.assertIn("BAR_NOT_COMPLETED:15m", proposal["blockers"])

    def test_lookahead_bar_is_rejected_before_proposal(self):
        source = copy.deepcopy(self.source)
        source["bars"]["15m"]["available_at_utc"] = "2026-08-27T00:30:00Z"
        source["bars"]["15m"]["valid_until_utc"] = "2026-08-27T01:01:00Z"
        with self.assertRaisesRegex(
            krx_bridge.KrxPaperProposalBridgeError,
            "BAR_TIME_ORDER_INVALID:15m",
        ):
            self._build(_resign_krx_bridge(source))

    def test_planned_loss_over_remaining_budget_is_rejected(self):
        source = copy.deepcopy(self.source)
        source["policy"]["planned_loss_units"] = 10001
        with self.assertRaisesRegex(
            krx_bridge.KrxPaperProposalBridgeError,
            "PLANNED_LOSS_EXCEEDS_REMAINING_RISK_BUDGET",
        ):
            self._build(_resign_krx_bridge(source))

    def test_contract_cannot_promote_source_policy_or_authority(self):
        promoted_policy = copy.deepcopy(self.contract)
        promoted_policy["policy_boundary"]["ratified_policy_bindings"] = [
            {
                "policy_id": "KRX_MULTITIMEFRAME_BREAKOUT_CANDIDATE",
                "source_sha256": "5" * 64,
            }
        ]
        promoted_order = copy.deepcopy(self.contract)
        promoted_order["authority"]["order_authority"] = True
        moved_head = copy.deepcopy(self.contract)
        moved_head["source_requirements"]["shadow"]["exact_head"] = "0" * 40
        moved_merge = copy.deepcopy(self.contract)
        moved_merge["source_requirements"]["shadow"]["merge_commit"] = "0" * 40
        for contract, error in (
            (promoted_policy, "CONTRACT_POLICY_AUTHORITY_DRIFT"),
            (promoted_order, "CONTRACT_AUTHORITY_ESCALATION"),
            (moved_head, "CONTRACT_SOURCE_PIN_DRIFT"),
            (moved_merge, "CONTRACT_SOURCE_PIN_DRIFT"),
        ):
            with self.subTest(error=error), self.assertRaisesRegex(
                krx_bridge.KrxPaperProposalBridgeError, error
            ):
                krx_bridge.validate_contract(contract)

    def test_rehashed_policy_result_or_threshold_tamper_is_rejected(self):
        result = copy.deepcopy(self.policy_packet)
        result["required_evidence"]["up_regime"] = {
            "status": "PASS",
            "result": {"net": 1},
        }
        with self.assertRaisesRegex(
            krx_bridge.KrxPaperProposalBridgeError,
            "POLICY_PACKET_EVIDENCE_DRIFT",
        ):
            krx_bridge.validate_policy_packet(result)
        threshold = copy.deepcopy(self.policy_packet)
        threshold["decision_metrics"]["minimum_sample_size"] = 30
        with self.assertRaisesRegex(
            krx_bridge.KrxPaperProposalBridgeError,
            "POLICY_PACKET_THRESHOLD_INVENTED",
        ):
            krx_bridge.validate_policy_packet(threshold)

    def test_resigned_output_semantic_tamper_is_rejected(self):
        tampered = copy.deepcopy(self.packet)
        tampered["machine_proposal"]["action"] = "ENTER"
        tampered["machine_proposal"]["proposal_sha256"] = (
            krx_bridge.payload_sha256(
                {
                    key: value
                    for key, value in tampered["machine_proposal"].items()
                    if key != "proposal_sha256"
                }
            )
        )
        tampered["packet_sha256"] = krx_bridge.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            krx_bridge.KrxPaperProposalBridgeError,
            "PROPOSAL_PACKET_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            krx_bridge.validate_packet(
                tampered, self.source, self.contract, self.policy_packet
            )

    def test_build_is_deterministic_and_exactly_revalidated(self):
        rebuilt = self._build()
        self.assertEqual(self.packet, rebuilt)
        self.assertEqual(
            self.packet,
            krx_bridge.validate_packet(
                self.packet, self.source, self.contract, self.policy_packet
            ),
        )


if __name__ == "__main__":
    unittest.main()
