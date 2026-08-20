#!/usr/bin/env python3
"""P4-02 SEC filing content acquisition regression (offline only)."""

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "collectors" / "sec_filing_content.py"
CONTRACT_PATH = ROOT / "config" / "sec_filing_content_contract.json"
FIXTURE = ROOT / "collectors" / "fixtures" / "sec_content_tsm_board_20260811_evidence_slice.html"
FIXTURE_MANIFEST = ROOT / "collectors" / "fixtures" / "sec_content_tsm_board_20260811_MANIFEST.json"
WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"

SPEC = importlib.util.spec_from_file_location("sec_filing_content", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def filing(**changes):
    value = {
        "date": "2026-08-11",
        "form": "6-K",
        "accession": "0001046179-26-000536",
        "url": (
            "https://www.sec.gov/Archives/edgar/data/1046179/"
            "000104617926000536/tsm-boardx20260811.htm"
        ),
        "index_url": (
            "https://www.sec.gov/Archives/edgar/data/1046179/"
            "000104617926000536/0001046179-26-000536-index.htm"
        ),
    }
    value.update(changes)
    return value


def urls_for(value):
    return MODULE.source_urls("0001046179", value)


def no_exhibit_sources(value, primary=None):
    urls = urls_for(value)
    primary = primary if primary is not None else FIXTURE.read_bytes()
    primary_name = MODULE._document_name(urls["primary"])
    form = value["form"]
    return {
        urls["submission"]: (
            f"<SEC-DOCUMENT><DOCUMENT><TYPE>{form}\n<FILENAME>{primary_name}\n"
            f"<TEXT>x</TEXT></DOCUMENT></SEC-DOCUMENT>"
        ).encode(),
        urls["index"]: (
            f"<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>"
            f"<tr><td>1</td><td>Primary</td><td><a href='{primary_name}'>{primary_name}</a></td>"
            f"<td>{form}</td></tr></table>"
        ).encode(),
        urls["primary"]: primary,
    }


class Fetcher:
    def __init__(self, mapping):
        self.mapping = mapping
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        value = self.mapping[url]
        if isinstance(value, Exception):
            raise value
        return value


class SecFilingContentTest(unittest.TestCase):
    def setUp(self):
        self.contract = MODULE.load_contract(CONTRACT_PATH)

    def capture(self, value=None, *, stage="Ready", sources=None, existing=None, force=False):
        value = value or filing()
        fetcher = Fetcher(sources or no_exhibit_sources(value))
        result, raw = MODULE.capture_filing(
            ticker="TSM",
            cik="0001046179",
            stage=stage,
            filing=value,
            fetcher=fetcher,
            retrieved_at_utc="2026-08-20T00:00:00Z",
            contract=self.contract,
            existing_manifest=existing,
            force_refresh=force,
        )
        return result, raw, fetcher

    def test_contract_authority_and_stage_form_scope_are_fail_closed(self):
        authority = self.contract["authority"]
        self.assertEqual(
            authority,
            {
                "evidence_only": True,
                "interpretation_authorized": False,
                "rule_evaluation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
        )
        for stage in ("Ready", "Buy", "Holding", "Candidate"):
            self.assertEqual(
                MODULE.filing_plan(filing(), stage, self.contract)["capture_policy"],
                "required",
            )
        self.assertEqual(
            MODULE.filing_plan(filing(), "Discovery", self.contract)["capture_policy"],
            "best_effort",
        )
        no_stage = MODULE.filing_plan(filing(), None, self.contract)
        self.assertEqual(no_stage["content_status"], "NOT_APPLICABLE")
        self.assertEqual(no_stage["capture_policy"], "index_only")

        ownership = MODULE.filing_plan(filing(form="4"), "Ready", self.contract)
        self.assertEqual(ownership["form_classification"], "OUT_OF_SCOPE_FOR_AUTO_CONSUMPTION")
        self.assertEqual(ownership["content_status"], "NOT_APPLICABLE")
        self.assertEqual(
            MODULE.form_class("10-K/A", self.contract), "MATERIAL"
        )

    def test_fixture_is_tamper_evident_and_extracts_three_currency_separated_values(self):
        manifest = json.loads(FIXTURE_MANIFEST.read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
            manifest["fixture_sha256"],
        )
        result, raw, fetcher = self.capture()
        self.assertEqual(result["content_status"], "OK")
        self.assertEqual(result["evidence_status"], "OK")
        self.assertEqual(result["interpretation_status"], "UNDETERMINED")
        self.assertEqual(result["rule_impact"], "NONE")
        self.assertEqual(result["action"], "NO_CHANGE")
        self.assertEqual(len(fetcher.calls), 3)
        self.assertEqual(set(raw), {"tsm-boardx20260811.htm"})
        self.assertEqual(
            set(result["identity_evidence"]), {"full_submission", "filing_index"}
        )
        self.assertTrue(
            all(
                len(item["content_sha256"]) == 64
                for item in result["identity_evidence"].values()
            )
        )

        by_label = {item["label"]: item for item in result["extracted"]}
        self.assertEqual(
            (by_label["capital_appropriations"]["value"], by_label["capital_appropriations"]["currency"]),
            ("29442.50", "USD"),
        )
        self.assertEqual(
            (by_label["sony_jv_subscription_cap"]["value"], by_label["sony_jv_subscription_cap"]["currency"]),
            ("282", "JPY"),
        )
        self.assertEqual(
            (by_label["cash_dividend_per_share"]["value"], by_label["cash_dividend_per_share"]["currency"]),
            ("7.0", "TWD"),
        )
        text = MODULE.normalized_visible_text(FIXTURE.read_bytes())
        for item in result["extracted"]:
            start = item["char_offset"]
            self.assertEqual(text[start : start + len(item["quote"])], item["quote"])
            self.assertIn(item["raw_value"], item["quote"])

    def test_ex99_is_discovered_from_sgml_and_cross_checked_with_index(self):
        value = filing(accession="0001046179-26-000600")
        value["url"] = value["url"].replace("000536", "000600").replace(
            "tsm-boardx20260811.htm", "cover.htm"
        )
        value["index_url"] = value["index_url"].replace("000536", "000600")
        urls = urls_for(value)
        sources = {
            urls["submission"]: (
                b"<SEC-DOCUMENT><DOCUMENT><TYPE>6-K\n<FILENAME>cover.htm\n<TEXT>x</TEXT></DOCUMENT>"
                b"<DOCUMENT><TYPE>EX-99.1\n<FILENAME>release.htm\n<TEXT>y</TEXT></DOCUMENT></SEC-DOCUMENT>"
            ),
            urls["index"]: (
                b"<table><tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>"
                b"<tr><td>1</td><td>Primary</td><td><a href='cover.htm'>cover.htm</a></td><td>6-K</td></tr>"
                b"<tr><td>2</td><td>Release</td><td><a href='release.htm'>release.htm</a></td><td>EX-99.1</td></tr></table>"
            ),
            urls["primary"]: b"<html><body>cover only</body></html>",
            f"{urls['base']}/release.htm": b"<html><body>release</body></html>",
        }
        result, raw, fetcher = self.capture(value, sources=sources)
        self.assertEqual(result["content_status"], "OK")
        self.assertEqual(result["evidence_status"], "PENDING")
        self.assertEqual(result["reasons"], ["EXTRACTOR_NOT_REGISTERED"])
        self.assertEqual({d["kind"] for d in result["documents"]}, {"primary", "exhibit"})
        self.assertEqual(set(raw), {"cover.htm", "release.htm"})
        self.assertEqual(len(fetcher.calls), 4)

    def test_archive_directory_is_compact_but_submission_filename_keeps_hyphens(self):
        urls = urls_for(filing())
        self.assertEqual(
            urls["base"],
            "https://www.sec.gov/Archives/edgar/data/1046179/000104617926000536",
        )
        self.assertEqual(
            urls["submission"],
            "https://www.sec.gov/Archives/edgar/data/1046179/000104617926000536/0001046179-26-000536.txt",
        )
        wrong = filing(
            url="https://www.sec.gov/Archives/edgar/data/1046179/000104617926000539/other.htm"
        )
        with self.assertRaisesRegex(MODULE.SecContentError, "PRIMARY_IDENTITY_PATH_MISMATCH"):
            urls_for(wrong)

        inline = (
            b"<tr><td>2</td><td>x</td><td><a href='https://www.sec.gov/ixviewer/doc/action?doc=/Archives/edgar/data/1/2/release.htm'>release</a></td><td>EX-99.1</td></tr>"
        )
        self.assertEqual(MODULE.parse_index_types(inline), {"release.htm": "EX-99.1"})

    def test_identity_ambiguity_oversize_and_missing_value_never_create_evidence(self):
        value = filing()
        urls = urls_for(value)
        conflicting = no_exhibit_sources(value)
        conflicting[urls["submission"]] = (
            b"<DOCUMENT><TYPE>6-K\n<FILENAME>tsm-boardx20260811.htm\n<TEXT>x</TEXT></DOCUMENT>"
            b"<DOCUMENT><TYPE>EX-99.1\n<FILENAME>release.htm\n<TEXT>x</TEXT></DOCUMENT>"
        )
        conflicting[urls["index"]] = (
            b"<tr><td>1</td><td>x</td><td><a href='tsm-boardx20260811.htm'>tsm-boardx20260811.htm</a></td><td>6-K</td></tr>"
            b"<tr><td>2</td><td>x</td><td><a href='release.htm'>release.htm</a></td><td>EX-99.2</td></tr>"
        )
        result, raw, _ = self.capture(sources=conflicting)
        self.assertEqual(result["content_status"], "PENDING")
        self.assertEqual(result["evidence_status"], "PENDING")
        self.assertIn("EXHIBIT_IDENTITY_CONFLICT", result["reasons"][0])
        self.assertEqual(raw, {})

        oversize_contract = copy.deepcopy(self.contract)
        oversize_contract["document_policy"]["max_document_bytes"] = 4
        fetcher = Fetcher(no_exhibit_sources(value))
        result, raw = MODULE.capture_filing(
            ticker="TSM",
            cik="0001046179",
            stage="Ready",
            filing=value,
            fetcher=fetcher,
            retrieved_at_utc="2026-08-20T00:00:00Z",
            contract=oversize_contract,
        )
        self.assertEqual(result["content_status"], "PENDING")
        self.assertIn("DOCUMENT_OVERSIZE", result["reasons"][0])
        self.assertEqual(raw, {})

        missing = FIXTURE.read_bytes().replace(b"NT$7.0", b"NT$X")
        result, raw, _ = self.capture(sources=no_exhibit_sources(value, missing))
        self.assertEqual(result["content_status"], "OK")
        self.assertEqual(result["evidence_status"], "FAILED")
        self.assertIn("EXTRACTION_CARDINALITY:cash_dividend_per_share:0", result["reasons"][0])
        self.assertEqual(set(raw), {"tsm-boardx20260811.htm"})

    def test_already_captured_skips_without_fetch_and_source_mutation_never_overwrites(self):
        first, raw, _ = self.capture()
        fetcher = Fetcher({})
        skipped, skipped_raw = MODULE.capture_filing(
            ticker="TSM",
            cik="0001046179",
            stage="Ready",
            filing=filing(),
            fetcher=fetcher,
            retrieved_at_utc="2026-08-20T01:00:00Z",
            contract=self.contract,
            existing_manifest=first,
        )
        self.assertEqual(skipped["operation"], "skipped")
        self.assertEqual(skipped["skip_reason"], "already_captured")
        self.assertEqual(skipped["raw_cache_policy"], "permanent")
        self.assertEqual(fetcher.calls, [])
        self.assertEqual(skipped_raw, {})

        discovery = copy.deepcopy(first)
        discovery["atlas_stage"] = "Discovery"
        discovery["raw_cache_policy"] = "delete_after_90_days_allowed"
        promoted, _ = MODULE.capture_filing(
            ticker="TSM",
            cik="0001046179",
            stage="Ready",
            filing=filing(),
            fetcher=Fetcher({}),
            retrieved_at_utc="2026-08-20T01:00:00Z",
            contract=self.contract,
            existing_manifest=discovery,
        )
        self.assertEqual(promoted["atlas_stage"], "Ready")
        self.assertEqual(promoted["raw_cache_policy"], "permanent")

        mutated_sources = no_exhibit_sources(
            filing(), FIXTURE.read_bytes().replace(b"US$29,442.50", b"US$29,442.51")
        )
        mutated, mutated_raw, _ = self.capture(
            sources=mutated_sources, existing=first, force=True
        )
        self.assertEqual(mutated["content_status"], "PENDING")
        self.assertTrue(mutated["reasons"][0].startswith("SOURCE_MUTATED"))
        self.assertEqual(mutated_raw, {})

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            MODULE.persist_success(root, first, raw)
            directory = MODULE.manifest_dir(root, "TSM", filing()["accession"])
            manifest_before = (directory / "_manifest.json").read_bytes()
            cache_before = (directory / "tsm-boardx20260811.htm.gz").read_bytes()
            changed = copy.deepcopy(first)
            changed_raw = b"changed"
            changed["documents"][0]["content_sha256"] = hashlib.sha256(changed_raw).hexdigest()
            with self.assertRaisesRegex(
                MODULE.SecContentError, "SOURCE_MUTATED_FAIL_CLOSED_NO_OVERWRITE"
            ):
                MODULE.persist_success(root, changed, {"tsm-boardx20260811.htm": changed_raw})
            self.assertEqual((directory / "_manifest.json").read_bytes(), manifest_before)
            self.assertEqual((directory / "tsm-boardx20260811.htm.gz").read_bytes(), cache_before)
            self.assertEqual(gzip.decompress(cache_before), FIXTURE.read_bytes())

    def test_run_is_temp_isolated_date_guarded_and_publishes_failure_truth(self):
        def tracked_snapshot():
            data_root = ROOT / "data"
            paths = []
            content_root = data_root / "sec_content"
            if content_root.exists():
                paths.extend(
                    path for path in content_root.rglob("*") if path.is_file()
                )
            latest = data_root / "latest_sec_content.json"
            if latest.exists():
                paths.append(latest)
            return {
                path.relative_to(data_root).as_posix(): path.read_bytes()
                for path in sorted(paths)
            }

        tracked_before = tracked_snapshot()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "latest_sec.json"
            source.write_text(
                json.dumps(
                    {
                        "collected_for_kst_date": "2026-08-20",
                        "stocks": {
                            "TSM": {
                                "status": "ok",
                                "cik": "0001046179",
                                "atlas_stage": "Ready",
                                "filings_recent": [filing()],
                            }
                        },
                    }
                ),
                encoding="utf-8",
            )
            fetcher = Fetcher(no_exhibit_sources(filing()))
            run = MODULE.run_capture(
                source_path=source,
                data_root=root / "data",
                expected_kst_date="2026-08-20",
                retrieved_at_utc="2026-08-20T00:00:00Z",
                fetcher=fetcher,
                contract=self.contract,
            )
            self.assertEqual(run["counts"], {"captured": 1, "skipped": 0, "failed": 0, "not_applicable": 0})
            self.assertTrue((root / "data" / "latest_sec_content.json").is_file())
            self.assertEqual(tracked_snapshot(), tracked_before)

            second_fetcher = Fetcher({})
            second = MODULE.run_capture(
                source_path=source,
                data_root=root / "data",
                expected_kst_date="2026-08-20",
                retrieved_at_utc="2026-08-20T01:00:00Z",
                fetcher=second_fetcher,
                contract=self.contract,
            )
            self.assertEqual(second["counts"]["skipped"], 1)
            self.assertEqual(second_fetcher.calls, [])
            stored = json.loads(
                (
                    root
                    / "data"
                    / "sec_content"
                    / "TSM"
                    / filing()["accession"]
                    / "_manifest.json"
                ).read_text(encoding="utf-8")
            )
            self.assertEqual(stored["operation"], "skipped")

            with self.assertRaisesRegex(MODULE.SecContentError, "SOURCE_DATE_MISMATCH"):
                MODULE.run_capture(
                    source_path=source,
                    data_root=root / "other",
                    expected_kst_date="2026-08-21",
                    retrieved_at_utc="2026-08-21T00:00:00Z",
                    fetcher=Fetcher({}),
                    contract=self.contract,
                )

            failure_root = root / "failure"
            with mock.patch.dict(
                MODULE.os.environ,
                {"SEC_USER_AGENT": "Atlas test@example.com"},
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
                (failure_root / "latest_sec_content.json").read_text(encoding="utf-8")
            )
            self.assertEqual(failure["run_status"], "FAILED")
            self.assertEqual(failure["counts"]["failed"], 1)
            self.assertIn("SOURCE_DATE_MISMATCH", failure["reasons"][0])
            self.assertEqual(tracked_snapshot(), tracked_before)

    def test_workflow_is_always_repairable_and_commits_content_state(self):
        workflow = WORKFLOW.read_text(encoding="utf-8")
        block = workflow.split("- name: Capture SEC filing content (P4-02)", 1)[1].split(
            "- name: Build briefing read model (P0-03)", 1
        )[0]
        self.assertIn("if: always()", block)
        self.assertIn("continue-on-error: true", block)
        self.assertIn("--expected-kst-date", block)
        self.assertIn("SEC_USER_AGENT", block)
        commit = workflow.split("- name: Commit data", 1)[1]
        self.assertIn("git add data/", commit)


if __name__ == "__main__":
    unittest.main()
