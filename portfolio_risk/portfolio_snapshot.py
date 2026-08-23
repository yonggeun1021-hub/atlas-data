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
  path in this module ever sets any of these True (see
  `test/test_portfolio_risk_input.py::AuthorityInvariantTests`).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

SCHEMA_VERSION = "portfolio_risk_input/1"

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


def _parse_utc(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PortfolioSnapshotError(f"TIMESTAMP_INVALID:{value!r}") from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def _validate_snapshot_timing(captured_at: str, available_at: str, decision_at: str) -> None:
    """Item 1 (future-dated snapshot vs a past decision) + item 2 (stale
    account balance) structural checks. `available_at >= captured_at` and
    `captured_at <= decision_at` are hard invariants -- never merely
    asserted at one call site."""
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
    """Item 3: duplicate positions for the same symbol are a genuine data
    problem (a real broker never legitimately returns the same open
    position twice) -- reject rather than silently pick one."""
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
    `/v2/positions` response bodies (already JSON-decoded)."""
    required_account_fields = ("account_number", "currency", "equity", "cash", "buying_power", "status")
    missing = [f for f in required_account_fields if f not in account]
    if missing:
        raise PortfolioSnapshotError(f"ALPACA_ACCOUNT_FIELDS_MISSING:{missing}")

    equity = _require_finite_number(account["equity"], "equity")
    cash = _require_finite_number(account["cash"], "cash")
    buying_power = _require_finite_number(account["buying_power"], "buying_power")
    if equity < 0 or cash < 0:
        # Item 7: negative NAV/cash is never a legitimate real-account state.
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

    # Item 8: account-level NAV must roughly reconcile with cash + sum(positions).
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
    """Item 5: manual/fixture input for accounts not connected via a real
    broker API (Korea, Crypto today). `verification_status` is ALWAYS
    forced to `PAPER_OR_MANUAL_UNVERIFIED` -- if a caller explicitly tries
    to claim `BROKER_VERIFIED` for a manual entry, that is a disguise
    attempt and is REJECTED outright (never silently downgraded)."""
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


def _compute_total_nav(account_facts: list[dict], fx_rates: dict, expected_sources: set[str]) -> dict:
    """Item 4 (mixed-currency summed without FX -- rejected), item 6/9
    (missing market data -- NOT_COMPUTABLE, never silently partial)."""
    present_sources = {f["source"].split(":")[-1] if ":" in f["source"] else f["source"] for f in account_facts}
    # Normalize expected_sources the same way (market labels), so a caller
    # can pass e.g. {"US", "KOREA", "CRYPTO"} directly.
    missing_sources = expected_sources - {s.split(":")[-1] for s in present_sources} - present_sources
    if missing_sources:
        return {
            "total_nav": None,
            "status": "NOT_COMPUTABLE_MISSING_MARKET_DATA",
            "missing_sources": sorted(missing_sources),
        }

    currencies = {f["currency"] for f in account_facts}
    if len(currencies) == 1:
        currency = next(iter(currencies))
        total = sum(f["equity"] for f in account_facts)
        return {"total_nav": total, "status": "OK", "currency": currency}

    # Multi-currency: every non-base currency needs a FRESH fx rate to USD.
    base_currency = "USD"
    total_usd = 0.0
    for f in account_facts:
        if f["currency"] == base_currency:
            total_usd += f["equity"]
            continue
        pair = f"{f['currency']}/{base_currency}"
        fx = fx_rates.get(pair)
        if fx is None:
            return {
                "total_nav": None, "status": "NOT_COMPUTABLE_MISSING_FX_RATE",
                "missing_fx_pair": pair,
            }
        if fx["staleness_status"] == "STALE":
            return {
                "total_nav": None, "status": "NOT_COMPUTABLE_STALE_FX_RATE",
                "stale_fx_pair": pair,
            }
        total_usd += f["equity"] * fx["rate"]
    return {"total_nav": total_usd, "status": "OK", "currency": base_currency}


def _exposure_breakdowns(account_facts: list[dict]) -> dict:
    by_ticker: dict[str, float] = {}
    by_market: dict[str, float] = {}
    by_currency: dict[str, float] = {}
    gross = 0.0
    net = 0.0
    for f in account_facts:
        market = f["source"].split(":")[-1] if ":" in f["source"] else f["source"]
        for p in f["positions"]:
            by_ticker[p["symbol"]] = by_ticker.get(p["symbol"], 0.0) + p["market_value"]
            by_market[market] = by_market.get(market, 0.0) + p["market_value"]
            by_currency[p["currency"]] = by_currency.get(p["currency"], 0.0) + p["market_value"]
            gross += abs(p["market_value"])
            net += p["market_value"]
    return {
        "by_ticker": [{"symbol": k, "market_value": v} for k, v in sorted(by_ticker.items())],
        "by_market": [{"market": k, "market_value": v} for k, v in sorted(by_market.items())],
        "by_currency": [{"currency": k, "market_value": v} for k, v in sorted(by_currency.items())],
        "gross_exposure": gross,
        "net_exposure": net,
    }


def assemble_snapshot(*, account_facts: list[dict], fx_rates: dict, expected_sources: set[str],
                       captured_at: str, available_at: str, decision_at: str) -> dict:
    """Top-level orchestrator. `account_facts` are pre-built via
    `build_alpaca_paper_account_fact`/`build_manual_account_fact` -- this
    function never talks to a network itself."""
    _validate_snapshot_timing(captured_at, available_at, decision_at)

    total_nav = _compute_total_nav(account_facts, fx_rates, expected_sources)
    exposure = _exposure_breakdowns(account_facts)
    total_cash = sum(f["cash"] for f in account_facts) if total_nav["status"] == "OK" else None
    existing_position_count = sum(f["position_count"] for f in account_facts)

    completeness = {
        "expected_sources": sorted(expected_sources),
        "present_sources": sorted({f["source"].split(":")[-1] if ":" in f["source"] else f["source"] for f in account_facts}),
        "any_stale": any(f["staleness_status"] == "STALE" for f in account_facts) or any(
            v.get("staleness_status") == "STALE" for v in fx_rates.values()),
        "any_nav_reconciliation_mismatch": any(f["nav_reconciliation_status"] != "OK" for f in account_facts),
    }

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
        "risk_capacity_inputs": {
            "total_nav": total_nav["total_nav"],
            "total_nav_status": total_nav["status"],
            "total_nav_detail": {k: v for k, v in total_nav.items() if k not in ("total_nav", "status")},
            "total_cash": total_cash,
            "gross_exposure": exposure["gross_exposure"] if total_nav["status"] == "OK" else None,
            "net_exposure": exposure["net_exposure"] if total_nav["status"] == "OK" else None,
            "exposure_by_ticker": exposure["by_ticker"],
            "exposure_by_market": exposure["by_market"],
            "exposure_by_currency": exposure["by_currency"],
            "existing_position_count": existing_position_count,
            "data_completeness": completeness,
        },
        "risk_policy": RISK_POLICY_UNRATIFIED,
        "position_size": POSITION_SIZE_UNRATIFIED,
    }
    packet["packet_sha256"] = payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
    return packet


def validate_snapshot(packet: dict) -> dict:
    """Independent re-validation of a round-tripped packet -- re-checks
    authority, re-hashes and compares packet_sha256 (tamper/re-signing
    detection, item 10's structural counterpart), and re-asserts
    risk_policy/position_size are still the unratified constants (item 12,
    item 13)."""
    _assert_authority_all_false(packet.get("authority"))
    if packet.get("risk_policy") != RISK_POLICY_UNRATIFIED:
        raise PortfolioSnapshotError("RISK_POLICY_TAMPERED_OR_RATIFIED")
    if packet.get("position_size") != POSITION_SIZE_UNRATIFIED:
        raise PortfolioSnapshotError("POSITION_SIZE_COMPUTED_WHILE_POLICY_UNRATIFIED")
    recomputed = payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
    if recomputed != packet.get("packet_sha256"):
        raise PortfolioSnapshotError("PACKET_HASH_MISMATCH")
    return packet
