#!/usr/bin/env python3
"""Read-only canonical identity observations for Dynamic Clock candidates.

This adapter joins two already-ratified, independently validated facts:

* a committed P8-12 review candidate's exact provider identity lineage; and
* the canonical identity / market-scope authority documents.

It does **not** decide candidate validity, entry eligibility, position size,
portfolio participation, or any money action.  A resolved identity is only a
mechanical join key that downstream read-only consumers (including Portal)
may display.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.review_candidate import validate_review_candidate
from identity import canonical_identity as ci


DEFAULT_REPORT = ROOT / "evidence/operational/dynamic_clock/dynamic_clock_report.json"
DEFAULT_OUTPUT = ROOT / "evidence/operational/dynamic_clock/candidate_identity_observation.json"
SCHEMA_VERSION = "candidate_identity_observation/1"

AUTHORITY_ALL_FALSE = {
    "stage_promotion_authority": False,
    "buy_authority": False,
    "action_authority": False,
    "order_authority": False,
    "production_authority": False,
    "trading_authority": False,
}


class CandidateIdentityObservationError(ValueError):
    pass


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical_json(value)).hexdigest()


def _parse_exact_operational_time(candidate: dict) -> str:
    context = candidate.get("operational_evaluation")
    if not isinstance(context, dict):
        raise CandidateIdentityObservationError("OPERATIONAL_EVALUATION_MISSING")
    if context.get("status") != "EXACT_CALLER_SUPPLIED_OPERATIONAL_RUN_TIMESTAMP":
        raise CandidateIdentityObservationError("OPERATIONAL_EVALUATION_NOT_EXACT")
    if context.get("time_precision") != "TIMESTAMP":
        raise CandidateIdentityObservationError("OPERATIONAL_EVALUATION_NOT_TIMESTAMP")
    value = context.get("evaluated_at_utc")
    if not isinstance(value, str) or not value.endswith("Z"):
        raise CandidateIdentityObservationError("OPERATIONAL_EVALUATED_AT_INVALID")
    try:
        parsed = dt.datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise CandidateIdentityObservationError("OPERATIONAL_EVALUATED_AT_INVALID") from exc
    canonical = parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if canonical != value:
        raise CandidateIdentityObservationError("OPERATIONAL_EVALUATED_AT_NOT_CANONICAL")
    return value


def _identity_pair_observations(candidate: dict, authority: dict, observed_at: str) -> list[dict]:
    lineage = candidate["source_identity_lineage"]
    if lineage.get("status") != "AVAILABLE":
        return []
    rows = []
    for pair in lineage["source_pairs"]:
        result = ci.resolve_instrument_identity(
            pair["source_name"], pair["source_asset_id"], candidate["market"],
            observed_at, authority,
        )
        rows.append({
            "source_name": pair["source_name"],
            "source_asset_id": pair["source_asset_id"],
            "status": result["status"],
            "canonical_issuer_id": result.get("canonical_issuer_id"),
            "canonical_instrument_id": result.get("canonical_instrument_id"),
            "listing_id": result.get("listing_id"),
        })
    return rows


def _overall_identity(pair_rows: list[dict], lineage_status: str) -> dict:
    if lineage_status != "AVAILABLE":
        return {
            "status": "IDENTITY_NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING",
            "canonical_issuer_id": None,
            "canonical_instrument_id": None,
            "listing_id": None,
        }
    if not pair_rows:
        raise CandidateIdentityObservationError("AVAILABLE_LINEAGE_WITHOUT_SOURCE_PAIRS")
    resolved = [row for row in pair_rows if row["status"] == ci.RESOLVED]
    if len(resolved) != len(pair_rows):
        statuses = sorted({row["status"] for row in pair_rows})
        return {
            "status": statuses[0] if len(statuses) == 1 else "IDENTITY_NOT_COMPUTABLE_SOURCE_PAIR_SET",
            "canonical_issuer_id": None,
            "canonical_instrument_id": None,
            "listing_id": None,
        }
    identities = {
        (row["canonical_issuer_id"], row["canonical_instrument_id"], row["listing_id"])
        for row in resolved
    }
    if len(identities) != 1:
        return {
            "status": "IDENTITY_NOT_COMPUTABLE_SOURCE_PAIR_SET",
            "canonical_issuer_id": None,
            "canonical_instrument_id": None,
            "listing_id": None,
        }
    issuer_id, instrument_id, listing_id = next(iter(identities))
    return {
        "status": ci.RESOLVED,
        "canonical_issuer_id": issuer_id,
        "canonical_instrument_id": instrument_id,
        "listing_id": listing_id,
    }


def build_observation(report: dict, authority: dict, scope_authority: dict) -> dict:
    report_decision_date = report.get("decision_date")
    if not isinstance(report_decision_date, str):
        raise CandidateIdentityObservationError("REPORT_DECISION_DATE_MISSING")

    observations = []
    for market in sorted(report.get("by_market", {})):
        market_result = report["by_market"][market]
        for candidate in sorted(market_result.get("review_queue", []), key=lambda row: row["candidate_id"]):
            validate_review_candidate(candidate)
            if candidate.get("market") != market:
                raise CandidateIdentityObservationError("CANDIDATE_MARKET_MISMATCH")
            observed_at = _parse_exact_operational_time(candidate)
            pair_rows = _identity_pair_observations(candidate, authority, observed_at)
            identity = _overall_identity(pair_rows, candidate["source_identity_lineage"]["status"])
            scope = ci.resolve_account_scope(market, observed_at, scope_authority)
            observations.append({
                "candidate_id": candidate["candidate_id"],
                "market": market,
                "subject": candidate["subject"],
                "decision_at": candidate["decision_at"],
                "operational_evaluated_at": observed_at,
                "identity": identity,
                "source_pair_observations": pair_rows,
                "account_scope": {
                    "status": scope["status"],
                    "account_scope": scope.get("account_scope"),
                },
                "boundary": "MECHANICAL_IDENTITY_OBSERVATION_ONLY",
                "candidate_validity_status": "NOT_EVALUATED_BY_THIS_CONTRACT",
                "entry_eligibility_status": "NOT_EVALUATED_BY_THIS_CONTRACT",
                "authority": dict(AUTHORITY_ALL_FALSE),
            })

    payload = {
        "schema_version": SCHEMA_VERSION,
        "decision_date": report_decision_date,
        "source_dynamic_clock_report_canonical_sha256": _sha256(report),
        "authority_documents": {
            "security_identity_sha256": hashlib.sha256(Path(authority["_source_path"]).read_bytes()).hexdigest(),
            "market_account_scope_sha256": hashlib.sha256(Path(scope_authority["_source_path"]).read_bytes()).hexdigest(),
        },
        "observations": observations,
        "summary": {
            "candidate_count": len(observations),
            "identity_resolved_count": sum(row["identity"]["status"] == ci.RESOLVED for row in observations),
            "scope_resolved_count": sum(row["account_scope"]["status"] == ci.RESOLVED for row in observations),
        },
        "authority": dict(AUTHORITY_ALL_FALSE),
    }
    payload["packet_sha256"] = _sha256(payload)
    return payload


def validate_observation(packet: dict, report: dict, authority: dict, scope_authority: dict) -> dict:
    expected = build_observation(report, authority, scope_authority)
    if packet != expected:
        raise CandidateIdentityObservationError("CANDIDATE_IDENTITY_OBSERVATION_MISMATCH")
    if any(any(value is not False for value in row["authority"].values()) for row in packet["observations"]):
        raise CandidateIdentityObservationError("CANDIDATE_IDENTITY_AUTHORITY_MUST_BE_FALSE")
    if any(row["candidate_validity_status"] != "NOT_EVALUATED_BY_THIS_CONTRACT" for row in packet["observations"]):
        raise CandidateIdentityObservationError("CANDIDATE_VALIDITY_AUTHORITY_LEAK")
    return packet


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    report = json.loads(args.report.read_text())
    authority = ci.load_authority()
    scope_authority = ci.load_scope_authority()
    packet = build_observation(report, authority, scope_authority)
    validate_observation(packet, report, authority, scope_authority)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if not args.output.exists() or args.output.read_text() != encoded:
        args.output.write_text(encoded)
    print(json.dumps(packet["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
