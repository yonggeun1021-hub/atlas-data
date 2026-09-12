#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "briefing" / "rotation_candidate_selection_daily_handoff.py"


def load_module():
    spec = importlib.util.spec_from_file_location(
        "rotation_candidate_selection_daily_handoff", SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MODULE = load_module()
DAILY_PATH = (
    ROOT
    / "evidence/daily_briefing/morning/2026-09-12/rev-002/packet.json"
)
STAGE1_PATH = ROOT / (
    "evidence/regime/paper_reference/2026-09-12/"
    "ab1283311ef0e4d2e9045dc95b25d14e289bdb3066616943d88c1ca31e1a0cce/"
    "packet.json"
)
STAGE2_PATH = ROOT / (
    "evidence/portfolio/capital_flow_posture_reference/2026-09-12/"
    "c396052fc6a335826f843276627b31f009c338ac0277cbc1d988f8fc6eae802a/"
    "packet.json"
)


def read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resign_daily(packet: dict) -> dict:
    packet.pop("packet_sha256", None)
    packet["packet_sha256"] = MODULE.STAGE3.payload_sha256(packet)
    return packet


class RotationCandidateSelectionDailyHandoffTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daily = read(DAILY_PATH)
        cls.stage1 = read(STAGE1_PATH)
        cls.stage2 = read(STAGE2_PATH)
        cls.packet = MODULE.build_from_daily_packet(
            cls.daily, cls.stage2, cls.stage1
        )

    def rotation_component(self, packet: dict) -> dict:
        return next(
            row for row in packet["components"]
            if row["component_id"] == "ROTATION_DISCOVERY"
        )

    def test_retained_component_is_real_and_independently_revalidated(self):
        briefing, ledger = MODULE.retained_rotation_inputs(self.daily)
        component = self.rotation_component(self.daily)
        self.assertEqual(
            briefing["packet_sha256"],
            "b204685196c23f2a87d7d69ce0f5f6a60e3c08093b60153f7dba80dc14394f9e",
        )
        self.assertEqual(component["source_packet_sha256"], briefing["packet_sha256"])
        self.assertEqual(briefing["rotation"]["ledger_status"], "EMPTY")
        self.assertEqual(briefing["rotation"]["latest_changes"], [])
        self.assertEqual(
            ledger["payload_sha256"],
            "32ef8896b61f4c08ab91fa9c0926c15c394b942ac68918102cc9a85cc4c72ed3",
        )
        self.assertEqual(briefing["rotation"]["source_ledger_sha256"], ledger["payload_sha256"])

    def test_real_daily_to_stage3_handoff_emits_honest_zero_row_packet(self):
        self.assertEqual(
            self.packet["schema_version"],
            "rotation_candidate_selection_input_packet/2",
        )
        self.assertEqual(self.packet["input_count"], 0)
        self.assertEqual(self.packet["inputs"], [])
        self.assertEqual(
            self.packet["payload_sha256"],
            "c2cd47d7ce8fd363225c8c487e6afbc183c35ec79de8e1769c99650de36a2ccc",
        )
        self.assertEqual(
            [row["market"] for row in self.packet["stage1_lineage"]["markets"]],
            ["US", "KR", "CRYPTO"],
        )
        self.assertTrue(
            all(
                value is False
                for key, value in self.packet["authority"].items()
                if key != "input_projection_only"
            )
        )

    def test_self_resigned_rotation_or_ledger_rebinding_fails(self):
        changed = copy.deepcopy(self.daily)
        component = self.rotation_component(changed)
        component["packet"]["rotation"]["source_ledger_sha256"] = "0" * 64
        child = component["packet"]
        child.pop("packet_sha256")
        child["packet_sha256"] = MODULE.STAGE3.payload_sha256(child)
        component["source_packet_sha256"] = child["packet_sha256"]
        resign_daily(changed)
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionDailyHandoffError,
            "DAILY_ROTATION_LEDGER_BINDING_MISMATCH",
        ):
            MODULE.build_from_daily_packet(changed, self.stage2, self.stage1)

    def test_retained_daily_container_requires_its_exact_self_hash(self):
        changed = copy.deepcopy(self.daily)
        changed["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionDailyHandoffError,
            "DAILY_PACKET_SHA_MISMATCH",
        ):
            MODULE.build_from_daily_packet(changed, self.stage2, self.stage1)

    def test_explicit_null_rotation_source_is_not_absence(self):
        changed = copy.deepcopy(self.daily)
        changed["frozen_sources"][MODULE.ROTATION_SOURCE_KEY] = None
        resign_daily(changed)
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionDailyHandoffError,
            "DAILY_ROTATION_SOURCE_INVALID",
        ):
            MODULE.build_from_daily_packet(changed, self.stage2, self.stage1)

    def test_component_order_and_closed_authority_are_enforced(self):
        reordered = copy.deepcopy(self.daily)
        reordered["components"][14], reordered["components"][15] = (
            reordered["components"][15], reordered["components"][14]
        )
        resign_daily(reordered)
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionDailyHandoffError,
            "DAILY_COMPONENT_ORDER_INVALID",
        ):
            MODULE.build_from_daily_packet(reordered, self.stage2, self.stage1)

        opened = copy.deepcopy(self.daily)
        self.rotation_component(opened)["decision_eligible"] = True
        resign_daily(opened)
        with self.assertRaisesRegex(
            MODULE.RotationCandidateSelectionDailyHandoffError,
            "DAILY_ROTATION_AUTHORITY_OPENED:decision_eligible",
        ):
            MODULE.build_from_daily_packet(opened, self.stage2, self.stage1)

    def test_cli_writes_exact_stage3_packet_only_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stage3.json"
            self.assertEqual(
                MODULE.run(DAILY_PATH, STAGE2_PATH, STAGE1_PATH, output), 0
            )
            self.assertEqual(read(output), self.packet)
            forbidden = ROOT / "data" / "stage3-daily-handoff-test.json"
            self.assertEqual(
                MODULE.run(DAILY_PATH, STAGE2_PATH, STAGE1_PATH, forbidden), 1
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
