#!/usr/bin/env python3
"""Offline source-retention and fail-closed checks."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAPTURE = load("krx_information_system_capture_test", ROOT / "regime/krx_information_system_capture.py")
DEPS = load("kr_paper_source_dependency_test", ROOT / ".github/scripts/verify_kr_paper_source_dependencies.py")


def request(date, market, family):
    if family == "stock":
        body = f"bld=dbms%2FMDC%2FSTAT%2Fstandard%2FMDCSTAT01501&trdDd={date}&mktId={'STK' if market == 'KOSPI' else 'KSQ'}"
    else:
        body = f"bld=dbms%2FMDC%2FSTAT%2Fstandard%2FMDCSTAT00101&trdDd={date}&idxIndMidclssCd={'02' if market == 'KOSPI' else '03'}"
    return SimpleNamespace(method="POST", url=CAPTURE.ENDPOINT, body=body)


def raw_body(label, family):
    if family == "stock":
        value = {"OutBlock_1": [{"ISU_SRT_CD": "000001", "TDD_CLSPRC": "1,100", "FLUC_RT": "1.25", "ACC_TRDVAL": "2,000", "MKTCAP": "3,000", "ISU_ABBRV": label}]}
    else:
        value = {"output": [{"IDX_NM": label, "CLSPRC_IDX": "1,234.50"}]}
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()


class FakeResponse:
    def __init__(self, raw, status_code=200):
        self._content = raw
        self._content_consumed = True
        self.headers = {"Content-Type": "application/json;charset=UTF-8", "Set-Cookie": "must-not-retain"}
        self.status_code = status_code

    @property
    def content(self):
        return self._content


def response(label, family):
    return FakeResponse(raw_body(label, family))


class CaptureContractTest(unittest.TestCase):
    NOW = "2026-09-12T21:22:10Z"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def complete(self):
        capture = CAPTURE.SourceCapture(self.root / "capture", ("20260910", "20260911"), clock=lambda: self.NOW)
        for date in ("20260910", "20260911"):
            for market in ("KOSPI", "KOSDAQ"):
                for family in ("stock", "index"):
                    key, stored = capture.capture(request(date, market, family), response(f"{market}지수" if family == "index" else market, family), received_at=self.NOW)
                    capture.bind_parser_input(key, stored)
                    capture.bind_normalized_frame(key, "a" * 64, CAPTURE._raw_projection(stored, family))
        return capture

    def test_complete_capture_retains_eight_exact_bodies_without_secrets(self):
        manifest = self.complete().finalize()
        self.assertEqual(len(manifest["records"]), 8)
        self.assertTrue(manifest["response_body_schema_allowlisted_before_retention"])
        text = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("Set-Cookie", text)
        self.assertNotIn("must-not-retain", text)
        for row in manifest["records"]:
            raw = (self.root / "capture" / row["response"]["path"]).read_bytes()
            self.assertEqual(CAPTURE.sha256_bytes(raw), row["response"]["sha256"])
            self.assertTrue(row["normalized_frame"]["raw_to_frame_equivalent"])

    def test_krx_json_body_with_text_html_content_type_is_accepted(self):
        capture = CAPTURE.SourceCapture(
            self.root / "html-json",
            ("20260910", "20260911"),
            clock=lambda: self.NOW,
        )
        payload = json.loads(raw_body("KOSPI", "stock"))
        payload["CURRENT_DATETIME"] = "2026-09-12 22:30:35"
        value = FakeResponse(json.dumps(payload).encode())
        value.headers["Content-Type"] = "text/html;charset=UTF-8"
        key, stored = capture.capture(
            request("20260910", "KOSPI", "stock"), value
        )
        self.assertEqual(key, "20260910:KOSPI:stock")
        self.assertEqual(json.loads(stored)["OutBlock_1"][0]["ISU_SRT_CD"], "000001")

    def test_stored_response_tamper_is_rechecked_at_finalize(self):
        capture = self.complete()
        path = self.root / "capture/responses/20260910-KOSPI-stock.json"
        path.write_bytes(raw_body("tampered", "stock"))
        with self.assertRaisesRegex(CAPTURE.CaptureError, "STORED_RESPONSE_HASH_INVALID"):
            capture.finalize()

    def test_raw_frame_mismatch_and_missing_binding_fail_closed(self):
        capture = CAPTURE.SourceCapture(self.root / "mismatch", ("20260910", "20260911"), clock=lambda: self.NOW)
        key, stored = capture.capture(request("20260910", "KOSPI", "stock"), response("KOSPI", "stock"), received_at=self.NOW)
        capture.bind_parser_input(key, stored)
        wrong = CAPTURE._raw_projection(stored, "stock")
        wrong["000001"]["close"] = "999"
        with self.assertRaisesRegex(CAPTURE.CaptureError, "RAW_FRAME_MISMATCH"):
            capture.bind_normalized_frame(key, "b" * 64, wrong)
        with self.assertRaisesRegex(CAPTURE.CaptureError, "MISSING_RESPONSES"):
            capture.finalize()

    def test_response_secret_error_and_unknown_fields_are_not_retained(self):
        capture = CAPTURE.SourceCapture(self.root / "secret", ("20260910", "20260911"), clock=lambda: self.NOW)
        bad = FakeResponse(b'{"error":"authentication","access_token":"synthetic"}')
        with self.assertRaisesRegex(CAPTURE.CaptureError, "SECRET_FIELD_REJECTED"):
            capture.capture(request("20260910", "KOSPI", "stock"), bad)
        self.assertFalse((self.root / "secret/responses").exists())
        capture2 = CAPTURE.SourceCapture(self.root / "unknown", ("20260910", "20260911"), clock=lambda: self.NOW)
        raw = json.loads(raw_body("KOSPI", "stock"))
        raw["OutBlock_1"][0]["UNKNOWN"] = "value"
        with self.assertRaisesRegex(CAPTURE.CaptureError, "ROW_SCHEMA_INVALID"):
            capture2.capture(request("20260910", "KOSPI", "stock"), FakeResponse(json.dumps(raw).encode()))
        self.assertFalse((self.root / "unknown/responses").exists())
        capture3 = CAPTURE.SourceCapture(self.root / "nested", ("20260910", "20260911"), clock=lambda: self.NOW)
        nested = json.loads(raw_body("KOSPI", "stock"))
        nested["OutBlock_1"][0]["ISU_ABBRV"] = {"token": "synthetic"}
        with self.assertRaisesRegex(CAPTURE.CaptureError, "SECRET_FIELD_REJECTED"):
            capture3.capture(request("20260910", "KOSPI", "stock"), FakeResponse(json.dumps(nested).encode()))
        self.assertFalse((self.root / "nested/responses").exists())

    def test_dispatch_is_allowlisted_and_reserved_before_network(self):
        outbound = []

        def fake_send(_session, req, **_kwargs):
            outbound.append(req)
            family = "stock" if "MDCSTAT01501" in req.body else "index"
            return response("KOSPI", family)

        class FakeSession:
            send = fake_send

        requests = SimpleNamespace(Session=FakeSession)
        with mock.patch.dict(sys.modules, {"requests": requests}):
            capture = CAPTURE.SourceCapture(self.root / "dispatch", ("20260910", "20260911"), clock=lambda: self.NOW)
            with CAPTURE.capture_requests(capture):
                returned = requests.Session().send(request("20260910", "KOSPI", "stock"))
                self.assertEqual(returned.content, raw_body("KOSPI", "stock"))
                with self.assertRaisesRegex(CAPTURE.CaptureError, "UNEXPECTED_SESSION"):
                    requests.Session().send(request("20260909", "KOSPI", "stock"))
            self.assertEqual(len(outbound), 1)
            row = capture.records["20260910:KOSPI:stock"]["response"]
            self.assertEqual(row["parser_input_sha256"], row["sha256"])

    def test_time_format_future_backward_and_claim_order_fail_closed(self):
        with self.assertRaisesRegex(CAPTURE.CaptureError, "CAPTURE_START_INVALID"):
            CAPTURE.SourceCapture(self.root / "bad-clock", ("20260910", "20260911"), clock=lambda: "not-a-time")
        capture = CAPTURE.SourceCapture(self.root / "times", ("20260910", "20260911"), clock=lambda: self.NOW)
        with self.assertRaisesRegex(CAPTURE.CaptureError, "RECEIVED_AT_INVALID"):
            capture.capture(request("20260910", "KOSPI", "stock"), response("KOSPI", "stock"), received_at="2026-09-12T21:22:10+00:00")
        capture = CAPTURE.SourceCapture(self.root / "future", ("20260910", "20260911"), clock=lambda: self.NOW)
        with self.assertRaisesRegex(CAPTURE.CaptureError, "RECEIVED_AT_ORDER_INVALID"):
            capture.capture(request("20260910", "KOSPI", "stock"), response("KOSPI", "stock"), received_at="2026-09-12T21:22:11Z")
        with self.assertRaisesRegex(CAPTURE.CaptureError, "CLAIMED_START_ORDER_INVALID"):
            CAPTURE.require_claimed_start("2026-09-12T21:21:00Z", self.NOW)

    def test_calendar_independently_resolves_last_two_completed_sessions(self):
        contract = ROOT / "config/krx_information_system_source_candidate_v1.json"
        result = CAPTURE.require_completed_session_pair("20260910", "20260911", contract, "2026-09-13T00:00:00Z")
        self.assertEqual(result["latest_completed_session"], "20260911")
        with self.assertRaisesRegex(CAPTURE.CaptureError, "STALE_SESSION"):
            CAPTURE.require_completed_session_pair("20260909", "20260910", contract, "2026-09-13T00:00:00Z")
        with self.assertRaisesRegex(CAPTURE.CaptureError, "TIME_ORDER_INVALID"):
            CAPTURE.require_completed_session_pair("20260904", "20260907", contract, "2026-09-08T15:32:01Z")
        with self.assertRaisesRegex(CAPTURE.CaptureError, "YEAR_INVALID"):
            CAPTURE.require_completed_session_pair("20261230", "20270101", contract, "2027-01-01T08:00:00Z")
        early = CAPTURE.dt.datetime(2026, 1, 2, 10, 0, tzinfo=CAPTURE.SEOUL)
        with self.assertRaisesRegex(CAPTURE.CaptureError, "RANGE_INSUFFICIENT"):
            CAPTURE.completed_session_pair(
                early, 2026, {CAPTURE.dt.date(2026, 1, 1)}
            )

    def test_no_overwrite_request_secret_and_unknown_status(self):
        path = self.root / "existing.json"
        CAPTURE.write_new(path, b"first")
        with self.assertRaisesRegex(CAPTURE.CaptureError, "NO_OVERWRITE"):
            CAPTURE.write_new(path, b"second")
        bad = request("20260910", "KOSPI", "stock")
        bad.body += "&password=secret"
        with self.assertRaisesRegex(CAPTURE.CaptureError, "SECRET_OR_UNKNOWN"):
            CAPTURE.classify_public_request(bad.method, bad.url, bad.body)
        query = request("20260910", "KOSPI", "stock")
        query.url += "?unexpected_scope=synthetic"
        with self.assertRaisesRegex(CAPTURE.CaptureError, "REQUEST_ENDPOINT_INVALID"):
            CAPTURE.classify_public_request(query.method, query.url, query.body)
        self.assertTrue(all(value is False for value in CAPTURE.unknown_status("STOP")["authority"].values()))

    def test_dependency_hash_version_and_tamper(self):
        wheel_dir = self.root / "wheels"
        wheel_dir.mkdir()
        packages = {}
        versions = {}
        for name in ("numpy", "pandas", "pykrx", "requests"):
            raw = f"fixture-{name}".encode()
            filename = f"{name}.whl"
            (wheel_dir / filename).write_bytes(raw)
            packages[name] = {"filename": filename, "sha256": CAPTURE.sha256_bytes(raw), "version": "1"}
            versions[name] = "1"
        contract = self.root / "contract.json"
        contract.write_text(json.dumps({"contract_version": "krx_information_system_source_candidate/1", "status": "DRAFT_NOT_RUNTIME_RATIFIED", "dependency_lock": {"packages": packages}}))
        self.assertEqual(DEPS.verify(contract, wheel_dir, installed_versions=versions)["status"], "PASS")
        (wheel_dir / "pykrx.whl").write_bytes(b"tampered")
        with self.assertRaisesRegex(DEPS.DependencyError, "WHEEL_HASH_INVALID:pykrx"):
            DEPS.verify(contract, wheel_dir, installed_versions=versions)


if __name__ == "__main__":
    unittest.main()
