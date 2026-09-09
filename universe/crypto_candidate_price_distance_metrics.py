#!/usr/bin/env python3
"""P5-08 Crypto candidate price-distance OBSERVATION capability.

P5-08's ``evaluate_overextension`` returns ``UNKNOWN`` /
``NO_RATIFIED_OVEREXTENSION_THRESHOLD`` because no mechanical overextension
definition is ratified anywhere in this repository. That stays exactly as it
is. What was missing was not the predicate -- it was the *measurement*: the
merged trend calculator (``universe/crypto_candidate_trend_metrics.py``)
publishes a latest close and a latest EMA per timeframe, but nothing anywhere
reports how far the close sits from that EMA, or what the close did over an
explicitly requested number of candles.

WHAT THIS MODULE IS
    A directly callable, versioned, explicit-input calculator that turns an
    already-validated P4-07 market-evidence packet into exactly two numeric
    observations per timeframe:

        close_to_ema_fraction        = (latest_close - latest_ema) / latest_ema
        lagged_close_return_fraction = (latest_close - lagged_close) / lagged_close

    Both are reported as **fractions**, never as percentages. ``0.1`` means one
    tenth, not one tenth of one percent; this module never multiplies by 100
    and never emits a ``%``-suffixed string.

WHAT THIS MODULE IS NOT
    It is not an overextension rule and it does not resolve U2. A positive,
    negative or large fraction is a mathematical fact about candles -- never a
    verdict, never a candidate/entry/PASS/FAIL, and never a bound or threshold.
    ``status`` is only ever ``CALCULATED`` or ``UNAVAILABLE``. No threshold is
    defined, read, or compared against anywhere in this file, so no consumer
    can extract one from it. ``evaluate_overextension`` is not imported, not
    wired, and not changed; P5-08/P5-09's emitted packets, every runtime
    config, workflow, registry, scheduler and natural collection are untouched.

NO ECONOMIC DEFAULTS
    Every parameter is required on every call and is a *caller-selected*
    input, never a ratified number. That specifically includes both
    ``return_lag_candles`` values: each timeframe must name its own explicit
    positive integer lag, there is no shared fallback, no chosen default, and
    no committed populated parameter file. Both lags -- together with the
    complete EMA parameter set they are calculated against -- are part of the
    single ``calculation_contract`` that ``calculation_contract_sha256``
    digests, so a lag can never be swapped without producing different
    lineage. The one new serialization parameter, ``fraction_output_scale``,
    is likewise explicit: a fraction is a different quantity from a price and
    silently reusing the price ``output_scale`` for it would be an invented
    choice, so the caller must state it.

REUSED, NOT REIMPLEMENTED
    The EMA is not implemented again here. ``build_trend_metrics`` is called
    with the caller's own trend calculation contract and its published
    ``latest_close`` / ``latest_ema`` are consumed verbatim, so both sides of
    ``close_to_ema_fraction`` are the exact emitted values the trend module
    already stands behind. The lagged close is read from the same finalized
    rows through the trend module's own ``_finalized_closes`` integrity check
    and quantized through its ``_calculation_context`` / ``_quantize`` under
    the contract's declared ``decimal_precision`` / ``decimal_rounding``, so
    numerator and denominator are rounded identically. P4-07 finality,
    freshness, duplicate, gap and malformed-row guards are reused as-is: this
    module adds no source, no fetch, no TTL, no point-in-time rule and no
    freshness threshold of its own.

TWO FAIL-CLOSED MODES (neither ever guesses a number)
    raise   -- the inputs themselves are malformed, inconsistent or tampered:
               an invalid price-distance or trend contract, an invalid packet
               schema/identity/hash/policy pin, an unfinalized row, rows that
               are not strictly increasing in close time, or a non-positive /
               non-finite close.
    UNAVAILABLE -- the inputs are well-formed but do not support some
               requested observation. Each of the four observations is
               resolved independently, so a 4h evidence failure never blanks a
               healthy 1d measurement and an insufficient return lag never
               blanks that same timeframe's close-to-EMA measurement. Every
               withheld observation is null, carries a named denominator
               status, and names its exact cause in ``unavailable_reasons``.

NAMED ZERO DENOMINATORS
    A close is always positive, but the *emitted* (quantized) EMA or lagged
    close can still be exactly zero at a coarse ``output_scale``. That is a
    real, reachable input -- not an impossibility -- so it is never divided by
    and never silently treated as a missing row. The affected observation is
    null with the explicit denominator status
    ``ZERO_DENOMINATOR_LATEST_EMA`` / ``ZERO_DENOMINATOR_LAGGED_CLOSE`` and a
    matching reason; the other observation stays reported.

DETERMINISM AND INDEPENDENT VALIDATION
    Every function is a pure function of its arguments: no wall clock, no
    randomness, no network, no mutation of any caller-supplied object.
    ``validate_price_distance_metrics`` requires the original market,
    evaluation date, market-evidence packet and calculation contract as its
    own mandatory arguments and re-derives the whole payload from *those*.
    It never reads a trusted parameter out of the untrusted output: editing
    the evaluation date, a lag, a fraction or the status and recomputing every
    embedded digest still fails, because a self-rehashed output cannot supply
    the originals it is being checked against.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import importlib.util
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class CryptoCandidatePriceDistanceMetricsError(ValueError):
    """Fail-closed P5-08 price-distance contract violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoCandidatePriceDistanceMetricsError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Read-only reuse of the merged PR603 trend calculator. Its EMA, its Decimal
