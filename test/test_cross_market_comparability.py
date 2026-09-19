"""Cross-market comparability at one common decision timestamp.

Read-only, offline, fixture-only.  No network, no clock, no git mutation, no
authority.  The tests pin the properties that make the label reachable without
ever coercing a date:

* ``T`` is caller-supplied and never inferred.
* Each market keeps its own ``as_of_date`` and ``available_at`` in the output.
* COMPLETE is gated on each market's own freshness state, not on date identity.
* One unusable market removes that market only, with explicit reason codes.
* Two-market and three-market results carry different labels.
"""

import datetime as dt
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from regime import cross_market_comparability as subject

ADOPTION = json.loads(
    (ROOT / "config" / "paper_regime_runtime_adoption_v1.json").read_text(
        encoding="utf-8"
    )
)

T = "2026-09-19T07:00:00Z"


def row(
    market,
    decision_date,
    available_at,
    *,
    eligibility="CURRENT",
    candidate="NEUTRAL",
    assessments=True,
    price_date=None,
):
    return {
        "market": market,
        "decision_date": decision_date,
        "price_date": price_date,
        "available_at": available_at,
        "market_eligibility": eligibility,
        "candidate_regime": candidate,
        "required_assessments_complete": assessments,
    }


def three_different_dates():
    """The ordinary shape: US, KR and Crypto on three different sessions."""
    return [
        row("US", "2026-09-18", "2026-09-18T23:32:57Z",
            eligibility="CURRENT_AS_FETCHED_NOT_PIT"),
        row("KR", "2026-09-17", "2026-09-18T13:39:39Z"),
        row("CRYPTO", "2026-09-19", "2026-09-19T06:14:29Z"),
    ]


class EvaluationClockTest(unittest.TestCase):
    def test_missing_evaluation_at_is_an_error_never_a_default(self):
        with self.assertRaises(subject.CrossMarketComparabilityError) as ctx:
            subject.compare_markets_at(three_different_dates(), evaluation_at=None)
        self.assertIn("EVALUATION_AT_REQUIRED_NEVER_INFERRED", str(ctx.exception))

    def test_malformed_evaluation_at_is_rejected(self):
        for bad in ("2026-09-19", "2026-09-19 07:00:00Z", "", 0, {}):
            with self.assertRaises(subject.CrossMarketComparabilityError):
                subject.compare_markets_at(
                    three_different_dates(), evaluation_at=bad
                )

    def test_evaluation_at_is_retained_verbatim_in_utc(self):
        result = subject.compare_markets_at(
            three_different_dates(), evaluation_at="2026-09-19T16:00:00+09:00"
        )
        self.assertEqual(result["evaluation_at"], "2026-09-19T07:00:00Z")


class NoDateCoercionTest(unittest.TestCase):
    def test_adopted_record_still_forbids_date_coercion(self):
        caveats = ADOPTION["comparability_and_caveats"]
        self.assertFalse(caveats["cross_market_date_coercion_authorized"])
        self.assertFalse(caveats["price_date_substitution_authorized"])

    def test_each_market_keeps_its_own_as_of_date(self):
        result = subject.compare_markets_at(three_different_dates(), evaluation_at=T)
        self.assertEqual(
            result["as_of_date_by_market"],
            {"US": "2026-09-18", "KR": "2026-09-17", "CRYPTO": "2026-09-19"},
        )
        self.assertEqual(
            result["distinct_as_of_dates"],
            ["2026-09-17", "2026-09-18", "2026-09-19"],
        )
        self.assertEqual(result["as_of_date_spread_days"], 2)
        self.assertFalse(result["same_decision_date"])
        self.assertFalse(result["date_coercion_used"])
        self.assertFalse(result["price_date_substitution_used"])
        self.assertFalse(result["cross_market_date_coercion_authorized"])

    def test_price_date_is_never_promoted_to_the_decision_date(self):
        rows = three_different_dates()
        rows[2]["price_date"] = "2026-09-18"
        result = subject.compare_markets_at(rows, evaluation_at=T)
        crypto = result["markets"][2]
        self.assertEqual(crypto["as_of_date"], "2026-09-19")
        self.assertEqual(crypto["price_date"], "2026-09-18")


