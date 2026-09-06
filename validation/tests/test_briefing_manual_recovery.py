from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from briefing_core import manual_recovery
from briefing import daily_orchestrator


REPO_ROOT = Path(manual_recovery.__file__).resolve().parents[1]


class ManualRecoveryTest(unittest.TestCase):
    def test_appends_labeled_revision_and_exact_replay_is_no_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            date_root = root / "evidence/daily_briefing/morning/2026-09-02"
            base = date_root / "rev-001"
            base.mkdir(parents=True)
            packet = {
                "slot": "morning", "decision_date": "2026-09-02",
                "packet_sha256": "a" * 64,
                "component_status_counts": {"READY": 1},
            }
            (base / "packet.json").write_text(json.dumps(packet))
            (base / "briefing.md").write_text("# Atlas\n\nBase\n")
            (date_root / "index.json").write_text(json.dumps({
                "schema_version": 1, "latest_revision": 1,
                "revisions": [{
                    "revision": 1, "path": "rev-001",
                    "packet_sha256": "a" * 64,
                    "generated_at": "2026-09-02T00:00:00Z",
                    "component_status_counts": {"READY": 1},
                }],
            }))
            registry_path = Path("evidence/briefing_events/2026-09-02/morning/registry.json")
            registry_file = root / registry_path
            registry_file.parent.mkdir(parents=True)
            registry = {
                "schema_version": "major_event_registry/1",
                "briefing_date": "2026-09-02", "slot": "AM",
                "source_status": "AVAILABLE",
                "events": [{
                    "event_id": "event-1", "importance": "CRITICAL",
                    "detected_at": "2026-09-02T07:00:00+09:00",
                    "display_headline_ko": "검증된 핵심 사건",
                    "sources": [
                        {
                            "source_id": "source-1", "grade": "PRIMARY_OFFICIAL",
                            "title": "Official", "url": "https://example.com/official",
                            "published_at": "2026-09-01", "supports_claim_ids": ["fact-1"],
                        },
                        {
                            "source_id": "source-2", "grade": "INDEPENDENT_MAJOR_MEDIA",
                            "title": "Independent", "url": "https://example.com/independent",
                            "published_at": "2026-09-01", "supports_claim_ids": ["fact-1"],
                        },
                    ],
                    "claims": [
                        {"claim_id": "fact-1", "classification": "FACT", "statement_ko": "확인된 사실", "source_ids": ["source-1", "source-2"]},
                        {"claim_id": "inference-1", "classification": "INFERENCE", "statement_ko": "상황 평가", "source_ids": ["source-1"]},
                        {"claim_id": "unknown-1", "classification": "UNKNOWN", "statement_ko": "확인 불가", "source_ids": []},
                    ],
                    "transmission_channels": [{
                        "channel": channel, "classification": "INFERENCE",
                        "statement_ko": f"{channel} 전달 경로", "source_claim_ids": ["fact-1"],
                        "price_causality_confirmed": False,
                    } for channel in (
                        "oil_shipping", "hormuz", "usd_rates",
                        "equity_risk_appetite", "defense",
                    )],
                }],
            }
            registry_file.write_text(json.dumps(registry))
            with mock.patch.object(manual_recovery, "validate_packet"):
                first = manual_recovery.publish(
                    root, slot="morning", decision_date="2026-09-02",
                    generated_at="2026-09-02T01:00:00Z",
                    registry_path=registry_path.as_posix(),
                )
                second = manual_recovery.publish(
                    root, slot="morning", decision_date="2026-09-02",
                    generated_at="2026-09-02T01:00:00Z",
                    registry_path=registry_path.as_posix(),
                )
            self.assertEqual(first["result"], "APPLIED")
            self.assertEqual(second["result"], "NO_CHANGE")
            recovered = (date_root / "rev-002/briefing.md").read_text()
            self.assertIn("MANUAL_RECOVERY", recovered)
            self.assertIn("검증된 핵심 사건", recovered)
            manifest = json.loads((date_root / "rev-002/manual-recovery.json").read_text())
            self.assertEqual(manifest["sample_qualification"], "MANUAL_RECOVERY_NOT_NATURAL_SAMPLE")
            self.assertFalse(any(manifest["authority"].values()))
            self.assertEqual(manifest["recovered_briefing_sha256"], hashlib.sha256(recovered.encode()).hexdigest())


