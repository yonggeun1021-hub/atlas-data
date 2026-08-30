#!/usr/bin/env python3
"""P3-12-TAX-01 runner: draft the Upbit taxonomy schema/eligible-content
candidate and (optionally) write it into ``config/upbit_exclusion_taxonomy.json``.

Never flips ``approval_status`` -- the candidate document carries the exact
``approval_status`` the input document already had (``PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY``
today). This script is meant to run inside a draft PR only; it does not open,
mark-ready, or merge any PR itself.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
TAXONOMY_PATH = ROOT / "config" / "upbit_exclusion_taxonomy.json"
DATA_ROOT = ROOT / "data" / "observations" / "upbit_taxonomy_schema_eligible_candidate"
SCHEMA_VERSION = "upbit_taxonomy_schema_eligible_candidate_packet/1"
REVIEW_STATUS = "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


UNI = _load_module("upbit_tradeable_universe_for_tax01_build", "universe/upbit_tradeable_universe.py")
IDP = _load_module("upbit_market_identity_proposal_for_tax01_build", "identity/upbit_market_identity_proposal.py")
HARNESS = _load_module("upbit_shadow_validation_harness_for_tax01_build", "universe/upbit_shadow_validation_harness.py")
TAX = _load_module("upbit_taxonomy_schema_eligible_candidate_for_build", "universe/upbit_taxonomy_schema_eligible_candidate.py")


class TaxonomyCandidateBuildError(ValueError):
    """Fail-closed P3-12-TAX-01 build/apply violation."""


def _summary(result: dict) -> dict:
    by_category: dict = {}
    for row in result["new_records"]:
        by_category[row["category"]] = by_category.get(row["category"], 0) + 1
    hold_by_reason: dict = {}
    for row in result["hold_list"]:
        hold_by_reason[row["reason"]] = hold_by_reason.get(row["reason"], 0) + 1
    return {
        "new_record_count": len(result["new_records"]),
        "new_records_by_category": dict(sorted(by_category.items())),
        "hold_count": len(result["hold_list"]),
        "hold_count_by_reason": dict(sorted(hold_by_reason.items())),
        "schema_gap_count": len(result["schema_gaps"]),
    }


def build(snapshot_date: str, *, raw_root: Path = HARNESS.RAW_ROOT, evaluation_as_of: str | None = None,
          code_commit_sha: str | None = None) -> dict:
    """Pure(ish) builder: reads already-committed inputs from disk, resolves
    the code commit SHA, and returns ``(evidence_packet, candidate_taxonomy)``.
    """
    directory = Path(raw_root) / snapshot_date
    if not directory.is_dir():
        raise TaxonomyCandidateBuildError(f"RAW_SNAPSHOT_MISSING:{snapshot_date}")
    capture_contract = UNI.UPBIT_CAPTURE.load_contract()
    try:
        core = UNI.load_snapshot_core(directory, capture_contract)
    except UNI.UPBIT_CAPTURE.CaptureError as exc:
        raise TaxonomyCandidateBuildError(f"RAW_SNAPSHOT_INVALID:{snapshot_date}:{exc}") from exc

    real_taxonomy = UNI.load_taxonomy()
    _kraken_doc, kraken_records_by_id = HARNESS.load_kraken_breadth_taxonomy()
    as_of = evaluation_as_of or snapshot_date
    proposals = HARNESS.build_identity_proposals(core, capture_contract, review_as_of=snapshot_date)
    findings = IDP.identity_review_findings(proposals, known_canonical_ids=None)
    blocked = IDP.blocked_markets(findings)

    result = TAX.build_candidate(
        core=core, capture_contract=capture_contract, real_taxonomy=real_taxonomy,
        kraken_records_by_id=kraken_records_by_id, proposals=proposals, blocked_markets=blocked,
        evaluation_as_of=as_of,
    )
    resolved_commit = code_commit_sha or HARNESS.git_commit_sha()

    packet = {
        "schema_version": SCHEMA_VERSION,
        "review_status": REVIEW_STATUS,
        "snapshot_date": snapshot_date,
        "evaluation_as_of": as_of,
        "generated_at": core["available_at"],
        "code_commit_sha": resolved_commit,
        "source": {
            "raw_snapshot_path": f"evidence/crypto/upbit/raw/{snapshot_date}",
            "raw_manifest_sha256": core["manifest_sha256"],
            "real_taxonomy_path": "config/upbit_exclusion_taxonomy.json",
            "real_taxonomy_file_sha256": HARNESS.file_sha256(UNI.TAXONOMY_PATH),
            "kraken_breadth_taxonomy_path": "config/crypto_breadth_exclusion_taxonomy.json",
            "kraken_breadth_taxonomy_file_sha256": HARNESS.file_sha256(HARNESS.KRAKEN_BREADTH_TAXONOMY_PATH),
        },
        "candidate_boundary": {
            "approval_status_changed": False,
            "approval_status": result["candidate_taxonomy"]["approval_status"],
            "generation_rule": TAX.GENERATION_RULE,
            "name_pattern_alone_never_sufficient": True,
        },
        "summary": _summary(result),
        "new_records": result["new_records"],
        "evidence": result["evidence"],
        "hold_list": result["hold_list"],
        "schema_gaps": result["schema_gaps"],
        "authority": {
            "review_only": True,
            "canonical_config_mutation_authorized_by_this_packet_alone": False,
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
    return packet, result["candidate_taxonomy"]


def write_candidate_taxonomy(candidate_taxonomy: dict, *, target: Path = TAXONOMY_PATH) -> None:
    if candidate_taxonomy.get("approval_status") is None:
        raise TaxonomyCandidateBuildError("CANDIDATE_TAXONOMY_MISSING_APPROVAL_STATUS")
    temp = target.with_name(f".{target.name}.tmp")
    payload = json.dumps(candidate_taxonomy, indent=2) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()


def output_path(snapshot_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / snapshot_date / "packet.json"


def populate(snapshot_date: str, *, raw_root: Path = HARNESS.RAW_ROOT, data_root: Path = DATA_ROOT,
             write_taxonomy: bool = True, code_commit_sha: str | None = None) -> dict:
    packet, candidate_taxonomy = build(snapshot_date, raw_root=raw_root, code_commit_sha=code_commit_sha)
    if write_taxonomy:
        write_candidate_taxonomy(candidate_taxonomy)

    target = output_path(snapshot_date, data_root)
    if target.exists():
        if target.is_symlink():
            raise TaxonomyCandidateBuildError(f"EXISTING_PACKET_INVALID:{target}")
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TaxonomyCandidateBuildError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        existing_hash = existing.get("payload_sha256")
        if not isinstance(existing_hash, str) or len(existing_hash) != 64:
            raise TaxonomyCandidateBuildError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}:missing_or_malformed")
        recomputed = HARNESS.payload_sha256({k: v for k, v in existing.items() if k != "payload_sha256"})
        if recomputed != existing_hash:
            raise TaxonomyCandidateBuildError(f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}:self_hash_mismatch")
        existing_without_commit = {k: v for k, v in existing.items() if k not in ("code_commit_sha", "payload_sha256")}
        packet_without_commit = {k: v for k, v in packet.items() if k not in ("code_commit_sha", "payload_sha256")}
        if existing_without_commit != packet_without_commit:
            raise TaxonomyCandidateBuildError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
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
    parser.add_argument("--raw-root", type=Path, default=HARNESS.RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--no-write-taxonomy", action="store_true",
                         help="Build the evidence packet only; do not touch config/upbit_exclusion_taxonomy.json")
    args = parser.parse_args(argv)
    try:
        result = populate(
            args.snapshot_date, raw_root=args.raw_root, data_root=args.data_root,
            write_taxonomy=not args.no_write_taxonomy,
        )
    except TaxonomyCandidateBuildError as exc:
        _write_github_output({"outcome": "failed", "path": "", "payload_sha256": "", "code_commit_sha": ""})
        print(f"P3-12-TAX-01 taxonomy candidate build failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P3-12-TAX-01 taxonomy candidate build {result['outcome']} date={args.snapshot_date} "
        f"path={result['path']} sha256={result['payload_sha256']} commit={result['code_commit_sha']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
