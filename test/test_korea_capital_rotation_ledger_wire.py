#!/usr/bin/env python3
"""P2-03 -> rotation_state_ledger -> daily briefing wiring regression.

PIT temporal-invariant correction (2026-08-22): decision_time is now a
required parameter -- the real current Leadership observation's own
available_at -- and no-lookahead is decision_available_at<=decision_time,
never <=observation_date. There is no more separate "confirmed_history"
bypass channel: first-seen evidence flows through the real Breadth
lineage (coverage_context.breadth) itself.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


WIRE = _load("korea_capital_rotation_ledger_wire", "rotation/korea_capital_rotation_ledger_wire.py")
KCR = _load("korea_capital_rotation", "rotation/korea_capital_rotation.py")
LEDGER = _load("rotation_state_ledger", "rotation/rotation_state_ledger.py")

sys.path.insert(0, str(ROOT / "test"))
KCR_TEST = _load("test_korea_capital_rotation", "test/test_korea_capital_rotation.py")

REAL_KOSDAQ_SHA = "3be02ecda92a143abb5a825f66a207bd2a92bdef1d8b59b1c28a5ea8b0fcfc94"
REAL_KOSPI_SHA = "086bddf7313fe0a36d87d86fc028982bc9835e3b4b55d15dff13ecdc2818caf2"

# as_of_date "2026-08-21" -> _fixture_bundle's current_observation.available_at
# (KCR_TEST.upstream_payload sets f"{as_of_date}T18:00:00+09:00").
DECISION_TIME = "2026-08-21T09:00:00+00:00"


def real_context_source() -> dict:
    """Exact real P1-KR-05 run 32549348644 (2026-08-21, KST) lineage --
    same literal values already used by
    test_korea_capital_rotation.py::test_real_p1_kr05_live_lineage_
    derives_blocked_not_available, on the v2 (first-seen lineage)
    schema. source_available_at stays the same honest null gap;
    captured_at/first_seen_at are the real run's own fetch instant (a
    day after as_of_date, and after DECISION_TIME above too), a genuine
    forward_live capture, not a synthetic fixture -- this real evidence
    still correctly derives BLOCKED (too late for THIS decision_time),
    not AVAILABLE."""
    summary = {
        "schema_version": "korea_breadth_context_lineage/2",
        "as_of_date": "2026-08-21",
        "capture_mode": "forward_live",
        "markets": {
            "KOSPI": {
                "lineage_sha256": REAL_KOSPI_SHA, "as_of_date": "2026-08-21",
                "source_available_at": None, "captured_at": "2026-08-22T03:35:36Z",
                "first_seen_at": "2026-08-22T03:35:36Z",
            },
            "KOSDAQ": {
                "lineage_sha256": REAL_KOSDAQ_SHA, "as_of_date": "2026-08-21",
                "source_available_at": None, "captured_at": "2026-08-22T03:35:36Z",
                "first_seen_at": "2026-08-22T03:35:36Z",
            },
        },
        "source": {"producer": "korea_breadth_derived_outputs.py", "scope": "recent", "workflow_run_id": "32549348644"},
        "generated_at": "2026-08-22T03:35:36Z",
    }
    summary["payload_sha256"] = WIRE.payload_sha256(summary)
    return summary


class BreadthDerivationTest(unittest.TestCase):
    def test_missing_source_derives_unknown_for_both_markets(self):
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, None, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "UNKNOWN")
        self.assertFalse(breadth["decision_eligible"])
        self.assertFalse(breadth["ranking_input_authorized"])
        self.assertIn("KOSPI_NO_LINEAGE_SUPPLIED", reason)
        self.assertIn("KOSDAQ_NO_LINEAGE_SUPPLIED", reason)

    def test_real_lineage_derives_blocked_not_available(self):
        # This real evidence's first_seen_at (2026-08-22) is genuinely
        # after DECISION_TIME (2026-08-21T09:00:00Z) -- correctly BLOCKED
        # (too late for this decision), not AVAILABLE.
        source = real_context_source()
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "BLOCKED")
        self.assertFalse(breadth["decision_eligible"])
        self.assertEqual(breadth["markets"]["KOSPI"]["lineage_sha256"], REAL_KOSPI_SHA)
        self.assertEqual(breadth["markets"]["KOSDAQ"]["lineage_sha256"], REAL_KOSDAQ_SHA)
        self.assertIn("NOT_YET_DECISION_AVAILABLE", reason)

    def test_available_when_source_available_at_before_decision_time(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        # Same calendar day as its own as_of_date, before DECISION_TIME.
        source["markets"]["KOSPI"]["source_available_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSDAQ"]["source_available_at"] = "2026-08-21T05:00:00Z"
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "AVAILABLE")
        self.assertTrue(breadth["decision_eligible"])
        self.assertEqual(reason, "KOSDAQ_AVAILABLE,KOSPI_AVAILABLE")

    def test_forward_live_first_seen_before_decision_time_is_available(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        source["markets"]["KOSPI"]["first_seen_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSPI"]["captured_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSDAQ"]["first_seen_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSDAQ"]["captured_at"] = "2026-08-21T05:00:00Z"
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "AVAILABLE")
        self.assertTrue(breadth["decision_eligible"])

    def test_stale_when_source_available_at_exceeds_freshness_limit(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        # Market's own as_of_date is genuinely older than decision_time's
        # date (2026-08-01), so a same-day-or-later source_available_at
        # for THAT observation is still well before DECISION_TIME
        # (2026-08-21) -- correctly stale by the freshness_limit_days
        # window, not a chronology violation.
        for market in ("KOSPI", "KOSDAQ"):
            source["markets"][market]["as_of_date"] = "2026-08-01"
            source["markets"][market]["source_available_at"] = "2026-08-01T18:00:00Z"
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "STALE")
        self.assertFalse(breadth["decision_eligible"])
        self.assertIn("DECISION_AVAILABLE_AT_STALE", reason)

    def test_worst_market_wins_when_one_blocked_one_available(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        source["markets"]["KOSPI"]["source_available_at"] = "2026-08-21T05:00:00Z"
        # KOSDAQ's first_seen_at stays after DECISION_TIME -> BLOCKED,
        # worse than KOSPI's AVAILABLE -- the overall status must be the
        # worse of the two.
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "BLOCKED")
        self.assertIn("KOSDAQ_NOT_YET_DECISION_AVAILABLE", reason)

    def test_source_available_at_after_decision_time_degrades_to_blocked_not_error(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        source["markets"]["KOSPI"]["source_available_at"] = "2026-08-22T00:00:00Z"
        source["markets"]["KOSDAQ"]["source_available_at"] = "2026-08-22T00:00:00Z"
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "BLOCKED")
        self.assertFalse(breadth["decision_eligible"])

    def test_partial_market_identity_fails_closed(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        source["markets"]["KOSPI"]["as_of_date"] = None  # lineage present, as_of_date missing
        with self.assertRaisesRegex(
            WIRE.KoreaRotationWireError, "BREADTH_MARKET_PARTIAL_IDENTITY:KOSPI"
        ):
            WIRE.build_coverage_context_breadth("2026-08-21", 3, source, DECISION_TIME)

    def test_invalid_capture_mode_fails_closed(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        source["capture_mode"] = "not_a_real_mode"
        with self.assertRaisesRegex(WIRE.KoreaRotationWireError, "BREADTH_CAPTURE_MODE_INVALID"):
            WIRE.build_coverage_context_breadth("2026-08-21", 3, source, DECISION_TIME)

    def test_historical_backfill_never_available_even_before_decision_time(self):
        source = real_context_source()
        source = copy.deepcopy(source)
        source["capture_mode"] = "historical_backfill"
        source["markets"]["KOSPI"]["first_seen_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSPI"]["captured_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSDAQ"]["first_seen_at"] = "2026-08-21T05:00:00Z"
        source["markets"]["KOSDAQ"]["captured_at"] = "2026-08-21T05:00:00Z"
        breadth, reason = WIRE.build_coverage_context_breadth(
            "2026-08-21", 3, source, DECISION_TIME
        )
        self.assertEqual(breadth["status"], "BLOCKED")
        self.assertFalse(breadth["decision_eligible"])

    def test_first_seen_before_captured_at_or_as_of_date_fails_closed(self):
        source = real_context_source()
        reversed_source = copy.deepcopy(source)
        reversed_source["markets"]["KOSPI"]["first_seen_at"] = "2026-08-21T00:00:00Z"
        reversed_source["markets"]["KOSPI"]["captured_at"] = "2026-08-21T05:00:00Z"
        with self.assertRaisesRegex(
            WIRE.KoreaRotationWireError, "BREADTH_MARKET_FIRST_SEEN_BEFORE_CAPTURED:KOSPI"
        ):
            WIRE.build_coverage_context_breadth("2026-08-21", 3, reversed_source, DECISION_TIME)

        before_as_of = copy.deepcopy(source)
        before_as_of["markets"]["KOSPI"]["first_seen_at"] = "2026-08-20T00:00:00Z"
        before_as_of["markets"]["KOSPI"]["captured_at"] = "2026-08-20T00:00:00Z"
        with self.assertRaisesRegex(
            WIRE.KoreaRotationWireError, "BREADTH_MARKET_FIRST_SEEN_BEFORE_AS_OF:KOSPI"
        ):
            WIRE.build_coverage_context_breadth("2026-08-21", 3, before_as_of, DECISION_TIME)

    def test_source_sha256_tamper_is_rejected_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-08-21" / "packet.json"
            path.parent.mkdir(parents=True)
            summary = real_context_source()
            summary["markets"]["KOSPI"]["lineage_sha256"] = "9" * 64  # tamper, stale digest
            path.write_text(json.dumps(summary))
            original_root = WIRE.BREADTH_CONTEXT_ROOT
            WIRE.BREADTH_CONTEXT_ROOT = Path(tmp)
            try:
                with self.assertRaisesRegex(
                    WIRE.KoreaRotationWireError, "BREADTH_CONTEXT_SOURCE_SHA_MISMATCH"
                ):
                    WIRE.load_breadth_context_source("2026-08-21")
            finally:
                WIRE.BREADTH_CONTEXT_ROOT = original_root

    def test_source_date_mismatch_is_rejected_on_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "2026-08-21" / "packet.json"
            path.parent.mkdir(parents=True)
            summary = real_context_source()
            summary["as_of_date"] = "2026-08-20"
            summary.pop("payload_sha256")
            summary["payload_sha256"] = WIRE.payload_sha256(summary)
            path.write_text(json.dumps(summary))
            original_root = WIRE.BREADTH_CONTEXT_ROOT
            WIRE.BREADTH_CONTEXT_ROOT = Path(tmp)
            try:
                with self.assertRaisesRegex(
                    WIRE.KoreaRotationWireError, "BREADTH_CONTEXT_SOURCE_DATE_MISMATCH"
                ):
                    WIRE.load_breadth_context_source("2026-08-21")
            finally:
                WIRE.BREADTH_CONTEXT_ROOT = original_root

    def test_missing_source_file_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = WIRE.BREADTH_CONTEXT_ROOT
            WIRE.BREADTH_CONTEXT_ROOT = Path(tmp)
            try:
                self.assertIsNone(WIRE.load_breadth_context_source("2099-01-01"))
            finally:
                WIRE.BREADTH_CONTEXT_ROOT = original_root


def _fixture_bundle(as_of_date: str):
    """Test-fixture price side (same helper the module's own test suite
    already uses) with current_observation rebuilt for as_of_date so it
    lines up with the real Breadth lineage date under test."""
    value, policy = KCR_TEST.make_bundle()
    current_values = {
        "11::KOSPI_반도체": "110", "12::KOSPI_바이오": "140", "13::KOSPI_방산": "80",
        "21::KOSDAQ_반도체": "130", "22::KOSDAQ_바이오": "110", "23::KOSDAQ_로봇": "90",
    }
    with tempfile.TemporaryDirectory() as raw:
        policy_path = KCR_TEST.write_upstream_policy(Path(raw) / "leadership-policy.json")
        current = KCR_TEST.KL.build_transform(
            KCR_TEST.upstream_payload(as_of_date, current_values), policy_path
        )
    value["as_of_date"] = as_of_date
    value["current_observation"] = current
    return value, policy


def _state_policy(rotation_packet: dict) -> dict:
    return {
        "schema_version": LEDGER.POLICY_SCHEMA_VERSION,
        "policy_id": "POLICY.P2.05.TEST",
        "approval_status": "RATIFIED",
        "ratified_by": "Atlas CIO",
        "ratified_at_utc": "2026-08-01T00:00:00Z",
        "effective_from": "2026-08-01",
        "effective_to": None,
        "market": "KOREA",
        "input_rotation_contract_version": rotation_packet["contract_version"],
        "input_rotation_policy_sha256": rotation_packet["lineage"]["rotation_policy_sha256"],
        "state_vocabulary": ["EMERGING", "STRONG", "WEAKENING"],
        "state_by_bucket_transition": {
            "BOTTOM_TO_BOTTOM": "WEAKENING", "BOTTOM_TO_MIDDLE": "EMERGING",
            "BOTTOM_TO_TOP": "EMERGING", "MIDDLE_TO_BOTTOM": "WEAKENING",
            "MIDDLE_TO_MIDDLE": "STRONG", "MIDDLE_TO_TOP": "EMERGING",
            "TOP_TO_BOTTOM": "WEAKENING", "TOP_TO_MIDDLE": "WEAKENING",
            "TOP_TO_TOP": "STRONG",
        },
        "maximum_ledger_gap_days": 30,
    }


class EndToEndProofTest(unittest.TestCase):
    def test_real_blocked_lineage_flows_through_ledger_and_pointer(self):
        as_of_date = "2026-08-21"
        source = real_context_source()
        value, policy = _fixture_bundle(as_of_date)
        decision_time = value["current_observation"]["available_at"]
        breadth, reason = WIRE.build_coverage_context_breadth(as_of_date, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        rotation_packet = KCR.build_packet(value, policy)
        self.assertEqual(rotation_packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertEqual(rotation_packet["coverage_context"]["breadth"]["status"], "BLOCKED")

        state_policy = _state_policy(rotation_packet)
        ledger = LEDGER.apply_rotation(rotation_packet, state_policy, previous_ledger=None)
        self.assertEqual(ledger["status"], "STATE_HISTORY_OBSERVED")
        self.assertEqual(ledger["ledger_revision"], 1)

        pointer = WIRE.build_briefing_pointer(
            rotation_packet, reason, source,
            "data/observations/korea_breadth_context/2026-08-21/packet.json",
            generated_at=source["generated_at"],
        )
        self.assertEqual(pointer["breadth"]["status"], "BLOCKED")
        self.assertEqual(pointer["breadth"]["source_context_sha256"], source["payload_sha256"])
        self.assertEqual(
            pointer["breadth"]["markets"]["KOSPI"]["lineage_sha256"], REAL_KOSPI_SHA
        )
        self.assertEqual(
            pointer["breadth"]["markets"]["KOSDAQ"]["lineage_sha256"], REAL_KOSDAQ_SHA
        )
        # Real first-seen lineage is fully visible directly in the primary
        # breadth.markets shape now -- no separate bypass channel.
        self.assertEqual(
            pointer["breadth"]["markets"]["KOSPI"]["first_seen_at"], "2026-08-22T03:35:36Z"
        )
        self.assertEqual(pointer["breadth"]["markets"]["KOSPI"]["capture_mode"], "forward_live")
        self.assertFalse(pointer["breadth"]["decision_eligible"])
        self.assertFalse(pointer["breadth"]["ranking_input_authorized"])
        self.assertNotIn("confirmed_history", pointer)
        for value_ in pointer["authority"].values():
            self.assertFalse(value_)

        # Re-derive the same run again -- byte-identical ledger and
        # pointer (idempotent replay).
        ledger_again = LEDGER.apply_rotation(rotation_packet, state_policy, previous_ledger=None)
        self.assertEqual(ledger_again, ledger)
        pointer_again = WIRE.build_briefing_pointer(
            rotation_packet, reason, source,
            "data/observations/korea_breadth_context/2026-08-21/packet.json",
            generated_at=source["generated_at"],
        )
        self.assertEqual(pointer_again, pointer)

    def test_real_available_lineage_flows_through_when_first_seen_before_decision_time(self):
        # The genuinely different, real-world-correct outcome: a
        # forward_live capture whose first_seen_at predates decision_time
        # is real, usable evidence -- READY end-to-end, never a forced
        # PASS. All authority stays closed regardless.
        as_of_date = "2026-08-21"
        source = copy.deepcopy(real_context_source())
        for market in ("KOSPI", "KOSDAQ"):
            source["markets"][market]["first_seen_at"] = "2026-08-21T05:00:00Z"
            source["markets"][market]["captured_at"] = "2026-08-21T05:00:00Z"
        value, policy = _fixture_bundle(as_of_date)
        decision_time = value["current_observation"]["available_at"]
        breadth, reason = WIRE.build_coverage_context_breadth(as_of_date, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        rotation_packet = KCR.build_packet(value, policy)
        self.assertEqual(rotation_packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(rotation_packet["rotation_policy_effective"])
        self.assertEqual(rotation_packet["coverage_context"]["breadth"]["status"], "AVAILABLE")
        self.assertTrue(rotation_packet["coverage_context"]["breadth"]["decision_eligible"])
        for field in (
            "trading_authorized", "production_authorized", "stage_promotion_authorized",
            "candidate_ranking_authorized",
        ):
            self.assertFalse(rotation_packet["authority"][field])

    def test_pointer_write_is_atomic_and_readable(self):
        as_of_date = "2026-08-21"
        source = real_context_source()
        value, policy = _fixture_bundle(as_of_date)
        decision_time = value["current_observation"]["available_at"]
        breadth, reason = WIRE.build_coverage_context_breadth(as_of_date, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        rotation_packet = KCR.build_packet(value, policy)
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "latest_korea_rotation.json"
            pointer = WIRE.refresh_briefing_pointer(
                rotation_packet, reason, source, "some/path.json",
                source["generated_at"], out_path=out_path,
            )
            on_disk = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk, pointer)


if __name__ == "__main__":
    unittest.main()
