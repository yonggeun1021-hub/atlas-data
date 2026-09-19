#!/usr/bin/env python3
"""Offline checks for the read-only pykrx KR PAPER source candidate."""

from __future__ import annotations

import importlib.util
import csv
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / ".github/scripts/korea_market_signals_pykrx_candidate.py"
SPEC = importlib.util.spec_from_file_location("korea_market_signals_pykrx_candidate", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class PykrxCandidateIdentityTest(unittest.TestCase):
    def test_ratified_names_resolve_after_exact_pykrx_rendering(self):
        expected = {"kospi": 25, "kosdaq": 23}
        for market, count in expected.items():
            mapping = MODULE.canonical_index_name_map(market)
            self.assertEqual(len(mapping), count)
            for rendered, canonical in mapping.items():
                self.assertEqual(rendered, MODULE.pykrx_rendered_index_name(canonical))
                self.assertEqual(mapping[rendered], canonical)
        self.assertEqual(
            MODULE.canonical_index_name_map("kospi")["IT서비스"], "IT 서비스"
        )
        self.assertEqual(
            MODULE.canonical_index_name_map("kosdaq")["운송창고"], "운송·창고"
        )

    def test_partial_leadership_remains_fail_closed(self):
        leadership = {
            "coverage": {
                "KOSDAQ": {"observed_sector_count": 12, "ratified_identity_count": 23},
                "KOSPI": {"observed_sector_count": 14, "ratified_identity_count": 25},
            }
        }
        with self.assertRaisesRegex(
            MODULE.CandidateError,
            r"LEADERSHIP_COVERAGE_INCOMPLETE:KOSDAQ=12/22,KOSPI=14/24",
        ):
            MODULE.require_complete_leadership(leadership)

    def test_complete_leadership_is_accepted(self):
        MODULE.require_complete_leadership(
            {
                "coverage": {
                    "KOSDAQ": {"observed_sector_count": 22, "ratified_identity_count": 23},
                    "KOSPI": {"observed_sector_count": 24, "ratified_identity_count": 25},
                }
            }
        )

    def test_normalization_collision_fails_closed(self):
        records = [
            {"series_identity": "KOSPI::A·B"},
            {"series_identity": "KOSPI::AB"},
        ]
        with self.assertRaisesRegex(
            MODULE.CandidateError, "INDEX_NAME_NORMALIZATION_COLLISION"
        ):
            MODULE.canonical_index_name_map_from_records(records, "kospi")

    def test_candidate_rejects_frame_that_differs_from_retained_raw(self):
        class Frame:
            columns = ["종가", "등락률", "거래대금", "시가총액"]
            empty = False
            index = ["000001"]

            class Accessor:
                values = {
                    "종가": 110,
                    "등락률": 10,
                    "거래대금": 1100,
                    "시가총액": 10000,
                }

                def __getitem__(self, key):
                    _identity, column = key
                    return self.values[column]

            at = Accessor()

            def iterrows(self):
                return iter(
                    [("000001", {"종가": 110, "등락률": 10, "거래대금": 1100, "시가총액": 10000})]
                )

            def sort_index(self):
                return self

            def to_csv(self, index=True, lineterminator="\n"):
                stream = io.StringIO()
                writer = csv.writer(stream, lineterminator=lineterminator)
                writer.writerow([""] + self.columns)
                writer.writerow(["000001", 110, 10, 1100, 10000])
                return stream.getvalue()

        class Stock:
            @staticmethod
            def get_market_ohlcv_by_ticker(*_args, **_kwargs):
                return Frame()

        raw = json.dumps(
            {
                "OutBlock_1": [
                    {
                        "ISU_SRT_CD": "000001",
                        "TDD_CLSPRC": "100",
                        "FLUC_RT": "0",
                        "ACC_TRDVAL": "1000",
                        "MKTCAP": "10000",
                    }
                ]
            },
            separators=(",", ":"),
        ).encode()
        req = SimpleNamespace(
            method="POST",
            url=MODULE.CAPTURE.ENDPOINT,
            body=(
                "bld=dbms%2FMDC%2FSTAT%2Fstandard%2FMDCSTAT01501"
                "&trdDd=20260910&mktId=STK"
            ),
        )
        resp = SimpleNamespace(
            content=raw,
            headers={"Content-Type": "application/json"},
            status_code=200,
        )
        with tempfile.TemporaryDirectory() as temp:
            capture = MODULE.CAPTURE.SourceCapture(
                Path(temp),
                ("20260910", "20260911"),
                clock=lambda: "2026-09-12T21:22:10Z",
            )
            key, stored = capture.capture(req, resp)
            capture.bind_parser_input(key, stored)
            with mock.patch.object(MODULE, "pykrx_stock", return_value=Stock()):
                with self.assertRaisesRegex(MODULE.CAPTURE.CaptureError, "RAW_FRAME_MISMATCH"):
                    MODULE.stock_snapshot(
                        "20260910",
                        "kospi",
                        "2026-09-12T21:22:10Z",
                        capture,
                    )

    def test_lineage_uses_each_actual_response_receipt(self):
        packet = {
            "source": {
                "requests": {
                    family: {
                        market: {
                            "previous_fetched_at_utc": "start",
                            "current_fetched_at_utc": "start",
                        }
                        for market in ("KOSPI", "KOSDAQ")
                    }
                    for family in ("stock", "index")
                }
            }
        }
        records = []
        counter = 0
        for date in ("20260910", "20260911"):
            for family in ("stock", "index"):
                for market in ("KOSPI", "KOSDAQ"):
                    counter += 1
                    records.append(
                        {
                            "key": f"{date}:{market}:{family}",
                            "response": {
                                "received_at_utc": f"2026-09-12T21:22:{counter:02d}Z"
                            },
                        }
                    )
        MODULE.bind_receipt_times(
            packet, {"dates": ["20260910", "20260911"], "records": records}
        )
        lineage = packet["source"]["requests"]["stock"]["KOSPI"]
        self.assertEqual(lineage["previous_fetched_at_utc"], "2026-09-12T21:22:01Z")
        self.assertEqual(lineage["current_fetched_at_utc"], "2026-09-12T21:22:05Z")
        self.assertEqual(lineage["time_semantics"], "ACTUAL_RESPONSE_RECEIVED_AT_UTC")


if __name__ == "__main__":
    unittest.main()