class CompleteWithoutDateIdentityTest(unittest.TestCase):
    def test_three_different_dates_still_reach_three_market_complete(self):
        result = subject.compare_markets_at(three_different_dates(), evaluation_at=T)
        self.assertEqual(result["comparison_status"], "COMPLETE_THREE_MARKET")
        self.assertEqual(result["comparison_scope"], "THREE_MARKET")
        self.assertEqual(result["three_market_comparison_status"], "COMPLETE")
        self.assertTrue(result["complete"])
        self.assertEqual(result["comparable_markets"], ["US", "KR", "CRYPTO"])
        self.assertEqual(result["excluded_markets"], {})

    def test_identical_dates_are_not_required_but_are_still_accepted(self):
        rows = [
            row("US", "2026-09-18", "2026-09-18T23:32:57Z",
                eligibility="CURRENT_AS_FETCHED_NOT_PIT"),
            row("KR", "2026-09-18", "2026-09-18T13:39:39Z"),
            row("CRYPTO", "2026-09-18", "2026-09-19T00:14:29Z"),
        ]
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertTrue(result["complete"])
        self.assertTrue(result["same_decision_date"])
        self.assertEqual(result["as_of_date_spread_days"], 0)


class LagIsRecordedTest(unittest.TestCase):
    def test_per_market_lag_from_the_common_timestamp(self):
        result = subject.compare_markets_at(three_different_dates(), evaluation_at=T)
        by_market = {entry["market"]: entry for entry in result["markets"]}
        self.assertEqual(by_market["US"]["available_at"], "2026-09-18T23:32:57Z")
        self.assertEqual(by_market["US"]["lag_seconds"], 7 * 3600 + 27 * 60 + 3)
        self.assertEqual(by_market["KR"]["lag_hours"], 17.339)
        self.assertEqual(by_market["CRYPTO"]["lag_seconds"], 45 * 60 + 31)
        self.assertEqual(by_market["US"]["as_of_lag_calendar_days"], 1)
        self.assertEqual(by_market["KR"]["as_of_lag_calendar_days"], 2)
        self.assertEqual(by_market["CRYPTO"]["as_of_lag_calendar_days"], 0)
        self.assertEqual(
            sorted(result["lag_hours_by_market"]), ["CRYPTO", "KR", "US"]
        )

    def test_a_session_available_only_after_T_is_not_pulled_backwards(self):
        rows = three_different_dates()
        rows[2]["available_at"] = "2026-09-19T08:00:00Z"
        result = subject.compare_markets_at(rows, evaluation_at=T)
        crypto = result["markets"][2]
        self.assertFalse(crypto["comparable"])
        self.assertIn(
            "SESSION_NOT_AVAILABLE_AT_DECISION_TIMESTAMP",
            crypto["exclusion_reason_codes"],
        )
        self.assertEqual(crypto["as_of_date"], "2026-09-19")
        self.assertEqual(result["comparison_status"], "COMPLETE_TWO_MARKET")

    def test_a_decision_date_after_T_is_excluded_not_clamped(self):
        rows = three_different_dates()
        rows[2] = row("CRYPTO", "2026-09-20", "2026-09-19T06:00:00Z")
        result = subject.compare_markets_at(rows, evaluation_at=T)
        crypto = result["markets"][2]
        self.assertEqual(crypto["as_of_date"], "2026-09-20")
        self.assertIn(
            "DECISION_DATE_AFTER_DECISION_TIMESTAMP",
            crypto["exclusion_reason_codes"],
        )


class FreshnessBudgetSourceTest(unittest.TestCase):
    def test_budget_states_come_from_the_adopted_record_not_from_new_numbers(self):
        self.assertEqual(
            set(subject.WITHIN_BUDGET_ELIGIBILITY_STATES),
            {
                ADOPTION["eligibility_states"][
                    "terminal_success_new_expected_source"
                ],
                ADOPTION["eligibility_states"][
                    "terminal_success_current_fetch_with_slower_source_frequency"
                ],
            },
        )
        self.assertIsNone(subject.FRESHNESS_BUDGET["numeric_lag_budget"])
        self.assertEqual(
            subject.FRESHNESS_BUDGET["numeric_lag_budget_status"],
            "NOT_DEFINED:PER_MARKET_NUMERIC_LAG_BUDGET",
        )

    def test_per_market_mode_name_matches_the_ratified_bridge_constant(self):
        bridge = (ROOT / "shadow" / "crypto_paper_runtime_bridge.py").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'PER_MARKET_FRESHNESS_MODE = "PER_MARKET_RATIFIED"', bridge
        )
        self.assertEqual(subject.COMPARABILITY_MODE, "PER_MARKET_RATIFIED")

    def test_every_non_current_eligibility_state_excludes_its_own_market(self):
        excluded_states = [
            value
            for key, value in ADOPTION["eligibility_states"].items()
            if value not in subject.WITHIN_BUDGET_ELIGIBILITY_STATES
        ]
        self.assertTrue(excluded_states)
        for state in excluded_states:
            rows = three_different_dates()
            rows[1]["market_eligibility"] = state
            result = subject.compare_markets_at(rows, evaluation_at=T)
            self.assertEqual(
                result["comparison_status"], "COMPLETE_TWO_MARKET", state
            )
            self.assertFalse(result["markets"][1]["freshness_within_budget"], state)
            self.assertIn(
                f"MARKET_ELIGIBILITY_OUTSIDE_FRESHNESS_BUDGET:{state}",
                result["markets"][1]["exclusion_reason_codes"],
            )
            self.assertEqual(result["comparable_markets"], ["US", "CRYPTO"])


