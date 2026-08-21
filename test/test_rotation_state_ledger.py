#!/usr/bin/env python3
"""P2-05 common rotation state ledger regression."""

import copy
import datetime as dt
from decimal import Decimal
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
US_FIXTURE = load_module(
    "us_rotation_state_fixture", ROOT / "test" / "test_us_capital_rotation.py"
)
KOREA_FIXTURE = load_module(
    "korea_rotation_state_fixture", ROOT / "test" / "test_korea_capital_rotation.py"
)
CRYPTO_FIXTURE = load_module(
    "crypto_rotation_state_fixture", ROOT / "test" / "test_crypto_rotation.py"
)


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


def _move_packet_date(packet, as_of_date, producer):
    result = copy.deepcopy(packet)
    if result["as_of_date"] == as_of_date:
        return result
    current = dt.date.fromisoformat(as_of_date)
    result["as_of_date"] = as_of_date
    pair = result.get("observation_pair")
    if pair is not None:
        gap = pair["calendar_gap_days"]
        pair["current_date"] = as_of_date
        pair["prior_date"] = (current - dt.timedelta(days=gap)).isoformat()
    result = refresh_packet(result)
    producer.validate_packet(result)
    return result


def _rederive_us_packet(packet, prior_values, current_values, keep_theme_ids=None):
    result = copy.deepcopy(packet)
    producer = US_FIXTURE.UCR
    policy = result["rotation_policy"]
    if keep_theme_ids is not None:
        policy["theme_ids"] = sorted(keep_theme_ids)
        result["theme_observations"] = [
            row for row in result["theme_observations"]
            if row["theme_id"] in keep_theme_ids
        ]
    rows = result["theme_observations"]
    for row in rows:
        theme_id = row["theme_id"]
        prior = Decimal(prior_values[theme_id])
        current = Decimal(current_values[theme_id])
        row["prior_relative_strength_vs_benchmark"] = producer._render(prior, 12)
        row["current_relative_strength_vs_benchmark"] = producer._render(current, 12)
        row["relative_strength_change"] = producer._render(current - prior, 12)
    prior_ranked = sorted(
        policy["theme_ids"], key=lambda item: (-Decimal(prior_values[item]), item)
    )
    current_ranked = sorted(
        policy["theme_ids"], key=lambda item: (-Decimal(current_values[item]), item)
    )
    prior_ranks = {item: index + 1 for index, item in enumerate(prior_ranked)}
    current_ranks = {item: index + 1 for index, item in enumerate(current_ranked)}
    prior_buckets = producer._buckets(
        prior_ranked, policy["top_count"], policy["bottom_count"]
    )
    current_buckets = producer._buckets(
        current_ranked, policy["top_count"], policy["bottom_count"]
    )
    for row in rows:
        theme_id = row["theme_id"]
        row.update({
            "prior_rank": prior_ranks[theme_id],
            "current_rank": current_ranks[theme_id],
            "rank_change": prior_ranks[theme_id] - current_ranks[theme_id],
            "prior_bucket": prior_buckets[theme_id],
            "current_bucket": current_buckets[theme_id],
            "bucket_transition": (
                f"{prior_buckets[theme_id]}_TO_{current_buckets[theme_id]}"
            ),
        })
    result["top_themes"] = current_ranked[: policy["top_count"]]
    result["bottom_themes"] = list(
        reversed(current_ranked[-policy["bottom_count"] :])
    )
    result["lineage"]["rotation_policy_sha256"] = producer.payload_sha256(policy)
    result = refresh_packet(result)
    producer.validate_packet(result)
    return result


def us_packet(
    as_of_date="2026-08-20", *, prior_values=None, current_values=None,
    keep_theme_ids=None,
):
    packet = US_FIXTURE.UCR.build_packet(
        US_FIXTURE.input_packet(), US_FIXTURE.policy()
    )
    packet = _move_packet_date(packet, as_of_date, US_FIXTURE.UCR)
    if prior_values is not None or current_values is not None or keep_theme_ids is not None:
        existing = {
            row["theme_id"]: row for row in packet["theme_observations"]
        }
        prior_values = prior_values or {
            key: row["prior_relative_strength_vs_benchmark"]
            for key, row in existing.items()
        }
        current_values = current_values or {
            key: row["current_relative_strength_vs_benchmark"]
            for key, row in existing.items()
        }
        packet = _rederive_us_packet(
            packet, prior_values, current_values, keep_theme_ids
        )
    return packet


