#!/usr/bin/env python3
"""P1-COM-05 CIO mandate 2026-09-04 — KR 5-axis historical replay population.

SHADOW historical-backfill evidence only, never NATURAL. Offline/fixture-only:
no real KRX network call is required or attempted here.
"""

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
SCRIPT = ROOT / "regime" / "kr_historical_replay_population.py"
SPEC = importlib.util.spec_from_file_location("kr_historical_replay_population_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

KMS = MODULE.KMS
PRR = MODULE.PRR
TOKEN = "KRX-SECRET-NEVER-PERSIST"
FIXED_NOW = "2026-09-04T09:20:00Z"


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
    """Serves fixture bytes for known (family, market, day) triples and a
    KRX-shaped empty response (matching real KRX_RESPONSE_EMPTY semantics —
    a real non-trading-day KRX response, never a network error) for
    everything else, so backward session-pair discovery over weekends and
    unrequested dates behaves exactly like it does against the real API."""

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
        payload = self.payloads.get((family, market, day), {"OutBlock_1": []})
        body = json.dumps(payload, ensure_ascii=False).encode()
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


TRADING_DAYS = ["20260826", "20260827", "20260828"]  # Wed, Thu, Fri


def base_fixtures(drop_benchmark_on: str | None = None) -> dict:
    """Two-market, three-trading-day fixture set.

    ``drop_benchmark_on`` removes the KOSPI benchmark index row for that one
    day only, to exercise a single-axis (TREND) failure without touching any
    other retained day.
    """
    values = {}
    for step, day in enumerate(TRADING_DAYS):
        values[("stock", "kospi", day)] = {"OutBlock_1": [
            stock_row(day, "K-A", 100 + step * 10, 10 if step else 0, 1000 + step * 100, 10000),
            stock_row(day, "K-B", 100 - step * 5, -5 if step else 0, 500 + step * 50, 5000),
            stock_row(day, "K-C", 100, 0, 250, 2500),
        ]}
        values[("stock", "kosdaq", day)] = {"OutBlock_1": [
            stock_row(day, "Q-A", 50 + step * 5, 10 if step else 0, 700 + step * 70, 7000),
            stock_row(day, "Q-B", 50 - step * 2, -4 if step else 0, 300 + step * 30, 3000),
        ]}
        kospi_indices = [index_row(day, "화학", 100 + step * 3), index_row(day, "금융", 100 - step)]
        if day != drop_benchmark_on:
            kospi_indices.insert(0, index_row(day, "코스피", 3000 + step * 30))
        values[("index", "kospi", day)] = {"OutBlock_1": kospi_indices}
        values[("index", "kosdaq", day)] = {"OutBlock_1": [
            index_row(day, "코스닥", 900 + step * 18),
            index_row(day, "제약", 100 + step * 4),
            index_row(day, "전기전자", 100 - step * 2),
        ]}
    return values


def opener_for(fixtures: dict) -> FakeOpener:
    return FakeOpener(fixtures)


def build_with_fixed_clock(auth_key, dates, opener):
    with mock.patch.object(KMS, "_now_utc", return_value=FIXED_NOW):
        return MODULE.build_population(auth_key, dates, opener=opener)


class KrHistoricalReplayPopulationTest(unittest.TestCase):
    def test_schema_mode_and_evidence_class_are_shadow_never_natural(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        self.assertEqual(population["schema_version"], "regime_kr_historical_replay_population/v1")
        self.assertEqual(population["mode"], "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL")
        self.assertEqual(population["wbs"], "P1-COM-05")
        self.assertEqual(population["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY")
        for record in population["records"]:
            self.assertEqual(record["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY")
        # "NATURAL" may only appear inside the explicit disclaimer strings
        # (the mode value itself, and the authority key that stays false) —
        # never as a status this population claims to hold.
        self.assertIs(population["authority"]["natural_promotion_authorized"], False)
        for record in population["records"]:
            self.assertNotEqual(record.get("status"), "NATURAL")
            five_axis = record.get("five_axis")
            if five_axis is not None:
                self.assertNotEqual(five_axis.get("status"), "NATURAL")

    def test_authority_is_all_false_except_the_one_shadow_flag(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        authority = population["authority"]
        self.assertTrue(authority["historical_replay_evidence_authorized"])
        for key, value in authority.items():
            if key == "historical_replay_evidence_authorized":
                continue
            self.assertIs(value, False, key)
        for critical in (
            "action_authorized", "order_authorized", "capital_authorized",
            "production_authorized", "trading_authorized", "real_authorized",
            "natural_promotion_authorized",
        ):
            self.assertIs(authority[critical], False, critical)

    def test_exact_trading_day_resolves_effective_date_to_itself(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        record = population["records"][0]
        self.assertEqual(record["status"], "OBSERVED")
        self.assertEqual(record["requested_date"], "2026-08-28")
        self.assertEqual(record["effective_trading_date"], "2026-08-28")
        self.assertEqual(record["previous_trading_date"], "2026-08-27")
        self.assertEqual(record["five_axis"]["coverage"]["ratio"], "5/5")
        self.assertEqual(set(record["five_axis"]["axes"]), {"TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"})
        self.assertIn(record["candidate_normalized_result"]["paper_reference"]["candidate_regime"], {"RISK_ON", "RISK_OFF", "NEUTRAL", "STRESS"})
        self.assertEqual(record["source"]["contract_version"], "korea_market_signals/1")
        self.assertRegex(record["source_hashes"]["packet_payload_sha256"], r"^[0-9a-f]{64}$")

    def test_weekend_requested_date_resolves_backward_to_nearest_trading_day(self):
        # 2026-08-29 is a Saturday; no such KRX session exists.
        population = build_with_fixed_clock(TOKEN, ["2026-08-29"], opener_for(base_fixtures()))
        record = population["records"][0]
        self.assertEqual(record["status"], "OBSERVED")
        self.assertEqual(record["requested_date"], "2026-08-29")
        self.assertEqual(record["effective_trading_date"], "2026-08-28")
        self.assertEqual(record["previous_trading_date"], "2026-08-27")

    def test_no_lookahead_effective_date_never_after_requested_date(self):
        for requested in ("2026-08-26", "2026-08-27", "2026-08-28", "2026-08-29", "2026-08-30"):
            population = build_with_fixed_clock(TOKEN, [requested], opener_for(base_fixtures()))
            record = population["records"][0]
            if record["status"] != "OBSERVED":
                continue
            self.assertLessEqual(record["effective_trading_date"], requested)
            self.assertIs(record["no_lookahead_attestation"]["any_session_date_after_requested_date"], False)

    def test_replay_one_date_fails_closed_on_a_lookahead_violation(self):
        contract = KMS.load_contract()
        policy = MODULE._load_candidate_policy()

        def lookahead_pair(auth_key, *, anchor, opener, contract):
            future = (anchor + __import__("datetime").timedelta(days=5)).strftime("%Y%m%d")
            past = anchor.strftime("%Y%m%d")
            return {"date": past, "stock": {}, "index": {}}, {"date": future, "stock": {}, "index": {}}

        with mock.patch.object(KMS, "discover_session_pair", side_effect=lookahead_pair):
            record = MODULE._replay_one_requested_date(
                TOKEN, "2026-08-28", opener=opener_for(base_fixtures()), contract=contract, policy=policy,
            )
        self.assertEqual(record["status"], "BLOCKED")
        self.assertIn("REPLAY_LOOKAHEAD_VIOLATION", record["failure_reason"])

    def test_malformed_date_fails_closed_without_affecting_other_dates(self):
        population = build_with_fixed_clock(
            TOKEN, ["2026-08-28", "not-a-date", "2026-13-40"], opener_for(base_fixtures()),
        )
        by_date = {record["requested_date"]: record for record in population["records"]}
        self.assertEqual(by_date["2026-08-28"]["status"], "OBSERVED")
        self.assertEqual(by_date["not-a-date"]["status"], "BLOCKED")
        self.assertIn("REQUESTED_DATE_FORMAT_INVALID", by_date["not-a-date"]["failure_reason"])
        self.assertEqual(by_date["2026-13-40"]["status"], "BLOCKED")
        self.assertIn("REQUESTED_DATE_CALENDAR_INVALID", by_date["2026-13-40"]["failure_reason"])

    def test_calendar_invalid_date_fails_closed(self):
        population = build_with_fixed_clock(TOKEN, ["2026-02-30"], opener_for(base_fixtures()))
        record = population["records"][0]
        self.assertEqual(record["status"], "BLOCKED")
        self.assertIn("REQUESTED_DATE_CALENDAR_INVALID", record["failure_reason"])

    def test_source_entirely_missing_within_search_window_fails_closed(self):
        population = build_with_fixed_clock(TOKEN, ["2020-01-06"], opener_for(base_fixtures()))
        record = population["records"][0]
        self.assertEqual(record["status"], "BLOCKED")
        self.assertIsNone(record["five_axis"])
        self.assertIsNone(record["candidate_normalized_result"])
        self.assertIsNotNone(record["failure_reason"])

    def test_missing_one_axis_fails_the_whole_date_closed(self):
        fixtures = base_fixtures(drop_benchmark_on="20260828")
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(fixtures))
        record = population["records"][0]
        self.assertEqual(record["status"], "BLOCKED")
        self.assertIn("BENCHMARK_INDEX_MISSING", record["failure_reason"])
        self.assertIsNone(record["five_axis"])
        self.assertIsNone(record["candidate_normalized_result"])
        # An adjacent, fully-covered date must still observe cleanly.
        clean = build_with_fixed_clock(TOKEN, ["2026-08-27"], opener_for(fixtures))
        self.assertEqual(clean["records"][0]["status"], "OBSERVED")

    def test_date_isolation_one_records_outcome_does_not_change_with_batch_membership(self):
        fixtures = base_fixtures()
        solo = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(fixtures))
        batched = build_with_fixed_clock(
            TOKEN, ["2026-08-28", "2020-01-06", "not-a-date"], opener_for(fixtures),
        )
        solo_record = solo["records"][0]
        batched_record = next(r for r in batched["records"] if r["requested_date"] == "2026-08-28")
        self.assertEqual(solo_record, batched_record)

    def test_shuffled_date_input_produces_deterministic_ordering(self):
        fixtures = base_fixtures()
        forward = build_with_fixed_clock(TOKEN, ["2026-08-28", "2026-08-27", "not-a-date"], opener_for(fixtures))
        shuffled = build_with_fixed_clock(TOKEN, ["not-a-date", "2026-08-27", "2026-08-28"], opener_for(fixtures))
        self.assertEqual(MODULE.canonical_json(forward), MODULE.canonical_json(shuffled))
        self.assertEqual(
            [r["requested_date"] for r in forward["records"]],
            sorted(r["requested_date"] for r in forward["records"]),
        )

    def test_duplicate_requested_dates_collapse_to_one_record(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28", "2026-08-28"], opener_for(base_fixtures()))
        self.assertEqual(population["requested_dates"], ["2026-08-28"])
        self.assertEqual(len(population["records"]), 1)

    def test_deterministic_rerun_is_byte_identical(self):
        fixtures = base_fixtures()
        first = build_with_fixed_clock(TOKEN, ["2026-08-27", "2026-08-28"], opener_for(fixtures))
        second = build_with_fixed_clock(TOKEN, ["2026-08-27", "2026-08-28"], opener_for(fixtures))
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))

    def test_reuses_korea_market_signals_and_paper_regime_reference_unmodified(self):
        # This module must carry no private copy of session/packet/build_kr
        # logic — it has to call the live functions so a future edit to the
        # producer or the candidate rule is automatically replayed.
        import importlib
        from regime import paper_regime_reference as live_prr
        self.assertIs(MODULE.PRR.build_kr, live_prr.build_kr)
        self.assertTrue(callable(MODULE.KMS.discover_session_pair))
        self.assertTrue(callable(MODULE.KMS.build_packet))

    def test_no_retained_or_live_source_mutation(self):
        korea_dir = ROOT / "data" / "observations" / "korea_market_signals"
        latest_path = ROOT / "data" / "latest_korea_market_signals.json"

        def snapshot():
            found = {}
            if korea_dir.is_dir():
                for entry in sorted(korea_dir.iterdir()):
                    packet = entry / "packet.json"
                    if packet.is_file():
                        found[entry.name] = MODULE.file_sha256(packet)
            latest = MODULE.file_sha256(latest_path) if latest_path.is_file() else None
            return found, latest

        before = snapshot()
        build_with_fixed_clock(TOKEN, ["2026-08-27", "2026-08-28", "2026-08-29"], opener_for(base_fixtures()))
        after = snapshot()
        self.assertEqual(before, after)

    def test_validate_population_accepts_its_own_output(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        validated = MODULE.validate_population(copy.deepcopy(population))
        self.assertEqual(validated, population)

    def test_validate_population_rejects_tampered_payload(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        tampered = copy.deepcopy(population)
        tampered["records"][0]["status"] = "OBSERVED_FAKE"
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(tampered)

    def test_validate_population_rejects_a_flipped_authority_flag(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        tampered = copy.deepcopy(population)
        tampered["authority"]["order_authorized"] = True
        # Bypass the payload hash so the authority check itself is exercised.
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
        # contains, so dropping the records must fail rather than satisfy every
        # per-record guarantee vacuously.
        for records in ([], None):
            with self.subTest(records=records):
                tampered = copy.deepcopy(
                    build_with_fixed_clock(TOKEN, ["2026-08-27", "2026-08-28"], opener_for(base_fixtures()))
                )
                tampered["records"] = records
                with self.assertRaises(MODULE.ReplayPopulationError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("POPULATION_RECORDS_NOT_BIJECTIVE", str(caught.exception))

    def test_validate_population_rejects_a_record_for_an_unrequested_date(self):
        tampered = copy.deepcopy(
            build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        )
        tampered["records"][0]["requested_date"] = "2020-03-16"
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("POPULATION_RECORDS_NOT_BIJECTIVE", str(caught.exception))

    def test_validate_population_rejects_an_omitted_authority_boundary(self):
        tampered = copy.deepcopy(
            build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        )
        tampered["authority"].pop("order_authorized")
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("POPULATION_AUTHORITY_SCHEMA_INVALID", str(caught.exception))
        emptied = copy.deepcopy(
            build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        )
        emptied["authority"] = {}
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.validate_population(self._resigned(emptied))

    def test_validate_population_rejects_a_record_that_dropped_its_evidence(self):
        tampered = copy.deepcopy(
            build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        )
        tampered["records"][0]["five_axis"] = None
        tampered["records"][0]["candidate_normalized_result"] = None
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("OBSERVED_RECORD_MUST_CARRY_ITS_EVIDENCE", str(caught.exception))

    def test_validate_population_rejects_a_record_that_looked_forward(self):
        tampered = copy.deepcopy(
            build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        )
        tampered["records"][0]["no_lookahead_attestation"]["session_dates_used"].append(
            "20260904"
        )
        with self.assertRaises(MODULE.ReplayPopulationError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("RECORD_LOOKAHEAD_VIOLATION", str(caught.exception))

    def test_build_population_requires_at_least_one_date(self):
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.build_population(TOKEN, [], opener=opener_for(base_fixtures()))

    def test_write_population_refuses_any_path_inside_the_checkout(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        forbidden = ROOT / "data" / "observations" / "kr_historical_replay_population" / "sneak.json"
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.write_population(population, forbidden, root=ROOT)
        self.assertFalse(forbidden.exists())
        other_tracked = ROOT / "evidence" / "kr_historical_replay_population_sneak.json"
        with self.assertRaises(MODULE.ReplayPopulationError):
            MODULE.write_population(population, other_tracked, root=ROOT)
        self.assertFalse(other_tracked.exists())

    def test_write_population_accepts_an_external_path(self):
        population = build_with_fixed_clock(TOKEN, ["2026-08-28"], opener_for(base_fixtures()))
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "outside_checkout" / "population.json"
            written = MODULE.write_population(population, out, root=ROOT)
            self.assertTrue(written.is_file())
            reloaded = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(reloaded, population)

    def test_default_temp_out_is_never_inside_the_checkout(self):
        path = MODULE._default_temp_out()
        try:
            with self.assertRaises(ValueError):
                path.resolve().relative_to(ROOT.resolve())
        finally:
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
