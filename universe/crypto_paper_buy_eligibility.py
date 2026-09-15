#!/usr/bin/env python3
"""P5-09 Crypto PAPER Buy Eligibility.

Continues P5-08's per-market state machine one step further, over
already-produced, already-revalidated evidence only:

    FOCUSED_REVIEW (P5-08)
        -> WATCH              (a gating criterion is UNKNOWN, none FAILED)
        -> BLOCKED            (a gating criterion FAILED)
        -> WAIT               (every gating criterion PASSED, but the order
                                 draft -- entry/invalidation/stop/quantity/
                                 fee/slippage/expiry/duplicate-guard-key --
                                 is not yet fully computable)
        -> PAPER_BUY_ELIGIBLE (every gating criterion PASSED AND the order
                                 draft is complete: no null/UNKNOWN field)

This module never captures anything and never calls an Upbit order,
withdrawal, or any private endpoint -- it is a pure derivation over an
already-built, already-validated P5-08 ``crypto_candidate_promotion_packet``.
It consumes ONLY that packet's ``FOCUSED_REVIEW`` rows, per the literal
funnel ``Universe -> Focused Review -> PAPER_READY`` in the canonical Crypto
policy doc. It revalidates the whole P5-08 packet via
``universe/crypto_candidate_promotion.py::validate_output`` before reading
anything out of it -- a caller cannot fabricate a FOCUSED_REVIEW row by
hand-editing a cached packet.

--------------------------------------------------------------------------
Nine buy-criteria (matching the canonical policy doc's own numbered list
1-9, "매수 기준 -- 다음이 전부 충족될 때만 PAPER_BUY_ELIGIBLE"). Every
criterion is PASS/FAIL/UNKNOWN with an explicit reason; see
docs/crypto_paper_buy_eligibility_contract.md for the full per-criterion
ratified-vs-UNKNOWN-by-construction table. Short version:

  1 FOCUSED_REVIEW_UPSTREAM       ratified/deterministic -- echoes P5-08's
                                   already-revalidated promotion_state.
  2 REGIME_PERMITS_ENTRY          UNKNOWN by construction -- echoes P1-CR-08/
                                   P5-08's own REGIME criterion; the Regime
                                   aggregate authorizes only "UNKNOWN" today.
  3 TRIGGER_TIMEFRAME_ALIGNMENT   mechanical/PAPER-baseline -- needs
                                   finalized 15m+1h candles for the trigger
                                   and non-conflicting 4h/1d direction.
                                   UNKNOWN when 15m/1h evidence is missing
                                   (structurally true in production today:
                                   P3-12's universe is empty, so P4-07 never
                                   captures any market's microstructure).
  4 BREAKOUT_OR_PULLBACK          mechanical/PAPER-baseline for Breakout
                                   (20x 1h-bar high + volume/median >= 1.5x,
                                   from PROPOSED_PAPER_BASELINE); Pullback
                                   stays UNKNOWN forever -- the policy doc's
                                   own "EMA20 부근" (near EMA20) has no
                                   numeric tolerance anywhere, ratified or
                                   proposed, so it is never fabricated. A
                                   disjunctive criterion with an
                                   undecidable leg resolves UNKNOWN, not
                                   FAIL, when the decidable leg (Breakout)
                                   does not itself PASS.
  5 INDEPENDENT_PRICE_VOLUME_     ratified/deterministic -- structural
    EVIDENCE                      presence only (candle family present AND
                                   orderbook+trades family present), same
                                   discipline as P5-08's VOLUME_LIQUIDITY.
  6 NO_BLOCKER_STALE_OVERHEAT_    composite worst-of four sub-checks:
    DUPLICATE                     MATERIAL_BLOCKER (echoes P5-08 exactly),
                                   OVEREXTENSION (UNKNOWN, echoes P5-08 --
                                   no ratified "과열" definition anywhere),
                                   FRESHNESS (UNKNOWN while P4-07's policy
                                   is unratified; FAIL for any STALE input
                                   once that policy is ratified), DUPLICATE
                                   (PASS/FAIL only when a prior-keys ledger
                                   is supplied by the caller, else UNKNOWN).
  7 ORDER_DRAFT_COMPLETE          PASS/UNKNOWN only (never FAIL) -- whether
                                   entry/invalidation/stop/quantity/fee/
                                   slippage/expiry/duplicate-guard-key are
                                   ALL non-null. Does not participate in the
                                   BLOCKED/WATCH gate; it only selects
                                   WAIT vs PAPER_BUY_ELIGIBLE once every
                                   other criterion has PASSED.
  8 PAPER_RISK_BUDGET             mechanical/PAPER-baseline against a
                                   caller-supplied virtual PAPER account
                                   snapshot; total exposure uses existing
                                   portfolio weights, never planned-loss
                                   fractions; UNKNOWN when no snapshot is
                                   supplied (no NAV is known).
  9 ZERO_ORDER_ENDPOINT_CALLS     constant PASS -- a structural invariant
                                   of this module (no network import
                                   anywhere in this file), not a per-
                                   candidate fact.

--------------------------------------------------------------------------
On the PROPOSED_PAPER_BASELINE numbers (criteria 3/4/8's thresholds): the
canonical Crypto policy doc gives these as an explicit, versioned,
"effective" PAPER-only comparison baseline -- distinct in kind from P3-12's/
P4-07's own still-`PROPOSED_UNRATIFIED`/`PROPOSED_PAPER_BASELINE_UNRATIFIED`
internal operational policies, and explicitly NOT a live-capital limit (the
policy doc's own words: "이 숫자들은 PAPER 비교용이지 실거래 한도가
아니다"). This module treats that doc as the deterministic source for those
specific numbers and encodes them in
``config/crypto_paper_buy_eligibility_policy.json`` under the
``PROPOSED_PAPER_BASELINE`` label, reusing P3-12's own spread/slippage caps
(``max_spread_bps``/``max_estimated_paper_slippage_bps``) directly rather
than re-deriving them, since they are numerically identical to the policy
doc's 0.20%/0.30% figures. Using them for PAPER-simulation-only arithmetic
never grants investment, entry, Stage, order, Production, or Trading
authority -- every output row's authority block stays hardcoded all-`false`,
exactly like every other module in this pipeline. A criterion with
genuinely NO numeric source anywhere (OVEREXTENSION, the Pullback leg's
EMA20 proximity tolerance, P4-07's own staleness policy) is never guessed
into a threshold; it stays UNKNOWN.

Determinism: every function here is a pure function of its arguments -- no
wall-clock or random value is read inside any derivation. No result-
shopping: thresholds are fixed in the loaded policy file before evaluation
and are never adjusted after seeing an outcome. Embedded policies are
exactly re-pinned to that repository file at every build/validation
boundary, so changing a threshold and recomputing the packet hash cannot
create a valid result.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_DOWN, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoPaperBuyEligibilityError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CryptoPaperBuyEligibilityError(ValueError):
    """Fail-closed P5-09 PAPER buy-eligibility contract violation."""


PROMOTION = _load("crypto_paper_buy_eligibility_promotion", "universe/crypto_candidate_promotion.py")
UPBIT_UNIVERSE = PROMOTION.UPBIT_UNIVERSE
CANDLE_FINALIZATION = _load("crypto_paper_buy_eligibility_candle_finalization", "microstructure/upbit_candle_finalization.py")


CONTRACT_PATH = ROOT / "config" / "crypto_paper_buy_eligibility_contract.json"
POLICY_PATH = ROOT / "config" / "crypto_paper_buy_eligibility_policy.json"
OUTPUT_SCHEMA_VERSION = "crypto_paper_buy_eligibility_packet/2"

STATE_WATCH = "WATCH"
STATE_WAIT = "WAIT"
STATE_BLOCKED = "BLOCKED"
STATE_PAPER_BUY_ELIGIBLE = "PAPER_BUY_ELIGIBLE"
ELIGIBILITY_STATES = (STATE_WATCH, STATE_WAIT, STATE_BLOCKED, STATE_PAPER_BUY_ELIGIBLE)

CRITERIA = (
    "FOCUSED_REVIEW_UPSTREAM",
    "REGIME_PERMITS_ENTRY",
    "TRIGGER_TIMEFRAME_ALIGNMENT",
    "BREAKOUT_OR_PULLBACK",
    "INDEPENDENT_PRICE_VOLUME_EVIDENCE",
    "NO_BLOCKER_STALE_OVERHEAT_DUPLICATE",
    "ORDER_DRAFT_COMPLETE",
    "PAPER_RISK_BUDGET",
    "ZERO_ORDER_ENDPOINT_CALLS",
)
CRITERION_STATUSES = ("PASS", "FAIL", "UNKNOWN")
# ORDER_DRAFT_COMPLETE deliberately does not participate in the BLOCKED/
# WATCH gate -- see module docstring criterion 7.
GATING_CRITERIA = tuple(name for name in CRITERIA if name != "ORDER_DRAFT_COMPLETE")

TRIGGER_TIMEFRAME = "1h"
CONFLICT_TIMEFRAMES = ("4h", "1d")

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")

# Hardcoded, never policy-driven: no evaluator in this module may set any of
# these to true. A PAPER_BUY_ELIGIBLE classification is a deterministic
# eligibility judgment, never Stage/Buy/Action/Order/Production/Trading
# authority.
_ROW_AUTHORITY = {
    "investable_eligible": False,
    "paper_eligible": False,
    "paper_buy_eligible_authorized": False,
    "entry_authorized": False,
    "stage_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
    "exchange_order_authorized": False,
}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperBuyEligibilityError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or value.get("contract_version") != "crypto_paper_buy_eligibility_contract/2"
    ):
        raise CryptoPaperBuyEligibilityError("CONTRACT_FIELD_MISMATCH:contract_version")
    if tuple(value.get("criteria", [])) != CRITERIA:
        raise CryptoPaperBuyEligibilityError("CONTRACT_FIELD_MISMATCH:criteria")
    if tuple(value.get("criterion_statuses", [])) != CRITERION_STATUSES:
        raise CryptoPaperBuyEligibilityError("CONTRACT_FIELD_MISMATCH:criterion_statuses")
    if tuple(value.get("eligibility_states", [])) != ELIGIBILITY_STATES:
        raise CryptoPaperBuyEligibilityError("CONTRACT_FIELD_MISMATCH:eligibility_states")
    for key, expected in value.get("authority", {}).items():
        if expected is not False:
            raise CryptoPaperBuyEligibilityError(f"CONTRACT_AUTHORITY_NOT_FALSE:{key}")
    if set(value.get("authority", {})) != set(_ROW_AUTHORITY):
        raise CryptoPaperBuyEligibilityError("CONTRACT_FIELD_MISMATCH:authority_keys")
    return copy.deepcopy(value)


def load_policy(path: Path = POLICY_PATH) -> dict:
    value = _read_json(Path(path))
    required = {
        "schema_version", "policy_version", "baseline_label", "baseline_version",
        "approval_status", "effective_date", "not_a_live_capital_limit",
        "source_reference", "decimal_scale", "trend", "breakout", "risk",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise CryptoPaperBuyEligibilityError("POLICY_FIELDS_INVALID")
    if value.get("schema_version") != 1:
        raise CryptoPaperBuyEligibilityError("POLICY_SCHEMA_VERSION_INVALID")
    if value.get("policy_version") != "crypto_paper_buy_eligibility_policy/1":
        raise CryptoPaperBuyEligibilityError("POLICY_VERSION_INVALID")
    if value.get("baseline_label") != "PROPOSED_PAPER_BASELINE":
        raise CryptoPaperBuyEligibilityError("POLICY_BASELINE_LABEL_INVALID")
    if value.get("baseline_version") != "v1":
        raise CryptoPaperBuyEligibilityError("POLICY_BASELINE_VERSION_INVALID")
    if value.get("approval_status") != "PROPOSED_PAPER_BASELINE_UNRATIFIED":
        raise CryptoPaperBuyEligibilityError("POLICY_APPROVAL_STATUS_INVALID")
    if not isinstance(value.get("effective_date"), str) or not _DATE_RE.fullmatch(value["effective_date"]):
        raise CryptoPaperBuyEligibilityError("POLICY_EFFECTIVE_DATE_INVALID")
    if value.get("not_a_live_capital_limit") is not True:
        raise CryptoPaperBuyEligibilityError("POLICY_LIVE_CAPITAL_INVARIANT_VIOLATED")
    if not isinstance(value.get("source_reference"), str) or not value["source_reference"].strip():
        raise CryptoPaperBuyEligibilityError("POLICY_SOURCE_REFERENCE_INVALID")
    if not isinstance(value.get("decimal_scale"), int) or not 0 <= value["decimal_scale"] <= 36:
        raise CryptoPaperBuyEligibilityError("POLICY_DECIMAL_SCALE_INVALID")
    trend = value["trend"]
    if not isinstance(trend, dict) or set(trend) != {"ema_period"} or trend["ema_period"] != 20:
        raise CryptoPaperBuyEligibilityError("POLICY_TREND_FIELDS_INVALID")
    breakout = value["breakout"]
    if not isinstance(breakout, dict) or set(breakout) != {"lookback_bars", "volume_ratio_min"}:
        raise CryptoPaperBuyEligibilityError("POLICY_BREAKOUT_FIELDS_INVALID")
    if not isinstance(breakout["lookback_bars"], int) or breakout["lookback_bars"] < 2:
        raise CryptoPaperBuyEligibilityError("POLICY_BREAKOUT_LOOKBACK_INVALID")
    _decimal(breakout["volume_ratio_min"], "POLICY_VOLUME_RATIO_INVALID", positive=True)
    risk = value["risk"]
    required_risk = {
        "per_trade_planned_loss_nav_fraction", "total_crypto_paper_exposure_nav_fraction",
        "single_asset_paper_exposure_nav_fraction", "max_concurrent_paper_positions",
    }
    if not isinstance(risk, dict) or set(risk) != required_risk:
        raise CryptoPaperBuyEligibilityError("POLICY_RISK_FIELDS_INVALID")
    for key in (
        "per_trade_planned_loss_nav_fraction",
        "total_crypto_paper_exposure_nav_fraction",
        "single_asset_paper_exposure_nav_fraction",
    ):
        _decimal(risk[key], f"POLICY_RISK_VALUE_INVALID:{key}", positive=True, maximum=Decimal("1"))
    if not isinstance(risk["max_concurrent_paper_positions"], int) or risk["max_concurrent_paper_positions"] < 1:
        raise CryptoPaperBuyEligibilityError("POLICY_MAX_POSITIONS_INVALID")
    return copy.deepcopy(value)


def _require_repository_policy(policy: dict) -> dict:
    """Pin every consumer to the repository baseline.

    Embedding a policy inside a packet is not authority to replace the
    repository policy.  Without this exact comparison, a caller could alter
    thresholds, rebuild the packet hash, and have ``validate_output``
    reproduce the forged derivation successfully.
    """
    canonical = load_policy(POLICY_PATH)
    if canonical_json(policy) != canonical_json(canonical):
        raise CryptoPaperBuyEligibilityError("POLICY_REPOSITORY_PIN_MISMATCH")
    return copy.deepcopy(canonical)


def _decimal(value, code: str, *, positive: bool = False, maximum: Decimal | None = None) -> Decimal:
    if not isinstance(value, str):
        raise CryptoPaperBuyEligibilityError(code)
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise CryptoPaperBuyEligibilityError(code) from exc
    if not parsed.is_finite() or parsed < 0 or (positive and parsed <= 0):
        raise CryptoPaperBuyEligibilityError(code)
    if maximum is not None and parsed > maximum:
        raise CryptoPaperBuyEligibilityError(code)
    return parsed


def _format_decimal(value: Decimal) -> str:
    if not value.is_finite():
        raise CryptoPaperBuyEligibilityError("DECIMAL_NON_FINITE")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def _floor(value: Decimal, scale: int) -> Decimal:
    quantum = Decimal(1).scaleb(-scale)
    with localcontext() as ctx:
        ctx.prec = max(50, len(value.as_tuple().digits) + scale + 10)
        return value.quantize(quantum, rounding=ROUND_DOWN)


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise CryptoPaperBuyEligibilityError(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperBuyEligibilityError(code) from exc


def _iso_utc(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _criterion(status: str, reason: str, **extra) -> dict:
    if status not in CRITERION_STATUSES:
        raise CryptoPaperBuyEligibilityError(f"CRITERION_STATUS_INVALID:{status}")
    return {"status": status, "reason": reason, **extra}


def _worst_of(statuses: list[str]) -> str:
    if "FAIL" in statuses:
        return "FAIL"
    if "UNKNOWN" in statuses:
        return "UNKNOWN"
    return "PASS"


# ---------------------------------------------------------------------------
# Criterion 1 -- FOCUSED_REVIEW_UPSTREAM
# ---------------------------------------------------------------------------

def evaluate_focused_review_upstream(candidate_row: dict) -> dict:
    """PASS iff the already-revalidated P5-08 row is FOCUSED_REVIEW. This
    module's own callers (``build_eligibility_packet``) only ever pass in
    FOCUSED_REVIEW rows, so the else-branch below is defensive, matching
    P5-08's own ``evaluate_tradability`` discipline for out-of-scope input.
    """
    if candidate_row.get("promotion_state") == "FOCUSED_REVIEW":
        return _criterion("PASS", "P5_08_FOCUSED_REVIEW_REVALIDATED")
    raise CryptoPaperBuyEligibilityError(
        f"CANDIDATE_ROW_OUT_OF_SCOPE:{candidate_row.get('promotion_state')}"
    )


# ---------------------------------------------------------------------------
# Criterion 2 -- REGIME_PERMITS_ENTRY (echoes P5-08's REGIME criterion)
# ---------------------------------------------------------------------------

def evaluate_regime_permits_entry(regime_payload: dict) -> dict:
    result = PROMOTION.evaluate_regime(regime_payload)
    return _criterion(
        result["status"],
        f"P5_08_REGIME_ECHO:{result['reason']}",
    )


# ---------------------------------------------------------------------------
# Criteria 3/4 -- trigger/breakout mechanics
# ---------------------------------------------------------------------------

def _finalized(market_evidence_packet: dict | None, timeframe: str) -> list | None:
    if market_evidence_packet is None:
        return None
    candles = (market_evidence_packet.get("candles") or {}).get(timeframe) or {}
    rows = candles.get("finalized_candles")
    if not isinstance(rows, list):
        return None
    return sorted(rows, key=lambda row: row["close_time"])


def evaluate_trigger_timeframe_alignment(market_evidence_packet: dict | None) -> dict:
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    fifteen_minute = _finalized(market_evidence_packet, "15m")
    trigger = _finalized(market_evidence_packet, TRIGGER_TIMEFRAME)
    four_hour = _finalized(market_evidence_packet, "4h")
    daily = _finalized(market_evidence_packet, "1d")
    fifteen_minute_direction = PROMOTION._direction(fifteen_minute) if fifteen_minute is not None else None
    trigger_direction = PROMOTION._direction(trigger) if trigger is not None else None
    four_hour_direction = PROMOTION._direction(four_hour) if four_hour is not None else None
    daily_direction = PROMOTION._direction(daily) if daily is not None else None
    if (
        fifteen_minute_direction is None
        or trigger_direction is None
        or four_hour_direction is None
        or daily_direction is None
    ):
        return _criterion(
            "UNKNOWN", "INSUFFICIENT_FINALIZED_CANDLES_FOR_TRIGGER",
            fifteen_minute_direction=fifteen_minute_direction,
            trigger_direction=trigger_direction,
            four_hour_direction=four_hour_direction,
            daily_direction=daily_direction,
        )
    if fifteen_minute_direction == "FLAT" or trigger_direction == "FLAT":
        return _criterion(
            "UNKNOWN", "TRIGGER_DIRECTION_FLAT_NO_CONVICTION",
            fifteen_minute_direction=fifteen_minute_direction,
            trigger_direction=trigger_direction,
        )
    conflict = (
        fifteen_minute_direction != "UP"
        or trigger_direction != "UP"
        or four_hour_direction == "DOWN"
        or daily_direction == "DOWN"
    )
    if conflict:
        return _criterion(
            "FAIL", "DIRECTION_CONFLICT_WITH_HIGHER_TIMEFRAME",
            fifteen_minute_direction=fifteen_minute_direction,
            trigger_direction=trigger_direction,
            four_hour_direction=four_hour_direction,
            daily_direction=daily_direction,
        )
    return _criterion(
        "PASS", "TRIGGER_CONFIRMED_NO_HIGHER_TIMEFRAME_CONFLICT",
        fifteen_minute_direction=fifteen_minute_direction,
        trigger_direction=trigger_direction,
        four_hour_direction=four_hour_direction,
        daily_direction=daily_direction,
    )


def _breakout_window(market_evidence_packet: dict | None, lookback_bars: int) -> dict | None:
    """The trigger (most-recent finalized 1h) candle plus its preceding
    ``lookback_bars`` finalized 1h candles, or None when there are not
    enough finalized 1h candles to evaluate Breakout at all.
    """
    trigger = _finalized(market_evidence_packet, TRIGGER_TIMEFRAME)
    if trigger is None or len(trigger) < lookback_bars + 1:
        return None
    trigger_candle = trigger[-1]
    lookback = trigger[-(lookback_bars + 1):-1]
    return {"trigger_candle": trigger_candle, "lookback": lookback}


def evaluate_breakout_or_pullback(market_evidence_packet: dict | None, policy: dict) -> dict:
    lookback_bars = policy["breakout"]["lookback_bars"]
    volume_ratio_min = _decimal(policy["breakout"]["volume_ratio_min"], "POLICY_VOLUME_RATIO_INVALID", positive=True)
    window = _breakout_window(market_evidence_packet, lookback_bars)
    if window is None:
        return _criterion("UNKNOWN", "INSUFFICIENT_1H_CANDLES_FOR_BREAKOUT")
    trigger_candle = window["trigger_candle"]
    lookback = window["lookback"]
    lookback_high = max(Decimal(row["high_price"]) for row in lookback)
    lookback_volumes = [Decimal(row["candle_acc_trade_volume"]) for row in lookback]
    median_volume = _median(lookback_volumes)
    trigger_close = Decimal(trigger_candle["trade_price"])
    trigger_volume = Decimal(trigger_candle["candle_acc_trade_volume"])
    price_broken = trigger_close > lookback_high
    volume_confirmed = median_volume > 0 and trigger_volume >= median_volume * volume_ratio_min
    details = {
        "lookback_bars": lookback_bars,
        "lookback_high": _format_decimal(lookback_high),
        "trigger_close": _format_decimal(trigger_close),
        "trigger_volume": _format_decimal(trigger_volume),
        "median_lookback_volume": _format_decimal(median_volume),
        "volume_ratio_min": _format_decimal(volume_ratio_min),
    }
    if price_broken and volume_confirmed:
        return _criterion("PASS", "BREAKOUT_CONFIRMED", **details)
    return _criterion("UNKNOWN", "BREAKOUT_NOT_TRIGGERED_PULLBACK_PROXIMITY_TOLERANCE_UNRATIFIED", **details)


# ---------------------------------------------------------------------------
# Criterion 5 -- independent price/volume evidence
# ---------------------------------------------------------------------------

def evaluate_independent_price_volume_evidence(market_evidence_packet: dict | None) -> dict:
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    candles = market_evidence_packet.get("candles") or {}
    price_family_present = all(
        bool((candles.get(timeframe) or {}).get("finalized_candle_count"))
        for timeframe in ("15m", "1h", "4h", "1d")
    )
    orderbook = market_evidence_packet.get("orderbook") or {}
    trades = market_evidence_packet.get("trades") or {}
    volume_quote_family_present = (
        orderbook.get("best_bid") is not None
        and orderbook.get("best_ask") is not None
        and bool(trades.get("trade_count"))
    )
    if price_family_present and volume_quote_family_present:
        return _criterion(
            "PASS", "PRICE_AND_VOLUME_QUOTE_EVIDENCE_INDEPENDENTLY_PRESENT",
            price_family_present=True, volume_quote_family_present=True,
        )
    return _criterion(
        "UNKNOWN", "EVIDENCE_FAMILY_INCOMPLETE",
        price_family_present=price_family_present,
        volume_quote_family_present=volume_quote_family_present,
    )


# ---------------------------------------------------------------------------
# Criterion 6 -- composite blocker/stale/overheat/duplicate
# ---------------------------------------------------------------------------

def evaluate_current_evidence_freshness(market_evidence_packet: dict | None) -> dict:
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    if market_evidence_packet.get("policy_ratified") is not True:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_FRESHNESS_POLICY_UNRATIFIED")
    components = {
        **{
            f"candle_{timeframe}": ((market_evidence_packet.get("candles") or {}).get(timeframe) or {}).get("freshness", {}).get("status")
            for timeframe in ("15m", "1h", "4h", "1d")
        },
        "trades": (market_evidence_packet.get("trades") or {}).get("freshness", {}).get("status"),
        "orderbook": (market_evidence_packet.get("orderbook") or {}).get("freshness", {}).get("status"),
    }
    invalid = sorted(name for name, status in components.items() if status not in ("FRESH", "STALE", "UNKNOWN"))
    if invalid:
        return _criterion("UNKNOWN", "FRESHNESS_STATUS_MISSING_OR_INVALID:" + ",".join(invalid), components=components)
    stale = sorted(name for name, status in components.items() if status == "STALE")
    if stale:
        return _criterion("FAIL", "CURRENT_EVIDENCE_STALE:" + ",".join(stale), components=components)
    unknown = sorted(name for name, status in components.items() if status == "UNKNOWN")
    if unknown:
        return _criterion("UNKNOWN", "CURRENT_EVIDENCE_FRESHNESS_UNKNOWN:" + ",".join(unknown), components=components)
    return _criterion("PASS", "ALL_REQUIRED_EVIDENCE_FRESH", components=components)


def evaluate_no_blocker_stale_overheat_duplicate(
    universe_row: dict,
    market_evidence_packet: dict | None,
    duplicate_guard_key: str,
    known_idempotency_keys,
) -> dict:
    material_blocker = PROMOTION.evaluate_material_blocker(universe_row)
    overheat = PROMOTION.evaluate_overextension()
    freshness = evaluate_current_evidence_freshness(market_evidence_packet)
    if known_idempotency_keys is None:
        duplicate = _criterion("UNKNOWN", "DUPLICATE_GUARD_LEDGER_NOT_SUPPLIED")
    elif duplicate_guard_key in known_idempotency_keys:
        duplicate = _criterion("FAIL", "DUPLICATE_GUARD_KEY_ALREADY_PRESENT")
    else:
        duplicate = _criterion("PASS", "DUPLICATE_GUARD_KEY_NOVEL")
    overall = _worst_of([
        material_blocker["status"], overheat["status"], freshness["status"], duplicate["status"],
    ])
    return _criterion(
        overall,
        "COMPOSITE:" + ",".join([
            f"MATERIAL_BLOCKER={material_blocker['status']}",
            f"OVEREXTENSION={overheat['status']}",
            f"FRESHNESS={freshness['status']}",
            f"DUPLICATE={duplicate['status']}",
        ]),
        material_blocker=material_blocker,
        overextension=overheat,
        freshness=freshness,
        duplicate=duplicate,
    )


# ---------------------------------------------------------------------------
# Order draft -- entry/invalidation/stop/quantity/fee/slippage/expiry/
# duplicate-guard-key. Structurally shaped to match P10-11's
# ``shadow/crypto_paper_simulator.py::build_intent`` kwargs (order_id,
# idempotency_key, market, side, order_type, quantity, limit_price,
# fee_rate, submitted_at, expires_at) so wiring a later BUY intent is
# mechanical, not a schema translation exercise.
# ---------------------------------------------------------------------------

def compute_duplicate_guard_key(
    market: str, evaluation_as_of: str, trigger_close_time: str, entry_price: str, invalidation_price: str,
) -> str:
    """Deterministic, stable across reruns of the same evidence; reuses the
    P9-04 ``action_order_idempotency.py`` idempotency-key token shape
    (``^[A-Z0-9][A-Z0-9_.:-]{2,127}$``) so this key can be handed directly
    to that module's ledger without translation.
    """
    basis = canonical_json({
        "market": market,
        "evaluation_as_of": evaluation_as_of,
        "trigger_close_time": trigger_close_time,
        "entry_price": entry_price,
        "invalidation_price": invalidation_price,
    })
    digest = hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24].upper()
    key = f"CRYPTO-PAPER-BUY-{market}-{evaluation_as_of.replace('-', '')}-{digest}"
    if not _TOKEN_RE.fullmatch(key):
        raise CryptoPaperBuyEligibilityError("DUPLICATE_GUARD_KEY_FORMAT_INVALID")
    return key


def _entry_and_invalidation(
    market_evidence_packet: dict | None, policy: dict, universe_policy: dict,
):
    lookback_bars = policy["breakout"]["lookback_bars"]
    window = _breakout_window(market_evidence_packet, lookback_bars)
    if window is None:
        return None
    trigger_candle = window["trigger_candle"]
    lookback = window["lookback"]
    entry_price = Decimal(trigger_candle["trade_price"])
    invalidation_price = min(Decimal(row["low_price"]) for row in lookback)
    if invalidation_price >= entry_price:
        return None
    slippage_bps = _decimal(
        universe_policy["max_estimated_paper_slippage_bps"], "UNIVERSE_POLICY_SLIPPAGE_INVALID", positive=True
    )
    entry_zone_high = entry_price * (Decimal("1") + slippage_bps / Decimal("10000"))
    return {
        "trigger_close_time": trigger_candle["close_time"],
        "entry_price": entry_price,
        "entry_zone_low": entry_price,
        "entry_zone_high": entry_zone_high,
        "invalidation_price": invalidation_price,
        "planned_stop_price": invalidation_price,
        "slippage_bps": slippage_bps,
    }


def _paper_risk(
    entry_price: Decimal, stop_price: Decimal, policy: dict, paper_account_state: dict | None,
    decimal_scale: int,
):
    if paper_account_state is None:
        return None
    total_nav = _decimal(paper_account_state["total_nav_krw"], "PAPER_NAV_INVALID", positive=True)
    open_positions = paper_account_state["open_positions"]
    if not isinstance(open_positions, list):
        raise CryptoPaperBuyEligibilityError("PAPER_OPEN_POSITIONS_INVALID")
    existing_total_loss = sum(
        (_decimal(row["planned_loss_nav_fraction"], "PAPER_POSITION_LOSS_INVALID") for row in open_positions),
        Decimal("0"),
    )
    existing_total_exposure = sum(
        (_decimal(row["portfolio_weight_nav_fraction"], "PAPER_POSITION_EXPOSURE_INVALID") for row in open_positions),
        Decimal("0"),
    )
    per_trade_fraction = _decimal(
        policy["risk"]["per_trade_planned_loss_nav_fraction"], "POLICY_PER_TRADE_LOSS_INVALID", positive=True
    )
    total_cap = _decimal(
        policy["risk"]["total_crypto_paper_exposure_nav_fraction"], "POLICY_TOTAL_CAP_INVALID", positive=True
    )
    single_cap = _decimal(
        policy["risk"]["single_asset_paper_exposure_nav_fraction"], "POLICY_SINGLE_CAP_INVALID", positive=True
    )
    max_positions = policy["risk"]["max_concurrent_paper_positions"]

    planned_loss_krw = total_nav * per_trade_fraction
    stop_distance = entry_price - stop_price
    quantity = _floor(planned_loss_krw / stop_distance, decimal_scale)
    position_notional = quantity * entry_price
    position_weight = position_notional / total_nav

    projected_total_loss = existing_total_loss + per_trade_fraction
    projected_total_exposure = existing_total_exposure + position_weight
    projected_position_count = len(open_positions) + 1
    breaches = []
    if projected_total_exposure > total_cap:
        breaches.append("TOTAL_CRYPTO_PAPER_EXPOSURE_CAP")
    if position_weight > single_cap:
        breaches.append("SINGLE_ASSET_PAPER_EXPOSURE_CAP")
    if projected_position_count > max_positions:
        breaches.append("MAX_CONCURRENT_PAPER_POSITIONS")
    return {
        "quantity": quantity,
        "planned_loss_krw": planned_loss_krw,
        "position_weight_nav_fraction": position_weight,
        "projected_total_planned_loss_nav_fraction": projected_total_loss,
        "projected_total_crypto_exposure_nav_fraction": projected_total_exposure,
        "projected_open_position_count": projected_position_count,
        "breaches": breaches,
    }


def evaluate_paper_risk_budget(
    entry_invalidation: dict | None, policy: dict, paper_account_state: dict | None, decimal_scale: int,
) -> dict:
    if paper_account_state is None:
        return _criterion("UNKNOWN", "PAPER_ACCOUNT_STATE_NOT_SUPPLIED")
    if entry_invalidation is None:
        return _criterion("UNKNOWN", "ENTRY_INVALIDATION_NOT_COMPUTABLE")
    risk = _paper_risk(
        entry_invalidation["entry_price"], entry_invalidation["planned_stop_price"],
        policy, paper_account_state, decimal_scale,
    )
    if risk["breaches"]:
        return _criterion("FAIL", "PAPER_RISK_BUDGET_BREACH:" + ",".join(risk["breaches"]))
    return _criterion(
        "PASS", "PAPER_RISK_BUDGET_WITHIN_PROPOSED_PAPER_BASELINE",
        projected_total_planned_loss_nav_fraction=_format_decimal(risk["projected_total_planned_loss_nav_fraction"]),
        projected_total_crypto_exposure_nav_fraction=_format_decimal(risk["projected_total_crypto_exposure_nav_fraction"]),
        projected_open_position_count=risk["projected_open_position_count"],
    )


def build_order_draft(
    market: str, market_evidence_packet: dict | None, policy: dict, universe_policy: dict,
    *, evaluation_as_of: str, paper_account_state: dict | None, fee_rate: str | None,
) -> dict:
    """Every field non-null iff a genuine PAPER_BUY_ELIGIBLE row is
    reachable. Any missing input collapses individual fields to ``None``
    rather than raising -- the caller (``evaluate_candidate``) reads
    completeness off this dict, it never guesses a substitute value.
    """
    policy = _require_repository_policy(policy)
    decimal_scale = policy["decimal_scale"]
    entry_invalidation = _entry_and_invalidation(market_evidence_packet, policy, universe_policy)
    if entry_invalidation is None:
        return {
            "entry_zone": None, "invalidation_price": None, "planned_stop_price": None,
            "quantity": None, "fee_rate": None, "fee_amount_krw": None,
            "assumed_slippage_bps": None, "planned_loss_krw": None,
            "expires_at": None, "next_review_at": None, "duplicate_guard_key": None,
            "entry_invalidation": None,
        }
    duplicate_guard_key = compute_duplicate_guard_key(
        market, evaluation_as_of, entry_invalidation["trigger_close_time"],
        _format_decimal(entry_invalidation["entry_price"]),
        _format_decimal(entry_invalidation["invalidation_price"]),
    )
    risk = _paper_risk(
        entry_invalidation["entry_price"], entry_invalidation["planned_stop_price"],
        policy, paper_account_state, decimal_scale,
    ) if paper_account_state is not None else None
    quantity = risk["quantity"] if risk is not None else None
    planned_loss_krw = risk["planned_loss_krw"] if risk is not None else None
    fee_amount = None
    fee_rate_decimal = None
    if fee_rate is not None and quantity is not None:
        fee_rate_decimal = _decimal(fee_rate, "FEE_RATE_INVALID", positive=True, maximum=Decimal("1"))
        fee_amount = _floor(quantity * entry_invalidation["entry_price"] * fee_rate_decimal, decimal_scale)
    if quantity is not None and quantity <= 0:
        quantity = None
        fee_amount = None

    trigger_close = _parse_utc(entry_invalidation["trigger_close_time"], "TRIGGER_CLOSE_TIME_INVALID")
    unit_seconds = CANDLE_FINALIZATION.TIMEFRAMES[TRIGGER_TIMEFRAME]["unit_seconds"]
    expires_at = trigger_close + dt.timedelta(seconds=unit_seconds)

    return {
        "entry_zone": {
            "low": _format_decimal(entry_invalidation["entry_zone_low"]),
            "high": _format_decimal(entry_invalidation["entry_zone_high"]),
        },
        "invalidation_price": _format_decimal(entry_invalidation["invalidation_price"]),
        "planned_stop_price": _format_decimal(entry_invalidation["planned_stop_price"]),
        "quantity": _format_decimal(quantity) if quantity is not None else None,
        "fee_rate": _format_decimal(fee_rate_decimal) if fee_rate_decimal is not None else None,
        "fee_amount_krw": _format_decimal(fee_amount) if fee_amount is not None else None,
        "assumed_slippage_bps": _format_decimal(entry_invalidation["slippage_bps"]),
        "planned_loss_krw": _format_decimal(planned_loss_krw) if planned_loss_krw is not None else None,
        "expires_at": _iso_utc(expires_at),
        "next_review_at": _iso_utc(expires_at),
        "duplicate_guard_key": duplicate_guard_key,
        "entry_invalidation": entry_invalidation,
    }


_ORDER_DRAFT_REQUIRED_FIELDS = (
    "entry_zone", "invalidation_price", "planned_stop_price", "quantity",
    "fee_rate", "fee_amount_krw", "assumed_slippage_bps", "planned_loss_krw",
    "expires_at", "next_review_at", "duplicate_guard_key",
)


def evaluate_order_draft_complete(order_draft: dict) -> dict:
    missing = [field for field in _ORDER_DRAFT_REQUIRED_FIELDS if order_draft.get(field) is None]
    if missing:
        return _criterion("UNKNOWN", "ORDER_DRAFT_FIELDS_MISSING:" + ",".join(missing), missing_fields=missing)
    return _criterion("PASS", "ORDER_DRAFT_COMPLETE_NO_NULL_FIELDS")


def evaluate_zero_order_endpoint_calls() -> dict:
    return _criterion("PASS", "MODULE_MAKES_ZERO_UPBIT_ORDER_ENDPOINT_CALLS_BY_CONSTRUCTION")


# ---------------------------------------------------------------------------
# State-machine transition rule
# ---------------------------------------------------------------------------

def aggregate_state(criteria: dict) -> tuple[str, str]:
    if set(criteria) != set(CRITERIA):
        raise CryptoPaperBuyEligibilityError(f"CRITERIA_SET_INVALID:{sorted(criteria)}")
    gating = {name: criteria[name] for name in GATING_CRITERIA}
    failed = sorted(name for name, result in gating.items() if result["status"] == "FAIL")
    if failed:
        return STATE_BLOCKED, "GATING_CRITERIA_FAILED:" + ",".join(failed)
    unknown = sorted(name for name, result in gating.items() if result["status"] == "UNKNOWN")
    if unknown:
        return STATE_WATCH, "GATING_CRITERIA_UNKNOWN:" + ",".join(unknown)
    if criteria["ORDER_DRAFT_COMPLETE"]["status"] == "PASS":
        return STATE_PAPER_BUY_ELIGIBLE, "ALL_GATING_CRITERIA_PASSED_ORDER_DRAFT_COMPLETE"
    return STATE_WAIT, "ALL_GATING_CRITERIA_PASSED_ORDER_DRAFT_INCOMPLETE"


# ---------------------------------------------------------------------------
# Per-candidate evaluation
# ---------------------------------------------------------------------------

def evaluate_candidate(
    candidate_row: dict,
    *,
    regime_payload: dict,
    market_evidence_packet: dict | None,
    universe_row: dict,
    policy: dict,
    universe_policy: dict,
    evaluation_as_of: str,
    paper_account_state: dict | None = None,
    fee_rate: str | None = None,
    known_idempotency_keys=None,
) -> dict:
    market = candidate_row["market"]
    if universe_row["market"] != market:
        raise CryptoPaperBuyEligibilityError(f"UNIVERSE_ROW_MARKET_MISMATCH:{market}")

    order_draft = build_order_draft(
        market, market_evidence_packet, policy, universe_policy,
        evaluation_as_of=evaluation_as_of, paper_account_state=paper_account_state, fee_rate=fee_rate,
    )
    duplicate_guard_key = order_draft["duplicate_guard_key"] or compute_duplicate_guard_key(
        market, evaluation_as_of, "NOT_COMPUTABLE", "NOT_COMPUTABLE", "NOT_COMPUTABLE",
    )

    criteria = {
        "FOCUSED_REVIEW_UPSTREAM": evaluate_focused_review_upstream(candidate_row),
        "REGIME_PERMITS_ENTRY": evaluate_regime_permits_entry(regime_payload),
        "TRIGGER_TIMEFRAME_ALIGNMENT": evaluate_trigger_timeframe_alignment(market_evidence_packet),
        "BREAKOUT_OR_PULLBACK": evaluate_breakout_or_pullback(market_evidence_packet, policy),
        "INDEPENDENT_PRICE_VOLUME_EVIDENCE": evaluate_independent_price_volume_evidence(market_evidence_packet),
        "NO_BLOCKER_STALE_OVERHEAT_DUPLICATE": evaluate_no_blocker_stale_overheat_duplicate(
            universe_row, market_evidence_packet, duplicate_guard_key, known_idempotency_keys,
        ),
        "ORDER_DRAFT_COMPLETE": evaluate_order_draft_complete(order_draft),
        "PAPER_RISK_BUDGET": evaluate_paper_risk_budget(
            order_draft["entry_invalidation"], policy, paper_account_state, policy["decimal_scale"],
        ),
        "ZERO_ORDER_ENDPOINT_CALLS": evaluate_zero_order_endpoint_calls(),
    }
    state, reason = aggregate_state(criteria)
    published_draft = {key: value for key, value in order_draft.items() if key != "entry_invalidation"}
    if state != STATE_PAPER_BUY_ELIGIBLE:
        # Never publish a partially-filled draft under a non-eligible state
        # -- avoids any appearance of an order-ready row that isn't.
        published_draft = {key: None for key in published_draft}
    return {
        "market": market,
        "canonical_asset_id": candidate_row.get("canonical_asset_id"),
        "p5_08_promotion_state": candidate_row["promotion_state"],
        "criteria": criteria,
        "eligibility_state": state,
        "eligibility_reason": reason,
        "order_draft": published_draft,
        "authority": dict(_ROW_AUTHORITY),
    }


# ---------------------------------------------------------------------------
# Packet assembly
# ---------------------------------------------------------------------------

def build_eligibility_packet(
    promotion_packet: dict,
    *,
    evaluation_as_of: str,
    policy: dict | None = None,
    paper_account_state: dict | None = None,
    fee_rate: str | None = None,
    known_idempotency_keys=None,
) -> dict:
    """Pure derivation over an already-built, already-timestamped P5-08
    promotion packet. Revalidates that packet completely (
    ``crypto_candidate_promotion.py::validate_output``) before reading
    anything from it, then evaluates every ``FOCUSED_REVIEW`` row only.
    """
    if not _DATE_RE.fullmatch(evaluation_as_of):
        raise CryptoPaperBuyEligibilityError("EVALUATION_AS_OF_INVALID")
    policy = _require_repository_policy(load_policy() if policy is None else policy)
    validated_promotion = PROMOTION.validate_output(promotion_packet)
    if validated_promotion["evaluation_as_of"] != evaluation_as_of:
        raise CryptoPaperBuyEligibilityError("EVALUATION_AS_OF_MISMATCH")

    sources = validated_promotion["source_packets"]
    universe_packet = sources["universe"]
    universe_policy = UPBIT_UNIVERSE.load_policy()
    if universe_packet["policy_version"] != universe_policy.get("policy_version"):
        raise CryptoPaperBuyEligibilityError("UNIVERSE_POLICY_PIN_MISMATCH")
    universe_by_market = {row["market"]: row for row in universe_packet["markets"]}
    market_evidence_by_market = sources["market_evidence_by_market"]
    regime_payload = sources["regime"]

    rows = []
    for candidate in validated_promotion["candidates"]:
        if candidate["promotion_state"] != "FOCUSED_REVIEW":
            continue
        market = candidate["market"]
        rows.append(
            evaluate_candidate(
                candidate,
                regime_payload=regime_payload,
                market_evidence_packet=market_evidence_by_market.get(market),
                universe_row=universe_by_market[market],
                policy=policy,
                universe_policy=universe_policy,
                evaluation_as_of=evaluation_as_of,
                paper_account_state=paper_account_state,
                fee_rate=fee_rate,
                known_idempotency_keys=known_idempotency_keys,
            )
        )

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": load_contract()["contract_version"],
        "evaluation_as_of": evaluation_as_of,
        "policy_version": policy["policy_version"],
        "promotion_packet_sha256": validated_promotion["payload_sha256"],
        "focused_review_input_count": sum(
            1 for c in validated_promotion["candidates"] if c["promotion_state"] == "FOCUSED_REVIEW"
        ),
        "candidates": rows,
        "summary": {
            "candidate_count": len(rows),
            "watch_count": sum(1 for r in rows if r["eligibility_state"] == STATE_WATCH),
            "wait_count": sum(1 for r in rows if r["eligibility_state"] == STATE_WAIT),
            "blocked_count": sum(1 for r in rows if r["eligibility_state"] == STATE_BLOCKED),
            "paper_buy_eligible_count": sum(1 for r in rows if r["eligibility_state"] == STATE_PAPER_BUY_ELIGIBLE),
        },
        "authority": dict(_ROW_AUTHORITY),
        "source": {
            "promotion_packet": copy.deepcopy(promotion_packet),
            "policy": copy.deepcopy(policy),
            "paper_account_state": copy.deepcopy(paper_account_state),
            "fee_rate": fee_rate,
            "known_idempotency_keys": sorted(known_idempotency_keys) if known_idempotency_keys is not None else None,
        },
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_output(packet: dict) -> dict:
    """Re-validate the embedded source and reproduce the full derivation.
    Rehashing a modified cached state or criterion cannot make it valid.
    """
    expected_keys = {
        "schema_version", "contract_version", "evaluation_as_of", "policy_version",
        "promotion_packet_sha256", "focused_review_input_count", "candidates",
        "summary", "authority", "source", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoPaperBuyEligibilityError("OUTPUT_SCHEMA_MISMATCH")
    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CryptoPaperBuyEligibilityError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    contract = load_contract()
    if packet.get("contract_version") != contract["contract_version"]:
        raise CryptoPaperBuyEligibilityError("OUTPUT_CONTRACT_VERSION_MISMATCH")
    claimed = packet.get("payload_sha256")
    if not isinstance(claimed, str) or not _SHA_RE.fullmatch(claimed):
        raise CryptoPaperBuyEligibilityError("PAYLOAD_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        raise CryptoPaperBuyEligibilityError("PAYLOAD_SHA256_MISMATCH")
    source = packet["source"]
    known_keys = set(source["known_idempotency_keys"]) if source["known_idempotency_keys"] is not None else None
    rebuilt = build_eligibility_packet(
        source["promotion_packet"],
        evaluation_as_of=packet["evaluation_as_of"],
        policy=source["policy"],
        paper_account_state=source["paper_account_state"],
        fee_rate=source["fee_rate"],
        known_idempotency_keys=known_keys,
    )
    if canonical_json(rebuilt) != canonical_json(packet):
        raise CryptoPaperBuyEligibilityError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


# ===========================================================================
# Contract/3 (crypto PAPER wiring v2, build plan PR3 item 2)
# ===========================================================================
#
# ``crypto_paper_buy_eligibility_contract/3`` consumes a
# ``crypto_candidate_promotion_packet/3`` that carries the CRYPTO_PAPER_
# RUNTIME_V1 decision and the crypto rotation confirmation packet.  Contract/2
# above is unchanged and stays the default (``build_eligibility_packet``).
#
# * Entry follows RULE.ENTRY.PAPER_BASELINE_B.V1: market state permits new
#   buys, the bucket is STRONG_CONFIRMED/STRONG_HELD and the six T2 conditions
#   pass (all carried by the upstream FOCUSED_REVIEW row); EMA20 / breakout /
#   chase / ATR features are record-only.  The contract/2 criteria 3-6
#   (trigger/timeframe alignment, breakout, independent evidence, freshness,
#   material blocker, overextension) are therefore emitted under
#   ``record_only_features`` and never change the state.
# * Size is the session budget from the PAPER execution core
#   (``portfolio/paper_session_budget.py``, RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2):
#   the draft quantity is the recorded allocation line; no planned-loss sizing.
# * Planned loss is record-only (RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1): the
#   draft carries quantity x DS5 ATR multiple x Wilder ATR14 when computable,
#   UNKNOWN otherwise, and it is never a gate.
# * Expiry is the end of the crypto decision cycle (next 07:00Z,
#   RULE.EXEC.TIME_CONTRACT.V1; canon 2-3).
# * The duplicate guard key is the R1 key (market, decision packet id,
#   instrument) of RULE.EXEC.REENTRY.V1, and D7 re-entry after a release sell
#   or the crypto 21-day time stop needs a new strength confirmation
#   (``portfolio/paper_position_episode.reentry_decision``).

CONTRACT_V3_PATH = ROOT / "config" / "crypto_paper_buy_eligibility_contract_v3.json"
OUTPUT_SCHEMA_VERSION_V3 = "crypto_paper_buy_eligibility_packet/3"
CRITERIA_V3 = (
    "FOCUSED_REVIEW_UPSTREAM",
    "REGIME_PERMITS_ENTRY",
    "ROTATION_MEMBERSHIP",
    "REENTRY_PERMITTED",
    "DUPLICATE_GUARD",
    "ORDER_DRAFT_COMPLETE",
    "ZERO_ORDER_ENDPOINT_CALLS",
)
GATING_CRITERIA_V3 = tuple(name for name in CRITERIA_V3 if name != "ORDER_DRAFT_COMPLETE")
RECORD_ONLY_FEATURES_V3 = (
    "TRIGGER_TIMEFRAME_ALIGNMENT",
    "BREAKOUT_OR_PULLBACK",
    "INDEPENDENT_PRICE_VOLUME_EVIDENCE",
    "CURRENT_EVIDENCE_FRESHNESS",
    "MATERIAL_BLOCKER",
    "OVEREXTENSION",
)
ORDER_DRAFT_V3_FIELDS = (
    "session_id", "session_budget_record_sha256", "allocated_krw", "planning_price_krw",
    "quantity", "fee_rate", "submitted_amount_krw", "expires_at", "next_review_at",
    "duplicate_guard_key", "reentry_key", "planned_loss",
)
_ORDER_DRAFT_V3_REQUIRED = (
    "session_id", "session_budget_record_sha256", "allocated_krw", "planning_price_krw",
    "quantity", "fee_rate", "submitted_amount_krw", "expires_at", "next_review_at",
    "duplicate_guard_key", "reentry_key",
)
RULE_ENTRY_B = "RULE.ENTRY.PAPER_BASELINE_B.V1"
RULE_SESSION_BUDGET = "RULE.SIZE.SESSION_BUDGET_ASSEMBLY.V2"
RULE_REENTRY = "RULE.EXEC.REENTRY.V1"
RULE_TIME_CONTRACT = "RULE.EXEC.TIME_CONTRACT.V1"
RULE_PLANNED_LOSS = "RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1"
CRYPTO_MARKET = "CRYPTO"

_V3_MODULES: dict = {}


def _v3_modules() -> dict:
    """Execution-core modules, loaded lazily so contract/2 imports stay unchanged."""
    if not _V3_MODULES:
        from portfolio import paper_execution_core as core_module
        from portfolio import paper_position_episode as episode_module
        from portfolio import paper_session_budget as budget_module
        shadow = _load("crypto_paper_buy_eligibility_shadow_controls", "portfolio/paper_shadow_controls.py")
        _V3_MODULES.update(CORE=core_module, EPISODE=episode_module, BUDGET=budget_module, SHADOW=shadow)
    return _V3_MODULES


def load_contract_v3(path: Path = CONTRACT_V3_PATH) -> dict:
    value = _read_json(Path(path))
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 3
        or value.get("contract_version") != "crypto_paper_buy_eligibility_contract/3"
        or value.get("input_promotion_contract") != "crypto_candidate_promotion_contract/3"
    ):
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_FIELD_MISMATCH:contract_version")
    if tuple(value.get("criteria", [])) != CRITERIA_V3:
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_FIELD_MISMATCH:criteria")
    if tuple(value.get("record_only_features", [])) != RECORD_ONLY_FEATURES_V3:
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_FIELD_MISMATCH:record_only_features")
    if tuple(value.get("criterion_statuses", [])) != CRITERION_STATUSES:
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_FIELD_MISMATCH:criterion_statuses")
    if tuple(value.get("eligibility_states", [])) != ELIGIBILITY_STATES:
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_FIELD_MISMATCH:eligibility_states")
    if tuple(value.get("order_draft_fields", [])) != ORDER_DRAFT_V3_FIELDS:
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_FIELD_MISMATCH:order_draft_fields")
    authority = value.get("authority", {})
    if set(authority) != set(_ROW_AUTHORITY) or any(item is not False for item in authority.values()):
        raise CryptoPaperBuyEligibilityError("CONTRACT_V3_AUTHORITY_NOT_FALSE")
    return copy.deepcopy(value)


def _inline_rule_refs(core, pairs, decision_at_utc: str) -> list:
    """Registry-row ``rule_refs`` (version + source record), in force at the decision.

    ``registry_sha256`` stays null, like P5-08 contract/3, so a packet re-derives
    after registry rows are added; a rule id's version and record never change.
    """
    from governance import rule_registry as registry_module

    registry = core.context.registry
    refs = {}
    for rule_id, role in pairs:
        row = core.context.rules.get(rule_id)
        if row is None:
            raise CryptoPaperBuyEligibilityError(f"RULE_NOT_REGISTERED:{rule_id}")
        if not registry_module.in_force_at(row, decision_at_utc, registry):
            raise CryptoPaperBuyEligibilityError(f"RULE_NOT_IN_FORCE_AT_DECISION:{rule_id}@{decision_at_utc}")
        refs[(rule_id, role)] = {
            "rule_id": rule_id,
            "version": row["version"],
            "registry_sha256": None,
            "source_record_sha256": registry_module.primary_record_sha256(row),
            "role": role,
        }
    return [refs[key] for key in sorted(refs)]


def compute_reentry_key(decision_packet_id: str, instrument: str) -> dict:
    """R1 key of RULE.EXEC.REENTRY.V1 and the order idempotency token derived from it."""
    if not isinstance(decision_packet_id, str) or not _SHA_RE.fullmatch(decision_packet_id):
        raise CryptoPaperBuyEligibilityError("DECISION_PACKET_ID_INVALID")
    key = {"market": CRYPTO_MARKET, "decision_packet_id": decision_packet_id, "instrument": instrument}
    digest = hashlib.sha256(canonical_json(key).encode("utf-8")).hexdigest()[:24].upper()
    token = f"CRYPTO-PAPER-BUY-{instrument}-R1-{digest}"
    if not _TOKEN_RE.fullmatch(token):
        raise CryptoPaperBuyEligibilityError("DUPLICATE_GUARD_KEY_FORMAT_INVALID")
    return {"key": key, "duplicate_guard_key": token}


def _record_only_features(universe_row: dict, market_evidence_packet: dict | None, policy: dict) -> dict:
    return {
        "TRIGGER_TIMEFRAME_ALIGNMENT": evaluate_trigger_timeframe_alignment(market_evidence_packet),
        "BREAKOUT_OR_PULLBACK": evaluate_breakout_or_pullback(market_evidence_packet, policy),
        "INDEPENDENT_PRICE_VOLUME_EVIDENCE": evaluate_independent_price_volume_evidence(market_evidence_packet),
        "CURRENT_EVIDENCE_FRESHNESS": evaluate_current_evidence_freshness(market_evidence_packet),
        "MATERIAL_BLOCKER": PROMOTION.evaluate_material_blocker(universe_row),
        "OVEREXTENSION": PROMOTION.evaluate_overextension(),
    }


def _daily_bars(market_evidence_packet: dict | None) -> list:
    rows = _finalized(market_evidence_packet, "1d") or []
    return [
        {
            "bar_date": row["open_time"][:10], "close_at": row["close_time"],
            "high": row["high_price"], "low": row["low_price"], "close": row["trade_price"],
        }
        for row in rows
    ]


def planned_loss_record(quantity: str | None, market_evidence_packet: dict | None, decision_at_utc: str) -> dict:
    """RULE.RISK.PLANNED_LOSS_RECORD_ONLY.V1: record, never a gate (build plan
    section 9 CIO note 4: stop distance = DS5 ATR multiple x Wilder ATR14)."""
    modules = _v3_modules()
    shadow = modules["SHADOW"]
    if "EXIT_POLICY" not in modules:
        modules["EXIT_POLICY"] = shadow.EXIT.load_policy()
    policy = modules["EXIT_POLICY"]
    multiple = policy["config"]["rules"]["shadow_controls"]["controls"]["DS5"]["atr_multiple"]
    atr = shadow.wilder_atr14(policy, _daily_bars(market_evidence_packet), decision_at_utc)
    base = {
        "role": "RECORD_ONLY",
        "basis": "QUANTITY_X_DS5_ATR_MULTIPLE_X_WILDER_ATR14",
        "atr_multiple": multiple,
        "atr14": atr["atr14"],
        "atr_status": atr["status"],
        "atr_record_sha256": atr["payload_sha256"],
    }
    if quantity is None or atr["status"] != "OBSERVED":
        return base | {"status": "UNKNOWN", "planned_loss_krw": None}
    core = modules["CORE"]
    loss = core.frac(quantity, "quantity") * core.frac(multiple, "atr_multiple") * core.frac(atr["atr14"], "atr14")
    return base | {"status": "RECORDED", "planned_loss_krw": core.fstr(loss)}


def _strength_episode(t2_rotation: dict) -> dict | None:
    if t2_rotation.get("status") != "PASS" or not t2_rotation.get("strong_confirmed_on"):
        return None
    episode = _v3_modules()["EPISODE"]
    confirmed_on = t2_rotation["strong_confirmed_on"]
    available = dt.datetime.strptime(confirmed_on, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc) + dt.timedelta(days=1)
    return {
        "strength_episode_id": episode.strength_episode_id(CRYPTO_MARKET, t2_rotation["rotation_bucket"], confirmed_on),
        "confirmation_available_at_utc": _iso_utc(available),
    }


def _reentry_criterion(core, *, instrument: str, decision_packet_id: str | None, decision_at_utc: str,
                       strength_episode: dict | None, position_fills: list | None) -> dict:
    if decision_packet_id is None:
        return _criterion("UNKNOWN", "DECISION_PACKET_ID_NOT_SUPPLIED")
    if position_fills is None:
        return _criterion("UNKNOWN", "POSITION_FILLS_NOT_SUPPLIED")
    episode = _v3_modules()["EPISODE"]
    own = [row for row in position_fills if row.get("market") == CRYPTO_MARKET and row.get("instrument") == instrument]
    try:
        record = episode.reentry_decision(
            core, market=CRYPTO_MARKET, instrument=instrument, decision_packet_id=decision_packet_id,
            decision_at_utc=decision_at_utc, current_strength_episode=strength_episode, fills=own,
        )
    except _v3_modules()["CORE"].PaperExecutionCoreError as exc:
        raise CryptoPaperBuyEligibilityError(f"REENTRY_INPUT_INVALID:{instrument}:{exc}") from exc
    status = {"ALLOWED": "PASS", "DENIED": "FAIL"}.get(record["verdict"], "UNKNOWN")
    return _criterion(status, f"REENTRY_{record['verdict']}:{record['reason']}",
                      strength_episode=strength_episode, fills_sha256=record["fills_sha256"])


def _normalize_fills(position_fills) -> list | None:
    if position_fills is None:
        return None
    if not isinstance(position_fills, list):
        raise CryptoPaperBuyEligibilityError("POSITION_FILLS_INVALID")
    fill_ids = [row.get("fill_id") if isinstance(row, dict) else None for row in position_fills]
    if len(fill_ids) != len(set(fill_ids)):
        raise CryptoPaperBuyEligibilityError("POSITION_FILL_ID_DUPLICATE")
    return sorted(copy.deepcopy(position_fills), key=lambda row: str(row.get("fill_id")))


def _session_budget_lines(record: dict | None, *, decision_at_utc: str) -> dict:
    if record is None:
        return {}
    modules = _v3_modules()
    budget = modules["BUDGET"]
    try:
        checked = budget.validate_session_budget_record(record)
        session_id = budget.crypto_session_id(modules["CORE"].load_core(), decision_at_utc)
    except modules["CORE"].PaperExecutionCoreError as exc:
        raise CryptoPaperBuyEligibilityError(f"SESSION_BUDGET_RECORD_INVALID:{exc}") from exc
    if checked["key"]["market"] != CRYPTO_MARKET or checked["key"]["session_id"] != session_id:
        raise CryptoPaperBuyEligibilityError("SESSION_BUDGET_RECORD_SESSION_MISMATCH")
    if checked["decision_at_utc"] != decision_at_utc:
        raise CryptoPaperBuyEligibilityError("SESSION_BUDGET_RECORD_DECISION_MISMATCH")
    return {"record": checked, "lines": {line["instrument"]: line for line in checked["allocation"]}}


def _order_draft_v3(
    core, *, market: str, budget: dict, decision_at_utc: str, fee_rate: str | None,
    reentry: dict | None,
) -> dict:
    session = _v3_modules()["BUDGET"]
    session_id = session.crypto_session_id(core, decision_at_utc)
    bounds = session.session_bounds(core, session_id)
    line = (budget.get("lines") or {}).get(market)
    candidate = None
    if line is not None:
        candidate = next(c for c in budget["record"]["inputs"]["candidates"] if c["instrument"] == market)
        if fee_rate is None or candidate["fee_rate"] != fee_rate:
            raise CryptoPaperBuyEligibilityError(f"SESSION_BUDGET_FEE_RATE_MISMATCH:{market}")
    quantity = line["quantity"] if line is not None else None
    draft = {
        "session_id": session_id,
        "session_budget_record_sha256": budget["record"]["record_sha256"] if budget else None,
        "allocated_krw": line["allocated_krw"] if line is not None and line["allocated_krw"] != "0" else None,
        "planning_price_krw": candidate["limit_price"] if candidate is not None else None,
        "quantity": quantity,
        "fee_rate": fee_rate if line is not None else None,
        "submitted_amount_krw": line["submitted_amount_krw"] if line is not None else None,
        "expires_at": bounds["order_valid_before_utc"],
        "next_review_at": bounds["order_valid_before_utc"],
        "duplicate_guard_key": reentry["duplicate_guard_key"] if reentry is not None else None,
        "reentry_key": reentry["key"] if reentry is not None else None,
        "planned_loss": None,
    }
    return draft


def evaluate_candidate_v3(
    core,
    candidate_row: dict,
    *,
    market_evidence_packet: dict | None,
    universe_row: dict,
    policy: dict,
    decision_at_utc: str,
    decision_packet_id: str | None,
    known_idempotency_keys,
    position_fills: list | None,
    budget: dict,
    fee_rate: str | None,
) -> dict:
    market = candidate_row["market"]
    if universe_row["market"] != market:
        raise CryptoPaperBuyEligibilityError(f"UNIVERSE_ROW_MARKET_MISMATCH:{market}")
    t2 = candidate_row["t2_required_conditions"]
    reentry = compute_reentry_key(decision_packet_id, market) if decision_packet_id is not None else None
    if reentry is None:
        duplicate = _criterion("UNKNOWN", "DECISION_PACKET_ID_NOT_SUPPLIED")
    elif known_idempotency_keys is None:
        duplicate = _criterion("UNKNOWN", "DUPLICATE_GUARD_LEDGER_NOT_SUPPLIED")
    elif reentry["duplicate_guard_key"] in known_idempotency_keys:
        duplicate = _criterion("FAIL", "R1_DUPLICATE_GUARD_KEY_ALREADY_PRESENT")
    else:
        duplicate = _criterion("PASS", "R1_DUPLICATE_GUARD_KEY_NOVEL")
    strength = _strength_episode(t2["T2_ROTATION_MEMBERSHIP"])
    criteria = {
        "FOCUSED_REVIEW_UPSTREAM": evaluate_focused_review_upstream(candidate_row),
        "REGIME_PERMITS_ENTRY": _criterion(
            t2["T2_REGIME_PERMITS_NEW_BUYS"]["status"],
            f"P5_08_T2_ECHO:{t2['T2_REGIME_PERMITS_NEW_BUYS']['reason']}",
        ),
        "ROTATION_MEMBERSHIP": _criterion(
            t2["T2_ROTATION_MEMBERSHIP"]["status"],
            f"P5_08_T2_ECHO:{t2['T2_ROTATION_MEMBERSHIP']['reason']}",
        ),
        "REENTRY_PERMITTED": _reentry_criterion(
            core, instrument=market, decision_packet_id=decision_packet_id, decision_at_utc=decision_at_utc,
            strength_episode=strength, position_fills=position_fills,
        ),
        "DUPLICATE_GUARD": duplicate,
        "ORDER_DRAFT_COMPLETE": None,
        "ZERO_ORDER_ENDPOINT_CALLS": evaluate_zero_order_endpoint_calls(),
    }
    gating = {name: criteria[name] for name in GATING_CRITERIA_V3}
    failed = sorted(name for name, result in gating.items() if result["status"] == "FAIL")
    unknown = sorted(name for name, result in gating.items() if result["status"] == "UNKNOWN")
    if not failed and not unknown and budget and market not in budget["lines"]:
        # Gating passed but this market was not a session budget candidate
        # (e.g. removed by a per-market cap before allocation).
        draft = _order_draft_v3(core, market=market, budget={}, decision_at_utc=decision_at_utc,
                                fee_rate=None, reentry=reentry)
    else:
        draft = _order_draft_v3(core, market=market, budget=budget, decision_at_utc=decision_at_utc,
                                fee_rate=fee_rate, reentry=reentry)
    missing = [field for field in _ORDER_DRAFT_V3_REQUIRED if draft.get(field) is None]
    criteria["ORDER_DRAFT_COMPLETE"] = (
        _criterion("UNKNOWN", "ORDER_DRAFT_FIELDS_MISSING:" + ",".join(missing), missing_fields=missing)
        if missing else _criterion("PASS", "ORDER_DRAFT_COMPLETE_SESSION_BUDGET_SIZED")
    )
    if failed:
        state, reason = STATE_BLOCKED, "GATING_CRITERIA_FAILED:" + ",".join(failed)
    elif unknown:
        state, reason = STATE_WATCH, "GATING_CRITERIA_UNKNOWN:" + ",".join(unknown)
    elif not missing:
        state, reason = STATE_PAPER_BUY_ELIGIBLE, "ALL_GATING_CRITERIA_PASSED_SESSION_BUDGET_SIZED"
    else:
        state, reason = STATE_WAIT, "ALL_GATING_CRITERIA_PASSED_ORDER_DRAFT_INCOMPLETE"
    if state != STATE_PAPER_BUY_ELIGIBLE:
        draft = {key: None for key in draft}
    else:
        draft["planned_loss"] = planned_loss_record(draft["quantity"], market_evidence_packet, decision_at_utc)
    pairs = [(RULE_ENTRY_B, "BLOCKED_BY" if state in (STATE_BLOCKED, STATE_WATCH) else "APPLIED"),
             (RULE_REENTRY, "BLOCKED_BY" if "REENTRY_PERMITTED" in failed or "DUPLICATE_GUARD" in failed else "APPLIED"),
             (RULE_TIME_CONTRACT, "APPLIED"), (RULE_PLANNED_LOSS, "APPLIED")]
    if state == STATE_PAPER_BUY_ELIGIBLE:
        pairs.append((RULE_SESSION_BUDGET, "SIZED_BY"))
    return {
        "market": market,
        "canonical_asset_id": candidate_row.get("canonical_asset_id"),
        "p5_08_promotion_state": candidate_row["promotion_state"],
        "criteria": criteria,
        "record_only_features": _record_only_features(universe_row, market_evidence_packet, policy),
        "eligibility_state": state,
        "eligibility_reason": reason,
        "order_draft": draft,
        "rule_refs": _inline_rule_refs(core, pairs, decision_at_utc),
        "authority": dict(_ROW_AUTHORITY),
    }


def build_eligibility_packet_v3(
    promotion_packet: dict,
    *,
    evaluation_as_of: str,
    decision_at_utc: str,
    decision_packet_id: str | None = None,
    known_idempotency_keys=None,
    position_fills: list | None = None,
    session_budget_record: dict | None = None,
    fee_rate: str | None = None,
    policy: dict | None = None,
) -> dict:
    """Contract/3 derivation (see the section comment above)."""
    if not _DATE_RE.fullmatch(evaluation_as_of):
        raise CryptoPaperBuyEligibilityError("EVALUATION_AS_OF_INVALID")
    _parse_utc(decision_at_utc, "DECISION_AT_UTC_INVALID")
    contract = load_contract_v3()
    policy = _require_repository_policy(load_policy() if policy is None else policy)
    validated_promotion = PROMOTION.validate_output(promotion_packet)
    if validated_promotion["schema_version"] != PROMOTION.OUTPUT_SCHEMA_VERSION_V3:
        raise CryptoPaperBuyEligibilityError("ELIGIBILITY_V3_REQUIRES_PROMOTION_CONTRACT_V3")
    if "rotation_confirmation" not in validated_promotion["source_packets"]:
        raise CryptoPaperBuyEligibilityError("ELIGIBILITY_V3_REQUIRES_ROTATION_CONFIRMATION_SOURCE")
    if validated_promotion["evaluation_as_of"] != evaluation_as_of:
        raise CryptoPaperBuyEligibilityError("EVALUATION_AS_OF_MISMATCH")
    if validated_promotion["regime_generated_at"] != decision_at_utc:
        raise CryptoPaperBuyEligibilityError("DECISION_AT_NOT_PROMOTION_REFERENCE")
    if fee_rate is not None:
        _decimal(fee_rate, "FEE_RATE_INVALID", maximum=Decimal("1"))
    if known_idempotency_keys is not None and not all(isinstance(key, str) for key in known_idempotency_keys):
        raise CryptoPaperBuyEligibilityError("KNOWN_IDEMPOTENCY_KEYS_INVALID")
    fills = _normalize_fills(position_fills)
    core = _v3_modules()["CORE"].load_core()
    budget = _session_budget_lines(session_budget_record, decision_at_utc=decision_at_utc)

    sources = validated_promotion["source_packets"]
    universe_packet = sources["universe"]
    universe_policy = UPBIT_UNIVERSE.load_policy()
    if universe_packet["policy_version"] != universe_policy.get("policy_version"):
        raise CryptoPaperBuyEligibilityError("UNIVERSE_POLICY_PIN_MISMATCH")
    universe_by_market = {row["market"]: row for row in universe_packet["markets"]}
    focused = [row for row in validated_promotion["candidates"] if row["promotion_state"] == "FOCUSED_REVIEW"]
    if budget:
        extra = sorted(set(budget["lines"]) - {row["market"] for row in focused})
        if extra:
            raise CryptoPaperBuyEligibilityError("SESSION_BUDGET_CANDIDATE_NOT_FOCUSED_REVIEW:" + ",".join(extra))
    rows = [
        evaluate_candidate_v3(
            core, candidate,
            market_evidence_packet=sources["market_evidence_by_market"].get(candidate["market"]),
            universe_row=universe_by_market[candidate["market"]],
            policy=policy,
            decision_at_utc=decision_at_utc,
            decision_packet_id=decision_packet_id,
            known_idempotency_keys=set(known_idempotency_keys) if known_idempotency_keys is not None else None,
            position_fills=fills,
            budget=budget,
            fee_rate=fee_rate,
        )
        for candidate in focused
    ]
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION_V3,
        "contract_version": contract["contract_version"],
        "evaluation_as_of": evaluation_as_of,
        "decision_at_utc": decision_at_utc,
        "policy_version": policy["policy_version"],
        "promotion_packet_sha256": validated_promotion["payload_sha256"],
        "focused_review_input_count": len(focused),
        "candidates": rows,
        "summary": {
            "candidate_count": len(rows),
            "watch_count": sum(1 for r in rows if r["eligibility_state"] == STATE_WATCH),
            "wait_count": sum(1 for r in rows if r["eligibility_state"] == STATE_WAIT),
            "blocked_count": sum(1 for r in rows if r["eligibility_state"] == STATE_BLOCKED),
            "paper_buy_eligible_count": sum(1 for r in rows if r["eligibility_state"] == STATE_PAPER_BUY_ELIGIBLE),
        },
        "authority": dict(_ROW_AUTHORITY),
        "source": {
            "promotion_packet": copy.deepcopy(promotion_packet),
            "policy": copy.deepcopy(policy),
            "decision_packet_id": decision_packet_id,
            "known_idempotency_keys": sorted(known_idempotency_keys) if known_idempotency_keys is not None else None,
            "position_fills": fills,
            "session_budget_record": copy.deepcopy(session_budget_record),
            "fee_rate": fee_rate,
        },
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def _validate_output_v3(packet: dict) -> dict:
    expected_keys = {
        "schema_version", "contract_version", "evaluation_as_of", "decision_at_utc", "policy_version",
        "promotion_packet_sha256", "focused_review_input_count", "candidates", "summary",
        "authority", "source", "payload_sha256",
    }
    if set(packet) != expected_keys:
        raise CryptoPaperBuyEligibilityError("OUTPUT_SCHEMA_MISMATCH")
    if packet.get("contract_version") != load_contract_v3()["contract_version"]:
        raise CryptoPaperBuyEligibilityError("OUTPUT_CONTRACT_VERSION_MISMATCH")
    claimed = packet.get("payload_sha256")
    if not isinstance(claimed, str) or not _SHA_RE.fullmatch(claimed):
        raise CryptoPaperBuyEligibilityError("PAYLOAD_SHA256_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        raise CryptoPaperBuyEligibilityError("PAYLOAD_SHA256_MISMATCH")
    source = packet["source"]
    if not isinstance(source, dict) or set(source) != {
        "promotion_packet", "policy", "decision_packet_id", "known_idempotency_keys",
        "position_fills", "session_budget_record", "fee_rate",
    }:
        raise CryptoPaperBuyEligibilityError("OUTPUT_SOURCE_INVALID")
    rebuilt = build_eligibility_packet_v3(
        source["promotion_packet"],
        evaluation_as_of=packet["evaluation_as_of"],
        decision_at_utc=packet["decision_at_utc"],
        decision_packet_id=source["decision_packet_id"],
        known_idempotency_keys=set(source["known_idempotency_keys"]) if source["known_idempotency_keys"] is not None else None,
        position_fills=source["position_fills"],
        session_budget_record=source["session_budget_record"],
        fee_rate=source["fee_rate"],
        policy=source["policy"],
    )
    if canonical_json(rebuilt) != canonical_json(packet):
        raise CryptoPaperBuyEligibilityError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


_validate_output_v2 = validate_output


def validate_output(packet: dict) -> dict:  # noqa: F811 - contract dispatch
    """Re-validate an eligibility packet under the contract its schema names."""
    if isinstance(packet, dict) and packet.get("schema_version") == OUTPUT_SCHEMA_VERSION_V3:
        return _validate_output_v3(packet)
    return _validate_output_v2(packet)
