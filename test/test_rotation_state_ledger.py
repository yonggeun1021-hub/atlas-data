#!/usr/bin/env python3
"""P2-05 common rotation state ledger regression."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "rotation" / "rotation_state_ledger.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("rotation_state_ledger", SCRIPT)
CONTRACT = MODULE.load_contract()


def refresh_packet(value):
    result = copy.deepcopy(value)
    result.pop("payload_sha256", None)
    result["payload_sha256"] = MODULE.payload_sha256(result)
    return result


def write_json(path, value):
    path = Path(path)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return path


def authority():
    return {
        "p2_state_vocabulary_authorized": False,
        "state_ledger_authorized": False,
        "bucket_transition_authorized": True,
        "production_authorized": False,
        "trading_authorized": False,
    }


def us_observation(
    theme_id,
    prior_bucket,
    current_bucket,
    prior_rank,
    current_rank,
):
    return {
        "theme_id": theme_id,
        "prior_relative_strength_vs_benchmark": "0.1",
        "current_relative_strength_vs_benchmark": "0.2",
        "relative_strength_change": "0.1",
        "prior_rank": prior_rank,
        "current_rank": current_rank,
        "rank_change": prior_rank - current_rank,
        "prior_bucket": prior_bucket,
        "current_bucket": current_bucket,
        "bucket_transition": f"{prior_bucket}_TO_{current_bucket}",
        "p2_state": "UNDEFINED_PENDING_P2_05",
    }


def korea_observation(
    series_identity,
    theme_id,
    prior_bucket,
    current_bucket,
    prior_rank,
    current_rank,
):
    return {
        "series_identity": series_identity,
        "theme_id": theme_id,
        "role": "THEME_PROXY",
        "prior_relative_strength_vs_benchmark": "0.1",
        "current_relative_strength_vs_benchmark": "0.2",
        "relative_strength_change": "0.1",
        "prior_rank_within_benchmark": prior_rank,
        "current_rank_within_benchmark": current_rank,
        "rank_change_within_benchmark": prior_rank - current_rank,
        "prior_bucket": prior_bucket,
        "current_bucket": current_bucket,
        "bucket_transition": f"{prior_bucket}_TO_{current_bucket}",
        "p2_state": "UNDEFINED_PENDING_P2_05",
    }


def crypto_observation(
    bucket_id,
    prior_bucket,
    current_bucket,
    prior_rank,
    current_rank,
):
    return {
        "bucket_id": bucket_id,
        "prior_relative_strength_vs_btc": "0.1",
        "current_relative_strength_vs_btc": "0.2",
        "relative_strength_change": "0.1",
        "prior_rank": prior_rank,
        "current_rank": current_rank,
        "rank_change": prior_rank - current_rank,
        "prior_bucket": prior_bucket,
        "current_bucket": current_bucket,
        "bucket_transition": f"{prior_bucket}_TO_{current_bucket}",
        "p2_state": "UNDEFINED_PENDING_P2_05",
    }


def us_packet(as_of_date="2026-08-20", observations=None):
    rotation_policy = {"policy_id": "US.ROTATION.TEST.V1"}
    rotation_policy_sha = MODULE.payload_sha256(rotation_policy)
    value = {
        "schema_version": "us_capital_rotation_packet/1",
        "contract_version": "us_capital_rotation/1",
        "measurement": "us_theme_relative_rotation_observation",
        "market": "US",
        "as_of_date": as_of_date,
        "status": "ROTATION_BUCKETS_OBSERVED",
        "benchmark_asset": "SPY",
        "observation_pair": {},
        "taxonomy_binding": {},
        "rotation_policy": rotation_policy,
        "rotation_policy_effective": True,
        "ranking_method": {},
        "top_themes": [],
        "bottom_themes": [],
        "theme_observations": observations or [
            us_observation("THEME.AI", "BOTTOM", "MIDDLE", 2, 2),
            us_observation("THEME.ENERGY", "MIDDLE", "TOP", 1, 1),
        ],
        "retention": {},
        "lineage": {"rotation_policy_sha256": rotation_policy_sha},
        "authority": authority(),
        "unresolved_boundaries": [],
    }
    return refresh_packet(value)


def korea_packet(as_of_date="2026-08-20"):
    rotation_policy = {"policy_id": "KOREA.ROTATION.TEST.V1"}
    rotation_policy_sha = MODULE.payload_sha256(rotation_policy)
    value = {
        "schema_version": "korea_capital_rotation_packet/1",
        "contract_version": "korea_capital_rotation/1",
        "measurement": "korea_theme_relative_rotation_observation",
        "market": "KOREA",
        "as_of_date": as_of_date,
        "status": "ROTATION_BUCKETS_OBSERVED",
        "observation_pair": {},
        "taxonomy_binding": {},
        "coverage_context": {},
        "rotation_policy": rotation_policy,
        "rotation_policy_effective": True,
        "ranking_method": {},
        "benchmark_scopes": [
            {
                "benchmark_identity": "KRX:KOSDAQ",
                "top_themes": [],
                "bottom_themes": [],
                "theme_observations": [
                    korea_observation(
                        "KRX:KOSDAQ:SEMICON",
                        "THEME.SEMICON",
                        "BOTTOM",
                        "MIDDLE",
                        1,
                        1,
                    )
                ],
            },
            {
                "benchmark_identity": "KRX:KOSPI",
                "top_themes": [],
                "bottom_themes": [],
                "theme_observations": [
                    korea_observation(
                        "KRX:KOSPI:POWER",
                        "THEME.POWER",
                        "MIDDLE",
                        "TOP",
                        1,
                        1,
                    )
                ],
            },
        ],
        "retention": {},
        "lineage": {"rotation_policy_sha256": rotation_policy_sha},
        "authority": authority(),
        "unresolved_boundaries": [],
    }
    return refresh_packet(value)


def crypto_packet(as_of_date="2026-08-20"):
    rotation_policy = {"policy_id": "CRYPTO.ROTATION.TEST.V1"}
    rotation_policy_sha = MODULE.payload_sha256(rotation_policy)
    value = {
        "schema_version": "crypto_rotation_packet/1",
        "contract_version": "crypto_rotation/1",
        "measurement": "crypto_bucket_relative_rotation_observation",
        "market": "CRYPTO",
        "as_of_date": as_of_date,
        "status": "ROTATION_BUCKETS_OBSERVED",
        "window_id": "pilot_7d",
        "lookback_calendar_days": 7,
        "rotation_policy": rotation_policy,
        "rotation_policy_effective": True,
        "ranking_method": {},
        "top_groups": [],
        "bottom_groups": [],
        "bucket_observations": [
            crypto_observation("ALT", "BOTTOM", "MIDDLE", 3, 2),
            crypto_observation("BTC", "MIDDLE", "BOTTOM", 2, 3),
            crypto_observation("ETH", "MIDDLE", "TOP", 1, 1),
        ],
        "sector_chain_layer": {
            "status": "UNKNOWN",
            "ranking_input_authorized": False,
        },
        "lineage": {"rotation_policy_sha256": rotation_policy_sha},
        "authority": authority(),
        "unresolved_boundaries": [],
    }
    return refresh_packet(value)


def state_mapping():
    return {
        "BOTTOM_TO_BOTTOM": "WEAKENING",
        "BOTTOM_TO_MIDDLE": "EMERGING",
        "BOTTOM_TO_TOP": "STRONG",
        "MIDDLE_TO_BOTTOM": "WEAKENING",
        "MIDDLE_TO_MIDDLE": "EMERGING",
        "MIDDLE_TO_TOP": "STRONG",
        "TOP_TO_BOTTOM": "WEAKENING",
        "TOP_TO_MIDDLE": "WEAKENING",
        "TOP_TO_TOP": "STRONG",
    }


def policy_for(packet, **overrides):
    value = {
        "schema_version": "rotation_state_policy/1",
        "policy_id": f"{packet['market']}.STATE.TEST.V1",
        "approval_status": "RATIFIED",
        "ratified_by": "test-cio",
        "ratified_at_utc": "2026-08-18T00:00:00Z",
        "effective_from": "2026-08-19",
        "effective_to": None,
        "market": packet["market"],
        "input_rotation_contract_version": packet["contract_version"],
        "input_rotation_policy_sha256": packet["lineage"][
            "rotation_policy_sha256"
        ],
        "state_vocabulary": ["EMERGING", "STRONG", "WEAKENING"],
        "state_by_bucket_transition": state_mapping(),
        "maximum_ledger_gap_days": 3,
    }
    value.update(overrides)
    return value


def records_by_entity(ledger, market=None):
    result = {}
    for item in ledger["records"]:
        if market is None or item["market"] == market:
            result.setdefault(item["entity_id"], []).append(item)
    return result


class RotationStateLedgerTest(unittest.TestCase):
    def test_contract_has_no_default_mapping_and_closes_adjacent_authority(self):
        self.assertEqual(CONTRACT["repository_default_policy"], "ABSENT")
        self.assertEqual(
            CONTRACT["state_vocabulary"],
            ["EMERGING", "STRONG", "WEAKENING"],
        )
        self.assertEqual(len(CONTRACT["structural_bucket_transitions"]), 9)
        self.assertTrue(
            CONTRACT["authority"]["external_ratified_state_policy_only"]
        )
        self.assertFalse(CONTRACT["authority"]["production_authorized"])
        self.assertFalse(CONTRACT["authority"]["trading_authorized"])

    def test_us_initial_state_records_are_policy_mapped_and_hash_lineaged(self):
        packet = us_packet()
        state_policy = policy_for(packet)
        result = MODULE.apply_rotation(packet, state_policy, contract=CONTRACT)
        records = records_by_entity(result, "US")

        self.assertEqual(result["status"], "STATE_HISTORY_OBSERVED")
        self.assertEqual(result["ledger_revision"], 1)
        self.assertEqual(records["THEME.AI"][0]["current_p2_state"], "EMERGING")
        self.assertEqual(
            records["THEME.AI"][0]["state_transition"],
            "UNINITIALIZED_TO_EMERGING",
        )
        self.assertEqual(records["THEME.ENERGY"][0]["current_p2_state"], "STRONG")
        self.assertEqual(
            result["source_packets"][0]["input_packet_sha256"],
            packet["payload_sha256"],
        )
        self.assertEqual(
            result["source_packets"][0]["state_policy_sha256"],
            MODULE.payload_sha256(state_policy),
        )
        self.assertTrue(result["authority"]["p2_state_vocabulary_authorized"])
        self.assertTrue(result["authority"]["state_ledger_authorized"])
        self.assertFalse(result["authority"]["regime_input_authorized"])

    def test_second_observation_appends_state_transition_and_record_chain(self):
        first_packet = us_packet()
        ledger = MODULE.apply_rotation(
            first_packet, policy_for(first_packet), contract=CONTRACT
        )
        second_packet = us_packet(
            "2026-08-21",
            [
                us_observation("THEME.AI", "MIDDLE", "TOP", 2, 1),
                us_observation("THEME.ENERGY", "TOP", "MIDDLE", 1, 2),
            ],
        )
        result = MODULE.apply_rotation(
            second_packet,
            policy_for(second_packet),
            ledger,
            CONTRACT,
        )
        records = records_by_entity(result, "US")

        self.assertEqual(result["ledger_revision"], 2)
        self.assertEqual(len(result["records"]), 4)
        self.assertEqual(
            records["THEME.AI"][1]["state_transition"],
            "EMERGING_TO_STRONG",
        )
        self.assertEqual(
            records["THEME.ENERGY"][1]["state_transition"],
            "STRONG_TO_WEAKENING",
        )
        self.assertEqual(
            records["THEME.AI"][1]["prior_record_sha256"],
            records["THEME.AI"][0]["record_sha256"],
        )

    def test_korea_benchmark_scopes_remain_independent(self):
        packet = korea_packet()
        result = MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)
        self.assertEqual(
            result["source_packets"][0]["scope_ids"],
            ["KRX:KOSDAQ", "KRX:KOSPI"],
        )
        rows = {(item["scope_id"], item["entity_id"]): item for item in result["records"]}
        self.assertEqual(rows[("KRX:KOSDAQ", "THEME.SEMICON")]["current_p2_state"], "EMERGING")
        self.assertEqual(rows[("KRX:KOSPI", "THEME.POWER")]["current_p2_state"], "STRONG")

    def test_crypto_btc_eth_alt_buckets_use_the_same_ledger_without_sector_inference(self):
        packet = crypto_packet()
        result = MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)
        rows = records_by_entity(result, "CRYPTO")
        self.assertEqual(sorted(rows), ["ALT", "BTC", "ETH"])
        self.assertEqual(rows["ALT"][0]["scope_id"], "BTC_RELATIVE_BUCKETS")
        self.assertEqual(rows["ALT"][0]["current_p2_state"], "EMERGING")
        self.assertEqual(rows["BTC"][0]["current_p2_state"], "WEAKENING")
        self.assertEqual(rows["ETH"][0]["current_p2_state"], "STRONG")
        self.assertNotIn("sector", MODULE.canonical_json(result).lower())

    def test_three_markets_share_storage_without_cross_market_state_chaining(self):
        us = us_packet()
        ledger = MODULE.apply_rotation(us, policy_for(us), contract=CONTRACT)
        korea = korea_packet()
        ledger = MODULE.apply_rotation(korea, policy_for(korea), ledger, CONTRACT)
        crypto = crypto_packet()
        ledger = MODULE.apply_rotation(crypto, policy_for(crypto), ledger, CONTRACT)

        self.assertEqual(ledger["ledger_revision"], 3)
        self.assertEqual(
            [item["market"] for item in ledger["source_packets"]],
            ["US", "KOREA", "CRYPTO"],
        )
        self.assertTrue(
            all(
                item["prior_p2_state"] is None
                and item["state_transition"].startswith("UNINITIALIZED_TO_")
                for item in ledger["records"]
            )
        )
        self.assertEqual(
            ledger["source_packets"][0]["state_policy"], policy_for(us)
        )

    def test_actual_p202_to_p204_packet_builders_are_contract_compatible(self):
        us_fixture = load_module(
            "us_rotation_state_integration_fixture",
            ROOT / "test" / "test_us_capital_rotation.py",
        )
        korea_fixture = load_module(
            "korea_rotation_state_integration_fixture",
            ROOT / "test" / "test_korea_capital_rotation.py",
        )
        crypto_fixture = load_module(
            "crypto_rotation_state_integration_fixture",
            ROOT / "test" / "test_crypto_rotation.py",
        )
        us_actual = us_fixture.UCR.build_packet(
            us_fixture.input_packet(), us_fixture.policy()
        )
        korea_input, korea_policy = korea_fixture.make_bundle()
        korea_actual = korea_fixture.KCR.build_packet(korea_input, korea_policy)
        crypto_actual = crypto_fixture.MODULE.build_packet(
            crypto_fixture.input_packet(),
            crypto_fixture.policy(),
            crypto_fixture.CONTRACT,
        )

        expected_counts = {"US": 3, "KOREA": 6, "CRYPTO": 3}
        for packet in (us_actual, korea_actual, crypto_actual):
            with self.subTest(market=packet["market"]):
                ledger = MODULE.apply_rotation(
                    packet, policy_for(packet), contract=CONTRACT
                )
                self.assertEqual(
                    len(ledger["records"]), expected_counts[packet["market"]]
                )

    def test_unratified_late_inactive_or_wrong_binding_policy_fails_closed(self):
        packet = us_packet()
        variants = [
            (policy_for(packet, approval_status="UNRATIFIED"), "STATE_POLICY_NOT_RATIFIED"),
            (policy_for(packet, ratified_at_utc="2026-08-20T00:00:00Z"), "STATE_POLICY_RATIFIED_TOO_LATE"),
            (policy_for(packet, effective_from="2026-08-21"), "STATE_POLICY_NOT_EFFECTIVE"),
            (policy_for(packet, market="CRYPTO"), "STATE_POLICY_INPUT_BINDING_MISMATCH"),
            (policy_for(packet, input_rotation_policy_sha256="a" * 64), "STATE_POLICY_INPUT_BINDING_MISMATCH"),
        ]
        for state_policy, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationStateLedgerError, error
            ):
                MODULE.apply_rotation(packet, state_policy, contract=CONTRACT)

    def test_policy_must_map_all_nine_transitions_and_exact_vocabulary(self):
        packet = us_packet()
        missing = state_mapping()
        missing.pop("TOP_TO_TOP")
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "STATE_POLICY_MAPPING_INVALID"):
            MODULE.apply_rotation(
                packet,
                policy_for(packet, state_by_bucket_transition=missing),
                contract=CONTRACT,
            )
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "STATE_POLICY_VOCABULARY_MISMATCH"):
            MODULE.apply_rotation(
                packet,
                policy_for(packet, state_vocabulary=["STRONG"]),
                contract=CONTRACT,
            )

    def test_rotation_packet_digest_identity_authority_and_pending_state_are_required(self):
        packet = us_packet()
        tampered = copy.deepcopy(packet)
        tampered["theme_observations"][0]["current_bucket"] = "TOP"
        variants = [
            (tampered, "ROTATION_PACKET_SHA_MISMATCH"),
            (refresh_packet(dict(packet, status="POLICY_NOT_EFFECTIVE")), "ROTATION_PACKET_IDENTITY_INVALID"),
        ]
        expanded = copy.deepcopy(packet)
        expanded["authority"]["p2_state_vocabulary_authorized"] = True
        variants.append((refresh_packet(expanded), "ROTATION_PACKET_AUTHORITY_INVALID"))
        classified = copy.deepcopy(packet)
        classified["theme_observations"][0]["p2_state"] = "STRONG"
        variants.append((refresh_packet(classified), "ROTATION_OBSERVATION_INVALID"))
        for value, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationStateLedgerError, error
            ):
                MODULE.apply_rotation(value, policy_for(packet), contract=CONTRACT)

    def test_structural_transition_must_match_prior_and_current_bucket(self):
        packet = us_packet()
        packet["theme_observations"][0]["bucket_transition"] = "BOTTOM_TO_TOP"
        packet = refresh_packet(packet)
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "ROTATION_OBSERVATION_INVALID"):
            MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)

    def test_exact_packet_reapply_is_byte_idempotent_but_policy_conflict_fails(self):
        packet = us_packet()
        state_policy = policy_for(packet)
        first = MODULE.apply_rotation(packet, state_policy, contract=CONTRACT)
        second = MODULE.apply_rotation(packet, state_policy, first, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))

        changed_mapping = state_mapping()
        changed_mapping["BOTTOM_TO_BOTTOM"] = "EMERGING"
        changed_mapping["MIDDLE_TO_MIDDLE"] = "WEAKENING"
        conflict = policy_for(packet, state_by_bucket_transition=changed_mapping)
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "SOURCE_PACKET_POLICY_CONFLICT"):
            MODULE.apply_rotation(packet, conflict, first, CONTRACT)

    def test_non_forward_and_excessive_gap_observations_fail(self):
        first_packet = us_packet()
        ledger = MODULE.apply_rotation(
            first_packet, policy_for(first_packet), contract=CONTRACT
        )
        stale = us_packet(
            "2026-08-20",
            [us_observation("THEME.AI", "MIDDLE", "TOP", 2, 1)],
        )
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "LEDGER_NON_FORWARD_OBSERVATION"):
            MODULE.apply_rotation(stale, policy_for(stale), ledger, CONTRACT)

        gap = us_packet("2026-08-25")
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "LEDGER_GAP_EXCEEDS_POLICY"):
            MODULE.apply_rotation(gap, policy_for(gap), ledger, CONTRACT)

    def test_missing_entity_does_not_create_synthetic_tombstone(self):
        first_packet = us_packet()
        ledger = MODULE.apply_rotation(
            first_packet, policy_for(first_packet), contract=CONTRACT
        )
        second = us_packet(
            "2026-08-21",
            [us_observation("THEME.AI", "MIDDLE", "TOP", 2, 1)],
        )
        result = MODULE.apply_rotation(second, policy_for(second), ledger, CONTRACT)
        records = records_by_entity(result, "US")
        self.assertEqual(len(records["THEME.AI"]), 2)
        self.assertEqual(len(records["THEME.ENERGY"]), 1)
        self.assertNotIn("tombstone", MODULE.canonical_json(result).lower())

    def test_source_identity_drift_fails_instead_of_silent_chain_join(self):
        first = korea_packet()
        ledger = MODULE.apply_rotation(first, policy_for(first), contract=CONTRACT)
        second = korea_packet("2026-08-21")
        second["benchmark_scopes"][0]["theme_observations"][0][
            "series_identity"
        ] = "KRX:KOSDAQ:DIFFERENT_PROXY"
        second = refresh_packet(second)
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "LEDGER_SOURCE_IDENTITY_DRIFT"):
            MODULE.apply_rotation(second, policy_for(second), ledger, CONTRACT)

    def test_tampered_ledger_digest_record_chain_or_record_coverage_fails(self):
        packet = us_packet()
        ledger = MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)
        bad_digest = copy.deepcopy(ledger)
        bad_digest["records"][0]["current_p2_state"] = "STRONG"
        with self.assertRaisesRegex(MODULE.RotationStateLedgerError, "LEDGER_SHA_MISMATCH"):
            MODULE.apply_rotation(us_packet("2026-08-21"), policy_for(us_packet("2026-08-21")), bad_digest, CONTRACT)

        missing = copy.deepcopy(ledger)
        missing["records"].pop()
        missing.pop("payload_sha256")
        missing["payload_sha256"] = MODULE.payload_sha256(missing)
        with self.assertRaisesRegex(
            MODULE.RotationStateLedgerError,
            "LEDGER_SOURCE_RECORD_COVERAGE_MISMATCH",
        ):
            MODULE.apply_rotation(us_packet("2026-08-21"), policy_for(us_packet("2026-08-21")), missing, CONTRACT)

    def test_transform_is_deterministic_and_does_not_mutate_inputs(self):
        packet = us_packet()
        state_policy = policy_for(packet)
        packet_before = MODULE.canonical_json(packet)
        policy_before = MODULE.canonical_json(state_policy)
        first = MODULE.apply_rotation(packet, state_policy, contract=CONTRACT)
        second = MODULE.apply_rotation(packet, state_policy, contract=CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(packet), packet_before)
        self.assertEqual(MODULE.canonical_json(state_policy), policy_before)
        digest = first.pop("payload_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_cli_writes_atomically_only_outside_repository(self):
        packet = us_packet()
        state_policy = policy_for(packet)
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            packet_path = write_json(tmp / "rotation.json", packet)
            policy_path = write_json(tmp / "policy.json", state_policy)
            output_path = tmp / "nested" / "ledger.json"
            self.assertEqual(
                MODULE.run(packet_path, policy_path, output_path),
                0,
            )
            result = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(result["ledger_revision"], 1)
            self.assertEqual(list(output_path.parent.glob(".ledger.json.*")), [])

            forbidden = ROOT / "data" / "rotation_state_ledger_test.json"
            self.assertEqual(
                MODULE.run(packet_path, policy_path, forbidden),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
