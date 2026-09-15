#!/usr/bin/env python3
"""PAPER allocation envelope v1: market caps, reallocation, reductions, UNKNOWN and drawdown.

Ratified inputs (numbers resolved from ``config/rule_registry_v1.json`` via
``config/paper_execution_core_v1.json``):

* RULE.ALLOCATION.V2 (record 345801ab...): base US 0.40 / KR 0.35 /
  CRYPTO 0.15, max US 0.50 / KR 0.45 / CRYPTO 0.20, state multipliers
  RISK_ON 1.00 / NEUTRAL 0.70 / RISK_OFF 0.25 / STRESS 0.00 / UNKNOWN hold
  up to 0.50 with no new buys; released capital moves to RISK_ON markets up
  to their max, remainder cash; drawdown overrides -5% one step more
  defensive, -10% all markets RISK_OFF limits.
* RULE.EXEC.MULTI_MARKET_REALLOCATION.V1 (D6, record 10de02bf...): several
  receivers split by base ratio without exceeding each market max.
* RULE.EXEC.ALLOCATION_REDUCTION.V1 (D5): no sell for price-driven excess;
  downgrade reduction over 2 sessions; STRESS full at the first allowed fill
  time; order released -> lower rank -> pro rata; UNKNOWN cap from the 2nd
  consecutive UNKNOWN cycle.
* RULE.RISK.NAV_DRAWDOWN_LIFT.V1 (record 47276abe...): combined three-market
  NAV; -5% stage lifts within -2.5% of peak, -10% stage within -5%.
* RULE.EXEC.DATA_FAILURE_PRIORITY.V1 (record 3d07cbf1...): risk reductions
  without a verified price are '위험 평가·집행 불확실' and execute at the first
  FRESH price.

* RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1 (P5, record 2a94be2b...): KR
  inverse hedge stays off; KR STRESS reduction validated by fixed-input replay
  only (a KR STRESS input outside FIXED_INPUT_REPLAY fails closed for KR only).

CIO interpretations (config ``cio_interpretations``): D5-b split = half then
the rest (build plan section 9 note 1); inverse hedge instruments are not
long holdings (canon 5-4); no drawdown peak reset (build plan C19, section 9
note 5).  Undecided items are emitted as NOT_DEFINED (config ``not_defined``).

Pure and offline.
"""
from __future__ import annotations

from fractions import Fraction
import re

try:
    from portfolio import paper_execution_core as CORE
    from portfolio import paper_execution_status as STATUS
except ImportError:  # pragma: no cover - direct script execution
    import paper_execution_core as CORE  # type: ignore
    import paper_execution_status as STATUS  # type: ignore


ENVELOPE_SCHEMA_VERSION = "paper_allocation_envelope/1"
REDUCTION_SCHEMA_VERSION = "paper_allocation_reduction_plan/1"
DRAWDOWN_SCHEMA_VERSION = "paper_nav_drawdown_state/1"
STATES = ("RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS", "UNKNOWN")
KNOWN_BELOW_RISK_ON = ("NEUTRAL", "RISK_OFF", "STRESS")
STAGES = ("NONE", "MINUS_5", "MINUS_10")
TRIGGERS = ("PRICE_DRIFT_NO_STATE_CHANGE", "DOWNGRADE", "STRESS", "UNKNOWN_CAP", "DRAWDOWN_OVERRIDE")
# D4 risk-reduction vocabulary for each reduction trigger (registry risk_reduction_kinds).
TRIGGER_RISK_KIND = {
    "DOWNGRADE": "REGIME_DOWNGRADE_REDUCTION",
    "STRESS": "STRESS",
    "DRAWDOWN_OVERRIDE": "DRAWDOWN_OVERRIDE",
}
RULE_ALLOC = "RULE.ALLOCATION.V2"
RULE_D5 = "RULE.EXEC.ALLOCATION_REDUCTION.V1"
RULE_D6 = "RULE.EXEC.MULTI_MARKET_REALLOCATION.V1"
RULE_DD = "RULE.RISK.NAV_DRAWDOWN_LIFT.V1"
RULE_D4 = "RULE.EXEC.DATA_FAILURE_PRIORITY.V1"
RULE_PACE = "RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1"
RULE_KR_HEDGE = "RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1"
EVIDENCE_MODES = ("NATURAL", "FIXED_INPUT_REPLAY")
FAIL_CLOSED = "FAIL_CLOSED"
KR_STRESS_FAIL_CLOSED_FLAG = "KR_STRESS_CONDITION_UNRATIFIED_FIXED_INPUT_REPLAY_ONLY"


