#!/usr/bin/env python3
"""CRYPTO_PAPER_RUNTIME_V1 pure runtime: ratified rules, freshness negatives,
PROVISIONAL_FORWARD_ACCEPTANCE and auto-revert to UNKNOWN."""

from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
from decimal import Decimal
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import crypto_paper_descriptive_normalization as DESCRIPTIVE  # noqa: E402
from regime import crypto_paper_runtime as RUNTIME  # noqa: E402
from regime import crypto_kraken_btc_replay_diagnostic as KRAKEN  # noqa: E402
from regime import decision_authority as COMMON  # noqa: E402

CODE_REVISION = "0" * 40
MANIFEST = "a" * 64
BTC_SHA = "b" * 64
D = dt.date


RECEIPT_PATH = ROOT / "evidence/crypto/kraken_bulk_btc_replay_diagnostic/receipt.json"


def receipt_raw() -> bytes:
    return RECEIPT_PATH.read_bytes()


def window(decision_date: dt.date, days: int, *, status="OBSERVED_UNCLASSIFIED",
           buckets=None, alts=None, source_reasons=(), sector_reason="TAXONOMY_UNRATIFIED"):
    end = decision_date - dt.timedelta(days=1)
    observed = status == "OBSERVED_UNCLASSIFIED"
    return {
        "window_id": RUNTIME.PILOT if days == 7 else RUNTIME.PRIMARY,
        "status": status,
        "unknown_reason": None if observed else "SOURCE_POINT_UNKNOWN",
        "start_date": (end - dt.timedelta(days=days - 1)).isoformat(),
        "end_date": end.isoformat(),
        "source_unknown_reasons": list(source_reasons),
        "sector_chain_unknown_reason": sector_reason if observed else None,
        "point_available_at": [f"{(end - dt.timedelta(days=i - 1)).isoformat()}T00:50:00Z"
                               for i in range(days, 0, -1)] if observed else [],
        "last_manifest_sha256": MANIFEST if observed else None,
        "bucket_gross_returns": (buckets or {"ALT": "1.02", "BTC": "1.01", "ETH": "1.00"}) if observed else None,
        "alt_asset_gross_returns": (alts or ["1.03", "1.01", "1.05"]) if observed else None,
    }


def record(decision_date: dt.date, *, primary=False, vol="0.5", dd="-0.05", breadth="0.60",
           daily="100", weekly="200") -> dict:
    iso = decision_date.isoformat()
    previous = (decision_date - dt.timedelta(days=1)).isoformat()
    return {
        "decision_date": iso,
        "btc": {
            "vintage_date": iso, "available_at": f"{iso}T00:38:56Z",
            "latest_finalized_day": previous, "trend_latest_finalized_day": previous,
            "risk_latest_finalized_day": previous, "trend_category": "ABOVE_200DMA",
            "trend_source_sha256": BTC_SHA, "risk_source_sha256": BTC_SHA,
            "risk_transform_version": "btc_risk/v1",
            "realized_vol_annualized_fraction": vol, "current_drawdown_fraction": dd,
        },
        "breadth": {
            "vintage_date": iso, "available_at": f"{iso}T00:52:00Z", "as_of_date": previous,
            "status": "OBSERVED_UNCLASSIFIED", "unknown_reason": None,
            "advance_fraction": breadth, "manifest_sha256": MANIFEST,
        },
        "stablecoin": {
            "vintage_date": iso, "available_at": f"{iso}T06:39:53Z", "observation_date": iso,
            "daily_status": "AVAILABLE", "weekly_status": "AVAILABLE",
            "daily_net_issuance": daily, "weekly_net_issuance": weekly, "response_sha256": "c" * 64,
        },
        "leadership": {"windows": {
            RUNTIME.PILOT: window(decision_date, 7),
            RUNTIME.PRIMARY: window(decision_date, 30) if primary else window(
                decision_date, 30, status="UNKNOWN"),
        }},
    }


