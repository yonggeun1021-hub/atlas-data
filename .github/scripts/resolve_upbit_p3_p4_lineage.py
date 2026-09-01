#!/usr/bin/env python3
"""Resolve P3-12 lineage for the scheduled P4-07 public evidence capture.

An unratified newest P3-12 record is an expected fail-closed WAIT state.  It
must skip provider calls without turning the GitHub Actions run red.  Every
other bridge error remains a hard failure.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from microstructure.upbit_p3_p4_bridge import (
    BridgeError,
    consume_universe_record,
    snapshot_key,
)
from universe import upbit_tradeable_universe as UNIVERSE


EXPECTED_WAIT_REASON = "UNIVERSE_RATIFICATION_NOT_EFFECTIVE"


def resolve_lineage(
    packet_path: Path,
    consumer: Callable[..., dict] = consume_universe_record,
) -> dict[str, str]:
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    declared = packet["payload_sha256"]
    try:
        lineage = consumer(packet_path, expected_record_sha256=declared)
    except BridgeError as exc:
        reason = str(exc)
        if reason != EXPECTED_WAIT_REASON:
            raise
        return {
            "ready": "false",
            "wait_reason": reason,
            "packet_path": str(packet_path),
            "packet_date": str(packet.get("snapshot_date") or ""),
        }

    return {
        "ready": "true",
        "wait_reason": "",
        "packet_path": str(packet_path),
        "packet_date": lineage["snapshot_date"],
        "record_sha256": lineage["record_payload_sha256"],
        "snapshot_key": snapshot_key(lineage),
        "market_count": str(lineage["market_count"]),
        "p4_policy_id": lineage["p4_policy"]["policy_id"],
        "p4_policy_sha256": lineage["p4_policy"]["packet_sha256"],
    }


def select_latest_packet(data_root: Path) -> dict:
    """Thin caller into the exact-code-approved P3 transition consumer."""
    try:
        entry = UNIVERSE.find_latest_population_record(
            data_root,
            not_after=dt.datetime.now(dt.timezone.utc),
        )
    except UNIVERSE.UpbitUniverseError as exc:
        raise BridgeError(f"UNIVERSE_TRANSITION_SELECTION_FAILED:{exc}") from exc
    if entry is None:
        raise BridgeError("UNIVERSE_PACKET_MISSING")
    return entry


def write_github_outputs(values: dict[str, str], output_path: Path) -> None:
    with output_path.open("a", encoding="utf-8") as out:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError(f"multiline GitHub output forbidden: {key}")
            out.write(f"{key}={value}\n")


def transition_outputs(entry: dict | None) -> dict[str, str]:
    entry = entry or {}
    return {
        "transition_manifest_path": str(entry.get("transition_manifest_path") or ""),
        "transition_manifest_file_sha256": str(entry.get("transition_manifest_file_sha256") or ""),
        "transition_manifest_payload_sha256": str(
            entry.get("transition_manifest_payload_sha256") or ""
        ),
        "transition_source_path": str(entry.get("source_path") or ""),
        "transition_source_file_sha256": str(entry.get("source_file_sha256") or ""),
        "transition_source_payload_sha256": str(entry.get("source_payload_sha256") or ""),
    }


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet_path", type=Path, nargs="?")
    parser.add_argument("legacy_github_output", type=Path, nargs="?")
    parser.add_argument("--latest-data-root", type=Path)
    parser.add_argument("--github-output", type=Path)
    parser.add_argument("--select-only", action="store_true")
    args = parser.parse_args(argv)

    github_output = args.github_output or args.legacy_github_output
    if github_output is None:
        parser.error("github output path is required")
    if args.latest_data_root is not None:
        entry = select_latest_packet(args.latest_data_root)
        packet_path = entry["path"]
    elif args.packet_path is not None:
        entry = None
        packet_path = args.packet_path
    else:
        parser.error("packet_path or --latest-data-root is required")

    if args.select_only:
        if entry is None:
            packet = json.loads(packet_path.read_text(encoding="utf-8"))
            values = {
                "packet_path": str(packet_path),
                "packet_date": str(packet.get("snapshot_date") or ""),
            }
        else:
            values = {
                "packet_path": str(packet_path),
                "packet_date": entry["date"],
            }
    else:
        values = resolve_lineage(packet_path)
    values.update(transition_outputs(entry))
    write_github_outputs(values, github_output)
    if args.select_only:
        print(
            "P3-12 exact transition-aware lineage selected"
            f" packet_date={values['packet_date']} path={values['packet_path']}"
        )
        return 0
    if values["ready"] == "false":
        print(
            "::warning title=P4-07 expected WAIT::"
            f"Latest P3-12 universe is not ratified; public provider capture skipped "
            f"({values['packet_date']}, {values['wait_reason']})."
        )
    else:
        print(
            "P4-07 exact-hash lineage ready"
            f" packet_date={values['packet_date']} markets={values['market_count']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
