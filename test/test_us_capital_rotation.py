"""P2-02 policy-gated US Theme capital-rotation regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "rotation" / "us_capital_rotation.py"
SPEC = importlib.util.spec_from_file_location("us_capital_rotation", MODULE_PATH)
UCR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(UCR)

TAXONOMY_DECISION_SHA = "a" * 64
TAXONOMY_PACKET_SHA = "b" * 64
UPSTREAM_TAXONOMY_SHA = "c" * 64


def group(theme_id: str, relative_strength: str) -> dict:
    return {
        "group_id": theme_id,
        "observed_session_count": 3,
        "minimum_daily_member_count": 2,
        "required_minimum_member_count": 1,
        "cumulative_gross_return": "1.05",
        "relative_strength_vs_benchmark": relative_strength,
        "classification": "UNDEFINED",
    }


def leadership_packet(
    observation_date: str,
    available_at: str,
    values: dict[str, str],
) -> dict:
    if observation_date == "2026-08-18":
        first_input, first_return = "2026-08-13", "2026-08-14"
        daily_dates = ["2026-08-14", "2026-08-17", "2026-08-18"]
    else:
        first_input, first_return = "2026-08-17", "2026-08-18"
        daily_dates = ["2026-08-18", "2026-08-19", "2026-08-20"]
    packet = {
        "schema_version": 1,
        "contract_version": "us_leadership_contract/v1",
        "transform_version": "us_leadership/v1",
        "market": "US",
        "measurement": "us_cross_sectional_leadership_observation",
        "status": "OBSERVED_UNCLASSIFIED",
        "observation_date": observation_date,
        "available_at": available_at,
        "benchmark_asset": "SPY",
        "window": {
            "first_input_session": first_input,
            "first_return_session": first_return,
            "last_return_session": observation_date,
            "lookback_sessions": 3,
            "exact_expected_sessions": True,
        },
        "temporal_eligibility": {
            "run_mode": "FORWARD_SHADOW",
            "price_basis": "RAW",
            "eligibility": "FORWARD_PIT_QUALIFIED",
            "reason_code": "FORWARD_CUTOFF_SATISFIED",
            "authoritative_historical_pit": False,
            "forward_pit_qualified": True,
        },
        "asset_relative_strength": [
            {
                "asset": asset,
                "observed_session_count": 3,
                "cumulative_gross_return": "1.05",
                "relative_strength_vs_benchmark": relative,
                "classification": "UNDEFINED",
            }
            for asset, relative in (
                ("F", "0.10"), ("NVDA", "0.20"), ("SPY", "0")
            )
        ],
        "partial_window_assets": [],
        "group_relative_strength": [
            group(theme_id, values[theme_id]) for theme_id in sorted(values)
        ],
        "daily_relative_participation": [
            {
                "session_date": day,
                "eligible_non_benchmark_count": 2,
                "outperforming_benchmark_count": 1,
                "outperformance_participation_fraction": "0.5",
                "required_group_member_counts": [
                    {"group_id": theme_id, "member_count": 2}
                    for theme_id in sorted(values)
                ],
            }
            for day in daily_dates
        ],
        "retention": {
            "input_policy": "transient_memory_or_stdin_only",
            "output_policy": "non_reconstructive_derived_observations_only",
            "vendor_rows_emitted": False,
            "vendor_prices_emitted": False,
            "reconstructive_series_emitted": False,
        },
        "policies": {
            "leadership": {
                "policy_version": "leadership/test-v1",
                "policy_sha256": "d" * 64,
                "approval_status": "RATIFIED",
                "session_calendar_source": "synthetic_xnys/v1",
            },
            "universe": {
                "policy_version": "universe/test-v1",
                "policy_sha256": "e" * 64,
                "approval_status": "RATIFIED",
                "membership_kind": "point_in_time_source_coverage",
            },
            "taxonomy": {
                "policy_version": "taxonomy/test-v1",
                "policy_sha256": UPSTREAM_TAXONOMY_SHA,
                "approval_status": "RATIFIED",
                "effective_dated": True,
            },
        },
        "lineage": {
            "input_sha256": "f" * 64,
            "source_temporal_contract": "atlas_price_pit_contract.py/v0.1",
            "session_count": 4,
            "return_session_count": 3,
            "session_coverage_complete": True,
            "current_membership_backfill_authorized": False,
        },
    }
    for field in UCR.AUTHORITY_FIELDS:
        packet[field] = False
    return packet


def taxonomy_binding() -> dict:
    return {
        "taxonomy_contract_version": "theme_taxonomy/1",
        "taxonomy_id": "TAXONOMY.GLOBAL.2026",
        "taxonomy_decision_id": "DECISION.P2.01",
        "taxonomy_decision_sha256": TAXONOMY_DECISION_SHA,
        "taxonomy_packet_sha256": TAXONOMY_PACKET_SHA,
        "upstream_taxonomy_policy_sha256": UPSTREAM_TAXONOMY_SHA,
    }


def input_packet() -> dict:
    return {
        "schema_version": "us_capital_rotation_input/1",
        "as_of_date": "2026-08-20",
        "taxonomy_binding": taxonomy_binding(),
        "prior_observation": leadership_packet(
            "2026-08-18",
            "2026-08-18T20:20:00-04:00",
            {
                "THEME.COMPUTE": "0.30",
                "THEME.NETWORK": "0.10",
                "THEME.POWER": "-0.10",
            },
        ),
        "current_observation": leadership_packet(
            "2026-08-20",
            "2026-08-20T20:20:00-04:00",
            {
                "THEME.COMPUTE": "0.10",
                "THEME.NETWORK": "0.40",
                "THEME.POWER": "-0.20",
            },
        ),
    }


def policy(status: str = "RATIFIED") -> dict:
    ratified = status == "RATIFIED"
    return {
        "schema_version": "us_capital_rotation_policy/1",
        "policy_id": "POLICY.P2.02.TEST",
        "approval_status": status,
        "ratified_by": "Atlas CIO" if ratified else None,
        "ratified_at_utc": "2026-08-17T12:00:00Z" if ratified else None,
        "effective_from": "2026-08-01",
        "effective_to": None,
        "taxonomy_decision_sha256": TAXONOMY_DECISION_SHA,
        "taxonomy_packet_sha256": TAXONOMY_PACKET_SHA,
        "upstream_taxonomy_policy_sha256": UPSTREAM_TAXONOMY_SHA,
        "theme_ids": ["THEME.COMPUTE", "THEME.NETWORK", "THEME.POWER"],
        "ranking_metric": "GROUP_RELATIVE_STRENGTH_VS_BENCHMARK",
        "ranking_order": "DESCENDING",
        "tie_break": "THEME_ID_ASC",
        "top_count": 1,
        "bottom_count": 1,
        "maximum_calendar_gap_days": 5,
    }


class USCapitalRotationTests(unittest.TestCase):
    def test_effective_ratified_policy_reproduces_rank_buckets_and_transitions(self):
        packet = UCR.build_packet(input_packet(), policy())
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(packet["rotation_policy_effective"])
        self.assertEqual(packet["top_themes"], ["THEME.NETWORK"])
        self.assertEqual(packet["bottom_themes"], ["THEME.POWER"])
        rows = {row["theme_id"]: row for row in packet["theme_observations"]}
        self.assertEqual(rows["THEME.NETWORK"]["prior_rank"], 2)
        self.assertEqual(rows["THEME.NETWORK"]["current_rank"], 1)
        self.assertEqual(rows["THEME.NETWORK"]["rank_change"], 1)
        self.assertEqual(rows["THEME.NETWORK"]["bucket_transition"], "MIDDLE_TO_TOP")
        self.assertEqual(rows["THEME.COMPUTE"]["bucket_transition"], "TOP_TO_MIDDLE")
        self.assertEqual(rows["THEME.POWER"]["bucket_transition"], "BOTTOM_TO_BOTTOM")
        self.assertEqual(rows["THEME.COMPUTE"]["relative_strength_change"], "-0.2")

    def test_unratified_policy_preserves_raw_delta_without_ranking_authority(self):
        packet = UCR.build_packet(input_packet(), policy("UNRATIFIED"))
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertIsNone(packet["ranking_method"])
        self.assertEqual(packet["top_themes"], [])
        for row in packet["theme_observations"]:
            self.assertIsNone(row["prior_rank"])
            self.assertIsNone(row["current_bucket"])
            self.assertIsNone(row["bucket_transition"])
            self.assertEqual(row["p2_state"], "UNDEFINED_PENDING_P2_05")

    def test_future_or_expired_policy_is_not_effective_for_both_observations(self):
        future = policy()
        future["effective_from"] = "2026-08-19"
        self.assertFalse(UCR.build_packet(input_packet(), future)["rotation_policy_effective"])
        expired = policy()
        expired["effective_to"] = "2026-08-20"
        self.assertFalse(UCR.build_packet(input_packet(), expired)["rotation_policy_effective"])

    def test_policy_must_be_ratified_before_prior_observation(self):
        value = policy()
        value["ratified_at_utc"] = "2026-08-19T00:21:00Z"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"):
            UCR.build_packet(input_packet(), value)
        value = policy("UNRATIFIED")
        value["ratified_by"] = "Fake proof"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UNRATIFIED_POLICY_PROOF_FORBIDDEN"):
            UCR.build_packet(input_packet(), value)

    def test_taxonomy_decision_packet_and_upstream_policy_are_exactly_bound(self):
        for field, code in (
            ("taxonomy_decision_sha256", "POLICY_TAXONOMY_DECISION_MISMATCH"),
            ("taxonomy_packet_sha256", "POLICY_TAXONOMY_PACKET_MISMATCH"),
            ("upstream_taxonomy_policy_sha256", "POLICY_UPSTREAM_TAXONOMY_MISMATCH"),
        ):
            with self.subTest(field=field):
                value = policy()
                value[field] = "9" * 64
                with self.assertRaisesRegex(UCR.USCapitalRotationError, code):
                    UCR.build_packet(input_packet(), value)
        value = input_packet()
        value["current_observation"]["policies"]["taxonomy"]["policy_sha256"] = "9" * 64
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_TAXONOMY_BINDING_MISMATCH"):
            UCR.build_packet(value, policy())

    def test_theme_sets_cannot_drift_or_expand_outside_policy(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["group_id"] = "THEME.EXTRA"
        for row in value["current_observation"]["daily_relative_participation"]:
            row["required_group_member_counts"][0]["group_id"] = "THEME.EXTRA"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_THEME_SET_DRIFT"):
            UCR.build_packet(value, policy())
        value_policy = policy()
        value_policy["theme_ids"] = value_policy["theme_ids"][:-1]
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_THEME_SET_MISMATCH"):
            UCR.build_packet(input_packet(), value_policy)

    def test_observation_date_available_time_and_gap_are_fail_closed(self):
        value = input_packet()
        prior = value["prior_observation"]
        prior["observation_date"] = "2026-08-20"
        prior["available_at"] = "2026-08-20T19:20:00-04:00"
        prior["window"].update({
            "first_input_session": "2026-08-17",
            "first_return_session": "2026-08-18",
            "last_return_session": "2026-08-20",
        })
        for row, day in zip(
            prior["daily_relative_participation"],
            ("2026-08-18", "2026-08-19", "2026-08-20"),
        ):
            row["session_date"] = day
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "OBSERVATION_DATE_ORDER_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["prior_observation"]["available_at"] = "2026-08-21T00:21:00Z"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "OBSERVATION_AVAILABLE_AT_ORDER_INVALID"):
            UCR.build_packet(value, policy())
        value_policy = policy()
        value_policy["maximum_calendar_gap_days"] = 1
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "OBSERVATION_GAP_EXCEEDS_POLICY"):
            UCR.build_packet(input_packet(), value_policy)

    def test_only_forward_pit_closed_authority_upstream_is_accepted(self):
        value = input_packet()
        value["prior_observation"]["status"] = "CAUSAL_RESEARCH_ONLY"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_IDENTITY_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["current_observation"]["ranking_authorized"] = True
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_AUTHORITY_EXPANDED"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["prior_observation"]["policies"]["universe"]["approval_status"] = "UNRATIFIED"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_POLICY_UNRATIFIED"):
            UCR.build_packet(value, policy())

    def test_group_schema_order_counts_and_numbers_are_strict(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"].reverse()
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_GROUP_ORDER_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["observed_session_count"] = 2
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_GROUP_SEMANTICS_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["relative_strength_vs_benchmark"] = "NaN"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_GROUP_RELATIVE_STRENGTH_INVALID"):
            UCR.build_packet(value, policy())

    def test_policy_is_exact_and_top_bottom_cannot_overlap(self):
        value = policy()
        value["score"] = 1
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_FIELDS_MISMATCH"):
            UCR.build_packet(input_packet(), value)
        value = policy()
        value["top_count"] = 2
        value["bottom_count"] = 2
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_BUCKETS_OVERLAP"):
            UCR.build_packet(input_packet(), value)

    def test_equal_metric_uses_explicit_theme_id_tie_break(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["relative_strength_vs_benchmark"] = "0.40"
        packet = UCR.build_packet(value, policy())
        self.assertEqual(packet["top_themes"], ["THEME.COMPUTE"])
        rows = {row["theme_id"]: row for row in packet["theme_observations"]}
        self.assertEqual(rows["THEME.COMPUTE"]["current_rank"], 1)
        self.assertEqual(rows["THEME.NETWORK"]["current_rank"], 2)

    def test_output_is_deterministic_alphabetical_and_digest_bound(self):
        first = UCR.build_packet(input_packet(), policy())
        second = UCR.build_packet(copy.deepcopy(input_packet()), copy.deepcopy(policy()))
        self.assertEqual(first, second)
        self.assertEqual(
            [row["theme_id"] for row in second["theme_observations"]],
            ["THEME.COMPUTE", "THEME.NETWORK", "THEME.POWER"],
        )
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, UCR.payload_sha256(second))

    def test_self_rehashed_rank_and_delta_tamper_fail_closed(self):
        packet = UCR.build_packet(input_packet(), policy())
        packet["theme_observations"][0]["current_rank"] = 1
        packet["payload_sha256"] = UCR.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_RANK_BUCKET_MISMATCH"
        ):
            UCR.validate_packet(packet)

        packet = UCR.build_packet(input_packet(), policy("UNRATIFIED"))
        packet["theme_observations"][0]["relative_strength_change"] = "9"
        packet["payload_sha256"] = UCR.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_THEME_DERIVATION_MISMATCH"
        ):
            UCR.validate_packet(packet)

    def test_p2_state_regime_stage_production_and_trading_remain_closed(self):
        packet = UCR.build_packet(input_packet(), policy())
        self.assertTrue(packet["authority"]["theme_ranking_authorized"])
        self.assertTrue(packet["authority"]["top_bottom_bucket_authorized"])
        self.assertTrue(packet["authority"]["bucket_transition_authorized"])
        for field in (
            "p2_state_vocabulary_authorized", "state_ledger_authorized",
            "regime_input_authorized", "candidate_ranking_authorized",
            "stage_promotion_authorized", "production_authorized", "trading_authorized",
        ):
            self.assertFalse(packet["authority"][field], field)
        self.assertIn("P2_STATE_VOCABULARY_PENDING_P2_05", packet["unresolved_boundaries"])

    def test_contract_tamper_input_extra_and_default_policy_absence(self):
        contract = UCR.load_contract()
        contract["authority"]["trading_authorized"] = True
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "CONTRACT_FIELD_MISMATCH"):
            UCR.build_packet(input_packet(), policy(), contract=contract)
        value = input_packet()
        value["action"] = "BUY"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "INPUT_FIELDS_MISMATCH"):
            UCR.build_packet(value, policy())
        self.assertFalse((ROOT / "config" / "us_capital_rotation_policy.json").exists())
        source_text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", source_text)
        self.assertNotIn("urllib.request", source_text)

    def test_cli_is_temp_only_atomic_and_rejects_tracked_output(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            policy_path = temp / "policy.json"
            output_path = temp / "output.json"
            input_path.write_text(json.dumps(input_packet()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(MODULE_PATH), str(input_path),
                    "--policy", str(policy_path), "--out", str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text()), UCR.build_packet(input_packet(), policy()))
            output_path.write_text("sentinel\n", encoding="utf-8")
            input_path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(UCR.run(input_path, policy_path, output_path), 1)
            self.assertEqual(output_path.read_text(), "sentinel\n")
        tracked = ROOT / ".test-us-capital-rotation-output.json"
        self.assertFalse(tracked.exists())
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            policy_path = temp / "policy.json"
            input_path.write_text(json.dumps(input_packet()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            self.assertEqual(UCR.run(input_path, policy_path, tracked), 1)
        self.assertFalse(tracked.exists())


if __name__ == "__main__":
    unittest.main()