def _state_multiplier(core, state: str) -> Fraction:
    table = core.param("state_multipliers")
    if state == "UNKNOWN":
        spec = core.interpretations["unknown_hold_cap_multiplier"]
        match = re.fullmatch(spec["pattern"], table["UNKNOWN"])
        if match is None:
            CORE.fail("UNKNOWN_MULTIPLIER_UNPARSEABLE")
        return CORE.frac(match.group("multiplier"))
    return CORE.frac(table[state], f"multiplier.{state}")


def _inverse_hedge_status(core, market: str) -> dict:
    """Inverse hedge sizing is not part of this core; KR stays OFF by P5."""
    if market == "KR":
        if core.param("kr_inverse_hedge_interim") != "OFF":
            CORE.fail("KR_HEDGE_INTERIM_RULE_UNEXPECTED")
        return {"status": "OFF", "rule_id": RULE_KR_HEDGE, "until": "KR_STRESS_CONDITION_CONFIRMED"}
    return {"status": "NOT_EVALUATED_BY_THIS_CORE", "rule_id": "RULE.HEDGE.INVERSE.V1", "until": None}


def unknown_streak(cycle_states: list) -> int:
    """Consecutive trailing UNKNOWN finalized decision cycles (oldest first)."""
    if not isinstance(cycle_states, list):
        CORE.fail("CYCLE_STATES_INVALID")
    streak = 0
    for state in cycle_states:
        if state not in STATES:
            CORE.fail("STATE_INVALID", str(state))
        streak = streak + 1 if state == "UNKNOWN" else 0
    return streak


# ---------------------------------------------------------------------------
# Combined-NAV drawdown (RULE.RISK.NAV_DRAWDOWN_LIFT.V1), no peak reset
# ---------------------------------------------------------------------------

def drawdown_state(core, nav_series: list, *, decision_at_utc: str) -> dict:
    """Run the two independent stage latches over verified combined NAV.

    ``nav_series`` items: ``{"as_of_utc", "combined_nav_krw", "verified"}`` in
    time order.  Unverified observations never move the peak or a latch.
    There is deliberately no peak-reset input.
    """
    stage5 = core.param("drawdown_minus_5_stage")
    stage10 = core.param("drawdown_minus_10_stage")
    trig5, lift5 = CORE.frac(stage5["stage"]), CORE.frac(stage5["lifts_within_of_peak"])
    trig10, lift10 = CORE.frac(stage10["stage"]), CORE.frac(stage10["lifts_within_of_peak"])
    if not isinstance(nav_series, list):
        CORE.fail("NAV_SERIES_INVALID")
    peak = None
    latch5 = latch10 = False
    last_as_of = None
    flags = set()
    drawdown = None
    transitions = []
    for item in nav_series:
        if not isinstance(item, dict) or set(item) != {"as_of_utc", "combined_nav_krw", "verified"}:
            CORE.fail("NAV_OBSERVATION_FIELDS_INVALID")
        as_of = CORE.require_utc(item["as_of_utc"], "nav.as_of_utc")
        if last_as_of is not None and as_of <= last_as_of:
            CORE.fail("NAV_SERIES_NOT_INCREASING")
        if as_of > decision_at_utc:
            CORE.fail("NAV_OBSERVATION_AFTER_DECISION")
        last_as_of = as_of
        if item["verified"] is not True:
            flags.add("NAV_PARTIALLY_UNVERIFIED")
            continue
        nav = CORE.frac(item["combined_nav_krw"], "combined_nav_krw")
        if nav <= 0:
            CORE.fail("NAV_NOT_POSITIVE")
        peak = nav if peak is None or nav > peak else peak
        drawdown = nav / peak - 1
        before = (latch5, latch10)
        # Lift first, then trigger: at exactly -5% the -10% stage lifts and the
        # -5% stage is (still) triggered; "within" is inclusive.
        if latch10 and drawdown >= lift10:
            latch10 = False
        if latch5 and drawdown >= lift5:
            latch5 = False
        if drawdown <= trig10:
            latch10 = True
        if drawdown <= trig5:
            latch5 = True
        if (latch5, latch10) != before:
            transitions.append({"as_of_utc": as_of, "minus_5": latch5, "minus_10": latch10,
                                "drawdown": CORE.fstr(drawdown)})
    stage = "MINUS_10" if latch10 else "MINUS_5" if latch5 else "NONE"
    status = "KNOWN" if peak is not None else "UNKNOWN"
    record = {
        "schema_version": DRAWDOWN_SCHEMA_VERSION,
        "decision_at_utc": decision_at_utc,
        "nav_basis": core.param("drawdown_nav_basis"),
        "status": status,
        "peak_nav_krw": CORE.opt_fstr(peak),
        "last_drawdown": CORE.opt_fstr(drawdown),
        "stage": stage if status == "KNOWN" else "UNKNOWN",
        "latches": {"minus_5": latch5, "minus_10": latch10},
        "peak_reset_applied": False,
        "transitions": transitions,
        "flags": sorted(flags),
        "rule_refs": core.rule_refs([(RULE_DD, "APPLIED"), (RULE_ALLOC, "APPLIED")], decision_at_utc),
    }
    return CORE.sign(record, "record_sha256")


