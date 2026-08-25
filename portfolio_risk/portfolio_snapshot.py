#!/usr/bin/env python3
"""Portfolio Risk Input Contract -- READ-ONLY account-facts snapshot.

★ Purpose (repeated from the package docstring, deliberately, because this
  is the single most important boundary in this module): this is NOT
  "decide how much to buy". It supplies the real, PIT-safe account facts a
  FUTURE sizing/policy decision will need. Risk-budget percentages,
  stop-loss caps, max-concurrent-Probe counts, and any other policy number
  are NEVER ratified or computed here.

★ Physical separation of concerns (all four ALWAYS present, never merged):
    - `portfolio_facts`       -- real observed account facts (Alpaca paper
      account/positions, or explicitly-labeled manual snapshots).
    - `risk_capacity_inputs`  -- inputs a FUTURE policy calculation will
      consume (NAV/cash/exposure breakdowns, completeness/staleness).
    - `risk_policy`           -- ALWAYS `{"approval_status": "UNRATIFIED"}`
      in this module. No real policy value is ever computed or stored here.
    - `position_size`         -- ALWAYS
      `{"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED"}`. No sizing number
      is ever computed here, structurally (see `POSITION_SIZE_UNRATIFIED`,
      a module-level frozen constant, never a function that could branch).

★ Authority: every packet carries a hard-`False` authority block. No code
  path in this module ever sets any of these True.

★ IMPORTANT -- this module builds a real packet containing real financial
  figures IN MEMORY. It is `capture.py`'s job (not this module's) to never
  let that real packet reach a public-repo write path -- see
  `capture._redact_for_public_repo()`. This module never writes to disk
  itself; it is a pure in-memory builder/validator.

★ CIO review history (2026-08-23):

  Round 1 fixed 6 P0/P1 defects (raw account-number leak into evidence,
  non-append-only storage, blended multi-currency math, stale/mismatch not
  gating the whole risk block, caller-shrinkable account scope, hash-only
  tamper validation). See git history for the round-1 fix commit.

  Round 2 found a much bigger defect: this repo (`yonggeun1021-hub/atlas-data`)
  is PUBLIC. Round 1's fix (sanitizing the account number out of raw
  evidence) does not address that real NAV/cash/positions/P&L figures
  would still be committed publicly -- that is fixed entirely in
  `capture.py` (never write the real packet anywhere, only a redacted,
  explicitly-allowlisted public-safe summary). Round 2 ALSO found and
  fixed 4 real PIT defects in *this* module, all at the fix sites below:
    1. `_enforce_pit_timing()` -- a future-dated account `captured_at` was
       silently passing as `FRESH` (a negative staleness age is not `>
       STALENESS_MAX_AGE_HOURS`). Now explicitly REJECTED before staleness
       is ever computed, in both `build_alpaca_paper_account_fact()` and
       `build_manual_account_fact()`.
    2. `_validate_snapshot_timing()` -- `available_at > decision_at` had no
       check at all. Now explicitly REJECTED.
    3. `assemble_fx_rates()` -- a future-dated FX `as_of` had the same
       silent-FRESH bug as (1). Now explicitly REJECTED via the same
       `_enforce_pit_timing()`.
    4. `_compute_risk_capacity_inputs()` -- any manual/unverified account
       fact anywhere in the input was allowed to reach `status:
       COMPUTABLE` / `full_portfolio_nav_status: OK` exactly like a fully
       broker-verified snapshot. Now: any unverified source present forces
       `status: DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT` (never
       `COMPUTABLE`), `full_portfolio_nav` stays `null` with
       `full_portfolio_nav_status: NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE`
       unconditionally, and every other computed figure's own status is
       downgraded from `OK` to `DIAGNOSTIC_UNVERIFIED`.
  Confirmed correct from round 1, unchanged: the order-API structural
  block (`alpaca_client.py`), the hard-coded paper host, multi-currency
  arithmetic separation (`_aggregate_base_currency`), the direction of the
  stale/mismatch block (still highest-priority gate), and the validator's
  independent re-derivation of downstream values.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

SCHEMA_VERSION = "portfolio_risk_input/1"
POSITION_SOURCE_IDENTITY_CONTRACT_VERSION = "portfolio_position_source_lineage/1"
POSITION_SOURCE_NAME_ALPACA = "alpaca_paper_positions"
SOURCE_IDENTITY_AVAILABLE = "AVAILABLE"
SOURCE_IDENTITY_MISSING = "NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING"
RAW_EXPOSURE_IDENTITY_BASIS = "RAW_PROVIDER_SYMBOL_DIAGNOSTIC_NOT_CANONICAL_INSTRUMENT"
FORBIDDEN_POSITION_IDENTITY_CLAIMS = frozenset({
    "canonical_issuer_id", "canonical_instrument_id", "listing_id", "identity_status",
})
BASE_CURRENCY = "USD"

# ★ Fix 5/6 (round 1): the set of markets that make up "the portfolio" is a
#   fixed registry, NEVER a caller-suppliable parameter -- there is nothing
#   here a caller could shrink to make an incomplete scope read as complete.
CANONICAL_ACCOUNT_SCOPE = frozenset({"ALPACA_PAPER_ACCOUNT", "KOREA", "CRYPTO"})

# ★ Fix 1/2 (round 1): recursively stripped from ANY raw broker payload
#   before it is ever written anywhere -- kept as defense-in-depth even
#   though round 2 additionally forbids writing ANY real-figure payload to
#   the public repo at all (see capture.py).
FORBIDDEN_RAW_EVIDENCE_KEYS = frozenset({"account_number", "id"})

AUTHORITY_ALL_FALSE = {
    "review_only": True,
    "action_authorized": False,
    "order_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}

# ★ Structurally constant -- never a function, never branches, never takes
# an argument that could change the value. No real sizing number can ever
# come out of this module.
POSITION_SIZE_UNRATIFIED = {"status": "NOT_COMPUTABLE_POLICY_UNRATIFIED"}
RISK_POLICY_UNRATIFIED = {
    "approval_status": "UNRATIFIED",
    "note": (
        "No real risk-budget percentage, stop-loss cap, max-concurrent-Probe count, or any "
        "other policy value is ratified or computed by this module. See "
        "docs/portfolio_risk_input_contract.md."
    ),
}

# Diagnostic-only thresholds -- explicitly NOT ratified risk-policy values,
# used only to flag obvious data-quality problems (rounding noise vs a
# genuine account/position mismatch; a stale broker read vs a fresh one).
NAV_RECONCILIATION_TOLERANCE_PCT = 0.5
STALENESS_MAX_AGE_HOURS = 24


class PortfolioSnapshotError(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _hash_identifier(raw: str) -> str:
    """Never store a raw broker account identifier in committed evidence --
    only its sha256. This is what makes cross-run consistency checks
    possible (same hash = same account) without ever writing the real
    account number in plaintext anywhere. NOTE: since round 2, even this
    hash never reaches a public-repo write path -- see
    `capture._redact_for_public_repo()`, which does not include
    `account_id_hash` at all."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_for_raw_evidence(value, forbidden_keys: frozenset = FORBIDDEN_RAW_EVIDENCE_KEYS):
    """Recursively strip every key in `forbidden_keys` from a raw broker
    response. Kept as a still-useful, still-tested utility (defense in
    depth for any future private-storage evidence path), but round 2
    established this alone is NOT sufficient to make a payload public-repo
    safe -- real NAV/cash/position/P&L figures are still real financial
    data even with the account number removed. `capture.py`'s live-capture
    path does not write any raw payload (sanitized or not) to disk at all;
    see `capture._redact_for_public_repo()`."""
    if isinstance(value, dict):
        return {k: sanitize_for_raw_evidence(v, forbidden_keys) for k, v in value.items() if k not in forbidden_keys}
    if isinstance(value, list):
        return [sanitize_for_raw_evidence(v, forbidden_keys) for v in value]
    return value


