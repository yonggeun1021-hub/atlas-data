#!/usr/bin/env python3
"""P5-08 crypto candidate price-distance observation regression.

Every expected fraction in this file is derived by hand (the arithmetic is
written out in each math test's docstring) and compared against the real
implementation -- the calculator is never mocked to "prove" a positive number.

Source fixtures are built by the merged trend suite's own P4-07 packet builder
(``test/test_crypto_candidate_trend_metrics.py::market_evidence_packet``),
which uses the actual ``build_market_evidence_packet`` against the committed
ratified P4 policy. That module is imported, not copied, so this suite cannot
drift away from the packets the EMA calculator is already verified against, and
the unchanged EMA tests are not repeated here.

The close series and parameters here are synthetic calculation examples chosen
so the arithmetic is exactly hand-checkable. They are not a ratification of any
investment parameter, and no threshold, bound or verdict is asserted anywhere.
"""
from __future__ import annotations

import copy
from decimal import Decimal
import importlib.util
import inspect
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PD = _load("crypto_candidate_price_distance_metrics",
           "universe/crypto_candidate_price_distance_metrics.py")
# The exact trend module PD itself loaded -- so error identity is shared.
TREND = PD.TREND
PROMO = TREND.PROMOTION
# Fixture reuse only. Importing the module binds its helper functions here;
# its TestCase classes are deliberately not imported into this namespace, so
# the merged EMA suite is not re-run by this focused suite.
TRENDTEST = _load("test_crypto_candidate_trend_metrics_fixtures",
                  "test/test_crypto_candidate_trend_metrics.py")

PDError = PD.CryptoCandidatePriceDistanceMetricsError

MARKET = TRENDTEST.MARKET
EVAL_AS_OF = TRENDTEST.EVAL_AS_OF
SMA = TRENDTEST.SMA
FIRST = TRENDTEST.FIRST

# 1d rises into its own EMA; 4h falls away from its own EMA. Both series are
# chosen so the SMA seed lands on a whole number and every hand-derived
# fraction below terminates exactly.
DAILY_CLOSES = ("90", "100", "110")
FOUR_HOUR_CLOSES = ("120", "100", "80", "60")

LATEST_DAILY_CLOSE_TIME = "2026-08-28T00:00:00Z"
PRIOR_DAILY_CLOSE_TIME = "2026-08-27T00:00:00Z"
OLDEST_DAILY_CLOSE_TIME = "2026-08-26T00:00:00Z"
LATEST_FOUR_HOUR_CLOSE_TIME = "2026-08-28T00:00:00Z"
PRIOR_FOUR_HOUR_CLOSE_TIME = "2026-08-27T20:00:00Z"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def market_evidence_packet(**kwargs) -> dict:
    """The merged suite's real P4-07 packet builder, with this suite's series."""
    kwargs.setdefault("daily_closes", DAILY_CLOSES)
    kwargs.setdefault("four_hour_closes", FOUR_HOUR_CLOSES)
    return TRENDTEST.market_evidence_packet(**kwargs)


def trend_contract(*, output_scale=4, decimal_rounding="ROUND_HALF_EVEN") -> dict:
    """A complete, explicit trend contract -- unchanged PR603 shape."""
    return TRENDTEST.calculation_contract(
        daily_period=3, daily_seed=SMA, daily_min=3,
        four_hour_period=3, four_hour_seed=SMA, four_hour_min=4,
        rising_lag_bars=1, decimal_precision=28,
        decimal_rounding=decimal_rounding, output_scale=output_scale,
    )


def price_distance_contract(
    *,
    daily_return_lag=1,
    four_hour_return_lag=1,
    fraction_output_scale=8,
    output_scale=4,
    decimal_rounding="ROUND_HALF_EVEN",
) -> dict:
    """A complete, explicit price-distance contract.

    Nothing here is a default: both ``return_lag_candles`` values and the
    fraction scale are spelled out on every call, and the module refuses the
    call when any one of them is absent.
    """
    return {
        "schema_version": 1,
        "contract_version": PD.CALCULATION_CONTRACT_VERSION,
        "trend_calculation_contract": trend_contract(
            output_scale=output_scale, decimal_rounding=decimal_rounding,
        ),
        "timeframes": {
            "1d": {"return_lag_candles": daily_return_lag},
            "4h": {"return_lag_candles": four_hour_return_lag},
        },
        "fraction_output_scale": fraction_output_scale,
    }