def effective_market_state(core, confirmed_state: str, drawdown_stage: str, streak: int) -> dict:
    """Confirmed state -> cap multiplier / new-buy permission after overrides."""
    if confirmed_state not in STATES:
        CORE.fail("STATE_INVALID", str(confirmed_state))
    if drawdown_stage not in STAGES + ("UNKNOWN",):
        CORE.fail("DRAWDOWN_STAGE_INVALID", str(drawdown_stage))
    if not isinstance(streak, int) or isinstance(streak, bool) or streak < 0:
        CORE.fail("UNKNOWN_STREAK_INVALID")
    if (confirmed_state == "UNKNOWN") != (streak > 0):
        CORE.fail("UNKNOWN_STREAK_STATE_MISMATCH")
    new_buys_table = core.param("new_buys_by_state")
    flags = []
    state = confirmed_state
    if drawdown_stage == "UNKNOWN":
        flags.append("DRAWDOWN_STAGE_UNKNOWN_NAV_UNVERIFIED")
    if drawdown_stage == "MINUS_5":
        ladder = core.interpretations["drawdown"]["minus_5_effect_ladder"]
        if state in ladder[:-1]:
            state = ladder[ladder.index(state) + 1]
        elif state in ("RISK_OFF", "UNKNOWN"):
            flags.append("NOT_DEFINED:DRAWDOWN_STEP_BEYOND_RISK_OFF_OR_UNKNOWN")
    cap_multiplier = None
    unknown_start = core.param("unknown_cap_start")["consecutive_unknown"]
    if state == "UNKNOWN":
        if streak >= unknown_start:
            cap_multiplier = _state_multiplier(core, "UNKNOWN")
            flags.append("UNKNOWN_CAP_ACTIVE")
        else:
            flags.append("UNKNOWN_FIRST_CYCLE_HOLD_NO_CAP")
    else:
        cap_multiplier = _state_multiplier(core, state)
    new_buys = new_buys_table[state]
    if drawdown_stage == "MINUS_10":
        risk_off = _state_multiplier(core, "RISK_OFF")
        cap_multiplier = risk_off if cap_multiplier is None else min(cap_multiplier, risk_off)
        if state in ("RISK_ON", "NEUTRAL"):
            state = "RISK_OFF"
        new_buys = new_buys_table["RISK_OFF"]
        flags.append("DRAWDOWN_MINUS_10_RISK_OFF_LIMITS")
    if drawdown_stage == "UNKNOWN":
        # Without a verified drawdown state no new buy is sized.
        new_buys = new_buys_table["UNKNOWN"]
    return {
        "confirmed_state": confirmed_state,
        "effective_state": state,
        "cap_multiplier": CORE.opt_fstr(cap_multiplier),
        "new_buys": new_buys,
        "flags": sorted(flags),
    }


