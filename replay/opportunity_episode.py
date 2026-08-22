#!/usr/bin/env python3
"""Opportunity Episode deduplication (CIO review round 2 flaw 5, reworked in
round 3 flaw 3).

★ Round 3 fix: the round-2 grouping rule (same subject + same root_cause +
  flat 4-calendar-day gap) both (a) could wrongly merge two genuinely
  distinct rally events that happened to fall within 4 days of each other
  by coincidence, and (b) could wrongly split one continuous rally that had
  a 5+ day evidence gap in the middle. The corrected rule groups on:

    (subject, trigger_family, root_cause, forward-window overlap)

  -- two consecutive daily rows merge into one episode only when the LATER
  row's real `entry_date` (hypothetical_entry_at) falls within or adjacent
  to the EARLIER row's own real forward-measurement window
  (`outcome_window_end`), not an arbitrary flat calendar-day count. This is
  tied to the actual price-measurement windows each row already computed
  (see `forward_metrics.py`), not a second, independent heuristic.

  `trigger_family` is the row's own detected trigger-type set (e.g.
  `("PRICE_CONFIRMATION",)`), or the sentinel `"NO_SIGNAL"` for
  SIGNAL_MISS/DATA_FAILURE rows where nothing was actually detected --
  episodes never merge across different trigger families, matching the CIO
  review's requirement that grouping reflect the same causal event, not
  just subject+date proximity.

★ Round 3 fix: `first_detected_date` is BANNED on episodes built from
  SIGNAL_MISS/DATA_FAILURE rows (nothing was actually detected) -- the field
  is now `first_signal_date` and is `None` whenever the episode's root_cause
  is SIGNAL_MISS or DATA_FAILURE. The full renamed field set:
  `episode_start_date`, `first_signal_date`, `first_action_eligible_date`,
  `episode_end_date`, `outcome_window_start`, `outcome_window_end`,
  `representative_forward_return_pct` (was `final_outcome_pct` -- it is the
  first row's return, never actually "final").
"""
from __future__ import annotations

import datetime as dt
import hashlib

NO_SIGNAL_FAMILY = "NO_SIGNAL"
FALLBACK_GAP_DAYS = 4  # only used when a row has no computed outcome_window_end at all


def _date(s: str) -> dt.date:
    return dt.date.fromisoformat(s)


def trigger_family(triggers_detected) -> str:
    if not triggers_detected:
        return NO_SIGNAL_FAMILY
    return "+".join(sorted(triggers_detected))


def _episode_id(subject: str, family: str, first_date: str, last_date: str, root_cause: str | None) -> str:
    payload = f"{subject}|{family}|{first_date}|{last_date}|{root_cause}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _windows_chain(prev: dict, cur: dict) -> bool:
    """True if `cur`'s entry_date falls within/adjacent to `prev`'s own real
    forward-measurement window -- the real anti-arbitrary-gap rule."""
    prev_end = prev.get("outcome_window_end")
    cur_entry = cur.get("entry_date")
    if prev_end and cur_entry:
        return cur_entry <= prev_end
    # Fallback only when window info is genuinely unavailable on either row.
    gap = (_date(cur["decision_date"]) - _date(prev["decision_date"])).days
    return gap <= FALLBACK_GAP_DAYS


def group_into_episodes(records: list[dict], outcome_field: str) -> list[dict]:
    """`records` must already carry `subject`, `decision_date`, `root_cause`
    (optional), `triggers_detected` (list, may be empty), `entry_date`
    (hypothetical_entry_at), `outcome_window_end`, and `outcome_field`."""
    by_key: dict[tuple, list[dict]] = {}
    for r in records:
        family = trigger_family(r.get("triggers_detected"))
        by_key.setdefault((r["subject"], family, r.get("root_cause")), []).append(r)

    episodes = []
    for (subject, family, root_cause), rows in by_key.items():
        rows = sorted(rows, key=lambda r: (r.get("entry_date") or r["decision_date"], r["decision_date"]))
        current: list[dict] = []
        for row in rows:
            if not current:
                current = [row]
                continue
            if _windows_chain(current[-1], row):
                current.append(row)
            else:
                episodes.append(_build_episode(subject, family, root_cause, current, outcome_field))
                current = [row]
        if current:
            episodes.append(_build_episode(subject, family, root_cause, current, outcome_field))

    episodes.sort(key=lambda e: (e["subject"], e["episode_start_date"]))
    return episodes


NO_SIGNAL_ROOT_CAUSES = ("SIGNAL_MISS", "DATA_FAILURE")


def _build_episode(subject: str, family: str, root_cause: str | None, rows: list[dict], outcome_field: str) -> dict:
    rows_by_date = sorted(rows, key=lambda r: r["decision_date"])
    first, last = rows_by_date[0], rows_by_date[-1]

    signal_rows = [r for r in rows if root_cause not in NO_SIGNAL_ROOT_CAUSES and r.get("triggers_detected")]
    first_signal_date = min((r["decision_date"] for r in signal_rows), default=None)

    action_eligible_rows = [r for r in rows if root_cause != "DATA_FAILURE"]
    first_action_eligible_date = min((r["decision_date"] for r in action_eligible_rows), default=None)

    window_starts = [r["entry_date"] for r in rows if r.get("entry_date")]
    window_ends = [r["outcome_window_end"] for r in rows if r.get("outcome_window_end")]

    return {
        "episode_id": _episode_id(subject, family, first["decision_date"], last["decision_date"], root_cause),
        "subject": subject,
        "trigger_family": family,
        "root_cause": root_cause,
        "episode_start_date": first["decision_date"],
        "episode_end_date": last["decision_date"],
        "first_signal_date": first_signal_date,   # None on pure SIGNAL_MISS/DATA_FAILURE episodes
        "first_action_eligible_date": first_action_eligible_date,
        "outcome_window_start": min(window_starts) if window_starts else None,
        "outcome_window_end": max(window_ends) if window_ends else None,
        "max_delay_days": (_date(last["decision_date"]) - _date(first["decision_date"])).days,
        "daily_rows_deduped": len(rows),
        "representative_forward_return_pct": first.get(outcome_field),
        "representative_horizon_used": first.get("materiality_horizon_used"),
        "evidence_sha256": first.get("evidence_sha256"),
        "source": first.get("source"),
    }
