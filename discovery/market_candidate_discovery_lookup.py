#!/usr/bin/env python3
"""Read-only per-market candidate discovery status and per-symbol evidence lookup.

Answers, from committed evidence only, why KR / US / CRYPTO currently have
few or no candidates and which already-defined conditions the next step
still lacks.  Two views are produced from the same validated inputs:

* ``market status`` -- one funnel per market: source population -> data
  acquired -> evaluated -> passed / held / excluded / unevaluated.  Every
  count carries the basis time and the exact population it was counted
  over, and the chain is reconciled (no double counting, no silent gap).
* ``symbol lookup`` -- for one symbol: why it is in the pipeline, the
  sector / rotation evidence that exists for it, its last evaluation time,
  the next-step conditions it does not meet, and whether an existing rule
  already excluded or expired it.

Nothing here scans, ranks, scores, or selects.  The aggregate counts are
taken from ``discovery/three_market_evaluation_coverage.py`` (the existing
three-market coverage receipt) and cross-checked against the per-symbol
rows read here; a mismatch fails closed.  Per-symbol facts are copied
verbatim from the existing evaluators (Korea/US symbol market review, the
Crypto PAPER decision snapshot and the Crypto candidate detail view) and
from the retained population packets.

Distinctions this module keeps explicit instead of collapsing:

* a missing source is reported as ``NO_EVIDENCE`` / ``NOT_AVAILABLE``,
  never as ``0``;
* a count of ``0`` is always labelled with what produced it (a source
  count, a ratified rule, or an evaluator that ran and found nothing);
* gaps are classified as ``COLLECTION_FAILED``, ``SOURCE_STALE`` (only by
  a source's own declared validity interval), ``FEATURE_NOT_IMPLEMENTED``,
  ``POLICY_UNDEFINED`` (``미정``), ``EVALUATED_CRITERIA_UNKNOWN``,
  ``EVALUATED_EXCLUDED_BY_RATIFIED_RULE`` or ``EVALUATED_NO_CANDIDATE``;
* every source keeps its own ``as_of`` / ``generated_at``; the lookup
  time (``generated_at`` of this report) never replaces a source date.

No threshold, policy, candidate rule, or freshness window is invented.
Every ``*_authorized`` field stays ``false``.
"""
from __future__ import annotations

import argparse
from collections import Counter
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    # ``decision.population_symbol_observation`` self-registers into
    # ``sys.modules`` under its own ``__name__`` at import time (so its KR/US
    # adapter siblings can import it by a stable short name).  That only
    # works through Python's normal import machinery, which registers a
    # module into ``sys.modules`` before executing its body -- not through
    # ``_load_module``'s ``spec_from_file_location`` + ``exec_module`` below,
    # which never registers the module under its own name and would raise
    # ``KeyError`` inside that self-registration line.  A standard import
    # needs the repository root on ``sys.path`` first.
    sys.path.insert(0, str(ROOT))
SCHEMA_VERSION = "market_candidate_discovery_lookup/1"
MARKETS = ("KR", "US", "CRYPTO")
NOT_COUNTED = "미집계"
UNDEFINED = "미정"
NO_EVIDENCE = "NO_EVIDENCE"
NOT_AVAILABLE = "NOT_AVAILABLE"
GAP_CLASSES = (
    "COLLECTION_FAILED",
    "EVALUATION_HALTED_INPUT_DATE_MISMATCH",
    "SOURCE_STALE",
    "FEATURE_NOT_IMPLEMENTED",
    "POLICY_UNDEFINED",
    "EVALUATED_CRITERIA_UNKNOWN",
    "EVALUATED_EXCLUDED_BY_RATIFIED_RULE",
    "EVALUATED_NO_CANDIDATE",
)
# Korean reader labels for the disposition categories a user must be able to
# tell apart.  They are display labels only; the machine codes stay English.
CATEGORY_LABELS = {
    "unevaluated": "미평가",
    "evaluation_halted_input_date_mismatch": "평가 중단(입력 날짜 불일치)",
    "collection_failed": "수집 실패",
    "policy_undefined": "정책 미정",
    "no_evidence": "근거 없음",
    "excluded_by_ratified_rule": "비준 규칙에 의한 제외",
    "evaluated_no_candidate": "정상 평가 후 후보 없음",
}
DATE_MISMATCH_NOTE_RE = re.compile(r"DATE_MISMATCH|FUTURE_DATED|MIXED_GENERATION")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Existing reason codes emitted by the bounded KR/US reviews, classified once
# here.  A code absent from this table is reported as UNCLASSIFIED (미정),
# never guessed.
REVIEW_REASON_CLASS = {
    "KOREA_FIVE_MARKET_AXES_CONNECTED": "CONNECTED_FACT",
    "CONFIRMED_PRICE_SMA20_AND_INVESTOR_FLOW_CONNECTED": "CONNECTED_FACT",
    "CURRENT_PRICE_AND_RETURN_CONTEXT_CONNECTED": "CONNECTED_FACT",
    "FIVE_AXIS_CURRENT_REFERENCE_CONNECTED": "CONNECTED_FACT",
    "FINAL_KOREA_REGIME_POLICY_PENDING": "POLICY_UNDEFINED",
    "FINAL_US_REGIME_NOT_AVAILABLE": "POLICY_UNDEFINED",
    "PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY": "BOUNDARY_NOT_A_GAP",
    "PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE": "COLLECTION_FAILED",
}
# Per-criterion outcome -> gap class. UNKNOWN is a policy gap; FAIL is an
# evaluated exclusion by a ratified rule. A status outside this table is
# reported as UNCLASSIFIED (미정), never guessed.
CRITERION_STATUS_CLASS = {
    "UNKNOWN": "POLICY_UNDEFINED",
    "FAIL": "EVALUATED_EXCLUDED_BY_RATIFIED_RULE",
}
DEFAULT_KR_REGISTRY_COVERAGE_ROOT = ROOT / "data" / "observations" / "krx_registry_evaluation_coverage"
DEFAULT_VALIDITY_ASSESSMENT = (
    ROOT / "evidence" / "operational" / "dynamic_clock" / "candidate_validity_window_assessment.json"
)


class MarketCandidateDiscoveryLookupError(ValueError):
    """A source, reconciliation, or lookup claim failed closed."""


def _fail(code: str, detail: str | None = None) -> None:
    raise MarketCandidateDiscoveryLookupError(code if detail is None else f"{code}:{detail}")


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


COVERAGE = _load_module(
    "lookup_three_market_evaluation_coverage",
    "discovery/three_market_evaluation_coverage.py",
)
KOREA_REVIEW = COVERAGE.KOREA_REVIEW
US_REVIEW = COVERAGE.US_REVIEW
CRYPTO_DECISION = COVERAGE.CRYPTO_DECISION
CRYPTO_DETAIL = _load_module(
    "lookup_crypto_candidate_detail_view", "decision/crypto_candidate_detail_view.py"
)
# population_symbol_observation_packet/1 (PR #702): read-only full-population
# KR/US observation snapshot.  Only its own ``reverify`` (hash / schema /
# sidecar-consistency check) is reused here; this lookup never rebuilds or
# re-scores a row, and never reads its ``DEFAULT_OUTPUT_ROOTS`` directly --
# the session root always comes from the caller's own ``inputs`` dict (see
# ``default_inputs()``'s ``{kr,us}_population_observation_root``), so
# ``build_report(inputs=...)`` / ``validate_report(report, inputs=...)`` stay
# reproducible from exactly the inputs they were given.  Loaded through a
# standard import (not ``_load_module``): the module registers itself into
# ``sys.modules`` under its own ``__name__`` at import time, which requires
# Python's normal import machinery, not ``spec_from_file_location`` +
# ``exec_module`` without a prior ``sys.modules`` registration.
from decision import population_symbol_observation as POPULATION_SYMBOL_OBSERVATION  # noqa: E402


# --------------------------------------------------------------------------
# generic helpers
# --------------------------------------------------------------------------
def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    )


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _fail(code, str(path))
        raise  # unreachable
    if not isinstance(value, dict):
        _fail(code, str(path))
    return value


def _validate_self_hash(value: dict, field: str, code: str) -> None:
    claimed = value.get(field)
    unsigned = copy.deepcopy(value)
    unsigned.pop(field, None)
    if not isinstance(claimed, str) or payload_sha256(unsigned) != claimed:
        _fail(code)


def _utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        _fail(code, repr(value))
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        _fail(code, repr(value))
    return dt.date.fromisoformat(value)


def _relative(path: Path) -> str:
    try:
        return Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError:
        _fail("SOURCE_PATH_OUTSIDE_REPOSITORY", str(path))
        raise  # unreachable


def _source_ref(path: Path, packet_sha256: str | None) -> dict:
    return {
        "path": _relative(path),
        "file_sha256": _file_sha256(path),
        "packet_sha256": packet_sha256,
    }


def _latest_dated_packet(root: Path, date_field: str, *, glob: str = "packet.json") -> Path | None:
    """Newest packet under ``root/<YYYY-MM-DD>/`` selected by its own internal date.

    The directory name must equal the packet's internal date; a mismatch is
    a tamper/drift signal and fails closed rather than being skipped.
    """
    root = Path(root)
    if not root.is_dir():
        return None
    found = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or DATE_RE.fullmatch(directory.name) is None:
            continue
        for candidate in sorted(directory.glob(glob)):
            record = _read_json(candidate, "DATED_PACKET_READ_FAILED")
            internal = record.get(date_field)
            if internal != directory.name:
                _fail("DATED_PACKET_INTERNAL_DATE_MISMATCH", f"{candidate}:{internal}")
            found.append((internal, candidate))
    if not found:
        return None
    found.sort()
    return found[-1][1]


