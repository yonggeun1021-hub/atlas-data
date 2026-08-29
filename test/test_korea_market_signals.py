#!/usr/bin/env python3
"""Official KRX Korea five-signal observation regression."""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_market_signals.py"
SPEC = importlib.util.spec_from_file_location("korea_market_signals_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
TOKEN = "KRX-SECRET-NEVER-PERSIST"


class FakeResponse:
    def __init__(self, body: bytes):
        self.body = body
        self.status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self.body

    def getcode(self):
        return self.status


class FakeOpener:
    def __init__(self, payloads):
        self.payloads = payloads
        self.calls = []

    def __call__(self, request, timeout=30):
        parsed = urlparse(request.full_url)
        day = parse_qs(parsed.query)["basDd"][0]
        if "/sto/stk_" in parsed.path:
            family, market = "stock", "kospi"
        elif "/sto/ksq_" in parsed.path:
            family, market = "stock", "kosdaq"
        elif "/idx/kospi_" in parsed.path:
            family, market = "index", "kospi"
        else:
            family, market = "index", "kosdaq"
        self.calls.append((family, market, day))
        body = json.dumps(self.payloads[(family, market, day)], ensure_ascii=False).encode()
        return FakeResponse(body)


def stock_row(day, code, close, move, value, cap):
    return {
        "BAS_DD": day,
        "ISU_CD": code,
        "TDD_CLSPRC": str(close),
        "FLUC_RT": str(move),
        "ACC_TRDVAL": str(value),
        "MKTCAP": str(cap),
    }


def index_row(day, name, close):
    return {"BAS_DD": day, "IDX_NM": name, "CLSPRC_IDX": str(close)}


def fixtures(previous="20260827", current="20260828"):
    values = {}
    for day, step in ((previous, 0), (current, 1)):
        values[("stock", "kospi", day)] = {"OutBlock_1": [
            stock_row(day, "K-A", 100 + step * 10, 10 if step else 0, 1000 + step * 100, 10000),
            stock_row(day, "K-B", 100 - step * 5, -5 if step else 0, 500 + step * 50, 5000),
            stock_row(day, "K-C", 100, 0, 250, 2500),
        ]}
        values[("stock", "kosdaq", day)] = {"OutBlock_1": [
            stock_row(day, "Q-A", 50 + step * 5, 10 if step else 0, 700 + step * 70, 7000),
            stock_row(day, "Q-B", 50 - step * 2, -4 if step else 0, 300 + step * 30, 3000),
        ]}
        values[("index", "kospi", day)] = {"OutBlock_1": [
            index_row(day, "코스피", 3000 + step * 30),
            index_row(day, "화학", 100 + step * 3),
            index_row(day, "금융", 100 - step),
        ]}
        values[("index", "kosdaq", day)] = {"OutBlock_1": [
            index_row(day, "코스닥", 900 + step * 18),
            index_row(day, "제약", 100 + step * 4),
            index_row(day, "전기전자", 100 - step * 2),
        ]}
    return values


def session(opener, day):
    with mock.patch.object(MODULE, "_now_utc", return_value="2026-08-28T09:20:00Z"):
        return MODULE.fetch_complete_session(TOKEN, day, opener=opener)


class KoreaMarketSignalsTest(unittest.TestCase):
    def test_contract_keeps_every_decision_and_trading_authority_closed(self):
        contract = MODULE.load_contract()
        self.assertEqual(contract["required_axes"], [
            "TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP",
        ])
        self.assertEqual(contract["raw_persistence"], 0)
        self.assertEqual(contract["per_symbol_persistence"], 0)
        self.assertTrue(contract["authority"]["observation_only"])
        for key, value in contract["authority"].items():
            if key.endswith("_authorized"):
                self.assertFalse(value, key)

    def test_official_pair_builds_five_plain_measurements(self):
        opener = FakeOpener(fixtures())
        previous = session(opener, "20260827")
        current = session(opener, "20260828")
        packet = MODULE.build_packet(previous, current)
        self.assertEqual(packet["status"], "OBSERVED_UNCLASSIFIED")
        self.assertEqual(packet["coverage"]["ratio"], "5/5")
        self.assertEqual(set(packet["axes"]), {
            "TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP",
        })
        self.assertEqual(
            packet["axes"]["TREND"]["measurement"]["benchmarks"]["KOSPI"]["one_session_return_pct"],
            "1.000000",
        )
        combined = packet["axes"]["BREADTH"]["measurement"]["combined"]
        self.assertEqual(combined["paired_count"], 5)
        self.assertEqual(combined["advancing_count"], 2)
        self.assertEqual(combined["declining_count"], 2)
        self.assertEqual(combined["unchanged_count"], 1)
        self.assertEqual(
            packet["axes"]["LIQUIDITY"]["measurement"]["combined"]["trading_value_change_pct"],
            "9.090909",
        )
        self.assertGreater(
            len(packet["axes"]["LEADERSHIP"]["measurement"]["observations"]), 0
        )
        self.assertFalse(
            packet["axes"]["LEADERSHIP"]["measurement"]["investment_ranking_authorized"]
        )
        self.assertEqual(MODULE.validate_packet(packet), packet)

    def test_index_parser_ignores_blank_non_series_rows(self):
        payload = {"OutBlock_1": [
            {"BAS_DD": "20260828", "IDX_NM": "분류", "CLSPRC_IDX": ""},
            {"BAS_DD": "20260828", "IDX_NM": "", "CLSPRC_IDX": "123.45"},
            index_row("20260828", "코스피", 3210.5),
        ]}
        parsed = MODULE._index_snapshot(payload, "20260828", "kospi")
        self.assertEqual(set(parsed["indices"]), {"코스피"})
        self.assertEqual(parsed["indices"]["코스피"], MODULE.Decimal("3210.5"))

        with self.assertRaisesRegex(MODULE.KoreaMarketSignalsError, "KRX_RESPONSE_EMPTY"):
            MODULE._index_snapshot(
                {"OutBlock_1": [{"BAS_DD": "20260828", "IDX_NM": "분류", "CLSPRC_IDX": ""}]},
                "20260828",
                "kospi",
            )

    def test_no_secret_raw_price_or_per_symbol_identity_is_persisted(self):
        opener = FakeOpener(fixtures())
        packet = MODULE.build_packet(
            session(opener, "20260827"), session(opener, "20260828")
        )
        rendered = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn(TOKEN, rendered)
        for identity in ("K-A", "K-B", "K-C", "Q-A", "Q-B"):
            self.assertNotIn(identity, rendered)
        for forbidden_key in ("TDD_CLSPRC", "ISU_CD"):
            self.assertNotIn(forbidden_key, rendered)

    def test_append_only_same_date_conflict_and_hash_tamper_fail_closed(self):
        opener = FakeOpener(fixtures())
        packet = MODULE.build_packet(
            session(opener, "20260827"), session(opener, "20260828")
        )
        with tempfile.TemporaryDirectory() as tmp:
            MODULE.publish(packet, Path(tmp))
            self.assertEqual(MODULE.publish(packet, Path(tmp))["observation_path"].endswith("packet.json"), True)
            tampered = copy.deepcopy(packet)
            tampered["axes"]["TREND"]["measurement"]["benchmarks"]["KOSPI"]["one_session_return_pct"] = "99.000000"
            with self.assertRaisesRegex(MODULE.KoreaMarketSignalsError, "PACKET_HASH_INVALID"):
                MODULE.publish(tampered, Path(tmp))

    def test_discovery_skips_non_sessions_and_returns_latest_two_complete_dates(self):
        payloads = fixtures("20260827", "20260828")
        for family in ("stock", "index"):
            for market in MODULE.MARKETS:
                payloads[(family, market, "20260829")] = {"OutBlock_1": []}
        opener = FakeOpener(payloads)
        with mock.patch.object(MODULE, "_now_utc", return_value="2026-08-29T05:00:00Z"):
            previous, current = MODULE.discover_session_pair(
                TOKEN, anchor=MODULE.dt.date(2026, 8, 29), opener=opener
            )
        self.assertEqual(previous["date"], "20260827")
        self.assertEqual(current["date"], "20260828")

    def test_explicit_pair_reuses_committed_packet_without_provider_call(self):
        opener = FakeOpener(fixtures())
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with mock.patch.object(MODULE, "_now_utc", return_value="2026-08-28T09:20:00Z"):
                first = MODULE.run(
                    TOKEN,
                    previous_date="20260827",
                    current_date="20260828",
                    opener=opener,
                    root=root,
                )
            calls = len(opener.calls)
            second = MODULE.run(
                "",
                previous_date="20260827",
                current_date="20260828",
                opener=opener,
                root=root,
            )
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(len(opener.calls), calls)


if __name__ == "__main__":
    unittest.main()
