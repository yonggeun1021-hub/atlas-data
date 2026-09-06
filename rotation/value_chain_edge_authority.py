#!/usr/bin/env python3
"""P2-01 cross-market Value-Chain EDGE authority layer.

CIO architecture decision (2026-09-04): Korea / US / Crypto market-native
classification families stay authoritative in their own markets. This module
does **not** create a unified 3-market Theme taxonomy and does **not**
re-validate or redefine market-native membership. It adds a separate,
evidence-bound authority for cross-market value-chain EDGEs whose two
endpoints each *reference* an already-ratified market-native membership.

Node references are resolved exclusively against an already-built
``theme_taxonomy_packet/2`` (``rotation/theme_taxonomy.py`` +
``rotation/theme_taxonomy_authority.py``, reused here, not forked). A node
reference is valid only when the supplied packet is byte-verified
tamper-free, reports ``theme_membership_authorized: true`` (i.e. an
independent ``theme_taxonomy_authority_registry/1`` record already backs it),
and the claimed membership is present in that packet's own
``global_asset_master_membership_adapter``. Crypto is a structurally allowed
``market`` value but has no wired membership source in this slice -- see
``docs/value_chain_edge_authority_contract.md`` -- so a Crypto node
reference always fails closed to ``UNKNOWN`` rather than being fabricated.

Edges carry their own exact source/evidence binding (hash-bound, reusing the
same market source/host allow-list as ``theme_taxonomy_contract.json``) and
their own ``valid_from``/``valid_to`` window. An edge is authorized only when
BOTH endpoint node references independently resolve to a ratified
market-native membership AND a matching ``RATIFIED`` row exists in the
separate, git-provenance-verified ``value_chain_edge_authority_registry/1``.
That registry mechanism reuses the same generic git first-seen / tamper /
PIT-safety primitives as ``theme_taxonomy_authority.py`` (imported, not
copied) applied to a new record shape scoped to one edge at a time.

The packet additionally reports a purely descriptive per-market-pair
linkage roll-up (``market_pair_linkage``) so the cross-market question this
layer exists to answer -- is a US and a Korea security actually joined by a
ratified value-chain edge yet -- is machine-readable instead of having to be
re-derived by every consumer. It is a count of the edge statuses already
decided above; it grants nothing and can never turn an ``UNKNOWN_*`` edge
into a linkage.

No scoring, ranking, weighting, Stage, Production, capital, order, or
trading authority is added anywhere in this module. Partial graphs are
expected: any node or edge that cannot be resolved reports an explicit
``UNKNOWN_*`` status rather than being silently accepted or erroring the
whole packet.

The CLI validates one external ``value_chain_edge_input/1`` document and
writes the resulting packet through ``theme_taxonomy.write_json_atomic``,
which refuses any destination inside the repository -- there is still no
tracked output path and no repository-default edge catalog.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import json
from pathlib import Path
from urllib.parse import urlparse

try:
    from rotation import theme_taxonomy as TT
    from rotation import theme_taxonomy_authority as TTA
except ModuleNotFoundError:  # direct ``python rotation/value_chain_edge_authority.py`` CLI
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from rotation import theme_taxonomy as TT
    from rotation import theme_taxonomy_authority as TTA


ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "value_chain_edge_authority_contract.json"
REGISTRY_PATH = ROOT / "config" / "value_chain_edge_authority_registry.json"
REGISTRY_SCHEMA = "value_chain_edge_authority_registry/1"
EVIDENCE_SCHEMA = "value_chain_edge_approval_evidence/1"
INPUT_SCHEMA_VERSION = "value_chain_edge_input/1"
OUTPUT_SCHEMA_VERSION = "value_chain_edge_packet/1"

# Reused, not redefined: same token/hash/time primitives already hardened in
# theme_taxonomy_authority.py.
TOKEN_RE = TTA.TOKEN_RE
SHA256_RE = TTA.SHA256_RE
UTC_RE = TTA.UTC_RE


class ValueChainEdgeAuthorityError(ValueError):
    """Fail-closed value-chain edge authority contract violation."""


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueChainEdgeAuthorityError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "value_chain_edge_authority/1",
        "input_schema_version": INPUT_SCHEMA_VERSION,
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "allowed_markets": ["CRYPTO", "KOREA", "US"],
        "allowed_node_membership_sources": ["theme_taxonomy_packet/2"],
        "allowed_relation_types": [
            "COMPETES_WITH", "CUSTOMER_OF", "DEPENDS_ON", "ENABLES", "SUPPLIES",
        ],
        "membership_adapter_status": "REFERENCE_ONLY_NO_REDEFINITION",
        "policy_status": {
            "repository_default_edge_catalog": "ABSENT",
            "edge_authority_registry": "PRESENT_EMPTY",
            "crypto_membership_source": "STRUCTURALLY_ALLOWED_NOT_WIRED",
            "rotation_scoring": "OUT_OF_SCOPE",
            "capital_action_order_authority": "OUT_OF_SCOPE",
        },
        "authority": {
            "external_ratification_claim_validation_only": True,
            "edge_activation_authorized": False,
            "node_membership_redefinition_authorized": False,
            "theme_inference_authorized": False,
            "membership_inference_authorized": False,
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
        raise ValueChainEdgeAuthorityError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise ValueChainEdgeAuthorityError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise ValueChainEdgeAuthorityError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _token(value, code: str) -> str:
    if not isinstance(value, str) or TOKEN_RE.fullmatch(value) is None:
        raise ValueChainEdgeAuthorityError(code)
    return value


def _text(value, code: str) -> str:
    if not isinstance(value, str) or not value.strip() or value.strip() != value:
        raise ValueChainEdgeAuthorityError(code)
    return value


AUTHORITY_FALSE = {
    "edge_activation_authorized": False,
    "node_membership_redefinition_authorized": False,
    "theme_inference_authorized": False,
    "membership_inference_authorized": False,
    "rotation_score_authorized": False,
    "candidate_ranking_authorized": False,
    "stage_promotion_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


# ---------------------------------------------------------------------------
# Node references -- bind to an already-ratified market-native membership.
# ---------------------------------------------------------------------------

def _validate_node_ref_claim(value: dict, contract: dict) -> dict:
    fields = {
        "node_ref_id", "market", "membership_source", "asset_id",
        "membership_id", "membership_packet",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueChainEdgeAuthorityError("NODE_REF_FIELDS_MISMATCH")
    node_ref_id = _token(value.get("node_ref_id"), "NODE_REF_ID_INVALID")
    market = value.get("market")
    if market not in contract["allowed_markets"]:
        raise ValueChainEdgeAuthorityError(f"NODE_REF_MARKET_INVALID:{node_ref_id}")
    membership_source = value.get("membership_source")
    if not isinstance(membership_source, str) or not membership_source:
        raise ValueChainEdgeAuthorityError(f"NODE_REF_MEMBERSHIP_SOURCE_INVALID:{node_ref_id}")
    asset_id = _token(value.get("asset_id"), f"NODE_REF_ASSET_ID_INVALID:{node_ref_id}")
    membership_id = _token(value.get("membership_id"), f"NODE_REF_MEMBERSHIP_ID_INVALID:{node_ref_id}")
    packet = value.get("membership_packet")
    if not isinstance(packet, dict):
        raise ValueChainEdgeAuthorityError(f"NODE_REF_MEMBERSHIP_PACKET_INVALID:{node_ref_id}")
    return {
        "node_ref_id": node_ref_id,
        "market": market,
        "membership_source": membership_source,
        "asset_id": asset_id,
        "membership_id": membership_id,
        "membership_packet": copy.deepcopy(packet),
    }


def _resolve_node_ref(node_ref: dict, contract: dict) -> dict:
    """Resolve a node reference strictly against an already-ratified,
    tamper-verified ``theme_taxonomy_packet/2``. Never inspects names,
    tickers, ETF membership, or watchlists -- absence of ratified evidence
    stays UNKNOWN, it is never inferred.
    """
    node_ref_id = node_ref["node_ref_id"]
    if node_ref["membership_source"] not in contract["allowed_node_membership_sources"]:
        return {
            "node_ref_id": node_ref_id, "status": "UNKNOWN_MEMBERSHIP_SOURCE_NOT_SUPPORTED",
            "market": node_ref["market"], "asset_id": node_ref["asset_id"],
            "membership_id": node_ref["membership_id"],
            "valid_from": None, "valid_to": None, "real_usable_from": None,
        }
    packet = node_ref["membership_packet"]
    # Independently re-derive the packet's own hash -- never trust a
    # caller-declared sha, recompute it the same way theme_taxonomy.py does.
    declared_sha = packet.get("payload_sha256")
    recomputed = TT.payload_sha256({k: v for k, v in packet.items() if k != "payload_sha256"})
    base = {
        "node_ref_id": node_ref_id, "market": node_ref["market"],
        "asset_id": node_ref["asset_id"], "membership_id": node_ref["membership_id"],
        "valid_from": None, "valid_to": None, "real_usable_from": None,
    }
    if (
        not isinstance(declared_sha, str)
        or declared_sha != recomputed
        or packet.get("schema_version") != TT.OUTPUT_SCHEMA_VERSION
    ):
        return {**base, "status": "UNKNOWN_MEMBERSHIP_PACKET_TAMPERED_OR_MALFORMED"}
    if packet.get("theme_membership_authorized") is not True:
        return {**base, "status": "UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED"}
    adapter = packet.get("global_asset_master_membership_adapter")
    if not isinstance(adapter, list):
        return {**base, "status": "UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED"}
    matches = [
        row for row in adapter
        if isinstance(row, dict)
        and row.get("membership_id") == node_ref["membership_id"]
        and row.get("asset_id") == node_ref["asset_id"]
        and row.get("market") == node_ref["market"]
    ]
    if len(matches) != 1:
        return {**base, "status": "UNKNOWN_MEMBERSHIP_NOT_IN_RATIFIED_ADAPTER"}
    row = matches[0]
    authority_resolution = packet.get("authority_resolution")
    real_usable_from = (
        authority_resolution.get("real_usable_from")
        if isinstance(authority_resolution, dict)
        else None
    )
    return {
        **base,
        "status": "RATIFIED_MARKET_NATIVE_MEMBERSHIP",
        "valid_from": row.get("valid_from"),
        "valid_to": row.get("valid_to"),
        "real_usable_from": real_usable_from,
    }


# ---------------------------------------------------------------------------
# Edges -- exact evidence binding, own effective window, independent registry.
# ---------------------------------------------------------------------------

def _validate_evidence(value: dict, from_market: str, to_market: str, tt_contract: dict, cutoff: dt.datetime, context: str) -> dict:
    fields = {"evidence_id", "claim_text", "source_identity"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueChainEdgeAuthorityError(f"EDGE_EVIDENCE_FIELDS_MISMATCH:{context}")
    evidence_id = _token(value.get("evidence_id"), f"EDGE_EVIDENCE_ID_INVALID:{context}")
    source = value.get("source_identity")
    last_error = None
    validated_source = None
    for market in dict.fromkeys([from_market, to_market]):
        if market not in tt_contract["market_sources"]:
            continue
        try:
            # Reused directly from theme_taxonomy.py -- not reimplemented.
            validated_source = TT._validate_source(source, market, cutoff, tt_contract, context)
            break
        except TT.ThemeTaxonomyError as exc:
            last_error = exc
    if validated_source is None:
        raise ValueChainEdgeAuthorityError(
            f"EDGE_EVIDENCE_SOURCE_INVALID:{context}:{last_error}"
        )
    return {
        "evidence_id": evidence_id,
        "claim_text": _text(value.get("claim_text"), f"EDGE_EVIDENCE_CLAIM_INVALID:{context}"),
        "source_identity": validated_source,
    }


def _validate_edge_claim(value: dict, node_refs: dict, contract: dict, cutoff: dt.datetime) -> dict:
    fields = {
        "edge_id", "from_node_ref_id", "to_node_ref_id", "relation_type",
        "evidence", "valid_from", "valid_to",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueChainEdgeAuthorityError("EDGE_FIELDS_MISMATCH")
    edge_id = _token(value.get("edge_id"), "EDGE_ID_INVALID")
    left = value.get("from_node_ref_id")
    right = value.get("to_node_ref_id")
    if left not in node_refs or right not in node_refs:
        raise ValueChainEdgeAuthorityError(f"EDGE_NODE_REF_UNKNOWN:{edge_id}")
    if left == right:
        raise ValueChainEdgeAuthorityError(f"EDGE_SELF_REFERENCE:{edge_id}")
    relation = value.get("relation_type")
    if relation not in contract["allowed_relation_types"]:
        raise ValueChainEdgeAuthorityError(f"EDGE_RELATION_INVALID:{edge_id}")
    try:
        start, end = TT._interval(value, edge_id)
    except TT.ThemeTaxonomyError as exc:
        raise ValueChainEdgeAuthorityError(f"EDGE_INTERVAL_INVALID:{edge_id}:{exc}") from exc
    tt_contract = TT.load_contract()
    # Evidence must exist no later than the graph's own as_of_date (the
    # packet-level PIT boundary) -- not the edge's own valid_from, which may
    # legitimately predate when the evidence documenting it was retrieved.
    evidence = _validate_evidence(
        value.get("evidence"), node_refs[left]["market"], node_refs[right]["market"],
        tt_contract, cutoff, edge_id,
    )
    return {
        "edge_id": edge_id, "from_node_ref_id": left, "to_node_ref_id": right,
        "relation_type": relation, "evidence": evidence,
        "valid_from": start, "valid_to": end,
    }


def edge_determining_claim(edge: dict, resolved_left: dict, resolved_right: dict) -> dict:
    """Exact bytes a value-chain edge authority record must bind to.

    Deliberately excludes ``real_usable_from``/adapter contents (those are
    already independently git-provenance-verified inside the referenced
    theme_taxonomy packet) and keeps only the identity/evidence/window that
    is unique to this edge claim.
    """
    return {
        "edge_id": edge["edge_id"],
        "relation_type": edge["relation_type"],
        "from_node_ref": {
            "market": resolved_left["market"], "asset_id": resolved_left["asset_id"],
            "membership_id": resolved_left["membership_id"],
        },
        "to_node_ref": {
            "market": resolved_right["market"], "asset_id": resolved_right["asset_id"],
            "membership_id": resolved_right["membership_id"],
        },
        "evidence": edge["evidence"],
        "valid_from": edge["valid_from"], "valid_to": edge["valid_to"],
    }


def determining_payload(record: dict) -> dict:
    fields = (
        "rule_id", "rule_version", "approval_status", "ratified_at",
        "effective_from", "effective_to", "edge_id", "approved_edge_payload_sha256",
    )
    return {field: record.get(field) for field in fields}


def validate_registry_record(record: dict) -> dict:
    fields = {
        "rule_id", "rule_version", "approval_status", "ratified_at",
        "effective_from", "effective_to", "edge_id", "approved_edge_payload_sha256",
        "approval_evidence_ref", "approval_evidence_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise ValueChainEdgeAuthorityError("EDGE_AUTHORITY_RECORD_FIELDS_MISMATCH")
    _token(record["rule_id"], "RULE_ID_INVALID")
    _token(record["rule_version"], "RULE_VERSION_INVALID")
    _token(record["edge_id"], "EDGE_ID_INVALID")
    if record["approval_status"] not in {"PROPOSED", "RATIFIED", "REVOKED"}:
        raise ValueChainEdgeAuthorityError("APPROVAL_STATUS_INVALID")
    TTA._utc(record["ratified_at"], "RATIFIED_AT_INVALID")
    start = TTA._utc(record["effective_from"], "EFFECTIVE_FROM_INVALID")
    end_value = record["effective_to"]
    end = None if end_value is None else TTA._utc(end_value, "EFFECTIVE_TO_INVALID")
    if end is not None and end <= start:
        raise ValueChainEdgeAuthorityError("EFFECTIVE_INTERVAL_INVALID")
    if not isinstance(record["approved_edge_payload_sha256"], str) or SHA256_RE.fullmatch(record["approved_edge_payload_sha256"]) is None:
        raise ValueChainEdgeAuthorityError("EDGE_PAYLOAD_SHA256_INVALID")
    ref = record["approval_evidence_ref"]
    if not isinstance(ref, str) or not ref or ref.startswith("/") or ".." in Path(ref).parts:
        raise ValueChainEdgeAuthorityError("APPROVAL_EVIDENCE_REF_INVALID")
    if not isinstance(record["approval_evidence_sha256"], str) or SHA256_RE.fullmatch(record["approval_evidence_sha256"]) is None:
        raise ValueChainEdgeAuthorityError("APPROVAL_EVIDENCE_SHA256_INVALID")
    return json.loads(json.dumps(record))


def load_registry(path: Path = REGISTRY_PATH) -> dict:
    path = Path(path).resolve()
    try:
        raw = path.read_bytes()
        value = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueChainEdgeAuthorityError("REGISTRY_READ_FAILED") from exc
    if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
        raise ValueChainEdgeAuthorityError("REGISTRY_FIELDS_MISMATCH")
    if value["schema_version"] != REGISTRY_SCHEMA or not isinstance(value["records"], list):
        raise ValueChainEdgeAuthorityError("REGISTRY_SCHEMA_MISMATCH")
    records = [validate_registry_record(record) for record in value["records"]]
    keys = [(r["rule_id"], r["rule_version"]) for r in records]
    if len(keys) != len(set(keys)):
        raise ValueChainEdgeAuthorityError("EDGE_AUTHORITY_RECORD_DUPLICATE")
    value["records"] = records
    value["_source_path"] = str(path)
    return value


def _first_seen_edge_record(repo: Path, commit: str, rel: str, expected: dict) -> str | None:
    for candidate in TTA._commits(repo, commit, rel):
        blob = TTA._git_blob(repo, candidate, rel)
        if blob is None:
            continue
        try:
            document = json.loads(blob.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        for row in document.get("records", []) if isinstance(document, dict) else []:
            if isinstance(row, dict) and determining_payload(row) == expected:
                return TTA._commit_time(repo, candidate)
    return None


def _public_result(status: str, **diagnostics) -> dict:
    result = {"status": status, "authority": dict(AUTHORITY_FALSE)}
    result.update(diagnostics)
    return result


def resolve_edge_authority(
    edge_claim: dict,
    edge_hash: str,
    as_of_date: str,
    registry_path: Path = REGISTRY_PATH,
    trusted_commit: str | None = None,
) -> dict:
    """Reuses the same git-provenance/tamper/PIT mechanism as
    ``theme_taxonomy_authority.resolve_graph_authority`` (its low-level
    primitives are imported directly, not reimplemented) applied to one
    edge's own determining payload instead of a whole taxonomy graph.
    """
    try:
        decision_day = dt.date.fromisoformat(as_of_date)
        if decision_day.isoformat() != as_of_date:
            raise ValueError
    except (TypeError, ValueError):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_AS_OF_DATE_INVALID")
    try:
        registry = load_registry(registry_path)
    except ValueChainEdgeAuthorityError:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_REGISTRY_INVALID")
    path = Path(registry["_source_path"])
    repo = TTA._repo_root(path)
    if repo is None:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")
    commit = TTA._trusted_commit(repo, trusted_commit)
    rel = TTA._relative(repo, path)
    if commit is None or rel is None:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")
    try:
        disk = path.read_bytes()
    except OSError:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")
    if TTA._git_blob(repo, commit, rel) != disk:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED")
    if trusted_commit is None:
        dirty = TTA._run_git(repo, "status", "--porcelain", "--", rel)
        if dirty:
            return _public_result("AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED")

    matching = [
        row for row in registry["records"]
        if row["edge_id"] == edge_claim["edge_id"]
        and row["approved_edge_payload_sha256"] == edge_hash
    ]
    if not matching:
        return _public_result(
            "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD",
            approved_edge_payload_sha256=edge_hash,
        )
    if len(matching) != 1:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_AMBIGUOUS_AUTHORITY_RECORD")
    row = matching[0]
    if row["approval_status"] != "RATIFIED":
        return _public_result("AUTHORITY_NOT_COMPUTABLE_UNRATIFIED_RECORD")

    evidence_path = (repo / row["approval_evidence_ref"]).resolve()
    try:
        evidence_path.relative_to(repo)
        evidence_bytes = evidence_path.read_bytes()
    except (ValueError, OSError):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")
    evidence_rel = TTA._relative(repo, evidence_path)
    if (
        evidence_rel is None
        or TTA.sha256_bytes(evidence_bytes) != row["approval_evidence_sha256"]
        or TTA._git_blob(repo, commit, evidence_rel) != evidence_bytes
    ):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")
    try:
        evidence = json.loads(evidence_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")
    expected_evidence = {
        "schema_version": EVIDENCE_SCHEMA,
        "approved_full_payload_sha256": TTA.payload_sha256(determining_payload(row)),
        **determining_payload(row),
    }
    if evidence != expected_evidence:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")

    row_first_seen = _first_seen_edge_record(repo, commit, rel, determining_payload(row))
    evidence_first_seen = TTA._first_seen_exact_bytes(repo, commit, evidence_rel, evidence_bytes)
    if row_first_seen is None or evidence_first_seen is None:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED")
    try:
        usable = max(
            TTA._utc(row["effective_from"], "EFFECTIVE_FROM_INVALID"),
            TTA._utc(row["ratified_at"], "RATIFIED_AT_INVALID"),
            TTA._utc(row_first_seen, "ROW_FIRST_SEEN_INVALID"),
            TTA._utc(evidence_first_seen, "EVIDENCE_FIRST_SEEN_INVALID"),
        )
        effective_to = (
            None if row["effective_to"] is None
            else TTA._utc(row["effective_to"], "EFFECTIVE_TO_INVALID")
        )
    except TTA.ThemeTaxonomyAuthorityError:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_TIME_INVALID")
    if usable.date() == decision_day:
        return _public_result(
            "AUTHORITY_NOT_COMPUTABLE_DATE_ONLY_PRECISION",
            real_usable_from=usable.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    decision_at = dt.datetime.combine(decision_day, dt.time.min, tzinfo=dt.timezone.utc)
    if usable > decision_at:
        return _public_result(
            "AUTHORITY_NOT_COMPUTABLE_PIT_VIOLATION",
            real_usable_from=usable.strftime("%Y-%m-%dT%H:%M:%SZ"),
        )
    if effective_to is not None and decision_at >= effective_to:
        return _public_result("AUTHORITY_NOT_COMPUTABLE_NO_ACTIVE_AUTHORITY_RECORD")

    authority = dict(AUTHORITY_FALSE)
    authority["edge_activation_authorized"] = True
    return {
        "status": "AUTHORIZED", "authority": authority,
        "rule_id": row["rule_id"], "rule_version": row["rule_version"],
        "approved_edge_payload_sha256": edge_hash,
        "real_usable_from": usable.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "trusted_commit": commit,
    }


# ---------------------------------------------------------------------------
# Descriptive linkage roll-up -- counts already-decided edge statuses only.
# ---------------------------------------------------------------------------

def _market_pair_linkage(edges_out: list, node_refs: dict) -> list:
    """Group already-resolved edges by their unordered endpoint market pair.

    Derived strictly from ``edge_status``/``edge_activation_authorized``
    decided above; it never re-checks membership, evidence, or the registry,
    and a pair is reported as linked only when at least one of its edges is
    already an activated ``RATIFIED_CROSS_MARKET_VALUE_CHAIN_EDGE``. Same
    market pairs (``cross_market: false``) are reported too rather than
    dropped, so a graph that only looks cross-market is visibly not.
    """
    pairs: dict[tuple[str, str], dict] = {}
    for entry in edges_out:
        left = node_refs[entry["from_node_ref_id"]]["market"]
        right = node_refs[entry["to_node_ref_id"]]["market"]
        key = (left, right) if left <= right else (right, left)
        row = pairs.setdefault(key, {
            "market_pair": list(key),
            "cross_market": key[0] != key[1],
            "edge_count": 0,
            "activated_edge_count": 0,
            "edge_status_counts": {},
        })
        row["edge_count"] += 1
        if entry["edge_activation_authorized"]:
            row["activated_edge_count"] += 1
        status = entry["edge_status"]
        row["edge_status_counts"][status] = row["edge_status_counts"].get(status, 0) + 1

    linkage = []
    for key in sorted(pairs):
        row = pairs[key]
        row["edge_status_counts"] = dict(sorted(row["edge_status_counts"].items()))
        row["linkage_status"] = (
            "RATIFIED_MARKET_PAIR_LINKAGE" if row["activated_edge_count"]
            else "UNKNOWN_MARKET_PAIR_LINKAGE_NOT_RATIFIED"
        )
        linkage.append(row)
    return linkage


# ---------------------------------------------------------------------------
# Top-level packet builder.
# ---------------------------------------------------------------------------

def build_packet(
    value: dict,
    contract: dict | None = None,
    registry_path: Path = REGISTRY_PATH,
    trusted_commit: str | None = None,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    fields = {"schema_version", "graph_id", "as_of_date", "node_refs", "edges"}
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise ValueChainEdgeAuthorityError("INPUT_SCHEMA_MISMATCH")
    if set(value) != fields:
        raise ValueChainEdgeAuthorityError("INPUT_FIELDS_MISMATCH")
    graph_id = _token(value.get("graph_id"), "GRAPH_ID_INVALID")
    as_of_date = value.get("as_of_date")
    if not TT._valid_date(as_of_date):
        raise ValueChainEdgeAuthorityError("AS_OF_DATE_INVALID")
    raw_node_refs = value.get("node_refs")
    raw_edges = value.get("edges")
    if not isinstance(raw_node_refs, list) or not raw_node_refs:
        raise ValueChainEdgeAuthorityError("NODE_REFS_EMPTY")
    if not isinstance(raw_edges, list):
        raise ValueChainEdgeAuthorityError("EDGES_LIST_INVALID")

    node_refs = {}
    for raw in raw_node_refs:
        claim = _validate_node_ref_claim(raw, contract)
        if claim["node_ref_id"] in node_refs:
            raise ValueChainEdgeAuthorityError(f"NODE_REF_ID_DUPLICATE:{claim['node_ref_id']}")
        node_refs[claim["node_ref_id"]] = claim
    resolved_nodes = {
        node_ref_id: _resolve_node_ref(claim, contract)
        for node_ref_id, claim in node_refs.items()
    }

    evidence_cutoff = dt.datetime.combine(dt.date.fromisoformat(as_of_date), dt.time.max)
    edges_out = []
    edge_ids = set()
    for raw in raw_edges:
        edge = _validate_edge_claim(raw, node_refs, contract, evidence_cutoff)
        if edge["edge_id"] in edge_ids:
            raise ValueChainEdgeAuthorityError(f"EDGE_ID_DUPLICATE:{edge['edge_id']}")
        edge_ids.add(edge["edge_id"])
        left = resolved_nodes[edge["from_node_ref_id"]]
        right = resolved_nodes[edge["to_node_ref_id"]]
        both_ratified = (
            left["status"] == "RATIFIED_MARKET_NATIVE_MEMBERSHIP"
            and right["status"] == "RATIFIED_MARKET_NATIVE_MEMBERSHIP"
        )
        entry = {
            "edge_id": edge["edge_id"], "from_node_ref_id": edge["from_node_ref_id"],
            "to_node_ref_id": edge["to_node_ref_id"], "relation_type": edge["relation_type"],
            "evidence": edge["evidence"], "valid_from": edge["valid_from"],
            "valid_to": edge["valid_to"],
        }
        if not both_ratified:
            entry["edge_status"] = "UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED"
            entry["edge_activation_authorized"] = False
            entry["authority_resolution"] = None
            edges_out.append(entry)
            continue
        if not TT._contains(left["valid_from"], left["valid_to"], edge["valid_from"], edge["valid_to"]) or \
           not TT._contains(right["valid_from"], right["valid_to"], edge["valid_from"], edge["valid_to"]):
            entry["edge_status"] = "UNKNOWN_INTERVAL_OUTSIDE_NODE_MEMBERSHIP_WINDOW"
            entry["edge_activation_authorized"] = False
            entry["authority_resolution"] = None
            edges_out.append(entry)
            continue
        claim_payload = edge_determining_claim(edge, left, right)
        edge_hash = TTA.payload_sha256(claim_payload)
        authority_resolution = resolve_edge_authority(
            edge, edge_hash, as_of_date, registry_path=registry_path, trusted_commit=trusted_commit,
        )
        activated = authority_resolution["status"] == "AUTHORIZED"
        entry["edge_status"] = (
            "RATIFIED_CROSS_MARKET_VALUE_CHAIN_EDGE" if activated
            else "UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED"
        )
        entry["edge_activation_authorized"] = activated
        entry["approved_edge_payload_sha256"] = edge_hash
        entry["authority_resolution"] = authority_resolution
        edges_out.append(entry)

    edges_out.sort(key=lambda item: item["edge_id"])
    node_refs_out = [
        {**node_refs[node_ref_id], "resolution": resolved_nodes[node_ref_id]}
        for node_ref_id in sorted(node_refs)
    ]
    activated_edge_count = sum(1 for item in edges_out if item["edge_activation_authorized"])
    market_pair_linkage = _market_pair_linkage(edges_out, node_refs)
    cross_market = [row for row in market_pair_linkage if row["cross_market"]]
    output_authority = copy.deepcopy(contract["authority"])
    output_authority["edge_activation_authorized"] = activated_edge_count > 0
    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "graph_id": graph_id, "as_of_date": as_of_date,
        "node_ref_count": len(node_refs_out), "edge_count": len(edges_out),
        "activated_edge_count": activated_edge_count,
        "cross_market_edge_count": sum(row["edge_count"] for row in cross_market),
        "activated_cross_market_edge_count": sum(row["activated_edge_count"] for row in cross_market),
        "market_pair_linkage": market_pair_linkage,
        "node_refs": node_refs_out, "edges": edges_out,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": output_authority,
        "unresolved_boundaries": [
            "REPOSITORY_DEFAULT_EDGE_CATALOG_ABSENT",
            "EDGE_AUTHORITY_REGISTRY_PRESENT_BUT_EMPTY",
            "CRYPTO_MEMBERSHIP_SOURCE_NOT_WIRED",
            "ROTATION_SCORING_OUT_OF_SCOPE",
            "CAPITAL_ACTION_ORDER_AUTHORITY_OUT_OF_SCOPE",
        ],
    }
    packet["payload_sha256"] = TT.payload_sha256(packet)
    return packet


def run(
    input_path: Path,
    output_path: Path,
    registry_path: Path = REGISTRY_PATH,
    trusted_commit: str | None = None,
) -> int:
    try:
        packet = build_packet(
            _read_json(input_path), registry_path=registry_path, trusted_commit=trusted_commit,
        )
        # Reused from theme_taxonomy.py: refuses any destination inside the
        # repository, so no tracked output path is introduced here either.
        TT.write_json_atomic(output_path, packet)
        return 0
    except (ValueChainEdgeAuthorityError, TT.ThemeTaxonomyError, OSError, TypeError, ValueError) as exc:
        print(f"value chain edge authority failed: {exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate an external cross-market value-chain edge document",
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--trusted-commit")
    args = parser.parse_args()
    return run(args.input, args.out, args.registry, args.trusted_commit)


if __name__ == "__main__":
    raise SystemExit(main())