def _kr_stress_fail_closed(core, market: str, state: str, evidence_mode: str) -> bool:
    """RULE.HEDGE.KR_STRESS_UNRATIFIED_INTERIM.V1 (P5): KR STRESS only in fixture replay.

    Outside FIXED_INPUT_REPLAY a KR STRESS input fails closed for KR only
    (no new buy, no cap, no reduction); US and crypto proceed.
    """
    if evidence_mode not in EVIDENCE_MODES:
        CORE.fail("EVIDENCE_MODE_INVALID", str(evidence_mode))
    if market != "KR" or state != "STRESS" or evidence_mode == "FIXED_INPUT_REPLAY":
        return False
    if core.param("kr_stress_reduction_validation") != "FIXED_INPUT_REPLAY_ONLY":
        CORE.fail("KR_STRESS_INTERIM_RULE_UNEXPECTED")
    return True


def allocation_envelope(
    core, *, decision_at_utc: str, market_states: dict, unknown_streaks: dict,
    drawdown_stage: str, cross_market_flow_validated: bool, evidence_mode: str,
) -> dict:
    """Per-market cap fractions of NAV0 for one decision cycle."""
    CORE.require_utc(decision_at_utc, "decision_at_utc")
    if set(market_states) != set(CORE.MARKETS) or set(unknown_streaks) != set(CORE.MARKETS):
        CORE.fail("ENVELOPE_MARKETS_INCOMPLETE")
    if cross_market_flow_validated not in (True, False):
        CORE.fail("FLOW_VALIDATION_FLAG_INVALID")
    base = {m: CORE.frac(core.param("base_allocation")[m]) for m in CORE.MARKETS}
    maximum = {m: CORE.frac(core.param("market_max_allocation")[m]) for m in CORE.MARKETS}
    effective = {}
    flags = set()
    for m in CORE.MARKETS:
        if _kr_stress_fail_closed(core, m, market_states[m], evidence_mode):
            effective[m] = {"confirmed_state": market_states[m], "effective_state": FAIL_CLOSED,
                            "cap_multiplier": None, "new_buys": core.param("new_buys_by_state")["STRESS"],
                            "flags": [KR_STRESS_FAIL_CLOSED_FLAG]}
            flags.add(KR_STRESS_FAIL_CLOSED_FLAG)
        else:
            effective[m] = effective_market_state(core, market_states[m], drawdown_stage, unknown_streaks[m])
    released = Fraction(0)
    for m in CORE.MARKETS:
        eff = effective[m]
        if eff["effective_state"] in KNOWN_BELOW_RISK_ON:
            released += base[m] * (1 - CORE.frac(eff["cap_multiplier"]))
        elif eff["effective_state"] == "UNKNOWN":
            flags.add("NOT_DEFINED:UNKNOWN_MARKET_RELEASED_CAPITAL")
    receivers = sorted(m for m in CORE.MARKETS if effective[m]["effective_state"] == "RISK_ON")
    extra = {m: Fraction(0) for m in CORE.MARKETS}
    reallocation_status = "NONE"
    if released > 0 and receivers:
        if cross_market_flow_validated:
            reallocation_status = "NOT_DEFINED"
            flags.add("NOT_DEFINED:FLOW_PRIORITY_METHOD")
        else:
            # Capped proportional split by base ratio; overflow of a capped
            # receiver is re-split among the others; the final remainder is cash.
            remaining = released
            active = list(receivers)
            while remaining > 0 and active:
                weight = sum(base[m] for m in active)
                capped = [m for m in active if maximum[m] - base[m] - extra[m] <= remaining * base[m] / weight]
                if not capped:
                    for m in active:
                        extra[m] += remaining * base[m] / weight
                    remaining = Fraction(0)
                    break
                for m in capped:
                    give = maximum[m] - base[m] - extra[m]
                    extra[m] += give
                    remaining -= give
                    active.remove(m)
            reallocation_status = "APPLIED_MULTI_RECEIVER" if len(receivers) > 1 else "APPLIED_SINGLE_RECEIVER"
    markets = {}
    for m in CORE.MARKETS:
        eff = effective[m]
        cap = None if eff["cap_multiplier"] is None else base[m] * CORE.frac(eff["cap_multiplier"]) + extra[m]
        markets[m] = {
            **eff,
            "base_fraction": CORE.fstr(base[m]),
            "max_fraction": CORE.fstr(maximum[m]),
            "reallocated_extra_fraction": CORE.fstr(extra[m]),
            "cap_fraction": CORE.opt_fstr(cap),
            "long_holdings_exclude_inverse_hedge": core.interpretations["inverse_hedge_excluded_from_long"]["value"],
            "inverse_hedge": _inverse_hedge_status(core, m),
        }
    pairs = [(RULE_ALLOC, "SIZED_BY"), (RULE_D5, "APPLIED"), (RULE_KR_HEDGE, "BLOCKED_BY")]
    if drawdown_stage != "NONE":
        pairs.append((RULE_DD, "APPLIED"))
    if reallocation_status == "APPLIED_MULTI_RECEIVER":
        pairs.append((RULE_D6, "SIZED_BY"))
    record = {
        "schema_version": ENVELOPE_SCHEMA_VERSION,
        "decision_at_utc": decision_at_utc,
        "inputs": {
            "market_states": dict(market_states), "unknown_streaks": dict(unknown_streaks),
            "drawdown_stage": drawdown_stage, "cross_market_flow_validated": cross_market_flow_validated,
            "evidence_mode": evidence_mode,
        },
        "markets": markets,
        "released_fraction": CORE.fstr(released),
        "reallocation_receivers": receivers,
        "reallocation_status": reallocation_status,
        "flags": sorted(flags),
        "config_sha256": core.config_sha256,
        "rule_refs": core.rule_refs(pairs, decision_at_utc),
    }
    return CORE.sign(record, "record_sha256")


