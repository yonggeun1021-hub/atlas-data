#!/usr/bin/env python3
"""P3-12 Shadow Validation Harness runner: persists one CIO evidence packet.

Reads the exact, already-committed Upbit public raw snapshot for one
snapshot_date plus the current (still `PROPOSED_UNRATIFIED`) policy,
taxonomy, identity-exceptions, and Kraken-breadth-taxonomy config files, and
writes a reproducible, tamper-checked shadow evaluation packet under
``data/observations/upbit_p3_12_shadow_validation/<snapshot_date>/packet.json``.

This script never calls a network provider, never writes to any canonical
identity/taxonomy/policy config file, and never grants Universe, PAPER,
decision, action, order, Production, or Trading authority -- see
``universe/upbit_shadow_validation_harness.py`` and
``docs/upbit_shadow_validation_harness_contract.md``.
"""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = ROOT / "data" / "observations" / "upbit_p3_12_shadow_validation"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


HARNESS = _load_module("upbit_shadow_validation_harness_for_run", "universe/upbit_shadow_validation_harness.py")


def output_path(snapshot_date: str, data_root: Path = DATA_ROOT) -> Path:
    return Path(data_root) / snapshot_date / "packet.json"


def populate(snapshot_date: str, *, raw_root: Path = HARNESS.RAW_ROOT, data_root: Path = DATA_ROOT,
             code_commit_sha: str | None = None) -> dict:
    packet = HARNESS.evaluate(snapshot_date, raw_root=raw_root, code_commit_sha=code_commit_sha)
    target = output_path(snapshot_date, data_root)
    if target.exists():
        if target.is_symlink():
            raise HARNESS.ShadowValidationHarnessError(f"EXISTING_PACKET_INVALID:{target}")
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise HARNESS.ShadowValidationHarnessError(f"EXISTING_PACKET_UNREADABLE:{snapshot_date}:{exc}") from exc
        # CIO review (2026-08-29, PR #459): verify the EXISTING file's own
        # declared payload_sha256 is self-consistent BEFORE trusting it for
        # anything else. Comparing only "content minus hash fields" (below)
        # cannot catch a file whose payload_sha256 was mutated in isolation
        # (e.g. tampered to an arbitrary value) while its body stayed
        # byte-identical to what this run would produce -- that previously
        # passed silently as "verified_existing". Missing, malformed, or
        # merely mismatched all fail closed the same way.
        existing_hash = existing.get("payload_sha256")
        if not isinstance(existing_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", existing_hash):
            raise HARNESS.ShadowValidationHarnessError(
                f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}:missing_or_malformed_payload_sha256"
            )
        recomputed_existing_hash = HARNESS.payload_sha256({k: v for k, v in existing.items() if k != "payload_sha256"})
        if recomputed_existing_hash != existing_hash:
            raise HARNESS.ShadowValidationHarnessError(
                f"EXISTING_PACKET_HASH_INVALID:{snapshot_date}:self_hash_mismatch"
            )
        # code_commit_sha legitimately changes on every later re-run (new
        # commits keep landing on this fast-moving repo); every other field
        # must be byte-identical for the same snapshot_date and same
        # committed config inputs, or this is drift/tamper, not a rerun.
        existing_without_commit = {k: v for k, v in existing.items() if k not in ("code_commit_sha", "payload_sha256")}
        packet_without_commit = {k: v for k, v in packet.items() if k not in ("code_commit_sha", "payload_sha256")}
        if existing_without_commit != packet_without_commit:
            raise HARNESS.ShadowValidationHarnessError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{snapshot_date}")
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
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot_date")
    parser.add_argument("--raw-root", type=Path, default=HARNESS.RAW_ROOT)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(args.snapshot_date, raw_root=args.raw_root, data_root=args.data_root)
    except HARNESS.ShadowValidationHarnessError as exc:
        _write_github_output({"outcome": "failed", "path": "", "payload_sha256": "", "code_commit_sha": ""})
        print(f"P3-12 shadow validation harness failed: {exc}")
        return 1
    _write_github_output(result)
    print(
        f"P3-12 shadow validation harness {result['outcome']} date={args.snapshot_date} "
        f"path={result['path']} sha256={result['payload_sha256']} commit={result['code_commit_sha']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
