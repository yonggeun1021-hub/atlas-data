#!/usr/bin/env python3
"""Read-only P4-04 live probe for the TSMC official-release adapter.

The probe proves that a GitHub-hosted runner can fetch the current official IR
page, parse it, and pass the live capture identity through the P4-04 adapter.
It deliberately does not invent a publication/availability date.  Until that
date is observed from an approved source, the resulting evidence envelopes
must remain policy-blocked and non-consumable.

The only output is an explicitly requested JSON report.  Production data,
fixtures, Rules, and briefing state are never written.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "collectors"))

from bridge import official_release_evidence as EVIDENCE  # noqa: E402
import tsmc_monthly as TSMC  # noqa: E402


SCHEMA_VERSION = "official_release_live_probe/1"
STATUS_BLOCKED = "LIVE_CAPTURE_OBSERVED_POLICY_BLOCKED"
STATUS_FAILED = "LIVE_CAPTURE_FAILED"


class LiveProbeError(RuntimeError):
    """The live response or fail-closed adapter result broke the contract."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def canonical_json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def report_sha256(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _raw_bytes(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return value.encode("utf-8")
    raise LiveProbeError("LIVE_FETCH_DID_NOT_RETURN_BYTES")


def run_probe(year: int, *, retrieved_at_utc: str | None = None, fetcher=None) -> dict:
    """Fetch and normalize one live page without granting evidence authority."""
    retrieved_at_utc = retrieved_at_utc or utc_now()
    fetcher = fetcher or TSMC.fetch_source_bytes
    raw = _raw_bytes(fetcher(year))
    if not raw:
        raise LiveProbeError("LIVE_FETCH_EMPTY")

    html = raw.decode("utf-8", errors="replace")
    parsed = TSMC.extract_from_html(html, year)
    normalized = TSMC.normalize(parsed, published_at=None)
    capture = {
        "source_url": TSMC.monthly_revenue_url(year),
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "retrieved_at_utc": retrieved_at_utc,
        "available_at": None,
        "capture_kind": "LIVE_OFFICIAL_CAPTURE",
    }
    envelopes = EVIDENCE.tsmc_monthly_envelopes(normalized, capture)
    required_blockers = {
        EVIDENCE.AVAILABLE_AT_UNOBSERVED,
        EVIDENCE.COLLECTOR_NOT_DECISION_READY,
    }
    if not envelopes:
        raise LiveProbeError("LIVE_ADAPTER_EMITTED_NO_ENVELOPES")
    for envelope in envelopes:
        if envelope.get("status") != EVIDENCE.EVIDENCE_BLOCKED:
            raise LiveProbeError("LIVE_ADAPTER_BYPASSED_POLICY_BLOCK")
        if not required_blockers.issubset(set(envelope.get("blocked_by") or [])):
            raise LiveProbeError("LIVE_ADAPTER_DROPPED_REQUIRED_BLOCKER")
        if envelope.get("consumable") is not False or envelope.get("observation") is not None:
            raise LiveProbeError("LIVE_ADAPTER_EXPOSED_BLOCKED_OBSERVATION")

    bundle = EVIDENCE.bundle(envelopes)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_BLOCKED,
        "live_fetch_succeeded": True,
        "operating_gate_closed": False,
        "year": year,
        "source_url": capture["source_url"],
        "source_sha256": capture["source_sha256"],
        "source_bytes": len(raw),
        "retrieved_at_utc": retrieved_at_utc,
        "availability_policy": {
            "status": "UNOBSERVED",
            "available_at": None,
            "reason": EVIDENCE.AVAILABLE_AT_UNOBSERVED,
            "first_seen_is_not_promoted_to_published_at": True,
        },
        "collector": {
            "collector_version": normalized.get("collector_version"),
            "published_at": normalized.get("published_at"),
            "decision_ready": normalized.get("decision_ready"),
            "decision_ready_blockers": normalized.get("decision_ready_blockers"),
            "observed_months": sorted((normalized.get("months") or {}).keys()),
        },
        "evidence_bundle": bundle,
        "authority": {
            "evidence_only": True,
            "interpretation_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    report["report_sha256"] = report_sha256(report)
    return report


def failure_report(year: int, exc: Exception, retrieved_at_utc: str) -> dict:
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_FAILED,
        "live_fetch_succeeded": False,
        "operating_gate_closed": False,
        "year": year,
        "retrieved_at_utc": retrieved_at_utc,
        "error_type": type(exc).__name__,
        "error": str(exc),
        "authority": {
            "evidence_only": True,
            "interpretation_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    report["report_sha256"] = report_sha256(report)
    return report


def write_report(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temp.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temp, path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--out", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    observed = utc_now()
    try:
        report = run_probe(args.year, retrieved_at_utc=observed)
    except Exception as exc:  # the failure artifact is part of the live contract
        report = failure_report(args.year, exc, observed)
        write_report(args.out, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    write_report(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