def _parse_utc(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PortfolioSnapshotError(f"TIMESTAMP_INVALID:{value!r}") from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def _validate_snapshot_timing(captured_at: str, available_at: str, decision_at: str) -> None:
    """Future-dated snapshot vs a past decision -- rejected. Enforces the
    full ordering `captured_at <= available_at <= decision_at` -- never
    merely asserted at one call site.

    ★ CIO round 2 PIT fix 2: the previous version never checked
    `available_at > decision_at` at all -- a snapshot claiming to have
    become "available" AFTER the decision it's being used for passed
    validation silently. Now explicitly REJECTED."""
    captured = _parse_utc(captured_at)
    available = _parse_utc(available_at)
    decision = _parse_utc(decision_at)
    if available < captured:
        raise PortfolioSnapshotError(
            f"TIMING_INVARIANT_VIOLATED:available_at({available_at})<captured_at({captured_at})"
        )
    if captured > decision:
        raise PortfolioSnapshotError(
            f"FUTURE_DATED_SNAPSHOT_REJECTED:captured_at({captured_at})>decision_at({decision_at})"
        )
    if available > decision:
        raise PortfolioSnapshotError(
            f"AVAILABLE_AFTER_DECISION_REJECTED:available_at({available_at})>decision_at({decision_at})"
        )


def _enforce_pit_timing(*, label: str, event_at: str, decision_at: str) -> None:
    """★ CIO round 2 PIT fix 1/3: an `event_at` (an account fact's own
    `captured_at`, or an FX rate's `as_of`) that is AFTER `decision_at` is
    a genuine PIT violation and must be REJECTED outright -- NOT silently
    marked `FRESH` by an accidentally-negative staleness age (the previous
    `_staleness_status()` computed `age_hours = (decision -
    event).total_seconds() / 3600`, and a future `event_at` makes that
    negative, which is not `> STALENESS_MAX_AGE_HOURS` and so read as
    fresh). Call this BEFORE `_staleness_status()` everywhere an
    externally-supplied timestamp is compared against `decision_at`."""
    event = _parse_utc(event_at)
    decision = _parse_utc(decision_at)
    if event > decision:
        raise PortfolioSnapshotError(
            f"FUTURE_DATED_VALUE_REJECTED:{label}={event_at}>decision_at={decision_at}"
        )


def _staleness_status(captured_at: str, decision_at: str) -> str:
    """Only ever called AFTER `_enforce_pit_timing()` has already confirmed
    `captured_at <= decision_at` -- so `age_hours` here is always >= 0."""
    captured = _parse_utc(captured_at)
    decision = _parse_utc(decision_at)
    age_hours = (decision - captured).total_seconds() / 3600.0
    return "STALE" if age_hours > STALENESS_MAX_AGE_HOURS else "FRESH"


def _derive_account_fact_diagnostics(equity: float, cash: float, positions: list[dict],
                                      captured_at: str, decision_at: str) -> dict:
    """★ CIO round 3 fix: recomputes `position_count` /
    `nav_reconciliation_status` / `nav_reconciliation_mismatch_pct` /
    `staleness_status` purely from a fact's own RAW fields (`equity`,
    `cash`, `positions`, `captured_at`) against `decision_at` -- NEVER
    trusts a fact's own self-reported values for these. This is the ONE
    implementation used by BOTH `build_alpaca_paper_account_fact()` /
    `build_manual_account_fact()` (to build) AND `validate_snapshot()` (to
    independently re-derive and compare) -- there is no second
    implementation that could drift out of sync with the first, which is
    exactly how round 2's "validator only re-hashed" class of bug
    happened again at the per-fact level (round 3 CIO finding:
    `validate_snapshot()` trusted each fact's embedded `staleness_status`
    instead of recomputing it, so a post-hoc-tampered `staleness_status`/
    `position_count`/`nav_reconciliation_status` + a freshly regenerated
    hash passed validation)."""
    position_market_value_sum = sum(p["market_value"] for p in positions)
    computed_total = cash + position_market_value_sum
    denom = max(abs(equity), 1e-9)
    mismatch_pct = abs(equity - computed_total) / denom * 100.0
    nav_reconciliation_status = "OK" if mismatch_pct <= NAV_RECONCILIATION_TOLERANCE_PCT else "MISMATCH_FLAGGED"
    return {
        "position_count": len(positions),
        "nav_reconciliation_status": nav_reconciliation_status,
        "nav_reconciliation_mismatch_pct": mismatch_pct,
        "staleness_status": _staleness_status(captured_at, decision_at),
    }


def _assert_authority_all_false(authority: dict) -> None:
    if authority != AUTHORITY_ALL_FALSE:
        raise PortfolioSnapshotError("AUTHORITY_BLOCK_TAMPERED_OR_NOT_ALL_FALSE")


def _require_finite_number(value, field: str) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioSnapshotError(f"NON_NUMERIC_VALUE:{field}={value!r}") from exc
    if math.isnan(f) or math.isinf(f):
        raise PortfolioSnapshotError(f"NON_FINITE_VALUE:{field}={value!r}")
    return f


def _dedupe_positions(raw_positions: list[dict]) -> list[dict]:
    """Duplicate positions for the same symbol are a genuine data problem
    (a real broker never legitimately returns the same open position
    twice) -- reject rather than silently pick one."""
    seen: dict[str, dict] = {}
    for row in raw_positions:
        symbol = row.get("symbol")
        if symbol is None:
            raise PortfolioSnapshotError("POSITION_MISSING_SYMBOL")
        if symbol in seen:
            if canonical_json(seen[symbol]) != canonical_json(row):
                raise PortfolioSnapshotError(f"DUPLICATE_POSITION_CONFLICTING_DATA:{symbol}")
            continue  # identical duplicate row -- dedupe silently
        seen[symbol] = row
    return [seen[s] for s in sorted(seen)]


def _source_pair(source_name: str, source_asset_id: str) -> dict:
    """Build one exact provider identity pair without ticker normalization.

    ``source_asset_id`` must be the identifier directly supplied by the
    provider adapter.  Callers may not substitute a display ticker or infer a
    canonical instrument here.
    """
    if not isinstance(source_name, str) or not source_name.strip():
        raise PortfolioSnapshotError("POSITION_SOURCE_NAME_INVALID")
    if not isinstance(source_asset_id, str) or not source_asset_id.strip():
        raise PortfolioSnapshotError("POSITION_SOURCE_ASSET_ID_INVALID")
    return {"source_name": source_name, "source_asset_id": source_asset_id}


def _alpaca_position_source_identity(row: dict) -> dict:
    """Transport Alpaca's own stable ``asset_id`` from `/v2/positions`.

    The display ``symbol`` is deliberately not used as ``source_asset_id``.
    This is an adapter-boundary extraction, not an identity mapping or an
    investability assertion.
    """
    if "asset_id" not in row:
        raise PortfolioSnapshotError("ALPACA_POSITION_ASSET_ID_MISSING")
    return {
        "contract_version": POSITION_SOURCE_IDENTITY_CONTRACT_VERSION,
        "status": SOURCE_IDENTITY_AVAILABLE,
        "source_pairs": [_source_pair(POSITION_SOURCE_NAME_ALPACA, row["asset_id"])],
    }


def _manual_position_source_identity(row: dict) -> dict:
    """Preserve optional manual provider identifiers but never verify them.

    A partial pair is rejected.  A complete pair is retained for audit, while
    the status remains NOT_COMPUTABLE because a manual account fact is not a
    broker-verified source.
    """
    source_name = row.get("source_name")
    source_asset_id = row.get("source_asset_id")
    if (source_name is None) != (source_asset_id is None):
        raise PortfolioSnapshotError("MANUAL_POSITION_SOURCE_IDENTITY_PARTIAL")
    pairs = [] if source_name is None else [_source_pair(source_name, source_asset_id)]
    return {
        "contract_version": POSITION_SOURCE_IDENTITY_CONTRACT_VERSION,
        "status": SOURCE_IDENTITY_MISSING,
        "source_pairs": pairs,
    }


def _validate_position_source_identity(account_fact: dict) -> None:
    """Fail closed on lineage tampering independently of packet hashing.

    For Alpaca facts the provider name is fixed by the read-only endpoint and
    each normalized position must contain exactly one non-empty asset ID.
    Manual facts may retain a complete pair for audit, but can never claim
    AVAILABLE.  This verifies the transport contract only; authenticity after
    capture is provided by the private append-only record, not by this
    self-contained packet.
    """
    source = account_fact.get("source")
    verification_status = account_fact.get("verification_status")
    if source == "ALPACA_PAPER_ACCOUNT":
        if verification_status != "BROKER_VERIFIED":
            raise PortfolioSnapshotError("ALPACA_ACCOUNT_VERIFICATION_STATUS_INVALID")
    elif isinstance(source, str) and source.startswith("MANUAL_SNAPSHOT:"):
        if verification_status != "PAPER_OR_MANUAL_UNVERIFIED":
            raise PortfolioSnapshotError("MANUAL_ACCOUNT_VERIFICATION_STATUS_INVALID")
    else:
        raise PortfolioSnapshotError(f"POSITION_SOURCE_IDENTITY_UNSUPPORTED_ACCOUNT_SOURCE:{source}")

    seen_source_pairs: set[tuple[str, str]] = set()
    for position in account_fact.get("positions", []):
        forbidden = sorted(FORBIDDEN_POSITION_IDENTITY_CLAIMS & set(position))
        if forbidden:
            raise PortfolioSnapshotError(f"POSITION_CANONICAL_IDENTITY_CLAIM_FORBIDDEN:{forbidden}")
        lineage = position.get("source_identity_lineage")
        if not isinstance(lineage, dict) or set(lineage) != {"contract_version", "status", "source_pairs"}:
            raise PortfolioSnapshotError("POSITION_SOURCE_IDENTITY_LINEAGE_SCHEMA_INVALID")
        if lineage.get("contract_version") != POSITION_SOURCE_IDENTITY_CONTRACT_VERSION:
            raise PortfolioSnapshotError("POSITION_SOURCE_IDENTITY_CONTRACT_VERSION_INVALID")
        pairs = lineage.get("source_pairs")
        if not isinstance(pairs, list):
            raise PortfolioSnapshotError("POSITION_SOURCE_IDENTITY_PAIRS_NOT_A_LIST")
        normalized_pairs = []
        for pair in pairs:
            if not isinstance(pair, dict) or set(pair) != {"source_name", "source_asset_id"}:
                raise PortfolioSnapshotError("POSITION_SOURCE_IDENTITY_PAIR_SCHEMA_INVALID")
            normalized = _source_pair(pair["source_name"], pair["source_asset_id"])
            normalized_pairs.append((normalized["source_name"], normalized["source_asset_id"]))
        if normalized_pairs != sorted(set(normalized_pairs)):
            raise PortfolioSnapshotError("POSITION_SOURCE_IDENTITY_PAIRS_NOT_CANONICAL")

        if source == "ALPACA_PAPER_ACCOUNT":
            if lineage.get("status") != SOURCE_IDENTITY_AVAILABLE:
                raise PortfolioSnapshotError("ALPACA_POSITION_SOURCE_IDENTITY_NOT_AVAILABLE")
            if len(normalized_pairs) != 1 or normalized_pairs[0][0] != POSITION_SOURCE_NAME_ALPACA:
                raise PortfolioSnapshotError("ALPACA_POSITION_SOURCE_IDENTITY_INVALID")
        elif isinstance(source, str) and source.startswith("MANUAL_SNAPSHOT:"):
            if lineage.get("status") != SOURCE_IDENTITY_MISSING:
                raise PortfolioSnapshotError("MANUAL_POSITION_SOURCE_IDENTITY_CANNOT_BE_AVAILABLE")
            if len(normalized_pairs) > 1:
                raise PortfolioSnapshotError("MANUAL_POSITION_SOURCE_IDENTITY_MULTIPLE_PAIRS")

        # One account snapshot must never contain the same exact provider
        # asset twice under two different display symbols.
        # This is not canonical alias resolution: we merge nothing and make
        # no investability claim.  We simply reject a provider-identity
        # collision before raw-symbol exposure can be double-counted.
        for pair in normalized_pairs:
            if pair in seen_source_pairs:
                raise PortfolioSnapshotError(
                    "DUPLICATE_POSITION_SOURCE_IDENTITY_WITHIN_ACCOUNT:"
                    f"{pair[0]}:{pair[1]}"
                )
            seen_source_pairs.add(pair)


def _validate_account_fact_uniqueness(account_facts: list[dict]) -> None:
    """Reject the same account snapshot appearing twice in one packet.

    Two distinct accounts may legitimately hold the same instrument and are
    kept separate.  Repeating one ``account_id_hash`` is never a second
    holding, however; accepting it would double NAV, cash and exposure.
    """
    seen: dict[str, int] = {}
    for index, fact in enumerate(account_facts):
        account_id_hash = fact.get("account_id_hash")
        if (
            not isinstance(account_id_hash, str)
            or len(account_id_hash) != 64
            or any(ch not in "0123456789abcdef" for ch in account_id_hash)
        ):
            raise PortfolioSnapshotError(
                f"ACCOUNT_ID_HASH_INVALID:account[{index}]"
            )
        if account_id_hash in seen:
            raise PortfolioSnapshotError(
                "DUPLICATE_ACCOUNT_FACT:"
                f"account[{seen[account_id_hash]}] == account[{index}]"
            )
        seen[account_id_hash] = index


def _order_eligibility_status(account: dict) -> str:
    blocked_flags = [
        flag for flag in ("trading_blocked", "account_blocked", "trade_suspended_by_user")
        if bool(account.get(flag))
    ]
    status = account.get("status")
    if blocked_flags:
        return "BLOCKED:" + "+".join(sorted(blocked_flags))
    if status != "ACTIVE":
        return f"BLOCKED:status={status}"
    return "ELIGIBLE_DIAGNOSTIC_ONLY"  # ★ diagnostic status field -- NEVER an authorization


def build_alpaca_paper_account_fact(account: dict, raw_positions: list[dict], *,
                                     captured_at: str, decision_at: str) -> dict:
    """`account` / `raw_positions` are the RAW Alpaca `/v2/account` /
    `/v2/positions` response bodies (already JSON-decoded). This function
    is where the real account number is last seen in memory before being
    replaced by `account_id_hash` -- it is never returned or logged here."""
    required_account_fields = ("account_number", "currency", "equity", "cash", "buying_power", "status")
    missing = [f for f in required_account_fields if f not in account]
    if missing:
        raise PortfolioSnapshotError(f"ALPACA_ACCOUNT_FIELDS_MISSING:{missing}")

    _enforce_pit_timing(label="alpaca_account.captured_at", event_at=captured_at, decision_at=decision_at)

    equity = _require_finite_number(account["equity"], "equity")
    cash = _require_finite_number(account["cash"], "cash")
    buying_power = _require_finite_number(account["buying_power"], "buying_power")
    if equity < 0 or cash < 0:
        raise PortfolioSnapshotError(f"NEGATIVE_NAV_OR_CASH_REJECTED:equity={equity}:cash={cash}")

    positions = _dedupe_positions(raw_positions)
    normalized_positions = []
    for row in positions:
        market_value = _require_finite_number(row.get("market_value"), f"position.market_value[{row.get('symbol')}]")
        qty = _require_finite_number(row.get("qty"), f"position.qty[{row.get('symbol')}]")
        unrealized_pl = _require_finite_number(row.get("unrealized_pl", 0.0), f"position.unrealized_pl[{row.get('symbol')}]")
        normalized_positions.append({
            "symbol": row["symbol"],
            "quantity": qty,
            "market_value": market_value,
            "unrealized_pl": unrealized_pl,
            "currency": account["currency"],
            "source_identity_lineage": _alpaca_position_source_identity(row),
        })

    diagnostics = _derive_account_fact_diagnostics(equity, cash, normalized_positions, captured_at, decision_at)

    fact = {
        "account_id_hash": _hash_identifier(str(account["account_number"])),  # ★ never the raw account number
        "source": "ALPACA_PAPER_ACCOUNT",
        "verification_status": "BROKER_VERIFIED",
        "currency": account["currency"],
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "positions": normalized_positions,
        "position_count": diagnostics["position_count"],
        "order_eligibility_status": _order_eligibility_status(account),
        "nav_reconciliation_status": diagnostics["nav_reconciliation_status"],
        "nav_reconciliation_mismatch_pct": diagnostics["nav_reconciliation_mismatch_pct"],
        "staleness_status": diagnostics["staleness_status"],
        "captured_at": captured_at,
    }
    _validate_position_source_identity(fact)
    return fact


def build_manual_account_fact(*, market: str, currency: str, cash: float,
                               positions: list[dict], captured_at: str, decision_at: str,
                               claimed_verification_status: str | None = None) -> dict:
    """Manual/fixture input for accounts not connected via a real broker
    API (Korea, Crypto today). `verification_status` is ALWAYS forced to
    `PAPER_OR_MANUAL_UNVERIFIED` -- if a caller explicitly tries to claim
    `BROKER_VERIFIED` for a manual entry, that is a disguise attempt and is
    REJECTED outright (never silently downgraded). See
    `_compute_risk_capacity_inputs()` for how the presence of ANY manual
    fact downgrades the whole snapshot's computed figures to
    `DIAGNOSTIC_UNVERIFIED` (CIO round 2 PIT fix 4)."""
    if claimed_verification_status is not None and claimed_verification_status != "PAPER_OR_MANUAL_UNVERIFIED":
        raise PortfolioSnapshotError(
            f"MANUAL_INPUT_DISGUISED_AS_VERIFIED:claimed={claimed_verification_status!r}"
        )
    _enforce_pit_timing(label=f"manual_account[{market}].captured_at", event_at=captured_at, decision_at=decision_at)
    cash_f = _require_finite_number(cash, "manual.cash")
    if cash_f < 0:
        raise PortfolioSnapshotError(f"NEGATIVE_NAV_OR_CASH_REJECTED:manual.cash={cash_f}")

    normalized_positions = _dedupe_positions(positions)
    position_rows = []
    market_value_sum = 0.0
    for row in normalized_positions:
        market_value = _require_finite_number(row.get("market_value"), f"manual.position.market_value[{row.get('symbol')}]")
        qty = _require_finite_number(row.get("qty", row.get("quantity")), f"manual.position.qty[{row.get('symbol')}]")
        market_value_sum += market_value
        position_rows.append({
            "symbol": row["symbol"], "quantity": qty, "market_value": market_value,
            "unrealized_pl": _require_finite_number(row.get("unrealized_pl", 0.0), "manual.unrealized_pl"),
            "currency": currency,
            "source_identity_lineage": _manual_position_source_identity(row),
        })

    equity = cash_f + market_value_sum  # ★ derived FROM cash+positions here, so this always reconciles exactly
    diagnostics = _derive_account_fact_diagnostics(equity, cash_f, position_rows, captured_at, decision_at)

    fact = {
        "account_id_hash": _hash_identifier(f"MANUAL:{market}"),
        "source": f"MANUAL_SNAPSHOT:{market}",
        "verification_status": "PAPER_OR_MANUAL_UNVERIFIED",
        "currency": currency,
        "equity": equity,
        "cash": cash_f,
        "buying_power": cash_f,  # manual entries: no margin concept, buying_power == cash
        "positions": position_rows,
        "position_count": diagnostics["position_count"],
        "order_eligibility_status": "NOT_APPLICABLE_MANUAL_SNAPSHOT",
        "nav_reconciliation_status": diagnostics["nav_reconciliation_status"],
        "nav_reconciliation_mismatch_pct": diagnostics["nav_reconciliation_mismatch_pct"],
        "staleness_status": diagnostics["staleness_status"],
        "captured_at": captured_at,
    }
    _validate_position_source_identity(fact)
    return fact


def _validate_fx_rate_value(rate: float, pair: str) -> float:
    if rate <= 0:
        raise PortfolioSnapshotError(f"NON_POSITIVE_FX_RATE:{pair}={rate}")
    return rate


def _derive_fx_staleness(as_of: str | None, decision_at: str, *, pair: str) -> str:
    """★ CIO round 3 fix: the ONE implementation of FX staleness derivation,
    used by BOTH `assemble_fx_rates()` (to build) AND `validate_snapshot()`
    (to independently re-derive and compare against a claimed value) --
    same reasoning as `_derive_account_fact_diagnostics()`: a stored
    `staleness_status` must never be trusted at validate time, only
    re-derived from `as_of`."""
    if not as_of:
        return "STALE"
    _enforce_pit_timing(label=f"fx[{pair}].as_of", event_at=as_of, decision_at=decision_at)
    return _staleness_status(as_of, decision_at)


def assemble_fx_rates(fx_inputs: dict[str, dict] | None, decision_at: str) -> dict:
    """Keeps FX provenance separate per pair -- never blended into a single
    number. Each entry: {"rate": float, "as_of": iso8601, "source": str}.
    A stale or missing rate is reported per-pair, never silently dropped.

    ★ CIO round 2 PIT fix 3: a future-dated `as_of` (after `decision_at`)
    is now explicitly REJECTED via `_enforce_pit_timing()`, closing the
    same silent-FRESH bug fixed for account facts (fix 1)."""
    fx_inputs = fx_inputs or {}
    out = {}
    for pair, entry in sorted(fx_inputs.items()):
        rate = _validate_fx_rate_value(_require_finite_number(entry.get("rate"), f"fx[{pair}].rate"), pair)
        as_of = entry.get("as_of")
        stale = _derive_fx_staleness(as_of, decision_at, pair=pair)
        out[pair] = {"rate": rate, "as_of": as_of, "source": entry.get("source", "UNKNOWN"), "staleness_status": stale}
    return out


def _market_label(source: str) -> str:
    return source.split(":")[-1] if ":" in source else source


def _convert_amount_to_base(amount: float, currency: str, fx_rates: dict, base_currency: str = BASE_CURRENCY):
    """The ONE place amounts are ever converted across currency. Returns
    (converted_amount_or_None, status, fx_pair_or_None). Never silently
    blends -- a missing or stale rate always yields `None` plus an
    explicit NOT_COMPUTABLE status, never a partial/estimated total."""
    if currency == base_currency:
        return amount, "OK", None
    pair = f"{currency}/{base_currency}"
    fx = fx_rates.get(pair)
    if fx is None:
        return None, "NOT_COMPUTABLE_MISSING_FX_RATE", pair
    if fx.get("staleness_status") == "STALE":
        return None, "NOT_COMPUTABLE_STALE_FX_RATE", pair
    return amount * fx["rate"], "OK", pair


def _aggregate_base_currency(items, fx_rates: dict):
    """`items`: list[(amount, currency)]. Returns (total_or_None, status,
    detail). The FIRST item that cannot be converted stops the whole
    aggregate at NOT_COMPUTABLE -- never a partial/estimated sum."""
    total = 0.0
    for amount, currency in items:
        converted, status, pair = _convert_amount_to_base(amount, currency, fx_rates)
        if status != "OK":
            return None, status, pair
        total += converted
    return total, "OK", None


def _exposure_breakdowns_raw(account_facts: list[dict]) -> dict:
    """Per-ticker/market/currency RAW sums. `by_currency` in particular is
    the never-blended-across-currency view -- it is NOT a substitute for a
    real cross-currency total (see `*_base_currency` fields for that)."""
    by_ticker: dict[str, float] = {}
    by_market: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    for f in account_facts:
        market = _market_label(f["source"])
        for p in f["positions"]:
            by_ticker[p["symbol"]] = by_ticker.get(p["symbol"], 0.0) + p["market_value"]
            by_market[market] = by_market.get(market, 0.0) + p["market_value"]
            by_currency[p["currency"]] = by_currency.get(p["currency"], 0.0) + p["market_value"]
    return {
        "by_ticker": [{"symbol": k, "market_value": v} for k, v in sorted(by_ticker.items())],
        "by_market": [{"market": k, "market_value": v} for k, v in sorted(by_market.items())],
        "by_currency": [{"currency": k, "market_value": v} for k, v in sorted(by_currency.items())],
    }


def _account_scope_label(present_set: set[str]) -> str:
    if present_set == {"ALPACA_PAPER_ACCOUNT"}:
        return "US_PAPER_ACCOUNT_SCOPE_ONLY"
    if present_set == set(CANONICAL_ACCOUNT_SCOPE):
        return "FULL_CANONICAL_ACCOUNT_SCOPE"
    return "PARTIAL_ACCOUNT_SCOPE:" + "+".join(sorted(present_set))


def _not_computable_risk_capacity_block(status: str, completeness: dict, account_scope_label: str) -> dict:
    """The WHOLE risk_capacity_inputs block goes NOT_COMPUTABLE -- every
    number is None, not just flagged while still being computed. Used for
    the stale/mismatch gate (highest priority -- checked before the
    unverified-source downgrade)."""
    return {
        "status": status,
        "account_scope_label": account_scope_label,
        "data_completeness": completeness,
        "existing_position_count": None,
        "connected_scope_nav": None, "connected_scope_nav_status": status,
        "full_portfolio_nav": None, "full_portfolio_nav_status": status,
        "cash_by_currency": [],
        "total_cash_base_currency": None, "total_cash_base_currency_status": status,
        "exposure_identity_basis": RAW_EXPOSURE_IDENTITY_BASIS,
        "exposure_by_ticker": [], "exposure_by_market": [], "exposure_by_currency": [],
        "gross_exposure_base_currency": None, "gross_exposure_base_currency_status": status,
        "net_exposure_base_currency": None, "net_exposure_base_currency_status": status,
    }


def _compute_risk_capacity_inputs(account_facts: list[dict], fx_rates: dict) -> dict:
    """The single source of truth for every risk-capacity number. Called
    from BOTH `assemble_snapshot()` (to build) and `validate_snapshot()`
    (to independently re-derive from `portfolio_facts` and compare against
    the packet's claimed values). Never takes an `expected_sources`
    parameter: account scope is always `CANONICAL_ACCOUNT_SCOPE`.

    Priority order for `status` (highest first):
      1. Any stale account or NAV-reconciliation mismatch ->
         `NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT` (whole block nulled).
      2. Any manual/unverified account fact present (CIO round 2 PIT fix 4)
         -> `DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT` (figures ARE
         still computed, but every status downgrades from `OK` to
         `DIAGNOSTIC_UNVERIFIED`, and `full_portfolio_nav` is *always*
         `null` / `NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE`, even with a
         full canonical scope connected).
      3. Otherwise -> `COMPUTABLE`.
    """
    present_sources = sorted({_market_label(f["source"]) for f in account_facts})
    present_set = set(present_sources)
    missing_canonical = sorted(CANONICAL_ACCOUNT_SCOPE - present_set)
    account_scope_label = _account_scope_label(present_set)

    any_stale = any(f["staleness_status"] == "STALE" for f in account_facts) or any(
        v.get("staleness_status") == "STALE" for v in fx_rates.values())
    any_mismatch = any(f["nav_reconciliation_status"] != "OK" for f in account_facts)

    completeness = {
        "canonical_account_scope": sorted(CANONICAL_ACCOUNT_SCOPE),
        "present_sources": present_sources,
        "missing_sources": missing_canonical,
        "any_stale": any_stale,
        "any_nav_reconciliation_mismatch": any_mismatch,
    }

    if any_stale or any_mismatch:
        return _not_computable_risk_capacity_block(
            "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT", completeness, account_scope_label,
        )

    # -- Shared computation (identical regardless of verification status) --
    existing_position_count = sum(f["position_count"] for f in account_facts)

    connected_scope_nav, connected_scope_nav_status, _ = _aggregate_base_currency(
        [(f["equity"], f["currency"]) for f in account_facts], fx_rates,
    )

    cash_by_currency_map: dict[str, float] = {}
    for f in account_facts:
        cash_by_currency_map[f["currency"]] = cash_by_currency_map.get(f["currency"], 0.0) + f["cash"]
    cash_by_currency = [{"currency": k, "amount": v} for k, v in sorted(cash_by_currency_map.items())]

    total_cash_base_currency, total_cash_base_currency_status, _ = _aggregate_base_currency(
        [(f["cash"], f["currency"]) for f in account_facts], fx_rates,
    )

    exposure_raw = _exposure_breakdowns_raw(account_facts)
    gross_exposure_base_currency, gross_status, _ = _aggregate_base_currency(
        [(abs(p["market_value"]), p["currency"]) for f in account_facts for p in f["positions"]], fx_rates,
    )
    net_exposure_base_currency, net_status, _ = _aggregate_base_currency(
        [(p["market_value"], p["currency"]) for f in account_facts for p in f["positions"]], fx_rates,
    )

    has_unverified_source = any(f["verification_status"] != "BROKER_VERIFIED" for f in account_facts)

    if has_unverified_source:
        # ★ CIO round 2 PIT fix 4: unverified manual data is NEVER treated
        # as equivalent to broker-verified data for completeness purposes.
        # full_portfolio_nav is unconditionally null/NOT_COMPUTABLE here --
        # even if every canonical market happens to be present.
        def _diag(status: str) -> str:
            return "DIAGNOSTIC_UNVERIFIED" if status == "OK" else status

        return {
            "status": "DIAGNOSTIC_UNVERIFIED_ACCOUNT_SOURCE_PRESENT",
            "account_scope_label": account_scope_label,
            "data_completeness": completeness,
            "existing_position_count": existing_position_count,
            "connected_scope_nav": connected_scope_nav,
            "connected_scope_nav_status": _diag(connected_scope_nav_status),
            "full_portfolio_nav": None,
            "full_portfolio_nav_status": "NOT_COMPUTABLE_UNVERIFIED_ACCOUNT_SOURCE",
            "cash_by_currency": cash_by_currency,
            "total_cash_base_currency": total_cash_base_currency,
            "total_cash_base_currency_status": _diag(total_cash_base_currency_status),
            "exposure_identity_basis": RAW_EXPOSURE_IDENTITY_BASIS,
            "exposure_by_ticker": exposure_raw["by_ticker"],
            "exposure_by_market": exposure_raw["by_market"],
            "exposure_by_currency": exposure_raw["by_currency"],
            "gross_exposure_base_currency": gross_exposure_base_currency,
            "gross_exposure_base_currency_status": _diag(gross_status),
            "net_exposure_base_currency": net_exposure_base_currency,
            "net_exposure_base_currency_status": _diag(net_status),
        }

    # -- Fully broker-verified path --
    if missing_canonical:
        full_portfolio_nav, full_portfolio_nav_status = None, "NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE"
    else:
        full_portfolio_nav, full_portfolio_nav_status = connected_scope_nav, connected_scope_nav_status

    return {
        "status": "COMPUTABLE",
        "account_scope_label": account_scope_label,
        "data_completeness": completeness,
        "existing_position_count": existing_position_count,
        "connected_scope_nav": connected_scope_nav, "connected_scope_nav_status": connected_scope_nav_status,
        "full_portfolio_nav": full_portfolio_nav, "full_portfolio_nav_status": full_portfolio_nav_status,
        "cash_by_currency": cash_by_currency,
        "total_cash_base_currency": total_cash_base_currency,
        "total_cash_base_currency_status": total_cash_base_currency_status,
        "exposure_identity_basis": RAW_EXPOSURE_IDENTITY_BASIS,
        "exposure_by_ticker": exposure_raw["by_ticker"],
        "exposure_by_market": exposure_raw["by_market"],
        "exposure_by_currency": exposure_raw["by_currency"],
        "gross_exposure_base_currency": gross_exposure_base_currency,
        "gross_exposure_base_currency_status": gross_status,
        "net_exposure_base_currency": net_exposure_base_currency,
        "net_exposure_base_currency_status": net_status,
    }


def assemble_snapshot(*, account_facts: list[dict], fx_rates: dict,
                       captured_at: str, available_at: str, decision_at: str) -> dict:
    """Top-level orchestrator. `account_facts` are pre-built via
    `build_alpaca_paper_account_fact`/`build_manual_account_fact` -- this
    function never talks to a network itself, and never writes anything to
    disk. No `expected_sources` parameter: account scope always comes from
    `CANONICAL_ACCOUNT_SCOPE`, never from the caller."""
    _validate_snapshot_timing(captured_at, available_at, decision_at)
    _validate_account_fact_uniqueness(account_facts)
    for fact in account_facts:
        _validate_position_source_identity(fact)
    risk_capacity_inputs = _compute_risk_capacity_inputs(account_facts, fx_rates)

    packet = {
        "schema_version": SCHEMA_VERSION,
        "captured_at": captured_at,
        "available_at": available_at,
        "decision_at": decision_at,
        "authority": AUTHORITY_ALL_FALSE,
        "portfolio_facts": {
            "accounts": account_facts,
            "fx_rates": fx_rates,
        },
        "risk_capacity_inputs": risk_capacity_inputs,
        "risk_policy": RISK_POLICY_UNRATIFIED,
        "position_size": POSITION_SIZE_UNRATIFIED,
    }
    packet["packet_sha256"] = payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
    return packet


def validate_snapshot(packet: dict) -> dict:
    """Independent re-validation of a round-tripped packet.

    ★ CIO round 3 fix: a round-2-era gap let a tampered-then-rehashed
    packet through two ways: (1) `validate_snapshot()` never called
    `_validate_snapshot_timing()` at all, so a tampered `available_at` (or
    an account fact's tampered `captured_at`) past `decision_at` was never
    caught here even though the BUILDER already rejects it; (2) this
    function trusted each account fact's own embedded `staleness_status`
    (and `nav_reconciliation_status`/`nav_reconciliation_mismatch_pct`/
    `position_count`) instead of recomputing them, so
    `_compute_risk_capacity_inputs()`'s re-derivation just re-read the same
    (already tampered) values and trivially matched -- the same class of
    "build-path lockdown and validate-path lockdown drifted apart" bug as
    round 2's hash-only check, one level deeper.

    Fixed by making this the FIRST thing that runs, before anything else:
      1. `_validate_snapshot_timing()` on the packet's own
         captured_at/available_at/decision_at.
      2. Each account fact's `captured_at <= available_at` (an
         already-captured fact cannot back a snapshot that became
         "available" before it was captured).
      3. Each FX rate's `as_of <= decision_at` (`_derive_fx_staleness()`
         enforces this as a side effect).
    Then, before the final hash check: every account fact's
    `position_count`/`nav_reconciliation_status`/
    `nav_reconciliation_mismatch_pct`/`staleness_status` is independently
    RE-DERIVED from that fact's own raw fields via
    `_derive_account_fact_diagnostics()` (the SAME function the builders
    use -- there is no second implementation to drift) and compared
    field-by-field against the fact's claimed values; every FX rate's
    `rate` (finite, positive) and `staleness_status` is independently
    re-verified the same way via `_derive_fx_staleness()`. Only after all
    of that does the existing `risk_capacity_inputs` re-derivation and the
    final hash check run. None of this is bypassable by regenerating a
    fresh, internally-consistent hash over a tampered packet -- every
    check here compares against independently-recomputed values, not
    against anything the packet itself claims."""
    captured_at = packet.get("captured_at")
    available_at = packet.get("available_at")
    decision_at = packet.get("decision_at")
    _validate_snapshot_timing(captured_at, available_at, decision_at)

    facts = packet.get("portfolio_facts") or {}
    account_facts = facts.get("accounts", [])
    fx_rates = facts.get("fx_rates", {})

    _validate_account_fact_uniqueness(account_facts)

    for i, fact in enumerate(account_facts):
        _enforce_pit_timing(
            label=f"account[{i}:{fact.get('source')}].captured_at_vs_available_at",
            event_at=fact.get("captured_at"), decision_at=available_at,
        )

    for pair, entry in sorted(fx_rates.items()):
        # `_derive_fx_staleness` itself enforces `as_of <= decision_at`.
        recomputed_rate = _validate_fx_rate_value(
            _require_finite_number(entry.get("rate"), f"fx[{pair}].rate"), pair,
        )
        recomputed_fx_stale = _derive_fx_staleness(entry.get("as_of"), decision_at, pair=pair)
        if recomputed_rate != entry.get("rate") or recomputed_fx_stale != entry.get("staleness_status"):
            raise PortfolioSnapshotError(
                f"FX_DIAGNOSTIC_TAMPER_DETECTED:{pair}:rate={entry.get('rate')}->{recomputed_rate}:"
                f"staleness_status={entry.get('staleness_status')}->{recomputed_fx_stale}"
            )

    _assert_authority_all_false(packet.get("authority"))
    if packet.get("risk_policy") != RISK_POLICY_UNRATIFIED:
        raise PortfolioSnapshotError("RISK_POLICY_TAMPERED_OR_RATIFIED")
    if packet.get("position_size") != POSITION_SIZE_UNRATIFIED:
        raise PortfolioSnapshotError("POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED")

    for i, fact in enumerate(account_facts):
        _validate_position_source_identity(fact)
        recomputed = _derive_account_fact_diagnostics(
            fact.get("equity"), fact.get("cash"), fact.get("positions", []),
            fact.get("captured_at"), decision_at,
        )
        claimed = {k: fact.get(k) for k in recomputed}
        if recomputed != claimed:
            mismatched = sorted(k for k in recomputed if recomputed[k] != claimed.get(k))
            raise PortfolioSnapshotError(
                f"FACT_DIAGNOSTIC_TAMPER_DETECTED:account[{i}:{fact.get('source')}]:{mismatched}"
            )

    recomputed_risk = _compute_risk_capacity_inputs(account_facts, fx_rates)
    claimed_risk = packet.get("risk_capacity_inputs")
    if recomputed_risk != claimed_risk:
        claimed_risk = claimed_risk or {}
        mismatched_fields = sorted(
            k for k in set(recomputed_risk) | set(claimed_risk)
            if recomputed_risk.get(k) != claimed_risk.get(k)
        )
        raise PortfolioSnapshotError(f"SEMANTIC_TAMPER_DETECTED:{mismatched_fields}")

    recomputed_hash = payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
    if recomputed_hash != packet.get("packet_sha256"):
        raise PortfolioSnapshotError("PACKET_HASH_MISMATCH")
    return packet
