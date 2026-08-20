"""P3-03 KOSPI/KOSDAQ exact-date Global Asset Master adapter regression."""
from __future__ import annotations

import base64
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
MODULE_PATH = ROOT / "universe" / "krx_global_universe.py"
SPEC = importlib.util.spec_from_file_location("krx_global_universe", MODULE_PATH)
KRU = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(KRU)


def row(code: str, name: str, market: str, day: str = "20260820") -> dict:
    return {
        "BAS_DD": day,
        "ISU_CD": code,
        "ISU_NM": name,
        "MKT_NM": market,
        "SECT_TP_NM": "fixture-section",
        "TDD_CLSPRC": "100",
        "CMPPREVDD_PRC": "1",
        "FLUC_RT": "1.00",
        "TDD_OPNPRC": "99",
        "TDD_HGPRC": "101",
        "TDD_LWPRC": "98",
        "ACC_TRDVOL": "1000",
        "ACC_TRDVAL": "100000",
        "MKTCAP": "1000000",
        "LIST_SHRS": "10000",
    }


def raw_payload(rows: list[dict]) -> bytes:
    return json.dumps(
        {"OutBlock_1": rows}, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")


def snapshot(market: str, rows: list[dict]) -> dict:
    body = raw_payload(rows)
    endpoint = {
        "KOSPI": "stk_bydd_trd",
        "KOSDAQ": "ksq_bydd_trd",
    }[market]
    return {
        "market": market,
        "response_body_base64": base64.b64encode(body).decode("ascii"),
        "source_identity": {
            "source_id": "krx_open_api_stock_daily",
            "source_url": (
                f"https://data-dbg.krx.co.kr/svc/apis/sto/{endpoint}?basDd=20260820"
            ),
            "source_sha256": hashlib.sha256(body).hexdigest(),
            "available_at": "2026-08-20T07:00:00Z",
            "retrieved_at_utc": "2026-08-20T07:05:00Z",
        },
    }


def sample_input() -> dict:
    return {
        "schema_version": "krx_global_universe_input/1",
        "master_id": "KRX_SOURCE_COVERAGE_20260820",
        "as_of_date": "2026-08-20",
        "snapshots": [
            snapshot(
                "KOSPI",
                [
                    row("KR7005930003", "삼성전자", "KOSPI"),
                    row("KR7000660001", "SK하이닉스", "KOSPI"),
                ],
            ),
            snapshot(
                "KOSDAQ",
                [row("KR7035720002", "카카오", "KOSDAQ")],
            ),
        ],
    }


class KrxGlobalUniverseTests(unittest.TestCase):
    def test_both_exact_date_markets_enter_global_master(self):
        packet = KRU.build_packet(sample_input())
        self.assertEqual(packet["status"], "SOURCE_COVERAGE_UNIVERSE_VALIDATED")
        self.assertEqual(packet["market_counts"], {"KOSDAQ": 1, "KOSPI": 2})
        self.assertEqual(packet["total_count"], 3)
        self.assertEqual(packet["asset_master"]["record_count"], 3)
        self.assertEqual(
            [record["asset_id"] for record in packet["asset_master"]["records"]],
            ["KR:XKRX:KR7000660001", "KR:XKRX:KR7005930003", "KR:XKRX:KR7035720002"],
        )
        for record in packet["asset_master"]["records"]:
            self.assertEqual(record["market"], "KOREA")
            self.assertEqual(record["asset_class"], "EQUITY")
            self.assertEqual(record["exchange_id"], "XKRX")
            self.assertEqual(record["quote_currency"], "KRW")
            self.assertFalse(record["universe_approved"])
            self.assertFalse(record["investable_eligible"])

    def test_membership_is_exact_date_and_source_coverage_only(self):
        packet = KRU.build_packet(sample_input())
        self.assertEqual(
            packet["effective_interval"],
            {"valid_from": "2026-08-20", "valid_to": "2026-08-21"},
        )
        self.assertEqual(
            packet["membership_semantics"],
            "exact_trading_date_source_coverage_not_investable",
        )
        for record in packet["asset_master"]["records"]:
            universe = [
                item
                for item in record["active_memberships"]
                if item["membership_type"] == "UNIVERSE"
            ]
            self.assertEqual(len(universe), 1)
            self.assertIn(universe[0]["membership_id"], {"KOSPI", "KOSDAQ"})
            self.assertEqual(universe[0]["valid_from"], "2026-08-20")
            self.assertEqual(universe[0]["valid_to"], "2026-08-21")
        self.assertTrue(packet["authority"]["source_coverage_universe_only"])
        self.assertFalse(packet["authority"]["investable_universe_authorized"])
        self.assertFalse(packet["authority"]["liquidity_filter_authorized"])
        self.assertFalse(packet["authority"]["tradability_filter_authorized"])

    def test_identity_uses_exact_krx_id_without_name_or_ticker_inference(self):
        packet = KRU.build_packet(sample_input())
        samsung = next(
            row
            for row in packet["asset_master"]["records"]
            if row["display_name"] == "삼성전자"
        )
        self.assertEqual(samsung["primary_symbol"], "KR7005930003")
        self.assertEqual(
            samsung["identifiers"],
            [{"namespace": "KRX_ISU_CD", "value": "KR7005930003"}],
        )
        self.assertNotIn(
            "KRX_SHORT_CODE", {item["namespace"] for item in samsung["identifiers"]}
        )

    def test_exact_raw_response_sha_is_verified_and_preserved(self):
        value = sample_input()
        expected = {
            item["market"]: item["source_identity"]["source_sha256"]
            for item in value["snapshots"]
        }
        packet = KRU.build_packet(value)
        self.assertEqual(
            {item["market"]: item["source_sha256"] for item in packet["source_snapshots"]},
            expected,
        )
        tampered = sample_input()
        tampered["snapshots"][0]["source_identity"]["source_sha256"] = "0" * 64
        with self.assertRaisesRegex(KRU.KrxUniverseError, "SOURCE_SHA256_MISMATCH"):
            KRU.build_packet(tampered)

    def test_source_url_is_exact_official_market_endpoint_and_date(self):
        wrong_host = sample_input()
        wrong_host["snapshots"][0]["source_identity"]["source_url"] = (
            "https://example.invalid/svc/apis/sto/stk_bydd_trd?basDd=20260820"
        )
        with self.assertRaisesRegex(KRU.KrxUniverseError, "SOURCE_URL_MISMATCH:KOSPI"):
            KRU.build_packet(wrong_host)

        wrong_market = sample_input()
        wrong_market["snapshots"][0]["source_identity"]["source_url"] = (
            "https://data-dbg.krx.co.kr/svc/apis/sto/ksq_bydd_trd?basDd=20260820"
        )
        with self.assertRaisesRegex(KRU.KrxUniverseError, "SOURCE_URL_MISMATCH:KOSPI"):
            KRU.build_packet(wrong_market)

        wrong_date = sample_input()
        wrong_date["snapshots"][0]["source_identity"]["source_url"] = (
            "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd?basDd=20260819"
        )
        with self.assertRaisesRegex(KRU.KrxUniverseError, "SOURCE_URL_MISMATCH:KOSPI"):
            KRU.build_packet(wrong_date)

    def test_input_order_is_deterministic_and_digest_bound(self):
        first = KRU.build_packet(sample_input())
        value = sample_input()
        value["snapshots"].reverse()
        second = KRU.build_packet(value)
        self.assertEqual(KRU.canonical_json(first), KRU.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, KRU.payload_sha256(second))

    def test_both_markets_are_required_once(self):
        missing = sample_input()
        missing["snapshots"] = missing["snapshots"][:1]
        with self.assertRaisesRegex(KRU.KrxUniverseError, "REQUIRED_MARKET_MISSING:KOSDAQ"):
            KRU.build_packet(missing)

        duplicate = sample_input()
        duplicate["snapshots"].append(copy.deepcopy(duplicate["snapshots"][0]))
        with self.assertRaisesRegex(KRU.KrxUniverseError, "SNAPSHOT_MARKET_DUPLICATE"):
            KRU.build_packet(duplicate)

    def test_row_market_and_exact_date_must_match_snapshot(self):
        wrong_market = sample_input()
        rows = [row("KR7005930003", "삼성전자", "KOSDAQ")]
        wrong_market["snapshots"][0] = snapshot("KOSPI", rows)
        with self.assertRaisesRegex(KRU.KrxUniverseError, "ROW_MARKET_MISMATCH"):
            KRU.build_packet(wrong_market)

        wrong_date = sample_input()
        rows = [row("KR7005930003", "삼성전자", "KOSPI", day="20260819")]
        wrong_date["snapshots"][0] = snapshot("KOSPI", rows)
        with self.assertRaisesRegex(KRU.KrxUniverseError, "BAS_DD_MISMATCH"):
            KRU.build_packet(wrong_date)

    def test_identity_collision_across_markets_fails_closed(self):
        value = sample_input()
        duplicate = row("KR7005930003", "다른이름", "KOSDAQ")
        value["snapshots"][1] = snapshot("KOSDAQ", [duplicate])
        with self.assertRaisesRegex(KRU.KrxUniverseError, "ASSET_ID_DUPLICATE"):
            KRU.build_packet(value)

    def test_malformed_base64_json_and_schema_fail_closed(self):
        invalid_base64 = sample_input()
        invalid_base64["snapshots"][0]["response_body_base64"] = "***"
        with self.assertRaisesRegex(KRU.KrxUniverseError, "RESPONSE_BODY_BASE64_INVALID"):
            KRU.build_packet(invalid_base64)

        invalid_json = sample_input()
        body = b"{bad-json"
        invalid_json["snapshots"][0]["response_body_base64"] = base64.b64encode(
            body
        ).decode()
        invalid_json["snapshots"][0]["source_identity"]["source_sha256"] = hashlib.sha256(
            body
        ).hexdigest()
        with self.assertRaisesRegex(KRU.KrxUniverseError, "KRX_RESPONSE_INVALID_JSON"):
            KRU.build_packet(invalid_json)

        missing_field = sample_input()
        broken = row("KR7005930003", "삼성전자", "KOSPI")
        broken.pop("LIST_SHRS")
        missing_field["snapshots"][0] = snapshot("KOSPI", [broken])
        with self.assertRaisesRegex(KRU.KrxUniverseError, "REQUIRED_FIELDS_MISSING"):
            KRU.build_packet(missing_field)

    def test_contract_authority_tamper_is_rejected_for_file_and_api(self):
        contract = KRU.load_contract()
        contract["authority"]["investable_universe_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(KRU.KrxUniverseError, "CONTRACT_FIELD_MISMATCH"):
                KRU.load_contract(path)
        with self.assertRaisesRegex(KRU.KrxUniverseError, "CONTRACT_FIELD_MISMATCH"):
            KRU.build_packet(sample_input(), contract)

    def test_cli_is_temp_only_atomic_and_preserves_existing_output_on_failure(self):
        tracked_before = (ROOT / "config" / "universe.json").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            output_path = tmp / "output.json"
            input_path.write_text(json.dumps(sample_input()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["total_count"], 3)

            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            broken = sample_input()
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

    def test_adapter_has_no_network_or_tracked_output_path(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("urlopen", text)
        self.assertNotIn("import requests", text)
        self.assertNotIn("config/universe.json", text)
        self.assertNotIn("data/", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
