#!/usr/bin/env python3
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "us_session_bars", ROOT / "market_data" / "us_session_bars.py"
)
assert SPEC and SPEC.loader
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)

SHA = "2" * 64
NY = ZoneInfo("America/New_York")


def calendar(day: str = "2026-03-09", status: str = "OPEN_REGULAR") -> dict:
    offsets = {"2026-03-06": "-05:00", "2026-03-09": "-04:00", "2026-11-27": "-05:00"}
    offset = offsets.get(day, "-04:00")
    close = "13:00:00" if status == "OPEN_EARLY_CLOSE" else "16:00:00"
    is_open = status.startswith("OPEN_")
    return {
        "session_date": day,
        "status": status,
        "timezone": "America/New_York",
        "open_at": f"{day}T09:30:00{offset}" if is_open else None,
        "close_at": f"{day}T{close}{offset}" if is_open else None,
        "observed_at": "2026-01-01T00:00:00Z",
        "available_at": "2026-01-01T00:00:01Z",
        "source_class": "OFFICIAL_EXCHANGE_CALENDAR",
        "source_id": "NYSE.NASDAQ.CALENDAR",
        "source_ref": "official://calendar",
        "source_sha256": SHA,
    }


def decision(day: str = "2026-03-09", status: str = "OPEN_REGULAR") -> dt.datetime:
    offset = "-05:00" if day in {"2026-03-06", "2026-11-27"} else "-04:00"
    close = "13:05:00" if status == "OPEN_EARLY_CLOSE" else "16:05:00"
    return dt.datetime.fromisoformat(f"{day}T{close}{offset}")


def freshness_policy(max_age: int = 3600) -> dict:
    value = {
        "schema_version": "intraday_freshness_policy/1",
        "policy_id": "US.PAPER.FRESHNESS.TEST",
        "approval_status": "RATIFIED",
        "ratified_by": "SYNTHETIC TEST FIXTURE ONLY",
        "ratified_at_utc": "2026-01-01T00:00:00Z",
        "effective_from_utc": "2026-01-02T00:00:00Z",
        "effective_to_utc": "2027-01-01T00:00:00Z",
        "input_contract_version": "intraday_freshness_guard/1",
        "max_provider_age_seconds_by_market": {"US": max_age, "KOREA": 3600, "CRYPTO": 3600},
        "max_transport_delay_seconds_by_market": {"US": 600, "KOREA": 600, "CRYPTO": 600},
    }
    value["packet_sha256"] = MOD.P9_FRESHNESS.payload_sha256(value)
    return value


def timeline(day: str = "2026-03-09", symbol: str = "ACME") -> list[dict]:
    return [{
        "symbol": symbol,
        "effective_from": "2020-01-01T00:00:00-05:00",
        "effective_to": None,
        "available_at": "2020-01-01T00:00:00Z",
        "source_ref": "official://symbol-master",
        "source_sha256": SHA,
    }]


def source(close_at: dt.datetime, *, capture_kind: str = "ORIGINAL", original=None, age_minutes: int = 5) -> dict:
    observed = close_at
    available = observed + dt.timedelta(seconds=10)
    generated = observed + dt.timedelta(seconds=15)
    return {
        "provider_id": "SYNTHETIC.PROVIDER",
        "feed_scope": "IEX_ONLY",
        "observed_at": observed.isoformat(timespec="seconds"),
        "available_at": available.isoformat(timespec="seconds"),
        "generated_at": generated.isoformat(timespec="seconds"),
        "first_seen_at": available.isoformat(timespec="seconds"),
        "original_available_at": original,
        "capture_kind": capture_kind,
        "snapshot_ref": "memory://synthetic",
        "snapshot_sha256": SHA,
        "redistribution_status": "NOT_GRANTED",
    }


def series(timeframe: str, cal: dict, at: dt.datetime, symbol: str = "ACME", actions=None) -> dict:
    contract = MOD.load_contract()
    intervals = MOD.expected_intervals(timeframe, cal, at, contract)
    bars = []
    for index, (opened, closed) in enumerate(intervals):
        price = str(100 + index)
        bars.append({
            "symbol": symbol,
            "open_at": opened.isoformat(timespec="seconds"),
            "close_at": closed.isoformat(timespec="seconds"),
            "open": price,
            "high": str(101 + index),
            "low": price,
            "close": str(101 + index),
            "volume": "1000",
            "source": source(closed),
        })
    return {
        "asset_id": "US.NASDAQ.ACME",
        "timeframe": timeframe,
        "price_basis": "RAW",
        "symbol_timeline": timeline(cal["session_date"], symbol),
        "corporate_actions": [] if actions is None else actions,
        "bars": bars,
    }


def packet(cal: dict, at: dt.datetime) -> dict:
    contract = MOD.load_contract()
    return {
        "schema_version": "us_market_data_input/1",
        "decision_at": at.isoformat(timespec="seconds"),
        "calendar": cal,
        "series": [series(tf, cal, at) for tf in ("15m", "1h", "1d")],
        "freshness_policy": freshness_policy(),
        "authority": contract["authority"],
    }


