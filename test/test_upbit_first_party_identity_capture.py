"""P3-12 first-party identity-evidence capture regression."""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / ".github" / "scripts" / "upbit_first_party_identity_capture.py"
SPEC = importlib.util.spec_from_file_location("upbit_first_party_identity_capture", MODULE_PATH)
CAP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CAP)


NOW = dt.datetime(2026, 8, 30, 8, 45, 0, tzinfo=dt.timezone.utc)


def fixed_clock():
    return NOW


def build_fetcher(contract, *, evil_redirect_market=None, missing_marker_market=None):
    by_url = {row["url"]: row for row in contract["assets"]}

    def fetcher(url, timeout_seconds, max_response_bytes):
        del timeout_seconds, max_response_bytes
        source = by_url[url]
        markers = source["required_markers"]
        raw = ("identity page " + " ".join(markers)).encode("utf-8")
        if source["market"] == missing_marker_market:
            raw = b"unrelated page"
        effective_url = source["url"]
        if source["market"] == evil_redirect_market:
            effective_url = "https://attacker.example/forged"
        return CAP.FetchResult(raw, effective_url, 200, "text/html")

    return fetcher


class UpbitFirstPartyIdentityCaptureTests(unittest.TestCase):
    def test_contract_is_exactly_bounded_to_frozen_paper_markets(self):
        contract = CAP.load_contract()
        freeze = json.loads((ROOT / "config/upbit_identity_taxonomy_governance_freeze.json").read_text())
        self.assertEqual(
            sorted(row["market"] for row in contract["assets"]),
            sorted(freeze["blocked_paper_markets"]),
        )
        self.assertEqual(len(contract["assets"]), 8)
        self.assertTrue(all(value is False for value in contract["authority"].values()))
        self.assertFalse(contract["auth_required"])
        self.assertFalse(contract["order_or_withdrawal_endpoints_called"])

    def test_capture_binds_source_type_domain_hash_and_times(self):
        contract = CAP.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            target = CAP.capture_snapshot(
                Path(tmp), capture_id="20260830T084500Z",
                fetcher=build_fetcher(contract), clock=fixed_clock,
            )
            manifest = CAP.validate_snapshot(target)
            self.assertEqual(manifest["asset_count"], 8)
            self.assertEqual(manifest["review_status"], "PROPOSED_EVIDENCE_ONLY_AUTHORITY_FALSE")
            self.assertTrue(all(value is False for value in manifest["authority"].values()))
            for row in manifest["sources"]:
                self.assertIn(row["source_type"], {
                    "PROJECT_FIRST_PARTY_PUBLIC_WEB",
                    "PROJECT_FIRST_PARTY_PUBLIC_DOCUMENTATION",
                })
                self.assertTrue(row["validated_authority_domain"])
                self.assertEqual(row["observed_at"], "2026-08-30T08:45:00Z")
                self.assertEqual(row["available_at"], "2026-08-30T08:45:00Z")
                self.assertIsNone(row["source_published_at"])
                self.assertFalse(row["atlas_capture_time_is_source_published_at"])
                with gzip.open(target / row["raw_file"], "rb") as handle:
                    self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), row["content_sha256"])

    def test_append_only_violation_preserves_snapshot(self):
        contract = CAP.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = CAP.capture_snapshot(
                root, capture_id="20260830T084500Z",
                fetcher=build_fetcher(contract), clock=fixed_clock,
            )
            before = (target / "_manifest.json").read_bytes()
            with self.assertRaisesRegex(CAP.CaptureError, "APPEND_ONLY_VIOLATION"):
                CAP.capture_snapshot(
                    root, capture_id="20260830T084500Z",
                    fetcher=build_fetcher(contract), clock=fixed_clock,
                )
            self.assertEqual((target / "_manifest.json").read_bytes(), before)

    def test_redirect_outside_allowlist_fails_closed(self):
        contract = CAP.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CAP.CaptureError, "REDIRECT_AUTHORITY_REJECTED"):
                CAP.capture_snapshot(
                    Path(tmp), capture_id="20260830T084500Z",
                    fetcher=build_fetcher(contract, evil_redirect_market="KRW-BTC"),
                    clock=fixed_clock,
                )

    def test_missing_identity_marker_fails_closed(self):
        contract = CAP.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(CAP.CaptureError, "IDENTITY_MARKER_MISSING"):
                CAP.capture_snapshot(
                    Path(tmp), capture_id="20260830T084500Z",
                    fetcher=build_fetcher(contract, missing_marker_market="KRW-ETH"),
                    clock=fixed_clock,
                )

    def test_raw_content_tamper_is_rejected(self):
        contract = CAP.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            target = CAP.capture_snapshot(
                Path(tmp), capture_id="20260830T084500Z",
                fetcher=build_fetcher(contract), clock=fixed_clock,
            )
            btc = target / "BTC/source.html.gz"
            with gzip.GzipFile(filename=str(btc), mode="wb", mtime=0) as handle:
                handle.write(b"tampered")
            with self.assertRaisesRegex(CAP.CaptureError, "RAW_FILE_HASH_MISMATCH"):
                CAP.validate_snapshot(target)

    def test_manifest_time_tamper_is_rejected(self):
        contract = CAP.load_contract()
        with tempfile.TemporaryDirectory() as tmp:
            target = CAP.capture_snapshot(
                Path(tmp), capture_id="20260830T084500Z",
                fetcher=build_fetcher(contract), clock=fixed_clock,
            )
            path = target / "_manifest.json"
            manifest = json.loads(path.read_text())
            manifest["sources"][0]["available_at"] = "2026-08-30T08:44:59Z"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(CAP.CaptureError, "MANIFEST_TIME_ORDER_INVALID"):
                CAP.validate_snapshot(target)

    def test_contract_rejects_scope_expansion_and_authority_opening(self):
        base = json.loads((ROOT / "config/upbit_first_party_identity_capture_contract.json").read_text())
        freeze = ROOT / "config/upbit_identity_taxonomy_governance_freeze.json"
        for mutation, expected in (
            (lambda value: value["assets"].pop(), "CONTRACT_FREEZE_SCOPE_MISMATCH"),
            (lambda value: value["authority"].__setitem__("order_authorized", True), "CONTRACT_AUTHORITY_INVALID"),
        ):
            contract = copy.deepcopy(base)
            mutation(contract)
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "contract.json"
                path.write_text(json.dumps(contract), encoding="utf-8")
                with self.assertRaisesRegex(CAP.CaptureError, expected):
                    CAP.load_contract(path, freeze)


if __name__ == "__main__":
    unittest.main(verbosity=2)
