#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "portfolio" / "capital_flow_posture_reference.py"
SPEC = importlib.util.spec_from_file_location("capital_flow_posture_reference_tested", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class CapitalFlowPostureReferenceTest(unittest.TestCase):
    def test_current_inputs_expose_two_stage_capital_model_without_numbers(self):
        packet = MODULE.build_reference()
        self.assertEqual(packet["cross_market_flow"]["actual_money_flow"], "UNKNOWN")
        self.assertEqual(packet["cross_market_flow"]["comparison_status"], "PARTIAL_RELATIVE_STRENGTH_REFERENCE")
        self.assertEqual(packet["cross_market_flow"]["comparison_as_of_date"], "2026-08-28")
        self.assertEqual(packet["cross_market_flow"]["relative_strength_leader"], "US")
        self.assertEqual(packet["cross_market_flow"]["relative_strength_laggard"], "KR")
        self.assertEqual(packet["total_exposure_review"]["review"], "WAIT_INCOMPLETE_MARKET_SET")
        self.assertIsNone(packet["total_exposure_review"]["invested_target_pct"])
        self.assertIsNone(packet["total_exposure_review"]["cash_target_pct"])

    def test_each_market_has_review_priority_but_no_target_weight(self):
        packet = MODULE.build_reference()
        reviews = {row["market"]: row for row in packet["market_allocation_reviews"]}
        self.assertEqual(reviews["US"]["review_priority"], "RELATIVE_STRENGTH_LEADER_REFERENCE")
        self.assertEqual(reviews["KR"]["review_priority"], "RELATIVE_STRENGTH_LAGGARD_REFERENCE")
        self.assertEqual(reviews["CRYPTO"]["review_priority"], "WAIT_FOR_COMPLETE_REGIME")
        self.assertTrue(all(row["target_weight_pct"] is None for row in reviews.values()))

    def test_authority_boundary_keeps_capital_and_orders_closed(self):
        authority = MODULE.build_reference()["authority"]
        self.assertTrue(authority["paper_reference_display_authorized"])
        self.assertTrue(authority["relative_strength_comparison_authorized"])
        for key, value in authority.items():
            if key not in {"paper_reference_display_authorized", "relative_strength_comparison_authorized"}:
                self.assertFalse(value, key)

    def test_resigned_output_tamper_and_source_tamper_fail_closed(self):
        packet = MODULE.build_reference()
        self.assertEqual(MODULE.validate_reference(packet), packet)
        tampered = copy.deepcopy(packet)
        tampered["total_exposure_review"]["invested_target_pct"] = 80
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
            MODULE.validate_reference(tampered)

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            for name in (
                "latest_free_market_data.json",
                "latest_korea_market_signals.json",
                "latest_crypto_regime_refresh_status.json",
                "latest_paper_regime_reference.json",
            ):
                shutil.copy2(ROOT / "data" / name, root / "data" / name)
            source = json.loads((root / "data/latest_paper_regime_reference.json").read_text())
            source["markets"][0]["paper_reference"]["score"] = 5
            unsigned_source = copy.deepcopy(source)
            unsigned_source.pop("payload_sha256")
            source["payload_sha256"] = MODULE.payload_sha256(unsigned_source)
            (root / "data/latest_paper_regime_reference.json").write_text(json.dumps(source), encoding="utf-8")
            with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "SOURCE_REVALIDATION_FAILED"):
                MODULE.build_reference(root)

    def test_write_is_append_only_and_latest_is_identical(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            shutil.copytree(ROOT / "config", root / "config")
            (root / "data").mkdir()
            for name in (
                "latest_free_market_data.json",
                "latest_korea_market_signals.json",
                "latest_paper_regime_reference.json",
            ):
                shutil.copy2(ROOT / "data" / name, root / "data" / name)
            packet = MODULE.build_reference()
            evidence, latest = MODULE.write_packet(packet, root)
            self.assertEqual(evidence.read_bytes(), latest.read_bytes())
            self.assertEqual(MODULE.validate_reference(json.loads(latest.read_text())), packet)
            MODULE.write_packet(packet, root)


if __name__ == "__main__":
    unittest.main()
