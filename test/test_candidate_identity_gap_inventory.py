#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci
from identity.candidate_identity_observation import build_observation
from identity.candidate_identity_gap_inventory import (
    CandidateIdentityGapInventoryError,
    DIAGNOSTIC_MATCH,
    DIAGNOSTIC_NO_RECORD,
    DIAGNOSTIC_UNSUPPORTED_PAIR,
    _load_taxonomy,
    _sha256,
    _taxonomy_diagnostic,
    build_inventory,
    validate_inventory,
)

FIXTURE_REPORT_PATH = (
    ROOT / "evidence" / "operational" / "dynamic_clock"
    / "candidate_validity_source_reports"
    / "report-8dce78ebbbd43fb241afd77270ef80e67e8ab6ca2d89184302421707c4271512.json"
)


def _identity_fixture_report() -> dict:
    full = json.loads(FIXTURE_REPORT_PATH.read_text(encoding="utf-8"))
    crypto_rows = full["by_market"]["CRYPTO"]["review_queue"]
    unresolved_crypto_subject = next(
        row["subject"] for row in crypto_rows if row["subject"] != "BTC"
    )
    wanted = {"BTC", "005930", unresolved_crypto_subject}
    by_market = {}
    for market, result in full["by_market"].items():
        rows = [
            copy.deepcopy(row)
            for row in result["review_queue"]
            if row["subject"] in wanted
        ]
        if rows:
            by_market[market] = {"review_queue": rows}
    return {
        "decision_date": full["decision_date"],
        "operational_evaluation": copy.deepcopy(full["operational_evaluation"]),
        "by_market": by_market,
    }


class CandidateIdentityGapInventoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.report = _identity_fixture_report()
        cls.authority = ci.load_authority()
        cls.scope_authority = ci.load_scope_authority()
        cls.observation = build_observation(
            cls.report, cls.authority, cls.scope_authority
        )
        cls.taxonomy_path = ROOT / "config/crypto_breadth_exclusion_taxonomy.json"
        cls.taxonomy, cls.records = _load_taxonomy(cls.taxonomy_path)
        cls.taxonomy_sha = hashlib.sha256(cls.taxonomy_path.read_bytes()).hexdigest()
        cls.baseline_packet = build_inventory(
            cls.observation,
            cls.report,
            cls.authority,
            cls.scope_authority,
            cls.taxonomy,
            cls.records,
            taxonomy_bytes_sha256=cls.taxonomy_sha,
        )

    def build(self):
        return copy.deepcopy(self.baseline_packet)

    def test_real_population_reconciles_without_creating_authority(self):
        packet = self.build()
        source = self.observation["summary"]
        self.assertEqual(packet["summary"]["candidate_count"], source["candidate_count"])
        self.assertEqual(
            packet["summary"]["identity_resolved_count"], source["identity_resolved_count"]
        )
        self.assertEqual(
            packet["summary"]["identity_gap_count"],
            source["candidate_count"] - source["identity_resolved_count"],
        )
        self.assertGreater(packet["summary"]["identity_gap_count"], 0)
        self.assertEqual(packet["policy_boundary"]["authority_rows_created"], 0)
        self.assertFalse(packet["policy_boundary"]["taxonomy_category_is_identity_authority"])

    def test_existing_resolved_instruments_are_not_identity_gaps(self):
        subjects = {row["subject"] for row in self.build()["identity_gaps"]}
        self.assertNotIn("BTC", subjects)
        self.assertNotIn("005930", subjects)
        self.assertNotIn("000660", subjects)

    def test_zero_candidate_population_builds_an_empty_valid_inventory(self):
        report = {
            "decision_date": self.report["decision_date"],
            "operational_evaluation": copy.deepcopy(
                self.report["operational_evaluation"]
            ),
            "by_market": {},
        }
        observation = build_observation(
            report, self.authority, self.scope_authority
        )
        packet = build_inventory(
            observation,
            report,
            self.authority,
            self.scope_authority,
            self.taxonomy,
            self.records,
            taxonomy_bytes_sha256=self.taxonomy_sha,
        )
        self.assertEqual(packet["identity_gaps"], [])
        self.assertEqual(packet["summary"]["candidate_count"], 0)
        self.assertEqual(packet["summary"]["identity_gap_count"], 0)
        self.assertEqual(
            validate_inventory(
                packet,
                observation,
                report,
                self.authority,
                self.scope_authority,
                self.taxonomy,
                self.records,
                taxonomy_bytes_sha256=self.taxonomy_sha,
            ),
            packet,
        )

    def test_every_gap_and_packet_authority_remains_false(self):
        packet = self.build()
        self.assertTrue(all(value is False for value in packet["authority"].values()))
        for row in packet["identity_gaps"]:
            self.assertTrue(all(value is False for value in row["authority"].values()))
            self.assertEqual(row["authority_record_status"], "PROPOSED_UNRATIFIED_NOT_CREATED")
            self.assertTrue(all(
                pair["identity_authority_effect"] == "NONE"
                for pair in row["provider_pair_diagnostics"]
            ))

    def test_exact_pair_match_does_not_guess_alias_or_quote_currency(self):
        self.assertEqual(
            _taxonomy_diagnostic(
                {"source_name": "kraken_spot_ohlc", "source_asset_id": "ETH/USD"},
                "2026-08-25",
                self.records,
            )["diagnostic_status"],
            DIAGNOSTIC_MATCH,
        )
        for pair in (
            {"source_name": "kraken_spot_ohlc", "source_asset_id": "ETH/EUR"},
            {"source_name": "another_provider", "source_asset_id": "ETH/USD"},
            {"source_name": "kraken_spot_ohlc", "source_asset_id": "XETH/USD"},
        ):
            with self.subTest(pair=pair):
                self.assertEqual(
                    _taxonomy_diagnostic(pair, "2026-08-25", self.records)["diagnostic_status"],
                    DIAGNOSTIC_UNSUPPORTED_PAIR if pair["source_asset_id"] != "XETH/USD" else DIAGNOSTIC_NO_RECORD,
                )

    def test_taxonomy_effective_interval_is_point_in_time(self):
        future = {"ETH": {"canonical_asset_id": "ETH", "category": "eligible_crypto", "effective_from": "2026-08-26", "effective_to": None}}
        expired = {"ETH": {"canonical_asset_id": "ETH", "category": "eligible_crypto", "effective_from": "2026-08-20", "effective_to": "2026-08-24"}}
        pair = {"source_name": "kraken_spot_ohlc", "source_asset_id": "ETH/USD"}
        self.assertEqual(_taxonomy_diagnostic(pair, "2026-08-25", future)["diagnostic_status"], DIAGNOSTIC_NO_RECORD)
        self.assertEqual(_taxonomy_diagnostic(pair, "2026-08-25", expired)["diagnostic_status"], DIAGNOSTIC_NO_RECORD)

    def test_unratified_or_duplicate_taxonomy_fails_closed(self):
        for mutation, expected in (
            (lambda d: d.update(approval_status="PROPOSED"), "TAXONOMY_NOT_RATIFIED"),
            (lambda d: d["records"].append(copy.deepcopy(d["records"][0])), "TAXONOMY_ASSET_ID_DUPLICATE"),
        ):
            doc = copy.deepcopy(self.taxonomy)
            mutation(doc)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "taxonomy.json"
                path.write_text(json.dumps(doc))
                with self.assertRaisesRegex(CandidateIdentityGapInventoryError, expected):
                    _load_taxonomy(path)

    def test_invalid_category_or_effective_interval_fails_closed(self):
        mutations = (
            (lambda d: d["records"][0].update(category="INVENTED"), "TAXONOMY_CATEGORY_INVALID"),
            (lambda d: d["records"][0].update(effective_from="08/22/2026"), "TAXONOMY_EFFECTIVE_INTERVAL_INVALID"),
            (lambda d: d["records"][0].update(effective_to="2026-08-01"), "TAXONOMY_EFFECTIVE_INTERVAL_INVALID"),
        )
        for mutation, expected in mutations:
            doc = copy.deepcopy(self.taxonomy)
            mutation(doc)
            with tempfile.TemporaryDirectory() as td:
                path = Path(td) / "taxonomy.json"
                path.write_text(json.dumps(doc))
                with self.assertRaisesRegex(CandidateIdentityGapInventoryError, expected):
                    _load_taxonomy(path)

    def test_validator_rebuilds_and_rejects_resigned_semantic_tamper(self):
        packet = self.build()
        packet["identity_gaps"][0]["authority_record_status"] = "RATIFIED"
        packet["packet_sha256"] = _sha256({k: v for k, v in packet.items() if k != "packet_sha256"})
        with self.assertRaisesRegex(CandidateIdentityGapInventoryError, "IDENTITY_GAP_INVENTORY_MISMATCH"):
            validate_inventory(
                packet,
                self.observation,
                self.report,
                self.authority,
                self.scope_authority,
                self.taxonomy,
                self.records,
                taxonomy_bytes_sha256=self.taxonomy_sha,
            )

    def test_injected_or_resigned_source_observation_is_revalidated(self):
        observation = copy.deepcopy(self.observation)
        observation["observations"][0]["identity"]["status"] = "IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD"
        observation["packet_sha256"] = _sha256({k: v for k, v in observation.items() if k != "packet_sha256"})
        with self.assertRaises(Exception):
            build_inventory(
                observation,
                self.report,
                self.authority,
                self.scope_authority,
                self.taxonomy,
                self.records,
                taxonomy_bytes_sha256=self.taxonomy_sha,
            )

    def test_output_is_deterministic(self):
        rebuilt = build_inventory(
            self.observation,
            self.report,
            self.authority,
            self.scope_authority,
            self.taxonomy,
            self.records,
            taxonomy_bytes_sha256=self.taxonomy_sha,
        )
        self.assertEqual(self.build(), rebuilt)

    def test_workflow_builds_inventory_after_validated_identity_observation(self):
        text = (ROOT / ".github/workflows/p8-12-dynamic-clock.yml").read_text()
        identity = "python3 identity/candidate_identity_observation.py"
        inventory = "python3 identity/candidate_identity_gap_inventory.py"
        self.assertIn(identity, text)
        self.assertIn(inventory, text)
        self.assertLess(text.index(identity), text.index(inventory))

    def test_run_all_registers_inventory_contract(self):
        text = (ROOT / "run_all.py").read_text()
        self.assertIn('"test/test_candidate_identity_gap_inventory.py"', text)


if __name__ == "__main__":
    unittest.main()
