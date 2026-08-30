#!/usr/bin/env python3
"""P5-08 Crypto Candidate Promotion Rule.

Per-market state machine, continuing P3-12's own state machine one step
further, over already-produced evidence packets only:

    TRADEABLE_UNIVERSE / PAPER_ELIGIBLE (P3-12)
        -> WATCH            (one or more criteria UNKNOWN, none FAILED)
        -> FOCUSED_REVIEW    (every criterion PASSED)
        -> BLOCKED           (one or more criteria FAILED)

This module never captures anything itself. It is a pure derivation over
already-built, already-validated evidence packets from four upstream,
independently-ratified-or-honestly-unratified sources:

* ``universe/upbit_tradeable_universe.py`` (P3-12)             -- identity,
  tradability, and the Upbit ``market_event.caution`` flag.
* ``regime/output_contract.py`` (P1-COM-01, bound live by P1-CR-08)  -- the
  Regime aggregate for market="CRYPTO".
* ``microstructure/upbit_market_evidence.py`` (P4-07)          -- finalized
  1d/4h candles, orderbook + trades. The packet is retained as evidence,
  but it cannot satisfy TREND or VOLUME_LIQUIDITY while its policy is
  unratified and no ratified candidate-level transform exists.
* ``.github/scripts/crypto_leadership.py`` (P1-CR-07)          -- the
  ratified BTC-reference relative-strength measurement.

Every output row's ``authority`` block is hardcoded all-``false``: a
``FOCUSED_REVIEW`` classification is a review-queue label, never an
investable/PAPER/Stage/order grant. Turning this into real authority is a
separate, later, explicitly-ratified change this module cannot make.

Scope boundary vs P5-09 (Crypto PAPER Buy Eligibility, next WBS item): this
module stops at FOCUSED_REVIEW/WATCH/BLOCKED classification with reasons. It
never computes entry zone, invalidation price, planned stop, PAPER quantity,
fee/slippage assumptions, planned loss vs. Crypto risk headroom, expiry/next
review time, a duplicate-guard key, or "PAPER_READY" readiness -- those
fields are explicitly P5-09's job per the Notion policy doc's own
``Focused Review -> PAPER_READY`` state-machine step.

--------------------------------------------------------------------------
Per-criterion evidence basis (see docs/crypto_candidate_promotion_contract.md
for the full table -- this is the short version):

  IDENTITY           ratified/deterministic -- reused P3-12 gate.
  TRADABILITY        ratified/deterministic -- reused P3-12 gate.
  REGIME             UNKNOWN by construction -- P1-CR-08's own boundary:
                      regime/output_contract.py authorizes only "UNKNOWN" for
                      every market until P1-COM-05 ratifies a minimum
                      coverage gate. No RISK_ON/NEUTRAL/RISK_OFF/STRESS value
                      is ever readable. This directly matches the Notion
                      policy's own text: "Regime가 ... UNKNOWN이면 WATCH만
                      허용" -- a correct literal reading, not a workaround.
  TREND              UNKNOWN by construction -- there is no ratified
                      candidate-level daily/4h trend transform. A two-close
                      comparison would itself invent the missing rule.
  RELATIVE_STRENGTH  FAIL when the ratified BTC leg is non-positive;
                      otherwise UNKNOWN because the required peer-group leg
                      is explicitly UNRATIFIED.
  VOLUME_LIQUIDITY   UNKNOWN by construction -- family presence is coverage,
                      not confirmation, and the relevant P4-07 thresholds are
                      PROPOSED_UNRATIFIED.
  OVEREXTENSION      UNKNOWN by construction -- no mechanical or ratified
                      definition of "과열·급등 추격" exists anywhere in this
                      repository; inventing one is exactly what this module
                      must never do.
  MATERIAL_BLOCKER    FAIL when Upbit caution is active; otherwise UNKNOWN
                      because security/network-outage coverage is missing.
--------------------------------------------------------------------------
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


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoCandidatePromotionError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CryptoCandidatePromotionError(ValueError):
    """Fail-closed P5-08 candidate-promotion contract violation."""


UPBIT_UNIVERSE = _load("crypto_candidate_promotion_universe", "universe/upbit_tradeable_universe.py")
REGIME_OUTPUT_CONTRACT = _load("crypto_candidate_promotion_regime_output_contract", "regime/output_contract.py")
CRYPTO_LEADERSHIP = _load("crypto_candidate_promotion_leadership", ".github/scripts/crypto_leadership.py")
MARKET_EVIDENCE = _load("crypto_candidate_promotion_market_evidence", "microstructure/upbit_market_evidence.py")


CONTRACT_PATH = ROOT / "config" / "crypto_candidate_promotion_contract.json"
OUTPUT_SCHEMA_VERSION = "crypto_candidate_promotion_packet/2"

STATE_WATCH = "WATCH"
STATE_FOCUSED_REVIEW = "FOCUSED_REVIEW"
STATE_BLOCKED = "BLOCKED"
PROMOTION_STATES = (STATE_WATCH, STATE_FOCUSED_REVIEW, STATE_BLOCKED)

CRITERIA = (
    "IDENTITY", "TRADABILITY", "REGIME", "TREND",
    "RELATIVE_STRENGTH", "VOLUME_LIQUIDITY", "OVEREXTENSION", "MATERIAL_BLOCKER",
)
CRITERION_STATUSES = ("PASS", "FAIL", "UNKNOWN")

# The ratified PRIMARY window from config/crypto_leadership_policy.json.
# Hardcoded, not re-derived: if that policy's window_id ever changes, this
# module must be updated deliberately, not silently follow along.
LEADERSHIP_PRIMARY_WINDOW_ID = "primary_30d"

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")

# Hardcoded, never policy-driven: no evaluator in this module may set any of
# these to true. Turning a FOCUSED_REVIEW classification into real
# investable/PAPER/Stage/order authority is a separate, later,
# explicitly-ratified change (P5-09's job at the earliest, not this one's).
_ROW_AUTHORITY = {
    "investable_eligible": False,
    "paper_eligible": False,
    "focused_review_authorized": False,
    "entry_authorized": False,
    "stage_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
}


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
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != 2
        or value.get("contract_version") != "crypto_candidate_promotion_contract/2"
    ):
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:contract_version")
    if tuple(value.get("criteria", [])) != CRITERIA:
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:criteria")
    if tuple(value.get("criterion_statuses", [])) != CRITERION_STATUSES:
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:criterion_statuses")
    if tuple(value.get("promotion_states", [])) != PROMOTION_STATES:
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:promotion_states")
    for key, expected in value.get("authority", {}).items():
        if expected is not False:
            raise CryptoCandidatePromotionError(f"CONTRACT_AUTHORITY_NOT_FALSE:{key}")
    if set(value.get("authority", {})) != set(_ROW_AUTHORITY):
        raise CryptoCandidatePromotionError("CONTRACT_FIELD_MISMATCH:authority_keys")
    return copy.deepcopy(value)


def _decimal(value, label: str) -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise CryptoCandidatePromotionError(f"DECIMAL_INVALID:{label}:{value!r}") from exc


def _criterion(status: str, reason: str, **extra) -> dict:
    if status not in CRITERION_STATUSES:
        raise CryptoCandidatePromotionError(f"CRITERION_STATUS_INVALID:{status}")
    return {"status": status, "reason": reason, **extra}


def _parse_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        raise CryptoCandidatePromotionError(f"DATE_INVALID:{label}")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise CryptoCandidatePromotionError(f"DATE_INVALID:{label}") from exc


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not _UTC_RE.fullmatch(value):
        raise CryptoCandidatePromotionError(f"UTC_INVALID:{label}")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise CryptoCandidatePromotionError(f"UTC_INVALID:{label}") from exc


def _require_false_authority(value: object, expected: dict, label: str) -> None:
    if value != expected or any(item is not False for item in value.values()):
        raise CryptoCandidatePromotionError(f"AUTHORITY_INVALID:{label}")


def _validate_payload_hash(packet: dict, label: str) -> None:
    claimed = packet.get("payload_sha256")
    if not isinstance(claimed, str) or not _SHA_RE.fullmatch(claimed):
        raise CryptoCandidatePromotionError(f"PAYLOAD_SHA256_INVALID:{label}")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    if payload_sha256(unsigned) != claimed:
        raise CryptoCandidatePromotionError(f"PAYLOAD_SHA256_MISMATCH:{label}")


def _validate_universe_packet(packet: dict, evaluation_as_of: str) -> dict:
    """Validate the P3-12 consumer boundary before trusting any row state.

    P3-12 does not yet export a public validator, so the consumer pins its
    complete emitted schema, hash, authority, summary, and current local
    policy/taxonomy ratification state here. A self-consistent fabricated
    PAPER_ELIGIBLE row cannot bypass an unratified local policy.
    """
    expected_keys = {
        "schema_version", "snapshot_date", "evaluation_as_of", "available_at",
        "manifest_sha256", "policy_version", "policy_ratified", "taxonomy_version",
        "taxonomy_ratified", "duplicate_market_codes", "summary", "markets",
        "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoCandidatePromotionError("UNIVERSE_PACKET_SCHEMA_MISMATCH")
    if packet["schema_version"] != UPBIT_UNIVERSE.OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidatePromotionError("UNIVERSE_PACKET_SCHEMA_MISMATCH")
    _validate_payload_hash(packet, "universe")
    _require_false_authority(packet["authority"], UPBIT_UNIVERSE._ROW_AUTHORITY, "universe")

    source_date = _parse_date(packet["evaluation_as_of"], "universe.evaluation_as_of")
    if source_date != _parse_date(evaluation_as_of, "evaluation_as_of"):
        raise CryptoCandidatePromotionError("UNIVERSE_EVALUATION_DATE_MISMATCH")
    _parse_date(packet["snapshot_date"], "universe.snapshot_date")
    available_at = _parse_utc(packet["available_at"], "universe.available_at")
    evaluation_end = dt.datetime.combine(source_date, dt.time.max, tzinfo=dt.timezone.utc)
    if available_at > evaluation_end:
        raise CryptoCandidatePromotionError("UNIVERSE_AVAILABLE_AT_FUTURE_DATED")
    if not isinstance(packet["manifest_sha256"], str) or not _SHA_RE.fullmatch(packet["manifest_sha256"]):
        raise CryptoCandidatePromotionError("UNIVERSE_MANIFEST_SHA256_INVALID")

    policy = UPBIT_UNIVERSE.load_policy()
    taxonomy = UPBIT_UNIVERSE.load_taxonomy()
    expected_policy_ratified = UPBIT_UNIVERSE._approval_effective(
        policy, evaluation_as_of, date_field="effective_date",
    )
    expected_taxonomy_ratified = UPBIT_UNIVERSE._approval_effective(
        taxonomy, evaluation_as_of, date_field="effective_from",
    )
    if packet["policy_version"] != policy.get("policy_version") or packet["policy_ratified"] is not expected_policy_ratified:
        raise CryptoCandidatePromotionError("UNIVERSE_POLICY_PIN_MISMATCH")
    if packet["taxonomy_version"] != taxonomy.get("policy_version") or packet["taxonomy_ratified"] is not expected_taxonomy_ratified:
        raise CryptoCandidatePromotionError("UNIVERSE_TAXONOMY_PIN_MISMATCH")

    rows = packet["markets"]
    if not isinstance(rows, list):
        raise CryptoCandidatePromotionError("UNIVERSE_MARKETS_INVALID")
    market_ids = [row.get("market") if isinstance(row, dict) else None for row in rows]
    if any(not isinstance(market, str) or not market for market in market_ids):
        raise CryptoCandidatePromotionError("UNIVERSE_MARKETS_INVALID")
    if market_ids != sorted(set(market_ids)):
        raise CryptoCandidatePromotionError("UNIVERSE_MARKETS_INVALID")
    states = (
        UPBIT_UNIVERSE.STATE_OBSERVATION_POOL, UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE,
        UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE, UPBIT_UNIVERSE.STATE_BLOCKED,
    )
    row_keys = {
        "market", "state", "reason", "candidate_canonical_asset_id",
        "market_event_warning", "market_event_caution_any", "observed_daily_candle_count",
        "trailing_30d_krw_turnover", "kraken_cross_exchange_reference", "authority",
    }
    for row in rows:
        if not isinstance(row, dict) or set(row) != row_keys or row["state"] not in states:
            raise CryptoCandidatePromotionError("UNIVERSE_ROW_INVALID")
        _require_false_authority(row["authority"], UPBIT_UNIVERSE._ROW_AUTHORITY, f"universe:{row.get('market')}")
        if row["state"] in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
            if not (expected_policy_ratified and expected_taxonomy_ratified):
                raise CryptoCandidatePromotionError("UNIVERSE_IN_SCOPE_STATE_WITH_UNRATIFIED_POLICY")
            if not isinstance(row["candidate_canonical_asset_id"], str) or not row["candidate_canonical_asset_id"]:
                raise CryptoCandidatePromotionError("UNIVERSE_IN_SCOPE_IDENTITY_INVALID")
        if row["state"] == UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE and row["reason"] != "PAPER_ELIGIBLE_ALL_GATES_PASSED":
            raise CryptoCandidatePromotionError("UNIVERSE_PAPER_ELIGIBLE_REASON_INVALID")
    expected_summary = {
        "market_count": len(rows),
        "observation_pool_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_OBSERVATION_POOL for row in rows),
        "tradeable_universe_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE for row in rows),
        "paper_eligible_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE for row in rows),
        "blocked_count": sum(row["state"] == UPBIT_UNIVERSE.STATE_BLOCKED for row in rows),
    }
    if packet["summary"] != expected_summary:
        raise CryptoCandidatePromotionError("UNIVERSE_SUMMARY_MISMATCH")
    return copy.deepcopy(packet)


def _validate_market_evidence_packet(packet: dict, market: str, evaluation_as_of: str) -> dict:
    expected_keys = {
        "schema_version", "market", "as_of", "captured_at", "policy_version",
        "policy_ratified", "candles", "trades", "orderbook", "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_SCHEMA_MISMATCH:{market}")
    if packet["schema_version"] != MARKET_EVIDENCE.OUTPUT_SCHEMA_VERSION or packet["market"] != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_IDENTITY_MISMATCH:{market}")
    _validate_payload_hash(packet, f"market_evidence:{market}")
    _require_false_authority(packet["authority"], MARKET_EVIDENCE._EVIDENCE_AUTHORITY, f"market_evidence:{market}")
    policy = MARKET_EVIDENCE.load_policy()
    if packet["policy_version"] != policy.get("policy_version"):
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_POLICY_PIN_MISMATCH:{market}")
    if packet["policy_ratified"] is not (policy.get("approval_status") == "RATIFIED"):
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_POLICY_STATUS_MISMATCH:{market}")
    as_of = _parse_utc(packet["as_of"], f"market_evidence.{market}.as_of")
    captured_at = _parse_utc(packet["captured_at"], f"market_evidence.{market}.captured_at")
    evaluation_end = dt.datetime.combine(_parse_date(evaluation_as_of, "evaluation_as_of"), dt.time.max, tzinfo=dt.timezone.utc)
    if captured_at < as_of or captured_at > evaluation_end:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_TIME_INVALID:{market}")
    if set(packet["candles"]) != set(MARKET_EVIDENCE.finalization.TIMEFRAMES):
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_TIMEFRAMES_MISMATCH:{market}")
    for timeframe, candle in packet["candles"].items():
        if candle.get("market") != market or candle.get("timeframe") != timeframe:
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_CANDLE_IDENTITY_MISMATCH:{market}:{timeframe}")
        if candle.get("finalized_candle_count") != len(candle.get("finalized_candles", [])):
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_CANDLE_COUNT_MISMATCH:{market}:{timeframe}")
        _require_false_authority(candle.get("authority"), MARKET_EVIDENCE._EVIDENCE_AUTHORITY, f"candle:{market}:{timeframe}")
    for label in ("trades", "orderbook"):
        value = packet[label]
        if not isinstance(value, dict) or value.get("market") != market:
            raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_{label.upper()}_INVALID:{market}")
        _require_false_authority(value.get("authority"), MARKET_EVIDENCE._EVIDENCE_AUTHORITY, f"{label}:{market}")
    return copy.deepcopy(packet)


def _validate_leadership_output(output: dict, evaluation_as_of: str) -> dict:
    if not isinstance(output, dict) or output.get("schema_version") != 2:
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_SCHEMA_MISMATCH")
    contract = CRYPTO_LEADERSHIP.load_contract()
    policy = CRYPTO_LEADERSHIP.load_leadership_policy()
    CRYPTO_LEADERSHIP.require_ratified_leadership_policy(policy)
    if output.get("contract_version") != contract["contract_version"] or output.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_HEADER_MISMATCH")
    if _parse_date(output.get("as_of_date"), "leadership.as_of_date") > _parse_date(evaluation_as_of, "evaluation_as_of"):
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_FUTURE_DATED")
    leadership_pin = (output.get("policies") or {}).get("leadership") or {}
    if (
        leadership_pin.get("policy_version") != policy["policy_version"]
        or leadership_pin.get("policy_sha256") != CRYPTO_LEADERSHIP.file_sha256(CRYPTO_LEADERSHIP.LEADERSHIP_POLICY_PATH)
        or leadership_pin.get("approval_status") != "RATIFIED"
        or leadership_pin.get("group_coverage_policy_status") != "UNRATIFIED"
    ):
        raise CryptoCandidatePromotionError("LEADERSHIP_POLICY_PIN_MISMATCH")
    authority = CRYPTO_LEADERSHIP.authority_boundary()
    for key, expected in authority.items():
        if output.get(key) is not expected:
            raise CryptoCandidatePromotionError(f"LEADERSHIP_AUTHORITY_INVALID:{key}")
    windows = output.get("windows")
    if not isinstance(windows, list) or {row.get("window_id") for row in windows} != {
        item["window_id"] for item in policy["windows"]
    }:
        raise CryptoCandidatePromotionError("LEADERSHIP_WINDOWS_INVALID")
    return copy.deepcopy(output)


# ---------------------------------------------------------------------------
# Individual criterion evaluators
# ---------------------------------------------------------------------------

def evaluate_identity(universe_row: dict) -> dict:
    """P3-12 already requires a ratified ``canonical_asset_id`` mapping
    before any market can leave OBSERVATION_POOL -- see
    ``universe/upbit_tradeable_universe.py::build_classification``'s
    ``IDENTITY_UNRATIFIED`` gate. For any row this module is scoped to
    process (state TRADEABLE_UNIVERSE/PAPER_ELIGIBLE), this is therefore
    unreachable-as-UNKNOWN by construction; the UNKNOWN branch below is a
    defensive check, not a real production path.
    """
    if universe_row.get("candidate_canonical_asset_id") is not None:
        return _criterion("PASS", "IDENTITY_RATIFIED_VIA_P3_12")
    return _criterion("UNKNOWN", "IDENTITY_UNRATIFIED")


def evaluate_tradability(universe_row: dict) -> dict:
    """A market only reaches this module's input set after clearing every
    P3-12 turnover/spread/listing-history/capture-freshness gate. This
    criterion is a documented echo of that fact, never a new check.
    """
    state = universe_row.get("state")
    if state in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
        return _criterion("PASS", f"P3_12_STATE:{state}")
    raise CryptoCandidatePromotionError(f"UNIVERSE_ROW_OUT_OF_SCOPE:{state}")


def evaluate_regime(regime_payload: dict) -> dict:
    """See module docstring's REGIME row. ``regime_payload`` must already be
    a schema-valid ``regime/output_contract.py`` payload for market="CRYPTO"
    (``build_promotion_packet`` validates this before calling here). Its
    ``regime`` field can never legally be anything but "UNKNOWN" while
    ``runtime_authorized_regimes == ["UNKNOWN"]`` -- ``validate_output``
    itself raises otherwise. The defensive check below still fails closed
    (raises, never guesses an interpretation) if that invariant is ever
    violated without this module being updated first.
    """
    if regime_payload.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError(f"REGIME_PAYLOAD_MARKET_MISMATCH:{regime_payload.get('market')}")
    regime_value = regime_payload.get("regime")
    if regime_value != "UNKNOWN":
        raise CryptoCandidatePromotionError(f"REGIME_VALUE_NOT_UNDERSTOOD:{regime_value}")
    return _criterion("UNKNOWN", "REGIME_AGGREGATE_UNAUTHORIZED_PENDING_P1_COM_05")


def _candle_close(candle_row: dict) -> Decimal:
    return _decimal(candle_row.get("trade_price"), "trade_price")


def _direction(finalized_candles) -> str | None:
    """The minimal-parameter mechanical directional fact: compares the two
    most-recently-finalized candles' close prices for one timeframe. Returns
    ``None`` (never a guess) when fewer than two finalized candles exist.
    """
    if not isinstance(finalized_candles, list) or len(finalized_candles) < 2:
        return None
    ordered = sorted(finalized_candles, key=lambda row: row["close_time"])
    latest = _candle_close(ordered[-1])
    previous = _candle_close(ordered[-2])
    if latest > previous:
        return "UP"
    if latest < previous:
        return "DOWN"
    return "FLAT"


def evaluate_trend(market: str, market_evidence_packet: dict | None) -> dict:
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    if market_evidence_packet.get("market") != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_PACKET_MARKET_MISMATCH:{market}")
    candles = market_evidence_packet.get("candles") or {}
    daily = (candles.get("1d") or {}).get("finalized_candles")
    four_hour = (candles.get("4h") or {}).get("finalized_candles")
    daily_direction = _direction(daily)
    four_hour_direction = _direction(four_hour)
    if daily_direction is None or four_hour_direction is None:
        return _criterion(
            "UNKNOWN", "INSUFFICIENT_FINALIZED_CANDLES",
            daily_direction=daily_direction, four_hour_direction=four_hour_direction,
        )
    return _criterion(
        "UNKNOWN", "NO_RATIFIED_CANDIDATE_TREND_RULE",
        daily_direction=daily_direction, four_hour_direction=four_hour_direction,
    )


def evaluate_relative_strength(canonical_asset_id: str | None, leadership_output: dict | None) -> dict:
    """Evaluate the known BTC leg without pretending the peer leg exists.

    The criterion is conjunctive in the canonical policy: non-positive BTC
    relative strength is enough to FAIL, while a positive BTC leg remains
    UNKNOWN until the peer-group leg is ratified and measured.
    """
    if canonical_asset_id is None:
        return _criterion("UNKNOWN", "IDENTITY_UNRATIFIED")
    if leadership_output is None:
        return _criterion("UNKNOWN", "LEADERSHIP_OUTPUT_MISSING")
    if canonical_asset_id == "BTC":
        return _criterion("UNKNOWN", "BTC_SELF_REFERENCE_RULE_UNRATIFIED")
    if leadership_output.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError("LEADERSHIP_OUTPUT_MARKET_MISMATCH")
    windows = {w.get("window_id"): w for w in leadership_output.get("windows", [])}
    window = windows.get(LEADERSHIP_PRIMARY_WINDOW_ID)
    if window is None or window.get("role") != "PRIMARY":
        raise CryptoCandidatePromotionError("LEADERSHIP_PRIMARY_WINDOW_MISSING")
    if window.get("status") != "OBSERVED_UNCLASSIFIED":
        return _criterion("UNKNOWN", f"LEADERSHIP_WINDOW_UNKNOWN:{window.get('unknown_reason')}")
    partial_ids = {item["canonical_asset_id"] for item in window.get("partial_window_assets", [])}
    if canonical_asset_id in partial_ids:
        return _criterion("UNKNOWN", "LEADERSHIP_ASSET_WINDOW_INCOMPLETE")
    asset_row = next(
        (item for item in window.get("asset_relative_strength", []) if item.get("canonical_asset_id") == canonical_asset_id),
        None,
    )
    if asset_row is None:
        return _criterion("UNKNOWN", "LEADERSHIP_ASSET_NOT_COVERED")
    rs_text = asset_row.get("relative_strength_vs_btc")
    rs = _decimal(rs_text, "relative_strength_vs_btc")
    if rs > 0:
        return _criterion(
            "UNKNOWN", "PEER_RELATIVE_STRENGTH_UNRATIFIED",
            btc_leg_status="PASS", relative_strength_vs_btc=rs_text,
        )
    return _criterion("FAIL", f"RELATIVE_STRENGTH_VS_BTC_NOT_POSITIVE:{rs_text}", relative_strength_vs_btc=rs_text)


def evaluate_volume_liquidity(market: str, market_evidence_packet: dict | None) -> dict:
    """See module docstring's VOLUME_LIQUIDITY row: structural presence
    only, never the unratified spread/slippage/staleness thresholds.
    """
    if market_evidence_packet is None:
        return _criterion("UNKNOWN", "MARKET_EVIDENCE_PACKET_MISSING")
    if market_evidence_packet.get("market") != market:
        raise CryptoCandidatePromotionError(f"MARKET_EVIDENCE_PACKET_MARKET_MISMATCH:{market}")
    candles = market_evidence_packet.get("candles") or {}
    price_family_present = bool((candles.get("1d") or {}).get("finalized_candle_count")) and bool(
        (candles.get("4h") or {}).get("finalized_candle_count")
    )
    orderbook = market_evidence_packet.get("orderbook") or {}
    trades = market_evidence_packet.get("trades") or {}
    liquidity_family_present = orderbook.get("best_bid") is not None and orderbook.get("best_ask") is not None
    volume_family_present = bool(trades.get("trade_count"))
    if price_family_present and liquidity_family_present and volume_family_present:
        return _criterion(
            "UNKNOWN", "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED",
            price_family_present=True,
            liquidity_family_present=True,
            volume_family_present=True,
        )
    return _criterion(
        "UNKNOWN", "EVIDENCE_FAMILY_INCOMPLETE",
        price_family_present=price_family_present,
        liquidity_family_present=liquidity_family_present,
        volume_family_present=volume_family_present,
    )


def evaluate_overextension() -> dict:
    """See module docstring's OVEREXTENSION row: no mechanical or ratified
    definition of "과열·급등 추격" exists anywhere in this repository.
    """
    return _criterion("UNKNOWN", "NO_RATIFIED_OVEREXTENSION_THRESHOLD")


def evaluate_material_blocker(universe_row: dict) -> dict:
    """See module docstring's MATERIAL_BLOCKER row."""
    caution_any = universe_row.get("market_event_caution_any")
    if caution_any is None:
        return _criterion("UNKNOWN", "CAUTION_FLAG_STATUS_UNKNOWN")
    if caution_any is True:
        return _criterion("FAIL", "UPBIT_MARKET_EVENT_CAUTION_ACTIVE")
    return _criterion(
        "UNKNOWN", "SECURITY_AND_NETWORK_OUTAGE_COVERAGE_MISSING",
        upbit_market_event_caution_active=False,
    )


