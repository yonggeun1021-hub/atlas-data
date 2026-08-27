#!/usr/bin/env python3
"""P3-11 committed source-body intake and append-only publication regression."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "discovery" / "wildcard_operational_intake.py"
SPEC = importlib.util.spec_from_file_location("wildcard_operational_intake_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def git(root: Path, *args: str, env: dict | None = None) -> str:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    completed = subprocess.run(
        ["git", "-C", str(root), *args],
        text=True,
        capture_output=True,
        env=merged,
        check=False,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def commit(root: Path, message: str, when: str) -> str:
    git(root, "add", ".")
    return_value = git(
        root,
        "commit",
        "-m",
        message,
        env={"GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when},
    )
    if not return_value:
        raise AssertionError("commit failed")
    return git(root, "rev-parse", "HEAD")


def init_repo(root: Path, *, linked: bool = True) -> tuple[str, str]:
    git(root, "init", "-q")
    git(root, "config", "user.name", "Atlas Test")
    git(root, "config", "user.email", "atlas-test@example.com")
    for relative in (
        "config/wildcard_operational_intake_contract.json",
        "config/wildcard_discovery_contract.json",
    ):
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    source_path = root / "data/source/sec/test.txt"
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"official primary-source body\n")
    commit(root, "source body", "2026-08-19T09:00:00Z")

    source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
    evidence = (
        {
            "evidence_id": "EVIDENCE.WC.OPERATIONAL.1",
            "status": "EVIDENCE_LINKED",
            "claim_text": "Primary source contains an observed event outside the current Theme taxonomy.",
            "missing_reasons": [],
            "source_identity": {
                "source_id": "sec_edgar",
                "source_url": "https://www.sec.gov/Archives/edgar/data/1/test.txt",
                "source_sha256": source_sha,
                "available_at": "2026-08-19",
                "retrieved_at_utc": "2026-08-19T10:00:00Z",
            },
            "audit_provenance": {
                "record_locator": "data/source/sec/test.txt",
                "capture_kind": "PRIMARY_SOURCE",
            },
        }
        if linked
        else {
            "evidence_id": "EVIDENCE.WC.OPERATIONAL.1",
            "status": "EVIDENCE_UNRESOLVED",
            "claim_text": None,
            "missing_reasons": ["SOURCE_RECORD_NOT_YET_LINKED"],
            "source_identity": None,
            "audit_provenance": None,
        }
    )
    submission = {
        "submission_id": "WILDCARD.OPERATIONAL.1",
        "market": "US",
        "asset_id": "US:XNAS:TEST",
        "subject": "TEST",
        "observed_on": "2026-08-19",
        "theme_membership_status": "OUTSIDE_CURRENT_TAXONOMY",
        "theme_ids": [],
        "nominated_by": "research-observer",
        "nominated_at_utc": "2026-08-19T12:00:00Z",
        "nomination_authority": "OBSERVATION_ONLY",
        "submission_reason": "Not represented by the current Theme taxonomy.",
        "hypothesis": "Warrants evidence collection without investment-strength implication.",
        "evidence": [evidence],
    }
    relative = "data/intake/wildcard/operational-1.json"
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(submission, indent=2) + "\n", encoding="utf-8")
    head = commit(root, "wildcard submission", "2026-08-19T13:00:00Z")
    return head, relative


class WildcardOperationalIntakeTest(unittest.TestCase):
    def test_committed_linked_submission_publishes_case_with_exact_source_body(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root)
            value = MODULE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root, True
            )
            self.assertEqual(value["status"], "WILDCARD_OPERATIONAL_INTAKE_PUBLISHED")
            self.assertEqual(value["summary"], {
                "submission_count": 1,
                "case_count": 1,
                "pending_count": 0,
                "linked_source_body_count": 1,
            })
            self.assertEqual(
                value["source_body_lineage"][0]["exact_content_first_seen_at"],
                "2026-08-19T09:00:00Z",
            )
            self.assertEqual(MODULE.validate_envelope(value, root), value)

    def test_unresolved_submission_is_published_pending_not_promoted(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root, linked=False)
            value = MODULE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            self.assertEqual(value["summary"]["case_count"], 0)
            self.assertEqual(value["summary"]["pending_count"], 1)
            self.assertEqual(value["source_body_lineage"], [])

    def test_source_body_hash_and_locator_are_verified_not_self_reported(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root)
            submission_path = root / relative
            submission = json.loads(submission_path.read_text(encoding="utf-8"))
            submission["evidence"][0]["source_identity"]["source_sha256"] = "a" * 64
            submission_path.write_text(json.dumps(submission), encoding="utf-8")
            changed = commit(root, "bad source hash", "2026-08-19T13:30:00Z")
            with self.assertRaisesRegex(
                MODULE.WildcardOperationalIntakeError, "SOURCE_BODY_SHA_MISMATCH"
            ):
                MODULE.build_envelope([relative], changed, "2026-08-19T14:00:00Z", root)

            submission["evidence"][0]["audit_provenance"]["record_locator"] = "../escape"
            submission_path.write_text(json.dumps(submission), encoding="utf-8")
            changed = commit(root, "bad locator", "2026-08-19T13:40:00Z")
            with self.assertRaisesRegex(
                MODULE.WildcardOperationalIntakeError, "PATH_INVALID"
            ):
                MODULE.build_envelope([relative], changed, "2026-08-19T14:00:00Z", root)

    def test_source_first_seen_after_nomination_is_lookahead_and_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root)
            source_path = root / "data/source/sec/test.txt"
            source_path.write_bytes(b"later exact body\n")
            source_sha = hashlib.sha256(source_path.read_bytes()).hexdigest()
            submission_path = root / relative
            submission = json.loads(submission_path.read_text(encoding="utf-8"))
            submission["evidence"][0]["source_identity"]["source_sha256"] = source_sha
            submission_path.write_text(json.dumps(submission), encoding="utf-8")
            changed = commit(root, "future body", "2026-08-19T12:30:00Z")
            with self.assertRaisesRegex(
                MODULE.WildcardOperationalIntakeError, "SOURCE_BODY_PIT_ORDER_INVALID"
            ):
                MODULE.build_envelope([relative], changed, "2026-08-19T14:00:00Z", root)

    def test_full_sha_and_current_checkout_are_required_for_operational_publish(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root)
            for ref in ("HEAD", head[:8], "main"):
                with self.subTest(ref=ref), self.assertRaisesRegex(
                    MODULE.WildcardOperationalIntakeError,
                    "SOURCE_COMMIT_NOT_IMMUTABLE_FULL_SHA",
                ):
                    MODULE.build_envelope([relative], ref, "2026-08-19T14:00:00Z", root)
            (root / relative).write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.WildcardOperationalIntakeError,
                "CHECKOUT_PATH_NOT_AT_SOURCE_COMMIT",
            ):
                MODULE.build_envelope(
                    [relative], head, "2026-08-19T14:00:00Z", root, True
                )

    def test_envelope_rederivation_blocks_resigned_authority_or_case_tamper(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root)
            original = MODULE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            for name, mutate in (
                ("authority", lambda value: value["authority"].update(trading_authorized=True)),
                ("case", lambda value: value["packet"].update(case_count=0)),
            ):
                with self.subTest(name=name):
                    changed = copy.deepcopy(original)
                    mutate(changed)
                    changed.pop("payload_sha256")
                    changed["payload_sha256"] = MODULE.payload_sha256(changed)
                    with self.assertRaisesRegex(
                        MODULE.WildcardOperationalIntakeError,
                        "ENVELOPE_REDERIVATION_MISMATCH",
                    ):
                        MODULE.validate_envelope(changed, root)

    def test_publication_is_content_addressed_append_only_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = init_repo(root)
            value = MODULE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            first = MODULE.publish(value, root)
            original = first.read_bytes()
            second = MODULE.publish(value, root)
            self.assertEqual(first, second)
            self.assertEqual(first.read_bytes(), original)
            self.assertIn(value["payload_sha256"][:16], first.name)
            first.write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.WildcardOperationalIntakeError,
                "APPEND_ONLY_PUBLICATION_DRIFT",
            ):
                MODULE.publish(value, root)

    def test_workflow_is_manual_committed_path_only_and_no_provider_call(self):
        workflow = (ROOT / ".github/workflows/p3-11-wildcard-intake.yml").read_text(
            encoding="utf-8"
        )
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("workflow_dispatch", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("fetch-depth: 0", workflow)
        self.assertIn("data/intake/wildcard/*.json", workflow)
        self.assertNotIn("requests", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("curl ", workflow)
        self.assertNotIn("wget ", workflow)
        self.assertNotIn("--force", workflow)
        self.assertNotIn("--force-with-lease", workflow)

    def test_all_money_and_trading_authority_remain_false(self):
        authority = MODULE.load_contract()["authority"]
        self.assertTrue(authority["intake_validation_authorized"])
        self.assertTrue(authority["case_publication_authorized"])
        for key, value in authority.items():
            if key not in {"intake_validation_authorized", "case_publication_authorized"}:
                self.assertFalse(value, key)


if __name__ == "__main__":
    unittest.main()
