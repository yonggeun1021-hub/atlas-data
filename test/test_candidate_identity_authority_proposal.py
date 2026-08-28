from __future__ import annotations

import copy
import datetime as dt
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity.candidate_identity_authority_proposal import (
    AUTHORITY_ALL_FALSE, COMPLETE, INCOMPLETE,
    CandidateIdentityAuthorityProposalError, _proposal, build_packet, validate_packet,
)


class CandidateIdentityAuthorityProposalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gaps = json.loads((ROOT / "evidence/operational/dynamic_clock/candidate_identity_gap_inventory.json").read_text())
        cls.taxonomy = ROOT / "config/crypto_breadth_exclusion_taxonomy.json"
        cls.raw = ROOT / "evidence/crypto/breadth/raw"
        cls.packet = build_packet(cls.gaps, cls.taxonomy, cls.raw)

    def test_real_gap_population_reconciles(self):
        expected_ids = {
            row["candidate_id"] for row in self.gaps["identity_gaps"]
        }
        proposal_ids = {row["candidate_id"] for row in self.packet["proposals"]}
        expected_count = len(expected_ids)
        self.assertEqual(self.packet["summary"]["gap_count"], expected_count)
        self.assertEqual(self.packet["summary"]["proposal_count"], expected_count)
        self.assertEqual(proposal_ids, expected_ids)
        self.assertEqual(
            sum(self.packet["summary"]["review_status_counts"].values()),
            expected_count,
        )
        self.assertEqual(
            set(self.packet["summary"]["review_status_counts"]),
            {row["review_status"] for row in self.packet["proposals"]},
        )
        # The live gap population is expected to change as identity rows are
        # resolved.  Reconcile every currently-present Crypto proposal to its
        # own exact provider pair instead of requiring DOGE/USD to remain a
        # gap forever.
        for proposal in self.packet["proposals"]:
            if proposal["market"] == "CRYPTO" and proposal["review_status"] == COMPLETE:
                self.assertEqual(
                    proposal["proposed_rows"]["source_alias"]["source_asset_id"],
                    proposal["subject"],
                )

    def test_mechanical_proposals_remain_unratified_and_create_no_authority(self):
        self.assertEqual(self.packet["summary"]["canonical_authority_rows_created"], 0)
        self.assertFalse(self.packet["policy_boundary"]["proposal_is_identity_authority"])
        self.assertEqual(self.packet["authority"], AUTHORITY_ALL_FALSE)
        for row in self.packet["proposals"]:
            self.assertEqual(row["authority"], AUTHORITY_ALL_FALSE)
            self.assertNotEqual(row.get("proposal_status"), "RATIFIED")

    def test_exact_kraken_pair_and_taxonomy_are_both_required(self):
        gap = copy.deepcopy(self.gaps["identity_gaps"][0])
        gap["provider_pair_diagnostics"][0]["diagnostic_status"] = "TAXONOMY_RECORD_NOT_FOUND"
        row = _proposal(gap, {})
        self.assertEqual(row["review_status"], INCOMPLETE)

    def test_provider_display_alias_may_differ_when_exact_key_and_structured_identity_match(self):
        gap = {
            "candidate_id": "doge",
            "market": "CRYPTO",
            "subject": "DOGE/USD",
            "provider_pair_diagnostics": [{
                "source_asset_id": "DOGE/USD",
                "taxonomy_canonical_asset_id": "DOGE",
                "diagnostic_status": "MECHANICAL_TAXONOMY_SYMBOL_MATCH_DIAGNOSTIC",
                "source_name": "kraken_asset_pairs",
                "taxonomy_effective_from": "2026-08-22",
            }],
        }
        pairs = {
            "DOGE/USD": {
                "wsname": "XDG/USD",
                "base": "DOGE",
                "quote": "USD",
                "status": "online",
            }
        }
        self.assertEqual(_proposal(gap, pairs)["review_status"], COMPLETE)
        pairs["DOGE/USD"]["base"] = "NOT_DOGE"
        self.assertEqual(_proposal(gap, pairs)["review_status"], INCOMPLETE)

    def test_resigned_source_gap_tamper_is_independently_rejected(self):
        gaps = copy.deepcopy(self.gaps)
        gaps["identity_gaps"][0]["subject"] = "TAMPERED"
        unsigned = dict(gaps)
        unsigned.pop("packet_sha256", None)
        import hashlib
        gaps["packet_sha256"] = hashlib.sha256(
            json.dumps(unsigned, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()
        with self.assertRaisesRegex(
            CandidateIdentityAuthorityProposalError,
            "SOURCE_GAP_INVENTORY_INDEPENDENT_VALIDATION_FAILED",
        ):
            build_packet(gaps, self.taxonomy, self.raw)

    def test_korea_direct_review_uses_two_official_sources_and_stays_unclassified(self):
        row = next(
            x for x in self.packet["proposals"]
            if x["market"] == "KOREA" and x["subject"] == "034020"
        )
        self.assertEqual(row["subject"], "034020")
        self.assertEqual(row["review_status"], COMPLETE)
        self.assertEqual(row["proposed_rows"]["issuer"]["canonical_issuer_id"], "DART:00159616")
        self.assertEqual(row["proposed_rows"]["instrument"]["instrument_type"], "OTHER_UNCLASSIFIED")
        self.assertEqual(row["proposed_rows"]["listing"]["listing_id"], "XKRX:034020")
        self.assertEqual(row["source_evidence"]["krx"]["source"], "KRX 정보데이터시스템 (pykrx)")
        self.assertEqual(row["source_evidence"]["dart"]["source"], "OpenDART (금융감독원)")

    def test_korea_cross_source_name_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day = root / self.gaps["decision_date"]
            day.mkdir()
            for name in ("krx.json", "dart.json"):
                shutil.copy2(ROOT / "data" / self.gaps["decision_date"] / name, day / name)
            dart = json.loads((day / "dart.json").read_text())
            dart["stocks"]["034020"]["name"] = "다른회사"
            (day / "dart.json").write_text(json.dumps(dart, ensure_ascii=False))
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KOREA_CROSS_SOURCE_NAME_MISMATCH"):
                build_packet(self.gaps, self.taxonomy, self.raw, market_data_root=root)

    def test_korea_future_collector_timestamp_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            day = root / self.gaps["decision_date"]
            day.mkdir()
            for name in ("krx.json", "dart.json"):
                shutil.copy2(ROOT / "data" / self.gaps["decision_date"] / name, day / name)
            krx = json.loads((day / "krx.json").read_text())
            future_date = dt.date.fromisoformat(self.gaps["decision_date"]) + dt.timedelta(days=1)
            krx["collected_at_utc"] = future_date.isoformat() + "T00:00:00Z"
            (day / "krx.json").write_text(json.dumps(krx, ensure_ascii=False))
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KOREA_KRX_EVIDENCE_INVALID"):
                build_packet(self.gaps, self.taxonomy, self.raw, market_data_root=root)

    def test_korea_subject_must_equal_the_exact_provider_symbol(self):
        gap = copy.deepcopy(next(x for x in self.gaps["identity_gaps"] if x["market"] == "KOREA"))
        source_id = gap["provider_pair_diagnostics"][0]["source_asset_id"]
        evidence = next(
            row for row in self.packet["source_korea_identity_evidence"].values()
            if row["symbol"] == source_id
        )
        gap["subject"] = f"NOT-{source_id}"
        row = _proposal(gap, {}, evidence)
        self.assertEqual(row["review_status"], INCOMPLETE)
        self.assertEqual(row["reason_codes"], ["KOREA_SUBJECT_SOURCE_ID_MISMATCH"])

    def test_no_canonical_authority_configuration_is_modified_or_embedded(self):
        self.assertFalse(self.packet["policy_boundary"]["canonical_config_modified"])
        proposal_text = json.dumps(self.packet["proposals"])
        self.assertNotIn('"approval_status": "RATIFIED"', proposal_text)
        self.assertNotIn('"ratified_at"', proposal_text)

    def test_validator_rebuilds_and_rejects_resigned_tamper(self):
        packet = copy.deepcopy(self.packet)
        packet["proposals"][0]["proposal_status"] = "RATIFIED"
        packet["packet_sha256"] = "0" * 64
        with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "PROPOSAL_PACKET_MISMATCH"):
            validate_packet(packet, self.gaps, self.taxonomy, self.raw)

    def test_validator_replays_the_advertised_capture_not_a_later_capture(self):
        decision = dt.date.fromisoformat(self.gaps["decision_date"])
        captures = sorted(
            path.name for path in self.raw.iterdir()
            if path.is_dir()
            and (path / "_manifest.json").is_file()
            and dt.date.fromisoformat(path.name) <= decision
        )
        self.assertGreaterEqual(len(captures), 2)
        packet = build_packet(
            self.gaps,
            self.taxonomy,
            self.raw,
            kraken_capture_date=captures[-2],
        )
        self.assertEqual(packet["source_kraken_capture"]["capture_date"], captures[-2])
        self.assertEqual(validate_packet(packet, self.gaps, self.taxonomy, self.raw), packet)

    def test_missing_eligible_capture_is_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            empty = Path(td)
            with self.assertRaisesRegex(CandidateIdentityAuthorityProposalError, "KRAKEN_CAPTURE_NOT_AVAILABLE"):
                build_packet(self.gaps, self.taxonomy, empty)

    def test_output_is_deterministic(self):
        self.assertEqual(self.packet, build_packet(copy.deepcopy(self.gaps), self.taxonomy, self.raw))

    def test_run_all_registers_proposal_contract(self):
        self.assertIn('"test/test_candidate_identity_authority_proposal.py"', (ROOT / "run_all.py").read_text())


if __name__ == "__main__":
    unittest.main()
