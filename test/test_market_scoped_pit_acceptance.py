#!/usr/bin/env python3
"""P1-COM-05 G8 market-scoped runtime PIT acceptance regressions.

CIO decision identity CIO-P1-COM-05-NORMALIZATION-FRESHNESS-PIT-FINAL-V1-
2026-09-12 ratified the acceptance RULE only; every market's evidence starts,
and as of this commit remains, NOT_ACCEPTED. These tests exercise the six
ratified conditions against constructed sequences that carry the exact
provenance markers of the real
regime.us_historical_replay_population/regime.kr_historical_replay_population
output contract (schema_version/mode/wbs/evidence_class), never claiming any
of them is a real historical replay.
"""

import copy
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "market_scoped_pit_acceptance.py"
SPEC = importlib.util.spec_from_file_location(
    "market_scoped_pit_acceptance", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
SCORE = {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1, "STRESS": -1}


def axis_row(axis, direction):
    return {
        "axis": axis,
        "direction": direction,
        "score": SCORE[direction],
        "observed_value": None,
        "summary_ko": "test fixture",
    }


def record(date, directions, *, evidence_class=None, status="OBSERVED", attest=True):
    return {
        "requested_date": date,
        "status": status,
        "evidence_class": (
            MODULE.POPULATION_EVIDENCE_CLASS if evidence_class is None else evidence_class
        ),
        "effective_trading_date": date,
        "no_lookahead_attestation": {"ok": True} if attest else None,
        "candidate_normalized_result": {
            "axes": [axis_row(axis, direction) for axis, direction in zip(AXES, directions)]
        },
    }


# A full bull -> neutral -> bear -> stress sequence: two consecutive packets
# confirm each ordinary regime (ratified common-v1 hysteresis), stress enters
# on a single packet.
FULL_CYCLE = [
    ("2026-01-05", ["POSITIVE"] * 5),
    ("2026-01-06", ["POSITIVE"] * 5),
    ("2026-01-07", ["NEUTRAL"] * 5),
    ("2026-01-08", ["NEUTRAL"] * 5),
    ("2026-01-09", ["NEGATIVE"] * 5),
    ("2026-01-10", ["NEGATIVE"] * 5),
    ("2026-01-11", ["NEUTRAL", "NEUTRAL", "STRESS", "NEUTRAL", "NEUTRAL"]),
]


def real_bundle(market, rows=None, **overrides):
    schema_version = MODULE.POPULATION_SCHEMA_VERSION[market]
    records = [record(date, directions) for date, directions in (rows or FULL_CYCLE)]
    bundle = {
        "schema_version": schema_version,
        "mode": MODULE.POPULATION_MODE,
        "wbs": "P1-COM-05",
        "records": records,
    }
    bundle.update(overrides)
    return bundle


class ContractTest(unittest.TestCase):
    def test_contract_is_ratified_and_pinned(self):
        contract = MODULE.load_contract()
        self.assertEqual(contract["policy_status"], "RATIFIED")
        self.assertEqual(contract["markets"], ["US", "KR", "CRYPTO"])
        self.assertTrue(contract["episode_selection_forbidden"])
        self.assertTrue(contract["cherry_pick_forbidden"])
        self.assertEqual(len(contract["pit_accepted_conditions"]), 6)

    def test_contract_does_not_touch_regime_replay_harness_v1(self):
        contract = MODULE.load_contract()
        unaffected = contract["regime_replay_harness_v1_unaffected"]
        self.assertEqual(unaffected["status"], "UNCHANGED_UNWEAKENED")
        harness_contract = json.loads(
            (ROOT / "config" / "regime_replay_harness_contract.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(harness_contract["required_markets"], ["US", "KR", "CRYPTO"])


class NoEvidenceBundleTest(unittest.TestCase):
    def test_no_bundle_is_not_accepted(self):
        result = MODULE.evaluate_market_pit_acceptance("US", None)
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE.REASON_NO_BUNDLE])

    def test_committed_status_snapshot_is_honestly_not_accepted_today(self):
        status = MODULE.build_status()
        for row in status["markets"]:
            self.assertEqual(row["status"], MODULE.STATUS_NOT_ACCEPTED)


class CryptoIndependenceTest(unittest.TestCase):
    def test_crypto_is_always_not_accepted_without_evaluation(self):
        result = MODULE.evaluate_market_pit_acceptance("CRYPTO", real_bundle("US"))
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE.REASON_CRYPTO])
        self.assertEqual(result["evaluated_date_count"], 0)

    def test_market_scoped_us_acceptance_is_independent_of_crypto(self):
        """A fully PIT_ACCEPTED US bundle must not be affected by Crypto's
        permanent NOT_ACCEPTED status, and vice versa."""
        status = MODULE.build_status({"US": real_bundle("US")})
        by_market = {row["market"]: row for row in status["markets"]}
        self.assertEqual(by_market["US"]["status"], MODULE.STATUS_PIT_ACCEPTED)
        self.assertEqual(by_market["CRYPTO"]["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(by_market["CRYPTO"]["reasons"], [MODULE.REASON_CRYPTO])
        # And the reverse: KR absent entirely does not degrade US's status.
        self.assertEqual(by_market["KR"]["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(by_market["KR"]["reasons"], [MODULE.REASON_NO_BUNDLE])


class SyntheticCreditZeroTest(unittest.TestCase):
    def test_missing_schema_version_marker_gets_zero_credit(self):
        bundle = real_bundle("KR")
        del bundle["schema_version"]
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE.REASON_NO_BUNDLE])

    def test_wrong_market_schema_version_gets_zero_credit(self):
        """A US-shaped bundle handed in for KR (or vice versa) must not be
        silently accepted -- provenance is checked per market."""
        bundle = real_bundle("US")  # schema_version says US
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE.REASON_NO_BUNDLE])

    def test_forged_evidence_class_gets_zero_credit(self):
        bundle = real_bundle("KR")
        bundle["records"][0]["evidence_class"] = "HAND_CRAFTED_NOT_REAL"
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE.REASON_NO_BUNDLE])

    def test_a_perfectly_shaped_but_unmarked_sequence_gets_zero_credit(self):
        """The exact same axis content that would otherwise reach
        PIT_ACCEPTED, supplied as a bare caller-constructed dict without any
        of the three provenance markers, must earn nothing."""
        bundle = {"records": real_bundle("KR")["records"]}
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE.REASON_NO_BUNDLE])
        self.assertEqual(result["evaluated_date_count"], 0)