def chain(start: dt.date, end: dt.date, **kwargs) -> dict:
    result, cursor = {}, start
    while cursor <= end:
        result[cursor.isoformat()] = record(cursor, **kwargs)
        cursor += dt.timedelta(days=1)
    return result


def evaluate(records: dict, evaluation_at="2026-09-20T07:30:00Z", **overrides) -> dict:
    inputs = {
        "evaluation_at": evaluation_at, "code_revision": CODE_REVISION,
        "day_records": records, "rerun_day_records": copy.deepcopy(records),
        "evidence_class": RUNTIME.LIVE_NATURAL, "kraken_receipt_raw": receipt_raw(),
    }
    inputs.update(overrides)
    return RUNTIME.evaluate_crypto_paper_runtime(**inputs)


class PolicyIdentityTest(unittest.TestCase):
    def test_policy_is_hash_pinned_and_bound_to_user_ratification(self):
        policy = RUNTIME.load_policy()
        self.assertEqual(ROOT / policy["acceptance"]["replaced_condition_6"]["receipt_path"], RECEIPT_PATH)
        self.assertEqual(policy["decision"]["identity"], "CRYPTO-PAPER-RUNTIME-V1-20260914")
        self.assertEqual(policy["ratified_markets"], ["CRYPTO"])
        self.assertEqual(policy["acceptance"]["label"], "PROVISIONAL_FORWARD_ACCEPTANCE")
        self.assertEqual(policy["runtime_authorized_regimes_scope"], "CRYPTO_PAPER_RUNTIME_DECISION_ONLY")
        self.assertTrue(all(v is False for k, v in policy["authority"].items()
                            if k != "paper_runtime_display_authorized"))

    def test_policy_drift_fails_closed_to_unknown(self):
        records = chain(D(2026, 9, 14), D(2026, 9, 20))
        with mock.patch.object(RUNTIME, "POLICY_SHA256", "f" * 64):
            packet = evaluate(records)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["reasons"], ["POLICY_HASH_MISMATCH"])
        self.assertTrue(all(v is False for v in packet["authority"].values()))

    def test_existing_us_kr_and_g8_identities_are_untouched_bindings(self):
        policy = RUNTIME.load_policy()
        bindings = policy["bindings"]
        self.assertEqual(bindings["paper_runtime_normalization_path"], "config/paper_runtime_normalization_v1.json")
        self.assertEqual(bindings["pit_acceptance_contract_path"], "config/market_scoped_pit_acceptance_contract_v1.json")

    def test_normalization_is_verbatim_descriptive_provisional_values(self):
        self.assertEqual(RUNTIME.trend_direction("ABOVE_200DMA"), "POSITIVE")
        self.assertEqual(RUNTIME.trend_direction("AT_200DMA"), "NEUTRAL")
        self.assertEqual(RUNTIME.trend_direction("BELOW_200DMA"), "NEGATIVE")
        self.assertEqual(DESCRIPTIVE.BREADTH_POSITIVE_MIN, Decimal("0.55"))
        cases = [("0.55", "POSITIVE"), ("0.549999", "NEUTRAL"), ("0.450001", "NEUTRAL"),
                 ("0.45", "NEGATIVE"), ("1", "POSITIVE"), ("0", "NEGATIVE")]
        for value, expected in cases:
            with self.subTest(value=value):
                self.assertEqual(RUNTIME.breadth_direction(Decimal(value)), expected)
        self.assertEqual(RUNTIME.liquidity_direction(Decimal(1), Decimal(1)), "POSITIVE")
        self.assertEqual(RUNTIME.liquidity_direction(Decimal(-1), Decimal(-1)), "NEGATIVE")
        self.assertEqual(RUNTIME.liquidity_direction(Decimal(1), Decimal(-1)), "NEUTRAL")
        self.assertEqual(RUNTIME.liquidity_direction(Decimal(0), Decimal(1)), "NEUTRAL")
        self.assertEqual(RUNTIME.leadership_direction("BROAD_ALT_LEADERSHIP"), "POSITIVE")
        for code in ("BTC_LEADERSHIP", "ETH_LEADERSHIP", "MIXED_WINDOW_LEADERSHIP", "NARROW_ALT_LEADERSHIP"):
            self.assertEqual(RUNTIME.leadership_direction(code), "NEUTRAL")
        for code in ("UNKNOWN", "", None):
            with self.assertRaisesRegex(RUNTIME.CryptoPaperRuntimeError, "LEADERSHIP_CODE_UNKNOWN"):
                RUNTIME.leadership_direction(code)


