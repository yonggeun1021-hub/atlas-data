#!/usr/bin/env python3
"""Audit P2-01 source facts without inventing Theme or value-chain policy.

This is a population boundary for the existing ``theme_taxonomy/2`` engine,
not a second taxonomy.  It pins canonical repository sources byte-for-byte,
checks their git provenance and identity collisions, and reports exactly what
they prove.  Sector/chain/Theme memberships remain absent until the independent
authority registry authorizes an exact graph.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
import subprocess

from rotation import theme_taxonomy_authority as TTA


ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = ROOT / "config" / "theme_taxonomy_source_fact_registry.json"
SCHEMA_VERSION = "theme_taxonomy_source_fact_registry/1"
PACKET_VERSION = "theme_taxonomy_source_fact_population/1"
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
MARKETS = ("KOREA", "US", "CRYPTO")
FALSE_AUTHORITY = {
    "theme_membership_authorized": False,
    "sector_chain_membership_authorized": False,
    "rotation_score_authorized": False,
    "candidate_ranking_authorized": False,
    "stage_promotion_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
    "order_authorized": False,
    "real_capital_authorized": False,
}


class ThemeTaxonomyPopulationError(ValueError):
    """Fail-closed source population contract violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def payload_sha256(value) -> str:
    return sha256_bytes(canonical_json(value).encode("utf-8"))