def build(packet=None, contract=None, *, market=MARKET, evaluation_as_of=EVAL_AS_OF) -> dict:
    return PD.build_price_distance_metrics(
        market_evidence_packet() if packet is None else packet,
        market=market,
        evaluation_as_of=evaluation_as_of,
        calculation_contract=price_distance_contract() if contract is None else contract,
    )


def validate(metrics, packet, contract, *, market=MARKET, evaluation_as_of=EVAL_AS_OF) -> dict:
    return PD.validate_price_distance_metrics(
        metrics,
        market=market,
        evaluation_as_of=evaluation_as_of,
        market_evidence_packet=packet,
        calculation_contract=contract,
    )


def resign(metrics: dict) -> dict:
    """A caller's self-rehash after editing an emitted value."""
    edited = copy.deepcopy(metrics)
    edited.pop("payload_sha256", None)
    edited["payload_sha256"] = PD.payload_sha256(edited)
    return edited


def resign_contract_and_payload(metrics: dict) -> dict:
    """A more determined self-rehash: the embedded contract digest is
    recomputed too, so the output is fully internally consistent.
    """
    edited = copy.deepcopy(metrics)
    edited["calculation_contract_sha256"] = PD.payload_sha256(edited["calculation_contract"])
    return resign(edited)


# ---------------------------------------------------------------------------
# Hand-derived observation arithmetic
# ---------------------------------------------------------------------------

class HandDerivedObservationTests(unittest.TestCase):
    """1d closes 90/100/110, 4h closes 120/100/80/60, both period 3 + SMA seed.

        1d seed (the only 1d EMA level) = (90 + 100 + 110) / 3 = 100
        4h seed                         = (120 + 100 + 80) / 3 = 100
        4h e3 = 0.5*60 + 0.5*100 = 30 + 50                     = 80
    """

    def test_daily_close_above_its_ema_is_a_positive_fraction(self):
        """(110 - 100) / 100 = 10/100 = 0.1 exactly."""
        daily = build()["timeframes"]["1d"]
        self.assertEqual(daily["latest_close"], "110.0000")
        self.assertEqual(daily["latest_ema"], "100.0000")
        self.assertEqual(daily["close_to_ema_fraction"], "0.10000000")
        self.assertEqual(daily["close_to_ema_denominator_status"], PD.DENOMINATOR_AVAILABLE)

    def test_four_hour_close_below_its_ema_is_a_negative_fraction(self):
        """(60 - 80) / 80 = -20/80 = -0.25 exactly."""
        four_hour = build()["timeframes"]["4h"]
        self.assertEqual(four_hour["latest_close"], "60.0000")
        self.assertEqual(four_hour["latest_ema"], "80.0000")
        self.assertEqual(four_hour["close_to_ema_fraction"], "-0.25000000")

    def test_lagged_return_uses_the_close_that_many_candles_back(self):
        """1d lag 1: (110 - 100) / 100 = 0.1;  4h lag 1: (60 - 80) / 80 = -0.25."""
        metrics = build()
        daily = metrics["timeframes"]["1d"]
        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(daily["lagged_close"], "100.0000")
        self.assertEqual(daily["lagged_close_return_fraction"], "0.10000000")
        self.assertEqual(four_hour["lagged_close"], "80.0000")
        self.assertEqual(four_hour["lagged_close_return_fraction"], "-0.25000000")

    def test_each_timeframe_uses_its_own_explicit_lag(self):
        """1d lag 2: (110 - 90) / 90 = 20/90 = 0.2222...  -> 0.22222222 at scale 8.
        4h lag 3: (60 - 120) / 120 = -60/120 = -0.5 exactly.
        The two lags are independent; neither falls back to the other.
        """
        metrics = build(contract=price_distance_contract(
            daily_return_lag=2, four_hour_return_lag=3,
        ))
        daily = metrics["timeframes"]["1d"]
        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(daily["return_lag_candles"], 2)
        self.assertEqual(daily["lagged_close"], "90.0000")
        self.assertEqual(daily["lagged_close_return_fraction"], "0.22222222")
        self.assertEqual(four_hour["return_lag_candles"], 3)
        self.assertEqual(four_hour["lagged_close"], "120.0000")
        self.assertEqual(four_hour["lagged_close_return_fraction"], "-0.50000000")

    def test_values_are_fractions_never_percentages(self):
        """A +10% move is reported as 0.1, not 10 and not "10%"."""
        daily = build()["timeframes"]["1d"]
        for key in ("close_to_ema_fraction", "lagged_close_return_fraction"):
            with self.subTest(key=key):
                value = daily[key]
                self.assertNotIn("%", value)
                self.assertEqual(Decimal(value), Decimal("0.1"))
                self.assertNotEqual(Decimal(value), Decimal("10"))

    def test_time_endpoints_are_preserved_from_the_source_rows(self):
        metrics = build()
        daily = metrics["timeframes"]["1d"]
        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(daily["latest_finalized_close_time"], LATEST_DAILY_CLOSE_TIME)
        self.assertEqual(daily["lagged_close_time"], PRIOR_DAILY_CLOSE_TIME)
        self.assertEqual(daily["finalized_candle_count"], 3)
        self.assertEqual(four_hour["latest_finalized_close_time"], LATEST_FOUR_HOUR_CLOSE_TIME)
        self.assertEqual(four_hour["lagged_close_time"], PRIOR_FOUR_HOUR_CLOSE_TIME)
        self.assertEqual(four_hour["finalized_candle_count"], 4)

    def test_lag_two_reaches_the_oldest_daily_close_time(self):
        metrics = build(contract=price_distance_contract(daily_return_lag=2))
        self.assertEqual(
            metrics["timeframes"]["1d"]["lagged_close_time"], OLDEST_DAILY_CLOSE_TIME,
        )

    def test_fraction_output_scale_changes_the_emitted_precision(self):
        for scale, expected in ((2, "0.10"), (0, "0"), (8, "0.10000000")):
            with self.subTest(scale=scale):
                metrics = build(contract=price_distance_contract(fraction_output_scale=scale))
                self.assertEqual(
                    metrics["timeframes"]["1d"]["close_to_ema_fraction"], expected,
                )

    def test_declared_rounding_mode_binds_the_fraction(self):
        """20/90 = 0.2222... repeating. The seed is exactly 100 either way, so
        only the fraction's rounding can move -- and it does.
        """
        contract = price_distance_contract(daily_return_lag=2)
        self.assertEqual(
            build(contract=contract)["timeframes"]["1d"]["lagged_close_return_fraction"],
            "0.22222222",
        )
        ceiling = price_distance_contract(daily_return_lag=2, decimal_rounding="ROUND_CEILING")
        self.assertEqual(
            build(contract=ceiling)["timeframes"]["1d"]["lagged_close_return_fraction"],
            "0.22222223",
        )

    def test_healthy_payload_status_and_no_reasons(self):
        metrics = build()
        self.assertEqual(metrics["status"], PD.STATUS_CALCULATED)
        self.assertEqual(metrics["unavailable_reasons"], [])
        self.assertEqual(metrics["market"], MARKET)
        self.assertEqual(metrics["evaluation_as_of"], EVAL_AS_OF)

    def test_observation_fraction_is_directly_callable_and_pure(self):
        """The public helper is the same arithmetic, reusable in isolation."""
        value = PD.observation_fraction(
            Decimal("110"), Decimal("100"),
            decimal_precision=28, decimal_rounding="ROUND_HALF_EVEN",
            fraction_output_scale=8, label="unit",
        )
        self.assertEqual(str(value), "0.10000000")

    def test_observation_fraction_refuses_a_zero_denominator(self):
        with self.assertRaises(PDError):
            PD.observation_fraction(
                Decimal("110"), Decimal("0"),
                decimal_precision=28, decimal_rounding="ROUND_HALF_EVEN",
                fraction_output_scale=8, label="unit",
            )


