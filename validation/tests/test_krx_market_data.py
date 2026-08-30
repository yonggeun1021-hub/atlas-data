#!/usr/bin/env python3
"""KRX completed market-data/session/freshness contract regression."""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "market_data" / "krx_session_bars.py"
SPEC = importlib.util.spec_from_file_location("krx_session_bars", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

FIXTURE_ROOT = ROOT / "test" / "fixtures" / "krx_market_data"
CALENDARS = json.loads((FIXTURE_ROOT / "calendar_cases.json").read_text(encoding="utf-8"))
ACTIONS = json.loads((FIXTURE_ROOT / "corporate_action_cases.json").read_text(encoding="utf-8"))
CONTRACT = MODULE.load_contract()
CONSUMER = json.loads((ROOT / "config" / "krx_market_data_consumer_contract.json").read_text(encoding="utf-8"))
KST = ZoneInfo("Asia/Seoul")


def instant(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value)


def iso(value: dt.datetime) -> str:
    return value.isoformat(timespec="seconds")


def raw_adjustment() -> dict:
    return {
        "status": "NONE", "factor": None, "action_refs": [],
        "snapshot_ref": None, "snapshot_sha256": None, "available_at": None,
    }


def freshness_policy() -> dict:
    value = {
        "schema_version": "intraday_freshness_policy/1",
        "policy_id": "KRX.MARKET.DATA.TEST.FIXTURE.V1",
        "approval_status": "RATIFIED",
        "ratified_by": "TEST FIXTURE ONLY - NOT OPERATIONAL AUTHORITY",
        "ratified_at_utc": "2025-12-31T00:00:00Z",
        "effective_from_utc": "2026-01-01T00:00:00Z",
        "effective_to_utc": "2027-01-01T00:00:00Z",
        "input_contract_version": "intraday_freshness_guard/1",
        "max_provider_age_seconds_by_market": {
            "US": 120, "KOREA": 120, "CRYPTO": 120,
        },
        "max_transport_delay_seconds_by_market": {
            "US": 8, "KOREA": 8, "CRYPTO": 8,
        },
    }
    value["packet_sha256"] = MODULE.P9_FRESHNESS.payload_sha256(value)
    return value


FRESHNESS_POLICY = freshness_policy()


def bar(
    opened: dt.datetime,
    closed: dt.datetime,
    *,
    endpoint: str = "FHKST03010230",
    capture_kind: str = "ORIGINAL",
    adjustment: dict | None = None,
    observed_delay: int = 1,
    transport_delay: int = 1,
    generation_delay: int = 1,
) -> dict:
    observed = closed + dt.timedelta(seconds=observed_delay)
    available = observed + dt.timedelta(seconds=transport_delay)
    generated = available + dt.timedelta(seconds=generation_delay)
    return {
        "open_at": iso(opened), "close_at": iso(closed),
        "open": "100", "high": "110", "low": "90", "close": "105", "volume": "1000",
        "source": {
            "provider_id": "KIS_OPEN_API", "endpoint_id": endpoint,
            "observed_at": iso(observed), "available_at": iso(available),
            "generated_at": iso(generated),
            "snapshot_ref": f"fixture://kis/{endpoint}/{iso(opened)}",
            "snapshot_sha256": "1" * 64, "capture_kind": capture_kind,
        },
        "adjustment": copy.deepcopy(adjustment if adjustment is not None else raw_adjustment()),
    }


def series(timeframe: str, decision_at: dt.datetime, *, price_basis: str = "RAW") -> dict:
    intervals = MODULE.expected_intervals(
        timeframe, CALENDARS["regular_open"], decision_at, CONTRACT
    )
    endpoint = "FHKST03010100" if timeframe == "1d" else "FHKST03010230"
    rows = [bar(a, b, endpoint=endpoint) for a, b in intervals]
    for row in rows:
        row["source"]["observed_at"] = iso(decision_at - dt.timedelta(seconds=3))
        row["source"]["available_at"] = iso(decision_at - dt.timedelta(seconds=2))
        row["source"]["generated_at"] = iso(decision_at - dt.timedelta(seconds=1))
    return {
        "asset_id": "KR:XKRX:005930", "timeframe": timeframe,
        "price_basis": price_basis,
        "bars": rows,
    }


def market_state(decision_at: dt.datetime) -> dict:
    as_of = decision_at - dt.timedelta(seconds=10)
    return {
        "asset_id": "KR:XKRX:005930", "as_of": iso(as_of),
        "available_at": iso(decision_at - dt.timedelta(seconds=5)),
        "source_ref": "fixture://kis/inquire-price/005930",
        "source_sha256": "2" * 64, "provider_id": "KIS_OPEN_API",
        "price_limits": {"status": "KNOWN", "base_price": "100", "lower_price": "70", "upper_price": "130"},
        "tick_size": {"status": "KNOWN", "krw": "1"},
        "volatility_interruption": {"status": "INACTIVE"},
        "trading_halt": {"status": "TRADING"},
        "market_circuit_breaker": {"status": "INACTIVE"},
    }


class SessionBoundaryTests(unittest.TestCase):
    def test_consumer_pins_merged_gate_and_does_not_pin_other_lane_work(self):
        self.assertEqual(
            CONSUMER["krx_gate_pin"]["merged_main_sha"],
            "016a2889c503066a3a07180e8d12b9da81869e7b",
        )
        self.assertEqual(CONSUMER["krx_gate_pin"]["current_state_at_pin"], "LOCKED")
        self.assertEqual(
            CONSUMER["external_consumers"]["universe"]["status"],
            "EXACT_HASH_REQUIRED_NOT_PINNED_BY_THIS_LANE",
        )
        self.assertTrue(CONSUMER["separation"]["ci_success_is_not_gate_pass"])
        self.assertTrue(all(value is False for value in CONSUMER["authority"].values()))

    def test_contract_is_kst_as_of_without_dst_and_all_authority_closed(self):
        self.assertEqual(CONTRACT["timezone"], "Asia/Seoul")
        self.assertFalse(CONTRACT["dst_observed_as_of"])
        self.assertEqual(CONTRACT["freshness"]["repository_default_policy"], "ABSENT")
        self.assertEqual(
            CONTRACT["freshness"]["policy_requirement"],
            "EXTERNAL_RATIFIED_POLICY_REQUIRED",
        )
        for month in (1, 8):
            point = dt.datetime(2026, month, 15, 12, tzinfo=KST)
            self.assertEqual(point.utcoffset(), dt.timedelta(hours=9))
        self.assertTrue(CONTRACT["authority"]["market_data_observation_only"])
        self.assertTrue(all(
            value is False for key, value in CONTRACT["authority"].items()
            if key != "market_data_observation_only"
        ))

    def test_regular_session_has_26_fifteen_minute_and_6_full_hour_bars(self):
        decision = instant("2026-08-28T15:31:00+09:00")
        fifteen = MODULE.expected_intervals("15m", CALENDARS["regular_open"], decision, CONTRACT)
        hourly = MODULE.expected_intervals("1h", CALENDARS["regular_open"], decision, CONTRACT)
        daily = MODULE.expected_intervals("1d", CALENDARS["regular_open"], decision, CONTRACT)
        self.assertEqual(len(fifteen), 26)
        self.assertEqual((iso(fifteen[0][0]), iso(fifteen[-1][1])), (
            "2026-08-28T09:00:00+09:00", "2026-08-28T15:30:00+09:00"
        ))
        self.assertEqual(len(hourly), 6)
        self.assertEqual(iso(hourly[-1][1]), "2026-08-28T15:00:00+09:00")
        self.assertEqual(len(daily), 1)

    def test_current_partial_interval_and_daily_bar_are_not_completed(self):
        decision = instant("2026-08-28T09:20:00+09:00")
        self.assertEqual(len(MODULE.expected_intervals("15m", CALENDARS["regular_open"], decision, CONTRACT)), 1)
        self.assertEqual(MODULE.expected_intervals("1h", CALENDARS["regular_open"], decision, CONTRACT), [])
        self.assertEqual(MODULE.expected_intervals("1d", CALENDARS["regular_open"], decision, CONTRACT), [])

    def test_four_hour_boundary_is_explicitly_unratified(self):
        with self.assertRaisesRegex(MODULE.KrxMarketDataError, "FOUR_HOUR"):
            MODULE.expected_intervals(
                "4h", CALENDARS["regular_open"],
                instant("2026-08-28T15:31:00+09:00"), CONTRACT,
            )

    def test_weekend_holiday_and_unknown_special_session_never_create_bars(self):
        cases = (
            ("weekend_closed", "2026-08-30T15:31:00+09:00"),
            ("holiday_closed", "2026-05-05T15:31:00+09:00"),
            ("special_session_unknown", "2026-08-31T15:31:00+09:00"),
        )
        for name, decision in cases:
            with self.subTest(name=name):
                self.assertEqual(
                    MODULE.expected_intervals("15m", CALENDARS[name], instant(decision), CONTRACT), []
                )


class SeriesGateTests(unittest.TestCase):
    def test_missing_or_unratified_p9_policy_fails_closed(self):
        decision = instant("2026-08-28T10:01:00+09:00")
        value = series("15m", decision)
        draft = copy.deepcopy(FRESHNESS_POLICY)
        draft["approval_status"] = "DRAFT"
        draft["packet_sha256"] = MODULE.P9_FRESHNESS.payload_sha256(
            {key: item for key, item in draft.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.KrxMarketDataError, "P9_FRESHNESS_POLICY"):
            MODULE.assess_series(
                value, CALENDARS["regular_open"], decision, draft, CONTRACT
            )

    def test_normalized_minutes_create_only_complete_bars_and_never_fill_gaps(self):
        decision = instant("2026-08-28T10:00:05+09:00")
        minutes = []
        for index in range(60):
            started = instant("2026-08-28T09:00:00+09:00") + dt.timedelta(minutes=index)
            minutes.append({
                "interval_start": iso(started), "open": str(100 + index),
                "high": str(101 + index), "low": str(99 + index),
                "close": str(100 + index), "volume": "1",
            })
        source = bar(
            instant("2026-08-28T09:00:00+09:00"),
            instant("2026-08-28T10:00:00+09:00"),
        )["source"]
        normalized = {
            "asset_id": "KR:XKRX:005930", "price_basis": "RAW",
            "timestamp_semantics": "INTERVAL_START_RATIFIED",
            "minutes": minutes, "source": source,
        }
        fifteen = MODULE.aggregate_normalized_minutes(
            normalized, "15m", CALENDARS["regular_open"], decision, CONTRACT
        )
        hourly = MODULE.aggregate_normalized_minutes(
            normalized, "1h", CALENDARS["regular_open"], decision, CONTRACT
        )
        self.assertEqual(len(fifteen["bars"]), 4)
        self.assertEqual(len(hourly["bars"]), 1)
        self.assertEqual(hourly["bars"][0]["volume"], "60")
        missing = copy.deepcopy(normalized)
        missing["minutes"].pop(7)
        missing_fifteen = MODULE.aggregate_normalized_minutes(
            missing, "15m", CALENDARS["regular_open"], decision, CONTRACT
        )
        self.assertEqual(len(missing_fifteen["bars"]), 3)
        result = MODULE.assess_series(
            missing_fifteen, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any(reason.startswith("GAP:") for reason in result["reasons"]))

    def test_raw_kis_minute_timestamp_semantics_must_be_ratified(self):
        decision = instant("2026-08-28T09:15:05+09:00")
        value = {
            "asset_id": "KR:XKRX:005930", "price_basis": "RAW",
            "timestamp_semantics": "UNKNOWN_KIS_LABEL",
            "minutes": [],
            "source": bar(
                instant("2026-08-28T09:00:00+09:00"),
                instant("2026-08-28T09:15:00+09:00"),
            )["source"],
        }
        with self.assertRaisesRegex(MODULE.KrxMarketDataError, "TIMESTAMP_SEMANTICS_UNKNOWN"):
            MODULE.aggregate_normalized_minutes(
                value, "15m", CALENDARS["regular_open"], decision, CONTRACT
            )

    def test_complete_regular_series_passes_deterministically(self):
        decision = instant("2026-08-28T15:31:00+09:00")
        value = series("15m", decision)
        first = MODULE.assess_series(
            value, CALENDARS["regular_open"], decision, FRESHNESS_POLICY, CONTRACT
        )
        second = MODULE.assess_series(
            copy.deepcopy(value), CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["accepted_bar_count"], 26)
        self.assertEqual(
            first["p9_policy_lineage"]["policy_sha256"],
            FRESHNESS_POLICY["packet_sha256"],
        )

    def test_gap_exact_duplicate_and_conflicting_duplicate(self):
        decision = instant("2026-08-28T10:01:00+09:00")
        value = series("15m", decision)
        missing = copy.deepcopy(value)
        missing["bars"].pop(1)
        self.assertEqual(
            MODULE.assess_series(
                missing, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )["status"],
            "BLOCKED",
        )
        duplicate = copy.deepcopy(value)
        duplicate["bars"].append(copy.deepcopy(duplicate["bars"][0]))
        duplicate_result = MODULE.assess_series(
            duplicate, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(duplicate_result["status"], "PASS")
        self.assertEqual(duplicate_result["exact_duplicate_count"], 1)
        conflict = copy.deepcopy(value)
        changed = copy.deepcopy(conflict["bars"][0])
        changed["close"] = "106"
        conflict["bars"].append(changed)
        result = MODULE.assess_series(
            conflict, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any(reason.startswith("CONFLICTING_DUPLICATE") for reason in result["reasons"]))

    def test_stale_reversed_and_partial_bar_fail_closed(self):
        decision = instant("2026-08-28T10:01:00+09:00")
        stale = series("15m", decision)
        stale["bars"][-1] = bar(
            instant(stale["bars"][-1]["open_at"]), instant(stale["bars"][-1]["close_at"]),
            transport_delay=9,
        )
        stale_result = MODULE.assess_series(
            stale, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertIn("P9_01_TRANSPORT_DELAY_EXCEEDED", stale_result["reasons"])
        reversed_time = series("15m", decision)
        reversed_time["bars"][0]["source"]["available_at"] = reversed_time["bars"][0]["source"]["observed_at"]
        reversed_time["bars"][0]["source"]["observed_at"] = iso(
            instant(reversed_time["bars"][0]["source"]["observed_at"]) + dt.timedelta(seconds=1)
        )
        reversed_result = MODULE.assess_series(
            reversed_time, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertIn("SOURCE_TIME_ORDER_INVALID", reversed_result["reasons"])
        early = instant("2026-08-28T09:20:00+09:00")
        partial = series("15m", early)
        partial["bars"].append(bar(
            instant("2026-08-28T09:15:00+09:00"),
            instant("2026-08-28T09:30:00+09:00"),
        ))
        partial_result = MODULE.assess_series(
            partial, CALENDARS["regular_open"], early,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(partial_result["status"], "BLOCKED")

    def test_backfill_is_visible_only_after_first_available_at(self):
        row = bar(
            instant("2026-08-28T09:15:00+09:00"),
            instant("2026-08-28T09:30:00+09:00"), capture_kind="BACKFILL",
        )
        row["source"]["available_at"] = "2026-08-28T10:00:00+09:00"
        row["source"]["generated_at"] = "2026-08-28T10:00:01+09:00"
        self.assertEqual(
            MODULE.replay_visible_bars([row], instant("2026-08-28T09:59:59+09:00")), []
        )
        self.assertEqual(
            len(MODULE.replay_visible_bars([row], instant("2026-08-28T10:00:00+09:00"))), 1
        )


class CorporateActionReplayTests(unittest.TestCase):
    def test_split_adjustment_passes_only_with_pit_snapshot(self):
        decision = instant("2026-08-28T15:31:00+09:00")
        value = series("1d", decision, price_basis="ADJUSTED")
        value["bars"][0]["adjustment"] = copy.deepcopy(ACTIONS["split_adjusted"])
        result = MODULE.assess_series(
            value, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["price_basis"], "ADJUSTED")

    def test_future_dividend_adjustment_and_intraday_adjusted_mix_fail_closed(self):
        decision = instant("2026-08-28T15:31:00+09:00")
        value = series("1d", decision, price_basis="ADJUSTED")
        value["bars"][0]["adjustment"] = copy.deepcopy(ACTIONS["dividend_adjusted_future"])
        result = MODULE.assess_series(
            value, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertIn("CORPORATE_ACTION_NOT_POINT_IN_TIME_AVAILABLE", result["reasons"])
        intraday = series("15m", decision, price_basis="ADJUSTED")
        for item in intraday["bars"]:
            item["adjustment"] = copy.deepcopy(ACTIONS["split_adjusted"])
        intraday_result = MODULE.assess_series(
            intraday, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertIn("INTRADAY_ADJUSTED_SERIES_UNSUPPORTED", intraday_result["reasons"])

    def test_raw_dividend_series_preserves_disclosure_without_rewriting_prices(self):
        decision = instant("2026-08-28T15:31:00+09:00")
        value = series("1d", decision)
        value["bars"][0]["adjustment"] = copy.deepcopy(ACTIONS["dividend_raw"])
        result = MODULE.assess_series(
            value, CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["bars"][0]["adjustment"]["status"], "DISCLOSED_NOT_APPLIED")


class MarketStateTests(unittest.TestCase):
    def test_operability_is_separate_from_universe_and_order_authority(self):
        decision = instant("2026-08-28T10:00:00+09:00")
        result = MODULE.assess_market_state(
            market_state(decision), CALENDARS["regular_open"], decision,
            FRESHNESS_POLICY, CONTRACT,
        )
        self.assertEqual(result["market_operability"], "ORDERABLE_OBSERVATION_ONLY")
        self.assertIsNone(result["universe_eligibility"])
        self.assertFalse(CONTRACT["authority"]["kis_mock_order_authorized"])

    def test_vi_is_call_auction_while_halt_is_not_orderable(self):
        decision = instant("2026-08-28T10:00:00+09:00")
        vi = market_state(decision)
        vi["volatility_interruption"]["status"] = "ACTIVE"
        self.assertEqual(
            MODULE.assess_market_state(
                vi, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )["market_operability"],
            "VI_CALL_AUCTION_OBSERVATION_ONLY",
        )
        halt = market_state(decision)
        halt["trading_halt"]["status"] = "HALTED"
        self.assertEqual(
            MODULE.assess_market_state(
                halt, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )["market_operability"],
            "NOT_ORDERABLE_MARKET_STATE",
        )

    def test_unknown_or_stale_market_state_is_unknown(self):
        decision = instant("2026-08-28T10:00:00+09:00")
        unknown = market_state(decision)
        unknown["volatility_interruption"]["status"] = "UNKNOWN"
        self.assertEqual(
            MODULE.assess_market_state(
                unknown, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )["market_operability"],
            "UNKNOWN",
        )
        stale = market_state(decision)
        stale["as_of"] = "2026-08-28T09:57:59+09:00"
        self.assertEqual(
            MODULE.assess_market_state(
                stale, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )["market_operability"],
            "UNKNOWN",
        )

    def test_inconsistent_price_limit_or_tick_snapshot_is_rejected(self):
        decision = instant("2026-08-28T10:00:00+09:00")
        reversed_limits = market_state(decision)
        reversed_limits["price_limits"]["lower_price"] = "140"
        with self.assertRaisesRegex(MODULE.KrxMarketDataError, "PRICE_LIMIT_ORDER_INVALID"):
            MODULE.assess_market_state(
                reversed_limits, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )
        misaligned = market_state(decision)
        misaligned["tick_size"]["krw"] = "3"
        with self.assertRaisesRegex(MODULE.KrxMarketDataError, "TICK_ALIGNMENT"):
            MODULE.assess_market_state(
                misaligned, CALENDARS["regular_open"], decision,
                FRESHNESS_POLICY, CONTRACT,
            )


class PacketTests(unittest.TestCase):
    def test_packet_replay_is_byte_deterministic_and_non_authorizing(self):
        decision = instant("2026-08-28T15:31:00+09:00")
        packet = {
            "schema_version": "krx_market_data_input/1", "decision_at": iso(decision),
            "calendar": copy.deepcopy(CALENDARS["regular_open"]),
            "series": [series("15m", decision), series("1h", decision), series("1d", decision)],
            "market_state": {},
            "freshness_policy": copy.deepcopy(FRESHNESS_POLICY),
            "authority": copy.deepcopy(CONTRACT["authority"]),
        }
        first = MODULE.evaluate_packet(packet, CONTRACT)
        second = MODULE.evaluate_packet(copy.deepcopy(packet), CONTRACT)
        self.assertEqual(first, second)
        self.assertEqual(first["status"], "PASS")
        self.assertEqual(first["market_state"]["market_operability"], "CLOSED_SESSION")
        self.assertEqual(first["packet_sha256"], MODULE.payload_sha256({
            key: value for key, value in first.items() if key != "packet_sha256"
        }))
        self.assertFalse(first["authority"]["real_capital_authorized"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
