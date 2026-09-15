#!/usr/bin/env python3
"""PAPER execution status vocabulary v1 and delay-loss rows.

Ratified inputs (via ``config/paper_execution_core_v1.json``):

* RULE.EXEC.DATA_FAILURE_PRIORITY.V1 (option C, record 3d07cbf1...): no PAPER
  fill from an unverified price; ordinary exits (release, time exit, drift)
  hold while STALE; risk reductions (STRESS, disaster stop, drawdown override,
  regime downgrade reduction) without a verified price are shown as
  '위험 평가·집행 불확실' (symbol, reason, start, elapsed) and execute at the
  symbol's first FRESH price without waiting for the next decision slot;
  market-blocked execution (limit-down lock, halt, VI single price) is
  '집행 불가(시장)'; delay loss (decision-time vs fill price) is recorded
  separately.  The alternative independent price path is not yet verified,
  so it is never accepted here (registry ``implementation_status``).
* RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1 (D9): monitoring gaps recorded
  separately ('감시 공백'); gap = no expected observation for more than 2x the
  monitoring interval (canon 7-3 item 4).
* RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1: a lapse from exceeding the
  maximum observation gap is not a release; hold, stop new buys, '판정 공백'.
  The gap fact itself comes from the exit policy layer (PR #756).

Pure and offline.
"""
from __future__ import annotations

import datetime as dt
from fractions import Fraction

try:
    from portfolio import paper_execution_core as CORE
except ImportError:  # pragma: no cover
    import paper_execution_core as CORE  # type: ignore


STATUS_ROW_SCHEMA_VERSION = "paper_execution_status_row/1"
DELAY_LOSS_SCHEMA_VERSION = "paper_delay_loss_row/1"
STATUS_CODES = ("RISK_EXECUTION_UNCERTAIN", "MARKET_EXECUTION_BLOCKED", "MONITORING_GAP", "JUDGMENT_GAP")
STATUS_RULES = {
    "RISK_EXECUTION_UNCERTAIN": "RULE.EXEC.DATA_FAILURE_PRIORITY.V1",
    "MARKET_EXECUTION_BLOCKED": "RULE.EXEC.DATA_FAILURE_PRIORITY.V1",
    "MONITORING_GAP": "RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1",
    "JUDGMENT_GAP": "RULE.ROTATION.INTERPRETATION_OBSERVATION_GAP.V1",
}
PRICE_STATUSES = ("FRESH", "STALE", "MISSING")
ORDINARY_EXIT_KINDS = ("ORDINARY", "RELEASE_FULL_SELL", "CRYPTO_TIME_STOP_21D", "DRIFT")


