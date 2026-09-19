#!/usr/bin/env python3
"""Policy-neutral P9-05 normalized observation preparation.

The caller must choose the source/session profile, 15-minute elapsed buckets,
mean or median baseline, and prior comparable-session count.  This module
contains no operational default, threshold, schedule, clock, or trading path.
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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "intraday_risk_observation_preparation_contract.json"
VOLUME_ARITHMETIC_SOURCE = ROOT / "discovery" / "market_behavior.py"
VOLUME_ARITHMETIC_SHA256 = "c29d26ff140990bb85a522a4ca338a36623f3a65914e167b400a168574b4d9f9"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class IntradayRiskObservationPreparationError(ValueError):
    """Fail-closed observation-preparation contract violation."""


def _reject_duplicate_keys(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise IntradayRiskObservationPreparationError(
                f"JSON_DUPLICATE_KEY:{key}"
            )
        value[key] = item
    return value


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(
            Path(path).read_text(encoding="utf-8"),
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (OSError, json.JSONDecodeError) as exc:
        raise IntradayRiskObservationPreparationError(
            f"JSON_READ_FAILED:{path}:{exc}"
        ) from exc


def _validate_contract(value: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "definition_schema_version",
        "current_session_schema_version", "prior_session_schema_version",
        "output_schema_version", "consumer_observation_schema_version",
        "consumer_contract", "metric_formula_source", "volume_arithmetic",
        "elapsed_timeframes", "baseline_methods",
        "prior_comparable_session_count", "source_profiles", "session_profiles",
        "metric_semantics", "repository_defaults", "authority",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskObservationPreparationError("CONTRACT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != 1
        or value.get("contract_version")
        != "intraday_risk_observation_preparation/1"
        or value.get("definition_schema_version")
        != "intraday_risk_observation_definition/1"
        or value.get("current_session_schema_version")
        != "intraday_risk_current_session/1"
        or value.get("prior_session_schema_version")
        != "intraday_risk_prior_comparable_session/1"
        or value.get("output_schema_version")
        != "intraday_risk_observation_preparation_receipt/1"
        or value.get("consumer_observation_schema_version")
        != "intraday_risk_observation_batch/3"
    ):
        raise IntradayRiskObservationPreparationError("CONTRACT_IDENTITY_MISMATCH")
    expected_defaults = {
        "elapsed_timeframe": "ABSENT",
        "baseline_method": "ABSENT",
        "prior_comparable_session_count": "ABSENT",
        "session_profile": "ABSENT",
        "thresholds": "ABSENT",
    }
    if value.get("repository_defaults") != expected_defaults:
        raise IntradayRiskObservationPreparationError("CONTRACT_DEFAULTS_INVALID")
    expected_dependencies = {
        "consumer_contract": {
            "ref": "config/intraday_risk_escalation_contract.json",
            "sha256": "ded62f9d702329ca4f0626829a33db855b927319908690207e3fc9abfee70bca",
        },
        "metric_formula_source": {
            "ref": "execution/intraday_risk_escalation.py",
            "sha256": "6f48a69c2b2de876b537c1138c5c2449cfbc5b4ab1f261cb39911bb553fb52ed",
        },
        "volume_arithmetic": {
            "ref": "discovery/market_behavior.py",
            "sha256": VOLUME_ARITHMETIC_SHA256,
            "function": "volume_baseline_features",
        },
    }
    for name, expected in expected_dependencies.items():
        if value.get(name) != expected:
            raise IntradayRiskObservationPreparationError(
                f"CONTRACT_DEPENDENCY_INVALID:{name}"
            )
    if value.get("baseline_methods") != ["PRIOR_MEAN", "PRIOR_MEDIAN"]:
        raise IntradayRiskObservationPreparationError("CONTRACT_BASELINES_INVALID")
    if value.get("elapsed_timeframes") != {
        "15m": {"duration_seconds": 900, "selection": "CALLER_REQUIRED_NO_DEFAULT"}
    }:
        raise IntradayRiskObservationPreparationError("CONTRACT_TIMEFRAMES_INVALID")
    if value.get("prior_comparable_session_count") != (
        "CALLER_REQUIRED_POSITIVE_INTEGER_NO_DEFAULT"
    ):
        raise IntradayRiskObservationPreparationError("CONTRACT_LOOKBACK_INVALID")
    expected_authority = {
        "normalized_observation_preparation_only": True,
        "session_calendar_authorized": False,
        "provider_selection_authorized": False,
        "risk_threshold_policy_authorized": False,
        "risk_evaluation_authorized": False,
        "strategy_authorized": False,
        "action_generation_authorized": False,
        "order_generation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if value.get("authority") != expected_authority:
        raise IntradayRiskObservationPreparationError("CONTRACT_AUTHORITY_INVALID")
    if set(value.get("source_profiles", {})) != {
        "US_COMPLETED_15M", "KRX_COMPLETED_15M", "UPBIT_COMPLETED_15M"
    } or set(value.get("session_profiles", {})) != {
        "US_REGULAR", "US_EARLY_CLOSE", "KOREA_REGULAR", "UPBIT_UTC_DAY"
    }:
        raise IntradayRiskObservationPreparationError("CONTRACT_PROFILES_INVALID")
    expected_sources = {
        "US_COMPLETED_15M": {
            "market": "US",
            "provider_contract_ref": "config/us_completed_market_data_contract.json",
            "provider_contract_sha256": "800770f7e9afe93b33d49ea0430c1b6f7bfbc55a872291cc71f69b3958e206b7",
            "allowed_session_profile_ids": ["US_REGULAR", "US_EARLY_CLOSE"],
            "price_basis": "RAW",
            "completed_bucket_volume_field": "volume",
        },
        "KRX_COMPLETED_15M": {
            "market": "KOREA",
            "provider_contract_ref": "config/krx_market_data_contract.json",
            "provider_contract_sha256": "437b07ec2f1c35ee56236a5044e73bc9b566faa2350d7fe9bc14292ce8061649",
            "allowed_session_profile_ids": ["KOREA_REGULAR"],
            "price_basis": "CALLER_SOURCE_CONTRACT",
            "completed_bucket_volume_field": "volume",
        },
        "UPBIT_COMPLETED_15M": {
            "market": "CRYPTO",
            "provider_contract_ref": "microstructure/upbit_candle_finalization.py",
            "provider_contract_sha256": "d865ddd14952d86dee123b7dfe8baf34598280074a3dcef86a54d91143552d22",
            "allowed_session_profile_ids": ["UPBIT_UTC_DAY"],
            "price_basis": "PROVIDER_NATIVE",
            "completed_bucket_volume_field": "candle_acc_trade_volume",
        },
    }
    if value["source_profiles"] != expected_sources:
        raise IntradayRiskObservationPreparationError("CONTRACT_SOURCE_PROFILES_INVALID")
    expected_sessions = {
        "US_REGULAR": {
            "market": "US", "timezone": "America/New_York",
            "open_local": "09:30:00", "close_local": "16:00:00",
            "duration_seconds": 23400, "session_kind": "REGULAR",
            "boundary_authority": "DATE_SPECIFIC_OFFICIAL_CALENDAR_REQUIRED",
        },
        "US_EARLY_CLOSE": {
            "market": "US", "timezone": "America/New_York",
            "open_local": "09:30:00", "close_local": "13:00:00",
            "duration_seconds": 12600, "session_kind": "EARLY_CLOSE",
            "boundary_authority": "DATE_SPECIFIC_OFFICIAL_CALENDAR_REQUIRED",
        },
        "KOREA_REGULAR": {
            "market": "KOREA", "timezone": "Asia/Seoul",
            "open_local": "09:00:00", "close_local": "15:30:00",
            "duration_seconds": 23400, "session_kind": "REGULAR",
            "boundary_authority": "DATE_SPECIFIC_OFFICIAL_CALENDAR_REQUIRED",
        },
        "UPBIT_UTC_DAY": {
            "market": "CRYPTO", "timezone": "UTC",
            "open_local": "00:00:00", "close_local": "00:00:00",
            "duration_seconds": 86400, "session_kind": "CONTINUOUS_24H",
            "boundary_authority": (
                "CALLER_SELECTED_UPBIT_PROVIDER_CONTRACT_NOT_UNIVERSAL_CRYPTO_FACT"
            ),
        },
    }
    if value["session_profiles"] != expected_sessions:
        raise IntradayRiskObservationPreparationError("CONTRACT_SESSION_PROFILES_INVALID")
    semantics = value.get("metric_semantics", {})
    if semantics.get("DRAWDOWN_FRACTION") != {
        "semantic_id": "REFERENCE_CLOSE_TO_LAST_DECLINE_FRACTION/1",
        "display_name": "Reference-close decline fraction",
        "formula": "max(0,(reference_close-last_price)/reference_close)",
        "true_peak_drawdown": False,
    } or semantics.get("RELATIVE_VOLUME_FRACTION") != {
        "semantic_id": "CUMULATIVE_VOLUME_TO_EXPLICIT_PRIOR_BASELINE/1",
        "formula": "cumulative_volume/expected_volume_to_time",
        "zero_denominator": "NOT_AVAILABLE",
    }:
        raise IntradayRiskObservationPreparationError("CONTRACT_SEMANTICS_INVALID")
    file_pins = [
        *expected_dependencies.values(),
        *(
            {
                "ref": profile["provider_contract_ref"],
                "sha256": profile["provider_contract_sha256"],
            }
            for profile in expected_sources.values()
        ),
    ]
    for pin in file_pins:
        path = ROOT / pin["ref"]
        try:
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError as exc:
            raise IntradayRiskObservationPreparationError(
                f"CONTRACT_PIN_READ_FAILED:{pin['ref']}"
            ) from exc
        if actual != pin["sha256"]:
            raise IntradayRiskObservationPreparationError(
                f"CONTRACT_PIN_SHA_MISMATCH:{pin['ref']}"
            )
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _load_volume_arithmetic():
    try:
        source_sha256 = hashlib.sha256(VOLUME_ARITHMETIC_SOURCE.read_bytes()).hexdigest()
    except OSError as exc:
        raise IntradayRiskObservationPreparationError(
            "VOLUME_ARITHMETIC_PIN_READ_FAILED"
        ) from exc
    if source_sha256 != VOLUME_ARITHMETIC_SHA256:
        raise IntradayRiskObservationPreparationError(
            "VOLUME_ARITHMETIC_PIN_SHA_MISMATCH"
        )
    spec = importlib.util.spec_from_file_location(
        "intraday_risk_volume_arithmetic", VOLUME_ARITHMETIC_SOURCE
    )
    if spec is None or spec.loader is None:
        raise IntradayRiskObservationPreparationError("VOLUME_ARITHMETIC_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VOLUME_ARITHMETIC = _load_volume_arithmetic()


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise IntradayRiskObservationPreparationError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise IntradayRiskObservationPreparationError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise IntradayRiskObservationPreparationError(code)
    return value


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise IntradayRiskObservationPreparationError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise IntradayRiskObservationPreparationError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise IntradayRiskObservationPreparationError(code)
    return parsed


def _instant(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or value.endswith("Z"):
        raise IntradayRiskObservationPreparationError(code)
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise IntradayRiskObservationPreparationError(code) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise IntradayRiskObservationPreparationError(code)
    return parsed


def _decimal(value, code: str, *, positive: bool) -> Decimal:
    if not isinstance(value, str):
        raise IntradayRiskObservationPreparationError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise IntradayRiskObservationPreparationError(code) from exc
    if not parsed.is_finite() or (parsed <= 0 if positive else parsed < 0):
        raise IntradayRiskObservationPreparationError(code)
    return parsed


def _decimal_text(value: Decimal) -> str:
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _definition(value: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "definition_id", "market", "source_profile_id",
        "session_profile_id", "provider_id", "provider_contract_ref",
        "provider_contract_sha256", "elapsed_timeframe", "baseline_method",
        "prior_comparable_session_count",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskObservationPreparationError("DEFINITION_FIELDS_MISMATCH")
    if value.get("schema_version") != contract["definition_schema_version"]:
        raise IntradayRiskObservationPreparationError("DEFINITION_SCHEMA_MISMATCH")
    _token(value.get("definition_id"), "DEFINITION_ID_INVALID")
    _token(value.get("provider_id"), "PROVIDER_ID_INVALID")
    source = contract["source_profiles"].get(value.get("source_profile_id"))
    session = contract["session_profiles"].get(value.get("session_profile_id"))
    if source is None:
        raise IntradayRiskObservationPreparationError("SOURCE_PROFILE_INVALID")
    if session is None:
        raise IntradayRiskObservationPreparationError("SESSION_PROFILE_INVALID")
    if (
        value.get("market") != source["market"]
        or value.get("market") != session["market"]
        or value.get("session_profile_id")
        not in source["allowed_session_profile_ids"]
    ):
        raise IntradayRiskObservationPreparationError("PROFILE_MARKET_MISMATCH")
    if (
        value.get("provider_contract_ref") != source["provider_contract_ref"]
        or value.get("provider_contract_sha256")
        != source["provider_contract_sha256"]
    ):
        raise IntradayRiskObservationPreparationError("PROVIDER_CONTRACT_MISMATCH")
    if value.get("elapsed_timeframe") not in contract["elapsed_timeframes"]:
        raise IntradayRiskObservationPreparationError("ELAPSED_TIMEFRAME_INVALID")
    if value.get("baseline_method") not in contract["baseline_methods"]:
        raise IntradayRiskObservationPreparationError("BASELINE_METHOD_INVALID")
    count = value.get("prior_comparable_session_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise IntradayRiskObservationPreparationError("PRIOR_SESSION_COUNT_INVALID")
    return copy.deepcopy(value)


def _validate_boundary(
    session_id: str,
    open_value: str,
    close_value: str,
    profile: dict,
    context: str,
) -> tuple[dt.datetime, dt.datetime]:
    if not isinstance(session_id, str) or DATE_RE.fullmatch(session_id) is None:
        raise IntradayRiskObservationPreparationError(f"SESSION_ID_INVALID:{context}")
    try:
        dt.date.fromisoformat(session_id)
        zone = ZoneInfo(profile["timezone"])
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise IntradayRiskObservationPreparationError(
            f"SESSION_TIMEZONE_INVALID:{context}"
        ) from exc
    opened = _instant(open_value, f"SESSION_OPEN_INVALID:{context}")
    closed = _instant(close_value, f"SESSION_CLOSE_INVALID:{context}")
    local_open = opened.astimezone(zone)
    local_close = closed.astimezone(zone)
    if (
        local_open.date().isoformat() != session_id
        or local_open.strftime("%H:%M:%S") != profile["open_local"]
        or local_close.strftime("%H:%M:%S") != profile["close_local"]
        or local_open.utcoffset() != opened.utcoffset()
        or local_close.utcoffset() != closed.utcoffset()
        or (closed - opened).total_seconds() != profile["duration_seconds"]
    ):
        raise IntradayRiskObservationPreparationError(
            f"SESSION_BOUNDARY_MISMATCH:{context}"
        )
    return opened, closed


def _bars(value, opened: dt.datetime, observed: dt.datetime, duration: int, context: str):
    if not isinstance(value, list) or not value:
        raise IntradayRiskObservationPreparationError(f"BARS_EMPTY:{context}")
    expected_open = opened
    normalized = []
    for index, row in enumerate(value):
        row_context = f"{context}:{index}"
        if not isinstance(row, dict) or set(row) != {"open_at", "close_at", "volume"}:
            raise IntradayRiskObservationPreparationError(
                f"BAR_FIELDS_MISMATCH:{row_context}"
            )
        bar_open = _instant(row.get("open_at"), f"BAR_OPEN_INVALID:{row_context}")
        bar_close = _instant(row.get("close_at"), f"BAR_CLOSE_INVALID:{row_context}")
        if bar_open != expected_open:
            raise IntradayRiskObservationPreparationError(
                f"MISSING_OR_DUPLICATE_BUCKET:{row_context}"
            )
        if (bar_close - bar_open).total_seconds() != duration:
            raise IntradayRiskObservationPreparationError(
                f"BAR_DURATION_INVALID:{row_context}"
            )
        if bar_close > observed:
            raise IntradayRiskObservationPreparationError(
                f"UNFINISHED_OR_FUTURE_BUCKET:{row_context}"
            )
        volume = _decimal(
            row.get("volume"), f"BAR_VOLUME_INVALID:{row_context}", positive=False
        )
        normalized.append((bar_open, bar_close, volume))
        expected_open = bar_close
    return normalized


def _current(value: dict, definition: dict, contract: dict) -> dict:
    fields = {
        "schema_version", "subject_id", "market", "session_id",
        "session_profile_id", "session_open_at", "session_close_at", "observed_at",
        "provider_timestamp", "received_at", "reference_close", "open_price",
        "last_price", "bid_price", "ask_price", "bars", "source_ref",
        "source_sha256", "available_at",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskObservationPreparationError("CURRENT_FIELDS_MISMATCH")
    if value.get("schema_version") != contract["current_session_schema_version"]:
        raise IntradayRiskObservationPreparationError("CURRENT_SCHEMA_MISMATCH")
    if (
        value.get("market") != definition["market"]
        or value.get("session_profile_id") != definition["session_profile_id"]
    ):
        raise IntradayRiskObservationPreparationError("CURRENT_PROFILE_MISMATCH")
    profile = contract["session_profiles"][definition["session_profile_id"]]
    opened, closed = _validate_boundary(
        value.get("session_id"), value.get("session_open_at"),
        value.get("session_close_at"), profile, "CURRENT"
    )
    observed = _utc(value.get("observed_at"), "CURRENT_OBSERVED_AT_INVALID")
    provider = _utc(value.get("provider_timestamp"), "PROVIDER_TIMESTAMP_INVALID")
    received = _utc(value.get("received_at"), "RECEIVED_AT_INVALID")
    available = _utc(value.get("available_at"), "CURRENT_AVAILABLE_AT_INVALID")
    if not opened <= observed <= closed or not provider <= received <= observed:
        raise IntradayRiskObservationPreparationError("CURRENT_TIME_ORDER_INVALID")
    if available > observed:
        raise IntradayRiskObservationPreparationError("CURRENT_SOURCE_FROM_FUTURE")
    duration = contract["elapsed_timeframes"][definition["elapsed_timeframe"]][
        "duration_seconds"
    ]
    bars = _bars(value.get("bars"), opened, observed, duration, "CURRENT")
    if bars[-1][1] > closed:
        raise IntradayRiskObservationPreparationError("CURRENT_BUCKET_AFTER_SESSION_CLOSE")
    completed_seconds = (min(observed, closed) - opened).total_seconds()
    expected_bucket_count = int(completed_seconds // duration)
    if len(bars) != expected_bucket_count:
        raise IntradayRiskObservationPreparationError("CURRENT_COMPLETED_BUCKET_MISSING")
    latest_close_utc = bars[-1][1].astimezone(dt.timezone.utc)
    if available < latest_close_utc or provider < latest_close_utc:
        raise IntradayRiskObservationPreparationError(
            "CURRENT_SOURCE_BEFORE_LATEST_BUCKET_CLOSE"
        )
    prices = {}
    for field in ("reference_close", "open_price", "last_price", "bid_price", "ask_price"):
        _decimal(value.get(field), f"{field.upper()}_INVALID", positive=True)
        prices[field] = value[field]
    if Decimal(prices["bid_price"]) > Decimal(prices["ask_price"]):
        raise IntradayRiskObservationPreparationError("CROSSED_QUOTE_INVALID")
    return {
        "raw": copy.deepcopy(value),
        "subject_id": _token(value.get("subject_id"), "SUBJECT_ID_INVALID"),
        "opened": opened,
        "closed": closed,
        "observed": observed,
        "bars": bars,
        "prices": prices,
        "source_ref": _text(value.get("source_ref"), "SOURCE_REF_INVALID"),
        "source_sha256": _sha(
            value.get("source_sha256"), "SOURCE_SHA256_INVALID"
        ),
    }


def _prior(value: dict, definition: dict, current: dict, contract: dict, index: int) -> dict:
    context = f"PRIOR:{index}"
    fields = {
        "schema_version", "subject_id", "market", "session_id",
        "session_profile_id", "session_open_at", "session_close_at", "bars",
        "source_ref", "source_sha256", "available_at",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskObservationPreparationError(f"PRIOR_FIELDS_MISMATCH:{index}")
    if value.get("schema_version") != contract["prior_session_schema_version"]:
        raise IntradayRiskObservationPreparationError(f"PRIOR_SCHEMA_MISMATCH:{index}")
    if (
        value.get("subject_id") != current["subject_id"]
        or value.get("market") != definition["market"]
        or value.get("session_profile_id") != definition["session_profile_id"]
    ):
        raise IntradayRiskObservationPreparationError(f"PRIOR_COMPARABILITY_MISMATCH:{index}")
    profile = contract["session_profiles"][definition["session_profile_id"]]
    opened, closed = _validate_boundary(
        value.get("session_id"), value.get("session_open_at"),
        value.get("session_close_at"), profile, context
    )
    if closed > current["opened"]:
        raise IntradayRiskObservationPreparationError(f"PRIOR_SESSION_NOT_PRIOR:{index}")
    available = _utc(value.get("available_at"), f"PRIOR_AVAILABLE_AT_INVALID:{index}")
    if available > current["observed"]:
        raise IntradayRiskObservationPreparationError(f"PRIOR_SOURCE_FROM_FUTURE:{index}")
    duration = contract["elapsed_timeframes"][definition["elapsed_timeframe"]][
        "duration_seconds"
    ]
    target = opened + dt.timedelta(seconds=duration * len(current["bars"]))
    bars = _bars(value.get("bars"), opened, target, duration, context)
    if len(bars) != len(current["bars"]) or bars[-1][1] != target or target > closed:
        raise IntradayRiskObservationPreparationError(f"PRIOR_PREFIX_MISMATCH:{index}")
    if available < target.astimezone(dt.timezone.utc):
        raise IntradayRiskObservationPreparationError(
            f"PRIOR_SOURCE_BEFORE_PREFIX_CLOSE:{index}"
        )
    return {
        "session_id": value["session_id"], "bars": bars,
        "source_ref": _text(value.get("source_ref"), f"PRIOR_SOURCE_REF_INVALID:{index}"),
        "source_sha256": _sha(
            value.get("source_sha256"), f"PRIOR_SOURCE_SHA_INVALID:{index}"
        ),
        "available_at": value["available_at"],
    }


def _metric_semantics(contract: dict) -> dict:
    return copy.deepcopy(contract["metric_semantics"])


def build_preparation(
    definition_value: dict,
    current_value: dict,
    prior_values: list,
    contract: dict | None = None,
) -> dict:
    """Build one deterministic P9 observation-preparation receipt."""
    checked_contract = (
        load_contract() if contract is None else _validate_contract(contract)
    )
    definition = _definition(definition_value, checked_contract)
    current = _current(current_value, definition, checked_contract)
    if not isinstance(prior_values, list) or len(prior_values) != definition[
        "prior_comparable_session_count"
    ]:
        raise IntradayRiskObservationPreparationError("PRIOR_SESSION_COUNT_MISMATCH")
    priors = [
        _prior(value, definition, current, checked_contract, index)
        for index, value in enumerate(prior_values)
    ]
    session_ids = [row["session_id"] for row in priors]
    if len(session_ids) != len(set(session_ids)):
        raise IntradayRiskObservationPreparationError("PRIOR_SESSION_DUPLICATE")
    priors.sort(key=lambda row: row["session_id"])
    current_cumulative = sum((bar[2] for bar in current["bars"]), Decimal(0))
    prior_cumulative = [
        sum((bar[2] for bar in prior["bars"]), Decimal(0)) for prior in priors
    ]
    try:
        features = VOLUME_ARITHMETIC.volume_baseline_features(
            prior_cumulative, current_cumulative
        )
    except Exception as exc:
        raise IntradayRiskObservationPreparationError(
            f"VOLUME_ARITHMETIC_FAILED:{exc}"
        ) from exc
    method = definition["baseline_method"]
    expected = (
        features["prior_mean"]
        if method == "PRIOR_MEAN"
        else features["prior_median"]
    )
    status = "PREPARED" if expected > 0 else "NOT_AVAILABLE"
    observation = None
    if status == "PREPARED":
        observation = {
            "subject_id": current["subject_id"],
            "market": definition["market"],
            **current["prices"],
            "cumulative_volume": _decimal_text(current_cumulative),
            "expected_volume_to_time": _decimal_text(expected),
            "provider_timestamp": current["raw"]["provider_timestamp"],
            "received_at": current["raw"]["received_at"],
            "source_ref": current["source_ref"],
            "source_sha256": current["source_sha256"],
        }
    normalized = {
        "schema_version": checked_contract["output_schema_version"],
        "contract_version": checked_contract["contract_version"],
        "status": status,
        "definition_id": definition["definition_id"],
        "selection": {
            "market": definition["market"],
            "source_profile_id": definition["source_profile_id"],
            "session_profile_id": definition["session_profile_id"],
            "provider_id": definition["provider_id"],
            "elapsed_timeframe": definition["elapsed_timeframe"],
            "baseline_method": method,
            "prior_comparable_session_count": definition[
                "prior_comparable_session_count"
            ],
        },
        "elapsed_bucket_count": len(current["bars"]),
        "elapsed_bucket_end": current["bars"][-1][1].astimezone(
            dt.timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "baseline": {
            "status": "OBSERVED" if expected > 0 else "ZERO_BASELINE_UNKNOWN",
            "current_cumulative_volume": _decimal_text(current_cumulative),
            "prior_cumulative_volumes": [_decimal_text(value) for value in prior_cumulative],
            "expected_volume_to_time": _decimal_text(expected) if expected > 0 else None,
        },
        "prepared_observation": observation,
        "metric_semantics": _metric_semantics(checked_contract),
        "lineage": {
            "definition_sha256": payload_sha256(definition),
            "provider_contract_ref": definition["provider_contract_ref"],
            "provider_contract_sha256": definition["provider_contract_sha256"],
            "current_source_ref": current["source_ref"],
            "current_source_sha256": current["source_sha256"],
            "prior_sources": [
                {
                    "session_id": prior["session_id"],
                    "source_ref": prior["source_ref"],
                    "source_sha256": prior["source_sha256"],
                    "available_at": prior["available_at"],
                }
                for prior in priors
            ],
        },
        "authority": copy.deepcopy(checked_contract["authority"]),
    }
    return {**normalized, "packet_sha256": payload_sha256(normalized)}


def validate_receipt(value: dict, contract: dict | None = None) -> dict:
    """Validate receipt identity, closed authority, semantic names, and hash."""
    checked_contract = (
        load_contract() if contract is None else _validate_contract(contract)
    )
    fields = {
        "schema_version", "contract_version", "status", "definition_id",
        "selection", "elapsed_bucket_count", "elapsed_bucket_end", "baseline",
        "prepared_observation", "metric_semantics", "lineage", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise IntradayRiskObservationPreparationError("RECEIPT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != checked_contract["output_schema_version"]
        or value.get("contract_version") != checked_contract["contract_version"]
        or value.get("authority") != checked_contract["authority"]
        or value.get("metric_semantics") != checked_contract["metric_semantics"]
    ):
        raise IntradayRiskObservationPreparationError("RECEIPT_IDENTITY_MISMATCH")
    if value.get("status") not in {"PREPARED", "NOT_AVAILABLE"}:
        raise IntradayRiskObservationPreparationError("RECEIPT_STATUS_INVALID")
    if (value["status"] == "PREPARED") != (
        value.get("prepared_observation") is not None
    ):
        raise IntradayRiskObservationPreparationError("RECEIPT_OBSERVATION_STATUS_MISMATCH")
    _token(value.get("definition_id"), "RECEIPT_DEFINITION_ID_INVALID")
    _utc(value.get("elapsed_bucket_end"), "RECEIPT_BUCKET_END_INVALID")
    count = value.get("elapsed_bucket_count")
    if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
        raise IntradayRiskObservationPreparationError("RECEIPT_BUCKET_COUNT_INVALID")
    selection = value.get("selection")
    selection_fields = {
        "market", "source_profile_id", "session_profile_id", "provider_id",
        "elapsed_timeframe", "baseline_method", "prior_comparable_session_count",
    }
    if not isinstance(selection, dict) or set(selection) != selection_fields:
        raise IntradayRiskObservationPreparationError("RECEIPT_SELECTION_FIELDS_MISMATCH")
    source = checked_contract["source_profiles"].get(selection.get("source_profile_id"))
    session = checked_contract["session_profiles"].get(selection.get("session_profile_id"))
    prior_count = selection.get("prior_comparable_session_count")
    if (
        source is None
        or session is None
        or selection.get("market") != source["market"]
        or selection.get("market") != session["market"]
        or selection.get("session_profile_id")
        not in source["allowed_session_profile_ids"]
        or selection.get("elapsed_timeframe") not in checked_contract["elapsed_timeframes"]
        or selection.get("baseline_method") not in checked_contract["baseline_methods"]
        or isinstance(prior_count, bool)
        or not isinstance(prior_count, int)
        or prior_count <= 0
    ):
        raise IntradayRiskObservationPreparationError("RECEIPT_SELECTION_INVALID")
    _token(selection.get("provider_id"), "RECEIPT_PROVIDER_ID_INVALID")
    baseline = value.get("baseline")
    baseline_fields = {
        "status", "current_cumulative_volume", "prior_cumulative_volumes",
        "expected_volume_to_time",
    }
    if not isinstance(baseline, dict) or set(baseline) != baseline_fields:
        raise IntradayRiskObservationPreparationError("RECEIPT_BASELINE_FIELDS_MISMATCH")
    current_cumulative = _decimal(
        baseline.get("current_cumulative_volume"),
        "RECEIPT_CURRENT_CUMULATIVE_INVALID",
        positive=False,
    )
    raw_prior = baseline.get("prior_cumulative_volumes")
    if not isinstance(raw_prior, list) or len(raw_prior) != prior_count:
        raise IntradayRiskObservationPreparationError("RECEIPT_PRIOR_CUMULATIVE_INVALID")
    prior_cumulative = [
        _decimal(item, "RECEIPT_PRIOR_CUMULATIVE_INVALID", positive=False)
        for item in raw_prior
    ]
    try:
        features = VOLUME_ARITHMETIC.volume_baseline_features(
            prior_cumulative, current_cumulative
        )
    except Exception as exc:
        raise IntradayRiskObservationPreparationError(
            f"RECEIPT_VOLUME_ARITHMETIC_FAILED:{exc}"
        ) from exc
    expected = (
        features["prior_mean"]
        if selection["baseline_method"] == "PRIOR_MEAN"
        else features["prior_median"]
    )
    expected_text = _decimal_text(expected) if expected > 0 else None
    expected_status = "OBSERVED" if expected > 0 else "ZERO_BASELINE_UNKNOWN"
    if (
        baseline.get("expected_volume_to_time") != expected_text
        or baseline.get("status") != expected_status
        or (value["status"] == "PREPARED") != (expected > 0)
    ):
        raise IntradayRiskObservationPreparationError("RECEIPT_BASELINE_INVALID")
    observation = value.get("prepared_observation")
    if observation is not None:
        observation_fields = {
            "subject_id", "market", "reference_close", "open_price", "last_price",
            "bid_price", "ask_price", "cumulative_volume",
            "expected_volume_to_time", "provider_timestamp", "received_at",
            "source_ref", "source_sha256",
        }
        if not isinstance(observation, dict) or set(observation) != observation_fields:
            raise IntradayRiskObservationPreparationError(
                "RECEIPT_OBSERVATION_FIELDS_MISMATCH"
            )
        _token(observation.get("subject_id"), "RECEIPT_SUBJECT_ID_INVALID")
        if observation.get("market") != selection["market"]:
            raise IntradayRiskObservationPreparationError(
                "RECEIPT_OBSERVATION_MARKET_MISMATCH"
            )
        for field in ("reference_close", "open_price", "last_price", "bid_price", "ask_price"):
            _decimal(observation.get(field), f"RECEIPT_{field.upper()}_INVALID", positive=True)
        if Decimal(observation["bid_price"]) > Decimal(observation["ask_price"]):
            raise IntradayRiskObservationPreparationError("RECEIPT_CROSSED_QUOTE_INVALID")
        if (
            observation.get("cumulative_volume")
            != baseline["current_cumulative_volume"]
            or observation.get("expected_volume_to_time") != expected_text
        ):
            raise IntradayRiskObservationPreparationError(
                "RECEIPT_OBSERVATION_BASELINE_MISMATCH"
            )
        provider = _utc(
            observation.get("provider_timestamp"),
            "RECEIPT_PROVIDER_TIMESTAMP_INVALID",
        )
        received = _utc(observation.get("received_at"), "RECEIPT_RECEIVED_AT_INVALID")
        if provider > received:
            raise IntradayRiskObservationPreparationError(
                "RECEIPT_OBSERVATION_TIME_ORDER_INVALID"
            )
        _text(observation.get("source_ref"), "RECEIPT_SOURCE_REF_INVALID")
        _sha(observation.get("source_sha256"), "RECEIPT_SOURCE_SHA_INVALID")
    lineage = value.get("lineage")
    lineage_fields = {
        "definition_sha256", "provider_contract_ref", "provider_contract_sha256",
        "current_source_ref", "current_source_sha256", "prior_sources",
    }
    if not isinstance(lineage, dict) or set(lineage) != lineage_fields:
        raise IntradayRiskObservationPreparationError("RECEIPT_LINEAGE_FIELDS_MISMATCH")
    _sha(lineage.get("definition_sha256"), "RECEIPT_DEFINITION_SHA_INVALID")
    if (
        lineage.get("provider_contract_ref") != source["provider_contract_ref"]
        or lineage.get("provider_contract_sha256")
        != source["provider_contract_sha256"]
    ):
        raise IntradayRiskObservationPreparationError(
            "RECEIPT_PROVIDER_CONTRACT_MISMATCH"
        )
    _text(lineage.get("current_source_ref"), "RECEIPT_CURRENT_SOURCE_REF_INVALID")
    _sha(lineage.get("current_source_sha256"), "RECEIPT_CURRENT_SOURCE_SHA_INVALID")
    if observation is not None and (
        observation["source_ref"] != lineage["current_source_ref"]
        or observation["source_sha256"] != lineage["current_source_sha256"]
    ):
        raise IntradayRiskObservationPreparationError(
            "RECEIPT_CURRENT_SOURCE_LINEAGE_MISMATCH"
        )
    prior_sources = lineage.get("prior_sources")
    if not isinstance(prior_sources, list) or len(prior_sources) != prior_count:
        raise IntradayRiskObservationPreparationError("RECEIPT_PRIOR_SOURCES_INVALID")
    prior_ids = []
    for index, item in enumerate(prior_sources):
        if not isinstance(item, dict) or set(item) != {
            "session_id", "source_ref", "source_sha256", "available_at"
        }:
            raise IntradayRiskObservationPreparationError(
                f"RECEIPT_PRIOR_SOURCE_FIELDS_MISMATCH:{index}"
            )
        if not isinstance(item.get("session_id"), str) or DATE_RE.fullmatch(
            item["session_id"]
        ) is None:
            raise IntradayRiskObservationPreparationError(
                f"RECEIPT_PRIOR_SESSION_ID_INVALID:{index}"
            )
        prior_ids.append(item["session_id"])
        _text(item.get("source_ref"), f"RECEIPT_PRIOR_SOURCE_REF_INVALID:{index}")
        _sha(item.get("source_sha256"), f"RECEIPT_PRIOR_SOURCE_SHA_INVALID:{index}")
        _utc(item.get("available_at"), f"RECEIPT_PRIOR_AVAILABLE_AT_INVALID:{index}")
    if prior_ids != sorted(set(prior_ids)):
        raise IntradayRiskObservationPreparationError("RECEIPT_PRIOR_ORDER_INVALID")
    digest = _sha(value.get("packet_sha256"), "RECEIPT_SHA_INVALID")
    normalized = copy.deepcopy(value)
    normalized.pop("packet_sha256")
    if payload_sha256(normalized) != digest:
        raise IntradayRiskObservationPreparationError("RECEIPT_SHA_MISMATCH")
    return copy.deepcopy(value)
