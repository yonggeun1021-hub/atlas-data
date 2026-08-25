#!/usr/bin/env python3
"""Portfolio position provider-identity lineage counterexamples.

This suite proves transport and fail-closed validation only.  It deliberately
does not resolve provider IDs to canonical instruments, merge aliases, open a
position-size gate, or authorize an order.
"""
from __future__ import annotations

import copy
import importlib.util
import inspect
import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load(name: str, rel: str):
    path = ROOT / rel
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PS = _load("portfolio_risk.portfolio_snapshot.lineage_test", "portfolio_risk/portfolio_snapshot.py")
T0 = "2026-08-25T07:00:00Z"
ASSET_ID = "b0b6dd9d-8b9b-48a9-ba46-b9d54906e415"


def _account() -> dict:
    return {
        "account_number": "PA-LINEAGE-TEST",
        "currency": "USD",
        "equity": "10000.00",
        "cash": "5000.00",
        "buying_power": "10000.00",
        "status": "ACTIVE",
        "trading_blocked": False,
        "account_blocked": False,
        "trade_suspended_by_user": False,
    }


def _position(**overrides) -> dict:
    row = {
        "asset_id": ASSET_ID,
        "symbol": "AAPL",
        "qty": "10",
        "market_value": "5000.00",
        "unrealized_pl": "100.00",
    }
    row.update(overrides)
    return row


def _packet() -> dict:
    fact = PS.build_alpaca_paper_account_fact(
        _account(), [_position()], captured_at=T0, decision_at=T0,
    )
    return PS.assemble_snapshot(
        account_facts=[fact], fx_rates={}, captured_at=T0, available_at=T0, decision_at=T0,
    )


def _resign(packet: dict) -> dict:
    packet["packet_sha256"] = PS.payload_sha256(
        {key: value for key, value in packet.items() if key != "packet_sha256"}
    )
    return packet


class AlpacaProviderLineageTests(unittest.TestCase):
    def test_exact_provider_asset_id_is_transported_not_display_symbol(self):
        packet = _packet()
        position = packet["portfolio_facts"]["accounts"][0]["positions"][0]
        self.assertEqual(position["symbol"], "AAPL")
        self.assertEqual(position["source_identity_lineage"], {
            "status": "AVAILABLE",
            "source_pairs": [{
                "source_name": "alpaca_paper_positions",
                "source_asset_id": ASSET_ID,
            }],
        })
        self.assertNotEqual(ASSET_ID, position["symbol"])
        PS.validate_snapshot(packet)

    def test_provider_asset_id_is_required(self):
        row = _position()
        del row["asset_id"]
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "ALPACA_POSITION_ASSET_ID_MISSING"):
            PS.build_alpaca_paper_account_fact(
                _account(), [row],
                captured_at=T0, decision_at=T0,
            )

    def test_blank_provider_asset_id_is_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "POSITION_SOURCE_ASSET_ID_INVALID"):
            PS.build_alpaca_paper_account_fact(
                _account(), [_position(asset_id="  ")], captured_at=T0, decision_at=T0,
            )

    def test_symbol_format_is_preserved_without_normalization(self):
        fact = PS.build_alpaca_paper_account_fact(
            _account(), [_position(symbol="BRK.B", asset_id="provider-uuid-brk-b")],
            captured_at=T0, decision_at=T0,
        )
        position = fact["positions"][0]
        self.assertEqual(position["symbol"], "BRK.B")
        self.assertEqual(
            position["source_identity_lineage"]["source_pairs"][0]["source_asset_id"],
            "provider-uuid-brk-b",
        )


class ManualLineageFailClosedTests(unittest.TestCase):
    def _fact(self, positions: list[dict]) -> dict:
        return PS.build_manual_account_fact(
            market="CRYPTO", currency="USD", cash=100.0, positions=positions,
            captured_at=T0, decision_at=T0,
        )

    def test_manual_position_without_pair_stays_not_computable(self):
        fact = self._fact([{"symbol": "BTC", "qty": 1, "market_value": 10}])
        self.assertEqual(fact["positions"][0]["source_identity_lineage"], {
            "status": "NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING",
            "source_pairs": [],
        })

    def test_manual_complete_pair_is_retained_but_never_available(self):
        fact = self._fact([{
            "symbol": "BTC", "qty": 1, "market_value": 10,
            "source_name": "manual_exchange_export", "source_asset_id": "XBT",
        }])
        lineage = fact["positions"][0]["source_identity_lineage"]
        self.assertEqual(lineage["status"], "NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING")
        self.assertEqual(lineage["source_pairs"], [{
            "source_name": "manual_exchange_export", "source_asset_id": "XBT",
        }])

    def test_manual_partial_pair_is_rejected(self):
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "MANUAL_POSITION_SOURCE_IDENTITY_PARTIAL"):
            self._fact([{
                "symbol": "BTC", "qty": 1, "market_value": 10,
                "source_name": "manual_exchange_export",
            }])


