#!/usr/bin/env python3
"""P1-COM-05 — US free-axis (TREND/RISK_VOL/LIQUIDITY) historical replay population.

SHADOW historical-backfill evidence only, never NATURAL. Offline/fixture-only:
no real Alpaca or FRED network call is required or attempted here.

The two load-bearing guarantees under test are:

1. US BREADTH and US LEADERSHIP are never populated — they stay UNKNOWN with an
   attributable exclusion basis in every record, and the population validator
   rejects any output that carries a value for them.
2. The three replayed axes are byte-identical to what the live, unmodified
   ``regime/paper_regime_reference.py::build_us`` produces for the same inputs,
   across every threshold boundary, so no threshold is silently forked here.
"""

from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "us_historical_replay_population.py"
SPEC = importlib.util.spec_from_file_location("us_historical_replay_population_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

FMD = MODULE.FMD
PRR = MODULE.PRR

FRED_KEY = "FRED-SECRET-NEVER-PERSIST"
CREDENTIALS = {
    "fred_key": FRED_KEY,
    "alpaca_key": "ALPACA-MARKET-DATA-KEY",
    "alpaca_secret": "ALPACA-MARKET-DATA-SECRET",
}
ANCHOR = "2026-08-28"
TREND_SYMBOLS = ("SPY", "QQQ", "IWM")


def sessions(start: dt.date, end: dt.date) -> list[dt.date]:
    days = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            days.append(current)
        current += dt.timedelta(days=1)
    return days


class FakeProviders:
    """Serves deterministic Alpaca IEX daily bars and FRED observations.

    Every response is generated from the *requested* window only, so the fake
    behaves like a real point-in-time provider: asking for an earlier anchor
    genuinely yields an earlier last session, and nothing after ``end`` exists
    unless a test deliberately turns on ``leak_future``.
    """

    def __init__(
        self,
        *,
        slopes=None,
        vix="17.5",
        liquidity=None,
        leak_future_bar=False,
        leak_future_observation=False,
        fail_fred=False,
        fail_alpaca=False,
    ):
        self.slopes = slopes or {"SPY": "0.5", "QQQ": "0.5", "IWM": "0.5"}
        self.vix = vix
        self.liquidity = liquidity or {
            "WRESBAL": ("3000", "3100"), "TOTBKCR": ("17000", "17200"),
        }
        self.leak_future_bar = leak_future_bar
        self.leak_future_observation = leak_future_observation
        self.fail_fred = fail_fred
        self.fail_alpaca = fail_alpaca
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, url, headers=None):
        parsed = urlparse(url)
        query = parse_qs(parsed.query)
        self.calls.append((parsed.path, query))
        if parsed.netloc == "data.alpaca.markets":
            if self.fail_alpaca:
                raise FMD.FreeMarketDataError("HTTP_ERROR:403")
            return self._alpaca(parsed.path, query)
        if self.fail_fred:
            # Deliberately embeds the credential so redaction is exercised.
            raise FMD.FreeMarketDataError(f"HTTP_ERROR:{FRED_KEY}")
        if parsed.path == "/fred/series":
            return self._fred_metadata(query)
        return self._fred_observations(query)

    def _alpaca(self, path, query) -> bytes:
        symbol = path.split("/")[3]
        start = dt.date.fromisoformat(query["start"][0][:10])
        end = dt.date.fromisoformat(query["end"][0][:10])
        if self.leak_future_bar:
            end = end + dt.timedelta(days=3)
        slope = MODULE.Decimal(self.slopes[symbol])
        bars = []
        for index, day in enumerate(sessions(start, end)):
            close = MODULE.Decimal("400") + slope * MODULE.Decimal(index)
            bars.append({
                "t": f"{day.isoformat()}T00:00:00Z",
                "o": str(close), "h": str(close + 1), "l": str(close - 1),
                "c": str(close), "v": "1000",
            })
        return json.dumps({"bars": bars, "symbol": symbol}).encode()

    def _fred_metadata(self, query) -> bytes:
        series_id = query["series_id"][0]
        return json.dumps({"seriess": [{
            "id": series_id,
            "title": f"{series_id} title",
            "frequency": "Weekly, Ending Wednesday",
            "units": "Billions of Dollars",
        }]}).encode()

    def _fred_observations(self, query) -> bytes:
        series_id = query["series_id"][0]
        end = dt.date.fromisoformat(query["observation_end"][0])
        if self.leak_future_observation:
            end = end + dt.timedelta(days=2)
        if series_id == "VIXCLS":
            values = ["16.0", self.vix]
        else:
            values = list(self.liquidity[series_id])
        rows = [
            {
                "date": (end - dt.timedelta(days=7 * (len(values) - 1 - offset))).isoformat(),
                "value": value,
                "realtime_start": query["realtime_start"][0],
                "realtime_end": query["realtime_end"][0],
            }
            for offset, value in enumerate(values)
        ]
        return json.dumps({"observations": rows}).encode()


