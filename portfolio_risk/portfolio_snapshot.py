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

★ CIO review round 1 (2026-08-23) fixed 6 P0/P1 defects found in the first
  version of this module -- each is called out at its fix site below:
    1/2. `sanitize_for_raw_evidence()` -- a real broker account number must
         NEVER reach committed evidence bytes, sanitized or not. Gzip is
         not encryption.
    3.   `_compute_risk_capacity_inputs()` -- cash/exposure totals across
         currencies are NEVER summed raw. Only `*_base_currency` fields
         (explicit FX-converted) are cross-currency totals; `cash_by_currency`
         / `exposure_by_currency` stay per-currency, never blended.
    4.   `_compute_risk_capacity_inputs()` -- ANY stale account or
         NAV-reconciliation mismatch forces `status:
         NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT` for the WHOLE
         `risk_capacity_inputs` block, not just a flag next to numbers that
         still get computed.
    5/6. `CANONICAL_ACCOUNT_SCOPE` -- account scope is NEVER caller-supplied
         (nothing to shrink arbitrarily). `full_portfolio_nav` is only ever
         non-null when every canonical market is present; a partial scope
         (e.g. Alpaca-only) gets its own explicit `account_scope_label`
         (`US_PAPER_ACCOUNT_SCOPE_ONLY`) and is never presented as the full
         portfolio total.
    7/8. `validate_snapshot()` independently RE-DERIVES `risk_capacity_inputs`
         from `portfolio_facts` via `_compute_risk_capacity_inputs()` and
         compares field-by-field against the packet's claimed values --
         this catches a "value changed + hash regenerated to match" tamper,
         which a hash-only check cannot.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

SCHEMA_VERSION = "portfolio_risk_input/1"
BASE_CURRENCY = "USD"

# ★ Fix 5/6: the set of markets that make up "the portfolio" is a fixed
#   registry, NEVER a caller-suppliable parameter -- there is nothing here
#   a caller could shrink to make an incomplete scope read as complete.
CANONICAL_ACCOUNT_SCOPE = frozenset({"ALPACA_PAPER_ACCOUNT", "KOREA", "CRYPTO"})

# ★ Fix 1/2: recursively stripped from ANY raw broker payload before it is
#   ever written to committed evidence bytes, sanitized or not.
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
    account number in plaintext anywhere."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def sanitize_for_raw_evidence(value, forbidden_keys: frozenset = FORBIDDEN_RAW_EVIDENCE_KEYS):
    """★ Fix 1/2 (CIO P0): recursively strip every key in `forbidden_keys`
    from a raw broker response BEFORE it is ever written to committed
    evidence bytes -- gzip is not encryption, and the earlier version of
    this module stored the untouched raw Alpaca response (including the
    real `account_number`) straight into a committed `.json.gz`. This
    function is applied unconditionally in `capture.py` to every raw
    payload before compression; the caller never sees an un-sanitized raw
    write path. See
    `test/test_portfolio_risk_input.py::CounterExampleAccountNumberNeverInRawEvidence`
    for the decompress-and-scan proof."""
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
    """Future-dated snapshot vs a past decision -- rejected. `available_at
    >= captured_at` and `captured_at <= decision_at` are hard invariants --
    never merely asserted at one call site."""
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