# ---------------------------------------------------------------------------
# Calculation contract -- no implicit defaults anywhere
# ---------------------------------------------------------------------------

class CalculationContractTests(unittest.TestCase):
    def test_complete_contract_normalizes(self):
        normalized = PD.validate_calculation_contract(price_distance_contract())
        self.assertEqual(normalized["contract_version"], PD.CALCULATION_CONTRACT_VERSION)
        self.assertEqual(normalized["timeframes"]["1d"]["return_lag_candles"], 1)
        self.assertEqual(normalized["timeframes"]["4h"]["return_lag_candles"], 1)
        self.assertEqual(normalized["fraction_output_scale"], 8)
        self.assertEqual(
            normalized["trend_calculation_contract"]["contract_version"],
            TREND.CALCULATION_CONTRACT_VERSION,
        )

    def test_every_top_level_field_is_required(self):
        for field in sorted(price_distance_contract()):
            with self.subTest(field=field):
                contract = price_distance_contract()
                del contract[field]
                with self.assertRaises(PDError):
                    PD.validate_calculation_contract(contract)

    def test_both_return_lags_are_required(self):
        for timeframe in ("1d", "4h"):
            with self.subTest(timeframe=timeframe):
                contract = price_distance_contract()
                del contract["timeframes"][timeframe]["return_lag_candles"]
                with self.assertRaises(PDError):
                    PD.validate_calculation_contract(contract)

    def test_a_missing_timeframe_is_never_defaulted_from_the_other(self):
        contract = price_distance_contract()
        del contract["timeframes"]["4h"]
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)

    def test_return_lag_must_be_an_explicit_positive_integer(self):
        for value in (None, True, False, 0, -1, "1", 1.0, Decimal("1")):
            with self.subTest(value=repr(value)):
                contract = price_distance_contract()
                contract["timeframes"]["1d"]["return_lag_candles"] = value
                with self.assertRaises(PDError):
                    PD.validate_calculation_contract(contract)

    def test_fraction_output_scale_bounds(self):
        for value in (-1, PD.MAX_OUTPUT_SCALE + 1, None, True, "8", 8.0):
            with self.subTest(value=repr(value)):
                contract = price_distance_contract()
                contract["fraction_output_scale"] = value
                with self.assertRaises(PDError):
                    PD.validate_calculation_contract(contract)

    def test_unexpected_field_rejected_at_both_levels(self):
        contract = price_distance_contract()
        contract["investment_policy_ratified"] = True
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)
        contract = price_distance_contract()
        contract["timeframes"]["1d"]["approved"] = True
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)

    def test_wrong_contract_or_schema_version_rejected(self):
        contract = price_distance_contract()
        contract["contract_version"] = "crypto_candidate_price_distance_calculation/2"
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)
        contract = price_distance_contract()
        contract["schema_version"] = True
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)

    def test_nested_trend_contract_is_validated_not_bypassed(self):
        contract = price_distance_contract()
        del contract["trend_calculation_contract"]["timeframes"]["1d"]["ema_period"]
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)
        contract = price_distance_contract()
        contract["trend_calculation_contract"]["decimal_rounding"] = "ROUND_BANKERS"
        with self.assertRaises(PDError):
            PD.validate_calculation_contract(contract)

    def test_both_lags_belong_to_the_complete_contract_digest(self):
        """Changing either lag alone changes the single contract digest, so a
        lag can never be swapped without producing different lineage.
        """
        base = build()
        base_digest = base["calculation_contract_sha256"]
        for kwargs in ({"daily_return_lag": 2}, {"four_hour_return_lag": 2}):
            with self.subTest(**kwargs):
                other = build(contract=price_distance_contract(**kwargs))
                self.assertNotEqual(other["calculation_contract_sha256"], base_digest)
        # The EMA parameters are inside the same digest too.
        scaled = build(contract=price_distance_contract(output_scale=2))
        self.assertNotEqual(scaled["calculation_contract_sha256"], base_digest)

    def test_emitted_contract_matches_its_digest(self):
        metrics = build()
        self.assertEqual(
            PD.payload_sha256(metrics["calculation_contract"]),
            metrics["calculation_contract_sha256"],
        )
        self.assertEqual(metrics["calculation_contract"]["timeframes"]["1d"]["return_lag_candles"], 1)
        self.assertEqual(metrics["calculation_contract"]["timeframes"]["4h"]["return_lag_candles"], 1)


