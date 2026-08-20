#!/usr/bin/env python3
"""P1-CR-07 dual-window Crypto leadership regression."""

import datetime as dt
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


def write_leadership_policy(path, effective="2026-01-01"):
    return write_json(
        path,
        {
            "schema_version": 2,
            "policy_version": "crypto_leadership/test-v2",
            "approval_status": "RATIFIED",
            "effective_from": effective,
            "windows": [
                {
                    "window_id": "pilot_7d",
                    "role": "PILOT",
                    "lookback_calendar_days": 7,
                },
                {
                    "window_id": "primary_30d",
                    "role": "PRIMARY",
                    "lookback_calendar_days": 30,
                },
            ],
            "group_return_method": "equal_weight_daily_rebalanced",
            "relative_strength_reference": "BTC",
            "bucket_policy": "btc_eth_else_alt",
            "sector_chain_missing_policy": "unknown_group_layer",
            "group_coverage_policy_status": "UNRATIFIED",
        },
    )


def taxonomy_record(
    asset_id,
    sectors,
    chains,
    start="2026-01-01",
    end=None,
):
    return {
        "canonical_asset_id": asset_id,
        "effective_from": start,
        "effective_to": end,
        "bucket": asset_id if asset_id in {"BTC", "ETH"} else "ALT",
        "sectors": sorted(sectors),
        "chains": sorted(chains),
        "reason": "test fixture taxonomy",
    }


def default_taxonomy_records():
    return [
        taxonomy_record("BTC", ["MONETARY"], ["BITCOIN"]),
        taxonomy_record("ETH", ["SMART_CONTRACT"], ["ETHEREUM"]),
        taxonomy_record("SOL", ["SMART_CONTRACT"], ["SOLANA"]),
    ]


def write_taxonomy(path, records=None, approval="RATIFIED"):
    return write_json(
        path,
        {
            "schema_version": 1,
            "policy_version": "crypto_asset_taxonomy/test-v1",
            "approval_status": approval,
            "source_name": "kraken_spot_market_data",
            "effective_from": (
                "2026-01-01" if approval == "RATIFIED" else None
            ),
            "records": default_taxonomy_records() if records is None else records,
        },
    )


def prices_for_index(index, current):
    return {
        "BTC": (100 + index, 101 + index, current),
        "ETH": (200 + 2 * index, 202 + 2 * index, current),
        "SOL": (300 + index, 301 + index, current),
    }


def write_window(
    root,
    days,
    end_as_of="2026-08-19",
    current=9999,
    skip_as_of=None,
    mismatch_as_of=None,
):
    root = Path(root)
    end = dt.date.fromisoformat(end_as_of)
    start = end - dt.timedelta(days=days - 1)
    for index in range(days):
        as_of = start + dt.timedelta(days=index)
        if as_of.isoformat() == skip_as_of:
            continue
        prices = prices_for_index(index, current)
        if as_of.isoformat() == mismatch_as_of:
            previous, latest, current_value = prices["BTC"]
            prices["BTC"] = (previous + 1, latest, current_value)
        BREADTH_FIXTURE.write_snapshot(
            root,
            vintage=(as_of + dt.timedelta(days=1)).isoformat(),
            prices=prices,
        )
    return root


def inputs(tmp, taxonomy_path=None, exclusion_taxonomy_path=None):
    tmp = Path(tmp)
    return {
        "universe_policy_path": BREADTH_FIXTURE.write_policy(
            tmp / "universe.json", target=3
        ),
        "exclusion_taxonomy_path": (
            exclusion_taxonomy_path
            or BREADTH_FIXTURE.write_taxonomy(
                tmp / "breadth_exclusion_taxonomy.json",
                {
                    "BTC": "eligible_crypto",
                    "ETH": "eligible_crypto",
                    "SOL": "eligible_crypto",
                },
            )
        ),
        "leadership_policy_path": write_leadership_policy(
            tmp / "leadership.json"
        ),
        "taxonomy_path": (
            taxonomy_path
            or write_taxonomy(tmp / "taxonomy.json")
        ),
    }


