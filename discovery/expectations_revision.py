#!/usr/bin/env python3
"""P3-06 provider-neutral expectations / estimate revision radar."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "expectations_revision_radar_contract.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ExpectationsRevisionError(ValueError):
    """Fail-closed P3-06 contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ExpectationsRevisionError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "expectations_revision_radar/1",
        "source_policy_schema_version": "consensus_estimate_source_policy/1",
        "input_schema_version": "expectations_revision_radar_input/1",
        "output_schema_version": "expectations_revision_radar_packet/1",
        "allowed_markets": ["KOREA", "US"],
        "allowed_metric_types": ["EBIT", "EPS", "FREE_CASH_FLOW", "REVENUE"],
        "allowed_statistics": ["MEAN", "MEDIAN"],
        "required_vintage_count": 2,
        "revision_directions": ["UPWARD", "DOWNWARD", "UNCHANGED", "UNKNOWN_EVIDENCE"],
        "repository_default_source_policy": "ABSENT",
        "revision_semantics": "LATEST_ESTIMATE_MINUS_PRIOR_ESTIMATE_FOR_EXACT_TARGET",
        "case_semantics": "NONZERO_CONFIRMED_REVISION_OBSERVED_ONLY",
        "output_decimal_places": 12,
        "input_authority": {
            "normalized_consensus_estimate_observation_only": True,
            "source_selection_authorized": False,
            "importance_classification_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "authority": {
            "raw_revision_observation_only": True,
            "source_policy_creation_authorized": False,
            "importance_classification_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "rule_evaluation_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ExpectationsRevisionError("CONTRACT_FIELDS_MISMATCH")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ExpectationsRevisionError(f"CONTRACT_FIELD_MISMATCH:{key}")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise ExpectationsRevisionError(code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)
    except ValueError as exc:
        raise ExpectationsRevisionError(code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise ExpectationsRevisionError(code)
    return parsed


def _date(value, code: str) -> dt.date:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        raise ExpectationsRevisionError(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ExpectationsRevisionError(code) from exc
    if parsed.isoformat() != value:
        raise ExpectationsRevisionError(code)
    return parsed


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ExpectationsRevisionError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ExpectationsRevisionError(code)
    return value


def _sha(value, code: str) -> str:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        raise ExpectationsRevisionError(code)
    return value


def _decimal(value, code: str) -> Decimal:
    if not isinstance(value, str):
        raise ExpectationsRevisionError(code)
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ExpectationsRevisionError(code) from exc
    if not parsed.is_finite():
        raise ExpectationsRevisionError(code)
    return parsed


def _render(value: Decimal, places: int) -> str:
    with localcontext() as context:
        context.prec = 50
        quantum = Decimal(1).scaleb(-places)
        return format(value.quantize(quantum, rounding=ROUND_HALF_EVEN), "f")


def _validate_policy(value: dict, as_of: dt.datetime, contract: dict) -> dict:
    fields = {
        "schema_version", "contract_version", "policy_id", "status", "ratified_by",
        "ratified_at", "effective_from", "effective_to", "sources", "authority",
        "packet_sha256",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ExpectationsRevisionError("SOURCE_POLICY_FIELDS_MISMATCH")
    expected_authority = {
        "source_contract_only": True,
        "source_purchase_authorized": False,
        "importance_classification_authorized": False,
        "candidate_ranking_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if (
        value.get("schema_version") != contract["source_policy_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("status") != "RATIFIED"
        or value.get("ratified_by") != "CIO"
        or value.get("authority") != expected_authority
    ):
        raise ExpectationsRevisionError("SOURCE_POLICY_IDENTITY_INVALID")
    policy_id = _token(value.get("policy_id"), "SOURCE_POLICY_ID_INVALID")
    ratified = _utc(value.get("ratified_at"), "SOURCE_POLICY_RATIFIED_AT_INVALID")
    start = _date(value.get("effective_from"), "SOURCE_POLICY_EFFECTIVE_FROM_INVALID")
    end = None if value.get("effective_to") is None else _date(value["effective_to"], "SOURCE_POLICY_EFFECTIVE_TO_INVALID")
    if ratified > as_of:
        raise ExpectationsRevisionError("SOURCE_POLICY_RATIFIED_AFTER_AS_OF")
    if ratified.date() > start or (end is not None and end < start):
        raise ExpectationsRevisionError("SOURCE_POLICY_INTERVAL_INVALID")
    if as_of.date() < start or (end is not None and as_of.date() > end):
        raise ExpectationsRevisionError("SOURCE_POLICY_NOT_EFFECTIVE")
    digest = _sha(value.get("packet_sha256"), "SOURCE_POLICY_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ExpectationsRevisionError("SOURCE_POLICY_SHA_MISMATCH")
    source_fields = {
        "source_id", "allowed_hosts", "markets", "available_at_semantics",
        "license_basis_ref", "license_basis_sha256", "permitted_use",
    }
    sources = []
    for index, row in enumerate(value.get("sources") or []):
        context = f"source:{index}"
        if not isinstance(row, dict) or set(row) != source_fields:
            raise ExpectationsRevisionError(f"SOURCE_POLICY_SOURCE_FIELDS_MISMATCH:{context}")
        hosts = row.get("allowed_hosts")
        markets = row.get("markets")
        if (
            not isinstance(hosts, list) or not hosts or hosts != sorted(set(hosts))
            or any(not isinstance(host, str) or not host or ":" in host or "/" in host for host in hosts)
            or not isinstance(markets, list) or not markets or markets != sorted(set(markets))
            or any(market not in contract["allowed_markets"] for market in markets)
            or row.get("permitted_use") != "RESEARCH_DERIVED_FEATURES_ONLY"
        ):
            raise ExpectationsRevisionError(f"SOURCE_POLICY_SOURCE_INVALID:{context}")
        sources.append({
            "source_id": _token(row.get("source_id"), f"SOURCE_ID_INVALID:{context}"),
            "allowed_hosts": list(hosts),
            "markets": list(markets),
            "available_at_semantics": _text(row.get("available_at_semantics"), f"AVAILABLE_AT_SEMANTICS_INVALID:{context}"),
            "license_basis_ref": _text(row.get("license_basis_ref"), f"LICENSE_BASIS_REF_INVALID:{context}"),
            "license_basis_sha256": _sha(row.get("license_basis_sha256"), f"LICENSE_BASIS_SHA_INVALID:{context}"),
            "permitted_use": row["permitted_use"],
        })
    if not sources:
        raise ExpectationsRevisionError("SOURCE_POLICY_SOURCES_EMPTY")
    sources.sort(key=lambda row: row["source_id"])
    ids = [row["source_id"] for row in sources]
    if len(ids) != len(set(ids)):
        raise ExpectationsRevisionError("SOURCE_POLICY_SOURCE_DUPLICATE")
    return {"policy_id": policy_id, "packet_sha256": digest, "sources": sources}


def _validate_vintage(row: dict, as_of: dt.datetime, source_by_id: dict, contract: dict, context: str) -> dict:
    fields = {
        "vintage_id", "estimate_as_of", "available_at", "retrieved_at", "source_id",
        "source_url", "source_sha256", "evidence_status", "estimate_value",
        "estimate_count", "unavailable_reasons",
    }
    if not isinstance(row, dict) or set(row) != fields:
        raise ExpectationsRevisionError(f"VINTAGE_FIELDS_MISMATCH:{context}")
    estimate_as_of = _utc(row.get("estimate_as_of"), f"ESTIMATE_AS_OF_INVALID:{context}")
    available = _utc(row.get("available_at"), f"AVAILABLE_AT_INVALID:{context}")
    retrieved = _utc(row.get("retrieved_at"), f"RETRIEVED_AT_INVALID:{context}")
    if not estimate_as_of <= available <= retrieved <= as_of:
        raise ExpectationsRevisionError(f"VINTAGE_TIME_ORDER_INVALID:{context}")
    source_id = _token(row.get("source_id"), f"SOURCE_ID_INVALID:{context}")
    source_contract = source_by_id.get(source_id)
    if source_contract is None:
        raise ExpectationsRevisionError(f"SOURCE_NOT_RATIFIED:{context}")
    parsed_url = urlparse(str(row.get("source_url") or ""))
    if (
        parsed_url.scheme != "https" or parsed_url.hostname not in source_contract["allowed_hosts"]
        or parsed_url.username is not None or parsed_url.password is not None
    ):
        raise ExpectationsRevisionError(f"SOURCE_URL_INVALID:{context}")
    status = row.get("evidence_status")
    reasons = row.get("unavailable_reasons")
    if (
        status not in {"EVIDENCE_AVAILABLE", "EVIDENCE_UNAVAILABLE"}
        or not isinstance(reasons, list) or reasons != sorted(set(reasons))
        or any(not isinstance(reason, str) or TOKEN_RE.fullmatch(reason) is None for reason in reasons)
    ):
        raise ExpectationsRevisionError(f"EVIDENCE_STATUS_INVALID:{context}")
    if status == "EVIDENCE_AVAILABLE":
        value = _decimal(row.get("estimate_value"), f"ESTIMATE_VALUE_INVALID:{context}")
        count = row.get("estimate_count")
        if type(count) is not int or count < 1 or reasons:
            raise ExpectationsRevisionError(f"AVAILABLE_EVIDENCE_INVALID:{context}")
        rendered_value = _render(value, contract["output_decimal_places"])
    else:
        if row.get("estimate_value") is not None or row.get("estimate_count") is not None or not reasons:
            raise ExpectationsRevisionError(f"UNAVAILABLE_EVIDENCE_INVALID:{context}")
        rendered_value, count = None, None
    return {
        "vintage_id": _token(row.get("vintage_id"), f"VINTAGE_ID_INVALID:{context}"),
        "estimate_as_of": row["estimate_as_of"],
        "available_at": row["available_at"],
        "retrieved_at": row["retrieved_at"],
        "source_id": source_id,
        "source_url": row["source_url"],
        "source_sha256": _sha(row.get("source_sha256"), f"SOURCE_SHA_INVALID:{context}"),
        "evidence_status": status,
        "estimate_value": rendered_value,
        "estimate_count": count,
        "unavailable_reasons": list(reasons),
    }


def _validate_input(value: dict, as_of: dt.datetime, policy: dict, contract: dict) -> dict:
    fields = {"schema_version", "contract_version", "batch_id", "as_of_utc", "series", "authority", "packet_sha256"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ExpectationsRevisionError("INPUT_FIELDS_MISMATCH")
    if (
        value.get("schema_version") != contract["input_schema_version"]
        or value.get("contract_version") != contract["contract_version"]
        or value.get("authority") != contract["input_authority"]
        or _utc(value.get("as_of_utc"), "AS_OF_INVALID") != as_of
    ):
        raise ExpectationsRevisionError("INPUT_IDENTITY_INVALID")
    batch_id = _token(value.get("batch_id"), "BATCH_ID_INVALID")
    digest = _sha(value.get("packet_sha256"), "INPUT_SHA_INVALID")
    unsigned = copy.deepcopy(value)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ExpectationsRevisionError("INPUT_SHA_MISMATCH")
    source_by_id = {row["source_id"]: row for row in policy["sources"]}
    series_fields = {
        "series_id", "market", "asset_id", "metric_type", "fiscal_period_end",
        "estimate_statistic", "unit", "comparison_basis", "vintages",
    }
    rows = []
    for index, row in enumerate(value.get("series") or []):
        context = f"series:{index}"
        if not isinstance(row, dict) or set(row) != series_fields:
            raise ExpectationsRevisionError(f"SERIES_FIELDS_MISMATCH:{context}")
        market = row.get("market")
        if (
            market not in contract["allowed_markets"]
            or row.get("metric_type") not in contract["allowed_metric_types"]
            or row.get("estimate_statistic") not in contract["allowed_statistics"]
        ):
            raise ExpectationsRevisionError(f"SERIES_IDENTITY_INVALID:{context}")
        vintages = [
            _validate_vintage(item, as_of, source_by_id, contract, f"{context}:vintage:{vintage_index}")
            for vintage_index, item in enumerate(row.get("vintages") or [])
        ]
        if len(vintages) != contract["required_vintage_count"]:
            raise ExpectationsRevisionError(f"VINTAGE_COUNT_INVALID:{context}")
        vintages.sort(key=lambda item: (item["estimate_as_of"], item["vintage_id"]))
        if vintages[0]["estimate_as_of"] >= vintages[1]["estimate_as_of"]:
            raise ExpectationsRevisionError(f"VINTAGE_ORDER_INVALID:{context}")
        ids = [item["vintage_id"] for item in vintages]
        if len(ids) != len(set(ids)):
            raise ExpectationsRevisionError(f"VINTAGE_ID_DUPLICATE:{context}")
        if any(market not in source_by_id[item["source_id"]]["markets"] for item in vintages):
            raise ExpectationsRevisionError(f"SOURCE_MARKET_NOT_RATIFIED:{context}")
        rows.append({
            "series_id": _token(row.get("series_id"), f"SERIES_ID_INVALID:{context}"),
            "market": market,
            "asset_id": _token(row.get("asset_id"), f"ASSET_ID_INVALID:{context}"),
            "metric_type": row["metric_type"],
            "fiscal_period_end": _date(row.get("fiscal_period_end"), f"FISCAL_PERIOD_INVALID:{context}").isoformat(),
            "estimate_statistic": row["estimate_statistic"],
            "unit": _token(row.get("unit"), f"UNIT_INVALID:{context}"),
            "comparison_basis": _text(row.get("comparison_basis"), f"COMPARISON_BASIS_INVALID:{context}"),
            "vintages": vintages,
        })
    rows.sort(key=lambda row: (contract["allowed_markets"].index(row["market"]), row["asset_id"], row["series_id"]))
    ids = [row["series_id"] for row in rows]
    targets = [(row["market"], row["asset_id"], row["metric_type"], row["fiscal_period_end"], row["estimate_statistic"], row["unit"], row["comparison_basis"]) for row in rows]
    if len(ids) != len(set(ids)):
        raise ExpectationsRevisionError("SERIES_ID_DUPLICATE")
    if len(targets) != len(set(targets)):
        raise ExpectationsRevisionError("SERIES_TARGET_DUPLICATE")
    return {"batch_id": batch_id, "packet_sha256": digest, "series": rows}


def _derive(rows: list[dict], contract: dict) -> tuple[list[dict], list[dict]]:
    results, cases = [], []
    for row in rows:
        vintages = copy.deepcopy(row["vintages"])
        if any(item["evidence_status"] != "EVIDENCE_AVAILABLE" for item in vintages):
            direction, prior, latest, change = "UNKNOWN_EVIDENCE", None, None, None
        else:
            prior_value = Decimal(vintages[0]["estimate_value"])
            latest_value = Decimal(vintages[1]["estimate_value"])
            difference = latest_value - prior_value
            direction = "UPWARD" if difference > 0 else "DOWNWARD" if difference < 0 else "UNCHANGED"
            prior = _render(prior_value, contract["output_decimal_places"])
            latest = _render(latest_value, contract["output_decimal_places"])
            change = _render(difference, contract["output_decimal_places"])
        result = {
            "series_id": row["series_id"], "market": row["market"], "asset_id": row["asset_id"],
            "metric_type": row["metric_type"], "fiscal_period_end": row["fiscal_period_end"],
            "estimate_statistic": row["estimate_statistic"], "unit": row["unit"],
            "comparison_basis": row["comparison_basis"], "revision_direction": direction,
            "prior_estimate": prior, "latest_estimate": latest, "revision_change": change,
            "vintages": vintages,
        }
        results.append(result)
        if direction in {"UPWARD", "DOWNWARD"}:
            identity = {key: result[key] for key in ("series_id", "market", "asset_id", "metric_type", "fiscal_period_end", "estimate_statistic", "unit", "comparison_basis", "revision_direction", "revision_change")}
            cases.append({
                "schema_version": "expectations_revision_case/1",
                "case_id": f"RADAR-ER-{payload_sha256(identity)[:24].upper()}",
                **identity,
                "available_at": vintages[1]["available_at"],
                "confirmed_evidence": vintages,
                "importance": "UNRATIFIED",
                "candidate_eligible": False,
                "candidate_rank": None,
                "stage_transition": None,
                "action": None,
            })
    return results, cases


def build_packet(input_value: dict, source_policy: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    as_of = _utc(input_value.get("as_of_utc") if isinstance(input_value, dict) else None, "AS_OF_INVALID")
    policy = _validate_policy(source_policy, as_of, contract)
    checked = _validate_input(input_value, as_of, policy, contract)
    results, cases = _derive(checked["series"], contract)
    counts = {direction: sum(row["revision_direction"] == direction for row in results) for direction in contract["revision_directions"]}
    packet = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "as_of_utc": input_value["as_of_utc"],
        "status": "ESTIMATE_REVISIONS_OBSERVED_SOURCE_POLICY_EXTERNAL",
        "series_results": results,
        "cases": cases,
        "summary": {"series_count": len(results), "case_count": len(cases), **counts},
        "lineage": {
            "input_batch_id": checked["batch_id"], "input_packet_sha256": checked["packet_sha256"],
            "source_policy_id": policy["policy_id"], "source_policy_sha256": policy["packet_sha256"],
        },
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REPOSITORY_DEFAULT_SOURCE_POLICY_ABSENT", "LIVE_CONSENSUS_SOURCE_NOT_SELECTED",
            "PAID_SOURCE_REQUIRES_USER_REAPPROVAL", "IMPORTANCE_AND_RANKING_UNRATIFIED",
            "STAGE_PRODUCTION_TRADING_NOT_AUTHORIZED",
        ],
    }
    packet["packet_sha256"] = payload_sha256(packet)
    return validate_packet(packet, input_value, source_policy, contract)


def validate_packet(packet: dict, input_value: dict, source_policy: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {"schema_version", "contract_version", "as_of_utc", "status", "series_results", "cases", "summary", "lineage", "authority", "unresolved_boundaries", "packet_sha256"}
    if not isinstance(packet, dict) or set(packet) != fields:
        raise ExpectationsRevisionError("OUTPUT_FIELDS_MISMATCH")
    expected = build_packet_unvalidated(input_value, source_policy, contract)
    digest = _sha(packet.get("packet_sha256"), "OUTPUT_SHA_INVALID")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("packet_sha256")
    if payload_sha256(unsigned) != digest:
        raise ExpectationsRevisionError("OUTPUT_SHA_MISMATCH")
    if unsigned != expected:
        raise ExpectationsRevisionError("OUTPUT_CONTENT_MISMATCH")
    return copy.deepcopy(packet)


def build_packet_unvalidated(input_value: dict, source_policy: dict, contract: dict) -> dict:
    as_of = _utc(input_value.get("as_of_utc") if isinstance(input_value, dict) else None, "AS_OF_INVALID")
    policy = _validate_policy(source_policy, as_of, contract)
    checked = _validate_input(input_value, as_of, policy, contract)
    results, cases = _derive(checked["series"], contract)
    counts = {direction: sum(row["revision_direction"] == direction for row in results) for direction in contract["revision_directions"]}
    return {
        "schema_version": contract["output_schema_version"], "contract_version": contract["contract_version"],
        "as_of_utc": input_value["as_of_utc"], "status": "ESTIMATE_REVISIONS_OBSERVED_SOURCE_POLICY_EXTERNAL",
        "series_results": results, "cases": cases,
        "summary": {"series_count": len(results), "case_count": len(cases), **counts},
        "lineage": {"input_batch_id": checked["batch_id"], "input_packet_sha256": checked["packet_sha256"], "source_policy_id": policy["policy_id"], "source_policy_sha256": policy["packet_sha256"]},
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": ["REPOSITORY_DEFAULT_SOURCE_POLICY_ABSENT", "LIVE_CONSENSUS_SOURCE_NOT_SELECTED", "PAID_SOURCE_REQUIRES_USER_REAPPROVAL", "IMPORTANCE_AND_RANKING_UNRATIFIED", "STAGE_PRODUCTION_TRADING_NOT_AUTHORIZED"],
    }


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ExpectationsRevisionError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, policy_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(input_path), _read_json(policy_path)))
        return 0
    except (ExpectationsRevisionError, OSError, TypeError, ValueError) as exc:
        print(f"Expectations revision radar failed: {exc}")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("source_policy", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    return run(args.input, args.source_policy, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