class PerMarketExclusionTest(unittest.TestCase):
    def test_unknown_crypto_removes_crypto_only(self):
        rows = three_different_dates()
        rows[2] = row(
            "CRYPTO",
            "2026-09-19",
            "2026-09-19T06:14:29Z",
            eligibility="SOURCE_INVALID",
            candidate="UNKNOWN",
        )
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(result["comparison_status"], "COMPLETE_TWO_MARKET")
        self.assertEqual(result["comparison_scope"], "TWO_MARKET")
        self.assertEqual(result["comparable_markets"], ["US", "KR"])
        self.assertEqual(
            result["excluded_markets"]["CRYPTO"],
            [
                "MARKET_ELIGIBILITY_OUTSIDE_FRESHNESS_BUDGET:SOURCE_INVALID",
                "CANDIDATE_REGIME_NOT_CLASSIFIED:UNKNOWN",
            ],
        )
        # The excluded market's own facts stay visible.
        self.assertEqual(result["as_of_date_by_market"]["CRYPTO"], "2026-09-19")

    def test_two_market_result_is_never_labelled_three_market(self):
        rows = three_different_dates()
        rows[2]["candidate_regime"] = "UNKNOWN"
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(result["comparison_scope"], "TWO_MARKET")
        self.assertNotEqual(result["comparison_status"], "COMPLETE_THREE_MARKET")
        self.assertEqual(
            result["three_market_comparison_status"], "PARTIAL_OR_NON_COMPARABLE"
        )
        self.assertFalse(result["complete"])
        self.assertEqual(result["comparable_market_count"], 2)

    def test_one_and_zero_market_days_are_labelled_not_comparable(self):
        rows = three_different_dates()
        rows[1]["candidate_regime"] = "UNKNOWN"
        rows[2]["candidate_regime"] = "UNKNOWN"
        one = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(one["comparison_status"], "NOT_COMPARABLE_SINGLE_MARKET")
        rows[0]["candidate_regime"] = "UNKNOWN"
        none = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(none["comparison_status"], "NOT_COMPARABLE_NO_MARKET")
        self.assertEqual(none["comparable_markets"], [])

    def test_missing_market_row_is_that_market_only(self):
        rows = three_different_dates()[:2]
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(result["comparison_status"], "COMPLETE_TWO_MARKET")
        self.assertEqual(
            result["excluded_markets"]["CRYPTO"], ["MARKET_ROW_ABSENT"]
        )
        self.assertIsNone(result["as_of_date_by_market"]["CRYPTO"])

    def test_duplicate_market_rows_disqualify_that_market(self):
        rows = three_different_dates()
        rows.append(row("KR", "2026-09-16", "2026-09-17T13:39:39Z"))
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertIn(
            "MARKET_ROW_DUPLICATED", result["excluded_markets"]["KR"]
        )
        self.assertEqual(result["comparable_markets"], ["US", "CRYPTO"])

    def test_incomplete_required_assessments_exclude_one_market(self):
        rows = three_different_dates()
        rows[0]["required_assessments_complete"] = False
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(
            result["excluded_markets"]["US"], ["REQUIRED_ASSESSMENTS_INCOMPLETE"]
        )
        self.assertEqual(result["comparable_markets"], ["KR", "CRYPTO"])

    def test_foreign_market_names_are_recorded_and_ignored(self):
        rows = three_different_dates()
        rows.append(row("JP", "2026-09-18", "2026-09-18T09:00:00Z"))
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertTrue(result["complete"])
        self.assertEqual(
            result["foreign_rows"],
            [{
                "market": "JP",
                "exclusion_reason_codes": ["MARKET_NOT_IN_COMPARISON_SET"],
            }],
        )


