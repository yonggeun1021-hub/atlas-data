#!/usr/bin/env python3
"""P1-US-06 transient-only US Leadership contract regression.

Every price row and ratified policy fixture is synthetic and temporary. The
tests make no live request and write no tracked vendor data or factor.
"""

from decimal import Decimal
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "us_leadership.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("us_leadership", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def write_json(path, payload):
    path = Path(path)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def write_leadership_policy(path, price_basis="RAW", groups=None):
    return write_json(
        path,
        {
            "schema_version": 1,
            "policy_version": "us_leadership/test-v1",
            "approval_status": "RATIFIED",
            "effective_from": "2026-01-01",
            "source_name": "tiingo_eod",
            "quote_currency": "USD",
            "market_timezone": "America/New_York",
            "price_basis": price_basis,
            "allowed_run_modes": ["FORWARD_SHADOW", "HISTORICAL_BACKFILL"],
            "session_calendar_source": "synthetic_xnys_fixture/v1",
            "benchmark_asset": "SPY",
            "lookback_sessions": 3,
            "minimum_assets": 3,
            "required_groups": sorted(groups or ["BROAD", "GROWTH"]),
            "group_minimum_members": 1,
            "group_return_method": "equal_weight_daily_rebalanced",
            "split_window_policy": "no_split_events_required",
        },
    )


def universe_record(asset, start="2026-01-01", end=None):
    return {
        "asset": asset,
        "effective_from": start,
        "effective_to": end,
        "reason": "synthetic PIT membership",
    }


def write_universe(path, records=None):
    return write_json(
        path,
        {
            "schema_version": 1,
            "policy_version": "us_leadership_universe/test-v1",
            "approval_status": "RATIFIED",
            "source_name": "tiingo_eod",
            "effective_from": "2026-01-01",
            "membership_kind": "point_in_time_source_coverage",
            "records": records
            or [
                universe_record("IWM"),
                universe_record("QQQ"),
                universe_record("SPY"),
            ],
        },
    )


def taxonomy_record(asset, groups, start="2026-01-01", end=None):
    return {
        "asset": asset,
        "effective_from": start,
        "effective_to": end,
        "groups": sorted(groups),
        "reason": "synthetic effective taxonomy",
    }


def default_taxonomy(include_xle=False):
    records = [
        taxonomy_record("IWM", ["BROAD"]),
        taxonomy_record("QQQ", ["GROWTH"]),
        taxonomy_record("SPY", ["BROAD"]),
    ]
    if include_xle:
        records.append(taxonomy_record("XLE", ["ENERGY"]))
    return records


def write_taxonomy(path, records=None):
    return write_json(
        path,
        {
            "schema_version": 1,
            "policy_version": "us_asset_taxonomy/test-v1",
            "approval_status": "RATIFIED",
            "source_name": "tiingo_eod",
            "effective_from": "2026-01-01",
            "records": records or default_taxonomy(),
        },
    )


def sessions():
    return [
        dt.date(2026, 8, 13),
        dt.date(2026, 8, 14),
        dt.date(2026, 8, 17),
        dt.date(2026, 8, 18),
    ]


def payload(
    prices=None,
    run_mode="FORWARD_SHADOW",
    price_basis="RAW",
    fetched_at="2026-08-18T20:20:00-04:00",
    decision_at="2026-08-18T20:30:00-04:00",
    source="tiingo_eod",
):
    days = sessions()
    prices = prices or {
        "IWM": ["100", "80", "72", "64.8"],
        "QQQ": ["100", "95", "90.25", "85.7375"],
        "SPY": ["100", "90", "81", "72.9"],
    }
    if run_mode == "HISTORICAL_BACKFILL":
        fetched_at = "2026-08-19T12:00:00Z"
        decision_at = None
    return {
        "schema_version": 1,
        "source_name": source,
        "quote_currency": "USD",
        "market_timezone": "America/New_York",
        "run_mode": run_mode,
        "price_basis": price_basis,
        "observation_date": days[-1].isoformat(),
        "fetched_at": fetched_at,
        "decision_at": decision_at,
        "expected_session_dates": [day.isoformat() for day in days],
        "asset_rows": [
            {
                "asset": asset,
                "rows": [
                    {
                        "session_date": day.isoformat(),
                        "close": close,
                        "split_factor": "1",
                    }
                    for day, close in zip(days, prices[asset])
                ],
            }
            for asset in sorted(prices)
        ],
    }


