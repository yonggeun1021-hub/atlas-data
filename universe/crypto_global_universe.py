#!/usr/bin/env python3
"""P3-04 Kraken breadth source-coverage → Global Asset Master adapter.

The adapter reuses the ratified P1-CR-06 Top-N selection exactly as scoped:
breadth source coverage, not investability. It reads one validated append-only
snapshot, requires full selected-member observation coverage, preserves every
policy and exact-source lineage component, and keeps all portfolio authority
closed.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universe import global_asset_master as GAM  # noqa: E402


_BREADTH_SPEC = importlib.util.spec_from_file_location(
    "crypto_breadth_for_global_universe",
    ROOT / ".github" / "scripts" / "crypto_breadth.py",
)
CRYPTO_BREADTH = importlib.util.module_from_spec(_BREADTH_SPEC)
assert _BREADTH_SPEC.loader is not None
_BREADTH_SPEC.loader.exec_module(CRYPTO_BREADTH)


CONTRACT_PATH = ROOT / "config" / "crypto_global_universe_contract.json"
UNIVERSE_POLICY_PATH = ROOT / "config" / "crypto_breadth_universe_policy.json"
TAXONOMY_PATH = ROOT / "config" / "crypto_breadth_exclusion_taxonomy.json"
IDENTITY_PATH = ROOT / "config" / "crypto_asset_identity_exceptions.json"
OUTPUT_SCHEMA_VERSION = "crypto_global_universe_packet/1"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class CryptoUniverseError(ValueError):
    """Fail-closed Crypto source-coverage Master violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoUniverseError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _validate_contract(value: dict) -> dict:
    expected_policy = {
        "breadth_source_coverage_selection": (
            "RATIFIED_REUSED_WITHOUT_AUTHORITY_EXPANSION"
        ),
        "exchange_coverage": "KRAKEN_ONLY",
        "listing_policy": "UNRATIFIED",
        "delisting_policy": "UNRATIFIED",
        "rename_policy": "EXPLICIT_EFFECTIVE_DATED_EXCEPTION_ONLY",
        "liquidity_for_investability": "UNRATIFIED",
        "tradability_policy": "UNRATIFIED",
        "custody_policy": "UNRATIFIED",
        "investable_universe_policy": "UNRATIFIED",
    }
    expected_authority = {
        "breadth_source_coverage_universe_only": True,
        "breadth_rank_as_investability_authorized": False,
        "liquidity_filter_authorized": False,
        "tradability_filter_authorized": False,
        "custody_filter_authorized": False,
        "investable_universe_authorized": False,
        "current_catalog_backfill_authorized": False,
        "stage_promotion_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    expected = {
        "schema_version": 1,
        "contract_version": "crypto_global_universe_adapter/1",
        "output_schema_version": OUTPUT_SCHEMA_VERSION,
        "asset_master_contract_version": "global_asset_master/1",
        "source_contract": {
            "path": "config/crypto_breadth_contract.json",
            "schema_version": 1,
            "source_name": "kraken_spot_market_data",
            "historical_universe_policy": (
                "as_captured_append_only_no_current_state_backfill"
            ),
        },
        "breadth_universe_policy": {
            "path": "config/crypto_breadth_universe_policy.json",
            "approval_status": "RATIFIED",
            "universe_kind": "breadth_source_coverage_not_investable",
        },
        "taxonomy_policy": {
            "path": "config/crypto_breadth_exclusion_taxonomy.json",
            "approval_status": "RATIFIED",
            "unknown_asset_policy": "fail_closed_unknown",
        },
        "identity_policy_path": "config/crypto_asset_identity_exceptions.json",
        "source_id": "kraken_public_api",
        "market": "CRYPTO",
        "asset_class": "CRYPTO_ASSET",
        "exchange_id": "KRAKEN",
        "quote_currency": "USD",
        "membership_id": "KRAKEN_BREADTH_SOURCE_COVERAGE",
        "membership_semantics": (
            "ratified_breadth_selection_source_coverage_not_investable"
        ),
        "effective_interval": "[breadth_as_of_date, next_calendar_date)",
        "coverage_requirement": "selection_observed_and_full_target_coverage",
        "source_identity_semantics": (
            "exact_asset_pairs_response_plus_validated_manifest_and_component_lineage"
        ),
        "policy_status": expected_policy,
        "authority": expected_authority,
    }
    if not isinstance(value, dict):
        raise CryptoUniverseError("CONTRACT_NOT_OBJECT")
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise CryptoUniverseError(f"CONTRACT_FIELD_MISMATCH:{key}")
    if set(value) != set(expected):
        raise CryptoUniverseError("CONTRACT_FIELDS_MISMATCH")
    return copy.deepcopy(value)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return _validate_contract(_read_json(Path(path)))


