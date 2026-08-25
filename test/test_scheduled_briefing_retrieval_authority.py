#!/usr/bin/env python3
"""P0-06 scheduled-consumer bootstrap authority regression."""

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github/scripts/publish_scheduled_briefing_authority.py"
SPEC = importlib.util.spec_from_file_location("publish_scheduled_briefing_authority", SCRIPT)
M = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(M)

DATE = "2026-08-25"
GENERATION = "2" * 64


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class AuthorityRepo:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "test"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        (self.root / "config").mkdir()
        (self.root / M.CONTRACT_PATH).write_bytes(
            (ROOT / M.CONTRACT_PATH).read_bytes()
        )
        (self.root / "config/read_model_authority_contract.json").write_bytes(
            (ROOT / "config/read_model_authority_contract.json").read_bytes()
        )
        self.write_generation(GENERATION)
        self.commit = self.commit_all("baseline")

    def write_generation(self, generation: str, date: str = DATE) -> None:
        meta = {"generation_id": generation, "generation_contract_version": 1}
        write_json(self.root / "data/briefing/step0_status.json", {
            "schema_version": 2,
            "expected_kst_date": date,
            "generation": meta,
        })
        write_json(self.root / "data/briefing_status.json", {
            "schema_version": 2,
            "expected_kst_date": date,
            "generation": meta,
        })

    def commit_all(self, message: str) -> str:
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", message], check=True)
        return subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

    def close(self):
        self.temp.cleanup()


class ScheduledBriefingRetrievalAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.repo = AuthorityRepo()

    def tearDown(self):
        self.repo.close()

    def build(self, slot="morning", date=DATE, commit=None):
        return M.build_envelope(
            self.repo.root, commit or self.repo.commit, slot, date
        )

    def test_bootstrap_is_unique_date_slot_and_artifacts_are_commit_pinned(self):
        envelope = self.build()
        self.assertEqual(
            envelope["bootstrap_path"],
            f"evidence/scheduled_briefing_retrieval/{DATE}/morning/rev-001.json",
        )
        self.assertIn("/main/evidence/scheduled_briefing_retrieval/", envelope["bootstrap_url"])
        for record in envelope["required_artifacts"]:
            self.assertIn(f"/{self.repo.commit}/", record["immutable_url"])
            self.assertNotIn("/main/", record["immutable_url"])
        for url in envelope["compact_immutable_url_templates"].values():
            self.assertIn(f"/{self.repo.commit}/", url)
            self.assertNotIn("/main/", url)

    def test_pointer_binds_date_generation_commit_and_stale_pass(self):
        envelope = self.build()
        self.assertEqual(envelope["expected_kst_date"], DATE)
        self.assertEqual(envelope["generation_id"], GENERATION)
        self.assertEqual(envelope["source_commit"], self.repo.commit)
        self.assertEqual(envelope["stale_detection"], "PASS")
        self.assertFalse(envelope["consumer_rules"]["floating_artifact_fallback_allowed"])
        self.assertFalse(envelope["consumer_rules"]["prior_date_fallback_allowed"])
        self.assertTrue(envelope["consumer_rules"]["bootstrap_query_nonce_required"])

    def test_all_investment_and_trading_authorities_remain_false(self):
        authority = self.build()["authority"]
        self.assertTrue(authority["retrieval_pointer_only"])
        self.assertFalse(any(v for k, v in authority.items() if k != "retrieval_pointer_only"))

    def test_short_uppercase_and_mutable_commits_are_rejected(self):
        for value in ("1" * 7, "A" * 40, "HEAD", "main", f"{self.repo.commit}~1"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(M.ScheduledAuthorityError, "SOURCE_COMMIT_NOT_IMMUTABLE"):
                    self.build(commit=value)

    def test_stale_step0_is_rejected_from_exact_commit(self):
        self.repo.write_generation(GENERATION, "2026-08-24")
        commit = self.repo.commit_all("stale")
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "SOURCE_ARTIFACT_STALE_DATE"):
            self.build(commit=commit)

    def test_health_from_another_generation_is_rejected(self):
        health = json.loads((self.repo.root / "data/briefing_status.json").read_text())
        health["generation"]["generation_id"] = "3" * 64
        write_json(self.repo.root / "data/briefing_status.json", health)
        commit = self.repo.commit_all("mixed-generation")
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "SOURCE_MIXED_GENERATION_READ"):
            self.build(commit=commit)

    def test_dirty_worktree_artifact_cannot_change_commit_bound_envelope(self):
        before = self.build()
        self.repo.write_generation("9" * 64)
        after = self.build()
        self.assertEqual(before, after)
        self.assertEqual(after["generation_id"], GENERATION)

    def test_first_publish_is_atomic_and_second_identical_publish_is_noop(self):
        path, changed = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        self.assertTrue(changed)
        self.assertTrue(path.is_file())
        parsed = json.loads(path.read_text())
        M.validate_envelope(self.repo.root, parsed)
        same_path, changed = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        self.assertEqual(path, same_path)
        self.assertFalse(changed)

    def test_same_slot_new_generation_appends_revision_without_overwrite(self):
        first, _ = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        first_bytes = first.read_bytes()
        self.repo.write_generation("4" * 64)
        new_commit = self.repo.commit_all("new-generation")
        second, changed = M.publish(self.repo.root, new_commit, "morning", DATE)
        self.assertTrue(changed)
        self.assertEqual(second.name, "rev-002.json")
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertEqual(json.loads(second.read_text())["revision"], 2)
        self.assertEqual(json.loads(second.read_text())["source_commit"], new_commit)

    def test_revision_gap_is_fail_closed(self):
        first, _ = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        first.rename(first.with_name("rev-002.json"))
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "BOOTSTRAP_REVISION_SEQUENCE_INVALID"):
            M.publish(self.repo.root, self.repo.commit, "morning", DATE)

    def test_same_generation_with_different_bytes_is_rejected(self):
        M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        step = json.loads((self.repo.root / "data/briefing/step0_status.json").read_text())
        step["extra_field_that_should_change_generation"] = "tampered"
        write_json(self.repo.root / "data/briefing/step0_status.json", step)
        new_commit = self.repo.commit_all("reused-generation")
        with self.assertRaisesRegex(
            M.ScheduledAuthorityError, "SOURCE_GENERATION_REUSED_WITH_DIFFERENT_BYTES"
        ):
            M.publish(self.repo.root, new_commit, "morning", DATE)

    def test_tampered_pointer_is_rejected_even_when_json_is_valid(self):
        path, _ = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        envelope = json.loads(path.read_text())
        envelope["generation_id"] = "5" * 64
        write_json(path, envelope)
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "ENVELOPE_DRIFT_OR_TAMPER"):
            M.validate_envelope(self.repo.root, envelope)

    def test_validation_binds_expected_commit_slot_date_and_path(self):
        path, _ = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        envelope = json.loads(path.read_text())
        M.validate_expected_identity(
            self.repo.root, envelope, path, self.repo.commit, "morning", DATE
        )
        cases = (
            (path, "f" * 40, "morning", DATE, "EXPECTED_IDENTITY_MISMATCH"),
            (path, self.repo.commit, "evening", DATE, "EXPECTED_IDENTITY_MISMATCH"),
            (path, self.repo.commit, "morning", "2026-08-24", "EXPECTED_IDENTITY_MISMATCH"),
            (path.with_name("other.json"), self.repo.commit, "morning", DATE, "PATH_IDENTITY_MISMATCH"),
        )
        for actual_path, commit, slot, date, code in cases:
            with self.subTest(code=code):
                with self.assertRaisesRegex(M.ScheduledAuthorityError, code):
                    M.validate_expected_identity(
                        self.repo.root, envelope, actual_path, commit, slot, date
                    )

    def test_slot_and_date_validation_precedes_path_construction(self):
        for slot, date, code in (
            ("night", DATE, "SLOT_UNSUPPORTED"),
            ("morning", "../../etc/passwd", "EXPECTED_KST_DATE_INVALID"),
            ("morning", "2026-99-99", "EXPECTED_KST_DATE_INVALID"),
        ):
            with self.subTest(slot=slot, date=date):
                with self.assertRaisesRegex(M.ScheduledAuthorityError, code):
                    self.build(slot=slot, date=date)

    def test_adapter_contract_cannot_escalate_authority(self):
        contract_path = self.repo.root / M.CONTRACT_PATH
        contract = json.loads(contract_path.read_text())
        contract["authority"]["trading_authority"] = True
        write_json(contract_path, contract)
        commit = self.repo.commit_all("authority-escalation")
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "ADAPTER_AUTHORITY_BOUNDARY_INVALID"):
            self.build(commit=commit)

    def test_authority_contract_cannot_omit_or_rename_a_boundary(self):
        for mutation in ("omit", "rename"):
            repo = AuthorityRepo()
            try:
                path = repo.root / M.CONTRACT_PATH
                contract = json.loads(path.read_text())
                if mutation == "omit":
                    contract["authority"].pop("trading_authority")
                else:
                    contract["authority"]["buy"] = "AUTHORIZED"
                write_json(path, contract)
                commit = repo.commit_all("authority-shape")
                with self.assertRaisesRegex(
                    M.ScheduledAuthorityError,
                    "ADAPTER_AUTHORITY_BOUNDARY_INVALID",
                ):
                    M.build_envelope(repo.root, commit, "morning", DATE)
            finally:
                repo.close()

    def test_adapter_contract_cannot_redirect_bootstrap_or_artifacts(self):
        for key, value, code in (
            ("bootstrap_url_template", "https://evil.example/{slot}", "BOOTSTRAP_URL_MISMATCH"),
            ("immutable_raw_url_template", "https://evil.example/{path}", "IMMUTABLE_URL_MISMATCH"),
        ):
            with self.subTest(key=key):
                repo = AuthorityRepo()
                try:
                    path = repo.root / M.CONTRACT_PATH
                    contract = json.loads(path.read_text())
                    contract[key] = value
                    write_json(path, contract)
                    commit = repo.commit_all("redirect")
                    with self.assertRaisesRegex(M.ScheduledAuthorityError, code):
                        M.build_envelope(repo.root, commit, "morning", DATE)
                finally:
                    repo.close()

    def test_morning_and_evening_have_distinct_append_only_paths(self):
        morning = self.build("morning")
        evening = self.build("evening")
        self.assertNotEqual(morning["bootstrap_path"], evening["bootstrap_path"])
        self.assertNotEqual(morning["bootstrap_url"], evening["bootstrap_url"])
        self.assertEqual(morning["source_commit"], evening["source_commit"])

    def test_artifact_hashes_recompute_from_git_blob_not_worktree(self):
        envelope = self.build()
        step = next(
            item for item in envelope["required_artifacts"]
            if item["path"] == "data/briefing/step0_status.json"
        )
        raw = subprocess.check_output([
            "git", "-C", str(self.repo.root), "show",
            f"{self.repo.commit}:data/briefing/step0_status.json",
        ])
        self.assertEqual(step["content_sha256"], __import__("hashlib").sha256(raw).hexdigest())
        self.assertEqual(step["git_blob_sha1"], M.read_model.git_blob_sha1(raw))


if __name__ == "__main__":
    unittest.main()
