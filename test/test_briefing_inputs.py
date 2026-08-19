#!/usr/bin/env python3

import hashlib
import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / ".github/scripts/build_briefing_inputs.py"
DATA = ROOT / "data"


class BriefingInputsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location(
            "build_briefing_inputs_test",
            BUILDER,
        )
        cls.module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.module)

        cls.tempdir = tempfile.TemporaryDirectory()
        cls.data = Path(cls.tempdir.name) / "data"
        cls.out = cls.data / "briefing"
        cls.health = cls.data / "briefing_status.json"
        cls.data.mkdir()

        for name in ("krx", "dart", "sec"):
            shutil.copy2(
                DATA / f"latest_{name}.json",
                cls.data / f"latest_{name}.json",
            )

        cls.module.DATA = cls.data
        cls.module.OUT = cls.out
        cls.module.HEALTH = cls.health

        krx = json.loads((cls.data / "latest_krx.json").read_text())
        cls.today = krx["collected_for_kst_date"]
        cls.module.run(cls.today)

    @classmethod
    def tearDownClass(cls):
        cls.tempdir.cleanup()

    def test_step0_summary_matches_sources(self):
        status = json.loads((self.out / "step0_status.json").read_text())

        total_ok = 0
        total_failed = 0

        for name in ("krx", "dart", "sec"):
            src = json.loads((self.data / f"latest_{name}.json").read_text())
            self.assertEqual(
                status["collectors"][name]["ok"],
                src["summary"]["ok"],
            )
            self.assertEqual(
                status["collectors"][name]["failed"],
                src["summary"]["failed"],
            )
            total_ok += src["summary"]["ok"]
            total_failed += src["summary"]["failed"]

        self.assertEqual(status["totals"]["ok"], total_ok)
        self.assertEqual(status["totals"]["failed"], total_failed)
        self.assertEqual(status["overall"], "pass")
        self.assertEqual(
            status["optional_evidence"]["sec_content"]["status"],
            "missing",
        )
        self.assertEqual(
            status["optional_evidence"]["dart_content"]["status"],
            "missing",
        )

    def test_krx_tail_symbols_have_exact_views(self):
        for code in ("000660", "005930"):
            p = self.out / "krx" / f"{code}.json"
            self.assertTrue(p.exists(), code)
            obj = json.loads(p.read_text())
            self.assertEqual(obj["symbol"], code)
            self.assertIsNotNone(obj["latest_confirmed_day"])
            self.assertIsInstance(obj["latest_confirmed_row"], dict)

    def test_krx_compact_preserves_confirmed_sma20_contract(self):
        source = json.loads(
            (self.data / "latest_krx.json").read_text()
        )

        for code, stock in source["stocks"].items():
            compact = json.loads(
                (self.out / "krx" / f"{code}.json").read_text()
            )
            metrics = compact["confirmed_metrics"]

            self.assertEqual(compact["schema_version"], 2)
            self.assertEqual(metrics["history_basis"], "confirmed_only")
            self.assertEqual(metrics["status"], stock["sma20_status"])
            self.assertEqual(metrics["sma20"], stock["sma20"])
            self.assertEqual(
                metrics["sma20_basis"],
                stock["sma20_basis"],
            )
            self.assertEqual(
                metrics["sma20_through"],
                stock["sma20_through"],
            )
            self.assertEqual(
                metrics["sma20_through"],
                compact["latest_confirmed_day"],
            )

    def test_krx_compact_has_authoritative_investor_completeness(self):
        source = json.loads(
            (self.data / "latest_krx.json").read_text()
        )

        for code, stock in source["stocks"].items():
            compact = json.loads(
                (self.out / "krx" / f"{code}.json").read_text()
            )
            completeness = compact["investor_data_completeness"]

            expected_complete = not (
                stock["missing_investors"]
                or stock["investor_rows_missing"]
                or any(stock["investor_rows_missing_by_source"].values())
            )
            self.assertEqual(
                completeness["complete"],
                expected_complete,
            )
            self.assertEqual(
                completeness["status"],
                "complete" if expected_complete else "incomplete",
            )
            self.assertEqual(
                completeness["missing_investors"],
                sorted(stock["missing_investors"]),
            )
            self.assertEqual(
                completeness["investor_rows_missing"],
                sorted(stock["investor_rows_missing"]),
            )
            self.assertEqual(
                completeness["investor_rows_missing_by_source"],
                {
                    source_name: sorted(days)
                    for source_name, days in sorted(
                        stock["investor_rows_missing_by_source"].items()
                    )
                },
            )

    def test_krx_missing_source_contract_is_unknown_not_complete(self):
        metrics = self.module.krx_confirmed_metrics({}, "2026-08-18")
        completeness = self.module.krx_investor_data_completeness({})

        self.assertEqual(metrics["status"], "unknown")
        self.assertIsNone(metrics["sma20"])
        self.assertEqual(completeness["status"], "unknown")
        self.assertFalse(completeness["complete"])

    def test_krx_reported_investor_gap_is_incomplete(self):
        completeness = self.module.krx_investor_data_completeness(
            {
                "missing_investors": ["기관합계"],
                "investor_rows_missing": [],
                "investor_rows_missing_by_source": {},
            }
        )

        self.assertEqual(completeness["status"], "incomplete")
        self.assertFalse(completeness["complete"])
        self.assertEqual(
            completeness["reason"],
            "reported_missing_investor_data",
        )

    def test_source_hash_matches(self):
        krx_hash = hashlib.sha256(
            (self.data / "latest_krx.json").read_bytes()
        ).hexdigest()

        samsung = json.loads(
            (self.out / "krx" / "005930.json").read_text()
        )

        self.assertEqual(
            samsung["source"]["source_sha256"],
            krx_hash,
        )

    def test_scheduled_collector_inventory_has_date_basis(self):
        status = json.loads((self.out / "step0_status.json").read_text())

        inventory = {
            x["name"]: x
            for x in status["scheduled_collectors"]
        }

        self.assertEqual(
            inventory["daily_collect"]["date_basis"],
            "KST",
        )
        self.assertEqual(
            inventory["stablecoin_daily_capture"]["date_basis"],
            "UTC",
        )

    def test_read_model_inventory_has_exact_authority_paths(self):
        status = json.loads((self.out / "step0_status.json").read_text())

        self.assertEqual(
            status["read_model_inventory"],
            {
                "date_basis": "KST",
                "authority_path": "data/briefing/step0_status.json",
                "health_path": "data/briefing_status.json",
                "compact_path_templates": [
                    "data/briefing/krx/{SYMBOL}.json",
                    "data/briefing/dart/{SYMBOL}.json",
                    "data/briefing/sec/{SYMBOL}.json",
                ],
                "optional_evidence_sources": [
                    "data/latest_dart_content.json",
                    "data/latest_sec_content.json",
                ],
            },
        )

    def test_sec_compact_view_is_bounded(self):
        for path in (self.out / "sec").glob("*.json"):
            obj = json.loads(path.read_text())
            filings = obj["stock"].get("filings_recent", [])
            self.assertLessEqual(len(filings), 10)
            self.assertLess(path.stat().st_size, 32768)

    def test_dart_compact_view_is_bounded(self):
        source = json.loads((self.data / "latest_dart.json").read_text())
        for symbol in source["stocks"]:
            path = self.out / "dart" / f"{symbol}.json"
            self.assertTrue(path.is_file())
            obj = json.loads(path.read_text())
            self.assertEqual(obj["schema_version"], 2)
            self.assertEqual(obj["market"], "DART")
            self.assertLessEqual(len(obj["stock"].get("relevant", [])), 20)
            self.assertLess(path.stat().st_size, 32768)

    def test_dart_content_overlay_preserves_unratified_item_boundary(self):
        stock = {
            "name": "삼성전자",
            "relevant": [
                {
                    "date": "20260820",
                    "title": "단일판매ㆍ공급계약체결",
                    "rcept_no": "20260820800123",
                    "url": (
                        "https://dart.fss.or.kr/dsaf001/main.do?"
                        "rcpNo=20260820800123"
                    ),
                }
            ],
        }
        content = {
            ("005930", "20260820800123"): {
                "filing_identity": {"rcept_no": "20260820800123"},
                "filing_classification": "MATERIAL_RELEVANT_TITLE",
                "capture_policy": "required",
                "content_status": "OK",
                "evidence_status": "PENDING",
                "interpretation_status": "UNDETERMINED",
                "rule_impact": "NONE",
                "action": "NO_CHANGE",
                "reasons": ["ITEM_EXTRACTION_POLICY_UNRATIFIED"],
                "source_archive": {
                    "source_uri": (
                        "https://opendart.fss.or.kr/api/document.xml?"
                        "rcept_no=20260820800123"
                    ),
                    "content_sha256": "1" * 64,
                },
                "documents": [],
                "extracted": [],
            }
        }
        compact = self.module.compact_dart_stock(
            stock,
            symbol="005930",
            content=content,
            content_source={"source_file": "data/latest_dart_content.json"},
        )
        item = compact["relevant"][0]
        self.assertTrue(item["body_captured"])
        self.assertEqual(item["body_capture_status"], "OK")
        self.assertEqual(item["content"]["evidence_status"], "PENDING")
        self.assertEqual(
            item["content"]["interpretation_status"], "UNDETERMINED"
        )
        self.assertEqual(item["content"]["rule_impact"], "NONE")
        self.assertEqual(item["content"]["action"], "NO_CHANGE")

    def test_sec_content_overlay_replaces_legacy_false_without_claiming_interpretation(self):
        stock = {
            "name": "TSMC",
            "filings_recent": [
                {
                    "accession": "0001046179-26-000536",
                    "body_captured": False,
                    "body_capture_status": "Unimplemented",
                }
            ],
        }
        content = {
            ("TSM", "0001046179-26-000536"): {
                "filing_identity": {"accession": "0001046179-26-000536"},
                "content_status": "OK",
                "evidence_status": "OK",
                "interpretation_status": "UNDETERMINED",
                "rule_impact": "NONE",
                "action": "NO_CHANGE",
                "extracted": [
                    {
                        "label": "capital_appropriations",
                        "value": "29442.50",
                        "currency": "USD",
                        "quote": "US$29,442.50 million",
                        "char_offset": 10,
                    }
                ],
            }
        }
        compact = self.module.compact_sec_stock(
            stock,
            symbol="TSM",
            content=content,
            content_source={"source_file": "data/latest_sec_content.json"},
        )
        filing = compact["filings_recent"][0]
        self.assertTrue(filing["body_captured"])
        self.assertEqual(filing["body_capture_status"], "OK")
        self.assertEqual(filing["content"]["evidence_status"], "OK")
        self.assertEqual(
            filing["content"]["interpretation_status"], "UNDETERMINED"
        )
        self.assertEqual(filing["content"]["rule_impact"], "NONE")
        self.assertEqual(filing["content"]["action"], "NO_CHANGE")

    def test_invalid_json_fails_closed(self):
        spec = importlib.util.spec_from_file_location(
            "build_briefing_inputs",
            BUILDER,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        with tempfile.TemporaryDirectory() as td:
            broken = Path(td) / "latest_krx.json"
            broken.write_bytes(
                b'{"summary":{"ok":5,"failed":0},"stocks":{"005930":'
            )

            with self.assertRaisesRegex(
                RuntimeError,
                "incomplete_or_invalid_json",
            ):
                module.load_json(broken)

    def snapshot_bundle(self):
        snapshot = {}
        for path in sorted(self.out.rglob("*")):
            if path.is_file():
                snapshot[str(path.relative_to(self.out))] = hashlib.sha256(
                    path.read_bytes()
                ).hexdigest()
        return snapshot

    def test_failed_build_does_not_change_published_bundle(self):
        self.module.build_and_publish(self.today)
        before = self.snapshot_bundle()
        self.assertTrue(before)

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_failure_before_publish",
        ):
            self.module.build_and_publish(
                self.today,
                fail_before_publish=True,
            )

        after = self.snapshot_bundle()
        self.assertEqual(after, before)

    def test_successful_build_publishes_complete_bundle(self):
        status = self.module.build_and_publish(self.today)

        self.assertEqual(status["expected_kst_date"], self.today)
        self.assertTrue((self.out / "step0_status.json").is_file())
        self.assertTrue((self.out / "krx" / "005930.json").is_file())

        dart_views = list((self.out / "dart").glob("*.json"))
        self.assertTrue(dart_views)

        sec_views = list((self.out / "sec").glob("*.json"))
        self.assertTrue(sec_views)

    def test_read_model_failure_is_degraded_not_data_failure(self):
        self.module.build_and_publish(self.today)
        before = self.snapshot_bundle()

        with self.assertRaisesRegex(
            RuntimeError,
            "injected_failure_before_publish",
        ):
            self.module.run(
                self.today,
                fail_before_publish=True,
            )

        health = json.loads(
            self.health.read_text()
        )

        self.assertTrue(health["data_ready"])
        self.assertFalse(health["read_model_ready"])
        self.assertEqual(
            health["status"],
            "read_model_degraded",
        )
        self.assertEqual(
            self.snapshot_bundle(),
            before,
        )


if __name__ == "__main__":
    unittest.main()
