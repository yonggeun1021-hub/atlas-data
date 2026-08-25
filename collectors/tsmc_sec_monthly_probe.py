#!/usr/bin/env python3
"""Read-only live probe for TSMC's primary SEC 6-K monthly-revenue path.

Atlas Rules name the SEC 6-K filed by TSMC (CIK 0001046179) as the primary
automated acquisition path.  The investor-relations web page is a human
secondary verification surface and can be blocked by its WAF.  This probe
therefore discovers the newest monthly-revenue 6-K, verifies its contents,
and parses only the approved consolidated NT$ million table.

The probe writes one explicitly requested JSON report.  It never writes
tracked data, changes Rule state, evaluates a threshold, or grants trading
authority.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from typing import Callable


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors"))

import c4_sec_edgar_check as C4  # noqa: E402


SCHEMA_VERSION = "tsmc_sec_monthly_live_probe/1"
STATUS_OBSERVED = "PRIMARY_SEC_CAPTURE_OBSERVED"
STATUS_FAILED = "PRIMARY_SEC_CAPTURE_FAILED"
TITLE_RE = re.compile(
    r"\bTSMC\s+(%s)\s+(\d{4})\s+Revenue\s+Report\b"
    % "|".join(C4.MONTHS),
    re.I,
)


class SecProbeError(RuntimeError):
    """The SEC discovery, source, or approved decision table was invalid."""


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def canonical_json(value: dict) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def report_sha256(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _body(value) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        value = value[1]
        if isinstance(value, bytes):
            return value
    raise SecProbeError("FETCHER_DID_NOT_RETURN_BYTES")


def _default_fetch(url: str) -> bytes:
    return _body(C4.get(url))


def _recent_6k_candidates(submissions: dict) -> list[dict]:
    try:
        recent = submissions["filings"]["recent"]
        forms = recent["form"]
    except (KeyError, TypeError) as exc:
        raise SecProbeError("SUBMISSIONS_SHAPE_INVALID") from exc
    if not isinstance(forms, list):
        raise SecProbeError("SUBMISSIONS_SHAPE_INVALID")

    required = (
        "filingDate",
        "accessionNumber",
        "acceptanceDateTime",
        "primaryDocument",
    )
    if any(
        not isinstance(recent.get(key), list)
        or len(recent[key]) != len(forms)
        for key in required
    ):
        raise SecProbeError("SUBMISSIONS_COLUMNS_INVALID")

    candidates = []
    for index, form in enumerate(forms):
        if form != "6-K":
            continue
        candidates.append(
            {
                "filing_date": recent["filingDate"][index],
                "accession": recent["accessionNumber"][index],
                "acceptance": recent["acceptanceDateTime"][index],
                "primary_doc": recent["primaryDocument"][index],
            }
        )
    candidates.sort(
        key=lambda item: (
            item["filing_date"],
            item["acceptance"],
            item["accession"],
        ),
        reverse=True,
    )
    return candidates


def _document_url(candidate: dict) -> str:
    accession = candidate["accession"].replace("-", "")
    return f"{C4.ARCHIVE_BASE}/{accession}/{candidate['primary_doc']}"


def _title_identity(text: str) -> tuple[str, int] | None:
    identities = {
        (match.group(1).title(), int(match.group(2)))
        for match in TITLE_RE.finditer(text)
    }
    if not identities:
        return None
    if len(identities) != 1:
        raise SecProbeError(
            "MONTHLY_REPORT_IDENTITY_AMBIGUOUS: "
            + repr(sorted(identities))
        )
    return next(iter(identities))


def _parse_identified_report(
    candidate: dict, source_url: str, raw: bytes, text: str
) -> dict:
    identity = _title_identity(text)
    if identity is None:
        raise SecProbeError("MONTHLY_REPORT_IDENTITY_MISSING")
    month_name, year = identity
    month_no = C4.month_index(month_name)
    if month_no is None:
        raise SecProbeError("MONTH_IDENTITY_INVALID")

    checks = C4.identify(text, month_name, year)
    if not all(value for _, value, _ in checks):
        failed = [label for label, value, _ in checks if not value]
        raise SecProbeError("MONTHLY_REPORT_IDENTITY_INVALID: " + repr(failed))

    unit_ok, unit_evidence = C4.verify_unit_million(text)
    if not unit_ok:
        raise SecProbeError("DECISION_TABLE_UNIT_UNVERIFIED")
    html_text = raw.decode("utf-8", errors="replace")
    parser = C4.TableCollector()
    parser.feed(html_text)
    rejected = []
    candidates = C4.final_candidates(
        parser.tables, month_name, year, rejected=rejected
    )
    chosen, problems = C4.unique_candidate(candidates)
    if chosen is None:
        raise SecProbeError(
            "DECISION_TABLE_NOT_UNIQUE: " + "; ".join(problems)
        )
    decision = chosen["bound"]

    published_at, published_evidence = C4.body_published_at(
        text, year, month_no
    )
    if published_at is None:
        raise SecProbeError("PUBLISHED_AT_UNOBSERVED")
    if published_at != candidate["filing_date"]:
        raise SecProbeError(
            "PUBLISHED_AT_SEC_FILING_DATE_CONFLICT: "
            f"{published_at} != {candidate['filing_date']}"
        )

    prose = C4.prose_layer(text, month_name, year)
    thousands = C4.thousands_layer(parser.tables, month_name, year)
    crosscheck_notes, crosscheck_differences = C4.crosscheck(
        decision, prose, thousands
    )
    return {
        "target_month": f"{year:04d}-{month_no:02d}",
        "published_at": published_at,
        "published_at_evidence": published_evidence,
        "sec_filing_date": candidate["filing_date"],
        "sec_acceptance": candidate["acceptance"],
        "accession": candidate["accession"],
        "primary_document": candidate["primary_doc"],
        "source_url": source_url,
        "source_sha256": hashlib.sha256(raw).hexdigest(),
        "source_bytes": len(raw),
        "table_locator": {
            "table_index": chosen["table_index"],
            "data_row_index": chosen["data_i"],
            "unit_evidence": unit_evidence,
        },
        "observation": {
            "monthly_revenue_ntd_mn": decision["monthly_revenue"],
            "monthly_yoy_pct_published": decision["monthly_yoy"],
            "cumulative_revenue_ntd_mn": decision["cumulative_revenue"],
            "cumulative_yoy_pct_published": decision["cumulative_yoy"],
        },
        "crosscheck": {
            "status": (
                "OBSERVED_DIFFERENCE"
                if crosscheck_differences
                else "PASS"
            ),
            "notes": crosscheck_notes,
            "differences": crosscheck_differences,
        },
    }


def parse_retained_monthly_report(manifest: dict, raw: bytes) -> dict:
    """Re-derive one already-retained P4-02 TSMC monthly-revenue report.

    Network discovery and retained-evidence consumption deliberately share the
    exact same table/title parser.  The caller must first pass the manifest and
    all retained bytes through ``sec_filing_content.validate_manifest``; this
    function then independently binds the selected primary byte stream to that
    manifest before exposing the published observations.
    """
    if not isinstance(manifest, dict) or not isinstance(raw, bytes) or not raw:
        raise SecProbeError("RETAINED_REPORT_INPUT_INVALID")
    identity = manifest.get("filing_identity")
    documents = manifest.get("documents")
    if (
        manifest.get("ticker") != "TSM"
        or manifest.get("form") != "6-K"
        or manifest.get("content_status") != "OK"
        or not isinstance(identity, dict)
        or identity.get("cik") != C4.CIK
        or not isinstance(documents, list)
    ):
        raise SecProbeError("RETAINED_REPORT_MANIFEST_IDENTITY_INVALID")
    primary = [
        row
        for row in documents
        if isinstance(row, dict) and row.get("kind") == "primary"
    ]
    if len(primary) != 1:
        raise SecProbeError("RETAINED_REPORT_PRIMARY_CARDINALITY_INVALID")
    primary = primary[0]
    if (
        len(raw) != primary.get("content_bytes")
        or hashlib.sha256(raw).hexdigest() != primary.get("content_sha256")
    ):
        raise SecProbeError("RETAINED_REPORT_PRIMARY_BYTES_MISMATCH")
    candidate = {
        "filing_date": manifest.get("filing_date"),
        "acceptance": None,
        "accession": identity.get("accession"),
        "primary_doc": primary.get("document_name"),
    }
    source_url = primary.get("source_uri")
    text = C4.strip_html(raw.decode("utf-8", errors="replace"))
    parsed = _parse_identified_report(candidate, source_url, raw, text)
    if parsed.get("published_at") != manifest.get("filing_date"):
        raise SecProbeError("RETAINED_REPORT_PUBLICATION_MANIFEST_MISMATCH")
    return parsed


def run_probe(
    *,
    retrieved_at_utc: str | None = None,
    fetcher: Callable[[str], bytes] | None = None,
    max_candidates: int = 40,
    sleeper: Callable[[float], None] = time.sleep,
) -> dict:
    """Discover and verify the newest identified TSMC monthly-revenue 6-K."""
    if not isinstance(max_candidates, int) or max_candidates < 1:
        raise SecProbeError("MAX_CANDIDATES_INVALID")
    retrieved_at_utc = retrieved_at_utc or utc_now()
    fetch = fetcher or _default_fetch
    submissions_raw = _body(fetch(C4.SUBMISSIONS_URL))
    try:
        submissions = json.loads(submissions_raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SecProbeError("SUBMISSIONS_JSON_INVALID") from exc

    candidates = _recent_6k_candidates(submissions)
    if not candidates:
        raise SecProbeError("NO_RECENT_6K_CANDIDATES")

    examined = []
    for index, candidate in enumerate(candidates[:max_candidates]):
        if index:
            sleeper(C4.POLITE_DELAY_SEC)
        source_url = _document_url(candidate)
        raw = _body(fetch(source_url))
        text = C4.strip_html(raw.decode("utf-8", errors="replace"))
        identity = _title_identity(text)
        examined.append(
            {
                "filing_date": candidate["filing_date"],
                "accession": candidate["accession"],
                "primary_document": candidate["primary_doc"],
                "monthly_revenue_identity": identity is not None,
            }
        )
        if identity is None:
            continue
        parsed = _parse_identified_report(candidate, source_url, raw, text)
        report = {
            "schema_version": SCHEMA_VERSION,
            "status": STATUS_OBSERVED,
            "primary_live_fetch_succeeded": True,
            "operating_gate_closed": False,
            "retrieved_at_utc": retrieved_at_utc,
            "source_identity": {
                "subject": "TSM",
                "issuer": "Taiwan Semiconductor Manufacturing Company Limited",
                "cik": C4.CIK,
                "form": "6-K",
                "decision_table": (
                    "TSMC {Month} Revenue Report (Consolidated)"
                ),
                "unit": "NT$ million",
            },
            "discovery": {
                "submissions_url": C4.SUBMISSIONS_URL,
                "submissions_sha256": hashlib.sha256(
                    submissions_raw
                ).hexdigest(),
                "examined_candidates": examined,
            },
            **parsed,
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
    raise SecProbeError(
        f"MONTHLY_REVENUE_6K_NOT_FOUND_IN_FIRST_{max_candidates}"
    )


def failure_report(exc: Exception, retrieved_at_utc: str) -> dict:
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": STATUS_FAILED,
        "primary_live_fetch_succeeded": False,
        "operating_gate_closed": False,
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
    temporary = path.with_name(f".{path.name}.tmp.{os.getpid()}")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--max-candidates", type=int, default=40)
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    observed = utc_now()
    try:
        report = run_probe(
            retrieved_at_utc=observed,
            max_candidates=args.max_candidates,
        )
    except Exception as exc:  # failure artifact is part of the contract
        report = failure_report(exc, observed)
        write_report(args.out, report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 1
    write_report(args.out, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
