#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import kr_information_system_runtime_publication as PUBLICATION


class KrInformationSystemRuntimePublicationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.code_revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip()

    def ratified_qualification(self) -> bytes:
        value = json.loads(PUBLICATION.QUALIFICATION_PATH.read_bytes())
        value["status"] = "RATIFIED_KR_PAPER_DISPLAY_ONLY"
        value["effective_at"] = "2026-09-13T00:00:00Z"
        value["authority"]["paper_runtime_display_authorized"] = True
        return BRIDGE.pretty_bytes(value)

    def test_rebuilds_confirmed_display_only_decision_from_committed_bytes(self):
        result = PUBLICATION.build_decision(
            evaluation_at="2026-09-14T06:00:00Z",
            code_revision=self.code_revision,
            qualification_raw=self.ratified_qualification(),
        )
        self.assertEqual(result["runtime_regime"], "NEUTRAL")
        self.assertEqual(result["direction"], "DETERIORATING")
        self.assertEqual(result["confidence"], "0.2")
        self.assertEqual(result["current_observation"]["candidate_regime"], "RISK_OFF")
        self.assertEqual(result["current_observation"]["hysteresis"]["confirmation_count"], 1)
        self.assertTrue(result["authority"]["paper_runtime_display_authorized"])
        self.assertTrue(all(
            value is False
            for key, value in result["authority"].items()
            if key != "paper_runtime_display_authorized"
        ))

    def test_pending_qualification_and_changed_bytes_fail_closed(self):
        pending = json.loads(self.ratified_qualification())
        pending["status"] = "PENDING_CIO_RATIFICATION"
        pending["authority"]["paper_runtime_display_authorized"] = False
        with self.assertRaisesRegex(
            PUBLICATION.KrInformationSystemRuntimePublicationError,
            "QUALIFICATION_NOT_RATIFIED",
        ):
            PUBLICATION.build_decision(
                evaluation_at="2026-09-14T06:00:00Z",
                code_revision=self.code_revision,
                qualification_raw=BRIDGE.pretty_bytes(pending),
            )
        changed = json.loads(self.ratified_qualification())
        changed["bindings"] = copy.deepcopy(changed["bindings"])
        changed["bindings"]["context_session_date"] = "2026-09-10"
        with self.assertRaisesRegex(
            PUBLICATION.KrInformationSystemRuntimePublicationError,
            "QUALIFICATION_BINDING_MISMATCH",
        ):
            PUBLICATION.build_decision(
                evaluation_at="2026-09-14T06:00:00Z",
                code_revision=self.code_revision,
                qualification_raw=BRIDGE.pretty_bytes(changed),
            )

    def test_published_bytes_are_stable_and_checkable(self):
        with tempfile.TemporaryDirectory() as directory:
            qualification = Path(directory) / "qualification.json"
            qualification.write_bytes(self.ratified_qualification())
            target = Path(directory) / "decision.json"
            base_args = [
                "publication",
                "--evaluation-at", "2026-09-14T06:00:00Z",
                "--code-revision", self.code_revision,
                "--output", str(target),
            ]
            with mock.patch.object(PUBLICATION, "QUALIFICATION_PATH", qualification):
                with mock.patch.object(sys, "argv", base_args):
                    self.assertEqual(PUBLICATION.main(), 0)
                first = target.read_bytes()
                with mock.patch.object(sys, "argv", [*base_args, "--check"]):
                    self.assertEqual(PUBLICATION.main(), 0)
                self.assertEqual(target.read_bytes(), first)

    def test_committed_publication_rederives_exactly(self):
        # The #696 publication is frozen next to its evidence; the rolling
        # data/latest pointer is advanced by the daily adoption publisher.
        published = (PUBLICATION.EVIDENCE_ROOT / "decision.json").read_bytes()
        packet = json.loads(published)
        rebuilt = PUBLICATION.build_decision(
            evaluation_at=packet["evaluation_at"],
            code_revision=packet["code_revision"],
        )
        self.assertEqual(BRIDGE.pretty_bytes(rebuilt), published)


if __name__ == "__main__":
    unittest.main()
