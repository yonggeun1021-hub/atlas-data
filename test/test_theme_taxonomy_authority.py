import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotation import theme_taxonomy_authority as TTA
from test.test_theme_taxonomy import TT, fixture


def canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


class AuthorityRepo:
    def __init__(self, root: Path, graph: dict, *, status: str = "RATIFIED",
                 effective_from: str = "2026-07-01T00:00:00Z",
                 effective_to=None, commit_at: str = "2026-08-01T00:00:00Z"):
        self.root = root
        self.registry_path = root / "config" / "theme_taxonomy_authority_registry.json"
        self.evidence_path = root / "evidence" / "approvals" / "theme-taxonomy.json"
        self.registry_path.parent.mkdir(parents=True)
        self.evidence_path.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Atlas Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "atlas@example.invalid"], check=True)
        graph_hash = TTA.payload_sha256(TTA.graph_payload(graph))
        determining = {
            "rule_id": "RULE.THEME.TEST",
            "rule_version": "VERSION.1",
            "approval_status": status,
            "ratified_at": "2026-07-15T00:00:00Z",
            "effective_from": effective_from,
            "effective_to": effective_to,
            "taxonomy_id": graph["taxonomy_id"],
            "approved_graph_payload_sha256": graph_hash,
        }
        evidence = {
            "schema_version": TTA.EVIDENCE_SCHEMA,
            "approved_full_payload_sha256": TTA.payload_sha256(determining),
            **determining,
        }
        evidence_bytes = canonical_bytes(evidence)
        self.evidence_path.write_bytes(evidence_bytes)
        self.record = {
            **determining,
            "approval_evidence_ref": "evidence/approvals/theme-taxonomy.json",
            "approval_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        }
        self.write_registry([self.record])
        self.commit("authority", commit_at)

    def write_registry(self, records: list[dict]):
        self.registry_path.write_bytes(canonical_bytes({
            "schema_version": TTA.REGISTRY_SCHEMA,
            "records": records,
        }))

    def commit(self, message: str, when: str):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-q", "-m", message],
            check=True, env=env,
        )

    def head(self) -> str:
        return subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True,
        ).strip()


