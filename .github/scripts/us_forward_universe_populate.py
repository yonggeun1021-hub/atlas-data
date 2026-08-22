#!/usr/bin/env python3
"""P3-02 US Forward Universe scheduled population wiring.

Reads the exact, already-committed P1-US-04 raw bundle for one source_date
and publishes -- or verifies, or repairs -- the corresponding source-
coverage packet built by universe/us_global_universe.py.

This module never calls a network provider and never parses the raw
directory files itself. It reuses:
  - .github/scripts/us_breadth_forward.py's production bundle validator
    (replay_archive/validate_snapshot_bundle) to revalidate the exact raw
    bytes against their true archive predecessor, exactly as the P1-US-04
    capture workflow already does; and
  - universe/us_global_universe.py's production adapter builder
    (build_packet) to turn those validated bytes into a source-coverage
    packet.

The persisted population record is a thin, deterministic wrapper around
that packet -- source_date, the raw bundle's own response SHA-256s,
builder/contract version, and a generated_at derived from the raw bundle's
own recorded fetch time (never wall-clock "now"), so rebuilding from the
same immutable raw bundle is always byte-identical. It carries no
investable-universe, Stage, Production, or trading authority: this is
source coverage only.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "evidence" / "us_breadth" / "raw"
DATA_ROOT = ROOT / "data" / "observations" / "us_global_universe"
RECORD_SCHEMA_VERSION = "us_forward_universe_population/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


US_BREADTH = _load_module(
    "us_breadth_forward_for_population", ".github/scripts/us_breadth_forward.py"
)
UGU = _load_module("us_global_universe_for_population", "universe/us_global_universe.py")


class PopulationError(ValueError):
    """Fail-closed P3-02 population wiring violation."""


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


def _raw_bundle_sha256(core: dict) -> str:
    """Deterministic digest over exactly the response SHA-256 of every
    committed source endpoint in this bundle -- ties the population record
    to the immutable raw bytes without re-hashing directory contents or
    depending on filesystem iteration order."""
    return payload_sha256(
        sorted(
            (
                {"raw_file": item["raw_file"], "response_sha256": item["response_sha256"]}
                for item in core["endpoints"]
            ),
            key=lambda item: item["raw_file"],
        )
    )


def _build_universe_input(
    source_date: str, core: dict, snapshot_dir: Path, universe_contract: dict, source_contract: dict
) -> dict:
    definitions = {source["name"]: source for source in source_contract["sources"]}
    snapshots = []
    for endpoint in core["endpoints"]:
        source_name = endpoint["name"]
        source = definitions[source_name]
        body = US_BREADTH.read_raw(snapshot_dir, source["raw_file"])
        snapshots.append(
            {
                "source_name": source_name,
                "response_body_base64": base64.b64encode(body).decode("ascii"),
                "source_identity": {
                    "source_id": universe_contract["source_id"],
                    "source_url": source["endpoint"],
                    "source_sha256": endpoint["response_sha256"],
                    "available_at": source_date,
                    "retrieved_at_utc": core["fetched_at_utc"],
                },
            }
        )
    return {
        "schema_version": UGU.INPUT_SCHEMA_VERSION,
        "master_id": f"US.NASDAQ.DIRECTORY:{source_date}",
        "as_of_date": source_date,
        "as_of_utc": core["fetched_at_utc"],
        "snapshots": snapshots,
    }


def rebuild(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    breadth_contract: dict | None = None,
    universe_contract: dict | None = None,
) -> dict:
    """Independently rebuild the population record for source_date purely
    from the committed raw bundle archive. Fails closed (propagating
    US_BREADTH.ContractError or UGU.UsUniverseError) on a missing,
    partial, or tampered raw bundle anywhere in the archive up to and
    including source_date."""
    breadth_contract = (
        US_BREADTH.load_contract() if breadth_contract is None else breadth_contract
    )
    universe_contract = (
        UGU.load_contract() if universe_contract is None else universe_contract
    )
    source_contract = UGU._load_source_contract(universe_contract)

    snapshot_dir = bundle_dir(source_date, raw_root)
    if not snapshot_dir.is_dir():
        raise PopulationError(f"RAW_BUNDLE_MISSING:{source_date}")

    # Revalidate the true archive chain -- never a caller-supplied
    # predecessor -- exactly as the P1-US-04 capture workflow's own
    # replay-archive step already does.
    chain = US_BREADTH.replay_archive(raw_root, breadth_contract)
    core = next((item for item in chain if item["snapshot_date"] == source_date), None)
    if core is None:
        raise PopulationError(f"RAW_BUNDLE_NOT_IN_VALIDATED_ARCHIVE:{source_date}")

    universe_input = _build_universe_input(
        source_date, core, snapshot_dir, universe_contract, source_contract
    )
    universe_packet = UGU.build_packet(universe_input, universe_contract)

    record = {
        "schema_version": RECORD_SCHEMA_VERSION,
        "source_date": source_date,
        "generated_at": core["fetched_at_utc"],
        "raw_bundle": {
            "path": f"evidence/us_breadth/raw/{source_date}",
            "sha256": _raw_bundle_sha256(core),
        },
        "builder": {
            "module": "universe/us_global_universe.py",
            "contract_version": universe_packet["contract_version"],
        },
        "source_contract": {
            "module": ".github/scripts/us_breadth_forward.py",
            "schema_version": breadth_contract["schema_version"],
            "approval_status": breadth_contract["approval_status"],
        },
        "authority": {
            "source_coverage_population_only": True,
            "investable_universe_authorized": False,
            "stage_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
        "packet": universe_packet,
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def populate(
    source_date: str,
    raw_root: Path = RAW_ROOT,
    data_root: Path = DATA_ROOT,
    breadth_contract: dict | None = None,
    universe_contract: dict | None = None,
) -> dict:
    """Publish, verify, or repair the P3-02 population record for
    source_date. Never calls a network provider -- every input comes from
    the already-committed raw bundle archive.

    Returns {"outcome": "populated" | "verified_existing", "path": str,
    "payload_sha256": str}. Raises PopulationError (or the propagated
    US_BREADTH/UGU error) fail-closed on a missing/partial/tampered raw
    bundle, or on an existing packet that no longer matches a fresh
    rebuild from that immutable raw bundle (self-rehash tamper or drift).
    """
    record = rebuild(source_date, raw_root, breadth_contract, universe_contract)
    target = output_path(source_date, data_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(f"EXISTING_PACKET_UNREADABLE:{source_date}:{exc}") from exc
        if existing != record:
            raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{source_date}")
        return {
            "outcome": "verified_existing",
            "path": str(target),
            "payload_sha256": record["payload_sha256"],
        }
    US_BREADTH.write_json_append_only(record, target)
    return {
        "outcome": "populated",
        "path": str(target),
        "payload_sha256": record["payload_sha256"],
    }


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_date")
    parser.add_argument("--raw-root", type=Path, default=RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.source_date, args.raw_root, args.data_root)
    except (PopulationError, US_BREADTH.ContractError, UGU.UsUniverseError) as exc:
        print(f"P3-02 forward universe population failed: {exc}")
        return 1
    print(
        f"P3-02 forward universe population {result['outcome']}"
        f" date={args.source_date} path={result['path']}"
        f" sha256={result['payload_sha256']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
