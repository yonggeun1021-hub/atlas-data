#!/usr/bin/env python3
"""P2-01 externally ratified Theme / Value-Chain graph validator.

The repository contains no default Theme catalog.  This module validates an
external effective-dated graph and explicit evidence-linked US/Korea asset
memberships.  Only an effective RATIFIED graph activates a detached Global
Asset Master membership adapter; no inference, weighting or rotation score is
performed.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "theme_taxonomy_contract.json"
INPUT_SCHEMA_VERSION = "theme_taxonomy_input/1"
OUTPUT_SCHEMA_VERSION = "theme_taxonomy_packet/1"
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")


class ThemeTaxonomyError(ValueError):
    """Fail-closed Theme taxonomy contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ThemeTaxonomyError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "theme_taxonomy/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "allowed_markets": ["KOREA", "US"],
        "required_markets_for_ratified_graph": ["KOREA", "US"],
        "allowed_node_types": ["THEME", "VALUE_CHAIN_SEGMENT"],
        "allowed_relation_types": ["CONTAINS", "DEPENDS_ON", "ENABLES", "SUPPLIES"],
        "market_sources": {
            "KOREA": ["dart_open_api"],
            "US": ["microsoft_sec_issuer_disclosure", "sec_edgar", "tsmc_investor_relations"],
        },
        "source_hosts": {
            "dart_open_api": ["opendart.fss.or.kr"],
            "microsoft_sec_issuer_disclosure": ["www.sec.gov"],
            "sec_edgar": ["data.sec.gov", "www.sec.gov"],
            "tsmc_investor_relations": ["investor.tsmc.com"],
        },
        "effective_interval": "[valid_from, valid_to)",
        "membership_adapter_status": "DETACHED_REQUIRES_SEPARATE_MASTER_INGESTION",
        "policy_status": {
            "repository_default_taxonomy": "ABSENT",
            "theme_selection": "EXTERNAL_RATIFICATION_REQUIRED",
            "membership_selection": "EXPLICIT_EVIDENCE_LINKED_ONLY",
            "value_chain_role_vocabulary": "EXTERNAL_TAXONOMY_DEFINED",
            "source_hierarchy": "UNRATIFIED",
            "rotation_scoring": "UNRATIFIED",
        },
        "authority": {
            "ratified_graph_validation_only": True,
            "theme_inference_authorized": False,
            "membership_inference_authorized": False,
            "membership_weight_authorized": False,
            "source_ranking_authorized": False,
            "rotation_score_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def _validate_contract(value: dict) -> dict:
    expected = _expected_contract()
    if not isinstance(value, dict):
        raise ThemeTaxonomyError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ThemeTaxonomyError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise ThemeTaxonomyError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _valid_date(value) -> bool:
    if not isinstance(value, str) or DATE_RE.fullmatch(value) is None:
        return False
    try:
        return dt.date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _valid_utc(value) -> bool:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        return False
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%dT%H:%M:%SZ") == value
    except ValueError:
        return False


def _utc(value: str) -> dt.datetime:
    if not _valid_utc(value):
        raise ThemeTaxonomyError(f"UTC_INVALID:{value!r}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise ThemeTaxonomyError(code)
    return value


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ThemeTaxonomyError(code)
    return value


def _interval(value: dict, context: str) -> tuple[str, str | None]:
    start = value.get("valid_from")
    end = value.get("valid_to")
    if not _valid_date(start):
        raise ThemeTaxonomyError(f"VALID_FROM_INVALID:{context}")
    if end is not None and (not _valid_date(end) or end <= start):
        raise ThemeTaxonomyError(f"VALID_TO_INVALID:{context}")
    return start, end


def _active(start: str, end: str | None, day: str) -> bool:
    return start <= day and (end is None or day < end)


def _contains(outer_start: str, outer_end: str | None, inner_start: str, inner_end: str | None) -> bool:
    return outer_start <= inner_start and (
        outer_end is None or (inner_end is not None and inner_end <= outer_end)
    )


def _overlap(a_start: str, a_end: str | None, b_start: str, b_end: str | None) -> bool:
    return (a_end is None or b_start < a_end) and (b_end is None or a_start < b_end)


def _validate_approval(value: dict, as_of_date: str) -> tuple[dict, bool, dt.datetime]:
    fields = {
        "approval_status", "decision_id", "decision_sha256", "ratified_by",
        "ratified_at_utc", "effective_from", "effective_to",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ThemeTaxonomyError("APPROVAL_FIELDS_MISMATCH")
    status = value.get("approval_status")
    if status not in {"RATIFIED", "UNRATIFIED"}:
        raise ThemeTaxonomyError("APPROVAL_STATUS_INVALID")
    _token(value.get("decision_id"), "DECISION_ID_INVALID")
    if not isinstance(value.get("decision_sha256"), str) or SHA256_RE.fullmatch(value["decision_sha256"]) is None:
        raise ThemeTaxonomyError("DECISION_SHA256_INVALID")
    start = value.get("effective_from")
    end = value.get("effective_to")
    if not _valid_date(start) or (end is not None and (not _valid_date(end) or end <= start)):
        raise ThemeTaxonomyError("APPROVAL_EFFECTIVE_INTERVAL_INVALID")
    if status == "RATIFIED":
        if not isinstance(value.get("ratified_by"), str) or not value["ratified_by"].strip() or not _valid_utc(value.get("ratified_at_utc")):
            raise ThemeTaxonomyError("RATIFICATION_PROOF_INVALID")
        ratified_at = _utc(value["ratified_at_utc"])
        if ratified_at.date() > dt.date.fromisoformat(as_of_date):
            raise ThemeTaxonomyError("RATIFIED_AFTER_AS_OF")
    else:
        if value.get("ratified_by") is not None or value.get("ratified_at_utc") is not None:
            raise ThemeTaxonomyError("UNRATIFIED_PROOF_FORBIDDEN")
        ratified_at = dt.datetime.combine(dt.date.fromisoformat(as_of_date), dt.time.max)
    effective = status == "RATIFIED" and _active(start, end, as_of_date)
    return copy.deepcopy(value), effective, ratified_at


def _validate_source(source: dict, market: str, cutoff: dt.datetime, contract: dict, context: str) -> dict:
    fields = {"source_id", "source_url", "source_sha256", "available_at", "retrieved_at_utc"}
    if not isinstance(source, dict) or set(source) != fields:
        raise ThemeTaxonomyError(f"SOURCE_IDENTITY_FIELDS_MISMATCH:{context}")
    source_id = source.get("source_id")
    if source_id not in contract["market_sources"][market]:
        raise ThemeTaxonomyError(f"SOURCE_ID_NOT_ALLOWED:{context}:{source_id}")
    parsed = urlparse(str(source.get("source_url") or ""))
    if (
        parsed.scheme != "https" or parsed.hostname not in contract["source_hosts"][source_id]
        or parsed.username is not None or parsed.password is not None
    ):
        raise ThemeTaxonomyError(f"SOURCE_URL_INVALID:{context}")
    if not isinstance(source.get("source_sha256"), str) or SHA256_RE.fullmatch(source["source_sha256"]) is None:
        raise ThemeTaxonomyError(f"SOURCE_SHA256_INVALID:{context}")
    available = source.get("available_at")
    retrieved = source.get("retrieved_at_utc")
    if not (_valid_date(available) or _valid_utc(available)) or not _valid_utc(retrieved):
        raise ThemeTaxonomyError(f"SOURCE_TIME_INVALID:{context}")
    retrieved_at = _utc(retrieved)
    if _valid_date(available):
        bad_order = dt.date.fromisoformat(available) > retrieved_at.date()
        after_cutoff = dt.date.fromisoformat(available) > cutoff.date()
    else:
        available_at = _utc(available)
        bad_order = available_at > retrieved_at
        after_cutoff = available_at > cutoff
    if bad_order or after_cutoff or retrieved_at > cutoff:
        raise ThemeTaxonomyError(f"SOURCE_TEMPORAL_ORDER_INVALID:{context}")
    return copy.deepcopy(source)


def _validate_node(value: dict, contract: dict) -> dict:
    fields = {"theme_id", "display_name", "description", "node_type", "valid_from", "valid_to"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ThemeTaxonomyError("NODE_FIELDS_MISMATCH")
    theme_id = _token(value.get("theme_id"), "THEME_ID_INVALID")
    start, end = _interval(value, theme_id)
    if value.get("node_type") not in contract["allowed_node_types"]:
        raise ThemeTaxonomyError(f"NODE_TYPE_INVALID:{theme_id}")
    return {
        "theme_id": theme_id,
        "display_name": _text(value.get("display_name"), f"DISPLAY_NAME_INVALID:{theme_id}"),
        "description": _text(value.get("description"), f"DESCRIPTION_INVALID:{theme_id}"),
        "node_type": value["node_type"], "valid_from": start, "valid_to": end,
    }


def _validate_edge(value: dict, nodes: dict, contract: dict) -> dict:
    fields = {"edge_id", "from_theme_id", "to_theme_id", "relation_type", "rationale", "valid_from", "valid_to"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ThemeTaxonomyError("EDGE_FIELDS_MISMATCH")
    edge_id = _token(value.get("edge_id"), "EDGE_ID_INVALID")
    left = value.get("from_theme_id")
    right = value.get("to_theme_id")
    if left not in nodes or right not in nodes:
        raise ThemeTaxonomyError(f"EDGE_NODE_UNKNOWN:{edge_id}")
    if left == right:
        raise ThemeTaxonomyError(f"EDGE_SELF_REFERENCE:{edge_id}")
    relation = value.get("relation_type")
    if relation not in contract["allowed_relation_types"]:
        raise ThemeTaxonomyError(f"EDGE_RELATION_INVALID:{edge_id}")
    start, end = _interval(value, edge_id)
    for node_id in (left, right):
        node = nodes[node_id]
        if not _contains(node["valid_from"], node["valid_to"], start, end):
            raise ThemeTaxonomyError(f"EDGE_OUTSIDE_NODE_INTERVAL:{edge_id}:{node_id}")
    return {
        "edge_id": edge_id, "from_theme_id": left, "to_theme_id": right,
        "relation_type": relation,
        "rationale": _text(value.get("rationale"), f"EDGE_RATIONALE_INVALID:{edge_id}"),
        "valid_from": start, "valid_to": end,
    }


def _validate_evidence(value: dict, market: str, cutoff: dt.datetime, contract: dict, context: str) -> dict:
    fields = {"evidence_id", "claim_text", "source_identity", "audit_provenance"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ThemeTaxonomyError(f"EVIDENCE_FIELDS_MISMATCH:{context}")
    evidence_id = _token(value.get("evidence_id"), f"EVIDENCE_ID_INVALID:{context}")
    provenance = value.get("audit_provenance")
    if not isinstance(provenance, dict) or not provenance:
        raise ThemeTaxonomyError(f"AUDIT_PROVENANCE_INVALID:{evidence_id}")
    return {
        "evidence_id": evidence_id,
        "claim_text": _text(value.get("claim_text"), f"EVIDENCE_CLAIM_INVALID:{evidence_id}"),
        "claim_status": "SOURCE_LINKED_INPUT_TO_RATIFIED_TAXONOMY_DECISION",
        "source_identity": _validate_source(value.get("source_identity"), market, cutoff, contract, evidence_id),
        "audit_provenance": copy.deepcopy(provenance),
    }


def _validate_membership(value: dict, nodes: dict, cutoff: dt.datetime, contract: dict) -> dict:
    fields = {
        "membership_id", "asset_id", "market", "theme_id", "role_id",
        "valid_from", "valid_to", "evidence",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ThemeTaxonomyError("MEMBERSHIP_FIELDS_MISMATCH")
    membership_id = _token(value.get("membership_id"), "MEMBERSHIP_ID_INVALID")
    asset_id = _token(value.get("asset_id"), f"ASSET_ID_INVALID:{membership_id}")
    market = value.get("market")
    if market not in contract["allowed_markets"]:
        raise ThemeTaxonomyError(f"MEMBERSHIP_MARKET_INVALID:{membership_id}")
    prefix = "US:" if market == "US" else "KR:"
    if not asset_id.startswith(prefix):
        raise ThemeTaxonomyError(f"ASSET_MARKET_PREFIX_MISMATCH:{membership_id}")
    theme_id = value.get("theme_id")
    if theme_id not in nodes:
        raise ThemeTaxonomyError(f"MEMBERSHIP_THEME_UNKNOWN:{membership_id}")
    role_id = _token(value.get("role_id"), f"ROLE_ID_INVALID:{membership_id}")
    start, end = _interval(value, membership_id)
    node = nodes[theme_id]
    if not _contains(node["valid_from"], node["valid_to"], start, end):
        raise ThemeTaxonomyError(f"MEMBERSHIP_OUTSIDE_THEME_INTERVAL:{membership_id}")
    evidence = value.get("evidence")
    if not isinstance(evidence, list) or not evidence:
        raise ThemeTaxonomyError(f"MEMBERSHIP_EVIDENCE_EMPTY:{membership_id}")
    checked = [
        _validate_evidence(item, market, cutoff, contract, membership_id)
        for item in evidence
    ]
    checked.sort(key=lambda item: item["evidence_id"])
    evidence_ids = [item["evidence_id"] for item in checked]
    if len(evidence_ids) != len(set(evidence_ids)):
        raise ThemeTaxonomyError(f"EVIDENCE_ID_DUPLICATE:{membership_id}")
    return {
        "membership_id": membership_id, "asset_id": asset_id, "market": market,
        "theme_id": theme_id, "role_id": role_id, "valid_from": start,
        "valid_to": end, "evidence": checked,
    }


def _assert_contains_acyclic(edges: list[dict]) -> None:
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if edge["relation_type"] == "CONTAINS":
            adjacency.setdefault(edge["from_theme_id"], []).append(edge["to_theme_id"])
    visiting = set()
    visited = set()

    def visit(node: str) -> None:
        if node in visiting:
            raise ThemeTaxonomyError(f"CONTAINS_CYCLE:{node}")
        if node in visited:
            return
        visiting.add(node)
        for child in adjacency.get(node, []):
            visit(child)
        visiting.remove(node)
        visited.add(node)

    for node in sorted(adjacency):
        visit(node)


def _assert_membership_intervals(memberships: list[dict]) -> None:
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for item in memberships:
        grouped.setdefault((item["asset_id"], item["theme_id"], item["role_id"]), []).append(item)
    for key, items in grouped.items():
        ordered = sorted(items, key=lambda item: item["valid_from"])
        for index, left in enumerate(ordered):
            for right in ordered[index + 1:]:
                if _overlap(left["valid_from"], left["valid_to"], right["valid_from"], right["valid_to"]):
                    raise ThemeTaxonomyError(f"MEMBERSHIP_INTERVAL_OVERLAP:{':'.join(key)}")


def build_packet(value: dict, contract: dict | None = None) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {"schema_version", "taxonomy_id", "as_of_date", "approval", "nodes", "edges", "memberships"}
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ThemeTaxonomyError("INPUT_SCHEMA_MISMATCH")
    if set(value) != fields:
        raise ThemeTaxonomyError("INPUT_FIELDS_MISMATCH")
    taxonomy_id = _token(value.get("taxonomy_id"), "TAXONOMY_ID_INVALID")
    as_of_date = value.get("as_of_date")
    if not _valid_date(as_of_date):
        raise ThemeTaxonomyError("AS_OF_DATE_INVALID")
    approval, effective_ratified, cutoff = _validate_approval(value.get("approval"), as_of_date)
    raw_nodes = value.get("nodes")
    raw_edges = value.get("edges")
    raw_memberships = value.get("memberships")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ThemeTaxonomyError("NODES_EMPTY")
    if not isinstance(raw_edges, list) or not isinstance(raw_memberships, list):
        raise ThemeTaxonomyError("GRAPH_LIST_INVALID")
    nodes = {}
    for raw in raw_nodes:
        node = _validate_node(raw, contract)
        if node["theme_id"] in nodes:
            raise ThemeTaxonomyError(f"THEME_ID_DUPLICATE:{node['theme_id']}")
        nodes[node["theme_id"]] = node
    edges = [_validate_edge(raw, nodes, contract) for raw in raw_edges]
    edge_ids = [item["edge_id"] for item in edges]
    if len(edge_ids) != len(set(edge_ids)):
        raise ThemeTaxonomyError("EDGE_ID_DUPLICATE")
    _assert_contains_acyclic(edges)
    memberships = [
        _validate_membership(raw, nodes, cutoff, contract) for raw in raw_memberships
    ]
    membership_ids = [item["membership_id"] for item in memberships]
    if len(membership_ids) != len(set(membership_ids)):
        raise ThemeTaxonomyError("MEMBERSHIP_ID_DUPLICATE")
    _assert_membership_intervals(memberships)
    if approval["approval_status"] == "RATIFIED":
        if not edges or not memberships:
            raise ThemeTaxonomyError("RATIFIED_GRAPH_INCOMPLETE")
        covered = sorted({item["market"] for item in memberships})
        if covered != contract["required_markets_for_ratified_graph"]:
            raise ThemeTaxonomyError(f"RATIFIED_MARKET_COVERAGE_INCOMPLETE:{covered}")
    nodes_out = sorted(nodes.values(), key=lambda item: item["theme_id"])
    edges_out = sorted(edges, key=lambda item: item["edge_id"])
    memberships_out = sorted(memberships, key=lambda item: item["membership_id"])
    active_nodes = [item for item in nodes_out if _active(item["valid_from"], item["valid_to"], as_of_date)]
    active_edges = [item for item in edges_out if _active(item["valid_from"], item["valid_to"], as_of_date)]
    active_memberships = [item for item in memberships_out if _active(item["valid_from"], item["valid_to"], as_of_date)]
    active_covered_markets = sorted({item["market"] for item in active_memberships})
    # RATIFIED_MARKET_COVERAGE_INCOMPLETE above only proves the ratified
    # *document* once named both markets somewhere across its full history.
    # Individual edges/memberships carry their own valid_from/valid_to, which
    # can lapse or not yet have started independently of the approval's own
    # effective window. A graph is only truly EFFECTIVE_RATIFIED_GRAPH -- and
    # only then may it authorize the Global Asset Master membership adapter,
    # the thing that actually "connects US/KR names into a common Theme
    # graph" -- when the markets required for ratification are still active
    # as of as_of_date, with at least one active edge to place them in.
    # Otherwise this is temporal inactivity, not a document defect, so it
    # deactivates exactly like a not-yet-effective approval window does
    # rather than raising.
    graph_currently_effective = (
        effective_ratified
        and bool(active_edges)
        and active_covered_markets == contract["required_markets_for_ratified_graph"]
    )
    adapter = []
    if graph_currently_effective:
        adapter = [
            {
                "adapter_status": contract["membership_adapter_status"],
                "asset_id": item["asset_id"], "market": item["market"],
                "membership_type": "THEME", "membership_id": item["theme_id"],
                "value_chain_role_id": item["role_id"],
                "valid_from": item["valid_from"], "valid_to": item["valid_to"],
                "taxonomy_decision": {
                    "decision_id": approval["decision_id"],
                    "decision_sha256": approval["decision_sha256"],
                    "ratified_by": approval["ratified_by"],
                    "ratified_at_utc": approval["ratified_at_utc"],
                },
                "evidence": copy.deepcopy(item["evidence"]),
            }
            for item in active_memberships
        ]
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"], "taxonomy_id": taxonomy_id,
        "as_of_date": as_of_date,
        "graph_status": "EFFECTIVE_RATIFIED_GRAPH" if graph_currently_effective else "DRAFT_OR_NOT_EFFECTIVE_GRAPH",
        "approval": approval,
        "node_count": len(nodes_out), "edge_count": len(edges_out),
        "membership_count": len(memberships_out),
        "active_node_count": len(active_nodes), "active_membership_count": len(active_memberships),
        "active_edge_count": len(active_edges),
        "covered_markets": sorted({item["market"] for item in memberships_out}),
        "active_covered_markets": active_covered_markets,
        "nodes": nodes_out, "edges": edges_out, "memberships": memberships_out,
        "theme_membership_authorized": graph_currently_effective,
        "global_asset_master_membership_adapter": adapter,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "REPOSITORY_DEFAULT_TAXONOMY_ABSENT", "SOURCE_HIERARCHY_UNRATIFIED",
            "ROTATION_SCORING_UNRATIFIED", "TRACKED_TAXONOMY_NOT_IMPLEMENTED",
            "GLOBAL_ASSET_MASTER_INGESTION_NOT_IMPLEMENTED", "PRODUCTION_NOT_AUTHORIZED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT.resolve())
    except ValueError:
        pass
    else:
        raise ThemeTaxonomyError(f"TRACKED_OUTPUT_FORBIDDEN:{path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(name, path)
    except Exception:
        try:
            os.unlink(name)
        except FileNotFoundError:
            pass
        raise


def run(input_path: Path, output_path: Path) -> int:
    try:
        write_json_atomic(output_path, build_packet(_read_json(input_path)))
        return 0
    except (ThemeTaxonomyError, OSError, TypeError, ValueError) as exc:
        print(f"theme taxonomy failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate an externally ratified Theme graph")
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    return run(args.input, args.out)


if __name__ == "__main__":
    raise SystemExit(main())
