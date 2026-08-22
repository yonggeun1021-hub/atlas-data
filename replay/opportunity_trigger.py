#!/usr/bin/env python3
"""Opportunity Trigger Event schema (P10-02/P10-03 Opportunity Capture PIT Replay).

Implements the event shape described in "Opportunity Capture Control Loop —
신호를 실제 행동으로 변환하는 설계" section 3 (Opportunity Trigger Engine):
seven trigger types, each carrying first_seen_at / confirmed_at / expires_at /
strength / source / subject.

★ This module is intentionally standalone. It does not import anything from
  `decision/`, `shadow/`, or `briefing/` -- see
  `test/test_opportunity_trigger.py::test_module_has_no_decision_or_shadow_imports`.
  It has no authority of any kind: it cannot create a trade_proposal, a Stage
  change, or any Buy/Action/Order artifact. It is a pure, append-only data
  shape plus validation.

⛔ `decision_date` here means "the date this event is being evaluated as of"
  -- not a promise that any action was taken. Nothing in this module sets
  capital, Stage, or trading authority.
"""
from __future__ import annotations

import copy
import dataclasses
import datetime as dt
import hashlib
import json
import re

TRIGGER_TYPES = (
    "FLOW_REVERSAL",
    "RELATIVE_STRENGTH_REVERSAL",
    "PRICE_CONFIRMATION",
    "FUNDAMENTAL_REVISION",
    "CATALYST_APPROACH",
    "EXPECTATION_DISLOCATION",
    "INVALIDATION_TRIGGER",
)

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:/=-]{0,127}$")


class OpportunityTriggerError(ValueError):
    pass


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise OpportunityTriggerError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise OpportunityTriggerError(code) from exc
    if parsed.isoformat() != value:
        raise OpportunityTriggerError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise OpportunityTriggerError(code)
    return value


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclasses.dataclass(frozen=True)
class OpportunityTriggerEvent:
    """One detected change-event, per Control Loop doc section 3.

    Fields mirror the doc's Korean field list one-to-one:
      first_seen_at, confirmed_at, expires_at, strength, source, subject.
    `trigger_id` is a deterministic sha256 of the other fields -- the same
    inputs always produce the same id (no random / wall-clock component).
    """

    trigger_type: str
    subject: str
    first_seen_at: str          # date, YYYY-MM-DD -- when the underlying evidence was first captured
    decision_date: str          # date, YYYY-MM-DD -- the replay date this event is evaluated as of
    confirmed_at: str | None    # date or None
    expires_at: str | None      # date or None
    strength: float             # 0.0 .. 1.0
    source: str                 # evidence citation, e.g. "data/2026-08-13/krx.json#005930"
    evidence_sha256: str        # sha256 of the underlying committed evidence file

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        d["trigger_id"] = self.trigger_id()
        return d

    def trigger_id(self) -> str:
        payload = {k: v for k, v in dataclasses.asdict(self).items()}
        return payload_sha256(payload)

    def to_json(self) -> str:
        return canonical_json(self.to_dict())


def build_trigger_event(
    trigger_type: str,
    subject: str,
    first_seen_at: str,
    decision_date: str,
    source: str,
    evidence_sha256: str,
    strength: float,
    confirmed_at: str | None = None,
    expires_at: str | None = None,
) -> OpportunityTriggerEvent:
    if trigger_type not in TRIGGER_TYPES:
        raise OpportunityTriggerError(f"TRIGGER_TYPE_INVALID:{trigger_type}")
    _token(subject, "SUBJECT_INVALID")
    fsa = _date(first_seen_at, "FIRST_SEEN_AT_INVALID")
    dd = _date(decision_date, "DECISION_DATE_INVALID")
    if fsa > dd:
        # ★ Hard anti-lookahead gate at construction time: an event cannot be
        #   "first seen" after the replay date that is evaluating it.
        raise OpportunityTriggerError("FIRST_SEEN_AT_AFTER_DECISION_DATE")
    if confirmed_at is not None:
        ca = _date(confirmed_at, "CONFIRMED_AT_INVALID")
        if ca < fsa:
            raise OpportunityTriggerError("CONFIRMED_AT_BEFORE_FIRST_SEEN_AT")
        if ca > dd:
            raise OpportunityTriggerError("CONFIRMED_AT_AFTER_DECISION_DATE")
    if expires_at is not None:
        ea = _date(expires_at, "EXPIRES_AT_INVALID")
        if ea < fsa:
            raise OpportunityTriggerError("EXPIRES_AT_BEFORE_FIRST_SEEN_AT")
    if not isinstance(strength, (int, float)) or isinstance(strength, bool):
        raise OpportunityTriggerError("STRENGTH_INVALID")
    if not (0.0 <= float(strength) <= 1.0):
        raise OpportunityTriggerError("STRENGTH_OUT_OF_RANGE")
    if not isinstance(source, str) or not source:
        raise OpportunityTriggerError("SOURCE_INVALID")
    if not isinstance(evidence_sha256, str) or SHA_RE.fullmatch(evidence_sha256) is None:
        raise OpportunityTriggerError("EVIDENCE_SHA256_INVALID")

    return OpportunityTriggerEvent(
        trigger_type=trigger_type,
        subject=subject,
        first_seen_at=first_seen_at,
        decision_date=decision_date,
        confirmed_at=confirmed_at,
        expires_at=expires_at,
        strength=float(strength),
        source=source,
        evidence_sha256=evidence_sha256,
    )


def validate_trigger_event(value: dict) -> dict:
    """Re-validate a dict (e.g. round-tripped through JSON) as a well-formed
    OpportunityTriggerEvent. Returns a deep copy on success."""
    if not isinstance(value, dict):
        raise OpportunityTriggerError("EVENT_NOT_A_DICT")
    required = {
        "trigger_type", "subject", "first_seen_at", "decision_date", "confirmed_at",
        "expires_at", "strength", "source", "evidence_sha256", "trigger_id",
    }
    if set(value) != required:
        raise OpportunityTriggerError("EVENT_FIELDS_MISMATCH")
    rebuilt = build_trigger_event(
        trigger_type=value["trigger_type"],
        subject=value["subject"],
        first_seen_at=value["first_seen_at"],
        decision_date=value["decision_date"],
        source=value["source"],
        evidence_sha256=value["evidence_sha256"],
        strength=value["strength"],
        confirmed_at=value["confirmed_at"],
        expires_at=value["expires_at"],
    )
    if rebuilt.trigger_id() != value["trigger_id"]:
        raise OpportunityTriggerError("TRIGGER_ID_MISMATCH")
    return copy.deepcopy(value)
