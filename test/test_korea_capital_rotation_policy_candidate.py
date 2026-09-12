#!/usr/bin/env python3
"""P2-03 durable sector identity binding + rotation-policy candidate lane
regression.

Supersedes this lane's first attempt (theme_taxonomy/2 graph-backed
candidate, 2026-09-11): that candidate was pinned to one as_of_date and could
not be reused across sessions without a new graph/packet/hash every trading
day. This regression instead proves the CIO-mandated durability property
directly against real, unmodified production code:

- Two genuine Leadership observation packets, Day N and Day N+1, built via
  the real, unmodified `.github/scripts/korea_leadership.py::build_transform()`
  against the real, committed, already-RATIFIED
  `config/korea_leadership_policy.json` (not a synthetic fixture policy).
- The SAME committed binding/policy bytes, unchanged, validated successfully
  by the real, unmodified `rotation/korea_capital_rotation.py::build_packet()`
  against both days -- no rebuild, no new hash, no date-literal edit.
- A fabricated/missing sector identity and an upstream Leadership policy SHA
  drift both fail closed against real code, not a reimplementation of the
  rules.
- The candidate never reuses the tainted 2026-08-22T07:19:09Z timestamp, the
  all-zero taxonomy placeholders, or the 2026-09-11 date this lane's first,
  superseded attempt used.
- The real P2-01 authority registry stays untouched (0 records); this lane
  never uses the theme_taxonomy/2 graph producer at all.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
import json
import tempfile
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SCRIPT = ROOT / "rotation" / "korea_capital_rotation_policy_candidate.py"
KCR_SCRIPT = ROOT / "rotation" / "korea_capital_rotation.py"
KL_SCRIPT = ROOT / ".github" / "scripts" / "korea_leadership.py"
TTA_SCRIPT = ROOT / "rotation" / "theme_taxonomy_authority.py"
LEADERSHIP_PROOF_SCRIPT = ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py"
LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAND = load_module("korea_capital_rotation_policy_candidate", CANDIDATE_SCRIPT)
KCR = load_module("korea_capital_rotation_for_candidate_test", KCR_SCRIPT)
KL = load_module("korea_leadership_for_candidate_test", KL_SCRIPT)
TTA = load_module("theme_taxonomy_authority_for_candidate_test", TTA_SCRIPT)

TAINTED_RATIFIED_AT_UTC = "2026-08-22T07:19:09Z"
TAINTED_TAXONOMY_ID = "TAXONOMY.NOT_RATIFIED"
TAINTED_DECISION_ID = "DECISION.NOT_RATIFIED"
TAINTED_SHA = "0" * 64
SUPERSEDED_CANDIDATE_DATE = "2026-09-11"

ALL_DOCS_LOADERS = (
    CAND.load_committed_rationale,
    CAND.load_committed_document,
    CAND.load_committed_binding,
    CAND.load_committed_policy,
)

EMPTY_BREADTH_MARKET = {
    "lineage_sha256": None, "as_of_date": None, "source_available_at": None,
    "captured_at": None, "first_seen_at": None, "capture_mode": None,
}
COVERAGE_CONTEXT = {
    "breadth": {
        "status": "UNKNOWN",
        "markets": {"KOSDAQ": dict(EMPTY_BREADTH_MARKET), "KOSPI": dict(EMPTY_BREADTH_MARKET)},
        "freshness_limit_days": 3,
        "ranking_input_authorized": False,
        "decision_eligible": False,
    },
    "investor_flow": {
        "status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
        "market_venue_scope": "KRX_ONLY",
        "nxt_included": False,
        "whole_korea_market_claim_authorized": False,
        "source_release_time_status": "unverified",
        "available_at": None,
        "decision_eligible": False,
        "ranking_input_authorized": False,
    },
}


def _all_real_sector_identities():
    policy = json.loads(LEADERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
    return sorted(
        record["series_identity"] for record in policy["records"]
        if record.get("role") == "SECTOR"
    )


def _all_real_leadership_identities():
    """SECTOR + BENCHMARK identities -- the full set a real Leadership
    observation must cover (korea_leadership.py::build_transform() requires
    every active policy record, not just SECTOR rows)."""
    policy = json.loads(LEADERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
    return sorted(record["series_identity"] for record in policy["records"])


def _leadership_payload(prev_date, obs_date, identities):
    policy = json.loads(LEADERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
    return {
        "schema_version": 1,
        "source_name": policy["source_name"],
        "market": policy["market"],
        "market_timezone": policy["market_timezone"],
        "run_mode": "FORWARD_SHADOW",
        "observation_date": obs_date,
        "fetched_at": f"{obs_date}T18:05:00+09:00",
        "available_at": f"{obs_date}T18:00:00+09:00",
        "decision_at": f"{obs_date}T18:10:00+09:00",
        "expected_session_dates": [prev_date, obs_date],
        "series_rows": [
            {
                "series_identity": identity,
                "rows": [
                    {"session_date": prev_date, "close": "100"},
                    {"session_date": obs_date, "close": "101"},
                ],
            }
            for identity in identities
        ],
    }


def build_real_leadership_packet(prev_date, obs_date, *, policy_path=LEADERSHIP_POLICY_PATH, identities=None):
    """A genuine Leadership observation, built by the real, unmodified
    korea_leadership.py::build_transform() against a real committed (or, for
    the drift regression, a deliberately mutated copy of the) Leadership
    policy file -- never a synthetic look-alike."""
    payload = _leadership_payload(prev_date, obs_date, identities or _all_real_leadership_identities())
    return KL.build_transform(payload, policy_path=policy_path)


def build_rotation_packet(as_of_date, prior, current, *, binding=None, policy=None):
    value = {
        "schema_version": "korea_capital_rotation_input/1",
        "as_of_date": as_of_date,
        "taxonomy_binding": copy.deepcopy(binding if binding is not None else CAND.load_committed_binding()),
        "coverage_context": copy.deepcopy(COVERAGE_CONTEXT),
        "prior_observation": prior,
        "current_observation": current,
    }
    return KCR.build_packet(value, copy.deepcopy(policy if policy is not None else CAND.load_committed_policy()))


class CommittedVsRebuiltTests(unittest.TestCase):
    def test_all_four_documents_byte_identical_to_rebuild(self):
        rationale, document, binding, policy = CAND.build_all()
        self.assertEqual(rationale, CAND.load_committed_rationale())
        self.assertEqual(document, CAND.load_committed_document())
        self.assertEqual(binding, CAND.load_committed_binding())
        self.assertEqual(policy, CAND.load_committed_policy())

    def test_rebuild_is_deterministic(self):
        self.assertEqual(CAND.build_all(), CAND.build_all())

    def test_identity_document_carries_no_as_of_date_field(self):
        # The whole point of this correction: nothing in the durable identity
        # document is decision-date-specific.
        document = CAND.load_committed_document()
        self.assertNotIn("as_of_date", document)
        self.assertNotIn("decision_date", document)


class CiOCandidateDirectionTests(unittest.TestCase):
    def test_policy_matches_revised_cio_direction(self):
        policy = CAND.load_committed_policy()
        self.assertEqual(policy["ranking_metric"], "RELATIVE_STRENGTH_VS_OWN_BENCHMARK")
        self.assertEqual(policy["ranking_order"], "DESCENDING_WITHIN_BENCHMARK_SCOPE")
        self.assertEqual(policy["tie_break"], "SERIES_IDENTITY_ASC")
        self.assertEqual(policy["maximum_calendar_gap_days"], 7)
        for scope in policy["benchmark_scopes"]:
            self.assertEqual(scope["top_count"], 3)
            self.assertEqual(scope["bottom_count"], 3)

    def test_covers_real_46_sector_identities(self):
        policy = CAND.load_committed_policy()
        all_series = sorted(
            member["series_identity"]
            for scope in policy["benchmark_scopes"]
            for member in scope["members"]
        )
        self.assertEqual(all_series, _all_real_sector_identities())
        self.assertEqual(len(all_series), 46)


class NeverRatifiedRegressionTests(unittest.TestCase):
    def test_no_document_claims_ratified_by_or_at(self):
        for loader in ALL_DOCS_LOADERS:
            document = loader()
            self.assertIsNone(document.get("ratified_by"), loader.__name__)
            self.assertIsNone(document.get("ratified_at_utc"), loader.__name__)

    def test_policy_approval_status_is_unratified(self):
        self.assertEqual(CAND.load_committed_policy()["approval_status"], "UNRATIFIED")

    def test_never_reuses_tainted_timestamp_as_a_real_field(self):
        for loader in ALL_DOCS_LOADERS:
            document = loader()
            self.assertNotEqual(document.get("ratified_at_utc"), TAINTED_RATIFIED_AT_UTC)

    def test_never_reuses_all_zero_taxonomy_placeholder(self):
        for loader in ALL_DOCS_LOADERS:
            document = loader()
            for key in ("taxonomy_decision_sha256", "taxonomy_packet_sha256"):
                if key in document:
                    self.assertNotEqual(document[key], TAINTED_SHA, f"{loader.__name__}:{key}")

    def test_never_reuses_not_ratified_placeholder_tokens(self):
        for loader in ALL_DOCS_LOADERS:
            text = str(loader())
            self.assertNotIn(TAINTED_TAXONOMY_ID, text, loader.__name__)
            self.assertNotIn(TAINTED_DECISION_ID, text, loader.__name__)

    def test_never_reuses_the_superseded_candidates_authoring_date_as_a_real_field(self):
        for loader in ALL_DOCS_LOADERS:
            document = loader()
            self.assertNotEqual(document.get("effective_from"), SUPERSEDED_CANDIDATE_DATE)
            self.assertNotEqual(document.get("proposed_effective_from"), SUPERSEDED_CANDIDATE_DATE)

    def test_proof_script_still_contains_the_tainted_values_unmodified(self):
        text = LEADERSHIP_PROOF_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(TAINTED_RATIFIED_AT_UTC, text)
        self.assertIn(TAINTED_TAXONOMY_ID, text)


class RealAuthorityRegistryUntouchedTests(unittest.TestCase):
    """This lane must never populate the real P2-01 authority registry, and
    must never use the theme_taxonomy/2 graph producer at all -- that graph
    approach is exactly what this correction replaces."""

    def test_registry_still_has_zero_records(self):
        registry = TTA.load_registry()
        self.assertEqual(registry["records"], [])

    def test_binding_does_not_use_the_p2_01_producer_contract_version(self):
        binding = CAND.load_committed_binding()
        contract = KCR.load_contract()
        self.assertNotEqual(
            binding["taxonomy_contract_version"], KCR.taxonomy_producer_contract_version()
        )
        self.assertNotEqual(
            binding["taxonomy_contract_version"], contract["taxonomy_contract_version"]
        )
        self.assertEqual(
            binding["taxonomy_contract_version"],
            KCR.sector_identity_binding_contract_version(),
        )


class RealProducerAcceptanceTests(unittest.TestCase):
    """Every claim here is proven against the real, unmodified
    korea_capital_rotation.py / korea_leadership.py functions."""

    @classmethod
    def setUpClass(cls):
        cls.contract = KCR.load_contract()
        cls.rationale, cls.document, cls.binding, cls.policy = CAND.build_all()

    def test_binding_accepted_by_real_validate_binding(self):
        validated = KCR._validate_binding(self.binding, self.contract, derived=False)
        self.assertEqual(validated, self.binding)

    def test_upstream_leadership_policy_sha_independently_recomputed(self):
        real_sha = CAND.file_sha256(LEADERSHIP_POLICY_PATH)
        self.assertEqual(self.binding["upstream_leadership_policy_sha256"], real_sha)
        self.assertEqual(self.policy["upstream_leadership_policy_sha256"], real_sha)

    def _eligible(self):
        return {
            identity: {"benchmark_identity": benchmark, "role": "SECTOR"}
            for prefix, benchmark, members in CAND._sector_scopes()
            for identity in members
        }

    def test_policy_stays_inert_regardless_of_dates(self):
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        eligible = self._eligible()
        _, effective, _ = KCR._validate_policy(
            self.policy, validated_binding, eligible,
            dt.date(2026, 9, 10), dt.date(2026, 9, 11),
            dt.datetime(2026, 9, 10, 10, 0, tzinfo=dt.timezone.utc),
        )
        self.assertFalse(effective)

    def test_consume_taxonomy_never_enters_the_v2_graph_path(self):
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        derived, active_theme_ids = KCR._consume_taxonomy(
            validated_binding, None, dt.date(2026, 9, 11), None, None
        )
        self.assertEqual(derived, {})
        self.assertIsNone(active_theme_ids)


class DurabilityAcrossNaturalSessionsTests(unittest.TestCase):
    """The CIO-mandated regression: build the identity binding + UNRATIFIED
    policy once, then validate the exact same committed bytes against a real
    Day N observation pair and a real, later Day N+1 observation pair -- no
    rebuild, no new hash, no date-literal edit."""

    @classmethod
    def setUpClass(cls):
        cls.binding = CAND.load_committed_binding()
        cls.policy = CAND.load_committed_policy()
        cls.day_n_prior = build_real_leadership_packet("2026-09-07", "2026-09-08")
        cls.day_n_current = build_real_leadership_packet("2026-09-08", "2026-09-09")
        cls.day_n1_current = build_real_leadership_packet("2026-09-09", "2026-09-10")

    def test_real_leadership_packets_bind_to_the_real_committed_policy_file(self):
        real_sha = CAND.file_sha256(LEADERSHIP_POLICY_PATH)
        self.assertEqual(self.day_n_prior["policy"]["policy_sha256"], real_sha)
        self.assertEqual(self.day_n_current["policy"]["policy_sha256"], real_sha)
        self.assertEqual(self.day_n1_current["policy"]["policy_sha256"], real_sha)

    def test_same_binding_and_policy_bytes_validate_day_n(self):
        packet = build_rotation_packet(
            "2026-09-09", self.day_n_prior, self.day_n_current,
            binding=self.binding, policy=self.policy,
        )
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertEqual(packet["taxonomy_binding"], self.binding)
        self.assertEqual(packet["rotation_policy"], self.policy)

    def test_same_binding_and_policy_bytes_validate_day_n_plus_1_unchanged(self):
        # Day N's own "current" observation becomes Day N+1's "prior" --
        # a genuine rolling window, exactly as natural daily operation would
        # produce it. Crucially: self.binding / self.policy are passed
        # through completely unchanged from the Day N call above.
        packet = build_rotation_packet(
            "2026-09-10", self.day_n_current, self.day_n1_current,
            binding=self.binding, policy=self.policy,
        )
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertEqual(packet["taxonomy_binding"], self.binding)
        self.assertEqual(packet["rotation_policy"], self.policy)

    def test_binding_and_policy_identical_across_both_days(self):
        # Byte-identical reuse is the entire point of this correction.
        packet_n = build_rotation_packet(
            "2026-09-09", self.day_n_prior, self.day_n_current,
            binding=self.binding, policy=self.policy,
        )
        packet_n1 = build_rotation_packet(
            "2026-09-10", self.day_n_current, self.day_n1_current,
            binding=self.binding, policy=self.policy,
        )
        self.assertEqual(packet_n["taxonomy_binding"], packet_n1["taxonomy_binding"])
        self.assertEqual(packet_n["rotation_policy"], packet_n1["rotation_policy"])
        self.assertEqual(
            packet_n["taxonomy_binding"]["taxonomy_packet_sha256"],
            packet_n1["taxonomy_binding"]["taxonomy_packet_sha256"],
        )

    def test_fabricated_series_identity_fails_closed(self):
        bad_policy = copy.deepcopy(self.policy)
        bad_policy["benchmark_scopes"][0]["members"][0]["series_identity"] = (
            "KOSDAQ::FABRICATED_NOT_REAL"
        )
        with self.assertRaises(KCR.KoreaCapitalRotationError):
            build_rotation_packet(
                "2026-09-09", self.day_n_prior, self.day_n_current,
                binding=self.binding, policy=bad_policy,
            )

    def test_missing_series_identity_fails_closed_at_leadership_stage(self):
        identities = _all_real_leadership_identities()
        with self.assertRaises(KL.KoreaLeadershipError):
            build_real_leadership_packet(
                "2026-09-07", "2026-09-08", identities=identities[1:]
            )

    def test_upstream_leadership_policy_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            mutated_path = Path(tmp) / "mutated-korea-leadership-policy.json"
            mutated = json.loads(LEADERSHIP_POLICY_PATH.read_text(encoding="utf-8"))
            mutated["policy_version"] = mutated["policy_version"] + "-MUTATED-FOR-TEST"
            mutated_path.write_text(
                json.dumps(mutated, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            drifted_prior = build_real_leadership_packet(
                "2026-09-07", "2026-09-08", policy_path=mutated_path
            )
            drifted_current = build_real_leadership_packet(
                "2026-09-08", "2026-09-09", policy_path=mutated_path
            )
            self.assertNotEqual(
                drifted_current["policy"]["policy_sha256"],
                self.binding["upstream_leadership_policy_sha256"],
            )
            with self.assertRaisesRegex(
                KCR.KoreaCapitalRotationError, "UPSTREAM_POLICY_BINDING_MISMATCH"
            ):
                build_rotation_packet(
                    "2026-09-09", drifted_prior, drifted_current,
                    binding=self.binding, policy=self.policy,
                )


class ScopeNoteHonestyTests(unittest.TestCase):
    def test_scope_note_disclaims_p2_01_and_the_superseded_graph_approach(self):
        note = CAND.load_committed_rationale().get("scope_note", "")
        self.assertIn("NOT the P2-01 cross-market", note)
        self.assertIn("#576", note)
        self.assertIn("as_of_date", note)


if __name__ == "__main__":
    unittest.main()
