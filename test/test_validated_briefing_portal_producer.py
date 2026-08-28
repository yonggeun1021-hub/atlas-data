#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).parents[1]


def _load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


producer = _load(
    "validated_briefing_portal_producer",
    ".github/scripts/validated_briefing_portal_producer.py",
)
dispatcher = _load(
    "dispatch_portal_projection",
    ".github/scripts/dispatch_portal_projection.py",
)


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _write_json(path: Path, value: dict) -> bytes:
    body = producer.canonical(value) + b"\n"
    path.write_bytes(body)
    return body


class FakeGitHubData:
    def __init__(self, bodies: dict[tuple[str, str], bytes], ancestors: set[str]):
        self.bodies = bodies
        self.ancestors = ancestors
        self.requested: list[tuple[str, str]] = []

    def require_main_ancestor(self, commit: str, default_branch: str) -> None:
        if default_branch != "main" or commit not in self.ancestors:
            raise dispatcher.DispatchError(f"NON_MAIN_COMMIT_REJECTED:{commit}")

    def get_bytes(self, commit: str, path: str) -> bytes:
        self.requested.append((commit, path))
        try:
            return self.bodies[(commit, path)]
        except KeyError:
            raise dispatcher.DispatchError(f"CONTENT_NOT_FILE:{path}") from None


class ValidatedBriefingPortalProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Atlas Test"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "atlas@example.invalid"], cwd=self.repo, check=True)
        self.generation = "a" * 64
        source = producer.canonical({"generation_id": self.generation, "value": 7}) + b"\n"
        source_path = self.repo / "evidence/source.json"
        source_path.parent.mkdir(parents=True)
        source_path.write_bytes(source)
        subprocess.run(["git", "add", "evidence/source.json"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "source"], cwd=self.repo, check=True)
        self.source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        self.source_sha = _sha(source)

    def tearDown(self):
        self.temp.cleanup()

    def _inputs(self, *, bad_briefing_hash=False, post_delivery=None):
        input_dir = self.repo / "input"
        input_dir.mkdir(exist_ok=True)
        briefing = b"# Validated briefing\n\nNo money action.\n"
        briefing_path = input_dir / "briefing.md"
        briefing_path.write_bytes(briefing)
        ledger = {
            "schema_version": "claim_ledger/1",
            "state": "READY_FOR_CHATGPT_VALIDATION",
            "briefing_id": "2026-08-28-am",
            "briefing_date": "2026-08-28",
            "slot": "AM",
            "generation_id": self.generation,
            "source_commit": self.source_commit,
            "source_refs": [{
                "path": "evidence/source.json",
                "sha256": self.source_sha,
                "generation_id": self.generation,
            }],
            "claims": [
                {
                    "claim_id": "fact-1", "kind": "FACT",
                    "statement": "The retained source value is 7.",
                    "status": "VERIFIED",
                    "source_ref_paths": ["evidence/source.json"],
                },
                {
                    "claim_id": "unknown-1", "kind": "UNKNOWN",
                    "statement": "Price authority is unavailable.",
                    "status": "UNKNOWN", "source_ref_paths": [],
                },
            ],
            "safety_attestation": producer.SAFETY_ATTESTATION,
        }
        ledger_path = input_dir / "claim-ledger.json"
        ledger_bytes = _write_json(ledger_path, ledger)
        display = {
            "schema_version": "portal_display_proposal/1",
            "briefing_id": ledger["briefing_id"],
            "changes": [{
                "path": "generated/atlas-public-snapshot.json",
                "content": {
                    "briefing_id": ledger["briefing_id"],
                    "summary": "No money action; one unknown remains.",
                    "authority": {"order_authority": False, "trading_authority": False},
                },
            }],
        }
        display_path = input_dir / "display-proposal.json"
        display_bytes = _write_json(display_path, display)
        corrections = ([{"kind": "POST_DELIVERY", "summary": "corrected"}]
                       if post_delivery is not None else [])
        report = {
            "schema_version": "briefing_validation_report/1",
            "briefing_id": ledger["briefing_id"],
            "briefing_date": ledger["briefing_date"],
            "slot": ledger["slot"],
            "generation_id": ledger["generation_id"],
            "source_commit": ledger["source_commit"],
            "validated_at_kst": "2026-08-28T10:00:00+09:00",
            "completion_state": "VALIDATED",
            "verdict": "PASS_WITH_CORRECTION" if corrections else "PASS",
            "briefing_sha256": "0" * 64 if bad_briefing_hash else _sha(briefing),
            "claim_ledger_sha256": _sha(ledger_bytes),
            "display_proposal_sha256": _sha(display_bytes),
            "unknown_escalation": "ESCALATE",
            "corrections": corrections,
            "post_delivery": post_delivery,
            "safety_attestation": producer.SAFETY_ATTESTATION,
        }
        report_path = input_dir / "validation-report.json"
        _write_json(report_path, report)
        return argparse.Namespace(
            repo_root=str(self.repo), briefing=str(briefing_path),
            claim_ledger=str(ledger_path), validation_report=str(report_path),
            display_proposal=str(display_path),
            out_root="evidence/validated_briefing_portal",
        )

    def _dispatch_fixture(self):
        built = producer.build(self._inputs())
        attack_path = self.repo / ".github/scripts/dispatch_portal_projection.py"
        attack_path.parent.mkdir(parents=True)
        attack_path.write_text("raise RuntimeError('ATTACK_CODE_EXECUTED')\n")
        subprocess.run(
            ["git", "add", "evidence/validated_briefing_portal", ".github"],
            cwd=self.repo, check=True,
        )
        subprocess.run(["git", "commit", "-qm", "validated envelope plus hostile code"],
                       cwd=self.repo, check=True)
        envelope_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        bodies: dict[tuple[str, str], bytes] = {
            (self.source_commit, "evidence/source.json"): subprocess.check_output(
                ["git", "show", f"{self.source_commit}:evidence/source.json"], cwd=self.repo
            ),
            (envelope_commit, ".github/scripts/dispatch_portal_projection.py"):
                attack_path.read_bytes(),
        }
        root = self.repo / Path(built["envelope_path"]).parent
        for path in root.iterdir():
            bodies[(envelope_commit, path.relative_to(self.repo).as_posix())] = path.read_bytes()
        index = root.parent / "index.json"
        bodies[(envelope_commit, index.relative_to(self.repo).as_posix())] = index.read_bytes()
        client = FakeGitHubData(bodies, {self.source_commit, envelope_commit})
        return built, envelope_commit, client

    def test_builds_atomic_bundle_and_replay_is_no_change(self):
        args = self._inputs()
        first = producer.build(args)
        second = producer.build(args)
        self.assertEqual(first["result"], "APPLIED")
        self.assertEqual(second["result"], "NO_CHANGE")
        self.assertEqual(first["projection_id"], second["projection_id"])
        envelope = json.loads((self.repo / first["envelope_path"]).read_text())
        self.assertEqual(envelope["schema_version"], "portal_projection/2")
        self.assertEqual([row["claim_id"] for row in envelope["verified_facts"]], ["fact-1"])
        self.assertEqual([row["claim_id"] for row in envelope["unknown_blocked"]], ["unknown-1"])
        self.assertEqual(envelope["safety_attestation"], producer.SAFETY_ATTESTATION)
        index = json.loads((self.repo / "evidence/validated_briefing_portal/morning/2026-08-28/index.json").read_text())
        self.assertEqual(len(index["revisions"]), 1)

    def test_output_tree_symlink_cannot_redirect_publish_outside_repository(self):
        with tempfile.TemporaryDirectory() as outside_name:
            out_root = self.repo / "evidence/validated_briefing_portal"
            out_root.mkdir(parents=True)
            (out_root / "morning").symlink_to(
                Path(outside_name), target_is_directory=True
            )
            with self.assertRaisesRegex(
                producer.PortalProducerError, "OUTPUT_SLOT_OUTSIDE_REPOSITORY"
            ):
                producer.build(self._inputs())
            self.assertEqual(list(Path(outside_name).iterdir()), [])

    def test_replay_rejects_untrusted_index_envelope_path(self):
        args = self._inputs()
        producer.build(args)
        index_path = (
            self.repo
            / "evidence/validated_briefing_portal/morning/2026-08-28/index.json"
        )
        index = json.loads(index_path.read_text())
        index["revisions"][0]["envelope_path"] = "/etc/passwd"
        _write_json(index_path, index)
        with self.assertRaisesRegex(
            producer.PortalProducerError, "PORTAL_INDEX_ENVELOPE_PATH_INVALID"
        ):
            producer.build(args)

    def test_report_must_bind_exact_input_bytes(self):
        with self.assertRaisesRegex(producer.PortalProducerError, "BRIEFING_HASH_MISMATCH"):
            producer.build(self._inputs(bad_briefing_hash=True))

    def test_unknown_cannot_be_relabelled_verified_without_source(self):
        args = self._inputs()
        ledger_path = Path(args.claim_ledger)
        ledger = json.loads(ledger_path.read_text())
        ledger["claims"][1].update({"kind": "FACT", "status": "VERIFIED"})
        ledger_bytes = _write_json(ledger_path, ledger)
        report_path = Path(args.validation_report)
        report = json.loads(report_path.read_text())
        report["claim_ledger_sha256"] = _sha(ledger_bytes)
        report["unknown_escalation"] = "NONE"
        _write_json(report_path, report)
        with self.assertRaisesRegex(producer.PortalProducerError, "VERIFIED_FACT_SOURCE_MISSING"):
            producer.build(args)

    def test_post_delivery_change_without_signed_source_ruling_is_blocked(self):
        post_delivery = {
            "post_delivery_change_key": "b" * 64,
            "signed_ruling_path": "evidence/missing-ruling.json",
            "signed_ruling_sha256": "c" * 64,
            "redelivery": "FORBIDDEN",
        }
        with self.assertRaisesRegex(producer.PortalProducerError, "SIGNED_RULING_NOT_A_SOURCE_REF"):
            producer.build(self._inputs(post_delivery=post_delivery))

    def test_post_delivery_accepts_only_existing_anchored_ed25519_ruling(self):
        scripts = self.repo / ".github/scripts"
        scripts.mkdir(parents=True)
        shutil.copy(ROOT / ".github/scripts/briefing_finalization.py", scripts)
        shutil.copy(ROOT / ".github/scripts/atlas_ed25519.py", scripts)
        ed = _load("test_atlas_ed25519", ".github/scripts/atlas_ed25519.py")
        bf = _load("test_briefing_finalization", ".github/scripts/briefing_finalization.py")
        secret = bytes(range(32))
        public = ed.publickey(secret)
        config = self.repo / "config"
        config.mkdir()
        (config / "atlas_approval_pubkey.txt").write_text(public.hex() + "\n")
        change_key = "b" * 64
        message = bf.change_resolution_message(
            "2026-08-28-am", change_key, "NONE", "CIO", "Portal correction only"
        )
        ruling = {
            "contract_version": bf.CONTRACT_VERSION,
            "post_delivery_change_key": change_key,
            "capital_impact": "NONE",
            "resolved_by": "CIO",
            "action_taken": "Portal correction only",
            "signature": ed.sign(message, secret).hex(),
        }
        ruling_path = self.repo / "evidence/signed-ruling.json"
        ruling_bytes = _write_json(ruling_path, ruling)
        subprocess.run(["git", "add", ".github", "config", "evidence"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "signed correction ruling"], cwd=self.repo, check=True)
        self.source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        post_delivery = {
            "post_delivery_change_key": change_key,
            "signed_ruling_path": "evidence/signed-ruling.json",
            "signed_ruling_sha256": _sha(ruling_bytes),
            "redelivery": "FORBIDDEN",
        }
        args = self._inputs(post_delivery=post_delivery)
        ledger_path = Path(args.claim_ledger)
        ledger = json.loads(ledger_path.read_text())
        ledger["source_refs"].append({
            "path": "evidence/signed-ruling.json",
            "sha256": _sha(ruling_bytes),
            "generation_id": self.generation,
        })
        ledger_bytes = _write_json(ledger_path, ledger)
        report_path = Path(args.validation_report)
        report = json.loads(report_path.read_text())
        report["claim_ledger_sha256"] = _sha(ledger_bytes)
        _write_json(report_path, report)
        fingerprint = hashlib.sha256(public).hexdigest()
        with mock.patch.dict("os.environ", {"ATLAS_APPROVAL_PUBKEY_FINGERPRINT": fingerprint}):
            built = producer.build(args)
        self.assertEqual(built["result"], "APPLIED")
        envelope = json.loads((self.repo / built["envelope_path"]).read_text())
        self.assertEqual(envelope["safety_attestation"]["trading_authority"], False)
        manifest = json.loads((self.repo / built["envelope_path"]).with_name("bundle.json").read_text())
        self.assertEqual(manifest["post_delivery_change_key"], change_key)
        self.assertEqual(manifest["redelivery"], "FORBIDDEN")

        subprocess.run(
            ["git", "add", "evidence/validated_briefing_portal"],
            cwd=self.repo, check=True,
        )
        subprocess.run(
            ["git", "commit", "-qm", "valid signed correction projection"],
            cwd=self.repo, check=True,
        )
        envelope_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()
        bodies: dict[tuple[str, str], bytes] = {}
        for ref in ledger["source_refs"]:
            bodies[(self.source_commit, ref["path"])] = subprocess.check_output(
                ["git", "show", f"{self.source_commit}:{ref['path']}"], cwd=self.repo
            )
        revision_root = self.repo / Path(built["envelope_path"]).parent
        for path in revision_root.iterdir():
            bodies[(envelope_commit, path.relative_to(self.repo).as_posix())] = path.read_bytes()
        index_path = revision_root.parent / "index.json"
        bodies[(envelope_commit, index_path.relative_to(self.repo).as_posix())] = index_path.read_bytes()
        client = FakeGitHubData(bodies, {self.source_commit, envelope_commit})
        with mock.patch.dict(
            "os.environ", {"ATLAS_APPROVAL_PUBKEY_FINGERPRINT": fingerprint}
        ):
            payload = dispatcher.validate_dispatch_candidate(
                client, envelope_commit, built["envelope_path"],
                built["envelope_sha256"], "main", repo_root=self.repo,
            )
        self.assertEqual(
            payload["client_payload"]["projection_id"], built["projection_id"]
        )

    def test_dispatch_rebuilds_bundle_and_never_reads_attacker_script(self):
        built, envelope_commit, client = self._dispatch_fixture()
        payload = dispatcher.validate_dispatch_candidate(
            client, envelope_commit, built["envelope_path"],
            built["envelope_sha256"], "main",
        )
        self.assertNotEqual(envelope_commit, self.source_commit)
        self.assertEqual(payload["client_payload"]["envelope_commit"], envelope_commit)
        self.assertEqual(payload["client_payload"]["source_commit"], self.source_commit)
        self.assertNotIn(
            (envelope_commit, ".github/scripts/dispatch_portal_projection.py"),
            client.requested,
        )

    def test_dispatch_rejects_non_main_envelope_commit(self):
        built, envelope_commit, client = self._dispatch_fixture()
        client.ancestors.remove(envelope_commit)
        with self.assertRaisesRegex(dispatcher.DispatchError, "NON_MAIN_COMMIT_REJECTED"):
            dispatcher.validate_dispatch_candidate(
                client, envelope_commit, built["envelope_path"],
                built["envelope_sha256"], "main",
            )

    def test_dispatch_rejects_path_outside_immutable_prefix(self):
        _, envelope_commit, client = self._dispatch_fixture()
        with self.assertRaisesRegex(dispatcher.DispatchError, "ENVELOPE_PATH_PREFIX_REJECTED"):
            dispatcher.validate_dispatch_candidate(
                client, envelope_commit, ".github/scripts/dispatch_portal_projection.py",
                "a" * 64, "main",
            )

    def test_dispatch_rejects_bundle_or_index_lineage_tamper(self):
        built, envelope_commit, client = self._dispatch_fixture()
        bundle_path = built["envelope_path"].replace("portal-projection.json", "bundle.json")
        bundle = json.loads(client.bodies[(envelope_commit, bundle_path)])
        bundle["projection_id"] = "2026-08-28-AM-tampered"
        client.bodies[(envelope_commit, bundle_path)] = producer.canonical(bundle) + b"\n"
        with self.assertRaisesRegex(dispatcher.DispatchError, "BUNDLE_IDENTITY_MISMATCH"):
            dispatcher.validate_dispatch_candidate(
                client, envelope_commit, built["envelope_path"],
                built["envelope_sha256"], "main",
            )

    def test_dispatch_revalidates_post_delivery_signature_on_self_consistent_bundle(self):
        built, envelope_commit, client = self._dispatch_fixture()
        revision_root = built["envelope_path"].rsplit("/", 1)[0]
        report_path = f"{revision_root}/validation-report.json"
        ledger_path = f"{revision_root}/claim-ledger.json"
        display_path = f"{revision_root}/display-proposal.json"
        bundle_path = f"{revision_root}/bundle.json"
        index_path = f"{revision_root.rsplit('/', 1)[0]}/index.json"
        envelope_path = built["envelope_path"]

        ledger = json.loads(client.bodies[(envelope_commit, ledger_path)])
        report = json.loads(client.bodies[(envelope_commit, report_path)])
        display = json.loads(client.bodies[(envelope_commit, display_path)])
        report["verdict"] = "PASS_WITH_CORRECTION"
        report["corrections"] = [{"kind": "POST_DELIVERY", "summary": "forged"}]
        report["post_delivery"] = {
            "post_delivery_change_key": "b" * 64,
            "signed_ruling_path": "evidence/source.json",
            "signed_ruling_sha256": self.source_sha,
            "redelivery": "FORBIDDEN",
        }
        report_body = producer.canonical(report) + b"\n"
        envelope = producer.build_envelope(ledger, report, display)
        envelope_body = producer.canonical(envelope) + b"\n"
        envelope_sha = _sha(envelope_body)

        bundle = json.loads(client.bodies[(envelope_commit, bundle_path)])
        bundle["projection_id"] = envelope["projection_id"]
        bundle["post_delivery_change_key"] = "b" * 64
        for artifact in bundle["artifacts"]:
            if artifact["path"] == "validation-report.json":
                artifact.update({"sha256": _sha(report_body), "bytes": len(report_body)})
            elif artifact["path"] == "portal-projection.json":
                artifact.update({"sha256": envelope_sha, "bytes": len(envelope_body)})
        index = json.loads(client.bodies[(envelope_commit, index_path)])
        index["latest_projection_id"] = envelope["projection_id"]
        index["revisions"][0].update({
            "projection_id": envelope["projection_id"],
            "envelope_sha256": envelope_sha,
        })
        client.bodies[(envelope_commit, report_path)] = report_body
        client.bodies[(envelope_commit, envelope_path)] = envelope_body
        client.bodies[(envelope_commit, bundle_path)] = producer.canonical(bundle) + b"\n"
        client.bodies[(envelope_commit, index_path)] = producer.canonical(index) + b"\n"

        with self.assertRaisesRegex(
            dispatcher.DispatchError, "SIGNED_RULING_CHANGE_MISMATCH"
        ):
            dispatcher.validate_dispatch_candidate(
                client, envelope_commit, envelope_path, envelope_sha, "main"
            )

    def test_dispatch_workflow_executes_default_branch_code_only(self):
        workflow = (ROOT / ".github/workflows/dispatch-validated-portal-projection.yml").read_text()
        self.assertIn("github.ref == format('refs/heads/{0}'", workflow)
        self.assertIn("environment: atlas-portal-dispatch", workflow)
        self.assertIn("ref: ${{ github.event.repository.default_branch }}", workflow)
        self.assertNotIn("ref: ${{ inputs.envelope_commit }}", workflow)
        self.assertIn("persist-credentials: false", workflow)
        self.assertIn(
            "ATLAS_APPROVAL_PUBKEY_FINGERPRINT: ${{ secrets.ATLAS_APPROVAL_PUBKEY_FINGERPRINT }}",
            workflow,
        )

    def test_retry_repairs_index_after_atomic_directory_publish(self):
        args = self._inputs()
        built = producer.build(args)
        index = self.repo / "evidence/validated_briefing_portal/morning/2026-08-28/index.json"
        index.unlink()
        replay = producer.build(args)
        self.assertEqual(replay["result"], "NO_CHANGE")
        self.assertEqual(replay["projection_id"], built["projection_id"])
        repaired = json.loads(index.read_text())
        self.assertEqual(len(repaired["revisions"]), 1)


if __name__ == "__main__":
    unittest.main()
