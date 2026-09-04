#!/usr/bin/env python3
"""P1-COM-05 — combined KR+US SHADOW historical replay population and report.

SHADOW historical-backfill evidence only, never NATURAL. Offline/fixture-only:
no real KRX, Alpaca, or FRED network call is required or attempted here.

The KR and US fixtures are imported from the two market slices' own test
modules rather than re-declared, so this suite exercises exactly the inputs
those slices consider valid and cannot drift from them.

The three load-bearing guarantees under test are:

1. No episode is ever selected here. Every date is caller-supplied, and an
   episode name is an opaque label — a labelled run and an unlabelled run over
   the same dates produce identical records.
2. No cross-market regime is ever published. Each market's existing
   normalization is carried verbatim and the combined answer stays UNKNOWN /
   NOT_COMPUTABLE_NO_RATIFIED_CROSS_MARKET_RULE.
3. Failure is contained. One blocked market, one blocked date, one unavailable
   market population, or one lookahead violation never contaminates the rest.
"""

from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "combined_shadow_historical_replay.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load("combined_shadow_historical_replay_tested", SCRIPT)
KR_FIXTURE = _load(
    "combined_replay_kr_fixture", ROOT / "test" / "test_kr_historical_replay_population.py"
)
US_FIXTURE = _load(
    "combined_replay_us_fixture", ROOT / "test" / "test_us_historical_replay_population.py"
)

ANCHOR = "2026-08-28"
PREVIOUS = "2026-08-27"
# A date the KR fixtures deliberately do not cover, so KR fails closed there
# while the US fixture provider still answers.
KR_UNCOVERED = "2020-01-06"

CREDENTIALS = {
    "krx_auth_key": KR_FIXTURE.TOKEN,
    "fred_key": US_FIXTURE.FRED_KEY,
    "alpaca_key": US_FIXTURE.CREDENTIALS["alpaca_key"],
    "alpaca_secret": US_FIXTURE.CREDENTIALS["alpaca_secret"],
}

def build(dates=None, episodes=None, *, credentials=None, kr_fixtures=None,
          us_providers=None, episode_sources=None):
    """Build a combined population against both markets' offline fixtures.

    ``_now_utc`` is pinned on the *live* KR collector the combined module
    actually imports (not the KR test module's separately loaded copy), so the
    KRX packet's fetch timestamps stay deterministic across reruns.
    """
    with mock.patch.object(
        MODULE.KRP.KMS, "_now_utc", return_value=KR_FIXTURE.FIXED_NOW,
    ):
        return MODULE.build_population(
            credentials or CREDENTIALS,
            dates=dates,
            episodes=episodes,
            episode_sources=episode_sources,
            kr_opener=KR_FIXTURE.opener_for(kr_fixtures or KR_FIXTURE.base_fixtures()),
            us_getter=us_providers or US_FIXTURE.FakeProviders(),
        )


def record_for(population, date):
    return next(r for r in population["records"] if r["requested_date"] == date)


