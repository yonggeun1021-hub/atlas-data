#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from acceptance import capital_rotation_e2e as acceptance
from acceptance import fail_closed_observation_receipt as fail_closed
from acceptance import portal_observation_receipt as portal


class CapitalRotationAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.morning_path = "evidence/scheduled_briefing_retrieval/2026-08-26/morning/rev-001.json"
        cls.evening_path = "evidence/scheduled_briefing_retrieval/2026-08-26/evening/rev-001.json"
        cls.morning_envelope = json.loads((ROOT / cls.morning_path).read_text())
        cls.evening_envelope = json.loads((ROOT / cls.evening_path).read_text())

    def receipt(self, slot: str, run_id: int, event_name="schedule", schedule=None):
        envelope = self.morning_envelope if slot == "morning" else self.evening_envelope
        path = self.morning_path if slot == "morning" else self.evening_path
        schedule = acceptance.SCHEDULES[slot] if schedule is None else schedule
        return acceptance.build_run_receipt(
            ROOT,
            event_name=event_name,
            event_schedule=schedule,
            run_id=run_id,
            run_attempt=1,
            workflow_head_sha="1" * 40,
            decision_date="2026-08-26",
            slot=slot,
            source_commit=envelope["source_commit"],
            authority_path=path,
        )

    @staticmethod
    def write_run(root: Path, receipt: dict):
        path = acceptance._run_path(root, receipt)
        acceptance._write_append_only(path, receipt)
        return path

    @staticmethod
    def write_json(path: Path, value: dict):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")

    @staticmethod
    def portal_slot(run_receipt: dict):
        github = run_receipt["github"]
        delivery = run_receipt["delivery"]
        return {
            "slot": run_receipt["slot"],
            "run_id": github["run_id"],
            "run_attempt": github["run_attempt"],
            "workflow_head_sha": github["workflow_head_sha"],
            "source_commit": run_receipt["source_commit"],
            "generation_id": run_receipt["generation_id"],
            "packet_sha256": delivery["packet_sha256"],
            "briefing_sha256": delivery["briefing_sha256"],
        }

    @staticmethod
    def portal_receipt(
        event_name="schedule",
        event_schedule=portal.OBSERVER_SCHEDULE,
        *,
        slots=None,
        observer_run_id=301,
    ):
        observer = {
            "workflow": portal.OBSERVER_WORKFLOW,
            "event_name": event_name,
            "event_schedule": event_schedule,
            "run_id": observer_run_id,
            "run_attempt": 1,
            "workflow_head_sha": "4" * 40,
            "observed_at_utc": "2026-08-26T23:05:00Z",
        }
        value = {
            "schema_version": "portal_projection_observation/1",
            "wbs_item": "P8-15",
            "sample_qualification": (
                "MANUAL_DIAGNOSTIC_EXCLUDED"
                if event_name == "workflow_dispatch"
                else "NATURAL_SCHEDULED_PORTAL_OBSERVATION"
            ),
            "observer": observer,
            "site": {
                "url": "https://atlas-investment-console.yonggeun1021.chatgpt.site",
                "portal_source_commit": "5" * 40,
                "api_url": "https://atlas-investment-console.yonggeun1021.chatgpt.site/api/v1/atlas/scheduled-briefing",
                "api_sha256": "6" * 64,
                "page_url": "https://atlas-investment-console.yonggeun1021.chatgpt.site/briefing",
                "page_html_sha256": "7" * 64,
            },
            "natural_pair": {
                "decision_date": "2026-08-26",
                "atlas_discovery_commit": "8" * 40,
                "slots": slots or [
                    {
                        "slot": slot,
                        "run_id": run_id,
                        "run_attempt": 1,
                        "workflow_head_sha": "9" * 40,
                        "source_commit": "a" * 40,
                        "generation_id": "b" * 64,
                        "packet_sha256": "c" * 64,
                        "briefing_sha256": "d" * 64,
                    }
                    for slot, run_id in (("morning", 201), ("evening", 202))
                ],
            },
            "completion_state": "VIEWER_HTML_AND_API_PAIR_VALIDATED",
            "authority": dict(portal.RECEIPT_AUTHORITY),
        }
        value["receipt_sha256"] = portal.payload_sha256(value, "receipt_sha256")
        return value

    @staticmethod
    def write_portal_package(root: Path, receipt: dict):
        package = portal._package_path(root, receipt)
        package.mkdir(parents=True)
        receipt_bytes = (json.dumps(receipt, indent=2, sort_keys=True) + "\n").encode()
        bundle = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}\n'
        trusted_root = b'{"mediaType":"application/vnd.dev.sigstore.trustedroot+json;version=0.1"}\n'
        imported = {
            "schema_version": "portal_observation_import/1",
            "wbs_item": "P8-15",
            "source_repository": portal.PORTAL_REPOSITORY,
            "source_commit": "e" * 40,
            "source_commit_role": "DISCOVERY_COMMIT_NOT_ATTESTATION_IDENTITY",
            "source_path": (
                "evidence/p8-15/portal-observations/2026-08-26/"
                f"run-{receipt['observer']['run_id']}-attempt-{receipt['observer']['run_attempt']}.json"
            ),
            "receipt_sha256": portal.bytes_sha256(receipt_bytes),
            "attestation_bundle_sha256": portal.bytes_sha256(bundle),
            "trusted_root_sha256": portal.bytes_sha256(trusted_root),
            "attestation_policy": {
                "predicate_type": "https://slsa.dev/provenance/v1",
                "repository": portal.PORTAL_REPOSITORY,
                "signer_workflow": portal.SIGNER_WORKFLOW,
                "source_digest": receipt["observer"]["workflow_head_sha"],
                "source_ref": "refs/heads/main",
                "self_hosted_runners_allowed": False,
                "online_verification_performed": True,
                "offline_bundle_reverification": True,
            },
            "importer": {
                "workflow": "Import P8-15 Portal Observation",
                "event_name": "schedule",
                "event_schedule": "20 23 * * 1-5",
                "run_id": 401,
                "run_attempt": 1,
                "workflow_head_sha": "f" * 40,
            },
            "authority": dict(portal.IMPORT_AUTHORITY),
        }
        imported["import_record_sha256"] = portal.payload_sha256(imported, "import_record_sha256")
        (package / "receipt.json").write_bytes(receipt_bytes)
        (package / "attestation.jsonl").write_bytes(bundle)
        (package / "trusted_root.jsonl").write_bytes(trusted_root)
        (package / "import.json").write_text(json.dumps(imported, indent=2, sort_keys=True) + "\n")
        return package

    @staticmethod
    def fail_closed_receipt(
        *,
        observer_run_id=501,
        upstream_run_id=401,
        upstream_run_attempt=1,
        conclusion="failure",
    ):
        head = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
        status, value = fail_closed.build_receipt(
            ROOT,
            observer_event="workflow_run",
            observer_run_id=observer_run_id,
            observer_run_attempt=1,
            observer_head_sha=head,
            upstream_workflow_name=fail_closed.UPSTREAM_WORKFLOW_NAME,
            upstream_workflow_path=fail_closed.UPSTREAM_WORKFLOW_PATH,
            upstream_event="schedule",
            upstream_conclusion=conclusion,
            upstream_run_id=upstream_run_id,
            upstream_run_attempt=upstream_run_attempt,
            upstream_head_sha=head,
            upstream_started_at="2026-08-27T22:05:00Z",
            upstream_completed_at="2026-08-27T22:06:00Z",
        )
        if status != "GENUINE_SCHEDULED_FAIL_CLOSED_RUN" or value is None:
            raise AssertionError(status)
        return value

    @staticmethod
    def write_fail_closed_package(root: Path, value: dict, trusted_root: bytes):
        receipt_path, _ = fail_closed.prepare_package(root, value)
        package = receipt_path.parent
        bundle = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}\n'
        (package / "attestation.jsonl").write_bytes(bundle)
        (package / "trusted_root.jsonl").write_bytes(trusted_root)
        observed = fail_closed._observation_record(
            value, receipt_path.read_bytes(), bundle, trusted_root
        )
        (package / "observation.json").write_text(
            json.dumps(observed, indent=2, sort_keys=True) + "\n"
        )
        return package

    def test_real_retrieval_envelope_builds_natural_receipt(self):
        receipt = self.receipt("evening", 101)
        validated = acceptance.validate_run_receipt(ROOT, receipt)
        self.assertEqual(validated["sample_qualification"], "NATURAL_SCHEDULED_RUN")
        self.assertEqual(validated["source_commit"], self.evening_envelope["source_commit"])
        self.assertEqual(validated["generation_id"], self.evening_envelope["generation_id"])

    def test_manual_dispatch_is_visible_but_excluded(self):
        receipt = self.receipt("evening", 102, "workflow_dispatch", "")
        self.assertEqual(receipt["sample_qualification"], "MANUAL_DIAGNOSTIC_EXCLUDED")
        acceptance.validate_run_receipt(ROOT, receipt)

    def test_unknown_schedule_never_becomes_natural(self):
        receipt = self.receipt("evening", 103, "schedule", "0 0 * * *")
        self.assertEqual(receipt["sample_qualification"], "NATURAL_PROVENANCE_NOT_COMPUTABLE")

    def test_qualification_tamper_rehashed_is_rejected(self):
        receipt = self.receipt("evening", 104, "workflow_dispatch", "")
        receipt["sample_qualification"] = "NATURAL_SCHEDULED_RUN"
        receipt["receipt_sha256"] = acceptance.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "QUALIFICATION_TAMPERED"):
            acceptance.validate_run_receipt(ROOT, receipt)

    def test_source_commit_tamper_rehashed_is_rejected(self):
        receipt = self.receipt("evening", 105)
        receipt["source_commit"] = "2" * 40
        receipt["receipt_sha256"] = acceptance.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "SOURCE_PINNED_VALIDATOR_UNAVAILABLE"):
            acceptance.validate_run_receipt(ROOT, receipt)

    def test_authority_bytes_tamper_is_rejected(self):
        receipt = self.receipt("evening", 106)
        receipt["retrieval_authority"]["sha256"] = "3" * 64
        receipt["receipt_sha256"] = acceptance.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "AUTHORITY_BYTES_MISMATCH"):
            acceptance.validate_run_receipt(ROOT, receipt)

    def test_append_only_same_bytes_noop_different_bytes_conflict(self):
        with tempfile.TemporaryDirectory() as name:
            target = Path(name) / "receipt.json"
            value = self.receipt("evening", 107)
            self.assertTrue(acceptance._write_append_only(target, value))
            self.assertFalse(acceptance._write_append_only(target, value))
            changed = copy.deepcopy(value)
            changed["github"]["run_attempt"] = 2
            changed["receipt_sha256"] = acceptance.payload_sha256(changed, "receipt_sha256")
            with self.assertRaisesRegex(acceptance.AcceptanceError, "APPEND_ONLY_CONFLICT"):
                acceptance._write_append_only(target, changed)

    def test_one_real_natural_pair_counts_once_but_gate_stays_not_ready(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root, portal_root, fail_root = base / "runs", base / "portal", base / "fail"
            self.write_run(run_root, self.receipt("morning", 201))
            self.write_run(run_root, self.receipt("evening", 202))
            result = acceptance.build_inventory(ROOT, run_root=run_root, portal_root=portal_root, fail_root=fail_root)
            self.assertEqual(result["observed"]["natural_pair_dates"], ["2026-08-26"])
            self.assertEqual(result["status"], "NOT_READY")

    def test_manual_receipt_never_completes_pair(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root = base / "runs"
            self.write_run(run_root, self.receipt("morning", 203))
            self.write_run(run_root, self.receipt("evening", 204, "workflow_dispatch", ""))
            result = acceptance.build_inventory(ROOT, run_root=run_root, portal_root=base / "portal", fail_root=base / "fail")
            self.assertEqual(result["observed"]["natural_pair_dates"], [])
            self.assertEqual(result["observed"]["manual_or_non_schedule_receipt_count"], 1)

    def test_duplicate_natural_slot_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root = base / "runs"
            self.write_run(run_root, self.receipt("morning", 205))
            self.write_run(run_root, self.receipt("morning", 206))
            with self.assertRaisesRegex(acceptance.AcceptanceError, "DUPLICATE_NATURAL_SLOT_DISTINCT_RUNS"):
                acceptance.build_inventory(ROOT, run_root=run_root, portal_root=base / "portal", fail_root=base / "fail")

    def test_same_run_rerun_attempt_is_counted_once(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root = base / "runs"
            first = self.receipt("morning", 210)
            second = copy.deepcopy(first)
            second["github"]["run_attempt"] = 2
            second["receipt_sha256"] = acceptance.payload_sha256(second, "receipt_sha256")
            self.write_run(run_root, first)
            self.write_run(run_root, second)
            result = acceptance.build_inventory(ROOT, run_root=run_root, portal_root=base / "portal", fail_root=base / "fail")
            self.assertEqual(result["observed"]["natural_run_receipt_count"], 2)
            self.assertEqual(result["observed"]["superseded_natural_rerun_attempt_count"], 1)
            self.assertEqual(result["observed"]["natural_pair_dates"], [])

    def test_self_hashed_portal_receipt_is_rejected_until_trusted_producer_exists(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            self.write_json(base / "portal" / "forged.json", {"receipt_sha256": "a" * 64})
            with self.assertRaisesRegex(acceptance.AcceptanceError, "UNTRUSTED_PORTAL_RECEIPT_PRESENT"):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=base / "portal",
                    fail_root=base / "fail",
                )

    def test_github_attested_portal_pair_counts_only_with_matching_natural_pair(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root, portal_root = base / "runs", base / "portal"
            morning = self.receipt("morning", 201)
            evening = self.receipt("evening", 202)
            self.write_run(run_root, morning)
            self.write_run(run_root, evening)
            self.write_portal_package(
                portal_root,
                self.portal_receipt(
                    slots=[self.portal_slot(morning), self.portal_slot(evening)]
                ),
            )
            verified = []
            result = acceptance.build_inventory(
                ROOT,
                run_root=run_root,
                portal_root=portal_root,
                fail_root=base / "fail",
                portal_attestation_verifier=lambda *args: verified.append(args),
                portal_trusted_root_sha256=portal.bytes_sha256(
                    (portal_root / "2026-08-26" / "run-301-attempt-1" / "trusted_root.jsonl").read_bytes()
                ),
            )
            self.assertEqual(result["observed"]["viewer_visible_projected_pair_dates"], ["2026-08-26"])
            self.assertEqual(result["observed"]["portal_receipt_count"], 1)
            self.assertEqual(len(verified), 1)
            self.assertEqual(result["status"], "NOT_READY")

    def test_same_date_portal_pair_with_different_source_lineage_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root, portal_root = base / "runs", base / "portal"
            morning = self.receipt("morning", 201)
            evening = self.receipt("evening", 202)
            self.write_run(run_root, morning)
            self.write_run(run_root, evening)
            mismatched = self.portal_receipt(
                slots=[self.portal_slot(morning), self.portal_slot(evening)]
            )
            mismatched["natural_pair"]["slots"][0]["packet_sha256"] = "e" * 64
            mismatched["receipt_sha256"] = portal.payload_sha256(
                mismatched, "receipt_sha256"
            )
            self.write_portal_package(portal_root, mismatched)
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "PORTAL_RECEIPT_SOURCE_LINEAGE_MISMATCH",
            ):
                acceptance.build_inventory(
                    ROOT,
                    run_root=run_root,
                    portal_root=portal_root,
                    fail_root=base / "fail",
                    portal_attestation_verifier=lambda *args: None,
                    portal_trusted_root_sha256=portal.bytes_sha256(
                        (portal_root / "2026-08-26" / "run-301-attempt-1" / "trusted_root.jsonl").read_bytes()
                    ),
                )

    def test_reobserved_identical_portal_pair_counts_once(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            run_root, portal_root = base / "runs", base / "portal"
            morning = self.receipt("morning", 201)
            evening = self.receipt("evening", 202)
            slots = [self.portal_slot(morning), self.portal_slot(evening)]
            self.write_run(run_root, morning)
            self.write_run(run_root, evening)
            self.write_portal_package(
                portal_root, self.portal_receipt(slots=slots, observer_run_id=301)
            )
            self.write_portal_package(
                portal_root, self.portal_receipt(slots=slots, observer_run_id=302)
            )
            result = acceptance.build_inventory(
                ROOT,
                run_root=run_root,
                portal_root=portal_root,
                fail_root=base / "fail",
                portal_attestation_verifier=lambda *args: None,
                portal_trusted_root_sha256=portal.bytes_sha256(
                    (portal_root / "2026-08-26" / "run-301-attempt-1" / "trusted_root.jsonl").read_bytes()
                ),
            )
            self.assertEqual(
                result["observed"]["viewer_visible_projected_pair_dates"],
                ["2026-08-26"],
            )
            self.assertEqual(result["observed"]["portal_receipt_count"], 2)

    def test_conflicting_portal_pairs_for_same_date_fail_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            portal_root = base / "portal"
            first = self.portal_receipt(observer_run_id=301)
            second = self.portal_receipt(observer_run_id=302)
            second["natural_pair"]["atlas_discovery_commit"] = "f" * 40
            second["receipt_sha256"] = portal.payload_sha256(
                second, "receipt_sha256"
            )
            self.write_portal_package(portal_root, first)
            self.write_portal_package(portal_root, second)
            with self.assertRaisesRegex(
                acceptance.AcceptanceError, "PORTAL_RECEIPT_LINEAGE_CONFLICT"
            ):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=portal_root,
                    fail_root=base / "fail",
                    portal_attestation_verifier=lambda *args: None,
                    portal_trusted_root_sha256=portal.bytes_sha256(
                        (portal_root / "2026-08-26" / "run-301-attempt-1" / "trusted_root.jsonl").read_bytes()
                    ),
                )

    def test_portal_receipt_bundle_tamper_is_rejected_before_counting(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            package = self.write_portal_package(base / "portal", self.portal_receipt())
            (package / "attestation.jsonl").write_bytes(b"changed")
            with self.assertRaisesRegex(acceptance.AcceptanceError, "INVALID_TRUSTED_PORTAL_RECEIPT_PRESENT"):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=base / "portal",
                    fail_root=base / "fail",
                    portal_attestation_verifier=lambda *args: None,
                    portal_trusted_root_sha256=portal.bytes_sha256(b'{"mediaType":"application/vnd.dev.sigstore.trustedroot+json;version=0.1"}\n'),
                )

    def test_portal_attestation_command_pins_signer_source_and_hosted_runner(self):
        receipt = self.portal_receipt()
        command = portal._verification_command(
            Path("receipt.json"), Path("attestation.jsonl"), Path("trusted_root.jsonl"), receipt
        )
        self.assertIn(portal.SIGNER_WORKFLOW, command)
        self.assertIn(receipt["observer"]["workflow_head_sha"], command)
        self.assertIn("refs/heads/main", command)
        self.assertIn("--deny-self-hosted-runners", command)
        self.assertIn("--no-public-good", command)

    def test_portal_authority_promotion_fails_even_after_rehash(self):
        receipt = self.portal_receipt()
        receipt["authority"]["trading_authority"] = True
        receipt["receipt_sha256"] = portal.payload_sha256(receipt, "receipt_sha256")
        with self.assertRaisesRegex(portal.PortalReceiptError, "PORTAL_AUTHORITY_INVALID"):
            portal.validate_portal_receipt(receipt)

    def test_self_hashed_fail_closed_receipt_is_rejected_until_observer_exists(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            self.write_json(base / "fail" / "forged.json", {"receipt_sha256": "b" * 64})
            with self.assertRaisesRegex(acceptance.AcceptanceError, "UNTRUSTED_FAIL_CLOSED_RECEIPT_PRESENT"):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=base / "portal",
                    fail_root=base / "fail",
                )

    def test_attested_genuine_scheduled_failure_counts_once(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            trusted_root = b'{"trustedRoot":"fixture"}\n'
            self.write_fail_closed_package(
                base / "fail", self.fail_closed_receipt(), trusted_root
            )
            result = acceptance.build_inventory(
                ROOT,
                run_root=base / "runs",
                portal_root=base / "portal",
                fail_root=base / "fail",
                fail_closed_attestation_verifier=lambda *args: None,
                fail_closed_trusted_root_sha256=fail_closed.bytes_sha256(
                    trusted_root
                ),
            )
            self.assertEqual(result["observed"]["fail_closed_receipt_count"], 1)
            self.assertEqual(
                result["observed"]["genuine_scheduled_fail_closed_sample_count"],
                1,
            )
            self.assertNotIn(
                "GENUINE_SCHEDULED_FAIL_CLOSED_RECEIPT_MISSING",
                result["blockers"],
            )
            self.assertNotIn(
                "TRUSTED_FAIL_CLOSED_OBSERVER_WAITING_FOR_SAMPLE",
                result["blockers"],
            )

    def test_fail_closed_rerun_attempts_for_one_subject_count_once(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            trusted_root = b'{"trustedRoot":"fixture"}\n'
            self.write_fail_closed_package(
                base / "fail",
                self.fail_closed_receipt(
                    observer_run_id=501, upstream_run_attempt=1
                ),
                trusted_root,
            )
            self.write_fail_closed_package(
                base / "fail",
                self.fail_closed_receipt(
                    observer_run_id=502, upstream_run_attempt=2
                ),
                trusted_root,
            )
            result = acceptance.build_inventory(
                ROOT,
                run_root=base / "runs",
                portal_root=base / "portal",
                fail_root=base / "fail",
                fail_closed_attestation_verifier=lambda *args: None,
                fail_closed_trusted_root_sha256=fail_closed.bytes_sha256(
                    trusted_root
                ),
            )
            self.assertEqual(result["observed"]["fail_closed_receipt_count"], 2)
            self.assertEqual(
                result["observed"]["genuine_scheduled_fail_closed_sample_count"],
                1,
            )

    def test_conflicting_fail_closed_subject_lineage_fails_closed(self):
        with tempfile.TemporaryDirectory() as name:
            base = Path(name)
            trusted_root = b'{"trustedRoot":"fixture"}\n'
            self.write_fail_closed_package(
                base / "fail",
                self.fail_closed_receipt(
                    observer_run_id=501, upstream_run_attempt=1
                ),
                trusted_root,
            )
            self.write_fail_closed_package(
                base / "fail",
                self.fail_closed_receipt(
                    observer_run_id=502,
                    upstream_run_attempt=2,
                    conclusion="timed_out",
                ),
                trusted_root,
            )
            with self.assertRaisesRegex(
                acceptance.AcceptanceError,
                "FAIL_CLOSED_SUBJECT_LINEAGE_CONFLICT",
            ):
                acceptance.build_inventory(
                    ROOT,
                    run_root=base / "runs",
                    portal_root=base / "portal",
                    fail_root=base / "fail",
                    fail_closed_attestation_verifier=lambda *args: None,
                    fail_closed_trusted_root_sha256=fail_closed.bytes_sha256(
                        trusted_root
                    ),
                )

    def test_inventory_recomputed_validation_rejects_rehash_tamper(self):
        current = acceptance.build_inventory(ROOT)
        changed = copy.deepcopy(current)
        changed["status"] = "PASS"
        changed["inventory_sha256"] = acceptance.payload_sha256(changed, "inventory_sha256")
        with self.assertRaisesRegex(acceptance.AcceptanceError, "DRIFT_OR_TAMPER"):
            acceptance.validate_inventory(ROOT, changed)

    def test_all_money_and_trading_authority_remains_false(self):
        inventory = acceptance.build_inventory(ROOT)
        self.assertEqual(inventory["authority"], acceptance.AUTHORITY)
        self.assertTrue(inventory["authority"]["evidence_inventory_only"])
        self.assertFalse(any(value for key, value in inventory["authority"].items() if key != "evidence_inventory_only"))

    def test_workflow_binds_provenance_to_github_context_and_commits_receipt(self):
        text = (ROOT / ".github/workflows/daily-briefing.yml").read_text()
        self.assertIn("EVENT_NAME: ${{ github.event_name }}", text)
        self.assertIn("RUN_ID: ${{ github.run_id }}", text)
        self.assertIn("RUN_ATTEMPT: ${{ github.run_attempt }}", text)
        self.assertIn("WORKFLOW_HEAD_SHA: ${{ github.sha }}", text)
        self.assertNotIn("inputs.event_name", text)
        self.assertIn("publish-run-receipt", text)
        self.assertIn('git add "$RUN_RECEIPT_PATH"', text)
        self.assertIn('validate-inventory "$ACCEPTANCE_PATH"', text)

    def test_portal_import_workflow_is_scheduled_attestation_bound_and_fail_closed(self):
        text = (ROOT / ".github/workflows/import-p8-15-portal-observation.yml").read_text()
        self.assertIn('cron: "20 23 * * 1-5"', text)
        self.assertIn("attestations: read", text)
        self.assertIn("fetch-depth: 0", text)
        self.assertIn("acceptance.portal_observation_receipt", text)
        self.assertIn("acceptance.capital_rotation_e2e validate-inventory", text)
        self.assertIn('git push origin "HEAD:${{ github.event.repository.default_branch }}"', text)
        self.assertNotIn("--force", text)

    def test_committed_inventory_is_exact_rebuild_and_not_ready(self):
        value = json.loads((ROOT / "evidence/operational/capital_rotation_e2e_acceptance.json").read_text())
        self.assertEqual(acceptance.validate_inventory(ROOT, value), value)
        self.assertEqual(value["status"], "NOT_READY")
        self.assertEqual(value["schema_version"], "capital_rotation_e2e_acceptance/3")
        self.assertEqual(value["observed"]["portal_receipt_count"], 0)
        self.assertEqual(value["observed"]["natural_pair_dates"], [])


if __name__ == "__main__":
    unittest.main()
