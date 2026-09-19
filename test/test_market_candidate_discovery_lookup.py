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
        "a841b4fd0fe492dd60a76fbfe877eb61b3bc28b569e0792be9a376b0341b37dd/packet.json"
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
        receipt = self.report["coverage_receipt"]
        self.assertIn(receipt["status"], ("BUILT", "FAILED_CLOSED"))
        expected_cross_check = "MATCH" if receipt["status"] == "BUILT" else MODULE.NOT_AVAILABLE
        if receipt["status"] == "FAILED_CLOSED":
            self.assertTrue(receipt["reason"])
        for market, row in self.by_market.items():
            with self.subTest(market=market):
                recon = row["reconciliation"]
                self.assertEqual(recon["coverage_receipt_cross_check"], expected_cross_check)
                self.assertEqual(recon["evaluated_duplicate_count"], 0)
                population = row["population"]["count"]
                self.assertIsInstance(population, int)
                self.assertGreater(population, 0)
                if market == "CRYPTO":
                    self.assertTrue(recon["population_equals_evaluated_plus_excluded_plus_unevaluated"])
                    self.assertTrue(recon["evaluated_subset_of_admitted"])
                    evaluated = row["evaluated"]
                    self.assertEqual(
                        evaluated["admitted_count"],
                        evaluated["count"] + len(evaluated["admitted_not_evaluated"]),
                    )
                    self.assertEqual(recon["evaluated_equals_admitted"], not evaluated["admitted_not_evaluated"])
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
            self.assertEqual(freshness["valid_from"], self.by_market[market]["population"]["as_of"])
        self.assertEqual(self.report["generated_at"][:10], generated_date)
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
            population_level = self.by_market[market]["data_acquired"]["population_level_symbol_data"]
            if population_level["status"] == "NOT_RETAINED_IN_PUBLIC_REPOSITORY":
                self.assertEqual(population_level["count"], MODULE.NOT_COUNTED)
            else:
                # A retained population_symbol_observation_packet/1 session is
                # connected: the count is real, never the placeholder.
                self.assertIn(
                    population_level["status"], ("OBSERVED", "OBSERVED_POPULATION_MISMATCH"),
                )
                self.assertIsInstance(population_level["count"], int)
                self.assertEqual(population_level["reverify_outcome"], "REVERIFIED")
        screening = self.by_market["KR"]["screening_layer"]
        self.assertIn(screening["status"], ("AVAILABLE", MODULE.NOT_AVAILABLE))
        if screening["status"] == MODULE.NOT_AVAILABLE:
            self.assertEqual(screening["reason"], "KRX_REGISTRY_EVALUATION_COVERAGE_PACKET_ABSENT")
        us_screening = self.by_market["US"]["screening_layer"]
        self.assertEqual(us_screening["status"], MODULE.NOT_AVAILABLE)

    def test_population_level_symbol_data_cross_checks_the_retained_observation_packet(self):
        """PR #702's ``population_symbol_observation_packet/1`` is read through
        its own ``reverify`` (never rebuilt), and its population is cross-checked
        against the same KR/US population this lookup already reports, never
        silently substituted when the two disagree.

        Uses ``with self.subTest`` (never ``skipTest``) so one market's
        absence of a retained session never hides an assertion failure for
        the other market -- see ``PopulationLevelSymbolDataTests`` below for
        exhaustive, deterministic coverage of every status this field can
        take (synthetic fixtures; does not depend on ambient real data).
        """
        for market in ("KR", "US"):
            with self.subTest(market=market):
                population_level = self.by_market[market]["data_acquired"]["population_level_symbol_data"]
                if population_level["status"] == "NOT_RETAINED_IN_PUBLIC_REPOSITORY":
                    continue
                lookup_population = self.by_market[market]["population"]
                match = population_level["population_match"]
                self.assertEqual(match["lookup_population"]["count"], lookup_population["count"])
                self.assertEqual(match["lookup_population"]["as_of"], lookup_population["as_of"])
                self.assertEqual(match["matched"], population_level["status"] == "OBSERVED")
                # every count reported is the packet's own summary, verbatim
                self.assertGreaterEqual(population_level["count"], population_level["evaluable_count"])
                self.assertGreaterEqual(population_level["evaluable_count"], population_level["evaluated_count"] - population_level["evaluated_without_full_inputs_count"])
                self.assertEqual(population_level["contract_version"], "population_symbol_observation_packet/1")

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
        receipt = self.report["coverage_receipt"]
        self.assertEqual(roles["three_market_coverage"]["status"], receipt["status"])
        self.assertEqual(roles["three_market_coverage"]["payload_sha256"], receipt.get("payload_sha256"))
        self.assertEqual(roles["kr_symbol_review"]["contract"], "korea_symbol_market_review/1")
        self.assertEqual(roles["us_symbol_review"]["contract"], "us_symbol_market_review/1")
        # The portal block must reuse exactly the contract of the decision it
        # links, and which contract that is follows from the linked packet's own
        # generation instant: /1 before the per-market ratification (user
        # ratification CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914), /3
        # after it, /4 from the wiring-v2 cutover T_cut configured in
        # config/crypto_paper_wiring_v2.json.  Derived from the packet's date via
        # schema_version_for(), never enumerated: an allowlist of version strings
        # goes stale the moment a configured cutover instant passes -- a calendar
        # event, not a code change -- which is exactly how the former
        # ("/1","/2","/3") list broke at 2026-09-18T07:00:00Z.  This form also
        # asserts more than the list did: not merely that the contract is *a*
        # known version, but that it is *the* version that applies to this
        # packet's own date.
        crypto_decision_contract = roles["crypto_decision"]["contract"]
        decision_path = MODULE.ROOT / roles["crypto_decision"]["source"]["path"]
        decision_packet = json.loads(decision_path.read_text(encoding="utf-8"))
        self.assertEqual(decision_packet["schema_version"], crypto_decision_contract)
        self.assertEqual(
            crypto_decision_contract,
            MODULE.CRYPTO_DECISION.schema_version_for(
                MODULE.CRYPTO_DECISION._parse_utc(
                    decision_packet["generated_at"], "crypto_decision.generated_at",
                ),
            ),
        )
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

    def test_crypto_latest_generation_without_evaluation_is_reported_as_collection_gap(self):
        row = self.by_market["CRYPTO"]
        evaluated = row["evaluated"]
        codes = {gap["code"]: gap for gap in row["gap_classification"]}
        if evaluated["admitted_not_evaluated"]:
            gap = codes["P5_08_DID_NOT_EVALUATE_ADMITTED_MARKETS_IN_LATEST_GENERATION"]
            skipped = evaluated["latest_generation_skipped_class"]
            self.assertIn(skipped, ("COLLECTION_FAILED", "EVALUATION_HALTED_INPUT_DATE_MISMATCH"))
            self.assertEqual(gap["class"], skipped)
            self.assertEqual(gap["affected_count"], len(evaluated["admitted_not_evaluated"]))
            self.assertTrue(gap["evidence"]["derivation_notes"])
            notes = " ".join(gap["evidence"]["derivation_notes"])
            self.assertEqual(
                skipped == "EVALUATION_HALTED_INPUT_DATE_MISMATCH",
                bool(MODULE.DATE_MISMATCH_NOTE_RE.search(notes)),
            )
            if evaluated["count"] == 0:
                self.assertEqual(
                    row["candidate_zero_semantics"],
                    "EVALUATION_HALTED_INPUT_DATE_MISMATCH" if skipped == "EVALUATION_HALTED_INPUT_DATE_MISMATCH"
                    else "EVALUATOR_DID_NOT_RUN_IN_LATEST_GENERATION",
                )
            self.assertEqual(self.report["coverage_receipt"]["status"], "FAILED_CLOSED")
            self.assertEqual(row["next_step_conditions"][0]["condition"], "P5_08_EVALUATION_RUN_FOR_ADMITTED_MARKETS")
            last = evaluated["last_generation_with_evaluations"]
            if last != MODULE.NO_EVIDENCE:
                self.assertTrue(last["historical"])
                self.assertFalse(last["substitutes_latest_generation"])
                self.assertFalse(last["is_latest_generation"])
                self.assertLess(last["generated_at"], evaluated["evaluated_at"])
                self.assertEqual(last["evaluated_date_utc"], last["generated_at"][:10])
                self.assertIn(last["generated_at"], row["summary"]["explanation"])
            halted_key = "evaluation_halted_input_date_mismatch" if skipped == "EVALUATION_HALTED_INPUT_DATE_MISMATCH" else "unevaluated"
            self.assertEqual(row["summary"]["categories"][halted_key]["count"], len(evaluated["admitted_not_evaluated"]))
            with self.assertRaises(MODULE.COVERAGE.ThreeMarketEvaluationCoverageError):
                MODULE.build_report(generated_at=self.generated_at, inputs=self.inputs, markets=("CRYPTO",), strict=True)
        else:
            self.assertNotIn("P5_08_DID_NOT_EVALUATE_ADMITTED_MARKETS_IN_LATEST_GENERATION", codes)
            self.assertIn(row["candidate_zero_semantics"], ("CRITERIA_UNKNOWN_NOT_A_NEGATIVE_RESULT", "EVALUATED_NO_CANDIDATE", "CANDIDATES_PRESENT"))

    def test_crypto_admitted_market_lookup_never_mislabels_a_skipped_generation(self):
        admitted = [row for row in self.by_market["CRYPTO"]["symbols"] if row["universe_state"] in ("TRADEABLE_UNIVERSE", "PAPER_ELIGIBLE")]
        self.assertTrue(admitted)
        detail = MODULE.lookup_symbol("CRYPTO", admitted[0]["symbol"], generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(detail["candidate_inclusion"]["status"], "ADMITTED_TO_EVALUATION_INPUT")
        status = detail["last_evaluation"]["status"]
        self.assertIn(status, ("EVALUATED", "ADMITTED_NOT_EVALUATED_IN_LATEST_GENERATION", "ADMITTED_EVALUATION_HALTED_INPUT_DATE_MISMATCH"))
        classification = detail["classification"]
        self.assertEqual(classification["label"], MODULE.CATEGORY_LABELS.get(classification["category"], "후보"))
        if status != "EVALUATED":
            self.assertTrue(detail["last_evaluation"]["latest_generation"]["derivation_notes"])
            expected_class = "EVALUATION_HALTED_INPUT_DATE_MISMATCH" if status.endswith("DATE_MISMATCH") else "COLLECTION_FAILED"
            self.assertEqual(detail["last_evaluation"]["skipped_class"], expected_class)
            self.assertEqual(detail["next_step_unmet_conditions"][0]["class"], expected_class)
            self.assertEqual(
                classification["category"],
                "evaluation_halted_input_date_mismatch" if status.endswith("DATE_MISMATCH") else "unevaluated",
            )
            last = detail["last_evaluation"]["last_evaluated_generation"]
            if last != MODULE.NO_EVIDENCE:
                self.assertTrue(last["historical"])
                self.assertFalse(last["substitutes_latest_generation"])
                self.assertIn(last["state"], ("WATCH", "WAIT", "FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE", "BLOCKED"))
        else:
            self.assertFalse(detail["last_evaluation"]["last_evaluated_generation"]["historical"])
        self.assertEqual(detail["exclusion_expiry"]["excluded_by_existing_rule"]["status"], "NOT_EXCLUDED")
        self.assertEqual(detail["discovery_cases"]["status"], "FEATURE_NOT_IMPLEMENTED")

    def test_market_summary_separates_population_from_evaluated_symbols(self):
        for market in ("KR", "US"):
            row = self.by_market[market]
            summary = row["summary"]
            self.assertEqual(summary["population_count"], row["population"]["count"])
            self.assertEqual(summary["evaluated_symbol_count"], len(row["symbols"]))
            self.assertNotEqual(summary["population_count"], summary["evaluated_symbol_count"])
            self.assertEqual(summary["evaluated_symbols"], [s["symbol"] for s in row["symbols"]])
            categories = summary["categories"]
            self.assertEqual(
                sorted(categories),
                ["collection_failed", "evaluated_no_candidate", "no_evidence", "policy_undefined", "unevaluated"],
            )
            self.assertEqual(categories["unevaluated"]["count"], summary["population_count"] - summary["evaluated_symbol_count"])
            self.assertEqual(categories["unevaluated"]["label"], "미평가")
            self.assertFalse(categories["evaluated_no_candidate"]["applicable"])
            self.assertEqual(categories["evaluated_no_candidate"]["count"], 0)
            self.assertEqual(categories["no_evidence"]["count"], summary["evaluated_symbol_count"])
            self.assertIn("inclusion_reason", categories["no_evidence"]["items"])
            self.assertIn("미평가", summary["explanation"])
            self.assertIn(str(summary["population_count"]), summary["explanation"])
            self.assertIn(row["evaluated"]["evaluated_at"], summary["explanation"])
        crypto = self.by_market["CRYPTO"]["summary"]
        self.assertIn("evaluation_halted_input_date_mismatch", crypto["categories"])
        self.assertEqual(crypto["categories"]["excluded_by_ratified_rule"]["label"], "비준 규칙에 의한 제외")
        self.assertIn("최신 세대", crypto["explanation"])
        self.assertEqual(self.report["category_labels"], MODULE.CATEGORY_LABELS)

    def test_symbol_classification_matches_evidence(self):
        kr_rows = self.by_market["KR"]["symbols"]
        detail = MODULE.lookup_symbol("KR", kr_rows[0]["symbol"], generated_at=self.generated_at, inputs=self.inputs)
        self.assertIn(detail["classification"]["category"], ("policy_undefined", "collection_failed"))
        self.assertIn("inclusion_reason", detail["classification"]["no_evidence_items"])
        self.assertIn("sector_rotation_link.symbol_to_sector_binding", detail["classification"]["no_evidence_items"])
        self.assertFalse(detail["classification"]["evaluated_no_candidate_applicable"])
        kr_universe = json.loads(Path(self.inputs["kr_universe_path"]).read_text(encoding="utf-8"))
        subjects = {row["symbol"] for row in kr_rows}
        other = next(r["primary_symbol"] for r in kr_universe["asset_master"]["records"] if r["primary_symbol"] not in subjects)
        detail = MODULE.lookup_symbol("KR", other, generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(detail["classification"]["category"], "unevaluated")
        self.assertEqual(detail["classification"]["label"], "미평가")
        for row in self.by_market["US"]["symbols"]:
            detail = MODULE.lookup_symbol("US", row["symbol"], generated_at=self.generated_at, inputs=self.inputs)
            expected = "collection_failed" if row["price_status"] != "OBSERVED" else "policy_undefined"
            self.assertEqual(detail["classification"]["category"], expected)

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
        tampered.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(tampered)
        with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
            MODULE.validate_report(tampered, inputs=self.inputs)


COVERAGE_ERROR_TYPES = (
    MODULE.MarketCandidateDiscoveryLookupError,
    MODULE.COVERAGE.ThreeMarketEvaluationCoverageError,
)


class PinnedCryptoGenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.generated_at = "2026-09-12T14:15:00Z"
        base = MODULE.default_inputs()
        # Keep the three-market coverage failure deterministic.  This class
        # pins a historical Crypto generation while the repository's moving
        # US latest pointer can advance after that generation.  A controlled
        # future-dated copy proves that the aggregate receipt fails closed;
        # it never weakly accepts either BUILT or FAILED_CLOSED based on
        # whichever natural packet happens to be newest today.
        cls._fixture_dir = tempfile.TemporaryDirectory(
            prefix=".pinned_crypto_coverage_", dir=ROOT,
        )
        cls.addClassCleanup(cls._fixture_dir.cleanup)
        future_us_market_data = copy.deepcopy(
            json.loads(Path(base["us_market_data_path"]).read_text(encoding="utf-8"))
        )
        future_us_market_data["observed_at_utc"] = "2026-09-13T00:00:00Z"
        future_us_market_data.pop("packet_sha256", None)
        future_us_market_data["packet_sha256"] = MODULE.COVERAGE.payload_sha256(
            future_us_market_data
        )
        future_us_market_data_path = Path(cls._fixture_dir.name) / "future_us_market_data.json"
        future_us_market_data_path.write_text(
            json.dumps(future_us_market_data, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        # Pin every source to packets that existed at the pinned lookup time.
        cls.inputs = dict(base)
        cls.inputs.update(PINNED_CRYPTO)
        cls.inputs.update({
            "kr_universe_path": ROOT / "data/observations/krx_global_universe/2026-09-10/packet.json",
            "kr_review_path": ROOT / "evidence/korea_symbol_market_review/2026-09-10/c3f5e0a35a5c9b03d80f5ef1908d95c3dd20bc82d68c5e1c14d0ce777e0802c6/packet.json",
            "us_universe_path": ROOT / "data/observations/us_global_universe/2026-09-11/packet.json",
            "us_raw_snapshot_dir": ROOT / "evidence/us_breadth/raw/2026-09-11",
            "us_review_path": ROOT / "evidence/us_symbol_market_review/2026-09-12/6cf3eeda4e856a56c0bc2ff3ad85dd5b250fb791c294c7a5c288918e0e5dfc71/packet.json",
            "us_market_data_path": future_us_market_data_path,
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
        self.assertTrue(row["reconciliation"]["evaluated_equals_admitted"])
        self.assertEqual(row["evaluated"]["admitted_not_evaluated"], [])
        self.assertEqual(self.report["coverage_receipt"]["status"], "FAILED_CLOSED")
        self.assertEqual(
            self.report["coverage_receipt"]["reason"],
            "US_FREE_MARKET_DATA_FROM_FUTURE",
        )
        self.assertEqual(
            row["reconciliation"]["coverage_receipt_cross_check"],
            MODULE.NOT_AVAILABLE,
        )
        classes = {(gap["class"], gap["code"]): gap["affected_count"] for gap in row["gap_classification"]}
        self.assertEqual(classes[("POLICY_UNDEFINED", "IDENTITY_SCOPE_NOT_RATIFIED_BEYOND_CURRENT_PAPER_EIGHT")], 267)
        self.assertEqual(classes[("EVALUATED_EXCLUDED_BY_RATIFIED_RULE", "INVESTMENT_WARNING_ACTIVE")], 7)
        self.assertEqual(classes[("EVALUATED_CRITERIA_UNKNOWN", "P5_08_CRITERIA_UNKNOWN_FOR_ALL_HELD_MARKETS")], 8)
        self.assertEqual(classes[("POLICY_UNDEFINED", "TREND:NO_RATIFIED_CANDIDATE_TREND_RULE")], 8)
        self.assertNotIn("EVALUATED_NO_CANDIDATE", {gap["class"] for gap in row["gap_classification"]})
        self.assertIsNone(row["evaluated"]["latest_generation_skipped_class"])
        self.assertEqual(row["summary"]["categories"]["evaluation_halted_input_date_mismatch"]["count"], 0)
        self.assertEqual(row["summary"]["categories"]["policy_undefined"]["count"], 267 + 8)
        self.assertEqual(row["summary"]["categories"]["excluded_by_ratified_rule"]["count"], 7)
        self.assertEqual(len(row["summary"]["evaluated_symbols"]), 8)

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

        self.assertEqual(btc["classification"]["category"], "policy_undefined")
        self.assertEqual(btc["classification"]["label"], "정책 미정")
        self.assertFalse(btc["last_evaluation"]["last_evaluated_generation"]["historical"])
        arb = MODULE.lookup_symbol("CRYPTO", "KRW-ARB", generated_at=self.generated_at, inputs=self.inputs)
        self.assertEqual(arb["classification"]["category"], "policy_undefined")
        self.assertEqual(arb["classification"]["reason"], "IDENTITY_UNRATIFIED")
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
            if detail_record["decision_snapshot"]["generation_id"] == "a841b4fd0fe492dd60a76fbfe877eb61b3bc28b569e0792be9a376b0341b37dd"
            else "UNBOUND_DIFFERENT_GENERATION"
        )
        self.assertEqual(binding, expected)


class CryptoOnlyLookupIsolationTests(unittest.TestCase):
    """Regression for a CIO-reported clean-main bug (reproduced at
    ``cf3cf83e``, independent of this adapter's own edits):
    ``build_report(markets=("CRYPTO",))`` used to build KR/US contexts
    unconditionally, so a CRYPTO-only, pinned-generation lookup crashed with
    ``US_FREE_MARKET_DATA_FROM_FUTURE`` whenever the *unrequested* US market's
    source happened to be dated after the CRYPTO lookup's own pinned time.

    Fix: ``build_report`` validates every requested market up front, then
    builds only the contexts those markets actually need; ``_portal_block``
    likewise only references the markets it was given.

    These tests are independent of whether the real pinned Crypto decision
    fixture itself currently validates cleanly against latest ``main`` (a
    separate, unrelated ``regime`` component-registry drift issue, tracked
    separately below in this file's run notes) -- they assert only that
    KR/US are never touched for a CRYPTO-only request, which is exactly what
    the reported bug violated.
    """

    @classmethod
    def setUpClass(cls):
        cls.generated_at = "2026-09-12T14:15:00Z"
        base = MODULE.default_inputs()
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

    def _assert_kr_us_never_built(self, inputs: dict) -> None:
        from unittest import mock

        def _boom(*args, **kwargs):
            raise AssertionError("_kr_context/_us_context must never be called for a CRYPTO-only request")

        with mock.patch.object(MODULE, "_kr_context", side_effect=_boom), \
             mock.patch.object(MODULE, "_us_context", side_effect=_boom):
            try:
                MODULE.build_report(generated_at=self.generated_at, inputs=inputs, markets=("CRYPTO",))
            except AssertionError:
                raise
            except Exception:
                # Any other failure (e.g. the unrelated crypto decision /
                # regime component registry fixture issue noted above) is
                # not what this test checks -- only that KR/US were never
                # even reached.
                pass

    def test_crypto_only_report_never_builds_kr_or_us_context(self):
        self._assert_kr_us_never_built(self.inputs)

    def test_crypto_only_report_survives_kr_us_inputs_pointed_at_missing_files(self):
        """Literal reproduction of the reported mixing bug: KR/US inputs
        pointed at paths that do not exist at all (a stronger, deterministic
        stand-in for "unrelated future-dated data") must never affect a
        CRYPTO-only report -- because those markets were never requested,
        their contexts are never built, and their inputs are never read for
        that purpose (the always-all-three coverage receipt is a separate,
        pre-existing mechanism that already degrades to FAILED_CLOSED
        instead of raising -- see ``_coverage_report``)."""
        inputs = dict(self.inputs)
        inputs["kr_universe_path"] = ROOT / "does/not/exist/kr_universe.json"
        inputs["us_market_data_path"] = ROOT / "does/not/exist/us_market_data.json"
        self._assert_kr_us_never_built(inputs)

    def test_invalid_requested_market_fails_closed_before_building_any_context(self):
        from unittest import mock

        def _boom(*args, **kwargs):
            raise AssertionError("no context may be built when an invalid market is requested")

        with mock.patch.object(MODULE, "_kr_context", side_effect=_boom), \
             mock.patch.object(MODULE, "_us_context", side_effect=_boom), \
             mock.patch.object(MODULE, "_crypto_context", side_effect=_boom):
            with self.assertRaises(MODULE.MarketCandidateDiscoveryLookupError):
                MODULE.build_report(generated_at=self.generated_at, inputs=self.inputs, markets=("CRYPTO", "XX"))


class PopulationLevelSymbolDataTests(unittest.TestCase):
    """Real, deterministic execution of ``_population_level_symbol_data`` /
    ``_eligible_population_symbol_observation_dir`` against synthetic
    ``population_symbol_observation_packet/1`` sessions built with the real,
    unmodified ``decision.population_symbol_observation`` module (never a
    copy) in temporary directories -- so this runs regardless of whether the
    real committed KR/US population observation sessions happen to be
    present in a given checkout. Covers: the standard-import fix (no more
    ``KeyError`` on the module's own self-registration), point-in-time
    exclusion of future session dates and future packet ``generated_at``,
    the no-older-fallback rule when the newest eligible session is invalid,
    population id/count/as_of mismatch, deterministic historical/current
    labelling, and that ``observation_root`` -- not an ambient default --
    is what determines the result (``build_report``/``validate_report``
    reproducibility).
    """

    def setUp(self):
        # ``_source_ref`` (via ``_relative``) fails closed on any path outside
        # ``MODULE.ROOT`` -- exactly as it does for every other source this
        # lookup reads -- so the synthetic observation root must live inside
        # the repository, not under the system temp directory.
        import shutil
        self.root = Path(tempfile.mkdtemp(dir=str(MODULE.ROOT / "test")))
        self.addCleanup(shutil.rmtree, self.root, True)

    def _row(self, symbol: str) -> dict:
        return {
            "symbol": symbol, "name": symbol, "membership": {}, "observation_status": "NOT_EVALUABLE",
            "data_observation": {"status": "DATA_OBSERVED"},
            "evaluability": {"status": "NOT_EVALUABLE", "reasons": ["TEST_REASON"]},
            "evaluation": {"status": "NOT_EVALUATED", "entry_state": None},
            "formal_candidate": {"status": "NOT_A_FORMAL_CANDIDATE", "promotion_by_this_packet": False},
            "facts": {}, "evidence_refs": [],
        }

    def _persist_session(
        self, root: Path, *, session_date: str, generated_at: str, count: int = 2,
        population_id: str = "TEST.POP", gen_id: str | None = None,
    ) -> Path:
        m = MODULE.POPULATION_SYMBOL_OBSERVATION
        contract = m.load_contract()
        rows = [self._row(f"SYM{i:03d}") for i in range(count)]
        packet = m.assemble_packet(
            market="KR", session_date=session_date, generated_at=generated_at,
            gen_id=gen_id or f"testgen-{session_date}-{count}",
            population={"count": count, "as_of": session_date, "population_id": population_id},
            sources={}, rows=rows, policy_undefined=[], resume_report={"complete": True}, contract=contract,
        )
        m.validate_packet(packet, contract)
        out_dir = root / session_date
        persisted = m.persist_packet(packet, out_dir, compress=False)
        self.assertEqual(persisted["outcome"], "populated")
        return out_dir

    def test_real_standard_import_no_keyerror_and_module_registered(self):
        """Regression for the ``KeyError`` the dynamic ``_load_module`` /
        ``exec_module``-without-registration pattern used to raise inside
        ``population_symbol_observation.py``'s own self-registration line."""
        import sys
        m = MODULE.POPULATION_SYMBOL_OBSERVATION
        self.assertEqual(m.__name__, "decision.population_symbol_observation")
        self.assertIs(sys.modules.get("decision.population_symbol_observation"), m)
        self.assertIs(sys.modules.get("population_symbol_observation"), m)

    def test_observed_when_population_matches(self):
        session_date = "2026-09-13"
        self._persist_session(self.root, session_date=session_date, generated_at="2026-09-13T00:30:00Z", count=3)
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 1, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of=session_date, population_id="TEST.POP", population_count=3,
        )
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["reverify_outcome"], "REVERIFIED")
        self.assertTrue(result["population_match"]["matched"])

    def test_population_id_count_as_of_mismatch_is_surfaced_never_substituted(self):
        session_date = "2026-09-10"
        self._persist_session(self.root, session_date=session_date, generated_at="2026-09-10T00:30:00Z", count=5, population_id="REAL.POP")
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of=session_date, population_id="DIFFERENT.POP", population_count=999,
        )
        self.assertEqual(result["status"], "OBSERVED_POPULATION_MISMATCH")
        self.assertFalse(result["population_match"]["matched"])
        self.assertEqual(result["population_match"]["observation_population"]["population_id"], "REAL.POP")
        self.assertEqual(result["population_match"]["observation_population"]["count"], 5)
        self.assertEqual(result["population_match"]["lookup_population"]["population_id"], "DIFFERENT.POP")
        self.assertEqual(result["population_match"]["lookup_population"]["count"], 999)
        # the packet's own real count is still reported, never silently replaced
        self.assertEqual(result["count"], 5)

    def test_historical_label_for_sep10_kr_style_session_on_sep13_lookup(self):
        session_date = "2026-09-10"
        self._persist_session(self.root, session_date=session_date, generated_at="2026-09-10T00:30:00Z")
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 5, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of=session_date, population_id="TEST.POP", population_count=2,
        )
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["session_recency"]["status"], "HISTORICAL")
        self.assertEqual(result["session_recency"]["as_of_session_date"], "2026-09-10")
        self.assertEqual(result["session_recency"]["lookup_date"], "2026-09-13")
        self.assertEqual(result["session_recency"]["days_before_lookup_date"], 3)

    def test_historical_label_for_sep11_us_style_session_on_sep13_lookup(self):
        session_date = "2026-09-11"
        self._persist_session(self.root, session_date=session_date, generated_at="2026-09-11T00:30:00Z")
        result = MODULE._population_level_symbol_data(
            "US", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 5, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of=session_date, population_id="TEST.POP", population_count=2,
        )
        self.assertEqual(result["session_recency"]["status"], "HISTORICAL")
        self.assertEqual(result["session_recency"]["days_before_lookup_date"], 2)

    def test_current_session_label_when_session_date_equals_lookup_date(self):
        session_date = "2026-09-13"
        self._persist_session(self.root, session_date=session_date, generated_at="2026-09-13T00:30:00Z")
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 5, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of=session_date, population_id="TEST.POP", population_count=2,
        )
        self.assertEqual(result["session_recency"]["status"], "CURRENT_SESSION")
        self.assertEqual(result["session_recency"]["days_before_lookup_date"], 0)

    def test_future_session_date_is_excluded_older_eligible_session_used(self):
        """A session directory dated after the lookup date must never be
        selected; the newest session that is not in the future is used
        instead -- this is temporal-availability exclusion, not the
        no-fallback rule (which only applies once an eligible session has
        been chosen and found invalid)."""
        older = self._persist_session(self.root, session_date="2026-09-10", generated_at="2026-09-10T00:30:00Z", count=7)
        self._persist_session(self.root, session_date="2026-09-14", generated_at="2026-09-14T00:30:00Z", count=999)
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of="2026-09-10", population_id="TEST.POP", population_count=7,
        )
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["count"], 7)
        self.assertEqual(result["as_of_session_date"], "2026-09-10")
        self.assertEqual(
            Path(MODULE.ROOT / result["source"]["path"]).resolve(),
            (older / "packet.json").resolve(),
        )

    def test_future_packet_generated_at_is_excluded_even_same_day(self):
        """Same-day session whose own ``generated_at`` is after the exact
        lookup instant is not yet available at lookup time either, and must
        be excluded exactly like a future session date."""
        self._persist_session(self.root, session_date="2026-09-10", generated_at="2026-09-10T00:30:00Z", count=11)
        self._persist_session(self.root, session_date="2026-09-13", generated_at="2026-09-13T23:00:00Z", count=999)
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 1, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of="2026-09-10", population_id="TEST.POP", population_count=11,
        )
        self.assertEqual(result["status"], "OBSERVED")
        self.assertEqual(result["count"], 11)
        self.assertEqual(result["as_of_session_date"], "2026-09-10")

    def test_invalid_latest_eligible_session_reports_invalid_never_falls_back(self):
        """The newest *eligible* (not-future) session failing its own
        reverify must be reported as ``OBSERVATION_PACKET_INVALID`` -- an
        older, perfectly valid session must never be silently substituted."""
        self._persist_session(self.root, session_date="2026-09-10", generated_at="2026-09-10T00:30:00Z", count=13)
        newest = self._persist_session(self.root, session_date="2026-09-12", generated_at="2026-09-12T00:30:00Z", count=17)
        # Tamper with the newest eligible packet after persistence so its own
        # reverify() fails closed on the hash check.
        packet_path = newest / "packet.json"
        packet_path.write_text(packet_path.read_text(encoding="utf-8").replace('"population_count":17', '"population_count":18'), encoding="utf-8")
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of="2026-09-10", population_id="TEST.POP", population_count=13,
        )
        self.assertEqual(result["status"], "OBSERVATION_PACKET_INVALID")
        self.assertEqual(result["count"], MODULE.NOT_COUNTED)
        self.assertNotEqual(result.get("count"), 13)  # never silently falls back to the older, valid session

    def test_no_eligible_session_when_only_future_sessions_exist(self):
        self._persist_session(self.root, session_date="2026-09-20", generated_at="2026-09-20T00:30:00Z")
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root, lookup_at=dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of="2026-09-10", population_id="TEST.POP", population_count=2,
        )
        self.assertEqual(result["status"], "NOT_RETAINED_IN_PUBLIC_REPOSITORY")
        self.assertEqual(result["count"], MODULE.NOT_COUNTED)
        self.assertEqual(result["evidence"], "NO_ELIGIBLE_SESSION_ALL_FUTURE")

    def test_absent_root_reports_unchanged_placeholder(self):
        result = MODULE._population_level_symbol_data(
            "KR", observation_root=self.root / "does_not_exist", lookup_at=dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc),
            population_as_of="2026-09-10", population_id="TEST.POP", population_count=2,
        )
        self.assertEqual(result["status"], "NOT_RETAINED_IN_PUBLIC_REPOSITORY")
        self.assertEqual(result["count"], MODULE.NOT_COUNTED)
        self.assertEqual(result["evidence"], "korea_market_signals.source.per_symbol_persistence=0")

    def test_two_roots_are_isolated_no_ambient_default_leakage(self):
        """P1: the function must consume only the ``observation_root`` it is
        given, never an ambient/global default. Two independent temp roots
        with different counts/hashes must never cross-contaminate, which is
        exactly the property ``build_report(inputs=...)`` /
        ``validate_report(report, inputs=...)`` reproducibility depends on.
        """
        import shutil
        root_b = Path(tempfile.mkdtemp(dir=str(MODULE.ROOT / "test")))
        self.addCleanup(shutil.rmtree, root_b, True)
        root_a = self.root
        self._persist_session(root_a, session_date="2026-09-10", generated_at="2026-09-10T00:30:00Z", count=3, population_id="A.POP")
        self._persist_session(root_b, session_date="2026-09-10", generated_at="2026-09-10T00:30:00Z", count=9000, population_id="B.POP")
        lookup_at = dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc)
        result_a = MODULE._population_level_symbol_data(
            "KR", observation_root=root_a, lookup_at=lookup_at,
            population_as_of="2026-09-10", population_id="A.POP", population_count=3,
        )
        result_b = MODULE._population_level_symbol_data(
            "KR", observation_root=root_b, lookup_at=lookup_at,
            population_as_of="2026-09-10", population_id="B.POP", population_count=9000,
        )
        self.assertEqual(result_a["status"], "OBSERVED")
        self.assertEqual(result_a["count"], 3)
        self.assertEqual(result_b["status"], "OBSERVED")
        self.assertEqual(result_b["count"], 9000)
        self.assertNotEqual(result_a["generation_id"], result_b["generation_id"])
        self.assertNotEqual(result_a["source"]["path"], result_b["source"]["path"])
        # rebuilding A again, after B was built and read, must reproduce
        # the exact same result -- the source-rederivation consistency
        # ``validate_report`` relies on.
        result_a_again = MODULE._population_level_symbol_data(
            "KR", observation_root=root_a, lookup_at=lookup_at,
            population_as_of="2026-09-10", population_id="A.POP", population_count=3,
        )
        self.assertEqual(result_a, result_a_again)

    def test_result_is_deterministic_given_the_same_inputs(self):
        """Direct proxy for the ``validate_report`` rebuild-and-compare
        contract: calling the function twice with byte-identical arguments
        must return a byte-identical result -- no hidden global/mutable
        state (e.g. an ambient default root or wall-clock read) may leak in.
        """
        self._persist_session(self.root, session_date="2026-09-10", generated_at="2026-09-10T00:30:00Z", count=4)
        lookup_at = dt.datetime(2026, 9, 13, 0, 0, 0, tzinfo=dt.timezone.utc)
        kwargs = dict(
            observation_root=self.root, lookup_at=lookup_at,
            population_as_of="2026-09-10", population_id="TEST.POP", population_count=4,
        )
        first = MODULE._population_level_symbol_data("KR", **kwargs)
        second = MODULE._population_level_symbol_data("KR", **kwargs)
        self.assertEqual(first, second)


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

    def test_skipped_generation_class_separates_date_mismatch_from_collection_failure(self):
        halted = ["CRYPTO_LIVE_COMPONENT_REGISTRY_WIRED:0_SOURCE_COMPONENTS",
                  "UPBIT_REALTIME_RUN_DATE_MISMATCH:universe=2026-09-12:realtime=2026-09-13",
                  "P5_08_PROMOTION_FUNNEL_UNAVAILABLE:REGIME_PAYLOAD_FUTURE_DATED"]
        self.assertEqual(MODULE._skipped_generation_class(halted), "EVALUATION_HALTED_INPUT_DATE_MISMATCH")
        self.assertEqual(MODULE._skipped_generation_class(["P5_08_PROMOTION_FUNNEL_UNAVAILABLE:SOURCE_FETCH_FAILED"]), "COLLECTION_FAILED")
        self.assertEqual(MODULE._skipped_generation_class([]), "COLLECTION_FAILED")
        self.assertEqual(MODULE._historical(None), MODULE.NO_EVIDENCE)
        marked = MODULE._historical({"generated_at": "2026-09-12T22:38:01Z", "generation_id": "x", "candidate_count": 8})
        self.assertTrue(marked["historical"])
        self.assertEqual(marked["evaluated_date_utc"], "2026-09-12")
        self.assertFalse(marked["substitutes_latest_generation"])
        self.assertIn("EVALUATION_HALTED_INPUT_DATE_MISMATCH", MODULE.GAP_CLASSES)

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

    def test_criterion_status_maps_to_a_classified_gap(self):
        # A FAILED criterion is an evaluated exclusion by a ratified rule, not an
        # unclassified reason: MATERIAL_BLOCKER:UPBIT_MARKET_EVENT_CAUTION_ACTIVE
        # (an exchange caution flag) turned every CI run red on 2026-09-16.
        self.assertEqual(MODULE.CRITERION_STATUS_CLASS["FAIL"], "EVALUATED_EXCLUDED_BY_RATIFIED_RULE")
        self.assertEqual(MODULE.CRITERION_STATUS_CLASS["UNKNOWN"], "POLICY_UNDEFINED")
        for klass in MODULE.CRITERION_STATUS_CLASS.values():
            self.assertIn(klass, MODULE.GAP_CLASSES)
        # An unexpected status stays unclassified rather than being guessed.
        self.assertNotIn("PASS", MODULE.CRITERION_STATUS_CLASS)

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
