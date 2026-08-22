#!/usr/bin/env python3
"""P8-10 Event Evidence Envelope verification (CIO round 5 on PR #212).

★ CIO round 4 fixed the "file exists + hash matches" gap for the RETURN
  figure (never a caller-supplied number, always internally computed from
  real, PIT-verified close prices). CIO round 5 found the SAME class of gap
  one layer up, on the EVENT ITSELF: `decision/price_reflection.py`'s round-4
  `_verify_evidence_citation` only proved a `source_ref`/`source_sha256`
  resolved to a real committed file with a matching hash -- it never proved
  that file was actually EVIDENCE OF the claimed subject/event/direction.
  Confirmed reproducible: `data/2026-08-20/krx.json` (a plain KRX price
  snapshot with zero event semantics) was cited as "evidence" of a POSITIVE
  event on `329180.KS`, and the hash-only check happily accepted it -- any
  tracked file, of any kind, could "authorize" an arbitrary claimed
  direction as long as its real hash was supplied.

  This module closes that gap with a real, structured, closed-vocabulary
  **Event Evidence Envelope**: a small, self-contained JSON record that
  itself explicitly declares which `subject`, which `event_at` (a real
  timestamp, not just a date), which `direction`, and which `source_class`
  it is evidence for. `decision/price_reflection.py` never trusts a bare
  file citation for an event claim any more -- it must resolve to a REAL
  committed file (path + independently-recomputed sha256, same discipline
  as round 4) whose PARSED CONTENT is itself a valid Event Evidence
  Envelope, and that envelope's own subject/event_at/direction/source_class
  must match the caller's claim EXACTLY. A generic price/config/any-other
  file can never satisfy this, because it does not (and structurally cannot)
  contain those fields -- closing the exact defect the CIO's round-5 review
  demonstrated.

  Two further gaps closed in the same round:

  * PIT availability of the EVIDENCE ITSELF (not just the return's price
    endpoints, which round 4 already covered). A file merely existing in
    today's checkout is not proof Atlas could have used it at some earlier
    historical `decision_date` -- every envelope also carries its own
    `captured_at` timestamp (the same "evidence embeds its own capture
    time" convention `replay/evidence_index.py` already uses for KRX/BTC
    snapshots), and `captured_at` must be at-or-before the decision instant
    being evaluated. A future-committed envelope (captured_at in the
    future relative to decision_date) fails closed, reusing
    `replay.lookahead_gate.assert_no_signal_lookahead` for the date-level
    check plus an additional full-timestamp comparison this module adds.
  * Malformed/corrupt SUPPLIED citations now RAISE `EventEvidenceError`
    instead of silently downgrading. A citation the caller never supplied
    at all is genuinely "no evidence" (softly UNKNOWN, handled by
    `price_reflection.py`); a citation the caller DID supply that turns out
    to point at a non-existent file, a hash mismatch, a non-envelope file,
    or a content mismatch is corruption/tampering and must surface loudly,
    not blend into the same bucket as "nothing was ever cited".

★ Event timing (CIO round 5, required item 4): `event_at` is now a full UTC
  timestamp, not just a date, so pre-market/intraday/after-hours events are
  at least distinguishable in principle. This repo's real price evidence is
  DAILY-granularity only for every subject (KRX/BTC/US) -- there is no
  intraday series anywhere, and no CIO-ratified market-session-boundary
  rule exists to translate a specific time-of-day into "before/after that
  day's close". Rather than fabricate an unratified session table, this
  module applies the one policy that is always safe given daily-only data:
  a genuinely time-stamped `event_at` (any time-of-day other than the
  `00:00:00Z` sentinel used for "caller only knows the date") rolls the
  reference date back to the latest REAL, PIT-live trading date STRICTLY
  BEFORE `event_at`'s own calendar date -- guaranteeing the reference close
  can never accidentally already reflect the event's own trading session,
  regardless of whether the event was pre-market, intraday, or after-hours.
  A bare, midnight-UTC `event_at` (no real intraday precision at all) keeps
  timing `NOT_COMPUTABLE` (returns `None`), exactly per the CIO's explicit
  fallback instruction ("with date-only evidence, keep timing NOT_COMPUTABLE
  /UNKNOWN rather than claiming an event reaction").

★ `capture_kind` (`LIVE_OFFICIAL_CAPTURE` | `REGRESSION_FIXTURE`) mirrors
  `bridge/official_release_evidence.py`'s existing envelope vocabulary
  (`available_capture_kinds` / `blocked_capture_kinds`) so this module's
  test fixtures (committed under `test/fixtures/event_evidence/`, never
  under `data/`, always using a subject that is not a real Pilot/CIO-
  tracked ticker) can be genuinely, structurally verified end-to-end
  without ever being confused with real production evidence -- the safety
  property that matters is SUBJECT identity (no real Pilot subject has, or
  will accidentally get, a committed envelope), not this flag.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from replay import lookahead_gate as lg  # noqa: E402

SCHEMA_VERSION = "event_evidence_envelope/1"

UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SOURCE_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:-]{0,255}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

ALLOWED_DIRECTION = ("POSITIVE", "NEGATIVE")
# Real evidentiary categories this module recognizes as capable, in
# principle, of backing an event/direction claim -- deliberately closed and
# small; a generic price/config/universe/anything-else file can never be
# tagged with one of these from the inside, since it has no `source_class`
# field at all.
ALLOWED_SOURCE_CLASS = (
    "SEC_FILING_EVENT", "DART_FILING_EVENT", "OFFICIAL_RELEASE_EVENT", "GUIDANCE_CHANGE_EVENT",
)
ALLOWED_CAPTURE_KIND = ("LIVE_OFFICIAL_CAPTURE", "REGRESSION_FIXTURE")

REQUIRED_ENVELOPE_FIELDS = {
    "schema_version", "subject", "event_at", "direction", "source_class",
    "capture_kind", "captured_at", "citation",
}


class EventEvidenceError(ValueError):
    """Fail-closed P8-10 Event Evidence Envelope violation. Raised (never a
    silent downgrade) whenever a caller-SUPPLIED citation turns out to be
    unresolvable, hash-mismatched, malformed, semantically mismatched, or
    not yet PIT-available -- see module docstring."""


def _parse_utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise EventEvidenceError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise EventEvidenceError(code) from exc


def _resolve_repo_file(source_ref: str) -> Path:
    """Resolves `source_ref` to a real file strictly inside this repo's
    root. Raises on a non-existent path or a path that escapes ROOT (path
    traversal) -- both are treated as corruption on a SUPPLIED citation,
    per the module docstring, never a soft downgrade."""
    if not isinstance(source_ref, str) or SOURCE_REF_RE.fullmatch(source_ref) is None:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_REF_INVALID:{source_ref!r}")
    try:
        candidate = (ROOT / source_ref).resolve()
        candidate.relative_to(ROOT.resolve())
    except (ValueError, OSError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_REF_ESCAPES_REPO_ROOT:{source_ref}") from exc
    if not candidate.is_file():
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_FILE_NOT_FOUND:{source_ref}")
    return candidate


def _verify_hash(path: Path, expected_sha256: str) -> None:
    if not isinstance(expected_sha256, str) or SHA256_RE.fullmatch(expected_sha256) is None:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_SHA256_INVALID:{expected_sha256!r}")
    try:
        real = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_UNREADABLE:{path}:{exc}") from exc
    if real != expected_sha256:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_HASH_MISMATCH:{path}")


def _load_envelope(path: Path) -> dict:
    """Parses `path` as JSON and validates it is structurally a real Event
    Evidence Envelope (`event_evidence_envelope/1`) -- exactly the check
    that fails, loudly, for a generic file like a KRX price snapshot (no
    `schema_version`/`event_at`/`direction`/... fields at all)."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_UNREADABLE:{path}:{exc}") from exc
    if not isinstance(raw, dict) or set(raw) != REQUIRED_ENVELOPE_FIELDS:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_NOT_AN_EVENT_ENVELOPE:{path}")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SCHEMA_VERSION_MISMATCH:{path}")
    if not isinstance(raw.get("subject"), str) or not raw["subject"].strip():
        raise EventEvidenceError(f"EVENT_EVIDENCE_ENVELOPE_SUBJECT_INVALID:{path}")
    _parse_utc(raw.get("event_at"), f"EVENT_EVIDENCE_ENVELOPE_EVENT_AT_INVALID:{path}")
    if raw.get("direction") not in ALLOWED_DIRECTION:
        raise EventEvidenceError(f"EVENT_EVIDENCE_ENVELOPE_DIRECTION_INVALID:{path}")
    if raw.get("source_class") not in ALLOWED_SOURCE_CLASS:
        raise EventEvidenceError(f"EVENT_EVIDENCE_ENVELOPE_SOURCE_CLASS_INVALID:{path}")
    if raw.get("capture_kind") not in ALLOWED_CAPTURE_KIND:
        raise EventEvidenceError(f"EVENT_EVIDENCE_ENVELOPE_CAPTURE_KIND_INVALID:{path}")
    _parse_utc(raw.get("captured_at"), f"EVENT_EVIDENCE_ENVELOPE_CAPTURED_AT_INVALID:{path}")
    if not isinstance(raw.get("citation"), dict):
        raise EventEvidenceError(f"EVENT_EVIDENCE_ENVELOPE_CITATION_INVALID:{path}")
    return raw


