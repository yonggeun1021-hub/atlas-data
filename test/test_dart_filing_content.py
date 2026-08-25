#!/usr/bin/env python3
"""P4-03 DART original-document evidence regression (offline only)."""

import copy
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock
import zipfile


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "collectors" / "dart_filing_content.py"
CONTRACT_PATH = ROOT / "config" / "dart_filing_content_contract.json"
WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"

SPEC = importlib.util.spec_from_file_location("dart_filing_content", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def filing(**changes):
    value = {
        "date": "20260820",
        "title": "단일판매ㆍ공급계약체결",
        "rcept_no": "20260820800123",
        "url": (
            "https://dart.fss.or.kr/dsaf001/main.do?"
            "rcpNo=20260820800123"
        ),
    }
    value.update(changes)
    return value


def archive_bytes(members=None):
    members = members or {
        "report.xml": (
            "<?xml version='1.0' encoding='UTF-8'?><DOCUMENT>"
            "<TITLE>단일판매ㆍ공급계약체결</TITLE>"
            "<BODY>계약금액 1,234,567 원</BODY></DOCUMENT>"
        ).encode("utf-8"),
        "appendix.html": "<html><body>첨부 문서</body></html>".encode("utf-8"),
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    return output.getvalue()


class Fetcher:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def __call__(self, rcept_no):
        self.calls.append(rcept_no)
        if isinstance(self.raw, Exception):
            raise self.raw
        return self.raw


class DartFilingContentTest(unittest.TestCase):
    def setUp(self):
        self.contract = MODULE.load_contract(CONTRACT_PATH)

    def capture(
        self,
        value=None,
        *,
        stage="Ready",
        raw=None,
        existing=None,
        force=False,
    ):
        fetcher = Fetcher(raw if raw is not None else archive_bytes())
        result, raw_zip, members = MODULE.capture_filing(
            ticker="005930",
            stage=stage,
            filing=value or filing(),
            fetcher=fetcher,
            retrieved_at_utc="2026-08-20T00:00:00Z",
            contract=self.contract,
            existing_manifest=existing,
            force_refresh=force,
        )
        return result, raw_zip, members, fetcher

    def test_authority_stage_and_title_scope_are_fail_closed(self):
        self.assertEqual(
            self.contract["authority"],
            {
                "evidence_only": True,
                "item_extraction_authorized": False,
                "interpretation_authorized": False,
                "rule_evaluation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
        )
        for stage in ("Ready", "Buy", "Holding", "Candidate"):
            self.assertEqual(
                MODULE.filing_plan(filing(), stage, self.contract)[
                    "capture_policy"
                ],
                "required",
            )
        self.assertEqual(
            MODULE.filing_plan(filing(), "Discovery", self.contract)[
                "capture_policy"
            ],
            "best_effort",
        )
        self.assertEqual(
            MODULE.filing_plan(filing(), None, self.contract)["content_status"],
            "NOT_APPLICABLE",
        )
        unrelated = MODULE.filing_plan(
            filing(title="주주총회소집공고"), "Ready", self.contract
        )
        self.assertEqual(unrelated["filing_classification"], "UNCLASSIFIED_TITLE")
        self.assertEqual(unrelated["capture_policy"], "index_only")

    def test_complete_zip_is_one_content_unit_without_invented_items(self):
        result, raw_zip, members, fetcher = self.capture()

        self.assertEqual(fetcher.calls, [filing()["rcept_no"]])
        self.assertEqual(result["content_status"], "OK")
        self.assertEqual(result["evidence_status"], "PENDING")
        self.assertEqual(result["interpretation_status"], "UNDETERMINED")
        self.assertEqual(result["rule_impact"], "NONE")
        self.assertEqual(result["action"], "NO_CHANGE")
        self.assertEqual(result["reasons"], ["ITEM_EXTRACTION_POLICY_UNRATIFIED"])
        self.assertEqual(result["extracted"], [])
        self.assertEqual(result["raw_cache_policy"], "permanent")
        self.assertEqual(len(result["documents"]), 2)
        self.assertEqual(len(members), 2)
        self.assertEqual(
            result["source_archive"]["content_sha256"],
            hashlib.sha256(raw_zip).hexdigest(),
        )
        self.assertEqual(
            result["source_archive"]["source_uri"],
            "https://opendart.fss.or.kr/api/document.xml?"
            "rcept_no=20260820800123",
        )
        self.assertNotIn("crtfc_key", json.dumps(result))
        for document in result["documents"]:
            self.assertEqual(len(document["content_sha256"]), 64)
            self.assertEqual(len(document["normalized_text_sha256"]), 64)
            self.assertGreater(document["normalized_text_chars"], 0)

        binary, _, _, _ = self.capture(
            raw=archive_bytes({"image.png": b"\x89PNG\x00\x01"})
        )
        self.assertEqual(binary["content_status"], "OK")
        self.assertEqual(
            binary["documents"][0]["text_status"],
            "NOT_APPLICABLE_BINARY",
        )
        self.assertIsNone(binary["documents"][0]["normalized_text_sha256"])

    def test_live_opendart_right_padded_title_is_normalized_before_manifest(self):
        raw_title = "단일판매ㆍ공급계약체결              "
        source = filing(title=raw_title)
        result, raw_zip, members, fetcher = self.capture(source)

        self.assertEqual(fetcher.calls, [source["rcept_no"]])
        self.assertEqual(source["title"], raw_title)
        self.assertEqual(result["title"], "단일판매ㆍ공급계약체결")
        self.assertEqual(result["content_status"], "OK")
        self.assertEqual(
            MODULE.validate_manifest(result, raw_zip, members, self.contract),
            result,
        )

        tampered = copy.deepcopy(result)
        tampered["title"] += " "
        with self.assertRaisesRegex(
            MODULE.DartContentError, "MANIFEST_TITLE_INVALID"
        ):
            MODULE.validate_manifest(tampered, raw_zip, members, self.contract)

    def test_persisted_manifest_validator_rederives_archive_member_index(self):
        result, raw_zip, members, _ = self.capture()
        checked = MODULE.validate_manifest(
            copy.deepcopy(result), raw_zip, members, self.contract
        )
        self.assertEqual(checked, result)

        changed = copy.deepcopy(result)
        changed["documents"][0]["normalized_text_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.DartContentError, "MANIFEST_ARCHIVE_DERIVATION_MISMATCH"
        ):
            MODULE.validate_manifest(changed, raw_zip, members, self.contract)

    def test_persisted_manifest_validator_rejects_authority_and_source_drift(self):
        result, raw_zip, members, _ = self.capture()
        changed = copy.deepcopy(result)
        changed["action"] = "BUY"
        with self.assertRaisesRegex(
            MODULE.DartContentError, "MANIFEST_STATUS_OR_AUTHORITY_MISMATCH"
        ):
            MODULE.validate_manifest(changed, raw_zip, members, self.contract)

        changed = copy.deepcopy(result)
        changed["source_archive"]["source_uri"] = "https://example.com/document.zip"
        with self.assertRaisesRegex(
            MODULE.DartContentError, "MANIFEST_SOURCE_ARCHIVE_IDENTITY_MISMATCH"
        ):
            MODULE.validate_manifest(changed, raw_zip, members, self.contract)

    def test_loading_existing_cache_revalidates_manifest_and_archive(self):
        result, raw_zip, members, _ = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            MODULE.persist_success(
                root, result, raw_zip, members, self.contract
            )
            loaded = MODULE.load_existing_manifest(
                root, "005930", filing()["rcept_no"], self.contract
            )
            self.assertEqual(loaded, result)

            path = MODULE.manifest_dir(
                root, "005930", filing()["rcept_no"]
            ) / "_manifest.json"
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["documents"][0]["normalized_text_chars"] += 1
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.DartContentError, "MANIFEST_ARCHIVE_DERIVATION_MISMATCH"
            ):
                MODULE.load_existing_manifest(
                    root, "005930", filing()["rcept_no"], self.contract
                )

    def test_provider_free_skip_rejects_semantically_tampered_manifest(self):
        result, _, _, _ = self.capture()
        result["action"] = "BUY"
        failed, raw_zip, members, fetcher = self.capture(
            existing=result, raw=AssertionError("provider must not be called")
        )
        self.assertEqual(failed["operation"], "failed")
        self.assertIn("MANIFEST_STATUS_OR_AUTHORITY_MISMATCH", failed["reasons"][0])
        self.assertIsNone(raw_zip)
        self.assertEqual(members, {})
        self.assertEqual(fetcher.calls, [])

    def test_identity_archive_and_limits_fail_without_partial_content(self):
        bad_url = filing(
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260820800999"
        )
        result, raw_zip, members, _ = self.capture(bad_url)
        self.assertEqual(result["content_status"], "PENDING")
        self.assertIn("FILING_URL_IDENTITY_MISMATCH", result["reasons"][0])
        self.assertIsNone(raw_zip)
        self.assertEqual(members, {})

        result, raw_zip, members, _ = self.capture(raw=b"not a zip")
        self.assertEqual(result["content_status"], "PENDING")
        self.assertIn("ARCHIVE_INVALID_ZIP", result["reasons"][0])
        self.assertIsNone(raw_zip)
        self.assertEqual(members, {})

        result, _, _, _ = self.capture(
            raw=b"<result><status>020</status><message>limit</message></result>"
        )
        self.assertIn("DART_API_ERROR:020", result["reasons"][0])

        result, _, _, _ = self.capture(
            raw=archive_bytes({"../escape.xml": b"<x>escape</x>"})
        )
        self.assertIn("ARCHIVE_MEMBER_PATH_INVALID", result["reasons"][0])

        duplicate_io = io.BytesIO()
        with zipfile.ZipFile(duplicate_io, "w") as duplicate:
            with mock.patch("warnings.warn"):
                duplicate.writestr("same.xml", b"<x>one</x>")
                duplicate.writestr("same.xml", b"<x>two</x>")
        result, _, _, _ = self.capture(raw=duplicate_io.getvalue())
        self.assertIn("ARCHIVE_MEMBER_DUPLICATE", result["reasons"][0])

        symlink_io = io.BytesIO()
        link = zipfile.ZipInfo("link.xml")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        with zipfile.ZipFile(symlink_io, "w") as symlink:
            symlink.writestr(link, b"target.xml")
        result, _, _, _ = self.capture(raw=symlink_io.getvalue())
        self.assertIn("ARCHIVE_SYMLINK_MEMBER_NOT_ALLOWED", result["reasons"][0])

        limited = copy.deepcopy(self.contract)
        limited["archive_policy"]["max_member_bytes"] = 2
        fetcher = Fetcher(archive_bytes({"large.xml": b"<x>large</x>"}))
        result, _, _ = MODULE.capture_filing(
            ticker="005930",
            stage="Ready",
            filing=filing(),
            fetcher=fetcher,
            retrieved_at_utc="2026-08-20T00:00:00Z",
            contract=limited,
        )
        self.assertEqual(result["content_status"], "PENDING")
        self.assertIn("ARCHIVE_MEMBER_OVERSIZE", result["reasons"][0])

    def test_skip_avoids_provider_and_source_mutation_never_overwrites(self):
        first, raw_zip, members, _ = self.capture()
        skipped, skipped_zip, skipped_members, fetcher = self.capture(
            existing=first, raw=AssertionError("provider must not be called")
        )
        self.assertEqual(skipped["operation"], "skipped")
        self.assertEqual(skipped["skip_reason"], "already_captured")
        self.assertEqual(fetcher.calls, [])
        self.assertIsNone(skipped_zip)
        self.assertEqual(skipped_members, {})

        changed, changed_zip, changed_members, _ = self.capture(
            existing=first,
            force=True,
            raw=archive_bytes({"report.xml": b"<x>changed</x>"}),
        )
        self.assertEqual(changed["content_status"], "PENDING")
        self.assertIn(
            "SOURCE_MUTATED_FAIL_CLOSED_NO_OVERWRITE", changed["reasons"][0]
        )
        self.assertIsNone(changed_zip)
        self.assertEqual(changed_members, {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            MODULE.persist_success(root, first, raw_zip, members)
            directory = MODULE.manifest_dir(root, "005930", filing()["rcept_no"])
            before = {
                path.name: path.read_bytes()
                for path in directory.iterdir()
                if path.is_file()
            }
            mutation = copy.deepcopy(first)
            mutation["source_archive"]["content_sha256"] = "0" * 64
            with self.assertRaisesRegex(
                MODULE.DartContentError,
                "SOURCE_MUTATED_FAIL_CLOSED_NO_OVERWRITE",
            ):
                MODULE.persist_success(
                    root,
                    mutation,
                    archive_bytes({"report.xml": b"<x>changed</x>"}),
                    {"member-001-0000000000000000.gz": b"changed"},
                )
            after = {
                path.name: path.read_bytes()
                for path in directory.iterdir()
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_persistence_is_atomic_and_cache_round_trips(self):
        result, raw_zip, members, _ = self.capture()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            MODULE.persist_success(root, result, raw_zip, members)
            directory = MODULE.manifest_dir(root, "005930", filing()["rcept_no"])
            stored = json.loads(
                (directory / "_manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual(stored["filing_identity"], result["filing_identity"])
            self.assertEqual(
                hashlib.sha256((directory / "_source.zip").read_bytes()).hexdigest(),
                result["source_archive"]["content_sha256"],
            )
            for document in stored["documents"]:
                member = gzip.decompress(
                    (directory / document["cache_name"]).read_bytes()
                )
                self.assertEqual(
                    hashlib.sha256(member).hexdigest(),
                    document["content_sha256"],
                )
            self.assertFalse(list(directory.parent.glob(f".{directory.name}.tmp.*")))

    def test_run_is_date_guarded_temp_isolated_and_publishes_failure_truth(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "latest_dart.json"
            source.write_text(
                json.dumps(
                    {
                        "collected_for_kst_date": "2026-08-20",
                        "stocks": {
                            "005930": {
                                "name": "삼성전자",
                                "status": "ok",
                                "atlas_stage": "Candidate",
                                "relevant": [filing()],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            data = root / "data"
            run = MODULE.run_capture(
                source_path=source,
                data_root=data,
                expected_kst_date="2026-08-20",
                retrieved_at_utc="2026-08-20T00:00:00Z",
                fetcher=Fetcher(archive_bytes()),
                contract=self.contract,
            )
            self.assertEqual(
                run["counts"],
                {"captured": 1, "skipped": 0, "failed": 0, "not_applicable": 0},
            )
            self.assertTrue((data / "latest_dart_content.json").is_file())
            self.assertFalse((ROOT / "data" / "dart_content").exists())

            padded_source = root / "latest_dart_padded.json"
            padded_source.write_text(
                json.dumps(
                    {
                        "collected_for_kst_date": "2026-08-20",
                        "stocks": {
                            "005930": {
                                "name": "삼성전자",
                                "status": "ok",
                                "atlas_stage": "Candidate",
                                "relevant": [
                                    filing(title="단일판매ㆍ공급계약체결              ")
                                ],
                            }
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            padded_data = root / "padded-data"
            padded = MODULE.run_capture(
                source_path=padded_source,
                data_root=padded_data,
                expected_kst_date="2026-08-20",
                retrieved_at_utc="2026-08-20T00:00:00Z",
                fetcher=Fetcher(archive_bytes()),
                contract=self.contract,
            )
            self.assertEqual(padded["counts"]["captured"], 1)
            self.assertEqual(padded["counts"]["failed"], 0)
            self.assertEqual(
                padded["records"][0]["title"], "단일판매ㆍ공급계약체결"
            )

            second_fetcher = Fetcher(AssertionError("no provider call"))
            second = MODULE.run_capture(
                source_path=source,
                data_root=data,
                expected_kst_date="2026-08-20",
                retrieved_at_utc="2026-08-20T01:00:00Z",
                fetcher=second_fetcher,
                contract=self.contract,
            )
            self.assertEqual(second["counts"]["skipped"], 1)
            self.assertEqual(second_fetcher.calls, [])

            with self.assertRaisesRegex(
                MODULE.DartContentError, "SOURCE_DATE_MISMATCH"
            ):
                MODULE.run_capture(
                    source_path=source,
                    data_root=root / "wrong",
                    expected_kst_date="2026-08-21",
                    retrieved_at_utc="2026-08-21T00:00:00Z",
                    fetcher=Fetcher(archive_bytes()),
                    contract=self.contract,
                )

            failure_root = root / "failure"
            with mock.patch.dict(
                MODULE.os.environ,
                {"DART_API_KEY": "A" * 40},
                clear=False,
            ):
                exit_code = MODULE.main(
                    [
                        "--source",
                        str(source),
                        "--data-root",
                        str(failure_root),
                        "--expected-kst-date",
                        "2026-08-21",
                        "--observed-at-utc",
                        "2026-08-21T00:00:00Z",
                    ]
                )
            self.assertEqual(exit_code, 1)
            failure = json.loads(
                (failure_root / "latest_dart_content.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(failure["run_status"], "FAILED")
            self.assertIn("SOURCE_DATE_MISMATCH", failure["reasons"][0])

    def test_workflow_is_always_repairable_and_commits_content_state(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("- name: Capture DART filing content (P4-03)", workflow)
        block = workflow.split(
            "- name: Capture DART filing content (P4-03)", 1
        )[1].split("- name: Capture SEC filing content (P4-02)", 1)[0]
        self.assertIn("if: always()", block)
        self.assertIn("continue-on-error: true", block)
        self.assertIn("DART_API_KEY", block)
        self.assertIn("--expected-kst-date", block)
        commit = workflow.split("- name: Commit data", 1)[1]
        self.assertIn("git add data/", commit)


if __name__ == "__main__":
    unittest.main()
