#!/usr/bin/env python3
"""P5-03 Rule ↔ Evidence Envelope binding regression (offline/temp only)."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bridge" / "rule_evidence_binding.py"
SPEC = importlib.util.spec_from_file_location("rule_evidence_binding", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)

MEASUREMENT = "Azure and other cloud services revenue YoY constant currency"
PERIOD = "2026-06-30"


def available_envelope(**changes):
    value = {
        "schema_version": "evidence_envelope/1",
        "subject": "MSFT",
        "measurement_identity": MEASUREMENT,
        "economic_period_end": PERIOD,
        "status": "EVIDENCE_AVAILABLE",
        "reasons": [],
        "consumable": True,
        "blocked_by": [],
        "acquisition_provenance_present": True,
        "source_identity": {
            "source_id": "msft_official_earnings_release",
            "source_name": "Microsoft official earnings release filed as SEC EX-99.1",
            "source_url": "https://www.sec.gov/Archives/edgar/data/789019/example.htm",
            "source_sha256": "a" * 64,
            "available_at": "2026-07-30",
            "retrieved_at_utc": "2026-07-30T21:00:00Z",
        },
        "audit_provenance": {"capture_kind": "VERBATIM_SOURCE_SLICE"},
        "observation": {"raw_value": "39%", "numeric_value": "39", "unit": "pct"},
    }
    value.update(changes)
    return value


def binding_doc(*rows):
    return {
        "schema_version": "rule_evidence_bindings/1",
        "binding_set_id": "approved-test-binding-set",
        "bindings": list(rows),
    }


def binding(rule_id="RULE-0021", subject="MSFT", measurement=MEASUREMENT, period=PERIOD):
    return {
        "rule_id": rule_id,
        "selection_mode": "ALL_REQUIRED",
        "evidence_keys": [{
            "subject": subject,
            "measurement_identity": measurement,
            "economic_period_end": period,
        }],
    }


class RuleEvidenceBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = MODULE.load_contract()
        cls.rules = MODULE.load_rules()

    def test_contract_and_source_preserve_authority_boundary(self):
        self.assertEqual(len(self.rules["rules"]), 25)
        self.assertFalse(self.rules["consumable_by_evaluator"])
        self.assertEqual(self.contract["source_hierarchy_status"], "UNRATIFIED")
        self.assertFalse(self.contract["automatic_binding_authorized"])
        self.assertEqual(self.contract["selection_mode"], "ALL_REQUIRED")
        self.assertEqual(self.contract["authority"], {
            "linkage_only": True,
            "automatic_source_selection_authorized": False,
            "interpretation_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })

        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in (
            "requests", "urllib", "socket", "http", "subprocess", "git", "notion",
            "evaluator",
        ):
            self.assertNotIn(prohibited, imported)

    def test_explicit_available_link_preserves_lineage_without_rule_result(self):
        envelope = available_envelope()
        packet = MODULE.build_packet(
            envelopes=[envelope], bindings=binding_doc(binding()),
            rules=self.rules, contract=self.contract,
        )
        self.assertEqual(packet["summary"], {
            "total_rules": 25,
            "LINK_AVAILABLE": 1,
            "LINK_BLOCKED": 0,
            "LINK_UNRESOLVED": 24,
        })
        linked = next(row for row in packet["rules"] if row["rule_id"] == "RULE-0021")
        self.assertEqual(linked["link_status"], MODULE.LINK_AVAILABLE)
        self.assertEqual(linked["evaluation_status"], MODULE.EVALUATION_NOT_AUTHORIZED)
        self.assertIsNone(linked["rule_result"])
        self.assertEqual(linked["selection_mode"], "ALL_REQUIRED")
        self.assertEqual(len(linked["evidence_references"]), 1)
        ref = linked["evidence_references"][0]
        self.assertEqual(ref["reference_status"], MODULE.LINK_AVAILABLE)
        self.assertEqual(ref["lineage"], {
            "evidence_as_of": PERIOD,
            "available_at": "2026-07-30",
            "retrieved_at_utc": "2026-07-30T21:00:00Z",
            "source_id": "msft_official_earnings_release",
            "source_url": "https://www.sec.gov/Archives/edgar/data/789019/example.htm",
            "source_sha256": "a" * 64,
            "envelope_sha256": MODULE.payload_sha256(envelope),
        })
        self.assertNotIn("observation", ref)
        self.assertEqual(
            packet["packet_sha256"],
            MODULE.payload_sha256({k: v for k, v in packet.items() if k != "packet_sha256"}),
        )
        for row in packet["rules"]:
            self.assertIsNone(row["rule_result"])
            self.assertEqual(row["evaluation_status"], MODULE.EVALUATION_NOT_AUTHORIZED)

    def test_absent_binding_and_reference_remain_unresolved(self):
        empty = MODULE.build_packet(
            envelopes=[], bindings=binding_doc(), rules=self.rules, contract=self.contract
        )
        self.assertEqual(empty["summary"][MODULE.LINK_UNRESOLVED], 25)
        self.assertTrue(all(
            row["link_reasons"] == [MODULE.EXPLICIT_BINDING_ABSENT]
            and row["evidence_references"] == []
            for row in empty["rules"]
        ))

        missing = MODULE.build_packet(
            envelopes=[], bindings=binding_doc(binding()),
            rules=self.rules, contract=self.contract,
        )
        row = next(item for item in missing["rules"] if item["rule_id"] == "RULE-0021")
        self.assertEqual(row["link_status"], MODULE.LINK_UNRESOLVED)
        self.assertEqual(
            row["evidence_references"][0]["reasons"],
            [MODULE.EVIDENCE_REFERENCE_ABSENT],
        )

    def test_unresolved_evidence_is_not_promoted_or_reclassified_as_blocked(self):
        envelope = available_envelope(
            status="EVIDENCE_UNRESOLVED", consumable=False, source_identity=None,
            acquisition_provenance_present=False, observation=None,
            reasons=["OBSERVATION_ABSENT"],
        )
        packet = MODULE.build_packet(
            envelopes=[envelope], bindings=binding_doc(binding()),
            rules=self.rules, contract=self.contract,
        )
        row = next(item for item in packet["rules"] if item["rule_id"] == "RULE-0021")
        self.assertEqual(row["link_status"], MODULE.LINK_UNRESOLVED)
        self.assertEqual(row["evidence_references"][0]["reference_status"], MODULE.LINK_UNRESOLVED)
        self.assertIsNone(row["rule_result"])

    def test_missing_or_invalid_available_lineage_blocks_link(self):
        for changes in (
            {"source_identity": {**available_envelope()["source_identity"], "available_at": None}},
            {"source_identity": {**available_envelope()["source_identity"], "available_at": "2026-02-30"}},
            {"source_identity": {**available_envelope()["source_identity"], "retrieved_at_utc": "2026-07-30"}},
            {"source_identity": {**available_envelope()["source_identity"], "source_sha256": "bad"}},
            {"consumable": False},
        ):
            with self.subTest(changes=changes):
                packet = MODULE.build_packet(
                    envelopes=[available_envelope(**changes)], bindings=binding_doc(binding()),
                    rules=self.rules, contract=self.contract,
                )
                row = next(
                    item for item in packet["rules"] if item["rule_id"] == "RULE-0021"
                )
                self.assertEqual(row["link_status"], MODULE.LINK_BLOCKED)
                self.assertIsNone(row["rule_result"])

    def test_blocked_reference_blocks_all_required_binding(self):
        second_period = "2026-03-31"
        first = available_envelope()
        second = available_envelope(
            economic_period_end=second_period,
            status="EVIDENCE_BLOCKED",
            consumable=False,
            blocked_by=["REVISION_AUTHORITY_UNRESOLVED"],
            reasons=["REVISION_AUTHORITY_UNRESOLVED"],
            observation=None,
        )
        row = binding()
        row["evidence_keys"].append({
            "subject": "MSFT", "measurement_identity": MEASUREMENT,
            "economic_period_end": second_period,
        })
        packet = MODULE.build_packet(
            envelopes=[first, second], bindings=binding_doc(row),
            rules=self.rules, contract=self.contract,
        )
        linked = next(item for item in packet["rules"] if item["rule_id"] == "RULE-0021")
        self.assertEqual(linked["link_status"], MODULE.LINK_BLOCKED)
        self.assertEqual(
            [ref["economic_period_end"] for ref in linked["evidence_references"]],
            [second_period, PERIOD],
        )

    def test_structural_ambiguity_and_hidden_selection_fail_closed(self):
        cases = [
            ([available_envelope(), copy.deepcopy(available_envelope())], binding_doc(binding()),
             "ENVELOPE_KEY_DUPLICATE"),
            ([available_envelope()], binding_doc(binding(rule_id="RULE-9999")),
             "BINDING_RULE_UNKNOWN"),
            ([available_envelope()], binding_doc(binding(subject="TSM")),
             "BINDING_SUBJECT_MISMATCH"),
            ([available_envelope()], binding_doc(binding(), copy.deepcopy(binding())),
             "BINDING_RULE_DUPLICATE"),
        ]
        non_all = binding()
        non_all["selection_mode"] = "FIRST_AVAILABLE"
        cases.append(([available_envelope()], binding_doc(non_all), "BINDING_SELECTION_MODE_INVALID"))
        for envelopes, bindings, message in cases:
            with self.subTest(message=message):
                with self.assertRaisesRegex(MODULE.RuleEvidenceBindingError, message):
                    MODULE.build_packet(
                        envelopes=envelopes, bindings=bindings,
                        rules=self.rules, contract=self.contract,
                    )

    def test_input_permutation_is_byte_deterministic(self):
        other_measurement = "Azure second explicit evidence"
        other_period = "2026-03-31"
        first = available_envelope()
        second = available_envelope(
            measurement_identity=other_measurement, economic_period_end=other_period,
            source_identity={
                **available_envelope()["source_identity"], "source_sha256": "b" * 64
            },
        )
        refs = binding()["evidence_keys"] + [{
            "subject": "MSFT", "measurement_identity": other_measurement,
            "economic_period_end": other_period,
        }]
        row_a = binding()
        row_a["evidence_keys"] = refs
        row_b = copy.deepcopy(row_a)
        row_b["evidence_keys"].reverse()
        packet_a = MODULE.build_packet(
            envelopes=[first, second], bindings=binding_doc(row_a),
            rules=self.rules, contract=self.contract,
        )
        packet_b = MODULE.build_packet(
            envelopes=[second, first], bindings=binding_doc(row_b),
            rules=self.rules, contract=self.contract,
        )
        self.assertEqual(MODULE.canonical_json(packet_a), MODULE.canonical_json(packet_b))

    def test_cli_writes_only_requested_temp_output_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            envelopes = root / "envelopes.json"
            bindings = root / "bindings.json"
            out = root / "nested" / "packet.json"
            envelopes.write_text(
                json.dumps({"envelopes": [available_envelope()]}), encoding="utf-8"
            )
            bindings.write_text(json.dumps(binding_doc(binding())), encoding="utf-8")
            before = (ROOT / "data").stat().st_mtime_ns
            self.assertEqual(MODULE.run([
                "--envelopes", str(envelopes), "--bindings", str(bindings),
                "--out", str(out),
            ]), 0)
            self.assertTrue(out.exists())
            self.assertEqual(json.loads(out.read_text())["summary"][MODULE.LINK_AVAILABLE], 1)
            self.assertEqual(list((root / "nested").glob(".*.tmp")), [])
            self.assertEqual((ROOT / "data").stat().st_mtime_ns, before)


if __name__ == "__main__":
    unittest.main()
