#!/usr/bin/env python3
"""P0-06 scheduled-consumer bootstrap authority regression."""

from __future__ import annotations

import copy
import hashlib
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
PACKET_SHA = "7" * 64


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class AuthorityRepo:
    def __init__(self, decision_date: str = DATE, source_date: str | None = None, slot: str = "morning"):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.decision_date = decision_date
        self.source_date = source_date or decision_date
        self.slot = slot
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
        self.pre_delivery_commit = self.commit_all("read-model-before-h24")
        self.write_delivery()
        self.commit = self.commit_all("consumer-ready-h24")

    def write_generation(self, generation: str, date: str | None = None) -> None:
        date = date or self.source_date
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

    def write_delivery(
        self,
        slot: str | None = None,
        revision: int = 1,
        packet_sha: str = PACKET_SHA,
    ) -> None:
        slot = slot or self.slot
        base = self.root / f"evidence/daily_briefing/{slot}/{self.decision_date}"
        index_path = base / "index.json"
        revision_name = f"rev-{revision:03d}"
        packet_path = base / f"{revision_name}/packet.json"
        briefing_path = base / f"{revision_name}/briefing.md"
        packet = {
            "slot": slot,
            "decision_date": self.decision_date,
            "packet_sha256": packet_sha,
            "components": [
                {
                    "component_id": "STEP0_READ_MODEL_HEALTH",
                    "status": "DATA_BLOCKED",
                    "packet": {
                        "expected_kst_date": self.decision_date,
                        "sources": {
                            name: {"collected_for_kst_date": self.source_date}
                            for name in ("krx", "dart", "sec")
                        },
                    },
                },
                {
                    "component_id": "KRX_POST_CLOSE",
                    "status": "PENDING",
                    "reason": (
                        "WEEKEND_MORNING_MARKET_CLOSED_NO_NEW_SESSION_"
                        "LATEST_CONFIRMED_EVIDENCE"
                        if slot == "morning" and self.decision_date in {"2026-08-29", "2026-08-30"}
                        else "MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY"
                    ),
                },
            ],
        }
        prior_revisions = []
        if index_path.exists():
            prior_revisions = json.loads(index_path.read_text())["revisions"]
        if revision != len(prior_revisions) + 1:
            raise AssertionError("delivery revisions must be appended sequentially")
        revisions = prior_revisions + [{
            "revision": revision,
            "path": revision_name,
            "packet_sha256": packet_sha,
        }]
        write_json(index_path, {
            "schema_version": 1,
            "slot": slot,
            "decision_date": self.decision_date,
            "latest_revision": revision,
            "revisions": revisions,
        })
        write_json(packet_path, packet)
        briefing_path.parent.mkdir(parents=True, exist_ok=True)
        briefing = f"# briefing revision {revision}\n"
        if slot == "morning" and self.decision_date in {"2026-08-29", "2026-08-30"}:
            briefing += (
                "- market_session: MARKET_CLOSED\n"
                "- new_session: NONE\n"
                f"- latest_confirmed_evidence_date: {self.source_date}\n"
                "- latest_confirmed_evidence_relabelled_as_today: false\n"
            )
        briefing_path.write_text(briefing, encoding="utf-8")
        relative = lambda path: path.relative_to(self.root).as_posix()
        digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(self.root / "data/briefing/daily_briefing_sources.json", {
            "schema_version": "daily_briefing_delivery/1",
            "slot": slot,
            "decision_date": self.decision_date,
            "revision": revision,
            "index_path": relative(index_path),
            "index_sha256": digest(index_path),
            "packet_path": relative(packet_path),
            "packet_file_sha256": digest(packet_path),
            "packet_sha256": packet_sha,
            "briefing_path": relative(briefing_path),
            "briefing_sha256": digest(briefing_path),
            "delivery_scope": [
                "INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW",
                "SHADOW_ENTRY_REVIEW",
            ],
            "authority": {
                "stage": False, "buy": False, "action": False,
                "order": False, "production": False, "trading": False,
            },
        })

    def commit_all(self, message: str) -> str:
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", message], check=True)
        return subprocess.check_output(
            ["git", "-C", str(self.root), "rev-parse", "HEAD"], text=True
        ).strip()

    def commit_dynamic_clock_source(self, source: object, message: str) -> str:
        locator_path = self.root / "data/briefing/daily_briefing_sources.json"
        locator = json.loads(locator_path.read_text())
        packet_path = self.root / locator["packet_path"]
        packet = json.loads(packet_path.read_text())
        packet["frozen_sources"] = {"DYNAMIC_CLOCK": source}
        unsigned = copy.deepcopy(packet)
        unsigned.pop("packet_sha256", None)
        packet_sha = hashlib.sha256(
            M.canonical_json(unsigned).encode("utf-8")
        ).hexdigest()
        packet["packet_sha256"] = packet_sha
        write_json(packet_path, packet)

        index_path = self.root / locator["index_path"]
        index = json.loads(index_path.read_text())
        index["revisions"][-1]["packet_sha256"] = packet_sha
        write_json(index_path, index)

        locator["packet_sha256"] = packet_sha
        locator["packet_file_sha256"] = hashlib.sha256(
            packet_path.read_bytes()
        ).hexdigest()
        locator["index_sha256"] = hashlib.sha256(index_path.read_bytes()).hexdigest()
        write_json(locator_path, locator)
        return self.commit_all(message)

    def close(self):
        self.temp.cleanup()