def _staleness_status(captured_at: str, decision_at: str) -> str:
    captured = _parse_utc(captured_at)
    decision = _parse_utc(decision_at)
    age_hours = (decision - captured).total_seconds() / 3600.0
    return "STALE" if age_hours > STALENESS_MAX_AGE_HOURS else "FRESH"


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

    equity = _require_finite_number(account["equity"], "equity")
    cash = _require_finite_number(account["cash"], "cash")
    buying_power = _require_finite_number(account["buying_power"], "buying_power")
    if equity < 0 or cash < 0:
        raise PortfolioSnapshotError(f"NEGATIVE_NAV_OR_CASH_REJECTED:equity={equity}:cash={cash}")

    positions = _dedupe_positions(raw_positions)
    normalized_positions = []
    position_market_value_sum = 0.0
    for row in positions:
        market_value = _require_finite_number(row.get("market_value"), f"position.market_value[{row.get('symbol')}]")
        qty = _require_finite_number(row.get("qty"), f"position.qty[{row.get('symbol')}]")
        unrealized_pl = _require_finite_number(row.get("unrealized_pl", 0.0), f"position.unrealized_pl[{row.get('symbol')}]")
        position_market_value_sum += market_value
        normalized_positions.append({
            "symbol": row["symbol"],
            "quantity": qty,
            "market_value": market_value,
            "unrealized_pl": unrealized_pl,
            "currency": account["currency"],
        })

    computed_total = cash + position_market_value_sum
    denom = max(abs(equity), 1e-9)
    mismatch_pct = abs(equity - computed_total) / denom * 100.0
    nav_reconciliation_status = "OK" if mismatch_pct <= NAV_RECONCILIATION_TOLERANCE_PCT else "MISMATCH_FLAGGED"

    staleness_status = _staleness_status(captured_at, decision_at)

    return {
        "account_id_hash": _hash_identifier(str(account["account_number"])),  # ★ never the raw account number
        "source": "ALPACA_PAPER_ACCOUNT",
        "verification_status": "BROKER_VERIFIED",
        "currency": account["currency"],
        "equity": equity,
        "cash": cash,
        "buying_power": buying_power,
        "positions": normalized_positions,
        "position_count": len(normalized_positions),
        "order_eligibility_status": _order_eligibility_status(account),
        "nav_reconciliation_status": nav_reconciliation_status,
        "nav_reconciliation_mismatch_pct": mismatch_pct,
        "staleness_status": staleness_status,
        "captured_at": captured_at,
    }


def build_manual_account_fact(*, market: str, currency: str, cash: float,
                               positions: list[dict], captured_at: str, decision_at: str,
                               claimed_verification_status: str | None = None) -> dict:
    """Manual/fixture input for accounts not connected via a real broker
    API (Korea, Crypto today). `verification_status` is ALWAYS forced to
    `PAPER_OR_MANUAL_UNVERIFIED` -- if a caller explicitly tries to claim
    `BROKER_VERIFIED` for a manual entry, that is a disguise attempt and is
    REJECTED outright (never silently downgraded)."""
    if claimed_verification_status is not None and claimed_verification_status != "PAPER_OR_MANUAL_UNVERIFIED":
        raise PortfolioSnapshotError(
            f"MANUAL_INPUT_DISGUISED_AS_VERIFIED:claimed={claimed_verification_status!r}"
        )
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
        })

    equity = cash_f + market_value_sum
    staleness_status = _staleness_status(captured_at, decision_at)

    return {
        "account_id_hash": _hash_identifier(f"MANUAL:{market}"),
        "source": f"MANUAL_SNAPSHOT:{market}",
        "verification_status": "PAPER_OR_MANUAL_UNVERIFIED",
        "currency": currency,
        "equity": equity,
        "cash": cash_f,
        "buying_power": cash_f,  # manual entries: no margin concept, buying_power == cash
        "positions": position_rows,
        "position_count": len(position_rows),
        "order_eligibility_status": "NOT_APPLICABLE_MANUAL_SNAPSHOT",
        "nav_reconciliation_status": "OK",  # equity is derived FROM cash+positions here, always reconciles
        "nav_reconciliation_mismatch_pct": 0.0,
        "staleness_status": staleness_status,
        "captured_at": captured_at,
    }


def assemble_fx_rates(fx_inputs: dict[str, dict] | None, decision_at: str) -> dict:
    """Keeps FX provenance separate per pair -- never blended into a single
    number. Each entry: {"rate": float, "as_of": iso8601, "source": str}.
    A stale or missing rate is reported per-pair, never silently dropped."""
    fx_inputs = fx_inputs or {}
    out = {}
    for pair, entry in sorted(fx_inputs.items()):
        rate = _require_finite_number(entry.get("rate"), f"fx[{pair}].rate")
        if rate <= 0:
            raise PortfolioSnapshotError(f"NON_POSITIVE_FX_RATE:{pair}={rate}")
        as_of = entry.get("as_of")
        stale = _staleness_status(as_of, decision_at) if as_of else "STALE"
        out[pair] = {"rate": rate, "as_of": as_of, "source": entry.get("source", "UNKNOWN"), "staleness_status": stale}
    return out


def _market_label(source: str) -> str:
    return source.split(":")[-1] if ":" in source else source