def evaluate_criteria(
    universe_row: dict,
    *,
    regime_payload: dict,
    market_evidence_packet: dict | None,
    leadership_output: dict | None,
) -> dict:
    market = universe_row["market"]
    return {
        "IDENTITY": evaluate_identity(universe_row),
        "TRADABILITY": evaluate_tradability(universe_row),
        "REGIME": evaluate_regime(regime_payload),
        "TREND": evaluate_trend(market, market_evidence_packet),
        "RELATIVE_STRENGTH": evaluate_relative_strength(
            universe_row.get("candidate_canonical_asset_id"), leadership_output
        ),
        "VOLUME_LIQUIDITY": evaluate_volume_liquidity(market, market_evidence_packet),
        "OVEREXTENSION": evaluate_overextension(),
        "MATERIAL_BLOCKER": evaluate_material_blocker(universe_row),
    }


# ---------------------------------------------------------------------------
# State-machine transition rule -- a pure function of already-computed
# criteria, independent of how each criterion was sourced. This is what
# proves the RULE reaches FOCUSED_REVIEW given an all-PASS input, even
# though REGIME's own evaluator can never itself produce PASS today.
# ---------------------------------------------------------------------------

def aggregate_state(criteria: dict) -> tuple[str, str]:
    if set(criteria) != set(CRITERIA):
        raise CryptoCandidatePromotionError(f"CRITERIA_SET_INVALID:{sorted(criteria)}")
    failed = sorted(name for name, result in criteria.items() if result["status"] == "FAIL")
    if failed:
        return STATE_BLOCKED, "CRITERIA_FAILED:" + ",".join(failed)
    unknown = sorted(name for name, result in criteria.items() if result["status"] == "UNKNOWN")
    if unknown:
        return STATE_WATCH, "CRITERIA_UNKNOWN:" + ",".join(unknown)
    return STATE_FOCUSED_REVIEW, "ALL_CRITERIA_PASSED"


