#!/usr/bin/env python3
"""P10-01 three-market zero-capital Shadow ledger regression."""

import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "shadow" / "three_market_shadow_ledger.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("three_market_shadow_ledger", SOURCE)
UNIFIED_FIXTURE = load_module(
    "shadow_unified_fixture", ROOT / "test" / "test_unified_decision_contract.py"
)
ENTRY_EXIT_FIXTURE = load_module(
    "shadow_entry_exit_fixture",
    ROOT / "test" / "test_entry_exit_trigger_eligibility.py",
)
INTRADAY_RISK_FIXTURE = load_module(
    "shadow_intraday_risk_fixture",
    ROOT / "test" / "test_intraday_risk_escalation.py",
)
CONTRACT = MODULE.load_contract()


def decision(day="2026-08-21", generated="2026-08-21T02:10:00Z"):
    return UNIFIED_FIXTURE.MODULE.build_packet(
        UNIFIED_FIXTURE.components(), UNIFIED_FIXTURE.reasons(), day, "morning",
        generated, UNIFIED_FIXTURE.CONTRACT,
    )


def unavailable_rule_decision():
    source = UNIFIED_FIXTURE.components()
    source["RULE"] = None
    reasons = UNIFIED_FIXTURE.reasons()
    reasons["RULE"] = ["RULE_PACKET_NOT_PROVIDED"]
    return UNIFIED_FIXTURE.MODULE.build_packet(
        source, reasons, "2026-08-21", "morning",
        "2026-08-21T02:10:00Z", UNIFIED_FIXTURE.CONTRACT,
    )


def intraday_evidence(source):
    day = source["decision_date"]
    quote = ENTRY_EXIT_FIXTURE.FRESHNESS.quote(
        "US:XNAS:TSM",
        "US",
        f"{day}T02:10:30Z",
        f"{day}T02:10:35Z",
    )
    freshness_batch = ENTRY_EXIT_FIXTURE.FRESHNESS.batch(
        [quote], f"{day}T02:11:00Z"
    )
    freshness_batch["batch_id"] = f"INTRADAY.TEST.{day.replace('-', '')}"
    freshness_batch.pop("packet_sha256")
    freshness_batch["packet_sha256"] = (
        ENTRY_EXIT_FIXTURE.FRESHNESS.MODULE.payload_sha256(freshness_batch)
    )
    end_day = str(dt.date.fromisoformat(day) + dt.timedelta(days=1))
    freshness_policy = ENTRY_EXIT_FIXTURE.FRESHNESS.policy(
        effective_from_utc=f"{day}T00:00:00Z",
        effective_to_utc=f"{end_day}T00:00:00Z",
    )
    freshness = ENTRY_EXIT_FIXTURE.FRESHNESS.MODULE.evaluate_freshness(
        freshness_batch,
        freshness_policy,
        ENTRY_EXIT_FIXTURE.FRESHNESS.CONTRACT,
    )
    entry_exit = ENTRY_EXIT_FIXTURE.MODULE.build_packet(
        source, freshness, f"{day}T02:12:00Z", ENTRY_EXIT_FIXTURE.CONTRACT
    )
    batch = INTRADAY_RISK_FIXTURE.batch()
    batch["batch_id"] = f"INTRADAY.RISK.BATCH.{day.replace('-', '')}"
    batch["observed_at"] = f"{day}T02:13:00Z"
    batch["observations"][0]["provider_timestamp"] = f"{day}T02:12:30Z"
    batch["observations"][0]["received_at"] = f"{day}T02:12:35Z"
    batch["upstream_lineage"][
        "entry_exit_trigger_eligibility_packet_sha256"
    ] = entry_exit["packet_sha256"]
    batch.pop("packet_sha256")
    batch["packet_sha256"] = INTRADAY_RISK_FIXTURE.MODULE.payload_sha256(batch)
    start = f"{day}T00:00:00Z"
    policy = INTRADAY_RISK_FIXTURE.policy(
        effective_from=start, effective_to=f"{end_day}T00:00:00Z"
    )
    intraday_risk = INTRADAY_RISK_FIXTURE.MODULE.build_packet(
        batch, policy, INTRADAY_RISK_FIXTURE.CONTRACT
    )
    return entry_exit, intraday_risk


def append(source, recorded_at, previous=None):
    entry_exit, intraday_risk = intraday_evidence(source)
    return MODULE.append_decision(
        source, entry_exit, intraday_risk, recorded_at, previous, CONTRACT
    )


