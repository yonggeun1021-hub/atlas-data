#!/usr/bin/env python3
"""P5-08 Crypto Candidate Promotion Rule.

Separates the size of the Upbit *observation pool* from the (much smaller)
set of PAPER *candidates*, and makes every promotion/blocking decision
explainable gate-by-gate. Per-market state machine, no stage skipping:

    OBSERVATION_POOL -> TRADEABLE_UNIVERSE -> FOCUSED_REVIEW -> PAPER_READY

This module is a **consumer**, not a re-implementation, of upstream
authority:

* ``universe/upbit_tradeable_universe.py`` (P3-12) already owns identity /
  Upbit KRW tradability / listing-warning-delisting / policy+taxonomy
  ratification. This module reads that packet's own ``state``/``reason``
  for the OBSERVATION_POOL -> TRADEABLE_UNIVERSE transition; it never
  re-derives or second-guesses those thresholds.
* ``microstructure/upbit_market_evidence.py`` (P4-07) already owns
  candle/orderbook/trade evidence + freshness. This module reads finalized
  candles and orderbook spread/slippage status from that packet; it never
  invents its own freshness or spread math.
* ``regime/live_axis_adapter.py`` + ``regime/output_contract.py`` (P1-CR-08)
  already own the ``PRE_SCORE_UNKNOWN_ONLY`` boundary. This module reads
  ``regime_output["regime"]`` verbatim and never interprets, scores, or
  aggregates it. In the current, natural, ratified state that value is
  always the literal string ``"UNKNOWN"`` -- so the natural output of this
  module today is candidates capped at ``TRADEABLE_UNIVERSE`` with
  disposition ``WATCH``, never ``FOCUSED_REVIEW``/``PAPER_READY``. That is
  the *correct* current behavior, not a bug to "fix" by interpreting Regime.

Trend / relative-strength / breakout / pullback / volume-confirmation math
IS new logic this module owns -- no other module in this repository
computes it. Every numeric threshold used here is copied verbatim from the
canonical v1 ``PROPOSED_PAPER_BASELINE`` comparison table (see
``config/crypto_candidate_promotion_contract.json``); none are invented,
tuned, or loosened here. Two gates the canonical table does not give a
ratified numeric definition for -- "overextension" and a generic "event
blocker" evidence feed -- are wired as structural gates that always report
``UNKNOWN`` (never silently PASS) until such a definition is ratified and
supplied via ``config/crypto_candidate_promotion_contract.json``'s
``unratified_thresholds_pending`` list.

Every upstream packet this module is handed (``universe_packet``,
``market_evidence_packet`` and its peer/BTC variants) is re-hashed at the
door: ``payload_sha256`` is recomputed from the packet bytes actually
received and compared against the packet's own embedded
``payload_sha256`` field. A mismatch is treated as tamper, never as a
warning -- the candidate is force-routed to ``BLOCKED``
(``reason=LINEAGE_HASH_MISMATCH``). This mirrors P10-04's "trust only
ancestor source, revalidate at the point of consumption" hardening, not a
one-off invention for this module.

``authority`` on every output row/packet is hardcoded all-``false``. This
module classifies; it never sizes, drafts, or places an order. Reaching
``PAPER_READY`` here does *not* mean an order exists -- P5-09 is the only
module authorized to build a PAPER order draft, and even it may not call
any real Upbit Exchange order/withdrawal endpoint.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

CONTRACT_PATH = ROOT / "config" / "crypto_candidate_promotion_contract.json"
OUTPUT_SCHEMA_VERSION = "crypto_candidate_promotion_packet/1"


def _load_sibling(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNIVERSE = _load_sibling("upbit_tradeable_universe_for_p5_08", "universe/upbit_tradeable_universe.py")
EVIDENCE = _load_sibling("upbit_market_evidence_for_p5_08", "microstructure/upbit_market_evidence.py")
REGIME_OUTPUT = _load_sibling("regime_output_contract_for_p5_08", "regime/output_contract.py")


STATE_OBSERVATION_POOL = "OBSERVATION_POOL"
STATE_TRADEABLE_UNIVERSE = "TRADEABLE_UNIVERSE"
STATE_FOCUSED_REVIEW = "FOCUSED_REVIEW"
STATE_PAPER_READY = "PAPER_READY"
STATES = (STATE_OBSERVATION_POOL, STATE_TRADEABLE_UNIVERSE, STATE_FOCUSED_REVIEW, STATE_PAPER_READY)

DISPOSITION_PROMOTED = "PROMOTED"
DISPOSITION_WATCH = "WATCH"
DISPOSITION_WAIT = "WAIT"
DISPOSITION_BLOCKED = "BLOCKED"
DISPOSITIONS = (DISPOSITION_PROMOTED, DISPOSITION_WATCH, DISPOSITION_WAIT, DISPOSITION_BLOCKED)

GATE_PASS = "PASS"
GATE_FAIL = "FAIL"
GATE_UNKNOWN = "UNKNOWN"
GATE_STATUSES = (GATE_PASS, GATE_FAIL, GATE_UNKNOWN)

# PROPOSED_PAPER_BASELINE -- verbatim from canonical v1, never re-derived.
EMA_PERIOD = 20
BREAKOUT_LOOKBACK = 20
RS_LOOKBACK_DAYS = 20
VOLUME_MEDIAN_RATIO_MIN = Decimal("1.5")
MAX_MEDIAN_SPREAD_BPS = Decimal("20")
MAX_PAPER_SLIPPAGE_BPS = Decimal("30")

# Not a policy toggle: never read, set, or made overridable by config.
_AUTHORITY = {
    "candidate_classification_only": True,
    "identity_ratification_authorized": False,
    "policy_ratification_authorized": False,
    "regime_interpretation_authorized": False,
    "regime_aggregate_score_authorized": False,
    "position_sizing_authorized": False,
    "risk_budget_authorized": False,
    "order_draft_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authority": False,
}


class CryptoCandidatePromotionError(ValueError):
    """Fail-closed P5-08 candidate-promotion contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoCandidatePromotionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    if not isinstance(value, dict) or value.get("contract_version") != "crypto_candidate_promotion/1":
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:contract_version")
    if value.get("mode") != "PROVISIONAL_PAPER_ONLY":
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:mode")
    for key, expected in value.get("authority", {}).items():
        if key == "candidate_classification_only":
            if expected is not True:
                raise CryptoCandidatePromotionError(f"CONTRACT_AUTHORITY_INVALID:{key}")
            continue
        if expected is not False:
            raise CryptoCandidatePromotionError(f"CONTRACT_AUTHORITY_NOT_FALSE:{key}")
    if value.get("upstream_dependencies", {}).get("regime_contract_mode_required") != "PRE_SCORE_UNKNOWN_ONLY":
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:regime_contract_mode_required")
    return copy.deepcopy(value)


