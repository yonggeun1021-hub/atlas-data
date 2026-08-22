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
     `_verify_raw_source_citation`, `_git_exact_content_first_seen`, with
     `forbid_test_root=False`) -- "classifier mechanics below the
     production evidence boundary", never through the real `build_packet()`
     entry point, which is not reachable with `forbid_test_root=False` from
     any code outside this module's own test-only call sites.
  2. **`captured_at` was a self-declared backdate, not proven PIT
     availability.** Every committed fixture in the round-5 PR was first
     added to THIS repo's git history on 2026-08-23, yet declared
     `captured_at=2026-08-14` -- the verifier trusted that field, which is
     the exact retroactive-creation problem this whole workstream exists to
     prevent. `_git_exact_content_first_seen` (round 7; round 6's path-only
     `_git_first_commit_timestamp` is retired) now queries this repo's REAL
     git history for the earliest commit whose stored content at that path
     is byte-identical to the file's current content, and that
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

★ CIO round 7 approved the mock-based classifier-arithmetic test design
  (`test/test_price_reflection.py`'s `mocked_event_evidence_verification`/
  `mocked_eg_canonical_verification`) as sound in principle, but found the
  PRODUCTION provenance implementation still had 4 P1 defects and 1 P2:

  1. **Path-level first-add was insufficient.** `_git_first_commit_
     timestamp` (retired) only checked when a PATH was first added --
     editing that same path's CONTENT today would retain the ORIGINAL
     file's first-seen date while the verifier reads TODAY's (edited)
     bytes. `_git_exact_content_first_seen` replaces it: it walks every
     commit that ever touched the path and finds the EARLIEST one whose
     git-recorded content is byte-for-byte identical to what's on disk
     right now. Editing a file always produces a brand-new first-seen date.
  2. **The raw primary-source document had no git-availability check at
     all** -- `_verify_raw_source_citation` only hash-verified current
     bytes and trusted the self-declared `published_at`. It now runs the
     SAME `_verify_first_availability` gate the envelope itself uses,
     treating `published_at` as its own "declared_at" subject to the
     identical real, content-addressed ordering check.
  3. **`captured_at`/`published_at` could be declared AFTER `decision_at`
     and still pass** -- round 6's `_verify_first_availability` only
     checked that the declared value didn't PRECEDE the real first-seen
     time; it never checked the declared value against the UPPER bound.
     Now enforces the full `first_seen <= declared_at <= decision_at`
     chain everywhere this gate is used (envelope, raw source, EG record).
  4. **A quoted phrase anywhere in the raw text was accepted regardless of
     its actual meaning** -- an envelope could claim `direction=POSITIVE`
     while citing a "revenue decline" quotation and nothing caught it,
     because nothing ever checked the SEMANTIC direction of the observed
     content against the claim. There is no ratified NLP/sentiment
     derivation rule in this repo (and this module will never invent one
     unilaterally), so per the CIO's explicit alternative, the raw source
     document is now required to be REAL STRUCTURED JSON carrying its own
     explicit, human-curated `observed_direction` field -- an authoritative
     source schema field, not a free assertion -- which must literally
     equal the envelope's claimed `direction`.
  5. **`locator` was checked for non-emptiness only**, never actually
     verified against the document. It must now name a real top-level key
     in the raw source's parsed JSON whose value genuinely contains
     `observed_fact` -- proving the citation is anchored to a real,
     resolvable location, not merely a free-text label.
  6. **`_git_first_commit_timestamp` used author time (`%aI`)**, a field
     freely backdatable by whoever writes the commit. Replaced with
     committer time (`%cI`) everywhere -- see `_git_exact_content_first_
     seen`'s own docstring for the documented, still-remaining authority
     boundary (an offline, local-repository signal, not a third-party-
     observed timestamp).
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


def _git_relpath(path: Path) -> str:
    return path.resolve().relative_to(ROOT.resolve()).as_posix()


