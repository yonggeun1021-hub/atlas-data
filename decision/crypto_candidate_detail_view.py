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
# P3-12-GOV-03A: the structured, self-hash-bound identity/taxonomy freeze
# registry. A frozen tuple must block this read-model regardless of whether
# the CURRENTLY OBSERVED bytes happen to match, differ from, or have been
# restored back to the frozen value -- see
# governance/upbit_identity_taxonomy_governance_freeze.py's module
# docstring and ``_bound_universe_source()``/``_universe_packet_is_frozen()``
# below.
GOVERNANCE_FREEZE = _load(
    "crypto_candidate_detail_view_governance_freeze",
    "governance/upbit_identity_taxonomy_governance_freeze.py",
)
GOVERNANCE_FROZEN_REASON = "IDENTITY_TAXONOMY_PENDING_GOVERNANCE_RESOLUTION"


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


def _path_is_frozen(relative_path: str, *, declared_sha256: str | None = None) -> bool:
    """True iff ``relative_path`` has an exact, registered freeze-registry
    entry matching ANY of: ``declared_sha256`` (a caller-supplied pinned/
    expected hash -- checked even if the actual current file no longer has
    those bytes, or never did), the CURRENT file's own raw byte hash, the
    current file's own embedded record ``payload_sha256``, or its nested
    ``packet.payload_sha256`` (checked even if those differ from
    ``declared_sha256`` or from each other).

    This is P3-12-GOV-03A's core fix (item C): a freeze must never depend
    on first observing a byte MISMATCH. In this specific incident the
    frozen universe packet's bytes were never reverted, so a "check only on
    mismatch" design (the defect this function replaces) would never even
    fire -- every one of these hash identities is checked unconditionally,
    every time, regardless of whether any of them currently agree with each
    other.
    """
    if declared_sha256 is not None and GOVERNANCE_FREEZE.is_frozen(relative_path, file_sha256=declared_sha256):
        return True
    absolute = (ROOT / relative_path).resolve()
    try:
        absolute.relative_to(ROOT.resolve())
    except ValueError:
        return False
    if not absolute.is_file():
        return False
    actual_file_hash = hashlib.sha256(absolute.read_bytes()).hexdigest()
    if GOVERNANCE_FREEZE.is_frozen(relative_path, file_sha256=actual_file_hash):
        return True
    try:
        record = DECISION_SNAPSHOT._read_json(absolute)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return False
    packet = record.get("packet") if isinstance(record, dict) else None
    return GOVERNANCE_FREEZE.is_frozen(
        relative_path,
        record_payload_sha256=record.get("payload_sha256") if isinstance(record, dict) else None,
        inner_packet_sha256=(packet or {}).get("payload_sha256") if isinstance(packet, dict) else None,
    )


def _decision_references_frozen_universe(decision_record: dict) -> bool:
    """True iff this decision record's own ``upbit_tradeable_universe_packet``
    source_ref -- by its declared path+hash alone, never requiring a
    successful bind first -- is governance-frozen. A malformed/ambiguous
    ref is not this function's concern (the normal bind path raises its own
    specific error for that); this only answers "is the thing it POINTS AT
    frozen," which must be knowable even when the ref currently resolves
    cleanly with no byte mismatch at all.
    """
    matches = [
        row for row in decision_record.get("source_refs") or []
        if row.get("role") == "upbit_tradeable_universe_packet"
    ]
    if len(matches) != 1:
        return False
    relative = matches[0].get("path")
    expected_sha = matches[0].get("sha256")
    if not isinstance(relative, str) or not isinstance(expected_sha, str) or not SHA256_RE.fullmatch(expected_sha):
        return False
    return _path_is_frozen(relative, declared_sha256=expected_sha)


def find_latest_decision_snapshot(root: Path = DECISION_SNAPSHOT_ROOT) -> dict | None:
    """Latest verified decision packet by its internal UTC timestamp.

    Directory names are checked against the signed-in-payload time basis and
    never used as the selection authority. A decision packet whose universe
    source_ref points at exact, registered-frozen lineage (P3-12-GOV-03A) is
    EXCLUDED from selection here -- never deleted, never modified on disk,
    simply never eligible to be "latest" while that lineage stays frozen,
    regardless of whether its reference currently resolves with a byte
    match or a mismatch.
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
    candidates = [row for row in candidates if not _decision_references_frozen_universe(row["record"])]
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
    explicit = decision_packet_path is not None

    # P3-12-GOV-03A item D.2: an EXPLICITLY named decision packet whose
    # universe lineage is governance-frozen must fail closed here -- never
    # silently fall back to something else -- checked BEFORE attempting any
    # bind, so this fires even when the ref currently resolves with a clean
    # byte match (the defect this whole mechanism exists to close).
    if isinstance(decision_record, dict) and _decision_references_frozen_universe(decision_record):
        if explicit:
            _fail("DECISION_SOURCE_GOVERNANCE_FROZEN", str(decision_packet_path))
        # Auto-latest should never reach here -- find_latest_decision_snapshot()
        # already excludes frozen-lineage candidates -- but if it somehow
        # does (e.g. a caller passes a hand-picked decision_snapshot_root),
        # never inherit its P5 evaluation: fall back exactly as if no
        # decision snapshot existed at all.
        decision_record = None

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

    # P3-12-GOV-03A item D.1/D.4: the universe packet ULTIMATELY selected --
    # whether bound from a decision snapshot's source_ref or obtained via
    # the plain "latest committed" fallback -- is checked for governance
    # freeze independently. In this incident the frozen universe packet's
    # bytes were never reverted, so even the fallback path returns exactly
    # the frozen content; this is what actually drives the funnel down to
    # zero, not merely excluding one stale decision snapshot.
    universe_path = universe_entry.get("path")
    universe_relative = None
    if universe_path is not None:
        try:
            universe_relative = str(Path(universe_path).resolve().relative_to(ROOT.resolve()))
        except ValueError:
            universe_relative = None
    universe_frozen = universe_relative is not None and _path_is_frozen(universe_relative)
    if universe_frozen:
        if explicit and bound_universe is not None:
            _fail("DECISION_SOURCE_GOVERNANCE_FROZEN", str(decision_packet_path))
        decision_record = None

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
        effective_row = universe_row
        if universe_frozen and universe_row.get("state") != "OBSERVATION_POOL":
            # Identity/taxonomy authority is frozen -- this market can never
            # be shown as anything but OBSERVATION_POOL right now, no matter
            # what state the (frozen) committed packet itself declares.
            effective_row = dict(universe_row)
            effective_row["state"] = "OBSERVATION_POOL"
            effective_row["reason"] = GOVERNANCE_FROZEN_REASON
            effective_row["candidate_canonical_asset_id"] = None
        candidates.append(
            _candidate_row(
                effective_row,
                None if universe_frozen else decision_by_market.get(market),
                None if universe_frozen else _market_evidence_packet_for(market, market_evidence_entry),
            )
        )

    funnel_counts = (
        decision_record.get("funnel_counts")
        if isinstance(decision_record, dict) and not universe_frozen
        else None
    )
    if funnel_counts is None:
        if universe_frozen:
            funnel_counts = {
                "observation_pool_count": len(candidates),
                "tradeable_universe_count": 0,
                "focused_review_count": 0,
                "paper_ready_count": 0,
            }
        else:
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


def main(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--decision-packet", type=Path)
    parser.add_argument("--generated-at")
    args = parser.parse_args(argv)
    try:
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