class SessionSelectionTest(unittest.TestCase):
    def test_latest_session_available_at_or_before_T_is_selected(self):
        candidates = [
            {"as_of_date": "2026-09-16", "available_at": "2026-09-17T13:39:39Z"},
            {"as_of_date": "2026-09-17", "available_at": "2026-09-18T13:39:39Z"},
            {"as_of_date": "2026-09-18", "available_at": "2026-09-19T13:39:39Z"},
        ]
        info = subject.select_market_session(candidates, evaluation_at=T)
        self.assertEqual(info["selected"]["as_of_date"], "2026-09-17")
        self.assertEqual(info["usable_count"], 2)
        self.assertEqual(
            info["rejected_not_yet_available"],
            [{
                "as_of_date": "2026-09-18",
                "available_at": "2026-09-19T13:39:39Z",
                "reason_codes": ["SESSION_NOT_AVAILABLE_AT_DECISION_TIMESTAMP"],
            }],
        )

    def test_no_usable_session_selects_nothing_rather_than_the_newest(self):
        candidates = [
            {"as_of_date": "2026-09-19", "available_at": "2026-09-19T23:00:00Z"},
        ]
        info = subject.select_market_session(candidates, evaluation_at=T)
        self.assertIsNone(info["selected"])
        self.assertEqual(info["usable_count"], 0)

    def test_selection_also_requires_the_caller_supplied_clock(self):
        with self.assertRaises(subject.CrossMarketComparabilityError):
            subject.select_market_session([], evaluation_at=None)


class AuthorityTest(unittest.TestCase):
    FLAGS = (
        "real_trading",
        "capital_authorized",
        "order_authorized",
        "buy_authorized",
        "trading_authorized",
        "position_size_authorized",
        "target_weight_authorized",
        "exchange_order_authorized",
        "real_capital_authorized",
    )

    def test_module_neither_reads_nor_writes_any_authority_flag(self):
        source = (ROOT / "regime" / "cross_market_comparability.py").read_text(
            encoding="utf-8"
        )
        for flag in self.FLAGS:
            self.assertNotIn(flag, source, flag)

    def test_result_carries_no_authority_keys(self):
        result = subject.compare_markets_at(three_different_dates(), evaluation_at=T)
        serialized = json.dumps(result)
        for flag in self.FLAGS:
            self.assertNotIn(flag, serialized, flag)
        self.assertNotIn("authority", result)

    def test_adopted_record_authority_is_still_fail_closed(self):
        authority = ADOPTION["authority"]
        for flag in self.FLAGS:
            if flag in authority:
                self.assertFalse(authority[flag], flag)
        self.assertFalse(authority["runtime_production_regime_authorized"])


class PurityTest(unittest.TestCase):
    def test_result_is_json_serializable_and_stable(self):
        rows = three_different_dates()
        first = subject.compare_markets_at(rows, evaluation_at=T)
        second = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(
            json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True)
        )

    def test_input_rows_are_not_mutated(self):
        rows = three_different_dates()
        snapshot = json.dumps(rows, sort_keys=True)
        subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(json.dumps(rows, sort_keys=True), snapshot)

    def test_module_opens_no_file_socket_or_clock(self):
        source = (ROOT / "regime" / "cross_market_comparability.py").read_text(
            encoding="utf-8"
        )
        for forbidden in (
            "open(",
            "Path(",
            "import requests",
            "import urllib",
            "import socket",
            "import subprocess",
            "socket.",
            "utcnow",
            "dt.datetime.now",
            "time.time",
        ):
            self.assertNotIn(forbidden, source, forbidden)

    def test_schema_version_is_pinned(self):
        self.assertEqual(
            subject.SCHEMA_VERSION, "cross_market_comparability/1"
        )
        self.assertEqual(
            subject.COMPARISON_BASIS,
            "COMMON_DECISION_TIMESTAMP_PER_MARKET_LATEST_COMPLETED_SESSION",
        )

    def test_utc_conversion_does_not_shift_a_recorded_date(self):
        rows = [
            row("US", "2026-09-18", "2026-09-19T08:32:57+09:00",
                eligibility="CURRENT_AS_FETCHED_NOT_PIT"),
            row("KR", "2026-09-17", "2026-09-18T22:39:39+09:00"),
            row("CRYPTO", "2026-09-19", "2026-09-19T06:14:29Z"),
        ]
        result = subject.compare_markets_at(rows, evaluation_at=T)
        self.assertEqual(result["as_of_date_by_market"]["US"], "2026-09-18")
        self.assertEqual(
            result["markets"][0]["available_at"], "2026-09-19T08:32:57+09:00"
        )
        self.assertEqual(
            result["markets"][0]["lag_seconds"],
            int(
                (
                    dt.datetime(2026, 9, 19, 7, tzinfo=dt.timezone.utc)
                    - dt.datetime(
                        2026, 9, 18, 23, 32, 57, tzinfo=dt.timezone.utc
                    )
                ).total_seconds()
            ),
        )


if __name__ == "__main__":
    unittest.main()
