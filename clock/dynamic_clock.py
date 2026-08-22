#!/usr/bin/env python3
"""P8-12 Dynamic Review Clock -- event-driven re-review state machine.

This is the genuinely NEW logic this module contributes (PR #210's
`replay/` package was retrospective audit only; it never needed a live
clock because every "decision_date" was a fixed historical replay point).
It turns a chronological stream of trigger detections for one
(subject, trigger_type) into a small number of **episodes**, applying four
behaviors the task requires explicitly:

  1. Duplicate-event suppression -- re-seeing the exact same evidence
     (same `evidence_hash`) inside an already-open episode is a no-op: it
     does not renew, does not extend expiry, does not open a new episode.
  2. Cooldown -- genuinely new evidence (different `evidence_hash`) that
     arrives before the currently-open episode's `expiry` folds INTO that
     same episode (renews it) instead of spawning a duplicate review
     candidate every single day the underlying condition keeps being true
     (e.g. BTC's PRICE_CONFIRMATION kept firing 2026-08-20/21/22 in a row on
     real repo evidence -- that is one continuing episode, not three
     separate review requests). Each renewal resets `next_review_at` to
     `cooldown_days` after the new evidence -- the human-review cadence a
     still-developing situation should be re-surfaced at.
  3. Expiry -- an episode that is not renewed within `expiry_days` of its
     last activity is closed as EXPIRED. This is the mechanism that
     replaces the old "wait for the next monthly review" default: instead
     of silently rotting for 30 days, a fired trigger has an explicit,
     short shelf life unless a human (or fresh evidence) engages it.
  4. Re-activation -- once an episode is EXPIRED, a fresh trigger detection
     for the same (subject, trigger_type) opens a brand-new episode rather
     than being silently dropped or merged into the dead one. The new
     episode records `reactivated_from_episode_id` for audit traceability.

Zero future-data leakage: this module never reads a wall clock and never
compares against anything except the `detected_at` values it is handed,
which are themselves already anti-lookahead-checked at construction time by
`replay.opportunity_trigger.build_trigger_event` (see
`clock/operational_scan.py`). Given the same event stream, this module
always produces the same episode history -- no hidden state, no I/O.
"""
from __future__ import annotations

import dataclasses
import datetime as dt

from replay.opportunity_trigger import payload_sha256

DATE_FMT = "%Y-%m-%d"


class DynamicClockError(ValueError):
    pass


# Cadence policy, one row per Opportunity Trigger Engine type (Control Loop
# doc section 6, "재검토 시계" / re-review clock):
#   - price/flow-structure triggers -> next trading day / 24h
#   - relative-strength confirmation -> next trading day (slightly lower
#     urgency than an outright price/flow break)
#   - event catalyst -> right after the event (kept at the same tight
#     cadence as price/flow so the policy is ready the day this trigger
#     type becomes computable; see operational_scan.py for why it is
#     NOT_COMPUTABLE today)
#   - fundamental revision -> next scheduled announcement (kept long/default
#     since this repo has no announcement-calendar evidence yet)
#   - expectation dislocation -> long-thesis default (30 days, same default
#     the doc explicitly reserves for long-horizon thesis review)
# `cooldown_days` governs behavior 2 above; `expiry_days` (>= cooldown_days)
# governs behavior 3. Calendar days, not trading days: this is a
# human-review SLA, not a market-data window.
TRIGGER_CLOCK_POLICY: dict[str, dict] = {
    "PRICE_CONFIRMATION": {"cooldown_days": 1, "expiry_days": 2, "urgency": "HIGH"},
    "INVALIDATION_TRIGGER": {"cooldown_days": 1, "expiry_days": 2, "urgency": "HIGH"},
    "FLOW_REVERSAL": {"cooldown_days": 1, "expiry_days": 2, "urgency": "HIGH"},
    "RELATIVE_STRENGTH_REVERSAL": {"cooldown_days": 1, "expiry_days": 2, "urgency": "MEDIUM"},
    "CATALYST_APPROACH": {"cooldown_days": 1, "expiry_days": 1, "urgency": "HIGH"},
    "FUNDAMENTAL_REVISION": {"cooldown_days": 30, "expiry_days": 30, "urgency": "MEDIUM"},
    "EXPECTATION_DISLOCATION": {"cooldown_days": 30, "expiry_days": 30, "urgency": "LOW"},
}


def policy_for(trigger_type: str) -> dict:
    policy = TRIGGER_CLOCK_POLICY.get(trigger_type)
    if policy is None:
        raise DynamicClockError(f"NO_CLOCK_POLICY_FOR_TRIGGER_TYPE:{trigger_type}")
    return policy


def _parse(date_str: str) -> dt.date:
    try:
        return dt.datetime.strptime(date_str, DATE_FMT).date()
    except (ValueError, TypeError) as exc:
        raise DynamicClockError(f"DATE_INVALID:{date_str!r}") from exc


def add_days(date_str: str, days: int) -> str:
    return (_parse(date_str) + dt.timedelta(days=days)).strftime(DATE_FMT)


def _series_id(subject: str, market: str, trigger_type: str) -> str:
    return payload_sha256({"subject": subject, "market": market, "trigger_type": trigger_type})