class RehashedTamperTests(unittest.TestCase):
    def _position(self, packet: dict) -> dict:
        return packet["portfolio_facts"]["accounts"][0]["positions"][0]

    def test_deleted_lineage_rejected_after_rehash(self):
        tampered = copy.deepcopy(_packet())
        del self._position(tampered)["source_identity_lineage"]
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "LINEAGE_SCHEMA_INVALID"):
            PS.validate_snapshot(_resign(tampered))

    def test_provider_name_tamper_rejected_after_rehash(self):
        tampered = copy.deepcopy(_packet())
        self._position(tampered)["source_identity_lineage"]["source_pairs"][0]["source_name"] = "made_up"
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "ALPACA_POSITION_SOURCE_IDENTITY_INVALID"):
            PS.validate_snapshot(_resign(tampered))

    def test_available_status_tamper_rejected_for_manual_after_rehash(self):
        fact = PS.build_manual_account_fact(
            market="CRYPTO", currency="USD", cash=100.0,
            positions=[{"symbol": "BTC", "qty": 1, "market_value": 10}],
            captured_at=T0, decision_at=T0,
        )
        packet = PS.assemble_snapshot(
            account_facts=[fact], fx_rates={}, captured_at=T0, available_at=T0, decision_at=T0,
        )
        tampered = copy.deepcopy(packet)
        self._position(tampered)["source_identity_lineage"]["status"] = "AVAILABLE"
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "MANUAL_POSITION_SOURCE_IDENTITY_CANNOT_BE_AVAILABLE"):
            PS.validate_snapshot(_resign(tampered))

    def test_account_source_cannot_keep_broker_verified_after_manual_relabel(self):
        tampered = copy.deepcopy(_packet())
        account = tampered["portfolio_facts"]["accounts"][0]
        account["source"] = "MANUAL_SNAPSHOT:US"
        account["positions"][0]["source_identity_lineage"] = {
            "status": "NOT_COMPUTABLE_SOURCE_IDENTITY_LINEAGE_MISSING",
            "source_pairs": [],
        }
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "MANUAL_ACCOUNT_VERIFICATION_STATUS_INVALID"):
            PS.validate_snapshot(_resign(tampered))

    def test_forged_canonical_instrument_claim_is_rejected_after_rehash(self):
        tampered = copy.deepcopy(_packet())
        self._position(tampered)["canonical_instrument_id"] = "US:AAPL:COMMON"
        with self.assertRaisesRegex(PS.PortfolioSnapshotError, "POSITION_CANONICAL_IDENTITY_CLAIM_FORBIDDEN"):
            PS.validate_snapshot(_resign(tampered))


class AuthorityAndAggregationBoundaryTests(unittest.TestCase):
    def test_raw_ticker_exposure_is_explicitly_not_canonical(self):
        packet = _packet()
        risk = packet["risk_capacity_inputs"]
        self.assertEqual(
            risk["exposure_identity_basis"],
            "RAW_PROVIDER_SYMBOL_DIAGNOSTIC_NOT_CANONICAL_INSTRUMENT",
        )
        self.assertEqual(risk["exposure_by_ticker"], [{"symbol": "AAPL", "market_value": 5000.0}])

    def test_no_identity_resolver_or_alias_mapping_was_added(self):
        source = inspect.getsource(PS)
        self.assertNotIn("canonical_identity", source)
        self.assertNotIn("resolve_instrument_identity", source)
        self.assertNotIn("XBT", source)
        self.assertNotIn("XXBT", source)

    def test_all_authority_remains_false_and_policy_unratified(self):
        packet = _packet()
        self.assertEqual(packet["authority"], PS.AUTHORITY_ALL_FALSE)
        self.assertTrue(all(value is False for key, value in packet["authority"].items() if key != "review_only"))
        self.assertEqual(packet["risk_policy"], PS.RISK_POLICY_UNRATIFIED)
        self.assertEqual(packet["position_size"], PS.POSITION_SIZE_UNRATIFIED)

    def test_contract_v2_describes_transport_not_resolution(self):
        contract = json.loads((ROOT / "config" / "portfolio_risk_input_contract.json").read_text())
        self.assertEqual(contract["contract_version"], "portfolio_risk_input/2")
        lineage = contract["position_source_identity_lineage"]
        self.assertEqual(lineage["alpaca"]["source_asset_id_basis"], "EXACT_PROVIDER_ASSET_ID_FROM_GET_V2_POSITIONS")
        self.assertIn("does not mean canonical instrument identity is resolved", lineage["alpaca"]["note"])


if __name__ == "__main__":
    unittest.main()