def korea_packet(as_of_date="2026-08-20"):
    input_value, rotation_policy = KOREA_FIXTURE.make_bundle()
    packet = KOREA_FIXTURE.KCR.build_packet(input_value, rotation_policy)
    return _move_packet_date(packet, as_of_date, KOREA_FIXTURE.KCR)


def crypto_packet(as_of_date="2026-08-20"):
    packet = CRYPTO_FIXTURE.MODULE.build_packet(
        CRYPTO_FIXTURE.input_packet(),
        CRYPTO_FIXTURE.policy(),
        CRYPTO_FIXTURE.CONTRACT,
    )
    return _move_packet_date(packet, as_of_date, CRYPTO_FIXTURE.MODULE)


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
        self.assertEqual(records["THEME.COMPUTE"][0]["current_p2_state"], "WEAKENING")
        self.assertEqual(
            records["THEME.COMPUTE"][0]["state_transition"],
            "UNINITIALIZED_TO_WEAKENING",
        )
        self.assertEqual(records["THEME.NETWORK"][0]["current_p2_state"], "STRONG")
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
            prior_values={
                "THEME.COMPUTE": "0.2", "THEME.NETWORK": "0.4",
                "THEME.POWER": "0",
            },
            current_values={
                "THEME.COMPUTE": "0.5", "THEME.NETWORK": "0.3",
                "THEME.POWER": "0",
            },
        )
        result = MODULE.apply_rotation(
            second_packet,
            policy_for(second_packet),
            ledger,
            CONTRACT,
        )
        records = records_by_entity(result, "US")

        self.assertEqual(result["ledger_revision"], 2)
        self.assertEqual(len(result["records"]), 6)
        self.assertEqual(
            records["THEME.COMPUTE"][1]["state_transition"],
            "WEAKENING_TO_STRONG",
        )
        self.assertEqual(
            records["THEME.NETWORK"][1]["state_transition"],
            "STRONG_TO_WEAKENING",
        )
        self.assertEqual(
            records["THEME.COMPUTE"][1]["prior_record_sha256"],
            records["THEME.COMPUTE"][0]["record_sha256"],
        )

    def test_korea_benchmark_scopes_remain_independent(self):
        packet = korea_packet()
        result = MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)
        self.assertEqual(
            result["source_packets"][0]["scope_ids"],
            ["01::KOSPI", "02::KOSDAQ"],
        )
        rows = {(item["scope_id"], item["entity_id"]): item for item in result["records"]}
        self.assertEqual(
            rows[("02::KOSDAQ", "THEME.KR.KOSDAQ.SEMICONDUCTOR")]["current_p2_state"],
            "STRONG",
        )
        self.assertEqual(
            rows[("01::KOSPI", "THEME.KR.KOSPI.BIO")]["current_p2_state"],
            "STRONG",
        )

    def test_crypto_btc_eth_alt_buckets_use_the_same_ledger_without_sector_inference(self):
        packet = crypto_packet()
        result = MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)
        rows = records_by_entity(result, "CRYPTO")
        self.assertEqual(sorted(rows), ["ALT", "BTC", "ETH"])
        self.assertEqual(rows["ALT"][0]["scope_id"], "BTC_RELATIVE_BUCKETS")
        self.assertEqual(rows["ALT"][0]["current_p2_state"], "WEAKENING")
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
            (tampered, "ROTATION_PACKET_SEMANTIC_INVALID:US"),
            (
                refresh_packet(dict(packet, status="POLICY_NOT_EFFECTIVE")),
                "ROTATION_PACKET_SEMANTIC_INVALID:US",
            ),
        ]
        expanded = copy.deepcopy(packet)
        expanded["authority"]["p2_state_vocabulary_authorized"] = True
        variants.append((refresh_packet(expanded), "ROTATION_PACKET_SEMANTIC_INVALID:US"))
        classified = copy.deepcopy(packet)
        classified["theme_observations"][0]["p2_state"] = "STRONG"
        variants.append((refresh_packet(classified), "ROTATION_PACKET_SEMANTIC_INVALID:US"))
        for value, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationStateLedgerError, error
            ):
                MODULE.apply_rotation(value, policy_for(packet), contract=CONTRACT)

    def test_stale_producer_schema_version_is_rejected_by_ledger(self):
        # A producer packet still stamped with the pre-hardening schema/
        # contract version must never silently pass through the ledger --
        # it is caught by the producer's own validate_packet() (invoked
        # first, per PRODUCTION_ROTATION_VALIDATORS) since that producer's
        # own contract was bumped in the same PR as the ledger's identity
        # pin in config/rotation_state_ledger_contract.json.
        packet = us_packet()
        for field, stale in (
            ("schema_version", "us_capital_rotation_packet/1"),
            ("contract_version", "us_capital_rotation/1"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(packet)
                tampered[field] = stale
                tampered = refresh_packet(tampered)
                with self.assertRaisesRegex(
                    MODULE.RotationStateLedgerError,
                    "ROTATION_PACKET_SEMANTIC_INVALID:US:OUTPUT_IDENTITY_INVALID",
                ):
                    MODULE.apply_rotation(tampered, policy_for(packet), contract=CONTRACT)

    def test_structural_transition_must_match_prior_and_current_bucket(self):
        packet = us_packet()
        packet["theme_observations"][0]["bucket_transition"] = "BOTTOM_TO_TOP"
        packet = refresh_packet(packet)
        with self.assertRaisesRegex(
            MODULE.RotationStateLedgerError, "ROTATION_PACKET_SEMANTIC_INVALID:US"
        ):
            MODULE.apply_rotation(packet, policy_for(packet), contract=CONTRACT)

    def test_all_market_production_validators_reject_self_rehashed_semantic_tamper(self):
        us = us_packet()
        us["theme_observations"][0]["current_rank"] = 3
        korea = korea_packet()
        korea["benchmark_scopes"][0]["theme_observations"][0][
            "current_rank_within_benchmark"
        ] = 3
        crypto = crypto_packet()
        crypto["sector_chain_layer"]["status"] = "OBSERVED_UNCLASSIFIED"
        for packet in (us, korea, crypto):
            market = packet["market"]
            packet = refresh_packet(packet)
            with self.subTest(market=market), self.assertRaisesRegex(
                MODULE.RotationStateLedgerError,
                f"ROTATION_PACKET_SEMANTIC_INVALID:{market}",
            ):
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
            prior_values={
                "THEME.COMPUTE": "0.2", "THEME.NETWORK": "0.4",
                "THEME.POWER": "0",
            },
            current_values={
                "THEME.COMPUTE": "0.5", "THEME.NETWORK": "0.3",
                "THEME.POWER": "0",
            },
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
            prior_values={
                "THEME.COMPUTE": "0.2", "THEME.NETWORK": "0.4",
                "THEME.POWER": "0",
            },
            current_values={
                "THEME.COMPUTE": "0.5", "THEME.NETWORK": "0.3",
                "THEME.POWER": "0",
            },
            keep_theme_ids=["THEME.COMPUTE", "THEME.NETWORK"],
        )
        result = MODULE.apply_rotation(second, policy_for(second), ledger, CONTRACT)
        records = records_by_entity(result, "US")
        self.assertEqual(len(records["THEME.COMPUTE"]), 2)
        self.assertEqual(len(records["THEME.POWER"]), 1)
        self.assertNotIn("tombstone", MODULE.canonical_json(result).lower())

    def test_source_identity_drift_fails_instead_of_silent_chain_join(self):
        first = korea_packet()
        ledger = MODULE.apply_rotation(first, policy_for(first), contract=CONTRACT)
        second = korea_packet("2026-08-21")
        scope = second["benchmark_scopes"][0]
        row = scope["theme_observations"][0]
        old_identity = row["series_identity"]
        new_identity = f"{old_identity}_V2"
        row["series_identity"] = new_identity
        policy_scope = second["rotation_policy"]["benchmark_scopes"][0]
        member = next(
            item for item in policy_scope["members"]
            if item["series_identity"] == old_identity
        )
        member["series_identity"] = new_identity
        policy_scope["members"].sort(key=lambda item: item["series_identity"])
        scope["theme_observations"].sort(key=lambda item: item["series_identity"])
        second["lineage"]["rotation_policy_sha256"] = MODULE.payload_sha256(
            second["rotation_policy"]
        )
        second = refresh_packet(second)
        KOREA_FIXTURE.KCR.validate_packet(second)
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