# ---------------------------------------------------------------------------
# Named zero denominators
# ---------------------------------------------------------------------------

class ZeroDenominatorTests(unittest.TestCase):
    def test_zero_emitted_ema_and_lagged_close_are_named_not_divided_by(self):
        """Closes of 0.4 are positive (so no raise), but every emitted value
        quantizes to 0 at output_scale 0. Both denominators are then exactly
        zero: each observation is null with its own named status.
        """
        packet = market_evidence_packet(
            daily_closes=("0.4", "0.4", "0.4"),
            four_hour_closes=("0.4", "0.4", "0.4", "0.4"),
        )
        metrics = build(packet=packet, contract=price_distance_contract(output_scale=0))
        self.assertEqual(metrics["status"], PD.STATUS_UNAVAILABLE)
        for timeframe in ("1d", "4h"):
            with self.subTest(timeframe=timeframe):
                block = metrics["timeframes"][timeframe]
                self.assertEqual(block["latest_ema"], "0")
                self.assertIsNone(block["close_to_ema_fraction"])
                self.assertEqual(
                    block["close_to_ema_denominator_status"], PD.DENOMINATOR_ZERO_LATEST_EMA,
                )
                self.assertEqual(block["lagged_close"], "0")
                self.assertIsNone(block["lagged_close_return_fraction"])
                self.assertEqual(
                    block["lagged_close_return_denominator_status"],
                    PD.DENOMINATOR_ZERO_LAGGED_CLOSE,
                )
                self.assertIn(
                    f"{timeframe}:{PD.DENOMINATOR_ZERO_LATEST_EMA}",
                    metrics["unavailable_reasons"],
                )
                self.assertIn(
                    f"{timeframe}:{PD.DENOMINATOR_ZERO_LAGGED_CLOSE}",
                    metrics["unavailable_reasons"],
                )

    def test_one_zero_denominator_does_not_suppress_the_healthy_observation(self):
        """1d closes 0.4/0.4/3 at output_scale 0:

            seed = (0.4 + 0.4 + 3) / 3 = 1.2666...  -> 1 at scale 0
            latest close 3 -> 3;  (3 - 1) / 1 = 2 exactly       (available)
            lagged close 0.4 -> 0                              (named zero)

        The 4h block on the same packet stays fully healthy, so this proves
        independence both within and across timeframes.
        """
        packet = market_evidence_packet(daily_closes=("0.4", "0.4", "3"))
        metrics = build(packet=packet, contract=price_distance_contract(output_scale=0))
        daily = metrics["timeframes"]["1d"]
        self.assertEqual(daily["latest_ema"], "1")
        self.assertEqual(daily["close_to_ema_fraction"], "2.00000000")
        self.assertEqual(daily["close_to_ema_denominator_status"], PD.DENOMINATOR_AVAILABLE)
        self.assertIsNone(daily["lagged_close_return_fraction"])
        self.assertEqual(
            daily["lagged_close_return_denominator_status"], PD.DENOMINATOR_ZERO_LAGGED_CLOSE,
        )

        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(four_hour["close_to_ema_fraction"], "-0.25000000")
        self.assertEqual(four_hour["lagged_close_return_fraction"], "-0.25000000")
        self.assertEqual(metrics["status"], PD.STATUS_UNAVAILABLE)
        self.assertEqual(
            metrics["unavailable_reasons"], [f"1d:{PD.DENOMINATOR_ZERO_LAGGED_CLOSE}"],
        )


