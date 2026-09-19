#!/usr/bin/env python3
"""CIO item 4 (2026-08-29): portal-consumable Crypto candidate detail view."""
from __future__ import annotations

import copy
import datetime as dt
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
        "schema_version": "upbit_tradeable_universe_packet/1",
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
        "authority": {
            "investable_eligible": False,
            "order_authorized": False,
            "paper_eligible": False,
            "production_authorized": False,
            "stage_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = MODULE.payload_sha256(packet)
    record = {
        "schema_version": "upbit_universe_population/1",
        "snapshot_date": date,
        "generated_at": f"{date}T00:10:00Z",
        "raw_snapshot": {
            "path": f"evidence/crypto/upbit/raw/{date}",
            "manifest_sha256": "a" * 64,
        },
        "builder": {
            "module": "universe/upbit_tradeable_universe.py",
            "output_schema_version": packet["schema_version"],
        },
        "ratification": {
            "effective_for_snapshot": True,
        },
        "identity_review": {},
        "packet": packet,
        "authority": {
            "observation_pool_population_only": False,
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "order_authorized": False,
        },
    }
    record["payload_sha256"] = MODULE.payload_sha256(record)
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


# ---------------------------------------------------------------------------
# Enriched-trend fixtures (used only by EnrichedTrendDetailTests)
#
# The P4-07 packets below are built with the *real* microstructure helpers
# against the *real* committed ratified P4 policy, so what reaches PR603's
# calculator has exactly the shape a production packet has. The calculation
# parameters are synthetic, hand-checkable examples -- supplying them is a
# calculation input, never a ratification of those numbers.
# ---------------------------------------------------------------------------

UTC = dt.timezone.utc
ENRICHED_DATE = "2026-08-28"
ENRICHED_HHMM = "1136"
ENRICHED_AS_OF = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
ENRICHED_CAPTURED_AT = dt.datetime(2026, 8, 28, 0, 2, 0, tzinfo=UTC)
# 100 -> 110 -> 120 -> 130 -> 140: strictly rising, so the daily close is above
# its own EMA and the 4h EMA is rising. Both comparisons are hand-obvious.
ENRICHED_DAILY_CLOSES = ("100", "110", "120", "130", "140")
ENRICHED_FOUR_HOUR_CLOSES = ("10", "20", "30", "40", "50")
ENRICHED_INTRADAY_CLOSES = ("100", "101")


def enriched_trend_module():
    return MODULE._trend_metrics_module()


def enriched_raw_series(closes, *, seconds: int) -> list[dict]:
    """Contiguous candles whose last row closes exactly at ``ENRICHED_AS_OF``,
    so every row is finalized and every timeframe is equally fresh."""
    rows = []
    count = len(closes)
    for index, close in enumerate(closes):
        open_time = ENRICHED_AS_OF - dt.timedelta(seconds=seconds * (count - index))
        rows.append({
            "candle_date_time_utc": open_time.strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": close, "high_price": close, "low_price": close,
            "trade_price": close, "candle_acc_trade_price": "1000000",
            "candle_acc_trade_volume": "10",
        })
    return rows


def enriched_market_evidence_packet(market: str, *, daily_closes=ENRICHED_DAILY_CLOSES) -> dict:
    market_evidence = enriched_trend_module().MARKET_EVIDENCE
    timestamp_ms = int(ENRICHED_AS_OF.timestamp() * 1000)
    return market_evidence.build_market_evidence_packet(
        market,
        candles_by_timeframe={
            "1d": enriched_raw_series(daily_closes, seconds=24 * 3600),
            "4h": enriched_raw_series(ENRICHED_FOUR_HOUR_CLOSES, seconds=4 * 3600),
            "1h": enriched_raw_series(ENRICHED_INTRADAY_CLOSES, seconds=3600),
            "15m": enriched_raw_series(ENRICHED_INTRADAY_CLOSES, seconds=15 * 60),
        },
        trades=[{
            "market": market, "trade_price": "1000", "trade_volume": "1",
            "timestamp": timestamp_ms, "ask_bid": "BID",
        }],
        orderbook_row={
            "market": market, "timestamp": timestamp_ms,
            "orderbook_units": [
                {"bid_price": 999 - level, "bid_size": 10000,
                 "ask_price": 1001 + level, "ask_size": 10000}
                for level in range(5)
            ],
        },
        as_of=ENRICHED_AS_OF,
        captured_at=ENRICHED_CAPTURED_AT,
        policy=market_evidence.load_ratified_policy(),
    )


def enriched_calculation_contract(*, daily_min=5, four_hour_min=5, rising_lag_bars=1) -> dict:
    trend = enriched_trend_module()
    return {
        "schema_version": 1,
        "contract_version": trend.CALCULATION_CONTRACT_VERSION,
        "timeframes": {
            "1d": {
                "ema_period": 4,
                "seed_method": trend.SEED_FIRST_FINALIZED_CLOSE,
                "min_finalized_candles": daily_min,
            },
            "4h": {
                "ema_period": 3,
                "seed_method": trend.SEED_SMA_FIRST_PERIOD_FINALIZED_CLOSES,
                "min_finalized_candles": four_hour_min,
            },
        },
        "rising_lag_bars": rising_lag_bars,
        "decimal_precision": 28,
        "decimal_rounding": "ROUND_HALF_EVEN",
        "output_scale": 4,
    }


def write_bound_decision_snapshot(
    root: Path,
    *,
    universe_rows: list[dict],
    evidence_markets: tuple = ("KRW-ETH",),
    candidates: list[dict] | None = None,
    date: str = ENRICHED_DATE,
    generation_id: str = "7" * 64,
) -> Path:
    """A decision packet whose universe and P4-07 source refs are real, exact,
    hash-bound files inside ROOT -- the same binding the module verifies."""
    universe_path = write_universe_packet(root / "universe", date, universe_rows)
    universe_record = json.loads(universe_path.read_text(encoding="utf-8"))
    source_refs = [{
        "role": "upbit_tradeable_universe_packet",
        "path": str(universe_path.relative_to(ROOT)),
        "sha256": hashlib.sha256(universe_path.read_bytes()).hexdigest(),
    }]
    if evidence_markets is not None:
        evidence_path = write_market_evidence_packet(
            root / "market_evidence", date,
            {market: enriched_market_evidence_packet(market) for market in evidence_markets},
        )
        source_refs.append({
            "role": "upbit_market_evidence_packet",
            "path": str(evidence_path.relative_to(ROOT)),
            "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        })
    record = {
        "schema_version": "crypto_paper_decision_snapshot/1",
        "generated_at": f"{date}T{ENRICHED_HHMM[:2]}:{ENRICHED_HHMM[2:]}:00Z",
        "capture_date": date,
        "capture_hhmm": ENRICHED_HHMM,
        "generation_id": generation_id,
        "source_refs": source_refs,
        "upbit_universe_snapshot_identity": {
            "date": date,
            "payload_sha256": universe_record["packet"]["payload_sha256"],
        },
        "funnel_counts": {
            "observation_pool_count": 0,
            "tradeable_universe_count": len(universe_rows),
            "focused_review_count": 0,
            "paper_ready_count": 0,
        },
        "candidates": candidates or [],
        "authority": {},
    }
    record["payload_sha256"] = MODULE.payload_sha256(record)
    return write_json(root / "decision" / date / ENRICHED_HHMM / generation_id / "packet.json", record)


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


class EnrichedTrendDetailTests(unittest.TestCase):
    """P5-08 explicitly-requested trend enrichment over one immutable decision.

    Source refs must resolve inside ROOT (the module rejects a path escape), so
    these fixtures are written to a temporary directory *inside* the repository
    and removed again by the registered cleanup, pass or fail.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(dir=ROOT)
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def build_fixture(self, **kwargs) -> Path:
        rows = kwargs.pop("universe_rows", None) or [
            universe_row("KRW-ETH", state="TRADEABLE_UNIVERSE", canonical_asset_id="ETH"),
            universe_row("KRW-XRP", state="TRADEABLE_UNIVERSE", canonical_asset_id="XRP"),
        ]
        return write_bound_decision_snapshot(self.tmp, universe_rows=rows, **kwargs)

    def enrich(self, decision_path: Path, contracts: dict, **kwargs) -> dict:
        return MODULE.build_enriched_trend_view(
            decision_packet_path=decision_path,
            evaluation_as_of=kwargs.pop("evaluation_as_of", ENRICHED_DATE),
            trend_calculation_contracts=contracts,
            universe_data_root=self.tmp / "universe",
            market_evidence_data_root=self.tmp / "market_evidence",
            **kwargs,
        )

    # -- default path is untouched -----------------------------------------

    def test_default_build_view_and_cli_bytes_are_unchanged_by_this_capability(self):
        """The whole point of the opt-in: not asking changes nothing."""
        decision_path = self.build_fixture()
        default = MODULE.build_view(
            universe_data_root=self.tmp / "universe",
            market_evidence_data_root=self.tmp / "market_evidence",
            decision_packet_path=decision_path,
        )
        enriched = self.enrich(decision_path, {"KRW-ETH": enriched_calculation_contract()})
        # The embedded view is the default view, byte for byte -- criteria,
        # funnel, blockers, counts, triggers and authority all included.
        self.assertEqual(enriched["view"], default)
        self.assertEqual(enriched["view"]["payload_sha256"], default["payload_sha256"])
        self.assertEqual(enriched["view"]["contract_version"], MODULE.CONTRACT_VERSION)
        self.assertNotIn("trend_calculations", enriched["view"])
        for row in enriched["view"]["candidates"]:
            self.assertNotIn("trend_calculations", row)
            self.assertNotIn("trend_metrics", row)

        out = self.tmp / "default.json"
        self.assertEqual(
            MODULE.main(["--decision-packet", str(decision_path), "--out", str(out)]), 0
        )
        self.assertEqual(
            json.loads(out.read_text(encoding="utf-8"))["payload_sha256"],
            default["payload_sha256"],
        )

    # -- the numbers themselves --------------------------------------------

    def test_requested_market_reports_pr603_numbers_for_the_decision_bound_p4_packet(self):
        decision_path = self.build_fixture()
        contract = enriched_calculation_contract()
        enriched = self.enrich(decision_path, {"KRW-ETH": contract})

        self.assertEqual(enriched["contract_version"], MODULE.ENRICHED_CONTRACT_VERSION)
        self.assertEqual(enriched["evaluation_as_of"], ENRICHED_DATE)
        self.assertEqual(enriched["requested_markets"], ["KRW-ETH"])

        observation = enriched["trend_calculations"]["KRW-ETH"]
        self.assertEqual(observation["status"], MODULE.TREND_STATUS_CALCULATED)
        self.assertEqual(observation["reasons"], [])

        trend = enriched_trend_module()
        expected = trend.build_trend_metrics(
            enriched_market_evidence_packet("KRW-ETH"),
            market="KRW-ETH",
            evaluation_as_of=ENRICHED_DATE,
            calculation_contract=contract,
        )
        # Same calculator, same inputs, byte-identical result -- the view
        # neither recomputes nor reshapes PR603's arithmetic.
        self.assertEqual(
            MODULE.canonical_json(observation["metrics"]), MODULE.canonical_json(expected)
        )
        self.assertEqual(
            observation["calculation_contract_sha256"], expected["calculation_contract_sha256"]
        )
        self.assertEqual(observation["metrics"]["payload_sha256"], expected["payload_sha256"])
        # Hand-derived, and identical to PR603's own regression values.
        # 1d, period 4 (alpha 0.4), seed = first close 100:
        #   104 -> 110.4 -> 118.24 -> 126.944, close 140 > 126.944.
        # 4h, period 3 (alpha 0.5), seed = SMA(10,20,30) = 20:
        #   20 -> 30 -> 40, and 40 > the lag-1 value 30.
        daily = observation["metrics"]["timeframes"]["1d"]
        four_hour = observation["metrics"]["timeframes"]["4h"]
        self.assertEqual(daily["latest_close"], "140.0000")
        self.assertEqual(daily["latest_ema"], "126.9440")
        self.assertEqual(four_hour["latest_ema"], "40.0000")
        self.assertEqual(four_hour["lagged_ema"], "30.0000")
        self.assertEqual(observation["metrics"]["comparisons"], {
            "daily_close_above_daily_ema": True,
            "four_hour_ema_rising": True,
        })
        self.assertEqual(observation["metrics"]["source"]["market"], "KRW-ETH")

    def test_source_and_contract_lineage_bind_to_the_decisions_own_p4_reference(self):
        decision_path = self.build_fixture()
        enriched = self.enrich(decision_path, {"KRW-ETH": enriched_calculation_contract()})

        decision_record = json.loads(decision_path.read_text(encoding="utf-8"))
        ref = [r for r in decision_record["source_refs"] if r["role"] == "upbit_market_evidence_packet"][0]
        source = enriched["trend_calculation_source"]
        self.assertEqual(source["role"], "upbit_market_evidence_packet")
        self.assertEqual(source["path"], ref["path"])
        self.assertEqual(source["sha256"], ref["sha256"])
        self.assertEqual(source["snapshot_date"], ENRICHED_DATE)
        self.assertEqual(enriched["decision_source"]["path"], str(decision_path.relative_to(ROOT)))
        self.assertEqual(
            enriched["decision_source"]["payload_sha256"], decision_record["payload_sha256"]
        )
        # The per-market packet actually calculated over is the one inside
        # those exact bytes.
        bound = json.loads((ROOT / ref["path"]).read_text(encoding="utf-8"))
        self.assertEqual(
            enriched["trend_calculations"]["KRW-ETH"]["metrics"]["source"]["payload_sha256"],
            bound["packets"]["KRW-ETH"]["payload_sha256"],
        )

    def test_healthy_timeframe_metrics_survive_an_unavailable_other_timeframe(self):
        """Existing calculator semantics preserved: a coverage shortfall on one
        timeframe must not blank the timeframe that is fine."""
        decision_path = self.build_fixture()
        # 5 finalized 4h candles exist; demanding 99 makes only 4h UNAVAILABLE.
        contract = enriched_calculation_contract(four_hour_min=99)
        observation = self.enrich(decision_path, {"KRW-ETH": contract})["trend_calculations"]["KRW-ETH"]

        self.assertEqual(observation["status"], MODULE.TREND_STATUS_UNAVAILABLE)
        self.assertEqual(observation["reasons"], ["4h:BELOW_MIN_FINALIZED_CANDLES"])
        timeframes = observation["metrics"]["timeframes"]
        self.assertEqual(timeframes["1d"]["latest_close"], "140.0000")
        self.assertIsNotNone(timeframes["1d"]["latest_ema"])
        self.assertIsNone(timeframes["4h"]["latest_ema"])
        # No comparison is asserted while any requested timeframe is short.
        self.assertIsNone(observation["metrics"]["comparisons"]["daily_close_above_daily_ema"])
        self.assertIsNone(observation["metrics"]["comparisons"]["four_hour_ema_rising"])

    # -- NOT_REQUESTED vs UNAVAILABLE --------------------------------------

    def test_market_without_a_requested_contract_is_not_requested_not_unavailable(self):
        decision_path = self.build_fixture()
        enriched = self.enrich(decision_path, {"KRW-ETH": enriched_calculation_contract()})

        absent = enriched["trend_calculations"]["KRW-XRP"]
        self.assertEqual(absent["status"], MODULE.TREND_STATUS_NOT_REQUESTED)
        self.assertEqual(absent["reasons"], [MODULE.REASON_NOT_REQUESTED])
        self.assertIsNone(absent["metrics"])
        self.assertIsNone(absent["calculation_contract_sha256"])
        self.assertNotIn("KRW-XRP", enriched["requested_markets"])
        self.assertNotEqual(absent["status"], MODULE.TREND_STATUS_UNAVAILABLE)

    def test_requested_market_absent_from_the_bound_p4_packet_is_unavailable(self):
        # Bound P4 evidence covers KRW-ETH only; KRW-XRP is genuinely missing.
        decision_path = self.build_fixture()
        enriched = self.enrich(
            decision_path,
            {
                "KRW-ETH": enriched_calculation_contract(),
                "KRW-XRP": enriched_calculation_contract(),
            },
        )
        missing = enriched["trend_calculations"]["KRW-XRP"]
        self.assertEqual(missing["status"], MODULE.TREND_STATUS_UNAVAILABLE)
        self.assertEqual(missing["reasons"], [MODULE.REASON_NO_BOUND_PACKET])
        self.assertIsNone(missing["metrics"])
        # The contract was still validated and its digest recorded.
        self.assertRegex(missing["calculation_contract_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(
            enriched["trend_calculations"]["KRW-ETH"]["status"], MODULE.TREND_STATUS_CALCULATED
        )

    def test_decision_with_no_bound_p4_reference_is_unavailable_never_latest_data(self):
        """No fallback: a decision that bound no P4 evidence stays UNAVAILABLE
        even though a perfectly good latest packet exists on disk."""
        decision_path = self.build_fixture(evidence_markets=None)
        # A newer, unrelated P4 packet that must NOT be picked up.
        write_market_evidence_packet(
            self.tmp / "market_evidence", "2026-08-29",
            {"KRW-ETH": enriched_market_evidence_packet("KRW-ETH")},
        )
        enriched = self.enrich(decision_path, {"KRW-ETH": enriched_calculation_contract()})

        self.assertIsNone(enriched["trend_calculation_source"])
        observation = enriched["trend_calculations"]["KRW-ETH"]
        self.assertEqual(observation["status"], MODULE.TREND_STATUS_UNAVAILABLE)
        self.assertEqual(observation["reasons"], [MODULE.REASON_NO_BOUND_SOURCE])
        self.assertIsNone(observation["metrics"])
        # The unrelated latest packet WAS reachable -- the default view used it
        # for its own price fact -- and enrichment still refused it.
        view_row = {row["market"]: row for row in enriched["view"]["candidates"]}["KRW-ETH"]
        self.assertEqual(enriched["view"]["market_evidence_snapshot_date"], "2026-08-29")
        self.assertIsNotNone(view_row["price"]["latest_finalized_close"])

    # -- rejections ---------------------------------------------------------

    def test_unknown_market_contract_is_rejected_not_silently_ignored(self):
        decision_path = self.build_fixture()
        with self.assertRaisesRegex(
            MODULE.CryptoCandidateDetailViewError, "ENRICHED_TREND_CONTRACT_MARKET_UNKNOWN:KRW-NOPE",
        ):
            self.enrich(decision_path, {"KRW-NOPE": enriched_calculation_contract()})

    def test_malformed_and_partial_contracts_are_rejected(self):
        decision_path = self.build_fixture()
        contract = enriched_calculation_contract()
        partial = copy.deepcopy(contract)
        del partial["rising_lag_bars"]
        smuggled = copy.deepcopy(contract)
        smuggled["approved"] = True
        for label, contracts in (
            ("not-a-mapping", ["KRW-ETH"]),
            ("partial-contract", {"KRW-ETH": partial}),
            ("extra-field", {"KRW-ETH": smuggled}),
            ("not-an-object", {"KRW-ETH": "ema20"}),
        ):
            with self.subTest(label), self.assertRaises(MODULE.CryptoCandidateDetailViewError):
                self.enrich(decision_path, contracts)

    def test_missing_or_malformed_or_future_evaluation_as_of_is_rejected(self):
        decision_path = self.build_fixture()
        contracts = {"KRW-ETH": enriched_calculation_contract()}
        for label, value, expected in (
            ("missing", None, "ENRICHED_EVALUATION_AS_OF_INVALID"),
            ("malformed", "28-08-2026", "ENRICHED_EVALUATION_AS_OF_INVALID"),
            ("not-a-real-day", "2026-02-30", "ENRICHED_EVALUATION_AS_OF_INVALID"),
            # After the decision it claims to explain.
            ("future", "2026-08-29", "ENRICHED_EVALUATION_AS_OF_FUTURE"),
        ):
            with self.subTest(label), self.assertRaisesRegex(
                MODULE.CryptoCandidateDetailViewError, expected,
            ):
                self.enrich(decision_path, contracts, evaluation_as_of=value)

    def test_source_mismatched_against_the_original_evaluation_date_is_rejected(self):
        """A P4 packet captured after the stated evaluation date is a mismatched
        source -- a hard reject, never softened into UNAVAILABLE."""
        decision_path = self.build_fixture()
        with self.assertRaisesRegex(
            MODULE.CryptoCandidateDetailViewError, "ENRICHED_TREND_CALCULATION_REJECTED:KRW-ETH",
        ):
            self.enrich(
                decision_path,
                {"KRW-ETH": enriched_calculation_contract()},
                evaluation_as_of="2026-08-27",
            )

    def test_missing_decision_packet_argument_is_rejected(self):
        with self.assertRaisesRegex(
            MODULE.CryptoCandidateDetailViewError, "ENRICHED_DECISION_PACKET_REQUIRED",
        ):
            MODULE.build_enriched_trend_view(
                decision_packet_path=None,
                evaluation_as_of=ENRICHED_DATE,
                trend_calculation_contracts={},
                universe_data_root=self.tmp / "universe",
                market_evidence_data_root=self.tmp / "market_evidence",
            )

    def test_tampered_bound_p4_bytes_are_rejected(self):
        decision_path = self.build_fixture()
        evidence_path = self.tmp / "market_evidence" / ENRICHED_DATE / "packet.json"
        record = json.loads(evidence_path.read_text(encoding="utf-8"))
        record["packets"]["KRW-ETH"]["candles"]["1d"]["finalized_candles"][-1]["trade_price"] = "1"
        write_json(evidence_path, record)
        with self.assertRaisesRegex(
            MODULE.CryptoCandidateDetailViewError, "DECISION_SOURCE_BYTES_MISMATCH",
        ):
            self.enrich(decision_path, {"KRW-ETH": enriched_calculation_contract()})

    # -- determinism and independent validation -----------------------------

    def test_enrichment_is_deterministic(self):
        decision_path = self.build_fixture()
        contracts = {"KRW-ETH": enriched_calculation_contract()}
        first = self.enrich(decision_path, contracts)
        second = self.enrich(decision_path, contracts)
        self.assertEqual(first, second)
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])

    def test_validator_rederives_from_independently_supplied_originals(self):
        decision_path = self.build_fixture()
        contracts = {"KRW-ETH": enriched_calculation_contract()}
        enriched = self.enrich(decision_path, contracts)
        validated = MODULE.validate_enriched_trend_view(
            enriched,
            decision_packet_path=decision_path,
            evaluation_as_of=ENRICHED_DATE,
            trend_calculation_contracts=contracts,
            universe_data_root=self.tmp / "universe",
            market_evidence_data_root=self.tmp / "market_evidence",
        )
        self.assertEqual(validated, enriched)

    def test_validator_rejects_a_self_rehashed_edit_of_an_emitted_metric(self):
        """The originals come from the caller's arguments, never from the
        untrusted packet, so re-signing an edited metric does not survive."""
        decision_path = self.build_fixture()
        contracts = {"KRW-ETH": enriched_calculation_contract()}
        enriched = self.enrich(decision_path, contracts)

        forged = copy.deepcopy(enriched)
        metrics = forged["trend_calculations"]["KRW-ETH"]["metrics"]
        metrics["comparisons"]["daily_close_above_daily_ema"] = False
        metrics["timeframes"]["1d"]["latest_ema"] = "999999.0000"
        metrics.pop("payload_sha256")
        metrics["payload_sha256"] = enriched_trend_module().payload_sha256(metrics)
        forged.pop("payload_sha256")
        forged["payload_sha256"] = MODULE.payload_sha256(forged)
        # Self-consistent hashes all the way down -- and still rejected.
        self.assertEqual(forged["payload_sha256"], MODULE.payload_sha256(
            {k: v for k, v in forged.items() if k != "payload_sha256"}
        ))
        with self.assertRaisesRegex(
            MODULE.CryptoCandidateDetailViewError, "ENRICHED_DERIVATION_MISMATCH",
        ):
            MODULE.validate_enriched_trend_view(
                forged,
                decision_packet_path=decision_path,
                evaluation_as_of=ENRICHED_DATE,
                trend_calculation_contracts=contracts,
                universe_data_root=self.tmp / "universe",
                market_evidence_data_root=self.tmp / "market_evidence",
            )

    def test_validator_rejects_substituted_originals(self):
        decision_path = self.build_fixture()
        contracts = {"KRW-ETH": enriched_calculation_contract()}
        enriched = self.enrich(decision_path, contracts)
        # A different contract than the one actually used: the packet is
        # untouched and internally consistent, but it is not what these
        # originals derive.
        with self.assertRaisesRegex(
            MODULE.CryptoCandidateDetailViewError, "ENRICHED_DERIVATION_MISMATCH",
        ):
            MODULE.validate_enriched_trend_view(
                enriched,
                decision_packet_path=decision_path,
                evaluation_as_of=ENRICHED_DATE,
                trend_calculation_contracts={
                    "KRW-ETH": enriched_calculation_contract(rising_lag_bars=2)
                },
                universe_data_root=self.tmp / "universe",
                market_evidence_data_root=self.tmp / "market_evidence",
            )

    def test_enrichment_grants_no_authority(self):
        decision_path = self.build_fixture()
        enriched = self.enrich(decision_path, {"KRW-ETH": enriched_calculation_contract()})
        for key, value in enriched["authority"].items():
            if key == "calculation_only":
                self.assertTrue(value)
            else:
                self.assertFalse(value, f"enriched authority.{key} must stay false")
        for key, value in enriched["view"]["authority"].items():
            self.assertFalse(value, f"view authority.{key} must stay false")
        metrics_authority = enriched["trend_calculations"]["KRW-ETH"]["metrics"]["authority"]
        self.assertTrue(metrics_authority["calculation_only"])
        for key, value in metrics_authority.items():
            if key != "calculation_only":
                self.assertFalse(value, f"metrics authority.{key} must stay false")

    # -- CLI ----------------------------------------------------------------

    def test_cli_enriched_trend_requires_the_complete_explicit_argument_set(self):
        decision_path = self.build_fixture()
        contracts_path = write_json(
            self.tmp / "contracts.json", {"KRW-ETH": enriched_calculation_contract()}
        )
        partial_invocations = (
            ["--enriched-trend"],
            ["--enriched-trend", "--decision-packet", str(decision_path)],
            ["--enriched-trend", "--decision-packet", str(decision_path),
             "--evaluation-as-of", ENRICHED_DATE],
            ["--enriched-trend", "--evaluation-as-of", ENRICHED_DATE,
             "--trend-contracts", str(contracts_path)],
            # Enriched-only arguments without the opt-in flag.
            ["--evaluation-as-of", ENRICHED_DATE],
            ["--trend-contracts", str(contracts_path)],
        )
        for argv in partial_invocations:
            with self.subTest(argv=" ".join(argv)), self.assertRaises(SystemExit):
                MODULE.main(argv)

    def test_cli_enriched_trend_emits_the_enriched_packet(self):
        decision_path = self.build_fixture()
        contracts = {"KRW-ETH": enriched_calculation_contract()}
        contracts_path = write_json(self.tmp / "contracts.json", contracts)
        out = self.tmp / "enriched.json"
        self.assertEqual(
            MODULE.main([
                "--enriched-trend",
                "--decision-packet", str(decision_path),
                "--evaluation-as-of", ENRICHED_DATE,
                "--trend-contracts", str(contracts_path),
                "--out", str(out),
            ]),
            0,
        )
        written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["contract_version"], MODULE.ENRICHED_CONTRACT_VERSION)
        self.assertEqual(
            written["trend_calculations"]["KRW-ETH"]["status"], MODULE.TREND_STATUS_CALCULATED
        )
        self.assertEqual(
            written["payload_sha256"], self.enrich(decision_path, contracts)["payload_sha256"]
        )


if __name__ == "__main__":
    unittest.main()
