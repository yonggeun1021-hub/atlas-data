#!/usr/bin/env python3
"""P5-08 Crypto Candidate Promotion Rule tests.

All upstream packets (universe / market-evidence / regime) used here are
hand-built synthetic fixtures matching the real upstream schemas -- never
real captured evidence, and never committed to any evidence directory.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from decimal import Decimal
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

_SPEC = importlib.util.spec_from_file_location(
    "crypto_candidate_promotion_under_test", ROOT / "rules" / "crypto_candidate_promotion.py"
)
CCP = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(CCP)


AS_OF = "2026-08-29"
UNI = CCP.UNIVERSE


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def universe_row(
    market, *, state=UNI.STATE_TRADEABLE_UNIVERSE, reason="TRADEABLE",
    candidate_canonical_asset_id="ASSET:BTC", market_event_warning=False, market_event_caution_any=False,
):
    return {
        "market": market,
        "state": state,
        "reason": reason,
        "candidate_canonical_asset_id": candidate_canonical_asset_id,
        "market_event_warning": market_event_warning,
        "market_event_caution_any": market_event_caution_any,
        "observed_daily_candle_count": 400,
        "trailing_30d_krw_turnover": "10000000000",
        "kraken_cross_exchange_reference": True,
        "authority": dict(UNI._ROW_AUTHORITY),
    }


def universe_packet(rows, *, evaluation_as_of=AS_OF, policy_ratified=True, taxonomy_ratified=True):
    packet = {
        "schema_version": UNI.OUTPUT_SCHEMA_VERSION,
        "snapshot_date": evaluation_as_of,
        "evaluation_as_of": evaluation_as_of,
        "available_at": f"{evaluation_as_of}T00:10:00Z",
        "manifest_sha256": "0" * 64,
        "policy_version": "v1",
        "policy_ratified": policy_ratified,
        "taxonomy_version": "v1",
        "taxonomy_ratified": taxonomy_ratified,
        "duplicate_market_codes": {},
        "summary": {},
        "markets": rows,
        "authority": dict(UNI._ROW_AUTHORITY),
    }
    packet["payload_sha256"] = CCP.payload_sha256(packet)
    return packet


def _candles(prices, *, volumes=None, high_bump="1.001"):
    volumes = volumes or ["100"] * len(prices)
    rows = []
    for i, price in enumerate(prices):
        p = Decimal(str(price))
        rows.append({
            "open_time": f"2026-08-{(i % 28) + 1:02d}T00:00:00Z",
            "close_time": f"2026-08-{(i % 28) + 1:02d}T01:00:00Z",
            "opening_price": str(p),
            "high_price": str(p * Decimal(high_bump)),
            "low_price": str(p * Decimal("0.999")),
            "trade_price": str(p),
            "candle_acc_trade_price": str(p * Decimal(volumes[i])),
            "candle_acc_trade_volume": str(volumes[i]),
        })
    return rows


def candle_evidence(timeframe, prices, *, volumes=None, freshness="FRESH", high_bump="1.001"):
    rows = _candles(prices, volumes=volumes, high_bump=high_bump)
    return {
        "market": "KRW-TEST",
        "timeframe": timeframe,
        "finalized_candle_count": len(rows),
        "in_progress_candle_count": 0,
        "duplicate_row_count": 0,
        "finalized_candles": rows,
        "latest_finalized_close_time": rows[-1]["close_time"] if rows else None,
        "freshness": {"status": freshness, "age_seconds": 60, "max_staleness_seconds": 600},
        "authority": {},
    }


def orderbook_evidence(*, freshness="FRESH", slippage_bps="10"):
    return {
        "market": "KRW-TEST",
        "best_bid": "100", "best_ask": "100.1",
        "spread_bps": "10", "spread_status": "NORMAL",
        "depth": {"levels_requested": 5, "levels_available": 5, "bid_depth_krw": "1000000", "ask_depth_krw": "1000000"},
        "slippage_estimate_notional_krw": "500000",
        "slippage_bps": slippage_bps, "slippage_status": "NORMAL",
        "freshness": {"status": freshness, "age_seconds": 5, "max_staleness_seconds": 60},
        "authority": {},
    }


def rising_series(n, start="100", step="1.0"):
    s, st = Decimal(start), Decimal(step)
    return [str(s + st * i) for i in range(n)]


def flat_series(n, value="100"):
    return [value] * n


def evidence_packet(
    market="KRW-TEST", *, as_of=AS_OF, d1=None, h4=None, h1=None, m15=None,
    orderbook=None, freshness="FRESH",
):
    d1 = d1 or candle_evidence("1d", rising_series(30, "100", "1"), freshness=freshness)
    h4 = h4 or candle_evidence("4h", rising_series(25, "100", "0.5"), freshness=freshness)
    h1 = h1 or candle_evidence(
        "1h", rising_series(25, "100", "0.2"),
        volumes=["100"] * 24 + ["500"], freshness=freshness,
    )
    m15 = m15 or candle_evidence("15m", rising_series(25, "100", "0.1"), freshness=freshness)
    orderbook = orderbook or orderbook_evidence(freshness=freshness)
    packet = {
        "schema_version": "upbit_market_evidence_packet/1",
        "market": market,
        "as_of": f"{as_of}T00:00:00Z",
        "captured_at": f"{as_of}T00:05:00Z",
        "policy_version": "v1",
        "policy_ratified": True,
        "candles": {"1d": d1, "4h": h4, "1h": h1, "15m": m15},
        "trades": {"market": market},
        "orderbook": orderbook,
        "authority": {},
    }
    packet["payload_sha256"] = CCP.payload_sha256(packet)
    return packet


def flat_evidence_packet(market="KRW-TEST", *, as_of=AS_OF):
    d1 = candle_evidence("1d", flat_series(30), freshness="FRESH")
    h4 = candle_evidence("4h", flat_series(25), freshness="FRESH")
    h1 = candle_evidence("1h", flat_series(25), freshness="FRESH")
    m15 = candle_evidence("15m", flat_series(25), freshness="FRESH")
    return evidence_packet(market, as_of=as_of, d1=d1, h4=h4, h1=h1, m15=m15)


def natural_regime_output(as_of=AS_OF):
    return CCP.REGIME_OUTPUT.build_unknown_output("CRYPTO", f"{as_of}T00:10:00Z", {})


def base_candidate_input(*, market="KRW-TEST", regime=None, spread_history=("10", "12", "9")):
    return {
        "market": market,
        "universe_packet": universe_packet([universe_row(market)]),
        "market_evidence_packet": evidence_packet(market),
        "btc_market_evidence_packet": flat_evidence_packet("KRW-BTC"),
        "peer_market_evidence_packets": [flat_evidence_packet("KRW-PEER1"), flat_evidence_packet("KRW-PEER2")],
        "regime_output": regime if regime is not None else natural_regime_output(),
        "spread_history_bps": list(spread_history),
    }


class ContractTests(unittest.TestCase):
    def test_contract_authority_all_false_except_classification_only(self):
        contract = CCP.load_contract()
        self.assertTrue(contract["authority"]["candidate_classification_only"])
        for key, value in contract["authority"].items():
            if key == "candidate_classification_only":
                continue
            self.assertFalse(value, key)

    def test_contract_mode_is_provisional_paper_only(self):
        self.assertEqual(CCP.load_contract()["mode"], "PROVISIONAL_PAPER_ONLY")


class DeterminismTests(unittest.TestCase):
    def test_same_pit_input_is_byte_identical(self):
        candidates = [base_candidate_input()]
        first = CCP.evaluate_pool(candidates, evaluation_as_of=AS_OF)
        second = CCP.evaluate_pool(copy.deepcopy(candidates), evaluation_as_of=AS_OF)
        self.assertEqual(CCP.canonical_json(first), CCP.canonical_json(second))
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])


class Stage1Tests(unittest.TestCase):
    def test_identity_ambiguity_blocks_at_observation_pool(self):
        candidate = base_candidate_input()
        candidate["universe_packet"] = universe_packet([
            universe_row("KRW-TEST", state=UNI.STATE_OBSERVATION_POOL, reason="IDENTITY_UNRATIFIED", candidate_canonical_asset_id=None)
        ])
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_OBSERVATION_POOL)
        self.assertIn(row["disposition"], (CCP.DISPOSITION_BLOCKED, CCP.DISPOSITION_WAIT))
        self.assertEqual(row["blocking_gate"], "identity")

    def test_unratified_policy_blocks_at_observation_pool(self):
        candidate = base_candidate_input()
        candidate["universe_packet"] = universe_packet(
            [universe_row("KRW-TEST", state=UNI.STATE_OBSERVATION_POOL, reason="POLICY_UNRATIFIED")],
            policy_ratified=False, taxonomy_ratified=False,
        )
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_OBSERVATION_POOL)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_BLOCKED)
        self.assertEqual(row["blocking_gate"], "source_policy_ratification")

    def test_market_event_warning_blocks(self):
        candidate = base_candidate_input()
        candidate["universe_packet"] = universe_packet([
            universe_row("KRW-TEST", state=UNI.STATE_BLOCKED, reason="INVESTMENT_WARNING_ACTIVE", market_event_warning=True)
        ])
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_OBSERVATION_POOL)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_BLOCKED)

    def test_mixed_market_packet_rejected(self):
        candidate = base_candidate_input(market="KRW-ETH")
        candidate["universe_packet"] = universe_packet([universe_row("KRW-BTC")])  # different market only
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_OBSERVATION_POOL)
        self.assertEqual(row["blocking_reason"], "MARKET_NOT_IN_UNIVERSE_PACKET")

    def test_stage_skip_forbidden_even_with_ready_looking_stage2_data(self):
        """A candidate whose Stage-1 universe row is stuck at
        OBSERVATION_POOL must never advance to FOCUSED_REVIEW/PAPER_READY,
        no matter how promotion-ready its market evidence looks."""
        candidate = base_candidate_input()
        candidate["universe_packet"] = universe_packet(
            [universe_row("KRW-TEST", state=UNI.STATE_OBSERVATION_POOL, reason="POLICY_UNRATIFIED")],
            policy_ratified=False,
        )
        candidate["sizing_input"] = {
            "paper_quantity": "1", "fee_assumption": "0.0005", "slippage_assumption": "0.003",
            "expiry": f"{AS_OF}T12:00:00Z", "next_review_time": f"{AS_OF}T06:00:00Z",
        }
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_OBSERVATION_POOL)
        self.assertIsNone(row["price_plan"])


class Stage2Tests(unittest.TestCase):
    def test_natural_unknown_regime_caps_at_tradeable_universe_watch(self):
        """The natural, currently-authorized state: regime is always the
        literal string UNKNOWN, so no candidate may reach FOCUSED_REVIEW."""
        candidate = base_candidate_input()  # natural_regime_output() -> regime "UNKNOWN"
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_TRADEABLE_UNIVERSE)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_WATCH)
        self.assertEqual(row["blocking_gate"], "regime_evidence")
        gate_names = {g["gate"] for g in row["gates"]}
        # every stage-2 gate is still computed and reported even though the
        # candidate does not advance past TRADEABLE_UNIVERSE.
        self.assertIn("daily_4h_trend_consistency", gate_names)
        self.assertIn("relative_strength_btc_peer", gate_names)
        self.assertIn("price_volume_trigger", gate_names)
        self.assertIn("overextension", gate_names)
        self.assertIn("event_blocker", gate_names)

    def test_partial_peer_coverage_relative_strength_unknown(self):
        candidate = base_candidate_input()
        candidate["peer_market_evidence_packets"] = []
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        rs_gate = next(g for g in row["gates"] if g["gate"] == "relative_strength_btc_peer")
        self.assertEqual(rs_gate["status"], CCP.GATE_UNKNOWN)

    def test_overextension_and_event_blocker_default_unknown(self):
        candidate = base_candidate_input()
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        overext = next(g for g in row["gates"] if g["gate"] == "overextension")
        event = next(g for g in row["gates"] if g["gate"] == "event_blocker")
        self.assertEqual(overext["status"], CCP.GATE_UNKNOWN)
        self.assertEqual(event["status"], CCP.GATE_UNKNOWN)

    def test_event_blocker_active_fails(self):
        candidate = base_candidate_input()
        candidate["event_blocker_evidence"] = {"active": True, "reason": "DELISTING_NOTICE", "source_sha256": "a" * 64}
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        event = next(g for g in row["gates"] if g["gate"] == "event_blocker")
        self.assertEqual(event["status"], CCP.GATE_FAIL)
        self.assertEqual(event["reason"], "DELISTING_NOTICE")

    def test_flat_trend_series_fails_trend_gate(self):
        candidate = base_candidate_input()
        candidate["market_evidence_packet"] = flat_evidence_packet("KRW-TEST")
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        trend = next(g for g in row["gates"] if g["gate"] == "daily_4h_trend_consistency")
        self.assertIn(trend["status"], (CCP.GATE_FAIL, CCP.GATE_UNKNOWN))

    def test_insufficient_candle_history_is_unknown_not_pass(self):
        candidate = base_candidate_input()
        short_d1 = candle_evidence("1d", rising_series(5, "100", "1"))
        candidate["market_evidence_packet"] = evidence_packet("KRW-TEST", d1=short_d1)
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        trend = next(g for g in row["gates"] if g["gate"] == "daily_4h_trend_consistency")
        self.assertEqual(trend["status"], CCP.GATE_UNKNOWN)

    def test_in_progress_candle_marker_does_not_change_gate_result(self):
        candidate_a = base_candidate_input()
        candidate_b = base_candidate_input()
        candidate_b["market_evidence_packet"]["candles"]["1d"]["in_progress_candle_count"] = 3
        candidate_b["market_evidence_packet"]["payload_sha256"] = CCP.payload_sha256(
            {k: v for k, v in candidate_b["market_evidence_packet"].items() if k != "payload_sha256"}
        )
        row_a = CCP.evaluate_candidate(candidate_a, evaluation_as_of=AS_OF)
        row_b = CCP.evaluate_candidate(candidate_b, evaluation_as_of=AS_OF)
        gates_a = [(g["gate"], g["status"], g["reason"]) for g in row_a["gates"]]
        gates_b = [(g["gate"], g["status"], g["reason"]) for g in row_b["gates"]]
        self.assertEqual(gates_a, gates_b)


class LineageAndPitTests(unittest.TestCase):
    def test_tampered_universe_packet_hash_is_detected(self):
        candidate = base_candidate_input()
        candidate["universe_packet"]["policy_ratified"] = False  # mutate after signing, hash now stale
        with self.assertRaisesRegex(CCP.CryptoCandidatePromotionError, "LINEAGE_HASH_MISMATCH"):
            CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)

    def test_tampered_market_evidence_hash_is_detected(self):
        candidate = base_candidate_input()
        candidate["market_evidence_packet"]["orderbook"]["slippage_bps"] = "1"
        with self.assertRaisesRegex(CCP.CryptoCandidatePromotionError, "LINEAGE_HASH_MISMATCH"):
            CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)

    def test_missing_lineage_hash_is_rejected(self):
        candidate = base_candidate_input()
        del candidate["universe_packet"]["payload_sha256"]
        with self.assertRaisesRegex(CCP.CryptoCandidatePromotionError, "LINEAGE_HASH_MISSING"):
            CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)

    def test_pit_mismatch_universe_packet_raises(self):
        candidate = base_candidate_input()
        candidate["universe_packet"] = universe_packet([universe_row("KRW-TEST")], evaluation_as_of="2026-08-28")
        with self.assertRaisesRegex(CCP.CryptoCandidatePromotionError, "PIT_MISMATCH"):
            CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)

    def test_pit_mismatch_market_evidence_raises(self):
        candidate = base_candidate_input()
        candidate["market_evidence_packet"] = evidence_packet("KRW-TEST", as_of="2026-08-27")
        with self.assertRaisesRegex(CCP.CryptoCandidatePromotionError, "PIT_MISMATCH"):
            CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)

    def test_row_forgery_after_the_fact_is_detectable_via_hash(self):
        candidate = base_candidate_input()
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        forged = copy.deepcopy(row)
        forged["state"] = CCP.STATE_PAPER_READY
        forged["disposition"] = CCP.DISPOSITION_PROMOTED
        forged["blocking_gate"] = None
        forged["blocking_reason"] = None
        unsigned = {k: v for k, v in forged.items() if k != "row_sha256"}
        self.assertNotEqual(CCP.payload_sha256(unsigned), row["row_sha256"])


class MechanismTests(unittest.TestCase):
    """Mechanism-only tests proving the state machine correctly advances
    once hypothetical future ratified authority exists for the two things
    that are *permanently* UNKNOWN in production today:

    1. Regime (``REGIME_OUTPUT.validate_output`` is monkeypatched to accept
       a non-UNKNOWN regime literal -- no production code path can do this
       today; the real validator only ever authorizes ``UNKNOWN``).
    2. The "overextension" gate, for which canonical v1 gives no ratified
       numeric definition at all (see module docstring) -- ``_overextension_gate``
       is monkeypatched to PASS so the rest of the pipeline can be exercised.

    Both patches are restored in tearDown. Neither changes production
    behavior; together they only prove the mechanism is correct, not that
    today's natural inputs can reach PAPER_READY (they cannot, and must
    not)."""

    def setUp(self):
        self._orig_validate = CCP.REGIME_OUTPUT.validate_output
        self._orig_overextension = CCP._overextension_gate
        CCP.REGIME_OUTPUT.validate_output = lambda payload, contract=None: payload
        CCP._overextension_gate = lambda contract: CCP._gate(
            "overextension", CCP.GATE_PASS, "MECHANISM_TEST_ONLY_HYPOTHETICAL_RATIFIED_THRESHOLD"
        )

    def tearDown(self):
        CCP.REGIME_OUTPUT.validate_output = self._orig_validate
        CCP._overextension_gate = self._orig_overextension

    def _authorized_regime(self, regime="RISK_ON"):
        return {"regime": regime, "direction": "UNKNOWN", "market": "CRYPTO", "generated_at": f"{AS_OF}T00:10:00Z"}

    def _ready_candidate(self, **overrides):
        candidate = base_candidate_input(regime=self._authorized_regime("RISK_ON"))
        candidate["event_blocker_evidence"] = {"active": False, "source_sha256": "b" * 64}
        candidate.update(overrides)
        return candidate

    def test_full_promotion_reaches_paper_ready_when_all_gates_pass(self):
        candidate = self._ready_candidate(sizing_input={
            "paper_quantity": "0.01", "fee_assumption": "0.0005", "slippage_assumption": "0.003",
            "expiry": f"{AS_OF}T12:00:00Z", "next_review_time": f"{AS_OF}T06:00:00Z",
        })
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_PAPER_READY)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_PROMOTED)
        self.assertIsNone(row["blocking_gate"])
        self.assertIsNotNone(row["price_plan"])
        self.assertIsNotNone(row["price_plan"]["duplicate_guard_key"])

    def test_stale_evidence_blocks_at_focused_review_even_with_authorized_regime(self):
        # Only the daily candle is stale: Stage-2 trend/RS/trigger math never
        # gates on freshness (only on finalized-candle values), so the
        # candidate still clears Stage 2 -- proving Stage 3's freshness gate
        # is the one actually catching this, not an accidental Stage-2 trip.
        stale_d1 = candle_evidence("1d", rising_series(30, "100", "1"), freshness="STALE")
        candidate = self._ready_candidate(market_evidence_packet=evidence_packet("KRW-TEST", d1=stale_d1))
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_FOCUSED_REVIEW)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_BLOCKED)
        self.assertEqual(row["blocking_gate"], "current_candle_orderbook_freshness")

    def test_incomplete_order_plan_waits_at_focused_review(self):
        candidate = self._ready_candidate()  # no sizing_input -> quantity/fee/slippage/expiry incomplete
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_FOCUSED_REVIEW)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_WAIT)
        self.assertEqual(row["blocking_gate"], "order_plan_completeness")

    def test_stress_regime_blocks_entry(self):
        candidate = self._ready_candidate(regime_output=self._authorized_regime("STRESS"))
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_TRADEABLE_UNIVERSE)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_BLOCKED)
        self.assertEqual(row["blocking_gate"], "regime_evidence")

    def test_overextension_permanently_blocks_natural_promotion_even_with_authorized_regime(self):
        """Without the monkeypatch, overextension alone caps every
        candidate at TRADEABLE_UNIVERSE/WATCH -- this is the real,
        permanent, honest behavior of this module today."""
        CCP._overextension_gate = self._orig_overextension
        candidate = self._ready_candidate(sizing_input={
            "paper_quantity": "0.01", "fee_assumption": "0.0005", "slippage_assumption": "0.003",
            "expiry": f"{AS_OF}T12:00:00Z", "next_review_time": f"{AS_OF}T06:00:00Z",
        })
        row = CCP.evaluate_candidate(candidate, evaluation_as_of=AS_OF)
        self.assertEqual(row["state"], CCP.STATE_TRADEABLE_UNIVERSE)
        self.assertEqual(row["disposition"], CCP.DISPOSITION_WATCH)
        self.assertEqual(row["blocking_gate"], "overextension")


class BreakoutPullbackHelperTests(unittest.TestCase):
    def test_breakout_check_detects_confirmed_breakout(self):
        h1 = candle_evidence("1h", rising_series(25, "100", "0.2"), volumes=["100"] * 24 + ["500"])
        triggered, lineage = CCP._breakout_check(h1)
        self.assertTrue(triggered)

    def test_breakout_check_rejects_without_volume_confirmation(self):
        h1 = candle_evidence("1h", rising_series(25, "100", "0.2"), volumes=["100"] * 25)
        triggered, lineage = CCP._breakout_check(h1)
        self.assertFalse(triggered)

    def test_pullback_check_detects_recovery_close(self):
        prices = rising_series(20, "100", "1") + ["118", "121"]  # dip then recovery above ema
        h4 = candle_evidence("4h", prices)
        result, lineage = CCP._pullback_check(h4)
        self.assertIsNotNone(result)


class PoolLevelTests(unittest.TestCase):
    def test_observation_pool_count_separate_from_paper_ready_count(self):
        candidates = [base_candidate_input(market=f"KRW-M{i}") for i in range(5)]
        for c in candidates:
            c["universe_packet"] = universe_packet([
                universe_row(c["market"], state=UNI.STATE_OBSERVATION_POOL, reason="IDENTITY_UNRATIFIED", candidate_canonical_asset_id=None)
            ])
        packet = CCP.evaluate_pool(candidates, evaluation_as_of=AS_OF)
        self.assertEqual(packet["observation_pool_count"], 5)
        self.assertEqual(packet["summary"]["paper_ready"], 0)
        self.assertEqual(packet["summary"]["observation_pool"], 5)

    def test_duplicate_markets_flagged(self):
        candidates = [base_candidate_input(), base_candidate_input()]  # both KRW-TEST
        packet = CCP.evaluate_pool(candidates, evaluation_as_of=AS_OF)
        self.assertIn("KRW-TEST", packet["duplicate_markets"])

    def test_no_candidate_counted_paper_ready_without_full_promotion(self):
        candidates = [base_candidate_input(market=f"KRW-N{i}") for i in range(3)]
        packet = CCP.evaluate_pool(candidates, evaluation_as_of=AS_OF)
        self.assertEqual(packet["summary"]["paper_ready"], 0)
        for row in packet["candidates"]:
            self.assertNotEqual(row["disposition"], CCP.DISPOSITION_PROMOTED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
