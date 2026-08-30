#!/usr/bin/env python3
"""P3-12 Upbit tradeable-universe scheduled population wiring.

Reads the exact, already-committed Upbit public raw snapshot for one
snapshot_date and publishes, verifies, or repairs the corresponding
classification packet built by universe/upbit_tradeable_universe.py.

This module never calls a network provider. It still builds mechanical
identity proposals for transparency, but production classification now
loads the evidence-bound, effective-dated RATIFIED identity registry. An
unmapped/held market remains fail-closed at OBSERVATION_POOL.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "upbit_tradeable_universe"
IDENTITY_GOVERNANCE_FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"
RECORD_SCHEMA_VERSION = "upbit_universe_population/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_population", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_population", "identity/upbit_market_identity_proposal.py")


class PopulationError(ValueError):
    """Fail-closed P3-12 population wiring violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot_dir(snapshot_date: str, raw_root: Path = RAW_ROOT) -> Path:
    return Path(raw_root) / snapshot_date


def output_path(snapshot_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / snapshot_date / "packet.json"


def _identity_review(core: dict, capture_contract: dict, *, source_url: str, review_as_of: str) -> dict:
    exceptions_doc = None
    exceptions_path = ROOT / "config" / "upbit_asset_identity_exceptions.json"
    if exceptions_path.exists():
        exceptions_doc = json.loads(exceptions_path.read_text(encoding="utf-8"))
    proposals = []
    for market, entry in sorted(core["markets"].items()):
        if not entry.get("market_all_available"):
            continue
        proposals.append(
            IDP.build_proposal(
                {"market": market, "korean_name": entry.get("korean_name"), "english_name": entry.get("english_name")},
                review_as_of=review_as_of, source_url=source_url,
                response_sha256=core["component_hashes"][capture_contract["market_all_raw_file"]],
                available_at=core["available_at"], exceptions_doc=exceptions_doc,
            )
        )
    # known_canonical_ids is intentionally omitted here: this repository has
    # no ratified, broad "known-good canonical crypto asset" registry to
    # cross-reference against yet -- config/upbit_exclusion_taxonomy.json
    # only enumerates known EXCLUDED categories (stablecoins so far), not
    # every legitimate asset, so treating it as that registry would flag
    # nearly every real market (e.g. BTC, ETH) as a false
    # NO_CANONICAL_CROSS_REFERENCE gap. Only the collision check --
    # DUPLICATE_CANONICAL_TARGET, which needs no external registry -- runs
    # in production. identity/upbit_market_identity_proposal.py's own tests
    # exercise the cross-reference check directly against a fixture
    # registry to prove the mechanism itself is correct.
    findings = IDP.identity_review_findings(proposals, known_canonical_ids=None)
    return {
        "proposal_count": len(proposals),
        "findings": findings,
        "blocked_markets": sorted(IDP.blocked_markets(findings)),
    }


def rebuild(snapshot_date: str, raw_root: Path = RAW_ROOT) -> dict:
    directory = snapshot_dir(snapshot_date, raw_root)
    if not directory.is_dir():
        raise PopulationError(f"RAW_SNAPSHOT_MISSING:{snapshot_date}")
    capture_contract = UNI.UPBIT_CAPTURE.load_contract()
    try:
        core = UNI.load_snapshot_core(directory, capture_contract)
    except UNI.UPBIT_CAPTURE.CaptureError as exc:
        raise PopulationError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}:{exc}") from exc

    identity_review = _identity_review(
        core, capture_contract,
        source_url=capture_contract["market_all_endpoint"], review_as_of=snapshot_date,
    )
    policy = UNI.load_policy()
    taxonomy = UNI.load_taxonomy()
    identity_registry = UNI.load_identity_registry()
    effective_registry = UNI.effective_identity_mapping(identity_registry, snapshot_date)
    packet = UNI.build_classification(
        core, evaluation_as_of=snapshot_date, policy=policy, taxonomy=taxonomy,
        ratified_identity_registry=effective_registry,
        blocked_markets=set(identity_review["blocked_markets"]),
    )
    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "snapshot_date": snapshot_date,
        "generated_at": core["available_at"],
        "raw_snapshot": {
            "path": f"evidence/crypto/upbit/raw/{snapshot_date}",
            "manifest_sha256": core["manifest_sha256"],
        },
        "builder": {
            "module": "universe/upbit_tradeable_universe.py",
            "output_schema_version": packet["schema_version"],
        },
        "ratification": {
            "effective_for_snapshot": bool(
                packet["policy_ratified"] and packet["taxonomy_ratified"] and effective_registry
            ),
            "policy": {
                "path": "config/upbit_tradeable_universe_policy.json",
                "file_sha256": UNI._file_sha(UNI.POLICY_PATH),
                "effective_from": policy.get("effective_date"),
            },
            "taxonomy": {
                "path": "config/upbit_exclusion_taxonomy.json",
                "file_sha256": UNI._file_sha(UNI.TAXONOMY_PATH),
                "effective_from": taxonomy.get("effective_from"),
            },
            "identity_registry": {
                "path": "config/upbit_asset_identity_registry.json",
                "file_sha256": UNI._file_sha(UNI.IDENTITY_REGISTRY_PATH),
                "registry_version": identity_registry["registry_version"],
                "effective_from": identity_registry["effective_from"],
                "mapping_count": len(effective_registry),
            },
        },
        "identity_review": identity_review,
        "authority": {
            "observation_pool_population_only": not bool(effective_registry),
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
        },
        "packet": packet,
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def _paper_markets(record: dict) -> list[str]:
    packet = record.get("packet") or {}
    return sorted(
        row.get("market")
        for row in packet.get("markets") or []
        if isinstance(row, dict)
        and row.get("state") == UNI.STATE_PAPER_ELIGIBLE
        and isinstance(row.get("market"), str)
    )


