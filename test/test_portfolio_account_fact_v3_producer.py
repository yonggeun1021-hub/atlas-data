#!/usr/bin/env python3
"""Focused suite for the ``portfolio_account_fact/3`` producer/validator.

Everything positive here runs against a DISPOSABLE git repository built in
a temporary directory.  The consumption authority row, its approval
evidence and the consumer identifier in that repository are UNMISTAKABLY
SYNTHETIC -- they are never committed to this repository, never a real CIO
ratification, and never a real grant.  The shipped registry stays empty and
is asserted to resolve blocked.

The account numbers, valuations and hashes in the source bundle are equally
synthetic.  No real account fact exists here, no broker/network/credential
access happens, and passing tests establish implementation correctness
only -- never live readiness, never empirical freshness validation, and
never any risk, sizing, order, production or trading authority.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity  # noqa: E402
from portfolio_risk import kis_valuation_authority  # noqa: E402
from portfolio_risk import portfolio_account_fact_consumption_authority as auth  # noqa: E402
from portfolio_risk import portfolio_account_fact_v3 as fact_v3  # noqa: E402
from portfolio_risk import portfolio_account_fact_v3_producer as producer  # noqa: E402


# --- unmistakably synthetic fixture identifiers -----------------------------
SYNTHETIC_CONSUMER = "SYNTHETIC_FIXTURE_CONSUMER_NOT_A_REAL_GRANT"
SYNTHETIC_OTHER_CONSUMER = "SYNTHETIC_FIXTURE_UNLISTED_CONSUMER"
SYNTHETIC_PROPOSAL_SHA256 = hashlib.sha256(
    b"SYNTHETIC_FIXTURE_ACCOUNT_FACT_V3_CONSUMPTION_PROPOSAL"
).hexdigest()
SYNTHETIC_WRONG_BINDING_SHA256 = hashlib.sha256(
    b"SYNTHETIC_FIXTURE_WRONG_SEMANTIC_BINDING"
).hexdigest()

RATIFIED_AT = "2026-08-29T03:20:00Z"
FUTURE_RATIFIED_AT = "2026-09-01T00:00:00Z"
BASE_COMMIT_AT = "2026-08-20T00:00:00Z"
DECISION_AT = "2026-08-29T03:20:00Z"
LATER_DECISION_AT = "2026-08-29T04:00:00Z"
EXPIRY_AT = "2026-08-29T03:59:00Z"

ACTIVE_REGISTRY = "config/portfolio_account_fact_consumption_authority.json"
EMPTY_REGISTRY = (
    "config/portfolio_account_fact_consumption_authority_empty_fixture.json"
)
EXPIRED_REGISTRY = (
    "config/portfolio_account_fact_consumption_authority_expired_fixture.json"
)
FUTURE_REGISTRY = (
    "config/portfolio_account_fact_consumption_authority_future_fixture.json"
)
WRONG_BINDING_REGISTRY = (
    "config/portfolio_account_fact_consumption_authority_wrong_binding_fixture.json"
)
PROVIDER_DOC = "config/data_provider_authority.json"
IDENTITY_DOC = "config/canonical_security_identity.json"
VALUATION_DOC = "config/kis_valuation_authority.json"

# Real, already-ratified artifacts copied byte-for-byte into the disposable
# repository so the existing resolvers have genuine content to verify. No
# copied file is modified, and nothing is written back to this repository.
COPIED_PATHS = (
    PROVIDER_DOC,
    IDENTITY_DOC,
    VALUATION_DOC,
    "evidence/portfolio_risk/approvals/2026-08-29/kis-paper-valuation-semantics.json",
    "evidence/portfolio_risk/approvals/2026-08-29/kis-paper-valuation-freshness.json",
    "portfolio_risk/kis_valuation_semantic_proposal.py",
    "portfolio_risk/kis_valuation_semantic_review.py",
    "portfolio_risk/kis_valuation_freshness_policy_proposal.py",
    "portfolio_risk/kis_valuation_freshness_policy_review.py",
    "portfolio_risk/portfolio_account_fact_v3.py",
)
APPROVAL_SOURCE_EVIDENCE_PATHS = (
    "portfolio_risk/portfolio_account_fact_v3.py",
    VALUATION_DOC,
)


def _json_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _entry(raw_field: str, value: int) -> dict:
    return {"rawKisField": raw_field, "value": value}


def _shift(timestamp: str, seconds: int) -> str:
    moment = dt.datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%SZ")
    return (moment + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bundle(decision_at: str = DECISION_AT, *, positions: bool = True) -> dict:
    """A SYNTHETIC private source bundle. Not a real account fact."""
    market_value = 700_000 if positions else 0
    unrealized = -5_000 if positions else 0
    cash = 300_000
    value = {
        "contractVersion": fact_v3.SOURCE_BUNDLE_VERSION,
        "providerTuple": dict(fact_v3.PROVIDER_TUPLE),
        "balanceObservation": {
            "sourceContractVersion": "kis_paper_full_account_snapshot/3",
            "sourceRecordSha256": "1" * 64,
            "accountIdentityHash": "2" * 64,
            "capturedAt": _shift(decision_at, -3),
            "availableAt": _shift(decision_at, -2),
            "account": {
                "netAssetKrw": _entry("nass_amt", cash + market_value),
                "cashDepositTotalKrw": _entry("dnca_tot_amt", cash),
            },
            "rawReconciliation": {
                "scts_evlu_amt": market_value,
                "tot_evlu_amt": cash + market_value,
                "evlu_amt_smtl_amt": market_value,
                "evlu_pfls_smtl_amt": unrealized,
            },
            "positions": [{
                "sourceName": "kis_paper_domestic_balance",
                "sourceAssetId": "071050",
                "holdingQuantity": 10,
                "orderableQuantity": 7,
                "marketValueKrw": _entry("evlu_amt", market_value),
                "unrealizedPlKrw": _entry("evlu_pfls_amt", unrealized),
            }] if positions else [],
        },
        "instrumentBuyCapacityObservation": {
            "sourceContractVersion": "kis_paper_buy_capacity_snapshot/1",
            "sourceRecordSha256": "3" * 64,
            "accountIdentityHash": "2" * 64,
            "capturedAt": _shift(decision_at, -1),
            "availableAt": _shift(decision_at, -1),
            "instrument": {
                "sourceName": "kis_paper_domestic_balance",
                "sourceAssetId": "071050",
            },
            "capacity": {
                "noReceivableBuyAmountKrw": _entry("nrcvb_buy_amt", 200_000),
                "noReceivableBuyQuantity": _entry("nrcvb_buy_qty", 2),
                "quantityCalculationPriceKrw": _entry("psbl_qty_calc_unpr", 90_000),
            },
        },
        "sourceBindings": {
            "fullAccountRecordSha256": "1" * 64,
            "buyCapacityRecordSha256": "3" * 64,
            "pairBindingRecordSha256": "4" * 64,
            "lockedRuntimeReceiptSha256": "5" * 64,
        },
    }
    value["bundleSha256"] = fact_v3.payload_sha256(value)
    return value


def _rehash_fact(fact: dict) -> dict:
    """Recompute the self-hash of a mutated fact -- exactly what a tamper
    with access to the public hashing rule would do."""
    fact["factSha256"] = fact_v3.payload_sha256(
        {key: value for key, value in fact.items() if key != "factSha256"}
    )
    return fact


# ---------------------------------------------------------------------------
# Disposable synthetic repository
# ---------------------------------------------------------------------------

class _SyntheticRepo:
    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init", "-q")
        self._run("config", "user.email", "synthetic-fixture@example.invalid")
        self._run("config", "user.name", "Synthetic Fixture")

    def _run(self, *args: str, env=None) -> None:
        subprocess.run(
            ["git", *args], cwd=self.root, check=True, capture_output=True, env=env,
        )

    def write(self, rel_path: str, data: bytes) -> Path:
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def commit(self, message: str, commit_iso: str) -> str:
        self._run("add", "-A")
        env = dict(
            os.environ,
            GIT_AUTHOR_DATE=commit_iso.replace("Z", "+00:00"),
            GIT_COMMITTER_DATE=commit_iso.replace("Z", "+00:00"),
        )
        self._run("commit", "-q", "-m", message, env=env)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.root,
            capture_output=True, text=True, check=True,
        ).stdout.strip()


def _consumption_document(
    *, ratified_at: str, effective_to: str | None, permitted_consumers: list[str],
    bound_semantic: str, bound_freshness: str, source_evidence: list[dict],
) -> tuple[dict, dict]:
    """Build a SYNTHETIC consumption record and its approval evidence."""
    row = {
        "ruleId": auth.RULE_ID,
        "ruleVersion": 1,
        "authorityKind": auth.AUTHORITY_KIND,
        "approvalStatus": auth.RATIFIED,
        "ratifiedAt": ratified_at,
        "firstSeenAt": ratified_at,
        "effectiveFrom": ratified_at,
        "effectiveTo": effective_to,
        "providerTuple": dict(auth.PROVIDER_TUPLE),
        "targetContractVersion": auth.TARGET_CONTRACT_VERSION,
        "sourceBundleContractVersion": auth.SOURCE_BUNDLE_CONTRACT_VERSION,
        "proposalSha256": SYNTHETIC_PROPOSAL_SHA256,
        "boundSemanticAuthorityBusinessPayloadSha256": bound_semantic,
        "boundFreshnessAuthorityBusinessPayloadSha256": bound_freshness,
        "exactCapacityCanonicalInstrumentId": (
            fact_v3.EXACT_CAPACITY_CANONICAL_INSTRUMENT_ID
        ),
        "exactCapacityListingId": fact_v3.EXACT_CAPACITY_LISTING_ID,
        "permittedConsumers": list(permitted_consumers),
        "permittedUse": auth.PERMITTED_USE,
        "approvalBasis": (
            "SYNTHETIC_TEMPORARY_GIT_FIXTURE_NOT_A_REAL_CIO_RATIFICATION"
        ),
        "empiricalValidationStatus": (
            "SYNTHETIC_FIXTURE_ONLY_NO_LIVE_ACCOUNT_AND_NO_EMPIRICAL_VALIDATION"
        ),
        "authority": dict(auth.ACCOUNT_FACT_CONSUMPTION_AUTHORITY),
    }
    business_hash = auth.payload_sha256(auth.business_payload(row))
    row["businessPayloadSha256"] = business_hash
    approval = {
        "schemaVersion": auth.APPROVAL_SCHEMA_VERSION,
        "approvalStatus": auth.RATIFIED,
        "ratifiedAt": ratified_at,
        "authorityKind": auth.AUTHORITY_KIND,
        "ruleId": auth.RULE_ID,
        "ruleVersion": 1,
        "approvedBusinessPayloadSha256": business_hash,
        "sourceEvidence": source_evidence,
        "assertion": {
            "providerTuple": dict(auth.PROVIDER_TUPLE),
            "targetContractVersion": row["targetContractVersion"],
            "sourceBundleContractVersion": row["sourceBundleContractVersion"],
            "proposalSha256": row["proposalSha256"],
            "boundSemanticAuthorityBusinessPayloadSha256": bound_semantic,
            "boundFreshnessAuthorityBusinessPayloadSha256": bound_freshness,
            "exactCapacityCanonicalInstrumentId": row[
                "exactCapacityCanonicalInstrumentId"
            ],
            "exactCapacityListingId": row["exactCapacityListingId"],
            "permittedConsumers": list(permitted_consumers),
            "riskInputIncluded": False,
            "sizingIncluded": False,
            "orderIncluded": False,
            "productionIncluded": False,
        },
        "decision": {
            "decisionStatus": auth.APPROVAL_DECISION_STATUS,
            "basis": (
                "Synthetic disposable fixture proving the mechanism resolves. "
                "It is not a CIO decision and authorizes nothing outside this "
                "temporary directory."
            ),
            "retroactiveUsePermitted": False,
            "realCapitalUseIncluded": False,
        },
        "boundary": auth.APPROVAL_BOUNDARY,
    }
    approval_bytes = _json_bytes(approval)
    approval_name = f"account_fact_v3_consumption_{business_hash[:12]}_{ratified_at[:10]}"
    approval_ref = f"evidence/synthetic_fixture/{approval_name}.json"
    row["approvalEvidenceRef"] = approval_ref
    row["approvalEvidenceSha256"] = hashlib.sha256(approval_bytes).hexdigest()
    document = {
        "schemaVersion": auth.SCHEMA_VERSION,
        "policyVersion": auth.POLICY_VERSION,
        "evidenceBasis": (
            "SYNTHETIC disposable test fixture. Not a CIO ratification, not a "
            "real consumer grant, and never committed to the real repository."
        ),
        "accountFactConsumptionAuthorityRecords": [row],
    }
    return document, {"ref": approval_ref, "bytes": approval_bytes}


class SyntheticRepositoryFixture:
    """Builds the disposable repository exactly once for the whole module."""

    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = _SyntheticRepo(Path(self._tmp.name).resolve() / "repo")
        self.repo.write("README.md", b"synthetic disposable fixture\n")
        self.base_commit = self.repo.commit("base", BASE_COMMIT_AT)

        for rel_path in COPIED_PATHS:
            self.repo.write(rel_path, (ROOT / rel_path).read_bytes())
        source_evidence = [
            {
                "path": rel_path,
                "sha256": hashlib.sha256(
                    (ROOT / rel_path).read_bytes()
                ).hexdigest(),
            }
            for rel_path in APPROVAL_SOURCE_EVIDENCE_PATHS
        ]

        valuation = json.loads((ROOT / VALUATION_DOC).read_text(encoding="utf-8"))
        self.semantic_business_sha256 = valuation[
            "valuationSemanticAuthorityRecords"
        ][0]["businessPayloadSha256"]
        self.freshness_business_sha256 = valuation[
            "freshnessPolicyAuthorityRecords"
        ][0]["businessPayloadSha256"]

        def build(**kwargs):
            return _consumption_document(
                bound_semantic=kwargs.pop(
                    "bound_semantic", self.semantic_business_sha256
                ),
                bound_freshness=kwargs.pop(
                    "bound_freshness", self.freshness_business_sha256
                ),
                permitted_consumers=kwargs.pop(
                    "permitted_consumers", [SYNTHETIC_CONSUMER]
                ),
                source_evidence=source_evidence,
                **kwargs,
            )

        active, approval_a = build(ratified_at=RATIFIED_AT, effective_to=None)
        expired, approval_expired = build(
            ratified_at=RATIFIED_AT, effective_to=EXPIRY_AT
        )
        wrong, approval_wrong = build(
            ratified_at=RATIFIED_AT, effective_to=None,
            bound_semantic=SYNTHETIC_WRONG_BINDING_SHA256,
        )
        empty = {
            "schemaVersion": auth.SCHEMA_VERSION,
            "policyVersion": auth.POLICY_VERSION,
            "evidenceBasis": "SYNTHETIC empty-registry fixture.",
            "accountFactConsumptionAuthorityRecords": [],
        }
        for approval in (approval_a, approval_expired, approval_wrong):
            self.repo.write(approval["ref"], approval["bytes"])
        self.repo.write(ACTIVE_REGISTRY, _json_bytes(active))
        self.repo.write(EXPIRED_REGISTRY, _json_bytes(expired))
        self.repo.write(WRONG_BINDING_REGISTRY, _json_bytes(wrong))
        self.repo.write(EMPTY_REGISTRY, _json_bytes(empty))
        self.ratified_commit = self.repo.commit("synthetic ratification", RATIFIED_AT)

        future, approval_future = build(
            ratified_at=FUTURE_RATIFIED_AT, effective_to=None
        )
        self.repo.write(approval_future["ref"], approval_future["bytes"])
        self.repo.write(FUTURE_REGISTRY, _json_bytes(future))
        self.head_commit = self.repo.commit(
            "synthetic future ratification", FUTURE_RATIFIED_AT
        )

        self.provider_authority = canonical_identity.load_provider_authority(
            self.repo.root / PROVIDER_DOC
        )
        self.security_identity = canonical_identity.load_authority(
            self.repo.root / IDENTITY_DOC
        )
        self.valuation_authority = kis_valuation_authority.load_authority(
            self.repo.root / VALUATION_DOC
        )

    def registry(self, rel_path: str = ACTIVE_REGISTRY) -> dict:
        return auth.load_authority(self.repo.root / rel_path)

    def cleanup(self) -> None:
        self._tmp.cleanup()


_FIXTURE: SyntheticRepositoryFixture | None = None


def setUpModule() -> None:
    global _FIXTURE
    _FIXTURE = SyntheticRepositoryFixture()


def tearDownModule() -> None:
    if _FIXTURE is not None:
        _FIXTURE.cleanup()


class ShippedRegistryDefaultTests(unittest.TestCase):
    """The committed registry is empty and must stay fail-closed."""

    def test_shipped_registry_is_empty_and_resolves_blocked(self):
        document = auth.load_authority()
        self.assertEqual(
            document["accountFactConsumptionAuthorityRecords"], []
        )
        result = auth.resolve_account_fact_consumption_authority(
            decision_at=DECISION_AT, authority=document,
            consumer_id=SYNTHETIC_CONSUMER,
        )
        self.assertEqual(result["status"], auth.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
        self.assertTrue(all(value is False for value in result["authority"].values()))

    def test_more_than_one_record_is_structurally_refused(self):
        document = auth.load_authority()
        auth.validate_authority_document(document)
        two = copy.deepcopy(document)
        two["accountFactConsumptionAuthorityRecords"] = [{}, {}]
        with self.assertRaisesRegex(
            auth.PortfolioAccountFactConsumptionAuthorityError,
            "AUTHORITY_AT_MOST_ONE_RECORD_REQUIRED",
        ):
            auth.validate_authority_document(two)

    def test_missing_consumer_id_is_refused_before_any_resolution(self):
        document = auth.load_authority()
        for bad in ("", "   ", None, 1):
            with self.assertRaisesRegex(
                auth.PortfolioAccountFactConsumptionAuthorityError,
                "ACCOUNT_FACT_CONSUMER_ID_REQUIRED",
            ):
                auth.resolve_account_fact_consumption_authority(
                    decision_at=DECISION_AT, authority=document, consumer_id=bad,
                )

    def test_held_evaluator_contract_and_boundary_are_unchanged(self):
        self.assertEqual(
            fact_v3.READINESS_VERSION, "kis_portfolio_account_fact_v3_readiness/1"
        )
        self.assertEqual(
            producer.READINESS_VERSION, "kis_portfolio_account_fact_v3_readiness/2"
        )
        blocked = fact_v3._blocked("ANY", "0" * 64)
        self.assertEqual(
            blocked["privateSourceValidationBoundary"],
            producer.PRIVATE_SOURCE_VALIDATION_BOUNDARY,
        )
        self.assertEqual(blocked["contractVersion"], fact_v3.READINESS_VERSION)
        self.assertIs(
            producer.ACCOUNT_FACT_AUTHORITY["accountFactAuthorized"], True
        )
        downstream = {
            key: value for key, value in producer.ACCOUNT_FACT_AUTHORITY.items()
            if key != "accountFactAuthorized"
        }
        self.assertTrue(all(value is False for value in downstream.values()))


class ProducerSyntheticGitBackedTests(unittest.TestCase):
    """Positive and negative controls against the disposable repository."""

    @classmethod
    def setUpClass(cls):
        cls.fixture = _FIXTURE

    def build(
        self, *, bundle=None, decision_at=DECISION_AT,
        registry=ACTIVE_REGISTRY, consumer_id=SYNTHETIC_CONSUMER,
        trusted_commit=None, security_identity=None,
    ) -> dict:
        return producer.build_kis_portfolio_account_fact_v3(
            bundle=_bundle(decision_at) if bundle is None else bundle,
            decision_at=decision_at,
            provider_authority=self.fixture.provider_authority,
            security_identity=(
                self.fixture.security_identity if security_identity is None
                else security_identity
            ),
            valuation_authority_document=self.fixture.valuation_authority,
            account_fact_authority_document=self.fixture.registry(registry),
            consumer_id=consumer_id,
            trusted_commit=trusted_commit,
        )

    def validate(
        self, fact, source_bundle, *, decision_at=DECISION_AT,
        consumer_id=SYNTHETIC_CONSUMER, registry=ACTIVE_REGISTRY,
        trusted_commit=None,
    ) -> dict:
        return producer.validate_kis_portfolio_account_fact_v3(
            fact, source_bundle=source_bundle, decision_at=decision_at,
            consumer_id=consumer_id,
            provider_authority=self.fixture.provider_authority,
            security_identity=self.fixture.security_identity,
            valuation_authority_document=self.fixture.valuation_authority,
            account_fact_authority_document=self.fixture.registry(registry),
            trusted_commit=trusted_commit,
        )

    # --- positive ---------------------------------------------------------

    def test_full_positive_roundtrip(self):
        bundle = _bundle()
        result = self.build(bundle=bundle)
        self.assertEqual(
            result["status"], producer.RESOLVED_ACCOUNT_FACT_PRODUCED, result
        )
        self.assertEqual(result["contractVersion"], producer.READINESS_VERSION)
        self.assertEqual(result["authority"], producer.ACCOUNT_FACT_AUTHORITY)
        fact = result["accountFact"]
        self.assertIsNotNone(fact)
        self.assertEqual(fact["contractVersion"], "portfolio_account_fact/3")
        self.assertEqual(fact["consumerId"], SYNTHETIC_CONSUMER)
        self.assertEqual(fact["decisionAt"], DECISION_AT)
        self.assertEqual(fact["sourceBundleSha256"], bundle["bundleSha256"])
        self.assertEqual(
            fact["authorityBasis"]["trustedCommit"], self.fixture.head_commit
        )
        self.assertEqual(len(fact["positions"]), 1)
        self.assertEqual(
            fact["positions"][0]["canonicalInstrumentId"], "KRX:071050:COMMON"
        )
        self.assertEqual(fact["positions"][0]["listingId"], "XKRX:071050")
        self.assertEqual(fact["positions"][0]["canonicalIssuerId"], "DART:00432102")
        self.assertEqual(len(fact["instrumentBuyCapacity"]), 1)
        self.assertEqual(
            fact["instrumentBuyCapacity"][0]["canonicalInstrumentId"],
            "KRX:071050:COMMON",
        )
        self.assertEqual(fact["freshness"]["sourceAgeSeconds"], 2)
        self.assertEqual(fact["freshness"]["sourcePairGapSeconds"], 1)
        self.assertEqual(fact["rawReconciliation"]["tot_evlu_amt"], 1_000_000)
        self.assertEqual(self.validate(fact, bundle), fact)

    def test_produced_fact_carries_no_sizing_or_buying_power_vocabulary(self):
        fact = self.build()["accountFact"]
        rendered = json.dumps(fact, sort_keys=True)
        for forbidden in ("buyingPower", "riskCapacity", "positionSize", "ord_psbl_cash"):
            self.assertNotIn(forbidden, rendered)
        self.assertEqual(fact["authority"], producer.ACCOUNT_FACT_AUTHORITY)
        for key, value in fact["authority"].items():
            if key != "accountFactAuthorized":
                self.assertIs(value, False)

    def test_empty_positions_remain_valid_and_mapping_completeness_is_manifest_based(self):
        bundle = _bundle(positions=False)
        result = self.build(bundle=bundle)
        self.assertEqual(result["status"], producer.RESOLVED_ACCOUNT_FACT_PRODUCED)
        fact = result["accountFact"]
        self.assertEqual(fact["positions"], [])
        self.assertEqual(fact["rawReconciliation"]["evlu_amt_smtl_amt"], 0)
        self.assertEqual(fact["account"]["netAssetKrw"]["value"], 300_000)
        self.assertEqual(len(fact_v3._implemented_semantic_mapping_pairs()), 7)
        self.assertEqual(self.validate(fact, bundle), fact)

    def test_explicit_immutable_pin_matches_default_head_resolution(self):
        bundle = _bundle()
        pinned = self.build(bundle=bundle, trusted_commit=self.fixture.head_commit)
        default = self.build(bundle=bundle)
        self.assertEqual(pinned["accountFact"], default["accountFact"])

    # --- negative: authority ---------------------------------------------

    def test_absent_authority_record_keeps_the_existing_terminal_status(self):
        result = self.build(registry=EMPTY_REGISTRY)
        self.assertEqual(
            result["status"],
            fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED,
        )
        self.assertIsNone(result["accountFact"])
        self.assertEqual(result["contractVersion"], producer.READINESS_VERSION)
        self.assertTrue(
            all(value is False for value in result["authority"].values())
        )

    def test_not_yet_usable_record_is_blocked(self):
        result = self.build(registry=FUTURE_REGISTRY)
        self.assertEqual(
            result["status"],
            fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED,
        )
        self.assertEqual(
            result["accountFactAuthorityStatus"],
            auth.NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE,
        )
        self.assertIsNone(result["accountFact"])

    def test_expired_record_is_blocked_on_the_exclusive_upper_bound(self):
        result = self.build(
            decision_at=LATER_DECISION_AT, registry=EXPIRED_REGISTRY,
        )
        self.assertEqual(
            result["status"],
            fact_v3.NOT_COMPUTABLE_ACCOUNT_FACT_AUTHORITY_UNRATIFIED,
        )
        self.assertEqual(
            result["accountFactAuthorityStatus"],
            auth.NOT_COMPUTABLE_AUTHORITY_NOT_YET_USABLE,
        )
        self.assertIsNone(result["accountFact"])
        # The same registry still resolves before its effectiveTo.
        self.assertEqual(
            self.build(registry=EXPIRED_REGISTRY)["status"],
            producer.RESOLVED_ACCOUNT_FACT_PRODUCED,
        )

    def test_unlisted_consumer_is_refused(self):
        result = self.build(consumer_id=SYNTHETIC_OTHER_CONSUMER)
        self.assertEqual(
            result["status"], producer.ACCOUNT_FACT_CONSUMER_NOT_PERMITTED
        )
        self.assertIsNone(result["accountFact"])

    def test_empty_consumer_id_is_refused_outright(self):
        with self.assertRaisesRegex(
            auth.PortfolioAccountFactConsumptionAuthorityError,
            "ACCOUNT_FACT_CONSUMER_ID_REQUIRED",
        ):
            self.build(consumer_id="")

    def test_semantic_binding_mismatch_blocks_production(self):
        result = self.build(registry=WRONG_BINDING_REGISTRY)
        self.assertEqual(
            result["status"],
            producer.ACCOUNT_FACT_AUTHORITY_SEMANTIC_BINDING_MISMATCH,
        )
        self.assertIsNone(result["accountFact"])

    def test_mutable_trusted_commit_is_refused(self):
        for candidate in ("HEAD", self.fixture.head_commit[:8], "main"):
            with self.assertRaisesRegex(
                auth.PortfolioAccountFactConsumptionAuthorityError,
                "AUTHORITY_TRUSTED_COMMIT_NOT_IMMUTABLE",
            ):
                self.build(trusted_commit=candidate)

    def test_historical_commit_without_the_document_is_not_silently_upgraded(self):
        # Pinning to a real earlier commit that predates every authority
        # document must fail closed there -- never fall back to HEAD.
        result = self.build(trusted_commit=self.fixture.base_commit)
        self.assertNotEqual(
            result["status"], producer.RESOLVED_ACCOUNT_FACT_PRODUCED
        )
        self.assertIsNone(result["accountFact"])
        self.assertEqual(result["trustedCommit"], self.fixture.base_commit)

    def test_memory_disk_divergence_is_refused(self):
        path = self.fixture.repo.root / ACTIVE_REGISTRY
        original = path.read_bytes()
        document = self.fixture.registry()
        try:
            tampered = json.loads(original.decode("utf-8"))
            tampered["evidenceBasis"] = "attacker rewrote the basis text"
            path.write_bytes(_json_bytes(tampered))
            with self.assertRaisesRegex(
                auth.PortfolioAccountFactConsumptionAuthorityError,
                "AUTHORITY_MEMORY_DISK_MISMATCH",
            ):
                producer.build_kis_portfolio_account_fact_v3(
                    bundle=_bundle(), decision_at=DECISION_AT,
                    provider_authority=self.fixture.provider_authority,
                    security_identity=self.fixture.security_identity,
                    valuation_authority_document=self.fixture.valuation_authority,
                    account_fact_authority_document=document,
                    consumer_id=SYNTHETIC_CONSUMER,
                    trusted_commit=self.fixture.head_commit,
                )
        finally:
            path.write_bytes(original)

    def test_disk_and_memory_co_tamper_still_fails_against_the_commit(self):
        path = self.fixture.repo.root / ACTIVE_REGISTRY
        original = path.read_bytes()
        try:
            tampered = json.loads(original.decode("utf-8"))
            tampered["evidenceBasis"] = "attacker rewrote disk and memory together"
            path.write_bytes(_json_bytes(tampered))
            with self.assertRaisesRegex(
                auth.PortfolioAccountFactConsumptionAuthorityError,
                "AUTHORITY_DISK_COMMIT_MISMATCH",
            ):
                producer.build_kis_portfolio_account_fact_v3(
                    bundle=_bundle(), decision_at=DECISION_AT,
                    provider_authority=self.fixture.provider_authority,
                    security_identity=self.fixture.security_identity,
                    valuation_authority_document=self.fixture.valuation_authority,
                    account_fact_authority_document=self.fixture.registry(),
                    consumer_id=SYNTHETIC_CONSUMER,
                    trusted_commit=self.fixture.head_commit,
                )
        finally:
            path.write_bytes(original)

    def test_authority_documents_must_share_one_repository(self):
        with self.assertRaisesRegex(
            auth.PortfolioAccountFactConsumptionAuthorityError,
            "AUTHORITY_DOCUMENT_REPOSITORY_MISMATCH",
        ):
            producer.build_kis_portfolio_account_fact_v3(
                bundle=_bundle(), decision_at=DECISION_AT,
                provider_authority=self.fixture.provider_authority,
                security_identity=self.fixture.security_identity,
                valuation_authority_document=self.fixture.valuation_authority,
                account_fact_authority_document=auth.load_authority(),
                consumer_id=SYNTHETIC_CONSUMER,
                trusted_commit=self.fixture.head_commit,
            )

    def test_non_file_backed_authority_document_is_refused(self):
        document = self.fixture.registry()
        document.pop("_sourcePath")
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_AUTHORITY_FILE_PROVENANCE_REQUIRED",
        ):
            producer.build_kis_portfolio_account_fact_v3(
                bundle=_bundle(), decision_at=DECISION_AT,
                provider_authority=self.fixture.provider_authority,
                security_identity=self.fixture.security_identity,
                valuation_authority_document=self.fixture.valuation_authority,
                account_fact_authority_document=document,
                consumer_id=SYNTHETIC_CONSUMER,
                trusted_commit=self.fixture.head_commit,
            )

    # --- negative: existing prerequisites still fire first ----------------

    def test_unratified_position_alias_still_blocks_before_any_fact(self):
        bundle = _bundle()
        bundle["balanceObservation"]["positions"][0]["sourceAssetId"] = "005930"
        bundle["bundleSha256"] = fact_v3.payload_sha256(
            {k: v for k, v in bundle.items() if k != "bundleSha256"}
        )
        result = self.build(bundle=bundle)
        self.assertEqual(
            result["status"], fact_v3.NOT_COMPUTABLE_POSITION_IDENTITY_INCOMPLETE
        )
        self.assertIsNone(result["accountFact"])
        self.assertEqual(result["contractVersion"], producer.READINESS_VERSION)

    def test_stale_source_still_blocks_before_any_fact(self):
        bundle = _bundle()
        balance = bundle["balanceObservation"]
        balance["capturedAt"] = "2026-08-29T03:14:58Z"
        balance["availableAt"] = "2026-08-29T03:14:59Z"
        bundle["bundleSha256"] = fact_v3.payload_sha256(
            {k: v for k, v in bundle.items() if k != "bundleSha256"}
        )
        result = self.build(bundle=bundle)
        self.assertEqual(
            result["status"], fact_v3.NOT_COMPUTABLE_SOURCE_STALE_OR_FUTURE
        )
        self.assertIsNone(result["accountFact"])

    def test_tampered_bundle_hash_is_rejected(self):
        bundle = _bundle()
        bundle["bundleSha256"] = "f" * 64
        with self.assertRaisesRegex(
            fact_v3.PortfolioAccountFactV3Error, "SOURCE_BUNDLE_SHA_MISMATCH"
        ):
            self.build(bundle=bundle)

    # --- negative: validator ---------------------------------------------

    def test_coherent_value_tamper_with_recomputed_hash_is_rejected(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["account"]["cashDepositTotalKrw"]["value"] += 111_000
        tampered["account"]["netAssetKrw"]["value"] += 111_000
        tampered["rawReconciliation"]["tot_evlu_amt"] += 111_000
        _rehash_fact(tampered)
        # Internally coherent and correctly self-hashed -- and still wrong.
        producer.validate_account_fact_structure(
            tampered, valuation_authority_document=self.fixture.valuation_authority,
        )
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_REDERIVATION_MISMATCH",
        ):
            self.validate(tampered, bundle)

    def test_canonical_identity_tamper_with_recomputed_hash_is_rejected(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["positions"][0]["canonicalInstrumentId"] = "KRX:005930:COMMON"
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_REDERIVATION_MISMATCH",
        ):
            self.validate(tampered, bundle)

    def test_self_hash_alone_is_not_accepted_as_source_authentication(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["sourceBindings"]["pairBindingRecordSha256"] = "e" * 64
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_REDERIVATION_MISMATCH",
        ):
            self.validate(tampered, bundle)

    def test_unrecomputed_self_hash_is_rejected(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        # Keep structural arithmetic valid so this case isolates the stale hash.
        tampered["sourceBindings"]["pairBindingRecordSha256"] = "e" * 64
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError, "ACCOUNT_FACT_SHA_MISMATCH"
        ):
            self.validate(tampered, bundle)

    def test_validator_requires_the_original_source_bundle(self):
        fact = self.build(bundle=_bundle())["accountFact"]
        other = _bundle()
        other["sourceBindings"]["lockedRuntimeReceiptSha256"] = "a" * 64
        other["bundleSha256"] = fact_v3.payload_sha256(
            {k: v for k, v in other.items() if k != "bundleSha256"}
        )
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_SOURCE_BUNDLE_BINDING_MISMATCH",
        ):
            self.validate(fact, other)

    def test_validator_rejects_a_wrong_consumer(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_CONSUMER_BINDING_MISMATCH",
        ):
            self.validate(fact, bundle, consumer_id=SYNTHETIC_OTHER_CONSUMER)

    def test_validator_rejects_a_wrong_decision_instant(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_DECISION_BINDING_MISMATCH",
        ):
            self.validate(fact, bundle, decision_at=LATER_DECISION_AT)

    def test_validator_rejects_a_wrong_trusted_commit(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["authorityBasis"]["trustedCommit"] = (
            self.fixture.ratified_commit
        )
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_TRUSTED_COMMIT_MISMATCH",
        ):
            self.validate(tampered, bundle)

    def test_validator_rejects_an_unknown_field(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["buyingPower"] = 999
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_FIELDS_INVALID",
        ):
            self.validate(tampered, bundle)

    def test_validator_rejects_an_authority_upgrade_inside_the_fact(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["authority"]["riskInputAuthorized"] = True
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_AUTHORITY_BOUNDARY_INVALID",
        ):
            self.validate(tampered, bundle)

    def test_validator_rejects_a_second_buy_capacity_entry(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["instrumentBuyCapacity"].append(
            copy.deepcopy(fact["instrumentBuyCapacity"][0])
        )
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_BUY_CAPACITY_NOT_EXACTLY_ONE_ENTRY",
        ):
            self.validate(tampered, bundle)

    def test_validator_rejects_a_relabelled_raw_kis_field(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["account"]["cashDepositTotalKrw"]["rawKisField"] = "ord_psbl_cash"
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_ACCOUNT_CASHDEPOSITTOTALKRW_KIS_FIELD_MISMATCH",
        ):
            self.validate(tampered, bundle)

    def test_validator_rejects_a_forged_freshness_measurement(self):
        bundle = _bundle()
        fact = self.build(bundle=bundle)["accountFact"]
        tampered = copy.deepcopy(fact)
        tampered["freshness"]["sourceAgeSeconds"] = 0
        _rehash_fact(tampered)
        with self.assertRaisesRegex(
            producer.PortfolioAccountFactV3ProducerError,
            "ACCOUNT_FACT_FRESHNESS_AGE_MISMATCH",
        ):
            self.validate(tampered, bundle)


if __name__ == "__main__":
    unittest.main()
