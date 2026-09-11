#!/usr/bin/env python3
"""P2-03 rotation-policy canonicalization-only candidate lane regression.

Covers: committed-vs-rebuilt byte-identical for all five candidate documents,
real-code acceptance by the unmodified `rotation/korea_capital_rotation.py`
(`_validate_binding`, `_validate_policy`, `_consume_taxonomy`,
`_assert_theme_nodes`) and the unmodified `rotation/theme_taxonomy.py` /
`rotation/theme_taxonomy_authority.py` (real `build_packet()` /
`resolve_graph_authority()` -- not a reimplementation of their rules), the
honest inert outcome (`effective=False`, `graph_currently_effective=False`,
`AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD`), the real P2-01 authority
registry staying untouched (0 records), and a regression against ever
reintroducing the tainted 2026-08-22T07:19:09Z self-declared ratification
timestamp or the all-zero taxonomy placeholders this lane exists to replace.
"""
from __future__ import annotations

import copy
import datetime as dt
import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_SCRIPT = ROOT / "rotation" / "korea_capital_rotation_policy_candidate.py"
KCR_SCRIPT = ROOT / "rotation" / "korea_capital_rotation.py"
TT_SCRIPT = ROOT / "rotation" / "theme_taxonomy.py"
TTA_SCRIPT = ROOT / "rotation" / "theme_taxonomy_authority.py"
LEADERSHIP_PROOF_SCRIPT = ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CAND = load_module("korea_capital_rotation_policy_candidate", CANDIDATE_SCRIPT)
KCR = load_module("korea_capital_rotation_for_candidate_test", KCR_SCRIPT)
TT = load_module("theme_taxonomy_for_candidate_test", TT_SCRIPT)
TTA = load_module("theme_taxonomy_authority_for_candidate_test", TTA_SCRIPT)

TAINTED_RATIFIED_AT_UTC = "2026-08-22T07:19:09Z"
TAINTED_TAXONOMY_ID = "TAXONOMY.NOT_RATIFIED"
TAINTED_DECISION_ID = "DECISION.NOT_RATIFIED"
TAINTED_SHA = "0" * 64

ALL_DOCS_LOADERS = (
    CAND.load_committed_rationale,
    CAND.load_committed_graph,
    CAND.load_committed_packet,
    CAND.load_committed_binding,
    CAND.load_committed_policy,
)


class CommittedVsRebuiltTests(unittest.TestCase):
    def test_all_five_documents_byte_identical_to_rebuild(self):
        rationale, graph, packet, binding, policy = CAND.build_all()
        self.assertEqual(rationale, CAND.load_committed_rationale())
        self.assertEqual(graph, CAND.load_committed_graph())
        self.assertEqual(packet, CAND.load_committed_packet())
        self.assertEqual(binding, CAND.load_committed_binding())
        self.assertEqual(policy, CAND.load_committed_policy())

    def test_rebuild_is_deterministic(self):
        first = CAND.build_all()
        second = CAND.build_all()
        self.assertEqual(first, second)


class NeverRatifiedRegressionTests(unittest.TestCase):
    """No document in this lane may claim ratification, and none may reuse
    the tainted values this lane exists to replace."""

    def test_no_document_claims_ratified_by_or_at(self):
        for loader in ALL_DOCS_LOADERS:
            document = loader()
            self.assertIsNone(document.get("ratified_by"), loader.__name__)
            self.assertIsNone(document.get("ratified_at_utc"), loader.__name__)
            if "approval" in document:
                self.assertEqual(document["approval"].get("ratified_by"), None)
                self.assertEqual(document["approval"].get("ratified_at_utc"), None)
                self.assertEqual(document["approval"]["approval_status"], "UNRATIFIED")

    def test_policy_approval_status_is_unratified(self):
        policy = CAND.load_committed_policy()
        self.assertEqual(policy["approval_status"], "UNRATIFIED")

    def test_never_reuses_tainted_timestamp_as_a_real_field(self):
        # scope_note prose is allowed to *mention* the tainted timestamp as a
        # disclaimer (it names exactly what this lane must not reuse) -- this
        # checks the actual data fields, not free-text explanation.
        for loader in ALL_DOCS_LOADERS:
            document = loader()
            self.assertNotEqual(document.get("ratified_at_utc"), TAINTED_RATIFIED_AT_UTC)
            approval = document.get("approval")
            if isinstance(approval, dict):
                self.assertNotEqual(approval.get("ratified_at_utc"), TAINTED_RATIFIED_AT_UTC)

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

    def test_proof_script_still_contains_the_tainted_values_unmodified(self):
        # Confirms this lane did not edit the old proof script -- it is left
        # exactly as Phase A found it, superseded by this candidate lane, not
        # silently rewritten.
        text = LEADERSHIP_PROOF_SCRIPT.read_text(encoding="utf-8")
        self.assertIn(TAINTED_RATIFIED_AT_UTC, text)
        self.assertIn(TAINTED_TAXONOMY_ID, text)


class RealAuthorityRegistryUntouchedTests(unittest.TestCase):
    """This lane must never populate the real P2-01 authority registry --
    that remains PR #576's separate, owned scope."""

    def test_registry_still_has_zero_records(self):
        registry = TTA.load_registry()
        self.assertEqual(registry["records"], [])

    def test_authority_resolution_is_honestly_no_record(self):
        packet = CAND.load_committed_packet()
        self.assertEqual(
            packet["authority_resolution"]["status"],
            "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD",
        )
        for value in packet["authority_resolution"]["authority"].values():
            self.assertFalse(value)