# ---------------------------------------------------------------------------
# Source / history unavailability -- P4-07 guards reused, never re-derived
# ---------------------------------------------------------------------------

class SourceAndHistoryUnavailabilityTests(unittest.TestCase):
    def test_duplicate_rows_make_that_timeframe_unavailable_only(self):
        metrics = build(packet=market_evidence_packet(daily_duplicate=True))
        daily = metrics["timeframes"]["1d"]
        self.assertEqual(metrics["status"], PD.STATUS_UNAVAILABLE)
        self.assertIsNone(daily["close_to_ema_fraction"])
        self.assertIsNone(daily["lagged_close_return_fraction"])
        self.assertEqual(
            daily["close_to_ema_denominator_status"], PD.DENOMINATOR_SOURCE_UNAVAILABLE,
        )
        self.assertEqual(
            daily["lagged_close_return_denominator_status"], PD.DENOMINATOR_SOURCE_UNAVAILABLE,
        )
        self.assertIn("1d:DUPLICATE_CANDLE_ROWS", metrics["unavailable_reasons"])
        # The healthy timeframe is still fully reported.
        four_hour = metrics["timeframes"]["4h"]
        self.assertEqual(four_hour["close_to_ema_fraction"], "-0.25000000")
        self.assertEqual(four_hour["lagged_close_return_fraction"], "-0.25000000")
        self.assertFalse([r for r in metrics["unavailable_reasons"] if r.startswith("4h:")])

    def test_candle_gap_makes_that_timeframe_unavailable_only(self):
        metrics = build(packet=market_evidence_packet(daily_gap=True))
        self.assertEqual(metrics["status"], PD.STATUS_UNAVAILABLE)
        self.assertIn("1d:CANDLE_GAP", metrics["unavailable_reasons"])
        self.assertIsNone(metrics["timeframes"]["1d"]["close_to_ema_fraction"])
        self.assertEqual(metrics["timeframes"]["4h"]["close_to_ema_fraction"], "-0.25000000")

    def test_stale_evidence_is_inherited_from_p4_not_reinvented(self):
        """A far-later capture time is P4-07's own staleness call (its ratified
        policy allows 172800s on 1d and 28800s on 4h; three days exceeds both).
        This module adds no freshness threshold of its own; it consumes the
        published result.
        """
        import datetime as dt

        packet = market_evidence_packet(
            captured_at=TRENDTEST.CAPTURED_AT + dt.timedelta(days=3),
        )
        metrics = build(packet=packet, evaluation_as_of="2026-08-31")
        self.assertEqual(metrics["status"], PD.STATUS_UNAVAILABLE)
        self.assertTrue(
            any("CANDLE_NOT_FRESH" in reason for reason in metrics["unavailable_reasons"]),
            metrics["unavailable_reasons"],
        )
        for timeframe in ("1d", "4h"):
            block = metrics["timeframes"][timeframe]
            self.assertIsNone(block["close_to_ema_fraction"])
            self.assertIsNone(block["lagged_close_return_fraction"])

    def test_insufficient_history_for_the_lag_leaves_the_ema_distance_reported(self):
        """3 finalized daily candles cannot support a 5-candle return, but the
        close-to-EMA distance on the same timeframe is unaffected.
        """
        metrics = build(contract=price_distance_contract(daily_return_lag=5))
        daily = metrics["timeframes"]["1d"]
        self.assertEqual(metrics["status"], PD.STATUS_UNAVAILABLE)
        self.assertEqual(daily["close_to_ema_fraction"], "0.10000000")
        self.assertEqual(daily["close_to_ema_denominator_status"], PD.DENOMINATOR_AVAILABLE)
        self.assertIsNone(daily["lagged_close_return_fraction"])
        self.assertIsNone(daily["lagged_close"])
        self.assertIsNone(daily["lagged_close_time"])
        self.assertEqual(
            daily["lagged_close_return_denominator_status"], PD.DENOMINATOR_SOURCE_UNAVAILABLE,
        )
        self.assertEqual(
            metrics["unavailable_reasons"],
            ["1d:INSUFFICIENT_FINALIZED_CANDLES_FOR_RETURN_LAG"],
        )

    def test_exact_lag_boundary_is_usable(self):
        """3 closes support a lag of exactly 2, and not 3."""
        usable = build(contract=price_distance_contract(daily_return_lag=2))
        self.assertEqual(usable["timeframes"]["1d"]["lagged_close_return_fraction"], "0.22222222")
        unusable = build(contract=price_distance_contract(daily_return_lag=3))
        self.assertIsNone(unusable["timeframes"]["1d"]["lagged_close_return_fraction"])

    def test_unavailable_source_never_yields_a_return_off_rejected_evidence(self):
        """The rows are physically present under a duplicate-flagged 1d block,
        but the return is still withheld rather than computed off evidence
        P4-07 already refused.
        """
        metrics = build(packet=market_evidence_packet(daily_duplicate=True))
        daily = metrics["timeframes"]["1d"]
        self.assertGreaterEqual(daily["finalized_candle_count"], 2)
        self.assertIsNone(daily["lagged_close"])
        self.assertIsNone(daily["lagged_close_return_fraction"])


