"""P3-02 forward-only US source-coverage universe adapter regression."""
from __future__ import annotations

import base64
import copy
import csv
import gzip
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "us_global_universe.py"
SPEC = importlib.util.spec_from_file_location("us_global_universe", MODULE_PATH)
UGU = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(UGU)

CONTRACT = UGU.load_contract()
SOURCE_CONTRACT = UGU._load_source_contract(CONTRACT)
SOURCES = {row["name"]: row for row in SOURCE_CONTRACT["sources"]}


def directory_body(source_name: str, *, count: int = 1000, day="08202026") -> bytes:
    source = SOURCES[source_name]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=source["required_fields"],
        delimiter="|",
        lineterminator="\r\n",
    )
    writer.writeheader()
    for index in range(count):
        if source_name == "nasdaq_listed":
            symbol = f"N{index:04d}"
            row = {
                "Symbol": symbol,
                "Security Name": f"Nasdaq Synthetic {index}",
                "Market Category": "Q",
                "Test Issue": "Y" if index == 0 else "N",
                "Financial Status": "D" if index == 0 else "N",
                "Round Lot Size": "100",
                "ETF": "Y" if index == 0 else "N",
                "NextShares": "N",
            }
        else:
            symbol = "PREF$A" if index == 0 else f"O{index:04d}"
            row = {
                "ACT Symbol": symbol,
                "Security Name": f"Other Synthetic {index}",
                "Exchange": "N" if index % 2 == 0 else "P",
                "CQS Symbol": symbol,
                "ETF": "Y" if index == 0 else "N",
                "Round Lot Size": "100",
                "Test Issue": "Y" if index == 0 else "N",
                "NASDAQ Symbol": symbol,
            }
        writer.writerow(row)
    output.write("|".join([f"File Creation Time: {day}08:46"] + [""] * 7))
    output.write("\r\n")
    return output.getvalue().encode("utf-8")


BASE_BODIES = {name: directory_body(name) for name in SOURCES}


def snapshot(source_name: str, body: bytes | None = None) -> dict:
    source = SOURCES[source_name]
    body = BASE_BODIES[source_name] if body is None else body
    return {
        "source_name": source_name,
        "response_body_base64": base64.b64encode(body).decode("ascii"),
        "source_identity": {
            "source_id": "nasdaq_trader_symbol_directory",
            "source_url": source["endpoint"],
            "source_sha256": hashlib.sha256(body).hexdigest(),
            "available_at": "2026-08-20",
            "retrieved_at_utc": "2026-08-20T12:50:00Z",
        },
    }


def payload() -> dict:
    return {
        "schema_version": "us_global_universe_input/1",
        "master_id": "US_SOURCE_COVERAGE_20260820",
        "as_of_date": "2026-08-20",
        "as_of_utc": "2026-08-20T13:00:00Z",
        "snapshots": [snapshot("nasdaq_listed"), snapshot("other_listed")],
    }


def tracked_payload() -> dict:
    root = ROOT / "evidence" / "us_breadth" / "raw" / "2026-08-19"
    manifest = json.loads((root / "_manifest.json").read_text(encoding="utf-8"))
    snapshots = []
    for endpoint in manifest["endpoints"]:
        body = gzip.open(root / endpoint["raw_file"], "rb").read()
        snapshots.append(
            {
                "source_name": endpoint["name"],
                "response_body_base64": base64.b64encode(body).decode("ascii"),
                "source_identity": {
                    "source_id": "nasdaq_trader_symbol_directory",
                    "source_url": endpoint["endpoint"],
                    "source_sha256": endpoint["response_sha256"],
                    "available_at": manifest["snapshot_date"],
                    "retrieved_at_utc": manifest["fetched_at_utc"],
                },
            }
        )
    return {
        "schema_version": "us_global_universe_input/1",
        "master_id": "US_SOURCE_COVERAGE_20260819",
        "as_of_date": "2026-08-19",
        "as_of_utc": "2026-08-19T13:00:00Z",
        "snapshots": snapshots,
    }