class ScheduledBriefingRetrievalAuthorityTests(unittest.TestCase):
    def setUp(self):
        self.repo = AuthorityRepo()

    def tearDown(self):
        self.repo.close()

    def build(self, slot="morning", date=None, commit=None):
        return M.build_envelope(
            self.repo.root, commit or self.repo.commit, slot,
            date or self.repo.decision_date,
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
        self.assertEqual(envelope["delivery_locator"]["decision_date"], DATE)
        self.assertEqual(len(envelope["delivery_artifacts"]), 4)
        for record in envelope["delivery_artifacts"]:
            self.assertIn(f"/{self.repo.commit}/", record["immutable_url"])

    def test_legacy_packet_without_dynamic_clock_source_remains_readable(self):
        self.assertNotIn(
            "frozen_sources",
            json.loads(subprocess.check_output([
                "git", "-C", str(self.repo.root), "show",
                f"{self.repo.commit}:evidence/daily_briefing/morning/{DATE}/rev-001/packet.json",
            ])),
        )
        self.assertEqual(self.build()["delivery_locator"]["decision_date"], DATE)

    def test_present_dynamic_clock_source_requires_exact_identity(self):
        report = {"decision_date": DATE, "candidates": []}
        report_sha256 = hashlib.sha256(
            M.canonical_json(report).encode("utf-8")
        ).hexdigest()
        valid = {
            "kind": "report",
            "report_sha256": report_sha256,
            "report": report,
        }
        commit = self.repo.commit_dynamic_clock_source(valid, "valid-dynamic-clock")
        self.assertEqual(self.build(commit=commit)["source_commit"], commit)

        cases = (
            ({**valid, "kind": True}, "SOURCE_INVALID"),
            ({**valid, "report_sha256": True}, "SOURCE_INVALID"),
            ({**valid, "report_sha256": "0" * 64}, "SOURCE_SHA_MISMATCH"),
            ({**valid, "extra": None}, "SOURCE_INVALID"),
            ({"kind": "unavailable", "value": "alias"}, "SOURCE_INVALID"),
            ({"kind": "error", "value": True}, "SOURCE_INVALID"),
        )
        for source, code in cases:
            with self.subTest(code=code, source=source):
                repo = AuthorityRepo()
                try:
                    tampered_commit = repo.commit_dynamic_clock_source(
                        source, f"dynamic-clock-{code.lower()}"
                    )
                    with self.assertRaisesRegex(M.ScheduledAuthorityError, code):
                        M.build_envelope(
                            repo.root, tampered_commit, "morning", repo.decision_date
                        )
                finally:
                    repo.close()

        wrong_date_report = {"decision_date": "2026-08-24", "candidates": []}
        wrong_date = {
            "kind": "report",
            "report_sha256": hashlib.sha256(
                M.canonical_json(wrong_date_report).encode("utf-8")
            ).hexdigest(),
            "report": wrong_date_report,
        }
        repo = AuthorityRepo()
        try:
            tampered_commit = repo.commit_dynamic_clock_source(
                wrong_date, "dynamic-clock-wrong-date"
            )
            with self.assertRaisesRegex(
                M.ScheduledAuthorityError, "SOURCE_DATE_MISMATCH"
            ):
                M.build_envelope(
                    repo.root, tampered_commit, "morning", repo.decision_date
                )
        finally:
            repo.close()

    def test_prepublication_commit_without_h24_locator_cannot_be_advertised(self):
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "GIT_READ_FAILED"):
            self.build(commit=self.repo.pre_delivery_commit)

    def test_h24_locator_cannot_redirect_latest_revision_with_matching_files(self):
        locator_path = self.repo.root / "data/briefing/daily_briefing_sources.json"
        locator = json.loads(locator_path.read_text())
        locator["packet_path"] = locator["packet_path"].replace("rev-001", "rev-999")
        locator["briefing_path"] = locator["briefing_path"].replace("rev-001", "rev-999")
        for field in ("packet_path", "briefing_path"):
            source = self.repo.root / locator[field].replace("rev-999", "rev-001")
            target = self.repo.root / locator[field]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read_bytes())
        write_json(locator_path, locator)
        commit = self.repo.commit_all("redirected-h24")
        with self.assertRaisesRegex(M.ScheduledAuthorityError, "LATEST_REVISION_IDENTITY"):
            self.build(commit=commit)

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

    def test_saturday_morning_binds_exact_previous_friday_without_fallback(self):
        repo = AuthorityRepo("2026-08-29", "2026-08-28")
        try:
            envelope = M.build_envelope(
                repo.root, repo.commit, "morning", repo.decision_date
            )
            self.assertEqual(envelope["schema_version"], "scheduled_briefing_retrieval_authority/3")
            self.assertEqual(envelope["source_date_binding"], {
                "mode": "WEEKEND_MORNING_PREVIOUS_FRIDAY",
                "decision_date": "2026-08-29",
                "source_evidence_kst_date": "2026-08-28",
                "calendar_day_lag": 1,
                "market_session_semantics":
                    "MARKET_CLOSED_NO_NEW_SESSION_LATEST_CONFIRMED_EVIDENCE",
                "prior_date_fallback_used": False,
                "last_confirmed_evidence_relabelled_as_decision_date": False,
            })
            self.assertFalse(envelope["consumer_rules"]["prior_date_fallback_allowed"])
            M.validate_envelope(repo.root, envelope)
        finally:
            repo.close()

    def test_sunday_morning_binds_friday_with_two_day_lag(self):
        repo = AuthorityRepo("2026-08-30", "2026-08-28")
        try:
            envelope = M.build_envelope(
                repo.root, repo.commit, "morning", repo.decision_date
            )
            self.assertEqual(envelope["source_date_binding"]["calendar_day_lag"], 2)
            self.assertEqual(
                envelope["source_date_binding"]["source_evidence_kst_date"],
                "2026-08-28",
            )
        finally:
            repo.close()

    def test_weekend_exception_rejects_evening_future_and_non_friday_dates(self):
        cases = (
            ("2026-08-29", "2026-08-28", "evening"),
            ("2026-08-29", "2026-08-27", "morning"),
            ("2026-08-29", "2026-08-30", "morning"),
        )
        for decision_date, source_date, slot in cases:
            with self.subTest(decision_date=decision_date, source_date=source_date, slot=slot):
                repo = AuthorityRepo(decision_date, source_date, slot)
                try:
                    with self.assertRaisesRegex(
                        M.ScheduledAuthorityError, "SOURCE_ARTIFACT_STALE_DATE"
                    ):
                        M.build_envelope(repo.root, repo.commit, slot, decision_date)
                finally:
                    repo.close()

    def test_weekend_mixed_source_dates_are_rejected(self):
        repo = AuthorityRepo("2026-08-29", "2026-08-28")
        try:
            health_path = repo.root / "data/briefing_status.json"
            health = json.loads(health_path.read_text())
            health["expected_kst_date"] = "2026-08-27"
            write_json(health_path, health)
            commit = repo.commit_all("mixed-weekend-source-dates")
            with self.assertRaisesRegex(
                M.ScheduledAuthorityError, "SOURCE_ARTIFACT_DATE_MISMATCH"
            ):
                M.build_envelope(repo.root, commit, "morning", repo.decision_date)
        finally:
            repo.close()

    def test_weekend_briefing_must_explicitly_disclose_session_context(self):
        repo = AuthorityRepo("2026-08-29", "2026-08-28")
        try:
            locator_path = repo.root / "data/briefing/daily_briefing_sources.json"
            locator = json.loads(locator_path.read_text())
            briefing_path = repo.root / locator["briefing_path"]
            briefing_path.write_text("# missing weekend context\n", encoding="utf-8")
            locator["briefing_sha256"] = hashlib.sha256(briefing_path.read_bytes()).hexdigest()
            write_json(locator_path, locator)
            commit = repo.commit_all("missing-weekend-context")
            with self.assertRaisesRegex(
                M.ScheduledAuthorityError, "WEEKEND_BRIEFING_SESSION_CONTEXT_MISSING"
            ):
                M.build_envelope(repo.root, commit, "morning", repo.decision_date)
        finally:
            repo.close()

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

    def test_same_generation_new_delivery_appends_authority_revision(self):
        first, _ = M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        first_bytes = first.read_bytes()
        self.repo.write_delivery(revision=2, packet_sha="8" * 64)
        new_commit = self.repo.commit_all("same-generation-new-delivery")

        second, changed = M.publish(
            self.repo.root, new_commit, "morning", DATE
        )

        self.assertTrue(changed)
        self.assertEqual(second.name, "rev-002.json")
        self.assertEqual(first.read_bytes(), first_bytes)
        second_value = json.loads(second.read_text())
        self.assertEqual(second_value["revision"], 2)
        self.assertEqual(second_value["source_commit"], new_commit)
        self.assertEqual(second_value["delivery_locator"]["revision"], 2)
        M.validate_envelope(self.repo.root, second_value)

        same_path, changed = M.publish(
            self.repo.root, new_commit, "morning", DATE
        )
        self.assertFalse(changed)
        self.assertEqual(same_path, second)

    def test_same_delivery_revision_cannot_be_rewritten_as_new_authority(self):
        M.publish(self.repo.root, self.repo.commit, "morning", DATE)
        locator_path = self.repo.root / "data/briefing/daily_briefing_sources.json"
        locator = json.loads(locator_path.read_text())
        briefing_path = self.repo.root / locator["briefing_path"]
        briefing_path.write_text("# rewritten in place\n", encoding="utf-8")
        locator["briefing_sha256"] = hashlib.sha256(
            briefing_path.read_bytes()
        ).hexdigest()
        write_json(locator_path, locator)
        rewritten_commit = self.repo.commit_all("rewrite-same-delivery-revision")

        with self.assertRaisesRegex(
            M.ScheduledAuthorityError,
            "DELIVERY_REVISION_NOT_FORWARD_APPEND_ONLY",
        ):
            M.publish(self.repo.root, rewritten_commit, "morning", DATE)

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
        self.repo.write_delivery("evening")
        evening_commit = self.repo.commit_all("evening-delivery")
        evening = self.build("evening", commit=evening_commit)
        self.assertNotEqual(morning["bootstrap_path"], evening["bootstrap_path"])
        self.assertNotEqual(morning["bootstrap_url"], evening["bootstrap_url"])
        self.assertNotEqual(morning["source_commit"], evening["source_commit"])

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
