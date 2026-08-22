#!/usr/bin/env python3
"""P2-03 minimal own-benchmark rotation_policy ratification regression.

Covers both the real ratified policy's construction (BuildRealPriceSide*)
and the real end-to-end proof against three genuinely different real
observation pairs:
  - the OLD 2026-08-19/2026-08-21 pair, fetched BEFORE this policy was
    ratified -- must be correctly REJECTED by the anti-lookahead check
    (AntiLookaheadRejectionTest), not silently accepted.
  - the 2026-08-18/2026-08-20 pair, fetched AFTER ratification --
    ROTATION_BUCKETS_OBSERVED, still correctly held back by Breadth
    (EndToEndRealRatifiedProofTest): this real evidence's Breadth
    first_seen_at is genuinely AFTER the current Leadership observation's
    own available_at (decision_time) -- BLOCKED, not a forced PASS.
  - the 2026-08-13/2026-08-14 pair (RealAvailableEndToEndProofTest):
    real evidence where Breadth's first_seen_at genuinely predates
    decision_time -- READY end-to-end for the first time, proving the
    PIT temporal-invariant correction (2026-08-22) is not merely
    theoretical. Buy/Stage/Action/Order/Production/trading authority
    stay closed regardless.
"""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "korea_capital_rotation_ledger_proof.py"
SPEC = importlib.util.spec_from_file_location("korea_capital_rotation_ledger_proof", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

OLD_PRIOR, OLD_CURRENT = "2026-08-19", "2026-08-21"  # pre-ratification evidence
NEW_PRIOR, NEW_CURRENT = "2026-08-18", "2026-08-20"  # post-ratification evidence, Breadth BLOCKED
AVAILABLE_PRIOR, AVAILABLE_CURRENT = "2026-08-13", "2026-08-14"  # Breadth AVAILABLE (real)


class LoadRealLeadershipPacketTest(unittest.TestCase):
    def test_missing_evidence_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "NO_LEADERSHIP_EVIDENCE_FOR_DATE"):
            MODULE.load_real_leadership_packet("1999-01-01")

    def test_blocked_evidence_fails_closed_not_silently_substituted(self):
        with tempfile.TemporaryDirectory() as tmp:
            original_root = MODULE.ROOT
            MODULE.ROOT = Path(tmp)
            path = Path(tmp) / "data" / "observations" / "korea_leadership_context" / "2026-08-20" / "packet.json"
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps({"outcome": "blocked", "reason": "LEADERSHIP_POLICY_UNRATIFIED"}), encoding="utf-8")
            try:
                with self.assertRaisesRegex(RuntimeError, "LEADERSHIP_NOT_POPULATED_FOR_DATE"):
                    MODULE.load_real_leadership_packet("2026-08-20")
            finally:
                MODULE.ROOT = original_root

    def test_real_committed_evidence_loads(self):
        packet = MODULE.load_real_leadership_packet(NEW_CURRENT)
        self.assertEqual(packet["market"], "KOREA")
        self.assertEqual(len(packet["relative_strength_observations"]), 48)


class BuildRealPriceSideRatifiedTest(unittest.TestCase):
    def test_uses_real_prior_and_current_observations(self):
        value, _ = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        self.assertEqual(value["prior_observation"]["observation_date"], NEW_PRIOR)
        self.assertEqual(value["current_observation"]["observation_date"], NEW_CURRENT)
        self.assertEqual(value["as_of_date"], NEW_CURRENT)

    def test_rotation_policy_is_ratified_for_real(self):
        _, policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        self.assertEqual(policy["approval_status"], "RATIFIED")
        self.assertEqual(policy["ratified_by"], "Atlas CIO")
        self.assertEqual(
            policy["ratified_at_utc"], MODULE.REAL_ROTATION_POLICY_RATIFIED_AT_UTC
        )
        self.assertEqual(
            policy["effective_from"], MODULE.REAL_ROTATION_POLICY_EFFECTIVE_FROM
        )
        self.assertIsNone(policy["effective_to"])

    def test_top_bottom_count_is_minimal_extremal_one_not_an_invented_threshold(self):
        _, policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        for scope in policy["benchmark_scopes"]:
            self.assertEqual(scope["top_count"], 1)
            self.assertEqual(scope["bottom_count"], 1)

    def test_scopes_use_real_ratified_p1_kr07_sector_identities(self):
        _, policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        scopes = {scope["benchmark_identity"]: scope for scope in policy["benchmark_scopes"]}
        self.assertEqual(set(scopes), {"KOSPI::코스피", "KOSDAQ::코스닥"})
        self.assertEqual(len(scopes["KOSPI::코스피"]["members"]), 24)
        self.assertEqual(len(scopes["KOSDAQ::코스닥"]["members"]), 22)
        for member in scopes["KOSPI::코스피"]["members"]:
            self.assertTrue(member["series_identity"].startswith("KOSPI::"))
        for member in scopes["KOSDAQ::코스닥"]["members"]:
            self.assertTrue(member["series_identity"].startswith("KOSDAQ::"))
        # benchmark_scopes order must satisfy korea_capital_rotation.py's
        # own ascending-order check (KOSDAQ before KOSPI).
        self.assertEqual(
            [s["benchmark_identity"] for s in policy["benchmark_scopes"]],
            sorted(s["benchmark_identity"] for s in policy["benchmark_scopes"]),
        )

    def test_no_real_p2_01_taxonomy_is_fabricated(self):
        value, _ = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        binding = value["taxonomy_binding"]
        self.assertEqual(binding["taxonomy_id"], "TAXONOMY.NOT_RATIFIED")
        self.assertEqual(binding["taxonomy_decision_sha256"], "0" * 64)
        self.assertEqual(binding["taxonomy_packet_sha256"], "0" * 64)


