#!/usr/bin/env python3
"""P1-KR-05 shared-fetch derived output regression (Korea Breadth + P3-03)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_breadth_derived_outputs.py"
WORKFLOW = ROOT / ".github" / "workflows" / "p1-kr05-korea-breadth-live.yml"
FIXTURE_TEST = ROOT / "test" / "test_korea_breadth.py"

SPEC = importlib.util.spec_from_file_location("korea_breadth_derived_outputs", SCRIPT)
DERIVED = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(DERIVED)

FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "korea_breadth_fixtures", FIXTURE_TEST
)
FIXTURES = importlib.util.module_from_spec(FIXTURE_SPEC)
assert FIXTURE_SPEC.loader is not None
FIXTURE_SPEC.loader.exec_module(FIXTURES)

KOREA_BREADTH = DERIVED.KOREA_BREADTH
CONTRACT = KOREA_BREADTH.load_contract()
TOKEN = "SECRET-KRX-TOKEN-NEVER-PRINT"
RAW_NAME = "RAW-NAME-SENTINEL"
RAW_CODE = "RAW-CODE-SENTINEL"


def row(day, code, close, market="KOSPI", name=RAW_NAME):
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


def eight_point_matrix(kospi_prev, kospi_cur, kosdaq_prev, kosdaq_cur):
    """Payload order matching run_derived_outputs' own iteration: for each
    market in order, historical (prev, current) then recent (prev, current)."""
    return [
        payload([row("20100104", "A", kospi_prev[0], "KOSPI")]),
        payload([row("20100105", "A", kospi_prev[1], "KOSPI")]),
        payload([row("20260814", "C", kospi_cur[0], "KOSPI")]),
        payload([row("20260818", "C", kospi_cur[1], "KOSPI")]),
        payload([row("20100104", "B", kosdaq_prev[0], "KOSDAQ")]),
        payload([row("20100105", "B", kosdaq_prev[1], "KOSDAQ")]),
        payload([row("20260814", "D", kosdaq_cur[0], "KOSDAQ")]),
        payload([row("20260818", "D", kosdaq_cur[1], "KOSDAQ")]),
    ]


PAIRS = (
    ("historical", "20100104", "20100105"),
    ("recent", "20260814", "20260818"),
)


class KoreaBreadthDerivedOutputsTests(unittest.TestCase):
    def run_matrix(self, payloads, markets=("kospi", "kosdaq")):
        opener = FIXTURES.SequenceOpener(payloads)
        with tempfile.TemporaryDirectory() as tmp:
            result = DERIVED.run_derived_outputs(
                TOKEN, markets, PAIRS, Path(tmp), opener=opener, contract=CONTRACT
            )
            breadth = {
                path.name: json.loads(path.read_text(encoding="utf-8"))
                for path in result["breadth_paths"]
            }
            p3_03 = (
                json.loads(result["p3_03_path"].read_text(encoding="utf-8"))
                if result["p3_03_path"]
                else None
            )
        return result, breadth, p3_03, opener

    def test_shared_fetch_produces_four_breadth_packets_and_one_p3_03_packet(self):
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        result, breadth, p3_03, opener = self.run_matrix(payloads)

        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(len(opener.requests), 8)
        self.assertEqual(
            set(breadth),
            {
                "korea-breadth-historical-kospi.json",
                "korea-breadth-recent-kospi.json",
                "korea-breadth-historical-kosdaq.json",
                "korea-breadth-recent-kosdaq.json",
            },
        )
        self.assertIsNotNone(p3_03)
        self.assertEqual(p3_03["as_of_date"], "2026-08-18")
        self.assertEqual(p3_03["total_count"], 2)
        self.assertEqual(p3_03["market_counts"], {"KOSDAQ": 1, "KOSPI": 1})

    def test_breadth_packet_carries_no_raw_identity_name_or_price(self):
        # The Breadth observation packet is explicitly non-reconstructive:
        # no ISU_CD, no display name, no raw price, no auth token. (The
        # P3-03 Master packet is a different deliverable that legitimately
        # carries display names/identities -- that is its whole purpose --
        # so this check is scoped to the Breadth packets only.)
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        result, breadth, p3_03, opener = self.run_matrix(payloads)
        dump = json.dumps(breadth)
        for forbidden in (TOKEN, RAW_NAME, RAW_CODE, "123456", "response_body_base64"):
            self.assertNotIn(forbidden, dump)
        self.assertNotIn(TOKEN, json.dumps(p3_03))

    def test_breadth_packet_required_fields_and_non_decision_boundary(self):
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        result, breadth, p3_03, opener = self.run_matrix(payloads)
        recent_kospi = breadth["korea-breadth-recent-kospi.json"]

        self.assertEqual(recent_kospi["schema_version"], DERIVED.BREADTH_PACKET_SCHEMA_VERSION)
        self.assertEqual(recent_kospi["scope"], "recent")
        self.assertEqual(recent_kospi["market"], "KOSPI")
        self.assertEqual(recent_kospi["previous_date"], "20260814")
        self.assertEqual(recent_kospi["as_of_date"], "20260818")
        self.assertIsNone(recent_kospi["source_available_at"])
        self.assertEqual(recent_kospi["captured_at"], recent_kospi["fetched_at_utc"]["current"])
        self.assertEqual(recent_kospi["first_seen_at"], recent_kospi["fetched_at_utc"]["current"])
        self.assertFalse(recent_kospi["breadth_classification_authorized"])
        self.assertFalse(recent_kospi["threshold_authorized"])
        self.assertFalse(recent_kospi["regime_score_authorized"])
        self.assertFalse(recent_kospi["production_wiring_authorized"])
        self.assertFalse(recent_kospi["trading_action_authorized"])
        self.assertEqual(recent_kospi["participation"]["classification"], "UNDEFINED")
        self.assertEqual(recent_kospi["participation"]["advancing_count"], 1)
        identity = recent_kospi["request_identity"]
        self.assertEqual(set(identity), {"previous", "current"})
        for side in ("previous", "current"):
            self.assertIn("endpoint", identity[side])
            self.assertRegex(identity[side]["response_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(set(recent_kospi["fetched_at_utc"]), {"previous", "current"})
        # Self-rehash / digest binding, matching the repo-wide convention.
        digest = recent_kospi.pop("payload_sha256")
        self.assertEqual(digest, DERIVED.payload_sha256(recent_kospi))

    def test_p3_03_packet_reuses_krx_global_universe_build_packet_unchanged(self):
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        result, breadth, p3_03, opener = self.run_matrix(payloads)

        self.assertEqual(p3_03["schema_version"], DERIVED.KRX_UNIVERSE.OUTPUT_SCHEMA_VERSION)
        self.assertEqual(p3_03["status"], "SOURCE_COVERAGE_UNIVERSE_VALIDATED")
        for field in (
            "investable_universe_authorized", "stage_promotion_authorized",
            "production_authorized", "trading_authorized",
        ):
            self.assertFalse(p3_03["authority"][field])
        for policy in p3_03["policy_status"].values():
            self.assertIn(policy, ("IMPLEMENTED", "UNRATIFIED"))
        digest = p3_03.pop("payload_sha256")
        self.assertEqual(digest, DERIVED.KRX_UNIVERSE.payload_sha256(p3_03))

    def test_scope_failure_is_isolated_and_still_shares_the_fetch(self):
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        # Corrupt the recent KOSPI current-date row's BAS_DD so that one
        # scope fails validation -- the other three scopes and the P3-03
        # packet (built from KOSDAQ's still-valid current response and the
        # already-failed KOSPI one) must not be silently skipped en masse.
        payloads[3] = payload([row("20260817", "C", "11", "KOSPI")])
        opener = FIXTURES.SequenceOpener(payloads)
        with tempfile.TemporaryDirectory() as tmp:
            result = DERIVED.run_derived_outputs(
                TOKEN, ("kospi", "kosdaq"), PAIRS, Path(tmp), opener=opener, contract=CONTRACT
            )
        self.assertEqual(result["failed_count"], 2)  # recent-kospi breadth + p3_03 (needs both markets)
        names = {path.name for path in result["breadth_paths"]}
        self.assertEqual(
            names,
            {
                "korea-breadth-historical-kospi.json",
                "korea-breadth-historical-kosdaq.json",
                "korea-breadth-recent-kosdaq.json",
            },
        )
        self.assertIsNone(result["p3_03_path"])

    def test_no_second_fetch_for_the_same_market_and_date(self):
        # historical-current(20100105) and any later reuse of the same
        # market/date must come from the cache, not a second request.
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        opener = FIXTURES.SequenceOpener(payloads)
        with tempfile.TemporaryDirectory() as tmp:
            DERIVED.run_derived_outputs(
                TOKEN, ("kospi", "kosdaq"), PAIRS, Path(tmp), opener=opener, contract=CONTRACT
            )
        self.assertEqual(len(opener.requests), 8)
        self.assertEqual(len(opener.payloads), 0)

    def test_successful_run_writes_only_under_out_dir(self):
        payloads = eight_point_matrix(
            ("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")
        )
        opener = FIXTURES.SequenceOpener(payloads)
        with tempfile.TemporaryDirectory() as tmp:
            before = sorted(Path(tmp).rglob("*"))
            self.assertEqual(before, [])
            DERIVED.run_derived_outputs(
                TOKEN, ("kospi", "kosdaq"), PAIRS, Path(tmp), opener=opener, contract=CONTRACT
            )
            after = sorted(p.name for p in Path(tmp).rglob("*") if p.is_file())
        self.assertEqual(
            after,
            sorted(
                [
                    "korea-breadth-historical-kospi.json",
                    "korea-breadth-recent-kospi.json",
                    "korea-breadth-historical-kosdaq.json",
                    "korea-breadth-recent-kosdaq.json",
                    "p3-03-krx-global-universe.json",
                    DERIVED.REQUEST_RECEIPT_NAME,
                ]
            ),
        )

    def test_workflow_reuses_the_shared_fetch_script_adds_no_schedule_and_uploads_artifacts(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        with WORKFLOW.open(encoding="utf-8") as stream:
            workflow = yaml.safe_load(stream)
        triggers = workflow.get("on", workflow.get(True))
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("schedule", triggers)
        self.assertEqual(workflow["permissions"]["contents"], "read")
        self.assertIn("korea_breadth_derived_outputs.py", text)
        self.assertIn("actions/upload-artifact", text)
        self.assertIn("RUNNER_TEMP", text)
        # The append-only/no-tracked-file guard must still run last and
        # still be a plain git diff check -- artifacts never touch the
        # tracked tree.
        self.assertIn("Raw and derived artifact prohibition", text)
        self.assertIn("git diff --exit-code", text)
        prohibition_index = text.index("Raw and derived artifact prohibition")
        upload_index = text.index("actions/upload-artifact")
        self.assertLess(upload_index, prohibition_index)

    def test_module_makes_no_extra_endpoint_beyond_korea_breadth_py(self):
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("krx.co.kr", text)
        self.assertNotIn("import requests", text)


class KoreaBreadthRequestReceiptTests(unittest.TestCase):
    """Regression for the run34235447413 diagnostic loss: a failed
    market/date used to discard its own requested date, HTTP status,
    response digest and shape, and a dependent date that was never
    requested was indistinguishable from one that failed."""

    def run_matrix(self, payloads, markets=("kospi",), pairs=PAIRS, statuses=None):
        opener = FIXTURES.SequenceOpener(payloads, statuses=statuses)
        with tempfile.TemporaryDirectory() as tmp:
            result = DERIVED.run_derived_outputs(
                TOKEN, markets, pairs, Path(tmp), opener=opener, contract=CONTRACT
            )
            text = result["request_receipt_path"].read_text(encoding="utf-8")
        return result, json.loads(text), text, opener

    @staticmethod
    def by_date(receipt, bas_dd):
        return [
            entry
            for entry in receipt["requests"]
            if entry["requested_bas_dd"] == bas_dd
        ]

    def test_previous_date_failure_records_its_own_request_and_skips_the_current_date(self):
        # The exact production shape: the recent pair's previous date came
        # back with zero rows, so the current date was never requested.
        result, receipt, _, opener = self.run_matrix(
            [
                payload([row("20100104", "A", "1")]),
                payload([row("20100105", "A", "2")]),
                payload([]),
            ]
        )

        # One original request per unique market/date, no retry, and the
        # dependent current date genuinely never left the process.
        self.assertEqual(len(opener.requests), 3)
        self.assertEqual(len(receipt["requests"]), 3)
        self.assertEqual(self.by_date(receipt, "20260818"), [])

        failing = self.by_date(receipt, "20260814")[0]
        self.assertEqual(failing["market"], "KOSPI")
        self.assertEqual(failing["attempt"], "ATTEMPTED")
        self.assertEqual(failing["outcome"], "FAILED")
        self.assertEqual(failing["error_code"], "RESPONSE_ZERO_ROWS")
        self.assertEqual(failing["http_status"], 200)
        self.assertRegex(failing["response_sha256"], r"^[0-9a-f]{64}$")
        self.assertGreater(failing["response_byte_count"], 0)
        self.assertEqual(failing["response_shape"]["expected_block_row_count"], 0)
        self.assertTrue(failing["response_shape"]["expected_block_present"])
        self.assertEqual(
            failing["endpoint"], CONTRACT["market_endpoints"]["kospi"]
        )
        self.assertIsNotNone(failing["captured_at"])
        # Nothing the response does not actually show is asserted.
        self.assertEqual(failing["provider_status"], "UNKNOWN")
        self.assertIsNone(failing["source_available_at"])

        self.assertEqual(len(receipt["not_attempted"]), 1)
        skipped = receipt["not_attempted"][0]
        self.assertEqual(skipped["dependent_bas_dd"], "20260818")
        self.assertIsNone(skipped["requested_bas_dd"])
        self.assertEqual(skipped["attempt"], "NOT_ATTEMPTED")
        self.assertEqual(skipped["outcome"], "NOT_ATTEMPTED")
        self.assertEqual(skipped["not_attempted_reason"], "PREVIOUS_DATE_FAILED")
        self.assertEqual(
            skipped["blocked_by"],
            {"bas_dd": "20260814", "error_code": "RESPONSE_ZERO_ROWS"},
        )
        self.assertIsNone(skipped["http_status"])
        self.assertIsNone(skipped["response_sha256"])

        recent_scope = [s for s in receipt["scopes"] if s["scope"] == "recent"][0]
        self.assertEqual(recent_scope["status"], "FAILED")
        self.assertEqual(recent_scope["failing_stage"], "previous_date_fetch")
        self.assertEqual(recent_scope["failing_bas_dd"], "20260814")
        self.assertEqual(recent_scope["error_code"], "RESPONSE_ZERO_ROWS")
        self.assertEqual(result["failed_count"], 1)
        self.assertEqual(len(result["notices"]), 1)
        self.assertIn("status=NOT_ATTEMPTED", result["notices"][0])
        self.assertIn("dependent_bas_dd=20260818", result["notices"][0])

    def test_current_date_failure_is_attributed_to_the_current_date(self):
        # Same visible error code as the previous-date failure above, but
        # a different requested date -- the distinction the old receiptless
        # summary could not express.
        result, receipt, _, opener = self.run_matrix(
            [
                payload([row("20100104", "A", "1")]),
                payload([row("20100105", "A", "2")]),
                payload([row("20260814", "C", "10")]),
                payload([]),
            ]
        )

        self.assertEqual(len(opener.requests), 4)
        self.assertEqual(receipt["not_attempted"], [])
        self.assertEqual(self.by_date(receipt, "20260814")[0]["outcome"], "SUCCESS")

        failing = self.by_date(receipt, "20260818")[0]
        self.assertEqual(failing["attempt"], "ATTEMPTED")
        self.assertEqual(failing["error_code"], "RESPONSE_ZERO_ROWS")
        self.assertEqual(failing["response_shape"]["expected_block_row_count"], 0)

        recent_scope = [s for s in receipt["scopes"] if s["scope"] == "recent"][0]
        self.assertEqual(recent_scope["failing_stage"], "current_date_fetch")
        self.assertEqual(recent_scope["failing_bas_dd"], "20260818")
        self.assertEqual(result["failed_count"], 1)

    def test_a_failed_date_repeated_across_pairs_is_never_requested_again(self):
        # Both supplied pairs share the same previous date. The failure is
        # cached exactly like a success: no second request, no retry.
        pairs = (
            ("historical", "20100104", "20100105"),
            ("recent", "20100104", "20260818"),
        )
        result, receipt, _, opener = self.run_matrix([payload([])], pairs=pairs)

        self.assertEqual(len(opener.requests), 1)
        self.assertEqual(len(receipt["requests"]), 1)
        self.assertEqual(receipt["requests"][0]["requested_bas_dd"], "20100104")
        self.assertEqual(result["failed_count"], 2)
        self.assertEqual(
            sorted(entry["dependent_bas_dd"] for entry in receipt["not_attempted"]),
            ["20100105", "20260818"],
        )
        for entry in receipt["not_attempted"]:
            self.assertEqual(entry["blocked_by"]["bas_dd"], "20100104")

    def test_every_transport_and_decode_failure_keeps_its_own_evidence(self):
        block = CONTRACT["response_block"]
        cases = (
            ("http_error_body", FIXTURES.http_error(401, b'{"x": 1}'), None,
             "KRX_HTTP_ERROR_401", "ATTEMPTED", 401, True, True),
            ("non_200", payload([]), 204, "KRX_HTTP_ERROR_204", "ATTEMPTED",
             204, True, True),
            ("malformed_json", b"{bad-json", None, "KRX_RESPONSE_MALFORMED_JSON",
             "ATTEMPTED", 200, True, False),
            ("not_utf8", b"\xff\xfe rows", None, "KRX_RESPONSE_NOT_UTF8_JSON",
             "ATTEMPTED", 200, True, False),
            ("root_not_object", b"[]", None, "KRX_RESPONSE_ROOT_NOT_OBJECT",
             "ATTEMPTED", 200, True, True),
            ("block_missing", {"OtherBlock": []}, None, "RESPONSE_BLOCK_MISSING",
             "ATTEMPTED", 200, True, True),
            ("zero_rows", payload([]), None, "RESPONSE_ZERO_ROWS", "ATTEMPTED",
             200, True, True),
            ("date_mismatch", payload([row("20100105", "A", "1")]), None,
             "BAS_DD_MISMATCH", "ATTEMPTED", 200, True, True),
            ("network_error", FIXTURES.URLError("down %s" % TOKEN), None,
             "KRX_NETWORK_ERROR", "ATTEMPTED", None, False, False),
        )
        for name, body, status, code, attempt, http_status, has_digest, parseable in cases:
            with self.subTest(case=name):
                opener = FIXTURES.SequenceOpener(
                    [body], statuses=None if status is None else [status]
                )
                record = DERIVED.attempt_fetch(
                    TOKEN, "20100104", "kospi", opener=opener, contract=CONTRACT
                )
                receipt = record["receipt"]
                self.assertIsNone(record["snapshot"])
                self.assertEqual(len(opener.requests), 1)
                self.assertEqual(receipt["requested_bas_dd"], "20100104")
                self.assertEqual(receipt["attempt"], attempt)
                self.assertEqual(receipt["outcome"], "FAILED")
                self.assertEqual(receipt["error_code"], code)
                self.assertEqual(receipt["http_status"], http_status)
                self.assertIsNotNone(receipt["captured_at"])
                if has_digest:
                    self.assertRegex(receipt["response_sha256"], r"^[0-9a-f]{64}$")
                    self.assertEqual(
                        receipt["response_shape"]["parseable_json"], parseable
                    )
                    self.assertEqual(
                        receipt["response_shape"]["expected_block"], block
                    )
                else:
                    # No response was ever observed.
                    self.assertIsNone(receipt["response_sha256"])
                    self.assertIsNone(receipt["response_byte_count"])
                    self.assertIsNone(receipt["response_shape"])
                self.assertNotIn(TOKEN, json.dumps(receipt))

    def test_a_request_that_was_never_built_is_not_reported_as_attempted(self):
        opener = FIXTURES.SequenceOpener([])
        record = DERIVED.attempt_fetch(
            "", "20100104", "kospi", opener=opener, contract=CONTRACT
        )
        receipt = record["receipt"]
        self.assertEqual(len(opener.requests), 0)
        self.assertEqual(receipt["attempt"], "NOT_ATTEMPTED")
        self.assertEqual(receipt["not_attempted_reason"], "REQUEST_NOT_CONSTRUCTED")
        self.assertEqual(receipt["error_code"], "KRX_API_KEY_MISSING")
        self.assertIsNone(receipt["http_status"])
        self.assertIsNone(receipt["response_sha256"])

    def test_receipt_and_printed_summaries_leak_no_raw_body_key_or_price(self):
        leaky = json.dumps(
            payload([row("20260818", RAW_CODE, "123456", "KOSDAQ", RAW_NAME)])
        ).encode("utf-8")
        result, receipt, receipt_text, opener = self.run_matrix(
            [
                payload([row("20100104", "A", "1", "KOSPI")]),
                payload([row("20100105", "A", "2", "KOSPI")]),
                payload([]),
                payload([row("20100104", "B", "1", "KOSDAQ")]),
                payload([row("20100105", "B", "2", "KOSDAQ")]),
                payload([row("20260814", "D", "20", "KOSDAQ")]),
                FIXTURES.http_error(500, leaky),
            ],
            markets=("kospi", "kosdaq"),
        )

        printed = "\n".join(result["summaries"] + result["notices"])
        for forbidden in (
            TOKEN, RAW_NAME, RAW_CODE, "123456", "response_body_base64",
            "AUTH_KEY", "auth_key", "User-Agent", "basDd", "OutBlock_1\":",
        ):
            self.assertNotIn(forbidden, receipt_text)
            self.assertNotIn(forbidden, printed)
        # The bounded digest of that leaky error body is kept; the body is not.
        error_entry = self.by_date(receipt, "20260818")[0]
        self.assertEqual(error_entry["http_status"], 500)
        self.assertRegex(error_entry["response_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(error_entry["response_byte_count"], len(leaky))
        self.assertEqual(receipt["raw_persistence"], 0)

        # No unsupported cause is ever attributed to the failure.
        lowered = (receipt_text + printed).lower()
        for invented in ("publication", "entitlement", "holiday", "delay", "lag"):
            self.assertNotIn(invented, lowered)

        self.assertEqual(
            receipt["p3_03"],
            {
                "status": "FAILED",
                "as_of_date": "20260818",
                "error_code": "DEPENDENCY_UNAVAILABLE",
                "missing_markets": ["KOSPI", "KOSDAQ"],
            },
        )
        self.assertIsNone(result["p3_03_path"])
        self.assertEqual(result["failed_count"], 3)

    def test_successful_run_receipt_records_every_attempt_and_no_skips(self):
        result, receipt, receipt_text, opener = self.run_matrix(
            eight_point_matrix(("1", "2"), ("10", "11"), ("1", "2"), ("20", "19")),
            markets=("kospi", "kosdaq"),
        )

        self.assertEqual(result["failed_count"], 0)
        self.assertEqual(len(opener.requests), 8)
        self.assertEqual(len(receipt["requests"]), 8)
        self.assertEqual(receipt["not_attempted"], [])
        self.assertEqual(len(receipt["scopes"]), 4)
        for entry in receipt["requests"]:
            self.assertEqual(entry["attempt"], "ATTEMPTED")
            self.assertEqual(entry["outcome"], "SUCCESS")
            self.assertEqual(entry["http_status"], 200)
            self.assertIsNone(entry["error_code"])
            self.assertRegex(entry["response_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(entry["response_shape"]["expected_block_row_count"], 1)
            self.assertEqual(entry["provider_status"], "UNKNOWN")
            self.assertIsNone(entry["source_available_at"])
        for scope in receipt["scopes"]:
            self.assertEqual(scope["status"], "PASS")
            self.assertIsNone(scope["error_code"])
        self.assertEqual(receipt["p3_03"]["status"], "PASS")
        self.assertEqual(receipt["schema_version"], DERIVED.REQUEST_RECEIPT_SCHEMA_VERSION)
        for forbidden in (TOKEN, RAW_NAME, RAW_CODE, "response_body_base64"):
            self.assertNotIn(forbidden, receipt_text)
        digest = receipt.pop("payload_sha256")
        self.assertEqual(digest, DERIVED.payload_sha256(receipt))


if __name__ == "__main__":
    unittest.main(verbosity=2)
