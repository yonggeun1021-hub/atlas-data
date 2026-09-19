#!/usr/bin/env python3
"""PAPER session budget v1: NAV0, market room, B = Room/3, water-filling, session_budget_record/1.

Ratified inputs (via ``config/paper_execution_core_v1.json``):

* RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2 (wording correction, record
  9af25a3b...): market session buy total <= 1/3 of the market share's
  remaining room at session start; per-name cumulative holding <= 5% NAV and
  <= 1% of the ratified average traded value (KR/US 20 sessions, crypto 30
  days); partial quantities, next-session top-ups and equal split across
  candidates allowed.
* RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1 (record 47276abe...): BTC/ETH stay at 5%.
* RULE.ALLOCATION.V2 via the allocation envelope (cap fraction, new buys).
* RULE.EXEC.TIME_CONTRACT.V1 (D1): crypto decision cycle 07:00Z; KR/US
  fill windows (order validity ends at the session window end).

CIO interpretations with sources (config ``cio_interpretations``): canon 2-2
NAV0 / L / Cap / Room / B and allocation steps (equal share, cap, redistribute,
floor to quantity step, no carry, code ascending); canon 2-3 submitted-amount
consumption, no restore on cancel, one allocation per session; canon 2-4
record key (market, session_id, rule version) reused on restart, an
unverified holding valuation in any market blocks new buys in every
market (user ratification 2026-09-18); canon 5-4 inverse hedge not in L; build plan 2-3
principle 4 / section 9 note 3 for NAV0 in the first crypto cycle.

US holdings are valued in USD and converted with RULE.NAV.KRW_USD_CONVERSION_
FRED_DEXKOUS.V1 (P2): the caller supplies the DEXKOUS observation; a stale
rate marks NAV 'NAV 일부 미검증' on the record and never blocks allocation.

Pure and offline.  The ledger here is an in-memory append-only replay model
for tests and consumers; persistence belongs to the private runtime (PR4).
"""
from __future__ import annotations

import copy
import datetime as dt
from fractions import Fraction
import math
import re
from zoneinfo import ZoneInfo
import calendar

try:
    from portfolio import paper_allocation_envelope as ENV
    from portfolio import paper_execution_core as CORE
except ImportError:  # pragma: no cover
    import paper_allocation_envelope as ENV  # type: ignore
    import paper_execution_core as CORE  # type: ignore


RECORD_SCHEMA_VERSION = "session_budget_record/1"
LEDGER_EVENT_SCHEMA_VERSION = "session_budget_ledger_event/1"
RULE_SIZE = "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2"
RULE_BTC_ETH = "RULE.SIZE.BTC_ETH_PER_NAME_CAP.V1"
RULE_ALLOC = "RULE.ALLOCATION.V2"
RULE_TIME = "RULE.EXEC.TIME_CONTRACT.V1"
RULE_FX = "RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1"
SESSION_ID_RE = re.compile(r"^(?P<market>CRYPTO|KR|US)-(?P<day>\d{4}-\d{2}-\d{2})$")
ADV_WINDOW_BY_MARKET = {"KR": "20_SESSIONS", "US": "20_SESSIONS", "CRYPTO": "30_DAYS"}
HOLDING_FIELDS = {"market", "instrument", "currency", "valuation", "last_verified_valuation", "is_inverse_hedge"}
NAV_SNAPSHOT_FIELDS = {"as_of_utc", "virtual_cash_krw", "holdings", "open_buy_reservations", "fx_observation"}
RESERVATION_FIELDS = {"market", "instrument", "order_id", "reserved_krw"}
CANDIDATE_FIELDS = {"instrument", "avg_traded_value", "adv_window", "adv_source", "limit_price", "quantity_step", "fee_rate"}