def _decimal(value, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CryptoCandidatePromotionError(f"DECIMAL_INVALID:{label}:{value!r}") from exc


def _gate(name: str, status: str, reason: str, *, evidence_lineage: dict | None = None) -> dict:
    if status not in GATE_STATUSES:
        raise CryptoCandidatePromotionError(f"GATE_STATUS_INVALID:{name}:{status}")
    return {
        "gate": name,
        "status": status,
        "reason": reason,
        "evidence_lineage": evidence_lineage or {},
    }


def _verify_pit_alignment(*, evaluation_as_of: str, universe_packet: dict, evidence_packets: dict) -> None:
    """Reject any upstream packet whose own PIT stamp does not agree with
    the PIT this evaluation is being run for. A candidate evaluated "as of"
    one date must not silently absorb a universe classification or market
    evidence snapshot stamped for a different date -- that is exactly the
    kind of cross-market/cross-date packet mixing this module must never
    tolerate silently.
    """
    universe_as_of = universe_packet.get("evaluation_as_of")
    if universe_as_of != evaluation_as_of:
        raise CryptoCandidatePromotionError(
            f"PIT_MISMATCH:universe_packet.evaluation_as_of={universe_as_of!r}!=evaluation_as_of={evaluation_as_of!r}"
        )
    for label, packet in evidence_packets.items():
        if packet is None:
            continue
        market_as_of = packet.get("as_of")
        if not isinstance(market_as_of, str) or not market_as_of.startswith(evaluation_as_of):
            raise CryptoCandidatePromotionError(
                f"PIT_MISMATCH:{label}.as_of={market_as_of!r} not aligned with evaluation_as_of={evaluation_as_of!r}"
            )


def _verify_lineage_hash(packet: dict, label: str) -> None:
    """Re-hash a received upstream packet and compare against its own
    embedded ``payload_sha256``. Any mismatch -- tamper, truncation, a hand
    -edited fixture pretending to be validated -- fails closed. This is the
    *only* trust boundary this module applies to upstream packets; it does
    not re-derive their internal field-level validation logic.
    """
    if not isinstance(packet, dict):
        raise CryptoCandidatePromotionError(f"LINEAGE_PACKET_NOT_DICT:{label}")
    claimed = packet.get("payload_sha256")
    if not isinstance(claimed, str) or len(claimed) != 64:
        raise CryptoCandidatePromotionError(f"LINEAGE_HASH_MISSING:{label}")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    recomputed = payload_sha256(unsigned)
    if recomputed != claimed:
        raise CryptoCandidatePromotionError(f"LINEAGE_HASH_MISMATCH:{label}")


# ---------------------------------------------------------------------------
# Candle math -- pure functions of already-finalized candle rows only.
# ---------------------------------------------------------------------------

def _finalized_closes(candle_evidence: dict) -> list[Decimal]:
    rows = candle_evidence.get("finalized_candles") or []
    return [_decimal(row["trade_price"], "trade_price") for row in rows]


def _finalized_highs(candle_evidence: dict) -> list[Decimal]:
    rows = candle_evidence.get("finalized_candles") or []
    return [_decimal(row["high_price"], "high_price") for row in rows]


def _finalized_volumes(candle_evidence: dict) -> list[Decimal]:
    rows = candle_evidence.get("finalized_candles") or []
    return [_decimal(row["candle_acc_trade_volume"], "candle_acc_trade_volume") for row in rows]


def _ema_series(values: list[Decimal], period: int) -> list[Decimal] | None:
    """Standard EMA, SMA-seeded on the first ``period`` values. Returns
    ``None`` (never a partial/guessed series) when fewer than ``period``
    values are available.
    """
    if len(values) < period:
        return None
    k = Decimal(2) / Decimal(period + 1)
    seed = sum(values[:period]) / Decimal(period)
    series = [seed]
    for value in values[period:]:
        series.append((value - series[-1]) * k + series[-1])
    return series


def _median(values: list[Decimal]) -> Decimal:
    ordered = sorted(values)
    n = len(ordered)
    mid = n // 2
    if n % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


# ---------------------------------------------------------------------------
# Stage 1: OBSERVATION_POOL -> TRADEABLE_UNIVERSE
# (identity / tradability / listing-warning-delisting / policy ratification)
# -- pure pass-through of the P3-12 packet's own row. No re-derivation.
# ---------------------------------------------------------------------------

def _find_universe_row(universe_packet: dict, market: str) -> dict | None:
    for row in universe_packet.get("markets", []):
        if row.get("market") == market:
            return row
    return None


def _stage1_gates(universe_packet: dict, market: str) -> tuple[list[dict], dict | None]:
    row = _find_universe_row(universe_packet, market)
    lineage = {"universe_packet_sha256": universe_packet.get("payload_sha256")}
    if row is None:
        return [
            _gate("identity", GATE_UNKNOWN, "MARKET_NOT_IN_UNIVERSE_PACKET", evidence_lineage=lineage),
            _gate("upbit_krw_tradability", GATE_UNKNOWN, "MARKET_NOT_IN_UNIVERSE_PACKET", evidence_lineage=lineage),
            _gate("listing_warning_delisting_blocker", GATE_UNKNOWN, "MARKET_NOT_IN_UNIVERSE_PACKET", evidence_lineage=lineage),
            _gate("source_policy_ratification", GATE_UNKNOWN, "MARKET_NOT_IN_UNIVERSE_PACKET", evidence_lineage=lineage),
        ], None

    identity_status = GATE_PASS if row.get("candidate_canonical_asset_id") else GATE_FAIL
    identity_reason = "CANONICAL_IDENTITY_MAPPED" if identity_status == GATE_PASS else (row.get("reason") or "IDENTITY_UNRATIFIED")

    # Deliberately independent of ``row["state"]`` reaching TRADEABLE_UNIVERSE:
    # a market can be genuinely tradable on Upbit (not BLOCKED, no active
    # warning) while still sitting in OBSERVATION_POOL purely for identity/
    # policy-ratification reasons captured by the *other* gates below. This
    # gate reports only the market-level tradability fact.
    if row["state"] == UNIVERSE.STATE_BLOCKED:
        tradability_status, tradability_reason = GATE_FAIL, row.get("reason") or "BLOCKED"
    elif row.get("market_event_warning") is None:
        tradability_status, tradability_reason = GATE_UNKNOWN, "MISSING_FIELD:market_event"
    elif row["market_event_warning"] is True:
        tradability_status, tradability_reason = GATE_FAIL, "INVESTMENT_WARNING_ACTIVE"
    else:
        tradability_status, tradability_reason = GATE_PASS, "UPBIT_KRW_SPOT_TRADEABLE"

    if row.get("market_event_warning") is True:
        blocker_status, blocker_reason = GATE_FAIL, "MARKET_EVENT_WARNING_ACTIVE"
    elif row.get("market_event_warning") is None:
        blocker_status, blocker_reason = GATE_UNKNOWN, "MISSING_FIELD:market_event"
    elif row.get("market_event_caution_any"):
        blocker_status, blocker_reason = GATE_FAIL, "MARKET_EVENT_CAUTION_ACTIVE"
    else:
        blocker_status, blocker_reason = GATE_PASS, "NO_LISTING_WARNING_DELISTING_BLOCKER"

    policy_ratified = universe_packet.get("policy_ratified") is True
    taxonomy_ratified = universe_packet.get("taxonomy_ratified") is True
    if policy_ratified and taxonomy_ratified:
        ratify_status, ratify_reason = GATE_PASS, "POLICY_AND_TAXONOMY_RATIFIED"
    elif not policy_ratified and not taxonomy_ratified:
        ratify_status, ratify_reason = GATE_FAIL, "POLICY_AND_TAXONOMY_UNRATIFIED"
    elif not policy_ratified:
        ratify_status, ratify_reason = GATE_FAIL, "POLICY_UNRATIFIED"
    else:
        ratify_status, ratify_reason = GATE_FAIL, "TAXONOMY_UNRATIFIED"

    gates = [
        _gate("identity", identity_status, identity_reason, evidence_lineage=lineage),
        _gate("upbit_krw_tradability", tradability_status, tradability_reason, evidence_lineage=lineage),
        _gate("listing_warning_delisting_blocker", blocker_status, blocker_reason, evidence_lineage=lineage),
        _gate("source_policy_ratification", ratify_status, ratify_reason, evidence_lineage=lineage),
    ]
    return gates, row


# ---------------------------------------------------------------------------
# Stage 2: TRADEABLE_UNIVERSE -> FOCUSED_REVIEW
# (regime / trend / relative strength / trigger+volume / overextension /
#  event blocker)
# ---------------------------------------------------------------------------

def _regime_evidence_gate(regime_output: dict | None) -> dict:
    if regime_output is None:
        return _gate("regime_evidence", GATE_UNKNOWN, "REGIME_OUTPUT_NOT_SUPPLIED")
    try:
        REGIME_OUTPUT.validate_output(regime_output)
    except Exception as exc:  # noqa: BLE001 -- any validation failure fails closed
        return _gate("regime_evidence", GATE_UNKNOWN, f"REGIME_OUTPUT_INVALID:{exc}")
    regime = regime_output.get("regime")
    lineage = {"regime_generated_at": regime_output.get("generated_at"), "regime_market": regime_output.get("market")}
    if regime in ("RISK_OFF", "STRESS"):
        return _gate("regime_evidence", GATE_FAIL, f"REGIME_{regime}", evidence_lineage=lineage)
    if regime == "UNKNOWN":
        return _gate("regime_evidence", GATE_UNKNOWN, "REGIME_PRE_SCORE_UNKNOWN_ONLY", evidence_lineage=lineage)
    return _gate("regime_evidence", GATE_PASS, f"REGIME_{regime}_ALLOWS_ENTRY", evidence_lineage=lineage)


def _daily_4h_trend_gate(market_evidence: dict) -> dict:
    daily = market_evidence.get("candles", {}).get("1d")
    h4 = market_evidence.get("candles", {}).get("4h")
    if not daily or not h4:
        return _gate("daily_4h_trend_consistency", GATE_UNKNOWN, "CANDLE_TIMEFRAME_MISSING")
    daily_closes = _finalized_closes(daily)
    h4_closes = _finalized_closes(h4)
    daily_ema = _ema_series(daily_closes, EMA_PERIOD)
    h4_ema = _ema_series(h4_closes, EMA_PERIOD)
    if daily_ema is None or h4_ema is None or len(h4_ema) < 2:
        return _gate("daily_4h_trend_consistency", GATE_UNKNOWN, "INSUFFICIENT_FINALIZED_CANDLES")
    daily_up = daily_closes[-1] > daily_ema[-1]
    h4_rising = h4_ema[-1] > h4_ema[-2]
    if daily_up and h4_rising:
        return _gate("daily_4h_trend_consistency", GATE_PASS, "DAILY_CLOSE_ABOVE_EMA20_AND_4H_EMA20_RISING")
    if not daily_up and not h4_rising:
        return _gate("daily_4h_trend_consistency", GATE_FAIL, "DAILY_BELOW_EMA20_AND_4H_EMA20_FALLING")
    return _gate("daily_4h_trend_consistency", GATE_FAIL, "DAILY_4H_TREND_CONFLICT")


def _relative_strength_gate(
    market_evidence: dict, btc_evidence: dict | None, peer_evidence: dict | None,
) -> dict:
    daily = market_evidence.get("candles", {}).get("1d")
    if not daily:
        return _gate("relative_strength_btc_peer", GATE_UNKNOWN, "CANDLE_TIMEFRAME_MISSING")
    closes = _finalized_closes(daily)
    if len(closes) <= RS_LOOKBACK_DAYS:
        return _gate("relative_strength_btc_peer", GATE_UNKNOWN, "INSUFFICIENT_FINALIZED_CANDLES")
    candidate_return = closes[-1] / closes[-1 - RS_LOOKBACK_DAYS] - 1

    def _return_of(evidence: dict | None) -> Decimal | None:
        if not evidence:
            return None
        tf = evidence.get("candles", {}).get("1d")
        if not tf:
            return None
        c = _finalized_closes(tf)
        if len(c) <= RS_LOOKBACK_DAYS:
            return None
        return c[-1] / c[-1 - RS_LOOKBACK_DAYS] - 1

    btc_return = _return_of(btc_evidence)
    peer_returns = [r for r in (_return_of(p) for p in (peer_evidence or [])) if r is not None]
    if btc_return is None or not peer_returns:
        return _gate("relative_strength_btc_peer", GATE_UNKNOWN, "BTC_OR_PEER_RETURN_UNAVAILABLE")
    peer_median = _median(peer_returns)
    if candidate_return > btc_return and candidate_return > peer_median:
        return _gate("relative_strength_btc_peer", GATE_PASS, "20D_RETURN_EXCEEDS_BTC_AND_PEER_MEDIAN")
    return _gate("relative_strength_btc_peer", GATE_FAIL, "20D_RETURN_DOES_NOT_EXCEED_BTC_OR_PEER_MEDIAN")


def _breakout_check(h1_evidence: dict) -> tuple[bool | None, dict]:
    highs = _finalized_highs(h1_evidence)
    closes = _finalized_closes(h1_evidence)
    volumes = _finalized_volumes(h1_evidence)
    if len(highs) <= BREAKOUT_LOOKBACK:
        return None, {}
    prior_high = max(highs[-1 - BREAKOUT_LOOKBACK:-1])
    trigger_close = closes[-1]
    prior_median_volume = _median(volumes[-1 - BREAKOUT_LOOKBACK:-1])
    ratio = None if prior_median_volume == 0 else volumes[-1] / prior_median_volume
    triggered = trigger_close > prior_high
    volume_ok = ratio is not None and ratio >= VOLUME_MEDIAN_RATIO_MIN
    return (triggered and volume_ok), {
        "prior_20_high": str(prior_high), "trigger_close": str(trigger_close),
        "volume_ratio": str(ratio) if ratio is not None else None,
    }


def _pullback_check(h4_evidence: dict) -> tuple[bool | None, dict]:
    closes = _finalized_closes(h4_evidence)
    ema = _ema_series(closes, EMA_PERIOD)
    if ema is None or len(closes) < len(ema) + 1 or len(ema) < 2:
        return None, {}
    # align closes tail with ema tail (ema series starts at index EMA_PERIOD-1 of closes)
    latest_close = closes[-1]
    prior_close = closes[-2]
    latest_ema = ema[-1]
    uptrend = ema[-1] > ema[-2]
    recovered = prior_close <= latest_ema and latest_close > latest_ema
    return (uptrend and recovered), {
        "latest_close": str(latest_close), "prior_close": str(prior_close), "ema20": str(latest_ema),
    }


def _price_volume_trigger_gate(market_evidence: dict) -> dict:
    h1 = market_evidence.get("candles", {}).get("1h")
    h4 = market_evidence.get("candles", {}).get("4h")
    if not h1 or not h4:
        return _gate("price_volume_trigger", GATE_UNKNOWN, "CANDLE_TIMEFRAME_MISSING")
    breakout, breakout_lineage = _breakout_check(h1)
    pullback, pullback_lineage = _pullback_check(h4)
    if breakout is None and pullback is None:
        return _gate("price_volume_trigger", GATE_UNKNOWN, "INSUFFICIENT_FINALIZED_CANDLES")
    if breakout or pullback:
        kind = "BREAKOUT" if breakout else "PULLBACK"
        return _gate(
            "price_volume_trigger", GATE_PASS, f"{kind}_CONFIRMED_WITH_INDEPENDENT_VOLUME_EVIDENCE",
            evidence_lineage={"breakout": breakout_lineage, "pullback": pullback_lineage},
        )
    return _gate(
        "price_volume_trigger", GATE_FAIL, "NEITHER_BREAKOUT_NOR_PULLBACK_CONFIRMED",
        evidence_lineage={"breakout": breakout_lineage, "pullback": pullback_lineage},
    )


def _spread_slippage_gate(market_evidence: dict, spread_history_bps: list | None) -> dict:
    orderbook = market_evidence.get("orderbook", {})
    if orderbook.get("freshness", {}).get("status") != "FRESH":
        return _gate("spread_depth_slippage", GATE_UNKNOWN, "ORDERBOOK_NOT_FRESH")
    if not spread_history_bps:
        return _gate("spread_depth_slippage", GATE_UNKNOWN, "MEDIAN_SPREAD_HISTORY_UNAVAILABLE")
    history = [_decimal(v, "spread_history_bps") for v in spread_history_bps]
    median_spread = _median(history)
    slippage_bps = orderbook.get("slippage_bps")
    if slippage_bps is None:
        return _gate("spread_depth_slippage", GATE_UNKNOWN, "SLIPPAGE_NOT_COMPUTABLE")
    slippage = _decimal(slippage_bps, "slippage_bps")
    lineage = {"median_spread_bps": str(median_spread), "paper_slippage_bps": str(slippage)}
    if median_spread <= MAX_MEDIAN_SPREAD_BPS and slippage <= MAX_PAPER_SLIPPAGE_BPS:
        return _gate("spread_depth_slippage", GATE_PASS, "MEDIAN_SPREAD_AND_SLIPPAGE_WITHIN_BASELINE", evidence_lineage=lineage)
    return _gate("spread_depth_slippage", GATE_FAIL, "MEDIAN_SPREAD_OR_SLIPPAGE_ABOVE_BASELINE", evidence_lineage=lineage)


def _overextension_gate(contract: dict) -> dict:
    if "overextension_numeric_definition" in contract.get("unratified_thresholds_pending", []):
        return _gate("overextension", GATE_UNKNOWN, "OVEREXTENSION_THRESHOLD_UNRATIFIED")
    raise CryptoCandidatePromotionError("OVEREXTENSION_GATE_LOGIC_NOT_IMPLEMENTED_FOR_RATIFIED_THRESHOLD")


def _event_blocker_gate(event_blocker_evidence: dict | None, contract: dict) -> dict:
    if event_blocker_evidence is None:
        return _gate("event_blocker", GATE_UNKNOWN, "EVENT_BLOCKER_EVIDENCE_UNAVAILABLE")
    if not isinstance(event_blocker_evidence, dict) or "active" not in event_blocker_evidence:
        raise CryptoCandidatePromotionError("EVENT_BLOCKER_EVIDENCE_MALFORMED")
    if event_blocker_evidence["active"] is True:
        return _gate(
            "event_blocker", GATE_FAIL, event_blocker_evidence.get("reason") or "EVENT_BLOCKER_ACTIVE",
            evidence_lineage={"source_sha256": event_blocker_evidence.get("source_sha256")},
        )
    return _gate(
        "event_blocker", GATE_PASS, "NO_ACTIVE_EVENT_BLOCKER",
        evidence_lineage={"source_sha256": event_blocker_evidence.get("source_sha256")},
    )


# ---------------------------------------------------------------------------
# Stage 3: FOCUSED_REVIEW -> PAPER_READY
# (price-structural entry/invalidation/stop/expiry + freshness + duplicate
#  guard key; PAPER quantity/fee/slippage/risk-headroom are explicitly out
#  of P5-08's scope -- P7-02/P7-05/P7-06 authority belongs to P5-09.)
# ---------------------------------------------------------------------------

def _derive_price_plan(market: str, market_evidence: dict) -> dict | None:
    h1 = market_evidence.get("candles", {}).get("1h")
    h4 = market_evidence.get("candles", {}).get("4h")
    if not h1 or not h4:
        return None
    breakout, breakout_lineage = _breakout_check(h1)
    pullback, pullback_lineage = _pullback_check(h4)
    h1_rows = h1.get("finalized_candles") or []
    if breakout:
        trigger_row = h1_rows[-1]
        entry_low = _decimal(trigger_row["trade_price"], "trade_price")
        invalidation = _decimal(breakout_lineage["prior_20_high"], "prior_20_high")
        trigger_open_time = trigger_row["open_time"]
        kind = "BREAKOUT"
    elif pullback:
        h4_rows = h4.get("finalized_candles") or []
        trigger_row = h4_rows[-1]
        entry_low = _decimal(trigger_row["trade_price"], "trade_price")
        invalidation = _decimal(pullback_lineage["ema20"], "ema20")
        trigger_open_time = trigger_row["open_time"]
        kind = "PULLBACK"
    else:
        return None
    entry_high = entry_low * Decimal("1.005")  # zone ceiling: trigger close to +0.5%, structural not a new risk threshold
    duplicate_guard_key = hashlib.sha256(
        f"{market}|{kind}|{trigger_open_time}".encode("utf-8")
    ).hexdigest()
    return {
        "trigger_kind": kind,
        "entry_zone": {"low": str(entry_low), "high": str(entry_high)},
        "invalidation_price": str(invalidation),
        "planned_stop_price": str(invalidation),
        "trigger_open_time": trigger_open_time,
        "duplicate_guard_key": duplicate_guard_key,
    }


def _freshness_currency_gate(market_evidence: dict) -> dict:
    candle_statuses = {tf: c.get("freshness", {}).get("status") for tf, c in market_evidence.get("candles", {}).items()}
    orderbook_status = market_evidence.get("orderbook", {}).get("freshness", {}).get("status")
    all_statuses = list(candle_statuses.values()) + [orderbook_status]
    if any(s is None for s in all_statuses):
        return _gate("current_candle_orderbook_freshness", GATE_UNKNOWN, "FRESHNESS_FIELD_MISSING")
    if any(s == "UNKNOWN" for s in all_statuses):
        return _gate("current_candle_orderbook_freshness", GATE_UNKNOWN, "FRESHNESS_UNKNOWN")
    if any(s == "STALE" for s in all_statuses):
        return _gate("current_candle_orderbook_freshness", GATE_FAIL, "STALE_EVIDENCE")
    return _gate("current_candle_orderbook_freshness", GATE_PASS, "ALL_EVIDENCE_FRESH")


def _order_plan_completeness_gate(price_plan: dict | None, sizing_input: dict | None) -> dict:
    if price_plan is None:
        return _gate("order_plan_completeness", GATE_FAIL, "PRICE_STRUCTURAL_PLAN_NOT_DERIVABLE")
    required_sizing_fields = ("paper_quantity", "fee_assumption", "slippage_assumption", "expiry", "next_review_time")
    if not sizing_input or any(sizing_input.get(f) in (None, "") for f in required_sizing_fields):
        return _gate(
            "order_plan_completeness", GATE_FAIL,
            "SIZING_FEE_SLIPPAGE_EXPIRY_NOT_SUPPLIED_DEFERRED_TO_P5_09",
        )
    return _gate("order_plan_completeness", GATE_PASS, "ENTRY_INVALIDATION_STOP_QUANTITY_FEE_SLIPPAGE_EXPIRY_COMPLETE")


# ---------------------------------------------------------------------------
# Per-candidate evaluation
# ---------------------------------------------------------------------------

def evaluate_candidate(candidate_input: dict, *, evaluation_as_of: str, contract: dict | None = None) -> dict:
    contract = contract or load_contract()
    market = candidate_input.get("market")
    if not isinstance(market, str) or not market:
        raise CryptoCandidatePromotionError("MARKET_MISSING")

    universe_packet = candidate_input.get("universe_packet")
    if universe_packet is None:
        raise CryptoCandidatePromotionError("UNIVERSE_PACKET_MISSING")
    _verify_lineage_hash(universe_packet, "universe_packet")

    market_evidence = candidate_input.get("market_evidence_packet")
    btc_evidence = candidate_input.get("btc_market_evidence_packet")
    peer_evidence_list = candidate_input.get("peer_market_evidence_packets") or []
    for label, packet in (
        ("market_evidence_packet", market_evidence),
        ("btc_market_evidence_packet", btc_evidence),
        *[(f"peer_market_evidence_packet[{i}]", p) for i, p in enumerate(peer_evidence_list)],
    ):
        if packet is not None:
            _verify_lineage_hash(packet, label)

    _verify_pit_alignment(
        evaluation_as_of=evaluation_as_of,
        universe_packet=universe_packet,
        evidence_packets={
            "market_evidence_packet": market_evidence,
            "btc_market_evidence_packet": btc_evidence,
            **{f"peer_market_evidence_packet[{i}]": p for i, p in enumerate(peer_evidence_list)},
        },
    )

    all_gates: list[dict] = []

    # ---- Stage 1 ----
    stage1_gates, universe_row = _stage1_gates(universe_packet, market)
    all_gates.extend(stage1_gates)
    stage1_pass = all(g["status"] == GATE_PASS for g in stage1_gates) and universe_row is not None and universe_row["state"] in (
        UNIVERSE.STATE_TRADEABLE_UNIVERSE, UNIVERSE.STATE_PAPER_ELIGIBLE,
    )
    stage1_blocked = universe_row is not None and universe_row["state"] == UNIVERSE.STATE_BLOCKED

    state = STATE_OBSERVATION_POOL
    limiting_gate = next((g for g in stage1_gates if g["status"] != GATE_PASS), None)

    if not stage1_pass:
        disposition = DISPOSITION_BLOCKED if stage1_blocked or (limiting_gate and limiting_gate["status"] == GATE_FAIL) else DISPOSITION_WAIT
        return _finalize(market, evaluation_as_of, state, disposition, limiting_gate, all_gates, contract)

    state = STATE_TRADEABLE_UNIVERSE

    if market_evidence is None:
        limiting_gate = _gate("market_evidence", GATE_UNKNOWN, "MARKET_EVIDENCE_PACKET_MISSING")
        all_gates.append(limiting_gate)
        return _finalize(market, evaluation_as_of, state, DISPOSITION_WAIT, limiting_gate, all_gates, contract)

    # ---- Stage 2 ----
    regime_output = candidate_input.get("regime_output")
    stage2_gates = [
        _regime_evidence_gate(regime_output),
        _daily_4h_trend_gate(market_evidence),
        _relative_strength_gate(market_evidence, btc_evidence, peer_evidence_list),
        _price_volume_trigger_gate(market_evidence),
        _spread_slippage_gate(market_evidence, candidate_input.get("spread_history_bps")),
        _overextension_gate(contract),
        _event_blocker_gate(candidate_input.get("event_blocker_evidence"), contract),
    ]
    all_gates.extend(stage2_gates)
    stage2_fail = next((g for g in stage2_gates if g["status"] == GATE_FAIL), None)
    stage2_unknown = next((g for g in stage2_gates if g["status"] == GATE_UNKNOWN), None)

    if stage2_fail is not None:
        return _finalize(market, evaluation_as_of, state, DISPOSITION_BLOCKED, stage2_fail, all_gates, contract)
    if stage2_unknown is not None:
        return _finalize(market, evaluation_as_of, state, DISPOSITION_WATCH, stage2_unknown, all_gates, contract)

    state = STATE_FOCUSED_REVIEW

    # ---- Stage 3 ----
    price_plan = _derive_price_plan(market, market_evidence)
    freshness_gate = _freshness_currency_gate(market_evidence)
    completeness_gate = _order_plan_completeness_gate(price_plan, candidate_input.get("sizing_input"))
    stage3_gates = [freshness_gate, completeness_gate]
    all_gates.extend(stage3_gates)
    stage3_fail = next((g for g in stage3_gates if g["status"] == GATE_FAIL), None)
    stage3_unknown = next((g for g in stage3_gates if g["status"] == GATE_UNKNOWN), None)

    if stage3_fail is not None:
        disposition = DISPOSITION_BLOCKED if stage3_fail["gate"] == "current_candle_orderbook_freshness" else DISPOSITION_WAIT
        return _finalize(market, evaluation_as_of, state, disposition, stage3_fail, all_gates, contract, price_plan=price_plan)
    if stage3_unknown is not None:
        return _finalize(market, evaluation_as_of, state, DISPOSITION_WAIT, stage3_unknown, all_gates, contract, price_plan=price_plan)

    state = STATE_PAPER_READY
    return _finalize(market, evaluation_as_of, state, DISPOSITION_PROMOTED, None, all_gates, contract, price_plan=price_plan)


def _finalize(
    market: str, evaluation_as_of: str, state: str, disposition: str,
    limiting_gate: dict | None, gates: list[dict], contract: dict, *, price_plan: dict | None = None,
) -> dict:
    if state not in STATES:
        raise CryptoCandidatePromotionError(f"STATE_INVALID:{state}")
    if disposition not in DISPOSITIONS:
        raise CryptoCandidatePromotionError(f"DISPOSITION_INVALID:{disposition}")
    row = {
        "market": market,
        "evaluation_as_of": evaluation_as_of,
        "state": state,
        "disposition": disposition,
        "blocking_gate": limiting_gate["gate"] if limiting_gate else None,
        "blocking_reason": limiting_gate["reason"] if limiting_gate else None,
        "gates": gates,
        "price_plan": price_plan,
        "authority": dict(_AUTHORITY),
    }
    row["row_sha256"] = payload_sha256(row)
    return row


def evaluate_pool(candidates: list[dict], *, evaluation_as_of: str, contract: dict | None = None) -> dict:
    """Batch entry point. Each element of ``candidates`` is one
    ``candidate_input`` dict as accepted by :func:`evaluate_candidate`.
    ``observation_pool_count`` is simply ``len(candidates)`` -- the size of
    what was *looked at*, kept separate from ``paper_ready_count`` -- the
    size of what was actually promoted.
    """
    contract = contract or load_contract()
    rows = [evaluate_candidate(c, evaluation_as_of=evaluation_as_of, contract=contract) for c in candidates]
    seen_markets = {}
    for row in rows:
        seen_markets.setdefault(row["market"], 0)
        seen_markets[row["market"]] += 1
    duplicate_markets = sorted(m for m, n in seen_markets.items() if n > 1)
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evaluation_as_of": evaluation_as_of,
        "observation_pool_count": len(rows),
        "duplicate_markets": duplicate_markets,
        "summary": {
            "observation_pool": sum(1 for r in rows if r["state"] == STATE_OBSERVATION_POOL),
            "tradeable_universe": sum(1 for r in rows if r["state"] == STATE_TRADEABLE_UNIVERSE),
            "focused_review": sum(1 for r in rows if r["state"] == STATE_FOCUSED_REVIEW),
            "paper_ready": sum(1 for r in rows if r["state"] == STATE_PAPER_READY),
        },
        "candidates": rows,
        "authority": dict(_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet
