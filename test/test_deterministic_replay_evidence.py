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

    def test_a_population_whose_us_fred_vintage_is_in_the_future_is_never_summarized(self):
        # ``revalidated_by_its_own_validator`` has to mean the market validators
        # too, and specifically the point-in-time bind inside them. Every
        # observation date in this record is on or before the requested date, so
        # the combined join's lookahead re-check is satisfied; only the ALFRED
        # vintage the US FRED measurement was served at moved past it. Every
        # coverage, UNKNOWN, transition, stress, and hysteresis fact below is
        # counted from that record, so summarizing it would publish a
        # deterministic replay report over evidence that did not exist on the
        # replayed date.
        future = "2026-09-01"
        tampered = copy.deepcopy(population([ANCHOR]))
        embedded = tampered["market_populations"]["US"]
        embedded["records"][0]["five_axis"]["axes"]["LIQUIDITY"]["measurement"][
            "series"
        ][0].update({"realtime_start": future, "realtime_end": future})
        embedded["payload_sha256"] = MODULE.CSR.USP.payload_sha256(
            {k: v for k, v in embedded.items() if k != "payload_sha256"}
        )
        tampered["market_population_status"]["US"]["payload_sha256"] = embedded[
            "payload_sha256"
        ]
        tampered["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in tampered.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(tampered)
        self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))
        self.assertIn("US_REPLAY_LOOKAHEAD_VIOLATION", str(caught.exception))

    def test_a_population_dated_by_a_day_no_calendar_has_is_never_summarized(self):
        # The same requirement one level deeper than a future vintage: the date
        # is not later than the requested date, it is not a date at all.
        # 2026-02-31 is DATE10-shaped and sorts *before* the requested
        # 2026-08-28, so under a shape check plus a string comparison it read as
        # ordinary backward-looking evidence in both markets, and every
        # coverage, UNKNOWN, transition, stress, and hysteresis fact below would
        # have been counted from a record dated by a day that never existed.
        impossible = "2026-02-31"
        for market, module, mutate in (
            ("KR", MODULE.CSR.KRP,
             lambda record: record["no_lookahead_attestation"][
                 "session_dates_used"].append("20260231")),
            ("US", MODULE.CSR.USP,
             lambda record: record["five_axis"]["axes"]["RISK_VOL"][
                 "measurement"].__setitem__("observation_date", impossible)),
        ):
            with self.subTest(market=market):
                tampered = copy.deepcopy(population([ANCHOR]))
                embedded = tampered["market_populations"][market]
                mutate(embedded["records"][0])
                embedded["payload_sha256"] = module.payload_sha256(
                    {k: v for k, v in embedded.items() if k != "payload_sha256"}
                )
                tampered["market_population_status"][market]["payload_sha256"] = embedded[
                    "payload_sha256"
                ]
                tampered["payload_sha256"] = MODULE.CSR.payload_sha256(
                    {k: v for k, v in tampered.items() if k != "payload_sha256"}
                )
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.build_evidence(tampered)
                self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))
                self.assertIn("CALENDAR_INVALID", str(caught.exception))

    def test_a_population_whose_us_pit_declaration_denies_pit_is_never_summarized(self):
        tampered = copy.deepcopy(population([ANCHOR]))
        embedded = tampered["market_populations"]["US"]
        embedded["pit_replay"]["future_dates_used_in_any_date_evaluation"] = True
        embedded["payload_sha256"] = MODULE.CSR.USP.payload_sha256(
            {k: v for k, v in embedded.items() if k != "payload_sha256"}
        )
        tampered["market_population_status"]["US"]["payload_sha256"] = embedded[
            "payload_sha256"
        ]
        tampered["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in tampered.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(tampered)
        self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))
        self.assertIn("PIT_REPLAY_DECLARATION_INVALID", str(caught.exception))

    def test_a_population_whose_market_provenance_was_stripped_is_never_summarized(self):
        # ``revalidated_by_its_own_validator`` is a claim about the *market*
        # validators as well as the join. An embedded record whose
        # official-source hashes were deleted and then re-signed must never
        # reach this report as an observation, because every coverage,
        # transition, stress, and run fact below would then describe a
        # measurement nothing attributes to a source response.
        for market, module in (("KR", MODULE.CSR.KRP), ("US", MODULE.CSR.USP)):
            with self.subTest(market=market):
                tampered = copy.deepcopy(population([ANCHOR]))
                embedded = tampered["market_populations"][market]
                embedded["records"][0]["source_hashes"] = None
                embedded["payload_sha256"] = module.payload_sha256(
                    {k: v for k, v in embedded.items() if k != "payload_sha256"}
                )
                tampered["market_population_status"][market]["payload_sha256"] = embedded[
                    "payload_sha256"
                ]
                tampered["payload_sha256"] = MODULE.CSR.payload_sha256(
                    {k: v for k, v in tampered.items() if k != "payload_sha256"}
                )
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.build_evidence(tampered)
                self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))
                self.assertIn(
                    "OBSERVED_RECORD_MUST_CARRY_ITS_SOURCE_HASHES", str(caught.exception),
                )

    def test_an_unattributed_blocked_market_never_becomes_an_unknown_fact(self):
        # A market whose whole population is unavailable is BLOCKED on every
        # date, and its recorded reason is the only thing that says why. A
        # re-signed population that deleted the reason — from the status *and*
        # from every record derived from it, so the payload stays
        # self-consistent — must be refused by the join's own validator here,
        # rather than summarized into UNKNOWN facts nothing attributes.
        with mock.patch.object(
            MODULE.CSR.KRP, "build_population",
            side_effect=MODULE.CSR.KRP.ReplayPopulationError("CONTRACT_MISSING"),
        ):
            source = population([ANCHOR, PREVIOUS])
        self.assertIsNone(source["market_populations"]["KR"])
        # The attributed population is summarized normally: containment still
        # produces honest single-market evidence.
        report = MODULE.build_evidence(copy.deepcopy(source))
        self.assertIs(
            report["coverage_facts"]["markets"]["KR"]["population_available"], False,
        )
        self.assertEqual(
            sorted(report["unknown_facts"]["markets"]["KR"]["blocked_reason_codes"]),
            ["MARKET_POPULATION_UNAVAILABLE"],
        )

        stripped = copy.deepcopy(source)
        stripped["market_population_status"]["KR"]["unavailable_reason"] = None
        for record in stripped["records"]:
            record["markets"]["KR"]["failure_reason"] = None
        stripped["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in stripped.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(stripped)
        self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))
        self.assertIn(
            "UNAVAILABLE_MARKET_MUST_CARRY_AN_ATTRIBUTABLE_REASON",
            str(caught.exception),
        )

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
            "common_v1_replay_policy_ratified_or_changed", "market_regime_asserted",
        ):
            self.assertIs(conclusion[key], False, key)

    def test_validator_rejects_a_deleted_refusal_flag(self):
        # Adversarial: a report can be made to "say less" by deleting a flag
        # rather than flipping it. Removing an explicit refusal must fail, not
        # pass by leaving nothing to check.
        for flag in MODULE.CONCLUSION_FALSE_FLAGS:
            with self.subTest(flag=flag):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                tampered["policy_conclusion"].pop(flag)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn("POLICY_CONCLUSION_SCHEMA_INVALID", str(caught.exception))

    def test_validator_rejects_a_deleted_authority_boundary(self):
        for key in ("order_authorized", "hysteresis_authorized", "real_authorized"):
            with self.subTest(key=key):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                tampered["authority"].pop(key)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn("EVIDENCE_AUTHORITY_SCHEMA_INVALID", str(caught.exception))

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
    """Two policies, read from disk, kept apart, and neither reported absent."""

    def test_the_unratified_market_specific_candidate_policy_is_read_from_disk(self):
        basis = evidence([ANCHOR])["policy_basis"]["market_specific_candidate_policy"]
        path = ROOT / "evidence" / "regime" / "policy_candidates" / "candidate_inventory.json"
        self.assertEqual(
            basis["path"], "evidence/regime/policy_candidates/candidate_inventory.json",
        )
        self.assertEqual(basis["sha256"], MODULE.file_sha256(path))
        self.assertEqual(basis["policy_status"], "DRAFT_NOT_RATIFIED")
        self.assertEqual(
            basis["scope"], "MARKET_SPECIFIC_NORMALIZATION_FRESHNESS_AND_REPLAY",
        )
        self.assertEqual(
            basis["component_status"], {"HYSTERESIS": "BLOCKED", "STRESS_OVERRIDE": "BLOCKED"},
        )

    def test_the_ratified_common_replay_policy_is_quoted_not_called_absent(self):
        # The correction this section exists for. The repository *does* hold a
        # ratified replay-only common policy with explicit hysteresis and stress
        # behavior; the report must quote it from the live registry, through the
        # module that owns it, rather than describing it as missing.
        basis = evidence([ANCHOR])["policy_basis"]["common_v1_replay_policy"]
        registry = ROOT / "config" / "regime_source_owner_registry_v2.json"
        alignment = json.loads(registry.read_text(encoding="utf-8"))["common_v1_alignment"]
        self.assertEqual(basis["path"], "config/regime_source_owner_registry_v2.json")
        self.assertEqual(basis["sha256"], MODULE.file_sha256(registry))
        self.assertEqual(basis["policy_status"], "RATIFIED_PAPER_BASELINE_V1")
        self.assertIs(basis["present_in_repository"], True)
        self.assertEqual(basis["hysteresis"], alignment["hysteresis"])
        self.assertEqual(
            basis["stress_classification"], alignment["classification"]["STRESS"],
        )
        self.assertEqual(
            basis["implemented_by"],
            "regime/decision_authority.py::load_common_v1_policy",
        )
        self.assertEqual(basis["pit_replay_acceptance"], "NOT_ACCEPTED")

    def test_the_ratified_common_policy_is_reported_as_not_applied_here(self):
        basis = evidence([ANCHOR])["policy_basis"]["common_v1_replay_policy"]
        self.assertIs(basis["applied_to_this_replay"], False)
        self.assertIs(
            basis["market_specific_normalization_freshness_and_replay_inherited"], False,
        )
        self.assertIn("NOT_INHERITED", basis["not_applied_reason"])
        statement = evidence([ANCHOR])["policy_basis"]["statement"]
        self.assertIn("is not absent", statement)
        self.assertIn("not applied to this", statement)

    def test_the_common_policy_binds_to_the_module_that_owns_it(self):
        from regime import decision_authority as live_da
        self.assertIs(MODULE.DA, live_da)
        basis = evidence([ANCHOR])["policy_basis"]["common_v1_replay_policy"]
        expected = live_da.load_common_v1_policy()
        self.assertEqual(basis["binding"], expected["binding"])
        self.assertEqual(basis["hysteresis"], expected["hysteresis"])

    def test_a_registry_that_no_longer_carries_the_ratified_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = self._root_with_inventory(
                tmp,
                json.loads(
                    (ROOT / "evidence" / "regime" / "policy_candidates"
                     / "candidate_inventory.json").read_text(encoding="utf-8")
                ),
            )
            # The candidate inventory is present and unchanged, so only the
            # missing ratified common policy can fail this.
            with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                MODULE.load_policy_basis(root)
        self.assertIn("COMMON_V1_POLICY_UNREADABLE", str(caught.exception))

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

    def test_validate_rejects_a_policy_pin_that_is_not_a_sha256(self):
        # Adversarial: both layers pin the file they quote, but the pins were
        # only required to be *present*. A re-signed report could therefore name
        # a policy digest that could never identify any file, while still reading
        # as provenance.
        base = evidence([ANCHOR])
        for layer in ("market_specific_candidate_policy", "common_v1_replay_policy"):
            for label, digest in (
                ("not_a_sha", "not-a-sha256"),
                ("empty", ""),
                ("truncated", "a" * 63),
                ("overlong", "a" * 65),
                ("uppercase", "A" * 64),
                ("non_hex", "z" * 64),
                ("null", None),
                ("numeric", 0),
            ):
                with self.subTest(layer=layer, digest=label):
                    tampered = copy.deepcopy(base)
                    tampered["policy_basis"][layer]["sha256"] = digest
                    with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                        MODULE.validate_evidence(resigned(tampered))
                    self.assertIn("POLICY_BASIS_SHA_INVALID", str(caught.exception))
                    self.assertIn(layer, str(caught.exception))

    def test_a_well_formed_policy_pin_is_still_only_re_bound_by_disk(self):
        # The honest limit of the syntax check above, stated rather than implied:
        # a detached verifier holding only the report cannot tell a well-formed
        # wrong digest from the right one. Re-binding the pin to the quoted file
        # is load_policy_basis re-reading disk, so it stays a separate,
        # checkout-bound guarantee.
        tampered = copy.deepcopy(evidence([ANCHOR]))
        tampered["policy_basis"]["common_v1_replay_policy"]["sha256"] = "0" * 64
        MODULE.validate_evidence(resigned(tampered))
        rebuilt = evidence([ANCHOR])["policy_basis"]["common_v1_replay_policy"]["sha256"]
        self.assertEqual(
            rebuilt,
            MODULE.file_sha256(ROOT / "config" / "regime_source_owner_registry_v2.json"),
        )

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
        # The combined slice's own validator recomputes its summary, so this is
        # already refused upstream; this module's independent recount is a
        # second, deliberately redundant guard exercised directly below.
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(tampered)
        self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.source_combined_counts(tampered)
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
        self.assertEqual(stress["stress_policy_status"], {
            "market_specific_candidate_component": "BLOCKED",
            "common_v1_replay_policy": "RATIFIED_PAPER_BASELINE_V1",
        })
        # The ratified common policy's own stress behavior is quoted, and
        # explicitly not applied — never reported as nonexistent.
        behavior = stress["common_v1_replay_stress_behavior"]
        self.assertEqual(behavior["stress_entry"], "IMMEDIATE")
        self.assertEqual(
            behavior["stress_exit"],
            "TWO_CONSECUTIVE_NON_STRESS_AND_S_GREATER_THAN_NEGATIVE_3",
        )
        self.assertIs(behavior["applied_to_this_replay"], False)
        self.assertIn("draws no conclusion", stress["statement"])
        self.assertIn("rather than described as absent", stress["statement"])

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
        self.assertEqual(facts["hysteresis_policy_status"], {
            "market_specific_candidate_component": "BLOCKED",
            "common_v1_replay_policy": "RATIFIED_PAPER_BASELINE_V1",
        })
        self.assertIn("not evidence that", facts["statement"])
        self.assertIn("not a proposed value", facts["statement"])

    def test_the_ratified_common_hysteresis_rule_is_quoted_but_not_applied(self):
        # The report may not describe the repository as having no hysteresis
        # rule: one is ratified for replay-only common aggregation. It is quoted
        # with its own parameters and explicitly marked unapplied here.
        quoted = evidence([ANCHOR])["hysteresis_facts"]["common_v1_replay_hysteresis"]
        alignment = json.loads(
            (ROOT / "config" / "regime_source_owner_registry_v2.json")
            .read_text(encoding="utf-8")
        )["common_v1_alignment"]["hysteresis"]
        for key, value in alignment.items():
            self.assertEqual(quoted[key], value, key)
        self.assertIs(quoted["applied_to_this_replay"], False)
        self.assertIn("NOT_INHERITED", quoted["not_applied_reason"])

    def test_validator_rejects_applying_the_ratified_common_rule_here(self):
        for path in (
            ("hysteresis_facts", "common_v1_replay_hysteresis"),
            ("stress_detection_facts", "common_v1_replay_stress_behavior"),
        ):
            with self.subTest(path=path):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                tampered[path[0]][path[1]]["applied_to_this_replay"] = True
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn(
                    "COMMON_V1_POLICY_MUST_NOT_BE_APPLIED", str(caught.exception),
                )

    def test_validator_rejects_reporting_the_ratified_policy_as_absent(self):
        for mutate in (
            lambda basis: basis.pop("common_v1_replay_policy"),
            lambda basis: basis["common_v1_replay_policy"].update(
                {"present_in_repository": False}
            ),
            lambda basis: basis["common_v1_replay_policy"].update({"hysteresis": None}),
            lambda basis: basis["common_v1_replay_policy"].update(
                {"policy_status": "UNRATIFIED"}
            ),
        ):
            with self.subTest(mutate=mutate):
                tampered = copy.deepcopy(evidence([ANCHOR]))
                mutate(tampered["policy_basis"])
                with self.assertRaises(MODULE.ReplayEvidenceError):
                    MODULE.validate_evidence(resigned(tampered))


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
        # Containment is exercised at the projection and at the aggregation
        # rather than by editing a population: the combined slice's validator
        # now re-derives every market view from its embedded record, so a
        # contained view cannot be forged into a population this module accepts,
        # and a genuine contained view can only come from a record the join
        # itself demoted. What must hold either way is that the demoted market
        # contributes nothing — its intact record is never read behind the
        # containment.
        source = population([ANCHOR])
        embedded = source["market_populations"]["KR"]
        kr_record = next(
            row for row in embedded["records"] if row["requested_date"] == ANCHOR
        )
        self.assertEqual(kr_record["status"], "OBSERVED")
        contained_view = {
            "market": "KR",
            "market_status": "OBSERVED",
            "outcome": "BLOCKED",
            "effective_date": None,
            "axis_coverage": None,
            "candidate_regime": None,
            "candidate_classification_status": None,
            "runtime_regime": "UNKNOWN",
            "failure_reason": MODULE.CSR.LOOKAHEAD_CONTAINED,
            "lookahead_violation": True,
            "source_dates_consulted": [],
        }
        cell = MODULE.market_observation(
            contained_view, kr_record, MODULE.declared_excluded_axes(embedded),
        )
        self.assertEqual(cell["outcome"], "BLOCKED")
        self.assertIs(cell["lookahead_contained"], True)
        self.assertIsNone(cell["candidate_regime"])
        self.assertIsNone(cell["candidate_classification_status"])
        for axis in MODULE.AXES:
            self.assertEqual(cell["axis_status"][axis], "NOT_ATTEMPTED_DATE_BLOCKED")
            self.assertIsNone(cell["axis_direction"][axis])

        report = MODULE.build_evidence(source)
        table = {"KR": {ANCHOR: cell}, "US": {ANCHOR: observation(report, "US", ANCHOR)}}
        facts = MODULE.unknown_facts(table, [ANCHOR])["markets"]["KR"]
        self.assertEqual(facts["lookahead_contained_date_count"], 1)
        self.assertEqual(
            facts["blocked_reason_codes"], {"COMBINED_LOOKAHEAD_VIOLATION": 1},
        )

    def test_a_forged_contained_market_view_never_reaches_this_module(self):
        # The counterpart to the projection test above: a population that claims
        # containment its embedded record does not support is refused by the
        # combined slice's own validator, so no fact here can be built on it.
        forged = copy.deepcopy(population([ANCHOR, PREVIOUS]))
        forged["records"][0]["markets"]["KR"].update({
            "outcome": "BLOCKED",
            "failure_reason": MODULE.CSR.LOOKAHEAD_CONTAINED,
            "lookahead_violation": True,
            "candidate_regime": None,
            "candidate_classification_status": None,
            "source_dates_consulted": [],
        })
        forged["records"][0]["combined_normalized_result"][
            "per_market_candidate_regime"
        ]["KR"] = None
        forged["records"][0]["combined_status"] = "SINGLE_MARKET_ONLY"
        forged["records"][0]["markets_by_outcome"] = {
            "OBSERVED": ["US"], "PARTIAL": [], "BLOCKED": ["KR"],
        }
        summary = forged["combined_summary"]
        summary["combined_status_counts"]["BOTH_MARKETS_REPLAYED"] -= 1
        summary["combined_status_counts"]["SINGLE_MARKET_ONLY"] += 1
        summary["per_market_outcome_counts"]["KR"]["OBSERVED"] -= 1
        summary["per_market_outcome_counts"]["KR"]["BLOCKED"] += 1
        forged["payload_sha256"] = MODULE.CSR.payload_sha256(
            {k: v for k, v in forged.items() if k != "payload_sha256"}
        )
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.build_evidence(forged)
        self.assertIn("SOURCE_POPULATION_INVALID", str(caught.exception))

    def test_validate_rejects_a_report_claiming_it_used_future_dates(self):
        # Adversarial, and the load-bearing case: the separation block was
        # previously carried unread, so a report could re-sign itself declaring
        # that future dates *were* used in a date's evaluation and still verify —
        # a valid signature over a claim that breaches the one boundary
        # docs/ATLAS_SESSION_BOOTSTRAP.md treats as non-negotiable.
        def at(report):
            return report["pit_and_audit_separation"]

        base = evidence([ANCHOR, PREVIOUS])
        for label, mutate in (
            (
                "future_dates_claimed",
                lambda r: at(r).__setitem__("future_dates_used_in_any_date_evaluation", True),
            ),
            (
                "own_record_denied",
                lambda r: at(r).__setitem__("each_date_summarized_from_its_own_record", False),
            ),
            (
                "cross_date_alteration_claimed",
                lambda r: at(r).__setitem__("no_date_observation_altered_by_another_date", False),
            ),
            (
                "source_mutated",
                lambda r: at(r).__setitem__("source_population_mutated_by_this_module", True),
            ),
            (
                "observations_recomputed",
                lambda r: at(r).__setitem__(
                    "market_observations_recomputed_by_this_module", True,
                ),
            ),
            (
                "candidate_rule_modified",
                lambda r: at(r).__setitem__("candidate_rule_modified_by_this_module", True),
            ),
            (
                "threshold_introduced",
                lambda r: at(r).__setitem__("threshold_introduced_by_this_module", True),
            ),
            (
                "hysteresis_applied",
                lambda r: at(r).__setitem__("hysteresis_applied_by_this_module", True),
            ),
            (
                "episode_selected",
                lambda r: at(r).__setitem__("episode_selected_by_this_module", True),
            ),
            (
                "truthy_not_true",
                lambda r: at(r).__setitem__("each_date_summarized_from_its_own_record", 1),
            ),
        ):
            with self.subTest(mutate=label):
                tampered = copy.deepcopy(base)
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn("PIT_SEPARATION_DECLARATION_INVALID", str(caught.exception))

    def test_validate_rejects_a_deleted_or_rewritten_separation_block(self):
        # Deleting a flag must fail rather than leave nothing to check, and the
        # prose and adjacency basis must survive too: a human reads those, not
        # the booleans, and requested-date adjacency relabelled as calendar
        # adjacency would misdescribe every transition count in the report.
        base = evidence([ANCHOR, PREVIOUS])
        for label, mutate, code in (
            (
                "dropped_flag",
                lambda r: r["pit_and_audit_separation"].pop(
                    "future_dates_used_in_any_date_evaluation",
                ),
                "PIT_SEPARATION_SCHEMA_INVALID",
            ),
            (
                "emptied",
                lambda r: r.__setitem__("pit_and_audit_separation", {}),
                "PIT_SEPARATION_SCHEMA_INVALID",
            ),
            (
                "deleted",
                lambda r: r.pop("pit_and_audit_separation"),
                "PIT_SEPARATION_SCHEMA_INVALID",
            ),
            (
                "extra_key",
                lambda r: r["pit_and_audit_separation"].__setitem__(
                    "future_dates_used_where_convenient", True,
                ),
                "PIT_SEPARATION_SCHEMA_INVALID",
            ),
            (
                "rewritten_statement",
                lambda r: r["pit_and_audit_separation"].__setitem__(
                    "statement", "Later dates were consulted where they helped.",
                ),
                "PIT_SEPARATION_STATEMENT_INVALID",
            ),
            (
                "relabelled_adjacency",
                lambda r: r["pit_and_audit_separation"].__setitem__(
                    "sequence_adjacency_basis", "CALENDAR_CONSECUTIVE_SESSIONS",
                ),
                "ADJACENCY_BASIS_INVALID",
            ),
        ):
            with self.subTest(mutate=label):
                tampered = copy.deepcopy(base)
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn(code, str(caught.exception))

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

    def test_validate_checks_the_source_pin_without_the_population(self):
        # Adversarial, and the whole point of this check: --verify holds only the
        # report. A source block that was deleted, relabelled NATURAL, pointed at
        # another module, or narrowed to a different date set must be refused on
        # the standalone path too — otherwise a re-signed report keeps claiming
        # its source is pinned while carrying no usable pin at all.
        def at(report):
            return report["source_population"]

        base = evidence([ANCHOR, PREVIOUS])
        for code, mutate in (
            ("SOURCE_POPULATION_SCHEMA_INVALID", lambda r: r.pop("source_population")),
            (
                "SOURCE_POPULATION_SCHEMA_INVALID",
                lambda r: at(r).pop("payload_sha256"),
            ),
            (
                "SOURCE_POPULATION_SHA_INVALID",
                lambda r: at(r).__setitem__("payload_sha256", None),
            ),
            (
                "SOURCE_POPULATION_SHA_INVALID",
                lambda r: at(r).__setitem__("payload_sha256", "not-a-sha256"),
            ),
            (
                "SOURCE_POPULATION_MODE_INVALID",
                lambda r: at(r).__setitem__("mode", "NATURAL"),
            ),
            (
                "SOURCE_POPULATION_MODE_INVALID",
                lambda r: at(r).__setitem__("evidence_class", "NATURAL_OBSERVATION"),
            ),
            (
                "SOURCE_POPULATION_SCHEMA_INVALID",
                lambda r: at(r).__setitem__("schema_version", "something_else/v1"),
            ),
            (
                "SOURCE_POPULATION_SCHEMA_INVALID",
                lambda r: at(r).__setitem__("source_module", "regime/elsewhere.py"),
            ),
            (
                "SOURCE_POPULATION_NOT_REVALIDATED_BY_ITS_OWNER",
                lambda r: at(r).__setitem__("revalidated_by_its_own_validator", False),
            ),
            (
                "SOURCE_POPULATION_SCOPE_INCONSISTENT",
                lambda r: at(r).__setitem__("requested_dates", [ANCHOR]),
            ),
            (
                "SOURCE_POPULATION_SCOPE_INCONSISTENT",
                lambda r: at(r)["market_population_available"].__setitem__("KR", False),
            ),
            (
                "SOURCE_POPULATION_SCHEMA_INVALID",
                lambda r: at(r)["market_population_available"].pop("US"),
            ),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(base)
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_still_pins_the_source_digest_it_cannot_re_derive(self):
        # Standalone verification cannot prove the digest names this population —
        # only --verify-against can, and it is retained. What it does prove is
        # that a syntactically valid pin is present and matches the scope the
        # facts were derived over.
        source = population([ANCHOR, PREVIOUS])
        report = MODULE.build_evidence(source)
        MODULE.validate_evidence(copy.deepcopy(report))
        forged = copy.deepcopy(report)
        forged["source_population"]["payload_sha256"] = "0" * 64
        # Accepted standalone (the digest is well-formed and nothing on hand can
        # contradict it) and refused as soon as the population is supplied.
        MODULE.validate_evidence(resigned(copy.deepcopy(forged)))
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(forged), population=source)
        self.assertIn("EVIDENCE_REDERIVATION_MISMATCH", str(caught.exception))

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

    def test_validate_rejects_an_emptied_or_short_observation_table(self):
        # Adversarial: re-signing a report whose observations were dropped must
        # not pass. Every requested date needs an observation, or the counts and
        # sequences below describe a replay the report no longer contains.
        for code, mutate in (
            (
                "OBSERVATIONS_NOT_BIJECTIVE_OVER_REQUESTED_DATES",
                lambda report: report["per_date_observations"].__setitem__("KR", {}),
            ),
            (
                "OBSERVATIONS_NOT_BIJECTIVE_OVER_REQUESTED_DATES",
                lambda report: report["per_date_observations"]["US"].pop(ANCHOR),
            ),
            (
                "PER_DATE_OBSERVATIONS_INVALID",
                lambda report: report["per_date_observations"].pop("US"),
            ),
        ):
            with self.subTest(code=code, mutate=mutate):
                tampered = copy.deepcopy(evidence([ANCHOR, PREVIOUS]))
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn(code, str(caught.exception))

    def test_validate_rejects_facts_that_do_not_follow_from_the_observations(self):
        # Every fact family must reproduce from the report's own published
        # observations, so a count cannot be edited into agreement with itself.
        for section, mutate in (
            (
                "stress_detection_facts",
                lambda report: report["stress_detection_facts"]["markets"]["US"].update(
                    {"dates_with_any_stress_axis": 4}
                ),
            ),
            (
                "unknown_facts",
                lambda report: report["unknown_facts"]["markets"]["KR"].update(
                    {"blocked_date_count": 7}
                ),
            ),
            (
                "hysteresis_facts",
                lambda report: report["hysteresis_facts"]["markets"]["KR"][
                    "candidate_regime"
                ].update({"run_count": 99}),
            ),
        ):
            with self.subTest(section=section):
                tampered = copy.deepcopy(evidence([ANCHOR, PREVIOUS]))
                mutate(tampered)
                with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
                    MODULE.validate_evidence(resigned(tampered))
                self.assertIn("INCONSISTENT", str(caught.exception))

    def test_validate_rejects_a_direction_on_an_unobserved_axis(self):
        tampered = copy.deepcopy(evidence([ANCHOR]))
        observation(tampered, "US", ANCHOR)["axis_direction"]["BREADTH"] = "POSITIVE"
        with self.assertRaises(MODULE.ReplayEvidenceError) as caught:
            MODULE.validate_evidence(resigned(tampered))
        self.assertIn(
            "UNOBSERVED_AXIS_MUST_NOT_CARRY_A_DIRECTION", str(caught.exception),
        )

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
