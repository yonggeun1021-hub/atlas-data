#!/usr/bin/env python3
"""P0-04 KRX post-close observation and workflow contract regression.

No live KRX or Notion calls are made.  Publication tests use isolated temporary
data roots and must not modify the tracked morning or briefing bundles.
"""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "collectors" / "krx_post_close.py"
WORKFLOW = ROOT / ".github" / "workflows" / "krx-post-close.yml"
DAILY_WORKFLOW = ROOT / ".github" / "workflows" / "collect.yml"

SPEC = importlib.util.spec_from_file_location("krx_post_close", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

with WORKFLOW.open(encoding="utf-8") as stream:
    WF = yaml.safe_load(stream)
with DAILY_WORKFLOW.open(encoding="utf-8") as stream:
    DAILY_WF = yaml.safe_load(stream)

STEPS = WF["jobs"]["observe"]["steps"]


def workflow_step(name):
    for item in STEPS:
        if item.get("name") == name:
            return item
    return None


def daily(today="2026-08-19", *, missing_value_column=False):
    investors = {
        "기관합계": 10,
        "외국인합계": 20,
        "개인": -25,
        "기타법인": -5,
    }
    if missing_value_column:
        investors.pop("기관합계")
    return {
        "2026-08-18": {
            "close": 100,
            "open": 95,
            "high": 105,
            "low": 94,
            "volume": 900,
            "change_pct": 1.0,
            "net_value": {name: 0 for name in MODULE.BASIC_INVESTORS},
            "net_volume": {name: 0 for name in MODULE.BASIC_INVESTORS},
            "investor_rows_absent": [],
            "observed_at_kst": "2026-08-19T06:10:00+09:00",
            "confirmed": True,
            "confirm_reason": "prior_session",
        },
        today: {
            "close": 110,
            "open": 101,
            "high": 112,
            "low": 99,
            "volume": 1200,
            "change_pct": 10.0,
            "net_value": investors,
            "net_volume": {
                "기관합계": 1,
                "외국인합계": 2,
                "개인": -2,
                "기타법인": -1,
            },
            "net_value_detail": {},
            "net_volume_detail": {},
            "investor_rows_absent": [],
            "observed_at_kst": f"{today}T16:10:00+09:00",
            "confirmed": False,
            "confirm_reason": "deferred_to_next_day",
        },
    }


def stock(code, today="2026-08-19", **kwargs):
    return {
        "name": f"stock-{code}",
        "atlas_stage": None,
        "status": "ok",
        "daily": daily(today, **kwargs),
        "latest_trading_day": "2026-08-18",
        "latest_observed_day": today,
        "unconfirmed_days": [today],
        "decision_ready": True,
        "sma20": 90.5,
        "sma20_basis": 20,
        "sma20_through": "2026-08-18",
        "sma20_status": "ok",
        "missing_investors": [],
        "investor_rows_missing": [],
        "investor_rows_missing_by_source": {},
    }


def source(today="2026-08-19"):
    stocks = {
        "005930": stock("005930", today),
        "000660": stock("000660", today),
    }
    return {
        "collected_at_utc": f"{today}T07:10:00+00:00",
        "collected_at_kst": f"{today}T16:10:00+09:00",
        "collected_for_kst_date": today,
        "source": "KRX 정보데이터시스템 (pykrx)",
        "source_tier": "Official",
        "collector_version": "v4.1",
        "same_day_confirmation": "next_day",
        "stocks": stocks,
        "summary": {"ok": len(stocks), "failed": 0},
        "decision_readiness": {
            "confirmed_through": "2026-08-18",
            "same_day_confirmation": "next_day",
        },
    }


class P004KrxPostCloseTest(unittest.TestCase):
    def require_step(self, name):
        found = workflow_step(name)
        self.assertIsNotNone(found, f"missing workflow step: {name}")
        return found

    def test_symbol_view_is_observed_and_structurally_ineligible(self):
        _, index, views = MODULE.build_bundle(source(), "2026-08-19")
        view = views["005930"]
        row = view["observed_row"]

        self.assertEqual(index["status"], "ready_observed_unconfirmed")
        self.assertFalse(index["decision_eligible"])
        self.assertEqual(view["latest_observed_day"], "2026-08-19")
        self.assertEqual(view["latest_trading_day"], "2026-08-18")
        self.assertEqual(row["observation_status"], "observed_unconfirmed")
        self.assertFalse(row["decision_eligible"])
        self.assertFalse(row["confirmed"])
        self.assertEqual(row["confirm_reason"], "deferred_to_next_day")
        self.assertNotIn("close", view)
        self.assertEqual(row["close"], 110)
        self.assertEqual(
            view["decision_boundary"]["sma20_through"],
            view["latest_trading_day"],
        )

    def test_source_sha_is_exact_file_lineage(self):
        raw, index, views = MODULE.build_bundle(source(), "2026-08-19")
        expected = hashlib.sha256(raw).hexdigest()

        self.assertEqual(index["source"]["source_snapshot_sha256"], expected)
        for view in views.values():
            self.assertEqual(
                view["observed_row"]["source_snapshot_sha256"], expected
            )
            self.assertEqual(
                view["source"]["source_snapshot_sha256"], expected
            )

    def test_publication_is_atomic_and_does_not_touch_morning_authority(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            latest = data_root / "latest_krx.json"
            morning = data_root / "2026-08-19" / "krx.json"
            latest.parent.mkdir(parents=True)
            morning.parent.mkdir(parents=True)
            latest.write_bytes(b"morning-latest\n")
            morning.write_bytes(b"morning-archive\n")

            target = MODULE.publish_bundle(
                source(), "2026-08-19", data_root=data_root
            )

            self.assertEqual(latest.read_bytes(), b"morning-latest\n")
            self.assertEqual(morning.read_bytes(), b"morning-archive\n")
            self.assertTrue((target / "source.json").is_file())
            self.assertTrue((target / "index.json").is_file())
            self.assertTrue((target / "symbols" / "005930.json").is_file())
            self.assertTrue(MODULE.check_bundle("2026-08-19", data_root))
            self.assertFalse(
                list((data_root / "observations").glob(".*.tmp.*"))
            )

    def test_same_date_bundle_is_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            target = MODULE.publish_bundle(
                source(), "2026-08-19", data_root=data_root
            )
            before = {
                path.relative_to(target): path.read_bytes()
                for path in target.rglob("*")
                if path.is_file()
            }

            with self.assertRaisesRegex(
                MODULE.PostCloseError, "APPEND_ONLY_VIOLATION"
            ):
                MODULE.publish_bundle(
                    source(), "2026-08-19", data_root=data_root
                )

            after = {
                path.relative_to(target): path.read_bytes()
                for path in target.rglob("*")
                if path.is_file()
            }
            self.assertEqual(after, before)

    def test_missing_exact_day_or_investor_column_fails_closed(self):
        no_day = source()
        no_day["stocks"]["005930"]["daily"].pop("2026-08-19")
        missing_column = source()
        missing_column["stocks"]["005930"] = stock(
            "005930", missing_value_column=True
        )

        cases = (
            (no_day, "EXACT_DAY_ROW_MISSING"),
            (missing_column, "INVESTOR_COLUMNS_MISSING"),
        )
        for payload, error in cases:
            with self.subTest(error=error):
                with self.assertRaisesRegex(MODULE.PostCloseError, error):
                    MODULE.build_bundle(payload, "2026-08-19")

    def test_invalid_symbol_path_and_missing_credentials_fail_closed(self):
        invalid_code = source()
        invalid_code["stocks"]["../../outside"] = invalid_code["stocks"].pop(
            "005930"
        )

        with self.assertRaisesRegex(
            MODULE.PostCloseError, "INVALID_KRX_CODE"
        ):
            MODULE.build_bundle(invalid_code, "2026-08-19")

        with mock.patch.dict(
            os.environ, {"KRX_ID": "", "KRX_PW": ""}, clear=False
        ):
            with self.assertRaisesRegex(
                MODULE.PostCloseError, "KRX_CREDENTIALS_MISSING"
            ):
                MODULE.collect_source(MODULE.parse_date("2026-08-19"))

    def test_partial_response_writes_unknown_incident_not_bundle(self):
        partial = source()
        partial["stocks"]["005930"] = {
            "name": "stock-005930",
            "status": "FAILED",
            "error": "ConnectionError",
        }
        partial["summary"] = {"ok": 1, "failed": 1}

        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            env = {"GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2"}
            with mock.patch.object(MODULE, "collect_source", return_value=partial):
                with self.assertRaisesRegex(
                    MODULE.PostCloseError, "PARTIAL_SOURCE_RESPONSE"
                ):
                    MODULE.run_collection(
                        "2026-08-19", data_root=data_root, environ=env
                    )

            self.assertFalse(
                (data_root / "observations" / "krx_post_close").exists()
            )
            incident = (
                data_root
                / "incident"
                / "krx_post_close"
                / "2026-08-19"
                / "run-123-attempt-2.json"
            )
            payload = json.loads(incident.read_text(encoding="utf-8"))
            self.assertEqual(payload["status"], "unknown")
            self.assertFalse(payload["decision_eligible"])

    def test_guard_rejects_tampered_decision_eligibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_root = Path(tmp) / "data"
            target = MODULE.publish_bundle(
                source(), "2026-08-19", data_root=data_root
            )
            path = target / "symbols" / "005930.json"
            view = json.loads(path.read_text(encoding="utf-8"))
            view["observed_row"]["decision_eligible"] = True
            path.write_text(json.dumps(view), encoding="utf-8")

            self.assertFalse(MODULE.check_bundle("2026-08-19", data_root))

    def test_workflow_has_separate_three_slot_post_close_schedule(self):
        triggers = WF.get("on", WF.get(True))
        schedules = {item["cron"] for item in triggers["schedule"]}
        daily_triggers = DAILY_WF.get("on", DAILY_WF.get(True))
        daily_schedules = {
            item["cron"] for item in daily_triggers["schedule"]
        }

        self.assertEqual(
            schedules,
            {"5 7 * * 1-5", "25 7 * * 1-5", "45 7 * * 1-5"},
        )
        self.assertEqual(
            daily_schedules,
            {"55 20 * * 0-4", "15 21 * * 0-4", "35 21 * * 0-4"},
        )
        self.assertNotEqual(
            WF["concurrency"]["group"], DAILY_WF["concurrency"]["group"]
        )

    def test_workflow_guard_and_commit_are_scoped_to_post_close_paths(self):
        guard = self.require_step("Guard — 오늘자 post-close bundle 확인")
        collect = self.require_step(
            "Collect and publish KRX post-close observation"
        )
        commit = self.require_step("Commit post-close observation evidence")

        self.assertIn("--check", guard.get("run", ""))
        self.assertEqual(
            collect.get("if"),
            "steps.guard.outcome == 'success' && steps.guard.outputs.skip != 'yes'",
        )
        self.assertIn("krx_post_close.py", collect.get("run", ""))
        self.assertEqual(commit.get("if"), "always()")
        command = commit.get("run", "")
        self.assertIn("data/observations/krx_post_close", command)
        self.assertIn("data/incident/krx_post_close", command)
        self.assertIn("data/operations/krx_post_close_runs", command)
        self.assertIn('if [ -d "$evidence_path" ]', command)
        self.assertIn("successful collection produced no staged", command)
        self.assertNotIn("|| true", command)
        self.assertEqual(
            commit.get("env", {}).get("GUARD_SKIP"),
            "${{ steps.guard.outputs.skip }}",
        )
        self.assertEqual(
            commit.get("env", {}).get("COLLECT_OUTCOME"),
            "${{ steps.post_close.outcome }}",
        )
        self.assertNotIn("latest_krx.json", command)
        self.assertNotIn("data/briefing", command)
        self.assertIn('git pull --rebase origin "$DEFAULT_BRANCH"', command)
        self.assertIn('git push origin "HEAD:$DEFAULT_BRANCH"', command)


if __name__ == "__main__":
    unittest.main()
