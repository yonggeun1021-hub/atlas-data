#!/usr/bin/env python3
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from portfolio import profit_harvest_transition_readiness as transition


def seal(value, field="packet_sha256"):
    result = copy.deepcopy(value)
    result[field] = transition.payload_sha256(result)
    return result


def ratification():
    return seal(
        {
            "schema_version": transition.RATIFICATION_SCHEMA_VERSION,
            "scope": "P7_11_FORMAL_TRANSITION_EVIDENCE_ONLY",
            "wbs": copy.deepcopy(transition.P7_11_ROW),
            "status": "RATIFIED",
            "ratified_by": "CIO",
            "ratified_at_utc": "2026-09-03T00:00:00Z",
            "effective_from_utc": "2026-09-03T00:05:00Z",
            "policy_sha256": "a" * 64,
            "authority": copy.deepcopy(transition.EVIDENCE_AUTHORITY),
        }
    )


def settlement(amount="18"):
    return seal(
        {
            "schema_version": transition.SETTLEMENT_SCHEMA_VERSION,
            "settlement_status": "SETTLED_AVAILABLE_CASH",
            "settlement_id": "SETTLEMENT.1",
            "market": "CRYPTO",
            "canonical_instrument_id": "UPBIT:KRW-BTC:SPOT",
            "ledger_id": "PAPER.LEDGER.1",
            "entry_order_id": "PAPER.BUY.1",
            "exit_order_ids": ["PAPER.SELL.1"],
            "sell_fill_ids": ["PAPER.FILL.SELL.1"],
            "settled_at_utc": "2026-09-03T00:11:00Z",
            "proceeds": {"amount": amount, "currency": "KRW"},
            "source": {
                "source_kind": "PRIVATE_VIRTUAL_LEDGER_SELL_FILL_RECONCILIATION",
                "runtime_receipt_sha256": "b" * 64,
                "ledger_receipt_sha256": "c" * 64,
                "p7_13_exit_decision_sha256": "d" * 64,
                "sell_fill_reconciliation_sha256": "e" * 64,
            },
            "authority": copy.deepcopy(transition.EVIDENCE_AUTHORITY),
        }
    )


def p8_link(settlement_sha):
    return seal(
        {
            "schema_version": transition.P8_13_LINK_SCHEMA_VERSION,
            "wbs_page_id": transition.P8_13_PAGE_ID,
            "proposal_id": "P8.13.HARVEST.1",
            "proposed_action": "HARVEST_REVIEW",
            "proposal_created_at_utc": "2026-09-03T00:06:00Z",
            "entry_proposal_boundary_sha256": "f" * 64,
            "settlement_receipt_sha256": settlement_sha,
            "authority": copy.deepcopy(transition.EVIDENCE_AUTHORITY),
        }
    )


def p7_link(settlement_sha):
    return seal(
        {
            "schema_version": transition.P7_10_LINK_SCHEMA_VERSION,
            "wbs_page_id": transition.P7_10_PAGE_ID,
            "consumer_schema_version": "capital_reallocation_readiness/future",
            "consumer_contract_sha256": "1" * 64,
            "accepted_input_schema_version": transition.SETTLEMENT_SCHEMA_VERSION,
            "settlement_receipt_sha256": settlement_sha,
            "consumer_validation_status": "EXACT_SETTLED_PROCEEDS_INPUT_SUPPORTED",
            "authority": copy.deepcopy(transition.EVIDENCE_AUTHORITY),
        }
    )


def schedule_attestation(settled, *, origin="NATURAL_AUTOMATED"):
    return seal(
        {
            "schema_version": transition.SCHEDULE_ATTESTATION_SCHEMA_VERSION,
            "sample_origin": origin,
            "trigger_kind": "SCHEDULE",
            "scheduler_id": "crypto-paper-runtime.timer",
            "run_id": "RUN.1",
            "scheduled_for_utc": "2026-09-03T00:10:00Z",
            "started_at_utc": "2026-09-03T00:10:05Z",
            "completed_at_utc": "2026-09-03T00:12:00Z",
            "runtime_receipt_sha256": settled["source"]["runtime_receipt_sha256"],
            "settlement_receipt_sha256": settled["packet_sha256"],
            "authority": copy.deepcopy(transition.EVIDENCE_AUTHORITY),
        },
        field="attestation_sha256",
    )


