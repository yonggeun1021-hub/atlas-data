#!/usr/bin/env python3
"""CRYPTO_PAPER_RUNTIME_V1: fail-closed Crypto PAPER runtime decision.

User ratification ``CRYPTO-PAPER-RUNTIME-V1-20260914`` (record bound by hash in
``config/crypto_paper_runtime_v1.json``) opens a Crypto *INTERNAL_VIRTUAL_PAPER*
market state only.  This module is the pure calculation:

* signed normalization reuses the provisional values of
  ``regime.crypto_paper_descriptive_normalization`` verbatim;
* RISK_VOL is the ratified absolute rule on ``btc_risk/v1`` fields, evaluated
  top to bottom (STRESS, NEGATIVE, POSITIVE, otherwise NEUTRAL);
* LEADERSHIP uses the ratified pilot 7d window until the primary 30d window is
  observed in the runtime chain, then 30d permanently; any
  ``TAXONOMY_COVERAGE_UNKNOWN`` day makes the axis missing;
* one finalized packet per UTC day at 07:00Z; any missing, invalid, stale,
  mixed-generation or lookahead axis is immediate UNKNOWN with no carry;
* classification and hysteresis come only from the unmodified
  ``regime.decision_authority.replay_common_v1``;
* a runtime regime is emitted only while the crypto-only
  ``PROVISIONAL_FORWARD_ACCEPTANCE`` passes, re-evaluated on every call, so any
  validator failure reverts to UNKNOWN.

No file is written, no provider is called, and no strategy, stage, buy,
action, capital, order, production, trading or REAL authority is opened.
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

from regime import crypto_paper_descriptive_normalization as DESCRIPTIVE
from regime import decision_authority as COMMON


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "crypto_paper_runtime_v1.json"
POLICY_SHA256 = "6a4f643d66bde2b418f951b47aed2dbd75b89938a171d812f9c3925d1c0935f9"
SCHEMA_VERSION = "crypto_paper_runtime_decision/1"
POLICY_CONTRACT_VERSION = "crypto_paper_runtime/v1"
RATIFICATION_IDENTITY = "CRYPTO-PAPER-RUNTIME-V1-20260914"
RATIFICATION_RECORD_SHA256 = (
    "e2f9f69461088d52300258ab22f17d7f287bd7c2fd6efd1d49962b44f16fffe1"
)
ACCEPTANCE_LABEL = "PROVISIONAL_FORWARD_ACCEPTANCE"
ACCEPTED = "PROVISIONAL_FORWARD_ACCEPTED"
NOT_ACCEPTED = "NOT_ACCEPTED"
LIVE_NATURAL = "LIVE_NATURAL"
EVIDENCE_CLASSES = {LIVE_NATURAL, "SYNTHETIC_OFFLINE_FIXTURE"}
AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
PILOT = "pilot_7d"
PRIMARY = "primary_30d"
TAXONOMY_COVERAGE_UNKNOWN = "TAXONOMY_COVERAGE_UNKNOWN"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
ONE_DAY = dt.timedelta(days=1)

# Ratified numbers, verbatim from the user ratification record.  The config
# file must carry exactly these; the code never reads a number it cannot
# cross-check here.
RISK_STRESS_DD_LTE = Decimal("-0.25")
RISK_STRESS_VOL_GTE = Decimal("0.90")
RISK_STRESS_AND_DD_LTE = Decimal("-0.10")
RISK_NEGATIVE_DD_LTE = Decimal("-0.15")
RISK_NEGATIVE_VOL_GTE = Decimal("0.70")
RISK_POSITIVE_DD_GT = Decimal("-0.08")
RISK_POSITIVE_VOL_LT = Decimal("0.45")
DECISION_TIME = dt.time(7, 0, 0)
MINIMUM_CONSECUTIVE_COMPLETE_DAYS = 5
KRAKEN_REPLAY_START_DATE = "2019-01-01"
REQUIRED_RISK_VOL_RESULTS = ["STRESS", "NEGATIVE", "POSITIVE"]

AUTHORITY_CLOSED = {
    "paper_runtime_display_authorized": False,
    "strategy_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "capital_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "real_authorized": False,
}


class CryptoPaperRuntimeError(ValueError):
    pass


def require(condition: bool, code: str) -> None:
    if not condition:
        raise CryptoPaperRuntimeError(code)


def sha256(raw: bytes) -> str:
    require(isinstance(raw, bytes), "BYTES_REQUIRED")
    return hashlib.sha256(raw).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return COMMON.canonical_bytes(value)


def payload_sha256(value: object) -> str:
    return COMMON.payload_sha256(value)


def pretty_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _object(raw: bytes, code: str) -> dict:
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                           parse_constant=lambda _: require(False, "NONFINITE_JSON"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CryptoPaperRuntimeError(code) from exc
    require(isinstance(value, dict), code)
    return value


def instant(value: object, code: str) -> dt.datetime:
    require(isinstance(value, str) and UTC_SECOND.fullmatch(value) is not None, code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoPaperRuntimeError(code) from exc


def day(value: object, code: str) -> dt.date:
    require(isinstance(value, str) and ISO_DATE.fullmatch(value) is not None, code)
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoPaperRuntimeError(code) from exc


def utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def number(value: object, code: str) -> Decimal:
    require(isinstance(value, str) and bool(value.strip()), code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise CryptoPaperRuntimeError(code) from exc
    require(parsed.is_finite(), code)
    return parsed


# ---------------------------------------------------------------------------
# Ratified identity
# ---------------------------------------------------------------------------

def load_policy(root: Path = ROOT) -> dict:
    """Load the hash-pinned crypto identity and prove its external bindings."""
    path = root / "config" / "crypto_paper_runtime_v1.json"
    raw = path.read_bytes()
    require(sha256(raw) == POLICY_SHA256, "POLICY_HASH_MISMATCH")
    policy = _object(raw, "POLICY_JSON_INVALID")
    require(policy.get("contract_version") == POLICY_CONTRACT_VERSION
            and policy.get("policy_status") == "RATIFIED", "POLICY_SCOPE_INVALID")
    decision = policy["decision"]
    require(decision.get("identity") == RATIFICATION_IDENTITY
            and decision.get("record_sha256") == RATIFICATION_RECORD_SHA256,
            "RATIFICATION_BINDING_INVALID")
    record_raw = (root / decision["record_path"]).read_bytes()
    require(sha256(record_raw) == RATIFICATION_RECORD_SHA256, "RATIFICATION_RECORD_HASH_MISMATCH")
    record = _object(record_raw, "RATIFICATION_RECORD_INVALID")
    require(record.get("ratification_id") == RATIFICATION_IDENTITY
            and record.get("basis", {}).get("sha256") == decision["basis_sha256"]
            and all(value is False for value in record.get("authority", {}).values()),
            "RATIFICATION_RECORD_SCOPE_INVALID")
    require(policy["ratified_markets"] == ["CRYPTO"], "POLICY_MARKET_INVALID")
    require(policy["required_axes"] == AXES == COMMON.load_common_v1_policy()["required_axes"],
            "POLICY_AXES_INVALID")
    for name, binding in policy["bindings"].items():
        if name.endswith("_path"):
            expected = policy["bindings"][name[:-5] + "_sha256"]
            require(sha256((root / binding).read_bytes()) == expected, "POLICY_BINDING_DRIFT")
    norm = policy["normalization"]
    require(norm["TREND"] == DESCRIPTIVE.TREND_DIRECTION, "TREND_NORMALIZATION_NOT_VERBATIM")
    require(Decimal(norm["BREADTH"]["positive_min"]) == DESCRIPTIVE.BREADTH_POSITIVE_MIN
            and Decimal(norm["BREADTH"]["negative_max"]) == DESCRIPTIVE.BREADTH_NEGATIVE_MAX,
            "BREADTH_NORMALIZATION_NOT_VERBATIM")
    leadership = {k: v for k, v in norm["LEADERSHIP"].items() if k != "unknown_code"}
    require(leadership == DESCRIPTIVE.LEADERSHIP_DIRECTION
            and norm["LEADERSHIP"]["unknown_code"] == "AXIS_MISSING",
            "LEADERSHIP_NORMALIZATION_NOT_VERBATIM")
    rules = policy["RISK_VOL"]["rules"]
    require(
        [row["result"] for row in rules] == ["STRESS", "NEGATIVE", "POSITIVE", "NEUTRAL"]
        and Decimal(rules[0]["dd_lte"]) == RISK_STRESS_DD_LTE
        and Decimal(rules[0]["or_vol_gte"]) == RISK_STRESS_VOL_GTE
        and Decimal(rules[0]["and_dd_lte"]) == RISK_STRESS_AND_DD_LTE
        and Decimal(rules[1]["dd_lte"]) == RISK_NEGATIVE_DD_LTE
        and Decimal(rules[1]["or_vol_gte"]) == RISK_NEGATIVE_VOL_GTE
        and Decimal(rules[2]["dd_gt"]) == RISK_POSITIVE_DD_GT
        and Decimal(rules[2]["and_vol_lt"]) == RISK_POSITIVE_VOL_LT
        and policy["RISK_VOL"]["evaluation_order"] == "TOP_TO_BOTTOM_FIRST_MATCH",
        "RISK_VOL_RULE_NOT_RATIFIED",
    )
    packet = policy["finalized_packet"]
    require(packet["decision_time_utc"] == DECISION_TIME.isoformat()
            and packet["missing_invalid_or_stale_axis"] == "IMMEDIATE_UNKNOWN_NO_CARRY",
            "FINALIZED_PACKET_RULE_INVALID")
    acceptance = policy["acceptance"]
    replaced = acceptance["replaced_condition_6"]
    require(acceptance["label"] == ACCEPTANCE_LABEL
            and acceptance["minimum_consecutive_complete_utc_days"] == MINIMUM_CONSECUTIVE_COMPLETE_DAYS
            and acceptance["auto_revert"] == "UNKNOWN_ON_ANY_VALIDATOR_FAILURE"
            and replaced["replay_start_date"] == KRAKEN_REPLAY_START_DATE
            and replaced["required_risk_vol_results"] == REQUIRED_RISK_VOL_RESULTS
            and replaced["range_selection_forbidden"] is True,
            "ACCEPTANCE_RULE_INVALID")
    authority = policy["authority"]
    require(authority.get("paper_runtime_display_authorized") is True
            and set(authority) == set(AUTHORITY_CLOSED)
            and all(v is False for k, v in authority.items() if k != "paper_runtime_display_authorized"),
            "POLICY_AUTHORITY_ESCALATION")
    return copy.deepcopy(policy)


# ---------------------------------------------------------------------------
# Signed normalization
# ---------------------------------------------------------------------------

def risk_vol_direction(vol: Decimal, dd: Decimal) -> str:
    """Ratified ordered RISK_VOL rule on btc_risk/v1 vol and drawdown."""
    require(isinstance(vol, Decimal) and isinstance(dd, Decimal)
            and vol.is_finite() and dd.is_finite(), "RISK_INPUT_INVALID")
    require(vol >= 0, "RISK_VOLATILITY_NEGATIVE")
    require(Decimal(-1) <= dd <= 0, "RISK_DRAWDOWN_RANGE_INVALID")
    if dd <= RISK_STRESS_DD_LTE or (vol >= RISK_STRESS_VOL_GTE and dd <= RISK_STRESS_AND_DD_LTE):
        return "STRESS"
    if dd <= RISK_NEGATIVE_DD_LTE or vol >= RISK_NEGATIVE_VOL_GTE:
        return "NEGATIVE"
    if dd > RISK_POSITIVE_DD_GT and vol < RISK_POSITIVE_VOL_LT:
        return "POSITIVE"
    return "NEUTRAL"


def trend_direction(category: object) -> str:
    require(isinstance(category, str) and category in DESCRIPTIVE.TREND_DIRECTION,
            "TREND_CATEGORY_UNAVAILABLE")
    return DESCRIPTIVE.TREND_DIRECTION[category]


def breadth_direction(fraction: Decimal) -> str:
    require(Decimal(0) <= fraction <= Decimal(1), "BREADTH_RANGE_INVALID")
    if fraction >= DESCRIPTIVE.BREADTH_POSITIVE_MIN:
        return "POSITIVE"
    if fraction <= DESCRIPTIVE.BREADTH_NEGATIVE_MAX:
        return "NEGATIVE"
    return "NEUTRAL"


def liquidity_direction(daily: Decimal, weekly: Decimal) -> str:
    if daily > 0 and weekly > 0:
        return "POSITIVE"
    if daily < 0 and weekly < 0:
        return "NEGATIVE"
    return "NEUTRAL"


def leadership_direction(code: object) -> str:
    require(isinstance(code, str) and code in DESCRIPTIVE.LEADERSHIP_DIRECTION,
            "LEADERSHIP_CODE_UNKNOWN")
    return DESCRIPTIVE.LEADERSHIP_DIRECTION[code]


def _recent_reference_module():
    path = ROOT / ".github" / "scripts" / "crypto_recent_reference.py"
    spec = importlib.util.spec_from_file_location("atlas_crypto_runtime_recent_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def leadership_code(window: dict) -> str:
    """Existing crypto_recent_reference state mapping on one official PIT window.

    The composite of a window with itself is aligned by construction, so the
    returned code is exactly that window's state; no threshold is added.
    """
    buckets = window.get("bucket_gross_returns")
    require(isinstance(buckets, dict) and set(buckets) == {"ALT", "BTC", "ETH"},
            "LEADERSHIP_BUCKET_UNKNOWN")
    gross = {key: number(value, "LEADERSHIP_BUCKET_UNKNOWN") for key, value in buckets.items()}
    alts = window.get("alt_asset_gross_returns")
    require(isinstance(alts, list) and bool(alts), "LEADERSHIP_ALT_UNIVERSE_EMPTY")
    recent = _recent_reference_module()
    pct = {key: (value - 1) * 100 for key, value in gross.items()}
    alt_median = (recent.median([number(v, "LEADERSHIP_ALT_INVALID") for v in alts]) - 1) * 100
    leaders = {"BTC": pct["BTC"], "ETH": pct["ETH"], "ALT_EQUAL_WEIGHT": pct["ALT"]}
    leading = sorted(leaders, key=lambda key: (-leaders[key], key))[0]
    row = {
        "leading_bucket_by_raw_return": leading,
        "alt_median_return_pct": str(alt_median),
        "btc_return_pct": str(pct["BTC"]),
        "eth_return_pct": str(pct["ETH"]),
    }
    return recent.leadership_reference({"7d": row, "30d": row})["composite_code"]


# ---------------------------------------------------------------------------
# One finalized packet per UTC day
# ---------------------------------------------------------------------------

def decision_at_for(decision_date: dt.date) -> dt.datetime:
    return dt.datetime.combine(decision_date, DECISION_TIME, tzinfo=dt.timezone.utc)


def current_decision_date(evaluation_at: dt.datetime) -> dt.date:
    candidate = evaluation_at.date()
    return candidate if evaluation_at >= decision_at_for(candidate) else candidate - ONE_DAY


def _available(record: dict, decision_date: dt.date, prefix: str) -> dt.datetime:
    available = instant(record.get("available_at"), prefix + "_AVAILABLE_AT_INVALID")
    require(available <= decision_at_for(decision_date), prefix + "_LOOKAHEAD")
    floor = dt.datetime.combine(decision_date, dt.time.min, tzinfo=dt.timezone.utc)
    require(available >= floor, prefix + "_STALE")
    return available


def _vintage(record: dict, decision_date: dt.date, prefix: str) -> None:
    vintage = day(record.get("vintage_date"), prefix + "_DATE_INVALID")
    require(vintage <= decision_date, prefix + "_LOOKAHEAD")
    require(vintage == decision_date, prefix + "_STALE")


def _error(record: object, prefix: str) -> dict:
    require(isinstance(record, dict), prefix + "_MISSING")
    if "error" in record:
        code = record["error"]
        code = code if isinstance(code, str) and re.fullmatch(r"[A-Z][A-Z0-9_]*", code) else "INVALID"
        raise CryptoPaperRuntimeError(f"{prefix}_SOURCE_{code}")
    return record


def _btc(record: object, decision_date: dt.date) -> dict:
    btc = _error(record, "BTC")
    _vintage(btc, decision_date, "BTC")
    _available(btc, decision_date, "BTC")
    finalized = day(btc.get("latest_finalized_day"), "BTC_DATE_INVALID")
    require(finalized < decision_date, "BTC_LOOKAHEAD")
    require(finalized == decision_date - ONE_DAY, "BTC_DATE_MISMATCH")
    for key in ("trend_source_sha256", "risk_source_sha256"):
        require(isinstance(btc.get(key), str) and SHA256.fullmatch(btc[key]) is not None,
                "BTC_LINEAGE_INVALID")
    require(btc["trend_source_sha256"] == btc["risk_source_sha256"]
            and btc.get("trend_latest_finalized_day") == btc.get("risk_latest_finalized_day")
            == btc["latest_finalized_day"], "BTC_MIXED_GENERATION")
    require(btc.get("risk_transform_version") == "btc_risk/v1", "RISK_TRANSFORM_VERSION_INVALID")
    return btc


def _stablecoin(record: object, decision_date: dt.date) -> dict:
    stable = _error(record, "LIQUIDITY")
    _vintage(stable, decision_date, "LIQUIDITY")
    _available(stable, decision_date, "LIQUIDITY")
    observed = day(stable.get("observation_date"), "LIQUIDITY_DATE_INVALID")
    require(observed <= decision_date, "LIQUIDITY_LOOKAHEAD")
    require(observed == decision_date, "LIQUIDITY_DATE_MISMATCH")
    require(stable.get("daily_status") == "AVAILABLE" and stable.get("weekly_status") == "AVAILABLE",
            "LIQUIDITY_INPUT_UNAVAILABLE")
    return stable


def _breadth(record: object, decision_date: dt.date) -> dict:
    breadth = _error(record, "BREADTH")
    _vintage(breadth, decision_date, "BREADTH")
    _available(breadth, decision_date, "BREADTH")
    as_of = day(breadth.get("as_of_date"), "BREADTH_DATE_INVALID")
    require(as_of < decision_date, "BREADTH_LOOKAHEAD")
    require(as_of == decision_date - ONE_DAY, "BREADTH_DATE_MISMATCH")
    if breadth.get("unknown_reason") == TAXONOMY_COVERAGE_UNKNOWN:
        raise CryptoPaperRuntimeError("BREADTH_TAXONOMY_COVERAGE_UNKNOWN")
    require(breadth.get("status") == "OBSERVED_UNCLASSIFIED", "BREADTH_SOURCE_UNKNOWN")
    return breadth


def _window_observed(window: object, decision_date: dt.date) -> bool:
    return (
        isinstance(window, dict)
        and window.get("status") == "OBSERVED_UNCLASSIFIED"
        and window.get("end_date") == (decision_date - ONE_DAY).isoformat()
    )


def select_leadership_window(record: object, decision_date: dt.date,
                             primary_observed_earlier: bool) -> tuple[str, dict]:
    """Ratified window rule. Returns (official window id, window) or raises."""
    leadership = _error(record, "LEADERSHIP")
    windows = leadership.get("windows")
    require(isinstance(windows, dict) and set(windows) == {PILOT, PRIMARY},
            "LEADERSHIP_WINDOWS_INVALID")
    primary_now = _window_observed(windows[PRIMARY], decision_date)
    official = PRIMARY if (primary_observed_earlier or primary_now) else PILOT
    window = windows[official]
    require(isinstance(window, dict), "LEADERSHIP_WINDOWS_INVALID")
    reasons = set(window.get("source_unknown_reasons") or [])
    reasons.update([window.get("unknown_reason"), window.get("sector_chain_unknown_reason")])
    if TAXONOMY_COVERAGE_UNKNOWN in reasons:
        raise CryptoPaperRuntimeError("LEADERSHIP_TAXONOMY_COVERAGE_UNKNOWN")
    require(window.get("status") == "OBSERVED_UNCLASSIFIED",
            f"LEADERSHIP_{official.upper()}_NOT_OBSERVED")
    end = day(window.get("end_date"), "LEADERSHIP_DATE_INVALID")
    require(end < decision_date, "LEADERSHIP_LOOKAHEAD")
    require(end == decision_date - ONE_DAY, "LEADERSHIP_DATE_MISMATCH")
    points = window.get("point_available_at")
    require(isinstance(points, list) and bool(points), "LEADERSHIP_LINEAGE_INVALID")
    for value in points:
        require(instant(value, "LEADERSHIP_AVAILABLE_AT_INVALID") <= decision_at_for(decision_date),
                "LEADERSHIP_LOOKAHEAD")
    return official, window


def evaluate_day(record: object, decision_date: dt.date, primary_observed_earlier: bool) -> dict:
    """Signed axes for one finalized packet; each failure is that axis missing."""
    axes, diagnostics, reasons = {}, {}, []
    record = record if isinstance(record, dict) else None
    if record is not None and record.get("decision_date") != decision_date.isoformat():
        record = None
        reasons.append("FINALIZED_PACKET_DATE_MISMATCH")
    if record is None and not reasons:
        reasons.append("FINALIZED_PACKET_MISSING")
    source = record or {}

    def attempt(axis: str, derive) -> None:
        try:
            direction, observed = derive()
            axes[axis] = {"status": "DEFINED", "direction": direction}
            diagnostics[axis] = observed
        except (CryptoPaperRuntimeError, KeyError, TypeError, AttributeError, RuntimeError) as exc:
            # RuntimeError covers owner helpers such as crypto_recent_reference.fail;
            # a derivation failure is that axis missing, never a crash.
            if isinstance(exc, CryptoPaperRuntimeError):
                code = str(exc).split(":", 1)[0]
            elif isinstance(exc, RuntimeError):
                code = f"{axis}_DERIVATION_FAILED"
            else:
                code = f"{axis}_INPUT_SHAPE_INVALID"
            axes[axis] = {"status": "UNDEFINED", "direction": None}
            diagnostics[axis] = {"missing_reason": code}
            reasons.append(code)

    def trend():
        btc = _btc(source.get("btc"), decision_date)
        return trend_direction(btc.get("trend_category")), {
            "category": btc["trend_category"], "observation_date": btc["latest_finalized_day"],
            "available_at": btc["available_at"], "source_sha256": btc["trend_source_sha256"]}

    def risk():
        btc = _btc(source.get("btc"), decision_date)
        vol = number(btc.get("realized_vol_annualized_fraction"), "RISK_INPUT_INVALID")
        dd = number(btc.get("current_drawdown_fraction"), "RISK_INPUT_INVALID")
        return risk_vol_direction(vol, dd), {
            "vol": str(vol), "dd": str(dd), "rule": "CRYPTO_PAPER_RUNTIME_V1_ABSOLUTE",
            "observation_date": btc["latest_finalized_day"], "available_at": btc["available_at"],
            "source_sha256": btc["risk_source_sha256"]}

    def breadth():
        row = _breadth(source.get("breadth"), decision_date)
        fraction = number(row.get("advance_fraction"), "BREADTH_INPUT_INVALID")
        return breadth_direction(fraction), {
            "advance_fraction": str(fraction), "observation_date": row["as_of_date"],
            "available_at": row["available_at"], "manifest_sha256": row.get("manifest_sha256")}

    def liquidity():
        row = _stablecoin(source.get("stablecoin"), decision_date)
        daily = number(row.get("daily_net_issuance"), "LIQUIDITY_INPUT_INVALID")
        weekly = number(row.get("weekly_net_issuance"), "LIQUIDITY_INPUT_INVALID")
        return liquidity_direction(daily, weekly), {
            "daily_net_issuance": str(daily), "weekly_net_issuance": str(weekly),
            "observation_date": row["observation_date"], "available_at": row["available_at"],
            "source_sha256": row.get("response_sha256")}

    def leadership():
        official, window = select_leadership_window(
            source.get("leadership"), decision_date, primary_observed_earlier)
        breadth_row = source.get("breadth")
        require(isinstance(breadth_row, dict), "LEADERSHIP_MIXED_GENERATION")
        for value in (window.get("last_manifest_sha256"), breadth_row.get("manifest_sha256")):
            require(isinstance(value, str) and SHA256.fullmatch(value) is not None,
                    "LEADERSHIP_MANIFEST_BINDING_INVALID")
        require(window["last_manifest_sha256"] == breadth_row["manifest_sha256"],
                "LEADERSHIP_MIXED_GENERATION")
        code = leadership_code(window)
        return leadership_direction(code), {
            "official_window": official, "leadership_code": code,
            "start_date": window.get("start_date"), "observation_date": window["end_date"],
            "available_at": max(window["point_available_at"])}

    for axis, derive in (("TREND", trend), ("BREADTH", breadth), ("RISK_VOL", risk),
                         ("LIQUIDITY", liquidity), ("LEADERSHIP", leadership)):
        attempt(axis, derive)
    primary_observed = primary_observed_earlier or (
        isinstance(source.get("leadership"), dict)
        and isinstance(source["leadership"].get("windows"), dict)
        and _window_observed(source["leadership"]["windows"].get(PRIMARY), decision_date)
    )
    return {
        "decision_date": decision_date.isoformat(),
        "decision_at": utc_text(decision_at_for(decision_date)),
        "packet_id": f"crypto-{decision_date.isoformat()}",
        "axes": axes,
        "axis_observations": diagnostics,
        "complete": all(axes[axis]["status"] == "DEFINED" for axis in AXES),
        "reasons": sorted(set(reasons)),
        "primary_window_observed": bool(primary_observed),
    }


def build_chain(day_records: dict, chain_start: dt.date, current: dt.date) -> list[dict]:
    require(isinstance(day_records, dict), "DAY_RECORDS_INVALID")
    steps, primary_seen, cursor = [], False, chain_start
    while cursor <= current:
        step = evaluate_day(copy.deepcopy(day_records.get(cursor.isoformat())), cursor, primary_seen)
        primary_seen = step["primary_window_observed"]
        steps.append(step)
        cursor += ONE_DAY
    return steps


def common_sequence(steps: list[dict], case_id: str) -> dict:
    return {
        "schema_version": 1, "market": "CRYPTO", "case_id": case_id,
        "steps": [{"packet_id": s["packet_id"], "as_of_date": s["decision_date"],
                   "axes": copy.deepcopy(s["axes"])} for s in steps],
    }


# ---------------------------------------------------------------------------
# PROVISIONAL_FORWARD_ACCEPTANCE (crypto only)
# ---------------------------------------------------------------------------

def _no_lookahead(step: dict) -> bool:
    decision_date = day(step["decision_date"], "STEP_DATE_INVALID")
    decision_at = decision_at_for(decision_date)
    for axis in AXES:
        observed = step["axis_observations"].get(axis, {})
        if "missing_reason" in observed:
            return False
        if instant(observed.get("available_at"), "STEP_AVAILABLE_AT_INVALID") > decision_at:
            return False
        if day(observed.get("observation_date"), "STEP_DATE_INVALID") > decision_date:
            return False
    return True


def validate_kraken_receipt(raw: bytes | None, policy: dict) -> dict:
    replaced = policy["acceptance"]["replaced_condition_6"]
    require(raw is not None, "KRAKEN_REPLAY_RECEIPT_MISSING")
    expected = replaced.get("receipt_sha256")
    require(isinstance(expected, str) and SHA256.fullmatch(expected) is not None,
            "KRAKEN_REPLAY_TRUST_ANCHOR_MISSING")
    require(sha256(raw) == expected, "KRAKEN_REPLAY_RECEIPT_HASH_MISMATCH")
    from regime import crypto_kraken_btc_replay_diagnostic as KRAKEN

    try:
        receipt = KRAKEN.validate_receipt(_object(raw, "KRAKEN_REPLAY_RECEIPT_INVALID"))
    except KRAKEN.KrakenReplayDiagnosticError as exc:
        raise CryptoPaperRuntimeError("KRAKEN_REPLAY_RECEIPT_INVALID:" + str(exc)) from exc
    require(receipt["status"] == "PASS", "KRAKEN_REPLAY_REQUIRED_RESULTS_NOT_OBSERVED")
    return receipt


def evaluate_acceptance(*, steps: list[dict], rerun_steps: list[dict] | None,
                        evidence_class: str, kraken_receipt_raw: bytes | None,
                        policy: dict) -> dict:
    """Crypto-only acceptance over the chain before the current packet.

    Every call re-evaluates every condition; there is no cached acceptance.
    """
    conditions = {name: False for name in (
        "1_REAL_EVIDENCE_ONLY", "2_REQUIRED_5_OF_5_AXES", "3_NO_LOOKAHEAD",
        "4_DETERMINISTIC_RERUN_BYTE_IDENTICAL", "5_EXACT_COMMON_V1_REPLAY",
        "6_REPLACED_KRAKEN_BULK_BTC_REPLAY_DIAGNOSTIC")}
    result = {
        "label": ACCEPTANCE_LABEL, "status": NOT_ACCEPTED, "reasons": [],
        "conditions": conditions, "accepted_run": None, "replay_report_sha256": None,
        "kraken_receipt_sha256": None,
        "re_review": policy["acceptance"]["re_review"],
    }
    reasons = result["reasons"]
    history = steps[:-1]
    run = []
    for step in reversed(history):
        if not step["complete"]:
            break
        run.insert(0, step)
    conditions["1_REAL_EVIDENCE_ONLY"] = evidence_class == LIVE_NATURAL
    if not conditions["1_REAL_EVIDENCE_ONLY"]:
        reasons.append("ACCEPTANCE_CONDITION_FAILED:1_REAL_EVIDENCE_ONLY")
    conditions["2_REQUIRED_5_OF_5_AXES"] = len(run) >= MINIMUM_CONSECUTIVE_COMPLETE_DAYS
    if not conditions["2_REQUIRED_5_OF_5_AXES"]:
        reasons.append("ACCEPTANCE_CONDITION_FAILED:2_MINIMUM_CONSECUTIVE_COMPLETE_DAYS")
    try:
        conditions["3_NO_LOOKAHEAD"] = bool(run) and all(_no_lookahead(step) for step in run)
    except CryptoPaperRuntimeError:
        conditions["3_NO_LOOKAHEAD"] = False
    if not conditions["3_NO_LOOKAHEAD"]:
        reasons.append("ACCEPTANCE_CONDITION_FAILED:3_NO_LOOKAHEAD")
    conditions["4_DETERMINISTIC_RERUN_BYTE_IDENTICAL"] = (
        rerun_steps is not None and canonical_bytes(rerun_steps) == canonical_bytes(steps)
    )
    if not conditions["4_DETERMINISTIC_RERUN_BYTE_IDENTICAL"]:
        reasons.append("ACCEPTANCE_CONDITION_FAILED:4_DETERMINISTIC_RERUN")
    if run:
        try:
            sequence = common_sequence(run, "crypto-provisional-forward-acceptance")
            report = COMMON.replay_common_v1(sequence)
            COMMON.validate_common_v1_replay(copy.deepcopy(report), copy.deepcopy(sequence))
            conditions["5_EXACT_COMMON_V1_REPLAY"] = True
            result["replay_report_sha256"] = payload_sha256(report)
            result["accepted_run"] = {
                "first_decision_date": run[0]["decision_date"],
                "last_decision_date": run[-1]["decision_date"],
                "complete_day_count": len(run),
                "confirmed_regimes_observed": sorted({row["confirmed_regime"] for row in report["steps"]}),
            }
        except COMMON.DecisionAuthorityError:
            conditions["5_EXACT_COMMON_V1_REPLAY"] = False
    if not conditions["5_EXACT_COMMON_V1_REPLAY"]:
        reasons.append("ACCEPTANCE_CONDITION_FAILED:5_EXACT_COMMON_V1_REPLAY")
    try:
        validate_kraken_receipt(kraken_receipt_raw, policy)
        conditions["6_REPLACED_KRAKEN_BULK_BTC_REPLAY_DIAGNOSTIC"] = True
        result["kraken_receipt_sha256"] = sha256(kraken_receipt_raw)
    except CryptoPaperRuntimeError as exc:
        reasons.append("ACCEPTANCE_CONDITION_FAILED:6_" + str(exc).split(":", 1)[0])
    if all(conditions.values()):
        result["status"] = ACCEPTED
    return result


# ---------------------------------------------------------------------------
# Runtime decision
# ---------------------------------------------------------------------------

def evaluate_crypto_paper_runtime(*, evaluation_at: str, code_revision: str, day_records: dict,
                                  rerun_day_records: dict | None, evidence_class: str,
                                  kraken_receipt_raw: bytes | None, root: Path = ROOT) -> dict:
    """Pure calculation. Any failure yields runtime UNKNOWN, never a carried state."""
    packet = {
        "schema_version": SCHEMA_VERSION, "market": "CRYPTO",
        "evaluation_at": evaluation_at, "code_revision": code_revision,
        "policy_identity": RATIFICATION_IDENTITY, "policy_sha256": POLICY_SHA256,
        "scope": "CRYPTO_INTERNAL_VIRTUAL_PAPER_ONLY",
        "evidence_class": evidence_class, "current_decision_date": None, "decision_at": None,
        "decision_status": "BLOCKED", "paper_regime": "UNKNOWN", "runtime_regime": "UNKNOWN",
        "direction": "UNKNOWN", "confidence": None, "runtime_decision_available": False,
        "acceptance": None, "current_observation": None, "chain": [], "aggregation": None,
        "reasons": [],
        "caveats": [
            "PROVISIONAL_FORWARD_ACCEPTANCE_CONDITION_6_REPLACED_CRYPTO_ONLY",
            "RISK_VOL_INITIAL_PAPER_DEFAULTS",
            "LIQUIDITY_STABLECOIN_ISSUANCE_PROXY_NOT_EXCHANGE_BUYING_POWER",
            "CONFIDENCE_MATCHING_AXIS_FRACTION_NOT_PROBABILITY",
        ],
        "authority": dict(AUTHORITY_CLOSED),
    }
    try:
        now = instant(evaluation_at, "EVALUATION_TIME_INVALID")
        require(isinstance(code_revision, str) and re.fullmatch(r"[0-9a-f]{40}", code_revision) is not None,
                "CODE_REVISION_INVALID")
        require(evidence_class in EVIDENCE_CLASSES, "EVIDENCE_CLASS_INVALID")
        policy = load_policy(root)
        chain_start = day(policy["finalized_packet"]["runtime_chain_start_date"], "CHAIN_START_INVALID")
        require(instant(policy["decision"]["ratified_at_utc"], "RATIFIED_AT_INVALID") <= decision_at_for(chain_start),
                "CHAIN_START_BEFORE_RATIFICATION")
        current = current_decision_date(now)
        require(current >= chain_start, "BEFORE_RUNTIME_CHAIN_START")
        packet["current_decision_date"] = current.isoformat()
        packet["decision_at"] = utc_text(decision_at_for(current))
        steps = build_chain(day_records, chain_start, current)
        rerun = None if rerun_day_records is None else build_chain(rerun_day_records, chain_start, current)
        packet["chain"] = [
            {"decision_date": s["decision_date"], "complete": s["complete"], "reasons": s["reasons"],
             "axis_directions": {a: s["axes"][a]["direction"] for a in AXES}}
            for s in steps
        ]
        report = COMMON.replay_common_v1(common_sequence(steps, "crypto-paper-runtime"))
        packet["aggregation"] = report
        last = steps[-1]
        row = report["steps"][-1]
        packet["current_observation"] = {
            "decision_date": last["decision_date"], "complete": last["complete"],
            "axis_observations": last["axis_observations"], "reasons": last["reasons"],
            "candidate_regime": row["raw_classification"], "score": row["score"],
            "confirmed_regime": row["confirmed_regime"], "hysteresis": row["hysteresis"],
        }
        acceptance = evaluate_acceptance(
            steps=steps, rerun_steps=rerun, evidence_class=evidence_class,
            kraken_receipt_raw=kraken_receipt_raw, policy=policy)
        packet["acceptance"] = acceptance
        reasons = []
        if acceptance["status"] != ACCEPTED:
            reasons.append("PROVISIONAL_FORWARD_ACCEPTANCE_NOT_PASSED")
            reasons.extend(acceptance["reasons"])
        if not last["complete"]:
            reasons.append("CURRENT_FINALIZED_PACKET_INCOMPLETE")
            reasons.extend(last["reasons"])
        elif report["final_regime"] == "UNKNOWN":
            reasons.append("COMMON_CONFIRMATION_PENDING")
        if reasons:
            packet["reasons"] = reasons
        else:
            packet.update(
                decision_status="PAPER_RUNTIME_CLASSIFIED", paper_regime=report["final_regime"],
                runtime_regime=report["final_regime"], direction=report["final_direction"],
                confidence=report["final_confidence"], runtime_decision_available=True,
            )
            packet["authority"]["paper_runtime_display_authorized"] = True
    except (CryptoPaperRuntimeError, COMMON.DecisionAuthorityError, OSError, KeyError,
            TypeError, ValueError, RuntimeError) as exc:
        if isinstance(exc, (CryptoPaperRuntimeError, COMMON.DecisionAuthorityError)):
            code = str(exc).split(":", 1)[0]
        elif isinstance(exc, RuntimeError):
            code = "RUNTIME_DERIVATION_FAILED"
        else:
            code = "INPUT_SHAPE_INVALID"
        packet.update(decision_status="BLOCKED", paper_regime="UNKNOWN", runtime_regime="UNKNOWN",
                      direction="UNKNOWN", confidence=None, runtime_decision_available=False,
                      reasons=[code], authority=dict(AUTHORITY_CLOSED))
    require(packet["runtime_regime"] in load_runtime_authorized_regimes(root), "RUNTIME_REGIME_UNAUTHORIZED")
    packet["decision_id"] = "crypto-paper-regime:" + payload_sha256(packet)
    return packet


def load_runtime_authorized_regimes(root: Path = ROOT) -> list[str]:
    try:
        return list(load_policy(root)["runtime_authorized_regimes"])
    except (CryptoPaperRuntimeError, OSError, KeyError, ValueError):
        return ["UNKNOWN"]


def validate_crypto_paper_runtime(packet: dict, **inputs) -> dict:
    expected = evaluate_crypto_paper_runtime(**inputs)
    require(canonical_bytes(packet) == canonical_bytes(expected), "RUNTIME_REDERIVATION_MISMATCH")
    return copy.deepcopy(expected)
