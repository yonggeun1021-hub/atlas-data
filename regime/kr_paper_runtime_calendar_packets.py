#!/usr/bin/env python3
"""Derive committed KRX date-specific calendar packets for KR PAPER runtime.

The packets are an offline projection of the already committed official KRX
holiday capture through the unchanged ``build_calendar_packet``.  Nothing is
fetched, no open/closed status is guessed here, and existing packet bytes are
never overwritten with different bytes.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from market_data import krx_official_holiday_calendar as CALENDAR


CALENDAR_ROOT = "evidence/market_calendar/krx_global_holiday/2026-09-09"
CAPTURE_REF = f"{CALENDAR_ROOT}/capture-2026.json"
CAPTURE_SHA256 = "dac31a1006134c6c715f389e70f7966d1a0c0a9de2b66e8a8b322e774a144f9c"


class CalendarPacketError(ValueError):
    """Calendar packets could not be derived or do not match committed bytes."""


def packet_paths(day: dt.date) -> tuple[str, str]:
    stamp = day.isoformat()
    return (
        f"{CALENDAR_ROOT}/calendar-{stamp}.json",
        f"{CALENDAR_ROOT}/derivation-{stamp}.json",
    )


def expected_packets(start: str, end: str, root: Path = ROOT) -> dict[str, bytes]:
    first = dt.date.fromisoformat(start)
    last = dt.date.fromisoformat(end)
    if first > last:
        raise CalendarPacketError("CALENDAR_RANGE_INVALID")
    capture_raw = (root / CAPTURE_REF).read_bytes()
    if CALENDAR.digest(capture_raw) != CAPTURE_SHA256:
        raise CalendarPacketError("CALENDAR_CAPTURE_HASH_MISMATCH")
    result = {}
    day = first
    while day <= last:
        packet, receipt = CALENDAR.build_calendar_packet(
            capture_raw, CAPTURE_REF, day.isoformat()
        )
        calendar_path, derivation_path = packet_paths(day)
        result[calendar_path] = CALENDAR.canonical_bytes(packet)
        result[derivation_path] = CALENDAR.canonical_bytes(receipt)
        day += dt.timedelta(days=1)
    return result


def load_packet(day: dt.date, root: Path = ROOT) -> dict:
    """Read one committed packet after proving it is the capture projection."""
    calendar_path, _ = packet_paths(day)
    path = root / calendar_path
    if not path.is_file():
        raise CalendarPacketError(f"CALENDAR_PACKET_MISSING:{day.isoformat()}")
    raw = path.read_bytes()
    expected = expected_packets(day.isoformat(), day.isoformat(), root)[calendar_path]
    if raw != expected:
        raise CalendarPacketError(f"CALENDAR_PACKET_BYTES_MISMATCH:{day.isoformat()}")
    return json.loads(raw)


def write_packets(start: str, end: str, root: Path = ROOT) -> list[str]:
    written = []
    for relative, raw in expected_packets(start, end, root).items():
        path = root / relative
        if path.exists():
            if path.read_bytes() != raw:
                raise CalendarPacketError(f"NO_OVERWRITE_DIFFERENT_BYTES:{relative}")
            continue
        path.write_bytes(raw)
        written.append(relative)
    return written


def check_packets(start: str, end: str, root: Path = ROOT) -> None:
    for relative, raw in expected_packets(start, end, root).items():
        path = root / relative
        if not path.is_file() or path.read_bytes() != raw:
            raise CalendarPacketError(f"CALENDAR_PACKET_BYTES_MISMATCH:{relative}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.check:
        check_packets(args.start, args.end)
        print(json.dumps({"status": "CALENDAR_PACKETS_MATCH", "start": args.start, "end": args.end}))
        return 0
    written = write_packets(args.start, args.end)
    print(json.dumps({"status": "CALENDAR_PACKETS_WRITTEN", "written": len(written)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
