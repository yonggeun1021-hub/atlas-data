#!/usr/bin/env python3
"""P9-02 important filing/news event detector regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "execution" / "important_event_detector.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("important_event_detector", SOURCE)
CONTRACT = MODULE.load_contract()


def rule(
    rule_id="SEC.MATERIAL.FILING",
    source_kind="SEC_EDGAR",
    market="US",
    event_type="MATERIAL_FILING",
    importance="IMPORTANT",
    max_delay=600,
):
    return {
        "rule_id": rule_id,
        "source_kind": source_kind,
        "market": market,
        "event_type": event_type,
        "importance": importance,
        "max_detection_delay_seconds": max_delay,
        "policy_basis_ref": f"notion://policy/{rule_id}",
        "policy_basis_sha256": "b" * 64,
    }


def policy(rows=None, **changes):
    value = {
        "schema_version": CONTRACT["policy_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "policy_id": "IMPORTANT.EVENT.TEST.V1",
        "status": "RATIFIED",
        "ratified_by": "CIO",
        "ratified_at": "2026-08-20T00:00:00Z",
        "effective_from": "2026-08-21",
        "effective_to": None,
        "rules": [rule()] if rows is None else rows,
        "authority": {
            "importance_policy_only": True,
            "event_type_inference_authorized": False,
            "notification_authorized": False,
            "action_generation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    value.update(changes)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


def event(
    event_id="SEC.20260821.0001",
    market="US",
    subject_id="US.XNYS.TSM",
    source_kind="SEC_EDGAR",
    event_type="MATERIAL_FILING",
    event_at="2026-08-21T02:57:00Z",
    available_at="2026-08-21T02:59:00Z",
    received_at="2026-08-21T02:59:30Z",
    evidence_status="CONFIRMED",
    blocked_reasons=None,
):
    return {
        "event_id": event_id,
        "market": market,
        "subject_id": subject_id,
        "source_kind": source_kind,
        "event_type": event_type,
        "event_at": event_at,
        "available_at": available_at,
        "received_at": received_at,
        "source_ref": f"test://event/{event_id}",
        "source_sha256": "a" * 64,
        "evidence_status": evidence_status,
        "blocked_reasons": [] if blocked_reasons is None else blocked_reasons,
    }


def batch(rows=None, observed_at="2026-08-21T03:00:00Z"):
    value = {
        "schema_version": CONTRACT["input_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "batch_id": "IMPORTANT.EVENT.BATCH.20260821",
        "observed_at": observed_at,
        "events": [event()] if rows is None else rows,
        "authority": copy.deepcopy(CONTRACT["input_authority"]),
    }
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class ImportantEventDetectorTests(unittest.TestCase):
    DETECTED_AT = "2026-08-21T03:05:00Z"

    def build(self, events=None, ratified_policy=None, detected_at=None):
        return MODULE.build_packet(
            batch(events),
            policy() if ratified_policy is None else ratified_policy,
            detected_at or self.DETECTED_AT,
            CONTRACT,
        )

    def test_contract_requires_external_policy_and_closes_side_effect_authority(self):
        self.assertEqual(CONTRACT["repository_default_policy"], "ABSENT")
        self.assertEqual(CONTRACT["matching_policy"], "EXACT_SOURCE_MARKET_EVENT_TYPE")
        self.assertTrue(CONTRACT["authority"]["ratified_policy_event_detection_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "ratified_policy_event_detection_only":
                self.assertFalse(value, key)

    def test_confirmed_important_sec_event_escalates_on_time(self):
        packet = self.build()
        row = packet["detections"][0]
        self.assertEqual(row["detection_status"], "ESCALATED")
        self.assertEqual(row["detection_delay_seconds"], 360)
        self.assertEqual(row["timing_status"], "ON_TIME")
        self.assertEqual(packet["summary"]["ESCALATED"], 1)
        self.assertEqual(packet["summary"]["late_escalation_count"], 0)

    def test_dart_and_official_news_are_supported_and_late_is_explicit(self):
        rules = [
            rule("DART.MATERIAL", "DART_OPEN_API", "KOREA", "MATERIAL_FILING", max_delay=60),
            rule("NEWS.EXCHANGE", "OFFICIAL_NEWS", "CRYPTO", "EXCHANGE_INCIDENT", max_delay=60),
        ]
        rows = [
            event("DART.20260821.1", "KOREA", "KR.XKRX.005930", "DART_OPEN_API"),
            event("NEWS.20260821.1", "CRYPTO", "CRYPTO.KRAKEN.BTC", "OFFICIAL_NEWS", "EXCHANGE_INCIDENT"),
        ]
        packet = self.build(rows, policy(rules))
        self.assertEqual([row["source_kind"] for row in packet["detections"]], ["DART_OPEN_API", "OFFICIAL_NEWS"])
        self.assertEqual([row["timing_status"] for row in packet["detections"]], ["LATE", "LATE"])
        self.assertEqual(packet["summary"]["late_escalation_count"], 2)

    def test_routine_unmatched_and_blocked_events_remain_distinct(self):
        rules = [
            rule("SEC.ROUTINE", event_type="ROUTINE_FILING", importance="ROUTINE"),
            rule(),
        ]
        rows = [
            event("SEC.ROUTINE.1", event_type="ROUTINE_FILING"),
            event("SEC.UNKNOWN.1", event_type="UNCLASSIFIED_EVENT"),
            event("SEC.BLOCKED.1", evidence_status="BLOCKED", blocked_reasons=["SOURCE_BYTES_MISSING"]),
        ]
        packet = self.build(rows, policy(rules))
        by_id = {row["event_id"]: row for row in packet["detections"]}
        self.assertEqual(by_id["SEC.ROUTINE.1"]["detection_status"], "ROUTINE")
        self.assertEqual(by_id["SEC.UNKNOWN.1"]["detection_status"], "UNASSESSED")
        self.assertEqual(by_id["SEC.BLOCKED.1"]["detection_status"], "BLOCKED")
        for event_id in by_id:
            self.assertEqual(by_id[event_id]["timing_status"], "NOT_APPLICABLE")

    def test_unratified_future_or_ineffective_policy_fails_closed(self):
        cases = [
            (policy(status="DRAFT"), "POLICY_IDENTITY_INVALID"),
            (policy(ratified_at="2026-08-21T04:00:00Z"), "POLICY_RATIFIED_AFTER_DETECTION"),
            (policy(effective_from="2026-08-22"), "POLICY_NOT_EFFECTIVE"),
        ]
        for value, error in cases:
            with self.subTest(error=error), self.assertRaisesRegex(MODULE.ImportantEventDetectorError, error):
                self.build(ratified_policy=value)

    def test_duplicate_rules_and_events_fail_closed(self):
        with self.assertRaisesRegex(MODULE.ImportantEventDetectorError, "POLICY_RULE_MATCH_DUPLICATE"):
            self.build(ratified_policy=policy([rule(), rule("SEC.MATERIAL.FILING.2")]))
        with self.assertRaisesRegex(MODULE.ImportantEventDetectorError, "EVENT_ID_DUPLICATE"):
            self.build([event(), copy.deepcopy(event())])

    def test_one_and_two_character_subject_ids_are_valid(self):
        for subject_id in ("A", "MU"):
            with self.subTest(subject_id=subject_id):
                packet = self.build([event(subject_id=subject_id)])
                self.assertEqual(packet["detections"][0]["subject_id"], subject_id)

    def test_malformed_subject_ids_fail_closed_without_relaxing_generic_tokens(self):
        for subject_id in ("", "mu", "/MU", "MU/../X", " MU"):
            with self.subTest(subject_id=subject_id), self.assertRaisesRegex(
                MODULE.ImportantEventDetectorError, "SUBJECT_ID_INVALID"
            ):
                self.build([event(subject_id=subject_id)])
        self.assertIsNone(MODULE.TOKEN_RE.fullmatch("MU"))
        self.assertIsNotNone(MODULE.SUBJECT_ID_RE.fullmatch("MU"))
        with self.assertRaisesRegex(
            MODULE.ImportantEventDetectorError, "EVENT_TYPE_INVALID"
        ):
            self.build([event(event_type="MU")])

    def test_time_digest_and_authority_drift_fail_closed(self):
        reversed_time = batch([event(received_at="2026-08-21T02:58:00Z")])
        with self.assertRaisesRegex(MODULE.ImportantEventDetectorError, "EVENT_TIME_ORDER_INVALID"):
            MODULE.build_packet(reversed_time, policy(), self.DETECTED_AT, CONTRACT)
        digest = batch()
        digest["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(MODULE.ImportantEventDetectorError, "EVENT_BATCH_SHA_MISMATCH"):
            MODULE.build_packet(digest, policy(), self.DETECTED_AT, CONTRACT)
        authority = batch()
        authority["authority"]["notification_authorized"] = True
        authority["packet_sha256"] = MODULE.payload_sha256({key: value for key, value in authority.items() if key != "packet_sha256"})
        with self.assertRaisesRegex(MODULE.ImportantEventDetectorError, "EVENT_BATCH_IDENTITY_INVALID"):
            MODULE.build_packet(authority, policy(), self.DETECTED_AT, CONTRACT)

    def test_output_never_sends_notifications_or_creates_actions_orders(self):
        packet = self.build()
        row = packet["detections"][0]
        self.assertEqual(row["notification_status"], "NOT_SENT")
        self.assertIsNone(row["action"])
        self.assertIsNone(row["order_intent"])
        self.assertEqual(packet["summary"]["notification_sent_count"], 0)
        tampered = copy.deepcopy(packet)
        tampered["detections"][0]["notification_status"] = "SENT"
        with self.assertRaisesRegex(MODULE.ImportantEventDetectorError, "PACKET_CONTENT_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_exact_sources_make_output_self_validating(self):
        source_batch, source_policy = batch(), policy()
        packet = MODULE.build_packet(
            source_batch, source_policy, self.DETECTED_AT, CONTRACT
        )
        self.assertEqual(packet["source_packets"]["EVENT_BATCH"], source_batch)
        self.assertEqual(packet["source_packets"]["POLICY"], source_policy)
        self.assertEqual(MODULE.validate_packet(packet, CONTRACT), packet)

        tampered = copy.deepcopy(packet)
        embedded = tampered["source_packets"]["POLICY"]
        embedded["authority"]["notification_authorized"] = True
        embedded["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in embedded.items() if key != "packet_sha256"}
        )
        tampered["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in tampered.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.ImportantEventDetectorError, "POLICY_IDENTITY_INVALID"
        ):
            MODULE.validate_packet(tampered, CONTRACT)

    def test_deterministic_permutation_safe_and_inputs_immutable(self):
        rows = [event(), event("SEC.20260821.0002", subject_id="US.XNAS.MSFT")]
        source_batch, source_policy = batch(rows), policy()
        before = MODULE.canonical_json([source_batch, source_policy])
        first = MODULE.build_packet(source_batch, source_policy, self.DETECTED_AT, CONTRACT)
        repeated = MODULE.build_packet(source_batch, source_policy, self.DETECTED_AT, CONTRACT)
        second = MODULE.build_packet(batch(list(reversed(rows))), policy(), self.DETECTED_AT, CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(repeated))
        self.assertEqual(first["detections"], second["detections"])
        self.assertEqual(first["summary"], second["summary"])
        self.assertNotEqual(first["lineage"]["event_batch_sha256"], second["lineage"]["event_batch_sha256"])
        self.assertEqual(MODULE.canonical_json([source_batch, source_policy]), before)

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
            event_path, policy_path = temp / "events.json", temp / "policy.json"
            event_path.write_text(json.dumps(batch()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(event_path, policy_path, self.DETECTED_AT, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "important_event_detector_test.json"
            self.assertEqual(MODULE.run(event_path, policy_path, self.DETECTED_AT, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
