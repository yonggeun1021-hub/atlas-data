#!/usr/bin/env python3
"""Read-only KR/US/CRYPTO population-to-evaluation coverage receipt.

This module does not scan, rank, or create candidates.  It validates the
existing source-coverage universes and the current market outputs, then
reports only counts those sources actually establish.  Bounded review
symbols are never projected as full-population evaluation counts.
"""
from __future__ import annotations

import argparse
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
    checked = _read_json(path, "CRYPTO_DECISION_READ_FAILED")
    if checked.get("schema_version") != "crypto_paper_decision_snapshot_packet/1":
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_SCHEMA_INVALID")
    _validate_self_hash(
        checked, "payload_sha256", "CRYPTO_DECISION_HASH_MISMATCH"
    )
    try:
        CRYPTO_DECISION._require_all_false(checked.get("authority"))
    except CRYPTO_DECISION.CryptoPaperDecisionSnapshotError as exc:
        raise ThreeMarketEvaluationCoverageError(
            f"CRYPTO_DECISION_AUTHORITY_INVALID:{exc}"
        ) from exc
    refs = checked.get("source_refs")
    if not isinstance(refs, list) or not refs:
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_SOURCE_REFS_INVALID")
    for ref in refs:
        if (
            not isinstance(ref, dict)
            or set(ref) != {"role", "path", "sha256"}
            or not isinstance(ref.get("path"), str)
            or not isinstance(ref.get("sha256"), str)
        ):
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_DECISION_SOURCE_REF_INVALID"
            )
        source_path = (ROOT / ref["path"]).resolve()
        try:
            source_path.relative_to(ROOT.resolve())
        except ValueError as exc:
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_DECISION_SOURCE_PATH_INVALID"
            ) from exc
        if _file_sha256(source_path) != ref["sha256"]:
            raise ThreeMarketEvaluationCoverageError(
                "CRYPTO_DECISION_SOURCE_HASH_MISMATCH"
            )
    _require_not_future(
        checked.get("generated_at"), observed_at, "CRYPTO_DECISION_FROM_FUTURE"
    )
    return checked


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

    funnel = crypto_decision.get("funnel_counts")
    candidates = crypto_decision.get("candidates")
    if not isinstance(funnel, dict) or not isinstance(candidates, list):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_COUNTS_INVALID")
    evaluation_input_count = funnel.get("tradeable_universe_count")
    candidate_count = funnel.get("focused_review_count")
    paper_ready_count = funnel.get("paper_ready_count")
    observation_pool_count = funnel.get("observation_pool_count")
    if (
        any(type(value) is not int or value < 0 for value in (
            evaluation_input_count, candidate_count, paper_ready_count,
            observation_pool_count,
        ))
        or evaluation_input_count != len(candidates)
        or observation_pool_count + evaluation_input_count != crypto_count
    ):
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_COUNTS_INVALID")
    held_count = sum(row.get("state") in {"WATCH", "WAIT"} for row in candidates)
    if candidate_count + paper_ready_count + held_count != evaluation_input_count:
        raise ThreeMarketEvaluationCoverageError("CRYPTO_DECISION_STATE_COUNTS_INVALID")
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
        "excluded_count": NOT_COUNTED,
        "paper_ready_count": paper_ready_count,
        "coverage_status": "EVALUATED_SUBSET_COUNTS_AVAILABLE_EXCLUSIONS_NOT_EMITTED",
        "missing_reasons": ["FULL_POPULATION_EXCLUSION_COUNT_NOT_EMITTED"],
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
