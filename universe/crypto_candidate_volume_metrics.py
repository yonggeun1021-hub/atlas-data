#!/usr/bin/env python3
"""P5-08 explicit-input Crypto candidate volume calculation.

Validated P4-07 finalized candles already retain real base volume
(``candle_acc_trade_volume``) and KRW turnover (``candle_acc_trade_price``),
but P5-08's ``evaluate_volume_liquidity`` reads only evidence-family presence
and trade count -- it computes no volume baseline and no latest-to-baseline
ratio. This module adds exactly that observation statistic and nothing else.

What it is:

* a pure calculator over one already-built, already-validated P4-07
  ``upbit_market_evidence_packet/1``. The packet is revalidated here through
  the existing P5-08 consumer boundary
  (``universe/crypto_candidate_promotion.py::_validate_market_evidence_packet``,
  reused rather than restated) so its schema, content hash, exact P4 policy
  binding -- ratified or honestly unratified -- timestamps, timeframes,
  market identity, counts and all-false authority are checked before a single
  number is read;
* built on the *same* volume arithmetic P3-07 already publishes:
  ``discovery/market_behavior.py::volume_baseline_features`` (latest / prior
  arithmetic mean and latest / prior median under a 50-digit Decimal
  context), imported unchanged. No second formula is introduced here, and
  P3-07's own source contract is untouched -- this module never presents
  Upbit data to that module's CRYPTO ``kraken_public_api`` source registry.

What it is not: it selects no window, no period default, no threshold, no
TTL/PIT rule, no source and no metric. Both timeframes' prior candle counts
are caller-supplied explicit positive integers, and both base volume and KRW
turnover, each with both mean and median ratios, are always reported.

Two null shapes are deliberately distinct and never conflated:

* ``ZERO_BASELINE_UNKNOWN`` -- the timeframe was calculated, but a zero mean
  or median denominator makes that one ratio unknown. Never 0, never
  infinity. The other metrics for that timeframe are still present.
* timeframe ``UNAVAILABLE`` -- the source evidence itself is not usable
  (non-PASS P4 evidence status: stale/gap/duplicate/no finalized candle, or
  insufficient finalized history for the requested window). Its metrics are
  null with named reasons, while a healthy timeframe keeps its own metrics.

Malformed input is a different thing again: an inconsistent schema, hash,
market identity, candle time ordering, or a non-string/non-finite/negative
number fails closed by raising, never by quietly reporting UNAVAILABLE.

Every emitted ``status`` here is a *calculation* status. It is never a
candidate PASS/FAIL, and every ``authority`` field stays hardcoded false:
this module changes no P5-08/P5-09 emitted packet, no criterion evaluator and
no production wiring.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class CryptoCandidateVolumeMetricsError(ValueError):
    """Fail-closed P5-08 volume-calculation contract violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoCandidateVolumeMetricsError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MARKET_BEHAVIOR = _load(
    "crypto_candidate_volume_metrics_market_behavior", "discovery/market_behavior.py"
)
PROMOTION = _load(
    "crypto_candidate_volume_metrics_promotion", "universe/crypto_candidate_promotion.py"
)


OUTPUT_SCHEMA_VERSION = "crypto_candidate_volume_metrics/1"
CALCULATION_SCHEMA_VERSION = "crypto_candidate_volume_calculation/1"

# Exactly the two timeframes this capability is approved for. Both counts are
# always explicit; neither is defaulted, inferred or tuned here.
CALCULATED_TIMEFRAMES = ("1d", "4h")

STATUS_CALCULATED = "CALCULATED"
STATUS_UNAVAILABLE = "UNAVAILABLE"

# The two P4-07 finalized-candle numeric fields, reported together so no
# metric choice is silently made on the caller's behalf.
METRIC_FIELDS = (
    ("base_volume", "candle_acc_trade_volume"),
    ("quote_turnover", "candle_acc_trade_price"),
)

_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

_CANDLE_ROW_FIELDS = {
    "open_time", "close_time", "opening_price", "high_price", "low_price",
    "trade_price", "candle_acc_trade_price", "candle_acc_trade_volume",
}

