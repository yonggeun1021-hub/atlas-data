#!/usr/bin/env python3
"""P8-08 Forward Thesis / Earnings Conversion regression."""

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "forward_thesis.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("forward_thesis", SOURCE)
CONTRACT = MODULE.load_contract()


def evidence_entry(source_ref="SRC.NARRATIVE.1", source_type="NARRATIVE_SOURCED", filing_date=None, **overrides):
    if source_type in {"SEC_EXHIBIT", "DART_EXHIBIT"}:
        value = {
            "source_ref": source_ref,
            "source_type": source_type,
            "accession": "0000320193-26-000001",
            "filing_date": filing_date or "2026-08-15",
            "exhibit_type": "EX-99.1",
            "exhibit_document": "ex991.htm",
            "source_sha256": "a" * 64,
        }
    else:
        value = {
            "source_ref": source_ref,
            "source_type": source_type,
            "accession": None,
            "filing_date": filing_date,
            "exhibit_type": None,
            "exhibit_document": None,
            "source_sha256": None,
        }
    value.update(overrides)
    return value


def observed_fact(source_ref="SRC.NARRATIVE.1", as_of="2026-08-19", **overrides):
    value = {
        "statement": "Curated watchlist note cites a disclosed capacity expansion.",
        "source_ref": source_ref,
        "source_class": "NARRATIVE_SOURCED",
        "as_of": as_of,
    }
    value.update(overrides)
    return value


def forward_inference(**overrides):
    value = {
        "statement": "Revenue conversion is expected to begin once capacity comes online.",
        "basis": "Public capex disclosures and industry lead-time norms.",
        "confidence": "MEDIUM",
    }
    value.update(overrides)
    return value


def earnings_conversion(status="REVENUE_CONVERSION_EXPECTED", **overrides):
    value = {
        "status": status,
        "mechanism": "Hyperscaler capex converts to subject's advanced-packaging revenue.",
        "expected_start_window": "2026Q4-2027Q2",
        "expected_duration": "UNKNOWN",
        "revenue_direction": "UP",
        "margin_direction": "UNKNOWN",
        "confidence": "MEDIUM",
        "blockers": [],
    }
    value.update(overrides)
    return value


def capital_commitment(**overrides):
    value = {
        "description": "Multi-year capacity commitment disclosed in curated watchlist note.",
        "amount_range_or_unknown": "UNKNOWN",
        "currency_or_unknown": "UNKNOWN",
        "source_ref": None,
    }
    value.update(overrides)
    return value


def minimal_input(**overrides):
    value = {
        "schema_version": CONTRACT["output_schema_version"],
        "contract_version": CONTRACT["contract_version"],
        "generated_at": "2026-08-20T09:00:00Z",
        "subject": "TSM",
        "decision_date": "2026-08-20",
        "thesis_id": "FT-TSM-20260820-001",
        "thesis_version": 1,
        "spend_owner": "Hyperscaler-A",
        "capital_commitment": capital_commitment(),
        "revenue_recipient": "Taiwan Semiconductor Manufacturing Co",
        "atlas_linked_ticker": "TSM",
        "observed_facts": [observed_fact()],
        "forward_inferences": [forward_inference()],
        "assumptions": [{
            "statement": "Capacity build-out proceeds on the disclosed timeline.",
            "why_unconfirmed": "No confirmed construction-completion filing yet.",
        }],
        "counter_evidence": [{
            "statement": "A competitor announced a rival capacity expansion.",
            "source_ref": "SRC.NARRATIVE.1",
        }],
        "unknowns": ["Exact contract value not disclosed."],
        "earnings_conversion": earnings_conversion(),
        "catalysts": [{
            "description": "Next quarterly earnings call.",
            "expected_date_or_window": "2026Q4",
            "source_ref": None,
        }],
        "invalidation_conditions": ["Capacity build-out is publicly cancelled or delayed >2 quarters."],
        "review_dates": ["2026-11-15"],
        "evidence_lineage": [evidence_entry()],
        "as_of_ceiling": None,
    }
    value.update(overrides)
    return value


