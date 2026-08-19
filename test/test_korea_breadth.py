#!/usr/bin/env python3
"""P1-KR-05 KRX stock PIT universe and raw breadth regression."""

import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_breadth.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p1-kr05-korea-breadth-live.yml"
SPEC = importlib.util.spec_from_file_location("korea_breadth", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()
with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)
TOKEN = "SECRET-KRX-TOKEN-NEVER-PRINT"
RAW_NAME = "RAW-NAME-SENTINEL"
RAW_CODE = "RAW-CODE-SENTINEL"


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class SequenceOpener:
    def __init__(self, payloads, statuses=None):
        self.payloads = list(payloads)
        self.statuses = list(statuses or [200] * len(self.payloads))
        self.requests = []

    def __call__(self, request, timeout=30):
        self.requests.append(request)
        payload = self.payloads.pop(0)
        status = self.statuses.pop(0)
        if isinstance(payload, bytes):
            body = payload
        else:
            body = json.dumps(payload).encode("utf-8")
        return FakeResponse(body, status)


def row(day, code, close, name=RAW_NAME, market="KOSPI"):
    return {
        "BAS_DD": day,
        "ISU_CD": code,
        "ISU_NM": name,
        "MKT_NM": market,
        "SECT_TP_NM": "fixture-section",
        "TDD_CLSPRC": close,
        "CMPPREVDD_PRC": "1",
        "FLUC_RT": "1.00",
        "TDD_OPNPRC": "100",
        "TDD_HGPRC": "110",
        "TDD_LWPRC": "90",
        "ACC_TRDVOL": "1000",
        "ACC_TRDVAL": "100000",
        "MKTCAP": "1000000",
        "LIST_SHRS": "10000",
    }


def payload(rows):
    return {"OutBlock_1": rows}


def snapshot(day, market, members):
    return MODULE.validate_snapshot(
        payload([row(day, code, close) for code, close in members]),
        day,
        market,
    )