_METRICS_PACKET_FIELDS = {
    "schema_version", "calculation_schema_version", "market", "evaluation_as_of",
    "evidence_as_of", "evidence_captured_at", "evidence_policy_version",
    "evidence_policy_ratified", "source_packet_sha256",
    "calculation_contract_sha256", "prior_finalized_candle_counts", "timeframes",
    "status", "unavailable_reasons", "authority", "payload_sha256",
}


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise CryptoCandidateVolumeMetricsError(f"UTC_INVALID:{label}")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise CryptoCandidateVolumeMetricsError(f"UTC_INVALID:{label}") from exc


def _volume_decimal(value: object, label: str) -> Decimal:
    """P4-07 emits every candle number as a string. A bool, int, float,
    ``None``, NaN/Infinity or negative value is malformed input, never a
    silently coerced observation.
    """
    if not isinstance(value, str):
        raise CryptoCandidateVolumeMetricsError(f"VOLUME_VALUE_NOT_STRING:{label}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CryptoCandidateVolumeMetricsError(f"VOLUME_VALUE_INVALID:{label}") from exc
    if not parsed.is_finite() or parsed < 0:
        raise CryptoCandidateVolumeMetricsError(f"VOLUME_VALUE_INVALID:{label}")
    return parsed


def normalize_calculation_contract(contract: dict) -> dict:
    """The caller's explicit, defaults-free calculation contract.

    ``prior_finalized_candle_count`` is required for each of 1d and 4h, as an
    exact positive integer (``bool`` is not an integer here). There is no
    automatic window selection, no fallback and no tuning.
    """
    if not isinstance(contract, dict) or set(contract) != {
        "schema_version", "prior_finalized_candle_counts",
    }:
        raise CryptoCandidateVolumeMetricsError("CALCULATION_CONTRACT_FIELDS_MISMATCH")
    if contract["schema_version"] != CALCULATION_SCHEMA_VERSION:
        raise CryptoCandidateVolumeMetricsError("CALCULATION_CONTRACT_SCHEMA_MISMATCH")
    counts = contract["prior_finalized_candle_counts"]
    if not isinstance(counts, dict) or set(counts) != set(CALCULATED_TIMEFRAMES):
        raise CryptoCandidateVolumeMetricsError(
            "CALCULATION_CONTRACT_TIMEFRAMES_MISMATCH"
        )
    for timeframe in CALCULATED_TIMEFRAMES:
        value = counts[timeframe]
        if type(value) is not int or value < 1:
            raise CryptoCandidateVolumeMetricsError(
                f"PRIOR_FINALIZED_CANDLE_COUNT_INVALID:{timeframe}"
            )
    return copy.deepcopy(contract)


def _finalized_rows(evidence: dict, market: str, timeframe: str) -> list[dict]:
    """Parse and time-check one timeframe's finalized candle rows.

    Structural or temporal inconsistency raises. Sufficiency of history is a
    separate, non-fatal availability question decided by the caller.
    """
    rows = evidence.get("finalized_candles")
    if not isinstance(rows, list):
        raise CryptoCandidateVolumeMetricsError(
            f"FINALIZED_CANDLES_INVALID:{market}:{timeframe}"
        )
    parsed = []
    for index, row in enumerate(rows):
        label = f"{market}:{timeframe}:{index}"
        if not isinstance(row, dict) or set(row) != _CANDLE_ROW_FIELDS:
            raise CryptoCandidateVolumeMetricsError(
                f"CANDLE_ROW_FIELDS_MISMATCH:{label}"
            )
        open_time = _parse_utc(row["open_time"], f"{label}.open_time")
        close_time = _parse_utc(row["close_time"], f"{label}.close_time")
        if close_time <= open_time:
            raise CryptoCandidateVolumeMetricsError(f"CANDLE_ROW_TIME_INVALID:{label}")
        parsed.append({"open_time": open_time, "close_time": close_time, "raw": row})
    for previous, current in zip(parsed, parsed[1:]):
        if (
            current["open_time"] <= previous["open_time"]
            or current["close_time"] <= previous["close_time"]
        ):
            raise CryptoCandidateVolumeMetricsError(
                f"CANDLE_ROW_SEQUENCE_INVALID:{market}:{timeframe}"
            )
    return parsed


def _metric_block(
    prior_rows: list[dict], latest_row: dict, field: str, contract: dict, label: str
) -> dict:
    prior_values = [
        _volume_decimal(row["raw"][field], f"{label}:{field}:{index}")
        for index, row in enumerate(prior_rows)
    ]
    latest_value = _volume_decimal(latest_row["raw"][field], f"{label}:{field}:latest")
    computed = MARKET_BEHAVIOR.volume_baseline_features(prior_values, latest_value)
    render = MARKET_BEHAVIOR._render
    return {
        "latest": render(computed["latest"], contract),
        "prior_mean": render(computed["prior_mean"], contract),
        "prior_median": render(computed["prior_median"], contract),
        "latest_vs_prior_mean": (
            None if computed["latest_vs_prior_mean"] is None
            else render(computed["latest_vs_prior_mean"], contract)
        ),
        "latest_vs_prior_median": (
            None if computed["latest_vs_prior_median"] is None
            else render(computed["latest_vs_prior_median"], contract)
        ),
        "baseline_status": computed["baseline_status"],
    }


def _unavailable_timeframe(
    timeframe: str, prior_count: int, observed_count: int, reasons: list
) -> dict:
    return {
        "timeframe": timeframe,
        "status": STATUS_UNAVAILABLE,
        "unavailable_reasons": sorted(set(reasons)),
        "prior_finalized_candle_count": prior_count,
        "observed_finalized_candle_count": observed_count,
        "window": None,
        "base_volume": None,
        "quote_turnover": None,
    }


def _timeframe_metrics(
    evidence: dict, market: str, timeframe: str, prior_count: int, contract: dict
) -> dict:
    rows = _finalized_rows(evidence, market, timeframe)
    reasons = []
    evidence_status = evidence.get("evidence_status")
    if evidence_status != "PASS":
        fail_reasons = evidence.get("fail_closed_reasons")
        detail = (
            ",".join(fail_reasons)
            if isinstance(fail_reasons, list) and fail_reasons
            else str(evidence_status)
        )
        reasons.append(f"EVIDENCE_STATUS_NOT_PASS:{detail}")
    required = prior_count + 1
    if len(rows) < required:
        reasons.append(f"INSUFFICIENT_FINALIZED_HISTORY:{len(rows)}/{required}")
    if reasons:
        return _unavailable_timeframe(timeframe, prior_count, len(rows), reasons)

    window_rows = rows[-required:]
    latest_row = window_rows[-1]
    prior_rows = window_rows[:-1]
    label = f"{market}:{timeframe}"
    stamp = "%Y-%m-%dT%H:%M:%SZ"
    result = {
        "timeframe": timeframe,
        "status": STATUS_CALCULATED,
        "unavailable_reasons": [],
        "prior_finalized_candle_count": prior_count,
        "observed_finalized_candle_count": len(rows),
        "window": {
            "prior_first_open_time": prior_rows[0]["open_time"].strftime(stamp),
            "prior_last_close_time": prior_rows[-1]["close_time"].strftime(stamp),
            "latest_open_time": latest_row["open_time"].strftime(stamp),
            "latest_close_time": latest_row["close_time"].strftime(stamp),
        },
    }
    for name, field in METRIC_FIELDS:
        result[name] = _metric_block(prior_rows, latest_row, field, contract, label)
    return result


def build_volume_metrics(
    market_evidence_packet: dict,
    market: str,
    evaluation_as_of: str,
    calculation_contract: dict,
) -> dict:
    """Compute both volume metrics for both timeframes from one validated
    P4-07 packet. Deterministic and input-preserving: no wall-clock is read,
    and neither argument is mutated.
    """
    if not isinstance(market, str) or not market:
        raise CryptoCandidateVolumeMetricsError("MARKET_INVALID")
    contract = normalize_calculation_contract(calculation_contract)
    # Reuses -- never restates -- the existing P5-08 consumer boundary: exact
    # schema, payload hash, P4 ratified/unratified policy pin, timestamp
    # order, timeframe set, market identity, counts and false authority. Its
    # rejection is re-raised under this module's own fail-closed error, with
    # the upstream cause preserved verbatim in the message and the chain.
    try:
        validated = PROMOTION._validate_market_evidence_packet(
            copy.deepcopy(market_evidence_packet), market, evaluation_as_of
        )
    except PROMOTION.CryptoCandidatePromotionError as exc:
        raise CryptoCandidateVolumeMetricsError(
            f"MARKET_EVIDENCE_INVALID:{exc}"
        ) from exc
    p3_contract = MARKET_BEHAVIOR.load_contract()

    counts = contract["prior_finalized_candle_counts"]
    timeframes = {}
    for timeframe in CALCULATED_TIMEFRAMES:
        evidence = validated["candles"].get(timeframe)
        if not isinstance(evidence, dict):
            raise CryptoCandidateVolumeMetricsError(
                f"MARKET_EVIDENCE_TIMEFRAME_MISSING:{market}:{timeframe}"
            )
        timeframes[timeframe] = _timeframe_metrics(
            evidence, market, timeframe, counts[timeframe], p3_contract
        )

    unavailable_reasons = sorted(
        f"{timeframe}:{reason}"
        for timeframe, result in timeframes.items()
        for reason in result["unavailable_reasons"]
    )
    status = (
        STATUS_CALCULATED
        if all(
            result["status"] == STATUS_CALCULATED for result in timeframes.values()
        )
        else STATUS_UNAVAILABLE
    )
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "calculation_schema_version": CALCULATION_SCHEMA_VERSION,
        "market": market,
        "evaluation_as_of": evaluation_as_of,
        "evidence_as_of": validated["as_of"],
        "evidence_captured_at": validated["captured_at"],
        "evidence_policy_version": validated["policy_version"],
        "evidence_policy_ratified": validated["policy_ratified"],
        "source_packet_sha256": validated["payload_sha256"],
        "calculation_contract_sha256": payload_sha256(contract),
        "prior_finalized_candle_counts": copy.deepcopy(counts),
        "timeframes": timeframes,
        # A calculation status, never a candidate PASS/FAIL.
        "status": status,
        "unavailable_reasons": unavailable_reasons,
        "authority": dict(PROMOTION._ROW_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_volume_metrics(
    metrics: dict,
    original_packet: dict,
    original_contract: dict,
    original_evaluation_as_of: str,
) -> dict:
    """Rederive the complete result from the *original* inputs.

    A caller that edits the evaluation date, a ratio, a baseline status, a
    window endpoint or a calculation status and then rehashes the output
    cannot pass: the original evaluation date is supplied independently,
    the digests are re-bound to the original packet and contract, and the
    whole derivation is rebuilt and compared canonically.
    """
    if not isinstance(metrics, dict) or set(metrics) != _METRICS_PACKET_FIELDS:
        raise CryptoCandidateVolumeMetricsError("OUTPUT_SCHEMA_MISMATCH")
    if metrics.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidateVolumeMetricsError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    claimed = metrics.get("payload_sha256")
    if not isinstance(claimed, str) or not _SHA_RE.fullmatch(claimed):
        raise CryptoCandidateVolumeMetricsError("OUTPUT_SHA256_INVALID")
    unsigned = copy.deepcopy(metrics)
    unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != claimed:
        raise CryptoCandidateVolumeMetricsError("OUTPUT_SHA256_MISMATCH")

    if not isinstance(original_packet, dict) or metrics.get(
        "source_packet_sha256"
    ) != original_packet.get("payload_sha256"):
        raise CryptoCandidateVolumeMetricsError("SOURCE_PACKET_BINDING_MISMATCH")
    contract = normalize_calculation_contract(original_contract)
    if metrics.get("calculation_contract_sha256") != payload_sha256(contract):
        raise CryptoCandidateVolumeMetricsError("CALCULATION_CONTRACT_BINDING_MISMATCH")

    if metrics.get("evaluation_as_of") != original_evaluation_as_of:
        raise CryptoCandidateVolumeMetricsError("EVALUATION_AS_OF_BINDING_MISMATCH")

    rebuilt = build_volume_metrics(
        original_packet,
        metrics.get("market"),
        original_evaluation_as_of,
        original_contract,
    )
    if canonical_json(rebuilt) != canonical_json(metrics):
        raise CryptoCandidateVolumeMetricsError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(metrics)
