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
always produces the same episode history -- no hidden state, no I/O beyond
reading the policy config file once.

★ CIO review round 1 on PR #211: cadence numbers moved OUT of code and into
  `config/dynamic_clock_policy.json` (`approval_status: "PROVISIONAL_CIO_MVP"`
  -- an engineering scheduling default, never presented as a ratified
  investment threshold). Korea now uses a business-day (Mon-Fri) calendar
  approximation instead of raw calendar days, since KRX does not trade on
  weekends -- but this repo has NO committed KRX holiday calendar, so every
  KOREA-market date this module derives carries an explicit
  `calendar_confidence` flag (`CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR`)
  rather than a silently-confident date. BTC/CRYPTO trade 24/7, so plain
  calendar-day arithmetic is exact for them
  (`CalendarConfidence.VERIFIED_24_7`).
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import json
from pathlib import Path

from replay.opportunity_trigger import payload_sha256

DATE_FMT = "%Y-%m-%d"
ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "dynamic_clock_policy.json"


class DynamicClockError(ValueError):
    pass


class CalendarConfidence:
    VERIFIED_24_7 = "VERIFIED_24_7"  # BTC/CRYPTO: every calendar day is a trading day, exact.
    UNVERIFIED_NO_HOLIDAY_CALENDAR = "UNVERIFIED_NO_HOLIDAY_CALENDAR"  # KOREA: weekday-only approximation.


_CALENDAR_TYPE_TO_CONFIDENCE = {
    "CALENDAR_DAY_24_7": CalendarConfidence.VERIFIED_24_7,
    "WEEKDAY_BUSINESS_DAY_APPROXIMATION": CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR,
}

_policy_cache: dict | None = None


