"""Record-only PAPER shadow controls (RULE.EXIT.SHADOW_CONTROLS.V1) and D9 monitored stop fill model."""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SC = load_module("paper_shadow_controls_under_test", ROOT / "portfolio" / "paper_shadow_controls.py")
EXIT = SC.EXIT
RC = EXIT.RC
POLICY = EXIT.load_policy()
ROTATION_POLICY = POLICY["rotation_policy"]
MAPPING = RC.load_state_mapping()
FIRST_FILL = "2026-10-08T08:06:30Z"
ENTRY_DECISION = "2026-10-08T08:06:00Z"
INTERVAL = 1800  # public realtime capture cadence supplied by the caller (30 minutes)


def at(base: str, **delta) -> str:
    return EXIT.stamp(EXIT.parse_utc(base) + dt.timedelta(**delta))


def position(**overrides):
    value = {
        "market": "CRYPTO", "symbol": "KRW-SOL", "position_episode_id": "CR-SOL-EP-1",
        "rotation_scope_id": "BTC_RELATIVE_BUCKETS", "rotation_entity_id": "ALT",
        "entry_rotation_as_of_date": "2026-10-07", "first_fill_at": FIRST_FILL, "quantity": "2",
    }
    return value | overrides


def flat_bars(days=20, high="104", low="100", close="102", start=dt.date(2026, 9, 18)):
    bars = []
    for i in range(days):
        day = start + dt.timedelta(days=i)
        bars.append({"bar_date": day.isoformat(), "close_at": f"{(day + dt.timedelta(days=1)).isoformat()}T00:00:00Z",
                     "high": high, "low": low, "close": close})
    return bars


def atr_record(value_bars=None):
    return SC.wilder_atr14(POLICY, value_bars or flat_bars(), ENTRY_DECISION)


def start(**overrides):
    return SC.start_shadow_controls(POLICY, position(**overrides), first_fill_price="100", entry_decision_at=ENTRY_DECISION,
                                    atr=atr_record())


def snap(oid, t, price, freshness="FRESH"):
    return {"observation_id": oid, "kind": "SNAPSHOT", "t_obs": t, "price": price, "freshness": freshness}


def bar(oid, t, open_, high, low, freshness="FRESH"):
    return {"observation_id": oid, "kind": "BAR", "t_obs": t, "bar_end": at(t, hours=1), "open": open_, "high": high,
            "low": low, "freshness": freshness}


def regular_snapshots(start_at, count, price="100", step_minutes=30, prefix="S"):
    return [snap(f"{prefix}{i}", at(start_at, minutes=step_minutes * (i + 1)), price) for i in range(count)]


def evaluate(start_row, *, as_of, observations=(), snapshots=(), packets=(), default_fills=(), lots=None):
    return SC.evaluate_shadow_controls(
        POLICY, start_row, as_of=as_of, lots=lots or [{"t_fill": FIRST_FILL, "quantity": "2"}],
        price_observations=list(observations), decision_snapshots=list(snapshots), rotation_packets=list(packets),
        default_exit_fills=list(default_fills), expected_monitoring_interval_seconds=INTERVAL)


def control(result, cid):
    return next(row for row in result["controls"] if row["control_id"] == cid)


def lagging_packets():
    begin = dt.date(2026, 9, 1)
    observations = []
    for i in range(50):
        day = (begin + dt.timedelta(days=i)).isoformat()
        alt = "1.01" if i < 40 else "0.97"
        observations.append({
            "as_of_date": day, "status": "OBSERVED", "unknown_reason": None, "sources": [],
            "aux": {"daily_points": [{"as_of_date": day, "gross": {"ALT": alt, "BTC": "1.00", "ETH": "1.00"}}]},
            "scopes": {"BTC_RELATIVE_BUCKETS": [
                {"entity_id": "ALT", "source_identity": "ALT", "strength": "0.05"},
                {"entity_id": "BTC", "source_identity": "BTC", "strength": "0"},
                {"entity_id": "ETH", "source_identity": "ETH", "strength": "0.01"},
            ]},
        })
    packets = RC.build_market_packets(ROTATION_POLICY, "CRYPTO", observations, MAPPING)
    return [{"packet": p, "available_at": f"{p['as_of_date']}T07:20:00Z"} for p in packets]


