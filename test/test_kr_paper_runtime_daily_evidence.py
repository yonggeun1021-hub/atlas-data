#!/usr/bin/env python3
"""Offline regressions for KR PAPER daily evidence: calendar packets + history roll."""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import kr_paper_runtime_calendar_packets as PACKETS
from regime import kr_paper_runtime_history_extension as EXTENSION


ROOT_BUNDLE = ROOT / "evidence/regime/kr_information_system/2026-09-11"
ROOT_HISTORY = ROOT_BUNDLE / "history/common-v1-replay-through-2026-09-10.json"
ROOT_RECEIPT = ROOT_BUNDLE / "history/final-receipt.json"
# Frozen dated copy of the #696 publication (the rolling pointer may advance).
RATIFIED_DECISION = ROOT_BUNDLE / "decision.json"


def root_evidence():
    reference = (ROOT_BUNDLE / "KR_PAPER_REFERENCE_CANDIDATE.json").read_bytes()
    manifest = (ROOT_BUNDLE / "source-capture/manifest.json").read_bytes()
    responses = {
        str(path.relative_to(ROOT_BUNDLE / "source-capture")): path.read_bytes()
        for path in sorted((ROOT_BUNDLE / "source-capture/responses").glob("*.json"))
    }
    return reference, manifest, responses


class CalendarPacketTests(unittest.TestCase):
    def test_committed_packets_reproduce_from_official_capture(self):
        PACKETS.check_packets("2026-09-08", "2026-12-31")

    def test_statuses_come_from_capture_not_weekday_guess(self):
        self.assertEqual(PACKETS.load_packet(dt.date(2026, 9, 15))["calendar"]["status"], "OPEN_REGULAR")
        for closed in ("2026-09-24", "2026-09-25", "2026-10-05", "2026-10-09", "2026-12-25", "2026-12-31"):
            packet = PACKETS.load_packet(dt.date.fromisoformat(closed))
            self.assertEqual(packet["calendar"]["status"], "CLOSED", closed)

    def test_tampered_or_missing_packet_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / PACKETS.CALENDAR_ROOT).mkdir(parents=True)
            (root / PACKETS.CAPTURE_REF).write_bytes((ROOT / PACKETS.CAPTURE_REF).read_bytes())
            with self.assertRaisesRegex(PACKETS.CalendarPacketError, "CALENDAR_PACKET_MISSING"):
                PACKETS.load_packet(dt.date(2026, 9, 15), root)
            self.assertEqual(len(PACKETS.write_packets("2026-09-15", "2026-09-16", root)), 4)
            path = root / PACKETS.packet_paths(dt.date(2026, 9, 15))[0]
            path.write_bytes(path.read_bytes().replace(b"OPEN_REGULAR", b"CLOSED"))
            with self.assertRaisesRegex(PACKETS.CalendarPacketError, "BYTES_MISMATCH"):
                PACKETS.load_packet(dt.date(2026, 9, 15), root)
            with self.assertRaisesRegex(PACKETS.CalendarPacketError, "NO_OVERWRITE"):
                PACKETS.write_packets("2026-09-15", "2026-09-15", root)

    def test_capture_hash_is_pinned(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / PACKETS.CALENDAR_ROOT).mkdir(parents=True)
            (root / PACKETS.CAPTURE_REF).write_bytes(b"{}")
            with self.assertRaisesRegex(PACKETS.CalendarPacketError, "CAPTURE_HASH"):
                PACKETS.expected_packets("2026-09-15", "2026-09-15", root)


class HistoryExtensionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reference, manifest, responses = root_evidence()
        # The appended observation must be admitted by the unchanged bridge.
        BRIDGE.validate_natural_evidence(
            reference_raw=reference, manifest_raw=manifest, raw_responses=responses,
            expected={
                "reference_sha256": EXTENSION.sha256(reference),
                "manifest_sha256": EXTENSION.sha256(manifest),
                "source_contract_sha256": EXTENSION.sha256(BRIDGE.SOURCE_CONTRACT_PATH.read_bytes()),
                "leadership_policy_sha256": EXTENSION.sha256(BRIDGE.LEADERSHIP_POLICY_PATH.read_bytes()),
                "reference_policy_sha256": EXTENSION.sha256(BRIDGE.REFERENCE_POLICY_PATH.read_bytes()),
            },
        )
        cls.reference, cls.manifest = reference, manifest
        cls.observation = EXTENSION.observation_step(reference, manifest)
        cls.history_raw = ROOT_HISTORY.read_bytes()
        cls.receipt_raw = ROOT_RECEIPT.read_bytes()
        cls.validation = "a" * 64

    def extend(self, **changes):
        observation = copy.deepcopy(self.observation) | changes
        return EXTENSION.extend(
            self.history_raw, self.receipt_raw, observation, validation_sha256=self.validation
        )

    def test_observation_step_matches_live_step_identity(self):
        decision = json.loads(RATIFIED_DECISION.read_bytes())
        live = decision["aggregation"]["steps"][-1]
        self.assertEqual(self.observation["as_of_date"], "2026-09-11")
        self.assertEqual(self.observation["previous_date"], "2026-09-10")
        self.assertEqual(self.observation["packet_id"], live["packet_id"])
        self.assertEqual(self.observation["axis_directions"], live["axis_directions"])

    def test_rolled_window_passes_unchanged_bridge_validator(self):
        history, receipt = self.extend()
        report, steps = BRIDGE.validate_historical_replay(
            history, EXTENSION.sha256(history), receipt, EXTENSION.sha256(receipt)
        )
        self.assertEqual(report["step_count"], 28)
        self.assertEqual(report["steps"][0]["as_of_date"], "2026-08-04")
        self.assertEqual(report["steps"][-1]["as_of_date"], "2026-09-11")
        self.assertEqual(len(steps), 28)
        packet = json.loads(receipt)
        self.assertEqual(packet["derivation"]["dropped_session"], "2026-08-03")
        self.assertEqual(packet["derivation"]["predecessor_acceptance_sha256"], EXTENSION.sha256(self.receipt_raw))
        self.assertEqual(packet["derivation"]["appended_observation"]["validation_sha256"], self.validation)
        self.assertTrue(packet["integration_boundary"]["runtime_integration_requires_separate_ratification"])
        self.assertTrue(all(value is False for key, value in packet["authority"].items()
                            if key != "historical_replay_evidence_authorized"))

    def test_rolled_window_agrees_with_ratified_live_replay(self):
        history, _ = self.extend()
        rolled = json.loads(history)["steps"]
        live = json.loads(RATIFIED_DECISION.read_bytes())["aggregation"]["steps"]
        fields = ("as_of_date", "confirmed_regime", "raw_classification", "score", "hysteresis")
        self.assertEqual([{k: row[k] for k in fields} for row in rolled[-20:]],
                         [{k: row[k] for k in fields} for row in live[-20:]])

    def test_extension_is_deterministic_and_verifiable(self):
        first = self.extend()
        self.assertEqual(first, self.extend())
        EXTENSION.verify_extension(*first, self.history_raw, self.receipt_raw, self.observation)
        tampered = first[1].replace(b'"dropped_session": "2026-08-03"', b'"dropped_session": "2026-08-04"')
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "EXTENSION_BYTES_MISMATCH"):
            EXTENSION.verify_extension(first[0], tampered, self.history_raw, self.receipt_raw, self.observation)

    def test_gap_order_and_binding_fail_closed(self):
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "EXTENSION_CHAIN_GAP"):
            self.extend(previous_date="2026-09-09")
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "EXTENSION_ORDER_INVALID"):
            self.extend(as_of_date="2026-09-10")
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "VALIDATION_BINDING_MISSING"):
            EXTENSION.extend(self.history_raw, self.receipt_raw, self.observation, validation_sha256="")

    def test_predecessor_must_pass_bridge_validator(self):
        receipt = json.loads(self.receipt_raw)
        receipt["pit_status"]["status"] = "PIT_PENDING"
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "PREDECESSOR_HISTORY_NOT_ACCEPTED"):
            EXTENSION.extend(self.history_raw, BRIDGE.pretty_bytes(receipt), self.observation,
                             validation_sha256=self.validation)
        history = json.loads(self.history_raw)
        history["steps"][3]["score"] = 99
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "PREDECESSOR_HISTORY_REPLAY_REDERIVATION_MISMATCH"):
            EXTENSION.extend(BRIDGE.pretty_bytes(history), self.receipt_raw, self.observation,
                             validation_sha256=self.validation)

    def test_observation_rejects_rehashed_or_unbound_reference(self):
        wrapper = json.loads(self.reference)
        wrapper["source_packet"]["axes"]["BREADTH"]["measurement"]["combined"]["advance_fraction"] = "0.900000"
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "PAYLOAD_HASH_MISMATCH"):
            EXTENSION.observation_step(BRIDGE.pretty_bytes(wrapper), self.manifest)
        manifest = json.loads(self.manifest)
        manifest["payload_sha256"] = "b" * 64
        with self.assertRaisesRegex(EXTENSION.HistoryExtensionError, "MANIFEST_BINDING_MISMATCH"):
            EXTENSION.observation_step(self.reference, BRIDGE.pretty_bytes(manifest))


if __name__ == "__main__":
    unittest.main()