def _convert_amount_to_base(amount: float, currency: str, fx_rates: dict, base_currency: str = BASE_CURRENCY):
    """★ Fix 3: the ONE place amounts are ever converted across currency.
    Returns (converted_amount_or_None, status, fx_pair_or_None). Never
    silently blends -- a missing or stale rate always yields `None` plus an
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
    """★ Fix 4: the WHOLE risk_capacity_inputs block goes NOT_COMPUTABLE --
    every number is None, not just flagged while still being computed."""
    return {
        "status": status,
        "account_scope_label": account_scope_label,
        "data_completeness": completeness,
        "existing_position_count": None,
        "connected_scope_nav": None, "connected_scope_nav_status": status,
        "full_portfolio_nav": None, "full_portfolio_nav_status": status,
        "cash_by_currency": [],
        "total_cash_base_currency": None, "total_cash_base_currency_status": status,
        "exposure_by_ticker": [], "exposure_by_market": [], "exposure_by_currency": [],
        "gross_exposure_base_currency": None, "gross_exposure_base_currency_status": status,
        "net_exposure_base_currency": None, "net_exposure_base_currency_status": status,
    }


def _compute_risk_capacity_inputs(account_facts: list[dict], fx_rates: dict) -> dict:
    """The single source of truth for every risk-capacity number. Called
    from BOTH `assemble_snapshot()` (to build) and `validate_snapshot()`
    (to independently re-derive from `portfolio_facts` and compare against
    the packet's claimed values -- Fix 7/8). Never takes an `expected_sources`
    parameter (Fix 5/6): account scope is always `CANONICAL_ACCOUNT_SCOPE`."""
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
        # ★ Fix 4: not just a flag next to numbers that keep getting
        # computed -- the entire block collapses to NOT_COMPUTABLE.
        return _not_computable_risk_capacity_block(
            "NOT_COMPUTABLE_STALE_OR_MISMATCHED_ACCOUNT", completeness, account_scope_label,
        )

    existing_position_count = sum(f["position_count"] for f in account_facts)

    connected_scope_nav, connected_scope_nav_status, _ = _aggregate_base_currency(
        [(f["equity"], f["currency"]) for f in account_facts], fx_rates,
    )

    # ★ Fix 5/6: full_portfolio_nav is non-null ONLY when every canonical
    # market is connected -- a partial scope is never presented as the
    # full-portfolio total, regardless of what the caller happens to pass in
    # (there is nothing caller-suppliable here at all).
    if missing_canonical:
        full_portfolio_nav, full_portfolio_nav_status = None, "NOT_COMPUTABLE_MISSING_ACCOUNT_SCOPE"
    else:
        full_portfolio_nav, full_portfolio_nav_status = connected_scope_nav, connected_scope_nav_status

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
    function never talks to a network itself. No `expected_sources`
    parameter (Fix 5/6) -- account scope always comes from
    `CANONICAL_ACCOUNT_SCOPE`, never from the caller."""
    _validate_snapshot_timing(captured_at, available_at, decision_at)
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

    ★ Fix 7/8 (CIO P0): the previous version of this function ONLY
    recomputed and compared `packet_sha256` -- so a caller could change
    `total_nav`/cash/exposure/completeness, regenerate a fresh hash over
    the tampered packet, and validation would pass. This version
    independently RE-DERIVES `risk_capacity_inputs` from
    `portfolio_facts` via `_compute_risk_capacity_inputs()` (the exact
    same function `assemble_snapshot()` used to build it) and compares
    field-by-field against the packet's claimed `risk_capacity_inputs`.
    A semantic tamper is caught here regardless of whether the hash was
    also regenerated to match. The final hash check still runs too (cheap,
    and catches tampering anywhere else in the packet, e.g.
    `captured_at`/`available_at`/`decision_at`)."""
    _assert_authority_all_false(packet.get("authority"))
    if packet.get("risk_policy") != RISK_POLICY_UNRATIFIED:
        raise PortfolioSnapshotError("RISK_POLICY_TAMPERED_OR_RATIFIED")
    if packet.get("position_size") != POSITION_SIZE_UNRATIFIED:
        raise PortfolioSnapshotError("POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED")

    facts = packet.get("portfolio_facts") or {}
    recomputed_risk = _compute_risk_capacity_inputs(facts.get("accounts", []), facts.get("fx_rates", {}))
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
