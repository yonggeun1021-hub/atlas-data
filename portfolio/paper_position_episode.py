#!/usr/bin/env python3
"""PAPER position episodes v1, strength episode ids and re-entry judgment.

Ratified inputs (via ``config/paper_execution_core_v1.json``):

* RULE.EXEC.TOPUP_POSITION_LEVEL.V1 (D8, record 10de02bf...): top-ups are
  managed per position; the holding period counts from the first fill.
* RULE.EXEC.REENTRY.V1 (D7): no re-buy in the same decision packet after a
  sale; after a strength-release sell or the crypto 21-day time stop,
  re-entry only on a new strength confirmation; re-entry after an allocation
  reduction is allowed.

CIO interpretations (canon 6-1 R1/R2/R4/R5, 6-2): R1 key = (market, decision
packet id, instrument); strength episode id = (market, bucket, confirmation
date); a position episode opens on the first BUY fill from zero quantity,
keeps top-ups and partial fills, closes when quantity returns to zero;
realized P&L on average cost.  An exit reason outside the registered rules
makes the re-entry verdict UNKNOWN (fail closed), never guessed.

Pure and offline.
"""
from __future__ import annotations

import datetime as dt
from fractions import Fraction

try:
    from portfolio import paper_execution_core as CORE
except ImportError:  # pragma: no cover
    import paper_execution_core as CORE  # type: ignore


EPISODE_SCHEMA_VERSION = "paper_position_episode/1"
REENTRY_SCHEMA_VERSION = "paper_reentry_decision/1"
FILL_FIELDS = {"fill_id", "market", "instrument", "side", "filled_at_utc", "quantity", "price", "fee",
               "decision_packet_id", "exit_reason", "strength_episode_id"}
RULE_TOPUP = "RULE.EXEC.TOPUP_POSITION_LEVEL.V1"
RULE_REENTRY = "RULE.EXEC.REENTRY.V1"
# Exit reasons -> the re-entry clause of RULE.EXEC.REENTRY.V1 that governs them.
EXIT_REASON_RULE = {
    "RELEASE_FULL_SELL": "RULE.EXIT.RELEASE_FULL_SELL.V1",
    "CRYPTO_TIME_STOP_21D": "RULE.EXIT.CRYPTO_TIME_STOP_21D.V1",
    "ALLOCATION_REDUCTION": "RULE.EXEC.ALLOCATION_REDUCTION.V1",
}


def strength_episode_id(market: str, bucket_id: str, confirmed_on: str) -> str:
    """R2: one id per (market, sector/bucket, strength confirmation date)."""
    CORE.require_market(market)
    CORE.require_token(bucket_id, "bucket_id")
    dt.date.fromisoformat(confirmed_on)
    return "SE-" + CORE.payload_sha256({"market": market, "bucket_id": bucket_id, "confirmed_on": confirmed_on})


def _fills(fills: list) -> list:
    rows = []
    for fill in fills:
        if not isinstance(fill, dict) or set(fill) != FILL_FIELDS:
            CORE.fail("FILL_FIELDS_INVALID")
        CORE.require_token(fill["fill_id"], "fill_id")
        CORE.require_market(fill["market"])
        CORE.require_token(fill["instrument"], "instrument")
        CORE.require_utc(fill["filled_at_utc"], "filled_at_utc")
        CORE.require_token(fill["decision_packet_id"], "decision_packet_id")
        if fill["side"] not in ("BUY", "SELL"):
            CORE.fail("FILL_SIDE_INVALID")
        if fill["side"] == "BUY" and (fill["exit_reason"] is not None or not isinstance(fill["strength_episode_id"], str)):
            CORE.fail("BUY_FILL_NEEDS_STRENGTH_EPISODE_AND_NO_EXIT_REASON", fill["fill_id"])
        if fill["side"] == "SELL" and not isinstance(fill["exit_reason"], str):
            CORE.fail("SELL_FILL_NEEDS_EXIT_REASON", fill["fill_id"])
        qty, price, fee = (CORE.frac(fill[k], k) for k in ("quantity", "price", "fee"))
        if qty <= 0 or price <= 0 or fee < 0:
            CORE.fail("FILL_NUMBERS_INVALID", fill["fill_id"])
        rows.append({**fill, "qty": qty, "px": price, "fee_v": fee})
    ids = [r["fill_id"] for r in rows]
    if len(ids) != len(set(ids)):
        CORE.fail("FILL_ID_DUPLICATE")
    return sorted(rows, key=lambda r: (r["filled_at_utc"], r["fill_id"]))


