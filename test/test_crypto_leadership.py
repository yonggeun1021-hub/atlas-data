#!/usr/bin/env python3
"""P1-CR-07 Crypto leadership PIT relative-strength regression.

All source snapshots and ratified policy fixtures live under temporary
directories.  The tests make no live request and write no tracked factor.
"""

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_leadership.py"
BREADTH_TEST_SCRIPT = ROOT / "test" / "test_crypto_breadth.py"
WORKFLOWS = ROOT / ".github" / "workflows"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("crypto_leadership", SCRIPT)
BREADTH_FIXTURE = load_module("crypto_breadth_fixture", BREADTH_TEST_SCRIPT)
CONTRACT = MODULE.load_contract()


def write_json(path, payload):
    path = Path(path)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_leadership_policy(
    path,
    lookback=3,
    sectors=None,
    chains=None,
    group_minimum=1,
):
    payload = {
        "schema_version": 1,
        "policy_version": "crypto_leadership/test-v1",
        "approval_status": "RATIFIED",
        "effective_from": "2026-01-01",
        "lookback_calendar_days": lookback,
        "group_return_method": "equal_weight_daily_rebalanced",
        "relative_strength_reference": "BTC",
        "required_bucket_minimum_members": {
            "ALT": 1,
            "BTC": 1,
            "ETH": 1,
        },
        "required_sectors": sorted(
            sectors or ["MONETARY", "SMART_CONTRACT"]
        ),
        "required_chains": sorted(
            chains or ["BITCOIN", "ETHEREUM", "SOLANA"]
        ),
        "taxonomy_group_minimum_members": group_minimum,
    }
    return write_json(path, payload)


def taxonomy_record(
    asset_id,
    bucket,
    sectors,
    chains,
    start="2026-01-01",
    end=None,
):
    return {
        "canonical_asset_id": asset_id,
        "effective_from": start,
        "effective_to": end,
        "bucket": bucket,
        "sectors": sorted(sectors),
        "chains": sorted(chains),
        "reason": "test fixture taxonomy",
    }


def default_taxonomy_records(include_ada=False):
    records = [
        taxonomy_record("BTC", "BTC", ["MONETARY"], ["BITCOIN"]),
        taxonomy_record(
            "ETH", "ETH", ["SMART_CONTRACT"], ["ETHEREUM"]
        ),
        taxonomy_record(
            "SOL", "ALT", ["SMART_CONTRACT"], ["SOLANA"]
        ),
    ]
    if include_ada:
        records.append(
            taxonomy_record(
                "ADA", "ALT", ["SMART_CONTRACT"], ["SOLANA"]
            )
        )
    return records


def write_taxonomy(path, records=None):
    payload = {
        "schema_version": 1,
        "policy_version": "crypto_asset_taxonomy/test-v1",
        "approval_status": "RATIFIED",
        "source_name": "kraken_spot_market_data",
        "effective_from": "2026-01-01",
        "records": records or default_taxonomy_records(),
    }
    return write_json(path, payload)


def prices(previous_btc, latest_btc, previous_eth, latest_eth,
           previous_sol, latest_sol, current=9999, ada=None):
    result = {
        "BTC": (previous_btc, latest_btc, current),
        "ETH": (previous_eth, latest_eth, current),
        "SOL": (previous_sol, latest_sol, current),
    }
    if ada is not None:
        result["ADA"] = (ada[0], ada[1], current)
    return result


def write_window(root, current=9999, include_ada_after_first=False,
                 mismatch=False):
    root = Path(root)
    BREADTH_FIXTURE.write_snapshot(
        root,
        vintage="2026-08-18",
        prices=prices(100, 110, 100, 120, 100, 90, current),
    )
    second_btc = 111 if mismatch else 110
    second = prices(second_btc, 121, 120, 132, 90, 99, current)
    if include_ada_after_first:
        second["ADA"] = (200, 210, current)
    BREADTH_FIXTURE.write_snapshot(
        root, vintage="2026-08-19", prices=second
    )
    third = prices(121, "133.1", 132, "158.4", 99, 99, current)
    if include_ada_after_first:
        third["ADA"] = (210, 220, current)
    BREADTH_FIXTURE.write_snapshot(
        root, vintage="2026-08-20", prices=third
    )
    return root


