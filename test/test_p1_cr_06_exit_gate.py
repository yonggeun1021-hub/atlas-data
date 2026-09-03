#!/usr/bin/env python3
"""P1-CR-06 first-qualified-live-Top-100 Exit Gate regression."""

import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
REGISTRY = ROOT / "config" / "operational_validation_registry.json"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class P1CR06ExitGateTest(unittest.TestCase):
    def setUp(self):
        registry = read_json(REGISTRY)
        self.item = next(
            row
            for row in registry["items"]
            if row["work_item_id"] == "P1-CR-06"
        )

    def test_registry_binds_two_committed_natural_qualified_chains(self):
        self.assertEqual(self.item["exit_gate_state"], "SATISFIED")
        self.assertEqual(
            self.item["exit_gate_basis"],
            "FIRST_QUALIFIED_LIVE_TOP100_SNAPSHOT",
        )
        self.assertIsNone(self.item["remaining_gate"])
        self.assertEqual(
            [row["snapshot_date_utc"] for row in self.item["exit_gate_evidence"]],
            ["2026-08-30", "2026-08-31"],
        )

        for row in self.item["exit_gate_evidence"]:
            telemetry = read_json(ROOT / row["telemetry_path"])
            inventory = read_json(ROOT / row["taxonomy_inventory_path"])
            population = read_json(ROOT / row["population_path"])

            self.assertEqual(telemetry["github"]["event_name"], "schedule")
            self.assertEqual(telemetry["capture"]["result"], "captured")
            self.assertEqual(
                telemetry["p1_cr_06_validation"]["result"], "passed"
            )
            self.assertEqual(
                telemetry["p3_04_population"]["result"], "populated"
            )
            self.assertEqual(
                telemetry["p3_04_population"]["payload_sha256"],
                row["population_payload_sha256"],
            )

            selection = inventory["selection_context"]
            self.assertEqual(
                inventory["source_outcome"]["status"],
                "OBSERVED_UNCLASSIFIED",
            )
            self.assertEqual(selection["target_asset_count"], 100)
            self.assertEqual(selection["unknown_before_cutoff_count"], 0)

            packet = population["packet"]
            self.assertEqual(
                population["payload_sha256"],
                row["population_payload_sha256"],
            )
            self.assertEqual(
                packet["status"],
                "BREADTH_SOURCE_COVERAGE_UNIVERSE_VALIDATED",
            )
            self.assertEqual(packet["selected_count"], 100)
            self.assertEqual(packet["target_count"], 100)
            self.assertTrue(population["authority"]["source_coverage_population_only"])
            for field in (
                "investable_universe_authorized",
                "production_authorized",
                "stage_promotion_authorized",
                "trading_authorized",
            ):
                self.assertIs(population["authority"][field], False)

    def test_later_unknown_snapshots_do_not_claim_persistent_readiness(self):
        for day in ("2026-09-01", "2026-09-02"):
            telemetry_path = next(
                (ROOT / "data" / "operations" / "crypto_breadth_capture_runs" / day).glob(
                    "run-*-attempt-*.json"
                )
            )
            telemetry = read_json(telemetry_path)
            inventory = read_json(
                ROOT
                / "data"
                / "observations"
                / "crypto_taxonomy_gap"
                / day
                / "packet.json"
            )
            self.assertEqual(
                telemetry["p3_04_population"]["result"], "blocked"
            )
            self.assertEqual(inventory["source_outcome"]["status"], "UNKNOWN")
            self.assertGreater(
                inventory["selection_context"]["unknown_before_cutoff_count"],
                0,
            )


if __name__ == "__main__":
    unittest.main()
