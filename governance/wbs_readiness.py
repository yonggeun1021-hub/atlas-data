#!/usr/bin/env python3
"""Deterministic, read-only Atlas WBS readiness collector and reporter."""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import sys
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT = ROOT / "config" / "wbs_readiness_contract.json"

SNAPSHOT_SCHEMA = "atlas_wbs_snapshot/1"
PAPER_EVIDENCE_SCHEMA = "atlas_paper_rotation_readiness_evidence/1"
REPORT_SCHEMA = "atlas_wbs_readiness_report/1"

_ID_PATTERNS = (
    re.compile(r"^(P\d+(?:-[A-Z]+)?-\d+[A-Z]?)\b"),
    re.compile(r"^(P-[A-Z]+-\d+)\b"),
    re.compile(r"^(US-\d+-\d+)\b"),
)
_NON_CANONICAL_PREFIXES = ("[OBSOLETE ", "[SUPERSEDED ")
_SPECIAL_IDS = ("PAPER 9-3", "P-PORTAL-01", "US-1-1")


class ReadinessError(ValueError):
    """The supplied contract or evidence is not safe to score."""


def _require(condition: bool, code: str) -> None:
    if not condition:
        raise ReadinessError(code)


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReadinessError(f"JSON_LOAD_FAILED:{path}:{exc}") from exc


def _canonical_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _parse_utc(value: Any, field: str) -> datetime:
    _require(isinstance(value, str) and value.endswith("Z"), f"{field}_UTC_REQUIRED")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ReadinessError(f"{field}_INVALID") from exc
    _require(parsed.tzinfo is not None, f"{field}_UTC_REQUIRED")
    return parsed.astimezone(timezone.utc)


def _fraction(numerator: int, denominator: int) -> dict[str, Any]:
    _require(isinstance(numerator, int) and not isinstance(numerator, bool), "NUMERATOR_NOT_INTEGER")
    _require(isinstance(denominator, int) and not isinstance(denominator, bool), "DENOMINATOR_NOT_INTEGER")
    _require(numerator >= 0 and denominator >= 0, "NEGATIVE_FRACTION")
    _require(numerator <= denominator, "NUMERATOR_EXCEEDS_DENOMINATOR")
    percentage = None
    if denominator:
        percentage = str(
            (Decimal(numerator * 100) / Decimal(denominator)).quantize(
                Decimal("0.1"), rounding=ROUND_HALF_UP
            )
        )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "ratio": f"{numerator}/{denominator}",
        "percentage": percentage,
    }


def _weighted_fraction(earned_units: int, maximum_units: int) -> dict[str, Any]:
    result = _fraction(earned_units, maximum_units)
    return {
        "earnedUnits": result["numerator"],
        "maximumUnits": result["denominator"],
        "ratio": result["ratio"],
        "percentage": result["percentage"],
    }


def _extract_row_id(work_item: str) -> str | None:
    if work_item.startswith(_NON_CANONICAL_PREFIXES):
        return None
    for special in _SPECIAL_IDS:
        if work_item == special or work_item.startswith(special + " "):
            return special
    for pattern in _ID_PATTERNS:
        match = pattern.match(work_item)
        if match:
            return match.group(1)
    return None