class CombinedReplayScopeTest(unittest.TestCase):
    def test_schema_mode_and_evidence_class_are_shadow_never_natural(self):
        population = build([ANCHOR])
        self.assertEqual(
            population["schema_version"], "regime_combined_shadow_historical_replay/v1",
        )
        self.assertEqual(population["mode"], "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL")
        self.assertEqual(population["wbs"], "P1-COM-05")
        self.assertEqual(population["markets"], ["KR", "US"])
        self.assertEqual(
            population["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY",
        )
        self.assertIs(population["authority"]["natural_promotion_authorized"], False)
        for record in population["records"]:
            self.assertEqual(
                record["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY",
            )
            self.assertNotEqual(record["combined_status"], "NATURAL")

    def test_authority_is_all_false_except_the_one_shadow_flag(self):
        authority = build([ANCHOR])["authority"]
        self.assertTrue(authority["historical_replay_evidence_authorized"])
        for key, value in authority.items():
            if key == "historical_replay_evidence_authorized":
                continue
            self.assertIs(value, False, key)
        for critical in (
            "natural_promotion_authorized", "episode_selection_authorized",
            "cross_market_regime_authorized", "threshold_tuning_authorized",
            "us_breadth_authorized", "us_leadership_authorized",
            "action_authorized", "order_authorized", "capital_authorized",
            "production_authorized", "trading_authorized", "real_authorized",
        ):
            self.assertIs(authority[critical], False, critical)

    def test_module_reuses_both_market_replay_modules_unmodified(self):
        # No private copy of KR/US replay or normalization logic may exist here:
        # the live modules must be called so a future edit to either slice, or
        # to the candidate rule underneath them, is automatically replayed.
        from regime import kr_historical_replay_population as live_kr
        from regime import us_historical_replay_population as live_us
        from regime import paper_regime_reference as live_prr
        self.assertIs(MODULE.KRP, live_kr)
        self.assertIs(MODULE.USP, live_us)
        self.assertIs(MODULE.KRP.PRR.build_kr, live_prr.build_kr)
        self.assertIs(MODULE.USP.PRR.build_us, live_prr.build_us)
        self.assertEqual(
            build([ANCHOR])["source_reuse"],
            [
                "regime/kr_historical_replay_population.py::build_population,validate_population",
                "regime/us_historical_replay_population.py::build_population,validate_population",
            ],
        )

    def test_embedded_market_populations_are_carried_verbatim(self):
        population = build([ANCHOR])
        for market in ("KR", "US"):
            embedded = population["market_populations"][market]
            status = population["market_population_status"][market]
            self.assertIs(status["available"], True, market)
            self.assertIsNone(status["unavailable_reason"], market)
            self.assertEqual(status["payload_sha256"], embedded["payload_sha256"], market)
            self.assertIs(embedded["authority"]["natural_promotion_authorized"], False)
        MODULE.KRP.validate_population(
            copy.deepcopy(population["market_populations"]["KR"])
        )
        MODULE.USP.validate_population(
            copy.deepcopy(population["market_populations"]["US"])
        )

    def test_us_breadth_and_leadership_stay_unknown_inside_the_combined_report(self):
        population = build([ANCHOR])
        for record in population["market_populations"]["US"]["records"]:
            five_axis = record["five_axis"]
            if five_axis is None:
                continue
            for name in ("BREADTH", "LEADERSHIP"):
                self.assertEqual(five_axis["axes"][name]["status"], "UNKNOWN", name)
                self.assertIsNone(five_axis["axes"][name]["measurement"], name)


class CombinedReplayEpisodeTest(unittest.TestCase):
    """Episodes are caller labels — never a selection made by this module."""

    def test_no_episode_is_ever_selected_automatically(self):
        population = build([ANCHOR], [{"name": "CALLER-WINDOW", "dates": [PREVIOUS]}])
        selection = population["episode_selection"]
        self.assertIs(selection["selected_by_this_module"], False)
        self.assertIs(selection["label_influences_any_record"], False)
        self.assertEqual(selection["selection_source"], "CALLER_SUPPLIED_ONLY")
        self.assertEqual(population["requested_dates"], [PREVIOUS, ANCHOR])
        self.assertEqual(population["ungrouped_dates"], [ANCHOR])
        self.assertEqual(
            [episode["name"] for episode in population["episodes"]], ["CALLER-WINDOW"],
        )
        self.assertEqual(population["episodes"][0]["dates"], [PREVIOUS])
        self.assertEqual(
            population["episodes"][0]["label_semantics"],
            "OPAQUE_CALLER_LABEL_NOT_A_REGIME_OR_OUTCOME_CLAIM",
        )

    def test_an_episode_label_never_changes_any_replayed_record(self):
        plain = build([ANCHOR, PREVIOUS])
        labelled = build(
            None, [{"name": "BULL-2026-Q3", "dates": [ANCHOR, PREVIOUS]}],
        )

        def stripped(population):
            records = copy.deepcopy(population["records"])
            for record in records:
                record.pop("episode_names")
            return MODULE.canonical_json(records)

        self.assertEqual(stripped(plain), stripped(labelled))
        self.assertEqual(record_for(plain, ANCHOR)["episode_names"], [])
        self.assertEqual(
            record_for(labelled, ANCHOR)["episode_names"], ["BULL-2026-Q3"],
        )

    def test_a_regime_sounding_label_is_never_treated_as_a_regime_claim(self):
        population = build(None, [{"name": "STRESS-EPISODE", "dates": [ANCHOR]}])
        record = record_for(population, ANCHOR)
        combined = record["combined_normalized_result"]
        self.assertEqual(combined["cross_market_regime"], "UNKNOWN")
        self.assertNotIn("STRESS-EPISODE", MODULE.canonical_json(combined))

    def test_episode_coverage_counts_only_its_own_dates(self):
        population = build(
            None,
            [
                {"name": "COVERED", "dates": [ANCHOR, PREVIOUS]},
                {"name": "UNCOVERED-KR", "dates": [KR_UNCOVERED]},
            ],
        )
        episodes = {episode["name"]: episode for episode in population["episodes"]}
        self.assertEqual(episodes["COVERED"]["coverage"]["requested_date_count"], 2)
        self.assertEqual(
            episodes["COVERED"]["coverage"]["combined_status_counts"]["BOTH_MARKETS_REPLAYED"], 2,
        )
        self.assertEqual(
            episodes["UNCOVERED-KR"]["coverage"]["combined_status_counts"]["SINGLE_MARKET_ONLY"], 1,
        )

    def test_episodes_and_loose_dates_are_deduplicated_into_one_request(self):
        population = build(
            [ANCHOR], [{"name": "OVERLAP", "dates": [ANCHOR, ANCHOR, PREVIOUS]}],
        )
        self.assertEqual(population["requested_dates"], [PREVIOUS, ANCHOR])
        self.assertEqual(len(population["records"]), 2)
        self.assertEqual(population["episodes"][0]["dates"], [PREVIOUS, ANCHOR])
        self.assertEqual(record_for(population, ANCHOR)["episode_names"], ["OVERLAP"])

    def test_a_request_with_no_caller_date_fails_closed(self):
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.build_population(CREDENTIALS, dates=[], episodes=[])

    def test_invalid_duplicate_or_empty_episodes_fail_closed(self):
        for episodes in (
            [{"name": "", "dates": [ANCHOR]}],
            [{"name": "bad name/slash", "dates": [ANCHOR]}],
            [{"name": None, "dates": [ANCHOR]}],
            [{"name": "SAME", "dates": [ANCHOR]}, {"name": "SAME", "dates": [PREVIOUS]}],
            [{"name": "EMPTY", "dates": []}],
        ):
            with self.subTest(episodes=episodes):
                with self.assertRaises(MODULE.CombinedReplayError):
                    MODULE.resolve_request([], episodes)

    def test_parse_episode_argument_reads_name_and_dates(self):
        self.assertEqual(
            MODULE.parse_episode_argument(f"AUG-2026={ANCHOR},{PREVIOUS}"),
            {"name": "AUG-2026", "dates": [ANCHOR, PREVIOUS]},
        )
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.parse_episode_argument("no-equals-sign")

    def test_episode_file_is_caller_input_and_is_hash_pinned(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "episodes.json"
            path.write_text(
                json.dumps({"episodes": [{"name": "FROM-FILE", "dates": [ANCHOR]}]}),
                encoding="utf-8",
            )
            episodes, source = MODULE.load_episode_file(path)
            self.assertEqual(episodes, [{"name": "FROM-FILE", "dates": [ANCHOR]}])
            self.assertEqual(source["sha256"], MODULE.file_sha256(path))
            self.assertEqual(source["episode_count"], 1)
            population = build(None, episodes, episode_sources=[source])
            self.assertEqual(population["episode_input_sources"], [source])

    def test_a_malformed_episode_file_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            for body in ('{"episodes": []}', '{"episodes": [{"name": "X"}]}', "not json"):
                path = Path(tmp) / "bad.json"
                path.write_text(body, encoding="utf-8")
                with self.subTest(body=body):
                    with self.assertRaises(MODULE.CombinedReplayError):
                        MODULE.load_episode_file(path)
            with self.assertRaises(MODULE.CombinedReplayError):
                MODULE.load_episode_file(Path(tmp) / "missing.json")


class CombinedNormalizationTest(unittest.TestCase):
    """Existing per-market normalization is carried; nothing is combined."""

    def test_no_cross_market_regime_is_ever_produced(self):
        population = build([ANCHOR, PREVIOUS, KR_UNCOVERED, "not-a-date"])
        for record in population["records"]:
            combined = record["combined_normalized_result"]
            self.assertEqual(combined["cross_market_regime"], "UNKNOWN")
            self.assertEqual(
                combined["cross_market_classification_status"],
                "NOT_COMPUTABLE_NO_RATIFIED_CROSS_MARKET_RULE",
            )
            self.assertIsNone(combined["cross_market_score"])
            self.assertIsNone(combined["cross_market_confidence"])
            self.assertEqual(combined["runtime_regime"], "UNKNOWN")
        self.assertEqual(
            population["combined_summary"]["cross_market_classification_status"],
            "NOT_COMPUTABLE_NO_RATIFIED_CROSS_MARKET_RULE",
        )

    def test_each_market_candidate_regime_is_carried_verbatim(self):
        population = build([ANCHOR])
        record = record_for(population, ANCHOR)
        kr_record = next(
            r for r in population["market_populations"]["KR"]["records"]
            if r["requested_date"] == ANCHOR
        )
        expected = kr_record["candidate_normalized_result"]["paper_reference"]["candidate_regime"]
        self.assertIn(expected, {"RISK_ON", "RISK_OFF", "NEUTRAL", "STRESS"})
        self.assertEqual(record["markets"]["KR"]["candidate_regime"], expected)
        self.assertEqual(
            record["combined_normalized_result"]["per_market_candidate_regime"]["KR"], expected,
        )
        self.assertEqual(record["markets"]["KR"]["axis_coverage"]["ratio"], "5/5")

    def test_us_partial_coverage_still_never_classifies(self):
        record = record_for(build([ANCHOR]), ANCHOR)
        self.assertEqual(record["markets"]["US"]["candidate_regime"], "UNKNOWN")
        self.assertEqual(
            record["markets"]["US"]["candidate_classification_status"],
            "NOT_COMPUTABLE_PARTIAL_AXIS_COVERAGE",
        )
        self.assertEqual(record["markets"]["US"]["axis_coverage"]["ratio"], "3/5")

    def test_runtime_regime_stays_unknown_everywhere(self):
        population = build([ANCHOR, KR_UNCOVERED])
        for record in population["records"]:
            self.assertEqual(record["combined_normalized_result"]["runtime_regime"], "UNKNOWN")
            for market in ("KR", "US"):
                self.assertEqual(record["markets"][market]["runtime_regime"], "UNKNOWN")


class CombinedJoinContainmentTest(unittest.TestCase):
    def test_both_markets_replayed_on_a_fully_covered_date(self):
        record = record_for(build([ANCHOR]), ANCHOR)
        self.assertEqual(record["combined_status"], "BOTH_MARKETS_REPLAYED")
        self.assertEqual(record["markets"]["KR"]["market_status"], "OBSERVED")
        self.assertEqual(record["markets"]["KR"]["outcome"], "OBSERVED")
        self.assertEqual(record["markets"]["US"]["market_status"], "FREE_AXES_OBSERVED")
        self.assertEqual(record["markets"]["US"]["outcome"], "OBSERVED")
        self.assertEqual(record["markets_by_outcome"]["OBSERVED"], ["KR", "US"])
        self.assertEqual(record["markets"]["KR"]["effective_date"], ANCHOR)

    def test_one_blocked_market_leaves_the_other_market_reported(self):
        record = record_for(build([KR_UNCOVERED]), KR_UNCOVERED)
        self.assertEqual(record["combined_status"], "SINGLE_MARKET_ONLY")
        self.assertEqual(record["markets"]["KR"]["outcome"], "BLOCKED")
        self.assertIsNotNone(record["markets"]["KR"]["failure_reason"])
        self.assertIsNone(record["markets"]["KR"]["candidate_regime"])
        self.assertEqual(record["markets"]["US"]["outcome"], "OBSERVED")
        self.assertEqual(record["markets_by_outcome"]["BLOCKED"], ["KR"])

    def test_a_us_credential_gap_degrades_to_partial_not_blocked(self):
        population = build(
            [ANCHOR],
            credentials={
                "krx_auth_key": KR_FIXTURE.TOKEN, "fred_key": US_FIXTURE.FRED_KEY,
                "alpaca_key": "", "alpaca_secret": "",
            },
        )
        record = record_for(population, ANCHOR)
        self.assertEqual(record["markets"]["US"]["market_status"], "FREE_AXES_PARTIAL")
        self.assertEqual(record["markets"]["US"]["outcome"], "PARTIAL")
        self.assertEqual(record["combined_status"], "BOTH_MARKETS_REPLAYED")
        self.assertEqual(record["markets_by_outcome"]["PARTIAL"], ["US"])

    def test_a_malformed_date_blocks_both_markets_without_touching_others(self):
        population = build([ANCHOR, "not-a-date", "2026-02-30"])
        for bad in ("not-a-date", "2026-02-30"):
            record = record_for(population, bad)
            self.assertEqual(record["combined_status"], "NOT_COMPUTABLE_NO_MARKET_REPLAYED")
            self.assertEqual(record["markets_by_outcome"]["BLOCKED"], ["KR", "US"])
            self.assertIsNone(record["combined_normalized_result"]["per_market_candidate_regime"]["KR"])
        self.assertEqual(
            record_for(population, ANCHOR)["combined_status"], "BOTH_MARKETS_REPLAYED",
        )

    def test_an_unavailable_market_population_degrades_instead_of_aborting(self):
        with mock.patch.object(
            MODULE.KRP, "build_population",
            side_effect=MODULE.KRP.ReplayPopulationError("CONTRACT_MISSING"),
        ):
            population = build([ANCHOR, PREVIOUS])
        status = population["market_population_status"]["KR"]
        self.assertIs(status["available"], False)
        self.assertIn("MARKET_POPULATION_UNAVAILABLE:KR", status["unavailable_reason"])
        self.assertIsNone(population["market_populations"]["KR"])
        self.assertIs(population["market_population_status"]["US"]["available"], True)
        for record in population["records"]:
            self.assertEqual(record["combined_status"], "SINGLE_MARKET_ONLY")
            self.assertEqual(record["markets"]["KR"]["outcome"], "BLOCKED")
            self.assertIn(
                "MARKET_POPULATION_UNAVAILABLE:KR", record["markets"]["KR"]["failure_reason"],
            )
            self.assertEqual(record["markets"]["US"]["outcome"], "OBSERVED")

    def test_an_unrecognized_market_population_shape_is_contained(self):
        with mock.patch.object(
            MODULE.USP, "build_population", side_effect=RuntimeError("RAW-DETAIL-MUST-NOT-LEAK"),
        ):
            population = build([ANCHOR])
        reason = population["market_population_status"]["US"]["unavailable_reason"]
        # Only the exception *type* is recorded — never its message.
        self.assertEqual(reason, "MARKET_POPULATION_UNAVAILABLE:US:UNSUPPORTED_SHAPE_RuntimeError")
        self.assertNotIn("RAW-DETAIL-MUST-NOT-LEAK", MODULE.canonical_json(population))

    def test_a_subpopulation_its_own_validator_rejects_is_never_published(self):
        original = MODULE.USP.build_population

        def tampered(credentials, dates, *, getter=None):
            population = original(credentials, dates, getter=getter)
            population["authority"]["order_authorized"] = True
            return population

        with mock.patch.object(MODULE.USP, "build_population", side_effect=tampered):
            population = build([ANCHOR])
        self.assertIsNone(population["market_populations"]["US"])
        self.assertIn(
            "MARKET_POPULATION_UNAVAILABLE:US",
            population["market_population_status"]["US"]["unavailable_reason"],
        )

    def test_a_missing_market_record_for_a_requested_date_is_contained(self):
        original = MODULE.KRP.build_population

        def short(auth_key, requested_dates, *, opener=None):
            return original(auth_key, [ANCHOR], opener=opener)

        with mock.patch.object(MODULE.KRP, "build_population", side_effect=short):
            population = build([ANCHOR, PREVIOUS])
        self.assertEqual(
            record_for(population, PREVIOUS)["markets"]["KR"]["failure_reason"],
            "MARKET_RECORD_MISSING_FROM_POPULATION",
        )
        self.assertEqual(record_for(population, ANCHOR)["markets"]["KR"]["outcome"], "OBSERVED")
        # Containment and certification are different jobs. A market module that
        # returns fewer records than it was asked for is violating its own
        # contract, so the report still says honestly what each date showed, but
        # the population is not certifiable: the embedded evidence does not
        # cover the replay it is attached to.
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(copy.deepcopy(population))
        self.assertIn("EMBEDDED_POPULATION_DATE_SET_MISMATCH", str(caught.exception))

    def test_an_unrecognized_market_record_status_fails_that_view_closed(self):
        view = MODULE.market_view(
            "KR",
            {"status": "OBSERVED_BUT_NEW", "no_lookahead_attestation": {}},
            ANCHOR,
        )
        self.assertEqual(view["outcome"], "BLOCKED")
        self.assertIn("UNRECOGNIZED_MARKET_RECORD_STATUS", view["failure_reason"])
        self.assertIsNone(view["candidate_regime"])

    def test_per_market_outcome_counts_match_the_records(self):
        population = build([ANCHOR, KR_UNCOVERED, "not-a-date"])
        counts = population["combined_summary"]["per_market_outcome_counts"]
        self.assertEqual(counts["KR"], {"OBSERVED": 1, "PARTIAL": 0, "BLOCKED": 2})
        self.assertEqual(counts["US"], {"OBSERVED": 2, "PARTIAL": 0, "BLOCKED": 1})
        self.assertEqual(
            population["combined_summary"]["combined_status_counts"],
            {
                "BOTH_MARKETS_REPLAYED": 1,
                "SINGLE_MARKET_ONLY": 1,
                "NOT_COMPUTABLE_NO_MARKET_REPLAYED": 1,
            },
        )


class CombinedPointInTimeTest(unittest.TestCase):
    def test_no_consumed_source_date_is_ever_after_the_requested_date(self):
        population = build([ANCHOR, PREVIOUS, "2026-08-29"])
        for record in population["records"]:
            attestation = record["no_lookahead_attestation"]
            self.assertIs(attestation["any_source_date_after_requested_date"], False)
            self.assertIs(attestation["other_requested_dates_consulted"], False)
            self.assertIs(attestation["episode_label_used_in_evaluation"], False)
            self.assertEqual(attestation["markets_failed_closed_for_lookahead"], [])
            for market in ("KR", "US"):
                for date in attestation["per_market_source_dates"][market]:
                    self.assertLessEqual(date, record["requested_date"], market)

    def test_a_market_that_consumed_a_later_date_is_failed_closed_for_that_date(self):
        view = MODULE.market_view(
            "KR",
            {
                "status": "OBSERVED",
                "effective_trading_date": "2026-09-04",
                "five_axis": {"coverage": {"ratio": "5/5"}, "axes": {}},
                "candidate_normalized_result": {
                    "paper_reference": {"candidate_regime": "RISK_ON"},
                    "classification_status": "PAPER_REFERENCE_CLASSIFIED",
                    "runtime_regime": "UNKNOWN",
                },
                "failure_reason": None,
                "no_lookahead_attestation": {"session_dates_used": ["20260903", "20260904"]},
            },
            ANCHOR,
        )
        self.assertEqual(view["outcome"], "BLOCKED")
        self.assertEqual(view["failure_reason"], "COMBINED_LOOKAHEAD_VIOLATION")
        self.assertIs(view["lookahead_violation"], True)
        self.assertIsNone(view["candidate_regime"])
        self.assertEqual(view["source_dates_consulted"], [])

        record = MODULE.combined_record(
            ANCHOR, {"KR": view, "US": MODULE._unavailable_view("US", "x")}, [],
        )
        self.assertEqual(record["combined_status"], "NOT_COMPUTABLE_NO_MARKET_REPLAYED")
        self.assertEqual(
            record["no_lookahead_attestation"]["markets_failed_closed_for_lookahead"], ["KR"],
        )

    def test_krx_yyyymmdd_and_iso_dates_normalize_to_one_comparable_form(self):
        self.assertEqual(MODULE._iso_date("20260828"), ANCHOR)
        self.assertEqual(MODULE._iso_date(ANCHOR), ANCHOR)
        self.assertIsNone(MODULE._iso_date("not-a-date"))
        self.assertIsNone(MODULE._iso_date(None))
        self.assertEqual(
            sorted(MODULE._date_like_strings(
                {"a": ["20260827", "x"], "b": {"c": ANCHOR}, "d": 3}
            )),
            [PREVIOUS, ANCHOR],
        )

    def test_date_isolation_one_records_outcome_ignores_batch_membership(self):
        solo = record_for(build([ANCHOR]), ANCHOR)
        batched = record_for(
            build([ANCHOR, KR_UNCOVERED, "not-a-date"]), ANCHOR,
        )
        self.assertEqual(solo, batched)

    def test_pit_replay_facts_are_declared(self):
        pit = build([ANCHOR])["pit_replay"]
        for key in (
            "each_date_replayed_independently", "each_market_replayed_independently",
            "lookahead_rechecked_at_join",
        ):
            self.assertIs(pit[key], True, key)
        for key in (
            "future_dates_used_in_any_date_evaluation",
            "retained_sources_mutated_by_this_module",
            "candidate_rule_modified_by_this_module",
            "market_observations_recomputed_by_this_module",
            "cross_market_rule_invented_by_this_module",
        ):
            self.assertIs(pit[key], False, key)


class CombinedDeterminismTest(unittest.TestCase):
    def test_shuffled_input_produces_deterministic_ordering(self):
        forward = build(
            [ANCHOR, PREVIOUS], [{"name": "B", "dates": [ANCHOR]}, {"name": "A", "dates": [PREVIOUS]}],
        )
        shuffled = build(
            [PREVIOUS, ANCHOR], [{"name": "A", "dates": [PREVIOUS]}, {"name": "B", "dates": [ANCHOR]}],
        )
        self.assertEqual(MODULE.canonical_json(forward), MODULE.canonical_json(shuffled))
        self.assertEqual(
            [r["requested_date"] for r in forward["records"]],
            sorted(r["requested_date"] for r in forward["records"]),
        )
        self.assertEqual([e["name"] for e in forward["episodes"]], ["A", "B"])

    def test_deterministic_rerun_is_byte_identical(self):
        self.assertEqual(
            MODULE.canonical_json(build([ANCHOR, PREVIOUS])),
            MODULE.canonical_json(build([ANCHOR, PREVIOUS])),
        )

    def test_duplicate_requested_dates_collapse_to_one_record(self):
        population = build([ANCHOR, ANCHOR])
        self.assertEqual(population["requested_dates"], [ANCHOR])
        self.assertEqual(len(population["records"]), 1)


class CombinedValidationTest(unittest.TestCase):
    def test_validate_population_accepts_its_own_output(self):
        population = build([ANCHOR], [{"name": "E1", "dates": [ANCHOR]}])
        self.assertEqual(MODULE.validate_population(copy.deepcopy(population)), population)

    def _resigned(self, population):
        population["payload_sha256"] = MODULE.payload_sha256(
            {k: v for k, v in population.items() if k != "payload_sha256"}
        )
        return population

    def test_validate_population_rejects_a_tampered_payload(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["combined_status"] = "BOTH_MARKETS_REPLAYED_FAKE"
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(tampered)

    def test_validate_population_rejects_a_flipped_authority_flag(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["authority"]["order_authorized"] = True
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_a_classified_cross_market_regime(self):
        for field, value in (
            ("cross_market_regime", "RISK_ON"),
            ("cross_market_classification_status", "PAPER_REFERENCE_CLASSIFIED"),
            ("cross_market_score", 4),
            ("cross_market_confidence", "0.8"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered["records"][0]["combined_normalized_result"][field] = value
                with self.assertRaises(MODULE.CombinedReplayError):
                    MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_a_classified_us_market_view(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["markets"]["US"]["candidate_regime"] = "RISK_ON"
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_a_lookahead_source_date(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["markets"]["KR"]["source_dates_consulted"].append("2026-09-04")
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_an_episode_date_it_never_requested(self):
        tampered = copy.deepcopy(build([ANCHOR], [{"name": "E1", "dates": [ANCHOR]}]))
        tampered["episodes"][0]["dates"] = [ANCHOR, "2020-03-16"]
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_a_relabelled_episode_selection_claim(self):
        for path, value in (
            (("episode_selection", "selected_by_this_module"), True),
            (("episode_selection", "label_influences_any_record"), True),
            (("episode_selection", "selection_source"), "AUTO_SELECTED"),
        ):
            with self.subTest(path=path):
                tampered = copy.deepcopy(build([ANCHOR]))
                tampered[path[0]][path[1]] = value
                with self.assertRaises(MODULE.CombinedReplayError):
                    MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_an_embedded_population_its_owner_rejects(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["market_populations"]["US"]["records"][0]["five_axis"]["axes"]["BREADTH"] = {
            "status": "OBSERVED", "reason": None, "measurement": {"advance_fraction": "0.9"},
        }
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_an_inconsistent_market_availability_claim(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["market_populations"]["KR"] = None
        with self.assertRaises(MODULE.CombinedReplayError):
            MODULE.validate_population(self._resigned(tampered))

    def test_validate_population_rejects_omitted_records(self):
        # Adversarial: a re-signed payload is a valid hash over whatever it
        # contains, so dropping the records must fail rather than satisfy every
        # per-record guarantee vacuously against an empty list.
        for records in ([], None):
            with self.subTest(records=records):
                tampered = copy.deepcopy(build([ANCHOR, PREVIOUS]))
                tampered["records"] = records
                with self.assertRaises(MODULE.CombinedReplayError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn("POPULATION_RECORDS_NOT_BIJECTIVE", str(caught.exception))

    def test_validate_population_rejects_an_omitted_authority_boundary(self):
        for mutate in (
            lambda population: population["authority"].pop("cross_market_regime_authorized"),
            lambda population: population.__setitem__("authority", {}),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered)
                with self.assertRaises(MODULE.CombinedReplayError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "POPULATION_AUTHORITY_SCHEMA_INVALID", str(caught.exception),
                )

    def test_validate_population_recomputes_every_published_count(self):
        for code, mutate in (
            (
                "COMBINED_SUMMARY_INCONSISTENT",
                lambda population: population["combined_summary"][
                    "combined_status_counts"
                ].__setitem__("SINGLE_MARKET_ONLY", 9),
            ),
            (
                "COMBINED_SUMMARY_INCONSISTENT",
                lambda population: population["combined_summary"][
                    "per_market_outcome_counts"
                ]["KR"].__setitem__("BLOCKED", 5),
            ),
            (
                "RECORD_OUTCOME_GROUPING_INCONSISTENT",
                lambda population: population["records"][0][
                    "markets_by_outcome"
                ].__setitem__("BLOCKED", ["KR", "US"]),
            ),
            (
                "RECORD_COMBINED_STATUS_INCONSISTENT",
                lambda population: population["records"][0].__setitem__(
                    "combined_status", "SINGLE_MARKET_ONLY"
                ),
            ),
        ):
            with self.subTest(code=code):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered)
                with self.assertRaises(MODULE.CombinedReplayError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_population_rejects_an_asserted_episode_coverage(self):
        tampered = copy.deepcopy(build([ANCHOR], [{"name": "E1", "dates": [ANCHOR]}]))
        tampered["episodes"][0]["coverage"]["combined_status_counts"][
            "BOTH_MARKETS_REPLAYED"
        ] = 4
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("EPISODE_COVERAGE_INCONSISTENT", str(caught.exception))

    def test_validate_population_rejects_a_mispinned_embedded_population(self):
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["market_population_status"]["US"]["payload_sha256"] = "0" * 64
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("MARKET_POPULATION_STATUS_INCONSISTENT", str(caught.exception))

    def _repinned(self, population, market, module):
        """Re-sign a tampered embedded population and re-pin its identity.

        Adversarial helper: a forger controls every hash in the file, so a test
        that left a stale embedded sha would only prove the hash check works.
        Re-signing forces the *content* guarantees to do the rejecting.
        """
        embedded = population["market_populations"][market]
        embedded["payload_sha256"] = module.payload_sha256(
            {k: v for k, v in embedded.items() if k != "payload_sha256"}
        )
        population["market_population_status"][market]["payload_sha256"] = embedded[
            "payload_sha256"
        ]
        return self._resigned(population)

    def test_validate_population_rejects_a_forged_embedded_kr_normalization(self):
        # The KR record keeps its genuine five-axis evidence; only the state it
        # supposedly supports is changed. Every hash is recomputed, so this can
        # only be caught by re-deriving the normalization from that evidence.
        tampered = copy.deepcopy(build([ANCHOR]))
        record = tampered["market_populations"]["KR"]["records"][0]
        record["candidate_normalized_result"]["paper_reference"][
            "candidate_regime"
        ] = "FORGED_STATE"
        record["candidate_normalized_result"]["axes"][0]["direction"] = "FORGED_DIRECTION"
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(self._repinned(tampered, "KR", MODULE.KRP))
        self.assertIn("EMBEDDED_POPULATION_INVALID:KR", str(caught.exception))
        self.assertIn(
            "OBSERVED_RECORD_CANDIDATE_NOT_DERIVED_FROM_ITS_EVIDENCE",
            str(caught.exception),
        )

    def test_validate_population_rejects_a_forged_embedded_us_axis_direction(self):
        # The US candidate regime stays UNKNOWN, so the never-classify rules are
        # all satisfied — only the axis direction underneath it is forged, and
        # that is what every downstream transition and stress fact is built from.
        tampered = copy.deepcopy(build([ANCHOR]))
        record = tampered["market_populations"]["US"]["records"][0]
        rows = record["candidate_normalized_result"]["axes"]
        self.assertTrue(rows)
        rows[0]["direction"] = "FORGED_DIRECTION"
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(self._repinned(tampered, "US", MODULE.USP))
        self.assertIn("EMBEDDED_POPULATION_INVALID:US", str(caught.exception))
        self.assertIn(
            "RECORD_CANDIDATE_NOT_DERIVED_FROM_ITS_EVIDENCE", str(caught.exception),
        )

    def test_validate_population_rejects_embedded_records_stripped_of_provenance(self):
        # A re-signed embedded population whose official-source hashes were
        # deleted must be refused by the owning market validator at the join.
        # Without that, the join would publish an observation whose five-axis
        # evidence and normalization look intact while nothing names the KRX or
        # provider response it was measured from.
        for market, module in (("KR", MODULE.KRP), ("US", MODULE.USP)):
            with self.subTest(market=market):
                tampered = copy.deepcopy(build([ANCHOR]))
                record = tampered["market_populations"][market]["records"][0]
                record["source_hashes"] = None
                with self.assertRaises(MODULE.CombinedReplayError) as caught:
                    MODULE.validate_population(self._repinned(tampered, market, module))
                self.assertIn(
                    f"EMBEDDED_POPULATION_INVALID:{market}", str(caught.exception),
                )
                self.assertIn(
                    "OBSERVED_RECORD_MUST_CARRY_ITS_SOURCE_HASHES", str(caught.exception),
                )

    def test_validate_population_rejects_an_embedded_population_for_another_date(self):
        # An internally valid market population is still not *this* population's
        # evidence: one date's genuine KR replay must not be able to stand in
        # for another date's.
        tampered = copy.deepcopy(build([ANCHOR]))
        with mock.patch.object(
            MODULE.KRP.KMS, "_now_utc", return_value=KR_FIXTURE.FIXED_NOW,
        ):
            other = MODULE.KRP.build_population(
                KR_FIXTURE.TOKEN,
                [PREVIOUS],
                opener=KR_FIXTURE.opener_for(KR_FIXTURE.base_fixtures()),
            )
        MODULE.KRP.validate_population(copy.deepcopy(other))
        tampered["market_populations"]["KR"] = other
        tampered["market_population_status"]["KR"]["payload_sha256"] = other[
            "payload_sha256"
        ]
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn("EMBEDDED_POPULATION_DATE_SET_MISMATCH", str(caught.exception))

    def test_validate_population_rejects_a_market_view_its_record_does_not_support(self):
        for mutate in (
            lambda record: record["markets"]["KR"].__setitem__(
                "effective_date", "2026-08-20",
            ),
            lambda record: record["markets"]["KR"].__setitem__("axis_coverage", None),
            lambda record: record["markets"]["KR"].__setitem__(
                "source_dates_consulted", [],
            ),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(build([ANCHOR]))
                mutate(tampered["records"][0])
                with self.assertRaises(MODULE.CombinedReplayError) as caught:
                    MODULE.validate_population(self._resigned(tampered))
                self.assertIn(
                    "RECORD_NOT_DERIVED_FROM_ITS_EMBEDDED_MARKET_RECORDS",
                    str(caught.exception),
                )

    def test_validate_population_rejects_a_forged_per_market_candidate_regime(self):
        # Forging the view *and* the combined view together defeats every
        # self-consistency check; only the embedded record can refuse it.
        tampered = copy.deepcopy(build([ANCHOR]))
        tampered["records"][0]["markets"]["KR"]["candidate_regime"] = "FORGED_STATE"
        tampered["records"][0]["combined_normalized_result"][
            "per_market_candidate_regime"
        ]["KR"] = "FORGED_STATE"
        with self.assertRaises(MODULE.CombinedReplayError) as caught:
            MODULE.validate_population(self._resigned(tampered))
        self.assertIn(
            "RECORD_NOT_DERIVED_FROM_ITS_EMBEDDED_MARKET_RECORDS", str(caught.exception),
        )


class CombinedOutputBoundaryTest(unittest.TestCase):
    def test_write_population_refuses_any_path_inside_the_checkout(self):
        population = build([ANCHOR])
        for forbidden in (
            ROOT / "data" / "observations" / "combined_shadow_replay" / "sneak.json",
            ROOT / "evidence" / "regime" / "combined_shadow_replay_sneak.json",
            ROOT / "combined_shadow_replay_sneak.json",
        ):
            with self.subTest(path=str(forbidden)):
                with self.assertRaises(MODULE.CombinedReplayError):
                    MODULE.write_population(population, forbidden, root=ROOT)
                self.assertFalse(forbidden.exists())

    def test_write_population_accepts_an_external_path(self):
        population = build([ANCHOR])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "outside_checkout" / "combined.json"
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

    def test_no_retained_or_live_source_mutation_in_either_market(self):
        korea_dir = ROOT / "data" / "observations" / "korea_market_signals"
        us_dir = ROOT / "evidence" / "free_market_data" / "derived"
        latest = [
            ROOT / "data" / "latest_korea_market_signals.json",
            ROOT / "data" / "latest_free_market_data.json",
        ]

        def snapshot():
            found = {}
            for directory, filename in ((korea_dir, "packet.json"), (us_dir, "manifest.json")):
                if directory.is_dir():
                    for entry in sorted(directory.iterdir()):
                        source = entry / filename
                        if source.is_file():
                            found[str(source)] = MODULE.file_sha256(source)
            return found, [
                MODULE.file_sha256(path) if path.is_file() else None for path in latest
            ]

        before = snapshot()
        build([ANCHOR, PREVIOUS, KR_UNCOVERED, "not-a-date"])
        self.assertEqual(before, snapshot())

    def test_no_credential_ever_reaches_the_population(self):
        with mock.patch.object(
            MODULE.KRP, "build_population",
            side_effect=MODULE.KRP.ReplayPopulationError(f"HTTP_ERROR:{KR_FIXTURE.TOKEN}"),
        ):
            population = build([ANCHOR])
        serialized = MODULE.canonical_json(population)
        for secret in CREDENTIALS.values():
            self.assertNotIn(secret, serialized)
        self.assertIn(
            "[REDACTED]", population["market_population_status"]["KR"]["unavailable_reason"],
        )

    def test_module_never_reads_the_account_or_trading_alpaca_credential(self):
        code = "\n".join(
            line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        )
        self.assertNotIn("ALPACA_API_KEY", code)
        self.assertNotIn("ALPACA_API_SECRET", code)

    def test_credentials_from_env_reads_only_market_data_names(self):
        environment = {
            "KRX_API_KEY": " krx ",
            "FRED_API_KEY": "fred",
            "ALPACA_MARKET_DATA_API_KEY": "data-key",
            "ALPACA_MARKET_DATA_API_SECRET": "data-secret",
            "ALPACA_API_KEY": "TRADING-KEY-MUST-NOT-BE-USED",
            "ALPACA_API_SECRET": "TRADING-SECRET-MUST-NOT-BE-USED",
        }
        with mock.patch.dict(MODULE.os.environ, environment, clear=True):
            credentials = MODULE._credentials_from_env()
        self.assertEqual(credentials, {
            "krx_auth_key": "krx", "fred_key": "fred",
            "alpaca_key": "data-key", "alpaca_secret": "data-secret",
        })


if __name__ == "__main__":
    unittest.main()
