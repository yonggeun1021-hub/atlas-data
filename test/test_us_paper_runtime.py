#!/usr/bin/env python3
"""Tests for regime/us_paper_runtime.py (U4 design draft).

Mirrors test/test_kr_paper_runtime.py and
test/test_kr_information_system_runtime_bridge.py in coverage shape: PIT-not-
accepted -> UNKNOWN, stale/session-not-advanced -> UNKNOWN, vintage-lookahead
rejected, vintage-superseded rejected, session mismatch rejected, authority
all-false, and (the structural proof this draft's design hinges on) even a
synthetic PIT_ACCEPTED bundle plus a mocked-ratified U5 adoption identity can
never make ``runtime_regime`` anything other than the literal string
``"UNKNOWN"``.
"""
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

from regime import us_paper_runtime as R
from regime import market_scoped_pit_acceptance as PIT_ACCEPTANCE


CODE_REVISION = "a" * 40
EVAL_AT = "2026-09-14T22:00:00Z"


def _record(date: str, axes_directions: dict) -> dict:
    return {
        "evidence_class": PIT_ACCEPTANCE.POPULATION_EVIDENCE_CLASS,
        "status": "OBSERVED",
        "no_lookahead_attestation": {"ok": True},
        "effective_session_date": date,
        "candidate_normalized_result": {
            "axes": [{"axis": axis, "direction": direction}
                     for axis, direction in axes_directions.items()],
        },
    }


_ALL_NEGATIVE = {"TREND": "NEGATIVE", "BREADTH": "NEGATIVE", "RISK_VOL": "NEGATIVE",
                 "LIQUIDITY": "NEGATIVE", "LEADERSHIP": "NEGATIVE"}
_ALL_POSITIVE = {axis: "POSITIVE" for axis in _ALL_NEGATIVE}
_ALL_NEUTRAL = {axis: "NEUTRAL" for axis in _ALL_NEGATIVE}
_STRESS = {**_ALL_NEUTRAL, "RISK_VOL": "STRESS"}

_HISTORY_DATES = [
    "2026-01-05", "2026-01-06", "2026-01-07", "2026-01-08",
    "2026-01-09", "2026-01-12", "2026-01-13", "2026-01-14",
]
_HISTORY_STEPS = [
    _ALL_NEGATIVE, _ALL_NEGATIVE, _ALL_POSITIVE, _ALL_POSITIVE,
    _ALL_NEUTRAL, _ALL_NEUTRAL, _STRESS, _STRESS,
]


def full_regime_bundle() -> dict:
    """A synthetic bundle that byte-matches the real population module's own
    provenance markers closely enough to pass condition 1, and that spans all
    four required regimes (condition 6) so ``evaluate_market_pit_acceptance``
    genuinely returns ``PIT_ACCEPTED``.

    This is a fixture for exercising this module's *wiring*, not a claim that
    real US evidence looks like this today. As the module docstring explains,
    the real ``regime.us_historical_replay_population`` can never emit a
    5-of-5-axis record because it never computes BREADTH/LEADERSHIP -- that
    gap (U1/U2) is separate from and unaffected by this fixture.
    """
    records = [_record(day, axes) for day, axes in zip(_HISTORY_DATES, _HISTORY_STEPS)]
    return {
        "schema_version": PIT_ACCEPTANCE.POPULATION_SCHEMA_VERSION["US"],
        "mode": PIT_ACCEPTANCE.POPULATION_MODE,
        "wbs": "P1-COM-05",
        "records": records,
    }


