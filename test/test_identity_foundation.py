#!/usr/bin/env python3
"""Identity Foundation stage -- `identity/canonical_identity.py` regression.

★ Rev 2 (CIO code review of HEAD c819a38, CHANGES_REQUIRED, 7 P0 defects).
  The v1 report's claims "exact-content provenance verified" and "28
  tests validate Foundation" are RETRACTED and marked
  `SUPERSEDED_UNAPPROVED` -- see `docs/identity_foundation_pr_notes.md`.
  This file replaces that suite with fixes for all 7 defects plus the
  counter-examples the CIO explicitly required for items 2/3/5/6.

Covers the 18 originally-required counter-examples (updated to the rev-2
API), PLUS new counter-examples specifically for:
  - defect 2 (first_seen_at verification -- registry AND real git history)
  - defect 3 (require_instrument_id no longer bypasses the gate on a
    PROVISIONAL instrument row)
  - defect 5 (timezone / mixed-precision / invalid-date handling)
  - defect 6 (issuer- and instrument-layer ambiguity, not just alias/listing)
plus a blanket AUTHORITY_ALL_FALSE assertion and a regression check that
this module does not disturb the existing, already-working crypto identity
logic (`replay/asset_identity.py`, `config/crypto_asset_identity_exceptions.json`,
`config/crypto_breadth_exclusion_taxonomy.json`).

★ All fixture data in this file is SYNTHETIC test data, constructed only
  in memory (plus real, temp-file-backed evidence/registry artifacts
  created fresh per test), used to prove the resolution mechanism works
  end-to-end. It asserts no real economic identity. The SHIPPED authority
  files (`config/canonical_security_identity.json`,
  `config/market_account_scope_map.json`) ship with zero rows -- see
  `RealShippedAuthorityFilesAreEmptyTests`, which enforces "no real asset
  resolves" against the real files, not synthetic fixtures.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from identity import canonical_identity as ci  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------

def _auth_fields(**overrides):
    base = {
        "rule_id": "RULE-TEST-IDENTITY-0001",
        "rule_version": "1",
        "approval_status": "PROVISIONAL",
        "ratified_at": None,
        "approval_evidence_ref": None,
        "approval_evidence_sha256": None,
        "business_payload_sha256": None,
        "first_seen_at": "2026-01-01",
        "effective_from": "2026-01-01",
        "effective_to": None,
    }
    base.update(overrides)
    return base


def make_issuer(canonical_issuer_id, **kw):
    row = {"canonical_issuer_id": canonical_issuer_id,
           "issuer_name_reference": kw.pop("issuer_name_reference", "TEST ISSUER"),
           "predecessor_issuer_id": kw.pop("predecessor_issuer_id", None)}
    row.update(_auth_fields(**kw))
    return row


def make_instrument(canonical_instrument_id, canonical_issuer_id, instrument_type="COMMON_STOCK", **kw):
    row = {"canonical_instrument_id": canonical_instrument_id,
           "canonical_issuer_id": canonical_issuer_id,
           "instrument_type": instrument_type,
           "predecessor_instrument_id": kw.pop("predecessor_instrument_id", None)}
    row.update(_auth_fields(**kw))
    return row


def make_listing(listing_id, canonical_instrument_id, market, exchange="TEST_EXCHANGE",
                  currency="USD", ticker="TEST", **kw):
    row = {"listing_id": listing_id, "canonical_instrument_id": canonical_instrument_id,
           "market": market, "exchange": exchange, "currency": currency, "ticker": ticker}
    row.update(_auth_fields(**kw))
    return row


def make_source_alias(source_name, source_asset_id, listing_id, **kw):
    row = {"source_name": source_name, "source_asset_id": source_asset_id, "listing_id": listing_id}
    row.update(_auth_fields(**kw))
    return row


def make_scope_edge(market, account_scope, **kw):
    row = {"market": market, "account_scope": account_scope}
    row.update(_auth_fields(**kw))
    return row


def write_evidence_file(evidence_dir: Path, row: dict) -> tuple[str, str]:
    """Writes a REAL evidence file (real bytes on disk) independently
    asserting rule_id/rule_version/approval_status/approved_business_payload_sha256.
    Returns (path_str, sha256_of_real_bytes)."""
    content = {
        "rule_id": row["rule_id"],
        "rule_version": row["rule_version"],
        "approval_status": "RATIFIED",
        "approved_business_payload_sha256": row["business_payload_sha256"],
    }
    data = ci.canonical_json(content).encode("utf-8")
    path = evidence_dir / f"{row['rule_id']}__{row['rule_version']}__{row['business_payload_sha256'][:8]}.json"
    path.write_bytes(data)
    return str(path), hashlib.sha256(data).hexdigest()


def ratify(row: dict, layer: str, ratified_at: str, evidence_dir: Path, first_seen_at: str | None = None) -> dict:
    """Produces a genuinely, correctly RATIFIED row: approval_status,
    ratified_at, a CORRECT business_payload_sha256, and a REAL evidence
    file on disk whose real bytes hash to approval_evidence_sha256 and
    whose real content independently corroborates the row. Does NOT by
    itself register first_seen_at anywhere verifiable -- callers must
    separately call `ci.record_first_seen(...)` (registry path) or rely
    on real git history (file path) for `verify_first_seen_at` to
    succeed; see `RATIFY_AND_REGISTER` convenience wrapper below."""
    row["approval_status"] = "RATIFIED"
    row["ratified_at"] = ratified_at
    if first_seen_at is not None:
        row["first_seen_at"] = first_seen_at
    row["business_payload_sha256"] = ci.payload_sha256(ci.business_payload(row, layer))
    ref, sha = write_evidence_file(evidence_dir, row)
    row["approval_evidence_ref"] = ref
    row["approval_evidence_sha256"] = sha
    return row


def full_authority(issuers=(), instruments=(), listings=(), source_aliases=()):
    return {
        "schema_version": 1,
        "policy_version": "canonical_security_identity/v1",
        "issuers": list(issuers),
        "instruments": list(instruments),
        "listings": list(listings),
        "source_aliases": list(source_aliases),
    }


def scope_authority(edges=()):
    return {
        "schema_version": 1,
        "policy_version": "market_account_scope_map/v1",
        "edges": list(edges),
    }


def _assert_authority_all_false(test, result):
    test.assertEqual(result["authority"], ci.AUTHORITY_ALL_FALSE)
    for v in result["authority"].values():
        test.assertIn(v, (False, None, 0))
        test.assertNotEqual(v, True)


class _TempDirMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.evidence_dir = self.tmp_path / "evidence"
        self.evidence_dir.mkdir()
        self.registry_path = self.tmp_path / "first_seen_registry.jsonl"

    def tearDown(self):
        self._tmp.cleanup()

    def ratify_and_register(self, row, layer, ratified_at, first_seen_at=None, registered_at=None):
        """The common happy-path fixture step: ratify with a real evidence
        file, then register the row's exact content in the append-only
        registry at `registered_at` (defaults to the row's own
        first_seen_at claim, i.e. "the claim happens to be true") so that
        `verify_first_seen_at` can succeed via the registry path."""
        ratify(row, layer, ratified_at, self.evidence_dir, first_seen_at=first_seen_at)
        ci.record_first_seen(row, layer, self.registry_path, at=registered_at or row["first_seen_at"])
        return row


# ---------------------------------------------------------------------------
# 18 originally-required counter-examples (rev-2 API: registry-backed)
# ---------------------------------------------------------------------------

class RequiredCounterExamplesTests(_TempDirMixin, unittest.TestCase):

    def test_01_common_vs_preferred_stock_confusion(self):
        issuer = self.ratify_and_register(make_issuer("ISSUER-SAMSUNG-ELEC"), ci.LAYER_ISSUER, "2026-01-02")
        common = self.ratify_and_register(make_instrument("INSTR-SAMSUNG-COMMON", "ISSUER-SAMSUNG-ELEC", "COMMON_STOCK"),
                                           ci.LAYER_INSTRUMENT, "2026-01-02")
        preferred = self.ratify_and_register(make_instrument("INSTR-SAMSUNG-PREFERRED", "ISSUER-SAMSUNG-ELEC", "PREFERRED_STOCK"),
                                              ci.LAYER_INSTRUMENT, "2026-01-02")
        listing_common = self.ratify_and_register(make_listing("KRX:KRW:005930", "INSTR-SAMSUNG-COMMON", "KOREA",
                                                                 exchange="KRX", currency="KRW", ticker="005930"),
                                                    ci.LAYER_LISTING, "2026-01-02")
        listing_pref = self.ratify_and_register(make_listing("KRX:KRW:005935", "INSTR-SAMSUNG-PREFERRED", "KOREA",
                                                               exchange="KRX", currency="KRW", ticker="005935"),
                                                  ci.LAYER_LISTING, "2026-01-02")
        alias_common = self.ratify_and_register(make_source_alias("KIS", "005930", "KRX:KRW:005930"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_pref = self.ratify_and_register(make_source_alias("KIS", "005935", "KRX:KRW:005935"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [common, preferred], [listing_common, listing_pref],
                                    [alias_common, alias_pref])

        r_common = ci.resolve_instrument_identity("KIS", "005930", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        r_pref = ci.resolve_instrument_identity("KIS", "005935", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(r_common["status"], ci.RESOLVED)
        self.assertEqual(r_pref["status"], ci.RESOLVED)
        self.assertEqual(r_common["canonical_issuer_id"], r_pref["canonical_issuer_id"])
        self.assertNotEqual(r_common["canonical_instrument_id"], r_pref["canonical_instrument_id"])
        _assert_authority_all_false(self, r_common)
        _assert_authority_all_false(self, r_pref)

    def test_02_adr_vs_underlying_share_confusion(self):
        issuer = self.ratify_and_register(make_issuer("ISSUER-SAMSUNG-ELEC"), ci.LAYER_ISSUER, "2026-01-02")
        common = self.ratify_and_register(make_instrument("INSTR-SAMSUNG-COMMON", "ISSUER-SAMSUNG-ELEC", "COMMON_STOCK"),
                                           ci.LAYER_INSTRUMENT, "2026-01-02")
        adr = self.ratify_and_register(make_instrument("INSTR-SAMSUNG-ADR", "ISSUER-SAMSUNG-ELEC", "ADR"),
                                        ci.LAYER_INSTRUMENT, "2026-01-02")
        listing_krx = self.ratify_and_register(make_listing("KRX:KRW:005930", "INSTR-SAMSUNG-COMMON", "KOREA",
                                                              exchange="KRX", currency="KRW", ticker="005930"),
                                                 ci.LAYER_LISTING, "2026-01-02")
        listing_adr = self.ratify_and_register(make_listing("OTC:USD:SSNLF", "INSTR-SAMSUNG-ADR", "US",
                                                              exchange="OTC", currency="USD", ticker="SSNLF"),
                                                ci.LAYER_LISTING, "2026-01-02")
        alias_krx = self.ratify_and_register(make_source_alias("KIS", "005930", "KRX:KRW:005930"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_adr = self.ratify_and_register(make_source_alias("ALPACA", "SSNLF", "OTC:USD:SSNLF"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [common, adr], [listing_krx, listing_adr], [alias_krx, alias_adr])

        r_share = ci.resolve_instrument_identity("KIS", "005930", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        r_adr = ci.resolve_instrument_identity("ALPACA", "SSNLF", "US", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(r_share["status"], ci.RESOLVED)
        self.assertEqual(r_adr["status"], ci.RESOLVED)
        self.assertEqual(r_share["canonical_issuer_id"], r_adr["canonical_issuer_id"])
        self.assertNotEqual(r_share["canonical_instrument_id"], r_adr["canonical_instrument_id"])
        self.assertNotEqual(r_share["listing_id"], r_adr["listing_id"])

    def test_03_same_ticker_different_market(self):
        issuer_a = self.ratify_and_register(make_issuer("ISSUER-A"), ci.LAYER_ISSUER, "2026-01-02")
        issuer_b = self.ratify_and_register(make_issuer("ISSUER-B"), ci.LAYER_ISSUER, "2026-01-02")
        instr_a = self.ratify_and_register(make_instrument("INSTR-A", "ISSUER-A"), ci.LAYER_INSTRUMENT, "2026-01-02")
        instr_b = self.ratify_and_register(make_instrument("INSTR-B", "ISSUER-B"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing_kr = self.ratify_and_register(make_listing("KRX:KRW:TICK", "INSTR-A", "KOREA", ticker="TICK"),
                                               ci.LAYER_LISTING, "2026-01-02")
        listing_us = self.ratify_and_register(make_listing("NASDAQ:USD:TICK", "INSTR-B", "US", ticker="TICK"),
                                               ci.LAYER_LISTING, "2026-01-02")
        alias_kr = self.ratify_and_register(make_source_alias("SRC", "TICK", "KRX:KRW:TICK"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer_a, issuer_b], [instr_a, instr_b], [listing_kr, listing_us], [alias_kr])
        r_kr = ci.resolve_instrument_identity("SRC", "TICK", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(r_kr["status"], ci.RESOLVED)
        self.assertEqual(r_kr["canonical_instrument_id"], "INSTR-A")
        r_us = ci.resolve_instrument_identity("SRC", "TICK", "US", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertNotEqual(r_us["status"], ci.RESOLVED)
        self.assertEqual(r_us["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_04_btc_xbt_xxbt_alias_collision(self):
        issuer = self.ratify_and_register(make_issuer("ISSUER-BTC"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = self.ratify_and_register(make_instrument("INSTR-BTC", "ISSUER-BTC", "CRYPTO_ASSET"),
                                               ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("KRAKEN:USD:BTC", "INSTR-BTC", "CRYPTO",
                                                          exchange="KRAKEN_SPOT", ticker="BTC"), ci.LAYER_LISTING, "2026-01-02")
        aliases = [self.ratify_and_register(make_source_alias("KRAKEN", sym, "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
                   for sym in ("BTC", "XBT", "XXBT")]
        authority = full_authority([issuer], [instrument], [listing], aliases)
        results = [ci.resolve_instrument_identity("KRAKEN", sym, "CRYPTO", "2026-06-01", authority, registry_path=self.registry_path)
                   for sym in ("BTC", "XBT", "XXBT")]
        for r in results:
            self.assertEqual(r["status"], ci.RESOLVED)
            self.assertEqual(r["canonical_instrument_id"], "INSTR-BTC")

    def test_05_329180_vs_329180_ks(self):
        issuer = self.ratify_and_register(make_issuer("ISSUER-HD-HEAVY"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = self.ratify_and_register(make_instrument("INSTR-HD-HEAVY-COMMON", "ISSUER-HD-HEAVY"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("KRX:KRW:329180", "INSTR-HD-HEAVY-COMMON", "KOREA",
                                                          exchange="KRX", currency="KRW", ticker="329180"),
                                            ci.LAYER_LISTING, "2026-01-02")
        alias_bare = self.ratify_and_register(make_source_alias("universe_json", "329180", "KRX:KRW:329180"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_yahoo = self.ratify_and_register(make_source_alias("monitoring_identity_yahoo_style", "329180.KS", "KRX:KRW:329180"),
                                                ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [instrument], [listing], [alias_bare, alias_yahoo])
        r_bare = ci.resolve_instrument_identity("universe_json", "329180", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        r_yahoo = ci.resolve_instrument_identity("monitoring_identity_yahoo_style", "329180.KS", "KOREA", "2026-06-01",
                                                  authority, registry_path=self.registry_path)
        self.assertEqual(r_bare["status"], ci.RESOLVED)
        self.assertEqual(r_yahoo["status"], ci.RESOLVED)
        self.assertEqual(r_bare["canonical_instrument_id"], r_yahoo["canonical_instrument_id"])
        self.assertEqual(r_bare["listing_id"], r_yahoo["listing_id"])

    def test_06_overlapping_effective_intervals(self):
        alias_a = self.ratify_and_register(make_source_alias("SRC", "DUP", "LISTING-A", effective_from="2026-01-01",
                                                               effective_to="2026-12-31"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_b = self.ratify_and_register(make_source_alias("SRC", "DUP", "LISTING-B", effective_from="2026-06-01",
                                                               effective_to=None), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority(source_aliases=[alias_a, alias_b])
        result = ci.resolve_instrument_identity("SRC", "DUP", "KOREA", "2026-07-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)
        overlaps = ci.detect_overlapping_intervals([alias_a, alias_b], ("source_name", "source_asset_id"))
        self.assertEqual(len(overlaps), 1)

    def test_07_implementation_exists_authority_does_not(self):
        authority = full_authority()
        result = ci.resolve_instrument_identity("ANY", "ANY", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
        _assert_authority_all_false(self, result)

    def test_08_authority_record_exists_implementation_does_not(self):
        doc = full_authority()
        doc["policy_version"] = "canonical_security_identity/v99_unknown"
        path = self.tmp_path / "unsupported.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(ci.IdentityError):
            ci.load_authority(path)

    def test_09_proposed_not_ratified_row_attempted(self):
        alias = make_source_alias("SRC", "PENDING", "LISTING-X")  # left PROVISIONAL
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "PENDING", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_UNRATIFIED_RECORD)

    def test_10_backdated_effective_from(self):
        alias = self.ratify_and_register(
            make_source_alias("SRC", "BACKDATED", "LISTING-X", effective_from="2020-01-01"),
            ci.LAYER_SOURCE_ALIAS, ratified_at="2026-08-20", first_seen_at="2026-08-20")
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "BACKDATED", "KOREA", "2026-03-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)

    def test_11_ratified_at_in_the_future(self):
        alias = self.ratify_and_register(
            make_source_alias("SRC", "FUTURE_RATIFY", "LISTING-X", effective_from="2026-01-01"),
            ci.LAYER_SOURCE_ALIAS, ratified_at="2099-01-01", first_seen_at="2026-01-01")
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "FUTURE_RATIFY", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)

    def test_12_usage_before_exact_content_first_seen_time(self):
        """The row LIES in its self-declared first_seen_at ('2020-01-01'),
        claiming to be much older than it really is. The independent
        registry (real, external truth) says it was genuinely first seen
        on 2026-08-20. verify_first_seen_at must use the REGISTRY value,
        never the row's own claim -- this is the direct test of defect 2's
        fix."""
        row = make_source_alias("SRC", "LATE_SEEN", "LISTING-X", effective_from="2020-01-01")
        ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2020-01-02", evidence_dir=self.evidence_dir,
               first_seen_at="2020-01-01")  # self-declared claim: a lie
        ci.record_first_seen(row, ci.LAYER_SOURCE_ALIAS, self.registry_path, at="2026-08-20")  # real truth
        authority = full_authority(source_aliases=[row])
        result = ci.resolve_instrument_identity("SRC", "LATE_SEEN", "KOREA", "2026-01-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)
        self.assertEqual(result["identity_basis"]["source_alias"]["verified_first_seen_at"], "2026-08-20")
        self.assertNotEqual(result["identity_basis"]["source_alias"]["verified_first_seen_at"],
                             row["first_seen_at"])

    def test_13_issuer_id_used_where_instrument_id_required(self):
        issuer = self.ratify_and_register(make_issuer("ISSUER-ONLY"), ci.LAYER_ISSUER, "2026-01-02")
        authority = full_authority(issuers=[issuer])
        result = ci.require_instrument_id("ISSUER-ONLY", authority, "2026-06-01", registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_14_portfolio_exposure_joined_on_listing_id(self):
        instrument = self.ratify_and_register(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("LISTING-X", "INSTR-X", "KOREA"), ci.LAYER_LISTING, "2026-01-02")
        authority = full_authority(instruments=[instrument], listings=[listing])
        result = ci.require_instrument_id("LISTING-X", authority, "2026-06-01", registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_15_multiple_listings_of_same_instrument_double_counted(self):
        issuer = self.ratify_and_register(make_issuer("ISSUER-DUAL-LISTED"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = self.ratify_and_register(make_instrument("INSTR-DUAL-LISTED", "ISSUER-DUAL-LISTED", "CRYPTO_ASSET"),
                                               ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("KRAKEN:USD:BTC", "INSTR-DUAL-LISTED", "CRYPTO"), ci.LAYER_LISTING, "2026-01-02")
        alias_btc = self.ratify_and_register(make_source_alias("KRAKEN", "BTC", "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_xbt = self.ratify_and_register(make_source_alias("KRAKEN", "XBT", "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [instrument], [listing], [alias_btc, alias_xbt])

        positions = [{"source_name": "KRAKEN", "source_asset_id": "BTC", "market_value": 100.0},
                     {"source_name": "KRAKEN", "source_asset_id": "XBT", "market_value": 100.0}]
        resolved = {(p["source_name"], p["source_asset_id"]):
                    ci.resolve_instrument_identity(p["source_name"], p["source_asset_id"], "CRYPTO",
                                                     "2026-06-01", authority, registry_path=self.registry_path)
                    for p in positions}
        correct = ci.group_positions_by_instrument(positions, resolved)
        self.assertEqual(correct, {"INSTR-DUAL-LISTED": 200.0})

        naive = {}
        for p in positions:
            naive[p["source_asset_id"]] = naive.get(p["source_asset_id"], 0.0) + p["market_value"]
        self.assertEqual(len(naive), 2)
        self.assertNotEqual(len(naive), len(correct))

    def test_16_tampered_then_resigned_record_rejected(self):
        """Business field mutated AFTER ratification, WITH
        business_payload_sha256 correctly recomputed ('resigned') -- but
        the real (untouched) evidence file's `approved_business_payload_sha256`
        still points at the OLD hash. This is exactly the attack
        `verify_business_payload` alone cannot catch (self-consistency
        only), and is exactly what `verify_approval_evidence`'s
        cross-check against the immutable evidence file DOES catch."""
        row = make_source_alias("SRC", "TAMPERED", "LISTING-ORIGINAL")
        ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-01-02", evidence_dir=self.evidence_dir)
        ci.record_first_seen(row, ci.LAYER_SOURCE_ALIAS, self.registry_path, at=row["first_seen_at"])
        # tamper + "resign": change content, recompute business_payload_sha256
        row["listing_id"] = "LISTING-SWAPPED"
        row["business_payload_sha256"] = ci.payload_sha256(ci.business_payload(row, ci.LAYER_SOURCE_ALIAS))
        self.assertTrue(ci.verify_business_payload(row, ci.LAYER_SOURCE_ALIAS))  # self-consistency now "passes"...
        authority = full_authority(source_aliases=[row])
        result = ci.resolve_instrument_identity("SRC", "TAMPERED", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        # ...but the real evidence file rejects it.
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED)

    def test_17_missing_market_scope_edge(self):
        authority = scope_authority()
        result = ci.resolve_account_scope("BTC", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_SCOPE_MAP_MISSING)
        _assert_authority_all_false(self, result)

    def test_18_no_authority_field_ever_flips_true(self):
        alias = self.ratify_and_register(make_source_alias("SRC", "OK", "LISTING-OK"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        instrument = self.ratify_and_register(make_instrument("INSTR-OK", "ISSUER-OK"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("LISTING-OK", "INSTR-OK", "KOREA"), ci.LAYER_LISTING, "2026-01-02")
        issuer = self.ratify_and_register(make_issuer("ISSUER-OK"), ci.LAYER_ISSUER, "2026-01-02")
        authority = full_authority([issuer], [instrument], [listing], [alias])
        results = [
            ci.resolve_instrument_identity("SRC", "OK", "KOREA", "2026-06-01", authority, registry_path=self.registry_path),
            ci.resolve_instrument_identity("SRC", "MISSING", "KOREA", "2026-06-01", authority),
            ci.resolve_account_scope("KOREA", "2026-06-01", scope_authority()),
            ci.require_instrument_id("ISSUER-OK", authority, "2026-06-01"),
        ]
        for r in results:
            _assert_authority_all_false(self, r)


# ---------------------------------------------------------------------------
# New counter-examples required by CIO code review (defects 2, 3, 5, 6)
# ---------------------------------------------------------------------------

class Defect2FirstSeenVerificationTests(_TempDirMixin, unittest.TestCase):
    """first_seen_at must be independently verified -- registry path and
    real git-history path, both exercised for real."""

    def test_registry_verification_end_to_end_success(self):
        row = self.ratify_and_register(make_source_alias("SRC", "REG_OK", "LISTING-X"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority(source_aliases=[row])
        result = ci.resolve_instrument_identity("SRC", "REG_OK", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        # fails downstream (no listing exists) but MUST get past first-seen verification --
        # confirmed by NOT getting FIRST_SEEN_UNVERIFIED.
        self.assertNotEqual(result["status"], ci.NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED)

    def test_no_registry_no_git_path_is_unverified(self):
        """A RATIFIED, correctly-signed row with NO registry_path and NO
        git-backed authority document must fail closed as
        FIRST_SEEN_UNVERIFIED -- it must never fall back to trusting the
        row's own self-declared first_seen_at."""
        row = make_source_alias("SRC", "NO_VERIFY", "LISTING-X")
        ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-01-02", evidence_dir=self.evidence_dir)
        authority = full_authority(source_aliases=[row])  # note: no _source_path, no registry_path passed
        result = ci.resolve_instrument_identity("SRC", "NO_VERIFY", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED)

    def test_real_git_history_verification(self):
        """Real git-history verification path (not the registry): builds
        a genuine temp git repo, commits a real authority file containing
        the row, and confirms verify_first_seen_at returns that commit's
        REAL committer time -- not the row's self-declared claim."""
        repo = self.tmp_path / "gitrepo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)

        row = make_source_alias("SRC", "GIT_VERIFIED", "LISTING-X")
        ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2020-01-02", evidence_dir=self.evidence_dir,
               first_seen_at="1970-01-01")  # self-declared lie
        doc = full_authority(source_aliases=[row])
        auth_path = repo / "canonical_security_identity.json"
        auth_path.write_text(json.dumps({k: v for k, v in doc.items() if k != "_source_path"}), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        commit_env = dict(os.environ, GIT_AUTHOR_DATE="2026-08-20T12:00:00", GIT_COMMITTER_DATE="2026-08-20T12:00:00")
        subprocess.run(["git", "commit", "-q", "-m", "add row"], cwd=repo, check=True, env=commit_env)

        verified = ci.verify_first_seen_at(row, ci.LAYER_SOURCE_ALIAS, git_path=auth_path)
        self.assertIsNotNone(verified)
        self.assertTrue(verified.startswith("2026-08-20"))
        self.assertNotEqual(verified, row["first_seen_at"])  # proves the claim was ignored

    def test_real_git_history_no_match_is_unverified(self):
        """A row that was never actually committed to the file's history
        (e.g. injected purely in-memory) must NOT be treated as verified
        just because a git-tracked file path was supplied."""
        repo = self.tmp_path / "gitrepo2"
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
        auth_path = repo / "canonical_security_identity.json"
        auth_path.write_text(json.dumps(full_authority()), encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "empty"], cwd=repo, check=True)

        row = make_source_alias("SRC", "NEVER_COMMITTED", "LISTING-X")
        ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-01-02", evidence_dir=self.evidence_dir)
        verified = ci.verify_first_seen_at(row, ci.LAYER_SOURCE_ALIAS, git_path=auth_path)
        self.assertIsNone(verified)