class RealProducerAcceptanceTests(unittest.TestCase):
    """Every claim in this lane is proven against the real, unmodified
    theme_taxonomy.py / theme_taxonomy_authority.py / korea_capital_
    rotation.py functions -- never a reimplementation of their rules."""

    @classmethod
    def setUpClass(cls):
        cls.contract = KCR.load_contract()
        cls.rationale, cls.graph, cls.packet, cls.binding, cls.policy = CAND.build_all()

    def test_graph_accepted_by_real_theme_taxonomy_producer(self):
        rebuilt_packet = TT.build_packet(self.graph)
        self.assertEqual(rebuilt_packet, self.packet)

    def test_graph_is_honestly_inert(self):
        self.assertEqual(self.packet["graph_status"], "DRAFT_OR_NOT_EFFECTIVE_GRAPH")
        self.assertFalse(self.packet["structurally_eligible_ratification_claim"])
        self.assertFalse(self.packet["theme_membership_authorized"])
        self.assertEqual(self.packet["global_asset_master_membership_adapter"], [])
        for key, value in self.packet["authority"].items():
            if key == "external_ratification_claim_validation_only":
                # Meta-flag describing the module's own boundary, not a grant.
                self.assertTrue(value)
                continue
            self.assertFalse(value, key)

    def test_graph_covers_real_46_sector_nodes(self):
        self.assertEqual(self.packet["node_count"], 46)
        self.assertEqual(self.packet["active_node_count"], 46)
        prefixes = {node["theme_id"].split(".", 1)[0] for node in self.graph["nodes"]}
        self.assertEqual(prefixes, {"KOSPI", "KOSDAQ"})
        kospi = [n for n in self.graph["nodes"] if n["theme_id"].startswith("KOSPI")]
        kosdaq = [n for n in self.graph["nodes"] if n["theme_id"].startswith("KOSDAQ")]
        self.assertEqual(len(kospi), 24)
        self.assertEqual(len(kosdaq), 22)

    def test_binding_accepted_by_real_validate_binding(self):
        validated = KCR._validate_binding(self.binding, self.contract, derived=False)
        self.assertEqual(validated, self.binding)

    def test_upstream_leadership_policy_sha_independently_recomputed(self):
        real_sha = CAND.file_sha256(CAND.LEADERSHIP_POLICY_PATH)
        self.assertEqual(self.binding["upstream_leadership_policy_sha256"], real_sha)
        self.assertEqual(self.policy["upstream_leadership_policy_sha256"], real_sha)

    def _eligible_from_real_leadership_policy(self):
        eligible = {}
        for prefix, benchmark, members in CAND._sector_scopes():
            for identity in members:
                eligible[identity] = {"benchmark_identity": benchmark, "role": "SECTOR"}
        return eligible

    def test_policy_accepted_by_real_validate_policy_and_stays_inert(self):
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        eligible = self._eligible_from_real_leadership_policy()
        prior_date = dt.date(2026, 9, 10)
        current_date = dt.date(2026, 9, 11)
        prior_available_at = dt.datetime(2026, 9, 10, 10, 0, tzinfo=dt.timezone.utc)
        checked_policy, effective, scopes = KCR._validate_policy(
            self.policy, validated_binding, eligible,
            prior_date, current_date, prior_available_at,
        )
        self.assertFalse(effective)
        self.assertEqual(checked_policy, self.policy)
        self.assertEqual(len(scopes), 2)

    def test_real_taxonomy_consumption_and_referential_integrity(self):
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        graph_bytes = CAND.GRAPH_PATH.read_bytes()
        current_date = dt.date(2026, 9, 11)
        derived, active_theme_ids = KCR._consume_taxonomy(
            validated_binding, graph_bytes, current_date, None, None,
        )
        self.assertFalse(derived["theme_membership_authorized"])
        self.assertEqual(len(active_theme_ids), 46)
        eligible = self._eligible_from_real_leadership_policy()
        _, effective, scopes = KCR._validate_policy(
            self.policy, validated_binding, eligible,
            dt.date(2026, 9, 10), current_date,
            dt.datetime(2026, 9, 10, 10, 0, tzinfo=dt.timezone.utc),
        )
        all_theme_ids = {
            theme_id for scope in scopes.values() for theme_id in scope["series_to_theme"].values()
        }
        # Must not raise -- every policy-declared theme_id is a real active
        # node in the real consumed graph.
        KCR._assert_theme_nodes(all_theme_ids, active_theme_ids)

    def test_referential_integrity_actually_catches_a_missing_node(self):
        validated_binding = KCR._validate_binding(self.binding, self.contract, derived=False)
        with self.assertRaises(KCR.KoreaCapitalRotationError):
            KCR._assert_theme_nodes({"KOSPI.SECTOR.99"}, {"KOSPI.SECTOR.01"})


class ScopeNoteHonestyTests(unittest.TestCase):
    def test_scope_note_disclaims_p2_01_cross_market_taxonomy(self):
        # The graph document's fields are fixed exactly by theme_taxonomy_
        # input/1 (no extra key allowed) -- the scope_note lives on the
        # rationale document its approval.decision_sha256 traces back to.
        note = CAND.load_committed_rationale().get("scope_note", "")
        self.assertIn("NOT the P2-01 cross-market", note)
        self.assertIn("#576", note)

    def test_documents_do_not_claim_production_or_trading_authority(self):
        binding_decision_authority = CAND.load_committed_rationale()
        self.assertIn("candidate_status", binding_decision_authority)
        self.assertEqual(
            binding_decision_authority["candidate_status"], "UNRATIFIED_CANDIDATE"
        )


if __name__ == "__main__":
    unittest.main()