# context, its quantization, its candle-integrity check and (through it) P5-08's
# existing market-evidence validator are all consumed unchanged. Nothing in this
# module edits, wraps or re-implements any of them.
TREND = _load("crypto_candidate_price_distance_trend", "universe/crypto_candidate_trend_metrics.py")

CALCULATION_CONTRACT_VERSION = "crypto_candidate_price_distance_calculation/1"
OUTPUT_SCHEMA_VERSION = "crypto_candidate_price_distance_metrics/1"

# Reused verbatim so the two modules can never drift apart on vocabulary.
STATUS_CALCULATED = TREND.STATUS_CALCULATED
STATUS_UNAVAILABLE = TREND.STATUS_UNAVAILABLE
STATUSES = TREND.STATUSES
CALCULATION_TIMEFRAMES = TREND.CALCULATION_TIMEFRAMES
DAILY_TIMEFRAME = TREND.DAILY_TIMEFRAME
FOUR_HOUR_TIMEFRAME = TREND.FOUR_HOUR_TIMEFRAME
MAX_OUTPUT_SCALE = TREND.MAX_OUTPUT_SCALE

# Named denominator states. ``AVAILABLE`` is the only one that can carry a
# fraction; the other three always accompany a null.
DENOMINATOR_AVAILABLE = "AVAILABLE"
DENOMINATOR_ZERO_LATEST_EMA = "ZERO_DENOMINATOR_LATEST_EMA"
DENOMINATOR_ZERO_LAGGED_CLOSE = "ZERO_DENOMINATOR_LAGGED_CLOSE"
DENOMINATOR_SOURCE_UNAVAILABLE = "SOURCE_UNAVAILABLE"
DENOMINATOR_STATUSES = (
    DENOMINATOR_AVAILABLE,
    DENOMINATOR_ZERO_LATEST_EMA,
    DENOMINATOR_ZERO_LAGGED_CLOSE,
    DENOMINATOR_SOURCE_UNAVAILABLE,
)

_CONTRACT_KEYS = {
    "schema_version", "contract_version", "trend_calculation_contract",
    "timeframes", "fraction_output_scale",
}
_CONTRACT_TIMEFRAME_KEYS = {"return_lag_candles"}

_OUTPUT_KEYS = {
    "schema_version", "status", "market", "evaluation_as_of",
    "calculation_contract", "calculation_contract_sha256",
    "source", "trend_metrics", "trend_metrics_sha256",
    "timeframes", "unavailable_reasons", "authority", "payload_sha256",
}
_SOURCE_KEYS = {
    "market_evidence_schema_version", "market", "as_of", "captured_at",
    "policy_version", "policy_ratified", "payload_sha256",
}
_TIMEFRAME_OUTPUT_KEYS = {
    "timeframe", "return_lag_candles", "finalized_candle_count",
    "latest_finalized_close_time", "lagged_close_time",
    "latest_close", "latest_ema", "lagged_close",
    "close_to_ema_fraction", "lagged_close_return_fraction",
    "close_to_ema_denominator_status", "lagged_close_return_denominator_status",
}

# Hardcoded, never contract-driven and never caller-driven -- the identical
# block the trend calculator emits. ``calculation_only`` is the only true flag
# this module may ever emit.
_AUTHORITY = dict(TREND._AUTHORITY)


