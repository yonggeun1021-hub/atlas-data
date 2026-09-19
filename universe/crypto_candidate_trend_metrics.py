#!/usr/bin/env python3
"""P5-08 Crypto candidate trend metric CALCULATION capability.

This module supplies the arithmetic that P5-08 does not currently have, and
nothing else. Before it, ``universe/crypto_candidate_promotion.py::_direction``
could only compare the two most-recently-finalized closes of one timeframe,
and ``evaluate_trend`` therefore always returned
``UNKNOWN/NO_RATIFIED_CANDIDATE_TREND_RULE``;
``universe/crypto_paper_buy_eligibility.py`` validates an ``ema_period`` of 20
in its baseline policy file but contains no EMA computation anywhere. There is
no other EMA implementation in ``universe/``, ``regime/`` or
``microstructure/`` to reuse.

WHAT THIS MODULE IS
    A directly callable, versioned, explicit-input calculator over an
    already-built, already-validated P4-07 market-evidence packet. Given a
    complete ``crypto_candidate_trend_calculation/1`` contract it returns the
    latest finalized daily close and daily EMA, the latest finalized 4h EMA
    and the 4h EMA at an explicitly requested lag, plus two purely
    mathematical comparisons (close-above-EMA, EMA-increasing).

WHAT THIS MODULE IS NOT
    It is not a trend *rule*, not a promotion rule, and not an eligibility
    rule. Its output ``status`` is only ever ``CALCULATED`` or ``UNAVAILABLE``
    -- never ``PASS``/``BUY``/``FOCUSED_REVIEW`` -- and every output carries
    ``calculation_only=true`` with ``investment_policy_ratified=false`` and
    ``candidate_promotion_authorized=false``. A positive comparison is a
    mathematical fact about candles, never a grant of candidate, buy, order,
    exchange, production or trading authority. Nothing here is wired into
    ``evaluate_trend``, the P5-08/P5-09 emitted packets, any runtime config,
    workflow, scheduler or natural collection: binding these numbers to a
    decision requires a separate, explicitly-ratified policy change that this
    module cannot make. U1/U2/U3/U4 and trend-policy selection stay
    unratified.

NO PRODUCTION DEFAULTS
    Every investment-shaped parameter (``ema_period``, ``seed_method``,
    ``min_finalized_candles`` per timeframe, ``rising_lag_bars``,
    ``decimal_precision``, ``decimal_rounding``, ``output_scale``) is required
    on every call. There is no default, no fallback, and no committed
    populated parameter file: an omitted or malformed field is a hard error,
    never a silently-chosen number. The two supported seed methods are
    explicitly-selected mathematical algorithms
    (``FIRST_FINALIZED_CLOSE``, ``SMA_FIRST_PERIOD_FINALIZED_CLOSES``); an
    omitted or unsupported method is rejected rather than guessed. Parameter
    values supplied by a caller or a test are calculation inputs, never a
    ratification of those numbers.

SOURCE DISCIPLINE
    The input packet is validated by the *existing* P5-08 validator
    (``crypto_candidate_promotion._validate_market_evidence_packet``), which
    already pins the packet schema, identity, payload hash, all-false evidence
    authority, and the exact ratified-vs-proposed P4 policy binding. This
    module adds no new source, no fetching, no TTL and no point-in-time
    interpretation of its own, and it never invents a freshness threshold: it
    consumes P4-07's own ``evidence_status`` / ``freshness`` / duplicate / gap
    results as prerequisites.

TWO FAIL-CLOSED MODES (both withhold comparisons; neither ever guesses)
    raise   -- the inputs themselves are malformed, inconsistent or tampered:
               an invalid calculation contract, an invalid packet schema/
               identity/hash, a candle row whose close time has not elapsed as
               of the packet's own ``as_of`` (i.e. not finalized), rows that
               are not strictly increasing in close time, or a non-positive /
               non-finite close price.
    UNAVAILABLE -- the inputs are well-formed but P4-07's own evidence quality
               or coverage does not support the requested calculation: a
               non-``PASS`` ``evidence_status``, non-``FRESH`` candles,
               reported duplicates or gaps, or too little finalized history
               for the explicitly requested period / minimum / lag. The failing
               timeframe's metrics are ``null``; healthy-timeframe metrics
               remain reported. Both comparisons are ``null`` in this case.

DETERMINISM AND LINEAGE
    Every function is a pure function of its arguments: no wall clock, no
    randomness, no network, and no mutation of any caller-supplied object.
    The same packet plus the same contract always yields a byte-identical
    payload. The output binds the exact calculation-contract digest and the
    exact source-packet digest, and embeds the source packet, so
    ``validate_trend_metrics`` recomputes the whole derivation from those
    exact sources. Editing a metric and recomputing ``payload_sha256`` (a
    self-rehash) does not survive validation. Changing a parameter produces a
    different contract digest and therefore a different payload -- new
    lineage, never a rewrite of previously-emitted evidence.
"""
from __future__ import annotations

