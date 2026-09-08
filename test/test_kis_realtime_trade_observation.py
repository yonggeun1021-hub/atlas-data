"""Contract tests use synthetic rows only and make no actual-source claim."""
import copy
import ast
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data import kis_realtime_trade_observation as O
from market_data import krx_session_bars as KRX


def encoded(value):
    return O.canonical_bytes(value)


def source_fields(symbol, trade_time, business_date="20260908"):
    fields = ["0"] * 46
    updates = {
        0: symbol, 1: trade_time, 2: "73500", 4: "1200", 5: "1.66",
        7: "72400", 8: "74000", 9: "72000", 10: "73600",
        11: "73500", 12: "25", 13: "1234567", 33: business_date,
    }
    for index, value in updates.items():
        fields[index] = value
    return fields


def row(record_id, symbol, trade_time):
    observed = f"2026-09-08T00:05:0{record_id}+00:00"
    fields = source_fields(symbol, trade_time)
    source = json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
    parsed_value = {
        "symbol": symbol, "trade_time": trade_time, "price": 73500,
        "change": 1200, "change_rate": 1.66, "open": 72400,
        "high": 74000, "low": 72000, "ask": 73600, "bid": 73500,
        "trade_volume": 25, "cumulative_volume": 1234567,
        "business_date": "20260908", "observed_at": observed,
    }
    parsed = json.dumps(parsed_value, ensure_ascii=False, separators=(",", ":"))
    return {
        "record_id": record_id,
        "schema_version": "kis_quote_observation_record/1",
        "source_kind": "DECODED_TRADE_RECORD",
        "provider_tr_id": "H0STCNT0",
        "source_record_json": source,
        "source_record_sha256": O.digest(source.encode()),
        "parsed_quote_json": parsed,
        "parsed_quote_sha256": O.digest(parsed.encode()),
        "observed_at": observed,
        "record_attempted_at": f"2026-09-08T00:05:1{record_id}+00:00",
    }


def calendar(status="OPEN_REGULAR"):
    day = "2026-09-08"
    source_ref = "fixture:ctca0903r:20260908"
    source_sha = O.digest(b"fixture calendar response")
    return {
        "schema_version": "krx_date_specific_session_source/1",
        "as_of_date": day,
        "official_response_ref": source_ref,
        "official_response_sha256": source_sha,
        "calendar": {
            "session_date": day, "status": status, "timezone": "Asia/Seoul",
            "open_at": day + "T09:00:00+09:00" if status == "OPEN_REGULAR" else None,
            "close_at": day + "T15:30:00+09:00" if status == "OPEN_REGULAR" else None,
            "observed_at": "2026-09-07T23:00:00+00:00",
            "available_at": "2026-09-07T23:00:00+00:00",
            "source_ref": source_ref, "source_sha256": source_sha,
            "provider_id": "KIS_OPEN_API_DOMESTIC_HOLIDAY_CTCA0903R",
            "market_rule_source": "KRX_EQUITY_MARKET_OPERATION_RULES",
        },
    }


def make_inputs():
    contract = O.load_contract()
    packet = {
        "schema_version": "kis_realtime_trade_row_packet/1",
        "evidence_class": "SYNTHETIC_OFFLINE_FIXTURE",
        "session_date": "2026-09-08",
        "rows": [
            row(1, "000660", "090501"),
            row(2, "005930", "090502"),
            row(3, "071050", "090503"),
        ],
    }
    packet_bytes = encoded(packet)
    owner = {
        "schema_version": "kis_realtime_trade_export_owner_receipt/1",
        "evidence_class": packet["evidence_class"],
        "market": "KOREA", "environment": "PAPER",
        "session_date": packet["session_date"],
        "owner_receipt_ref": "fixture:protected-export:1",
        "row_packet_sha256": O.digest(packet_bytes),
        "row_range": {"first": 1, "last": 3, "count": 3},
        "symbols": copy.deepcopy(contract["approved_symbols"]),
        "export_started_at": "2026-09-08T00:06:00+00:00",
        "export_available_at": "2026-09-08T00:06:01+00:00",
        "decoded_source_record_bytes_retained": True,
        "official_source": copy.deepcopy(contract["official_source"]),
        "deployed_source_pins": copy.deepcopy(contract["deployed_source_pins"]),
        "authority": copy.deepcopy(contract["authority"]),
    }
    calendar_bytes = encoded(calendar())
    owner_bytes = encoded(owner)
    return {
        "row_packet": packet_bytes,
        "expected_row_packet_sha256": O.digest(packet_bytes),
        "owner_receipt": owner_bytes,
        "expected_owner_receipt_sha256": O.digest(owner_bytes),
        "calendar_packet": calendar_bytes,
        "expected_calendar_packet_sha256": O.digest(calendar_bytes),
        "evaluation_at": "2026-09-08T00:06:02+00:00",
    }


