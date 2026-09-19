#!/usr/bin/env python3
"""Build a KR PAPER runtime ratification review packet without opening runtime.

The packet answers whether the already-produced historical evidence and the
current KR five-axis source are ready for a separate CIO decision.  It emits no
Regime and every operational authority remains false.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
from zoneinfo import ZoneInfo

from market_data import krx_official_holiday_calendar as CALENDAR
from regime import kr_information_system_runtime_bridge as INFORMATION_SYSTEM


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config/kr_paper_runtime_ratification_candidate_v1.json"
CONTRACT_VERSION = "kr_paper_runtime_ratification_candidate/v1"
RECEIPT_SCHEMA = "kr_contiguous_historical_pit_receipt/1"
REQUIRED_REGIMES = {"NEUTRAL", "RISK_OFF", "RISK_ON", "STRESS"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

_spec = importlib.util.spec_from_file_location(
    "kr_runtime_ratification_source",
    ROOT / ".github/scripts/korea_market_signals.py",
)
SOURCE = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SOURCE)


class KrPaperRuntimeRatificationCandidateError(ValueError):
    """The review packet cannot be independently reconstructed."""


def fail(code: str, detail: str = "") -> None:
    raise KrPaperRuntimeRatificationCandidateError(
        f"{code}:{detail}" if detail else code
    )


def canonical_bytes(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_json(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise KrPaperRuntimeRatificationCandidateError(code) from exc
    if not isinstance(value, dict):
        fail(code)
    return value


def parse_time(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        raise KrPaperRuntimeRatificationCandidateError("REVIEW_TIME_INVALID") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        fail("REVIEW_TIME_INVALID")
    return parsed


def session_date(value: str) -> dt.date:
    """Parse an exact YYYY-MM-DD session date, fail-closed on anything else."""
    try:
        return dt.date.fromisoformat(value)
    except (AttributeError, TypeError, ValueError) as exc:
        raise KrPaperRuntimeRatificationCandidateError(
            "SESSION_DATE_INVALID"
        ) from exc


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = parse_json(path.read_bytes(), "CONTRACT_JSON_INVALID")
    if (
        contract.get("candidate_version") != CONTRACT_VERSION
        or contract.get("status") != "DRAFT_NOT_RATIFIED"
        or contract.get("scope") != "KR_PAPER_READ_ONLY_MARKET_REGIME_ONLY"
    ):
        fail("CONTRACT_SCOPE_INVALID")
    current = contract.get("current_authority")
    if not isinstance(current, dict) or any(value is not False for value in current.values()):
        fail("CURRENT_AUTHORITY_MUST_REMAIN_FALSE")
    for name, binding in contract.get("existing_policy_bindings", {}).items():
        if not name.endswith("_path"):
            continue
        digest_key = name[:-5] + "_sha256"
        expected = contract["existing_policy_bindings"].get(digest_key)
        if not isinstance(expected, str) or SHA256_RE.fullmatch(expected) is None:
            fail("POLICY_BINDING_HASH_INVALID", digest_key)
        target = ROOT / binding
        if sha256(target.read_bytes()) != expected:
            fail("POLICY_BINDING_DRIFT", binding)
    return contract


def latest_completed_session(capture_raw: bytes, source_ref: str, reviewed_at: str) -> str:
    instant = parse_time(reviewed_at)
    local = instant.astimezone(ZoneInfo("Asia/Seoul"))
    day = local.date()
    for _ in range(370):
        packet, _ = CALENDAR.build_calendar_packet(
            capture_raw, source_ref, day.isoformat()
        )
        calendar = packet["calendar"]
        if calendar["status"] == "OPEN_REGULAR":
            close = dt.datetime.fromisoformat(calendar["close_at"])
            if close <= local:
                return day.isoformat()
        day -= dt.timedelta(days=1)
    fail("COMPLETED_SESSION_NOT_FOUND")


def authority_boundary() -> dict:
    return {
        "evidence_accepted": False,
        "runtime_classification_authorized": False,
        "runtime_binding_authorized": False,
        "regime_result_ratification_authorized": False,
        "strategy_eligibility_authorized": False,
        "stage_authorized": False,
        "buy_authorized": False,
        "action_authorized": False,
        "capital_authorized": False,
        "order_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
        "real_authorized": False,
    }


def build_candidate(
    *,
    historical_receipt_raw: bytes,
    latest_signal_raw: bytes,
    reviewed_at: str,
    contract: dict | None = None,
    latest_manifest_raw: bytes | None = None,
    latest_raw_responses: dict[str, bytes] | None = None,
    expected_information_system_source: dict[str, str] | None = None,
) -> dict:
    contract = load_contract() if contract is None else contract
    receipt = parse_json(historical_receipt_raw, "HISTORICAL_RECEIPT_JSON_INVALID")
    expected_receipt_sha = contract["historical_evidence"][
        "final_receipt_file_sha256"
    ]
    if sha256(historical_receipt_raw) != expected_receipt_sha:
        fail("HISTORICAL_RECEIPT_HASH_MISMATCH")
    if receipt.get("schema") != RECEIPT_SCHEMA:
        fail("HISTORICAL_RECEIPT_SCHEMA_INVALID")

    coverage = receipt.get("coverage", {})
    pit = receipt.get("pit_status", {})
    historical_ready = (
        coverage.get("requested_session_count") == 28
        and coverage.get("observed_count") == 28
        and coverage.get("complete_five_axis_count") == 28
        and coverage.get("blocked_count") == 0
        and coverage.get("missing_rate") == "0.000000"
        and pit.get("status") == "PIT_ACCEPTED"
        and set(pit.get("regimes_observed", [])) == REQUIRED_REGIMES
        and pit.get("missing_regimes") == []
        and pit.get("replay_report_sha256")
        == contract["historical_evidence"]["replay_report_sha256"]
        and receipt.get("population", {}).get("payload_sha256")
        == contract["historical_evidence"]["population_payload_sha256"]
    )
    if not historical_ready:
        fail("HISTORICAL_EVIDENCE_CONDITIONS_NOT_MET")

    latest_value = parse_json(latest_signal_raw, "LIVE_SIGNAL_JSON_INVALID")
    if latest_value.get("schema_version") == "kr_paper_information_system_reference_candidate/1":
        if latest_manifest_raw is None or latest_raw_responses is None or expected_information_system_source is None:
            fail("INFORMATION_SYSTEM_SOURCE_EVIDENCE_MISSING")
        try:
            live = INFORMATION_SYSTEM.validate_natural_evidence(
                reference_raw=latest_signal_raw,
                manifest_raw=latest_manifest_raw,
                raw_responses=latest_raw_responses,
                expected=expected_information_system_source,
            )["source_packet"]
        except INFORMATION_SYSTEM.InformationSystemRuntimeError as exc:
            raise KrPaperRuntimeRatificationCandidateError(
                "LIVE_INFORMATION_SYSTEM_VALIDATION_FAILED"
            ) from exc
    else:
        try:
            live = SOURCE.validate_packet(latest_value)
        except Exception as exc:
            raise KrPaperRuntimeRatificationCandidateError(
                "LIVE_SIGNAL_VALIDATION_FAILED"
            ) from exc
    calendar_path = ROOT / contract["existing_policy_bindings"][
        "official_calendar_path"
    ]
    expected_session = latest_completed_session(
        calendar_path.read_bytes(), str(calendar_path.relative_to(ROOT)), reviewed_at
    )
    observed_session = live["as_of_date"]
    live_ready = observed_session == expected_session
    observed_ahead = (not live_ready) and session_date(
        observed_session
    ) > session_date(expected_session)

    checks = {
        "historical_28_of_28_five_axis": True,
        "market_scoped_pit_accepted": True,
        "all_four_required_regimes_observed": True,
        "policy_hashes_exact": True,
        "latest_live_session_exact": live_ready,
        "cio_runtime_binding_ratified": False,
        "cio_regime_result_ratified": False,
    }
    if live_ready:
        status = "READY_FOR_CIO_RATIFICATION_RUNTIME_STILL_CLOSED"
        freshness_status = "EXACT_LATEST_COMPLETED_SESSION"
        next_step = "CIO_REVIEW_EXACT_EVIDENCE_AND_RUNTIME_BINDING"
    elif observed_ahead:
        # The source is not behind: it reports a session later than the last
        # officially completed one at this review instant.  Reporting that as
        # SOURCE_NOT_ADVANCED_EXPECTED_SESSION would misuse a ratified token —
        # config/regime_semantic_freshness_policy_v1.json scopes that reason to
        # "the observed session date is an earlier session".  The exact-match
        # requirement still blocks, only the reason differs.
        status = "BLOCKED_LIVE_SESSION_AHEAD_OF_EXPECTED"
        freshness_status = "SOURCE_AHEAD_OF_EXPECTED_SESSION"
        next_step = "RE_REVIEW_AT_CURRENT_INSTANT_OR_RECONCILE_SOURCE_SESSION_DATING"
    else:
        status = "BLOCKED_LIVE_SESSION_NOT_ADVANCED"
        freshness_status = "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
        next_step = "CAPTURE_AND_RETAIN_EXACT_LATEST_COMPLETED_KRX_SESSION"
    packet = {
        "schema_version": 1,
        "candidate_version": CONTRACT_VERSION,
        "status": status,
        "reviewed_at": reviewed_at,
        "market": "KR",
        "historical_evidence": {
            "status": "PIT_ACCEPTED_CONDITION_SATISFIED",
            "range": receipt["range"],
            "requested_sessions": coverage["requested_session_count"],
            "complete_five_axis_sessions": coverage["complete_five_axis_count"],
            "blocked_sessions": coverage["blocked_count"],
            "population_payload_sha256": receipt["population"]["payload_sha256"],
            "replay_report_sha256": pit["replay_report_sha256"],
        },
        "live_input": {
            "observed_session": observed_session,
            "expected_latest_completed_session": expected_session,
            "freshness_status": freshness_status,
        },
        "checks": checks,
        "next_executable_step": next_step,
        "runtime_decision_available": False,
        "regime": "UNKNOWN",
        "authority": authority_boundary(),
    }
    packet["packet_sha256"] = sha256(canonical_bytes(packet))
    return packet


def validate_candidate(packet: dict, **inputs) -> dict:
    expected = build_candidate(**inputs)
    if canonical_bytes(packet) != canonical_bytes(expected):
        fail("CANDIDATE_REDERIVATION_MISMATCH")
    return expected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--historical-receipt", type=Path, required=True)
    parser.add_argument("--latest-signal", type=Path, required=True)
    parser.add_argument("--reviewed-at", required=True)
    args = parser.parse_args()
    packet = build_candidate(
        historical_receipt_raw=args.historical_receipt.read_bytes(),
        latest_signal_raw=args.latest_signal.read_bytes(),
        reviewed_at=args.reviewed_at,
    )
    print(canonical_bytes(packet).decode("utf-8"), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