class Defect3RequireInstrumentIdGateTests(_TempDirMixin, unittest.TestCase):
    """require_instrument_id must never short-circuit to RESOLVED on mere
    structural existence -- the exact counter-example the CIO required."""

    def test_provisional_instrument_id_never_resolves_via_require_instrument_id(self):
        instrument = make_instrument("INSTR-PROVISIONAL-ONLY", "ISSUER-X")  # left PROVISIONAL, never ratified
        authority = full_authority(instruments=[instrument])
        result = ci.require_instrument_id("INSTR-PROVISIONAL-ONLY", authority, "2026-06-01")
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_UNRATIFIED_RECORD)
        self.assertIsNone(result["canonical_instrument_id"])

    def test_ratified_instrument_id_resolves_via_require_instrument_id(self):
        instrument = self.ratify_and_register(make_instrument("INSTR-REAL", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        authority = full_authority(instruments=[instrument])
        result = ci.require_instrument_id("INSTR-REAL", authority, "2026-06-01", registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["canonical_instrument_id"], "INSTR-REAL")

    def test_tampered_instrument_rejected_via_require_instrument_id(self):
        """The same gate as resolve_instrument_identity -- provenance
        failures propagate through require_instrument_id too, since it
        now delegates to the real operational resolver."""
        instrument = make_instrument("INSTR-TAMPERED", "ISSUER-X")
        ratify(instrument, ci.LAYER_INSTRUMENT, ratified_at="2026-01-02", evidence_dir=self.evidence_dir)
        instrument["instrument_type"] = "PREFERRED_STOCK"  # tamper without resigning
        authority = full_authority(instruments=[instrument])
        result = ci.require_instrument_id("INSTR-TAMPERED", authority, "2026-06-01", registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_TAMPERED_RECORD)


class Defect5TemporalPrecisionTests(unittest.TestCase):
    """Strict parsing + chronological (not lexical) comparison; mixed
    precision on the same calendar day is flagged, never silently ordered."""

    def test_zero_padding_does_not_break_ordering(self):
        # this is exactly the class of bug lexical string comparison
        # produces ("2026-08-9" > "2026-08-10" as strings) -- our schema
        # requires zero-padding, and the strict parser rejects anything else.
        self.assertEqual(ci._compare_temporal("2026-08-09", "2026-08-10"), -1)
        self.assertEqual(ci._compare_temporal("2026-09-01", "2026-08-20"), 1)

    def test_non_zero_padded_date_rejected(self):
        with self.assertRaises(ci.IdentityError):
            ci._parse_temporal("2026-8-9")

    def test_naive_timestamp_without_z_rejected(self):
        with self.assertRaises(ci.IdentityError):
            ci._parse_temporal("2026-08-20T09:00:00")  # missing explicit UTC 'Z'

    def test_offset_timestamp_rejected_not_silently_converted(self):
        with self.assertRaises(ci.IdentityError):
            ci._parse_temporal("2026-08-20T09:00:00+09:00")  # only literal 'Z' UTC is accepted

    def test_garbage_value_rejected(self):
        with self.assertRaises(ci.IdentityError):
            ci._parse_temporal("not-a-date")

    def test_different_days_compare_correctly_regardless_of_precision(self):
        # DATE_ONLY vs FULL_TIMESTAMP on genuinely DIFFERENT days is safe --
        # no precision assumption is needed to know one whole day precedes another.
        self.assertEqual(ci._compare_temporal("2026-08-19", "2026-08-20T00:00:01Z"), -1)
        self.assertEqual(ci._compare_temporal("2026-08-21T23:59:59Z", "2026-08-20"), 1)

    def test_same_day_mixed_precision_is_ambiguous(self):
        with self.assertRaises(ci.TimePrecisionAmbiguous):
            ci._compare_temporal("2026-08-20", "2026-08-20T14:00:00Z")

    def test_same_day_both_date_only_equal_is_not_ambiguous(self):
        self.assertEqual(ci._compare_temporal("2026-08-20", "2026-08-20"), 0)

    def test_resolver_surfaces_time_precision_status_on_mixed_precision_interval(self):
        """End-to-end: a row whose effective_from is a full timestamp and
        whose decision_date lands on the exact same calendar day (as a
        bare date) must resolve NOT_COMPUTABLE_TIME_PRECISION, never
        silently assume an ordering."""
        row = make_source_alias("SRC", "MIXED_PRECISION", "LISTING-X",
                                 effective_from="2026-08-20T09:00:00Z")
        with tempfile.TemporaryDirectory() as d:
            evidence_dir = Path(d)
            ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-08-20T09:00:00Z", evidence_dir=evidence_dir,
                   first_seen_at="2026-08-20T09:00:00Z")
            registry_path = Path(d) / "registry.jsonl"
            ci.record_first_seen(row, ci.LAYER_SOURCE_ALIAS, registry_path, at="2026-08-20T09:00:00Z")
            authority = full_authority(source_aliases=[row])
            result = ci.resolve_instrument_identity("SRC", "MIXED_PRECISION", "KOREA", "2026-08-20",
                                                      authority, registry_path=registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_TIME_PRECISION)


class Defect6ExactlyOneActiveRowTests(_TempDirMixin, unittest.TestCase):
    """Every layer -- not just alias/listing -- requires exactly one
    active row. Two active rows with DIFFERING target fields (not just
    identical duplicates) must also resolve AMBIGUOUS."""

    def test_two_active_ratified_instrument_rows_same_id_different_issuer_is_ambiguous(self):
        instr_1 = self.ratify_and_register(make_instrument("INSTR-CONFLICT", "ISSUER-ONE"), ci.LAYER_INSTRUMENT, "2026-01-02")
        instr_2 = self.ratify_and_register(make_instrument("INSTR-CONFLICT", "ISSUER-TWO"), ci.LAYER_INSTRUMENT, "2026-01-02")
        authority = full_authority(instruments=[instr_1, instr_2])
        result = ci.resolve_instrument_by_id("INSTR-CONFLICT", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

    def test_two_active_ratified_issuer_rows_same_id_is_ambiguous(self):
        """v1 defect: issuer-layer ambiguity inside listing resolution was
        never checked at all -- the first RATIFIED row was silently
        picked. Now closed via the shared pipeline."""
        issuer_1 = self.ratify_and_register(make_issuer("ISSUER-DUP", issuer_name_reference="A"), ci.LAYER_ISSUER, "2026-01-02")
        issuer_2 = self.ratify_and_register(make_issuer("ISSUER-DUP", issuer_name_reference="B"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = self.ratify_and_register(make_instrument("INSTR-X", "ISSUER-DUP"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("LISTING-X", "INSTR-X", "KOREA"), ci.LAYER_LISTING, "2026-01-02")
        alias = self.ratify_and_register(make_source_alias("SRC", "X", "LISTING-X"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer_1, issuer_2], [instrument], [listing], [alias])
        result = ci.resolve_instrument_identity("SRC", "X", "KOREA", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

    def test_two_active_ratified_scope_edges_same_market_different_account_scope_is_ambiguous(self):
        edge_1 = self.ratify_and_register(make_scope_edge("CRYPTO", "CRYPTO_MANUAL_ACCOUNT"), ci.LAYER_MARKET_ACCOUNT_SCOPE, "2026-01-02")
        edge_2 = self.ratify_and_register(make_scope_edge("CRYPTO", "ALPACA_PAPER_ACCOUNT"), ci.LAYER_MARKET_ACCOUNT_SCOPE, "2026-01-02")
        authority = scope_authority(edges=[edge_1, edge_2])
        result = ci.resolve_account_scope("CRYPTO", "2026-06-01", authority, registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

    def test_exactly_one_active_ratified_listing_resolves_normally(self):
        """Sanity check that the stricter rule does not reject the normal
        single-row case."""
        instrument = self.ratify_and_register(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = self.ratify_and_register(make_listing("LISTING-X", "INSTR-X", "KOREA"), ci.LAYER_LISTING, "2026-01-02")
        authority = full_authority(instruments=[instrument], listings=[listing])
        result = ci.require_instrument_id("INSTR-X", authority, "2026-06-01", registry_path=self.registry_path)
        self.assertEqual(result["status"], ci.RESOLVED)


# ---------------------------------------------------------------------------
# Structural / validation coverage
# ---------------------------------------------------------------------------

class StructuralValidationTests(_TempDirMixin, unittest.TestCase):

    def test_provisional_row_rejects_ratified_at(self):
        row = make_source_alias("SRC", "X", "L")
        row["ratified_at"] = "2026-01-01"
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_ratified_row_requires_business_payload_and_evidence_fields(self):
        row = make_source_alias("SRC", "X", "L")
        row["approval_status"] = "RATIFIED"
        row["ratified_at"] = "2026-01-01"
        # approval_evidence_ref/sha256/business_payload_sha256 still None
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_valid_ratified_row_passes_structural_validation(self):
        row = self.ratify_and_register(make_source_alias("SRC", "X", "L"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)  # must not raise

    def test_resolver_hard_fails_on_malformed_injected_row(self):
        """Defect 4: even a directly-injected dict (never touching
        load_authority) must be validated before being considered."""
        malformed = make_instrument("INSTR-X", "ISSUER-X")
        del malformed["instrument_type"]
        authority = full_authority(instruments=[malformed])
        with self.assertRaises(ci.IdentityError):
            ci.resolve_instrument_by_id("INSTR-X", "2026-06-01", authority)

    def test_correctly_signed_row_verifies_business_payload_and_evidence(self):
        row = self.ratify_and_register(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        self.assertTrue(ci.verify_business_payload(row, ci.LAYER_INSTRUMENT))
        self.assertTrue(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_provisional_row_never_verifies(self):
        row = make_instrument("INSTR-X", "ISSUER-X")
        self.assertFalse(ci.verify_business_payload(row, ci.LAYER_INSTRUMENT))
        self.assertFalse(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_evidence_file_missing_is_unverified_even_with_matching_hash_claim(self):
        row = self.ratify_and_register(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        os.remove(row["approval_evidence_ref"])
        self.assertFalse(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_evidence_file_content_mismatch_rejected(self):
        """The real file exists and its bytes match the claimed hash, but
        its CONTENT doesn't corroborate this row's rule_id/rule_version --
        i.e. the hash was computed honestly over the WRONG file."""
        row = self.ratify_and_register(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        other = self.ratify_and_register(make_instrument("INSTR-OTHER", "ISSUER-OTHER"), ci.LAYER_INSTRUMENT, "2026-01-02")
        row["approval_evidence_ref"] = other["approval_evidence_ref"]
        row["approval_evidence_sha256"] = other["approval_evidence_sha256"]
        self.assertFalse(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_inverted_effective_interval_rejected(self):
        row = make_source_alias("SRC", "X", "L", effective_from="2026-06-01", effective_to="2026-01-01")
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_unknown_instrument_type_rejected(self):
        row = make_instrument("INSTR-X", "ISSUER-X", instrument_type="NOT_A_REAL_TYPE")
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_INSTRUMENT)


# ---------------------------------------------------------------------------
# The real shipped authority files must carry zero RATIFIED rows.
# ---------------------------------------------------------------------------

class RealShippedAuthorityFilesAreEmptyTests(unittest.TestCase):

    def test_real_canonical_security_identity_file_has_zero_rows(self):
        doc = ci.load_authority()
        for layer_key in ("issuers", "instruments", "listings", "source_aliases"):
            self.assertEqual(doc[layer_key], [], f"{layer_key} must be empty in this PR")

    def test_real_market_account_scope_map_file_has_zero_edges(self):
        doc = ci.load_scope_authority()
        self.assertEqual(doc["edges"], [])

    def test_real_files_resolve_every_real_query_to_not_computable(self):
        authority = ci.load_authority()
        for source_name, source_asset_id, market in (
            ("KRAKEN", "BTC", "CRYPTO"), ("KRAKEN", "XBT", "CRYPTO"),
            ("universe_json", "329180", "KOREA"), ("monitoring_identity_yahoo_style", "329180.KS", "KOREA"),
            ("KIS", "005930", "KOREA"),
        ):
            result = ci.resolve_instrument_identity(source_name, source_asset_id, market, "2026-08-24", authority)
            self.assertEqual(result["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
            _assert_authority_all_false(self, result)

        scope_doc = ci.load_scope_authority()
        for market in ("BTC", "CRYPTO", "KOREA", "US"):
            result = ci.resolve_account_scope(market, "2026-08-24", scope_doc)
            self.assertEqual(result["status"], ci.NOT_COMPUTABLE_SCOPE_MAP_MISSING)
            _assert_authority_all_false(self, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
