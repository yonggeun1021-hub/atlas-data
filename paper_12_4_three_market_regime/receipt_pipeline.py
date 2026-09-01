#!/usr/bin/env python3
"""Fail-closed PAPER 12-4 market-input to Regime-receipt pipeline.

The module validates retained local inputs only.  It never fetches data,
classifies a market, creates PAPER authority, or emits an order action.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Iterable


ENVELOPE_SCHEMA = "paper12_4_market_input_envelope/v1"
RECEIPT_SCHEMA = "paper12_4_market_regime_receipt/v1"
HEADER_SCHEMA = "paper12_4_three_market_header/v1"
BUNDLE_SCHEMA = "paper12_4_regime_receipt_bundle/v1"
MARKETS = ("KRX", "US", "CRYPTO")
INPUT_ROLES = ("LEADERSHIP", "SECTOR_FLOW", "AXES")
POLICY_STATUSES = {"RATIFIED", "UNRATIFIED"}
ROTATION_STATES = {"READY", "PENDING", "DEGRADED"}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
AUTHORITY = {
    "paper_authorized": False,
    "strategy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}
REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


class ReceiptPipelineError(ValueError):
    """Structural, timestamp, lineage, or derivation contract violation."""


def fail(code: str, detail: object = "") -> None:
    suffix = f":{detail}" if detail != "" else ""
    raise ReceiptPipelineError(f"{code}{suffix}")


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReceiptPipelineError("CANONICAL_JSON_INVALID") from exc


def canonical_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ReceiptPipelineError(f"SOURCE_FILE_UNREADABLE:{path}") from exc


def read_json(path: Path, code: str = "JSON_INVALID") -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReceiptPipelineError(f"{code}:{path}") from exc
    if not isinstance(value, dict):
        fail(code, "OBJECT_REQUIRED")
    return value


def parse_utc(value: object, label: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        fail("UTC_TIMESTAMP_INVALID", label)
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReceiptPipelineError(f"UTC_TIMESTAMP_INVALID:{label}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() != timezone.utc.utcoffset(parsed):
        fail("UTC_TIMESTAMP_INVALID", label)
    return parsed


def _strict_fields(value: object, expected: set[str], code: str) -> dict:
    if not isinstance(value, dict) or set(value) != expected:
        fail(code)
    return value


def _resolve_source(source_root: Path, source_path: object) -> Path:
    if not isinstance(source_path, str) or not source_path or Path(source_path).is_absolute():
        fail("SOURCE_PATH_INVALID", source_path)
    root = Path(source_root).resolve()
    resolved = (root / source_path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        fail("SOURCE_PATH_ESCAPE", source_path)
    return resolved


def validate_market_envelope(
    envelope: object,
    envelope_path: Path,
    source_root: Path = REPOSITORY_ROOT,
) -> dict:
    envelope = _strict_fields(
        envelope,
        {
            "schema_version",
            "market",
            "envelope_id",
            "regime_scoring_policy_status",
            "declared_rotation_state",
            "inputs",
        },
        "ENVELOPE_FIELDS_INVALID",
    )
    if envelope.get("schema_version") != ENVELOPE_SCHEMA:
        fail("ENVELOPE_SCHEMA_INVALID")
    if envelope.get("market") not in MARKETS:
        fail("MARKET_INVALID", envelope.get("market"))
    if not isinstance(envelope.get("envelope_id"), str) or not envelope["envelope_id"]:
        fail("ENVELOPE_ID_INVALID")
    if envelope.get("regime_scoring_policy_status") not in POLICY_STATUSES:
        fail("SCORING_POLICY_STATUS_INVALID")
    if envelope.get("declared_rotation_state") not in ROTATION_STATES:
        fail("ROTATION_STATE_INVALID")
    inputs = _strict_fields(
        envelope.get("inputs"), set(INPUT_ROLES), "INPUT_ROLES_INVALID"
    )
    normalized = copy.deepcopy(envelope)
    for role in INPUT_ROLES:
        row = _strict_fields(
            inputs[role],
            {
                "availability",
                "source_id",
                "source_path",
                "source_sha256",
                "source_time_utc",
                "bar_close_time_utc",
                "completed_bar",
                "ttl_seconds",
                "policy_status",
                "coverage_policy_status",
                "coverage",
            },
            f"INPUT_FIELDS_INVALID:{role}",
        )
        if not isinstance(row.get("source_id"), str) or not row["source_id"]:
            fail("SOURCE_ID_INVALID", role)
        availability = row.get("availability")
        if availability not in {"PRESENT", "MISSING"}:
            fail("SOURCE_AVAILABILITY_INVALID", role)
        if type(row.get("completed_bar")) is not bool:
            fail("COMPLETED_BAR_INVALID", role)
        if availability == "PRESENT":
            if not isinstance(row.get("source_sha256"), str) or SHA256_RE.fullmatch(row["source_sha256"]) is None:
                fail("SOURCE_SHA_FORMAT_INVALID", role)
            resolved = _resolve_source(source_root, row.get("source_path"))
            if file_sha256(resolved) != row["source_sha256"]:
                fail("SOURCE_SHA_MISMATCH", role)
            payload = read_json(resolved, f"SOURCE_JSON_INVALID:{role}")
            canonical_snapshot = payload.get("canonical_snapshot")
            if canonical_snapshot is not None:
                canonical_snapshot = _strict_fields(
                    canonical_snapshot,
                    set(canonical_snapshot) & {
                        "path",
                        "sha256",
                        "approval_status",
                        "policy_version",
                        "group_coverage_policy_status",
                        "as_of_date",
                    },
                    f"CANONICAL_SNAPSHOT_FIELDS_INVALID:{role}",
                )
                if not {"path", "sha256"}.issubset(canonical_snapshot):
                    fail("CANONICAL_SNAPSHOT_FIELDS_INVALID", role)
                expected_sha = canonical_snapshot["sha256"]
                if not isinstance(expected_sha, str) or SHA256_RE.fullmatch(expected_sha) is None:
                    fail("CANONICAL_SNAPSHOT_SHA_INVALID", role)
                canonical_path = _resolve_source(
                    source_root, canonical_snapshot.get("path")
                )
                if file_sha256(canonical_path) != expected_sha:
                    fail("CANONICAL_SNAPSHOT_SHA_MISMATCH", role)
                approval_status = canonical_snapshot.get("approval_status")
                if (
                    approval_status is not None
                    and approval_status != row["policy_status"]
                ):
                    fail("POLICY_STATUS_MISMATCH", role)
                group_coverage_status = canonical_snapshot.get(
                    "group_coverage_policy_status"
                )
                if (
                    group_coverage_status is not None
                    and group_coverage_status != row["coverage_policy_status"]
                ):
                    fail("COVERAGE_POLICY_STATUS_MISMATCH", role)
            declared_coverage = payload.get("coverage")
            if (
                isinstance(declared_coverage, str)
                and declared_coverage
                != f"{row['coverage']['defined_count']}/{row['coverage']['required_count']}"
            ):
                fail("SOURCE_COVERAGE_MISMATCH", role)
            parse_utc(row.get("source_time_utc"), f"{role}.source_time_utc")
            parse_utc(row.get("bar_close_time_utc"), f"{role}.bar_close_time_utc")
            if type(row.get("ttl_seconds")) is not int or row["ttl_seconds"] <= 0:
                fail("TTL_INVALID", role)
        elif any(
            row.get(key) is not None
            for key in (
                "source_path",
                "source_sha256",
                "source_time_utc",
                "bar_close_time_utc",
                "ttl_seconds",
            )
        ) or row["completed_bar"] is not False:
            fail("MISSING_SOURCE_FIELDS_INVALID", role)
        if row.get("policy_status") not in POLICY_STATUSES:
            fail("INPUT_POLICY_STATUS_INVALID", role)
        if row.get("coverage_policy_status") not in POLICY_STATUSES:
            fail("COVERAGE_POLICY_STATUS_INVALID", role)
        coverage = _strict_fields(
            row.get("coverage"), {"defined_count", "required_count"}, f"COVERAGE_FIELDS_INVALID:{role}"
        )
        defined = coverage.get("defined_count")
        required = coverage.get("required_count")
        if (
            type(defined) is not int
            or type(required) is not int
            or defined < 0
            or required <= 0
            or defined > required
        ):
            fail("COVERAGE_VALUE_INVALID", role)
    return normalized


def _input_readiness(role: str, row: dict, evaluation: datetime) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if row["availability"] == "MISSING":
        reasons.append(f"{role}_SOURCE_MISSING")
    else:
        source_time = parse_utc(row["source_time_utc"], f"{role}.source_time_utc")
        close_time = parse_utc(row["bar_close_time_utc"], f"{role}.bar_close_time_utc")
        if not row["completed_bar"]:
            reasons.append(f"{role}_BAR_INCOMPLETE")
        if close_time > source_time:
            reasons.append(f"{role}_BAR_CLOSE_AFTER_SOURCE_TIME")
        if source_time > evaluation:
            reasons.append(f"{role}_SOURCE_FROM_FUTURE")
        elif (evaluation - source_time).total_seconds() > row["ttl_seconds"]:
            reasons.append(f"{role}_TTL_EXPIRED")
    if row["policy_status"] != "RATIFIED":
        reasons.append(f"{role}_POLICY_UNRATIFIED")
    if row["coverage_policy_status"] != "RATIFIED":
        reasons.append(f"{role}_COVERAGE_POLICY_UNRATIFIED")
    coverage = row["coverage"]
    if coverage["defined_count"] != coverage["required_count"]:
        reasons.append(f"{role}_COVERAGE_INCOMPLETE")
    return ("READY" if not reasons else "WAIT", sorted(reasons))


def _lineage_row(role: str, row: dict, source_root: Path) -> dict:
    canonical_path = None
    canonical_sha = None
    if row["availability"] == "PRESENT":
        source = read_json(
            _resolve_source(source_root, row["source_path"]),
            f"SOURCE_JSON_INVALID:{role}",
        )
        canonical = source.get("canonical_snapshot")
        if isinstance(canonical, dict):
            canonical_path = canonical.get("path")
            canonical_sha = canonical.get("sha256")
    return {
        "role": role,
        "availability": row["availability"],
        "source_id": row["source_id"],
        "source_path": row["source_path"],
        "source_sha256": row["source_sha256"],
        "source_time_utc": row["source_time_utc"],
        "bar_close_time_utc": row["bar_close_time_utc"],
        "canonical_source_path": canonical_path,
        "canonical_source_sha256": canonical_sha,
    }


def build_market_receipt(
    envelope: object,
    envelope_path: Path,
    evaluation_time_utc: str,
    source_root: Path = REPOSITORY_ROOT,
) -> dict:
    validated = validate_market_envelope(envelope, envelope_path, source_root)
    evaluation = parse_utc(evaluation_time_utc, "evaluation_time_utc")
    readiness: dict[str, dict] = {}
    blockers: list[str] = []
    lineage: list[dict] = []
    for role in INPUT_ROLES:
        row = validated["inputs"][role]
        status, reasons = _input_readiness(role, row, evaluation)
        readiness[role] = {
            "status": status,
            "reasons": reasons,
            "coverage": {
                "defined_count": row["coverage"]["defined_count"],
                "required_count": row["coverage"]["required_count"],
                "ratio": f"{row['coverage']['defined_count']}/{row['coverage']['required_count']}",
            },
        }
        blockers.extend(reasons)
        lineage.append(_lineage_row(role, row, source_root))

    if validated["regime_scoring_policy_status"] != "RATIFIED":
        blockers.append("REGIME_SCORING_POLICY_UNRATIFIED")
    rotation_reasons: list[str] = []
    if validated["declared_rotation_state"] != "READY":
        rotation_reasons.append(
            f"{validated['market']}_ROTATION_{validated['declared_rotation_state']}"
        )
    for role in ("LEADERSHIP", "SECTOR_FLOW"):
        rotation_reasons.extend(readiness[role]["reasons"])
    rotation_reasons = sorted(set(rotation_reasons))
    blockers = sorted(set(blockers))
    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "market": validated["market"],
        "envelope_id": validated["envelope_id"],
        "evaluated_at_utc": evaluation_time_utc,
        "receipt_status": "WAIT" if blockers else "INPUTS_READY",
        "regime": "UNKNOWN",
        "paper_disposition": "HOLD",
        "input_readiness": readiness,
        "blocked_reasons": blockers,
        "rotation_input": {
            "status": "BLOCKED" if rotation_reasons else "READY",
            "declared_state": validated["declared_rotation_state"],
            "blocked_reasons": rotation_reasons,
        },
        "source_lineage": lineage,
        "source_lineage_sha256": canonical_sha256(lineage),
        "authority": copy.deepcopy(AUTHORITY),
    }
    receipt["receipt_sha256"] = canonical_sha256(receipt)
    return receipt


def _missing_market_row(market: str) -> dict:
    return {
        "market": market,
        "receipt_status": "WAIT",
        "regime": "UNKNOWN",
        "paper_disposition": "HOLD",
        "blocked_reasons": ["MARKET_INPUT_ENVELOPE_MISSING"],
        "rotation_input": {
            "status": "BLOCKED",
            "declared_state": "PENDING",
            "blocked_reasons": ["MARKET_INPUT_ENVELOPE_MISSING"],
        },
        "receipt_sha256": None,
        "source_lineage_sha256": None,
        "authority": copy.deepcopy(AUTHORITY),
    }


def _header(receipts: Iterable[dict], generated_at_utc: str) -> dict:
    by_market: dict[str, dict] = {}
    for receipt in receipts:
        market = receipt.get("market") if isinstance(receipt, dict) else None
        if market not in MARKETS:
            fail("RECEIPT_MARKET_INVALID", market)
        if market in by_market:
            fail("RECEIPT_MARKET_DUPLICATE", market)
        by_market[market] = receipt
    rows = []
    for market in MARKETS:
        receipt = by_market.get(market)
        if receipt is None:
            rows.append(_missing_market_row(market))
            continue
        rows.append(
            {
                "market": market,
                "receipt_status": receipt["receipt_status"],
                "regime": receipt["regime"],
                "paper_disposition": receipt["paper_disposition"],
                "blocked_reasons": list(receipt["blocked_reasons"]),
                "rotation_input": copy.deepcopy(receipt["rotation_input"]),
                "receipt_sha256": receipt["receipt_sha256"],
                "source_lineage_sha256": receipt["source_lineage_sha256"],
                "authority": copy.deepcopy(receipt["authority"]),
            }
        )
    ready_count = sum(row["receipt_status"] == "INPUTS_READY" for row in rows)
    rotation_blocked = [row["market"] for row in rows if row["rotation_input"]["status"] != "READY"]
    header = {
        "schema_version": HEADER_SCHEMA,
        "generated_at_utc": generated_at_utc,
        "header_status": "PENDING" if ready_count < len(MARKETS) else "INPUTS_READY",
        "markets": rows,
        "summary": {
            "required_market_count": len(MARKETS),
            "present_receipt_count": len(by_market),
            "inputs_ready_count": ready_count,
            "wait_count": len(MARKETS) - ready_count,
            "market_ranking": None,
            "paper_action": None,
        },
        "rotation_discovery": {
            "status": "DEGRADED" if rotation_blocked else "READY",
            "blocked_markets": rotation_blocked,
        },
        "authority": copy.deepcopy(AUTHORITY),
    }
    header["header_sha256"] = canonical_sha256(header)
    return header


def _transition_entry(
    subject: str,
    previous_status: str | None,
    current_status: str,
    artifact_sha256: str,
    lineage_sha256: str | None,
    reasons: list[str],
    generated_at_utc: str,
) -> dict:
    entry = {
        "schema_version": "paper12_4_transition_ledger_entry/v1",
        "subject": subject,
        "observed_at_utc": generated_at_utc,
        "from_status": previous_status,
        "to_status": current_status,
        "artifact_sha256": artifact_sha256,
        "source_lineage_sha256": lineage_sha256,
        "reasons": sorted(set(reasons)),
    }
    entry["entry_sha256"] = canonical_sha256(entry)
    return entry


def build_bundle(
    envelope_paths: Iterable[Path],
    evaluation_time_utc: str,
    previous_bundle: dict | None = None,
    source_root: Path = REPOSITORY_ROOT,
) -> dict:
    parse_utc(evaluation_time_utc, "evaluation_time_utc")
    receipts = []
    for path in envelope_paths:
        envelope_path = Path(path)
        envelope = read_json(envelope_path, "ENVELOPE_JSON_INVALID")
        receipts.append(
            build_market_receipt(
                envelope, envelope_path, evaluation_time_utc, source_root
            )
        )
    if len({row["market"] for row in receipts}) != len(receipts):
        fail("ENVELOPE_MARKET_DUPLICATE")
    receipts.sort(key=lambda row: MARKETS.index(row["market"]))
    header = _header(receipts, evaluation_time_utc)

    previous_receipts: dict[str, dict] = {}
    previous_header_status = None
    if previous_bundle is not None:
        validated_previous = validate_bundle(previous_bundle)
        previous_receipts = {
            row["market"]: row for row in validated_previous["market_receipts"]
        }
        previous_header_status = validated_previous["three_market_header"]["header_status"]
    current_receipts = {row["market"]: row for row in receipts}
    ledger = []
    for market in MARKETS:
        current = current_receipts.get(market)
        previous = previous_receipts.get(market)
        if current is None:
            missing = next(row for row in header["markets"] if row["market"] == market)
            ledger.append(
                _transition_entry(
                    market,
                    previous["receipt_status"] if previous else None,
                    "WAIT",
                    header["header_sha256"],
                    None,
                    missing["blocked_reasons"],
                    evaluation_time_utc,
                )
            )
        else:
            ledger.append(
                _transition_entry(
                    market,
                    previous["receipt_status"] if previous else None,
                    current["receipt_status"],
                    current["receipt_sha256"],
                    current["source_lineage_sha256"],
                    current["blocked_reasons"],
                    evaluation_time_utc,
                )
            )
    ledger.append(
        _transition_entry(
            "THREE_MARKET_REGIME_HEADER",
            previous_header_status,
            header["header_status"],
            header["header_sha256"],
            canonical_sha256(
                [row["source_lineage_sha256"] for row in header["markets"]]
            ),
            [
                reason
                for row in header["markets"]
                for reason in row["blocked_reasons"]
            ],
            evaluation_time_utc,
        )
    )
    bundle = {
        "schema_version": BUNDLE_SCHEMA,
        "generated_at_utc": evaluation_time_utc,
        "market_receipts": receipts,
        "three_market_header": header,
        "transition_ledger": ledger,
        "authority": copy.deepcopy(AUTHORITY),
    }
    bundle["bundle_sha256"] = canonical_sha256(bundle)
    return validate_bundle(bundle)


def _verify_digest(value: dict, field: str, code: str) -> None:
    claimed = value.get(field)
    if not isinstance(claimed, str) or SHA256_RE.fullmatch(claimed) is None:
        fail(code, "FORMAT")
    unsigned = copy.deepcopy(value)
    unsigned.pop(field)
    if canonical_sha256(unsigned) != claimed:
        fail(code, "MISMATCH")


def validate_bundle(bundle: object) -> dict:
    bundle = _strict_fields(
        bundle,
        {
            "schema_version",
            "generated_at_utc",
            "market_receipts",
            "three_market_header",
            "transition_ledger",
            "authority",
            "bundle_sha256",
        },
        "BUNDLE_FIELDS_INVALID",
    )
    if bundle.get("schema_version") != BUNDLE_SCHEMA:
        fail("BUNDLE_SCHEMA_INVALID")
    parse_utc(bundle.get("generated_at_utc"), "bundle.generated_at_utc")
    if bundle.get("authority") != AUTHORITY:
        fail("BUNDLE_AUTHORITY_INVALID")
    _verify_digest(bundle, "bundle_sha256", "BUNDLE_SHA_INVALID")
    receipts = bundle.get("market_receipts")
    if not isinstance(receipts, list) or len(receipts) > len(MARKETS):
        fail("BUNDLE_RECEIPTS_INVALID")
    if [row.get("market") for row in receipts] != sorted(
        [row.get("market") for row in receipts], key=MARKETS.index
    ):
        fail("BUNDLE_RECEIPT_ORDER_INVALID")
    for receipt in receipts:
        if receipt.get("schema_version") != RECEIPT_SCHEMA or receipt.get("authority") != AUTHORITY:
            fail("MARKET_RECEIPT_INVALID", receipt.get("market"))
        _verify_digest(receipt, "receipt_sha256", "MARKET_RECEIPT_SHA_INVALID")
        if receipt.get("regime") != "UNKNOWN" or receipt.get("paper_disposition") != "HOLD":
            fail("MARKET_RECEIPT_AUTHORITY_LEAK", receipt.get("market"))
        if receipt.get("source_lineage_sha256") != canonical_sha256(receipt.get("source_lineage")):
            fail("MARKET_LINEAGE_SHA_INVALID", receipt.get("market"))
    header = bundle.get("three_market_header")
    if not isinstance(header, dict) or header.get("schema_version") != HEADER_SCHEMA:
        fail("HEADER_INVALID")
    if header.get("authority") != AUTHORITY:
        fail("HEADER_AUTHORITY_INVALID")
    _verify_digest(header, "header_sha256", "HEADER_SHA_INVALID")
    expected_header = _header(receipts, bundle["generated_at_utc"])
    if canonical_json(header) != canonical_json(expected_header):
        fail("HEADER_DERIVATION_MISMATCH")
    ledger = bundle.get("transition_ledger")
    if not isinstance(ledger, list) or len(ledger) != 4:
        fail("TRANSITION_LEDGER_INVALID")
    expected_subjects = [*MARKETS, "THREE_MARKET_REGIME_HEADER"]
    if [entry.get("subject") for entry in ledger] != expected_subjects:
        fail("TRANSITION_LEDGER_ORDER_INVALID")
    header_rows = {row["market"]: row for row in header["markets"]}
    expected_current = {}
    for market in MARKETS:
        row = header_rows[market]
        expected_current[market] = {
            "to_status": row["receipt_status"],
            "artifact_sha256": row["receipt_sha256"] or header["header_sha256"],
            "source_lineage_sha256": row["source_lineage_sha256"],
            "reasons": sorted(set(row["blocked_reasons"])),
        }
    expected_current["THREE_MARKET_REGIME_HEADER"] = {
        "to_status": header["header_status"],
        "artifact_sha256": header["header_sha256"],
        "source_lineage_sha256": canonical_sha256(
            [row["source_lineage_sha256"] for row in header["markets"]]
        ),
        "reasons": sorted(
            {
                reason
                for row in header["markets"]
                for reason in row["blocked_reasons"]
            }
        ),
    }
    ledger_fields = {
        "schema_version",
        "subject",
        "observed_at_utc",
        "from_status",
        "to_status",
        "artifact_sha256",
        "source_lineage_sha256",
        "reasons",
        "entry_sha256",
    }
    for entry in ledger:
        if not isinstance(entry, dict) or set(entry) != ledger_fields:
            fail("TRANSITION_ENTRY_FIELDS_INVALID")
        _verify_digest(entry, "entry_sha256", "TRANSITION_ENTRY_SHA_INVALID")
        if entry.get("schema_version") != "paper12_4_transition_ledger_entry/v1":
            fail("TRANSITION_ENTRY_SCHEMA_INVALID")
        if entry.get("observed_at_utc") != bundle["generated_at_utc"]:
            fail("TRANSITION_ENTRY_TIME_INVALID", entry.get("subject"))
        if entry.get("from_status") not in {
            None,
            "WAIT",
            "PENDING",
            "INPUTS_READY",
        }:
            fail("TRANSITION_FROM_STATUS_INVALID", entry.get("subject"))
        current = expected_current[entry["subject"]]
        if any(entry.get(key) != value for key, value in current.items()):
            fail("TRANSITION_DERIVATION_MISMATCH", entry.get("subject"))
    return copy.deepcopy(bundle)