class DefinitionTests(unittest.TestCase):
    def test_definitions_match_study_v2_and_record(self):
        rule = POLICY["config"]["rules"]["shadow_controls"]
        self.assertEqual(rule["rule_id"], "RULE.EXIT.SHADOW_CONTROLS.V1")
        self.assertTrue(rule["record_only"])
        self.assertEqual(POLICY["config"]["source_documents"]["exit_study_v2"]["sha256"],
                         "2f8e4b39a26aa108b701a22b477b8b945cff2b6d8087c9b9556ead07cd005c81")
        self.assertEqual({cid: cfg["study_configuration"] for cid, cfg in rule["controls"].items()},
                         {"1-B": "LAG", "TS14": "TS14", "PTP1": "PTP1", "DS5": "DS5"})
        row = start()
        levels = {c["control_id"]: c["levels"] for c in row["controls"]}
        self.assertEqual(row["atr14"]["atr14"], "4")
        self.assertEqual(levels["DS5"], {"stop_price": "80"})                    # P1 - 5 x ATR14
        self.assertEqual(levels["PTP1"]["limit_price"], "112")                   # P1 + 1 x (3 x ATR14)
        self.assertEqual(levels["PTP1"]["quantity_fraction"], "0.5")
        self.assertEqual(levels["TS14"], {"deadline_at": "2026-10-22T08:06:30Z"})  # first fill + 14 x 24h
        self.assertEqual(row["record_only"], True)
        self.assertEqual(set(row["authority"].values()), {False})
        EXIT.verify_payload_sha(row, "START_SHA")

    def test_kr_us_units_from_p3_record(self):
        for market, scope, entity in (("KR", "KOSPI", "KOSPI.SECTOR.18"), ("US", "SPY", "XLK")):
            row = SC.start_shadow_controls(POLICY, position(market=market, rotation_scope_id=scope, rotation_entity_id=entity),
                                           first_fill_price="100", entry_decision_at=ENTRY_DECISION, atr=atr_record())
            by_id = {c["control_id"]: c for c in row["controls"]}
            self.assertEqual(row["units_ratification"], {
                "rule_id": "RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1", "record_id": "USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915",
                "sha256": "2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd",
                "registry_status": "NOT_YET_IN_RULE_REGISTRY_CITED_BY_RECORD"})
            self.assertEqual(by_id["1-B"]["status"], "DEFINED_EQUALS_DEFAULT_RELEASE_ONLY")
            self.assertEqual(by_id["TS14"]["levels"]["trading_days"], 14)
            self.assertEqual(by_id["DS5"]["levels"], {"stop_price": "80"})          # P1 - 5 x daily ATR14
            self.assertEqual(by_id["PTP1"]["levels"]["limit_price"], "112")         # P1 + 1R, R = 3 x daily ATR14
            self.assertNotIn("NOT_DEFINED", {c["status"] for c in row["controls"]})

    def test_wilder_atr14_matches_study_formula_and_ignores_later_bars(self):
        bars = flat_bars(days=14, high="110", low="100", close="105")
        bars.append({"bar_date": "2026-10-02", "close_at": "2026-10-03T00:00:00Z", "high": "130", "low": "100", "close": "120"})
        record = SC.wilder_atr14(POLICY, bars, ENTRY_DECISION)
        # TR: first bar 10, next 13 bars max(10, 5, 5) = 10 -> seed 10; then (10 x 13 + 30) / 14
        self.assertEqual(record["atr14"], SC._q((Decimal(10) * 13 + Decimal(30)) / 14))
        future = bars + [{"bar_date": "2026-10-08", "close_at": "2026-10-09T00:00:00Z", "high": "500", "low": "1", "close": "2"}]
        later = SC.wilder_atr14(POLICY, future, ENTRY_DECISION)
        self.assertEqual((later["atr14"], later["bars_excluded_after_decision"], later["bars_sha256"]),
                         (record["atr14"], 1, record["bars_sha256"]))
        short = SC.wilder_atr14(POLICY, flat_bars(days=13), ENTRY_DECISION)
        self.assertEqual((short["status"], short["atr14"]), ("UNKNOWN", None))
        row = SC.start_shadow_controls(POLICY, position(), first_fill_price="100", entry_decision_at=ENTRY_DECISION, atr=short)
        self.assertEqual({c["control_id"]: c["status"] for c in row["controls"]},
                         {"1-B": "DEFINED", "TS14": "DEFINED", "PTP1": "UNKNOWN", "DS5": "UNKNOWN"})

    def test_btc_bucket_lagging_not_defined(self):
        row = start(symbol="KRW-BTC", rotation_entity_id="BTC")
        lag = next(c for c in row["controls"] if c["control_id"] == "1-B")
        self.assertEqual((lag["status"], lag["reason"]), ("DEFINED_EQUALS_DEFAULT_BENCHMARK_BUCKET", "LAGGING_NOT_DEFINED_FOR_BENCHMARK_BUCKET"))


