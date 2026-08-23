"""Canonical Security Identity resolver -- Identity Foundation stage.

Implements the 4-layer identity model (issuer / instrument / listing /
source_asset_id) and the row-level authority + anti-backdating PIT gate
designed in "Canonical Security Identity / Market Scope Authority" v2
(Notion design packet, CIO-approved 2026-08-24 as the implementation
baseline for this stage).

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

★ Resolution priority order (highest first) -- see `resolve_instrument_identity`
  and `resolve_account_scope` docstrings for the exact sequence.

★ `portfolio_risk/portfolio_snapshot.py`'s `by_ticker` aggregation still
  groups by raw provider symbol, not `canonical_instrument_id` -- a real,
  already-found defect (BTC's XBT/XXBT alias pair could double-count).
  That defect is NOT fixed by this module and is NOT patched here with a
  symbol-normalization workaround; it is tracked as a dependent defect
  (background task `task_8dcdbccb`, currently being worked in a separate
  session) that can only be safely resolved once canonical-instrument
  adoption actually lands in that file. `group_positions_by_instrument`
  below exists only to DEMONSTRATE, in tests, why instrument-level
  grouping is the correct fix -- it is not wired into any real portfolio
  code path by this PR.
"""
from __future__ import annotations

import hashlib
import json
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

# Fields every authority-record row must carry, regardless of layer
# (Packet 1 v2 section 7 -- row-level authority contract).
AUTHORITY_FIELDS = (
    "rule_id", "rule_version", "approval_status", "ratified_at",
    "approval_evidence_ref", "approval_evidence_sha256", "first_seen_at",
    "effective_from", "effective_to",
)

# Business (identity-bearing) fields per layer -- used both for structural
# validation and for exact-content provenance hashing (the authority
# fields themselves are excluded from the hashed payload, since a field
# cannot certify its own value).
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
RESOLVED = "RESOLVED"


class IdentityError(ValueError):
    pass


# ---------------------------------------------------------------------------
# Canonical hashing / exact-content provenance
# ---------------------------------------------------------------------------

def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def business_payload(row: dict, layer: str) -> dict:
    """The identity-bearing subset of a row -- excludes every authority
    field (rule_id/approval_status/ratified_at/.../approval_evidence_sha256
    itself) so the hash can certify the row's content without certifying
    its own certification."""
    fields = LAYER_BUSINESS_FIELDS[layer]
    return {k: row.get(k) for k in fields}


def verify_row_provenance(row: dict, layer: str) -> bool:
    """Exact-content provenance check: `approval_evidence_sha256` must
    equal sha256(canonical_json(business_payload(row, layer))). A row
    with no `approval_evidence_sha256` at all is PROVISIONAL by
    construction and is never treated as tampered (there is nothing to
    verify against) -- callers only invoke this for rows attempting
    RATIFIED use. Returns True/False; never raises for a mismatch."""
    expected = row.get("approval_evidence_sha256")
    if not expected:
        return False
    actual = payload_sha256(business_payload(row, layer))
    return actual == expected


# ---------------------------------------------------------------------------
# Row-level structural validation
# ---------------------------------------------------------------------------

def validate_authority_row(row: dict, layer: str) -> None:
    """Structural validation only. Raises IdentityError on malformed rows.
    Never asserts truthfulness of ratification -- that is decided by the
    resolvers below, which additionally consult `approval_status` and
    `real_usable_from`."""
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
    if row["approval_status"] == "RATIFIED" and not row.get("ratified_at"):
        raise IdentityError("RATIFIED_ROW_MISSING_RATIFIED_AT")
    if not row.get("effective_from"):
        raise IdentityError("EFFECTIVE_FROM_REQUIRED")
    if not row.get("first_seen_at"):
        raise IdentityError("FIRST_SEEN_AT_REQUIRED")
    eff_to = row.get("effective_to")
    if eff_to is not None and eff_to <= row["effective_from"]:
        raise IdentityError("EFFECTIVE_INTERVAL_EMPTY_OR_INVERTED")
    if layer == LAYER_INSTRUMENT and row.get("instrument_type") not in INSTRUMENT_TYPES:
        raise IdentityError(f"INSTRUMENT_TYPE_INVALID:{row.get('instrument_type')!r}")


