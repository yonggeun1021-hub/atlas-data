"""Ratified population-level policy rules (2026-09-16 user ratification).

Covers: each of the six rules (INVESTABLE_UNIVERSE, LIQUIDITY,
LISTING_DELISTING, TAXONOMY, TRADABILITY, and US-only SOURCE_HIERARCHY)
filters the way the ratification card says it should; a symbol whose
required input is not wired into this pipeline resolves to UNKNOWN, never a
silent pass or exclusion; the two population-level zero-states -- "no rule
exists" (a legacy, pre-ratification packet) and "the wired rules did not
pass every symbol" (today's real state) -- stay distinguishable in the
assembled packet; the ratification evidence is byte-checked, not merely
referenced; and CANDIDATE_PASS_RULE / STAGE_TRANSITION_RULE stay untouched.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "population_ratified_policy.py"
SPEC = importlib.util.spec_from_file_location("population_ratified_policy", MODULE_PATH)
POLICY = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(POLICY)

CORE_SPEC = importlib.util.spec_from_file_location(
    "population_symbol_observation", ROOT / "decision" / "population_symbol_observation.py"
)
CORE = importlib.util.module_from_spec(CORE_SPEC)
sys.modules["population_symbol_observation"] = CORE
assert CORE_SPEC.loader is not None
CORE_SPEC.loader.exec_module(CORE)


class ContractAndEvidenceTests(unittest.TestCase):
    def test_contract_loads_and_scope_excludes_candidate_and_stage_rules(self):
        contract = POLICY.load_contract()
        self.assertEqual(contract["excludes"], ["CANDIDATE_PASS_RULE", "STAGE_TRANSITION_RULE"])
        self.assertTrue(all(v is False for v in contract["authority"].values()))

    def test_ratification_evidence_is_byte_checked_not_merely_referenced(self):
        contract = POLICY.load_contract()
        evidence = contract["ratification_evidence"][0]
        real_path = ROOT / evidence["path"]
        original = real_path.read_bytes()
        try:
            real_path.write_bytes(original + b" ")
            with self.assertRaises(POLICY.PopulationRatifiedPolicyError) as ctx:
                POLICY.load_contract()
            self.assertIn("RATIFICATION_EVIDENCE_DRIFT", str(ctx.exception))
        finally:
            real_path.write_bytes(original)
        # Unmodified, it loads clean again.
        POLICY.load_contract()

    def test_liquidity_thresholds_are_kept_per_market_kr_krw_us_usd(self):
        contract = POLICY.load_contract()
        kr = contract["rules"]["LIQUIDITY"]["KR"]
        us = contract["rules"]["LIQUIDITY"]["US"]
        self.assertEqual(kr["min_avg_trading_value"], 1_000_000_000)
        self.assertEqual(kr["min_avg_trading_value_currency"], "KRW")
        self.assertEqual(kr["min_close"], 1000)
        self.assertEqual(us["min_avg_dollar_volume"], 10_000_000)
        self.assertEqual(us["min_avg_dollar_volume_currency"], "USD")
        self.assertEqual(us["min_close"], 5)
        self.assertEqual(kr["window_sessions"], 20)
        self.assertEqual(us["window_sessions"], 20)


class KrInvestableUniverseTests(unittest.TestCase):
    def test_common_stock_name_passes(self):
        self.assertEqual(POLICY.kr_investable_universe("삼성전자", frozenset())["status"], "MET")

    def test_rights_spac_reit_are_excluded(self):
        self.assertEqual(POLICY.kr_investable_universe("테스나신주인수권", frozenset())["status"], "UNMET")
        self.assertEqual(POLICY.kr_investable_universe("알테오젠스팩", frozenset())["status"], "UNMET")
        self.assertEqual(POLICY.kr_investable_universe("신한서부티엔디리츠", frozenset())["status"], "UNMET")

    def test_multi_class_preferred_suffix_is_excluded_without_needing_a_base_match(self):
        self.assertEqual(POLICY.kr_investable_universe("삼성전자2우B", frozenset())["status"], "UNMET")
        self.assertEqual(POLICY.kr_investable_universe("삼성전자우B", frozenset())["status"], "UNMET")

    def test_bare_preferred_suffix_resolves_by_base_name_cross_check(self):
        # "삼성전자우" is genuinely preferred stock of "삼성전자" -- the
        # population also carries the base name, so this is confidently
        # UNMET, not a guess.
        verdict = POLICY.kr_investable_universe("삼성전자우", frozenset(["삼성전자"]))
        self.assertEqual(verdict["status"], "UNMET")

    def test_ambiguous_bare_preferred_suffix_without_base_match_is_unknown_not_a_guess(self):
        # "미래에셋대우" is a real KOSPI common stock whose legal name happens
        # to end in the syllable "우" ("대우" is part of the company name).
        # Without its own base name ("미래에셋대") present elsewhere in the
        # population, this module refuses to guess either way.
        verdict = POLICY.kr_investable_universe("미래에셋대우", frozenset())
        self.assertEqual(verdict["status"], "UNKNOWN")

    def test_missing_display_name_is_unknown(self):
        self.assertEqual(POLICY.kr_investable_universe(None, frozenset())["status"], "UNKNOWN")
        self.assertEqual(POLICY.kr_investable_universe("  ", frozenset())["status"], "UNKNOWN")


class UsInvestableUniverseTests(unittest.TestCase):
    def test_common_stock_passes(self):
        verdict = POLICY.us_investable_universe(["N"], ["N"], "APPLE INC COMMON STOCK")
        self.assertEqual(verdict["status"], "MET")

    def test_etf_flag_excludes(self):
        verdict = POLICY.us_investable_universe(["Y"], ["N"], "SPDR S&P 500 ETF")
        self.assertEqual(verdict["status"], "UNMET")
        self.assertEqual(verdict["reason"], "ETF_FLAG_Y")

    def test_test_issue_flag_excludes(self):
        verdict = POLICY.us_investable_universe(["N"], ["Y"], "TEST STOCK")
        self.assertEqual(verdict["status"], "UNMET")
        self.assertEqual(verdict["reason"], "TEST_ISSUE_FLAG_Y")

    def test_preferred_warrant_and_ads_name_patterns_exclude(self):
        for name in ("SOME CO 8.5% PREFERRED SHARES", "SOME CO WARRANT", "SOME CO DEPOSITARY SHARES"):
            with self.subTest(name=name):
                verdict = POLICY.us_investable_universe(["N"], ["N"], name)
                self.assertEqual(verdict["status"], "UNMET")

    def test_missing_directory_flags_is_unknown_not_a_silent_pass(self):
        verdict = POLICY.us_investable_universe([], [], "SOMETHING INC")
        self.assertEqual(verdict["status"], "UNKNOWN")


class LiquidityTests(unittest.TestCase):
    def test_kr_liquidity_needs_a_full_20_session_window_else_unknown(self):
        self.assertEqual(
            POLICY.kr_liquidity(None, 0, None, required_sessions=20, min_avg_trading_value_krw=1e9, min_close_krw=1000)["status"],
            "UNKNOWN",
        )
        self.assertEqual(
            POLICY.kr_liquidity(2e9, 19, 2000, required_sessions=20, min_avg_trading_value_krw=1e9, min_close_krw=1000)["status"],
            "UNKNOWN",
        )

    def test_kr_liquidity_met_and_unmet_at_the_ratified_threshold(self):
        met = POLICY.kr_liquidity(1.5e9, 20, 5000, required_sessions=20, min_avg_trading_value_krw=1e9, min_close_krw=1000)
        self.assertEqual(met["status"], "MET")
        unmet_value = POLICY.kr_liquidity(5e8, 20, 5000, required_sessions=20, min_avg_trading_value_krw=1e9, min_close_krw=1000)
        self.assertEqual(unmet_value["status"], "UNMET")
        unmet_close = POLICY.kr_liquidity(1.5e9, 20, 500, required_sessions=20, min_avg_trading_value_krw=1e9, min_close_krw=1000)
        self.assertEqual(unmet_close["status"], "UNMET")

    def test_us_liquidity_needs_20_bars_else_unknown(self):
        bars = [{"close": "10", "volume": "2000000"}] * 19
        self.assertEqual(
            POLICY.us_liquidity(bars, required_sessions=20, min_avg_dollar_volume_usd=1e7, min_close_usd=5)["status"],
            "UNKNOWN",
        )

    def test_us_liquidity_met_and_unmet_at_the_ratified_threshold(self):
        bars_met = [{"close": "10", "volume": "2000000"}] * 20  # avg $20,000,000
        self.assertEqual(
            POLICY.us_liquidity(bars_met, required_sessions=20, min_avg_dollar_volume_usd=1e7, min_close_usd=5)["status"],
            "MET",
        )
        bars_unmet = [{"close": "10", "volume": "1000"}] * 20  # avg $10,000
        self.assertEqual(
            POLICY.us_liquidity(bars_unmet, required_sessions=20, min_avg_dollar_volume_usd=1e7, min_close_usd=5)["status"],
            "UNMET",
        )
        bars_low_close = [{"close": "1", "volume": "20000000"}] * 20  # volume high, close < $5
        self.assertEqual(
            POLICY.us_liquidity(bars_low_close, required_sessions=20, min_avg_dollar_volume_usd=1e7, min_close_usd=5)["status"],
            "UNMET",
        )


class MissingInputResolvesUnknownTests(unittest.TestCase):
    """RULE.5 -- a required input this pipeline does not wire yet must
    resolve UNKNOWN, never a silent pass or exclusion."""

    def test_listing_delisting_is_unknown_for_both_markets_no_source_wired(self):
        self.assertEqual(POLICY.kr_listing_delisting(None, None)["status"], "UNKNOWN")
        self.assertEqual(POLICY.us_listing_delisting(None, None)["status"], "UNKNOWN")

    def test_kr_taxonomy_is_unknown_no_46_industry_table_wired(self):
        self.assertEqual(POLICY.kr_taxonomy(None)["status"], "UNKNOWN")

    def test_kr_taxonomy_is_met_when_a_sector_is_supplied(self):
        verdict = POLICY.kr_taxonomy("전기전자")
        self.assertEqual(verdict["status"], "MET")
        self.assertEqual(verdict["sector"], "전기전자")

    def test_kr_tradability_is_unknown_no_kis_master_wired(self):
        self.assertEqual(POLICY.kr_tradability(None)["status"], "UNKNOWN")

    def test_us_tradability_is_unknown_no_halt_deficiency_feed_wired(self):
        self.assertEqual(POLICY.us_tradability(None)["status"], "UNKNOWN")

    def test_us_taxonomy_reuses_spdr_reader_status_verbatim(self):
        self.assertEqual(POLICY.us_taxonomy({"status": "OK", "sector_etf": "XLK", "basis": "SOLE_HOLDER"})["status"], "MET")
        self.assertEqual(POLICY.us_taxonomy({"status": "UNKNOWN_NO_T2"})["status"], "UNKNOWN")
        self.assertEqual(POLICY.us_taxonomy({"status": "NO_POINT_IN_TIME_CAPTURE_AVAILABLE"})["status"], "UNKNOWN")

    def test_us_source_hierarchy_sip_first_iex_recorded_second_else_unknown(self):
        self.assertEqual(POLICY.us_source_hierarchy("sip")["status"], "MET")
        iex = POLICY.us_source_hierarchy("iex")
        self.assertEqual(iex["status"], "MET")
        self.assertEqual(iex["note"], "IEX 기준(실제 거래량의 일부)")
        self.assertEqual(POLICY.us_source_hierarchy(None)["status"], "UNKNOWN")


class OverallCombinationTests(unittest.TestCase):
    def test_all_met_is_pass(self):
        self.assertEqual(POLICY.overall({"A": POLICY.MET("x"), "B": POLICY.MET("y")})["status"], "RATIFIED_POPULATION_PASS")

    def test_any_unmet_is_excluded_even_with_unknowns_present(self):
        result = POLICY.overall({"A": POLICY.UNMET("x"), "B": POLICY.UNKNOWN("y"), "C": POLICY.MET("z")})
        self.assertEqual(result["status"], "RATIFIED_POPULATION_EXCLUDED")

    def test_no_unmet_but_not_all_met_is_unknown(self):
        result = POLICY.overall({"A": POLICY.MET("x"), "B": POLICY.UNKNOWN("y")})
        self.assertEqual(result["status"], "RATIFIED_POPULATION_UNKNOWN")

    def test_evaluate_kr_and_us_cover_exactly_the_ratified_rule_set(self):
        contract = POLICY.load_contract()
        kr = POLICY.evaluate_kr(
            display_name="삼성전자", population_display_names=frozenset(), avg_trading_value_20s_krw=None,
            sessions_available=0, latest_close_krw=None, sector_46=None, contract=contract,
        )
        self.assertEqual(set(kr["rules"]), set(POLICY.KR_RULES))
        us = POLICY.evaluate_us(
            etf_flags=["N"], test_issue_flags=["N"], security_name="APPLE INC COMMON STOCK", bars=[],
            spdr_result={"status": "UNKNOWN_NO_T2"}, feed=None, contract=contract,
        )
        self.assertEqual(set(us["rules"]), set(POLICY.US_RULES))
        # CANDIDATE_PASS_RULE / STAGE_TRANSITION_RULE are never among them.
        self.assertNotIn("CANDIDATE_PASS_RULE", kr["rules"])
        self.assertNotIn("STAGE_TRANSITION_RULE", us["rules"])


class PacketLevelZeroStateDistinguishabilityTests(unittest.TestCase):
    """RULE.3 -- "0 because no rule exists" (a legacy pre-ratification
    packet, persisted before this wiring existed) and "0 because the wired
    rules did not pass every symbol" (today's real state) must stay
    distinguishable when read back.

    ``assemble_packet`` -- the builder real KR/US adapters call -- always
    receives rows the current code already stamped with
    ``population_policy`` (``decision/{korea,us}_population_symbol_observation.py``).
    The legacy scenario only ever exists as an *already-persisted* packet
    built by an older code version, so it is constructed here by taking a
    freshly assembled packet and stripping the field back out (with its own
    old-era summary/hash), never by asking today's builder to omit it.
    """

    def _row(self, symbol: str, population_policy: dict) -> dict:
        return {
            "symbol": symbol, "name": symbol, "membership": {}, "observation_status": "NOT_EVALUABLE",
            "data_observation": {"status": "DATA_OBSERVED"},
            "evaluability": {"status": "NOT_EVALUABLE", "reasons": ["TEST_REASON"]},
            "evaluation": {"status": "NOT_EVALUATED", "entry_state": None},
            "formal_candidate": {"status": "NOT_A_FORMAL_CANDIDATE", "promotion_by_this_packet": False},
            "facts": {}, "evidence_refs": [],
            "population_policy": population_policy,
        }

    def _assemble(self, rows: list) -> dict:
        contract = CORE.load_contract()
        return CORE.assemble_packet(
            market="KR", session_date="2026-09-16", generated_at="2026-09-16T00:00:00Z", gen_id="test-gen",
            population={"count": len(rows), "as_of": "2026-09-16", "population_id": "TEST.POP"},
            sources={}, rows=rows, policy_undefined=[], resume_report={"complete": True}, contract=contract,
        )

    def _as_legacy_packet(self, packet: dict) -> dict:
        """Simulate a packet persisted before 2026-09-16 by this exact repo's
        own older code: no ``population_policy`` key on any row, the old
        absence semantics, and a self-hash recomputed over that older shape
        (not tampered -- an honest packet of the older contract version)."""
        legacy = copy.deepcopy(packet)
        for row in legacy["symbols"]:
            del row["population_policy"]
        legacy["summary"] = dict(legacy["summary"])
        del legacy["summary"]["population_policy_counts"]
        del legacy["summary"]["population_policy_rule_counts"]
        legacy["summary"]["passed_count"] = 0
        legacy["summary"]["passed_semantics"] = "NO_RATIFIED_PASS_RULE_ZERO_IS_ABSENCE_OF_RULE"
        legacy["status_counts"] = dict(legacy["status_counts"])
        del legacy["status_counts"]["population_policy"]
        unsigned = dict(legacy)
        del unsigned["payload_sha256"]
        legacy["payload_sha256"] = CORE.payload_sha256(unsigned)
        return legacy

    def test_legacy_packet_with_no_population_policy_field_keeps_the_old_absence_semantics(self):
        unknown_policy = {"status": "RATIFIED_POPULATION_UNKNOWN", "rules": {"INVESTABLE_UNIVERSE": {"status": "UNKNOWN", "reason": "x"}}}
        rows = [self._row("A", unknown_policy), self._row("B", unknown_policy)]
        legacy = self._as_legacy_packet(self._assemble(rows))
        self.assertEqual(legacy["summary"]["passed_count"], 0)
        self.assertEqual(legacy["summary"]["passed_semantics"], "NO_RATIFIED_PASS_RULE_ZERO_IS_ABSENCE_OF_RULE")
        CORE.validate_packet(legacy)

    def test_ratified_packet_with_zero_passes_carries_the_new_semantics_and_breakdown(self):
        unknown_policy = {"status": "RATIFIED_POPULATION_UNKNOWN", "rules": {"INVESTABLE_UNIVERSE": {"status": "UNKNOWN", "reason": "x"}}}
        excluded_policy = {"status": "RATIFIED_POPULATION_EXCLUDED", "rules": {"INVESTABLE_UNIVERSE": {"status": "UNMET", "reason": "y"}}}
        rows = [self._row("A", unknown_policy), self._row("B", excluded_policy)]
        packet = self._assemble(rows)
        self.assertEqual(packet["summary"]["passed_count"], 0)
        self.assertEqual(
            packet["summary"]["passed_semantics"],
            "RATIFIED_POPULATION_POLICY_PASS_COUNT_SIX_RULES_20260916_NOT_CANDIDATE_PASS_RULE",
        )
        self.assertEqual(packet["summary"]["population_policy_counts"]["RATIFIED_POPULATION_UNKNOWN"], 1)
        self.assertEqual(packet["summary"]["population_policy_counts"]["RATIFIED_POPULATION_EXCLUDED"], 1)
        CORE.validate_packet(packet)
        # The two zero-states are never the same string.
        legacy = self._as_legacy_packet(self._assemble([self._row("A", unknown_policy)]))
        self.assertNotEqual(legacy["summary"]["passed_semantics"], packet["summary"]["passed_semantics"])

    def test_ratified_packet_with_a_real_pass_is_counted_and_validated(self):
        pass_policy = {"status": "RATIFIED_POPULATION_PASS", "rules": {"INVESTABLE_UNIVERSE": {"status": "MET", "reason": "x"}}}
        unknown_policy = {"status": "RATIFIED_POPULATION_UNKNOWN", "rules": {"INVESTABLE_UNIVERSE": {"status": "UNKNOWN", "reason": "y"}}}
        rows = [self._row("A", pass_policy), self._row("B", unknown_policy)]
        packet = self._assemble(rows)
        self.assertEqual(packet["summary"]["passed_count"], 1)
        CORE.validate_packet(packet)

    def test_partially_present_population_policy_is_rejected_as_inconsistent(self):
        pass_policy = {"status": "RATIFIED_POPULATION_PASS", "rules": {"INVESTABLE_UNIVERSE": {"status": "MET", "reason": "x"}}}
        rows = [self._row("A", pass_policy), self._row("B", pass_policy)]
        packet = self._assemble(rows)
        del packet["symbols"][1]["population_policy"]
        unsigned = dict(packet)
        del unsigned["payload_sha256"]
        packet["payload_sha256"] = CORE.payload_sha256(unsigned)
        with self.assertRaises(CORE.PopulationSymbolObservationError):
            CORE.validate_packet(packet)

    def test_inconsistent_overall_status_is_rejected(self):
        contradictory = {"status": "RATIFIED_POPULATION_PASS", "rules": {"INVESTABLE_UNIVERSE": {"status": "UNMET", "reason": "x"}}}
        rows = [self._row("A", contradictory)]
        packet = self._assemble(rows)
        # assemble_packet trusts its caller's population_policy verbatim;
        # the overall/rule consistency check lives in validate_packet, so a
        # corrupted verdict must be injected after assembly and re-signed to
        # exercise that guard specifically.
        unsigned = dict(packet)
        del unsigned["payload_sha256"]
        packet["payload_sha256"] = CORE.payload_sha256(unsigned)
        with self.assertRaises(CORE.PopulationSymbolObservationError):
            CORE.validate_packet(packet)


if __name__ == "__main__":
    unittest.main()
