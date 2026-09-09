#!/usr/bin/env python3
"""Signed Crypto five-axis normalization for the PAPER descriptive reference.

This module is the one missing piece of the adopted Crypto descriptive rule:
turning already-retained Crypto measurements into signed axis rows.  It is a
pure function.  It reads no file, opens no socket, calls no provider, writes no
evidence and holds no CLI.

What it deliberately does NOT do:

* It does not classify.  ``normalize_crypto_measurements`` returns axis rows in
  the canonical ``AXES`` order so that producer integration can pass them to the
  single existing classifier in ``regime.paper_regime_reference``.  There is no
  second aggregation or confidence engine here.
* It does not assess absolute stress.  RISK_VOL is a day-over-day *direction*
  between two finalized ``btc_risk/v1`` results; ``btc_stress_features`` remains
  ``UNDEFINED_UNCALIBRATED`` and ``absolute_stress_status`` is ``NOT_ASSESSED``.
* It does not qualify sources.  The supplied payload hashes prove only that the
  transform dictionaries handed in are byte-exact under the existing
  ``canonical_json``/``payload_sha256`` convention.  Original raw-file hash
  validation and transform rederivation stay mandatory at the producer
  integration boundary; ``lineage.source_sha256`` is passed through unchanged so
  the producer can bind it.
* It does not promote authority.  No runtime, capital, order or trading state is
  made available, and no TTL or new risk threshold is introduced.

Missing, incomplete, future, tampered or non-comparable evidence raises
``CryptoPaperDescriptiveNormalizationError``.  The caller maps that to UNKNOWN;
no axis is ever filled from an older value.
"""

from __future__ import annotations

import datetime as dt
from decimal import Decimal
import re


SCHEMA_VERSION = "crypto_paper_descriptive_normalization/v1"
AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
CURRENT_REFERENCE_MODE = "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY"

DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
ONE_DAY = dt.timedelta(days=1)

# TREND reuses the source-native 200DMA category verbatim.  There is no
# recomputation of the moving average and no fourth category.
TREND_DIRECTION = {
    "ABOVE_200DMA": "POSITIVE",
    "AT_200DMA": "NEUTRAL",
    "BELOW_200DMA": "NEGATIVE",
}

# Explicitly provisional Crypto PAPER policy choice, adopted for descriptive
# display only.  It is NOT inherited empirical suitability from another market.
BREADTH_POSITIVE_MIN = Decimal("0.55")
BREADTH_NEGATIVE_MAX = Decimal("0.45")

# BROAD_ALT_LEADERSHIP is read as a provisional descriptive participation
# signal.  Every other *defined* source category is NEUTRAL on purpose: BTC and
# ETH concentration are not scored as negative participation here.  An unknown
# category is unavailable and fails closed rather than defaulting to NEUTRAL.
LEADERSHIP_DIRECTION = {
    "BROAD_ALT_LEADERSHIP": "POSITIVE",
    "BTC_LEADERSHIP": "NEUTRAL",
    "ETH_LEADERSHIP": "NEUTRAL",
    "MIXED_WINDOW_LEADERSHIP": "NEUTRAL",
    "NARROW_ALT_LEADERSHIP": "NEUTRAL",
}

RISK_TRANSFORM_VERSION = "btc_risk/v1"
# Scalar identity every accepted risk transform must carry verbatim.
RISK_IDENTITY = {
    "schema_version": 1,
    "transform_version": RISK_TRANSFORM_VERSION,
    "market": "CRYPTO",
    "asset": "BTC",
    "quote_currency": "USD",
    "market_timezone": "UTC",
    "measurement": "btc_risk_features",
    "status": "AVAILABLE_UNCALIBRATED",
}
# Flags the upstream transform publishes as refusals.  Any of them turning true
# would mean the payload is no longer the uncalibrated descriptive input this
# helper is allowed to read.
RISK_REFUSAL_FLAGS = (
    "stress_threshold_authorized",
    "stress_classification_authorized",
    "regime_score_authorized",
    "production_wiring_authorized",
    "trading_action_authorized",
)
# Parameters that must match between the two days for the comparison to mean
# anything.  A changed lookback or estimator makes the pair incomparable.
VOLATILITY_IDENTITY = ("lookback_returns", "return_semantics", "estimator", "annualization_days")
DRAWDOWN_IDENTITY = ("lookback_closes", "semantics")
LINEAGE_IDENTITY = (
    "pit_status",
    "source_name",
    "capture_version",
    "source_transform_version",
    "missing_data_policy",
)