def verify_event_reaction_claim(
    *, subject: str, event_at: str, direction: str, source_class: str,
    source_ref: str, source_sha256: str, decision_at: dt.datetime,
) -> dict:
    """The ONLY way an `event_reaction` citation can ever be accepted.
    `subject`/`event_at`/`direction`/`source_class` are the CLAIM the
    caller is making (already format-validated by
    `decision/price_reflection.py`'s own structural check); `source_ref`/
    `source_sha256` must resolve to a real committed file whose PARSED
    CONTENT is a valid Event Evidence Envelope independently asserting the
    SAME subject/event_at/direction/source_class -- and whose own
    `captured_at` must be at-or-before `decision_at` (PIT availability).
    Raises `EventEvidenceError` on ANY failure -- never returns a soft
    "not verified" signal; a caller that reaches this function has already
    supplied a citation, so any failure here is corruption, not absence.
    Returns the verified envelope dict only on full success."""
    path = _resolve_repo_file(source_ref)
    _verify_hash(path, source_sha256)
    envelope = _load_envelope(path)

    mismatches = [
        field for field, claimed, actual in (
            ("subject", subject, envelope["subject"]),
            ("event_at", event_at, envelope["event_at"]),
            ("direction", direction, envelope["direction"]),
            ("source_class", source_class, envelope["source_class"]),
        )
        if claimed != actual
    ]
    if mismatches:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CLAIM_MISMATCH:{','.join(mismatches)}")

    captured_at_dt = _parse_utc(envelope["captured_at"], "EVENT_EVIDENCE_ENVELOPE_CAPTURED_AT_INVALID")
    try:
        lg.assert_no_signal_lookahead(
            decision_at.date().isoformat(), [captured_at_dt.date().isoformat()],
            label="event_evidence_envelope.captured_at",
        )
    except lg.LookaheadViolation as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_NOT_YET_AVAILABLE_AS_OF_DECISION:{exc}") from exc
    if captured_at_dt > decision_at:
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_NOT_YET_AVAILABLE_AS_OF_DECISION:"
            f"captured_at={envelope['captured_at']}>decision_at={decision_at.isoformat()}"
        )
    return envelope


