#!/usr/bin/env python3
"""P8-10 Event Evidence Envelope verification (CIO rounds 5-6 on PR #212).

★ CIO round 4 fixed the "file exists + hash matches" gap for the RETURN
  figure (never a caller-supplied number, always internally computed from
  real, PIT-verified close prices). CIO round 5 found the SAME class of gap
  one layer up, on the EVENT ITSELF: `decision/price_reflection.py`'s round-4
  `_verify_evidence_citation` only proved a `source_ref`/`source_sha256`
  resolved to a real committed file with a matching hash -- it never proved
  that file was actually EVIDENCE OF the claimed subject/event/direction.
  Confirmed reproducible: `data/2026-08-20/krx.json` (a plain KRX price
  snapshot with zero event semantics) was cited as "evidence" of a POSITIVE
  event on `329180.KS`, and the hash-only check happily accepted it.

  Round 5 closed that gap with a real, structured, closed-vocabulary Event
  Evidence Envelope whose own subject/event_at/direction/source_class must
  match the caller's claim EXACTLY, plus a `captured_at` PIT-availability
  check and hard-raise-on-corruption semantics.

★ CIO round 6 found round 5's envelope was still not a production/test
  boundary, and its `captured_at` was still just a self-declared field:

  1. **`REGRESSION_FIXTURE` was an accepted production value.**
     `ALLOWED_CAPTURE_KIND` included it, so the committed
     `test/fixtures/event_evidence/*.json` files could drive a real
     `build_packet()` call to a non-`UNKNOWN` verdict. "It's not a current
     Pilot ticker" was never a real authority boundary -- `329180.KS` is a
     real listed subject. Closed TWO independent ways: (a)
     `ALLOWED_CAPTURE_KIND` is now `("LIVE_OFFICIAL_CAPTURE",)` only --
     `REGRESSION_FIXTURE` is not even a legal envelope value any more, so
     any envelope declaring it fails `_load_envelope`'s closed-vocab check
     immediately; (b) independently, `verify_event_reaction_claim` and
     `verify_expectations_gap_canonical_record` (the two functions
     `decision/price_reflection.py`'s real, operational `build_packet()`
     path calls) now REFUSE to even resolve a `source_ref`/`packet_ref`
     located under this repo's `test/` directory at all
     (`_resolve_repo_file(..., forbid_test_root=True)`) -- a structural,
     path-based production/test separation that does not depend on any
     self-declared field surviving intact. Test fixtures remain committed
     under `test/fixtures/event_evidence/` and are exercised ONLY by
     calling this module's lower-level primitives directly (`_load_envelope`,
     `_verify_raw_source_citation`, `_git_first_commit_timestamp`, with
     `forbid_test_root=False`) -- "classifier mechanics below the
     production evidence boundary", never through the real `build_packet()`
     entry point, which is not reachable with `forbid_test_root=False` from
     any code outside this module's own test-only call sites.
  2. **`captured_at` was a self-declared backdate, not proven PIT
     availability.** Every committed fixture in the round-5 PR was first
     added to THIS repo's git history on 2026-08-23, yet declared
     `captured_at=2026-08-14` -- the verifier trusted that field, which is
     the exact retroactive-creation problem this whole workstream exists to
     prevent. `_git_first_commit_timestamp` now queries this repo's REAL
     git history (`git log --follow --diff-filter=A`, offline, read-only)
     for the earliest commit that actually added the cited file, and that
     timestamp -- not the file's self-declared `captured_at` -- is the
     authoritative gate: `first_authoritative_seen_at <= decision_at` is
     required, AND the self-declared `captured_at` may never precede
     `first_authoritative_seen_at` (a caller cannot claim an earlier
     capture than the file's real, git-provable first appearance). If git
     history for the file is unavailable (e.g. uncommitted), the result is
     `NOT_COMPUTABLE` (rejected), never a fallback to the self-declared
     field. Applied to both the Event Evidence Envelope and the P8-09
     canonical record.
  3. **`citation` was an unconstrained dict that could contain only a free
     note.** An envelope asserting subject/event_at/direction was still
     just a newly-typed assertion, never evidence of one, unless it cited
     and verified the underlying raw official document. `citation` is now
     a CLOSED schema: `raw_source_ref` + `raw_source_sha256` (a real,
     hash-verified raw artifact -- resolved and hashed exactly like the
     envelope file itself), `published_at` (the raw source's own real
     announcement/availability timestamp, must be at-or-before the decision
     instant), `locator` (a required, non-empty description of where in the
     document the claimed language appears), and `observed_fact` (the
     actual quoted/extracted text) -- which `_verify_raw_source_citation`
     additionally confirms appears VERBATIM inside the raw source file's
     real decoded text content, not merely asserted alongside it. This is
     the "confirmation the document was genuinely observed" check: the
     `direction` an envelope declares is only ever legitimately grounded in
     this observed, hash-verified, location-anchored quotation -- never a
     bare assertion with nothing behind it.

  Until a genuine raw primary-source document is committed for a real
  subject, no envelope can ever pass ALL of: real `LIVE_OFFICIAL_CAPTURE`
  classification, real closed-schema citation to a real raw artifact, and
  real git-provable first-availability at-or-before the decision instant --
  so `LIVE_OFFICIAL_CAPTURE` remains genuinely unproducible for every real
  subject in this repo today, and every real subject's `reflection_status`
  stays honestly `UNKNOWN`, exactly as required.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
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
# ★ CIO round 6, required item 1: `REGRESSION_FIXTURE` is RETIRED from the
#   accepted envelope vocabulary entirely -- it is not merely discouraged,
#   it is not a legal value `_load_envelope` will ever accept. Test-only
#   fixtures continue to self-describe their non-production nature via
#   `citation.observed_fact`/file path/subject naming, but that labeling is
#   documentation, not the safety mechanism; the safety mechanism is that
#   this vocabulary literally has one member.
ALLOWED_CAPTURE_KIND = ("LIVE_OFFICIAL_CAPTURE",)

REQUIRED_ENVELOPE_FIELDS = {
    "schema_version", "subject", "event_at", "direction", "source_class",
    "capture_kind", "captured_at", "citation",
}
REQUIRED_CITATION_FIELDS = {
    "raw_source_ref", "raw_source_sha256", "published_at", "locator", "observed_fact",
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


def _resolve_repo_file(source_ref: str, *, forbid_test_root: bool) -> Path:
    """Resolves `source_ref` to a real file strictly inside this repo's
    root. Raises on a non-existent path or a path that escapes ROOT (path
    traversal) -- both are treated as corruption on a SUPPLIED citation,
    per the module docstring, never a soft downgrade.

    `forbid_test_root` is a REQUIRED keyword (no default) so every call
    site must explicitly declare which side of the production/test
    boundary it is on (CIO round 6, required items 1/2). Every call inside
    `decision/price_reflection.py`'s real, operational path passes `True`
    unconditionally, with no parameter or configuration anywhere that lets
    a caller flip it -- a `source_ref` resolving under `test/` is refused
    outright before any other check runs. Only this module's own
    below-the-production-boundary test helpers pass `False`."""
    if not isinstance(source_ref, str) or SOURCE_REF_RE.fullmatch(source_ref) is None:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_REF_INVALID:{source_ref!r}")
    try:
        candidate = (ROOT / source_ref).resolve()
        relative = candidate.relative_to(ROOT.resolve())
    except (ValueError, OSError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_SOURCE_REF_ESCAPES_REPO_ROOT:{source_ref}") from exc
    if forbid_test_root and relative.parts and relative.parts[0] == "test":
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_SOURCE_REF_UNDER_TEST_ROOT_FORBIDDEN_IN_PRODUCTION:{source_ref}"
        )
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


def _git_first_commit_timestamp(path: Path) -> dt.datetime | None:
    """CIO round 6, required items 3/4: the REAL, independently-verifiable
    first-availability timestamp for a committed file -- this repo's own
    git history, not the file's self-declared `captured_at`. Runs a
    read-only, offline `git log` (no network, no writes) asking for every
    commit that ADDED `path`, and returns the EARLIEST one's author
    timestamp. Returns `None` -- never a fallback to any other value -- if
    git is unavailable, the file has no add-history (e.g. uncommitted), or
    anything about the lookup is ambiguous; callers must treat `None` as
    NOT_COMPUTABLE and reject, exactly per the CIO's explicit instruction."""
    try:
        result = subprocess.run(
            ["git", "log", "--follow", "--diff-filter=A", "--format=%aI", "--", str(path)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    lines = [ln.strip() for ln in result.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    # `git log` lists newest-first; the LAST line is the earliest (first-add)
    # commit. `%aI` (strict ISO 8601) may render a UTC offset as a trailing
    # "Z" or as "+00:00" depending on the committer's local git/timezone
    # config -- Python 3.9's `fromisoformat` only accepts the latter.
    earliest = lines[-1]
    if earliest.endswith("Z"):
        earliest = earliest[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(earliest).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _verify_first_availability(
    path: Path, declared_captured_at: dt.datetime, decision_at: dt.datetime, label: str,
) -> dt.datetime:
    """Shared PIT-availability gate for both the Event Evidence Envelope
    and the P8-09 canonical record (CIO round 6, required item 2: "do this
    for both"). Returns the real, git-verified `first_authoritative_seen_at`
    on success; raises on any of: git history unavailable (NOT_COMPUTABLE),
    first appearance after `decision_at` (not yet available), or the
    self-declared `captured_at` preceding the real first appearance (an
    impossible backdate)."""
    first_seen = _git_first_commit_timestamp(path)
    if first_seen is None:
        raise EventEvidenceError(f"{label}_FIRST_AVAILABILITY_NOT_COMPUTABLE:{path}")
    if first_seen > decision_at:
        raise EventEvidenceError(
            f"{label}_NOT_YET_AVAILABLE_AS_OF_DECISION:"
            f"first_authoritative_seen_at={first_seen.isoformat()}>decision_at={decision_at.isoformat()}"
        )
    if declared_captured_at < first_seen:
        raise EventEvidenceError(
            f"{label}_CAPTURED_AT_PRECEDES_FIRST_AUTHORITATIVE_APPEARANCE:"
            f"captured_at={declared_captured_at.isoformat()}<first_authoritative_seen_at={first_seen.isoformat()}"
        )
    return first_seen


def _verify_raw_source_citation(
    citation: dict, decision_at: dt.datetime, *, forbid_test_root: bool,
) -> dict:
    """CIO round 6, required item 3: a closed citation schema requiring and
    verifying a real primary-source document -- `raw_source_ref` must
    resolve to a real committed file whose real recomputed sha256 matches
    `raw_source_sha256`; `published_at` (the raw source's own real
    announcement/availability timestamp) must be at-or-before
    `decision_at`; `locator` must be a real, non-empty description; and
    `observed_fact` must appear VERBATIM inside the raw source file's own
    decoded text content -- the "actual location of the claimed language"
    and "genuinely observed, not just referenced by note" requirements.
    Raises on any failure. Returns `{"raw_source_ref", "raw_source_sha256",
    "published_at", "locator"}` on success (never echoes `observed_fact`
    redundantly beyond what's already been verified)."""
    if not isinstance(citation, dict) or set(citation) != REQUIRED_CITATION_FIELDS:
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_FIELDS_MISMATCH")
    raw_source_ref = citation.get("raw_source_ref")
    raw_source_sha256 = citation.get("raw_source_sha256")
    published_at = _parse_utc(citation.get("published_at"), "EVENT_EVIDENCE_CITATION_PUBLISHED_AT_INVALID")
    if published_at > decision_at:
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_PUBLISHED_AT_IN_FUTURE")
    locator = citation.get("locator")
    if not isinstance(locator, str) or not locator.strip():
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_LOCATOR_INVALID")
    observed_fact = citation.get("observed_fact")
    if not isinstance(observed_fact, str) or not observed_fact.strip():
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_OBSERVED_FACT_INVALID")

    raw_path = _resolve_repo_file(raw_source_ref, forbid_test_root=forbid_test_root)
    _verify_hash(raw_path, raw_source_sha256)
    try:
        raw_text = raw_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_RAW_SOURCE_NOT_TEXT_DECODABLE:{raw_path}") from exc
    if observed_fact not in raw_text:
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_CITATION_OBSERVED_FACT_NOT_FOUND_IN_RAW_SOURCE:{raw_path}"
        )
    return {
        "raw_source_ref": raw_source_ref, "raw_source_sha256": raw_source_sha256,
        "published_at": citation["published_at"], "locator": locator,
    }


def verify_event_reaction_claim(
    *, subject: str, event_at: str, direction: str, source_class: str,
    source_ref: str, source_sha256: str, decision_at: dt.datetime,
) -> dict:
    """The ONLY way an `event_reaction` citation can ever be accepted in
    production. `subject`/`event_at`/`direction`/`source_class` are the
    CLAIM the caller is making (already format-validated by
    `decision/price_reflection.py`'s own structural check); `source_ref`/
    `source_sha256` must resolve to a real committed file -- NEVER under
    `test/` (CIO round 6, required items 1/2, `forbid_test_root=True`
    hardcoded, not a parameter) -- whose PARSED CONTENT is a valid Event
    Evidence Envelope independently asserting the SAME subject/event_at/
    direction/source_class, whose `capture_kind` is `LIVE_OFFICIAL_CAPTURE`
    (the only legal value at all -- see `ALLOWED_CAPTURE_KIND`), whose
    `citation` resolves and verifies a real raw primary-source document
    (round 6, required item 3, `_verify_raw_source_citation`), and whose
    REAL, git-verified first-availability is at-or-before `decision_at`
    (round 6, required items 2/4, `_verify_first_availability` -- never the
    self-declared `captured_at` alone). Raises `EventEvidenceError` on ANY
    failure -- never returns a soft "not verified" signal; a caller that
    reaches this function has already supplied a citation, so any failure
    here is corruption, not absence. Returns a dict with the verified
    envelope's renderable fields (including `capture_kind`,
    `first_authoritative_seen_at`, and raw-source lineage -- CIO round 6,
    required item 7) only on full success."""
    path = _resolve_repo_file(source_ref, forbid_test_root=True)
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
    first_seen = _verify_first_availability(path, captured_at_dt, decision_at, "EVENT_EVIDENCE")

    raw_lineage = _verify_raw_source_citation(envelope["citation"], decision_at, forbid_test_root=True)

    return {
        "capture_kind": envelope["capture_kind"],
        "first_authoritative_seen_at": first_seen.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "raw_source_ref": raw_lineage["raw_source_ref"],
        "raw_source_sha256": raw_lineage["raw_source_sha256"],
        "published_at": raw_lineage["published_at"],
        "locator": raw_lineage["locator"],
    }


def _load_envelope(path: Path) -> dict:
    """Parses `path` as JSON and validates it is structurally a real Event
    Evidence Envelope (`event_evidence_envelope/1`) -- exactly the check
    that fails, loudly, for a generic file like a KRX price snapshot (no
    `schema_version`/`event_at`/`direction`/... fields at all), and (round
    6) that `REGRESSION_FIXTURE` fails as an illegal `capture_kind` value.
    `path` has already been resolved (with the correct production/test
    boundary) by the caller before this function is ever invoked; the
    citation's own raw-source resolution happens separately, in
    `_verify_raw_source_citation`, which takes its OWN `forbid_test_root`."""
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


EG_RECORD_SCHEMA_VERSION = "expectations_gap_canonical_record/1"
REQUIRED_EG_RECORD_FIELDS = {"schema_version", "captured_at", "expectations_gap_packet"}


def verify_expectations_gap_canonical_record(
    *, expectations_gap_module, eg_contract: dict, subject: str, decision_date: str,
    decision_at: dt.datetime, packet_ref: str, packet_sha256: str,
) -> dict:
    """CIO round 5, required item 3 (production/test separation and
    git-verified first-availability added round 6, required items 1/2 --
    "apply the same separation to the P8-09 canonical-record fixture"): a
    P8-09 packet built fresh IN MEMORY (however internally hash-consistent)
    proves nothing about whether Atlas actually possessed it as of some
    earlier `decision_date` -- hash validity only proves internal
    consistency, never provenance. The caller supplies
    `expectations_gap_packet_ref` (a path) + `expectations_gap_packet_
    sha256` (that file's real hash) pointing at a REAL COMMITTED wrapper
    record -- NEVER under `test/` (`forbid_test_root=True` hardcoded) --
    this module reads and validates the packet FROM THAT FILE itself,
    never trusting a caller-supplied in-memory dict at all. The wrapper's
    REAL, git-verified first-availability (not its self-declared
    `captured_at` alone) must be at-or-before `decision_at`. Raises
    `EventEvidenceError` on any failure -- a supplied citation that turns
    out to be unresolvable/mismatched/not-yet-available is corruption, not
    absence. Returns a dict including `first_authoritative_seen_at` (round
    6, required item 7) alongside the validated packet fields."""
    path = _resolve_repo_file(packet_ref, forbid_test_root=True)
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

    first_seen = _verify_first_availability(path, captured_at_dt, decision_at, "EG_CANONICAL_RECORD")

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
    # reference-return start point -- rounds 5/6 keep that behavior
    # unchanged, they only add the captured_at/first-availability checks.
    if validated["decision_date"] > decision_date:
        raise EventEvidenceError("EG_CANONICAL_RECORD_DECISION_DATE_IN_FUTURE")
    validated = dict(validated)
    validated["first_authoritative_seen_at"] = first_seen.strftime("%Y-%m-%dT%H:%M:%SZ")
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
