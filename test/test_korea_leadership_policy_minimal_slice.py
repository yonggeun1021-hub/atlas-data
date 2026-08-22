#!/usr/bin/env python3
"""P1-KR-07 Korea Leadership minimal ratified policy Slice regression.

Classifies the full real 89-index catalog discovered by the 2026-08-21
live run (data/observations/korea_leadership_context/2026-08-21/
packet.json, KOSPI 50 + KOSDAQ 39) into INCLUDED (48: 2 market
benchmarks + 46 official KRX base-market SECTOR indices) and EXCLUDED
(41: KOSPI 200-family and KOSDAQ 150-family size-tier/segment/strategy
sub-indices -- duplicate-of-benchmark, weight-cap/strategy variants, or
a distinct secondary classification scheme this minimal Slice
deliberately does not mix with the base-market scheme). 0 UNKNOWN in
this Slice -- every one of the 89 real names is an unambiguous,
standard KRX industry/index term.
"""
from __future__ import annotations

import datetime as dt
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


LEADERSHIP = _load("korea_leadership", ".github/scripts/korea_leadership.py")

KOSPI_SECTORS = [
    "IT 서비스", "건설", "금속", "금융", "기계·장비", "보험", "부동산", "비금속", "섬유·의류",
    "오락·문화", "운송·창고", "운송장비·부품", "유통", "음식료·담배", "의료·정밀기기", "일반서비스",
    "전기·가스", "전기전자", "제약", "제조", "종이·목재", "증권", "통신", "화학",
]
KOSDAQ_SECTORS = [
    "IT 서비스", "건설", "금속", "금융", "기계·장비", "기타제조", "비금속", "섬유·의류",
    "오락·문화", "운송·창고", "운송장비·부품", "유통", "음식료·담배", "의료·정밀기기", "일반서비스",
    "전기전자", "제약", "제조", "종이·목재", "출판·매체복제", "통신", "화학",
]
KOSPI_EXCLUDED = [
    "코스피 100", "코스피 200", "코스피 50", "코스피 대형주", "코스피 소형주", "코스피 중형주",
    "코스피200제외 코스피지수",
    "코스피 200 TOP 10", "코스피 200 건설", "코스피 200 경기방어소비재지수", "코스피 200 경기소비재",
    "코스피 200 금융", "코스피 200 비중상한 20%", "코스피 200 비중상한 25%", "코스피 200 비중상한 30%",
    "코스피 200 산업재", "코스피 200 생활소비재", "코스피 200 에너지/화학", "코스피 200 정보기술",
    "코스피 200 중공업", "코스피 200 중소형주", "코스피 200 철강/소재", "코스피 200 초대형제외 지수",
    "코스피 200 커뮤니케이션서비스", "코스피 200 헬스케어",
]
KOSDAQ_EXCLUDED = [
    "코스닥 150", "코스닥 150 산업재", "코스닥 150 소재", "코스닥 150 자유소비재", "코스닥 150 정보기술",
    "코스닥 150 커뮤니케이션서비스", "코스닥 150 필수소비재", "코스닥 150 헬스케어",
    "코스닥 글로벌", "코스닥 기술성장기업부", "코스닥 대형주", "코스닥 벤처기업부", "코스닥 소형주",
    "코스닥 우량기업부", "코스닥 중견기업부", "코스닥 중형주",
]


class RealCatalogCoverageTest(unittest.TestCase):
    """Cross-checks the classification against the actual real-run
    catalog -- not a hand-maintained list that could silently drift from
    what was really discovered."""

    def setUp(self):
        packet = json.loads(
            (ROOT / "data/observations/korea_leadership_context/2026-08-21/packet.json")
            .read_text(encoding="utf-8")
        )
        self.real_kospi = set(packet["markets"]["KOSPI"]["discovered_index_names"])
        self.real_kosdaq = set(packet["markets"]["KOSDAQ"]["discovered_index_names"])

    def test_classification_is_a_complete_exact_partition_of_the_real_catalog(self):
        kospi_classified = set(KOSPI_SECTORS) | set(KOSPI_EXCLUDED) | {"코스피"}
        kosdaq_classified = set(KOSDAQ_SECTORS) | set(KOSDAQ_EXCLUDED) | {"코스닥"}
        self.assertEqual(kospi_classified, self.real_kospi)
        self.assertEqual(kosdaq_classified, self.real_kosdaq)

    def test_counts_match_89_total_48_included_41_excluded_0_unknown(self):
        included = 2 + len(KOSPI_SECTORS) + len(KOSDAQ_SECTORS)
        excluded = len(KOSPI_EXCLUDED) + len(KOSDAQ_EXCLUDED)
        total = len(self.real_kospi) + len(self.real_kosdaq)
        self.assertEqual(total, 89)
        self.assertEqual(included, 48)
        self.assertEqual(excluded, 41)
        self.assertEqual(included + excluded, total)