def _file_sha(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoUniverseError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def _next_date(value: str) -> str:
    return (dt.date.fromisoformat(value) + dt.timedelta(days=1)).isoformat()


def _validate_dependencies(
    contract: dict,
    source_contract: dict,
    universe_policy: dict,
    taxonomy: dict,
) -> None:
    expected_source = contract["source_contract"]
    if (
        source_contract.get("schema_version") != expected_source["schema_version"]
        or source_contract.get("source_name") != expected_source["source_name"]
        or source_contract.get("historical_universe_policy")
        != expected_source["historical_universe_policy"]
    ):
        raise CryptoUniverseError("SOURCE_CONTRACT_MISMATCH")
    expected_universe = contract["breadth_universe_policy"]
    if (
        universe_policy.get("approval_status")
        != expected_universe["approval_status"]
        or universe_policy.get("universe_kind") != expected_universe["universe_kind"]
    ):
        raise CryptoUniverseError("BREADTH_POLICY_SCOPE_MISMATCH")
    expected_taxonomy = contract["taxonomy_policy"]
    if (
        taxonomy.get("approval_status") != expected_taxonomy["approval_status"]
        or taxonomy.get("unknown_asset_policy")
        != expected_taxonomy["unknown_asset_policy"]
    ):
        raise CryptoUniverseError("TAXONOMY_SCOPE_MISMATCH")


def _active_identity_record(source_asset_id: str, as_of: dt.date, identity: dict):
    matches = []
    for record in identity.get("records", []):
        if record["source_asset_id"] != source_asset_id:
            continue
        start = dt.date.fromisoformat(record["effective_from"])
        end = (
            dt.date.fromisoformat(record["effective_to"])
            if record["effective_to"] is not None
            else None
        )
        if start <= as_of and (end is None or as_of < end):
            matches.append(record)
    if len(matches) > 1:
        raise CryptoUniverseError(f"IDENTITY_RECORD_OVERLAP:{source_asset_id}")
    return matches[0] if matches else None


def _source_identity(
    core: dict,
    manifest: dict,
    member: dict,
    manifest_sha: str,
    policy_path: Path,
    taxonomy_path: Path,
    contract: dict,
) -> dict:
    pair_id = member["pair_id"]
    series = core["ohlc"][pair_id]
    components = {
        "snapshot_manifest": {
            "file": "_manifest.json",
            "sha256": manifest_sha,
            "capture_version": manifest["capture_version"],
        },
        "assets": {
            "source_url": core["contract"]["assets_endpoint"],
            "response_sha256": core["assets_raw"]["response_sha256"],
        },
        "asset_pairs": {
            "source_url": core["contract"]["asset_pairs_endpoint"],
            "response_sha256": core["pairs_raw"]["response_sha256"],
        },
        "ohlc_bundle": {
            "file": core["ohlc_bundle_raw"]["file"],
            "response_sha256": core["ohlc_bundle_raw"]["response_sha256"],
        },
        "member_ohlc": {
            "pair_id": pair_id,
            "file": series["file"],
            "response_sha256": series["response_sha256"],
        },
        "breadth_universe_policy": {
            "path": Path(policy_path).name,
            "sha256": _file_sha(policy_path),
        },
        "taxonomy_policy": {
            "path": Path(taxonomy_path).name,
            "sha256": _file_sha(taxonomy_path),
        },
        "identity_policy": {
            "policy_version": core["identity"]["policy_version"],
            "sha256": core["identity_policy_sha256"],
        },
    }
    for section in components.values():
        digest = section.get("sha256") or section.get("response_sha256")
        if digest is not None and SHA256_RE.fullmatch(digest) is None:
            raise CryptoUniverseError("SOURCE_COMPONENT_SHA_INVALID")
    return {
        "source_id": contract["source_id"],
        "source_url": core["contract"]["asset_pairs_endpoint"],
        "source_sha256": core["pairs_raw"]["response_sha256"],
        "available_at": core["fetched_at_utc"],
        "retrieved_at_utc": core["fetched_at_utc"],
        "lineage_kind": "VALIDATED_COMPOSITE_SNAPSHOT_MANIFEST",
        "lineage_components": components,
    }


def _aliases(
    canonical_id: str,
    source_asset_id: str,
    altname: str,
    as_of: dt.date,
    identity: dict,
) -> list[str]:
    values = {canonical_id, source_asset_id, altname}
    record = _active_identity_record(source_asset_id, as_of, identity)
    if record is not None:
        values.update(record["aliases"])
    valid = sorted(value for value in values if value)
    if not valid or any(GAM.TOKEN_RE.fullmatch(value) is None for value in valid):
        raise CryptoUniverseError(f"ALIAS_TOKEN_INVALID:{source_asset_id}")
    return valid


def _to_master_record(
    core: dict,
    member: dict,
    as_of: dt.date,
    valid_to: str,
    source_identity: dict,
    contract: dict,
) -> tuple[dict, dict]:
    canonical_id = member["canonical_asset_id"]
    source_asset_id = member["source_asset_id"]
    source_asset = core["assets"][source_asset_id]
    pair = core["pairs"][member["pair_id"]]
    aliases = _aliases(
        canonical_id,
        source_asset_id,
        source_asset["altname"],
        as_of,
        core["identity"],
    )
    interval = {
        "valid_from": as_of.isoformat(),
        "valid_to": valid_to,
        "source_identity": copy.deepcopy(source_identity),
    }
    record = {
        "asset_id": f"CRYPTO:KRAKEN:{canonical_id}",
        "market": contract["market"],
        "asset_class": contract["asset_class"],
        "display_name": canonical_id,
        "primary_symbol": canonical_id,
        "exchange_id": contract["exchange_id"],
        "quote_currency": contract["quote_currency"],
        "identifiers": [
            {"namespace": "ATLAS_CANONICAL_ASSET_ID", "value": canonical_id},
            {"namespace": "KRAKEN_ASSET_ID", "value": source_asset_id},
            {"namespace": "KRAKEN_PAIR_ID", "value": member["pair_id"]},
        ],
        "aliases": [
            {
                "alias_type": "SYMBOL",
                "value": alias,
                "exchange_id": contract["exchange_id"],
                **copy.deepcopy(interval),
            }
            for alias in aliases
        ],
        "memberships": [
            {
                "membership_type": "MARKET",
                "membership_id": contract["market"],
                **copy.deepcopy(interval),
            },
            {
                "membership_type": "UNIVERSE",
                "membership_id": contract["membership_id"],
                **copy.deepcopy(interval),
            },
        ],
        "source_identity": copy.deepcopy(source_identity),
    }
    attributes = {
        "asset_id": record["asset_id"],
        "canonical_asset_id": canonical_id,
        "source_asset_id": source_asset_id,
        "source_altname": source_asset["altname"],
        "asset_status": source_asset["status"],
        "pair_id": member["pair_id"],
        "pair_status": pair["status"],
        "pair_altname": pair["altname"],
        "pair_wsname": pair["wsname"],
        "breadth_rank_before_taxonomy": member["rank_before_taxonomy"],
        "breadth_selected_rank": member["selected_rank"],
        "breadth_trailing_30d_usd_turnover": CRYPTO_BREADTH.render_decimal(
            member["series"]["trailing_usd_turnover"], 12
        ),
        "breadth_taxonomy_category": member["taxonomy_category"],
        "breadth_scope_only": True,
        "liquidity_for_investability": None,
        "tradability_decision": None,
        "custody_decision": None,
        "investable_eligible": False,
    }
    return record, attributes


def build_packet(
    snapshot_dir: Path,
    contract: dict | None = None,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = TAXONOMY_PATH,
    identity_path: Path = IDENTITY_PATH,
) -> dict:
    contract = _validate_contract(contract) if contract is not None else load_contract()
    try:
        source_contract = CRYPTO_BREADTH.load_contract(
            ROOT / contract["source_contract"]["path"]
        )
        universe_policy = CRYPTO_BREADTH.load_universe_policy(universe_policy_path)
        taxonomy = CRYPTO_BREADTH.load_exclusion_taxonomy(taxonomy_path)
        core = CRYPTO_BREADTH.source_core(
            Path(snapshot_dir),
            contract=source_contract,
            identity_exceptions_path=identity_path,
        )
        manifest = CRYPTO_BREADTH.validate_manifest(core, Path(snapshot_dir))
    except CRYPTO_BREADTH.BreadthError as exc:
        raise CryptoUniverseError(f"CRYPTO_BREADTH_INPUT_INVALID:{exc}") from exc
    _validate_dependencies(contract, source_contract, universe_policy, taxonomy)
    try:
        selection = CRYPTO_BREADTH.qualified_members(core, universe_policy, taxonomy)
    except CRYPTO_BREADTH.BreadthError as exc:
        raise CryptoUniverseError(f"CRYPTO_BREADTH_SELECTION_INVALID:{exc}") from exc
    target = universe_policy["target_asset_count"]
    if selection["status"] != "OBSERVED_UNCLASSIFIED":
        raise CryptoUniverseError(
            f"BREADTH_SELECTION_UNKNOWN:{selection['reason']}"
        )
    if (
        len(selection["members"]) != target
        or selection["diagnostics"].get("selected_asset_count") != target
        or selection["diagnostics"].get("observed_asset_count") != target
    ):
        raise CryptoUniverseError("BREADTH_SELECTION_FULL_COVERAGE_REQUIRED")

    as_of = core["vintage"] - dt.timedelta(days=1)
    valid_to = _next_date(as_of.isoformat())
    manifest_sha = _file_sha(Path(snapshot_dir) / "_manifest.json")
    records = []
    attributes = []
    for member in selection["members"]:
        source_identity = _source_identity(
            core,
            manifest,
            member,
            manifest_sha,
            Path(universe_policy_path),
            Path(taxonomy_path),
            contract,
        )
        record, attribute = _to_master_record(
            core, member, as_of, valid_to, source_identity, contract
        )
        records.append(record)
        attributes.append(attribute)
    try:
        master = GAM.build_master(
            {
                "schema_version": GAM.INPUT_SCHEMA_VERSION,
                "master_id": f"KRAKEN_BREADTH_SOURCE_COVERAGE_{as_of:%Y%m%d}",
                "as_of_date": as_of.isoformat(),
                "records": records,
            }
        )
    except GAM.AssetMasterError as exc:
        raise CryptoUniverseError(f"GLOBAL_ASSET_MASTER_INVALID:{exc}") from exc

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "contract_version": contract["contract_version"],
        "as_of_date": as_of.isoformat(),
        "knowledge_as_of_utc": core["fetched_at_utc"],
        "status": "BREADTH_SOURCE_COVERAGE_UNIVERSE_VALIDATED",
        "membership_semantics": contract["membership_semantics"],
        "effective_interval": {
            "valid_from": as_of.isoformat(),
            "valid_to": valid_to,
        },
        "selected_count": len(records),
        "target_count": target,
        "snapshot_lineage": {
            "vintage_date": core["snapshot_date"],
            "manifest_sha256": manifest_sha,
            "capture_version": manifest["capture_version"],
            "breadth_policy_version": universe_policy["policy_version"],
            "breadth_policy_sha256": _file_sha(universe_policy_path),
            "taxonomy_policy_version": taxonomy["policy_version"],
            "taxonomy_policy_sha256": _file_sha(taxonomy_path),
            "identity_policy_version": core["identity"]["policy_version"],
            "identity_policy_sha256": core["identity_policy_sha256"],
            "historical_universe_policy": source_contract[
                "historical_universe_policy"
            ],
        },
        "source_attribute_rows": sorted(
            attributes, key=lambda item: item["canonical_asset_id"]
        ),
        "asset_master": master,
        "policy_status": copy.deepcopy(contract["policy_status"]),
        "authority": copy.deepcopy(contract["authority"]),
        "unresolved_boundaries": [
            "LISTING_POLICY_UNRATIFIED",
            "DELISTING_POLICY_UNRATIFIED",
            "LIQUIDITY_FOR_INVESTABILITY_UNRATIFIED",
            "TRADABILITY_POLICY_UNRATIFIED",
            "CUSTODY_POLICY_UNRATIFIED",
            "INVESTABLE_UNIVERSE_POLICY_UNRATIFIED",
            "EXCHANGE_COVERAGE_KRAKEN_ONLY",
            "LIVE_MASTER_POPULATION_NOT_IMPLEMENTED",
        ],
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def run(
    snapshot_dir: Path,
    output_path: Path,
    contract_path: Path = CONTRACT_PATH,
    universe_policy_path: Path = UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = TAXONOMY_PATH,
    identity_path: Path = IDENTITY_PATH,
) -> dict:
    packet = build_packet(
        snapshot_dir,
        load_contract(contract_path),
        universe_policy_path,
        taxonomy_path,
        identity_path,
    )
    GAM.write_json_atomic(output_path, packet)
    return packet


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    parser.add_argument("--universe-policy", type=Path, default=UNIVERSE_POLICY_PATH)
    parser.add_argument("--taxonomy", type=Path, default=TAXONOMY_PATH)
    parser.add_argument("--identity", type=Path, default=IDENTITY_PATH)
    args = parser.parse_args(argv)
    try:
        packet = run(
            args.snapshot_dir,
            args.out,
            args.contract,
            args.universe_policy,
            args.taxonomy,
            args.identity,
        )
    except CryptoUniverseError as exc:
        print(f"Crypto global universe failed: {exc}")
        return 1
    print(
        f"Crypto global universe: selected={packet['selected_count']} "
        f"as_of={packet['as_of_date']} sha256={packet['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
