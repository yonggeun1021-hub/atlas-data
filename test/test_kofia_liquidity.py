#!/usr/bin/env python3
"""P1-KR-03 KOFIA liquidity source qualification regression.

No live API call or tracked output is allowed.  Every response is a synthetic
fixture written under a temporary directory.
"""

import hashlib
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "kofia_liquidity.py"
CONTRACT_PATH = ROOT / "config" / "kofia_liquidity_contract.json"
SPEC = importlib.util.spec_from_file_location("kofia_liquidity", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract(CONTRACT_PATH)


def investor_row(date="20260814", value=100710332):
    return {
        "onbdDrvPrdTrRcAdvAmt": 100,
        "toCstRpchCndBndSlgBal": 200,
        "brkTrdUcolMny": 300,
        "brkTrdUcolMnyVsOppsTrdAmt": 400,
        "ucolMnyVsOppsTrdRlImpt": 5.25,
        "basDt": date,
        "invrDpsgAmt": value,
    }


def credit_row(date="20260814", value=30867510):
    return {
        "basDt": date,
        "crdTrFingWhl": value,
        "crdTrFingScrs": 17000000,
        "crdTrFingKosdaq": 13867510,
        "crdTrLndrWhl": 1000,
        "crdTrLndrScrs": 600,
        "crdTrLndrKosdaq": 400,
        "sbscCapLn": 2000,
        "dpsgScrtMogFing": 3000,
    }


def response(rows, wrapped=True, **body_overrides):
    body = {
        "numOfRows": len(rows),
        "pageNo": 1,
        "totalCount": len(rows),
        "items": {"item": rows},
    }
    body.update(body_overrides)
    payload = {
        "header": {"resultCode": "00", "resultMsg": "NORMAL SERVICE."},
        "body": body,
    }
    return {"response": payload} if wrapped else payload


def write_json(directory, name, payload):
    path = Path(directory) / name
    path.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    return path


def fixture_paths(directory, investor=None, credit=None):
    investor = response(
        [investor_row("20260813", 100000000), investor_row()]
    ) if investor is None else investor
    credit = response(
        [credit_row("20260813", 30000000), credit_row()]
    ) if credit is None else credit
    return {
        "investor_deposits": write_json(directory, "investor.json", investor),
        "credit_financing": write_json(directory, "credit.json", credit),
    }


class KofiaLiquidityContractTest(unittest.TestCase):
    def test_contract_fixes_official_operations_and_unverified_boundaries(self):
        operations = {item["name"]: item for item in CONTRACT["operations"]}

        self.assertEqual(
            CONTRACT["contract_version"],
            "kofia_liquidity_source/v2",
        )
        self.assertEqual(CONTRACT["catalog_id"], "15094809")
        self.assertEqual(
            operations["investor_deposits"]["operation_id"],
            "getSecuritiesMarketTotalCapitalInfo",
        )
        self.assertEqual(
            operations["investor_deposits"]["primary_value_field"],
            "invrDpsgAmt",
        )
        self.assertEqual(
            operations["credit_financing"]["operation_id"],
            "getGrantingOfCreditBalanceInfo",
        )
        self.assertEqual(
            operations["credit_financing"]["primary_value_field"],
            "crdTrFingWhl",
        )
        self.assertEqual(
            CONTRACT["qualification"]["historical_range_status"],
            "unverified",
        )
        self.assertEqual(
            CONTRACT["qualification"]["source_release_time_status"],
            "unverified",
        )
        self.assertFalse(CONTRACT["qualification"]["decision_eligible"])
        self.assertEqual(
            CONTRACT["numeric_transport_policy"],
            MODULE.EXPECTED_NUMERIC_TRANSPORT_POLICY,
        )

    def test_contract_schema_drift_is_rejected(self):
        tampered = json.loads(json.dumps(CONTRACT))
        tampered["operations"][0]["primary_value_field"] = "wrongField"

        with tempfile.TemporaryDirectory() as tmp:
            path = write_json(tmp, "contract.json", tampered)
            with self.assertRaisesRegex(
                MODULE.KofiaContractError,
                "CONTRACT_INVALID: official operation schema",
            ):
                MODULE.load_contract(path)

    def test_complete_responses_build_evidence_without_available_at(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = fixture_paths(tmp)
            report = MODULE.build_qualification(
                paths,
                "2026-08-19T05:00:00Z",
                contract=CONTRACT,
            )

            self.assertEqual(
                report["coverage_evidence_status"],
                "complete_response_observed",
            )
            self.assertEqual(report["historical_range_status"], "unverified")
            self.assertEqual(
                report["source_release_time_status"],
                "unverified",
            )
            self.assertIsNone(report["available_at"])
            self.assertFalse(report["decision_eligible"])
            self.assertFalse(report["regime_score_authorized"])
            self.assertFalse(report["production_wiring_authorized"])
            self.assertFalse(report["trading_action_authorized"])
            deposits = report["operations"]["investor_deposits"]
            self.assertEqual(deposits["row_count"], 2)
            self.assertEqual(
                deposits["earliest_observation_date"],
                "2026-08-13",
            )
            self.assertEqual(
                deposits["latest_observation_date"],
                "2026-08-14",
            )
            self.assertEqual(deposits["latest_primary_value_raw"], "100710332")
            self.assertEqual(
                deposits["response_sha256"],
                hashlib.sha256(paths["investor_deposits"].read_bytes()).hexdigest(),
            )
            self.assertEqual(deposits["api_field_unit_status"], "unverified")

    def test_documented_root_and_common_response_wrapper_are_both_accepted(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = fixture_paths(
                tmp,
                investor=response([investor_row()], wrapped=False),
                credit=response([credit_row()], wrapped=True),
            )

            report = MODULE.build_qualification(
                paths,
                "2026-08-19T05:00:00Z",
                contract=CONTRACT,
            )

            self.assertEqual(
                set(report["operations"]),
                {"investor_deposits", "credit_financing"},
            )

    def test_single_item_object_is_normalized_without_weakening_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            investor = response([investor_row()])
            investor["response"]["body"]["items"]["item"] = investor_row()
            paths = fixture_paths(tmp, investor=investor)

            report = MODULE.build_qualification(
                paths,
                "2026-08-19T05:00:00Z",
                contract=CONTRACT,
            )

            self.assertEqual(
                report["operations"]["investor_deposits"]["row_count"],
                1,
            )

    def test_partial_page_cannot_claim_historical_coverage(self):
        with tempfile.TemporaryDirectory() as tmp:
            partial = response([investor_row()], totalCount=2)
            paths = fixture_paths(tmp, investor=partial)

            with self.assertRaisesRegex(
                MODULE.KofiaContractError,
                "COVERAGE_PROBE_INCOMPLETE",
            ):
                MODULE.build_qualification(
                    paths,
                    "2026-08-19T05:00:00Z",
                    contract=CONTRACT,
                )

    def test_schema_date_numeric_duplicate_and_source_errors_fail_closed(self):
        cases = []

        missing = investor_row()
        missing.pop("invrDpsgAmt")
        cases.append((response([missing]), "ROW_SCHEMA_INVALID"))

        negative = investor_row(value=-1)
        cases.append((response([negative]), "VALUE_INVALID"))

        bad_date = investor_row(date="20260230")
        cases.append((response([bad_date]), "OBSERVATION_DATE_INVALID"))

        duplicate = response([investor_row(), investor_row()])
        cases.append((duplicate, "OBSERVATION_DATE_DUPLICATE"))

        source_error = response([investor_row()])
        source_error["response"]["header"] = {
            "resultCode": "22",
            "resultMsg": "LIMIT EXCEEDED",
        }
        cases.append((source_error, "SOURCE_ERROR"))

        for investor, code in cases:
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                paths = fixture_paths(tmp, investor=investor)
                with self.assertRaisesRegex(MODULE.KofiaContractError, code):
                    MODULE.build_qualification(
                        paths,
                        "2026-08-19T05:00:00Z",
                        contract=CONTRACT,
                    )

    def test_unexpected_value_diagnostic_reports_shape_without_value(self):
        secret_like_value = "12345678901234567890e0"

        with self.assertRaises(MODULE.KofiaContractError) as caught:
            MODULE.parse_nonnegative_number(secret_like_value, "field")

        message = str(caught.exception)
        self.assertIn("VALUE_TEXT_INVALID: field", message)
        self.assertIn("observed=str(length=22,stripped_length=22", message)
        self.assertIn("decimal_text=false", message)
        self.assertNotIn(secret_like_value, message)

        self.assertEqual(MODULE.safe_value_shape(" - "), (
            "str(length=3,stripped_length=1,decimal_text=false,"
            "grouped_decimal_text=false,"
            "character_classes=whitespace,minus,whitespace)"
        ))
        self.assertEqual(MODULE.safe_value_shape(None), "null")
        self.assertIn("character_classes=dot,digit", MODULE.safe_value_shape(".0"))
        self.assertIn("character_classes=minus,digit", MODULE.safe_value_shape("-1"))
        self.assertIn("character_classes=digit,percent", MODULE.safe_value_shape("0%"))
        self.assertIn("character_classes=minus*2", MODULE.safe_value_shape("--"))
        self.assertIn(
            "character_classes=digit,exponent,digit",
            MODULE.safe_value_shape("1e3"),
        )

    def test_canonical_numeric_text_is_normalized_and_other_text_fails_closed(self):
        accepted = {
            "0": Decimal("0"),
            "123": Decimal("123"),
            "0.25": Decimal("0.25"),
            "123.00": Decimal("123.00"),
        }
        for raw, expected in accepted.items():
            with self.subTest(raw=raw):
                self.assertEqual(
                    MODULE.parse_nonnegative_number(raw, "field"),
                    expected,
                )

        rejected = ["", " ", "-1", "+1", "01", "1e3", "1,000", "NaN", "-"]
        for raw in rejected:
            with self.subTest(raw=raw), self.assertRaisesRegex(
                MODULE.KofiaContractError,
                "VALUE_TEXT_INVALID",
            ):
                MODULE.parse_nonnegative_number(raw, "field")

        with self.assertRaisesRegex(
            MODULE.KofiaContractError,
            "VALUE_TYPE_INVALID",
        ):
            MODULE.parse_nonnegative_number(None, "field")

        investor = investor_row(value="100.50")
        investor = {
            key: (str(value) if key != "basDt" else value)
            for key, value in investor.items()
        }
        credit = credit_row()
        credit = {
            key: (str(value) if key != "basDt" else value)
            for key, value in credit.items()
        }
        with tempfile.TemporaryDirectory() as tmp:
            paths = fixture_paths(
                tmp,
                investor=response([investor]),
                credit=response([credit]),
            )
            report = MODULE.build_qualification(
                paths,
                "2026-08-19T05:00:00Z",
                contract=CONTRACT,
            )

        self.assertEqual(
            report["operations"]["investor_deposits"]["latest_primary_value_raw"],
            "100.50",
        )

    def test_future_observation_and_bad_capture_time_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = fixture_paths(
                tmp,
                investor=response([investor_row("20260820")]),
            )
            with self.assertRaisesRegex(
                MODULE.KofiaContractError,
                "OBSERVATION_FROM_FUTURE",
            ):
                MODULE.build_qualification(
                    paths,
                    "2026-08-19T05:00:00Z",
                    contract=CONTRACT,
                )
            with self.assertRaisesRegex(
                MODULE.KofiaContractError,
                "CAPTURE_TIME_INVALID",
            ):
                MODULE.build_qualification(
                    paths,
                    "2026-08-19T05:00:00+00:00",
                    contract=CONTRACT,
                )

    def test_report_writer_forbids_tracked_and_existing_outputs(self):
        payload = {"schema_version": 1}
        with self.assertRaisesRegex(
            MODULE.KofiaContractError,
            "TRACKED_OUTPUT_FORBIDDEN",
        ):
            MODULE.write_qualification(
                payload,
                ROOT / "data" / "kofia-qualification.json",
            )

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "qualification.json"
            MODULE.write_qualification(payload, target)
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                payload,
            )
            with self.assertRaisesRegex(
                MODULE.KofiaContractError,
                "OUTPUT_EXISTS",
            ):
                MODULE.write_qualification(payload, target)

    def test_script_has_no_live_network_or_service_key_access(self):
        source = SCRIPT.read_text(encoding="utf-8")

        self.assertNotIn("import requests", source)
        self.assertNotIn("import urllib", source)
        self.assertNotIn("urlopen", source)
        self.assertNotIn("SERVICE_KEY", source)
        self.assertNotIn("os.environ", source)


if __name__ == "__main__":
    unittest.main()
