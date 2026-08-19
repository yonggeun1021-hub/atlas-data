#!/usr/bin/env python3
"""P1-KR-04 KRX/NXT investor-flow coverage regression.

All output files and synthetic snapshots are temporary.  No live market call,
credential, workflow mutation, or tracked data write is allowed.
"""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "collectors" / "krx_investor_flow.py"
CONTRACT_PATH = ROOT / "config" / "korea_investor_flow_contract.json"
SPEC = importlib.util.spec_from_file_location("krx_investor_flow_contract", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract(CONTRACT_PATH)


def confirmed_row(value=10, volume=1):
    return {
        "net_value": {
            "기관합계": value,
            "외국인합계": -value,
            "개인": 0,
            "기타법인": 0,
        },
        "net_volume": {
            "기관합계": volume,
            "외국인합계": -volume,
            "개인": 0,
            "기타법인": 0,
        },
        "investor_rows_absent": [],
        "observed_at_kst": "2026-08-19T06:50:31+09:00",
        "confirmed": True,
        "confirm_reason": "prior_session",
    }


def snapshot(stock_override=None):
    stock = {
        "name": "삼성전자",
        "status": "ok",
        "daily": {
            "2026-08-18": confirmed_row(),
            "2026-08-19": {
                **confirmed_row(20, 2),
                "confirmed": False,
                "confirm_reason": "deferred_to_next_day",
            },
        },
        "latest_trading_day": "2026-08-18",
        "latest_observed_day": "2026-08-19",
        "unconfirmed_days": ["2026-08-19"],
        "decision_ready": True,
        "missing_investors": [],
        "investor_rows_missing": [],
        "investor_rows_missing_by_source": {},
    }
    if stock_override:
        stock.update(stock_override)
    return {
        "collected_at_utc": "2026-08-18T21:50:31+00:00",
        "collected_at_kst": "2026-08-19T06:50:31+09:00",
        "collected_for_kst_date": "2026-08-19",
        "source": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
        "collector_version": "v4.1",
        "same_day_confirmation": "next_day",
        "investor_flow_coverage": MODULE.coverage_metadata(CONTRACT),
        "stocks": {"005930": stock},
        "summary": {"ok": 1, "failed": 0},
    }


def build(payload):
    raw = json.dumps(
        payload, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    return MODULE.build_report(
        payload,
        hashlib.sha256(raw).hexdigest(),
        CONTRACT,
    )


class KoreaInvestorFlowContractTest(unittest.TestCase):
    def test_contract_fixes_krx_only_nxt_exclusion_and_no_authority(self):
        metadata = MODULE.coverage_metadata(CONTRACT)

        self.assertEqual(metadata["market_venue_scope"], "KRX_ONLY")
        self.assertEqual(
            metadata["security_market_segment_status"],
            "not_recorded_in_payload",
        )
        self.assertFalse(metadata["nxt_included"])
        self.assertFalse(metadata["whole_korea_market_claim_authorized"])
        self.assertEqual(metadata["same_day_confirmation"], "next_day")
        self.assertEqual(metadata["source_release_time_status"], "unverified")
        self.assertIsNone(metadata["available_at"])
        self.assertFalse(metadata["decision_eligible"])
        self.assertFalse(metadata["regime_score_authorized"])
        self.assertFalse(metadata["production_wiring_authorized"])
        self.assertFalse(metadata["trading_action_authorized"])

    def test_contract_tampering_fails_closed(self):
        tampered = copy.deepcopy(CONTRACT)
        tampered["coverage"]["nxt_included"] = True

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "contract.json"
            path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.InvestorFlowContractError,
                "CONTRACT_INVALID",
            ):
                MODULE.load_contract(path)

    def test_complete_snapshot_preserves_raw_krx_values_but_denies_total_market(self):
        report = build(snapshot())
        stock = report["stocks"]["005930"]

        self.assertEqual(
            report["coverage_status"],
            "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
        )
        self.assertEqual(report["market_venue_scope"], "KRX_ONLY")
        self.assertFalse(report["nxt_included"])
        self.assertFalse(report["whole_korea_market_claim_authorized"])
        self.assertIsNone(report["available_at"])
        self.assertEqual(stock["status"], "OBSERVED_KRX_ONLY")
        self.assertEqual(stock["observation_date"], "2026-08-18")
        self.assertEqual(stock["flows"]["net_value"]["기관합계"], 10)
        self.assertEqual(stock["flows"]["net_value"]["외국인합계"], -10)
        self.assertEqual(stock["flows"]["net_value"]["개인"], 0)
        self.assertIn("VENUE_NOT_INCLUDED", stock["warnings"])
        self.assertFalse(stock["decision_eligible"])

    def test_row_column_and_venue_absence_are_three_different_states(self):
        row_missing = snapshot()
        row = row_missing["stocks"]["005930"]["daily"]["2026-08-18"]
        row["net_value"] = None
        row["investor_rows_absent"] = ["net_value"]
        row_report = build(row_missing)["stocks"]["005930"]

        column_missing = snapshot()
        column_missing["stocks"]["005930"]["daily"]["2026-08-18"][
            "net_value"
        ].pop("외국인합계")
        column_report = build(column_missing)["stocks"]["005930"]

        self.assertEqual(row_report["missing"]["source_rows"], ["net_value"])
        self.assertEqual(
            column_report["missing"]["investor_categories_by_source"],
            {"net_value": ["외국인합계"]},
        )
        self.assertEqual(
            CONTRACT["missing_policy"]["venue_not_covered"],
            "VENUE_NOT_INCLUDED",
        )
        self.assertNotIn(
            "VENUE_NOT_INCLUDED", row_report["missing"]["source_rows"]
        )
        self.assertNotIn(
            "VENUE_NOT_INCLUDED",
            column_report["missing"]["investor_categories_by_source"],
        )
        self.assertEqual(column_report["flows"]["net_value"]["개인"], 0)

    def test_only_prior_session_confirmed_row_is_eligible_for_observation(self):
        same_day = snapshot()
        same_day["stocks"]["005930"]["latest_trading_day"] = "2026-08-19"

        with self.assertRaisesRegex(
            MODULE.InvestorFlowContractError,
            "CONFIRMED_DAY_INVALID",
        ):
            build(same_day)

        bad_reason = snapshot()
        bad_reason["stocks"]["005930"]["daily"]["2026-08-18"][
            "confirm_reason"
        ] = "time_threshold"
        with self.assertRaisesRegex(
            MODULE.InvestorFlowContractError,
            "CONFIRMED_ROW_INVALID",
        ):
            build(bad_reason)

    def test_coverage_metadata_tampering_cannot_relabel_snapshot(self):
        cases = []
        nxt = snapshot()
        nxt["investor_flow_coverage"]["nxt_included"] = True
        cases.append(nxt)
        total = snapshot()
        total["investor_flow_coverage"][
            "whole_korea_market_claim_authorized"
        ] = True
        cases.append(total)
        available = snapshot()
        available["investor_flow_coverage"]["available_at"] = (
            "2026-08-19T06:50:31+09:00"
        )
        cases.append(available)

        for payload in cases:
            with self.subTest(payload=payload["investor_flow_coverage"]):
                with self.assertRaisesRegex(
                    MODULE.InvestorFlowContractError,
                    "COVERAGE_METADATA_INVALID",
                ):
                    build(payload)

    def test_failed_stock_is_undefined_not_zero_flow(self):
        payload = snapshot(
            {"status": "FAILED", "error": "ConnectionError: unavailable"}
        )
        payload["summary"] = {"ok": 0, "failed": 1}
        stock = build(payload)["stocks"]["005930"]

        self.assertEqual(stock["status"], "UNDEFINED")
        self.assertIsNone(stock["flows"]["net_value"])
        self.assertEqual(stock["missing"]["stock_collection"], "SOURCE_FAILED")
        self.assertIn("SOURCE_FAILED", stock["warnings"])

    def test_production_collector_embeds_exact_metadata_without_new_call(self):
        collector_dir = str(ROOT / "collectors")
        common = types.ModuleType("common")
        common.save = lambda *args, **kwargs: None
        common.save_incident = lambda *args, **kwargs: None
        common.load_universe = lambda: [
            {
                "code": "005930",
                "name": "삼성전자",
                "atlas_stage": None,
                "coverage": True,
                "db_state": None,
                "in_notion": True,
            }
        ]
        common.today_kst = lambda: __import__("datetime").date(2026, 8, 19)
        common.now_utc_iso = lambda: "2026-08-18T21:50:31+00:00"
        common.record_stage_snapshot = lambda *args, **kwargs: None
        common.stage_distribution = lambda *args, **kwargs: {}
        pykrx = types.ModuleType("pykrx")
        pykrx.stock = types.ModuleType("pykrx.stock")
        module_names = (
            "common",
            "pykrx",
            "pykrx.stock",
            "krx",
            "krx_investor_flow",
        )
        previous = {
            name: sys.modules.get(name)
            for name in module_names
        }
        previous_env = {
            name: os.environ.get(name) for name in ("KRX_ID", "KRX_PW")
        }
        added_path = collector_dir not in sys.path
        try:
            if added_path:
                sys.path.insert(0, collector_dir)
            sys.modules["common"] = common
            sys.modules["pykrx"] = pykrx
            sys.modules["pykrx.stock"] = pykrx.stock
            os.environ["KRX_ID"] = "test"
            os.environ["KRX_PW"] = "test"
            krx_spec = importlib.util.spec_from_file_location(
                "krx", ROOT / "collectors" / "krx.py"
            )
            krx = importlib.util.module_from_spec(krx_spec)
            krx_spec.loader.exec_module(krx)
            krx.collect_one = lambda *args, **kwargs: {
                "daily": {"2026-08-18": confirmed_row()},
                "latest_trading_day": "2026-08-18",
                "latest_observed_day": "2026-08-18",
                "unconfirmed_days": [],
                "decision_ready": True,
                "sma20": None,
                "sma20_basis": 1,
                "sma20_through": "2026-08-18",
                "sma20_status": "insufficient_confirmed_history",
                "missing_investors": [],
                "investor_rows_missing": [],
                "investor_rows_missing_by_source": {},
            }
            payload = krx.collect_payload(
                __import__("datetime").date(2026, 8, 19),
                record_stage=False,
            )
        finally:
            for name, value in previous.items():
                if value is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = value
            for name, value in previous_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
            if added_path:
                sys.path.remove(collector_dir)

        self.assertEqual(
            payload["investor_flow_coverage"],
            MODULE.coverage_metadata(CONTRACT),
        )
        self.assertEqual(payload["summary"], {"ok": 1, "failed": 0})

    def test_report_writer_forbids_tracked_and_existing_outputs(self):
        report = build(snapshot())
        with self.assertRaisesRegex(
            MODULE.InvestorFlowContractError,
            "TRACKED_OUTPUT_FORBIDDEN",
        ):
            MODULE.write_report(report, ROOT / "data" / "flow-report.json")

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "flow-report.json"
            MODULE.write_report(report, target)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                report,
            )
            with self.assertRaisesRegex(
                MODULE.InvestorFlowContractError,
                "OUTPUT_EXISTS",
            ):
                MODULE.write_report(report, target)

    def test_helper_has_no_network_or_secret_access(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("import requests", source)
        self.assertNotIn("import urllib", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("KRX_ID", source)
        self.assertNotIn("KRX_PW", source)
        self.assertNotIn("os.environ", source)


if __name__ == "__main__":
    unittest.main()
