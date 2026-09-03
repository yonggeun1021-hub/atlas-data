#!/usr/bin/env python3
"""P8-12 frozen Dynamic Clock identity at the immutable briefing-core edge."""

from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing_core import chain


DATE = "2026-09-02"
GENERATION = "a" * 64


class BriefingCoreDynamicClockSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.name", "Atlas Test"], cwd=self.repo, check=True
        )
        subprocess.run(
            ["git", "config", "user.email", "atlas@example.invalid"],
            cwd=self.repo,
            check=True,
        )
        self.packet_path = (
            "evidence/daily_briefing/morning/2026-09-02/rev-001/packet.json"
        )
        self.briefing_path = (
            "evidence/daily_briefing/morning/2026-09-02/rev-001/briefing.md"
        )
        self.registry_path = self.repo / "config/briefing_module_registry_v2.json"
        self.registry_path.parent.mkdir(parents=True)
        self.registry_path.write_text(
            json.dumps({"schema_version": "briefing_module_registry/2", "modules": []})
            + "\n",
            encoding="utf-8",
        )
        briefing = self.repo / self.briefing_path
        briefing.parent.mkdir(parents=True)
        briefing.write_text("# Immutable briefing\n", encoding="utf-8")
        self.base_packet = {
            "slot": "morning",
            "decision_date": DATE,
            "components": [
                {
                    "component_id": "STEP0_READ_MODEL_HEALTH",
                    "packet": {"generation": {"generation_id": GENERATION}},
                }
            ],
            "authority": {
                "stage_authority": False,
                "buy_authority": False,
                "action_authority": False,
                "order_generation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
                "broker_credentials_present": False,
                "real_capital": 0,
            },
        }

    def tearDown(self):
        self.temp.cleanup()

    def commit_packet(self, packet: dict, message: str) -> str:
        packet = copy.deepcopy(packet)
        packet.pop("packet_sha256", None)
        packet["packet_sha256"] = chain.digest(packet)
        target = self.repo / self.packet_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(chain.canonical(packet) + b"\n")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.repo, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def build(self, packet: dict, message: str = "source packet") -> dict:
        source_commit = self.commit_packet(packet, message)
        return chain.build_input_envelope(
            self.repo,
            source_commit=source_commit,
            packet_path=self.packet_path,
            briefing_path=self.briefing_path,
            decision_date=DATE,
            slot="morning",
            registry_path=self.registry_path,
        )

    @staticmethod
    def report_source(decision_date: str = DATE) -> dict:
        report = {"decision_date": decision_date, "candidates": []}
        return {
            "kind": "report",
            "report_sha256": chain.digest(report),
            "report": report,
        }

    def with_source(self, source: object) -> dict:
        packet = copy.deepcopy(self.base_packet)
        packet["frozen_sources"] = {"DYNAMIC_CLOCK": source}
        return packet

    def test_legacy_packets_without_dynamic_clock_remain_readable(self):
        for frozen_sources in (None, {}):
            with self.subTest(frozen_sources=frozen_sources):
                packet = copy.deepcopy(self.base_packet)
                if frozen_sources is not None:
                    packet["frozen_sources"] = frozen_sources
                envelope = self.build(packet, f"legacy-{frozen_sources is not None}")
                self.assertEqual(envelope["generation_id"], GENERATION)
                self.assertEqual(envelope["safety_attestation"], chain.SAFETY_ATTESTATION)

    def test_exact_variants_are_accepted_without_authority_change(self):
        for source in (
            {"kind": "unavailable"},
            {"kind": "error", "value": "DynamicClockError:sealed"},
            self.report_source(),
        ):
            with self.subTest(kind=source["kind"]):
                envelope = self.build(self.with_source(source), f"valid-{source['kind']}")
                self.assertEqual(envelope["safety_attestation"], chain.SAFETY_ATTESTATION)

    def test_rehashed_immutable_packet_cannot_change_variant_shape_or_types(self):
        valid = self.report_source()
        cases = (
            (True, "SOURCE_INVALID"),
            ({"kind": True}, "SOURCE_INVALID"),
            ({"kind": "unavailable", "value": None}, "SOURCE_INVALID"),
            ({"kind": "error", "value": True}, "SOURCE_INVALID"),
            ({**valid, "extra": None}, "SOURCE_INVALID"),
            ({**valid, "report_sha256": True}, "SOURCE_INVALID"),
            ({**valid, "report_sha256": "A" * 64}, "SOURCE_INVALID"),
            ({**valid, "report": []}, "SOURCE_INVALID"),
        )
        for index, (source, code) in enumerate(cases):
            with self.subTest(source=source):
                with self.assertRaisesRegex(chain.ChainError, code):
                    self.build(self.with_source(source), f"invalid-shape-{index}")

    def test_rehashed_report_hash_tamper_and_wrong_date_fail_closed(self):
        bad_hash = self.report_source()
        bad_hash["report_sha256"] = "0" * 64
        wrong_date = self.report_source("2026-09-01")
        for source, code in (
            (bad_hash, "SOURCE_SHA_MISMATCH"),
            (wrong_date, "SOURCE_DATE_MISMATCH"),
        ):
            with self.subTest(code=code):
                with self.assertRaisesRegex(chain.ChainError, code):
                    self.build(self.with_source(source), code.lower())


if __name__ == "__main__":
    unittest.main()
