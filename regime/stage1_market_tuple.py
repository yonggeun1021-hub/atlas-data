#!/usr/bin/env python3
"""Build the Stage1 three-market handoff from retained source-event facts."""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import paper_regime_reference


INPUT = ROOT / "config" / "stage1_market_tuple_20260909.json"
LATEST = ROOT / "data" / "latest_stage1_market_tuple.json"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_SHA = re.compile(r"^[0-9a-f]{40}$")


class Stage1MarketTupleError(ValueError):
    pass


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: object) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def timestamp(value: object) -> dt.datetime:
    if not isinstance(value, str):
        raise Stage1MarketTupleError("TIMESTAMP_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise Stage1MarketTupleError("TIMESTAMP_INVALID") from exc
    if parsed.tzinfo is None:
        raise Stage1MarketTupleError("TIMESTAMP_OFFSET_REQUIRED")
    return parsed.astimezone(dt.timezone.utc)


def file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise Stage1MarketTupleError(f"SOURCE_MISSING:{path}") from exc


def git_bytes(root: Path, revision: str, path: str) -> bytes:
    if GIT_SHA.fullmatch(revision) is None:
        raise Stage1MarketTupleError("GIT_SHA_INVALID")
    try:
        return subprocess.run(
            ["git", "show", f"{revision}:{path}"],
            cwd=root,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ).stdout
    except subprocess.CalledProcessError as exc:
        raise Stage1MarketTupleError("HISTORIC_WORKFLOW_UNAVAILABLE") from exc


def event_receipt(root: Path, event: dict, evaluation_at: dt.datetime) -> dict:
    for key in ("github_head_sha", "publication_commit_sha"):
        if GIT_SHA.fullmatch(str(event.get(key))) is None:
            raise Stage1MarketTupleError(f"EVENT_{key.upper()}_INVALID")
    completed = timestamp(event.get("completed_at"))
    expected = timestamp(event.get("expected_at"))
    if not expected <= completed <= evaluation_at:
        raise Stage1MarketTupleError("EVENT_TIME_ORDER_INVALID")
    workflow_hash = hashlib.sha256(
        git_bytes(root, event["github_head_sha"], event["workflow_path"])
    ).hexdigest()
    if workflow_hash != event.get("workflow_file_sha256"):
        raise Stage1MarketTupleError("WORKFLOW_HASH_MISMATCH")
    output_path = root / event["output_path"]
    output_hash = file_sha(output_path)
    observation_identity = digest({
        "market": event["market"],
        "observation_date": event["observation_date"],
        "price_date": event.get("price_date"),
        "source_id": event["source_id"],
        "output_path": event["output_path"],
        "output_file_sha256": output_hash,
    })
    receipt = {
        "schema_version": "stage1_source_event_adoption_receipt/1",
        "evidence_type": "GITHUB_ACTIONS_REST_TERMINAL_READBACK",
        "observed_at": evaluation_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source_id": event["source_id"],
        "market": event["market"],
        "workflow": {
            "path": event["workflow_path"],
            "file_sha256": workflow_hash,
            "github_head_sha": event["github_head_sha"],
            "run_id": event["run_id"],
            "run_attempt": event["run_attempt"],
        },
        "event": {"expected_at_utc": event["expected_at"]},
        "terminal": {
            "status": "completed",
            "conclusion": "success",
            "completed_at_utc": event["completed_at"],
        },
        "source_output": {
            "path": event["output_path"],
            "file_sha256": output_hash,
            "observation_date": event["observation_date"],
            "price_date": event.get("price_date"),
            "observation_identity_sha256": observation_identity,
            "publication_commit_sha": event["publication_commit_sha"],
            "publication_result": event["publication_result"],
        },
        "authority": {
            "operations_telemetry_only": True,
            "decision_authorized": False,
            "capital_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    receipt["payload_sha256"] = digest(receipt)
    return receipt


def build(root: Path = ROOT, input_path: Path | None = None) -> dict:
    path = input_path or root / INPUT.relative_to(ROOT)
    try:
        spec = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise Stage1MarketTupleError("INPUT_INVALID") from exc
    if spec.get("contract_version") != "stage1_market_tuple_input/v1":
        raise Stage1MarketTupleError("INPUT_CONTRACT_INVALID")
    evaluation_at = timestamp(spec.get("evaluation_at"))
    reference = paper_regime_reference.build_reference(root)
    by_market = {row["market"]: row for row in reference["markets"]}
    receipts = [event_receipt(root, row, evaluation_at) for row in spec["events"]]
    expected_dates = spec["expected_market_dates"]
    markets = []
    for market in ("US", "KR", "CRYPTO"):
        judgement = by_market[market]
        observed = judgement["as_of_date"]
        expected = expected_dates[market]
        eligibility = (
            "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
            if observed != expected
            else "CURRENT_AS_FETCHED_NOT_PIT" if market == "US" else "CURRENT"
        )
        markets.append({
            "market": market,
            "expected_observation_date": expected,
            "decision_date": observed,
            "price_date": judgement.get("price_as_of_date"),
            "market_eligibility": eligibility,
            "classification_may_be_displayed": (
                judgement["paper_reference"]["candidate_regime"] != "UNKNOWN"
            ),
            "candidate_regime": judgement["paper_reference"]["candidate_regime"],
            "score": judgement["paper_reference"]["score"],
            "judgement_payload_sha256": digest(judgement),
            "source_event_receipt_sha256": [
                row["payload_sha256"] for row in receipts if row["market"] == market
            ],
        })
    for slot in spec["future_slots"]:
        if slot.get("status") != "NOT_DUE" or timestamp(slot.get("expected_at")) <= evaluation_at:
            raise Stage1MarketTupleError("FUTURE_SLOT_INVALID")
    same_dates = len({row["decision_date"] for row in markets}) == 1
    all_current = all(
        row["market_eligibility"] in {"CURRENT", "CURRENT_AS_FETCHED_NOT_PIT"}
        for row in markets
    )
    packet = {
        "schema_version": "stage1_market_runtime_tuple/1",
        "evaluation_at": spec["evaluation_at"],
        "status": (
            "STAGE2_READY_COMPLETE"
            if all_current and same_dates
            else "STAGE2_READY_PARTIAL_KR_SOURCE_NOT_ADVANCED"
        ),
        "paper_reference": {
            "generation_id": reference["generation_id"],
            "payload_sha256": reference["payload_sha256"],
            "status": reference["status"],
        },
        "markets": markets,
        "source_event_receipts": receipts,
        "future_scheduled_slots": copy.deepcopy(spec["future_slots"]),
        "official_calendar": copy.deepcopy(spec["official_calendar"]),
        "comparison": {
            "same_decision_date": same_dates,
            "three_market_comparison_status": (
                "COMPLETE" if all_current and same_dates else "PARTIAL_OR_NON_COMPARABLE"
            ),
            "price_date_substitution_used": False,
        },
        "authority": {
            "paper_reference_display_authorized": True,
            "relative_strength_comparison_authorized": True,
            "runtime_production_regime_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "capital_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_trading": False,
        },
        "input": {"path": str(path.relative_to(root)), "sha256": file_sha(path)},
    }
    packet["tuple_id"] = digest(packet)
    packet["payload_sha256"] = digest(packet)
    return packet


def write(packet: dict, root: Path = ROOT) -> tuple[Path, Path]:
    evidence = root / "evidence" / "regime" / "runtime_adoption" / "2026-09-09" / packet["tuple_id"] / "tuple.json"
    latest = root / "data" / "latest_stage1_market_tuple.json"
    text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    if evidence.exists() and evidence.read_text(encoding="utf-8") != text:
        raise Stage1MarketTupleError("APPEND_ONLY_CONFLICT")
    evidence.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return evidence, latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    packet = build()
    if args.write:
        evidence, latest = write(packet)
        print(json.dumps({"status": packet["status"], "tuple_id": packet["tuple_id"], "evidence": str(evidence.relative_to(ROOT)), "latest": str(latest.relative_to(ROOT))}, sort_keys=True))
    else:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
