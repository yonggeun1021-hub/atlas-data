#!/usr/bin/env python3
"""Narrow authority regression for the exact KIS PAPER 071050 chain."""
from __future__ import annotations

import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci  # noqa: E402
from identity.kis_071050_proposal_review import (  # noqa: E402
    validate_alias_proposal,
    validate_identity_proposal,
)


RATIFIED_AT = "2026-08-28T17:44:03Z"
KIS_SOURCE = "kis_paper_domestic_balance"
APPROVAL_BOUNDARY = (
    "MECHANICAL_IDENTITY_OR_SCOPE_ONLY_NO_INVESTMENT_OR_TRADING_AUTHORITY"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Kis071050IdentityAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.authority = ci.load_authority()
        cls.identity_proposal = validate_identity_proposal(json.loads(
            (ROOT / "evidence/identity/proposals/kis_071050_instrument_identity_proposal.json")
            .read_text(encoding="utf-8")
        ))
        cls.alias_proposal = validate_alias_proposal(json.loads(
            (ROOT / "evidence/identity/proposals/kis_071050_source_alias_proposal.json")
            .read_text(encoding="utf-8")
        ))

    def test_proposal_packets_remain_unratified_review_inputs(self):
        for packet in (self.identity_proposal, self.alias_proposal):
            self.assertEqual(
                packet["proposalStatus"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY"
            )
            self.assertFalse(packet["canonicalAuthorityConfigMutated"])
            self.assertTrue(all(value is False for value in packet["authority"].values()))

    def test_exact_four_row_chain_is_the_only_new_authority(self):
        expected = (
            (ci.LAYER_ISSUER, "issuers", "atlas.identity.issuer.korea-investment-holdings"),
            (
                ci.LAYER_INSTRUMENT, "instruments",
                "atlas.identity.instrument.korea-investment-holdings-common",
            ),
            (
                ci.LAYER_LISTING, "listings",
                "atlas.identity.listing.korea-investment-holdings-common",
            ),
            (
                ci.LAYER_SOURCE_ALIAS, "source_aliases",
                "atlas.identity.alias.kis-paper-korea-investment-holdings-common",
            ),
        )
        for layer, key, rule_id in expected:
            rows = [row for row in self.authority[key] if row["rule_id"] == rule_id]
            self.assertEqual(len(rows), 1, rule_id)
            row = rows[0]
            self.assertEqual(row["approval_status"], "RATIFIED")
            self.assertEqual(row["ratified_at"], RATIFIED_AT)
            self.assertEqual(row["first_seen_at"], RATIFIED_AT)
            self.assertEqual(row["effective_from"], RATIFIED_AT)
            self.assertIsNone(row["effective_to"])
            self.assertTrue(ci.verify_business_payload(row, layer), rule_id)
            self.assertTrue(ci.verify_approval_evidence(row, layer), rule_id)

    def test_approval_packets_bind_real_proposal_and_review_bytes(self):
        rows = [
            row for key in ("issuers", "instruments", "listings", "source_aliases")
            for row in self.authority[key]
            if "korea-investment-holdings" in row["rule_id"]
        ]
        self.assertEqual(len(rows), 4)
        for row in rows:
            approval_path = ROOT / row["approval_evidence_ref"]
            self.assertEqual(_sha(approval_path), row["approval_evidence_sha256"])
            approval = json.loads(approval_path.read_text(encoding="utf-8"))
            self.assertEqual(approval["boundary"], APPROVAL_BOUNDARY)
            self.assertEqual(approval["assertion"]["review_status"], "REVIEW_READY_FOR_CIO")
            for source in approval["source_evidence"]:
                source_path = ROOT / source["path"]
                self.assertTrue(source_path.is_file(), source)
                self.assertEqual(_sha(source_path), source["sha256"], source)

    def test_only_071050_kis_alias_is_ratified(self):
        aliases = [
            row for row in self.authority["source_aliases"]
            if row.get("source_name") == KIS_SOURCE
        ]
        self.assertEqual(
            [(row["source_asset_id"], row["listing_id"]) for row in aliases],
            [("071050", "XKRX:071050")],
        )
        self.assertTrue({"005930", "000660", "071055"}.isdisjoint(
            {row["source_asset_id"] for row in aliases}
        ))

    def test_resolution_is_pit_gated_and_never_grants_money_authority(self):
        too_early = ci.resolve_instrument_identity(
            KIS_SOURCE, "071050", "KOREA", "2026-08-28", self.authority,
        )
        self.assertNotEqual(too_early["status"], ci.RESOLVED)
        resolved = ci.resolve_instrument_identity(
            KIS_SOURCE, "071050", "KOREA", "2026-08-29", self.authority,
        )
        self.assertEqual(resolved["status"], ci.RESOLVED, resolved)
        self.assertEqual(resolved["canonical_issuer_id"], "DART:00432102")
        self.assertEqual(resolved["canonical_instrument_id"], "KRX:071050:COMMON")
        self.assertEqual(resolved["listing_id"], "XKRX:071050")
        self.assertTrue(all(value is False for value in resolved["authority"].values()))


if __name__ == "__main__":
    unittest.main()