def build_position_episodes(core, *, market: str, instrument: str, fills: list, as_of_utc: str) -> list:
    """Replay one instrument's fills into position episodes (D8)."""
    CORE.require_market(market)
    if core.param("topup_unit") != "POSITION_LEVEL" or core.param("holding_period_start") != "FIRST_FILL":
        CORE.fail("TOPUP_RULE_UNEXPECTED")
    episodes = []
    current = None
    for fill in _fills(fills):
        if fill["market"] != market or fill["instrument"] != instrument:
            CORE.fail("FILL_OTHER_INSTRUMENT", fill["fill_id"])
        if fill["filled_at_utc"] > as_of_utc:
            CORE.fail("FILL_AFTER_AS_OF", fill["fill_id"])
        if current is None:
            if fill["side"] == "SELL":
                CORE.fail("SELL_WITHOUT_OPEN_POSITION", fill["fill_id"])
            current = {"first": fill, "qty": Fraction(0), "cost": Fraction(0), "realized": Fraction(0),
                       "buys": [], "sells": [], "exit_reasons": []}
        if fill["side"] == "BUY":
            current["qty"] += fill["qty"]
            current["cost"] += fill["qty"] * fill["px"] + fill["fee_v"]
            current["buys"].append(fill["fill_id"])
        else:
            if fill["qty"] > current["qty"]:
                CORE.fail("SELL_EXCEEDS_POSITION", fill["fill_id"])
            average = current["cost"] / current["qty"]
            current["realized"] += fill["qty"] * (fill["px"] - average) - fill["fee_v"]
            current["cost"] -= average * fill["qty"]
            current["qty"] -= fill["qty"]
            current["sells"].append(fill["fill_id"])
            current["exit_reasons"].append(fill["exit_reason"])
        if current["qty"] == 0:
            episodes.append(_episode(core, market, instrument, current, fill, as_of_utc))
            current = None
    if current is not None:
        episodes.append(_episode(core, market, instrument, current, None, as_of_utc))
    return episodes


def _episode(core, market, instrument, state, closing_fill, as_of_utc) -> dict:
    first = state["first"]
    record = {
        "schema_version": EPISODE_SCHEMA_VERSION,
        "position_episode_id": "PE-" + CORE.payload_sha256({"market": market, "instrument": instrument,
                                                            "first_fill_id": first["fill_id"],
                                                            "first_fill_at_utc": first["filled_at_utc"]}),
        "market": market,
        "instrument": instrument,
        "status": "CLOSED" if closing_fill is not None else "OPEN",
        "holding_clock_start_utc": first["filled_at_utc"],
        "entry_strength_episode_id": first["strength_episode_id"],
        "entry_decision_packet_id": first["decision_packet_id"],
        "buy_fill_ids": state["buys"],
        "sell_fill_ids": state["sells"],
        "top_up_count": len(state["buys"]) - 1,
        "open_quantity": CORE.fstr(state["qty"]),
        "average_cost_open": None if state["qty"] == 0 else CORE.fstr(state["cost"] / state["qty"]),
        "realized_pnl_net_fees": CORE.fstr(state["realized"]),
        "closed_at_utc": None if closing_fill is None else closing_fill["filled_at_utc"],
        "closing_exit_reason": None if closing_fill is None else closing_fill["exit_reason"],
        "closing_decision_packet_id": None if closing_fill is None else closing_fill["decision_packet_id"],
        "as_of_utc": as_of_utc,
        "rule_refs": core.rule_refs([(RULE_TOPUP, "APPLIED")], as_of_utc),
    }
    return CORE.sign(record, "record_sha256")


