#!/usr/bin/env python3
"""P3-06 expectations / estimate revision radar regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery" / "expectations_revision.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("expectations_revision", SOURCE)
CONTRACT = MODULE.load_contract()


def source_policy(**changes):
    value = {
        "schema_version": "consensus_estimate_source_policy/1",
        "contract_version": "expectations_revision_radar/1",
        "policy_id": "CONSENSUS.SOURCE.TEST.V1",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "effective_from": "2026-08-21",
        "effective_to": None,
        "sources": [{
            "source_id": "CONSENSUS.TEST",
            "allowed_hosts": ["consensus.example.com"],
            "markets": ["KOREA", "US"],
            "available_at_semantics": "provider publication timestamp captured before retrieval",
            "license_basis_ref": "test://license/consensus",
            "license_basis_sha256": "c" * 64,
            "permitted_use": "RESEARCH_DERIVED_FEATURES_ONLY",
        }],
        "authority": {
            "source_contract_only": True,
            "source_purchase_authorized": False,
            "importance_classification_authorized": False,
            "candidate_ranking_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    value.update(changes)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def vintage(
    vintage_id,
    estimate_as_of,
    value,
    marker,
    *,
    available_at=None,
    retrieved_at=None,
    status="EVIDENCE_AVAILABLE",
    reasons=None,
):
    available_at = available_at or estimate_as_of
    retrieved_at = retrieved_at or available_at
    return {
        "vintage_id": vintage_id,
        "estimate_as_of": estimate_as_of,
        "available_at": available_at,
        "retrieved_at": retrieved_at,
        "source_id": "CONSENSUS.TEST",
        "source_url": "https://consensus.example.com/v1/estimates",
        "source_sha256": marker * 64,
        "evidence_status": status,
        "estimate_value": value if status == "EVIDENCE_AVAILABLE" else None,
        "estimate_count": 12 if status == "EVIDENCE_AVAILABLE" else None,
        "unavailable_reasons": [] if reasons is None else reasons,
    }


def series(
    values=("10", "12.5"),
    *,
    series_id="US.TSM.REVENUE.FY2026",
    market="US",
    asset_id="US.XNYS.TSM",
):
    return {
        "series_id": series_id,
        "market": market,
        "asset_id": asset_id,
        "metric_type": "REVENUE",
        "fiscal_period_end": "2026-12-31",
        "estimate_statistic": "MEAN",
        "unit": "USD",
        "comparison_basis": "same-provider reported annual revenue consensus",
        "vintages": [
            vintage("VINTAGE.20260814", "2026-08-14T01:00:00Z", values[0], "a"),
            vintage("VINTAGE.20260821", "2026-08-21T01:00:00Z", values[1], "b"),
        ],
    }


def batch(rows=None):
    value = {
        "schema_version": "expectations_revision_radar_input/1",
        "contract_version": "expectations_revision_radar/1",
        "batch_id": "EXPECTATIONS.REVISION.TEST.20260821",
        "as_of_utc": "2026-08-21T03:00:00Z",
        "series": [series()] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class ExpectationsRevisionTests(unittest.TestCase):
    def build(self, rows=None, policy=None):
        return MODULE.build_packet(batch(rows), source_policy() if policy is None else policy, CONTRACT)

    def test_contract_has_no_default_source_and_closes_decision_authority(self):
        self.assertEqual(CONTRACT["repository_default_source_policy"], "ABSENT")
        self.assertTrue(CONTRACT["authority"]["raw_revision_observation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "raw_revision_observation_only":
                self.assertFalse(value, key)

    def test_upward_revision_is_reproduced_with_exact_vintage_lineage(self):
        packet = self.build()
        result = packet["series_results"][0]
        self.assertEqual(result["revision_direction"], "UPWARD")
        self.assertEqual(result["prior_estimate"], "10.000000000000")
        self.assertEqual(result["latest_estimate"], "12.500000000000")
        self.assertEqual(result["revision_change"], "2.500000000000")
        self.assertEqual([row["source_sha256"] for row in result["vintages"]], ["a" * 64, "b" * 64])
        self.assertEqual(packet["summary"]["UPWARD"], 1)
        self.assertEqual(packet["summary"]["case_count"], 1)

    def test_downward_is_a_case_but_unchanged_is_not(self):
        down = self.build([series(("12", "9"))])
        self.assertEqual(down["series_results"][0]["revision_direction"], "DOWNWARD")
        self.assertEqual(down["cases"][0]["revision_change"], "-3.000000000000")
        same = self.build([series(("10", "10"))])
        self.assertEqual(same["series_results"][0]["revision_direction"], "UNCHANGED")
        self.assertEqual(same["summary"]["case_count"], 0)

    def test_missing_vintage_is_unknown_not_zero_or_unchanged(self):
        value = series()
        value["vintages"][1] = vintage(
            "VINTAGE.20260821", "2026-08-21T01:00:00Z", None, "b",
            status="EVIDENCE_UNAVAILABLE", reasons=["EXACT_TARGET_ABSENT"],
        )
        packet = self.build([value])
        result = packet["series_results"][0]
        self.assertEqual(result["revision_direction"], "UNKNOWN_EVIDENCE")
        self.assertIsNone(result["prior_estimate"])
        self.assertIsNone(result["latest_estimate"])
        self.assertIsNone(result["revision_change"])
        self.assertEqual(packet["summary"]["case_count"], 0)

    def test_case_never_grants_importance_candidate_stage_or_action(self):
        case = self.build()["cases"][0]
        self.assertEqual(case["importance"], "UNRATIFIED")
        self.assertFalse(case["candidate_eligible"])
        self.assertIsNone(case["candidate_rank"])
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["action"])

    def test_unratified_future_or_ineffective_source_policy_fails_closed(self):
        cases = [
            (source_policy(status="DRAFT"), "SOURCE_POLICY_IDENTITY_INVALID"),
            (source_policy(ratified_at="2026-08-21T04:00:00Z"), "SOURCE_POLICY_RATIFIED_AFTER_AS_OF"),
            (source_policy(effective_from="2026-08-22"), "SOURCE_POLICY_NOT_EFFECTIVE"),
        ]
        for value, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(MODULE.ExpectationsRevisionError, error):
                self.build(policy=value)

    def test_source_host_market_and_time_must_match_ratified_contract(self):
        wrong_host = series()
        wrong_host["vintages"][0]["source_url"] = "https://other.example.com/v1/estimates"
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "SOURCE_URL_INVALID"):
            self.build([wrong_host])
        policy = source_policy()
        policy["sources"][0]["markets"] = ["KOREA"]
        policy["packet_sha256"] = MODULE.payload_sha256({key: value for key, value in policy.items() if key != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "SOURCE_MARKET_NOT_RATIFIED"):
            self.build(policy=policy)
        reversed_time = series()
        reversed_time["vintages"][0]["retrieved_at"] = "2026-08-13T23:00:00Z"
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "VINTAGE_TIME_ORDER_INVALID"):
            self.build([reversed_time])

    def test_duplicates_digest_and_authority_drift_fail_closed(self):
        duplicate = series()
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "SERIES_ID_DUPLICATE"):
            self.build([duplicate, copy.deepcopy(duplicate)])
        duplicated_vintage = series()
        duplicated_vintage["vintages"][1]["vintage_id"] = duplicated_vintage["vintages"][0]["vintage_id"]
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "VINTAGE_ID_DUPLICATE"):
            self.build([duplicated_vintage])
        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "INPUT_SHA_MISMATCH"):
            MODULE.build_packet(digest, source_policy(), CONTRACT)
        authority = batch()
        authority["authority"]["candidate_ranking_authorized"] = True
        authority["packet_sha256"] = MODULE.payload_sha256({key: value for key, value in authority.items() if key != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "INPUT_IDENTITY_INVALID"):
            MODULE.build_packet(authority, source_policy(), CONTRACT)

    def test_deterministic_permutation_safe_immutable_and_tamper_evident(self):
        korea = series(series_id="KR.005930.REVENUE.FY2026", market="KOREA", asset_id="KR.XKRX.005930")
        rows = [series(), korea]
        input_value, policy_value = batch(rows), source_policy()
        before = MODULE.canonical_json([input_value, policy_value])
        first = MODULE.build_packet(input_value, policy_value, CONTRACT)
        second = MODULE.build_packet(batch(list(reversed(rows))), source_policy(), CONTRACT)
        self.assertEqual(first["series_results"], second["series_results"])
        self.assertEqual(first["cases"], second["cases"])
        self.assertEqual(MODULE.canonical_json([input_value, policy_value]), before)
        tampered = copy.deepcopy(first)
        tampered["cases"][0]["candidate_eligible"] = True
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.ExpectationsRevisionError, "OUTPUT_CONTENT_MISMATCH"):
            MODULE.validate_packet(tampered, input_value, policy_value, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path, policy_path = temp / "input.json", temp / "policy.json"
            input_path.write_text(json.dumps(batch()), encoding="utf-8")
            policy_path.write_text(json.dumps(source_policy()), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, policy_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "expectations_revision_test.json"
            self.assertEqual(MODULE.run(input_path, policy_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