canonical_json = TREND.canonical_json
payload_sha256 = TREND.payload_sha256


# ---------------------------------------------------------------------------
# Calculation contract
# ---------------------------------------------------------------------------

def validate_calculation_contract(contract) -> dict:
    """Accept only a complete ``crypto_candidate_price_distance_calculation/1``.

    The nested trend contract is validated by the trend module's own
    ``validate_calculation_contract`` -- not re-checked here -- so the EMA
    parameter rules stay in exactly one place. Unknown extra keys are rejected
    at both levels, which is what stops a caller from smuggling an
    ``"approved"``/``"ratified"``-style label into the parameter block and
    having it echoed back inside the output.
    """
    if not isinstance(contract, dict):
        raise CryptoCandidatePriceDistanceMetricsError("CONTRACT_NOT_OBJECT")
    missing = sorted(_CONTRACT_KEYS - set(contract))
    if missing:
        raise CryptoCandidatePriceDistanceMetricsError("CONTRACT_FIELD_MISSING:" + ",".join(missing))
    unexpected = sorted(set(contract) - _CONTRACT_KEYS)
    if unexpected:
        raise CryptoCandidatePriceDistanceMetricsError("CONTRACT_FIELD_UNEXPECTED:" + ",".join(unexpected))
    _require_int(contract["schema_version"], "schema_version", minimum=1, maximum=1)
    if contract["contract_version"] != CALCULATION_CONTRACT_VERSION:
        raise CryptoCandidatePriceDistanceMetricsError("CONTRACT_VERSION_INVALID")

    try:
        trend_contract = TREND.validate_calculation_contract(contract["trend_calculation_contract"])
    except TREND.CryptoCandidateTrendMetricsError as exc:
        raise CryptoCandidatePriceDistanceMetricsError(
            f"TREND_CALCULATION_CONTRACT_INVALID:{exc}"
        ) from exc

    timeframes = contract["timeframes"]
    if not isinstance(timeframes, dict) or set(timeframes) != set(CALCULATION_TIMEFRAMES):
        raise CryptoCandidatePriceDistanceMetricsError("CONTRACT_TIMEFRAMES_INVALID")
    normalized_timeframes = {}
    for timeframe in CALCULATION_TIMEFRAMES:
        spec = timeframes[timeframe]
        if not isinstance(spec, dict):
            raise CryptoCandidatePriceDistanceMetricsError(
                f"CONTRACT_TIMEFRAME_NOT_OBJECT:{timeframe}"
            )
        spec_missing = sorted(_CONTRACT_TIMEFRAME_KEYS - set(spec))
        if spec_missing:
            raise CryptoCandidatePriceDistanceMetricsError(
                f"CONTRACT_TIMEFRAME_FIELD_MISSING:{timeframe}:" + ",".join(spec_missing)
            )
        spec_unexpected = sorted(set(spec) - _CONTRACT_TIMEFRAME_KEYS)
        if spec_unexpected:
            raise CryptoCandidatePriceDistanceMetricsError(
                f"CONTRACT_TIMEFRAME_FIELD_UNEXPECTED:{timeframe}:" + ",".join(spec_unexpected)
            )
        # Each timeframe names its own lag. There is deliberately no shared
        # value and no fallback: an omitted 4h lag is an error, not the 1d one.
        normalized_timeframes[timeframe] = {
            "return_lag_candles": _require_int(
                spec["return_lag_candles"], f"{timeframe}.return_lag_candles", minimum=1,
            ),
        }

    return {
        "schema_version": 1,
        "contract_version": CALCULATION_CONTRACT_VERSION,
        "trend_calculation_contract": trend_contract,
        "timeframes": normalized_timeframes,
        "fraction_output_scale": _require_int(
            contract["fraction_output_scale"], "fraction_output_scale",
            minimum=0, maximum=MAX_OUTPUT_SCALE,
        ),
    }


def _require_int(value, label: str, *, minimum: int, maximum: int | None = None) -> int:
    """Reuse the trend module's integer guard (which also rejects ``bool``,
    since ``isinstance(True, int)`` is true and a boolean lag must never
    become 1) and re-raise under this module's own error type.
    """
    try:
        return TREND._require_int(value, label, minimum=minimum, maximum=maximum)
    except TREND.CryptoCandidateTrendMetricsError as exc:
        raise CryptoCandidatePriceDistanceMetricsError(str(exc)) from exc


