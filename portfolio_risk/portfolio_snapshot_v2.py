#!/usr/bin/env python3
"""Portfolio Risk Input Contract v2 -- explicit provider/scope separation.

v1 (``portfolio_snapshot.py``, ``schema_version "portfolio_risk_input/1"``)
stays completely unmodified by this file -- Alpaca capture keeps using it
exactly as it always has. This is a genuinely independent, additive v2
account-fact contract (``portfolio_account_fact/2``) for a new provider
(KIS PAPER first; see ``config/portfolio_risk_input_contract_v2.json``),
whose account fact separates ``provider`` (the actual broker/data source)
from ``account_scope`` (the market) explicitly, instead of v1's single
conflated ``source`` string (``"ALPACA_PAPER_ACCOUNT"``,
``"MANUAL_SNAPSHOT:KOREA"``).

v2 does not call or extend v1's ``_validate_position_source_identity()``
(v1's own Alpaca/Manual-only source validator) -- it has its own,
independent account-fact validator here. It reuses only v1's genuinely
public, already-ratified constant ``CANONICAL_ACCOUNT_SCOPE`` -- never its
private (``_``-prefixed) helpers -- matching this codebase's existing
convention of small, independently-auditable per-module mechanics rather
than shared private internals (see, e.g., every ``private_evidence``
module re-implementing its own ``canonical_json``/``payload_sha256``
rather than importing one shared copy).

Scope of this file: ONE provider's account fact, built and independently
re-validated. Combining a v2 KIS fact with v1 Alpaca facts into one
packet-level, cross-provider ``risk_capacity_inputs`` computation is
explicitly NOT done here -- that is a separate, later decision, not
smuggled in through this contract.

Same physical-separation boundary as v1: this supplies real, PIT-safe
account facts a FUTURE sizing/policy decision will need. It never computes
or ratifies a risk-budget percentage, a stop-loss cap, or a position size.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math

from portfolio_risk.portfolio_snapshot import CANONICAL_ACCOUNT_SCOPE

SCHEMA_VERSION = "portfolio_account_fact/2"
POSITION_SOURCE_IDENTITY_CONTRACT_VERSION = "portfolio_position_source_lineage_v2/1"
FORBIDDEN_POSITION_IDENTITY_CLAIMS = frozenset({
    "canonical_issuer_id", "canonical_instrument_id", "listing_id", "identity_status",
})

# Fixed, not caller-suppliable: which provider requires which verification
# status. Adding a new provider here is its own reviewed, additive change --
# never something a caller can assert simply by passing a matching string.
PROVIDER_VERIFICATION_STATUS = {
    "KIS_PAPER_ACCOUNT": "BROKER_VERIFIED",
}

NAV_RECONCILIATION_TOLERANCE_PCT = 0.5
STALENESS_MAX_AGE_HOURS = 24

ACCOUNT_FACT_FIELDS = {
    "contractVersion", "provider", "accountScope", "verificationStatus",
    "accountIdentityHash", "currency", "equity", "cash", "buyingPower",
    "positions", "positionCount", "orderEligibilityStatus",
    "navReconciliationStatus", "navReconciliationMismatchPct",
    "stalenessStatus", "capturedAt", "factSha256",
}
POSITION_FIELDS = {"symbol", "quantity", "market_value", "unrealized_pl", "currency", "source_identity_lineage"}


class PortfolioAccountFactV2Error(ValueError):
    pass


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise PortfolioAccountFactV2Error(f"TIMESTAMP_INVALID:{value!r}")
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PortfolioAccountFactV2Error(f"TIMESTAMP_INVALID:{value!r}") from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def _enforce_pit_timing(*, label: str, event_at: str, decision_at: str) -> None:
    """A ``captured_at`` after ``decision_at`` is a genuine PIT violation
    and is rejected outright -- never silently treated as fresh via an
    accidentally-negative staleness age."""
    event = _parse_utc(event_at)
    decision = _parse_utc(decision_at)
    if event > decision:
        raise PortfolioAccountFactV2Error(
            f"FUTURE_DATED_VALUE_REJECTED:{label}={event_at}>decision_at={decision_at}"
        )


def _staleness_status(captured_at: str, decision_at: str) -> str:
    """Only ever called after ``_enforce_pit_timing`` has already
    confirmed ``captured_at <= decision_at``."""
    captured = _parse_utc(captured_at)
    decision = _parse_utc(decision_at)
    age_hours = (decision - captured).total_seconds() / 3600.0
    return "STALE" if age_hours > STALENESS_MAX_AGE_HOURS else "FRESH"


def _require_finite_number(value: object, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioAccountFactV2Error(f"NON_NUMERIC_VALUE:{field}={value!r}") from exc
    if math.isnan(parsed) or math.isinf(parsed):
        raise PortfolioAccountFactV2Error(f"NON_FINITE_VALUE:{field}={value!r}")
    return parsed


def _dedupe_positions(raw_positions: list[dict]) -> list[dict]:
    """Duplicate positions for the same symbol are a genuine data problem
    -- reject rather than silently pick one, same discipline as v1."""
    seen: dict[str, dict] = {}
    for row in raw_positions:
        symbol = row.get("symbol")
        if symbol is None:
            raise PortfolioAccountFactV2Error("POSITION_MISSING_SYMBOL")
        if symbol in seen:
            if canonical_json(seen[symbol]) != canonical_json(row):
                raise PortfolioAccountFactV2Error(f"DUPLICATE_POSITION_CONFLICTING_DATA:{symbol}")
            continue
        seen[symbol] = row
    return [seen[key] for key in sorted(seen)]


def _derive_account_fact_diagnostics(equity: float, cash: float, positions: list[dict],
                                      captured_at: str, decision_at: str) -> dict:
    """The ONE implementation used by both the builder and the validator --
    never trusts a fact's own self-reported diagnostic values."""
    position_market_value_sum = sum(position["market_value"] for position in positions)
    computed_total = cash + position_market_value_sum
    denominator = max(abs(equity), 1e-9)
    mismatch_pct = abs(equity - computed_total) / denominator * 100.0
    nav_reconciliation_status = "OK" if mismatch_pct <= NAV_RECONCILIATION_TOLERANCE_PCT else "MISMATCH_FLAGGED"
    return {
        "position_count": len(positions),
        "nav_reconciliation_status": nav_reconciliation_status,
        "nav_reconciliation_mismatch_pct": mismatch_pct,
        "staleness_status": _staleness_status(captured_at, decision_at),
    }