class MonitoredStopTests(unittest.TestCase):
    """D9: fill at the first allowed price after the trigger, gap at the open, monitoring gaps recorded."""

    def test_fill_is_first_allowed_price_after_trigger_not_the_stop(self):
        obs = regular_snapshots(FIRST_FILL, 3) + [snap("T", at(FIRST_FILL, minutes=120), "79"),
                                                  snap("F", at(FIRST_FILL, minutes=150), "83")]
        ds5 = control(evaluate(start(), as_of=at(FIRST_FILL, hours=3), observations=obs), "DS5")
        self.assertEqual(ds5["status"], "CLOSED")
        leg = ds5["legs"][0]
        self.assertEqual((leg["price"], leg["t_fill"], leg["gap_down"], leg["quantity"]), ("83", at(FIRST_FILL, minutes=150), False, "2"))
        self.assertEqual(leg["stop_minus_fill"], "-3")
        self.assertIsNone(leg["study_v2_gap_primary_reference_price"])  # study GAP model is bar-based only
        self.assertEqual(ds5["component"]["trigger"]["observation_id"], "T")

    def test_gap_down_fills_at_open_and_stale_is_never_a_fill(self):
        obs = regular_snapshots(FIRST_FILL, 6, price="95") + [
            bar("B", at(FIRST_FILL, minutes=60), "95", "96", "79"),      # completed bar (known +120) low below the stop
            snap("STALE", at(FIRST_FILL, minutes=122), "90", freshness="STALE"),
            bar("N", at(FIRST_FILL, minutes=125), "70", "72", "65"),     # next allowed price opens below the stop (gap)
        ]
        ds5 = control(evaluate(start(), as_of=at(FIRST_FILL, hours=4), observations=obs), "DS5")
        leg = ds5["legs"][0]
        self.assertEqual((leg["price"], leg["gap_down"], leg["fill_basis"]), ("70", True, "D9_FIRST_ALLOWED_BAR_AFTER_TRIGGER"))
        self.assertEqual(leg["stop_minus_fill"], "10")

    def test_back_to_back_bars_fill_at_next_bar_open_at_trigger_bar_end(self):
        # B1 [+60m, +120m) crosses the stop; B2 starts exactly at B1's end and is the first price after the trigger
        obs = regular_snapshots(FIRST_FILL, 6, price="95") + [
            bar("B1", at(FIRST_FILL, minutes=60), "95", "96", "79"),
            bar("B2", at(FIRST_FILL, minutes=120), "84", "86", "83"),
        ]
        ds5 = control(evaluate(start(), as_of=at(FIRST_FILL, hours=4), observations=obs), "DS5")
        leg = ds5["legs"][0]
        self.assertEqual((leg["price"], leg["t_fill"], leg["fill_basis"]), ("84", at(FIRST_FILL, minutes=120), "D9_FIRST_ALLOWED_BAR_AFTER_TRIGGER"))
        self.assertEqual((leg["gap_down"], leg["stop_minus_fill"]), (False, "-4"))
        self.assertEqual(leg["study_v2_gap_primary_reference_price"], "80")  # min(stop 80, next open 84)
        gapped = obs[:-1] + [bar("B2", at(FIRST_FILL, minutes=120), "70", "72", "65")]
        leg = control(evaluate(start(), as_of=at(FIRST_FILL, hours=4), observations=gapped), "DS5")["legs"][0]
        self.assertEqual((leg["price"], leg["gap_down"], leg["study_v2_gap_primary_reference_price"]), ("70", True, "70"))
        # a snapshot captured exactly at the bar end is not strictly after the trigger and is not used
        snapshot_at_end = regular_snapshots(FIRST_FILL, 1, price="95") + [
            bar("B1", at(FIRST_FILL, minutes=30), "95", "96", "79"),
            snap("SAME", at(FIRST_FILL, minutes=90), "88"), snap("NEXT", at(FIRST_FILL, minutes=100), "87")]
        leg = control(evaluate(start(), as_of=at(FIRST_FILL, hours=2), observations=snapshot_at_end), "DS5")["legs"][0]
        self.assertEqual(leg["price"], "87")

    def test_no_trigger_from_observations_at_or_before_entry(self):
        obs = [snap("PRE", FIRST_FILL, "70"), bar("ENTRYBAR", at(FIRST_FILL, minutes=-6), "100", "101", "60")] + regular_snapshots(FIRST_FILL, 4)
        ds5 = control(evaluate(start(), as_of=at(FIRST_FILL, hours=2, minutes=1), observations=obs), "DS5")
        self.assertEqual((ds5["status"], ds5["component"]["status"]), ("OPEN", "NOT_TRIGGERED"))

    def test_monitoring_gap_recorded_and_fill_only_after_recovery(self):
        obs = [snap("S1", at(FIRST_FILL, minutes=30), "100"),
               # no snapshot for 3 hours (> 2 x 30 minutes); a back-filled completed bar shows the stop was crossed
               bar("GAPBAR", at(FIRST_FILL, hours=1), "90", "91", "75"),
               bar("GAPBAR2", at(FIRST_FILL, hours=2), "76", "80", "74"),
               snap("RECOVER", at(FIRST_FILL, hours=3, minutes=30), "78")]
        result = evaluate(start(), as_of=at(FIRST_FILL, hours=3, minutes=31), observations=obs)
        ds5 = control(result, "DS5")
        self.assertEqual(result["monitoring_gaps"][0], {"from": at(FIRST_FILL, minutes=30), "to": at(FIRST_FILL, hours=3, minutes=30),
                                                       "seconds": 10800, "open_at_as_of": False})
        leg = ds5["legs"][0]
        self.assertEqual((leg["price"], leg["t_fill"]), ("78", at(FIRST_FILL, hours=3, minutes=30)))  # not the in-gap bar open 76
        self.assertTrue(ds5["component"]["trigger_in_monitoring_gap"])
        self.assertEqual(leg["stop_minus_fill"], "2")

    def test_triggered_without_allowed_price_waits(self):
        obs = [snap("S1", at(FIRST_FILL, minutes=30), "79")]
        ds5 = control(evaluate(start(), as_of=at(FIRST_FILL, minutes=40), observations=obs), "DS5")
        self.assertEqual((ds5["status"], ds5["legs"]), ("TRIGGERED_AWAITING_FIRST_ALLOWED_PRICE", []))


