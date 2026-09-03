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

from decision import entry_policy_readiness
from decision import entry_proposal_boundary
from portfolio import capital_reallocation_readiness as reallocation
from portfolio import profit_harvest_readiness as harvest
from replay.opportunity_trigger import payload_sha256


class CapitalReallocationReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(reallocation.DEFAULT_REPORT.read_text())
        cls.identity = json.loads(reallocation.DEFAULT_IDENTITY.read_text())
        cls.shadow_contract = json.loads(reallocation.DEFAULT_SHADOW_CONTRACT.read_text())
        cls.shadow_packet = json.loads(reallocation.DEFAULT_SHADOW_PACKET.read_text())
        cls.entry_readiness_contract = json.loads(
            reallocation.DEFAULT_ENTRY_READINESS_CONTRACT.read_text()
        )
        cls.entry_readiness_packet = entry_policy_readiness.build_packet(
            cls.entry_readiness_contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            trigger_kind=cls.shadow_packet["source"]["trigger_kind"],
        )
        cls.trigger_kind = cls.entry_readiness_packet["source"]["trigger_kind"]
        cls.entry_contract = json.loads(reallocation.DEFAULT_ENTRY_BOUNDARY_CONTRACT.read_text())
        cls.entry_packet = entry_proposal_boundary.build_packet(
            cls.entry_contract,
            cls.entry_readiness_packet,
            cls.entry_readiness_contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            trigger_kind=cls.trigger_kind,
        )
        cls.harvest_contract = json.loads(reallocation.DEFAULT_HARVEST_CONTRACT.read_text())
        cls.source_commit = harvest.current_source_commit()
        cls.harvest_readiness = harvest.build_readiness(
            cls.entry_packet,
            cls.entry_contract,
            cls.entry_readiness_packet,
            cls.entry_readiness_contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            source_commit=cls.source_commit,
            trigger_kind=cls.trigger_kind,
        )
        cls.harvest_packet = harvest.build_operational_packet(
            cls.harvest_readiness, cls.harvest_contract
        )
        cls.contract = reallocation.load_contract()
        cls.validation_inputs = {
            "entry_packet": cls.entry_packet,
            "entry_contract": cls.entry_contract,
            "readiness_packet": cls.entry_readiness_packet,
            "readiness_contract": cls.entry_readiness_contract,
            "shadow_packet": cls.shadow_packet,
            "report": cls.report,
            "identity_packet": cls.identity,
            "shadow_contract": cls.shadow_contract,
            "harvest_contract": cls.harvest_contract,
            "source_commit": cls.source_commit,
            "trigger_kind": cls.trigger_kind,
        }
        cls.packet = reallocation.build_packet(
            cls.contract, cls.harvest_packet, **cls.validation_inputs
        )

    def test_all_six_input_axes_are_explicitly_not_ready(self):
        self.assertEqual(reallocation.EXPECTED_INPUT_AXES, self.packet["input_axes"])
        self.assertEqual(6, len(self.packet["input_axes"]))

    def test_real_readiness_has_zero_proposals_and_orders(self):
        summary = self.packet["summary"]
        for field in summary:
            self.assertEqual(0, summary[field], field)

    def test_decision_has_no_amount_proceeds_or_action(self):
        self.assertEqual(reallocation.EXPECTED_PROPOSAL_BOUNDARY, self.packet["decision"])
        self.assertEqual(reallocation.AUTHORITY_ALL_FALSE, self.packet["authority"])

    def test_contract_cannot_inject_risk_budget_ranking_or_amount(self):
        mutations = (
            ("input_axes", "risk_budget", "RATIFIED"),
            ("input_axes", "allocation_ranking", "RATIFIED"),
            ("proposal_boundary", "add_amount", 100),
            ("proposal_boundary", "expected_proceeds", 50),
        )
        for section, field, value in mutations:
            contract = copy.deepcopy(self.contract)
            contract[section][field] = value
            with self.subTest(field=field), self.assertRaises(
                reallocation.CapitalReallocationReadinessError
            ):
                reallocation.validate_contract(contract)

    def test_contract_cannot_enable_any_authority(self):
        for field in reallocation.AUTHORITY_ALL_FALSE:
            if field == "review_only":
                continue
            contract = copy.deepcopy(self.contract)
            contract["authority"][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(
                reallocation.CapitalReallocationReadinessError,
                "CONTRACT_AUTHORITY_ESCALATION",
            ):
                reallocation.validate_contract(contract)

    def test_contract_rejects_numeric_boolean_aliases(self):
        mutations = (
            ("authority", "review_only", 1, "CONTRACT_AUTHORITY_ESCALATION"),
            ("authority", "buy_authority", 0, "CONTRACT_AUTHORITY_ESCALATION"),
            ("proposal_boundary", "capital", False, "PROPOSAL_BOUNDARY_DRIFT"),
            (
                "proposal_boundary",
                "settled_proceeds_available",
                0,
                "PROPOSAL_BOUNDARY_DRIFT",
            ),
        )
        for section, field, alias, error in mutations:
            contract = copy.deepcopy(self.contract)
            contract[section][field] = alias
            with self.subTest(section=section, field=field), self.assertRaisesRegex(
                reallocation.CapitalReallocationReadinessError,
                error,
            ):
                reallocation.validate_contract(contract)

    def test_validated_upstream_requires_type_exact_summary_and_authority(self):
        mutations = (
            (
                "summary",
                "harvest_proposal_count",
                False,
                "UPSTREAM_HARVEST_READINESS_CHANGED",
            ),
            ("authority", "buy_authority", 0, "UPSTREAM_AUTHORITY_ESCALATION"),
        )
        for section, field, alias, error in mutations:
            upstream = copy.deepcopy(self.harvest_packet)
            upstream[section][field] = alias
            with self.subTest(section=section, field=field), mock.patch.object(
                harvest,
                "validate_operational_packet",
                return_value=upstream,
            ), self.assertRaisesRegex(
                reallocation.CapitalReallocationReadinessError,
                error,
            ):
                reallocation.build_packet(
                    self.contract,
                    upstream,
                    **self.validation_inputs,
                )

    def test_resigned_upstream_harvest_proposal_is_rejected(self):
        tampered = copy.deepcopy(self.harvest_packet)
        tampered["summary"]["harvest_proposal_count"] = 1
        tampered["packet_sha256"] = payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            harvest.ProfitHarvestReadinessError,
            "PROFIT_HARVEST_OPERATIONAL_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            reallocation.build_packet(
                self.contract, tampered, **self.validation_inputs
            )

    def test_resigned_output_cannot_add_reallocation(self):
        tampered = copy.deepcopy(self.packet)
        tampered["decision"]["reallocation_proposal"] = {"to": "BTC"}
        tampered["packet_sha256"] = payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with mock.patch.object(
            harvest,
            "validate_operational_packet",
            return_value=copy.deepcopy(self.harvest_packet),
        ):
            with self.assertRaisesRegex(
                reallocation.CapitalReallocationReadinessError,
                "CAPITAL_REALLOCATION_READINESS_SEMANTIC_TAMPER_OR_DRIFT",
            ):
                reallocation.validate_packet(
                    tampered,
                    self.contract,
                    self.harvest_packet,
                    **self.validation_inputs,
                )

    def test_resigned_output_rejects_numeric_boolean_aliases(self):
        mutations = (
            ("decision", "capital", False),
            ("decision", "settled_proceeds_available", 0),
            ("summary", "reallocation_proposal_count", False),
            ("authority", "buy_authority", 0),
        )
        for section, field, alias in mutations:
            tampered = copy.deepcopy(self.packet)
            tampered[section][field] = alias
            tampered["packet_sha256"] = payload_sha256(
                {key: value for key, value in tampered.items() if key != "packet_sha256"}
            )
            with self.subTest(section=section, field=field), mock.patch.object(
                harvest,
                "validate_operational_packet",
                return_value=copy.deepcopy(self.harvest_packet),
            ), self.assertRaisesRegex(
                reallocation.CapitalReallocationReadinessError,
                "CAPITAL_REALLOCATION_READINESS_SEMANTIC_TAMPER_OR_DRIFT",
            ):
                reallocation.validate_packet(
                    tampered,
                    self.contract,
                    self.harvest_packet,
                    **self.validation_inputs,
                )

    def test_packet_contains_no_outcome_or_portfolio_amount_inputs(self):
        serialized = json.dumps(self.packet, sort_keys=True)
        for forbidden in (
            "forward_return", "mfe", "mae", "outcome_category", "cash_balance",
            "nav_amount", "position_quantity", "target_weight", "recommended_quantity",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_history_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            first = reallocation.write_outputs(
                self.packet, output=root / "latest.json", history_root=root / "history"
            )
            first_bytes = first.read_bytes()
            second = reallocation.write_outputs(
                self.packet, output=root / "latest.json", history_root=root / "history"
            )
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(1, len(list((root / "history").glob("readiness-*.json"))))

    def test_validator_returns_exact_copy(self):
        with mock.patch.object(
            harvest,
            "validate_operational_packet",
            return_value=copy.deepcopy(self.harvest_packet),
        ):
            self.assertEqual(
                self.packet,
                reallocation.validate_packet(
                    self.packet,
                    self.contract,
                    self.harvest_packet,
                    **self.validation_inputs,
                ),
            )


if __name__ == "__main__":
    unittest.main()
