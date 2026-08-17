#!/usr/bin/env python3
"""
Atlas — R-1 US price temporal eligibility contract (v0.1)

Purpose
-------
Classify US EOD price inputs without pretending that a present-day historical
backfill is an archived historical vintage.

This module is policy-only:
- fetches no market data;
- stores no Tiingo Data;
- calculates no Trend/Leadership or Regime score.

Approved temporal classes
-------------------------
HISTORICAL_BACKFILL + RAW
    CAUSAL_RESEARCH_ONLY. It may support bounded research if the downstream
    transform is split-aware as-of the decision date, or the lookback window
    is independently proven to contain no split.

HISTORICAL_BACKFILL + ADJUSTED
    REVISED_SENSITIVITY_ONLY. Present-day adjusted history is not a historical
    point-in-time input.

FORWARD_SHADOW
    A payload fetched after Atlas's post-correction qualification cutoff can
    be decision-time PIT-qualified. Under a Starter transient-only license,
    durable replay must use permitted non-reconstructive derived features,
    not stored vendor rows.
"""

import argparse
import sys
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

TOOL_VERSION = "0.1"
MARKET_TZ = "America/New_York"

# Tiingo documents exchange corrections through 20:00 New York time.
# Atlas adds a 15-minute operational buffer before PIT qualification.
QUALIFICATION_CUTOFF = time(20, 15)

RUN_MODES = frozenset({
    "HISTORICAL_BACKFILL",
    "FORWARD_SHADOW",
})

PRICE_BASES = frozenset({
    "RAW",
    "ADJUSTED",
})


class Stop(Exception):
    pass


def parse_date(value, label):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        raise Stop(
            "%s must be YYYY-MM-DD" % label
        )


def parse_ts(value, label):
    if not isinstance(value, str):
        raise Stop(
            "%s must be an ISO8601 timestamp" % label
        )

    try:
        dt = datetime.fromisoformat(
            value.replace("Z", "+00:00")
        )
    except ValueError:
        raise Stop(
            "%s must be an ISO8601 timestamp" % label
        )

    if dt.tzinfo is None or dt.utcoffset() is None:
        raise Stop(
            "%s must include a timezone offset" % label
        )

    return dt


def qualification_cutoff(
    observation_date,
    market_tz=MARKET_TZ,
):
    d = parse_date(
        observation_date,
        "observation_date",
    )

    try:
        tz = ZoneInfo(market_tz)
    except Exception:
        raise Stop(
            "invalid market timezone"
        )

    return datetime.combine(
        d,
        QUALIFICATION_CUTOFF,
        tzinfo=tz,
    )


def classify(
    run_mode,
    price_basis,
    observation_date,
    fetched_at=None,
    decision_at=None,
    market_tz=MARKET_TZ,
):
    mode = str(
        run_mode or ""
    ).upper()

    basis = str(
        price_basis or ""
    ).upper()

    if mode not in RUN_MODES:
        raise Stop(
            "unsupported run_mode"
        )

    if basis not in PRICE_BASES:
        raise Stop(
            "unsupported price_basis"
        )

    parse_date(
        observation_date,
        "observation_date",
    )

    base = {
        "run_mode": mode,
        "price_basis": basis,
        "market_timezone": market_tz,
        "availability_semantics": "not_before",
        "historical_payload_vintage_proven": False,
        "decision_time_payload_captured": False,
        "authoritative_historical_pit": False,
        "regime_score_authorized": False,
    }

    if mode == "HISTORICAL_BACKFILL":
        # A fetch timestamp in 2026 does not manufacture
        # a 2008 payload vintage.
        if fetched_at is not None:
            parse_ts(
                fetched_at,
                "fetched_at",
            )

        if decision_at is not None:
            parse_ts(
                decision_at,
                "decision_at",
            )

        if basis == "ADJUSTED":
            base.update({
                "eligibility":
                    "REVISED_SENSITIVITY_ONLY",
                "causal_research_eligible":
                    False,
                "forward_pit_qualified":
                    False,
                "reason_code":
                    "CURRENT_ADJUSTED_HISTORY_NOT_HISTORICAL_VINTAGE",
                "historical_transform_requirement":
                    "NOT_AUTHORIZED_FOR_CAUSAL_REPLAY",
            })

            return base

        base.update({
            "eligibility":
                "CAUSAL_RESEARCH_ONLY",
            "causal_research_eligible":
                True,
            "forward_pit_qualified":
                False,
            "reason_code":
                "CURRENT_RAW_HISTORY_HAS_NO_ARCHIVED_PAYLOAD_VINTAGE",
            "historical_transform_requirement":
                "SPLIT_AWARE_ASOF_OR_PROVEN_NO_SPLIT_WINDOW",
        })

        return base

    if fetched_at is None or decision_at is None:
        raise Stop(
            "forward Shadow requires "
            "fetched_at and decision_at"
        )

    fetched = parse_ts(
        fetched_at,
        "fetched_at",
    )

    decision = parse_ts(
        decision_at,
        "decision_at",
    )

    cutoff = qualification_cutoff(
        observation_date,
        market_tz,
    )

    if fetched < cutoff:
        base.update({
            "eligibility":
                "NOT_YET_QUALIFIED",
            "causal_research_eligible":
                False,
            "forward_pit_qualified":
                False,
            "reason_code":
                "FETCH_BEFORE_ATLAS_QUALIFICATION_CUTOFF",
        })

        return base

    if decision < fetched:
        base.update({
            "eligibility":
                "NOT_YET_QUALIFIED",
            "causal_research_eligible":
                False,
            "forward_pit_qualified":
                False,
            "reason_code":
                "DECISION_PRECEDES_ATLAS_INGESTION",
        })

        return base

    base.update({
        "eligibility":
            "FORWARD_PIT_QUALIFIED",
        "causal_research_eligible":
            True,
        "forward_pit_qualified":
            True,
        "reason_code":
            "CAPTURED_AFTER_ATLAS_QUALIFICATION_CUTOFF",
        "decision_time_payload_captured":
            True,
        "available_at":
            fetched.isoformat(),
        "decision_at":
            decision.isoformat(),
        "source_availability_not_before":
            cutoff.isoformat(),
        "replayability":
            "DERIVED_FEATURES_ONLY_UNDER_STARTER",
    })

    return base


def format_summary(result):
    keys = (
        "run_mode",
        "price_basis",
        "eligibility",
        "authoritative_historical_pit",
        "causal_research_eligible",
        "forward_pit_qualified",
        "reason_code",
        "regime_score_authorized",
    )

    return "\n".join(
        "%s=%s" % (
            key,
            result[key],
        )
        for key in keys
    )


def main(argv=None):
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--run-mode",
        required=True,
        choices=sorted(RUN_MODES),
    )

    parser.add_argument(
        "--price-basis",
        required=True,
        choices=sorted(PRICE_BASES),
    )

    parser.add_argument(
        "--observation-date",
        required=True,
    )

    parser.add_argument(
        "--fetched-at",
    )

    parser.add_argument(
        "--decision-at",
    )

    args = parser.parse_args(argv)

    try:
        result = classify(
            args.run_mode,
            args.price_basis,
            args.observation_date,
            fetched_at=args.fetched_at,
            decision_at=args.decision_at,
        )

        print(
            format_summary(result)
        )

        return 0

    except Stop as exc:
        print(
            "STOP: %s" % exc,
            file=sys.stderr,
        )

        return 2


if __name__ == "__main__":
    raise SystemExit(main())
