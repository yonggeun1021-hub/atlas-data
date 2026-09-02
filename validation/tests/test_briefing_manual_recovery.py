from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from briefing_core import manual_recovery


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


if __name__ == "__main__":
    unittest.main()