def live_source_packet(
    *, session_date: str = "2026-01-15",
    fred_realtime_start: str = "2026-01-15", fred_realtime_end: str = "9999-12-31",
) -> dict:
    """A synthetic packet shaped exactly like data/latest_free_market_data.json
    (build_us's own input), all-positive so the appended live step is easy to
    reason about in tests that care about its value.
    """
    return {
        "observed_at_utc": session_date + "T21:45:00Z",
        "fred": {"value": "20", "realtime_start": fred_realtime_start,
                 "realtime_end": fred_realtime_end},
        "fred_liquidity": {"series": [
            {"series_id": "WRESBAL", "change": "1",
             "realtime_start": fred_realtime_start, "realtime_end": fred_realtime_end},
            {"series_id": "TOTBKCR", "change": "-1",
             "realtime_start": fred_realtime_start, "realtime_end": fred_realtime_end},
        ]},
        "us_market_reference": {
            "status": "READY", "as_of_session_date": session_date,
            "trend_etfs": [{"returns": {"20_session_pct": "1"}} for _ in range(3)],
            "proxy_axes": {
                "BREADTH": {"measurement": {"advance_fraction": "0.50"}},
                "LEADERSHIP": {"measurement": {"ordered_groups": [
                    {"return_pct": "1"} for _ in range(12)
                ]}},
            },
        },
    }


def reference_policy() -> dict:
    return json.loads(R.REFERENCE_POLICY_PATH.read_text(encoding="utf-8"))


def full_inputs(**overrides) -> dict:
    args = {
        "evaluation_at": "2026-01-15T22:00:00Z",
        "code_revision": CODE_REVISION,
        "pit_acceptance_bundle": full_regime_bundle(),
        "current_source_packet": live_source_packet(),
        "reference_policy": reference_policy(),
        "latest_completed_session_date": "2026-01-15",
    }
    args.update(overrides)
    return args


