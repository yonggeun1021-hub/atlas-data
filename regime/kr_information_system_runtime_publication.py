#!/usr/bin/env python3
"""Rebuild the latest ratified KR PAPER display decision from committed bytes."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import kr_paper_runtime as RUNTIME


ROOT = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = ROOT / "evidence/regime/kr_information_system/2026-09-11"
QUALIFICATION_PATH = (
    ROOT
    / "evidence/authority/kr_information_system_runtime_qualification_candidate_20260913.json"
)
OUTPUT_PATH = ROOT / "data/latest_kr_paper_runtime_decision.json"
CALENDAR_ROOT = "evidence/market_calendar/krx_global_holiday/2026-09-09"
# The 2026-09-14 (#696) publication frozen under EVIDENCE_ROOT recorded the
# qualification bytes it actually consumed, by sha256, inside its own packet.
# QUALIFICATION_PATH above is a *live* record: its bindings.implementation_sha256
# tracks the current bytes of the seven KR-pinned implementation files, so it
# legitimately moves whenever one of them is requalified (2026-09-19,
# RATIFICATION_MARKET_STATE_SOURCE_BINDING, for
# regime/paper_regime_reference.py).  Re-deriving a frozen artifact against a
# moving input would fail every time that happens, and the only other way to
# make it pass would be to rewrite committed append-only evidence so that it
# claims a qualification it never read.  The superseded bytes are retained
# instead, so the frozen publication stays checkable against exactly what it
# consumed while the live record keeps following the live code.
RETAINED_QUALIFICATION_PATH = (
    ROOT
    / "evidence/authority/kr_information_system_runtime_qualification_retained_20260914_publication.json"
)


class KrInformationSystemRuntimePublicationError(ValueError):
    """The display-only decision could not be reproduced exactly."""


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def information_system_evidence(qualification_raw: bytes) -> dict:
    reference = (EVIDENCE_ROOT / "KR_PAPER_REFERENCE_CANDIDATE.json").read_bytes()
    manifest = (EVIDENCE_ROOT / "source-capture/manifest.json").read_bytes()
    response_root = EVIDENCE_ROOT / "source-capture/responses"
    responses = {
        str(path.relative_to(EVIDENCE_ROOT / "source-capture")): path.read_bytes()
        for path in sorted(response_root.glob("*.json"))
    }
    history = (
        EVIDENCE_ROOT / "history/common-v1-replay-through-2026-09-10.json"
    ).read_bytes()
    acceptance = (EVIDENCE_ROOT / "history/final-receipt.json").read_bytes()
    return {
        "reference_raw": reference,
        "manifest_raw": manifest,
        "raw_responses": responses,
        "expected_source": {
            "reference_sha256": sha256(reference),
            "manifest_sha256": sha256(manifest),
            "source_contract_sha256": sha256(BRIDGE.SOURCE_CONTRACT_PATH.read_bytes()),
            "leadership_policy_sha256": sha256(BRIDGE.LEADERSHIP_POLICY_PATH.read_bytes()),
            "reference_policy_sha256": sha256(BRIDGE.REFERENCE_POLICY_PATH.read_bytes()),
        },
        "historical_replay_raw": history,
        "expected_historical_sha256": sha256(history),
        "historical_acceptance_raw": acceptance,
        "expected_historical_acceptance_sha256": sha256(acceptance),
        "qualification_raw": qualification_raw,
        "expected_qualification_sha256": sha256(qualification_raw),
    }


def session_boundary_input(code_revision: str) -> dict:
    return {
        "schema_version": RUNTIME.SESSION_BOUNDARY_INPUT_SCHEMA,
        "context_session_date": "2026-09-11",
        "execution_session_date": "2026-09-14",
        "context_session_close_at": "2026-09-11T06:30:00Z",
        "execution_session_close_at": "2026-09-14T06:30:00Z",
        "session_calendar_packet_paths": [
            f"{CALENDAR_ROOT}/calendar-2026-09-{day}.json"
            for day in ("11", "12", "13", "14")
        ],
        "trusted_commit": code_revision,
    }


def build_decision(
    *,
    evaluation_at: str,
    code_revision: str,
    qualification_raw: bytes | None = None,
) -> dict:
    qualification_raw = (
        QUALIFICATION_PATH.read_bytes()
        if qualification_raw is None
        else qualification_raw
    )
    result = RUNTIME.evaluate_kr_paper_runtime(
        source_packets=None,
        evaluation_at=evaluation_at,
        code_revision=code_revision,
        session_boundary_freshness=session_boundary_input(code_revision),
        information_system_evidence=information_system_evidence(qualification_raw),
    )
    if (
        result.get("decision_status") != "PAPER_RUNTIME_CLASSIFIED"
        or result.get("runtime_decision_available") is not True
        or result.get("actual_source_qualification")
        != "RATIFIED_KR_PAPER_DISPLAY_ONLY"
        or result.get("reasons") != []
    ):
        reason = result.get("reasons") or ["RUNTIME_NOT_AVAILABLE"]
        raise KrInformationSystemRuntimePublicationError(str(reason[0]))
    authority = result.get("authority")
    if not isinstance(authority, dict) or authority.get(
        "paper_runtime_display_authorized"
    ) is not True:
        raise KrInformationSystemRuntimePublicationError("DISPLAY_AUTHORITY_MISSING")
    if any(
        value is not False
        for key, value in authority.items()
        if key != "paper_runtime_display_authorized"
    ):
        raise KrInformationSystemRuntimePublicationError("AUTHORITY_ESCALATION")
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evaluation-at", required=True)
    parser.add_argument("--code-revision", required=True)
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = BRIDGE.pretty_bytes(
        build_decision(
            evaluation_at=args.evaluation_at,
            code_revision=args.code_revision,
        )
    )
    if args.check:
        if not args.output.is_file() or args.output.read_bytes() != expected:
            raise KrInformationSystemRuntimePublicationError(
                "PUBLISHED_DECISION_BYTES_MISMATCH"
            )
        return 0
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(expected)
    temporary.replace(args.output)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "sha256": sha256(expected),
                "status": "PUBLISHED_KR_PAPER_DISPLAY_ONLY",
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