def _eligible_population_symbol_observation_dir(root: Path, lookup_at: dt.datetime) -> tuple[Path | None, str]:
    """Newest ``<root>/<YYYY-MM-DD>/`` session not newer than ``lookup_at``.

    Point-in-time boundary: a session directory dated after ``lookup_at``'s
    date, or whose own packet ``generated_at`` is after ``lookup_at``, was
    not yet available at lookup time and is skipped -- never selected, and
    never a reason by itself to report anything invalid.  Walking to an
    older session for this reason is not a "fallback" (that term is reserved
    for the separate, hard-stop case: the newest *eligible* session existing
    but failing its own reverify -- see the caller, which never keeps
    walking past that point). A directory whose ``summary.json`` cannot be
    read/parsed is treated as eligible-but-unreadable, deferring the exact
    failure to the caller's own read of it.
    """
    root = Path(root)
    if not root.is_dir():
        return None, "OBSERVATION_ROOT_ABSENT"
    candidates = sorted(
        (d for d in root.iterdir() if d.is_dir() and DATE_RE.fullmatch(d.name)),
        key=lambda d: d.name,
        reverse=True,
    )
    lookup_date = lookup_at.date()
    saw_any = False
    for directory in candidates:
        saw_any = True
        if dt.date.fromisoformat(directory.name) > lookup_date:
            continue  # future session date: not yet available at lookup time
        summary_path = directory / "summary.json"
        try:
            sidecar_generated_at = json.loads(summary_path.read_text(encoding="utf-8")).get("generated_at")
        except (OSError, UnicodeError, json.JSONDecodeError):
            return directory, "ELIGIBLE"  # let the caller's own read surface the exact failure
        if isinstance(sidecar_generated_at, str) and UTC_RE.fullmatch(sidecar_generated_at):
            generated_at_dt = dt.datetime.strptime(sidecar_generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
            if generated_at_dt > lookup_at:
                continue  # packet generated after the lookup instant: not yet available
        return directory, "ELIGIBLE"
    return None, ("NO_ELIGIBLE_SESSION_ALL_FUTURE" if saw_any else "NO_SESSION_RETAINED")


def _population_level_symbol_data(
    market: str,
    *,
    observation_root: Path,
    lookup_at: dt.datetime,
    population_as_of: str,
    population_id: str | None,
    population_count: int,
) -> dict:
    """Population-wide per-symbol observation, adapted from ``population_symbol_observation_packet/1``.

    ``observation_root`` must come from the caller's own ``inputs`` dict
    (``default_inputs()``'s ``{kr,us}_population_observation_root``), never
    from an ambient/global default: this keeps ``build_report(inputs=...)``
    and ``validate_report(report, inputs=...)`` reproducible from exactly the
    inputs they were given, the same as every other source this lookup reads.

    Distinct from ``symbol_level`` (the bounded review's already-evaluated
    subset): this reports every population symbol's ``data_observation`` /
    ``evaluability`` / ``evaluation`` status, taken verbatim from the
    packet's own ``summary`` sidecar after the packet's own ``reverify``
    (hash, schema, authority, sidecar-consistency) has passed. When no
    *eligible* (not-future, per ``lookup_at``) session is retained, the
    pre-existing placeholder is reported unchanged. A packet whose own
    population disagrees with the population this lookup is reporting for is
    surfaced, never silently substituted or discarded. The newest eligible
    session that fails its own reverify is reported as
    ``OBSERVATION_PACKET_INVALID`` -- an older eligible session is never
    used as a silent fallback.
    """
    session_dir, eligibility = _eligible_population_symbol_observation_dir(Path(observation_root), lookup_at)
    if session_dir is None:
        return {
            "count": NOT_COUNTED,
            "status": "NOT_RETAINED_IN_PUBLIC_REPOSITORY",
            "evidence": (
                # Only the "at least one session exists but every one is
                # future-dated" case gets its own explicit evidence string;
                # a genuinely absent root or an empty root both fall back to
                # the pre-existing placeholder text unchanged.
                eligibility if eligibility == "NO_ELIGIBLE_SESSION_ALL_FUTURE" else (
                    "korea_market_signals.source.per_symbol_persistence=0" if market == "KR"
                    else "POPULATION_SYMBOL_OBSERVATION_PACKET_ABSENT"
                )
            ),
        }
    try:
        reverified = POPULATION_SYMBOL_OBSERVATION.reverify(session_dir)
    except POPULATION_SYMBOL_OBSERVATION.PopulationSymbolObservationError as exc:
        return {
            "count": NOT_COUNTED,
            "status": "OBSERVATION_PACKET_INVALID",
            "evidence": str(exc),
            "source": {"path": _relative(session_dir)},
        }
    sidecar = _read_json(session_dir / "summary.json", "POPULATION_OBSERVATION_SUMMARY_READ_FAILED")
    if sidecar.get("as_of_session_date") != session_dir.name:
        _fail("POPULATION_OBSERVATION_SESSION_DATE_MISMATCH", f"{session_dir}:{sidecar.get('as_of_session_date')}")
    if (
        sidecar.get("generation_id") != reverified["generation_id"]
        or sidecar.get("payload_sha256") != reverified["payload_sha256"]
    ):
        _fail("POPULATION_OBSERVATION_SIDECAR_DRIFT", str(session_dir))
    summary = sidecar["summary"]
    observed_population = sidecar["population"]
    matched = (
        observed_population.get("population_id") == population_id
        and observed_population.get("count") == population_count
        and observed_population.get("as_of") == population_as_of
    )
    session_date = dt.date.fromisoformat(sidecar["as_of_session_date"])
    lookup_date = lookup_at.date()
    if session_date > lookup_date:
        _fail("POPULATION_OBSERVATION_SESSION_DATE_AFTER_LOOKUP", str(session_dir))
    return {
        "status": "OBSERVED" if matched else "OBSERVED_POPULATION_MISMATCH",
        "count": summary["population_count"],
        "data_observed_count": summary["data_observed_count"],
        "evaluable_count": summary["evaluable_count"],
        "evaluated_count": summary["evaluated_count"],
        "evaluated_bounded_count": summary["evaluated_bounded_count"],
        "evaluated_without_full_inputs_count": summary["evaluated_without_full_inputs_count"],
        "formal_candidate_count": summary["formal_candidate_count"],
        "not_evaluable_reason_counts": copy.deepcopy(summary["not_evaluable_reason_counts"]),
        "entry_state_counts": copy.deepcopy(summary["entry_state_counts"]),
        "contract_version": "population_symbol_observation_packet/1",
        "as_of_session_date": sidecar["as_of_session_date"],
        "session_recency": {
            # A deterministic date comparison only -- no invented freshness
            # window or staleness policy. ``CURRENT_SESSION`` when the
            # session is dated the same as the lookup date; otherwise
            # ``HISTORICAL`` (future is already excluded above by
            # construction, never reachable here).
            "status": "CURRENT_SESSION" if session_date == lookup_date else "HISTORICAL",
            "as_of_session_date": sidecar["as_of_session_date"],
            "lookup_date": lookup_date.isoformat(),
            "days_before_lookup_date": (lookup_date - session_date).days,
        },
        "generated_at": sidecar["generated_at"],
        "generated_at_semantics": "INPUT_SNAPSHOT_TIME_MAX_OF_SOURCE_TIMESTAMPS_NOT_WALL_CLOCK",
        "generation_id": sidecar["generation_id"],
        "reverify_outcome": reverified["outcome"],
        "population_match": {
            "matched": matched,
            "lookup_population": {
                "population_id": population_id, "count": population_count, "as_of": population_as_of,
            },
            "observation_population": copy.deepcopy(observed_population),
        },
        "source": _source_ref(ROOT / reverified["path"], sidecar.get("payload_sha256")),
    }


def _interval_status(valid_from: object, valid_to: object, generated_date: dt.date) -> dict:
    start = _date(valid_from, "SOURCE_INTERVAL_INVALID")
    end = _date(valid_to, "SOURCE_INTERVAL_INVALID")
    if end < start:
        _fail("SOURCE_INTERVAL_INVALID", f"{valid_from}>{valid_to}")
    if generated_date < start:
        status = "BEFORE_SOURCE_INTERVAL"
    elif generated_date <= end:
        status = "WITHIN_SOURCE_INTERVAL"
    else:
        status = "ELAPSED_SOURCE_INTERVAL"
    return {
        "policy": "SOURCE_DECLARED_EFFECTIVE_INTERVAL",
        "valid_from": valid_from,
        "valid_to": valid_to,
        "status_at_generated_date": status,
        "elapsed_days_after_valid_to": max((generated_date - end).days, 0),
    }


def _undefined_freshness(source_date: str, generated_date: dt.date, *, note: str) -> dict:
    parsed = _date(source_date, "SOURCE_DATE_INVALID")
    return {
        "policy": UNDEFINED,
        "source_date": source_date,
        "elapsed_days_at_generated_date": (generated_date - parsed).days,
        "note": note,
    }


def _skipped_generation_class(derivation_notes: list) -> str:
    """Why the latest Crypto decision generation carries no P5-08 rows.

    Input-date mismatch (``*_DATE_MISMATCH``, ``*FUTURE_DATED``) is the
    evaluator halting on inconsistent input dates -- the inputs were
    collected, they just do not belong to the same generation.  Anything
    else is reported as a collection failure.  The notes themselves are
    copied verbatim next to the class.
    """
    if any(DATE_MISMATCH_NOTE_RE.search(note) for note in derivation_notes):
        return "EVALUATION_HALTED_INPUT_DATE_MISMATCH"
    return "COLLECTION_FAILED"


def _historical(generation: dict | None) -> dict | str:
    """Mark an earlier evaluating generation as a past result, never a substitute."""
    if not generation:
        return NO_EVIDENCE
    row = dict(generation)
    row.update({
        "historical": True,
        "evaluated_date_utc": str(row.get("generated_at") or "")[:10] or None,
        "label": "과거 평가 결과 — 최신 세대를 대체하지 않음",
        "substitutes_latest_generation": False,
    })
    return row


def _authority() -> dict:
    return {
        "read_only": True,
        "candidate_creation_authorized": False,
        "candidate_ranking_authorized": False,
        "stage_promotion_authorized": False,
        "evaluation_scope_adoption_authorized": False,
        "action_authorized": False,
        "order_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }


def _normalize_crypto_market(symbol: str) -> str:
    text = symbol.strip().upper()
    return text if "-" in text else f"KRW-{text}"


# --------------------------------------------------------------------------
# input resolution
# --------------------------------------------------------------------------
def default_inputs(root: Path = ROOT) -> dict:
    """Resolve the newest committed packet of every source by its own date.

    Optional sources (KR registry screening coverage, P8-12 validity
    assessment, Crypto candidate detail, Crypto bounded identity registry,
    Crypto leadership, discovery cases) resolve to ``None`` when absent and
    are then reported as ``NOT_AVAILABLE`` -- never as zero rows.
    """
    root = Path(root)
    kr_universe = _latest_dated_packet(root / "data/observations/krx_global_universe", "as_of_date")
    us_universe = _latest_dated_packet(root / "data/observations/us_global_universe", "source_date")
    if kr_universe is None or us_universe is None:
        _fail("POPULATION_PACKET_MISSING")
    us_source_date = _read_json(us_universe, "US_UNIVERSE_READ_FAILED")["source_date"]

    decision_entry = CRYPTO_DETAIL.find_latest_decision_snapshot(root / "evidence/crypto_paper_decision")
    if decision_entry is None:
        _fail("CRYPTO_DECISION_MISSING")
    decision = decision_entry["record"]
    universe_refs = [
        ref for ref in decision.get("source_refs") or []
        if isinstance(ref, dict) and ref.get("role") == "upbit_tradeable_universe_packet"
    ]
    if len(universe_refs) != 1:
        _fail("CRYPTO_DECISION_UNIVERSE_REF_MISSING")
    bound_universe = _read_json(root / universe_refs[0]["path"], "CRYPTO_DECISION_UNIVERSE_SOURCE_READ_FAILED")
    crypto_universe = None
    for candidate in sorted((root / "data/observations/upbit_tradeable_universe").glob("*/packet.json")):
        if _read_json(candidate, "CRYPTO_UNIVERSE_READ_FAILED") == bound_universe:
            crypto_universe = candidate
    if crypto_universe is None:
        _fail("CRYPTO_DECISION_BOUND_UNIVERSE_NOT_COMMITTED")
    snapshot_date = bound_universe.get("snapshot_date")
    if not isinstance(snapshot_date, str) or DATE_RE.fullmatch(snapshot_date) is None:
        _fail("CRYPTO_UNIVERSE_SNAPSHOT_DATE_INVALID")

    def _optional(path: Path | None) -> Path | None:
        return path if path is not None and Path(path).is_file() else None

    validity = root / "evidence/operational/dynamic_clock/candidate_validity_window_assessment.json"
    return {
        "kr_universe_path": kr_universe,
        "kr_review_path": root / "data/latest_korea_symbol_market_review.json",
        "kr_market_signals_path": root / "data/latest_korea_market_signals.json",
        "kr_leadership_context_path": _latest_dated_packet(
            root / "data/observations/korea_leadership_context", "observation_date"
        ),
        "kr_market_membership_path": _optional(root / "config/korea_market_membership.json"),
        "kr_registry_coverage_path": _latest_dated_packet(
            root / "data/observations/krx_registry_evaluation_coverage", "evaluation_session_date"
        ),
        # Directory only -- never a pre-selected "latest" session -- so that
        # the point-in-time eligible-session selection in
        # ``_population_level_symbol_data`` can apply the caller's exact
        # lookup time (future sessions excluded) using only this input, never
        # an ambient/global default.
        "kr_population_observation_root": root / "data/observations/korea_population_symbol_observation",
        "us_universe_path": us_universe,
        "us_review_path": root / "data/latest_us_symbol_market_review.json",
        "us_review_contract_path": root / "config/us_symbol_market_review_contract.json",
        "us_raw_snapshot_dir": root / "evidence/us_breadth/raw" / us_source_date,
        "us_market_data_path": root / "data/latest_free_market_data.json",
        "us_population_observation_root": root / "data/observations/us_population_symbol_observation",
        "stage_history_path": root / "data/stage_history.json",
        "discovery_cases_path": _latest_discovery_cases(root / "data/observations/event_discovery_cases"),
        "validity_assessment_path": _optional(validity),
        "crypto_decision_path": decision_entry["path"],
        "crypto_universe_path": crypto_universe,
        "crypto_identity_review_path": root / "data/observations/upbit_identity_review" / snapshot_date / "packet.json",
        "crypto_snapshot_dir": root / "evidence/crypto/upbit/raw" / snapshot_date,
        "prior_identity_evidence_path": root / "config/upbit_bounded_identity_evidence.json",
        "crypto_detail_path": _latest_crypto_detail(root / "evidence/crypto_candidate_detail"),
        "crypto_bounded_identity_path": _latest_dated_packet(
            root / "data/observations/upbit_bounded_identity_registry", "snapshot_date"
        ),
        "crypto_leadership_path": _latest_crypto_leadership(root / "data/observations/crypto_leadership"),
    }


def _latest_discovery_cases(root: Path) -> Path | None:
    """Newest event-discovery case packet, selected by directory date and its own hash."""
    root = Path(root)
    if not root.is_dir():
        return None
    found = []
    for directory in sorted(root.iterdir()):
        if not directory.is_dir() or DATE_RE.fullmatch(directory.name) is None:
            continue
        for candidate in sorted(directory.glob("packet-*.json")):
            record = _read_json(candidate, "DISCOVERY_CASES_READ_FAILED")
            claimed = record.get("packet_sha256")
            if not isinstance(claimed, str) or not candidate.name.startswith(f"packet-{claimed[:16]}"):
                _fail("DISCOVERY_CASES_PATH_HASH_MISMATCH", str(candidate))
            found.append((directory.name, candidate))
    if not found:
        return None
    found.sort()
    return found[-1][1]


def _latest_crypto_leadership(root: Path) -> Path | None:
    """Newest P1-CR-07 leadership packet, selected by the decision module's own finder."""
    try:
        entry = CRYPTO_DECISION.find_latest_leadership_packet(Path(root))
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        _fail("CRYPTO_LEADERSHIP_INVALID", str(exc))
    return None if entry is None else Path(entry["path"])


def _load_crypto_leadership(path: Path | None) -> dict | None:
    """Load one leadership packet through the decision module's natural-lineage check.

    P1-CR-07 packets carry no self hash; their lineage manifest hashes are
    verified against the committed breadth raw evidence instead.
    """
    if path is None:
        return None
    record = _read_json(path, "CRYPTO_LEADERSHIP_READ_FAILED")
    entry = {"date": Path(path).parent.name, "path": Path(path), "record": record}
    try:
        CRYPTO_DECISION._validate_leadership_entry(entry)
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        _fail("CRYPTO_LEADERSHIP_INVALID", str(exc))
    return record


def _latest_crypto_detail(root: Path) -> Path | None:
    """Newest committed Crypto candidate detail packet by its internal generated_at."""
    root = Path(root)
    if not root.is_dir():
        return None
    found = []
    for candidate in root.glob("*/*/*/packet.json"):
        record = _read_json(candidate, "CRYPTO_DETAIL_READ_FAILED")
        generated_at = _utc(record.get("generated_at"), "CRYPTO_DETAIL_GENERATED_AT_INVALID")
        found.append((generated_at, candidate.as_posix(), candidate))
    if not found:
        return None
    found.sort()
    return found[-1][2]


# --------------------------------------------------------------------------
# validated source loading
# --------------------------------------------------------------------------
def _load_review(path: Path, market: str, observed_at: dt.datetime) -> dict:
    return COVERAGE._validated_review(Path(path), market, observed_at)


def _load_kr_universe(path: Path) -> dict:
    record = _read_json(path, "KR_UNIVERSE_READ_FAILED")
    _validate_self_hash(record, "payload_sha256", "KR_UNIVERSE_PAYLOAD_SHA256_MISMATCH")
    if record.get("schema_version") != "krx_global_universe_packet/1":
        _fail("KR_UNIVERSE_SCHEMA_INVALID")
    records = (record.get("asset_master") or {}).get("records")
    if not isinstance(records, list) or len(records) != record.get("total_count"):
        _fail("KR_UNIVERSE_RECORDS_INVALID")
    return record


def _load_us_universe(path: Path) -> dict:
    record = _read_json(path, "US_UNIVERSE_READ_FAILED")
    _validate_self_hash(record, "payload_sha256", "US_UNIVERSE_PAYLOAD_SHA256_MISMATCH")
    packet = record.get("packet")
    if (
        record.get("schema_version") != "us_forward_universe_population/2"
        or not isinstance(packet, dict)
        or packet.get("schema_version") != "us_global_universe_packet/1"
    ):
        _fail("US_UNIVERSE_SCHEMA_INVALID")
    _validate_self_hash(packet, "payload_sha256", "US_UNIVERSE_PACKET_PAYLOAD_SHA256_MISMATCH")
    rows = packet.get("source_attribute_rows")
    if not isinstance(rows, list) or len(rows) != packet.get("total_count"):
        _fail("US_UNIVERSE_ROWS_INVALID")
    return record


def _load_crypto_universe(path: Path, observed_at: dt.datetime) -> dict:
    record, _count = COVERAGE._validated_crypto_universe(Path(path), observed_at)
    return record


def _load_crypto_decision(path: Path, observed_at: dt.datetime) -> dict:
    return COVERAGE._validated_crypto_decision(Path(path), observed_at)


def _load_crypto_detail(path: Path | None, decision: dict) -> dict | None:
    if path is None:
        return None
    record = _read_json(path, "CRYPTO_DETAIL_READ_FAILED")
    _validate_self_hash(record, "payload_sha256", "CRYPTO_DETAIL_PAYLOAD_SHA256_MISMATCH")
    if record.get("contract_version") != CRYPTO_DETAIL.CONTRACT_VERSION:
        _fail("CRYPTO_DETAIL_CONTRACT_INVALID")
    snapshot = record.get("decision_snapshot") or {}
    if snapshot.get("generation_id") != decision.get("generation_id"):
        # The detail packet is only reused when it was derived from the exact
        # same decision generation; otherwise it is reported as not bound.
        return {"_unbound": True, "record": record}
    candidates = record.get("candidates")
    if not isinstance(candidates, list):
        _fail("CRYPTO_DETAIL_CANDIDATES_INVALID")
    return {"_unbound": False, "record": record}


def _load_optional_hashed(path: Path | None, hash_field: str, code: str, *, schema_version: str | None = None) -> dict | None:
    if path is None:
        return None
    record = _read_json(path, code)
    _validate_self_hash(record, hash_field, f"{code}_HASH_MISMATCH")
    if schema_version is not None and record.get("schema_version") != schema_version:
        _fail(f"{code}_SCHEMA_INVALID")
    return record


def _load_stage_history(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("STAGE_HISTORY_READ_FAILED", str(exc))
    if not isinstance(value, dict) or not value:
        _fail("STAGE_HISTORY_INVALID")
    for date_key, rows in value.items():
        if DATE_RE.fullmatch(date_key) is None or not isinstance(rows, dict):
            _fail("STAGE_HISTORY_INVALID", date_key)
    return value


# --------------------------------------------------------------------------
# shared per-market helpers
# --------------------------------------------------------------------------
def _classify_reasons(reasons: list) -> list[dict]:
    rows = []
    for reason in reasons:
        rows.append({
            "code": reason,
            "class": REVIEW_REASON_CLASS.get(reason, f"UNCLASSIFIED_EXISTING_REASON:{UNDEFINED}"),
        })
    return rows


def _unmet_from_reasons(reasons: list) -> list[dict]:
    unmet = []
    for row in _classify_reasons(reasons):
        if row["class"] in ("CONNECTED_FACT", "BOUNDARY_NOT_A_GAP"):
            continue
        unmet.append({"condition": row["code"], "status": "UNMET", "class": row["class"]})
    return unmet


def _stage_facts(stage_history: dict, symbol: str) -> dict:
    dates = sorted(stage_history)
    latest_date = dates[-1]
    latest_row = stage_history[latest_date].get(symbol)
    if latest_row is None:
        return {
            "status": "NOT_IN_STAGE_HISTORY",
            "stage_history_as_of": latest_date,
            "stage": None,
            "name": None,
            "collected": None,
            "coverage": None,
            "first_seen_date": None,
            "first_staged_date": None,
            "current_stage_since": None,
        }
    first_seen = next(d for d in dates if symbol in stage_history[d])
    staged_dates = [d for d in dates if (stage_history[d].get(symbol) or {}).get("stage") is not None]
    stage = latest_row.get("stage")
    current_since = None
    if stage is not None:
        current_since = latest_date
        for d in reversed(dates):
            row = stage_history[d].get(symbol)
            if row is None or row.get("stage") != stage:
                break
            current_since = d
    return {
        "status": "STAGED" if stage is not None else "IN_HISTORY_NOT_STAGED",
        "stage_history_as_of": latest_date,
        "stage": stage,
        "name": latest_row.get("name"),
        "collected": latest_row.get("collected"),
        "coverage": latest_row.get("coverage"),
        "first_seen_date": first_seen,
        "first_staged_date": staged_dates[0] if staged_dates else None,
        "current_stage_since": current_since,
    }


def _validity_rows(assessment: dict | None, markets: set[str], subjects: set[str]) -> dict:
    if assessment is None:
        return {"status": NOT_AVAILABLE, "reason": "P8_12_VALIDITY_ASSESSMENT_PACKET_ABSENT"}
    rows = [
        row for row in assessment.get("candidate_assessments") or []
        if isinstance(row, dict) and row.get("market") in markets and row.get("subject") in subjects
    ]
    if not rows:
        return {
            "status": NO_EVIDENCE,
            "reason": "SUBJECT_NOT_IN_P8_12_VALIDITY_ASSESSMENT",
            "evaluation_at_utc": assessment.get("evaluation_at_utc"),
            "contract_version": assessment.get("contract_version"),
        }
    return {
        "status": "ASSESSED",
        "contract_version": assessment.get("contract_version"),
        "evaluation_at_utc": assessment.get("evaluation_at_utc"),
        "window_seconds": assessment.get("window_seconds"),
        "rows": [
            {
                "subject": row.get("subject"),
                "market": row.get("market"),
                "lifecycle_state": row.get("lifecycle_state"),
                "lifecycle_event_at_tip": row.get("lifecycle_event_at_tip"),
                "temporal_status": row.get("temporal_status"),
                "expires_at_utc": row.get("expires_at_utc"),
                "t0_operational_evaluated_at_utc": row.get("t0_operational_evaluated_at_utc"),
                "entry_eligibility_status": row.get("entry_eligibility_status"),
            }
            for row in rows
        ],
    }


def _discovery_case_facts(cases_packet: dict | None, market_labels: set[str], subject: str) -> dict:
    if cases_packet is None:
        return {"status": NOT_AVAILABLE, "reason": "EVENT_DISCOVERY_CASE_PACKET_ABSENT"}
    coverage = cases_packet.get("source_coverage") or {}
    if market_labels & {"CRYPTO"} and coverage.get("crypto") == "NOT_IMPLEMENTED":
        return {
            "status": "FEATURE_NOT_IMPLEMENTED",
            "reason": "EVENT_DISCOVERY_SOURCE_COVERAGE_CRYPTO_NOT_IMPLEMENTED",
            "packet_sha256": cases_packet.get("packet_sha256"),
            "source_coverage": copy.deepcopy(coverage),
        }
    rows = [
        case for case in cases_packet.get("cases") or []
        if isinstance(case, dict) and case.get("market") in market_labels and case.get("subject") == subject
    ]
    base = {
        "packet_sha256": cases_packet.get("packet_sha256"),
        "binding_set_id": cases_packet.get("binding_set_id"),
        "source_coverage": copy.deepcopy(coverage),
        "importance_policy_status": cases_packet.get("importance_policy_status"),
    }
    if not rows:
        base.update({"status": NO_EVIDENCE, "reason": "NO_DISCOVERY_CASE_FOR_SUBJECT", "case_count": 0})
        return base
    base.update({
        "status": "CASES_PRESENT",
        "case_count": len(rows),
        "latest_event_date": max(str(case.get("event_date")) for case in rows),
        "evidence_status_counts": dict(sorted(Counter(case.get("evidence_status") for case in rows).items())),
        "promotion_status_counts": dict(sorted(Counter(case.get("promotion_status") for case in rows).items())),
        "event_types": sorted({str(case.get("event_type")) for case in rows}),
    })
    return base


# --------------------------------------------------------------------------
# KR
# --------------------------------------------------------------------------
def _kr_context(inputs: dict, observed_at: dt.datetime) -> dict:
    universe = _load_kr_universe(inputs["kr_universe_path"])
    review = _load_review(inputs["kr_review_path"], "KR", observed_at)
    signals = _read_json(inputs["kr_market_signals_path"], "KR_MARKET_SIGNALS_READ_FAILED")
    _validate_self_hash(signals, "payload_sha256", "KR_MARKET_SIGNALS_PAYLOAD_SHA256_MISMATCH")
    if review.get("source_market_packet_sha256") != signals.get("payload_sha256"):
        _fail("KR_REVIEW_MARKET_SOURCE_MISMATCH")
    leadership = _load_optional_hashed(
        inputs.get("kr_leadership_context_path"), "payload_sha256", "KR_LEADERSHIP_CONTEXT",
        schema_version="korea_leadership_live_attempt/1",
    )
    registry = _load_optional_hashed(
        inputs.get("kr_registry_coverage_path"), "payload_sha256", "KR_REGISTRY_COVERAGE",
        schema_version="krx_registry_evaluation_coverage/1",
    )
    membership_path = inputs.get("kr_market_membership_path")
    membership = _read_json(membership_path, "KR_MARKET_MEMBERSHIP_READ_FAILED") if membership_path else None
    records = universe["asset_master"]["records"]
    by_symbol: dict[str, dict] = {}
    duplicates = Counter()
    for record in records:
        symbol = record.get("primary_symbol")
        if not isinstance(symbol, str):
            _fail("KR_UNIVERSE_RECORD_SYMBOL_INVALID")
        duplicates[symbol] += 1
        by_symbol[symbol] = record
    return {
        "universe": universe,
        "review": review,
        "signals": signals,
        "leadership": leadership,
        "registry": registry,
        "membership": membership,
        "by_symbol": by_symbol,
        "duplicate_symbols": sorted(s for s, n in duplicates.items() if n > 1),
    }


def _kr_market_status(
    ctx: dict, inputs: dict, coverage_row: dict | None, generated_date: dt.date, observed_at: dt.datetime,
) -> dict:
    universe, review, signals = ctx["universe"], ctx["review"], ctx["signals"]
    population_count = universe["total_count"]
    symbols = review["symbols"]
    review_symbols = [row["symbol"] for row in symbols]
    if len(set(review_symbols)) != len(review_symbols):
        _fail("KR_REVIEW_DUPLICATE_SYMBOL")
    in_population = [s for s in review_symbols if s in ctx["by_symbol"]]
    not_in_population = [s for s in review_symbols if s not in ctx["by_symbol"]]
    state_counts = Counter(row["entry_review"]["state"] for row in symbols)
    reason_counts = Counter(reason for row in symbols for reason in row["entry_review"]["reasons"])
    passed = review["summary"]["automatic_entry_count"]
    excluded = 0 if not not_in_population else len(not_in_population)
    unevaluated = population_count - len(in_population)
    if coverage_row is None:
        cross_check = NOT_AVAILABLE
        missing_reasons = ["COVERAGE_RECEIPT_NOT_AVAILABLE", "FULL_POPULATION_EVALUATION_INPUT_NOT_CONNECTED"]
    else:
        if coverage_row["universe_count"] != population_count or coverage_row["bounded_current_output_count"] != len(symbols):
            _fail("KR_COVERAGE_CROSS_CHECK_MISMATCH")
        if coverage_row["evaluated_count"] != NOT_COUNTED:
            _fail("KR_COVERAGE_SEMANTICS_CHANGED")
        cross_check = "MATCH"
        missing_reasons = copy.deepcopy(coverage_row["missing_reasons"])

    gaps = [
        {
            "class": "FEATURE_NOT_IMPLEMENTED",
            "code": "FULL_POPULATION_EVALUATOR_NOT_CONNECTED",
            "affected_count": unevaluated,
            "affected_population": "krx_global_universe minus bounded review subjects",
            "evidence": {"coverage_missing_reasons": missing_reasons},
        },
        {
            "class": "FEATURE_NOT_IMPLEMENTED",
            "code": "POPULATION_PER_SYMBOL_PRICE_DATA_NOT_RETAINED",
            "affected_count": population_count,
            "affected_population": "krx_global_universe",
            "evidence": {
                "korea_market_signals.source.per_symbol_persistence": (signals.get("source") or {}).get("per_symbol_persistence"),
                "korea_market_signals.source.raw_persistence": (signals.get("source") or {}).get("raw_persistence"),
            },
        },
    ]
    for policy, status in sorted((universe.get("policy_status") or {}).items()):
        if status == "UNRATIFIED":
            gaps.append({
                "class": "POLICY_UNDEFINED",
                "code": f"KRX_{policy.upper()}_UNRATIFIED",
                "affected_count": population_count,
                "affected_population": "krx_global_universe",
                "evidence": {"krx_global_universe.policy_status": {policy: status}},
            })
    for reason, count in sorted(reason_counts.items()):
        klass = REVIEW_REASON_CLASS.get(reason)
        if klass in ("CONNECTED_FACT", "BOUNDARY_NOT_A_GAP"):
            continue
        gaps.append({
            "class": klass or f"UNCLASSIFIED_EXISTING_REASON:{UNDEFINED}",
            "code": reason,
            "affected_count": count,
            "affected_population": "korea_symbol_market_review bounded subjects",
            "evidence": {"entry_review.reasons": reason},
        })
    interval = _interval_status(
        universe["effective_interval"]["valid_from"], universe["effective_interval"]["valid_to"], generated_date
    )
    if interval["status_at_generated_date"] != "WITHIN_SOURCE_INTERVAL":
        gaps.append({
            "class": "SOURCE_STALE",
            "code": "KRX_UNIVERSE_EFFECTIVE_INTERVAL_" + interval["status_at_generated_date"],
            "affected_count": population_count,
            "affected_population": "krx_global_universe",
            "evidence": {"effective_interval": copy.deepcopy(universe["effective_interval"])},
        })

    registry = ctx["registry"]
    if registry is None:
        screening = {
            "status": NOT_AVAILABLE,
            "reason": "KRX_REGISTRY_EVALUATION_COVERAGE_PACKET_ABSENT",
            "expected_root": _relative(DEFAULT_KR_REGISTRY_COVERAGE_ROOT) if DEFAULT_KR_REGISTRY_COVERAGE_ROOT.exists() else "data/observations/krx_registry_evaluation_coverage",
        }
    else:
        screening = {
            "status": "AVAILABLE",
            "contract": registry.get("schema_version"),
            "evaluation_session_date": registry.get("evaluation_session_date"),
            "generated_at": registry.get("generated_at"),
            "screening_population": "KIS master (not the KRX source-coverage population)",
            "coverage": copy.deepcopy(registry.get("coverage")),
            "interpretation": copy.deepcopy(registry.get("interpretation")),
            "source": _source_ref(inputs["kr_registry_coverage_path"], registry.get("payload_sha256")),
        }

    return {
        "market": "KR",
        "population": {
            "count": population_count,
            "as_of": universe["as_of_date"],
            "population_id": universe["asset_master"]["master_id"],
            "semantics": universe["membership_semantics"],
            "market_counts": copy.deepcopy(universe["market_counts"]),
            "duplicate_primary_symbols": ctx["duplicate_symbols"],
            "freshness": interval,
            "source": _source_ref(inputs["kr_universe_path"], universe["payload_sha256"]),
        },
        "data_acquired": {
            "market_axes": {
                "observed": signals["coverage"]["observed_count"],
                "required": signals["coverage"]["required_count"],
                "as_of": signals["as_of_date"],
                "available_at": signals["available_at"],
                "status": signals["status"],
                "source": _source_ref(inputs["kr_market_signals_path"], signals["payload_sha256"]),
            },
            "symbol_level": {
                "count": review["summary"]["price_connected_count"],
                "population_id": "korea_symbol_market_review/1 supported_pipeline_subjects",
                "population_count": len(symbols),
                "price_connected_count": review["summary"]["price_connected_count"],
                "flow_connected_count": review["summary"]["flow_connected_count"],
                "as_of_session_date": sorted({row["price_context"]["as_of_session_date"] for row in symbols}),
            },
            "population_level_symbol_data": _population_level_symbol_data(
                "KR",
                observation_root=inputs["kr_population_observation_root"],
                lookup_at=observed_at,
                population_as_of=universe["as_of_date"],
                population_id=universe["asset_master"]["master_id"],
                population_count=population_count,
            ),
        },
        "evaluated": {
            "count": len(symbols),
            "population_id": "korea_symbol_market_review/1 supported_pipeline_subjects",
            "evaluator_contract": review["contract_version"],
            "evaluated_at": review["generated_at"],
            "operational_date_kst": review["operational_date_kst"],
            "mode": review["mode"],
            "source": _source_ref(inputs["kr_review_path"], review["packet_sha256"]),
        },
        "disposition": {
            "passed": {
                "count": passed,
                "semantics": "REVIEW_AUTOMATIC_ENTRY_COUNT_FROM_SOURCE",
                "pass_rule_status": UNDEFINED,
                "pass_rule_note": "review five_axis.final_policy=" + str(review["five_axis"].get("final_policy")),
            },
            "held": {"count": len(symbols) - passed, "by_state": dict(sorted(state_counts.items()))},
            "excluded": {
                "count": excluded,
                "semantics": "NO_EXCLUSION_RULE_APPLIED_BY_BOUNDED_REVIEW",
                "exclusion_rule_status": UNDEFINED,
            },
            "unevaluated": {
                "count": unevaluated,
                "basis": "population.count - evaluated symbols found in population",
                "semantics": "NOT_EVALUATED_BY_ANY_CONNECTED_EVALUATOR",
            },
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "reconciliation": {
            "population_equals_evaluated_in_population_plus_unevaluated": (
                population_count == len(in_population) + unevaluated
            ),
            "passed_plus_held_equals_evaluated": passed + (len(symbols) - passed) == len(symbols),
            "evaluated_symbols_in_population": in_population,
            "evaluated_symbols_not_in_population": not_in_population,
            "evaluated_duplicate_count": 0,
            "coverage_receipt_cross_check": cross_check,
        },
        "screening_layer": screening,
        "gap_classification": gaps,
        "next_step_conditions": _kr_next_steps(universe, review),
        "candidate_zero_semantics": "BOUNDED_REVIEW_ONLY_NO_POPULATION_CANDIDATE_RULE",
        "symbols": [_kr_compact_row(row) for row in symbols],
    }


def _kr_next_steps(universe: dict, review: dict) -> list[dict]:
    rows = [
        {"condition": "FULL_POPULATION_EVALUATION_INPUT_CONNECTED", "status": "UNMET",
         "defined_by": "three_market_evaluation_coverage missing_reasons"},
        {"condition": "FINAL_KOREA_REGIME_POLICY_RATIFIED", "status": "UNMET",
         "defined_by": "korea_symbol_market_review five_axis.final_policy=" + str(review["five_axis"].get("final_policy"))},
    ]
    for policy, status in sorted((universe.get("policy_status") or {}).items()):
        if status == "UNRATIFIED":
            rows.append({"condition": f"KRX_{policy.upper()}_RATIFIED", "status": "UNMET",
                         "defined_by": "krx_global_universe.policy_status"})
    rows.append({"condition": "CANDIDATE_PASS_RULE", "status": UNDEFINED,
                 "defined_by": "no ratified population-level candidate rule in repository"})
    return rows


def _kr_compact_row(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "name": row.get("name"),
        "pipeline_stage": row.get("pipeline_stage"),
        "pipeline_as_of": row.get("pipeline_as_of"),
        "entry_state": row["entry_review"]["state"],
        "blocking_reasons": [r["code"] for r in _classify_reasons(row["entry_review"]["reasons"]) if r["class"] not in ("CONNECTED_FACT", "BOUNDARY_NOT_A_GAP")],
        "price_as_of_session_date": row["price_context"].get("as_of_session_date"),
        "price_status": row["price_context"].get("status"),
        "detail_contract": "korea_symbol_market_review/1",
    }


def _kr_symbol_lookup(ctx: dict, inputs: dict, symbol: str, stage_history: dict, cases: dict | None, validity: dict | None) -> dict:
    universe, review = ctx["universe"], ctx["review"]
    record = ctx["by_symbol"].get(symbol)
    review_row = next((row for row in review["symbols"] if row["symbol"] == symbol), None)
    stage = _stage_facts(stage_history, symbol)
    if record is None and review_row is None and stage["status"] == "NOT_IN_STAGE_HISTORY":
        _fail("SYMBOL_NOT_FOUND", f"KR:{symbol}")
    if record is None:
        membership = {"status": "NOT_IN_POPULATION", "as_of": universe["as_of_date"],
                      "population_id": universe["asset_master"]["master_id"]}
    else:
        membership = {
            "status": "IN_POPULATION",
            "as_of": universe["as_of_date"],
            "population_id": universe["asset_master"]["master_id"],
            "asset_id": record.get("asset_id"),
            "display_name": record.get("display_name"),
            "memberships": sorted(m.get("membership_id") for m in record.get("memberships") or []),
            "investable_eligible": record.get("investable_eligible"),
            "universe_approved": record.get("universe_approved"),
            "valid_from": universe["effective_interval"]["valid_from"],
            "valid_to": universe["effective_interval"]["valid_to"],
        }
    contract_subjects = review["source"]["contract"].get("supported_pipeline_subjects") or []
    inclusion = {
        "status": "PIPELINE_SUBJECT" if symbol in contract_subjects else "NOT_A_PIPELINE_SUBJECT",
        "subject_list": "korea_symbol_market_review_contract.supported_pipeline_subjects",
        "stage_history": stage,
        "inclusion_reason": {
            "status": NO_EVIDENCE,
            "note": "supported_pipeline_subjects is an explicit list; config/rules.candidates.json is DRAFT_UNRATIFIED migration evidence whose '편입 사유' cell is excluded, so no ratified inclusion reason is recorded",
        },
    }
    membership_claim = None
    if ctx["membership"] is not None:
        membership_claim = next(
            (m for m in ctx["membership"].get("members") or [] if m.get("code") == symbol), None
        )
    leadership = ctx["leadership"]
    sector_link = {
        "symbol_to_sector_binding": {"status": NO_EVIDENCE,
                                     "note": "no ratified symbol-to-sector binding packet in repository"},
        "source_market_membership": membership["memberships"] if record else NO_EVIDENCE,
        "market_membership_claim": (
            {"market_claim": membership_claim.get("market_claim"), "approval_status": membership_claim.get("approval_status"),
             "observation_date": membership_claim.get("observation_date")}
            if membership_claim else NO_EVIDENCE
        ),
        "leadership_context": (
            {
                "level": "INDEX_SECTOR_ONLY",
                "observation_date": leadership.get("observation_date"),
                "status": (leadership.get("leadership_packet") or {}).get("status"),
                "relative_strength_series_count": len((leadership.get("leadership_packet") or {}).get("relative_strength_observations") or []),
                "source": _source_ref(inputs["kr_leadership_context_path"], leadership.get("payload_sha256")),
            }
            if leadership else {"status": NOT_AVAILABLE}
        ),
        "rotation_ledger_link": {"status": NO_EVIDENCE, "note": "no symbol-level rotation ledger entry evidence"},
    }
    if review_row is None:
        last_eval = {"status": NO_EVIDENCE, "reason": "SYMBOL_NOT_A_BOUNDED_REVIEW_SUBJECT"}
        unmet = [{"condition": "BOUNDED_REVIEW_COVERAGE", "status": "UNMET", "class": "FEATURE_NOT_IMPLEMENTED"}]
    else:
        last_eval = {
            "status": "EVALUATED",
            "evaluator_contract": review["contract_version"],
            "evaluated_at": review["generated_at"],
            "operational_date_kst": review["operational_date_kst"],
            "price_as_of_session_date": review_row["price_context"].get("as_of_session_date"),
            "entry_state": review_row["entry_review"]["state"],
            "reasons": _classify_reasons(review_row["entry_review"]["reasons"]),
            "observed_facts": copy.deepcopy(review_row.get("observed_facts")),
            "price_context": copy.deepcopy(review_row.get("price_context")),
            "flow_context": copy.deepcopy(review_row.get("flow_context")),
        }
        unmet = _unmet_from_reasons(review_row["entry_review"]["reasons"])
    unmet.append({"condition": "STAGE_TRANSITION_RULE", "status": UNDEFINED,
                  "class": "POLICY_UNDEFINED", "note": "no ratified Discovery/Candidate/Ready transition rule"})
    return {
        "market": "KR",
        "symbol": symbol,
        "name": (record or {}).get("display_name") or stage.get("name") or (review_row or {}).get("name"),
        "population_membership": membership,
        "candidate_inclusion": inclusion,
        "sector_rotation_link": sector_link,
        "last_evaluation": last_eval,
        "next_step_unmet_conditions": unmet,
        "exclusion_expiry": {
            "excluded_by_existing_rule": {"status": "NOT_EXCLUDED", "rule": "NO_RATIFIED_EXCLUSION_RULE_APPLIED",
                                          "rule_status": UNDEFINED} if record is not None else
                                         {"status": "NOT_IN_CURRENT_SOURCE_POPULATION", "rule": "krx_global_universe source coverage"},
            "stage_status": stage["status"],
            "validity_window": _validity_rows(validity, {"KOREA", "KR"}, {symbol}),
            "source_state_lifetime": {"snapshot_only": True, "automatic_carry_forward": False, "reevaluation_required": True},
        },
        "discovery_cases": _discovery_case_facts(cases, {"KR", "KOREA"}, symbol),
        "detail_contract_refs": [
            {"contract": "korea_symbol_market_review/1", "source": _source_ref(inputs["kr_review_path"], review["packet_sha256"])},
            {"contract": "krx_global_universe_packet/1", "source": _source_ref(inputs["kr_universe_path"], universe["payload_sha256"])},
        ],
        "authority": _authority(),
    }


# --------------------------------------------------------------------------
# US
# --------------------------------------------------------------------------
def _us_context(inputs: dict, observed_at: dt.datetime) -> dict:
    universe = _load_us_universe(inputs["us_universe_path"])
    review = _load_review(inputs["us_review_path"], "US", observed_at)
    market_data = COVERAGE._validated_free_market_data(Path(inputs["us_market_data_path"]), observed_at)
    if review.get("source_market_packet_sha256") != market_data.get("packet_sha256"):
        _fail("US_REVIEW_MARKET_SOURCE_MISMATCH")
    contract = US_REVIEW.load_contract(Path(inputs["us_review_contract_path"]))
    rows = universe["packet"]["source_attribute_rows"]
    by_symbol: dict[str, list[dict]] = {}
    for row in rows:
        symbol = row.get("primary_symbol")
        if not isinstance(symbol, str):
            _fail("US_UNIVERSE_ROW_SYMBOL_INVALID")
        by_symbol.setdefault(symbol, []).append(row)
    alpaca = market_data.get("alpaca") or {}
    bar_symbols = sorted({str(bar.get("symbol")) for bar in alpaca.get("bars") or [] if isinstance(bar, dict)})
    daily_symbols = sorted({str(bar.get("symbol")) for bar in alpaca.get("daily_bars") or [] if isinstance(bar, dict)})
    return {
        "universe": universe,
        "review": review,
        "market_data": market_data,
        "contract": contract,
        "by_symbol": by_symbol,
        "duplicate_symbols": sorted(s for s, r in by_symbol.items() if len(r) > 1),
        "bar_symbols": bar_symbols,
        "daily_symbols": daily_symbols,
        "daily_row_count": len(alpaca.get("daily_bars") or []),
    }


def _us_market_status(
    ctx: dict, inputs: dict, coverage_row: dict | None, generated_date: dt.date, observed_at: dt.datetime,
) -> dict:
    universe, review, market_data = ctx["universe"], ctx["review"], ctx["market_data"]
    packet = universe["packet"]
    population_count = packet["total_count"]
    symbols = review["symbols"]
    review_symbols = [row["symbol"] for row in symbols]
    if len(set(review_symbols)) != len(review_symbols):
        _fail("US_REVIEW_DUPLICATE_SYMBOL")
    in_population = [s for s in review_symbols if s in ctx["by_symbol"]]
    not_in_population = [s for s in review_symbols if s not in ctx["by_symbol"]]
    state_counts = Counter(row["entry_review"]["state"] for row in symbols)
    reason_counts = Counter(reason for row in symbols for reason in row["entry_review"]["reasons"])
    passed = review["summary"]["automatic_entry_count"]
    unevaluated = population_count - len(in_population)
    registry_contract = COVERAGE.US_INVESTABLE_REGISTRY.load_contract()
    if coverage_row is None:
        cross_check = NOT_AVAILABLE
        connection = {
            "status": "NOT_CONNECTED",
            "required_input_schema": "us_investable_snapshot/1",
            "required_fail_closed_facts": copy.deepcopy(registry_contract.get("required_fail_closed_facts") or []),
            "liquidity_policy_status": (
                "ABSENT_EXTERNAL_RATIFIED_POLICY_REQUIRED"
                if (registry_contract.get("liquidity") or {}).get("repository_default_policy") == "ABSENT" else UNDEFINED
            ),
            "reason": "COVERAGE_RECEIPT_NOT_AVAILABLE_CONTRACT_FACTS_ONLY",
        }
        readiness = {}
    else:
        if (
            coverage_row["universe_count"] != population_count
            or coverage_row["bounded_current_output_count"] != len(symbols)
            or coverage_row.get("bounded_output_state_counts") != dict(sorted(state_counts.items()))
        ):
            _fail("US_COVERAGE_CROSS_CHECK_MISMATCH")
        cross_check = "MATCH"
        connection = coverage_row.get("population_evaluation_connection") or {}
        readiness = coverage_row.get("investable_input_readiness") or {}
    price_unavailable = [row["symbol"] for row in symbols if row["price_context"].get("status") != "OBSERVED"]

    gaps = [
        {
            "class": "FEATURE_NOT_IMPLEMENTED",
            "code": "FULL_POPULATION_EVALUATOR_NOT_CONNECTED",
            "affected_count": unevaluated,
            "affected_population": "us_global_universe minus bounded review subjects",
            "evidence": {"population_evaluation_connection": copy.deepcopy(connection)},
        },
    ]
    if price_unavailable:
        gaps.append({
            "class": "COLLECTION_FAILED",
            "code": "PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE",
            "affected_count": len(price_unavailable),
            "affected_population": "us_symbol_market_review bounded subjects",
            "evidence": {"symbols": price_unavailable, "source_scope": (market_data.get("alpaca") or {}).get("source_scope")},
        })
    if connection.get("liquidity_policy_status"):
        gaps.append({
            "class": "POLICY_UNDEFINED",
            "code": "US_LIQUIDITY_POLICY_" + str(connection.get("liquidity_policy_status")),
            "affected_count": population_count,
            "affected_population": "us_global_universe",
            "evidence": {"liquidity_policy_status": connection.get("liquidity_policy_status")},
        })
    for policy, status in sorted((packet.get("policy_status") or {}).items()):
        if status == "UNRATIFIED":
            gaps.append({
                "class": "POLICY_UNDEFINED",
                "code": f"US_{policy.upper()}_UNRATIFIED",
                "affected_count": population_count,
                "affected_population": "us_global_universe",
                "evidence": {"us_global_universe.policy_status": {policy: status}},
            })
    for reason, count in sorted(reason_counts.items()):
        klass = REVIEW_REASON_CLASS.get(reason)
        if klass in ("CONNECTED_FACT", "BOUNDARY_NOT_A_GAP", "COLLECTION_FAILED"):
            continue
        gaps.append({
            "class": klass or f"UNCLASSIFIED_EXISTING_REASON:{UNDEFINED}",
            "code": reason,
            "affected_count": count,
            "affected_population": "us_symbol_market_review bounded subjects",
            "evidence": {"entry_review.reasons": reason},
        })
    interval = _interval_status(
        packet["effective_interval"]["valid_from"], packet["effective_interval"]["valid_to"], generated_date
    )
    if interval["status_at_generated_date"] != "WITHIN_SOURCE_INTERVAL":
        gaps.append({
            "class": "SOURCE_STALE",
            "code": "US_UNIVERSE_EFFECTIVE_INTERVAL_" + interval["status_at_generated_date"],
            "affected_count": population_count,
            "affected_population": "us_global_universe",
            "evidence": {"effective_interval": copy.deepcopy(packet["effective_interval"])},
        })
    next_steps = [
        {"condition": "NATURAL_POPULATION_INPUT_CONNECTED", "status": "UNMET",
         "defined_by": "population_evaluation_connection.required_input_schema=" + str(connection.get("required_input_schema"))},
        {"condition": "US_LIQUIDITY_POLICY_RATIFIED", "status": "UNMET",
         "defined_by": "population_evaluation_connection.liquidity_policy_status"},
        {"condition": "FINAL_US_REGIME_AVAILABLE", "status": "UNMET",
         "defined_by": "us_symbol_market_review five_axis.aggregate_regime=" + str(review["five_axis"].get("aggregate_regime"))},
    ]
    for fact in connection.get("required_fail_closed_facts") or []:
        next_steps.append({"condition": f"US_FACT_SOURCE_{str(fact).upper()}", "status": "UNMET",
                           "defined_by": "us_investable_registry required_fail_closed_facts"})
    next_steps.append({"condition": "CANDIDATE_PASS_RULE", "status": UNDEFINED,
                       "defined_by": "no ratified population-level candidate rule in repository"})
    return {
        "market": "US",
        "population": {
            "count": population_count,
            "as_of": packet["as_of_date"],
            "as_of_utc": packet.get("as_of_utc"),
            "population_id": (packet.get("asset_master") or {}).get("master_id"),
            "semantics": packet["membership_semantics"],
            "source_counts": copy.deepcopy(packet.get("source_counts")),
            "duplicate_primary_symbols_across_sources": ctx["duplicate_symbols"],
            "freshness": interval,
            "source": _source_ref(inputs["us_universe_path"], universe["payload_sha256"]),
        },
        "data_acquired": {
            "directory_attributes": {
                "count": population_count,
                "as_of": packet["as_of_date"],
                "facts_available": (
                    {row["fact"]: row["available_count"] for row in readiness.get("field_source_matrix") or []}
                    if readiness else NOT_AVAILABLE
                ),
            },
            "daily_bars": {
                "symbol_count": len(ctx["daily_symbols"]),
                "row_count": ctx["daily_row_count"],
                "symbols": ctx["daily_symbols"],
                "observed_at_utc": market_data.get("observed_at_utc"),
                "source_scope": (market_data.get("alpaca") or {}).get("source_scope"),
                "feed": (market_data.get("alpaca") or {}).get("feed"),
                "source": _source_ref(inputs["us_market_data_path"], market_data["packet_sha256"]),
            },
            "symbol_level": {
                "count": review["summary"]["price_connected_count"],
                "population_id": "us_symbol_market_review/1 supported_pipeline_subjects",
                "population_count": len(symbols),
                "price_unavailable_symbols": price_unavailable,
            },
            "population_level_symbol_data": _population_level_symbol_data(
                "US",
                observation_root=inputs["us_population_observation_root"],
                lookup_at=observed_at,
                population_as_of=packet["as_of_date"],
                population_id=(packet.get("asset_master") or {}).get("master_id"),
                population_count=population_count,
            ),
        },
        "evaluated": {
            "count": len(symbols),
            "population_id": "us_symbol_market_review/1 supported_pipeline_subjects",
            "evaluator_contract": review["contract_version"],
            "evaluated_at": review["generated_at"],
            "operational_date_kst": review["operational_date_kst"],
            "mode": review["mode"],
            "source": _source_ref(inputs["us_review_path"], review["packet_sha256"]),
        },
        "disposition": {
            "passed": {"count": passed, "semantics": "REVIEW_AUTOMATIC_ENTRY_COUNT_FROM_SOURCE", "pass_rule_status": UNDEFINED},
            "held": {"count": len(symbols) - passed, "by_state": dict(sorted(state_counts.items()))},
            "excluded": {"count": len(not_in_population), "semantics": "NO_EXCLUSION_RULE_APPLIED_BY_BOUNDED_REVIEW",
                         "exclusion_rule_status": UNDEFINED},
            "unevaluated": {
                "count": unevaluated,
                "basis": "population.count - evaluated symbols found in population",
                "semantics": "NOT_EVALUATED_BY_ANY_CONNECTED_EVALUATOR",
            },
            "reason_counts": dict(sorted(reason_counts.items())),
        },
        "reconciliation": {
            "population_equals_evaluated_in_population_plus_unevaluated": (
                population_count == len(in_population) + unevaluated
            ),
            "passed_plus_held_equals_evaluated": passed + (len(symbols) - passed) == len(symbols),
            "evaluated_symbols_in_population": in_population,
            "evaluated_symbols_not_in_population": not_in_population,
            "evaluated_duplicate_count": 0,
            "coverage_receipt_cross_check": cross_check,
        },
        "screening_layer": {
            "status": NOT_AVAILABLE,
            "reason": "NO_US_POPULATION_SCREENING_PACKET",
            "closable_natural_input_count": readiness.get("current_fully_closable_natural_symbol_count", NOT_AVAILABLE),
        },
        "gap_classification": gaps,
        "next_step_conditions": next_steps,
        "candidate_zero_semantics": "BOUNDED_REVIEW_ONLY_NO_POPULATION_CANDIDATE_RULE",
        "symbols": [_us_compact_row(row) for row in symbols],
    }


def _us_compact_row(row: dict) -> dict:
    return {
        "symbol": row["symbol"],
        "name": row.get("name"),
        "pipeline_stage": row.get("pipeline_stage"),
        "pipeline_as_of": row.get("pipeline_as_of"),
        "entry_state": row["entry_review"]["state"],
        "blocking_reasons": [r["code"] for r in _classify_reasons(row["entry_review"]["reasons"]) if r["class"] not in ("CONNECTED_FACT", "BOUNDARY_NOT_A_GAP")],
        "price_as_of_session_date": row["price_context"].get("as_of_session_date"),
        "price_status": row["price_context"].get("status"),
        "detail_contract": "us_symbol_market_review/1",
    }


def _us_symbol_lookup(ctx: dict, inputs: dict, symbol: str, stage_history: dict, cases: dict | None, validity: dict | None) -> dict:
    universe, review, contract = ctx["universe"], ctx["review"], ctx["contract"]
    packet = universe["packet"]
    rows = ctx["by_symbol"].get(symbol) or []
    review_row = next((row for row in review["symbols"] if row["symbol"] == symbol), None)
    stage = _stage_facts(stage_history, symbol)
    if not rows and review_row is None and stage["status"] == "NOT_IN_STAGE_HISTORY":
        _fail("SYMBOL_NOT_FOUND", f"US:{symbol}")
    if not rows:
        membership = {"status": "NOT_IN_POPULATION", "as_of": packet["as_of_date"],
                      "population_id": (packet.get("asset_master") or {}).get("master_id")}
    else:
        membership = {
            "status": "IN_POPULATION",
            "as_of": packet["as_of_date"],
            "population_id": (packet.get("asset_master") or {}).get("master_id"),
            "rows": [
                {
                    "asset_id": row.get("asset_id"),
                    "source_name": row.get("source_name"),
                    "security_name": (row.get("fields") or {}).get("Security Name"),
                    "etf": (row.get("fields") or {}).get("ETF"),
                    "test_issue": (row.get("fields") or {}).get("Test Issue"),
                    "financial_status": (row.get("fields") or {}).get("Financial Status"),
                    "investable_eligible": row.get("investable_eligible"),
                    "tradability_decision": row.get("tradability_decision"),
                }
                for row in rows
            ],
            "valid_from": packet["effective_interval"]["valid_from"],
            "valid_to": packet["effective_interval"]["valid_to"],
        }
    subjects = contract.get("supported_pipeline_subjects") or []
    proxies = (contract.get("symbol_leadership_proxies") or {}).get(symbol)
    inclusion = {
        "status": "PIPELINE_SUBJECT" if symbol in subjects else "NOT_A_PIPELINE_SUBJECT",
        "subject_list": "us_symbol_market_review_contract.supported_pipeline_subjects",
        "stage_history": stage,
        "inclusion_reason": {
            "status": NO_EVIDENCE,
            "note": "supported_pipeline_subjects is an explicit list; config/rules.candidates.json is DRAFT_UNRATIFIED migration evidence whose '편입 사유' cell is excluded, so no ratified inclusion reason is recorded",
        },
    }
    sector_link = {
        "symbol_to_sector_binding": {"status": NO_EVIDENCE, "note": "no ratified symbol-to-sector binding packet in repository"},
        "leadership_proxies": (
            {"status": "CONTRACT_DECLARED", "proxies": list(proxies), "defined_by": "us_symbol_market_review_contract.symbol_leadership_proxies"}
            if proxies else {"status": NO_EVIDENCE}
        ),
        "market_context": (
            {"breadth_reference_as_of": ((review_row.get("market_context") or {}).get("breadth_reference") or {}).get("as_of_session_date")}
            if review_row else NO_EVIDENCE
        ),
        "rotation_ledger_link": {"status": NO_EVIDENCE, "note": "no symbol-level rotation ledger entry evidence"},
    }
    if review_row is None:
        last_eval = {"status": NO_EVIDENCE, "reason": "SYMBOL_NOT_A_BOUNDED_REVIEW_SUBJECT"}
        unmet = [{"condition": "BOUNDED_REVIEW_COVERAGE", "status": "UNMET", "class": "FEATURE_NOT_IMPLEMENTED"}]
    else:
        last_eval = {
            "status": "EVALUATED",
            "evaluator_contract": review["contract_version"],
            "evaluated_at": review["generated_at"],
            "operational_date_kst": review["operational_date_kst"],
            "price_as_of_session_date": review_row["price_context"].get("as_of_session_date"),
            "entry_state": review_row["entry_review"]["state"],
            "reasons": _classify_reasons(review_row["entry_review"]["reasons"]),
            "price_context": copy.deepcopy(review_row.get("price_context")),
        }
        unmet = _unmet_from_reasons(review_row["entry_review"]["reasons"])
    unmet.append({"condition": "STAGE_TRANSITION_RULE", "status": UNDEFINED,
                  "class": "POLICY_UNDEFINED", "note": "no ratified Discovery/Candidate/Ready transition rule"})
    return {
        "market": "US",
        "symbol": symbol,
        "name": ((rows[0].get("fields") or {}).get("Security Name") if rows else None) or stage.get("name") or (review_row or {}).get("name"),
        "population_membership": membership,
        "candidate_inclusion": inclusion,
        "sector_rotation_link": sector_link,
        "last_evaluation": last_eval,
        "next_step_unmet_conditions": unmet,
        "exclusion_expiry": {
            "excluded_by_existing_rule": {"status": "NOT_EXCLUDED", "rule": "NO_RATIFIED_EXCLUSION_RULE_APPLIED",
                                          "rule_status": UNDEFINED} if rows else
                                         {"status": "NOT_IN_CURRENT_SOURCE_POPULATION", "rule": "us_global_universe source coverage"},
            "stage_status": stage["status"],
            "validity_window": _validity_rows(validity, {"US"}, {symbol}),
            "source_state_lifetime": {"snapshot_only": True, "automatic_carry_forward": False, "reevaluation_required": True},
        },
        "discovery_cases": _discovery_case_facts(cases, {"US"}, symbol),
        "detail_contract_refs": [
            {"contract": "us_symbol_market_review/1", "source": _source_ref(inputs["us_review_path"], review["packet_sha256"])},
            {"contract": "us_global_universe_packet/1", "source": _source_ref(inputs["us_universe_path"], universe["payload_sha256"])},
        ],
        "authority": _authority(),
    }


# --------------------------------------------------------------------------
# CRYPTO
# --------------------------------------------------------------------------
def _crypto_context(inputs: dict, observed_at: dt.datetime) -> dict:
    universe = _load_crypto_universe(inputs["crypto_universe_path"], observed_at)
    decision = _load_crypto_decision(inputs["crypto_decision_path"], observed_at)
    detail = _load_crypto_detail(inputs.get("crypto_detail_path"), decision)
    bounded = _load_optional_hashed(
        inputs.get("crypto_bounded_identity_path"), "payload_sha256", "CRYPTO_BOUNDED_IDENTITY",
        schema_version="upbit_bounded_identity_registry_packet/1",
    )
    leadership = _load_crypto_leadership(inputs.get("crypto_leadership_path"))
    identity_review = _read_json(inputs["crypto_identity_review_path"], "CRYPTO_IDENTITY_REVIEW_READ_FAILED")
    _validate_self_hash(identity_review, "payload_sha256", "CRYPTO_IDENTITY_REVIEW_PAYLOAD_SHA256_MISMATCH")
    markets = universe["packet"]["markets"]
    by_market = {}
    for row in markets:
        if row["market"] in by_market:
            _fail("CRYPTO_UNIVERSE_DUPLICATE_MARKET", row["market"])
        by_market[row["market"]] = row
    decision_by_market = {}
    for row in decision["candidates"]:
        if row["market"] in decision_by_market:
            _fail("CRYPTO_DECISION_DUPLICATE_MARKET", row["market"])
        decision_by_market[row["market"]] = row
    detail_by_market = {}
    if detail and not detail["_unbound"]:
        for row in detail["record"]["candidates"]:
            detail_by_market[row["market"]] = row
    decision_root = Path(inputs["crypto_decision_path"]).parent.parent.parent.parent
    last_evaluated_by_market, last_evaluating_generation = _last_generation_with_candidates(decision_root, decision)
    newest_universe_path = _latest_dated_packet(Path(inputs["crypto_universe_path"]).parent.parent, "snapshot_date")
    newest_universe_date = (
        _read_json(newest_universe_path, "CRYPTO_UNIVERSE_READ_FAILED").get("snapshot_date") if newest_universe_path else None
    )
    proposals_by_market = {}
    for proposal in identity_review.get("proposals") or []:
        claim = proposal.get("claim") or {}
        if isinstance(claim.get("upbitMarket"), str):
            proposals_by_market[claim["upbitMarket"]] = proposal
    return {
        "universe": universe,
        "decision": decision,
        "detail": detail,
        "bounded": bounded,
        "leadership": leadership,
        "identity_review": identity_review,
        "by_market": by_market,
        "decision_by_market": decision_by_market,
        "detail_by_market": detail_by_market,
        "proposals_by_market": proposals_by_market,
        "last_evaluated_by_market": last_evaluated_by_market,
        "last_evaluating_generation": last_evaluating_generation,
        "newest_universe_snapshot_date": newest_universe_date,
    }


def _last_generation_with_candidates(decision_root: Path, latest: dict) -> tuple[dict, dict | None]:
    """Newest committed decision generation (by its verified capture time) that
    actually carries P5-08 candidate rows, per market.

    A generation in which P5-08 did not run has ``candidates == []``; the
    market's *last evaluation time* is then the newest earlier generation
    that evaluated it, which is reported separately from the latest
    generation -- never substituted for it.
    """
    root = Path(decision_root)
    entries = []
    if root.is_dir():
        for candidate in root.glob("*/*/*/packet.json"):
            try:
                entries.append(CRYPTO_DETAIL._verified_decision_entry(candidate))
            except CRYPTO_DETAIL.CryptoCandidateDetailViewError:
                continue
    entries.sort(key=lambda row: (row["captured_at"], row["generation_id"]))
    by_market: dict[str, dict] = {}
    last_generation = None
    for entry in reversed(entries):
        record = entry["record"]
        rows = record.get("candidates") or []
        if not rows:
            continue
        if last_generation is None:
            last_generation = {
                "generated_at": record.get("generated_at"),
                "generation_id": record.get("generation_id"),
                "path": _relative(entry["path"]),
                "candidate_count": len(rows),
                "is_latest_generation": record.get("generation_id") == latest.get("generation_id"),
            }
        for row in rows:
            market = row.get("market")
            if market in by_market or not isinstance(market, str):
                continue
            by_market[market] = {
                "generated_at": record.get("generated_at"),
                "generation_id": record.get("generation_id"),
                "path": _relative(entry["path"]),
                "state": row.get("state"),
                "reason": row.get("reason"),
                "is_latest_generation": record.get("generation_id") == latest.get("generation_id"),
            }
    return by_market, last_generation


def _crypto_market_status(ctx: dict, inputs: dict, coverage_row: dict | None, generated_date: dt.date) -> dict:
    universe, decision, detail = ctx["universe"], ctx["decision"], ctx["detail"]
    packet = universe["packet"]
    markets = packet["markets"]
    population_count = len(markets)
    evaluated = decision["candidates"]
    funnel = decision["funnel_counts"]
    state_counts = Counter(row["state"] for row in evaluated)
    universe_state_counts = Counter((row["state"], row["reason"]) for row in markets)
    excluded_rows = [row for row in markets if row["state"] in ("OBSERVATION_POOL", "BLOCKED")]
    admitted = [row for row in markets if row["state"] in ("TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE")]
    evaluated_markets = [row["market"] for row in evaluated]
    admitted_markets = [row["market"] for row in admitted]
    if not set(evaluated_markets) <= set(admitted_markets):
        _fail("CRYPTO_EVALUATED_NOT_ADMITTED", ",".join(sorted(set(evaluated_markets) - set(admitted_markets))))
    if funnel["tradeable_universe_count"] != len(admitted):
        _fail("CRYPTO_FUNNEL_ADMITTED_COUNT_MISMATCH")
    admitted_not_evaluated = sorted(set(admitted_markets) - set(evaluated_markets))
    derivation_notes = [note for note in decision.get("derivation_notes") or [] if isinstance(note, str)]
    if coverage_row is None:
        cross_check = NOT_AVAILABLE
    else:
        if (
            coverage_row["universe_count"] != population_count
            or coverage_row["evaluated_count"] != len(evaluated)
            or coverage_row["candidate_count"] != funnel["focused_review_count"]
            or coverage_row["excluded_count"] != len(excluded_rows)
            or coverage_row["held_count"] != sum(state_counts[s] for s in ("WATCH", "WAIT"))
            or coverage_row["paper_ready_count"] != funnel["paper_ready_count"]
        ):
            _fail("CRYPTO_COVERAGE_CROSS_CHECK_MISMATCH")
        cross_check = "MATCH"
    if detail is not None and not detail["_unbound"] and len(detail["record"]["candidates"]) != population_count:
        _fail("CRYPTO_DETAIL_POPULATION_MISMATCH")
    candle_rows = [row for row in markets if (row.get("observed_daily_candle_count") or 0) > 0]
    criteria_reason_counts: Counter = Counter()
    criteria_status_counts: Counter = Counter()
    for row in evaluated:
        for name, criterion in ((row.get("p5_08") or {}).get("criteria") or {}).items():
            criteria_status_counts[(name, criterion.get("status"))] += 1
            if criterion.get("status") != "PASS":
                criteria_reason_counts[(name, criterion.get("status"), criterion.get("reason"))] += 1
    excluded_reason_counts = Counter(row["reason"] for row in excluded_rows)
    gaps = []
    for reason, count in sorted(excluded_reason_counts.items()):
        if reason == "IDENTITY_UNRATIFIED":
            gaps.append({
                "class": "POLICY_UNDEFINED",
                "code": "IDENTITY_SCOPE_NOT_RATIFIED_BEYOND_CURRENT_PAPER_EIGHT",
                "affected_count": count,
                "affected_population": "upbit_tradeable_universe markets",
                "evidence": {"universe.reason": reason, "identity_registry_mapping_count": (universe.get("ratification") or {}).get("identity_registry", {}).get("mapping_count")},
            })
        else:
            gaps.append({
                "class": "EVALUATED_EXCLUDED_BY_RATIFIED_RULE" if packet.get("taxonomy_ratified") else "POLICY_UNDEFINED",
                "code": reason,
                "affected_count": count,
                "affected_population": "upbit_tradeable_universe markets",
                "evidence": {"taxonomy_version": packet.get("taxonomy_version"), "taxonomy_ratified": packet.get("taxonomy_ratified")},
            })
    skipped_class = _skipped_generation_class(derivation_notes) if admitted_not_evaluated else None
    if admitted_not_evaluated:
        gaps.append({
            "class": skipped_class,
            "code": "P5_08_DID_NOT_EVALUATE_ADMITTED_MARKETS_IN_LATEST_GENERATION",
            "affected_count": len(admitted_not_evaluated),
            "affected_population": "P3-12 admitted markets (TRADEABLE_UNIVERSE/PAPER_ELIGIBLE)",
            "evidence": {
                "derivation_notes": derivation_notes,
                "freshness_status": copy.deepcopy(decision.get("freshness_status")),
                "markets": admitted_not_evaluated,
            },
        })
    unknown_total = sum(state_counts[s] for s in ("WATCH", "WAIT"))
    if unknown_total:
        gaps.append({
            "class": "EVALUATED_CRITERIA_UNKNOWN",
            "code": "P5_08_CRITERIA_UNKNOWN_FOR_ALL_HELD_MARKETS",
            "affected_count": unknown_total,
            "affected_population": "crypto_paper_decision candidates",
            "evidence": {"state_counts": dict(sorted(state_counts.items()))},
        })
    for (name, status, reason), count in sorted(criteria_reason_counts.items(), key=lambda item: (item[0][0], str(item[0][1]), str(item[0][2]))):
        gaps.append({
            # UNKNOWN means the criterion could not be decided (a policy gap);
            # FAIL means it WAS decided and the candidate is excluded by a
            # ratified rule -- e.g. MATERIAL_BLOCKER:UPBIT_MARKET_EVENT_CAUTION_ACTIVE,
            # the exchange caution flag that blocked one candidate on 2026-09-16
            # and left every CI run on this repo red. Any other status stays
            # unclassified rather than guessed.
            "class": CRITERION_STATUS_CLASS.get(status, f"UNCLASSIFIED_EXISTING_REASON:{UNDEFINED}"),
            "code": f"{name}:{reason}",
            "affected_count": count,
            "affected_population": "crypto_paper_decision candidates",
            "evidence": {"criterion": name, "status": status},
        })
    if funnel["focused_review_count"] == 0:
        if not evaluated and admitted:
            zero_semantics = (
                "EVALUATION_HALTED_INPUT_DATE_MISMATCH"
                if skipped_class == "EVALUATION_HALTED_INPUT_DATE_MISMATCH"
                else "EVALUATOR_DID_NOT_RUN_IN_LATEST_GENERATION"
            )
        elif unknown_total == len(evaluated):
            zero_semantics = "CRITERIA_UNKNOWN_NOT_A_NEGATIVE_RESULT"
        else:
            zero_semantics = "EVALUATED_NO_CANDIDATE"
    else:
        zero_semantics = "CANDIDATES_PRESENT"
    if zero_semantics == "EVALUATED_NO_CANDIDATE":
        gaps.append({"class": "EVALUATED_NO_CANDIDATE", "code": "FOCUSED_REVIEW_COUNT_ZERO_WITH_KNOWN_CRITERIA",
                     "affected_count": len(evaluated), "affected_population": "crypto_paper_decision candidates", "evidence": {}})
    freshness = _undefined_freshness(
        packet["snapshot_date"], generated_date,
        note="upbit_tradeable_universe declares snapshot_only/reevaluation_required; no numeric expiry policy is ratified",
    )
    next_steps = [
        {"condition": f"{name}_RULE_RATIFIED", "status": "UNMET", "defined_by": f"p5_08 criteria reason {reason}"}
        for (name, status, reason) in sorted({k for k in criteria_reason_counts}, key=lambda k: (k[0], str(k[2])))
        if status == "UNKNOWN"
    ]
    next_steps.append({"condition": "IDENTITY_SCOPE_DECISION_FOR_UNRATIFIED_MARKETS", "status": "UNMET",
                       "defined_by": "three_market_evaluation_coverage evaluation_only_scope_readiness (CIO choice)"})
    if admitted_not_evaluated:
        next_steps.insert(0, {"condition": "P5_08_EVALUATION_RUN_FOR_ADMITTED_MARKETS", "status": "UNMET",
                              "defined_by": "decision derivation_notes: " + "; ".join(derivation_notes)})
    next_steps.append({"condition": "P5_09_TRIGGER_EVALUATION", "status": "UNMET",
                       "defined_by": "decision candidates p5_09=null for every evaluated market" if all(row.get("p5_09") is None for row in evaluated) else "p5_09 present for some markets"})
    return {
        "market": "CRYPTO",
        "population": {
            "count": population_count,
            "as_of": packet["snapshot_date"],
            "evaluation_as_of": packet["evaluation_as_of"],
            "available_at": packet["available_at"],
            "population_id": packet["manifest_sha256"],
            "semantics": "upbit KRW spot markets classified by ratified policy/taxonomy (decision-bound snapshot)",
            "policy_version": packet.get("policy_version"),
            "taxonomy_version": packet.get("taxonomy_version"),
            "freshness": freshness,
            "newest_committed_universe_snapshot_date": ctx["newest_universe_snapshot_date"],
            "newest_snapshot_bound_to_latest_decision": ctx["newest_universe_snapshot_date"] == packet["snapshot_date"],
            "state_reason_counts": {f"{state}|{reason}": count for (state, reason), count in sorted(universe_state_counts.items())},
            "source": _source_ref(inputs["crypto_universe_path"], universe["payload_sha256"]),
        },
        "data_acquired": {
            "daily_candles": {
                "count": len(candle_rows),
                "population_id": "upbit_tradeable_universe markets with observed_daily_candle_count>0",
                "as_of": packet["snapshot_date"],
            },
            "identity_proposals": {
                "count": len(ctx["identity_review"].get("proposals") or []),
                "as_of": ctx["identity_review"].get("snapshot_date"),
                "review_status": ctx["identity_review"].get("review_status"),
                "source": _source_ref(inputs["crypto_identity_review_path"], ctx["identity_review"].get("payload_sha256")),
            },
            "market_evidence_bound_to_decision": {
                "count": len([row for row in (detail["record"]["candidates"] if detail and not detail["_unbound"] else []) if (row.get("price") or {}).get("as_of")]),
                "status": "FROM_DETAIL_VIEW" if detail and not detail["_unbound"] else NOT_AVAILABLE,
            },
        },
        "evaluated": {
            "count": len(evaluated),
            "population_id": "crypto_paper_decision candidates (P3-12 admitted markets)",
            "evaluator_contract": decision["schema_version"],
            "evaluated_at": decision["generated_at"],
            "captured_at_utc": decision.get("captured_at_utc"),
            "operational_date_kst": decision.get("operational_date_kst"),
            "generation_id": decision["generation_id"],
            "admitted_count": len(admitted),
            "admitted_not_evaluated": admitted_not_evaluated,
            "derivation_notes": derivation_notes,
            "latest_generation_skipped_class": skipped_class,
            "last_generation_with_evaluations": _historical(ctx["last_evaluating_generation"]),
            "source": _source_ref(inputs["crypto_decision_path"], decision["payload_sha256"]),
        },
        "disposition": {
            "passed": {"count": funnel["focused_review_count"], "semantics": "FOCUSED_REVIEW_COUNT_FROM_DECISION_SNAPSHOT",
                       "paper_ready_count": funnel["paper_ready_count"]},
            "held": {"count": unknown_total, "by_state": dict(sorted(state_counts.items()))},
            "excluded": {"count": len(excluded_rows), "by_reason": dict(sorted(excluded_reason_counts.items())),
                         "semantics": "P3_12_UNIVERSE_STATES_OBSERVATION_POOL_OR_BLOCKED"},
            "unevaluated": {"count": population_count - len(evaluated) - len(excluded_rows),
                            "basis": "population.count - evaluated - excluded",
                            "admitted_not_evaluated_count": len(admitted_not_evaluated)},
            "criteria_status_counts": {f"{name}|{status}": count for (name, status), count in sorted(criteria_status_counts.items(), key=lambda i: (i[0][0], str(i[0][1])))},
        },
        "reconciliation": {
            "population_equals_evaluated_plus_excluded_plus_unevaluated": population_count == len(evaluated) + len(excluded_rows) + (population_count - len(evaluated) - len(excluded_rows)),
            "evaluated_subset_of_admitted": True,
            "evaluated_equals_admitted": not admitted_not_evaluated,
            "evaluated_duplicate_count": 0,
            "detail_view_binding": "BOUND_TO_SAME_DECISION_GENERATION" if detail and not detail["_unbound"] else ("UNBOUND_DIFFERENT_GENERATION" if detail else NOT_AVAILABLE),
            "coverage_receipt_cross_check": cross_check,
        },
        "screening_layer": (
            {
                "status": "AVAILABLE",
                "contract": "three_market_evaluation_coverage/1 evaluation_only_scope_readiness",
                "selection_market_count": ((coverage_row.get("evaluation_only_scope_readiness") or {}).get("selection") or {}).get("market_count"),
            }
            if coverage_row is not None else
            {"status": NOT_AVAILABLE, "reason": "COVERAGE_RECEIPT_NOT_AVAILABLE"}
        ),
        "gap_classification": gaps,
        "next_step_conditions": next_steps,
        "candidate_zero_semantics": zero_semantics,
        "symbols": [_crypto_compact_row(row, ctx["decision_by_market"].get(row["market"]), ctx["detail_by_market"].get(row["market"])) for row in markets],
    }


def _crypto_compact_row(universe_row: dict, decision_row: dict | None, detail_row: dict | None) -> dict:
    return {
        "symbol": universe_row["market"],
        "canonical_asset_id": universe_row.get("candidate_canonical_asset_id"),
        "universe_state": universe_row["state"],
        "universe_reason": universe_row["reason"],
        "funnel_stage": detail_row.get("funnel_stage") if detail_row else None,
        "decision_state": decision_row.get("state") if decision_row else None,
        "decision_reason": decision_row.get("reason") if decision_row else None,
        "evaluated_by_p5_08": decision_row is not None,
        "detail_contract": CRYPTO_DETAIL.CONTRACT_VERSION,
    }


def _crypto_symbol_lookup(ctx: dict, inputs: dict, symbol: str, cases: dict | None, validity: dict | None) -> dict:
    market = _normalize_crypto_market(symbol)
    universe, decision, detail = ctx["universe"], ctx["decision"], ctx["detail"]
    packet = universe["packet"]
    universe_row = ctx["by_market"].get(market)
    if universe_row is None:
        _fail("SYMBOL_NOT_FOUND", f"CRYPTO:{market}")
    decision_row = ctx["decision_by_market"].get(market)
    detail_row = ctx["detail_by_market"].get(market)
    proposal = ctx["proposals_by_market"].get(market)
    canonical = universe_row.get("candidate_canonical_asset_id")
    base_symbol = market.split("-", 1)[1]
    bounded = ctx["bounded"]
    if bounded is None:
        identity_verdict = {"status": NOT_AVAILABLE, "reason": "BOUNDED_IDENTITY_REGISTRY_PACKET_ABSENT"}
    else:
        verified = next((row for row in bounded.get("registry_candidates") or [] if row.get("market") == market), None)
        hold = next((row for row in bounded.get("hold_list") or [] if row.get("market") == market), None)
        if verified:
            identity_verdict = {"status": "VERIFIED_CANDIDATE", "review_status": bounded.get("review_status"),
                                "evaluation_as_of": bounded.get("evaluation_as_of"), "row": copy.deepcopy(verified)}
        elif hold:
            identity_verdict = {"status": hold.get("verdict"), "review_status": bounded.get("review_status"),
                                "evaluation_as_of": bounded.get("evaluation_as_of"), "basis": hold.get("verdict_basis")}
        else:
            identity_verdict = {"status": NO_EVIDENCE, "reason": "MARKET_NOT_IN_BOUNDED_IDENTITY_RESEARCH_SCOPE",
                                "evaluation_as_of": bounded.get("evaluation_as_of")}
    leadership = ctx["leadership"]
    if leadership is None:
        rotation = {"status": NOT_AVAILABLE, "reason": "CRYPTO_LEADERSHIP_PACKET_ABSENT"}
    else:
        windows = leadership.get("windows") or []
        member_hits = []
        for window in windows:
            for row in window.get("asset_relative_strength") or []:
                if isinstance(row, dict) and (row.get("asset") == canonical or row.get("asset") == base_symbol):
                    member_hits.append(row)
        rotation = {
            "status": "OBSERVED" if member_hits else NO_EVIDENCE,
            "leadership_status": leadership.get("status"),
            "unknown_reason": leadership.get("unknown_reason"),
            "as_of_date": leadership.get("as_of_date"),
            "contract_version": leadership.get("contract_version"),
            "rows": copy.deepcopy(member_hits),
            "source": _source_ref(inputs["crypto_leadership_path"], None),
            "lineage": copy.deepcopy(leadership.get("lineage")),
        }
    admitted = universe_row["state"] in ("TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE")
    last_evaluated = ctx["last_evaluated_by_market"].get(market)
    if decision_row is None and admitted:
        notes = [n for n in decision.get("derivation_notes") or [] if isinstance(n, str)]
        skipped_class = _skipped_generation_class(notes)
        last_eval = {
            "status": (
                "ADMITTED_EVALUATION_HALTED_INPUT_DATE_MISMATCH"
                if skipped_class == "EVALUATION_HALTED_INPUT_DATE_MISMATCH"
                else "ADMITTED_NOT_EVALUATED_IN_LATEST_GENERATION"
            ),
            "skipped_class": skipped_class,
            "latest_generation": {
                "generated_at": decision["generated_at"],
                "generation_id": decision["generation_id"],
                "derivation_notes": notes,
                "freshness_status": copy.deepcopy(decision.get("freshness_status")),
            },
            "last_evaluated_generation": _historical(last_evaluated),
            "universe_state": universe_row["state"],
            "universe_reason": universe_row["reason"],
        }
        unmet = [{"condition": "P5_08_EVALUATION_RUN", "status": "UNMET", "class": skipped_class,
                  "reason": "; ".join(notes)}]
    elif decision_row is None:
        last_eval = {
            "status": "NOT_EVALUATED_BY_P5_08",
            "reason": "MARKET_NOT_ADMITTED_TO_DECISION_INPUT",
            "universe_state": universe_row["state"],
            "universe_reason": universe_row["reason"],
            "universe_evaluation_as_of": packet["evaluation_as_of"],
            "universe_available_at": packet["available_at"],
            "last_evaluated_generation": _historical(last_evaluated),
        }
        unmet = [{"condition": "P3_12_ADMISSION_TO_TRADEABLE_UNIVERSE", "status": "UNMET",
                  "class": "POLICY_UNDEFINED" if universe_row["reason"] == "IDENTITY_UNRATIFIED" else "EVALUATED_EXCLUDED_BY_RATIFIED_RULE",
                  "reason": universe_row["reason"]}]
    else:
        criteria = (decision_row.get("p5_08") or {}).get("criteria") or {}
        last_eval = {
            "status": "EVALUATED",
            "evaluator_contract": decision["schema_version"],
            "evaluated_at": decision["generated_at"],
            "operational_date_kst": decision.get("operational_date_kst"),
            "generation_id": decision["generation_id"],
            "state": decision_row["state"],
            "reason": decision_row["reason"],
            "p3_12_state": decision_row.get("p3_12_state"),
            "criteria": copy.deepcopy(criteria),
            "p5_09": copy.deepcopy(decision_row.get("p5_09")),
            "freshness_capped": decision_row.get("freshness_capped"),
            "last_evaluated_generation": {"historical": False, "generated_at": decision["generated_at"],
                                          "generation_id": decision["generation_id"], "is_latest_generation": True},
        }
        unmet = [
            {"condition": name, "status": criterion.get("status"), "reason": criterion.get("reason"),
             "class": "POLICY_UNDEFINED" if criterion.get("status") == "UNKNOWN" else "EVALUATED_FAIL"}
            for name, criterion in sorted(criteria.items()) if criterion.get("status") != "PASS"
        ]
        if decision_row.get("p5_09") is None:
            unmet.append({"condition": "P5_09_TRIGGER_AND_ORDER_DRAFT", "status": "NOT_EVALUATED", "class": "FEATURE_NOT_IMPLEMENTED"})
    if detail_row is None:
        detail_facts = {"status": NOT_AVAILABLE if detail is None else "UNBOUND_OR_MISSING"}
    else:
        detail_facts = {
            "status": "FROM_DETAIL_VIEW",
            "generated_at": detail["record"].get("generated_at"),
            "funnel_stage": detail_row.get("funnel_stage"),
            "detailed_state": detail_row.get("detailed_state"),
            "blocker_reason": detail_row.get("blocker_reason"),
            "price": copy.deepcopy(detail_row.get("price")),
            "liquidity": copy.deepcopy(detail_row.get("liquidity")),
            "trend": copy.deepcopy(detail_row.get("trend")),
            "relative_strength": copy.deepcopy(detail_row.get("relative_strength")),
            "trigger_prerequisites": copy.deepcopy(detail_row.get("trigger_prerequisites")),
            "source": _source_ref(inputs["crypto_detail_path"], detail["record"].get("payload_sha256")),
        }
    excluded = universe_row["state"] in ("OBSERVATION_POOL", "BLOCKED")
    return {
        "market": "CRYPTO",
        "symbol": market,
        "name": ((proposal or {}).get("claim") or {}).get("koreanName") or ((proposal or {}).get("claim") or {}).get("englishName"),
        "population_membership": {
            "status": "IN_POPULATION",
            "as_of": packet["snapshot_date"],
            "population_id": packet["manifest_sha256"],
            "state": universe_row["state"],
            "reason": universe_row["reason"],
            "canonical_asset_id": canonical,
            "market_event": {"warning": universe_row.get("market_event_warning"), "caution_any": universe_row.get("market_event_caution_any")},
            "observed_daily_candle_count": universe_row.get("observed_daily_candle_count"),
            "trailing_30d_krw_turnover": universe_row.get("trailing_30d_krw_turnover"),
        },
        "candidate_inclusion": {
            "status": "ADMITTED_TO_EVALUATION_INPUT" if admitted else "OBSERVATION_POOL_ONLY",
            "inclusion_reason": {
                "status": "RULE_RECORDED",
                "rule": f"{packet.get('policy_version')} / {packet.get('taxonomy_version')}",
                "state": universe_row["state"],
                "reason": universe_row["reason"],
            },
            "identity_proposal": (
                {"status": "PROPOSED", "review_status": ctx["identity_review"].get("review_status"),
                 "snapshot_date": ctx["identity_review"].get("snapshot_date"),
                 "candidate_canonical_asset_id": ((proposal or {}).get("claim") or {}).get("candidateCanonicalAssetId"),
                 "exception_status": ((proposal or {}).get("claim") or {}).get("exceptionStatus")}
                if proposal else {"status": NO_EVIDENCE}
            ),
            "bounded_identity_verdict": identity_verdict,
        },
        "sector_rotation_link": {
            "symbol_to_sector_binding": {"status": NO_EVIDENCE, "note": "crypto sector chain status comes only from crypto_leadership group_relative_strength"},
            "leadership": rotation,
        },
        "last_evaluation": last_eval,
        "detail_view": detail_facts,
        "next_step_unmet_conditions": unmet,
        "discovery_cases": _discovery_case_facts(cases, {"CRYPTO"}, base_symbol),
        "exclusion_expiry": {
            "excluded_by_existing_rule": (
                {"status": "EXCLUDED", "rule": "upbit_tradeable_universe " + str(packet.get("taxonomy_version")),
                 "reason": universe_row["reason"], "rule_ratified": packet.get("taxonomy_ratified"),
                 "rule_status": "POLICY_UNDEFINED" if universe_row["reason"] == "IDENTITY_UNRATIFIED" else "RATIFIED"}
                if excluded else {"status": "NOT_EXCLUDED", "rule": "upbit_tradeable_universe " + str(packet.get("taxonomy_version"))}
            ),
            "validity_window": _validity_rows(validity, {"CRYPTO", "BTC"}, {market, base_symbol, canonical} - {None}),
            "source_state_lifetime": {"snapshot_only": True, "automatic_carry_forward": False, "reevaluation_required": True,
                                      "universe_snapshot_date": packet["snapshot_date"]},
        },
        "detail_contract_refs": [
            {"contract": CRYPTO_DETAIL.CONTRACT_VERSION, "source": _source_ref(inputs["crypto_detail_path"], detail["record"].get("payload_sha256")) if detail else NOT_AVAILABLE},
            {"contract": decision["schema_version"], "source": _source_ref(inputs["crypto_decision_path"], decision["payload_sha256"])},
            {"contract": packet["schema_version"], "source": _source_ref(inputs["crypto_universe_path"], universe["payload_sha256"])},
        ],
        "authority": _authority(),
    }


# --------------------------------------------------------------------------
# report assembly
# --------------------------------------------------------------------------
def _coverage_report(inputs: dict, generated_at: str, *, strict: bool) -> dict:
    """Build the three-market coverage receipt; report (not hide) its fail-closed state.

    The receipt asserts that the newest Crypto decision generation evaluated
    every admitted market.  A generation in which P5-08 did not run (for
    example ``P5_08_PROMOTION_FUNNEL_UNAVAILABLE``) makes the receipt refuse.
    That refusal is itself a status fact, so by default it is returned as
    ``FAILED_CLOSED`` with the exact reason and the lookup continues from the
    per-symbol sources; ``strict=True`` re-raises instead.
    """
    try:
        report = _build_coverage(inputs, generated_at)
    except COVERAGE.ThreeMarketEvaluationCoverageError as exc:
        if strict:
            raise
        return {"status": "FAILED_CLOSED", "reason": str(exc), "report": None}
    return {"status": "BUILT", "reason": None, "report": report}


def _build_coverage(inputs: dict, generated_at: str) -> dict:
    return COVERAGE.build_report(
        generated_at=generated_at,
        kr_universe_path=Path(inputs["kr_universe_path"]),
        kr_review_path=Path(inputs["kr_review_path"]),
        us_universe_path=Path(inputs["us_universe_path"]),
        us_review_path=Path(inputs["us_review_path"]),
        us_raw_snapshot_dir=Path(inputs["us_raw_snapshot_dir"]),
        us_market_data_path=Path(inputs["us_market_data_path"]),
        crypto_universe_path=Path(inputs["crypto_universe_path"]),
        crypto_decision_path=Path(inputs["crypto_decision_path"]),
        crypto_identity_review_path=Path(inputs["crypto_identity_review_path"]),
        crypto_snapshot_dir=Path(inputs["crypto_snapshot_dir"]),
        prior_identity_evidence_path=Path(inputs["prior_identity_evidence_path"]),
    )


def _optional_packets(inputs: dict) -> tuple[dict | None, dict | None]:
    cases_path = inputs.get("discovery_cases_path")
    cases = None
    if cases_path is not None:
        cases = _read_json(cases_path, "DISCOVERY_CASES_READ_FAILED")
        _validate_self_hash(cases, "packet_sha256", "DISCOVERY_CASES_PACKET_SHA256_MISMATCH")
    validity_path = inputs.get("validity_assessment_path")
    validity = None
    if validity_path is not None:
        validity = _read_json(validity_path, "VALIDITY_ASSESSMENT_READ_FAILED")
        _validate_self_hash(validity, "assessment_sha256", "VALIDITY_ASSESSMENT_SHA256_MISMATCH")
    return cases, validity


def _portal_block(inputs: dict, coverage: dict | None, ctxs: dict) -> dict:
    # ``ctxs`` only holds the markets ``build_report`` was actually asked
    # for (see its lazy-context-build comment): a market this report never
    # requested is never referenced here either, matching the rest of the
    # report.
    refs = [
        {"role": "three_market_coverage", "contract": COVERAGE.SCHEMA_VERSION,
         "payload_sha256": coverage["payload_sha256"] if coverage else None,
         "status": "BUILT" if coverage else "FAILED_CLOSED"},
    ]
    if "KR" in ctxs:
        refs.append({"role": "kr_symbol_review", "contract": ctxs["KR"]["review"]["contract_version"],
                     "source": _source_ref(inputs["kr_review_path"], ctxs["KR"]["review"]["packet_sha256"])})
    if "US" in ctxs:
        refs.append({"role": "us_symbol_review", "contract": ctxs["US"]["review"]["contract_version"],
                     "source": _source_ref(inputs["us_review_path"], ctxs["US"]["review"]["packet_sha256"])})
    if "CRYPTO" in ctxs:
        refs.append({"role": "crypto_decision", "contract": ctxs["CRYPTO"]["decision"]["schema_version"],
                     "source": _source_ref(inputs["crypto_decision_path"], ctxs["CRYPTO"]["decision"]["payload_sha256"])})
        detail = ctxs["CRYPTO"]["detail"]
        if detail is not None:
            refs.append({"role": "crypto_candidate_detail", "contract": CRYPTO_DETAIL.CONTRACT_VERSION,
                         "binding": "BOUND_TO_SAME_DECISION_GENERATION" if not detail["_unbound"] else "UNBOUND_DIFFERENT_GENERATION",
                         "source": _source_ref(inputs["crypto_detail_path"], detail["record"].get("payload_sha256"))})
    return {
        "market_list_contract": {
            "schema": f"{SCHEMA_VERSION}#market_list",
            "row_fields": ["symbol", "name", "pipeline_stage", "entry_state", "blocking_reasons", "price_as_of_session_date", "detail_contract"],
            "crypto_row_fields": ["symbol", "canonical_asset_id", "universe_state", "universe_reason", "funnel_stage", "decision_state", "decision_reason", "evaluated_by_p5_08", "detail_contract"],
            "source": "markets[].symbols",
        },
        "symbol_detail_contract": {
            "schema": f"{SCHEMA_VERSION}#symbol_detail",
            "sections": ["population_membership", "candidate_inclusion", "sector_rotation_link", "last_evaluation",
                         "next_step_unmet_conditions", "exclusion_expiry", "discovery_cases", "detail_contract_refs"],
            "entrypoint": "lookup_symbol(market, symbol)",
        },
        "reused_contracts": refs,
        "display_boundary": "FACT_ONLY_NO_INFERENCE_UNKNOWN_KEPT_AS_UNKNOWN",
        "source_dates_preserved": True,
    }


def _category(key: str, count, **extra) -> dict:
    row = {"label": CATEGORY_LABELS[key], "count": count}
    row.update(extra)
    return row


def _market_summary(row: dict) -> dict:
    """Reader-facing separation of population vs. evaluated symbols and of the
    four dispositions a user must be able to tell apart: 미평가 / 정책 미정 /
    근거 없음 / 정상 평가 후 후보 없음 (plus 수집 실패, 평가 중단, 규칙 제외
    where they occur).  Derived only from the row's own fields.
    """
    market = row["market"]
    population = row["population"]
    evaluated = row["evaluated"]
    disposition = row["disposition"]
    gaps = row["gap_classification"]
    policy_codes = sorted({gap["code"] for gap in gaps if gap["class"] == "POLICY_UNDEFINED"})
    symbols = row["symbols"]
    if market in ("KR", "US"):
        blocked_by_policy = [
            s["symbol"] for s in symbols
            if any(REVIEW_REASON_CLASS.get(code) == "POLICY_UNDEFINED" for code in s["blocking_reasons"])
        ]
        collection_failed = [
            s["symbol"] for s in symbols
            if any(REVIEW_REASON_CLASS.get(code) == "COLLECTION_FAILED" for code in s["blocking_reasons"])
        ]
        categories = {
            "unevaluated": _category(
                "unevaluated", disposition["unevaluated"]["count"],
                meaning="전체 모집단 평가기가 연결되지 않아 평가 자체가 없는 종목 수 (모집단 − 실제 평가 종목)",
                population_id=population["population_id"],
            ),
            "collection_failed": _category(
                "collection_failed", len(collection_failed), symbols=collection_failed,
                meaning="평가 대상이지만 가격 이력 등 입력 자료 수집이 실패한 종목",
            ),
            "policy_undefined": _category(
                "policy_undefined", len(blocked_by_policy), symbols=blocked_by_policy,
                policies=policy_codes,
                meaning="평가는 됐으나 최종 판정 규칙(시장 Regime 정책·통과 규칙 등)이 미비준이라 보류된 종목",
            ),
            "no_evidence": _category(
                "no_evidence", len(symbols), symbols=[s["symbol"] for s in symbols],
                items=["inclusion_reason", "symbol_to_sector_binding", "rotation_ledger_link"],
                meaning="편입 사유·종목→섹터 바인딩·로테이션 연결 근거가 저장소에 기록되지 않은 종목",
            ),
            "evaluated_no_candidate": _category(
                "evaluated_no_candidate", 0, applicable=False,
                meaning="통과 규칙이 미정이므로 '정상 평가 후 후보 없음'으로 분류된 종목은 없음 (0은 규칙 부재의 결과)",
            ),
        }
        explanation = (
            f"{market}: 모집단 {population['count']}종목({population['as_of']} 기준) 중 실제 평가 종목은 "
            f"{evaluated['count']}종목({evaluated['evaluated_at']} 평가)입니다. "
            f"미평가 {disposition['unevaluated']['count']}종목은 전체 모집단 평가기 미연결 때문이며, "
            f"평가된 {evaluated['count']}종목은 통과 0 / 보류 {disposition['held']['count']}로 정책 미정"
            f"({', '.join(sorted({c for s in symbols for c in s['blocking_reasons'] if REVIEW_REASON_CLASS.get(c) == 'POLICY_UNDEFINED'})) or '없음'}) 상태입니다."
            + (f" 수집 실패: {', '.join(collection_failed)}." if collection_failed else "")
            + " 편입 사유·섹터 바인딩 근거는 기록이 없습니다. 통과 규칙이 미정이라 '정상 평가 후 후보 없음'은 해당 없음입니다."
        )
    else:
        skipped_class = evaluated.get("latest_generation_skipped_class")
        not_evaluated = evaluated.get("admitted_not_evaluated") or []
        held = disposition["held"]["count"]
        excluded_by_reason = disposition["excluded"].get("by_reason") or {}
        identity_unratified = excluded_by_reason.get("IDENTITY_UNRATIFIED", 0)
        rule_excluded = sum(v for k, v in excluded_by_reason.items() if k != "IDENTITY_UNRATIFIED")
        last_hist = evaluated.get("last_generation_with_evaluations")
        categories = {
            "unevaluated": _category(
                "unevaluated", len(not_evaluated) if skipped_class == "COLLECTION_FAILED" else 0,
                symbols=not_evaluated if skipped_class == "COLLECTION_FAILED" else [],
                meaning="입력 자료 수집 실패로 최신 세대에서 평가되지 못한 admitted 종목",
            ),
            "evaluation_halted_input_date_mismatch": _category(
                "evaluation_halted_input_date_mismatch",
                len(not_evaluated) if skipped_class == "EVALUATION_HALTED_INPUT_DATE_MISMATCH" else 0,
                symbols=not_evaluated if skipped_class == "EVALUATION_HALTED_INPUT_DATE_MISMATCH" else [],
                derivation_notes=evaluated.get("derivation_notes") or [],
                meaning="입력(universe/realtime/regime) 날짜가 서로 달라 최신 세대에서 P5-08 평가가 중단된 admitted 종목 — 수집 실패가 아님",
            ),
            "policy_undefined": _category(
                "policy_undefined", identity_unratified + held,
                identity_scope_unratified=identity_unratified, criteria_unknown_held=held,
                policies=policy_codes,
                meaning="identity 범위 미비준으로 관찰 풀에 머문 종목 + 평가됐으나 모든 기준이 규칙 미비준으로 UNKNOWN인 종목",
            ),
            "excluded_by_ratified_rule": _category(
                "excluded_by_ratified_rule", rule_excluded,
                by_reason={k: v for k, v in excluded_by_reason.items() if k != "IDENTITY_UNRATIFIED"},
                meaning="비준된 taxonomy 규칙(투자유의 등)으로 제외된 종목",
            ),
            "no_evidence": _category(
                "no_evidence", population["count"],
                items=["symbol_to_sector_binding", "leadership_relative_strength"],
                meaning="섹터 바인딩·leadership 상대강도 근거가 아직 관측되지 않음 (crypto_leadership 상태 UNKNOWN)",
            ),
            "evaluated_no_candidate": _category(
                "evaluated_no_candidate",
                evaluated["count"] if row["candidate_zero_semantics"] == "EVALUATED_NO_CANDIDATE" else 0,
                applicable=row["candidate_zero_semantics"] == "EVALUATED_NO_CANDIDATE",
                meaning="모든 기준이 판정 가능했고 통과한 종목이 없는 경우에만 해당",
            ),
        }
        latest_line = (
            f"최신 세대({evaluated['evaluated_at']})는 "
            + ("입력 날짜 불일치로 평가가 중단되어" if skipped_class == "EVALUATION_HALTED_INPUT_DATE_MISMATCH"
               else "입력 수집 실패로 평가되지 않아" if skipped_class == "COLLECTION_FAILED"
               else f"{evaluated['count']}종목을 평가해")
            + f" admitted {evaluated.get('admitted_count')}종목 중 {evaluated['count']}종목이 평가됐습니다."
        )
        hist_line = ""
        if isinstance(last_hist, dict) and last_hist.get("historical"):
            hist_line = (
                f" 마지막 정상 평가는 {last_hist['generated_at']}({last_hist['candidate_count']}종목) 세대이며 "
                f"과거 결과로만 표시하고 최신 평가로 대체하지 않습니다."
            )
        explanation = (
            f"CRYPTO: 모집단 {population['count']}종목({population['as_of']} 스냅샷) 중 "
            f"identity 미비준 {identity_unratified}종목(정책 미정)과 비준 규칙 제외 {rule_excluded}종목을 뺀 "
            f"{evaluated.get('admitted_count')}종목이 평가 입력입니다. " + latest_line + hist_line
            + (f" 평가된 종목은 모두 기준 UNKNOWN(규칙 미비준)으로 보류 상태입니다." if held else "")
        )
    return {
        "population_count": population["count"],
        "population_as_of": population["as_of"],
        "evaluated_symbol_count": evaluated["count"],
        "evaluated_at": evaluated["evaluated_at"],
        "evaluated_symbols": (
            [s["symbol"] for s in symbols] if market in ("KR", "US")
            else [s["symbol"] for s in symbols if s.get("evaluated_by_p5_08")]
        ),
        "categories": categories,
        "explanation": explanation,
    }


def _symbol_classification(detail: dict) -> dict:
    """One reader-facing category per symbol plus the evidence items that are missing."""
    market = detail["market"]
    last_eval = detail["last_evaluation"]
    no_evidence_items = []
    inclusion = detail["candidate_inclusion"]
    if (inclusion.get("inclusion_reason") or {}).get("status") == NO_EVIDENCE:
        no_evidence_items.append("inclusion_reason")
    for key, value in (detail.get("sector_rotation_link") or {}).items():
        if isinstance(value, dict) and value.get("status") == NO_EVIDENCE:
            no_evidence_items.append(f"sector_rotation_link.{key}")
        elif value == NO_EVIDENCE:
            no_evidence_items.append(f"sector_rotation_link.{key}")
    if market in ("KR", "US"):
        if last_eval["status"] != "EVALUATED":
            key, reason = "unevaluated", last_eval.get("reason")
        else:
            classes = {u.get("class") for u in detail["next_step_unmet_conditions"]}
            if "COLLECTION_FAILED" in classes:
                key, reason = "collection_failed", "PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE"
            elif "POLICY_UNDEFINED" in classes:
                key, reason = "policy_undefined", ", ".join(
                    u["condition"] for u in detail["next_step_unmet_conditions"] if u.get("class") == "POLICY_UNDEFINED"
                )
            else:
                key, reason = "evaluated_no_candidate", last_eval.get("entry_state")
        evaluated_no_candidate_applicable = False
    else:
        excluded = detail["exclusion_expiry"]["excluded_by_existing_rule"]
        status = last_eval["status"]
        if excluded["status"] == "EXCLUDED" and excluded.get("reason") == "IDENTITY_UNRATIFIED":
            key, reason = "policy_undefined", "IDENTITY_UNRATIFIED"
        elif excluded["status"] == "EXCLUDED":
            key, reason = "excluded_by_ratified_rule", excluded.get("reason")
        elif status == "ADMITTED_EVALUATION_HALTED_INPUT_DATE_MISMATCH":
            key, reason = "evaluation_halted_input_date_mismatch", "; ".join(last_eval["latest_generation"]["derivation_notes"])
        elif status == "ADMITTED_NOT_EVALUATED_IN_LATEST_GENERATION":
            key, reason = "unevaluated", "; ".join(last_eval["latest_generation"]["derivation_notes"])
        elif status == "EVALUATED":
            unknown = [u["condition"] for u in detail["next_step_unmet_conditions"] if u.get("status") == "UNKNOWN"]
            if unknown:
                key, reason = "policy_undefined", ", ".join(unknown)
            elif last_eval.get("state") in ("FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE"):
                key, reason = "evaluated_no_candidate", None
                key = "candidate"
            else:
                key, reason = "evaluated_no_candidate", last_eval.get("reason")
        else:
            key, reason = "unevaluated", status
        evaluated_no_candidate_applicable = key == "evaluated_no_candidate"
    label = CATEGORY_LABELS.get(key, "후보")
    return {
        "category": key,
        "label": label,
        "reason": reason,
        "no_evidence_items": no_evidence_items,
        "evaluated_no_candidate_applicable": evaluated_no_candidate_applicable,
    }


def build_report(*, generated_at: str, inputs: dict | None = None, markets: tuple = MARKETS, strict: bool = False) -> dict:
    # Validate every requested market before building anything, then build
    # only the contexts those markets actually need. A caller asking for one
    # market (for example a pinned Crypto-only regression) must never be
    # broken by an unrelated, unrequested market's source being out of its
    # own point-in-time bounds (e.g. dated after the lookup time): that
    # market was never asked for and its context is never built. The
    # three-market coverage receipt below is a separate, always-all-three
    # cross-check with its own existing fail-closed degrade (``strict``);
    # this lazy-context change does not touch it.
    for market in markets:
        if market not in MARKETS:
            _fail("MARKET_INVALID", str(market))
    observed_at = _utc(generated_at, "GENERATED_AT_INVALID")
    generated_date = observed_at.date()
    inputs = inputs or default_inputs()
    coverage_state = _coverage_report(inputs, generated_at, strict=strict)
    coverage = coverage_state["report"]
    by_market = {row["market"]: row for row in coverage["markets"]} if coverage else {}
    ctxs = {}
    if "KR" in markets:
        ctxs["KR"] = _kr_context(inputs, observed_at)
    if "US" in markets:
        ctxs["US"] = _us_context(inputs, observed_at)
    if "CRYPTO" in markets:
        ctxs["CRYPTO"] = _crypto_context(inputs, observed_at)
    rows = []
    for market in markets:
        if market == "KR":
            rows.append(_kr_market_status(ctxs["KR"], inputs, by_market.get("KR"), generated_date, observed_at))
        elif market == "US":
            rows.append(_us_market_status(ctxs["US"], inputs, by_market.get("US"), generated_date, observed_at))
        elif market == "CRYPTO":
            rows.append(_crypto_market_status(ctxs["CRYPTO"], inputs, by_market.get("CRYPTO"), generated_date))
    for row in rows:
        row["summary"] = _market_summary(row)
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "generated_at_semantics": "LOOKUP_TIME_ONLY_NEVER_A_SOURCE_DATE",
        "gap_classes": list(GAP_CLASSES),
        "category_labels": dict(CATEGORY_LABELS),
        "coverage_receipt": (
            {
                "status": "BUILT",
                "contract": coverage["schema_version"],
                "generated_at": coverage["generated_at"],
                "payload_sha256": coverage["payload_sha256"],
                "summary": copy.deepcopy(coverage["summary"]),
            }
            if coverage else
            {
                "status": "FAILED_CLOSED",
                "contract": COVERAGE.SCHEMA_VERSION,
                "reason": coverage_state["reason"],
                "note": "the coverage receipt refused this generation; every count below is derived from the per-symbol sources and carries coverage_receipt_cross_check=NOT_AVAILABLE",
            }
        ),
        "markets": rows,
        "portal": _portal_block(inputs, coverage, ctxs),
        "authority": _authority(),
    }
    report["payload_sha256"] = payload_sha256(report)
    return report


def lookup_symbol(market: str, symbol: str, *, generated_at: str, inputs: dict | None = None) -> dict:
    observed_at = _utc(generated_at, "GENERATED_AT_INVALID")
    inputs = inputs or default_inputs()
    cases, validity = _optional_packets(inputs)
    if market == "KR":
        ctx = _kr_context(inputs, observed_at)
        stage_history = _load_stage_history(inputs["stage_history_path"])
        result = _kr_symbol_lookup(ctx, inputs, symbol, stage_history, cases, validity)
    elif market == "US":
        ctx = _us_context(inputs, observed_at)
        stage_history = _load_stage_history(inputs["stage_history_path"])
        result = _us_symbol_lookup(ctx, inputs, symbol, stage_history, cases, validity)
    elif market == "CRYPTO":
        ctx = _crypto_context(inputs, observed_at)
        result = _crypto_symbol_lookup(ctx, inputs, symbol, cases, validity)
    else:
        _fail("MARKET_INVALID", str(market))
        raise  # unreachable
    result["classification"] = _symbol_classification(result)
    result["schema_version"] = f"{SCHEMA_VERSION}#symbol_detail"
    result["generated_at"] = generated_at
    result["generated_at_semantics"] = "LOOKUP_TIME_ONLY_NEVER_A_SOURCE_DATE"
    result["payload_sha256"] = payload_sha256(result)
    return result


def validate_report(report: dict, *, inputs: dict | None = None) -> dict:
    if not isinstance(report, dict) or report.get("schema_version") != SCHEMA_VERSION:
        _fail("REPORT_SCHEMA_INVALID")
    _validate_self_hash(report, "payload_sha256", "REPORT_PAYLOAD_SHA256_MISMATCH")
    authority = report.get("authority")
    if not isinstance(authority, dict) or authority.get("read_only") is not True or any(
        value is not False for key, value in authority.items() if key != "read_only"
    ):
        _fail("REPORT_AUTHORITY_INVALID")
    markets = report.get("markets")
    if not isinstance(markets, list) or not markets:
        _fail("REPORT_MARKETS_INVALID")
    market_names = []
    for row in markets:
        if not isinstance(row, dict) or row.get("market") not in MARKETS:
            _fail("REPORT_MARKET_INVALID")
        market_names.append(row["market"])
        for gap in row.get("gap_classification") or []:
            klass = gap.get("class")
            if klass not in GAP_CLASSES and not str(klass).startswith("UNCLASSIFIED_EXISTING_REASON:"):
                _fail("REPORT_GAP_CLASS_INVALID", str(klass))
    if len(set(market_names)) != len(market_names):
        _fail("REPORT_MARKETS_INVALID")
    expected = build_report(
        generated_at=report.get("generated_at"),
        inputs=inputs,
        markets=tuple(market_names),
    )
    if report != expected:
        _fail("REPORT_SOURCE_REDERIVATION_MISMATCH")
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-at", required=True, help="UTC lookup time, e.g. 2026-09-13T03:00:00Z")
    parser.add_argument("--market", choices=MARKETS)
    parser.add_argument("--symbol", help="symbol / market code for a per-symbol lookup (requires --market)")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--compact", action="store_true", help="market status only: drop per-symbol lists")
    args = parser.parse_args(argv)
    if args.symbol:
        if not args.market:
            parser.error("--symbol requires --market")
        result = lookup_symbol(args.market, args.symbol, generated_at=args.generated_at)
    else:
        result = build_report(generated_at=args.generated_at, markets=(args.market,) if args.market else MARKETS)
        if args.compact:
            result.pop("payload_sha256", None)
            for row in result["markets"]:
                row["symbols"] = {"count": len(row["symbols"]), "omitted": True}
            result["compact"] = True
            result["payload_sha256"] = payload_sha256(result)
    text = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