class RiskVolRuleTest(unittest.TestCase):
    def check(self, vol, dd, expected):
        with self.subTest(vol=vol, dd=dd):
            self.assertEqual(RUNTIME.risk_vol_direction(Decimal(vol), Decimal(dd)), expected)

    def test_stress_boundaries(self):
        self.check("0.10", "-0.25", "STRESS")
        self.check("0.10", "-0.249999", "NEGATIVE")
        self.check("0.90", "-0.10", "STRESS")
        self.check("0.899999", "-0.10", "NEGATIVE")
        self.check("0.90", "-0.099999", "NEGATIVE")
        self.check("1.50", "0", "NEGATIVE")

    def test_negative_boundaries(self):
        self.check("0.30", "-0.15", "NEGATIVE")
        self.check("0.30", "-0.149999", "NEUTRAL")
        self.check("0.70", "0", "NEGATIVE")
        self.check("0.699999", "0", "NEUTRAL")

    def test_positive_boundaries(self):
        self.check("0.30", "-0.08", "NEUTRAL")
        self.check("0.30", "-0.079999", "POSITIVE")
        self.check("0.45", "0", "NEUTRAL")
        self.check("0.449999", "0", "POSITIVE")
        self.check("0", "0", "POSITIVE")

    def test_rules_are_ordered_top_to_bottom(self):
        self.check("0.95", "-0.30", "STRESS")   # also NEGATIVE, STRESS wins
        self.check("0.20", "-0.20", "NEGATIVE")  # never POSITIVE/NEUTRAL
        self.check("0.496126645795", "-0.049352761759", "NEUTRAL")  # diagnosis example

    def test_invalid_inputs_raise(self):
        for vol, dd in (("-0.01", "0"), ("0.3", "0.01"), ("0.3", "-1.01")):
            with self.assertRaises(RUNTIME.CryptoPaperRuntimeError):
                RUNTIME.risk_vol_direction(Decimal(vol), Decimal(dd))


