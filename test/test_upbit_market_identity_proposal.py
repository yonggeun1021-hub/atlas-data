"""P3-12 Upbit canonical asset <-> market identity proposal regression."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "identity" / "upbit_market_identity_proposal.py"
SPEC = importlib.util.spec_from_file_location("upbit_market_identity_proposal", MODULE_PATH)
IDP = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(IDP)

SHA = "a" * 64
AVAILABLE_AT = "2026-08-28T00:40:00Z"
SOURCE_URL = "https://api.upbit.com/v1/market/all?is_details=true"


def row(market, korean="코인", english="Coin"):
    return {"market": market, "korean_name": korean, "english_name": english}


class UpbitMarketIdentityProposalTests(unittest.TestCase):
    def test_default_rule_is_base_symbol_as_canonical_id(self):
        self.assertEqual(IDP.default_candidate_canonical_asset_id("KRW-BTC"), "BTC")
        with self.assertRaises(IDP.UpbitMarketIdentityProposalError):
            IDP.default_candidate_canonical_asset_id("USDT-BTC")

    def test_proposal_is_unratified_with_authority_all_false(self):
        proposal = IDP.build_proposal(
            row("KRW-BTC"), review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT,
        )
        self.assertEqual(proposal["proposalStatus"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")
        self.assertFalse(proposal["canonicalAuthorityConfigMutated"])
        self.assertTrue(proposal["authority"]["review_only"])
        for key, value in proposal["authority"].items():
            if key != "review_only":
                self.assertFalse(value, key)
        self.assertEqual(proposal["claim"]["candidateCanonicalAssetId"], "BTC")
        self.assertEqual(proposal["proposalSha256"], IDP.payload_sha256(
            {k: v for k, v in proposal.items() if k != "proposalSha256"}
        ))

    def test_proposal_never_claims_ratified_or_broker_verified(self):
        proposal = IDP.build_proposal(
            row("KRW-BTC"), review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT,
        )
        text = IDP.canonical_json(proposal)
        self.assertNotIn("RATIFIED\"", text.replace("UNRATIFIED", ""))
        for forbidden in IDP._FORBIDDEN_STATUS_STRINGS:
            self.assertNotEqual(proposal["proposalStatus"], forbidden)

    def test_evidence_hash_and_available_at_are_validated(self):
        with self.assertRaises(IDP.UpbitMarketIdentityProposalError):
            IDP.build_proposal(
                row("KRW-BTC"), review_as_of="2026-08-28", source_url=SOURCE_URL,
                response_sha256="not-a-hash", available_at=AVAILABLE_AT,
            )
        with self.assertRaises(IDP.UpbitMarketIdentityProposalError):
            IDP.build_proposal(
                row("KRW-BTC"), review_as_of="2026-08-28", source_url=SOURCE_URL,
                response_sha256=SHA, available_at="2026-08-28",
            )

    def test_exception_override_is_proposed_not_auto_applied(self):
        exceptions_doc = {"records": [{"source_asset_id": "BTC", "canonical_asset_id": "XBT_LEGACY"}]}
        proposal = IDP.build_proposal(
            row("KRW-BTC"), review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT, exceptions_doc=exceptions_doc,
        )
        self.assertEqual(proposal["claim"]["candidateCanonicalAssetId"], "XBT_LEGACY")
        self.assertEqual(proposal["claim"]["exceptionStatus"], "PROPOSED_EXCEPTION_APPLIED_UNRATIFIED")
        # Still unratified overall -- an exception record changes the
        # *candidate*, never the proposal's own ratification status.
        self.assertEqual(proposal["proposalStatus"], "PROPOSED_UNRATIFIED_CIO_REVIEW_ONLY")

    def test_duplicate_canonical_target_collision_is_blocked_and_listed(self):
        rows = [row("KRW-BTC"), row("KRW-XBT")]  # contrived: two markets -> same default id impossible
        exceptions_doc = {"records": [{"source_asset_id": "XBT", "canonical_asset_id": "BTC"}]}
        proposals = IDP.build_proposals(
            rows, review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT, exceptions_doc=exceptions_doc,
        )
        findings = IDP.identity_review_findings(proposals)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["finding"], "DUPLICATE_CANONICAL_TARGET")
        self.assertEqual(findings[0]["status"], "BLOCKED")
        self.assertEqual(findings[0]["upbitMarkets"], ["KRW-BTC", "KRW-XBT"])
        blocked = IDP.blocked_markets(findings)
        self.assertEqual(blocked, {"KRW-BTC", "KRW-XBT"})

    def test_no_canonical_cross_reference_gap_is_blocked_and_listed(self):
        proposals = IDP.build_proposals(
            [row("KRW-UNKNOWNTOKEN")], review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT,
        )
        findings = IDP.identity_review_findings(proposals, known_canonical_ids={"BTC", "ETH"})
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["finding"], "NO_CANONICAL_CROSS_REFERENCE")
        self.assertEqual(findings[0]["status"], "BLOCKED")
        self.assertEqual(IDP.blocked_markets(findings), {"KRW-UNKNOWNTOKEN"})

    def test_clean_proposal_set_has_no_findings(self):
        proposals = IDP.build_proposals(
            [row("KRW-BTC"), row("KRW-ETH")], review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT,
        )
        findings = IDP.identity_review_findings(proposals, known_canonical_ids={"BTC", "ETH"})
        self.assertEqual(findings, [])
        self.assertEqual(IDP.blocked_markets(findings), set())

    def test_findings_never_silently_drop_a_market(self):
        rows = [row("KRW-AA"), row("KRW-BB"), row("KRW-CC")]
        exceptions_doc = {"records": [
            {"source_asset_id": "AA", "canonical_asset_id": "SAME"},
            {"source_asset_id": "BB", "canonical_asset_id": "SAME"},
        ]}
        proposals = IDP.build_proposals(
            rows, review_as_of="2026-08-28", source_url=SOURCE_URL,
            response_sha256=SHA, available_at=AVAILABLE_AT, exceptions_doc=exceptions_doc,
        )
        findings = IDP.identity_review_findings(proposals, known_canonical_ids={"SAME", "CC"})
        markets_in_findings = {m for f in findings for m in f["upbitMarkets"]}
        self.assertEqual(markets_in_findings, {"KRW-AA", "KRW-BB"})
        self.assertNotIn("KRW-CC", markets_in_findings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
