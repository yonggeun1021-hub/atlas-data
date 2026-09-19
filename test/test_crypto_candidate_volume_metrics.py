#!/usr/bin/env python3
"""P5-08 explicit-input Crypto candidate volume calculation regression.

Every P4-07 evidence packet used here is built by the real
``microstructure/upbit_market_evidence.py::build_market_evidence_packet``
under the exact ratified P4 policy -- never a hand-written packet literal --
so schema, hash, policy pin, finalization, freshness and gap/duplicate
detection are the production ones.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "crypto_candidate_volume_metrics.py"
SPEC = importlib.util.spec_from_file_location("crypto_candidate_volume_metrics", MODULE_PATH)
VOL = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(VOL)

PROMO = VOL.PROMOTION
MB = VOL.MARKET_BEHAVIOR
MARKET_EV = PROMO.MARKET_EVIDENCE

MARKET = "KRW-BTC"
EVAL_AS_OF = "2026-08-28"
AS_OF = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=dt.timezone.utc)
DAY = 24 * 60 * 60
FOUR_HOURS = 4 * 60 * 60

LAST_DAILY_OPEN = dt.datetime(2026, 8, 27, 0, 0, 0, tzinfo=dt.timezone.utc)
LAST_FOUR_HOUR_OPEN = dt.datetime(2026, 8, 27, 20, 0, 0, tzinfo=dt.timezone.utc)

# Hand-derived reference series (oldest -> newest, latest last).
#   1d base volume  : prior 10/20/60   -> mean 30,  median 20  ; latest 120 -> 4, 6
#   1d KRW turnover : prior 1000/2000/6000 -> mean 3000, median 2000; latest 9000 -> 3, 4.5
#   4h base volume  : prior 1/2/3/4/40 -> mean 10,  median 3   ; latest 30  -> 3, 10
#   4h KRW turnover : prior 2/4/6/8/80  -> mean 20,  median 6   ; latest 120 -> 6, 20
DAILY_VOLUMES = ("10", "20", "60", "120")
DAILY_TURNOVERS = ("1000", "2000", "6000", "9000")
FOUR_HOUR_VOLUMES = ("1", "2", "3", "4", "40", "30")
FOUR_HOUR_TURNOVERS = ("2", "4", "6", "8", "80", "120")

CONTRACT = {
    "schema_version": VOL.CALCULATION_SCHEMA_VERSION,
    "prior_finalized_candle_counts": {"1d": 3, "4h": 5},
}


def d(text: str) -> str:
    """Render a literal through the exact existing P3-07 serialization."""
    return MB._render(Decimal(text), MB.load_contract())


# ---------------------------------------------------------------------------
# Fixture builders -- real P4-07 production builder only
# ---------------------------------------------------------------------------

def raw_candle(open_time: dt.datetime, volume: str, turnover: str) -> dict:
    return {
        "candle_date_time_utc": open_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "opening_price": "1000",
        "high_price": "1010",
        "low_price": "990",
        "trade_price": "1005",
        "candle_acc_trade_price": turnover,
        "candle_acc_trade_volume": volume,
    }


def _series(last_open: dt.datetime, step_seconds: int, volumes, turnovers) -> list:
    count = len(volumes)
    return [
        raw_candle(
            last_open - dt.timedelta(seconds=step_seconds * (count - 1 - index)),
            volumes[index],
            turnovers[index],
        )
        for index in range(count)
    ]


def evidence_packet(
    *,
    market=MARKET,
    daily_volumes=DAILY_VOLUMES,
    daily_turnovers=DAILY_TURNOVERS,
    four_hour_volumes=FOUR_HOUR_VOLUMES,
    four_hour_turnovers=FOUR_HOUR_TURNOVERS,
    captured_offset_seconds=240,
    daily_gap=False,
    daily_duplicate=False,
) -> dict:
    captured_at = AS_OF + dt.timedelta(seconds=captured_offset_seconds)
    daily = _series(LAST_DAILY_OPEN, DAY, daily_volumes, daily_turnovers)
    if daily_gap and len(daily) >= 3:
        del daily[-2]
    if daily_duplicate:
        daily = daily + [copy.deepcopy(daily[0])]
    four_hour = _series(
        LAST_FOUR_HOUR_OPEN, FOUR_HOURS, four_hour_volumes, four_hour_turnovers
    )
    candles = {
        "1d": daily,
        "4h": four_hour,
        "1h": [raw_candle(AS_OF - dt.timedelta(hours=1), "7", "700")],
        "15m": [raw_candle(AS_OF - dt.timedelta(minutes=15), "3", "300")],
    }
    timestamp_ms = int(AS_OF.timestamp() * 1000)
    trades = [{
        "market": market,
        "trade_price": 1000,
        "trade_volume": 1,
        "timestamp": timestamp_ms,
        "ask_bid": "BID",
    }]
    orderbook = {
        "market": market,
        "timestamp": timestamp_ms,
        "orderbook_units": [
            {"bid_price": 999 - level, "bid_size": 10000,
             "ask_price": 1001 + level, "ask_size": 10000}
            for level in range(5)
        ],
    }
    return MARKET_EV.build_market_evidence_packet(
        market,
        candles_by_timeframe=candles,
        trades=trades,
        orderbook_row=orderbook,
        as_of=AS_OF,
        captured_at=captured_at,
        policy=MARKET_EV.load_ratified_policy(),
    )


def rehash(packet: dict) -> dict:
    """Re-sign a mutated packet, so a test reaches the semantic check rather
    than stopping at the outer hash check.
    """
    unsigned = copy.deepcopy(packet)
    unsigned.pop("payload_sha256", None)
    unsigned["payload_sha256"] = MARKET_EV.payload_sha256(unsigned)
    return unsigned


def contract(daily=3, four_hour=5) -> dict:
    return {
        "schema_version": VOL.CALCULATION_SCHEMA_VERSION,
        "prior_finalized_candle_counts": {"1d": daily, "4h": four_hour},
    }


def build(packet=None, market=MARKET, evaluation_as_of=EVAL_AS_OF, calc=None) -> dict:
    return VOL.build_volume_metrics(
        packet if packet is not None else evidence_packet(),
        market,
        evaluation_as_of,
        calc if calc is not None else contract(),
    )


# ---------------------------------------------------------------------------
# Hand-derived arithmetic
# ---------------------------------------------------------------------------

class VolumeArithmeticTests(unittest.TestCase):
    def test_daily_base_and_turnover_match_independent_hand_derivation(self):
        result = build()["timeframes"]["1d"]
        self.assertEqual(result["status"], VOL.STATUS_CALCULATED)
        self.assertEqual(result["unavailable_reasons"], [])
        base = result["base_volume"]
        self.assertEqual(base["latest"], d("120"))
        self.assertEqual(base["prior_mean"], d("30"))
        self.assertEqual(base["prior_median"], d("20"))
        self.assertEqual(base["latest_vs_prior_mean"], d("4"))
        self.assertEqual(base["latest_vs_prior_median"], d("6"))
        self.assertEqual(base["baseline_status"], "OBSERVED")
        quote = result["quote_turnover"]
        self.assertEqual(quote["latest"], d("9000"))
        self.assertEqual(quote["prior_mean"], d("3000"))
        self.assertEqual(quote["prior_median"], d("2000"))
        self.assertEqual(quote["latest_vs_prior_mean"], d("3"))
        self.assertEqual(quote["latest_vs_prior_median"], d("4.5"))
        self.assertEqual(quote["baseline_status"], "OBSERVED")

    def test_four_hour_uses_its_own_explicit_window_and_values(self):
        result = build()["timeframes"]["4h"]
        self.assertEqual(result["status"], VOL.STATUS_CALCULATED)
        self.assertEqual(result["prior_finalized_candle_count"], 5)
        base = result["base_volume"]
        self.assertEqual(base["prior_mean"], d("10"))
        self.assertEqual(base["prior_median"], d("3"))
        self.assertEqual(base["latest_vs_prior_mean"], d("3"))
        self.assertEqual(base["latest_vs_prior_median"], d("10"))
        quote = result["quote_turnover"]
        self.assertEqual(quote["prior_mean"], d("20"))
        self.assertEqual(quote["prior_median"], d("6"))
        self.assertEqual(quote["latest_vs_prior_mean"], d("6"))
        self.assertEqual(quote["latest_vs_prior_median"], d("20"))

    def test_mean_and_median_differ_and_are_both_reported(self):
        base = build()["timeframes"]["4h"]["base_volume"]
        self.assertNotEqual(base["prior_mean"], base["prior_median"])
        self.assertNotEqual(
            base["latest_vs_prior_mean"], base["latest_vs_prior_median"]
        )

    def test_even_prior_count_uses_two_middle_value_median(self):
        packet = evidence_packet(
            daily_volumes=("10", "20", "30", "100", "50"),
            daily_turnovers=("10", "20", "30", "100", "50"),
        )
        result = build(packet, calc=contract(daily=4))["timeframes"]["1d"]
        self.assertEqual(result["base_volume"]["prior_mean"], d("40"))
        self.assertEqual(result["base_volume"]["prior_median"], d("25"))
        self.assertEqual(result["base_volume"]["latest_vs_prior_mean"], d("1.25"))
        self.assertEqual(result["base_volume"]["latest_vs_prior_median"], d("2"))

    def test_flat_series_yields_unit_ratios(self):
        packet = evidence_packet(
            daily_volumes=("10", "10", "10", "10"),
            daily_turnovers=("10", "10", "10", "10"),
        )
        base = build(packet)["timeframes"]["1d"]["base_volume"]
        self.assertEqual(base["latest_vs_prior_mean"], d("1"))
        self.assertEqual(base["latest_vs_prior_median"], d("1"))
        self.assertEqual(base["baseline_status"], "OBSERVED")

    def test_selects_latest_candle_and_exactly_the_requested_prior_count(self):
        packet = evidence_packet(
            daily_volumes=("999", "10", "20", "60", "120"),
            daily_turnovers=("999", "1000", "2000", "6000", "9000"),
        )
        result = build(packet)["timeframes"]["1d"]
        # The extra oldest candle is retained as observed history but is
        # outside the explicitly requested 3-candle prior window.
        self.assertEqual(result["observed_finalized_candle_count"], 5)
        self.assertEqual(result["base_volume"]["prior_mean"], d("30"))
        self.assertEqual(result["window"]["latest_close_time"], "2026-08-28T00:00:00Z")
        self.assertEqual(
            result["window"]["prior_first_open_time"], "2026-08-24T00:00:00Z"
        )
        self.assertEqual(
            result["window"]["prior_last_close_time"], "2026-08-27T00:00:00Z"
        )

    def test_reuses_shared_p3_volume_arithmetic_rather_than_restating_it(self):
        shared = MB.volume_baseline_features(
            [Decimal("10"), Decimal("20"), Decimal("60")], Decimal("120")
        )
        base = build()["timeframes"]["1d"]["base_volume"]
        p3_contract = MB.load_contract()
        self.assertEqual(
            base["latest_vs_prior_mean"],
            MB._render(shared["latest_vs_prior_mean"], p3_contract),
        )
        self.assertEqual(
            base["latest_vs_prior_median"],
            MB._render(shared["latest_vs_prior_median"], p3_contract),
        )
        self.assertEqual(base["baseline_status"], shared["baseline_status"])


# ---------------------------------------------------------------------------
# Zero baseline vs source/history unavailability -- two distinct null shapes
# ---------------------------------------------------------------------------

class ZeroBaselineTests(unittest.TestCase):
    def test_all_zero_prior_gives_null_ratios_not_zero_or_infinity(self):
        packet = evidence_packet(
            daily_volumes=("0", "0", "0", "120"),
            daily_turnovers=("0", "0", "0", "9000"),
        )
        result = build(packet)["timeframes"]["1d"]
        self.assertEqual(result["status"], VOL.STATUS_CALCULATED)
        base = result["base_volume"]
        self.assertIsNone(base["latest_vs_prior_mean"])
        self.assertIsNone(base["latest_vs_prior_median"])
        self.assertEqual(base["baseline_status"], "ZERO_BASELINE_UNKNOWN")
        self.assertEqual(base["prior_mean"], d("0"))
        self.assertEqual(base["latest"], d("120"))
        self.assertEqual(result["unavailable_reasons"], [])

    def test_zero_median_alone_keeps_mean_ratio_and_flags_unknown_baseline(self):
        packet = evidence_packet(
            daily_volumes=("0", "0", "30", "120"),
            daily_turnovers=("1000", "2000", "6000", "9000"),
        )
        result = build(packet)["timeframes"]["1d"]
        base = result["base_volume"]
        self.assertEqual(base["prior_median"], d("0"))
        self.assertIsNone(base["latest_vs_prior_median"])
        self.assertIsNotNone(base["latest_vs_prior_mean"])
        self.assertEqual(base["baseline_status"], "ZERO_BASELINE_UNKNOWN")
        # The other metric on the same timeframe is untouched.
        self.assertEqual(result["quote_turnover"]["baseline_status"], "OBSERVED")

    def test_zero_baseline_is_calculated_never_reported_as_unavailable(self):
        packet = evidence_packet(
            daily_volumes=("0", "0", "0", "0"),
            daily_turnovers=("0", "0", "0", "0"),
            four_hour_volumes=("0",) * 6,
            four_hour_turnovers=("0",) * 6,
        )
        metrics = build(packet)
        self.assertEqual(metrics["status"], VOL.STATUS_CALCULATED)
        self.assertEqual(metrics["unavailable_reasons"], [])
        for timeframe in ("1d", "4h"):
            self.assertEqual(
                metrics["timeframes"][timeframe]["status"], VOL.STATUS_CALCULATED
            )


# ---------------------------------------------------------------------------
# Source/history unavailability, per timeframe
# ---------------------------------------------------------------------------

class TimeframeAvailabilityTests(unittest.TestCase):
    def _assert_unavailable(self, result, fragment):
        self.assertEqual(result["status"], VOL.STATUS_UNAVAILABLE)
        self.assertIsNone(result["base_volume"])
        self.assertIsNone(result["quote_turnover"])
        self.assertIsNone(result["window"])
        self.assertTrue(
            any(fragment in reason for reason in result["unavailable_reasons"]),
            result["unavailable_reasons"],
        )

    def test_stale_four_hour_evidence_is_unavailable_while_daily_is_kept(self):
        # 10h staleness exceeds the ratified 4h bound (28800s) but not the 1d
        # bound (172800s) -- no new threshold is introduced by this module.
        packet = evidence_packet(captured_offset_seconds=10 * 60 * 60)
        metrics = build(packet)
        self._assert_unavailable(metrics["timeframes"]["4h"], "CANDLE_STALE")
        daily = metrics["timeframes"]["1d"]
        self.assertEqual(daily["status"], VOL.STATUS_CALCULATED)
        self.assertEqual(daily["base_volume"]["latest_vs_prior_mean"], d("4"))
        self.assertEqual(metrics["status"], VOL.STATUS_UNAVAILABLE)
        self.assertTrue(
            any(reason.startswith("4h:") for reason in metrics["unavailable_reasons"])
        )
        self.assertFalse(
            any(reason.startswith("1d:") for reason in metrics["unavailable_reasons"])
        )

    def test_daily_gap_is_unavailable(self):
        metrics = build(evidence_packet(daily_gap=True))
        self._assert_unavailable(metrics["timeframes"]["1d"], "CANDLE_GAP")
        self.assertEqual(metrics["timeframes"]["4h"]["status"], VOL.STATUS_CALCULATED)

    def test_duplicate_daily_rows_are_unavailable(self):
        metrics = build(evidence_packet(daily_duplicate=True))
        self._assert_unavailable(metrics["timeframes"]["1d"], "DUPLICATE_CANDLE")

    def test_insufficient_history_is_unavailable_with_exact_counts(self):
        metrics = build(calc=contract(daily=10))
        result = metrics["timeframes"]["1d"]
        self._assert_unavailable(result, "INSUFFICIENT_FINALIZED_HISTORY:4/11")
        self.assertEqual(result["observed_finalized_candle_count"], 4)
        self.assertEqual(result["prior_finalized_candle_count"], 10)

    def test_overall_calculated_requires_both_timeframes(self):
        self.assertEqual(build()["status"], VOL.STATUS_CALCULATED)
        self.assertEqual(build(calc=contract(four_hour=99))["status"], VOL.STATUS_UNAVAILABLE)

    def test_missing_finalized_candles_is_unavailable_not_a_crash(self):
        packet = evidence_packet()
        packet["candles"]["1d"]["finalized_candles"] = []
        packet["candles"]["1d"]["finalized_candle_count"] = 0
        packet["candles"]["1d"]["evidence_status"] = "UNKNOWN"
        packet["candles"]["1d"]["fail_closed_reasons"] = ["NO_FINALIZED_CANDLE"]
        metrics = build(rehash(packet))
        self._assert_unavailable(metrics["timeframes"]["1d"], "NO_FINALIZED_CANDLE")


# ---------------------------------------------------------------------------
# Malformed input rejects (never a quiet UNAVAILABLE)
# ---------------------------------------------------------------------------

class MalformedInputTests(unittest.TestCase):
    def _tamper_daily(self, field, value, index=-1):
        packet = evidence_packet()
        packet["candles"]["1d"]["finalized_candles"][index][field] = value
        return rehash(packet)

    def test_non_string_number_rejected(self):
        for value in (12.5, 12, None, True):
            with self.subTest(value=value):
                with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
                    build(self._tamper_daily("candle_acc_trade_volume", value))
                self.assertIn("VOLUME_VALUE_NOT_STRING", str(ctx.exception))

    def test_non_finite_number_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity", "abc"):
            with self.subTest(value=value):
                with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError):
                    build(self._tamper_daily("candle_acc_trade_price", value))

    def test_negative_volume_rejected(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(self._tamper_daily("candle_acc_trade_volume", "-1"))
        self.assertIn("VOLUME_VALUE_INVALID", str(ctx.exception))

    def test_unknown_candle_row_field_rejected(self):
        packet = evidence_packet()
        packet["candles"]["1d"]["finalized_candles"][-1]["surprise"] = "1"
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(rehash(packet))
        self.assertIn("CANDLE_ROW_FIELDS_MISMATCH", str(ctx.exception))

    def test_candle_row_close_before_open_rejected(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(self._tamper_daily("close_time", "2026-08-01T00:00:00Z"))
        self.assertIn("CANDLE_ROW_TIME_INVALID", str(ctx.exception))

    def test_out_of_order_candle_rows_rejected(self):
        packet = evidence_packet()
        rows = packet["candles"]["1d"]["finalized_candles"]
        rows[0], rows[1] = rows[1], rows[0]
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(rehash(packet))
        self.assertIn("CANDLE_ROW_SEQUENCE_INVALID", str(ctx.exception))

    def test_malformed_time_string_rejected(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(self._tamper_daily("open_time", "2026-08-27 00:00:00"))
        self.assertIn("UTC_INVALID", str(ctx.exception))


# ---------------------------------------------------------------------------
# Exact market / hash / time binding
# ---------------------------------------------------------------------------

class SourceBindingTests(unittest.TestCase):
    def test_market_identity_mismatch_rejected(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(market="KRW-ETH")
        self.assertIn("MARKET_EVIDENCE_INVALID", str(ctx.exception))

    def test_unsigned_packet_tamper_rejected_by_source_hash(self):
        packet = evidence_packet()
        packet["candles"]["1d"]["finalized_candles"][-1]["candle_acc_trade_volume"] = "1200"
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(packet)
        self.assertIn("PAYLOAD_SHA256_MISMATCH", str(ctx.exception))

    def test_evaluation_date_before_capture_rejected(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            build(evaluation_as_of="2026-08-27")
        self.assertIn("MARKET_EVIDENCE_INVALID", str(ctx.exception))

    def test_source_and_contract_digests_are_bound_into_the_output(self):
        packet = evidence_packet()
        metrics = build(packet)
        self.assertEqual(metrics["source_packet_sha256"], packet["payload_sha256"])
        self.assertEqual(
            metrics["calculation_contract_sha256"], VOL.payload_sha256(contract())
        )
        self.assertEqual(metrics["evidence_policy_ratified"], True)
        self.assertEqual(metrics["market"], MARKET)
        self.assertEqual(metrics["evaluation_as_of"], EVAL_AS_OF)

    def test_inputs_are_not_mutated(self):
        packet = evidence_packet()
        calc = contract()
        packet_before = copy.deepcopy(packet)
        calc_before = copy.deepcopy(calc)
        build(packet, calc=calc)
        self.assertEqual(packet, packet_before)
        self.assertEqual(calc, calc_before)


# ---------------------------------------------------------------------------
# Explicit calculation contract -- no defaults, no tuning
# ---------------------------------------------------------------------------

class CalculationContractTests(unittest.TestCase):
    def test_both_timeframe_counts_are_required(self):
        for counts in ({"1d": 3}, {"4h": 5}, {}, {"1d": 3, "4h": 5, "1h": 2}):
            with self.subTest(counts=counts):
                with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError):
                    build(calc={
                        "schema_version": VOL.CALCULATION_SCHEMA_VERSION,
                        "prior_finalized_candle_counts": counts,
                    })

    def test_non_positive_or_non_integer_counts_rejected(self):
        for value in (0, -1, 3.0, "3", True, None):
            with self.subTest(value=value):
                with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
                    build(calc={
                        "schema_version": VOL.CALCULATION_SCHEMA_VERSION,
                        "prior_finalized_candle_counts": {"1d": value, "4h": 5},
                    })
                self.assertIn(
                    "PRIOR_FINALIZED_CANDLE_COUNT_INVALID", str(ctx.exception)
                )

    def test_schema_version_and_field_set_pinned(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError):
            build(calc={
                "schema_version": "other/1",
                "prior_finalized_candle_counts": {"1d": 3, "4h": 5},
            })
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError):
            build(calc={
                "schema_version": VOL.CALCULATION_SCHEMA_VERSION,
                "prior_finalized_candle_counts": {"1d": 3, "4h": 5},
                "window_selection": "auto",
            })

    def test_different_explicit_windows_produce_different_results(self):
        packet = evidence_packet()
        three = build(packet, calc=contract(daily=3))["timeframes"]["1d"]
        two = build(packet, calc=contract(daily=2))["timeframes"]["1d"]
        self.assertEqual(three["base_volume"]["prior_mean"], d("30"))
        self.assertEqual(two["base_volume"]["prior_mean"], d("40"))
        self.assertNotEqual(
            three["base_volume"]["latest_vs_prior_mean"],
            two["base_volume"]["latest_vs_prior_mean"],
        )


# ---------------------------------------------------------------------------
# Deterministic rederivation and tamper rejection
# ---------------------------------------------------------------------------

class RederivationTests(unittest.TestCase):
    def setUp(self):
        self.packet = evidence_packet()
        self.calc = contract()
        self.metrics = build(self.packet, calc=self.calc)

    def test_same_inputs_produce_identical_output(self):
        again = build(self.packet, calc=self.calc)
        self.assertEqual(
            VOL.canonical_json(again), VOL.canonical_json(self.metrics)
        )

    def test_valid_output_revalidates_against_original_inputs(self):
        self.assertEqual(
            VOL.validate_volume_metrics(self.metrics, self.packet, self.calc, EVAL_AS_OF),
            self.metrics,
        )

    def test_self_rehashed_evaluation_date_tamper_rejected(self):
        for changed_date in ("2026-08-27", "2026-08-29"):
            with self.subTest(changed_date=changed_date):
                tampered = copy.deepcopy(self.metrics)
                tampered["evaluation_as_of"] = changed_date
                tampered.pop("payload_sha256")
                tampered["payload_sha256"] = VOL.payload_sha256(tampered)
                with self.assertRaisesRegex(
                    VOL.CryptoCandidateVolumeMetricsError,
                    "EVALUATION_AS_OF_BINDING_MISMATCH",
                ):
                    VOL.validate_volume_metrics(
                        tampered, self.packet, self.calc, EVAL_AS_OF
                    )

    def test_self_rehashed_ratio_tamper_rejected(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["timeframes"]["1d"]["base_volume"]["latest_vs_prior_mean"] = d("9")
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = VOL.payload_sha256(tampered)
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            VOL.validate_volume_metrics(tampered, self.packet, self.calc, EVAL_AS_OF)
        self.assertIn("OUTPUT_DERIVATION_MISMATCH", str(ctx.exception))

    def test_self_rehashed_status_tamper_rejected(self):
        metrics = build(calc=contract(daily=99))
        self.assertEqual(metrics["status"], VOL.STATUS_UNAVAILABLE)
        tampered = copy.deepcopy(metrics)
        tampered["status"] = VOL.STATUS_CALCULATED
        tampered["unavailable_reasons"] = []
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = VOL.payload_sha256(tampered)
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError):
            VOL.validate_volume_metrics(tampered, self.packet, contract(daily=99), EVAL_AS_OF)

    def test_unsigned_output_tamper_rejected(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["timeframes"]["4h"]["base_volume"]["prior_median"] = d("1")
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            VOL.validate_volume_metrics(tampered, self.packet, self.calc, EVAL_AS_OF)
        self.assertIn("OUTPUT_SHA256_MISMATCH", str(ctx.exception))

    def test_substituted_source_packet_rejected(self):
        other = evidence_packet(
            daily_volumes=("1", "1", "1", "1"), daily_turnovers=("1", "1", "1", "1")
        )
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            VOL.validate_volume_metrics(self.metrics, other, self.calc, EVAL_AS_OF)
        self.assertIn("SOURCE_PACKET_BINDING_MISMATCH", str(ctx.exception))

    def test_substituted_calculation_contract_rejected(self):
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            VOL.validate_volume_metrics(self.metrics, self.packet, contract(daily=2), EVAL_AS_OF)
        self.assertIn("CALCULATION_CONTRACT_BINDING_MISMATCH", str(ctx.exception))

    def test_output_field_set_pinned(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["extra"] = 1
        with self.assertRaises(VOL.CryptoCandidateVolumeMetricsError) as ctx:
            VOL.validate_volume_metrics(tampered, self.packet, self.calc, EVAL_AS_OF)
        self.assertIn("OUTPUT_SCHEMA_MISMATCH", str(ctx.exception))


# ---------------------------------------------------------------------------
# Existing P5-08 candidate behavior is unchanged
# ---------------------------------------------------------------------------

class ExistingCriterionUnchangedTests(unittest.TestCase):
    def test_real_volume_liquidity_evaluator_identical_before_and_after(self):
        packet = evidence_packet()
        before = PROMO.evaluate_volume_liquidity(MARKET, packet)
        packet_snapshot = copy.deepcopy(packet)
        metrics = build(packet)
        after = PROMO.evaluate_volume_liquidity(MARKET, packet)
        self.assertEqual(before, after)
        self.assertEqual(packet, packet_snapshot)
        self.assertEqual(before["status"], "UNKNOWN")
        self.assertEqual(before["reason"], "VOLUME_LIQUIDITY_THRESHOLDS_UNRATIFIED")
        # Positive numeric metrics exist, and still grant nothing.
        self.assertEqual(metrics["status"], VOL.STATUS_CALCULATED)
        self.assertIsNotNone(
            metrics["timeframes"]["1d"]["base_volume"]["latest_vs_prior_mean"]
        )
        self.assertNotIn("latest_vs_prior_mean", before)

    def test_candidate_state_machine_input_is_unaffected(self):
        criteria = {name: {"status": "UNKNOWN", "reason": "X"} for name in PROMO.CRITERIA}
        criteria["VOLUME_LIQUIDITY"] = PROMO.evaluate_volume_liquidity(
            MARKET, evidence_packet()
        )
        state, reason = PROMO.aggregate_state(criteria)
        self.assertEqual(state, PROMO.STATE_WATCH)
        self.assertIn("VOLUME_LIQUIDITY", reason)

    def test_calculation_status_is_not_a_candidate_state(self):
        metrics = build()
        self.assertNotIn(metrics["status"], PROMO.PROMOTION_STATES)
        self.assertNotIn(metrics["status"], PROMO.CRITERION_STATUSES)

    def test_authority_is_false_everywhere(self):
        metrics = build()
        self.assertEqual(set(metrics["authority"]), set(PROMO._ROW_AUTHORITY))
        self.assertTrue(all(value is False for value in metrics["authority"].values()))


if __name__ == "__main__":
    unittest.main()
