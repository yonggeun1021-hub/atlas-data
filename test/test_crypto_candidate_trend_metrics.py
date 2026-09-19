#!/usr/bin/env python3
"""P5-08 crypto candidate trend metric calculation regression.

Every EMA expectation in this file is derived by hand (the arithmetic is
written out in the docstring of each math test) and compared against the real
implementation -- the calculator is never mocked to "prove" positive math.
Source fixtures are built with the actual P4-07 helpers
(``build_candle_evidence`` via ``build_market_evidence_packet``) against the
committed ratified P4 policy, so the packets that reach the calculator are the
same shape production packets are.

The parameter values here are synthetic calculation examples chosen so the
arithmetic is exactly hand-checkable. They are not a ratification of any
investment parameter.
"""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


TREND = _load("crypto_candidate_trend_metrics", "universe/crypto_candidate_trend_metrics.py")
# The actual, unchanged P5-08 and P5-09 modules -- not copies, not stubs.
PROMO = TREND.PROMOTION
MARKET_EV = TREND.MARKET_EVIDENCE
P59 = _load("crypto_paper_buy_eligibility", "universe/crypto_paper_buy_eligibility.py")

TrendError = TREND.CryptoCandidateTrendMetricsError

UTC = dt.timezone.utc
MARKET = "KRW-ETH"
EVAL_AS_OF = "2026-08-28"
AS_OF = dt.datetime(2026, 8, 28, 0, 0, 0, tzinfo=UTC)
CAPTURED_AT = dt.datetime(2026, 8, 28, 0, 2, 0, tzinfo=UTC)

FIRST = TREND.SEED_FIRST_FINALIZED_CLOSE
SMA = TREND.SEED_SMA_FIRST_PERIOD_FINALIZED_CLOSES

DAILY_CLOSES = ("100", "110", "120", "130", "140")
FOUR_HOUR_CLOSES = ("10", "20", "30", "40", "50")
INTRADAY_CLOSES = ("100", "101")

_CONTRACT_TIMEFRAME_FIELDS = ("ema_period", "seed_method", "min_finalized_candles")


# ---------------------------------------------------------------------------
# P4-07 fixtures -- real helpers, real ratified policy
# ---------------------------------------------------------------------------

def _opens(count: int, *, seconds: int) -> list[dt.datetime]:
    """``count`` contiguous candle opens whose LAST candle closes exactly at
    ``AS_OF``, so every row is finalized and the freshness age is the same
    ``CAPTURED_AT - AS_OF`` for every timeframe.
    """
    return [AS_OF - dt.timedelta(seconds=seconds * (count - index)) for index in range(count)]


def raw_candle(open_time: dt.datetime, close: str) -> dict:
    return {
        "candle_date_time_utc": open_time.strftime("%Y-%m-%dT%H:%M:%S"),
        "opening_price": close,
        "high_price": close,
        "low_price": close,
        "trade_price": close,
        "candle_acc_trade_price": "1000000",
        "candle_acc_trade_volume": "10",
    }


def _raw_series(closes, *, seconds: int) -> list[dict]:
    return [
        raw_candle(open_time, close)
        for open_time, close in zip(_opens(len(closes), seconds=seconds), closes)
    ]


