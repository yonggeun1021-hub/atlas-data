#!/usr/bin/env python3
"""Read-only KR/US/CRYPTO population-to-evaluation coverage receipt.

This module does not scan, rank, or create candidates.  It validates the
existing source-coverage universes and the current market outputs, then
reports only counts those sources actually establish.  Bounded review
symbols are never projected as full-population evaluation counts.
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


ROOT = Path(__file__).resolve().parents[1]
NOT_COUNTED = "미집계"
SCHEMA_VERSION = "three_market_evaluation_coverage/1"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class ThreeMarketEvaluationCoverageError(ValueError):
    """A source or derived coverage claim failed closed."""


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"SOURCE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GLOBAL_ASSET_MASTER = _load_module(
    "coverage_global_asset_master", "universe/global_asset_master.py"
)
KOREA_REVIEW = _load_module(
    "coverage_korea_symbol_market_review", "decision/korea_symbol_market_review.py"
)
US_REVIEW = _load_module(
    "coverage_us_symbol_market_review", "decision/us_symbol_market_review.py"
)
US_INVESTABLE_REGISTRY = _load_module(
    "coverage_us_investable_registry", "universe/us_investable_registry.py"
)
CRYPTO_DECISION = _load_module(
    "coverage_crypto_paper_decision", "decision/crypto_paper_decision_snapshot.py"
)


def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
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
        raise ThreeMarketEvaluationCoverageError(code) from exc
    if not isinstance(value, dict):
        raise ThreeMarketEvaluationCoverageError(code)
    return value


def _validate_self_hash(value: dict, field: str, code: str) -> None:
    claimed = value.get(field)
    unsigned = copy.deepcopy(value)
    unsigned.pop(field, None)
    if not isinstance(claimed, str) or payload_sha256(unsigned) != claimed:
        raise ThreeMarketEvaluationCoverageError(code)


def _require_not_future(value: str, observed_at: dt.datetime, code: str) -> None:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ThreeMarketEvaluationCoverageError(code)
    parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=dt.timezone.utc
    )
    if parsed > observed_at:
        raise ThreeMarketEvaluationCoverageError(code)


def _require_date_not_future(value: str, observed_at: dt.datetime, code: str) -> None:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ThreeMarketEvaluationCoverageError(code) from exc
    if parsed.isoformat() != value or parsed > observed_at.date():
        raise ThreeMarketEvaluationCoverageError(code)


def _source_ref(path: Path, packet_sha256: str) -> dict:
    try:
        relative = Path(path).resolve().relative_to(ROOT.resolve()).as_posix()
    except ValueError as exc:
        raise ThreeMarketEvaluationCoverageError("SOURCE_PATH_OUTSIDE_REPOSITORY") from exc
    return {
        "path": relative,
        "file_sha256": _file_sha256(path),
        "packet_sha256": packet_sha256,
    }


def _validated_global_universe(
    path: Path, market: str, observed_at: dt.datetime,
) -> tuple[dict, int]:
    wrapper = _read_json(path, f"{market}_UNIVERSE_READ_FAILED")
    if market == "KR":
        packet = wrapper
        expected_schema = "krx_global_universe_packet/1"
        expected_status = "SOURCE_COVERAGE_UNIVERSE_VALIDATED"
    elif market == "US":
        if wrapper.get("schema_version") != "us_forward_universe_population/2":
            raise ThreeMarketEvaluationCoverageError("US_UNIVERSE_WRAPPER_INVALID")
        _validate_self_hash(wrapper, "payload_sha256", "US_UNIVERSE_WRAPPER_HASH_MISMATCH")
        _require_not_future(
            wrapper.get("generated_at"), observed_at, "US_UNIVERSE_FROM_FUTURE"
        )
        packet = wrapper.get("packet")
        expected_schema = "us_global_universe_packet/1"
        expected_status = "FORWARD_SOURCE_COVERAGE_UNIVERSE_VALIDATED"
    else:
        raise ThreeMarketEvaluationCoverageError("GLOBAL_UNIVERSE_MARKET_INVALID")
    if not isinstance(packet, dict):
        raise ThreeMarketEvaluationCoverageError(f"{market}_UNIVERSE_PACKET_INVALID")
    _validate_self_hash(packet, "payload_sha256", f"{market}_UNIVERSE_HASH_MISMATCH")
    try:
        master = GLOBAL_ASSET_MASTER.validate_packet(packet.get("asset_master"))
    except GLOBAL_ASSET_MASTER.GlobalAssetMasterError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"{market}_ASSET_MASTER_INVALID:{exc}"
        ) from exc
    _require_date_not_future(
        packet.get("as_of_date"), observed_at, f"{market}_UNIVERSE_FROM_FUTURE"
    )
    source_snapshots = packet.get("source_snapshots")
    if not isinstance(source_snapshots, list) or not source_snapshots:
        raise ThreeMarketEvaluationCoverageError(
            f"{market}_UNIVERSE_SOURCE_SNAPSHOTS_INVALID"
        )
    source_count = 0
    count_field = "universe_count" if market == "KR" else "record_count"
    for source in source_snapshots:
        if not isinstance(source, dict) or type(source.get(count_field)) is not int:
            raise ThreeMarketEvaluationCoverageError(
                f"{market}_UNIVERSE_SOURCE_SNAPSHOTS_INVALID"
            )
        _require_not_future(
            source.get("retrieved_at_utc"), observed_at,
            f"{market}_UNIVERSE_SOURCE_FROM_FUTURE",
        )
        source_count += source[count_field]
    count = packet.get("total_count")
    if (
        packet.get("schema_version") != expected_schema
        or packet.get("status") != expected_status
        or packet.get("as_of_date") != master.get("as_of_date")
        or type(count) is not int
        or count < 0
        or count != master.get("record_count")
        or count != len(master.get("records", []))
        or count != source_count
    ):
        raise ThreeMarketEvaluationCoverageError(f"{market}_UNIVERSE_COUNT_INVALID")
    return packet, count


def _validated_crypto_universe(path: Path, generated_at: dt.datetime) -> tuple[dict, int]:
    record = _read_json(path, "CRYPTO_UNIVERSE_READ_FAILED")
    entry = {
        "date": record.get("snapshot_date"),
        "path": Path(path),
        "record": record,
        "packet": record.get("packet"),
    }
    try:
        CRYPTO_DECISION._validate_universe_entry(entry, not_after=generated_at)
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_UNIVERSE_INVALID:{exc}"
        ) from exc
    packet = record["packet"]
    summary = packet.get("summary")
    markets = packet.get("markets")
    if (
        not isinstance(summary, dict)
        or not isinstance(markets, list)
        or type(summary.get("market_count")) is not int
        or summary["market_count"] != len(markets)
        or sum(
            summary.get(key, -1)
            for key in (
                "observation_pool_count", "tradeable_universe_count",
                "paper_eligible_count", "blocked_count",
            )
        ) != len(markets)
    ):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_UNIVERSE_COUNT_INVALID")
    market_codes = []
    state_counts = Counter()
    for row in markets:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("market"), str)
            or row.get("state") not in {
                "OBSERVATION_POOL", "TRADEABLE_UNIVERSE",
                "PAPER_ELIGIBLE", "BLOCKED",
            }
            or not isinstance(row.get("reason"), str)
            or not row["reason"]
        ):
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_UNIVERSE_MARKET_STATE_INVALID"
            )
        try:
            CRYPTO_DECISION._require_all_false(row.get("authority"))
        except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
            raise ThreeMarketEvaluationCoverageError(
                f"CRYPTO_UNIVERSE_MARKET_AUTHORITY_INVALID:{exc}"
            ) from exc
        market_codes.append(row["market"])
        state_counts[row["state"]] += 1
    if len(market_codes) != len(set(market_codes)):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_UNIVERSE_MARKET_DUPLICATE")
    expected_state_counts = {
        "OBSERVATION_POOL": summary["observation_pool_count"],
        "TRADEABLE_UNIVERSE": summary["tradeable_universe_count"],
        "PAPER_ELIGIBLE": summary["paper_eligible_count"],
        "BLOCKED": summary["blocked_count"],
    }
    if {
        state: state_counts.get(state, 0) for state in expected_state_counts
    } != expected_state_counts:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_UNIVERSE_STATE_COUNTS_INVALID"
        )
    return record, summary["market_count"]


def _validated_review(path: Path, market: str, observed_at: dt.datetime) -> dict:
    value = _read_json(path, f"{market}_REVIEW_READ_FAILED")
    try:
        if market == "KR":
            checked = KOREA_REVIEW.validate_output(value)
        elif market == "US":
            checked = US_REVIEW.validate_output(value)
        else:
            raise ThreeMarketEvaluationCoverageError("REVIEW_MARKET_INVALID")
    except (KOREA_REVIEW.KoreaSymbolMarketReviewError,
            US_REVIEW.UsSymbolMarketReviewError) as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"{market}_BOUNDED_REVIEW_INVALID:{exc}"
        ) from exc
    _require_not_future(
        checked.get("generated_at"), observed_at, f"{market}_REVIEW_FROM_FUTURE"
    )
    return checked


def _validated_crypto_decision(path: Path, observed_at: dt.datetime) -> dict:
    try:
        checked = CRYPTO_DECISION.validate_output(
            _read_json(path, "CRYPTO_DECISION_READ_FAILED")
        )
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_DECISION_INVALID:{exc}"
        ) from exc
    _require_not_future(
        checked.get("generated_at"), observed_at, "CRYPTO_DECISION_FROM_FUTURE"
    )
    return checked


def _crypto_held_reason_analysis(candidates: list[dict]) -> dict:
    """Classify the owning evaluator's exact UNKNOWN reasons, never scores."""
    policy_reasons = {
        "REGIME_AGGREGATE_UNAUTHORIZED_PENDING_P1_COM_05",
        "NO_RATIFIED_CANDIDATE_TREND_RULE",
        "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED",
        "NO_RATIFIED_OVEREXTENSION_THRESHOLD",
        "BTC_SELF_REFERENCE_RULE_UNRATIFIED",
        "PEER_RELATIVE_STRENGTH_UNRATIFIED",
    }
    data_reasons = {
        "SECURITY_AND_NETWORK_OUTAGE_COVERAGE_MISSING",
        "INSUFFICIENT_FINALIZED_CANDLES",
        "EVIDENCE_FAMILY_INCOMPLETE",
        "LEADERSHIP_ASSET_WINDOW_INCOMPLETE",
        "LEADERSHIP_ASSET_NOT_COVERED",
    }
    connection_reasons = {
        "MARKET_EVIDENCE_PACKET_MISSING",
        "LEADERSHIP_OUTPUT_MISSING",
    }
    categories = (
        "UNRATIFIED_POLICY_OR_AUTHORITY",
        "SOURCE_DATA_OR_COVERAGE_INSUFFICIENT",
        "CONSUMER_INPUT_NOT_CONNECTED",
        "OTHER_UNKNOWN_REASON",
    )
    occurrence_counts = Counter()
    reason_counts = Counter()
    markets_by_category = {category: set() for category in categories}
    held_rows = [row for row in candidates if row.get("state") in {"WATCH", "WAIT"}]
    for row in held_rows:
        criteria = (row.get("p5_08") or {}).get("criteria")
        if not isinstance(criteria, dict):
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_HELD_CRITERIA_INVALID"
            )
        for criterion, result in criteria.items():
            if not isinstance(result, dict) or result.get("status") not in {
                "PASS", "FAIL", "UNKNOWN",
            }:
                raise ThreeMarketEvaluationCoverageError(
                    "CRYPTO_HELD_CRITERIA_INVALID"
                )
            if result["status"] != "UNKNOWN":
                continue
            reason = result.get("reason")
            if not isinstance(reason, str) or not reason:
                raise ThreeMarketEvaluationCoverageError(
                    "CRYPTO_HELD_REASON_INVALID"
                )
            if reason in policy_reasons:
                category = "UNRATIFIED_POLICY_OR_AUTHORITY"
            elif reason in data_reasons or reason.startswith(
                "LEADERSHIP_WINDOW_UNKNOWN:"
            ):
                category = "SOURCE_DATA_OR_COVERAGE_INSUFFICIENT"
            elif reason in connection_reasons:
                category = "CONSUMER_INPUT_NOT_CONNECTED"
            else:
                category = "OTHER_UNKNOWN_REASON"
            occurrence_counts[category] += 1
            reason_counts[f"{criterion}:{reason}"] += 1
            markets_by_category[category].add(row["market"])
    return {
        "count_semantics": "UNKNOWN_CRITERION_OCCURRENCES_NOT_DISTINCT_MARKETS",
        "held_market_count": len(held_rows),
        "criterion_occurrence_counts": {
            category: occurrence_counts.get(category, 0)
            for category in categories
        },
        "affected_market_counts": {
            category: len(markets_by_category[category])
            for category in categories
        },
        "reason_counts": dict(sorted(reason_counts.items())),
    }