class LeadershipWindowTest(unittest.TestCase):
    day = D(2026, 9, 20)

    def test_code_reuses_existing_state_mapping_on_one_window(self):
        self.assertEqual(RUNTIME.leadership_code(window(self.day, 7)), "BROAD_ALT_LEADERSHIP")
        self.assertEqual(RUNTIME.leadership_code(window(self.day, 7, alts=["1.00", "0.99", "1.001"])),
                         "NARROW_ALT_LEADERSHIP")
        self.assertEqual(RUNTIME.leadership_code(window(
            self.day, 7, buckets={"ALT": "0.98", "BTC": "1.05", "ETH": "1.00"})), "BTC_LEADERSHIP")
        self.assertEqual(RUNTIME.leadership_code(window(
            self.day, 7, buckets={"ALT": "0.98", "BTC": "1.00", "ETH": "1.05"})), "ETH_LEADERSHIP")

    def test_pilot_is_official_until_primary_observed(self):
        official, _ = RUNTIME.select_leadership_window(record(self.day)["leadership"], self.day, False)
        self.assertEqual(official, RUNTIME.PILOT)
        official, _ = RUNTIME.select_leadership_window(record(self.day, primary=True)["leadership"], self.day, False)
        self.assertEqual(official, RUNTIME.PRIMARY)

    def test_switch_is_automatic_and_permanent_without_pilot_fallback(self):
        records = chain(D(2026, 9, 14), D(2026, 9, 20))
        records["2026-09-17"] = record(D(2026, 9, 17), primary=True)
        steps = RUNTIME.build_chain(records, D(2026, 9, 14), D(2026, 9, 20))
        self.assertEqual(steps[2]["axis_observations"]["LEADERSHIP"]["official_window"], RUNTIME.PILOT)
        self.assertEqual(steps[3]["axis_observations"]["LEADERSHIP"]["official_window"], RUNTIME.PRIMARY)
        for step in steps[4:]:
            self.assertEqual(step["axes"]["LEADERSHIP"]["status"], "UNDEFINED")
            self.assertIn("LEADERSHIP_PRIMARY_30D_NOT_OBSERVED", step["reasons"])

    def test_taxonomy_coverage_unknown_day_makes_axis_missing(self):
        bad = record(self.day)
        bad["leadership"]["windows"][RUNTIME.PILOT] = window(
            self.day, 7, status="UNKNOWN", source_reasons=["TAXONOMY_COVERAGE_UNKNOWN"])
        step = RUNTIME.evaluate_day(bad, self.day, False)
        self.assertEqual(step["axes"]["LEADERSHIP"]["status"], "UNDEFINED")
        self.assertIn("LEADERSHIP_TAXONOMY_COVERAGE_UNKNOWN", step["reasons"])
        sector = record(self.day)
        sector["leadership"]["windows"][RUNTIME.PILOT]["sector_chain_unknown_reason"] = "TAXONOMY_COVERAGE_UNKNOWN"
        self.assertIn("LEADERSHIP_TAXONOMY_COVERAGE_UNKNOWN", RUNTIME.evaluate_day(sector, self.day, False)["reasons"])
        breadth = record(self.day)
        breadth["breadth"].update(status="UNKNOWN", unknown_reason="TAXONOMY_COVERAGE_UNKNOWN")
        self.assertIn("BREADTH_TAXONOMY_COVERAGE_UNKNOWN", RUNTIME.evaluate_day(breadth, self.day, False)["reasons"])


