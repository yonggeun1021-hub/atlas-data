"""Fail-closed KIS + KRX point-in-time universe registry regression."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


M = load("krx_investable_registry", "universe/krx_investable_registry.py")
E = load("krx_execution_measurements_for_registry", "universe/krx_execution_measurements.py")
KRU_FIXTURE = load("krx_global_universe_registry_fixture", "test/test_krx_global_universe.py")


def kis_line(
    market: str,
    short_code: str,
    standard_code: str,
    name: str,
    **overrides,
) -> bytes:
    widths, fields = M.MASTER_LAYOUT[market]
    values = {field: "" for field in fields}
    values.update({
        "security_group": "ST",
        "preferred_code": "0",
        "spac": "N",
        "low_liquidity": "N",
        "trading_halt": "N",
        "liquidation_trading": "N",
        "managed_issue": "N",
        "market_warning": "00",
        "warning_advance": "N",
    })
    if market == "KOSDAQ":
        values["investment_attention"] = "N"
    values.update(overrides)
    tail = "".join(str(values[field]).ljust(width)[:width] for field, width in zip(fields, widths))
    head = short_code.ljust(9) + standard_code.ljust(12) + name
    return head.encode("cp949") + tail.encode("ascii")


def write_master(path: Path, market: str, rows: list[bytes]) -> Path:
    member = {"KOSPI": "kospi_code.mst", "KOSDAQ": "kosdaq_code.mst"}[market]
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(member, b"\n".join(rows) + b"\n")
    return path


def krx_packet(kospi: list[tuple[str, str]], kosdaq: list[tuple[str, str]], day="20260820"):
    value = {
        "schema_version": "krx_global_universe_input/1",
        "master_id": f"KRX_SOURCE_COVERAGE_{day}",
        "as_of_date": f"{day[:4]}-{day[4:6]}-{day[6:]}",
        "snapshots": [
            KRU_FIXTURE.snapshot(
                "KOSPI", [KRU_FIXTURE.row(code, name, "KOSPI", day=day) for code, name in kospi]
            ),
            KRU_FIXTURE.snapshot(
                "KOSDAQ", [KRU_FIXTURE.row(code, name, "KOSDAQ", day=day) for code, name in kosdaq]
            ),
        ],
    }
    for snapshot in value["snapshots"]:
        snapshot["source_identity"]["source_url"] = snapshot["source_identity"]["source_url"].replace(
            "20260820", day
        )
    return M.KRU.build_packet(value)


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def session_evidence(root: Path, day="2026-08-20") -> tuple[Path, str]:
    packet = {
        "schema_version": "korea_market_signals_observation/1",
        "as_of_date": day,
        "status": "OBSERVED_UNCLASSIFIED",
        "authority": {
            "observation_only": True,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "strategy_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["payload_sha256"] = M.payload_sha256(packet)
    path = write_json(root / "session-evidence.json", packet)
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


class KrxInvestableRegistryTests(unittest.TestCase):
    def make_inputs(self, root: Path, *, stale=False, previous=None, kospi_rows=None, kosdaq_rows=None):
        samsung = ("005930", "KR7005930003", "삼성전자")
        preferred = ("005935", "KR7005931001", "삼성전자우")
        etf = ("069500", "KR7069500007", "KODEX 200")
        kakao = ("035720", "KR7035720002", "카카오")
        spac = ("123450", "KR7123450006", "테스트스팩")
        kospi_rows = kospi_rows or [
            kis_line("KOSPI", *samsung),
            kis_line("KOSPI", *preferred, preferred_code="1"),
            kis_line("KOSPI", *etf, security_group="EF", etp_code="2", preferred_code="0"),
        ]
        kosdaq_rows = kosdaq_rows or [
            kis_line("KOSDAQ", *kakao),
            kis_line("KOSDAQ", *spac, spac="Y"),
        ]
        kospi_path = write_master(root / "kospi.zip", "KOSPI", kospi_rows)
        kosdaq_path = write_master(root / "kosdaq.zip", "KOSDAQ", kosdaq_rows)
        packet = krx_packet(
            [(samsung[0], samsung[2]), (preferred[0], preferred[2])],
            [(kakao[0], kakao[2]), (spac[0], spac[2])],
        )
        packet_path = write_json(root / "krx.json", packet)
        session_day = "2026-08-21" if stale else "2026-08-20"
        session_path, session_sha = session_evidence(root, session_day)
        return {
            "schema_version": "krx_investable_registry_input/1",
            "captured_at_utc": "2026-08-30T07:30:00Z",
            "latest_completed_session_date": session_day,
            "latest_session_evidence": {
                "source_name": "KRX_OFFICIAL_MARKET_SESSION_EVIDENCE",
                "as_of_date": session_day,
                "source_sha256": session_sha,
                "path": str(session_path),
            },
            "kis_parser_commit": M.load_contract()["kis_primary_source"]["parser_commit"],
            "masters": {
                "KOSPI": {
                    "path": str(kospi_path),
                    "source_url": M.load_contract()["kis_primary_source"]["master_urls"]["KOSPI"],
                    "retrieved_at_utc": "2026-08-30T07:21:45Z",
                    "http_last_modified": "2026-08-29T09:55:03Z",
                },
                "KOSDAQ": {
                    "path": str(kosdaq_path),
                    "source_url": M.load_contract()["kis_primary_source"]["master_urls"]["KOSDAQ"],
                    "retrieved_at_utc": "2026-08-30T07:21:45Z",
                    "http_last_modified": "2026-08-29T09:55:03Z",
                },
            },
            "krx_packet_path": str(packet_path),
            "previous_registry_path": previous,
        }

    def test_common_stock_etf_preferred_and_spac_are_separate(self):
        with tempfile.TemporaryDirectory() as raw:
            registry, public = M.build_registry(self.make_inputs(Path(raw)))
        records = {row["short_code"]: row for row in registry["records"]}
        self.assertEqual(records["005930"]["product_type"], "COMMON_STOCK")
        self.assertEqual(records["005930"]["screening_state"], "CATEGORICAL_CANDIDATE")
        self.assertEqual(records["069500"]["product_type"], "ETF")
        self.assertEqual(records["069500"]["screening_state"], "CATEGORICAL_CANDIDATE")
        self.assertEqual(records["005935"]["product_type"], "PREFERRED_STOCK")
        self.assertEqual(records["005935"]["screening_state"], "EXCLUDED")
        self.assertEqual(records["123450"]["product_type"], "SPAC")
        self.assertEqual(records["123450"]["screening_state"], "EXCLUDED")
        self.assertEqual(public["summary"]["screening_counts"], {
            "CATEGORICAL_CANDIDATE": 3, "EXCLUDED": 2, "UNKNOWN": 0
        })

    def test_candidate_is_not_decision_eligible_without_measurements_policy_and_history(self):
        with tempfile.TemporaryDirectory() as raw:
            registry, public = M.build_registry(self.make_inputs(Path(raw), stale=True))
        samsung = next(row for row in registry["records"] if row["short_code"] == "005930")
        self.assertEqual(samsung["decision_eligibility"], "UNKNOWN")
        self.assertIn("KRX_SNAPSHOT_NOT_LATEST_COMPLETED_SESSION", samsung["decision_blocker_codes"])
        self.assertIn("SPREAD_MEASUREMENT_MISSING", samsung["decision_blocker_codes"])
        self.assertIn("LIQUIDITY_AND_EXECUTION_THRESHOLDS_UNRATIFIED", samsung["decision_blocker_codes"])
        self.assertEqual(public["summary"]["decision_counts"]["ELIGIBLE"], 0)
        self.assertEqual(public["krx_snapshot_freshness"], "STALE")

    def test_status_flags_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kospi = [kis_line("KOSPI", "005930", "KR7005930003", "삼성전자", trading_halt="Y")]
            kosdaq = [kis_line("KOSDAQ", "035720", "KR7035720002", "카카오", investment_attention="Y")]
            registry, _ = M.build_registry(self.make_inputs(root, kospi_rows=kospi, kosdaq_rows=kosdaq))
        records = {row["short_code"]: row for row in registry["records"]}
        self.assertEqual(records["005930"]["screening_state"], "EXCLUDED")
        self.assertIn("KIS_TRADING_HALT", records["005930"]["eligibility_reason_codes"])
        self.assertEqual(records["035720"]["screening_state"], "EXCLUDED")
        self.assertIn("KIS_KOSDAQ_INVESTMENT_ATTENTION", records["035720"]["eligibility_reason_codes"])

    def test_undocumented_codes_become_unknown_without_name_inference(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            kospi = [kis_line("KOSPI", "Q520100", "KRG520100001", "이름에 ETN", security_group="EN", etp_code="9")]
            kosdaq = [kis_line("KOSDAQ", "035720", "KR7035720002", "카카오")]
            inputs = self.make_inputs(root, kospi_rows=kospi, kosdaq_rows=kosdaq)
            write_json(
                Path(inputs["krx_packet_path"]),
                krx_packet([("005930", "삼성전자")], [("035720", "카카오")]),
            )
            registry, _ = M.build_registry(inputs)
        target = next(row for row in registry["records"] if row["short_code"] == "Q520100")
        self.assertEqual(target["product_type"], "UNKNOWN")
        self.assertEqual(target["screening_state"], "UNKNOWN")
        self.assertIn("KIS_SECURITY_GROUP_UNDOCUMENTED:EN", target["eligibility_reason_codes"])

    def test_duplicates_fail_entire_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            duplicated = "005930"
            kospi = [kis_line("KOSPI", duplicated, "KR7005930003", "삼성전자")]
            kosdaq = [kis_line("KOSDAQ", duplicated, "KR7035720002", "카카오")]
            with self.assertRaisesRegex(M.RegistryError, "CURRENT_SHORT_CODE_DUPLICATE"):
                M.build_registry(self.make_inputs(root, kospi_rows=kospi, kosdaq_rows=kosdaq))

    def test_short_code_reuse_is_unknown_against_prior_snapshot(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first, _ = M.build_registry(self.make_inputs(root))
            prior_path = write_json(root / "prior.json", first)
            reused_standard = "KR7005939996"
            kospi = [kis_line("KOSPI", "005930", reused_standard, "새회사")]
            kosdaq = [kis_line("KOSDAQ", "035720", "KR7035720002", "카카오")]
            inputs = self.make_inputs(
                root, previous=str(prior_path), kospi_rows=kospi, kosdaq_rows=kosdaq
            )
            write_json(
                Path(inputs["krx_packet_path"]),
                krx_packet([("005930", "새회사")], [("035720", "카카오")]),
            )
            second, _ = M.build_registry(inputs)
        target = next(row for row in second["records"] if row["short_code"] == "005930")
        self.assertEqual(target["code_reuse_status"], "REUSED")
        self.assertEqual(target["screening_state"], "UNKNOWN")
        self.assertEqual(second["summary"]["code_reuse_count"], 1)

    def test_rehashed_krx_semantic_tamper_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = self.make_inputs(root)
            packet = json.loads(Path(inputs["krx_packet_path"]).read_text())
            packet["asset_master"]["records"][0]["investable_eligible"] = True
            packet["asset_master"]["payload_sha256"] = M.GAM.payload_sha256({
                key: value for key, value in packet["asset_master"].items() if key != "payload_sha256"
            })
            packet["payload_sha256"] = M.KRU.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            })
            write_json(Path(inputs["krx_packet_path"]), packet)
            with self.assertRaisesRegex(M.RegistryError, "KRX_ASSET_MASTER_INVALID"):
                M.build_registry(inputs)

    def test_rehashed_krx_outer_authority_promotion_fails(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = self.make_inputs(root)
            packet = json.loads(Path(inputs["krx_packet_path"]).read_text())
            packet["authority"]["trading_authorized"] = True
            packet["payload_sha256"] = M.KRU.payload_sha256({
                key: value for key, value in packet.items() if key != "payload_sha256"
            })
            write_json(Path(inputs["krx_packet_path"]), packet)
            with self.assertRaisesRegex(M.RegistryError, "KRX_PACKET_AUTHORITY_MISMATCH"):
                M.build_registry(inputs)

    def test_public_summary_contains_no_symbol_rows_or_names(self):
        with tempfile.TemporaryDirectory() as raw:
            registry, public = M.build_registry(self.make_inputs(Path(raw)))
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("records", public)
        self.assertNotIn("삼성전자", serialized)
        self.assertNotIn("005930", serialized)
        self.assertEqual(public["private_registry_payload_sha256"], registry["payload_sha256"])
        self.assertFalse(public["authority"]["paper_order_authorized"])
        self.assertFalse(public["authority"]["real_order_authorized"])

    def test_public_summary_is_non_authority_evidence_for_merged_krx_gate(self):
        with tempfile.TemporaryDirectory() as raw:
            registry, public = M.build_registry(self.make_inputs(Path(raw)))
        compatibility = public["krx_paper_gate_compatibility"]
        self.assertEqual(
            compatibility["contract_versions"],
            {
                "common_safety": "krx_paper_common_safety_gate/1",
                "krx_market": "krx_paper_market_gate/1",
            },
        )
        self.assertEqual(
            compatibility["evidence_targets"],
            [
                "COMMON_PIT_AND_IMMUTABLE_LINEAGE",
                "KRX_FINAL_CANDIDATE_POLICY_RATIFIED",
            ],
        )
        self.assertEqual(compatibility["evidence_role"], "NON_AUTHORITY_EVIDENCE_CANDIDATE")
        self.assertEqual(compatibility["evidence_state"], "INSUFFICIENT")
        self.assertIn(
            "KRX_FINAL_CANDIDATE_AUTHORITY_UNRATIFIED",
            compatibility["evidence_reason_codes"],
        )
        self.assertIsNone(compatibility["current_state_claim"])
        self.assertFalse(compatibility["gate_result_authorized"])
        self.assertFalse(compatibility["state_transition_authorized"])
        self.assertTrue(all(value is False for value in compatibility["authority"].values()))
        self.assertEqual(
            compatibility["contract_sha256"],
            {
                "common_safety": "83ceb7bfcb05d5b8c492b6d98c8a2f0d73274c87a228a9369291db95adef8411",
                "krx_market": "e8275ac083c5624946718da0dc7db7f01e9700a82aa330e6d306ab734e73cd3f",
            },
        )
        self.assertEqual(
            registry["krx_paper_gate_compatibility"],
            compatibility,
        )

    def test_contract_rejects_threshold_or_authority_promotion(self):
        contract = M.load_contract()
        for mutation, reason in (
            (("authority", "paper_order_authorized", True), "CONTRACT_AUTHORITY_PROMOTED"),
            (("measurement_policy", "spread", {"status": "RATIFIED", "proposed_threshold": 0.01}), "CONTRACT_MEASUREMENT_THRESHOLD_RATIFIED"),
        ):
            changed = copy.deepcopy(contract)
            changed[mutation[0]][mutation[1]] = mutation[2]
            with tempfile.TemporaryDirectory() as raw:
                path = write_json(Path(raw) / "contract.json", changed)
                with self.assertRaisesRegex(M.RegistryError, reason):
                    M.load_contract(path)

    def test_contract_rejects_krx_gate_authority_promotion(self):
        changed = M.load_contract()
        changed["krx_paper_gate_compatibility"]["gate_result_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = write_json(Path(raw) / "contract.json", changed)
            with self.assertRaisesRegex(
                M.RegistryError,
                "CONTRACT_KRX_PAPER_GATE_COMPATIBILITY_INVALID",
            ):
                M.load_contract(path)

    def test_source_url_archive_shape_and_latest_session_evidence_are_exact(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = self.make_inputs(root)
            bad = copy.deepcopy(inputs)
            bad["masters"]["KOSPI"]["source_url"] = "https://example.invalid/master.zip"
            with self.assertRaisesRegex(M.RegistryError, "MASTER_URL_MISMATCH"):
                M.build_registry(bad)
            bad = copy.deepcopy(inputs)
            bad["latest_session_evidence"]["as_of_date"] = "2026-08-19"
            with self.assertRaisesRegex(M.RegistryError, "LATEST_SESSION_EVIDENCE_DATE_MISMATCH"):
                M.build_registry(bad)
            bad = copy.deepcopy(inputs)
            bad["latest_session_evidence"]["source_sha256"] = "0" * 64
            with self.assertRaisesRegex(M.RegistryError, "LATEST_SESSION_EVIDENCE_FILE_SHA_MISMATCH"):
                M.build_registry(bad)
            wrong_zip = root / "wrong.zip"
            with zipfile.ZipFile(wrong_zip, "w") as archive:
                archive.writestr("wrong.mst", b"x")
            bad = copy.deepcopy(inputs)
            bad["masters"]["KOSPI"]["path"] = str(wrong_zip)
            with self.assertRaisesRegex(M.RegistryError, "MASTER_ARCHIVE_MEMBERS_MISMATCH"):
                M.build_registry(bad)

    def test_execution_evidence_removes_only_measured_blockers_and_never_promotes(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            inputs = self.make_inputs(root)
            base, _ = M.build_registry(inputs)
            registry_path = write_json(root / "base-registry.json", base)
            krx_sources = []
            for market, code in (("KOSPI", "005930"), ("KOSDAQ", "035720")):
                path = write_json(root / f"{market}.json", {"OutBlock_1": [{
                    "BAS_DD": "20260820",
                    "ISU_CD": code,
                    "MKT_NM": market,
                    "ACC_TRDVAL": "1000000",
                }]})
                krx_sources.append({
                    "market": market,
                    "path": str(path),
                    "source_url": E.load_contract()["krx_turnover_source"]["market_endpoints"][market],
                    "retrieved_at_utc": "2026-08-30T09:00:00Z",
                })
            captures = []
            for row in base["records"]:
                if row["screening_state"] != "CATEGORICAL_CANDIDATE":
                    continue
                output = {"aspr_acpt_hour": "151500"}
                for level in range(1, 11):
                    output[f"askp{level}"] = str(10000 + level * 10)
                    output[f"bidp{level}"] = str(10000 - level * 10)
                    output[f"askp_rsqn{level}"] = str(level * 100)
                    output[f"bidp_rsqn{level}"] = str(level * 110)
                captures.append({
                    "security_id": row["security_id"],
                    "captured_at_utc": "2026-08-20T06:15:05Z",
                    "http_method": "GET",
                    "endpoint_path": E.load_contract()["kis_order_book_source"]["endpoint_path"],
                    "tr_id": "FHKST01010200",
                    "venue_code": "J",
                    "response": {"rt_cd": "0", "output1": output},
                })
            capture_path = write_json(root / "orderbooks.json", {
                "schema_version": "kis_domestic_order_book_capture/1",
                "session_date": "2026-08-20",
                "session_state": "COMPLETED",
                "completed_session_evidence_sha256": inputs["latest_session_evidence"]["source_sha256"],
                "captures": captures,
            })
            private, _ = E.build_measurements({
                "schema_version": "krx_execution_measurement_input/1",
                "captured_at_utc": "2026-08-30T10:10:00Z",
                "completed_session_date": "2026-08-20",
                "registry_path": str(registry_path),
                "krx_turnover_snapshots": krx_sources,
                "kis_order_book_capture_path": str(capture_path),
            })
            evidence_path = write_json(root / "execution-evidence.json", private)
            integrated_inputs = copy.deepcopy(inputs)
            integrated_inputs["execution_evidence_path"] = str(evidence_path)
            integrated, public = M.build_registry(integrated_inputs)
        samsung = next(row for row in integrated["records"] if row["short_code"] == "005930")
        etf = next(row for row in integrated["records"] if row["short_code"] == "069500")
        self.assertNotIn("TURNOVER_MEASUREMENT_MISSING", samsung["decision_blocker_codes"])
        self.assertNotIn("SPREAD_MEASUREMENT_MISSING", samsung["decision_blocker_codes"])
        self.assertIn("TURNOVER_MEASUREMENT_MISSING", etf["decision_blocker_codes"])
        self.assertNotIn("SLIPPAGE_MEASUREMENT_MISSING", etf["decision_blocker_codes"])
        self.assertIn("LIQUIDITY_AND_EXECUTION_THRESHOLDS_UNRATIFIED", samsung["decision_blocker_codes"])
        self.assertEqual(public["summary"]["measurement_coverage"], {
            "turnover": 2,
            "order_book_depth": 3,
            "spread": 3,
            "slippage": 3,
        })
        self.assertEqual(public["summary"]["decision_counts"]["ELIGIBLE"], 0)
        self.assertNotIn("records", public)


if __name__ == "__main__":
    unittest.main(verbosity=2)
