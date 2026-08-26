#!/usr/bin/env python3
"""P3-09 stablecoin PIT population and workflow regression."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "stablecoin_supply_demand_population.py"
EVIDENCE = ROOT / "evidence" / "stablecoin" / "raw"
WORKFLOW = ROOT / ".github" / "workflows" / "stablecoin-capture.yml"
SPEC = importlib.util.spec_from_file_location(
    "stablecoin_supply_demand_population", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class StablecoinSupplyDemandPopulationTest(unittest.TestCase):
    def test_actual_pit_snapshot_populates_three_exact_raw_features(self):
        packet = MODULE.build_packet(EVIDENCE / "2026-08-26")
        result = packet["series_results"][0]

        self.assertEqual(packet["schema_version"], "supply_demand_radar_packet/3")
        self.assertEqual(packet["case_count"], 0)
        self.assertEqual(result["feature_status"], "OBSERVED")
        self.assertEqual(result["expected_periods"], [
            "2026-08-24", "2026-08-25", "2026-08-26",
        ])
        self.assertEqual(result["values"], [
            "308278952768.000000000000",
            "308419839027.000000000000",
            "308703008349.080000000000",
        ])
        self.assertEqual(result["prior_change"], "140886259.000000000000")
        self.assertEqual(result["latest_change"], "283169322.080000000000")
        self.assertEqual(
            result["acceleration_change"], "142283063.080000000000"
        )
        self.assertEqual(result["candidate_policy_status"], "ABSENT_OR_UNRATIFIED")
        self.assertEqual(result["radar_case_created"], False)
        self.assertEqual(
            packet["source_coverage"]["CRYPTO"],
            "OPERATIONAL_PIT_POPULATION_WIRED",
        )
        self.assertIn(
            "LIVE_RADAR_POPULATION_PARTIAL_CRYPTO_ONLY",
            packet["unresolved_boundaries"],
        )
        self.assertNotIn(
            "LIVE_RADAR_POPULATION_NOT_IMPLEMENTED",
            packet["unresolved_boundaries"],
        )
        for row in result["evidence_lineage"]:
            source = row["source_identity"]
            self.assertEqual(
                source["source_sha256"],
                "e461ffd27ef40559101209f3cc986572335e85b45492033ae8528f8ab8f7be23",
            )
            self.assertEqual(
                source["available_at"],
                source["retrieved_at_utc"],
            )

    def test_missing_exact_calendar_point_remains_unknown_not_zero_filled(self):
        transform = MODULE.stablecoin.build_transform(EVIDENCE / "2026-08-26")
        transform["rows"] = [
            row for row in transform["rows"]
            if row["observation_date"] != "2026-08-25"
        ]
        value = MODULE.build_input(transform)
        packet = MODULE.supply_demand.build_packet(value)
        result = packet["series_results"][0]

        self.assertEqual(result["feature_status"], "UNKNOWN_EVIDENCE")
        self.assertIsNone(result["values"])
        self.assertEqual(
            result["unavailable_evidence"],
            [{
                "period_end": "2026-08-25",
                "status": "EVIDENCE_UNRESOLVED",
                "missing_reasons": ["EXACT_PERIOD_OBSERVATION_ABSENT"],
            }],
        )
        self.assertEqual(packet["case_count"], 0)

    def test_source_identity_or_transform_drift_fails_closed(self):
        transform = MODULE.stablecoin.build_transform(EVIDENCE / "2026-08-26")
        bad = copy.deepcopy(transform)
        bad["source"]["endpoint"] = "https://example.com/not-source"
        with self.assertRaisesRegex(MODULE.PopulationError, "SOURCE_IDENTITY_INVALID"):
            MODULE.build_input(bad)

        bad = copy.deepcopy(transform)
        bad["transform_version"] = "stablecoin_net_issuance/future"
        with self.assertRaisesRegex(MODULE.PopulationError, "TRANSFORM_IDENTITY_INVALID"):
            MODULE.build_input(bad)

    def test_publish_is_append_only_and_byte_identical(self):
        packet = MODULE.build_packet(EVIDENCE / "2026-08-26")
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target, first = MODULE.publish_packet(packet, "2026-08-26", root)
            same_target, second = MODULE.publish_packet(packet, "2026-08-26", root)
            self.assertEqual(first, "published")
            self.assertEqual(second, "existing_identical")
            self.assertEqual(target, same_target)

            tampered = copy.deepcopy(packet)
            tampered["case_count"] = 1
            with self.assertRaisesRegex(
                MODULE.PopulationError, "APPEND_ONLY_PACKET_MISMATCH"
            ):
                MODULE.publish_packet(tampered, "2026-08-26", root)
            self.assertEqual(target.read_bytes(), MODULE.packet_bytes(packet))

    def test_workflow_populates_on_capture_or_existing_snapshot_and_commits_packet(self):
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        steps = workflow["jobs"]["capture"]["steps"]
        population = next(
            step for step in steps
            if step.get("name") == "Populate P3-09 Crypto supply-demand raw features"
        )
        commit = next(step for step in steps if step.get("name") == "Commit")

        self.assertIn("stablecoin_supply_demand_population.py", population["run"])
        self.assertIn("steps.capture.outputs.snapshot_date", population["env"]["SNAPSHOT_DATE"])
        self.assertIn(
            'evidence/supply_demand/crypto/$SNAPSHOT_DATE',
            commit["run"],
        )
        self.assertNotIn("--policy", population["run"])

    def test_packet_keeps_all_decision_and_trading_authority_closed(self):
        packet = MODULE.build_packet(EVIDENCE / "2026-08-26")
        authority = packet["authority"]
        for key in (
            "source_ranking_authorized",
            "cross_market_scoring_authorized",
            "importance_ranking_authorized",
            "candidate_ranking_authorized",
            "stage_promotion_authorized",
            "rule_evaluation_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertIs(authority[key], False)


if __name__ == "__main__":
    unittest.main(verbosity=2)