# ---------------------------------------------------------------------------
# PIT / anti-backdating gate
# ---------------------------------------------------------------------------

def real_usable_from(row: dict) -> str:
    """`max(effective_from, ratified_at, first_seen_at)` -- the anti-
    backdating rule (CIO-mandated, Packet 1 v2 section 7). A PROVISIONAL
    row has no `ratified_at`; callers only consult this function for rows
    already confirmed RATIFIED (see resolvers), so all three inputs are
    expected to be present at that point. If none are present this raises
    rather than silently returning None -- there is no safe default."""
    dates = [d for d in (row.get("effective_from"), row.get("ratified_at"), row.get("first_seen_at")) if d]
    if not dates:
        raise IdentityError("REAL_USABLE_FROM_INPUTS_MISSING")
    return max(dates)


def _row_active(row: dict, as_of: str) -> bool:
    """Plain interval membership using the row's OWN asserted
    effective_from/effective_to -- deliberately NOT real_usable_from.
    Used for ambiguity detection, which is a data-integrity question
    (do two rows both claim to cover this date?) independent of whether
    either row is actually usable yet."""
    start = row["effective_from"]
    end = row.get("effective_to")
    return start <= as_of and (end is None or as_of < end)


def _intervals_overlap(a_start: str, a_end, b_start: str, b_end) -> bool:
    """Independently re-derived half-open-interval overlap check --
    same semantics as `portfolio/market_theme_exposure_budget.py`'s
    `_overlap()`, reimplemented locally (not imported) to keep this
    package free of a dependency on `portfolio/`."""
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def detect_overlapping_intervals(rows: list[dict], key_fields: tuple) -> list[tuple[dict, dict]]:
    """Returns every pair of rows that share the same `key_fields` values
    and whose [effective_from, effective_to) intervals overlap. Used both
    as a standalone integrity check and internally by the resolvers'
    ambiguity detection."""
    pairs = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if tuple(a.get(k) for k in key_fields) != tuple(b.get(k) for k in key_fields):
                continue
            if _intervals_overlap(a["effective_from"], a.get("effective_to"),
                                   b["effective_from"], b.get("effective_to")):
                pairs.append((a, b))
    return pairs


# ---------------------------------------------------------------------------
# Authority document loading
# ---------------------------------------------------------------------------

def load_authority(path=SECURITY_IDENTITY_PATH) -> dict:
    if not Path(path).is_file():
        raise IdentityError("AUTHORITY_FILE_NOT_FOUND")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("policy_version") not in SUPPORTED_SECURITY_IDENTITY_POLICY_VERSIONS:
        raise IdentityError(f"UNSUPPORTED_POLICY_VERSION:{doc.get('policy_version')!r}")
    return doc


def load_scope_authority(path=MARKET_ACCOUNT_SCOPE_PATH) -> dict:
    if not Path(path).is_file():
        raise IdentityError("SCOPE_AUTHORITY_FILE_NOT_FOUND")
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    if doc.get("policy_version") not in SUPPORTED_MARKET_ACCOUNT_SCOPE_POLICY_VERSIONS:
        raise IdentityError(f"UNSUPPORTED_SCOPE_POLICY_VERSION:{doc.get('policy_version')!r}")
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


def _basis_from_row(row: dict | None) -> dict:
    """Every result echoes exactly which rule/version/approval basis was
    used (or, on failure, the closest candidate row inspected) -- never a
    bare status code with no traceable source."""
    if row is None:
        return {"rule_id": None, "rule_version": None, "approval_status": None,
                "ratified_at": None, "first_seen_at": None}
    return {
        "rule_id": row.get("rule_id"),
        "rule_version": row.get("rule_version"),
        "approval_status": row.get("approval_status"),
        "ratified_at": row.get("ratified_at"),
        "first_seen_at": row.get("first_seen_at"),
    }


# ---------------------------------------------------------------------------
# Core resolver: source_asset_id -> listing -> instrument -> issuer
# ---------------------------------------------------------------------------

def _matching_source_alias_rows(authority: dict, source_name: str, source_asset_id: str) -> list[dict]:
    return [r for r in authority.get("source_aliases", [])
            if r.get("source_name") == source_name and r.get("source_asset_id") == source_asset_id]


