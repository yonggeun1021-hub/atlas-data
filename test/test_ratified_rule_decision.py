#!/usr/bin/env python3
"""P5-02 exact external-authority provenance regression."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ratified_rule_decision", ROOT / "rules" / "ratified_rule_decision.py")
MODULE = importlib.util.module_from_spec(spec); spec.loader.exec_module(MODULE)
RULES = MODULE.load_rules(); CONTRACT = MODULE.load_contract()
REGISTRY = {row["rule_id"]: row for row in RULES["rules"]}


def results(result="PASS"):
    return [{"rule_id": rid, "subject": "TSM",
        "condition_text_sha256": REGISTRY[rid]["condition_text_sha256"], "result": result,
        "evidence_reference_ids": [f"evidence:{rid.lower()}"],
        "reason": "Externally reviewed against the canonical condition."}
        for rid in CONTRACT["required_rule_ids"]]


class AuthorityRepo:
    def __init__(self, rows=None, ratified_at="2026-08-24T00:01:00Z"):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.email", "atlas@test.invalid"], cwd=self.root, check=True)
        subprocess.run(["git", "config", "user.name", "Atlas Test"], cwd=self.root, check=True)
        self.rows = results() if rows is None else copy.deepcopy(rows)
        self.ref = "evidence/rules/approvals/p5-02-tsm-v1.json"
        self.path = self.root / self.ref; self.path.parent.mkdir(parents=True)
        determining = MODULE.determining_payload(
            self.rows, "a" * 64, "2026-08-24T00:00:00Z", "cio:human-review",
            "authority:p5-02-tsm-v1", RULES, CONTRACT)
        evidence = {"schema_version": "ratified_rule_authority_evidence/1",
            "approval_status": "RATIFIED", "authority_ref": "authority:p5-02-tsm-v1",
            "approved_by": "cio:human-review", "evaluated_at": "2026-08-24T00:00:00Z",
            "ratified_at": ratified_at,
            "approved_decision_payload_sha256": MODULE.payload_sha256(determining)}
        self.path.write_text(json.dumps(evidence, sort_keys=True) + "\n", encoding="utf-8")
        subprocess.run(["git", "add", self.ref], cwd=self.root, check=True)
        env = os.environ | {"GIT_AUTHOR_DATE": "2026-08-24T00:02:00Z", "GIT_COMMITTER_DATE": "2026-08-24T00:02:00Z"}
        subprocess.run(["git", "commit", "-qm", "ratify"], cwd=self.root, env=env, check=True)
        current = self.path.read_bytes()
        commit, first_at = MODULE._exact_first_seen(self.root, self.ref, current)
        self.binding = {"ref": self.ref,
            "sha256": __import__("hashlib").sha256(current).hexdigest(),
            "first_seen_commit": commit, "first_seen_at": first_at, "usable_from": first_at}

    def packet(self):
        return MODULE.build_packet(self.rows, "a" * 64, "2026-08-24T00:00:00Z",
            "cio:human-review", "authority:p5-02-tsm-v1", self.binding,
            RULES, CONTRACT, self.root)

    def close(self):
        self.tmp.cleanup()


class RatifiedRuleDecisionTests(unittest.TestCase):
    def test_exact_committed_authority_envelope_is_required(self):
        repo = AuthorityRepo()
        try:
            value = repo.packet()
            self.assertEqual(value["schema_version"], "ratified_rule_decision_packet/2")
            self.assertEqual(MODULE.validate_packet(value, RULES, CONTRACT, repo.root), value)
            self.assertFalse(value["authority"]["action_authorized"])
        finally: repo.close()

    def test_partial_or_unknown_slice_is_rejected(self):
        repo = AuthorityRepo()
        try:
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "RESULT_RULE_SET_INVALID"):
                MODULE.build_packet(results()[:-1], "a"*64, "2026-08-24T00:00:00Z",
                    "cio:human-review", "authority:p5-02-tsm-v1", repo.binding,
                    RULES, CONTRACT, repo.root)
            rows = results(); rows[0]["result"] = "UNKNOWN"
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "RESULT_INVALID"):
                MODULE.build_packet(rows, "a"*64, "2026-08-24T00:00:00Z",
                    "cio:human-review", "authority:p5-02-tsm-v1", repo.binding,
                    RULES, CONTRACT, repo.root)
        finally: repo.close()

    def test_bare_authority_string_can_no_longer_create_packet(self):
        with self.assertRaises(TypeError):
            MODULE.build_packet(results(), "a"*64, "2026-08-24T00:00:00Z",
                "cio:human-review", "authority:p5-02-tsm-v1")

    def test_result_change_and_self_rehash_cannot_reuse_approval(self):
        repo = AuthorityRepo()
        try:
            value = repo.packet(); value["results"][0]["result"] = "FAIL"
            value["summary"] = {"total": 7, "PASS": 6, "FAIL": 1}
            value["packet_sha256"] = MODULE.payload_sha256({k:v for k,v in value.items() if k != "packet_sha256"})
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "AUTHORITY_EVIDENCE_SEMANTIC_MISMATCH"):
                MODULE.validate_packet(value, RULES, CONTRACT, repo.root)
        finally: repo.close()

    def test_dirty_or_replaced_authority_file_is_rejected(self):
        repo = AuthorityRepo()
        try:
            value = repo.packet(); repo.path.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "AUTHORITY_EVIDENCE_DIRTY"):
                MODULE.validate_packet(value, RULES, CONTRACT, repo.root)
        finally: repo.close()

    def test_first_seen_and_usable_from_are_rederived(self):
        repo = AuthorityRepo()
        try:
            value = repo.packet()
            for key in ("first_seen_commit", "first_seen_at", "usable_from"):
                changed = copy.deepcopy(value)
                changed["authority_evidence"][key] = ("f"*40 if key == "first_seen_commit" else "2020-01-01T00:00:00Z")
                changed["packet_sha256"] = MODULE.payload_sha256({k:v for k,v in changed.items() if k != "packet_sha256"})
                with self.assertRaises(MODULE.RatifiedRuleDecisionError):
                    MODULE.validate_packet(changed, RULES, CONTRACT, repo.root)
        finally: repo.close()

    def test_ratification_cannot_be_later_than_first_commit(self):
        repo = AuthorityRepo(ratified_at="2026-08-24T00:03:00Z")
        try:
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "AUTHORITY_TIME_ORDER_INVALID"):
                repo.packet()
        finally: repo.close()

    def test_test_root_and_missing_git_history_are_not_operational_authority(self):
        repo = AuthorityRepo()
        try:
            value = repo.packet(); changed = copy.deepcopy(value)
            changed["authority_evidence"]["ref"] = "test/fixtures/fake.json"
            changed["packet_sha256"] = MODULE.payload_sha256({k:v for k,v in changed.items() if k != "packet_sha256"})
            with self.assertRaisesRegex(MODULE.RatifiedRuleDecisionError, "PATH_FORBIDDEN"):
                MODULE.validate_packet(changed, RULES, CONTRACT, repo.root)
        finally: repo.close()
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(MODULE.RatifiedRuleDecisionError):
                MODULE.validate_packet({}, RULES, CONTRACT, Path(folder))

    def test_default_operational_path_requires_canonical_rules_clean_at_head(self):
        source = (ROOT / "rules" / "ratified_rule_decision.py").read_text(encoding="utf-8")
        self.assertIn("_verify_canonical_rules_at_head()", source)
        self.assertIn("RULE_SSOT_DIRTY", source)
        self.assertIn("RULE_SSOT_HEAD_MISMATCH", source)


if __name__ == "__main__": unittest.main()
