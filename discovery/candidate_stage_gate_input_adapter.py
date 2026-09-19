#!/usr/bin/env python3
"""Fail-closed candidate_stage_gate_input/1 adapter for the five symbols the
bounded Korea and US market-native evaluator contracts already declare.

Scope (STAGE3-CANDIDATE-GATE-INPUT-ADAPTER-001): this adapter reuses
``decision.korea_symbol_market_review`` and ``decision.us_symbol_market_review``
unmodified -- their own ``load_contract()`` and ``validate_output()`` are the
only source of truth for what a current, hash-verified, authority-closed
market-native review packet looks like. This module never re-implements or
re-scores that evaluation logic.

Only ``market_native_evaluation_coverage`` is computed here, and only from
the existing review packet's own current-observation facts: whether the
bounded evaluator produced a complete five-axis reference and an observed,
confirmed price for the symbol. A validated review row establishes coverage;
it never establishes entry eligibility, evidence quality, Translation,
Expectations Gap, invalidation, freshness, active-veto, Ready, Buy, or
trading authority.

The remaining eight ratified stage gates -- canonical_population_membership,
resolved_security_identity, evidence_quality_status, translation_status,
expectations_gap_status, invalidation_status, freshness_status, and
active_veto_status -- have no per-symbol ratified current source connected
anywhere in this repository today (see
docs/candidate_evidence_lifecycle_receipt.md). This adapter always emits
MISSING for them and never infers, backfills, or defaults a value into PASS.

A manual Notion Watchlist Stage tag is never read, consumed, or required by
this adapter.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from decision import korea_symbol_market_review as korea_review  # noqa: E402
from decision import us_symbol_market_review as us_review  # noqa: E402
from discovery.candidate_evidence_lifecycle_receipt import (  # noqa: E402
    REQUIRED_STAGE_GATES,
    load_stage_evaluation_policy,
    normalize_symbol,
)

CONTRACT_VERSION = "candidate_stage_gate_input/1"
ADAPTER_VERSION = "candidate_stage_gate_input_adapter/1"
ADAPTER_IDENTITY = f"MECHANICAL_ADAPTER:{ADAPTER_VERSION}"
COVERAGE_GATE = "market_native_evaluation_coverage"

MISSING = "MISSING"
GATE_PASS = "PASS"
GATE_FAIL = "FAIL"

KOREA_LATEST_REVIEW_PATH = ROOT / "data" / "latest_korea_symbol_market_review.json"
US_LATEST_REVIEW_PATH = ROOT / "data" / "latest_us_symbol_market_review.json"


class CandidateStageGateInputAdapterError(ValueError):
    """Fail-closed candidate_stage_gate_input/1 adapter violation."""


def _fail(code: str) -> None:
    raise CandidateStageGateInputAdapterError(code)


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CandidateStageGateInputAdapterError(code) from exc
    if not isinstance(value, dict):
        _fail(f"{code}_NOT_OBJECT")
    return value


def _datetime(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str):
        _fail(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise CandidateStageGateInputAdapterError(code) from exc
    if parsed.tzinfo is None:
        _fail(code)
    return parsed


def _required_gates() -> tuple[str, ...]:
    policy = load_stage_evaluation_policy()["policy"]
    order = tuple(policy["required_gate_order"])
    if order != REQUIRED_STAGE_GATES or COVERAGE_GATE not in order:
        _fail("STAGE_GATE_ORDER_MISMATCH")
    return order


def _missing_gate() -> dict:
    return {"status": MISSING, "evidence_refs": []}


def _coverage_gate(status: str, ref: str, sha256: str, available_at_utc: str) -> dict:
    if status not in (GATE_PASS, GATE_FAIL):
        _fail("COVERAGE_STATUS_INVALID")
    return {
        "status": status,
        "evidence_refs": [
            {"ref": ref, "sha256": sha256, "available_at_utc": available_at_utc}
        ],
    }


def _build_gate_input(
    *,
    symbol: str,
    market: str,
    evaluation_at_utc: str,
    review_or_expiry_time_utc: str,
    market_native_evidence_contract: str,
    coverage_status: str,
    evidence_ref: str,
    evidence_sha256: str,
    evidence_available_at_utc: str,
    required_gates: tuple[str, ...],
) -> dict:
    gates = {name: _missing_gate() for name in required_gates}
    gates[COVERAGE_GATE] = _coverage_gate(
        coverage_status, evidence_ref, evidence_sha256, evidence_available_at_utc
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "symbol": symbol,
        "market": market,
        "evaluation_at_utc": evaluation_at_utc,
        "review_or_expiry_time_utc": review_or_expiry_time_utc,
        "market_native_evidence_contract": market_native_evidence_contract,
        "reviewer_identity": ADAPTER_IDENTITY,
        "gates": gates,
    }


def _check_times(
    *,
    evaluation_dt: dt.datetime,
    review_dt: dt.datetime,
    evidence_dt: dt.datetime,
    time_order_code: str,
    future_evidence_code: str,
) -> None:
    if review_dt < evaluation_dt:
        _fail(time_order_code)
    if evidence_dt > evaluation_dt:
        _fail(future_evidence_code)


def korea_gate_inputs(
    *,
    review_path: Path = KOREA_LATEST_REVIEW_PATH,
    evaluation_at_utc: str,
    review_or_expiry_time_utc: str,
    required_gates: tuple[str, ...],
) -> dict[str, dict]:
    review_path = Path(review_path)
    if not review_path.exists():
        return {}
    packet = korea_review.validate_output(
        _read_json(review_path, "KOREA_REVIEW_READ_FAILED")
    )
    contract = korea_review.load_contract()
    evaluation_dt = _datetime(evaluation_at_utc, "EVALUATION_AT_INVALID")
    review_dt = _datetime(review_or_expiry_time_utc, "REVIEW_OR_EXPIRY_TIME_INVALID")
    generated_dt = _datetime(packet["generated_at"], "KOREA_REVIEW_GENERATED_AT_INVALID")
    _check_times(
        evaluation_dt=evaluation_dt,
        review_dt=review_dt,
        evidence_dt=generated_dt,
        time_order_code="REVIEW_TIME_PRECEDES_EVALUATION",
        future_evidence_code="KOREA_REVIEW_EVIDENCE_NOT_POINT_IN_TIME",
    )
    subjects = {row["symbol"]: row for row in packet["symbols"]}
    if set(subjects) != set(contract["supported_pipeline_subjects"]):
        _fail("KOREA_REVIEW_SUBJECT_SET_MISMATCH")
    if packet.get("five_axis", {}).get("ratio") != "5/5":
        # korea_symbol_market_review.build_review() cannot produce a packet
        # with an incomplete five-axis reference at all (it fails closed
        # before returning), so a validated packet reaching this point is
        # already 5/5. This guard only protects against a future change to
        # that invariant -- it never widens what counts as coverage here.
        _fail("KOREA_REVIEW_FIVE_AXIS_INCOMPLETE")
    result: dict[str, dict] = {}
    for symbol, row in subjects.items():
        coverage_status = (
            GATE_PASS if row["price_context"]["status"] == "OBSERVED_CONFIRMED" else GATE_FAIL
        )
        result[symbol] = _build_gate_input(
            symbol=symbol,
            market="KOREA",
            evaluation_at_utc=evaluation_at_utc,
            review_or_expiry_time_utc=review_or_expiry_time_utc,
            market_native_evidence_contract=contract["contract_version"],
            coverage_status=coverage_status,
            evidence_ref=f"data/latest_korea_symbol_market_review.json#/symbols/{symbol}",
            evidence_sha256=packet["packet_sha256"],
            evidence_available_at_utc=packet["generated_at"],
            required_gates=required_gates,
        )
    return result


def us_gate_inputs(
    *,
    review_path: Path = US_LATEST_REVIEW_PATH,
    evaluation_at_utc: str,
    review_or_expiry_time_utc: str,
    required_gates: tuple[str, ...],
) -> dict[str, dict]:
    review_path = Path(review_path)
    if not review_path.exists():
        return {}
    packet = us_review.validate_output(
        _read_json(review_path, "US_REVIEW_READ_FAILED")
    )
    contract = us_review.load_contract()
    evaluation_dt = _datetime(evaluation_at_utc, "EVALUATION_AT_INVALID")
    review_dt = _datetime(review_or_expiry_time_utc, "REVIEW_OR_EXPIRY_TIME_INVALID")
    generated_dt = _datetime(packet["generated_at"], "US_REVIEW_GENERATED_AT_INVALID")
    _check_times(
        evaluation_dt=evaluation_dt,
        review_dt=review_dt,
        evidence_dt=generated_dt,
        time_order_code="REVIEW_TIME_PRECEDES_EVALUATION",
        future_evidence_code="US_REVIEW_EVIDENCE_NOT_POINT_IN_TIME",
    )
    subjects = {row["symbol"]: row for row in packet["symbols"]}
    if set(subjects) != set(contract["supported_pipeline_subjects"]):
        _fail("US_REVIEW_SUBJECT_SET_MISMATCH")
    five_axis_complete = packet.get("five_axis", {}).get("ratio") == "5/5"
    result: dict[str, dict] = {}
    for symbol, row in subjects.items():
        coverage_status = (
            GATE_PASS
            if five_axis_complete and row["price_context"]["status"] == "OBSERVED"
            else GATE_FAIL
        )
        result[symbol] = _build_gate_input(
            symbol=symbol,
            market="US",
            evaluation_at_utc=evaluation_at_utc,
            review_or_expiry_time_utc=review_or_expiry_time_utc,
            market_native_evidence_contract=contract["contract_version"],
            coverage_status=coverage_status,
            evidence_ref=f"data/latest_us_symbol_market_review.json#/symbols/{symbol}",
            evidence_sha256=packet["packet_sha256"],
            evidence_available_at_utc=packet["generated_at"],
            required_gates=required_gates,
        )
    return result


def build_gate_inputs(
    *,
    evaluation_at_utc: str,
    review_or_expiry_time_utc: str | None = None,
    korea_review_path: Path = KOREA_LATEST_REVIEW_PATH,
    us_review_path: Path = US_LATEST_REVIEW_PATH,
) -> dict[str, dict]:
    """Build current candidate_stage_gate_input/1 records for exactly the
    five symbols the bounded Korea and US market-native evaluator contracts
    already declare. A market whose latest review file is not present or
    not yet materialized in this checkout contributes no records (it is
    NOT_CONNECTED for that market, not a synthesized MISSING record); a
    market whose review file is present but fails its own reused
    validate_output() (tamper, stale schema, opened authority) raises
    instead of silently downgrading. No manual Notion Watchlist Stage
    input is read here.
    """
    required_gates = _required_gates()
    review_or_expiry_time_utc = review_or_expiry_time_utc or evaluation_at_utc
    korea = korea_gate_inputs(
        review_path=korea_review_path,
        evaluation_at_utc=evaluation_at_utc,
        review_or_expiry_time_utc=review_or_expiry_time_utc,
        required_gates=required_gates,
    )
    us = us_gate_inputs(
        review_path=us_review_path,
        evaluation_at_utc=evaluation_at_utc,
        review_or_expiry_time_utc=review_or_expiry_time_utc,
        required_gates=required_gates,
    )
    overlap = set(korea) & set(us)
    if overlap:
        _fail(f"SYMBOL_MARKET_COLLISION:{sorted(overlap)}")
    return {**korea, **us}


def gate_input_for_symbol(gate_inputs: dict[str, dict], symbol: str) -> dict:
    normalized = normalize_symbol(symbol)
    if normalized not in gate_inputs:
        _fail(f"UNSUPPORTED_SYMBOL:{normalized}")
    return copy.deepcopy(gate_inputs[normalized])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-at-utc", required=True)
    parser.add_argument("--review-or-expiry-time-utc")
    parser.add_argument("--korea-review-path", type=Path, default=KOREA_LATEST_REVIEW_PATH)
    parser.add_argument("--us-review-path", type=Path, default=US_LATEST_REVIEW_PATH)
    args = parser.parse_args()
    result = build_gate_inputs(
        evaluation_at_utc=args.evaluation_at_utc,
        review_or_expiry_time_utc=args.review_or_expiry_time_utc,
        korea_review_path=args.korea_review_path,
        us_review_path=args.us_review_path,
    )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