def resolve_instrument_identity(source_name: str, source_asset_id: str, market: str,
                                 decision_date: str, authority: dict) -> dict:
    """Resolve (source_name, source_asset_id) -> canonical_instrument_id
    (and its canonical_issuer_id / listing_id) as of `decision_date`,
    constrained to `market`.

    Priority order (highest first) -- mirrors Packet 1 v2 section 9:
      1. No structurally matching source_alias row at all
         -> NO_AUTHORITY_RECORD
      2. A matching row fails exact-content provenance verification
         (only checked for rows otherwise eligible to resolve, i.e. those
         claiming RATIFIED) -> TAMPERED_RECORD
      3. More than one distinct row (different listing_id) both claim
         (via their own effective_from/effective_to, regardless of
         approval_status) to cover decision_date -> AMBIGUOUS
      4. None of the matching rows is RATIFIED -> UNRATIFIED_RECORD
      5. The RATIFIED row's real_usable_from > decision_date, or
         decision_date is outside [real_usable_from, effective_to)
         -> PIT_VIOLATION
      6. Chain breaks at listing->instrument or instrument->issuer for the
         same reasons, checked in the same order, layer by layer.
      7. Otherwise: resolved.

    `authority` is the loaded authority document (dependency injection --
    same reuse pattern as `replay/asset_identity.py` and the pinned
    `clock/review_candidate.py` validator: callers pass data in, this
    function never reaches for a single hardcoded global).
    """
    alias_rows = _matching_source_alias_rows(authority, source_name, source_asset_id)
    if not alias_rows:
        return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(None))

    active_alias_rows = [r for r in alias_rows if _row_active(r, decision_date)]
    if len(active_alias_rows) > 1 and len({r.get("listing_id") for r in active_alias_rows}) > 1:
        return _result(NOT_COMPUTABLE_AMBIGUOUS, decision_date,
                        identity_basis={"candidates": [_basis_from_row(r) for r in active_alias_rows]})

    ratified_alias_rows = [r for r in active_alias_rows if r.get("approval_status") == "RATIFIED"]
    if not ratified_alias_rows:
        closest = active_alias_rows[0] if active_alias_rows else alias_rows[0]
        return _result(NOT_COMPUTABLE_UNRATIFIED_RECORD, decision_date, identity_basis=_basis_from_row(closest))

    alias_row = ratified_alias_rows[0]
    if not verify_row_provenance(alias_row, LAYER_SOURCE_ALIAS):
        return _result(NOT_COMPUTABLE_TAMPERED_RECORD, decision_date, identity_basis=_basis_from_row(alias_row))

    usable_from = real_usable_from(alias_row)
    eff_to = alias_row.get("effective_to")
    if usable_from > decision_date or (eff_to is not None and decision_date >= eff_to):
        return _result(NOT_COMPUTABLE_PIT_VIOLATION, decision_date, identity_basis=_basis_from_row(alias_row))

    listing_id = alias_row["listing_id"]
    listing_result = _resolve_listing(listing_id, market, decision_date, authority)
    if listing_result["status"] != RESOLVED:
        return _result(listing_result["status"], decision_date, identity_basis=listing_result["identity_basis"])

    instrument_row = listing_result["_instrument_row"]
    listing_row = listing_result["_listing_row"]

    issuer_result = _resolve_issuer(instrument_row["canonical_issuer_id"], decision_date, authority)
    if issuer_result["status"] != RESOLVED:
        return _result(issuer_result["status"], decision_date, identity_basis=issuer_result["identity_basis"])

    return _result(
        RESOLVED, decision_date,
        identity_basis={
            "source_alias": _basis_from_row(alias_row),
            "listing": _basis_from_row(listing_row),
            "instrument": _basis_from_row(instrument_row),
            "issuer": _basis_from_row(issuer_result["_issuer_row"]),
        },
        canonical_issuer_id=instrument_row["canonical_issuer_id"],
        canonical_instrument_id=instrument_row["canonical_instrument_id"],
        listing_id=listing_row["listing_id"],
    )


