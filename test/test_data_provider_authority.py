#!/usr/bin/env python3
"""P0-2C-1 prep: provider authority for KIS_PAPER_ACCOUNT (and any future
data provider) as a layer independent from, and strictly prior to,
instrument-identity resolution (P0-2C-2, test_kis_paper_source_identity_resolution.py).

"Can Atlas technically read this provider's API" and "is this provider a
RATIFIED portfolio-fact source" are two separate facts. This file proves
the resolution boundary only -- it does not add, propose, or ratify any
real provider_authority_records row (config/data_provider_authority.json
stays an empty mechanism today; see that file's own evidence_basis).
"""
from __future__ import annotations

import copy
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


class DataProviderAuthorityTodayStateTests(unittest.TestCase):
    """Honest current (pre-P0-2C-1-authority-PR) state of the world."""

    @classmethod
    def setUpClass(cls):
        cls.authority = ci.load_provider_authority()

    def test_no_provider_authority_records_exist_yet(self):
        self.assertEqual(self.authority["provider_authority_records"], [])

    def test_kis_paper_account_tuple_currently_resolves_not_computable(self):
        result = ci.resolve_provider_authority(
            **KIS_PROVIDER_TUPLE, decision_date=DECISION_DATE, authority=self.authority,
        )
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED)
        self.assertIsNone(result.get("provider"))
        self.assertTrue(all(v is False for v in result["authority"].values()))


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
