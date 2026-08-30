#!/usr/bin/env python3
"""Build the latest P3-12 exact-hash PAPER identity review candidate."""
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


BUILD = _load("upbit_paper_identity_hardening_candidate_cli", "identity/upbit_paper_identity_hardening_candidate.py")
FP = _load("upbit_first_party_capture_for_candidate_cli", ".github/scripts/upbit_first_party_identity_capture.py")
MARKET = _load("upbit_market_capture_for_candidate_cli", ".github/scripts/upbit_market_capture.py")


class BuildError(RuntimeError):
    pass


def _latest(root: Path, validator, timestamp_field: str) -> tuple[Path, dict]:
    candidates = []
    for manifest_path in Path(root).glob("**/_manifest.json"):
        snapshot = manifest_path.parent
        manifest = validator(snapshot)
        timestamp = manifest.get(timestamp_field)
        if not isinstance(timestamp, str):
            raise BuildError(f"LATEST_TIMESTAMP_MISSING:{snapshot}:{timestamp_field}")
        parsed = FP.parse_utc(timestamp, f"{snapshot}:{timestamp_field}")
        candidates.append((parsed, snapshot, manifest))
    if not candidates:
        raise BuildError(f"LATEST_SNAPSHOT_MISSING:{root}")
    timestamps = [row[0] for row in candidates]
    if len(timestamps) != len(set(timestamps)):
        raise BuildError(f"LATEST_TIMESTAMP_DUPLICATE:{root}")
    _, snapshot, manifest = max(candidates, key=lambda row: row[0])
    return snapshot, manifest


def find_latest_first_party(root: Path) -> tuple[Path, dict]:
    return _latest(root, FP.validate_snapshot, "available_at")


def find_latest_market(root: Path) -> tuple[Path, dict]:
    return _latest(root, MARKET.validate_snapshot, "downloaded_at_utc")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--first-party-root", type=Path, required=True)
    parser.add_argument("--market-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args(argv)
    first_party_path, first_party = find_latest_first_party(args.first_party_root)
    market_path, _ = find_latest_market(args.market_root)
    packet = BUILD.build_candidate(
        first_party_snapshot_dir=first_party_path,
        market_snapshot_dir=market_path,
    )
    target = args.output_root / packet["evaluation_as_of"] / first_party["capture_id"] / "packet.json"
    if target.exists():
        raise BuildError(f"APPEND_ONLY_VIOLATION:{target}")
    target.parent.mkdir(parents=True, exist_ok=False)
    target.write_text(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "path": str(target),
        "payload_sha256": packet["payload_sha256"],
        "proposed_registry_payload_sha256": packet["proposed_registry_payload_sha256"],
        "proposed_taxonomy_payload_sha256": packet["proposed_taxonomy_payload_sha256"],
        "consumer_file_sha256": packet["consumer_file_sha256"],
        "release_ready": packet["release_ready"],
    }, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (BuildError, BUILD.HardeningError, FP.CaptureError, MARKET.CaptureError) as exc:
        print(f"FATAL:{exc}", file=sys.stderr)
        sys.exit(1)
