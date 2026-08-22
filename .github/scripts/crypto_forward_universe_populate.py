#!/usr/bin/env python3
"""P3-04 Crypto source-coverage scheduled population wiring.

Reads the exact, already-committed P1-CR-06 raw Kraken breadth snapshot for
one source_date and publishes, verifies, or repairs the corresponding
source-coverage packet built by universe/crypto_global_universe.py.

This module never calls a network provider and never parses the raw
snapshot itself -- it reuses universe/crypto_global_universe.py's own
build_packet(), which in turn reuses the ratified P1-CR-06 breadth
selection and exclusion taxonomy exactly as scoped.

When the ratified taxonomy/breadth selection does not reach full target
coverage for this snapshot (a known, expected, ongoing operational state
-- not a bug), this module does not invent eligibility, promote any
asset, or publish a partial/empty universe. It records a deterministic
BLOCKED outcome for the caller (the P1-CR-06 workflow's own operations
telemetry) instead, and writes no packet.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "crypto_global_universe"
RECORD_SCHEMA_VERSION = "crypto_forward_universe_population/1"

# The two deterministic, expected-not-a-bug refusal codes
# crypto_global_universe.build_packet() raises when the ratified breadth
# selection/taxonomy does not (yet) reach full target coverage for this
# snapshot. Any other CryptoUniverseError is a genuine fail-closed
# violation (missing/tampered raw, contract mismatch, GAM error, ...)
# and must still propagate.
_BLOCKED_PREFIXES = (
    "BREADTH_SELECTION_UNKNOWN:",
    "BREADTH_SELECTION_FULL_COVERAGE_REQUIRED",
)


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CGU = _load_module(
    "crypto_global_universe_for_population", "universe/crypto_global_universe.py"
)


class PopulationError(ValueError):
    """Fail-closed P3-04 population wiring violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def bundle_dir(source_date: str, raw_root: Path = RAW_ROOT) -> Path:
    return Path(raw_root) / source_date


def output_dir(source_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / source_date


def output_path(source_date: str, data_root: Path = DATA_ROOT) -> Path:
    return output_dir(source_date, data_root) / "packet.json"


def _blocked_reason(exc: CGU.CryptoUniverseError) -> str | None:
    message = str(exc)
    for prefix in _BLOCKED_PREFIXES:
        if message.startswith(prefix):
            return message
    return None


def rebuild(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    contract: dict | None = None,
    universe_policy_path: Path = CGU.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CGU.TAXONOMY_PATH,
    identity_path: Path = CGU.IDENTITY_PATH,
) -> dict:
    """Independently rebuild the population record for source_date purely
    from the committed raw snapshot, reusing crypto_global_universe.py's
    own build_packet() unchanged. Returns
    {"status": "ready", "record": dict} or
    {"status": "blocked", "reason": str}. Fails closed (propagating
    PopulationError or CGU.CryptoUniverseError) on a missing raw bundle or
    any adapter violation that is not one of the two known,
    expected-not-a-bug coverage-blocked refusals."""
    snapshot_dir = bundle_dir(source_date, raw_root)
    if not snapshot_dir.is_dir():
        raise PopulationError(f"RAW_BUNDLE_MISSING:{source_date}")
    try:
        packet = CGU.build_packet(
            snapshot_dir, contract, universe_policy_path, taxonomy_path, identity_path
        )
    except CGU.CryptoUniverseError as exc:
        reason = _blocked_reason(exc)
        if reason is None:
            raise
        return {"status": "blocked", "reason": reason}

    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "source_date": source_date,
        "generated_at": packet["knowledge_as_of_utc"],
        "raw_bundle": {
            "path": f"evidence/crypto/breadth/raw/{source_date}",
            "manifest_sha256": packet["snapshot_lineage"]["manifest_sha256"],
        },
        "builder": {
            "module": "universe/crypto_global_universe.py",
            "contract_version": packet["contract_version"],
        },
        "authority": {
            "source_coverage_population_only": True,
            "investable_universe_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "packet": packet,
    }
    record["payload_sha256"] = payload_sha256(record)
    return {"status": "ready", "record": record}


def populate(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    data_root: Path = DATA_ROOT,
    contract: dict | None = None,
    universe_policy_path: Path = CGU.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = CGU.TAXONOMY_PATH,
    identity_path: Path = CGU.IDENTITY_PATH,
) -> dict:
    """Publish, verify, or repair the P3-04 population record for
    source_date -- or report a deterministic BLOCKED outcome without
    writing a packet. Never calls a network provider.

    Returns {"outcome": "populated" | "verified_existing" | "blocked",
    "reason": str | None, "path": str | None, "payload_sha256": str | None}.
    Raises PopulationError (or the propagated CGU error) fail-closed on a
    missing raw bundle, a genuine adapter violation, or an existing
    packet that no longer matches a fresh rebuild.
    """
    outcome = rebuild(
        source_date, raw_root, contract, universe_policy_path, taxonomy_path, identity_path
    )
    if outcome["status"] == "blocked":
        return {
            "outcome": "blocked",
            "reason": outcome["reason"],
            "path": None,
            "payload_sha256": None,
        }
    record = outcome["record"]
    target = output_path(source_date, data_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(
                f"EXISTING_PACKET_UNREADABLE:{source_date}:{exc}"
            ) from exc
        if existing != record:
            raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{source_date}")
        return {
            "outcome": "verified_existing",
            "reason": None,
            "path": str(target),
            "payload_sha256": record["payload_sha256"],
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
        "outcome": "populated",
        "reason": None,
        "path": str(target),
        "payload_sha256": record["payload_sha256"],
    }


def _write_github_output(result: dict) -> None:
    """Expose outcome/reason/path/sha256 to a subsequent workflow step via
    $GITHUB_OUTPUT -- the standard GitHub Actions step-output mechanism.
    No-op outside Actions (the env var is unset), so this has no effect on
    direct CLI or test usage."""
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
    parser.add_argument("source_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.source_date, args.raw_root, args.data_root)
    except (PopulationError, CGU.CryptoUniverseError) as exc:
        _write_github_output(
            {"outcome": "failed", "reason": str(exc), "path": None, "payload_sha256": None}
        )
        print(f"P3-04 crypto universe population failed: {exc}")
        return 1
    _write_github_output(result)
    if result["outcome"] == "blocked":
        print(
            f"P3-04 crypto universe population blocked"
            f" date={args.source_date} reason={result['reason']}"
        )
        return 0
    print(
        f"P3-04 crypto universe population {result['outcome']}"
        f" date={args.source_date} path={result['path']}"
        f" sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