def _resolve_listing(listing_id: str, market: str, decision_date: str, authority: dict) -> dict:
    rows = [r for r in authority.get("listings", []) if r.get("listing_id") == listing_id]
    if not rows:
        return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(None))
    mismatched_market = [r for r in rows if r.get("market") != market]
    rows = [r for r in rows if r.get("market") == market]
    if not rows:
        if mismatched_market:
            return _result(NOT_COMPUTABLE_LAYER_MISMATCH, decision_date,
                            identity_basis=_basis_from_row(mismatched_market[0]))
        return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(None))
    active = [r for r in rows if _row_active(r, decision_date)]
    ratified = [r for r in active if r.get("approval_status") == "RATIFIED"]
    if len(active) > 1 and len({r.get("canonical_instrument_id") for r in active}) > 1:
        return _result(NOT_COMPUTABLE_AMBIGUOUS, decision_date,
                        identity_basis={"candidates": [_basis_from_row(r) for r in active]})
    if not ratified:
        closest = active[0] if active else rows[0]
        return _result(NOT_COMPUTABLE_UNRATIFIED_RECORD, decision_date, identity_basis=_basis_from_row(closest))
    listing_row = ratified[0]
    if not verify_row_provenance(listing_row, LAYER_LISTING):
        return _result(NOT_COMPUTABLE_TAMPERED_RECORD, decision_date, identity_basis=_basis_from_row(listing_row))
    usable_from = real_usable_from(listing_row)
    eff_to = listing_row.get("effective_to")
    if usable_from > decision_date or (eff_to is not None and decision_date >= eff_to):
        return _result(NOT_COMPUTABLE_PIT_VIOLATION, decision_date, identity_basis=_basis_from_row(listing_row))

    instrument_rows = [r for r in authority.get("instruments", [])
                        if r.get("canonical_instrument_id") == listing_row["canonical_instrument_id"]]
    if not instrument_rows:
        return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(listing_row))
    active_instr = [r for r in instrument_rows if _row_active(r, decision_date)]
    ratified_instr = [r for r in active_instr if r.get("approval_status") == "RATIFIED"]
    if not ratified_instr:
        closest = active_instr[0] if active_instr else instrument_rows[0]
        return _result(NOT_COMPUTABLE_UNRATIFIED_RECORD, decision_date, identity_basis=_basis_from_row(closest))
    instrument_row = ratified_instr[0]
    if not verify_row_provenance(instrument_row, LAYER_INSTRUMENT):
        return _result(NOT_COMPUTABLE_TAMPERED_RECORD, decision_date, identity_basis=_basis_from_row(instrument_row))
    usable_from = real_usable_from(instrument_row)
    eff_to_i = instrument_row.get("effective_to")
    if usable_from > decision_date or (eff_to_i is not None and decision_date >= eff_to_i):
        return _result(NOT_COMPUTABLE_PIT_VIOLATION, decision_date, identity_basis=_basis_from_row(instrument_row))

    out = _result(RESOLVED, decision_date, identity_basis=_basis_from_row(listing_row))
    out["_listing_row"] = listing_row
    out["_instrument_row"] = instrument_row
    return out


def _resolve_issuer(canonical_issuer_id: str, decision_date: str, authority: dict) -> dict:
    rows = [r for r in authority.get("issuers", []) if r.get("canonical_issuer_id") == canonical_issuer_id]
    if not rows:
        return _result(NOT_COMPUTABLE_NO_AUTHORITY_RECORD, decision_date, identity_basis=_basis_from_row(None))
    active = [r for r in rows if _row_active(r, decision_date)]
    ratified = [r for r in active if r.get("approval_status") == "RATIFIED"]
    if not ratified:
        closest = active[0] if active else rows[0]
        return _result(NOT_COMPUTABLE_UNRATIFIED_RECORD, decision_date, identity_basis=_basis_from_row(closest))
    issuer_row = ratified[0]
    if not verify_row_provenance(issuer_row, LAYER_ISSUER):
        return _result(NOT_COMPUTABLE_TAMPERED_RECORD, decision_date, identity_basis=_basis_from_row(issuer_row))
    usable_from = real_usable_from(issuer_row)
    eff_to = issuer_row.get("effective_to")
    if usable_from > decision_date or (eff_to is not None and decision_date >= eff_to):
        return _result(NOT_COMPUTABLE_PIT_VIOLATION, decision_date, identity_basis=_basis_from_row(issuer_row))
    out = _result(RESOLVED, decision_date, identity_basis=_basis_from_row(issuer_row))
    out["_issuer_row"] = issuer_row
    return out


