#!/usr/bin/env python3
"""P1-COM-05 — deterministic replay evidence over the combined SHADOW replay.

SHADOW historical-backfill evidence only, never NATURAL. Offline/fixture-only:
no real KRX, Alpaca, or FRED network call is required or attempted here.

The combined population under summary is built by the combined slice's own test
module rather than re-declared, so this suite exercises exactly the population
that slice considers valid and cannot drift from it.

The load-bearing guarantees under test are:

1. **No policy or threshold conclusion.** Every count stays a count. The report
   carries a null conclusion, applies no hysteresis, proposes no threshold, and
   the validator rejects a recommendation smuggled in under any key name.
2. **UNKNOWN semantics survive.** "Excluded by ratification scope",
   "attempted and not computable", and "the whole date was blocked" stay three
   distinct buckets, none of them ever reported as an observed NEUTRAL state,
   and an UNKNOWN pair is never counted as a market state change.
3. **Facts are total and deterministic.** Every requested date appears in every
   sequence (so a run can never bridge a blocked date), the buckets add up, and
   the report is a pure function of the population it summarizes.
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
SCRIPT = ROOT / "regime" / "deterministic_replay_evidence.py"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = _load("deterministic_replay_evidence_tested", SCRIPT)
COMBINED = _load(
    "deterministic_replay_evidence_combined_fixture",
    ROOT / "test" / "test_combined_shadow_historical_replay.py",
)
US_FIXTURE = COMBINED.US_FIXTURE
KR_FIXTURE = COMBINED.KR_FIXTURE

ANCHOR = COMBINED.ANCHOR              # 2026-08-28, covered by both fixtures
PREVIOUS = COMBINED.PREVIOUS          # 2026-08-27, covered by both fixtures
KR_UNCOVERED = COMBINED.KR_UNCOVERED  # 2020-01-06, US only — KR fails closed
STRESS_VIX = "35.0"                   # >= 30 => the existing rule emits STRESS
CALM_VIX = "17.5"


class VixByDate(US_FIXTURE.FakeProviders):
    """The US fixture provider, with VIXCLS varying by the replayed date.

    The replay pins ``observation_end`` to the requested date, so keying on it
    reproduces a genuinely different point-in-time observation per date — which
    is what makes an axis-direction transition observable end to end.
    """

    def __init__(self, vix_by_date, *, default_vix=CALM_VIX, **kwargs):
        super().__init__(vix=default_vix, **kwargs)
        self.vix_by_date = dict(vix_by_date)
        self.default_vix = default_vix

    def _fred_observations(self, query):
        self.vix = self.vix_by_date.get(query["observation_end"][0], self.default_vix)
        return super()._fred_observations(query)


def population(dates=None, episodes=None, **kwargs):
    return COMBINED.build(dates, episodes, **kwargs)


def evidence(dates=None, episodes=None, **kwargs):
    return MODULE.build_evidence(population(dates, episodes, **kwargs))


def resigned(report):
    report["payload_sha256"] = MODULE.payload_sha256(
        {key: value for key, value in report.items() if key != "payload_sha256"}
    )
    return report


def observation(report, market, date):
    return report["per_date_observations"][market][date]


def axis_sequence(report, market, axis):
    return report["transition_facts"]["markets"][market]["axis_direction"][axis]


class EvidenceScopeTest(unittest.TestCase):
    def test_schema_mode_and_evidence_class_are_shadow_never_natural(self):
        report = evidence([ANCHOR])
        self.assertEqual(
            report["schema_version"], "regime_deterministic_replay_evidence/v1",
        )
        self.assertEqual(report["mode"], "SHADOW_REPLAY_EVIDENCE_ONLY_NOT_POLICY")
        self.assertEqual(report["wbs"], "P1-COM-05")
        self.assertEqual(
            report["evidence_class"], "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY",
        )
        self.assertEqual(report["markets"], ["KR", "US"])
        self.assertEqual(
            report["axes"], ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
        )
        self.assertIs(report["authority"]["natural_promotion_authorized"], False)

    def test_authority_is_all_false_except_the_one_summary_flag(self):
        authority = evidence([ANCHOR])["authority"]
        self.assertTrue(authority["replay_evidence_summary_authorized"])
        for key, value in authority.items():
            if key == "replay_evidence_summary_authorized":
                continue
            self.assertIs(value, False, key)
        for critical in (
            "policy_conclusion_authorized", "threshold_ratification_authorized",
            "hysteresis_authorized", "stress_override_ratification_authorized",
            "episode_selection_authorized", "natural_promotion_authorized",
            "us_breadth_authorized", "us_leadership_authorized",
            "pit_replay_acceptance_authorized", "runtime_regime_wiring_authorized",
            "action_authorized", "order_authorized", "capital_authorized",
            "production_authorized", "trading_authorized", "real_authorized",
        ):
            self.assertIs(authority[critical], False, critical)

    def test_module_reuses_the_live_replay_and_normalization_modules(self):
        # No private copy of the join, the market replays, or the candidate rule
        # may exist here: the live modules must be used so a future edit to any
        # of them is automatically reflected in this summary.
        from regime import combined_shadow_historical_replay as live_combined
        from regime import paper_regime_reference as live_prr
        self.assertIs(MODULE.CSR, live_combined)
        self.assertIs(MODULE.PRR, live_prr)
        self.assertEqual(MODULE.AXES, tuple(live_prr.AXES))
        self.assertEqual(MODULE.MARKETS, live_combined.MARKETS)
        self.assertEqual(MODULE.EVIDENCE_CLASS, live_combined.EVIDENCE_CLASS)

    def test_source_population_is_pinned_and_revalidated_by_its_owner(self):
        source = population([ANCHOR])
        report = MODULE.build_evidence(source)
        pinned = report["source_population"]
        self.assertEqual(pinned["payload_sha256"], source["payload_sha256"])
        self.assertEqual(pinned["schema_version"], source["schema_version"])
        self.assertEqual(pinned["mode"], "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL")
        self.assertEqual(
            pinned["source_module"], "regime/combined_shadow_historical_replay.py",
        )
        self.assertIs(pinned["revalidated_by_its_own_validator"], True)

    def test_a_population_its_own_validator_rejects_is_never_summarized(self):
        tampered = copy.deepcopy(population([ANCHOR]))
        tampered["records"][0]["combined_normalized_result"]["cross_market_regime"] = "RISK_ON"
        tampered["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in tampered.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(tampered)
        self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))

    def test_build_evidence_never_mutates_the_caller_population(self):
        source = population([ANCHOR, PREVIOUS])
        before = MODULE.canonical_json(source)
        MODULE.build_evidence(source)
        self.assertEqual(before, MODULE.canonical_json(source))


class NoPolicyConclusionTest(unittest.TestCase):
    """The load-bearing guarantee: facts only, never a conclusion."""

    def test_policy_conclusion_is_withheld_in_every_field(self):
        conclusion = evidence([ANCHOR, PREVIOUS])["policy_conclusion"]
        self.assertIsNone(conclusion["conclusion"])
        self.assertEqual(
            conclusion["conclusion_status"], "WITHHELD_NO_POLICY_OR_THRESHOLD_AUTHORITY",
        )
        for key in (
            "threshold_proposed", "threshold_tuned", "stress_threshold_proposed",
            "hysteresis_parameter_proposed", "minimum_coverage_proposed",
            "replay_acceptance_asserted", "candidate_policy_ratified",
            "market_regime_asserted",
        ):
            self.assertIs(conclusion[key], False, key)

    def test_no_hysteresis_is_applied_and_no_stress_threshold_is_introduced(self):
        report = evidence([ANCHOR])
        self.assertIs(report["hysteresis_facts"]["hysteresis_applied"], False)
        self.assertIsNone(report["hysteresis_facts"]["hysteresis_parameter_source"])
        self.assertIs(
            report["stress_detection_facts"]["stress_threshold_introduced_by_this_module"],
            False,
        )
        self.assertIs(
            report["pit_and_audit_separation"]["threshold_introduced_by_this_module"],
            False,
        )
        self.assertIs(
            report["pit_and_audit_separation"]["hysteresis_applied_by_this_module"],
            False,
        )

    def test_validator_rejects_a_recommendation_smuggled_under_any_key(self):
        for path, key in (
            (("hysteresis_facts",), "recommended_dwell_days"),
            (("stress_detection_facts",), "proposed_vix_threshold"),
            (("coverage_facts",), "verdict"),
            (("transition_facts",), "suggested_confirmation_count"),
        ):
            with self.subTest(key=key):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                tampered[path[0]][key] = 3
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn("POLICY_CONCLUSION_SMUGGLED", str(caught.exception))

    def test_validator_rejects_a_stated_conclusion_or_flipped_claim(self):
        for path, value in (
            (("policy_conclusion", "conclusion"), "HYSTERESIS_REQUIRED"),
            (("policy_conclusion", "conclusion_status"), "CONCLUDED"),
            (("policy_conclusion", "threshold_proposed"), True),
            (("policy_conclusion", "replay_acceptance_asserted"), True),
            (("hysteresis_facts", "hysteresis_applied"), True),
            (("hysteresis_facts", "hysteresis_parameter_source"), "config/x.json"),
            (
                ("stress_detection_facts", "stress_threshold_introduced_by_this_module"),
                True,
            ),
        ):
            with self.subTest(path=path):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                tampered[path[0]][path[1]] = value
                with self.assertRaises(MODULE.ReplayEvidenceError):
                    MODULE.validate_evidence(resigned(tampered))

    def test_no_axis_threshold_value_is_reproduced_anywhere_in_the_report(self):
        # The report counts directions the existing rule emitted; it must never
        # restate the comparison values that produced them, which would fork the
        # threshold into a second place it could drift from.
        serialized = MODULE.canonical_json(evidence([ANCHOR, PREVIOUS]))
        for boundary in ("0.666667", "0.333333", "0.55", "0.45", "1.5", "2.5", "3.5"):
            self.assertNotIn(boundary, serialized, boundary)


class PolicyBasisTest(unittest.TestCase):
    """"No hysteresis applied" is read from disk, never merely asserted."""

    def test_policy_basis_is_read_from_the_repository_candidate_inventory(self):
        basis = evidence([ANCHOR])["policy_basis"]
        path = ROOT / "evidence" / "regime" / "policy_candidates" / "candidate_inventory.json"
        self.assertEqual(
            basis["path"], "evidence/regime/policy_candidates/candidate_inventory.json",
        )
        self.assertEqual(basis["sha256"], MODULE.file_sha256(path))
        self.assertEqual(basis["policy_status"], "DRAFT_NOT_RATIFIED")
        self.assertEqual(
            basis["component_status"], {"HYSTERESIS": "BLOCKED", "STRESS_OVERRIDE": "BLOCKED"},
        )

    def _root_with_inventory(self, tmp: str, inventory) -> Path:
        root = Path(tmp)
        target = root / "evidence" / "regime" / "policy_candidates" / "candidate_inventory.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            inventory if isinstance(inventory, str) else json.dumps(inventory),
            encoding="utf-8",
        )
        return root

    def test_a_ratified_component_fails_this_module_closed(self):
        base = json.loads(
            (ROOT / "evidence" / "regime" / "policy_candidates" / "candidate_inventory.json")
            .read_text(encoding="utf-8")
        )
        for component in ("HYSTERESIS", "STRESS_OVERRIDE"):
            with self.subTest(component=component):
                changed = copy.deepcopy(base)
                for row in changed["parameters"]:
                    if row["component"] == component:
                        row["status"] = "SUPPORTED"
                with tempfile.TemporaryDirectory() as tmp:
                    root = self._root_with_inventory(tmp, changed)
                    with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                        MODULE.load_policy_basis(root)
                self.assertIn("POLICY_COMPONENT_STATUS_CHANGED", str(caught.exception))

    def test_a_ratified_policy_status_fails_this_module_closed(self):
        base = json.loads(
            (ROOT / "evidence" / "regime" / "policy_candidates" / "candidate_inventory.json")
            .read_text(encoding="utf-8")
        )
        base["policy_status"] = "RATIFIED"
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root_with_inventory(tmp, base)
            with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                MODULE.load_policy_basis(root)
        self.assertIn("POLICY_STATUS_CHANGED", str(caught.exception))

    def test_a_missing_or_malformed_policy_basis_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(MODULE.ReplayEvidenceError):
                MODULE.load_policy_basis(Path(tmp))
            for body in ("not json", '{"contract_version": "other/v1"}'):
                root = self._root_with_inventory(tmp, body)
                with self.subTest(body=body):
                    with self.assertRaises(MODULE.ReplayEvidenceError):
                        MODULE.load_policy_basis(root)


class CoverageFactsTest(unittest.TestCase):
    def test_coverage_counts_are_total_over_every_requested_date(self):
        dates = [ANCHOR, PREVIOUS, KR_UNCOVERED, "not-a-date"]
        report = evidence(dates)
        coverage = report["coverage_facts"]
        self.assertEqual(coverage["requested_date_count"], len(dates))
        self.assertEqual(coverage["requested_dates"], sorted(dates))
        for market in ("KR", "US"):
            facts = coverage["markets"][market]
            self.assertEqual(sum(facts["outcome_counts"].values()), len(dates))
            for axis in MODULE.AXES:
                buckets = facts["axis_status_counts"][axis]
                self.assertEqual(sorted(buckets), sorted(MODULE.AXIS_STATUSES))
                self.assertEqual(sum(buckets.values()), len(dates), f"{market}.{axis}")
                self.assertEqual(
                    facts["axis_observed_ratio"][axis],
                    f"{buckets['OBSERVED']}/{len(dates)}",
                )

    def test_combined_status_counts_match_the_populations_own_summary(self):
        source = population([ANCHOR, KR_UNCOVERED, "not-a-date"])
        report = MODULE.build_evidence(source)
        self.assertEqual(
            report["coverage_facts"]["combined_status_counts"],
            source["combined_summary"]["combined_status_counts"],
        )
        self.assertEqual(
            report["coverage_facts"]["combined_status_counts"],
            {
                "BOTH_MARKETS_REPLAYED": 1,
                "SINGLE_MARKET_ONLY": 1,
                "NOT_COMPUTABLE_NO_MARKET_REPLAYED": 1,
            },
        )

    def test_a_population_summary_that_contradicts_its_records_fails_closed(self):
        tampered = copy.deepcopy(population([ANCHOR]))
        tampered["combined_summary"]["combined_status_counts"]["SINGLE_MARKET_ONLY"] = 9
        tampered["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in tampered.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(tampered)
        self.assertIn("SOURCE_SUMMARY_INCONSISTENT", str(caught.exception))

    def test_kr_five_of_five_and_us_three_of_five_coverage_are_reported_apart(self):
        report = evidence([ANCHOR])
        kr = report["coverage_facts"]["markets"]["KR"]["axis_status_counts"]
        us = report["coverage_facts"]["markets"]["US"]["axis_status_counts"]
        for axis in MODULE.AXES:
            self.assertEqual(kr[axis]["OBSERVED"], 1, axis)
        for axis in ("TREND", "RISK_VOL", "LIQUIDITY"):
            self.assertEqual(us[axis]["OBSERVED"], 1, axis)
        for axis in ("BREADTH", "LEADERSHIP"):
            self.assertEqual(us[axis]["OBSERVED"], 0, axis)
            self.assertEqual(us[axis]["UNKNOWN_EXCLUDED_BY_RATIFICATION_SCOPE"], 1, axis)

    def test_coverage_never_claims_sufficiency(self):
        statement = evidence([ANCHOR])["coverage_facts"]["statement"]
        self.assertIn("not a sufficiency finding", statement)


class UnknownSemanticsTest(unittest.TestCase):
    def test_the_three_absence_meanings_are_never_collapsed(self):
        report = evidence([ANCHOR, KR_UNCOVERED])
        us_axes = report["unknown_facts"]["markets"]["US"]["axes"]
        # Excluded by ratification scope: never a source failure, never blocked.
        for axis in ("BREADTH", "LEADERSHIP"):
            self.assertEqual(us_axes[axis]["unknown_excluded_by_ratification_scope"], 2, axis)
            self.assertEqual(us_axes[axis]["not_computable"], 0, axis)
            self.assertEqual(us_axes[axis]["not_attempted_date_blocked"], 0, axis)
            self.assertEqual(
                us_axes[axis]["excluded_axis_reason_codes"],
                ["EXCLUDED_PROXY_RATIFIED_CURRENT_REFERENCE_ONLY"],
                axis,
            )
        # The whole KR date was blocked: neither excluded nor not-computable.
        kr_axes = report["unknown_facts"]["markets"]["KR"]["axes"]
        for axis in MODULE.AXES:
            self.assertEqual(kr_axes[axis]["not_attempted_date_blocked"], 1, axis)
            self.assertEqual(kr_axes[axis]["unknown_excluded_by_ratification_scope"], 0, axis)
            self.assertEqual(kr_axes[axis]["not_computable"], 0, axis)

    def test_semantics_flags_state_the_unknown_boundary(self):
        semantics = evidence([ANCHOR])["unknown_facts"]["semantics"]
        for key in (
            "unknown_is_insufficient_evidence_not_a_neutral_observation",
            "unknown_excluded_by_ratification_scope_is_not_a_source_failure",
            "not_computable_is_attempted_and_unsupported_not_out_of_scope",
            "not_attempted_date_blocked_is_neither",
        ):
            self.assertIs(semantics[key], True, key)

    def test_an_excluded_axis_never_carries_a_direction_or_a_neutral_state(self):
        report = evidence([ANCHOR, PREVIOUS])
        for axis in ("BREADTH", "LEADERSHIP"):
            sequence = axis_sequence(report, "US", axis)
            self.assertEqual(sequence["distinct_states"], ["UNKNOWN"])
            self.assertEqual(sequence["observed_state_count"], 0)
            self.assertEqual(sequence["state_change_count"], 0)
            for date in (ANCHOR, PREVIOUS):
                self.assertIsNone(observation(report, "US", date)["axis_direction"][axis])

    def test_a_blocked_market_records_an_attributable_reason_code_only(self):
        report = evidence([KR_UNCOVERED])
        kr = report["unknown_facts"]["markets"]["KR"]
        self.assertEqual(kr["blocked_date_count"], 1)
        self.assertEqual(sum(kr["blocked_reason_codes"].values()), 1)
        for code in kr["blocked_reason_codes"]:
            self.assertNotIn(":", code)
        self.assertEqual(kr["lookahead_contained_date_count"], 0)

    def test_a_credential_gap_is_not_computable_not_excluded_and_not_blocked(self):
        report = evidence(
            [ANCHOR],
            credentials={
                "krx_auth_key": KR_FIXTURE.TOKEN, "fred_key": US_FIXTURE.FRED_KEY,
                "alpaca_key": "", "alpaca_secret": "",
            },
        )
        axes = report["unknown_facts"]["markets"]["US"]["axes"]
        self.assertEqual(axes["TREND"]["not_computable"], 1)
        self.assertEqual(axes["TREND"]["unknown_excluded_by_ratification_scope"], 0)
        self.assertEqual(axes["TREND"]["not_attempted_date_blocked"], 0)
        self.assertEqual(
            axes["TREND"]["not_computable_reason_codes"],
            {"BLOCKED_BY_DEDICATED_MARKET_DATA_CREDENTIAL": 1},
        )
        # A source failure on one axis leaves the other two observed.
        for axis in ("RISK_VOL", "LIQUIDITY"):
            self.assertEqual(axes[axis]["not_computable"], 0, axis)

    def test_us_candidate_regime_stays_unknown_and_is_attributed(self):
        report = evidence([ANCHOR, KR_UNCOVERED])
        us = report["unknown_facts"]["markets"]["US"]
        self.assertEqual(us["unknown_candidate_regime_dates"], [KR_UNCOVERED, ANCHOR])
        self.assertEqual(
            us["candidate_classification_status_counts"],
            {"NOT_COMPUTABLE_PARTIAL_AXIS_COVERAGE": 2},
        )
        basis = report["transition_facts"]["markets"]["US"][
            "candidate_regime_unknown_basis_counts"
        ]
        self.assertEqual(basis, {"DATE_BLOCKED": 0, "OBSERVED_BUT_UNCLASSIFIED": 2})

    def test_a_blocked_date_is_attributed_apart_from_an_unclassified_one(self):
        basis = evidence([ANCHOR, "not-a-date"])["transition_facts"]["markets"]["KR"][
            "candidate_regime_unknown_basis_counts"
        ]
        self.assertEqual(basis["DATE_BLOCKED"], 1)
        self.assertEqual(basis["OBSERVED_BUT_UNCLASSIFIED"], 0)


class TransitionFactsTest(unittest.TestCase):
    def test_every_requested_date_is_in_every_sequence(self):
        dates = [ANCHOR, PREVIOUS, KR_UNCOVERED, "not-a-date"]
        report = evidence(dates)
        for market in ("KR", "US"):
            sequences = [report["transition_facts"]["markets"][market]["candidate_regime"]]
            sequences += [axis_sequence(report, market, axis) for axis in MODULE.AXES]
            for sequence in sequences:
                self.assertEqual(sequence["sequence_length"], len(dates))
                self.assertEqual(sequence["sequence_dates"], sorted(dates))
                self.assertEqual(
                    sequence["observed_state_count"] + sequence["unknown_state_count"],
                    len(dates),
                )

    def test_a_blocked_date_breaks_a_run_instead_of_bridging_it(self):
        report = evidence([PREVIOUS, ANCHOR, KR_UNCOVERED])
        sequence = report["transition_facts"]["markets"]["KR"]["candidate_regime"]
        # Sorted order puts the KR-uncovered 2020 date first, so KR reads
        # UNKNOWN then two observed sessions — never one run spanning all three.
        self.assertEqual(sequence["state_sequence"][0], "UNKNOWN")
        self.assertEqual(sequence["runs"][0]["state"], "UNKNOWN")
        self.assertEqual(sequence["runs"][0]["length"], 1)
        self.assertGreaterEqual(sequence["run_count"], 2)
        self.assertNotIn(
            len(sequence["state_sequence"]),
            [run["length"] for run in sequence["runs"] if run["state"] == "UNKNOWN"],
        )

    def test_a_pair_touching_unknown_is_an_evidence_change_not_a_state_change(self):
        report = evidence([ANCHOR, KR_UNCOVERED])
        sequence = report["transition_facts"]["markets"]["KR"]["candidate_regime"]
        self.assertEqual(sequence["transition_count"], 1)
        self.assertEqual(sequence["state_change_count"], 0)
        self.assertEqual(sequence["evidence_availability_change_count"], 1)
        self.assertEqual(
            sequence["transitions"][0]["kind"], "EVIDENCE_AVAILABILITY_CHANGE",
        )
        self.assertEqual(sequence["transitions"][0]["from_state"], "UNKNOWN")

    def test_an_axis_direction_change_is_a_state_change(self):
        report = evidence(
            [PREVIOUS, ANCHOR],
            us_providers=VixByDate({ANCHOR: STRESS_VIX, PREVIOUS: CALM_VIX}),
        )
        sequence = axis_sequence(report, "US", "RISK_VOL")
        self.assertEqual(sequence["state_sequence"], ["NEUTRAL", "STRESS"])
        self.assertEqual(sequence["state_change_count"], 1)
        self.assertEqual(sequence["evidence_availability_change_count"], 0)
        transition = sequence["transitions"][0]
        self.assertEqual(transition["kind"], "STATE_CHANGE")
        self.assertEqual((transition["from_date"], transition["to_date"]), (PREVIOUS, ANCHOR))
        self.assertEqual(transition["calendar_gap_days"], 1)

    def test_adjacency_is_requested_date_order_not_calendar_adjacency(self):
        adjacent = axis_sequence(evidence([PREVIOUS, ANCHOR]), "US", "RISK_VOL")["adjacency"]
        self.assertEqual(adjacent["basis"], "REQUESTED_DATE_ORDER_NOT_CALENDAR_ADJACENCY")
        self.assertEqual(adjacent["adjacent_pair_count"], 1)
        self.assertEqual(adjacent["calendar_consecutive_pair_count"], 1)
        self.assertEqual(adjacent["max_calendar_gap_days"], 1)

        distant = axis_sequence(
            evidence([KR_UNCOVERED, ANCHOR]), "US", "RISK_VOL",
        )["adjacency"]
        self.assertEqual(distant["calendar_consecutive_pair_count"], 0)
        self.assertGreater(distant["max_calendar_gap_days"], 1)

    def test_an_unparseable_requested_date_reports_an_unknown_calendar_gap(self):
        adjacency = axis_sequence(
            evidence([ANCHOR, "not-a-date"]), "KR", "TREND",
        )["adjacency"]
        self.assertEqual(adjacency["unknown_calendar_gap_pair_count"], 1)
        self.assertIsNone(adjacency["max_calendar_gap_days"])

    def test_us_partial_coverage_never_produces_a_regime_transition(self):
        report = evidence([PREVIOUS, ANCHOR])
        sequence = report["transition_facts"]["markets"]["US"]["candidate_regime"]
        self.assertEqual(sequence["distinct_states"], ["UNKNOWN"])
        self.assertEqual(sequence["state_change_count"], 0)
        self.assertEqual(sequence["transition_count"], 0)

    def test_no_cross_market_transition_is_computed(self):
        report = evidence([ANCHOR, PREVIOUS])
        self.assertIs(report["transition_facts"]["cross_market_transitions_computed"], False)
        self.assertEqual(
            sorted(report["transition_facts"]["markets"]), ["KR", "US"],
        )

    def test_transition_counts_agree_with_the_per_date_observations(self):
        dates = sorted([PREVIOUS, ANCHOR, KR_UNCOVERED])
        report = evidence(dates)
        for market in ("KR", "US"):
            states = [
                observation(report, market, date)["candidate_regime"] or "UNKNOWN"
                for date in dates
            ]
            expected_changes = sum(
                1 for before, after in zip(states, states[1:])
                if before != after and "UNKNOWN" not in (before, after)
            )
            sequence = report["transition_facts"]["markets"][market]["candidate_regime"]
            self.assertEqual(sequence["state_sequence"], states, market)
            self.assertEqual(sequence["state_change_count"], expected_changes, market)


class SequenceFactsUnitTest(unittest.TestCase):
    """The one shared derivation behind both transition and hysteresis facts."""

    def test_runs_transitions_and_distinct_states(self):
        facts = MODULE.sequence_facts([
            ("2026-01-05", "RISK_ON"),
            ("2026-01-06", "RISK_ON"),
            ("2026-01-07", "RISK_OFF"),
        ])
        self.assertEqual(facts["sequence_length"], 3)
        self.assertEqual(facts["observed_state_count"], 3)
        self.assertEqual(facts["unknown_state_count"], 0)
        self.assertEqual(facts["distinct_states"], ["RISK_OFF", "RISK_ON"])
        self.assertEqual(facts["run_count"], 2)
        self.assertEqual(facts["longest_run_length"], 2)
        self.assertEqual(facts["state_change_count"], 1)
        self.assertEqual(facts["runs"][0], {
            "state": "RISK_ON", "length": 2,
            "start_date": "2026-01-05", "end_date": "2026-01-06",
        })

    def test_an_immediate_reversal_is_counted_once(self):
        facts = MODULE.sequence_facts([
            ("2026-01-05", "RISK_ON"),
            ("2026-01-06", "RISK_OFF"),
            ("2026-01-07", "RISK_ON"),
        ])
        self.assertEqual(facts["immediate_reversal_count"], 1)
        self.assertEqual(facts["single_observation_run_count"], 3)
        self.assertEqual(facts["immediate_reversals"][0], {
            "state": "RISK_ON",
            "interrupting_state": "RISK_OFF",
            "interrupting_date": "2026-01-06",
            "before_date": "2026-01-05",
            "after_date": "2026-01-07",
        })

    def test_an_unknown_gap_is_never_an_immediate_reversal(self):
        facts = MODULE.sequence_facts([
            ("2026-01-05", "RISK_ON"),
            ("2026-01-06", "UNKNOWN"),
            ("2026-01-07", "RISK_ON"),
        ])
        self.assertEqual(facts["immediate_reversal_count"], 0)
        self.assertEqual(facts["state_change_count"], 0)
        self.assertEqual(facts["evidence_availability_change_count"], 2)
        # The two RISK_ON observations are separate runs — the gap is not bridged.
        self.assertEqual(facts["run_count"], 3)

    def test_an_empty_or_single_point_sequence_is_safe(self):
        for points in ([], [("2026-01-05", "UNKNOWN")]):
            with self.subTest(points=points):
                facts = MODULE.sequence_facts(points)
                self.assertEqual(facts["transition_count"], 0)
                self.assertEqual(facts["immediate_reversal_count"], 0)
                self.assertEqual(facts["longest_run_length"], len(points))
                self.assertIsNone(facts["adjacency"]["max_calendar_gap_days"])


class StressDetectionFactsTest(unittest.TestCase):
    def test_a_stress_direction_the_existing_rule_emitted_is_counted(self):
        report = evidence(
            [PREVIOUS, ANCHOR],
            us_providers=VixByDate({ANCHOR: STRESS_VIX, PREVIOUS: CALM_VIX}),
        )
        stress = report["stress_detection_facts"]["markets"]["US"]
        self.assertEqual(stress["dates_with_any_stress_axis"], 1)
        self.assertEqual(stress["stress_axis_counts"]["RISK_VOL"], 1)
        self.assertEqual(stress["stress_axis_counts"]["TREND"], 0)
        self.assertEqual(stress["stress_observations"], [{
            "requested_date": ANCHOR,
            "stress_axes": ["RISK_VOL"],
            "candidate_regime_is_stress": False,
        }])

    def test_a_calm_market_reports_zero_stress_without_inventing_a_state(self):
        us = evidence([ANCHOR, PREVIOUS])["stress_detection_facts"]["markets"]["US"]
        self.assertEqual(us["dates_with_any_stress_axis"], 0)
        self.assertEqual(us["dates_with_stress_candidate_regime"], 0)
        self.assertEqual(us["stress_observations"], [])
        self.assertEqual(
            us["stress_axis_counts"], {axis: 0 for axis in MODULE.AXES},
        )

    def test_stress_is_carried_from_the_market_record_not_recomputed(self):
        # The KR fixture's cross-section is volatile enough that the existing
        # unmodified rule already returns STRESS; this report must report exactly
        # that, taken from the market's own record rather than re-derived.
        source = population([ANCHOR])
        report = MODULE.build_evidence(source)
        kr_record = next(
            row for row in source["market_populations"]["KR"]["records"]
            if row["requested_date"] == ANCHOR
        )
        candidate = kr_record["candidate_normalized_result"]
        risk_vol = next(
            row for row in candidate["axes"] if row["axis"] == "RISK_VOL"
        )
        self.assertEqual(risk_vol["direction"], "STRESS")
        self.assertEqual(candidate["paper_reference"]["candidate_regime"], "STRESS")

        stress = report["stress_detection_facts"]["markets"]["KR"]
        self.assertEqual(stress["dates_with_any_stress_axis"], 1)
        self.assertEqual(stress["dates_with_stress_candidate_regime"], 1)
        self.assertEqual(stress["stress_observations"], [{
            "requested_date": ANCHOR,
            "stress_axes": ["RISK_VOL"],
            "candidate_regime_is_stress": True,
        }])
        self.assertEqual(
            observation(report, "KR", ANCHOR)["axis_direction"]["RISK_VOL"], "STRESS",
        )

    def test_stress_detection_is_attributed_to_the_existing_unmodified_rule(self):
        stress = evidence([ANCHOR])["stress_detection_facts"]
        self.assertEqual(
            stress["detection_source"],
            "regime/paper_regime_reference.py::axis,classify"
            " (applied by the KR and US replay populations, not re-applied here)",
        )
        self.assertEqual(stress["stress_override_component_status"], "BLOCKED")
        self.assertIn("draws no conclusion", stress["statement"])

    def test_a_blocked_date_never_contributes_a_stress_observation(self):
        report = evidence(
            [ANCHOR, KR_UNCOVERED],
            us_providers=VixByDate({}, default_vix=STRESS_VIX),
        )
        kr_dates = [
            row["requested_date"]
            for row in report["stress_detection_facts"]["markets"]["KR"]["stress_observations"]
        ]
        # The KR-uncovered date was blocked, so it contributes nothing at all —
        # only the date KR actually replayed can carry a stress observation.
        self.assertEqual(kr_dates, [ANCHOR])
        us_dates = [
            row["requested_date"]
            for row in report["stress_detection_facts"]["markets"]["US"]["stress_observations"]
        ]
        self.assertEqual(us_dates, [KR_UNCOVERED, ANCHOR])


class HysteresisFactsTest(unittest.TestCase):
    def test_hysteresis_view_mirrors_the_transition_sequence_exactly(self):
        report = evidence(
            [PREVIOUS, ANCHOR],
            us_providers=VixByDate({ANCHOR: STRESS_VIX, PREVIOUS: CALM_VIX}),
        )
        sequence = axis_sequence(report, "US", "RISK_VOL")
        view = report["hysteresis_facts"]["markets"]["US"]["axis_direction"]["RISK_VOL"]
        self.assertEqual(view["runs"], sequence["runs"])
        self.assertEqual(view["run_count"], sequence["run_count"])
        self.assertEqual(view["raw_state_change_count"], sequence["state_change_count"])
        self.assertEqual(
            view["immediate_reversal_count"], sequence["immediate_reversal_count"],
        )
        self.assertEqual(view["adjacency"], sequence["adjacency"])

    def test_every_hysteresis_view_carries_its_adjacency_caveat(self):
        report = evidence([ANCHOR, PREVIOUS])
        for market in ("KR", "US"):
            views = [report["hysteresis_facts"]["markets"][market]["candidate_regime"]]
            views += [
                report["hysteresis_facts"]["markets"][market]["axis_direction"][axis]
                for axis in MODULE.AXES
            ]
            for view in views:
                self.assertEqual(
                    view["adjacency"]["basis"],
                    "REQUESTED_DATE_ORDER_NOT_CALENDAR_ADJACENCY",
                )

    def test_hysteresis_statement_stops_at_observation(self):
        facts = evidence([ANCHOR])["hysteresis_facts"]
        self.assertEqual(facts["hysteresis_component_status"], "BLOCKED")
        self.assertIn("not evidence that", facts["statement"])
        self.assertIn("not a proposed value", facts["statement"])


class PitAndAuditSeparationTest(unittest.TestCase):
    def test_structural_facts_are_declared(self):
        facts = evidence([ANCHOR])["pit_and_audit_separation"]
        for key in (
            "each_date_summarized_from_its_own_record",
            "no_date_observation_altered_by_another_date",
        ):
            self.assertIs(facts[key], True, key)
        for key in (
            "future_dates_used_in_any_date_evaluation",
            "source_population_mutated_by_this_module",
            "market_observations_recomputed_by_this_module",
            "candidate_rule_modified_by_this_module",
            "threshold_introduced_by_this_module",
            "hysteresis_applied_by_this_module",
            "episode_selected_by_this_module",
        ):
            self.assertIs(facts[key], False, key)

    def test_one_dates_observation_ignores_the_rest_of_the_batch(self):
        solo = observation(evidence([ANCHOR]), "KR", ANCHOR)
        batched = observation(
            evidence([ANCHOR, PREVIOUS, KR_UNCOVERED, "not-a-date"]), "KR", ANCHOR,
        )
        self.assertEqual(solo, batched)

    def test_a_market_contained_for_lookahead_contributes_nothing(self):
        source = population([ANCHOR, PREVIOUS])
        contained = copy.deepcopy(source)
        view = contained["records"][0]["markets"]["KR"]
        view.update({
            "outcome": "BLOCKED",
            "market_status": "OBSERVED",
            "failure_reason": "COMBINED_LOOKAHEAD_VIOLATION",
            "lookahead_violation": True,
            "candidate_regime": None,
            "candidate_classification_status": None,
            "source_dates_consulted": [],
        })
        contained["records"][0]["combined_normalized_result"][
            "per_market_candidate_regime"
        ]["KR"] = None
        contained["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in contained.items() if k != "payload_sha256"}
        )
        report = MODULE.build_evidence(contained)
        date = contained["records"][0]["requested_date"]
        cell = observation(report, "KR", date)
        self.assertEqual(cell["outcome"], "BLOCKED")
        self.assertIs(cell["lookahead_contained"], True)
        self.assertIsNone(cell["candidate_regime"])
        for axis in MODULE.AXES:
            self.assertEqual(cell["axis_status"][axis], "NOT_ATTEMPTED_DATE_BLOCKED")
            self.assertIsNone(cell["axis_direction"][axis])
        self.assertEqual(
            report["unknown_facts"]["markets"]["KR"]["lookahead_contained_date_count"], 1,
        )
        self.assertEqual(
            report["unknown_facts"]["markets"]["KR"]["blocked_reason_codes"],
            {"COMBINED_LOOKAHEAD_VIOLATION": 1},
        )

    def test_no_retained_or_live_source_is_mutated(self):
        korea_dir = ROOT / "data" / "observations" / "korea_market_signals"
        us_dir = ROOT / "evidence" / "free_market_data" / "derived"
        latest = [
            ROOT / "data" / "latest_korea_market_signals.json",
            ROOT / "data" / "latest_free_market_data.json",
            ROOT / "evidence" / "regime" / "policy_candidates" / "candidate_inventory.json",
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
        evidence([ANCHOR, PREVIOUS, KR_UNCOVERED, "not-a-date"])
        self.assertEqual(before, snapshot())


class EpisodeLabelTest(unittest.TestCase):
    def test_labels_are_carried_verbatim_and_never_selected_here(self):
        report = evidence(None, [{"name": "CALLER-WINDOW", "dates": [ANCHOR]}])
        selection = report["episode_selection"]
        self.assertIs(selection["episode_selected_by_this_module"], False)
        self.assertIs(selection["label_influences_any_fact"], False)
        self.assertEqual(selection["selection_source"], "CALLER_SUPPLIED_ONLY")
        self.assertEqual([row["name"] for row in report["episodes"]], ["CALLER-WINDOW"])
        self.assertEqual(
            report["episodes"][0]["label_semantics"],
            "OPAQUE_CALLER_LABEL_NOT_A_REGIME_OR_OUTCOME_CLAIM",
        )

    def test_a_label_never_changes_a_single_fact(self):
        plain = evidence([ANCHOR, PREVIOUS])
        labelled = evidence(None, [{"name": "STRESS-EPISODE", "dates": [ANCHOR, PREVIOUS]}])

        def stripped(report):
            trimmed = copy.deepcopy(report)
            trimmed.pop("episodes")
            trimmed.pop("payload_sha256")
            trimmed["source_population"].pop("payload_sha256")
            return MODULE.canonical_json(trimmed)

        self.assertEqual(stripped(plain), stripped(labelled))

    def test_a_regime_sounding_label_never_reaches_a_stress_or_regime_fact(self):
        report = evidence(None, [{"name": "STRESS-EPISODE", "dates": [ANCHOR]}])
        for section in ("stress_detection_facts", "transition_facts", "hysteresis_facts"):
            self.assertNotIn("STRESS-EPISODE", MODULE.canonical_json(report[section]))


class DeterminismTest(unittest.TestCase):
    def test_the_report_is_a_pure_function_of_the_population(self):
        source = population([ANCHOR, PREVIOUS])
        first = MODULE.build_evidence(source)
        second = MODULE.build_evidence(copy.deepcopy(source))
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))

    def test_shuffled_requested_dates_produce_an_identical_report(self):
        forward = evidence([ANCHOR, PREVIOUS, KR_UNCOVERED])
        shuffled = evidence([KR_UNCOVERED, ANCHOR, PREVIOUS])
        self.assertEqual(
            MODULE.canonical_json(forward), MODULE.canonical_json(shuffled),
        )

    def test_duplicate_requested_dates_collapse_to_one_observation(self):
        report = evidence([ANCHOR, ANCHOR])
        self.assertEqual(report["coverage_facts"]["requested_date_count"], 1)
        self.assertEqual(list(report["per_date_observations"]["KR"]), [ANCHOR])


class ValidationTest(unittest.TestCase):
    def test_validate_accepts_its_own_output(self):
        report = evidence([ANCHOR, PREVIOUS])
        self.assertEqual(MODULE.validate_evidence(copy.deepcopy(report)), report)

    def test_validate_rederives_from_the_source_population_when_supplied(self):
        source = population([ANCHOR, PREVIOUS])
        report = MODULE.build_evidence(source)
        self.assertEqual(
            MODULE.validate_evidence(copy.deepcopy(report), population=source), report,
        )
        drifted = copy.deepcopy(report)
        drifted["coverage_facts"]["markets"]["KR"]["outcome_counts"]["OBSERVED"] = 0
        drifted["coverage_facts"]["markets"]["KR"]["outcome_counts"]["BLOCKED"] = 2
        with self.assertRaises(MODULE.ReplayEvidenceError):
            MODULE.validate_evidence(resigned(drifted), population=source)

    def test_validate_rejects_a_tampered_or_unsigned_payload(self):
        tampered = copy.deepcopy(evidence([ANCHOR]))
        tampered["coverage_facts"]["requested_date_count"] = 99
        with self.assertRaises(MODULE.ReplayEvidenceError):
            MODULE.validate_evidence(tampered)
        unsigned = copy.deepcopy(evidence([ANCHOR]))
        unsigned.pop("payload_sha256")
        with self.assertRaises(MODULE.ReplayEvidenceError):
            MODULE.validate_evidence(unsigned)

    def test_validate_rejects_a_flipped_authority_flag(self):
        tampered = copy.deepcopy(evidence([ANCHOR]))
        tampered["authority"]["order_authorized"] = True
        with self.assertRaises(MODULE.ReplayEvidenceError):
            MODULE.validate_evidence(resigned(tampered))

    def test_validate_rejects_axis_buckets_that_do_not_add_up(self):
        tampered = copy.deepcopy(evidence([ANCHOR, PREVIOUS]))
        tampered["coverage_facts"]["markets"]["US"]["axis_status_counts"]["BREADTH"][
            "UNKNOWN_EXCLUDED_BY_RATIFICATION_SCOPE"
        ] = 0
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(tampered))
        self.assertIn("COVERAGE_COUNTS_NOT_TOTAL", str(caught.exception))

    def test_validate_rejects_a_dropped_date_in_a_sequence(self):
        tampered = copy.deepcopy(evidence([ANCHOR, PREVIOUS]))
        sequence = tampered["transition_facts"]["markets"]["KR"]["candidate_regime"]
        sequence["sequence_length"] = 1
        sequence["sequence_dates"] = [ANCHOR]
        sequence["state_sequence"] = sequence["state_sequence"][:1]
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(tampered))
        self.assertIn("SEQUENCE_NOT_TOTAL_OVER_REQUESTED_DATES", str(caught.exception))

    def test_validate_rejects_an_unknown_pair_relabelled_as_a_state_change(self):
        tampered = copy.deepcopy(evidence([ANCHOR, KR_UNCOVERED]))
        sequence = tampered["transition_facts"]["markets"]["KR"]["candidate_regime"]
        sequence["transitions"][0]["kind"] = "STATE_CHANGE"
        sequence["state_change_count"] = 1
        sequence["evidence_availability_change_count"] = 0
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(tampered))
        self.assertIn("UNKNOWN_MUST_NOT_COUNT_AS_STATE_CHANGE", str(caught.exception))

    def test_validate_rejects_inconsistent_transition_totals(self):
        tampered = copy.deepcopy(evidence([ANCHOR, PREVIOUS]))
        tampered["transition_facts"]["markets"]["KR"]["candidate_regime"][
            "transition_count"
        ] = 7
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(tampered))
        self.assertIn("TRANSITION_COUNTS_INCONSISTENT", str(caught.exception))

    def test_validate_rejects_an_invented_axis_status_bucket(self):
        tampered = copy.deepcopy(evidence([ANCHOR]))
        buckets = tampered["coverage_facts"]["markets"]["US"]["axis_status_counts"]["BREADTH"]
        buckets.pop("UNRECOGNIZED_SOURCE_AXIS_STATUS")
        buckets["ESTIMATED"] = 0
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(tampered))
        self.assertIn("AXIS_STATUS_VOCABULARY_INVALID", str(caught.exception))

    def test_validate_rejects_a_relabelled_episode_selection_claim(self):
        for key in ("episode_selected_by_this_module", "label_influences_any_fact"):
            with self.subTest(key=key):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                tampered["episode_selection"][key] = True
                with self.assertRaises(MODULE.ReplayEvidenceError):
                    MODULE.validate_evidence(resigned(tampered))


class OutputBoundaryTest(unittest.TestCase):
    def test_write_refuses_any_path_inside_the_checkout(self):
        report = evidence([ANCHOR])
        for forbidden in (
            ROOT / "data" / "observations" / "deterministic_replay_evidence.json",
            ROOT / "evidence" / "regime" / "deterministic_replay_evidence" / "report.json",
            ROOT / "deterministic_replay_evidence_sneak.json",
        ):
            with self.subTest(path=str(forbidden)):
                with self.assertRaises(MODULE.ReplayEvidenceError):
                    MODULE.write_evidence(report, forbidden, root=ROOT)
                self.assertFalse(forbidden.exists())

    def test_write_accepts_an_external_path(self):
        report = evidence([ANCHOR])
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "outside_checkout" / "evidence.json"
            written = MODULE.write_evidence(report, out, root=ROOT)
            self.assertTrue(written.is_file())
            self.assertEqual(json.loads(written.read_text(encoding="utf-8")), report)

    def test_default_temp_out_is_never_inside_the_checkout(self):
        path = MODULE._default_temp_out()
        try:
            with self.assertRaises(ValueError):
                path.resolve().relative_to(ROOT.resolve())
        finally:
            path.unlink(missing_ok=True)

    def test_load_population_file_reads_external_input_and_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "population.json"
            source = population([ANCHOR])
            path.write_text(json.dumps(source), encoding="utf-8")
            self.assertEqual(MODULE.load_population_file(path), source)
            for body in ("not json", "[]"):
                path.write_text(body, encoding="utf-8")
                with self.subTest(body=body):
                    with self.assertRaises(MODULE.ReplayEvidenceError):
                        MODULE.load_population_file(path)
            with self.assertRaises(MODULE.ReplayEvidenceError):
                MODULE.load_population_file(Path(tmp) / "missing.json")

    def test_no_credential_ever_reaches_the_report(self):
        with mock.patch.object(
            MODULE.CSR.KRP, "build_population",
            side_effect=MODULE.CSR.KRP.ReplayPopulationError(f"HTTP_ERROR:{KR_FIXTURE.TOKEN}"),
        ):
            report = evidence([ANCHOR])
        serialized = MODULE.canonical_json(report)
        for secret in COMBINED.CREDENTIALS.values():
            self.assertNotIn(secret, serialized)
        self.assertEqual(
            report["unknown_facts"]["markets"]["KR"]["blocked_reason_codes"],
            {"MARKET_POPULATION_UNAVAILABLE": 1},
        )

    def test_module_never_reads_the_account_or_trading_alpaca_credential(self):
        code = "\n".join(
            line for line in SCRIPT.read_text(encoding="utf-8").splitlines()
            if not line.strip().startswith("#")
        )
        self.assertNotIn("ALPACA_API_KEY", code)
        self.assertNotIn("ALPACA_API_SECRET", code)


if __name__ == "__main__":
    unittest.main()
