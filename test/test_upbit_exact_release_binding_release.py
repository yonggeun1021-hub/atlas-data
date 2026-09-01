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
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upbit_exact_release_binding_release_test",
    ROOT / "identity" / "upbit_exact_release_binding_release.py",
)
RELEASE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RELEASE)
GOVERNANCE = RELEASE.GOVERNANCE

_UNIVERSE_SPEC = importlib.util.spec_from_file_location(
    "upbit_tradeable_universe_for_transition_release_test",
    ROOT / "universe" / "upbit_tradeable_universe.py",
)
UNIVERSE = importlib.util.module_from_spec(_UNIVERSE_SPEC)
_UNIVERSE_SPEC.loader.exec_module(UNIVERSE)

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


def _population_record(*, exact_release: bool) -> dict:
    row_authority = {
        "investable_eligible": False,
        "paper_eligible": False,
        "stage_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
        "order_authorized": False,
    }
    rows = []
    for market, asset in sorted(FIXTURES.MARKETS_TO_IDS.items()):
        rows.append({
            "market": market,
            "state": "PAPER_ELIGIBLE" if exact_release else "OBSERVATION_POOL",
            "reason": "PAPER_ELIGIBLE_ALL_GATES_PASSED" if exact_release else "IDENTITY_UNRATIFIED",
            "candidate_canonical_asset_id": asset if exact_release else None,
            "authority": dict(row_authority),
        })
    packet = {
        "schema_version": "upbit_tradeable_universe_packet/1",
        "snapshot_date": FIXTURES.EVAL_AS_OF,
        "evaluation_as_of": FIXTURES.EVAL_AS_OF,
        "available_at": "2026-08-30T00:40:00Z",
        "manifest_sha256": "1" * 64,
        "policy_version": "fixture-policy",
        "policy_ratified": True,
        "taxonomy_version": "fixture-taxonomy",
        "taxonomy_ratified": exact_release,
        "duplicate_market_codes": {},
        "summary": {
            "market_count": 8,
            "observation_pool_count": 0 if exact_release else 8,
            "tradeable_universe_count": 0,
            "paper_eligible_count": 8 if exact_release else 0,
            "blocked_count": 0,
        },
        "markets": rows,
        "authority": dict(row_authority),
    }
    packet["payload_sha256"] = RELEASE.payload_sha256(packet)
    record_authority = {
        "observation_pool_population_only": not exact_release,
        "identity_ratification_authorized": False,
        "taxonomy_ratification_authorized": False,
        "policy_ratification_authorized": False,
        "tradeable_universe_promotion_authorized": False,
        "paper_eligible_promotion_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
        "order_authorized": False,
    }
    record = {
        "schema_version": "upbit_universe_population/1",
        "snapshot_date": FIXTURES.EVAL_AS_OF,
        "generated_at": "2026-08-30T00:40:00Z",
        "raw_snapshot": {
            "path": f"evidence/crypto/upbit/raw/{FIXTURES.EVAL_AS_OF}",
            "manifest_sha256": "1" * 64,
        },
        "builder": {
            "module": "universe/upbit_tradeable_universe.py",
            "output_schema_version": "upbit_tradeable_universe_packet/1",
        },
        "ratification": {"effective_for_snapshot": exact_release},
        "identity_review": {"proposal_count": 8, "findings": [], "blocked_markets": []},
        "authority": record_authority,
        "packet": packet,
    }
    record["payload_sha256"] = RELEASE.payload_sha256(record)
    return record


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