# ---------------------------------------------------------------------------
# Market <-> account_scope resolver
# ---------------------------------------------------------------------------

def resolve_account_scope(market: str, decision_date: str, scope_authority: dict) -> dict:
    """Resolve `market` -> `account_scope` via the (separate)
    market_account_scope authority document. Never joins market directly
    to account_scope by string equality -- see Packet 1 v2 section 4."""
    rows = [r for r in scope_authority.get("edges", []) if r.get("market") == market]
    if not rows:
        return _result(NOT_COMPUTABLE_SCOPE_MAP_MISSING, decision_date, identity_basis=_basis_from_row(None))
    active = [r for r in rows if _row_active(r, decision_date)]
    ratified = [r for r in active if r.get("approval_status") == "RATIFIED"]
    if len(active) > 1 and len({r.get("account_scope") for r in active}) > 1:
        return _result(NOT_COMPUTABLE_AMBIGUOUS, decision_date,
                        identity_basis={"candidates": [_basis_from_row(r) for r in active]})
    if not ratified:
        closest = active[0] if active else rows[0]
        return _result(NOT_COMPUTABLE_SCOPE_MAP_MISSING, decision_date, identity_basis=_basis_from_row(closest))
    edge_row = ratified[0]
    if not verify_row_provenance(edge_row, LAYER_MARKET_ACCOUNT_SCOPE):
        return _result(NOT_COMPUTABLE_TAMPERED_RECORD, decision_date, identity_basis=_basis_from_row(edge_row))
    usable_from = real_usable_from(edge_row)
    eff_to = edge_row.get("effective_to")
    if usable_from > decision_date or (eff_to is not None and decision_date >= eff_to):
        return _result(NOT_COMPUTABLE_PIT_VIOLATION, decision_date, identity_basis=_basis_from_row(edge_row))
    return _result(RESOLVED, decision_date, identity_basis=_basis_from_row(edge_row), account_scope=edge_row["account_scope"])


# ---------------------------------------------------------------------------
# Layer-confusion guard -- catches a caller passing an id from the wrong
# layer (e.g. a canonical_issuer_id where a canonical_instrument_id, or a
# listing_id, is required) instead of silently mis-resolving or returning
# a generic "not found".
# ---------------------------------------------------------------------------

def identify_layer_of_id(candidate_id: str, authority: dict) -> str | None:
    """Best-effort structural lookup: which layer (if any) an opaque id
    string belongs to in the authority document. Checked in
    instrument -> issuer -> listing order since instrument is the layer
    every real caller is expected to use; a match in any layer other than
    the one requested is a layer-confusion finding, not a not-found."""
    if any(r.get("canonical_instrument_id") == candidate_id for r in authority.get("instruments", [])):
        return LAYER_INSTRUMENT
    if any(r.get("canonical_issuer_id") == candidate_id for r in authority.get("issuers", [])):
        return LAYER_ISSUER
    if any(r.get("listing_id") == candidate_id for r in authority.get("listings", [])):
        return LAYER_LISTING
    return None


def require_instrument_id(candidate_id: str, authority: dict, decision_date: str) -> dict:
    """Guard for any consumer (e.g. a future portfolio-join) that must use
    a `canonical_instrument_id` as its join key. If `candidate_id`
    structurally belongs to a DIFFERENT layer (issuer or listing),
    returns `IDENTITY_NOT_COMPUTABLE_LAYER_MISMATCH` instead of silently
    proceeding with the wrong granularity or returning an undifferentiated
    not-found."""
    found_layer = identify_layer_of_id(candidate_id, authority)
    if found_layer == LAYER_INSTRUMENT:
        row = next(r for r in authority.get("instruments", []) if r.get("canonical_instrument_id") == candidate_id)
        return _result(RESOLVED, decision_date, identity_basis=_basis_from_row(row),
                        canonical_instrument_id=candidate_id)
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
# symbol) is required; see counter-examples 14/15 in the PR test suite and
# the dependent-defect note at the top of this module.
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
