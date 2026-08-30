#!/usr/bin/env python3
"""Exact-hash P3-12 -> P4-07 public evidence consumer boundary.

This module consumes one committed P3 universe *record*, verifies both of
its canonical payload digests and effective-time lineage, and returns the
complete nonzero P4 capture cohort.  It never reclassifies historical rows:
an ``IDENTITY_UNRATIFIED`` row remains outside the cohort even if a current
registry happens to know that symbol.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
BRIDGE_CONTRACT_PATH = ROOT / "config" / "upbit_p3_p4_bridge_contract.json"
P4_POLICY_PATH = ROOT / "config" / "upbit_market_evidence_policy.json"
UTC = dt.timezone.utc
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class BridgeError(ValueError):
    """Fail-closed P3->P4 lineage/ratification violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path, code: str) -> dict:
    if Path(path).is_symlink():
        raise BridgeError(f"{code}_SYMLINK:{path}")
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BridgeError(f"{code}_UNREADABLE:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise BridgeError(f"{code}_ROOT_INVALID:{path}")
    return value


def _parse_utc(value, code: str) -> dt.datetime:
    if not isinstance(value, str):
        raise BridgeError(code)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise BridgeError(code) from exc
    if parsed.tzinfo is None:
        raise BridgeError(code)
    return parsed.astimezone(UTC)


def _hash_without_self(value: dict, field: str, code: str) -> str:
    declared = value.get(field)
    if not isinstance(declared, str) or SHA256_RE.fullmatch(declared) is None:
        raise BridgeError(f"{code}_SHAPE")
    actual = payload_sha256({key: item for key, item in value.items() if key != field})
    if actual != declared:
        raise BridgeError(f"{code}_MISMATCH:expected={declared}:actual={actual}")
    return declared


def load_bridge_contract(path: Path = BRIDGE_CONTRACT_PATH) -> dict:
    contract = _read_json(path, "BRIDGE_CONTRACT")
    if contract.get("schema_version") != "upbit_p3_p4_bridge_contract/1":
        raise BridgeError("BRIDGE_CONTRACT_VERSION_MISMATCH")
    if contract.get("provider_public_get_only") is not True:
        raise BridgeError("BRIDGE_PUBLIC_PROVIDER_INVARIANT_VIOLATED")
    for field in (
        "historical_identity_backfill_authorized", "exchange_authorized",
        "order_authorized", "paper_exit_authorized", "production_authorized",
        "real_capital_authorized", "trading_authorized",
    ):
        if contract.get(field) is not False:
            raise BridgeError(f"BRIDGE_AUTHORITY_INVARIANT_VIOLATED:{field}")
    return contract


def load_ratified_p4_policy(
    contract: dict | None = None,
    *, policy_path: Path = P4_POLICY_PATH,
) -> dict:
    contract = contract or load_bridge_contract()
    policy = _read_json(policy_path, "P4_POLICY")
    digest = _hash_without_self(policy, "packet_sha256", "P4_POLICY_HASH")
    pin = contract.get("p4_policy") or {}
    if (
        policy.get("approval_status") != "RATIFIED"
        or policy.get("policy_id") != pin.get("policy_id")
        or policy.get("policy_version") != pin.get("policy_version")
        or digest != pin.get("packet_sha256")
    ):
        raise BridgeError("P4_POLICY_EXACT_PIN_MISMATCH")
    for field in (
        "exchange_authorized", "order_authorized", "paper_exit_authorized",
        "production_authorized", "real_capital_authorized", "trading_authorized",
    ):
        if policy.get(field) is not False:
            raise BridgeError(f"P4_POLICY_AUTHORITY_INVARIANT_VIOLATED:{field}")
    return policy


def consume_universe_record(
    record_path: Path,
    *,
    expected_record_sha256: str | None = None,
    contract_path: Path = BRIDGE_CONTRACT_PATH,
    repo_root: Path = ROOT,
) -> dict:
    """Validate one exact P3 record and return its full eligible cohort.

    ``expected_record_sha256`` is the caller's exact-hash pin.  The initial
    post-ratification record is additionally pinned by the bridge contract,
    so a locally rehashed forged record cannot replace that natural anchor.
    """
    contract = load_bridge_contract(contract_path)
    policy_path = Path(repo_root) / contract["p4_policy"]["path"]
    p4_policy = load_ratified_p4_policy(contract, policy_path=policy_path)
    path = Path(record_path)
    record = _read_json(path, "UNIVERSE_RECORD")
    record_hash = _hash_without_self(record, "payload_sha256", "UNIVERSE_RECORD_HASH")
    if expected_record_sha256 is not None and record_hash != expected_record_sha256:
        raise BridgeError(
            f"UNIVERSE_RECORD_EXACT_HASH_MISMATCH:expected={expected_record_sha256}:actual={record_hash}"
        )

    initial = contract["initial_post_ratification_anchor"]
    anchored_path = (Path(repo_root) / initial["path"]).resolve()
    if path.resolve() == anchored_path and record_hash != initial["record_payload_sha256"]:
        raise BridgeError("INITIAL_UNIVERSE_ANCHOR_HASH_MISMATCH")

    if record.get("schema_version") != contract["p3_record_schema_version"]:
        raise BridgeError("UNIVERSE_RECORD_SCHEMA_MISMATCH")
    packet = record.get("packet")
    if not isinstance(packet, dict):
        raise BridgeError("UNIVERSE_PACKET_MISSING")
    packet_hash = _hash_without_self(packet, "payload_sha256", "UNIVERSE_PACKET_HASH")
    if packet.get("schema_version") != contract["p3_packet_schema_version"]:
        raise BridgeError("UNIVERSE_PACKET_SCHEMA_MISMATCH")
    if (
        packet.get("policy_version") != contract["required_p3_policy_version"]
        or packet.get("taxonomy_version") != contract["required_p3_taxonomy_version"]
        or packet.get("policy_ratified") is not True
        or packet.get("taxonomy_ratified") is not True
        or (record.get("ratification") or {}).get("effective_for_snapshot") is not True
    ):
        raise BridgeError("UNIVERSE_RATIFICATION_NOT_EFFECTIVE")

    snapshot_date = record.get("snapshot_date")
    if not isinstance(snapshot_date, str) or packet.get("snapshot_date") != snapshot_date:
        raise BridgeError("UNIVERSE_SNAPSHOT_DATE_MISMATCH")
    try:
        snapshot_day = dt.date.fromisoformat(snapshot_date)
    except ValueError as exc:
        raise BridgeError("UNIVERSE_SNAPSHOT_DATE_INVALID") from exc
    effective_from = _parse_utc(contract["effective_from_utc"], "BRIDGE_EFFECTIVE_FROM_INVALID")
    if snapshot_day < effective_from.date():
        raise BridgeError("HISTORICAL_IDENTITY_BACKFILL_FORBIDDEN")

    raw = record.get("raw_snapshot") or {}
    manifest_path = Path(repo_root) / str(raw.get("path", "")) / "_manifest.json"
    manifest = _read_json(manifest_path, "UNIVERSE_RAW_MANIFEST")
    manifest_hash = file_sha256(manifest_path)
    if (
        raw.get("manifest_sha256") != manifest_hash
        or packet.get("manifest_sha256") != manifest_hash
        or manifest.get("vintage_date") != snapshot_date
    ):
        raise BridgeError("UNIVERSE_RAW_MANIFEST_BINDING_MISMATCH")

    observed_at = _parse_utc(manifest.get("downloaded_at_utc"), "UNIVERSE_OBSERVED_AT_INVALID")
    available_at = _parse_utc(packet.get("available_at"), "UNIVERSE_AVAILABLE_AT_INVALID")
    generated_at = _parse_utc(record.get("generated_at"), "UNIVERSE_GENERATED_AT_INVALID")
    if not (observed_at <= available_at <= generated_at):
        raise BridgeError("UNIVERSE_EFFECTIVE_TIME_ORDER_INVALID")

    rows = packet.get("markets")
    if not isinstance(rows, list) or not rows:
        raise BridgeError("UNIVERSE_MARKETS_EMPTY_OR_INVALID")
    market_codes = [row.get("market") for row in rows if isinstance(row, dict)]
    if len(market_codes) != len(rows) or any(not isinstance(code, str) for code in market_codes):
        raise BridgeError("UNIVERSE_MARKET_ROW_MALFORMED")
    duplicate_diagnostics = packet.get("duplicate_market_codes")
    duplicate_reported = bool(duplicate_diagnostics)
    if isinstance(duplicate_diagnostics, dict):
        duplicate_reported = any(bool(value) for value in duplicate_diagnostics.values())
    if len(set(market_codes)) != len(market_codes) or duplicate_reported:
        raise BridgeError("UNIVERSE_DUPLICATE_MARKET")

    summary = packet.get("summary") or {}
    if summary.get("market_count") != len(rows):
        raise BridgeError("UNIVERSE_SUMMARY_MARKET_COUNT_MISMATCH")
    eligible_states = set(contract["eligible_states"])
    cohort = sorted(row["market"] for row in rows if row.get("state") in eligible_states)
    if not cohort:
        raise BridgeError("UNIVERSE_ELIGIBLE_COHORT_EMPTY")
    if any(
        row.get("candidate_canonical_asset_id") is None
        for row in rows if row.get("state") in eligible_states
    ):
        raise BridgeError("UNIVERSE_ELIGIBLE_IDENTITY_MISSING")

    identity_unratified_count = sum(
        row.get("reason") == contract["identity_unratified_reason"] for row in rows
    )
    if path.resolve() == anchored_path:
        if cohort != initial["paper_markets"]:
            raise BridgeError("INITIAL_UNIVERSE_COHORT_MISMATCH")
        if identity_unratified_count != initial["identity_unratified_count"]:
            raise BridgeError("INITIAL_IDENTITY_UNRATIFIED_COUNT_MISMATCH")

    return {
        "bridge_id": contract["bridge_id"],
        "record_path": str(path),
        "record_payload_sha256": record_hash,
        "packet_payload_sha256": packet_hash,
        "snapshot_date": snapshot_date,
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "available_at": available_at.isoformat().replace("+00:00", "Z"),
        "generated_at": generated_at.isoformat().replace("+00:00", "Z"),
        "markets": cohort,
        "market_count": len(cohort),
        "identity_unratified_count": identity_unratified_count,
        "historical_identity_backfill_applied": False,
        "p4_policy": {
            "policy_id": p4_policy["policy_id"],
            "policy_version": p4_policy["policy_version"],
            "packet_sha256": p4_policy["packet_sha256"],
        },
        "authority": {
            "evidence_derivation_only": True,
            "decision_eligible": False,
            "candidate_promotion_authorized": False,
            "paper_exit_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def snapshot_key(lineage: dict) -> str:
    digest = lineage["record_payload_sha256"]
    return f"{lineage['snapshot_date']}-p3-{digest[:16]}"
