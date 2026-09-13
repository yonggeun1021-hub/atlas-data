#!/usr/bin/env python3
"""Per-market candidate discovery status / per-symbol lookup regression.

Runs against the real committed inputs (newest packet of every source,
selected by each packet's own date) plus a pinned immutable Crypto decision
generation, and checks that the lookup only re-labels existing evidence:
counts reconcile, zero is always labelled, missing evidence stays
NO_EVIDENCE / NOT_AVAILABLE, source dates survive, tampering fails closed,
and no authority is opened.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery" / "market_candidate_discovery_lookup.py"
SPEC = importlib.util.spec_from_file_location("market_candidate_discovery_lookup", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

# Immutable, already-committed Crypto generation (same one the coverage
# receipt's own regression pins), so the Crypto assertions are exact.
PINNED_CRYPTO = {
    "crypto_universe_path": ROOT / "data/observations/upbit_tradeable_universe/2026-09-12/packet.json",
    "crypto_identity_review_path": ROOT / "data/observations/upbit_identity_review/2026-09-12/packet.json",
    "crypto_snapshot_dir": ROOT / "evidence/crypto/upbit/raw/2026-09-12",
    "crypto_decision_path": ROOT / (
        "evidence/crypto_paper_decision/2026-09-12/1351/"
        "4aaa821be20c357e201f83fe3b1a2ae3adfd9b533fbe7c7cbc25d31eac06c6ad/packet.json"
    ),
    "crypto_detail_path": None,
}


def now_utc() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(path: Path, value) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    return path


class CurrentInputsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated_at = now_utc()
        cls.inputs = MODULE.default_inputs()
        cls.report = MODULE.build_report(generated_at=cls.generated_at, inputs=cls.inputs)
        cls.by_market = {row["market"]: row for row in cls.report["markets"]}

    def test_report_validates_and_carries_no_authority(self):
        self.assertEqual(MODULE.validate_report(self.report), self.report)
        self.assertEqual(self.report["schema_version"], MODULE.SCHEMA_VERSION)
        self.assertEqual(self.report["generated_at_semantics"], "LOOKUP_TIME_ONLY_NEVER_A_SOURCE_DATE")
        authority = self.report["authority"]
        self.assertTrue(authority["read_only"])
        self.assertTrue(all(value is False for key, value in authority.items() if key != "read_only"))
        self.assertEqual(sorted(self.by_market), ["CRYPTO", "KR", "US"])
        self.assertEqual(self.report["coverage_receipt"]["contract"], "three_market_evaluation_coverage/1")

    def test_every_market_chain_reconciles_and_cross_checks_coverage(self):
        for market, row in self.by_market.items():
            with self.subTest(market=market):
                recon = row["reconciliation"]
                self.assertEqual(recon["coverage_receipt_cross_check"], "MATCH")
                self.assertEqual(recon["evaluated_duplicate_count"], 0)
                population = row["population"]["count"]
                self.assertIsInstance(population, int)
                self.assertGreater(population, 0)
                if market == "CRYPTO":
                    self.assertTrue(recon["population_equals_evaluated_plus_excluded_plus_unevaluated"])
                    self.assertTrue(recon["evaluated_equals_admitted"])
                    disposition = row["disposition"]
                    self.assertEqual(
                        population,
                        row["evaluated"]["count"] + disposition["excluded"]["count"] + disposition["unevaluated"]["count"],
                    )
                else:
                    self.assertTrue(recon["population_equals_evaluated_in_population_plus_unevaluated"])
                    self.assertTrue(recon["passed_plus_held_equals_evaluated"])
                    self.assertEqual(recon["evaluated_symbols_not_in_population"], [])
                    self.assertEqual(
                        population,
                        len(recon["evaluated_symbols_in_population"]) + row["disposition"]["unevaluated"]["count"],
                    )
                    self.assertEqual(
                        sorted(recon["evaluated_symbols_in_population"]),
                        sorted(symbol["symbol"] for symbol in row["symbols"]),
                    )

    def test_source_dates_are_preserved_not_overwritten_by_lookup_time(self):
        generated_date = self.generated_at[:10]
        kr_universe = json.loads(Path(self.inputs["kr_universe_path"]).read_text(encoding="utf-8"))
        us_universe = json.loads(Path(self.inputs["us_universe_path"]).read_text(encoding="utf-8"))
        kr_review = json.loads(Path(self.inputs["kr_review_path"]).read_text(encoding="utf-8"))
        self.assertEqual(self.by_market["KR"]["population"]["as_of"], kr_universe["as_of_date"])
        self.assertEqual(self.by_market["US"]["population"]["as_of"], us_universe["packet"]["as_of_date"])
        self.assertEqual(self.by_market["KR"]["evaluated"]["evaluated_at"], kr_review["generated_at"])
        # The lookup date only appears as the report's own generated_at.
        for market in ("KR", "US"):
            freshness = self.by_market[market]["population"]["freshness"]
            self.assertEqual(freshness["policy"], "SOURCE_DECLARED_EFFECTIVE_INTERVAL")
            self.assertNotEqual(freshness["valid_to"], generated_date + "T")
        crypto_freshness = self.by_market["CRYPTO"]["population"]["freshness"]
        self.assertEqual(crypto_freshness["policy"], MODULE.UNDEFINED)
        self.assertEqual(crypto_freshness["source_date"], self.by_market["CRYPTO"]["population"]["as_of"])

    def test_zero_counts_are_labelled_and_missing_sources_are_not_zero(self):
        for market in ("KR", "US"):
            passed = self.by_market[market]["disposition"]["passed"]
            self.assertEqual(passed["semantics"], "REVIEW_AUTOMATIC_ENTRY_COUNT_FROM_SOURCE")
            self.assertEqual(passed["pass_rule_status"], MODULE.UNDEFINED)
            self.assertEqual(
                self.by_market[market]["candidate_zero_semantics"],
                "BOUNDED_REVIEW_ONLY_NO_POPULATION_CANDIDATE_RULE",
            )
            self.assertEqual(
                self.by_market[market]["data_acquired"].get("population_level_symbol_data", {}).get("count", MODULE.NOT_COUNTED),
                MODULE.NOT_COUNTED,
            )
        screening = self.by_market["KR"]["screening_layer"]
        self.assertIn(screening["status"], ("AVAILABLE", MODULE.NOT_AVAILABLE))
        if screening["status"] == MODULE.NOT_AVAILABLE:
            self.assertEqual(screening["reason"], "KRX_REGISTRY_EVALUATION_COVERAGE_PACKET_ABSENT")
        us_screening = self.by_market["US"]["screening_layer"]
        self.assertEqual(us_screening["status"], MODULE.NOT_AVAILABLE)

    def test_gap_classes_come_only_from_the_fixed_enumeration(self):
        for market, row in self.by_market.items():
            for gap in row["gap_classification"]:
                self.assertIn(gap["class"], MODULE.GAP_CLASSES, msg=f"{market}:{gap}")
                self.assertIsInstance(gap["affected_count"], int)
            codes = {gap["code"] for gap in row["gap_classification"]}
            if market in ("KR", "US"):
                self.assertIn("FULL_POPULATION_EVALUATOR_NOT_CONNECTED", codes)
                unevaluated = row["disposition"]["unevaluated"]["count"]
                gap = next(g for g in row["gap_classification"] if g["code"] == "FULL_POPULATION_EVALUATOR_NOT_CONNECTED")
                self.assertEqual(gap["class"], "FEATURE_NOT_IMPLEMENTED")
                self.assertEqual(gap["affected_count"], unevaluated)
            # A stale classification may only come from the source's own interval.
            for gap in row["gap_classification"]:
                if gap["class"] == "SOURCE_STALE":
                    self.assertIn("effective_interval", gap["evidence"])
                    self.assertEqual(row["population"]["freshness"]["status_at_generated_date"], "ELAPSED_SOURCE_INTERVAL")

    def test_next_step_conditions_use_existing_codes_and_keep_undefined_rules_undefined(self):
        for market, row in self.by_market.items():
            statuses = {item["condition"]: item["status"] for item in row["next_step_conditions"]}
            self.assertTrue(statuses, market)
            if market in ("KR", "US"):
                self.assertEqual(statuses["CANDIDATE_PASS_RULE"], MODULE.UNDEFINED)
            for item in row["next_step_conditions"]:
                self.assertIn(item["status"], ("UNMET", MODULE.UNDEFINED))

    def test_portal_block_links_existing_contracts_with_hashes(self):
        portal = self.report["portal"]
        roles = {ref["role"]: ref for ref in portal["reused_contracts"]}
        self.assertEqual(roles["three_market_coverage"]["payload_sha256"], self.report["coverage_receipt"]["payload_sha256"])
        self.assertEqual(roles["kr_symbol_review"]["contract"], "korea_symbol_market_review/1")
        self.assertEqual(roles["us_symbol_review"]["contract"], "us_symbol_market_review/1")
        self.assertEqual(roles["crypto_decision"]["contract"], "crypto_paper_decision_snapshot_packet/1")
        for ref in portal["reused_contracts"]:
            if "source" in ref:
                self.assertRegex(ref["source"]["file_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(portal["display_boundary"], "FACT_ONLY_NO_INFERENCE_UNKNOWN_KEPT_AS_UNKNOWN")
        for row in self.report["markets"]:
            for symbol in row["symbols"]:
                self.assertIn("detail_contract", symbol)

    def test_kr_pipeline_subject_lookup_reports_evidence_and_gaps_verbatim(self):
        kr_symbols = [row["symbol"] for row in self.by_market["KR"]["symbols"]]
        symbol = kr_symbols[0]
        detail = MODULE.lookup_symbol("KR", symbol, generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(detail["schema_version"], f"{MODULE.SCHEMA_VERSION}#symbol_detail")
        self.assertEqual(detail["population_membership"]["status"], "IN_POPULATION")
        self.assertEqual(detail["population_membership"]["as_of"], self.by_market["KR"]["population"]["as_of"])
        self.assertEqual(detail["candidate_inclusion"]["status"], "PIPELINE_SUBJECT")
        self.assertEqual(detail["candidate_inclusion"]["inclusion_reason"]["status"], MODULE.NO_EVIDENCE)
        self.assertEqual(detail["sector_rotation_link"]["symbol_to_sector_binding"]["status"], MODULE.NO_EVIDENCE)
        self.assertEqual(detail["last_evaluation"]["status"], "EVALUATED")
        self.assertEqual(detail["last_evaluation"]["evaluated_at"], self.by_market["KR"]["evaluated"]["evaluated_at"])
        review_row = next(row for row in self.by_market["KR"]["symbols"] if row["symbol"] == symbol)
        self.assertEqual(detail["last_evaluation"]["entry_state"], review_row["entry_state"])
        unmet = {item["condition"] for item in detail["next_step_unmet_conditions"]}
        self.assertTrue(set(review_row["blocking_reasons"]) <= unmet)
        self.assertIn("STAGE_TRANSITION_RULE", unmet)
        self.assertEqual(detail["exclusion_expiry"]["excluded_by_existing_rule"]["rule_status"], MODULE.UNDEFINED)
        self.assertIn(detail["exclusion_expiry"]["validity_window"]["status"], ("ASSESSED", MODULE.NO_EVIDENCE, MODULE.NOT_AVAILABLE))
        self.assertTrue(all(value is False for key, value in detail["authority"].items() if key != "read_only"))

    def test_population_member_that_is_not_a_pipeline_subject_is_not_zeroed(self):
        kr_universe = json.loads(Path(self.inputs["kr_universe_path"]).read_text(encoding="utf-8"))
        subjects = {row["symbol"] for row in self.by_market["KR"]["symbols"]}
        symbol = next(
            record["primary_symbol"] for record in kr_universe["asset_master"]["records"]
            if record["primary_symbol"] not in subjects
        )
        detail = MODULE.lookup_symbol("KR", symbol, generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(detail["population_membership"]["status"], "IN_POPULATION")
        self.assertEqual(detail["candidate_inclusion"]["status"], "NOT_A_PIPELINE_SUBJECT")
        self.assertEqual(detail["last_evaluation"]["status"], MODULE.NO_EVIDENCE)
        self.assertEqual(detail["last_evaluation"]["reason"], "SYMBOL_NOT_A_BOUNDED_REVIEW_SUBJECT")
        self.assertEqual(detail["next_step_unmet_conditions"][0]["condition"], "BOUNDED_REVIEW_COVERAGE")

    def test_us_lookup_keeps_collection_failure_separate_from_policy_gap(self):
        us_rows = self.by_market["US"]["symbols"]
        for row in us_rows:
            detail = MODULE.lookup_symbol("US", row["symbol"], generated_at=self.generated_at, inputs=self.inputs)
            self.assertEqual(detail["population_membership"]["status"], "IN_POPULATION")
            classes = {item["condition"]: item["class"] for item in detail["next_step_unmet_conditions"]}
            if row["price_status"] != "OBSERVED":
                self.assertEqual(classes.get("PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE"), "COLLECTION_FAILED")
            else:
                self.assertNotIn("PIPELINE_SYMBOL_PRICE_HISTORY_UNAVAILABLE", classes)
            self.assertEqual(detail["sector_rotation_link"]["symbol_to_sector_binding"]["status"], MODULE.NO_EVIDENCE)
            self.assertIn(detail["discovery_cases"]["status"], ("CASES_PRESENT", MODULE.NO_EVIDENCE, MODULE.NOT_AVAILABLE))

    def test_unknown_symbol_fails_closed(self):
        for market, symbol in (("KR", "999999"), ("US", "ZZZZNOTASYMBOL"), ("CRYPTO", "KRW-NOPE")):
            with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError) as ctx:
                MODULE.lookup_symbol(market, symbol, generated_at=self.generated_at, inputs=self.inputs)
            self.assertTrue(str(ctx.exception).startswith("SYMBOL_NOT_FOUND"))
        with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
            MODULE.lookup_symbol("XX", "A", generated_at=self.generated_at, inputs=self.inputs)

    def test_lookup_time_before_sources_fails_closed(self):
        with self.assertRaises(COVERAGE_ERROR_TYPES):
            MODULE.build_report(generated_at="2026-01-01T00:00:00Z", inputs=self.inputs)

    def test_tampered_population_count_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = json.loads(Path(self.inputs["kr_universe_path"]).read_text(encoding="utf-8"))
            record["total_count"] = record["total_count"] + 1
            unsigned = copy.deepcopy(record)
            unsigned.pop("payload_sha256", None)
            record["payload_sha256"] = MODULE.payload_sha256(unsigned)
            path = _write(Path(tmp) / "data/observations/krx_global_universe/2026-09-10/packet.json", record)
            inputs = dict(self.inputs)
            inputs["kr_universe_path"] = path
            with self.assertRaises((MODULE.MarketCandidateDiscoveryLookupError, MODULE.COVERAGE.ThreeMarketEvaluationCoverageError)):
                MODULE.build_report(generated_at=self.generated_at, inputs=inputs)

    def test_rehashed_report_authority_flip_is_rejected(self):
        tampered = copy.deepcopy(self.report)
        tampered["authority"]["order_authorized"] = True
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(tampered)
        with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
            MODULE.validate_report(tampered)
        tampered = copy.deepcopy(self.report)
        tampered["markets"][0]["population"]["count"] += 1
        with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
            MODULE.validate_report(tampered)


COVERAGE_ERROR_TYPES = (
    MODULE.MarketCandidateDiscoveryLookupError,
    MODULE.COVERAGE.ThreeMarketEvaluationCoverageError,
)


class PinnedCryptoGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated_at = "2026-09-12T14:15:00Z"
        base = MODULE.default_inputs()
        # Pin every source to packets that existed at the pinned lookup time.
        cls.inputs = dict(base)
        cls.inputs.update(PINNED_CRYPTO)
        cls.inputs.update({
            "kr_universe_path": ROOT / "data/observations/krx_global_universe/2026-09-10/packet.json",
            "kr_review_path": ROOT / "evidence/korea_symbol_market_review/2026-09-10/c3f5e0a35a5c9b03d80f5ef1908d95c3dd20bc82d68c5e1c14d0ce777e0802c6/packet.json",
            "us_universe_path": ROOT / "data/observations/us_global_universe/2026-09-11/packet.json",
            "us_raw_snapshot_dir": ROOT / "evidence/us_breadth/raw/2026-09-11",
            "us_review_path": ROOT / "evidence/us_symbol_market_review/2026-09-12/6cf3eeda4e856a56c0bc2ff3ad85dd5b250fb791c294c7a5c288918e0e5dfc71/packet.json",
            "crypto_bounded_identity_path": ROOT / "data/observations/upbit_bounded_identity_registry/2026-08-29/packet.json",
        })
        cls.report = MODULE.build_report(generated_at=cls.generated_at, inputs=cls.inputs, markets=("CRYPTO",))
        cls.crypto = cls.report["markets"][0]

    def test_exact_pinned_crypto_chain(self):
        row = self.crypto
        self.assertEqual(row["population"]["count"], 282)
        self.assertEqual(row["population"]["as_of"], "2026-09-12")
        self.assertEqual(row["data_acquired"]["daily_candles"]["count"], 282)
        self.assertEqual(row["evaluated"]["count"], 8)
        self.assertEqual(row["evaluated"]["evaluated_at"], "2026-09-12T13:51:24Z")
        self.assertEqual(row["disposition"]["passed"]["count"], 0)
        self.assertEqual(row["disposition"]["held"]["count"], 8)
        self.assertEqual(row["disposition"]["held"]["by_state"], {"WATCH": 8})
        self.assertEqual(row["disposition"]["excluded"]["count"], 274)
        self.assertEqual(
            row["disposition"]["excluded"]["by_reason"],
            {"IDENTITY_UNRATIFIED": 267, "INVESTMENT_WARNING_ACTIVE": 7},
        )
        self.assertEqual(row["disposition"]["unevaluated"]["count"], 0)
        self.assertEqual(row["candidate_zero_semantics"], "CRITERIA_UNKNOWN_NOT_A_NEGATIVE_RESULT")
        self.assertEqual(row["reconciliation"]["detail_view_binding"], MODULE.NOT_AVAILABLE)
        classes = {(gap["class"], gap["code"]): gap["affected_count"] for gap in row["gap_classification"]}
        self.assertEqual(classes[("POLICY_UNDEFINED", "IDENTITY_SCOPE_NOT_RATIFIED_BEYOND_CURRENT_PAPER_EIGHT")], 267)
        self.assertEqual(classes[("EVALUATED_EXCLUDED_BY_RATIFIED_RULE", "INVESTMENT_WARNING_ACTIVE")], 7)
        self.assertEqual(classes[("EVALUATED_CRITERIA_UNKNOWN", "P5_08_CRITERIA_UNKNOWN_FOR_ALL_HELD_MARKETS")], 8)
        self.assertEqual(classes[("POLICY_UNDEFINED", "TREND:NO_RATIFIED_CANDIDATE_TREND_RULE")], 8)
        self.assertNotIn("EVALUATED_NO_CANDIDATE", {gap["class"] for gap in row["gap_classification"]})

    def test_pinned_crypto_symbol_lookups(self):
        btc = MODULE.lookup_symbol("CRYPTO", "BTC", generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(btc["symbol"], "KRW-BTC")
        self.assertEqual(btc["candidate_inclusion"]["status"], "ADMITTED_TO_EVALUATION_INPUT")
        self.assertEqual(btc["candidate_inclusion"]["inclusion_reason"]["status"], "RULE_RECORDED")
        self.assertEqual(btc["candidate_inclusion"]["bounded_identity_verdict"]["status"], "VERIFIED_CANDIDATE")
        self.assertEqual(btc["last_evaluation"]["status"], "EVALUATED")
        self.assertEqual(btc["last_evaluation"]["evaluated_at"], "2026-09-12T13:51:24Z")
        self.assertEqual(btc["last_evaluation"]["state"], "WATCH")
        unmet = {item["condition"]: item["status"] for item in btc["next_step_unmet_conditions"]}
        self.assertEqual(unmet["TREND"], "UNKNOWN")
        self.assertEqual(unmet["P5_09_TRIGGER_AND_ORDER_DRAFT"], "NOT_EVALUATED")
        self.assertNotIn("IDENTITY", unmet)
        self.assertEqual(btc["exclusion_expiry"]["excluded_by_existing_rule"]["status"], "NOT_EXCLUDED")
        self.assertEqual(btc["detail_view"]["status"], MODULE.NOT_AVAILABLE)
        self.assertEqual(btc["discovery_cases"]["status"], "FEATURE_NOT_IMPLEMENTED")

        arb = MODULE.lookup_symbol("CRYPTO", "KRW-ARB", generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(arb["candidate_inclusion"]["status"], "OBSERVATION_POOL_ONLY")
        self.assertEqual(arb["candidate_inclusion"]["bounded_identity_verdict"]["status"], "HOLD_TICKER_COLLISION")
        self.assertEqual(arb["last_evaluation"]["status"], "NOT_EVALUATED_BY_P5_08")
        excluded = arb["exclusion_expiry"]["excluded_by_existing_rule"]
        self.assertEqual(excluded["status"], "EXCLUDED")
        self.assertEqual(excluded["reason"], "IDENTITY_UNRATIFIED")
        self.assertEqual(excluded["rule_status"], "POLICY_UNDEFINED")
        self.assertEqual(arb["next_step_unmet_conditions"][0]["condition"], "P3_12_ADMISSION_TO_TRADEABLE_UNIVERSE")

    def test_detail_view_from_another_generation_is_reported_unbound_not_reused(self):
        detail_path = MODULE._latest_crypto_detail(ROOT / "evidence/crypto_candidate_detail")
        if detail_path is None:
            self.skipTest("no committed crypto candidate detail packet")
        inputs = dict(self.inputs)
        inputs["crypto_detail_path"] = detail_path
        report = MODULE.build_report(generated_at=self.generated_at, inputs=inputs, markets=("CRYPTO",))
        binding = report["markets"][0]["reconciliation"]["detail_view_binding"]
        self.assertIn(binding, ("UNBOUND_DIFFERENT_GENERATION", "BOUND_TO_SAME_DECISION_GENERATION"))
        detail_record = json.loads(detail_path.read_text(encoding="utf-8"))
        expected = (
            "BOUND_TO_SAME_DECISION_GENERATION"
            if detail_record["decision_snapshot"]["generation_id"] == "4aaa821be20c357e201f83fe3b1a2ae3adfd9b533fbe7c7cbc25d31eac06c6ad"
            else "UNBOUND_DIFFERENT_GENERATION"
        )
        self.assertEqual(binding, expected)


class HelperTests(unittest.TestCase):
    def test_interval_status_uses_only_the_source_interval(self):
        within = MODULE._interval_status("2026-09-10", "2026-09-11", dt.date(2026, 9, 11))
        self.assertEqual(within["status_at_generated_date"], "WITHIN_SOURCE_INTERVAL")
        self.assertEqual(within["elapsed_days_after_valid_to"], 0)
        elapsed = MODULE._interval_status("2026-09-10", "2026-09-11", dt.date(2026, 9, 13))
        self.assertEqual(elapsed["status_at_generated_date"], "ELAPSED_SOURCE_INTERVAL")
        self.assertEqual(elapsed["elapsed_days_after_valid_to"], 2)
        before = MODULE._interval_status("2026-09-10", "2026-09-11", dt.date(2026, 9, 9))
        self.assertEqual(before["status_at_generated_date"], "BEFORE_SOURCE_INTERVAL")
        with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
            MODULE._interval_status("2026-09-12", "2026-09-11", dt.date(2026, 9, 13))

    def test_unclassified_reason_codes_stay_undefined(self):
        rows = MODULE._classify_reasons(["FINAL_KOREA_REGIME_POLICY_PENDING", "SOME_FUTURE_CODE"])
        self.assertEqual(rows[0]["class"], "POLICY_UNDEFINED")
        self.assertEqual(rows[1]["class"], f"UNCLASSIFIED_EXISTING_REASON:{MODULE.UNDEFINED}")
        unmet = MODULE._unmet_from_reasons(["KOREA_FIVE_MARKET_AXES_CONNECTED", "PIPELINE_STAGE_IS_NOT_BUY_AUTHORITY", "SOME_FUTURE_CODE"])
        self.assertEqual([row["condition"] for row in unmet], ["SOME_FUTURE_CODE"])

    def test_dated_packet_selection_fails_on_directory_date_mismatch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "packets"
            _write(root / "2026-09-01/packet.json", {"as_of_date": "2026-09-01"})
            _write(root / "2026-09-02/packet.json", {"as_of_date": "2026-09-02"})
            self.assertEqual(MODULE._latest_dated_packet(root, "as_of_date"), root / "2026-09-02/packet.json")
            _write(root / "2026-09-03/packet.json", {"as_of_date": "2026-08-01"})
            with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
                MODULE._latest_dated_packet(root, "as_of_date")
            self.assertIsNone(MODULE._latest_dated_packet(Path(tmp) / "missing", "as_of_date"))

    def test_stage_facts_track_first_seen_and_current_stage_since(self):
        history = {
            "2026-08-13": {"AAA": {"stage": None, "name": "A"}},
            "2026-08-14": {"AAA": {"stage": "Discovery", "name": "A"}},
            "2026-08-15": {"AAA": {"stage": "Candidate", "name": "A"}},
            "2026-08-16": {"AAA": {"stage": "Candidate", "name": "A"}},
        }
        facts = MODULE._stage_facts(history, "AAA")
        self.assertEqual(facts["status"], "STAGED")
        self.assertEqual(facts["first_seen_date"], "2026-08-13")
        self.assertEqual(facts["first_staged_date"], "2026-08-14")
        self.assertEqual(facts["current_stage_since"], "2026-08-15")
        self.assertEqual(MODULE._stage_facts(history, "BBB")["status"], "NOT_IN_STAGE_HISTORY")

    def test_validity_and_case_helpers_report_missing_as_no_evidence(self):
        self.assertEqual(MODULE._validity_rows(None, {"KOREA"}, {"000660"})["status"], MODULE.NOT_AVAILABLE)
        assessment = {"candidate_assessments": [], "evaluation_at_utc": "2026-09-11T01:36:08Z", "contract_version": "x"}
        self.assertEqual(MODULE._validity_rows(assessment, {"KOREA"}, {"000660"})["status"], MODULE.NO_EVIDENCE)
        self.assertEqual(MODULE._discovery_case_facts(None, {"US"}, "TSM")["status"], MODULE.NOT_AVAILABLE)
        packet = {"cases": [], "source_coverage": {"crypto": "NOT_IMPLEMENTED"}, "packet_sha256": "0" * 64}
        self.assertEqual(MODULE._discovery_case_facts(packet, {"CRYPTO"}, "BTC")["status"], "FEATURE_NOT_IMPLEMENTED")
        self.assertEqual(MODULE._discovery_case_facts(packet, {"US"}, "TSM")["status"], MODULE.NO_EVIDENCE)


if __name__ == "__main__":
    unittest.main()