def _validate_contract(contract: Any) -> dict[str, Any]:
    _require(isinstance(contract, dict), "CONTRACT_NOT_OBJECT")
    _require(contract.get("schemaVersion") == "atlas_wbs_readiness_contract/1", "CONTRACT_SCHEMA_INVALID")
    source = contract.get("source")
    statuses = contract.get("statuses")
    scopes = contract.get("scopes")
    paper = contract.get("paperRotation")
    for name, value in (("SOURCE", source), ("STATUSES", statuses), ("SCOPES", scopes), ("PAPER_ROTATION", paper)):
        _require(isinstance(value, dict), f"CONTRACT_{name}_INVALID")

    weights = statuses.get("weightedUnits")
    _require(isinstance(weights, dict) and weights, "STATUS_WEIGHTS_INVALID")
    _require(all(isinstance(key, str) and key for key in weights), "STATUS_NAME_INVALID")
    _require(
        all(isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 100 for value in weights.values()),
        "STATUS_WEIGHT_INVALID",
    )
    _require(statuses.get("formalComplete") in weights, "COMPLETE_STATUS_UNKNOWN")
    _require(statuses.get("forbidden") in weights, "FORBIDDEN_STATUS_UNKNOWN")
    late = statuses.get("lateStage")
    _require(isinstance(late, list) and late and len(late) == len(set(late)), "LATE_STAGE_INVALID")
    _require(set(late).issubset(weights), "LATE_STAGE_STATUS_UNKNOWN")

    small_paper = scopes.get("smallPaper")
    live_add = scopes.get("smallLiveAdditional")
    for label, values in (("SMALL_PAPER", small_paper), ("SMALL_LIVE_ADDITIONAL", live_add)):
        _require(isinstance(values, list) and all(isinstance(x, str) and x for x in values), f"{label}_INVALID")
        _require(len(values) == len(set(values)), f"{label}_DUPLICATE_ID")
    _require(set(small_paper).isdisjoint(live_add), "SCOPE_OVERLAP")

    gate_scores = paper.get("gateScoreUnits")
    gates = paper.get("gates")
    markets = paper.get("naturalMarkets")
    _require(isinstance(gate_scores, dict) and gate_scores, "GATE_SCORES_INVALID")
    _require(all(isinstance(x, int) and not isinstance(x, bool) and x >= 0 for x in gate_scores.values()), "GATE_SCORE_INVALID")
    _require(isinstance(gates, list) and gates, "GATES_INVALID")
    gate_ids = [gate.get("id") for gate in gates if isinstance(gate, dict)]
    _require(gate_ids == list(range(1, len(gates) + 1)), "GATE_IDS_INVALID")
    _require(all(isinstance(gate.get("name"), str) and gate["name"] for gate in gates), "GATE_NAME_INVALID")
    _require(isinstance(markets, list) and markets and len(markets) == len(set(markets)), "NATURAL_MARKETS_INVALID")
    _require(all(isinstance(x, str) and x for x in markets), "NATURAL_MARKET_INVALID")

    _require(source.get("databaseUrl"), "SOURCE_DATABASE_URL_MISSING")
    _require(source.get("dataSourceUrl"), "SOURCE_DATA_SOURCE_URL_MISSING")
    max_age = source.get("maxSourceAgeSeconds")
    _require(isinstance(max_age, int) and not isinstance(max_age, bool) and max_age > 0, "MAX_SOURCE_AGE_INVALID")
    return contract


