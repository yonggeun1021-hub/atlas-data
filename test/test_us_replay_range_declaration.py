#!/usr/bin/env python3
"""US-DATA-1 U3: coverage probe, range declaration and resumable replay driver.

Offline only. A fake provider "world" answers the probe's bounded capture, and
every instant is a fixed constant, so no test depends on today's date. Chunk
execution uses injected runners, clocks and sleeps.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import datetime as dt
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.parse
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from market_data import us_official_session_calendar as CAL  # noqa: E402
from regime import us_replay_coverage_probe as PROBE  # noqa: E402
from regime import us_replay_range_declaration as DECL  # noqa: E402
from regime import us_replay_range_driver as DRIVER  # noqa: E402

FIXTURES = ROOT / "test" / "fixtures" / "us_session_calendar"
NYSE_HTML = (FIXTURES / "nyse_hours_calendars_synthetic.html").read_bytes()
NASDAQ_HTML = (FIXTURES / "nasdaq_holiday_schedule_synthetic.html").read_bytes()
NYSE_PARSED = CAL.parse_nyse_page(NYSE_HTML)
SECRETS = {"alpaca_key": "ALPACAKEYSECRET0001", "alpaca_secret": "ALPACASECRETVALUE0002", "fred_key": "FREDKEYSECRET0003"}
PRICE_MARKER = 987.6543
AFTER_CLOSE = dt.datetime(2026, 9, 14, 21, 0, tzinfo=dt.timezone.utc)
BEFORE_CLOSE = dt.datetime(2026, 9, 14, 15, 0, tzinfo=dt.timezone.utc)

# Fixture-only closure list for the 2025 two-source year (test data, not a rule
# in production code; production never derives sessions from weekdays).
FIXTURE_2025_CLOSED = {
    "2025-01-01", "2025-01-09", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
}
FIXTURE_2025_EARLY = {"2025-07-03", "2025-11-28", "2025-12-24"}


def fixture_calendar(start: dt.date, end: dt.date) -> list[tuple[str, str]]:
    rows = []
    for day in CAL.daterange(start, end):
        key = day.isoformat()
        if day.year >= 2026:
            status = CAL.official_page_status(NYSE_PARSED, day)
            if status in CAL.OPEN_STATUSES:
                rows.append((key, "13:00" if status == CAL.STATUS_EARLY else "16:00"))
        elif day.weekday() < 5 and key not in FIXTURE_2025_CLOSED:
            rows.append((key, "13:00" if key in FIXTURE_2025_EARLY else "16:00"))
    return rows


class FakeWorld:
    def __init__(self, *, starts=None, fred=None, drop_spy=(), page_rows=2500, fail=None, clock=AFTER_CLOSE):
        self.calendar = fixture_calendar(dt.date(2025, 1, 2), clock.astimezone(CAL.NY).date())
        self.starts = {symbol: "2025-01-02" for symbol in PROBE.replay_symbols()}
        self.starts["XLC"] = "2025-03-03"
        self.starts.update(starts or {})
        self.fred = {"VIXCLS": "2010-01-04", "WRESBAL": "2011-02-01", "TOTBKCR": "2012-03-01"}
        self.fred.update(fred or {})
        self.drop_spy = set(drop_spy)
        self.page_rows = page_rows
        self.fail = fail or {}
        self.calls = []
        self.clock = clock

    def _rows(self):
        rows = []
        for symbol in PROBE.replay_symbols():
            for day, _ in self.calendar:
                if day < self.starts[symbol] or (symbol == "SPY" and day in self.drop_spy):
                    continue
                rows.append((symbol, {"t": f"{day}T04:00:00Z", "o": PRICE_MARKER, "h": PRICE_MARKER, "l": PRICE_MARKER, "c": PRICE_MARKER, "v": 10}))
        return rows

    def opener(self, url, headers):
        self.calls.append((url, dict(headers)))
        parsed = urllib.parse.urlsplit(url)
        base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
        query = dict(urllib.parse.parse_qsl(parsed.query))
        for prefix, code in self.fail.items():
            if base.startswith(prefix) or query.get("series_id") == prefix:
                raise urllib.error.HTTPError(url, code, "fail", {}, None)
        if base == CAL.NYSE_URL:
            return 200, "text/html", CAL.NYSE_URL, 0, NYSE_HTML
        if base == CAL.NASDAQ_URL:
            return 200, "text/html", CAL.NASDAQ_URL, 0, NASDAQ_HTML
        if base in CAL.ALPACA_CALENDAR_URLS:
            assert headers["APCA-API-KEY-ID"] == SECRETS["alpaca_key"]
            body = [{"date": day, "open": "09:30", "close": close} for day, close in self.calendar]
            return 200, "application/json", url, 0, json.dumps(body).encode()
        if base == CAL.ALPACA_BARS_URL:
            rows = self._rows()
            page = int(query.get("page_token", "p0")[1:])
            chunk = rows[page * self.page_rows:(page + 1) * self.page_rows]
            grouped = {}
            for symbol, row in chunk:
                grouped.setdefault(symbol, []).append(row)
            token = f"p{page + 1}" if (page + 1) * self.page_rows < len(rows) else None
            return 200, "application/json", CAL.ALPACA_BARS_URL, 0, json.dumps({"bars": grouped, "next_page_token": token}).encode()
        if base == PROBE.FRED_VINTAGEDATES_URL:
            assert query["api_key"] == SECRETS["fred_key"]
            series = query["series_id"]
            return 200, "application/json", url, 0, json.dumps({"vintage_dates": [self.fred[series], "2026-09-01"]}).encode()
        raise AssertionError(f"unexpected url {base}")


def probe(world: FakeWorld, tmp: Path, **kwargs) -> tuple[dict, dict]:
    manifest = PROBE.capture(tmp / "raw", SECRETS, opener=world.opener, clock=lambda: world.clock, **kwargs)
    return manifest, PROBE.analyze(tmp / "raw")


def resign(summary: dict) -> dict:
    summary = copy.deepcopy(summary)
    summary.pop("summary_sha256", None)
    summary["summary_sha256"] = CAL.payload_sha256(summary)
    return summary


def commit_pair(directory: Path, summary: dict, declaration: dict | None = None) -> Path:
    """Write a declaration and its probe summary under the committed naming convention."""
    directory.mkdir(parents=True, exist_ok=True)
    declaration = DECL.declare(summary) if declaration is None else declaration
    (directory / DECL.SUMMARY_FILE_NAME).write_bytes(CAL.canonical_bytes(summary))
    path = directory / DECL.DECLARATION_FILE_NAME
    path.write_bytes(CAL.canonical_bytes(declaration))
    return path


def parser_option_strings(parser: argparse.ArgumentParser) -> set[str]:
    options: set[str] = set()
    for action in parser._actions:
        options.update(action.option_strings)
        if isinstance(action, argparse._SubParsersAction):
            for sub in action.choices.values():
                options |= parser_option_strings(sub)
    return options


DATE_LIKE_OPTION = r"date|start|end|first|last|range|session|from|until|since|begin|skip|offset|limit"


class TempDirCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)


class ProbeCaptureTest(TempDirCase):
    def test_replay_symbols_are_trend_plus_sector_reference(self):
        contract = json.loads((ROOT / "config/free_market_data_contract.json").read_text())
        symbols = PROBE.replay_symbols(contract)
        self.assertEqual(len(symbols), 15)
        self.assertEqual(set(symbols), set(contract["alpaca"]["trend_symbols"]) | set(contract["alpaca"]["sector_reference_symbols"]))
        self.assertNotIn("MSFT", symbols)

    def test_capture_is_bounded_and_records_no_secret(self):
        world = FakeWorld()
        manifest, summary = probe(world, self.tmp)
        self.assertLessEqual(len(world.calls), PROBE.REQUEST_BUDGET)
        self.assertEqual(manifest["requests_used"], len(world.calls))
        self.assertTrue(manifest["bar_pagination_complete"])
        self.assertGreater(manifest["bar_pages"], 1)
        for path in self.tmp.rglob("*"):
            if path.is_file():
                text = path.read_bytes()
                for secret in SECRETS.values():
                    self.assertNotIn(secret.encode(), text, path.name)
        summary_text = json.dumps(summary)
        self.assertNotIn(str(PRICE_MARKER), summary_text)
        self.assertFalse(summary["bars"]["price_fields_retained"])
        self.assertEqual(summary["bars"]["per_symbol"]["XLC"]["earliest_bar_date"], "2025-03-03")
        self.assertEqual(summary["fred"]["VIXCLS"]["earliest_vintage_date"], "2010-01-04")

    def test_bar_pagination_cap_keeps_total_at_budget_and_blocks(self):
        world = FakeWorld(page_rows=200)
        manifest, summary = probe(world, self.tmp)
        self.assertEqual(len(world.calls), 15)
        self.assertEqual(manifest["bar_pages"], PROBE.MAX_BAR_PAGES)
        self.assertFalse(manifest["bar_pagination_complete"])
        self.assertEqual(summary["bars"]["status"], "INCOMPLETE")
        declaration = DECL.declare(summary)
        self.assertEqual(declaration["status"], "BLOCKED")
        self.assertIsNone(declaration["range"])

    def test_budget_counter_refuses_the_request_over_the_limit(self):
        with self.assertRaisesRegex(PROBE.ProbeError, "REQUEST_BUDGET_EXHAUSTED"):
            probe(FakeWorld(), self.tmp, budget=PROBE.RequestBudget(limit=5))

    def test_analyze_rejects_tampered_capture_file(self):
        probe(FakeWorld(), self.tmp)
        path = self.tmp / "raw/public/nyse_page.json"
        path.write_bytes(path.read_bytes().replace(b'"http_status": 200', b'"http_status": 201'))
        with self.assertRaisesRegex(PROBE.ProbeError, "CAPTURE_FILE_HASH_MISMATCH"):
            PROBE.analyze(self.tmp / "raw")

    def test_cli_refuses_output_inside_checkout(self):
        with self.assertRaisesRegex(PROBE.ProbeError, "OUTPUT_INSIDE_CHECKOUT_FORBIDDEN"):
            PROBE.main(["capture", "--out-dir", str(ROOT / "tmp-probe")])


class DeclarationTest(TempDirCase):
    def expected_sessions(self, start: str, last: str) -> list[str]:
        return [day for day, _ in fixture_calendar(dt.date(2025, 1, 2), dt.date(2026, 9, 14)) if start <= day <= last]

    def test_declared_range_uses_61_session_warm_up(self):
        _, summary = probe(FakeWorld(), self.tmp)
        declaration = DECL.declare(summary)
        self.assertEqual(declaration["status"], "DECLARED", declaration["blocked_reasons"])
        sessions = self.expected_sessions("2025-03-03", "2026-09-14")
        block = declaration["range"]
        self.assertEqual(block["data_start"], "2025-03-03")
        self.assertEqual(block["binding_symbol"], "XLC")
        self.assertEqual(block["warm_up_sessions"], 61)
        self.assertEqual(block["warm_up_window_first_session"], "2025-03-03")
        self.assertEqual(block["first_replay_date"], sessions[60])
        self.assertEqual(block["sessions"], sessions[60:])
        self.assertEqual(block["session_count"], len(sessions) - 60)
        self.assertEqual(block["last_replay_date"], "2026-09-14")
        self.assertEqual(block["sessions_sha256"], CAL.payload_sha256(sessions[60:]))
        counts = declaration["calendar_session_basis_counts"]
        self.assertGreater(counts[CAL.BASIS_OFFICIAL], 0)
        self.assertGreater(counts[CAL.BASIS_TWO_SOURCE], 0)
        self.assertFalse(declaration["rule"]["sub_range_selection_allowed"])
        self.assertEqual(declaration["rule"]["replay_symbol_count"], 15)
        self.assertEqual(declaration["declared_at"], "2026-09-14T21:00:00Z")
        self.assertEqual(CAL.canonical_bytes(declaration), CAL.canonical_bytes(DECL.declare(summary)))

    def test_upper_bound_is_latest_session_closed_before_capture(self):
        _, summary = probe(FakeWorld(clock=BEFORE_CLOSE), self.tmp)
        declaration = DECL.declare(summary)
        self.assertEqual(declaration["range"]["last_replay_date"], "2026-09-11")

    def test_later_alfred_vintage_binds_data_start(self):
        _, summary = probe(FakeWorld(fred={"TOTBKCR": "2025-06-02"}), self.tmp)
        declaration = DECL.declare(summary)
        sessions = self.expected_sessions("2025-06-02", "2026-09-14")
        self.assertEqual(declaration["range"]["data_start"], "2025-06-02")
        self.assertEqual(declaration["range"]["first_replay_date"], sessions[60])

    def test_unknown_calendar_date_blocks_whole_range_without_truncation(self):
        _, summary = probe(FakeWorld(drop_spy={"2025-05-06"}), self.tmp)
        declaration = DECL.declare(summary)
        self.assertEqual(declaration["status"], "BLOCKED")
        self.assertIsNone(declaration["range"])
        self.assertIn("CALENDAR_UNKNOWN_DATES_INSIDE_RANGE:1", declaration["blocked_reasons"])
        self.assertEqual(declaration["unknown_dates_sample"], ["2025-05-06"])

    def test_unknown_date_before_data_start_does_not_block(self):
        _, summary = probe(FakeWorld(drop_spy={"2025-02-04"}), self.tmp)
        self.assertEqual(DECL.declare(summary)["status"], "DECLARED")

    def test_left_censored_binding_symbol_blocks(self):
        _, summary = probe(FakeWorld(), self.tmp)
        for row in summary["bars"]["per_symbol"].values():
            row["earliest_bar_date"] = "2015-01-05"
        declaration = DECL.declare(resign(summary))
        self.assertTrue(any(reason.startswith("BINDING_SYMBOL_LEFT_CENSORED") for reason in declaration["blocked_reasons"]))

    def test_missing_sources_block(self):
        _, summary = probe(FakeWorld(fail={CAL.NYSE_URL: 403, "WRESBAL": 500}), self.tmp)
        reasons = DECL.declare(summary)["blocked_reasons"]
        self.assertTrue(any(reason.startswith("NYSE_OFFICIAL_CAPTURE_UNAVAILABLE") for reason in reasons))
        self.assertTrue(any(reason.startswith("ALFRED_VINTAGES_UNAVAILABLE:WRESBAL") for reason in reasons))

    def test_tampered_summary_fails_closed(self):
        _, summary = probe(FakeWorld(), self.tmp)
        summary["bars"]["per_symbol"]["XLC"]["earliest_bar_date"] = "2025-01-02"
        with self.assertRaisesRegex(DECL.DeclarationError, "SUMMARY_HASH_MISMATCH"):
            DECL.declare(summary)

    def test_warm_up_follows_only_the_ratified_contract(self):
        contract = json.loads((ROOT / "config/free_market_data_contract.json").read_text())
        self.assertEqual(DECL.warm_up_sessions(contract), 61)
        contract["alpaca"]["return_windows_sessions"] = [5, 20, 90]
        with self.assertRaisesRegex(DECL.DeclarationError, "WARM_UP_CONTRACT_CHANGED"):
            DECL.warm_up_sessions(contract)

    def test_cli_has_no_sub_range_arguments(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            DECL.main(["declare", "--summary", "x.json", "--out", "y.json", "--start", "2020-01-02"])
        options = parser_option_strings(DECL.build_parser())
        self.assertEqual(options, {"-h", "--help", "--summary", "--out", "--declaration"})
        self.assertFalse([option for option in options if __import__("re").search(DATE_LIKE_OPTION, option)])

    def test_rule_text_states_the_2018_clamp(self):
        self.assertIn("2018-01-01", DECL.RULE_TEXT)
        self.assertIn("clamped", DECL.RULE_TEXT)

    def test_load_declaration_checks_hash_status_and_rule(self):
        _, summary = probe(FakeWorld(), self.tmp)
        declaration = DECL.declare(summary)
        path = commit_pair(self.tmp / "pair", summary)
        self.assertEqual(DECL.load_declaration(path)["declaration_sha256"], declaration["declaration_sha256"])

        tampered = copy.deepcopy(declaration)
        tampered["range"]["sessions"] = tampered["range"]["sessions"][10:]
        path.write_bytes(CAL.canonical_bytes(tampered))
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_HASH_MISMATCH"):
            DECL.load_declaration(path)

        resigned = copy.deepcopy(tampered)
        resigned.pop("declaration_sha256")
        resigned["declaration_sha256"] = CAL.payload_sha256(resigned)
        path.write_bytes(CAL.canonical_bytes(resigned))
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_SESSIONS_INCONSISTENT"):
            DECL.load_declaration(path)

        blocked_summary = probe(FakeWorld(drop_spy={"2025-05-06"}), self.tmp / "b")[1]
        path = commit_pair(self.tmp / "blocked", blocked_summary)
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_NOT_DECLARED"):
            DECL.load_declaration(path)

    def test_consistent_resigned_sub_range_is_rejected(self):
        # The reviewer's attack: cut the declared sessions to a sub-range, make
        # every derived field consistent, re-sign. Only the rebuild catches it.
        _, summary = probe(FakeWorld(), self.tmp)
        declaration = DECL.declare(summary)
        self.assertGreater(declaration["range"]["session_count"], 260)
        forged = copy.deepcopy(declaration)
        cut = forged["range"]["sessions"][200:260]
        forged["range"].update({
            "sessions": cut, "first_replay_date": cut[0], "last_replay_date": cut[-1],
            "session_count": len(cut), "sessions_sha256": CAL.payload_sha256(cut),
        })
        forged.pop("declaration_sha256")
        forged["declaration_sha256"] = CAL.payload_sha256(forged)
        path = commit_pair(self.tmp / "forged", summary, forged)
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_DOES_NOT_REBUILD_FROM_COMMITTED_SUMMARY"):
            DECL.load_declaration(path)
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_DOES_NOT_REBUILD_FROM_COMMITTED_SUMMARY"):
            DECL.main(["verify", "--declaration", str(path)])
        # The driver refuses it on every command, before any chunk is planned.
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_DOES_NOT_REBUILD_FROM_COMMITTED_SUMMARY"):
            DRIVER.main(["plan", "--declaration", str(path)])

    def test_declaration_needs_the_committed_summary_under_the_naming_convention(self):
        _, summary = probe(FakeWorld(), self.tmp)
        path = commit_pair(self.tmp / "pair", summary)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(DECL.main(["verify", "--declaration", str(path)]), 0)
        self.assertIn("PASS_US_REPLAY_RANGE_DECLARATION_VERIFIED", out.getvalue())
        renamed = self.tmp / "pair" / "declaration.json"
        shutil.copyfile(path, renamed)
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_PATH_CONVENTION"):
            DECL.load_declaration(renamed)
        (self.tmp / "pair" / DECL.SUMMARY_FILE_NAME).unlink()
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_PROBE_SUMMARY_NOT_COMMITTED"):
            DECL.load_declaration(path)
        # A different (validly signed) summary next to the declaration also fails.
        other = probe(FakeWorld(clock=BEFORE_CLOSE), self.tmp / "other")[1]
        (self.tmp / "pair" / DECL.SUMMARY_FILE_NAME).write_bytes(CAL.canonical_bytes(other))
        with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_DOES_NOT_REBUILD_FROM_COMMITTED_SUMMARY"):
            DECL.load_declaration(path)

    def test_contract_drift_only_on_range_relevant_fields(self):
        _, summary = probe(FakeWorld(), self.tmp)
        declaration = DECL.declare(summary)
        root = self.tmp / "root"
        for rel in (
            "config/free_market_data_contract.json", "config/us_session_calendar_source_v1.json",
            "evidence/authority/us_session_calendar_source_user_ratification_20260914.json",
            "config/regime_source_owner_registry_v2.json",
        ):
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / rel, root / rel)
        path = commit_pair(self.tmp / "pair", summary, declaration)
        contract_path = root / "config/free_market_data_contract.json"
        contract = json.loads(contract_path.read_text())
        contract["alpaca"]["current_proxy_axes"]["approval_status"] = "SOME_LATER_RATIFICATION"
        contract_path.write_text(json.dumps(contract))
        with mock.patch.object(DECL, "ROOT", root):
            # An unrelated contract change neither moves the range nor breaks
            # the byte-identical rebuild.
            DECL.load_declaration(path)
            contract["alpaca"]["sector_reference_symbols"].append("XLZ")
            contract_path.write_text(json.dumps(contract))
            with self.assertRaisesRegex(DECL.DeclarationError, "DECLARATION_INPUT_DRIFT"):
                DECL.load_declaration(path)


def stub_declaration(count: int = 10) -> dict:
    sessions = [(dt.date(2019, 1, 1) + dt.timedelta(days=index)).isoformat() for index in range(count)]
    return {
        "declaration_sha256": "d" * 64,
        "range": {
            "sessions": sessions, "first_replay_date": sessions[0], "last_replay_date": sessions[-1],
            "session_count": count, "sessions_sha256": CAL.payload_sha256(sessions),
        },
    }


def stub_validator(population):
    if population.get("schema_version") != "stub":
        raise ValueError("not a population")


def stub_runner(record_text: str = "ok", code: int = 0, output: str = ""):
    calls = []

    def runner(chunk, out_path, env):
        calls.append(list(chunk["dates"]))
        if code == 0:
            Path(out_path).write_text(json.dumps({
                "schema_version": "stub", "mode": "m", "requested_dates": chunk["dates"],
                "records": [{"requested_date": day, "status": "FREE_AXES_OBSERVED", "failure_reason": record_text} for day in chunk["dates"]],
            }))
        return code, output

    runner.calls = calls
    return runner


class FakeClock:
    def __init__(self, step: float = 1.0):
        self.now = 0.0
        self.step = step

    def __call__(self):
        self.now += self.step
        return self.now


class DriverTest(TempDirCase):
    def run_driver(self, declaration, runner, **kwargs):
        logs = []
        sleeps = []
        progress = DRIVER.run(
            declaration, self.tmp / "work", chunk_size=kwargs.pop("chunk_size", 4), runner=runner,
            validator=stub_validator, sleep=sleeps.append, monotonic=kwargs.pop("monotonic", FakeClock()),
            env=kwargs.pop("env", {}), log=logs.append, **kwargs,
        )
        return progress, logs, sleeps

    def test_plan_is_deterministic_and_covers_every_session_once(self):
        sessions = stub_declaration(10)["range"]["sessions"]
        plan = DRIVER.plan_chunks(sessions, 4)
        self.assertEqual([len(chunk["dates"]) for chunk in plan], [4, 4, 2])
        self.assertEqual([day for chunk in plan for day in chunk["dates"]], sessions)
        self.assertEqual(plan, DRIVER.plan_chunks(list(sessions), 4))
        self.assertTrue(plan[0]["file_name"].startswith("chunk-0001-"))
        with self.assertRaisesRegex(DRIVER.DriverError, "CHUNK_SIZE_INVALID"):
            DRIVER.plan_chunks(sessions, 0)

    def test_resume_runs_only_pending_chunks(self):
        declaration = stub_declaration(10)
        runner = stub_runner()
        self.run_driver(declaration, runner, time_budget_seconds=2.5, monotonic=FakeClock(step=1.0))
        first_calls = len(runner.calls)
        self.assertGreaterEqual(first_calls, 1)
        self.assertLess(first_calls, 3)
        second = stub_runner()
        progress, _, _ = self.run_driver(declaration, second, time_budget_seconds=10_000)
        self.assertEqual(len(second.calls), 3 - first_calls)
        self.assertEqual(progress["complete_chunks"], 3)
        third = stub_runner()
        self.run_driver(declaration, third, time_budget_seconds=10_000)
        self.assertEqual(third.calls, [])  # idempotent: nothing left to do

    def test_chunk_starts_are_spaced_by_the_window_from_chunk_start(self):
        _, _, sleeps = self.run_driver(stub_declaration(10), stub_runner(), time_budget_seconds=10_000)
        # Three chunks: no wait before the first; later waits are measured from
        # the previous start (the fake clock advances 1s per reading).
        self.assertEqual(len(sleeps), 2)
        for wait in sleeps:
            self.assertGreater(wait, 50)
            self.assertLessEqual(wait, DRIVER.PACING_WINDOW_SECONDS)
        self.assertEqual(DRIVER.start_wait_seconds(None, 5.0), 0.0)
        self.assertEqual(DRIVER.start_wait_seconds(10.0, 25.0), 45.0)
        self.assertEqual(DRIVER.start_wait_seconds(10.0, 200.0), 0.0)

    def test_chunk_size_is_capped_at_half_of_each_per_minute_limit(self):
        self.assertEqual(DRIVER.max_chunk_size(), 5)
        self.assertEqual(DRIVER.DEFAULT_CHUNK_SIZE, 5)
        self.assertLessEqual(5 * DRIVER.DEFAULT_ALPACA_REQUESTS_PER_DATE, DRIVER.DEFAULT_ALPACA_RPM / 2)
        self.assertLessEqual(5 * DRIVER.DEFAULT_FRED_REQUESTS_PER_DATE, DRIVER.DEFAULT_FRED_RPM / 2)
        self.assertEqual(DRIVER.max_chunk_size(alpaca_rpm=200, fred_rpm=120), 5)
        self.assertEqual(DRIVER.max_chunk_size(fred_rpm=60), 5)
        self.assertEqual(DRIVER.max_chunk_size(fred_rpm=48), 4)
        with self.assertRaisesRegex(DRIVER.DriverError, "CHUNK_SIZE_INVALID"):
            DRIVER.plan_chunks(stub_declaration(10)["range"]["sessions"], 6)
        with self.assertRaisesRegex(DRIVER.DriverError, "CHUNK_SIZE_INVALID"):
            DRIVER.run(stub_declaration(10), self.tmp / "work", chunk_size=40, runner=stub_runner(),
                       validator=stub_validator, sleep=lambda _: None, log=lambda _: None)

    def test_no_sixty_second_window_exceeds_either_limit_under_worst_case_bursts(self):
        clock = {"now": 0.0}
        events = []  # (time, provider, count)
        durations = [0.0, 59.9, 0.2, 75.0, 3.0, 120.0, 0.0, 30.0, 59.999, 1.0]
        attempts = {"n": 0}

        def sleep(seconds):
            self.assertGreaterEqual(seconds, 0)
            clock["now"] += seconds

        def runner(chunk, out_path, env):
            index = attempts["n"]
            attempts["n"] += 1
            start = clock["now"]
            clock["now"] += durations[index % len(durations)]
            # The child is unpaced: adversarially burst every worst-case request
            # at the start of one attempt and at the end of the next, so
            # consecutive bursts sit as close together as the driver allows.
            burst_at = start if index % 2 else clock["now"]
            dates = len(chunk["dates"])
            events.append((burst_at, "alpaca", dates * DRIVER.DEFAULT_ALPACA_REQUESTS_PER_DATE))
            events.append((burst_at, "fred", dates * DRIVER.DEFAULT_FRED_REQUESTS_PER_DATE))
            if index % 7 == 3:
                return 1, "HTTP_ERROR:503"  # failed attempt still spent its requests
            Path(out_path).write_text(json.dumps({
                "schema_version": "stub", "requested_dates": chunk["dates"],
                "records": [{"requested_date": day, "status": "FREE_AXES_OBSERVED"} for day in chunk["dates"]],
            }))
            return 0, ""

        progress = DRIVER.run(
            stub_declaration(83), self.tmp / "work", chunk_size=DRIVER.DEFAULT_CHUNK_SIZE, runner=runner,
            validator=stub_validator, sleep=sleep, monotonic=lambda: clock["now"], env={},
            log=lambda _: None, time_budget_seconds=10**9,
        )
        self.assertEqual(progress["complete_chunks"], progress["planned_chunks"])
        self.assertGreater(attempts["n"], progress["planned_chunks"])  # retries happened
        limits = {"alpaca": DRIVER.DEFAULT_ALPACA_RPM, "fred": DRIVER.DEFAULT_FRED_RPM}
        for provider, limit in limits.items():
            times = [(at, count) for at, name, count in events if name == provider]
            worst = max(sum(count for other, count in times if at <= other < at + 60.0) for at, _ in times)
            self.assertLessEqual(worst, limit, provider)

    def test_estimate_for_about_two_thousand_sessions(self):
        self.assertEqual(DRIVER.estimate(2000), {
            "session_count": 2000, "chunk_size": 5, "chunks": 400, "alpaca_requests_upper_bound": 36000,
            "fred_requests_upper_bound": 12000, "paced_minutes_lower_bound": 400.0,
            "workflow_runs_lower_bound": 2,
        })

    def test_driver_parser_accepts_no_date_or_range_option(self):
        parser = DRIVER.build_parser()
        options = parser_option_strings(parser)
        self.assertEqual(options, {
            "-h", "--help", "--declaration", "--work-dir", "--chunk-size", "--time-budget-minutes", "--summary-out",
        })
        self.assertFalse([option for option in options if __import__("re").search(DATE_LIKE_OPTION, option)])
        positionals = [action for action in parser._actions if not action.option_strings and action.dest != "help"]
        self.assertEqual([(action.dest, list(action.choices), action.nargs) for action in positionals],
                         [("command", ["plan", "run", "finalize"], None)])
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            DRIVER.main(["plan", "--first-date", "2020-01-02"])

    def test_nonzero_child_exit_never_promotes_its_output(self):
        def runner(chunk, out_path, env):
            Path(out_path).write_text(json.dumps({
                "schema_version": "stub", "requested_dates": chunk["dates"],
                "records": [{"requested_date": day, "status": "FREE_AXES_OBSERVED"} for day in chunk["dates"]],
            }))
            return 1, "crashed after writing"

        progress, _, _ = self.run_driver(stub_declaration(4), runner, time_budget_seconds=10_000)
        chunks_dir = self.tmp / "work" / "chunks"
        chunk = DRIVER.plan_chunks(stub_declaration(4)["range"]["sessions"], 4)[0]
        self.assertFalse((chunks_dir / chunk["file_name"]).exists())
        self.assertFalse(DRIVER.meta_path(chunks_dir, chunk).exists())
        self.assertEqual(list(chunks_dir.iterdir()), [])
        self.assertEqual(progress["complete_chunks"], 0)
        self.assertEqual(progress["failures_this_run"], [{"chunk": 1, "state": "ABSENT"}])

    def test_chunks_from_different_replay_code_are_never_merged(self):
        declaration = stub_declaration(10)
        work = self.tmp / "work"
        DRIVER.run(declaration, work, chunk_size=4, runner=stub_runner(), validator=stub_validator,
                   sleep=lambda _: None, log=lambda _: None, env={}, time_budget_seconds=10**9,
                   code_sha256="a" * 64)
        chunks_dir = work / "chunks"
        plan = DRIVER.plan_chunks(declaration["range"]["sessions"], 4)
        for chunk in plan:
            self.assertEqual(DRIVER.read_chunk_meta(chunks_dir, chunk)["replay_code_sha256"], "a" * 64)
        evaluated = []
        # Current code differs from the code that made every chunk: refused.
        with self.assertRaisesRegex(DRIVER.DriverError, "CHUNK_CODE_HASH_MISMATCH"):
            DRIVER.finalize(declaration, work, chunk_size=4, validator=stub_validator,
                            evaluate=lambda *args: evaluated.append(args))
        # One chunk re-labelled as made by other code: mixed hashes, refused.
        meta_file = DRIVER.meta_path(chunks_dir, plan[1])
        meta = json.loads(meta_file.read_text())
        meta["replay_code_sha256"] = "b" * 64
        meta_file.write_bytes(CAL.canonical_bytes(meta))
        with self.assertRaisesRegex(DRIVER.DriverError, "CHUNK_CODE_HASH_MISMATCH"):
            DRIVER.finalize(declaration, work, chunk_size=4, validator=stub_validator,
                            evaluate=lambda *args: evaluated.append(args), code_sha256="a" * 64)
        self.assertEqual(evaluated, [])
        # Resuming under different code refuses to add chunks as well.
        with self.assertRaisesRegex(DRIVER.DriverError, "CHUNK_CODE_HASH_MISMATCH"):
            DRIVER.run(declaration, work, chunk_size=4, runner=stub_runner(), validator=stub_validator,
                       sleep=lambda _: None, log=lambda _: None, env={}, code_sha256="a" * 64)

    def test_chunk_file_without_a_matching_meta_is_not_complete(self):
        declaration = stub_declaration(4)
        self.run_driver(declaration, stub_runner(), time_budget_seconds=10_000)
        chunks_dir = self.tmp / "work" / "chunks"
        chunk = DRIVER.plan_chunks(declaration["range"]["sessions"], 4)[0]
        self.assertEqual(DRIVER.chunk_state(chunk, chunks_dir, validator=stub_validator), "COMPLETE")
        path = chunks_dir / chunk["file_name"]
        path.write_text(path.read_text() + " ")  # file no longer matches its meta
        self.assertEqual(DRIVER.chunk_state(chunk, chunks_dir, validator=stub_validator), "INVALID")
        path.write_text(path.read_text()[:-1])
        DRIVER.meta_path(chunks_dir, chunk).unlink()
        self.assertEqual(DRIVER.chunk_state(chunk, chunks_dir, validator=stub_validator), "INVALID")

    def test_transport_failure_is_retried_then_stops_the_run(self):
        runner = stub_runner(record_text="HTTP_ERROR:429")
        progress, _, _ = self.run_driver(stub_declaration(10), runner, time_budget_seconds=10_000)
        self.assertEqual(len(runner.calls), 2)  # two attempts on chunk 1, then stop
        self.assertEqual(progress["failures_this_run"], [{"chunk": 1, "state": "PROVIDER_ACCESS_FAILURE"}])
        self.assertEqual(progress["complete_chunks"], 0)

    def test_child_output_is_redacted(self):
        env = {"FRED_API_KEY": SECRETS["fred_key"], "ALPACA_MARKET_DATA_API_KEY": SECRETS["alpaca_key"], "ALPACA_MARKET_DATA_API_SECRET": SECRETS["alpaca_secret"]}
        runner = stub_runner(code=1, output=f"Traceback url?api_key={SECRETS['fred_key']} header {SECRETS['alpaca_secret']}")
        progress, logs, _ = self.run_driver(stub_declaration(4), runner, env=env, time_budget_seconds=10_000)
        joined = "\n".join(logs) + json.dumps(progress)
        for secret in SECRETS.values():
            self.assertNotIn(secret, joined)
        self.assertIn("[REDACTED]", joined)

    def test_work_dir_inside_checkout_is_refused(self):
        with self.assertRaisesRegex(DRIVER.DriverError, "WORK_DIR_INSIDE_CHECKOUT_FORBIDDEN"):
            DRIVER.run(stub_declaration(4), ROOT / "tmp-replay", runner=stub_runner(), validator=stub_validator)

    def test_default_validator_is_the_population_module_and_rejects_stubs(self):
        declaration = stub_declaration(4)
        chunks_dir = self.tmp / "work" / "chunks"
        chunks_dir.mkdir(parents=True)
        chunk = DRIVER.plan_chunks(declaration["range"]["sessions"], 4)[0]
        (chunks_dir / chunk["file_name"]).write_text(json.dumps({"schema_version": "stub", "requested_dates": chunk["dates"], "records": []}))
        DRIVER.write_chunk_meta(chunks_dir, chunk, "c" * 64)
        self.assertEqual(DRIVER.chunk_state(chunk, chunks_dir), "INVALID")
        self.assertEqual(DRIVER.chunk_state(chunk, chunks_dir, validator=stub_validator), "COMPLETE")

    def test_finalize_incomplete_never_evaluates(self):
        declaration = stub_declaration(10)
        self.run_driver(declaration, stub_runner(), time_budget_seconds=1.5, monotonic=FakeClock(step=1.0))
        calls = []
        summary = DRIVER.finalize(declaration, self.tmp / "work", chunk_size=4, validator=stub_validator,
                                  evaluate=lambda *args: calls.append(args))
        self.assertEqual(summary["status"], "INCOMPLETE_RESUME_REQUIRED")
        self.assertIsNone(summary["acceptance"])
        self.assertEqual(calls, [])
        self.assertTrue(summary["incomplete_chunk_indexes"])

    def test_finalize_complete_evaluates_exact_declared_bundle_once(self):
        declaration = stub_declaration(10)
        self.run_driver(declaration, stub_runner(), time_budget_seconds=10_000)

        class StubPopulation:
            validated = []

            @staticmethod
            def payload_sha256(value):
                return CAL.payload_sha256(value)

            @classmethod
            def validate_population(cls, value):
                cls.validated.append(value["requested_dates"])

        calls = []

        def evaluate(market, bundle):
            calls.append((market, bundle["requested_dates"]))
            return {"market": market, "status": "NOT_ACCEPTED", "reasons": ["X"], "evaluated_date_count": 0,
                    "regimes_observed": [], "missing_regimes": ["RISK_ON"], "replay_report_sha256": None,
                    "contract_version": "c", "authority": {}}

        summary = DRIVER.finalize(declaration, self.tmp / "work", chunk_size=4, validator=stub_validator,
                                  population_module=StubPopulation, evaluate=evaluate)
        self.assertEqual(summary["status"], "EVALUATED")
        self.assertEqual(calls, [("US", declaration["range"]["sessions"])])
        self.assertEqual(StubPopulation.validated, [declaration["range"]["sessions"]])
        self.assertEqual(summary["acceptance"]["status"], "NOT_ACCEPTED")
        self.assertEqual(summary["record_status_counts"], {"FREE_AXES_OBSERVED": 10})
        self.assertNotIn(str(PRICE_MARKER), json.dumps(summary))

    def test_real_population_cli_offline_end_to_end(self):
        # The unmodified population CLI with no credentials makes no network
        # call: every axis blocks on the missing credential before any fetch.
        sessions = ["2019-03-01", "2019-03-04", "2019-03-05"]
        declaration = {
            "declaration_sha256": "e" * 64,
            "range": {"sessions": sessions, "first_replay_date": sessions[0], "last_replay_date": sessions[-1],
                      "session_count": 3, "sessions_sha256": CAL.payload_sha256(sessions)},
        }
        env = {"PATH": "/usr/bin:/bin", "FRED_API_KEY": "", "ALPACA_MARKET_DATA_API_KEY": "", "ALPACA_MARKET_DATA_API_SECRET": ""}
        progress = DRIVER.run(declaration, self.tmp / "work", chunk_size=2, env=env, sleep=lambda _: None,
                              log=lambda _: None, time_budget_seconds=10_000)
        # A missing credential is a provider-access failure, never a complete chunk.
        self.assertEqual(progress["failures_this_run"], [{"chunk": 1, "state": "PROVIDER_ACCESS_FAILURE"}])
        summary = DRIVER.finalize(declaration, self.tmp / "work", chunk_size=2)
        self.assertEqual(summary["status"], "INCOMPLETE_RESUME_REQUIRED")
        chunk = DRIVER.plan_chunks(sessions, 2)[0]
        population = json.loads((self.tmp / "work" / "chunks" / chunk["file_name"]).read_text())
        merged = DRIVER.merge_populations([population])  # real validate_population
        from regime import market_scoped_pit_acceptance as MSPA
        self.assertEqual(MSPA.evaluate_market_pit_acceptance("US", merged)["status"], "NOT_ACCEPTED")

    def test_merge_rejects_heterogeneous_or_overlapping_chunks(self):
        class StubPopulation:
            payload_sha256 = staticmethod(CAL.payload_sha256)
            validate_population = staticmethod(lambda value: None)

        first = {"schema_version": "s", "mode": "a", "requested_dates": ["2019-01-01"], "records": []}
        other_mode = {"schema_version": "s", "mode": "b", "requested_dates": ["2019-01-02"], "records": []}
        overlap = {"schema_version": "s", "mode": "a", "requested_dates": ["2019-01-01"], "records": []}
        with self.assertRaisesRegex(DRIVER.DriverError, "NOT_HOMOGENEOUS"):
            DRIVER.merge_populations([first, other_mode], population_module=StubPopulation)
        with self.assertRaisesRegex(DRIVER.DriverError, "OVERLAP"):
            DRIVER.merge_populations([first, overlap], population_module=StubPopulation)


if __name__ == "__main__":
    unittest.main()