def load_policy(path: Path = POLICY_PATH) -> dict:
    global _policy_cache
    if _policy_cache is not None and path == POLICY_PATH:
        return _policy_cache
    if not path.is_file():
        raise DynamicClockError(f"POLICY_FILE_NOT_FOUND:{path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    for required in ("policy_version", "approval_status", "trigger_types", "market_calendars"):
        if required not in doc:
            raise DynamicClockError(f"POLICY_FILE_MISSING_FIELD:{required}")
    if path == POLICY_PATH:
        _policy_cache = doc
    return doc


def policy_for(trigger_type: str) -> dict:
    doc = load_policy()
    policy = doc["trigger_types"].get(trigger_type)
    if policy is None:
        raise DynamicClockError(f"NO_CLOCK_POLICY_FOR_TRIGGER_TYPE:{trigger_type}")
    return policy


def calendar_confidence_for(market: str) -> str:
    doc = load_policy()
    calendar = doc["market_calendars"].get(market)
    if calendar is None:
        raise DynamicClockError(f"NO_MARKET_CALENDAR_FOR_MARKET:{market}")
    confidence = _CALENDAR_TYPE_TO_CONFIDENCE.get(calendar["calendar_type"])
    if confidence is None:
        raise DynamicClockError(f"UNKNOWN_CALENDAR_TYPE:{calendar['calendar_type']}")
    return confidence


def _parse(date_str: str) -> dt.date:
    try:
        return dt.datetime.strptime(date_str, DATE_FMT).date()
    except (ValueError, TypeError) as exc:
        raise DynamicClockError(f"DATE_INVALID:{date_str!r}") from exc


def add_days(date_str: str, days: int) -> str:
    """Plain calendar-day arithmetic -- exact for BTC/CRYPTO (24/7)."""
    return (_parse(date_str) + dt.timedelta(days=days)).strftime(DATE_FMT)


def add_business_days(date_str: str, days: int) -> str:
    """Adds `days` weekdays (Mon-Fri), skipping Saturday/Sunday only -- a
    real KRX public holiday inside the window is NOT skipped (no committed
    holiday-calendar evidence exists in this repo; see
    `calendar_confidence_for`, which callers must surface alongside any date
    this function returns)."""
    d = _parse(date_str)
    remaining = days
    while remaining > 0:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:  # Mon=0 .. Fri=4
            remaining -= 1
    return d.strftime(DATE_FMT)


def add_review_days(date_str: str, days: int, market: str) -> tuple[str, str]:
    """Market-aware date arithmetic. Returns (new_date, calendar_confidence).
    KOREA uses the business-day approximation; BTC/CRYPTO use plain calendar
    days (exact, since they trade 24/7)."""
    confidence = calendar_confidence_for(market)
    if confidence == CalendarConfidence.UNVERIFIED_NO_HOLIDAY_CALENDAR:
        return add_business_days(date_str, days), confidence
    return add_days(date_str, days), confidence


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
    # Exact collector timestamp when the cited committed snapshot provides
    # one.  This is lineage/diagnostic precision only: `detected_at` remains
    # date-granularity, so the event as a whole must not be treated as an
    # intraday-timestamped investment signal.
    evidence_captured_at: str | None = None
    evidence_capture_time_precision: str = "NOT_AVAILABLE"
    # Structured provider identity supplied by the source adapter.  These
    # fields are lineage only: this state machine never resolves them into a
    # canonical instrument and never infers them from `market`, `subject`, or
    # the human-readable citation path.
    source_name: str | None = None
    source_asset_id: str | None = None


def _parse_timestamp(value: str, *, field: str) -> dt.datetime:
    candidate = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = dt.datetime.fromisoformat(candidate)
    except (TypeError, ValueError) as exc:
        raise DynamicClockError(f"{field}_INVALID:{value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise DynamicClockError(f"{field}_TIMEZONE_REQUIRED:{value!r}")
    return parsed.astimezone(dt.timezone.utc)


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
        if ev.evidence_captured_at is None:
            if ev.evidence_capture_time_precision != "NOT_AVAILABLE":
                raise DynamicClockError("EVIDENCE_CAPTURE_PRECISION_WITHOUT_TIMESTAMP")
        else:
            if ev.evidence_capture_time_precision != "TIMESTAMP":
                raise DynamicClockError("EVIDENCE_CAPTURE_TIMESTAMP_PRECISION_MISMATCH")
            captured = _parse_timestamp(
                ev.evidence_captured_at, field="EVIDENCE_CAPTURED_AT"
            )
            # `detected_at` is still DATE_ONLY.  We can reject a capture on a
            # strictly later UTC calendar date, but a same-date ordering is
            # deliberately NOT asserted: that would manufacture an intraday
            # order from date-only trigger evidence.
            if captured.date() > d:
                raise DynamicClockError(
                    "EVIDENCE_CAPTURED_AT_AFTER_DATE_ONLY_DETECTED_AT:"
                    f"{ev.evidence_captured_at} > {ev.detected_at}"
                )
        if (ev.source_name is None) != (ev.source_asset_id is None):
            raise DynamicClockError("SOURCE_IDENTITY_LINEAGE_PARTIAL")
        if ev.source_name is not None:
            if not isinstance(ev.source_name, str) or not ev.source_name.strip():
                raise DynamicClockError("SOURCE_NAME_INVALID")
            if not isinstance(ev.source_asset_id, str) or not ev.source_asset_id.strip():
                raise DynamicClockError("SOURCE_ASSET_ID_INVALID")
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
                expiry, expiry_conf = add_review_days(ev.detected_at, policy["expiry_days"], market)
                next_review, next_conf = add_review_days(ev.detected_at, policy["cooldown_days"], market)
                current["evidence_trail"].append(dataclasses.asdict(ev))
                current["last_detected_at"] = ev.detected_at
                current["expiry"] = expiry
                current["expiry_calendar_confidence"] = expiry_conf
                current["next_review_at"] = next_review
                current["next_review_at_calendar_confidence"] = next_conf
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
        expiry, expiry_conf = add_review_days(ev.detected_at, policy["expiry_days"], market)
        next_review, next_conf = add_review_days(ev.detected_at, policy["cooldown_days"], market)
        current = {
            "episode_id": _episode_id(series_id, ev.detected_at, ev.evidence_hash),
            "series_id": series_id,
            "subject": subject,
            "market": market,
            "trigger_type": trigger_type,
            "status": "ACTIVE",
            "opened_at": ev.detected_at,
            "last_detected_at": ev.detected_at,
            "expiry": expiry,
            "expiry_calendar_confidence": expiry_conf,
            "next_review_at": next_review,
            "next_review_at_calendar_confidence": next_conf,
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