class ThreeMarketShadowLedgerTests(unittest.TestCase):
    def test_contract_is_zero_capital_recording_only(self):
        self.assertEqual(CONTRACT["markets"], ["US", "KOREA", "CRYPTO"])
        self.assertEqual(CONTRACT["capital_mode"], "ZERO_CAPITAL_SHADOW_ONLY")
        self.assertTrue(CONTRACT["authority"]["shadow_observation_recording_only"])
        self.assertTrue(CONTRACT["authority"]["intraday_evidence_recording_only"])
        for key, value in CONTRACT["authority"].items():
            if key not in {
                "shadow_observation_recording_only",
                "intraday_evidence_recording_only",
            }:
                self.assertFalse(value, key)

    def test_empty_ledger_has_no_capital_orders_or_decision(self):
        ledger = MODULE.empty_ledger(CONTRACT)
        self.assertEqual(ledger["status"], "EMPTY")
        self.assertEqual(ledger["ledger_revision"], 0)
        self.assertEqual(ledger["records"], [])
        self.assertEqual(ledger["summary"]["real_capital_deployed"], "0")
        self.assertEqual(ledger["summary"]["real_order_count"], 0)

    def test_append_records_exact_unified_decision_and_three_markets(self):
        source = decision()
        entry_exit, intraday_risk = intraday_evidence(source)
        ledger = MODULE.append_decision(
            source, entry_exit, intraday_risk, "2026-08-21T02:15:00Z",
            None, CONTRACT,
        )
        self.assertEqual(ledger["status"], "SHADOW_HISTORY_RECORDED")
        self.assertEqual(ledger["ledger_revision"], 1)
        row = ledger["records"][0]
        self.assertEqual(row["unified_decision"], source)
        self.assertEqual(row["unified_decision_sha256"], source["packet_sha256"])
        self.assertEqual(
            [item["market"] for item in row["market_snapshots"]],
            ["US", "KOREA", "CRYPTO"],
        )
        self.assertEqual(row["rotation_change_count"], 0)
        self.assertEqual(row["discovery_case_count"], 1)
        self.assertEqual(row["entry_exit_trigger_eligibility"], entry_exit)
        self.assertEqual(row["intraday_risk_escalation"], intraday_risk)
        self.assertEqual(
            row["entry_exit_trigger_eligibility_sha256"],
            entry_exit["packet_sha256"],
        )
        self.assertEqual(
            row["intraday_risk_escalation_sha256"], intraday_risk["packet_sha256"]
        )
        self.assertEqual(row["entry_eligible_count"], 0)
        self.assertEqual(row["exit_eligible_count"], 0)
        self.assertEqual(row["intraday_alert_count"], 1)

    def test_shadow_record_never_deploys_capital_or_creates_order(self):
        ledger = append(decision(), "2026-08-21T02:15:00Z")
        row = ledger["records"][0]
        self.assertEqual(row["capital_mode"], "ZERO_CAPITAL_SHADOW_ONLY")
        self.assertEqual(row["real_capital_deployed"], "0")
        self.assertEqual(row["real_order_count"], 0)
        self.assertIsNone(row["action"])
        self.assertIsNone(row["entry_trigger"])
        self.assertIsNone(row["position_size"])
        self.assertIsNone(row["order_intent"])

    def test_forward_append_builds_record_hash_chain(self):
        first = append(decision(), "2026-08-21T02:15:00Z")
        second_decision = decision("2026-08-22", "2026-08-22T02:10:00Z")
        second = append(second_decision, "2026-08-22T02:15:00Z", first)
        self.assertEqual(second["ledger_revision"], 2)
        self.assertEqual(second["summary"]["decision_date_count"], 2)
        self.assertEqual(
            second["records"][1]["prior_record_sha256"],
            second["records"][0]["record_sha256"],
        )

    def test_same_decision_retry_is_idempotent_but_payload_conflict_fails(self):
        first = append(decision(), "2026-08-21T02:15:00Z")
        retry = append(decision(), "2026-08-21T02:20:00Z", first)
        self.assertEqual(retry, first)
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "DECISION_ID_EVIDENCE_CONFLICT"
        ):
            append(unavailable_rule_decision(), "2026-08-21T02:20:00Z", first)

    def test_required_regime_rotation_and_record_time_fail_closed(self):
        source = UNIFIED_FIXTURE.components()
        source["REGIME"] = None
        reasons = UNIFIED_FIXTURE.reasons()
        reasons["REGIME"] = ["REGIME_PACKET_NOT_PROVIDED"]
        missing = UNIFIED_FIXTURE.MODULE.build_packet(
            source, reasons, "2026-08-21", "morning",
            "2026-08-21T02:10:00Z", UNIFIED_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError,
            "REQUIRED_COMPONENT_UNAVAILABLE:REGIME",
        ):
            entry_exit, intraday_risk = intraday_evidence(missing)
            MODULE.append_decision(
                missing, entry_exit, intraday_risk, "2026-08-21T02:15:00Z",
                None, CONTRACT,
            )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "RECORDED_BEFORE_SOURCE_EVIDENCE"
        ):
            append(decision(), "2026-08-21T02:09:59Z")

    def test_non_forward_decision_and_tampered_chain_fail_closed(self):
        later = append(
            decision("2026-08-22", "2026-08-22T02:10:00Z"),
            "2026-08-22T02:15:00Z",
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "LEDGER_NON_FORWARD_DECISION"
        ):
            append(decision(), "2026-08-22T02:20:00Z", later)

        first = append(decision(), "2026-08-21T02:15:00Z")
        tampered = copy.deepcopy(first)
        tampered["records"][0]["real_capital_deployed"] = "1"
        tampered["records"][0]["record_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered["records"][0].items()
            if key != "record_sha256"
        })
        tampered["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError, "RECORD_MISMATCH"
        ):
            MODULE.validate_ledger(tampered, CONTRACT)

    def test_output_is_deterministic_and_inputs_are_immutable(self):
        source = decision()
        before = MODULE.canonical_json(source)
        first = append(source, "2026-08-21T02:15:00Z")
        second = append(source, "2026-08-21T02:15:00Z")
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(MODULE.canonical_json(source), before)

    def test_intraday_lineage_and_self_rehashed_authority_tamper_fail_closed(self):
        source = decision()
        entry_exit, intraday_risk = intraday_evidence(source)
        wrong_batch = copy.deepcopy(intraday_risk["source_batch"])
        wrong_batch["upstream_lineage"][
            "entry_exit_trigger_eligibility_packet_sha256"
        ] = "f" * 64
        wrong_batch["packet_sha256"] = INTRADAY_RISK_FIXTURE.MODULE.payload_sha256(
            wrong_batch
        )
        wrong_lineage = INTRADAY_RISK_FIXTURE.MODULE.build_packet(
            wrong_batch,
            intraday_risk["policy_packet"],
            INTRADAY_RISK_FIXTURE.CONTRACT,
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError,
            "INTRADAY_RISK_ENTRY_EXIT_LINEAGE_MISMATCH",
        ):
            MODULE.append_decision(
                source, entry_exit, wrong_lineage, "2026-08-21T02:15:00Z",
                None, CONTRACT,
            )

        tampered = copy.deepcopy(entry_exit)
        tampered["authority"]["action_generation_authorized"] = True
        tampered["packet_sha256"] = ENTRY_EXIT_FIXTURE.MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ThreeMarketShadowLedgerError,
            "ENTRY_EXIT_TRIGGER_ELIGIBILITY_INVALID",
        ):
            MODULE.append_decision(
                source, tampered, intraday_risk, "2026-08-21T02:15:00Z",
                None, CONTRACT,
            )

    def test_cli_is_offline_and_writes_only_outside_repository(self):
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
            temp = Path(tmp)
            source = decision()
            entry_exit, intraday_risk = intraday_evidence(source)
            source_path = temp / "decision.json"
            entry_exit_path = temp / "entry-exit.json"
            intraday_risk_path = temp / "intraday-risk.json"
            source_path.write_text(json.dumps(source), encoding="utf-8")
            entry_exit_path.write_text(json.dumps(entry_exit), encoding="utf-8")
            intraday_risk_path.write_text(json.dumps(intraday_risk), encoding="utf-8")
            output = temp / "out" / "ledger.json"
            self.assertEqual(
                MODULE.run(
                    source_path, entry_exit_path, intraday_risk_path,
                    "2026-08-21T02:15:00Z", output, None,
                ),
                0,
            )
            self.assertEqual(json.loads(output.read_text())["ledger_revision"], 1)
            forbidden = ROOT / "data" / "three_market_shadow_ledger_test.json"
            self.assertEqual(
                MODULE.run(
                    source_path, entry_exit_path, intraday_risk_path,
                    "2026-08-21T02:15:00Z", forbidden, None,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
