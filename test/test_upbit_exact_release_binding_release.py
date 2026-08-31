#!/usr/bin/env python3
"""P3-12-GOV-05: identity/upbit_exact_release_binding_release.py -- v2
(release-grade: reuses the real governance.verify_code_chain() full
validation, computes hashes itself, rejects absolute/outside paths, and
the committed-release validator re-runs the projection through the real
runtime validator end to end).

No genuine code approval exists yet, so release tests use tempdir
artifacts.  The E2E fixture deliberately preserves the asymmetric shipped
shape: the registry has a six-field ``source_candidate_packet`` while the
taxonomy has no such field.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_release_test",
    ROOT / "identity" / "upbit_exact_release_binding_release.py",
)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
GOVERNANCE = RELEASE.GOVERNANCE

_FIXTURES_SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_fixtures_for_release_test",
    ROOT / "test" / "test_upbit_exact_release_binding.py",
)
FIXTURES = importlib.util.module_from_spec(_FIXTURES_SPEC)
_FIXTURES_SPEC.loader.exec_module(FIXTURES)


def _write(path: Path, obj) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return path


def _code_approval(**overrides):
    doc = {
        "schema_version": "upbit_exact_release_binding_code_approval/1",
        "approval_status": "RATIFIED",
        "ratified_by": "CIO_USER",
        "ratified_at_utc": "2026-08-30T13:00:00Z",
        "successor_candidate": {
            "path": "successor_candidate.json",
            "file_sha256": "a" * 64,
            "payload_sha256": "b" * 64,
        },
        "authority": {"order_authorized": False},
    }
    doc.update(overrides)
    return doc


class BuildReleaseProjectionUnitTests(unittest.TestCase):
    """Narrow unit tests against a stub GOVERNANCE.verify_code_chain --
    the full, real chain is exercised end to end in
    EndToEndReleaseAndRuntimeTests below."""

    def test_rejects_absolute_approval_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.build_release_projection(
                    repo_root=tmp,
                    code_approval_relative_path="/etc/passwd",
                    current_registry={"source_candidate_packet": {"path": "x", "file_sha256": "a" * 64, "payload_sha256": "b" * 64}},
                    current_taxonomy={"source_candidate_packet": {"path": "x", "file_sha256": "a" * 64, "payload_sha256": "b" * 64}},
                    current_freeze={},
                    evaluation_as_of="2026-08-30",
                )

    def test_rejects_path_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.build_release_projection(
                    repo_root=tmp,
                    code_approval_relative_path="../outside.json",
                    current_registry={"source_candidate_packet": {"path": "x", "file_sha256": "a" * 64, "payload_sha256": "b" * 64}},
                    current_taxonomy={"source_candidate_packet": {"path": "x", "file_sha256": "a" * 64, "payload_sha256": "b" * 64}},
                    current_freeze={},
                    evaluation_as_of="2026-08-30",
                )

    def test_rejects_missing_approval_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.build_release_projection(
                    repo_root=tmp,
                    code_approval_relative_path="does_not_exist.json",
                    current_registry={"source_candidate_packet": {"path": "x", "file_sha256": "a" * 64, "payload_sha256": "b" * 64}},
                    current_taxonomy={"source_candidate_packet": {"path": "x", "file_sha256": "a" * 64, "payload_sha256": "b" * 64}},
                    current_freeze={},
                    evaluation_as_of="2026-08-30",
                )

    def test_registry_and_taxonomy_base_candidate_pin_mismatch_raises(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, _ = FIXTURES.build_full_chain(tmp)
            taxonomy["source_candidate_packet"]["payload_sha256"] = "0" * 64
            current_freeze = json.loads(
                (tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json").read_text(encoding="utf-8")
            )
            with self.assertRaises(RELEASE.ReleaseProjectionError) as ctx:
                RELEASE.build_release_projection(
                    repo_root=tmp,
                    code_approval_relative_path="code_approval.json",
                    current_registry=registry,
                    current_taxonomy=taxonomy,
                    current_freeze=current_freeze,
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )
            self.assertIn("TAXONOMY_CONTENT_APPROVAL_CHAIN_INVALID", str(ctx.exception))

    def test_invalid_code_chain_raises(self):
        """A code approval that does not pass the real full chain
        validation (here: everything else is a genuine, valid setup, but
        the approval itself points at a successor candidate file that
        does not exist) must not be projected -- proving build_release_
        projection() runs the SAME full verify_code_chain(), not a
        minimal-shape-only check."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, artifacts = FIXTURES.build_full_chain(tmp)
            code_approval = json.loads((tmp / "code_approval.json").read_text(encoding="utf-8"))
            code_approval["successor_candidate"]["path"] = "no_such_successor_candidate.json"
            _write(tmp / "code_approval.json", code_approval)
            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.build_release_projection(
                    repo_root=tmp,
                    code_approval_relative_path="code_approval.json",
                    current_registry=registry,
                    current_taxonomy=taxonomy,
                    current_freeze={},
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )


class EndToEndReleaseAndRuntimeTests(unittest.TestCase):
    """No-mock: builds the full real content+code chain (via the SAME
    fixture builder test_upbit_exact_release_binding.py's own tests use),
    then reshapes the pre-release documents to the actual committed form:
    a six-field source_candidate_packet on the registry and no such field
    on the taxonomy.  It then drives
    release builder -> registry/taxonomy/freeze projection -> the real
    runtime validator, proving: before the code approval's ratification
    date the projected release is NOT yet effective, and on/after it,
    effective for exactly the eight approved markets."""

    def _setup(self, tmp: Path):
        registry, taxonomy, artifacts = FIXTURES.build_full_chain(tmp)
        # Actual committed shapes, not a symmetric synthetic convenience:
        # registry has a six-field redundant pin; taxonomy has none and
        # must resolve the same base candidate from its content approval.
        registry["source_candidate_packet"] = dict(
            registry["source_candidate_packet"],
            evaluation_as_of="2026-08-30", review_status="RATIFIED_BY_EXPLICIT_CIO_DECISION",
            snapshot_date="2026-08-30",
        )
        del taxonomy["source_candidate_packet"]
        current_freeze = json.loads(
            (tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json").read_text(encoding="utf-8")
        )
        # The release builder projects code_approval_resolution itself --
        # the pre-release freeze must not already carry one.
        current_freeze_without_code_resolution = {
            k: v for k, v in current_freeze.items() if k != "code_approval_resolution"
        }
        code_approval_relative = "code_approval.json"
        self.assertTrue((tmp / code_approval_relative).is_file())
        return registry, taxonomy, current_freeze_without_code_resolution, artifacts

    def test_release_builder_projection_matches_hand_assembled_activation(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._setup(tmp)
            projection = RELEASE.build_release_projection(
                repo_root=tmp, code_approval_relative_path="code_approval.json",
                current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            hand_activated = artifacts["activate"](registry)
            self.assertEqual(projection["registry"]["code_approval_evidence_ref"], hand_activated["code_approval_evidence_ref"])
            self.assertEqual(projection["registry"]["code_approval_evidence_sha256"], hand_activated["code_approval_evidence_sha256"])
            # original inputs never mutated in place
            self.assertNotIn("code_approval_evidence_ref", registry)
            self.assertNotIn("source_candidate_packet", taxonomy)
            self.assertNotIn("source_candidate_packet", projection["taxonomy"])

    def test_e2e_committed_release_is_false_before_ratification_and_exact_eight_after(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._setup(tmp)

            projection = RELEASE.build_release_projection(
                repo_root=tmp, code_approval_relative_path="code_approval.json",
                current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            committed_registry_path = _write(tmp / "committed_registry.json", projection["registry"])
            committed_taxonomy_path = _write(tmp / "committed_taxonomy.json", projection["taxonomy"])
            # governance.validate_exact_release() always reads the freeze
            # from its own FIXED canonical path (repo_root/config/upbit_
            # identity_taxonomy_governance_freeze.json) -- never from a
            # caller-chosen "committed_freeze_relative_path". A real
            # release MUST publish the projected freeze there for the
            # runtime leg to see it at all.
            committed_freeze_relative = "config/upbit_identity_taxonomy_governance_freeze.json"
            committed_freeze_path = _write(tmp / committed_freeze_relative, projection["freeze"])

            # E2E validate_committed_release: projection equality AND both
            # documents independently pass the real runtime validator.
            RELEASE.validate_committed_release(
                repo_root=tmp, code_approval_relative_path="code_approval.json",
                current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                committed_registry_relative_path="committed_registry.json",
                committed_taxonomy_relative_path="committed_taxonomy.json",
                committed_freeze_relative_path=committed_freeze_relative,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )  # must not raise

            committed_registry = json.loads(committed_registry_path.read_text(encoding="utf-8"))
            committed_taxonomy = json.loads(committed_taxonomy_path.read_text(encoding="utf-8"))

            # Before the code approval's own ratification date (2026-08-30):
            # not yet effective.
            self.assertFalse(GOVERNANCE.validate_exact_release(
                committed_registry, content_field="mappings", evaluation_as_of="2026-08-29", repo_root=tmp,
            ))
            # On/after: effective for exactly the eight approved markets.
            self.assertTrue(GOVERNANCE.validate_exact_release(
                committed_registry, content_field="mappings", evaluation_as_of=FIXTURES.EVAL_AS_OF, repo_root=tmp,
            ))
            self.assertEqual(committed_registry["mappings"], FIXTURES.MARKETS_TO_IDS)
            self.assertEqual(len(committed_registry["mappings"]), 8)
            self.assertTrue(GOVERNANCE.validate_exact_release(
                committed_taxonomy, content_field="records", evaluation_as_of=FIXTURES.EVAL_AS_OF, repo_root=tmp,
            ))
            # Registry metadata is preserved; taxonomy stays in its real
            # committed shape and the builder does not invent a pin.
            pin = committed_registry["source_candidate_packet"]
            self.assertEqual(pin["evaluation_as_of"], "2026-08-30")
            self.assertEqual(pin["review_status"], "RATIFIED_BY_EXPLICIT_CIO_DECISION")
            self.assertEqual(pin["snapshot_date"], "2026-08-30")
            self.assertNotIn("source_candidate_packet", committed_taxonomy)

    def test_e2e_diverging_committed_registry_fails_validate_committed_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._setup(tmp)
            projection = RELEASE.build_release_projection(
                repo_root=tmp, code_approval_relative_path="code_approval.json",
                current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            tampered_registry = copy.deepcopy(projection["registry"])
            tampered_registry["mappings"]["KRW-BTC"] = "TAMPERED"
            _write(tmp / "committed_registry.json", tampered_registry)
            _write(tmp / "committed_taxonomy.json", projection["taxonomy"])
            _write(tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json", projection["freeze"])

            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.validate_committed_release(
                    repo_root=tmp, code_approval_relative_path="code_approval.json",
                    current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                    committed_registry_relative_path="committed_registry.json",
                    committed_taxonomy_relative_path="committed_taxonomy.json",
                    committed_freeze_relative_path="config/upbit_identity_taxonomy_governance_freeze.json",
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )

    def test_e2e_canonical_freeze_not_actually_published_fails_runtime_leg(self):
        """governance.validate_exact_release() reads the freeze from its
        own FIXED canonical path, never from validate_committed_release()'s
        `committed_freeze_relative_path` argument -- so a caller who
        commits a byte-for-byte-correct copy of the projected freeze
        somewhere but never actually publishes it to the canonical
        location must still fail closed here, because the real runtime
        would never see the code_approval_resolution block either. This
        proves projection equality against an arbitrary path is not
        sufficient; the release must land at the one real location."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._setup(tmp)
            projection = RELEASE.build_release_projection(
                repo_root=tmp, code_approval_relative_path="code_approval.json",
                current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            _write(tmp / "committed_registry.json", projection["registry"])
            _write(tmp / "committed_taxonomy.json", projection["taxonomy"])
            # A byte-for-byte-correct copy of the projected freeze exists
            # at a path validate_committed_release is told to check --
            # projection equality alone will pass.
            _write(tmp / "committed_freeze.json", projection["freeze"])
            # But the canonical location the runtime validator actually
            # reads from was never updated to carry code_approval_resolution
            # at all -- simulating a release that forgot to publish it.
            stale_canonical_freeze = {k: v for k, v in projection["freeze"].items() if k != "code_approval_resolution"}
            _write(tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json", stale_canonical_freeze)

            with self.assertRaises(RELEASE.ReleaseProjectionError) as ctx:
                RELEASE.validate_committed_release(
                    repo_root=tmp, code_approval_relative_path="code_approval.json",
                    current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                    committed_registry_relative_path="committed_registry.json",
                    committed_taxonomy_relative_path="committed_taxonomy.json",
                    committed_freeze_relative_path="committed_freeze.json",
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )
            self.assertIn("RUNTIME_VALIDATION", str(ctx.exception))


class ReleaseBuilderRemainingNegativeTests(unittest.TestCase):
    """The remaining item-7 negative scenarios, exercised specifically at
    the release-builder / E2E layer (not just inside governance's own
    unit tests): incomplete authority approval, equal approval/generated
    timestamps, a tampered (not merely missing) freeze code resolution,
    and hand-adding the ref fields without ever running the builder."""

    def _valid_setup(self, tmp: Path):
        registry, taxonomy, artifacts = FIXTURES.build_full_chain(tmp)
        current_freeze = json.loads(
            (tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json").read_text(encoding="utf-8")
        )
        current_freeze = {k: v for k, v in current_freeze.items() if k != "code_approval_resolution"}
        return registry, taxonomy, current_freeze, artifacts

    def test_incomplete_authority_code_approval_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._valid_setup(tmp)
            code_approval = json.loads((tmp / "code_approval.json").read_text(encoding="utf-8"))
            # Authority must be the EXACT key set, all False -- dropping a
            # required key (rather than merely setting one True) must also
            # be refused: a partial/incomplete authority declaration is not
            # equivalent to explicitly declaring every key False.
            code_approval["authority"] = {"order_authorized": False}
            _write(tmp / "code_approval.json", code_approval)
            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.build_release_projection(
                    repo_root=tmp, code_approval_relative_path="code_approval.json",
                    current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )

    def test_equal_code_approval_and_successor_generated_timestamps_rejected_by_builder(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, artifacts = FIXTURES.build_full_chain(
                tmp, code_approval_ratified_at=FIXTURES.SUCCESSOR_GENERATED_AT,
            )
            current_freeze = json.loads(
                (tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json").read_text(encoding="utf-8")
            )
            current_freeze = {k: v for k, v in current_freeze.items() if k != "code_approval_resolution"}
            with self.assertRaises(RELEASE.ReleaseProjectionError):
                RELEASE.build_release_projection(
                    repo_root=tmp, code_approval_relative_path="code_approval.json",
                    current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )

    def test_tampered_freeze_code_resolution_fails_runtime_leg(self):
        """A canonical freeze that DOES carry a code_approval_resolution
        block, but one that does not exactly match what verify_code_chain()
        independently resolves (e.g. pointing at a different code approval
        ref), must fail closed -- not merely a MISSING block."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._valid_setup(tmp)
            projection = RELEASE.build_release_projection(
                repo_root=tmp, code_approval_relative_path="code_approval.json",
                current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            _write(tmp / "committed_registry.json", projection["registry"])
            _write(tmp / "committed_taxonomy.json", projection["taxonomy"])
            # committed_freeze.json matches the CORRECT projection exactly
            # (so projection equality alone would pass) -- but the
            # canonical location the runtime validator actually reads from
            # carries a TAMPERED resolution (wrong sha256), simulating a
            # release where the committed copy and the real, live
            # governance freeze have drifted apart.
            _write(tmp / "committed_freeze.json", projection["freeze"])
            tampered_freeze = copy.deepcopy(projection["freeze"])
            tampered_freeze["code_approval_resolution"]["code_approval_evidence_sha256"] = "0" * 64
            _write(tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json", tampered_freeze)

            with self.assertRaises(RELEASE.ReleaseProjectionError) as ctx:
                RELEASE.validate_committed_release(
                    repo_root=tmp, code_approval_relative_path="code_approval.json",
                    current_registry=registry, current_taxonomy=taxonomy, current_freeze=current_freeze,
                    committed_registry_relative_path="committed_registry.json",
                    committed_taxonomy_relative_path="committed_taxonomy.json",
                    committed_freeze_relative_path="committed_freeze.json",
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )
            self.assertIn("RUNTIME_VALIDATION", str(ctx.exception))

    def test_manual_ref_addition_without_builder_never_activates(self):
        """Hand-adding just the two code_approval_evidence_* fields to the
        real registry/taxonomy -- the exact shortcut this whole design
        exists to close -- must never activate the runtime chain, because
        no builder ever ran to produce a matching freeze
        code_approval_resolution block."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            registry, taxonomy, current_freeze, artifacts = self._valid_setup(tmp)
            code_approval_hash = GOVERNANCE.file_sha256(tmp / "code_approval.json")
            hand_edited_registry = copy.deepcopy(registry)
            hand_edited_registry["code_approval_evidence_ref"] = "code_approval.json"
            hand_edited_registry["code_approval_evidence_sha256"] = code_approval_hash
            # The canonical freeze on disk never got a code_approval_
            # resolution block, because no builder ever ran -- only the
            # two ref fields were hand-added to the registry itself.
            _write(tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json", current_freeze)
            self.assertFalse(GOVERNANCE.validate_exact_release(
                hand_edited_registry, content_field="mappings", evaluation_as_of=FIXTURES.EVAL_AS_OF, repo_root=tmp,
            ))


if __name__ == "__main__":
    unittest.main()
