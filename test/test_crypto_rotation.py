#!/usr/bin/env python3
"""P2-04 policy-gated BTC/ETH/ALT rotation regression."""

import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rotation" / "crypto_rotation.py"
CONTRACT_PATH = ROOT / "config" / "crypto_rotation_contract.json"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("crypto_rotation", SCRIPT)
CONTRACT = MODULE.load_contract()
SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64
SHA_E = "e" * 64


def write_json(path, payload):
    path = Path(path)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def rehash_output(packet):
    packet.pop("payload_sha256", None)
    packet["payload_sha256"] = MODULE.payload_sha256(packet)


def bucket_record(bucket_id, relative_strength, lookback):
    return {
        "group_id": bucket_id,
        "status": "OBSERVED_UNCLASSIFIED",
        "unknown_reason": None,
        "missing_dates": [],
        "observed_day_count": lookback,
        "required_day_count": lookback,
        "minimum_daily_member_count": 1,
        "required_minimum_member_count": None,
        "cumulative_gross_return": str(1 + relative_strength),
        "relative_strength_vs_btc": str(relative_strength),
        "classification": "UNDEFINED",
    }


def observed_window(window_id, as_of_date, available_at, strengths):
    lookback = 7 if window_id == "pilot_7d" else 30
    role = "PILOT" if window_id == "pilot_7d" else "PRIMARY"
    end = dt.date.fromisoformat(as_of_date)
    start = end - dt.timedelta(days=lookback - 1)
    daily_points = []
    for index in range(lookback):
        day = start + dt.timedelta(days=index)
        point_available_at = (
            available_at
            if index == lookback - 1
            else f"{(day + dt.timedelta(days=1)).isoformat()}T00:30:00Z"
        )
        daily_points.append(
            {
                "as_of_date": day.isoformat(),
                "lineage": {
                    "available_at": point_available_at,
                    "manifest_sha256": SHA_B,
                },
            }
        )
    manifest_lineage = [
        {
            "as_of_date": item["as_of_date"],
            "manifest_sha256": item["lineage"]["manifest_sha256"],
        }
        for item in daily_points
    ]
    return {
        "window_id": window_id,
        "role": role,
        "status": "OBSERVED_UNCLASSIFIED",
        "unknown_reason": None,
        "window": {
            "window_id": window_id,
            "role": role,
            "start_date": start.isoformat(),
            "end_date": as_of_date,
            "lookback_calendar_days": lookback,
            "required_point_count": lookback,
            "available_point_count": lookback,
            "missing_dates": [],
            "exact_contiguous_calendar_days": True,
        },
        "blockers": [],
        "source_unknown_points": [],
        "asset_relative_strength": [],
        "partial_window_assets": [],
        "group_relative_strength": {
            "bucket": [
                bucket_record(bucket_id, strengths[bucket_id], lookback)
                for bucket_id in CONTRACT["bucket_ids"]
            ],
            "sector_chain": {
                "status": "UNKNOWN",
                "unknown_reason": "GROUP_COVERAGE_POLICY_UNRATIFIED",
                "missing_asset_dates": [],
                "group_coverage_policy_status": "UNRATIFIED",
                "sector": [],
                "chain": [],
            },
        },
        "daily_points": daily_points,
        "lineage": {
            "pit_status": "independent_as_captured_daily_snapshots",
            "manifest_sha256_by_date": manifest_lineage,
            "current_catalog_backfill_authorized": False,
        },
    }


