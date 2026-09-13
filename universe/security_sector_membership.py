#!/usr/bin/env python3
"""Build and read the KR security <-> sector membership (security_sector_membership/1).

Maps every KIS common stock to at most one ratified P2-03 rotation sector series
(``theme_id`` of ``config/korea_rotation_sector_identity_binding_document.json``)
from the KIS stock master sector codes and the KIS index-code master.

Source policy is CIO decision CIO-TKT3-MEMBERSHIP-D1-D5-20260914:

- KIS_ONLY, ``source_count = 1``, ``verification = SINGLE_SOURCE_KIS``. This
  satisfies T2 (PAPER) condition C5 only; T3/REAL keeps BOTH_MUST_AGREE.
- Explicit (market, code) sector table with exactly two ratified aliases; any
  other name mismatch is UNMAPPED, never punctuation-normalized.
- ACTIVE uses the deepest non-zero KIS level only (ACTIVE <= 1 per asset).
  Parent series are a derived ``parent_view`` that never counts as ACTIVE.
- A changed assignment is PENDING_CHANGE for one publication before ACTIVE.
- No-code stocks stay UNMAPPED; nothing is inferred.

Full per-stock documents are private evidence. The public summary carries only
counts and hashes. This module does not fetch anything, approve candidates or
authorize PAPER/REAL orders.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import krx_investable_registry as REG  # noqa: E402

CONTRACT_PATH = ROOT / "config" / "security_sector_membership_contract.json"
DOCUMENT_SCHEMA_VERSION = "security_sector_membership/1"
PUBLIC_SUMMARY_SCHEMA_VERSION = "security_sector_membership_public_summary/1"
INPUT_SCHEMA_VERSION = "security_sector_membership_input/1"
GENERATOR_PATH = "universe/security_sector_membership.py"

ACTIVE = "ACTIVE"
PENDING_CHANGE = "PENDING_CHANGE"
CONFLICT = "CONFLICT"
UNMAPPED = "UNMAPPED"
ROW_STATUSES = (ACTIVE, PENDING_CHANGE, CONFLICT, UNMAPPED)
UNMAPPED_REASONS = (
    "NO_SECTOR_CODE", "SECTOR_CODE_NOT_IN_TABLE", "SECTOR_CODE_NAME_CHANGED",
    "HIERARCHY_UNDECLARED", "SECTOR_SMALL_LEVEL_UNDECLARED",
)
MARKETS = ("KOSDAQ", "KOSPI")
ZERO = "0000"
VERIFICATION = "SINGLE_SOURCE_KIS"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SESSION_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_RE = re.compile(r"^[0-9]{4}$")
ROW_FIELDS = (
    "row_id", "asset_id", "listing_id", "kis_standard_code", "market", "status",
    "unmapped_reason", "membership_id", "series_identity", "kis_sector_code",
    "leaf_level", "parent_membership_id", "source_count", "verification",
    "source_refs", "effective_from", "effective_from_session", "effective_to",
    "effective_to_session",
)
AUTHORITY_KEYS = (
    "membership_is_candidate_approval", "t2_eligibility_granted",
    "t3_real_candidate_authorized", "paper_order_authorized",
    "real_order_authorized", "capital_movement_authorized", "production_authorized",
)


class MembershipError(ValueError):
    """The membership cannot be built or read without violating its contract."""


canonical_json = REG.canonical_json
payload_sha256 = REG.payload_sha256


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _self_hashed(value: dict) -> dict:
    value = copy.deepcopy(value)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = payload_sha256(value)
    return value


def _check_self_hash(value: dict, code: str) -> None:
    unsigned = copy.deepcopy(value)
    digest = unsigned.pop("payload_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise MembershipError(code)


def _utc(value: object, code: str) -> str:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise MembershipError(code)
    try:
        dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise MembershipError(code) from exc
    return value


def _session(value: object, code: str) -> str:
    if not isinstance(value, str) or not SESSION_RE.fullmatch(value):
        raise MembershipError(code)
    try:
        dt.date.fromisoformat(value)
    except ValueError as exc:
        raise MembershipError(code) from exc
    return value


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MembershipError(f"JSON_READ_FAILED:{Path(path).name}:{type(exc).__name__}") from exc
    if not isinstance(value, dict):
        raise MembershipError(f"JSON_ROOT_NOT_OBJECT:{Path(path).name}")
    return value


# ------------------------------------------------------------------ contract
def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = _read_json(path)
    if (
        contract.get("schema_version") != 1
        or contract.get("contract_version") != "security_sector_membership/1"
        or contract.get("document_schema_version") != DOCUMENT_SCHEMA_VERSION
        or contract.get("public_summary_schema_version") != PUBLIC_SUMMARY_SCHEMA_VERSION
        or contract.get("market_scope") != "KR"
        or contract.get("markets") != list(MARKETS)
        or contract.get("row_statuses") != list(ROW_STATUSES)
        or contract.get("unmapped_reasons") != list(UNMAPPED_REASONS)
    ):
        raise MembershipError("CONTRACT_IDENTITY_INVALID")
    authority = contract.get("authority")
    if not isinstance(authority, dict) or tuple(authority) != AUTHORITY_KEYS or any(v is not False for v in authority.values()):
        raise MembershipError("CONTRACT_AUTHORITY_INVALID")
    policy = contract.get("source_policy") or {}
    if (
        policy.get("kr_sector_membership_source") != "KIS_ONLY"
        or policy.get("source_count") != 1
        or policy.get("verification") != VERIFICATION
        or policy.get("satisfies_t2_c5") is not True
        or policy.get("satisfies_t3") is not False
        or policy.get("t3_target") != "BOTH_MUST_AGREE"
        or policy.get("change_detection", {}).get("pending_change_publications") != 1
    ):
        raise MembershipError("CONTRACT_SOURCE_POLICY_INVALID")
    table = contract.get("sector_code_table")
    if not isinstance(table, list) or not table:
        raise MembershipError("CONTRACT_SECTOR_TABLE_INVALID")
    keys = set()
    for row in table:
        if (
            set(row) != {"market", "kis_sector_code", "source_name", "series_identity", "membership_id", "match"}
            or row["market"] not in MARKETS
            or not CODE_RE.fullmatch(str(row["kis_sector_code"]))
            or row["match"] not in ("EXACT", "RATIFIED_ALIAS")
            or not str(row["series_identity"]).startswith(f"{row['market']}::")
        ):
            raise MembershipError("CONTRACT_SECTOR_TABLE_ROW_INVALID")
        key = (row["market"], row["kis_sector_code"])
        if key in keys:
            raise MembershipError("CONTRACT_SECTOR_TABLE_DUPLICATE_CODE")
        keys.add(key)
        if row["match"] == "EXACT" and row["series_identity"] != f"{row['market']}::{row['source_name']}":
            raise MembershipError("CONTRACT_SECTOR_TABLE_EXACT_NAME_MISMATCH")
    series = [row["series_identity"] for row in table]
    if len(series) != len(set(series)):
        raise MembershipError("CONTRACT_SECTOR_TABLE_DUPLICATE_SERIES")
    aliases = contract.get("ratified_aliases")
    alias_rows = [
        {k: row[k] for k in ("market", "kis_sector_code", "source_name", "series_identity")}
        for row in table if row["match"] == "RATIFIED_ALIAS"
    ]
    def _alias_key(entry):
        return (entry.get("market"), entry.get("kis_sector_code"))

    if (
        not isinstance(aliases, list)
        or len({_alias_key(a) for a in aliases}) != len(aliases)
        or sorted(aliases, key=_alias_key) != sorted(alias_rows, key=_alias_key)
    ):
        raise MembershipError("CONTRACT_ALIAS_TABLE_MISMATCH")
    by_code = {(row["market"], row["kis_sector_code"]): row for row in table}
    parents = contract.get("parent_view")
    if not isinstance(parents, list):
        raise MembershipError("CONTRACT_PARENT_VIEW_INVALID")
    seen_children = set()
    for parent in parents:
        market = parent.get("market")
        prow = by_code.get((market, parent.get("parent_kis_sector_code")))
        if (
            prow is None
            or prow["series_identity"] != parent.get("parent_series_identity")
            or prow["membership_id"] != parent.get("parent_membership_id")
            or not isinstance(parent.get("child_kis_sector_codes"), list)
            or not parent["child_kis_sector_codes"]
        ):
            raise MembershipError("CONTRACT_PARENT_VIEW_INVALID")
        child_ids = []
        for code in parent["child_kis_sector_codes"]:
            crow = by_code.get((market, code))
            if crow is None or (market, code) in seen_children or code == parent["parent_kis_sector_code"]:
                raise MembershipError("CONTRACT_PARENT_VIEW_CHILD_INVALID")
            seen_children.add((market, code))
            child_ids.append(crow["membership_id"])
        if parent.get("child_membership_ids") != child_ids:
            raise MembershipError("CONTRACT_PARENT_VIEW_CHILD_IDS_MISMATCH")
    return copy.deepcopy(contract)


def load_binding(contract: dict) -> dict[str, str]:
    """series_identity -> theme_id, verified against the pinned binding hash."""
    spec = contract["taxonomy_binding"]
    document = _read_json(ROOT / spec["document_path"])
    _check_self_hash(document, "BINDING_DOCUMENT_SHA256_MISMATCH")
    if document["payload_sha256"] != spec["document_payload_sha256"]:
        raise MembershipError("BINDING_DOCUMENT_NOT_PINNED")
    taxonomy = _read_json(ROOT / spec["taxonomy_binding_path"])
    if taxonomy.get("taxonomy_packet_sha256") != spec["document_payload_sha256"]:
        raise MembershipError("TAXONOMY_BINDING_PACKET_MISMATCH")
    if document.get("binding_status") != "RATIFIED" or document.get("taxonomy_id") != spec["taxonomy_id"]:
        raise MembershipError("BINDING_DOCUMENT_NOT_RATIFIED")
    mapping = {}
    for scope in document["benchmark_scopes"]:
        for member in scope["members"]:
            mapping[member["series_identity"]] = member["theme_id"]
    table = contract["sector_code_table"]
    if {row["series_identity"] for row in table} != set(mapping):
        raise MembershipError("SECTOR_TABLE_BINDING_COVERAGE_MISMATCH")
    for row in table:
        if mapping[row["series_identity"]] != row["membership_id"]:
            raise MembershipError("SECTOR_TABLE_BINDING_THEME_MISMATCH")
    return mapping


def read_idxcode(source: dict, contract: dict) -> tuple[dict[str, str], dict]:
    spec = contract["kis_source"]
    path = Path(source["path"])
    try:
        archive_bytes = path.read_bytes()
        with zipfile.ZipFile(path) as archive:
            if archive.namelist() != [spec["idxcode_archive_member"]]:
                raise MembershipError("IDXCODE_ARCHIVE_MEMBERS_MISMATCH")
            raw = archive.read(spec["idxcode_archive_member"])
    except (OSError, zipfile.BadZipFile) as exc:
        raise MembershipError(f"IDXCODE_ARCHIVE_INVALID:{type(exc).__name__}") from exc
    names: dict[str, str] = {}
    for index, line in enumerate(raw.splitlines(), start=1):
        if len(line) != spec["idxcode_line_byte_length"]:
            raise MembershipError(f"IDXCODE_LINE_LENGTH_INVALID:{index}")
        try:
            code = line[1:5].decode("ascii")
            name = line[5:].decode(spec["idxcode_encoding"]).strip()
        except (UnicodeDecodeError, LookupError) as exc:
            raise MembershipError(f"IDXCODE_LINE_DECODE_FAILED:{index}") from exc
        if code in names and names[code] != name:
            raise MembershipError(f"IDXCODE_DUPLICATE_CODE:{code}")
        names[code] = name
    return names, {
        "source": "KIS_IDXCODE_MASTER",
        "source_url": source["source_url"],
        "retrieved_at_utc": _utc(source["retrieved_at_utc"], "IDXCODE_RETRIEVED_AT_INVALID"),
        "http_last_modified": source.get("http_last_modified"),
        "archive_sha256": _sha_bytes(archive_bytes),
        "archive_byte_length": len(archive_bytes),
        "master_sha256": _sha_bytes(raw),
        "row_count": len(names),
    }


# ------------------------------------------------------------ classification
def classify(row: dict, market: str, contract: dict, idx_names: dict[str, str]) -> dict:
    """Deepest non-zero KIS level -> one ratified series, or an UNMAPPED reason."""
    by_code = {(r["market"], r["kis_sector_code"]): r for r in contract["sector_code_table"]}
    parents = {(p["market"], p["parent_kis_sector_code"]): p for p in contract["parent_view"]}
    large, medium, small = row["sector_large"], row["sector_medium"], row["sector_small"]
    base = {"kis_sector_code": None, "leaf_level": None, "membership_id": None,
            "series_identity": None, "parent_membership_id": None}
    if small not in ("", ZERO):
        return {**base, "status": UNMAPPED, "unmapped_reason": "SECTOR_SMALL_LEVEL_UNDECLARED"}
    has_large = large not in ("", ZERO)
    has_medium = medium not in ("", ZERO)
    if not has_large and not has_medium:
        return {**base, "status": UNMAPPED, "unmapped_reason": "NO_SECTOR_CODE"}
    parent = None
    if has_medium:
        leaf, level = medium, "sector_medium"
        parent = parents.get((market, large)) if has_large else None
        if parent is None or medium not in parent["child_kis_sector_codes"]:
            return {**base, "kis_sector_code": medium, "leaf_level": level,
                    "status": UNMAPPED, "unmapped_reason": "HIERARCHY_UNDECLARED"}
    else:
        leaf, level = large, "sector_large"
    entry = by_code.get((market, leaf))
    if entry is None:
        return {**base, "kis_sector_code": leaf, "leaf_level": level,
                "status": UNMAPPED, "unmapped_reason": "SECTOR_CODE_NOT_IN_TABLE"}
    if idx_names.get(leaf) != entry["source_name"]:
        return {**base, "kis_sector_code": leaf, "leaf_level": level,
                "status": UNMAPPED, "unmapped_reason": "SECTOR_CODE_NAME_CHANGED"}
    if parent is not None and idx_names.get(parent["parent_kis_sector_code"]) != by_code[(market, parent["parent_kis_sector_code"])]["source_name"]:
        return {**base, "kis_sector_code": leaf, "leaf_level": level,
                "status": UNMAPPED, "unmapped_reason": "SECTOR_CODE_NAME_CHANGED"}
    return {
        "kis_sector_code": leaf, "leaf_level": level, "membership_id": entry["membership_id"],
        "series_identity": entry["series_identity"],
        "parent_membership_id": parent["parent_membership_id"] if parent else None,
        "status": ACTIVE, "unmapped_reason": None,
    }


# -------------------------------------------------------------------- build
def _validate_input(value: dict) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != INPUT_SCHEMA_VERSION:
        raise MembershipError("INPUT_SCHEMA_MISMATCH")
    if set(value) != {"schema_version", "as_of_session", "masters", "idxcode", "previous_document_path"}:
        raise MembershipError("INPUT_FIELDS_INVALID")
    _session(value["as_of_session"], "INPUT_AS_OF_SESSION_INVALID")
    masters = value["masters"]
    if not isinstance(masters, dict) or set(masters) != set(MARKETS):
        raise MembershipError("INPUT_MASTERS_INVALID")
    for source in [*masters.values(), value["idxcode"]]:
        if not isinstance(source, dict) or not {"path", "source_url", "retrieved_at_utc"} <= set(source):
            raise MembershipError("INPUT_SOURCE_INVALID")
        _utc(source["retrieved_at_utc"], "INPUT_RETRIEVED_AT_INVALID")
    return value


def _row_id(row: dict) -> str:
    return payload_sha256({k: row[k] for k in (
        "asset_id", "kis_standard_code", "status", "unmapped_reason", "membership_id",
        "kis_sector_code", "effective_from",
    )})


def _new_row(stock: dict, target: dict, status: str, observed_at: str, session: str) -> dict:
    row = {
        "row_id": None,
        "asset_id": stock["asset_id"],
        "listing_id": stock["listing_id"],
        "kis_standard_code": stock["kis_standard_code"],
        "market": stock["market"],
        "status": status,
        "unmapped_reason": target["unmapped_reason"] if status == UNMAPPED else None,
        "membership_id": target["membership_id"] if status in (ACTIVE, PENDING_CHANGE) else None,
        "series_identity": target["series_identity"] if status in (ACTIVE, PENDING_CHANGE) else None,
        "kis_sector_code": target["kis_sector_code"],
        "leaf_level": target["leaf_level"],
        "parent_membership_id": target["parent_membership_id"] if status in (ACTIVE, PENDING_CHANGE) else None,
        "source_count": 1,
        "verification": VERIFICATION,
        "source_refs": stock["source_refs"],
        "effective_from": observed_at,
        "effective_from_session": session,
        "effective_to": None,
        "effective_to_session": None,
    }
    row["row_id"] = _row_id(row)
    return row


def _same_target(open_row: dict, target: dict) -> bool:
    if target["status"] == UNMAPPED:
        return open_row["status"] == UNMAPPED and open_row["unmapped_reason"] == target["unmapped_reason"]
    return open_row["status"] in (ACTIVE, PENDING_CHANGE) and open_row["membership_id"] == target["membership_id"]


def build_document(value: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    value = _validate_input(value)
    binding = load_binding(contract)
    registry_contract = REG.load_contract()
    session = value["as_of_session"]
    idx_names, idx_meta = read_idxcode(value["idxcode"], contract)

    stocks: dict[str, dict] = {}
    source_meta = [idx_meta]
    excluded = {market: {} for market in MARKETS}
    for market in MARKETS:
        rows, meta = REG._read_master(value["masters"][market], market, registry_contract)
        source_meta.append({"source": "KIS_STOCK_MASTER", **meta})
        for row in rows:
            product, _, _ = REG._product(row, registry_contract)
            if product != contract["kis_source"]["population_product_type"]:
                excluded[market][product] = excluded[market].get(product, 0) + 1
                continue
            asset_id = f"KR:XKRX:{row['short_code']}"
            if asset_id in stocks:
                raise MembershipError(f"DUPLICATE_ASSET_IN_MASTERS:{asset_id}")
            stocks[asset_id] = {
                "asset_id": asset_id,
                "listing_id": f"XKRX:{row['short_code']}",
                "kis_standard_code": row["standard_code"],
                "market": market,
                "target": classify(row, market, contract, idx_names),
                "source_refs": [
                    {"source": "KIS_STOCK_MASTER", "market": market, "master_sha256": meta["master_sha256"],
                     "row_sha256": row["row_sha256"], "sector_large": row["sector_large"],
                     "sector_medium": row["sector_medium"], "sector_small": row["sector_small"]},
                    {"source": "KIS_IDXCODE_MASTER", "master_sha256": idx_meta["master_sha256"]},
                ],
            }
    observed_at = max(meta["retrieved_at_utc"] for meta in source_meta)

    previous = None
    history: list[dict] = []
    if value["previous_document_path"] is not None:
        previous = read_document(Path(value["previous_document_path"]), contract)
        prev_pub = previous["publication"]
        if session <= prev_pub["as_of_session"] or observed_at <= prev_pub["observed_at_utc"]:
            raise MembershipError("PUBLICATION_NOT_AFTER_PREVIOUS")
        history = copy.deepcopy(previous["rows"])
    open_rows = {row["asset_id"]: row for row in history if row["effective_to"] is None}

    def close(row: dict) -> None:
        row["effective_to"] = observed_at
        row["effective_to_session"] = session

    for asset_id, open_row in open_rows.items():
        stock = stocks.get(asset_id)
        if stock is None or stock["kis_standard_code"] != open_row["kis_standard_code"]:
            close(open_row)  # delisted, left the common-stock population, or short code reused
    for asset_id in sorted(stocks):
        stock = stocks[asset_id]
        target = stock["target"]
        open_row = open_rows.get(asset_id)
        if open_row is not None and open_row["effective_to"] is not None:
            open_row = None  # closed above (code reuse): treat as a new security
        if open_row is None:
            status = target["status"]
        elif _same_target(open_row, target):
            if open_row["status"] == PENDING_CHANGE and session > open_row["effective_from_session"]:
                close(open_row)
                status = ACTIVE
            else:
                continue
        else:
            close(open_row)
            status = UNMAPPED if target["status"] == UNMAPPED else PENDING_CHANGE
        history.append(_new_row(stock, target, status, observed_at, session))

    history.sort(key=lambda r: (r["asset_id"], r["effective_from"], r["row_id"]))
    document = {
        "schema_version": DOCUMENT_SCHEMA_VERSION,
        "market_scope": "KR",
        "contract_payload_sha256": payload_sha256(contract),
        "taxonomy_binding_sha256": contract["taxonomy_binding"]["document_payload_sha256"],
        "source_contract_sha256": payload_sha256(registry_contract),
        "source_policy": {k: contract["source_policy"][k] for k in (
            "decision_id", "decision_sha256", "kr_sector_membership_source", "source_count",
            "verification", "satisfies_t2_c5", "satisfies_t3", "t3_target",
        )},
        "generator": {"path": GENERATOR_PATH, "file_sha256": _sha_bytes((ROOT / GENERATOR_PATH).read_bytes())},
        "publication": {
            "as_of_session": session,
            "observed_at_utc": observed_at,
            "sources": sorted(source_meta, key=lambda m: (m["source"], m.get("market", ""))),
            "excluded_non_common_stock": excluded,
        },
        "previous_document_sha256": previous["payload_sha256"] if previous else None,
        "binding_theme_count": len(binding),
        "parent_view": copy.deepcopy(contract["parent_view"]),
        "path_a_crosswalk": copy.deepcopy(contract["path_a_crosswalk"]),
        "counts": _counts(history, observed_at),
        "rows": history,
        "authority": copy.deepcopy(contract["authority"]),
    }
    document = _self_hashed(document)
    validate_document(document, contract)
    if previous is not None:
        validate_successor(previous, document)
    return document


def _open_at(rows: list[dict], t: str) -> list[dict]:
    return [r for r in rows if r["effective_from"] <= t and (r["effective_to"] is None or t < r["effective_to"])]


def _counts(rows: list[dict], t: str) -> dict:
    current = _open_at(rows, t)
    out = {}
    for market in MARKETS:
        mrows = [r for r in current if r["market"] == market]
        by_status = {s: sum(1 for r in mrows if r["status"] == s) for s in ROW_STATUSES}
        reasons = {}
        for r in mrows:
            if r["status"] == UNMAPPED:
                reasons[r["unmapped_reason"]] = reasons.get(r["unmapped_reason"], 0) + 1
        active_by_id = {}
        pending_by_id = {}
        for r in mrows:
            if r["status"] == ACTIVE:
                active_by_id[r["membership_id"]] = active_by_id.get(r["membership_id"], 0) + 1
            elif r["status"] == PENDING_CHANGE:
                pending_by_id[r["membership_id"]] = pending_by_id.get(r["membership_id"], 0) + 1
        out[market] = {
            "common_stocks": len(mrows),
            "by_status": by_status,
            "unmapped_by_reason": dict(sorted(reasons.items())),
            "active_by_membership_id": dict(sorted(active_by_id.items())),
            "pending_change_by_membership_id": dict(sorted(pending_by_id.items())),
        }
    return out


# --------------------------------------------------------------- validation
def validate_document(document: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    _check_self_hash(document, "DOCUMENT_SHA256_MISMATCH")
    if document.get("schema_version") != DOCUMENT_SCHEMA_VERSION or document.get("market_scope") != "KR":
        raise MembershipError("DOCUMENT_SCHEMA_INVALID")
    if document.get("contract_payload_sha256") != payload_sha256(contract):
        raise MembershipError("DOCUMENT_CONTRACT_MISMATCH")
    if document.get("taxonomy_binding_sha256") != contract["taxonomy_binding"]["document_payload_sha256"]:
        raise MembershipError("DOCUMENT_BINDING_MISMATCH")
    if document.get("authority") != contract["authority"]:
        raise MembershipError("DOCUMENT_AUTHORITY_INVALID")
    publication = document["publication"]
    observed_at = _utc(publication["observed_at_utc"], "DOCUMENT_OBSERVED_AT_INVALID")
    _session(publication["as_of_session"], "DOCUMENT_SESSION_INVALID")
    by_id = {(r["market"], r["membership_id"]) for r in contract["sector_code_table"]}
    parent_of = {}
    for p in contract["parent_view"]:
        for child in p["child_membership_ids"]:
            parent_of[child] = p["parent_membership_id"]
    per_asset: dict[str, list[dict]] = {}
    for row in document["rows"]:
        if not isinstance(row, dict) or set(row) != set(ROW_FIELDS):
            raise MembershipError("ROW_FIELDS_INVALID")
        if row["status"] not in ROW_STATUSES:
            raise MembershipError("ROW_STATUS_INVALID")
        if row["status"] == CONFLICT:
            raise MembershipError("ROW_CONFLICT_NOT_PRODUCED_UNDER_KIS_ONLY")
        if row["source_count"] != 1 or row["verification"] != VERIFICATION:
            raise MembershipError("ROW_VERIFICATION_INVALID")
        if row["row_id"] != _row_id(row):
            raise MembershipError("ROW_ID_MISMATCH")
        _utc(row["effective_from"], "ROW_EFFECTIVE_FROM_INVALID")
        if row["effective_from"] > observed_at:
            raise MembershipError("ROW_EFFECTIVE_FROM_AFTER_PUBLICATION")
        if row["effective_to"] is not None:
            _utc(row["effective_to"], "ROW_EFFECTIVE_TO_INVALID")
            if row["effective_to"] <= row["effective_from"] or row["effective_to"] > observed_at:
                raise MembershipError("ROW_INTERVAL_INVALID")
        if row["status"] == UNMAPPED:
            if row["unmapped_reason"] not in UNMAPPED_REASONS or row["membership_id"] is not None or row["parent_membership_id"] is not None:
                raise MembershipError("ROW_UNMAPPED_INVALID")
        else:
            if row["unmapped_reason"] is not None or (row["market"], row["membership_id"]) not in by_id:
                raise MembershipError("ROW_MEMBERSHIP_INVALID")
            expected_parent = parent_of.get(row["membership_id"]) if row["leaf_level"] == "sector_medium" else None
            if row["leaf_level"] not in ("sector_large", "sector_medium") or row["parent_membership_id"] != expected_parent:
                raise MembershipError("ROW_PARENT_VIEW_INVALID")
            if row["leaf_level"] == "sector_medium" and expected_parent is None:
                raise MembershipError("ROW_PARENT_VIEW_INVALID")
        per_asset.setdefault(row["asset_id"], []).append(row)
    for asset_id, rows in per_asset.items():
        rows = sorted(rows, key=lambda r: r["effective_from"])
        for earlier, later in zip(rows, rows[1:]):
            if earlier["effective_to"] is None or earlier["effective_to"] > later["effective_from"]:
                raise MembershipError(f"ROW_INTERVALS_OVERLAP:{asset_id}")
    if document["counts"] != _counts(document["rows"], observed_at):
        raise MembershipError("DOCUMENT_COUNTS_MISMATCH")
    return document


def validate_successor(previous: dict, document: dict) -> None:
    """No retroactive edits: earlier rows are kept verbatim except closing an open row."""
    observed_at = document["publication"]["observed_at_utc"]
    if document["previous_document_sha256"] != previous["payload_sha256"]:
        raise MembershipError("SUCCESSOR_PREVIOUS_SHA_MISMATCH")
    if observed_at <= previous["publication"]["observed_at_utc"]:
        raise MembershipError("SUCCESSOR_NOT_AFTER_PREVIOUS")
    new_rows = {row["row_id"]: row for row in document["rows"]}
    for old in previous["rows"]:
        new = new_rows.get(old["row_id"])
        if new is None:
            raise MembershipError("SUCCESSOR_DROPPED_ROW")
        if old["effective_to"] is None and new["effective_to"] is not None:
            if new["effective_to"] != observed_at or new["effective_to_session"] != document["publication"]["as_of_session"]:
                raise MembershipError("SUCCESSOR_RETROACTIVE_CLOSE")
            old_cmp = {k: v for k, v in old.items() if k not in ("effective_to", "effective_to_session")}
            new_cmp = {k: v for k, v in new.items() if k not in ("effective_to", "effective_to_session")}
            if old_cmp != new_cmp:
                raise MembershipError("SUCCESSOR_ROW_REWRITTEN")
        elif old != new:
            raise MembershipError("SUCCESSOR_ROW_REWRITTEN")
    old_ids = {row["row_id"] for row in previous["rows"]}
    for row in document["rows"]:
        if row["row_id"] not in old_ids and row["effective_from"] != observed_at:
            raise MembershipError("SUCCESSOR_NEW_ROW_BACKDATED")


def read_document(path: Path, contract: dict | None = None) -> dict:
    return validate_document(_read_json(path), load_contract() if contract is None else contract)


# ------------------------------------------------------------------- reader
def members(document: dict, membership_id: str, t: str) -> list[str]:
    _utc(t, "QUERY_TIME_INVALID")
    return sorted(r["asset_id"] for r in _open_at(document["rows"], t)
                  if r["status"] == ACTIVE and r["membership_id"] == membership_id)


def c5_rotation_membership(document: dict, asset_id: str, selected_membership_ids, t: str) -> dict:
    """T2 condition C5 (rotation membership) for one asset at time t."""
    _utc(t, "QUERY_TIME_INVALID")
    selected = set(selected_membership_ids)
    base = {"asset_id": asset_id, "evaluated_at": t, "verification": VERIFICATION,
            "source_count": 1, "t3_two_source_verified": False, "via_parent": False,
            "membership_id": None, "row_id": None}
    rows = [r for r in _open_at(document["rows"], t) if r["asset_id"] == asset_id]
    if len(rows) > 1:
        raise MembershipError(f"ROW_INTERVALS_OVERLAP:{asset_id}")
    if not rows:
        return {**base, "result": "FAIL", "reason": "MEMBERSHIP_NOT_OBSERVED"}
    row = rows[0]
    base = {**base, "membership_id": row["membership_id"], "row_id": row["row_id"]}
    if row["status"] != ACTIVE:
        return {**base, "result": "FAIL", "reason": f"MEMBERSHIP_{row['status']}"}
    if row["membership_id"] in selected:
        return {**base, "result": "PASS", "reason": "ACTIVE_SERIES_SELECTED"}
    if row["parent_membership_id"] is not None and row["parent_membership_id"] in selected:
        return {**base, "result": "PASS", "reason": "PARENT_SERIES_SELECTED", "via_parent": True}
    return {**base, "result": "FAIL", "reason": "MEMBERSHIP_NOT_IN_ROTATION_SELECTION"}


def build_public_summary(document: dict, contract: dict | None = None) -> dict:
    contract = load_contract() if contract is None else contract
    validate_document(document, contract)
    counts = document["counts"]
    coverage = {
        market: {
            "common_stocks": counts[market]["common_stocks"],
            "active": counts[market]["by_status"][ACTIVE],
            "pending_change": counts[market]["by_status"][PENDING_CHANGE],
            "unmapped": counts[market]["by_status"][UNMAPPED],
        } for market in MARKETS
    }
    summary = {
        "schema_version": PUBLIC_SUMMARY_SCHEMA_VERSION,
        "market_scope": "KR",
        "publication": {k: document["publication"][k] for k in ("as_of_session", "observed_at_utc")},
        "source_shas": [
            {k: m[k] for k in ("source", "archive_sha256", "master_sha256", "row_count") if k in m}
            | ({"market": m["market"]} if "market" in m else {})
            for m in document["publication"]["sources"]
        ],
        "private_document_payload_sha256": document["payload_sha256"],
        "contract_payload_sha256": document["contract_payload_sha256"],
        "taxonomy_binding_sha256": document["taxonomy_binding_sha256"],
        "generator": document["generator"],
        "source_policy": document["source_policy"],
        "coverage": {
            **coverage,
            "total": {
                "common_stocks": sum(c["common_stocks"] for c in coverage.values()),
                "active": sum(c["active"] for c in coverage.values()),
                "pending_change": sum(c["pending_change"] for c in coverage.values()),
                "unmapped": sum(c["unmapped"] for c in coverage.values()),
            },
        },
        "unmapped_by_reason": {m: counts[m]["unmapped_by_reason"] for m in MARKETS},
        "active_by_membership_id": {m: counts[m]["active_by_membership_id"] for m in MARKETS},
        "pending_change_by_membership_id": {m: counts[m]["pending_change_by_membership_id"] for m in MARKETS},
        "authority": copy.deepcopy(contract["authority"]),
    }
    return _self_hashed(summary)


def write_json_atomic(path: Path, value: dict) -> None:
    REG.write_json_atomic(path, value)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output-document", required=True, type=Path)
    parser.add_argument("--output-summary", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        document = build_document(_read_json(args.input))
        summary = build_public_summary(document)
    except (MembershipError, REG.RegistryError) as exc:
        print(f"security_sector_membership: {exc}", file=sys.stderr)
        return 2
    write_json_atomic(args.output_document, document)
    write_json_atomic(args.output_summary, summary)
    print(json.dumps(summary["coverage"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