# ---------------------------------------------------------------------------
# The two observations
# ---------------------------------------------------------------------------

def observation_fraction(
    numerator_value: Decimal,
    denominator_value: Decimal,
    *,
    decimal_precision: int,
    decimal_rounding: str,
    fraction_output_scale: int,
    label: str,
) -> Decimal:
    """``(numerator - denominator) / denominator`` as a plain fraction.

    Runs inside the trend module's own Decimal context so the declared
    ``decimal_precision`` / ``decimal_rounding`` -- never the ambient process
    default -- decides the arithmetic, and quantizes through its own
    ``_quantize`` at the explicitly declared ``fraction_output_scale``.

    The result is a fraction. It is never scaled by 100, never formatted as a
    percentage, and never compared to anything. Callers must not pass a zero
    denominator; ``_timeframe_result`` names that case instead of dividing.
    """
    if denominator_value == 0:
        raise CryptoCandidatePriceDistanceMetricsError(f"ZERO_DENOMINATOR:{label}")
    with TREND._calculation_context(decimal_precision, decimal_rounding):
        ratio = (numerator_value - denominator_value) / denominator_value
        try:
            return TREND._quantize(ratio, fraction_output_scale, label)
        except TREND.CryptoCandidateTrendMetricsError as exc:
            raise CryptoCandidatePriceDistanceMetricsError(str(exc)) from exc


def _timeframe_result(
    timeframe: str,
    contract: dict,
    trend_metrics: dict,
    closes: list,
    rows: list,
) -> tuple[dict, list[str]]:
    """Resolve one timeframe's two observations independently of each other.

    ``closes``/``rows`` are the already-integrity-checked finalized rows for
    this timeframe. ``trend_metrics`` is the merged trend calculator's own
    output; its per-timeframe block is null exactly when that timeframe's P4-07
    evidence quality or history did not support the EMA, so this module inherits
    that decision instead of re-deriving it.
    """
    trend_contract = contract["trend_calculation_contract"]
    lag = contract["timeframes"][timeframe]["return_lag_candles"]
    trend_block = trend_metrics["timeframes"][timeframe]

    result = {
        "timeframe": timeframe,
        "return_lag_candles": lag,
        "finalized_candle_count": len(closes),
        "latest_finalized_close_time": trend_block["latest_finalized_close_time"],
        "lagged_close_time": None,
        "latest_close": trend_block["latest_close"],
        "latest_ema": trend_block["latest_ema"],
        "lagged_close": None,
        "close_to_ema_fraction": None,
        "lagged_close_return_fraction": None,
        "close_to_ema_denominator_status": DENOMINATOR_SOURCE_UNAVAILABLE,
        "lagged_close_return_denominator_status": DENOMINATOR_SOURCE_UNAVAILABLE,
    }

    # This timeframe's own upstream causes, verbatim -- no new vocabulary and
    # no re-interpretation of P4-07's freshness/duplicate/gap results. The
    # other timeframe's reasons are deliberately not inherited.
    reasons = [
        reason for reason in trend_metrics["unavailable_reasons"]
        if reason.startswith(f"{timeframe}:")
    ]
    source_available = trend_block["latest_close"] is not None and trend_block["latest_ema"] is not None

    # --- observation 1: distance from the EMA -------------------------------
    if source_available:
        latest_close = Decimal(trend_block["latest_close"])
        latest_ema = Decimal(trend_block["latest_ema"])
        if latest_ema == 0:
            # Reachable: a positive close can still quantize to zero at a
            # coarse output_scale. Named, never divided by, never guessed.
            result["close_to_ema_denominator_status"] = DENOMINATOR_ZERO_LATEST_EMA
            reasons.append(f"{timeframe}:{DENOMINATOR_ZERO_LATEST_EMA}")
        else:
            result["close_to_ema_fraction"] = str(observation_fraction(
                latest_close, latest_ema,
                decimal_precision=trend_contract["decimal_precision"],
                decimal_rounding=trend_contract["decimal_rounding"],
                fraction_output_scale=contract["fraction_output_scale"],
                label=f"{timeframe}.close_to_ema_fraction",
            ))
            result["close_to_ema_denominator_status"] = DENOMINATOR_AVAILABLE

    # --- observation 2: return over the explicitly requested lag ------------
    if len(closes) < lag + 1:
        reasons.append(f"{timeframe}:INSUFFICIENT_FINALIZED_CANDLES_FOR_RETURN_LAG")
        return result, reasons
    if not source_available:
        # The rows exist, but this timeframe's evidence quality already failed
        # upstream; a return computed off rejected evidence would be a new,
        # weaker source rule. Withheld, with the upstream reasons already listed.
        return result, reasons

    # Quantized under the identical declared context as the trend module's own
    # ``latest_close``, so both ends of the return are rounded the same way.
    with TREND._calculation_context(
        trend_contract["decimal_precision"], trend_contract["decimal_rounding"],
    ):
        try:
            lagged_close = TREND._quantize(
                closes[-1 - lag], trend_contract["output_scale"], f"{timeframe}.lagged_close",
            )
        except TREND.CryptoCandidateTrendMetricsError as exc:
            raise CryptoCandidatePriceDistanceMetricsError(str(exc)) from exc
    result["lagged_close"] = str(lagged_close)
    result["lagged_close_time"] = rows[-1 - lag]["close_time"]
    if lagged_close == 0:
        result["lagged_close_return_denominator_status"] = DENOMINATOR_ZERO_LAGGED_CLOSE
        reasons.append(f"{timeframe}:{DENOMINATOR_ZERO_LAGGED_CLOSE}")
        return result, reasons
    result["lagged_close_return_fraction"] = str(observation_fraction(
        Decimal(trend_block["latest_close"]), lagged_close,
        decimal_precision=trend_contract["decimal_precision"],
        decimal_rounding=trend_contract["decimal_rounding"],
        fraction_output_scale=contract["fraction_output_scale"],
        label=f"{timeframe}.lagged_close_return_fraction",
    ))
    result["lagged_close_return_denominator_status"] = DENOMINATOR_AVAILABLE
    return result, reasons


