#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
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
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name)
        shutil.copytree(ROOT / "config", self.root / "config")
        (self.root / "data").mkdir()
        for name in (
            "latest_free_market_data.json",
            "latest_korea_market_signals.json",
            "latest_crypto_regime_refresh_status.json",
        ):
            shutil.copy2(ROOT / "data" / name, self.root / "data" / name)
        paper = MODULE.PAPER_REGIME.build_reference(self.root)
        MODULE.PAPER_REGIME.write_packet(paper, self.root)

    def tearDown(self):
        self._temp.cleanup()

    def test_current_inputs_expose_two_stage_capital_model_without_numbers(self):
        packet = MODULE.build_reference(self.root)
        self.assertEqual(packet["cross_market_flow"]["actual_money_flow"], "UNKNOWN")
        self.assertIn(
            packet["cross_market_flow"]["comparison_status"],
            {"UNKNOWN", "PARTIAL_RELATIVE_STRENGTH_REFERENCE", "THREE_MARKET_RELATIVE_STRENGTH_REFERENCE"},
        )
        self.assertEqual(packet["total_exposure_review"]["review"], "WAIT_INCOMPLETE_MARKET_SET")
        self.assertIsNone(packet["total_exposure_review"]["invested_target_pct"])
        self.assertIsNone(packet["total_exposure_review"]["cash_target_pct"])

    def test_each_market_has_review_priority_but_no_target_weight(self):
        packet = MODULE.build_reference(self.root)
        reviews = {row["market"]: row for row in packet["market_allocation_reviews"]}
        self.assertEqual(set(reviews), {"US", "KR", "CRYPTO"})
        self.assertEqual(reviews["CRYPTO"]["review_priority"], "WAIT_FOR_COMPLETE_REGIME")
        self.assertTrue(all(row["target_weight_pct"] is None for row in reviews.values()))

        leaders = [row["market"] for row in reviews.values() if row["review_priority"] == "RELATIVE_STRENGTH_LEADER_REFERENCE"]
        laggards = [row["market"] for row in reviews.values() if row["review_priority"] == "RELATIVE_STRENGTH_LAGGARD_REFERENCE"]
        expected_leader = leaders[0] if len(leaders) == 1 else None
        expected_laggard = laggards[0] if len(laggards) == 1 else None
        self.assertEqual(packet["cross_market_flow"]["relative_strength_leader"], expected_leader)
        self.assertEqual(packet["cross_market_flow"]["relative_strength_laggard"], expected_laggard)

    def test_p2_com_01_contract_identity_is_hash_bound_and_read(self):
        packet = MODULE.build_reference(self.root)
        sources = {row["source_type"]: row for row in packet["sources"]}
        regime = sources["P1_PAPER_REGIME_REFERENCE_PACKET"]
        self.assertEqual(regime["schema_version"], "paper_regime_reference/v2")
        self.assertEqual(regime["contract_version"], "paper_regime_reference_policy/v1")
        self.assertEqual(len(regime["payload_sha256"]), 64)

        flow = sources["P2_COM_01_CROSS_ASSET_FLOW_CONTRACT"]
        flow_path = self.root / flow["path"]
        self.assertEqual(flow["sha256"], hashlib.sha256(flow_path.read_bytes()).hexdigest())
        self.assertEqual(flow["contract_version"], "cross_asset_flow_evidence/1")
        self.assertEqual(flow["output_schema_version"], "cross_asset_flow_evidence_packet/1")
        self.assertEqual(flow["cross_market_assessment_status"], "UNKNOWN")
        self.assertFalse(flow["cross_market_flow_claim_authorized"])
        self.assertEqual(
            packet["generation_id"],
            MODULE.payload_sha256({
                "policy_sha256": MODULE.file_sha256(
                    self.root / "config/capital_flow_posture_reference_policy_v1.json"
                ),
                "sources": packet["sources"],
            }),
        )

    def test_relative_candidates_are_not_promoted_to_actual_flow(self):
        packet = MODULE.build_reference(self.root)
        flow = packet["cross_market_flow"]
        candidates = packet["flow_candidates"]
        self.assertEqual(candidates["receiver_candidate"]["market"], flow["relative_strength_leader"])
        self.assertEqual(candidates["donor_candidate"]["market"], flow["relative_strength_laggard"])
        self.assertEqual(candidates["actual_flow_claim"], "UNKNOWN")
        self.assertIsNone(candidates["confidence"])
        self.assertEqual(candidates["transition"], {
            "status": "UNKNOWN", "source": "P2_COM_03_APPEND_ONLY_LEDGER",
        })
        self.assertIsNone(candidates["persistence"]["observation_count"])
        self.assertEqual(
            candidates["invalidation"]["status"],
            "NOT_COMPUTABLE_POLICY_UNRATIFIED",
        )

    def test_authority_boundary_keeps_capital_and_orders_closed(self):
        authority = MODULE.build_reference(self.root)["authority"]
        self.assertTrue(authority["paper_reference_display_authorized"])
        self.assertTrue(authority["relative_strength_comparison_authorized"])
        for key, value in authority.items():
            if key not in {"paper_reference_display_authorized", "relative_strength_comparison_authorized"}:
                self.assertFalse(value, key)

    def test_resigned_output_tamper_and_source_tamper_fail_closed(self):
        packet = MODULE.build_reference(self.root)
        self.assertEqual(MODULE.validate_reference(packet, self.root), packet)
        tampered = copy.deepcopy(packet)
        tampered["total_exposure_review"]["invested_target_pct"] = 80
        unsigned = copy.deepcopy(tampered)
        unsigned.pop("payload_sha256")
        tampered["payload_sha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "REFERENCE_REDERIVATION_MISMATCH"):
            MODULE.validate_reference(tampered, self.root)

        source = json.loads((self.root / "data/latest_paper_regime_reference.json").read_text())
        source["markets"][0]["paper_reference"]["score"] = 5
        unsigned_source = copy.deepcopy(source)
        unsigned_source.pop("payload_sha256")
        source["payload_sha256"] = MODULE.payload_sha256(unsigned_source)
        (self.root / "data/latest_paper_regime_reference.json").write_text(json.dumps(source), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "SOURCE_REVALIDATION_FAILED"):
            MODULE.build_reference(self.root)

    def test_policy_identity_and_boolean_types_fail_closed(self):
        path = self.root / "config/capital_flow_posture_reference_policy_v1.json"
        original = json.loads(path.read_text())
        cases = [
            ("schema_version", True, "POLICY_VERSION_INVALID"),
            ("status", "RATIFIED", "POLICY_STATUS_INVALID"),
        ]
        for key, value, code in cases:
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed[key] = value
                path.write_text(json.dumps(changed), encoding="utf-8")
                with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, code):
                    MODULE.build_reference(self.root)
        changed = copy.deepcopy(original)
        changed["authority"]["order_authorized"] = 0
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(MODULE.CapitalFlowPostureReferenceError, "POLICY_AUTHORITY_INVALID"):
            MODULE.build_reference(self.root)

    def test_p2_com_01_contract_semantic_tamper_fails_even_with_new_file_digest(self):
        path = self.root / "config/cross_asset_flow_evidence_contract.json"
        value = json.loads(path.read_text())
        value["authority"]["cross_market_flow_claim_authorized"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaisesRegex(
            MODULE.CapitalFlowPostureReferenceError,
            "CROSS_ASSET_FLOW_CONTRACT_REVALIDATION_FAILED",
        ):
            MODULE.build_reference(self.root)

    def test_write_is_append_only_and_latest_is_identical(self):
        packet = MODULE.build_reference(self.root)
        evidence, latest = MODULE.write_packet(packet, self.root)
        self.assertEqual(evidence.read_bytes(), latest.read_bytes())
        self.assertEqual(MODULE.validate_reference(json.loads(latest.read_text()), self.root), packet)
        MODULE.write_packet(packet, self.root)


if __name__ == "__main__":
    unittest.main()
