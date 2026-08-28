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


def _build_fact(*, decision_at=T0, captured_at=T0, positions=None, **overrides):
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
        decision_at=decision_at,
    )
    kwargs.update(overrides)
    return PS2.build_provider_account_fact_v2(**kwargs)


class ContractShapeTests(unittest.TestCase):
    def test_contract_config_declares_v2_and_kis_provider(self):
        contract = json.loads((ROOT / "config" / "portfolio_risk_input_contract_v2.json").read_text())
        self.assertEqual(contract["contract_version"], "portfolio_account_fact/2")
        self.assertIn("KIS_PAPER_ACCOUNT", contract["registered_providers"])
        self.assertEqual(
            contract["registered_providers"]["KIS_PAPER_ACCOUNT"]["required_verification_status"],
            "BROKER_VERIFIED",
        )
        self.assertFalse(contract["authority"]["order_authorized"])
        self.assertFalse(contract["authority"]["trading_authorized"])

    def test_v1_module_is_completely_unaffected(self):
        # v2 must never monkeypatch, extend, or otherwise mutate v1's
        # module-level state.
        self.assertEqual(PS.SCHEMA_VERSION, "portfolio_risk_input/1")
        self.assertIn("ALPACA_PAPER_ACCOUNT", PS.CANONICAL_ACCOUNT_SCOPE)


class BuildProviderAccountFactV2Tests(unittest.TestCase):
    def test_real_fact_has_explicit_provider_and_scope_fields(self):
        fact = _build_fact()
        self.assertEqual(fact["contractVersion"], "portfolio_account_fact/2")
        self.assertEqual(fact["provider"], "KIS_PAPER_ACCOUNT")
        self.assertEqual(fact["accountScope"], "KOREA")
        self.assertEqual(fact["verificationStatus"], "BROKER_VERIFIED")
        self.assertEqual(fact["accountIdentityHash"], ACCOUNT_IDENTITY_HASH)
        self.assertEqual(fact["positions"][0]["source_identity_lineage"]["source_pairs"], [
            {"source_name": "kis_paper_domestic_balance", "source_asset_id": "005930"}
        ])
        # Reuses v1's OWN already-ratified account-scope registry.
        self.assertIn(fact["accountScope"], PS.CANONICAL_ACCOUNT_SCOPE)
        PS2.validate_provider_account_fact_v2(fact, decision_at=T0)  # round-trips cleanly

    def test_unregistered_provider_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "PROVIDER_NOT_REGISTERED"):
            _build_fact(provider="SOME_OTHER_BROKER")

    def test_account_scope_not_in_canonical_registry_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "ACCOUNT_SCOPE_NOT_RATIFIED"):
            _build_fact(account_scope="JAPAN")

    def test_malformed_account_identity_hash_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "ACCOUNT_IDENTITY_HASH_INVALID"):
            _build_fact(account_identity_hash="not-a-hash")

    def test_future_dated_captured_at_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "FUTURE_DATED_VALUE_REJECTED"):
            _build_fact(captured_at=T_LATER, decision_at=T0)

    def test_negative_cash_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "NEGATIVE_NAV_OR_CASH_REJECTED"):
            _build_fact(cash=-1.0)

    def test_duplicate_source_asset_id_is_rejected(self):
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "DUPLICATE_POSITION_SOURCE_ASSET_ID"):
            _build_fact(positions=[
                {"symbol": "005930", "quantity": 1, "market_value": 70000.0, "unrealized_pl": 0.0,
                 "source_name": "kis_paper_domestic_balance", "source_asset_id": "005930"},
                {"symbol": "005930A", "quantity": 1, "market_value": 70000.0, "unrealized_pl": 0.0,
                 "source_name": "kis_paper_domestic_balance", "source_asset_id": "005930"},
            ])

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

    def test_wrong_schema_version_is_rejected(self):
        fact = _build_fact()
        fact["contractVersion"] = "portfolio_account_fact/1"
        fact["factSha256"] = PS2.payload_sha256({k: v for k, v in fact.items() if k != "factSha256"})
        with self.assertRaisesRegex(PS2.PortfolioAccountFactV2Error, "SCHEMA_VERSION_INVALID"):
            PS2.validate_provider_account_fact_v2(fact, decision_at=T0)


if __name__ == "__main__":
    unittest.main()