def validate_envelope(record: dict, *, root=CORE.ROOT) -> dict:
    """Re-derive under the registry snapshot and config the record names."""
    CORE.verify_signed(record, "record_sha256", "ENVELOPE_SHA_MISMATCH")
    core = CORE.load_core_for_record(record, root)
    rebuilt = allocation_envelope(core, decision_at_utc=record["decision_at_utc"], **record["inputs"])
    if rebuilt != record:
        CORE.fail("ENVELOPE_NOT_REDERIVABLE")
    return rebuilt


# ---------------------------------------------------------------------------
# Reductions (D5) with D4 execution status
# ---------------------------------------------------------------------------

def _tiers(core, holdings: list) -> list:
    """D5-d order: released -> ranked (worst rank first, ties together) -> rest.

    The tier kinds are the registry ``reduction_order`` (checked at core load
    against config ``reduction.tier_order``).
    """
    released_kind, rank_kind, rest_kind = core.interpretations["reduction"]["tier_order"]
    released = [h for h in holdings if h["strength_state"] == "RELEASED"]
    ranked = [h for h in holdings if h["strength_state"] != "RELEASED" and h["rank"] is not None]
    rest = [h for h in holdings if h["strength_state"] != "RELEASED" and h["rank"] is None]
    tiers = [(released_kind, released)] if released else []
    for rank in sorted({h["rank"] for h in ranked}, reverse=True):
        tiers.append((f"{rank_kind}:{rank}", [h for h in ranked if h["rank"] == rank]))
    if rest:
        tiers.append((rest_kind, rest))
    return tiers