def replace_packet(inputs, mutate, *, preserve_owner_range=True):
    packet = json.loads(inputs["row_packet"])
    mutate(packet)
    inputs["row_packet"] = encoded(packet)
    inputs["expected_row_packet_sha256"] = O.digest(inputs["row_packet"])
    owner = json.loads(inputs["owner_receipt"])
    owner["row_packet_sha256"] = inputs["expected_row_packet_sha256"]
    if not preserve_owner_range:
        ids = [row["record_id"] for row in packet["rows"]]
        owner["row_range"] = {"first": ids[0], "last": ids[-1], "count": len(ids)}
    inputs["owner_receipt"] = encoded(owner)
    inputs["expected_owner_receipt_sha256"] = O.digest(inputs["owner_receipt"])


def resign_row(row_value):
    row_value["source_record_sha256"] = O.digest(row_value["source_record_json"].encode())
    row_value["parsed_quote_sha256"] = O.digest(row_value["parsed_quote_json"].encode())


class KisRealtimeTradeObservationTests(unittest.TestCase):
    def test_caller_cannot_relax_repository_contract_or_authority(self):
        inputs = make_inputs()
        relaxed = O.load_contract()
        relaxed["authority"]["real_capital_authorized"] = True
        owner = json.loads(inputs["owner_receipt"])
        owner["authority"] = copy.deepcopy(relaxed["authority"])
        inputs["owner_receipt"] = encoded(owner)
        inputs["expected_owner_receipt_sha256"] = O.digest(inputs["owner_receipt"])
        inputs["contract"] = relaxed
        with self.assertRaisesRegex(
            O.KisRealtimeTradeObservationError,
            "CALLER_CONTRACT_OVERRIDE_REJECTED",
        ):
            O.qualify_kis_realtime_trade_observation(**inputs)

    def test_profile_has_no_provider_database_or_service_client(self):
        source = Path(O.__file__).read_text()
        tree = ast.parse(source)
        imported = {
            alias.name.split(".")[0]
            for node in ast.walk(tree)
            if isinstance(node, (ast.Import, ast.ImportFrom))
            for alias in node.names
        }
        self.assertTrue({
            "requests", "urllib", "aiohttp", "socket", "subprocess", "sqlite3"
        }.isdisjoint(imported))
        self.assertNotIn("H0STCNT0", {
            row["endpoint_id"] for row in KRX.load_contract()["provider_allowlist"]
        })

    def test_synthetic_positive_is_rederived_but_never_actual_admitted(self):
        inputs = make_inputs()
        result = O.qualify_kis_realtime_trade_observation(**inputs)
        self.assertEqual(result["status"], "TEST_ONLY_NON_PROMOTABLE")
        self.assertEqual(result["actual_source_qualification"], "TEST_ONLY_NON_PROMOTABLE")
        self.assertFalse(result["actual_source_admitted"])
        self.assertFalse(result["paper_market_data_source_qualified"])
        self.assertFalse(result["completed_minute_ohlcv_qualified"])
        self.assertFalse(result["all_trades_exhaustiveness_qualified"])
        self.assertTrue(result["retained_decoded_source_record_bytes_revalidated"])
        self.assertFalse(result["original_provider_message_bytes_retained"])
        self.assertFalse(result["completed_bar_provider_allowlist_changed"])
        self.assertEqual([row["symbol"] for row in result["candidates"]],
                         ["000660", "005930", "071050"])
        sealed = copy.deepcopy(result)
        expected_seal = sealed.pop("qualification_sha256")
        self.assertEqual(expected_seal, O.digest(O.canonical_bytes(sealed)))
        self.assertEqual(result, O.validate_qualification(result, **inputs))
        serialized = json.dumps(result)
        self.assertNotIn("73500", serialized)
        self.assertNotIn("1234567", serialized)

    def test_completed_bar_allowlist_is_unchanged(self):
        contract = KRX.load_contract()
        intraday = {
            row["endpoint_id"] for row in contract["provider_allowlist"]
            if row["purpose"] in {
                "KRX_DATED_ONE_MINUTE_GET_ONLY_PRIMARY",
                "KRX_CURRENT_DAY_ONE_MINUTE_GET_ONLY_SUPPLEMENTAL",
            }
        }
        self.assertEqual(intraday, {"FHKST03010230", "FHKST03010200"})
        self.assertNotIn("H0STCNT0", {
            row["endpoint_id"] for row in contract["provider_allowlist"]
        })

    def test_raw_and_parsed_tampering_fail_independent_hash_checks(self):
        for field, expected in (
            ("source_record_json", "SOURCE_RECORD_HASH_MISMATCH"),
            ("parsed_quote_json", "PARSED_QUOTE_HASH_MISMATCH"),
        ):
            with self.subTest(field=field):
                inputs = make_inputs()
                def mutate(packet):
                    packet["rows"][0][field] += " "
                replace_packet(inputs, mutate)
                with self.assertRaisesRegex(O.KisRealtimeTradeObservationError, expected):
                    O.qualify_kis_realtime_trade_observation(**inputs)

    def test_wrong_date_time_order_and_out_of_session_fail(self):
        cases = []

        def wrong_date(packet):
            row_value = packet["rows"][0]
            source = json.loads(row_value["source_record_json"])
            parsed = json.loads(row_value["parsed_quote_json"])
            source[33] = parsed["business_date"] = "20260907"
            row_value["source_record_json"] = json.dumps(source, separators=(",", ":"))
            row_value["parsed_quote_json"] = json.dumps(parsed, separators=(",", ":"))
            resign_row(row_value)
        cases.append((wrong_date, "BUSINESS_DATE_MISMATCH"))

        def received_before_trade(packet):
            row_value = packet["rows"][0]
            parsed = json.loads(row_value["parsed_quote_json"])
            parsed["observed_at"] = row_value["observed_at"] = "2026-09-08T00:04:59+00:00"
            row_value["parsed_quote_json"] = json.dumps(parsed, separators=(",", ":"))
            resign_row(row_value)
        cases.append((received_before_trade, "ROW_TIME_ORDER_INVALID"))

        def before_open(packet):
            row_value = packet["rows"][0]
            source = json.loads(row_value["source_record_json"])
            parsed = json.loads(row_value["parsed_quote_json"])
            source[1] = parsed["trade_time"] = "085959"
            row_value["source_record_json"] = json.dumps(source, separators=(",", ":"))
            row_value["parsed_quote_json"] = json.dumps(parsed, separators=(",", ":"))
            resign_row(row_value)
        cases.append((before_open, "TRADE_OUTSIDE_OPEN_REGULAR_SESSION"))

        for mutate, expected in cases:
            with self.subTest(expected=expected):
                inputs = make_inputs()
                replace_packet(inputs, mutate)
                with self.assertRaisesRegex(O.KisRealtimeTradeObservationError, expected):
                    O.qualify_kis_realtime_trade_observation(**inputs)

    def test_unknown_symbol_and_missing_approved_symbol_fail(self):
        inputs = make_inputs()
        def unknown(packet):
            row_value = packet["rows"][0]
            source = json.loads(row_value["source_record_json"])
            parsed = json.loads(row_value["parsed_quote_json"])
            source[0] = parsed["symbol"] = "999999"
            row_value["source_record_json"] = json.dumps(source, separators=(",", ":"))
            row_value["parsed_quote_json"] = json.dumps(parsed, separators=(",", ":"))
            resign_row(row_value)
        replace_packet(inputs, unknown)
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError, "UNAPPROVED_SYMBOL"):
            O.qualify_kis_realtime_trade_observation(**inputs)

        inputs = make_inputs()
        replace_packet(inputs, lambda packet: packet["rows"].pop(), preserve_owner_range=False)
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "APPROVED_SYMBOL_OBSERVATION_MISSING"):
            O.qualify_kis_realtime_trade_observation(**inputs)

    def test_truncated_row_range_and_external_pin_mismatch_fail(self):
        inputs = make_inputs()
        replace_packet(inputs, lambda packet: packet["rows"].pop())
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "ROW_RANGE_OR_TRUNCATION_MISMATCH"):
            O.qualify_kis_realtime_trade_observation(**inputs)

        inputs = make_inputs()
        inputs["expected_row_packet_sha256"] = "f" * 64
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "ROW_PACKET_HASH_MISMATCH"):
            O.qualify_kis_realtime_trade_observation(**inputs)

    def test_owner_export_pin_time_and_closed_calendar_fail(self):
        inputs = make_inputs()
        owner = json.loads(inputs["owner_receipt"])
        owner["deployed_source_pins"]["collector_sha256"] = "f" * 64
        inputs["owner_receipt"] = encoded(owner)
        inputs["expected_owner_receipt_sha256"] = O.digest(inputs["owner_receipt"])
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "OWNER_RECEIPT_BINDING_MISMATCH"):
            O.qualify_kis_realtime_trade_observation(**inputs)

        inputs = make_inputs()
        owner = json.loads(inputs["owner_receipt"])
        owner["export_started_at"] = "2026-09-08T00:05:05+00:00"
        inputs["owner_receipt"] = encoded(owner)
        inputs["expected_owner_receipt_sha256"] = O.digest(inputs["owner_receipt"])
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "ROW_NOT_AVAILABLE_AT_EXPORT"):
            O.qualify_kis_realtime_trade_observation(**inputs)

        inputs = make_inputs()
        closed = encoded(calendar("CLOSED"))
        inputs["calendar_packet"] = closed
        inputs["expected_calendar_packet_sha256"] = O.digest(closed)
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "SESSION_NOT_OPEN_REGULAR"):
            O.qualify_kis_realtime_trade_observation(**inputs)

    def test_output_tamper_fails_rederivation(self):
        inputs = make_inputs()
        result = O.qualify_kis_realtime_trade_observation(**inputs)
        result["completed_minute_ohlcv_qualified"] = True
        with self.assertRaisesRegex(O.KisRealtimeTradeObservationError,
                                    "QUALIFICATION_REDERIVATION_MISMATCH"):
            O.validate_qualification(result, **inputs)


if __name__ == "__main__":
    unittest.main()
