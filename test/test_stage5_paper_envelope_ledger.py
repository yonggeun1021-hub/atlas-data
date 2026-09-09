#!/usr/bin/env python3
"""Stage5 decision-envelope to virtual-ledger adapter regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "stage5_paper_envelope_ledger.py"
SPEC = importlib.util.spec_from_file_location("stage5_paper_envelope_ledger", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()


def decision(status="NOT_EVALUATED"):
    value = {
        "schema_version": CONTRACT["input_schema_version"], "decision_id": "STAGE4.FIXTURE.1", "status": status,
        "candle_open_at": "2026-09-10T00:00:00Z", "candle_closed_at": "2026-09-10T00:01:00Z",
        "available_at": "2026-09-10T00:01:01Z", "decided_at": "2026-09-10T00:01:02Z",
        "source_ref": "fixture://stage4/decision/1", "source_sha256": "a" * 64,
        "authority": copy.deepcopy(CONTRACT["authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def envelope():
    snapshot = MODULE.SIMULATOR.build_snapshot(
        snapshot_id="STAGE5.SNAPSHOT.1", market="KRW-BTC", captured_at="2026-09-10T00:02:00Z",
        freshness_status="FRESH", ask_levels=[{"price": "100", "quantity": "2"}], bid_levels=[{"price": "99", "quantity": "2"}],
        source_ref="fixture://stage5/orderbook/1", source_sha256="b" * 64,
    )
    value = {
        "schema_version": CONTRACT["input_schema_version"], "contract_version": CONTRACT["contract_version"], "mode": CONTRACT["mode"],
        "envelope_id": "STAGE5.ENVELOPE.1", "decision": decision(),
        "plan": {
            "plan_id": "STAGE5.PLAN.1", "ledger_id": "STAGE5.LEDGER.1", "initial_cash": "1000", "opened_at": "2026-09-10T00:01:02Z", "opening_idempotency_key": "STAGE5.OPEN.1",
            "order_id": "STAGE5.ORDER.1", "submit_idempotency_key": "STAGE5.SUBMIT.1", "match_idempotency_key": "STAGE5.MATCH.1",
            "side": "BUY", "order_type": "MARKET", "quantity": "2", "limit_price": None, "fee_rate": "0.001", "queue_fraction": "1",
            "submitted_at": "2026-09-10T00:01:03Z", "expires_at": "2026-09-10T00:03:00Z", "match_at": "2026-09-10T00:02:01Z", "mark_price": "101", "account_observed_at": "2026-09-10T00:02:02Z",
        },
        "snapshot": snapshot, "authority": copy.deepcopy(CONTRACT["authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class Stage5PaperEnvelopeLedgerTests(unittest.TestCase):
    def test_not_evaluated_fixture_derives_deterministic_plan_fill_position_and_reconciliation(self):
        value = envelope()
        first = MODULE.build_result(value)
        second = MODULE.build_result(value)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(first["virtual_plan"]["market_regime_status"], "NOT_EVALUATED")
        self.assertEqual(first["virtual_fill_event"]["event_type"], "FILL_APPLIED")
        self.assertEqual(first["reconciliation"]["status"], "RECONCILED")
        self.assertEqual(first["reconciliation"]["filled_quantity"], first["reconciliation"]["position_quantity"])
        self.assertEqual(MODULE.validate_result(first, value), first)

    def test_authority_is_closed_and_no_network_or_broker_surface_exists(self):
        self.assertTrue(all(value is False for value in CONTRACT["authority"].values()))
        text = SOURCE.read_text(encoding="utf-8")
        for forbidden in ("requests", "urllib", "socket", "websocket", "api_key", "secret", "/v1/orders", "KIS"):
            self.assertNotIn(forbidden, text)

    def test_source_hash_decision_hash_and_same_candle_guards_fail_closed(self):
        cases = []
        bad = envelope(); bad["decision"]["source_sha256"] = "0" * 64; cases.append((bad, "DECISION_PACKET_SHA_MISMATCH"))
        bad = envelope(); bad["snapshot"]["captured_at"] = "2026-09-10T00:01:00Z"; bad["snapshot"]["packet_sha256"] = MODULE.SIMULATOR.payload_sha256({k: v for k, v in bad["snapshot"].items() if k != "packet_sha256"}); bad["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in bad.items() if k != "packet_sha256"}); cases.append((bad, "SAME_CANDLE_EXECUTION_FORBIDDEN"))
        bad = envelope(); bad["decision"]["authority"]["entry_authorized"] = True; bad["decision"]["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in bad["decision"].items() if k != "packet_sha256"}); bad["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in bad.items() if k != "packet_sha256"}); cases.append((bad, "DECISION_AUTHORITY_ESCALATION"))
        for value, code in cases:
            with self.subTest(code=code), self.assertRaisesRegex(MODULE.Stage5PaperEnvelopeError, code):
                MODULE.build_result(value)

    def test_result_replay_and_external_restart_reject_tamper(self):
        value = envelope(); result = MODULE.build_result(value)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            MODULE.SIMULATOR.publish_ledger_snapshot(root, result["ledger"])
            recovered = MODULE.SIMULATOR.recover_ledger(root, "STAGE5.LEDGER.1")
            self.assertEqual(MODULE.canonical_json(recovered), MODULE.canonical_json(result["ledger"]))
        tampered = copy.deepcopy(result)
        tampered["account_state"]["cash"] = "999"
        tampered["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in tampered.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.Stage5PaperEnvelopeError, "RESULT_SEMANTIC_TAMPER_OR_REPLAY_DRIFT"):
            MODULE.validate_result(tampered, value)

    def test_same_idempotency_key_with_changed_plan_is_rejected_by_simulator(self):
        value = envelope(); changed = envelope(); changed["plan"]["quantity"] = "1"; changed["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in changed.items() if k != "packet_sha256"})
        first = MODULE.build_result(value)
        with self.assertRaisesRegex(MODULE.Stage5PaperEnvelopeError, "RESULT_SEMANTIC_TAMPER_OR_REPLAY_DRIFT"):
            MODULE.validate_result(first, changed)
        duplicate = envelope(); duplicate["plan"]["match_idempotency_key"] = duplicate["plan"]["submit_idempotency_key"]; duplicate["packet_sha256"] = MODULE.payload_sha256({k: v for k, v in duplicate.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.Stage5PaperEnvelopeError, "PLAN_IDEMPOTENCY_KEYS_NOT_DISTINCT"):
            MODULE.build_result(duplicate)


if __name__ == "__main__":
    unittest.main(verbosity=2)