def _two_session_amount(core, progress, excess: Fraction) -> Fraction:
    """D5-b pace: half of the excess at the trigger in session 1, the remainder
    (bounded by the current excess, D5-a) in session 2.

    RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1 ratifies this same pace
    for the UNKNOWN cap and NAV drawdown overrides; its registry parameters must
    equal the downgrade rule's, so a drift fails closed instead of diverging.
    """
    sessions = core.param("downgrade_reduction")["sessions"]
    fraction = CORE.frac(core.interpretations["downgrade_split"]["fraction_session_1"])
    if "unknown_drawdown_reduction_pace" not in core.unavailable:
        pace = core.param("unknown_drawdown_reduction_pace")
        same_as = core.param("unknown_drawdown_reduction_same_as")
        if pace.get("sessions") != sessions or CORE.frac(pace.get("session_1_fraction_of_excess")) != fraction \
                or pace.get("session_2") != "REMAINDER" \
                or same_as != {"rule_id": RULE_D5, "key_parameter": "downgrade_reduction"}:
            CORE.fail("REDUCTION_PACE_DIFFERS_FROM_DOWNGRADE_PACE")
    progress = progress or {}
    if set(progress) != {"session_index", "excess_at_trigger_krw", "reduced_krw"} \
            or progress["session_index"] not in range(1, sessions + 1):
        CORE.fail("DOWNGRADE_PROGRESS_INVALID")
    at_trigger = CORE.frac(progress["excess_at_trigger_krw"])
    reduced = CORE.frac(progress["reduced_krw"])
    if progress["session_index"] == 1:
        if reduced != 0 or at_trigger != excess:
            CORE.fail("DOWNGRADE_SESSION_1_MUST_START_AT_CURRENT_EXCESS")
        return excess * fraction
    return max(Fraction(0), min(excess, at_trigger - reduced))