def _parse(ts: str) -> dt.datetime:
    return dt.datetime.strptime(CORE.require_utc(ts, "timestamp"), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def display_ko(core, code: str) -> str:
    if code not in STATUS_CODES:
        CORE.fail("STATUS_CODE_INVALID", str(code))
    return core.interpretations["status_display_ko"][code]


def classify_required_exit(core, *, exit_kind: str, price_status: str, market_block, market: str) -> dict:
    """What a required sell does now under D4 option C.

    ``exit_kind``: an ordinary exit (hold while STALE) or one of the registry
    ``risk_reduction_kinds``.  ``market_block``: null or a KR blocked condition.
    """
    CORE.require_market(market)
    risk_kinds = core.param("risk_reduction_kinds")
    if exit_kind not in ORDINARY_EXIT_KINDS and exit_kind not in risk_kinds:
        CORE.fail("EXIT_KIND_INVALID", str(exit_kind))
    if price_status not in PRICE_STATUSES:
        CORE.fail("PRICE_STATUS_INVALID", str(price_status))
    if core.param("alternative_price_path_status") != "ALTERNATIVE_PATH_NOT_YET_VERIFIED":
        CORE.fail("ALTERNATIVE_PRICE_PATH_STATUS_CHANGED_REVIEW_REQUIRED")
    if market_block is not None:
        allowed = core.interpretations["market_blocked_conditions"].get(market, [])
        if market_block not in allowed:
            CORE.fail("MARKET_BLOCK_CONDITION_INVALID", f"{market}:{market_block}")
        return {"action": "CARRY_TO_NEXT_EXECUTABLE_TIME", "status_code": "MARKET_EXECUTION_BLOCKED",
                "status_ko": display_ko(core, "MARKET_EXECUTION_BLOCKED"), "reason": market_block}
    if price_status == "FRESH":
        return {"action": "EXECUTE_AT_FIRST_ALLOWED_FILL", "status_code": None, "status_ko": None, "reason": None}
    if exit_kind in risk_kinds:
        return {"action": "EXECUTE_AT_FIRST_FRESH_PRICE_WITHOUT_WAITING_FOR_SLOT",
                "status_code": "RISK_EXECUTION_UNCERTAIN",
                "status_ko": display_ko(core, "RISK_EXECUTION_UNCERTAIN"),
                "reason": f"PRICE_{price_status}_NO_VERIFIED_ALTERNATIVE_PATH"}
    if core.param("ordinary_exits_behavior") != "HOLD_WHILE_STALE":
        CORE.fail("ORDINARY_EXIT_RULE_UNEXPECTED")
    return {"action": "HOLD_WHILE_STALE", "status_code": None, "status_ko": None,
            "reason": f"PRICE_{price_status}_ORDINARY_EXIT_HELD"}


def status_row(core, *, code: str, market: str, instrument: str, reason: str,
               started_at_utc: str, as_of_utc: str, resolved_at_utc=None) -> dict:
    """One portal chapter 04 status row (symbol, reason, start, elapsed)."""
    CORE.require_market(market)
    CORE.require_token(instrument, "instrument")
    CORE.require_token(reason, "reason")
    start, as_of = _parse(started_at_utc), _parse(as_of_utc)
    if as_of < start:
        CORE.fail("STATUS_AS_OF_BEFORE_START")
    end = as_of
    if resolved_at_utc is not None:
        end = _parse(resolved_at_utc)
        if not start <= end <= as_of:
            CORE.fail("STATUS_RESOLVED_OUTSIDE_WINDOW")
    if code == "JUDGMENT_GAP":
        handling = core.param("judgment_gap_handling")
        if handling.get("is_release") is not False or handling.get("display") != display_ko(core, code):
            CORE.fail("JUDGMENT_GAP_RULE_UNEXPECTED")
    row = {
        "schema_version": STATUS_ROW_SCHEMA_VERSION,
        "status_code": code,
        "status_ko": display_ko(core, code),
        "market": market,
        "instrument": instrument,
        "reason": reason,
        "started_at_utc": started_at_utc,
        "as_of_utc": as_of_utc,
        "resolved_at_utc": resolved_at_utc,
        "elapsed_seconds": int((end - start).total_seconds()),
        "rule_refs": core.rule_refs([(STATUS_RULES[code], "APPLIED")], as_of_utc),
    }
    return CORE.sign(row, "row_sha256")


def monitoring_gap(core, *, market: str, instrument: str, observation_times_utc: list,
                   monitoring_interval_seconds: int, as_of_utc: str) -> list:
    """Status rows for every stretch without an observation for > multiple x interval.

    The monitoring interval is an input (it is a collection fact, not a rule).
    An open gap at ``as_of_utc`` is reported unresolved.
    """
    if not isinstance(monitoring_interval_seconds, int) or isinstance(monitoring_interval_seconds, bool) \
            or monitoring_interval_seconds <= 0:
        CORE.fail("MONITORING_INTERVAL_INVALID")
    limit = CORE.frac(core.interpretations["monitoring_gap"]["interval_multiple"]) * monitoring_interval_seconds
    times = [_parse(t) for t in observation_times_utc]
    if times != sorted(times) or len(set(times)) != len(times):
        CORE.fail("OBSERVATION_TIMES_NOT_INCREASING")
    as_of = _parse(as_of_utc)
    if times and times[-1] > as_of:
        CORE.fail("OBSERVATION_AFTER_AS_OF")
    rows = []
    for previous, current in zip(times, times[1:]):
        if Fraction(int((current - previous).total_seconds())) > limit:
            rows.append(status_row(core, code="MONITORING_GAP", market=market, instrument=instrument,
                                   reason="NO_OBSERVATION_BEYOND_INTERVAL_MULTIPLE",
                                   started_at_utc=previous.strftime("%Y-%m-%dT%H:%M:%SZ"), as_of_utc=as_of_utc,
                                   resolved_at_utc=current.strftime("%Y-%m-%dT%H:%M:%SZ")))
    if times and Fraction(int((as_of - times[-1]).total_seconds())) > limit:
        rows.append(status_row(core, code="MONITORING_GAP", market=market, instrument=instrument,
                               reason="NO_OBSERVATION_BEYOND_INTERVAL_MULTIPLE",
                               started_at_utc=times[-1].strftime("%Y-%m-%dT%H:%M:%SZ"), as_of_utc=as_of_utc))
    return rows


def delay_loss_row(core, *, market: str, instrument: str, side: str, quantity, decision_at_utc: str,
                   decision_reference_price, decision_reference_kind: str, fill_at_utc: str, fill_price,
                   status_code=None) -> dict:
    """Decision-time reference vs fill price, recorded apart from P&L; never interpolated.

    Positive ``delay_loss`` = the delay cost money (sell filled lower / buy
    filled higher than the decision-time reference).  A missing reference
    leaves the loss null with a reason instead of an estimate.
    """
    CORE.require_market(market)
    CORE.require_token(instrument, "instrument")
    if side not in ("BUY", "SELL"):
        CORE.fail("SIDE_INVALID")
    if decision_reference_kind not in ("VERIFIED_AT_DECISION", "LAST_VERIFIED_BEFORE_DECISION", "NONE"):
        CORE.fail("REFERENCE_KIND_INVALID")
    if status_code is not None and status_code not in STATUS_CODES:
        CORE.fail("STATUS_CODE_INVALID")
    if fill_at_utc <= CORE.require_utc(decision_at_utc, "decision_at_utc"):
        CORE.fail("FILL_NOT_AFTER_DECISION")
    qty = CORE.frac(quantity, "quantity")
    fill = CORE.frac(fill_price, "fill_price")
    if qty <= 0 or fill <= 0:
        CORE.fail("DELAY_LOSS_NON_POSITIVE_INPUT")
    reference = None if decision_reference_kind == "NONE" else CORE.frac(decision_reference_price, "reference")
    if decision_reference_kind == "NONE" and decision_reference_price is not None:
        CORE.fail("REFERENCE_PRICE_WITHOUT_KIND")
    loss = None
    if reference is not None:
        loss = (reference - fill) * qty if side == "SELL" else (fill - reference) * qty
    row = {
        "schema_version": DELAY_LOSS_SCHEMA_VERSION,
        "market": market,
        "instrument": instrument,
        "side": side,
        "quantity": CORE.fstr(qty),
        "decision_at_utc": decision_at_utc,
        "decision_reference_price": CORE.opt_fstr(reference),
        "decision_reference_kind": decision_reference_kind,
        "fill_at_utc": CORE.require_utc(fill_at_utc, "fill_at_utc"),
        "fill_price": CORE.fstr(fill),
        "delay_loss": CORE.opt_fstr(loss),
        "delay_loss_reason": None if loss is not None else "NO_DECISION_TIME_REFERENCE_PRICE_NOT_INTERPOLATED",
        "status_code": status_code,
        "scorecard_separate": True,
        "rule_refs": core.rule_refs([("RULE.EXEC.DATA_FAILURE_PRIORITY.V1", "APPLIED")], fill_at_utc),
    }
    return CORE.sign(row, "row_sha256")