class ForwardThesisTests(unittest.TestCase):
    def build(self, **overrides):
        return MODULE.build_packet(minimal_input(**overrides), CONTRACT)

    def test_valid_minimal_packet_builds_and_validates(self):
        packet = self.build()
        revalidated = MODULE.validate_packet(packet, CONTRACT)
        self.assertEqual(revalidated, packet)
        self.assertEqual(packet["subject"], "TSM")
        self.assertEqual(packet["earnings_conversion"]["status"], "REVENUE_CONVERSION_EXPECTED")

    def test_observed_fact_as_of_after_decision_date_is_rejected(self):
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "OBSERVED_FACT_AS_OF_AFTER_DECISION_DATE"):
            self.build(observed_facts=[observed_fact(as_of="2026-08-21")])

    def test_generated_at_after_ceiling_is_rejected(self):
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "GENERATED_AT_AFTER_CEILING"):
            self.build(generated_at="2026-08-21T00:00:00Z")
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "GENERATED_AT_AFTER_CEILING"):
            self.build(generated_at="2026-08-20T23:59:59Z", as_of_ceiling="2026-08-19")

    def test_fact_without_source_ref_or_with_future_as_of_cannot_impersonate_inference(self):
        no_source = observed_fact()
        no_source["source_ref"] = ""
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "OBSERVED_FACT_SOURCE_REF_INVALID"):
            self.build(observed_facts=[no_source])
        dangling = observed_fact(source_ref="SRC.DOES.NOT.EXIST")
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "OBSERVED_FACT_SOURCE_REF_DANGLING"):
            self.build(observed_facts=[dangling])
        future = observed_fact(as_of="2026-12-01")
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "OBSERVED_FACT_AS_OF_AFTER_DECISION_DATE"):
            self.build(observed_facts=[future])

    def test_empty_invalidation_conditions_is_rejected(self):
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "INVALIDATION_CONDITIONS_EMPTY"):
            self.build(invalidation_conditions=[])

    def test_invalid_earnings_conversion_status_is_rejected(self):
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "EARNINGS_CONVERSION_STATUS_INVALID"):
            self.build(earnings_conversion=earnings_conversion(status="MOONSHOT"))

    def test_every_valid_earnings_conversion_status_builds_including_early_stage(self):
        for status in CONTRACT["earnings_conversion_statuses"]:
            with self.subTest(status=status):
                packet = self.build(earnings_conversion=earnings_conversion(status=status, confidence="LOW"))
                self.assertEqual(packet["earnings_conversion"]["status"], status)
                self.assertEqual(packet["earnings_conversion"]["confidence"], "LOW")

    def test_tamper_detection_rejects_mutated_packet(self):
        packet = self.build()
        # mutate a field but leave packet_sha256 stale -> hash recomputation catches it.
        tampered = copy.deepcopy(packet)
        tampered["earnings_conversion"]["status"] = "CONVERSION_CONFIRMED"
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "OUTPUT_SHA_MISMATCH"):
            MODULE.validate_packet(tampered, CONTRACT)
        # even if the attacker recomputes the hash to match, a structurally
        # invalid mutation (here: wiping invalidation_conditions) still fails
        # closed on rebuild -- rehashing alone cannot legitimize a corrupted result.
        corrupted = copy.deepcopy(packet)
        corrupted["invalidation_conditions"] = []
        corrupted["packet_sha256"] = MODULE.payload_sha256(
            {key: value for key, value in corrupted.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "INVALIDATION_CONDITIONS_EMPTY"):
            MODULE.validate_packet(corrupted, CONTRACT)

    def test_determinism_same_input_yields_byte_identical_output(self):
        input_value = minimal_input()
        before = MODULE.canonical_json(input_value)
        first = MODULE.build_packet(copy.deepcopy(input_value), CONTRACT)
        second = MODULE.build_packet(copy.deepcopy(input_value), CONTRACT)
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(first["packet_sha256"], second["packet_sha256"])
        self.assertEqual(MODULE.canonical_json(input_value), before)

    def test_authority_dict_matches_exact_specified_values(self):
        packet = self.build()
        self.assertEqual(packet["authority"], {
            "thesis_assembly_only": True,
            "forward_inference_generation_authorized": True,
            "stage_promotion_authorized": False,
            "candidate_ready_buy_promotion_authorized": False,
            "rule_pass_fail_authorized": False,
            "rule_result_generation_authorized": False,
            "ticker_identity_mapping_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        })

    def test_bare_precise_capital_commitment_without_source_is_rejected(self):
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "CAPITAL_COMMITMENT_PRECISE_FIGURE_WITHOUT_SOURCE"):
            self.build(capital_commitment=capital_commitment(amount_range_or_unknown="500000000", source_ref=None))
        # a range is fine without a source
        ranged = self.build(capital_commitment=capital_commitment(amount_range_or_unknown="300000000-500000000", source_ref=None))
        self.assertEqual(ranged["capital_commitment"]["amount_range_or_unknown"], "300000000-500000000")
        # a precise figure is fine WITH a source
        sourced = self.build(capital_commitment=capital_commitment(amount_range_or_unknown="500000000", source_ref="SRC.NARRATIVE.1"))
        self.assertEqual(sourced["capital_commitment"]["amount_range_or_unknown"], "500000000")

    def test_low_confidence_does_not_block_packet_creation(self):
        packet = self.build(
            earnings_conversion=earnings_conversion(status="PRE_REVENUE_SIGNAL", confidence="LOW"),
            forward_inferences=[forward_inference(confidence="LOW")],
        )
        self.assertEqual(packet["earnings_conversion"]["confidence"], "LOW")
        self.assertEqual(packet["forward_inferences"][0]["confidence"], "LOW")

    def test_evidence_lineage_filing_date_after_decision_date_is_rejected(self):
        future_exhibit = evidence_entry(
            source_ref="SRC.SEC.1", source_type="SEC_EXHIBIT", filing_date="2026-08-21"
        )
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "EVIDENCE_LINEAGE_FILING_DATE_AFTER_DECISION_DATE"):
            self.build(evidence_lineage=[future_exhibit])

    def test_dangling_and_duplicate_evidence_lineage_fail_closed(self):
        with self.assertRaisesRegex(MODULE.ForwardThesisError, "EVIDENCE_LINEAGE_SOURCE_REF_DUPLICATE"):
            self.build(evidence_lineage=[evidence_entry(), evidence_entry()])

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)
        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            input_path = temp / "input.json"
            input_path.write_text(json.dumps(minimal_input()), encoding="utf-8")
            output = temp / "out" / "packet.json"
            self.assertEqual(MODULE.run(input_path, output), 0)
            self.assertTrue(output.exists())
            forbidden = ROOT / "data" / "forward_thesis_test.json"
            self.assertEqual(MODULE.run(input_path, forbidden), 1)
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