class FinalizedPacketNegativeTest(unittest.TestCase):
    """Mirrors the KR runtime negatives: stale, missing axis, date mismatch,
    lookahead and mixed generation each give immediate UNKNOWN, no carry."""

    start, current = D(2026, 9, 14), D(2026, 9, 20)

    def assert_axis_missing(self, mutate, axis, reason):
        records = chain(self.start, self.current)
        mutate(records[self.current.isoformat()])
        packet = evaluate(records)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertFalse(packet["runtime_decision_available"])
        self.assertIn("CURRENT_FINALIZED_PACKET_INCOMPLETE", packet["reasons"])
        self.assertIn(reason, packet["reasons"])
        self.assertIsNone(packet["current_observation"]["axis_observations"][axis].get("available_at"))
        # No carry: the previous finalized packet was confirmed RISK_ON.
        self.assertEqual(packet["aggregation"]["steps"][-2]["confirmed_regime"], "RISK_ON")
        self.assertEqual(packet["aggregation"]["steps"][-1]["confirmed_regime"], "UNKNOWN")
        self.assertTrue(all(v is False for v in packet["authority"].values()))

    def test_baseline_is_known(self):
        packet = evaluate(chain(self.start, self.current))
        self.assertEqual(packet["decision_status"], "PAPER_RUNTIME_CLASSIFIED", packet["reasons"])
        self.assertEqual(packet["runtime_regime"], "RISK_ON")
        self.assertEqual(packet["acceptance"]["status"], "PROVISIONAL_FORWARD_ACCEPTED")
        self.assertEqual(packet["acceptance"]["label"], "PROVISIONAL_FORWARD_ACCEPTANCE")
        self.assertTrue(packet["authority"]["paper_runtime_display_authorized"])
        self.assertTrue(all(v is False for k, v in packet["authority"].items()
                            if k != "paper_runtime_display_authorized"))

    def test_stale_btc_capture(self):
        def mutate(row):
            row["btc"]["vintage_date"] = "2026-09-19"
        self.assert_axis_missing(mutate, "TREND", "BTC_STALE")

    def test_stale_availability_before_decision_day(self):
        def mutate(row):
            row["breadth"]["available_at"] = "2026-09-19T23:59:59Z"
        self.assert_axis_missing(mutate, "BREADTH", "BREADTH_STALE")

    def test_missing_axis(self):
        def mutate(row):
            row["stablecoin"] = None
        self.assert_axis_missing(mutate, "LIQUIDITY", "LIQUIDITY_MISSING")

    def test_source_error_is_missing_axis(self):
        def mutate(row):
            row["btc"] = {"error": "SNAPSHOT_MISSING"}
        self.assert_axis_missing(mutate, "RISK_VOL", "BTC_SOURCE_SNAPSHOT_MISSING")

    def test_date_mismatch(self):
        def mutate(row):
            row["breadth"]["as_of_date"] = "2026-09-18"
        self.assert_axis_missing(mutate, "BREADTH", "BREADTH_DATE_MISMATCH")

    def test_stablecoin_must_be_same_day(self):
        def mutate(row):
            row["stablecoin"]["observation_date"] = "2026-09-19"
        self.assert_axis_missing(mutate, "LIQUIDITY", "LIQUIDITY_DATE_MISMATCH")

    def test_lookahead_after_07z(self):
        def mutate(row):
            row["stablecoin"]["available_at"] = "2026-09-20T07:00:01Z"
        self.assert_axis_missing(mutate, "LIQUIDITY", "LIQUIDITY_LOOKAHEAD")

    def test_lookahead_future_finalized_day(self):
        def mutate(row):
            row["btc"].update(latest_finalized_day="2026-09-20", trend_latest_finalized_day="2026-09-20",
                              risk_latest_finalized_day="2026-09-20")
        self.assert_axis_missing(mutate, "TREND", "BTC_LOOKAHEAD")

    def test_leadership_point_lookahead(self):
        def mutate(row):
            row["leadership"]["windows"][RUNTIME.PILOT]["point_available_at"][-1] = "2026-09-20T08:00:00Z"
        self.assert_axis_missing(mutate, "LEADERSHIP", "LEADERSHIP_LOOKAHEAD")

    def test_mixed_generation_btc(self):
        def mutate(row):
            row["btc"]["risk_source_sha256"] = "d" * 64
        self.assert_axis_missing(mutate, "RISK_VOL", "BTC_MIXED_GENERATION")

    def test_mixed_generation_leadership(self):
        def mutate(row):
            row["leadership"]["windows"][RUNTIME.PILOT]["last_manifest_sha256"] = "e" * 64
        self.assert_axis_missing(mutate, "LEADERSHIP", "LEADERSHIP_MIXED_GENERATION")

    def test_finalized_packet_missing_or_misdated(self):
        records = chain(self.start, self.current)
        del records[self.current.isoformat()]
        packet = evaluate(records)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("FINALIZED_PACKET_MISSING", packet["reasons"])
        records = chain(self.start, self.current)
        records[self.current.isoformat()]["decision_date"] = "2026-09-19"
        self.assertIn("FINALIZED_PACKET_DATE_MISMATCH", evaluate(records)["reasons"])

    def test_decision_time_boundary_is_07z(self):
        records = chain(self.start, self.current)
        before = evaluate(records, evaluation_at="2026-09-20T06:59:59Z")
        self.assertEqual(before["current_decision_date"], "2026-09-19")
        at = evaluate(records, evaluation_at="2026-09-20T07:00:00Z")
        self.assertEqual(at["current_decision_date"], "2026-09-20")
        # A packet that is not refreshed for the next UTC day is stale -> UNKNOWN.
        stale = evaluate(records, evaluation_at="2026-09-21T07:00:00Z")
        self.assertEqual(stale["runtime_regime"], "UNKNOWN")
        self.assertIn("FINALIZED_PACKET_MISSING", stale["reasons"])

    def test_before_runtime_chain_start(self):
        packet = evaluate({}, evaluation_at="2026-09-14T06:59:59Z")
        self.assertEqual(packet["reasons"], ["BEFORE_RUNTIME_CHAIN_START"])
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")

    def test_stress_entry_is_immediate(self):
        records = chain(self.start, self.current)
        records["2026-09-20"] = record(self.current, dd="-0.30")
        packet = evaluate(records)
        self.assertEqual(packet["runtime_regime"], "STRESS")
        self.assertEqual(packet["confidence"], "1")