def collect_query_pages(
    pages: Iterable[Any], contract: dict[str, Any], retrieved_at: str
) -> dict[str, Any]:
    """Normalize one or more Notion SQL query result pages without network I/O."""
    _parse_utc(retrieved_at, "RETRIEVED_AT")
    normalized: list[dict[str, Any]] = []
    for page_index, page in enumerate(pages):
        if isinstance(page, dict) and "content" in page:
            blocks = page.get("content")
            _require(isinstance(blocks, list), f"QUERY_PAGE_CONTENT_INVALID:{page_index}")
            texts = [block.get("text") for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
            _require(len(texts) == 1 and isinstance(texts[0], str), f"QUERY_PAGE_TEXT_INVALID:{page_index}")
            try:
                page = json.loads(texts[0])
            except json.JSONDecodeError as exc:
                raise ReadinessError(f"QUERY_PAGE_TEXT_JSON_INVALID:{page_index}") from exc
        _require(isinstance(page, dict) and isinstance(page.get("results"), list), f"QUERY_PAGE_INVALID:{page_index}")
        for row_index, row in enumerate(page["results"]):
            _require(isinstance(row, dict), f"QUERY_ROW_INVALID:{page_index}:{row_index}")
            work_item = row.get("Work Item", row.get("workItem"))
            status = row.get("Status", row.get("status"))
            url = row.get("url")
            phase = row.get("Phase", row.get("phase"))
            _require(isinstance(work_item, str) and work_item.strip(), f"WORK_ITEM_INVALID:{page_index}:{row_index}")
            _require(isinstance(status, str) and status, f"STATUS_INVALID:{page_index}:{row_index}")
            _require(isinstance(url, str) and url.startswith("https://"), f"ROW_URL_INVALID:{page_index}:{row_index}")
            _require(phase is None or isinstance(phase, str), f"PHASE_INVALID:{page_index}:{row_index}")
            normalized.append({"url": url, "workItem": work_item, "status": status, "phase": phase})

    normalized.sort(key=lambda row: (row["workItem"], row["url"]))
    return {
        "schemaVersion": SNAPSHOT_SCHEMA,
        "source": {
            "databaseUrl": contract["source"]["databaseUrl"],
            "dataSourceUrl": contract["source"]["dataSourceUrl"],
            "retrievedAt": retrieved_at,
        },
        "rows": normalized,
    }


def _validate_snapshot(snapshot: Any, contract: dict[str, Any], evaluated_at: datetime) -> list[dict[str, Any]]:
    _require(isinstance(snapshot, dict) and snapshot.get("schemaVersion") == SNAPSHOT_SCHEMA, "SNAPSHOT_SCHEMA_INVALID")
    source = snapshot.get("source")
    rows = snapshot.get("rows")
    _require(isinstance(source, dict), "SNAPSHOT_SOURCE_INVALID")
    _require(source.get("databaseUrl") == contract["source"]["databaseUrl"], "SNAPSHOT_DATABASE_MISMATCH")
    _require(source.get("dataSourceUrl") == contract["source"]["dataSourceUrl"], "SNAPSHOT_DATA_SOURCE_MISMATCH")
    retrieved_at = _parse_utc(source.get("retrievedAt"), "RETRIEVED_AT")
    age = (evaluated_at - retrieved_at).total_seconds()
    _require(age >= 0, "SNAPSHOT_FROM_FUTURE")
    _require(age <= contract["source"]["maxSourceAgeSeconds"], "SNAPSHOT_STALE")
    _require(isinstance(rows, list), "SNAPSHOT_ROWS_INVALID")

    known_statuses = set(contract["statuses"]["weightedUnits"])
    seen_urls: set[str] = set()
    seen_titles: set[str] = set()
    seen_ids: set[str] = set()
    for index, row in enumerate(rows):
        _require(isinstance(row, dict), f"SNAPSHOT_ROW_INVALID:{index}")
        _require(set(row) == {"url", "workItem", "status", "phase"}, f"SNAPSHOT_ROW_FIELDS_INVALID:{index}")
        url = row["url"]
        title = row["workItem"]
        status = row["status"]
        _require(isinstance(url, str) and url.startswith("https://"), f"SNAPSHOT_URL_INVALID:{index}")
        _require(isinstance(title, str) and title, f"SNAPSHOT_TITLE_INVALID:{index}")
        _require(status in known_statuses, f"SNAPSHOT_STATUS_UNKNOWN:{index}:{status}")
        _require(url not in seen_urls, f"DUPLICATE_ROW_URL:{url}")
        _require(title not in seen_titles, f"DUPLICATE_WORK_ITEM:{title}")
        seen_urls.add(url)
        seen_titles.add(title)
        row_id = _extract_row_id(title)
        if row_id is not None:
            _require(row_id not in seen_ids, f"DUPLICATE_CANONICAL_ROW_ID:{row_id}")
            seen_ids.add(row_id)

    required_ids = set(contract["scopes"]["smallPaper"]) | set(contract["scopes"]["smallLiveAdditional"])
    missing = sorted(required_ids - seen_ids)
    _require(not missing, "SCOPE_ROWS_MISSING:" + ",".join(missing))
    return rows


def _validate_paper_evidence(evidence: Any, contract: dict[str, Any], evaluated_at: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    _require(isinstance(evidence, dict) and evidence.get("schemaVersion") == PAPER_EVIDENCE_SCHEMA, "PAPER_EVIDENCE_SCHEMA_INVALID")
    as_of = _parse_utc(evidence.get("asOf"), "PAPER_EVIDENCE_AS_OF")
    age = (evaluated_at - as_of).total_seconds()
    _require(age >= 0, "PAPER_EVIDENCE_FROM_FUTURE")
    _require(age <= contract["source"]["maxSourceAgeSeconds"], "PAPER_EVIDENCE_STALE")
    gates = evidence.get("gates")
    natural = evidence.get("naturalLifecycle")
    authority = evidence.get("authority")
    expected_gates = contract["paperRotation"]["gates"]
    expected_markets = contract["paperRotation"]["naturalMarkets"]
    scores = contract["paperRotation"]["gateScoreUnits"]
    _require(isinstance(gates, list) and len(gates) == len(expected_gates), "PAPER_GATES_INVALID")
    _require(isinstance(natural, list) and len(natural) == len(expected_markets), "NATURAL_LIFECYCLE_INVALID")
    _require(isinstance(authority, dict) and authority, "PAPER_AUTHORITY_INVALID")
    _require(all(value is False for value in authority.values()), "PAPER_AUTHORITY_MUST_REMAIN_FALSE")

    for expected, actual in zip(expected_gates, gates):
        _require(isinstance(actual, dict), f"PAPER_GATE_INVALID:{expected['id']}")
        _require(actual.get("id") == expected["id"], f"PAPER_GATE_ID_INVALID:{expected['id']}")
        _require(actual.get("status") in scores, f"PAPER_GATE_STATUS_INVALID:{expected['id']}")
        refs = actual.get("evidenceRefs")
        _require(isinstance(refs, list) and refs and all(isinstance(x, str) and x for x in refs), f"PAPER_GATE_EVIDENCE_MISSING:{expected['id']}")
        _require(isinstance(actual.get("firstBlocker"), str) and actual["firstBlocker"], f"PAPER_GATE_BLOCKER_MISSING:{expected['id']}")

    observed_markets: list[str] = []
    for row in natural:
        _require(isinstance(row, dict), "NATURAL_MARKET_ROW_INVALID")
        market = row.get("market")
        observed_markets.append(market)
        _require(row.get("completed") in (True, False), f"NATURAL_COMPLETED_INVALID:{market}")
        _require(isinstance(row.get("evidenceRefs"), list) and row["evidenceRefs"], f"NATURAL_EVIDENCE_MISSING:{market}")
        _require(isinstance(row.get("firstBlocker"), str) and row["firstBlocker"], f"NATURAL_BLOCKER_MISSING:{market}")
    _require(observed_markets == expected_markets, "NATURAL_MARKET_ORDER_INVALID")
    return gates, natural


def _scope_summary(ids: list[str], row_by_id: dict[str, dict[str, Any]], contract: dict[str, Any]) -> dict[str, Any]:
    rows = [row_by_id[row_id] for row_id in ids]
    complete = contract["statuses"]["formalComplete"]
    weights = contract["statuses"]["weightedUnits"]
    done = sum(row["status"] == complete for row in rows)
    earned = sum(weights[row["status"]] for row in rows)
    counts = {status: 0 for status in weights}
    for row in rows:
        counts[row["status"]] += 1
    return {
        "formalReadiness": _fraction(done, len(rows)),
        "weightedReadiness": _weighted_fraction(earned, len(rows) * 100),
        "statusCounts": counts,
        "rowIds": list(ids),
    }


def build_report(
    snapshot: Any,
    paper_evidence: Any,
    contract: Any,
    evaluated_at: str,
) -> dict[str, Any]:
    contract = _validate_contract(copy.deepcopy(contract))
    evaluated_dt = _parse_utc(evaluated_at, "EVALUATED_AT")
    rows = _validate_snapshot(copy.deepcopy(snapshot), contract, evaluated_dt)
    gates, natural = _validate_paper_evidence(copy.deepcopy(paper_evidence), contract, evaluated_dt)

    weights = contract["statuses"]["weightedUnits"]
    complete_status = contract["statuses"]["formalComplete"]
    forbidden_status = contract["statuses"]["forbidden"]
    late_statuses = set(contract["statuses"]["lateStage"])
    counts = {status: 0 for status in weights}
    for row in rows:
        counts[row["status"]] += 1

    total = len(rows)
    complete = counts[complete_status]
    forbidden = counts[forbidden_status]
    actionable = sum(
        count for status, count in counts.items() if status not in {complete_status, forbidden_status}
    )
    late_stage = sum(count for status, count in counts.items() if status in late_statuses)
    earned = sum(counts[status] * units for status, units in weights.items())

    row_by_id = {
        row_id: row
        for row in rows
        if (row_id := _extract_row_id(row["workItem"])) is not None
    }
    small_paper_ids = contract["scopes"]["smallPaper"]
    small_live_ids = small_paper_ids + contract["scopes"]["smallLiveAdditional"]

    gate_scores = contract["paperRotation"]["gateScoreUnits"]
    gate_rows = []
    earned_gate_units = 0
    max_gate_units = len(gates) * max(gate_scores.values())
    for definition, evidence_row in zip(contract["paperRotation"]["gates"], gates):
        units = gate_scores[evidence_row["status"]]
        earned_gate_units += units
        gate_rows.append({
            "id": definition["id"],
            "name": definition["name"],
            "status": evidence_row["status"],
            "scoreUnits": units,
            "maximumUnits": max(gate_scores.values()),
            "evidenceRefs": list(evidence_row["evidenceRefs"]),
            "firstBlocker": evidence_row["firstBlocker"],
        })
    complete_markets = sum(row["completed"] is True for row in natural)

    report: dict[str, Any] = {
        "schemaVersion": REPORT_SCHEMA,
        "evaluatedAt": evaluated_at,
        "inventory": {
            "totalRows": total,
            "statusCounts": counts,
            "formalCompletion": _fraction(complete, total),
            "weightedProgress": _weighted_fraction(earned, total * 100),
            "forbiddenExcludedWeightedProgress": _weighted_fraction(
                earned, (total - forbidden) * 100
            ),
            "forbiddenRows": forbidden,
            "nonForbiddenRows": total - forbidden,
            "actionableRows": actionable,
            "actionableDefinition": "status is neither formal-complete nor forbidden",
            "lateStageEntry": _fraction(late_stage, total),
            "lateStageDefinition": list(contract["statuses"]["lateStage"]),
        },
        "paperRotation": {
            "score": _weighted_fraction(earned_gate_units, max_gate_units),
            "gates": gate_rows,
            "naturalE2E": {
                **_fraction(complete_markets, len(natural)),
                "markets": copy.deepcopy(natural),
            },
        },
        "scopes": {
            "smallPaper": _scope_summary(small_paper_ids, row_by_id, contract),
            "smallLive": _scope_summary(small_live_ids, row_by_id, contract),
        },
        "authority": copy.deepcopy(paper_evidence["authority"]),
        "lineage": {
            "contractSha256": _sha256(contract),
            "snapshotSha256": _sha256(snapshot),
            "paperEvidenceSha256": _sha256(paper_evidence),
        },
    }
    report["lineage"]["reportSha256"] = _sha256(report)
    return report


def _write_json(value: Any, output: Path | None) -> None:
    rendered = json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if output is None:
        sys.stdout.write(rendered)
        return
    output.write_text(rendered, encoding="utf-8")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    collect = subparsers.add_parser("collect", help="Normalize saved Notion query result pages")
    collect.add_argument("--query-page", action="append", required=True, type=Path)
    collect.add_argument("--retrieved-at", required=True)
    collect.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    collect.add_argument("--out", type=Path)

    report = subparsers.add_parser("report", help="Compute canonical readiness metrics")
    report.add_argument("--snapshot", required=True, type=Path)
    report.add_argument("--paper-evidence", required=True, type=Path)
    report.add_argument("--evaluated-at", required=True)
    report.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    report.add_argument("--out", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        contract = _validate_contract(_load_json(args.contract))
        if args.command == "collect":
            value = collect_query_pages(
                [_load_json(path) for path in args.query_page], contract, args.retrieved_at
            )
        else:
            value = build_report(
                _load_json(args.snapshot),
                _load_json(args.paper_evidence),
                contract,
                args.evaluated_at,
            )
        _write_json(value, args.out)
        return 0
    except ReadinessError as exc:
        print(f"wbs readiness failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
