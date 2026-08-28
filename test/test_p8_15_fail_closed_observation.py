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

from acceptance import fail_closed_observation_receipt as receipt


def head_sha() -> str:
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()


def build(**changes):
    values = {
        "observer_event": "workflow_run",
        "observer_run_id": 501,
        "observer_run_attempt": 1,
        "observer_head_sha": head_sha(),
        "upstream_workflow_name": receipt.UPSTREAM_WORKFLOW_NAME,
        "upstream_workflow_path": receipt.UPSTREAM_WORKFLOW_PATH,
        "upstream_event": "schedule",
        "upstream_conclusion": "failure",
        "upstream_run_id": 401,
        "upstream_run_attempt": 1,
        "upstream_head_sha": head_sha(),
        "upstream_started_at": "2026-08-27T22:05:00Z",
        "upstream_completed_at": "2026-08-27T22:06:00Z",
    }
    values.update(changes)
    return receipt.build_receipt(ROOT, **values)


class FailClosedObservationTests(unittest.TestCase):
    def test_only_genuine_scheduled_failure_builds_receipt(self):
        status, value = build()
        self.assertEqual(status, "GENUINE_SCHEDULED_FAIL_CLOSED_RUN")
        self.assertIsNotNone(value)
        self.assertEqual(receipt.validate_receipt(ROOT, value), value)
        self.assertEqual(value["subject"]["conclusion"], "failure")
        self.assertEqual(value["authority"], receipt.AUTHORITY)
        self.assertFalse(
            any(
                enabled
                for key, enabled in value["authority"].items()
                if key != "evidence_observation_only"
            )
        )

    def test_manual_success_cancel_and_non_observer_are_excluded(self):
        cases = [
            ({"upstream_event": "workflow_dispatch"}, "NON_SCHEDULE_UPSTREAM_EXCLUDED"),
            ({"upstream_conclusion": "success"}, "NON_FAILURE_UPSTREAM_EXCLUDED"),
            ({"upstream_conclusion": "cancelled"}, "NON_FAILURE_UPSTREAM_EXCLUDED"),
            ({"observer_event": "workflow_dispatch"}, "OBSERVER_EVENT_EXCLUDED"),
        ]
        for changes, expected in cases:
            with self.subTest(changes=changes):
                status, value = build(**changes)
                self.assertEqual(status, expected)
                self.assertIsNone(value)

    def test_timed_out_is_a_genuine_fail_closed_conclusion(self):
        status, value = build(upstream_conclusion="timed_out")
        self.assertEqual(status, "GENUINE_SCHEDULED_FAIL_CLOSED_RUN")
        self.assertEqual(receipt.validate_receipt(ROOT, value), value)

    def test_rehashed_manual_tamper_cannot_become_natural(self):
        _, value = build()
        changed = copy.deepcopy(value)
        changed["subject"]["event_name"] = "workflow_dispatch"
        changed["receipt_sha256"] = receipt.payload_sha256(
            changed, "receipt_sha256"
        )
        with self.assertRaisesRegex(
            receipt.FailClosedReceiptError, "FAIL_CLOSED_QUALIFICATION_INVALID"
        ):
            receipt.validate_receipt(ROOT, changed)

    def test_rehashed_workflow_bytes_tamper_is_rejected(self):
        _, value = build()
        changed = copy.deepcopy(value)
        changed["subject"]["workflow_sha256"] = "f" * 64
        changed["receipt_sha256"] = receipt.payload_sha256(
            changed, "receipt_sha256"
        )
        with self.assertRaisesRegex(
            receipt.FailClosedReceiptError, "PINNED_WORKFLOW_MISMATCH"
        ):
            receipt.validate_receipt(ROOT, changed)

    def test_observer_receipt_requires_exact_checked_out_event_sha(self):
        with self.assertRaisesRegex(
            receipt.FailClosedReceiptError,
            "FAIL_CLOSED_OBSERVER_CHECKOUT_MISMATCH",
        ):
            build(observer_head_sha="f" * 40)

    def test_attested_package_revalidates_and_binds_path(self):
        _, value = build()
        with tempfile.TemporaryDirectory() as name:
            fail_root = Path(name) / "fail"
            receipt_path, changed = receipt.prepare_package(fail_root, value)
            self.assertTrue(changed)
            package = receipt_path.parent
            bundle = b'{"mediaType":"application/vnd.dev.sigstore.bundle.v0.3+json"}\n'
            trusted_root = b'{"trustedRoot":"fixture"}\n'
            (package / "attestation.jsonl").write_bytes(bundle)
            (package / "trusted_root.jsonl").write_bytes(trusted_root)
            record = receipt._observation_record(
                value, receipt_path.read_bytes(), bundle, trusted_root
            )
            (package / "observation.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n"
            )
            calls = []

            def verifier(receipt_path, bundle_path, root_path, checked):
                calls.append((receipt_path, bundle_path, root_path, checked))

            checked = receipt.validate_package(
                ROOT,
                package,
                fail_root=fail_root,
                expected_trusted_root_sha256=receipt.bytes_sha256(trusted_root),
                verifier=verifier,
            )
            self.assertEqual(checked, value)
            self.assertEqual(len(calls), 1)
            self.assertEqual(receipt.iter_receipts(
                ROOT,
                fail_root,
                expected_trusted_root_sha256=receipt.bytes_sha256(trusted_root),
                verifier=verifier,
            ), [value])

    def test_package_byte_tamper_and_stray_json_fail_closed(self):
        _, value = build()
        with tempfile.TemporaryDirectory() as name:
            fail_root = Path(name) / "fail"
            receipt_path, _ = receipt.prepare_package(fail_root, value)
            package = receipt_path.parent
            bundle = b"bundle\n"
            trusted_root = b"root\n"
            (package / "attestation.jsonl").write_bytes(bundle)
            (package / "trusted_root.jsonl").write_bytes(trusted_root)
            record = receipt._observation_record(
                value, receipt_path.read_bytes(), bundle, trusted_root
            )
            (package / "observation.json").write_text(
                json.dumps(record, indent=2, sort_keys=True) + "\n"
            )
            changed = json.loads((package / "observation.json").read_text())
            changed["receipt_sha256"] = "0" * 64
            (package / "observation.json").write_text(
                json.dumps(changed, indent=2, sort_keys=True) + "\n"
            )
            with self.assertRaisesRegex(
                receipt.FailClosedReceiptError,
                "FAIL_CLOSED_OBSERVATION_DRIFT_OR_TAMPER",
            ):
                receipt.validate_package(
                    ROOT,
                    package,
                    fail_root=fail_root,
                    expected_trusted_root_sha256=receipt.bytes_sha256(trusted_root),
                    verifier=lambda *_: None,
                )
        with tempfile.TemporaryDirectory() as name:
            fail_root = Path(name)
            (fail_root / "forged.json").write_text("{}\n")
            with self.assertRaisesRegex(
                receipt.FailClosedReceiptError,
                "UNTRUSTED_FAIL_CLOSED_RECEIPT_PRESENT",
            ):
                receipt.iter_receipts(
                    ROOT,
                    fail_root,
                    expected_trusted_root_sha256="0" * 64,
                    verifier=lambda *_: None,
                )

    def test_workflow_uses_trusted_default_branch_and_pinned_attestation(self):
        text = (
            ROOT / ".github/workflows/observe-daily-briefing-fail-closed.yml"
        ).read_text()
        self.assertIn("workflow_run:", text)
        self.assertIn("github.event.workflow_run.event == 'schedule'", text)
        self.assertIn("github.event.workflow_run.conclusion == 'failure'", text)
        self.assertIn("GH_TOKEN: ${{ github.token }}", text)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertNotIn("ref: ${{ github.event.workflow_run.head_sha }}", text)
        self.assertIn(
            "actions/attest-build-provenance@"
            "4d101475d8b20a2381f78447822ac1eab6504dd8",
            text,
        )
        self.assertNotIn("workflow_dispatch:", text)


if __name__ == "__main__":
    unittest.main()
