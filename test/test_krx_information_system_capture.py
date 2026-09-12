#!/usr/bin/env python3
"""Offline source-retention and fail-closed checks."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest


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


def response(label):
    return SimpleNamespace(
        content=json.dumps({"output": [{"label": label}]}, separators=(",", ":")).encode(),
        headers={"Content-Type": "application/json;charset=UTF-8", "Set-Cookie": "must-not-retain"},
    )


class CaptureContractTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def complete(self):
        capture = CAPTURE.SourceCapture(self.root / "capture", ("20260910", "20260911"))
        for date in ("20260910", "20260911"):
            for market in ("KOSPI", "KOSDAQ"):
                for family in ("stock", "index"):
                    capture.capture(request(date, market, family), response(f"{date}-{market}-{family}"), received_at="2026-09-12T21:22:08Z")
        return capture

    def test_complete_capture_retains_eight_exact_bodies_without_secrets(self):
        manifest = self.complete().finalize()
        self.assertEqual(len(manifest["records"]), 8)
        self.assertTrue(manifest["original_response_bytes_retained"])
        self.assertFalse(manifest["request_headers_retained"])
        self.assertFalse(manifest["cookies_retained"])
        text = json.dumps(manifest, ensure_ascii=False)
        self.assertNotIn("Set-Cookie", text)
        self.assertNotIn("must-not-retain", text)
        for row in manifest["records"]:
            raw = (self.root / "capture" / row["response"]["path"]).read_bytes()
            self.assertEqual(CAPTURE.sha256_bytes(raw), row["response"]["sha256"])

    def test_missing_duplicate_and_stale_fail_closed(self):
        capture = CAPTURE.SourceCapture(self.root / "missing", ("20260910", "20260911"))
        capture.capture(request("20260910", "KOSPI", "stock"), response("one"))
        with self.assertRaisesRegex(CAPTURE.CaptureError, "DUPLICATE_RESPONSE"):
            capture.capture(request("20260910", "KOSPI", "stock"), response("two"))
        with self.assertRaisesRegex(CAPTURE.CaptureError, "MISSING_RESPONSES"):
            capture.finalize()
        with self.assertRaisesRegex(CAPTURE.CaptureError, "STALE_SESSION"):
            CAPTURE.require_current_session("20260910", "20260911")
        closed = CAPTURE.unknown_status("STALE_SESSION")
        self.assertEqual(closed["status"], "UNKNOWN_NO_OVERWRITE")
        self.assertTrue(all(value is False for value in closed["authority"].values()))

    def test_no_overwrite_and_secret_request_field_rejection(self):
        path = self.root / "existing.json"
        CAPTURE.write_new(path, b"first")
        with self.assertRaisesRegex(CAPTURE.CaptureError, "NO_OVERWRITE"):
            CAPTURE.write_new(path, b"second")
        self.assertEqual(path.read_bytes(), b"first")
        bad = request("20260910", "KOSPI", "stock")
        bad.body += "&password=secret"
        with self.assertRaisesRegex(CAPTURE.CaptureError, "SECRET_OR_UNKNOWN"):
            CAPTURE.classify_public_request(bad.method, bad.url, bad.body)

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