def evaluate_candidate(
    universe_row: dict,
    *,
    regime_payload: dict,
    market_evidence_packet: dict | None,
    leadership_output: dict | None,
) -> dict:
    criteria = evaluate_criteria(
        universe_row,
        regime_payload=regime_payload,
        market_evidence_packet=market_evidence_packet,
        leadership_output=leadership_output,
    )
    state, reason = aggregate_state(criteria)
    return {
        "market": universe_row["market"],
        "canonical_asset_id": universe_row.get("candidate_canonical_asset_id"),
        "p3_12_state": universe_row["state"],
        "criteria": criteria,
        "promotion_state": state,
        "promotion_reason": reason,
        "authority": dict(_ROW_AUTHORITY),
    }


def build_promotion_packet(
    universe_packet: dict,
    regime_payload: dict,
    market_evidence_by_market: dict | None,
    leadership_output: dict | None,
    *,
    evaluation_as_of: str,
) -> dict:
    """Pure derivation over four already-built, already-timestamped
    upstream evidence packets. Deterministic: the same inputs always
    produce byte-identical output (no wall-clock or random value is read
    inside this function).
    """
    _parse_date(evaluation_as_of, "evaluation_as_of")
    universe_packet = _validate_universe_packet(universe_packet, evaluation_as_of)
    try:
        regime_payload = REGIME_OUTPUT_CONTRACT.validate_output(regime_payload)
    except REGIME_OUTPUT_CONTRACT.OutputContractError as exc:
        raise CryptoCandidatePromotionError(f"REGIME_PAYLOAD_INVALID:{exc}") from exc
    if regime_payload.get("market") != "CRYPTO":
        raise CryptoCandidatePromotionError("REGIME_PAYLOAD_MARKET_MISMATCH")
    regime_generated_at = _parse_utc(regime_payload.get("generated_at"), "regime.generated_at")
    evaluation_date = _parse_date(evaluation_as_of, "evaluation_as_of")
    evaluation_end = dt.datetime.combine(
        evaluation_date, dt.time.max, tzinfo=dt.timezone.utc
    )
    if regime_generated_at > evaluation_end:
        raise CryptoCandidatePromotionError("REGIME_PAYLOAD_FUTURE_DATED")
    if regime_generated_at.date() != evaluation_date:
        raise CryptoCandidatePromotionError("REGIME_PAYLOAD_DATE_MISMATCH")

    market_evidence_by_market = market_evidence_by_market or {}
    if not isinstance(market_evidence_by_market, dict):
        raise CryptoCandidatePromotionError("MARKET_EVIDENCE_MAP_INVALID")
    universe_markets = {row["market"] for row in universe_packet["markets"]}
    if any(not isinstance(market, str) or market not in universe_markets for market in market_evidence_by_market):
        raise CryptoCandidatePromotionError("MARKET_EVIDENCE_OUT_OF_UNIVERSE")
    normalized_market_evidence = {
        market: _validate_market_evidence_packet(packet, market, evaluation_as_of)
        for market, packet in sorted(market_evidence_by_market.items())
    }
    normalized_leadership = (
        _validate_leadership_output(leadership_output, evaluation_as_of)
        if leadership_output is not None else None
    )

    rows = []
    for row in universe_packet.get("markets", []):
        if row.get("state") not in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
            continue
        rows.append(
            evaluate_candidate(
                row,
                regime_payload=regime_payload,
                market_evidence_packet=normalized_market_evidence.get(row["market"]),
                leadership_output=normalized_leadership,
            )
        )

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": load_contract()["contract_version"],
        "evaluation_as_of": evaluation_as_of,
        "universe_snapshot_date": universe_packet.get("snapshot_date"),
        "universe_manifest_sha256": universe_packet.get("manifest_sha256"),
        "regime_contract_version": regime_payload.get("contract_version"),
        "regime_generated_at": regime_payload.get("generated_at"),
        "leadership_as_of_date": (leadership_output or {}).get("as_of_date"),
        "source_packets": {
            "universe": copy.deepcopy(universe_packet),
            "regime": copy.deepcopy(regime_payload),
            "market_evidence_by_market": copy.deepcopy(normalized_market_evidence),
            "leadership": copy.deepcopy(normalized_leadership),
        },
        "candidates": rows,
        "summary": {
            "candidate_count": len(rows),
            "watch_count": sum(1 for r in rows if r["promotion_state"] == STATE_WATCH),
            "focused_review_count": sum(1 for r in rows if r["promotion_state"] == STATE_FOCUSED_REVIEW),
            "blocked_count": sum(1 for r in rows if r["promotion_state"] == STATE_BLOCKED),
        },
        "authority": dict(_ROW_AUTHORITY),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_output(packet: dict) -> dict:
    """Re-validate embedded sources and reproduce the full derivation.

    Downstream P5-09 must call this function instead of trusting a cached
    state label or a caller-supplied subset of fields.
    """
    expected_keys = {
        "schema_version", "contract_version", "evaluation_as_of",
        "universe_snapshot_date", "universe_manifest_sha256",
        "regime_contract_version", "regime_generated_at", "leadership_as_of_date",
        "source_packets", "candidates", "summary", "authority", "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != expected_keys:
        raise CryptoCandidatePromotionError("OUTPUT_SCHEMA_MISMATCH")
    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidatePromotionError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    contract = load_contract()
    if packet.get("contract_version") != contract["contract_version"]:
        raise CryptoCandidatePromotionError("OUTPUT_CONTRACT_VERSION_MISMATCH")
    _validate_payload_hash(packet, "promotion_output")
    _require_false_authority(packet.get("authority"), _ROW_AUTHORITY, "promotion_output")
    sources = packet.get("source_packets")
    if not isinstance(sources, dict) or set(sources) != {
        "universe", "regime", "market_evidence_by_market", "leadership"
    }:
        raise CryptoCandidatePromotionError("OUTPUT_SOURCE_PACKETS_INVALID")
    rebuilt = build_promotion_packet(
        sources["universe"],
        sources["regime"],
        sources["market_evidence_by_market"],
        sources["leadership"],
        evaluation_as_of=packet["evaluation_as_of"],
    )
    if canonical_json(rebuilt) != canonical_json(packet):
        raise CryptoCandidatePromotionError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)