def ratified_inputs(tmp, include_xle=False, groups=None, price_basis="RAW"):
    tmp = Path(tmp)
    universe = [
        universe_record("IWM"),
        universe_record("QQQ"),
        universe_record("SPY"),
    ]
    if include_xle:
        universe.append(universe_record("XLE", start="2026-08-17"))
    return {
        "leadership_policy_path": write_leadership_policy(
            tmp / "leadership.json", price_basis=price_basis, groups=groups
        ),
        "universe_policy_path": write_universe(
            tmp / "universe.json", sorted(universe, key=lambda item: item["asset"])
        ),
        "taxonomy_path": write_taxonomy(
            tmp / "taxonomy.json", default_taxonomy(include_xle=include_xle)
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


class USLeadershipTest(unittest.TestCase):
    def test_default_policies_keep_calculation_authority_closed(self):
        leadership = MODULE.load_leadership_policy()
        universe = MODULE.load_universe_policy()
        taxonomy = MODULE.load_taxonomy()

        self.assertEqual(leadership["approval_status"], "UNRATIFIED")
        self.assertIsNone(leadership["benchmark_asset"])
        self.assertEqual(universe["approval_status"], "UNRATIFIED")
        self.assertEqual(universe["records"], [])
        self.assertEqual(taxonomy["approval_status"], "UNRATIFIED")

        with self.assertRaisesRegex(
            MODULE.USLeadershipError, "LEADERSHIP_POLICY_UNRATIFIED"
        ):
            MODULE.build_transform(payload())

    def test_relative_strength_and_participation_are_reproduced(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = MODULE.build_transform(payload(), **ratified_inputs(tmp))

        self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
        self.assertEqual(result["benchmark_asset"], "SPY")
        assets = {
            item["asset"]: item for item in result["asset_relative_strength"]
        }
        self.assertEqual(list(assets), ["IWM", "QQQ", "SPY"])
        self.assertEqual(assets["SPY"]["cumulative_gross_return"], "0.729")
        self.assertEqual(assets["SPY"]["relative_strength_vs_benchmark"], "0")
        expected = Decimal("0.857375") / Decimal("0.729") - Decimal(1)
        self.assertEqual(
            assets["QQQ"]["relative_strength_vs_benchmark"],
            MODULE.render_decimal(expected, 12),
        )
        self.assertEqual(
            [
                point["outperformance_participation_fraction"]
                for point in result["daily_relative_participation"]
            ],
            ["0.5", "0.5", "0.5"],
        )
        groups = {
            item["group_id"]: item for item in result["group_relative_strength"]
        }
        self.assertEqual(groups["BROAD"]["cumulative_gross_return"], "0.6885")
        self.assertFalse(result["leader_classification_authorized"])
        self.assertFalse(result["ranking_authorized"])
        self.assertFalse(result["regime_score_authorized"])
        self.assertFalse(result["production_wiring_authorized"])
        self.assertFalse(result["trading_action_authorized"])

    def test_production_validator_recomputes_retained_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = MODULE.build_transform(payload(), **ratified_inputs(tmp))

        self.assertEqual(MODULE.validate_output(copy.deepcopy(result)), result)

        asset_drift = copy.deepcopy(result)
        asset_drift["asset_relative_strength"][0][
            "relative_strength_vs_benchmark"
        ] = "0.9"
        with self.assertRaisesRegex(
            MODULE.USLeadershipError, "OUTPUT_ASSET_RS_MISMATCH"
        ):
            MODULE.validate_output(asset_drift)

        group_drift = copy.deepcopy(result)
        group_drift["group_relative_strength"][0][
            "relative_strength_vs_benchmark"
        ] = "0.9"
        with self.assertRaisesRegex(
            MODULE.USLeadershipError, "OUTPUT_GROUP_RS_MISMATCH"
        ):
            MODULE.validate_output(group_drift)

        fraction_drift = copy.deepcopy(result)
        fraction_drift["daily_relative_participation"][0][
            "outperformance_participation_fraction"
        ] = "0.25"
        with self.assertRaisesRegex(
            MODULE.USLeadershipError, "OUTPUT_DAILY_FRACTION_MISMATCH"
        ):
            MODULE.validate_output(fraction_drift)

        minimum_drift = copy.deepcopy(result)
        minimum_drift["group_relative_strength"][0][
            "minimum_daily_member_count"
        ] += 1
        with self.assertRaisesRegex(
            MODULE.USLeadershipError, "OUTPUT_GROUP_MINIMUM_MISMATCH"
        ):
            MODULE.validate_output(minimum_drift)

    def test_relative_participation_is_not_market_trend_or_breadth(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = MODULE.build_transform(payload(), **ratified_inputs(tmp))

        # QQQ, SPY, and IWM all fall over the window. QQQ still participates
        # because it falls less than SPY. That is relative leadership, not
        # positive-return breadth or a market Trend direction.
        self.assertEqual(
            result["daily_relative_participation"][0][
                "outperforming_benchmark_count"
            ],
            1,
        )
        self.assertFalse(result["trend_direction_authorized"])
        self.assertFalse(result["breadth_direction_authorized"])
        self.assertFalse(result["threshold_authorized"])

    def test_forward_cutoff_and_historical_temporal_classes_stay_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp)
            with self.assertRaisesRegex(
                MODULE.USLeadershipError,
                "TEMPORAL_INPUT_NOT_QUALIFIED.*FETCH_BEFORE",
            ):
                MODULE.build_transform(
                    payload(fetched_at="2026-08-18T20:14:59-04:00"),
                    **inputs,
                )

            historical = MODULE.build_transform(
                payload(run_mode="HISTORICAL_BACKFILL"), **inputs
            )
            self.assertEqual(historical["status"], "CAUSAL_RESEARCH_ONLY")
            self.assertFalse(
                historical["temporal_eligibility"]["authoritative_historical_pit"]
            )

        with tempfile.TemporaryDirectory() as tmp:
            adjusted_inputs = ratified_inputs(tmp, price_basis="ADJUSTED")
            adjusted = MODULE.build_transform(
                payload(
                    run_mode="HISTORICAL_BACKFILL",
                    price_basis="ADJUSTED",
                ),
                **adjusted_inputs,
            )
            self.assertEqual(adjusted["status"], "REVISED_SENSITIVITY_ONLY")
            self.assertFalse(adjusted["regime_axis_input_authorized"])

    def test_each_session_uses_effective_membership_and_marks_partial_assets(self):
        prices = {
            "IWM": ["100", "80", "72", "64.8"],
            "QQQ": ["100", "95", "90.25", "85.7375"],
            "SPY": ["100", "90", "81", "72.9"],
            "XLE": ["100", "100", "110", "121"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            result = MODULE.build_transform(
                payload(prices), **ratified_inputs(tmp, include_xle=True)
            )

        self.assertEqual(
            [
                point["eligible_non_benchmark_count"]
                for point in result["daily_relative_participation"]
            ],
            [2, 3, 3],
        )
        self.assertEqual(
            result["partial_window_assets"],
            [
                {
                    "asset": "XLE",
                    "observed_session_count": 2,
                    "required_session_count": 3,
                    "reason": "not_present_in_every_point_in_time_universe",
                }
            ],
        )
        self.assertNotIn(
            "XLE", [item["asset"] for item in result["asset_relative_strength"]]
        )
        self.assertFalse(
            result["lineage"]["current_membership_backfill_authorized"]
        )

    def test_session_gap_duplicate_split_and_extra_asset_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp)
            missing = payload()
            del missing["asset_rows"][1]["rows"][2]
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "SESSION_COVERAGE_MISMATCH.*QQQ"
            ):
                MODULE.build_transform(missing, **inputs)

            duplicate = payload()
            duplicate["expected_session_dates"][1] = duplicate[
                "expected_session_dates"
            ][0]
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "SESSION_CALENDAR_INVALID"
            ):
                MODULE.build_transform(duplicate, **inputs)

            split = payload()
            split["asset_rows"][0]["rows"][1]["split_factor"] = "2"
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "SPLIT_EVENT_IN_WINDOW"
            ):
                MODULE.build_transform(split, **inputs)

            extra = payload(
                {
                    "IWM": ["100", "80", "72", "64.8"],
                    "QQQ": ["100", "95", "90.25", "85.7375"],
                    "SPY": ["100", "90", "81", "72.9"],
                    "VENDOR_EXTRA": ["1", "1", "1", "1"],
                }
            )
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "INPUT_ASSET_OUTSIDE_UNIVERSE"
            ):
                MODULE.build_transform(extra, **inputs)

    def test_taxonomy_overlap_gap_and_group_coverage_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            overlap = default_taxonomy() + [
                taxonomy_record("QQQ", ["GROWTH"])
            ]
            path = write_taxonomy(Path(tmp) / "overlap.json", overlap)
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "TAXONOMY_RANGE_OVERLAP.*QQQ"
            ):
                MODULE.load_taxonomy(path)

        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp)
            inputs["taxonomy_path"] = write_taxonomy(
                Path(tmp) / "missing.json", default_taxonomy()[:-1]
            )
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "TAXONOMY_MISSING.*SPY"
            ):
                MODULE.build_transform(payload(), **inputs)

        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp, groups=["NOT_PRESENT"])
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "GROUP_COVERAGE_INCOMPLETE"
            ):
                MODULE.build_transform(payload(), **inputs)

    def test_policy_mismatch_and_string_number_boundary_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp)
            with self.assertRaisesRegex(
                MODULE.USLeadershipError,
                "INPUT_POLICY_MISMATCH.*source_name",
            ):
                MODULE.build_transform(payload(source="another_vendor"), **inputs)

            numeric = payload()
            numeric["asset_rows"][0]["rows"][0]["close"] = 100.0
            with self.assertRaisesRegex(
                MODULE.USLeadershipError, "INPUT_NUMBER_MUST_BE_STRING"
            ):
                MODULE.build_transform(numeric, **inputs)

    def test_output_is_non_reconstructive_deterministic_and_atomic(self):
        sentinel = "123.456789123456789"
        prices = {
            "IWM": [sentinel] * 4,
            "QQQ": [sentinel] * 4,
            "SPY": [sentinel] * 4,
        }
        with tempfile.TemporaryDirectory() as tmp:
            inputs = ratified_inputs(tmp)
            source = payload(prices)
            first = MODULE.build_transform(source, **inputs)
            second = MODULE.build_transform(source, **inputs)
            output = Path(tmp) / "output" / "leadership.json"
            MODULE.write_output(first, output)
            rendered = output.read_text(encoding="utf-8")

            self.assertEqual(first, second)
            self.assertFalse(has_float(first))
            self.assertNotIn(sentinel, rendered)
            self.assertFalse(first["retention"]["vendor_rows_emitted"])
            self.assertFalse(first["retention"]["vendor_prices_emitted"])
            self.assertFalse(
                first["retention"]["reconstructive_series_emitted"]
            )
            self.assertEqual(json.loads(rendered), first)
            self.assertFalse(list(output.parent.glob(".*.tmp.*")))

    def test_no_network_workflow_or_tracked_factor_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )

        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertNotIn("subprocess", script)
        self.assertNotIn("TIINGO_API_KEY", script)
        self.assertNotIn("input_file", script)
        self.assertNotIn("us_leadership.py", workflows)
        self.assertNotIn("us_leadership", workflows)


if __name__ == "__main__":
    unittest.main()