def _build_position_source_identity(*, source_name: object, source_asset_id: object) -> dict:
    if not isinstance(source_name, str) or not source_name.strip():
        raise PortfolioAccountFactV2Error("POSITION_SOURCE_NAME_INVALID")
    if not isinstance(source_asset_id, str) or not source_asset_id.strip():
        raise PortfolioAccountFactV2Error("POSITION_SOURCE_ASSET_ID_INVALID")
    return {
        "contract_version": POSITION_SOURCE_IDENTITY_CONTRACT_VERSION,
        "status": "AVAILABLE",
        "source_pairs": [{"source_name": source_name, "source_asset_id": source_asset_id}],
    }


def _validate_account_identity_hash(value: object) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(ch not in "0123456789abcdef" for ch in value):
        raise PortfolioAccountFactV2Error("ACCOUNT_IDENTITY_HASH_INVALID")
    return value


def build_provider_account_fact_v2(
    *, provider: str, account_scope: str, account_identity_hash: str,
    currency: str, equity: object, cash: object, buying_power: object,
    positions: list[dict], captured_at: str, decision_at: str,
    order_eligibility_status: str = "NOT_APPLICABLE",
) -> dict:
    """``positions``: list of ``{symbol, quantity, market_value,
    unrealized_pl, source_name, source_asset_id}``. ``source_asset_id``
    must be the identifier the provider itself supplied for that position
    -- never a display ticker substituted for it, and never a
    canonical-instrument claim (that mapping is a separate, independently
    reviewed identity-alias concern, not this contract's job).
    """
    required_verification_status = PROVIDER_VERIFICATION_STATUS.get(provider)
    if required_verification_status is None:
        raise PortfolioAccountFactV2Error(f"PROVIDER_NOT_REGISTERED:{provider}")
    if account_scope not in CANONICAL_ACCOUNT_SCOPE:
        raise PortfolioAccountFactV2Error(f"ACCOUNT_SCOPE_NOT_RATIFIED:{account_scope}")
    _validate_account_identity_hash(account_identity_hash)
    _enforce_pit_timing(label=f"{provider}.captured_at", event_at=captured_at, decision_at=decision_at)

    equity_value = _require_finite_number(equity, "equity")
    cash_value = _require_finite_number(cash, "cash")
    buying_power_value = _require_finite_number(buying_power, "buying_power")
    if equity_value < 0 or cash_value < 0:
        raise PortfolioAccountFactV2Error(
            f"NEGATIVE_NAV_OR_CASH_REJECTED:equity={equity_value}:cash={cash_value}"
        )

    deduped = _dedupe_positions(positions)
    normalized_positions = []
    seen_asset_ids: set[str] = set()
    for row in deduped:
        source_asset_id = row.get("source_asset_id")
        if source_asset_id in seen_asset_ids:
            raise PortfolioAccountFactV2Error(f"DUPLICATE_POSITION_SOURCE_ASSET_ID:{source_asset_id}")
        seen_asset_ids.add(source_asset_id)
        market_value = _require_finite_number(
            row.get("market_value"), f"position.market_value[{row.get('symbol')}]"
        )
        quantity = _require_finite_number(row.get("quantity"), f"position.quantity[{row.get('symbol')}]")
        unrealized_pl = _require_finite_number(
            row.get("unrealized_pl", 0.0), f"position.unrealized_pl[{row.get('symbol')}]"
        )
        normalized_positions.append({
            "symbol": row["symbol"],
            "quantity": quantity,
            "market_value": market_value,
            "unrealized_pl": unrealized_pl,
            "currency": currency,
            "source_identity_lineage": _build_position_source_identity(
                source_name=row.get("source_name"), source_asset_id=source_asset_id,
            ),
        })

    diagnostics = _derive_account_fact_diagnostics(
        equity_value, cash_value, normalized_positions, captured_at, decision_at
    )

    fact = {
        "contractVersion": SCHEMA_VERSION,
        "provider": provider,
        "accountScope": account_scope,
        "verificationStatus": required_verification_status,
        "accountIdentityHash": account_identity_hash,
        "currency": currency,
        "equity": equity_value,
        "cash": cash_value,
        "buyingPower": buying_power_value,
        "positions": normalized_positions,
        "positionCount": diagnostics["position_count"],
        "orderEligibilityStatus": order_eligibility_status,
        "navReconciliationStatus": diagnostics["nav_reconciliation_status"],
        "navReconciliationMismatchPct": diagnostics["nav_reconciliation_mismatch_pct"],
        "stalenessStatus": diagnostics["staleness_status"],
        "capturedAt": captured_at,
    }
    fact["factSha256"] = payload_sha256({key: value for key, value in fact.items() if key != "factSha256"})
    return fact


