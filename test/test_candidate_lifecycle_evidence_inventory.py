#!/usr/bin/env python3
"""P8-12 empirical Candidate Lifecycle inventory regressions."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from clock.candidate_lifecycle_evidence_inventory import (
    CandidateLifecycleEvidenceInventoryError,
    _build_from_validated,
    build_inventory,
    validate_inventory,
)
from clock.candidate_lifecycle_observation import (
    BASELINE_PREEXISTING,
    CHAIN_MANUAL,
    CHAIN_NATURAL,
    CONTINUING_CHANGED,
    CONTINUING_UNCHANGED,
    FIRST_ABSENCE,
    MANUAL_SAMPLE,
    NATURAL_SAMPLE,
    REAPPEARED,
)
from clock.review_candidate import AUTHORITY_ALL_FALSE
from replay.opportunity_trigger import payload_sha256


def key(subject: str, market: str) -> str:
    return payload_sha256({"market": market, "subject": subject})


def record(subject: str, market: str, event: str, *, active: bool = True) -> dict:
    return {
        "stable_candidate_key": key(subject, market),
        "subject": subject,
        "market": market,
        "state": "ACTIVE" if active else "ABSENT_OBSERVED",
        "lifecycle_event": event,
        "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
    }


def source(subject: str, market: str, *triggers: str) -> dict[str, dict]:
    return {
        key(subject, market): {
            "subject": subject,
            "market": market,
            "trigger_types": list(triggers),
        }
    }


class ArtifactFactory:
    def __init__(self, root: Path):
        self.root = root

    def artifact(
        self,
        at: str,
        rows: list[dict],
        source_map: dict[str, dict],
        *,
        prior: tuple[Path, dict, dict] | None = None,
        qualification: str = NATURAL_SAMPLE,
        distinct: bool | None = None,
        nonce: str = "",
    ) -> tuple[Path, dict, dict]:
        natural = qualification == NATURAL_SAMPLE
        if prior is None:
            prior_ref = None
            basis = "BASELINE_EVIDENCE_BASIS"
        else:
            prior_path, prior_doc, _ = prior
            prior_ref = {
                "path": prior_path.relative_to(self.root).as_posix(),
                "lifecycle_observation_sha256": prior_doc["lifecycle_observation_sha256"],
                "operational_evaluated_at_utc": prior_doc["operational_evaluated_at_utc"],
            }
            basis = (
                "DISTINCT_EVIDENCE_BASIS"
                if distinct else "DUPLICATE_EVIDENCE_BASIS_EVALUATION_ONLY"
            )
        document = {
            "contract_version": "candidate_lifecycle_shadow_observation/1",
            "observation_date": at[:10],
            "operational_evaluated_at_utc": at,
            "sample_qualification": qualification,
            "chain_qualification": CHAIN_NATURAL if natural else CHAIN_MANUAL,
            "chain_advancing": natural,
            "prior_lifecycle": prior_ref,
            "evidence_basis_status": basis,
            "distinct_evidence_basis_from_previous": distinct,
            "state_counts": {
                "total": len(rows),
                "active": sum(row["state"] == "ACTIVE" for row in rows),
                "absent_observed": sum(row["state"] == "ABSENT_OBSERVED" for row in rows),
            },
            "state_records": rows,
            "nonce": nonce,
            "authority": copy.deepcopy(AUTHORITY_ALL_FALSE),
        }
        document["lifecycle_observation_sha256"] = payload_sha256(document)
        path = (
            self.root
            / "candidate_lifecycle_observations"
            / at[:10]
            / f"lifecycle-{document['lifecycle_observation_sha256']}.json"
        )
        return path, document, source_map


class EmpiricalAggregationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.factory = ArtifactFactory(self.root)

    def baseline(self):
        return self.factory.artifact(
            "2026-08-25T10:00:00Z",
            [record("BTC", "BTC", BASELINE_PREEXISTING)],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
        )

    def test_baseline_only_never_invents_a_validity_window(self):
        inventory = _build_from_validated([self.baseline()], self.root)
        self.assertEqual(inventory["evidence_status"], "NATURAL_BASELINE_ONLY_NO_TRANSITION")
        self.assertEqual(inventory["natural_forward_chain"]["transition_count"], 0)
        self.assertIsNone(inventory["policy_boundary"]["minimum_sample_threshold"])
        self.assertIsNone(inventory["policy_boundary"]["validity_window_days"])
        self.assertFalse(inventory["policy_boundary"]["candidate_freshness_evaluated"])

    def test_unchanged_endpoint_transition_is_counted_without_claiming_continuity(self):
        baseline = self.baseline()
        current = self.factory.artifact(
            "2026-08-25T10:05:00Z",
            [record("BTC", "BTC", CONTINUING_UNCHANGED)],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
            prior=baseline,
            distinct=True,
        )
        inventory = _build_from_validated([baseline, current], self.root)
        chain = inventory["natural_forward_chain"]
        self.assertEqual(chain["observation_span_seconds"], 300)
        self.assertEqual(chain["distinct_evidence_transition_count"], 1)
        self.assertEqual(chain["distinct_evidence_transition_event_counts"][CONTINUING_UNCHANGED], 1)
        self.assertEqual(
            chain["observation_span_interpretation"],
            "ENDPOINT_OBSERVATION_SPAN_NOT_CONTINUOUS_CANDIDATE_LIFETIME",
        )
        self.assertFalse(inventory["policy_boundary"]["continuous_presence_assumed_between_observations"])

    def test_evaluation_only_duplicate_is_separated_from_distinct_evidence(self):
        baseline = self.baseline()
        duplicate = self.factory.artifact(
            "2026-08-25T10:01:00Z",
            [record("BTC", "BTC", CONTINUING_UNCHANGED)],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
            prior=baseline,
            distinct=False,
        )
        inventory = _build_from_validated([baseline, duplicate], self.root)
        chain = inventory["natural_forward_chain"]
        self.assertEqual(chain["evaluation_only_duplicate_transition_count"], 1)
        self.assertEqual(chain["distinct_evidence_transition_count"], 0)
        self.assertEqual(chain["transition_event_counts"][CONTINUING_UNCHANGED], 1)
        self.assertEqual(chain["distinct_evidence_transition_event_counts"][CONTINUING_UNCHANGED], 0)

    def test_absence_preserves_prior_trigger_lineage_without_source_backfill(self):
        baseline = self.baseline()
        absent = self.factory.artifact(
            "2026-08-25T10:10:00Z",
            [record("BTC", "BTC", FIRST_ABSENCE, active=False)],
            {},
            prior=baseline,
            distinct=True,
        )
        inventory = _build_from_validated([baseline, absent], self.root)
        price = next(row for row in inventory["by_trigger_type"] if row["trigger_type"] == "PRICE_CONFIRMATION")
        self.assertEqual(price["event_counts"][FIRST_ABSENCE], 1)

    def test_changed_candidate_uses_current_trigger_set(self):
        baseline = self.baseline()
        changed = self.factory.artifact(
            "2026-08-25T10:10:00Z",
            [record("BTC", "BTC", CONTINUING_CHANGED)],
            source("BTC", "BTC", "INVALIDATION_TRIGGER"),
            prior=baseline,
            distinct=True,
        )
        inventory = _build_from_validated([baseline, changed], self.root)
        invalidation = next(row for row in inventory["by_trigger_type"] if row["trigger_type"] == "INVALIDATION_TRIGGER")
        self.assertEqual(invalidation["event_counts"][CONTINUING_CHANGED], 1)

    def test_reappearance_is_observed_event_not_lifetime_inference(self):
        baseline = self.baseline()
        absent = self.factory.artifact(
            "2026-08-25T10:10:00Z",
            [record("BTC", "BTC", FIRST_ABSENCE, active=False)],
            {}, prior=baseline, distinct=True,
        )
        reappeared = self.factory.artifact(
            "2026-08-25T10:20:00Z",
            [record("BTC", "BTC", REAPPEARED)],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
            prior=absent, distinct=True,
        )
        inventory = _build_from_validated([baseline, absent, reappeared], self.root)
        self.assertEqual(inventory["natural_forward_chain"]["transition_event_counts"][REAPPEARED], 1)
        self.assertFalse(inventory["policy_boundary"]["candidate_lifetime_inferred"])

    def test_manual_artifact_is_visible_but_never_advances_natural_chain(self):
        baseline = self.baseline()
        manual = self.factory.artifact(
            "2026-08-25T10:30:00Z",
            [record("BTC", "BTC", "FIRST_SEEN_EXACT_FORWARD_ONLY")],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
            qualification=MANUAL_SAMPLE,
            nonce="manual",
        )
        inventory = _build_from_validated([baseline, manual], self.root)
        self.assertEqual(inventory["artifact_qualification_counts"][MANUAL_SAMPLE], 1)
        self.assertEqual(inventory["natural_forward_chain"]["artifact_count"], 1)
        self.assertEqual(len(inventory["diagnostic_artifacts_excluded_from_natural_chain"]), 1)

    def test_manual_only_population_is_not_misrepresented_as_natural_evidence(self):
        manual = self.factory.artifact(
            "2026-08-25T10:30:00Z", [], {}, qualification=MANUAL_SAMPLE
        )
        inventory = _build_from_validated([manual], self.root)
        self.assertEqual(inventory["evidence_status"], "NO_NATURAL_FORWARD_CHAIN")
        self.assertEqual(inventory["natural_forward_chain"]["artifact_count"], 0)

    def test_forked_natural_chain_fails_closed(self):
        baseline = self.baseline()
        one = self.factory.artifact(
            "2026-08-25T10:10:00Z",
            [record("BTC", "BTC", CONTINUING_UNCHANGED)],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
            prior=baseline, distinct=True, nonce="one",
        )
        two = self.factory.artifact(
            "2026-08-25T10:20:00Z",
            [record("BTC", "BTC", CONTINUING_CHANGED)],
            source("BTC", "BTC", "INVALIDATION_TRIGGER"),
            prior=baseline, distinct=True, nonce="two",
        )
        with self.assertRaisesRegex(CandidateLifecycleEvidenceInventoryError, "NATURAL_CHAIN_FORK"):
            _build_from_validated([baseline, one, two], self.root)

    def test_missing_natural_parent_fails_closed(self):
        baseline = self.baseline()
        child = self.factory.artifact(
            "2026-08-25T10:10:00Z",
            [record("BTC", "BTC", CONTINUING_UNCHANGED)],
            source("BTC", "BTC", "PRICE_CONFIRMATION"),
            prior=baseline, distinct=True,
        )
        with self.assertRaisesRegex(CandidateLifecycleEvidenceInventoryError, "BASELINE_COUNT_INVALID"):
            _build_from_validated([child], self.root)

    def test_authority_and_downstream_gates_remain_closed(self):
        inventory = _build_from_validated([self.baseline()], self.root)
        self.assertEqual(inventory["authority"], AUTHORITY_ALL_FALSE)
        boundary = inventory["policy_boundary"]
        self.assertFalse(boundary["risk_capacity_opened"])
        self.assertFalse(boundary["p8_13_entry_proposal_opened"])
        self.assertEqual(boundary["money_action"], "NONE")

    def test_hash_is_deterministic_and_binds_the_complete_inventory(self):
        first = _build_from_validated([self.baseline()], self.root)
        second = _build_from_validated([self.baseline()], self.root)
        self.assertEqual(first, second)
        unsigned = {key: value for key, value in first.items() if key != "inventory_sha256"}
        self.assertEqual(first["inventory_sha256"], payload_sha256(unsigned))


class RealEvidenceAndWiringTests(unittest.TestCase):
    def test_current_committed_evidence_is_independently_revalidatable(self):
        inventory = build_inventory()
        self.assertGreaterEqual(inventory["artifact_count"], 1)
        self.assertGreaterEqual(inventory["natural_forward_chain"]["artifact_count"], 1)
        self.assertEqual(inventory["authority"], AUTHORITY_ALL_FALSE)
        self.assertFalse(inventory["policy_boundary"]["validity_window_selected"])

    def test_semantic_tamper_is_rejected_by_independent_rebuild(self):
        inventory = build_inventory()
        tampered = copy.deepcopy(inventory)
        tampered["policy_boundary"]["risk_capacity_opened"] = True
        tampered["inventory_sha256"] = payload_sha256({
            key: value for key, value in tampered.items() if key != "inventory_sha256"
        })
        with self.assertRaisesRegex(CandidateLifecycleEvidenceInventoryError, "SEMANTIC_TAMPER"):
            validate_inventory(tampered)

    def test_empty_exact_root_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            lifecycle = root / "candidate_lifecycle_observations"
            lifecycle.mkdir()
            with self.assertRaisesRegex(CandidateLifecycleEvidenceInventoryError, "NO_LIFECYCLE"):
                build_inventory(lifecycle, root)

    def test_workflow_and_authoritative_suite_register_the_inventory(self):
        workflow = (ROOT / ".github/workflows/p8-12-dynamic-clock.yml").read_text()
        run_all = (ROOT / "run_all.py").read_text()
        self.assertIn("python3 clock/candidate_lifecycle_evidence_inventory.py", workflow)
        self.assertIn("test/test_candidate_lifecycle_evidence_inventory.py", workflow)
        self.assertIn('"test/test_candidate_lifecycle_evidence_inventory.py"', run_all)

    def test_output_contains_no_policy_recommendation_or_executable_quantity(self):
        inventory = build_inventory()
        rendered = json.dumps(inventory, sort_keys=True)
        for forbidden in ("recommended_window", "position_size", "order_quantity"):
            self.assertNotIn(forbidden, rendered)


if __name__ == "__main__":
    unittest.main()