class CalendarTests(unittest.TestCase):
    def test_dst_offsets_are_date_specific_not_fixed(self):
        contract = MOD.load_contract()
        before = MOD.validate_calendar(calendar("2026-03-06"), decision("2026-03-06"), contract)
        after = MOD.validate_calendar(calendar("2026-03-09"), decision("2026-03-09"), contract)
        self.assertEqual(before["_open"].utcoffset(), dt.timedelta(hours=-5))
        self.assertEqual(after["_open"].utcoffset(), dt.timedelta(hours=-4))
        bad = calendar("2026-03-09")
        bad["open_at"] = "2026-03-09T09:30:00-05:00"
        with self.assertRaisesRegex(MOD.UsMarketDataError, "NOT_NEW_YORK_OFFSET"):
            MOD.validate_calendar(bad, decision(), contract)

    def test_regular_and_early_close_interval_counts(self):
        contract = MOD.load_contract()
        regular = calendar()
        at = decision()
        self.assertEqual(len(MOD.expected_intervals("15m", regular, at, contract)), 26)
        self.assertEqual(len(MOD.expected_intervals("1h", regular, at, contract)), 6)
        self.assertEqual(len(MOD.expected_intervals("1d", regular, at, contract)), 1)
        early = calendar("2026-11-27", "OPEN_EARLY_CLOSE")
        early_at = decision("2026-11-27", "OPEN_EARLY_CLOSE")
        self.assertEqual(len(MOD.expected_intervals("15m", early, early_at, contract)), 14)
        self.assertEqual(len(MOD.expected_intervals("1h", early, early_at, contract)), 3)
        self.assertEqual(len(MOD.expected_intervals("1d", early, early_at, contract)), 1)

    def test_closed_and_unknown_sessions_never_infer_weekday_open(self):
        at = decision()
        closed = calendar(status="CLOSED")
        value = packet(calendar(), at)
        value["calendar"] = closed
        value["series"] = []
        result = MOD.evaluate_packet(value)
        self.assertEqual(result["status"], "CLOSED_SESSION")
        unknown = copy.deepcopy(value)
        unknown["calendar"] = calendar(status="UNKNOWN")
        result = MOD.evaluate_packet(unknown)
        self.assertEqual(result["status"], "BLOCKED_UNKNOWN_SESSION")


