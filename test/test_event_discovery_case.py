#!/usr/bin/env python3
"""P3-08 SEC D1 event → evidence-linked Discovery Case regression."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "discovery" / "event_case.py"
SPEC = importlib.util.spec_from_file_location("event_case", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

ACCESSION = "0001628280-26-053346"
URL = "https://www.sec.gov/Archives/edgar/data/2023554/example.htm"


def d1_record(**changes):
    value = {
        "ticker": "SNDK",
        "name": "SanDisk",
        "atlas_stage": "Discovery",
        "coverage": True,
        "filing_date": "2026-08-05",
        "form": "8-K",
        "form_family": "current_report",
        "accession": ACCESSION,
        "item_codes": ["1.01"],
        "url": URL,
        "event_types": ["Contract"],
        "undetermined": ["Guidance", "Litigation"],
        "resolution": "resolved",
        "classification_reason": "item_map",
        "unknown_item_codes": [],
        "taxonomy_gap_codes": [],
        "taxonomy_version": "1.0",
        "decision_version": "d1_v1",
        "collector_version": "sec_v2",
        "source_collected_for": "2026-08-06",
    }
    value.update(changes)
    return value


def record_key(record=None):
    return MODULE.D1.record_key(record or d1_record())


def evidence(**changes):
    value = {
        "schema_version": "event_source_evidence/1",
        "source_system": "SEC_EDGAR",
        "subject": "SNDK",
        "event_date": "2026-08-05",
        "source_identity": {
            "source_id": "sec_edgar",
            "accession": ACCESSION,
            "source_url": URL,
            "source_sha256": "a" * 64,
            "available_at": "2026-08-05",
            "retrieved_at_utc": "2026-08-06T00:00:00Z",
        },
    }
    value.update(changes)
    return value


def bindings(*rows):
    return {
        "schema_version": "event_case_evidence_bindings/1",
        "binding_set_id": "approved-test-set",
        "bindings": list(rows),
    }


def binding(record=None, proof=None):
    return {
        "source_record_key": record_key(record),
        "evidence": proof or evidence(),
    }


def rehash(packet):
    value = copy.deepcopy(packet)
    value.pop("packet_sha256", None)
    value["packet_sha256"] = MODULE.payload_sha256(value)
    return value


class EventDiscoveryCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = MODULE.load_contract()

    def test_contract_reuses_d1_and_keeps_all_authority_closed(self):
        self.assertEqual(
            self.contract["classification_source"],
            {
                "module": "decision/event_classifier.py",
                "taxonomy_version": MODULE.D1.TAXONOMY_VERSION,
                "decision_version": MODULE.D1.DECISION_VERSION,
                "supported_resolutions": ["resolved", "partial"],
            },
        )
        self.assertEqual(tuple(MODULE.D1.EVENT_TYPES), (
            "Contract", "Management", "Cybersecurity", "Financial Results",
            "Capital", "M&A", "Reg FD", "Distress", "Accounting", "Other",
        ))
        self.assertEqual(self.contract["importance_policy_status"], "UNRATIFIED")
        self.assertFalse(self.contract["automatic_promotion_authorized"])
        self.assertEqual(self.contract["source_coverage"], {
            "sec_edgar": "CLASSIFICATION_SUPPORTED",
            "dart_open_api": "ITEM_EXTRACTION_UNRATIFIED",
            "news": "NOT_IMPLEMENTED",
            "policy": "NOT_IMPLEMENTED",
            "crypto": "NOT_IMPLEMENTED",
        })
        self.assertTrue(self.contract["authority"]["case_recording_only"])
        self.assertTrue(all(
            value is False
            for key, value in self.contract["authority"].items()
            if key != "case_recording_only"
        ))

        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in (
            "requests", "socket", "http", "subprocess", "git", "notion",
            "evaluator",
        ):
            self.assertNotIn(prohibited, imported)

    def test_resolved_d1_event_creates_case_but_never_promotes(self):
        packet = MODULE.build_packet(
            records=[d1_record()], evidence_bindings=bindings(), contract=self.contract
        )
        self.assertEqual(packet["summary"], {
            "source_records": 1,
            "cases": 1,
            "excluded_records": 0,
            "EVIDENCE_LINKED": 0,
            "EVIDENCE_BLOCKED": 0,
            "EVIDENCE_UNRESOLVED": 1,
        })
        case = packet["cases"][0]
        self.assertEqual(case["schema_version"], "discovery_case/1")
        self.assertEqual(case["market"], "US")
        self.assertEqual(case["subject"], "SNDK")
        self.assertEqual(case["event_type"], "Contract")
        self.assertEqual(case["event_date"], "2026-08-05")
        self.assertEqual(case["evidence_status"], MODULE.EVIDENCE_UNRESOLVED)
        self.assertEqual(
            case["evidence_reasons"], [MODULE.EXPLICIT_EVIDENCE_BINDING_ABSENT]
        )
        self.assertEqual(case["importance_status"], MODULE.IMPORTANCE_UNRATIFIED)
        self.assertEqual(
            case["interpretation_status"], MODULE.INTERPRETATION_NOT_AUTHORIZED
        )
        self.assertEqual(case["promotion_status"], MODULE.PROMOTION_NOT_AUTHORIZED)
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["investment_action"])

    def test_explicit_evidence_binding_preserves_complete_lineage(self):
        proof = evidence()
        packet = MODULE.build_packet(
            records=[d1_record()], evidence_bindings=bindings(binding(proof=proof)),
            contract=self.contract,
        )
        case = packet["cases"][0]
        self.assertEqual(case["evidence_status"], MODULE.EVIDENCE_LINKED)
        self.assertEqual(case["evidence_reasons"], [])
        self.assertEqual(case["evidence_lineage"], {
            "event_as_of": "2026-08-05",
            "available_at": "2026-08-05",
            "retrieved_at_utc": "2026-08-06T00:00:00Z",
            "source_id": "sec_edgar",
            "source_url": URL,
            "source_sha256": "a" * 64,
            "source_accession": ACCESSION,
            "evidence_sha256": MODULE.payload_sha256(proof),
        })
        self.assertEqual(
            packet["packet_sha256"],
            MODULE.payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"}),
        )
        self.assertEqual(case["importance_status"], MODULE.IMPORTANCE_UNRATIFIED)
        self.assertIsNone(case["stage_transition"])

    def test_standalone_validator_accepts_persisted_packet(self):
        packet = MODULE.build_packet(
            records=[d1_record()],
            evidence_bindings=bindings(binding()),
            contract=self.contract,
        )
        checked = MODULE.validate_packet(copy.deepcopy(packet), self.contract)
        self.assertEqual(MODULE.canonical_json(checked), MODULE.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_case_identity_tamper(self):
        packet = MODULE.build_packet(
            records=[d1_record()], evidence_bindings=bindings(), contract=self.contract
        )
        packet["cases"][0]["case_id"] = "event-case-" + "0" * 24
        with self.assertRaisesRegex(MODULE.EventCaseError, "OUTPUT_CASE_IDENTITY_MISMATCH"):
            MODULE.validate_packet(rehash(packet), self.contract)

    def test_standalone_validator_rejects_rehashed_authority_expansion(self):
        packet = MODULE.build_packet(
            records=[d1_record()], evidence_bindings=bindings(), contract=self.contract
        )
        packet["cases"][0]["importance_status"] = "IMPORTANT"
        with self.assertRaisesRegex(MODULE.EventCaseError, "OUTPUT_CASE_AUTHORITY_EXPANSION"):
            MODULE.validate_packet(rehash(packet), self.contract)

    def test_standalone_validator_rejects_same_source_case_drift(self):
        record = d1_record(
            event_types=["Financial Results", "Other"], item_codes=["2.02", "8.01"]
        )
        packet = MODULE.build_packet(
            records=[record], evidence_bindings=bindings(), contract=self.contract
        )
        packet["cases"][1]["classification"]["item_codes"] = ["8.01"]
        with self.assertRaisesRegex(MODULE.EventCaseError, "OUTPUT_SOURCE_RECORD_CASE_DRIFT"):
            MODULE.validate_packet(rehash(packet), self.contract)

    def test_standalone_validator_rejects_rehashed_summary_tamper(self):
        packet = MODULE.build_packet(
            records=[d1_record()], evidence_bindings=bindings(), contract=self.contract
        )
        packet["summary"][MODULE.EVIDENCE_UNRESOLVED] = 0
        with self.assertRaisesRegex(MODULE.EventCaseError, "OUTPUT_SUMMARY_DERIVATION_MISMATCH"):
            MODULE.validate_packet(rehash(packet), self.contract)

    def test_one_filing_with_multiple_resolved_types_creates_distinct_cases(self):
        record = d1_record(
            event_types=["Financial Results", "Other"],
            item_codes=["2.02", "8.01"],
        )
        packet = MODULE.build_packet(
            records=[record], evidence_bindings=bindings(binding(record=record)),
            contract=self.contract,
        )
        self.assertEqual(packet["summary"]["cases"], 2)
        self.assertEqual(
            sorted(case["event_type"] for case in packet["cases"]),
            ["Financial Results", "Other"],
        )
        self.assertEqual(len({case["case_id"] for case in packet["cases"]}), 2)
        self.assertTrue(all(
            case["source_record_key"] == record_key(record) for case in packet["cases"]
        ))

    def test_partial_classification_preserves_unknown_and_taxonomy_gaps(self):
        record = d1_record(
            resolution="partial",
            event_types=["Other"],
            item_codes=["2.05", "9.99"],
            unknown_item_codes=["9.99"],
            taxonomy_gap_codes=["2.05"],
        )
        packet = MODULE.build_packet(
            records=[record], evidence_bindings=bindings(), contract=self.contract
        )
        case = packet["cases"][0]
        self.assertEqual(case["classification"]["resolution"], "partial")
        self.assertEqual(case["classification"]["unknown_item_codes"], ["9.99"])
        self.assertEqual(case["classification"]["taxonomy_gap_codes"], ["2.05"])
        self.assertEqual(case["classification"]["undetermined"], ["Guidance", "Litigation"])

    def test_unresolved_and_non_narrative_records_are_auditable_exclusions(self):
        unresolved = d1_record(
            accession="0001628280-26-053347", resolution="unresolved",
            event_types=[], item_codes=[],
        )
        not_applicable = d1_record(
            accession="0001628280-26-053348", resolution="not_applicable",
            event_types=[], item_codes=[], form="4", form_family="ownership",
        )
        no_type_partial = d1_record(
            accession="0001628280-26-053349", resolution="partial",
            event_types=[], item_codes=["9.99"], unknown_item_codes=["9.99"],
        )
        packet = MODULE.build_packet(
            records=[not_applicable, no_type_partial, unresolved],
            evidence_bindings=bindings(), contract=self.contract,
        )
        self.assertEqual(packet["summary"]["cases"], 0)
        self.assertEqual(packet["summary"]["excluded_records"], 3)
        reasons = {row["reason"] for row in packet["excluded_records"]}
        self.assertEqual(reasons, {
            "RESOLUTION_UNRESOLVED", "RESOLUTION_NOT_APPLICABLE",
            "NO_RESOLVED_EVENT_TYPE",
        })

    def test_missing_or_invalid_lineage_blocks_without_interpretation(self):
        source = evidence()["source_identity"]
        variants = [
            {**source, "available_at": None},
            {**source, "available_at": "2026-02-30"},
            {**source, "retrieved_at_utc": "2026-08-06"},
            {**source, "source_sha256": "bad"},
            {**source, "source_id": "other"},
            {**source, "available_at": "2026-08-04"},
            {**source, "available_at": "2026-08-07"},
        ]
        for identity in variants:
            with self.subTest(identity=identity):
                proof = evidence(source_identity=identity)
                packet = MODULE.build_packet(
                    records=[d1_record()],
                    evidence_bindings=bindings(binding(proof=proof)),
                    contract=self.contract,
                )
                case = packet["cases"][0]
                self.assertEqual(case["evidence_status"], MODULE.EVIDENCE_BLOCKED)
                self.assertIn(MODULE.EVIDENCE_LINEAGE_INCOMPLETE, case["evidence_reasons"][0])
                self.assertEqual(case["importance_status"], MODULE.IMPORTANCE_UNRATIFIED)
                self.assertIsNone(case["investment_action"])

    def test_identity_ambiguity_and_unapproved_taxonomy_fail_closed(self):
        other = d1_record(accession="0001628280-26-053347")
        cases = [
            ([d1_record(), copy.deepcopy(d1_record())], bindings(), "D1_RECORD_KEY_DUPLICATE"),
            ([d1_record()], bindings(binding(), copy.deepcopy(binding())),
             "BINDING_SOURCE_RECORD_DUPLICATE"),
            ([d1_record()], bindings({
                "source_record_key": record_key(other), "evidence": evidence()
            }), "BINDING_SOURCE_RECORD_UNKNOWN"),
            ([d1_record(taxonomy_version="2.0")], bindings(),
             "D1_TAXONOMY_VERSION_MISMATCH"),
            ([d1_record(event_types=["Guidance"])], bindings(), "D1_EVENT_TYPE_UNKNOWN"),
            ([d1_record()], bindings(binding(proof=evidence(subject="MU"))),
             "EVIDENCE_SUBJECT_MISMATCH"),
            ([d1_record()], bindings(binding(proof=evidence(event_date="2026-08-06"))),
             "EVIDENCE_EVENT_DATE_MISMATCH"),
            ([d1_record()], bindings(binding(proof=evidence(
                source_identity={**evidence()["source_identity"], "accession": other["accession"]}
            ))), "EVIDENCE_ACCESSION_MISMATCH"),
            ([d1_record()], bindings(binding(proof=evidence(
                source_identity={
                    **evidence()["source_identity"],
                    "source_url": "https://example.com/not-sec",
                }
            ))), "EVIDENCE_SOURCE_URL_MISMATCH"),
            (
                [d1_record(resolution="not_applicable", event_types=[])],
                bindings(binding(
                    record=d1_record(resolution="not_applicable", event_types=[])
                )),
                "BINDING_SOURCE_RECORD_NOT_CASE_ELIGIBLE",
            ),
        ]
        for records, binding_doc, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(MODULE.EventCaseError, expected):
                    MODULE.build_packet(
                        records=records, evidence_bindings=binding_doc, contract=self.contract
                    )

        expanded = copy.deepcopy(self.contract)
        expanded["authority"]["stage_promotion_authorized"] = True
        with self.assertRaisesRegex(MODULE.EventCaseError, "AUTHORITY_BOUNDARY_MISMATCH"):
            MODULE.build_packet(
                records=[d1_record()], evidence_bindings=bindings(), contract=expanded
            )

    def test_committed_d1_history_is_read_only_compatible(self):
        path = ROOT / "data" / "event_records.jsonl"
        before = path.stat().st_mtime_ns
        rows = MODULE.load_jsonl(path)
        packet = MODULE.build_packet(
            records=rows, evidence_bindings=bindings(), contract=self.contract
        )
        self.assertEqual(packet["summary"]["source_records"], len(rows))
        self.assertGreater(packet["summary"]["cases"], 0)
        self.assertEqual(
            packet["summary"][MODULE.EVIDENCE_UNRESOLVED], packet["summary"]["cases"]
        )
        self.assertTrue(all(
            case["importance_status"] == MODULE.IMPORTANCE_UNRATIFIED
            and case["stage_transition"] is None
            for case in packet["cases"]
        ))
        self.assertEqual(path.stat().st_mtime_ns, before)

    def test_record_and_binding_permutation_is_byte_deterministic(self):
        second = d1_record(
            ticker="NVDA", name="NVIDIA", accession="0001628280-26-053347",
            event_types=["Cybersecurity"], item_codes=["1.05"],
        )
        second_evidence = evidence(
            subject="NVDA",
            source_identity={
                **evidence()["source_identity"],
                "accession": second["accession"],
                "source_sha256": "b" * 64,
            },
        )
        first_binding = binding()
        second_binding = binding(record=second, proof=second_evidence)
        packet_a = MODULE.build_packet(
            records=[d1_record(), second],
            evidence_bindings=bindings(first_binding, second_binding),
            contract=self.contract,
        )
        packet_b = MODULE.build_packet(
            records=[second, d1_record()],
            evidence_bindings=bindings(second_binding, first_binding),
            contract=self.contract,
        )
        self.assertEqual(MODULE.canonical_json(packet_a), MODULE.canonical_json(packet_b))

    def test_cli_uses_jsonl_and_writes_only_requested_temp_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            records = root / "records.jsonl"
            proof = root / "bindings.json"
            out = root / "nested" / "cases.json"
            records.write_text(json.dumps(d1_record()) + "\n", encoding="utf-8")
            proof.write_text(json.dumps(bindings(binding())), encoding="utf-8")
            data_before = (ROOT / "data").stat().st_mtime_ns
            self.assertEqual(MODULE.run([
                "--records", str(records),
                "--evidence-bindings", str(proof),
                "--out", str(out),
            ]), 0)
            packet = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(packet["summary"][MODULE.EVIDENCE_LINKED], 1)
            self.assertEqual(list((root / "nested").glob(".*.tmp")), [])
            self.assertEqual((ROOT / "data").stat().st_mtime_ns, data_before)


if __name__ == "__main__":
    unittest.main()
