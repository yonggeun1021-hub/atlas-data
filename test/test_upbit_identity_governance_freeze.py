#!/usr/bin/env python3
"""Deterministic audit tests for the P3 identity/taxonomy authority freeze."""

from __future__ import annotations

import gzip
import hashlib
import json
import unittest
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parents[1]
PACKET_PATH = ROOT / "data/observations/upbit_bounded_identity_registry/2026-08-29/packet.json"
RAW_ROOT = ROOT / "evidence/crypto/upbit/raw/2026-08-29"

# Aggregators, explorers, news/release distributors, source hosts, or exchanges:
# useful corroboration, but not first-party canonical identity authority.
THIRD_PARTY_DOMAINS = frozenset(
    {
        "basescan.org", "bybit.com", "coingecko.com", "coinmarketcap.com",
        "crypto.news", "etherscan.io", "github.com", "globenewswire.com",
        "kraken.com", "medium.com", "solscan.io",
    }
)


def _load(relative_path: str) -> dict:
    with (ROOT / relative_path).open(encoding="utf-8") as handle:
        return json.load(handle)


def _is_third_party(url: str) -> bool:
    hostname = (urlparse(url).hostname or "").lower().removeprefix("www.")
    return any(
        hostname == domain or hostname.endswith(f".{domain}")
        for domain in THIRD_PARTY_DOMAINS
    )


def _source_class(evidence: dict) -> str:
    if evidence["market"] == "KRW-LIT":
        return "content_conflict"
    if evidence["verdict"].startswith("HOLD_"):
        return "ambiguous"
    sources = (evidence.get("independent_evidence") or {}).get(
        "official_project_sources"
    ) or []
    if any(not _is_third_party(url) for url in sources):
        return "first_party_candidate_unbound"
    return "third_party_only"


class UpbitIdentityGovernanceFreezeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.freeze = _load("config/upbit_identity_taxonomy_governance_freeze.json")
        cls.registry = _load("config/upbit_asset_identity_registry.json")
        cls.taxonomy = _load("config/upbit_exclusion_taxonomy.json")
        with PACKET_PATH.open(encoding="utf-8") as handle:
            cls.packet = json.load(handle)
        with (RAW_ROOT / "_manifest.json").open(encoding="utf-8") as handle:
            cls.manifest = json.load(handle)
        with gzip.open(RAW_ROOT / "upbit_market_all.json.gz", "rb") as handle:
            cls.raw_bytes = handle.read()
        cls.raw_rows = json.loads(cls.raw_bytes)

    def test_all_282_markets_have_one_deterministic_authority_class(self) -> None:
        evidence_classes = {
            evidence["market"]: _source_class(evidence)
            for evidence in self.packet["evidence"]
        }
        shadow_markets = {row["market"] for row in self.packet["shadow_funnel_markets"]}
        self.assertEqual(len(shadow_markets), 282)
        self.assertEqual(len(evidence_classes), 81)
        self.assertTrue(set(evidence_classes).issubset(shadow_markets))

        counts = {
            "first_party_candidate_unbound_count": 0,
            "third_party_only_count": 0,
            "ambiguous_count": 0,
            "content_conflict_count": 0,
            "official_listing_only_unresolved_count": len(
                shadow_markets - set(evidence_classes)
            ),
        }
        key_by_class = {
            "first_party_candidate_unbound": "first_party_candidate_unbound_count",
            "third_party_only": "third_party_only_count",
            "ambiguous": "ambiguous_count",
            "content_conflict": "content_conflict_count",
        }
        for source_class in evidence_classes.values():
            counts[key_by_class[source_class]] += 1

        expected = self.freeze["audit_counts"]
        for key, count in counts.items():
            self.assertEqual(count, expected[key], key)
        self.assertEqual(sum(counts.values()), expected["bounded_market_count"])

    def test_official_listing_hash_names_and_times_bind_to_raw_capture(self) -> None:
        raw_by_market = {row["market"]: row for row in self.raw_rows}
        expected_hash = self.manifest["checksums"]["upbit_market_all.json.gz"]
        self.assertEqual(hashlib.sha256(self.raw_bytes).hexdigest(), expected_hash)
        self.assertEqual(self.manifest["downloaded_at_utc"], "2026-08-29T00:52:31Z")
        self.assertEqual(self.manifest["market_count"], 288)

        for market in self.packet["shadow_funnel_markets"]:
            self.assertIn(market["market"], raw_by_market)
        for evidence in self.packet["evidence"]:
            official = evidence["upbit_evidence"]
            raw = raw_by_market[evidence["market"]]
            self.assertEqual(official["response_sha256"], expected_hash)
            self.assertEqual(official["available_at"], self.manifest["downloaded_at_utc"])
            self.assertEqual(official["korean_name"], raw["korean_name"])
            self.assertEqual(official["english_name"], raw["english_name"])

    def test_no_independent_source_has_required_authority_binding(self) -> None:
        required_fields = {
            "source_type", "validated_authority_domain", "content_sha256",
            "observed_at", "available_at",
        }
        authority_valid = 0
        for evidence in self.packet["evidence"]:
            sources = (evidence.get("independent_evidence") or {}).get(
                "official_project_sources"
            ) or []
            self.assertTrue(all(isinstance(source, str) for source in sources))
            if sources and all(
                isinstance(source, dict) and required_fields <= set(source)
                for source in sources
            ):
                authority_valid += 1
        self.assertEqual(authority_valid, 0)
        self.assertEqual(
            authority_valid,
            self.freeze["audit_counts"]["authority_valid_identity_count"],
        )

    def test_historical_candidates_stay_frozen_and_release_is_exact_eight(self) -> None:
        candidate_markets = {
            candidate["market"] for candidate in self.packet["registry_candidates"]
        }
        hold_markets = {hold["market"] for hold in self.packet["hold_list"]}
        mapping_markets = set(self.registry["mappings"])
        mapping_ids = list(self.registry["mappings"].values())

        self.assertEqual(len(candidate_markets), 55)
        self.assertEqual(len(hold_markets), 26)
        self.assertEqual(mapping_markets, set(self.freeze["released_paper_markets"]))
        self.assertEqual(len(mapping_markets), 8)
        self.assertTrue(mapping_markets.isdisjoint(hold_markets))
        self.assertEqual(len(mapping_ids), len(set(mapping_ids)))
        self.assertEqual(
            len(candidate_markets),
            self.freeze["audit_counts"]["authority_invalid_registry_mapping_count"],
        )

    def test_krw_lit_conflict_is_structural_and_never_enters_registry(self) -> None:
        lit_evidence = next(
            row for row in self.packet["evidence"] if row["market"] == "KRW-LIT"
        )
        mapping_markets = set(self.registry["mappings"])

        self.assertEqual(lit_evidence["verdict"], "HOLD_TICKER_COLLISION")
        self.assertEqual(lit_evidence["upbit_evidence"]["english_name"], "Lighter")
        self.assertEqual(self.freeze["blocked_taxonomy"]["content_conflict_markets"], ["KRW-LIT"])
        self.assertNotIn("LIT", {row["canonical_asset_id"] for row in self.taxonomy["records"]})
        self.assertNotIn("KRW-LIT", mapping_markets)

    def test_exact_paper_scope_is_released_while_operational_authority_stays_false(self) -> None:
        self.assertEqual(
            self.freeze["resolution_status"], "RATIFIED_BY_EXPLICIT_CIO_DECISION"
        )
        self.assertEqual(self.registry["approval_status"], "RATIFIED")
        self.assertEqual(self.taxonomy["approval_status"], "RATIFIED")
        self.assertTrue(self.freeze["paper_classification_scope_approved"])
        self.assertEqual(set(self.freeze["released_paper_markets"]), set(self.registry["mappings"]))
        self.assertTrue(all(value is False for value in self.freeze["authority"].values()))
        self.assertEqual(
            self.freeze["blocked_universe_record_payload_sha256s"],
            ["a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12"],
        )


if __name__ == "__main__":
    unittest.main()