class RatifiedPolicyTest(unittest.TestCase):
    def setUp(self):
        self.policy = LEADERSHIP.load_policy()
        self.as_of = dt.date(2026, 8, 22)

    def test_policy_is_ratified_with_exactly_48_records(self):
        self.assertEqual(self.policy["approval_status"], "RATIFIED")
        self.assertEqual(len(self.policy["records"]), 48)

    def test_both_benchmarks_are_present_and_self_referential(self):
        kospi = LEADERSHIP.active_record(self.policy["records"], "KOSPI::코스피", self.as_of)
        kosdaq = LEADERSHIP.active_record(self.policy["records"], "KOSDAQ::코스닥", self.as_of)
        self.assertEqual(kospi["role"], "KOSPI_BENCHMARK")
        self.assertEqual(kospi["benchmark_identity"], "KOSPI::코스피")
        self.assertEqual(kosdaq["role"], "KOSDAQ_BENCHMARK")
        self.assertEqual(kosdaq["benchmark_identity"], "KOSDAQ::코스닥")

    def test_every_included_sector_is_active_and_scoped_to_its_own_market_benchmark(self):
        for name in KOSPI_SECTORS:
            with self.subTest(market="KOSPI", name=name):
                record = LEADERSHIP.active_record(
                    self.policy["records"], f"KOSPI::{name}", self.as_of
                )
                self.assertIsNotNone(record)
                self.assertEqual(record["role"], "SECTOR")
                self.assertEqual(record["benchmark_identity"], "KOSPI::코스피")
        for name in KOSDAQ_SECTORS:
            with self.subTest(market="KOSDAQ", name=name):
                record = LEADERSHIP.active_record(
                    self.policy["records"], f"KOSDAQ::{name}", self.as_of
                )
                self.assertIsNotNone(record)
                self.assertEqual(record["role"], "SECTOR")
                self.assertEqual(record["benchmark_identity"], "KOSDAQ::코스닥")

    def test_excluded_indices_have_no_active_record_in_either_market(self):
        for name in KOSPI_EXCLUDED:
            with self.subTest(market="KOSPI", name=name):
                self.assertIsNone(
                    LEADERSHIP.active_record(self.policy["records"], f"KOSPI::{name}", self.as_of)
                )
        for name in KOSDAQ_EXCLUDED:
            with self.subTest(market="KOSDAQ", name=name):
                self.assertIsNone(
                    LEADERSHIP.active_record(self.policy["records"], f"KOSDAQ::{name}", self.as_of)
                )

    def test_same_sector_name_different_market_are_distinct_records(self):
        # "IT 서비스" exists as a SECTOR in both markets -- must be two
        # genuinely separate records (qualified identity), never one
        # record silently shared/collided across markets.
        kospi_it = LEADERSHIP.active_record(self.policy["records"], "KOSPI::IT 서비스", self.as_of)
        kosdaq_it = LEADERSHIP.active_record(self.policy["records"], "KOSDAQ::IT 서비스", self.as_of)
        self.assertIsNot(kospi_it, kosdaq_it)
        self.assertEqual(kospi_it["benchmark_identity"], "KOSPI::코스피")
        self.assertEqual(kosdaq_it["benchmark_identity"], "KOSDAQ::코스닥")

    def test_ratification_is_not_retroactive(self):
        before = dt.date(2026, 8, 19)
        self.assertIsNone(
            LEADERSHIP.active_record(self.policy["records"], "KOSPI::코스피", before)
        )
        self.assertEqual(self.policy["effective_from"], "2026-08-20")

    def test_future_tampered_effective_from_is_still_inactive(self):
        tampered = self.policy["records"][0] | {"effective_from": "2099-01-01"}
        records = [tampered] + self.policy["records"][1:]
        self.assertIsNone(
            LEADERSHIP.active_record(records, tampered["series_identity"], self.as_of)
        )

    def test_stale_or_missing_series_identity_is_simply_absent(self):
        self.assertIsNone(
            LEADERSHIP.active_record(self.policy["records"], "KOSPI::존재하지않음", self.as_of)
        )

    def test_no_overlap_across_all_48_records(self):
        # require_ratified() itself would fail_closed on overlap -- this
        # just proves it does not fail, i.e. the ratified file is
        # genuinely overlap-free today.
        LEADERSHIP.require_ratified(self.policy)


if __name__ == "__main__":
    unittest.main()