class ProvisionalForwardAcceptanceTest(unittest.TestCase):
    start, current = D(2026, 9, 14), D(2026, 9, 20)

    def test_crypto_remains_unknown_until_acceptance_passes(self):
        records = chain(self.start, self.current)
        del records["2026-09-14"]
        del records["2026-09-15"]  # only 4 complete days before the current packet
        packet = evaluate(records)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["acceptance"]["status"], "NOT_ACCEPTED")
        self.assertIn("ACCEPTANCE_CONDITION_FAILED:2_MINIMUM_CONSECUTIVE_COMPLETE_DAYS", packet["reasons"])
        # The same history with exactly 5 complete prior days is accepted.
        records["2026-09-15"] = record(D(2026, 9, 15))
        accepted = evaluate(records)
        self.assertEqual(accepted["acceptance"]["accepted_run"]["complete_day_count"], 5)
        self.assertEqual(accepted["runtime_regime"], "RISK_ON")

    def test_real_evidence_only(self):
        packet = evaluate(chain(self.start, self.current), evidence_class="SYNTHETIC_OFFLINE_FIXTURE")
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("ACCEPTANCE_CONDITION_FAILED:1_REAL_EVIDENCE_ONLY", packet["reasons"])

    def test_deterministic_rerun_required(self):
        records = chain(self.start, self.current)
        rerun = copy.deepcopy(records)
        rerun["2026-09-16"]["breadth"]["advance_fraction"] = "0.50"
        packet = evaluate(records, rerun_day_records=rerun)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("ACCEPTANCE_CONDITION_FAILED:4_DETERMINISTIC_RERUN", packet["reasons"])
        self.assertIn("ACCEPTANCE_CONDITION_FAILED:4_DETERMINISTIC_RERUN",
                      evaluate(records, rerun_day_records=None)["reasons"])

    def test_auto_revert_on_kraken_validator_failure(self):
        records = chain(self.start, self.current)
        self.assertEqual(evaluate(records)["runtime_regime"], "RISK_ON")
        for raw, reason in (
            (None, "6_KRAKEN_REPLAY_RECEIPT_MISSING"),
            (receipt_raw() + b" ", "6_KRAKEN_REPLAY_RECEIPT_HASH_MISMATCH"),
        ):
            with self.subTest(reason=reason):
                packet = evaluate(records, kraken_receipt_raw=raw)
                self.assertEqual(packet["runtime_regime"], "UNKNOWN")
                self.assertEqual(packet["decision_status"], "BLOCKED")
                self.assertIn("ACCEPTANCE_CONDITION_FAILED:" + reason, packet["reasons"])
                self.assertTrue(all(v is False for v in packet["authority"].values()))

    def test_auto_revert_on_missing_trust_anchor_or_failed_receipt(self):
        records = chain(self.start, self.current)
        policy = RUNTIME.load_policy()
        unanchored = copy.deepcopy(policy)
        unanchored["acceptance"]["replaced_condition_6"]["receipt_sha256"] = None
        with mock.patch.object(RUNTIME, "load_policy", return_value=unanchored):
            packet = evaluate(records)
        self.assertIn("ACCEPTANCE_CONDITION_FAILED:6_KRAKEN_REPLAY_TRUST_ANCHOR_MISSING", packet["reasons"])
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")

    def test_history_lookahead_breaks_acceptance(self):
        records = chain(self.start, self.current)
        records["2026-09-17"]["btc"]["available_at"] = "2026-09-17T09:00:00Z"
        packet = evaluate(records)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["acceptance"]["accepted_run"]["first_decision_date"], "2026-09-18")

    def test_committed_kraken_receipt_passes_required_results(self):
        receipt = RUNTIME.validate_kraken_receipt(receipt_raw(), RUNTIME.load_policy())
        self.assertEqual(receipt["status"], "PASS")
        for name in ("STRESS", "NEGATIVE", "POSITIVE"):
            self.assertGreater(receipt["risk_vol_counts"][name], 0)
        self.assertEqual(receipt["range"]["requested_start_date"], "2019-01-01")
        self.assertEqual(receipt["overlap_check"]["close_mismatch_count"], 0)

    def test_decision_id_is_deterministic(self):
        records = chain(self.start, self.current)
        self.assertEqual(evaluate(records)["decision_id"], evaluate(copy.deepcopy(records))["decision_id"])
        packet = evaluate(records)
        RUNTIME.validate_crypto_paper_runtime(
            packet, evaluation_at="2026-09-20T07:30:00Z", code_revision=CODE_REVISION,
            day_records=records, rerun_day_records=copy.deepcopy(records),
            evidence_class=RUNTIME.LIVE_NATURAL, kraken_receipt_raw=receipt_raw())


