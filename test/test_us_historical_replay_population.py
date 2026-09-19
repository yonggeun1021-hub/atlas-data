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
        vintage_start_shift_days=0,
        vintage_end_shift_days=0,
        open_ended_vintage=False,
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
        # A real provider answers with the vintage it actually served, which need
        # not be the one the query pinned. These knobs let the response disagree
        # with the request the way a misbehaving or misconfigured ALFRED call
        # would, so the module's *bind* on the returned vintage is exercised
        # rather than only its query construction.
        self.vintage_start_shift_days = vintage_start_shift_days
        self.vintage_end_shift_days = vintage_end_shift_days
        self.open_ended_vintage = open_ended_vintage
        self.fail_fred = fail_fred
        self.fail_alpaca = fail_alpaca
        self.calls: list[tuple[str, dict]] = []

    def _vintage(self, query) -> tuple[str, str]:
        start = dt.date.fromisoformat(query["realtime_start"][0]) + dt.timedelta(
            days=self.vintage_start_shift_days
        )
        if self.open_ended_vintage:
            # Exactly what FRED serves while a value is still the current one.
            return start.isoformat(), "9999-12-31"
        end = dt.date.fromisoformat(query["realtime_end"][0]) + dt.timedelta(
            days=self.vintage_end_shift_days
        )
        return start.isoformat(), end.isoformat()

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
        realtime_start, realtime_end = self._vintage(query)
        return json.dumps({"seriess": [{
            "id": series_id,
            "title": f"{series_id} title",
            "frequency": "Weekly, Ending Wednesday",
            "units": "Billions of Dollars",
            "realtime_start": realtime_start,
            "realtime_end": realtime_end,
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
        realtime_start, realtime_end = self._vintage(query)
        rows = [
            {
                "date": (end - dt.timedelta(days=7 * (len(values) - 1 - offset))).isoformat(),
                "value": value,
                "realtime_start": realtime_start,
                "realtime_end": realtime_end,
            }
            for offset, value in enumerate(values)
        ]
        return json.dumps({"observations": rows}).encode()


def build(dates, providers=None, credentials=None):
    return MODULE.build_population(
        credentials or CREDENTIALS, dates, getter=providers or FakeProviders(),
    )


# A date-shaped string that is not a day: 2026 has no 31st of February. It also
# sorts *before* ``ANCHOR``, which is the whole reason a shape check plus a
# string comparison accepted it as backward-looking evidence.
IMPOSSIBLE_DATE = "2026-02-31"


def _with_observations(mutate):
    """A provider whose FRED observation rows are rewritten by ``mutate``.

    The rows keep their real values and hashes; only the dates a caller chooses
    are replaced, so what is under test is the module's date handling and not a
    malformed response in general.
    """
    providers = FakeProviders()
    original = providers._fred_observations

    def broken(query):
        body = json.loads(original(query))
        mutate(body["observations"])
        return json.dumps(body).encode()

    providers._fred_observations = broken
    return providers


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


# The full trend ∪ sector-reference symbol set `replay_breadth_leadership_source`
# fetches -- 15 symbols, matching config/free_market_data_contract.json exactly.
PROXY_SYMBOLS = sorted({
    "SPY", "QQQ", "IWM",
    "XLK", "XLF", "XLE", "XLI", "XLV", "XLY", "XLP", "XLB", "XLU", "XLRE", "XLC", "SMH",
})


def _leadership_slopes(positive_count: int) -> dict:
    """SPY flat; the first `positive_count` sector-reference symbols outperform
    it over the 20-session window, the rest underperform -- giving a known
    positive_groups count for leadership_axis_row's boundary to land on."""
    slopes = {"SPY": "0", "QQQ": "0", "IWM": "0"}
    sector_symbols = sorted({
        "XLK", "XLF", "XLE", "XLI", "XLV", "XLY", "XLP", "XLB", "XLU", "XLRE", "XLC", "SMH",
    })
    for index, symbol in enumerate(sector_symbols):
        slopes[symbol] = "1" if index < positive_count else "-1"
    return slopes


class UsBreadthLeadershipPreparedNotWiredTest(unittest.TestCase):
    """U1 (CIO US-DATA-1, 2026-09-14): BREADTH/LEADERSHIP arithmetic and its
    fetch are added and parity-tested, but not wired into the replay
    pipeline -- U2 (the ratification widening
    ``alpaca.current_proxy_axes.approval_status`` past
    ``RATIFIED_CURRENT_REFERENCE_ONLY``) is a separate CIO decision this
    module still has no authority to make on its own. Every guarantee
    ``UsFreeAxisReplayScopeTest`` already asserts (BREADTH/LEADERSHIP always
    UNKNOWN, 3/3 replay, 3/5 coverage) must therefore keep holding unchanged.
    """

    def setUp(self):
        self.policy = MODULE._load_candidate_policy()
        self.contract = FMD.load_contract(FMD.CONTRACT_PATH)

    def _built_rows(self, packet):
        return {row["axis"]: row for row in PRR.build_us(packet, self.policy)["axes"]}

    def test_not_wired_replayed_and_excluded_axes_unchanged(self):
        self.assertEqual(MODULE.REPLAYED_AXES, ["TREND", "RISK_VOL", "LIQUIDITY"])
        self.assertEqual(MODULE.EXCLUDED_AXES, ["BREADTH", "LEADERSHIP"])
        self.assertEqual(MODULE.PREPARED_NOT_WIRED_AXES, ["BREADTH", "LEADERSHIP"])

    def test_breadth_rows_match_build_us_at_every_threshold_boundary(self):
        for advance_fraction in (
            "0", "0.4499", "0.45", "0.4501", "0.5", "0.5499", "0.55", "0.5501", "1",
        ):
            with self.subTest(advance_fraction=advance_fraction):
                packet = full_us_packet(("1.5", "2.5", "3.5"), "17.5", ("1", "1"))
                packet["us_market_reference"]["proxy_axes"]["BREADTH"]["measurement"][
                    "advance_fraction"
                ] = advance_fraction
                self.assertEqual(
                    MODULE.breadth_axis_row(advance_fraction),
                    self._built_rows(packet)["BREADTH"],
                )

    def test_leadership_rows_match_build_us_for_every_positive_count(self):
        for positive_count in range(0, 13):
            with self.subTest(positive_count=positive_count):
                groups = [
                    {"return_pct": "1" if index < positive_count else "-1"}
                    for index in range(12)
                ]
                packet = full_us_packet(("1.5", "2.5", "3.5"), "17.5", ("1", "1"))
                packet["us_market_reference"]["proxy_axes"]["LEADERSHIP"]["measurement"][
                    "ordered_groups"
                ] = groups
                self.assertEqual(
                    MODULE.leadership_axis_row(groups),
                    self._built_rows(packet)["LEADERSHIP"],
                )

    def test_leadership_rejects_incomplete_group_coverage(self):
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.leadership_axis_row([{"return_pct": "1"}] * 11)

    def test_fetch_reuses_derive_us_market_reference_and_matches_build_us(self):
        slopes = _leadership_slopes(9)
        providers = FakeProviders(slopes=slopes)
        source = MODULE.replay_breadth_leadership_source(
            "KEY", "SECRET", dt.date.fromisoformat(ANCHOR),
            getter=providers, contract=self.contract,
        )
        self.assertEqual(source["symbols"], PROXY_SYMBOLS)
        self.assertEqual(source["requested_end_date"], ANCHOR)

        breadth_row = MODULE.breadth_axis_row(
            source["breadth_measurement"]["advance_fraction"]
        )
        leadership_row = MODULE.leadership_axis_row(
            source["leadership_measurement"]["ordered_groups"]
        )
        packet = full_us_packet(("1.5", "2.5", "3.5"), "17.5", ("1", "1"))
        packet["us_market_reference"]["proxy_axes"]["BREADTH"]["measurement"][
            "advance_fraction"
        ] = source["breadth_measurement"]["advance_fraction"]
        packet["us_market_reference"]["proxy_axes"]["LEADERSHIP"]["measurement"][
            "ordered_groups"
        ] = source["leadership_measurement"]["ordered_groups"]
        expected = self._built_rows(packet)
        self.assertEqual(breadth_row, expected["BREADTH"])
        self.assertEqual(leadership_row, expected["LEADERSHIP"])
        # 9 of 12 (0.75, above the 0.666667 positive_min) have a positive
        # 20-session return -> POSITIVE.
        self.assertEqual(leadership_row["direction"], "POSITIVE")

    def test_fetch_no_lookahead_rejects_a_future_dated_bar(self):
        providers = FakeProviders(slopes=_leadership_slopes(6), leak_future_bar=True)
        with self.assertRaisesRegex(MODULE.ReplayPopulationError, "US_REPLAY_LOOKAHEAD_VIOLATION"):
            MODULE.replay_breadth_leadership_source(
                "KEY", "SECRET", dt.date.fromisoformat(ANCHOR),
                getter=providers, contract=self.contract,
            )

    def test_fetch_pins_end_to_the_requested_date_for_every_symbol(self):
        providers = FakeProviders(slopes=_leadership_slopes(6))
        MODULE.replay_breadth_leadership_source(
            "KEY", "SECRET", dt.date.fromisoformat(ANCHOR),
            getter=providers, contract=self.contract,
        )
        alpaca = [q for path, q in providers.calls if "/v2/stocks/" in path]
        self.assertEqual(len(alpaca), len(PROXY_SYMBOLS))
        for query in alpaca:
            self.assertTrue(query["end"][0].startswith(ANCHOR))
            self.assertEqual(query["feed"], ["iex"])
            self.assertEqual(query["adjustment"], ["raw"])

    def test_fetch_blocked_without_credentials(self):
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.replay_breadth_leadership_source(
                "", "", dt.date.fromisoformat(ANCHOR),
                getter=FakeProviders(slopes=_leadership_slopes(6)), contract=self.contract,
            )

    def test_not_wired_replay_output_still_excludes_both_axes(self):
        """Calling build_population must not be affected by these new
        functions existing -- BREADTH/LEADERSHIP stay UNKNOWN end to end."""
        population = build([ANCHOR], FakeProviders(slopes=_leadership_slopes(6)))
        record = population["records"][0]
        for name in ("BREADTH", "LEADERSHIP"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "UNKNOWN", name)
            self.assertIsNone(entry["measurement"], name)


class _MissingSymbolProviders(FakeProviders):
    """Serves zero bars for one symbol, everything else as usual.

    Used to exercise ``US_BREADTH_NOT_OBSERVED`` end to end: the provider
    genuinely answered, it just never has data for one required symbol, the
    same shape a real gap in Alpaca's IEX coverage would take.
    """

    def __init__(self, missing_symbol: str, **kwargs):
        super().__init__(**kwargs)
        self.missing_symbol = missing_symbol

    def _alpaca(self, path, query) -> bytes:
        symbol = path.split("/")[3]
        if symbol == self.missing_symbol:
            return json.dumps({"bars": [], "symbol": symbol}).encode()
        return super()._alpaca(path, query)


class UsHistoricalPitReplayIdentityTest(unittest.TestCase):
    """CIO plan U2 (2026-09-13/14, decision record
    ``CIO-REGIME-PATH-AND-REDESIGN-START-20260914``): the hash-bound identity
    that widens this replay from 3-axis to 5-axis. It lives in its own file
    (``config/us_historical_pit_replay_identity_v1.json``), deliberately not
    a field inside ``config/free_market_data_contract.json`` -- that
    contract's exact bytes are pinned by
    ``config/regime_source_owner_registry_v2.json``
    (``regime/decision_authority.py``'s source-owner registry) as an
    unrelated governance anchor, so editing it at all would change its
    sha256 and break that pin. ``authority.us_breadth_authorized`` is never
    the gate -- these tests pin that it stays exactly ``False`` under every
    widened/narrowed identity below -- and the real on-disk identity file's
    own ``replay_population_wiring_activated`` stays ``False``, so
    ``UsFreeAxisReplayScopeTest``/``UsBreadthLeadershipPreparedNotWiredTest``
    above keep holding unchanged against it.
    """

    def setUp(self):
        self.contract = FMD.load_contract(FMD.CONTRACT_PATH)
        self.identity = MODULE._load_historical_pit_replay_identity()

    def _active_identity(self):
        active = copy.deepcopy(self.identity)
        active["replay_population_wiring_activated"] = True
        return active

    def test_real_identity_file_is_present_but_inactive_and_narrow(self):
        self.assertIsNotNone(self.identity)
        self.assertEqual(
            self.identity["status"], MODULE.RATIFIED_HISTORICAL_PIT_REPLAY_STATUS,
        )
        self.assertEqual(
            self.identity["decision_record"]["decision_id"],
            MODULE.RATIFIED_HISTORICAL_PIT_REPLAY_DECISION_ID,
        )
        self.assertEqual(
            self.identity["decision_record"]["sha256"],
            MODULE.RATIFIED_HISTORICAL_PIT_REPLAY_DECISION_SHA256,
        )
        self.assertIs(self.identity["replay_population_wiring_activated"], False)
        self.assertEqual(
            MODULE.authorized_axes(self.contract), ["TREND", "RISK_VOL", "LIQUIDITY"],
        )
        self.assertEqual(sorted(MODULE.exclusion_basis(self.contract)), ["BREADTH", "LEADERSHIP"])

    def test_activating_the_identity_widens_to_all_five_axes(self):
        with mock.patch.object(
            MODULE, "_load_historical_pit_replay_identity",
            return_value=self._active_identity(),
        ):
            self.assertEqual(MODULE.authorized_axes(self.contract), list(PRR.AXES))
            self.assertEqual(MODULE.exclusion_basis(self.contract), {})

    def test_a_wrong_decision_sha256_fails_closed(self):
        bad = self._active_identity()
        bad["decision_record"]["sha256"] = "0" * 64
        with mock.patch.object(MODULE, "_load_historical_pit_replay_identity", return_value=bad):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.authorized_axes(self.contract)

    def test_a_wrong_decision_id_fails_closed(self):
        bad = self._active_identity()
        bad["decision_record"]["decision_id"] = "SOME-OTHER-DECISION"
        with mock.patch.object(MODULE, "_load_historical_pit_replay_identity", return_value=bad):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.authorized_axes(self.contract)

    def test_a_wrong_status_string_fails_closed(self):
        bad = self._active_identity()
        bad["status"] = "SOMETHING_ELSE"
        with mock.patch.object(MODULE, "_load_historical_pit_replay_identity", return_value=bad):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.authorized_axes(self.contract)

    def test_a_non_boolean_activation_flag_fails_closed(self):
        bad = self._active_identity()
        bad["replay_population_wiring_activated"] = "true"
        with mock.patch.object(MODULE, "_load_historical_pit_replay_identity", return_value=bad):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.authorized_axes(self.contract)

    def test_a_missing_identity_file_is_treated_as_the_pre_u1_narrow_default(self):
        with mock.patch.object(
            MODULE, "_load_historical_pit_replay_identity", return_value=None,
        ):
            self.assertEqual(
                MODULE.authorized_axes(self.contract), ["TREND", "RISK_VOL", "LIQUIDITY"],
            )

    def _identity_file(self, content: bytes | None):
        directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        path = directory / "us_historical_pit_replay_identity_v1.json"
        if content is not None:
            path.write_bytes(content)
        self.enterContext(
            mock.patch.object(MODULE, "HISTORICAL_PIT_REPLAY_IDENTITY_PATH", path)
        )
        return path

    def test_a_truly_absent_identity_file_on_disk_is_the_narrow_default(self):
        self._identity_file(None)
        self.assertIsNone(MODULE._load_historical_pit_replay_identity())
        self.assertEqual(
            MODULE.authorized_axes(self.contract), ["TREND", "RISK_VOL", "LIQUIDITY"],
        )

    def test_a_present_but_malformed_identity_file_fails_closed_not_absent(self):
        # A truncated/garbled identity is a corrupted claim, not an absence:
        # silently re-narrowing scope would hide it.
        real = MODULE.HISTORICAL_PIT_REPLAY_IDENTITY_PATH.read_bytes()
        for label, content in (
            ("truncated_json", real[: len(real) // 2]),
            ("empty_file", b""),
            ("not_utf8", b"\xff\xfe{}"),
            ("json_array", b"[]"),
            ("json_null", b"null"),
        ):
            with self.subTest(label):
                with mock.patch.object(
                    MODULE, "HISTORICAL_PIT_REPLAY_IDENTITY_PATH",
                    Path(self.enterContext(tempfile.TemporaryDirectory())) / "identity.json",
                ) as path:
                    path.write_bytes(content)
                    with self.assertRaisesRegex(
                        MODULE.ReplayPopulationError, "HISTORICAL_PIT_REPLAY_IDENTITY_INVALID",
                    ):
                        MODULE._load_historical_pit_replay_identity()
                    with self.assertRaisesRegex(
                        MODULE.ReplayPopulationError, "HISTORICAL_PIT_REPLAY_IDENTITY_INVALID",
                    ):
                        MODULE.authorized_axes(self.contract)

    def test_the_real_identity_file_bytes_still_load_through_the_disk_path(self):
        path = self._identity_file(MODULE.HISTORICAL_PIT_REPLAY_IDENTITY_PATH.read_bytes())
        self.assertTrue(path.is_file())
        self.assertIs(
            MODULE._load_historical_pit_replay_identity()["replay_population_wiring_activated"],
            False,
        )
        self.assertEqual(
            MODULE.authorized_axes(self.contract), ["TREND", "RISK_VOL", "LIQUIDITY"],
        )

    def test_breadth_authorized_flipping_still_fails_closed_with_no_identity(self):
        # The original pre-U1 guarantee, preserved: us_breadth_authorized is
        # never itself the gate, so a contract that flips it without a
        # matching identity is still an unrecognized, fail-closed state.
        narrow_contract = copy.deepcopy(self.contract)
        narrow_contract["authority"]["us_breadth_authorized"] = True
        with mock.patch.object(
            MODULE, "_load_historical_pit_replay_identity", return_value=None,
        ):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.authorized_axes(narrow_contract)

    def test_breadth_authorized_flipping_still_fails_closed_when_activated(self):
        widened_contract = copy.deepcopy(self.contract)
        widened_contract["authority"]["us_breadth_authorized"] = True
        with mock.patch.object(
            MODULE, "_load_historical_pit_replay_identity",
            return_value=self._active_identity(),
        ):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.authorized_axes(widened_contract)


class UsWiredFiveAxisReplayTest(unittest.TestCase):
    """U1 wiring, stacked on U2's hash-bound identity mechanism: once the
    dedicated identity file activates, BREADTH/LEADERSHIP are actually
    attempted -- through the exact same ``replay_one_requested_date`` entry
    point ``build_population`` calls -- and a genuine 5/5 result is allowed
    to classify. The real on-disk identity file stays inactive (see
    ``UsHistoricalPitReplayIdentityTest``), so every test here patches
    ``MODULE._load_historical_pit_replay_identity`` to a widened+activated
    identity rather than touching any file on disk.
    """

    def setUp(self):
        self.policy = MODULE._load_candidate_policy()
        self.contract = FMD.load_contract(FMD.CONTRACT_PATH)
        identity = copy.deepcopy(MODULE._load_historical_pit_replay_identity())
        identity["replay_population_wiring_activated"] = True
        self.enterContext(
            mock.patch.object(
                MODULE, "_load_historical_pit_replay_identity", return_value=identity,
            )
        )
        self.replayed = MODULE.authorized_axes(self.contract)
        self.excluded = MODULE.exclusion_basis(self.contract)

    def _replay(self, providers, requested_date=ANCHOR, credentials=None):
        return MODULE.replay_one_requested_date(
            credentials or CREDENTIALS, requested_date, getter=providers,
            contract=self.contract, policy=self.policy, excluded=self.excluded,
            replayed=self.replayed,
        )

    def test_replayed_and_excluded_axes_are_five_and_empty(self):
        self.assertEqual(self.replayed, list(PRR.AXES))
        self.assertEqual(self.excluded, {})

    def test_full_five_axis_coverage_classifies_and_matches_prr_classify(self):
        slopes = _leadership_slopes(9)
        slopes.update({"SPY": "0.5", "QQQ": "0.5", "IWM": "0.5"})
        providers = FakeProviders(
            slopes=slopes, vix="10",
            liquidity={"WRESBAL": ("3000", "3100"), "TOTBKCR": ("17000", "17200")},
        )
        record = self._replay(providers)
        self.assertEqual(record["status"], "FREE_AXES_OBSERVED")
        self.assertEqual(record["five_axis"]["coverage"]["ratio"], "5/5")
        self.assertEqual(record["free_axis_coverage"]["ratio"], "5/5")
        for name in ("BREADTH", "LEADERSHIP"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "OBSERVED", name)
            self.assertIsNotNone(entry["measurement"], name)
            self.assertIn(name, record["free_axis_coverage"]["observed_axes"])

        candidate = record["candidate_normalized_result"]
        self.assertEqual([row["axis"] for row in candidate["axes"]], list(PRR.AXES))
        self.assertEqual(
            candidate["classification_status"], MODULE.CLASSIFICATION_STATUS_CLASSIFIED,
        )
        self.assertNotEqual(candidate["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertEqual(
            candidate["runtime_regime"], candidate["paper_reference"]["candidate_regime"],
        )
        self.assertIsNotNone(candidate["paper_reference"]["score"])
        self.assertEqual(candidate["candidate_rule_source"], "regime/paper_regime_reference.py::build_us")

        # Parity: the live, unmodified classifier over the exact same rows
        # (already in PRR.AXES order) must agree with what this module
        # published, score and explanation included.
        expected_regime, expected_score, expected_explanation = PRR.classify(
            candidate["axes"], self.policy,
        )
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], expected_regime)
        self.assertEqual(candidate["paper_reference"]["score"], expected_score)
        self.assertEqual(candidate["paper_reference"]["explanation_ko"], expected_explanation)
        expected_confidence = PRR.confidence(expected_regime, candidate["axes"])
        self.assertEqual(
            candidate["paper_reference"]["confidence"],
            None if expected_confidence is None else str(expected_confidence),
        )

    def test_missing_breadth_symbol_leaves_the_record_at_partial_coverage_and_unknown(self):
        providers = _MissingSymbolProviders("XLK", slopes=_leadership_slopes(6))
        record = self._replay(providers)
        # The unified fetch that failed to observe BREADTH also backs TREND
        # and LEADERSHIP, so all three go NOT_COMPUTABLE together -- FRED-only
        # RISK_VOL/LIQUIDITY are unaffected.
        for name in ("TREND", "BREADTH", "LEADERSHIP"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_BREADTH_NOT_OBSERVED", entry["reason"], name)
        for name in ("RISK_VOL", "LIQUIDITY"):
            self.assertEqual(record["five_axis"]["axes"][name]["status"], "OBSERVED", name)
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        self.assertEqual(record["free_axis_coverage"]["ratio"], "2/5")
        candidate = record["candidate_normalized_result"]
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertIsNone(candidate["paper_reference"]["score"])
        self.assertIsNone(candidate["paper_reference"]["confidence"])
        self.assertEqual(candidate["runtime_regime"], "UNKNOWN")
        self.assertEqual(candidate["classification_status"], MODULE.CLASSIFICATION_STATUS)
        self.assertEqual(candidate["coverage"]["ratio"], "2/5")

    def test_a_future_dated_bar_in_the_combined_fetch_leaves_trend_breadth_leadership_not_computable(
        self,
    ):
        providers = FakeProviders(slopes=_leadership_slopes(6), leak_future_bar=True)
        record = self._replay(providers)
        for name in ("TREND", "BREADTH", "LEADERSHIP"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", entry["reason"], name)
            self.assertIsNone(entry["measurement"], name)
        for name in ("RISK_VOL", "LIQUIDITY"):
            self.assertEqual(record["five_axis"]["axes"][name]["status"], "OBSERVED", name)
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        candidate = record["candidate_normalized_result"]
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertEqual(candidate["runtime_regime"], "UNKNOWN")

    def test_trend_breadth_leadership_share_one_response_hash_and_one_alpaca_fetch(self):
        providers = FakeProviders(slopes=_leadership_slopes(6))
        record = self._replay(providers)
        alpaca_calls = [q for path, q in providers.calls if "/v2/stocks/" in path]
        self.assertEqual(len(alpaca_calls), len(PROXY_SYMBOLS))
        hashes = record["source_hashes"]
        self.assertEqual(
            hashes["trend_response_sha256"], hashes["breadth_leadership_response_sha256"],
        )
        trend_measurement = record["five_axis"]["axes"]["TREND"]["measurement"]
        breadth_measurement = record["five_axis"]["axes"]["BREADTH"]["measurement"]
        self.assertEqual(
            trend_measurement["response_sha256"], breadth_measurement["response_sha256"],
        )
        self.assertEqual(
            trend_measurement["as_of_session_date"],
            breadth_measurement["breadth_measurement"]["as_of_session_date"],
        )
        self.assertEqual(
            trend_measurement["as_of_session_date"],
            record["five_axis"]["axes"]["LEADERSHIP"]["measurement"][
                "leadership_measurement"
            ]["as_of_session_date"],
        )

    def test_leadership_group_with_exactly_zero_percent_return_counts_as_not_positive(self):
        groups = [{"return_pct": "0"}] + [{"return_pct": "1"}] * 11
        row = MODULE.leadership_axis_row(groups)
        packet = full_us_packet(("1.5", "2.5", "3.5"), "17.5", ("1", "1"))
        packet["us_market_reference"]["proxy_axes"]["LEADERSHIP"]["measurement"][
            "ordered_groups"
        ] = groups
        expected = {r["axis"]: r for r in PRR.build_us(packet, self.policy)["axes"]}["LEADERSHIP"]
        self.assertEqual(row, expected)
        self.assertEqual(row["observed_value"]["positive_groups"], 11)

    def test_wired_candidate_result_validates_through_the_record_validators(self):
        # The dynamic ``replayed``/``excluded`` threading through the
        # validators (not just the builders) is exercised by re-validating a
        # genuinely 5/5-classified record with the same helpers
        # ``validate_population`` uses.
        slopes = _leadership_slopes(9)
        slopes.update({"SPY": "0.5", "QQQ": "0.5", "IWM": "0.5"})
        providers = FakeProviders(slopes=slopes, vix="10")
        record = self._replay(providers)
        MODULE._validate_record(
            record, ANCHOR, self.policy, self.excluded, self.replayed,
        )


# A real, committed Alpaca IEX daily-bars capture (not a FakeProviders
# fixture) and the real committed BREADTH/LEADERSHIP measurement it was used
# to produce, both already on ``main``. None of the synthetic fixtures above
# can catch a 20-session off-by-one, a relative-vs-absolute return mixup, or
# an accidental same-day-bar truncation, because they generate exactly the
# bars the module expects; replaying real committed provider bytes and
# comparing against the real committed output can.
REAL_EVIDENCE_ANCHOR = "2026-09-11"
# The content-addressed derived packet the 2026-09-11 capture published --
# never the rolling ``data/latest_free_market_data.json``, which the daily
# free-market capture overwrites and would silently change what this compares
# against. ``setUp`` re-verifies both the packet's own signature and that it is
# the packet derived from exactly the raw daily-bars file replayed here.
REAL_EVIDENCE_COMMITTED_PATH = (
    ROOT / "evidence" / "free_market_data" / "derived" / REAL_EVIDENCE_ANCHOR
    / "56c6b9829014ea66c02987b349ea3a3743267d9d73f73fdced6c93d4e802d01a"
    / "manifest.json"
)
REAL_EVIDENCE_PACKET_SHA256 = (
    "6cf3eeda4e856a56c0bc2ff3ad85dd5b250fb791c294c7a5c288918e0e5dfc71"
)


def _real_evidence_daily_raw_path():
    """Resolve the immutable daily-bars revision the committed packet pins.

    ``evidence/free_market_data/raw/<day>/alpaca_iex_daily_bars.json.gz`` is a
    latest-wins compatibility pointer that ``free_market_data.publish``
    overwrites in place on a same-UTC-date recapture, so reading it would pin
    this class's ``daily_raw_sha256`` assertion to bytes that can change
    without any regression. The packet names the revision it actually consumed
    in ``alpaca.daily_raw_evidence``, an ``APPEND_ONLY_CONTENT_ADDRESSED``
    object under ``evidence/free_market_data/raw/alpaca/daily_bars/<response
    sha256>/``; that pointer is what the collector's own ``resolve_daily_raw``
    reads, and it is the only address for these bytes a later capture cannot
    rebind. Returns ``None`` when the packet is absent from this checkout, so
    the skip guard below stays a checkout question rather than an import error.
    """
    if not REAL_EVIDENCE_COMMITTED_PATH.is_file():
        return None
    packet = json.loads(REAL_EVIDENCE_COMMITTED_PATH.read_text(encoding="utf-8"))
    pointer = (packet.get("alpaca") or {}).get("daily_raw_evidence") or {}
    raw_path = pointer.get("raw_path")
    if not isinstance(raw_path, str):
        return None
    return ROOT / raw_path


REAL_EVIDENCE_RAW_PATH = _real_evidence_daily_raw_path()


@unittest.skipUnless(
    REAL_EVIDENCE_RAW_PATH is not None and REAL_EVIDENCE_RAW_PATH.is_file(),
    "real committed US evidence fixtures not present in this checkout",
)
class UsRealEvidenceReplayFidelityTest(unittest.TestCase):
    """Replay a real committed Alpaca response and match the real committed
    output -- the one test class no synthetic ``FakeProviders`` fixture can
    substitute for.
    """

    def setUp(self):
        self.policy = MODULE._load_candidate_policy()
        self.contract = FMD.load_contract(FMD.CONTRACT_PATH)
        identity = copy.deepcopy(MODULE._load_historical_pit_replay_identity())
        identity["replay_population_wiring_activated"] = True
        self.enterContext(
            mock.patch.object(
                MODULE, "_load_historical_pit_replay_identity", return_value=identity,
            )
        )
        self.replayed = MODULE.authorized_axes(self.contract)
        self.excluded = MODULE.exclusion_basis(self.contract)
        self.committed = json.loads(
            REAL_EVIDENCE_COMMITTED_PATH.read_text(encoding="utf-8")
        )
        unsigned = {k: v for k, v in self.committed.items() if k != "packet_sha256"}
        self.assertEqual(self.committed["packet_sha256"], REAL_EVIDENCE_PACKET_SHA256)
        self.assertEqual(
            FMD.sha256_bytes(FMD.canonical_bytes(unsigned)), REAL_EVIDENCE_PACKET_SHA256,
        )
        # Replay the immutable revision this packet pinned, verified by the
        # collector's own reader, rather than the mutable per-day compatibility
        # pointer -- and keep the skip guard reading the same object setUp does.
        pointer = self.committed["alpaca"]["daily_raw_evidence"]
        self.assertEqual(ROOT / pointer["raw_path"], REAL_EVIDENCE_RAW_PATH)
        self.assertEqual(pointer["raw_retention"], FMD.ALPACA_RAW_RETENTION)
        raw = FMD.read_alpaca_raw_revision(ROOT, pointer)
        self.responses = json.loads(raw)["responses"]
        self.assertEqual(self.committed["alpaca"]["daily_raw_sha256"], FMD.sha256_bytes(raw))
        self.assertEqual(
            self.committed["us_market_reference"]["as_of_session_date"], REAL_EVIDENCE_ANCHOR,
        )
        # No committed real FRED capture is used here -- only the real
        # committed Alpaca bars are what this class is pinning. RISK_VOL/
        # LIQUIDITY are served by the same synthetic FakeProviders fixture
        # every other test in this file uses, so a full 5/5 record can be
        # assembled without depending on the FRED capture's own point-in-time
        # vintage matching this specific anchor date.
        self._fred_fallback = FakeProviders()

    def _real_evidence_provider(self, url, headers=None):
        # Serves exactly the bars the real committed capture contains for
        # each requested symbol -- no synthesis, no slicing by the query's
        # own start/end (the retained capture already covers ~180 days
        # ending on the anchor date, so the module's own point-in-time
        # filtering inside ``_grouped_sessions`` governs what is used).
        if urlparse(url).netloc != "data.alpaca.markets":
            return self._fred_fallback(url, headers)
        symbol = urlparse(url).path.split("/")[3]
        body = self.responses[symbol]
        return json.dumps({"bars": body["bars"], "symbol": symbol}).encode()

    def test_replay_reproduces_the_committed_breadth_and_leadership_measurements(self):
        record = MODULE.replay_one_requested_date(
            CREDENTIALS, REAL_EVIDENCE_ANCHOR, getter=self._real_evidence_provider,
            contract=self.contract, policy=self.policy, excluded=self.excluded,
            replayed=self.replayed,
        )
        self.assertEqual(record["status"], "FREE_AXES_OBSERVED")
        committed_proxy = self.committed["us_market_reference"]["proxy_axes"]

        breadth_measurement = record["five_axis"]["axes"]["BREADTH"]["measurement"][
            "breadth_measurement"
        ]
        leadership_measurement = record["five_axis"]["axes"]["LEADERSHIP"]["measurement"][
            "leadership_measurement"
        ]
        # Byte-exact against the real committed production measurement --
        # not merely "some plausible value".
        self.assertEqual(breadth_measurement, committed_proxy["BREADTH"]["measurement"])
        self.assertEqual(leadership_measurement, committed_proxy["LEADERSHIP"]["measurement"])

        # TREND's session-return arithmetic must also match the committed
        # values exactly, even though replay_trend_source's own richer shape
        # (previous_session_date/earliest_session_date/available_session_count)
        # legitimately differs from derive_us_market_reference's leaner
        # ``trend`` list shape -- both have always described the same bars.
        committed_trend = {
            row["symbol"]: row for row in self.committed["us_market_reference"]["trend_etfs"]
        }
        replayed_trend_etfs = record["five_axis"]["axes"]["TREND"]["measurement"]["trend_etfs"]
        for row in replayed_trend_etfs:
            committed_row = committed_trend[row["symbol"]]
            self.assertEqual(row["close"], committed_row["close"], row["symbol"])
            self.assertEqual(row["returns"], committed_row["returns"], row["symbol"])
            self.assertEqual(
                row["as_of_session_date"], committed_row["as_of_session_date"], row["symbol"],
            )

        # Session alignment, enforced structurally by the unified fetch, and
        # asserted explicitly: every date this record names equals the
        # anchor exactly -- trend_etfs, the combined fetch's own
        # reference_as_of_session_date, and every individual axis'
        # measurement date all agree.
        combined_measurement = record["five_axis"]["axes"]["TREND"]["measurement"]
        self.assertEqual(record["effective_session_date"], REAL_EVIDENCE_ANCHOR)
        self.assertEqual(combined_measurement["as_of_session_date"], REAL_EVIDENCE_ANCHOR)
        self.assertEqual(
            combined_measurement["reference_as_of_session_date"], REAL_EVIDENCE_ANCHOR,
        )
        for row in replayed_trend_etfs:
            self.assertEqual(row["as_of_session_date"], REAL_EVIDENCE_ANCHOR, row["symbol"])
        self.assertEqual(breadth_measurement["as_of_session_date"], REAL_EVIDENCE_ANCHOR)
        for group in leadership_measurement["ordered_groups"]:
            self.assertEqual(group["as_of_session_date"], REAL_EVIDENCE_ANCHOR, group["symbol"])

        # And the genuine candidate result is a real classification, not a
        # forced UNKNOWN, matching PRR.classify called directly.
        candidate = record["candidate_normalized_result"]
        expected_regime, expected_score, _ = PRR.classify(candidate["axes"], self.policy)
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], expected_regime)
        self.assertEqual(candidate["paper_reference"]["score"], expected_score)

    def _provider_dropping_last_bar(self, dropped_symbol):
        def provider(url, headers=None):
            if urlparse(url).netloc != "data.alpaca.markets":
                return self._fred_fallback(url, headers)
            symbol = urlparse(url).path.split("/")[3]
            bars = list(self.responses[symbol]["bars"])
            if symbol == dropped_symbol:
                bars = bars[:-1]
            return json.dumps({"bars": bars, "symbol": symbol}).encode()
        return provider

    def test_real_bars_with_smh_last_bar_dropped_fail_closed_in_the_builder(self):
        # SMH is a LEADERSHIP group but not a BREADTH symbol: before the
        # session-generation check this still produced a 5/5 OBSERVED record
        # whose LEADERSHIP mixed SMH's previous session into 2026-09-11.
        contract_proxy = self.contract["alpaca"]["current_proxy_axes"]
        self.assertIn("SMH", contract_proxy["leadership_symbols"])
        self.assertNotIn("SMH", contract_proxy["breadth_symbols"])
        record = MODULE.replay_one_requested_date(
            CREDENTIALS, REAL_EVIDENCE_ANCHOR,
            getter=self._provider_dropping_last_bar("SMH"),
            contract=self.contract, policy=self.policy, excluded=self.excluded,
            replayed=self.replayed,
        )
        for name in ("TREND", "BREADTH", "LEADERSHIP"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn(MODULE.PROXY_MIXED_SESSION_GENERATION, entry["reason"], name)
            self.assertIsNone(entry["measurement"], name)
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        candidate = record["candidate_normalized_result"]
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], "UNKNOWN")
        self.assertEqual(candidate["runtime_regime"], "UNKNOWN")
        # And the builder's own population is still a valid one.
        with mock.patch.object(FMD, "_get", side_effect=AssertionError("network")):
            population = MODULE.build_population(
                CREDENTIALS, [REAL_EVIDENCE_ANCHOR],
                getter=self._provider_dropping_last_bar("SMH"),
            )
        MODULE.validate_population(copy.deepcopy(population))

    def test_real_bars_with_smh_last_bar_dropped_fail_closed_in_the_validator(self):
        # The reviewer's exact probe: a mixed-generation record that reached
        # 5/5 OBSERVED (here produced by disabling only the builder-side
        # check) must be refused by validate_population, not accepted.
        with mock.patch.object(MODULE, "_assert_proxy_session_alignment"):
            population = MODULE.build_population(
                CREDENTIALS, [REAL_EVIDENCE_ANCHOR],
                getter=self._provider_dropping_last_bar("SMH"),
            )
        record = population["records"][0]
        self.assertEqual(record["status"], "FREE_AXES_OBSERVED")
        groups = record["five_axis"]["axes"]["LEADERSHIP"]["measurement"][
            "leadership_measurement"
        ]["ordered_groups"]
        smh = [row for row in groups if row["symbol"] == "SMH"][0]
        self.assertLess(smh["as_of_session_date"], REAL_EVIDENCE_ANCHOR)
        with self.assertRaisesRegex(
            MODULE.ReplayPopulationError, MODULE.PROXY_MIXED_SESSION_GENERATION,
        ):
            MODULE.validate_population(copy.deepcopy(population))
        with self.assertRaisesRegex(
            MODULE.ReplayPopulationError, MODULE.PROXY_MIXED_SESSION_GENERATION,
        ):
            MODULE._validate_record(
                record, REAL_EVIDENCE_ANCHOR, self.policy, self.excluded, self.replayed,
            )

    def test_the_validator_refuses_each_misaligned_session_field(self):
        population = MODULE.build_population(
            CREDENTIALS, [REAL_EVIDENCE_ANCHOR], getter=self._real_evidence_provider,
        )
        MODULE.validate_population(copy.deepcopy(population))
        earlier = "2026-09-10"

        def breadth_date(m):
            m["breadth_measurement"]["as_of_session_date"] = earlier

        def reference_date(m):
            m["reference_as_of_session_date"] = earlier

        def group_date(m):
            m["leadership_measurement"]["ordered_groups"][-1]["as_of_session_date"] = earlier

        def breadth_row_date(m):
            m["breadth_measurement"]["observations"][0]["as_of_session_date"] = earlier

        for label, mutate in (
            ("breadth", breadth_date), ("reference", reference_date),
            ("leadership_group", group_date), ("breadth_observation", breadth_row_date),
        ):
            with self.subTest(label):
                record = copy.deepcopy(population["records"][0])
                for name in ("BREADTH", "LEADERSHIP"):
                    mutate(record["five_axis"]["axes"][name]["measurement"])
                with self.assertRaisesRegex(
                    MODULE.ReplayPopulationError, MODULE.PROXY_MIXED_SESSION_GENERATION,
                ):
                    MODULE._validate_measurement_source_dates(
                        record["five_axis"]["axes"], list(PRR.AXES), REAL_EVIDENCE_ANCHOR,
                    )

    def _provider_keeping_last_bars(self, count):
        def provider(url, headers=None):
            if urlparse(url).netloc != "data.alpaca.markets":
                return self._fred_fallback(url, headers)
            symbol = urlparse(url).path.split("/")[3]
            bars = [
                bar for bar in self.responses[symbol]["bars"]
                if bar["t"][:10] <= REAL_EVIDENCE_ANCHOR
            ][-count:]
            self.assertEqual(len(bars), count, symbol)
            return json.dumps({"bars": bars, "symbol": symbol}).encode()
        return provider

    def test_sixty_one_real_bars_observe_and_sixty_are_not_computable(self):
        # The longest return window is 60 sessions, which needs 61 closes:
        # pinned on both sides of the boundary with the real committed bars.
        self.assertEqual(max(self.contract["alpaca"]["return_windows_sessions"]), 60)
        observed = MODULE.replay_one_requested_date(
            CREDENTIALS, REAL_EVIDENCE_ANCHOR, getter=self._provider_keeping_last_bars(61),
            contract=self.contract, policy=self.policy, excluded=self.excluded,
            replayed=self.replayed,
        )
        self.assertEqual(observed["status"], "FREE_AXES_OBSERVED")
        for name in PRR.AXES:
            self.assertEqual(observed["five_axis"]["axes"][name]["status"], "OBSERVED", name)
        for row in observed["five_axis"]["axes"]["TREND"]["measurement"]["trend_etfs"]:
            self.assertEqual(row["available_session_count"], 61, row["symbol"])
        MODULE._validate_record(
            observed, REAL_EVIDENCE_ANCHOR, self.policy, self.excluded, self.replayed,
        )

        short = MODULE.replay_one_requested_date(
            CREDENTIALS, REAL_EVIDENCE_ANCHOR, getter=self._provider_keeping_last_bars(60),
            contract=self.contract, policy=self.policy, excluded=self.excluded,
            replayed=self.replayed,
        )
        self.assertEqual(short["status"], "FREE_AXES_PARTIAL")
        for name in ("TREND", "BREADTH", "LEADERSHIP"):
            entry = short["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_TREND_HISTORY_INSUFFICIENT", entry["reason"], name)
        self.assertEqual(
            short["candidate_normalized_result"]["paper_reference"]["candidate_regime"],
            "UNKNOWN",
        )
        MODULE._validate_record(
            short, REAL_EVIDENCE_ANCHOR, self.policy, self.excluded, self.replayed,
        )

    def test_missing_symbol_from_a_real_evidence_style_fetch_still_fails_breadth_closed(self):
        # Same real bytes, minus one required breadth/sector symbol -- the
        # provider genuinely answered for everything else, exactly the shape
        # a real IEX coverage gap would take.
        def provider(url, headers=None):
            symbol = urlparse(url).path.split("/")[3]
            if symbol == "XLK":
                return json.dumps({"bars": [], "symbol": symbol}).encode()
            return self._real_evidence_provider(url, headers)

        record = MODULE.replay_one_requested_date(
            CREDENTIALS, REAL_EVIDENCE_ANCHOR, getter=provider,
            contract=self.contract, policy=self.policy, excluded=self.excluded,
            replayed=self.replayed,
        )
        for name in ("TREND", "BREADTH", "LEADERSHIP"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_BREADTH_NOT_OBSERVED", entry["reason"], name)
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")
        self.assertEqual(
            record["candidate_normalized_result"]["paper_reference"]["candidate_regime"],
            "UNKNOWN",
        )


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

    def test_a_future_vintage_fred_response_fails_those_axes_closed(self):
        # The exact adversarial probe an integration review used: the requested
        # date is 2026-08-28, every observation date is on or before it, and the
        # request pins realtime_start/realtime_end correctly — but the provider
        # answers with a 2026-09-01 ALFRED vintage, i.e. a revision published
        # after the replayed date. Pinning the query does not catch this; only
        # binding the *returned* vintage does, and without the bind the axis is
        # OBSERVED with an internally consistent measurement, row, and hash.
        providers = FakeProviders(vintage_start_shift_days=4, vintage_end_shift_days=4)
        record = build([ANCHOR], providers)["records"][0]
        for name in ("RISK_VOL", "LIQUIDITY"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", entry["reason"])
            self.assertIn("FRED_VINTAGE", entry["reason"])
            self.assertIsNone(entry["measurement"], name)
        # Contained to the axes that consumed it: TREND never touches FRED.
        self.assertEqual(record["five_axis"]["axes"]["TREND"]["status"], "OBSERVED")
        self.assertEqual(record["status"], "FREE_AXES_PARTIAL")

    def test_a_superseded_fred_vintage_is_refused_as_its_own_distinct_fact(self):
        # A vintage window that ended before the requested date is not a
        # lookahead — the value existed, it had simply already been replaced — so
        # it must not be reported as one, while still failing the axis closed:
        # it is not what that date could have been evaluated with either.
        record = build(
            [ANCHOR],
            FakeProviders(vintage_start_shift_days=-60, vintage_end_shift_days=-10),
        )["records"][0]
        for name in ("RISK_VOL", "LIQUIDITY"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn(
                "US_FRED_VINTAGE_SUPERSEDED_BEFORE_REQUESTED_DATE", entry["reason"],
            )
            self.assertNotIn("LOOKAHEAD", entry["reason"])

    def test_a_still_current_open_ended_fred_vintage_is_accepted(self):
        # The other side of the bind, asserted so it cannot harden into a
        # false-positive: FRED serves realtime_end = 9999-12-31 while a value is
        # still current, so the requirement is that the vintage window *contains*
        # the requested date, never that it equals it. Treating that sentinel as
        # lookahead would make every genuine current-vintage replay unusable.
        population = build([ANCHOR], FakeProviders(open_ended_vintage=True))
        record = population["records"][0]
        self.assertEqual(record["status"], "FREE_AXES_OBSERVED")
        risk_vol = record["five_axis"]["axes"]["RISK_VOL"]["measurement"]
        self.assertEqual(risk_vol["realtime_start"], ANCHOR)
        self.assertEqual(risk_vol["realtime_end"], "9999-12-31")
        MODULE.validate_population(population)

    def test_a_missing_fred_vintage_fails_those_axes_closed(self):
        # A response with no vintage at all cannot be bound to the requested
        # date, so it is refused rather than consumed as if it had been.
        providers = FakeProviders()
        original = providers._fred_observations

        def stripped(query):
            body = json.loads(original(query))
            for row in body["observations"]:
                row.pop("realtime_start")
            return json.dumps(body).encode()

        providers._fred_observations = stripped
        record = build([ANCHOR], providers)["records"][0]
        for name in ("RISK_VOL", "LIQUIDITY"):
            entry = record["five_axis"]["axes"][name]
            self.assertEqual(entry["status"], "NOT_COMPUTABLE", name)
            self.assertIn("US_FRED_VINTAGE_MISSING", entry["reason"])

    def test_the_recorded_vintage_is_the_one_the_provider_returned(self):
        # Recorded, not copied from the request: a measurement that echoed the
        # query would make the bind above unverifiable offline, because
        # validate_population would be re-checking the requested date against
        # itself rather than against what was served.
        record = build([ANCHOR], FakeProviders(vintage_end_shift_days=30))["records"][0]
        series = record["five_axis"]["axes"]["LIQUIDITY"]["measurement"]["series"]
        for row in series:
            self.assertEqual(row["realtime_start"], ANCHOR)
            self.assertEqual(row["previous_realtime_start"], ANCHOR)
            self.assertEqual(row["metadata_realtime_start"], ANCHOR)
            for key in ("realtime_end", "previous_realtime_end", "metadata_realtime_end"):
                self.assertGreater(row[key], ANCHOR, key)

    def test_a_provider_observation_date_that_is_no_calendar_day_fails_closed(self):
        # The defect an integration review found, at the source that produces it:
        # 2026-02-31 is DATE10-shaped, is a day no calendar has, and — because
        # ISO dates compare lexicographically — sorts *before* the requested
        # 2026-08-28. A shape check followed by a string comparison therefore
        # cleared it as an ordinary earlier observation and let it into the
        # population under a valid signature.
        latest = _with_observations(
            lambda rows: rows[-1].__setitem__("date", IMPOSSIBLE_DATE)
        )
        record = build([ANCHOR], latest)["records"][0]
        risk_vol = record["five_axis"]["axes"]["RISK_VOL"]
        liquidity = record["five_axis"]["axes"]["LIQUIDITY"]
        self.assertEqual(risk_vol["status"], "NOT_COMPUTABLE")
        self.assertIn("US_VIX_OBSERVATION_DATE_INVALID", risk_vol["reason"])
        self.assertIsNone(risk_vol["measurement"])
        self.assertEqual(liquidity["status"], "NOT_COMPUTABLE")
        self.assertIn("US_LIQUIDITY_OBSERVATION_DATE_INVALID", liquidity["reason"])
        # Contained to the axes that consumed it: TREND never touches FRED.
        self.assertEqual(record["five_axis"]["axes"]["TREND"]["status"], "OBSERVED")

        # The *previous* observation is consumed too — the liquidity change is a
        # difference of the two — so it is bound with the same rule rather than
        # only the latest row being parsed. RISK_VOL reads only the latest
        # observation, so it stays observed and the containment stays visible.
        previous = _with_observations(
            lambda rows: rows[-2].__setitem__("date", IMPOSSIBLE_DATE)
        )
        record = build([ANCHOR], previous)["records"][0]
        self.assertEqual(
            record["five_axis"]["axes"]["LIQUIDITY"]["status"], "NOT_COMPUTABLE",
        )
        self.assertIn(
            "US_LIQUIDITY_OBSERVATION_DATE_INVALID",
            record["five_axis"]["axes"]["LIQUIDITY"]["reason"],
        )
        self.assertEqual(record["five_axis"]["axes"]["RISK_VOL"]["status"], "OBSERVED")

    def test_a_vintage_bound_that_is_no_calendar_day_fails_closed(self):
        # Same defect on the ALFRED window. A vintage of 2026-02-31/2026-02-31
        # "contains" nothing, but under string comparison it opened before and
        # closed after the requested date's own ordering position, so the
        # containment bind passed over a window that cannot exist.
        for mutate, label in (
            (lambda rows: rows[-1].__setitem__("realtime_start", IMPOSSIBLE_DATE),
             "latest observation vintage"),
            (lambda rows: rows[-1].__setitem__("realtime_end", IMPOSSIBLE_DATE),
             "latest observation vintage end"),
            (lambda rows: rows[-2].__setitem__("realtime_start", IMPOSSIBLE_DATE),
             "previous observation vintage"),
        ):
            with self.subTest(bound=label):
                record = build([ANCHOR], _with_observations(mutate))["records"][0]
                self.assertEqual(
                    record["five_axis"]["axes"]["LIQUIDITY"]["status"], "NOT_COMPUTABLE",
                )
                self.assertIn(
                    "US_FRED_VINTAGE_MISSING",
                    record["five_axis"]["axes"]["LIQUIDITY"]["reason"],
                )

        # And on the series metadata, which fixes the units and therefore the
        # normalization factor the change is expressed in.
        providers = FakeProviders()
        original = providers._fred_metadata

        def broken(query):
            body = json.loads(original(query))
            body["seriess"][0]["realtime_start"] = IMPOSSIBLE_DATE
            return json.dumps(body).encode()

        providers._fred_metadata = broken
        record = build([ANCHOR], providers)["records"][0]
        self.assertEqual(
            record["five_axis"]["axes"]["LIQUIDITY"]["status"], "NOT_COMPUTABLE",
        )
        self.assertIn(
            "US_FRED_VINTAGE_MISSING", record["five_axis"]["axes"]["LIQUIDITY"]["reason"],
        )

    def test_an_alpaca_session_that_is_no_calendar_day_fails_closed(self):
        # The same defect on the session extraction: a bar stamped 2026-02-31
        # is not a session, and string-comparing it against the anchor admitted
        # it as an ordinary earlier bar whose close then entered the 20-session
        # return the TREND direction is derived from.
        def broken(url, headers=None):
            parsed = urlparse(url)
            if parsed.netloc != "data.alpaca.markets":
                return FakeProviders()(url, headers)
            body = json.loads(
                FakeProviders()._alpaca(parsed.path, parse_qs(parsed.query))
            )
            body["bars"][-1]["t"] = f"{IMPOSSIBLE_DATE}T00:00:00Z"
            return json.dumps(body).encode()

        record = build([ANCHOR], broken)["records"][0]
        trend = record["five_axis"]["axes"]["TREND"]
        self.assertEqual(trend["status"], "NOT_COMPUTABLE")
        self.assertIn("US_TREND_SESSION_DATE_INVALID", trend["reason"])
        self.assertIsNone(trend["measurement"])
        self.assertIsNone(record["effective_session_date"])
        # Contained: the FRED axes never saw the Alpaca response.
        self.assertEqual(record["five_axis"]["axes"]["RISK_VOL"]["status"], "OBSERVED")

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

    def test_validate_population_rejects_an_unpinned_source_contract(self):
        # Adversarial: re-signing the payload makes any digest self-consistent,
        # so the pinned contract must be re-read from disk rather than trusted —
        # otherwise the exclusion basis rests on a contract nothing checked.
        cases = {
            "sha256": ("SOURCE_CONTRACT_SHA_MISMATCH", "0" * 64),
            "path": ("POPULATION_SOURCE_CONTRACT_INVALID", "config/somewhere_else.json"),
            "contract_version": (
                "POPULATION_SOURCE_CONTRACT_INVALID", "free_market_data/99",
            ),
        }
        for key, (code, value) in cases.items():
            with self.subTest(field=key):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["source_contract"][key] = value
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))
        for missing in ({}, None):
            with self.subTest(source_contract=missing):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["source_contract"] = missing
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("POPULATION_SOURCE_CONTRACT_INVALID", str(caught.exception))

    def test_validate_population_rejects_a_rewritten_exclusion_basis(self):
        # The reason BREADTH/LEADERSHIP stay UNKNOWN is derived from the
        # contract, so rewriting it — even consistently at both the population
        # and record level — must fail rather than publish a ratification scope
        # the contract does not have.
        for mutate in (
            lambda excluded: excluded["BREADTH"]["basis"].__setitem__(
                "config/free_market_data_contract.json"
                "#alpaca.current_proxy_axes.approval_status",
                "RATIFIED_HISTORICAL_REPLAY",
            ),
            lambda excluded: excluded["LEADERSHIP"].__setitem__(
                "reason_code", "EXCLUDED_PENDING_REVIEW",
            ),
            lambda excluded: excluded["BREADTH"].__setitem__(
                "statement", "US BREADTH is excluded for unrelated reasons.",
            ),
            lambda excluded: excluded["LEADERSHIP"].__setitem__("status", "OBSERVED"),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["excluded_axes"])
                for record in tampered["records"]:
                    for name in ("BREADTH", "LEADERSHIP"):
                        record["five_axis"]["axes"][name]["reason"] = (
                            tampered["excluded_axes"][name]["reason_code"]
                        )
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "EXCLUDED_AXIS_BASIS_NOT_DERIVED_FROM_THE_PINNED_CONTRACT",
                    str(caught.exception),
                )

    def test_validate_population_rejects_a_dropped_or_renamed_excluded_axis(self):
        for excluded in ({}, None, {"BREADTH": {}, "MOMENTUM": {}}):
            with self.subTest(excluded_axes=excluded):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["excluded_axes"] = excluded
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("POPULATION_SCOPE_INVALID", str(caught.exception))

    def test_validate_population_rejects_a_record_excluded_axis_reason_swap(self):
        # A record may not keep the UNKNOWN status while attributing it to a
        # cause the population's own contract-derived basis does not state.
        for name in ("BREADTH", "LEADERSHIP"):
            for reason in ("EXCLUDED_PENDING_REVIEW", None):
                with self.subTest(axis=name, reason=reason):
                    tampered = copy.deepcopy(build([ANCHOR]))
                    tampered["records"][0]["five_axis"]["axes"][name]["reason"] = reason
                    with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                        MODULE.validate_population(self._resigned(tampered))
                    self.assertIn(
                        "EXCLUDED_AXIS_REASON_NOT_DERIVED_FROM_THE_PINNED_CONTRACT",
                        str(caught.exception),
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

    def test_validate_population_rejects_a_re_signed_future_fred_vintage(self):
        # The adversarial mirror of the build-time bind, and the gap that let a
        # future-vintage US replay be accepted: every observation date stays on
        # or before the requested date, so the attestation walk is satisfied, and
        # every axis row still re-derives from its measurement — only the ALFRED
        # vintage the measurement was served at is moved past the replayed date.
        # Each stored window is checked, because each one entered the result: the
        # latest observation, the previous observation the change is measured
        # against, and the metadata that fixes the units.
        future = "2026-09-01"
        for mutate in (
            lambda record: record["five_axis"]["axes"]["RISK_VOL"][
                "measurement"
            ].update({"realtime_start": future, "realtime_end": future}),
            lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"
            ][0].update({"realtime_start": future, "realtime_end": future}),
            lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"
            ][1].update({
                "previous_realtime_start": future, "previous_realtime_end": future,
            }),
            lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"
            ][0].update({
                "metadata_realtime_start": future, "metadata_realtime_end": future,
            }),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", str(caught.exception))
                self.assertIn("FRED_VINTAGE", str(caught.exception))

    def test_validate_population_rejects_a_dropped_or_superseded_fred_vintage(self):
        for mutate, code in (
            (lambda record: record["five_axis"]["axes"]["RISK_VOL"]["measurement"].pop(
                "realtime_end"),
             "US_FRED_VINTAGE_MISSING"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"][0].pop("metadata_realtime_start"),
             "US_FRED_VINTAGE_MISSING"),
            (lambda record: record["five_axis"]["axes"]["RISK_VOL"][
                "measurement"].__setitem__("realtime_end", "2026-08-01"),
             "US_FRED_VINTAGE_SUPERSEDED_BEFORE_REQUESTED_DATE"),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_population_rejects_a_re_signed_date_that_is_no_calendar_day(self):
        # The adversarial probe an integration review used, on the validator
        # side: every date below is DATE10-shaped, is a day no calendar has, and
        # sorts before the requested 2026-08-28. Under a shape check plus a
        # string comparison each one passed — the axis row still re-derived, the
        # coverage still agreed, the attestation still read as backward-looking,
        # and the payload was re-signed — so the population published a
        # point-in-time claim over a date that never existed.
        #
        # Every date a measurement carries is covered, because every one of them
        # entered the result: the observation the value came from, the previous
        # observation the change is measured against, the Alpaca session the
        # closes came from, the ALFRED windows, and the record's own attestation.
        for mutate, code in (
            (lambda record: record["five_axis"]["axes"]["RISK_VOL"][
                "measurement"].__setitem__("observation_date", IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"][0].__setitem__("observation_date", IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"][1].__setitem__("previous_observation_date", IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["five_axis"]["axes"]["TREND"]["measurement"][
                "trend_etfs"][0].__setitem__("previous_session_date", IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["five_axis"]["axes"]["TREND"][
                "measurement"].__setitem__("earliest_session_date", IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["no_lookahead_attestation"].__setitem__(
                "vix_observation_date", IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["no_lookahead_attestation"][
                "liquidity_observation_dates"].append(IMPOSSIBLE_DATE),
             "RECORD_SOURCE_DATE_CALENDAR_INVALID"),
            (lambda record: record["five_axis"]["axes"]["RISK_VOL"][
                "measurement"].__setitem__("realtime_start", IMPOSSIBLE_DATE),
             "US_FRED_VINTAGE_MISSING"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"][0].__setitem__("previous_realtime_end", IMPOSSIBLE_DATE),
             "US_FRED_VINTAGE_MISSING"),
            (lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"][1].__setitem__("metadata_realtime_start", IMPOSSIBLE_DATE),
             "US_FRED_VINTAGE_MISSING"),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_population_rejects_a_future_date_inside_a_measurement(self):
        # The real-date counterpart, and a gap of its own: the attestation walk
        # never reaches inside a measurement, so a re-signed record could keep a
        # clean attestation while the measurement it published named an
        # observation or session after the replayed date.
        future = "2026-09-04"
        for mutate in (
            lambda record: record["five_axis"]["axes"]["RISK_VOL"][
                "measurement"].__setitem__("observation_date", future),
            lambda record: record["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
                "series"][0].__setitem__("previous_observation_date", future),
            lambda record: record["five_axis"]["axes"]["TREND"]["measurement"][
                "trend_etfs"][1].__setitem__("previous_session_date", future),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", str(caught.exception))

    def test_the_open_ended_vintage_sentinel_survives_the_measurement_date_walk(self):
        # The other side of the walk above, asserted so it cannot harden into a
        # false positive: ``9999-12-31`` is later than every requested date and
        # is exactly what FRED serves while a value is still current. It is bound
        # as a containment window, never as a backward-looking source date, so a
        # genuine current-vintage replay must still validate.
        population = build([ANCHOR], FakeProviders(open_ended_vintage=True))
        risk_vol = population["records"][0]["five_axis"]["axes"]["RISK_VOL"]
        self.assertEqual(risk_vol["measurement"]["realtime_end"], "9999-12-31")
        self.assertEqual(
            MODULE.validate_population(copy.deepcopy(population)), population,
        )

    def test_validate_population_rejects_a_vintage_date_the_record_did_not_request(self):
        # ``vintage_date`` is the measurement's own claim about which ALFRED
        # vintage it was requested at; unbound, it can be re-signed to name any
        # date while the record is filed under another.
        for axis in ("RISK_VOL", "LIQUIDITY"):
            with self.subTest(axis=axis):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["records"][0]["five_axis"]["axes"][axis]["measurement"][
                    "vintage_date"
                ] = "2026-09-01"
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "FRED_VINTAGE_NOT_BOUND_TO_THE_REQUESTED_DATE", str(caught.exception),
                )

    def test_validate_population_rejects_a_tampered_pit_replay_declaration(self):
        # The declaration was carried unread, so a re-signed payload could assert
        # that it *had* used future dates — or delete the assertion entirely —
        # while every record-level check still passed. A valid signature over a
        # self-contradicting claim is exactly what a validator must refuse.
        for mutate, code in (
            (lambda population: population["pit_replay"].__setitem__(
                "future_dates_used_in_any_date_evaluation", True),
             "PIT_REPLAY_DECLARATION_INVALID"),
            (lambda population: population["pit_replay"].__setitem__(
                "fred_returned_vintage_bound_to_requested_date", False),
             "PIT_REPLAY_DECLARATION_INVALID"),
            (lambda population: population["pit_replay"].__setitem__(
                "close_adjustment", "split"),
             "PIT_REPLAY_DECLARATION_INVALID"),
            (lambda population: population["pit_replay"].pop(
                "future_dates_used_in_any_date_evaluation"),
             "PIT_REPLAY_SCHEMA_INVALID"),
            (lambda population: population["pit_replay"].__setitem__("extra", True),
             "PIT_REPLAY_SCHEMA_INVALID"),
            (lambda population: population.__setitem__("pit_replay", None),
             "PIT_REPLAY_SCHEMA_INVALID"),
            (lambda population: population.pop("pit_replay"),
             "PIT_REPLAY_SCHEMA_INVALID"),
            (lambda population: population["pit_replay"].__setitem__(
                "statement", "Point-in-time integrity was maintained."),
             "PIT_REPLAY_STATEMENT_INVALID"),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_the_published_pit_replay_block_is_the_one_that_is_validated(self):
        # Published and required from the same constants, so a field can never be
        # emitted without being checked or checked without being emitted.
        population = build([ANCHOR])
        self.assertEqual(population["pit_replay"], MODULE._pit_replay_block())
        self.assertIs(
            population["pit_replay"]["future_dates_used_in_any_date_evaluation"], False,
        )
        self.assertIs(
            population["pit_replay"]["fred_returned_vintage_bound_to_requested_date"],
            True,
        )
        MODULE.validate_population(population)

    def test_validate_population_requires_the_candidate_policy_it_pinned(self):
        # Re-derivation is only meaningful against the same policy the
        # population was built with, so a mismatched pin fails closed with its
        # own code instead of surfacing as a normalization mismatch.
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["candidate_policy"]["sha256"] = "0" * 64
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("CANDIDATE_POLICY_SHA_MISMATCH", str(caught.exception))

    def test_validate_population_rejects_a_forged_attempted_count(self):
        # The exact adversarial probe an integration review used: re-sign the
        # coverage denominator alone. Every axis, measurement, row, hash, and
        # digest stays genuine, so nothing but re-deriving the whole coverage
        # block can refuse a record reporting three observed axes out of zero
        # attempted — a replay that never ran and still produced evidence.
        for attempted in (0, 1, 99, None, "3"):
            with self.subTest(attempted_count=attempted):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["records"][0]["free_axis_coverage"][
                    "attempted_count"
                ] = attempted
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("RECORD_COVERAGE_INCONSISTENT", str(caught.exception))

    def test_validate_population_rejects_a_dropped_or_extended_coverage_block(self):
        for mutate in (
            lambda coverage: coverage.pop("attempted_count"),
            lambda coverage: coverage.pop("not_computable_axes"),
            lambda coverage: coverage.__setitem__("observed_fraction", "1.0"),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0]["free_axis_coverage"])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("RECORD_COVERAGE_INCONSISTENT", str(caught.exception))

    def test_validate_population_rejects_forged_five_axis_status_or_coverage(self):
        # The five-axis packet's own status and coverage are derived from the
        # same axes, and checking only the axis *set* left them free text: a
        # defined_count of 999 or a missing_axes list that quietly drops the
        # excluded pair both misreport the honest partial 3/5 coverage while the
        # axes underneath stay intact.
        derived = "RECORD_FIVE_AXIS_NOT_DERIVED_FROM_ITS_AXES"
        for mutate, code in (
            (lambda five_axis: five_axis["coverage"].__setitem__("defined_count", 999),
             derived),
            (lambda five_axis: five_axis["coverage"].__setitem__("ratio", "5/5"), derived),
            (lambda five_axis: five_axis["coverage"].__setitem__("missing_axes", []),
             derived),
            (lambda five_axis: five_axis["coverage"].__setitem__("required_count", 3),
             derived),
            (lambda five_axis: five_axis["coverage"].pop("defined_axes"), derived),
            (lambda five_axis: five_axis.__setitem__("status", "FIVE_AXES_OBSERVED"),
             derived),
            (lambda five_axis: five_axis.__setitem__("coverage_note", "looks fine"),
             "RECORD_FIVE_AXIS_INVALID"),
            (lambda five_axis: five_axis.pop("coverage"), "RECORD_FIVE_AXIS_INVALID"),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0]["five_axis"])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_population_rejects_a_five_axis_status_observed_when_nothing_was(self):
        # The status names what the packet holds, so a date on which every free
        # axis failed may not publish an "observed" packet.
        tampered = copy.deepcopy(
            build([ANCHOR], FakeProviders(fail_fred=True, fail_alpaca=True))
        )
        self.assertEqual(tampered["records"][0]["status"], "BLOCKED")
        tampered["records"][0]["five_axis"]["status"] = (
            "OBSERVED_UNCLASSIFIED_FREE_AXES_ONLY"
        )
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "RECORD_FIVE_AXIS_NOT_DERIVED_FROM_ITS_AXES", str(caught.exception),
        )

    def test_validate_population_rejects_a_fabricated_failure_on_an_observed_record(self):
        # A record whose axes all survived has no failure to report. Left
        # unchecked the field is free text under a valid signature, and a reader
        # would take it as the cause of a failure that never happened.
        for forged in ("FABRICATED_FAILURE", "", "ALL_FREE_AXES_NOT_COMPUTABLE"):
            with self.subTest(failure_reason=forged):
                tampered = copy.deepcopy(build([ANCHOR]))
                self.assertEqual(tampered["records"][0]["status"], "FREE_AXES_OBSERVED")
                tampered["records"][0]["failure_reason"] = forged
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "REPLAYED_RECORD_MUST_NOT_CARRY_A_FAILURE", str(caught.exception),
                )

    def test_validate_population_rejects_an_unattributed_blocked_record(self):
        # The mirror image: a date that observed nothing may not drop its reason
        # and become a BLOCKED record with no recorded cause.
        for forged in (None, "", "NOT_THE_RECORD_LEVEL_CODE"):
            with self.subTest(failure_reason=forged):
                tampered = copy.deepcopy(
                    build([ANCHOR], FakeProviders(fail_fred=True, fail_alpaca=True))
                )
                self.assertEqual(tampered["records"][0]["status"], "BLOCKED")
                tampered["records"][0]["failure_reason"] = forged
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("BLOCKED_RECORD_MUST_BE_ATTRIBUTED", str(caught.exception))

    def test_validate_population_rejects_a_contradictory_axis_entry_shape(self):
        # An OBSERVED axis that also carries a failure reason, or a
        # NOT_COMPUTABLE one that keeps a measurement, describes two outcomes at
        # once — and every derived field built from "the observed axes" would
        # silently follow only one of them.
        cases = (
            (
                lambda axes: axes["TREND"].__setitem__("reason", "BLOCKED_BY_CREDENTIAL"),
                "OBSERVED_AXIS_MUST_NOT_CARRY_A_REASON",
            ),
            (
                lambda axes: axes.__setitem__("RISK_VOL", {
                    "status": "NOT_COMPUTABLE",
                    "reason": "X",
                    "measurement": copy.deepcopy(axes["RISK_VOL"]["measurement"]),
                }),
                "NOT_COMPUTABLE_AXIS_MUST_NOT_CARRY_A_MEASUREMENT",
            ),
            (
                lambda axes: axes.__setitem__("LIQUIDITY", {
                    "status": "NOT_COMPUTABLE", "reason": None, "measurement": None,
                }),
                "NOT_COMPUTABLE_AXIS_MUST_BE_ATTRIBUTED",
            ),
            (
                lambda axes: axes["TREND"].pop("reason"),
                "REPLAYED_AXIS_STATUS_INVALID",
            ),
            (
                lambda axes: axes["BREADTH"].__setitem__("note", "n/a"),
                "EXCLUDED_AXIS_MUST_STAY_UNKNOWN",
            ),
        )
        for mutate, code in cases:
            with self.subTest(code=code):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0]["five_axis"]["axes"])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_population_rejects_a_shortened_source_date_attestation(self):
        # ``_validate_no_lookahead`` bounds the dates the attestation *names*,
        # not the list itself, so dropping the sources a record consulted left it
        # publishing a "no lookahead" claim over evidence it no longer named.
        for mutate in (
            lambda attestation: attestation.__setitem__("liquidity_observation_dates", []),
            lambda attestation: attestation.__setitem__("vix_observation_date", None),
            lambda attestation: attestation.__setitem__("trend_session_date_range", []),
            lambda attestation: attestation.__setitem__(
                "fred_realtime_vintage_date", None,
            ),
            lambda attestation: attestation.pop("liquidity_observation_dates"),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0]["no_lookahead_attestation"])
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "RECORD_ATTESTATION_NOT_DERIVED_FROM_ITS_EVIDENCE",
                    str(caught.exception),
                )

    def test_validate_population_rejects_a_dropped_disclosed_warning(self):
        # The unadjusted-close convention is a disclosed limitation a reader
        # relies on, exactly like ``close_adjustment`` in the PIT block.
        for warnings in (
            [],
            None,
            ["SHADOW_HISTORICAL_BACKFILL_NOT_NATURAL_OBSERVATION"],
        ):
            with self.subTest(warnings=warnings):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["records"][0]["warnings"] = warnings
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("RECORD_WARNINGS_INVALID", str(caught.exception))

    def test_validate_population_rejects_an_effective_session_date_without_a_packet(self):
        # A date that produced no axis packet has no TREND measurement to bind
        # the effective session date to, so it may not carry one.
        tampered = copy.deepcopy(build(["not-a-date"]))
        self.assertIsNone(tampered["records"][0]["five_axis"])
        tampered["records"][0]["effective_session_date"] = "2026-08-27"
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "EFFECTIVE_SESSION_DATE_NOT_DERIVED_FROM_ITS_EVIDENCE",
            str(caught.exception),
        )

    def test_the_published_record_shape_is_the_one_that_is_validated(self):
        # Published and re-required from the same helpers, so a derived field can
        # never be emitted without being checked or checked without being
        # emitted.
        record = build([ANCHOR])["records"][0]
        self.assertEqual(
            record["free_axis_coverage"],
            MODULE._free_axis_coverage(
                ["TREND", "RISK_VOL", "LIQUIDITY"], [], 3,
                ["TREND", "RISK_VOL", "LIQUIDITY"],
            ),
        )
        self.assertEqual(
            record["five_axis"],
            MODULE._five_axis_block(
                ["TREND", "RISK_VOL", "LIQUIDITY"], [], record["five_axis"]["axes"],
                ["BREADTH", "LEADERSHIP"],
            ),
        )
        self.assertEqual(record["free_axis_coverage"]["attempted_count"], 3)
        self.assertIsNone(record["failure_reason"])

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


EVIDENCE_SCRIPT = ROOT / "regime" / "us_replay_evidence_source.py"
EVIDENCE_SPEC = importlib.util.spec_from_file_location(
    "us_replay_evidence_source_tested", EVIDENCE_SCRIPT,
)
EVIDENCE = importlib.util.module_from_spec(EVIDENCE_SPEC)
assert EVIDENCE_SPEC.loader is not None
EVIDENCE_SPEC.loader.exec_module(EVIDENCE)
HIST = EVIDENCE.HIST


class UsCommittedHistoryStoreReplayTest(unittest.TestCase):
    """Replay the *committed* history store, not a fixture.

    ``evidence/free_market_data/history/`` has been in this repository since the
    US-DATA-1 capture and nothing read it: the replay issued live Alpaca/FRED
    requests for every date, so the committed capture could not produce a single
    replayed axis. These tests bind the read path to that store as it actually
    stands -- its own bars, its own ALFRED vintage rows, its own content
    addresses -- so a store that grows, shrinks, or is edited in place changes
    the outcome here rather than silently changing a score.
    """

    @classmethod
    def setUpClass(cls):
        cls.contract = FMD.load_contract(FMD.CONTRACT_PATH)
        try:
            cls.store = EVIDENCE.EvidenceStore(ROOT, cls.contract)
        except EVIDENCE.ReplayEvidenceSourceError as exc:  # pragma: no cover
            raise unittest.SkipTest(f"committed history store unusable: {exc}")

    def _getter(self):
        return EVIDENCE.EvidenceGetter(self.store)

    # -- the derived window -------------------------------------------------

    def test_lead_sessions_required_is_derived_and_is_sixty_one_not_twenty_one(self):
        """The replay needs 61 bars of lead, and the number is derived, not typed.

        ``derive_us_market_reference`` computes every configured return window
        and ``_session_return(closes, n)`` needs ``n + 1`` closes, so the binding
        requirement is the longest configured window plus one. The capture
        receipts' own ``feasibility`` blocks report 21 because
        ``free_market_data_history.LEAD_BARS_REQUIRED`` describes a 20-session
        scoring window; assuming that number here would quietly leave the first
        forty intersection sessions PARTIAL.
        """
        windows = list(self.contract["alpaca"]["return_windows_sessions"])
        self.assertEqual(self.store.lead_sessions_required, max(windows) + 1)
        self.assertEqual(self.store.lead_sessions_required, 61)
        self.assertNotEqual(self.store.lead_sessions_required, HIST.LEAD_BARS_REQUIRED)
        sessions = self.store.session_intersection()
        scoreable = self.store.scoreable_sessions()
        # Derived from the intersection, so more landed data means more
        # scoreable dates and less means fewer -- never a hardcoded window.
        self.assertEqual(len(scoreable), len(sessions) - (max(windows) + 1) + 1)
        self.assertEqual(scoreable[0], sessions[max(windows)])

    def test_intersection_is_every_replay_symbol_and_the_first_scoreable_date_is_replayable(self):
        sessions = self.store.session_intersection()
        self.assertTrue(sessions)
        symbols = sorted(
            set(self.contract["alpaca"]["trend_symbols"])
            | set(self.contract["alpaca"]["sector_reference_symbols"])
        )
        self.assertEqual(self.store.symbols, symbols)
        for symbol in symbols:
            held = {row["session_date"] for row in self.store.bars[symbol]}
            self.assertTrue(set(sessions) <= held, symbol)
        # The very first scoreable session is exactly the one the 61-bar lead
        # makes reachable, and it really does replay -- the boundary is the case
        # a lead-by-one error hides in.
        first = self.store.scoreable_sessions()[0]
        identity = copy.deepcopy(MODULE._load_historical_pit_replay_identity())
        identity["replay_population_wiring_activated"] = True
        with mock.patch.object(
            MODULE, "_load_historical_pit_replay_identity", return_value=identity,
        ):
            record = MODULE.replay_one_requested_date(
                dict(EVIDENCE.EVIDENCE_CREDENTIALS), first, getter=self._getter(),
                contract=self.contract, policy=MODULE._load_candidate_policy(),
                excluded=MODULE.exclusion_basis(self.contract),
                replayed=MODULE.authorized_axes(self.contract),
                source_mode=MODULE.SOURCE_MODE_EVIDENCE,
                units_scale=self.store.units_scale(),
            )
        self.assertEqual(record["status"], MODULE.STATUS_OBSERVED, record.get("failure_reason"))
        self.assertEqual(record["effective_session_date"], first)

    # -- the no-future-information guarantee --------------------------------

    def test_a_later_revision_of_a_revised_series_never_reaches_an_earlier_date(self):
        """The load-bearing point-in-time claim, asserted on the real store.

        TOTBKCR is revised repeatedly, so the committed availability rows hold
        several published values for the same observation date, each with its own
        window. What this asserts is that replaying an early date serves the
        value that was current *then* and that the later revision of that same
        observation is genuinely absent -- not merely that dates were filtered.
        Reading the latest revision instead would leave every hash, axis row and
        signature internally consistent while silently turning the replay into
        hindsight.
        """
        rows = self.store.fred_rows["TOTBKCR"]
        revised = {}
        for row in rows:
            revised.setdefault(row["observation_date"], []).append(row)
        multi = sorted(
            date for date, group in revised.items()
            if len({row["value"] for row in group}) > 1
        )
        self.assertTrue(multi, "the committed store holds no revised TOTBKCR observation")
        observation_date = multi[0]
        group = sorted(revised[observation_date], key=lambda row: row["available_from"])
        first_vintage, latest_vintage = group[0], group[-1]
        self.assertNotEqual(first_vintage["value"], latest_vintage["value"])

        as_of = first_vintage["available_from"]
        visible = HIST.observations_available_at(rows, as_of)
        served = {row["observation_date"]: row for row in visible}
        self.assertIn(observation_date, served)
        self.assertEqual(served[observation_date]["value"], first_vintage["value"])
        self.assertNotEqual(served[observation_date]["value"], latest_vintage["value"])

        # And the same holds of what the getter actually answers with, including
        # each row's own publication window containing the as-of.
        body = json.loads(self._getter()(
            "https://api.stlouisfed.org/fred/series/observations?"
            + MODULE._fred_query("TOTBKCR", "KEY", dt.date.fromisoformat(as_of), 180)
        ))
        answered = {row["date"]: row for row in body["observations"]}
        self.assertEqual(answered[observation_date]["value"], first_vintage["value"])
        for row in body["observations"]:
            self.assertLessEqual(row["realtime_start"], as_of, row["date"])
            self.assertGreaterEqual(row["realtime_end"], as_of, row["date"])
            self.assertLessEqual(row["date"], as_of)

    def test_every_observation_the_store_answers_was_already_published(self):
        """No served row may open after the replayed date, on any series."""
        for series_id in self.store.fred_series:
            rows = self.store.fred_rows[series_id]
            vintages = sorted({row["available_from"] for row in rows})
            as_of = vintages[len(vintages) // 2]
            for row in HIST.observations_available_at(rows, as_of):
                self.assertLessEqual(row["available_from"], as_of, series_id)
                self.assertGreaterEqual(row["available_to"], as_of, series_id)
                self.assertLessEqual(row["observation_date"], as_of, series_id)

    def test_bars_answered_are_bounded_by_the_requested_window(self):
        anchor = self.store.scoreable_sessions()[len(self.store.scoreable_sessions()) // 2]
        end = dt.datetime.combine(
            dt.date.fromisoformat(anchor), dt.time(23, 59, 59), tzinfo=MODULE.UTC,
        )
        raw, normalized = FMD.fetch_alpaca_daily_bars(
            "KEY", "SECRET", ["SPY"], end, getter=self._getter(),
        )
        self.assertTrue(normalized)
        for row in normalized:
            self.assertLessEqual(str(row["opened_at"])[:10], anchor)
        # Closes travel as the committed decimal text, not through a float.
        committed = {
            row["session_date"]: row["bar"]["close"] for row in self.store.bars["SPY"]
        }
        for row in normalized:
            self.assertEqual(row["close"], committed[str(row["opened_at"])[:10]])
        self.assertIsInstance(raw, bytes)

    def test_the_getter_never_falls_back_to_the_network(self):
        for url in (
            "https://example.invalid/anything",
            "https://api.stlouisfed.org/fred/series/vintagedates?series_id=VIXCLS",
            "https://data.alpaca.markets/v2/stocks/NOPE/bars?feed=iex",
        ):
            with self.assertRaises(EVIDENCE.ReplayEvidenceSourceError):
                self._getter()(url, {})

    def test_a_fred_request_that_does_not_pin_one_vintage_day_is_refused(self):
        with self.assertRaises(EVIDENCE.ReplayEvidenceSourceError):
            self._getter()(
                "https://api.stlouisfed.org/fred/series/observations"
                "?series_id=VIXCLS&observation_start=2024-01-01&observation_end=2024-06-03"
                "&realtime_start=2024-01-01&realtime_end=2024-06-03"
            )

    # -- what the population records ---------------------------------------

    def _evidence_population(self, dates):
        return MODULE.build_population(
            dict(EVIDENCE.EVIDENCE_CREDENTIALS), dates, getter=self._getter(),
            source_mode=MODULE.SOURCE_MODE_EVIDENCE,
            source_store=self.store.descriptor(),
        )

    def test_evidence_mode_is_recorded_declared_and_validated(self):
        dates = self.store.scoreable_sessions()[-2:]
        population = self._evidence_population(dates)
        MODULE.validate_population(population)
        self.assertEqual(
            population["pit_source"]["mode"], MODULE.SOURCE_MODE_EVIDENCE,
        )
        self.assertFalse(population["pit_source"]["units_vintage_available"])
        self.assertEqual(
            population["pit_source"]["store"]["path"], EVIDENCE.HISTORY_STORE_REL,
        )
        self.assertTrue(population["pit_source"]["store"]["read_only"])
        for record in population["records"]:
            self.assertEqual(record["warnings"], MODULE.EVIDENCE_RECORD_WARNINGS)
            self.assertIn(
                "SOURCES_READ_FROM_COMMITTED_EVIDENCE_STORE_NOT_LIVE_PROVIDER",
                record["warnings"],
            )

    def test_an_evidence_population_cannot_be_re_signed_as_a_live_provider_one(self):
        """The weaker units guarantee cannot be laundered into the stronger one."""
        population = self._evidence_population(self.store.scoreable_sessions()[-1:])
        forged = copy.deepcopy(population)
        forged["pit_source"] = MODULE._pit_source_block(MODULE.SOURCE_MODE_API, None)
        forged.pop("payload_sha256")
        forged["payload_sha256"] = MODULE.payload_sha256(forged)
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(forged)

        dropped = copy.deepcopy(population)
        del dropped["pit_source"]
        dropped.pop("payload_sha256")
        dropped["payload_sha256"] = MODULE.payload_sha256(dropped)
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(dropped)

    def test_an_evidence_liquidity_row_derives_the_units_vintage_it_cannot_read(self):
        """The factor is the derived one, and cannot be the capture-time one.

        The committed store shows WRESBAL rescaled on 2025-11-13, so a date
        before that boundary must normalize with 1000 and a date after it with 1,
        while TOTBKCR -- which the store shows was never rescaled -- normalizes
        with 1000 throughout. Reading the capture-time metadata instead would
        give 1 for every WRESBAL date, which is right only after the boundary.
        """
        scale = self.store.units_scale()
        early, late = "2024-06-03", self.store.scoreable_sessions()[-1]
        population = self._evidence_population([early, late])
        MODULE.validate_population(population)
        factors = {}
        for record in population["records"]:
            rows = record["five_axis"]["axes"]["LIQUIDITY"]["measurement"]["series"]
            factors[record["requested_date"]] = {
                row["series_id"]: row["normalization_factor"] for row in rows
            }
            for row in rows:
                series_scale = scale[row["series_id"]]
                expected, undone = MODULE._derived_units_scale_at(
                    series_scale, record["requested_date"], row["series_id"],
                )
                self.assertEqual(
                    row["normalization_factor"], FMD._decimal_text(expected),
                )
                self.assertEqual(
                    row["units_vintage"], MODULE.units_vintage_block(expected, undone),
                )
                # The boundary dates stay out of the measurement: one of them is
                # later than this replayed date, and every date inside a
                # measurement is bound as a consumed source date.
                self.assertNotIn("rescale_events", row["units_vintage"])
                self.assertEqual(
                    row["normalized_unit"], series_scale["normalized_unit"],
                )
                self.assertIsNone(row["source_unit"])
                self.assertNotIn("metadata_realtime_start", row)
                self.assertNotIn("metadata_realtime_end", row)
        self.assertEqual(factors[early]["WRESBAL"], "1000")
        self.assertEqual(factors[late]["WRESBAL"], "1")
        self.assertEqual(factors[early]["TOTBKCR"], "1000")
        self.assertEqual(factors[late]["TOTBKCR"], "1000")

    def test_the_derived_rescale_timeline_is_what_the_committed_store_shows(self):
        """One WRESBAL rescale, exactly ×1000, and no TOTBKCR rescale at all."""
        scale = self.store.units_scale()
        wresbal = scale["WRESBAL"]["rescale_events"]
        self.assertEqual(len(wresbal), 1, wresbal)
        self.assertEqual(wresbal[0]["effective_from"], "2025-11-13")
        self.assertEqual(wresbal[0]["power_of_ten"], 3)
        self.assertGreater(wresbal[0]["observation_date_count"], 100)
        self.assertEqual(scale["TOTBKCR"]["rescale_events"], [])
        # Every derived factor must be one the production unit table contains --
        # a derived scale outside it could not have come from the live producer.
        supported = {value[1] for value in FMD.FRED_LIQUIDITY_UNITS.values()}
        for series_id, series_scale in scale.items():
            for date in ("2020-10-20", "2025-11-12", "2025-11-13", "2026-09-11"):
                self.assertIn(
                    MODULE._derived_units_scale_at(series_scale, date, series_id)[0],
                    supported, f"{series_id}:{date}",
                )

    def test_a_liquidity_row_cannot_carry_a_factor_the_timeline_does_not_yield(self):
        population = self._evidence_population(["2024-06-03"])
        MODULE.validate_population(population)
        for mutate in (
            # the capture-time factor, which is wrong before the rescale
            lambda row: row.update({"normalization_factor": "1"}),
            # a metadata vintage window the store does not hold
            lambda row: row.update({
                "metadata_realtime_start": "2026-09-18",
                "metadata_realtime_end": "2026-09-18",
            }),
            # a units string it never read
            lambda row: row.update({"source_unit": "Millions of U.S. Dollars"}),
            # a disclosure block that no longer says what was derived
            lambda row: row.update({"units_vintage": None}),
        ):
            forged = copy.deepcopy(population)
            rows = forged["records"][0]["five_axis"]["axes"][
                "LIQUIDITY"
            ]["measurement"]["series"]
            mutate(next(row for row in rows if row["series_id"] == "WRESBAL"))
            forged.pop("payload_sha256")
            forged["payload_sha256"] = MODULE.payload_sha256(forged)
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.validate_population(forged)

    # -- the VIX vintage lag, and the asymmetry it is half of ---------------

    def test_the_evidence_vix_is_the_previous_calendar_day_vintage(self):
        """The rule, asserted as a date relation rather than as a comment.

        ALFRED backdates a VIXCLS row's availability to its observation date, so
        resolving VIX at the replayed date's own vintage serves a value the live
        producer had not been published yet. Evidence mode resolves it one
        calendar day earlier, so no replayed VIX observation may be dated on the
        replayed date itself.
        """
        self.assertEqual(
            MODULE.fred_vintage_lag_days(MODULE.SOURCE_MODE_EVIDENCE, "RISK_VOL"), 1,
        )
        dates = self.store.scoreable_sessions()[-6:]
        population = self._evidence_population(dates)
        MODULE.validate_population(population)
        for record in population["records"]:
            measurement = record["five_axis"]["axes"]["RISK_VOL"]["measurement"]
            requested = record["requested_date"]
            expected_as_of = (
                dt.date.fromisoformat(requested) - dt.timedelta(days=1)
            ).isoformat()
            self.assertEqual(measurement["vintage_lag_days"], 1, requested)
            self.assertEqual(measurement["vintage_date"], requested)
            self.assertEqual(measurement["vintage_as_of_date"], expected_as_of)
            # The load-bearing assertion: never the replayed date's own value.
            self.assertLess(measurement["observation_date"], requested, requested)
            self.assertLessEqual(measurement["observation_date"], expected_as_of)

    def test_reverting_the_evidence_vix_to_the_same_day_vintage_fails(self):
        """The lock. "ALFRED says it was available that day" must not come back.

        Two ways back to the same-day value, both refused: flipping the declared
        lag to 0, and keeping the declared lag while carrying an observation dated
        on the replayed date.
        """
        population = self._evidence_population(self.store.scoreable_sessions()[-1:])
        MODULE.validate_population(population)
        requested = population["records"][0]["requested_date"]
        for mutate in (
            lambda m: m.update({
                "vintage_lag_days": 0, "vintage_as_of_date": requested,
            }),
            lambda m: m.update({"observation_date": requested}),
            lambda m: m.update({"vintage_as_of_date": requested}),
        ):
            forged = copy.deepcopy(population)
            mutate(forged["records"][0]["five_axis"]["axes"]["RISK_VOL"]["measurement"])
            forged.pop("payload_sha256")
            forged["payload_sha256"] = MODULE.payload_sha256(forged)
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.validate_population(forged)

    def test_the_liquidity_vintage_stays_same_day_and_the_asymmetry_holds(self):
        """The other half of the rule: liquidity is not lagged, and cannot be.

        The weekly series carry their release lag inside the row already, so a
        calendar-day lag here would be a second, invented delay. The asymmetry is
        the live producer's behaviour, so flattening it in *either* direction is a
        rule change and fails closed.
        """
        self.assertEqual(
            MODULE.fred_vintage_lag_days(MODULE.SOURCE_MODE_EVIDENCE, "LIQUIDITY"), 0,
        )
        self.assertNotEqual(
            MODULE.fred_vintage_lag_days(MODULE.SOURCE_MODE_EVIDENCE, "RISK_VOL"),
            MODULE.fred_vintage_lag_days(MODULE.SOURCE_MODE_EVIDENCE, "LIQUIDITY"),
        )
        population = self._evidence_population(self.store.scoreable_sessions()[-1:])
        MODULE.validate_population(population)
        requested = population["records"][0]["requested_date"]
        liquidity = population["records"][0]["five_axis"]["axes"][
            "LIQUIDITY"
        ]["measurement"]
        self.assertEqual(liquidity["vintage_lag_days"], 0)
        self.assertEqual(liquidity["vintage_as_of_date"], requested)
        self.assertEqual(liquidity["vintage_date"], requested)

        lagged = (dt.date.fromisoformat(requested) - dt.timedelta(days=1)).isoformat()
        forged = copy.deepcopy(population)
        forged["records"][0]["five_axis"]["axes"]["LIQUIDITY"]["measurement"].update({
            "vintage_lag_days": 1, "vintage_as_of_date": lagged,
        })
        forged.pop("payload_sha256")
        forged["payload_sha256"] = MODULE.payload_sha256(forged)
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(forged)

        # And the builder refuses to lag it even if the rule table were edited.
        with mock.patch.dict(
            MODULE.FRED_VINTAGE_LAG_DAYS[MODULE.SOURCE_MODE_EVIDENCE],
            {"LIQUIDITY": 1},
        ):
            with self.assertRaises(MODULE.ReplayPopulationError):
                MODULE.replay_liquidity_source(
                    "KEY", dt.date.fromisoformat(requested), getter=self._getter(),
                    contract=self.contract,
                    source_mode=MODULE.SOURCE_MODE_EVIDENCE,
                    units_scale=self.store.units_scale(),
                )

    def test_the_liquidity_direction_is_invariant_under_any_positive_units_factor(self):
        """Why the withheld units vintage cannot change an axis, only a magnitude.

        ``liquidity_axis_row`` reads the sign of each series' change; both sides
        of that difference come from one vintage and therefore one scale, and
        every factor in ``FMD.FRED_LIQUIDITY_UNITS`` is strictly positive. This
        pins that bound rather than asserting it in a comment: the evidence-read
        magnitudes are native rather than normalized, and the axis is the same.
        """
        for _, factor in FMD.FRED_LIQUIDITY_UNITS.values():
            self.assertGreater(factor, 0)
        population = self._evidence_population(self.store.scoreable_sessions()[-1:])
        series = population["records"][0]["five_axis"]["axes"][
            "LIQUIDITY"
        ]["measurement"]["series"]
        native = MODULE.liquidity_axis_row(series)
        for factor in ("1000", "0.001", "1000000"):
            scaled = copy.deepcopy(series)
            for row in scaled:
                for key in ("value", "previous_value", "change"):
                    row[key] = FMD._decimal_text(
                        FMD._decimal(row[key], "X") * FMD._decimal(factor, "X")
                    )
            rescaled = MODULE.liquidity_axis_row(scaled)
            self.assertEqual(rescaled["direction"], native["direction"], factor)
            self.assertEqual(rescaled["score"], native["score"], factor)

    def test_evidence_mode_is_deterministic(self):
        dates = self.store.scoreable_sessions()[-3:]
        first = self._evidence_population(dates)
        second = self._evidence_population(dates)
        self.assertEqual(
            MODULE.canonical_json(first), MODULE.canonical_json(second),
        )

    def test_a_live_provider_population_is_unchanged_by_the_evidence_mode(self):
        """The default path keeps its exact records, warnings and declaration.

        The only addition a live-provider population gains is the explicit
        ``pit_source`` block saying so -- the records, the warnings and the PIT
        statement are byte-identical to what this module produced before the
        evidence read path existed, and the liquidity rows still carry the
        strictly bound metadata vintage.
        """
        population = build([ANCHOR])
        MODULE.validate_population(population)
        self.assertEqual(
            population["pit_source"],
            MODULE._pit_source_block(MODULE.SOURCE_MODE_API, None),
        )
        self.assertEqual(population["records"][0]["warnings"], MODULE.RECORD_WARNINGS)
        self.assertEqual(
            population["pit_replay"]["statement"], MODULE.PIT_REPLAY_STATEMENT,
        )
        for row in population["records"][0]["five_axis"]["axes"][
            "LIQUIDITY"
        ]["measurement"]["series"]:
            self.assertNotIn("units_vintage", row)
            self.assertIn("metadata_realtime_start", row)
            self.assertIsNotNone(row["normalized_unit"])

    def test_a_population_built_before_this_block_existed_is_still_the_api_default(self):
        """An older payload carries no ``pit_source``; that is the live default."""
        population = build([ANCHOR])
        legacy = copy.deepcopy(population)
        del legacy["pit_source"]
        legacy.pop("payload_sha256")
        legacy["payload_sha256"] = MODULE.payload_sha256(legacy)
        MODULE.validate_population(legacy)


if __name__ == "__main__":
    unittest.main()
