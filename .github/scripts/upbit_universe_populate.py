#!/usr/bin/env python3
"""P3-12 Upbit tradeable-universe scheduled population wiring.

Reads the exact, already-committed Upbit public raw snapshot for one
snapshot_date and publishes or verifies the corresponding classification
packet built by universe/upbit_tradeable_universe.py. Existing canonical
packets are never repaired or replaced. The sole same-vintage exception is
an append-only, content-addressed successor built by the exact-code-approved
release builder when the preserved source is policy=True/taxonomy=False and
the live exact eight-market content+code release passes end to end.

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
RECORD_SCHEMA_VERSION = "upbit_universe_population/1"
TRANSITION_DIRECTORY = "transitions"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_population", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_population", "identity/upbit_market_identity_proposal.py")
RELEASE = _load_module(
    "upbit_exact_release_binding_release_for_population",
    "identity/upbit_exact_release_binding_release.py",
)


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


def transition_output_path(
    snapshot_date: str,
    *,
    source_payload_sha256: str,
    successor_payload_sha256: str,
    data_root: Path = DATA_ROOT,
) -> Path:
    """Content-addressed path for an immutable same-vintage successor.

    The canonical ``<date>/packet.json`` remains the original observation.
    A successor is a complete, directly consumable P3 record at a separate
    path whose directory binds both the preserved source record and the
    deterministically rebuilt successor.  Full hashes avoid mutable aliases
    such as ``latest`` and make a second, different write fail closed.
    """
    return (
        Path(data_root)
        / snapshot_date
        / TRANSITION_DIRECTORY
        / f"{source_payload_sha256}-to-{successor_payload_sha256}"
        / "packet.json"
    )


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


def _write_immutable_transition(target: Path, projection: dict, *, snapshot_date: str) -> str:
    """Atomically create successor+manifest; never replace existing bytes."""
    manifest = projection["manifest"]
    manifest_target = target.with_name("transition.json")
    successor_bytes = projection["successor_bytes"]
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")

    def verify_existing() -> str:
        if target.parent.is_symlink() or target.is_symlink() or manifest_target.is_symlink():
            raise PopulationError(f"EXISTING_TRANSITION_INVALID:{snapshot_date}:symlink")
        try:
            existing_record_bytes = target.read_bytes()
            existing_manifest_bytes = manifest_target.read_bytes()
        except OSError as exc:
            raise PopulationError(f"EXISTING_TRANSITION_UNREADABLE:{snapshot_date}:{exc}") from exc
        if existing_record_bytes != successor_bytes or existing_manifest_bytes != manifest_bytes:
            raise PopulationError(f"EXISTING_TRANSITION_DRIFT_OR_TAMPER:{snapshot_date}")
        return "verified_existing_transition"

    transition_root = target.parents[1]
    date_root = target.parents[2]
    if transition_root.is_symlink() or target.parent.is_symlink() or date_root.is_symlink():
        raise PopulationError(f"EXISTING_TRANSITION_INVALID:{snapshot_date}:symlink")
    transition_root.mkdir(parents=True, exist_ok=True)
    for child in transition_root.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_symlink():
            raise PopulationError(f"EXISTING_TRANSITION_INVALID:{snapshot_date}:symlink")
        if child.name != target.parent.name:
            raise PopulationError(f"SIBLING_TRANSITION_FORBIDDEN:{snapshot_date}:{child.name}")
    if target.parent.is_symlink() or target.is_symlink() or manifest_target.is_symlink():
        raise PopulationError(f"EXISTING_TRANSITION_INVALID:{snapshot_date}:symlink")
    if target.exists():
        return verify_existing()

    staging = transition_root / f".{target.parent.name}.tmp.{os.getpid()}"
    try:
        staging.mkdir()
        with (staging / "packet.json").open("xb") as handle:
            handle.write(successor_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        with (staging / "transition.json").open("x", encoding="utf-8") as handle:
            handle.write(manifest_bytes.decode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.rename(staging, target.parent)
        except OSError:
            if target.parent.exists():
                return verify_existing()
            raise
    finally:
        if staging.exists():
            for child in staging.iterdir():
                child.unlink()
            staging.rmdir()
    return "transition_populated"


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
            transition_target = transition_output_path(
                snapshot_date,
                source_payload_sha256=existing_hash,
                successor_payload_sha256=record["payload_sha256"],
                data_root=data_root,
            )
            try:
                source_relative = str(target.resolve().relative_to(ROOT.resolve()))
                successor_relative = str(transition_target.resolve().relative_to(ROOT.resolve()))
            except ValueError as exc:
                raise PopulationError(f"TRANSITION_PATH_OUTSIDE_REPOSITORY:{snapshot_date}") from exc
            try:
                projection = RELEASE.build_same_vintage_transition_projection(
                    repo_root=ROOT,
                    source_record_relative_path=source_relative,
                    successor_record_relative_path=successor_relative,
                    successor_record=record,
                    evaluation_as_of=snapshot_date,
                )
            except RELEASE.ReleaseProjectionError as exc:
                raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}:{exc}") from exc
            outcome = _write_immutable_transition(
                transition_target,
                projection,
                snapshot_date=snapshot_date,
            )
            return {
                "outcome": outcome,
                "reason": "POLICY_RATIFIED_TAXONOMY_UNRATIFIED_TO_EXACT_RELEASE_SAME_RAW_VINTAGE",
                "path": str(transition_target),
                "transition_manifest_path": str(transition_target.with_name("transition.json")),
                "payload_sha256": record["payload_sha256"],
                "source_payload_sha256": existing_hash,
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
        f"transition_manifest_path={single_line(result.get('transition_manifest_path'))}",
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