def window_by_id(result, window_id):
    return next(
        item for item in result["windows"] if item["window_id"] == window_id
    )


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class CryptoLeadershipTest(unittest.TestCase):
    def test_default_policy_ratifies_only_dual_window_and_keeps_group_gate_closed(self):
        policy = MODULE.load_leadership_policy()
        taxonomy = MODULE.load_taxonomy()

        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(
            [item["lookback_calendar_days"] for item in policy["windows"]],
            [7, 30],
        )
        self.assertEqual(policy["group_coverage_policy_status"], "UNRATIFIED")
        self.assertEqual(taxonomy["approval_status"], "UNRATIFIED")
        self.assertEqual(taxonomy["records"], [])

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", days=3)
            custom = inputs(tmp)
            custom.pop("leadership_policy_path")
            custom.pop("taxonomy_path")
            result = MODULE.build_transform(root, **custom)
            self.assertEqual(result["status"], "UNKNOWN")
            self.assertTrue(
                all(item["status"] == "UNKNOWN" for item in result["windows"])
            )
            self.assertFalse(result["ranking_authorized"])
            self.assertFalse(result["production_wiring_authorized"])
            self.assertFalse(result["trading_action_authorized"])

    def test_seven_day_pilot_observes_while_primary_stays_unknown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", days=7)
            result = MODULE.build_transform(root, **inputs(tmp))
            pilot = window_by_id(result, "pilot_7d")
            primary = window_by_id(result, "primary_30d")

            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(pilot["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(pilot["window"]["available_point_count"], 7)
            self.assertEqual(primary["status"], "UNKNOWN")
            self.assertEqual(
                primary["unknown_reason"], "INSUFFICIENT_CONTIGUOUS_HISTORY"
            )
            self.assertEqual(primary["window"]["available_point_count"], 7)
            assets = {
                item["canonical_asset_id"]: item
                for item in pilot["asset_relative_strength"]
            }
            self.assertEqual(sorted(assets), ["BTC", "ETH", "SOL"])
            self.assertEqual(assets["BTC"]["relative_strength_vs_btc"], "0")
            buckets = pilot["group_relative_strength"]["bucket"]
            self.assertEqual(
                [item["group_id"] for item in buckets], ["ALT", "BTC", "ETH"]
            )
            self.assertTrue(
                all(item["status"] == "OBSERVED_UNCLASSIFIED" for item in buckets)
            )

    def test_thirty_days_observe_both_windows_independently(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", days=30)
            result = MODULE.build_transform(root, **inputs(tmp))
            pilot = window_by_id(result, "pilot_7d")
            primary = window_by_id(result, "primary_30d")

            self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(pilot["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(primary["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(len(pilot["daily_points"]), 7)
            self.assertEqual(len(primary["daily_points"]), 30)
            self.assertEqual(
                len(result["lineage"]["manifest_sha256_by_date"]), 30
            )

    def test_older_gap_stops_primary_without_propagating_to_pilot(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(
                Path(tmp) / "raw", days=30, skip_as_of="2026-08-01"
            )
            result = MODULE.build_transform(root, **inputs(tmp))
            pilot = window_by_id(result, "pilot_7d")
            primary = window_by_id(result, "primary_30d")

            self.assertEqual(result["status"], "PARTIAL")
            self.assertEqual(pilot["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(primary["status"], "UNKNOWN")
            self.assertEqual(primary["window"]["missing_dates"], ["2026-08-01"])
            self.assertEqual(primary["daily_points"], [])

    def test_unratified_or_incomplete_taxonomy_stops_only_sector_chain_layer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", days=7)
            custom = inputs(tmp)
            custom["taxonomy_path"] = write_taxonomy(
                Path(tmp) / "unratified.json", records=[], approval="UNRATIFIED"
            )
            result = MODULE.build_transform(root, **custom)
            pilot = window_by_id(result, "pilot_7d")

            self.assertEqual(pilot["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(len(pilot["asset_relative_strength"]), 3)
            layer = pilot["group_relative_strength"]["sector_chain"]
            self.assertEqual(layer["status"], "UNKNOWN")
            self.assertEqual(layer["unknown_reason"], "TAXONOMY_UNRATIFIED")
            self.assertEqual(layer["sector"], [])
            self.assertEqual(layer["chain"], [])

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", days=7)
            partial_taxonomy = write_taxonomy(
                Path(tmp) / "partial.json", default_taxonomy_records()[:-1]
            )
            result = MODULE.build_transform(
                root, **inputs(tmp, taxonomy_path=partial_taxonomy)
            )
            pilot = window_by_id(result, "pilot_7d")
            layer = pilot["group_relative_strength"]["sector_chain"]
            self.assertEqual(pilot["status"], "OBSERVED_UNCLASSIFIED")
            self.assertEqual(layer["unknown_reason"], "TAXONOMY_COVERAGE_UNKNOWN")
            self.assertEqual(len(layer["missing_asset_dates"]), 7)
            self.assertTrue(
                all(item.startswith("SOL@") for item in layer["missing_asset_dates"])
            )

    def test_complete_taxonomy_still_cannot_invent_group_coverage_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = MODULE.build_transform(
                write_window(Path(tmp) / "raw", days=7), **inputs(tmp)
            )
            pilot = window_by_id(result, "pilot_7d")
            layer = pilot["group_relative_strength"]["sector_chain"]
            self.assertEqual(
                layer["unknown_reason"], "GROUP_COVERAGE_POLICY_UNRATIFIED"
            )
            self.assertEqual(layer["missing_asset_dates"], [])

    def test_source_unknown_is_window_unknown_and_structural_drift_hard_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(Path(tmp) / "raw", days=7)
            exclusion = BREADTH_FIXTURE.write_taxonomy(
                Path(tmp) / "source_taxonomy.json",
                {"BTC": "eligible_crypto", "ETH": "eligible_crypto"},
            )
            result = MODULE.build_transform(
                root,
                **inputs(tmp, exclusion_taxonomy_path=exclusion),
            )
            pilot = window_by_id(result, "pilot_7d")
            self.assertEqual(pilot["status"], "UNKNOWN")
            self.assertEqual(pilot["unknown_reason"], "SOURCE_POINT_UNKNOWN")
            self.assertEqual(len(pilot["source_unknown_points"]), 7)

        with tempfile.TemporaryDirectory() as tmp:
            root = write_window(
                Path(tmp) / "raw",
                days=7,
                mismatch_as_of="2026-08-18",
            )
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "CROSS_SNAPSHOT_CLOSE_MISMATCH.*BTC"
            ):
                MODULE.build_transform(root, **inputs(tmp))

        with tempfile.TemporaryDirectory() as tmp:
            overlap = default_taxonomy_records() + [
                taxonomy_record("SOL", ["SMART_CONTRACT"], ["SOLANA"])
            ]
            path = write_taxonomy(Path(tmp) / "overlap.json", overlap)
            with self.assertRaisesRegex(
                MODULE.LeadershipError, "TAXONOMY_RANGE_OVERLAP.*SOL"
            ):
                MODULE.load_taxonomy(path)

    def test_current_candle_is_excluded_and_output_is_deterministic_atomic(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = inputs(tmp)
            low = MODULE.build_transform(
                write_window(Path(tmp) / "low", days=7, current=2), **custom
            )
            high = MODULE.build_transform(
                write_window(Path(tmp) / "high", days=7, current=999999), **custom
            )
            low_pilot = window_by_id(low, "pilot_7d")
            high_pilot = window_by_id(high, "pilot_7d")
            self.assertEqual(
                low_pilot["asset_relative_strength"],
                high_pilot["asset_relative_strength"],
            )
            self.assertEqual(
                low_pilot["group_relative_strength"],
                high_pilot["group_relative_strength"],
            )
            self.assertNotEqual(low["lineage"], high["lineage"])

            repeated = MODULE.build_transform(
                Path(tmp) / "low", **custom
            )
            output = Path(tmp) / "output" / "leadership.json"
            MODULE.write_output(low, output)
            self.assertEqual(low, repeated)
            self.assertFalse(has_float(low))
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), low)
            self.assertFalse(list(output.parent.glob(".*.tmp.*")))

    def test_scheduled_replay_is_transient_and_adds_no_network_or_factor(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )

        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertIn("crypto_leadership.py transform", workflows)
        self.assertIn("$RUNNER_TEMP/crypto-leadership-live-replay.json", workflows)
        self.assertIn("p1-cr-07-live-replay", workflows)
        self.assertNotIn("data/factors/crypto_leadership", workflows)


if __name__ == "__main__":
    unittest.main()