def _base_market_row(
    market: str, universe_count: int, universe_ref: dict, review_count: int,
    review_ref: dict,
) -> dict:
    return {
        "market": market,
        "universe_count": universe_count,
        "source_valid_count": universe_count,
        "bounded_current_output_count": review_count,
        "evaluation_input_count": NOT_COUNTED,
        "evaluated_count": NOT_COUNTED,
        "candidate_count": NOT_COUNTED,
        "candidate_count_semantics": "NOT_COUNTED_NO_POPULATION_CANDIDATE_OUTPUT",
        "held_count": NOT_COUNTED,
        "excluded_count": NOT_COUNTED,
        "paper_ready_count": NOT_COUNTED,
        "coverage_status": "BOUNDED_OUTPUT_NOT_FULL_POPULATION_EVALUATION",
        "missing_reasons": [
            "FULL_POPULATION_EVALUATION_INPUT_NOT_CONNECTED",
            "FULL_POPULATION_CANDIDATE_AND_EXCLUSION_COUNTS_NOT_EMITTED",
        ],
        "state_lifetime": {
            "snapshot_only": True,
            "automatic_carry_forward": False,
            "reevaluation_required": True,
        },
        "sources": {"universe": universe_ref, "current_output": review_ref},
    }


def build_report(
    *, generated_at: str, kr_universe_path: Path, kr_review_path: Path,
    us_universe_path: Path, us_review_path: Path, crypto_universe_path: Path,
    crypto_decision_path: Path,
) -> dict:
    if not isinstance(generated_at, str) or UTC_RE.fullmatch(generated_at) is None:
        raise ThreeMarketEvaluationCoverageError("GENERATED_AT_INVALID")
    try:
        observed_at = dt.datetime.strptime(
            generated_at, "%Y-%m-%dT%H:%M:%SZ"
        ).replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ThreeMarketEvaluationCoverageError("GENERATED_AT_INVALID") from exc
    kr_universe, kr_count = _validated_global_universe(
        Path(kr_universe_path), "KR", observed_at
    )
    us_universe, us_count = _validated_global_universe(
        Path(us_universe_path), "US", observed_at
    )
    crypto_universe, crypto_count = _validated_crypto_universe(
        Path(crypto_universe_path), observed_at
    )

    kr_review = _validated_review(Path(kr_review_path), "KR", observed_at)
    us_review = _validated_review(Path(us_review_path), "US", observed_at)
    crypto_decision = _validated_crypto_decision(
        Path(crypto_decision_path), observed_at
    )
    crypto_universe_ref = next(
        (
            ref for ref in crypto_decision["source_refs"]
            if ref["role"] == "upbit_tradeable_universe_packet"
        ),
        None,
    )
    if crypto_universe_ref is None:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_DECISION_UNIVERSE_REF_MISSING"
        )
    decision_universe = _read_json(
        ROOT / crypto_universe_ref["path"],
        "CRYPTO_DECISION_UNIVERSE_SOURCE_READ_FAILED",
    )
    if decision_universe != crypto_universe:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_DECISION_UNIVERSE_SOURCE_MISMATCH"
        )

    kr_row = _base_market_row(
        "KR", kr_count,
        _source_ref(kr_universe_path, kr_universe["payload_sha256"]),
        kr_review["summary"]["symbol_count"],
        _source_ref(kr_review_path, kr_review["packet_sha256"]),
    )
    us_row = _base_market_row(
        "US", us_count,
        _source_ref(us_universe_path, us_universe["payload_sha256"]),
        us_review["summary"]["symbol_count"],
        _source_ref(us_review_path, us_review["packet_sha256"]),
    )
    us_symbols = us_review.get("symbols")
    if (
        not isinstance(us_symbols, list)
        or len(us_symbols) != us_review["summary"]["symbol_count"]
    ):
        raise ThreeMarketEvaluationCoverageError("US_BOUNDED_REVIEW_SYMBOLS_INVALID")
    us_entry_state_counts = Counter()
    us_entry_reason_counts = Counter()
    for row in us_symbols:
        entry_review = row.get("entry_review") if isinstance(row, dict) else None
        if (
            not isinstance(entry_review, dict)
            or not isinstance(entry_review.get("state"), str)
            or not isinstance(entry_review.get("reasons"), list)
            or any(
                not isinstance(reason, str) or not reason
                for reason in entry_review["reasons"]
            )
        ):
            raise ThreeMarketEvaluationCoverageError(
                "US_BOUNDED_REVIEW_DISPOSITION_INVALID"
            )
        us_entry_state_counts[entry_review["state"]] += 1
        us_entry_reason_counts.update(entry_review["reasons"])
    us_registry_contract = US_INVESTABLE_REGISTRY.load_contract()
    if (
        us_registry_contract.get("source_coverage_is_investability") is not False
        or (us_registry_contract.get("liquidity") or {}).get(
            "repository_default_policy"
        )
        != "ABSENT"
    ):
        raise ThreeMarketEvaluationCoverageError(
            "US_INVESTABLE_REGISTRY_BOUNDARY_INVALID"
        )
    us_row.update({
        "bounded_output_scope": (
            "SUPPORTED_PIPELINE_SUBJECTS_ONLY_NOT_POPULATION_EVALUATION"
        ),
        "bounded_output_state_counts": dict(
            sorted(us_entry_state_counts.items())
        ),
        "bounded_output_reason_counts": dict(
            sorted(us_entry_reason_counts.items())
        ),
        "population_evaluation_connection": {
            "status": "NOT_CONNECTED",
            "required_input_schema": "us_investable_snapshot/1",
            "existing_evaluator_output_schema": "us_investable_registry_result/1",
            "connected_source_universe_is_investability": False,
            "required_fail_closed_facts": copy.deepcopy(
                us_registry_contract["required_fail_closed_facts"]
            ),
            "liquidity_policy_status": "ABSENT_EXTERNAL_RATIFIED_POLICY_REQUIRED",
            "reason": (
                "NATURAL_POPULATION_INPUT_AND_LIQUIDITY_POLICY_NOT_CONNECTED"
            ),
        },
    })

    funnel = crypto_decision.get("funnel_counts")
    candidates = crypto_decision.get("candidates")
    if not isinstance(funnel, dict) or not isinstance(candidates, list):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_COUNTS_INVALID")
    evaluation_input_count = funnel.get("tradeable_universe_count")
    candidate_count = funnel.get("focused_review_count")
    paper_ready_count = funnel.get("paper_ready_count")
    observation_pool_count = funnel.get("observation_pool_count")
    universe_markets = crypto_universe["packet"]["markets"]
    admitted_rows = [
        row for row in universe_markets
        if row["state"] in {"TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE"}
    ]
    excluded_rows = [
        row for row in universe_markets
        if row["state"] in {"OBSERVATION_POOL", "BLOCKED"}
    ]
    candidate_markets = [row.get("market") for row in candidates]
    admitted_markets = [row["market"] for row in admitted_rows]
    if (
        any(type(value) is not int or value < 0 for value in (
            evaluation_input_count, candidate_count, paper_ready_count,
            observation_pool_count,
        ))
        or evaluation_input_count != len(candidates)
        or evaluation_input_count != len(admitted_rows)
        or observation_pool_count != sum(
            row["state"] == "OBSERVATION_POOL" for row in universe_markets
        )
        or len(excluded_rows) + evaluation_input_count != crypto_count
        or len(candidate_markets) != len(set(candidate_markets))
        or sorted(candidate_markets) != sorted(admitted_markets)
    ):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_COUNTS_INVALID")
    held_count = sum(row.get("state") in {"WATCH", "WAIT"} for row in candidates)
    if candidate_count + paper_ready_count + held_count != evaluation_input_count:
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_STATE_COUNTS_INVALID")
    excluded_reason_counts = dict(sorted(Counter(
        row["reason"] for row in excluded_rows
    ).items()))
    excluded_state_counts = {
        state: sum(row["state"] == state for row in excluded_rows)
        for state in ("OBSERVATION_POOL", "BLOCKED")
    }
    identity_registry = CRYPTO_DECISION.UNIVERSE.load_identity_registry()
    identity_scope = identity_registry.get("scope")
    if identity_scope != "UPBIT_KRW_SPOT_CRYPTO_PAPER_EIGHT_ONLY":
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_IDENTITY_APPROVAL_SCOPE_INVALID"
        )
    effective_identity_mapping = (
        CRYPTO_DECISION.UNIVERSE.effective_identity_mapping(
            identity_registry, crypto_universe["packet"]["evaluation_as_of"]
        )
    )
    if set(admitted_markets) != set(effective_identity_mapping):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_ADMITTED_IDENTITY_SCOPE_MISMATCH"
        )
    pre_evaluation_reason_classification = {
        "IDENTITY_UNRATIFIED": {
            "market_count": excluded_reason_counts.get("IDENTITY_UNRATIFIED", 0),
            "classification": "OUTSIDE_RATIFIED_IDENTITY_SCOPE",
            "approval_scope": identity_scope,
            "interpretation": (
                "NOT_AN_INVESTMENT_CONDITION_FAILURE"
            ),
        },
        "INVESTMENT_WARNING_ACTIVE": {
            "market_count": excluded_reason_counts.get(
                "INVESTMENT_WARNING_ACTIVE", 0
            ),
            "classification": "SAFETY_EXCLUSION",
            "interpretation": (
                "UPBIT_CAUTION_HARD_EXCLUSION_NOT_A_CANDIDATE_SCORE"
            ),
        },
    }
    if sum(
        row["market_count"] for row in pre_evaluation_reason_classification.values()
    ) != len(excluded_rows):
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_PRE_EVALUATION_REASON_UNCLASSIFIED"
        )
    held_reason_analysis = _crypto_held_reason_analysis(candidates)
    if held_reason_analysis["held_market_count"] != held_count:
        raise ThreeMarketEvaluationCoverageError(
            "CRYPTO_HELD_REASON_COUNT_INVALID"
        )
    crypto_row = {
        "market": "CRYPTO",
        "universe_count": crypto_count,
        "source_valid_count": crypto_count,
        "bounded_current_output_count": evaluation_input_count,
        "evaluation_input_count": evaluation_input_count,
        "evaluated_count": len(candidates),
        "candidate_count": candidate_count,
        "candidate_count_semantics": "SOURCE_FOCUSED_REVIEW_COUNT",
        "held_count": held_count,
        "excluded_count": len(excluded_rows),
        "excluded_count_semantics": (
            "SOURCE_MEMBERS_NOT_ADMITTED_TO_CURRENT_EVALUATION_INPUT"
        ),
        "excluded_state_counts": excluded_state_counts,
        "excluded_reason_counts": excluded_reason_counts,
        "pre_evaluation_reason_classification": (
            pre_evaluation_reason_classification
        ),
        "held_reason_analysis": held_reason_analysis,
        "paper_ready_count": paper_ready_count,
        "coverage_status": "SOURCE_POPULATION_EVALUATION_DISPOSITION_ACCOUNTED",
        "missing_reasons": [],
        "observation_pool_count": observation_pool_count,
        "state_lifetime": {
            "snapshot_only": True,
            "automatic_carry_forward": False,
            "reevaluation_required": True,
        },
        "sources": {
            "universe": _source_ref(
                crypto_universe_path, crypto_universe["payload_sha256"]
            ),
            "current_output": _source_ref(
                crypto_decision_path, crypto_decision["payload_sha256"]
            ),
        },
    }
    report = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": generated_at,
        "markets": [kr_row, us_row, crypto_row],
        "summary": {
            "market_count": 3,
            "full_population_universe_count_available": 3,
            "full_population_evaluation_count_available": 0,
            "bounded_subset_evaluation_count_available": 1,
            "full_population_evaluation_disposition_available": 1,
        },
        "authority": {
            "candidate_creation_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
        },
    }
    report["payload_sha256"] = payload_sha256(report)
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--kr-universe", type=Path, required=True)
    parser.add_argument("--kr-review", type=Path, required=True)
    parser.add_argument("--us-universe", type=Path, required=True)
    parser.add_argument("--us-review", type=Path, required=True)
    parser.add_argument("--crypto-universe", type=Path, required=True)
    parser.add_argument("--crypto-decision", type=Path, required=True)
    args = parser.parse_args(argv)
    report = build_report(
        generated_at=args.generated_at,
        kr_universe_path=args.kr_universe,
        kr_review_path=args.kr_review,
        us_universe_path=args.us_universe,
        us_review_path=args.us_review,
        crypto_universe_path=args.crypto_universe,
        crypto_decision_path=args.crypto_decision,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
