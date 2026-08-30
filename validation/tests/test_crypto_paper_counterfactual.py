#!/usr/bin/env python3
"""P10-12 counterfactual validation scaffolding regression tests."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "validation" / "crypto_paper_counterfactual.py"
SPEC = importlib.util.spec_from_file_location("crypto_paper_counterfactual", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
SIM = MODULE.SIMULATOR
CONTRACT = MODULE.load_contract()


def _intent(*, order_id, key, side, submitted_at, expires_at):
    return SIM.build_intent(
        order_id=order_id,
        idempotency_key=key,
        market="KRW-BTC",
        side=side,
        order_type="MARKET",
        quantity="2",
        limit_price=None,
        fee_rate="0.001",
        queue_fraction="0.5" if side == "BUY" else "1",
        submitted_at=submitted_at,
        expires_at=expires_at,
        market_regime_status="PASS",
        source_plan_ref="fixture://plan/BTC.1",
        source_plan_sha256="a" * 64,
        source_evidence_ref="fixture://decision/BTC.1",
        source_evidence_sha256="b" * 64,
    )


def _snapshot(*, snapshot_id, captured_at, asks=None, bids=None):
    return SIM.build_snapshot(
        snapshot_id=snapshot_id,
        market="KRW-BTC",
        captured_at=captured_at,
        freshness_status="FRESH",
        ask_levels=asks or [
            {"price": "100", "quantity": "1"},
            {"price": "101", "quantity": "2"},
        ],
        bid_levels=bids or [
            {"price": "110", "quantity": "1"},
            {"price": "109", "quantity": "2"},
        ],
        source_ref=f"fixture://book/{snapshot_id}",
        source_sha256="c" * 64,
    )


def _ledger_and_account():
    ledger = SIM.create_ledger(
        ledger_id="PAPER.LEDGER.P1012",
        initial_cash="1000",
        opened_at="2026-08-30T00:00:00Z",
        idempotency_key="PAPER.ACCOUNT.P1012.OPEN",
    )
    buy = _intent(
        order_id="PAPER.BUY.P1012", key="PAPER.BUY.P1012.SUBMIT", side="BUY",
        submitted_at="2026-08-30T00:10:00Z", expires_at="2026-08-30T01:10:00Z",
    )
    ledger = SIM.submit_order(ledger, buy)
    ledger = SIM.match_order(
        ledger, order_id=buy["order_id"],
        snapshot=_snapshot(snapshot_id="BOOK.BUY.1", captured_at="2026-08-30T00:11:00Z"),
        event_at="2026-08-30T00:11:01Z", idempotency_key="PAPER.BUY.P1012.MATCH.1",
    )
    ledger = SIM.match_order(
        ledger, order_id=buy["order_id"],
        snapshot=_snapshot(
            snapshot_id="BOOK.BUY.2", captured_at="2026-08-30T00:12:00Z",
            asks=[{"price": "102", "quantity": "2"}],
        ),
        event_at="2026-08-30T00:12:01Z", idempotency_key="PAPER.BUY.P1012.MATCH.2",
    )
    sell = _intent(
        order_id="PAPER.SELL.P1012", key="PAPER.SELL.P1012.SUBMIT", side="SELL",
        submitted_at="2026-08-30T01:00:00Z", expires_at="2026-08-30T02:00:00Z",
    )
    ledger = SIM.submit_order(ledger, sell)
    ledger = SIM.match_order(
        ledger, order_id=sell["order_id"],
        snapshot=_snapshot(snapshot_id="BOOK.SELL.1", captured_at="2026-08-30T01:01:00Z"),
        event_at="2026-08-30T01:01:01Z", idempotency_key="PAPER.SELL.P1012.MATCH.1",
    )
    account = SIM.build_account_state(
        ledger,
        observed_at="2026-08-30T01:10:00Z",
        mark_prices={},
        mark_freshness_status="FRESH",
        mark_source_ref="fixture://marks/final",
        mark_source_sha256="d" * 64,
    )
    return ledger, account


def _gate(status="CLOSED"):
    opened = status == "OPEN"
    return {
        "gate_id": "P10.12.D0.GATE.V1",
        "status": status,
        "d0_date": "2026-08-30" if opened else None,
        "opened_at": "2026-08-30T00:00:00Z" if opened else None,
        "prerequisites": {
            key: opened for key in CONTRACT["official_gate_prerequisites"]
        },
        "authority": {
            "official_count_authorized": opened,
            "live_review_authorized": False,
            "exchange_order_authorized": False,
            "real_capital_authorized": False,
        },
    }


def _artifact(role, payload, origin, available_at="2026-08-30T02:00:00Z"):
    return {
        "role": role,
        "origin": origin,
        "source_ref": f"fixture://p10-12/{role.lower()}",
        "available_at": available_at,
        "payload": copy.deepcopy(payload),
        "payload_sha256": MODULE.payload_sha256(payload),
    }


def _batch(*, origin="SYNTHETIC_FIXTURE", gate_status="CLOSED",
           report_id="P10.12.DAILY.20260830", report_date="2026-08-30",
           generated_at="2026-08-30T03:00:00Z", previous=None):
    ledger, account = _ledger_and_account()
    assessments = [
        {
            "assessment_id": f"ASSESS.{metric}.1",
            "metric_type": metric,
            "status": "PRESENT" if metric == "DUPLICATE" else "ABSENT",
            "assessed_at": "2026-08-30T02:00:00Z",
            "evidence_ref": f"fixture://assessment/{metric}",
            "evidence_sha256": "e" * 64,
        }
        for metric in CONTRACT["error_metric_types"]
    ]
    artifacts = [
        _artifact("D0_GATE", _gate(gate_status), "CONTROL", "2026-08-30T00:00:00Z"),
        _artifact("LEDGER", ledger, origin),
        _artifact("ACCOUNT_STATE", account, origin),
        _artifact("NAV_SERIES", [
            {"observed_at": "2026-08-30T00:00:00Z", "available_at": "2026-08-30T00:00:01Z", "total_nav": "1000"},
            {"observed_at": "2026-08-30T00:30:00Z", "available_at": "2026-08-30T00:30:01Z", "total_nav": "980"},
            {"observed_at": "2026-08-30T01:10:00Z", "available_at": "2026-08-30T01:10:01Z", "total_nav": account["total_nav"]},
        ], origin),
        _artifact("MARK_SERIES", [
            {"market": "KRW-BTC", "observed_at": "2026-08-30T00:15:00Z", "available_at": "2026-08-30T00:15:01Z", "price": "105"},
            {"market": "KRW-BTC", "observed_at": "2026-08-30T00:30:00Z", "available_at": "2026-08-30T00:30:01Z", "price": "95"},
            {"market": "KRW-BTC", "observed_at": "2026-08-30T01:00:00Z", "available_at": "2026-08-30T01:00:01Z", "price": "110"},
        ], origin),
        _artifact("PLANNED_LOSS", [{
            "trade_id": "TRADE.BTC.1",
            "entry_order_id": "PAPER.BUY.P1012",
            "exit_order_ids": ["PAPER.SELL.P1012"],
            "planned_at": "2026-08-30T00:09:00Z",
            "planned_loss": "20",
            "source_plan_ref": "fixture://plan/BTC.1",
            "source_plan_sha256": "a" * 64,
        }], origin),
        _artifact("ERROR_ASSESSMENT", assessments, origin),
    ]
    batch = {
        "schema_version": CONTRACT["input_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "report_id": report_id,
        "report_date": report_date,
        "generated_at": generated_at,
        "sample_origin": origin,
        "previous_report_sha256": previous,
        "source_artifacts": artifacts,
        "authority": copy.deepcopy(CONTRACT["authority"]),
    }
    batch["packet_sha256"] = MODULE.payload_sha256(batch)
    return batch


class ContractAndMetricTests(unittest.TestCase):
    def test_contract_and_source_have_no_network_or_exchange_path(self):
        source = SOURCE.read_text(encoding="utf-8")
        config = (ROOT / "config" / "crypto_paper_counterfactual_validation_contract.json").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "/v1/orders", "/v1/withdraws", "Authorization", "api_key", "secret_key",
            "requests.", "urllib.request", "websocket", "socket.",
        ):
            self.assertNotIn(forbidden, source)
            self.assertNotIn(forbidden, config)
        self.assertFalse(CONTRACT["authority"]["official_d0_automatic_start_authorized"])
        self.assertFalse(CONTRACT["authority"]["exchange_order_authorized"])

    def test_synthetic_report_computes_metrics_but_never_starts_d0(self):
        report = MODULE.build_daily_report(_batch())
        MODULE.validate_daily_report(report)
        self.assertEqual(report["official_validation"]["status"], "NOT_STARTED")
        self.assertFalse(report["official_validation"]["d0_started"])
        self.assertFalse(report["official_validation"]["countable"])
        self.assertEqual(report["sample_origin"], "SYNTHETIC_FIXTURE")
        pnl = report["metrics"]["pnl_and_no_trade"]
        self.assertEqual(pnl["no_trade_benchmark_pnl"], "0")
        self.assertEqual(pnl["fill_event_count"], 3)
        self.assertEqual(pnl["partial_fill_event_count"], 1)
        self.assertNotEqual(pnl["total_fee"], "0")
        self.assertEqual(report["metrics"]["max_drawdown"]["max_drawdown_pct"], "-2")
        trade = report["metrics"]["planned_vs_realized_and_excursions"]["trades"][0]
        self.assertEqual(trade["status"], "CLOSED")
        self.assertGreater(float(trade["mfe_pct"]), 0)
        self.assertLess(float(trade["mae_pct"]), 0)
        self.assertEqual(report["metrics"]["errors"]["duplicate"]["PRESENT"], 1)
        self.assertFalse(report["decision"]["wbs_p10_12_promoted"])
        self.assertFalse(report["authority"]["exchange_order_authorized"])

    def test_explicit_open_gate_counts_only_natural_automated(self):
        for origin in ("SYNTHETIC_FIXTURE", "MANUAL_OBSERVATION", "PIT_REPLAY"):
            with self.subTest(origin=origin):
                diagnostic = MODULE.build_daily_report(_batch(origin=origin, gate_status="OPEN"))
                self.assertTrue(diagnostic["official_validation"]["d0_started"])
                self.assertFalse(diagnostic["official_validation"]["countable"])
                self.assertIn(
                    "ORIGIN_NOT_NATURAL_AUTOMATED",
                    diagnostic["official_validation"]["reason"],
                )
        natural = MODULE.build_daily_report(_batch(origin="NATURAL_AUTOMATED", gate_status="OPEN"))
        self.assertTrue(natural["official_validation"]["countable"])
        self.assertEqual(natural["official_validation"]["day_number"], 1)

    def test_open_gate_with_missing_prerequisite_fails_closed(self):
        batch = _batch(origin="NATURAL_AUTOMATED", gate_status="OPEN")
        gate = next(row for row in batch["source_artifacts"] if row["role"] == "D0_GATE")
        gate["payload"]["prerequisites"]["silent_error_zero_24h_verified"] = False
        gate["payload_sha256"] = MODULE.payload_sha256(gate["payload"])
        batch["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in batch.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperValidationError, "OPEN_D0_GATE_PREREQUISITE_FALSE"):
            MODULE.build_daily_report(batch)


class LineageAndLookaheadTests(unittest.TestCase):
    def test_artifact_hash_tamper_and_future_mark_fail_closed(self):
        batch = _batch()
        nav = next(row for row in batch["source_artifacts"] if row["role"] == "NAV_SERIES")
        nav["payload"][1]["total_nav"] = "999"
        batch["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in batch.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperValidationError, "SOURCE_SHA_MISMATCH:NAV_SERIES"):
            MODULE.build_daily_report(batch)

        batch = _batch()
        marks = next(row for row in batch["source_artifacts"] if row["role"] == "MARK_SERIES")
        marks["payload"][2]["available_at"] = "2026-08-30T04:00:00Z"
        marks["payload_sha256"] = MODULE.payload_sha256(marks["payload"])
        batch["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in batch.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperValidationError, "MARK_SERIES_LOOKAHEAD"):
            MODULE.build_daily_report(batch)

    def test_rehashed_report_metric_tamper_is_rederived_and_rejected(self):
        report = MODULE.build_daily_report(_batch())
        report["metrics"]["pnl_and_no_trade"]["no_trade_benchmark_pnl"] = "999"
        report["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in report.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperValidationError, "DAILY_REPORT_DERIVATION_MISMATCH"):
            MODULE.validate_daily_report(report)

    def test_planned_loss_must_precede_entry_and_match_exact_plan_lineage(self):
        batch = _batch()
        plans = next(row for row in batch["source_artifacts"] if row["role"] == "PLANNED_LOSS")
        plans["payload"][0]["planned_at"] = "2026-08-30T00:10:01Z"
        plans["payload_sha256"] = MODULE.payload_sha256(plans["payload"])
        batch["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in batch.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.CryptoPaperValidationError, "PLANNED_LOSS_AFTER_ENTRY"):
            MODULE.build_daily_report(batch)


class AppendOnlyAndReviewTests(unittest.TestCase):
    def test_direct_cli_output_is_owner_only_and_rejects_symlinks(self):
        report = MODULE.build_daily_report(_batch())
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            output = root / "secure" / "report.json"
            MODULE._write_json(output, report)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            self.assertEqual(output.parent.stat().st_mode & 0o777, 0o700)
            self.assertEqual(json.loads(output.read_text(encoding="utf-8")), report)

            target = root / "target.json"
            target.write_text("unchanged\n", encoding="utf-8")
            symlink = root / "report-link.json"
            symlink.symlink_to(target)
            with self.assertRaisesRegex(
                MODULE.CryptoPaperValidationError, "REPORT_OUTPUT_SYMLINK_FORBIDDEN"
            ):
                MODULE._write_json(symlink, report)
            self.assertEqual(target.read_text(encoding="utf-8"), "unchanged\n")

    def test_external_daily_persistence_is_append_only_and_idempotent(self):
        first = MODULE.build_daily_report(_batch())
        with tempfile.TemporaryDirectory() as directory:
            path, created = MODULE.persist_daily_report(Path(directory), first)
            self.assertTrue(created)
            self.assertTrue(path.exists())
            retry_path, retry_created = MODULE.persist_daily_report(Path(directory), first)
            self.assertEqual(path, retry_path)
            self.assertFalse(retry_created)
            second = MODULE.build_daily_report(_batch(
                report_id="P10.12.DAILY.20260831",
                report_date="2026-08-31",
                generated_at="2026-08-31T03:00:00Z",
                previous=first["packet_sha256"],
            ))
            second_path, second_created = MODULE.persist_daily_report(Path(directory), second)
            self.assertTrue(second_created)
            self.assertTrue(second_path.exists())

    def test_synthetic_review_is_preview_not_official(self):
        first = MODULE.build_daily_report(_batch())
        second = MODULE.build_daily_report(_batch(
            report_id="P10.12.DAILY.20260831",
            report_date="2026-08-31",
            generated_at="2026-08-31T03:00:00Z",
            previous=first["packet_sha256"],
        ))
        review = MODULE.build_cio_review(
            [second, first], review_id="P10.12.CIO.PREVIEW.1",
            generated_at="2026-09-01T00:00:00Z",
        )
        self.assertEqual(review["status"], "PREVIEW_OR_INCOMPLETE_NOT_OFFICIAL")
        self.assertEqual(review["official_countable_day_count"], 0)
        self.assertFalse(review["decision"]["live_authorized"])
        self.assertTrue(review["decision"]["automatic_transition_forbidden"])
        MODULE.validate_cio_review(review)
        review["aggregate"]["period_net_pnl_vs_no_trade"] = "999"
        review["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in review.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(MODULE.CryptoPaperValidationError, "CIO_REVIEW_DERIVATION_MISMATCH"):
            MODULE.validate_cio_review(review)

    def test_exact_natural_days_1_through_30_only_open_cio_review(self):
        reports = []
        previous = None
        d0 = dt.date(2026, 8, 30)
        for offset in range(30):
            date = d0 + dt.timedelta(days=offset)
            report = MODULE.build_daily_report(_batch(
                origin="NATURAL_AUTOMATED",
                gate_status="OPEN",
                report_id=f"P10.12.DAILY.{date.strftime('%Y%m%d')}",
                report_date=date.isoformat(),
                generated_at=f"{date.isoformat()}T03:00:00Z",
                previous=previous,
            ))
            reports.append(report)
            previous = report["packet_sha256"]
        review = MODULE.build_cio_review(
            reports,
            review_id="P10.12.CIO.OFFICIAL.1",
            generated_at="2026-09-29T00:00:00Z",
        )
        MODULE.validate_cio_review(review)
        self.assertEqual(review["official_countable_day_count"], 30)
        self.assertTrue(review["official_chain_exactly_30_consecutive_days"])
        self.assertEqual(review["status"], "READY_FOR_CIO_REVIEW_NOT_LIVE_AUTHORIZED")
        self.assertFalse(review["decision"]["live_authorized"])


if __name__ == "__main__":
    unittest.main()
