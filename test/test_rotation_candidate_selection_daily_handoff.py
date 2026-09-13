#!/usr/bin/env python3
"""Stage 3 daily handoff regression on the retained 2026-09-12 AM packet.

Revalidating that packet's ROTATION_DISCOVERY component rebuilds its DART
observation packet from lineage.source_path / content_run_path, which are the
rolling pointers data/latest_dart.json and data/latest_dart_content.json.
Every later DART collect rewrites them (DART_SOURCE_FROM_FUTURE), so the DART
source root is pinned for this module to the exact seal-time bytes: the
content-addressed retained copies under
data/observations/dart_structural_content_index/<source_date>/, selected and
verified by the sha256 values the retained lineage itself records.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


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
STAGE1_PATH = ROOT / "data" / "latest_paper_regime_reference.json"
STAGE2_PATH = ROOT / "data" / "latest_capital_flow_posture_reference.json"
DART_INDEX_ROOT = ROOT / "data" / "observations" / "dart_structural_content_index"
_PINNED = contextlib.ExitStack()


def _retained_copy(source_date: str, prefix: str, sha256: str) -> bytes:
    path = DART_INDEX_ROOT / source_date / f"{prefix}-{sha256[:16]}.json"
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != sha256:
        raise AssertionError(f"retained DART copy is not the lineage bytes: {path}")
    return raw


def _dart_seal_root(dest: Path, daily: dict) -> Path:
    """A DART source root holding exactly the bytes the retained lineage names."""
    component = next(
        row for row in daily["components"] if row["component_id"] == "ROTATION_DISCOVERY"
    )
    source_packet = component["packet"]["dart_observations"]["source_packet"]
    lineage = source_packet["lineage"]
    source_date = source_packet["source_date"]
    dest = Path(dest).resolve()
    (dest / "data").mkdir(parents=True)
    (dest / lineage["source_path"]).write_bytes(
        _retained_copy(source_date, "source", lineage["source_sha256"])
    )
    content_raw = _retained_copy(source_date, "content-run", lineage["content_run_sha256"])
    (dest / lineage["content_run_path"]).write_bytes(content_raw)
    for record in json.loads(content_raw)["records"]:
        if record.get("content_status") != "OK":
            continue
        relative = Path("data") / "dart_content" / record["ticker"] / record["filing_identity"]["rcept_no"]
        shutil.copytree(ROOT / relative, dest / relative)
        expected = {key: value for key, value in record.items() if key != "publication_status"}
        live = json.loads((dest / relative / "_manifest.json").read_text(encoding="utf-8"))
        if live != expected:
            # The raw cache manifest was re-captured later: use the retained
            # seal-time manifest copy the content run actually recorded.
            pattern = f"manifest-{record['ticker']}-{record['filing_identity']['rcept_no']}-*.json"
            retained = [
                path.read_bytes() for path in sorted((DART_INDEX_ROOT / source_date).glob(pattern))
                if json.loads(path.read_text(encoding="utf-8")) == expected
            ]
            if not retained:
                raise AssertionError(f"no retained seal-time manifest for {relative}")
            (dest / relative / "_manifest.json").write_bytes(retained[0])
    return dest


def setUpModule():
    seal_root = _dart_seal_root(Path(_PINNED.enter_context(tempfile.TemporaryDirectory())), read(DAILY_PATH))
    stage3 = MODULE.STAGE3
    briefing = stage3.BRIEFING
    defaults = list(briefing.validate_briefing.__defaults__)
    defaults[-1] = seal_root  # dart_root
    _PINNED.enter_context(mock.patch.object(briefing.validate_briefing, "__defaults__", tuple(defaults)))
    for function in (stage3.build_candidate_selection_input, stage3.validate_candidate_selection_input):
        _PINNED.enter_context(mock.patch.object(
            function, "__kwdefaults__", dict(function.__kwdefaults__, dart_root=seal_root)
        ))
    # Lineage paths are labelled relative to the DART module ROOT.
    _PINNED.enter_context(mock.patch.object(briefing.DART_OBSERVATION, "ROOT", seal_root))


def tearDownModule():
    _PINNED.close()


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
        unsigned = copy.deepcopy(self.packet)
        claimed = unsigned.pop("payload_sha256")
        self.assertEqual(claimed, MODULE.STAGE3.payload_sha256(unsigned))
        lineage = self.packet["stage1_lineage"]
        self.assertEqual(lineage["generation_id"], self.stage1["generation_id"])
        self.assertEqual(lineage["payload_sha256"], self.stage1["payload_sha256"])
        self.assertEqual(
            lineage["file_sha256"], hashlib.sha256(STAGE1_PATH.read_bytes()).hexdigest()
        )
        stage2 = lineage["stage2_binding"]
        self.assertEqual(stage2["generation_id"], self.stage2["generation_id"])
        self.assertEqual(stage2["payload_sha256"], self.stage2["payload_sha256"])
        self.assertEqual(
            stage2["file_sha256"], hashlib.sha256(STAGE2_PATH.read_bytes()).hexdigest()
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