class MissingRequiredEpisodeTest(unittest.TestCase):
    def test_missing_stress_episode_is_not_accepted(self):
        rows = FULL_CYCLE[:-1]  # drop the single STRESS packet
        result = MODULE.evaluate_market_pit_acceptance("KR", real_bundle("KR", rows))
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertEqual(result["reasons"], [MODULE._condition_failed(6)])
        self.assertEqual(result["missing_regimes"], ["STRESS"])
        self.assertIn("RISK_ON", result["regimes_observed"])

    def test_missing_risk_on_episode_is_not_accepted(self):
        rows = [row for row in FULL_CYCLE if row[0] not in ("2026-01-05", "2026-01-06")]
        result = MODULE.evaluate_market_pit_acceptance("KR", real_bundle("KR", rows))
        self.assertEqual(result["status"], MODULE.STATUS_NOT_ACCEPTED)
        self.assertIn("RISK_ON", result["missing_regimes"])

    def test_full_cycle_with_every_regime_is_pit_accepted(self):
        result = MODULE.evaluate_market_pit_acceptance("KR", real_bundle("KR"))
        self.assertEqual(result["status"], MODULE.STATUS_PIT_ACCEPTED)
        self.assertEqual(result["reasons"], [])
        self.assertEqual(
            set(result["regimes_observed"]),
            {"RISK_ON", "NEUTRAL", "RISK_OFF", "STRESS"},
        )

    def test_episode_dates_are_never_selected_by_this_module(self):
        """The validator must consume every OBSERVED date in the supplied
        bundle -- it has no date-selection parameter at all."""
        import inspect

        signature = inspect.signature(MODULE.evaluate_market_pit_acceptance)
        self.assertEqual(list(signature.parameters), ["market", "bundle"])


class CoverageAndLookaheadTest(unittest.TestCase):
    def test_incomplete_axis_coverage_excludes_the_date_not_substitutes(self):
        rows = list(FULL_CYCLE)
        date, directions = rows[0]
        incomplete = record(date, directions[:4])  # only 4 of 5 axes present
        bundle = real_bundle("KR")
        bundle["records"][0] = incomplete
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["evaluated_date_count"], len(FULL_CYCLE) - 1)

    def test_missing_no_lookahead_attestation_excludes_the_date(self):
        bundle = real_bundle("KR")
        bundle["records"][0]["no_lookahead_attestation"] = None
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["evaluated_date_count"], len(FULL_CYCLE) - 1)

    def test_non_observed_status_record_is_excluded(self):
        bundle = real_bundle("KR")
        bundle["records"][0]["status"] = "BLOCKED"
        result = MODULE.evaluate_market_pit_acceptance("KR", bundle)
        self.assertEqual(result["evaluated_date_count"], len(FULL_CYCLE) - 1)