def _all_authority_false(record: dict) -> bool:
    packet_authority = (record.get("packet") or {}).get("authority") or {}
    record_authority = record.get("authority") or {}
    return (
        bool(packet_authority)
        and all(value is False for value in packet_authority.values())
        and bool(record_authority)
        and all(value is False for value in record_authority.values())
    )


def _safe_frozen_exact_hash_transition(
    existing: dict,
    record: dict,
    *,
    existing_hash: str,
    freeze: dict,
) -> bool:
    """Allow exactly one correction of the known frozen same-vintage record.

    The 2026-08-30 packet already contained eight PAPER rows before its
    identity/taxonomy lineage was frozen.  Requiring an old zero-row packet
    therefore cannot release the corrected exact-hash configuration.  This
    alternative remains narrow: it accepts only an explicitly frozen record
    hash, the exact old registry/taxonomy file hashes recorded by governance,
    the exact CIO-released market set, identical raw evidence, and zero
    operational authority on both sides.
    """
    immutable_keys = (
        "schema_version", "snapshot_date", "generated_at",
        "raw_snapshot", "builder", "identity_review",
    )
    if not all(existing.get(key) == record.get(key) for key in immutable_keys):
        return False
    if existing_hash not in (freeze.get("blocked_universe_record_payload_sha256s") or []):
        return False
    released_markets = freeze.get("released_paper_markets")
    if not isinstance(released_markets, list) or not released_markets:
        return False
    if _paper_markets(existing) != sorted(released_markets) or _paper_markets(record) != sorted(released_markets):
        return False
    if not _all_authority_false(existing) or not _all_authority_false(record):
        return False

    old_ratification = existing.get("ratification") or {}
    new_ratification = record.get("ratification") or {}
    old_registry = old_ratification.get("identity_registry") or {}
    old_taxonomy = old_ratification.get("taxonomy") or {}
    blocked_registry = freeze.get("blocked_identity_registry") or {}
    blocked_taxonomy = freeze.get("blocked_taxonomy") or {}
    if old_registry.get("file_sha256") != blocked_registry.get("pre_freeze_file_sha256"):
        return False
    if old_taxonomy.get("file_sha256") != blocked_taxonomy.get("pre_freeze_file_sha256"):
        return False
    if old_ratification.get("effective_for_snapshot") is not True:
        return False
    if new_ratification.get("effective_for_snapshot") is not True:
        return False
    if (new_ratification.get("identity_registry") or {}).get("mapping_count") != len(released_markets):
        return False

    old_packet = existing.get("packet") or {}
    new_packet = record.get("packet") or {}
    old_summary = old_packet.get("summary") or {}
    new_summary = new_packet.get("summary") or {}
    return (
        old_packet.get("policy_ratified") is True
        and old_packet.get("taxonomy_ratified") is True
        and new_packet.get("policy_ratified") is True
        and new_packet.get("taxonomy_ratified") is True
        and old_summary.get("paper_eligible_count") == len(released_markets)
        and new_summary.get("paper_eligible_count") == len(released_markets)
        and old_summary.get("market_count") == new_summary.get("market_count")
    )


