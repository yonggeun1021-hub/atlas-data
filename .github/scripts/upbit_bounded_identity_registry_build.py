#!/usr/bin/env python3
"""P3-12-ID-01 runner: build the Upbit Bounded Identity Registry candidate
and its evidence packet, and re-run the P3-12 shadow-apply funnel with it.

This module NEVER writes to config/upbit_exclusion_taxonomy.json or
config/upbit_tradeable_universe_policy.json, NEVER ratifies the identity
registry, and NEVER opens/merges a PR. It writes only an evidence packet
under data/observations/upbit_bounded_identity_registry/<date>/packet.json.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data" / "observations" / "upbit_bounded_identity_registry"
SCHEMA_VERSION = "upbit_bounded_identity_registry_packet/1"
REVIEW_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_id01_build", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_id01_build", "identity/upbit_market_identity_proposal.py")
HARNESS = _load_module("upbit_shadow_validation_harness_for_id01_build", "universe/upbit_shadow_validation_harness.py")
ID01 = _load_module("upbit_bounded_identity_registry_for_build", "identity/upbit_bounded_identity_registry.py")


class BoundedIdentityRegistryBuildError(ValueError):
    """Fail-closed P3-12-ID-01 build/apply violation."""


def _summary(result: dict) -> dict:
    hold_by_verdict: dict = {}
    for row in result["hold_list"]:
        hold_by_verdict[row["verdict"]] = hold_by_verdict.get(row["verdict"], 0) + 1
    return {
        "starting_scope_count": len(result["registry_candidates"]) + len(result["hold_list"]),
        "verified_candidate_count": len(result["registry_candidates"]),
        "hold_count": len(result["hold_list"]),
        "hold_count_by_verdict": dict(sorted(hold_by_verdict.items())),
    }


def build(snapshot_date: str, *, raw_root: Path = HARNESS.RAW_ROOT, evaluation_as_of: str,
          code_commit_sha: str | None = None) -> dict:
    directory = Path(raw_root) / snapshot_date
    if not directory.is_dir():
        raise BoundedIdentityRegistryBuildError(f"RAW_SNAPSHOT_MISSING:{snapshot_date}")
    capture_contract = UNI.UPBIT_CAPTURE.load_contract()
    try:
        core = UNI.load_snapshot_core(directory, capture_contract)
    except UNI.UPBIT_CAPTURE.CaptureError as exc:
        raise BoundedIdentityRegistryBuildError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}:{exc}") from exc

    real_taxonomy = UNI.load_taxonomy()
    real_policy = UNI.load_policy()
    evidence_by_id = ID01.load_identity_evidence()
    proposals = HARNESS.build_identity_proposals(core, capture_contract, review_as_of=snapshot_date)
    findings = IDP.identity_review_findings(proposals, known_canonical_ids=None)
    blocked = IDP.blocked_markets(findings)

    result = ID01.build_registry_candidate(
        core=core, capture_contract=capture_contract, taxonomy=real_taxonomy, proposals=proposals,
        blocked_markets=blocked, evidence_by_id=evidence_by_id, evaluation_as_of=evaluation_as_of,
    )
    registry_mapping = ID01.registry_candidate_as_mapping(result["registry_candidates"])
    shadow_after = ID01.shadow_apply_funnel(
        core=core, real_policy=real_policy, real_taxonomy=real_taxonomy, registry_mapping=registry_mapping,
        blocked_markets=blocked, evaluation_as_of=evaluation_as_of,
    )
    resolved_commit = code_commit_sha or HARNESS.git_commit_sha()

    packet = {
        "schema_version": SCHEMA_VERSION,
        "review_status": REVIEW_STATUS,
        "snapshot_date": snapshot_date,
        "evaluation_as_of": evaluation_as_of,
        "generated_at": core["available_at"],
        "code_commit_sha": resolved_commit,
        "source": {
            "raw_snapshot_path": f"evidence/crypto/upbit/raw/{snapshot_date}",
            "raw_manifest_sha256": core["manifest_sha256"],
            "real_taxonomy_path": "config/upbit_exclusion_taxonomy.json",
            "real_taxonomy_file_sha256": HARNESS.file_sha256(UNI.TAXONOMY_PATH),
            "identity_evidence_path": "config/upbit_bounded_identity_evidence.json",
            "identity_evidence_file_sha256": HARNESS.file_sha256(ID01.EVIDENCE_PATH),
        },
        "registry_boundary": {
            "registry_ratified": False,
            "taxonomy_approval_status_changed": False,
            "policy_approval_status_changed": False,
            "ticker_match_alone_never_sufficient": True,
            "scope": "Only Upbit markets whose candidate canonical id has an active taxonomy record as of evaluation_as_of.",
        },
        "summary": _summary(result),
        "registry_candidates": result["registry_candidates"],
        "hold_list": result["hold_list"],
        "evidence": result["evidence"],
        "shadow_funnel_after": shadow_after["summary"],
        "shadow_funnel_reason_distribution": dict(sorted(
            {row["reason"]: sum(1 for r in shadow_after["markets"] if r["reason"] == row["reason"])
             for row in shadow_after["markets"]}.items()
        )),
        "shadow_funnel_markets": shadow_after["markets"],
        "authority": {
            "review_only": True,
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "decision_eligible": False,
            "action_generation_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = HARNESS.payload_sha256(packet)
    return packet


def output_path(snapshot_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / snapshot_date / "packet.json"


def populate(snapshot_date: str, *, raw_root: Path = HARNESS.RAW_ROOT, data_root: Path = DATA_ROOT,
             evaluation_as_of: str, code_commit_sha: str | None = None) -> dict:
    packet = build(snapshot_date, raw_root=raw_root, evaluation_as_of=evaluation_as_of, code_commit_sha=code_commit_sha)
    target = output_path(snapshot_date, data_root)
    if target.exists():
        if target.is_symlink():
            raise BoundedIdentityRegistryBuildError(f"EXISTING_PACKET_INVALID:{target}")
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BoundedIdentityRegistryBuildError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        existing_hash = existing.get("payload_sha256")
        if not isinstance(existing_hash, str) or len(existing_hash) != 64:
            raise BoundedIdentityRegistryBuildError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}:missing_or_malformed")
        recomputed = HARNESS.payload_sha256({k: v for k, v in existing.items() if k != "payload_sha256"})
        if recomputed != existing_hash:
            raise BoundedIdentityRegistryBuildError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}:self_hash_mismatch")
        existing_without_commit = {k: v for k, v in existing.items() if k not in ("code_commit_sha", "payload_sha256")}
        packet_without_commit = {k: v for k, v in packet.items() if k not in ("code_commit_sha", "payload_sha256")}
        if existing_without_commit != packet_without_commit:
            raise BoundedIdentityRegistryBuildError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
        return {
            "outcome": "verified_existing", "path": str(target),
            "payload_sha256": existing["payload_sha256"], "code_commit_sha": existing["code_commit_sha"],
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    try:
        temp.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated", "path": str(target),
        "payload_sha256": packet["payload_sha256"], "code_commit_sha": packet["code_commit_sha"],
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key in ("outcome", "path", "payload_sha256", "code_commit_sha"):
            handle.write(f"{key}={result.get(key, '')}\n")


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_date")
    parser.add_argument("--evaluation-as-of", required=True)
    parser.add_argument("--raw-root", type=Path, default=HARNESS.RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(
            args.snapshot_date, raw_root=args.raw_root, data_root=args.data_root,
            evaluation_as_of=args.evaluation_as_of,
        )
    except BoundedIdentityRegistryBuildError as exc:
        _write_github_output({"outcome": "failed", "path": "", "payload_sha256": "", "code_commit_sha": ""})
        print(f"P3-12-ID-01 bounded identity registry build failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P3-12-ID-01 bounded identity registry build {result['outcome']} date={args.snapshot_date} "
        f"path={result['path']} sha256={result['payload_sha256']} commit={result['code_commit_sha']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