ZERO = Decimal("0")
NEGATIVE_ONE = Decimal("-1")

MANDATORY_CAVEATS = {
    "absolute_stress_status": "NOT_ASSESSED",
    "stress_calibration_status": "UNDEFINED_UNCALIBRATED",
    "breadth_threshold_status": "PROVISIONAL_CRYPTO_PAPER_POLICY",
    "leadership_interpretation_status": "PROVISIONAL_DESCRIPTIVE_PARTICIPATION_INTERPRETATION",
    "liquidity_input_semantics": "NAMED_STABLECOIN_ISSUANCE_PROXY_NOT_EXCHANGE_BUYING_POWER",
    "risk_direction_semantics": "RELATIVE_DAY_OVER_DAY_DIRECTION_ONLY_NOT_ABSOLUTE_STRESS_LEVEL",
    "point_in_time_status": "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY",
    "confidence_semantics": "CONFIDENCE_MATCHING_AXIS_FRACTION_NOT_PROBABILITY",
    "source_qualification_status": (
        "PAYLOAD_INTEGRITY_ONLY_RAW_SOURCE_QUALIFICATION_REQUIRED_AT_PRODUCER_INTEGRATION"
    ),
}

AUTHORITY = {
    "read_only_reference": True,
    "final_regime_authorized": False,
    "runtime_adoption_authorized": False,
    "capital_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class CryptoPaperDescriptiveNormalizationError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise CryptoPaperDescriptiveNormalizationError(f"{code}:{detail}" if detail else code)


def _common():
    """The shared reference helpers, imported at call time.

    Producer wiring is a later scope, so a module-level import would create an
    avoidable cycle between the reference builder and this normalizer.  The
    import is local for that reason only; the imported file is never modified.
    """
    from regime import paper_regime_reference as common

    return common


def _scalar(value: object, code: str, detail: str = "") -> Decimal:
    """One finite Decimal from a JSON scalar.  Booleans are not numbers."""
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        fail(code, detail)
    common = _common()
    try:
        return common.decimal(value, code)
    except common.PaperRegimeReferenceError as exc:
        raise CryptoPaperDescriptiveNormalizationError(
            f"{code}:{detail}" if detail else code
        ) from exc


def _text(value: object, code: str, detail: str = "") -> str:
    if not isinstance(value, str) or not value:
        fail(code, detail)
    return value


def _block(parent: object, key: str, code: str, detail: str = "") -> dict:
    value = parent.get(key) if isinstance(parent, dict) else None
    if not isinstance(value, dict):
        fail(code, detail or key)
    return value


def _date(value: object, code: str, detail: str = "") -> dt.date:
    if not isinstance(value, str) or DATE.fullmatch(value) is None:
        fail(code, detail)
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoPaperDescriptiveNormalizationError(
            f"{code}:{detail}" if detail else code
        ) from exc


def _timestamp(value: object, code: str, detail: str = "") -> dt.datetime:
    if not isinstance(value, str) or UTC_TIMESTAMP.fullmatch(value) is None:
        fail(code, detail)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise CryptoPaperDescriptiveNormalizationError(
            f"{code}:{detail}" if detail else code
        ) from exc
    return parsed.replace(tzinfo=dt.timezone.utc)


def _category(value: object, table: dict, code: str) -> str:
    """Resolve one source-native category against its mapping table.

    Membership is only tested once the value is known to be a nonempty string,
    so an unhashable list or dict fails closed with the typed module exception
    instead of escaping as ``TypeError``.  The caller maps that to UNKNOWN.
    """
    if not isinstance(value, str) or not value or value not in table:
        fail(code, repr(value))
    return table[value]


def _identity(block: dict, keys: tuple[str, ...], code: str, side: str) -> dict:
    """The comparability parameters, each present and a plain JSON scalar."""
    resolved = {}
    for key in keys:
        if key not in block:
            fail(code, f"{side}.{key}")
        value = block[key]
        if isinstance(value, bool) or not isinstance(value, (str, int, float)):
            fail(code, f"{side}.{key}")
        resolved[key] = value
    return resolved


def _risk_transform(
    payload: object,
    expected_sha256: object,
    side: str,
    decision_at: dt.datetime,
    decision_day: dt.date,
) -> dict:
    """Validate one finalized ``btc_risk/v1`` payload and pull its comparables.

    The hash check proves the dictionary handed in is byte-exact under the
    existing canonical convention.  It does not, and cannot, prove the original
    raw capture was qualified; that stays a producer-integration obligation.
    """
    if not isinstance(payload, dict):
        fail("RISK_TRANSFORM_MISSING", side)
    if not isinstance(expected_sha256, str) or SHA256.fullmatch(expected_sha256) is None:
        fail("RISK_PAYLOAD_SHA_FORMAT_INVALID", side)

    common = _common()
    try:
        actual_sha256 = common.payload_sha256(payload)
    except common.PaperRegimeReferenceError as exc:
        raise CryptoPaperDescriptiveNormalizationError(
            f"RISK_PAYLOAD_NOT_CANONICAL:{side}"
        ) from exc
    if actual_sha256 != expected_sha256:
        fail("RISK_PAYLOAD_SHA_MISMATCH", side)

    for key, expected in RISK_IDENTITY.items():
        value = payload.get(key)
        if isinstance(value, bool) or value != expected:
            fail("RISK_TRANSFORM_IDENTITY_INVALID", f"{side}.{key}")
    for key in RISK_REFUSAL_FLAGS:
        if type(payload.get(key)) is not bool or payload[key] is not False:
            fail("RISK_TRANSFORM_AUTHORITY_INVALID", f"{side}.{key}")

    point = _block(payload, "risk_point", "RISK_POINT_INVALID", side)
    as_of_date = _date(point.get("as_of_date"), "RISK_DATE_INVALID", f"{side}.as_of_date")
    finalized = _date(
        payload.get("latest_finalized_day"), "RISK_DATE_INVALID", f"{side}.latest_finalized_day"
    )
    if finalized != as_of_date:
        fail("RISK_FINALIZED_DAY_MISMATCH", side)

    # The in-progress candle must still be excluded: an included partial day
    # would make the finalized comparison silently intraday.
    candle = _block(payload, "current_candle", "RISK_CANDLE_INVALID", side)
    if candle.get("excluded") is not True:
        fail("RISK_CANDLE_NOT_EXCLUDED", side)
    candle_date = _date(candle.get("date"), "RISK_DATE_INVALID", f"{side}.current_candle.date")
    if candle_date <= as_of_date:
        fail("RISK_CANDLE_DATE_INVALID", side)
    # The excluded candle is the day still in progress at capture time, so it
    # cannot sit beyond the day the decision is taken on.
    if candle_date > decision_day:
        fail("RISK_CANDLE_AFTER_DECISION_DATE", side)

    volatility = _block(point, "realized_volatility", "RISK_VOLATILITY_INVALID", side)
    volatility_identity = _identity(
        volatility, VOLATILITY_IDENTITY, "RISK_VOLATILITY_INVALID", side
    )
    if _date(
        volatility.get("window_end"), "RISK_DATE_INVALID", f"{side}.volatility.window_end"
    ) != as_of_date:
        fail("RISK_VOLATILITY_WINDOW_INVALID", side)
    if _date(
        volatility.get("window_start"), "RISK_DATE_INVALID", f"{side}.volatility.window_start"
    ) > as_of_date:
        fail("RISK_VOLATILITY_WINDOW_INVALID", side)
    annualized = _scalar(
        volatility.get("annualized_fraction"), "RISK_VOLATILITY_INVALID", f"{side}.annualized_fraction"
    )
    if annualized < ZERO:
        fail("RISK_VOLATILITY_NEGATIVE", side)

    drawdown = _block(point, "drawdown", "RISK_DRAWDOWN_INVALID", side)
    drawdown_identity = _identity(drawdown, DRAWDOWN_IDENTITY, "RISK_DRAWDOWN_INVALID", side)
    if _date(
        drawdown.get("window_end"), "RISK_DATE_INVALID", f"{side}.drawdown.window_end"
    ) != as_of_date:
        fail("RISK_DRAWDOWN_WINDOW_INVALID", side)
    if _date(
        drawdown.get("window_start"), "RISK_DATE_INVALID", f"{side}.drawdown.window_start"
    ) > as_of_date:
        fail("RISK_DRAWDOWN_WINDOW_INVALID", side)
    current_drawdown = _scalar(
        drawdown.get("current_fraction"), "RISK_DRAWDOWN_INVALID", f"{side}.current_fraction"
    )
    maximum_drawdown = _scalar(
        drawdown.get("maximum_fraction"), "RISK_DRAWDOWN_INVALID", f"{side}.maximum_fraction"
    )
    for name, value in (("current", current_drawdown), ("maximum", maximum_drawdown)):
        if not NEGATIVE_ONE <= value <= ZERO:
            fail("RISK_DRAWDOWN_RANGE_INVALID", f"{side}.{name}")
    if maximum_drawdown > current_drawdown:
        fail("RISK_DRAWDOWN_INCOHERENT", side)

    # The upstream stress block must still be uncalibrated.  This helper reports
    # direction only and never reads a stress classification.
    stress = _block(point, "stress_features", "RISK_STRESS_INVALID", side)
    if (
        stress.get("calibration_status") != "UNDEFINED_UNCALIBRATED"
        or stress.get("thresholds_applied") is not False
        or stress.get("classification") != "UNDEFINED"
    ):
        fail("RISK_STRESS_NOT_UNCALIBRATED", side)

    lineage = _block(payload, "lineage", "RISK_LINEAGE_INVALID", side)
    lineage_identity = _identity(lineage, LINEAGE_IDENTITY, "RISK_LINEAGE_INVALID", side)
    source_sha256 = _text(lineage.get("source_sha256"), "RISK_LINEAGE_INVALID", f"{side}.source_sha256")
    if SHA256.fullmatch(source_sha256) is None:
        fail("RISK_LINEAGE_INVALID", f"{side}.source_sha256")
    vintage_date = _date(lineage.get("vintage_date"), "RISK_DATE_INVALID", f"{side}.vintage_date")
    if vintage_date < as_of_date:
        fail("RISK_VINTAGE_BEFORE_FINALIZED_DAY", side)
    # A capture vintage dated past the decision day is not evidence that could
    # have existed when the decision was taken.
    if vintage_date > decision_day:
        fail("RISK_VINTAGE_AFTER_DECISION_DATE", side)
    available_at = _timestamp(lineage.get("available_at"), "RISK_TIME_INVALID", f"{side}.available_at")
    # Availability earlier than the declared capture vintage is incoherent
    # metadata: the payload cannot be available before it was captured.
    if available_at < dt.datetime.combine(vintage_date, dt.time.min, tzinfo=dt.timezone.utc):
        fail("RISK_AVAILABLE_BEFORE_VINTAGE", side)
    if available_at > decision_at:
        fail("RISK_INPUT_NOT_YET_AVAILABLE", side)

    return {
        "as_of_date": as_of_date,
        "payload_sha256": actual_sha256,
        "source_sha256": source_sha256,
        "source_name": lineage_identity["source_name"],
        "vintage_date": vintage_date,
        "available_at": available_at,
        "annualized_volatility": annualized,
        "current_drawdown": current_drawdown,
        "maximum_drawdown": maximum_drawdown,
        "volatility_identity": volatility_identity,
        "drawdown_identity": drawdown_identity,
        "lineage_identity": lineage_identity,
    }


def _risk_direction(current: dict, prior: dict) -> tuple[str, str, str]:
    """Day-over-day direction across the volatility and drawdown pair.

    Volatility improves when it falls; drawdown improves when it is less
    negative.  Both no worse with at least one strict improvement is POSITIVE,
    both no better with at least one strict deterioration is NEGATIVE, and any
    mixed or fully unchanged pair stays NEUTRAL.
    """
    if current["volatility_identity"] != prior["volatility_identity"]:
        fail("RISK_VOLATILITY_NOT_COMPARABLE")
    if current["drawdown_identity"] != prior["drawdown_identity"]:
        fail("RISK_DRAWDOWN_NOT_COMPARABLE")
    if current["lineage_identity"] != prior["lineage_identity"]:
        fail("RISK_SOURCE_IDENTITY_NOT_COMPARABLE")
    if current["as_of_date"] != prior["as_of_date"] + ONE_DAY:
        fail("RISK_DATES_NOT_CONSECUTIVE")
    if prior["available_at"] > current["available_at"]:
        fail("RISK_AVAILABILITY_ORDER_INVALID")

    volatility_change = (
        "IMPROVED" if current["annualized_volatility"] < prior["annualized_volatility"]
        else "DETERIORATED" if current["annualized_volatility"] > prior["annualized_volatility"]
        else "UNCHANGED"
    )
    drawdown_change = (
        "IMPROVED" if current["current_drawdown"] > prior["current_drawdown"]
        else "DETERIORATED" if current["current_drawdown"] < prior["current_drawdown"]
        else "UNCHANGED"
    )
    changes = (volatility_change, drawdown_change)
    if "DETERIORATED" not in changes and "IMPROVED" in changes:
        direction = "POSITIVE"
    elif "IMPROVED" not in changes and "DETERIORATED" in changes:
        direction = "NEGATIVE"
    else:
        direction = "NEUTRAL"
    return direction, volatility_change, drawdown_change


def normalize_crypto_measurements(
    *,
    trend_category: str,
    breadth_advance_fraction: object,
    stablecoin_daily_net_issuance: object,
    stablecoin_weekly_net_issuance: object,
    leadership_code: str,
    current_risk_transform: dict,
    current_risk_payload_sha256: str,
    prior_risk_transform: dict,
    prior_risk_payload_sha256: str,
    decision_at: str,
    decision_date: str,
    price_as_of_date: str,
    current_reference_mode: str,
) -> dict:
    """Signed Crypto axes plus the mandatory descriptive caveats.

    All five measurements are required; there is no partial result and no
    carried-forward value.  ``decision_at`` is an explicit ``...Z`` UTC instant
    and bounds input availability; ``decision_date`` additionally bounds each
    payload's capture vintage and excluded in-progress candle, so capture
    metadata dated after the decision fails closed.  ``price_as_of_date`` is the
    finalized price date the decision is taken against and must equal the
    current risk transform's ``as_of_date``.

    Returns a dict with ``axes`` in canonical ``AXES`` order for the existing
    classifier, ``risk_binding`` metadata (both payload hashes, both raw
    ``source_sha256`` values and both risk dates) for the producer to bind, and
    ``caveats`` that the consumer must keep attached to any label it derives.
    """
    if current_reference_mode != CURRENT_REFERENCE_MODE:
        fail("CURRENT_REFERENCE_MODE_INVALID")
    decision_instant = _timestamp(decision_at, "DECISION_TIME_INVALID", "decision_at")
    decision_day = _date(decision_date, "DECISION_DATE_INVALID", "decision_date")
    if decision_instant.date() != decision_day:
        fail("DECISION_DATE_NOT_COHERENT")
    price_day = _date(price_as_of_date, "PRICE_DATE_INVALID", "price_as_of_date")
    # The in-progress candle is excluded upstream, so the finalized price date
    # is always strictly before the decision date.
    if price_day >= decision_day:
        fail("PRICE_DATE_NOT_BEFORE_DECISION_DATE")

    current = _risk_transform(
        current_risk_transform, current_risk_payload_sha256, "current", decision_instant, decision_day
    )
    prior = _risk_transform(
        prior_risk_transform, prior_risk_payload_sha256, "prior", decision_instant, decision_day
    )
    if current["as_of_date"] != price_day:
        fail("RISK_PRICE_DATE_MISMATCH")
    risk_direction, volatility_change, drawdown_change = _risk_direction(current, prior)

    trend_direction = _category(trend_category, TREND_DIRECTION, "TREND_CATEGORY_UNAVAILABLE")

    breadth = _scalar(breadth_advance_fraction, "BREADTH_INVALID", "advance_fraction")
    if not ZERO <= breadth <= Decimal("1"):
        fail("BREADTH_RANGE_INVALID")

    daily = _scalar(stablecoin_daily_net_issuance, "LIQUIDITY_INVALID", "daily_net_issuance")
    weekly = _scalar(stablecoin_weekly_net_issuance, "LIQUIDITY_INVALID", "weekly_net_issuance")

    leadership_direction = _category(
        leadership_code, LEADERSHIP_DIRECTION, "LEADERSHIP_CATEGORY_UNAVAILABLE"
    )

    common = _common()
    breadth_direction = common.ratio_direction(breadth, BREADTH_POSITIVE_MIN, BREADTH_NEGATIVE_MAX)
    liquidity_direction = common.sign_pair([daily, weekly])

    trend_summary = {
        "POSITIVE": "비트코인 가격이 200일 이동평균 위에 있습니다.",
        "NEUTRAL": "비트코인 가격이 200일 이동평균과 같은 수준입니다.",
        "NEGATIVE": "비트코인 가격이 200일 이동평균 아래에 있습니다.",
    }[trend_direction]
    liquidity_summary = {
        "POSITIVE": "스테이블코인 일간·주간 순발행이 모두 플러스입니다(거래소 매수 여력이 아니라 발행량 대리지표).",
        "NEGATIVE": "스테이블코인 일간·주간 순발행이 모두 마이너스입니다(거래소 매수 여력이 아니라 발행량 대리지표).",
        "NEUTRAL": "스테이블코인 일간·주간 순발행 방향이 엇갈리거나 변화가 없습니다(거래소 매수 여력이 아니라 발행량 대리지표).",
    }[liquidity_direction]
    risk_summary = {
        "POSITIVE": "직전 확정일 대비 변동성과 낙폭이 나빠지지 않았습니다.",
        "NEGATIVE": "직전 확정일 대비 변동성과 낙폭이 나아지지 않았습니다.",
        "NEUTRAL": "직전 확정일 대비 변동성과 낙폭 방향이 엇갈리거나 그대로입니다.",
    }[risk_direction] + " 절대 위험 수준은 판정하지 않았습니다."

    axes = [
        common.axis("TREND", trend_direction, {"category": trend_category}, trend_summary),
        common.axis(
            "BREADTH",
            breadth_direction,
            {"advance_fraction": str(breadth)},
            f"상승 비중은 {breadth * 100:.1f}%입니다(잠정 코인 PAPER 기준).",
        ),
        common.axis(
            "RISK_VOL",
            risk_direction,
            {
                "current_as_of_date": current["as_of_date"].isoformat(),
                "prior_as_of_date": prior["as_of_date"].isoformat(),
                "volatility_change": volatility_change,
                "drawdown_change": drawdown_change,
            },
            risk_summary,
        ),
        common.axis(
            "LIQUIDITY",
            liquidity_direction,
            {"daily_net_issuance": str(daily), "weekly_net_issuance": str(weekly)},
            liquidity_summary,
        ),
        common.axis(
            "LEADERSHIP",
            leadership_direction,
            {"leadership_code": leadership_code},
            f"주도 코인 구도는 {leadership_code}입니다(잠정 서술적 해석).",
        ),
    ]

    def binding(record: dict) -> dict:
        return {
            "as_of_date": record["as_of_date"].isoformat(),
            "payload_sha256": record["payload_sha256"],
            "source_sha256": record["source_sha256"],
            "source_name": record["source_name"],
            "vintage_date": record["vintage_date"].isoformat(),
            "available_at": record["available_at"].strftime("%Y-%m-%dT%H:%M:%SZ"),
            "realized_vol_annualized_fraction": str(record["annualized_volatility"]),
            "current_drawdown_fraction": str(record["current_drawdown"]),
            "maximum_drawdown_fraction": str(record["maximum_drawdown"]),
        }

    return {
        "schema_version": SCHEMA_VERSION,
        "market": "CRYPTO",
        "decision_at": decision_at,
        "decision_date": decision_date,
        "price_as_of_date": price_as_of_date,
        "current_reference_mode": CURRENT_REFERENCE_MODE,
        "classification_status": "NOT_CLASSIFIED_BY_THIS_HELPER",
        "runtime_regime": "UNKNOWN",
        "axes": axes,
        "risk_binding": {
            "transform_version": RISK_TRANSFORM_VERSION,
            "current": binding(current),
            "prior": binding(prior),
            "volatility_change": volatility_change,
            "drawdown_change": drawdown_change,
            "raw_source_qualification": "REQUIRED_AT_PRODUCER_INTEGRATION",
        },
        "caveats": dict(MANDATORY_CAVEATS),
        "authority": dict(AUTHORITY),
    }
