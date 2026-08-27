#!/usr/bin/env python3
"""Persist the already-built P3-03 KRX source-coverage packet.

The P1-KR-05 live-proof job already performs the only KRX requests and builds
``p3-03-krx-global-universe.json`` with the production adapter.  This module
does not call a provider or rebuild from a current catalogue.  It validates
that exact artifact, including its nested Global Asset Master, and writes it
append-only under its own observation date.

This is source coverage only.  It does not approve an investable universe,
liquidity, tradability, listing/delisting, Theme, Stage, Production, or trade.
"""
from __future__ import annotations

import argparse
import copy
from datetime import date, timedelta
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile


ROOT = Path(__file__).resolve().parents[2]
SOURCE_NAME = "p3-03-krx-global-universe.json"


class PopulationError(ValueError):
    """Fail-closed Korea Global Master population violation."""


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    if spec is None or spec.loader is None:
        raise PopulationError(f"MODULE_LOAD_FAILED:{relative}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KRU = _load("krx_global_universe_for_population", "universe/krx_global_universe.py")
GAM = _load("global_asset_master_for_krx_population", "universe/global_asset_master.py")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _forbidden_raw_fields(value: object) -> None:
    forbidden = {
        "response_body_base64",
        "TDD_CLSPRC",
        "TDD_OPNPRC",
        "TDD_HGPRC",
        "TDD_LWPRC",
        "ACC_TRDVOL",
        "ACC_TRDVAL",
        "MKTCAP",
        "LIST_SHRS",
    }
    if isinstance(value, dict):
        overlap = forbidden.intersection(value)
        if overlap:
            raise PopulationError(f"RAW_OR_PRICE_FIELD_PRESENT:{sorted(overlap)[0]}")
        for item in value.values():
            _forbidden_raw_fields(item)
    elif isinstance(value, list):
        for item in value:
            _forbidden_raw_fields(item)


def validate_packet(packet: dict) -> dict:
    fields = {
        "schema_version",
        "contract_version",
        "as_of_date",
        "status",
        "membership_semantics",
        "effective_interval",
        "market_counts",
        "total_count",
        "source_snapshots",
        "asset_master",
        "policy_status",
        "authority",
        "unresolved_boundaries",
        "payload_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != fields:
        raise PopulationError("PACKET_FIELDS_MISMATCH")
    digest = packet.get("payload_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    if not isinstance(digest, str) or digest != KRU.payload_sha256(unsigned):
        raise PopulationError("PACKET_SHA256_MISMATCH")

    contract = KRU.load_contract()
    if packet.get("schema_version") != KRU.OUTPUT_SCHEMA_VERSION:
        raise PopulationError("PACKET_SCHEMA_MISMATCH")
    if packet.get("contract_version") != contract["contract_version"]:
        raise PopulationError("PACKET_CONTRACT_MISMATCH")
    if packet.get("status") != "SOURCE_COVERAGE_UNIVERSE_VALIDATED":
        raise PopulationError("PACKET_STATUS_MISMATCH")
    if packet.get("membership_semantics") != contract["membership_semantics"]:
        raise PopulationError("MEMBERSHIP_SEMANTICS_MISMATCH")
    if packet.get("policy_status") != contract["policy_status"]:
        raise PopulationError("POLICY_STATUS_MISMATCH")
    if packet.get("authority") != contract["authority"]:
        raise PopulationError("AUTHORITY_MISMATCH")
    try:
        as_of = date.fromisoformat(packet["as_of_date"])
    except (KeyError, TypeError, ValueError) as exc:
        raise PopulationError("AS_OF_DATE_INVALID") from exc
    if packet.get("effective_interval") != {
        "valid_from": as_of.isoformat(),
        "valid_to": (as_of + timedelta(days=1)).isoformat(),
    }:
        raise PopulationError("EFFECTIVE_INTERVAL_MISMATCH")

    try:
        master = GAM.validate_packet(packet["asset_master"])
    except GAM.AssetMasterError as exc:
        raise PopulationError(f"ASSET_MASTER_INVALID:{exc}") from exc
    if master["as_of_date"] != packet.get("as_of_date"):
        raise PopulationError("AS_OF_DATE_MISMATCH")
    if master["record_count"] != packet.get("total_count"):
        raise PopulationError("TOTAL_COUNT_MISMATCH")

    required_markets = set(contract["required_markets"])
    counts = packet.get("market_counts")
    if not isinstance(counts, dict) or set(counts) != required_markets:
        raise PopulationError("MARKET_COUNTS_MISMATCH")
    source_rows = packet.get("source_snapshots")
    if not isinstance(source_rows, list) or {
        row.get("market") for row in source_rows if isinstance(row, dict)
    } != required_markets:
        raise PopulationError("SOURCE_SNAPSHOTS_MISMATCH")
    derived_counts = {market: 0 for market in required_markets}
    derived_sources: dict[str, dict] = {}
    for record in master["records"]:
        memberships = [
            item
            for item in record["active_memberships"]
            if item["membership_type"] == "UNIVERSE"
        ]
        if len(memberships) != 1 or memberships[0]["membership_id"] not in required_markets:
            raise PopulationError("RECORD_MARKET_MEMBERSHIP_INVALID")
        derived_counts[memberships[0]["membership_id"]] += 1
        market = memberships[0]["membership_id"]
        source_identity = record["source_identity"]
        source_summary = {
            "source_sha256": source_identity["source_sha256"],
            "available_at": source_identity["available_at"],
            "retrieved_at_utc": source_identity["retrieved_at_utc"],
        }
        prior = derived_sources.setdefault(market, source_summary)
        if prior != source_summary:
            raise PopulationError("MARKET_SOURCE_IDENTITY_INCONSISTENT")
        if record["universe_approved"] is not False or record["investable_eligible"] is not False:
            raise PopulationError("RECORD_AUTHORITY_INVALID")
    if counts != {market: derived_counts[market] for market in sorted(derived_counts)}:
        raise PopulationError("MARKET_COUNTS_REDERIVATION_MISMATCH")
    for source in source_rows:
        if set(source) != {
            "market", "source_sha256", "available_at", "retrieved_at_utc", "universe_count"
        }:
            raise PopulationError("SOURCE_SNAPSHOT_FIELDS_MISMATCH")
        if source.get("universe_count") != counts[source["market"]]:
            raise PopulationError("SOURCE_COUNT_MISMATCH")
        if {
            key: source[key]
            for key in ("source_sha256", "available_at", "retrieved_at_utc")
        } != derived_sources[source["market"]]:
            raise PopulationError("SOURCE_LINEAGE_REDERIVATION_MISMATCH")
    if packet["total_count"] != sum(counts.values()):
        raise PopulationError("TOTAL_COUNT_REDERIVATION_MISMATCH")
    _forbidden_raw_fields(packet)
    return copy.deepcopy(packet)


def load_candidate(derived_dir: Path) -> dict:
    path = Path(derived_dir) / SOURCE_NAME
    if not path.is_file():
        raise PopulationError("SOURCE_PACKET_MISSING")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PopulationError(f"SOURCE_PACKET_READ_FAILED:{type(exc).__name__}") from exc
    return validate_packet(value)


def output_path(as_of_date: str) -> Path:
    return ROOT / "data" / "observations" / "krx_global_universe" / as_of_date / "packet.json"


def _write_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
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


def populate(derived_dir: Path) -> dict:
    packet = load_candidate(derived_dir)
    target = output_path(packet["as_of_date"])
    if target.exists():
        try:
            existing = validate_packet(json.loads(target.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError, PopulationError) as exc:
            raise PopulationError(f"EXISTING_PACKET_INVALID:{exc}") from exc
        if existing == packet:
            return {"outcome": "verified_existing", "path": str(target), "payload_sha256": packet["payload_sha256"]}
        raise PopulationError("EXISTING_PACKET_DRIFT_OR_TAMPER")
    _write_atomic(target, packet)
    return {"outcome": "populated", "path": str(target), "payload_sha256": packet["payload_sha256"]}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived-dir", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        result = populate(args.derived_dir)
    except PopulationError as exc:
        print(f"Korea Global Master population failed reason={exc}")
        return 1
    print(
        "Korea Global Master population "
        f"outcome={result['outcome']} path={result['path']} sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
