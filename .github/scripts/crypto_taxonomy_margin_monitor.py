#!/usr/bin/env python3
"""Preventive crypto taxonomy classification-margin monitor.

The daily `crypto_taxonomy_gap` inventory reports a taxonomy gap the day
*after* the snapshot that already contains it.  This monitor reports the
distance that still separates the production eligibility scan from the
nearest unclassified asset, so the classification queue can be worked
before a `TAXONOMY_COVERAGE_UNKNOWN` day exists at all.

Why the distance matters, measured, not assumed:

  * The crypto LEADERSHIP axis switches permanently from `pilot_7d` to
    `primary_30d` the first time `primary_30d` resolves observed --
    `select_leadership_window` latches
    `official = PRIMARY if (primary_observed_earlier or primary_now)`
    and there is no fallback to pilot.
  * After that latch, one `TAXONOMY_COVERAGE_UNKNOWN` day sits in the
    30-day window for 30 successive as_of dates and then needs a
    `MINIMUM_CONSECUTIVE_COMPLETE_DAYS` re-acceptance run: ~35 days.
  * Every taxonomy gap observed between 2026-08-28 and 2026-09-07 was an
    already-listed asset climbing in trailing-30d USD turnover, not a new
    listing.  `ranking_lookback_finalized_days` + `ranking_history_complete`
    give 30 days of warning for brand-new listings only, so a climb is
    exactly the case nothing currently warns about.

The margin is measured on the *production* ranking.  This module never
builds a ranking of its own: it calls
`crypto_breadth.qualified_members()` twice over the same
already-captured snapshot --

  1. with the ratified taxonomy, to get the real scan outcome and the
     rank the scan stopped at, and
  2. with an in-memory taxonomy view whose classification records are
     empty, which makes the production scan enumerate every ranked
     candidate (nothing is ever selected, so the Top-N `break` cannot
     fire) and report each one's `rank_before_taxonomy` and
     `trailing_usd_turnover`.

The second call reads no file and writes nothing; it is the same ranking
code path, run with a view that classifies nothing.  Runtime invariants
require the two calls to agree on every rank the real scan visited and on
exactly which of those ranks are unclassified, so a margin can never be
reported from a ranking that differs from production's.

⛔ This tool creates no classification, ratification, investability,
Stage, threshold, or trading right.  It reports a queue; it does not
decide one.  Every authority flag is False and the record status is
`REVIEW_ONLY`.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import importlib.util
import json
import os
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "crypto_taxonomy_margin"
RECALC_ROOT = ROOT / "evidence" / "rotation" / "crypto_30d_coverage_recalc"
MONITOR_POLICY_PATH = ROOT / "config" / "crypto_taxonomy_margin_monitor_v1.json"
SCHEMA_VERSION = "crypto_taxonomy_margin_monitor/1"
TAXONOMY_COVERAGE_UNKNOWN = "TAXONOMY_COVERAGE_UNKNOWN"
RATE_PLACES = 6
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load_module("crypto_breadth_for_margin_monitor", ".github/scripts/crypto_breadth.py")
CL = _load_module("crypto_leadership_for_margin_monitor", ".github/scripts/crypto_leadership.py")

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# Read-only import: the re-acceptance run length is the production constant,
# never a number copied into this file.
from regime.crypto_paper_runtime import (  # noqa: E402
    MINIMUM_CONSECUTIVE_COMPLETE_DAYS,
    PILOT,
    PRIMARY,
)


class MarginMonitorError(ValueError):
    """Fail-closed classification-margin monitor violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def validate_source_date(source_date: str) -> dt.date:
    if not isinstance(source_date, str) or ISO_DATE.fullmatch(source_date) is None:
        raise MarginMonitorError(f"SOURCE_DATE_INVALID:{source_date!r}")
    try:
        parsed = dt.date.fromisoformat(source_date)
    except ValueError as exc:
        raise MarginMonitorError(f"SOURCE_DATE_INVALID:{source_date!r}") from exc
    if parsed.isoformat() != source_date:
        raise MarginMonitorError(f"SOURCE_DATE_INVALID:{source_date!r}")
    return parsed


def output_path(source_date: str, data_root: Path = DATA_ROOT) -> Path:
    validate_source_date(source_date)
    return Path(data_root) / source_date / "packet.json"


def source_ref(path: Path) -> str:
    resolved = Path(path).resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return f"external_fixture/{resolved.name}"


# ──────────────────────────────────────────────────────────────────────
# Alarm policy (thresholds are data, never literals in this module)
# ──────────────────────────────────────────────────────────────────────


