#!/usr/bin/env python3
"""P2-03 real Leadership observation_pair -> ledger/briefing e2e proof regression."""
from __future__ import annotations

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
        packet = MODULE.load_real_leadership_packet("2026-08-21")
        self.assertEqual(packet["market"], "KOREA")
        self.assertEqual(len(packet["relative_strength_observations"]), 48)


class BuildRealPriceSideTest(unittest.TestCase):
    def test_uses_real_prior_and_current_observations(self):
        value, policy = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
        self.assertEqual(value["prior_observation"]["observation_date"], "2026-08-19")
        self.assertEqual(value["current_observation"]["observation_date"], "2026-08-21")
        self.assertEqual(value["as_of_date"], "2026-08-21")

    def test_rotation_policy_is_explicitly_unratified_never_fabricated(self):
        _, policy = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
        self.assertEqual(policy["approval_status"], "UNRATIFIED")
        self.assertIsNone(policy["ratified_by"])
        self.assertIsNone(policy["ratified_at_utc"])

    def test_scopes_use_real_ratified_p1_kr07_sector_identities(self):
        _, policy = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
        scopes = {scope["benchmark_identity"]: scope for scope in policy["benchmark_scopes"]}
        self.assertEqual(set(scopes), {"KOSPI::코스피", "KOSDAQ::코스닥"})
        self.assertEqual(len(scopes["KOSPI::코스피"]["members"]), 24)
        self.assertEqual(len(scopes["KOSDAQ::코스닥"]["members"]), 22)
        for member in scopes["KOSPI::코스피"]["members"]:
            self.assertTrue(member["series_identity"].startswith("KOSPI::"))
        # benchmark_scopes order must satisfy korea_capital_rotation.py's
        # own ascending-order check.
        self.assertEqual(
            [s["benchmark_identity"] for s in policy["benchmark_scopes"]],
            sorted(s["benchmark_identity"] for s in policy["benchmark_scopes"]),
        )

    def test_no_real_p2_01_taxonomy_is_fabricated(self):
        value, _ = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
        binding = value["taxonomy_binding"]
        self.assertEqual(binding["taxonomy_id"], "TAXONOMY.NOT_RATIFIED")
        self.assertEqual(binding["taxonomy_decision_sha256"], "0" * 64)


class EndToEndRealProofTest(unittest.TestCase):
    def test_real_breadth_blocked_and_real_rotation_policy_not_effective(self):
        KCR = MODULE._load_module("kcr_for_e2e_test", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_e2e_test", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
        source = WIRE.load_breadth_context_source("2026-08-21")
        breadth, reason = WIRE.build_coverage_context_breadth("2026-08-21", 3, source)
        value["coverage_context"]["breadth"] = breadth
        packet = KCR.build_packet(value, rotation_policy)

        # Real, independently re-derived facts -- not asserted about a
        # mock, this literally re-derives from the real committed
        # evidence every time this test runs.
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "BLOCKED")
        self.assertFalse(packet["coverage_context"]["breadth"]["decision_eligible"])
        # A rotation policy that never becomes effective must never
        # silently emit ranks/buckets.
        for scope in packet["benchmark_scopes"]:
            self.assertEqual(scope["top_themes"], [])
            self.assertEqual(scope["bottom_themes"], [])
            for row in scope["theme_observations"]:
                self.assertIsNone(row["current_rank_within_benchmark"])
                self.assertIsNone(row["bucket_transition"])

        # Standalone re-verification (never trust the just-built packet).
        checked = KCR.validate_packet(__import__("copy").deepcopy(packet))
        self.assertEqual(checked, packet)

    def test_rerun_is_byte_identical(self):
        KCR = MODULE._load_module("kcr_for_e2e_test2", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_e2e_test2", "rotation/korea_capital_rotation_ledger_wire.py"
        )

        def build():
            value, rotation_policy = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
            source = WIRE.load_breadth_context_source("2026-08-21")
            breadth, _ = WIRE.build_coverage_context_breadth("2026-08-21", 3, source)
            value["coverage_context"]["breadth"] = breadth
            return KCR.build_packet(value, rotation_policy)

        first = build()
        second = build()
        self.assertEqual(first, second)

    def test_briefing_pointer_surfaces_both_breadth_and_rotation_status(self):
        KCR = MODULE._load_module("kcr_for_e2e_test3", "rotation/korea_capital_rotation.py")
        WIRE = MODULE._load_module(
            "wire_for_e2e_test3", "rotation/korea_capital_rotation_ledger_wire.py"
        )
        value, rotation_policy = MODULE.build_real_price_side("2026-08-19", "2026-08-21")
        source = WIRE.load_breadth_context_source("2026-08-21")
        breadth, reason = WIRE.build_coverage_context_breadth("2026-08-21", 3, source)
        value["coverage_context"]["breadth"] = breadth
        packet = KCR.build_packet(value, rotation_policy)
        pointer = WIRE.build_briefing_pointer(
            packet, reason, source,
            "data/observations/korea_breadth_context/2026-08-21/packet.json",
            generated_at=source["generated_at"],
        )
        self.assertEqual(pointer["rotation"]["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(pointer["rotation"]["rotation_policy_effective"])
        self.assertEqual(pointer["breadth"]["status"], "BLOCKED")

        daily_orchestrator = MODULE._load_module(
            "daily_orchestrator_for_e2e_test", "briefing/daily_orchestrator.py"
        )
        row = daily_orchestrator.build_korea_rotation(
            "2026-08-21", snapshot={"kind": "payload", "value": pointer}
        )
        self.assertEqual(row["status"], "POLICY_BLOCKED")
        self.assertIn("KOREA_BREADTH_BLOCKED", row["reason"])
        self.assertIn("KOREA_ROTATION_POLICY_NOT_EFFECTIVE", row["reason"])
        for value_ in row["authority"].values():
            self.assertFalse(value_)


if __name__ == "__main__":
    unittest.main()
