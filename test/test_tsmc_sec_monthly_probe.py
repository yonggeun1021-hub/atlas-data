#!/usr/bin/env python3
"""TSMC primary SEC monthly-revenue live probe offline contract tests."""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PROBE_PATH = ROOT / "collectors" / "tsmc_sec_monthly_probe.py"
SPEC = importlib.util.spec_from_file_location("tsmc_sec_monthly_probe", PROBE_PATH)
PROBE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PROBE)


def monthly_doc(*, month="July", year=2026, published="August 10, 2026") -> bytes:
    """Small synthetic document with the approved table semantics, not live evidence."""
    previous_month = "June"
    headers = [
        f"{month} {year}",
        f"{previous_month} {year}",
        "M-o-M Increase (Decrease) %",
        f"{month} {year - 1}",
        "Y-o-Y Increase (Decrease) %",
        f"January to {month} {year}",
        f"January to {month} {year - 1}",
        "Y-o-Y Increase (Decrease) %",
    ]
    values = [
        "467,580", "442,680", "5.6", "323,166", "44.7",
        "2,872,064", "2,096,211", "37.0",
    ]
    head = "".join(f"<th>{value}</th>" for value in headers)
    row = "".join(f"<td>{value}</td>" for value in values)
    return f"""<html><body>
<h2>TSMC {month} {year} Revenue Report</h2>
<p>Hsinchu, Taiwan, R.O.C., {published} - TSMC today announced that
revenue for {month} {year} was approximately NT$467.58 billion and
revenue for January through {month} {year} totaled NT$2,872.06 billion.</p>
<h3>TSMC {month} Revenue Report (Consolidated):</h3>
<p>(Unit:NT$ million)</p>
<table><tr><th>Period</th>{head}</tr>
<tr><td>Net Revenue</td>{row}</tr></table>
</body></html>""".encode("utf-8")


def submissions(*rows: dict) -> bytes:
    columns = {
        "form": [],
        "filingDate": [],
        "accessionNumber": [],
        "acceptanceDateTime": [],
        "primaryDocument": [],
    }
    for row in rows:
        for key in columns:
            columns[key].append(row[key])
    return json.dumps({"filings": {"recent": columns}}).encode("utf-8")


def document_url(row: dict) -> str:
    accession = row["accessionNumber"].replace("-", "")
    return f"{PROBE.C4.ARCHIVE_BASE}/{accession}/{row['primaryDocument']}"


BOARD = {
    "form": "6-K",
    "filingDate": "2026-08-14",
    "accessionNumber": "0001046179-26-000541",
    "acceptanceDateTime": "2026-08-14T06:00:00.000Z",
    "primaryDocument": "tsm-board.htm",
}
JULY = {
    "form": "6-K",
    "filingDate": "2026-08-10",
    "accessionNumber": "0001046179-26-000471",
    "acceptanceDateTime": "2026-08-10T10:28:44.000Z",
    "primaryDocument": "tsm-revenue20260810.htm",
}
JUNE = {
    "form": "6-K",
    "filingDate": "2026-07-13",
    "accessionNumber": "0001046179-26-000401",
    "acceptanceDateTime": "2026-07-13T09:00:00.000Z",
    "primaryDocument": "tsm-revenue20260713.htm",
}


class TsmcSecMonthlyProbeTest(unittest.TestCase):
    def fake_fetcher(self, metadata: bytes, documents: dict[str, bytes]):
        def fetch(url: str) -> bytes:
            if url == PROBE.C4.SUBMISSIONS_URL:
                return metadata
            return documents[url]

        return fetch

    def test_newest_monthly_6k_is_discovered_after_newer_decoy(self):
        metadata = submissions(BOARD, JULY)
        july_url = document_url(JULY)
        july_raw = monthly_doc()
        fetcher = self.fake_fetcher(
            metadata,
            {
                document_url(BOARD): (
                    b"<html><h2>TSMC Board Meeting Resolutions</h2></html>"
                ),
                july_url: july_raw,
            },
        )
        report = PROBE.run_probe(
            retrieved_at_utc="2026-08-20T00:00:00Z",
            fetcher=fetcher,
            sleeper=lambda _: None,
        )

        self.assertEqual(report["status"], PROBE.STATUS_OBSERVED)
        self.assertTrue(report["primary_live_fetch_succeeded"])
        self.assertEqual(report["target_month"], "2026-07")
        self.assertEqual(report["published_at"], "2026-08-10")
        self.assertEqual(report["sec_acceptance"], JULY["acceptanceDateTime"])
        self.assertEqual(report["source_url"], july_url)
        self.assertEqual(report["source_sha256"], hashlib.sha256(july_raw).hexdigest())
        self.assertEqual(
            report["observation"],
            {
                "monthly_revenue_ntd_mn": "467,580",
                "monthly_yoy_pct_published": "44.7",
                "cumulative_revenue_ntd_mn": "2,872,064",
                "cumulative_yoy_pct_published": "37.0",
            },
        )
        self.assertEqual(
            [item["monthly_revenue_identity"] for item in report["discovery"]["examined_candidates"]],
            [False, True],
        )
        self.assertFalse(report["operating_gate_closed"])
        self.assertTrue(report["authority"]["evidence_only"])
        self.assertFalse(report["authority"]["rule_evaluation_authorized"])
        self.assertFalse(report["authority"]["production_authorized"])
        self.assertFalse(report["authority"]["trading_authorized"])

        expected_hash = report.pop("report_sha256")
        self.assertEqual(expected_hash, PROBE.report_sha256(report))

    def test_identified_latest_report_malformed_does_not_fall_back(self):
        malformed = monthly_doc().replace(
            b"(Unit:NT$ million)", b"(Unit:unknown)"
        )
        fetcher = self.fake_fetcher(
            submissions(JULY, JUNE),
            {
                document_url(JULY): malformed,
                document_url(JUNE): monthly_doc(
                    month="June", published="July 13, 2026"
                ),
            },
        )
        with self.assertRaisesRegex(
            PROBE.SecProbeError, "DECISION_TABLE_UNIT_UNVERIFIED"
        ):
            PROBE.run_probe(fetcher=fetcher, sleeper=lambda _: None)

    def test_failure_artifact_is_temp_only_and_returns_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "failure.json"
            with mock.patch.object(
                PROBE, "run_probe", side_effect=PROBE.SecProbeError("offline")
            ):
                with contextlib.redirect_stdout(io.StringIO()):
                    exit_code = PROBE.main(["--out", str(out)])
            self.assertEqual(exit_code, 1)
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["status"], PROBE.STATUS_FAILED)
            self.assertFalse(report["primary_live_fetch_succeeded"])
            self.assertFalse(report["operating_gate_closed"])

        self.assertFalse((ROOT / "data" / "latest_tsmc_monthly.json").exists())

    def test_invalid_submissions_columns_fail_closed(self):
        bad = json.dumps(
            {
                "filings": {
                    "recent": {
                        "form": ["6-K"],
                        "filingDate": [],
                        "accessionNumber": [],
                        "acceptanceDateTime": [],
                        "primaryDocument": [],
                    }
                }
            }
        ).encode("utf-8")
        with self.assertRaisesRegex(PROBE.SecProbeError, "SUBMISSIONS_COLUMNS_INVALID"):
            PROBE.run_probe(fetcher=lambda _: bad, sleeper=lambda _: None)


if __name__ == "__main__":
    unittest.main()