def _utc(ts: str) -> dt.datetime:
    return dt.datetime.strptime(CORE.require_utc(ts, "timestamp"), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _iso(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def crypto_session_id(core, decision_at_utc: str) -> str:
    """Crypto session = one 07:00Z decision cycle (canon 1-3, D1)."""
    hour, minute = (int(x) for x in core.param("crypto_decision_cycle")["decision_time_utc"].split(":"))
    moment = _utc(decision_at_utc)
    anchor = moment.replace(hour=hour, minute=minute, second=0)
    if moment < anchor:
        anchor -= dt.timedelta(days=1)
    return f"CRYPTO-{anchor.date().isoformat()}"


def session_bounds(core, session_id: str) -> dict:
    """Session start and order-validity end (canon 2-3) for a session id.

    KR/US ids name an official trading date; whether that date is open is a
    calendar input checked by the caller (RULE.US.SESSION_CALENDAR.V1 etc.).
    """
    match = SESSION_ID_RE.fullmatch(session_id or "")
    if match is None:
        CORE.fail("SESSION_ID_INVALID", str(session_id))
    market, day = match.group("market"), dt.date.fromisoformat(match.group("day"))
    if market == "CRYPTO":
        hour, minute = (int(x) for x in core.param("crypto_decision_cycle")["decision_time_utc"].split(":"))
        start = dt.datetime(day.year, day.month, day.day, hour, minute, tzinfo=dt.timezone.utc)
        end = start + dt.timedelta(days=1)
    else:
        window = core.param("kr_fill_window" if market == "KR" else "us_fill_window")
        zone = ZoneInfo(window["timezone"])
        sh, sm = (int(x) for x in window["start"].split(":"))
        eh, em = (int(x) for x in window["end"].split(":"))
        start = dt.datetime(day.year, day.month, day.day, sh, sm, tzinfo=zone)
        end = dt.datetime(day.year, day.month, day.day, eh, em, tzinfo=zone)
    return {"market": market, "session_id": session_id, "start_utc": _iso(start),
            "order_valid_before_utc": _iso(end)}


def evaluate_fx(core, fx, decision_at_utc: str) -> dict:
    """RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1 (P2) on a caller-supplied observation.

    ``fx``: null or ``{"series", "observation_date", "published_at_utc",
    "krw_per_usd"}`` -- the latest value published before the decision.
    Published at or after the decision time is lookahead.  Staleness counts
    from the publication (availability) date of that latest value: more than
    the ratified business days -> STALE, displayed 'NAV 일부 미검증'.  It is a
    NAV status only; it never blocks allocation (CIO decision 2026-09-15:
    the user sentence only says display).  Business days = Mon-Fri after the
    publication UTC date up to the decision UTC date (CIO interpretation, US
    holidays not excluded).
    """
    source = core.param("fx_rate_source")
    staleness = core.param("fx_staleness")
    if fx is None:
        return {"status": "MISSING", "krw_per_usd": None, "observation_date": None, "published_at_utc": None,
                "business_days_since_publication": None, "display_ko": staleness["display"]}
    if not isinstance(fx, dict) or set(fx) != {"series", "observation_date", "published_at_utc", "krw_per_usd"}:
        CORE.fail("FX_OBSERVATION_FIELDS_INVALID")
    if fx["series"] != source["series"]:
        CORE.fail("FX_SERIES_NOT_RATIFIED", str(fx["series"]))
    if core.param("fx_availability") != "PUBLISHED_BEFORE_DECISION_TIME" \
            or CORE.require_utc(fx["published_at_utc"], "fx.published_at_utc") >= decision_at_utc:
        CORE.fail("FX_NOT_PUBLISHED_BEFORE_DECISION")
    published = _utc(fx["published_at_utc"]).date()
    if dt.date.fromisoformat(fx["observation_date"]) > published:
        CORE.fail("FX_OBSERVATION_AFTER_PUBLICATION")
    rate = CORE.frac(fx["krw_per_usd"], "krw_per_usd")
    if rate <= 0:
        CORE.fail("FX_RATE_NOT_POSITIVE")
    day, business = published, 0
    while day < _utc(decision_at_utc).date():
        day += dt.timedelta(days=1)
        business += day.weekday() not in (calendar.SATURDAY, calendar.SUNDAY)
    if staleness["comparison"] != "MORE_THAN":
        CORE.fail("FX_STALENESS_RULE_UNEXPECTED")
    stale = business > staleness["max_business_days_without_new_value"]
    return {"status": "STALE" if stale else "VERIFIED", "krw_per_usd": CORE.fstr(rate),
            "observation_date": fx["observation_date"], "published_at_utc": fx["published_at_utc"],
            "business_days_since_publication": business, "display_ko": staleness["display"] if stale else None}


def _holdings(core, snapshot: dict, fx: dict) -> list:
    """Holdings with KRW value used in NAV0 and whether that value is verified."""
    rows = []
    rate = CORE.opt_frac(fx["krw_per_usd"], "krw_per_usd")
    for item in snapshot["holdings"]:
        if not isinstance(item, dict) or set(item) != HOLDING_FIELDS:
            CORE.fail("NAV_HOLDING_FIELDS_INVALID")
        CORE.require_market(item["market"])
        CORE.require_token(item["instrument"], "instrument")
        if item["is_inverse_hedge"] not in (True, False):
            CORE.fail("NAV_HOLDING_INVERSE_FLAG_INVALID")
        if item["currency"] != ("USD" if item["market"] == "US" else "KRW"):
            CORE.fail("NAV_HOLDING_CURRENCY_INVALID", item["instrument"])
        value = CORE.opt_frac(item["valuation"], "valuation")
        last = CORE.opt_frac(item["last_verified_valuation"], "last_verified_valuation")
        verified = value is not None
        local = value if verified else last
        if item["currency"] == "USD":
            # FX staleness is a NAV display status only (P2); price verification is unchanged.
            krw = None if local is None or rate is None else local * rate
        else:
            krw = local
        rows.append({"market": item["market"], "instrument": item["instrument"],
                     "is_inverse_hedge": item["is_inverse_hedge"], "krw": krw, "verified": verified})
    keys = [(r["market"], r["instrument"]) for r in rows]
    if len(keys) != len(set(keys)):
        CORE.fail("NAV_HOLDING_DUPLICATE")
    return rows


def compute_nav0(core, snapshot: dict, decision_at_utc: str) -> dict:
    """NAV0 = virtual cash + sum of verified valuations (all markets, KRW) at S0."""
    if not isinstance(snapshot, dict) or set(snapshot) != NAV_SNAPSHOT_FIELDS:
        CORE.fail("NAV_SNAPSHOT_FIELDS_INVALID")
    cash = CORE.frac(snapshot["virtual_cash_krw"], "virtual_cash_krw")
    if cash < 0:
        CORE.fail("VIRTUAL_CASH_NEGATIVE")
    fx = evaluate_fx(core, snapshot["fx_observation"], decision_at_utc)
    rows = _holdings(core, snapshot, fx)
    flags = set()
    if any(r["market"] == "US" for r in rows) and fx["status"] != "VERIFIED":
        flags.add(f"FX_{fx['status']}")
        flags.add("NAV_PARTIALLY_UNVERIFIED")
    if any(r["krw"] is None for r in rows):
        return {"status": "UNKNOWN", "nav0_krw": None, "fx": fx, "flags": sorted(flags | {"NAV_HOLDING_WITHOUT_KRW_VALUE"}),
                "unverified_markets": sorted({r["market"] for r in rows if not r["verified"]})}
    unverified = sorted({r["market"] for r in rows if not r["verified"]})
    if unverified:
        flags.add("NAV_PARTIALLY_UNVERIFIED")
    nav = cash + sum((r["krw"] for r in rows), Fraction(0))
    return {"status": "KNOWN", "nav0_krw": CORE.fstr(nav), "fx": fx, "flags": sorted(flags),
            "nav_verification": "UNVERIFIED" if "NAV_PARTIALLY_UNVERIFIED" in flags else "VERIFIED",
            "unverified_markets": unverified}


def _water_fill(budget: Fraction, caps: list) -> dict:
    """Equal split, cap, redistribute to uncapped until nothing is left to give."""
    alloc = {code: Fraction(0) for code, _ in caps}
    active = [(code, cap) for code, cap in caps if cap > 0]
    remaining = budget
    while remaining > 0 and active:
        share = remaining / len(active)
        capped = [(code, cap) for code, cap in active if cap - alloc[code] <= share]
        if not capped:
            for code, _ in active:
                alloc[code] += share
            remaining = Fraction(0)
            break
        for code, cap in capped:
            remaining -= cap - alloc[code]
            alloc[code] = cap
            active.remove((code, cap))
    return {"alloc": alloc, "unallocated": remaining}


def build_session_budget_record(
    core, *, market: str, session_id: str, decision_at_utc: str, nav_snapshot: dict,
    envelope: dict, candidates: list,
) -> dict:
    CORE.require_market(market)
    bounds = session_bounds(core, session_id)
    if bounds["market"] != market:
        CORE.fail("SESSION_ID_MARKET_MISMATCH")
    CORE.require_utc(decision_at_utc, "decision_at_utc")
    if not bounds["start_utc"] <= decision_at_utc < bounds["order_valid_before_utc"] and market == "CRYPTO":
        CORE.fail("DECISION_OUTSIDE_CRYPTO_SESSION")
    if nav_snapshot.get("as_of_utc") is None or nav_snapshot["as_of_utc"] > decision_at_utc:
        CORE.fail("NAV_SNAPSHOT_AFTER_DECISION")
    # The embedded envelope must re-derive under its own registry/config and be
    # the envelope of this very decision (no stale envelope reuse).
    ENV.validate_envelope(envelope, root=core_root(core))
    if envelope["decision_at_utc"] != decision_at_utc:
        CORE.fail("ENVELOPE_DECISION_TIME_MISMATCH")
    env_market = envelope["markets"][market]
    row = core.context.rules[RULE_SIZE]
    size_cap = CORE.frac(core.param("per_name_nav_cap")["max_nav"])
    liq = core.param("per_name_liquidity_cap")
    liq_fraction = CORE.frac(liq["max_fraction"])
    btc_eth = core.param("btc_eth_cap")
    room_fraction = CORE.frac(core.param("session_room_fraction")["fraction_of_remaining_market_share_room_at_session_start"])
    nav = compute_nav0(core, nav_snapshot, decision_at_utc)
    holdings = _holdings(core, nav_snapshot, nav["fx"])
    reservations = []
    for item in nav_snapshot["open_buy_reservations"]:
        if not isinstance(item, dict) or set(item) != RESERVATION_FIELDS:
            CORE.fail("RESERVATION_FIELDS_INVALID")
        CORE.require_market(item["market"])
        reservations.append({**item, "value": CORE.frac(item["reserved_krw"], "reserved_krw")})
    reasons = []
    pairs = [(RULE_SIZE, "SIZED_BY"), (RULE_ALLOC, "SIZED_BY"), (RULE_TIME, "APPLIED")]
    if nav["fx"]["status"] != "MISSING" or any(h["market"] == "US" for h in holdings):
        pairs.append((RULE_FX, "APPLIED"))
    long_value = sum((h["krw"] or Fraction(0) for h in holdings
                      if h["market"] == market and not h["is_inverse_hedge"]), Fraction(0))
    own_reserved = sum((r["value"] for r in reservations if r["market"] == market), Fraction(0))
    all_reserved = sum((r["value"] for r in reservations), Fraction(0))
    load = long_value + own_reserved
    cap = room = budget = None
    available_cash = CORE.frac(nav_snapshot["virtual_cash_krw"]) - all_reserved
    if env_market["new_buys"] not in ("PERMIT", "PERMIT_SELECTIVE"):
        reasons.append(f"NEW_BUYS_{env_market['new_buys']}_BY_STATE_{env_market['effective_state']}")
        pairs.append((RULE_ALLOC, "BLOCKED_BY"))
    if nav["status"] != "KNOWN":
        reasons.append("NAV0_UNKNOWN")
    elif nav["unverified_markets"]:
        # User ratification 2026-09-18: an unverified holding valuation denies new
        # buys in every market, not only the market that holds it.  The reason
        # names the unverified markets so a later "why no buy that day" is
        # answerable from the record alone.
        reasons.append("NEW_BUYS_BLOCKED_HOLDING_VALUATION_UNVERIFIED_IN_" + "_".join(nav["unverified_markets"]))
    if env_market["cap_fraction"] is None:
        reasons.append("MARKET_CAP_NOT_APPLICABLE")
    if nav["status"] == "KNOWN" and env_market["cap_fraction"] is not None:
        nav0 = CORE.frac(nav["nav0_krw"])
        cap = nav0 * CORE.frac(env_market["cap_fraction"])
        room = max(Fraction(0), cap - load)
        budget = max(Fraction(0), min(room * room_fraction, available_cash))
    if reasons:
        budget = Fraction(0) if budget is not None else None
    lines = []
    caps = []
    codes = [c.get("instrument") if isinstance(c, dict) else None for c in candidates]
    if len(codes) != len(set(codes)):
        CORE.fail("CANDIDATE_DUPLICATE")
    if any(not isinstance(c, dict) or set(c) != CANDIDATE_FIELDS for c in candidates):
        CORE.fail("CANDIDATE_FIELDS_INVALID")
    for cand in sorted(candidates, key=lambda c: c["instrument"]):
        code = CORE.require_token(cand["instrument"], "instrument")
        held = sum((h["krw"] or Fraction(0) for h in holdings
                    if h["market"] == market and h["instrument"] == code), Fraction(0))
        held += sum((r["value"] for r in reservations if r["market"] == market and r["instrument"] == code), Fraction(0))
        line_reasons = []
        name_cap_fraction = size_cap
        asset = code.split("-", 1)[1] if market == "CRYPTO" and "-" in code else code
        if market == "CRYPTO" and asset in btc_eth["assets"]:
            name_cap_fraction = min(name_cap_fraction, CORE.frac(btc_eth["max_nav"]))
            pairs.append((RULE_BTC_ETH, "SIZED_BY"))
        name_room = liq_room = None
        if nav["status"] == "KNOWN":
            name_room = max(Fraction(0), name_cap_fraction * CORE.frac(nav["nav0_krw"]) - held)
        if cand["adv_window"] != liq["windows"][market]:
            CORE.fail("ADV_WINDOW_MISMATCH", f"{code}:{cand['adv_window']}")
        # Average traded value is in the market currency (US: USD via the DEXKOUS rate).
        adv = CORE.opt_frac(cand["avg_traded_value"], "avg_traded_value")
        if adv is not None and market == "US":
            adv = None if nav["fx"]["krw_per_usd"] is None else adv * CORE.frac(nav["fx"]["krw_per_usd"])
        if adv is None:
            line_reasons.append("ADV_UNKNOWN_NO_LIQUIDITY_ROOM")
        else:
            liq_room = max(Fraction(0), liq_fraction * adv - held)
        cand_cap = None if name_room is None or liq_room is None else min(name_room, liq_room)
        if cand_cap is not None and cand_cap == 0:
            line_reasons.append("NAME_OR_LIQUIDITY_ROOM_ZERO")
        caps.append((code, cand_cap if cand_cap is not None and budget is not None else Fraction(0)))
        lines.append({"instrument": code, "held_krw": CORE.fstr(held), "name_cap_fraction": CORE.fstr(name_cap_fraction),
                      "name_room_krw": CORE.opt_fstr(name_room), "liquidity_room_krw": CORE.opt_fstr(liq_room),
                      "adv_source": cand["adv_source"], "reasons": line_reasons, "_cap": cand_cap, "_cand": cand})
    filled = _water_fill(budget or Fraction(0), caps)
    total_submitted = Fraction(0)
    for line in lines:
        amount = filled["alloc"][line["instrument"]]
        cand = line.pop("_cand")
        cand_cap = line.pop("_cap")
        line["allocated_krw"] = CORE.fstr(amount)
        line["bound_by"] = None
        if amount > 0 and cand_cap is not None and amount == cand_cap:
            line["bound_by"] = "NAME_ROOM" if cand_cap == CORE.frac(line["name_room_krw"]) else "LIQUIDITY_ROOM"
        line["quantity"] = line["submitted_amount_krw"] = None
        if amount > 0:
            if any(cand[k] is None for k in ("limit_price", "quantity_step", "fee_rate")):
                line["reasons"].append("QUANTITY_INPUTS_NOT_PROVIDED")
            else:
                price, step, fee = (CORE.frac(cand[k], k) for k in ("limit_price", "quantity_step", "fee_rate"))
                if price <= 0 or step <= 0 or fee < 0:
                    CORE.fail("QUANTITY_INPUT_NON_POSITIVE", line["instrument"])
                # US limit prices are USD: convert with the same verified DEXKOUS rate.
                to_krw = CORE.frac(nav["fx"]["krw_per_usd"]) if market == "US" else Fraction(1)
                qty = math.floor(amount / (price * to_krw * (1 + fee) * step)) * step
                if qty == 0:
                    line["reasons"].append("QUANTITY_ROUNDS_TO_ZERO_SKIPPED")
                else:
                    submitted = price * to_krw * qty * (1 + fee)
                    total_submitted += submitted
                    line["quantity"] = CORE.fstr(qty)
                    line["submitted_amount_krw"] = CORE.fstr(submitted)
        line["reasons"] = sorted(line["reasons"])
    if not candidates:
        reasons.append("NO_ELIGIBLE_CANDIDATES")
    allocated = sum((filled["alloc"][code] for code, _ in caps), Fraction(0))
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "key": {"market": market, "session_id": session_id, "rule_id": RULE_SIZE, "rule_version": row["version"]},
        "decision_at_utc": decision_at_utc,
        "session": bounds,
        "inputs": {"nav_snapshot": copy.deepcopy(nav_snapshot), "envelope": copy.deepcopy(envelope),
                   "candidates": copy.deepcopy(candidates)},
        "nav0": nav,
        "market_room": {
            "long_value_krw_excluding_inverse": CORE.fstr(long_value),
            "open_buy_reserved_krw": CORE.fstr(own_reserved),
            "load_krw": CORE.fstr(load),
            "cap_fraction": env_market["cap_fraction"],
            "effective_state": env_market["effective_state"],
            "new_buys": env_market["new_buys"],
            "cap_krw": CORE.opt_fstr(cap),
            "room_krw": CORE.opt_fstr(room),
            "room_fraction": CORE.fstr(room_fraction),
            "available_cash_krw": CORE.fstr(available_cash),
            "budget_krw": CORE.opt_fstr(budget),
        },
        "allocation": lines,
        "allocated_krw": CORE.fstr(allocated),
        "planned_submitted_krw": CORE.fstr(total_submitted),
        "unallocated_krw": CORE.fstr((budget or Fraction(0)) - allocated),
        "carry_over": core.interpretations["water_filling"]["carry_over"],
        "status": "ALLOCATED" if allocated > 0 else "NO_ALLOCATION",
        "reasons": sorted(set(reasons)),
        "config_sha256": core.config_sha256,
        "envelope_record_sha256": envelope.get("record_sha256"),
        "rule_refs": core.rule_refs(pairs, decision_at_utc),
    }
    return CORE.sign(record, "record_sha256")


def core_root(core):
    return core.root


def validate_session_budget_record(record: dict, *, root=CORE.ROOT) -> dict:
    """Re-derive from embedded inputs under the registry snapshot the record names."""
    CORE.verify_signed(record, "record_sha256", "SESSION_BUDGET_RECORD_SHA_MISMATCH")
    if record.get("schema_version") != RECORD_SCHEMA_VERSION:
        CORE.fail("SESSION_BUDGET_RECORD_SCHEMA_INVALID")
    core = CORE.load_core_for_record(record, root)
    rebuilt = build_session_budget_record(
        core, market=record["key"]["market"], session_id=record["key"]["session_id"],
        decision_at_utc=record["decision_at_utc"], **record["inputs"])
    if rebuilt != record:
        CORE.fail("SESSION_BUDGET_RECORD_NOT_REDERIVABLE")
    return rebuilt


def _key_tuple(key: dict) -> tuple:
    return (key["market"], key["session_id"], key["rule_id"], key["rule_version"])


class SessionBudgetLedger:
    """Append-only session budget events: one allocation per session, consume by submission."""

    def __init__(self, core, events=None):
        self.core = core
        self.events = []
        self._records = {}
        self._consumed = {}
        for event in events or []:
            self._apply(copy.deepcopy(event))

    def _apply(self, event: dict) -> str:
        CORE.verify_signed(event, "event_sha256", "LEDGER_EVENT_SHA_MISMATCH")
        kind = event.get("event_type")
        if kind == "SESSION_BUDGET_RECORDED":
            record = validate_session_budget_record(event["record"], root=core_root(self.core))
            key = _key_tuple(record["key"])
            existing = self._records.get(key)
            if existing is not None:
                if existing != record:
                    CORE.fail("SESSION_BUDGET_KEY_CONFLICT", ":".join(map(str, key)))
                return "UNCHANGED_SAME_RECORD"
            self._records[key] = record
            self._consumed[key] = {}
        elif kind == "BUDGET_CONSUMED":
            key = _key_tuple(event["key"])
            if key not in self._records:
                CORE.fail("CONSUME_WITHOUT_SESSION_BUDGET_RECORD")
            order_id = CORE.require_token(event["order_id"], "order_id")
            amount = CORE.frac(event["submitted_amount_krw"], "submitted_amount_krw")
            if amount <= 0:
                CORE.fail("SUBMITTED_AMOUNT_NOT_POSITIVE")
            orders = self._consumed[key]
            if order_id in orders:
                if orders[order_id] != amount:
                    CORE.fail("ORDER_ID_CONFLICT", order_id)
                return "UNCHANGED_SAME_ORDER"
            budget = (CORE.opt_frac(self._records[key]["market_room"]["budget_krw"], "budget_krw") or Fraction(0))
            if sum(orders.values(), Fraction(0)) + amount > budget:
                CORE.fail("SESSION_BUDGET_EXCEEDED", order_id)
            orders[order_id] = amount
        elif kind == "ORDER_CANCELLED_OR_EXPIRED":
            key = _key_tuple(event["key"])
            if event["order_id"] not in self._consumed.get(key, {}):
                CORE.fail("CANCEL_UNKNOWN_ORDER")
            # canon 2-3: cancellation/expiry never restores this session's budget.
        else:
            CORE.fail("LEDGER_EVENT_TYPE_INVALID", str(kind))
        self.events.append(event)
        return "APPENDED"

    def _event(self, payload: dict) -> str:
        return self._apply(CORE.sign({"schema_version": LEDGER_EVENT_SCHEMA_VERSION, **payload}, "event_sha256"))

    def record_for(self, market: str, session_id: str):
        row = self.core.context.rules[RULE_SIZE]
        return copy.deepcopy(self._records.get((market, session_id, RULE_SIZE, row["version"])))

    def allocate_or_reuse(self, **inputs) -> tuple:
        """Restart-safe: an existing record for the session is returned, never recomputed."""
        existing = self.record_for(inputs["market"], inputs["session_id"])
        if existing is not None:
            return existing, True
        record = build_session_budget_record(self.core, **inputs)
        self._event({"event_type": "SESSION_BUDGET_RECORDED", "record": record})
        return record, False

    def consume(self, record: dict, order_id: str, submitted_amount_krw: str) -> str:
        return self._event({"event_type": "BUDGET_CONSUMED", "key": record["key"], "order_id": order_id,
                            "submitted_amount_krw": submitted_amount_krw})

    def cancel(self, record: dict, order_id: str) -> str:
        return self._event({"event_type": "ORDER_CANCELLED_OR_EXPIRED", "key": record["key"], "order_id": order_id})

    def remaining_krw(self, record: dict) -> str:
        key = _key_tuple(record["key"])
        budget = (CORE.opt_frac(self._records[key]["market_room"]["budget_krw"], "budget_krw") or Fraction(0))
        return CORE.fstr(budget - sum(self._consumed[key].values(), Fraction(0)))
