#!/usr/bin/env python3
"""Per-market stale-evidence HOLD and alert signal for held Crypto PAPER positions.

Ratified rule (``CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914``): a held
position in a market whose realtime freshness is not FRESH gets **no PAPER
exit execution** until fresh data returns; the runtime records a per-market
HOLD with its reason, and raises an alert once the position has stayed stale
for more than 30 minutes.  The 30 minutes is an engineering alert budget, not
a policy threshold, and it never changes an exit or entry outcome.

Inputs are one ``crypto_paper_decision_snapshot_packet/3`` or ``/4`` (per-market
freshness recorded for every subscribed market; ``/4`` keeps the ``/3``
per-market layout; an issued ``/2`` packet falls
back to its candidate rows), the caller's list of held markets (market codes only -- no
quantity, price, fee, or P&L ever enters this module), and the previous
state this function returned.  The private runtime owns the held-market list
and persists the returned state; this module is pure and offline and holds
no authority: it never builds, submits or cancels an order.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_VERSION = "crypto_paper_stale_hold_state/1"
EVALUATE = "EVALUATE_EXIT_WITH_FRESH_EVIDENCE"
HOLD = "HOLD_NO_PAPER_EXIT_EXECUTION"
ALERT_CODE = "HELD_POSITION_REALTIME_NOT_FRESH_BEYOND_ALERT_BUDGET"
MARKET_RE = re.compile(r"^KRW-[A-Z0-9]{1,20}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AUTHORITY = {
    "paper_exit_execution_authorized": False,
    "exit_order_authorized": False,
    "entry_order_authorized": False,
    "exchange_order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}
STATE_FIELDS = {
    "schema_version", "policy", "decision_generation_id", "decision_payload_sha256",
    "observed_at", "held_markets", "markets", "alerts", "prior_state_sha256",
    "authority", "packet_sha256",
}


class CryptoPaperStaleHoldError(ValueError):
    """Fail-closed stale-hold input or derivation violation."""


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    if spec is None or spec.loader is None:
        raise CryptoPaperStaleHoldError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


DECISION = _load("crypto_paper_stale_hold_decision", "decision/crypto_paper_decision_snapshot.py")
PER_MARKET = DECISION.PER_MARKET


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _utc(value, code: str) -> dt.datetime:
    try:
        return PER_MARKET.parse_utc(value, code)
    except PER_MARKET.CryptoRealtimePerMarketPolicyError as exc:
        raise CryptoPaperStaleHoldError(code) from exc


def _stamp(value: dt.datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _checked_decision(decision_packet: dict, *, revalidate: bool) -> dict:
    if not isinstance(decision_packet, dict):
        raise CryptoPaperStaleHoldError("DECISION_PACKET_INVALID")
    if decision_packet.get("schema_version") not in DECISION.PER_MARKET_LAYOUT_SCHEMA_VERSIONS:
        raise CryptoPaperStaleHoldError("DECISION_PACKET_NOT_PER_MARKET_SCHEMA")
    if revalidate:
        try:
            return DECISION.validate_output(copy.deepcopy(decision_packet))
        except DECISION.CryptoPaperDecisionSnapshotError as exc:
            raise CryptoPaperStaleHoldError(f"DECISION_PACKET_REDERIVATION_FAILED:{exc}") from exc
    unsigned = copy.deepcopy(decision_packet)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or payload_sha256(unsigned) != claimed:
        raise CryptoPaperStaleHoldError("DECISION_PACKET_HASH_MISMATCH")
    rows = decision_packet.get("candidates")
    subscribed = (decision_packet.get("realtime_per_market_freshness") or {}).get("subscribed_market_realtime")
    if decision_packet["schema_version"] == DECISION.PER_MARKET_V2_OUTPUT_SCHEMA_VERSION and subscribed is None:
        subscribed = {}  # issued /2 layout: candidate rows only
    if not isinstance(rows, list) or any(
        not isinstance(row, dict) or not isinstance(row.get("realtime_freshness"), dict)
        for row in rows
    ) or not isinstance(subscribed, dict) or any(
        not isinstance(row, dict) or not isinstance(row.get("status"), str)
        for row in subscribed.values()
    ):
        raise CryptoPaperStaleHoldError("DECISION_PACKET_PER_MARKET_FIELDS_MISSING")
    return copy.deepcopy(decision_packet)


def _checked_held_markets(held_markets) -> list[str]:
    if not isinstance(held_markets, (list, tuple)):
        raise CryptoPaperStaleHoldError("HELD_MARKETS_INVALID")
    markets = list(held_markets)
    if any(not isinstance(market, str) or MARKET_RE.fullmatch(market) is None for market in markets):
        raise CryptoPaperStaleHoldError("HELD_MARKET_CODE_INVALID")
    if len(set(markets)) != len(markets):
        raise CryptoPaperStaleHoldError("HELD_MARKET_DUPLICATE")
    return sorted(markets)


def _checked_prior_state(prior_state: dict | None, observed_at: dt.datetime, policy_ref: dict) -> dict:
    if prior_state is None:
        return {}
    if not isinstance(prior_state, dict) or set(prior_state) != STATE_FIELDS:
        raise CryptoPaperStaleHoldError("PRIOR_STATE_FIELDS_INVALID")
    unsigned = copy.deepcopy(prior_state)
    claimed = unsigned.pop("packet_sha256")
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None or payload_sha256(unsigned) != claimed:
        raise CryptoPaperStaleHoldError("PRIOR_STATE_HASH_MISMATCH")
    if prior_state["schema_version"] != SCHEMA_VERSION or prior_state["policy"] != policy_ref:
        raise CryptoPaperStaleHoldError("PRIOR_STATE_IDENTITY_MISMATCH")
    if prior_state["authority"] != AUTHORITY:
        raise CryptoPaperStaleHoldError("PRIOR_STATE_AUTHORITY_INVALID")
    if _utc(prior_state["observed_at"], "PRIOR_STATE_OBSERVED_AT_INVALID") >= observed_at:
        raise CryptoPaperStaleHoldError("PRIOR_STATE_NOT_BEFORE_DECISION")
    by_market = {}
    for row in prior_state["markets"]:
        if not isinstance(row, dict) or row.get("market") in by_market:
            raise CryptoPaperStaleHoldError("PRIOR_STATE_MARKETS_INVALID")
        by_market[row["market"]] = row
    return by_market


def evaluate_stale_holds(
    decision_packet: dict, *, held_markets, prior_state: dict | None = None,
    revalidate_decision: bool = True, policy: dict | None = None,
) -> dict:
    policy = PER_MARKET.load_policy() if policy is None else policy
    policy_ref = PER_MARKET.policy_reference(policy)
    alert_after = dt.timedelta(minutes=policy["stale_held_position"]["alert_after_stale_minutes"])
    decision = _checked_decision(decision_packet, revalidate=revalidate_decision)
    observed_at = _utc(decision.get("generated_at"), "DECISION_GENERATED_AT_INVALID")
    held = _checked_held_markets(held_markets)
    prior = _checked_prior_state(prior_state, observed_at, policy_ref)
    candidates = {row["market"]: row for row in decision["candidates"]}
    subscribed = {
        row["market"]: row["realtime_freshness"] for row in decision["candidates"]
    }
    subscribed.update(
        decision["realtime_per_market_freshness"].get("subscribed_market_realtime") or {}
    )

    markets = []
    alerts = []
    for market in held:
        candidate = candidates.get(market)
        # Exit freshness comes from the per-market realtime status recorded
        # for every subscribed market, so a held market that is not (or no
        # longer) a candidate still exits normally once its data is FRESH.
        realtime = subscribed.get(market)
        if realtime is None:
            realtime_status = DECISION.MISSING
            realtime_reasons = ["HELD_MARKET_NOT_SUBSCRIBED_IN_PUBLIC_REALTIME_CAPTURE"]
        else:
            realtime_status = realtime["status"]
            realtime_reasons = list(realtime.get("reasons") or [])
        floor_status = (
            (candidate.get("realtime_liquidity_floor") or {}).get("status")
            if candidate is not None else None
        )
        if realtime_status == DECISION.FRESH:
            markets.append({
                "market": market,
                "realtime_status": realtime_status,
                "liquidity_floor_status": floor_status,
                "exit_execution": EVALUATE,
                "hold_reason": None,
                "stale_since": None,
                "stale_seconds": None,
                "alert": False,
            })
            continue
        previous = prior.get(market)
        if previous is not None and previous.get("exit_execution") == HOLD:
            stale_since = _utc(previous.get("stale_since"), "PRIOR_STATE_STALE_SINCE_INVALID")
            if stale_since > observed_at:
                raise CryptoPaperStaleHoldError("PRIOR_STATE_STALE_SINCE_FUTURE")
        else:
            stale_since = observed_at
        stale_seconds = int((observed_at - stale_since).total_seconds())
        alert = (observed_at - stale_since) > alert_after
        hold_reason = f"REALTIME_{realtime_status}:{market}"
        if realtime_reasons:
            hold_reason += ":" + ",".join(realtime_reasons)
        markets.append({
            "market": market,
            "realtime_status": realtime_status,
            "liquidity_floor_status": floor_status,
            "exit_execution": HOLD,
            "hold_reason": hold_reason,
            "stale_since": _stamp(stale_since),
            "stale_seconds": stale_seconds,
            "alert": alert,
        })
        if alert:
            alerts.append({
                "code": ALERT_CODE,
                "market": market,
                "realtime_status": realtime_status,
                "stale_since": _stamp(stale_since),
                "stale_seconds": stale_seconds,
                "alert_after_minutes": policy["stale_held_position"]["alert_after_stale_minutes"],
                "alert_budget_kind": policy["stale_held_position"]["alert_budget_kind"],
            })

    state = {
        "schema_version": SCHEMA_VERSION,
        "policy": policy_ref,
        "decision_generation_id": decision["generation_id"],
        "decision_payload_sha256": decision["payload_sha256"],
        "observed_at": _stamp(observed_at),
        "held_markets": held,
        "markets": markets,
        "alerts": alerts,
        "prior_state_sha256": prior_state["packet_sha256"] if prior_state is not None else None,
        "authority": copy.deepcopy(AUTHORITY),
    }
    state["packet_sha256"] = payload_sha256(state)
    return state


def validate_state(
    state: dict, *, decision_packet: dict, held_markets, prior_state: dict | None = None,
    revalidate_decision: bool = True,
) -> dict:
    """Rebuild the state from its exact inputs; any drift fails closed."""
    rebuilt = evaluate_stale_holds(
        decision_packet, held_markets=held_markets, prior_state=prior_state,
        revalidate_decision=revalidate_decision,
    )
    if canonical_json(rebuilt) != canonical_json(state):
        raise CryptoPaperStaleHoldError("STATE_DERIVATION_MISMATCH")
    return copy.deepcopy(state)


def exit_observation_freshness(state: dict, market: str) -> str:
    """Freshness value for ``portfolio/crypto_paper_exit_manager.py`` observations.

    Only a market this state marks EVALUATE may carry FRESH; STALE stays
    STALE and every other non-FRESH status (MISSING, UNKNOWN,
    MIXED_GENERATION, not held) is UNKNOWN -- both of which the exit manager
    already maps to ``WAIT_STALE_EVIDENCE`` with no action.
    """
    rows = [row for row in (state or {}).get("markets", []) if row.get("market") == market]
    if len(rows) != 1:
        return "UNKNOWN"
    row = rows[0]
    if row["exit_execution"] == EVALUATE and row["realtime_status"] == DECISION.FRESH:
        return "FRESH"
    return "STALE" if row["realtime_status"] == DECISION.STALE else "UNKNOWN"
