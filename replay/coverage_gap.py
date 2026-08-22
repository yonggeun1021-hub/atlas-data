#!/usr/bin/env python3
"""Coverage Gap KPI block (CIO review round 3, flaw 4).

A ticker whose price happened to rise later, on a date where evidence
simply wasn't preserved, is NOT automatically an Opportunity Miss -- these
are two different things:
  (a) genuine opportunity miss = a signal was actually readable at the time
      and wasn't acted on.
  (b) unauditable = no evidence was preserved for that date/subject at all,
      so no judgment -- miss or defense -- can honestly be made.

`DATA_FAILURE` entries are excluded from the Opportunity Miss/Defense KPI
numerator AND denominator entirely (see
`opportunity_miss_ledger.build_miss_episodes` /
`defense_ledger.build_defense_episodes`) and reported here instead.

★ CIO review round 4 fix (item 7): a BLENDED cross-market
  `auditable_coverage_pct` (BTC + Korea + Crypto summed together) is
  reported here ONLY as a secondary, purely operational statistic --
  "how much of this repo's evidence trail exists at all". BTC, Korea, and
  Crypto have entirely different population definitions (a dedicated
  single-asset collector; a current-watchlist diagnostic cohort with no
  historical PIT reconstruction; a real ratified-taxonomy universe that is
  empty for most of the window) -- summing them into one number would
  imply they are one comparable investment-performance population, which
  they are not. Every caller MUST use the per-market breakdown
  (`run_pit_replay.py`'s `by_market`) for anything performance-shaped; this
  blended figure is tagged `NOT_AN_INVESTMENT_PERFORMANCE_KPI` explicitly.
"""
from __future__ import annotations

BLENDED_METRIC_WARNING = (
    "This is a blended cross-market operational statistic only "
    "(BTC + Korea + Crypto summed) -- BTC/Korea/Crypto have different "
    "population definitions and must never be treated as one comparable "
    "investment-performance KPI. Use by_market for anything performance-shaped."
)


def build_coverage_gap_report(entries: list[dict]) -> dict:
    total = len(entries)
    auditable = [e for e in entries if e["data_available"]]
    unauditable = [e for e in entries if not e["data_available"]]

    unauditable_by_subject: dict[str, int] = {}
    for e in unauditable:
        unauditable_by_subject[e["subject"]] = unauditable_by_subject.get(e["subject"], 0) + 1

    unauditable_subjects_entirely = sorted({
        subject for subject in {e["subject"] for e in entries}
        if all(not e["data_available"] for e in entries if e["subject"] == subject)
    })

    missing_evidence_types = []
    if any(e["decision_date"] < "2026-08-13" for e in unauditable):
        missing_evidence_types.append(
            "no committed Atlas evidence of any kind exists for any date before 2026-08-13 "
            "(this repo's own git history start -- see evidence_index.REPO_HISTORY_STARTS_AT)"
        )
    if any(e["subject"] == "BTC" for e in entries):
        missing_evidence_types.append(
            "no BTC ETF-flow dataset is committed anywhere in this repo"
        )
    missing_evidence_types.append(
        "no committed briefing TEXT output exists for any date (only raw collector snapshots)"
    )

    return {
        "total_entries": total,
        "auditable_entries": len(auditable),
        "unauditable_entries": len(unauditable),
        "auditable_coverage_pct": (len(auditable) / total * 100.0) if total else None,
        "auditable_coverage_pct_kpi_status": "SECONDARY_OPERATIONAL_METRIC_NOT_A_PERFORMANCE_KPI",
        "blended_metric_warning": BLENDED_METRIC_WARNING,
        "unauditable_days": sorted({e["decision_date"] for e in unauditable}),
        "unauditable_subjects_entirely": unauditable_subjects_entirely,
        "unauditable_entries_by_subject": dict(sorted(unauditable_by_subject.items())),
        "missing_evidence_types": missing_evidence_types,
    }
