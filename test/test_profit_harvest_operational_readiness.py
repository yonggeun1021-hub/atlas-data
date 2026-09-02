#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from decision import entry_policy_readiness
from decision import entry_proposal_boundary
from harvest_audit import profit_harvest_policy_boundary
from portfolio import profit_harvest_readiness as readiness
from replay.opportunity_trigger import payload_sha256


class ProfitHarvestOperationalReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = json.loads(readiness.DEFAULT_REPORT.read_text())
        cls.identity = json.loads(readiness.DEFAULT_IDENTITY.read_text())
        cls.shadow_contract = json.loads(readiness.DEFAULT_SHADOW_CONTRACT.read_text())
        cls.shadow_packet = json.loads(readiness.DEFAULT_SHADOW_PACKET.read_text())
        cls.entry_readiness_contract = json.loads(
            readiness.DEFAULT_ENTRY_READINESS_CONTRACT.read_text()
        )
        cls.entry_readiness_packet = entry_policy_readiness.build_packet(
            cls.entry_readiness_contract,
            cls.shadow_packet,
            cls.report,
            cls.identity,
            cls.shadow_contract,
            trigger_kind=cls.shadow_packet["source"]["trigger_kind"],
        )
        cls.entry_contract = json.loads(
            readiness.DEFAULT_ENTRY_BOUNDARY_CONTRACT.read_text()
        )
        cls.trigger_kind = cls.entry_readiness_packet["source"]["trigger_kind"]
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
        cls.harvest_contract = json.loads(readiness.DEFAULT_HARVEST_CONTRACT.read_text())
        cls.source_commit = readiness.current_source_commit()
        cls.readiness_packet = readiness.build_readiness(
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
        cls.packet = readiness.build_operational_packet(
            cls.readiness_packet, cls.harvest_contract
        )

    def test_real_baseline_is_connected_but_operational_counts_are_zero(self):
        self.assertEqual(11, self.packet["summary"]["baseline_episode_count"])
        self.assertEqual(0, self.packet["summary"]["entry_proposal_count"])
        self.assertEqual(0, self.packet["summary"]["live_position_eligible_count"])
        self.assertEqual(0, self.packet["summary"]["harvest_review_item_count"])
        self.assertEqual(0, self.packet["summary"]["harvest_proposal_count"])
        self.assertEqual(0, self.packet["summary"]["order_intent_count"])

    def test_blocking_reasons_are_explicit_not_a_generic_policy_label(self):
        self.assertEqual(readiness.EXPECTED_HARVEST, self.readiness_packet["harvest"])

    def test_p8_13_and_harvest_boundaries_are_both_locked(self):
        self.assertEqual(
            "LOCKED_POLICY_UNRATIFIED",
            self.entry_packet["decision"]["status"],
        )
        decision = self.packet["policy_boundary"]["decision"]
        self.assertEqual("LOCKED_POLICY_UNRATIFIED", decision["status"])
        self.assertEqual("NONE", decision["recommended_action"])
        self.assertEqual([], decision["review_items"])
        self.assertIsNone(decision["harvest_proposal"])
        self.assertIsNone(decision["quantity_proposal"])
        self.assertIsNone(decision["reallocation_handoff"])

    def test_all_authority_is_false(self):
        self.assertEqual(readiness.AUTHORITY_ALL_FALSE, self.packet["authority"])
        self.assertEqual(
            profit_harvest_policy_boundary.AUTHORITY_ALL_FALSE,
            self.packet["policy_boundary"]["authority"],
        )

    def test_source_commit_must_be_an_immutable_full_sha(self):
        for value in ("HEAD", "main", "HEAD~1", self.source_commit[:8], "z" * 40):
            with self.subTest(value=value), self.assertRaises(
                readiness.ProfitHarvestReadinessError
            ):
                readiness.validate_source_commit(value)

    def test_committed_baseline_drift_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp) / "audit"
            shutil.copytree(readiness.DEFAULT_AUDIT_ROOT, root)
            target = root / "episode_ledger.json"
            value = json.loads(target.read_text())
            value.pop()
            target.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n")
            with self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError,
                "BASELINE_ARTIFACT_DRIFT",
            ):
                readiness.validate_baseline(root)

    def test_resigned_p8_entry_boundary_tamper_is_rejected(self):
        tampered = copy.deepcopy(self.entry_packet)
        tampered["summary"]["entry_proposal_count"] = 1
        tampered["packet_sha256"] = payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            entry_proposal_boundary.EntryProposalBoundaryError,
            "ENTRY_PROPOSAL_BOUNDARY_SEMANTIC_TAMPER_OR_DRIFT",
        ):
            readiness.build_readiness(
                tampered,
                self.entry_contract,
                self.entry_readiness_packet,
                self.entry_readiness_contract,
                self.shadow_packet,
                self.report,
                self.identity,
                self.shadow_contract,
                source_commit=self.source_commit,
                trigger_kind=self.trigger_kind,
            )

    def test_resigned_operational_output_cannot_add_a_review_item(self):
        tampered = copy.deepcopy(self.packet)
        tampered["policy_boundary"]["decision"]["review_items"] = [
            {"subject": "BTC"}
        ]
        tampered["packet_sha256"] = payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with mock.patch.object(
            readiness,
            "build_readiness",
            return_value=copy.deepcopy(self.readiness_packet),
        ):
            with self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError,
                "PROFIT_HARVEST_OPERATIONAL_SEMANTIC_TAMPER_OR_DRIFT",
            ):
                readiness.validate_operational_packet(
                    tampered,
                    self.entry_packet,
                    self.entry_contract,
                    self.entry_readiness_packet,
                    self.entry_readiness_contract,
                    self.shadow_packet,
                    self.report,
                    self.identity,
                    self.shadow_contract,
                    self.harvest_contract,
                    source_commit=self.source_commit,
                    trigger_kind=self.trigger_kind,
                )

    def test_outcome_metrics_are_not_operational_inputs(self):
        serialized = json.dumps(self.packet, sort_keys=True)
        for forbidden in (
            "forward_return_pct", "mfe_pct", "mae_pct", "outcome_category",
            "early_exit_return_pct", "harvest_opportunity_count",
        ):
            self.assertNotIn(forbidden, serialized)

    def test_schema_has_no_position_or_quantity_injection_surface(self):
        self.assertNotIn("positions", self.readiness_packet)
        self.assertNotIn("position_quantity", self.readiness_packet)
        self.assertNotIn("quantity", self.readiness_packet["harvest"])

    def test_history_is_content_addressed_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw_temp:
            root = Path(raw_temp)
            first = readiness.write_outputs(
                self.packet,
                output=root / "readiness.json",
                boundary_output=root / "boundary.json",
                history_root=root / "history",
            )
            first_bytes = first.read_bytes()
            second = readiness.write_outputs(
                self.packet,
                output=root / "readiness.json",
                boundary_output=root / "boundary.json",
                history_root=root / "history",
            )
            self.assertEqual(first, second)
            self.assertEqual(first_bytes, second.read_bytes())
            self.assertEqual(1, len(list((root / "history").glob("readiness-*.json"))))

    def test_operational_validator_returns_exact_copy(self):
        with mock.patch.object(
            readiness,
            "build_readiness",
            return_value=copy.deepcopy(self.readiness_packet),
        ):
            validated = readiness.validate_operational_packet(
                self.packet,
                self.entry_packet,
                self.entry_contract,
                self.entry_readiness_packet,
                self.entry_readiness_contract,
                self.shadow_packet,
                self.report,
                self.identity,
                self.shadow_contract,
                self.harvest_contract,
                source_commit=self.source_commit,
                trigger_kind=self.trigger_kind,
            )
        self.assertEqual(self.packet, validated)


class ProfitHarvestExactTypeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entry_packet = json.loads(
            readiness.DEFAULT_ENTRY_BOUNDARY_PACKET.read_text()
        )
        cls.entry_contract = json.loads(
            readiness.DEFAULT_ENTRY_BOUNDARY_CONTRACT.read_text()
        )
        cls.harvest_contract = json.loads(readiness.DEFAULT_HARVEST_CONTRACT.read_text())
        cls.source_commit = readiness.current_source_commit()
        cls.trigger_kind = cls.entry_packet["source"]["trigger_kind"]
        cls.entry_inputs = (
            cls.entry_packet,
            cls.entry_contract,
            {},
            {},
            {},
            {},
            {},
            {},
        )
        with mock.patch.object(
            entry_proposal_boundary,
            "validate_packet",
            return_value=copy.deepcopy(cls.entry_packet),
        ):
            cls.readiness_packet = readiness.build_readiness(
                *cls.entry_inputs,
                source_commit=cls.source_commit,
                trigger_kind=cls.trigger_kind,
            )
        cls.packet = readiness.build_operational_packet(
            cls.readiness_packet, cls.harvest_contract
        )

    def build_readiness_from_validated_entry(self, validated_entry):
        with mock.patch.object(
            entry_proposal_boundary,
            "validate_packet",
            return_value=copy.deepcopy(validated_entry),
        ):
            return readiness.build_readiness(
                *self.entry_inputs,
                source_commit=self.source_commit,
                trigger_kind=self.trigger_kind,
            )

    def validate_output(self, packet):
        with mock.patch.object(
            readiness,
            "build_readiness",
            return_value=copy.deepcopy(self.readiness_packet),
        ):
            return readiness.validate_operational_packet(
                packet,
                *self.entry_inputs,
                self.harvest_contract,
                source_commit=self.source_commit,
                trigger_kind=self.trigger_kind,
            )

    def test_p8_13_numeric_boolean_aliases_fail_closed(self):
        for alias in (False, 0.0):
            entry_packet = copy.deepcopy(self.entry_packet)
            entry_packet["decision"]["capital"] = alias
            with self.subTest(boundary="decision", alias=alias), self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError,
                "P8_13_BOUNDARY_NOT_LOCKED",
            ):
                self.build_readiness_from_validated_entry(entry_packet)

        for alias in (False, 0.0):
            entry_packet = copy.deepcopy(self.entry_packet)
            entry_packet["summary"]["entry_proposal_count"] = alias
            with self.subTest(boundary="summary", alias=alias), self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError,
                "P8_13_ENTRY_PROPOSAL_PRESENT",
            ):
                self.build_readiness_from_validated_entry(entry_packet)

        for field, expected in entry_proposal_boundary.AUTHORITY_ALL_FALSE.items():
            entry_packet = copy.deepcopy(self.entry_packet)
            entry_packet["authority"][field] = int(expected)
            with self.subTest(
                boundary="authority", field=field
            ), self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError, "P8_13_AUTHORITY_ESCALATION"
            ):
                self.build_readiness_from_validated_entry(entry_packet)

    def test_readiness_numeric_boolean_aliases_fail_closed(self):
        packet = copy.deepcopy(self.readiness_packet)
        packet["baseline"]["episode_count"] = 11.0
        packet["readiness_sha256"] = profit_harvest_policy_boundary.payload_sha256(
            {key: value for key, value in packet.items() if key != "readiness_sha256"}
        )
        with self.assertRaisesRegex(
            readiness.ProfitHarvestReadinessError,
            "UPSTREAM_READINESS_BASELINE_DRIFT",
        ):
            readiness.build_operational_packet(packet, self.harvest_contract)

        for field, expected in readiness.AUTHORITY_ALL_FALSE.items():
            packet = copy.deepcopy(self.readiness_packet)
            packet["authority"][field] = int(expected)
            packet["readiness_sha256"] = profit_harvest_policy_boundary.payload_sha256(
                {
                    key: value
                    for key, value in packet.items()
                    if key != "readiness_sha256"
                }
            )
            with self.subTest(boundary="authority", field=field), self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError,
                "UPSTREAM_READINESS_AUTHORITY_ESCALATION",
            ):
                readiness.build_operational_packet(packet, self.harvest_contract)

    def test_recursive_output_numeric_boolean_aliases_fail_closed(self):
        summary_aliases = {
            "baseline_episode_count": 11.0,
            "entry_proposal_count": False,
            "live_position_eligible_count": 0.0,
            "harvest_review_item_count": False,
            "harvest_proposal_count": 0.0,
            "order_intent_count": False,
        }
        for field, alias in summary_aliases.items():
            packet = copy.deepcopy(self.packet)
            packet["summary"][field] = alias
            with self.subTest(boundary="summary", field=field), self.assertRaisesRegex(
                readiness.ProfitHarvestReadinessError,
                "PROFIT_HARVEST_OPERATIONAL_SEMANTIC_TAMPER_OR_DRIFT",
            ):
                self.validate_output(packet)

        authority_sections = (
            ("output", readiness.AUTHORITY_ALL_FALSE),
            (
                "policy_boundary",
                profit_harvest_policy_boundary.AUTHORITY_ALL_FALSE,
            ),
        )
        for section, expected_authority in authority_sections:
            for field, expected in expected_authority.items():
                packet = copy.deepcopy(self.packet)
                target = (
                    packet["authority"]
                    if section == "output"
                    else packet["policy_boundary"]["authority"]
                )
                target[field] = int(expected)
                with self.subTest(
                    boundary=section, field=field
                ), self.assertRaisesRegex(
                    readiness.ProfitHarvestReadinessError,
                    "PROFIT_HARVEST_OPERATIONAL_SEMANTIC_TAMPER_OR_DRIFT",
                ):
                    self.validate_output(packet)


if __name__ == "__main__":
    unittest.main()
