"""Canonical Security Identity resolver -- Identity Foundation stage.

Implements the 4-layer identity model (issuer / instrument / listing /
source_asset_id) and the row-level authority + anti-backdating PIT gate
designed in "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as this stage's
implementation baseline).

★ Rev 3 (CIO code review of HEAD 3bd9e0e, CHANGES_REQUIRED, 5 boundaries
  fixed in this pass; the rev-2 "provenance verified" claim is downgraded
  to PARTIALLY_VERIFIED -- see docs/identity_foundation_pr_notes.md):
   1. `real_usable_from` now also includes a real, independently git-
      verified first-seen time for the EVIDENCE FILE ITSELF (not just the
      row) -- `verify_evidence_first_seen_at` -- so a brand-new evidence
      file with a backdated `ratified_at` cannot make an old row look
      ratified since the past. `verify_approval_evidence` also cross-checks
      the evidence file's own claimed `ratified_at` against the row's.
      `real_usable_from = max(effective_from, ratified_at,
      verified_row_first_seen_at, verified_evidence_first_seen_at)`.
   2. The append-only registry escape hatch is REMOVED ENTIRELY from this
      module (it was, by construction, a backdating bypass API -- nothing
      stopped a caller from writing `record_first_seen(row, ..., at=<any
      past date>)`, and the registry file itself was just an editable
      JSONL with no independent verification of its own). There is no
      `registry_path` parameter anywhere in this module any more, and no
      `record_first_seen` function. Public authority is verified ONLY via
      full git-history exact-content checking in this PR. A real hash-
      chain / private append-only store is explicitly deferred, not part
      of this PR.
   3. Git-history verification now resolves the REAL repo-root-relative
      path of whatever file is being checked (`git rev-parse
      --show-toplevel` + `os.path.relpath`), not just the file's
      basename -- `git show {commit}:config/canonical_security_identity.json`,
      not `git show {commit}:canonical_security_identity.json`. This
      fixes real files nested under `config/`, not just root-level test
      fixtures.
   4. Every public resolver validates the AUTHORITY DOCUMENT itself
      (policy_version + required top-level arrays) at entry, via the
      same `validate_security_identity_document`/
      `validate_market_account_scope_document` functions `load_authority`/
      `load_scope_authority` use -- regardless of whether the document
      came from a file or was injected directly as a dict.
   5. `resolve_instrument_by_id` (and therefore `require_instrument_id`,
      which delegates to it) now also verifies the linked issuer through
      the exact same gate an instrument goes through inside
      `resolve_instrument_identity` -- an orphan, `PROVISIONAL`, or
      ambiguous issuer now correctly blocks resolution instead of being
      silently ignored.

★ Rev 2 fixes (CIO code review of HEAD c819a38) are retained: business-
  payload self-consistency vs. real external evidence-file verification
  are two distinct checks; every row (file-loaded or injected) is
  structurally validated before being considered; all temporal
  comparisons go through a strict parser (same-day mixed DATE_ONLY/
  full-timestamp precision is `IDENTITY_NOT_COMPUTABLE_TIME_PRECISION`,
  never guessed); every layer requires EXACTLY ONE active row.

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

_SECURITY_IDENTITY_ARRAYS = ("issuers", "instruments", "listings", "source_aliases")

# Fields every authority-record row must carry, regardless of layer.
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

# Every result this module emits echoes this block verbatim -- same
# "authority all false" pattern already established by
# clock/review_candidate.py's AUTHORITY_ALL_FALSE. This module resolves
# identity; it grants no operational authority.
AUTHORITY_ALL_FALSE = {
    "identity_resolution_authority": False,
    "market_scope_authority": False,
    "position_size_authority": False,
    "order_authority": False,
    "trading_authority": False,
}

# --- status vocabulary ---
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
NOT_COMPUTABLE_EVIDENCE_FIRST_SEEN_UNVERIFIED = "IDENTITY_NOT_COMPUTABLE_EVIDENCE_FIRST_SEEN_UNVERIFIED"
NOT_COMPUTABLE_TIME_PRECISION = "IDENTITY_NOT_COMPUTABLE_TIME_PRECISION"
RESOLVED = "RESOLVED"


class IdentityError(ValueError):
    pass


class TimePrecisionAmbiguous(IdentityError):
    """Two temporal values fall on the same UTC calendar day but cannot
    be safely ordered without assuming a within-day position (at least
    one is DATE_ONLY). Never silently resolved either direction."""


# ---------------------------------------------------------------------------
# Strict temporal parsing / comparison
# ---------------------------------------------------------------------------

_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_FULL_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

DATE_ONLY = "DATE_ONLY"
FULL_TIMESTAMP = "FULL_TIMESTAMP"


def _parse_temporal(value: str) -> tuple[datetime, str]:
    """Strict parser -- exactly two allowed shapes: `YYYY-MM-DD` (treated
    as 00:00:00 UTC of that day, precision DATE_ONLY) or
    `YYYY-MM-DDTHH:MM:SSZ` (precision FULL_TIMESTAMP, explicit UTC).
    Anything else raises IdentityError. No silent coercion."""
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
    value."""
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
    latest. Raises TimePrecisionAmbiguous on any ambiguous pairwise
    comparison. `None` entries are ignored."""
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
    fields = LAYER_BUSINESS_FIELDS[layer]
    return {k: row.get(k) for k in fields}


def verify_business_payload(row: dict, layer: str) -> bool:
    """Self-consistency check ONLY -- see module docstring for rev-2's
    explicit split between this and `verify_approval_evidence`."""
    expected = row.get("business_payload_sha256")
    if not expected:
        return False
    return payload_sha256(business_payload(row, layer)) == expected


def verify_approval_evidence(row: dict, layer: str, root: Path = ROOT) -> bool:
    """Independent provenance check: `approval_evidence_ref` must point
    at a REAL file; `approval_evidence_sha256` must match the sha256 of
    that file's REAL BYTES; and the file's own parsed content must
    independently assert the SAME `rule_id`/`rule_version`, an
    `approval_status` of `RATIFIED`, the SAME `ratified_at` the row
    claims (rev-3 addition -- closes the "backdated ratified_at on a
    brand-new evidence file" attack together with
    `verify_evidence_first_seen_at`), and the SAME
    `approved_business_payload_sha256` that was true of the row at the
    moment it was ratified. Any failure returns False; never raises."""
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
            and content.get("ratified_at") == row.get("ratified_at")
            and content.get("approved_business_payload_sha256") == row.get("business_payload_sha256"))


# ---------------------------------------------------------------------------
# Document-level validation (defect 4, rev 3) -- same function for
# file-loaded AND directly-injected documents.
# ---------------------------------------------------------------------------

def validate_security_identity_document(doc) -> None:
    if not isinstance(doc, dict):
        raise IdentityError("AUTHORITY_DOCUMENT_NOT_A_DICT")
    if doc.get("policy_version") not in SUPPORTED_SECURITY_IDENTITY_POLICY_VERSIONS:
        raise IdentityError(f"UNSUPPORTED_POLICY_VERSION:{doc.get('policy_version')!r}")
    for array_key in _SECURITY_IDENTITY_ARRAYS:
        if not isinstance(doc.get(array_key), list):
            raise IdentityError(f"AUTHORITY_DOCUMENT_MISSING_ARRAY:{array_key}")


def validate_market_account_scope_document(doc) -> None:
    if not isinstance(doc, dict):
        raise IdentityError("AUTHORITY_DOCUMENT_NOT_A_DICT")
    if doc.get("policy_version") not in SUPPORTED_MARKET_ACCOUNT_SCOPE_POLICY_VERSIONS:
        raise IdentityError(f"UNSUPPORTED_SCOPE_POLICY_VERSION:{doc.get('policy_version')!r}")
    if not isinstance(doc.get("edges"), list):
        raise IdentityError("AUTHORITY_DOCUMENT_MISSING_ARRAY:edges")


# ---------------------------------------------------------------------------
# Row-level structural validation
# ---------------------------------------------------------------------------

def validate_authority_row(row: dict, layer: str) -> None:
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
    try:
        return _compare_temporal(a, b) <= 0
    except TimePrecisionAmbiguous:
        return True


# ---------------------------------------------------------------------------
# PIT / anti-backdating gate (rev 3: 4-way max, row AND evidence first-seen)
# ---------------------------------------------------------------------------

def real_usable_from(row: dict, verified_row_first_seen_at: str, verified_evidence_first_seen_at: str) -> str:
    """`max(effective_from, ratified_at, verified_row_first_seen_at,
    verified_evidence_first_seen_at)`. `ratified_at` is only ever reached
    here after `verify_approval_evidence` has already confirmed it
    matches the evidence file's own independent claim -- by the time this
    runs it is a verified value, not a bare self-declaration. Takes BOTH
    independently-verified first-seen times (row content AND evidence
    file content) so that a brand-new evidence file with a backdated
    `ratified_at` cannot make an old row look ratified since the past --
    the evidence file's own real git first-seen time is what blocks that."""
    return _max_temporal(row.get("effective_from"), row.get("ratified_at"),
                          verified_row_first_seen_at, verified_evidence_first_seen_at)


