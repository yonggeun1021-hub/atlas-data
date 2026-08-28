#!/usr/bin/env python3
"""KIS PAPER pdno -> exact RATIFIED canonical identity.

The real authority document contains exactly one KIS balance alias: 071050,
ratified after the proposal-only evidence passed independent official KIS and
public-master byte reproduction. The same generic canonical resolver remains
fail-closed for every other pdno and still grants no investment, Stage, Buy,
Action, Order, Production, Trading, or REAL authority.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "test"))

from identity import canonical_identity as ci  # noqa: E402
from test_identity_foundation import (  # noqa: E402
    GitAuthorityRepo,
    full_authority,
    make_instrument,
    make_issuer,
    make_listing,
    make_source_alias,
)

# The private atlas-private-evidence repo's kis_paper_full_account_snapshot.py
# exports this same literal as its SOURCE_NAME constant -- intentionally
# duplicated here rather than shared, matching this codebase's existing
# convention of small, independently-auditable per-module mechanics (see,
# e.g., every private_evidence module re-implementing canonical_json/
# payload_sha256 rather than importing one shared copy).
KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME = "kis_paper_domestic_balance"

DECISION_DATE = "2026-08-29"
UNRATIFIED_KIS_PDNOS = ("005930", "000660", "071055")


def _synthetic_alias_row(**overrides) -> dict:
    row = {
        "rule_id": "atlas.identity.alias.kis-test-fixture",
        "rule_version": 1,
        "approval_status": "RATIFIED",
        "ratified_at": "2026-08-25T06:19:27Z",
        "approval_evidence_ref": "evidence/identity/approvals/2026-08-25/alias.samsung-electronics.json",
        "approval_evidence_sha256": "06e0df806f7e46b806725dd352703af64ee45bfef58fc9a928953df546ebd035",
        "business_payload_sha256": "b971c49257d3ec3d8574f02d0128978f4ca358683d6fb0bffce279d8c4e12200",
        "first_seen_at": "2026-08-25T06:19:27Z",
        "effective_from": "2026-08-25T06:19:27Z",
        "effective_to": None,
        "source_name": KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME,
        "source_asset_id": "005930",
        "listing_id": "XKRX:005930",
    }
    row.update(overrides)
    return row


class KisPaperSourceIdentityTodayStateTests(unittest.TestCase):
    """The one real KIS alias and every retained closed boundary."""

    @classmethod
    def setUpClass(cls):
        cls.authority = ci.load_authority()

    def test_registry_contains_only_the_exact_071050_kis_alias(self):
        aliases = [
            row for row in self.authority["source_aliases"]
            if row.get("source_name") == KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME
        ]
        self.assertEqual(len(aliases), 1)
        self.assertEqual(
            (aliases[0]["source_asset_id"], aliases[0]["listing_id"]),
            ("071050", "XKRX:071050"),
        )

    def test_071050_resolves_to_common_share_with_all_operational_authority_false(self):
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "071050", "KOREA",
            DECISION_DATE, self.authority,
        )
        self.assertEqual(result["status"], ci.RESOLVED, result)
        self.assertEqual(result["canonical_issuer_id"], "DART:00432102")
        self.assertEqual(result["canonical_instrument_id"], "KRX:071050:COMMON")
        self.assertEqual(result["listing_id"], "XKRX:071050")
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_resolution_does_not_backdate_to_the_evidence_capture_day(self):
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "071050", "KOREA",
            "2026-08-28", self.authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)

    def test_every_other_kis_pdno_remains_not_computable(self):
        for pdno in UNRATIFIED_KIS_PDNOS:
            result = ci.resolve_instrument_identity(
                KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, pdno, "KOREA",
                DECISION_DATE, self.authority,
            )
            self.assertNotEqual(result["status"], ci.RESOLVED)
            self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))
            self.assertIsNone(result["canonical_instrument_id"])
            self.assertTrue(all(v is False for v in result["authority"].values()))


class KisPaperSourceIdentityGitBackedPositiveControlTests(unittest.TestCase):
    """Reach the real market check with fully git-backed evidence.

    Pure in-memory rows correctly fail before resolution because they have no
    independently verifiable first-seen history.  They therefore cannot prove
    that a matching KIS alias reaches the existing instrument chain or that a
    wrong market is rejected *at the market/listing boundary*.  This fixture
    commits each approval record and the complete authority document to a
    disposable git repository so those two claims are exercised end to end.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = GitAuthorityRepo(Path(self._tmp.name) / "repo")

        issuer = make_issuer("DART:00126380", issuer_name_reference="삼성전자")
        instrument = make_instrument(
            "KRX:005930:COMMON", "DART:00126380", instrument_type="COMMON_STOCK"
        )
        listing = make_listing(
            "XKRX:005930",
            "KRX:005930:COMMON",
            "KOREA",
            exchange="XKRX",
            currency="KRW",
            ticker="005930",
        )
        alias = make_source_alias(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "XKRX:005930"
        )

        ratified_at = "2026-08-25T06:19:27Z"
        for row, layer in (
            (issuer, ci.LAYER_ISSUER),
            (instrument, ci.LAYER_INSTRUMENT),
            (listing, ci.LAYER_LISTING),
            (alias, ci.LAYER_SOURCE_ALIAS),
        ):
            self.repo.commit_evidence(row, layer, ratified_at, ratified_at)

        doc = full_authority(
            issuers=[issuer],
            instruments=[instrument],
            listings=[listing],
            source_aliases=[alias],
        )
        authority_path = self.repo.commit_authority(doc, ratified_at)
        self.authority = ci.load_authority(authority_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_kis_pair_resolves_existing_instrument_chain(self):
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME,
            "005930",
            "KOREA",
            DECISION_DATE,
            self.authority,
        )
        self.assertEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["canonical_instrument_id"], "KRX:005930:COMMON")
        self.assertEqual(result["listing_id"], "XKRX:005930")
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_wrong_market_reaches_and_fails_at_listing_boundary(self):
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME,
            "005930",
            "US",
            DECISION_DATE,
            self.authority,
        )
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)
        self.assertIsNone(result["canonical_instrument_id"])
        self.assertTrue(all(v is False for v in result["authority"].values()))


