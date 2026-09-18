#!/usr/bin/env python3
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_paper_runtime_ratification_candidate as MODULE
from regime import kr_information_system_runtime_bridge as BRIDGE


RECEIPT = (
    ROOT
    / "test/fixtures/kr_runtime_ratification_candidate/final-receipt.json"
)
LIVE = ROOT / "data/latest_korea_market_signals.json"
REVIEWED_AT = "2026-09-13T00:00:00Z"
CALENDAR_PATH = ROOT / MODULE.load_contract()["existing_policy_bindings"][
    "official_calendar_path"
]


def inputs():
    return {
        "historical_receipt_raw": RECEIPT.read_bytes(),
        "latest_signal_raw": LIVE.read_bytes(),
        "reviewed_at": REVIEWED_AT,
    }


class KrPaperRuntimeRatificationCandidateTest(unittest.TestCase):
    def test_information_system_source_uses_explicit_raw_evidence_branch(self):
        evidence = ROOT / "evidence/regime/kr_information_system/2026-09-11"
        reference = (evidence / "KR_PAPER_REFERENCE_CANDIDATE.json").read_bytes()
        manifest = (evidence / "source-capture/manifest.json").read_bytes()
        raw = {
            str(path.relative_to(evidence / "source-capture")): path.read_bytes()
            for path in (evidence / "source-capture/responses").glob("*.json")
        }
        packet = MODULE.build_candidate(
            historical_receipt_raw=RECEIPT.read_bytes(),
            latest_signal_raw=reference,
            reviewed_at=REVIEWED_AT,
            latest_manifest_raw=manifest,
            latest_raw_responses=raw,
            expected_information_system_source={
                "reference_sha256": MODULE.sha256(reference),
                "manifest_sha256": MODULE.sha256(manifest),
                "source_contract_sha256": MODULE.sha256(BRIDGE.SOURCE_CONTRACT_PATH.read_bytes()),
                "leadership_policy_sha256": MODULE.sha256(BRIDGE.LEADERSHIP_POLICY_PATH.read_bytes()),
                "reference_policy_sha256": MODULE.sha256(BRIDGE.REFERENCE_POLICY_PATH.read_bytes()),
            },
        )
        self.assertEqual(packet["status"], "READY_FOR_CIO_RATIFICATION_RUNTIME_STILL_CLOSED")
        self.assertEqual(packet["live_input"]["observed_session"], "2026-09-11")

    def test_current_main_live_pointer_is_reported_in_the_true_direction(self):
        # ``data/latest_korea_market_signals.json`` is the rolling pointer the
        # scheduled KR collector rewrites, so its session date must never be
        # hardcoded here: it moved 2026-09-10 -> 2026-09-17 while the collector
        # was down and then recovered.  Derive both sides and assert that the
        # packet describes the real relation between them.
        packet = MODULE.build_candidate(**inputs())
        observed = packet["live_input"]["observed_session"]
        expected = packet["live_input"]["expected_latest_completed_session"]
        self.assertEqual(observed, json.loads(LIVE.read_bytes())["as_of_date"])
        self.assertEqual(expected, MODULE.latest_completed_session(
            CALENDAR_PATH.read_bytes(),
            str(CALENDAR_PATH.relative_to(ROOT)),
            REVIEWED_AT,
        ))
        if observed == expected:
            self.assertEqual(
                packet["status"], "READY_FOR_CIO_RATIFICATION_RUNTIME_STILL_CLOSED"
            )
            self.assertEqual(
                packet["live_input"]["freshness_status"],
                "EXACT_LATEST_COMPLETED_SESSION",
            )
            self.assertTrue(packet["checks"]["latest_live_session_exact"])
        else:
            behind = observed < expected
            self.assertEqual(
                packet["status"],
                "BLOCKED_LIVE_SESSION_NOT_ADVANCED"
                if behind
                else "BLOCKED_LIVE_SESSION_AHEAD_OF_EXPECTED",
            )
            self.assertEqual(
                packet["live_input"]["freshness_status"],
                "SOURCE_NOT_ADVANCED_EXPECTED_SESSION"
                if behind
                else "SOURCE_AHEAD_OF_EXPECTED_SESSION",
            )
            self.assertFalse(packet["checks"]["latest_live_session_exact"])
        self.assertTrue(packet["checks"]["historical_28_of_28_five_axis"])
        self.assertTrue(packet["checks"]["market_scoped_pit_accepted"])
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertFalse(packet["runtime_decision_available"])
        self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_source_behind_expected_session_is_reported_as_not_advanced(self):
        with mock.patch.object(
            MODULE.SOURCE,
            "validate_packet",
            return_value={"as_of_date": "2026-09-10"},
        ):
            packet = MODULE.build_candidate(**inputs())
        self.assertEqual(packet["status"], "BLOCKED_LIVE_SESSION_NOT_ADVANCED")
        self.assertEqual(
            packet["live_input"]["freshness_status"],
            "SOURCE_NOT_ADVANCED_EXPECTED_SESSION",
        )
        self.assertEqual(
            packet["next_executable_step"],
            "CAPTURE_AND_RETAIN_EXACT_LATEST_COMPLETED_KRX_SESSION",
        )
        self.assertFalse(packet["checks"]["latest_live_session_exact"])
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_source_ahead_of_expected_session_still_blocks_but_is_not_called_stale(self):
        # config/regime_semantic_freshness_policy_v1.json scopes
        # SOURCE_NOT_ADVANCED_EXPECTED_SESSION to "the observed session date is
        # an earlier session".  A later session is still a block (the exact
        # match is what a runtime decision rests on) but must not borrow that
        # ratified reason, or a reader hunts a dead collector that is alive.
        with mock.patch.object(
            MODULE.SOURCE,
            "validate_packet",
            return_value={"as_of_date": "2026-09-17"},
        ):
            packet = MODULE.build_candidate(**inputs())
        self.assertEqual(packet["status"], "BLOCKED_LIVE_SESSION_AHEAD_OF_EXPECTED")
        self.assertEqual(
            packet["live_input"]["freshness_status"],
            "SOURCE_AHEAD_OF_EXPECTED_SESSION",
        )
        self.assertEqual(
            packet["next_executable_step"],
            "RE_REVIEW_AT_CURRENT_INSTANT_OR_RECONCILE_SOURCE_SESSION_DATING",
        )
        self.assertFalse(packet["checks"]["latest_live_session_exact"])
        self.assertFalse(packet["runtime_decision_available"])
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_unparseable_observed_session_fails_closed(self):
        with mock.patch.object(
            MODULE.SOURCE,
            "validate_packet",
            return_value={"as_of_date": "2026-09"},
        ):
            with self.assertRaisesRegex(
                MODULE.KrPaperRuntimeRatificationCandidateError,
                "SESSION_DATE_INVALID",
            ):
                MODULE.build_candidate(**inputs())

    def test_exact_latest_session_advances_only_to_cio_review_not_runtime(self):
        with mock.patch.object(
            MODULE.SOURCE,
            "validate_packet",
            return_value={"as_of_date": "2026-09-11"},
        ):
            packet = MODULE.build_candidate(**inputs())
        self.assertEqual(
            packet["status"],
            "READY_FOR_CIO_RATIFICATION_RUNTIME_STILL_CLOSED",
        )
        self.assertEqual(
            packet["next_executable_step"],
            "CIO_REVIEW_EXACT_EVIDENCE_AND_RUNTIME_BINDING",
        )
        self.assertEqual(packet["regime"], "UNKNOWN")
        self.assertFalse(packet["runtime_decision_available"])
        self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_historical_receipt_tamper_fails_before_review(self):
        raw = json.loads(RECEIPT.read_text(encoding="utf-8"))
        raw["coverage"]["complete_five_axis_count"] = 27
        bad = copy.deepcopy(inputs())
        bad["historical_receipt_raw"] = MODULE.canonical_bytes(raw)
        with self.assertRaisesRegex(
            MODULE.KrPaperRuntimeRatificationCandidateError,
            "HISTORICAL_RECEIPT_HASH_MISMATCH",
        ):
            MODULE.build_candidate(**bad)

    def test_candidate_rederivation_rejects_self_rehashed_claim(self):
        packet = MODULE.build_candidate(**inputs())
        packet["runtime_decision_available"] = True
        packet["packet_sha256"] = MODULE.sha256(MODULE.canonical_bytes(packet))
        with self.assertRaisesRegex(
            MODULE.KrPaperRuntimeRatificationCandidateError,
            "CANDIDATE_REDERIVATION_MISMATCH",
        ):
            MODULE.validate_candidate(packet, **inputs())

    def test_calendar_uses_last_completed_session_not_wall_clock_date(self):
        contract = MODULE.load_contract()
        path = ROOT / contract["existing_policy_bindings"]["official_calendar_path"]
        self.assertEqual(
            MODULE.latest_completed_session(
                path.read_bytes(), str(path.relative_to(ROOT)), REVIEWED_AT
            ),
            "2026-09-11",
        )

    def test_contract_is_draft_and_cannot_carry_boolean_proposed_authority(self):
        contract = MODULE.load_contract()
        self.assertEqual(contract["status"], "DRAFT_NOT_RATIFIED")
        self.assertTrue(
            all(value is False for value in contract["current_authority"].values())
        )
        proposed = contract["proposed_effect_after_separate_ratification"]
        self.assertTrue(all(type(value) is str for value in proposed.values()))


if __name__ == "__main__":
    unittest.main()