# Score fixtures: TREND +1 and RISK_VOL 0 (vol 0.5, dd -0.05) are fixed.
SCORE_4 = {}                                              # BREADTH +1, LIQUIDITY +1, LEADERSHIP +1
SCORE_3 = {"breadth": "0.50"}                             # BREADTH 0
SCORE_2 = {"breadth": "0.50", "daily": "100", "weekly": "-1"}  # BREADTH 0, LIQUIDITY 0


class CommonV1ReuseTest(unittest.TestCase):
    """B1: aggregation and hysteresis must be the unmodified common-v1 replay."""

    start, current = D(2026, 9, 14), D(2026, 9, 20)

    def records(self, *per_day):
        result = {}
        for offset, kwargs in enumerate(per_day):
            day = self.start + dt.timedelta(days=offset)
            result[day.isoformat()] = record(day, **kwargs)
        return result

    def independent_replay(self, packet):
        sequence = {"schema_version": 1, "market": "CRYPTO", "case_id": "crypto-paper-runtime", "steps": [
            {"packet_id": f"crypto-{row['decision_date']}", "as_of_date": row["decision_date"],
             "axes": {axis: ({"status": "DEFINED", "direction": direction} if direction is not None
                             else {"status": "UNDEFINED", "direction": None})
                      for axis, direction in row["axis_directions"].items()}}
            for row in packet["chain"]
        ]}
        return COMMON.replay_common_v1(sequence)

    def test_aggregation_equals_direct_common_v1_replay(self):
        for days in ([SCORE_4] * 7, [SCORE_2] * 6 + [SCORE_4], [SCORE_3, SCORE_2] * 3 + [SCORE_3]):
            with self.subTest(days=days):
                packet = evaluate(self.records(*days))
                self.assertEqual(COMMON.canonical_bytes(packet["aggregation"]),
                                 COMMON.canonical_bytes(self.independent_replay(packet)))
                self.assertEqual(packet["aggregation"]["thresholds"]["ordinary_transition_finalized_packets"], 2)
                self.assertEqual(packet["aggregation"]["thresholds"]["risk_on_min_score"], 3)

    def test_neutral_to_risk_on_flip_on_current_day_stays_pending(self):
        packet = evaluate(self.records(*([SCORE_2] * 6 + [SCORE_4])))
        self.assertEqual(packet["decision_status"], "PAPER_RUNTIME_CLASSIFIED", packet["reasons"])
        self.assertEqual(packet["runtime_regime"], "NEUTRAL")
        current = packet["current_observation"]
        self.assertEqual(current["candidate_regime"], "RISK_ON")
        self.assertEqual(current["score"], 4)
        self.assertEqual(current["hysteresis"]["confirmation_count"], 1)
        self.assertEqual(current["hysteresis"]["rule"], "ORDINARY_CONFIRMATION_PENDING")

    def test_score_plus_three_and_plus_two_land_on_correct_sides(self):
        plus_three = evaluate(self.records(*([SCORE_3] * 7)))
        self.assertEqual(plus_three["current_observation"]["score"], 3)
        self.assertEqual(plus_three["current_observation"]["candidate_regime"], "RISK_ON")
        self.assertEqual(plus_three["runtime_regime"], "RISK_ON")
        plus_two = evaluate(self.records(*([SCORE_2] * 7)))
        self.assertEqual(plus_two["current_observation"]["score"], 2)
        self.assertEqual(plus_two["current_observation"]["candidate_regime"], "NEUTRAL")
        self.assertEqual(plus_two["runtime_regime"], "NEUTRAL")


