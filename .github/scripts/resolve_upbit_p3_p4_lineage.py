#!/usr/bin/env python3
"""Resolve P3-12 lineage for the scheduled P4-07 public evidence capture.

An unratified newest P3-12 record is an expected fail-closed WAIT state.  It
must skip provider calls without turning the GitHub Actions run red.  Every
other bridge error remains a hard failure.
"""
from __future__ import annotations

import argparse
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


def write_github_outputs(values: dict[str, str], output_path: Path) -> None:
    with output_path.open("a", encoding="utf-8") as out:
        for key, value in values.items():
            if "\n" in value or "\r" in value:
                raise ValueError(f"multiline GitHub output forbidden: {key}")
            out.write(f"{key}={value}\n")


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("packet_path", type=Path)
    parser.add_argument("github_output", type=Path)
    args = parser.parse_args(argv)

    values = resolve_lineage(args.packet_path)
    write_github_outputs(values, args.github_output)
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