def _git(root: Path, *args: str, binary: bool = False):
    proc = subprocess.run(
        ["git", "-C", str(root), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
        text=not binary,
    )
    if proc.returncode:
        return None
    return proc.stdout if binary else proc.stdout.strip()


def _read_pinned(root: Path, pin: dict, trusted_commit: str) -> tuple[dict, bytes]:
    path_value = pin.get("path")
    if not isinstance(path_value, str) or not path_value or Path(path_value).is_absolute():
        raise ThemeTaxonomyPopulationError("PIN_PATH_INVALID")
    path = (root / path_value).resolve()
    try:
        path.relative_to(root.resolve())
        raw = path.read_bytes()
    except (ValueError, OSError) as exc:
        raise ThemeTaxonomyPopulationError(f"PIN_READ_FAILED:{path_value}") from exc
    if not isinstance(pin.get("sha256"), str) or SHA_RE.fullmatch(pin["sha256"]) is None:
        raise ThemeTaxonomyPopulationError(f"PIN_SHA_INVALID:{path_value}")
    if sha256_bytes(raw) != pin["sha256"]:
        raise ThemeTaxonomyPopulationError(f"PIN_BYTES_MISMATCH:{path_value}")
    blob = _git(root, "show", f"{trusted_commit}:{path_value}", binary=True)
    if blob != raw:
        raise ThemeTaxonomyPopulationError(f"PIN_GIT_BYTES_MISMATCH:{path_value}")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ThemeTaxonomyPopulationError(f"PIN_JSON_INVALID:{path_value}") from exc
    return value, raw


def _validate_registry(value: dict) -> dict:
    fields = {"schema_version", "theme_contract", "authority_registry", "sources", "consumers", "authority"}
    if not isinstance(value, dict) or set(value) != fields or value.get("schema_version") != SCHEMA_VERSION:
        raise ThemeTaxonomyPopulationError("REGISTRY_SCHEMA_MISMATCH")
    if not isinstance(value.get("sources"), list) or not isinstance(value.get("consumers"), list):
        raise ThemeTaxonomyPopulationError("REGISTRY_LIST_INVALID")
    source_fields = {
        "source_id", "market", "path", "sha256", "first_seen_commit",
        "policy_version", "approval_status", "identity_field",
        "classification_field", "record_count", "fact_scope",
        "theme_membership_authorized",
    }
    for source in value["sources"]:
        if not isinstance(source, dict) or set(source) != source_fields:
            raise ThemeTaxonomyPopulationError("SOURCE_PIN_FIELDS_MISMATCH")
        if source["market"] not in MARKETS or source["approval_status"] not in {"RATIFIED", "UNRATIFIED"}:
            raise ThemeTaxonomyPopulationError("SOURCE_PIN_VALUE_INVALID")
        if COMMIT_RE.fullmatch(str(source["first_seen_commit"])) is None:
            raise ThemeTaxonomyPopulationError("SOURCE_FIRST_SEEN_COMMIT_INVALID")
        if type(source["record_count"]) is not int or source["record_count"] < 0:
            raise ThemeTaxonomyPopulationError("SOURCE_RECORD_COUNT_INVALID")
        if source["theme_membership_authorized"] is not False:
            raise ThemeTaxonomyPopulationError("SOURCE_THEME_AUTHORITY_FORBIDDEN")
    ids = [source["source_id"] for source in value["sources"]]
    paths = [source["path"] for source in value["sources"]]
    if len(ids) != len(set(ids)) or len(paths) != len(set(paths)):
        raise ThemeTaxonomyPopulationError("SOURCE_PIN_DUPLICATE")
    consumer_fields = {"market", "path", "sha256", "contract_version", "taxonomy_contract_version"}
    for consumer in value["consumers"]:
        if not isinstance(consumer, dict) or set(consumer) != consumer_fields or consumer["market"] not in MARKETS:
            raise ThemeTaxonomyPopulationError("CONSUMER_PIN_FIELDS_MISMATCH")
    if sorted(consumer["market"] for consumer in value["consumers"]) != sorted(MARKETS):
        raise ThemeTaxonomyPopulationError("CONSUMER_MARKET_COVERAGE_INVALID")
    expected_authority = {"source_fact_audit_authorized": True, **FALSE_AUTHORITY}
    if value.get("authority") != expected_authority:
        raise ThemeTaxonomyPopulationError("REGISTRY_AUTHORITY_MISMATCH")
    return copy.deepcopy(value)


def _date(value, code: str) -> dt.date:
    try:
        parsed = dt.date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ThemeTaxonomyPopulationError(code) from exc
    if parsed.isoformat() != value:
        raise ThemeTaxonomyPopulationError(code)
    return parsed


def _active(record: dict, day: dt.date, source_id: str) -> bool:
    start = _date(record.get("effective_from"), f"SOURCE_EFFECTIVE_FROM_INVALID:{source_id}")
    end_value = record.get("effective_to")
    end = None if end_value is None else _date(end_value, f"SOURCE_EFFECTIVE_TO_INVALID:{source_id}")
    if end is not None and end <= start:
        raise ThemeTaxonomyPopulationError(f"SOURCE_EFFECTIVE_INTERVAL_INVALID:{source_id}")
    return start <= day and (end is None or day < end)


def _source_facts(root: Path, pin: dict, trusted_commit: str, day: dt.date) -> tuple[dict, dict[str, str]]:
    source, _ = _read_pinned(root, pin, trusted_commit)
    if source.get("policy_version") != pin["policy_version"] or source.get("approval_status") != pin["approval_status"]:
        raise ThemeTaxonomyPopulationError(f"SOURCE_POLICY_PIN_MISMATCH:{pin['source_id']}")
    records = source.get("records")
    if records is None and pin["approval_status"] == "UNRATIFIED" and pin["record_count"] == 0:
        records = []
    if not isinstance(records, list) or len(records) != pin["record_count"]:
        raise ThemeTaxonomyPopulationError(f"SOURCE_RECORD_COUNT_MISMATCH:{pin['source_id']}")
    first = pin["first_seen_commit"]
    if _git(root, "rev-parse", "--verify", f"{first}^{{commit}}") != first:
        raise ThemeTaxonomyPopulationError(f"SOURCE_FIRST_SEEN_UNVERIFIED:{pin['source_id']}")
    if _git(root, "merge-base", "--is-ancestor", first, trusted_commit) is None:
        raise ThemeTaxonomyPopulationError(f"SOURCE_FIRST_SEEN_NOT_ANCESTOR:{pin['source_id']}")
    first_blob = _git(root, "show", f"{first}:{pin['path']}", binary=True)
    if first_blob is None or sha256_bytes(first_blob) != pin["sha256"]:
        raise ThemeTaxonomyPopulationError(f"SOURCE_FIRST_SEEN_BYTES_MISMATCH:{pin['source_id']}")

    seen: dict[str, list[tuple[dt.date, dt.date | None, str]]] = {}
    active: dict[str, str] = {}
    category_counts: dict[str, int] = {}
    for record in records:
        identity = record.get(pin["identity_field"]) if isinstance(record, dict) else None
        category = record.get(pin["classification_field"]) if isinstance(record, dict) else None
        if not isinstance(identity, str) or not identity or not isinstance(category, str) or not category:
            raise ThemeTaxonomyPopulationError(f"SOURCE_IDENTITY_INVALID:{pin['source_id']}")
        start = _date(record.get("effective_from"), f"SOURCE_EFFECTIVE_FROM_INVALID:{pin['source_id']}")
        end_value = record.get("effective_to")
        end = None if end_value is None else _date(end_value, f"SOURCE_EFFECTIVE_TO_INVALID:{pin['source_id']}")
        if end is not None and end <= start:
            raise ThemeTaxonomyPopulationError(f"SOURCE_EFFECTIVE_INTERVAL_INVALID:{pin['source_id']}")
        for prior_start, prior_end, prior_category in seen.setdefault(identity, []):
            if start < (prior_end or dt.date.max) and prior_start < (end or dt.date.max):
                code = "SOURCE_IDENTITY_DUPLICATE" if category == prior_category else "SOURCE_IDENTITY_COLLISION"
                raise ThemeTaxonomyPopulationError(f"{code}:{pin['source_id']}:{identity}")
        seen[identity].append((start, end, category))
        if _active(record, day, pin["source_id"]):
            if identity in active:
                raise ThemeTaxonomyPopulationError(f"SOURCE_ACTIVE_IDENTITY_DUPLICATE:{pin['source_id']}:{identity}")
            active[identity] = category
            category_counts[category] = category_counts.get(category, 0) + 1
    return ({
        "source_id": pin["source_id"],
        "market": pin["market"],
        "path": pin["path"],
        "sha256": pin["sha256"],
        "first_seen_commit": first,
        "policy_version": pin["policy_version"],
        "approval_status": pin["approval_status"],
        "fact_scope": pin["fact_scope"],
        "record_count": len(records),
        "active_record_count": len(active),
        "active_classification_counts": dict(sorted(category_counts.items())),
        "theme_membership_authorized": False,
    }, active)


def build_population(
    as_of_date: str,
    registry_path: Path = REGISTRY_PATH,
    trusted_commit: str | None = None,
) -> dict:
    day = _date(as_of_date, "AS_OF_DATE_INVALID")
    registry_path = Path(registry_path).resolve()
    root_value = _git(registry_path.parent, "rev-parse", "--show-toplevel")
    if not root_value:
        raise ThemeTaxonomyPopulationError("REGISTRY_GIT_ROOT_UNVERIFIED")
    root = Path(root_value).resolve()
    try:
        registry_rel = registry_path.relative_to(root).as_posix()
        registry_raw = registry_path.read_bytes()
        registry = _validate_registry(json.loads(registry_raw.decode("utf-8")))
    except (ValueError, OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        if isinstance(exc, ThemeTaxonomyPopulationError):
            raise
        raise ThemeTaxonomyPopulationError("REGISTRY_READ_FAILED") from exc
    commit = trusted_commit or _git(root, "rev-parse", "HEAD")
    if not isinstance(commit, str) or COMMIT_RE.fullmatch(commit) is None or _git(root, "rev-parse", "--verify", f"{commit}^{{commit}}") != commit:
        raise ThemeTaxonomyPopulationError("TRUSTED_COMMIT_INVALID")
    if _git(root, "show", f"{commit}:{registry_rel}", binary=True) != registry_raw:
        raise ThemeTaxonomyPopulationError("REGISTRY_GIT_BYTES_MISMATCH")

    theme_contract, _ = _read_pinned(root, registry["theme_contract"], commit)
    if theme_contract.get("contract_version") != registry["theme_contract"]["contract_version"]:
        raise ThemeTaxonomyPopulationError("THEME_CONTRACT_VERSION_MISMATCH")
    authority_registry, _ = _read_pinned(root, registry["authority_registry"], commit)
    if authority_registry.get("schema_version") != registry["authority_registry"]["schema_version"]:
        raise ThemeTaxonomyPopulationError("AUTHORITY_REGISTRY_SCHEMA_MISMATCH")
    # Reuse the existing authority registry validator; no parallel authority semantics.
    validated_authority = TTA.load_registry(root / registry["authority_registry"]["path"])

    source_packets = []
    active_by_source: dict[str, dict[str, str]] = {}
    for pin in registry["sources"]:
        packet, active = _source_facts(root, pin, commit, day)
        source_packets.append(packet)
        active_by_source[pin["source_id"]] = active

    crypto_sources = [pin["source_id"] for pin in registry["sources"] if pin["market"] == "CRYPTO"]
    crypto_seen: dict[str, tuple[str, str]] = {}
    consistent_overlap_count = 0
    for source_id in crypto_sources:
        for identity, category in active_by_source[source_id].items():
            prior = crypto_seen.get(identity)
            if prior is not None:
                if prior[1] != category:
                    raise ThemeTaxonomyPopulationError(f"CROSS_SOURCE_IDENTITY_COLLISION:{identity}:{prior[0]}:{source_id}")
                consistent_overlap_count += 1
            else:
                crypto_seen[identity] = (source_id, category)

    consumer_packets = []
    for pin in registry["consumers"]:
        consumer, _ = _read_pinned(root, pin, commit)
        if consumer.get("contract_version") != pin["contract_version"]:
            raise ThemeTaxonomyPopulationError(f"CONSUMER_CONTRACT_VERSION_MISMATCH:{pin['market']}")
        actual_taxonomy = consumer.get("taxonomy_contract_version")
        if actual_taxonomy != pin["taxonomy_contract_version"]:
            raise ThemeTaxonomyPopulationError(f"CONSUMER_TAXONOMY_PIN_MISMATCH:{pin['market']}")
        consumer_packets.append({
            "market": pin["market"],
            "path": pin["path"],
            "sha256": pin["sha256"],
            "contract_version": pin["contract_version"],
            "taxonomy_contract_version": actual_taxonomy,
            "authority_compatible": actual_taxonomy == theme_contract["contract_version"],
        })

    market_population = {}
    for market in MARKETS:
        market_sources = [item for item in source_packets if item["market"] == market]
        active_count = sum(item["active_record_count"] for item in market_sources)
        ratified_count = sum(item["active_record_count"] for item in market_sources if item["approval_status"] == "RATIFIED")
        market_population[market] = {
            "source_count": len(market_sources),
            "active_source_record_count": active_count,
            "ratified_source_fact_count": ratified_count,
            "theme_or_sector_chain_membership_count": 0,
            "status": (
                "SOURCE_FACTS_RATIFIED_THEME_POLICY_UNRESOLVED"
                if ratified_count else "SOURCE_FACTS_UNRATIFIED_OR_EMPTY"
            ),
        }
    market_population["CRYPTO"]["unique_active_identity_count"] = len(crypto_seen)
    market_population["CRYPTO"]["consistent_cross_source_overlap_count"] = consistent_overlap_count

    packet = {
        "schema_version": PACKET_VERSION,
        "as_of_date": as_of_date,
        "trusted_commit": commit,
        "theme_contract_pin": registry["theme_contract"],
        "authority_registry_pin": registry["authority_registry"],
        "authority_registry_record_count": len(validated_authority["records"]),
        "ratified_graph_authority_record_count": sum(
            row["approval_status"] == "RATIFIED" for row in validated_authority["records"]
        ),
        "sources": sorted(source_packets, key=lambda item: item["source_id"]),
        "market_population": market_population,
        "consumer_contract_pins": sorted(consumer_packets, key=lambda item: item["market"]),
        "graph_population_status": "BLOCKED_CIO_THEME_VALUE_CHAIN_POLICY",
        "authority": {"source_fact_audit_authorized": True, **FALSE_AUTHORITY},
        "unresolved_records": [
            "KOREA_SECURITY_TO_THEME_MEMBERSHIP",
            "US_LEADERSHIP_AND_SECURITY_TO_THEME_MEMBERSHIP",
            "CRYPTO_SECTOR_CHAIN_MEMBERSHIP",
            "CROSS_MARKET_VALUE_CHAIN_EDGES",
            "MARKET_CONSUMER_THEME_TAXONOMY_V2_PINS",
            "INDEPENDENT_RATIFIED_GRAPH_AUTHORITY_RECORD",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("as_of_date")
    parser.add_argument("--registry", type=Path, default=REGISTRY_PATH)
    parser.add_argument("--trusted-commit")
    args = parser.parse_args()
    print(json.dumps(build_population(args.as_of_date, args.registry, args.trusted_commit), ensure_ascii=False, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