class DeterministicRerunTest(unittest.TestCase):
    def test_two_evaluations_of_the_same_bundle_are_byte_identical(self):
        bundle = real_bundle("KR")
        first = MODULE.evaluate_market_pit_acceptance("KR", copy.deepcopy(bundle))
        second = MODULE.evaluate_market_pit_acceptance("KR", copy.deepcopy(bundle))
        self.assertEqual(
            MODULE.canonical_json(first), MODULE.canonical_json(second)
        )
        self.assertEqual(first["replay_report_sha256"], second["replay_report_sha256"])


class ExactCommonV1ReuseTest(unittest.TestCase):
    def test_no_local_reimplementation_of_scoring_or_hysteresis(self):
        """This module must call regime.decision_authority.replay_common_v1
        for every classification/hysteresis result rather than reimplementing
        any of its ratified numbers."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("AUTHORITY.replay_common_v1", source)
        for forbidden in ("risk_on_min_score", "stress_exit_min", "S>=3", "S<=-3"):
            self.assertNotIn(forbidden, source)


class AuthorityAllFalseTest(unittest.TestCase):
    def test_authority_is_all_false_except_the_two_meta_flags(self):
        auth = MODULE.authority()
        self.assertTrue(auth["acceptance_rule_ratified"])
        for key, value in auth.items():
            if key == "acceptance_rule_ratified":
                continue
            self.assertFalse(value, key)

    def test_pit_accepted_market_still_has_all_authority_false(self):
        result = MODULE.evaluate_market_pit_acceptance("KR", real_bundle("KR"))
        self.assertEqual(result["status"], MODULE.STATUS_PIT_ACCEPTED)
        auth = result["authority"]
        for key, value in auth.items():
            if key == "acceptance_rule_ratified":
                continue
            self.assertFalse(value, key)

    def test_status_and_overlay_authority_all_false(self):
        status = MODULE.build_status()
        overlay = MODULE.build_readiness_overlay(status)
        for key, value in status["authority"].items():
            if key == "acceptance_rule_ratified":
                continue
            self.assertFalse(value, key)
        for key, value in overlay["authority"].items():
            if key == "acceptance_rule_ratified":
                continue
            self.assertFalse(value, key)
        self.assertFalse(overlay["runtime_decision_available"])


class ReadinessOverlayTest(unittest.TestCase):
    def test_ratified_market_with_no_evidence_is_policy_ready_pit_pending(self):
        status = MODULE.build_status()
        overlay = MODULE.build_readiness_overlay(status)
        by_market = {row["market"]: row for row in overlay["markets"]}
        self.assertEqual(
            by_market["US"]["policy_readiness_status"],
            MODULE.READINESS_POLICY_READY_PIT_PENDING,
        )
        self.assertEqual(
            by_market["KR"]["policy_readiness_status"],
            MODULE.READINESS_POLICY_READY_PIT_PENDING,
        )

    def test_crypto_is_never_policy_ready(self):
        status = MODULE.build_status()
        overlay = MODULE.build_readiness_overlay(status)
        by_market = {row["market"]: row for row in overlay["markets"]}
        self.assertEqual(
            by_market["CRYPTO"]["policy_readiness_status"],
            MODULE.READINESS_NORMALIZATION_UNRATIFIED,
        )

    def test_pit_accepted_market_reports_runtime_still_closed_not_open(self):
        status = MODULE.build_status({"KR": real_bundle("KR")})
        overlay = MODULE.build_readiness_overlay(status)
        by_market = {row["market"]: row for row in overlay["markets"]}
        self.assertEqual(
            by_market["KR"]["policy_readiness_status"],
            MODULE.READINESS_PIT_ACCEPTED_RUNTIME_STILL_CLOSED,
        )
        self.assertFalse(by_market["KR"]["runtime_decision_available"])

    def test_overlay_never_imports_runtime_regime_readiness_module(self):
        """The overlay may talk *about* runtime_regime_readiness in prose
        (it does, in the module docstring), but must never import it, call
        into it, or reuse its packet/validation contract."""
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("import runtime_regime_readiness", source)
        self.assertNotIn("RUNTIME_REGIME_READINESS", source)
        self.assertNotIn(".build_readiness(", source)
        self.assertNotIn(".validate_readiness(", source)


if __name__ == "__main__":
    unittest.main()