def populate(snapshot_date: str, raw_root: Path = RAW_ROOT, data_root: Path = DATA_ROOT) -> dict:
    record = rebuild(snapshot_date, raw_root)
    target = output_path(snapshot_date, data_root)
    if target.exists():
        if target.is_symlink():
            raise PopulationError(f"EXISTING_PACKET_INVALID:{snapshot_date}:symlink")
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        if existing != record:
            existing_hash = existing.get("payload_sha256")
            if not isinstance(existing_hash, str) or len(existing_hash) != 64:
                raise PopulationError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}")
            if payload_sha256({key: value for key, value in existing.items() if key != "payload_sha256"}) != existing_hash:
                raise PopulationError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}")
            immutable_keys = (
                "schema_version", "snapshot_date", "generated_at",
                "raw_snapshot", "builder", "identity_review",
            )
            same_pre_ratification_evidence = all(
                existing.get(key) == record.get(key) for key in immutable_keys
            )
            old_packet = existing.get("packet") or {}
            new_packet = record.get("packet") or {}
            old_summary = old_packet.get("summary") or {}
            old_rows = old_packet.get("markets") or []
            old_authority = old_packet.get("authority") or {}
            old_record_authority = existing.get("authority") or {}
            old_fail_closed = (
                old_summary.get("tradeable_universe_count") == 0
                and old_summary.get("paper_eligible_count") == 0
                and old_summary.get("market_count") == len(old_rows)
                and (
                    old_summary.get("observation_pool_count", 0)
                    + old_summary.get("blocked_count", 0)
                ) == len(old_rows)
                and all(
                    row.get("state") in (UNI.STATE_OBSERVATION_POOL, UNI.STATE_BLOCKED)
                    for row in old_rows
                    if isinstance(row, dict)
                )
                and old_authority
                and all(value is False for value in old_authority.values())
                and old_record_authority.get("observation_pool_population_only") is True
                and all(
                    value is False
                    for key, value in old_record_authority.items()
                    if key != "observation_pool_population_only"
                )
            )
            safe_ratification_transition = (
                same_pre_ratification_evidence
                and old_fail_closed
                and old_packet.get("policy_ratified") is False
                and old_packet.get("taxonomy_ratified") is False
                and new_packet.get("policy_ratified") is True
                and new_packet.get("taxonomy_ratified") is True
                and record.get("ratification", {}).get("effective_for_snapshot") is True
            )
            try:
                freeze = json.loads(IDENTITY_GOVERNANCE_FREEZE_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise PopulationError(f"IDENTITY_GOVERNANCE_FREEZE_INVALID:{exc}") from exc
            safe_frozen_transition = _safe_frozen_exact_hash_transition(
                existing, record, existing_hash=existing_hash, freeze=freeze,
            )
            if not (safe_ratification_transition or safe_frozen_transition):
                raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
            temporary = target.with_name(f".{target.name}.tmp.{os.getpid()}")
            try:
                temporary.write_text(
                    json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(target)
            finally:
                if temporary.exists():
                    temporary.unlink()
            return {
                "outcome": "ratified_reclassification",
                "reason": (
                    "FROZEN_TO_EXACT_HASH_RATIFIED_SAME_RAW_VINTAGE"
                    if safe_frozen_transition
                    else "UNRATIFIED_TO_RATIFIED_SAME_RAW_VINTAGE"
                ),
                "path": str(target), "payload_sha256": record["payload_sha256"],
            }
        return {
            "outcome": "verified_existing", "reason": None,
            "path": str(target), "payload_sha256": record["payload_sha256"],
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated", "reason": None,
        "path": str(target), "payload_sha256": record["payload_sha256"],
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    single_line = lambda value: (value or "").replace("\n", " ").replace("\r", " ")
    lines = [
        f"outcome={single_line(result.get('outcome'))}",
        f"reason={single_line(result.get('reason'))}",
        f"path={single_line(result.get('path'))}",
        f"payload_sha256={single_line(result.get('payload_sha256'))}",
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(argv=None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.snapshot_date, args.raw_root, args.data_root)
    except PopulationError as exc:
        _write_github_output({"outcome": "failed", "reason": str(exc), "path": None, "payload_sha256": None})
        print(f"P3-12 Upbit universe population failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P3-12 Upbit universe population {result['outcome']}"
        f" date={args.snapshot_date} path={result['path']} sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