def _episode_id(series_id: str, opened_at: str, evidence_hash: str) -> str:
    return payload_sha256({"series_id": series_id, "opened_at": opened_at, "evidence_hash": evidence_hash})


@dataclasses.dataclass
class ClockEvent:
    """One raw trigger detection, already anti-lookahead-validated upstream.

    `detected_at` == the trigger's `decision_date` (when the condition
    became knowable/actionable). `evidence_available_at` == the trigger's
    `confirmed_at` or `first_seen_at` (the real-world date the underlying
    evidence itself is dated to -- may lag `detected_at` by the collector's
    own finalization delay, exactly the `evaluation_lag_days` concept from
    PR #210's `signal_replay_ledger.py`)."""

    detected_at: str
    evidence_available_at: str
    evidence_hash: str
    source: str
    strength: float


def _validate_ascending(events: list[ClockEvent]) -> None:
    """Fails closed on out-of-order input AND, as defense-in-depth against
    future-data leakage (this module's own hard requirement, not merely
    inherited from upstream callers), on any event whose evidence claims to
    be available AFTER it was detected -- that would mean Atlas "detected"
    something before the evidence for it existed."""
    prev = None
    for ev in events:
        d = _parse(ev.detected_at)
        avail = _parse(ev.evidence_available_at)
        if avail > d:
            raise DynamicClockError(
                f"EVIDENCE_AVAILABLE_AT_AFTER_DETECTED_AT:{ev.evidence_available_at} > {ev.detected_at}"
            )
        if prev is not None and d < prev:
            raise DynamicClockError(
                f"EVENTS_NOT_CHRONOLOGICAL:{ev.detected_at} before {prev.strftime(DATE_FMT)}"
            )
        prev = d


def build_episode_history(subject: str, market: str, trigger_type: str,
                           events: list[ClockEvent]) -> list[dict]:
    """Pure state machine: given ALL known detections for one
    (subject, trigger_type), in ascending `detected_at` order, returns the
    full episode history (closed + the current one, if still open).

    Fails closed (`DynamicClockError`) rather than guessing on out-of-order
    input or an unrecognized trigger_type -- this module never silently
    reorders or drops evidence."""
    policy = policy_for(trigger_type)
    _validate_ascending(events)
    series_id = _series_id(subject, market, trigger_type)

    episodes: list[dict] = []
    current: dict | None = None

    for ev in events:
        if current is not None and current["status"] == "ACTIVE":
            last_evidence_hash = current["evidence_trail"][-1]["evidence_hash"]
            if ev.evidence_hash == last_evidence_hash:
                # Behavior 1: duplicate-event suppression -- identical
                # evidence re-observed. No state change of any kind.
                continue
            if ev.detected_at <= current["expiry"]:
                # Behavior 2: cooldown -- fresh evidence that arrives before
                # this episode would otherwise go stale folds into the SAME
                # episode (renewal), rather than opening a duplicate review
                # candidate for a condition that is still developing.
                current["evidence_trail"].append(dataclasses.asdict(ev))
                current["last_detected_at"] = ev.detected_at
                current["expiry"] = add_days(ev.detected_at, policy["expiry_days"])
                current["next_review_at"] = add_days(ev.detected_at, policy["cooldown_days"])
                current["renewal_count"] += 1
                continue
            # Past this episode's expiry: close it, then fall through to
            # open a brand-new (reactivated) episode below.
            current["status"] = "EXPIRED"
            episodes.append(current)
            current = None

        # Behavior 4 (when `current` was just expired above, or this is the
        # very first detection for this series): open a new episode.
        reactivated_from = None
        if episodes and episodes[-1]["status"] == "EXPIRED":
            reactivated_from = episodes[-1]["episode_id"]
        current = {
            "episode_id": _episode_id(series_id, ev.detected_at, ev.evidence_hash),
            "series_id": series_id,
            "subject": subject,
            "market": market,
            "trigger_type": trigger_type,
            "status": "ACTIVE",
            "opened_at": ev.detected_at,
            "last_detected_at": ev.detected_at,
            "expiry": add_days(ev.detected_at, policy["expiry_days"]),
            "next_review_at": add_days(ev.detected_at, policy["cooldown_days"]),
            "urgency": policy["urgency"],
            "renewal_count": 0,
            "reactivated_from_episode_id": reactivated_from,
            "evidence_trail": [dataclasses.asdict(ev)],
        }

    if current is not None:
        episodes.append(current)
    return episodes


def close_stale_episodes(episodes: list[dict], as_of_date: str) -> list[dict]:
    """Behavior 3 (expiry) applied against a real "as of" date rather than
    only at the next detection: an ACTIVE episode whose `expiry` has passed
    as of `as_of_date` -- with no further evidence having arrived at all --
    is still stale and must not be reported as a live review candidate.
    Returns a NEW list (does not mutate the input) with any such episode's
    `status` flipped to `EXPIRED`."""
    out = []
    for ep in episodes:
        if ep["status"] == "ACTIVE" and ep["expiry"] < as_of_date:
            ep = {**ep, "status": "EXPIRED"}
        out.append(ep)
    return out
