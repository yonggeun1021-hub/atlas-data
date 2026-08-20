#!/usr/bin/env python3
"""P7-07 raw quote-currency exposure regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "portfolio" / "currency_exposure.py"
ASSET_MASTER_TEST = ROOT / "test" / "test_global_asset_master.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("currency_exposure", SOURCE)
ASSET_FIXTURE = load_module("currency_asset_master_fixture", ASSET_MASTER_TEST)
CONTRACT = MODULE.load_contract()


def asset_master():
    value = ASSET_FIXTURE.sample_input()
    value["as_of_date"] = "2026-08-21"
    return ASSET_FIXTURE.GAM.build_master(value)


def position(
    account_id,
    position_id,
    asset_id,
    quantity,
    price,
    currency,
    marker,
):
    return {
        "account_id": account_id,
        "position_id": position_id,
        "asset_id": asset_id,
        "quantity": quantity,
        "price": price,
        "price_quote_currency": currency,
        "price_as_of": "2026-08-21T00:50:00Z",
        "price_source_ref": f"test://price/{position_id}",
        "price_source_sha256": marker * 64,
        "position_record_sha256": marker.upper().lower() * 64,
    }


def positions():
    return [
        position("PAPER", "MSFT", "US:XNAS:MSFT", "2", "100", "USD", "a"),
        position("PAPER", "BTC", "CRYPTO:KRAKEN:BTC", "0.1", "60000", "USD", "b"),
        position("PAPER", "SAMSUNG", "KR:XKRX:005930", "10", "70000", "KRW", "c"),
    ]


def snapshot(rows=None):
    value = {
        "schema_version": "currency_position_snapshot/1",
        "contract_version": "currency_exposure/1",
        "snapshot_id": "TEST-POSITIONS-2026-08-21",
        "as_of_date": "2026-08-21",
        "available_at": "2026-08-21T01:00:00Z",
        "positions": positions() if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    normalized = copy.deepcopy(value)
    normalized["positions"] = sorted(
        normalized["positions"],
        key=lambda row: (row["account_id"], row["position_id"]),
    )
    value["packet_sha256"] = MODULE.payload_sha256(normalized)
    return value


def write_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def contains_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(contains_float(item) for item in value.values())
    if isinstance(value, list):
        return any(contains_float(item) for item in value)
    return False


class CurrencyExposureTests(unittest.TestCase):
    def test_contract_forbids_conversion_limits_sizing_and_actions(self):
        self.assertEqual(
            CONTRACT["aggregation_policy"],
            "QUOTE_CURRENCY_ONLY_NO_CROSS_CURRENCY_TOTAL",
        )
        self.assertEqual(CONTRACT["fx_conversion_policy"], "NOT_AUTHORIZED")
        self.assertEqual(CONTRACT["exposure_limit_policy"], "UNRATIFIED")
        self.assertTrue(CONTRACT["authority"]["raw_currency_exposure_aggregation_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "raw_currency_exposure_aggregation_only":
                self.assertFalse(value, key)

    def test_three_markets_aggregate_only_inside_quote_currency(self):
        result = MODULE.build_packet(asset_master(), snapshot(), CONTRACT)
        by_currency = {
            row["quote_currency"]: row
            for row in result["quote_currency_exposures"]
        }
        self.assertEqual(by_currency["USD"]["raw_gross_notional"], "6200")
        self.assertEqual(by_currency["USD"]["position_count"], 2)
        self.assertEqual(by_currency["USD"]["markets"], ["CRYPTO", "US"])
        self.assertEqual(by_currency["KRW"]["raw_gross_notional"], "700000")
        self.assertEqual(result["summary"]["cross_currency_total"], None)
        self.assertIsNone(result["summary"]["reporting_currency"])
        self.assertEqual(result["summary"]["reporting_currency_status"], "UNRATIFIED")
        self.assertNotIn("706200", json.dumps(result))
        for row in by_currency.values():
            self.assertEqual(row["fx_conversion_status"], "NOT_AUTHORIZED")
            self.assertEqual(row["limit_status"], "UNRATIFIED")
            self.assertIsNone(row["limit_value"])
            self.assertIsNone(row["breach"])

    def test_position_currency_must_match_asset_master(self):
        rows = positions()
        rows[0]["price_quote_currency"] = "KRW"
        with self.assertRaisesRegex(
            MODULE.CurrencyExposureError,
            "POSITION_QUOTE_CURRENCY_MISMATCH",
        ):
            MODULE.build_packet(asset_master(), snapshot(rows), CONTRACT)

    def test_unknown_asset_and_duplicate_position_identity_fail_closed(self):
        unknown = positions()
        unknown[0]["asset_id"] = "US:XNAS:UNKNOWN"
        with self.assertRaisesRegex(MODULE.CurrencyExposureError, "POSITION_ASSET_UNKNOWN"):
            MODULE.build_packet(asset_master(), snapshot(unknown), CONTRACT)

        duplicate = positions()
        duplicate[1]["account_id"] = duplicate[0]["account_id"]
        duplicate[1]["position_id"] = duplicate[0]["position_id"]
        with self.assertRaisesRegex(MODULE.CurrencyExposureError, "POSITION_ID_DUPLICATE"):
            MODULE.build_packet(asset_master(), snapshot(duplicate), CONTRACT)

    def test_future_price_and_master_date_mismatch_fail_closed(self):
        future = positions()
        future[0]["price_as_of"] = "2026-08-21T01:00:01Z"
        with self.assertRaisesRegex(MODULE.CurrencyExposureError, "POSITION_PRICE_FROM_FUTURE"):
            MODULE.build_packet(asset_master(), snapshot(future), CONTRACT)

        dated = snapshot()
        dated["as_of_date"] = "2026-08-20"
        with self.assertRaisesRegex(
            MODULE.CurrencyExposureError,
            "POSITION_ASSET_MASTER_DATE_MISMATCH",
        ):
            MODULE.build_packet(asset_master(), dated, CONTRACT)

    def test_asset_master_digest_and_authority_tamper_fail_closed(self):
        digest = asset_master()
        digest["records"][0]["quote_currency"] = "KRW"
        with self.assertRaisesRegex(MODULE.CurrencyExposureError, "ASSET_MASTER_SHA_MISMATCH"):
            MODULE.build_packet(digest, snapshot(), CONTRACT)

        authority = asset_master()
        authority["authority"]["trading_authorized"] = True
        authority.pop("payload_sha256")
        authority["payload_sha256"] = MODULE.payload_sha256(authority)
        with self.assertRaisesRegex(MODULE.CurrencyExposureError, "ASSET_MASTER_IDENTITY_INVALID"):
            MODULE.build_packet(authority, snapshot(), CONTRACT)

    def test_snapshot_authority_and_digest_tamper_fail_closed(self):
        authority = snapshot()
        authority["authority"]["fx_conversion_authorized"] = True
        with self.assertRaisesRegex(
            MODULE.CurrencyExposureError,
            "POSITION_SNAPSHOT_IDENTITY_INVALID",
        ):
            MODULE.build_packet(asset_master(), authority, CONTRACT)

        digest = snapshot()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.CurrencyExposureError, "POSITION_SNAPSHOT_SHA_MISMATCH"):
            MODULE.build_packet(asset_master(), digest, CONTRACT)

    def test_noncanonical_zero_negative_and_float_values_fail_closed(self):
        cases = (("2.0", "100"), ("0", "100"), ("-1", "100"), (2, "100"), ("2", "0"))
        for quantity, price in cases:
            with self.subTest(quantity=quantity, price=price):
                rows = positions()
                rows[0]["quantity"] = quantity
                rows[0]["price"] = price
                with self.assertRaisesRegex(
                    MODULE.CurrencyExposureError,
                    "POSITION_(QUANTITY|PRICE)_INVALID",
                ):
                    MODULE.build_packet(asset_master(), snapshot(rows), CONTRACT)

    def test_output_is_decimal_string_deterministic_and_input_immutable(self):
        master = asset_master()
        value = snapshot()
        before_master = MODULE.canonical_json(master)
        before_value = MODULE.canonical_json(value)
        first = MODULE.build_packet(master, value, CONTRACT)
        second = MODULE.build_packet(master, value, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertFalse(contains_float(first))
        self.assertEqual(MODULE.canonical_json(master), before_master)
        self.assertEqual(MODULE.canonical_json(value), before_value)
        digest = first.pop("packet_sha256")
        self.assertEqual(digest, MODULE.payload_sha256(first))

    def test_position_input_permutation_is_canonical(self):
        master = asset_master()
        first = MODULE.build_packet(master, snapshot(), CONTRACT)
        permuted = snapshot(list(reversed(positions())))
        second = MODULE.build_packet(master, permuted, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))

    def test_source_is_offline_and_cli_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            master_path = write_json(tmp / "master.json", asset_master())
            snapshot_path = write_json(tmp / "positions.json", snapshot())
            output_path = tmp / "nested" / "exposure.json"
            self.assertEqual(MODULE.run(master_path, snapshot_path, output_path), 0)
            payload = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["summary"]["position_count"], 3)
            self.assertEqual(list(output_path.parent.glob(".exposure.json.*")), [])

            forbidden = ROOT / "data" / "currency_exposure_test.json"
            self.assertEqual(MODULE.run(master_path, snapshot_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
