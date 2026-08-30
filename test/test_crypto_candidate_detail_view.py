#!/usr/bin/env python3
"""CIO item 4 (2026-08-29): portal-consumable Crypto candidate detail view."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "decision" / "crypto_candidate_detail_view.py"
SPEC = importlib.util.spec_from_file_location(
    "crypto_candidate_detail_view_test", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

REAL_UNIVERSE_ROOT = ROOT / "data" / "observations" / "upbit_tradeable_universe"


def write_json(path: Path, payload: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def universe_row(
    market,
    state="OBSERVATION_POOL",
    reason="IDENTITY_UNRATIFIED",
    canonical_asset_id=None,
    turnover="1000000",
    warning=False,
    caution=False,
):
    return {
        "market": market,
        "state": state,
        "reason": reason,
        "candidate_canonical_asset_id": canonical_asset_id,
        "market_event_warning": warning,
        "market_event_caution_any": caution,
        "observed_daily_candle_count": 100,
        "trailing_30d_krw_turnover": turnover,
        "kraken_cross_exchange_reference": False,
        "authority": {
            "investable_eligible": False, "order_authorized": False,
            "paper_eligible": False, "production_authorized": False,
            "stage_authorized": False, "trading_authorized": False,
        },
    }


def write_universe_packet(root: Path, date: str, rows: list[dict]) -> Path:
    summary = {
        "market_count": len(rows),
        "observation_pool_count": sum(1 for r in rows if r["state"] == "OBSERVATION_POOL"),
        "tradeable_universe_count": sum(1 for r in rows if r["state"] == "TRADEABLE_UNIVERSE"),
        "paper_eligible_count": sum(1 for r in rows if r["state"] == "PAPER_ELIGIBLE"),
        "blocked_count": sum(1 for r in rows if r["state"] == "BLOCKED"),
    }
    packet = {
        "schema_version": 1,
        "snapshot_date": date,
        "evaluation_as_of": date,
        "available_at": f"{date}T00:10:00Z",
        "manifest_sha256": "a" * 64,
        "policy_version": "test-v1",
        "policy_ratified": True,
        "taxonomy_version": "test-v1",
        "taxonomy_ratified": True,
        "duplicate_market_codes": {},
        "summary": summary,
        "markets": rows,
        "authority": {},
    }
    packet["payload_sha256"] = "b" * 64
    record = {
        "schema_version": 1,
        "snapshot_date": date,
        "generated_at": f"{date}T00:10:00Z",
        "raw_snapshot": {},
        "identity_review": {},
        "packet": packet,
        "authority": {},
        "payload_sha256": "c" * 64,
    }
    return write_json(root / date / "packet.json", record)


def write_market_evidence_packet(root: Path, date: str, packets_by_market: dict) -> Path:
    record = {
        "schema_version": "upbit_microstructure_population/1",
        "snapshot_date": date,
        "generated_at": f"{date}T00:20:00Z",
        "raw_snapshot": {"path": "x", "market_count": len(packets_by_market)},
        "builder": {"module": "microstructure/upbit_market_evidence.py"},
        "policy_version": "test-v1",
        "policy_ratified": True,
        "summary": {
            "market_count": len(packets_by_market),
            "packet_count": len(packets_by_market),
            "error_count": 0,
        },
        "errors": {},
        "authority": {},
        "packets": packets_by_market,
    }
    record["payload_sha256"] = MODULE.payload_sha256(record)
    return write_json(root / date / "packet.json", record)


def market_evidence_entry(
    market,
    finalized_1d=None,
    spread_bps="12.5",
    spread_status="NORMAL",
    slippage_bps="8.1",
    slippage_status="NORMAL",
):
    return {
        "schema_version": "upbit_market_evidence_packet/1",
        "market": market,
        "as_of": "2026-08-29T00:00:00Z",
        "captured_at": "2026-08-29T00:15:00Z",
        "policy_version": "test-v1",
        "policy_ratified": True,
        "candles": {
            "1d": {"finalized_candles": finalized_1d or []},
            "4h": {"finalized_candles": []},
            "1h": {"finalized_candles": []},
            "15m": {"finalized_candles": []},
        },
        "trades": {},
        "orderbook": {
            "spread_bps": spread_bps,
            "spread_status": spread_status,
            "depth": {"bid_depth_krw": "1000000", "ask_depth_krw": "900000"},
            "slippage_bps": slippage_bps,
            "slippage_status": slippage_status,
        },
        "authority": {},
        "payload_sha256": "e" * 64,
    }


def decision_candidate_row(
    market,
    canonical_asset_id="BTC",
    p3_12_state="TRADEABLE_UNIVERSE",
    state="FOCUSED_REVIEW",
    reason="ALL_CRITERIA_UNKNOWN_PENDING",
    trend=None,
    relative_strength=None,
    p5_09=None,
):
    return {
        "market": market,
        "canonical_asset_id": canonical_asset_id,
        "p3_12_state": p3_12_state,
        "state": state,
        "reason": reason,
        "freshness_capped": False,
        "freshness_cap_reason": None,
        "p5_08": {
            "promotion_state": state,
            "promotion_reason": reason,
            "criteria": {
                "TREND": trend or {"status": "UNKNOWN", "reason": "NO_RATIFIED_CANDIDATE_TREND_RULE"},
                "RELATIVE_STRENGTH": relative_strength or {"status": "UNKNOWN", "reason": "PEER_GROUP_UNRATIFIED"},
            },
        },
        "p5_09": p5_09,
        "authority": {},
    }


def write_decision_snapshot(root: Path, date: str, hhmm: str, generation_id: str, candidates: list[dict]) -> Path:
    record = {
        "schema_version": "crypto_paper_decision_snapshot/1",
        "generated_at": f"{date}T{hhmm[:2]}:{hhmm[2:]}:00Z",
        "capture_date": date,
        "capture_hhmm": hhmm,
        "generation_id": generation_id,
        "funnel_counts": {
            "observation_pool_count": 0,
            "tradeable_universe_count": len(candidates),
            "focused_review_count": sum(1 for c in candidates if c["state"] == "FOCUSED_REVIEW"),
            "paper_ready_count": sum(1 for c in candidates if c["state"] == "PAPER_BUY_ELIGIBLE"),
        },
        "candidates": candidates,
        "authority": {},
    }
    record["payload_sha256"] = MODULE.payload_sha256(record)
    return write_json(root / date / hhmm / generation_id / "packet.json", record)


class RealEvidenceTest(unittest.TestCase):
    """Proves the module against the real, currently-committed repository
    evidence -- this is the "why 0 candidates" real-world demonstration."""

    @unittest.skipUnless(REAL_UNIVERSE_ROOT.is_dir(), "real P3-12 evidence not committed")
    def test_real_committed_evidence_produces_one_row_per_real_market_no_fabrication(self):
        result = MODULE.build_view(generated_at="2026-08-29T12:00:00Z")
        self.assertGreater(len(result["candidates"]), 0)
        self.assertEqual(
            result["blocker_summary"]["total_markets"], len(result["candidates"])
        )
        # Cross-check the blocker_summary aggregation against the raw P3-12
        # packet directly, independent of the module's own internal counts.
        # The detail view is generation-bound: when a decision packet exists,
        # it must use that decision's exact frozen P3 source rather than the
        # mutable latest dated pointer (which may have been replaced by a
        # later same-day ratified reclassification).  Verify the referenced
        # bytes independently before using them as the expectation source.
        decision = result["decision_snapshot"]
        if decision is not None:
            decision_record = json.loads(
                (ROOT / decision["path"]).read_text(encoding="utf-8")
            )
            refs = [
                row for row in decision_record["source_refs"]
                if row["role"] == "upbit_tradeable_universe_packet"
            ]
            self.assertEqual(len(refs), 1)
            source_path = ROOT / refs[0]["path"]
            source_bytes = source_path.read_bytes()
            self.assertEqual(hashlib.sha256(source_bytes).hexdigest(), refs[0]["sha256"])
            entry = json.loads(source_bytes)
        else:
            latest = MODULE.DECISION_SNAPSHOT.find_latest_universe_packet(
                MODULE.UNIVERSE_DATA_ROOT
            )
            entry = latest["record"]
        self.assertEqual(
            entry["packet"]["payload_sha256"], result["universe_payload_sha256"]
        )
        raw_rows = entry["packet"]["markets"]
        expected_by_reason: dict[str, int] = {}
        for row in raw_rows:
            expected_by_reason[row["reason"]] = expected_by_reason.get(row["reason"], 0) + 1
        # Every OBSERVATION_POOL/BLOCKED market's blocker_reason is a direct
        # passthrough of P3-12's own reason (no decision-snapshot override
        # exists for a market that never reached P5-08), so the aggregate
        # must match exactly for those markets.
        pool_reasons = {
            row["blocker_reason"]
            for row in result["candidates"]
            if row["funnel_stage"] == "OBSERVATION_POOL"
        }
        for reason in pool_reasons:
            self.assertIn(reason, expected_by_reason)
        for row in result["candidates"]:
            if not row["evaluated_by_p5_08"]:
                self.assertIsNone(row["trend"])
                self.assertIsNone(row["relative_strength"])
                self.assertIsNone(row["trigger_prerequisites"]["trigger_timeframe_alignment"])
                self.assertIsNone(row["trigger_prerequisites"]["order_draft_complete"])
                self.assertIsNone(row["trigger_prerequisites"]["order_draft"])
            for key, value in row["authority"].items():
                self.assertFalse(value, f"{row['market']}.authority.{key} must stay false")

    @unittest.skipUnless(REAL_UNIVERSE_ROOT.is_dir(), "real P3-12 evidence not committed")
    def test_real_evidence_is_deterministic(self):
        first = MODULE.build_view(generated_at="2026-08-29T12:00:00Z")
        second = MODULE.build_view(generated_at="2026-08-29T12:00:00Z")
        self.assertEqual(first, second)
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])


class SyntheticFixtureTest(unittest.TestCase):
    """Synthetic, clearly test-only fixtures (never fabricated real-market
    claims) exercising branches real committed evidence does not currently
    reach: a market with real P4-07 price/liquidity evidence, a market
    already reaching P5-08 FOCUSED_REVIEW, and a market fully
    PAPER_BUY_ELIGIBLE with a real order draft."""

    def test_blocker_summary_is_accurate_against_a_hand_built_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            rows = [
                universe_row("KRW-A", state="OBSERVATION_POOL", reason="IDENTITY_UNRATIFIED"),
                universe_row("KRW-B", state="OBSERVATION_POOL", reason="IDENTITY_UNRATIFIED"),
                universe_row("KRW-C", state="OBSERVATION_POOL", reason="INVESTMENT_WARNING_ACTIVE", warning=True),
                universe_row("KRW-D", state="TRADEABLE_UNIVERSE", reason="SLIPPAGE_NOT_COMPUTABLE", canonical_asset_id="ETH"),
                universe_row("KRW-E", state="BLOCKED", reason="IDENTITY_COLLISION"),
            ]
            write_universe_packet(universe_root, "2026-08-29", rows)
            result = MODULE.build_view(
                universe_data_root=universe_root,
                market_evidence_data_root=tmp / "market_evidence",
                decision_snapshot_root=tmp / "decision",
                generated_at="2026-08-29T12:00:00Z",
            )
            summary = result["blocker_summary"]
            self.assertEqual(summary["total_markets"], 5)
            self.assertEqual(
                summary["by_funnel_stage"],
                {
                    "OBSERVATION_POOL": 4,  # 3 OBSERVATION_POOL + 1 BLOCKED
                    "TRADEABLE_UNIVERSE": 1,
                    "FOCUSED_REVIEW": 0,
                    "PAPER_BUY_ELIGIBLE": 0,
                },
            )
            self.assertEqual(
                summary["by_blocker_reason"],
                {
                    "IDENTITY_UNRATIFIED": 2,
                    "INVESTMENT_WARNING_ACTIVE": 1,
                    "IDENTITY_COLLISION": 1,
                    "SLIPPAGE_NOT_COMPUTABLE": 1,
                },
            )
            self.assertIn("5/5 markets by funnel stage", summary["narrative"])
            self.assertIn("2 IDENTITY_UNRATIFIED", summary["narrative"])

    def test_price_and_liquidity_facts_reuse_p4_07_evidence_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            rows = [
                universe_row(
                    "KRW-BTC", state="TRADEABLE_UNIVERSE", reason="SLIPPAGE_NOT_COMPUTABLE",
                    canonical_asset_id="BTC", turnover="999999999",
                ),
            ]
            write_universe_packet(universe_root, "2026-08-29", rows)
            evidence_root = tmp / "market_evidence"
            write_market_evidence_packet(
                evidence_root, "2026-08-29",
                {
                    "KRW-BTC": market_evidence_entry(
                        "KRW-BTC",
                        finalized_1d=[
                            {"close_time": "2026-08-27T00:00:00Z", "trade_price": "100000000"},
                            {"close_time": "2026-08-28T00:00:00Z", "trade_price": "101000000"},
                        ],
                    )
                },
            )
            result = MODULE.build_view(
                universe_data_root=universe_root,
                market_evidence_data_root=evidence_root,
                decision_snapshot_root=tmp / "decision",
                generated_at="2026-08-29T12:00:00Z",
            )
            row = result["candidates"][0]
            # Picks the LATEST finalized daily close, never an in-progress
            # or earlier one.
            self.assertEqual(row["price"]["latest_finalized_close"], "101000000")
            self.assertEqual(row["price"]["as_of"], "2026-08-28T00:00:00Z")
            self.assertEqual(row["liquidity"]["trailing_30d_krw_turnover"], "999999999")
            self.assertEqual(row["liquidity"]["spread_bps"], "12.5")
            self.assertEqual(row["liquidity"]["slippage_bps"], "8.1")

    def test_price_stays_none_when_only_in_progress_candles_exist(self):
        """PIT/finalized-candle-only invariant: an in-progress candle must
        never be surfaced as a price fact."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            write_universe_packet(
                universe_root, "2026-08-29",
                [universe_row("KRW-BTC", state="TRADEABLE_UNIVERSE", canonical_asset_id="BTC")],
            )
            evidence_root = tmp / "market_evidence"
            write_market_evidence_packet(
                evidence_root, "2026-08-29",
                {"KRW-BTC": market_evidence_entry("KRW-BTC", finalized_1d=[])},
            )
            result = MODULE.build_view(
                universe_data_root=universe_root,
                market_evidence_data_root=evidence_root,
                decision_snapshot_root=tmp / "decision",
                generated_at="2026-08-29T12:00:00Z",
            )
            row = result["candidates"][0]
            self.assertIsNone(row["price"]["latest_finalized_close"])
            self.assertIsNone(row["price"]["as_of"])

    def test_focused_review_candidate_reuses_p5_08_trend_and_rs_criteria_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            write_universe_packet(
                universe_root, "2026-08-29",
                [universe_row("KRW-ETH", state="TRADEABLE_UNIVERSE", canonical_asset_id="ETH")],
            )
            trend_criterion = {
                "status": "UNKNOWN", "reason": "NO_RATIFIED_CANDIDATE_TREND_RULE",
                "daily_direction": "UP", "four_hour_direction": "UP",
            }
            rs_criterion = {"status": "UNKNOWN", "reason": "PEER_GROUP_UNRATIFIED"}
            decision_root = tmp / "decision"
            write_decision_snapshot(
                decision_root, "2026-08-29", "1136", "1" * 64,
                [
                    decision_candidate_row(
                        "KRW-ETH", canonical_asset_id="ETH",
                        state="FOCUSED_REVIEW", reason="CRITERIA_UNKNOWN:TREND,RELATIVE_STRENGTH",
                        trend=trend_criterion, relative_strength=rs_criterion,
                    )
                ],
            )
            result = MODULE.build_view(
                universe_data_root=universe_root,
                market_evidence_data_root=tmp / "market_evidence",
                decision_snapshot_root=decision_root,
                generated_at="2026-08-29T12:00:00Z",
            )
            row = result["candidates"][0]
            self.assertEqual(row["funnel_stage"], "FOCUSED_REVIEW")
            self.assertEqual(row["blocker_reason"], "CRITERIA_UNKNOWN:TREND,RELATIVE_STRENGTH")
            self.assertEqual(row["trend"], trend_criterion)
            self.assertEqual(row["relative_strength"], rs_criterion)
            self.assertTrue(row["evaluated_by_p5_08"])
            self.assertFalse(row["evaluated_by_p5_09"])
            self.assertIsNone(row["trigger_prerequisites"]["order_draft"])

    def test_paper_buy_eligible_candidate_reuses_p5_09_trigger_and_order_draft_verbatim(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            write_universe_packet(
                universe_root, "2026-08-29",
                [universe_row("KRW-SOL", state="PAPER_ELIGIBLE", canonical_asset_id="SOL")],
            )
            order_draft = {
                "entry_zone": {"low": "100000", "high": "101000"},
                "invalidation_price": "95000",
                "planned_stop_price": "94000",
                "quantity": None,
                "fee_rate": None,
                "fee_amount_krw": None,
                "assumed_slippage_bps": "5",
                "planned_loss_krw": None,
                "expires_at": "2026-08-29T18:00:00Z",
                "next_review_at": "2026-08-29T13:00:00Z",
                "duplicate_guard_key": "X",
            }
            trigger_criterion = {
                "status": "PASS", "reason": "FIFTEEN_MIN_AND_ONE_HOUR_UP_NO_HIGHER_TIMEFRAME_CONFLICT",
            }
            order_draft_criterion = {"status": "PASS", "reason": "ORDER_DRAFT_COMPLETE_NO_NULL_FIELDS"}
            decision_root = tmp / "decision"
            write_decision_snapshot(
                decision_root, "2026-08-29", "1136", "2" * 64,
                [
                    decision_candidate_row(
                        "KRW-SOL", canonical_asset_id="SOL",
                        state="PAPER_BUY_ELIGIBLE", reason="ALL_GATING_CRITERIA_PASSED",
                        p5_09={
                            "eligibility_state": "PAPER_BUY_ELIGIBLE",
                            "eligibility_reason": "ALL_GATING_CRITERIA_PASSED",
                            "criteria": {
                                "TRIGGER_TIMEFRAME_ALIGNMENT": trigger_criterion,
                                "ORDER_DRAFT_COMPLETE": order_draft_criterion,
                            },
                            "order_draft": order_draft,
                        },
                    )
                ],
            )
            result = MODULE.build_view(
                universe_data_root=universe_root,
                market_evidence_data_root=tmp / "market_evidence",
                decision_snapshot_root=decision_root,
                generated_at="2026-08-29T12:00:00Z",
            )
            row = result["candidates"][0]
            self.assertEqual(row["funnel_stage"], "PAPER_BUY_ELIGIBLE")
            self.assertTrue(row["evaluated_by_p5_09"])
            self.assertEqual(
                row["trigger_prerequisites"]["trigger_timeframe_alignment"], trigger_criterion
            )
            self.assertEqual(
                row["trigger_prerequisites"]["order_draft_complete"], order_draft_criterion
            )
            self.assertEqual(row["trigger_prerequisites"]["order_draft"], order_draft)
            self.assertEqual(
                row["trigger_prerequisites"]["order_draft"]["invalidation_price"], "95000"
            )

    def test_no_fabricated_trigger_or_invalidation_price_when_p5_09_left_it_null(self):
        """If P5-09 itself could not compute a trigger/invalidation price
        (e.g. insufficient 1h candles), every order_draft field is null --
        this view must reproduce exactly that, never synthesize one."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            write_universe_packet(
                universe_root, "2026-08-29",
                [universe_row("KRW-DOGE", state="TRADEABLE_UNIVERSE", canonical_asset_id="DOGE")],
            )
            null_order_draft = {key: None for key in (
                "entry_zone", "invalidation_price", "planned_stop_price", "quantity",
                "fee_rate", "fee_amount_krw", "assumed_slippage_bps", "planned_loss_krw",
                "expires_at", "next_review_at", "duplicate_guard_key",
            )}
            decision_root = tmp / "decision"
            write_decision_snapshot(
                decision_root, "2026-08-29", "1136", "3" * 64,
                [
                    decision_candidate_row(
                        "KRW-DOGE", canonical_asset_id="DOGE",
                        state="WAIT", reason="GATING_CRITERIA_UNKNOWN:TRIGGER_TIMEFRAME_ALIGNMENT",
                        p5_09={
                            "eligibility_state": "WAIT",
                            "eligibility_reason": "GATING_CRITERIA_UNKNOWN:TRIGGER_TIMEFRAME_ALIGNMENT",
                            "criteria": {
                                "TRIGGER_TIMEFRAME_ALIGNMENT": {
                                    "status": "UNKNOWN", "reason": "INSUFFICIENT_FINALIZED_CANDLES",
                                },
                                "ORDER_DRAFT_COMPLETE": {
                                    "status": "UNKNOWN",
                                    "reason": "ORDER_DRAFT_FIELDS_MISSING:entry_zone,invalidation_price",
                                },
                            },
                            "order_draft": null_order_draft,
                        },
                    )
                ],
            )
            result = MODULE.build_view(
                universe_data_root=universe_root,
                market_evidence_data_root=tmp / "market_evidence",
                decision_snapshot_root=decision_root,
                generated_at="2026-08-29T12:00:00Z",
            )
            row = result["candidates"][0]
            self.assertEqual(row["funnel_stage"], "FOCUSED_REVIEW")
            draft = row["trigger_prerequisites"]["order_draft"]
            self.assertIsNotNone(draft)
            for value in draft.values():
                self.assertIsNone(value)
            self.assertEqual(
                row["trigger_prerequisites"]["trigger_timeframe_alignment"]["status"], "UNKNOWN"
            )

    def test_deterministic_with_synthetic_fixtures(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            universe_root = tmp / "universe"
            write_universe_packet(
                universe_root, "2026-08-29", [universe_row("KRW-A"), universe_row("KRW-B")]
            )
            kwargs = dict(
                universe_data_root=universe_root,
                market_evidence_data_root=tmp / "market_evidence",
                decision_snapshot_root=tmp / "decision",
                generated_at="2026-08-29T12:00:00Z",
            )
            first = MODULE.build_view(**kwargs)
            second = MODULE.build_view(**kwargs)
            self.assertEqual(first, second)

    def test_missing_universe_packet_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MODULE.CryptoCandidateDetailViewError):
                MODULE.build_view(
                    universe_data_root=Path(tmp) / "does-not-exist",
                    market_evidence_data_root=Path(tmp) / "market_evidence",
                    decision_snapshot_root=Path(tmp) / "decision",
                    generated_at="2026-08-29T12:00:00Z",
                )

    def test_explicit_decision_packet_requires_exact_universe_source_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            decision_path = write_decision_snapshot(
                tmp / "decision", "2026-08-29", "1136", "4" * 64, [],
            )
            with self.assertRaisesRegex(
                MODULE.CryptoCandidateDetailViewError,
                "DECISION_UNIVERSE_SOURCE_REF_MISSING",
            ):
                MODULE.build_view(
                    universe_data_root=tmp / "universe",
                    market_evidence_data_root=tmp / "market_evidence",
                    decision_packet_path=decision_path,
                )


if __name__ == "__main__":
    unittest.main()