def kr_calendar():
    begin = dt.date(2026, 9, 21)
    holidays = {"2026-09-25", "2026-10-09"}
    sessions = {}
    for i in range(60):
        day = begin + dt.timedelta(days=i)
        sessions[day.isoformat()] = "CLOSED" if day.weekday() >= 5 or day.isoformat() in holidays else "OPEN"
    return {"market": "KR", "source": {"fixture": True}, "sessions": sessions}


def kr_bar(oid, kst_start, open_, high, low, freshness="FRESH"):
    local = dt.datetime.fromisoformat(kst_start).replace(tzinfo=dt.timezone(dt.timedelta(hours=9)))
    t = EXIT.stamp(local)
    return {"observation_id": oid, "kind": "BAR", "t_obs": t, "bar_end": at(t, minutes=15), "open": open_, "high": high,
            "low": low, "freshness": freshness}


class KrUsShadowTests(unittest.TestCase):
    FILL = "2026-09-24T01:00:00Z"  # 10:00 KST Thursday

    def kr_start(self):
        return SC.start_shadow_controls(POLICY, position(market="KR", symbol="005930", rotation_scope_id="KOSPI",
                                                         rotation_entity_id="KOSPI.SECTOR.18", first_fill_at=self.FILL),
                                        first_fill_price="100", entry_decision_at="2026-09-23T22:00:00Z", atr=SC.wilder_atr14(
                                            POLICY, flat_bars(start=dt.date(2026, 9, 1)), "2026-09-23T22:00:00Z"))

    def evaluate(self, *, as_of, observations=(), snapshots=(), default_fills=(), calendar="default"):
        return SC.evaluate_shadow_controls(
            POLICY, self.kr_start(), as_of=as_of, lots=[{"t_fill": self.FILL, "quantity": "10"}],
            price_observations=list(observations), decision_snapshots=list(snapshots), rotation_packets=[],
            default_exit_fills=list(default_fills), expected_monitoring_interval_seconds=900,
            calendar=kr_calendar() if calendar == "default" else calendar)

    def test_ts14_counts_open_sessions_and_fills_in_next_window(self):
        # OPEN sessions after 09-24 (09-25 holiday): 09-28..10-02 (5), 10-05..10-08 (9, 10-09 holiday), 10-12..10-16 (14)
        snapshots = [{"snapshot_id": "K1", "captured_at": "2026-10-16T06:19:00Z", "decision_at": "2026-10-16T06:19:30Z", "freshness": "FRESH"},
                     {"snapshot_id": "K2", "captured_at": "2026-10-16T07:10:00Z", "decision_at": "2026-10-16T07:10:30Z", "freshness": "FRESH"}]
        obs = [kr_bar("NXT", "2026-10-19T08:30:00", "98", "99", "97"),        # NXT pre-market: never a fill
               kr_bar("OPEN", "2026-10-19T09:15:00", "97", "98", "96")]
        ts = next(c for c in self.evaluate(as_of="2026-10-19T01:00:00Z", observations=obs, snapshots=snapshots)["controls"]
                  if c["control_id"] == "TS14")
        self.assertEqual((ts["component"]["deadline_session_date"], ts["component"]["deadline_at"]), ("2026-10-16", "2026-10-16T06:20:00Z"))
        self.assertEqual(ts["component"]["decision_snapshot_id"], "K2")
        self.assertEqual((ts["status"], ts["legs"][0]["price"], ts["legs"][0]["leg"]), ("CLOSED", "97", "TIME_STOP_14_TRADING_DAYS"))
        unknown = next(c for c in self.evaluate(as_of="2026-10-19T01:00:00Z", calendar=None)["controls"] if c["control_id"] == "TS14")
        self.assertEqual((unknown["status"], unknown["reason"]), ("UNKNOWN", "SESSION_CALENDAR_NOT_SUPPLIED"))

    def test_ds5_last_bar_trigger_fills_at_next_session_open_without_overnight_gap(self):
        obs = [kr_bar(f"B{i}", f"2026-09-24T{10 + i // 4:02d}:{(i % 4) * 15:02d}:00", "100", "101", "99") for i in range(20)]
        obs += [kr_bar("LAST", "2026-09-24T15:05:00", "90", "91", "79"),       # completes 15:20: stop crossed
                kr_bar("AFTER", "2026-09-24T15:30:00", "78", "78", "77"),      # after-market: not an allowed price
                kr_bar("NEXT", "2026-09-28T09:15:00", "75", "76", "74")]       # next OPEN session open (gap)
        result = self.evaluate(as_of="2026-09-28T00:40:00Z", observations=obs)
        ds5 = next(c for c in result["controls"] if c["control_id"] == "DS5")
        leg = ds5["legs"][0]
        self.assertEqual((ds5["status"], leg["price"], leg["t_fill"], leg["gap_down"]), ("CLOSED", "75", "2026-09-28T00:15:00Z", True))
        self.assertFalse(ds5["component"]["trigger_in_monitoring_gap"])
        self.assertEqual([g for g in result["monitoring_gaps"] if g["from"] < "2026-09-28T00:15:00Z" and not g["open_at_as_of"]], [])
        # an after-market print below the stop alone never triggers
        quiet = obs[:20] + [kr_bar("AM", "2026-09-24T16:30:00", "70", "71", "60")]
        ds5 = next(c for c in self.evaluate(as_of="2026-09-24T09:00:00Z", observations=quiet)["controls"] if c["control_id"] == "DS5")
        self.assertEqual(ds5["component"]["status"], "NOT_TRIGGERED")

    def test_1b_release_only_equals_default_exit(self):
        exit_fill = {"t_fill": "2026-09-29T00:30:00Z", "quantity": "10", "price": "103", "reason_code": "RELEASE_CONFIRMED"}
        result = self.evaluate(as_of="2026-09-30T00:00:00Z", default_fills=[exit_fill])
        lag = next(c for c in result["controls"] if c["control_id"] == "1-B")
        self.assertEqual((lag["status"], lag["shadow_minus_default_gross_proceeds"], lag["reason"]),
                         ("CLOSED", "0", "KR_US_1B_RELEASE_ONLY_UNTIL_LAGGING_DEFINITION_CONFIRMED"))
        self.assertEqual(result["units_ratification"]["rule_id"], "RULE.EXIT.SHADOW_CONTROLS_KR_US_UNITS.V1")