def ratified_inputs(tmp, include_ada=False):
    tmp = Path(tmp)
    return {
        "universe_policy_path": BREADTH_FIXTURE.write_policy(
            tmp / "universe.json", target=3
        ),
        "exclusion_taxonomy_path": BREADTH_FIXTURE.write_taxonomy(
            tmp / "breadth_exclusion_taxonomy.json",
            {
                "ADA": "eligible_crypto",
                "BTC": "eligible_crypto",
                "ETH": "eligible_crypto",
                "SOL": "eligible_crypto",
            },
        ),
        "leadership_policy_path": write_leadership_policy(
            tmp / "leadership.json"
        ),
        "taxonomy_path": write_taxonomy(
            tmp / "taxonomy.json",
            default_taxonomy_records(include_ada=include_ada),
        ),
    }


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class CryptoLeadershipTest(unittest.TestCase):
    def test_default_policy_and_taxonomy_keep_authority_closed(self):
        leadership = MODULE.load_leadership_policy()
        taxonomy = MODULE.load_taxonomy()

        self.assertEqual(leadership["approval_status"], "UNRATIFIED")
        self.assertIsNone(leadership["lookback_calendar_days"])
        self.assertEqual(taxonomy["approval_status"], "UNRATIFIED")
        self.assertEqual(taxonomy["records"], [])

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw")
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "LEADERSHIP_POLICY_UNRATIFIED"
            ):
                MODULE.build_transform(root)

            leadership_path = write_leadership_policy(
                Path(tmp) / "leadership.json"
            )
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "TAXONOMY_UNRATIFIED"
            ):
                MODULE.build_transform(
                    root, leadership_policy_path=leadership_path
                )

    def test_exact_window_builds_raw_asset_bucket_sector_chain_strength(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw")
            result = MODULE.build_transform(root, **ratified_inputs(tmp))

            self.assertEqual(result["window"]["start_date"], "2026-08-17")
            self.assertEqual(result["window"]["end_date"], "2026-08-19")
            self.assertEqual(result["window"]["point_count"], 3)
            assets = {
                item["canonical_asset_id"]: item
                for item in result["asset_relative_strength"]
            }
            self.assertEqual(sorted(assets), ["BTC", "ETH", "SOL"])
            self.assertEqual(assets["BTC"]["cumulative_gross_return"], "1.331")
            self.assertEqual(assets["BTC"]["relative_strength_vs_btc"], "0")
            self.assertEqual(assets["ETH"]["cumulative_gross_return"], "1.584")
            self.assertEqual(
                assets["ETH"]["relative_strength_vs_btc"],
                MODULE.render(
                    MODULE.Decimal("1.584") / MODULE.Decimal("1.331")
                    - MODULE.Decimal(1),
                    CONTRACT,
                ),
            )
            groups = result["group_relative_strength"]
            self.assertEqual(
                [item["group_id"] for item in groups["bucket"]],
                ["ALT", "BTC", "ETH"],
            )
            smart = next(
                item
                for item in groups["sector"]
                if item["group_id"] == "SMART_CONTRACT"
            )
            self.assertEqual(smart["minimum_daily_member_count"], 2)
            self.assertEqual(smart["cumulative_gross_return"], "1.2705")
            self.assertEqual(result["partial_window_assets"], [])
            self.assertFalse(result["leader_classification_authorized"])
            self.assertFalse(result["ranking_authorized"])
            self.assertFalse(result["regime_score_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(result["trading_action_authorized"])

    def test_current_uncommitted_rows_cannot_change_strength(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp)
            low = MODULE.build_transform(
                write_window(Path(tmp) / "low", current=2), **inputs
            )
            high = MODULE.build_transform(
                write_window(Path(tmp) / "high", current=999999), **inputs
            )

            self.assertEqual(
                low["asset_relative_strength"], high["asset_relative_strength"]
            )
            self.assertEqual(
                low["group_relative_strength"], high["group_relative_strength"]
            )
            self.assertNotEqual(low["lineage"], high["lineage"])

    def test_window_gap_and_adjacent_close_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "gap"
            BREADTH_FIXTURE.write_snapshot(
                root,
                vintage="2026-08-18",
                prices=prices(100, 110, 100, 120, 100, 90),
            )
            BREADTH_FIXTURE.write_snapshot(
                root,
                vintage="2026-08-20",
                prices=prices(121, "133.1", 132, "158.4", 99, 99),
            )
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "WINDOW_NOT_CONTIGUOUS"
            ):
                MODULE.build_transform(root, **ratified_inputs(tmp))

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", mismatch=True)
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "CROSS_SNAPSHOT_CLOSE_MISMATCH.*BTC"
            ):
                MODULE.build_transform(root, **ratified_inputs(tmp))

    def test_taxonomy_gap_overlap_and_group_coverage_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlap = default_taxonomy_records() + [
                taxonomy_record(
                    "SOL", "ALT", ["SMART_CONTRACT"], ["SOLANA"]
                )
            ]
            path = write_taxonomy(Path(tmp) / "overlap.json", overlap)
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "TAXONOMY_RANGE_OVERLAP.*SOL"
            ):
                MODULE.load_taxonomy(path)

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw")
            inputs = ratified_inputs(tmp)
            inputs["taxonomy_path"] = write_taxonomy(
                Path(tmp) / "missing.json",
                default_taxonomy_records()[:-1],
            )
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "TAXONOMY_MISSING.*SOL"
            ):
                MODULE.build_transform(root, **inputs)

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw")
            inputs = ratified_inputs(tmp)
            inputs["leadership_policy_path"] = write_leadership_policy(
                Path(tmp) / "coverage.json", sectors=["NOT_PRESENT"]
            )
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "GROUP_COVERAGE_INCOMPLETE.*NOT_PRESENT"
            ):
                MODULE.build_transform(root, **inputs)

    def test_each_day_uses_its_captured_universe_and_effective_taxonomy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(
                Path(tmp) / "raw", include_ada_after_first=True
            )
            result = MODULE.build_transform(
                root, **ratified_inputs(tmp, include_ada=True)
            )

            self.assertEqual(
                [len(point["members"]) for point in result["daily_points"]],
                [3, 3, 3],
            )
            self.assertEqual(
                result["partial_window_assets"],
                [
                    {
                        "canonical_asset_id": "ADA",
                        "observed_day_count": 2,
                        "required_day_count": 3,
                        "reason": (
                            "not_present_in_every_as_captured_daily_universe"
                        ),
                    },
                    {
                        "canonical_asset_id": "SOL",
                        "observed_day_count": 1,
                        "required_day_count": 3,
                        "reason": (
                            "not_present_in_every_as_captured_daily_universe"
                        ),
                    }
                ],
            )
            self.assertNotIn(
                "ADA",
                [
                    item["canonical_asset_id"]
                    for item in result["asset_relative_strength"]
                ],
            )
            alt_counts = [
                next(
                    item
                    for item in point["groups"]["bucket"]
                    if item["group_id"] == "ALT"
                )["member_count"]
                for point in result["daily_points"]
            ]
            self.assertEqual(alt_counts, [1, 1, 1])
            self.assertFalse(
                result["lineage"]["current_catalog_backfill_authorized"]
            )

    def test_output_is_deterministic_float_free_and_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw")
            inputs = ratified_inputs(tmp)
            first = MODULE.build_transform(root, **inputs)
            second = MODULE.build_transform(root, **inputs)
            output = Path(tmp) / "output" / "leadership.json"
            MODULE.write_output(first, output)

            self.assertEqual(first, second)
            self.assertFalse(has_float(first))
            self.assertEqual(
                json.loads(output.read_text(encoding="utf-8")), first
            )
            self.assertFalse(list(output.parent.glob(".*.tmp.*")))

    def test_no_network_workflow_or_tracked_factor_wiring_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )

        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertNotIn("crypto_leadership.py", workflows)
        self.assertNotIn("crypto_leadership", workflows)


if __name__ == "__main__":
    unittest.main()
