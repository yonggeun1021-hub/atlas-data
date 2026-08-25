#!/usr/bin/env python3
"""Real-evidence regression for the first mechanical identity authority rows.

This is deliberately a narrow pilot.  It proves identity/scope facts for
BTC, Samsung Electronics common stock, and SK hynix common stock.  It does
not make any security investable or eligible for entry, sizing, or trading.
"""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci  # noqa: E402


DECISION_DATE_AFTER_FIRST_SEEN = "2026-08-26"

EXPECTED_RESOLUTIONS = (
    ("kraken_spot_ohlc", "BTC/USD", "BTC", "CRYPTO:BTC", "KRAKEN:BTC-USD:SPOT"),
    ("krx_open_api_stock_daily", "005930", "KOREA", "KRX:005930:COMMON", "XKRX:005930"),
    ("krx_open_api_stock_daily", "000660", "KOREA", "KRX:000660:COMMON", "XKRX:000660"),
)


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class IdentityAuthorityPilotTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = ci.load_authority()
        cls.scope_authority = ci.load_scope_authority()

    def test_pilot_contains_only_the_three_approved_instruments(self):
        self.assertEqual(len(self.authority["issuers"]), 3)
        self.assertEqual(len(self.authority["instruments"]), 3)
        self.assertEqual(len(self.authority["listings"]), 3)
        self.assertEqual(len(self.authority["source_aliases"]), 3)
        self.assertEqual(
            {r["canonical_instrument_id"] for r in self.authority["instruments"]},
            {"CRYPTO:BTC", "KRX:005930:COMMON", "KRX:000660:COMMON"},
        )

    def test_scope_pilot_contains_only_current_clock_markets(self):
        self.assertEqual(
            {(r["market"], r["account_scope"]) for r in self.scope_authority["edges"]},
            {("BTC", "CRYPTO"), ("CRYPTO", "CRYPTO"), ("KOREA", "KOREA")},
        )

    def test_every_row_is_structurally_valid_and_ratified(self):
        for layer, key in (
            (ci.LAYER_ISSUER, "issuers"),
            (ci.LAYER_INSTRUMENT, "instruments"),
            (ci.LAYER_LISTING, "listings"),
            (ci.LAYER_SOURCE_ALIAS, "source_aliases"),
        ):
            for row in self.authority[key]:
                ci.validate_authority_row(row, layer)
                self.assertEqual(row["approval_status"], "RATIFIED")
                self.assertEqual(row["effective_from"], "2026-08-25")
                self.assertIsNone(row["effective_to"])
        for row in self.scope_authority["edges"]:
            ci.validate_authority_row(row, ci.LAYER_MARKET_ACCOUNT_SCOPE)
            self.assertEqual(row["approval_status"], "RATIFIED")

    def test_every_approval_file_and_underlying_source_hash_is_real(self):
        all_rows = (
            self.authority["issuers"]
            + self.authority["instruments"]
            + self.authority["listings"]
            + self.authority["source_aliases"]
            + self.scope_authority["edges"]
        )
        self.assertEqual(len(all_rows), 15)
        for row in all_rows:
            approval_path = ROOT / row["approval_evidence_ref"]
            self.assertTrue(approval_path.is_file(), row["rule_id"])
            self.assertEqual(sha256_file(approval_path), row["approval_evidence_sha256"])
            approval = json.loads(approval_path.read_text())
            self.assertEqual(approval["boundary"], "MECHANICAL_IDENTITY_OR_SCOPE_ONLY_NO_INVESTMENT_OR_TRADING_AUTHORITY")
            for source in approval["source_evidence"]:
                source_path = ROOT / source["path"]
                self.assertTrue(source_path.is_file(), source)
                self.assertEqual(sha256_file(source_path), source["sha256"])

    def test_btc_claim_matches_existing_committed_contracts(self):
        price = json.loads((ROOT / "config/btc_price_contract.json").read_text())
        aliases = json.loads((ROOT / "config/crypto_asset_identity_exceptions.json").read_text())
        taxonomy = json.loads((ROOT / "config/crypto_breadth_exclusion_taxonomy.json").read_text())
        self.assertEqual(price["request_pair"], "XBTUSD")
        self.assertEqual(price["response_pair"], "BTC/USD")
        record = aliases["records"][0]
        self.assertEqual(record["canonical_asset_id"], "BTC")
        self.assertEqual(set(record["aliases"]), {"XBT", "XXBT"})
        btc = next(r for r in taxonomy["records"] if r["canonical_asset_id"] == "BTC")
        self.assertEqual(btc["category"], "eligible_crypto")

    def test_korea_claims_match_krx_dart_and_identity_contract(self):
        contract = json.loads((ROOT / "config/krx_global_universe_contract.json").read_text())
        universe = {r["code"]: r["name"] for r in json.loads((ROOT / "config/universe.json").read_text())["kr"]}
        corp_map = json.loads((ROOT / "config/corp_map.json").read_text())
        krx = json.loads((ROOT / "data/2026-08-24/krx.json").read_text())
        dart = json.loads((ROOT / "data/2026-08-24/dart.json").read_text())
        self.assertEqual(contract["source_contract"]["identity_semantics"], "KRX_ISU_CD_exact_no_name_or_ticker_inference")
        self.assertEqual(contract["exchange_id"], "XKRX")
        for code, name, corp_code in (
            ("005930", "삼성전자", "00126380"),
            ("000660", "SK하이닉스", "00164779"),
        ):
            self.assertEqual(universe[code], name)
            self.assertEqual(corp_map[code], corp_code)
            self.assertEqual(krx["stocks"][code]["name"], name)
            self.assertEqual(dart["stocks"][code]["corp_code"], corp_code)

    def test_scope_claims_match_declared_clock_and_portfolio_vocabularies(self):
        clock = json.loads((ROOT / "config/dynamic_clock_policy.json").read_text())
        portfolio = json.loads((ROOT / "config/portfolio_risk_input_contract.json").read_text())
        self.assertTrue({"BTC", "CRYPTO", "KOREA"}.issubset(clock["market_calendars"]))
        self.assertEqual(set(portfolio["manual_markets"]["allowed"]), {"KOREA", "CRYPTO"})

    def test_real_source_resolution_succeeds_only_after_verified_first_seen(self):
        for source_name, source_asset_id, market, instrument_id, listing_id in EXPECTED_RESOLUTIONS:
            too_early = ci.resolve_instrument_identity(
                source_name, source_asset_id, market, "2026-08-25", self.authority
            )
            self.assertNotEqual(too_early["status"], ci.RESOLVED)
            resolved = ci.resolve_instrument_identity(
                source_name, source_asset_id, market,
                DECISION_DATE_AFTER_FIRST_SEEN, self.authority,
            )
            self.assertEqual(resolved["status"], ci.RESOLVED, resolved)
            self.assertEqual(resolved["canonical_instrument_id"], instrument_id)
            self.assertEqual(resolved["listing_id"], listing_id)
            self.assertTrue(all(v is False for v in resolved["authority"].values()))

    def test_scope_resolution_succeeds_only_for_explicit_edges(self):
        for market, expected_scope in (("BTC", "CRYPTO"), ("CRYPTO", "CRYPTO"), ("KOREA", "KOREA")):
            result = ci.resolve_account_scope(
                market, DECISION_DATE_AFTER_FIRST_SEEN, self.scope_authority
            )
            self.assertEqual(result["status"], ci.RESOLVED, result)
            self.assertEqual(result["account_scope"], expected_scope)
            self.assertTrue(all(v is False for v in result["authority"].values()))
        missing = ci.resolve_account_scope(
            "US", DECISION_DATE_AFTER_FIRST_SEEN, self.scope_authority
        )
        self.assertEqual(missing["status"], ci.NOT_COMPUTABLE_SCOPE_MAP_MISSING)

    def test_graph_relationships_are_unique_and_layer_correct(self):
        issuers = {r["canonical_issuer_id"] for r in self.authority["issuers"]}
        instruments = {r["canonical_instrument_id"]: r for r in self.authority["instruments"]}
        listings = {r["listing_id"]: r for r in self.authority["listings"]}
        self.assertEqual(len(issuers), 3)
        self.assertEqual(len(instruments), 3)
        self.assertEqual(len(listings), 3)
        for instrument in instruments.values():
            self.assertIn(instrument["canonical_issuer_id"], issuers)
        for listing in listings.values():
            self.assertIn(listing["canonical_instrument_id"], instruments)
        for alias in self.authority["source_aliases"]:
            self.assertIn(alias["listing_id"], listings)

    def test_no_row_or_approval_packet_grants_investment_authority(self):
        forbidden_true = {
            "investable", "entry_eligible", "buy_authorized", "order_authorized",
            "production_authorized", "trading_authorized", "stage_authorized",
        }
        for path in (ROOT / "evidence/identity/approvals/2026-08-25").glob("*.json"):
            text = path.read_text()
            for field in forbidden_true:
                self.assertNotIn(f'"{field}": true', text)


if __name__ == "__main__":
    unittest.main()