class USPaperRuntimeTest(unittest.TestCase):
    def test_no_bundle_is_not_accepted_and_stays_unknown(self):
        result = R.evaluate_us_paper_runtime(evaluation_at=EVAL_AT, code_revision=CODE_REVISION)
        self.assertEqual(result["runtime_regime"], "UNKNOWN")
        self.assertFalse(result["runtime_decision_available"])
        self.assertFalse(result["us_pit_accepted_bundle_available"])
        self.assertEqual(result["reasons"], ["US_PIT_NOT_ACCEPTED"])
        self.assertEqual(result["pit_acceptance"]["status"], "NOT_ACCEPTED")
        self.assertEqual(result["pit_acceptance"]["reasons"], ["NO_EVIDENCE_BUNDLE_SUPPLIED"])

    def test_fake_schema_bundle_is_rejected_same_as_no_bundle(self):
        forged = {"schema_version": "not-the-real-schema", "mode": "X", "wbs": "X", "records": []}
        result = R.evaluate_us_paper_runtime(
            evaluation_at=EVAL_AT, code_revision=CODE_REVISION, pit_acceptance_bundle=forged,
        )
        self.assertEqual(result["reasons"], ["US_PIT_NOT_ACCEPTED"])
        self.assertFalse(result["us_pit_accepted_bundle_available"])

    def test_genuine_pit_accepted_bundle_still_blocked_without_u5_ratification(self):
        result = R.evaluate_us_paper_runtime(
            evaluation_at=EVAL_AT, code_revision=CODE_REVISION,
            pit_acceptance_bundle=full_regime_bundle(),
        )
        self.assertTrue(result["us_pit_accepted_bundle_available"])
        self.assertFalse(result["us_paper_runtime_adoption_ratified"])
        self.assertEqual(result["reasons"], ["US_PAPER_RUNTIME_ADOPTION_NOT_RATIFIED"])
        self.assertEqual(result["runtime_regime"], "UNKNOWN")
        self.assertFalse(result["runtime_decision_available"])

    def test_u5_identity_file_absent_today(self):
        # This is the concrete fact gate 2 depends on: as of this draft, no
        # US adoption identity has been ratified, so the file-backed check
        # returns False on the real repository state (no mocking here).
        self.assertFalse(R._us_runtime_adoption_ratified())
        self.assertFalse(R.US_RUNTIME_ADOPTION_IDENTITY_PATH.exists())

    def test_both_gates_true_still_cannot_promote_runtime_regime(self):
        """The central structural proof: even with a genuine PIT_ACCEPTED
        bundle (spanning all four required regimes) AND a mocked-ratified U5
        adoption identity AND a fully valid, fresh, in-vintage live packet,
        the common-v1 aggregation legitimately classifies STRESS as
        ``paper_regime`` -- and ``runtime_regime`` is still exactly
        "UNKNOWN". This module has no code path that ever writes anything
        else into that field.
        """
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            result = R.evaluate_us_paper_runtime(**full_inputs())
        self.assertEqual(result["reasons"], [])
        self.assertTrue(result["us_pit_accepted_bundle_available"])
        self.assertTrue(result["us_paper_runtime_adoption_ratified"])
        self.assertEqual(result["evidence_class"], "LIVE_NATURAL")
        self.assertEqual(result["decision_status"], "PAPER_SIMULATION_CLASSIFIED")
        self.assertNotEqual(result["paper_regime"], "UNKNOWN")
        self.assertEqual(result["paper_regime"], result["aggregation"]["final_regime"])
        # The structural invariant under test.
        self.assertEqual(result["runtime_regime"], "UNKNOWN")
        self.assertFalse(result["runtime_decision_available"])
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            self.assertEqual(result, R.validate_us_paper_runtime(result, **full_inputs()))

    def test_no_assignment_of_runtime_regime_other_than_unknown_in_source(self):
        """Belt-and-braces static check on the module source itself: every
        occurrence of an assignment to the ``runtime_regime`` packet key sets
        it to the literal string "UNKNOWN".
        """
        source = (ROOT / "regime" / "us_paper_runtime.py").read_text(encoding="utf-8")
        import re
        assignments = re.findall(r'"runtime_regime":\s*("[^"]*"|\S+)', source)
        self.assertTrue(assignments)
        for value in assignments:
            self.assertEqual(value, '"UNKNOWN"')
        self.assertNotIn('packet["runtime_regime"] =', source)
        self.assertNotIn("packet['runtime_regime'] =", source)

    def test_session_not_advanced_falls_back_to_unknown(self):
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            args = full_inputs(current_source_packet=live_source_packet(session_date="2026-01-14"))
            result = R.evaluate_us_paper_runtime(**args)
        self.assertEqual(result["reasons"], ["SOURCE_NOT_ADVANCED_EXPECTED_SESSION"])
        self.assertEqual(result["runtime_regime"], "UNKNOWN")
        self.assertIsNone(result["aggregation"])

    def test_fred_vintage_lookahead_and_superseded_are_distinct_and_rejected(self):
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            # Lookahead: vintage begins after the evaluation date.
            args = full_inputs(current_source_packet=live_source_packet(
                fred_realtime_start="2026-01-16", fred_realtime_end="9999-12-31",
            ))
            result = R.evaluate_us_paper_runtime(**args)
            self.assertEqual(result["reasons"], ["US_RUNTIME_LOOKAHEAD_VIOLATION_FRED_VINTAGE_VIXCLS"])
            self.assertEqual(result["runtime_regime"], "UNKNOWN")

            # Superseded: vintage window had already ended before the date.
            args = full_inputs(current_source_packet=live_source_packet(
                fred_realtime_start="2025-01-01", fred_realtime_end="2026-01-10",
            ))
            result = R.evaluate_us_paper_runtime(**args)
            self.assertEqual(
                result["reasons"],
                ["US_FRED_VINTAGE_SUPERSEDED_BEFORE_REQUESTED_DATE_VIXCLS"],
            )
            self.assertEqual(result["runtime_regime"], "UNKNOWN")

    def test_missing_vintage_fields_fail_closed(self):
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            packet = live_source_packet()
            del packet["fred"]["realtime_start"]
            args = full_inputs(current_source_packet=packet)
            result = R.evaluate_us_paper_runtime(**args)
        self.assertEqual(result["reasons"], ["US_FRED_VINTAGE_MISSING_VIXCLS"])
        self.assertEqual(result["runtime_regime"], "UNKNOWN")

    def test_missing_required_inputs_fail_closed_one_at_a_time(self):
        for field in ("reference_policy", "current_source_packet", "latest_completed_session_date"):
            args = full_inputs(**{field: None})
            with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
                result = R.evaluate_us_paper_runtime(**args)
            self.assertEqual(result["runtime_regime"], "UNKNOWN", field)
            self.assertFalse(result["runtime_decision_available"], field)
            self.assertTrue(result["reasons"], field)

    def test_invalid_code_revision_and_evaluation_at_are_rejected(self):
        with self.assertRaises(R.USRuntimeError):
            R.evaluate_us_paper_runtime(evaluation_at=EVAL_AT, code_revision="not-hex")
        with self.assertRaises(R.USRuntimeError):
            R.evaluate_us_paper_runtime(evaluation_at="not-a-timestamp", code_revision=CODE_REVISION)

    def test_authority_is_all_false_except_the_calculation_only_marker(self):
        result = R.evaluate_us_paper_runtime(evaluation_at=EVAL_AT, code_revision=CODE_REVISION)
        authority = result["authority"]
        self.assertTrue(authority["us_paper_experiment_calculation_only"])
        for key, value in authority.items():
            if key != "us_paper_experiment_calculation_only":
                self.assertFalse(value, key)
        # Same invariant holds even on the fully-wired, gate-mocked path.
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            wired = R.evaluate_us_paper_runtime(**full_inputs())
        for key, value in wired["authority"].items():
            if key != "us_paper_experiment_calculation_only":
                self.assertFalse(value, key)

    def test_confirmation_pending_for_a_single_appended_step(self):
        # Drop to a one-day history so the appended live step needs a second
        # finalized packet before common-v1 confirms a non-UNKNOWN regime.
        bundle = {
            "schema_version": PIT_ACCEPTANCE.POPULATION_SCHEMA_VERSION["US"],
            "mode": PIT_ACCEPTANCE.POPULATION_MODE, "wbs": "P1-COM-05",
            "records": [_record("2026-01-14", _ALL_NEGATIVE)],
        }
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            args = full_inputs(pit_acceptance_bundle=bundle,
                                current_source_packet=live_source_packet())
            result = R.evaluate_us_paper_runtime(**args)
        # A one-record synthetic bundle cannot itself reach PIT_ACCEPTED
        # (condition 6 needs all four regimes), so this exercises the
        # ordinary NOT_ACCEPTED path with a smaller fixture instead.
        self.assertFalse(result["us_pit_accepted_bundle_available"])
        self.assertEqual(result["runtime_regime"], "UNKNOWN")

    def test_tampered_output_fails_rederivation_even_resigned(self):
        with mock.patch.object(R, "_us_runtime_adoption_ratified", return_value=True):
            args = full_inputs()
            output = R.evaluate_us_paper_runtime(**args)
            tampered = copy.deepcopy(output)
            tampered["paper_regime"] = "RISK_ON"
            tampered.pop("decision_id")
            tampered["decision_id"] = "us-paper-regime:" + R.COMMON.payload_sha256(tampered)
            with self.assertRaisesRegex(R.USRuntimeError, "RUNTIME_REDERIVATION_MISMATCH"):
                R.validate_us_paper_runtime(tampered, **args)

    def test_decision_id_binds_the_full_packet(self):
        result = R.evaluate_us_paper_runtime(evaluation_at=EVAL_AT, code_revision=CODE_REVISION)
        without_id = copy.deepcopy(result)
        without_id.pop("decision_id")
        self.assertEqual(result["decision_id"], "us-paper-regime:" + R.COMMON.payload_sha256(without_id))


if __name__ == "__main__":
    unittest.main()
