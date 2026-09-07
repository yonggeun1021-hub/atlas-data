#!/usr/bin/env python3
"""Focused suite for ``kis_account_observation_input/1``.

Everything positive here runs against the DISPOSABLE git repository the
existing account-fact producer suite already knows how to build, reused
directly so its thirty-six cases are not re-run.  One extra consumption
record is added inside that temporary directory so the ADOPTED consumer
identifier resolves there.  That record is UNMISTAKABLY SYNTHETIC: it is
never written to this repository, it is not a CIO ratification, and it
grants nothing.  The shipped registry stays committed empty and is
asserted to resolve blocked, and the shipped contract's real consumer
allowlist stays empty.

Every account number, valuation and hash below is synthetic.  No real
account fact exists here, no broker/network/credential access happens, and
passing tests establish implementation correctness only -- never live
readiness, never empirical validation, and never any risk, sizing, stage,
buy, action, order, production, trading or REAL authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
TEST_DIR = Path(__file__).resolve().parent
for _import_root in (str(ROOT), str(TEST_DIR)):
    if _import_root not in sys.path:
        sys.path.insert(0, _import_root)

from portfolio_risk import kis_account_observation_input as observation  # noqa: E402
from portfolio_risk import portfolio_account_fact_consumption_authority as auth  # noqa: E402
from portfolio_risk import portfolio_account_fact_v3 as fact_v3  # noqa: E402
from portfolio_risk import portfolio_account_fact_v3_producer as producer  # noqa: E402

# Reused synthetic helpers ONLY -- importing the module by name does not
# collect its test cases into this one.
import test_portfolio_account_fact_v3_producer as producer_fixture  # noqa: E402


# The adopted surface, restated literally so the tests fail if a constant
# in the implementation is quietly redefined.
ADOPTED_CONSUMER_ID = (
    "atlas-private-evidence.shadow_matrix.kis_account_observation.v1"
)
ADOPTED_PERMITTED_USE = "READ_ONLY_KIS_PAPER_ACCOUNT_OBSERVATION"
ADOPTED_CONTRACT_VERSION = "kis_account_observation_input/1"
ADOPTED_PACKET_FIELDS = (
    "contractVersion", "consumerId", "decisionAt", "providerTuple",
    "accountIdentityHash", "accountFact", "sourceBundleSha256", "coverage",
    "authority", "packetSha256",
)
ADOPTED_COVERAGE = {
    "accountScope": "KOREA",
    "currency": "KRW",
    "coverageKind": "SINGLE_KIS_ACCOUNT_OBSERVATION",
    "wholePortfolioComplete": False,
}

UNADOPTED_CONSUMER = "SYNTHETIC_FIXTURE_UNADOPTED_OBSERVATION_CONSUMER"
DECISION_AT = producer_fixture.DECISION_AT
LATER_DECISION_AT = producer_fixture.LATER_DECISION_AT

# Written ONLY into the disposable temporary repository.
ADOPTED_REGISTRY = (
    "config/kis_account_observation_adopted_consumer_fixture.json"
)


def _rehash_packet(packet: dict) -> dict:
    """Recompute the envelope self-hash -- exactly what a tamper with
    access to the public hashing rule would do."""
    packet["packetSha256"] = observation.payload_sha256(
        {name: value for name, value in packet.items() if name != "packetSha256"}
    )
    return packet


class ObservationFixture:
    """The producer fixture plus one synthetic ADOPTED-consumer record."""

    def __init__(self) -> None:
        self.base = producer_fixture.SyntheticRepositoryFixture()
        source_evidence = [
            {
                "path": rel_path,
                "sha256": hashlib.sha256((ROOT / rel_path).read_bytes()).hexdigest(),
            }
            for rel_path in producer_fixture.APPROVAL_SOURCE_EVIDENCE_PATHS
        ]
        document, approval = producer_fixture._consumption_document(
            ratified_at=producer_fixture.RATIFIED_AT,
            effective_to=None,
            permitted_consumers=[ADOPTED_CONSUMER_ID],
            bound_semantic=self.base.semantic_business_sha256,
            bound_freshness=self.base.freshness_business_sha256,
            source_evidence=source_evidence,
        )
        self.base.repo.write(approval["ref"], approval["bytes"])
        self.base.repo.write(
            ADOPTED_REGISTRY, producer_fixture._json_bytes(document)
        )
        self.head_commit = self.base.repo.commit(
            "synthetic adopted-observation-consumer ratification",
            producer_fixture.RATIFIED_AT,
        )

    def registry(self, rel_path: str = ADOPTED_REGISTRY) -> dict:
        return auth.load_authority(self.base.repo.root / rel_path)

    def cleanup(self) -> None:
        self.base.cleanup()


_FIXTURE: ObservationFixture | None = None


def setUpModule() -> None:
    global _FIXTURE
    _FIXTURE = ObservationFixture()


def tearDownModule() -> None:
    if _FIXTURE is not None:
        _FIXTURE.cleanup()


class ShippedContractTests(unittest.TestCase):
    """The committed contract is closed, mechanical and grants nothing."""

    def test_shipped_contract_matches_the_adopted_surface(self):
        contract = observation.load_contract()
        self.assertEqual(contract["contract_version"], ADOPTED_CONTRACT_VERSION)
        self.assertEqual(contract["consumer_id"], ADOPTED_CONSUMER_ID)
        self.assertEqual(contract["permitted_use"], ADOPTED_PERMITTED_USE)
        self.assertEqual(contract["packet_fields"], list(ADOPTED_PACKET_FIELDS))
        self.assertEqual(contract["coverage"], ADOPTED_COVERAGE)
        self.assertEqual(observation.PACKET_FIELDS, ADOPTED_PACKET_FIELDS)
        self.assertEqual(observation.COVERAGE, ADOPTED_COVERAGE)
        self.assertEqual(observation.ADOPTED_CONSUMER_ID, ADOPTED_CONSUMER_ID)
        self.assertEqual(observation.PERMITTED_USE, ADOPTED_PERMITTED_USE)

    def test_contract_is_not_a_runtime_grant_and_the_real_allowlist_stays_empty(self):
        contract = observation.load_contract()
        self.assertEqual(contract["real_consumer_allowlist"], [])
        self.assertIs(contract["runtime_ratified"], False)
        self.assertIs(contract["operationally_applied"], False)
        self.assertIs(
            contract["downstream_risk_consumption"]["risk_input_authorized"], False
        )
        # The technical identifier is separate from the still-empty real
        # allowlist: the shipped registry has no record and blocks even the
        # adopted consumer.
        shipped = auth.load_authority()
        self.assertEqual(shipped["accountFactConsumptionAuthorityRecords"], [])
        resolved = auth.resolve_account_fact_consumption_authority(
            decision_at=DECISION_AT, authority=shipped,
            consumer_id=ADOPTED_CONSUMER_ID,
        )
        self.assertEqual(resolved["status"], auth.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
        self.assertTrue(all(flag is False for flag in resolved["authority"].values()))

    def test_contract_authority_is_fact_only(self):
        contract = observation.load_contract()
        authority = contract["authority"]
        self.assertIs(authority["accountFactAuthorized"], True)
        for name, flag in authority.items():
            if name != "accountFactAuthorized":
                self.assertIs(flag, False, name)
        self.assertEqual(authority, dict(producer.ACCOUNT_FACT_AUTHORITY))
        self.assertEqual(
            contract["upstream"]["target_contract_version"],
            "portfolio_account_fact/3",
        )

    def test_contract_document_tampering_is_refused(self):
        base = observation.load_contract()
        cases = [
            ("real_consumer_allowlist", [ADOPTED_CONSUMER_ID],
             "KIS_OBSERVATION_CONTRACT_ALLOWLIST_NOT_EMPTY"),
            ("runtime_ratified", True,
             "KIS_OBSERVATION_CONTRACT_RUNTIME_RATIFIED_INVALID"),
            ("operationally_applied", True,
             "KIS_OBSERVATION_CONTRACT_OPERATIONALLY_APPLIED_INVALID"),
            ("consumer_id", "some.other.consumer",
             "KIS_OBSERVATION_CONTRACT_CONSUMER_INVALID"),
            ("packet_fields", list(ADOPTED_PACKET_FIELDS) + ["riskCapacityInputs"],
             "KIS_OBSERVATION_CONTRACT_PACKET_FIELDS_INVALID"),
            ("coverage", dict(ADOPTED_COVERAGE, wholePortfolioComplete=True),
             "KIS_OBSERVATION_WHOLE_PORTFOLIO_EXPOSURE_FORBIDDEN"),
            ("permitted_use", "READ_WRITE_EVERYTHING",
             "KIS_OBSERVATION_CONTRACT_PERMITTED_USE_INVALID"),
        ]
        for field, value, code in cases:
            with self.subTest(field=field):
                tampered = copy.deepcopy(base)
                tampered[field] = value
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError, code
                ):
                    observation.validate_contract_document(tampered)
        upgraded = copy.deepcopy(base)
        upgraded["authority"]["riskInputAuthorized"] = True
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID",
        ):
            observation.validate_contract_document(upgraded)
        extra = copy.deepcopy(base)
        extra["risk_capacity_inputs"] = {}
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_CONTRACT_DOCUMENT_FIELDS_INVALID",
        ):
            observation.validate_contract_document(extra)

    def test_forbidden_envelope_vocabulary_guard_rejects_by_name(self):
        # Defence in depth for the closed field set: the guard itself must
        # refuse a whole-portfolio/risk/purchasing-power alias anywhere.
        for token in ("equity", "buyingPower", "risk_capacity_inputs", "fxRate"):
            with self.subTest(token=token):
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError,
                    f"KIS_OBSERVATION_FORBIDDEN_ENVELOPE_VOCABULARY:{token}",
                ):
                    observation._assert_no_forbidden_vocabulary(
                        {"coverage": [{token: 1}]},
                        "KIS_OBSERVATION_FORBIDDEN_ENVELOPE_VOCABULARY",
                    )


class ObservationInputTests(unittest.TestCase):
    """Producer -> observation packet -> independent validator."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = _FIXTURE

    # --- helpers ---------------------------------------------------------

    def documents(self, registry=ADOPTED_REGISTRY) -> dict:
        return {
            "provider_authority": self.fixture.base.provider_authority,
            "security_identity": self.fixture.base.security_identity,
            "valuation_authority_document": self.fixture.base.valuation_authority,
            "account_fact_authority_document": self.fixture.registry(registry),
        }

    def produce_fact(
        self, *, bundle=None, decision_at=DECISION_AT, registry=ADOPTED_REGISTRY,
        consumer_id=ADOPTED_CONSUMER_ID, trusted_commit=None,
    ) -> dict:
        result = producer.build_kis_portfolio_account_fact_v3(
            bundle=producer_fixture._bundle(decision_at) if bundle is None else bundle,
            decision_at=decision_at, consumer_id=consumer_id,
            trusted_commit=trusted_commit, **self.documents(registry),
        )
        self.assertEqual(
            result["status"], producer.RESOLVED_ACCOUNT_FACT_PRODUCED, result
        )
        return result["accountFact"]

    def build(
        self, *, account_fact, source_bundle, decision_at=DECISION_AT,
        consumer_id=ADOPTED_CONSUMER_ID, registry=ADOPTED_REGISTRY,
        trusted_commit=None,
    ) -> dict:
        return observation.build_kis_account_observation_input(
            account_fact=account_fact, source_bundle=source_bundle,
            decision_at=decision_at, consumer_id=consumer_id,
            trusted_commit=trusted_commit, **self.documents(registry),
        )

    def validate(
        self, packet, source_bundle, *, decision_at=DECISION_AT,
        consumer_id=ADOPTED_CONSUMER_ID, registry=ADOPTED_REGISTRY,
        trusted_commit=None,
    ) -> dict:
        return observation.validate_kis_account_observation_input(
            packet, source_bundle=source_bundle, decision_at=decision_at,
            consumer_id=consumer_id, trusted_commit=trusted_commit,
            **self.documents(registry),
        )

    def packet(self, bundle=None, **kwargs) -> tuple[dict, dict]:
        bundle = producer_fixture._bundle() if bundle is None else bundle
        fact = self.produce_fact(bundle=bundle, **kwargs)
        return self.build(account_fact=fact, source_bundle=bundle, **kwargs), bundle

    # --- positive --------------------------------------------------------

    def test_producer_to_packet_to_independent_validator_roundtrip(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        packet = self.build(account_fact=fact, source_bundle=bundle)

        self.assertEqual(tuple(packet), ADOPTED_PACKET_FIELDS)
        self.assertEqual(packet["contractVersion"], ADOPTED_CONTRACT_VERSION)
        self.assertEqual(packet["consumerId"], ADOPTED_CONSUMER_ID)
        self.assertEqual(packet["decisionAt"], DECISION_AT)
        self.assertEqual(packet["providerTuple"], dict(fact_v3.PROVIDER_TUPLE))
        self.assertEqual(
            packet["accountIdentityHash"], fact["accountIdentityHash"]
        )
        self.assertEqual(packet["sourceBundleSha256"], bundle["bundleSha256"])
        self.assertEqual(packet["coverage"], ADOPTED_COVERAGE)
        self.assertEqual(packet["authority"], dict(producer.ACCOUNT_FACT_AUTHORITY))
        self.assertEqual(
            packet["packetSha256"],
            observation.payload_sha256(
                {k: v for k, v in packet.items() if k != "packetSha256"}
            ),
        )
        self.assertEqual(self.validate(packet, bundle), packet)

    def test_packet_carries_the_original_fact_verbatim(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        packet = self.build(account_fact=fact, source_bundle=bundle)
        self.assertEqual(packet["accountFact"], fact)
        # Carried by value, not aliased to the caller's object.
        self.assertIsNot(packet["accountFact"], fact)
        carried = packet["accountFact"]
        self.assertEqual(carried["factSha256"], fact["factSha256"])
        self.assertEqual(
            carried["authorityBasis"]["trustedCommit"], self.fixture.head_commit
        )
        # Native v3 names and their raw KIS provenance survive untouched.
        self.assertEqual(
            carried["account"]["netAssetKrw"]["rawKisField"], "nass_amt"
        )
        self.assertEqual(
            carried["instrumentBuyCapacity"][0]["noReceivableBuyAmountKrw"][
                "rawKisField"
            ],
            "nrcvb_buy_amt",
        )
        self.assertEqual(
            set(carried["positions"][0]), producer._FACT_POSITION_FIELDS
        )

    def test_packet_exposes_no_portfolio_risk_or_purchasing_power_alias(self):
        packet, _ = self.packet()
        rendered = json.dumps(packet, sort_keys=True)
        for token in sorted(observation.FORBIDDEN_OBSERVATION_VOCABULARY):
            self.assertNotIn(f'"{token}"', rendered, token)
        for token in ("riskCapacityInputs", "ord_psbl_cash", "orderableCash"):
            self.assertNotIn(f'"{token}"', rendered, token)
        self.assertIs(packet["coverage"]["wholePortfolioComplete"], False)
        self.assertIs(packet["authority"]["riskInputAuthorized"], False)
        self.assertIs(packet["authority"]["realCapitalAuthorized"], False)

    def test_empty_position_observation_remains_valid(self):
        bundle = producer_fixture._bundle(positions=False)
        packet, _ = self.packet(bundle)
        self.assertEqual(packet["accountFact"]["positions"], [])
        self.assertEqual(
            packet["accountFact"]["account"]["netAssetKrw"]["value"], 300_000
        )
        self.assertEqual(packet["coverage"], ADOPTED_COVERAGE)
        self.assertEqual(self.validate(packet, bundle), packet)

    def test_explicit_immutable_pin_matches_default_head_resolution(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        pinned = self.build(
            account_fact=fact, source_bundle=bundle,
            trusted_commit=self.fixture.head_commit,
        )
        default = self.build(account_fact=fact, source_bundle=bundle)
        self.assertEqual(pinned, default)

    # --- source variation -------------------------------------------------

    def test_source_variation_changes_the_consumed_fact_and_provenance(self):
        held = producer_fixture._bundle()
        empty = producer_fixture._bundle(positions=False)
        held_packet, _ = self.packet(held)
        empty_packet, _ = self.packet(empty)

        self.assertNotEqual(held["bundleSha256"], empty["bundleSha256"])
        self.assertNotEqual(
            held_packet["sourceBundleSha256"], empty_packet["sourceBundleSha256"]
        )
        self.assertNotEqual(
            held_packet["accountFact"], empty_packet["accountFact"]
        )
        self.assertNotEqual(
            held_packet["accountFact"]["factSha256"],
            empty_packet["accountFact"]["factSha256"],
        )
        self.assertNotEqual(
            held_packet["packetSha256"], empty_packet["packetSha256"]
        )
        self.assertEqual(
            held_packet["accountFact"]["rawReconciliation"]["evlu_amt_smtl_amt"],
            700_000,
        )
        self.assertEqual(
            empty_packet["accountFact"]["rawReconciliation"]["evlu_amt_smtl_amt"], 0
        )
        # Each packet validates only against its OWN original source.
        self.assertEqual(self.validate(held_packet, held), held_packet)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_ORIGINAL_SOURCE_BINDING_MISMATCH",
        ):
            self.validate(held_packet, empty)

    def test_wrong_source_with_a_recomputed_bundle_hash_is_refused(self):
        packet, _ = self.packet()
        other = producer_fixture._bundle()
        other["sourceBindings"]["lockedRuntimeReceiptSha256"] = "a" * 64
        other["bundleSha256"] = fact_v3.payload_sha256(
            {k: v for k, v in other.items() if k != "bundleSha256"}
        )
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_ORIGINAL_SOURCE_BINDING_MISMATCH",
        ):
            self.validate(packet, other)

    def test_coherent_fact_tamper_with_recomputed_hashes_is_refused(self):
        packet, bundle = self.packet()
        tampered = copy.deepcopy(packet)
        fact = tampered["accountFact"]
        fact["account"]["cashDepositTotalKrw"]["value"] += 111_000
        fact["account"]["netAssetKrw"]["value"] += 111_000
        fact["rawReconciliation"]["tot_evlu_amt"] += 111_000
        producer_fixture._rehash_fact(fact)
        _rehash_packet(tampered)
        # Internally coherent, correctly self-hashed at both levels, and
        # still refused because it no longer matches the original source.
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_REDERIVATION_MISMATCH",
        ):
            self.validate(tampered, bundle)

    # --- missing / unusable inputs ---------------------------------------

    def test_absent_account_fact_is_a_precise_closed_error(self):
        bundle = producer_fixture._bundle()
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_ACCOUNT_FACT_MISSING",
        ):
            self.build(account_fact=None, source_bundle=bundle)
        for bad in ({}, [], "portfolio_account_fact/3", 7):
            with self.subTest(bad=bad):
                with self.assertRaises(
                    observation.KisAccountObservationInputError
                ):
                    self.build(account_fact=bad, source_bundle=bundle)

    def test_absent_authority_keeps_the_existing_producer_refusal(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_REDERIVATION_BLOCKED:"
            "NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED",
        ):
            self.build(
                account_fact=fact, source_bundle=bundle,
                registry=producer_fixture.EMPTY_REGISTRY,
            )

    def test_non_file_backed_authority_document_is_refused(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        document = self.fixture.registry()
        document.pop("_sourcePath")
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_AUTHORITY_FILE_PROVENANCE_REQUIRED",
        ):
            observation.build_kis_account_observation_input(
                account_fact=fact, source_bundle=bundle,
                decision_at=DECISION_AT, consumer_id=ADOPTED_CONSUMER_ID,
                provider_authority=self.fixture.base.provider_authority,
                security_identity=self.fixture.base.security_identity,
                valuation_authority_document=(
                    self.fixture.base.valuation_authority
                ),
                account_fact_authority_document=document,
            )

    # --- consumer / decision / pin ---------------------------------------

    def test_unadopted_or_blank_caller_is_refused_before_any_authority_work(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_CONSUMER_NOT_ADOPTED",
        ):
            self.build(
                account_fact=fact, source_bundle=bundle,
                consumer_id=UNADOPTED_CONSUMER,
            )
        for blank in ("", "   ", None, 1):
            with self.subTest(blank=blank):
                with self.assertRaisesRegex(
                    auth.PortfolioAccountFactConsumptionAuthorityError,
                    "ACCOUNT_FACT_CONSUMER_ID_REQUIRED",
                ):
                    self.build(
                        account_fact=fact, source_bundle=bundle,
                        consumer_id=blank,
                    )

    def test_validator_rejects_a_misbound_consumer_and_decision(self):
        packet, bundle = self.packet()
        rebound = copy.deepcopy(packet)
        rebound["consumerId"] = UNADOPTED_CONSUMER
        _rehash_packet(rebound)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_CONSUMER_BINDING_MISMATCH",
        ):
            self.validate(rebound, bundle)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_DECISION_BINDING_MISMATCH",
        ):
            self.validate(packet, bundle, decision_at=LATER_DECISION_AT)

    def test_structural_validator_rejects_a_packet_consumer_that_is_not_adopted(self):
        packet, _ = self.packet()
        rebound = copy.deepcopy(packet)
        rebound["consumerId"] = UNADOPTED_CONSUMER
        _rehash_packet(rebound)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_CONSUMER_NOT_ADOPTED",
        ):
            observation.validate_observation_packet_structure(rebound)

    def test_builder_rejects_a_decision_instant_the_fact_does_not_carry(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_DECISION_BINDING_MISMATCH",
        ):
            self.build(
                account_fact=fact, source_bundle=bundle,
                decision_at=LATER_DECISION_AT,
            )

    def test_wrong_or_mutable_trusted_pin_is_refused(self):
        bundle = producer_fixture._bundle()
        fact = self.produce_fact(bundle=bundle)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_TRUSTED_COMMIT_MISMATCH",
        ):
            self.build(
                account_fact=fact, source_bundle=bundle,
                trusted_commit=self.fixture.base.base_commit,
            )
        for mutable in ("HEAD", self.fixture.head_commit[:8], "main"):
            with self.subTest(pin=mutable):
                with self.assertRaisesRegex(
                    auth.PortfolioAccountFactConsumptionAuthorityError,
                    "AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE",
                ):
                    self.build(
                        account_fact=fact, source_bundle=bundle,
                        trusted_commit=mutable,
                    )

    # --- corrupted packet -------------------------------------------------

    def test_corrupted_packet_hash_is_rejected(self):
        packet, bundle = self.packet()
        tampered = copy.deepcopy(packet)
        tampered["packetSha256"] = "f" * 64
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_PACKET_SHA_MISMATCH",
        ):
            self.validate(tampered, bundle)

    def test_recomputed_packet_hash_does_not_launder_a_rebound_envelope(self):
        packet, bundle = self.packet()
        for field, value, code in (
            ("accountIdentityHash", "9" * 64,
             "KIS_OBSERVATION_ACCOUNT_BINDING_MISMATCH"),
            ("sourceBundleSha256", "8" * 64,
             "KIS_OBSERVATION_SOURCE_BUNDLE_BINDING_MISMATCH"),
            ("contractVersion", "portfolio_risk_input/1",
             "KIS_OBSERVATION_CONTRACT_VERSION_INVALID"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(packet)
                tampered[field] = value
                _rehash_packet(tampered)
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError, code
                ):
                    self.validate(tampered, bundle)

    def test_extra_or_missing_packet_field_is_rejected(self):
        packet, bundle = self.packet()
        extra = copy.deepcopy(packet)
        extra["riskCapacityInputs"] = {"budgetPct": 1}
        _rehash_packet(extra)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_PACKET_FIELDS_INVALID",
        ):
            self.validate(extra, bundle)
        for field in ADOPTED_PACKET_FIELDS:
            with self.subTest(field=field):
                missing = copy.deepcopy(packet)
                missing.pop(field)
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError,
                    "KIS_OBSERVATION_PACKET_FIELDS_INVALID",
                ):
                    self.validate(missing, bundle)

    def test_coverage_invariants_are_enforced(self):
        packet, bundle = self.packet()
        cases = [
            ({"wholePortfolioComplete": True},
             "KIS_OBSERVATION_WHOLE_PORTFOLIO_EXPOSURE_FORBIDDEN"),
            ({"wholePortfolioComplete": 0},
             "KIS_OBSERVATION_COVERAGE_INVALID"),
            ({"coverageKind": "WHOLE_PORTFOLIO_NAV"},
             "KIS_OBSERVATION_COVERAGE_INVALID"),
            ({"currency": "USD"},
             "KIS_OBSERVATION_COVERAGE_PROVIDER_BINDING_MISMATCH"),
            ({"accountScope": "US"},
             "KIS_OBSERVATION_COVERAGE_PROVIDER_BINDING_MISMATCH"),
            ({"nav": 1_000_000}, "KIS_OBSERVATION_COVERAGE_INVALID"),
        ]
        for override, code in cases:
            with self.subTest(override=override):
                tampered = copy.deepcopy(packet)
                tampered["coverage"].update(override)
                _rehash_packet(tampered)
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError, code
                ):
                    self.validate(tampered, bundle)

    def test_authority_upgrade_inside_the_packet_is_rejected(self):
        packet, bundle = self.packet()
        for flag in (
            "riskInputAuthorized", "stageAuthorized", "buyAuthorized",
            "orderAuthorized", "tradingAuthorized", "realCapitalAuthorized",
        ):
            with self.subTest(flag=flag):
                tampered = copy.deepcopy(packet)
                tampered["authority"][flag] = True
                _rehash_packet(tampered)
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError,
                    "KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID",
                ):
                    self.validate(tampered, bundle)
        aliased = copy.deepcopy(packet)
        aliased["authority"]["accountFactAuthorized"] = 1
        _rehash_packet(aliased)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_AUTHORITY_BOUNDARY_INVALID",
        ):
            self.validate(aliased, bundle)

    def test_provider_tuple_tampering_is_rejected(self):
        packet, bundle = self.packet()
        tampered = copy.deepcopy(packet)
        tampered["providerTuple"]["currency"] = "USD"
        _rehash_packet(tampered)
        with self.assertRaisesRegex(
            observation.KisAccountObservationInputError,
            "KIS_OBSERVATION_PROVIDER_TUPLE_INVALID",
        ):
            self.validate(tampered, bundle)

    def test_fact_envelope_rebinding_is_rejected(self):
        packet, bundle = self.packet()
        for field, value, code in (
            ("consumerId", UNADOPTED_CONSUMER,
             "KIS_OBSERVATION_FACT_CONSUMER_BINDING_MISMATCH"),
            ("decisionAt", LATER_DECISION_AT,
             "KIS_OBSERVATION_FACT_DECISION_BINDING_MISMATCH"),
        ):
            with self.subTest(field=field):
                tampered = copy.deepcopy(packet)
                tampered["accountFact"][field] = value
                producer_fixture._rehash_fact(tampered["accountFact"])
                _rehash_packet(tampered)
                with self.assertRaisesRegex(
                    observation.KisAccountObservationInputError, code
                ):
                    self.validate(tampered, bundle)


if __name__ == "__main__":
    unittest.main()
