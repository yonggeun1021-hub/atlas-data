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

    def test_frozen_publication_provenance_is_retained_byte_for_byte(self):
        """What the frozen #696 publication consumed still exists and still hashes.

        The publication under EVIDENCE_ROOT is frozen (hardcoded evidence root and
        session boundary, 2026-09-11 -> 2026-09-14); the rolling data/latest
        pointer is advanced by the daily adoption publisher instead.  It names the
        qualification bytes it read by sha256, and those exact bytes are retained
        next to the live record, so its provenance stays checkable even after the
        live record is requalified.
        """
        published = (PUBLICATION.EVIDENCE_ROOT / "decision.json").read_bytes()
        packet = json.loads(published)
        retained = PUBLICATION.RETAINED_QUALIFICATION_PATH.read_bytes()
        self.assertEqual(PUBLICATION.sha256(retained), packet["qualification_sha256"])
        retained_value = json.loads(retained)
        self.assertEqual(
            retained_value["status"], packet["actual_source_qualification"]
        )
        self.assertEqual(
            retained_value["bindings"]["context_session_date"],
            packet["session_boundary_freshness"]["context_session_date"],
        )
        self.assertEqual(
            retained_value["bindings"]["execution_session_date"],
            packet["session_boundary_freshness"]["execution_session_date"],
        )

    def test_requalification_moves_only_the_qualification_binding(self):
        """Documented, dated narrowing -- read this before "fixing" it.

        This assertion used to be ``rederives exactly``, and it cannot survive a
        requalification: ``kr_information_system_runtime_bridge`` builds
        ``expected_bindings["implementation_sha256"]`` from the **live** bytes of
        its seven pinned implementation paths, so the live qualification record
        has to move whenever one of them legitimately changes -- here
        ``regime/paper_regime_reference.py``, requalified 2026-09-19 under
        RATIFICATION_MARKET_STATE_SOURCE_BINDING -- and the rebuilt packet then
        names the new qualification by sha256.

        Restoring byte-exactness would mean rewriting the frozen packet so that it
        names a qualification it never read: committed append-only evidence, and a
        false statement about the past.  That was refused.  What is asserted
        instead is tight: the rebuild under the requalified record must differ from
        the frozen bytes in **exactly** the qualification binding and the decision
        id derived from it, and in nothing else.  Any other field moving is still a
        failure.  Regime, direction, confidence, hysteresis and authority are
        re-asserted field by field below.
        """
        published = (PUBLICATION.EVIDENCE_ROOT / "decision.json").read_bytes()
        packet = json.loads(published)
        rebuilt = PUBLICATION.build_decision(
            evaluation_at=packet["evaluation_at"],
            code_revision=packet["code_revision"],
        )
        live_sha = PUBLICATION.sha256(PUBLICATION.QUALIFICATION_PATH.read_bytes())
        self.assertEqual(rebuilt["qualification_sha256"], live_sha)
        self.assertNotEqual(packet["qualification_sha256"], live_sha)
        moved = sorted(
            key for key in sorted(set(packet) | set(rebuilt))
            if packet.get(key) != rebuilt.get(key)
        )
        self.assertEqual(moved, ["decision_id", "qualification_sha256"])
        # The drift is exactly one pinned path, and it is the one this
        # requalification changed -- not a second, unnoticed one.
        retained = json.loads(
            PUBLICATION.RETAINED_QUALIFICATION_PATH.read_bytes()
        )["bindings"]["implementation_sha256"]
        drifted = sorted(
            relative for relative, claimed in retained.items()
            if PUBLICATION.sha256((ROOT / relative).read_bytes()) != claimed
        )
        self.assertEqual(drifted, ["regime/paper_regime_reference.py"])

    def test_live_qualification_record_tracks_the_live_implementation_bytes(self):
        """The live record is the one the bridge checks against current code."""
        live = json.loads(PUBLICATION.QUALIFICATION_PATH.read_bytes())
        pinned = live["bindings"]["implementation_sha256"]
        self.assertEqual(
            sorted(pinned), sorted(BRIDGE.IMPLEMENTATION_PATHS),
        )
        for relative, claimed in sorted(pinned.items()):
            self.assertEqual(
                PUBLICATION.sha256((ROOT / relative).read_bytes()), claimed, relative,
            )


if __name__ == "__main__":
    unittest.main()
