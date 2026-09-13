#!/usr/bin/env python3
"""Stage5 lifecycle connector to redacted P7-19 readiness regression."""
from __future__ import annotations

import ast
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "stage5_virtual_fill_performance_adapter.py"
SPEC = importlib.util.spec_from_file_location("stage5_virtual_fill_performance_adapter", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

INITIAL_CASH = "200000000"
EVALUATED_AT = "2026-09-13T12:00:00Z"
CONNECTOR_AUTHORITY = {
    "internal_virtual_acceptance_review_only": False,
    "stage4_decision_authorized": False,
    "allocation_policy_authorized": False,
    "economic_policy_authorized": False,
    "position_creation_authorized": False,
    "ledger_mutation_authorized_by_contract": False,
    "order_creation_or_submission": False,
    "broker_or_exchange": False,
    "provider_or_network": False,
    "credential_access": False,
    "capital_transfer": False,
    "real_account": False,
    "production": False,
    "live_trading": False,
}


def _sign(value: dict, field: str) -> dict:
    value[field] = MODULE.payload_sha256({key: item for key, item in value.items() if key != field})
    return value


def _source(label: str, digit: str) -> dict:
    return {
        "repository": "yonggeun1021-hub/atlas-data",
        "path": f"private-source/{label}.json",
        "commit": digit * 40,
        "sha256": digit * 64,
    }


def _stage4_decision(digit: str = "1") -> dict:
    return _sign({
        "schema_version": "stage4_paper_decision_envelope/1",
        "decision_id": "STAGE4.INTERNAL.TEST",
        "status": "NOT_EVALUATED",
        "candle_open_at": "2026-09-12T00:00:00Z",
        "candle_closed_at": "2026-09-12T00:15:00Z",
        "available_at": "2026-09-12T00:15:01Z",
        "decided_at": "2026-09-12T00:15:02Z",
        "source_ref": "data/stage4-private/" + digit * 64 + ".json",
        "source_sha256": digit * 64,
        "authority": {"stage4_decision_authorized": False, "trading_authorized": False},
        "packet_sha256": None,
    }, "packet_sha256")


def _allocation(market: str, side: str, decision_time: str) -> dict:
    drift = "40000000" if side == "BUY" else "-40000000"
    current = {
        "KOREA_VIRTUAL": "0", "US_VIRTUAL": "0",
        "CRYPTO_VIRTUAL": "0", "CASH": INITIAL_CASH,
    }
    target = copy.deepcopy(current)
    if side == "BUY":
        target[market] = "40000000"
        target["CASH"] = "160000000"
    else:
        current[market] = "40000000"
        current["CASH"] = "160000000"
    value = {
        "schema_version": "p7_17_allocation_decision_ledger_entry/1",
        "sequence": 1,
        "identity_sha256": "1" * 64,
        "previous_entry_sha256": None,
        "decision_time": decision_time,
        "recorded_at": decision_time,
        "current_allocation_krw": current,
        "previous_target_krw": None,
        "new_target_krw": target,
        "target_availability_status": "AVAILABLE",
        "target_transition": "GENESIS_NO_PRIOR_ENTRY",
        "drift_by_sleeve_krw": {
            "KOREA_VIRTUAL": drift if market == "KOREA_VIRTUAL" else "0",
            "US_VIRTUAL": drift if market == "US_VIRTUAL" else "0",
            "CRYPTO_VIRTUAL": drift if market == "CRYPTO_VIRTUAL" else "0",
            "CASH": "-40000000" if side == "BUY" else "40000000",
        },
        "decision_status": "POLICY_PENDING",
        "tolerance_policy": {
            "ratification_status": "NOT_CIO_RATIFIED",
            "ratified_by": None,
            "required_gate": "EXPLICIT_CIO_RATIFIED_REBALANCE_TOLERANCE_BAND_AND_MINIMUM_TRADE_SIZE_POLICY",
            "rule": "Synthetic canonical-shape test value; not an execution policy.",
        },
        "reason": {
            "gross_exposure_status": "SYNTHETIC_TEST_ONLY",
            "gross_exposure_blocker_codes": [],
            "cross_market_status": "SYNTHETIC_TEST_ONLY",
            "cross_market_blocker_codes": [],
            "target_available": True,
            "semantics": "NOT_EVALUATED_OR_POLICY_PENDING_IS_NOT_NO_REBALANCE_AND_NOT_A_ZERO_DRIFT_FINDING",
        },
        "effective_time": None,
        "source": {
            "portfolio_payload_sha256": "2" * 64,
            "gross_exposure": {
                "evaluation_id": "SYNTHETIC.GROSS",
                "evaluated_at": "2026-09-12T04:59:58Z",
                "payload_sha256": "3" * 64,
                "cash_target_fraction": "0.8",
                "gross_exposure_target_fraction": "0.2",
            },
            "cross_market": {
                "evaluation_id": "SYNTHETIC.CROSS",
                "evaluated_at": "2026-09-12T04:59:59Z",
                "payload_sha256": "4" * 64,
            },
        },
        "authority": copy.deepcopy(MODULE.ALLOCATION_AUTHORITY),
        "entry_sha256": None,
    }
    return _sign(value, "entry_sha256")


def _price(evaluation_id: str, market: str, decision_at: str, fill_time: str, digit: str) -> dict:
    return _sign({
        "schema_version": "p7_18_eligible_price_attestation/1",
        "market": market,
        "price_native": "100" if market == "US_VIRTUAL" else "70000",
        "price_currency": "USD" if market == "US_VIRTUAL" else "KRW",
        "decision_time": decision_at,
        "candle_closed_at": decision_at,
        "available_at": decision_at[:-3] + "01Z",
        "fill_time": fill_time,
        "source_pin": _source(f"price-{evaluation_id}", digit),
        "payload_sha256": None,
    }, "payload_sha256")


def _cost(evaluation_id: str, market: str, digit: str) -> dict:
    value = {
        "schema_version": "p7_18_market_cost_policy_korea/1" if market == "KOREA_VIRTUAL" else "p7_18_market_cost_policy/1",
        "market": market,
        "policy_id": f"COST-{evaluation_id}",
        "effective_from": "2026-09-01T00:00:00Z",
        "buy_fee_bps": "15" if market == "KOREA_VIRTUAL" else "5",
        "sell_fee_bps": "15" if market == "KOREA_VIRTUAL" else "5",
    }
    if market == "KOREA_VIRTUAL":
        value["sell_tax_bps"] = "18"
    value.update({
        "slippage_bps": "5" if market == "KOREA_VIRTUAL" else "3",
        "source_pin": _source(f"cost-{evaluation_id}", digit),
        "payload_sha256": None,
    })
    return _sign(value, "payload_sha256")


def _request(evaluation_id: str, market: str, side: str, decision_at: str, fill_time: str, evaluated_at: str, digit: str) -> dict:
    fx = None
    if market == "US_VIRTUAL":
        fx = _sign({
            "schema_version": "p7_18_fx_rate_attestation/1",
            "pair": "USD/KRW",
            "rate": "1350",
            "as_of": fill_time,
            "staleness_status": "FRESH",
            "source_pin": _source(f"fx-{evaluation_id}", digit),
            "payload_sha256": None,
        }, "payload_sha256")
    return {
        "evaluation_id": evaluation_id,
        "evaluated_at_utc": evaluated_at,
        "market": market,
        "side": side,
        "notional_native": "1000" if market == "US_VIRTUAL" else "10000000",
        "eligible_price": _price(evaluation_id, market, decision_at, fill_time, digit),
        "cost_policy": _cost(evaluation_id, market, digit),
        "fx_rate": fx,
    }


def _entry(sequence: int, request: dict, previous: str | None, cash, position: dict):
    pin = {
        "evaluation_id": request["evaluation_id"],
        "entry_sha256": "0" * 64,
        "fill_cost_payload_sha256": "0" * 64,
        "eligible_price_source_pin": copy.deepcopy(request["eligible_price"]["source_pin"]),
        "cost_policy_source_pin": copy.deepcopy(request["cost_policy"]["source_pin"]),
        "fx_source_pin": copy.deepcopy(request["fx_rate"]["source_pin"]) if request["fx_rate"] is not None else None,
    }
    fill = MODULE._derive_fill_result(request, pin)
    cash_after, position_after, realized = MODULE._derive_after_state(fill, position, cash)
    recorded = MODULE._utc(request["evaluated_at_utc"], "TEST")
    fill_time = MODULE._utc(request["eligible_price"]["fill_time"], "TEST")
    entry = {
        "schema_version": MODULE.ENTRY_SCHEMA,
        "sequence": sequence,
        "identity_sha256": fill["payload_sha256"],
        "previous_entry_sha256": previous,
        "recorded_at": request["evaluated_at_utc"],
        "market": request["market"],
        "side": request["side"],
        "fill_cost_payload_sha256": fill["payload_sha256"],
        "fill_cost_result": copy.deepcopy(fill),
        "fill_cost_request": copy.deepcopy(request),
        "max_price_age_seconds": 3600,
        "price_age_seconds": int((recorded - fill_time).total_seconds()),
        "cash_before_krw": MODULE._krw_text(cash),
        "cash_after_krw": MODULE._krw_text(cash_after),
        "position_before": copy.deepcopy(position),
        "position_after": copy.deepcopy(position_after),
        "realized_pnl_delta_krw": MODULE._krw_text(realized),
        "authority": copy.deepcopy(MODULE.ENTRY_AUTHORITY),
        "entry_sha256": None,
    }
    _sign(entry, "entry_sha256")
    pin["entry_sha256"] = entry["entry_sha256"]
    pin["fill_cost_payload_sha256"] = fill["payload_sha256"]
    return entry, cash_after, position_after, pin


def _encode(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _ledger(entries: list[dict]) -> tuple[dict, bytes]:
    value = _sign({
        "schema_version": MODULE.LEDGER_SCHEMA,
        "genesis_snapshot_payload_sha256": "9" * 64,
        "entries": entries,
        "file_sha256": None,
    }, "file_sha256")
    return value, _encode(value)


def _snapshot(cash, positions: dict, entries: list[dict]) -> dict:
    return {
        "cash_krw": MODULE._krw_text(cash),
        "positions": copy.deepcopy(positions),
        "book_value_krw": MODULE._krw_text(cash + sum((MODULE.Decimal(row["cost_basis_krw"]) for row in positions.values()), MODULE.Decimal(0))),
        "cumulative_realized_pnl_krw": MODULE._krw_text(sum((MODULE.Decimal(row["realized_pnl_krw"]) for row in positions.values()), MODULE.Decimal(0))),
        "entry_count": len(entries),
        "head_entry_sha256": entries[-1]["entry_sha256"] if entries else None,
        "reconciliation_note": (
            "book_value_krw is cost-basis book value, not a mark-to-market NAV -- "
            "there is no live price feed in scope here. book_value_krw must always "
            "equal ratified NAV plus cumulative_realized_pnl_krw; see reconcile()."
        ),
    }


def _state_pin(cash, positions: dict, entries: list[dict]) -> dict:
    partial, _raw = _ledger(copy.deepcopy(entries))
    return {
        "ledger_ref": "data/stage5_internal_virtual_ledgers/" + partial["file_sha256"] + ".json",
        "ledger_file_sha256": partial["file_sha256"],
        "snapshot_sha256": MODULE.payload_sha256(_snapshot(cash, positions, entries)),
        "head_entry_sha256": entries[-1]["entry_sha256"] if entries else None,
        "entry_count": len(entries),
    }


def fixture(market: str = "US_VIRTUAL") -> tuple[bytes, dict, dict, dict]:
    decision_at = "2026-09-12T05:00:00Z"
    request = _request("SYNTHETIC.BUY", market, "BUY", decision_at, "2026-09-12T06:30:00Z", "2026-09-12T07:00:00Z", "5")
    positions_before = {name: MODULE._zero_position(name) for name in MODULE.MARKETS}
    cash_before = MODULE.Decimal(INITIAL_CASH)
    entry, cash_after, position_after, pin = _entry(1, request, None, cash_before, positions_before[market])
    positions_after = copy.deepcopy(positions_before)
    positions_after[market] = position_after
    ledger, raw = _ledger([entry])
    decision = _stage4_decision()
    envelope = _sign({
        "schema_version": MODULE.CONNECTOR_ENVELOPE_SCHEMA,
        "contract_version": MODULE.CONNECTOR_CONTRACT_VERSION,
        "mode": MODULE.CONNECTOR_MODE,
        "envelope_id": "STAGE5.INTERNAL.TEST",
        "evidence_class": "SYNTHETIC_INTERNAL_VALIDATION",
        "decision": decision,
        "allocation_decision": _allocation(market, "BUY", decision_at),
        "fill_cost_request": copy.deepcopy(request),
        "fill_cost_result": copy.deepcopy(entry["fill_cost_result"]),
        "ledger_request": {"recorded_at": entry["recorded_at"], "max_price_age_seconds": 3600},
        "dependency_pins": {
            "stage4_result_store": _source("stage4-store", "1"),
            "p7_17_allocation_ledger": _source("allocation", "2"),
            "p7_18_cost_model": _source("cost-model", "3"),
            "p7_18_fill_ledger": _source("fill-ledger", "4"),
        },
        "authority": copy.deepcopy(CONNECTOR_AUTHORITY),
        "packet_sha256": None,
    }, "packet_sha256")
    before = _state_pin(cash_before, positions_before, [])
    after = _state_pin(cash_after, positions_after, [entry])
    receipt = _sign({
        "schema_version": MODULE.CONNECTOR_RECEIPT_SCHEMA,
        "contract_version": MODULE.CONNECTOR_CONTRACT_VERSION,
        "mode": MODULE.CONNECTOR_MODE,
        "envelope_id": envelope["envelope_id"],
        "evidence_class": envelope["evidence_class"],
        "attempted_at": envelope["ledger_request"]["recorded_at"],
        "status": "APPLIED_TO_EXISTING_INTERNAL_VIRTUAL_LEDGER",
        "execution_performed": True,
        "request_sha256": MODULE.payload_sha256(request),
        "fill_cost_payload_sha256": entry["fill_cost_payload_sha256"],
        "result_code": "APPLIED",
        "error_code": None,
        "before": before,
        "after": after,
        "deduplication": {"checked": True, "result": "NO_CHANGE", "state_unchanged": True},
        "recovery": {"status": "NOT_REQUIRED", "source_ref": after["ledger_ref"]},
        "authority": copy.deepcopy(CONNECTOR_AUTHORITY),
        "packet_sha256": None,
    }, "packet_sha256")
    expected = {
        "producer_repository": MODULE.PRODUCER_REPOSITORY,
        "producer_commit": "4" * 40,
        "connector_path": MODULE.CONNECTOR_PATH,
        "connector_module_sha256": "5" * 64,
        "connector_contract_payload_sha256": "6" * 64,
        "connector_envelope_sha256": envelope["packet_sha256"],
        "connector_lifecycle_receipt_sha256": receipt["packet_sha256"],
        "expected_decision_packet_sha256": decision["packet_sha256"],
        "expected_decision_source_sha256": decision["source_sha256"],
        "ledger_raw_bytes_sha256": hashlib.sha256(raw).hexdigest(),
        "ledger_content_payload_sha256": ledger["file_sha256"],
        "genesis_snapshot_payload_sha256": ledger["genesis_snapshot_payload_sha256"],
        "initial_cash_krw": INITIAL_CASH,
        "entries": [pin],
    }
    return raw, envelope, receipt, expected


def _resolve(raw, envelope, receipt, expected):
    return MODULE.resolve_connector_result(
        raw, connector_envelope=envelope, lifecycle_receipt=receipt,
        expected_pins=expected, evaluated_at=EVALUATED_AT,
    )


class SuccessfulRedactedMappingTests(unittest.TestCase):
    def test_lifecycle_shape_is_rederived_but_never_authenticated_or_counted(self):
        raw, envelope, receipt, expected = fixture()
        result = _resolve(raw, envelope, receipt, expected)
        self.assertEqual(MODULE.validate_projection(
            result, raw, connector_envelope=envelope,
            lifecycle_receipt=receipt, expected_pins=expected,
        ), result)
        self.assertEqual(result["status"], "UNTRUSTED_CALLER_PINNED_REDERIVATION_ONLY")
        self.assertTrue(result["authority"]["caller_pinned_rederivation"])
        self.assertFalse(result["authority"]["private_source_authentication"])
        self.assertFalse(result["authority"]["connector_lifecycle_authentication"])
        self.assertEqual(result["performance_input"]["sample_contribution"], 0)
        for key, value in result["performance_input"].items():
            if key not in {"status", "sample_contribution"}:
                self.assertIsNone(value)
        rendered = MODULE.canonical_json(result)
        for private_value in (INITIAL_CASH, "1350", "cash_before_krw", "position_after", "private-source/price-SYNTHETIC.BUY.json", "ledger_ref"):
            self.assertNotIn(private_value, rendered)

    def test_korea_path_uses_same_redacted_zero_sample_boundary(self):
        result = _resolve(*fixture("KOREA_VIRTUAL"))
        self.assertEqual(result["caller_pinned_source_identity"]["entry_count"], 1)
        self.assertEqual(result["performance_input"]["sample_contribution"], 0)
        self.assertIsNone(result["performance_input"]["pnl_krw"])


class FailClosedContractTests(unittest.TestCase):
    def test_cost_policy_must_be_effective_at_fill_not_merely_at_evaluation(self):
        _raw, envelope, _receipt, expected = fixture()
        request = envelope["fill_cost_request"]
        request["cost_policy"]["effective_from"] = "2026-09-12T06:45:00Z"
        _sign(request["cost_policy"], "payload_sha256")
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "COST_POLICY_NOT_EFFECTIVE_AT_FILL_TIME"):
            MODULE._derive_fill_result(request, expected["entries"][0])

    def test_stale_and_unknown_fx_are_never_consumable(self):
        for status in ("STALE", "UNKNOWN"):
            with self.subTest(status=status):
                _raw, envelope, _receipt, expected = fixture()
                request = envelope["fill_cost_request"]
                request["fx_rate"]["staleness_status"] = status
                _sign(request["fx_rate"], "payload_sha256")
                with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "FX_RATE_NOT_FRESH_AT_FILL"):
                    MODULE._derive_fill_result(request, expected["entries"][0])

    def test_raw_and_content_hash_names_have_distinct_meanings(self):
        raw, envelope, receipt, expected = fixture()
        expected["ledger_raw_bytes_sha256"] = expected["ledger_content_payload_sha256"]
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "LEDGER_RAW_BYTES_EXPECTED_PIN_MISMATCH"):
            _resolve(raw, envelope, receipt, expected)

    def test_connector_receipt_must_bind_derived_ledger_state(self):
        raw, envelope, receipt, expected = fixture()
        receipt["after"]["entry_count"] = 2
        _sign(receipt, "packet_sha256")
        expected["connector_lifecycle_receipt_sha256"] = receipt["packet_sha256"]
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "CONNECTOR_RECEIPT_STATUS_SHAPE_INVALID|CONNECTOR_RECEIPT_AFTER_STATE_MISMATCH"):
            _resolve(raw, envelope, receipt, expected)

    def test_coherently_resigned_caller_bundle_stays_explicitly_untrusted(self):
        raw, envelope, receipt, expected = fixture()
        expected["producer_commit"] = "8" * 40
        expected["connector_module_sha256"] = "8" * 64
        result = _resolve(raw, envelope, receipt, expected)
        self.assertEqual(result["status"], "UNTRUSTED_CALLER_PINNED_REDERIVATION_ONLY")
        self.assertFalse(result["authority"]["private_source_authentication"])
        self.assertIn("CALLER_SUPPLIED_PINS_ARE_NOT_AN_INDEPENDENT_TRUST_ANCHOR", result["limitations"])

    def test_source_substitution_fails_against_expected_pin(self):
        _raw, envelope, _receipt, expected = fixture()
        request = envelope["fill_cost_request"]
        request["eligible_price"]["source_pin"]["path"] = "private-source/substitute.json"
        _sign(request["eligible_price"], "payload_sha256")
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "ELIGIBLE_PRICE_SOURCE_EXPECTED_PIN_MISMATCH"):
            MODULE._derive_fill_result(request, expected["entries"][0])

    def test_future_fx_and_future_entry_fail_closed(self):
        raw, envelope, receipt, expected = fixture()
        request = envelope["fill_cost_request"]
        request["fx_rate"]["as_of"] = "2026-09-12T06:30:01Z"
        _sign(request["fx_rate"], "payload_sha256")
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "FX_FROM_FUTURE"):
            MODULE._derive_fill_result(request, expected["entries"][0])
        raw, envelope, receipt, expected = fixture()
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "ENTRY_FROM_FUTURE"):
            MODULE.resolve_connector_result(
                raw, connector_envelope=envelope, lifecycle_receipt=receipt,
                expected_pins=expected, evaluated_at="2026-09-12T06:59:59Z",
            )

    def test_rehashed_projection_cannot_inflate_sample_or_performance(self):
        raw, envelope, receipt, expected = fixture()
        result = _resolve(raw, envelope, receipt, expected)
        result["performance_input"]["sample_contribution"] = 1
        result["performance_input"]["return_pct"] = "99"
        _sign(result, "packet_sha256")
        with self.assertRaisesRegex(MODULE.Stage5VirtualFillPerformanceError, "PROJECTION_DERIVATION_MISMATCH"):
            MODULE.validate_projection(
                result, raw, connector_envelope=envelope,
                lifecycle_receipt=receipt, expected_pins=expected,
            )


class StaticPrivacyBoundaryTests(unittest.TestCase):
    def test_module_has_no_io_network_or_private_module_import(self):
        text = SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(text)
        imported_roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported_roots.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported_roots.add(node.module.split(".")[0])
        self.assertNotIn("private_evidence", imported_roots)
        self.assertNotIn("pathlib", imported_roots)
        for forbidden in ("requests", "urllib", "socket", "subprocess", "Path(", "open(", "write_text", "read_text"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