def reduction_plan(
    core, *, market: str, trigger: str, decision_at_utc: str, cap_krw, long_holdings: list,
    open_buy_orders: list, evidence_mode: str, downgrade_progress=None, market_blocked: dict | None = None,
) -> dict:
    """Which long value to sell now in one market, and under which status.

    ``long_holdings`` items: ``{"instrument", "value_krw", "price_status"
    (FRESH|STALE|MISSING), "strength_state" (HELD|RELEASED|UNKNOWN), "rank"
    (int, 1 = strongest, or null), "is_inverse_hedge"}``.
    ``downgrade_progress`` for DOWNGRADE: ``{"session_index": 1|2,
    "excess_at_trigger_krw", "reduced_krw"}``.
    """
    CORE.require_market(market)
    CORE.require_utc(decision_at_utc, "decision_at_utc")
    if trigger not in TRIGGERS:
        CORE.fail("REDUCTION_TRIGGER_INVALID", str(trigger))
    kr_fail_closed = _kr_stress_fail_closed(core, market, "STRESS" if trigger == "STRESS" else "", evidence_mode)
    market_blocked = market_blocked or {}
    cap = CORE.frac(cap_krw, "cap_krw")
    holdings = []
    seen = set()
    for item in long_holdings:
        if set(item) != {"instrument", "value_krw", "price_status", "strength_state", "rank", "is_inverse_hedge"}:
            CORE.fail("REDUCTION_HOLDING_FIELDS_INVALID")
        CORE.require_token(item["instrument"], "instrument")
        if item["instrument"] in seen:
            CORE.fail("REDUCTION_HOLDING_DUPLICATE", item["instrument"])
        seen.add(item["instrument"])
        if item["price_status"] not in ("FRESH", "STALE", "MISSING") \
                or item["strength_state"] not in ("HELD", "RELEASED", "UNKNOWN"):
            CORE.fail("REDUCTION_HOLDING_STATUS_INVALID", item["instrument"])
        if item["rank"] is not None and (not isinstance(item["rank"], int) or isinstance(item["rank"], bool) or item["rank"] < 1):
            CORE.fail("REDUCTION_RANK_INVALID", item["instrument"])
        if item["is_inverse_hedge"] is True:
            continue  # canon 5-4: inverse hedge instruments are not long holdings
        holdings.append({**item, "value": CORE.frac(item["value_krw"], "value_krw")})
    holdings.sort(key=lambda h: h["instrument"])
    long_total = sum((h["value"] for h in holdings), Fraction(0))
    excess = max(Fraction(0), long_total - cap)
    flags = []
    amount = None
    pace_used = False
    status = "PLANNED"
    obligation = excess > 0 or trigger == "STRESS"
    if kr_fail_closed:
        amount, status, obligation = None, FAIL_CLOSED, False
        flags.append(KR_STRESS_FAIL_CLOSED_FLAG)
    elif trigger == "PRICE_DRIFT_NO_STATE_CHANGE":
        if core.param("price_drift_over_cap") != "NO_SELL":
            CORE.fail("PRICE_DRIFT_RULE_UNEXPECTED")
        amount, status, obligation = Fraction(0), "NO_SELL_PRICE_DRIFT", False
    elif trigger == "STRESS":
        if core.param("stress_reduction") != "FULL_REDUCTION_AT_FIRST_ALLOWED_FILL":
            CORE.fail("STRESS_RULE_UNEXPECTED")
        if cap != 0:
            CORE.fail("STRESS_CAP_MUST_BE_ZERO")
        amount = long_total
    elif trigger == "DOWNGRADE":
        amount = _two_session_amount(core, downgrade_progress, excess)
    elif RULE_PACE in core.context.rules and market in core.context.rules[RULE_PACE]["markets"]:
        # UNKNOWN_CAP / DRAWDOWN_OVERRIDE: RULE.EXEC.REDUCTION_PACE_UNKNOWN_CAP_AND_DRAWDOWN.V1 ratifies
        # the D5-b downgrade pace (progress input shared with DOWNGRADE).
        if trigger not in core.param("unknown_drawdown_reduction_triggers"):
            CORE.fail("REDUCTION_PACE_TRIGGER_UNEXPECTED", trigger)
        amount = _two_session_amount(core, downgrade_progress, excess)
        pace_used = True
    else:
        pending = "UNKNOWN_CAP_REDUCTION_PACE" if trigger == "UNKNOWN_CAP" else "DRAWDOWN_OVERRIDE_REDUCTION_PACE"
        if pending not in core.not_defined_ids():
            CORE.fail("NOT_DEFINED_ITEM_MISSING", pending)
        status = "NOT_DEFINED"
        flags.append(f"NOT_DEFINED:{pending}")
    cancellations = []
    if obligation:
        for order in sorted(open_buy_orders, key=lambda o: o["order_id"]):
            if set(order) != {"order_id", "instrument", "reserved_krw"}:
                CORE.fail("OPEN_BUY_ORDER_FIELDS_INVALID")
            cancellations.append({"order_id": CORE.require_token(order["order_id"], "order_id"),
                                  "instrument": order["instrument"], "action": "CANCEL_BEFORE_REDUCTION"})
    lines = []
    risk_kind = TRIGGER_RISK_KIND.get(trigger)
    if amount is not None and amount > 0:
        needed = amount
        for tier_name, members in _tiers(core, holdings):
            if needed <= 0:
                break
            tier_total = sum(h["value"] for h in members)
            take = min(needed, tier_total)
            for h in members:
                sell = take * h["value"] / tier_total if tier_total else Fraction(0)
                if sell <= 0:
                    continue
                exec_status = STATUS.classify_required_exit(
                    core, exit_kind=risk_kind or "ORDINARY", price_status=h["price_status"],
                    market_block=market_blocked.get(h["instrument"]), market=market)
                lines.append({"instrument": h["instrument"], "tier": tier_name, "sell_value_krw": CORE.fstr(sell),
                              "full_position": sell == h["value"], "execution": exec_status})
            needed -= take
    pairs = [(RULE_D5, "EXITED_BY"), (RULE_ALLOC, "APPLIED"), (RULE_D4, "APPLIED")]
    if pace_used:
        pairs.append((RULE_PACE, "EXITED_BY"))
    record = {
        "schema_version": REDUCTION_SCHEMA_VERSION,
        "market": market,
        "trigger": trigger,
        "evidence_mode": evidence_mode,
        "decision_at_utc": decision_at_utc,
        "cap_krw": CORE.fstr(cap),
        "long_value_krw": CORE.fstr(long_total),
        "excess_krw": CORE.fstr(excess),
        "reduction_amount_krw": CORE.opt_fstr(amount),
        "status": status,
        "open_buy_cancellations": cancellations,
        "lines": lines,
        "order": core.param("reduction_order"),
        "flags": flags,
        "config_sha256": core.config_sha256,
        "rule_refs": core.rule_refs(pairs, decision_at_utc),
    }
    return CORE.sign(record, "record_sha256")