def full_inputs():
    approved = ratification()
    settled = settlement()
    schedule = schedule_attestation(settled)
    upstream = p8_link(settled["packet_sha256"])
    downstream = p7_link(settled["packet_sha256"])
    return {
        "ratification": approved,
        "trusted_ratification_sha256": approved["packet_sha256"],
        "settlement": settled,
        "p8_13_link": upstream,
        "trusted_p8_13_link_sha256": upstream["packet_sha256"],
        "p7_10_link": downstream,
        "trusted_p7_10_link_sha256": downstream["packet_sha256"],
        "schedule_attestation": schedule,
        "trusted_schedule_attestation_sha256": schedule["attestation_sha256"],
    }


class ProfitHarvestTransitionReadinessTests(unittest.TestCase):
    def test_default_is_exact_ratification_blocked_and_money_free(self):
        packet = transition.build_transition_readiness()
        self.assertEqual(packet["decision"]["status"], "BLOCKED_EXACT_RATIFICATION")
        self.assertEqual(packet["decision"]["candidate"], "NONE")
        self.assertIs(type(packet["decision"]["capital"]), int)
        self.assertEqual(packet["decision"]["capital"], 0)
        for field in (
            "expected_proceeds",
            "settled_proceeds",
            "harvest_proposal",
            "reallocation_proposal",
            "trade_proposal",
            "order_intent",
        ):
            self.assertIsNone(packet["decision"][field])
        self.assertEqual(packet["authority"], transition.AUTHORITY_ALL_FALSE)
        self.assertTrue(all(type(value) is int for value in packet["summary"].values()))
        self.assertTrue(all(value == 0 for value in packet["summary"].values()))

    def test_ratification_and_natural_evidence_are_separate_gates(self):
        approved = ratification()
        packet = transition.build_transition_readiness(
            ratification=approved,
            trusted_ratification_sha256=approved["packet_sha256"],
        )
        self.assertEqual(
            packet["decision"]["status"],
            "WAITING_FIRST_GENUINE_SCHEDULED_NATURAL_EVIDENCE",
        )
        self.assertEqual(packet["gates"]["exact_ratification"], "PASS")
        self.assertEqual(
            packet["gates"]["first_genuine_scheduled_natural_evidence"],
            "BLOCKED",
        )

    def test_complete_proof_is_local_adoption_ready_but_non_authorizing(self):
        inputs = full_inputs()
        packet = transition.build_transition_readiness(**inputs)
        self.assertEqual(packet["decision"]["status"], "ADOPTION_READY_LOCAL_ONLY")
        self.assertEqual(set(packet["gates"].values()), {"PASS"})
        self.assertEqual(set(packet["summary"].values()), {1})
        self.assertIsNone(packet["decision"]["settled_proceeds"])
        self.assertEqual(packet["authority"], transition.AUTHORITY_ALL_FALSE)
        self.assertEqual(
            transition.validate_transition_readiness(packet, **inputs), packet
        )

    def test_validated_output_rejects_python_scalar_aliases(self):
        inputs = full_inputs()
        packet = transition.build_transition_readiness(**inputs)
        for path in ("capital", "canonical_status_change_authorized"):
            tampered = copy.deepcopy(packet)
            tampered["decision"][path] = False if path == "capital" else 0
            tampered["packet_sha256"] = transition.payload_sha256(
                {key: value for key, value in tampered.items() if key != "packet_sha256"}
            )
            with self.subTest(path=path), self.assertRaisesRegex(
                transition.ProfitHarvestTransitionReadinessError,
                "TAMPER_OR_DRIFT",
            ):
                transition.validate_transition_readiness(tampered, **inputs)

    def test_positive_proceeds_require_canonical_decimal_string(self):
        for invalid in (False, 0, 18, 18.0, "0", "18.0", "01", "1e2", "-1"):
            packet = settlement()
            packet["proceeds"]["amount"] = invalid
            packet["packet_sha256"] = transition.payload_sha256(
                {key: value for key, value in packet.items() if key != "packet_sha256"}
            )
            with self.subTest(invalid=invalid), self.assertRaisesRegex(
                transition.ProfitHarvestTransitionReadinessError,
                "POSITIVE_CANONICAL_DECIMAL_REQUIRED",
            ):
                transition.validate_settlement(packet)
        for valid in ("1", "0.1", "18.25"):
            self.assertEqual(
                transition.validate_settlement(settlement(valid))["proceeds"]["amount"],
                valid,
            )

    def test_manual_or_replay_cannot_become_natural_by_rehashing(self):
        settled = settlement()
        for origin in ("MANUAL_OBSERVATION", "PIT_REPLAY", "SYNTHETIC_FIXTURE"):
            packet = schedule_attestation(settled, origin=origin)
            with self.subTest(origin=origin), self.assertRaisesRegex(
                transition.ProfitHarvestTransitionReadinessError,
                "GENUINE_SCHEDULED_NATURAL_ORIGIN_INVALID",
            ):
                transition.validate_schedule_attestation(
                    packet,
                    settlement=settled,
                    trusted_sha256=packet["attestation_sha256"],
                )

    def test_self_hash_without_independent_pin_is_rejected(self):
        approved = ratification()
        with self.assertRaisesRegex(
            transition.ProfitHarvestTransitionReadinessError,
            "NOT_INDEPENDENTLY_PINNED",
        ):
            transition.validate_ratification(approved, trusted_sha256="9" * 64)
        settled = settlement()
        schedule = schedule_attestation(settled)
        with self.assertRaisesRegex(
            transition.ProfitHarvestTransitionReadinessError,
            "NOT_INDEPENDENTLY_PINNED",
        ):
            transition.validate_schedule_attestation(
                schedule, settlement=settled, trusted_sha256="8" * 64
            )

    def test_natural_evidence_must_follow_effective_ratification(self):
        inputs = full_inputs()
        inputs["ratification"]["effective_from_utc"] = "2026-09-03T00:10:30Z"
        unsigned = {
            key: value
            for key, value in inputs["ratification"].items()
            if key != "packet_sha256"
        }
        inputs["ratification"]["packet_sha256"] = transition.payload_sha256(unsigned)
        inputs["trusted_ratification_sha256"] = inputs["ratification"]["packet_sha256"]
        inputs["p8_13_link"]["proposal_created_at_utc"] = "2026-09-03T00:10:45Z"
        unsigned = {
            key: value
            for key, value in inputs["p8_13_link"].items()
            if key != "packet_sha256"
        }
        inputs["p8_13_link"]["packet_sha256"] = transition.payload_sha256(unsigned)
        inputs["trusted_p8_13_link_sha256"] = inputs["p8_13_link"]["packet_sha256"]
        with self.assertRaisesRegex(
            transition.ProfitHarvestTransitionReadinessError,
            "NATURAL_EVIDENCE_PREDATES_RATIFICATION",
        ):
            transition.build_transition_readiness(**inputs)

    def test_both_upstream_and_downstream_must_link_exact_settlement(self):
        inputs = full_inputs()
        for field in ("p8_13_link", "p7_10_link"):
            broken = copy.deepcopy(inputs)
            broken[field]["settlement_receipt_sha256"] = "7" * 64
            broken[field]["packet_sha256"] = transition.payload_sha256(
                {
                    key: value
                    for key, value in broken[field].items()
                    if key != "packet_sha256"
                }
            )
            with self.subTest(field=field), self.assertRaisesRegex(
                transition.ProfitHarvestTransitionReadinessError,
                "SETTLEMENT_LINK_MISMATCH",
            ):
                transition.build_transition_readiness(**broken)


if __name__ == "__main__":
    unittest.main()