class ThemeTaxonomyAuthorityTests(unittest.TestCase):
    def make_repo(self, **kwargs):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        graph = fixture()
        return graph, AuthorityRepo(Path(temp.name), graph, **kwargs)

    def test_default_repository_registry_is_committed_empty_and_fail_closed(self):
        registry = TTA.load_registry()
        self.assertEqual(registry["records"], [])
        result = TTA.resolve_graph_authority(fixture(), "2026-08-22")
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD")
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_exact_committed_record_and_evidence_can_authorize_membership_only(self):
        graph, repo = self.make_repo()
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORIZED")
        self.assertTrue(result["authority"]["theme_membership_activation_authorized"])
        for field, value in result["authority"].items():
            if field != "theme_membership_activation_authorized":
                self.assertFalse(value, field)

    def test_build_packet_opens_only_detached_unweighted_adapter_after_authority(self):
        graph, repo = self.make_repo()
        packet = TT.build_packet(graph, authority_registry_path=repo.registry_path)
        self.assertTrue(packet["theme_membership_authorized"])
        self.assertEqual(len(packet["global_asset_master_membership_adapter"]), 2)
        forbidden = {"weight", "rank", "score", "stage", "buy", "order"}
        for row in packet["global_asset_master_membership_adapter"]:
            self.assertTrue(forbidden.isdisjoint(row))
        self.assertFalse(packet["authority"]["rotation_score_authorized"])
        self.assertFalse(packet["authority"]["trading_authorized"])

    def test_self_declared_claim_cannot_replace_empty_registry(self):
        graph = fixture()
        graph["approval"]["decision_id"] = "DECISION.SELF.DECLARED"
        graph["approval"]["decision_sha256"] = "a" * 64
        result = TTA.resolve_graph_authority(graph, "2026-08-22")
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD")

    def test_graph_content_change_no_longer_matches_authority(self):
        graph, repo = self.make_repo()
        changed = copy.deepcopy(graph)
        changed["nodes"][0]["display_name"] = "Changed"
        result = TTA.resolve_graph_authority(changed, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD")

    def test_proposed_record_never_authorizes(self):
        graph, repo = self.make_repo(status="PROPOSED")
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_UNRATIFIED_RECORD")

    def test_registry_disk_tamper_is_rejected(self):
        graph, repo = self.make_repo()
        value = json.loads(repo.registry_path.read_text())
        value["records"][0]["effective_to"] = "2027-01-01T00:00:00Z"
        repo.write_registry(value["records"])
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED")

    def test_evidence_disk_tamper_is_rejected(self):
        graph, repo = self.make_repo()
        repo.evidence_path.write_text("{}\n", encoding="utf-8")
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")

    def test_missing_evidence_is_rejected(self):
        graph, repo = self.make_repo()
        repo.evidence_path.unlink()
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED")

    def test_backdated_claim_waits_for_real_commit_first_seen(self):
        graph, repo = self.make_repo(commit_at="2026-08-10T12:00:00Z")
        before = TTA.resolve_graph_authority(graph, "2026-08-09", repo.registry_path)
        same_day = TTA.resolve_graph_authority(graph, "2026-08-10", repo.registry_path)
        after = TTA.resolve_graph_authority(graph, "2026-08-11", repo.registry_path)
        self.assertEqual(before["status"], "AUTHORITY_NOT_COMPUTABLE_PIT_VIOLATION")
        self.assertEqual(same_day["status"], "AUTHORITY_NOT_COMPUTABLE_DATE_ONLY_PRECISION")
        self.assertEqual(after["status"], "AUTHORIZED")

    def test_expired_record_is_not_active(self):
        graph, repo = self.make_repo(effective_to="2026-08-02T00:00:00Z")
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_NO_ACTIVE_AUTHORITY_RECORD")

    def test_ambiguous_matching_records_fail_closed(self):
        graph, repo = self.make_repo()
        second = copy.deepcopy(repo.record)
        second["rule_id"] = "RULE.THEME.TEST.SECOND"
        determining = TTA.determining_payload(second)
        evidence = {"schema_version": TTA.EVIDENCE_SCHEMA,
                    "approved_full_payload_sha256": TTA.payload_sha256(determining),
                    **determining}
        evidence_path = repo.root / "evidence" / "approvals" / "theme-taxonomy-second.json"
        evidence_bytes = canonical_bytes(evidence)
        evidence_path.write_bytes(evidence_bytes)
        second["approval_evidence_ref"] = "evidence/approvals/theme-taxonomy-second.json"
        second["approval_evidence_sha256"] = hashlib.sha256(evidence_bytes).hexdigest()
        repo.write_registry([repo.record, second])
        repo.commit("ambiguous", "2026-08-02T00:00:00Z")
        result = TTA.resolve_graph_authority(graph, "2026-08-03", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_AMBIGUOUS_AUTHORITY_RECORD")

    def test_mutable_or_abbreviated_commit_pin_is_rejected(self):
        graph, repo = self.make_repo()
        for pin in ("HEAD", "main", repo.head()[:12]):
            with self.subTest(pin=pin):
                result = TTA.resolve_graph_authority(
                    graph, "2026-08-03", repo.registry_path, trusted_commit=pin,
                )
                self.assertEqual(
                    result["status"],
                    "AUTHORITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED",
                )

    def test_exact_full_commit_pin_is_accepted(self):
        graph, repo = self.make_repo()
        result = TTA.resolve_graph_authority(
            graph, "2026-08-03", repo.registry_path, trusted_commit=repo.head(),
        )
        self.assertEqual(result["status"], "AUTHORIZED")
        self.assertEqual(result["trusted_commit"], repo.head())

    def test_registry_schema_and_record_fields_are_closed(self):
        graph, repo = self.make_repo()
        value = json.loads(repo.registry_path.read_text())
        value["unexpected"] = True
        repo.registry_path.write_bytes(canonical_bytes(value))
        with self.assertRaisesRegex(TTA.ThemeTaxonomyAuthorityError, "REGISTRY_FIELDS_MISMATCH"):
            TTA.load_registry(repo.registry_path)

    def test_path_traversal_evidence_reference_is_invalid(self):
        graph, repo = self.make_repo()
        value = json.loads(repo.registry_path.read_text())
        value["records"][0]["approval_evidence_ref"] = "../outside.json"
        repo.registry_path.write_bytes(canonical_bytes(value))
        with self.assertRaisesRegex(TTA.ThemeTaxonomyAuthorityError, "APPROVAL_EVIDENCE_REF_INVALID"):
            TTA.load_registry(repo.registry_path)

    def test_registry_never_carries_stage_buy_order_or_trading_authority(self):
        value = json.loads(TTA.REGISTRY_PATH.read_text())
        text = json.dumps(value).lower()
        for token in ("stage", "buy", "action", "order", "production", "trading"):
            self.assertNotIn(token, text)


if __name__ == "__main__":
    unittest.main()
