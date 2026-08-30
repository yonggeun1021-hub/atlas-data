#!/usr/bin/env python3
"""P3-12 Upbit tradeable-universe scheduled population wiring.

Reads the exact, already-committed Upbit public raw snapshot for one
snapshot_date and publishes, verifies, or repairs the corresponding
classification packet built by universe/upbit_tradeable_universe.py.

This module never calls a network provider. It builds
PROPOSED_UNRATIFIED identity proposals purely for transparency (they are
recorded in the packet's ``identity_review`` section) and passes an empty
``ratified_identity_registry`` to the classifier -- no ratified per-market
identity registry file exists in this repository. Every market therefore
lands at OBSERVATION_POOL in production. This is the expected, correct
current state, not a bug: a separate, later, human-ratified change is
required before any market can advance.
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
    packet = UNI.build_classification(
        core, evaluation_as_of=snapshot_date, policy=policy, taxonomy=taxonomy,
        ratified_identity_registry={},  # no ratified registry file exists yet -- see module docstring
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
        "identity_review": identity_review,
        "authority": {
            "observation_pool_population_only": True,
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


def populate(snapshot_date: str, raw_root: Path = RAW_ROOT, data_root: Path = DATA_ROOT) -> dict:
    record = rebuild(snapshot_date, raw_root)
    target = output_path(snapshot_date, data_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        if existing != record:
            raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
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