def _row_active(row: dict, as_of: str) -> bool:
    start = row["effective_from"]
    end = row.get("effective_to")
    if _compare_temporal(start, as_of) > 0:
        return False
    if end is not None and _compare_temporal(as_of, end) >= 0:
        return False
    return True


def _intervals_overlap(a_start: str, a_end, b_start: str, b_end) -> bool:
    left_ok = a_end is None or _compare_temporal(b_start, a_end) < 0
    right_ok = b_end is None or _compare_temporal(a_start, b_end) < 0
    return left_ok and right_ok


def detect_overlapping_intervals(rows: list[dict], key_fields: tuple) -> list[tuple[dict, dict]]:
    """Standalone diagnostic utility -- the resolvers require exactly one
    ACTIVE row per key directly and no longer depend on this for
    ambiguity detection, but it remains useful for auditing a whole
    authority file at once."""
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
                pairs.append((a, b))
    return pairs


# ---------------------------------------------------------------------------
# Git-history verification (rev 3: real repo-root-relative paths, no
# registry fallback at all -- git history is the ONLY source of truth)
# ---------------------------------------------------------------------------

def _parse_git_committer_iso(value: str) -> datetime:
    """`git log --format=%cI` (strict ISO 8601) renders a UTC offset as a
    trailing 'Z' -- `datetime.fromisoformat` only accepts that suffix
    from Python 3.11 onward, so normalize it to '+00:00' first for
    compatibility with this repo's supported Python versions."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


def _git_repo_root(path: Path) -> Path | None:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=path.parent, capture_output=True, text=True, check=True,
        ).stdout.strip()
        return Path(out).resolve()
    except Exception:
        return None


def _git_history_commits(path: Path) -> list[tuple[str, str, str]]:
    """Returns [(commit_hash, committer_iso, repo_relative_posix_path), ...]
    oldest-first, using `--follow` on the REAL repo-root-relative path
    (rev 3 fix -- previously only the basename was used, which breaks for
    any real file nested under a directory like `config/`)."""
    repo_root = _git_repo_root(path)
    if repo_root is None:
        return []
    try:
        rel = path.resolve().relative_to(repo_root)
    except ValueError:
        return []
    rel_posix = rel.as_posix()
    try:
        log = subprocess.run(
            ["git", "log", "--follow", "--format=%H|%cI", "--", rel_posix],
            cwd=repo_root, capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return []
    if not log:
        return []
    commits = []
    for line in log.splitlines():
        if "|" not in line:
            continue
        h, iso = line.split("|", 1)
        commits.append((h, iso, rel_posix))
    return list(reversed(commits))  # oldest first


def _git_show_bytes(repo_root: Path, commit_hash: str, rel_posix_path: str) -> bytes | None:
    try:
        r = subprocess.run(
            ["git", "show", f"{commit_hash}:{rel_posix_path}"],
            cwd=repo_root, capture_output=True, check=True,
        )
        return r.stdout
    except Exception:
        return None


def _git_first_commit_time_for_json_match(path: Path, matches) -> str | None:
    """Walks the REAL git history of `path` (real repo-root-relative path,
    oldest first) and returns the ISO-8601 UTC committer time of the
    first commit whose JSON content at that revision satisfies
    `matches(parsed_doc)`. None if never found (including a file with no
    git history at all -- correctly unverified, never guessed)."""
    repo_root = _git_repo_root(path)
    if repo_root is None:
        return None
    for commit_hash, committer_iso, rel_posix in _git_history_commits(path):
        raw = _git_show_bytes(repo_root, commit_hash, rel_posix)
        if raw is None:
            continue
        try:
            doc = json.loads(raw.decode("utf-8"))
        except Exception:
            continue
        if matches(doc):
            dt = _parse_git_committer_iso(committer_iso)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _git_first_commit_time_for_exact_bytes(path: Path, expected_sha256: str) -> str | None:
    """Same real git-history walk, but matches on the EXACT byte content
    hash of the file at each revision -- used for evidence files, which
    are a single opaque blob rather than a JSON array of rows."""
    repo_root = _git_repo_root(path)
    if repo_root is None:
        return None
    for commit_hash, committer_iso, rel_posix in _git_history_commits(path):
        raw = _git_show_bytes(repo_root, commit_hash, rel_posix)
        if raw is None:
            continue
        if hashlib.sha256(raw).hexdigest() == expected_sha256:
            dt = _parse_git_committer_iso(committer_iso)
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


def verify_row_first_seen_at(row: dict, layer: str, git_path) -> str | None:
    """Derives the row's `verified_row_first_seen_at` from REAL git
    commit history of the authority document at `git_path`. Returns None
    -- unverified, never a guessed date -- if `git_path` is not given, the
    file has no git history, or this exact row content never appears in
    it."""
    if git_path is None:
        return None
    return _git_first_commit_time_for_json_match(Path(git_path), _row_matcher(layer, row))


def verify_evidence_first_seen_at(row: dict, root: Path = ROOT) -> str | None:
    """Derives the evidence file's OWN `verified_evidence_first_seen_at`
    from real git commit history of the file `row['approval_evidence_ref']`
    points at (rev 3: independent of the row's own first-seen -- see
    module docstring item 1). Returns None if unverifiable."""
    ref = row.get("approval_evidence_ref")
    expected_hash = row.get("approval_evidence_sha256")
    if not ref or not expected_hash:
        return None
    path = Path(ref)
    if not path.is_absolute():
        path = root / path
    return _git_first_commit_time_for_exact_bytes(path, expected_hash)


# ---------------------------------------------------------------------------
# Authority document loading
# ---------------------------------------------------------------------------

def load_authority(path=SECURITY_IDENTITY_PATH) -> dict:
    path = Path(path)
    if not path.is_file():
        raise IdentityError("AUTHORITY_FILE_NOT_FOUND")
    doc = json.loads(path.read_text(encoding="utf-8"))
    validate_security_identity_document(doc)
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
    validate_market_account_scope_document(doc)
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
    base = {"rule_id": None, "rule_version": None, "approval_status": None,
            "ratified_at": None, "first_seen_at_claimed": None,
            "verified_row_first_seen_at": None, "verified_evidence_first_seen_at": None,
            "business_payload_verified": None, "approval_evidence_verified": None}
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
# The single shared authority-gate pipeline -- every layer (issuer /
# instrument / listing / source_alias / market_account_scope) goes
# through exactly this function. No layer gets a shortcut.
# ---------------------------------------------------------------------------

def _resolve_layer_row(candidate_rows: list[dict], decision_date: str, layer: str, git_path=None):
    """Returns (status_or_None, row_or_None, basis_dict). status_or_None
    is None ONLY on full success (RESOLVED)."""
    if not candidate_rows:
        return NOT_COMPUTABLE_NO_AUTHORITY_RECORD, None, _basis_from_row(None)

    for row in candidate_rows:
        validate_authority_row(row, layer)  # hard failure on malformed data

    try:
        active = [r for r in candidate_rows if _row_active(r, decision_date)]
    except TimePrecisionAmbiguous:
        return NOT_COMPUTABLE_TIME_PRECISION, candidate_rows[0], _basis_from_row(candidate_rows[0])

    if len(active) > 1:
        return NOT_COMPUTABLE_AMBIGUOUS, active[0], {"candidates": [_basis_from_row(r) for r in active]}
    if len(active) == 0:
        return NOT_COMPUTABLE_NO_AUTHORITY_RECORD, candidate_rows[0], _basis_from_row(candidate_rows[0])

    row = active[0]
    if row["approval_status"] != "RATIFIED":
        return NOT_COMPUTABLE_UNRATIFIED_RECORD, row, _basis_from_row(row)

    if not verify_business_payload(row, layer):
        return NOT_COMPUTABLE_TAMPERED_RECORD, row, _basis_from_row(row, {"business_payload_verified": False})

    if not verify_approval_evidence(row, layer):
        return NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED, row, _basis_from_row(
            row, {"business_payload_verified": True, "approval_evidence_verified": False})

    verified_row_first_seen = verify_row_first_seen_at(row, layer, git_path)
    if verified_row_first_seen is None:
        return NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED, row, _basis_from_row(
            row, {"business_payload_verified": True, "approval_evidence_verified": True})

    verified_evidence_first_seen = verify_evidence_first_seen_at(row)
    if verified_evidence_first_seen is None:
        return NOT_COMPUTABLE_EVIDENCE_FIRST_SEEN_UNVERIFIED, row, _basis_from_row(
            row, {"business_payload_verified": True, "approval_evidence_verified": True,
                  "verified_row_first_seen_at": verified_row_first_seen})

    diagnostics = {"business_payload_verified": True, "approval_evidence_verified": True,
                    "verified_row_first_seen_at": verified_row_first_seen,
                    "verified_evidence_first_seen_at": verified_evidence_first_seen}
    try:
        usable_from = real_usable_from(row, verified_row_first_seen, verified_evidence_first_seen)
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
                                 decision_date: str, authority: dict) -> dict:
    """`authority` is validated as a whole DOCUMENT (defect 4) before any
    row is touched, regardless of whether it came from `load_authority`
    or was injected directly."""
    _parse_temporal(decision_date)
    validate_security_identity_document(authority)
    git_path = authority.get("_source_path")

    alias_rows = [r for r in authority.get("source_aliases", [])
                  if r.get("source_name") == source_name and r.get("source_asset_id") == source_asset_id]
    status, alias_row, alias_basis = _resolve_layer_row(alias_rows, decision_date, LAYER_SOURCE_ALIAS, git_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"source_alias": alias_basis})

    listing_rows = [r for r in authority.get("listings", []) if r.get("listing_id") == alias_row["listing_id"]]
    status, listing_row, listing_basis = _resolve_layer_row(listing_rows, decision_date, LAYER_LISTING, git_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"source_alias": alias_basis, "listing": listing_basis})
    if listing_row["market"] != market:
        return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date,
                        identity_basis={"source_alias": alias_basis, "listing": listing_basis})

    instrument_rows = [r for r in authority.get("instruments", [])
                        if r.get("canonical_instrument_id") == listing_row["canonical_instrument_id"]]
    status, instrument_row, instrument_basis = _resolve_layer_row(instrument_rows, decision_date, LAYER_INSTRUMENT, git_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={
            "source_alias": alias_basis, "listing": listing_basis, "instrument": instrument_basis})

    issuer_rows = [r for r in authority.get("issuers", [])
                   if r.get("canonical_issuer_id") == instrument_row["canonical_issuer_id"]]
    status, issuer_row, issuer_basis = _resolve_layer_row(issuer_rows, decision_date, LAYER_ISSUER, git_path)
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

def resolve_account_scope(market: str, decision_date: str, scope_authority: dict) -> dict:
    _parse_temporal(decision_date)
    validate_market_account_scope_document(scope_authority)
    git_path = scope_authority.get("_source_path")
    rows = [r for r in scope_authority.get("edges", []) if r.get("market") == market]
    status, row, basis = _resolve_layer_row(rows, decision_date, LAYER_MARKET_ACCOUNT_SCOPE, git_path)
    if status is not None:
        if status in (NOT_COMPUTABLE_NO_AUTHORITY_RECORD, NOT_COMPUTABLE_UNRATIFIED_RECORD):
            status = NOT_COMPUTABLE_SCOPE_MAP_MISSING
        return _result(status, decision_date, identity_basis=basis)
    return _result(RESOLVED, decision_date, identity_basis=basis, account_scope=row["account_scope"])


# ---------------------------------------------------------------------------
# Layer-confusion guard + the REAL operational instrument-by-id resolver
# ---------------------------------------------------------------------------

def identify_layer_of_id(candidate_id: str, authority: dict) -> str | None:
    """Best-effort STRUCTURAL lookup only. Never certifies anything
    resolvable -- see `resolve_instrument_by_id` for the real operational
    check."""
    if any(r.get("canonical_instrument_id") == candidate_id for r in authority.get("instruments", [])):
        return LAYER_INSTRUMENT
    if any(r.get("canonical_issuer_id") == candidate_id for r in authority.get("issuers", [])):
        return LAYER_ISSUER
    if any(r.get("listing_id") == candidate_id for r in authority.get("listings", [])):
        return LAYER_LISTING
    return None


def resolve_instrument_by_id(canonical_instrument_id: str, decision_date: str, authority: dict) -> dict:
    """The REAL operational resolver for a caller that already has a
    `canonical_instrument_id`. Rev 3: also verifies the LINKED ISSUER
    through the exact same gate `resolve_instrument_identity` uses --
    previously this returned RESOLVED even with an orphan, PROVISIONAL,
    or ambiguous issuer, which was inconsistent with the full source
    resolver's judgment."""
    _parse_temporal(decision_date)
    validate_security_identity_document(authority)
    git_path = authority.get("_source_path")

    instrument_rows = [r for r in authority.get("instruments", []) if r.get("canonical_instrument_id") == canonical_instrument_id]
    status, instrument_row, instrument_basis = _resolve_layer_row(instrument_rows, decision_date, LAYER_INSTRUMENT, git_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"instrument": instrument_basis})

    issuer_rows = [r for r in authority.get("issuers", []) if r.get("canonical_issuer_id") == instrument_row["canonical_issuer_id"]]
    status, issuer_row, issuer_basis = _resolve_layer_row(issuer_rows, decision_date, LAYER_ISSUER, git_path)
    if status is not None:
        return _result(status, decision_date, identity_basis={"instrument": instrument_basis, "issuer": issuer_basis})

    return _result(RESOLVED, decision_date, identity_basis={"instrument": instrument_basis, "issuer": issuer_basis},
                    canonical_instrument_id=instrument_row["canonical_instrument_id"],
                    canonical_issuer_id=instrument_row["canonical_issuer_id"])


