#!/usr/bin/env python3
"""CIO item 4 (2026-08-29): portal-consumable Crypto candidate detail view.

Pure composition/derivation over already-committed evidence -- no new
capture, no new authority, no new threshold. For every market in the P3-12
Observation Pool it assembles, from whatever real evidence already exists
for that specific market:

* identity                (P3-12 ``candidate_canonical_asset_id``)
* funnel stage + exact blocker reason
                           (P3-12 ``state``/``reason``, refined by P5-08/
                            P5-09's own ``state``/``reason`` for whichever
                            markets a committed
                            ``decision/crypto_paper_decision_snapshot.py``
                            packet already evaluated)
* trend direction fact     (P5-08's own ``TREND`` criterion, reused verbatim
                            from the committed decision-snapshot candidate
                            row -- never recomputed here)
* relative strength vs BTC (P5-08's own ``RELATIVE_STRENGTH`` criterion,
                            same reuse)
* liquidity (spread/depth/turnover)
                           (P3-12's own ``trailing_30d_krw_turnover`` for
                            every market, plus P4-07's own orderbook
                            spread/depth/slippage evidence for whichever
                            markets a committed
                            ``data/observations/upbit_market_evidence``
                            packet already covers)
* trigger/invalidation-price prerequisites
                           (P5-09's own ``TRIGGER_TIMEFRAME_ALIGNMENT`` /
                            ``ORDER_DRAFT_COMPLETE`` criteria and
                            ``order_draft``, same verbatim reuse -- a
                            ``None``/absent trigger or invalidation price
                            here means P5-09 itself never computed one from
                            real ratified evidence, not that this module
                            declined to show one)

and a ``blocker_summary`` aggregating, in one place, exactly why today's
total candidate counts are what they are -- so a portal user can see "why 0
candidates" (or whatever the real current counts are) without re-deriving
the funnel by hand.

This module never fabricates a price, trend, relative-strength, liquidity,
trigger, or invalidation-price fact that an upstream module did not already
compute from real ratified evidence. A field the upstream evidence leaves
UNKNOWN/absent stays UNKNOWN/absent here -- it is never defaulted, guessed,
or backfilled. It grants no decision, action, order, Production, or trading
authority; every ``*_authorized`` field below stays ``false``.

Separately, and only when a caller explicitly asks for it,
``build_enriched_trend_view`` (CLI ``--enriched-trend``) layers PR603's
numeric trend calculations on top of an unchanged view of one immutable
decision -- see ``docs/crypto_candidate_detail_trend_calculations_contract.md``.
That path requires the decision packet, the evaluation date and a complete
per-market calculation contract from the caller, reads P4-07 evidence only
through that decision's own hash-verified source reference, and adds its
numbers beside -- never inside -- the existing criteria, funnel, blocker,
count, trigger and authority fields. Not requesting it changes nothing: the
default ``build_view`` result and the default CLI output are byte-identical
to what they were before it existed.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
UNIVERSE_DATA_ROOT = ROOT / "data" / "observations" / "upbit_tradeable_universe"
MARKET_EVIDENCE_DATA_ROOT = ROOT / "data" / "observations" / "upbit_market_evidence"
DECISION_SNAPSHOT_ROOT = ROOT / "evidence" / "crypto_paper_decision"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HHMM_RE = re.compile(r"^\d{4}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
SCHEMA_VERSION = 1
CONTRACT_VERSION = "crypto_candidate_detail_view/v1"
FUNNEL_STAGES = (
    "OBSERVATION_POOL", "TRADEABLE_UNIVERSE", "FOCUSED_REVIEW",
    "PAPER_BUY_ELIGIBLE",
)

# --- Explicitly requested, versioned trend-calculation enrichment ----------
# Everything below this line is reachable only through
# ``build_enriched_trend_view``/``--enriched-trend``. The default
# ``build_view`` result and the default CLI output are unchanged by it.
ENRICHED_SCHEMA_VERSION = 1
ENRICHED_CONTRACT_VERSION = "crypto_candidate_detail_view_enriched_trend/v1"
TREND_CALCULATION_SOURCE_ROLE = "upbit_market_evidence_packet"

TREND_STATUS_CALCULATED = "CALCULATED"
TREND_STATUS_UNAVAILABLE = "UNAVAILABLE"
TREND_STATUS_NOT_REQUESTED = "NOT_REQUESTED"
TREND_STATUSES = (TREND_STATUS_CALCULATED, TREND_STATUS_UNAVAILABLE, TREND_STATUS_NOT_REQUESTED)

# A market the caller did not ask about is NOT_REQUESTED -- explicitly
# distinct from UNAVAILABLE, which means the caller did ask and the
# decision-bound P4-07 evidence cannot answer.
REASON_NOT_REQUESTED = "TREND_CONTRACT_NOT_REQUESTED"
REASON_NO_BOUND_SOURCE = "NO_DECISION_BOUND_P4_MARKET_EVIDENCE_SOURCE"
REASON_NO_BOUND_PACKET = "NO_DECISION_BOUND_P4_MARKET_EVIDENCE_PACKET_FOR_MARKET"

_ENRICHED_KEYS = {
    "schema_version", "contract_version", "base_contract_version",
    "evaluation_as_of", "decision_source", "trend_calculation_source",
    "requested_markets", "trend_calculations", "view", "authority",
    "payload_sha256",
}
_TREND_OBSERVATION_KEYS = {
    "market", "status", "reasons", "calculation_contract_sha256", "metrics",
}


class CryptoCandidateDetailViewError(ValueError):
    """Fail-closed candidate-detail composition violation."""


def _fail(code: str, detail: str) -> None:
    raise CryptoCandidateDetailViewError(f"{code}:{detail}")


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        _fail("MODULE_LOAD_FAILED", relative_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The decision-snapshot module already contains the exact "find the latest
# committed P3-12 / P4-07 packet" discovery helpers this view needs -- reused
# verbatim rather than re-implemented, so there is exactly one definition of
# "which committed dated directory counts as latest" in this repository.
DECISION_SNAPSHOT = _load(
    "crypto_candidate_detail_view_decision_snapshot",
    "decision/crypto_paper_decision_snapshot.py",
)

_TREND_METRICS = None


def _trend_metrics_module():
    """PR603's calculator, loaded only when enrichment is actually requested.

    Deliberately lazy: the default view must keep working -- and keep costing
    the same -- in a checkout where the P4-07 ratified policy files that
    calculator validates against are not present. Nothing in the default
    ``build_view``/CLI path reaches this.
    """
    global _TREND_METRICS
    if _TREND_METRICS is None:
        _TREND_METRICS = _load(
            "crypto_candidate_detail_view_trend_metrics",
            "universe/crypto_candidate_trend_metrics.py",
        )
    return _TREND_METRICS


def _relative_or_absolute(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _authority_block() -> dict:
    return {
        "identity_authorized": False,
        "funnel_promotion_authorized": False,
        "trigger_authorized": False,
        "invalidation_price_authorized": False,
        "decision_eligible": False,
        "action_eligible": False,
        "order_eligible": False,
        "production_authorized": False,
        "trading_authorized": False,
    }


def _verified_decision_entry(candidate: Path) -> dict:
    try:
        generation_dir, hhmm_dir, date_dir = candidate.parent, candidate.parent.parent, candidate.parent.parent.parent
        if not DATE_RE.fullmatch(date_dir.name) or not HHMM_RE.fullmatch(hhmm_dir.name):
            _fail("DECISION_PATH_TIME_BASIS_INVALID", str(candidate))
        if not SHA256_RE.fullmatch(generation_dir.name):
            _fail("DECISION_PATH_GENERATION_INVALID", str(candidate))
        record = DECISION_SNAPSHOT._read_json(candidate)
        internal = record.get("captured_at_utc") or record.get("generated_at")
        if not isinstance(internal, str) or not UTC_RE.fullmatch(internal):
            _fail("DECISION_INTERNAL_TIMESTAMP_INVALID", str(candidate))
        internal_date, internal_hhmm = internal[:10], internal[11:13] + internal[14:16]
        if (internal_date, internal_hhmm) != (date_dir.name, hhmm_dir.name):
            _fail("DECISION_PATH_INTERNAL_TIMESTAMP_MISMATCH", str(candidate))
        if record.get("generation_id") != generation_dir.name:
            _fail("DECISION_GENERATION_ID_MISMATCH", str(candidate))
        unsigned = dict(record)
        expected = unsigned.pop("payload_sha256", None)
        if not isinstance(expected, str) or payload_sha256(unsigned) != expected:
            _fail("DECISION_PAYLOAD_SHA256_MISMATCH", str(candidate))
        parsed = dt.datetime.strptime(internal, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
        return {
            "date": date_dir.name,
            "hhmm": hhmm_dir.name,
            "generation_id": generation_dir.name,
            "path": candidate,
            "record": record,
            "captured_at": parsed,
        }
    except CryptoCandidateDetailViewError:
        raise
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        _fail("DECISION_PACKET_INVALID", f"{candidate}:{exc}")


def find_latest_decision_snapshot(root: Path = DECISION_SNAPSHOT_ROOT) -> dict | None:
    """Latest verified decision packet by its internal UTC timestamp.

    Directory names are checked against the signed-in-payload time basis and
    never used as the selection authority.
    """
    root = Path(root)
    if not root.is_dir():
        return None
    candidates = []
    for candidate in root.glob("*/*/*/packet.json"):
        try:
            candidates.append(_verified_decision_entry(candidate))
        except CryptoCandidateDetailViewError as exc:
            print(f"TAMPER_OR_DRIFT:{exc}", file=__import__("sys").stderr)
    if not candidates:
        return None
    candidates.sort(key=lambda row: row["captured_at"])
    latest_at = candidates[-1]["captured_at"]
    latest = [row for row in candidates if row["captured_at"] == latest_at]
    if len({row["generation_id"] for row in latest}) != 1:
        _fail("DECISION_LATEST_TIMESTAMP_AMBIGUOUS", latest_at.isoformat())
    return latest[-1]


def _market_evidence_packet_for(market: str, market_evidence_entry: dict | None) -> dict | None:
    if market_evidence_entry is None:
        return None
    record = market_evidence_entry.get("record")
    if not isinstance(record, dict):
        return None
    packets = record.get("packets")
    if not isinstance(packets, dict):
        return None
    packet = packets.get(market)
    return packet if isinstance(packet, dict) else None


def _source_entry_from_decision(decision_record: dict, role: str) -> dict | None:
    matches = [row for row in decision_record.get("source_refs") or [] if row.get("role") == role]
    if not matches:
        return None
    if len(matches) != 1:
        _fail("DECISION_SOURCE_ROLE_AMBIGUOUS", role)
    ref = matches[0]
    relative = ref.get("path")
    expected_sha = ref.get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
        _fail("DECISION_SOURCE_REF_INVALID", role)
    path = (ROOT / relative).resolve()
    try:
        path.relative_to(ROOT.resolve())
    except ValueError:
        _fail("DECISION_SOURCE_PATH_ESCAPE", relative)
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha:
        _fail("DECISION_SOURCE_BYTES_MISMATCH", relative)
    record = DECISION_SNAPSHOT._read_json(path)
    date = path.parent.name
    if role == "upbit_tradeable_universe_packet":
        packet = record.get("packet") if isinstance(record, dict) else None
        identity = decision_record.get("upbit_universe_snapshot_identity") or {}
        if not isinstance(packet, dict) or identity.get("date") != date or identity.get("payload_sha256") != packet.get("payload_sha256"):
            _fail("DECISION_UNIVERSE_IDENTITY_MISMATCH", relative)
        return {"date": date, "path": path, "record": record, "packet": packet}
    return {"date": date, "path": path, "record": record}


def _is_superseded_by_ratified_universe(path: Path, decision_record: dict) -> bool:
    """Return true only for a valid, authority-free ratified replacement.

    P3-12 permits one same-raw-vintage UNRATIFIED->RATIFIED reclassification.
    That intentionally replaces the date packet and makes a previously
    retained decision's path hash stale.  A current detail view must not bind
    that historical decision to different bytes; it falls back to the newly
    verified P3 packet with no inherited P5 evaluation instead.
    """
    try:
        record = DECISION_SNAPSHOT._read_json(path)
        unsigned_record = copy.deepcopy(record)
        claimed_record_hash = unsigned_record.pop("payload_sha256", None)
        packet = record.get("packet")
        unsigned_packet = copy.deepcopy(packet)
        claimed_packet_hash = unsigned_packet.pop("payload_sha256", None)
        identity = decision_record.get("upbit_universe_snapshot_identity") or {}
        return bool(
            isinstance(packet, dict)
            and claimed_record_hash == payload_sha256(unsigned_record)
            and claimed_packet_hash == payload_sha256(unsigned_packet)
            and record.get("ratification", {}).get("effective_for_snapshot") is True
            and packet.get("policy_ratified") is True
            and packet.get("taxonomy_ratified") is True
            and identity.get("date") == path.parent.name
            and identity.get("payload_sha256") != claimed_packet_hash
            and all(value is False for value in (packet.get("authority") or {}).values())
            and all(
                value is False
                for key, value in (record.get("authority") or {}).items()
                if key != "observation_pool_population_only"
            )
        )
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _price_fact(market_evidence_packet: dict | None) -> dict:
    """Real, already-finalized daily close from P4-07's own committed
    candle evidence -- never a live/current-candle price. ``None``
    whenever P4-07 has no committed packet, or no finalized daily candle,
    for this specific market; never defaulted to another timeframe or
    guessed."""
    if market_evidence_packet is None:
        return {"latest_finalized_close": None, "as_of": None, "source": None}
    daily = (market_evidence_packet.get("candles") or {}).get("1d") or {}
    finalized = daily.get("finalized_candles")
    if not isinstance(finalized, list) or not finalized:
        return {"latest_finalized_close": None, "as_of": None, "source": None}
    latest = sorted(finalized, key=lambda row: row.get("close_time") or "")[-1]
    return {
        "latest_finalized_close": latest.get("trade_price"),
        "as_of": latest.get("close_time"),
        "source": "microstructure/upbit_market_evidence.py:1d_finalized_candle",
    }


def _liquidity_fact(
    turnover: object, market_evidence_packet: dict | None
) -> dict:
    """P3-12's own trailing_30d_krw_turnover is available for every market
    in the Observation Pool (computed before any identity/taxonomy gate).
    spread/depth/slippage are P4-07 evidence and stay None until a
    committed P4-07 packet actually covers this market."""
    orderbook = (market_evidence_packet or {}).get("orderbook")
    return {
        "trailing_30d_krw_turnover": turnover,
        "spread_bps": (orderbook or {}).get("spread_bps"),
        "spread_status": (orderbook or {}).get("spread_status"),
        "depth": (orderbook or {}).get("depth"),
        "slippage_bps": (orderbook or {}).get("slippage_bps"),
        "slippage_status": (orderbook or {}).get("slippage_status"),
    }


def _funnel_stage(p3_12_state: str, decision_state: str | None) -> str:
    if p3_12_state in ("OBSERVATION_POOL", "BLOCKED"):
        return "OBSERVATION_POOL"
    if decision_state is None:
        # Reached P3-12's TRADEABLE_UNIVERSE/PAPER_ELIGIBLE floor but this
        # committed decision snapshot never evaluated it with P5-08 (e.g.
        # no committed decision-snapshot packet exists at all yet).
        return "TRADEABLE_UNIVERSE"
    if decision_state == "PAPER_BUY_ELIGIBLE":
        return "PAPER_BUY_ELIGIBLE"
    if decision_state in ("FOCUSED_REVIEW", "WAIT"):
        return "FOCUSED_REVIEW"
    # WATCH / BLOCKED (P5-08/P5-09's own terminal non-advancing states)
    return "TRADEABLE_UNIVERSE"


def _candidate_row(
    universe_row: dict,
    decision_row: dict | None,
    market_evidence_packet: dict | None,
) -> dict:
    p3_12_state = universe_row["state"]
    p3_12_reason = universe_row["reason"]
    if decision_row is not None:
        detailed_state = decision_row["state"]
        blocker_reason = decision_row["reason"]
    else:
        detailed_state = p3_12_state
        blocker_reason = p3_12_reason
    funnel_stage = _funnel_stage(p3_12_state, decision_row["state"] if decision_row else None)

    trend = None
    relative_strength = None
    trigger_timeframe_alignment = None
    order_draft_complete = None
    order_draft = None
    if decision_row is not None:
        p5_08 = decision_row.get("p5_08") or {}
        criteria_08 = p5_08.get("criteria") or {}
        trend = criteria_08.get("TREND")
        relative_strength = criteria_08.get("RELATIVE_STRENGTH")
        p5_09 = decision_row.get("p5_09")
        if p5_09 is not None:
            criteria_09 = p5_09.get("criteria") or {}
            trigger_timeframe_alignment = criteria_09.get("TRIGGER_TIMEFRAME_ALIGNMENT")
            order_draft_complete = criteria_09.get("ORDER_DRAFT_COMPLETE")
            order_draft = p5_09.get("order_draft")

    return {
        "market": universe_row["market"],
        "canonical_asset_id": universe_row.get("candidate_canonical_asset_id"),
        "funnel_stage": funnel_stage,
        "detailed_state": detailed_state,
        "blocker_reason": blocker_reason,
        "market_event": {
            "warning": universe_row.get("market_event_warning"),
            "caution_any": universe_row.get("market_event_caution_any"),
        },
        "price": _price_fact(market_evidence_packet),
        "trend": copy.deepcopy(trend),
        "relative_strength": copy.deepcopy(relative_strength),
        "liquidity": _liquidity_fact(
            universe_row.get("trailing_30d_krw_turnover"), market_evidence_packet
        ),
        "trigger_prerequisites": {
            "trigger_timeframe_alignment": copy.deepcopy(trigger_timeframe_alignment),
            "order_draft_complete": copy.deepcopy(order_draft_complete),
            "order_draft": copy.deepcopy(order_draft),
        },
        "evaluated_by_p5_08": decision_row is not None,
        "evaluated_by_p5_09": bool(decision_row and decision_row.get("p5_09") is not None),
        "authority": _authority_block(),
    }


def _blocker_summary(candidates: list[dict], total: int) -> dict:
    by_stage: dict[str, int] = {stage: 0 for stage in FUNNEL_STAGES}
    by_reason: dict[str, int] = {}
    for row in candidates:
        by_stage[row["funnel_stage"]] = by_stage.get(row["funnel_stage"], 0) + 1
        reason = row["blocker_reason"] or "UNKNOWN"
        by_reason[reason] = by_reason.get(reason, 0) + 1

    if total == 0:
        narrative = "NO_MARKETS_IN_OBSERVATION_POOL"
    else:
        reason_parts = ", ".join(
            f"{count} {reason}"
            for reason, count in sorted(by_reason.items(), key=lambda item: (-item[1], item[0]))
        )
        stage_parts = ", ".join(
            f"{stage}={by_stage[stage]}" for stage in FUNNEL_STAGES
        )
        narrative = (
            f"{total}/{total} markets by funnel stage ({stage_parts}); "
            f"blocker reasons: {reason_parts}"
        )
    return {
        "total_markets": total,
        "by_funnel_stage": by_stage,
        "by_blocker_reason": dict(
            sorted(by_reason.items(), key=lambda item: (-item[1], item[0]))
        ),
        "narrative": narrative,
    }


def build_view(
    *,
    universe_data_root: Path = UNIVERSE_DATA_ROOT,
    market_evidence_data_root: Path = MARKET_EVIDENCE_DATA_ROOT,
    decision_snapshot_root: Path = DECISION_SNAPSHOT_ROOT,
    decision_packet_path: Path | None = None,
    generated_at: str | None = None,
) -> dict:
    decision_entry = (
        _verified_decision_entry(Path(decision_packet_path))
        if decision_packet_path is not None
        else find_latest_decision_snapshot(decision_snapshot_root)
    )
    decision_record = decision_entry["record"] if decision_entry else None
    bound_universe = None
    if isinstance(decision_record, dict):
        try:
            bound_universe = _source_entry_from_decision(
                decision_record, "upbit_tradeable_universe_packet",
            )
        except CryptoCandidateDetailViewError as exc:
            refs = [
                row for row in decision_record.get("source_refs") or []
                if row.get("role") == "upbit_tradeable_universe_packet"
            ]
            source_path = (
                (ROOT / refs[0]["path"]).resolve()
                if len(refs) == 1 and isinstance(refs[0].get("path"), str)
                else None
            )
            if (
                "DECISION_SOURCE_BYTES_MISMATCH" not in str(exc)
                or source_path is None
                or not _is_superseded_by_ratified_universe(source_path, decision_record)
            ):
                raise
            decision_record = None
    if decision_packet_path is not None and bound_universe is None:
        _fail("DECISION_UNIVERSE_SOURCE_REF_MISSING", str(decision_packet_path))
    universe_entry = bound_universe or DECISION_SNAPSHOT.find_latest_universe_packet(universe_data_root)
    if universe_entry is None:
        _fail("UNIVERSE_PACKET_MISSING", str(universe_data_root))
    universe_packet = universe_entry["packet"]
    if not isinstance(universe_packet, dict):
        _fail("UNIVERSE_PACKET_INVALID", universe_entry["date"])

    bound_market_evidence = (
        _source_entry_from_decision(decision_record, "upbit_market_evidence_packet")
        if isinstance(decision_record, dict) else None
    )
    market_evidence_entry = bound_market_evidence or DECISION_SNAPSHOT.find_latest_market_evidence_packet(market_evidence_data_root)
    decision_by_market: dict[str, dict] = {}
    if isinstance(decision_record, dict):
        for row in decision_record.get("candidates") or []:
            decision_by_market[row["market"]] = row

    candidates = []
    for universe_row in sorted(universe_packet.get("markets") or [], key=lambda row: row["market"]):
        market = universe_row["market"]
        candidates.append(
            _candidate_row(
                universe_row,
                decision_by_market.get(market),
                _market_evidence_packet_for(market, market_evidence_entry),
            )
        )

    funnel_counts = (
        decision_record.get("funnel_counts")
        if isinstance(decision_record, dict)
        else None
    )
    if funnel_counts is None:
        summary = universe_packet.get("summary") or {}
        funnel_counts = {
            "observation_pool_count": summary.get("observation_pool_count"),
            "tradeable_universe_count": (
                (summary.get("tradeable_universe_count") or 0)
                + (summary.get("paper_eligible_count") or 0)
            ),
            "focused_review_count": None,
            "paper_ready_count": None,
        }

    if generated_at is None:
        generated_at = (
            (decision_entry["record"].get("captured_at_utc") or decision_entry["record"].get("generated_at"))
            if decision_entry
            else dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        )

    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "universe_snapshot_date": universe_entry["date"],
        "universe_payload_sha256": universe_packet.get("payload_sha256"),
        "market_evidence_snapshot_date": (
            market_evidence_entry["date"] if market_evidence_entry else None
        ),
        "decision_snapshot": (
            {
                "date": decision_entry["date"],
                "hhmm": decision_entry["hhmm"],
                "generation_id": decision_entry["generation_id"],
                "path": _relative_or_absolute(decision_entry["path"]),
            }
            if decision_entry
            else None
        ),
        "funnel_counts": funnel_counts,
        "candidates": candidates,
        "blocker_summary": _blocker_summary(candidates, len(candidates)),
        "authority": _authority_block(),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


# ---------------------------------------------------------------------------
# Explicitly requested trend-calculation enrichment
#
# This is an additive, separately versioned read model layered *on top of* an
# unchanged ``build_view`` result. It answers one question the default view
# cannot: for the exact decision that was already taken, what were the actual
# numeric trend observations over that decision's own hash-bound P4-07
# evidence?
#
# It never chooses inputs. The decision packet, the evaluation date and the
# per-market calculation contracts are all required from the caller, and the
# P4-07 evidence is taken only from that decision's own verified source
# reference -- never from the latest committed packet, and never from a
# default parameter set. It grants no authority: the numbers here are
# arithmetic about candles, and the candidate criteria, funnel stages,
# blocker reasons, counts, triggers and authority flags of the embedded view
# are reproduced byte-for-byte from the default build.
# ---------------------------------------------------------------------------

def _enriched_authority_block() -> dict:
    block = _authority_block()
    block["calculation_only"] = True
    block["trend_calculation_policy_ratified"] = False
    block["trend_rule_authorized"] = False
    return block


def _require_enriched_evaluation_as_of(value, decision_date: str) -> str:
    """The caller's own original evaluation date, never derived from a source.

    Rejected when malformed, or when it post-dates the decision being
    explained -- a decision cannot have been taken as of a later day.
    """
    if not isinstance(value, str) or not DATE_RE.fullmatch(value):
        _fail("ENRICHED_EVALUATION_AS_OF_INVALID", repr(value))
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        _fail("ENRICHED_EVALUATION_AS_OF_INVALID", value)
    if parsed > dt.datetime.strptime(decision_date, "%Y-%m-%d").date():
        _fail("ENRICHED_EVALUATION_AS_OF_FUTURE", f"{value}>{decision_date}")
    return value


def _require_trend_calculation_contracts(contracts, known_markets: set) -> dict:
    """Complete, caller-supplied, per-market calculation contracts.

    A contract naming a market this decision's own universe never carried is
    rejected rather than ignored: silently dropping it would let a caller
    believe a market was calculated when nothing was.
    """
    if not isinstance(contracts, dict):
        _fail("ENRICHED_TREND_CONTRACTS_INVALID", type(contracts).__name__)
    for market in contracts:
        if not isinstance(market, str) or not market:
            _fail("ENRICHED_TREND_CONTRACT_MARKET_INVALID", repr(market))
    normalized = {}
    trend = _trend_metrics_module()
    for market in sorted(contracts):
        if market not in known_markets:
            _fail("ENRICHED_TREND_CONTRACT_MARKET_UNKNOWN", market)
        # Validated up front, so a malformed contract is rejected even when
        # this market has no P4 evidence to calculate over.
        try:
            trend.validate_calculation_contract(contracts[market])
        except trend.CryptoCandidateTrendMetricsError as exc:
            _fail("ENRICHED_TREND_CONTRACT_INVALID", f"{market}:{exc}")
        normalized[market] = copy.deepcopy(contracts[market])
    return normalized


def _trend_observation(
    market: str,
    contract,
    evidence_packet,
    *,
    evaluation_as_of: str,
    has_bound_source: bool,
) -> dict:
    if contract is None:
        return {
            "market": market,
            "status": TREND_STATUS_NOT_REQUESTED,
            "reasons": [REASON_NOT_REQUESTED],
            "calculation_contract_sha256": None,
            "metrics": None,
        }
    trend = _trend_metrics_module()
    contract_sha256 = trend.payload_sha256(trend.validate_calculation_contract(contract))
    if not has_bound_source or evidence_packet is None:
        # Asked for, but this decision's own bound P4-07 evidence does not
        # cover it. Never backfilled from a newer or unrelated packet.
        return {
            "market": market,
            "status": TREND_STATUS_UNAVAILABLE,
            "reasons": [REASON_NO_BOUND_SOURCE if not has_bound_source else REASON_NO_BOUND_PACKET],
            "calculation_contract_sha256": contract_sha256,
            "metrics": None,
        }
    try:
        # PR603's calculator, unchanged: it re-validates the contract, the
        # packet schema/identity/hash/authority and the packet's own time
        # basis against ``evaluation_as_of``, and reports healthy timeframes
        # even when the other timeframe is UNAVAILABLE.
        metrics = trend.build_trend_metrics(
            evidence_packet,
            market=market,
            evaluation_as_of=evaluation_as_of,
            calculation_contract=contract,
        )
    except trend.CryptoCandidateTrendMetricsError as exc:
        # Malformed, future-dated or identity-mismatched source: a corrupt
        # input, not a coverage gap, so it must not soften into UNAVAILABLE.
        _fail("ENRICHED_TREND_CALCULATION_REJECTED", f"{market}:{exc}")
    return {
        "market": market,
        "status": metrics["status"],
        "reasons": list(metrics["unavailable_reasons"]),
        "calculation_contract_sha256": metrics["calculation_contract_sha256"],
        "metrics": metrics,
    }


def build_enriched_trend_view(
    *,
    decision_packet_path: Path,
    evaluation_as_of: str,
    trend_calculation_contracts,
    universe_data_root: Path = UNIVERSE_DATA_ROOT,
    market_evidence_data_root: Path = MARKET_EVIDENCE_DATA_ROOT,
    generated_at: str | None = None,
) -> dict:
    """Default view plus numeric trend observations for one immutable decision.

    Every input is explicit and required; there is no latest-source fallback
    and no default calculation parameter anywhere on this path.
    """
    if decision_packet_path is None:
        _fail("ENRICHED_DECISION_PACKET_REQUIRED", "decision_packet_path")
    decision_entry = _verified_decision_entry(Path(decision_packet_path))
    evaluation_as_of = _require_enriched_evaluation_as_of(evaluation_as_of, decision_entry["date"])

    # The embedded view is produced by the unchanged default builder with the
    # same explicit decision, so its candidate criteria, funnel stages,
    # blocker reasons, counts, triggers and authority are exactly what the
    # default path emits.
    view = build_view(
        universe_data_root=universe_data_root,
        market_evidence_data_root=market_evidence_data_root,
        decision_packet_path=Path(decision_packet_path),
        generated_at=generated_at,
    )
    known_markets = {row["market"] for row in view["candidates"]}
    contracts = _require_trend_calculation_contracts(trend_calculation_contracts, known_markets)

    # Enrichment reads P4-07 evidence ONLY through this decision's own
    # hash-verified source reference. Unlike ``build_view``, there is
    # deliberately no ``find_latest_market_evidence_packet`` fallback here: an
    # old decision must never be explained with newer numbers.
    bound_evidence = _source_entry_from_decision(
        decision_entry["record"], TREND_CALCULATION_SOURCE_ROLE,
    )
    trend_calculation_source = None
    if bound_evidence is not None:
        trend_calculation_source = {
            "role": TREND_CALCULATION_SOURCE_ROLE,
            "path": _relative_or_absolute(bound_evidence["path"]),
            "sha256": hashlib.sha256(bound_evidence["path"].read_bytes()).hexdigest(),
            "snapshot_date": bound_evidence["date"],
        }

    trend_calculations = {}
    for market in sorted(known_markets):
        trend_calculations[market] = _trend_observation(
            market,
            contracts.get(market),
            _market_evidence_packet_for(market, bound_evidence),
            evaluation_as_of=evaluation_as_of,
            has_bound_source=bound_evidence is not None,
        )

    packet = {
        "schema_version": ENRICHED_SCHEMA_VERSION,
        "contract_version": ENRICHED_CONTRACT_VERSION,
        "base_contract_version": CONTRACT_VERSION,
        "evaluation_as_of": evaluation_as_of,
        "decision_source": {
            "path": _relative_or_absolute(decision_entry["path"]),
            "date": decision_entry["date"],
            "hhmm": decision_entry["hhmm"],
            "generation_id": decision_entry["generation_id"],
            "payload_sha256": decision_entry["record"]["payload_sha256"],
        },
        "trend_calculation_source": trend_calculation_source,
        "requested_markets": sorted(contracts),
        "trend_calculations": trend_calculations,
        "view": view,
        "authority": _enriched_authority_block(),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_enriched_trend_view(
    enriched,
    *,
    decision_packet_path: Path,
    evaluation_as_of: str,
    trend_calculation_contracts,
    universe_data_root: Path = UNIVERSE_DATA_ROOT,
    market_evidence_data_root: Path = MARKET_EVIDENCE_DATA_ROOT,
    generated_at: str | None = None,
) -> dict:
    """Re-derive the enrichment from independently supplied originals.

    The decision packet, evaluation date and calculation contracts are taken
    from this call's own arguments and from nowhere else: nothing is read back
    out of ``enriched`` to decide what ``enriched`` should have been. A caller
    who edits a metric and recomputes ``payload_sha256`` therefore still fails,
    because the whole packet is rebuilt from the original sources and compared
    byte-for-byte.
    """
    if not isinstance(enriched, dict) or set(enriched) != _ENRICHED_KEYS:
        _fail("ENRICHED_SCHEMA_MISMATCH", "keys")
    if enriched["schema_version"] != ENRICHED_SCHEMA_VERSION:
        _fail("ENRICHED_SCHEMA_VERSION_MISMATCH", str(enriched["schema_version"]))
    if enriched["contract_version"] != ENRICHED_CONTRACT_VERSION:
        _fail("ENRICHED_CONTRACT_VERSION_MISMATCH", str(enriched["contract_version"]))
    authority = enriched["authority"]
    if not isinstance(authority, dict) or set(authority) != set(_enriched_authority_block()):
        _fail("ENRICHED_AUTHORITY_KEYS_INVALID", "authority")
    for key, value in sorted(authority.items()):
        if value is not (key == "calculation_only"):
            _fail("ENRICHED_AUTHORITY_INVALID", key)

    claimed = enriched["payload_sha256"]
    if not isinstance(claimed, str) or not SHA256_RE.fullmatch(claimed):
        _fail("ENRICHED_PAYLOAD_SHA256_INVALID", str(claimed))
    unsigned = copy.deepcopy(enriched)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != claimed:
        _fail("ENRICHED_PAYLOAD_SHA256_MISMATCH", claimed)

    rebuilt = build_enriched_trend_view(
        decision_packet_path=decision_packet_path,
        evaluation_as_of=evaluation_as_of,
        trend_calculation_contracts=trend_calculation_contracts,
        universe_data_root=universe_data_root,
        market_evidence_data_root=market_evidence_data_root,
        generated_at=generated_at,
    )
    if canonical_json(rebuilt) != canonical_json(enriched):
        _fail("ENRICHED_DERIVATION_MISMATCH", enriched["evaluation_as_of"])

    # One more independent pass: every embedded metric is re-derived by the
    # calculator's own validator from its own embedded, hash-pinned source.
    trend = _trend_metrics_module()
    for market, observation in sorted(enriched["trend_calculations"].items()):
        if not isinstance(observation, dict) or set(observation) != _TREND_OBSERVATION_KEYS:
            _fail("ENRICHED_TREND_OBSERVATION_SCHEMA_MISMATCH", market)
        if observation["status"] not in TREND_STATUSES:
            _fail("ENRICHED_TREND_OBSERVATION_STATUS_INVALID", market)
        if observation["metrics"] is None:
            continue
        try:
            trend.validate_trend_metrics(observation["metrics"])
        except trend.CryptoCandidateTrendMetricsError as exc:
            _fail("ENRICHED_TREND_METRICS_INVALID", f"{market}:{exc}")
    return copy.deepcopy(enriched)


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--decision-packet", type=Path)
    parser.add_argument("--generated-at")
    # Opt-in only. Absent these flags the invocation, and its output bytes,
    # are exactly what they were before this capability existed.
    parser.add_argument("--enriched-trend", action="store_true")
    parser.add_argument("--evaluation-as-of")
    parser.add_argument("--trend-contracts", type=Path)
    args = parser.parse_args(argv)

    enriched_only = {
        "--evaluation-as-of": args.evaluation_as_of is not None,
        "--trend-contracts": args.trend_contracts is not None,
    }
    if args.enriched_trend:
        # A partial explicit argument set is rejected rather than completed
        # with a chosen default.
        missing = sorted(name for name, present in enriched_only.items() if not present)
        if args.decision_packet is None:
            missing.append("--decision-packet")
        if missing:
            parser.error("--enriched-trend requires " + ", ".join(sorted(missing)))
        if args.output_root is not None:
            parser.error("--enriched-trend and --output-root are mutually exclusive")
    elif any(enriched_only.values()):
        supplied = sorted(name for name, present in enriched_only.items() if present)
        parser.error(", ".join(supplied) + " requires --enriched-trend")

    contracts = None
    if args.enriched_trend:
        # Read separately, so the default path's exception behaviour below is
        # exactly what it was before this capability existed.
        try:
            contracts = json.loads(args.trend_contracts.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            import sys

            print(f"TREND_CONTRACTS_UNREADABLE:{exc}", file=sys.stderr)
            return 1

    try:
        if args.enriched_trend:
            result = build_enriched_trend_view(
                decision_packet_path=args.decision_packet,
                evaluation_as_of=args.evaluation_as_of,
                trend_calculation_contracts=contracts,
                generated_at=args.generated_at,
            )
        else:
            result = build_view(generated_at=args.generated_at, decision_packet_path=args.decision_packet)
    except CryptoCandidateDetailViewError as exc:
        import sys

        print(exc, file=sys.stderr)
        return 1
    if args.out is not None and args.output_root is not None:
        parser.error("--out and --output-root are mutually exclusive")
    if args.output_root is not None:
        decision = result.get("decision_snapshot")
        if not isinstance(decision, dict):
            print("DECISION_PACKET_REQUIRED_FOR_APPEND_ONLY_OUTPUT", file=__import__("sys").stderr)
            return 1
        args.out = (
            args.output_root / decision["date"] / decision["hhmm"] /
            decision["generation_id"] / "packet.json"
        )
    if args.out is None:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        rendered = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        if args.out.exists() and args.out.read_text(encoding="utf-8") != rendered:
            print(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{args.out}", file=__import__("sys").stderr)
            return 1
        args.out.write_text(rendered, encoding="utf-8")
        print(str(args.out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