EG_RECORD_SCHEMA_VERSION = "expectations_gap_canonical_record/1"
REQUIRED_EG_RECORD_FIELDS = {"schema_version", "captured_at", "expectations_gap_packet"}


def verify_expectations_gap_canonical_record(
    *, expectations_gap_module, eg_contract: dict, subject: str, decision_date: str,
    decision_at: dt.datetime, packet_ref: str, packet_sha256: str,
) -> dict:
    """CIO round 5, required item 3: a P8-09 packet built fresh IN MEMORY
    (however internally hash-consistent) proves nothing about whether Atlas
    actually possessed it as of some earlier `decision_date` -- hash
    validity only proves internal consistency, never provenance. This is
    now the ONLY way `reflection_reference` can unlock a P8-09-anchored
    verdict: the caller supplies `expectations_gap_packet_ref` (a path) +
    `expectations_gap_packet_sha256` (that file's real hash) pointing at a
    REAL COMMITTED wrapper record -- this module reads and validates the
    packet FROM THAT FILE itself, never trusting a caller-supplied
    in-memory dict for this path at all. The wrapper's own `captured_at`
    (independent of anything the embedded packet self-reports) must be
    at-or-before `decision_at`, exactly the same PIT-availability
    discipline as `verify_event_reaction_claim` above. Raises
    `EventEvidenceError` on any failure -- a supplied citation that turns
    out to be unresolvable/mismatched/not-yet-available is corruption, not
    absence."""
    path = _resolve_repo_file(packet_ref)
    _verify_hash(path, packet_sha256)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_UNREADABLE:{path}:{exc}") from exc
    if not isinstance(raw, dict) or set(raw) != REQUIRED_EG_RECORD_FIELDS:
        raise EventEvidenceError(f"EG_CANONICAL_RECORD_NOT_A_VALID_RECORD:{path}")
    if raw.get("schema_version") != EG_RECORD_SCHEMA_VERSION:
        raise EventEvidenceError(f"EG_CANONICAL_RECORD_SCHEMA_VERSION_MISMATCH:{path}")
    captured_at_dt = _parse_utc(raw.get("captured_at"), f"EG_CANONICAL_RECORD_CAPTURED_AT_INVALID:{path}")

    try:
        lg.assert_no_signal_lookahead(
            decision_at.date().isoformat(), [captured_at_dt.date().isoformat()],
            label="expectations_gap_canonical_record.captured_at",
        )
    except lg.LookaheadViolation as exc:
        raise EventEvidenceError(f"EG_CANONICAL_RECORD_NOT_YET_AVAILABLE_AS_OF_DECISION:{exc}") from exc
    if captured_at_dt > decision_at:
        raise EventEvidenceError(
            f"EG_CANONICAL_RECORD_NOT_YET_AVAILABLE_AS_OF_DECISION:"
            f"captured_at={raw['captured_at']}>decision_at={decision_at.isoformat()}"
        )

    embedded = raw.get("expectations_gap_packet")
    if not isinstance(embedded, dict):
        raise EventEvidenceError(f"EG_CANONICAL_RECORD_PACKET_INVALID:{path}")
    try:
        validated = expectations_gap_module.validate_packet(embedded, eg_contract)
    except expectations_gap_module.ExpectationsGapError as exc:
        raise EventEvidenceError(f"EG_CANONICAL_RECORD_PACKET_INVALID:{exc}") from exc
    if validated["subject"] != subject:
        raise EventEvidenceError("EG_CANONICAL_RECORD_SUBJECT_MISMATCH")
    # CIO round 4 loosened this from exact equality to "at or before" so the
    # EG packet's own decision_date can serve as a genuine, earlier
    # reference-return start point -- round 5 keeps that behavior unchanged,
    # it only adds the captured_at PIT-availability check above.
    if validated["decision_date"] > decision_date:
        raise EventEvidenceError("EG_CANONICAL_RECORD_DECISION_DATE_IN_FUTURE")
    return validated


def select_pre_event_reference_date(price_evidence_module, subject: str, event_at: dt.datetime, decision_date: str) -> str | None:
    """CIO round 5, required item 4 -- see module docstring for the
    daily-granularity-only policy this implements. `price_evidence_module`
    is `decision/price_evidence.py`, passed in by the caller (never
    imported circularly here) so this stays a pure function of its inputs."""
    if event_at.time() == dt.time(0, 0, 0):
        return None
    _kind, series = price_evidence_module._series_for_subject(subject)
    if series is None:
        return None
    event_date = event_at.date().isoformat()
    candidates = [d for d in series.live_trading_dates_at_or_before(decision_date) if d < event_date]
    if not candidates:
        return None
    return candidates[-1]