class KoreaBreadthTest(unittest.TestCase):
    def test_contract_keeps_authority_and_persistence_closed(self):
        self.assertEqual(CONTRACT["raw_persistence"], 0)
        for key in (
            "breadth_classification_authorized",
            "threshold_authorized",
            "regime_score_authorized",
            "production_wiring_authorized",
            "trading_action_authorized",
        ):
            self.assertFalse(CONTRACT[key])

    def test_request_contract_is_header_only_for_both_markets(self):
        expected = {
            "kospi": "/svc/apis/sto/stk_bydd_trd",
            "kosdaq": "/svc/apis/sto/ksq_bydd_trd",
        }
        for market, path in expected.items():
            contract = MODULE.inspect_request_contract(
                TOKEN, "20100104", market
            )
            self.assertEqual(contract["scheme"], "https")
            self.assertEqual(contract["host"], "data-dbg.krx.co.kr")
            self.assertEqual(contract["path"], path)
            self.assertEqual(contract["query_keys"], ["basDd"])
            self.assertTrue(contract["auth_header_present"])
            self.assertFalse(contract["auth_in_url"])

    def test_exact_date_universe_and_participation_are_reconstructed(self):
        previous = snapshot(
            "20100104",
            "kospi",
            (("A", "100"), ("B", "100"), ("C", "100"), ("EXIT", "10")),
        )
        current = snapshot(
            "20100105",
            "kospi",
            (("A", "110"), ("B", "90"), ("C", "100"), ("ENTER", "10")),
        )
        result = MODULE.build_observation(previous, current, "historical")

        self.assertEqual(result["universe"]["previous_count"], 4)
        self.assertEqual(result["universe"]["current_count"], 4)
        self.assertEqual(result["universe"]["shared_count"], 3)
        self.assertEqual(result["universe"]["entered_count"], 1)
        self.assertEqual(result["universe"]["exited_count"], 1)
        self.assertEqual(result["participation"]["advancing_count"], 1)
        self.assertEqual(result["participation"]["declining_count"], 1)
        self.assertEqual(result["participation"]["unchanged_count"], 1)
        self.assertEqual(result["participation"]["advance_fraction"], "0.333333333333")
        self.assertEqual(result["participation"]["classification"], "UNDEFINED")

    def test_empty_close_preserves_member_but_excludes_paired_price(self):
        previous = snapshot(
            "20100104", "kosdaq", (("A", "100"), ("B", None))
        )
        current = snapshot(
            "20100105", "kosdaq", (("A", "110"), ("B", "100"))
        )
        result = MODULE.build_observation(previous, current, "historical")
        self.assertEqual(result["universe"]["shared_count"], 2)
        self.assertEqual(result["universe"]["previous_unavailable_close_count"], 1)
        self.assertEqual(result["universe"]["paired_price_unavailable_count"], 1)
        self.assertEqual(result["participation"]["paired_count"], 1)

    def test_duplicate_identity_and_missing_schema_fail_closed(self):
        duplicate = payload(
            [row("20100104", "A", "1"), row("20100104", "A", "2")]
        )
        with self.assertRaisesRegex(MODULE.BreadthError, "ISU_CD_DUPLICATE"):
            MODULE.validate_snapshot(duplicate, "20100104", "kospi")

        missing = row("20100104", "A", "1")
        missing.pop("LIST_SHRS")
        with self.assertRaisesRegex(MODULE.BreadthError, "REQUIRED_FIELDS_MISSING"):
            MODULE.validate_snapshot(payload([missing]), "20100104", "kospi")

    def test_date_identity_and_close_errors_fail_closed(self):
        cases = (
            (payload([row("20100105", "A", "1")]), "BAS_DD_MISMATCH"),
            (payload([row("20100104", "", "1")]), "IDENTITY_FIELD_EMPTY"),
            (payload([row("20100104", "A", "not-number")]), "CLOSE_VALUE_INVALID"),
        )
        for fixture, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(MODULE.BreadthError, error):
                    MODULE.validate_snapshot(fixture, "20100104", "kospi")

    def test_zero_rows_malformed_json_and_non_200_fail_closed(self):
        with self.assertRaisesRegex(MODULE.BreadthError, "RESPONSE_ZERO_ROWS"):
            MODULE.validate_snapshot(payload([]), "20100104", "kospi")

        with self.assertRaisesRegex(MODULE.BreadthError, "MALFORMED_JSON"):
            MODULE.fetch_snapshot(
                TOKEN,
                "20100104",
                "kospi",
                opener=SequenceOpener([b"{bad-json"]),
            )

        with self.assertRaisesRegex(MODULE.BreadthError, "HTTP_ERROR_401"):
            MODULE.fetch_snapshot(
                TOKEN,
                "20100104",
                "kospi",
                opener=SequenceOpener([payload([])], statuses=[401]),
            )

    def test_zero_paired_prices_and_reversed_dates_fail_closed(self):
        previous = snapshot("20100104", "kospi", (("A", None),))
        current = snapshot("20100105", "kospi", (("A", None),))
        with self.assertRaisesRegex(MODULE.BreadthError, "PAIRED_PRICE_COVERAGE_ZERO"):
            MODULE.build_observation(previous, current, "historical")
        with self.assertRaisesRegex(MODULE.BreadthError, "DATE_PAIR_NOT_ORDERED"):
            MODULE.build_observation(current, previous, "historical")

    def test_probe_returns_no_raw_identity_name_or_price(self):
        opener = SequenceOpener(
            [
                payload([row("20100104", RAW_CODE, "123456")]),
                payload([row("20100105", RAW_CODE, "123457")]),
            ]
        )
        result = MODULE.probe_pair(
            TOKEN,
            "20100104",
            "20100105",
            "kospi",
            "historical",
            opener=opener,
        )
        summary = MODULE.format_summary(result)
        self.assertNotIn(TOKEN, repr(result) + summary)
        self.assertNotIn(RAW_NAME, repr(result) + summary)
        self.assertNotIn(RAW_CODE, repr(result) + summary)
        self.assertNotIn("123456", repr(result) + summary)
        self.assertEqual(result["raw_persistence"], 0)

    def test_successful_probe_writes_no_files(self):
        opener = SequenceOpener(
            [
                payload([row("20100104", "A", "1")]),
                payload([row("20100105", "A", "2")]),
            ]
        )
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(os.listdir(tmp))
            old = os.getcwd()
            os.chdir(tmp)
            try:
                MODULE.probe_pair(
                    TOKEN,
                    "20100104",
                    "20100105",
                    "kospi",
                    "historical",
                    opener=opener,
                )
            finally:
                os.chdir(old)
            after = sorted(os.listdir(tmp))
        self.assertEqual(before, after)

    def test_live_workflow_is_manual_non_persistent_four_point_matrix(self):
        triggers = WF.get("on", WF.get(True))
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("schedule", triggers)
        self.assertEqual(WF["permissions"]["contents"], "read")

        steps = WF["jobs"]["korea-breadth-live-proof"]["steps"]
        dependency_step = next(
            step
            for step in steps
            if step.get("name") == "Install CI contract dependencies"
        )
        self.assertIn("requirements-ci.txt", dependency_step["run"])
        proof = next(
            step
            for step in steps
            if step.get("name")
            == "P1-KR-05 historical and recent direct proof"
        )
        command = proof["run"]
        self.assertIn("--market kospi", command)
        self.assertIn("--market kosdaq", command)
        self.assertIn("--historical-previous 20100104", command)
        self.assertIn("--historical-date 20100105", command)
        self.assertIn('--recent-previous "$RECENT_PREVIOUS"', command)
        self.assertIn('--recent-date "$RECENT_DATE"', command)
        self.assertIn("KRX_API_KEY", proof["env"])
        self.assertTrue(any(step.get("run") == "git diff --exit-code" for step in steps))


if __name__ == "__main__":
    unittest.main()
