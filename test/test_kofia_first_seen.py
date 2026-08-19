#!/usr/bin/env python3
"""P1-KR-03 append-only KOFIA first-seen capture regression."""

import copy
import gzip
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "kofia_first_seen.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p1-kr03-kofia-first-seen.yml"
SPEC = importlib.util.spec_from_file_location("kofia_first_seen", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
SOURCE_CONTRACT = MODULE.liquidity.load_contract()
CAPTURE_CONTRACT = MODULE.load_capture_contract()
OPERATIONS = {item["name"]: item for item in SOURCE_CONTRACT["operations"]}
TOKEN = "SECRET-DATA-GO-KR-TOKEN-NEVER-PERSIST"
with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)


def investor_row(day, value=100710332):
    return {
        "onbdDrvPrdTrRcAdvAmt": 100,
        "toCstRpchCndBndSlgBal": 200,
        "brkTrdUcolMny": 300,
        "brkTrdUcolMnyVsOppsTrdAmt": 400,
        "ucolMnyVsOppsTrdRlImpt": 5.25,
        "basDt": day,
        "invrDpsgAmt": value,
    }


def credit_row(day, value=30867510):
    return {
        "basDt": day,
        "crdTrFingWhl": value,
        "crdTrFingScrs": 17000000,
        "crdTrFingKosdaq": 13867510,
        "crdTrLndrWhl": 1000,
        "crdTrLndrScrs": 600,
        "crdTrLndrKosdaq": 400,
        "sbscCapLn": 2000,
        "dpsgScrtMogFing": 3000,
    }


def api_response(rows, page_no=1, page_size=10, total_count=None, code="00"):
    total_count = len(rows) if total_count is None else total_count
    items = {"item": rows} if rows else {}
    return json.dumps(
        {
            "response": {
                "header": {"resultCode": code, "resultMsg": "NORMAL SERVICE."},
                "body": {
                    "numOfRows": page_size,
                    "pageNo": page_no,
                    "totalCount": total_count,
                    "items": items,
                },
            }
        },
        separators=(",", ":"),
    ).encode()


class FakeResponse:
    def __init__(self, raw, status=200):
        self.raw = raw
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self.raw

    def getcode(self):
        return self.status


class FakeOpener:
    def __init__(self, exact=None, full=None, result_code="00"):
        self.exact = exact or {}
        self.full = full or {}
        self.result_code = result_code
        self.requests = []

    def __call__(self, request, timeout=60):
        self.requests.append(request)
        parsed = urlparse(request.full_url)
        query = parse_qs(parsed.query)
        name = next(
            name
            for name, operation in OPERATIONS.items()
            if parsed.path.endswith(operation["path"])
        )
        page_no = int(query["pageNo"][0])
        page_size = int(query["numOfRows"][0])
        query_date = query.get("basDt", [None])[0]
        if query_date is not None:
            rows = copy.deepcopy(self.exact.get((name, query_date), []))
            raw = api_response(
                rows,
                page_no=page_no,
                page_size=page_size,
                code=self.result_code,
            )
        else:
            pages = self.full[name]
            rows = copy.deepcopy(pages[page_no - 1])
            total = sum(len(page) for page in pages)
            raw = api_response(
                rows,
                page_no=page_no,
                page_size=page_size,
                total_count=total,
                code=self.result_code,
            )
        return FakeResponse(raw)


def test_capture_contract():
    contract = copy.deepcopy(CAPTURE_CONTRACT)
    contract["probe_lookback_calendar_days"] = 3
    contract["probe_page_size"] = 3
    contract["full_coverage_page_size"] = 2
    return contract


def exact_rows():
    return {
        ("investor_deposits", "20260818"): [investor_row("20260818")],
        ("investor_deposits", "20260817"): [investor_row("20260817", 99)],
        ("credit_financing", "20260818"): [credit_row("20260818")],
        ("credit_financing", "20260817"): [credit_row("20260817", 77)],
    }


