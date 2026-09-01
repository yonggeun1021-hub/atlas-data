"""Offline CLI for Gate 2/3 aggregate receipts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .aggregate import build_aggregate, validate_aggregate, write_json


def _read(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--input", required=True)
    build.add_argument("--previous")
    build.add_argument("--output", required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--input", required=True)
    verify.add_argument("--previous")
    verify.add_argument("--aggregate", required=True)
    args = parser.parse_args()

    source = _read(args.input)
    previous = _read(args.previous) if args.previous else None
    if args.command == "build":
        receipt = build_aggregate(source, previous)
        write_json(Path(args.output), receipt)
        print(receipt["aggregate_sha256"])
        return 0
    receipt = _read(args.aggregate)
    validate_aggregate(receipt, source, previous)
    print(receipt["aggregate_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