class MalformedSourceTests(unittest.TestCase):
    def test_unfinalized_row_raises_through_the_reused_guard(self):
        packet = copy.deepcopy(market_evidence_packet())
        packet["candles"]["1d"]["finalized_candles"][-1]["close_time"] = "2026-09-01T00:00:00Z"
        with self.assertRaises(PDError):
            build(packet=TRENDTEST.resign_packet(packet))

    def test_out_of_order_rows_raise(self):
        packet = copy.deepcopy(market_evidence_packet())
        rows = packet["candles"]["1d"]["finalized_candles"]
        rows[0], rows[1] = rows[1], rows[0]
        with self.assertRaises(PDError):
            build(packet=TRENDTEST.resign_packet(packet))

    def test_non_positive_close_raises(self):
        packet = copy.deepcopy(market_evidence_packet())
        packet["candles"]["1d"]["finalized_candles"][-1]["trade_price"] = "0"
        with self.assertRaises(PDError):
            build(packet=TRENDTEST.resign_packet(packet))

    def test_tampered_packet_hash_raises(self):
        packet = copy.deepcopy(market_evidence_packet())
        packet["candles"]["1d"]["finalized_candles"][-1]["trade_price"] = "999"
        with self.assertRaises(PDError):
            build(packet=packet)

    def test_missing_or_wrong_shaped_packet_raises(self):
        for value in (None, {}, [], "packet"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(PDError):
                    PD.build_price_distance_metrics(
                        value,
                        market=MARKET,
                        evaluation_as_of=EVAL_AS_OF,
                        calculation_contract=price_distance_contract(),
                    )

    def test_market_and_evaluation_date_are_validated(self):
        with self.assertRaises(PDError):
            build(market="KRW-DOES-NOT-MATCH")
        with self.assertRaises(PDError):
            build(evaluation_as_of="28-08-2026")


# ---------------------------------------------------------------------------
# Independent validation of the ORIGINAL inputs
# ---------------------------------------------------------------------------

class ValidatorOriginalInputTests(unittest.TestCase):
    def setUp(self):
        self.packet = market_evidence_packet()
        self.contract = price_distance_contract()
        self.metrics = build(packet=self.packet, contract=self.contract)

    def test_valid_output_revalidates(self):
        revalidated = validate(self.metrics, self.packet, self.contract)
        self.assertEqual(PD.canonical_json(revalidated), PD.canonical_json(self.metrics))

    def test_every_original_input_is_a_required_argument(self):
        """There is no signature path by which the output could supply its own
        trusted parameters: all four originals are keyword-only and required.
        """
        signature = inspect.signature(PD.validate_price_distance_metrics)
        for name in ("market", "evaluation_as_of", "market_evidence_packet", "calculation_contract"):
            with self.subTest(name=name):
                parameter = signature.parameters[name]
                self.assertIs(parameter.default, inspect.Parameter.empty)
                self.assertEqual(parameter.kind, inspect.Parameter.KEYWORD_ONLY)
        with self.assertRaises(TypeError):
            PD.validate_price_distance_metrics(self.metrics)

    def test_self_rehashed_evaluation_date_substitution_is_rejected(self):
        """The known volume-review defect: a re-signed output whose evaluation
        date was swapped must not pass, because the validator compares against
        the independently supplied original date instead of the output's.
        """
        tampered = copy.deepcopy(self.metrics)
        tampered["evaluation_as_of"] = "2026-08-29"
        tampered = resign(tampered)
        self.assertNotEqual(tampered["payload_sha256"], self.metrics["payload_sha256"])
        with self.assertRaises(PDError):
            validate(tampered, self.packet, self.contract)

    def test_self_rehashed_market_substitution_is_rejected(self):
        tampered = resign({**copy.deepcopy(self.metrics), "market": "KRW-BTC"})
        with self.assertRaises(PDError):
            validate(tampered, self.packet, self.contract)

    def test_self_rehashed_return_lag_substitution_is_rejected(self):
        """Even with the embedded contract digest recomputed, the lag swap
        fails against the independently supplied original contract.
        """
        tampered = copy.deepcopy(self.metrics)
        tampered["calculation_contract"]["timeframes"]["1d"]["return_lag_candles"] = 2
        tampered = resign_contract_and_payload(tampered)
        self.assertEqual(
            PD.payload_sha256(tampered["calculation_contract"]),
            tampered["calculation_contract_sha256"],
        )
        with self.assertRaises(PDError):
            validate(tampered, self.packet, self.contract)

    def test_self_rehashed_fraction_edit_is_rejected(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["timeframes"]["1d"]["close_to_ema_fraction"] = "0.99000000"
        with self.assertRaises(PDError):
            validate(resign(tampered), self.packet, self.contract)

    def test_self_rehashed_status_or_reason_edit_is_rejected(self):
        unavailable = build(
            packet=self.packet,
            contract=price_distance_contract(daily_return_lag=5),
        )
        tampered = copy.deepcopy(unavailable)
        tampered["status"] = PD.STATUS_CALCULATED
        tampered["unavailable_reasons"] = []
        with self.assertRaises(PDError):
            validate(
                resign(tampered), self.packet, price_distance_contract(daily_return_lag=5),
            )

    def test_self_rehashed_denominator_status_edit_is_rejected(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["timeframes"]["4h"]["close_to_ema_denominator_status"] = (
            PD.DENOMINATOR_ZERO_LATEST_EMA
        )
        with self.assertRaises(PDError):
            validate(resign(tampered), self.packet, self.contract)

    def test_substituted_source_packet_is_rejected(self):
        other = market_evidence_packet(daily_closes=("80", "100", "120"))
        with self.assertRaises(PDError):
            validate(self.metrics, other, self.contract)

    def test_substituted_original_contract_is_rejected(self):
        with self.assertRaises(PDError):
            validate(self.metrics, self.packet, price_distance_contract(daily_return_lag=2))

    def test_tampered_embedded_trend_metrics_is_rejected(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["trend_metrics"]["timeframes"]["1d"]["latest_ema"] = "1.0000"
        tampered["trend_metrics"] = TRENDTEST.resign_metrics(tampered["trend_metrics"])
        tampered["trend_metrics_sha256"] = tampered["trend_metrics"]["payload_sha256"]
        with self.assertRaises(PDError):
            validate(resign(tampered), self.packet, self.contract)

    def test_payload_hash_and_schema_guards(self):
        broken = copy.deepcopy(self.metrics)
        broken["payload_sha256"] = "0" * 64
        with self.assertRaises(PDError):
            validate(broken, self.packet, self.contract)
        missing = copy.deepcopy(self.metrics)
        del missing["timeframes"]
        with self.assertRaises(PDError):
            validate(missing, self.packet, self.contract)
        for value in (None, [], "metrics"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(PDError):
                    validate(value, self.packet, self.contract)

    def test_authority_tampering_is_rejected(self):
        tampered = copy.deepcopy(self.metrics)
        tampered["authority"]["trading_authorized"] = True
        with self.assertRaises(PDError):
            validate(resign(tampered), self.packet, self.contract)


# ---------------------------------------------------------------------------
# Determinism, authority and the unchanged P5-08 boundary
# ---------------------------------------------------------------------------

class DeterminismAndBoundaryTests(unittest.TestCase):
    def test_repeated_builds_are_byte_identical(self):
        packet = market_evidence_packet()
        contract = price_distance_contract()
        first = build(packet=packet, contract=contract)
        second = build(packet=packet, contract=contract)
        self.assertEqual(PD.canonical_json(first), PD.canonical_json(second))

    def test_inputs_are_not_mutated(self):
        packet = market_evidence_packet()
        contract = price_distance_contract()
        before_packet = PD.canonical_json(packet)
        before_contract = PD.canonical_json(contract)
        build(packet=packet, contract=contract)
        self.assertEqual(PD.canonical_json(packet), before_packet)
        self.assertEqual(PD.canonical_json(contract), before_contract)

    def test_authority_block_is_calculation_only(self):
        authority = build()["authority"]
        self.assertIs(authority["calculation_only"], True)
        for key, value in sorted(authority.items()):
            if key != "calculation_only":
                with self.subTest(key=key):
                    self.assertIs(value, False)
        self.assertEqual(set(authority), set(TREND._AUTHORITY))

    def test_status_domain_never_carries_a_verdict(self):
        self.assertEqual(set(PD.STATUSES), {"CALCULATED", "UNAVAILABLE"})
        for metrics in (build(), build(packet=market_evidence_packet(daily_gap=True))):
            self.assertIn(metrics["status"], PD.STATUSES)

    def test_module_defines_no_threshold_or_verdict_vocabulary(self):
        """The module docstring legitimately explains what the capability is
        *not*, so it is excluded; everything after it -- the executable body
        and its own docstrings -- must contain no threshold or verdict
        vocabulary and no percentage scaling. A consumer cannot extract a bound
        from a file that never names one.
        """
        source = (ROOT / "universe/crypto_candidate_price_distance_metrics.py").read_text(
            encoding="utf-8",
        )
        body = source.split('"""', 2)[-1]
        forbidden = (
            # no threshold/verdict constant, and no wiring into the criterion
            "THRESHOLD", "OVEREXTEN", "evaluate_overextension",
            "FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE", "CANDIDATE_PROMOTED",
            # no percentage scaling anywhere -- the outputs are fractions
            "* 100", "*100", "100 *", "Decimal(100)", 'Decimal("100")',
        )
        for token in forbidden:
            with self.subTest(token=token):
                self.assertNotIn(token, body)

    def test_evaluate_overextension_remains_unknown(self):
        """The P5-08 criterion this observation serves is deliberately not
        resolved, wired or changed by this module.
        """
        criterion = PROMO.evaluate_overextension()
        self.assertEqual(criterion["status"], "UNKNOWN")
        self.assertEqual(criterion["reason"], "NO_RATIFIED_OVEREXTENSION_THRESHOLD")

    def test_promotion_module_does_not_import_this_capability(self):
        source = (ROOT / "universe/crypto_candidate_promotion.py").read_text(encoding="utf-8")
        self.assertNotIn("price_distance", source)

    def test_source_lineage_is_bound(self):
        metrics = build()
        self.assertEqual(set(metrics["source"]), PD._SOURCE_KEYS)
        self.assertEqual(metrics["source"]["market"], MARKET)
        self.assertEqual(
            metrics["trend_metrics_sha256"], metrics["trend_metrics"]["payload_sha256"],
        )
        self.assertEqual(
            metrics["source"]["payload_sha256"],
            metrics["trend_metrics"]["source"]["payload_sha256"],
        )

    def test_output_key_set_is_exact(self):
        self.assertEqual(set(build()), PD._OUTPUT_KEYS)
        for block in build()["timeframes"].values():
            self.assertEqual(set(block), PD._TIMEFRAME_OUTPUT_KEYS)
            self.assertIn(block["close_to_ema_denominator_status"], PD.DENOMINATOR_STATUSES)
            self.assertIn(
                block["lagged_close_return_denominator_status"], PD.DENOMINATOR_STATUSES,
            )


if __name__ == "__main__":
    unittest.main()