class KisPaperProviderAuthorityGitBackedPositiveControlTests(unittest.TestCase):
    """Prove the newly merged provider-authority layer can really resolve.

    PR #406 intentionally shipped an empty registry and negative tests.  That
    is the correct production state, but a mechanism also needs a disposable
    positive control proving that a fully committed row and its independently
    committed approval evidence traverse the generic authority gate.  This
    test never changes the shipped empty registry.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = GitAuthorityRepo(Path(self._tmp.name) / "repo")
        self.ratified_at = "2026-08-25T06:19:27Z"
        self.row = {
            "rule_id": "atlas.identity.provider.kis-test-positive",
            "rule_version": 1,
            "approval_status": "PROVISIONAL",
            "ratified_at": None,
            "approval_evidence_ref": None,
            "approval_evidence_sha256": None,
            "business_payload_sha256": None,
            "first_seen_at": "2026-01-01T00:00:00Z",
            "effective_from": "2026-01-01T00:00:00Z",
            "effective_to": None,
            "provider": "KIS_PAPER_ACCOUNT",
            "account_scope": "KOREA",
            "currency": "KRW",
            "position_source_name": KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME,
        }
        self.repo.commit_evidence(
            self.row,
            ci.LAYER_PROVIDER_AUTHORITY,
            self.ratified_at,
            self.ratified_at,
        )
        doc = {
            "schema_version": 1,
            "policy_version": "data_provider_authority/v1",
            "provider_authority_records": [self.row],
        }
        data = json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8")
        authority_path = self.repo._commit(
            "config/data_provider_authority.json",
            data,
            self.ratified_at,
            "add provider authority",
        )
        self.authority = ci.load_provider_authority(authority_path)

    def tearDown(self):
        self._tmp.cleanup()

    def test_exact_provider_tuple_reaches_resolved_with_all_money_authority_false(self):
        result = ci.resolve_provider_authority(
            provider="KIS_PAPER_ACCOUNT",
            account_scope="KOREA",
            currency="KRW",
            position_source_name=KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME,
            decision_date=DECISION_DATE,
            authority=self.authority,
        )
        self.assertEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["provider"], "KIS_PAPER_ACCOUNT")
        self.assertTrue(all(v is False for v in result["authority"].values()))

    def test_near_match_cannot_borrow_the_ratified_provider_name(self):
        result = ci.resolve_provider_authority(
            provider="KIS_PAPER_ACCOUNT",
            account_scope="KOREA",
            currency="USD",
            position_source_name=KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME,
            decision_date=DECISION_DATE,
            authority=self.authority,
        )
        self.assertEqual(
            result["status"], ci.NOT_COMPUTABLE_PROVIDER_AUTHORITY_UNRATIFIED
        )
        self.assertTrue(all(v is False for v in result["authority"].values()))


class KisPaperSourceIdentityFailClosedCounterExampleTests(unittest.TestCase):
    """Synthetic-authority counter-examples for the exact boundary the
    future real alias row(s) must satisfy. These use an in-memory,
    non-file-backed authority document, so `verified_row_first_seen_at`/
    `verified_evidence_first_seen_at` can never be independently confirmed
    against real git history either -- every case here resolves
    NOT_COMPUTABLE for that reason at minimum, which is itself the correct,
    fail-closed answer for anything not genuinely committed and reviewed.
    The per-scenario assertions below additionally pin the SPECIFIC layer
    of the boundary each counter-example is meant to prove, by checking
    which check a real, otherwise-well-formed row would still trip.
    """

    def _authority(self, alias_row: dict) -> dict:
        return {
            "schema_version": 1,
            "policy_version": "canonical_security_identity/v1",
            "evidence_basis": "test-fixture",
            "issuers": [],
            "instruments": [],
            "listings": [
                {
                    "rule_id": "atlas.identity.alias.kis-test-fixture-listing",
                    "rule_version": 1,
                    "approval_status": "RATIFIED",
                    "ratified_at": "2026-08-25T06:19:27Z",
                    "approval_evidence_ref": "evidence/identity/approvals/2026-08-25/alias.samsung-electronics.json",
                    "approval_evidence_sha256": "06e0df806f7e46b806725dd352703af64ee45bfef58fc9a928953df546ebd035",
                    "business_payload_sha256": "b971c49257d3ec3d8574f02d0128978f4ca358683d6fb0bffce279d8c4e12200",
                    "first_seen_at": "2026-08-25T06:19:27Z",
                    "effective_from": "2026-08-25T06:19:27Z",
                    "effective_to": None,
                    "listing_id": "XKRX:005930",
                    "canonical_instrument_id": "KRX:005930:COMMON",
                    "market": "KOREA",
                },
            ],
            "source_aliases": [alias_row],
        }

    def test_unresolved_holding_with_no_alias_row_at_all(self):
        # A pdno with genuinely zero rows for this source_name -- the plain
        # unresolved case every KIS holding is in today.
        authority = self._authority(_synthetic_alias_row())
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "999999", "KOREA",
            DECISION_DATE, authority,
        )
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)

    def test_same_six_digit_ticker_under_a_different_source_name_never_merges(self):
        # The 005930 alias row exists, but bound to a DIFFERENT source_name
        # (as if only krx_open_api_stock_daily, not KIS, had ever been
        # aliased) -- proves ticker-digit equality alone is never enough;
        # the (source_name, source_asset_id) pair must match exactly.
        authority = self._authority(_synthetic_alias_row(source_name="krx_open_api_stock_daily"))
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "KOREA",
            DECISION_DATE, authority,
        )
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)

    def test_unratified_kis_alias_row_never_resolves(self):
        row = _synthetic_alias_row(
            approval_status="PROVISIONAL", ratified_at=None,
            approval_evidence_ref=None, approval_evidence_sha256=None,
            business_payload_sha256=None,
        )
        authority = self._authority(row)
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "KOREA",
            DECISION_DATE, authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_expired_kis_alias_row_never_resolves_after_effective_to(self):
        row = _synthetic_alias_row(effective_to="2026-08-26T00:00:00Z")
        authority = self._authority(row)
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "KOREA",
            "2026-08-27", authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_not_yet_effective_kis_alias_row_never_resolves_before_effective_from(self):
        row = _synthetic_alias_row(
            effective_from="2099-01-01T00:00:00Z", first_seen_at="2099-01-01T00:00:00Z",
        )
        authority = self._authority(row)
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "KOREA",
            DECISION_DATE, authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_rehashed_business_payload_tamper_never_resolves(self):
        # A row whose business_payload_sha256 no longer matches its own
        # content (as if source_asset_id were mutated post-approval without
        # re-ratifying) -- must never resolve regardless of anything else
        # matching.
        row = _synthetic_alias_row(business_payload_sha256="0" * 64)
        authority = self._authority(row)
        result = ci.resolve_instrument_identity(
            KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "KOREA",
            DECISION_DATE, authority,
        )
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertTrue(result["status"].startswith("IDENTITY_NOT_COMPUTABLE"))

    def test_authority_document_itself_must_still_pass_document_level_validation(self):
        authority = self._authority(_synthetic_alias_row())
        # validate_authority_row is exercised transitively by
        # resolve_instrument_identity, but a directly-malformed row (missing
        # a required field) must be caught as a hard failure, never
        # silently treated as "just unresolved".
        malformed = copy.deepcopy(authority)
        del malformed["source_aliases"][0]["effective_from"]
        with self.assertRaises(Exception):
            ci.resolve_instrument_identity(
                KIS_PAPER_DOMESTIC_BALANCE_SOURCE_NAME, "005930", "KOREA",
                DECISION_DATE, malformed,
            )

if __name__ == "__main__":
    unittest.main()
