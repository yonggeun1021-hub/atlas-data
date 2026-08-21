"""P3-01 Global Security / Asset Master contract regression."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "global_asset_master.py"
SPEC = importlib.util.spec_from_file_location("global_asset_master", MODULE_PATH)
GAM = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GAM)


def source(source_id: str, suffix: str = "a") -> dict:
    return {
        "source_id": source_id,
        "source_url": f"https://example.invalid/{source_id}/{suffix}",
        "source_sha256": hashlib.sha256(suffix.encode()).hexdigest(),
        "available_at": "2026-08-19T00:00:00Z",
        "retrieved_at_utc": "2026-08-19T00:05:00Z",
    }


def interval_source(source_id: str, suffix: str) -> dict:
    return source(source_id, suffix)


def record(
    asset_id: str,
    market: str,
    asset_class: str,
    symbol: str,
    exchange_id: str,
    currency: str,
    source_id: str,
) -> dict:
    namespace = {
        "US": "NASDAQ_SYMBOL",
        "KOREA": "KRX_CODE",
        "CRYPTO": "KRAKEN_ASSET_ID",
    }[market]
    return {
        "asset_id": asset_id,
        "market": market,
        "asset_class": asset_class,
        "display_name": {
            "US": "Synthetic US Equity",
            "KOREA": "Synthetic Korea Equity",
            "CRYPTO": "Synthetic Crypto Asset",
        }[market],
        "primary_symbol": symbol,
        "exchange_id": exchange_id,
        "quote_currency": currency,
        "identifiers": [
            {"namespace": namespace, "value": symbol},
            {"namespace": "ATLAS_SOURCE_KEY", "value": f"{source_id}:{symbol}"},
        ],
        "aliases": [
            {
                "alias_type": "SYMBOL",
                "value": symbol,
                "exchange_id": exchange_id,
                "valid_from": "2020-01-01",
                "valid_to": None,
                "source_identity": interval_source(source_id, f"alias-{asset_id}"),
            }
        ],
        "memberships": [
            {
                "membership_type": "MARKET",
                "membership_id": market,
                "valid_from": "2020-01-01",
                "valid_to": None,
                "source_identity": interval_source(source_id, f"market-{asset_id}"),
            }
        ],
        "source_identity": source(source_id, f"record-{asset_id}"),
    }


def sample_input() -> dict:
    us = record(
        "US:XNAS:MSFT",
        "US",
        "EQUITY",
        "MSFT",
        "XNAS",
        "USD",
        "nasdaq_trader_symbol_directory",
    )
    us["memberships"].extend(
        [
            {
                "membership_type": "THEME",
                "membership_id": "SYNTHETIC_THEME",
                "valid_from": "2025-01-01",
                "valid_to": None,
                "source_identity": interval_source(
                    "nasdaq_trader_symbol_directory", "theme-us"
                ),
            },
            {
                "membership_type": "UNIVERSE",
                "membership_id": "SYNTHETIC_US_RESEARCH",
                "valid_from": "2025-01-01",
                "valid_to": None,
                "source_identity": interval_source(
                    "nasdaq_trader_symbol_directory", "universe-us"
                ),
            },
        ]
    )
    korea = record(
        "KR:XKRX:005930",
        "KOREA",
        "EQUITY",
        "005930",
        "XKRX",
        "KRW",
        "krx_open_api_stock_daily",
    )
    crypto = record(
        "CRYPTO:KRAKEN:BTC",
        "CRYPTO",
        "CRYPTO_ASSET",
        "BTC",
        "KRAKEN",
        "USD",
        "kraken_public_api",
    )
    crypto["aliases"].insert(
        0,
        {
            "alias_type": "SYMBOL",
            "value": "XBT",
            "exchange_id": "KRAKEN",
            "valid_from": "2010-01-01",
            "valid_to": "2020-01-01",
            "source_identity": interval_source("kraken_public_api", "old-btc-alias"),
        },
    )
    return {
        "schema_version": "global_asset_master_input/1",
        "master_id": "ATLAS_RESEARCH_ASSETS",
        "as_of_date": "2026-08-20",
        "records": [us, korea, crypto],
    }


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = GAM.payload_sha256(value)
    return value


class GlobalAssetMasterTests(unittest.TestCase):
    def test_cross_market_master_and_authority_boundary(self):
        packet = GAM.build_master(sample_input())
        self.assertEqual(packet["status"], "IDENTITY_MASTER_VALIDATED")
        self.assertEqual(packet["record_count"], 3)
        self.assertEqual(
            [row["asset_id"] for row in packet["records"]],
            ["CRYPTO:KRAKEN:BTC", "KR:XKRX:005930", "US:XNAS:MSFT"],
        )
        self.assertEqual(
            {row["market"] for row in packet["records"]}, {"US", "KOREA", "CRYPTO"}
        )
        for row in packet["records"]:
            self.assertFalse(row["universe_approved"])
            self.assertFalse(row["investable_eligible"])
            self.assertIsNone(row["stage_transition"])
        authority = packet["authority"]
        self.assertTrue(authority["identity_recording_only"])
        self.assertFalse(authority["universe_approval_authorized"])
        self.assertFalse(authority["investability_authorized"])
        self.assertFalse(authority["production_authorized"])
        self.assertFalse(authority["trading_authorized"])

    def test_theme_and_universe_memberships_are_explicit_only(self):
        packet = GAM.build_master(sample_input())
        by_id = {row["asset_id"]: row for row in packet["records"]}
        us_types = {
            row["membership_type"] for row in by_id["US:XNAS:MSFT"]["active_memberships"]
        }
        korea_types = {
            row["membership_type"]
            for row in by_id["KR:XKRX:005930"]["active_memberships"]
        }
        self.assertEqual(us_types, {"MARKET", "THEME", "UNIVERSE"})
        self.assertEqual(korea_types, {"MARKET"})
        self.assertEqual(packet["policy_status"]["membership_selection"], "EXPLICIT_ONLY")
        self.assertEqual(packet["policy_status"]["theme_taxonomy"], "UNRATIFIED")

    def test_official_us_preferred_symbol_character_is_preserved(self):
        value = sample_input()
        us = value["records"][0]
        us["asset_id"] = "US:NASDAQDIR:PREFERRED"
        us["primary_symbol"] = "ABR$D"
        us["aliases"][0]["value"] = "ABR$D"
        us["identifiers"][0]["value"] = "ABR$D"
        packet = GAM.build_master(value)
        record = next(row for row in packet["records"] if row["market"] == "US")
        self.assertEqual(record["primary_symbol"], "ABR$D")
        self.assertEqual(record["active_aliases"][0]["value"], "ABR$D")

    def test_effective_dated_alias_preserves_history(self):
        packet = GAM.build_master(sample_input())
        btc = next(row for row in packet["records"] if row["market"] == "CRYPTO")
        self.assertEqual([row["value"] for row in btc["aliases"]], ["BTC", "XBT"])
        self.assertEqual([row["value"] for row in btc["active_aliases"]], ["BTC"])
        self.assertEqual(GAM.load_contract()["effective_interval"], "[valid_from, valid_to)")

    def test_output_is_order_independent_and_digest_bound(self):
        value = sample_input()
        first = GAM.build_master(value)
        value["records"].reverse()
        for row in value["records"]:
            row["identifiers"].reverse()
            row["memberships"].reverse()
        second = GAM.build_master(value)
        self.assertEqual(GAM.canonical_json(first), GAM.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, GAM.payload_sha256(second))

    def test_standalone_validator_accepts_persisted_packet(self):
        packet = GAM.build_master(sample_input())
        validated = GAM.validate_packet(copy.deepcopy(packet))
        self.assertEqual(GAM.canonical_json(validated), GAM.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_derived_membership_tamper(self):
        packet = GAM.build_master(sample_input())
        us = next(row for row in packet["records"] if row["market"] == "US")
        us["active_memberships"] = [
            row
            for row in us["active_memberships"]
            if row["membership_type"] != "THEME"
        ]
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "OUTPUT_RECORD_DERIVATION_MISMATCH"
        ):
            GAM.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_authority_expansion(self):
        packet = GAM.build_master(sample_input())
        packet["authority"]["investability_authorized"] = True
        with self.assertRaisesRegex(GAM.AssetMasterError, "OUTPUT_AUTHORITY_MISMATCH"):
            GAM.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_cross_asset_collision(self):
        packet = GAM.build_master(sample_input())
        packet["records"][1]["identifiers"][0] = copy.deepcopy(
            packet["records"][0]["identifiers"][0]
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "IDENTIFIER_COLLISION"):
            GAM.validate_packet(rehash(packet))

    def test_duplicate_asset_primary_and_identifier_collisions_fail_closed(self):
        duplicate = sample_input()
        duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
        with self.assertRaisesRegex(GAM.AssetMasterError, "ASSET_ID_DUPLICATE"):
            GAM.build_master(duplicate)

        primary = sample_input()
        other = copy.deepcopy(primary["records"][0])
        other["asset_id"] = "US:XNAS:MSFT.SECOND"
        other["identifiers"] = [
            {"namespace": "NASDAQ_SYMBOL_SECOND", "value": "MSFT.SECOND"}
        ]
        with self.assertRaisesRegex(GAM.AssetMasterError, "PRIMARY_IDENTITY_COLLISION"):
            GAM.build_master({**primary, "records": primary["records"] + [other]})

        identifier = sample_input()
        identifier["records"][1]["identifiers"][0] = copy.deepcopy(
            identifier["records"][0]["identifiers"][0]
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "IDENTIFIER_COLLISION"):
            GAM.build_master(identifier)

    def test_overlapping_aliases_across_assets_fail_but_reuse_after_end_is_allowed(self):
        value = sample_input()
        other = record(
            "CRYPTO:KRAKEN:BTC2",
            "CRYPTO",
            "CRYPTO_ASSET",
            "BTC2",
            "KRAKEN",
            "USD",
            "kraken_public_api",
        )
        other["aliases"].append(
            {
                "alias_type": "SYMBOL",
                "value": "XBT",
                "exchange_id": "KRAKEN",
                "valid_from": "2019-01-01",
                "valid_to": None,
                "source_identity": interval_source("kraken_public_api", "reuse-overlap"),
            }
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "ALIAS_IDENTITY_COLLISION"):
            GAM.build_master({**value, "records": value["records"] + [other]})

        other["aliases"][-1]["valid_from"] = "2000-01-01"
        other["aliases"][-1]["valid_to"] = "2010-01-01"
        packet = GAM.build_master({**value, "records": value["records"] + [other]})
        self.assertEqual(packet["record_count"], 4)

    def test_overlapping_membership_ranges_fail(self):
        value = sample_input()
        value["records"][0]["memberships"].append(
            {
                "membership_type": "MARKET",
                "membership_id": "US",
                "valid_from": "2025-01-01",
                "valid_to": None,
                "source_identity": interval_source(
                    "nasdaq_trader_symbol_directory", "overlap-market"
                ),
            }
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "MEMBERSHIP_INTERVAL_OVERLAP"):
            GAM.build_master(value)

    def test_primary_alias_and_market_membership_must_be_active(self):
        alias = sample_input()
        alias["records"][0]["aliases"][0]["valid_to"] = "2026-01-01"
        with self.assertRaisesRegex(GAM.AssetMasterError, "PRIMARY_ALIAS_NOT_ACTIVE"):
            GAM.build_master(alias)

        membership = sample_input()
        membership["records"][0]["memberships"][0]["valid_to"] = "2026-01-01"
        with self.assertRaisesRegex(GAM.AssetMasterError, "MARKET_MEMBERSHIP_NOT_ACTIVE"):
            GAM.build_master(membership)

    def test_lineage_and_temporal_errors_fail_closed(self):
        missing = sample_input()
        del missing["records"][0]["source_identity"]["source_sha256"]
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_LINEAGE_INCOMPLETE"):
            GAM.build_master(missing)

        unknown = sample_input()
        unknown["records"][0]["source_identity"]["source_id"] = "unknown_vendor"
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_ID_UNKNOWN"):
            GAM.build_master(unknown)

        future = sample_input()
        future["records"][0]["source_identity"]["available_at"] = "2026-08-20T00:00:00Z"
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            GAM.build_master(future)

    def test_market_and_asset_class_cannot_be_crossed(self):
        value = sample_input()
        value["records"][0]["asset_class"] = "CRYPTO_ASSET"
        with self.assertRaisesRegex(GAM.AssetMasterError, "MARKET_ASSET_CLASS_MISMATCH"):
            GAM.build_master(value)

    def test_contract_tampering_is_rejected(self):
        contract = GAM.load_contract()
        contract["authority"]["investability_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(GAM.AssetMasterError, "AUTHORITY_BOUNDARY_MISMATCH"):
                GAM.load_contract(path)
        with self.assertRaisesRegex(GAM.AssetMasterError, "AUTHORITY_BOUNDARY_MISMATCH"):
            GAM.build_master(sample_input(), contract)

    def test_date_lineage_cannot_be_later_than_retrieval_date(self):
        value = sample_input()
        value["records"][0]["source_identity"]["available_at"] = "2026-08-20"
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            GAM.build_master(value)

    def test_cli_writes_only_requested_temp_output_and_preserves_on_failure(self):
        tracked_before = (ROOT / "config" / "universe.json").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            output_path = tmp / "master.json"
            input_path.write_text(json.dumps(sample_input()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    str(input_path),
                    "--out",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["record_count"], 3)

            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            broken = sample_input()
            broken["records"][0]["memberships"] = []
            input_path.write_text(json.dumps(broken), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    str(input_path),
                    "--out",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "config" / "universe.json").read_bytes(), tracked_before)

    def test_module_has_no_network_client_or_tracked_default_output(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", text)
        self.assertNotIn("urllib.request", text)
        self.assertNotIn("config/universe.json", text)
        self.assertNotIn("data/", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
