"""Read-only KRX/KIS execution measurement regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def load(name: str, relative: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


M = load("krx_execution_measurements", "universe/krx_execution_measurements.py")


def write_json(path: Path, value: dict) -> Path:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def registry(root: Path, count: int = 5) -> Path:
    records = []
    for index in range(count):
        short = f"{5930 + index:06d}"
        records.append({
            "security_id": f"KR:XKRX:KR7{short}003",
            "short_code": short,
            "market": "KOSPI",
            "screening_state": "CATEGORICAL_CANDIDATE",
        })
    value = {
        "schema_version": "krx_investable_registry/1",
        "latest_completed_session_date": "2026-08-28",
        "latest_session_evidence": {"source_sha256": "1" * 64},
        "krx_snapshot_as_of_date": "2026-08-28",
        "krx_snapshot_freshness": "CURRENT",
        "authority": {"real_order_authorized": False},
        "records": records,
    }
    value["payload_sha256"] = M.payload_sha256(value)
    return write_json(root / "registry.json", value)


def krx_snapshot(root: Path, count: int = 5) -> Path:
    rows = []
    for index in range(count):
        rows.append({
            "BAS_DD": "20260828",
            "ISU_CD": f"{5930 + index:06d}",
            "MKT_NM": "KOSPI",
            "ACC_TRDVAL": str((index + 1) * 1000000),
        })
    return write_json(root / "krx.json", {"OutBlock_1": rows})


def order_output(offset: int = 0) -> dict:
    output = {"aspr_acpt_hour": "151500"}
    for level in range(1, 11):
        output[f"askp{level}"] = str(10000 + offset + level * 10)
        output[f"bidp{level}"] = str(10000 + offset - level * 10)
        output[f"askp_rsqn{level}"] = str(100 * level)
        output[f"bidp_rsqn{level}"] = str(110 * level)
    return output


def capture(root: Path, count: int = 5, **capture_overrides) -> Path:
    captures = []
    for index in range(count):
        short = f"{5930 + index:06d}"
        row = {
            "security_id": f"KR:XKRX:KR7{short}003",
            "captured_at_utc": "2026-08-28T06:15:05Z",
            "http_method": "GET",
            "endpoint_path": "/uapi/domestic-stock/v1/quotations/inquire-asking-price-exp-ccn",
            "tr_id": "FHKST01010200",
            "venue_code": "J",
            "response": {"rt_cd": "0", "output1": order_output(index)},
        }
        row.update(capture_overrides)
        captures.append(row)
    value = {
        "schema_version": "kis_domestic_order_book_capture/1",
        "session_date": "2026-08-28",
        "session_state": "COMPLETED",
        "completed_session_evidence_sha256": "1" * 64,
        "captures": captures,
    }
    return write_json(root / "orderbooks.json", value)


def inputs(root: Path, count: int = 5, *, with_krx=True, with_books=True) -> dict:
    value = {
        "schema_version": "krx_execution_measurement_input/1",
        "captured_at_utc": "2026-08-30T10:10:00Z",
        "completed_session_date": "2026-08-28",
        "registry_path": str(registry(root, count)),
        "krx_turnover_snapshots": [],
        "kis_order_book_capture_path": None,
    }
    if with_krx:
        value["krx_turnover_snapshots"] = [{
            "market": "KOSPI",
            "path": str(krx_snapshot(root, count)),
            "source_url": "https://data-dbg.krx.co.kr/svc/apis/sto/stk_bydd_trd",
            "retrieved_at_utc": "2026-08-30T10:05:14Z",
        }]
    if with_books:
        value["kis_order_book_capture_path"] = str(capture(root, count))
    return value


class KrxExecutionMeasurementTests(unittest.TestCase):
    def test_measures_turnover_depth_spread_and_source_derived_slippage_curve(self):
        with tempfile.TemporaryDirectory() as raw:
            private, public = M.build_measurements(inputs(Path(raw)))
        self.assertEqual(public["coverage"], {
            "candidate_count": 5,
            "turnover": 5,
            "order_book_depth": 5,
            "spread": 5,
            "slippage": 5,
        })
        record = private["records"][0]
        self.assertEqual(len(record["order_book"]["buy_slippage_curve"]), 10)
        self.assertEqual(record["order_book"]["buy_slippage_curve"][0]["impact_bps"], "0")
        self.assertIsNone(public["policy_candidates"]["slippage"]["order_notional_krw"])
        self.assertFalse(public["authority"]["broker_post_authorized"])
        self.assertFalse(public["authority"]["real_capital_authorized"])

    def test_zero_inputs_report_zero_coverage_without_fabrication(self):
        with tempfile.TemporaryDirectory() as raw:
            private, public = M.build_measurements(
                inputs(Path(raw), with_krx=False, with_books=False)
            )
        self.assertEqual(public["coverage"]["turnover"], 0)
        self.assertEqual(public["coverage"]["spread"], 0)
        self.assertTrue(public["measured_distributions"]["turnover_krw"]["suppressed"])
        self.assertTrue(all(row["turnover_krw"] is None for row in private["records"]))

    def test_public_output_contains_no_symbol_identity_or_raw_rows(self):
        with tempfile.TemporaryDirectory() as raw:
            private, public = M.build_measurements(inputs(Path(raw)))
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("records", public)
        self.assertNotIn("005930", serialized)
        self.assertNotIn("askp1", serialized)
        self.assertEqual(public["private_measurement_payload_sha256"], private["payload_sha256"])

    def test_small_sample_distribution_is_suppressed(self):
        with tempfile.TemporaryDirectory() as raw:
            _, public = M.build_measurements(inputs(Path(raw), count=1))
        distribution = public["measured_distributions"]["spread_bps"]
        self.assertEqual(distribution["sample_count"], 1)
        self.assertTrue(distribution["suppressed"])
        self.assertIsNone(distribution["p50"])

    def test_post_nxt_crossed_and_wrong_session_fail_closed(self):
        mutations = [
            ({"http_method": "POST"}, "KIS_CAPTURE_READ_ONLY_BOUNDARY_INVALID"),
            ({"venue_code": "NX"}, "KIS_CAPTURE_SOURCE_IDENTITY_INVALID"),
        ]
        for change, reason in mutations:
            with self.subTest(reason=reason), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                value = inputs(root, with_books=False)
                value["kis_order_book_capture_path"] = str(capture(root, **change))
                with self.assertRaisesRegex(M.MeasurementError, reason):
                    M.build_measurements(value)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = inputs(root)
            packet = json.loads(Path(value["kis_order_book_capture_path"]).read_text())
            packet["captures"][0]["response"]["output1"]["askp1"] = "9000"
            write_json(Path(value["kis_order_book_capture_path"]), packet)
            with self.assertRaisesRegex(M.MeasurementError, "KIS_ASK_LEVEL_ORDER_INVALID|KIS_ORDER_BOOK_CROSSED"):
                M.build_measurements(value)

    def test_contract_rejects_policy_or_authority_promotion(self):
        for change, reason in (
            (("policy_candidates", "spread", "maximum_spread_bps", 50), "CONTRACT_POLICY_THRESHOLD_SET"),
            (("authority", "broker_post_authorized", None, True), "CONTRACT_AUTHORITY_PROMOTED"),
        ):
            contract = M.load_contract()
            if change[2] is None:
                contract[change[0]][change[1]] = change[3]
            else:
                contract[change[0]][change[1]][change[2]] = change[3]
            with tempfile.TemporaryDirectory() as raw:
                path = write_json(Path(raw) / "contract.json", contract)
                with self.assertRaisesRegex(M.MeasurementError, reason):
                    M.load_contract(path)

    def test_rehashed_private_packet_cannot_inject_public_distribution_or_wrong_date(self):
        with tempfile.TemporaryDirectory() as raw:
            private, _ = M.build_measurements(inputs(Path(raw)))
        injected = copy.deepcopy(private)
        injected["measured_distributions"]["spread_bps"]["p50"] = "999999"
        injected["payload_sha256"] = M.payload_sha256({
            key: value for key, value in injected.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(M.MeasurementError, "PRIVATE_PACKET_DISTRIBUTION_MISMATCH"):
            M.validate_private_packet(injected)
        wrong_date = copy.deepcopy(private)
        wrong_date["records"][0]["order_book"]["captured_at_utc"] = "2026-08-27T06:15:05Z"
        wrong_date["payload_sha256"] = M.payload_sha256({
            key: value for key, value in wrong_date.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(M.MeasurementError, "PRIVATE_PACKET_ORDER_BOOK_DATE_MISMATCH"):
            M.validate_private_packet(wrong_date)


if __name__ == "__main__":
    unittest.main(verbosity=2)
