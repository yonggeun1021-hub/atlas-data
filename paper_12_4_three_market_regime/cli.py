#!/usr/bin/env python3
"""Offline CLI for PAPER 12-4 receipt bundle build/verification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import tempfile

try:
    from .receipt_pipeline import (
        ReceiptPipelineError,
        build_bundle,
        read_json,
        validate_bundle,
    )
except ImportError:  # direct script execution
    from receipt_pipeline import (  # type: ignore
        ReceiptPipelineError,
        build_bundle,
        read_json,
        validate_bundle,
    )


def write_json_atomic(path: Path, value: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--envelope", action="append", type=Path, default=[])
    build.add_argument("--evaluation-time-utc", required=True)
    build.add_argument("--previous-bundle", type=Path)
    build.add_argument("--source-root", type=Path)
    build.add_argument("--output", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            previous = (
                read_json(args.previous_bundle, "PREVIOUS_BUNDLE_INVALID")
                if args.previous_bundle
                else None
            )
            kwargs = {"previous_bundle": previous}
            if args.source_root is not None:
                kwargs["source_root"] = args.source_root
            packet = build_bundle(args.envelope, args.evaluation_time_utc, **kwargs)
            write_json_atomic(args.output, packet)
            print(f"PASS_PAPER12_4_BUNDLE_BUILT:{packet['bundle_sha256']}")
            return 0
        packet = read_json(args.bundle, "BUNDLE_JSON_INVALID")
        validated = validate_bundle(packet)
        print(f"PASS_PAPER12_4_BUNDLE_VERIFIED:{validated['bundle_sha256']}")
        return 0
    except ReceiptPipelineError as exc:
        print(f"FAIL_PAPER12_4:{exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
