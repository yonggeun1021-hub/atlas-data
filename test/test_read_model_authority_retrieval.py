#!/usr/bin/env python3
import base64
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/fetch_briefing_read_model.py"
SPEC = importlib.util.spec_from_file_location("fetch_briefing_read_model", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

SHA = "1" * 40
DATE = "2026-08-25"
GEN = "2" * 64


def json_bytes(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


class FakeGitHub:
    def __init__(self, files):
        self.files = files
        self.calls = []

    def __call__(self, url, headers):
        self.calls.append((url, copy.deepcopy(headers)))
        if "/git/ref/heads/main" in url:
            return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": SHA}}
        marker = "/contents/"
        path = url.split(marker, 1)[1].split("?", 1)[0]
        raw = self.files[path]
        encoded = base64.b64encode(raw).decode()
        encoded = "\n".join(encoded[i:i + 60] for i in range(0, len(encoded), 60))
        return {
            "type": "file", "path": path, "encoding": "base64",
            "content": encoded, "sha": M.git_blob_sha1(raw),
        }


class ReadModelAuthorityRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.contract = M.load_contract()
        generation = {"generation_id": GEN, "generation_contract_version": 1}
        self.files = {
            "data/briefing/step0_status.json": json_bytes({
                "schema_version": 2, "expected_kst_date": DATE,
                "generation": generation,
            }),
            "data/briefing_status.json": json_bytes({
                "schema_version": 2, "expected_kst_date": DATE,
                "generation": generation,
            }),
            "data/briefing/krx/005930.json": json_bytes({
                "schema_version": 2, "collected_for_kst_date": DATE,
                "generation": generation,
            }),
        }

    def retrieve(self, files=None, symbols=None, contract=None):
        fake = FakeGitHub(files or self.files)
        result = M.retrieve(
            DATE, symbols or {"krx": ["005930"]},
            contract or self.contract, fake,
        )
        return result, fake

    def test_resolves_once_then_pins_every_content_request_to_full_sha(self):
        (_, envelope), fake = self.retrieve()
        self.assertEqual(envelope["source_commit"], SHA)
        self.assertEqual(len([u for u, _ in fake.calls if "/git/ref/" in u]), 1)
        content_calls = [u for u, _ in fake.calls if "/contents/" in u]
        self.assertEqual(len(content_calls), 3)
        self.assertTrue(all(f"ref={SHA}" in url for url in content_calls))
        self.assertTrue(all("ref=main" not in url for url in content_calls))

    def test_no_cache_and_version_headers_are_used_for_every_request(self):
        (_, _), fake = self.retrieve()
        for _, headers in fake.calls:
            self.assertEqual(headers["Cache-Control"], "no-cache")
            self.assertEqual(headers["X-GitHub-Api-Version"], "2022-11-28")

    def test_happy_path_binds_date_generation_commit_and_authority(self):
        (raw, envelope), _ = self.retrieve()
        self.assertEqual(envelope["expected_kst_date"], DATE)
        self.assertEqual(envelope["generation_id"], GEN)
        self.assertEqual(envelope["stale_detection"], "PASS")
        self.assertEqual(len(raw), 3)
        self.assertTrue(envelope["authority"]["read_model_retrieval_only"])
        self.assertFalse(envelope["authority"]["trading_authority"])

    def test_branch_ref_response_with_non_commit_is_rejected(self):
        def bad(url, headers):
            return {"ref": "refs/heads/main", "object": {"type": "tag", "sha": SHA}}
        with self.assertRaisesRegex(M.RetrievalError, "IMMUTABLE_COMMIT_INVALID"):
            M.resolve_immutable_commit(self.contract, bad)

    def test_short_or_uppercase_commit_is_rejected(self):
        for bad_sha in ("1" * 7, "A" * 40):
            def bad(url, headers, value=bad_sha):
                return {"ref": "refs/heads/main", "object": {"type": "commit", "sha": value}}
            with self.assertRaisesRegex(M.RetrievalError, "IMMUTABLE_COMMIT_INVALID"):
                M.resolve_immutable_commit(self.contract, bad)

    def test_stale_step0_is_rejected(self):
        files = dict(self.files)
        value = json.loads(files["data/briefing/step0_status.json"])
        value["expected_kst_date"] = "2026-08-24"
        files["data/briefing/step0_status.json"] = json_bytes(value)
        with self.assertRaisesRegex(M.RetrievalError, "ARTIFACT_STALE_DATE"):
            self.retrieve(files=files)

    def test_stale_compact_is_rejected(self):
        files = dict(self.files)
        value = json.loads(files["data/briefing/krx/005930.json"])
        value["collected_for_kst_date"] = "2026-08-24"
        files["data/briefing/krx/005930.json"] = json_bytes(value)
        with self.assertRaisesRegex(M.RetrievalError, "ARTIFACT_STALE_DATE"):
            self.retrieve(files=files)

    def test_health_from_other_generation_is_rejected(self):
        files = dict(self.files)
        value = json.loads(files["data/briefing_status.json"])
        value["generation"]["generation_id"] = "3" * 64
        files["data/briefing_status.json"] = json_bytes(value)
        with self.assertRaisesRegex(M.RetrievalError, "MIXED_GENERATION_READ"):
            self.retrieve(files=files)

    def test_compact_from_other_generation_is_rejected(self):
        files = dict(self.files)
        value = json.loads(files["data/briefing/krx/005930.json"])
        value["generation"]["generation_id"] = "3" * 64
        files["data/briefing/krx/005930.json"] = json_bytes(value)
        with self.assertRaisesRegex(M.RetrievalError, "MIXED_GENERATION_READ"):
            self.retrieve(files=files)

    def test_blob_sha_mismatch_is_rejected(self):
        fake = FakeGitHub(self.files)
        original = fake.__call__
        def tampered(url, headers):
            value = original(url, headers)
            if "/contents/" in url:
                value["sha"] = "f" * 40
            return value
        with self.assertRaisesRegex(M.RetrievalError, "ARTIFACT_BLOB_SHA_MISMATCH"):
            M.retrieve(DATE, {"krx": ["005930"]}, self.contract, tampered)

    def test_response_path_substitution_is_rejected(self):
        fake = FakeGitHub(self.files)
        original = fake.__call__
        def substituted(url, headers):
            value = original(url, headers)
            if "/contents/" in url:
                value["path"] = "data/other.json"
            return value
        with self.assertRaisesRegex(M.RetrievalError, "ARTIFACT_IDENTITY_MISMATCH"):
            M.retrieve(DATE, {}, self.contract, substituted)

    def test_unsupported_market_and_unsafe_symbol_are_rejected(self):
        for symbols, code in (({"us": ["TSM"]}, "COMPACT_MARKET_UNSUPPORTED"),
                              ({"sec": ["../TSM"]}, "COMPACT_SYMBOL_INVALID")):
            with self.assertRaisesRegex(M.RetrievalError, code):
                self.retrieve(symbols=symbols)

    def test_contract_authority_escalation_is_rejected(self):
        contract = copy.deepcopy(self.contract)
        contract["authority"]["trading_authority"] = True
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "contract.json"
            path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(M.RetrievalError, "AUTHORITY_ESCALATION"):
                M.load_contract(path)

    def test_contract_cannot_redirect_canonical_endpoint(self):
        for field, value, code in (
            ("canonical_ref_endpoint", "https://evil.example/ref", "REF_ENDPOINT_MISMATCH"),
            ("canonical_content_endpoint_template", "https://evil.example/{path}", "CONTENT_ENDPOINT_MISMATCH"),
        ):
            contract = copy.deepcopy(self.contract)
            contract[field] = value
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "contract.json"
                path.write_text(json.dumps(contract))
                with self.assertRaisesRegex(M.RetrievalError, code):
                    M.load_contract(path)

    def test_contract_cannot_inject_path_traversal_template(self):
        contract = copy.deepcopy(self.contract)
        contract["compact_path_templates"]["krx"] = "../../{symbol}.json"
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "contract.json"
            path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(M.RetrievalError, "COMPACT_TEMPLATE_INVALID"):
                M.load_contract(path)

    def test_persist_is_atomic_and_refuses_overwrite(self):
        (raw, envelope), _ = self.retrieve()
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "retrieved"
            M.persist(out, raw, envelope)
            self.assertEqual(
                json.loads((out / "retrieval_authority.json").read_text())["source_commit"],
                SHA,
            )
            with self.assertRaisesRegex(M.RetrievalError, "OUTPUT_ALREADY_EXISTS"):
                M.persist(out, raw, envelope)

    def test_content_endpoint_template_never_uses_raw_cdn_or_floating_ref(self):
        template = self.contract["canonical_content_endpoint_template"]
        self.assertIn("api.github.com", template)
        self.assertNotIn("raw.githubusercontent.com", template)
        self.assertIn("immutable_commit_sha", template)
        self.assertNotIn("ref=main", template)


if __name__ == "__main__":
    unittest.main()
