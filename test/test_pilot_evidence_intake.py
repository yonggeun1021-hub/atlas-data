#!/usr/bin/env python3
"""P8-11 stage 2 — real Pilot evidence intake regression.

Exercises `decision/pilot_evidence_intake.py` against the REAL, already-
committed evidence files in this repository for the four Pilot subjects
(TSM, 298040.KS, 267260.KS, 034020.KS) -- no synthetic fixtures. Every
assertion below protects one of the explicit anti-fabrication rules the
module's own docstring commits to.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "pilot_evidence_intake.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("pilot_evidence_intake", SOURCE)
FORWARD_THESIS = MODULE.FORWARD_THESIS
EXPECTATIONS_GAP = MODULE.EXPECTATIONS_GAP
PRICE_REFLECTION = MODULE.PRICE_REFLECTION


class PilotEvidenceIntakeTests(unittest.TestCase):
    def test_all_four_forward_thesis_packets_validate(self):
        for subject, (ft_builder, _, _) in MODULE._BUILDERS.items():
            with self.subTest(subject=subject):
                ft_input = ft_builder(MODULE.PILOT_DECISION_DATE, MODULE.PILOT_GENERATED_AT)
                packet = FORWARD_THESIS.build_packet(ft_input)
                revalidated = FORWARD_THESIS.validate_packet(packet)
                self.assertEqual(revalidated["subject"], subject)

    def test_all_four_expectations_gap_packets_validate(self):
        for subject, (_, eg_builder, _) in MODULE._BUILDERS.items():
            with self.subTest(subject=subject):
                eg_input = eg_builder(MODULE.PILOT_DECISION_DATE, MODULE.PILOT_GENERATED_AT)
                packet = EXPECTATIONS_GAP.build_packet(eg_input)
                EXPECTATIONS_GAP.validate_packet(packet)

    def test_all_four_price_reflection_packets_validate(self):
        for subject, (_, _, pr_builder) in MODULE._BUILDERS.items():
            with self.subTest(subject=subject):
                pr_input = pr_builder(MODULE.PILOT_DECISION_DATE, MODULE.PILOT_GENERATED_AT)
                packet = PRICE_REFLECTION.build_packet(**pr_input)
                PRICE_REFLECTION.validate_packet(packet)

    def test_doosan_has_zero_observed_facts_and_evidence_lineage(self):
        ft_input = MODULE.build_doosan_enerbility_forward_thesis_input(
            MODULE.PILOT_DECISION_DATE, MODULE.PILOT_GENERATED_AT
        )
        packet = FORWARD_THESIS.build_packet(ft_input)
        self.assertEqual(packet["observed_facts"], [])
        self.assertEqual(packet["evidence_lineage"], [])
        self.assertEqual(packet["atlas_linked_ticker"], "034020.KS")

    def test_tsm_unextracted_accessions_never_back_an_exhibit_extracted_fact(self):
        """Regression guarding the raw-vs-extracted confusion: none of the
        four PENDING/EXTRACTOR_NOT_REGISTERED TSM accessions may ever appear
        as the source_ref of an EXHIBIT_EXTRACTED observed_fact. Only
        0001046179-26-000536 (evidence_status=OK, real extracted quote) may."""
        ft_input = MODULE.build_tsm_forward_thesis_input(
            MODULE.PILOT_DECISION_DATE, MODULE.PILOT_GENERATED_AT
        )
        packet = FORWARD_THESIS.build_packet(ft_input)

        unextracted_refs = {
            f"TSM_SEC_{accession.replace('-', '_')}"
            for accession in MODULE.TSM_ACCESSIONS_UNEXTRACTED
        }
        exhibit_extracted_refs = {
            fact["source_ref"]
            for fact in packet["observed_facts"]
            if fact["source_class"] == "EXHIBIT_EXTRACTED"
        }
        self.assertEqual(unextracted_refs & exhibit_extracted_refs, set())
        self.assertIn("TSM_SEC_000536", exhibit_extracted_refs)

        # The four unextracted accessions must still be present as
        # evidence_lineage (raw, hash-verified, dated) -- just never as an
        # EXHIBIT_EXTRACTED fact's source.
        lineage_refs = {row["source_ref"] for row in packet["evidence_lineage"]}
        self.assertTrue(unextracted_refs.issubset(lineage_refs))

        unknowns_text = " ".join(packet["unknowns"])
        self.assertIn("EXTRACTOR_NOT_REGISTERED", unknowns_text)

    def test_hyosung_backlog_facts_are_narrative_sourced(self):
        ft_input = MODULE.build_hyosung_forward_thesis_input(
            MODULE.PILOT_DECISION_DATE, MODULE.PILOT_GENERATED_AT
        )
        packet = FORWARD_THESIS.build_packet(ft_input)
        backlog_facts = [
            fact for fact in packet["observed_facts"]
            if "조원" in fact["statement"]
        ]
        self.assertTrue(backlog_facts)
        for fact in backlog_facts:
            self.assertEqual(fact["source_class"], "NARRATIVE_SOURCED")
            self.assertEqual(fact["source_ref"], "HYOSUNG_WATCHLIST_ROW")

    def test_run_all_pilots_produces_all_four_subjects(self):
        results = MODULE.run_all_pilots()
        self.assertEqual(set(results), set(MODULE.PILOT_SUBJECTS))
        for subject, bundle in results.items():
            with self.subTest(subject=subject):
                self.assertEqual(
                    set(bundle),
                    {
                        "forward_thesis", "expectations_gap", "price_reflection",
                        "alpha_review", "shadow_ledger_entry",
                    },
                )

    def test_doosan_opportunity_state_is_blocked(self):
        results = MODULE.run_all_pilots()
        alpha = results["034020.KS"]["alpha_review"]
        self.assertEqual(alpha["opportunity_state"], "BLOCKED")
        shadow = results["034020.KS"]["shadow_ledger_entry"]
        self.assertEqual(shadow["shadow_proposal"]["action"], "REJECT")
        self.assertEqual(shadow["shadow_proposal"]["capital"], 0)
        self.assertTrue(shadow["shadow_proposal"]["human_approval_required"])
        self.assertIsNone(alpha["trade_proposal"])

    def test_determinism_byte_identical_packet_sha256(self):
        first = MODULE.run_all_pilots()
        second = MODULE.run_all_pilots()
        for subject in MODULE.PILOT_SUBJECTS:
            for key in ("forward_thesis", "expectations_gap", "price_reflection", "alpha_review"):
                self.assertEqual(
                    first[subject][key]["packet_sha256"],
                    second[subject][key]["packet_sha256"],
                    f"{subject}.{key} not deterministic",
                )
            self.assertEqual(
                first[subject]["shadow_ledger_entry"]["entry_hash"],
                second[subject]["shadow_ledger_entry"]["entry_hash"],
            )


if __name__ == "__main__":
    unittest.main()