class KofiaFirstSeenTest(unittest.TestCase):
    def test_contract_keeps_first_seen_and_decision_authority_separate(self):
        self.assertEqual(
            CAPTURE_CONTRACT["source_release_time_status"],
            "atlas_first_seen_sequence_collecting",
        )
        self.assertIsNone(CAPTURE_CONTRACT["available_at"])
        for key in (
            "decision_eligible",
            "regime_score_authorized",
            "production_wiring_authorized",
            "trading_action_authorized",
        ):
            self.assertFalse(CAPTURE_CONTRACT[key])

    def test_request_uses_official_contract_without_exposing_key_in_summary(self):
        operation = OPERATIONS["investor_deposits"]
        request = MODULE.build_request(
            TOKEN,
            operation,
            SOURCE_CONTRACT,
            1,
            10,
            "20260818",
        )
        summary = MODULE.inspect_request(request)
        self.assertEqual(summary["scheme"], "https")
        self.assertEqual(summary["host"], "apis.data.go.kr")
        self.assertTrue(summary["service_key_present"])
        self.assertEqual(
            summary["query_keys"],
            ["basDt", "numOfRows", "pageNo", "resultType", "serviceKey"],
        )
        self.assertNotIn(TOKEN, repr(summary))
        with self.assertRaisesRegex(MODULE.CaptureError, "SERVICE_KEY_ECHOED"):
            MODULE.reject_echoed_service_key(
                json.dumps({"debug": TOKEN}).encode(), TOKEN
            )

    def test_first_capture_preserves_raw_and_marks_missing_dates(self):
        opener = FakeOpener(exact=exact_rows())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "staging" / "run-1-attempt-1"
            prior = root / "evidence"
            MODULE.capture(
                "first_seen",
                output,
                "2026-08-19T01:00:00Z",
                "1",
                "1",
                TOKEN,
                prior,
                opener=opener,
                capture_contract=test_capture_contract(),
                source_contract=SOURCE_CONTRACT,
            )
            observation = MODULE.validate_capture(
                output,
                prior,
                capture_contract=test_capture_contract(),
                source_contract=SOURCE_CONTRACT,
            )
            self.assertEqual(len(opener.requests), 6)
            deposits = observation["operations"]["investor_deposits"]
            self.assertEqual(deposits["latest_observation_date"], "2026-08-18")
            self.assertEqual(deposits["missing_query_dates"], ["2026-08-19"])
            self.assertEqual(
                deposits["observed_rows"][-1]["atlas_first_seen_at_utc"],
                "2026-08-19T01:00:00Z",
            )
            self.assertIsNone(observation["available_at"])
            self.assertFalse(observation["decision_eligible"])
            serialized = (
                (output / "_manifest.json").read_text()
                + (output / "_observation.json").read_text()
            )
            self.assertNotIn(TOKEN, serialized)
            self.assertNotIn("serviceKey", serialized)

    def test_later_capture_preserves_earliest_atlas_first_seen(self):
        contract = test_capture_contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            evidence = root / "evidence"
            first_staging = root / "first"
            MODULE.capture(
                "first_seen",
                first_staging,
                "2026-08-19T01:00:00Z",
                "10",
                "1",
                TOKEN,
                evidence,
                opener=FakeOpener(exact=exact_rows()),
                capture_contract=contract,
                source_contract=SOURCE_CONTRACT,
            )
            first = evidence / "2026-08-19" / "run-10-attempt-1"
            first.parent.mkdir(parents=True)
            shutil.move(first_staging, first)

            second = root / "second"
            MODULE.capture(
                "first_seen",
                second,
                "2026-08-19T04:00:00Z",
                "11",
                "1",
                TOKEN,
                evidence,
                opener=FakeOpener(exact=exact_rows()),
                capture_contract=contract,
                source_contract=SOURCE_CONTRACT,
            )
            observation = MODULE.validate_capture(
                second,
                evidence,
                capture_contract=contract,
                source_contract=SOURCE_CONTRACT,
            )
            row = observation["operations"]["credit_financing"]["observed_rows"][-1]
            self.assertEqual(row["atlas_first_seen_at_utc"], "2026-08-19T01:00:00Z")

    def test_full_coverage_requires_every_page_and_reports_observed_range(self):
        full = {
            "investor_deposits": [
                [investor_row("20260817"), investor_row("20260818")],
                [investor_row("20260819")],
            ],
            "credit_financing": [
                [credit_row("20260817"), credit_row("20260818")],
                [credit_row("20260819")],
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "full"
            MODULE.capture(
                "full_coverage",
                output,
                "2026-08-19T10:00:00Z",
                "20",
                "1",
                TOKEN,
                root / "prior",
                opener=FakeOpener(full=full),
                capture_contract=test_capture_contract(),
                source_contract=SOURCE_CONTRACT,
            )
            observation = MODULE.validate_capture(
                output,
                root / "prior",
                capture_contract=test_capture_contract(),
                source_contract=SOURCE_CONTRACT,
            )
            deposits = observation["operations"]["investor_deposits"]
            self.assertEqual(deposits["row_count"], 3)
            self.assertEqual(deposits["earliest_observation_date"], "2026-08-17")
            self.assertEqual(deposits["latest_observation_date"], "2026-08-19")
            self.assertFalse(observation["observed_range_is_durable_source_guarantee"])
            self.assertEqual(observation["historical_range_status"], "unverified")

    def test_mismatched_date_source_error_and_tamper_fail_closed(self):
        contract = test_capture_contract()
        bad = exact_rows()
        bad[("investor_deposits", "20260818")] = [investor_row("20260817")]
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(MODULE.CaptureError, "QUERY_DATE_MISMATCH"):
                MODULE.capture(
                    "first_seen",
                    Path(tmp) / "bad",
                    "2026-08-19T01:00:00Z",
                    "30",
                    "1",
                    TOKEN,
                    Path(tmp) / "prior",
                    opener=FakeOpener(exact=bad),
                    capture_contract=contract,
                    source_contract=SOURCE_CONTRACT,
                )
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(MODULE.CaptureError, "SOURCE_RESULT_ERROR"):
                MODULE.capture(
                    "first_seen",
                    Path(tmp) / "error",
                    "2026-08-19T01:00:00Z",
                    "31",
                    "1",
                    TOKEN,
                    Path(tmp) / "prior",
                    opener=FakeOpener(exact=exact_rows(), result_code="22"),
                    capture_contract=contract,
                    source_contract=SOURCE_CONTRACT,
                )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            output = root / "capture"
            MODULE.capture(
                "first_seen",
                output,
                "2026-08-19T01:00:00Z",
                "32",
                "1",
                TOKEN,
                root / "prior",
                opener=FakeOpener(exact=exact_rows()),
                capture_contract=contract,
                source_contract=SOURCE_CONTRACT,
            )
            raw_path = next((output / "raw").glob("**/*.json.gz"))
            with gzip.open(raw_path, "rb") as stream:
                raw = stream.read()
            with gzip.GzipFile(raw_path, "wb", mtime=0) as stream:
                stream.write(raw + b" ")
            with self.assertRaisesRegex(MODULE.CaptureError, "HASH_MISMATCH"):
                MODULE.validate_capture(
                    output,
                    root / "prior",
                    capture_contract=contract,
                    source_contract=SOURCE_CONTRACT,
                )

    def test_full_coverage_empty_and_future_rows_fail_closed(self):
        empty = {name: [[]] for name in OPERATIONS}
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(MODULE.CaptureError, "FULL_COVERAGE_EMPTY"):
                MODULE.capture(
                    "full_coverage",
                    Path(tmp) / "empty",
                    "2026-08-19T10:00:00Z",
                    "40",
                    "1",
                    TOKEN,
                    Path(tmp) / "prior",
                    opener=FakeOpener(full=empty),
                    capture_contract=test_capture_contract(),
                    source_contract=SOURCE_CONTRACT,
                )
        future = {
            "investor_deposits": [[investor_row("20260820")]],
            "credit_financing": [[credit_row("20260820")]],
        }
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                MODULE.CaptureError, "FULL_COVERAGE_FUTURE_OBSERVATION"
            ):
                MODULE.capture(
                    "full_coverage",
                    Path(tmp) / "future",
                    "2026-08-19T10:00:00Z",
                    "41",
                    "1",
                    TOKEN,
                    Path(tmp) / "prior",
                    opener=FakeOpener(full=future),
                    capture_contract=test_capture_contract(),
                    source_contract=SOURCE_CONTRACT,
                )

    def test_workflow_has_free_secret_bound_slots_and_append_only_staging(self):
        triggers = WF.get("on", WF.get(True))
        self.assertEqual(
            {item["cron"] for item in triggers["schedule"]},
            {
                "30 21 * * *",
                "30 0 * * *",
                "30 4 * * *",
                "30 8 * * *",
                "30 12 * * *",
            },
        )
        options = triggers["workflow_dispatch"]["inputs"]["mode"]["options"]
        self.assertEqual(options, ["first_seen", "full_coverage"])
        self.assertEqual(WF["permissions"], {"contents": "write"})
        steps = WF["jobs"]["capture"]["steps"]
        capture = next(step for step in steps if step.get("name") == "Capture immutable KOFIA evidence")
        command = capture["run"]
        self.assertEqual(
            capture["env"]["DATA_GO_KR_SERVICE_KEY"],
            "${{ secrets.DATA_GO_KR_SERVICE_KEY }}",
        )
        self.assertNotIn('echo "$DATA_GO_KR_SERVICE_KEY"', command)
        self.assertIn('if [ -e "$DIR" ]', command)
        self.assertIn('mktemp -d "$RUNNER_TEMP/kofia.', command)
        self.assertLess(command.index(" validate "), command.index('mv "$STAGING" "$DIR"'))
        commit = next(step for step in steps if step.get("name") == "Commit immutable evidence")
        self.assertIn('git add "$CAPTURE_PATH"', commit["run"])


if __name__ == "__main__":
    unittest.main()
