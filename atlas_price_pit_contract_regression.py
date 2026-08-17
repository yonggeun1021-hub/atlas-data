#!/usr/bin/env python3
import importlib.util
import sys

SRC = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "atlas_price_pit_contract.py"
)

spec = importlib.util.spec_from_file_location(
    "m",
    SRC,
)

m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((
        name,
        bool(cond),
        detail,
    ))

    print(
        "%s  %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            (" — " + detail) if detail else "",
        )
    )


def expect_stop(**kwargs):
    try:
        m.classify(**kwargs)
        return False
    except m.Stop:
        return True


print("=" * 72)
print(
    "Atlas R-1 US price temporal eligibility regression"
)
print("=" * 72)


raw_backfill = m.classify(
    "HISTORICAL_BACKFILL",
    "RAW",
    "2008-10-10",
)

check(
    "P1 historical RAW is causal research only",
    raw_backfill["eligibility"]
    == "CAUSAL_RESEARCH_ONLY"
    and raw_backfill[
        "causal_research_eligible"
    ] is True
    and raw_backfill[
        "authoritative_historical_pit"
    ] is False,
)

check(
    "P2 historical RAW requires split-aware/no-split-window contract",
    raw_backfill[
        "historical_transform_requirement"
    ]
    == "SPLIT_AWARE_ASOF_OR_PROVEN_NO_SPLIT_WINDOW",
)


adj_backfill = m.classify(
    "HISTORICAL_BACKFILL",
    "ADJUSTED",
    "2008-10-10",
)

check(
    "P3 historical ADJUSTED is revised sensitivity only",
    adj_backfill["eligibility"]
    == "REVISED_SENSITIVITY_ONLY"
    and adj_backfill[
        "causal_research_eligible"
    ] is False
    and adj_backfill[
        "authoritative_historical_pit"
    ] is False,
)


check(
    "P4 current fetch timestamps cannot create a 2008 vintage",
    m.classify(
        "HISTORICAL_BACKFILL",
        "RAW",
        "2008-10-10",
        fetched_at="2026-08-17T22:00:00+00:00",
        decision_at="2026-08-17T22:01:00+00:00",
    )[
        "historical_payload_vintage_proven"
    ] is False,
)


check(
    "P5 unsupported mode fails closed",
    expect_stop(
        run_mode="REPLAY",
        price_basis="RAW",
        observation_date="2008-10-10",
    ),
)


check(
    "P6 unsupported price basis fails closed",
    expect_stop(
        run_mode="HISTORICAL_BACKFILL",
        price_basis="TOTAL_RETURN",
        observation_date="2008-10-10",
    ),
)


check(
    "P7 invalid observation date fails closed",
    expect_stop(
        run_mode="HISTORICAL_BACKFILL",
        price_basis="RAW",
        observation_date="2008/10/10",
    ),
)


check(
    "P8 forward Shadow requires timestamps",
    expect_stop(
        run_mode="FORWARD_SHADOW",
        price_basis="RAW",
        observation_date="2026-08-17",
    ),
)


early = m.classify(
    "FORWARD_SHADOW",
    "RAW",
    "2026-08-17",
    fetched_at="2026-08-18T00:14:59+00:00",
    decision_at="2026-08-18T00:20:00+00:00",
)

check(
    "P9 forward fetch before 20:15 NY cutoff is not qualified",
    early["eligibility"]
    == "NOT_YET_QUALIFIED"
    and early["reason_code"]
    == "FETCH_BEFORE_ATLAS_QUALIFICATION_CUTOFF",
)


late = m.classify(
    "FORWARD_SHADOW",
    "RAW",
    "2026-08-17",
    fetched_at="2026-08-18T00:15:00+00:00",
    decision_at="2026-08-18T00:20:00+00:00",
)

check(
    "P10 forward RAW at/after cutoff is PIT qualified",
    late["eligibility"]
    == "FORWARD_PIT_QUALIFIED"
    and late[
        "forward_pit_qualified"
    ] is True
    and late[
        "decision_time_payload_captured"
    ] is True
    and late[
        "historical_payload_vintage_proven"
    ] is False,
)


late_adj = m.classify(
    "FORWARD_SHADOW",
    "ADJUSTED",
    "2026-08-17",
    fetched_at="2026-08-18T00:15:00+00:00",
    decision_at="2026-08-18T00:20:00+00:00",
)

check(
    "P11 forward ADJUSTED may be decision-time PIT qualified",
    late_adj[
        "forward_pit_qualified"
    ] is True
    and late_adj[
        "authoritative_historical_pit"
    ] is False,
)


bad_order = m.classify(
    "FORWARD_SHADOW",
    "RAW",
    "2026-08-17",
    fetched_at="2026-08-18T00:15:00+00:00",
    decision_at="2026-08-18T00:14:59+00:00",
)

check(
    "P12 decision before ingestion is not qualified",
    bad_order[
        "forward_pit_qualified"
    ] is False
    and bad_order["reason_code"]
    == "DECISION_PRECEDES_ATLAS_INGESTION",
)


check(
    "P13 naive timestamps fail closed",
    expect_stop(
        run_mode="FORWARD_SHADOW",
        price_basis="RAW",
        observation_date="2026-08-17",
        fetched_at="2026-08-18T00:15:00",
        decision_at="2026-08-18T00:20:00+00:00",
    ),
)


check(
    "P14 forward qualified output preserves Starter replay boundary",
    late["replayability"]
    == "DERIVED_FEATURES_ONLY_UNDER_STARTER"
    and late[
        "regime_score_authorized"
    ] is False,
)


summary = m.format_summary(late)

check(
    "P15 summary is policy-only and contains no market values",
    "close=" not in summary.lower()
    and "volume=" not in summary.lower()
    and "regime_score_authorized=False"
    in summary,
)


# 2026-01-15 20:15 America/New_York
# = 2026-01-16 01:15 UTC.
winter_early = m.classify(
    "FORWARD_SHADOW",
    "RAW",
    "2026-01-15",
    fetched_at="2026-01-16T01:14:59+00:00",
    decision_at="2026-01-16T01:20:00+00:00",
)

winter_late = m.classify(
    "FORWARD_SHADOW",
    "RAW",
    "2026-01-15",
    fetched_at="2026-01-16T01:15:00+00:00",
    decision_at="2026-01-16T01:20:00+00:00",
)

check(
    "P16 timezone/DST contract uses America/New_York",
    winter_early[
        "forward_pit_qualified"
    ] is False
    and winter_late[
        "forward_pit_qualified"
    ] is True,
)


passed = sum(
    1
    for _, ok, _ in RESULTS
    if ok
)

print("=" * 72)
print(
    "%d/%d 통과"
    % (
        passed,
        len(RESULTS),
    )
)

if passed != len(RESULTS):
    raise SystemExit(1)