def _parse_git_iso(value: str) -> dt.datetime | None:
    """`%cI`/`%aI` (strict ISO 8601) may render a UTC offset as a trailing
    "Z" or as "+00:00" depending on the committer's local git/timezone
    config -- Python 3.9's `fromisoformat` only accepts the latter."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc)
    except ValueError:
        return None


def _git_exact_content_first_seen(path: Path) -> dt.datetime | None:
    """CIO round 7, required items 1/6: the REAL, content-addressed
    first-availability timestamp for the EXACT BYTES currently at `path` --
    not merely the path's own first-add commit (round 6's
    `_git_first_commit_timestamp`, retired). A path added before
    `decision_at` can be MODIFIED after `decision_at`; checking only the
    path's original add-commit would let the verifier read today's
    (possibly just-edited) content while retroactively inheriting the old
    file's first-seen date. This walks every commit that ever touched
    `path` (read-only, offline `git log`), and for each one asks git what
    content was actually stored at that path in that commit
    (`git show <commit>:<relpath>`) -- the EARLIEST commit whose stored
    content byte-for-byte matches what's on disk right now is the real
    first appearance of THIS SPECIFIC VERSION. Editing the file today
    produces a brand-new "first seen" (today), exactly as it should.

    Uses COMMITTER time (`%cI`), not author time (`%aI`) -- round 7,
    required item 6: author time is a field the commit's author can freely
    backdate to any value; committer time is set by the git client actually
    recording the commit and is a meaningfully harder value to backdate.
    This is still only an OFFLINE, LOCAL-REPOSITORY signal, not a
    third-party-observed timestamp -- a sufficiently privileged actor with
    write access to the machine making the commit could still forge it.
    Documented, remaining authority boundary: a genuinely tamper-resistant
    bound would come from a server-side-observed timestamp this module does
    not have offline access to (e.g. GitHub's own recorded push/commit time
    via its API, or a signed append-only ingestion manifest) -- committer
    time is this round's CIO-specified minimum bar, not a claim of perfect
    tamper-resistance.

    Returns `None` -- never a fallback to any other value -- if git is
    unavailable, the path has no history, or the exact current content
    never matches any historical version recorded for that path (which can
    happen for content resolved from a symlink or an uncommitted/dirty
    working tree file); callers must treat `None` as NOT_COMPUTABLE."""
    try:
        relpath = _git_relpath(path)
    except ValueError:
        return None
    try:
        current_bytes = path.read_bytes()
    except OSError:
        return None
    try:
        log = subprocess.run(
            ["git", "log", "--format=%H|%cI", "--", relpath],
            cwd=str(ROOT), capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if log.returncode != 0:
        return None
    entries = [ln.strip() for ln in log.stdout.splitlines() if ln.strip()]
    if not entries:
        return None

    matches = []
    for entry in entries:
        parts = entry.split("|", 1)
        if len(parts) != 2:
            continue
        commit_hash, committer_date = parts
        try:
            show = subprocess.run(
                ["git", "show", f"{commit_hash}:{relpath}"],
                cwd=str(ROOT), capture_output=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if show.returncode != 0:
            continue
        if show.stdout == current_bytes:
            matches.append(committer_date)
    if not matches:
        return None
    # `git log` lists newest-first; the LAST match is the earliest commit
    # whose stored content matches the exact bytes on disk right now.
    return _parse_git_iso(matches[-1])


def _verify_first_availability(
    path: Path, declared_at: dt.datetime, decision_at: dt.datetime, label: str,
) -> dt.datetime:
    """Shared, content-addressed PIT-availability gate for the Event
    Evidence Envelope, the raw primary-source document, and the P8-09
    canonical record (CIO round 6, required item 2 / round 7, required
    item 2: apply identically to all three). Enforces the FULL ordering
    `first_seen <= declared_at <= decision_at` (round 7, required item 3)
    -- a `declared_at` (e.g. `captured_at`/`published_at`) AFTER
    `decision_at` is rejected here too, not merely a `declared_at` that
    precedes `first_seen`; round 6 only checked the latter half. Returns
    the real, git-verified `first_authoritative_seen_at` on success; raises
    on any of: exact-content git history unavailable (NOT_COMPUTABLE),
    first appearance after `decision_at` (not yet available), the declared
    timestamp preceding the real first appearance (an impossible backdate),
    or the declared timestamp being AFTER `decision_at` (claiming a future
    capture)."""
    first_seen = _git_exact_content_first_seen(path)
    if first_seen is None:
        raise EventEvidenceError(f"{label}_FIRST_AVAILABILITY_NOT_COMPUTABLE:{path}")
    if first_seen > decision_at:
        raise EventEvidenceError(
            f"{label}_NOT_YET_AVAILABLE_AS_OF_DECISION:"
            f"first_authoritative_seen_at={first_seen.isoformat()}>decision_at={decision_at.isoformat()}"
        )
    if declared_at < first_seen:
        raise EventEvidenceError(
            f"{label}_DECLARED_TIMESTAMP_PRECEDES_FIRST_AUTHORITATIVE_APPEARANCE:"
            f"declared_at={declared_at.isoformat()}<first_authoritative_seen_at={first_seen.isoformat()}"
        )
    if declared_at > decision_at:
        raise EventEvidenceError(
            f"{label}_DECLARED_TIMESTAMP_AFTER_DECISION_AT:"
            f"declared_at={declared_at.isoformat()}>decision_at={decision_at.isoformat()}"
        )
    return first_seen


def _verify_raw_source_citation(
    citation: dict, claimed_direction: str, decision_at: dt.datetime, *, forbid_test_root: bool,
) -> dict:
    """CIO round 6, required item 3 (closed citation schema) hardened round
    7, required items 2/4/5:

    * `raw_source_ref` must resolve to a real committed file -- NEVER
      under `test/` in production (`forbid_test_root`) -- whose real
      recomputed sha256 matches `raw_source_sha256`.
    * `published_at` (the raw source's own real announcement/availability
      timestamp) must satisfy the FULL `first_seen <= published_at <=
      decision_at` ordering (round 7, item 3), where `first_seen` is now
      the raw source's OWN real, content-addressed git-availability
      (round 7, required item 2: "the raw source has no git-availability
      check at all" -- closed; a raw source file is subject to the exact
      same `_verify_first_availability` gate as the envelope itself).
    * The raw source document must be REAL STRUCTURED JSON (this module's
      own "authoritative source schema" for what a human curator recorded
      as genuinely observed -- see module docstring) with a top-level
      `observed_direction` field that MUST equal `claimed_direction` (round
      7, required item 4: a bare quoted phrase co-occurring with an
      unrelated or contradictory claimed `direction` used to pass; there is
      now no automated sentiment/NLP derivation anywhere in this module --
      `observed_direction` must be an explicit, structured field a human
      curator recorded, not inferred from prose).
    * `locator` must name a REAL top-level key of that JSON document (round
      7, required item 5: "actually verify locator... not just non-empty")
      whose string value CONTAINS `observed_fact` VERBATIM -- proving
      `observed_fact` is not merely present somewhere in the file, but
      specifically at the cited location.

    Raises on any failure. Returns `{"raw_source_ref", "raw_source_sha256",
    "published_at", "locator"}` on success."""
    if not isinstance(citation, dict) or set(citation) != REQUIRED_CITATION_FIELDS:
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_FIELDS_MISMATCH")
    raw_source_ref = citation.get("raw_source_ref")
    raw_source_sha256 = citation.get("raw_source_sha256")
    published_at = _parse_utc(citation.get("published_at"), "EVENT_EVIDENCE_CITATION_PUBLISHED_AT_INVALID")
    locator = citation.get("locator")
    if not isinstance(locator, str) or not locator.strip():
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_LOCATOR_INVALID")
    observed_fact = citation.get("observed_fact")
    if not isinstance(observed_fact, str) or not observed_fact.strip():
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_OBSERVED_FACT_INVALID")

    raw_path = _resolve_repo_file(raw_source_ref, forbid_test_root=forbid_test_root)
    _verify_hash(raw_path, raw_source_sha256)

    # Cheap, content-only checks first (fail fast, and keep these
    # independent of git subprocess timing/history for testability) --
    # the git-availability gate (round 7, item 2) runs LAST, after the
    # document is already known to be structurally valid and semantically
    # consistent with the claim.
    try:
        raw_document = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_RAW_SOURCE_NOT_STRUCTURED:{raw_path}") from exc
    if not isinstance(raw_document, dict):
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_RAW_SOURCE_NOT_STRUCTURED:{raw_path}")

    observed_direction = raw_document.get("observed_direction")
    if observed_direction not in ALLOWED_DIRECTION:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_RAW_SOURCE_OBSERVED_DIRECTION_INVALID:{raw_path}")
    if observed_direction != claimed_direction:
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_CITATION_DIRECTION_MISMATCH_WITH_RAW_SOURCE:"
            f"claimed={claimed_direction}!=observed={observed_direction}"
        )

    located_value = raw_document.get(locator)
    if not isinstance(located_value, str):
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_LOCATOR_NOT_FOUND_IN_RAW_SOURCE:{locator!r}")
    if observed_fact not in located_value:
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_CITATION_OBSERVED_FACT_NOT_FOUND_AT_LOCATOR:{locator!r}"
        )

    # Round 7, required item 2: the raw source gets the SAME real,
    # content-addressed, committer-time git-availability gate as the
    # envelope -- `published_at` is this document's own "declared_at".
    _verify_first_availability(raw_path, published_at, decision_at, "EVENT_EVIDENCE_RAW_SOURCE")

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

    raw_lineage = _verify_raw_source_citation(
        envelope["citation"], direction, decision_at, forbid_test_root=True,
    )

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
