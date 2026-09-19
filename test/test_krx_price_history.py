#!/usr/bin/env python3
"""TKT-2 acceptance regression for the KR all-stock daily price history.

Every fixture below is shaped from evidence already committed to this
repository -- the real 2026 KRX official holiday capture, the real
``krx_global_universe`` 2026-09-10 population (2,766 codes; KOSPI 943 /
KOSDAQ 1,823), and the real KRX OpenAPI row field set.  No test performs
or permits a network call: the collector's ``fetch_part`` has no default
opener, so every transport used here is an explicit local fake.
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal
import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from collectors import krx_price_history as SUBJECT  # noqa: E402
from universe import price_history_store as STORE  # noqa: E402


CALENDAR_2026 = (
    ROOT / "evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json"
)
UNIVERSE_20260910 = (
    ROOT / "data/observations/krx_global_universe/2026-09-10/packet.json"
)
COMMIT = "0" * 40


def universe_codes() -> dict[str, list[str]]:
    """The real 2026-09-10 population, split by its real KOSPI/KOSDAQ membership."""
    packet = json.loads(UNIVERSE_20260910.read_bytes().decode("utf-8"))
    split: dict[str, list[str]] = {"kospi": [], "kosdaq": []}
    for record in packet["asset_master"]["records"]:
        memberships = {
            item.get("membership_id") for item in record.get("active_memberships") or []
        }
        part = "kospi" if "KOSPI" in memberships else "kosdaq"
        split[part].append(record["primary_symbol"])
    return {part: sorted(codes) for part, codes in split.items()}


def universe_total_count() -> int:
    return json.loads(UNIVERSE_20260910.read_bytes().decode("utf-8"))["total_count"]


def provider_row(code: str, bas_dd: str, part: str, *, seed: int, list_shrs: str) -> dict:
    """One row with the exact KRX OpenAPI stock-daily field set."""
    close = 1000 + seed * 5
    return {
        "BAS_DD": bas_dd,
        "ISU_CD": code,
        "ISU_NM": f"FIXTURE{code}",
        "MKT_NM": "KOSPI" if part == "kospi" else "KOSDAQ",
        "SECT_TP_NM": "",
        "TDD_CLSPRC": str(close),
        "CMPPREVDD_PRC": "5",
        "FLUC_RT": "0.50",
        "TDD_OPNPRC": str(close - 5),
        "TDD_HGPRC": str(close + 10),
        "TDD_LWPRC": str(close - 10),
        "ACC_TRDVOL": str(10000 + seed),
        "ACC_TRDVAL": str((10000 + seed) * close),
        "MKTCAP": str(close * int(list_shrs)),
        "LIST_SHRS": list_shrs,
    }


def provider_payload(codes: list[str], bas_dd: str, part: str, *, shares=None) -> bytes:
    shares = shares or {}
    rows = [
        provider_row(
            code,
            bas_dd,
            part,
            seed=index,
            list_shrs=shares.get(code, "8941100"),
        )
        for index, code in enumerate(codes)
    ]
    return json.dumps(
        {"OutBlock_1": rows}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


class Response:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def getcode(self):
        return self.status

    def read(self):
        return self._body


def opener_for(payloads: dict[str, bytes]):
    """A local transport keyed by the request URL; never reaches a network."""
    calls: list[str] = []

    def _open(request, timeout=None):
        url = request.full_url
        calls.append(url)
        for marker, body in payloads.items():
            if marker in url:
                return Response(body)
        raise AssertionError(f"UNEXPECTED_URL:{url}")

    _open.calls = calls
    return _open


def attempt(kind: str, requested: str, retrieved: str, rows: int, outcome: str) -> dict:
    return {
        "attempt_no": 1,
        "kind": kind,
        "requested_at_utc": requested,
        "retrieved_at_utc": retrieved,
        "http_status": 200,
        "row_count": rows,
        "outcome": outcome,
    }


class PriceHistoryContractTests(unittest.TestCase):
    def setUp(self):
        self.contract = SUBJECT.load_contract()

    def test_contract_is_market_agnostic_and_reserves_us(self):
        self.assertTrue(self.contract["market_agnostic"])
        self.assertEqual(self.contract["markets"], ["KR", "US"])
        self.assertEqual(self.contract["implemented_markets"], ["KR"])
        self.assertIsNone(self.contract["sources"]["US"])
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "MARKET_SOURCE_NOT_DEFINED"):
            SUBJECT.market_source(self.contract, "US")
        self.assertEqual(
            self.contract["compact_row_fields"],
            ["code", "open", "high", "low", "close", "cmpprevdd",
             "fluc_rt", "vol", "value", "mktcap", "list_shrs"],
        )

    def test_ac9_contract_requires_request_count_and_interval(self):
        """AC9 -- the backfill receipt fields are contractual, not incidental."""
        must = self.contract["backfill"]["receipt_must_record"]
        self.assertIn("request_count", must)
        self.assertIn("inter_request_interval_seconds", must)
        self.assertEqual(self.contract["backfill"]["sessions"], 260)
        self.assertEqual(self.contract["backfill"]["single_burst"], "PROHIBITED")
        self.assertEqual(
            self.contract["backfill"]["provider_daily_request_limit"], "UNVERIFIED"
        )

    def test_authority_is_entirely_false(self):
        granted = [
            key for key, value in self.contract["authority"].items()
            if value and key != "price_history_observation_only"
        ]
        self.assertEqual(granted, [])


class SessionDerivationTests(unittest.TestCase):
    def setUp(self):
        self.contract = SUBJECT.load_contract()
        self.codes = universe_codes()
        self.calendar = CALENDAR_2026.read_bytes()
        self.calendar_ref = CALENDAR_2026.relative_to(ROOT).as_posix()

    def raw(self, bas_dd: str, *, shares=None) -> dict[str, bytes]:
        return {
            part: provider_payload(self.codes[part], bas_dd, part, shares=shares)
            for part in ("kospi", "kosdaq")
        }

    def test_ac2_row_count_equals_universe_total_count(self):
        """AC2 -- compact row count equals that date's krx_global_universe total."""
        rows = SUBJECT.derive_compact_rows(self.raw("20260910"), "20260910", self.contract)
        self.assertEqual(len(rows), universe_total_count())
        self.assertEqual(len(rows), 2766)

    def test_ac3_compact_is_rederived_from_raw(self):
        """AC3 -- compact.jsonl is a pure function of the raw provider bytes."""
        raw = self.raw("20260910")
        stored = SUBJECT.compact_bytes(
            SUBJECT.derive_compact_rows(raw, "20260910", self.contract)
        )
        verdict = SUBJECT.rederive_compact(raw, "20260910", stored, self.contract)
        self.assertTrue(verdict["rederived"])
        self.assertEqual(
            verdict["stored_compact_sha256"], verdict["rederived_compact_sha256"]
        )
        tampered = stored.replace(b'"1000"', b'"1001"', 1)
        self.assertFalse(
            SUBJECT.rederive_compact(raw, "20260910", tampered, self.contract)["rederived"]
        )

    def test_compact_field_order_and_value_normalisation(self):
        raw = self.raw("20260910")
        parsed = SUBJECT.parse_compact(
            SUBJECT.compact_bytes(
                SUBJECT.derive_compact_rows(raw, "20260910", self.contract)
            ),
            self.contract,
        )
        first = parsed[0]
        self.assertEqual(list(first), self.contract["compact_row_fields"])
        self.assertEqual(first["code"], "000020")
        self.assertIsInstance(first["close"], str)

    def test_ac5_holiday_bas_dd_is_rejected(self):
        """AC5 -- a closed date is refused, from the official calendar alone."""
        # 2026-01-01 (New Year's Day) is listed by the official KRX capture.
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "SESSION_CLOSED:20260101"):
            SUBJECT.assert_collectable(
                "20260101",
                now_utc=dt.datetime(2026, 1, 2, tzinfo=dt.timezone.utc),
                calendar_capture_raw=self.calendar,
                calendar_source_ref=self.calendar_ref,
                contract=self.contract,
            )
        # A weekend is equally refused.
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "SESSION_CLOSED:20260912"):
            SUBJECT.assert_collectable(
                "20260912",
                now_utc=dt.datetime(2026, 9, 14, tzinfo=dt.timezone.utc),
                calendar_capture_raw=self.calendar,
                calendar_source_ref=self.calendar_ref,
                contract=self.contract,
            )

    def test_holiday_status_is_never_inferred_from_an_empty_response(self):
        raw = {part: json.dumps({"OutBlock_1": []}).encode("utf-8")
               for part in ("kospi", "kosdaq")}
        self.assertEqual(SUBJECT.classify(raw, "20260910"), "EMPTY")
        verdict = SUBJECT.session_status(
            "20260910", self.calendar, source_ref=self.calendar_ref,
            contract=self.contract,
        )
        self.assertEqual(verdict["status"], "OPEN_REGULAR")

    def test_missing_calendar_year_fails_closed(self):
        """The 2025 backfill year has no committed capture yet: fail, never assume."""
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "CALENDAR_CAPTURE_MISSING:2025"):
            SUBJECT.resolve_calendar_capture("20250102", self.contract)
        self.assertEqual(
            SUBJECT.resolve_calendar_capture("20260910", self.contract).name,
            "capture-2026.json",
        )

    def test_2025_calendar_derivation_works_once_a_real_capture_exists(self):
        """Offline proof of the 2025 path; the real capture is a human/CI network run.

        The capture below is synthesised in a temp directory from the exact
        2026 capture's own schema.  It is deliberately never committed to
        ``evidence/`` -- a fabricated official capture would be indistinguishable
        from a real one.
        """
        import base64

        capture = json.loads(self.calendar.decode("utf-8"))
        provider = {
            "block1": [
                {"calnd_dd": "2025-01-01", "dy_tp_cd": "WED",
                 "calnd_dd_dy": "2025-01-01", "kr_dy_tp": "Wednesday",
                 "holdy_eng_nm": "New Year's Day"},
            ]
        }
        raw = json.dumps(provider, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        capture["year"] = 2025
        capture["request"]["search_bas_yy"] = "2025"
        capture["response"]["raw_base64"] = base64.b64encode(raw).decode("ascii")
        capture["response"]["raw_sha256"] = SUBJECT.digest(raw)
        capture_raw = SUBJECT.calendar_module().canonical_bytes(capture)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "capture-2025.json"
            path.write_bytes(capture_raw)
            closed = SUBJECT.session_status(
                "20250101", capture_raw, source_ref=path.as_posix(),
                contract=self.contract,
            )
            self.assertEqual(closed["status"], "CLOSED")
            open_day = SUBJECT.session_status(
                "20250102", capture_raw, source_ref=path.as_posix(),
                contract=self.contract,
            )
            self.assertEqual(open_day["status"], "OPEN_REGULAR")

    def test_pre_publication_collection_is_refused(self):
        """Same-day evening is refused; next morning 09:10 KST is the earliest collection."""
        self.assertEqual(
            SUBJECT.earliest_collection_instant("20260910", self.contract).isoformat(),
            "2026-09-11T09:10:00+09:00",
        )
        for too_early in (
            dt.datetime(2026, 9, 10, 9, 13, tzinfo=dt.timezone.utc),  # 18:13 KST same day
            dt.datetime(2026, 9, 11, 0, 9, tzinfo=dt.timezone.utc),   # 09:09 KST next day
        ):
            with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "PRE_PUBLICATION_COLLECTION_REFUSED"):
                SUBJECT.assert_collectable(
                    "20260910", now_utc=too_early,
                    calendar_capture_raw=self.calendar,
                    calendar_source_ref=self.calendar_ref, contract=self.contract,
                )
        allowed = SUBJECT.assert_collectable(
            "20260910",
            now_utc=dt.datetime(2026, 9, 11, 0, 10, tzinfo=dt.timezone.utc),  # 09:10 KST
            calendar_capture_raw=self.calendar,
            calendar_source_ref=self.calendar_ref, contract=self.contract,
        )
        self.assertTrue(allowed["collectable"])

    def test_forward_target_is_previous_open_session_by_calendar(self):
        kst = dt.timezone(dt.timedelta(hours=9))
        cases = {
            dt.datetime(2026, 9, 11, 9, 10, tzinfo=kst): "20260910",  # Fri -> Thu
            dt.datetime(2026, 9, 14, 9, 10, tzinfo=kst): "20260911",  # Mon -> Fri
            dt.datetime(2026, 8, 18, 9, 10, tzinfo=kst): "20260814",  # Tue after 08-17 holiday -> Fri
        }
        for now, expected in cases.items():
            self.assertEqual(SUBJECT.forward_target_session(now, self.contract), expected, now)

    def test_open_sessions_ending_follows_the_official_calendar(self):
        window = SUBJECT.open_sessions_ending("20260914", 20, self.contract)
        self.assertEqual(len(window), 20)
        self.assertEqual(window[-1], "20260914")
        self.assertEqual(window[0], "20260818")  # 08-17 substitute holiday skipped
        self.assertIn("20260901", window)
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "END_SESSION_NOT_OPEN"):
            SUBJECT.open_sessions_ending("20260913", 20, self.contract)

    def test_partial_part_emptiness_fails_closed(self):
        raw = self.raw("20260910")
        raw["kosdaq"] = json.dumps({"OutBlock_1": []}).encode("utf-8")
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "PART_ROW_COUNT_ZERO:kosdaq"):
            SUBJECT.classify(raw, "20260910")

    def test_corporate_action_suspect_on_list_shrs_discontinuity(self):
        previous = SUBJECT.parse_compact(
            SUBJECT.compact_bytes(
                SUBJECT.derive_compact_rows(self.raw("20260910"), "20260910", self.contract)
            ),
            self.contract,
        )
        current = SUBJECT.parse_compact(
            SUBJECT.compact_bytes(
                SUBJECT.derive_compact_rows(
                    self.raw("20260911", shares={"005930": "5000000000"}),
                    "20260911",
                    self.contract,
                )
            ),
            self.contract,
        )
        suspects = SUBJECT.corporate_action_suspects(previous, current)
        self.assertEqual([item["code"] for item in suspects], ["005930"])
        self.assertEqual(suspects[0]["flag"], "CORPORATE_ACTION_SUSPECT")
        self.assertEqual(SUBJECT.corporate_action_suspects(None, current), [])
        self.assertEqual(SUBJECT.corporate_action_suspects(previous, previous), [])

    def test_returns_compound_fluc_rt_against_known_values(self):
        """``adjustment: NONE`` -- a window return is FLUC_RT compounded."""
        rows = [{"fluc_rt": "0.50"}, {"fluc_rt": "-1.25"}, {"fluc_rt": "2.00"}]
        expected = Decimal("1.005") * Decimal("0.9875") * Decimal("1.02")
        self.assertEqual(SUBJECT.compound_return(rows), expected)
        self.assertEqual(SUBJECT.session_gross_factor({"fluc_rt": "0.50"}), Decimal("1.005"))
        self.assertEqual(
            SUBJECT.implied_previous_close({"close": "1000", "cmpprevdd": "5"}),
            Decimal("995"),
        )
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "RETURN_WINDOW_EMPTY"):
            SUBJECT.compound_return([])

    def test_fetch_part_requires_an_injected_opener(self):
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "OPENER_REQUIRED"):
            SUBJECT.fetch_part("key", "20260910", "kospi", opener=None)
        raw = self.raw("20260910")
        fake = opener_for({"stk_bydd_trd": raw["kospi"], "ksq_bydd_trd": raw["kosdaq"]})
        part = SUBJECT.fetch_part("key", "20260910", "kospi", opener=fake)
        self.assertEqual(part["http_status"], 200)
        self.assertEqual(part["raw_sha256"], SUBJECT.digest(raw["kospi"]))
        self.assertEqual(len(fake.calls), 1)
        self.assertNotIn("key", part["endpoint"])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.contract = SUBJECT.load_contract()
        self.codes = {"kospi": ["005930", "000660"], "kosdaq": ["035720"]}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = STORE.PriceHistoryStore(self.tmp.name, contract=self.contract)

    def raw(self, bas_dd, *, codes=None, shares=None):
        codes = codes or self.codes
        return {
            part: provider_payload(codes[part], bas_dd, part, shares=shares)
            for part in sorted(codes)
        }

    def store_session(
        self, bas_dd, *, pit_class="FORWARD_CAPTURE", codes=None,
        available="2026-09-10T08:00:00Z", shares=None, previous=None,
    ):
        raw = self.raw(bas_dd, codes=codes, shares=shares)
        rows = SUBJECT.derive_compact_rows(raw, bas_dd, self.contract)
        compact = SUBJECT.compact_bytes(rows)
        parts = [
            {
                "part_id": part,
                "endpoint": f"https://data-dbg.krx.co.kr/svc/apis/sto/"
                            f"{'stk' if part == 'kospi' else 'ksq'}_bydd_trd",
                "http_status": 200,
                "row_count": len((codes or self.codes)[part]),
                "raw_relpath": f"raw/{part}.json.gz",
                "raw_sha256": SUBJECT.digest(raw[part]),
                "raw_byte_count": len(raw[part]),
            }
            for part in sorted(raw)
        ]
        kind = "BACKFILL" if pit_class == "HISTORICAL_BACKFILL" else "FORWARD_PRIMARY"
        manifest = SUBJECT.build_manifest(
            market="KR", bas_dd=bas_dd, status="OK", parts=parts, compact=compact,
            compact_row_count=len(rows), pit_class=pit_class,
            attempts=[attempt(kind, available, available, sum(p["row_count"] for p in parts), "OK")],
            public_code_commit=COMMIT, contract=self.contract,
            corporate_actions=SUBJECT.corporate_action_suspects(
                previous, SUBJECT.parse_compact(compact, self.contract)
            ),
            first_available_observed_at_utc=available,
        )
        self.store.write_session("KR", manifest, raw_by_part=raw, compact=compact)
        self.store.append_index("KR", manifest, observed_at_utc=available)
        return manifest, SUBJECT.parse_compact(compact, self.contract)

    def test_ac1_two_consecutive_sessions_and_every_manifest_field(self):
        """AC1 -- two consecutive trading days stored, every manifest field present."""
        first, rows = self.store_session("20260910", available="2026-09-10T08:00:00Z")
        second, _ = self.store_session(
            "20260911", available="2026-09-11T08:00:00Z", previous=rows
        )
        required = set(self.contract["manifest_core_fields"])
        for manifest in (first, second):
            self.assertTrue(required.issubset(set(manifest)), required - set(manifest))
            for field in required:
                self.assertIn(field, manifest)
            self.assertEqual(manifest["authority"], self.contract["authority"])
        self.assertEqual(
            self.store.sessions_available_at("KR", "2026-09-12T00:00:00Z"),
            ["20260910", "20260911"],
        )
        series = self.store.series("KR", "005930", 2, "2026-09-12T00:00:00Z")
        self.assertEqual([row["bas_dd"] for row in series], ["20260910", "20260911"])

    def test_ac4_zero_rows_recorded_as_empty_and_not_committed(self):
        """AC4 -- an empty response becomes an EMPTY manifest, never session data."""
        empty = {part: json.dumps({"OutBlock_1": []}).encode("utf-8")
                 for part in ("kospi", "kosdaq")}
        self.assertEqual(SUBJECT.classify(empty, "20260910"), "EMPTY")
        parts = [
            {"part_id": part, "endpoint": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
             "http_status": 200, "row_count": 0, "raw_relpath": f"raw/{part}.json.gz",
             "raw_sha256": SUBJECT.digest(empty[part]), "raw_byte_count": len(empty[part])}
            for part in sorted(empty)
        ]
        manifest = SUBJECT.build_manifest(
            market="KR", bas_dd="20260910", status="EMPTY", parts=parts,
            compact=b"", compact_row_count=0, pit_class="FORWARD_CAPTURE",
            attempts=[attempt("FORWARD_PRIMARY", "2026-09-10T08:00:00Z",
                              "2026-09-10T08:00:00Z", 0, "EMPTY")],
            public_code_commit=COMMIT, contract=self.contract,
        )
        self.assertEqual(manifest["status"], "EMPTY")
        self.assertIsNone(manifest["first_available_observed_at_utc"])
        directory = self.store.write_session("KR", manifest, raw_by_part=empty, compact=b"")
        self.assertFalse((directory / "compact.jsonl.gz").exists())
        self.assertFalse((directory / "raw").exists())
        self.assertTrue((directory / "manifest.json").is_file())
        # An EMPTY session is history, not data: it is never "available".
        self.assertEqual(self.store.sessions_available_at("KR", "2026-09-30T00:00:00Z"), [])
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "EMPTY_STATUS_CLAIMS_AVAILABILITY"):
            SUBJECT.build_manifest(
                market="KR", bas_dd="20260910", status="EMPTY", parts=parts,
                compact=b"", compact_row_count=0, pit_class="FORWARD_CAPTURE",
                attempts=[attempt("FORWARD_PRIMARY", "2026-09-10T08:00:00Z",
                                  "2026-09-10T08:00:00Z", 0, "EMPTY")],
                public_code_commit=COMMIT, contract=self.contract,
                first_available_observed_at_utc="2026-09-10T08:00:00Z",
            )

    def test_ac6_pit_class_tagged_on_manifest_and_index(self):
        """AC6 -- a backfilled session is tagged HISTORICAL_BACKFILL end to end."""
        manifest, _ = self.store_session(
            "20260908", pit_class="HISTORICAL_BACKFILL", available="2026-09-12T02:00:00Z"
        )
        self.assertEqual(manifest["pit_class"], "HISTORICAL_BACKFILL")
        index = self.store.index_rows("KR")
        self.assertEqual(index[-1]["pit_class"], "HISTORICAL_BACKFILL")
        forward, _ = self.store_session("20260910")
        self.assertEqual(forward["pit_class"], "FORWARD_CAPTURE")
        with self.assertRaisesRegex(SUBJECT.PriceHistoryError, "PIT_CLASS_UNSUPPORTED"):
            SUBJECT.build_manifest(
                market="KR", bas_dd="20260910", status="EMPTY", parts=[],
                compact=b"", compact_row_count=0, pit_class="GUESSED",
                attempts=[attempt("BACKFILL", "2026-09-10T08:00:00Z",
                                  "2026-09-10T08:00:00Z", 0, "EMPTY")],
                public_code_commit=COMMIT, contract=self.contract,
            )

    def test_ac7_sma20_computable_symbol_count_reported(self):
        """AC7 -- the store reports how many symbols can carry a 20-session SMA."""
        codes = {"kospi": ["005930", "000660"], "kosdaq": ["035720"]}
        partial = {"kospi": ["005930", "000660"], "kosdaq": []}
        for offset in range(20):
            day = dt.date(2026, 1, 5) + dt.timedelta(days=offset)
            # 035720 is absent from the last session: its window has a gap.
            use = partial if offset == 19 else codes
            self.store_session(
                day.strftime("%Y%m%d"), codes=use, available="2026-02-20T08:00:00Z"
            )
        report = self.store.sma_readiness("KR", "2026-03-01T00:00:00Z")
        self.assertEqual(report["status"], "COMPUTABLE")
        self.assertEqual(report["sma_sessions"], 20)
        self.assertEqual(report["available_sessions"], 20)
        self.assertEqual(report["sma_computable_symbol_count"], 2)
        self.assertEqual(report["symbols_short_of_window"], {"035720": 19})

    def test_sma_readiness_reports_insufficient_rather_than_estimating(self):
        self.store_session("20260910")
        report = self.store.sma_readiness("KR", "2026-09-30T00:00:00Z")
        self.assertEqual(report["status"], "INSUFFICIENT_SESSIONS")
        self.assertEqual(report["sma_computable_symbol_count"], 0)

    def test_series_leaves_gaps_as_gaps(self):
        self.store_session("20260910", available="2026-09-10T08:00:00Z")
        self.store_session(
            "20260911",
            codes={"kospi": ["005930"], "kosdaq": []},
            available="2026-09-11T08:00:00Z",
        )
        window = self.store.session_window("KR", 2, "2026-09-12T00:00:00Z")
        self.assertEqual(window, ["20260910", "20260911"])
        missing = self.store.series("KR", "035720", 2, "2026-09-12T00:00:00Z")
        self.assertEqual([row["bas_dd"] for row in missing], ["20260910"])
        self.assertLess(len(missing), len(window))

    def test_series_respects_point_in_time_availability(self):
        self.store_session("20260910", available="2026-09-10T08:00:00Z")
        self.store_session("20260911", available="2026-09-11T08:00:00Z")
        self.assertEqual(
            self.store.sessions_available_at("KR", "2026-09-10T23:59:59Z"), ["20260910"]
        )
        self.assertEqual(
            [row["bas_dd"] for row in self.store.series("KR", "005930", 5, "2026-09-10T23:59:59Z")],
            ["20260910"],
        )

    def test_index_is_append_only_with_revision_observed(self):
        manifest, _ = self.store_session("20260910")
        self.assertEqual(len(self.store.index_rows("KR")), 1)
        self.assertEqual(self.store.index_rows("KR")[0]["event"], "OBSERVED")
        again = self.store.append_index("KR", manifest, observed_at_utc="2026-09-10T09:00:00Z")
        self.assertEqual(again["event"], "NO_NEW_ROW")
        self.assertEqual(len(self.store.index_rows("KR")), 1)
        revised = dict(manifest)
        revised["compact_sha256"] = "b" * 64
        result = self.store.append_index("KR", revised, observed_at_utc="2026-09-11T09:00:00Z")
        self.assertEqual(result["event"], "REVISION_OBSERVED")
        rows = self.store.index_rows("KR")
        self.assertEqual([row["event"] for row in rows], ["OBSERVED", "REVISION_OBSERVED"])
        self.assertEqual(rows[0]["compact_sha256"], manifest["compact_sha256"])

    def test_session_is_never_overwritten(self):
        self.store_session("20260910")
        with self.assertRaisesRegex(STORE.PriceHistoryStoreError, "SESSION_ALREADY_STORED"):
            self.store_session("20260910")

    def test_stored_raw_and_compact_round_trip(self):
        manifest, rows = self.store_session("20260910")
        directory = self.store.session_dir("KR", "20260910")
        raw = {
            part["part_id"]: gzip.decompress(
                (directory / part["raw_relpath"]).read_bytes()
            )
            for part in manifest["parts"]
        }
        stored_compact = gzip.decompress((directory / "compact.jsonl.gz").read_bytes())
        verdict = SUBJECT.rederive_compact(raw, "20260910", stored_compact, self.contract)
        self.assertTrue(verdict["rederived"])
        self.assertEqual(manifest["raw_sha256"], SUBJECT.raw_sha256(raw))
        self.assertEqual(self.store.compact_rows("KR", "20260910"), rows)

    def test_behaviour_features_are_computed_by_market_behavior(self):
        """RS and volume come from discovery/market_behavior.py, not from here."""
        for offset in range(3):
            day = dt.date(2026, 9, 8) + dt.timedelta(days=offset)
            self.store_session(day.strftime("%Y%m%d"), available="2026-09-11T08:00:00Z")
        features = self.store.behavior_features(
            "KR", ["005930", "000660"], "005930", 3, "2026-09-12T00:00:00Z",
            window_id="KR_PRICE_HISTORY_3S",
        )
        self.assertEqual(features["market"], "KOREA")
        by_asset = {item["asset_id"]: item for item in features["features"]}
        self.assertEqual(by_asset["005930"]["relative_strength_vs_benchmark"],
                         "0.000000000000")
        self.assertTrue(by_asset["000660"]["is_benchmark"] is False)
        self.assertIn("latest_volume_vs_prior_mean", by_asset["000660"])


class PublicBoundaryTests(unittest.TestCase):
    def test_ac8_public_repository_tracks_no_price_history_bytes(self):
        """AC8 -- the public repo tracks collector/reader code only, zero price bytes."""
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        offenders = [
            path for path in tracked
            if path.startswith("price_history/")
            or path.endswith("compact.jsonl.gz")
            or "/price_history/" in path
        ]
        self.assertEqual(offenders, [])
        self.assertEqual(
            SUBJECT.load_contract()["storage"]["public_tracked_price_bytes"], 0
        )
        self.assertEqual(SUBJECT.load_contract()["storage"]["repository"], "private")

    def test_no_module_level_default_network_transport(self):
        source = (ROOT / "collectors" / "krx_price_history.py").read_text(encoding="utf-8")
        self.assertNotIn("urlopen", source)
        self.assertNotIn("import requests", source)
        self.assertIn("opener: Callable", source)
        store_source = (ROOT / "universe" / "price_history_store.py").read_text(
            encoding="utf-8"
        )
        for banned in ("urlopen", "urllib", "requests", "http.client", "socket"):
            self.assertNotIn(banned, store_source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
