"""Canonical Security Identity resolver -- Identity Foundation stage.

Implements the 4-layer identity model (issuer / instrument / listing /
source_asset_id) and the row-level authority + anti-backdating PIT gate
designed in "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as this stage's
implementation baseline).

★ Rev 2 (CIO code review of HEAD c819a38, CHANGES_REQUIRED, 7 P0 defects
  fixed together in this pass):
   1. `approval_evidence_sha256` now verifies a REAL external evidence
      file's real bytes, and that file's own content must independently
      state the same rule_id/rule_version/RATIFIED -- this is a distinct
      concept from `business_payload_sha256` (self-consistency of the
      row's own business fields), which is now its own separate field.
      Neither field alone is a full defense; see `verify_business_payload`
      and `verify_approval_evidence` docstrings for exactly what each
      does and does not catch.
   2. `first_seen_at` is now cross-checked -- against real git
      committer-time history of the authority file when loaded from
      disk, or against a separate append-only registry otherwise --
      producing an internally-derived `verified_first_seen_at` that
      `real_usable_from` uses instead of the row's bare self-declared
      claim. A row whose first-seen time cannot be verified against
      either source resolves `IDENTITY_NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED`,
      never trusting the unverified claim.
   3. `require_instrument_id` no longer short-circuits to RESOLVED on
      mere structural existence. It now delegates to
      `resolve_instrument_by_id`, the real operational resolver, which
      runs the full RATIFIED + provenance + PIT gate exactly like every
      other layer.
   4. Every row (whether loaded from a file or dependency-injected as a
      plain dict) is validated by `validate_authority_row` before it is
      ever considered a resolution candidate -- `load_authority` /
      `load_scope_authority` validate at load time; `_resolve_layer_row`
      (the single shared pipeline every layer now goes through)
      validates again for injected data. A malformed row is a hard
      failure (raises `IdentityError`), never a silently-skipped or
      silently-accepted row.
   5. All temporal fields (`effective_from`/`effective_to`/`ratified_at`/
      `first_seen_at`/`decision_date`/verified-first-seen values) are
      parsed by a strict parser (`_parse_temporal`) into real UTC
      datetimes before any comparison -- `max()`/`<`/`>=` never operate
      on raw strings. Two values that fall on the same UTC calendar day
      but differ in precision (one DATE_ONLY, one a full timestamp)
      cannot be safely ordered without assuming a within-day position
      this module never assumes -- that comparison raises
      `TimePrecisionAmbiguous`, converted by every resolver into
      `IDENTITY_NOT_COMPUTABLE_TIME_PRECISION` (same discipline as the
      paired Dynamic Clock Candidate Validity Window design).
   6. Every layer (issuer / instrument / listing / source_alias /
      market_account_scope) now goes through the same shared pipeline
      (`_resolve_layer_row`), which requires EXACTLY ONE active row for
      the structural key under consideration -- more than one active row
      is always `IDENTITY_NOT_COMPUTABLE_AMBIGUOUS`, regardless of
      whether the rows agree or disagree on their target fields (issuer,
      instrument_type, account_scope, ...). Previously issuer/instrument
      resolution inside listing lookup silently picked the first
      RATIFIED row without any ambiguity check at all -- that gap is
      closed by routing every layer through the one shared function.
   7. `test/test_identity_foundation.py` is registered in
      `run_all.py::APPROVED_TESTS`, and the authoritative verification
      path for this repo is `ATLAS_DISPOSABLE_CHECKOUT=1
      python3 run_all.py --authoritative` in a fresh disposable clone --
      not an ad hoc `unittest discover`, which is not this repo's
      canonical verification path and can hang on live-network test
      files.

★ This module NEVER itself claims any row is RATIFIED. Ratification is an
  authority-record fact -- it is read, validated, and echoed, never
  inferred, upgraded, or fabricated here. Zero real RATIFIED rows exist in
  this repo as of this PR (`config/canonical_security_identity.json` and
  `config/market_account_scope_map.json` ship with empty record arrays).
  Every real resolution attempt against those files therefore correctly
  returns `IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD` -- that is the
  CORRECT outcome for this stage, not a shortfall.

★ Forbidden by design (see docs/identity_foundation_pr_notes.md):
   - no in-code mapping table
   - no hardcoded per-ticker/market special-casing (BTC/US/Korea)
   - no fabricated RATIFIED status
   - this module does not wire into the Shadow Matrix and does not touch
     Dynamic Clock timestamps

★ `portfolio_risk/portfolio_snapshot.py`'s `by_ticker` aggregation still
  groups by raw provider symbol, not `canonical_instrument_id` -- a real,
  already-found defect (BTC's XBT/XXBT alias pair could double-count).
  That defect is NOT fixed by this module and is NOT patched here with a
  symbol-normalization workaround; it is tracked as a dependent defect
  (background task `task_8dcdbccb`, being worked in a separate session)
  that can only be safely resolved once canonical-instrument adoption
  actually lands in that file. `group_positions_by_instrument` below
  exists only to DEMONSTRATE, in tests, why instrument-level grouping is
  the correct fix -- it is not wired into any real portfolio code path.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SECURITY_IDENTITY_PATH = ROOT / "config" / "canonical_security_identity.json"
MARKET_ACCOUNT_SCOPE_PATH = ROOT / "config" / "market_account_scope_map.json"

SUPPORTED_SECURITY_IDENTITY_POLICY_VERSIONS = frozenset({"canonical_security_identity/v1"})
SUPPORTED_MARKET_ACCOUNT_SCOPE_POLICY_VERSIONS = frozenset({"market_account_scope_map/v1"})

ALLOWED_APPROVAL_STATUS = frozenset({"PROVISIONAL", "RATIFIED"})

LAYER_ISSUER = "ISSUER"
LAYER_INSTRUMENT = "INSTRUMENT"
LAYER_LISTING = "LISTING"
LAYER_SOURCE_ALIAS = "SOURCE_ALIAS"
LAYER_MARKET_ACCOUNT_SCOPE = "MARKET_ACCOUNT_SCOPE"

_LAYER_ARRAY_KEY = {
    LAYER_ISSUER: "issuers",
    LAYER_INSTRUMENT: "instruments",
    LAYER_LISTING: "listings",
    LAYER_SOURCE_ALIAS: "source_aliases",
    LAYER_MARKET_ACCOUNT_SCOPE: "edges",
}

# Fields every authority-record row must carry, regardless of layer
# (Packet 1 v2 section 7 -- row-level authority contract; rev 2 adds
# `business_payload_sha256` as its own field, distinct from
# `approval_evidence_sha256` -- see module docstring item 1).
AUTHORITY_FIELDS = (
    "rule_id", "rule_version", "approval_status", "ratified_at",
    "approval_evidence_ref", "approval_evidence_sha256", "business_payload_sha256",
    "first_seen_at", "effective_from", "effective_to",
)

# Business (identity-bearing) fields per layer -- used both for structural
# validation and for `business_payload_sha256` hashing. The authority
# fields themselves are excluded from the hashed payload.
LAYER_BUSINESS_FIELDS = {
    LAYER_ISSUER: ("canonical_issuer_id", "issuer_name_reference", "predecessor_issuer_id"),
    LAYER_INSTRUMENT: ("canonical_instrument_id", "canonical_issuer_id", "instrument_type", "predecessor_instrument_id"),
    LAYER_LISTING: ("listing_id", "canonical_instrument_id", "market", "exchange", "currency", "ticker"),
    LAYER_SOURCE_ALIAS: ("source_name", "source_asset_id", "listing_id"),
    LAYER_MARKET_ACCOUNT_SCOPE: ("market", "account_scope"),
}

INSTRUMENT_TYPES = frozenset({
    "COMMON_STOCK", "PREFERRED_STOCK", "ADR", "CRYPTO_ASSET", "OTHER_UNCLASSIFIED",
})

# Every result this module emits echoes this block verbatim (never a
# subset, never silently altered) -- same "authority all false" pattern
# already established by clock/review_candidate.py's AUTHORITY_ALL_FALSE.
# This module resolves identity; it grants no operational authority.
AUTHORITY_ALL_FALSE = {
    "identity_resolution_authority": False,
    "market_scope_authority": False,
    "position_size_authority": False,
    "order_authority": False,
    "trading_authority": False,
}

# --- status vocabulary (Packet 1 v2 section 9, plus this stage's additions) ---
NOT_COMPUTABLE_NO_AUTHORITY_RECORD = "IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD"
NOT_COMPUTABLE_UNRATIFIED_RECORD = "IDENTITY_NOT_COMPUTABLE_UNRATIFIED_RECORD"
NOT_COMPUTABLE_AMBIGUOUS = "IDENTITY_NOT_COMPUTABLE_AMBIGUOUS"
NOT_COMPUTABLE_PIT_VIOLATION = "IDENTITY_NOT_COMPUTABLE_PIT_VIOLATION"
NOT_COMPUTABLE_SCOPE_MAP_MISSING = "IDENTITY_NOT_COMPUTABLE_SCOPE_MAP_MISSING"
NOT_COMPUTABLE_SCHEMA_VERSION_MISMATCH = "IDENTITY_NOT_COMPUTABLE_SCHEMA_VERSION_MISMATCH"
NOT_COMPUTABLE_LAYER_MISMATCH = "IDENTITY_NOT_COMPUTABLE_LAYER_MISMATCH"
NOT_COMPUTABLE_TAMPERED_RECORD = "IDENTITY_NOT_COMPUTABLE_TAMPERED_RECORD"
NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED = "IDENTITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED"
NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED = "IDENTITY_NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED"
NOT_COMPUTABLE_TIME_PRECISION = "IDENTITY_NOT_COMPUTABLE_TIME_PRECISION"
RESOLVED = "RESOLVED"


class IdentityError(ValueError):
    pass


class TimePrecisionAmbiguous(IdentityError):
    """Two temporal values fall on the same UTC calendar day but cannot
    be safely ordered without assuming a within-day position (at least
    one is DATE_ONLY). Never silently resolved either direction."""


# ---------------------------------------------------------------------------
# Strict temporal parsing / comparison (defect 5)
# ---------------------------------------------------------------------------

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FULL_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

DATE_ONLY = "DATE_ONLY"
FULL_TIMESTAMP = "FULL_TIMESTAMP"


def _parse_temporal(value: str) -> tuple[datetime, str]:
    """Strict parser -- exactly two allowed shapes: `YYYY-MM-DD` (treated
    as 00:00:00 UTC of that day, precision DATE_ONLY) or
    `YYYY-MM-DDTHH:MM:SSZ` (precision FULL_TIMESTAMP, explicit UTC).
    Anything else -- non-zero-padded fields, a naive/offset timestamp
    without literal `Z`, garbage -- raises IdentityError. There is no
    silent coercion."""
    if not isinstance(value, str):
        raise IdentityError(f"TEMPORAL_VALUE_NOT_A_STRING:{value!r}")
    if _DATE_ONLY_RE.match(value):
        y, m, d = (int(x) for x in value.split("-"))
        try:
            return datetime(y, m, d, tzinfo=timezone.utc), DATE_ONLY
        except ValueError as e:
            raise IdentityError(f"TEMPORAL_VALUE_INVALID_DATE:{value!r}:{e}") from e
    if _FULL_TIMESTAMP_RE.match(value):
        try:
            dt = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise IdentityError(f"TEMPORAL_VALUE_INVALID_TIMESTAMP:{value!r}:{e}") from e
        return dt, FULL_TIMESTAMP
    raise IdentityError(f"TEMPORAL_VALUE_INVALID_FORMAT:{value!r}")


def _compare_temporal(a: str, b: str) -> int:
    """Strict chronological compare. Returns -1/0/1. Raises
    `TimePrecisionAmbiguous` if `a` and `b` fall on the same UTC calendar
    day, at least one is DATE_ONLY, and they are not the literal same
    value -- true relative order within that day is unknowable and this
    module never assumes one (mirrors the DATE_ONLY same-day discipline
    already established for Dynamic Clock candidates)."""
    dt_a, prec_a = _parse_temporal(a)
    dt_b, prec_b = _parse_temporal(b)
    if dt_a.date() == dt_b.date() and a != b and (prec_a == DATE_ONLY or prec_b == DATE_ONLY):
        raise TimePrecisionAmbiguous(f"SAME_DAY_MIXED_PRECISION:{a!r}:{b!r}")
    if dt_a < dt_b:
        return -1
    if dt_a > dt_b:
        return 1
    return 0


def _max_temporal(*values: str) -> str:
    """Returns whichever ORIGINAL string among `values` is chronologically
    latest (never a reformatted value -- preserves exact provenance of
    which field won). Raises TimePrecisionAmbiguous if any pairwise
    comparison along the way is ambiguous. `None` entries are ignored."""
    present = [v for v in values if v is not None]
    if not present:
        raise IdentityError("MAX_TEMPORAL_NO_VALUES")
    winner = present[0]
    for v in present[1:]:
        if _compare_temporal(v, winner) > 0:
            winner = v
    return winner


# ---------------------------------------------------------------------------
# Canonical hashing (business-payload self-consistency only -- see
# `verify_approval_evidence` for the independent, external-file check)
# ---------------------------------------------------------------------------

def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def business_payload(row: dict, layer: str) -> dict:
    """The identity-bearing subset of a row -- excludes every authority
    field so the hash can certify the row's own content without
    certifying its own certification."""
    fields = LAYER_BUSINESS_FIELDS[layer]
    return {k: row.get(k) for k in fields}


def verify_business_payload(row: dict, layer: str) -> bool:
    """Self-consistency check ONLY: does `business_payload_sha256` match
    a fresh hash of the row's own current business fields? This catches
    ACCIDENTAL drift (a business field edited without recomputing the
    hash) but is NOT a defense against a deliberate attacker who edits
    the content and correctly recomputes this hash too -- that class of
    attack is what `verify_approval_evidence` (an INDEPENDENT external
    file) exists to catch instead. Never conflate the two."""
    expected = row.get("business_payload_sha256")
    if not expected:
        return False
    return payload_sha256(business_payload(row, layer)) == expected


def verify_approval_evidence(row: dict, layer: str, root: Path = ROOT) -> bool:
    """Independent provenance check: `approval_evidence_ref` must point
    at a REAL file (absolute, or relative to `root`); `approval_evidence_sha256`
    must match the sha256 of that file's REAL BYTES (not a re-derivation
    of the row's own claimed content); and the file's own parsed content
    must independently assert the SAME `rule_id`/`rule_version`, an
    `approval_status` of `RATIFIED`, AND the SAME `business_payload_sha256`
    that was true of the row at the moment it was ratified
    (`approved_business_payload_sha256`). That last check is what closes
    the "tamper a business field, then correctly recompute
    `business_payload_sha256`" attack: the real evidence file is an
    immutable record of exactly which business content was approved, so a
    post-ratification business-field edit changes `business_payload_sha256`
    to a value the (untouched) evidence file no longer corroborates --
    without the attacker also forging the referenced evidence file's real
    bytes. Any failure (missing file, byte-hash mismatch, unparseable
    content, any of the four asserted fields mismatched) returns False;
    never raises."""
    ref = row.get("approval_evidence_ref")
    claimed_hash = row.get("approval_evidence_sha256")
    if not ref or not claimed_hash:
        return False
    path = Path(ref)
    if not path.is_absolute():
        path = root / path
    try:
        real_bytes = path.read_bytes()
    except OSError:
        return False
    if hashlib.sha256(real_bytes).hexdigest() != claimed_hash:
        return False
    try:
        content = json.loads(real_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    if not isinstance(content, dict):
        return False
    return (content.get("rule_id") == row.get("rule_id")
            and content.get("rule_version") == row.get("rule_version")
            and content.get("approval_status") == "RATIFIED"
            and content.get("approved_business_payload_sha256") == row.get("business_payload_sha256"))


# ---------------------------------------------------------------------------
# Row-level structural validation (defect 4 -- always invoked, hard failure)
# ---------------------------------------------------------------------------

def validate_authority_row(row: dict, layer: str) -> None:
    """Structural validation only. Raises IdentityError on malformed
    rows. Never asserts truthfulness of ratification -- that is decided
    by `_resolve_layer_row`, which additionally consults
    `approval_status`, provenance, and the PIT gate."""
    if layer not in LAYER_BUSINESS_FIELDS:
        raise IdentityError(f"UNKNOWN_LAYER:{layer}")
    missing_authority = [f for f in AUTHORITY_FIELDS if f not in row]
    if missing_authority:
        raise IdentityError(f"AUTHORITY_FIELDS_MISSING:{layer}:{missing_authority}")
    missing_business = [f for f in LAYER_BUSINESS_FIELDS[layer] if f not in row]
    if missing_business:
        raise IdentityError(f"BUSINESS_FIELDS_MISSING:{layer}:{missing_business}")
    if row["approval_status"] not in ALLOWED_APPROVAL_STATUS:
        raise IdentityError(f"APPROVAL_STATUS_INVALID:{row['approval_status']!r}")
    if row["approval_status"] == "PROVISIONAL" and row.get("ratified_at"):
        raise IdentityError("PROVISIONAL_ROW_MUST_NOT_CARRY_RATIFIED_AT")
    if row["approval_status"] == "RATIFIED":
        for f in ("ratified_at", "approval_evidence_ref", "approval_evidence_sha256", "business_payload_sha256"):
            if not row.get(f):
                raise IdentityError(f"RATIFIED_ROW_MISSING_{f.upper()}")
    # strict temporal parsing -- hard failure on any invalid format
    _parse_temporal(row["effective_from"])
    if row.get("effective_to") is not None:
        _parse_temporal(row["effective_to"])
        if _safe_le(row["effective_to"], row["effective_from"]):
            raise IdentityError("EFFECTIVE_INTERVAL_EMPTY_OR_INVERTED")
    if row.get("ratified_at") is not None:
        _parse_temporal(row["ratified_at"])
    if not row.get("first_seen_at"):
        raise IdentityError("FIRST_SEEN_AT_REQUIRED")
    _parse_temporal(row["first_seen_at"])
    if layer == LAYER_INSTRUMENT and row.get("instrument_type") not in INSTRUMENT_TYPES:
        raise IdentityError(f"INSTRUMENT_TYPE_INVALID:{row.get('instrument_type')!r}")


def _safe_le(a: str, b: str) -> bool:
    """effective_to <= effective_from structural check -- deliberately
    tolerant of TimePrecisionAmbiguous here (an interval whose bounds are
    ambiguously ordered is ALSO structurally invalid, just for a
    different reason; either way this returns True to reject it)."""
    try:
        return _compare_temporal(a, b) <= 0
    except TimePrecisionAmbiguous:
        return True


# ---------------------------------------------------------------------------
# PIT / anti-backdating gate (defect 2)
# ---------------------------------------------------------------------------

def real_usable_from(row: dict, verified_first_seen_at: str) -> str:
    """`max(effective_from, ratified_at, verified_first_seen_at)` -- the
    anti-backdating rule (CIO-mandated). Takes the ALREADY-VERIFIED
    first-seen time as a parameter (see `verify_first_seen_at`) rather
    than the row's bare self-declared `first_seen_at` claim -- using the
    unverified claim here would defeat the entire point of this gate."""
    return _max_temporal(row.get("effective_from"), row.get("ratified_at"), verified_first_seen_at)


def _row_active(row: dict, as_of: str) -> bool:
    """Plain interval membership using the row's OWN asserted
    effective_from/effective_to (deliberately NOT real_usable_from --
    ambiguity/active-set membership is a data-integrity question
    independent of whether a row is actually usable yet). May raise
    TimePrecisionAmbiguous."""
    start = row["effective_from"]
    end = row.get("effective_to")
    if _compare_temporal(start, as_of) > 0:
        return False
    if end is not None and _compare_temporal(as_of, end) >= 0:
        return False
    return True


def _intervals_overlap(a_start: str, a_end, b_start: str, b_end) -> bool:
    """Independently re-derived half-open-interval overlap check, using
    real chronological comparison (not string comparison)."""
    def _lt(x, y):
        return x is not None and y is not None and _compare_temporal(x, y) < 0
    left_ok = a_end is None or _compare_temporal(b_start, a_end) < 0
    right_ok = b_end is None or _compare_temporal(a_start, b_end) < 0
    return left_ok and right_ok


def detect_overlapping_intervals(rows: list[dict], key_fields: tuple) -> list[tuple[dict, dict]]:
    """Returns every pair of rows that share the same `key_fields` values
    and whose [effective_from, effective_to) intervals overlap. Standalone
    diagnostic utility -- the resolvers below no longer depend on this for
    ambiguity detection (they require exactly one ACTIVE row per key
    directly), but it remains useful for auditing a whole authority file
    at once."""
    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if tuple(a.get(k) for k in key_fields) != tuple(b.get(k) for k in key_fields):
                continue
            try:
                if _intervals_overlap(a["effective_from"], a.get("effective_to"),
                                       b["effective_from"], b.get("effective_to")):
                    pairs.append((a, b))
            except TimePrecisionAmbiguous:
                pairs.append((a, b))  # ambiguous overlap is still flagged, not silently skipped
    return pairs


# ---------------------------------------------------------------------------
# first_seen_at verification (defect 2) -- git history OR append-only registry
# ---------------------------------------------------------------------------

def _git_first_commit_time_for_content(path: Path, matches) -> str | None:
    """Walks the REAL git history of `path` (oldest first) and returns the
    ISO-8601 UTC committer time of the first commit whose version of the
    file satisfies `matches(parsed_json_doc)`. Returns None if the file
    has no git history, is not a JSON document at some revision, or no
    historical revision ever satisfies `matches` (including a brand-new,
    not-yet-committed file -- which correctly yields None, i.e.
    unverified, not a guessed date)."""
    try:
        repo_dir = path.parent
        log = subprocess.run(
            ["git", "log", "--follow", "--format=%H|%cI", "--", path.name],
            cwd=repo_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return None
    if not log:
        return None
    commits = [line.split("|", 1) for line in log.splitlines() if "|" in line]
    for commit_hash, committer_iso in reversed(commits):  # oldest first
        try:
            content = subprocess.run(
                ["git", "show", f"{commit_hash}:{path.name}"],
                cwd=path.parent, capture_output=True, text=True, check=True,
            ).stdout
            doc = json.loads(content)
        except Exception:
            continue
        if matches(doc):
            dt = datetime.fromisoformat(committer_iso)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _row_matcher(layer: str, row: dict):
    array_key = _LAYER_ARRAY_KEY[layer]
    target_hash = row.get("business_payload_sha256")

    def matches(doc) -> bool:
        if not isinstance(doc, dict):
            return False
        for candidate in doc.get(array_key, []):
            if candidate.get("business_payload_sha256") == target_hash:
                return True
        return False
    return matches


def _load_registry(registry_path) -> list[dict]:
    p = Path(registry_path)
    if not p.is_file():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


def record_first_seen(row: dict, layer: str, registry_path, at: str | None = None) -> str:
    """Append-only registration: if no entry for this row's EXACT content
    (`business_payload_sha256`) already exists in the registry, appends
    one with `at` (or, if omitted, this call's real current UTC time) as
    its recorded first-seen time. Returns the recorded (or, if one
    already existed, the pre-existing) first-seen time -- never
    overwrites an existing entry. `at=` is a test/backfill parameter for
    seeding an already-real historical registration; real production
    callers should omit it and let this record the actual current time."""
    p = Path(registry_path)
    entries = _load_registry(p)
    target_hash = row.get("business_payload_sha256")
    existing = [e for e in entries if e.get("business_payload_sha256") == target_hash]
    if existing:
        return min(e["first_seen_at"] for e in existing)
    recorded_at = at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = {"layer": layer, "business_payload_sha256": target_hash,
              "rule_id": row.get("rule_id"), "rule_version": row.get("rule_version"),
              "first_seen_at": recorded_at}
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(canonical_json(entry) + "\n")
    return recorded_at


def _registry_first_seen(row: dict, registry_path) -> str | None:
    entries = _load_registry(registry_path)
    target_hash = row.get("business_payload_sha256")
    matching = [e["first_seen_at"] for e in entries if e.get("business_payload_sha256") == target_hash]
    if not matching:
        return None
    return min(matching, key=lambda v: _parse_temporal(v)[0])


def verify_first_seen_at(row: dict, layer: str, git_path=None, registry_path=None) -> str | None:
    """Derives `verified_first_seen_at`: tries real git committer-time
    history of `git_path` first (when the authority document was loaded
    from an actual file on disk), then a separate append-only registry at
    `registry_path`. Returns None -- unverified, never a guessed date --
    if neither source has a matching entry."""
    if git_path is not None:
        found = _git_first_commit_time_for_content(Path(git_path), _row_matcher(layer, row))
        if found is not None:
            return found
    if registry_path is not None:
        found = _registry_first_seen(row, registry_path)
        if found is not None:
            return found
    return None


# ---------------------------------------------------------------------------
# Authority document loading (defect 4 -- validates every row at load time)
# ---------------------------------------------------------------------------

def load_authority(path=SECURITY_IDENTITY_PATH) -> dict:
    path = Path(path)
    if not path.is_file():
        raise IdentityError("AUTHORITY_FILE_NOT_FOUND")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("policy_version") not in SUPPORTED_SECURITY_IDENTITY_POLICY_VERSIONS:
        raise IdentityError(f"UNSUPPORTED_POLICY_VERSION:{doc.get('policy_version')!r}")
    for layer, array_key in (
        (LAYER_ISSUER, "issuers"), (LAYER_INSTRUMENT, "instruments"),
        (LAYER_LISTING, "listings"), (LAYER_SOURCE_ALIAS, "source_aliases"),
    ):
        for row in doc.get(array_key, []):
            validate_authority_row(row, layer)
    doc["_source_path"] = str(path)
    return doc


def load_scope_authority(path=MARKET_ACCOUNT_SCOPE_PATH) -> dict:
    path = Path(path)
    if not path.is_file():
        raise IdentityError("SCOPE_AUTHORITY_FILE_NOT_FOUND")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("policy_version") not in SUPPORTED_MARKET_ACCOUNT_SCOPE_POLICY_VERSIONS:
        raise IdentityError(f"UNSUPPORTED_SCOPE_POLICY_VERSION:{doc.get('policy_version')!r}")
    for row in doc.get("edges", []):
        validate_authority_row(row, LAYER_MARKET_ACCOUNT_SCOPE)
    doc["_source_path"] = str(path)
    return doc


# ---------------------------------------------------------------------------
# Result shape
# ---------------------------------------------------------------------------

def _result(status: str, decision_date: str, *, identity_basis: dict, **payload) -> dict:
    out = {
        "status": status,
        "decision_date": decision_date,
        "canonical_issuer_id": None,
        "canonical_instrument_id": None,
        "listing_id": None,
        "identity_basis": identity_basis,
    }
    out.update(payload)
    out["authority"] = dict(AUTHORITY_ALL_FALSE)
    return out


def _basis_from_row(row: dict | None, diagnostics: dict | None = None) -> dict:
    """Every result echoes exactly which rule/version/approval basis was
    used, plus (when the pipeline reached that far) what was
    independently verified -- never a bare status code with no traceable
    source."""
    base = {"rule_id": None, "rule_version": None, "approval_status": None,
            "ratified_at": None, "first_seen_at_claimed": None,
            "verified_first_seen_at": None, "business_payload_verified": None,
            "approval_evidence_verified": None}
    if row is not None:
        base.update({
            "rule_id": row.get("rule_id"),
            "rule_version": row.get("rule_version"),
            "approval_status": row.get("approval_status"),
            "ratified_at": row.get("ratified_at"),
            "first_seen_at_claimed": row.get("first_seen_at"),
        })
    if diagnostics:
        base.update(diagnostics)
    return base


# ---------------------------------------------------------------------------
# The single shared authority-gate pipeline (defects 3, 4, 6) -- every
# layer (issuer / instrument / listing / source_alias / market_account_scope)
# goes through exactly this function. No layer gets a shortcut.
# ---------------------------------------------------------------------------

def _resolve_layer_row(candidate_rows: list[dict], decision_date: str, layer: str,
                        git_path=None, registry_path=None):
    """Returns (status_or_None, row_or_None, basis_dict).
    status_or_None is None ONLY on full success (RESOLVED), in which case
    row_or_None is the single resolved row and basis_dict carries the
    verified diagnostics. On any failure, status_or_None is one of the
    IDENTITY_NOT_COMPUTABLE_* codes."""
    if not candidate_rows:
        return NOT_COMPUTABLE_NO_AUTHORITY_RECORD, None, _basis_from_row(None)

    for row in candidate_rows:
        validate_authority_row(row, layer)  # hard failure on malformed data -- defect 4

    try:
        active = [r for r in candidate_rows if _row_active(r, decision_date)]
    except TimePrecisionAmbiguous:
        return NOT_COMPUTABLE_TIME_PRECISION, candidate_rows[0], _basis_from_row(candidate_rows[0])

    if len(active) > 1:
        # defect 6: EXACTLY ONE active row required, full stop -- no
        # "only ambiguous if targets differ" exception.
        return NOT_COMPUTABLE_AMBIGUOUS, active[0], {
            "candidates": [_basis_from_row(r) for r in active]}
    if len(active) == 0:
        return NOT_COMPUTABLE_NO_AUTHORITY_RECORD, candidate_rows[0], _basis_from_row(candidate_rows[0])

    row = active[0]
    if row["approval_status"] != "RATIFIED":
        return NOT_COMPUTABLE_UNRATIFIED_RECORD, row, _basis_from_row(row)

    business_ok = verify_business_payload(row, layer)
    if not business_ok:
        return NOT_COMPUTABLE_TAMPERED_RECORD, row, _basis_from_row(row, {"business_payload_verified": False})

    evidence_ok = verify_approval_evidence(row, layer)
    if not evidence_ok:
        return NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED, row, _basis_from_row(
            row, {"business_payload_verified": True, "approval_evidence_verified": False})

    verified_first_seen = verify_first_seen_at(row, layer, git_path=git_path, registry_path=registry_path)
    if verified_first_seen is None:
        return NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED, row, _basis_from_row(
            row, {"business_payload_verified": True, "approval_evidence_verified": True})

    diagnostics = {"business_payload_verified": True, "approval_evidence_verified": True,
                    "verified_first_seen_at": verified_first_seen}
    try:
        usable_from = real_usable_from(row, verified_first_seen)
        if _compare_temporal(usable_from, decision_date) > 0:
            return NOT_COMPUTABLE_PIT_VIOLATION, row, _basis_from_row(row, diagnostics)
        eff_to = row.get("effective_to")
        if eff_to is not None and _compare_temporal(decision_date, eff_to) >= 0:
            return NOT_COMPUTABLE_PIT_VIOLATION, row, _basis_from_row(row, diagnostics)
    except TimePrecisionAmbiguous:
        return NOT_COMPUTABLE_TIME_PRECISION, row, _basis_from_row(row, diagnostics)

    return None, row, _basis_from_row(row, diagnostics)


# ---------------------------------------------------------------------------
# Core resolver: source_asset_id -> listing -> instrument -> issuer
# ---------------------------------------------------------------------------

def resolve_instrument_identity(source_name: str, source_asset_id: str, market: str,
                                 decision_date: str, authority: dict, registry_path=None) -> dict:
    """Resolve (source_name, source_asset_id) -> canonical_instrument_id
    (and its canonical_issuer_id / listing_id) as of `decision_date`,
    constrained to `market`. Every one of the four layers goes through
    `_resolve_layer_row` -- see module docstring item 6.

    `authority` is the loaded authority document (dependency injection --
    same reuse pattern as `replay/asset_identity.py`). `decision_date`
    itself is strictly parsed; an invalid value raises IdentityError
    (caller bug, not a business NOT_COMPUTABLE case).
    """
    _parse_temporal(decision_date)
    git_path = authority.get("_source_path")

    alias_rows = [r for r in authority.get("source_aliases", [])
                  if r.get("source_name") == source_name and r.get("source_asset_id") == source_asset_id]
    status, alias_row, alias_basis = _resolve_layer_row(alias_rows, decision_date, LAYER_SOURCE_ALIAS,
                                                          git_path=git_path, registry_path=registry_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"source_alias": alias_basis})

    listing_rows = [r for r in authority.get("listings", []) if r.get("listing_id") == alias_row["listing_id"]]
    status, listing_row, listing_basis = _resolve_layer_row(listing_rows, decision_date, LAYER_LISTING,
                                                              git_path=git_path, registry_path=registry_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"source_alias": alias_basis, "listing": listing_basis})
    if listing_row["market"] != market:
        return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date,
                        identity_basis={"source_alias": alias_basis, "listing": listing_basis})

    instrument_rows = [r for r in authority.get("instruments", [])
                        if r.get("canonical_instrument_id") == listing_row["canonical_instrument_id"]]
    status, instrument_row, instrument_basis = _resolve_layer_row(
        instrument_rows, decision_date, LAYER_INSTRUMENT, git_path=git_path, registry_path=registry_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={
            "source_alias": alias_basis, "listing": listing_basis, "instrument": instrument_basis})

    issuer_rows = [r for r in authority.get("issuers", [])
                   if r.get("canonical_issuer_id") == instrument_row["canonical_issuer_id"]]
    status, issuer_row, issuer_basis = _resolve_layer_row(
        issuer_rows, decision_date, LAYER_ISSUER, git_path=git_path, registry_path=registry_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={
            "source_alias": alias_basis, "listing": listing_basis,
            "instrument": instrument_basis, "issuer": issuer_basis})

    return _result(
        RESOLVED, decision_date,
        identity_basis={"source_alias": alias_basis, "listing": listing_basis,
                         "instrument": instrument_basis, "issuer": issuer_basis},
        canonical_issuer_id=instrument_row["canonical_issuer_id"],
        canonical_instrument_id=instrument_row["canonical_instrument_id"],
        listing_id=listing_row["listing_id"],
    )


# ---------------------------------------------------------------------------
# Market <-> account_scope resolver
# ---------------------------------------------------------------------------

def resolve_account_scope(market: str, decision_date: str, scope_authority: dict, registry_path=None) -> dict:
    """Resolve `market` -> `account_scope` via the (separate)
    market_account_scope authority document. Never joins market directly
    to account_scope by string equality -- see Packet 1 v2 section 4.
    Goes through the same `_resolve_layer_row` pipeline as every other
    layer."""
    _parse_temporal(decision_date)
    git_path = scope_authority.get("_source_path")
    rows = [r for r in scope_authority.get("edges", []) if r.get("market") == market]
    status, row, basis = _resolve_layer_row(rows, decision_date, LAYER_MARKET_ACCOUNT_SCOPE,
                                             git_path=git_path, registry_path=registry_path)
    if status is not None:
        # a missing/unratified market-account-scope edge is reported under
        # the SCOPE_MAP_MISSING vocabulary (Packet 1 v2 section 9), except
        # for statuses that are more specifically diagnostic (AMBIGUOUS,
        # TAMPERED_RECORD, etc.), which are more informative and kept as-is.
        if status in (NOT_COMPUTABLE_NO_AUTHORITY_RECORD, NOT_COMPUTABLE_UNRATIFIED_RECORD):
            status = NOT_COMPUTABLE_SCOPE_MAP_MISSING
        return _result(status, decision_date, identity_basis=basis)
    return _result(RESOLVED, decision_date, identity_basis=basis, account_scope=row["account_scope"])


# ---------------------------------------------------------------------------
# Layer-confusion guard + the REAL operational instrument-by-id resolver
# (defect 3 -- require_instrument_id no longer bypasses the authority gate)
# ---------------------------------------------------------------------------

def identify_layer_of_id(candidate_id: str, authority: dict) -> str | None:
    """Best-effort STRUCTURAL lookup only: which layer (if any) an opaque
    id string belongs to. This function alone never certifies anything
    resolvable -- see `resolve_instrument_by_id` for the real operational
    check."""
    if any(r.get("canonical_instrument_id") == candidate_id for r in authority.get("instruments", [])):
        return LAYER_INSTRUMENT
    if any(r.get("canonical_issuer_id") == candidate_id for r in authority.get("issuers", [])):
        return LAYER_ISSUER
    if any(r.get("listing_id") == candidate_id for r in authority.get("listings", [])):
        return LAYER_LISTING
    return None


def resolve_instrument_by_id(canonical_instrument_id: str, decision_date: str, authority: dict,
                              registry_path=None) -> dict:
    """The REAL operational resolver for a caller that already has a
    `canonical_instrument_id` and needs to confirm it is genuinely usable
    (RATIFIED + provenance + PIT), NOT merely that a row with that id
    exists. Only ever returns RESOLVED after the full gate passes -- a
    PROVISIONAL-only instrument row correctly returns
    IDENTITY_NOT_COMPUTABLE_UNRATIFIED_RECORD here, never RESOLVED."""
    _parse_temporal(decision_date)
    git_path = authority.get("_source_path")
    rows = [r for r in authority.get("instruments", []) if r.get("canonical_instrument_id") == canonical_instrument_id]
    status, row, basis = _resolve_layer_row(rows, decision_date, LAYER_INSTRUMENT,
                                             git_path=git_path, registry_path=registry_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"instrument": basis})
    return _result(RESOLVED, decision_date, identity_basis={"instrument": basis},
                    canonical_instrument_id=row["canonical_instrument_id"],
                    canonical_issuer_id=row["canonical_issuer_id"])


def require_instrument_id(candidate_id: str, authority: dict, decision_date: str, registry_path=None) -> dict:
    """Guard for any consumer (e.g. a future portfolio-join) that must use
    a `canonical_instrument_id` as its join key. If `candidate_id`
    structurally belongs to a DIFFERENT layer (issuer or listing), fails
    fast with `IDENTITY_NOT_COMPUTABLE_LAYER_MISMATCH` (a cheap, more
    specific diagnosis than a generic not-found). If it structurally
    belongs to the instrument layer, delegates to the real operational
    resolver `resolve_instrument_by_id` -- it does NOT return RESOLVED on
    structural existence alone (defect 3 fix)."""
    found_layer = identify_layer_of_id(candidate_id, authority)
    if found_layer == LAYER_INSTRUMENT:
        return resolve_instrument_by_id(candidate_id, decision_date, authority, registry_path=registry_path)
    if found_layer == LAYER_ISSUER:
        row = next(r for r in authority.get("issuers", []) if r.get("canonical_issuer_id") == candidate_id)
        return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date, identity_basis=_basis_from_row(row))
    if found_layer == LAYER_LISTING:
        row = next(r for r in authority.get("listings", []) if r.get("listing_id") == candidate_id)
        return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date, identity_basis=_basis_from_row(row))
    return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(None))


# ---------------------------------------------------------------------------
# Demonstration helper only -- NOT wired into any real portfolio code path.
# Shows why grouping by canonical_instrument_id (not listing_id or raw
# symbol) is required; see the dependent-defect note at the top of this
# module (portfolio_snapshot.py, task_8dcdbccb).
# ---------------------------------------------------------------------------

def group_positions_by_instrument(positions: list[dict], resolved_by_source_key: dict) -> dict:
    """`positions`: list of {"source_name", "source_asset_id", "market_value"}.
    `resolved_by_source_key`: {(source_name, source_asset_id): resolve_instrument_identity(...) result}
    pre-computed by the caller (this helper does no resolution itself).
    Returns {canonical_instrument_id: total_market_value}; positions whose
    resolution did not reach RESOLVED are grouped under None rather than
    silently dropped or silently merged into a guessed bucket."""
    out: dict = {}
    for p in positions:
        key = (p["source_name"], p["source_asset_id"])
        result = resolved_by_source_key.get(key)
        instrument_id = result["canonical_instrument_id"] if result and result["status"] == RESOLVED else None
        out[instrument_id] = out.get(instrument_id, 0.0) + p["market_value"]
    return out
