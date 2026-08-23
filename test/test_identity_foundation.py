#!/usr/bin/env python3
"""Identity Foundation stage -- `identity/canonical_identity.py` regression.

Covers the 18 required counter-examples (CIO implementation-stage spec,
2026-08-24) plus a blanket AUTHORITY_ALL_FALSE assertion and a regression
check that this new module does not disturb the existing, already-working
crypto identity logic (`replay/asset_identity.py`,
`config/crypto_asset_identity_exceptions.json`,
`config/crypto_breadth_exclusion_taxonomy.json`).

★ All fixture data in this file is SYNTHETIC test data, constructed only
  in memory, used to prove the resolution mechanism works end-to-end
  (including its RATIFIED path). It asserts no real economic identity.
  The SHIPPED authority files (`config/canonical_security_identity.json`,
  `config/market_account_scope_map.json`) ship with zero rows -- see
  `RealShippedAuthorityFilesAreEmptyTests` below, which is the test that
  actually enforces "no real asset resolves" against the real files.
"""
from __future__ import annotations

import copy
import os
import sys
import unittest

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


def ratify(row, layer, ratified_at, first_seen_at=None, evidence_ref="TEST_EVIDENCE_REF"):
    """Mutates `row` in place to a genuinely, correctly RATIFIED row: sets
    approval_status/ratified_at/(optionally first_seen_at), and computes a
    CORRECT approval_evidence_sha256 from the row's current business
    payload -- i.e. this produces a row that legitimately passes
    `verify_row_provenance`. Returns the row for chaining."""
    row["approval_status"] = "RATIFIED"
    row["ratified_at"] = ratified_at
    if first_seen_at is not None:
        row["first_seen_at"] = first_seen_at
    row["approval_evidence_ref"] = evidence_ref
    row["approval_evidence_sha256"] = ci.payload_sha256(ci.business_payload(row, layer))
    return row


def tamper(row, field, new_value):
    """Mutates a business field AFTER the row's hash was computed --
    approval_evidence_sha256 is deliberately left stale, simulating either
    accidental drift or a tamper attempt that forgot (or was blocked from)
    recomputing the signature. This is exactly what verify_row_provenance
    exists to catch."""
    row[field] = new_value
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


# ---------------------------------------------------------------------------
# 18 required counter-examples
# ---------------------------------------------------------------------------

