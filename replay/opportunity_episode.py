#!/usr/bin/env python3
"""Opportunity Episode deduplication (CIO review flaw 5, PR #210).

The Signal Replay Ledger has one row per (subject, calendar day). A single
real multi-day rally or drawdown was previously counted once PER DAY it
spanned in the Miss/Opportunity and Defense ledgers -- inflating the miss
count (a 5-day rally became 5 "misses"). This module groups consecutive
material daily records for the same subject with the same root_cause into
one `Opportunity Episode`, evaluated exactly once, with:

  - `first_detected_date`   -- the episode's earliest daily record.
  - `last_detected_date`    -- the episode's latest daily record.
  - `first_action_eligible_date` -- the earliest date within the episode
    where `data_available=True` (i.e. Atlas's own system could plausibly
    have detected something at all) -- None if the whole episode is
    DATA_FAILURE.
  - `max_delay_days`        -- (last_detected_date - first_detected_date),
    i.e. how long the same unresolved opportunity/drawdown persisted.
  - `daily_rows_deduped`    -- how many raw daily rows this episode collapses.
  - `final_outcome`         -- the representative forward-return figure,
    taken from the FIRST daily record in the episode (it captures the
    fullest forward move from the earliest detection point, per the same
    materiality/horizon policy the daily records already used).

Grouping rule: consecutive by CALENDAR date (not trading date, since a
weekend/holiday gap is not a new episode) with a tolerance of
`MAX_GAP_DAYS` calendar days between one record's date and the next
(handles ordinary weekends plus one holiday), same subject, same
`root_cause`. This is a real, auditable grouping rule, not an outcome-based
selection -- it groups on (subject, date adjacency, root_cause), never on
the size or sign of any forward return.
"""
from __future__ import annotations

import datetime as dt
import hashlib

MAX_GAP_DAYS = 4


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def _episode_id(subject: str, first_date: str, last_date: str, root_cause: str | None) -> str:
    payload = f"{subject}|{first_date}|{last_date}|{root_cause}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def group_into_episodes(records: list[dict], outcome_field: str) -> list[dict]:
    """`records` must already be sorted-independent daily records (as
    produced by opportunity_miss_ledger.build_daily_records /
    defense_ledger.build_daily_records), each with `subject`,
    `decision_date`, `root_cause` (optional -- defense records have none),
    and `outcome_field` (the forward-return-shaped number to carry as
    `final_outcome`)."""
    by_subject: dict[str, list[dict]] = {}
    for r in records:
        by_subject.setdefault(r["subject"], []).append(r)

    episodes = []
    for subject, rows in by_subject.items():
        rows = sorted(rows, key=lambda r: r["decision_date"])
        current: list[dict] = []
        for row in rows:
            if not current:
                current = [row]
                continue
            prev = current[-1]
            gap = (_date(row["decision_date"]) - _date(prev["decision_date"])).days
            same_cause = row.get("root_cause") == prev.get("root_cause")
            if gap <= MAX_GAP_DAYS and same_cause:
                current.append(row)
            else:
                episodes.append(_build_episode(subject, current, outcome_field))
                current = [row]
        if current:
            episodes.append(_build_episode(subject, current, outcome_field))
    episodes.sort(key=lambda e: (e["subject"], e["first_detected_date"]))
    return episodes


def _build_episode(subject: str, rows: list[dict], outcome_field: str) -> dict:
    first, last = rows[0], rows[-1]
    action_eligible_rows = [r for r in rows if r.get("root_cause") != "DATA_FAILURE"]
    first_action_eligible_date = action_eligible_rows[0]["decision_date"] if action_eligible_rows else None
    return {
        "episode_id": _episode_id(subject, first["decision_date"], last["decision_date"], first.get("root_cause")),
        "subject": subject,
        "root_cause": first.get("root_cause"),
        "first_detected_date": first["decision_date"],
        "last_detected_date": last["decision_date"],
        "first_action_eligible_date": first_action_eligible_date,
        "max_delay_days": (_date(last["decision_date"]) - _date(first["decision_date"])).days,
        "daily_rows_deduped": len(rows),
        "final_outcome_pct": first.get(outcome_field),
        "final_outcome_horizon_used": first.get("materiality_horizon_used"),
        "evidence_sha256": first.get("evidence_sha256"),
        "source": first.get("source"),
    }
