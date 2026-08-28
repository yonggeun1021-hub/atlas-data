#!/usr/bin/env python3
"""Fail closed unless the repository approval key matches its secret anchor.

The Ed25519 public key is intentionally public.  Its SHA-256 fingerprint is
stored out of band as a GitHub Actions secret so a repository writer cannot
replace the key and approve their own payload.  This check emits only a stable
error code on failure; it never prints the configured anchor value.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

import briefing_finalization as bf  # noqa: E402

REPOSITORY_ROOT = SCRIPT_DIR.parents[1]


def verify(repo_root: Path) -> None:
    """Validate key syntax and the out-of-band fingerprint anchor."""

    bf.load_public_key(repo_root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=REPOSITORY_ROOT)
    args = parser.parse_args(argv)
    try:
        verify(args.repo_root.resolve())
    except bf.FinalizationError as exc:
        print(f"approval public-key anchor check failed: {exc.code}", file=sys.stderr)
        return exc.exit_code
    print("approval public-key anchor verified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