class ComponentTests(unittest.TestCase):
    def test_ptp1_bar_and_snapshot_fill_rules_and_top_up_exclusion(self):
        lots = [{"t_fill": FIRST_FILL, "quantity": "2"}, {"t_fill": at(FIRST_FILL, days=2), "quantity": "2"}]
        obs = [bar("B1", at(FIRST_FILL, days=1), "113", "115", "111")]  # gap above the level -> max(level, open) = open
        exit_fill = {"t_fill": at(FIRST_FILL, days=5), "quantity": "4", "price": "120", "reason_code": "RELEASE_CONFIRMED"}
        ptp = control(evaluate(start(), as_of=at(FIRST_FILL, days=6), observations=obs, lots=lots, default_fills=[exit_fill]), "PTP1")
        self.assertEqual(ptp["status"], "CLOSED")
        self.assertEqual([(l["leg"], l["quantity"], l["price"]) for l in ptp["legs"]],
                         [("PARTIAL_TAKE_PROFIT_1R", "1", "113"), ("DEFAULT_EXIT", "1", "120")])
        self.assertEqual(ptp["component"]["top_ups_after_fill_excluded"], "2")
        self.assertIsNone(ptp["shadow_minus_default_gross_proceeds"])
        self.assertEqual(ptp["shadow_quantity_differs_from_default"], "2")
        snap_obs = [snap("S1", at(FIRST_FILL, hours=5), "125")]
        open_ptp = control(evaluate(start(), as_of=at(FIRST_FILL, days=1), observations=snap_obs), "PTP1")
        self.assertEqual((open_ptp["status"], open_ptp["legs"][0]["price"], open_ptp["legs"][0]["quantity"]),
                         ("PARTIAL_COMPONENT_FILLED_OPEN", "112", "1"))

    def test_ts14_first_fresh_decision_snapshot_then_first_allowed_price(self):
        deadline = "2026-10-22T08:06:30Z"
        snapshots = [{"snapshot_id": "D0", "captured_at": at(deadline, minutes=-1), "decision_at": at(deadline, minutes=-1), "freshness": "FRESH"},
                     {"snapshot_id": "D1", "captured_at": at(deadline, minutes=29), "decision_at": at(deadline, minutes=29, seconds=20), "freshness": "STALE"},
                     {"snapshot_id": "D2", "captured_at": at(deadline, minutes=59), "decision_at": at(deadline, minutes=59, seconds=20), "freshness": "FRESH"}]
        obs = [snap(f"P{i}", at(deadline, minutes=-300 + 30 * i), "101") for i in range(11)] + [
            snap("AT", at(deadline, minutes=59), "105"), snap("AFTER", at(deadline, minutes=89), "106")]
        ts = control(evaluate(start(), as_of=at(deadline, hours=2), observations=obs, snapshots=snapshots), "TS14")
        self.assertEqual((ts["status"], ts["component"]["decision_snapshot_id"]), ("CLOSED", "D2"))
        self.assertEqual((ts["legs"][0]["price"], ts["legs"][0]["t_fill"]), ("106", at(deadline, minutes=89)))

    def test_default_exit_before_component_leaves_shadow_equal_to_default(self):
        exit_fill = {"t_fill": at(FIRST_FILL, days=3), "quantity": "2", "price": "97", "reason_code": "RELEASE_CONFIRMED"}
        obs = regular_snapshots(FIRST_FILL, 3) + [snap("LATE_DROP", at(FIRST_FILL, days=4), "50")]
        result = evaluate(start(), as_of=at(FIRST_FILL, days=20), observations=obs, default_fills=[exit_fill])
        for cid in ("TS14", "1-B", "PTP1", "DS5"):
            row = control(result, cid)
            self.assertEqual((row["status"], row["shadow_minus_default_gross_proceeds"]), ("CLOSED", "0"), cid)
            self.assertEqual(row["legs"][-1]["leg"], "DEFAULT_EXIT")
        self.assertEqual(result["default_exit"]["reason_codes"], ["RELEASE_CONFIRMED"])

    def test_1b_sells_on_first_lagging_packet_after_entry(self):
        packets = lagging_packets()
        snapshots = [{"snapshot_id": "D14", "captured_at": "2026-10-14T08:06:00Z", "decision_at": "2026-10-14T08:06:20Z", "freshness": "FRESH"}]
        obs = [snap("P", "2026-10-14T08:36:00Z", "96")]
        row = control(evaluate(start(), as_of="2026-10-14T09:00:00Z", observations=obs, snapshots=snapshots, packets=packets), "1-B")
        self.assertEqual(row["component"]["trigger"]["as_of_date"], "2026-10-14")
        self.assertEqual(row["component"]["lagging_packets"][0], {"as_of_date": "2026-10-08", "status": "OBSERVED", "lagging_warning": False})
        self.assertEqual((row["status"], row["legs"][0]["price"], row["legs"][0]["leg"]), ("CLOSED", "96", "LAGGING_EXIT"))
        # the packet is not visible before its availability time (no lookahead)
        early = control(evaluate(start(), as_of="2026-10-14T07:00:00Z", packets=packets), "1-B")
        self.assertNotIn("trigger", early["component"])
        btc = control(evaluate(start(symbol="KRW-BTC", rotation_entity_id="BTC"), as_of="2026-10-14T09:00:00Z",
                               observations=obs, snapshots=snapshots, packets=packets), "1-B")
        self.assertEqual((btc["status"], btc["legs"]), ("OPEN", []))

    def test_future_observations_never_change_a_result(self):
        obs = regular_snapshots(FIRST_FILL, 4)
        base = evaluate(start(), as_of=at(FIRST_FILL, hours=2, minutes=1), observations=obs)
        future = obs + [snap("FUT", at(FIRST_FILL, hours=3), "10"), bar("FUTBAR", at(FIRST_FILL, hours=1, minutes=30), "100", "300", "1")]
        again = evaluate(start(), as_of=at(FIRST_FILL, hours=2, minutes=1), observations=future)
        self.assertEqual(EXIT.canonical_json(again), EXIT.canonical_json(base))
        self.assertEqual({c["control_id"]: c["status"] for c in base["controls"]}, {"TS14": "OPEN", "1-B": "OPEN", "PTP1": "OPEN", "DS5": "OPEN"})

    def test_result_is_record_only_and_tamper_evident(self):
        row = start()
        result = evaluate(row, as_of=at(FIRST_FILL, hours=1))
        self.assertTrue(result["record_only"])
        self.assertEqual(result["costs"], "NOT_APPLIED_SCORECARD_CONTRACT_OWNS_COSTS")
        refs = [(r["rule_id"], r["role"]) for r in result["rule_refs"]]
        self.assertEqual(refs, [("RULE.EXEC.MONITORED_STOP_FILL_MODEL.V1", "APPLIED"), ("RULE.EXIT.SHADOW_CONTROLS.V1", "APPLIED")])
        tampered = copy.deepcopy(row)
        tampered["controls"][3]["levels"]["stop_price"] = "90"
        with self.assertRaisesRegex(SC.PaperShadowControlError, "SHADOW_START_SHA_MISMATCH"):
            evaluate(tampered, as_of=at(FIRST_FILL, hours=1))
        text = (ROOT / "portfolio" / "paper_shadow_controls.py").read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "record_fill", "put_intent"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