class UsGlobalUniverseTests(unittest.TestCase):
    def test_both_exact_source_files_enter_global_master(self):
        packet = UGU.build_packet(payload())
        self.assertEqual(
            packet["status"], "FORWARD_SOURCE_COVERAGE_UNIVERSE_VALIDATED"
        )
        self.assertEqual(
            packet["source_counts"], {"nasdaq_listed": 1000, "other_listed": 1000}
        )
        self.assertEqual(packet["total_count"], 2000)
        self.assertEqual(packet["asset_master"]["record_count"], 2000)
        self.assertEqual(
            packet["effective_interval"],
            {"valid_from": "2026-08-20", "valid_to": "2026-08-21"},
        )
        for record in packet["asset_master"]["records"]:
            self.assertEqual(record["market"], "US")
            self.assertEqual(record["asset_class"], "EQUITY")
            self.assertEqual(record["quote_currency"], "USD")
            self.assertTrue(record["exchange_id"].startswith("NASDAQ_TRADER:"))
            self.assertFalse(record["universe_approved"])
            self.assertFalse(record["investable_eligible"])

    def test_source_attributes_are_preserved_but_never_interpreted(self):
        packet = UGU.build_packet(payload())
        nasdaq = next(
            row
            for row in packet["source_attribute_rows"]
            if row["source_name"] == "nasdaq_listed" and row["primary_symbol"] == "N0000"
        )
        self.assertEqual(nasdaq["fields"]["Test Issue"], "Y")
        self.assertEqual(nasdaq["fields"]["ETF"], "Y")
        self.assertEqual(nasdaq["fields"]["Financial Status"], "D")
        self.assertIsNone(nasdaq["eligibility_interpretation"])
        self.assertIsNone(nasdaq["liquidity_observation"])
        self.assertIsNone(nasdaq["tradability_decision"])
        self.assertFalse(nasdaq["investable_eligible"])

    def test_exact_dollar_symbol_is_preserved_without_ticker_inference(self):
        packet = UGU.build_packet(payload())
        preferred = next(
            row
            for row in packet["asset_master"]["records"]
            if row["primary_symbol"] == "PREF$A"
        )
        self.assertEqual(preferred["exchange_id"], "NASDAQ_TRADER:N")
        self.assertEqual(preferred["active_aliases"][0]["value"], "PREF$A")
        self.assertTrue(preferred["asset_id"].startswith("US:NASDAQDIR:"))
        self.assertNotIn("XNYS", preferred["exchange_id"])

    def test_membership_is_exact_date_source_coverage_only(self):
        packet = UGU.build_packet(payload())
        self.assertEqual(
            packet["membership_semantics"],
            "exact_source_date_forward_coverage_not_investable",
        )
        sample = packet["asset_master"]["records"][0]
        memberships = {
            (row["membership_type"], row["membership_id"])
            for row in sample["active_memberships"]
        }
        self.assertIn(("MARKET", "US"), memberships)
        self.assertTrue(
            any(
                kind == "UNIVERSE" and value.startswith("NASDAQ_TRADER_")
                for kind, value in memberships
            )
        )
        for row in sample["active_memberships"]:
            self.assertEqual(row["valid_from"], "2026-08-20")
            self.assertEqual(row["valid_to"], "2026-08-21")

    def test_exact_body_sha_and_official_endpoint_are_required(self):
        tampered = payload()
        tampered["snapshots"][0]["source_identity"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(UGU.UsUniverseError, "SOURCE_SHA256_MISMATCH"):
            UGU.build_packet(tampered)
        wrong_host = payload()
        wrong_host["snapshots"][0]["source_identity"]["source_url"] = (
            "https://example.invalid/dynamic/SymDir/nasdaqlisted.txt"
        )
        with self.assertRaisesRegex(UGU.UsUniverseError, "SOURCE_URL_MISMATCH"):
            UGU.build_packet(wrong_host)
        query = payload()
        query["snapshots"][0]["source_identity"]["source_url"] += "?date=20260820"
        with self.assertRaisesRegex(UGU.UsUniverseError, "SOURCE_URL_MISMATCH"):
            UGU.build_packet(query)

    def test_required_sources_duplicate_and_cross_source_collision_fail_closed(self):
        missing = payload()
        missing["snapshots"] = missing["snapshots"][:1]
        with self.assertRaisesRegex(UGU.UsUniverseError, "REQUIRED_SOURCE_MISSING"):
            UGU.build_packet(missing)
        duplicate = payload()
        duplicate["snapshots"].append(copy.deepcopy(duplicate["snapshots"][0]))
        with self.assertRaisesRegex(UGU.UsUniverseError, "SNAPSHOT_SOURCE_DUPLICATE"):
            UGU.build_packet(duplicate)

        body = directory_body("other_listed").replace(b"PREF$A|", b"N0000|")
        collision = payload()
        collision["snapshots"][1] = snapshot("other_listed", body)
        with self.assertRaisesRegex(UGU.UsUniverseError, "CROSS_SOURCE_SYMBOL_COLLISION"):
            UGU.build_packet(collision)

    def test_source_date_and_as_of_timing_fail_closed(self):
        wrong_date = payload()
        wrong_date["snapshots"][0] = snapshot(
            "nasdaq_listed", directory_body("nasdaq_listed", day="08192026")
        )
        with self.assertRaisesRegex(UGU.UsUniverseError, "SOURCE_DATE_MISMATCH"):
            UGU.build_packet(wrong_date)
        available = payload()
        available["snapshots"][0]["source_identity"]["available_at"] = "2026-08-19"
        with self.assertRaisesRegex(UGU.UsUniverseError, "AVAILABLE_AT_MISMATCH"):
            UGU.build_packet(available)
        future = payload()
        future["snapshots"][0]["source_identity"]["retrieved_at_utc"] = (
            "2026-08-20T14:00:00Z"
        )
        with self.assertRaisesRegex(UGU.UsUniverseError, "TEMPORAL_ORDER_INVALID"):
            UGU.build_packet(future)

    def test_malformed_header_footer_and_truncation_fail_closed(self):
        header = payload()
        body = BASE_BODIES["nasdaq_listed"].replace(b"Symbol|", b"Ticker|", 1)
        header["snapshots"][0] = snapshot("nasdaq_listed", body)
        with self.assertRaisesRegex(UGU.UsUniverseError, "SOURCE_BODY_INVALID"):
            UGU.build_packet(header)
        footer = payload()
        body = BASE_BODIES["other_listed"].replace(b"File Creation Time:", b"Broken:")
        footer["snapshots"][1] = snapshot("other_listed", body)
        with self.assertRaisesRegex(UGU.UsUniverseError, "SOURCE_BODY_INVALID"):
            UGU.build_packet(footer)
        short = payload()
        short["snapshots"][0] = snapshot(
            "nasdaq_listed", directory_body("nasdaq_listed", count=999)
        )
        with self.assertRaisesRegex(UGU.UsUniverseError, "RECORD_COUNT_TOO_SMALL"):
            UGU.build_packet(short)

    def test_input_and_snapshot_order_are_deterministic_and_digest_bound(self):
        first = UGU.build_packet(payload())
        value = payload()
        value["snapshots"].reverse()
        second = UGU.build_packet(value)
        self.assertEqual(UGU.canonical_json(first), UGU.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, UGU.payload_sha256(second))

    def test_authority_and_paid_data_checkpoint_stay_closed(self):
        packet = UGU.build_packet(payload())
        self.assertTrue(packet["authority"]["source_coverage_universe_only"])
        for field in (
            "cross_source_identity_merge_authorized",
            "exchange_MIC_inference_authorized",
            "security_type_filter_authorized",
            "liquidity_filter_authorized",
            "tradability_filter_authorized",
            "investable_universe_authorized",
            "historical_reconstruction_authorized",
            "stage_promotion_authorized",
            "production_authorized",
            "trading_authorized",
            "paid_data_acquisition_authorized",
        ):
            self.assertFalse(packet["authority"][field])
        checkpoint = packet["paid_data_checkpoint"]
        self.assertEqual(checkpoint["status"], "USER_RECONFIRMATION_REQUIRED")
        self.assertFalse(checkpoint["approved"])
        self.assertIn("delisted_security_ohlcv_acquisition", checkpoint["stop_before"])

    def test_contract_tampering_is_rejected_for_file_and_api(self):
        contract = UGU.load_contract()
        contract["authority"]["investable_universe_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(UGU.UsUniverseError, "CONTRACT_FIELD_MISMATCH"):
                UGU.load_contract(path)
        with self.assertRaisesRegex(UGU.UsUniverseError, "CONTRACT_FIELD_MISMATCH"):
            UGU.build_packet(payload(), contract)

    def test_tracked_2026_08_19_snapshot_replays_all_13161_rows(self):
        packet = UGU.build_packet(tracked_payload())
        self.assertEqual(
            packet["source_counts"],
            {"nasdaq_listed": 5603, "other_listed": 7558},
        )
        self.assertEqual(packet["total_count"], 13161)
        self.assertEqual(packet["asset_master"]["record_count"], 13161)
        self.assertEqual(
            {row["source_name"]: row["source_sha256"] for row in packet["source_snapshots"]},
            {
                "nasdaq_listed": "62ec703000e00392bae64f72ce44ae428aee82ac2b3bb28bd28f868bee964466",
                "other_listed": "ce97d609440f58e9930477171bfac6dfc6742b05ac0cc9175ff402c1beaa826b",
            },
        )

    def test_cli_is_temp_only_atomic_and_preserves_existing_output_on_failure(self):
        tracked_before = (ROOT / "config" / "universe.json").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            output_path = tmp / "output.json"
            input_path.write_text(json.dumps(payload()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["total_count"], 2000)
            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            broken = payload()
            broken["snapshots"] = broken["snapshots"][:1]
            input_path.write_text(json.dumps(broken), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "config" / "universe.json").read_bytes(), tracked_before)

    def test_adapter_has_no_network_workflow_or_tracked_output(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("urlopen", text)
        self.assertNotIn("import requests", text)
        self.assertNotIn("config/universe.json", text)
        self.assertNotIn("data/", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