def _decimal(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise MarginMonitorError(f"MONITOR_POLICY_INVALID:{label}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise MarginMonitorError(f"MONITOR_POLICY_INVALID:{label}") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise MarginMonitorError(f"MONITOR_POLICY_INVALID:{label}")
    return parsed


def _positive_int(value: object, label: str) -> int:
    if type(value) is not int or value < 1:
        raise MarginMonitorError(f"MONITOR_POLICY_INVALID:{label}")
    return value


def load_monitor_policy(path: Path = MONITOR_POLICY_PATH) -> dict:
    """Load and fail-closed validate the alarm policy.

    Every threshold, the severity ladder and the post-arming escalation
    table live in this file.  Nothing in this module falls back to a
    default when the policy is missing a field.
    """
    path = Path(path)
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise MarginMonitorError(f"MONITOR_POLICY_UNREADABLE:{exc}") from exc
    try:
        policy = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MarginMonitorError(f"MONITOR_POLICY_INVALID:json:{exc}") from exc
    expected = {
        "schema_version",
        "policy_version",
        "approval_status",
        "source_name",
        "purpose",
        "lookahead_band_ranks",
        "margin_thresholds",
        "rate_thresholds",
        "severity_ladder",
        "post_arming_escalation",
        "cost_model",
        "reasoning",
        "authority",
    }
    if not isinstance(policy, dict) or set(policy) != expected:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:schema")
    if (
        policy["schema_version"] != 1
        or not isinstance(policy["policy_version"], str)
        or not policy["policy_version"].strip()
        or policy["approval_status"] not in {"UNRATIFIED", "RATIFIED"}
        or policy["source_name"] != "kraken_spot_market_data"
        or not isinstance(policy["purpose"], str)
        or not policy["purpose"].strip()
    ):
        raise MarginMonitorError("MONITOR_POLICY_INVALID:header")

    # The monitor has no authority of any kind; a policy that claims one is
    # rejected rather than honoured.
    authority = policy["authority"]
    if not isinstance(authority, dict) or not authority:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:authority")
    for field, value in sorted(authority.items()):
        if value is not False:
            raise MarginMonitorError(f"MONITOR_POLICY_AUTHORITY_NOT_FALSE:{field}")

    band = _positive_int(policy["lookahead_band_ranks"], "lookahead_band_ranks")

    margin = policy["margin_thresholds"]
    if not isinstance(margin, dict) or set(margin) != {
        "warning_at_or_below",
        "critical_at_or_below",
    }:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:margin_thresholds")
    warning_level = _positive_int(margin["warning_at_or_below"], "warning_at_or_below")
    critical_level = _positive_int(
        margin["critical_at_or_below"], "critical_at_or_below"
    )
    if critical_level > warning_level:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:margin_threshold_order")

    rate = policy["rate_thresholds"]
    if not isinstance(rate, dict) or set(rate) != {
        "lookback_days",
        "minimum_observations",
        "warning_shrink_ranks_per_day_at_or_above",
        "critical_shrink_ranks_per_day_at_or_above",
    }:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:rate_thresholds")
    rate_lookback = _positive_int(rate["lookback_days"], "rate_lookback_days")
    minimum_observations = _positive_int(
        rate["minimum_observations"], "rate_minimum_observations"
    )
    if minimum_observations < 2:
        # A per-day rate cannot be derived from a single point.
        raise MarginMonitorError("MONITOR_POLICY_INVALID:rate_minimum_observations")
    warning_rate = _decimal(
        rate["warning_shrink_ranks_per_day_at_or_above"], "warning_rate"
    )
    critical_rate = _decimal(
        rate["critical_shrink_ranks_per_day_at_or_above"], "critical_rate"
    )
    if critical_rate < warning_rate:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:rate_threshold_order")

    ladder = policy["severity_ladder"]
    if (
        not isinstance(ladder, list)
        or len(ladder) != len(set(ladder))
        or any(not isinstance(item, str) or not item for item in ladder)
        or ladder[0] != "NONE"
    ):
        raise MarginMonitorError("MONITOR_POLICY_INVALID:severity_ladder")
    required_levels = {"NONE", "WARNING", "CRITICAL", "CRITICAL_ARMED", "GAP_OPEN"}
    if set(ladder) != required_levels:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:severity_ladder_levels")

    escalation = policy["post_arming_escalation"]
    if not isinstance(escalation, dict) or set(escalation) != set(ladder):
        raise MarginMonitorError("MONITOR_POLICY_INVALID:post_arming_escalation")
    rank = {level: index for index, level in enumerate(ladder)}
    for level, escalated in sorted(escalation.items()):
        if escalated not in rank or rank[escalated] < rank[level]:
            raise MarginMonitorError(
                f"MONITOR_POLICY_INVALID:post_arming_escalation:{level}"
            )
    # The whole point of the arming date is that the same margin cannot
    # carry the same severity on both sides of it.
    if escalation["WARNING"] == "WARNING" or escalation["CRITICAL"] == "CRITICAL":
        raise MarginMonitorError("MONITOR_POLICY_ESCALATION_NOT_AUTOMATIC")

    cost = policy["cost_model"]
    if not isinstance(cost, dict) or set(cost) != {
        "pilot_window_id",
        "primary_window_id",
        "gap_cost_days_pre_arming",
        "gap_cost_days_post_arming",
        "derivation",
    }:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:cost_model")
    if cost["pilot_window_id"] != PILOT or cost["primary_window_id"] != PRIMARY:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:cost_model_window_ids")
    pre_cost = _positive_int(cost["gap_cost_days_pre_arming"], "gap_cost_pre")
    post_cost = _positive_int(cost["gap_cost_days_post_arming"], "gap_cost_post")
    if post_cost <= pre_cost:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:cost_model_order")
    if not isinstance(cost["derivation"], str) or not cost["derivation"].strip():
        raise MarginMonitorError("MONITOR_POLICY_INVALID:cost_model_derivation")

    reasoning = policy["reasoning"]
    required_reasoning = {
        "measured_basis",
        "margin_warning_at_or_below",
        "margin_critical_at_or_below",
        "rate_warning_shrink_ranks_per_day_at_or_above",
        "rate_critical_shrink_ranks_per_day_at_or_above",
        "rate_lookback_days",
        "rate_minimum_observations",
        "lookahead_band_ranks",
        "post_arming_escalation",
    }
    if not isinstance(reasoning, dict) or set(reasoning) != required_reasoning:
        raise MarginMonitorError("MONITOR_POLICY_INVALID:reasoning")
    for field, value in sorted(reasoning.items()):
        if not isinstance(value, str) or not value.strip():
            raise MarginMonitorError(f"MONITOR_POLICY_INVALID:reasoning:{field}")

    return policy | {
        "_path": Path(path),
        "_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "_band": band,
        "_margin_warning": warning_level,
        "_margin_critical": critical_level,
        "_rate_lookback": rate_lookback,
        "_rate_minimum_observations": minimum_observations,
        "_rate_warning": warning_rate,
        "_rate_critical": critical_rate,
        "_ladder": list(ladder),
        "_rank": rank,
        "_escalation": dict(escalation),
        "_pre_cost": pre_cost,
        "_post_cost": post_cost,
    }


# ──────────────────────────────────────────────────────────────────────
# Production ranking reuse
# ──────────────────────────────────────────────────────────────────────


def unclassified_view(taxonomy_policy: dict) -> dict:
    """An in-memory view of the ratified taxonomy that classifies nothing.

    Only `_records_by_asset` is emptied: the policy header, ratification
    status and effective date are the ratified ones, so the production
    scan runs its normal ratification gate.  With no asset classified the
    Top-N `break` can never fire, which is what makes the production scan
    enumerate the complete ranking instead of stopping at the cutoff.
    """
    if "_records_by_asset" not in taxonomy_policy:
        raise MarginMonitorError("TAXONOMY_VIEW_UNSUPPORTED")
    return dict(taxonomy_policy) | {"_records_by_asset": {}}


def scan_stop_rank(diagnostics: dict) -> int:
    """The rank the production eligibility scan stopped at.

    Every rank from 1 to the stop is visited exactly once and lands in
    exactly one of selected / excluded / unknown, so the stop is their
    sum.  `qualified_members` exposes the selected count under a
    different key per outcome; a missing count fails closed.
    """
    for key in (
        "selected_asset_count",
        "known_eligible_count_so_far",
        "known_eligible_count",
    ):
        value = diagnostics.get(key)
        if type(value) is int:
            selected = value
            break
    else:
        raise MarginMonitorError("SCAN_STOP_UNDERIVABLE")
    excluded = diagnostics["taxonomy_excluded_before_cutoff"]
    unknown = diagnostics["taxonomy_unknown_before_cutoff"]
    stop = selected + len(excluded) + len(unknown)
    ranked = diagnostics["ranked_candidate_count"]
    if not 1 <= stop <= ranked:
        raise MarginMonitorError(f"SCAN_STOP_OUT_OF_RANGE:{stop}/{ranked}")
    for row in list(excluded) + list(unknown):
        if not 1 <= row["rank_before_taxonomy"] <= stop:
            raise MarginMonitorError(
                f"SCAN_STOP_INCONSISTENT:{row['canonical_asset_id']}"
            )
    return stop


def production_ranking(
    snapshot_dir: Path,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    """Run the production eligibility scan twice over one snapshot.

    Returns the real scan outcome, the rank it stopped at, and the
    complete ranking it ranked over -- all from
    `crypto_breadth.qualified_members`.
    """
    snapshot_dir = Path(snapshot_dir)
    if not snapshot_dir.is_dir():
        raise MarginMonitorError(f"RAW_BUNDLE_MISSING:{snapshot_dir.name}")
    contract = CB.load_contract()
    core = CB.source_core(
        snapshot_dir, contract=contract, identity_exceptions_path=identity_path
    )
    universe_policy = CB.load_universe_policy(universe_policy_path)
    taxonomy_policy = CB.load_exclusion_taxonomy(taxonomy_path)
    as_of = core["vintage"] - dt.timedelta(days=1)

    real = CB.qualified_members(core, universe_policy, taxonomy_policy)
    probe = CB.qualified_members(
        core, universe_policy, unclassified_view(taxonomy_policy)
    )
    real_diagnostics = real["diagnostics"]
    probe_diagnostics = probe["diagnostics"]

    ranked_count = real_diagnostics["ranked_candidate_count"]
    if probe_diagnostics["ranked_candidate_count"] != ranked_count:
        raise MarginMonitorError("RANKING_POPULATION_MISMATCH")
    rows = probe_diagnostics["taxonomy_unknown_before_cutoff"]
    if len(rows) != ranked_count:
        raise MarginMonitorError("RANKING_NOT_FULLY_ENUMERATED")
    by_rank = {}
    for row in rows:
        by_rank[row["rank_before_taxonomy"]] = row
    if sorted(by_rank) != list(range(1, ranked_count + 1)):
        raise MarginMonitorError("RANKING_RANKS_NOT_CONTIGUOUS")

    stop = scan_stop_rank(real_diagnostics)

    # Every rank the real scan visited must be the same asset with the
    # same turnover in the enumerated ranking, or the margin would be
    # measured against a different ranking than production's.
    visited = (
        list(real_diagnostics["taxonomy_excluded_before_cutoff"])
        + list(real_diagnostics["taxonomy_unknown_before_cutoff"])
        + [
            {
                "rank_before_taxonomy": item["rank_before_taxonomy"],
                "canonical_asset_id": item["canonical_asset_id"],
                "trailing_usd_turnover": CB.render_decimal(
                    item["series"]["trailing_usd_turnover"], 12
                ),
            }
            for item in real["members"]
        ]
    )
    if not visited and real["status"] == "OBSERVED_UNCLASSIFIED":
        # A completed scan always reports its selected members, so an empty
        # cross-check set means the production output shape changed.
        raise MarginMonitorError("PRODUCTION_SCAN_VISITED_NOTHING")
    for row in visited:
        rank = row["rank_before_taxonomy"]
        mirror = by_rank.get(rank)
        if (
            mirror is None
            or mirror["canonical_asset_id"] != row["canonical_asset_id"]
            or mirror["trailing_usd_turnover"] != row["trailing_usd_turnover"]
        ):
            raise MarginMonitorError(f"RANKING_DIVERGES_FROM_PRODUCTION:{rank}")

    # Which ranks are unclassified is decided by the ratified taxonomy, and
    # for ranks the real scan reached the two calls must name exactly the
    # same set.
    unclassified = []
    for rank in range(1, ranked_count + 1):
        row = by_rank[rank]
        if (
            CB.taxonomy_category(row["canonical_asset_id"], as_of, taxonomy_policy)
            is None
        ):
            unclassified.append(row)
    inside = {row["rank_before_taxonomy"] for row in unclassified if row["rank_before_taxonomy"] <= stop}
    reported = {
        row["rank_before_taxonomy"]
        for row in real_diagnostics["taxonomy_unknown_before_cutoff"]
    }
    if inside != reported:
        raise MarginMonitorError("UNCLASSIFIED_SET_DIVERGES_FROM_PRODUCTION")

    return {
        "core": core,
        "as_of": as_of,
        "universe_policy": universe_policy,
        "taxonomy_policy": taxonomy_policy,
        "real": real,
        "scan_stop_rank": stop,
        "ranked_candidate_count": ranked_count,
        "ranking_by_rank": by_rank,
        "unclassified": unclassified,
        "verified_production_rank_count": len(visited),
        "manifest": CB.validate_manifest(core, snapshot_dir),
        "manifest_sha256": CB.file_sha256(snapshot_dir / "_manifest.json"),
    }


# ──────────────────────────────────────────────────────────────────────
# Leadership-window arming
# ──────────────────────────────────────────────────────────────────────


def leadership_window_lookbacks(
    leadership_policy_path: Path = CL.LEADERSHIP_POLICY_PATH,
) -> dict:
    """Ratified pilot/primary lookback lengths, read from the policy."""
    policy = CL.load_leadership_policy(leadership_policy_path)
    CL.require_ratified_leadership_policy(policy)
    lookbacks = {}
    for window in policy["windows"]:
        lookbacks[window["window_id"]] = window["lookback_calendar_days"]
    for window_id in (PILOT, PRIMARY):
        if window_id not in lookbacks:
            raise MarginMonitorError(f"LEADERSHIP_WINDOW_MISSING:{window_id}")
    return {
        "policy_version": policy["policy_version"],
        "policy_sha256": CB.file_sha256(Path(leadership_policy_path)),
        "pilot_lookback_calendar_days": lookbacks[PILOT],
        "primary_lookback_calendar_days": lookbacks[PRIMARY],
    }


def recalc_unknown_days(recalc_root: Path = RECALC_ROOT) -> dict:
    """Point-in-time taxonomy-unknown days from the committed recalc evidence.

    This is the committed record of which days were
    `TAXONOMY_COVERAGE_UNKNOWN` at the time they were observed; it is what
    decides when the 30-day primary window can first contain no unknown
    day.  A malformed committed point fails closed rather than being
    silently read as clean.
    """
    recalc_root = Path(recalc_root)
    unknown_days, covered = [], []
    if not recalc_root.is_dir():
        raise MarginMonitorError("RECALC_HISTORY_MISSING")
    for directory in sorted(recalc_root.iterdir()):
        point = directory / "point.json"
        if not point.is_file():
            continue
        try:
            record = json.loads(point.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MarginMonitorError(f"RECALC_POINT_UNREADABLE:{directory.name}") from exc
        as_of = record.get("as_of_date")
        pit = record.get("point_in_time")
        if not isinstance(as_of, str) or not isinstance(pit, dict):
            raise MarginMonitorError(f"RECALC_POINT_INVALID:{directory.name}")
        day = validate_source_date(as_of)
        covered.append(day)
        status = pit.get("status")
        if status == "UNKNOWN" and pit.get("unknown_reason") == TAXONOMY_COVERAGE_UNKNOWN:
            unknown_days.append(day)
        elif status not in {"UNKNOWN", "OBSERVED_UNCLASSIFIED"}:
            raise MarginMonitorError(f"RECALC_POINT_STATUS_INVALID:{directory.name}")
    if not covered:
        raise MarginMonitorError("RECALC_HISTORY_EMPTY")
    return {
        "unknown_days": sorted(set(unknown_days)),
        "covered_days": sorted(set(covered)),
    }


def arming_state(
    as_of: dt.date,
    taxonomy_unknown_today: int,
    history: list,
    recalc_root: Path = RECALC_ROOT,
    leadership_policy_path: Path = CL.LEADERSHIP_POLICY_PATH,
) -> dict:
    """When `primary_30d` can first resolve observed, and whether it has.

    The primary window covering as_of A is [A - (lookback - 1), A].  A
    taxonomy-unknown day U therefore leaves the window at A = U + lookback,
    so the earliest as_of at which `primary_30d` can resolve observed --
    and permanently latch as the official LEADERSHIP window -- is the
    latest known unknown day plus the ratified primary lookback.

    Days the committed recalc evidence does not cover and no committed
    margin observation covers are reported as `unverified_clean_days`
    rather than assumed clean: the countdown is conditional on them.
    """
    windows = leadership_window_lookbacks(leadership_policy_path)
    primary_lookback = windows["primary_lookback_calendar_days"]
    recalc = recalc_unknown_days(recalc_root)

    monitor_unknown = [
        entry["as_of_date"]
        for entry in history
        if entry["taxonomy_unknown_count"] > 0
    ]
    monitor_covered = [entry["as_of_date"] for entry in history]
    unknown_days = set(recalc["unknown_days"]) | {
        dt.date.fromisoformat(day) for day in monitor_unknown
    }
    if taxonomy_unknown_today > 0:
        unknown_days.add(as_of)
    covered = set(recalc["covered_days"]) | {
        dt.date.fromisoformat(day) for day in monitor_covered
    }
    covered.add(as_of)

    if unknown_days:
        latest_unknown = max(unknown_days)
        arm_as_of = latest_unknown + dt.timedelta(days=primary_lookback)
    else:
        latest_unknown = None
        arm_as_of = min(covered)

    armed = as_of >= arm_as_of
    days_until = max((arm_as_of - as_of).days, 0)

    history_end = max(covered)
    first_uncovered = (
        latest_unknown + dt.timedelta(days=1) if latest_unknown else min(covered)
    )
    unverified = []
    day = first_uncovered
    while day <= history_end:
        if day not in covered:
            unverified.append(day.isoformat())
        day += dt.timedelta(days=1)

    return {
        "official_window_id": PRIMARY if armed else PILOT,
        "primary_window_id": PRIMARY,
        "pilot_window_id": PILOT,
        "leadership_policy_version": windows["policy_version"],
        "leadership_policy_sha256": windows["policy_sha256"],
        "pilot_lookback_calendar_days": windows["pilot_lookback_calendar_days"],
        "primary_lookback_calendar_days": primary_lookback,
        "minimum_consecutive_complete_days": MINIMUM_CONSECUTIVE_COMPLETE_DAYS,
        "latest_taxonomy_unknown_as_of": (
            latest_unknown.isoformat() if latest_unknown else None
        ),
        "earliest_primary_observed_as_of": arm_as_of.isoformat(),
        "already_armed": armed,
        "days_until_arming": days_until,
        "switch_is_permanent": True,
        "switch_rule": (
            "select_leadership_window latches official = PRIMARY if "
            "(primary_observed_earlier or primary_now); build_chain carries "
            "primary_seen forward monotonically; there is no fallback to pilot."
        ),
        "evidence_covered_through": history_end.isoformat(),
        "recalc_history_covered_through": max(recalc["covered_days"]).isoformat(),
        "unverified_clean_days": unverified,
        "unverified_clean_day_count": len(unverified),
        "countdown_conditional_on_unverified_days": bool(unverified),
    }


def gap_cost_days(policy: dict, arming: dict) -> dict:
    """Cost of one TAXONOMY_COVERAGE_UNKNOWN day, recomputed at runtime.

    Derived from the ratified window lookback plus the production
    re-acceptance constant, then cross-checked against the values the
    alarm policy records.  Disagreement fails closed instead of silently
    preferring one source.
    """
    derived_pre = (
        arming["pilot_lookback_calendar_days"] + MINIMUM_CONSECUTIVE_COMPLETE_DAYS
    )
    derived_post = (
        arming["primary_lookback_calendar_days"] + MINIMUM_CONSECUTIVE_COMPLETE_DAYS
    )
    if derived_pre != policy["_pre_cost"] or derived_post != policy["_post_cost"]:
        raise MarginMonitorError(
            "COST_MODEL_DRIFT:"
            f"derived={derived_pre}/{derived_post}"
            f" policy={policy['_pre_cost']}/{policy['_post_cost']}"
        )
    return {
        "pre_arming_days": derived_pre,
        "post_arming_days": derived_post,
        "applicable_days": derived_post if arming["already_armed"] else derived_pre,
        "derivation": (
            "official window lookback_calendar_days + "
            "MINIMUM_CONSECUTIVE_COMPLETE_DAYS"
        ),
    }


# ──────────────────────────────────────────────────────────────────────
# Trend (append-only margin history)
# ──────────────────────────────────────────────────────────────────────


def read_history(
    as_of: dt.date, lookback_days: int, data_root: Path = DATA_ROOT
) -> list:
    """Prior committed margin observations, append-only, never rewritten."""
    data_root = Path(data_root)
    if not data_root.is_dir():
        return []
    earliest = as_of - dt.timedelta(days=lookback_days)
    history = []
    for directory in sorted(data_root.iterdir()):
        packet = directory / "packet.json"
        if not packet.is_file():
            continue
        try:
            record = json.loads(packet.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MarginMonitorError(f"HISTORY_PACKET_UNREADABLE:{directory.name}") from exc
        if record.get("schema_version") != SCHEMA_VERSION:
            raise MarginMonitorError(f"HISTORY_PACKET_SCHEMA_INVALID:{directory.name}")
        margin = record.get("margin")
        if not isinstance(margin, dict) or not isinstance(
            record.get("as_of_date"), str
        ):
            raise MarginMonitorError(f"HISTORY_PACKET_INVALID:{directory.name}")
        for field in (
            "margin_ranks",
            "scan_stop_rank",
            "nearest_unclassified_rank",
            "taxonomy_unknown_count",
        ):
            if field not in margin:
                raise MarginMonitorError(
                    f"HISTORY_PACKET_INVALID:{directory.name}:{field}"
                )
        day = validate_source_date(record["as_of_date"])
        if day >= as_of or day < earliest:
            continue
        history.append(
            {
                "as_of_date": record["as_of_date"],
                "source_date": record["source_date"],
                "margin_ranks": margin["margin_ranks"],
                "scan_stop_rank": margin["scan_stop_rank"],
                "nearest_unclassified_rank": margin["nearest_unclassified_rank"],
                "taxonomy_unknown_count": margin["taxonomy_unknown_count"],
            }
        )
    history.sort(key=lambda item: item["as_of_date"])
    return history


def margin_trend(
    as_of: dt.date, margin_ranks: Optional[int], history: list, policy: dict
) -> dict:
    """Per-day shrink rate over the configured lookback.

    Positive `shrink_ranks_per_day` means the margin is closing.  The rate
    is measured against the oldest committed observation inside the
    lookback, which is the slope that actually matters for "how many days
    until a gap".
    """
    usable = [
        entry
        for entry in history
        if type(entry["margin_ranks"]) is int
    ]
    observations = [
        {"as_of_date": entry["as_of_date"], "margin_ranks": entry["margin_ranks"]}
        for entry in usable
    ]
    if margin_ranks is not None:
        observations.append(
            {"as_of_date": as_of.isoformat(), "margin_ranks": margin_ranks}
        )
    if len(observations) < policy["_rate_minimum_observations"]:
        return {
            "status": "INSUFFICIENT_HISTORY",
            "lookback_days": policy["_rate_lookback"],
            "minimum_observations": policy["_rate_minimum_observations"],
            "observation_count": len(observations),
            "observations": observations,
            "elapsed_days": None,
            "shrink_ranks": None,
            "shrink_ranks_per_day": None,
            "days_to_zero_margin_at_current_rate": None,
        }
    oldest = observations[0]
    latest = observations[-1]
    elapsed = (
        dt.date.fromisoformat(latest["as_of_date"])
        - dt.date.fromisoformat(oldest["as_of_date"])
    ).days
    if elapsed < 1:
        raise MarginMonitorError("TREND_ELAPSED_INVALID")
    shrink = oldest["margin_ranks"] - latest["margin_ranks"]
    rate = Decimal(shrink) / Decimal(elapsed)
    if rate > 0 and latest["margin_ranks"] > 0:
        days_to_zero = CB.render_decimal(
            Decimal(latest["margin_ranks"]) / rate, RATE_PLACES
        )
    else:
        days_to_zero = None
    return {
        "status": "DERIVED",
        "lookback_days": policy["_rate_lookback"],
        "minimum_observations": policy["_rate_minimum_observations"],
        "observation_count": len(observations),
        "observations": observations,
        "elapsed_days": elapsed,
        "shrink_ranks": shrink,
        "shrink_ranks_per_day": CB.render_decimal(rate, RATE_PLACES),
        "days_to_zero_margin_at_current_rate": days_to_zero,
    }


# ──────────────────────────────────────────────────────────────────────
# Alarm
# ──────────────────────────────────────────────────────────────────────


def level_severity(margin_ranks: Optional[int], policy: dict) -> str:
    if margin_ranks is None:
        # Nothing unclassified anywhere in the ranked population.
        return "NONE"
    if margin_ranks <= 0:
        return "GAP_OPEN"
    if margin_ranks <= policy["_margin_critical"]:
        return "CRITICAL"
    if margin_ranks <= policy["_margin_warning"]:
        return "WARNING"
    return "NONE"


def rate_severity(trend: dict, policy: dict) -> str:
    if trend["status"] != "DERIVED" or trend["shrink_ranks_per_day"] is None:
        return "NONE"
    rate = Decimal(trend["shrink_ranks_per_day"])
    if rate >= policy["_rate_critical"]:
        return "CRITICAL"
    if rate >= policy["_rate_warning"]:
        return "WARNING"
    return "NONE"


def evaluate_alarm(
    margin_ranks: Optional[int], trend: dict, arming: dict, policy: dict
) -> dict:
    """Combine level and rate, then escalate mechanically after arming."""
    from_level = level_severity(margin_ranks, policy)
    from_rate = rate_severity(trend, policy)
    rank = policy["_rank"]
    base = from_level if rank[from_level] >= rank[from_rate] else from_rate
    escalated = policy["_escalation"][base]
    armed = arming["already_armed"]
    severity = escalated if armed else base
    drivers = []
    if from_level != "NONE":
        drivers.append("MARGIN_LEVEL")
    if from_rate != "NONE":
        drivers.append("MARGIN_SHRINK_RATE")
    return {
        "severity": severity,
        "base_severity": base,
        "severity_from_level": from_level,
        "severity_from_rate": from_rate,
        "escalated_for_arming": bool(armed and severity != base),
        "post_arming_escalation_applied": armed,
        "drivers": drivers,
        "alarm_raised": severity != "NONE",
        "thresholds_applied": {
            "margin_warning_at_or_below": policy["_margin_warning"],
            "margin_critical_at_or_below": policy["_margin_critical"],
            "rate_warning_shrink_ranks_per_day_at_or_above": CB.render_decimal(
                policy["_rate_warning"], RATE_PLACES
            ),
            "rate_critical_shrink_ranks_per_day_at_or_above": CB.render_decimal(
                policy["_rate_critical"], RATE_PLACES
            ),
        },
    }


# ──────────────────────────────────────────────────────────────────────
# Record
# ──────────────────────────────────────────────────────────────────────


def authority_block() -> dict:
    return {
        "classifications_created": 0,
        "records_ratified": 0,
        "taxonomy_authorized": False,
        "classification_authorized": False,
        "investability_authorized": False,
        "stage_promotion_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
        "threshold_authorized": False,
    }


def build_margin_observation(ranking: dict, policy: dict) -> dict:
    """The deterministic, snapshot-only half of the record."""
    stop = ranking["scan_stop_rank"]
    ranked_count = ranking["ranked_candidate_count"]
    band = policy["_band"]
    band_end = min(stop + band, ranked_count)
    unclassified = ranking["unclassified"]
    ranks = [row["rank_before_taxonomy"] for row in unclassified]
    nearest = min(ranks) if ranks else None
    inside = [rank for rank in ranks if rank <= stop]
    band_rows = [
        {
            "rank_before_taxonomy": row["rank_before_taxonomy"],
            "ranks_past_scan_stop": row["rank_before_taxonomy"] - stop,
            "canonical_asset_id": row["canonical_asset_id"],
            "pair_id": row["pair_id"],
            "trailing_30d_usd_turnover": row["trailing_usd_turnover"],
        }
        for row in unclassified
        if stop < row["rank_before_taxonomy"] <= band_end
    ]
    real_diagnostics = ranking["real"]["diagnostics"]
    return {
        "scan_stop_rank": stop,
        "ranked_candidate_count": ranked_count,
        "target_asset_count": ranking["universe_policy"]["target_asset_count"],
        "selected_eligible_count": stop
        - len(real_diagnostics["taxonomy_excluded_before_cutoff"])
        - len(real_diagnostics["taxonomy_unknown_before_cutoff"]),
        "excluded_before_cutoff_count": len(
            real_diagnostics["taxonomy_excluded_before_cutoff"]
        ),
        "taxonomy_unknown_count": len(
            real_diagnostics["taxonomy_unknown_before_cutoff"]
        ),
        "taxonomy_unknown_ranks": sorted(inside),
        "nearest_unclassified_rank": nearest,
        "nearest_unclassified_asset_id": (
            ranking["ranking_by_rank"][nearest]["canonical_asset_id"]
            if nearest is not None
            else None
        ),
        # margin_ranks <= 0 means an unclassified asset is already inside the
        # scan: the gap this monitor exists to prevent is already open.
        "margin_ranks": (nearest - stop) if nearest is not None else None,
        "unclassified_candidate_count": len(unclassified),
        "lookahead_band_ranks": band,
        "lookahead_band_first_rank": stop + 1,
        "lookahead_band_last_rank": band_end,
        "lookahead_band_unclassified_count": len(band_rows),
        "lookahead_band_unclassified": band_rows,
        "ranking_ineligible_count": real_diagnostics["ranking_ineligible_count"],
        "ranking_ineligible_asset_ids": sorted(
            {
                row["canonical_asset_id"]
                for row in real_diagnostics["ranking_ineligible"]
            }
        ),
        "source_outcome": {
            "status": ranking["real"]["status"],
            "unknown_reason": ranking["real"]["reason"],
        },
        "ranking_provenance": {
            "function": "crypto_breadth.qualified_members",
            "module": ".github/scripts/crypto_breadth.py",
            "selection_rule": ranking["universe_policy"]["selection_rule"],
            "ranking_metric": ranking["universe_policy"]["ranking_metric"],
            "ranking_lookback_finalized_days": ranking["universe_policy"][
                "ranking_lookback_finalized_days"
            ],
            "full_enumeration_method": (
                "same qualified_members() call over an in-memory taxonomy view "
                "with no classification records, so the Top-N break cannot fire"
            ),
            # How many ranks the real scan and the full enumeration were
            # cross-checked on, asset id and turnover for turnover.
            "production_ranks_cross_checked": ranking["verified_production_rank_count"],
        },
    }


def build_packet(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    data_root: Path = DATA_ROOT,
    monitor_policy_path: Path = MONITOR_POLICY_PATH,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
    recalc_root: Path = RECALC_ROOT,
    leadership_policy_path: Path = CL.LEADERSHIP_POLICY_PATH,
) -> dict:
    validate_source_date(source_date)
    policy = load_monitor_policy(monitor_policy_path)
    ranking = production_ranking(
        Path(raw_root) / source_date,
        universe_policy_path=universe_policy_path,
        taxonomy_path=taxonomy_path,
        identity_path=identity_path,
    )
    transform_as_of = ranking["as_of"]
    margin = build_margin_observation(ranking, policy)
    history = read_history(transform_as_of, policy["_rate_lookback"], data_root)
    arming = arming_state(
        transform_as_of,
        margin["taxonomy_unknown_count"],
        history,
        recalc_root=recalc_root,
        leadership_policy_path=leadership_policy_path,
    )
    trend = margin_trend(transform_as_of, margin["margin_ranks"], history, policy)
    cost = gap_cost_days(policy, arming)
    alarm = evaluate_alarm(margin["margin_ranks"], trend, arming, policy)

    universe = ranking["universe_policy"]
    taxonomy = ranking["taxonomy_policy"]
    record = {
        "schema_version": SCHEMA_VERSION,
        "status": "REVIEW_ONLY",
        "source_date": source_date,
        "as_of_date": transform_as_of.isoformat(),
        "generated_at": ranking["core"]["fetched_at_utc"],
        "alarm": alarm,
        "margin": margin,
        "trend": trend,
        "arming": arming,
        "gap_cost": cost,
        "monitor_policy": {
            "path": source_ref(policy["_path"]),
            "policy_version": policy["policy_version"],
            "policy_sha256": policy["_sha256"],
            "approval_status": policy["approval_status"],
        },
        "lineage": {
            "raw_bundle_path": f"evidence/crypto/breadth/raw/{source_date}",
            "manifest_sha256": ranking["manifest_sha256"],
            "capture_version": ranking["manifest"]["capture_version"],
            "available_at": ranking["core"]["fetched_at_utc"],
            "identity_policy_version": ranking["core"]["identity"]["policy_version"],
            "identity_policy_sha256": ranking["core"]["identity_policy_sha256"],
            "universe_policy_path": source_ref(universe_policy_path),
            "universe_policy_version": universe["policy_version"],
            "universe_policy_sha256": CB.file_sha256(Path(universe_policy_path)),
            "taxonomy_path": source_ref(taxonomy_path),
            "taxonomy_policy_version": taxonomy["policy_version"],
            "taxonomy_policy_sha256": CB.file_sha256(Path(taxonomy_path)),
            "taxonomy_approval_status": taxonomy["approval_status"],
        },
        "authority": authority_block(),
        "not_applied_to": [
            "CRYPTO_MARKET_REGIME_LEADERSHIP_AXIS",
            "CRYPTO_MARKET_REGIME_BREADTH_AXIS",
            "CRYPTO_BREADTH_EXCLUSION_TAXONOMY",
            "CRYPTO_INVESTABLE_UNIVERSE",
            "CRYPTO_PAPER_RUNTIME",
        ],
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def validate_packet(
    record: dict,
    raw_root: Path = RAW_ROOT,
    monitor_policy_path: Path = MONITOR_POLICY_PATH,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
) -> str:
    """Independently re-derive the deterministic parts of a stored packet.

    The margin observation is rebuilt from the committed snapshot and must
    match byte-for-byte.  The trend is recomputed from the observations the
    packet recorded, so a stored rate cannot drift from its own inputs.
    `payload_sha256` is re-derived over the stored record.
    """
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise MarginMonitorError("PACKET_SCHEMA_INVALID")
    stored_sha = record.get("payload_sha256")
    body = {key: value for key, value in record.items() if key != "payload_sha256"}
    if stored_sha != payload_sha256(body):
        raise MarginMonitorError("PACKET_PAYLOAD_SHA_MISMATCH")
    if record.get("status") != "REVIEW_ONLY":
        raise MarginMonitorError("PACKET_STATUS_INVALID")
    for field, value in sorted(record.get("authority", {}).items()):
        if value not in (False, 0):
            raise MarginMonitorError(f"PACKET_AUTHORITY_NOT_FALSE:{field}")
    source_date = record.get("source_date")
    if not isinstance(source_date, str):
        raise MarginMonitorError("PACKET_SOURCE_DATE_INVALID")
    policy = load_monitor_policy(monitor_policy_path)
    ranking = production_ranking(
        Path(raw_root) / source_date,
        universe_policy_path=universe_policy_path,
        taxonomy_path=taxonomy_path,
        identity_path=identity_path,
    )
    if record.get("as_of_date") != ranking["as_of"].isoformat():
        raise MarginMonitorError("PACKET_AS_OF_MISMATCH")
    if record.get("margin") != build_margin_observation(ranking, policy):
        raise MarginMonitorError("PACKET_MARGIN_DRIFT_OR_TAMPER")
    trend = record.get("trend")
    if not isinstance(trend, dict):
        raise MarginMonitorError("PACKET_TREND_INVALID")
    replayed = margin_trend(
        ranking["as_of"],
        None,
        [
            {
                "as_of_date": item["as_of_date"],
                "margin_ranks": item["margin_ranks"],
                "taxonomy_unknown_count": 0,
            }
            for item in trend.get("observations", [])
        ],
        policy,
    )
    for field in (
        "status",
        "elapsed_days",
        "shrink_ranks",
        "shrink_ranks_per_day",
        "days_to_zero_margin_at_current_rate",
        "observation_count",
    ):
        if trend.get(field) != replayed[field]:
            raise MarginMonitorError(f"PACKET_TREND_DRIFT_OR_TAMPER:{field}")
    return "verified"


def populate(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    data_root: Path = DATA_ROOT,
    monitor_policy_path: Path = MONITOR_POLICY_PATH,
    universe_policy_path: Path = CB.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CB.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = CB.IDENTITY_EXCEPTIONS_PATH,
    recalc_root: Path = RECALC_ROOT,
    leadership_policy_path: Path = CL.LEADERSHIP_POLICY_PATH,
) -> dict:
    """Write today's margin observation.  Append-only: never rewrites a day."""
    target = output_path(source_date, data_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MarginMonitorError(f"EXISTING_PACKET_UNREADABLE:{exc}") from exc
        validate_packet(
            existing,
            raw_root,
            monitor_policy_path,
            universe_policy_path,
            taxonomy_path,
            identity_path,
        )
        return {
            "outcome": "verified_existing",
            "path": str(target),
            "payload_sha256": existing["payload_sha256"],
            "severity": existing["alarm"]["severity"],
            "margin_ranks": existing["margin"]["margin_ranks"],
            "days_until_arming": existing["arming"]["days_until_arming"],
        }
    record = build_packet(
        source_date,
        raw_root=raw_root,
        data_root=data_root,
        monitor_policy_path=monitor_policy_path,
        universe_policy_path=universe_policy_path,
        taxonomy_path=taxonomy_path,
        identity_path=identity_path,
        recalc_root=recalc_root,
        leadership_policy_path=leadership_policy_path,
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(
            json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated",
        "path": str(target),
        "payload_sha256": record["payload_sha256"],
        "severity": record["alarm"]["severity"],
        "margin_ranks": record["margin"]["margin_ranks"],
        "days_until_arming": record["arming"]["days_until_arming"],
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key in (
            "outcome",
            "path",
            "payload_sha256",
            "severity",
            "margin_ranks",
            "days_until_arming",
        ):
            handle.write(f"{key}={result.get(key, '')}\n")


def run(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Crypto taxonomy classification-margin monitor")
    parser.add_argument("source_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--monitor-policy", type=Path, default=MONITOR_POLICY_PATH)
    parser.add_argument(
        "--fail-on-severity",
        default="",
        help=(
            "Comma-separated severities that make this run exit non-zero, e.g. "
            "GAP_OPEN,CRITICAL_ARMED. Empty means report only."
        ),
    )
    args = parser.parse_args(argv)
    try:
        result = populate(
            args.source_date,
            raw_root=args.raw_root,
            data_root=args.data_root,
            monitor_policy_path=args.monitor_policy,
        )
    except (MarginMonitorError, CB.BreadthError) as exc:
        _write_github_output({"outcome": "failed"})
        print(f"crypto taxonomy margin monitor failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"crypto taxonomy margin monitor {result['outcome']}"
        f" date={args.source_date} severity={result['severity']}"
        f" margin_ranks={result['margin_ranks']}"
        f" days_until_arming={result['days_until_arming']}"
        f" path={result['path']} sha256={result['payload_sha256']}"
    )
    escalate_on = {
        item.strip()
        for item in args.fail_on_severity.split(",")
        if item.strip()
    }
    if result["severity"] in escalate_on:
        print(f"severity {result['severity']} is in --fail-on-severity")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