class ManualRecoveryHistoryContextTest(unittest.TestCase):
    """Recovery forwards trusted Flow history context; it never invents one.

    Recovering a briefing must not become a way to accept a packet whose Flow
    inputs cannot be proven, so the approved unreplayable failure is preserved
    all the way out through the STOP boundary.
    """

    def _base(self, root: Path, packet: dict) -> None:
        date_root = root / "evidence/daily_briefing/morning/2026-09-02"
        base = date_root / "rev-001"
        base.mkdir(parents=True)
        (base / "packet.json").write_text(json.dumps(packet))
        (base / "briefing.md").write_text("# Atlas\n\nBase\n")
        (date_root / "index.json").write_text(json.dumps({
            "schema_version": 1, "latest_revision": 1,
            "revisions": [{
                "revision": 1, "path": "rev-001",
                "packet_sha256": packet["packet_sha256"],
                "generated_at": "2026-09-02T00:00:00Z",
                "component_status_counts": {"READY": 1},
            }],
        }))

    @staticmethod
    def _legacy_shaped_packet() -> dict:
        """The smallest packet that reaches the Flow history boundary.

        No `flow_replay_version` marker, so validate_packet() classifies it as
        a legacy derivation -- which is the state under test. This is a
        boundary fixture and makes no claim about any real archived packet.
        """
        packet = {
            "slot": "morning",
            "decision_date": "2026-09-02",
            "generated_at": "2026-09-02T01:00:00Z",
            "component_status_counts": {"READY": 1},
            "frozen_sources": {"DYNAMIC_CLOCK": {}},
        }
        packet["packet_sha256"] = daily_orchestrator.payload_sha256(packet)
        return packet

    def test_context_is_forwarded_to_validation_and_defaults_to_the_repo(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._base(root, {"slot": "morning", "decision_date": "2026-09-02",
                              "packet_sha256": "a" * 64,
                              "component_status_counts": {"READY": 1}})
            with mock.patch.object(manual_recovery, "validate_packet") as validate:
                with self.assertRaises(Exception):
                    # Fails later, on the registry; validation already ran.
                    manual_recovery.publish(
                        root, slot="morning", decision_date="2026-09-02",
                        generated_at="2026-09-02T01:00:00Z",
                        registry_path="missing/registry.json",
                        historical_source_commit="a" * 40,
                        trusted_validation_head="b" * 40,
                    )
                kwargs = validate.call_args.kwargs
            self.assertEqual(kwargs["historical_source_commit"], "a" * 40)
            self.assertEqual(kwargs["trusted_validation_head"], "b" * 40)
            # Defaults to the repository being recovered, never to a value
            # read out of the packet.
            self.assertEqual(kwargs["trusted_repository_root"], root.resolve())

    def test_an_unreplayable_packet_stops_recovery_instead_of_succeeding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._base(root, self._legacy_shaped_packet())
            with self.assertRaisesRegex(
                daily_orchestrator.DailyOrchestratorError,
                "UNREPLAYABLE_FLOW_HISTORY_SOURCE_COMMIT_REQUIRED",
            ):
                manual_recovery.publish(
                    root, slot="morning", decision_date="2026-09-02",
                    generated_at="2026-09-02T01:00:00Z",
                    registry_path="evidence/registry.json",
                )
            # No recovery revision was appended and the index is untouched.
            date_root = root / "evidence/daily_briefing/morning/2026-09-02"
            self.assertFalse((date_root / "rev-002").exists())
            index = json.loads((date_root / "index.json").read_text())
            self.assertEqual(index["latest_revision"], 1)

    def test_the_stop_boundary_preserves_the_exact_error_code(self):
        """A failed validation is a STOP with its own code, not a traceback."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._base(root, self._legacy_shaped_packet())
            result = subprocess.run(
                [sys.executable, "briefing_core/manual_recovery.py",
                 "--repo-root", str(root),
                 "--slot", "morning",
                 "--decision-date", "2026-09-02",
                 "--generated-at", "2026-09-02T01:00:00Z",
                 "--registry-path", "evidence/registry.json"],
                cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            stderr = result.stderr.decode("utf-8", "replace")
            self.assertEqual(result.returncode, 2, stderr)
            self.assertTrue(stderr.startswith("STOP:"), stderr)
            self.assertIn(
                "UNREPLAYABLE_FLOW_HISTORY_SOURCE_COMMIT_REQUIRED", stderr
            )
            self.assertNotIn("Traceback", stderr)

    def test_the_cli_accepts_the_optional_history_arguments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._base(root, self._legacy_shaped_packet())
            result = subprocess.run(
                [sys.executable, "briefing_core/manual_recovery.py",
                 "--repo-root", str(root),
                 "--slot", "morning",
                 "--decision-date", "2026-09-02",
                 "--generated-at", "2026-09-02T01:00:00Z",
                 "--registry-path", "evidence/registry.json",
                 "--historical-source-commit", "0" * 40,
                 "--trusted-repository-root", str(REPO_ROOT),
                 "--trusted-validation-head", "HEAD"],
                cwd=REPO_ROOT, env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            )
            stderr = result.stderr.decode("utf-8", "replace")
            # The arguments parse and reach validation; an unknown commit is a
            # hard provenance failure, not a way in.
            self.assertEqual(result.returncode, 2, stderr)
            self.assertNotIn("unrecognized arguments", stderr)
            self.assertNotIn(
                "UNREPLAYABLE_FLOW_HISTORY_SOURCE_COMMIT_REQUIRED", stderr
            )
            self.assertIn("FLOW_REPLAY_SOURCE_COMMIT_OBJECT_MISSING", stderr)


if __name__ == "__main__":
    unittest.main()
