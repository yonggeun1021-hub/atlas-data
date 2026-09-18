#!/usr/bin/env python3
"""Fail loud, not silent, when a capture collector reports evidence that
never made it into the commit about to be pushed.

Background (2026-09-17/18 investigation of an apparent FRED DEXKOUS FX
evidence "loss"): the actual mechanism turned out to be a log-reading
false alarm -- the JSON block that looked like a lost 2026-09-15
observation was printed by test/test_fred_dexkous_fx.py's own offline
end-to-end test (MainBoundedCaptureTests), which calls the collector's
main() against an isolated tempfile.TemporaryDirectory(), never the real
checkout; its fixture just happens to hard-code the date string
"2026-09-15". The real capture step's own summary that same run correctly
reported zero new observations, because FRED's published DEXKOUS series
had not advanced past 2026-09-11 in any of the real fetches inspected
(evidence/fred_dexkous_fx/raw/2026-09-15/.../manifest.json through
.../2026-09-17/.../manifest.json all show
observation_date_range: ["...", "2026-09-11"]). No commit ever dropped a
file that was actually written.

That said, the underlying failure mode this guards against is real and
general: a collector's publish_capture() can report a path as newly
written while, for any reason (a bug in the git add path list, a
concurrent run's rebase discarding staged files, a collector writing
outside ROOT), that path is not actually staged for the commit about to be
pushed. Today that would push (or no-op) successfully and report green --
silent loss. This script makes that impossible to miss: it reads the
collector's own JSON summary (as captured by the workflow via `tee`) and
fails the run if any path the collector says is new is not currently
staged in git.

Usage (see .github/workflows/fred-dexkous-fx.yml):
    python3 collectors/verify_evidence_staged.py <summary.json> [--field NAME ...]

Exit code 0: every reported path is staged (or there was nothing to check).
Exit code 1: at least one reported path is missing from the staged diff --
the run must be treated as failed.

A second, optional shape (--written-dirs-field) covers a producer whose
summary reports an owning DIRECTORY plus a wrote-something boolean per unit
of work, rather than a flat list of exact new file paths -- e.g.
.github/scripts/population_symbol_observation_daily.py's per-market
records (`output_dir` + `wrote_anything`), used by
population-symbol-observation-daily.yml. This does NOT reshape that
producer's own JSON: its summary is read as-is; only this checker gained a
second reporting shape it understands, using the fixed filenames the
persist-packet writer in decision/population_symbol_observation.py is
already known to write under each output_dir. See that workflow's commit
step for the exact invocation.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def staged_paths() -> set[str]:
    result = subprocess.run(
        ["git", "diff", "--staged", "--name-only"],
        capture_output=True, text=True, check=True,
    )
    return set(result.stdout.splitlines())


def reported_new_paths(summary: dict, fields: list[str]) -> list[str]:
    paths: list[str] = []
    for field in fields:
        value = summary.get(field, [])
        if isinstance(value, list):
            paths.extend(str(p) for p in value)
    return paths


def reported_dir_paths(
    summary: dict, field: str, dir_key: str, wrote_key: str, filenames: list[str],
) -> list[str]:
    """Derive expected new-file paths from a list of {dir_key: ..., wrote_key:
    bool} records instead of a flat list of exact paths. Every filename in
    ``filenames`` is required directly under a record's directory whenever
    that record's ``wrote_key`` is true; a record missing the directory key,
    or not a dict, is skipped rather than guessed at."""
    paths: list[str] = []
    for record in summary.get(field, []) or []:
        if not isinstance(record, dict) or not record.get(wrote_key):
            continue
        directory = record.get(dir_key)
        if not directory:
            continue
        directory = str(directory).rstrip("/")
        paths.extend(f"{directory}/{filename}" for filename in filenames)
    return paths


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("summary_path", type=Path, help="JSON summary printed by the collector.")
    parser.add_argument(
        "--field", dest="fields", action="append", default=None,
        help="Key in the summary JSON holding a list of newly-written paths "
             "(repeatable). Default: new_observation_paths, unless "
             "--written-dirs-field is given instead/in addition.",
    )
    parser.add_argument(
        "--written-dirs-field", default=None,
        help="Key in the summary JSON holding a list of {dir_key: ..., "
             "wrote_key: bool} records (see module docstring) -- an "
             "alternative to --field for a producer that reports an owning "
             "directory plus a wrote-something flag instead of exact paths.",
    )
    parser.add_argument("--dir-key", default="output_dir", help="With --written-dirs-field.")
    parser.add_argument("--wrote-key", default="wrote_anything", help="With --written-dirs-field.")
    parser.add_argument(
        "--expect-file", dest="expect_files", action="append", default=None,
        help="Filename (repeatable) required directly under each written "
             "record's directory. With --written-dirs-field.",
    )
    args = parser.parse_args(argv)
    fields = args.fields or ([] if args.written_dirs_field else ["new_observation_paths"])

    try:
        summary = json.loads(args.summary_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"EVIDENCE_GUARD_SUMMARY_UNREADABLE: {args.summary_path}: {exc}", file=sys.stderr)
        return 1

    reported = reported_new_paths(summary, fields)
    if args.written_dirs_field:
        reported += reported_dir_paths(
            summary, args.written_dirs_field, args.dir_key, args.wrote_key,
            args.expect_files or ["summary.json"],
        )
    if not reported:
        return 0

    staged = staged_paths()
    missing = [p for p in reported if p not in staged]
    if missing:
        print(
            "EVIDENCE_LOSS_DETECTED: the collector reported these paths as "
            f"newly written, but they are not staged for this commit: {missing}. "
            "Failing the run instead of pushing/succeeding silently without them.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
