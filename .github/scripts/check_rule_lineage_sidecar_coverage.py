#!/usr/bin/env python3
"""Fails loudly when crypto PAPER decision rule-lineage sidecar coverage drops.

Background (2026-09-18/19 incident): the rule-lineage sidecar step in
``.github/workflows/upbit-realtime-capture.yml`` is guarded on the capture
job not having been cancelled. Once that job started exceeding its
``timeout-minutes`` budget, GitHub marked every affected run ``cancelled``
and the guard silently skipped the sidecar step (and several others) even
though the decision packet itself still committed. Coverage went from
49/49 (09-17) to 2/41 (09-18) to 0/1 (09-19) with no signal anywhere: the
workflow's own page looked normal (the schedule still fired), and the
server-side schedule dispatcher's ``cancelled_satisfies: true`` entry for
this workflow marked every cancelled run's slot satisfied, so nothing on
that side alarmed either.

This script is the alarm neither of those layers had. It reads only
already-committed evidence -- every committed crypto PAPER decision packet
under ``evidence/crypto_paper_decision/<date>/<hhmm>/<generation_id>/
packet.json`` dated ``>= --min-date``, and whether a rule-lineage sidecar
(``evidence/rule_lineage/crypto_paper_decision/<date>/<hhmm>/
<payload_sha256>/registry-*.json``) exists for it -- and exits non-zero the
moment any packet in that window lacks one. It writes nothing, changes no
packet, no sidecar and no decision.

Meant to run in ``.github/workflows/rule-lineage-crypto-paper-decision.yml``
immediately after that workflow's own
``governance/rule_lineage_producers.py crypto-decision-scan`` backfill step,
so a genuine miss (a derivation failure the scan itself could not repair --
see its ``FAILED`` status) is the only way this still fails after the scan
runs. That workflow is a separate, unpinned file: it depends on nothing
about how the capture job (whose bytes the schedule dispatcher pins) ends,
so a stretch of cancelled/overrun capture runs no longer produces a silent
lineage gap.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys

try:  # tolerate a partially-written or malformed packet without crashing
    _JSONDecodeError = json.JSONDecodeError
except AttributeError:  # pragma: no cover - defensive only
    _JSONDecodeError = ValueError


ROOT = Path(__file__).resolve().parents[2]

DATE_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PACKET_RELATIVE = "evidence/crypto_paper_decision"
LINEAGE_RELATIVE = "evidence/rule_lineage/crypto_paper_decision"
FULL = "FULL"
DROPPED = "DROPPED"


class CoverageCheckError(RuntimeError):
    """Invalid input to the coverage check (fails closed)."""


def _packet_date_dirs(root: Path, min_date: str) -> list[Path]:
    base = root / PACKET_RELATIVE
    if not base.is_dir():
        return []
    # Only directories shaped like a date are dates: the packet dedup store
    # at evidence/crypto_paper_decision/_sources is not one, and a plain
    # string compare against min_date would place it after every real date
    # (``_`` sorts after every digit).
    return sorted(
        p for p in base.iterdir()
        if p.is_dir() and DATE_DIR_RE.match(p.name) and p.name >= min_date
    )


def measure(root: Path, min_date: str) -> dict:
    if not isinstance(min_date, str) or not DATE_DIR_RE.match(min_date):
        raise CoverageCheckError(f"MIN_DATE_NOT_CANONICAL:{min_date!r}")
    root = Path(root)
    lineage_base = root / LINEAGE_RELATIVE
    per_date: dict[str, dict[str, int]] = {}
    missing: list[str] = []
    total_packets = 0
    total_covered = 0
    for date_dir in _packet_date_dirs(root, min_date):
        date = date_dir.name
        packets = sorted(date_dir.glob("*/*/packet.json"))
        covered = 0
        for packet_path in packets:
            hhmm = packet_path.parent.parent.name
            # The sidecar directory is keyed by the packet's payload_sha256
            # (governance/rule_lineage_producers.py::crypto_decision_sidecar_
            # path), not by the packet's own evidence folder name
            # (generation_id) -- the two are different values.
            try:
                payload_sha256 = json.loads(packet_path.read_text(encoding="utf-8")).get("payload_sha256")
            except (OSError, _JSONDecodeError):
                payload_sha256 = None
            has_sidecar = False
            if isinstance(payload_sha256, str) and payload_sha256:
                sidecar_dir = lineage_base / date / hhmm / payload_sha256
                has_sidecar = sidecar_dir.is_dir() and any(sidecar_dir.glob("registry-*.json"))
            if has_sidecar:
                covered += 1
            else:
                missing.append(str(packet_path.relative_to(root)))
        per_date[date] = {"packets": len(packets), "with_sidecar": covered}
        total_packets += len(packets)
        total_covered += covered
    return {
        "min_date": min_date,
        "total_packets": total_packets,
        "total_with_sidecar": total_covered,
        "missing_count": len(missing),
        "missing": missing,
        "per_date": per_date,
        "status": FULL if not missing else DROPPED,
    }


def _append(env_name: str, text: str) -> None:
    path = os.environ.get(env_name)
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--min-date", required=True, help="First evidence date to check (YYYY-MM-DD)")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args(argv)
    try:
        result = measure(args.root, args.min_date)
    except CoverageCheckError as exc:
        print(json.dumps({"status": "INVALID", "reason": str(exc)}, sort_keys=True))
        _append("GITHUB_OUTPUT", "status=INVALID\n")
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    _append(
        "GITHUB_OUTPUT",
        f"status={result['status']}\n"
        f"total_packets={result['total_packets']}\n"
        f"total_with_sidecar={result['total_with_sidecar']}\n"
        f"missing_count={result['missing_count']}\n",
    )
    per_date_lines = "\n".join(
        f"- `{date}`: {counts['with_sidecar']}/{counts['packets']}"
        for date, counts in sorted(result["per_date"].items())
    )
    _append(
        "GITHUB_STEP_SUMMARY",
        "### Crypto PAPER decision rule-lineage sidecar coverage\n\n"
        f"{per_date_lines}\n\n"
        f"- total: **{result['total_with_sidecar']}/{result['total_packets']}**\n"
        f"- status: `{result['status']}`\n",
    )
    return 0 if result["status"] == FULL else 1


if __name__ == "__main__":
    sys.exit(main())
