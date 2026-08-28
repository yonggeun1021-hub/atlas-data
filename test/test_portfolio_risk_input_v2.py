#!/usr/bin/env python3
"""Portfolio Risk Input Contract v2 (Account Fact) -- provider/scope
separation, and the counter-example scenarios that prove it doesn't
silently reintroduce v1's already-fixed defect classes."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


PS = _load("portfolio_risk.portfolio_snapshot", "portfolio_risk/portfolio_snapshot.py")
PS2 = _load("portfolio_risk.portfolio_snapshot_v2", "portfolio_risk/portfolio_snapshot_v2.py")

T0 = "2026-08-28T12:00:00Z"
T_LATER = "2026-08-28T13:00:00Z"
T_MUCH_LATER = "2026-08-30T13:00:00Z"  # > STALENESS_MAX_AGE_HOURS after T0
T_EARLIER = "2026-08-01T00:00:00Z"

ACCOUNT_IDENTITY_HASH = hashlib.sha256(b"KIS_PAPER_ACCOUNT:12345678:01").hexdigest()


def _positions(**overrides):
    row = {
        "symbol": "005930", "quantity": 10, "market_value": 700000.0, "unrealized_pl": 0.0,
        "source_name": "kis_paper_domestic_balance", "source_asset_id": "005930",
    }
    row.update(overrides)
    return [row]


def _build_fact(*, decision_at=T0, captured_at=T0, available_at=T0, positions=None, **overrides):
    kwargs = dict(
        provider="KIS_PAPER_ACCOUNT",
        account_scope="KOREA",
        account_identity_hash=ACCOUNT_IDENTITY_HASH,
        currency="KRW",
        equity=1_700_000.0,
        cash=1_000_000.0,
        buying_power=1_000_000.0,
        positions=positions if positions is not None else _positions(),
        captured_at=captured_at,
        available_at=available_at,
        decision_at=decision_at,
    )
    kwargs.update(overrides)
    return PS2.build_provider_account_fact_v2(**kwargs)


def _rehash(fact):
    fact["factSha256"] = PS2.payload_sha256({k: v for k, v in fact.items() if k != "factSha256"})
    return fact


class ContractShapeTests(unittest.TestCase):
    def test_contract_config_declares_mechanism_only_unratified_provider_shape(self):
        contract = json.loads((ROOT / "config" / "portfolio_risk_input_contract_v2.json").read_text())
        self.assertEqual(contract["contract_version"], "portfolio_account_fact/2")
        self.assertEqual(contract["status"], "MECHANISM_ONLY_PROPOSED_UNRATIFIED")
        self.assertIn("KIS_PAPER_ACCOUNT", contract["provider_implementations"])
        self.assertEqual(contract["provider_authority_records"], [])
        self.assertEqual(contract["provider_authority_status"], "PROPOSED_UNRATIFIED")
        self.assertEqual(
            contract["fact_usability_status"],
            "NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED",
        )
        self.assertFalse(contract["authority"]["order_authorized"])
        self.assertFalse(contract["authority"]["trading_authorized"])

    def test_contract_config_mirrors_implementation_without_creating_authority(self):
        contract = json.loads((ROOT / "config" / "portfolio_risk_input_contract_v2.json").read_text())
        self.assertEqual(contract["contract_version"], PS2.SCHEMA_VERSION)
        self.assertEqual(contract["provider_implementations"], PS2.PROVIDER_IMPLEMENTATIONS)
        self.assertEqual(contract["provider_authority_records"], [])
        self.assertEqual(contract["provider_authority_status"], PS2.PROVIDER_AUTHORITY_STATUS)
        self.assertEqual(contract["fact_usability_status"], PS2.FACT_USABILITY_STATUS)
        self.assertEqual(contract["authority"], PS2.AUTHORITY_ALL_FALSE)
        self.assertEqual(
            contract["order_eligibility_status"],
            PS2.ORDER_ELIGIBILITY_NOT_APPLICABLE,
        )

    def test_v1_module_is_completely_unaffected(self):
        # v2 must never monkeypatch, extend, or otherwise mutate v1's
        # module-level state.
        self.assertEqual(PS.SCHEMA_VERSION, "portfolio_risk_input/1")
        self.assertIn("ALPACA_PAPER_ACCOUNT", PS.CANONICAL_ACCOUNT_SCOPE)

    def test_unratified_v2_has_no_broker_verified_runtime_claim(self):
        runtime = (ROOT / "portfolio_risk" / "portfolio_snapshot_v2.py").read_text()
        config = (ROOT / "config" / "portfolio_risk_input_contract_v2.json").read_text()
        self.assertNotIn('"BROKER_VERIFIED"', runtime)
        self.assertNotIn('"BROKER_VERIFIED"', config)
        self.assertNotIn("CANONICAL_ACCOUNT_SCOPE", runtime)


class BuildProviderAccountFactV2Tests(unittest.TestCase):
    def test_real_fact_has_explicit_provider_and_scope_fields(self):
        fact = _build_fact()
        self.assertEqual(fact["contractVersion"], "portfolio_account_fact/2")
        self.assertEqual(fact["provider"], "KIS_PAPER_ACCOUNT")
        self.assertEqual(fact["accountScope"], "KOREA")
        self.assertEqual(fact["verificationStatus"], "PROPOSED_UNRATIFIED")
        self.assertEqual(fact["providerAuthorityStatus"], "PROPOSED_UNRATIFIED")
        self.assertEqual(
            fact["factUsabilityStatus"],
            "NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED",
        )
        self.assertEqual(fact["accountIdentityHash"], ACCOUNT_IDENTITY_HASH)
        self.assertEqual(fact["orderEligibilityStatus"], PS2.ORDER_ELIGIBILITY_NOT_APPLICABLE)
        self.assertEqual(fact["authority"], PS2.AUTHORITY_ALL_FALSE)
        self.assertEqual(fact["capturedAt"], T0)
        self.assertEqual(fact["availableAt"], T0)
        self.assertEqual(fact["positions"][0]["source_identity_lineage"]["source_pairs"], [
            {"source_name": "kis_paper_domestic_balance", "source_asset_id": "005930"}
        ])
        # A mechanically accepted tuple is explicitly not broker authority.
        self.assertNotEqual(fact["verificationStatus"], "BROKER_VERIFIED")
        PS2.validate_provider_account_fact_v2(fact, decision_at=T0)  # round-trips cleanly

    def test_unregistered_provider_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "PROVIDER_IMPLEMENTATION_NOT_AVAILABLE"):
            _build_fact(provider="SOME_OTHER_BROKER")

    def test_account_scope_outside_implemented_shape_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "PROVIDER_ACCOUNT_SCOPE_MISMATCH"):
            _build_fact(account_scope="JAPAN")

    def test_registered_provider_cannot_be_relabelled_to_another_ratified_scope(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "PROVIDER_ACCOUNT_SCOPE_MISMATCH"):
            _build_fact(account_scope="CRYPTO")

    def test_registered_provider_has_one_currency_and_read_only_eligibility(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "PROVIDER_CURRENCY_MISMATCH"):
            _build_fact(currency="USD")
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "ORDER_ELIGIBILITY_STATUS_NOT_READ_ONLY"):
            _build_fact(order_eligibility_status="ORDER_APPROVED")

    def test_malformed_account_identity_hash_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "ACCOUNT_IDENTITY_HASH_INVALID"):
            _build_fact(account_identity_hash="not-a-hash")

    def test_future_dated_captured_at_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "FUTURE_DATED_VALUE_REJECTED"):
            _build_fact(captured_at=T_LATER, available_at=T_LATER, decision_at=T0)

    # -- capturedAt / availableAt / decisionAt timing chain (P0-2B-2) --------

    def test_available_before_captured_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "TIMING_INVARIANT_VIOLATED"):
            _build_fact(captured_at=T_LATER, available_at=T0, decision_at=T_LATER)

    def test_available_after_decision_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "AVAILABLE_AFTER_DECISION_REJECTED"):
            _build_fact(captured_at=T0, available_at=T_LATER, decision_at=T0)

    def test_available_at_is_preserved_on_the_fact(self):
        fact = _build_fact(captured_at=T0, available_at=T_LATER, decision_at=T_LATER)
        self.assertEqual(fact["availableAt"], T_LATER)

    def test_negative_cash_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "NEGATIVE_VALUE_REJECTED"):
            _build_fact(cash=-1.0)

    def test_position_source_identity_is_exactly_the_kis_pdno(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "POSITION_SYMBOL_SOURCE_ASSET_ID_MISMATCH"):
            _build_fact(positions=_positions(source_asset_id="000660"))
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "POSITION_SOURCE_NAME_PROVIDER_MISMATCH"):
            _build_fact(positions=_positions(source_name="caller_alias"))

    def test_kis_holding_quantity_is_a_nonnegative_integer(self):
        for invalid in (1.5, 10.0, True):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "NONNEGATIVE_INTEGER_REQUIRED"):
                    _build_fact(positions=_positions(quantity=invalid))

    def test_staleness_is_independently_derived_not_trusted(self):
        fact = _build_fact(captured_at=T0, decision_at=T_MUCH_LATER)
        self.assertEqual(fact["stalenessStatus"], "STALE")
        fact_fresh = _build_fact(captured_at=T0, decision_at=T_LATER)
        self.assertEqual(fact_fresh["stalenessStatus"], "FRESH")

    def test_nav_mismatch_is_flagged(self):
        fact = _build_fact(equity=99_999_999.0)
        self.assertEqual(fact["navReconciliationStatus"], "MISMATCH_FLAGGED")

    def test_zero_position_account_is_valid(self):
        fact = _build_fact(positions=[])
        self.assertEqual(fact["positions"], [])
        self.assertEqual(fact["positionCount"], 0)


class ValidateProviderAccountFactV2Tests(unittest.TestCase):
    def test_tampered_diagnostic_is_caught_even_with_a_freshly_regenerated_hash(self):
        fact = _build_fact()
        tampered = copy.deepcopy(fact)
        tampered["positionCount"] = 999
        tampered["factSha256"] = PS2.payload_sha256(
            {k: v for k, v in tampered.items() if k != "factSha256"}
        )
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "FACT_DIAGNOSTIC_TAMPER_DETECTED"):
            PS2.validate_provider_account_fact_v2(tampered, decision_at=T0)

    def test_hash_mismatch_is_caught(self):
        fact = _build_fact()
        fact["factSha256"] = "0" * 64
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "FACT_HASH_MISMATCH"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T0)

    def test_rehashed_provider_scope_currency_and_authority_tamper_are_rejected(self):
        cases = [
            ("accountScope", "CRYPTO", "PROVIDER_ACCOUNT_SCOPE_MISMATCH"),
            ("currency", "USD", "PROVIDER_CURRENCY_MISMATCH"),
            ("verificationStatus", "BROKER_VERIFIED", "VERIFICATION_STATUS_INVALID"),
            ("providerAuthorityStatus", "RATIFIED", "PROVIDER_AUTHORITY_STATUS_INVALID"),
            ("factUsabilityStatus", "COMPUTABLE", "FACT_USABILITY_STATUS_INVALID"),
            ("orderEligibilityStatus", "ORDER_APPROVED", "ORDER_ELIGIBILITY_STATUS_NOT_READ_ONLY"),
        ]
        for field, value, code in cases:
            with self.subTest(field=field):
                fact = _rehash({**_build_fact(), field: value})
                with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, code):
                    PS2.validate_provider_account_fact_v2(fact, decision_at=T0)
        authority_tamper = copy.deepcopy(_build_fact())
        authority_tamper["authority"]["order_authorized"] = True
        _rehash(authority_tamper)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "ACCOUNT_FACT_AUTHORITY_INVALID"):
            PS2.validate_provider_account_fact_v2(authority_tamper, decision_at=T0)

    def test_rehashed_position_semantics_are_revalidated_not_only_hashed(self):
        cases = [
            ("quantity", "10", "NON_NUMERIC_VALUE"),
            ("quantity", 10.0, "NONNEGATIVE_INTEGER_REQUIRED"),
            ("market_value", float("nan"), "NON_FINITE_VALUE"),
            ("currency", "USD", "POSITION_CURRENCY_PROVIDER_MISMATCH"),
        ]
        for field, value, code in cases:
            with self.subTest(field=field):
                fact = copy.deepcopy(_build_fact())
                fact["positions"][0][field] = value
                _rehash(fact)
                with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, code):
                    PS2.validate_provider_account_fact_v2(fact, decision_at=T0)

    def test_rehashed_python_bool_numeric_aliases_are_rejected(self):
        position_count = copy.deepcopy(_build_fact())
        position_count["positionCount"] = True  # True == 1 in Python
        _rehash(position_count)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "FACT_POSITION_COUNT_INVALID"):
            PS2.validate_provider_account_fact_v2(position_count, decision_at=T0)

        mismatch = copy.deepcopy(_build_fact())
        mismatch["navReconciliationMismatchPct"] = False  # False == 0.0
        _rehash(mismatch)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "NON_NUMERIC_VALUE"):
            PS2.validate_provider_account_fact_v2(mismatch, decision_at=T0)

        authority = copy.deepcopy(_build_fact())
        authority["authority"] = {
            key: int(value) for key, value in authority["authority"].items()
        }
        _rehash(authority)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "ACCOUNT_FACT_AUTHORITY_INVALID"):
            PS2.validate_provider_account_fact_v2(authority, decision_at=T0)

    def test_rehashed_source_pair_alias_is_rejected(self):
        fact = copy.deepcopy(_build_fact())
        fact["positions"][0]["source_identity_lineage"]["source_pairs"][0]["source_name"] = "caller_alias"
        _rehash(fact)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "POSITION_SOURCE_NAME_PROVIDER_MISMATCH"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T0)

    def test_canonical_identity_claim_smuggled_onto_a_position_is_rejected(self):
        fact = _build_fact()
        fact["positions"][0]["canonical_instrument_id"] = "KRX:005930:COMMON"
        fact["factSha256"] = PS2.payload_sha256({k: v for k, v in fact.items() if k != "factSha256"})
        with self.assertRaisesRegex(
            PS2.PortfolioAccountFactV2Error, "POSITION_FIELDS_INVALID|POSITION_CANONICAL_IDENTITY_CLAIM_FORBIDDEN"
        ):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T0)

    def test_validator_rejects_a_future_decision_relative_to_captured_at_being_smuggled_past(self):
        fact = _build_fact(captured_at=T0, decision_at=T0)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "FUTURE_DATED_VALUE_REJECTED"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T_EARLIER)

    def test_validator_recomputes_available_at_chain_not_trusts_it(self):
        # Tamper availableAt to be after the caller-supplied decision_at,
        # post-hoc, with a freshly regenerated hash -- the validator must
        # still catch it by re-deriving the chain against the
        # *caller-supplied* decision_at, never by trusting anything
        # stored on the fact.
        fact = _build_fact(captured_at=T0, available_at=T0, decision_at=T_LATER)
        fact["availableAt"] = T_MUCH_LATER
        _rehash(fact)
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "AVAILABLE_AFTER_DECISION_REJECTED"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T_LATER)

    def test_validator_rejects_available_at_before_captured_at_even_rehashed(self):
        fact = _rehash({**_build_fact(captured_at=T0, available_at=T0, decision_at=T_LATER), "capturedAt": T_LATER})
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "TIMING_INVARIANT_VIOLATED"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T_LATER)

    def test_wrong_schema_version_is_rejected(self):
        fact = _build_fact()
        fact["contractVersion"] = "portfolio_account_fact/1"
        fact["factSha256"] = PS2.payload_sha256({k: v for k, v in fact.items() if k != "factSha256"})
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "SCHEMA_VERSION_INVALID"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T0)


if __name__ == "__main__":
    unittest.main()
