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

★ CIO round 8 approved BOTH the test-only mock design (round 6/7) AND the
  exact-content-addressed first-seen direction (round 7) outright, but
  stress-testing round 7's time-ordering rule against how evidence is
  ACTUALLY collected in the real world exposed two further production
  defects:

  1. **Round 7's ordering was INVERTED for a raw source's `published_at`.**
     `_verify_first_availability` required `first_seen(raw file's OWN git
     history) <= published_at` -- but a raw source's `published_at` is an
     EXTERNAL, real-world publication instant that legitimately, ALWAYS
     precedes when Atlas ingests/commits a copy of it: `published_at`
     (external) -> `captured_at` (Atlas fetches/observes it) -> git commit
     (Atlas records it). Requiring `published_at >= git_first_seen` rejected
     virtually every genuinely legitimate citation, since real publication
     necessarily happens BEFORE the commit, never after. Fixed by modeling
     THREE separate clocks instead of conflating them:
       * `source_published_at` -- the raw source's own external publication
         time (self-declared, never git-checked directly).
       * `captured_at` -- Atlas's own collector observation/fetch time
         (self-declared, but this IS the value checked against git).
       * `exact_content_first_seen_at` -- the conservative, git-provable
         floor (round 7's `_git_exact_content_first_seen`).
     `_verify_first_availability` now computes `effective_available_at =
     max(captured_at, exact_content_first_seen_at)` and only requires
     `effective_available_at <= decision_at` -- a self-declared EARLIER
     `captured_at` can never make the effective availability earlier than
     git's real, provable floor (backdating is still structurally
     impossible), but a `captured_at` that is honestly LATER than the
     commit (the normal case: capture, then later commit) is no longer
     falsely rejected for merely being later than the file's own git
     history. Applied identically to the Event Evidence Envelope's own
     `captured_at`, the raw source citation's `captured_at` (see item 2),
     and the P8-09 EG canonical record's `captured_at` -- exactly the same
     three call sites round 7 touched. `effective_available_at` (not the
     raw git first-seen) is what gets persisted as `first_authoritative_
     seen_at` in the verified result.
  2. **The raw source citation gained a NEW required `captured_at` field**
     (Atlas's own fetch/observation time), separate from `published_at`
     (the source's own claimed publication time) -- `published_at` is no
     longer routed through `_verify_first_availability` at all (that was
     precisely the inverted round-7 rule); it is only checked structurally
     against `captured_at` (`published_at <= captured_at` -- you cannot
     have captured something before its real-world publication), and
     `captured_at` is what actually goes through the git-availability gate
     against the raw source file's own history.
  3. **`observed_direction` was a manually-typed assertion in an
     Atlas-authored JSON wrapper being compared to another manually-typed
     assertion (the envelope's own `direction`)** -- round 7's fix compared
     two copies of the same human claim, never independent verification.
     Retired entirely. `citation.direction_origin` is now a closed,
     two-member vocabulary (`DIRECTION_ORIGIN`), and the raw source
     document itself must carry ONE of two closed, module-owned structures
     depending on which origin is declared:
       * `OFFICIAL_STRUCTURED_FIELD` -- `official_direction_field:
         {"provider_field", "provider_value"}`, looked up against
         `RATIFIED_OFFICIAL_DIRECTION_FIELDS` (keyed by `(source_class,
         provider_field, provider_value)`), a CLOSED table this module
         owns -- adding a real entry (naming a genuine official provider
         schema field, e.g. a specific DART/SEC XBRL tag) IS the
         ratification act, a human reviewing and committing code, never a
         runtime toggle or caller-suppliable mapping. Starts EMPTY: no
         real official-provider structured-field integration exists
         anywhere in this repo (no SEC/DART/XBRL parser), so this route
         remains genuinely unproducible for any real subject today.
       * `RATIFIED_DERIVATION` -- `direction_derivation: {"rule_id",
         "rule_version", "inputs"}`, looked up against
         `RATIFIED_DIRECTION_RULES` (keyed by `(rule_id, rule_version)`),
         each entry a PURE function of real, structured numeric inputs
         pulled from the raw document -- never a re-typed assertion. Same
         "adding an entry IS the ratification act" posture, same EMPTY
         starting state.
     A raw document supplying only a bare, human-curated `observed_
     direction`-shaped field (or any structure not matching one of the two
     closed shapes above) fails outright -- `direction_origin` gates WHICH
     verification the module attempts, it never accepts a free-standing
     claimed-direction field as sufficient on its own, however it is
     phrased or however "structured" its JSON container looks. This
     module's real, committed tables are both intentionally EMPTY today;
     positive-path mechanics are exercised only via `mocked_ratified_
     direction_tables()`, a test-only context manager that temporarily
     overlays entries onto a SPECIFIC test-loaded module instance's tables
     (never this module's own global state unless a test explicitly loads
     this file as that instance), restored via `finally` -- the same
     scoping discipline as `mocked_event_evidence_verification()`.
  4. **Explicit `PROVENANCE_NOT_COMPUTABLE` status, distinct from generic
     missing-price-data.** `_verify_first_availability` already failed
     closed when git history was unavailable; round 8 renames that error
     code to literally read `..._PROVENANCE_NOT_COMPUTABLE:...` so it can
     never be mistaken for (or silently conflated with) an ordinary
     `PRICE_DATA_MISSING`-style gap -- this is specifically a git-history-
     availability diagnostic. This whole provenance-verification feature
     structurally REQUIRES full git history (`git log`/`git show` walking
     every commit for a path) -- any operational workflow invoking this
     module MUST use a full-history checkout (`fetch-depth: 0`), reusing
     the exact pattern already established for `replay/asset_identity.py`'s
     own git-history backdating check (see `.github/workflows/actions-
     pass.yml`'s `actions/checkout` step, which already sets `fetch-depth:
     0` for precisely this reason -- this module rides the same gate, no
     new workflow change was needed).
  5. **New, analogous ordering guard at the envelope level**: the
     envelope's own `captured_at` (when Atlas claims to have captured the
     EVENT) may never precede `event_at` (the event itself) -- you cannot
     capture evidence of something before it happened. Mirrors the
     citation's `published_at <= captured_at` check at the one level up.

  All real subjects in this repo continue to have zero committed
  `LIVE_OFFICIAL_CAPTURE` envelopes, so `reflection_status` stays honestly
  `UNKNOWN` for every one of them, unchanged by any of the above.
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
# ★ CIO round 8, defect 2: `captured_at` (Atlas's own fetch/observation
#   time, distinct from `published_at`) and `direction_origin` (the closed
#   route by which `direction` is established) are NEW required fields --
#   `observed_direction` is retired entirely (see module docstring).
REQUIRED_CITATION_FIELDS = {
    "raw_source_ref", "raw_source_sha256", "published_at", "captured_at",
    "locator", "observed_fact", "direction_origin",
}

# ★ CIO round 8, defect 2: the ONLY two legitimate routes by which
#   `direction` may be established -- never a bare human-curated field,
#   however structured. See module docstring and `_derive_direction`.
DIRECTION_ORIGIN = ("OFFICIAL_STRUCTURED_FIELD", "RATIFIED_DERIVATION")

# ★ CIO round 9, defect 2: "a code-table entry is not itself CIO
#   ratification." Round 8 conflated IMPLEMENTATION (code that knows HOW to
#   map a provider field, or HOW to compute a derivation, to a direction)
#   with AUTHORITY (a genuine Rule Authority decision that a given rule is
#   approved for operational use) -- its own comments literally said
#   "adding an entry here IS the ratification act", which bypasses the
#   established Atlas P5 Rule Authority model (implementation code +
#   ratification record = operationally usable; neither one alone is).
#   Round 9 splits these into three independent, all EMPTY, structures:
#
#   1. `OFFICIAL_DIRECTION_FIELD_IMPLEMENTATIONS` -- keyed by `(source_class,
#      provider_field, provider_value)`, this is PURELY code: "if a raw
#      document ever carries this exact triple, here is the direction it
#      would map to, and which (rule_id, rule_version) that mapping belongs
#      to." It answers "what would this rule DO", never "is this rule
#      APPROVED."
#   2. `DERIVATION_RULE_IMPLEMENTATIONS` -- keyed by `(rule_id,
#      rule_version)`, same posture for `RATIFIED_DERIVATION`: a pure
#      function of real, structured numeric inputs. Also never itself
#      authority.
#   3. `DIRECTION_RULE_AUTHORITY_REGISTRY` -- keyed by `(rule_id,
#      rule_version)`, this is the ONLY place a rule can become
#      operationally usable. Each entry is a closed-schema authority
#      record (`approval_status`/`ratified_at`/`evidence_ref`/
#      `evidence_sha256` -- see `REQUIRED_AUTHORITY_RECORD_FIELDS`), and
#      `evidence_ref`/`evidence_sha256` are hash-verified against a real
#      committed file exactly like every other citation in this module
#      (tamper-evident -- CIO round 9, P2), never a bare string a caller
#      could type without backing it with anything.
#
#   `_derive_direction`/`_lookup_ratified_rule_authority` REQUIRE a matching
#   entry in BOTH the relevant implementation table AND the authority
#   registry, cross-checked by `(rule_id, rule_version)` -- an
#   implementation with no ratified authority record is unusable, and a
#   ratified authority record with no matching implementation is equally
#   unusable (see the module's own round-9 regression tests). No rule may
#   become ratified merely because a developer commits code -- adding an
#   entry to an implementation table is never, by itself, the ratification
#   act; only a genuine, cross-matching authority record is. All three
#   tables are intentionally EMPTY in this module's real, committed
#   source: no real official-provider structured-field integration and no
#   real ratified numeric-derivation rule exist anywhere in this repo yet,
#   and this workstream's own authority registry starts and stays unratified
#   until a real Rule Authority decision is made and recorded. Nothing here
#   is ever mutated at runtime by production code -- there is no mock/
#   override helper anywhere in this module (CIO round 9, defect 1: the
#   round-8 `mocked_ratified_direction_tables()` helper lived here, which
#   let ANY caller importing this module inject rules at runtime, defeating
#   the empty-table lock; it has been removed entirely and now exists ONLY
#   inside `test/test_price_reflection.py`, patching that file's own
#   test-loaded module instance, restored via `finally` -- identical scope
#   to `mocked_event_evidence_verification()`).
OFFICIAL_DIRECTION_FIELD_IMPLEMENTATIONS: dict = {}
DERIVATION_RULE_IMPLEMENTATIONS: dict = {}
DIRECTION_RULE_AUTHORITY_REGISTRY: dict = {}

# ★ CIO round 9, P2: closed schema for an authority record. `approval_
#   status` is deliberately a small closed vocabulary -- only `RATIFIED`
#   unlocks operational use; `PROPOSED` exists so a record can genuinely
#   represent "under review, not yet usable" rather than simply being
#   absent (a real Rule Authority workflow state, not a synonym for
#   missing).
REQUIRED_AUTHORITY_RECORD_FIELDS = {"approval_status", "ratified_at", "evidence_ref", "evidence_sha256"}
AUTHORITY_APPROVAL_STATUS = ("RATIFIED", "PROPOSED")


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
    path: Path, captured_at: dt.datetime, decision_at: dt.datetime, label: str,
) -> dt.datetime:
    """Shared PIT-availability gate for the Event Evidence Envelope, the raw
    primary-source document's own `captured_at`, and the P8-09 canonical
    record (CIO round 6, required item 2 / round 7, required item 2 / round
    8, defect 1: apply identically to all three).

    CIO round 8 correction: round 7 REJECTED any `declared_at` that
    preceded the file's real git first-seen -- correct for a self-declared
    timestamp that might be an impossible backdate, but WRONG for the case
    this function is actually asked to police, because `captured_at` is
    Atlas's OWN observation time and is legitimately allowed to be earlier
    OR later than when the artifact recording it happened to be committed.
    What must never happen is the EFFECTIVE available-as-of time being
    EARLIER than what git can actually prove -- so instead of rejecting a
    `captured_at` that precedes `first_seen`, this now silently CLAMPS the
    effective availability to `max(captured_at, first_seen)`: a self-
    declared earlier `captured_at` can never make the effective
    availability earlier than the real, git-provable floor (backdating is
    still structurally impossible), but it is no longer falsely required to
    occur AFTER the commit either (the normal, legitimate case: capture,
    then commit later).

    Returns `effective_available_at` on success -- this is what callers
    persist as `first_authoritative_seen_at`, not the raw `first_seen`
    alone. Raises on either of: exact-content git history unavailable
    (`..._PROVENANCE_NOT_COMPUTABLE` -- round 8, defect/item 4: a distinct,
    explicitly-named status, never conflated with plain missing price
    data), or `effective_available_at` AFTER `decision_at` (covers both
    "file not yet committed as of the decision instant" and "a future-dated
    `captured_at` claims a capture that hasn't happened yet" -- either one
    pushes `effective_available_at` past `decision_at`)."""
    first_seen = _git_exact_content_first_seen(path)
    if first_seen is None:
        raise EventEvidenceError(f"{label}_PROVENANCE_NOT_COMPUTABLE:{path}")
    effective_available_at = max(captured_at, first_seen)
    if effective_available_at > decision_at:
        raise EventEvidenceError(
            f"{label}_NOT_YET_AVAILABLE_AS_OF_DECISION:"
            f"effective_available_at={effective_available_at.isoformat()}>decision_at={decision_at.isoformat()}"
        )
    return effective_available_at


def _lookup_ratified_rule_authority(rule_id: str, rule_version: str, *, forbid_test_root: bool) -> dict:
    """CIO round 9, defect 2: the ONLY function in this module that can turn
    an implementation into something operationally usable. A `(rule_id,
    rule_version)` pair is usable if and only if `DIRECTION_RULE_AUTHORITY_
    REGISTRY` has a matching, well-formed, `approval_status=RATIFIED`
    record whose `evidence_ref`/`evidence_sha256` resolve and hash-verify
    against a real committed file (tamper-evident -- CIO round 9, P2: a
    caller cannot simply type a claim, the same discipline every other
    citation in this module already enforces). Raises on: no record at all,
    a malformed record (wrong field set), an unrecognized `approval_
    status`, unverifiable evidence, or a well-formed but not-yet-`RATIFIED`
    record (e.g. `PROPOSED`) -- never a soft pass. This module's own real
    registry starts and stays EMPTY (see module docstring), so this always
    fails for any real rule today."""
    key = (rule_id, rule_version)
    record = DIRECTION_RULE_AUTHORITY_REGISTRY.get(key)
    if record is None:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_RULE_AUTHORITY_RECORD_NOT_FOUND:{key!r}")
    if not isinstance(record, dict) or set(record) != REQUIRED_AUTHORITY_RECORD_FIELDS:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_RULE_AUTHORITY_RECORD_MALFORMED:{key!r}")
    approval_status = record.get("approval_status")
    if approval_status not in AUTHORITY_APPROVAL_STATUS:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_RULE_AUTHORITY_STATUS_INVALID:{key!r}")
    _parse_utc(
        record.get("ratified_at"),
        f"EVENT_EVIDENCE_CITATION_DIRECTION_RULE_AUTHORITY_RATIFIED_AT_INVALID:{key!r}",
    )
    evidence_path = _resolve_repo_file(record.get("evidence_ref"), forbid_test_root=forbid_test_root)
    _verify_hash(evidence_path, record.get("evidence_sha256"))
    if approval_status != "RATIFIED":
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_RULE_AUTHORITY_NOT_RATIFIED:{key!r}")
    return record


def _derive_direction(
    raw_document: dict, direction_origin: str, source_class: str, raw_path: Path, *, forbid_test_root: bool,
) -> str:
    """CIO round 8, defect 2: `direction` may ONLY be established via one of
    the two closed routes named in `DIRECTION_ORIGIN` -- never a bare
    human-curated field, however structured. CIO round 9, defect 2:
    finding a matching IMPLEMENTATION (`OFFICIAL_DIRECTION_FIELD_
    IMPLEMENTATIONS`/`DERIVATION_RULE_IMPLEMENTATIONS`) is necessary but
    never sufficient on its own -- `_lookup_ratified_rule_authority` must
    ALSO find a genuine, cross-matching, RATIFIED authority record for that
    exact `(rule_id, rule_version)` before the derived direction is
    returned. Either one missing fails closed; both tables start EMPTY in
    this module's real, committed source (see module docstring)."""
    if direction_origin == "OFFICIAL_STRUCTURED_FIELD":
        field = raw_document.get("official_direction_field")
        if (
            not isinstance(field, dict) or set(field) != {"provider_field", "provider_value"}
            or not isinstance(field.get("provider_field"), str) or not isinstance(field.get("provider_value"), str)
        ):
            raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_OFFICIAL_DIRECTION_FIELD_INVALID:{raw_path}")
        impl_key = (source_class, field["provider_field"], field["provider_value"])
        impl = OFFICIAL_DIRECTION_FIELD_IMPLEMENTATIONS.get(impl_key)
        if impl is None:
            raise EventEvidenceError(
                f"EVENT_EVIDENCE_CITATION_OFFICIAL_DIRECTION_FIELD_IMPLEMENTATION_NOT_FOUND:{impl_key!r}"
            )
        _lookup_ratified_rule_authority(impl["rule_id"], impl["rule_version"], forbid_test_root=forbid_test_root)
        return impl["direction"]

    # direction_origin == "RATIFIED_DERIVATION" -- the only other closed-vocab value.
    derivation = raw_document.get("direction_derivation")
    if not isinstance(derivation, dict) or set(derivation) != {"rule_id", "rule_version", "inputs"}:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_DERIVATION_INVALID:{raw_path}")
    rule_key = (derivation.get("rule_id"), derivation.get("rule_version"))
    impl = DERIVATION_RULE_IMPLEMENTATIONS.get(rule_key)
    if impl is None:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_RULE_IMPLEMENTATION_NOT_FOUND:{rule_key!r}")
    inputs = derivation.get("inputs")
    if not isinstance(inputs, dict) or not all(
        name in inputs and isinstance(inputs[name], (int, float)) and not isinstance(inputs[name], bool)
        for name in impl["required_inputs"]
    ):
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_DERIVATION_INPUTS_INVALID:{raw_path}")
    _lookup_ratified_rule_authority(rule_key[0], rule_key[1], forbid_test_root=forbid_test_root)
    return impl["derive"](inputs)


def _verify_raw_source_citation(
    citation: dict, claimed_direction: str, source_class: str, decision_at: dt.datetime, *, forbid_test_root: bool,
) -> dict:
    """CIO round 6, required item 3 (closed citation schema) hardened round
    7, required items 2/4/5, and round 8, defects 1/2:

    * `raw_source_ref` must resolve to a real committed file -- NEVER
      under `test/` in production (`forbid_test_root`) -- whose real
      recomputed sha256 matches `raw_source_sha256`.
    * `published_at` (the raw source's own real, EXTERNAL announcement
      time) must be at-or-before `captured_at` (Atlas's own fetch/
      observation time) -- round 8, defect 1: `published_at` is NEVER
      itself routed through the git-availability gate any more (round 7's
      rule was backwards -- see module docstring); only `captured_at` is.
    * `captured_at` must satisfy `effective_available_at = max(captured_at,
      raw_source_first_seen) <= decision_at` via `_verify_first_
      availability` -- the raw source file's OWN real, content-addressed
      git-availability (round 7, required item 2), now correctly modeled
      (round 8, defect 1).
    * `direction_origin` (closed vocabulary, `DIRECTION_ORIGIN`) selects
      which of the two closed, module-owned ratified routes must
      independently reproduce `claimed_direction` from the raw document's
      OWN structured content -- see `_derive_direction` and module
      docstring (round 8, defect 2). There is no automated sentiment/NLP
      derivation anywhere in this module, and a bare human-curated
      assertion is never sufficient on its own, however it is phrased.
    * `locator` must name a REAL top-level key of that JSON document (round
      7, required item 5: "actually verify locator... not just non-empty")
      whose string value CONTAINS `observed_fact` VERBATIM -- proving
      `observed_fact` is not merely present somewhere in the file, but
      specifically at the cited location.

    Raises on any failure. Returns `{"raw_source_ref", "raw_source_sha256",
    "published_at", "captured_at", "locator", "direction_origin",
    "effective_available_at"}` on success."""
    if not isinstance(citation, dict) or set(citation) != REQUIRED_CITATION_FIELDS:
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_FIELDS_MISMATCH")
    raw_source_ref = citation.get("raw_source_ref")
    raw_source_sha256 = citation.get("raw_source_sha256")
    published_at = _parse_utc(citation.get("published_at"), "EVENT_EVIDENCE_CITATION_PUBLISHED_AT_INVALID")
    captured_at = _parse_utc(citation.get("captured_at"), "EVENT_EVIDENCE_CITATION_CAPTURED_AT_INVALID")
    direction_origin = citation.get("direction_origin")
    if direction_origin not in DIRECTION_ORIGIN:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_DIRECTION_ORIGIN_INVALID:{direction_origin!r}")
    locator = citation.get("locator")
    if not isinstance(locator, str) or not locator.strip():
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_LOCATOR_INVALID")
    observed_fact = citation.get("observed_fact")
    if not isinstance(observed_fact, str) or not observed_fact.strip():
        raise EventEvidenceError("EVENT_EVIDENCE_CITATION_OBSERVED_FACT_INVALID")

    # ★ round 8, defect 1: the only ordering check `published_at` itself is
    #   subject to -- you cannot have captured/fetched something before its
    #   real-world publication. `published_at` never touches git.
    if published_at > captured_at:
        raise EventEvidenceError(
            "EVENT_EVIDENCE_CITATION_PUBLISHED_AT_AFTER_CAPTURED_AT:"
            f"published_at={published_at.isoformat()}>captured_at={captured_at.isoformat()}"
        )

    raw_path = _resolve_repo_file(raw_source_ref, forbid_test_root=forbid_test_root)
    _verify_hash(raw_path, raw_source_sha256)

    # Cheap, content-only checks first (fail fast, and keep these
    # independent of git subprocess timing/history for testability) --
    # the git-availability gate runs LAST, after the document is already
    # known to be structurally valid and semantically consistent with the
    # claim.
    try:
        raw_document = json.loads(raw_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_RAW_SOURCE_NOT_STRUCTURED:{raw_path}") from exc
    if not isinstance(raw_document, dict):
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_RAW_SOURCE_NOT_STRUCTURED:{raw_path}")

    derived_direction = _derive_direction(
        raw_document, direction_origin, source_class, raw_path, forbid_test_root=forbid_test_root,
    )
    if derived_direction != claimed_direction:
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_CITATION_DIRECTION_MISMATCH_WITH_RAW_SOURCE:"
            f"claimed={claimed_direction}!=derived={derived_direction}"
        )

    located_value = raw_document.get(locator)
    if not isinstance(located_value, str):
        raise EventEvidenceError(f"EVENT_EVIDENCE_CITATION_LOCATOR_NOT_FOUND_IN_RAW_SOURCE:{locator!r}")
    if observed_fact not in located_value:
        raise EventEvidenceError(
            f"EVENT_EVIDENCE_CITATION_OBSERVED_FACT_NOT_FOUND_AT_LOCATOR:{locator!r}"
        )

    # ★ round 8, defect 1: `captured_at` (never `published_at`) is what
    #   goes through the corrected, max()-clamped git-availability gate.
    effective_available_at = _verify_first_availability(
        raw_path, captured_at, decision_at, "EVENT_EVIDENCE_RAW_SOURCE",
    )

    return {
        "raw_source_ref": raw_source_ref, "raw_source_sha256": raw_source_sha256,
        "published_at": citation["published_at"], "captured_at": citation["captured_at"],
        "locator": locator, "direction_origin": direction_origin,
        "effective_available_at": effective_available_at,
    }


def _verify_envelope_captured_at_not_before_event_at(envelope: dict) -> dt.datetime:
    """CIO round 8, item 5: mirrors the citation's own `published_at <=
    captured_at` check one level up -- Atlas cannot claim to have captured
    evidence of an event before the event itself occurred. Factored out of
    `verify_event_reaction_claim` (which hardcodes `forbid_test_root=True`
    with no parameter, so it can never be exercised below the production
    boundary) so this specific check remains independently testable against
    an envelope loaded via `_load_envelope` directly, regardless of the
    envelope file's location. Returns the parsed `captured_at` on success."""
    event_at_dt = _parse_utc(envelope["event_at"], "EVENT_EVIDENCE_ENVELOPE_EVENT_AT_INVALID")
    captured_at_dt = _parse_utc(envelope["captured_at"], "EVENT_EVIDENCE_ENVELOPE_CAPTURED_AT_INVALID")
    if captured_at_dt < event_at_dt:
        raise EventEvidenceError(
            "EVENT_EVIDENCE_ENVELOPE_CAPTURED_AT_PRECEDES_EVENT_AT:"
            f"captured_at={captured_at_dt.isoformat()}<event_at={event_at_dt.isoformat()}"
        )
    return captured_at_dt


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

    captured_at_dt = _verify_envelope_captured_at_not_before_event_at(envelope)
    effective_available_at = _verify_first_availability(path, captured_at_dt, decision_at, "EVENT_EVIDENCE")

    raw_lineage = _verify_raw_source_citation(
        envelope["citation"], direction, source_class, decision_at, forbid_test_root=True,
    )

    return {
        "capture_kind": envelope["capture_kind"],
        "first_authoritative_seen_at": effective_available_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
