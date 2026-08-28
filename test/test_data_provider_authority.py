#!/usr/bin/env python3
"""Provider authority for KIS_PAPER_ACCOUNT as a layer independent from,
and strictly prior to, instrument-identity resolution.

"Can Atlas technically read this provider's API" and "is this provider a
RATIFIED portfolio-fact source" remain separate facts. The one shipped KIS
PAPER balance tuple is backed by a standalone approval packet and independently
reproduced official git bytes; every other provider tuple still fails closed.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from identity import canonical_identity as ci  # noqa: E402

DECISION_DATE = "2026-08-28"

KIS_PROVIDER_TUPLE = dict(
    provider="KIS_PAPER_ACCOUNT", account_scope="KOREA", currency="KRW",
    position_source_name="kis_paper_domestic_balance",
)


def _synthetic_provider_row(**overrides) -> dict:
    row = {
        "rule_id": "atlas.identity.provider.kis-test-fixture",
        "rule_version": 1,
        "approval_status": "RATIFIED",
        "ratified_at": "2026-08-25T06:19:27Z",
        "approval_evidence_ref": "evidence/identity/approvals/2026-08-25/alias.samsung-electronics.json",
        "approval_evidence_sha256": "06e0df806f7e46b806725dd352703af64ee45bfef58fc9a928953df546ebd035",
        "business_payload_sha256": "b971c49257d3ec3d8574f02d0128978f4ca358683d6fb0bffce279d8c4e12200",
        "first_seen_at": "2026-08-25T06:19:27Z",
        "effective_from": "2026-08-25T06:19:27Z",
        "effective_to": None,
        **KIS_PROVIDER_TUPLE,
    }
    row.update(overrides)
    return row


def _authority(*rows: dict) -> dict:
    return {
        "schema_version": 1,
        "policy_version": "data_provider_authority/v1",
        "evidence_basis": "test-fixture",
        "provider_authority_records": list(rows),
    }


class DataProviderAuthorityRatifiedStateTests(unittest.TestCase):
    """The one real provider tuple ratified after independent byte review."""

    @classmethod
    def setUpClass(cls):
        cls.authority = ci.load_provider_authority()

    def test_registry_contains_only_the_kis_paper_balance_tuple(self):
        rows = self.authority["provider_authority_records"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["approval_status"], "RATIFIED")
        self.assertEqual(
            {key: row[key] for key in KIS_PROVIDER_TUPLE},
            KIS_PROVIDER_TUPLE,
        )

    def test_kis_paper_account_tuple_resolves_only_after_verified_first_seen(self):
        too_early = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE,
            decision_date="2026-08-28",
            authority=self.authority,
        )
        self.assertNotEqual(too_early["status"], ci.RESOLVED)
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE,
            decision_date="2026-08-29",
            authority=self.authority,
        )
        self.assertEqual(result["status"], ci.RESOLVED, result)
        self.assertEqual(result["provider"], "KIS_PAPER_ACCOUNT")
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_real_approval_packet_and_every_retained_source_hash_match(self):
        row = self.authority["provider_authority_records"][0]
        approval_path = ROOT / row["approval_evidence_ref"]
        self.assertTrue(approval_path.is_file())
        self.assertEqual(
            hashlib.sha256(approval_path.read_bytes()).hexdigest(),
            row["approval_evidence_sha256"],
        )
        approval = json.loads(approval_path.read_text())
        self.assertEqual(approval["layer"], ci.LAYER_PROVIDER_AUTHORITY)
        self.assertEqual(
            approval["assertion"]["independent_resolution_sha256"],
            "a90c3a7c76bb9b468a4337ed11b24a475071f4d3c7aeaf384994ce50e3c8fdef",
        )
        self.assertEqual(approval["assertion"]["review_status"], "REVIEW_READY_FOR_CIO")
        for source in approval["source_evidence"]:
            source_path = ROOT / source["path"]
            self.assertTrue(source_path.is_file(), source)
            self.assertEqual(
                hashlib.sha256(source_path.read_bytes()).hexdigest(),
                source["sha256"],
            )

    def test_provider_ratification_grants_no_investment_or_order_authority(self):
        approval = json.loads(
            (ROOT / self.authority["provider_authority_records"][0]["approval_evidence_ref"]).read_text()
        )
        self.assertEqual(
            approval["boundary"],
            "MECHANICAL_IDENTITY_OR_SCOPE_ONLY_NO_INVESTMENT_OR_TRADING_AUTHORITY",
        )
        self.assertNotIn("user explicitly directed", self.authority["evidence_basis"].lower())
        self.assertNotIn("user instruction", approval["assertion"]["approval_basis"].lower())
        self.assertIn("does not approve", approval["assertion"]["approval_basis"].lower())
        for forbidden in ("instrument alias", "valuation semantics", "order", "trading", "real"):
            self.assertIn(forbidden, approval["assertion"]["approval_basis"].lower())


class DataProviderAuthorityFailClosedCounterExampleTests(unittest.TestCase):
    """Synthetic-authority counter-examples proving the boundary the
    future real KIS provider authority row must satisfy. As in
    test_kis_paper_source_identity_resolution.py, an in-memory,
    non-file-backed document can never pass first-seen git verification,
    so every case here resolves NOT_COMPUTABLE at minimum for that
    reason -- the assertions pin which specific boundary layer each
    scenario is meant to prove.
    """

    def test_no_record_at_all_for_the_tuple(self):
        authority = _authority()
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED)

    def test_partial_tuple_match_never_resolves(self):
        # Same provider name, but a different account_scope/currency/
        # position_source_name -- "KIS_PAPER_ACCOUNT" as a bare string
        # must never itself authorize a different tuple. This is the
        # provider-authority-layer analogue of P0-2C-2's "same 6-digit
        # ticker under a different source_name never merges".
        for field, wrong_value in (
            ("account_scope", "CRYPTO"),
            ("currency", "USD"),
            ("position_source_name", "some_other_kis_endpoint"),
        ):
            with self.subTest(field=field):
                mismatched_tuple = {**KIS_PROVIDER_TUPLE, field: wrong_value}
                authority = _authority(_synthetic_provider_row())
                result = ci.resolve_provider_authority(
                    **mismatched_tuple, decision_date=DECISION_DATE, authority=authority,
                )
                self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED)

    def test_proposed_unratified_row_never_resolves(self):
        row = _synthetic_provider_row(
            approval_status="PROVISIONAL", ratified_at=None,
            approval_evidence_ref=None, approval_evidence_sha256=None,
            business_payload_sha256=None,
        )
        authority = _authority(row)
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED)

    def test_expired_provider_authority_row_never_resolves(self):
        row = _synthetic_provider_row(effective_to="2026-08-26T00:00:00Z")
        authority = _authority(row)
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date="2026-08-27", authority=authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_not_yet_effective_provider_authority_row_never_resolves(self):
        row = _synthetic_provider_row(
            effective_from="2099-01-01T00:00:00Z", first_seen_at="2099-01-01T00:00:00Z",
        )
        authority = _authority(row)
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_ambiguous_overlapping_rows_never_resolve(self):
        row_a = _synthetic_provider_row(rule_id="atlas.identity.provider.kis-a")
        row_b = _synthetic_provider_row(rule_id="atlas.identity.provider.kis-b")
        authority = _authority(row_a, row_b)
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_rehashed_business_payload_tamper_never_resolves(self):
        row = _synthetic_provider_row(business_payload_sha256="0" * 64)
        authority = _authority(row)
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_resolved_status_never_appears_without_real_git_backed_evidence(self):
        # Even a structurally-perfect, fully RATIFIED synthetic row can
        # never reach RESOLVED without real, independently-verifiable git
        # history -- proving there is no in-memory shortcut to granting
        # this authority.
        authority = _authority(_synthetic_provider_row())
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)

    def test_resolve_provider_authority_never_grants_investment_authority(self):
        authority = _authority(_synthetic_provider_row())
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=authority,
        )
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_malformed_document_is_a_hard_failure_not_a_silent_unresolved(self):
        authority = _authority(_synthetic_provider_row())
        malformed = copy.deepcopy(authority)
        del malformed["provider_authority_records"][0]["effective_from"]
        with self.assertRaises(Exception):
            ci.resolve_provider_authority(
                **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=malformed,
            )


if __name__ == "__main__":
    unittest.main()