class SameVintageTransitionProjectionTests(unittest.TestCase):
    """The append-only population successor stays inside both approved
    code labels: its manifest is built by ``release_builder`` and consumed
    by ``consumer_file``.  No populate/workflow-only trust path exists."""

    def setUp(self):
        self.deterministic_successor = None

        def deterministic_rebuild(_source, **_kwargs):
            if self.deterministic_successor is None:
                raise AssertionError("deterministic successor fixture not initialized")
            return copy.deepcopy(self.deterministic_successor)

        self.release_rebuild_patch = mock.patch.object(
            RELEASE.UNIVERSE,
            "rebuild_same_vintage_population_record",
            side_effect=deterministic_rebuild,
        )
        self.consumer_rebuild_patch = mock.patch.object(
            UNIVERSE,
            "rebuild_same_vintage_population_record",
            side_effect=deterministic_rebuild,
        )
        self.release_rebuild_patch.start()
        self.consumer_rebuild_patch.start()

    def tearDown(self):
        self.consumer_rebuild_patch.stop()
        self.release_rebuild_patch.stop()

    def _setup(self, tmp: Path):
        registry, taxonomy, artifacts = FIXTURES.build_full_chain(tmp)
        registry = artifacts["activate"](registry)
        taxonomy = artifacts["activate"](taxonomy)
        del taxonomy["source_candidate_packet"]
        _write(tmp / "config" / "upbit_asset_identity_registry.json", registry)
        _write(tmp / "config" / "upbit_exclusion_taxonomy.json", taxonomy)

        freeze_path = tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json"
        freeze = json.loads(freeze_path.read_text(encoding="utf-8"))
        freeze["approval_resolution"].update({
            "approval_evidence_ref": registry["approval_evidence_ref"],
            "approval_evidence_sha256": registry["approval_evidence_sha256"],
        })
        _write(freeze_path, freeze)

        source = _population_record(exact_release=False)
        source_relative = (
            f"data/observations/upbit_tradeable_universe/{FIXTURES.EVAL_AS_OF}/packet.json"
        )
        source_path = _write(tmp / source_relative, source)
        successor = _population_record(exact_release=True)
        self.deterministic_successor = copy.deepcopy(successor)
        successor_relative = (
            f"data/observations/upbit_tradeable_universe/{FIXTURES.EVAL_AS_OF}/transitions/"
            f"{source['payload_sha256']}-to-{successor['payload_sha256']}/packet.json"
        )
        return source, source_path, successor, source_relative, successor_relative, artifacts

    @staticmethod
    def _rehash_record(record: dict) -> dict:
        record["packet"]["payload_sha256"] = RELEASE.payload_sha256(
            {key: value for key, value in record["packet"].items() if key != "payload_sha256"}
        )
        record["payload_sha256"] = RELEASE.payload_sha256(
            {key: value for key, value in record.items() if key != "payload_sha256"}
        )
        return record

    def test_builder_and_consumer_reject_rehashed_forged_successor_content_and_authority(self):
        mutators = {
            "reason": lambda record: record["packet"]["markets"][0].__setitem__("reason", "FORGED"),
            "canonical_asset_id": lambda record: record["packet"]["markets"][0].__setitem__(
                "candidate_canonical_asset_id", "FORGED-ASSET",
            ),
            "authority_key_shrink": lambda record: record["packet"]["markets"][0].__setitem__(
                "authority", {"other": False},
            ),
        }
        for label, mutate in mutators.items():
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                source, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
                valid_projection = RELEASE.build_same_vintage_transition_projection(
                    repo_root=tmp,
                    source_record_relative_path=source_relative,
                    successor_record_relative_path=successor_relative,
                    successor_record=successor,
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )
                forged = self._rehash_record(copy.deepcopy(successor))
                mutate(forged)
                forged = self._rehash_record(forged)
                forged_relative = (
                    f"data/observations/upbit_tradeable_universe/{FIXTURES.EVAL_AS_OF}/transitions/"
                    f"{source['payload_sha256']}-to-{forged['payload_sha256']}/packet.json"
                )
                with self.assertRaises(RELEASE.ReleaseProjectionError):
                    RELEASE.build_same_vintage_transition_projection(
                        repo_root=tmp,
                        source_record_relative_path=source_relative,
                        successor_record_relative_path=forged_relative,
                        successor_record=forged,
                        evaluation_as_of=FIXTURES.EVAL_AS_OF,
                    )

                manifest = copy.deepcopy(valid_projection["manifest"])
                forged_bytes = RELEASE.formatted_json_bytes(forged)
                manifest["successor_record"] = {
                    "path": forged_relative,
                    "file_sha256": RELEASE.hashlib.sha256(forged_bytes).hexdigest(),
                    "payload_sha256": forged["payload_sha256"],
                }
                manifest["payload_sha256"] = RELEASE.payload_sha256(
                    {key: value for key, value in manifest.items() if key != "payload_sha256"}
                )
                forged_path = _write(tmp / forged_relative, forged)
                manifest_path = _write(forged_path.with_name("transition.json"), manifest)
                with self.assertRaises(UNIVERSE.UpbitUniverseError):
                    UNIVERSE.validate_same_vintage_transition(manifest_path, repo_root=tmp)

    def test_builder_rejects_rehashed_source_with_truncated_authority_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source, source_path, successor, source_relative, successor_relative, _ = self._setup(tmp)
            source["authority"] = {"other": False}
            source["payload_sha256"] = RELEASE.payload_sha256(
                {key: value for key, value in source.items() if key != "payload_sha256"}
            )
            _write(source_path, source)
            successor_relative = (
                f"data/observations/upbit_tradeable_universe/{FIXTURES.EVAL_AS_OF}/transitions/"
                f"{source['payload_sha256']}-to-{successor['payload_sha256']}/packet.json"
            )
            with self.assertRaisesRegex(
                RELEASE.ReleaseProjectionError,
                "TRANSITION_SOURCE_AUTHORITY_NOT_CLOSED",
            ):
                RELEASE.build_same_vintage_transition_projection(
                    repo_root=tmp,
                    source_record_relative_path=source_relative,
                    successor_record_relative_path=successor_relative,
                    successor_record=successor,
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )

    def test_projection_preserves_exact_eight_base_and_pins_approved_builder_consumer(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source, source_path, successor, source_relative, successor_relative, artifacts = self._setup(tmp)
            source_bytes = source_path.read_bytes()

            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )

            manifest = projection["manifest"]
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertEqual(projection["successor_record"], successor)
            self.assertEqual(
                manifest["builder"],
                {
                    "path": "identity/upbit_exact_release_binding_release.py",
                    "file_sha256": GOVERNANCE.file_sha256(artifacts["release_builder_path"]),
                },
            )
            self.assertEqual(
                manifest["consumer"],
                {
                    "path": "universe/upbit_tradeable_universe.py",
                    "file_sha256": GOVERNANCE.file_sha256(artifacts["consumer_path"]),
                },
            )
            self.assertEqual(
                manifest["base_content_approval"],
                json.loads(
                    (tmp / "config" / "upbit_identity_taxonomy_governance_freeze.json")
                    .read_text(encoding="utf-8")
                )["approval_resolution"],
            )
            self.assertEqual(
                sorted(
                    row["market"] for row in successor["packet"]["markets"]
                    if row["state"] == "PAPER_ELIGIBLE"
                ),
                sorted(FIXTURES.MARKETS_TO_IDS),
            )
            self.assertTrue(all(value is False for value in manifest["authority"].values()))

    def test_pinned_consumer_independently_accepts_projection_and_rejects_tamper(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source, source_path, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            successor_path = _write(tmp / successor_relative, projection["successor_record"])
            manifest_path = _write(successor_path.with_name("transition.json"), projection["manifest"])

            selected = UNIVERSE.validate_same_vintage_transition(manifest_path, repo_root=tmp)
            self.assertEqual(selected["path"].resolve(), successor_path.resolve())
            self.assertEqual(selected["packet"]["summary"]["paper_eligible_count"], 8)

            tampered = copy.deepcopy(projection["manifest"])
            tampered["authority"]["order_authorized"] = True
            tampered["payload_sha256"] = RELEASE.payload_sha256(
                {key: value for key, value in tampered.items() if key != "payload_sha256"}
            )
            _write(manifest_path, tampered)
            with self.assertRaisesRegex(UNIVERSE.UpbitUniverseError, "TRANSITION_AUTHORITY_NOT_FALSE"):
                UNIVERSE.validate_same_vintage_transition(manifest_path, repo_root=tmp)

    def test_latest_selector_prefers_valid_same_day_transition_only_after_code_approval(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            successor_path = _write(tmp / successor_relative, projection["successor_record"])
            _write(successor_path.with_name("transition.json"), projection["manifest"])
            data_root = tmp / "data" / "observations" / "upbit_tradeable_universe"

            before = UNIVERSE.find_latest_population_record(
                data_root,
                not_after=dt.datetime(
                    2026, 8, 30, 12, 59, 59,
                    tzinfo=dt.timezone.utc,
                ),
                repo_root=tmp,
            )
            after = UNIVERSE.find_latest_population_record(
                data_root,
                not_after=dt.datetime(
                    2026, 8, 30, 13, 0, 0,
                    tzinfo=dt.timezone.utc,
                ),
                repo_root=tmp,
            )
            self.assertEqual(before["path"], tmp / source_relative)
            self.assertEqual(after["path"].resolve(), successor_path.resolve())
            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_SELECTION_REQUIRES_NOT_AFTER",
            ):
                UNIVERSE.find_latest_population_record(data_root, repo_root=tmp)
            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "UNIVERSE_NOT_AFTER_MUST_BE_TIMEZONE_AWARE",
            ):
                UNIVERSE.find_latest_population_record(
                    data_root,
                    not_after=dt.datetime(2026, 8, 30, 13, 0, 0),
                    repo_root=tmp,
                )

    def test_invalid_latest_transition_never_falls_back_to_old_or_previous_day(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            successor_path = _write(tmp / successor_relative, projection["successor_record"])
            manifest = copy.deepcopy(projection["manifest"])
            manifest["consumer"]["file_sha256"] = "0" * 64
            manifest["payload_sha256"] = RELEASE.payload_sha256(
                {key: value for key, value in manifest.items() if key != "payload_sha256"}
            )
            _write(successor_path.with_name("transition.json"), manifest)

            previous = _population_record(exact_release=False)
            previous["snapshot_date"] = "2026-08-29"
            previous["packet"]["snapshot_date"] = "2026-08-29"
            previous["packet"]["evaluation_as_of"] = "2026-08-29"
            previous["raw_snapshot"]["path"] = "evidence/crypto/upbit/raw/2026-08-29"
            previous["packet"]["payload_sha256"] = RELEASE.payload_sha256(
                {key: value for key, value in previous["packet"].items() if key != "payload_sha256"}
            )
            previous["payload_sha256"] = RELEASE.payload_sha256(
                {key: value for key, value in previous.items() if key != "payload_sha256"}
            )
            _write(
                tmp / "data" / "observations" / "upbit_tradeable_universe"
                / "2026-08-29" / "packet.json",
                previous,
            )
            (tmp / "evidence" / "crypto" / "upbit" / "realtime_validation" / "2026-08-30").mkdir(
                parents=True,
            )

            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_CONSUMER_FILE_HASH_MISMATCH",
            ):
                UNIVERSE.find_latest_population_record(
                    tmp / "data" / "observations" / "upbit_tradeable_universe",
                    repo_root=tmp,
                )

    def test_transition_directory_symlink_and_symlinked_manifest_parent_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            redirect = tmp / "redirected-transition"
            _write(redirect / "packet.json", projection["successor_record"])
            redirected_manifest = _write(redirect / "transition.json", projection["manifest"])
            transitions_root = (
                tmp / "data" / "observations" / "upbit_tradeable_universe"
                / FIXTURES.EVAL_AS_OF / "transitions"
            )
            transitions_root.mkdir()
            symlink_child = transitions_root / "symlink-child"
            symlink_child.symlink_to(redirect, target_is_directory=True)

            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_DIRECTORY_SYMLINK_FORBIDDEN",
            ):
                UNIVERSE.find_latest_population_record(
                    tmp / "data" / "observations" / "upbit_tradeable_universe",
                    repo_root=tmp,
                )
            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_MANIFEST_SYMLINK_FORBIDDEN",
            ):
                UNIVERSE.validate_same_vintage_transition(
                    symlink_child / redirected_manifest.name,
                    repo_root=tmp,
                )

    def test_manifest_must_resolve_to_exact_content_addressed_transition_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            _write(tmp / successor_relative, projection["successor_record"])
            relocated = _write(
                tmp / "relocated" / "transition.json",
                projection["manifest"],
            )
            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_MANIFEST_LOCATION_MISMATCH",
            ):
                UNIVERSE.validate_same_vintage_transition(relocated, repo_root=tmp)

    def test_consumer_rejects_final_packet_and_intermediate_parent_symlinks(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            successor_path = tmp / successor_relative
            successor_path.parent.mkdir(parents=True)
            backing = _write(tmp / "successor-backing.json", projection["successor_record"])
            successor_path.symlink_to(backing)
            manifest_path = _write(successor_path.with_name("transition.json"), projection["manifest"])
            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_SUCCESSOR_RECORD_SYMLINK_FORBIDDEN",
            ):
                UNIVERSE.validate_same_vintage_transition(manifest_path, repo_root=tmp)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            expected_successor = tmp / successor_relative
            transitions_root = expected_successor.parents[1]
            redirected_root = tmp / "redirected-transitions"
            redirected_successor = redirected_root / expected_successor.parent.name / "packet.json"
            _write(redirected_successor, projection["successor_record"])
            redirected_manifest = _write(
                redirected_successor.with_name("transition.json"),
                projection["manifest"],
            )
            transitions_root.parent.mkdir(parents=True, exist_ok=True)
            transitions_root.symlink_to(redirected_root, target_is_directory=True)
            with self.assertRaisesRegex(
                UNIVERSE.UpbitUniverseError,
                "TRANSITION_MANIFEST_SYMLINK_FORBIDDEN",
            ):
                UNIVERSE.validate_same_vintage_transition(
                    transitions_root / expected_successor.parent.name / redirected_manifest.name,
                    repo_root=tmp,
                )

    def test_retained_bundle_replay_requires_untampered_manifest_source_and_successor(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source, _, successor, source_relative, successor_relative, _ = self._setup(tmp)
            projection = RELEASE.build_same_vintage_transition_projection(
                repo_root=tmp,
                source_record_relative_path=source_relative,
                successor_record_relative_path=successor_relative,
                successor_record=successor,
                evaluation_as_of=FIXTURES.EVAL_AS_OF,
            )
            bundle = tmp / "retained" / "bundle"
            source_path = _write(bundle / "canonical-source.json", source)
            successor_path = _write(bundle / "successor.json", projection["successor_record"])
            manifest_path = _write(bundle / "transition.json", projection["manifest"])

            selected = UNIVERSE.validate_retained_same_vintage_transition(
                manifest_path,
                source_record_path=source_path,
                successor_record_path=successor_path,
                repo_root=tmp,
            )
            self.assertEqual(selected["record"], successor)

            source_path.unlink()
            with self.assertRaises(UNIVERSE.UpbitUniverseError):
                UNIVERSE.validate_retained_same_vintage_transition(
                    manifest_path,
                    source_record_path=source_path,
                    successor_record_path=successor_path,
                    repo_root=tmp,
                )
            _write(source_path, source)
            tampered_manifest = copy.deepcopy(projection["manifest"])
            tampered_manifest["source_record"]["file_sha256"] = "0" * 64
            tampered_manifest["payload_sha256"] = RELEASE.payload_sha256(
                {key: value for key, value in tampered_manifest.items() if key != "payload_sha256"}
            )
            _write(manifest_path, tampered_manifest)
            with self.assertRaises(UNIVERSE.UpbitUniverseError):
                UNIVERSE.validate_retained_same_vintage_transition(
                    manifest_path,
                    source_record_path=source_path,
                    successor_record_path=successor_path,
                    repo_root=tmp,
                )

    def test_builder_rejects_final_source_packet_and_intermediate_parent_symlinks(self):
        for symlink_kind in ("packet", "date-directory"):
            with self.subTest(symlink_kind=symlink_kind), tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                _, source_path, successor, source_relative, successor_relative, _ = self._setup(tmp)
                if symlink_kind == "packet":
                    backing = source_path.with_name("source-backing.json")
                    source_path.rename(backing)
                    source_path.symlink_to(backing)
                else:
                    date_directory = source_path.parent
                    backing = tmp / "source-date-backing"
                    date_directory.rename(backing)
                    date_directory.symlink_to(backing, target_is_directory=True)
                with self.assertRaisesRegex(
                    RELEASE.ReleaseProjectionError,
                    "TRANSITION_SOURCE_RECORD_SYMLINK_FORBIDDEN",
                ):
                    RELEASE.build_same_vintage_transition_projection(
                        repo_root=tmp,
                        source_record_relative_path=source_relative,
                        successor_record_relative_path=successor_relative,
                        successor_record=successor,
                        evaluation_as_of=FIXTURES.EVAL_AS_OF,
                    )

    def test_builder_rejects_any_old_state_other_than_policy_true_taxonomy_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            source, source_path, successor, source_relative, successor_relative, _ = self._setup(tmp)
            for policy_ratified, taxonomy_ratified in ((False, False), (False, True), (True, True)):
                with self.subTest(policy=policy_ratified, taxonomy=taxonomy_ratified):
                    mutated = copy.deepcopy(source)
                    mutated["packet"]["policy_ratified"] = policy_ratified
                    mutated["packet"]["taxonomy_ratified"] = taxonomy_ratified
                    mutated["packet"]["payload_sha256"] = RELEASE.payload_sha256(
                        {key: value for key, value in mutated["packet"].items() if key != "payload_sha256"}
                    )
                    mutated["payload_sha256"] = RELEASE.payload_sha256(
                        {key: value for key, value in mutated.items() if key != "payload_sha256"}
                    )
                    _write(source_path, mutated)
                    bad_successor_relative = successor_relative.replace(
                        source["payload_sha256"], mutated["payload_sha256"],
                    )
                    with self.assertRaisesRegex(
                        RELEASE.ReleaseProjectionError,
                        "TRANSITION_SOURCE_STATE_NOT_POLICY_TRUE_TAXONOMY_FALSE",
                    ):
                        RELEASE.build_same_vintage_transition_projection(
                            repo_root=tmp,
                            source_record_relative_path=source_relative,
                            successor_record_relative_path=bad_successor_relative,
                            successor_record=successor,
                            evaluation_as_of=FIXTURES.EVAL_AS_OF,
                        )
            _write(source_path, source)

    def test_builder_fails_when_approved_consumer_bytes_drift(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            _, _, successor, source_relative, successor_relative, artifacts = self._setup(tmp)
            artifacts["consumer_path"].write_text("# tampered after approval\n", encoding="utf-8")
            with self.assertRaisesRegex(
                RELEASE.ReleaseProjectionError,
                "TRANSITION_REGISTRY_EXACT_RELEASE_FAILED",
            ):
                RELEASE.build_same_vintage_transition_projection(
                    repo_root=tmp,
                    source_record_relative_path=source_relative,
                    successor_record_relative_path=successor_relative,
                    successor_record=successor,
                    evaluation_as_of=FIXTURES.EVAL_AS_OF,
                )


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
