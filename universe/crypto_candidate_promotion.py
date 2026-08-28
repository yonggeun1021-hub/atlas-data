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
  1d/4h candles (trend), orderbook + trades (volume/liquidity family
  presence).
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
  TREND              deterministic/mechanical -- minimal two-finalized-
                      candle direction comparison per timeframe (1d, 4h), no
                      invented lookback/EMA period (N=1 is the only
                      parameter-free choice; unlike btc_trend.py's BTC-
                      specific, ratified 200DMA convention, no repo-standard
                      lookback exists for arbitrary Upbit KRW markets).
  RELATIVE_STRENGTH  ratified/deterministic, narrowed scope -- only the
                      ratified BTC-reference leg (crypto_leadership_policy.json
                      PRIMARY 30-day window) is evaluated; the Notion text's
                      peer-group leg ("동종 peer 대비") is never evaluated
                      because crypto_leadership's own
                      group_coverage_policy_status is permanently UNRATIFIED
                      -- a documented scope decision, not a fresh conflict.
  VOLUME_LIQUIDITY   structural/deterministic -- presence of two
                      independently-sourced evidence families (P4-07
                      candles vs. orderbook+trades), never the (currently
                      PROPOSED_UNRATIFIED) spread/slippage/staleness
                      NORMAL/ABNORMAL thresholds from
                      upbit_market_evidence_policy.json.
  OVEREXTENSION      UNKNOWN by construction -- no mechanical or ratified
                      definition of "과열·급등 추격" exists anywhere in this
                      repository; inventing one is exactly what this module
                      must never do.
  MATERIAL_BLOCKER    ratified/deterministic, partial coverage -- reuses
                      P3-12's Upbit ``market_event.caution`` flag (listing
                      warnings are already excluded upstream by P3-12).
                      Security/network-outage incidents have no dedicated
                      evidence source in this repo and are NOT independently
                      detected; documented as a coverage gap.
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


CONTRACT_PATH = ROOT / "config" / "crypto_candidate_promotion_contract.json"
OUTPUT_SCHEMA_VERSION = "crypto_candidate_promotion_packet/1"

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
    if not isinstance(value, dict) or value.get("contract_version") != "crypto_candidate_promotion_contract/1":
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
    if {daily_direction, four_hour_direction} == {"UP", "DOWN"}:
        return _criterion(
            "FAIL", f"TREND_DIRECTION_CONFLICT:1d={daily_direction}:4h={four_hour_direction}",
            daily_direction=daily_direction, four_hour_direction=four_hour_direction,
        )
    return _criterion(
        "PASS", f"TREND_DIRECTION_NOT_CONFLICTING:1d={daily_direction}:4h={four_hour_direction}",
        daily_direction=daily_direction, four_hour_direction=four_hour_direction,
    )


def evaluate_relative_strength(canonical_asset_id: str | None, leadership_output: dict | None) -> dict:
    """See module docstring's RELATIVE_STRENGTH row: BTC-reference leg only."""
    if canonical_asset_id is None:
        return _criterion("UNKNOWN", "IDENTITY_UNRATIFIED")
    if leadership_output is None:
        return _criterion("UNKNOWN", "LEADERSHIP_OUTPUT_MISSING")
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
        return _criterion("PASS", f"RELATIVE_STRENGTH_VS_BTC_POSITIVE:{rs_text}", relative_strength_vs_btc=rs_text)
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
        return _criterion("PASS", "PRICE_AND_VOLUME_LIQUIDITY_EVIDENCE_FAMILIES_PRESENT")
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
    return _criterion("PASS", "NO_UPBIT_MARKET_EVENT_CAUTION_ACTIVE")


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
    if not isinstance(evaluation_as_of, str) or not _DATE_RE.fullmatch(evaluation_as_of):
        raise CryptoCandidatePromotionError("EVALUATION_AS_OF_INVALID")
    if not isinstance(universe_packet, dict) or universe_packet.get("schema_version") != UPBIT_UNIVERSE.OUTPUT_SCHEMA_VERSION:
        raise CryptoCandidatePromotionError("UNIVERSE_PACKET_SCHEMA_MISMATCH")
    try:
        REGIME_OUTPUT_CONTRACT.validate_output(regime_payload)
    except REGIME_OUTPUT_CONTRACT.OutputContractError as exc:
        raise CryptoCandidatePromotionError(f"REGIME_PAYLOAD_INVALID:{exc}") from exc

    market_evidence_by_market = market_evidence_by_market or {}

    rows = []
    for row in universe_packet.get("markets", []):
        if row.get("state") not in (UPBIT_UNIVERSE.STATE_TRADEABLE_UNIVERSE, UPBIT_UNIVERSE.STATE_PAPER_ELIGIBLE):
            continue
        rows.append(
            evaluate_candidate(
                row,
                regime_payload=regime_payload,
                market_evidence_packet=market_evidence_by_market.get(row["market"]),
                leadership_output=leadership_output,
            )
        )

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "evaluation_as_of": evaluation_as_of,
        "universe_snapshot_date": universe_packet.get("snapshot_date"),
        "universe_manifest_sha256": universe_packet.get("manifest_sha256"),
        "regime_contract_version": regime_payload.get("contract_version"),
        "regime_generated_at": regime_payload.get("generated_at"),
        "leadership_as_of_date": (leadership_output or {}).get("as_of_date"),
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
