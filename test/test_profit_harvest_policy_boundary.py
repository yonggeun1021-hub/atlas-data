#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from harvest_audit.profit_harvest_policy_boundary import (
    AUTHORITY_ALL_FALSE,
    ProfitHarvestPolicyBoundaryError,
    build_policy_boundary,
    load_contract,
    payload_sha256,
    validate_contract,
    validate_policy_boundary,
)

def contract() -> dict:
    return json.loads((ROOT / "config" / "profit_harvest_policy_contract.json").read_text())


def readiness() -> dict:
    value = {
        "schema_version": "profit_harvest_readiness/1",
        "as_of": "2026-08-25T16:23:01Z",
        "source": {"entry_proposal_boundary_sha256": "a" * 64, "public_code_commit_sha": "b" * 40, "audit_files_sha256": {"episode_ledger.json": "c" * 64}},
        "baseline": {"status": "VALIDATED_BASELINE_AUDIT_ONLY", "episode_count": 11},
        "policy": {"status": "NOT_COMPUTABLE_POLICY_PARAMETERS_UNRATIFIED", "grid_status": "ANALYTICAL_GRID_UNRATIFIED"},
        "harvest": {"status": "LOCKED_POLICY_UNRATIFIED", "recommended_action": "NONE", "harvest_review_items": [], "reduce_proposal": None, "exit_proposal": None, "trade_proposal": None, "blocking_reasons": ["PROFIT_HARVEST_POLICY_UNRATIFIED"]},
        "authority": {"review_only": True, "harvest_review_authorized": False, "reduce_authorized": False, "exit_authorized": False, "action_authorized": False, "order_authorized": False, "production_authorized": False, "trading_authorized": False},
    }
    value["readiness_sha256"] = payload_sha256(value)
    return value


class ProfitHarvestPolicyBoundaryTests(unittest.TestCase):
    def test_repository_contract_is_locked_and_numeric_parameter_free(self):
        loaded = load_contract(ROOT / "config" / "profit_harvest_policy_contract.json")
        self.assertEqual(loaded["approval_status"], "PROPOSED_UNRATIFIED")
        self.assertEqual(loaded["authority"], AUTHORITY_ALL_FALSE)

    def test_build_is_deterministic_and_non_executable(self):
        first = build_policy_boundary(contract(), readiness())
        second = build_policy_boundary(contract(), readiness())
        self.assertEqual(first, second)
        self.assertEqual(first["decision"], {"status": "LOCKED_POLICY_UNRATIFIED", "recommended_action": "NONE", "review_items": [], "harvest_proposal": None, "quantity_proposal": None, "reallocation_handoff": None})
        self.assertEqual(first["authority"], AUTHORITY_ALL_FALSE)

    def test_korea_and_crypto_cannot_be_promoted_to_official_kpi(self):
        for market in ("KOREA", "CRYPTO"):
            value = contract()
            value["population_authority"]["official_kpi_eligible_markets"].append(market)
            with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "POPULATION"):
                validate_contract(value)

    def test_outcome_labels_cannot_become_operational_inputs(self):
        value = contract(); value["population_authority"]["outcome_labels_are_operational_inputs"] = True
        with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "POPULATION"):
            validate_contract(value)

    def test_policy_number_is_rejected_even_in_an_extra_nested_field(self):
        value = contract(); value["policy_axes"]["trigger_eligibility"]["threshold"] = 1
        with self.assertRaises(ProfitHarvestPolicyBoundaryError):
            validate_contract(value)

    def test_each_policy_axis_is_required(self):
        for axis in tuple(contract()["policy_axes"]):
            value = contract(); del value["policy_axes"][axis]
            with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "AXES"):
                validate_contract(value)

    def test_design_preference_cannot_be_relabelled_as_ratified(self):
        for option in tuple(contract()["design_options"]):
            value = contract(); value["design_options"][option] = "RATIFIED"
            with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "DESIGN_OPTION"):
                validate_contract(value)

    def test_authority_promotion_is_rejected(self):
        for key in AUTHORITY_ALL_FALSE:
            if key == "review_only":
                continue
            value = contract(); value["authority"][key] = True
            with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "AUTHORITY_PROMOTION"):
                validate_contract(value)

    def test_upstream_action_review_or_proposal_is_rejected(self):
        mutations = (
            ("recommended_action", "HARVEST_PARTIAL"),
            ("harvest_review_items", [{"subject": "BTC"}]),
            ("reduce_proposal", {}),
            ("exit_proposal", {}),
            ("trade_proposal", {}),
        )
        for key, replacement in mutations:
            source = readiness(); source["harvest"][key] = replacement
            source["readiness_sha256"] = payload_sha256({k: v for k, v in source.items() if k != "readiness_sha256"})
            with self.assertRaises(ProfitHarvestPolicyBoundaryError):
                build_policy_boundary(contract(), source)

    def test_upstream_authority_promotion_is_rejected_after_resigning(self):
        for key in readiness()["authority"]:
            if key == "review_only":
                continue
            source = readiness(); source["authority"][key] = True
            source["readiness_sha256"] = payload_sha256({k: v for k, v in source.items() if k != "readiness_sha256"})
            with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "UPSTREAM_AUTHORITY"):
                build_policy_boundary(contract(), source)

    def test_upstream_hash_tamper_is_rejected(self):
        source = readiness(); source["baseline"]["episode_count"] = 99
        with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "HASH"):
            build_policy_boundary(contract(), source)

    def test_resigned_output_cannot_add_action_quantity_or_handoff(self):
        for key, replacement in (("recommended_action", "REDUCE"), ("quantity_proposal", {}), ("reallocation_handoff", {})):
            value = build_policy_boundary(contract(), readiness())
            value["decision"][key] = replacement
            value["boundary_sha256"] = payload_sha256({k: v for k, v in value.items() if k != "boundary_sha256"})
            with self.assertRaisesRegex(ProfitHarvestPolicyBoundaryError, "SEMANTIC_TAMPER"):
                validate_policy_boundary(value, contract(), readiness())

    def test_result_labels_never_create_operational_fields(self):
        serialized = json.dumps(build_policy_boundary(contract(), readiness()), sort_keys=True)
        for forbidden in ("return_pct", "mfe", "mae", "sell_ratio", "position_quantity", "order_id"):
            self.assertNotIn(forbidden, serialized)


if __name__ == "__main__":
    unittest.main()
