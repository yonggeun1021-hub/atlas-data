#!/usr/bin/env python3
"""Crypto PAPER runtime publication over retained committed evidence."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import crypto_paper_runtime as RUNTIME  # noqa: E402
from regime import crypto_paper_runtime_publication as PUBLICATION  # noqa: E402

EVALUATION_AT = "2026-09-14T08:00:00Z"


class CryptoPaperRuntimePublicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code_revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()

    def test_retained_2026_09_13_capture_rederives_axes_and_leadership_stays_missing(self):
        decision_date = dt.date(2026, 9, 13)
        record = PUBLICATION.collect_day_record(ROOT, decision_date)
        self.assertEqual(record["btc"]["trend_category"], "ABOVE_200DMA")
        self.assertEqual(record["btc"]["realized_vol_annualized_fraction"], "0.496126645795")
        self.assertEqual(record["btc"]["current_drawdown_fraction"], "-0.049352761759")
        self.assertEqual(record["stablecoin"]["observation_date"], "2026-09-13")
        pilot = record["leadership"]["windows"][RUNTIME.PILOT]
        self.assertEqual(pilot["source_unknown_reasons"], ["TAXONOMY_COVERAGE_UNKNOWN"])
        step = RUNTIME.evaluate_day(record, decision_date, False)
        self.assertEqual({axis: row["direction"] for axis, row in step["axes"].items()}, {
            "TREND": "POSITIVE", "BREADTH": "POSITIVE", "RISK_VOL": "NEUTRAL",
            "LIQUIDITY": "NEUTRAL", "LEADERSHIP": None,
        })
        self.assertFalse(step["complete"])
        self.assertEqual(step["reasons"], ["LEADERSHIP_TAXONOMY_COVERAGE_UNKNOWN"])

    def test_current_publication_is_unknown_with_closed_authority(self):
        packet = PUBLICATION.build_decision(evaluation_at=EVALUATION_AT, code_revision=self.code_revision)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["decision_status"], "BLOCKED")
        self.assertFalse(packet["runtime_decision_available"])
        self.assertEqual(packet["acceptance"]["label"], "PROVISIONAL_FORWARD_ACCEPTANCE")
        self.assertEqual(packet["acceptance"]["status"], "NOT_ACCEPTED")
        self.assertIn("PROVISIONAL_FORWARD_ACCEPTANCE_NOT_PASSED", packet["reasons"])
        self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_fixture_root_is_never_live_natural(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(PUBLICATION.evidence_class(Path(directory)), "SYNTHETIC_OFFLINE_FIXTURE")

    def test_authority_escalation_is_refused(self):
        forged = RUNTIME.evaluate_crypto_paper_runtime(
            evaluation_at=EVALUATION_AT, code_revision=self.code_revision, day_records={},
            rerun_day_records={}, evidence_class=RUNTIME.LIVE_NATURAL, kraken_receipt_raw=None)
        forged["authority"]["order_authorized"] = True
        with mock.patch.object(PUBLICATION.RUNTIME, "evaluate_crypto_paper_runtime", return_value=forged):
            with self.assertRaisesRegex(PUBLICATION.CryptoPaperRuntimePublicationError, "AUTHORITY_ESCALATION"):
                PUBLICATION.build_decision(evaluation_at=EVALUATION_AT, code_revision=self.code_revision)

    def test_published_bytes_are_stable_and_checkable(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "decision.json"
            args = ["--evaluation-at", EVALUATION_AT, "--code-revision", self.code_revision,
                    "--output", str(target)]
            with mock.patch("builtins.print"):
                self.assertEqual(PUBLICATION.main(args), 0)
            first = target.read_bytes()
            self.assertEqual(PUBLICATION.main([*args, "--check"]), 0)
            self.assertEqual(json.loads(first)["runtime_regime"], "UNKNOWN")
            target.write_bytes(first.replace(b'"UNKNOWN"', b'"RISK_ON"', 1))
            with self.assertRaisesRegex(PUBLICATION.CryptoPaperRuntimePublicationError,
                                        "PUBLISHED_DECISION_BYTES_MISMATCH"):
                PUBLICATION.main([*args, "--check"])


if __name__ == "__main__":
    unittest.main()
