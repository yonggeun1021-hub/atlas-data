#!/usr/bin/env python3
"""Record-only PAPER shadow exit controls (RULE.EXIT.SHADOW_CONTROLS.V1) and the D9 stop fill model.

Ratification ``USER_RATIFICATION_PAPER_EXIT_PROVISIONAL_V1_20260915`` (sha
47276abe...): no disaster stop and no partial take-profit in the defaults;
for every entry record the shadow results of 1-B (release + lagging), a
14-day time stop, partial TP 1R and a 5xATR disaster stop. Nothing here sells,
blocks or sizes anything: every row is a counterfactual record for the rule
scorecard.

Definitions are the exit study v2 ones (study sha 2f8e4b39..., pre-registration
sha 3b167792..., section 5), with no number added:

* units -- ATR = Wilder ATR14 of the first-entry decision day (daily UTC bars
  completed by the decision time); R_ref = 3 x ATR; levels are fixed at the
  first fill price P1 and never changed by top-ups;
* ``1-B`` (study ``LAG``) -- default exits + sell all when the position's
  bucket reads LAGGING (RRG N30/M7, the rotation packet's ``lagging`` field) at
  a packet after entry; BTC is the benchmark, so lagging is not defined there;
* ``TS14`` -- default exits + sell all at the first FRESH decision snapshot at
  or after first fill + 14 x 24h;
* ``PTP1`` -- default exits + resting limit sell of 50% at P1 + 1 x R_ref; no
  stop, no breakeven move, no top-ups counted after the TP fill;
* ``DS5`` -- default exits + monitored stop at P1 - 5 x ATR with the ratified
  D9 fill model (RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1): trigger only on a
  price observed after the entry fill; fill = the first allowed (FRESH) price
  observed after the trigger is known, never the stop price retroactively; a
  gap down fills at that open; monitoring gaps (no observation for more than 2
  expected intervals, canon 7-3) are recorded separately.

"Default exits" are the ratified defaults as actually executed for the
position (release full sell, crypto 21-day time stop). A component that has
not fired before the first default exit fill leaves the shadow identical to
the default.

KR / US units (RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1, user ratification
``USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915`` sha 2a94be2b..., P3):
1-B is replaced by release-only (identical to the default release sell) until
a lagging definition is confirmed; the time stop is 14 trading days (caller-
supplied official session calendar); partial take-profit 1R with R = 3 x daily
ATR14; disaster stop 5 x daily ATR14; record-only. KR/US prices count only
inside the D1 fill windows of OPEN sessions, and monitoring gaps are measured
inside those windows. CIO interpretations are listed in the config
(``kr_us_units.cio_interpretations``).
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import importlib.util
from pathlib import Path
from typing import Optional


HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
START_SCHEMA_VERSION = "paper_shadow_control_start/1"
RESULT_SCHEMA_VERSION = "paper_shadow_control_result/1"
ATR_SCHEMA_VERSION = "paper_shadow_atr14/1"
CONTROL_IDS = ("1-B", "TS14", "PTP1", "DS5")
QUANT = Decimal("0.000000000001")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


EXIT = _load_module("atlas_paper_exit_policy_v1_for_shadow_controls", HERE / "paper_exit_policy_v1.py")
PaperShadowControlError = EXIT.PaperExitPolicyError
_fail = EXIT._fail
parse_utc = EXIT.parse_utc
stamp = EXIT.stamp
parse_decimal = EXIT.parse_decimal
decimal_text = EXIT.decimal_text


def _q(value: Decimal) -> str:
    return decimal_text(value.quantize(QUANT))


def _shadow_rule(policy: dict) -> dict:
    return policy["config"]["rules"]["shadow_controls"]


def _refs(policy: dict, *extra: tuple) -> list:
    refs = [EXIT.rule_ref(policy, "shadow_controls", "APPLIED")]
    refs += [EXIT.rule_ref(policy, key, role) for key, role in extra]
    return EXIT._sorted_refs(refs, policy)


# ---------------------------------------------------------------------------
# ATR14 (Wilder) from completed daily bars only
# ---------------------------------------------------------------------------

def wilder_atr14(policy: dict, daily_bars: list, decision_at: str) -> dict:
    """Wilder ATR14 as in the study build: TR of the first bar = high - low,
    seed = mean of the first 14 TRs, then ATR = (ATR x 13 + TR) / 14.

    Only bars whose ``close_at`` is not after ``decision_at`` are used; later
    bars are counted and ignored (no lookahead).
    """
    units = _shadow_rule(policy)["crypto_units"]
    period = units["atr_period"]
    cutoff = parse_utc(decision_at, "ATR_DECISION_AT_INVALID")
    used, excluded, previous_date = [], 0, None
    for bar in daily_bars or []:
        day = EXIT.parse_date(bar.get("bar_date"), "ATR_BAR_DATE_INVALID")
        if previous_date is not None and day <= previous_date:
            _fail("ATR_BARS_NOT_STRICTLY_ASCENDING", bar.get("bar_date"))
        previous_date = day
        close_at = parse_utc(bar.get("close_at"), "ATR_BAR_CLOSE_AT_INVALID")
        if close_at > cutoff:
            excluded += 1
            continue
        high = parse_decimal(bar.get("high"), "ATR_BAR_HIGH_INVALID", positive=True)
        low = parse_decimal(bar.get("low"), "ATR_BAR_LOW_INVALID", positive=True)
        close = parse_decimal(bar.get("close"), "ATR_BAR_CLOSE_INVALID", positive=True)
        if low > high or not low <= close <= high:
            _fail("ATR_BAR_RANGE_INVALID", bar.get("bar_date"))
        used.append({"bar_date": day.isoformat(), "close_at": stamp(close_at), "high": decimal_text(high),
                     "low": decimal_text(low), "close": decimal_text(close)})
    base = {
        "schema_version": ATR_SCHEMA_VERSION,
        "method": units["atr_method"],
        "period": period,
        "decision_at": stamp(cutoff),
        "bars_used": len(used),
        "bars_excluded_after_decision": excluded,
        "first_bar_date": used[0]["bar_date"] if used else None,
        "as_of_bar_date": used[-1]["bar_date"] if used else None,
        "bars_sha256": EXIT.payload_sha256(used),
    }
    if len(used) < period:
        return EXIT.with_payload_sha(base | {"status": "UNKNOWN", "reason": "INSUFFICIENT_COMPLETED_DAILY_BARS", "atr14": None})
    atr, trs, prev_close = None, [], None
    for bar in used:
        high, low, close = Decimal(bar["high"]), Decimal(bar["low"]), Decimal(bar["close"])
        tr = high - low if prev_close is None else max(high - low, abs(high - prev_close), abs(low - prev_close))
        prev_close = close
        if atr is None:
            trs.append(tr)
            if len(trs) == period:
                atr = sum(trs) / Decimal(period)
        else:
            atr = (atr * Decimal(period - 1) + tr) / Decimal(period)
    return EXIT.with_payload_sha(base | {"status": "OBSERVED", "reason": None, "atr14": _q(atr)})


# ---------------------------------------------------------------------------
# Start rows (at entry)
# ---------------------------------------------------------------------------

def start_shadow_controls(policy: dict, position: dict, *, first_fill_price: str, entry_decision_at: str,
                          atr: Optional[dict] = None) -> dict:
    """Shadow start row for one position episode (recorded at the first fill)."""
    position = EXIT.validate_position(position)
    rule = _shadow_rule(policy)
    market = position["market"]
    p1 = parse_decimal(first_fill_price, "FIRST_FILL_PRICE_INVALID", positive=True)
    first_fill = parse_utc(position["first_fill_at"])
    decision = parse_utc(entry_decision_at, "ENTRY_DECISION_AT_INVALID")
    if decision > first_fill:
        _fail("ENTRY_DECISION_AFTER_FIRST_FILL")
    controls = {}
    cfg = rule["controls"]
    kr_us = rule["kr_us_units"]
    units_ratification = None
    if market != "CRYPTO":
        units_ratification = {
            "rule_id": kr_us["rule_id"],
            "record_id": policy["config"]["records"][kr_us["record"]]["record_id"],
            "sha256": policy["config"]["records"][kr_us["record"]]["sha256"],
            "registry_status": kr_us["registry_status"],
        }
        controls["TS14"] = {"control_id": "TS14", "status": "DEFINED", "reason": None,
                            "levels": {"trading_days": kr_us["TS14"]["trading_days"], "calendar": kr_us["TS14"]["calendar"]}}
        controls["1-B"] = {"control_id": "1-B", "status": "DEFINED_EQUALS_DEFAULT_RELEASE_ONLY",
                           "reason": "KR_US_1B_RELEASE_ONLY_UNTIL_LAGGING_DEFINITION_CONFIRMED",
                           "levels": {"component": kr_us["1-B"]["component"]}}
        r_ref_multiple, ptp_multiple, ds_multiple = kr_us["PTP1"]["r_ref_atr_multiple"], kr_us["PTP1"]["r_multiple"], kr_us["DS5"]["atr_multiple"]
    else:
        units = rule["crypto_units"]
        ts_deadline = first_fill + dt.timedelta(hours=cfg["TS14"]["days"] * cfg["TS14"]["day_length_hours"])
        controls["TS14"] = {"control_id": "TS14", "status": "DEFINED", "reason": None,
                            "levels": {"deadline_at": stamp(ts_deadline)}}
        bucket_is_benchmark = position["rotation_entity_id"] in policy["rotation_policy"]["markets"]["CRYPTO"]["lagging_warning"]["excluded_entities"]
        controls["1-B"] = {
            "control_id": "1-B",
            "status": "DEFINED_EQUALS_DEFAULT_BENCHMARK_BUCKET" if bucket_is_benchmark else "DEFINED",
            "reason": "LAGGING_NOT_DEFINED_FOR_BENCHMARK_BUCKET" if bucket_is_benchmark else None,
            "levels": {"lagging_source": cfg["1-B"]["lagging_source"], "entity_id": position["rotation_entity_id"]},
        }
        r_ref_multiple, ptp_multiple, ds_multiple = units["r_ref_atr_multiple"], cfg["PTP1"]["r_multiple"], cfg["DS5"]["atr_multiple"]
    if atr is None:
        atr_block = None
        for cid in ("PTP1", "DS5"):
            controls[cid] = {"control_id": cid, "status": "UNKNOWN", "reason": "ATR14_NOT_SUPPLIED", "levels": None}
    else:
        atr = EXIT.verify_payload_sha(atr, "ATR_RECORD_SHA_MISMATCH")
        if parse_utc(atr["decision_at"]) != decision:
            _fail("ATR_DECISION_TIME_MISMATCH")
        atr_block = copy.deepcopy(atr)
        if atr["status"] != "OBSERVED":
            for cid in ("PTP1", "DS5"):
                controls[cid] = {"control_id": cid, "status": "UNKNOWN", "reason": f"ATR14_{atr['reason']}", "levels": None}
        else:
            value = parse_decimal(atr["atr14"], "ATR14_INVALID", positive=True)
            r_ref = Decimal(r_ref_multiple) * value
            ptp_level = p1 + Decimal(ptp_multiple) * r_ref
            ds_level = p1 - Decimal(ds_multiple) * value
            controls["PTP1"] = {"control_id": "PTP1", "status": "DEFINED", "reason": None, "levels": {
                "limit_price": _q(ptp_level), "quantity_fraction": cfg["PTP1"]["quantity_fraction"],
                "r_ref": _q(r_ref),
            }}
            controls["DS5"] = {
                "control_id": "DS5",
                "status": "DEFINED" if ds_level > 0 else "DEFINED_STOP_LEVEL_NOT_POSITIVE",
                "reason": None if ds_level > 0 else "STOP_LEVEL_AT_OR_BELOW_ZERO_CANNOT_TRIGGER",
                "levels": {"stop_price": _q(ds_level)},
            }
    start = {
        "schema_version": START_SCHEMA_VERSION,
        "market": market,
        "symbol": position["symbol"],
        "position_episode_id": position["position_episode_id"],
        "rotation_entity": {"scope_id": position["rotation_scope_id"], "entity_id": position["rotation_entity_id"]},
        "entry_rotation_as_of_date": position["entry_rotation_as_of_date"],
        "first_fill_at": stamp(first_fill),
        "first_fill_price": decimal_text(p1),
        "entry_decision_at": stamp(decision),
        "atr14": atr_block,
        "controls": [controls[cid] for cid in CONTROL_IDS],
        "definition_source": copy.deepcopy(policy["config"]["source_documents"]["exit_study_v2"]),
        "units_ratification": units_ratification,
        "record_only": True,
        "rule_refs": _refs(policy),
        "authority": EXIT.authority(policy),
    }
    return EXIT.with_payload_sha(start)


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------

def _price_observations(observations: list, as_of: dt.datetime) -> list:
    """``SNAPSHOT``: ``t_obs``, ``price`` (executable sell reference). ``BAR``:
    ``t_obs`` = bar start, ``bar_end``, ``open``/``high``/``low``. Only rows known
    by ``as_of`` (snapshot captured, bar completed) are used."""
    rows, seen = [], set()
    for item in observations or []:
        oid = EXIT._token(item.get("observation_id"), "OBSERVATION_ID_INVALID")
        if oid in seen:
            _fail("OBSERVATION_ID_DUPLICATE", oid)
        seen.add(oid)
        kind = item.get("kind")
        t_obs = parse_utc(item.get("t_obs"), "OBSERVATION_T_OBS_INVALID")
        freshness = item.get("freshness")
        if freshness not in ("FRESH", "STALE", "UNKNOWN"):
            _fail("OBSERVATION_FRESHNESS_INVALID", oid)
        if kind == "SNAPSHOT":
            known = t_obs
            row = {"observation_id": oid, "kind": kind, "t_obs": t_obs, "known_at": known, "freshness": freshness,
                   "price": parse_decimal(item.get("price"), "OBSERVATION_PRICE_INVALID", positive=True)}
        elif kind == "BAR":
            known = parse_utc(item.get("bar_end"), "OBSERVATION_BAR_END_INVALID")
            if known <= t_obs:
                _fail("OBSERVATION_BAR_END_NOT_AFTER_START", oid)
            row = {"observation_id": oid, "kind": kind, "t_obs": t_obs, "known_at": known, "freshness": freshness,
                   "open": parse_decimal(item.get("open"), "OBSERVATION_OPEN_INVALID", positive=True),
                   "high": parse_decimal(item.get("high"), "OBSERVATION_HIGH_INVALID", positive=True),
                   "low": parse_decimal(item.get("low"), "OBSERVATION_LOW_INVALID", positive=True)}
            if row["low"] > row["high"] or not row["low"] <= row["open"] <= row["high"]:
                _fail("OBSERVATION_BAR_RANGE_INVALID", oid)
        else:
            _fail("OBSERVATION_KIND_INVALID", oid)
        if known <= as_of:
            rows.append(row)
    return sorted(rows, key=lambda r: (r["t_obs"], r["known_at"], r["observation_id"]))


def _decision_snapshots(snapshots: list, as_of: dt.datetime) -> list:
    rows = []
    for item in snapshots or []:
        captured = parse_utc(item.get("captured_at"), "DECISION_SNAPSHOT_CAPTURED_AT_INVALID")
        decided = parse_utc(item.get("decision_at"), "DECISION_SNAPSHOT_DECISION_AT_INVALID")
        if decided < captured:
            _fail("DECISION_BEFORE_SNAPSHOT_CAPTURE", str(item.get("snapshot_id")))
        if decided <= as_of:
            rows.append({"snapshot_id": EXIT._token(item.get("snapshot_id"), "DECISION_SNAPSHOT_ID_INVALID"),
                         "captured_at": captured, "decision_at": decided, "freshness": item.get("freshness")})
    return sorted(rows, key=lambda r: (r["captured_at"], r["snapshot_id"]))


def _in_gap(moment: dt.datetime, gaps: list) -> bool:
    return any(parse_utc(g["from"]) < moment < parse_utc(g["to"]) for g in gaps)


def _first_fresh_price_after(observations: list, after: dt.datetime, gaps: list, *,
                             bar_open_at_boundary: bool = False) -> Optional[dict]:
    return _first_allowed_price_after(observations, after, gaps, bar_open_at_boundary=bar_open_at_boundary)


def _first_allowed_price_after(observations: list, after: dt.datetime, gaps: list, *,
                               bar_open_at_boundary: bool = False) -> Optional[dict]:
    """First allowed price strictly after ``after``: SNAPSHOT price or BAR open.

    ``bar_open_at_boundary``: a trigger read from a completed bar is known at
    that bar's end, and the next bar's open at exactly that instant is the
    first price after it (back-to-back bars), so a BAR starting at ``after``
    is allowed. Decision-driven fills keep the strict ``t_obs > t_ord`` rule.

    Nothing inside a monitoring gap is an allowed price (no fill is created
    during a gap; the first price after recovery is used instead)."""
    for row in observations:
        if row["freshness"] != "FRESH" or not row.get("allowed", True) or _in_gap(row["t_obs"], gaps):
            continue
        if row["t_obs"] > after or (bar_open_at_boundary and row["kind"] == "BAR" and row["t_obs"] == after):
            return row
    return None


def _obs_price(row: dict) -> Decimal:
    return row["price"] if row["kind"] == "SNAPSHOT" else row["open"]


def _lots(lots: list, first_fill_at: dt.datetime) -> list:
    rows = []
    for lot in lots or []:
        t_fill = parse_utc(lot.get("t_fill"), "LOT_FILL_TIME_INVALID")
        if t_fill < first_fill_at:
            _fail("LOT_BEFORE_FIRST_FILL")
        rows.append({"t_fill": t_fill, "quantity": parse_decimal(lot.get("quantity"), "LOT_QUANTITY_INVALID", positive=True)})
    if not rows or min(r["t_fill"] for r in rows) != first_fill_at:
        _fail("LOTS_MUST_INCLUDE_FIRST_FILL")
    return sorted(rows, key=lambda r: r["t_fill"])


def _qty_at(lots: list, at: dt.datetime) -> Decimal:
    return sum((lot["quantity"] for lot in lots if lot["t_fill"] <= at), Decimal(0))


def _default_exit(fills: list, lots: list) -> dict:
    rows = []
    for fill in fills or []:
        rows.append({"t_fill": parse_utc(fill.get("t_fill"), "DEFAULT_EXIT_FILL_TIME_INVALID"),
                     "quantity": parse_decimal(fill.get("quantity"), "DEFAULT_EXIT_QUANTITY_INVALID", positive=True),
                     "price": parse_decimal(fill.get("price"), "DEFAULT_EXIT_PRICE_INVALID", positive=True),
                     "reason_code": fill.get("reason_code")})
    rows.sort(key=lambda r: r["t_fill"])
    if not rows:
        return {"status": "NOT_EXITED", "first_fill_at": None, "last_fill_at": None, "average_price": None, "quantity": None}
    total = sum((r["quantity"] for r in rows), Decimal(0))
    held = _qty_at(lots, rows[-1]["t_fill"])
    average = sum((r["quantity"] * r["price"] for r in rows), Decimal(0)) / total
    return {
        "status": "EXITED" if total >= held else "PARTIALLY_EXITED",
        "first_fill_at": rows[0]["t_fill"], "last_fill_at": rows[-1]["t_fill"],
        "average_price": average, "quantity": total,
        "reason_codes": sorted({str(r["reason_code"]) for r in rows}),
    }


def _leg(kind: str, quantity: Decimal, price: Decimal, t_fill: dt.datetime, p1: Decimal, basis: str, **extra) -> dict:
    return {
        "leg": kind, "quantity": _q(quantity), "price": _q(price), "t_fill": stamp(t_fill), "fill_basis": basis,
        "gross_return_vs_first_fill_price": _q(price / p1 - 1), "gross_proceeds": _q(price * quantity),
    } | extra


def _close_with_default(legs: list, remaining: Decimal, default: dict, p1: Decimal) -> tuple:
    if remaining <= 0:
        return legs, "CLOSED"
    if default["status"] != "EXITED":
        return legs, "OPEN"
    legs = legs + [_leg("DEFAULT_EXIT", remaining, default["average_price"], default["last_fill_at"], p1,
                        "DEFAULT_EXIT_AVERAGE_FILL_PRICE", default_reason_codes=default["reason_codes"])]
    return legs, "CLOSED"


# ---------------------------------------------------------------------------
# D9 monitored stop fill model
# ---------------------------------------------------------------------------

def monitoring_gaps(policy: dict, observations: list, start: dt.datetime, end: dt.datetime,
                    expected_interval_seconds: int) -> list:
    """Intervals with no FRESH monitoring SNAPSHOT for more than ``multiple`` x the expected cadence.

    Completed bars back-filled after a gap confirm triggers but are not the
    monitoring feed, so they never close a gap."""
    if type(expected_interval_seconds) is not int or expected_interval_seconds <= 0:
        _fail("EXPECTED_MONITORING_INTERVAL_INVALID")
    multiple = Decimal(policy["config"]["rules"]["monitored_stop_fill_model"]["monitoring_gap_interval_multiple"])
    limit = dt.timedelta(seconds=float(multiple * expected_interval_seconds))
    times = [start] + [row["known_at"] for row in observations
                       if row["kind"] == "SNAPSHOT" and row["freshness"] == "FRESH" and start < row["known_at"] <= end]
    if not times or times[-1] < end:
        times.append(end)
    gaps = []
    for previous, current in zip(times, times[1:]):
        if current - previous > limit:
            gaps.append({"from": stamp(previous), "to": stamp(current), "seconds": int((current - previous).total_seconds()),
                         "open_at_as_of": current == end})
    return gaps


def _session_windows(policy: dict, market: str, calendar: dict, start: dt.datetime, end: dt.datetime) -> list:
    zone = EXIT.ZoneInfo(policy["config"]["rules"]["time_contract"]["fill_windows"][market]["timezone"])
    day = start.astimezone(zone).date()
    last = end.astimezone(zone).date()
    windows = []
    while day <= last:
        if calendar["sessions"].get(day.isoformat()) == "OPEN":
            w_start, w_end = EXIT._window_bounds(policy, market, day)
            if w_end > start and w_start < end:
                windows.append((max(w_start, start), min(w_end, end)))
        day += dt.timedelta(days=1)
    return windows


def monitoring_gaps_in_windows(policy: dict, market: str, calendar: dict, observations: list, start: dt.datetime,
                               end: dt.datetime, expected_interval_seconds: int) -> list:
    """KR/US: gaps are measured only inside D1 fill windows of OPEN sessions (closed hours are not gaps).
    The monitoring feed is every FRESH allowed observation (completed bars or quote snapshots)."""
    if type(expected_interval_seconds) is not int or expected_interval_seconds <= 0:
        _fail("EXPECTED_MONITORING_INTERVAL_INVALID")
    multiple = Decimal(policy["config"]["rules"]["monitored_stop_fill_model"]["monitoring_gap_interval_multiple"])
    limit = dt.timedelta(seconds=float(multiple * expected_interval_seconds))
    gaps = []
    for w_start, w_end in _session_windows(policy, market, calendar, start, end):
        times = [w_start] + [row["known_at"] for row in observations
                             if row["freshness"] == "FRESH" and w_start < row["known_at"] <= w_end] + [w_end]
        for previous, current in zip(times, times[1:]):
            if current - previous > limit:
                gaps.append({"from": stamp(previous), "to": stamp(current), "seconds": int((current - previous).total_seconds()),
                             "open_at_as_of": current == end})
    return gaps


def _ts14_due_kr_us(policy: dict, market: str, calendar: Optional[dict], first_fill: dt.datetime, trading_days: int) -> dict:
    """Due at the end of the D1 fill window of the Nth OPEN session after the first-fill session (CIO interpretation)."""
    if calendar is None:
        return {"status": "UNKNOWN", "reason": "SESSION_CALENDAR_NOT_SUPPLIED"}
    calendar = EXIT.validate_session_calendar(calendar, market)
    zone = EXIT.ZoneInfo(policy["config"]["rules"]["time_contract"]["fill_windows"][market]["timezone"])
    day = first_fill.astimezone(zone).date()
    if calendar["sessions"].get(day.isoformat()) != "OPEN":
        return {"status": "UNKNOWN", "reason": "FIRST_FILL_SESSION_NOT_OPEN_IN_CALENDAR"}
    first_session, counted = day, 0
    last = max(EXIT.parse_date(d) for d in calendar["sessions"])
    while counted < trading_days:
        day += dt.timedelta(days=1)
        if day > last:
            return {"status": "UNKNOWN", "reason": "SESSION_CALENDAR_EXHAUSTED"}
        status = calendar["sessions"].get(day.isoformat())
        if status is None:
            return {"status": "UNKNOWN", "reason": f"SESSION_CALENDAR_DATE_UNKNOWN:{day.isoformat()}"}
        if status == "OPEN":
            counted += 1
        elif status != "CLOSED":
            return {"status": "UNKNOWN", "reason": f"NON_REGULAR_SESSION_NOT_RATIFIED:{status}"}
    _start, window_end = EXIT._window_bounds(policy, market, day)
    return {"status": "KNOWN", "reason": None, "first_fill_session_date": first_session.isoformat(),
            "deadline_session_date": day.isoformat(), "due_at": window_end, "trading_days": trading_days}


def monitored_stop(policy: dict, stop_price: Decimal, observations: list, entry_fill_at: dt.datetime,
                   before: Optional[dt.datetime], gaps: list) -> dict:
    """Trigger and fill under RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1 (D9)."""
    trigger = None
    for row in observations:
        if row["freshness"] != "FRESH" or (row["kind"] == "SNAPSHOT" and row["t_obs"] <= entry_fill_at):
            continue
        if not row.get("allowed", True):
            continue  # KR/US: prices outside the regular-session D1 window neither trigger nor fill
        if row["kind"] == "BAR" and row["t_obs"] < entry_fill_at:
            continue  # a bar overlapping the entry fill never triggers
        if before is not None and row["known_at"] >= before:
            break
        touched = row["price"] <= stop_price if row["kind"] == "SNAPSHOT" else row["low"] <= stop_price
        if touched:
            trigger = row
            break
    if trigger is None:
        return {"status": "NOT_TRIGGERED", "trigger": None, "fill": None}
    fill = _first_fresh_price_after(observations, trigger["known_at"], gaps,
                                    bar_open_at_boundary=trigger["kind"] == "BAR")
    trigger_out = {"observation_id": trigger["observation_id"], "kind": trigger["kind"], "t_obs": stamp(trigger["t_obs"]),
                   "known_at": stamp(trigger["known_at"]),
                   "observed_price": _q(trigger["price"] if trigger["kind"] == "SNAPSHOT" else trigger["low"])}
    if fill is None:
        return {"status": "TRIGGERED_AWAITING_FIRST_ALLOWED_PRICE", "trigger": trigger_out, "fill": None}
    price = _obs_price(fill)
    # Study v2 pre-registration 3 GAP (reference only): a completed-bar trigger
    # sells at the open of the next existing bar, fill = min(stop, that open).
    # Not defined for a snapshot trigger.
    study_reference = None
    if trigger["kind"] == "BAR":
        next_bar = next((row for row in observations if row["kind"] == "BAR" and row["freshness"] == "FRESH"
                         and row["t_obs"] >= trigger["known_at"]), None)
        if next_bar is not None:
            study_reference = _q(min(stop_price, next_bar["open"]))
    return {
        "status": "FILLED",
        "trigger": trigger_out,
        "fill": {
            "observation_id": fill["observation_id"], "kind": fill["kind"], "t_obs": stamp(fill["t_obs"]),
            "price": price, "gap_down": price < stop_price,
            "stop_minus_fill": _q(stop_price - price),
            "study_v2_gap_primary_reference_price": study_reference,
        },
    }


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def _decision_after(snapshots: list, not_before: dt.datetime, before: Optional[dt.datetime]) -> Optional[dict]:
    for row in snapshots:
        if row["captured_at"] >= not_before and row["freshness"] == "FRESH":
            if before is not None and row["decision_at"] >= before:
                return None
            return row
    return None


def evaluate_shadow_controls(policy: dict, start: dict, *, as_of: str, lots: list, price_observations: list,
                             decision_snapshots: list, rotation_packets: list, default_exit_fills: list,
                             expected_monitoring_interval_seconds: int, calendar: Optional[dict] = None) -> dict:
    """Record-only shadow result rows up to ``as_of`` for one position episode.

    KR/US need the caller-supplied official session ``calendar``: prices count
    only inside D1 fill windows, and TS14 counts OPEN sessions."""
    start = EXIT.verify_payload_sha(start, "SHADOW_START_SHA_MISMATCH")
    if start.get("schema_version") != START_SCHEMA_VERSION:
        _fail("SHADOW_START_SCHEMA_INVALID")
    cutoff = parse_utc(as_of, "SHADOW_AS_OF_INVALID")
    controls = {row["control_id"]: row for row in start["controls"]}
    base_result = {
        "schema_version": RESULT_SCHEMA_VERSION,
        "market": start["market"],
        "symbol": start["symbol"],
        "position_episode_id": start["position_episode_id"],
        "start_payload_sha256": start["payload_sha256"],
        "as_of": stamp(cutoff),
        "record_only": True,
        "authority": EXIT.authority(policy),
    }
    market = start["market"]
    first_fill = parse_utc(start["first_fill_at"])
    p1 = Decimal(start["first_fill_price"])
    lot_rows = _lots(lots, first_fill)
    observations = _price_observations(price_observations, cutoff)
    if market != "CRYPTO":
        if calendar is not None:
            calendar = EXIT.validate_session_calendar(calendar, market)
        for row in observations:
            row["allowed"] = calendar is not None and EXIT.is_allowed_fill_time(policy, market, stamp(row["t_obs"]), calendar)[0]
    snapshots = _decision_snapshots(decision_snapshots, cutoff)
    default = _default_exit(default_exit_fills, lot_rows)
    default_cut = default["first_fill_at"]
    gap_end = cutoff if default_cut is None else min(cutoff, default_cut)
    if market == "CRYPTO":
        gaps = monitoring_gaps(policy, observations, first_fill, gap_end, expected_monitoring_interval_seconds)
    elif calendar is None:
        gaps = []
    else:
        gaps = monitoring_gaps_in_windows(policy, market, calendar, observations, first_fill, gap_end,
                                          expected_monitoring_interval_seconds)
    rows = []

    # TS14
    ts = controls["TS14"]
    if market == "CRYPTO":
        due = {"status": "KNOWN", "reason": None, "due_at": parse_utc(ts["levels"]["deadline_at"])}
    else:
        due = _ts14_due_kr_us(policy, market, calendar, first_fill, ts["levels"]["trading_days"])
    ts_row = {"control_id": "TS14", "status": None, "reason": due["reason"], "legs": [],
              "component": {k: (stamp(v) if isinstance(v, dt.datetime) else v) for k, v in due.items() if k not in ("status", "reason")}}
    if "due_at" in ts_row["component"]:
        ts_row["component"]["deadline_at"] = ts_row["component"].pop("due_at")
    remaining = _qty_at(lot_rows, cutoff if default_cut is None else default_cut)
    decision = None if due["status"] != "KNOWN" else _decision_after(snapshots, due["due_at"], default_cut)
    if due["status"] != "KNOWN" and default["status"] != "EXITED":
        ts_row["status"] = "UNKNOWN"
    elif decision is not None:
        fill = _first_fresh_price_after(observations, decision["decision_at"], gaps)
        ts_row["component"].update(decision_snapshot_id=decision["snapshot_id"], t_dec=stamp(decision["decision_at"]))
        if fill is None:
            ts_row["status"] = "COMPONENT_TRIGGERED_AWAITING_FIRST_ALLOWED_PRICE"
        else:
            qty = _qty_at(lot_rows, decision["decision_at"])
            ts_row["legs"] = [_leg("TIME_STOP_14D" if market == "CRYPTO" else "TIME_STOP_14_TRADING_DAYS", qty, _obs_price(fill), fill["t_obs"], p1, f"FIRST_FRESH_{fill['kind']}_AFTER_DECISION")]
            ts_row["status"] = "CLOSED"
    if ts_row["status"] is None:
        ts_row["legs"], ts_row["status"] = _close_with_default([], remaining, default, p1)
    rows.append(ts_row)

    # 1-B
    lag = controls["1-B"]
    lag_row = {"control_id": "1-B", "status": None, "reason": lag["reason"], "component": {"lagging_packets": []}, "legs": []}
    if lag["status"] == "DEFINED":
        entity_id = start["rotation_entity"]["entity_id"]
        scope_id = start["rotation_entity"]["scope_id"]
        identity = EXIT.RC.policy_identity(policy["rotation_policy"])
        trigger = None
        for item in sorted(rotation_packets or [], key=lambda i: i["packet"]["as_of_date"]):
            packet = EXIT.RC.validate_packet(item["packet"])
            if packet["policy"] != identity or packet["market"] != "CRYPTO":
                _fail("SHADOW_ROTATION_PACKET_MISMATCH", packet["as_of_date"])
            available = parse_utc(item.get("available_at"), "ROTATION_PACKET_AVAILABLE_AT_INVALID")
            if packet["as_of_date"] <= start["entry_rotation_as_of_date"] or available > cutoff:
                continue
            if default_cut is not None and available >= default_cut:
                break
            entity = EXIT._entity(packet, scope_id, entity_id)
            lagging = (entity or {}).get("lagging") or {}
            lag_row["component"]["lagging_packets"].append({"as_of_date": packet["as_of_date"], "status": lagging.get("status"),
                                                            "lagging_warning": lagging.get("lagging_warning")})
            if lagging.get("lagging_warning") is True:
                trigger = {"as_of_date": packet["as_of_date"], "payload_sha256": packet["payload_sha256"], "available_at": available}
                break
        if trigger is not None:
            lag_row["component"]["trigger"] = {"as_of_date": trigger["as_of_date"], "payload_sha256": trigger["payload_sha256"],
                                               "available_at": stamp(trigger["available_at"])}
            decision = _decision_after(snapshots, trigger["available_at"], default_cut)
            fill = None if decision is None else _first_fresh_price_after(observations, decision["decision_at"], gaps)
            if decision is None and default["status"] == "EXITED":
                lag_row["component"]["superseded_by_default_exit"] = True
            elif decision is None or fill is None:
                lag_row["status"] = "COMPONENT_TRIGGERED_AWAITING_FIRST_ALLOWED_PRICE"
            else:
                lag_row["component"].update(decision_snapshot_id=decision["snapshot_id"], t_dec=stamp(decision["decision_at"]))
                qty = _qty_at(lot_rows, decision["decision_at"])
                lag_row["legs"] = [_leg("LAGGING_EXIT", qty, _obs_price(fill), fill["t_obs"], p1, f"FIRST_FRESH_{fill['kind']}_AFTER_DECISION")]
                lag_row["status"] = "CLOSED"
    if lag_row["status"] is None:
        lag_row["legs"], lag_row["status"] = _close_with_default([], remaining, default, p1)
    rows.append(lag_row)

    # PTP1
    ptp = controls["PTP1"]
    ptp_row = {"control_id": "PTP1", "status": None, "reason": ptp["reason"], "component": {}, "legs": []}
    if ptp["status"] != "DEFINED":
        ptp_row["status"] = ptp["status"]
    else:
        level = Decimal(ptp["levels"]["limit_price"])
        fraction = Decimal(ptp["levels"]["quantity_fraction"])
        tp_fill = None
        for row in observations:
            if row["freshness"] != "FRESH" or not row.get("allowed", True) or (default_cut is not None and row["known_at"] >= default_cut):
                continue
            if row["kind"] == "SNAPSHOT" and row["t_obs"] > first_fill and row["price"] > level:
                tp_fill = (row, level, "RESTING_LIMIT_PRICE_SNAPSHOT_ABOVE_LEVEL")
                break
            if row["kind"] == "BAR" and row["t_obs"] >= first_fill and row["high"] > level:
                tp_fill = (row, max(level, row["open"]), "STUDY_V2_BAR_HIGH_ABOVE_LEVEL_MAX_LEVEL_OPEN")
                break
        remaining = _qty_at(lot_rows, cutoff if default_cut is None else default_cut)
        legs = []
        if tp_fill is not None:
            row, price, basis = tp_fill
            held = _qty_at(lot_rows, row["t_obs"])
            sold = held * fraction
            legs.append(_leg("PARTIAL_TAKE_PROFIT_1R", sold, price, row["t_obs"], p1, basis))
            remaining = held - sold  # top-ups after the TP fill are not counted in the shadow
            ptp_row["component"]["top_ups_after_fill_excluded"] = decimal_text(_qty_at(lot_rows, cutoff) - held)
        ptp_row["legs"], closed = _close_with_default(legs, remaining, default, p1)
        ptp_row["status"] = closed if closed == "CLOSED" else ("PARTIAL_COMPONENT_FILLED_OPEN" if legs else "OPEN")
    rows.append(ptp_row)

    # DS5
    ds = controls["DS5"]
    ds_row = {"control_id": "DS5", "status": None, "reason": ds["reason"], "component": {}, "legs": []}
    if ds["status"] not in ("DEFINED", "DEFINED_STOP_LEVEL_NOT_POSITIVE"):
        ds_row["status"] = ds["status"]
    else:
        stop_price = Decimal(ds["levels"]["stop_price"])
        outcome = monitored_stop(policy, stop_price, observations, first_fill, default_cut, gaps)
        component = {"status": outcome["status"], "trigger": outcome["trigger"]}
        remaining = _qty_at(lot_rows, cutoff if default_cut is None else default_cut)
        if outcome["status"] == "FILLED":
            fill = outcome["fill"]
            trigger_known = parse_utc(outcome["trigger"]["known_at"])
            in_gap = any(parse_utc(g["from"]) < trigger_known <= parse_utc(g["to"]) for g in gaps)
            component["trigger_in_monitoring_gap"] = in_gap
            qty = _qty_at(lot_rows, trigger_known)
            ds_row["legs"] = [_leg("MONITORED_STOP_5ATR", qty, fill["price"], parse_utc(fill["t_obs"]), p1,
                                   f"D9_FIRST_ALLOWED_{fill['kind']}_AFTER_TRIGGER",
                                   gap_down=fill["gap_down"], stop_minus_fill=fill["stop_minus_fill"],
                                   study_v2_gap_primary_reference_price=fill["study_v2_gap_primary_reference_price"])]
            ds_row["status"] = "CLOSED"
        elif outcome["status"] == "TRIGGERED_AWAITING_FIRST_ALLOWED_PRICE":
            ds_row["status"] = outcome["status"]
        else:
            ds_row["legs"], ds_row["status"] = _close_with_default([], remaining, default, p1)
        ds_row["component"] = component
    rows.append(ds_row)

    default_out = None
    if default["status"] != "NOT_EXITED":
        default_out = {"status": default["status"], "first_fill_at": stamp(default["first_fill_at"]),
                       "last_fill_at": stamp(default["last_fill_at"]), "average_price": _q(default["average_price"]),
                       "quantity": _q(default["quantity"]), "reason_codes": default["reason_codes"],
                       "gross_proceeds": _q(default["average_price"] * default["quantity"])}
    for row in rows:
        row["shadow_minus_default_gross_proceeds"] = None
        if row["status"] == "CLOSED" and default_out is not None and default_out["status"] == "EXITED":
            quantity = sum((Decimal(leg["quantity"]) for leg in row["legs"]), Decimal(0))
            if quantity == Decimal(default_out["quantity"]):
                proceeds = sum((Decimal(leg["gross_proceeds"]) for leg in row["legs"]), Decimal(0))
                row["shadow_minus_default_gross_proceeds"] = _q(proceeds - Decimal(default_out["gross_proceeds"]))
            else:
                row["shadow_quantity_differs_from_default"] = _q(quantity)
    result = base_result | {
        "units_ratification": copy.deepcopy(start.get("units_ratification")),
        "controls": rows,
        "default_exit": default_out,
        "monitoring_gaps": gaps,
        "costs": "NOT_APPLIED_SCORECARD_CONTRACT_OWNS_COSTS",
        "rule_refs": _refs(policy, ("monitored_stop_fill_model", "APPLIED")),
    }
    return EXIT.with_payload_sha(result)