def market_evidence_packet(
    *,
    market=MARKET,
    daily_closes=DAILY_CLOSES,
    four_hour_closes=FOUR_HOUR_CLOSES,
    captured_at=CAPTURED_AT,
    daily_duplicate=False,
    daily_gap=False,
) -> dict:
    daily = _raw_series(daily_closes, seconds=24 * 3600)
    if daily_gap:
        del daily[1]
    if daily_duplicate:
        daily.append(copy.deepcopy(daily[0]))
    candles = {
        "1d": daily,
        "4h": _raw_series(four_hour_closes, seconds=4 * 3600),
        "1h": _raw_series(INTRADAY_CLOSES, seconds=3600),
        "15m": _raw_series(INTRADAY_CLOSES, seconds=15 * 60),
    }
    timestamp_ms = int(AS_OF.timestamp() * 1000)
    trades = [{
        "market": market, "trade_price": "1000", "trade_volume": "1",
        "timestamp": timestamp_ms, "ask_bid": "BID",
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


def calculation_contract(
    *,
    daily_period=4,
    daily_seed=FIRST,
    daily_min=5,
    four_hour_period=3,
    four_hour_seed=SMA,
    four_hour_min=5,
    rising_lag_bars=1,
    decimal_precision=28,
    decimal_rounding="ROUND_HALF_EVEN",
    output_scale=4,
) -> dict:
    """A complete, explicit calculation contract. Nothing here is a default:
    every field is spelled out on every call, and the module refuses the call
    when any one of them is absent.
    """
    return {
        "schema_version": 1,
        "contract_version": TREND.CALCULATION_CONTRACT_VERSION,
        "timeframes": {
            "1d": {
                "ema_period": daily_period,
                "seed_method": daily_seed,
                "min_finalized_candles": daily_min,
            },
            "4h": {
                "ema_period": four_hour_period,
                "seed_method": four_hour_seed,
                "min_finalized_candles": four_hour_min,
            },
        },
        "rising_lag_bars": rising_lag_bars,
        "decimal_precision": decimal_precision,
        "decimal_rounding": decimal_rounding,
        "output_scale": output_scale,
    }


def build(packet=None, contract=None, *, market=MARKET, evaluation_as_of=EVAL_AS_OF) -> dict:
    return TREND.build_trend_metrics(
        market_evidence_packet() if packet is None else packet,
        market=market,
        evaluation_as_of=evaluation_as_of,
        calculation_contract=calculation_contract() if contract is None else contract,
    )


def resign_metrics(metrics: dict) -> dict:
    """A caller's self-rehash after editing an emitted metric."""
    edited = copy.deepcopy(metrics)
    edited.pop("payload_sha256", None)
    edited["payload_sha256"] = TREND.payload_sha256(edited)
    return edited


def resign_packet(packet: dict) -> dict:
    edited = copy.deepcopy(packet)
    edited.pop("payload_sha256", None)
    edited["payload_sha256"] = MARKET_EV.payload_sha256(edited)
    return edited


def ema_strings(closes, **kwargs) -> list[str]:
    series = TREND.compute_ema_series(closes, **kwargs)
    return [str(value) for value in series["values"]]


HAND_DERIVED = {
    "ema_period": 4, "seed_method": FIRST, "decimal_precision": 28,
    "decimal_rounding": "ROUND_HALF_EVEN", "output_scale": 4,
}


# ---------------------------------------------------------------------------
# Hand-derived EMA arithmetic
# ---------------------------------------------------------------------------

class EmaHandDerivedMathTests(unittest.TestCase):
    def test_first_finalized_close_seed_rising_series(self):
        """period=4 -> alpha = 2/5 = 0.4, seed = first close.

            e0 = 100
            e1 = 0.4*110 + 0.6*100     = 44    + 60     = 104
            e2 = 0.4*120 + 0.6*104     = 48    + 62.4   = 110.4
            e3 = 0.4*130 + 0.6*110.4   = 52    + 66.24  = 118.24
            e4 = 0.4*140 + 0.6*118.24  = 56    + 70.944 = 126.944
        """
        self.assertEqual(
            ema_strings(list(DAILY_CLOSES), **HAND_DERIVED),
            ["100.0000", "104.0000", "110.4000", "118.2400", "126.9440"],
        )

    def test_first_finalized_close_seed_falling_series(self):
        """period=4, alpha=0.4, closes 200/190/180.

            e0 = 200
            e1 = 0.4*190 + 0.6*200   = 76 + 120   = 196
            e2 = 0.4*180 + 0.6*196   = 72 + 117.6 = 189.6
        """
        self.assertEqual(
            ema_strings(["200", "190", "180"], **HAND_DERIVED),
            ["200.0000", "196.0000", "189.6000"],
        )

    def test_flat_series_is_exactly_flat(self):
        """A constant series can never drift: every level equals the close."""
        self.assertEqual(
            ema_strings(["50", "50", "50", "50"], **HAND_DERIVED),
            ["50.0000", "50.0000", "50.0000", "50.0000"],
        )

    def test_nondefault_period_nine(self):
        """period=9 -> alpha = 2/10 = 0.2.

            e0 = 100
            e1 = 0.2*200 + 0.8*100 = 40 + 80 = 120
        """
        self.assertEqual(
            ema_strings(["100", "200"], **{**HAND_DERIVED, "ema_period": 9}),
            ["100.0000", "120.0000"],
        )

    def test_sma_seed_starts_at_period_minus_one(self):
        """period=3 -> alpha = 2/4 = 0.5, seed = mean of first 3 closes.

            seed = (10 + 20 + 30) / 3 = 20     (at index 2)
            e3   = 0.5*40 + 0.5*20 = 20 + 10 = 30
            e4   = 0.5*50 + 0.5*30 = 25 + 15 = 40
        """
        series = TREND.compute_ema_series(
            list(FOUR_HOUR_CLOSES),
            ema_period=3, seed_method=SMA, decimal_precision=28,
            decimal_rounding="ROUND_HALF_EVEN", output_scale=4,
        )
        self.assertEqual(series["seed_index"], 2)
        self.assertEqual(
            [str(value) for value in series["values"]],
            ["20.0000", "30.0000", "40.0000"],
        )

    def test_sma_seed_falling_series(self):
        """seed = (50+40+30)/3 = 40; e3 = 0.5*20+0.5*40 = 30; e4 = 0.5*10+0.5*30 = 20."""
        self.assertEqual(
            ema_strings(
                ["50", "40", "30", "20", "10"],
                ema_period=3, seed_method=SMA, decimal_precision=28,
                decimal_rounding="ROUND_HALF_EVEN", output_scale=4,
            ),
            ["40.0000", "30.0000", "20.0000"],
        )

    def test_sma_seed_non_terminating_average_is_rounded_at_the_declared_scale(self):
        """seed = (10 + 11 + 13)/3 = 11.33333... -> 11.3333 at scale 4.

            e3 = 0.5*20 + 0.5*11.3333 = 10 + 5.66665 = 15.66665
            ROUND_HALF_EVEN at scale 4: the dropped part is an exact tie and
            the retained digit 6 is even, so the level stays 15.6666.
        """
        self.assertEqual(
            ema_strings(
                ["10", "11", "13", "20"],
                ema_period=3, seed_method=SMA, decimal_precision=28,
                decimal_rounding="ROUND_HALF_EVEN", output_scale=4,
            ),
            ["11.3333", "15.6666"],
        )

    def test_same_tie_rounds_up_under_round_half_up(self):
        """The identical inputs under ROUND_HALF_UP resolve the 15.66665 tie
        upward -- proving the rounding parameter genuinely binds.
        """
        self.assertEqual(
            ema_strings(
                ["10", "11", "13", "20"],
                ema_period=3, seed_method=SMA, decimal_precision=28,
                decimal_rounding="ROUND_HALF_UP", output_scale=4,
            ),
            ["11.3333", "15.6667"],
        )

    def test_rounding_mode_changes_the_emitted_level(self):
        """period=3, alpha=0.5, closes 10/11, scale 0:
        e1 = 0.5*11 + 0.5*10 = 10.5, an exact tie at the emitted scale.
        """
        cases = {
            "ROUND_HALF_EVEN": ["10", "10"],
            "ROUND_HALF_UP": ["10", "11"],
            "ROUND_DOWN": ["10", "10"],
            "ROUND_UP": ["10", "11"],
            "ROUND_FLOOR": ["10", "10"],
            "ROUND_CEILING": ["10", "11"],
        }
        for rounding, expected in cases.items():
            with self.subTest(rounding=rounding):
                self.assertEqual(
                    ema_strings(
                        ["10", "11"], ema_period=3, seed_method=FIRST,
                        decimal_precision=28, decimal_rounding=rounding, output_scale=0,
                    ),
                    expected,
                )

    def test_output_scale_changes_the_emitted_precision(self):
        self.assertEqual(
            ema_strings(list(DAILY_CLOSES), **{**HAND_DERIVED, "output_scale": 2})[-1],
            "126.94",
        )
        self.assertEqual(
            ema_strings(list(DAILY_CLOSES), **{**HAND_DERIVED, "output_scale": 6})[-1],
            "126.944000",
        )

    def test_decimal_input_and_string_input_agree(self):
        self.assertEqual(
            ema_strings([Decimal("100"), Decimal("110")], **HAND_DERIVED),
            ema_strings(["100", "110"], **HAND_DERIVED),
        )


class EmaSeedAndInputGuardTests(unittest.TestCase):
    def test_seed_index_by_method(self):
        self.assertEqual(TREND.seed_index(4, FIRST), 0)
        self.assertEqual(TREND.seed_index(4, SMA), 3)

    def test_unsupported_seed_method_raises(self):
        with self.assertRaises(TrendError):
            TREND.seed_index(4, "MIDPOINT_OF_WHATEVER")

    def test_sma_seed_requires_full_period_of_closes(self):
        with self.assertRaises(TrendError):
            TREND.compute_ema_series(
                ["10", "11", "12"], ema_period=5, seed_method=SMA,
                decimal_precision=28, decimal_rounding="ROUND_HALF_EVEN", output_scale=4,
            )

    def test_empty_closes_raise(self):
        with self.assertRaises(TrendError):
            TREND.compute_ema_series(
                [], **HAND_DERIVED,
            )

    def test_float_close_is_refused_not_converted(self):
        with self.assertRaises(TrendError):
            ema_strings([100.0, 110.0], **HAND_DERIVED)

    def test_non_positive_or_non_finite_close_refused(self):
        for value in ("0", "-1", "NaN", "Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(TrendError):
                    ema_strings(["100", value], **HAND_DERIVED)

    def test_degenerate_period_one_refused(self):
        with self.assertRaises(TrendError):
            ema_strings(["100", "110"], **{**HAND_DERIVED, "ema_period": 1})


# ---------------------------------------------------------------------------
# Calculation contract -- no implicit defaults anywhere
# ---------------------------------------------------------------------------

class CalculationContractTests(unittest.TestCase):
    def test_complete_contract_normalizes(self):
        normalized = TREND.validate_calculation_contract(calculation_contract())
        self.assertEqual(normalized["contract_version"], TREND.CALCULATION_CONTRACT_VERSION)
        self.assertEqual(normalized["timeframes"]["1d"]["ema_period"], 4)
        self.assertEqual(normalized["timeframes"]["4h"]["seed_method"], SMA)
        self.assertEqual(normalized["rising_lag_bars"], 1)

    def test_every_top_level_field_is_required(self):
        for field in sorted(calculation_contract()):
            with self.subTest(field=field):
                contract = calculation_contract()
                del contract[field]
                with self.assertRaises(TrendError):
                    TREND.validate_calculation_contract(contract)

    def test_every_timeframe_field_is_required(self):
        for timeframe in ("1d", "4h"):
            for field in sorted(_CONTRACT_TIMEFRAME_FIELDS):
                with self.subTest(timeframe=timeframe, field=field):
                    contract = calculation_contract()
                    del contract["timeframes"][timeframe][field]
                    with self.assertRaises(TrendError):
                        TREND.validate_calculation_contract(contract)

    def test_missing_timeframe_is_never_defaulted(self):
        contract = calculation_contract()
        del contract["timeframes"]["4h"]
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_unexpected_contract_field_rejected(self):
        """A caller cannot smuggle an approval-flavoured label into the
        calculation inputs and have it echoed back as part of the parameters.
        """
        contract = calculation_contract()
        contract["investment_policy_ratified"] = True
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_unexpected_timeframe_field_rejected(self):
        contract = calculation_contract()
        contract["timeframes"]["1d"]["approved"] = True
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_null_boolean_and_non_integer_numerics_fail_closed(self):
        bad_values = (None, True, False, "4", 4.0, Decimal("4"))
        for value in bad_values:
            with self.subTest(value=repr(value)):
                contract = calculation_contract()
                contract["timeframes"]["1d"]["ema_period"] = value
                with self.assertRaises(TrendError):
                    TREND.validate_calculation_contract(contract)

    def test_out_of_range_numerics_fail_closed(self):
        cases = [
            ("rising_lag_bars", 0),
            ("decimal_precision", 0),
            ("decimal_precision", TREND.MAX_DECIMAL_PRECISION + 1),
            ("output_scale", -1),
            ("output_scale", TREND.MAX_OUTPUT_SCALE + 1),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                contract = calculation_contract()
                contract[field] = value
                with self.assertRaises(TrendError):
                    TREND.validate_calculation_contract(contract)

    def test_min_finalized_candles_must_be_at_least_one(self):
        contract = calculation_contract(daily_min=0)
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_period_below_two_rejected(self):
        contract = calculation_contract(daily_period=1)
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_unsupported_seed_method_rejected(self):
        for value in (None, "", "EMA_OF_EMA", "first_finalized_close"):
            with self.subTest(value=repr(value)):
                contract = calculation_contract(daily_seed=value)
                with self.assertRaises(TrendError):
                    TREND.validate_calculation_contract(contract)

    def test_unsupported_rounding_mode_rejected(self):
        contract = calculation_contract(decimal_rounding="ROUND_BANKERS")
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_wrong_contract_or_schema_version_rejected(self):
        contract = calculation_contract()
        contract["contract_version"] = "crypto_candidate_trend_calculation/2"
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)
        contract = calculation_contract()
        contract["schema_version"] = True
        with self.assertRaises(TrendError):
            TREND.validate_calculation_contract(contract)

    def test_non_object_contract_rejected(self):
        for value in (None, [], "crypto_candidate_trend_calculation/1"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TrendError):
                    TREND.validate_calculation_contract(value)


# ---------------------------------------------------------------------------
# Full calculation over real P4-07 packets
# ---------------------------------------------------------------------------

class TrendMetricsCalculationTests(unittest.TestCase):
    def test_calculated_metrics_match_hand_derived_values(self):
        metrics = build()
        self.assertEqual(metrics["status"], TREND.STATUS_CALCULATED)
        self.assertEqual(metrics["unavailable_reasons"], [])
        daily = metrics["timeframes"]["1d"]
        self.assertEqual(daily["finalized_candle_count"], 5)
        self.assertEqual(daily["seed_index"], 0)
        self.assertEqual(daily["ema_series_length"], 5)
        self.assertEqual(daily["latest_close"], "140.0000")
        self.assertEqual(daily["latest_ema"], "126.9440")
        self.assertEqual(daily["first_finalized_close_time"], "2026-08-24T00:00:00Z")
        self.assertEqual(daily["latest_finalized_close_time"], "2026-08-28T00:00:00Z")
        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(four_hour["seed_index"], 2)
        self.assertEqual(four_hour["ema_series_length"], 3)
        self.assertEqual(four_hour["latest_ema"], "40.0000")
        self.assertEqual(four_hour["lagged_ema"], "30.0000")
        self.assertEqual(four_hour["lagged_ema_close_time"], "2026-08-27T20:00:00Z")
        self.assertEqual(four_hour["latest_finalized_close_time"], "2026-08-28T00:00:00Z")

    def test_positive_comparisons_on_rising_evidence(self):
        metrics = build()
        self.assertEqual(metrics["comparisons"], {
            "daily_close_above_daily_ema": True,
            "four_hour_ema_rising": True,
        })

    def test_negative_comparisons_on_falling_evidence(self):
        """Daily 140->100 leaves the close under its own EMA; 4h 50->10 leaves
        the EMA below its lagged value.
        """
        packet = market_evidence_packet(
            daily_closes=("140", "130", "120", "110", "100"),
            four_hour_closes=("50", "40", "30", "20", "10"),
        )
        metrics = build(packet)
        self.assertEqual(metrics["status"], TREND.STATUS_CALCULATED)
        self.assertEqual(metrics["timeframes"]["1d"]["latest_ema"], "113.0560")
        self.assertEqual(metrics["timeframes"]["4h"]["latest_ema"], "20.0000")
        self.assertEqual(metrics["timeframes"]["4h"]["lagged_ema"], "30.0000")
        self.assertEqual(metrics["comparisons"], {
            "daily_close_above_daily_ema": False,
            "four_hour_ema_rising": False,
        })

    def test_flat_evidence_yields_false_not_true(self):
        """Both comparisons are strict: equality is not "above" or "rising"."""
        packet = market_evidence_packet(
            daily_closes=("100",) * 5, four_hour_closes=("10",) * 5,
        )
        metrics = build(packet)
        self.assertEqual(metrics["status"], TREND.STATUS_CALCULATED)
        self.assertEqual(metrics["timeframes"]["1d"]["latest_close"], "100.0000")
        self.assertEqual(metrics["timeframes"]["1d"]["latest_ema"], "100.0000")
        self.assertEqual(metrics["comparisons"], {
            "daily_close_above_daily_ema": False,
            "four_hour_ema_rising": False,
        })

    def test_lag_parameter_selects_a_different_bar(self):
        metrics = build(contract=calculation_contract(rising_lag_bars=2))
        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(four_hour["rising_lag_bars"], 2)
        self.assertEqual(four_hour["lagged_ema"], "20.0000")
        self.assertEqual(four_hour["lagged_ema_close_time"], "2026-08-27T16:00:00Z")
        self.assertTrue(metrics["comparisons"]["four_hour_ema_rising"])

    def test_seed_method_changes_the_daily_result(self):
        """The same daily closes under an SMA seed (period 4) start at index 3:
        seed = (100+110+120+130)/4 = 115; e4 = 0.4*140 + 0.6*115 = 56 + 69 = 125.
        """
        metrics = build(contract=calculation_contract(daily_seed=SMA))
        daily = metrics["timeframes"]["1d"]
        self.assertEqual(daily["seed_index"], 3)
        self.assertEqual(daily["ema_series_length"], 2)
        self.assertEqual(daily["latest_ema"], "125.0000")

    def test_status_is_never_a_policy_or_eligibility_label(self):
        for metrics in (build(), build(contract=calculation_contract(daily_min=99))):
            self.assertIn(metrics["status"], TREND.STATUSES)
            self.assertNotIn(metrics["status"], ("PASS", "FAIL", "BUY", "FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE"))

    def test_authority_block_is_calculation_only(self):
        metrics = build()
        self.assertEqual(metrics["authority"], {
            "calculation_only": True,
            "investment_policy_ratified": False,
            "candidate_promotion_authorized": False,
            "buy_authorized": False,
            "order_authorized": False,
            "exchange_authorized": False,
            "real_capital_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })

    def test_source_identity_and_hashes_are_preserved(self):
        packet = market_evidence_packet()
        metrics = build(packet)
        self.assertEqual(metrics["source"]["market"], MARKET)
        self.assertEqual(metrics["source"]["as_of"], "2026-08-28T00:00:00Z")
        self.assertEqual(metrics["source"]["payload_sha256"], packet["payload_sha256"])
        self.assertTrue(metrics["source"]["policy_ratified"])
        self.assertEqual(
            metrics["calculation_contract_sha256"],
            TREND.payload_sha256(TREND.validate_calculation_contract(calculation_contract())),
        )

    def test_deterministic_and_non_mutating(self):
        packet = market_evidence_packet()
        before = TREND.canonical_json(packet)
        contract = calculation_contract()
        contract_before = TREND.canonical_json(contract)
        first = TREND.build_trend_metrics(
            packet, market=MARKET, evaluation_as_of=EVAL_AS_OF, calculation_contract=contract,
        )
        second = TREND.build_trend_metrics(
            packet, market=MARKET, evaluation_as_of=EVAL_AS_OF, calculation_contract=contract,
        )
        self.assertEqual(TREND.canonical_json(first), TREND.canonical_json(second))
        self.assertEqual(first["payload_sha256"], second["payload_sha256"])
        self.assertEqual(TREND.canonical_json(packet), before)
        self.assertEqual(TREND.canonical_json(contract), contract_before)

    def test_parameter_change_creates_new_lineage_not_a_rewrite(self):
        packet = market_evidence_packet()
        base = build(packet)
        variant = build(packet, calculation_contract(daily_period=9))
        self.assertNotEqual(base["calculation_contract_sha256"], variant["calculation_contract_sha256"])
        self.assertNotEqual(base["payload_sha256"], variant["payload_sha256"])
        self.assertNotEqual(base["timeframes"]["1d"]["latest_ema"], variant["timeframes"]["1d"]["latest_ema"])
        # The earlier evidence is untouched and still validates on its own.
        TREND.validate_trend_metrics(base)
        TREND.validate_trend_metrics(variant)


# ---------------------------------------------------------------------------
# UNAVAILABLE: well-formed inputs, insufficient evidence quality/coverage
# ---------------------------------------------------------------------------

class UnavailableEvidenceTests(unittest.TestCase):
    def assert_unavailable(self, metrics, expected_reason):
        self.assertEqual(metrics["status"], TREND.STATUS_UNAVAILABLE)
        self.assertIn(expected_reason, metrics["unavailable_reasons"])
        self.assertEqual(metrics["comparisons"], {
            "daily_close_above_daily_ema": None,
            "four_hour_ema_rising": None,
        })

    def test_below_explicit_minimum_history(self):
        metrics = build(contract=calculation_contract(daily_min=6))
        self.assert_unavailable(metrics, "1d:BELOW_MIN_FINALIZED_CANDLES")
        self.assertIsNone(metrics["timeframes"]["1d"]["latest_ema"])
        self.assertIsNone(metrics["timeframes"]["1d"]["latest_close"])
        # The observed counts are still reported, so the gap is explainable.
        self.assertEqual(metrics["timeframes"]["1d"]["finalized_candle_count"], 5)

    def test_minimum_history_boundary_is_exact(self):
        self.assertEqual(build(contract=calculation_contract(daily_min=5))["status"], TREND.STATUS_CALCULATED)
        self.assertEqual(build(contract=calculation_contract(daily_min=6))["status"], TREND.STATUS_UNAVAILABLE)

    def test_sma_seed_needs_a_full_period_of_history(self):
        metrics = build(contract=calculation_contract(daily_seed=SMA, daily_period=6, daily_min=1))
        self.assert_unavailable(metrics, "1d:INSUFFICIENT_FINALIZED_CANDLES_FOR_SEED")

    def test_lag_longer_than_the_series_is_unavailable(self):
        metrics = build(contract=calculation_contract(rising_lag_bars=3))
        self.assert_unavailable(metrics, "4h:INSUFFICIENT_EMA_SERIES_FOR_LAG")
        self.assertIsNone(metrics["timeframes"]["4h"]["lagged_ema"])

    def test_lag_boundary_is_exact(self):
        self.assertEqual(build(contract=calculation_contract(rising_lag_bars=2))["status"], TREND.STATUS_CALCULATED)
        self.assertEqual(build(contract=calculation_contract(rising_lag_bars=3))["status"], TREND.STATUS_UNAVAILABLE)

    def test_stale_four_hour_evidence_fails_closed_per_timeframe(self):
        """P4-07's own ratified staleness policy marks 4h STALE at a 23h age
        while 1d is still FRESH. No new threshold is invented here.
        """
        packet = market_evidence_packet(captured_at=dt.datetime(2026, 8, 28, 23, 0, 0, tzinfo=UTC))
        metrics = build(packet)
        self.assert_unavailable(metrics, "4h:CANDLE_NOT_FRESH:STALE")
        self.assertIn("4h:EVIDENCE_STATUS_NOT_PASS:UNKNOWN", metrics["unavailable_reasons"])
        self.assertFalse([r for r in metrics["unavailable_reasons"] if r.startswith("1d:")])

    def test_duplicate_candle_rows_fail_closed(self):
        metrics = build(market_evidence_packet(daily_duplicate=True))
        self.assert_unavailable(metrics, "1d:DUPLICATE_CANDLE_ROWS")

    def test_candle_gap_fails_closed(self):
        metrics = build(market_evidence_packet(daily_gap=True), calculation_contract(daily_min=1))
        self.assert_unavailable(metrics, "1d:CANDLE_GAP")

    def test_one_bad_timeframe_nulls_both_comparisons_but_still_reports_the_healthy_one(self):
        """A single failing timeframe is enough to withhold every comparison,
        so no consumer can read a partial ``true``. The healthy timeframe's own
        metrics are still reported, and the failing one's are null, so the
        reason list is explainable rather than opaque.
        """
        packet = market_evidence_packet(captured_at=dt.datetime(2026, 8, 28, 23, 0, 0, tzinfo=UTC))
        metrics = build(packet)
        self.assertEqual(metrics["status"], TREND.STATUS_UNAVAILABLE)
        self.assertEqual(metrics["comparisons"], {
            "daily_close_above_daily_ema": None,
            "four_hour_ema_rising": None,
        })
        self.assertIsNone(metrics["timeframes"]["4h"]["latest_ema"])
        self.assertEqual(metrics["timeframes"]["1d"]["latest_ema"], "126.9440")

    def test_unavailable_output_still_validates_and_is_deterministic(self):
        metrics = build(contract=calculation_contract(daily_min=6))
        TREND.validate_trend_metrics(metrics)
        self.assertEqual(
            TREND.canonical_json(build(contract=calculation_contract(daily_min=6))),
            TREND.canonical_json(metrics),
        )


# ---------------------------------------------------------------------------
# Hard fail-closed: malformed / inconsistent / tampered sources
# ---------------------------------------------------------------------------

class SourceIntegrityTests(unittest.TestCase):
    def test_missing_or_non_object_packet_raises(self):
        for value in (None, [], "packet"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TrendError):
                    TREND.build_trend_metrics(
                        value, market=MARKET, evaluation_as_of=EVAL_AS_OF,
                        calculation_contract=calculation_contract(),
                    )

    def test_market_mismatch_raises(self):
        with self.assertRaises(TrendError):
            build(market_evidence_packet(market="KRW-BTC"), market=MARKET)

    def test_invalid_evaluation_as_of_raises(self):
        for value in ("2026-8-28", "2026-08-28T00:00:00Z", "", None):
            with self.subTest(value=repr(value)):
                with self.assertRaises(TrendError):
                    build(evaluation_as_of=value)

    def test_tampered_packet_payload_hash_raises(self):
        packet = market_evidence_packet()
        packet["payload_sha256"] = "0" * 64
        with self.assertRaises(TrendError):
            build(packet)

    def test_edited_close_without_rehash_raises(self):
        packet = market_evidence_packet()
        packet["candles"]["1d"]["finalized_candles"][-1]["trade_price"] = "999999"
        with self.assertRaises(TrendError):
            build(packet)

    def test_unfinalized_row_inside_finalized_candles_raises(self):
        """A row whose close time has not elapsed as of the packet's own
        ``as_of`` is not finalized evidence, even in a self-consistent packet.
        """
        packet = market_evidence_packet()
        block = packet["candles"]["1d"]
        future = copy.deepcopy(block["finalized_candles"][-1])
        future["open_time"] = "2026-08-28T00:00:00Z"
        future["close_time"] = "2026-08-29T00:00:00Z"
        block["finalized_candles"].append(future)
        block["finalized_candle_count"] += 1
        with self.assertRaises(TrendError):
            build(resign_packet(packet))

    def test_duplicated_close_time_inside_finalized_candles_raises(self):
        packet = market_evidence_packet()
        block = packet["candles"]["1d"]
        block["finalized_candles"].append(copy.deepcopy(block["finalized_candles"][-1]))
        block["finalized_candle_count"] += 1
        with self.assertRaises(TrendError):
            build(resign_packet(packet))

    def test_out_of_order_finalized_candles_raise(self):
        packet = market_evidence_packet()
        block = packet["candles"]["1d"]
        block["finalized_candles"].reverse()
        with self.assertRaises(TrendError):
            build(resign_packet(packet))

    def test_non_positive_close_price_raises(self):
        packet = market_evidence_packet()
        packet["candles"]["1d"]["finalized_candles"][-1]["trade_price"] = "0"
        with self.assertRaises(TrendError):
            build(resign_packet(packet))

    def test_caller_cannot_fabricate_evidence_authority(self):
        packet = market_evidence_packet()
        packet["authority"]["order_authorized"] = True
        with self.assertRaises(TrendError):
            build(resign_packet(packet))


# ---------------------------------------------------------------------------
# Deterministic validation / tamper refusal, including self-rehash
# ---------------------------------------------------------------------------

class ValidationTamperTests(unittest.TestCase):
    def test_valid_metrics_round_trip(self):
        metrics = build()
        self.assertEqual(
            TREND.canonical_json(TREND.validate_trend_metrics(metrics)),
            TREND.canonical_json(metrics),
        )

    def test_supplied_matching_packet_accepted(self):
        packet = market_evidence_packet()
        metrics = build(packet)
        TREND.validate_trend_metrics(metrics, market_evidence_packet=packet)

    def test_supplied_different_packet_rejected(self):
        metrics = build()
        other = market_evidence_packet(daily_closes=("100",) * 5)
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(metrics, market_evidence_packet=other)

    def test_edited_metric_without_rehash_rejected(self):
        metrics = build()
        metrics["timeframes"]["1d"]["latest_ema"] = "999.0000"
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(metrics)

    def test_edited_metric_with_self_rehash_still_rejected(self):
        """The central anti-forgery property: recomputing ``payload_sha256``
        over an edited metric does not make it valid, because validation
        re-derives from the embedded exact sources.
        """
        metrics = build()
        metrics["timeframes"]["1d"]["latest_ema"] = "1.0000"
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_flipped_comparison_with_self_rehash_rejected(self):
        metrics = build(market_evidence_packet(four_hour_closes=("50", "40", "30", "20", "10")))
        self.assertFalse(metrics["comparisons"]["four_hour_ema_rising"])
        metrics["comparisons"]["four_hour_ema_rising"] = True
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_flipped_status_rejected(self):
        metrics = build(contract=calculation_contract(daily_min=6))
        metrics["status"] = TREND.STATUS_CALCULATED
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_non_calculation_status_label_rejected(self):
        metrics = build()
        metrics["status"] = "PASS"
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_elevated_authority_rejected(self):
        for field in ("candidate_promotion_authorized", "investment_policy_ratified", "order_authorized"):
            with self.subTest(field=field):
                metrics = build()
                metrics["authority"][field] = True
                with self.assertRaises(TrendError):
                    TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_removed_calculation_only_flag_rejected(self):
        metrics = build()
        metrics["authority"]["calculation_only"] = False
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_edited_contract_without_matching_digest_rejected(self):
        metrics = build()
        metrics["calculation_contract"]["output_scale"] = 6
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_edited_contract_with_recomputed_digest_still_rejected(self):
        """Re-signing the parameter block too does not help: the metrics no
        longer match what those parameters actually produce.
        """
        metrics = build()
        metrics["calculation_contract"]["output_scale"] = 6
        metrics["calculation_contract_sha256"] = TREND.payload_sha256(metrics["calculation_contract"])
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_swapped_source_packet_rejected(self):
        metrics = build()
        metrics["source_packet"] = market_evidence_packet(daily_closes=("100",) * 5)
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_edited_source_packet_with_full_rehash_rejected(self):
        metrics = build()
        packet = copy.deepcopy(metrics["source_packet"])
        packet["candles"]["1d"]["finalized_candles"][-1]["trade_price"] = "1000"
        metrics["source_packet"] = resign_packet(packet)
        metrics["source"]["payload_sha256"] = metrics["source_packet"]["payload_sha256"]
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))

    def test_schema_and_key_set_pinned(self):
        metrics = build()
        metrics["extra_field"] = "x"
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))
        metrics = build()
        del metrics["comparisons"]
        with self.assertRaises(TrendError):
            TREND.validate_trend_metrics(resign_metrics(metrics))


# ---------------------------------------------------------------------------
# The existing P5-08 / P5-09 functions are genuinely unchanged
# ---------------------------------------------------------------------------

class ExistingBehaviourUnchangedTests(unittest.TestCase):
    """Imports the ACTUAL production evaluators and proves they still return
    exactly what they returned before, on the very packet where the new
    calculator reports CALCULATED with both comparisons true.
    """

    def setUp(self):
        self.packet = market_evidence_packet()
        self.metrics = build(self.packet)
        self.assertEqual(self.metrics["status"], TREND.STATUS_CALCULATED)
        self.assertTrue(self.metrics["comparisons"]["daily_close_above_daily_ema"])
        self.assertTrue(self.metrics["comparisons"]["four_hour_ema_rising"])

    def test_p5_08_evaluate_trend_still_unknown_with_no_ratified_rule(self):
        result = PROMO.evaluate_trend(MARKET, self.packet)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "NO_RATIFIED_CANDIDATE_TREND_RULE")
        self.assertEqual(result["daily_direction"], "UP")
        self.assertEqual(result["four_hour_direction"], "UP")

    def test_p5_08_evaluate_trend_exposes_no_ema_fields(self):
        result = PROMO.evaluate_trend(MARKET, self.packet)
        self.assertEqual(set(result), {"status", "reason", "daily_direction", "four_hour_direction"})

    def test_p5_08_evaluate_trend_is_byte_identical_before_and_after_calculation(self):
        before = PROMO.evaluate_trend(MARKET, self.packet)
        build(self.packet)
        after = PROMO.evaluate_trend(MARKET, self.packet)
        self.assertEqual(TREND.canonical_json(before), TREND.canonical_json(after))

    def test_p5_09_trigger_alignment_is_byte_identical_before_and_after_calculation(self):
        before = P59.evaluate_trigger_timeframe_alignment(self.packet)
        build(self.packet)
        after = P59.evaluate_trigger_timeframe_alignment(self.packet)
        self.assertEqual(TREND.canonical_json(before), TREND.canonical_json(after))

    def test_p5_09_trigger_alignment_still_uses_only_two_close_directions(self):
        result = P59.evaluate_trigger_timeframe_alignment(self.packet)
        self.assertEqual(
            set(result),
            {"status", "reason", "fifteen_minute_direction", "trigger_direction",
             "four_hour_direction", "daily_direction"},
        )

    def test_source_packet_is_not_mutated_by_calculation(self):
        before = TREND.canonical_json(self.packet)
        build(self.packet)
        PROMO.evaluate_trend(MARKET, self.packet)
        P59.evaluate_trigger_timeframe_alignment(self.packet)
        self.assertEqual(TREND.canonical_json(self.packet), before)

    def test_calculator_is_not_wired_into_production_modules(self):
        for relative in ("universe/crypto_candidate_promotion.py", "universe/crypto_paper_buy_eligibility.py"):
            with self.subTest(module=relative):
                self.assertNotIn(
                    "crypto_candidate_trend_metrics",
                    (ROOT / relative).read_text(encoding="utf-8"),
                )


if __name__ == "__main__":
    unittest.main()