class AcceptanceHardeningTest(unittest.TestCase):
    start, current = D(2026, 9, 14), D(2026, 9, 20)

    def test_fail_receipt_under_bound_anchor_is_unknown(self):
        """B2: a hash-anchored but FAIL receipt never passes condition 6."""
        receipt = json.loads(receipt_raw())
        receipt.pop("payload_sha256")
        counts = receipt["risk_vol_counts"]
        counts["NEUTRAL"] += counts["STRESS"]
        counts["STRESS"] = 0
        receipt["risk_vol_first_seen"]["STRESS"] = None
        receipt["missing_required_results"] = ["STRESS"]
        receipt["status"] = "FAIL"
        receipt["payload_sha256"] = KRAKEN.payload_sha256(receipt)
        raw = (json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")
        self.assertEqual(KRAKEN.validate_receipt(json.loads(raw))["status"], "FAIL")
        policy = copy.deepcopy(RUNTIME.load_policy())
        policy["acceptance"]["replaced_condition_6"]["receipt_sha256"] = hashlib.sha256(raw).hexdigest()
        records = chain(self.start, self.current)
        with mock.patch.object(RUNTIME, "load_policy", return_value=policy):
            packet = evaluate(records, kraken_receipt_raw=raw)
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertFalse(packet["acceptance"]["conditions"]["6_REPLACED_KRAKEN_BULK_BTC_REPLAY_DIAGNOSTIC"])
        self.assertIn("ACCEPTANCE_CONDITION_FAILED:6_KRAKEN_REPLAY_REQUIRED_RESULTS_NOT_OBSERVED",
                      packet["reasons"])
        self.assertTrue(all(v is False for v in packet["authority"].values()))

    def test_leadership_manifest_hashes_must_be_real_sha256(self):
        for window_hash, breadth_hash in ((None, None), ("", ""), ("x", "x"), (MANIFEST, None)):
            with self.subTest(window_hash=window_hash, breadth_hash=breadth_hash):
                row = record(self.current)
                row["leadership"]["windows"][RUNTIME.PILOT]["last_manifest_sha256"] = window_hash
                row["breadth"]["manifest_sha256"] = breadth_hash
                step = RUNTIME.evaluate_day(row, self.current, False)
                self.assertEqual(step["axes"]["LEADERSHIP"]["status"], "UNDEFINED")
                self.assertIn("LEADERSHIP_MANIFEST_BINDING_INVALID", step["reasons"])

    def test_owner_runtime_error_is_axis_missing_not_crash(self):
        class Broken:
            @staticmethod
            def median(values):
                return sorted(values)[len(values) // 2]

            @staticmethod
            def leadership_reference(windows):
                raise RuntimeError("LEADERSHIP_REFERENCE_INVALID: forced")

        with mock.patch.object(RUNTIME, "_recent_reference_module", return_value=Broken):
            packet = evaluate(chain(self.start, self.current))
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertIn("LEADERSHIP_DERIVATION_FAILED", packet["reasons"])

    def test_top_level_runtime_error_publishes_unknown(self):
        with mock.patch.object(RUNTIME, "build_chain", side_effect=RuntimeError("REFERENCE_FAIL: forced")):
            packet = evaluate(chain(self.start, self.current))
        self.assertEqual(packet["runtime_regime"], "UNKNOWN")
        self.assertEqual(packet["reasons"], ["RUNTIME_DERIVATION_FAILED"])
        self.assertTrue(all(v is False for v in packet["authority"].values()))


if __name__ == "__main__":
    unittest.main()