# ---------------------------------------------------------------------------
# Public calculation API
# ---------------------------------------------------------------------------

def build_price_distance_metrics(
    market_evidence_packet,
    *,
    market: str,
    evaluation_as_of: str,
    calculation_contract,
) -> dict:
    """Observe 1d/4h price distance for one market from one P4-07 packet.

    Pure and deterministic: no wall clock is read, no argument is mutated, and
    the same packet plus the same contract always produces a byte-identical
    payload. ``status`` is ``CALCULATED`` only when all four observations were
    produced; it is never an overextension, eligibility, promotion or order
    verdict, and no fraction is compared to any bound.
    """
    contract = validate_calculation_contract(calculation_contract)
    trend_contract = contract["trend_calculation_contract"]

    # The packet is validated twice, on purpose: once here for the finalized
    # rows this module reads directly, and once inside build_trend_metrics.
    # Both go through P5-08's existing validator, so there is exactly one
    # source/TTL/PIT rule and no bypass.
    try:
        packet = TREND._validate_source_packet(market_evidence_packet, market, evaluation_as_of)
        packet_as_of = TREND._parse_utc(packet["as_of"], "market_evidence.as_of")
        trend_metrics = TREND.build_trend_metrics(
            market_evidence_packet,
            market=market,
            evaluation_as_of=evaluation_as_of,
            calculation_contract=trend_contract,
        )
    except TREND.CryptoCandidateTrendMetricsError as exc:
        raise CryptoCandidatePriceDistanceMetricsError(f"TREND_METRICS_UNAVAILABLE:{exc}") from exc

    timeframes = {}
    unavailable_reasons: list[str] = []
    for timeframe in CALCULATION_TIMEFRAMES:
        block = packet["candles"][timeframe]
        try:
            # The trend module's own candle-integrity guard: unfinalized,
            # out-of-order, duplicated-time or non-positive rows raise here
            # exactly as they do for the EMA.
            closes = TREND._finalized_closes(block, timeframe, packet_as_of)
        except TREND.CryptoCandidateTrendMetricsError as exc:
            raise CryptoCandidatePriceDistanceMetricsError(f"SOURCE_CANDLES_INVALID:{exc}") from exc
        result, reasons = _timeframe_result(
            timeframe, contract, trend_metrics, closes, block["finalized_candles"],
        )
        timeframes[timeframe] = result
        unavailable_reasons.extend(reasons)

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
        # The trend payload already embeds the exact source packet, so the
        # whole EMA derivation stays independently re-checkable from here.
        "trend_metrics": copy.deepcopy(trend_metrics),
        "trend_metrics_sha256": trend_metrics["payload_sha256"],
        "timeframes": timeframes,
        "unavailable_reasons": unavailable_reasons,
        "authority": dict(_AUTHORITY),
    }
    payload["payload_sha256"] = payload_sha256(payload)
    return payload