def leadership_packet(as_of_date, available_at, strengths):
    return {
        "schema_version": 2,
        "contract_version": "crypto_leadership_contract/v2",
        "market": "CRYPTO",
        "measurement": "raw_relative_strength_observation",
        "status": "OBSERVED_UNCLASSIFIED",
        "unknown_reason": None,
        "as_of_date": as_of_date,
        "windows": [
            observed_window("pilot_7d", as_of_date, available_at, strengths),
            observed_window("primary_30d", as_of_date, available_at, strengths),
        ],
        "policies": {
            "universe": {
                "policy_version": "crypto_universe/test-v1",
                "policy_sha256": SHA_C,
                "approval_status": "RATIFIED",
                "universe_kind": "point_in_time_daily_catalog",
            },
            "leadership": {
                "policy_version": "crypto_leadership/test-v2",
                "policy_sha256": SHA_D,
                "approval_status": "RATIFIED",
                "group_return_method": "equal_weight_daily_rebalanced",
                "group_coverage_policy_status": "UNRATIFIED",
            },
            "taxonomy": {
                "policy_version": "crypto_asset_taxonomy/test-v1",
                "policy_sha256": SHA_E,
                "approval_status": "UNRATIFIED",
                "effective_dated": True,
            },
        },
        "current_candle": {
            "excluded_for_every_member_and_point": True,
            "reason": "source_documents_not_yet_committed_timeframe",
        },
        "lineage": {
            "pit_status": "independent_as_captured_daily_snapshots",
            "manifest_sha256_by_date": copy.deepcopy(
                observed_window(
                    "primary_30d", as_of_date, available_at, strengths
                )["lineage"]["manifest_sha256_by_date"]
            ),
            "current_catalog_backfill_authorized": False,
        },
        "leader_classification_authorized": False,
        "ranking_authorized": False,
        "threshold_authorized": False,
        "regime_score_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }


def input_packet():
    return {
        "schema_version": "crypto_rotation_input/1",
        "as_of_date": "2026-08-20",
        "prior_observation": leadership_packet(
            "2026-08-18",
            "2026-08-19T00:30:00Z",
            {"ALT": 0.30, "BTC": 0, "ETH": 0.10},
        ),
        "current_observation": leadership_packet(
            "2026-08-20",
            "2026-08-21T00:30:00Z",
            {"ALT": 0.10, "BTC": 0, "ETH": 0.40},
        ),
    }


def policy(**overrides):
    value = {
        "schema_version": "crypto_rotation_policy/1",
        "policy_id": "CRYPTO_ROTATION.TEST.V1",
        "approval_status": "RATIFIED",
        "ratified_by": "test-cio",
        "ratified_at_utc": "2026-08-18T00:00:00Z",
        "effective_from": "2026-08-01",
        "effective_to": None,
        "window_id": "pilot_7d",
        "bucket_ids": ["ALT", "BTC", "ETH"],
        "universe_policy_sha256": SHA_C,
        "leadership_policy_sha256": SHA_D,
        "taxonomy_policy_sha256": SHA_E,
        "ranking_metric": "BUCKET_RELATIVE_STRENGTH_VS_BTC",
        "ranking_order": "DESCENDING",
        "tie_break": "BUCKET_ID_ASC",
        "top_count": 1,
        "bottom_count": 1,
        "maximum_calendar_gap_days": 3,
    }
    value.update(overrides)
    return value


def by_bucket(packet):
    return {item["bucket_id"]: item for item in packet["bucket_observations"]}


class CryptoRotationTest(unittest.TestCase):
    def test_contract_closes_all_adjacent_authority_and_has_no_default_policy(self):
        self.assertEqual(CONTRACT["repository_default_policy"], "ABSENT")
        self.assertEqual(CONTRACT["bucket_ids"], ["ALT", "BTC", "ETH"])
        self.assertEqual(CONTRACT["sector_chain_policy"], "UNKNOWN_NOT_RANKING_INPUT")
        self.assertTrue(CONTRACT["authority"]["external_ratified_rotation_policy_only"])
        self.assertTrue(
            all(
                value is False
                for key, value in CONTRACT["authority"].items()
                if key != "external_ratified_rotation_policy_only"
            )
        )

    def test_effective_policy_emits_deterministic_rank_bucket_and_transition(self):
        result = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        buckets = by_bucket(result)

        self.assertEqual(result["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(result["rotation_policy_effective"])
        self.assertEqual(result["top_groups"], ["ETH"])
        self.assertEqual(result["bottom_groups"], ["BTC"])
        self.assertEqual(buckets["ALT"]["bucket_transition"], "TOP_TO_MIDDLE")
        self.assertEqual(buckets["ETH"]["bucket_transition"], "MIDDLE_TO_TOP")
        self.assertEqual(buckets["BTC"]["bucket_transition"], "BOTTOM_TO_BOTTOM")
        self.assertEqual(buckets["ETH"]["rank_change"], 1)
        self.assertEqual(buckets["ALT"]["relative_strength_change"], "-0.2")
        self.assertTrue(result["authority"]["bucket_ranking_authorized"])
        self.assertFalse(result["authority"]["asset_ranking_authorized"])
        self.assertFalse(result["authority"]["production_authorized"])
        self.assertEqual(result["sector_chain_layer"]["status"], "UNKNOWN")
        self.assertFalse(result["sector_chain_layer"]["ranking_input_authorized"])

    def test_unratified_or_not_effective_policy_keeps_raw_changes_but_no_ranks(self):
        variants = [
            policy(
                approval_status="UNRATIFIED",
                ratified_by=None,
                ratified_at_utc=None,
            ),
            policy(effective_from="2026-08-19"),
            policy(effective_to="2026-08-20"),
        ]
        for value in variants:
            with self.subTest(policy=value):
                result = MODULE.build_packet(input_packet(), value, CONTRACT)
                self.assertEqual(result["status"], "POLICY_NOT_EFFECTIVE")
                self.assertFalse(result["rotation_policy_effective"])
                self.assertIsNone(result["ranking_method"])
                self.assertEqual(result["top_groups"], [])
                self.assertEqual(result["bottom_groups"], [])
                for item in result["bucket_observations"]:
                    self.assertIsNotNone(item["relative_strength_change"])
                    self.assertIsNone(item["current_rank"])
                    self.assertIsNone(item["bucket_transition"])
                self.assertFalse(result["authority"]["bucket_ranking_authorized"])

    def test_ratification_must_predate_prior_observation(self):
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError,
            "POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION",
        ):
            MODULE.build_packet(
                input_packet(),
                policy(ratified_at_utc="2026-08-19T00:30:01Z"),
                CONTRACT,
            )

    def test_policy_selects_one_independent_window(self):
        primary = MODULE.build_packet(
            input_packet(), policy(window_id="primary_30d"), CONTRACT
        )
        self.assertEqual(primary["window_id"], "primary_30d")
        self.assertEqual(primary["lookback_calendar_days"], 30)

        damaged = input_packet()
        damaged["current_observation"]["windows"][1]["status"] = "UNKNOWN"
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError,
            "UPSTREAM_SELECTED_WINDOW_NOT_OBSERVED:current",
        ):
            MODULE.build_packet(damaged, policy(window_id="primary_30d"), CONTRACT)

    def test_policy_binds_all_upstream_policy_hashes(self):
        for field in (
            "universe_policy_sha256",
            "leadership_policy_sha256",
            "taxonomy_policy_sha256",
        ):
            with self.subTest(field=field), self.assertRaisesRegex(
                MODULE.CryptoRotationError,
                "POLICY_UPSTREAM_HASH_MISMATCH",
            ):
                MODULE.build_packet(input_packet(), policy(**{field: SHA_A}), CONTRACT)

    def test_upstream_authority_and_current_candle_exclusion_are_required(self):
        expanded = input_packet()
        expanded["current_observation"]["ranking_authorized"] = True
        with self.assertRaisesRegex(MODULE.CryptoRotationError, "UPSTREAM_AUTHORITY_EXPANDED"):
            MODULE.build_packet(expanded, policy(), CONTRACT)

        candle = input_packet()
        candle["current_observation"]["current_candle"][
            "excluded_for_every_member_and_point"
        ] = False
        with self.assertRaisesRegex(MODULE.CryptoRotationError, "UPSTREAM_CURRENT_CANDLE_INVALID"):
            MODULE.build_packet(candle, policy(), CONTRACT)

    def test_bucket_set_and_btc_reference_are_exact(self):
        bad_reference = input_packet()
        bad_reference["current_observation"]["windows"][0][
            "group_relative_strength"
        ]["bucket"][1]["relative_strength_vs_btc"] = "0.01"
        with self.assertRaisesRegex(MODULE.CryptoRotationError, "UPSTREAM_BTC_REFERENCE_INVALID"):
            MODULE.build_packet(bad_reference, policy(), CONTRACT)

        wrong_order = input_packet()
        buckets = wrong_order["current_observation"]["windows"][0][
            "group_relative_strength"
        ]["bucket"]
        buckets[0], buckets[1] = buckets[1], buckets[0]
        with self.assertRaisesRegex(MODULE.CryptoRotationError, "UPSTREAM_BUCKET_SET_INVALID"):
            MODULE.build_packet(wrong_order, policy(), CONTRACT)

    def test_sector_chain_layer_cannot_become_observed_or_feed_ranking(self):
        value = input_packet()
        sector = value["current_observation"]["windows"][0][
            "group_relative_strength"
        ]["sector_chain"]
        sector["status"] = "OBSERVED_UNCLASSIFIED"
        sector["sector"] = [{"group_id": "L1"}]
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError,
            "UPSTREAM_SECTOR_CHAIN_AUTHORITY_INVALID",
        ):
            MODULE.build_packet(value, policy(), CONTRACT)

    def test_observation_order_gap_and_pit_available_at_are_enforced(self):
        gap = input_packet()
        gap["prior_observation"] = leadership_packet(
            "2026-08-01",
            "2026-08-02T00:30:00Z",
            {"ALT": 0.30, "BTC": 0, "ETH": 0.10},
        )
        with self.assertRaisesRegex(MODULE.CryptoRotationError, "OBSERVATION_GAP_EXCEEDS_POLICY"):
            MODULE.build_packet(
                gap,
                policy(
                    ratified_at_utc="2026-07-31T00:00:00Z",
                    effective_from="2026-07-01",
                ),
                CONTRACT,
            )

        reversed_time = input_packet()
        for window in reversed_time["current_observation"]["windows"]:
            window["daily_points"][-1]["lineage"]["available_at"] = "2026-08-18T00:00:00Z"
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError,
            "UPSTREAM_DAILY_AVAILABILITY_ORDER_INVALID",
        ):
            MODULE.build_packet(reversed_time, policy(), CONTRACT)

    def test_equal_scores_use_bucket_id_ascending_tie_break(self):
        value = input_packet()
        for window in value["current_observation"]["windows"]:
            records = window["group_relative_strength"]["bucket"]
            records[0]["relative_strength_vs_btc"] = "0.4"
            records[0]["cumulative_gross_return"] = "1.4"
            records[2]["relative_strength_vs_btc"] = "0.4"
        result = MODULE.build_packet(value, policy(), CONTRACT)
        self.assertEqual(result["top_groups"], ["ALT"])
        self.assertEqual(by_bucket(result)["ALT"]["current_rank"], 1)
        self.assertEqual(by_bucket(result)["ETH"]["current_rank"], 2)

    def test_output_is_byte_deterministic_and_digest_covers_payload(self):
        first = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        second = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        digest = first.pop("payload_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_output_validator_rejects_self_rehashed_rank_tamper(self):
        packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        packet["bucket_observations"][0]["current_rank"] = 3
        rehash_output(packet)
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError, "OUTPUT_RANK_BUCKET_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_output_validator_rejects_self_rehashed_delta_tamper(self):
        packet = MODULE.build_packet(
            input_packet(),
            policy(approval_status="UNRATIFIED", ratified_by=None, ratified_at_utc=None),
            CONTRACT,
        )
        packet["bucket_observations"][0]["relative_strength_change"] = "0"
        rehash_output(packet)
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError, "OUTPUT_BUCKET_DERIVATION_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_output_validator_keeps_sector_chain_unknown_after_self_rehash(self):
        packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        packet["sector_chain_layer"].update({
            "status": "OBSERVED_UNCLASSIFIED",
            "ranking_input_authorized": True,
        })
        rehash_output(packet)
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError, "OUTPUT_SECTOR_CHAIN_BOUNDARY_MISMATCH"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_observation_pair_persists_available_at_for_standalone_reproof(self):
        packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        pair = packet["observation_pair"]
        self.assertEqual(pair["prior_date"], "2026-08-18")
        self.assertEqual(pair["current_date"], "2026-08-20")
        self.assertEqual(pair["calendar_gap_days"], 2)
        self.assertEqual(pair["prior_available_at"], "2026-08-19T00:30:00+00:00")
        self.assertEqual(pair["current_available_at"], "2026-08-21T00:30:00+00:00")
        # validate_packet() takes only the persisted packet -- neither upstream
        # Leadership packet is passed in, so a pass here is itself the proof
        # that temporal order, gap, and ratified-before-prior are
        # standalone-provable.
        MODULE.validate_packet(copy.deepcopy(packet), CONTRACT)

    def test_revision_a_and_b_are_each_independently_standalone_verifiable(self):
        revision_a = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        # Revision B: source pointer moves -- later available_at for both
        # observations, as if a fresher upstream snapshot had been read.
        moved = {
            "schema_version": "crypto_rotation_input/1",
            "as_of_date": "2026-08-20",
            "prior_observation": leadership_packet(
                "2026-08-18",
                "2026-08-19T06:00:00Z",
                {"ALT": 0.30, "BTC": 0, "ETH": 0.10},
            ),
            "current_observation": leadership_packet(
                "2026-08-20",
                "2026-08-21T06:00:00Z",
                {"ALT": 0.10, "BTC": 0, "ETH": 0.40},
            ),
        }
        revision_b = MODULE.build_packet(moved, policy(), CONTRACT)
        self.assertNotEqual(
            revision_a["observation_pair"]["prior_available_at"],
            revision_b["observation_pair"]["prior_available_at"],
        )
        # Each revision is re-verified from nothing but its own persisted
        # packet -- no fault injection, no monkeypatch, no shared live state.
        MODULE.validate_packet(copy.deepcopy(revision_a), CONTRACT)
        MODULE.validate_packet(copy.deepcopy(revision_b), CONTRACT)
        self.assertEqual(
            revision_b["observation_pair"]["prior_available_at"],
            "2026-08-19T06:00:00+00:00",
        )

    def test_validate_packet_rejects_missing_invalid_naive_and_malformed_timezone_available_at(self):
        for field in ("prior_available_at", "current_available_at"):
            with self.subTest(field=field, case="missing"):
                packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
                del packet["observation_pair"][field]
                with self.assertRaisesRegex(
                    MODULE.CryptoRotationError, "OUTPUT_OBSERVATION_PAIR_INVALID"
                ):
                    MODULE.validate_packet(packet, CONTRACT)
            code = (
                "OUTPUT_PRIOR_AVAILABLE_AT_INVALID"
                if field == "prior_available_at"
                else "OUTPUT_CURRENT_AVAILABLE_AT_INVALID"
            )
            for case, bad_value in (
                ("not_a_string", 12345),
                ("unparseable", "not-a-timestamp"),
                ("naive_no_offset", "2026-08-19T00:30:00"),
                ("malformed_timezone", "2026-08-19T00:30:00PST"),
            ):
                with self.subTest(field=field, case=case):
                    packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
                    packet["observation_pair"][field] = bad_value
                    with self.assertRaisesRegex(MODULE.CryptoRotationError, code):
                        MODULE.validate_packet(packet, CONTRACT)

    def test_validate_packet_rejects_available_at_order_gap_and_ratification_tamper_after_self_rehash(self):
        # Tamper 1: swap persisted available_at order, then re-hash the
        # packet so the top-level payload_sha256 digest matches the
        # tampered content -- the temporal-order re-derivation, not the
        # digest check, must be what catches this.
        packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        pair = packet["observation_pair"]
        pair["prior_available_at"], pair["current_available_at"] = (
            pair["current_available_at"], pair["prior_available_at"],
        )
        rehash_output(packet)
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError, "OUTPUT_AVAILABLE_AT_ORDER_INVALID"
        ):
            MODULE.validate_packet(packet, CONTRACT)

        # Tamper 2: push the persisted prior_available_at earlier than the
        # policy's own ratified_at_utc, self-rehash, and confirm the
        # ratified-before-prior re-proof still fires even though
        # build_packet() would never have produced this shape.
        packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        packet["observation_pair"]["prior_available_at"] = "2026-08-17T00:00:00+00:00"
        rehash_output(packet)
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError, "OUTPUT_POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"
        ):
            MODULE.validate_packet(packet, CONTRACT)

        # Tamper 3: widen the persisted gap beyond the policy's own
        # maximum_calendar_gap_days by moving prior_date and prior_
        # available_at earlier together, self-rehash, and confirm the
        # gap is independently re-derived from the persisted pair rather
        # than trusting calendar_gap_days at face value.
        packet = MODULE.build_packet(input_packet(), policy(), CONTRACT)
        pair = packet["observation_pair"]
        pair["prior_date"] = "2026-08-15"
        pair["calendar_gap_days"] = 5
        pair["prior_available_at"] = "2026-08-18T01:00:00+00:00"
        rehash_output(packet)
        with self.assertRaisesRegex(
            MODULE.CryptoRotationError, "OUTPUT_OBSERVATION_GAP_EXCEEDS_POLICY"
        ):
            MODULE.validate_packet(packet, CONTRACT)

    def test_contract_tamper_and_policy_method_drift_fail_closed(self):
        contract = copy.deepcopy(CONTRACT)
        contract["authority"]["production_authorized"] = True
        with self.assertRaisesRegex(MODULE.CryptoRotationError, "CONTRACT_FIELD_MISMATCH"):
            MODULE.build_packet(input_packet(), policy(), contract)

        with self.assertRaisesRegex(MODULE.CryptoRotationError, "POLICY_RANKING_METHOD_INVALID"):
            MODULE.build_packet(
                input_packet(), policy(ranking_order="ASCENDING"), CONTRACT
            )

    def test_cli_writes_atomically_only_outside_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            input_path = write_json(tmp / "input.json", input_packet())
            policy_path = write_json(tmp / "policy.json", policy())
            output_path = tmp / "nested" / "rotation.json"
            self.assertEqual(MODULE.run(input_path, policy_path, output_path), 0)
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "ROTATION_BUCKETS_OBSERVED")
            self.assertEqual(list(output_path.parent.glob(".rotation.json.*")), [])
            forbidden = ROOT / "data" / "crypto_rotation_test_output.json"
            self.assertEqual(
                MODULE.run(input_path, policy_path, forbidden),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