def reentry_decision(core, *, market: str, instrument: str, decision_packet_id: str, decision_at_utc: str,
                     current_strength_episode: dict | None, fills: list) -> dict:
    """May this instrument be bought in this decision packet (D7 / R1)?

    ``current_strength_episode``: null or ``{"strength_episode_id",
    "confirmation_available_at_utc"}`` for the bucket that makes the
    instrument eligible now.
    """
    CORE.require_token(decision_packet_id, "decision_packet_id")
    same_packet = core.param("reentry_same_packet")
    after_exit = core.param("reentry_after_release_or_time_stop")
    after_reduction = core.param("reentry_after_allocation_reduction")
    if same_packet != "NO_REBUY_IN_SAME_DECISION_PACKET" or after_reduction != "ALLOWED" \
            or after_exit.get("requires") != "NEW_STRONG_CONFIRMATION":
        CORE.fail("REENTRY_RULE_UNEXPECTED")
    rows = _fills(fills)
    if any(r["filled_at_utc"] > decision_at_utc for r in rows):
        CORE.fail("FILL_AFTER_DECISION")
    verdict, reason, pairs = "ALLOWED", "NO_PRIOR_EXIT", [(RULE_REENTRY, "APPLIED")]
    if any(r["side"] == "SELL" and r["decision_packet_id"] == decision_packet_id for r in rows):
        verdict, reason = "DENIED", "R1_SOLD_IN_SAME_DECISION_PACKET"
    else:
        episodes = build_position_episodes(core, market=market, instrument=instrument, fills=fills,
                                           as_of_utc=decision_at_utc)
        closed = [e for e in episodes if e["status"] == "CLOSED"]
        if episodes and episodes[-1]["status"] == "OPEN":
            reason = "OPEN_POSITION_TOP_UP_NOT_REENTRY"
        elif closed:
            last = closed[-1]
            exit_rule = EXIT_REASON_RULE.get(last["closing_exit_reason"])
            if exit_rule is None:
                verdict, reason = "UNKNOWN", f"EXIT_REASON_NOT_REGISTERED:{last['closing_exit_reason']}"
            elif exit_rule in after_exit["after"]:
                new = current_strength_episode
                if new is None:
                    verdict, reason = "DENIED", "NO_CURRENT_STRENGTH_EPISODE"
                elif new["strength_episode_id"] == last["entry_strength_episode_id"] \
                        or CORE.require_utc(new["confirmation_available_at_utc"], "confirmation") <= last["closed_at_utc"]:
                    verdict, reason = "DENIED", "REENTRY_REQUIRES_NEW_STRONG_CONFIRMATION_AFTER_EXIT"
                else:
                    reason = "NEW_STRONG_CONFIRMATION_AFTER_EXIT"
            else:
                reason = "AFTER_ALLOCATION_REDUCTION_ALLOWED"
    if verdict != "ALLOWED":
        pairs = [(RULE_REENTRY, "BLOCKED_BY")]
    record = {
        "schema_version": REENTRY_SCHEMA_VERSION,
        "key": {"market": market, "decision_packet_id": decision_packet_id, "instrument": instrument},
        "decision_at_utc": decision_at_utc,
        "current_strength_episode": current_strength_episode,
        "verdict": verdict,
        "reason": reason,
        "fills_sha256": CORE.payload_sha256(sorted(fills, key=lambda f: f["fill_id"])),
        "rule_refs": core.rule_refs(pairs, decision_at_utc),
    }
    return CORE.sign(record, "record_sha256")