import contextlib
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, localcontext
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class CryptoCandidateTrendMetricsError(ValueError):
    """Fail-closed P5-08 trend-calculation contract violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoCandidateTrendMetricsError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Read-only reuse. This module never edits P5-08; it borrows P5-08's already
# established market-evidence validator so that source/TTL/PIT semantics stay
# exactly as they are today.
PROMOTION = _load("crypto_candidate_trend_metrics_promotion", "universe/crypto_candidate_promotion.py")
MARKET_EVIDENCE = PROMOTION.MARKET_EVIDENCE


CALCULATION_CONTRACT_VERSION = "crypto_candidate_trend_calculation/1"
OUTPUT_SCHEMA_VERSION = "crypto_candidate_trend_metrics/1"

STATUS_CALCULATED = "CALCULATED"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUSES = (STATUS_CALCULATED, STATUS_UNAVAILABLE)

# The daily timeframe carries the close-above-EMA comparison; the 4h timeframe
# additionally carries the lagged EMA-increasing comparison. Order is fixed so
# that reason lists are deterministic.
DAILY_TIMEFRAME = "1d"
FOUR_HOUR_TIMEFRAME = "4h"
CALCULATION_TIMEFRAMES = (DAILY_TIMEFRAME, FOUR_HOUR_TIMEFRAME)

SEED_FIRST_FINALIZED_CLOSE = "FIRST_FINALIZED_CLOSE"
SEED_SMA_FIRST_PERIOD_FINALIZED_CLOSES = "SMA_FIRST_PERIOD_FINALIZED_CLOSES"
SUPPORTED_SEED_METHODS = (SEED_FIRST_FINALIZED_CLOSE, SEED_SMA_FIRST_PERIOD_FINALIZED_CLOSES)

SUPPORTED_ROUNDING_MODES = (
    "ROUND_CEILING", "ROUND_DOWN", "ROUND_FLOOR", "ROUND_HALF_DOWN",
    "ROUND_HALF_EVEN", "ROUND_HALF_UP", "ROUND_UP", "ROUND_05UP",
)

MIN_EMA_PERIOD = 2
MAX_DECIMAL_PRECISION = 60
MAX_OUTPUT_SCALE = 36

_CONTRACT_KEYS = {
    "schema_version", "contract_version", "timeframes",
    "rising_lag_bars", "decimal_precision", "decimal_rounding", "output_scale",
}
_CONTRACT_TIMEFRAME_KEYS = {"ema_period", "seed_method", "min_finalized_candles"}

_OUTPUT_KEYS = {
    "schema_version", "status", "market", "evaluation_as_of",
    "calculation_contract", "calculation_contract_sha256",
    "source", "source_packet", "timeframes", "comparisons",
    "unavailable_reasons", "authority", "payload_sha256",
}
_SOURCE_KEYS = {
    "market_evidence_schema_version", "market", "as_of", "captured_at",
    "policy_version", "policy_ratified", "payload_sha256",
}

_CANDLE_ROW_KEYS = {
    "open_time", "close_time", "opening_price", "high_price", "low_price",
    "trade_price", "candle_acc_trade_price", "candle_acc_trade_volume",
}

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# Hardcoded, never contract-driven and never caller-driven. ``calculation_only``
# is the only true flag this module may ever emit; no caller-supplied status,
# approval label or parameter value can change any of the rest.
_AUTHORITY = {
    "calculation_only": True,
    "investment_policy_ratified": False,
    "candidate_promotion_authorized": False,
    "buy_authorized": False,
    "order_authorized": False,
    "exchange_authorized": False,
    "real_capital_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Small typed validators -- every one rejects rather than substituting.
# ``bool`` is explicitly excluded everywhere an int is required: in Python
# ``isinstance(True, int)`` is true, and a boolean period/lag/scale is exactly
# the kind of malformed input that must fail closed rather than become 1.
# ---------------------------------------------------------------------------

def _require_int(value, label: str, *, minimum: int, maximum: int | None = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise CryptoCandidateTrendMetricsError(f"CONTRACT_INT_INVALID:{label}")
    if value < minimum or (maximum is not None and value > maximum):
        raise CryptoCandidateTrendMetricsError(f"CONTRACT_INT_OUT_OF_RANGE:{label}")
    return value


def _parse_utc(value, label: str) -> dt.datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise CryptoCandidateTrendMetricsError(f"UTC_INVALID:{label}")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoCandidateTrendMetricsError(f"UTC_INVALID:{label}") from exc


def _close_decimal(value, label: str) -> Decimal:
    """A close price is only ever read from an already-validated packet, where
    P4-07 emits it as a string. A float is refused rather than converted: the
    binary-float value would silently differ from the captured decimal.
    """
    if isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise CryptoCandidateTrendMetricsError(f"CLOSE_PRICE_INVALID:{label}") from exc
    else:
        raise CryptoCandidateTrendMetricsError(f"CLOSE_PRICE_TYPE_INVALID:{label}")
    if not parsed.is_finite() or parsed <= 0:
        raise CryptoCandidateTrendMetricsError(f"CLOSE_PRICE_NOT_POSITIVE_FINITE:{label}")
    return parsed


def _require_calculation_only_authority(value, label: str) -> None:
    if not isinstance(value, dict) or set(value) != set(_AUTHORITY):
        raise CryptoCandidateTrendMetricsError(f"AUTHORITY_KEYS_INVALID:{label}")
    if value.get("calculation_only") is not True:
        raise CryptoCandidateTrendMetricsError(f"AUTHORITY_CALCULATION_ONLY_INVALID:{label}")
    for key, flag in sorted(value.items()):
        if key != "calculation_only" and flag is not False:
            raise CryptoCandidateTrendMetricsError(f"AUTHORITY_NOT_FALSE:{label}:{key}")


# ---------------------------------------------------------------------------
# Calculation contract
# ---------------------------------------------------------------------------

def validate_calculation_contract(contract) -> dict:
    """Accept only a complete, exactly-shaped ``crypto_candidate_trend_calculation/1``.

    Every field is required. An unknown extra key is rejected too: that is what
    stops a caller from smuggling an ``"approved"``/``"ratified"``-style label
    into the calculation inputs and having it echoed back inside the output's
    parameter block.
    """
    if not isinstance(contract, dict):
        raise CryptoCandidateTrendMetricsError("CONTRACT_NOT_OBJECT")
    missing = sorted(_CONTRACT_KEYS - set(contract))
    if missing:
        raise CryptoCandidateTrendMetricsError("CONTRACT_FIELD_MISSING:" + ",".join(missing))
    unexpected = sorted(set(contract) - _CONTRACT_KEYS)
    if unexpected:
        raise CryptoCandidateTrendMetricsError("CONTRACT_FIELD_UNEXPECTED:" + ",".join(unexpected))
    # ``_require_int`` also rejects ``True``/``False`` here, which would
    # otherwise compare equal to 1.
    _require_int(contract["schema_version"], "schema_version", minimum=1, maximum=1)
    if contract["contract_version"] != CALCULATION_CONTRACT_VERSION:
        raise CryptoCandidateTrendMetricsError("CONTRACT_VERSION_INVALID")

    timeframes = contract["timeframes"]
    if not isinstance(timeframes, dict) or set(timeframes) != set(CALCULATION_TIMEFRAMES):
        raise CryptoCandidateTrendMetricsError("CONTRACT_TIMEFRAMES_INVALID")
    normalized_timeframes = {}
    for timeframe in CALCULATION_TIMEFRAMES:
        spec = timeframes[timeframe]
        if not isinstance(spec, dict):
            raise CryptoCandidateTrendMetricsError(f"CONTRACT_TIMEFRAME_NOT_OBJECT:{timeframe}")
        spec_missing = sorted(_CONTRACT_TIMEFRAME_KEYS - set(spec))
        if spec_missing:
            raise CryptoCandidateTrendMetricsError(
                f"CONTRACT_TIMEFRAME_FIELD_MISSING:{timeframe}:" + ",".join(spec_missing)
            )
        spec_unexpected = sorted(set(spec) - _CONTRACT_TIMEFRAME_KEYS)
        if spec_unexpected:
            raise CryptoCandidateTrendMetricsError(
                f"CONTRACT_TIMEFRAME_FIELD_UNEXPECTED:{timeframe}:" + ",".join(spec_unexpected)
            )
        if spec["seed_method"] not in SUPPORTED_SEED_METHODS:
            raise CryptoCandidateTrendMetricsError(f"CONTRACT_SEED_METHOD_UNSUPPORTED:{timeframe}")
        normalized_timeframes[timeframe] = {
            "ema_period": _require_int(
                spec["ema_period"], f"{timeframe}.ema_period", minimum=MIN_EMA_PERIOD,
            ),
            "seed_method": spec["seed_method"],
            "min_finalized_candles": _require_int(
                spec["min_finalized_candles"], f"{timeframe}.min_finalized_candles", minimum=1,
            ),
        }

    if contract["decimal_rounding"] not in SUPPORTED_ROUNDING_MODES:
        raise CryptoCandidateTrendMetricsError("CONTRACT_DECIMAL_ROUNDING_UNSUPPORTED")

    return {
        "schema_version": 1,
        "contract_version": CALCULATION_CONTRACT_VERSION,
        "timeframes": normalized_timeframes,
        "rising_lag_bars": _require_int(contract["rising_lag_bars"], "rising_lag_bars", minimum=1),
        "decimal_precision": _require_int(
            contract["decimal_precision"], "decimal_precision",
            minimum=1, maximum=MAX_DECIMAL_PRECISION,
        ),
        "decimal_rounding": contract["decimal_rounding"],
        "output_scale": _require_int(
            contract["output_scale"], "output_scale", minimum=0, maximum=MAX_OUTPUT_SCALE,
        ),
    }


# ---------------------------------------------------------------------------
# The EMA itself
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def _calculation_context(decimal_precision: int, decimal_rounding: str):
    """The one Decimal context every emitted number is produced under."""
    with localcontext() as ctx:
        ctx.prec = decimal_precision
        ctx.rounding = decimal_rounding
        yield ctx


def _quantize(value: Decimal, output_scale: int, label: str) -> Decimal:
    """Quantize under whatever Decimal context is currently active.

    Every caller runs this inside ``_calculation_context`` so the contract's
    own ``decimal_precision``/``decimal_rounding`` -- never the ambient process
    default -- decides how an emitted value is rounded.
    """
    quantum = Decimal(1).scaleb(-output_scale)
    try:
        return value.quantize(quantum)
    except InvalidOperation as exc:
        raise CryptoCandidateTrendMetricsError(f"DECIMAL_PRECISION_INSUFFICIENT:{label}") from exc


def seed_index(ema_period: int, seed_method: str) -> int:
    """Index of the first close that the EMA series can start on.

    ``FIRST_FINALIZED_CLOSE`` starts on the very first finalized close;
    ``SMA_FIRST_PERIOD_FINALIZED_CLOSES`` starts on the ``ema_period``-th one,
    because its seed is the simple average of the first ``ema_period`` closes.
    """
    if seed_method == SEED_FIRST_FINALIZED_CLOSE:
        return 0
    if seed_method == SEED_SMA_FIRST_PERIOD_FINALIZED_CLOSES:
        return ema_period - 1
    raise CryptoCandidateTrendMetricsError(f"SEED_METHOD_UNSUPPORTED:{seed_method}")


def compute_ema_series(
    closes,
    *,
    ema_period: int,
    seed_method: str,
    decimal_precision: int,
    decimal_rounding: str,
    output_scale: int,
) -> dict:
    """The full recursive EMA series over ``closes``, oldest-first.

    ``alpha = 2 / (ema_period + 1)`` and
    ``ema[i] = close[i] * alpha + ema[i-1] * (1 - alpha)``, with the seed fixed
    by ``seed_method``. Every arithmetic step runs inside an explicit Decimal
    context (``decimal_precision`` significant digits, ``decimal_rounding``)
    and every emitted level is quantized to ``output_scale`` decimal places, so
    the published numbers are exactly the numbers the comparisons were made on
    and the recursion is reproducible by hand.

    Returns ``{"seed_index": int, "values": [Decimal, ...]}`` where
    ``values[k]`` corresponds to ``closes[seed_index + k]``.
    """
    if not isinstance(closes, (list, tuple)) or not closes:
        raise CryptoCandidateTrendMetricsError("EMA_CLOSES_EMPTY")
    _require_int(ema_period, "ema_period", minimum=MIN_EMA_PERIOD)
    _require_int(decimal_precision, "decimal_precision", minimum=1, maximum=MAX_DECIMAL_PRECISION)
    _require_int(output_scale, "output_scale", minimum=0, maximum=MAX_OUTPUT_SCALE)
    if decimal_rounding not in SUPPORTED_ROUNDING_MODES:
        raise CryptoCandidateTrendMetricsError("DECIMAL_ROUNDING_UNSUPPORTED")
    start = seed_index(ema_period, seed_method)
    parsed = [_close_decimal(value, f"closes[{index}]") for index, value in enumerate(closes)]
    if len(parsed) <= start:
        raise CryptoCandidateTrendMetricsError("EMA_CLOSES_SHORTER_THAN_SEED_REQUIREMENT")

    with _calculation_context(decimal_precision, decimal_rounding):
        alpha = Decimal(2) / Decimal(ema_period + 1)
        one_minus_alpha = Decimal(1) - alpha
        if seed_method == SEED_FIRST_FINALIZED_CLOSE:
            seed = parsed[0]
        else:
            total = Decimal(0)
            for value in parsed[:ema_period]:
                total += value
            seed = total / Decimal(ema_period)
        values = [_quantize(seed, output_scale, "seed")]
        for index in range(start + 1, len(parsed)):
            previous = values[-1]
            level = parsed[index] * alpha + previous * one_minus_alpha
            values.append(_quantize(level, output_scale, f"ema[{index}]"))
    return {"seed_index": start, "values": values}


# ---------------------------------------------------------------------------
# Source packet
# ---------------------------------------------------------------------------

def _validate_source_packet(market_evidence_packet, market: str, evaluation_as_of: str) -> dict:
    """Reuse P5-08's existing market-evidence validator verbatim.

    It already pins the packet's schema, market identity, ``payload_sha256``,
    all-false evidence authority, timeframe set, per-timeframe candle identity
    and counts, ``as_of``/``captured_at`` ordering against ``evaluation_as_of``,
    and the exact ratified-vs-proposed P4 policy binding. Nothing about its
    source, TTL or point-in-time semantics is changed, extended or bypassed
    here; its failure is re-raised under this module's own error type so the
    calculation boundary stays legible.
    """
    if not isinstance(market, str) or not market:
        raise CryptoCandidateTrendMetricsError("MARKET_INVALID")
    if not isinstance(evaluation_as_of, str) or not _DATE_RE.fullmatch(evaluation_as_of):
        raise CryptoCandidateTrendMetricsError("EVALUATION_AS_OF_INVALID")
    if not isinstance(market_evidence_packet, dict):
        raise CryptoCandidateTrendMetricsError("MARKET_EVIDENCE_PACKET_MISSING")
    try:
        return PROMOTION._validate_market_evidence_packet(
            market_evidence_packet, market, evaluation_as_of,
        )
    except PROMOTION.CryptoCandidatePromotionError as exc:
        raise CryptoCandidateTrendMetricsError(f"MARKET_EVIDENCE_PACKET_INVALID:{exc}") from exc


def _finalized_closes(block: dict, timeframe: str, packet_as_of: dt.datetime) -> list[Decimal]:
    """Integrity-check one timeframe's finalized rows and return their closes.

    Anything wrong *with the rows themselves* raises: a row that is not
    finalized as of the packet's own ``as_of``, rows that are not strictly
    increasing in close time (which also covers duplicated close times), a
    close time at or before its own open time, or a non-positive/non-finite
    close price. These are corrupt-input conditions, not coverage gaps, so
    they must never be softened into an UNAVAILABLE metric.
    """
    rows = block.get("finalized_candles")
    if not isinstance(rows, list):
        raise CryptoCandidateTrendMetricsError(f"SOURCE_FINALIZED_CANDLES_INVALID:{timeframe}")
    closes: list[Decimal] = []
    previous_close: dt.datetime | None = None
    for index, row in enumerate(rows):
        label = f"{timeframe}[{index}]"
        if not isinstance(row, dict) or not _CANDLE_ROW_KEYS.issubset(set(row)):
            raise CryptoCandidateTrendMetricsError(f"SOURCE_CANDLE_ROW_INVALID:{label}")
        open_time = _parse_utc(row["open_time"], f"{label}.open_time")
        close_time = _parse_utc(row["close_time"], f"{label}.close_time")
        if close_time <= open_time:
            raise CryptoCandidateTrendMetricsError(f"SOURCE_CANDLE_TIMES_INVALID:{label}")
        if close_time > packet_as_of:
            raise CryptoCandidateTrendMetricsError(f"SOURCE_CANDLE_NOT_FINALIZED:{label}")
        if previous_close is not None and close_time <= previous_close:
            raise CryptoCandidateTrendMetricsError(f"SOURCE_CANDLE_ORDER_INVALID:{label}")
        previous_close = close_time
        closes.append(_close_decimal(row["trade_price"], f"{label}.trade_price"))
    return closes


def _evidence_quality_reasons(block: dict, timeframe: str) -> list[str]:
    """P4-07's own already-computed results, consumed as prerequisites.

    No freshness threshold, staleness window or gap definition is invented
    here -- these are P4-07's published ``duplicate_row_count``, ``gap_count``,
    ``freshness.status`` and ``evidence_status`` values, read as-is.
    """
    reasons: list[str] = []
    if block.get("duplicate_row_count"):
        reasons.append(f"{timeframe}:DUPLICATE_CANDLE_ROWS")
    if block.get("gap_count"):
        reasons.append(f"{timeframe}:CANDLE_GAP")
    freshness_status = (block.get("freshness") or {}).get("status")
    if freshness_status != MARKET_EVIDENCE.FRESH:
        reasons.append(f"{timeframe}:CANDLE_NOT_FRESH:{freshness_status}")
    evidence_status = block.get("evidence_status")
    if evidence_status != "PASS":
        reasons.append(f"{timeframe}:EVIDENCE_STATUS_NOT_PASS:{evidence_status}")
    return reasons


# ---------------------------------------------------------------------------
# Per-timeframe calculation
# ---------------------------------------------------------------------------

def _timeframe_result(
    packet: dict, timeframe: str, contract: dict, packet_as_of: dt.datetime,
) -> tuple[dict, list[str]]:
    spec = contract["timeframes"][timeframe]
    block = packet["candles"][timeframe]
    closes = _finalized_closes(block, timeframe, packet_as_of)
    rows = block["finalized_candles"]

    result = {
        "timeframe": timeframe,
        "ema_period": spec["ema_period"],
        "seed_method": spec["seed_method"],
        "min_finalized_candles": spec["min_finalized_candles"],
        "finalized_candle_count": len(closes),
        "first_finalized_close_time": rows[0]["close_time"] if rows else None,
        "latest_finalized_close_time": rows[-1]["close_time"] if rows else None,
        "latest_close": None,
        "seed_index": None,
        "ema_series_length": None,
        "latest_ema": None,
    }
    if timeframe == FOUR_HOUR_TIMEFRAME:
        result["rising_lag_bars"] = contract["rising_lag_bars"]
        result["lagged_ema"] = None
        result["lagged_ema_close_time"] = None

    reasons = _evidence_quality_reasons(block, timeframe)
    start = seed_index(spec["ema_period"], spec["seed_method"])
    if len(closes) < start + 1:
        reasons.append(f"{timeframe}:INSUFFICIENT_FINALIZED_CANDLES_FOR_SEED")
    if len(closes) < spec["min_finalized_candles"]:
        reasons.append(f"{timeframe}:BELOW_MIN_FINALIZED_CANDLES")
    if timeframe == FOUR_HOUR_TIMEFRAME and len(closes) - start < contract["rising_lag_bars"] + 1:
        reasons.append(f"{timeframe}:INSUFFICIENT_EMA_SERIES_FOR_LAG")
    if reasons:
        return result, reasons

    series = compute_ema_series(
        closes,
        ema_period=spec["ema_period"],
        seed_method=spec["seed_method"],
        decimal_precision=contract["decimal_precision"],
        decimal_rounding=contract["decimal_rounding"],
        output_scale=contract["output_scale"],
    )
    values = series["values"]
    # The close is emitted under the same declared context as the EMA levels,
    # so the two sides of the comparison are rounded identically.
    with _calculation_context(contract["decimal_precision"], contract["decimal_rounding"]):
        latest_close = _quantize(closes[-1], contract["output_scale"], f"{timeframe}.close")
    result["latest_close"] = str(latest_close)
    result["seed_index"] = series["seed_index"]
    result["ema_series_length"] = len(values)
    result["latest_ema"] = str(values[-1])
    if timeframe == FOUR_HOUR_TIMEFRAME:
        lag = contract["rising_lag_bars"]
        result["lagged_ema"] = str(values[-1 - lag])
        result["lagged_ema_close_time"] = rows[series["seed_index"] + len(values) - 1 - lag]["close_time"]
    return result, []


# ---------------------------------------------------------------------------
# Public calculation API
# ---------------------------------------------------------------------------

def build_trend_metrics(
    market_evidence_packet,
    *,
    market: str,
    evaluation_as_of: str,
    calculation_contract,
) -> dict:
    """Calculate 1d/4h EMA metrics for one market from one P4-07 packet.

    Pure and deterministic: no wall clock is read, no argument is mutated, and
    the same packet plus the same contract always produces a byte-identical
    payload. The returned ``status`` is ``CALCULATED`` or ``UNAVAILABLE`` and
    nothing else; it is never an eligibility, promotion or order verdict.
    """
    contract = validate_calculation_contract(calculation_contract)
    packet = _validate_source_packet(market_evidence_packet, market, evaluation_as_of)
    packet_as_of = _parse_utc(packet["as_of"], "market_evidence.as_of")

    timeframes = {}
    unavailable_reasons: list[str] = []
    for timeframe in CALCULATION_TIMEFRAMES:
        result, reasons = _timeframe_result(packet, timeframe, contract, packet_as_of)
        timeframes[timeframe] = result
        unavailable_reasons.extend(reasons)

    comparisons = {
        "daily_close_above_daily_ema": None,
        "four_hour_ema_rising": None,
    }
    if not unavailable_reasons:
        daily = timeframes[DAILY_TIMEFRAME]
        four_hour = timeframes[FOUR_HOUR_TIMEFRAME]
        # Purely mathematical, strict comparisons over the exact emitted
        # (quantized) values, so the published numbers justify the published
        # booleans. Neither one is a rule, a threshold or a verdict.
        comparisons["daily_close_above_daily_ema"] = Decimal(daily["latest_close"]) > Decimal(daily["latest_ema"])
        comparisons["four_hour_ema_rising"] = Decimal(four_hour["latest_ema"]) > Decimal(four_hour["lagged_ema"])

    payload = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "status": STATUS_CALCULATED if not unavailable_reasons else STATUS_UNAVAILABLE,
        "market": market,
        "evaluation_as_of": evaluation_as_of,
        "calculation_contract": copy.deepcopy(contract),
        "calculation_contract_sha256": payload_sha256(contract),
        "source": {
            "market_evidence_schema_version": packet["schema_version"],
            "market": packet["market"],
            "as_of": packet["as_of"],
            "captured_at": packet["captured_at"],
            "policy_version": packet["policy_version"],
            "policy_ratified": packet["policy_ratified"],
            "payload_sha256": packet["payload_sha256"],
        },
        "source_packet": copy.deepcopy(packet),
        "timeframes": timeframes,
        "comparisons": comparisons,
        "unavailable_reasons": unavailable_reasons,
        "authority": dict(_AUTHORITY),
    }
    payload["payload_sha256"] = payload_sha256(payload)
    return payload


def validate_trend_metrics(metrics, *, market_evidence_packet=None) -> dict:
    """Re-derive the whole calculation from the embedded exact sources.

    The payload hash alone is not trusted: a caller who edits a metric and
    recomputes ``payload_sha256`` still fails here, because the metrics are
    rebuilt from the embedded (independently hash-pinned) source packet and the
    embedded calculation contract and then compared byte-for-byte. Supplying
    ``market_evidence_packet`` additionally requires it to be exactly the
    embedded packet.
    """
    if not isinstance(metrics, dict) or set(metrics) != _OUTPUT_KEYS:
        raise CryptoCandidateTrendMetricsError("OUTPUT_SCHEMA_MISMATCH")
    if metrics["schema_version"] != OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidateTrendMetricsError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    if metrics["status"] not in STATUSES:
        raise CryptoCandidateTrendMetricsError("OUTPUT_STATUS_INVALID")
    if not isinstance(metrics["source"], dict) or set(metrics["source"]) != _SOURCE_KEYS:
        raise CryptoCandidateTrendMetricsError("OUTPUT_SOURCE_SCHEMA_MISMATCH")
    _require_calculation_only_authority(metrics["authority"], "trend_metrics")

    claimed = metrics["payload_sha256"]
    if not isinstance(claimed, str) or not _SHA_RE.fullmatch(claimed):
        raise CryptoCandidateTrendMetricsError("PAYLOAD_SHA256_INVALID")
    unsigned = copy.deepcopy(metrics)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        raise CryptoCandidateTrendMetricsError("PAYLOAD_SHA256_MISMATCH")

    # Parameter and source lineage must still be the ones that were bound at
    # build time, independent of the derivation replay below.
    if payload_sha256(metrics["calculation_contract"]) != metrics["calculation_contract_sha256"]:
        raise CryptoCandidateTrendMetricsError("CALCULATION_CONTRACT_SHA256_MISMATCH")
    source_packet = metrics["source_packet"]
    if not isinstance(source_packet, dict):
        raise CryptoCandidateTrendMetricsError("SOURCE_PACKET_INVALID")
    if source_packet.get("payload_sha256") != metrics["source"]["payload_sha256"]:
        raise CryptoCandidateTrendMetricsError("SOURCE_PACKET_SHA256_MISMATCH")
    if market_evidence_packet is not None and canonical_json(market_evidence_packet) != canonical_json(source_packet):
        raise CryptoCandidateTrendMetricsError("SOURCE_PACKET_MISMATCH")

    rebuilt = build_trend_metrics(
        source_packet,
        market=metrics["market"],
        evaluation_as_of=metrics["evaluation_as_of"],
        calculation_contract=metrics["calculation_contract"],
    )
    if canonical_json(rebuilt) != canonical_json(metrics):
        raise CryptoCandidateTrendMetricsError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(metrics)