def build(dates, providers=None, credentials=None):
    return MODULE.build_population(
        credentials or CREDENTIALS, dates, getter=providers or FakeProviders(),
    )


def full_us_packet(trend_returns, vix, liquidity_changes):
    """A complete 5/5 US packet in exactly the shape build_us consumes."""
    return {
        "us_market_reference": {
            "status": "READY",
            "as_of_session_date": ANCHOR,
            "trend_etfs": [
                {"symbol": symbol, "returns": {"20_session_pct": str(value)}}
                for symbol, value in zip(TREND_SYMBOLS, trend_returns)
            ],
            "proxy_axes": {
                "BREADTH": {"measurement": {"advance_fraction": "0.6"}},
                "LEADERSHIP": {"measurement": {"ordered_groups": [
                    {"return_pct": "1" if index < 8 else "-1"} for index in range(12)
                ]}},
            },
        },
        "fred": {"value": str(vix)},
        "fred_liquidity": {"series": [
            {"series_id": "WRESBAL", "change": str(liquidity_changes[0])},
            {"series_id": "TOTBKCR", "change": str(liquidity_changes[1])},
        ]},
    }


class UsFreeAxisReplayScopeTest(unittest.TestCase):
    def test_schema_mode_and_evidence_class_are_shadow_never_natural(self):
        population = build([ANCHOR])
        self.assertEqual(population["schema_version"], "regime_us_historical_replay_population/v1")
        self.assertEqual(population["mode"], "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL")
        self.assertEqual(population["wbs"], "P1-COM-05")
        self.assertEqual(population["market"], "US")
        self.assertEqual(population["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY")
        self.assertIs(population["authority"]["natural_promotion_authorized"], False)
        for record in population["records"]:
            self.assertEqual(record["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY")
            self.assertNotEqual(record["status"], "NATURAL")

    def test_replayed_axes_are_exactly_the_three_free_source_axes(self):
        population = build([ANCHOR])
        self.assertEqual(population["replayed_axes"], ["TREND", "RISK_VOL", "LIQUIDITY"])
        self.assertEqual(sorted(population["excluded_axes"]), ["BREADTH", "LEADERSHIP"])
        record = population["records"][0]
        self.assertEqual(record["status"], "FREE_AXES_OBSERVED")
        self.assertEqual(record["free_axis_coverage"]["ratio"], "3/3")
        self.assertEqual(record["five_axis"]["coverage"]["ratio"], "3/5")
        self.assertEqual(
            record["five_axis"]["coverage"]["missing_axes"], ["BREADTH", "LEADERSHIP"],
        )

    def test_breadth_and_leadership_are_never_populated(self):
        population = build([ANCHOR, "2026-08-27", "not-a-date"])
        for record in population["records"]:
            five_axis = record["five_axis"]
            if five_axis is None:
                continue
            for name in ("BREADTH", "LEADERSHIP"):
                entry = five_axis["axes"][name]
                self.assertEqual(entry["status"], "UNKNOWN", name)
                self.assertIsNone(entry["measurement"], name)
                self.assertEqual(
                    entry["reason"], "EXCLUDED_PROXY_RATIFIED_CURRENT_REFERENCE_ONLY",
                )
                self.assertNotIn(name, record["free_axis_coverage"]["observed_axes"])
                self.assertNotIn(name, five_axis["coverage"]["defined_axes"])
            candidate = record["candidate_normalized_result"]
            if candidate is not None:
                self.assertEqual(
                    [row["axis"] for row in candidate["axes"]],
                    ["TREND", "RISK_VOL", "LIQUIDITY"],
                )

    def test_exclusion_basis_is_read_from_the_contract_and_fails_closed_when_changed(self):
        contract = FMD.load_contract(FMD.CONTRACT_PATH)
        basis = MODULE.exclusion_basis(contract)
        self.assertEqual(
            basis["BREADTH"]["basis"][
                "config/free_market_data_contract.json"
                "#alpaca.current_proxy_axes.approval_status"
            ],
            "RATIFIED_CURRENT_REFERENCE_ONLY",
        )
        widened = copy.deepcopy(contract)
        widened["alpaca"]["current_proxy_axes"]["approval_status"] = "RATIFIED_HISTORICAL_REPLAY"
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.exclusion_basis(widened)
        authorized = copy.deepcopy(contract)
        authorized["authority"]["us_breadth_authorized"] = True
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.exclusion_basis(authorized)

    def test_partial_coverage_never_classifies_a_us_regime(self):
        record = build([ANCHOR])["records"][0]
        candidate = record["candidate_normalized_result"]
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertIsNone(candidate["paper_reference"]["score"])
        self.assertIsNone(candidate["paper_reference"]["confidence"])
        self.assertEqual(candidate["runtime_regime"], "UNKNOWN")
        self.assertEqual(
            candidate["classification_status"], "NOT_COMPUTABLE_PARTIAL_AXIS_COVERAGE",
        )
        self.assertEqual(candidate["coverage"]["ratio"], "3/5")

    def test_authority_is_all_false_except_the_one_shadow_flag(self):
        authority = build([ANCHOR])["authority"]
        self.assertTrue(authority["historical_replay_evidence_authorized"])
        for key, value in authority.items():
            if key == "historical_replay_evidence_authorized":
                continue
            self.assertIs(value, False, key)
        for critical in (
            "us_breadth_authorized", "us_leadership_authorized", "natural_promotion_authorized",
            "action_authorized", "order_authorized", "capital_authorized",
            "production_authorized", "trading_authorized", "real_authorized",
        ):
            self.assertIs(authority[critical], False, critical)


class UsFreeAxisRuleParityTest(unittest.TestCase):
    """The replayed rows must equal the live build_us rows, boundary by boundary."""

    def setUp(self):
        self.policy = MODULE._load_candidate_policy()

    def _built_rows(self, packet):
        return {row["axis"]: row for row in PRR.build_us(packet, self.policy)["axes"]}

    def test_trend_rows_match_build_us_for_every_positive_count(self):
        for returns in (
            ("1.5", "2.5", "3.5"), ("1.5", "2.5", "-3.5"), ("1.5", "-2.5", "-3.5"),
            ("-1.5", "-2.5", "-3.5"), ("0", "1.5", "-2.5"), ("0", "0", "0"),
        ):
            with self.subTest(returns=returns):
                packet = full_us_packet(returns, "17.5", ("1", "1"))
                self.assertEqual(
                    MODULE.trend_axis_row(packet["us_market_reference"]["trend_etfs"]),
                    self._built_rows(packet)["TREND"],
                )

    def test_risk_vol_rows_match_build_us_at_every_threshold_boundary(self):
        for vix in (
            "0", "9.99", "14.9999", "15", "15.0001", "24.9999", "25", "25.0001",
            "29.9999", "30", "30.0001", "45.5", "80",
        ):
            with self.subTest(vix=vix):
                packet = full_us_packet(("1.5", "2.5", "3.5"), vix, ("1", "1"))
                self.assertEqual(
                    MODULE.risk_vol_axis_row(packet["fred"]["value"]),
                    self._built_rows(packet)["RISK_VOL"],
                )

    def test_liquidity_rows_match_build_us_for_every_sign_pair(self):
        for changes in (
            ("100", "200"), ("-100", "-200"), ("100", "-200"), ("-100", "200"),
            ("0", "200"), ("0", "0"),
        ):
            with self.subTest(changes=changes):
                packet = full_us_packet(("1.5", "2.5", "3.5"), "17.5", changes)
                self.assertEqual(
                    MODULE.liquidity_axis_row(packet["fred_liquidity"]["series"]),
                    self._built_rows(packet)["LIQUIDITY"],
                )

    def test_replayed_record_rows_match_build_us_end_to_end(self):
        providers = FakeProviders(
            slopes={"SPY": "0.5", "QQQ": "-0.5", "IWM": "0.5"},
            vix="26.5",
            liquidity={"WRESBAL": ("3000", "2900"), "TOTBKCR": ("17000", "16800")},
        )
        record = build([ANCHOR], providers)["records"][0]
        rows = {row["axis"]: row for row in record["candidate_normalized_result"]["axes"]}
        packet = full_us_packet(
            [
                row["returns"]["20_session_pct"]
                for row in record["five_axis"]["axes"]["TREND"]["measurement"]["trend_etfs"]
            ],
            record["five_axis"]["axes"]["RISK_VOL"]["measurement"]["value"],
            [
                row["change"]
                for row in record["five_axis"]["axes"]["LIQUIDITY"]["measurement"]["series"]
            ],
        )
        expected = self._built_rows(packet)
        for axis_name in ("TREND", "RISK_VOL", "LIQUIDITY"):
            self.assertEqual(rows[axis_name], expected[axis_name], axis_name)
        self.assertEqual(rows["TREND"]["direction"], "NEUTRAL")
        self.assertEqual(rows["RISK_VOL"]["direction"], "NEGATIVE")
        self.assertEqual(rows["LIQUIDITY"]["direction"], "NEGATIVE")

    def test_module_reuses_the_live_collector_and_candidate_rule(self):
        from regime import paper_regime_reference as live_prr
        self.assertIs(MODULE.PRR.classify, live_prr.classify)
        self.assertIs(MODULE.PRR.axis, live_prr.axis)
        self.assertTrue(callable(MODULE.FMD.fetch_alpaca_daily_bars))
        self.assertTrue(callable(MODULE.FMD._session_return))
        self.assertIn("Billions of Dollars", MODULE.FMD.FRED_LIQUIDITY_UNITS)


class UsFreeAxisPointInTimeTest(unittest.TestCase):
    def test_every_request_is_pinned_to_the_requested_date(self):
        providers = FakeProviders()
        build([ANCHOR], providers)
        alpaca = [q for path, q in providers.calls if "/v2/stocks/" in path]
        self.assertEqual(len(alpaca), len(TREND_SYMBOLS))
        for query in alpaca:
            self.assertTrue(query["end"][0].startswith(ANCHOR))
            self.assertLess(query["start"][0][:10], ANCHOR)
            self.assertEqual(query["feed"], ["iex"])
            self.assertEqual(query["adjustment"], ["raw"])
        fred = [q for path, q in providers.calls if path.startswith("/fred/")]
        self.assertTrue(fred)
        for query in fred:
            self.assertEqual(query["realtime_start"], [ANCHOR])
            self.assertEqual(query["realtime_end"], [ANCHOR])
            if "observation_end" in query:
                self.assertEqual(query["observation_end"], [ANCHOR])

    def test_no_source_date_is_ever_after_the_requested_date(self):
        for requested in ("2026-08-24", "2026-08-26", "2026-08-28", "2026-08-29"):
            record = build([requested])["records"][0]
            attestation = record["no_lookahead_attestation"]
            self.assertIs(attestation["any_source_date_after_requested_date"], False)
            self.assertIs(attestation["other_requested_dates_consulted"], False)
            self.assertEqual(attestation["fred_realtime_vintage_date"], requested)
            for date in (
                attestation["trend_session_date_range"]
                + attestation["liquidity_observation_dates"]
                + [attestation["vix_observation_date"]]
            ):
                self.assertLessEqual(date, requested)
            self.assertLessEqual(record["effective_session_date"], requested)

    def test_an_earlier_anchor_yields_an_earlier_effective_session(self):
        earlier = build(["2026-08-21"])["records"][0]
        later = build([ANCHOR])["records"][0]
        self.assertLess(earlier["effective_session_date"], later["effective_session_date"])

    def test_a_provider_bar_after_the_requested_date_fails_that_date_closed(self):
        record = build([ANCHOR], FakeProviders(leak_future_bar=True))["records"][0]
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        trend = record["five_axis"]["axes"]["TREND"]
        self.assertEqual(trend["status"], "NOT_COMPUTABLE")
        self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", trend["reason"])
        self.assertIsNone(trend["measurement"])

    def test_a_fred_observation_after_the_requested_date_fails_those_axes_closed(self):
        record = build([ANCHOR], FakeProviders(leak_future_observation=True))["records"][0]
        for name in ("RISK_VOL", "LIQUIDITY"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", entry["reason"])
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")

    def test_date_isolation_one_records_outcome_ignores_batch_membership(self):
        solo = build([ANCHOR])["records"][0]
        batched = build([ANCHOR, "2026-08-21", "not-a-date"])
        matched = next(r for r in batched["records"] if r["requested_date"] == ANCHOR)
        self.assertEqual(solo, matched)


class UsFreeAxisFailClosedTest(unittest.TestCase):
    def test_a_fred_outage_leaves_trend_observed_and_the_record_partial(self):
        record = build([ANCHOR], FakeProviders(fail_fred=True))["records"][0]
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        self.assertEqual(record["free_axis_coverage"]["observed_axes"], ["TREND"])
        self.assertEqual(
            record["free_axis_coverage"]["not_computable_axes"], ["RISK_VOL", "LIQUIDITY"],
        )
        self.assertEqual(record["five_axis"]["axes"]["TREND"]["status"], "OBSERVED")
        self.assertEqual(
            [row["axis"] for row in record["candidate_normalized_result"]["axes"]], ["TREND"],
        )
        self.assertEqual(
            record["candidate_normalized_result"]["paper_reference"]["candidate_regime"],
            "UNKNOWN",
        )

    def test_a_credential_gap_blocks_only_the_axis_that_needs_it(self):
        record = build(
            [ANCHOR], None, {"fred_key": FRED_KEY, "alpaca_key": "", "alpaca_secret": ""},
        )["records"][0]
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        self.assertEqual(
            record["five_axis"]["axes"]["TREND"]["reason"],
            "BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL",
        )
        self.assertEqual(record["five_axis"]["axes"]["RISK_VOL"]["status"], "OBSERVED")
        self.assertEqual(record["five_axis"]["axes"]["LIQUIDITY"]["status"], "OBSERVED")
        self.assertIsNone(record["effective_session_date"])

    def test_an_incomplete_credential_pair_is_reported_distinctly(self):
        record = build(
            [ANCHOR], None,
            {"fred_key": FRED_KEY, "alpaca_key": "only-key", "alpaca_secret": ""},
        )["records"][0]
        self.assertEqual(
            record["five_axis"]["axes"]["TREND"]["reason"],
            "BLOCKED_BY_INCOMPLETE_DEDICATED_MARKET_DATA_CREDENTIAL",
        )

    def test_every_source_failing_blocks_the_date_without_a_candidate_result(self):
        record = build(
            [ANCHOR], FakeProviders(fail_fred=True, fail_alpaca=True),
        )["records"][0]
        self.assertEqual(record["status"], "BLOCKED")
        self.assertEqual(record["failure_reason"], "ALL_FREE_AXES_NOT_COMPUTABLE")
        self.assertIsNone(record["candidate_normalized_result"])
        self.assertEqual(record["free_axis_coverage"]["ratio"], "0/3")
        self.assertEqual(
            record["five_axis"]["status"], "NOT_COMPUTABLE_NO_FREE_AXIS_OBSERVED",
        )

    def test_a_credential_never_reaches_a_recorded_failure_reason(self):
        population = build([ANCHOR], FakeProviders(fail_fred=True))
        serialized = MODULE.canonical_json(population)
        self.assertNotIn(FRED_KEY, serialized)
        self.assertNotIn(CREDENTIALS["alpaca_key"], serialized)
        self.assertNotIn(CREDENTIALS["alpaca_secret"], serialized)
        self.assertIn(
            "[REDACTED]", population["records"][0]["five_axis"]["axes"]["RISK_VOL"]["reason"],
        )

    def test_malformed_and_calendar_invalid_dates_fail_only_themselves(self):
        population = build([ANCHOR, "not-a-date", "2026-13-40", "2026-02-30"])
        by_date = {record["requested_date"]: record for record in population["records"]}
        self.assertEqual(by_date[ANCHOR]["status"], "FREE_AXES_OBSERVED")
        self.assertEqual(by_date["not-a-date"]["status"], "BLOCKED")
        self.assertIn("REQUESTED_DATE_FORMAT_INVALID", by_date["not-a-date"]["failure_reason"])
        for bad in ("2026-13-40", "2026-02-30"):
            self.assertEqual(by_date[bad]["status"], "BLOCKED")
            self.assertIn("REQUESTED_DATE_CALENDAR_INVALID", by_date[bad]["failure_reason"])
            self.assertIsNone(by_date[bad]["five_axis"])
            self.assertIsNone(by_date[bad]["candidate_normalized_result"])

    def test_an_unsupported_response_shape_degrades_to_one_axis_not_a_crash(self):
        def broken(url, headers=None):
            if urlparse(url).netloc == "data.alpaca.markets":
                return json.dumps({"bars": [{"t": None}]}).encode()
            return FakeProviders()(url, headers)

        record = build([ANCHOR], broken)["records"][0]
        trend = record["five_axis"]["axes"]["TREND"]
        self.assertEqual(trend["status"], "NOT_COMPUTABLE")
        self.assertIsNotNone(trend["reason"])
        self.assertEqual(record["five_axis"]["axes"]["RISK_VOL"]["status"], "OBSERVED")

    def test_build_population_requires_at_least_one_date(self):
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.build_population(CREDENTIALS, [], getter=FakeProviders())


class UsFreeAxisDeterminismTest(unittest.TestCase):
    def test_shuffled_input_produces_deterministic_ordering(self):
        forward = build([ANCHOR, "2026-08-21", "not-a-date"])
        shuffled = build(["not-a-date", ANCHOR, "2026-08-21"])
        self.assertEqual(MODULE.canonical_json(forward), MODULE.canonical_json(shuffled))
        self.assertEqual(
            [r["requested_date"] for r in forward["records"]],
            sorted(r["requested_date"] for r in forward["records"]),
        )

    def test_duplicate_requested_dates_collapse_to_one_record(self):
        population = build([ANCHOR, ANCHOR])
        self.assertEqual(population["requested_dates"], [ANCHOR])
        self.assertEqual(len(population["records"]), 1)

    def test_deterministic_rerun_is_byte_identical(self):
        first = build([ANCHOR, "2026-08-21"])
        second = build([ANCHOR, "2026-08-21"])
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))


class UsFreeAxisValidationTest(unittest.TestCase):
    def test_validate_population_accepts_its_own_output(self):
        population = build([ANCHOR])
        self.assertEqual(MODULE.validate_population(copy.deepcopy(population)), population)

    def test_validate_population_rejects_a_tampered_payload(self):
        population = build([ANCHOR])
        tampered = copy.deepcopy(population)
        tampered["records"][0]["status"] = "FREE_AXES_OBSERVED_FAKE"
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(tampered)

    def test_validate_population_rejects_a_flipped_authority_flag(self):
        population = build([ANCHOR])
        tampered = copy.deepcopy(population)
        tampered["authority"]["order_authorized"] = True
        tampered["payload_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(tampered)

    def test_validate_population_rejects_a_populated_breadth_or_leadership_axis(self):
        for name in ("BREADTH", "LEADERSHIP"):
            with self.subTest(axis=name):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["records"][0]["five_axis"]["axes"][name] = {
                    "status": "OBSERVED",
                    "reason": None,
                    "measurement": {"advance_fraction": "0.9"},
                }
                tampered["payload_sha256"] = MODULE.payload_sha256(
                    {k: v for k, v in tampered.items() if k != "payload_sha256"}
                )
                with self.assertRaises(MODULE.ReplayPopulationError):
                    MODULE.validate_population(tampered)

    def _resigned(self, population):
        population["payload_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in population.items() if k != "payload_sha256"}
        )
        return population

    def test_validate_population_rejects_omitted_records(self):
        # Adversarial: a re-signed payload is a valid hash over whatever it
        # contains, so dropping the records must fail rather than satisfy the
        # never-BREADTH guarantee by simply having no axes left to check.
        for records in ([], None):
            with self.subTest(records=records):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["records"] = records
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("POPULATION_RECORDS_NOT_BIJECTIVE", str(caught.exception))

    def test_validate_population_rejects_an_omitted_authority_boundary(self):
        for mutate in (
            lambda population: population["authority"].pop("us_breadth_authorized"),
            lambda population: population.__setitem__("authority", {}),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "POPULATION_AUTHORITY_SCHEMA_INVALID", str(caught.exception),
                )

    def test_validate_population_rejects_an_observed_record_without_its_axes(self):
        # Nulling the axis packet must not be a way past the excluded-axis rule.
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["five_axis"] = None
        tampered["records"][0]["candidate_normalized_result"] = None
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("REPLAYED_RECORD_MUST_CARRY_ITS_EVIDENCE", str(caught.exception))

    def test_validate_population_rejects_coverage_the_axes_do_not_support(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["free_axis_coverage"]["observed_count"] = 1
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("RECORD_COVERAGE_INCONSISTENT", str(caught.exception))

    def test_validate_population_rejects_a_status_the_coverage_contradicts(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["five_axis"]["axes"]["TREND"] = {
            "status": "NOT_COMPUTABLE", "reason": "X", "measurement": None,
        }
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("RECORD_COVERAGE_INCONSISTENT", str(caught.exception))

    def test_validate_population_rejects_a_record_that_looked_forward(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["no_lookahead_attestation"][
            "liquidity_observation_dates"
        ].append("2026-09-04")
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("RECORD_LOOKAHEAD_VIOLATION", str(caught.exception))

    def test_validate_population_rejects_a_classified_us_regime(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["candidate_normalized_result"]["paper_reference"][
            "candidate_regime"
        ] = "RISK_ON"
        tampered["payload_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in tampered.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(tampered)

    def test_validate_population_rejects_a_forged_candidate_axis_row(self):
        # Adversarial, and exactly the gap the UNKNOWN guarantees leave open:
        # the candidate regime, runtime regime, and classification status all
        # stay honest while the axis row underneath them is forged. Every hash
        # is recomputed, so only re-deriving the row from the measurement the
        # record itself stores can refuse it — and those rows are what every
        # downstream transition and stress fact is built from.
        for index, field, value in (
            (0, "direction", "FORGED_DIRECTION"),
            (0, "observed_value", {"positive": 3, "total": 3}),
            (1, "score", 1),
            (1, "summary_ko", "VIX는 조용합니다."),
            (2, "direction", "POSITIVE"),
        ):
            with self.subTest(index=index, field=field):
                tampered = copy.deepcopy(build([ANCHOR]))
                rows = tampered["records"][0]["candidate_normalized_result"]["axes"]
                if rows[index][field] == value:
                    self.skipTest("fixture already carries this value")
                rows[index][field] = value
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "RECORD_CANDIDATE_NOT_DERIVED_FROM_ITS_EVIDENCE",
                    str(caught.exception),
                )

    def test_validate_population_rejects_a_tampered_axis_measurement(self):
        # The mirror image: keep the derived row and move the measurement it was
        # derived from. Either side moving alone must fail closed.
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["five_axis"]["axes"]["RISK_VOL"]["measurement"][
            "value"
        ] = "99.0"
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "RECORD_CANDIDATE_NOT_DERIVED_FROM_ITS_EVIDENCE", str(caught.exception),
        )

    def test_validate_population_rejects_a_forged_effective_session_date(self):
        # The effective session date is otherwise a free-standing claim, and a
        # backdated one passes the lookahead check while mislabelling which
        # session every axis row came from.
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["effective_session_date"] = "2026-08-20"
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "EFFECTIVE_SESSION_DATE_NOT_DERIVED_FROM_ITS_EVIDENCE",
            str(caught.exception),
        )

    def test_validate_population_rejects_an_observed_axis_without_its_measurement(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["five_axis"]["axes"]["LIQUIDITY"]["measurement"] = None
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "OBSERVED_AXIS_EVIDENCE_NOT_NORMALIZABLE", str(caught.exception),
        )

    def test_validate_population_rejects_a_record_that_dropped_its_source_hashes(self):
        # The exact adversarial probe an integration review used: delete the
        # provider provenance and recompute the payload hash. Every measurement,
        # every re-derivable axis row, the UNKNOWN candidate regime, and every
        # signature stay genuine — so nothing but an explicit provenance
        # requirement can refuse it, and without one an observed TREND, RISK_VOL,
        # or LIQUIDITY value no longer says which response produced it.
        for mutate, code in (
            (lambda record: record.__setitem__("source_hashes", None),
             "OBSERVED_RECORD_MUST_CARRY_ITS_SOURCE_HASHES"),
            (lambda record: record.pop("source_hashes"),
             "OBSERVED_RECORD_MUST_CARRY_ITS_SOURCE_HASHES"),
            (lambda record: record["source_hashes"].pop("trend_response_sha256"),
             "RECORD_SOURCE_HASH_SCHEMA_INVALID"),
            (lambda record: record["source_hashes"].__setitem__(
                "trend_response_sha256", None),
             "RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS"),
            (lambda record: record["source_hashes"].__setitem__(
                "liquidity_response_hashes", None),
             "RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS"),
            (lambda record: record["source_hashes"]["liquidity_response_hashes"].pop(
                "WRESBAL"),
             "RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS"),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_source_hash_check_is_consistency_not_an_external_anchor(self):
        # Two-sided on purpose, including the side that is NOT caught, so the
        # module's claim and its behaviour cannot drift apart again.
        #
        # Side 1 — a one-sided re-point fails closed: the record-level hash no
        # longer equals the one inside the measurement it claims to attribute.
        one_sided = copy.deepcopy(build([ANCHOR]))
        one_sided["records"][0]["source_hashes"]["trend_response_sha256"] = "a" * 64
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(one_sided))
        self.assertIn(
            "RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS",
            str(caught.exception),
        )

        # Side 2 — the documented limit. Both compared values are mutable fields
        # of the same payload, so replacing BOTH copies with the same arbitrary
        # valid SHA-256 and re-signing IS accepted: no raw Alpaca/FRED response
        # is retained and neither provider signs one, so nothing here can tell
        # the two apart. Asserting acceptance keeps the docstring honest — making
        # this fail later requires a real external anchor, not a re-word.
        both_sides = copy.deepcopy(build([ANCHOR]))
        record = both_sides["records"][0]
        forged = "b" * 64
        record["five_axis"]["axes"]["TREND"]["measurement"]["response_sha256"] = forged
        record["source_hashes"]["trend_response_sha256"] = forged
        validated = MODULE.validate_population(self._resigned(both_sides))
        self.assertEqual(
            validated["records"][0]["source_hashes"]["trend_response_sha256"], forged,
        )
        # Acceptance means "the two copies agree", nothing more: the axis is
        # still OBSERVED and the coverage is still the honest partial 3/5.
        self.assertEqual(
            validated["records"][0]["five_axis"]["axes"]["TREND"]["status"], "OBSERVED",
        )

    def test_validate_population_rejects_re_pointed_source_hashes(self):
        # The mirror image of deletion: keep the provenance block's shape and
        # change what it points at. Each per-axis hash is compared with the
        # provenance inside that axis's own measurement, so a foreign or swapped
        # response hash cannot be re-signed into place on its own.
        for mutate in (
            lambda record: record["source_hashes"].__setitem__(
                "risk_vol_response_sha256", "0" * 64,
            ),
            lambda record: record["source_hashes"].__setitem__(
                "trend_response_sha256", record["source_hashes"]["risk_vol_response_sha256"],
            ),
            lambda record: record["source_hashes"]["liquidity_response_hashes"][
                "TOTBKCR"
            ].__setitem__("observations_response_sha256", "1" * 64),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS",
                    str(caught.exception),
                )

    def test_validate_population_rejects_an_observed_measurement_without_provenance(self):
        # Deleting the provenance on *both* sides at once must not cancel out:
        # a measurement that no longer carries its own response hash is not a
        # measurement this population can attribute, whatever the record's
        # source_hashes block then agrees with.
        for mutate, code in (
            (lambda record: record["five_axis"]["axes"]["TREND"]["measurement"].pop(
                "response_sha256"),
             "OBSERVED_AXIS_MUST_CARRY_ITS_SOURCE_HASHES"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"].pop(
                "response_hashes"),
             "OBSERVED_AXIS_MUST_CARRY_ITS_SOURCE_HASHES"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "response_hashes"]["TOTBKCR"].pop("metadata_response_sha256"),
             "OBSERVED_AXIS_SOURCE_HASH_SHAPE_INVALID"),
            (lambda record: record["five_axis"]["axes"]["RISK_VOL"]["measurement"].__setitem__(
                "response_sha256", "NOT-A-SHA-256"),
             "OBSERVED_AXIS_MUST_CARRY_ITS_SOURCE_HASHES"),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                record = tampered["records"][0]
                mutate(record)
                record["source_hashes"] = {
                    "trend_response_sha256": None,
                    "risk_vol_response_sha256": None,
                    "liquidity_response_hashes": None,
                }
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_population_rejects_provenance_on_a_date_that_observed_nothing(self):
        # A blocked date measured nothing, so it may not borrow a response hash
        # a reader could still treat as attribution.
        tampered = copy.deepcopy(
            build([ANCHOR], FakeProviders(fail_fred=True, fail_alpaca=True))
        )
        record = tampered["records"][0]
        self.assertEqual(record["status"], "BLOCKED")
        record["source_hashes"] = {
            "trend_response_sha256": "0" * 64,
            "risk_vol_response_sha256": None,
            "liquidity_response_hashes": None,
        }
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS",
            str(caught.exception),
        )

    def test_validate_population_requires_the_candidate_policy_it_pinned(self):
        # Re-derivation is only meaningful against the same policy the
        # population was built with, so a mismatched pin fails closed with its
        # own code instead of surfacing as a normalization mismatch.
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["candidate_policy"]["sha256"] = "0" * 64
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("CANDIDATE_POLICY_SHA_MISMATCH", str(caught.exception))

    def test_partial_coverage_that_would_classify_fails_closed(self):
        policy = MODULE._load_candidate_policy()
        rows = [
            PRR.axis("TREND", "POSITIVE", {}, "x"),
            PRR.axis("RISK_VOL", "POSITIVE", {}, "x"),
            PRR.axis("LIQUIDITY", "POSITIVE", {}, "x"),
        ]
        with mock.patch.object(PRR, "classify", return_value=("RISK_ON", 3, "x")):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE._candidate_normalized_result(rows, ANCHOR, policy, {"BREADTH": {}})


class UsFreeAxisOutputBoundaryTest(unittest.TestCase):
    def test_write_population_refuses_any_path_inside_the_checkout(self):
        population = build([ANCHOR])
        for forbidden in (
            ROOT / "data" / "latest_us_historical_replay_population.json",
            ROOT / "evidence" / "free_market_data" / "derived" / ANCHOR / "sneak.json",
            ROOT / "us_historical_replay_sneak.json",
        ):
            with self.subTest(path=str(forbidden)):
                with self.assertRaises(MODULE.ReplayPopulationError):
                    MODULE.write_population(population, forbidden, root=ROOT)
                self.assertFalse(forbidden.exists())

    def test_write_population_accepts_an_external_path(self):
        population = build([ANCHOR])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "outside_checkout" / "population.json"
            written = MODULE.write_population(population, out, root=ROOT)
            self.assertTrue(written.is_file())
            self.assertEqual(json.loads(written.read_text(encoding="utf-8")), population)

    def test_default_temp_out_is_never_inside_the_checkout(self):
        path = MODULE._default_temp_out()
        try:
            with self.assertRaises(ValueError):
                path.resolve().relative_to(ROOT.resolve())
        finally:
            path.unlink(missing_ok=True)

    def test_no_retained_or_live_us_source_mutation(self):
        derived = ROOT / "evidence" / "free_market_data" / "derived"
        latest = ROOT / "data" / "latest_free_market_data.json"

        def snapshot():
            found = {}
            if derived.is_dir():
                for entry in sorted(derived.iterdir()):
                    manifest = entry / "manifest.json"
                    if manifest.is_file():
                        found[entry.name] = MODULE.file_sha256(manifest)
            return found, (MODULE.file_sha256(latest) if latest.is_file() else None)

        before = snapshot()
        build([ANCHOR, "2026-08-21", "not-a-date"])
        self.assertEqual(before, snapshot())

    def test_module_never_reads_the_account_or_trading_alpaca_credential(self):
        code = [
            line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        ]
        joined = "\n".join(code)
        self.assertNotIn("ALPACA_API_KEY", joined)
        self.assertNotIn("ALPACA_API_SECRET", joined)
        self.assertIn("ALPACA_MARKET_DATA_API_KEY", joined)

    def test_credentials_from_env_reads_only_market_data_names(self):
        environment = {
            "FRED_API_KEY": " fred ",
            "ALPACA_MARKET_DATA_API_KEY": "data-key",
            "ALPACA_MARKET_DATA_API_SECRET": "data-secret",
            "ALPACA_API_KEY": "TRADING-KEY-MUST-NOT-BE-USED",
            "ALPACA_API_SECRET": "TRADING-SECRET-MUST-NOT-BE-USED",
        }
        with mock.patch.dict(MODULE.os.environ, environment, clear=True):
            credentials = MODULE._credentials_from_env()
        self.assertEqual(credentials, {
            "fred_key": "fred", "alpaca_key": "data-key", "alpaca_secret": "data-secret",
        })


if __name__ == "__main__":
    unittest.main()