def require_instrument_id(candidate_id: str, authority: dict, decision_date: str) -> dict:
    """Guard for any consumer that must use a `canonical_instrument_id` as
    its join key. Wrong-layer ids fail fast with
    `IDENTITY_NOT_COMPUTABLE_LAYER_MISMATCH`; instrument-layer ids
    delegate to the real operational resolver `resolve_instrument_by_id`
    (never a structural-existence-only shortcut)."""
    validate_security_identity_document(authority)
    found_layer = identify_layer_of_id(candidate_id, authority)
    if found_layer == LAYER_INSTRUMENT:
        return resolve_instrument_by_id(candidate_id, decision_date, authority)
    if found_layer == LAYER_ISSUER:
        row = next(r for r in authority.get("issuers", []) if r.get("canonical_issuer_id") == candidate_id)
        return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date, identity_basis=_basis_from_row(row))
    if found_layer == LAYER_LISTING:
        row = next(r for r in authority.get("listings", []) if r.get("listing_id") == candidate_id)
        return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date, identity_basis=_basis_from_row(row))
    return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(None))


# ---------------------------------------------------------------------------
# Demonstration helper only -- NOT wired into any real portfolio code path.
# ---------------------------------------------------------------------------

def group_positions_by_instrument(positions: list[dict], resolved_by_source_key: dict) -> dict:
    out: dict = {}
    for p in positions:
        key = (p["source_name"], p["source_asset_id"])
        result = resolved_by_source_key.get(key)
        instrument_id = result["canonical_instrument_id"] if result and result["status"] == RESOLVED else None
        out[instrument_id] = out.get(instrument_id, 0.0) + p["market_value"]
    return out
