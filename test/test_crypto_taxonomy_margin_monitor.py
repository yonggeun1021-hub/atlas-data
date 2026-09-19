#!/usr/bin/env python3
"""Preventive crypto taxonomy classification-margin monitor regression.

The monitor must (a) measure the margin on the *production* eligibility
ranking, (b) raise an alarm when an asset climbs toward the scan stop,
(c) escalate severity automatically once `primary_30d` can latch as the
official LEADERSHIP window, and (d) never create or modify a
classification.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "crypto_taxonomy_margin_monitor.py"
WORKFLOW = ROOT / ".github" / "workflows" / "crypto-taxonomy-margin-monitor.yml"
MONITOR_POLICY = ROOT / "config" / "crypto_taxonomy_margin_monitor_v1.json"
REAL_RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
RUN_ALL = ROOT / "run_all.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MONITOR = _load("crypto_taxonomy_margin_monitor", SCRIPT)
FIXTURES = _load(
    "crypto_breadth_fixtures_for_margin_monitor", ROOT / "test" / "test_crypto_breadth.py"
)
CB = MONITOR.CB


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ──────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────

# Ranking is driven by trailing-30d turnover, which the shared breadth
# fixture derives from the first price element, so these prices *are* the
# rank order.  Rank 3 is excluded so the scan stop lands at rank 6 with
# five eligible assets selected.
BASELINE_PRICES = {
    "BTC": (1000, 1001, 1002),
    "Z02": (900, 901, 902),
    "Z03": (800, 801, 802),
    "Z04": (700, 701, 702),
    "Z05": (600, 601, 602),
    "Z06": (500, 501, 502),
    "Z07": (400, 401, 402),
    "Z08": (300, 301, 302),
    "Z09": (200, 201, 202),
    "Z10": (100, 101, 102),
    "Z11": (90, 91, 92),
    "Z12": (80, 81, 82),
}
# Z11 climbs past Z08/Z09/Z10 in a single day: rank 11 -> rank 8.
CLIMBED_PRICES = dict(BASELINE_PRICES) | {"Z11": (350, 351, 352)}
# Every asset except Z11 carries a ratified classification, so Z11 is the
# only unclassified candidate and the margin is unambiguous.
CATEGORIES = {
    asset: ("stablecoin" if asset == "Z03" else "eligible_crypto")
    for asset in BASELINE_PRICES
    if asset != "Z11"
}


def write_monitor_policy(
    path: Path,
    warning=3,
    critical=1,
    band=6,
    rate_warning="2",
    rate_critical="3",
    rate_lookback=7,
    minimum_observations=2,
    escalation=None,
) -> Path:
    payload = {
        "schema_version": 1,
        "policy_version": "crypto_taxonomy_margin_monitor/test-v1",
        "approval_status": "UNRATIFIED",
        "source_name": "kraken_spot_market_data",
        "purpose": "test fixture alarm policy",
        "lookahead_band_ranks": band,
        "margin_thresholds": {
            "warning_at_or_below": warning,
            "critical_at_or_below": critical,
        },
        "rate_thresholds": {
            "lookback_days": rate_lookback,
            "minimum_observations": minimum_observations,
            "warning_shrink_ranks_per_day_at_or_above": rate_warning,
            "critical_shrink_ranks_per_day_at_or_above": rate_critical,
        },
        "severity_ladder": [
            "NONE",
            "WARNING",
            "CRITICAL",
            "CRITICAL_ARMED",
            "GAP_OPEN",
        ],
        "post_arming_escalation": escalation
        or {
            "NONE": "NONE",
            "WARNING": "CRITICAL",
            "CRITICAL": "CRITICAL_ARMED",
            "CRITICAL_ARMED": "CRITICAL_ARMED",
            "GAP_OPEN": "GAP_OPEN",
        },
        "cost_model": {
            "pilot_window_id": "pilot_7d",
            "primary_window_id": "primary_30d",
            "gap_cost_days_pre_arming": 12,
            "gap_cost_days_post_arming": 35,
            "derivation": "test fixture derivation",
        },
        "reasoning": {
            key: "test fixture reasoning"
            for key in (
                "measured_basis",
                "margin_warning_at_or_below",
                "margin_critical_at_or_below",
                "rate_warning_shrink_ranks_per_day_at_or_above",
                "rate_critical_shrink_ranks_per_day_at_or_above",
                "rate_lookback_days",
                "rate_minimum_observations",
                "lookahead_band_ranks",
                "post_arming_escalation",
            )
        },
        "authority": {
            "taxonomy_authorized": False,
            "classification_authorized": False,
            "investability_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "threshold_authorized": False,
        },
    }
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return Path(path)


def write_recalc(root: Path, days: dict) -> Path:
    """Minimal committed recalc history: as_of -> point-in-time status."""
    root = Path(root)
    for as_of, status in sorted(days.items()):
        directory = root / as_of
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "point.json").write_text(
            json.dumps(
                {
                    "as_of_date": as_of,
                    "point_in_time": {
                        "status": status,
                        "unknown_reason": (
                            "TAXONOMY_COVERAGE_UNKNOWN"
                            if status == "UNKNOWN"
                            else None
                        ),
                    },
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    return root


class Bench:
    """One synthetic snapshot plus the policies the monitor reads."""

    def __init__(self, tmp: Path, prices=None, vintage="2026-08-20", target=5):
        self.tmp = Path(tmp)
        self.snapshot = FIXTURES.write_snapshot(
            self.tmp / "raw", vintage=vintage, prices=prices or BASELINE_PRICES
        )
        self.universe = FIXTURES.write_policy(self.tmp / "universe.json", target=target)
        self.taxonomy = FIXTURES.write_taxonomy(self.tmp / "taxonomy.json", CATEGORIES)
        self.monitor_policy = write_monitor_policy(self.tmp / "monitor.json")
        self.data_root = self.tmp / "data"
        self.recalc = write_recalc(
            self.tmp / "recalc", {"2026-08-10": "UNKNOWN"}
        )

    def kwargs(self, **overrides) -> dict:
        base = {
            "raw_root": self.snapshot.parent,
            "data_root": self.data_root,
            "monitor_policy_path": self.monitor_policy,
            "universe_policy_path": self.universe,
            "taxonomy_path": self.taxonomy,
            "recalc_root": self.recalc,
        }
        base.update(overrides)
        return base

    def build(self, **overrides) -> dict:
        return MONITOR.build_packet(self.snapshot.name, **self.kwargs(**overrides))

    def populate(self, **overrides) -> dict:
        return MONITOR.populate(self.snapshot.name, **self.kwargs(**overrides))


# ──────────────────────────────────────────────────────────────────────
# (a) the margin is computed from the production ranking
# ──────────────────────────────────────────────────────────────────────


class ProductionRankingTests(unittest.TestCase):
    def test_scan_stop_and_margin_come_from_the_production_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            record = bench.build()
            margin = record["margin"]
            # The production transform is an independent path into the same
            # scan; the monitor must agree with it on the cutoff population.
            transform = CB.build_transform(
                bench.snapshot,
                universe_policy_path=bench.universe,
                exclusion_taxonomy_path=bench.taxonomy,
            )
            universe = transform["universe"]
            derived_stop = (
                universe["selected_asset_count"]
                + len(universe["taxonomy_excluded_before_cutoff"])
                + len(universe["taxonomy_unknown_before_cutoff"])
            )
            self.assertEqual(margin["scan_stop_rank"], derived_stop)
            self.assertEqual(margin["scan_stop_rank"], 6)
            self.assertEqual(margin["selected_eligible_count"], 5)
            self.assertEqual(margin["excluded_before_cutoff_count"], 1)
            self.assertEqual(margin["taxonomy_unknown_count"], 0)
            self.assertEqual(
                margin["ranked_candidate_count"], universe["ranked_candidate_count"]
            )
            # The nearest unclassified asset is past the cutoff, so the
            # production scan never reports it -- which is exactly why a
            # detect-only gap packet cannot see it coming.
            self.assertEqual(margin["nearest_unclassified_rank"], 11)
            self.assertEqual(margin["nearest_unclassified_asset_id"], "Z11")
            self.assertEqual(margin["margin_ranks"], 5)
            self.assertNotIn(
                "Z11",
                {
                    row["canonical_asset_id"]
                    for row in universe["taxonomy_unknown_before_cutoff"]
                    + universe["taxonomy_excluded_before_cutoff"]
                },
            )
            self.assertEqual(
                margin["ranking_provenance"]["function"],
                "crypto_breadth.qualified_members",
            )

    def test_band_names_assets_with_rank_and_trailing_30d_turnover(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            margin = bench.build()["margin"]
            self.assertEqual(margin["lookahead_band_ranks"], 6)
            self.assertEqual(margin["lookahead_band_first_rank"], 7)
            self.assertEqual(margin["lookahead_band_last_rank"], 12)
            band = margin["lookahead_band_unclassified"]
            self.assertEqual([row["canonical_asset_id"] for row in band], ["Z11"])
            row = band[0]
            self.assertEqual(row["rank_before_taxonomy"], 11)
            self.assertEqual(row["ranks_past_scan_stop"], 5)
            self.assertEqual(row["pair_id"], "Z11/USD")
            # Turnover is the production ranking metric, rendered exactly as
            # the production member payload renders it.
            self.assertRegex(row["trailing_30d_usd_turnover"], r"^\d+(\.\d+)?$")
            self.assertGreater(float(row["trailing_30d_usd_turnover"]), 0.0)

    def test_band_is_configurable_and_excludes_assets_beyond_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            narrow = write_monitor_policy(Path(tmp) / "narrow.json", band=2)
            margin = bench.build(monitor_policy_path=narrow)["margin"]
            self.assertEqual(margin["lookahead_band_last_rank"], 8)
            self.assertEqual(margin["lookahead_band_unclassified"], [])
            # The margin itself is not clipped by the band.
            self.assertEqual(margin["margin_ranks"], 5)

    def test_a_ranking_that_diverges_from_production_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            real = CB.qualified_members

            def perturbed(core, universe_policy, taxonomy_policy):
                result = real(core, universe_policy, taxonomy_policy)
                rows = result["diagnostics"]["taxonomy_unknown_before_cutoff"]
                if len(rows) > 1:
                    # Only the full-enumeration call sees every candidate;
                    # corrupt one turnover so it no longer matches the
                    # ranking the real scan visited.
                    rows[0] = dict(rows[0]) | {"trailing_usd_turnover": "1"}
                    result["diagnostics"]["taxonomy_unknown_before_cutoff"] = rows
                return result

            with mock.patch.object(CB, "qualified_members", perturbed):
                with self.assertRaises(MONITOR.MarginMonitorError) as caught:
                    bench.build()
            self.assertIn("RANKING_DIVERGES_FROM_PRODUCTION", str(caught.exception))

    def test_a_truncated_enumeration_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            real = CB.qualified_members

            def truncated(core, universe_policy, taxonomy_policy):
                result = real(core, universe_policy, taxonomy_policy)
                rows = result["diagnostics"]["taxonomy_unknown_before_cutoff"]
                if len(rows) > 1:
                    result["diagnostics"]["taxonomy_unknown_before_cutoff"] = rows[:-1]
                return result

            with mock.patch.object(CB, "qualified_members", truncated):
                with self.assertRaises(MONITOR.MarginMonitorError) as caught:
                    bench.build()
            self.assertIn("RANKING_NOT_FULLY_ENUMERATED", str(caught.exception))

    def test_real_latest_snapshot_reports_its_actual_margin(self):
        dates = sorted(
            path.name for path in REAL_RAW_ROOT.iterdir() if path.is_dir()
        )
        self.assertTrue(dates, "no committed Kraken snapshot to measure")
        with tempfile.TemporaryDirectory() as tmp:
            record = MONITOR.build_packet(dates[-1], data_root=Path(tmp) / "data")
            margin = record["margin"]
            self.assertEqual(record["status"], "REVIEW_ONLY")
            self.assertGreaterEqual(margin["scan_stop_rank"], margin["target_asset_count"])
            self.assertLessEqual(
                margin["scan_stop_rank"], margin["ranked_candidate_count"]
            )
            self.assertIsNotNone(margin["nearest_unclassified_rank"])
            self.assertEqual(
                margin["margin_ranks"],
                margin["nearest_unclassified_rank"] - margin["scan_stop_rank"],
            )
            self.assertIn(
                record["alarm"]["severity"],
                MONITOR.load_monitor_policy()["severity_ladder"],
            )
            self.assertEqual(
                record["alarm"]["thresholds_applied"]["margin_critical_at_or_below"],
                MONITOR.load_monitor_policy()["_margin_critical"],
            )


# ──────────────────────────────────────────────────────────────────────
# (b) a synthetic asset climbing into the band raises the alarm
# ──────────────────────────────────────────────────────────────────────


class ClimbAlarmTests(unittest.TestCase):
    def test_an_asset_climbing_toward_the_scan_stop_raises_the_alarm(self):
        with tempfile.TemporaryDirectory() as tmp:
            calm = Bench(Path(tmp) / "calm")
            quiet = calm.build()
            self.assertEqual(quiet["margin"]["margin_ranks"], 5)
            self.assertEqual(quiet["alarm"]["severity"], "NONE")
            self.assertFalse(quiet["alarm"]["alarm_raised"])

            climbed = Bench(Path(tmp) / "climb", prices=CLIMBED_PRICES)
            loud = climbed.build()
            self.assertEqual(loud["margin"]["nearest_unclassified_rank"], 8)
            self.assertEqual(loud["margin"]["margin_ranks"], 2)
            self.assertTrue(loud["alarm"]["alarm_raised"])
            self.assertEqual(loud["alarm"]["severity"], "WARNING")
            self.assertIn("MARGIN_LEVEL", loud["alarm"]["drivers"])
            self.assertEqual(
                [
                    row["canonical_asset_id"]
                    for row in loud["margin"]["lookahead_band_unclassified"]
                ],
                ["Z11"],
            )

    def test_an_unclassified_asset_inside_the_scan_is_reported_as_gap_open(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Z11 climbs all the way to rank 2: it is now inside the scan and
            # the production outcome is TAXONOMY_COVERAGE_UNKNOWN.
            inside = Bench(
                Path(tmp),
                prices=dict(BASELINE_PRICES) | {"Z11": (950, 951, 952)},
            )
            record = inside.build()
            self.assertEqual(record["margin"]["taxonomy_unknown_count"], 1)
            self.assertEqual(record["margin"]["nearest_unclassified_rank"], 2)
            self.assertLessEqual(record["margin"]["margin_ranks"], 0)
            self.assertEqual(record["alarm"]["severity"], "GAP_OPEN")
            self.assertEqual(
                record["margin"]["source_outcome"]["unknown_reason"],
                "TAXONOMY_COVERAGE_UNKNOWN",
            )

    def test_the_shrink_rate_alarms_even_while_the_level_looks_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            data_root = tmp / "data"
            # Level thresholds are set so a margin of 2 is NOT an alarm; the
            # rate alone must raise CRITICAL.
            lenient = write_monitor_policy(
                tmp / "lenient.json", warning=1, critical=1, rate_critical="3"
            )
            day_one = Bench(tmp / "d1", vintage="2026-08-20")
            first = day_one.populate(
                data_root=data_root, monitor_policy_path=lenient
            )
            self.assertEqual(first["outcome"], "populated")

            day_two = Bench(tmp / "d2", vintage="2026-08-21", prices=CLIMBED_PRICES)
            second = MONITOR.build_packet(
                day_two.snapshot.name,
                **day_two.kwargs(data_root=data_root, monitor_policy_path=lenient),
            )
            trend = second["trend"]
            self.assertEqual(trend["status"], "DERIVED")
            self.assertEqual(trend["observation_count"], 2)
            self.assertEqual(trend["elapsed_days"], 1)
            self.assertEqual(trend["shrink_ranks"], 3)
            self.assertEqual(trend["shrink_ranks_per_day"], "3")
            self.assertEqual(second["alarm"]["severity_from_level"], "NONE")
            self.assertEqual(second["alarm"]["severity_from_rate"], "CRITICAL")
            self.assertEqual(second["alarm"]["severity"], "CRITICAL")
            self.assertIn("MARGIN_SHRINK_RATE", second["alarm"]["drivers"])
            self.assertEqual(
                trend["days_to_zero_margin_at_current_rate"], "0.666667"
            )

    def test_a_single_observation_reports_insufficient_history_not_a_slope(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            trend = bench.build()["trend"]
            self.assertEqual(trend["status"], "INSUFFICIENT_HISTORY")
            self.assertIsNone(trend["shrink_ranks_per_day"])
            self.assertEqual(trend["observation_count"], 1)

    def test_margin_history_is_append_only_and_per_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            first = bench.populate()
            packet = Path(first["path"])
            self.assertTrue(packet.is_file())
            before = packet.read_bytes()
            again = bench.populate()
            self.assertEqual(again["outcome"], "verified_existing")
            self.assertEqual(packet.read_bytes(), before)
            # One directory per source date: a later day never rewrites an
            # earlier day's observation.
            self.assertEqual(
                sorted(path.name for path in bench.data_root.iterdir()),
                [bench.snapshot.name],
            )

    def test_a_tampered_packet_fails_independent_revalidation(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            record = bench.build()
            tampered = json.loads(json.dumps(record))
            tampered["margin"]["margin_ranks"] = 99
            tampered["payload_sha256"] = MONITOR.payload_sha256(
                {k: v for k, v in tampered.items() if k != "payload_sha256"}
            )
            with self.assertRaises(MONITOR.MarginMonitorError) as caught:
                MONITOR.validate_packet(
                    tampered,
                    raw_root=bench.snapshot.parent,
                    monitor_policy_path=bench.monitor_policy,
                    universe_policy_path=bench.universe,
                    taxonomy_path=bench.taxonomy,
                )
            self.assertIn("PACKET_MARGIN_DRIFT_OR_TAMPER", str(caught.exception))


# ──────────────────────────────────────────────────────────────────────
# (c) after the switch-arm date the severity escalates
# ──────────────────────────────────────────────────────────────────────


class ArmingEscalationTests(unittest.TestCase):
    def test_countdown_is_the_latest_unknown_day_plus_the_primary_lookback(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            arming = bench.build()["arming"]
            self.assertEqual(arming["primary_lookback_calendar_days"], 30)
            self.assertEqual(arming["latest_taxonomy_unknown_as_of"], "2026-08-10")
            self.assertEqual(arming["earliest_primary_observed_as_of"], "2026-09-09")
            self.assertFalse(arming["already_armed"])
            self.assertEqual(
                arming["days_until_arming"],
                (dt.date(2026, 9, 9) - dt.date(2026, 8, 19)).days,
            )
            self.assertEqual(arming["official_window_id"], "pilot_7d")
            self.assertTrue(arming["switch_is_permanent"])

    def test_a_taxonomy_gap_today_restarts_the_countdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            inside = Bench(
                Path(tmp), prices=dict(BASELINE_PRICES) | {"Z11": (950, 951, 952)}
            )
            arming = inside.build()["arming"]
            # as_of is 2026-08-19 and today is itself an unknown day.
            self.assertEqual(arming["latest_taxonomy_unknown_as_of"], "2026-08-19")
            self.assertEqual(arming["earliest_primary_observed_as_of"], "2026-09-18")
            self.assertEqual(arming["days_until_arming"], 30)

    def test_severity_escalates_automatically_once_primary_can_latch(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            before = Bench(tmp / "before", prices=CLIMBED_PRICES)
            pre = before.build()
            self.assertFalse(pre["arming"]["already_armed"])
            self.assertEqual(pre["alarm"]["base_severity"], "WARNING")
            self.assertEqual(pre["alarm"]["severity"], "WARNING")
            self.assertFalse(pre["alarm"]["escalated_for_arming"])
            self.assertEqual(pre["gap_cost"]["applicable_days"], 12)

            after = Bench(tmp / "after", prices=CLIMBED_PRICES)
            # No unknown day in the committed history: primary_30d can
            # already resolve observed, so the switch has armed.
            armed_recalc = write_recalc(
                tmp / "armed_recalc", {"2026-07-01": "OBSERVED_UNCLASSIFIED"}
            )
            post = after.build(recalc_root=armed_recalc)
            self.assertTrue(post["arming"]["already_armed"])
            self.assertEqual(post["arming"]["days_until_arming"], 0)
            self.assertEqual(post["arming"]["official_window_id"], "primary_30d")
            # Same snapshot, same margin, higher severity -- mechanically.
            self.assertEqual(
                post["margin"]["margin_ranks"], pre["margin"]["margin_ranks"]
            )
            self.assertEqual(post["alarm"]["base_severity"], "WARNING")
            self.assertEqual(post["alarm"]["severity"], "CRITICAL")
            self.assertTrue(post["alarm"]["escalated_for_arming"])
            self.assertTrue(post["alarm"]["post_arming_escalation_applied"])
            # The cost of one unknown day is what changed, and it is derived,
            # not written down: 30 + MINIMUM_CONSECUTIVE_COMPLETE_DAYS.
            self.assertEqual(post["gap_cost"]["applicable_days"], 35)
            self.assertEqual(
                post["gap_cost"]["post_arming_days"],
                30 + MONITOR.MINIMUM_CONSECUTIVE_COMPLETE_DAYS,
            )

    def test_a_critical_margin_escalates_past_critical_after_arming(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bench = Bench(tmp / "bench", prices=CLIMBED_PRICES)
            strict = write_monitor_policy(tmp / "strict.json", warning=3, critical=2)
            armed_recalc = write_recalc(
                tmp / "armed_recalc", {"2026-07-01": "OBSERVED_UNCLASSIFIED"}
            )
            pre = bench.build(monitor_policy_path=strict)
            self.assertEqual(pre["alarm"]["severity"], "CRITICAL")
            post = bench.build(monitor_policy_path=strict, recalc_root=armed_recalc)
            self.assertEqual(post["alarm"]["severity"], "CRITICAL_ARMED")

    def test_an_escalation_table_that_does_not_escalate_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            flat = write_monitor_policy(
                Path(tmp) / "flat.json",
                escalation={
                    "NONE": "NONE",
                    "WARNING": "WARNING",
                    "CRITICAL": "CRITICAL_ARMED",
                    "CRITICAL_ARMED": "CRITICAL_ARMED",
                    "GAP_OPEN": "GAP_OPEN",
                },
            )
            with self.assertRaises(MONITOR.MarginMonitorError) as caught:
                MONITOR.load_monitor_policy(flat)
            self.assertIn("ESCALATION_NOT_AUTOMATIC", str(caught.exception))

    def test_committed_arming_countdown_matches_the_committed_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = MONITOR.build_packet(
                sorted(
                    path.name for path in REAL_RAW_ROOT.iterdir() if path.is_dir()
                )[-1],
                data_root=Path(tmp) / "data",
            )
        arming = record["arming"]
        self.assertEqual(arming["primary_lookback_calendar_days"], 30)
        self.assertEqual(arming["minimum_consecutive_complete_days"], 5)
        latest = arming["latest_taxonomy_unknown_as_of"]
        self.assertIsNotNone(latest)
        self.assertEqual(
            arming["earliest_primary_observed_as_of"],
            (dt.date.fromisoformat(latest) + dt.timedelta(days=30)).isoformat(),
        )
        # Days the committed evidence does not cover are disclosed, never
        # assumed clean.
        self.assertIsInstance(arming["unverified_clean_days"], list)
        self.assertEqual(
            arming["unverified_clean_day_count"], len(arming["unverified_clean_days"])
        )


# ──────────────────────────────────────────────────────────────────────
# (d) the tool cannot write a classification
# ──────────────────────────────────────────────────────────────────────


class NoClassificationAuthorityTests(unittest.TestCase):
    def test_every_authority_flag_is_false_and_no_classification_is_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            record = bench.build()
            self.assertEqual(record["status"], "REVIEW_ONLY")
            authority = record["authority"]
            self.assertEqual(authority["classifications_created"], 0)
            self.assertEqual(authority["records_ratified"], 0)
            self.assertFalse(authority["taxonomy_authorized"])
            for field, value in sorted(authority.items()):
                self.assertIn(value, (False, 0), field)
            self.assertIn(
                "CRYPTO_BREADTH_EXCLUSION_TAXONOMY", record["not_applied_to"]
            )

    def test_populate_leaves_every_taxonomy_and_policy_input_byte_identical(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            watched = [bench.taxonomy, bench.universe, bench.monitor_policy]
            before = {path: file_sha256(path) for path in watched}
            bench.populate()
            for path in watched:
                self.assertEqual(before[path], file_sha256(path), str(path))

    def test_populate_succeeds_with_every_classification_input_read_only(self):
        """A classification write would fail; the monitor must not attempt one."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            bench = Bench(tmp / "bench")
            frozen = tmp / "frozen"
            frozen.mkdir()
            taxonomy = frozen / "taxonomy.json"
            universe = frozen / "universe.json"
            shutil.copy2(bench.taxonomy, taxonomy)
            shutil.copy2(bench.universe, universe)
            for path in (taxonomy, universe):
                os.chmod(path, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            os.chmod(frozen, stat.S_IRUSR | stat.S_IXUSR)
            try:
                result = bench.populate(
                    taxonomy_path=taxonomy, universe_policy_path=universe
                )
                self.assertEqual(result["outcome"], "populated")
                self.assertEqual(sorted(path.name for path in frozen.iterdir()),
                                 ["taxonomy.json", "universe.json"])
            finally:
                os.chmod(frozen, stat.S_IRWXU)
                for path in (taxonomy, universe):
                    os.chmod(path, stat.S_IRWXU)

    def test_populate_writes_nothing_outside_the_observation_data_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            bench = Bench(Path(tmp))
            watched = sorted((ROOT / "config").glob("*.json"))
            self.assertTrue(watched)
            before = {path: file_sha256(path) for path in watched}
            bench.populate()
            for path in watched:
                self.assertEqual(before[path], file_sha256(path), str(path))
            written = sorted(
                str(path.relative_to(bench.data_root))
                for path in bench.data_root.rglob("*")
                if path.is_file()
            )
            self.assertEqual(written, [f"{bench.snapshot.name}/packet.json"])

    def test_a_policy_claiming_authority_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_monitor_policy(Path(tmp) / "bad.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["authority"]["taxonomy_authorized"] = True
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
            with self.assertRaises(MONITOR.MarginMonitorError) as caught:
                MONITOR.load_monitor_policy(path)
            self.assertIn("AUTHORITY_NOT_FALSE", str(caught.exception))


# ──────────────────────────────────────────────────────────────────────
# Thresholds as data, fail-closed policy, workflow and registration
# ──────────────────────────────────────────────────────────────────────


class PolicyAndWiringTests(unittest.TestCase):
    def test_committed_alarm_policy_records_thresholds_and_reasoning(self):
        policy = MONITOR.load_monitor_policy()
        self.assertEqual(policy["_margin_warning"], 25)
        self.assertEqual(policy["_margin_critical"], 12)
        self.assertEqual(policy["_band"], 40)
        self.assertEqual(str(policy["_rate_warning"]), "2")
        self.assertEqual(str(policy["_rate_critical"]), "5")
        self.assertEqual(policy["_pre_cost"], 12)
        self.assertEqual(policy["_post_cost"], 35)
        for key, value in sorted(policy["reasoning"].items()):
            self.assertGreater(len(value), 60, key)

    def test_thresholds_live_in_the_policy_file_not_in_the_module(self):
        source = SCRIPT.read_text(encoding="utf-8")
        for literal in ("25", "12", "35"):
            # Threshold values must never appear as comparison literals.
            self.assertNotIn(f"<= {literal}", source)
            self.assertNotIn(f">= {literal}", source)
        self.assertIn("policy[\"_margin_warning\"]", source)
        self.assertIn("policy[\"_margin_critical\"]", source)

    def test_a_missing_or_malformed_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaises(MONITOR.MarginMonitorError):
                MONITOR.load_monitor_policy(tmp / "absent.json")
            path = write_monitor_policy(tmp / "policy.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            del payload["margin_thresholds"]
            path.write_text(json.dumps(payload) + "\n", encoding="utf-8")
            with self.assertRaises(MONITOR.MarginMonitorError):
                MONITOR.load_monitor_policy(path)
            inverted = write_monitor_policy(tmp / "inverted.json", warning=2, critical=5)
            with self.assertRaises(MONITOR.MarginMonitorError):
                MONITOR.load_monitor_policy(inverted)

    def test_a_cost_model_that_disagrees_with_production_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = write_monitor_policy(Path(tmp) / "policy.json")
            payload = json.loads(path.read_text(encoding="utf-8"))
            payload["cost_model"]["gap_cost_days_post_arming"] = 34
            path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                            encoding="utf-8")
            policy = MONITOR.load_monitor_policy(path)
            arming = {
                "pilot_lookback_calendar_days": 7,
                "primary_lookback_calendar_days": 30,
                "already_armed": False,
            }
            with self.assertRaises(MONITOR.MarginMonitorError) as caught:
                MONITOR.gap_cost_days(policy, arming)
            self.assertIn("COST_MODEL_DRIFT", str(caught.exception))

    def test_source_date_traversal_is_rejected(self):
        for bad in ("../../etc", "2026-9-18", "2026-09-18/..", ""):
            with self.assertRaises(MONITOR.MarginMonitorError):
                MONITOR.output_path(bad, Path("/tmp"))

    def test_workflow_is_scheduled_and_writes_no_classification(self):
        # Until 2026-09-19 this test asserted the opposite -- that the workflow
        # carried no `schedule:`, because its header claimed the daily run was
        # "registered with the server dispatcher separately". It was not
        # registered with the dispatcher at all (absent from both
        # /etc/atlas-schedule-dispatcher configs), so the monitor had never run
        # on any cadence: its only runs ever were two pull_request runs on
        # 2026-09-18. The cadence now lives in this file and this test pins it.
        import yaml

        self.assertTrue(WORKFLOW.is_file(), str(WORKFLOW))
        text = WORKFLOW.read_text(encoding="utf-8")
        parsed = yaml.safe_load(text)
        # GitHub's `on:` key parses as the YAML boolean True.
        triggers = parsed.get("on") or parsed.get(True)
        self.assertEqual(
            sorted(triggers), ["pull_request", "schedule", "workflow_dispatch"]
        )
        self.assertEqual(
            [entry["cron"] for entry in triggers["schedule"]], ["30 3 * * *"]
        )
        # The monitor has no cutoff of its own, so it is deliberately NOT on the
        # server dispatcher: that machinery exists for captures which claim an
        # append-only date directory before a hard cutoff. Re-registering it
        # there requires a server change, so the header must not start claiming
        # dispatcher coverage again -- a false claim of coverage is worse than
        # an admitted gap.
        # The distinguishing clause of the old, false claim. The header may
        # still quote the claim in order to refute it (it does), so this pins
        # the assertive form rather than the words themselves.
        self.assertNotIn("so the cadence stays in one place", text)
        # And the header must keep saying where the absence was verified.
        self.assertIn("/etc/atlas-schedule-dispatcher", text)
        # The scheduled run must be able to go red; a monitor that can only be
        # green is not watching anything. pull_request runs stay report-only.
        job_env = parsed["jobs"]["classification-margin"]["env"]
        self.assertIn("SCHEDULED_FAIL_ON", job_env)
        self.assertIn("github.event_name == 'schedule'", job_env["SCHEDULED_FAIL_ON"])
        for severity in ("GAP_OPEN", "CRITICAL_ARMED"):
            self.assertIn(severity, job_env["SCHEDULED_FAIL_ON"])
        self.assertIn('--fail-on-severity "${SCHEDULED_FAIL_ON}"', text)
        # pipefail is what lets the monitor's exit code survive the `tee`;
        # without it --fail-on-severity could never fail the run.
        self.assertIn("set -euo pipefail", text)
        self.assertIn("permissions:\n  contents: read", text)
        self.assertIn(".github/scripts/crypto_taxonomy_margin_monitor.py", text)
        self.assertIn("test/test_crypto_taxonomy_margin_monitor.py", text)
        self.assertIn("git diff --exit-code", text)
        for forbidden in (
            "config/crypto_breadth_exclusion_taxonomy.json",
            "git push",
            "git commit",
        ):
            self.assertNotIn(forbidden, text)

    def test_workflow_actions_use_the_contract_immutable_pins(self):
        contract = json.loads(
            (ROOT / "config" / "github_actions_runtime_contract.json").read_text(
                encoding="utf-8"
            )
        )["actions"]
        uses = [
            line.split("uses:", 1)[1].split("#", 1)[0].strip()
            for line in WORKFLOW.read_text(encoding="utf-8").splitlines()
            if "uses:" in line and not line.lstrip().startswith("#")
        ]
        self.assertTrue(uses)
        for ref in uses:
            action = ref.split("@", 1)[0]
            self.assertIn(action, contract, ref)
            self.assertEqual(ref, f"{action}@{contract[action]['commit_sha']}")

    def test_this_regression_is_registered_in_run_all(self):
        self.assertIn(
            '"test/test_crypto_taxonomy_margin_monitor.py"',
            RUN_ALL.read_text(encoding="utf-8"),
        )


if __name__ == "__main__":
    unittest.main()