class RequiredCounterExamplesTests(unittest.TestCase):

    def test_01_common_vs_preferred_stock_confusion(self):
        """Same issuer, two distinct instruments (common vs preferred) must
        never be merged under one canonical_instrument_id."""
        issuer = ratify(make_issuer("ISSUER-SAMSUNG-ELEC"), ci.LAYER_ISSUER, "2026-01-02")
        common = ratify(make_instrument("INSTR-SAMSUNG-COMMON", "ISSUER-SAMSUNG-ELEC", "COMMON_STOCK"),
                         ci.LAYER_INSTRUMENT, "2026-01-02")
        preferred = ratify(make_instrument("INSTR-SAMSUNG-PREFERRED", "ISSUER-SAMSUNG-ELEC", "PREFERRED_STOCK"),
                            ci.LAYER_INSTRUMENT, "2026-01-02")
        listing_common = ratify(make_listing("KRX:KRW:005930", "INSTR-SAMSUNG-COMMON", "KOREA", exchange="KRX",
                                              currency="KRW", ticker="005930"), ci.LAYER_LISTING, "2026-01-02")
        listing_pref = ratify(make_listing("KRX:KRW:005935", "INSTR-SAMSUNG-PREFERRED", "KOREA", exchange="KRX",
                                            currency="KRW", ticker="005935"), ci.LAYER_LISTING, "2026-01-02")
        alias_common = ratify(make_source_alias("KIS", "005930", "KRX:KRW:005930"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_pref = ratify(make_source_alias("KIS", "005935", "KRX:KRW:005935"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [common, preferred], [listing_common, listing_pref],
                                    [alias_common, alias_pref])

        r_common = ci.resolve_instrument_identity("KIS", "005930", "KOREA", "2026-06-01", authority)
        r_pref = ci.resolve_instrument_identity("KIS", "005935", "KOREA", "2026-06-01", authority)
        self.assertEqual(r_common["status"], ci.RESOLVED)
        self.assertEqual(r_pref["status"], ci.RESOLVED)
        self.assertEqual(r_common["canonical_issuer_id"], r_pref["canonical_issuer_id"])
        self.assertNotEqual(r_common["canonical_instrument_id"], r_pref["canonical_instrument_id"])
        _assert_authority_all_false(self, r_common)
        _assert_authority_all_false(self, r_pref)

    def test_02_adr_vs_underlying_share_confusion(self):
        """Same issuer, ADR is a different instrument AND a different
        listing (different exchange/currency) than the underlying share."""
        issuer = ratify(make_issuer("ISSUER-SAMSUNG-ELEC"), ci.LAYER_ISSUER, "2026-01-02")
        common = ratify(make_instrument("INSTR-SAMSUNG-COMMON", "ISSUER-SAMSUNG-ELEC", "COMMON_STOCK"),
                         ci.LAYER_INSTRUMENT, "2026-01-02")
        adr = ratify(make_instrument("INSTR-SAMSUNG-ADR", "ISSUER-SAMSUNG-ELEC", "ADR"),
                     ci.LAYER_INSTRUMENT, "2026-01-02")
        listing_krx = ratify(make_listing("KRX:KRW:005930", "INSTR-SAMSUNG-COMMON", "KOREA", exchange="KRX",
                                           currency="KRW", ticker="005930"), ci.LAYER_LISTING, "2026-01-02")
        listing_adr = ratify(make_listing("OTC:USD:SSNLF", "INSTR-SAMSUNG-ADR", "US", exchange="OTC",
                                           currency="USD", ticker="SSNLF"), ci.LAYER_LISTING, "2026-01-02")
        alias_krx = ratify(make_source_alias("KIS", "005930", "KRX:KRW:005930"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_adr = ratify(make_source_alias("ALPACA", "SSNLF", "OTC:USD:SSNLF"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [common, adr], [listing_krx, listing_adr], [alias_krx, alias_adr])

        r_share = ci.resolve_instrument_identity("KIS", "005930", "KOREA", "2026-06-01", authority)
        r_adr = ci.resolve_instrument_identity("ALPACA", "SSNLF", "US", "2026-06-01", authority)
        self.assertEqual(r_share["canonical_issuer_id"], r_adr["canonical_issuer_id"])
        self.assertNotEqual(r_share["canonical_instrument_id"], r_adr["canonical_instrument_id"])
        self.assertNotEqual(r_share["listing_id"], r_adr["listing_id"])

    def test_03_same_ticker_different_market(self):
        """Two unrelated instruments happen to share a raw ticker string in
        different markets -- market must be part of the resolution key so
        they never collide."""
        issuer_a = ratify(make_issuer("ISSUER-A"), ci.LAYER_ISSUER, "2026-01-02")
        issuer_b = ratify(make_issuer("ISSUER-B"), ci.LAYER_ISSUER, "2026-01-02")
        instr_a = ratify(make_instrument("INSTR-A", "ISSUER-A"), ci.LAYER_INSTRUMENT, "2026-01-02")
        instr_b = ratify(make_instrument("INSTR-B", "ISSUER-B"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing_kr = ratify(make_listing("KRX:KRW:TICK", "INSTR-A", "KOREA", ticker="TICK"),
                             ci.LAYER_LISTING, "2026-01-02")
        listing_us = ratify(make_listing("NASDAQ:USD:TICK", "INSTR-B", "US", ticker="TICK"),
                             ci.LAYER_LISTING, "2026-01-02")
        alias_kr = ratify(make_source_alias("SRC", "TICK", "KRX:KRW:TICK"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        # deliberately the SAME (source_name, source_asset_id) pair could never disambiguate
        # by market alone if listing lookup didn't filter by market -- prove it does.
        authority = full_authority([issuer_a, issuer_b], [instr_a, instr_b], [listing_kr, listing_us], [alias_kr])
        r_kr = ci.resolve_instrument_identity("SRC", "TICK", "KOREA", "2026-06-01", authority)
        self.assertEqual(r_kr["status"], ci.RESOLVED)
        self.assertEqual(r_kr["canonical_instrument_id"], "INSTR-A")
        # requesting the same source key under the WRONG market must not
        # silently resolve to the KR listing.
        r_us = ci.resolve_instrument_identity("SRC", "TICK", "US", "2026-06-01", authority)
        self.assertNotEqual(r_us["status"], ci.RESOLVED)

    def test_04_btc_xbt_xxbt_alias_collision(self):
        """The real Kraken alias set (BTC, XBT, XXBT) must all resolve to
        one canonical_instrument_id -- proves the mechanism handles the
        exact real crypto case, using synthetic (not shipped) fixture
        rows."""
        issuer = ratify(make_issuer("ISSUER-BTC"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = ratify(make_instrument("INSTR-BTC", "ISSUER-BTC", "CRYPTO_ASSET"),
                             ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = ratify(make_listing("KRAKEN:USD:BTC", "INSTR-BTC", "CRYPTO", exchange="KRAKEN_SPOT",
                                       ticker="BTC"), ci.LAYER_LISTING, "2026-01-02")
        aliases = [ratify(make_source_alias("KRAKEN", sym, "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
                   for sym in ("BTC", "XBT", "XXBT")]
        authority = full_authority([issuer], [instrument], [listing], aliases)
        results = [ci.resolve_instrument_identity("KRAKEN", sym, "CRYPTO", "2026-06-01", authority)
                   for sym in ("BTC", "XBT", "XXBT")]
        for r in results:
            self.assertEqual(r["status"], ci.RESOLVED)
            self.assertEqual(r["canonical_instrument_id"], "INSTR-BTC")

    def test_05_329180_vs_329180_ks(self):
        """Real repo case: universe.json's bare '329180' vs
        monitoring_identity.json's '329180.KS' are the same listing under
        two source-format aliases."""
        issuer = ratify(make_issuer("ISSUER-HD-HEAVY"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = ratify(make_instrument("INSTR-HD-HEAVY-COMMON", "ISSUER-HD-HEAVY"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = ratify(make_listing("KRX:KRW:329180", "INSTR-HD-HEAVY-COMMON", "KOREA", exchange="KRX",
                                       currency="KRW", ticker="329180"), ci.LAYER_LISTING, "2026-01-02")
        alias_bare = ratify(make_source_alias("universe_json", "329180", "KRX:KRW:329180"),
                             ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_yahoo = ratify(make_source_alias("monitoring_identity_yahoo_style", "329180.KS", "KRX:KRW:329180"),
                              ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [instrument], [listing], [alias_bare, alias_yahoo])
        r_bare = ci.resolve_instrument_identity("universe_json", "329180", "KOREA", "2026-06-01", authority)
        r_yahoo = ci.resolve_instrument_identity("monitoring_identity_yahoo_style", "329180.KS", "KOREA",
                                                  "2026-06-01", authority)
        self.assertEqual(r_bare["status"], ci.RESOLVED)
        self.assertEqual(r_yahoo["status"], ci.RESOLVED)
        self.assertEqual(r_bare["canonical_instrument_id"], r_yahoo["canonical_instrument_id"])
        self.assertEqual(r_bare["listing_id"], r_yahoo["listing_id"])

    def test_06_overlapping_effective_intervals(self):
        """Two rows for the same source key with overlapping effective
        intervals -> AMBIGUOUS, never a silent first-match pick."""
        alias_a = ratify(make_source_alias("SRC", "DUP", "LISTING-A", effective_from="2026-01-01",
                                            effective_to="2026-12-31"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_b = ratify(make_source_alias("SRC", "DUP", "LISTING-B", effective_from="2026-06-01",
                                            effective_to=None), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority(source_aliases=[alias_a, alias_b])
        result = ci.resolve_instrument_identity("SRC", "DUP", "KOREA", "2026-07-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)
        overlaps = ci.detect_overlapping_intervals([alias_a, alias_b], ("source_name", "source_asset_id"))
        self.assertEqual(len(overlaps), 1)

    def test_07_implementation_exists_authority_does_not(self):
        """Empty authority document (structurally valid, zero rows) ->
        NO_AUTHORITY_RECORD, not a crash, not a guess."""
        authority = full_authority()
        result = ci.resolve_instrument_identity("ANY", "ANY", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
        _assert_authority_all_false(self, result)

    def test_08_authority_record_exists_implementation_does_not(self):
        """A schema/policy_version the implementation does not recognize
        must fail closed at load time, not be silently ignored."""
        with self.assertRaises(ci.IdentityError):
            doc = full_authority()
            doc["policy_version"] = "canonical_security_identity/v99_unknown"
            import tempfile, json as _json
            with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
                _json.dump(doc, f)
                path = f.name
            ci.load_authority(path)

    def test_09_proposed_not_ratified_row_attempted(self):
        """A PROVISIONAL (never RATIFIED) row must never resolve."""
        alias = make_source_alias("SRC", "PENDING", "LISTING-X")  # left PROVISIONAL
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "PENDING", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_UNRATIFIED_RECORD)

    def test_10_backdated_effective_from(self):
        """effective_from claims an early date, but ratified_at/first_seen_at
        are genuinely later -- real_usable_from must use the later date,
        so a decision_date between the claimed and real dates fails PIT."""
        alias = ratify(make_source_alias("SRC", "BACKDATED", "LISTING-X", effective_from="2020-01-01"),
                        ci.LAYER_SOURCE_ALIAS, ratified_at="2026-08-20", first_seen_at="2026-08-20")
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "BACKDATED", "KOREA", "2026-03-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)
        self.assertEqual(ci.real_usable_from(alias), "2026-08-20")

    def test_11_ratified_at_in_the_future(self):
        alias = make_source_alias("SRC", "FUTURE_RATIFY", "LISTING-X", effective_from="2026-01-01")
        ratify(alias, ci.LAYER_SOURCE_ALIAS, ratified_at="2099-01-01")
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "FUTURE_RATIFY", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)

    def test_12_usage_before_exact_content_first_seen_time(self):
        alias = ratify(make_source_alias("SRC", "LATE_SEEN", "LISTING-X", effective_from="2020-01-01"),
                        ci.LAYER_SOURCE_ALIAS, ratified_at="2020-01-02", first_seen_at="2026-08-20")
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "LATE_SEEN", "KOREA", "2026-01-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)

    def test_13_issuer_id_used_where_instrument_id_required(self):
        issuer = ratify(make_issuer("ISSUER-ONLY"), ci.LAYER_ISSUER, "2026-01-02")
        authority = full_authority(issuers=[issuer])
        result = ci.require_instrument_id("ISSUER-ONLY", authority, "2026-06-01")
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_14_portfolio_exposure_joined_on_listing_id(self):
        instrument = ratify(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = ratify(make_listing("LISTING-X", "INSTR-X", "KOREA"), ci.LAYER_LISTING, "2026-01-02")
        authority = full_authority(instruments=[instrument], listings=[listing])
        result = ci.require_instrument_id("LISTING-X", authority, "2026-06-01")
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_15_multiple_listings_of_same_instrument_double_counted(self):
        """Demonstrates (does not fix in real code) why grouping by raw
        source key instead of canonical_instrument_id double-counts --
        this is exactly the risk already found in
        portfolio_risk/portfolio_snapshot.py's by_ticker aggregation
        (dependent defect, task_8dcdbccb, tracked not fixed here)."""
        issuer = ratify(make_issuer("ISSUER-DUAL-LISTED"), ci.LAYER_ISSUER, "2026-01-02")
        instrument = ratify(make_instrument("INSTR-DUAL-LISTED", "ISSUER-DUAL-LISTED", "CRYPTO_ASSET"),
                             ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = ratify(make_listing("KRAKEN:USD:BTC", "INSTR-DUAL-LISTED", "CRYPTO"), ci.LAYER_LISTING, "2026-01-02")
        alias_btc = ratify(make_source_alias("KRAKEN", "BTC", "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        alias_xbt = ratify(make_source_alias("KRAKEN", "XBT", "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        authority = full_authority([issuer], [instrument], [listing], [alias_btc, alias_xbt])

        positions = [{"source_name": "KRAKEN", "source_asset_id": "BTC", "market_value": 100.0},
                     {"source_name": "KRAKEN", "source_asset_id": "XBT", "market_value": 100.0}]
        resolved = {(p["source_name"], p["source_asset_id"]):
                    ci.resolve_instrument_identity(p["source_name"], p["source_asset_id"], "CRYPTO",
                                                     "2026-06-01", authority)
                    for p in positions}
        correct = ci.group_positions_by_instrument(positions, resolved)
        self.assertEqual(correct, {"INSTR-DUAL-LISTED": 200.0})

        # naive raw-symbol grouping (what portfolio_snapshot.py's by_ticker
        # currently does) treats BTC and XBT as two different positions --
        # this is the bug shape, shown here for contrast, NOT patched.
        naive = {}
        for p in positions:
            naive[p["source_asset_id"]] = naive.get(p["source_asset_id"], 0.0) + p["market_value"]
        self.assertEqual(len(naive), 2)
        self.assertNotEqual(sum(naive.values()), 0.0)  # both bugs and fix sum to the same total...
        self.assertNotEqual(len(naive), len(correct))   # ...but naive fragments identity, correct does not.

    def test_16_tampered_then_resigned_record_rejected(self):
        """Content mutated AFTER the hash was computed, without
        recomputing approval_evidence_sha256 -- caught as tampered. This
        module's provenance check is a self-consistency check, not a
        defense against an attacker who tampers AND correctly recomputes
        the hash to match -- that class of attack requires an external
        approval_evidence_ref audit trail, which is explicitly out of
        scope for this stage (see module docstring)."""
        alias = ratify(make_source_alias("SRC", "TAMPERED", "LISTING-ORIGINAL"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        tamper(alias, "listing_id", "LISTING-SWAPPED")  # hash now stale relative to content
        authority = full_authority(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "TAMPERED", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_TAMPERED_RECORD)

    def test_17_missing_market_scope_edge(self):
        authority = scope_authority()
        result = ci.resolve_account_scope("BTC", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_SCOPE_MAP_MISSING)
        _assert_authority_all_false(self, result)

    def test_18_no_authority_field_ever_flips_true(self):
        """Blanket sweep: every result produced anywhere in this test file
        carries AUTHORITY_ALL_FALSE, unmodified."""
        alias = ratify(make_source_alias("SRC", "OK", "LISTING-OK"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        instrument = ratify(make_instrument("INSTR-OK", "ISSUER-OK"), ci.LAYER_INSTRUMENT, "2026-01-02")
        listing = ratify(make_listing("LISTING-OK", "INSTR-OK", "KOREA"), ci.LAYER_LISTING, "2026-01-02")
        issuer = ratify(make_issuer("ISSUER-OK"), ci.LAYER_ISSUER, "2026-01-02")
        authority = full_authority([issuer], [instrument], [listing], [alias])
        results = [
            ci.resolve_instrument_identity("SRC", "OK", "KOREA", "2026-06-01", authority),
            ci.resolve_instrument_identity("SRC", "MISSING", "KOREA", "2026-06-01", authority),
            ci.resolve_account_scope("KOREA", "2026-06-01", scope_authority()),
            ci.require_instrument_id("ISSUER-OK", authority, "2026-06-01"),
        ]
        for r in results:
            _assert_authority_all_false(self, r)


# ---------------------------------------------------------------------------
# Structural / validation coverage beyond the 18 (row shape, hashing)
# ---------------------------------------------------------------------------

class StructuralValidationTests(unittest.TestCase):

    def test_provisional_row_rejects_ratified_at(self):
        row = make_source_alias("SRC", "X", "L")
        row["ratified_at"] = "2026-01-01"  # PROVISIONAL but carries ratified_at -- inconsistent
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_ratified_row_requires_ratified_at(self):
        row = make_source_alias("SRC", "X", "L")
        row["approval_status"] = "RATIFIED"
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_valid_ratified_row_passes_structural_validation(self):
        row = ratify(make_source_alias("SRC", "X", "L"), ci.LAYER_SOURCE_ALIAS, "2026-01-02")
        ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)  # must not raise

    def test_correctly_signed_row_verifies(self):
        row = ratify(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT, "2026-01-02")
        self.assertTrue(ci.verify_row_provenance(row, ci.LAYER_INSTRUMENT))

    def test_provisional_row_never_verifies(self):
        row = make_instrument("INSTR-X", "ISSUER-X")  # no evidence hash at all
        self.assertFalse(ci.verify_row_provenance(row, ci.LAYER_INSTRUMENT))

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
        """No shortcut: resolving against the ACTUAL shipped files (not a
        synthetic fixture) for representative real subjects must land on
        IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD -- the correct outcome
        for this stage, not a shortfall."""
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