class AntiLookaheadRejectionTest(unittest.TestCase):
    """The real ratified policy's ratified_at_utc is fixed at the real
    moment it was ratified. Any observation pair whose prior evidence was
    already real (available_at) BEFORE that moment must be rejected if
    replayed under this policy -- proving the anti-cherry-picking
    property against real data, not a synthetic fixture."""

    def test_old_pre_ratification_evidence_pair_is_rejected(self):
        KCR = MODULE._load_module(
            "kcr_for_antilookahead_test", "rotation/korea_capital_rotation.py"
        )
        WIRE = MODULE._load_module(
            "wire_for_antilookahead_test", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side(OLD_PRIOR, OLD_CURRENT)
        source = WIRE.load_breadth_context_source(OLD_CURRENT)
        decision_time = value["current_observation"]["available_at"]
        breadth, _ = WIRE.build_coverage_context_breadth(OLD_CURRENT, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"
        ):
            KCR.build_packet(value, rotation_policy)


class EndToEndRealRatifiedProofTest(unittest.TestCase):
    """The genuinely NEW (post-ratification) 2026-08-18/2026-08-20 pair:
    rotation_policy_effective must flip to True and real ranks/buckets
    must appear, while Breadth (still available_at=null, a separate,
    still-open PR B boundary) keeps the overall decision BLOCKED --
    never PASS/READY."""

    def _build(self):
        KCR = MODULE._load_module("kcr_for_e2e_test", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_e2e_test", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        source = WIRE.load_breadth_context_source(NEW_CURRENT)
        decision_time = value["current_observation"]["available_at"]
        breadth, reason = WIRE.build_coverage_context_breadth(NEW_CURRENT, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        packet = KCR.build_packet(value, rotation_policy)
        return KCR, WIRE, packet, source, reason

    def test_real_rotation_effective_and_breadth_still_blocks(self):
        _, _, packet, _, _ = self._build()
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(packet["rotation_policy_effective"])
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "BLOCKED")
        self.assertFalse(packet["coverage_context"]["breadth"]["decision_eligible"])
        self.assertTrue(
            packet["authority"]["theme_ranking_within_benchmark_authorized"]
        )
        # trading/stage/production/candidate-ranking authority is
        # untouched by rotation_policy_effective becoming True.
        for key in (
            "trading_authorized", "production_authorized", "stage_promotion_authorized",
            "candidate_ranking_authorized",
        ):
            self.assertFalse(packet["authority"][key])

    def test_kospi_kosdaq_ranked_independently_no_cross_benchmark_leakage(self):
        _, _, packet, _, _ = self._build()
        scopes = {s["benchmark_identity"]: s for s in packet["benchmark_scopes"]}
        for benchmark, prefix in (("KOSPI::코스피", "KOSPI::"), ("KOSDAQ::코스닥", "KOSDAQ::")):
            scope = scopes[benchmark]
            self.assertEqual(len(scope["top_themes"]), 1)
            self.assertEqual(len(scope["bottom_themes"]), 1)
            for row in scope["theme_observations"]:
                self.assertTrue(row["series_identity"].startswith(prefix))
                self.assertIsNotNone(row["current_rank_within_benchmark"])
                self.assertIn(row["current_bucket"], {"TOP", "MIDDLE", "BOTTOM"})
        # Ranks are 1..N *within* each scope, never shared/cross-scoped.
        kospi_ranks = sorted(
            row["current_rank_within_benchmark"]
            for row in scopes["KOSPI::코스피"]["theme_observations"]
        )
        self.assertEqual(kospi_ranks, list(range(1, 25)))
        kosdaq_ranks = sorted(
            row["current_rank_within_benchmark"]
            for row in scopes["KOSDAQ::코스닥"]["theme_observations"]
        )
        self.assertEqual(kosdaq_ranks, list(range(1, 23)))

    def test_standalone_revalidation(self):
        KCR, _, packet, _, _ = self._build()
        checked = KCR.validate_packet(copy.deepcopy(packet))
        self.assertEqual(checked, packet)

    def test_rerun_is_byte_identical(self):
        _, _, first, _, _ = self._build()
        _, _, second, _, _ = self._build()
        self.assertEqual(first, second)

    def test_ledger_accepts_real_effective_packet(self):
        KCR, WIRE, packet, _, _ = self._build()
        LEDGER = MODULE._load_module(
            "ledger_for_e2e_test", "rotation/rotation_state_ledger.py"
        )
        state_policy = MODULE.build_state_policy(packet)
        ledger = LEDGER.apply_rotation(packet, state_policy, previous_ledger=None)
        self.assertEqual(ledger["status"], "STATE_HISTORY_OBSERVED")
        self.assertEqual(len(ledger["records"]), 46)
        for record in ledger["records"]:
            self.assertEqual(record["ledger_revision"], 1)
            self.assertEqual(record["market"], "KOREA")

    def test_briefing_pointer_and_orchestrator_render_breadth_blocked_not_pass(self):
        _, WIRE, packet, source, reason = self._build()
        pointer = WIRE.build_briefing_pointer(
            packet, reason, source,
            "data/observations/korea_breadth_context/2026-08-20/packet.json",
            generated_at=source["generated_at"],
        )
        self.assertEqual(pointer["rotation"]["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(pointer["rotation"]["rotation_policy_effective"])
        self.assertEqual(pointer["breadth"]["status"], "BLOCKED")

        daily_orchestrator = MODULE._load_module(
            "daily_orchestrator_for_e2e_test", "briefing/daily_orchestrator.py"
        )
        row = daily_orchestrator.build_korea_rotation(
            NEW_CURRENT, snapshot={"kind": "payload", "value": pointer}
        )
        self.assertNotEqual(row["status"], "READY")
        self.assertIn("KOREA_BREADTH_BLOCKED", row["reason"])
        # This is the required proof: once ratified, the reason string
        # no longer mentions the rotation policy as a blocker at all --
        # only Breadth remains.
        self.assertNotIn("ROTATION_POLICY_NOT_RATIFIED", row["reason"])
        self.assertNotIn("POLICY_NOT_EFFECTIVE", row["reason"])
        for authorized in row["authority"].values():
            self.assertFalse(authorized)


class TemporalAndScopeIntegrityTest(unittest.TestCase):
    """Negative tests layered on the real ratified policy + real evidence
    (not a synthetic library fixture) -- reversal, tamper, and
    cross-benchmark mixing must all still fail closed under the actual
    production policy object."""

    def test_prior_current_reversed_is_rejected(self):
        KCR = MODULE._load_module("kcr_for_reversal_test", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_reversal_test", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        value["prior_observation"], value["current_observation"] = (
            value["current_observation"], value["prior_observation"],
        )
        value["as_of_date"] = NEW_PRIOR
        source = WIRE.load_breadth_context_source(NEW_CURRENT)
        decision_time = value["current_observation"]["available_at"]
        breadth, _ = WIRE.build_coverage_context_breadth(NEW_PRIOR, 3, source, decision_time)
        # This will legitimately fail closed on either the date-order or
        # the as_of/breadth mismatch check -- both are real, both prove
        # reversal is rejected, not silently accepted.
        value["coverage_context"]["breadth"] = breadth
        with self.assertRaises(KCR.KoreaCapitalRotationError):
            KCR.build_packet(value, rotation_policy)

    def test_tampered_relative_strength_row_detected_by_payload_sha(self):
        KCR = MODULE._load_module("kcr_for_tamper_test", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_tamper_test", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        source = WIRE.load_breadth_context_source(NEW_CURRENT)
        decision_time = value["current_observation"]["available_at"]
        breadth, _ = WIRE.build_coverage_context_breadth(NEW_CURRENT, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        # Tamper with one real relative_strength_vs_benchmark value
        # without updating the upstream packet's own payload_sha256.
        value["current_observation"]["relative_strength_observations"][0][
            "relative_strength_vs_benchmark"
        ] = "9.999999999999"
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "UPSTREAM_PAYLOAD_SHA_MISMATCH"
        ):
            KCR.build_packet(value, rotation_policy)

    def test_cross_benchmark_scope_mixing_is_rejected(self):
        KCR = MODULE._load_module("kcr_for_mixing_test", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_mixing_test", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side(NEW_PRIOR, NEW_CURRENT)
        source = WIRE.load_breadth_context_source(NEW_CURRENT)
        decision_time = value["current_observation"]["available_at"]
        breadth, _ = WIRE.build_coverage_context_breadth(NEW_CURRENT, 3, source, decision_time)
        value["coverage_context"]["breadth"] = breadth
        scopes = {s["benchmark_identity"]: s for s in rotation_policy["benchmark_scopes"]}
        kosdaq_member = scopes["KOSDAQ::코스닥"]["members"].pop()
        # Smuggle a real KOSDAQ series into the KOSPI scope's member list.
        scopes["KOSPI::코스피"]["members"].append(kosdaq_member)
        scopes["KOSPI::코스피"]["members"].sort(key=lambda m: m["series_identity"])
        with self.assertRaises(KCR.KoreaCapitalRotationError):
            KCR.build_packet(value, rotation_policy)


class RealAvailableEndToEndProofTest(unittest.TestCase):
    """The real 2026-08-13/2026-08-14 pair: Breadth for 2026-08-13 was
    genuinely fetched BEFORE Leadership for 2026-08-13 (real run
    32563091197 then 32563128463), and Leadership for 2026-08-14 was
    fetched after Breadth for 2026-08-14 (real run 32563198793 then
    32563230714) -- so Breadth's real first_seen_at genuinely predates
    decision_time (the current Leadership observation's own real
    available_at). This is the actual, non-synthetic proof that the PIT
    temporal-invariant correction produces a real READY end-to-end
    result once the underlying evidence genuinely supports it -- not a
    forced PASS, and never any Buy/Stage/Action/Order/Production/trading
    authority."""

    def test_run_produces_real_available_breadth_and_ready_briefing(self):
        result = MODULE.run(AVAILABLE_PRIOR, AVAILABLE_CURRENT, None, None)
        packet = result["rotation_packet"]
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(packet["rotation_policy_effective"])
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "AVAILABLE")
        self.assertTrue(packet["coverage_context"]["breadth"]["decision_eligible"])
        for field in (
            "trading_authorized", "production_authorized", "stage_promotion_authorized",
            "candidate_ranking_authorized", "regime_input_authorized",
        ):
            self.assertFalse(packet["authority"][field])

        KCR = MODULE._load_module(
            "kcr_for_available_test", "rotation/korea_capital_rotation.py"
        )
        checked = KCR.validate_packet(copy.deepcopy(packet))
        self.assertEqual(checked, packet)

        pointer = result["pointer"]
        self.assertEqual(pointer["rotation"]["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertEqual(pointer["breadth"]["status"], "AVAILABLE")
        self.assertTrue(pointer["breadth"]["decision_eligible"])

        daily_orchestrator = MODULE._load_module(
            "daily_orchestrator_for_available_test", "briefing/daily_orchestrator.py"
        )
        row = daily_orchestrator.build_korea_rotation(
            AVAILABLE_CURRENT, snapshot={"kind": "payload", "value": pointer}
        )
        self.assertEqual(row["status"], "READY")
        self.assertIsNone(row["reason"])
        for authorized in row["authority"].values():
            self.assertFalse(authorized)

    def test_rerun_is_byte_identical(self):
        first = MODULE.run(AVAILABLE_PRIOR, AVAILABLE_CURRENT, None, None)
        second = MODULE.run(AVAILABLE_PRIOR, AVAILABLE_CURRENT, None, None)
        self.assertEqual(first["rotation_packet"], second["rotation_packet"])
        self.assertEqual(first["pointer"], second["pointer"])

    def test_ledger_accepts_real_available_packet(self):
        LEDGER = MODULE._load_module(
            "ledger_for_available_test", "rotation/rotation_state_ledger.py"
        )
        result = MODULE.run(AVAILABLE_PRIOR, AVAILABLE_CURRENT, None, None)
        packet = result["rotation_packet"]
        state_policy = MODULE.build_state_policy(packet)
        ledger = LEDGER.apply_rotation(packet, state_policy, previous_ledger=None)
        self.assertEqual(ledger["status"], "STATE_HISTORY_OBSERVED")
        self.assertEqual(len(ledger["records"]), 46)


if __name__ == "__main__":
    unittest.main()