def validate_provider_account_fact_v2(fact: object, *, decision_at: str) -> dict:
    """Independent re-validation: re-derives every diagnostic field from
    the fact's own raw fields (never trusts a stored value) and recomputes
    the hash -- the same discipline v1's ``validate_snapshot()`` uses.
    """
    if not isinstance(fact, dict) or set(fact) != ACCOUNT_FACT_FIELDS:
        raise PortfolioAccountFactV2Error("ACCOUNT_FACT_FIELDS_INVALID")
    if fact.get("contractVersion") != SCHEMA_VERSION:
        raise PortfolioAccountFactV2Error("SCHEMA_VERSION_INVALID")

    provider = fact.get("provider")
    required_verification_status = PROVIDER_VERIFICATION_STATUS.get(provider)
    if required_verification_status is None:
        raise PortfolioAccountFactV2Error(f"PROVIDER_NOT_REGISTERED:{provider}")
    if fact.get("verificationStatus") != required_verification_status:
        raise PortfolioAccountFactV2Error("VERIFICATION_STATUS_INVALID")
    if fact.get("accountScope") not in CANONICAL_ACCOUNT_SCOPE:
        raise PortfolioAccountFactV2Error(f"ACCOUNT_SCOPE_NOT_RATIFIED:{fact.get('accountScope')}")
    _validate_account_identity_hash(fact.get("accountIdentityHash"))

    _enforce_pit_timing(
        label=f"{provider}.captured_at", event_at=fact.get("capturedAt"), decision_at=decision_at
    )

    positions = fact.get("positions")
    if not isinstance(positions, list):
        raise PortfolioAccountFactV2Error("POSITIONS_INVALID")
    seen_asset_ids: set[str] = set()
    for position in positions:
        if not isinstance(position, dict) or set(position) != POSITION_FIELDS:
            raise PortfolioAccountFactV2Error("POSITION_FIELDS_INVALID")
        forbidden = sorted(FORBIDDEN_POSITION_IDENTITY_CLAIMS & set(position))
        if forbidden:
            raise PortfolioAccountFactV2Error(f"POSITION_CANONICAL_IDENTITY_CLAIM_FORBIDDEN:{forbidden}")
        lineage = position.get("source_identity_lineage")
        if not isinstance(lineage, dict) or set(lineage) != {"contract_version", "status", "source_pairs"}:
            raise PortfolioAccountFactV2Error("POSITION_SOURCE_IDENTITY_LINEAGE_SCHEMA_INVALID")
        if lineage.get("contract_version") != POSITION_SOURCE_IDENTITY_CONTRACT_VERSION:
            raise PortfolioAccountFactV2Error("POSITION_SOURCE_IDENTITY_CONTRACT_VERSION_INVALID")
        if lineage.get("status") != "AVAILABLE":
            raise PortfolioAccountFactV2Error("POSITION_SOURCE_IDENTITY_NOT_AVAILABLE")
        pairs = lineage.get("source_pairs")
        if not isinstance(pairs, list) or len(pairs) != 1:
            raise PortfolioAccountFactV2Error("POSITION_SOURCE_IDENTITY_PAIRS_INVALID")
        pair = pairs[0]
        if not isinstance(pair, dict) or set(pair) != {"source_name", "source_asset_id"}:
            raise PortfolioAccountFactV2Error("POSITION_SOURCE_IDENTITY_PAIR_SCHEMA_INVALID")
        asset_id = pair.get("source_asset_id")
        if asset_id in seen_asset_ids:
            raise PortfolioAccountFactV2Error(f"DUPLICATE_POSITION_SOURCE_ASSET_ID:{asset_id}")
        seen_asset_ids.add(asset_id)

    recomputed = _derive_account_fact_diagnostics(
        fact.get("equity"), fact.get("cash"), positions, fact.get("capturedAt"), decision_at,
    )
    claimed = {
        "position_count": fact.get("positionCount"),
        "nav_reconciliation_status": fact.get("navReconciliationStatus"),
        "nav_reconciliation_mismatch_pct": fact.get("navReconciliationMismatchPct"),
        "staleness_status": fact.get("stalenessStatus"),
    }
    if recomputed != claimed:
        mismatched = sorted(key for key in recomputed if recomputed[key] != claimed.get(key))
        raise PortfolioAccountFactV2Error(f"FACT_DIAGNOSTIC_TAMPER_DETECTED:{mismatched}")

    claimed_hash = fact.get("factSha256")
    recomputed_hash = payload_sha256({key: value for key, value in fact.items() if key != "factSha256"})
    if claimed_hash != recomputed_hash:
        raise PortfolioAccountFactV2Error("FACT_HASH_MISMATCH")
    return dict(fact)