def validate_price_distance_metrics(
    metrics,
    *,
    market: str,
    evaluation_as_of: str,
    market_evidence_packet,
    calculation_contract,
) -> dict:
    """Re-derive the payload from independently supplied ORIGINAL inputs.

    Every trusted input is a mandatory argument. None of them is ever read out
    of ``metrics``: the market, the evaluation date, the P4-07 packet and the
    calculation contract must be handed in by the caller from their own
    original source, and the output's corresponding claims are then compared
    *against* them. This is the whole point of the signature -- an output that
    was edited and re-signed cannot supply the originals it is checked against,
    so a self-rehashed evaluation date, market, lag or fraction fails here even
    though every embedded digest is internally consistent.
    """
    if not isinstance(metrics, dict) or set(metrics) != _OUTPUT_KEYS:
        raise CryptoCandidatePriceDistanceMetricsError("OUTPUT_SCHEMA_MISMATCH")
    if metrics["schema_version"] != OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidatePriceDistanceMetricsError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    if metrics["status"] not in STATUSES:
        raise CryptoCandidatePriceDistanceMetricsError("OUTPUT_STATUS_INVALID")
    if not isinstance(metrics["source"], dict) or set(metrics["source"]) != _SOURCE_KEYS:
        raise CryptoCandidatePriceDistanceMetricsError("OUTPUT_SOURCE_SCHEMA_MISMATCH")
    try:
        TREND._require_calculation_only_authority(metrics["authority"], "price_distance_metrics")
    except TREND.CryptoCandidateTrendMetricsError as exc:
        raise CryptoCandidatePriceDistanceMetricsError(str(exc)) from exc

    claimed = metrics["payload_sha256"]
    if not isinstance(claimed, str) or not TREND._SHA_RE.fullmatch(claimed):
        raise CryptoCandidatePriceDistanceMetricsError("PAYLOAD_SHA256_INVALID")
    unsigned = copy.deepcopy(metrics)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        raise CryptoCandidatePriceDistanceMetricsError("PAYLOAD_SHA256_MISMATCH")

    # The output's identity claims are checked against the independently
    # supplied originals -- never the other way round.
    if metrics["market"] != market:
        raise CryptoCandidatePriceDistanceMetricsError("ORIGINAL_MARKET_MISMATCH")
    if metrics["evaluation_as_of"] != evaluation_as_of:
        raise CryptoCandidatePriceDistanceMetricsError("ORIGINAL_EVALUATION_AS_OF_MISMATCH")
    original_contract = validate_calculation_contract(calculation_contract)
    if payload_sha256(original_contract) != metrics["calculation_contract_sha256"]:
        raise CryptoCandidatePriceDistanceMetricsError("ORIGINAL_CALCULATION_CONTRACT_MISMATCH")
    if canonical_json(metrics["calculation_contract"]) != canonical_json(original_contract):
        raise CryptoCandidatePriceDistanceMetricsError("EMBEDDED_CALCULATION_CONTRACT_MISMATCH")
    trend_metrics = metrics["trend_metrics"]
    if not isinstance(trend_metrics, dict):
        raise CryptoCandidatePriceDistanceMetricsError("TREND_METRICS_INVALID")
    if trend_metrics.get("payload_sha256") != metrics["trend_metrics_sha256"]:
        raise CryptoCandidatePriceDistanceMetricsError("TREND_METRICS_SHA256_MISMATCH")

    # The embedded trend payload is re-derived by its own merged validator, so
    # the EMA lineage is checked by the module that owns it rather than here.
    try:
        TREND.validate_trend_metrics(trend_metrics, market_evidence_packet=market_evidence_packet)
    except TREND.CryptoCandidateTrendMetricsError as exc:
        raise CryptoCandidatePriceDistanceMetricsError(f"TREND_METRICS_INVALID:{exc}") from exc

    rebuilt = build_price_distance_metrics(
        market_evidence_packet,
        market=market,
        evaluation_as_of=evaluation_as_of,
        calculation_contract=calculation_contract,
    )
    if canonical_json(rebuilt) != canonical_json(metrics):
        raise CryptoCandidatePriceDistanceMetricsError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(metrics)