class BarTests(unittest.TestCase):
    def test_complete_regular_packet_passes_but_has_no_order_authority(self):
        value = packet(calendar(), decision())
        result = MOD.evaluate_packet(value)
        self.assertEqual(result["status"], "PASS")
        self.assertEqual({row["timeframe"] for row in result["series"]}, {"15m", "1h", "1d"})
        self.assertFalse(result["authority"]["broker_order_post_authorized"])
        self.assertFalse(result["authority"]["real_account_authorized"])
        self.assertFalse(result["authority"]["production_authorized"])
        self.assertFalse(result["authority"]["trading_authorized"])

    def test_gap_exact_duplicate_and_conflicting_duplicate(self):
        cal, at = calendar(), decision()
        row = series("15m", cal, at)
        row["bars"].pop(5)
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertEqual(result["status"], "BLOCKED")
        self.assertTrue(any(reason.startswith("GAP:") for reason in result["reasons"]))
        row = series("15m", cal, at)
        row["bars"].append(copy.deepcopy(row["bars"][0]))
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["exact_duplicate_count"], 1)
        row = series("15m", cal, at)
        conflict = copy.deepcopy(row["bars"][0])
        conflict["volume"] = "9999"
        row["bars"].append(conflict)
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertTrue(any(reason.startswith("CONFLICTING_DUPLICATE:") for reason in result["reasons"]))

    def test_partial_or_out_of_session_and_adjusted_are_rejected(self):
        cal, at = calendar(), decision()
        row = series("1h", cal, at)
        row["bars"][0]["close_at"] = "2026-03-09T10:15:00-04:00"
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertEqual(result["status"], "BLOCKED")
        row = series("1d", cal, at)
        row["price_basis"] = "ADJUSTED"
        with self.assertRaisesRegex(MOD.UsMarketDataError, "ADJUSTED_BARS_UNRATIFIED"):
            MOD.assess_series(row, cal, at, freshness_policy())

    def test_stale_and_absent_freshness_policy_fail_closed(self):
        cal, at = calendar(), decision()
        row = series("15m", cal, at)
        result = MOD.assess_series(row, cal, at, freshness_policy(max_age=1))
        self.assertIn("P9_01_PROVIDER_AGE_EXCEEDED", result["reasons"])
        result = MOD.assess_series(row, cal, at, None)
        self.assertTrue(any(reason.startswith("P9_FRESHNESS_POLICY_OR_INPUT_INVALID") for reason in result["reasons"]))

    def test_backfill_requires_original_availability_and_replay_filters_late_rows(self):
        cal, at = calendar(), decision()
        row = series("1h", cal, at)
        row["bars"][0]["source"] = source(
            dt.datetime.fromisoformat(row["bars"][0]["close_at"]), capture_kind="BACKFILL"
        )
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertIn("BACKFILL_ORIGINAL_AVAILABILITY_UNKNOWN", result["reasons"])
        row = series("1h", cal, at)
        replay_at = dt.datetime.fromisoformat("2026-03-09T15:00:00-04:00")
        visible = MOD.replay_visible_series(row, replay_at)
        self.assertLess(len(visible["bars"]), len(row["bars"]))

        row = series("1h", cal, at)
        row["bars"][0]["source"]["first_seen_at"] = row["bars"][0]["source"]["observed_at"]
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertIn("SOURCE_FIRST_SEEN_ORDER_INVALID", result["reasons"])

    def test_split_dividend_and_symbol_change_lineage(self):
        cal, at = calendar(), decision()
        split = {
            "action_id": "SPLIT.1", "type": "SPLIT", "status": "APPLIED",
            "announced_at": "2026-02-01T12:00:00-05:00",
            "effective_at": "2026-03-08T00:00:00-05:00",
            "available_at": "2026-02-01T12:01:00-05:00", "factor": "2",
            "from_symbol": None, "to_symbol": None,
            "source_ref": "official://action", "source_sha256": SHA,
        }
        dividend = {
            "action_id": "DIV.1", "type": "CASH_DIVIDEND", "status": "APPLIED",
            "announced_at": "2026-02-01T12:00:00-05:00",
            "effective_at": "2026-03-08T00:00:00-05:00",
            "available_at": "2026-02-01T12:01:00-05:00", "factor": None,
            "from_symbol": None, "to_symbol": None,
            "source_ref": "official://action", "source_sha256": SHA,
        }
        row = series("1d", cal, at, actions=[split, dividend])
        result = MOD.assess_series(row, cal, at, freshness_policy())
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["corporate_action_count"], 2)

        change = {
            "action_id": "SYMBOL.1", "type": "SYMBOL_CHANGE", "status": "APPLIED",
            "announced_at": "2026-02-01T12:00:00-05:00",
            "effective_at": "2026-03-08T00:00:00-05:00",
            "available_at": "2026-02-01T12:01:00-05:00", "factor": None,
            "from_symbol": "OLD", "to_symbol": "NEW",
            "source_ref": "official://action", "source_sha256": SHA,
        }
        row = series("1d", cal, at, symbol="NEW", actions=[change])
        row["symbol_timeline"] = [
            {"symbol": "OLD", "effective_from": "2020-01-01T00:00:00-05:00", "effective_to": change["effective_at"], "available_at": "2020-01-01T00:00:00Z", "source_ref": "official://symbol", "source_sha256": SHA},
            {"symbol": "NEW", "effective_from": change["effective_at"], "effective_to": None, "available_at": change["available_at"], "source_ref": "official://symbol", "source_sha256": SHA},
        ]
        self.assertEqual(MOD.assess_series(row, cal, at, freshness_policy())["status"], "PASS")
        row["bars"][0]["symbol"] = "OLD"
        self.assertIn("BAR_SYMBOL_TIMELINE_MISMATCH", MOD.assess_series(row, cal, at, freshness_policy())["reasons"])

        future = copy.deepcopy(split)
        future["action_id"] = "SPLIT.FUTURE"
        future["effective_at"] = "2026-03-10T00:00:00-04:00"
        with self.assertRaisesRegex(MOD.UsMarketDataError, "FUTURE_CORPORATE_ACTION_APPLIED"):
            MOD.assess_series(series("1d", cal, at, actions=[future]), cal, at, freshness_policy())

        late_announcement = copy.deepcopy(split)
        late_announcement["action_id"] = "SPLIT.LATE.ANNOUNCEMENT"
        late_announcement["announced_at"] = "2026-03-08T01:00:00-05:00"
        late_announcement["available_at"] = "2026-03-08T01:01:00-05:00"
        with self.assertRaisesRegex(MOD.UsMarketDataError, "CORPORATE_ACTION_TIME_ORDER_INVALID"):
            MOD.assess_series(
                series("1d", cal, at, actions=[late_announcement]), cal, at, freshness_policy()
            )

    def test_missing_required_timeframe_and_bars_on_holiday_rejected(self):
        value = packet(calendar(), decision())
        value["series"].pop()
        with self.assertRaisesRegex(MOD.UsMarketDataError, "REQUIRED_TIMEFRAMES"):
            MOD.evaluate_packet(value)
        closed = calendar(status="CLOSED")
        value = packet(calendar(), decision())
        value["calendar"] = closed
        with self.assertRaisesRegex(MOD.UsMarketDataError, "BARS_PRESENT_FOR_NON_OPEN_SESSION"):
            MOD.evaluate_packet(value)


if __name__ == "__main__":
    unittest.main()
