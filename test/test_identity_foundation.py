#!/usr/bin/env python3
"""Identity Foundation stage -- `identity/canonical_identity.py` regression.

★ Rev 3 (CIO code review of HEAD 3bd9e0e, CHANGES_REQUIRED, 5 boundaries).
  The rev-2 report's claim "exact-content provenance verified" is
  downgraded to `PARTIALLY_VERIFIED` (not fully retracted -- rev 2's
  fixes for defects 1-7 of the FIRST review round stand; this round
  closes 5 further gaps found on top of them). See
  `docs/identity_foundation_pr_notes.md`.

This file replaces the registry-based fixture mechanism entirely (the
registry itself was removed from the operational module -- see defect 2
below) with a REAL, disposable git repo per test that needs a RATIFIED
row to actually resolve: `GitAuthorityRepo` commits authority files at
`config/canonical_security_identity.json` (nested, matching the real
repo layout -- defect 3) and evidence files at
`evidence/identity_foundation/approval_records/*.json`, at real,
controlled commit times, so `verify_row_first_seen_at` and
`verify_evidence_first_seen_at` have genuine git history to check.

Covers the 18 originally-required counter-examples (updated to the rev-3
API) plus the rev-2 additions (defects 2/3/5/6 of round 1) plus NEW
counter-examples specifically for round 2:
  - defect 1 (evidence-file-level backdating -- the exact CIO scenario:
    an old row + a brand-new evidence file with a backdated ratified_at)
  - defect 2 (the registry API is verifiably GONE, not just unused)
  - defect 3 (git verification against a REAL nested config/ path)
  - defect 4 (a directly-injected unsupported-version document is
    rejected identically to a file-loaded one, at every entry point)
  - defect 5 (resolve_instrument_by_id / require_instrument_id verify the
    linked issuer -- orphan / PROVISIONAL / ambiguous issuer counter-examples)
"""
from __future__ import annotations

import hashlib
import inspect
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
# Row fixture builders (business-field skeletons, PROVISIONAL by default)
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
# Plain (non-git) evidence file -- usable ONLY for scenarios that fail
# before evidence-first-seen verification is ever reached (e.g. a
# mismatch caught by verify_approval_evidence itself, or tamper). Any
# scenario expected to reach RESOLVED must use GitAuthorityRepo instead.
# ---------------------------------------------------------------------------

def write_plain_evidence_file(evidence_dir: Path, row: dict, layer: str, ratified_at: str) -> None:
    """rev 4: approval fields (approval_status/ratified_at) are set on
    `row` BEFORE the full-determining-payload hash is computed, so the
    evidence file's `approved_full_payload_sha256` reflects the row's
    complete, final RATIFIED state -- including effective_from/
    effective_to, not just business identity fields."""
    row["approval_status"] = "RATIFIED"
    row["ratified_at"] = ratified_at
    full_hash = ci.payload_sha256(ci.full_determining_payload(row, layer))
    content = {
        "rule_id": row["rule_id"], "rule_version": row["rule_version"],
        "approval_status": "RATIFIED", "ratified_at": ratified_at,
        "approved_full_payload_sha256": full_hash,
    }
    data = ci.canonical_json(content).encode("utf-8")
    path = evidence_dir / f"{row['rule_id']}__{row['rule_version']}__{row['business_payload_sha256'][:10]}.json"
    path.write_bytes(data)
    row["approval_evidence_ref"] = str(path)
    row["approval_evidence_sha256"] = hashlib.sha256(data).hexdigest()


def stamp_business_payload(row: dict, layer: str) -> dict:
    row["business_payload_sha256"] = ci.payload_sha256(ci.business_payload(row, layer))
    return row


# ---------------------------------------------------------------------------
# Real, disposable git repo -- the ONLY source of a genuinely verifiable
# first-seen time, for both authority rows and evidence files (defect 2:
# no registry escape hatch exists any more).
# ---------------------------------------------------------------------------

def _git_env_date(iso_z: str) -> str:
    """'2026-08-20T12:00:00Z' -> '2026-08-20T12:00:00+00:00' (git accepts
    explicit-offset ISO 8601 for GIT_AUTHOR_DATE/GIT_COMMITTER_DATE)."""
    return iso_z.replace("Z", "+00:00")


class GitAuthorityRepo:
    """Mirrors the real repo layout: authority files under `config/`,
    evidence files under `evidence/identity_foundation/approval_records/`
    (defect 3 -- exercises the real nested-path git verification, not a
    root-level fixture)."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self._run("init", "-q")
        self._run("config", "user.email", "test@example.com")
        self._run("config", "user.name", "Test")

    def _run(self, *args, env=None):
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, env=env)

    def _commit(self, rel_path: str, data: bytes, commit_iso: str, message: str):
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._run("add", rel_path)
        env = dict(os.environ, GIT_AUTHOR_DATE=_git_env_date(commit_iso), GIT_COMMITTER_DATE=_git_env_date(commit_iso))
        self._run("commit", "-q", "-m", message, env=env)
        return path

    def commit_evidence(self, row: dict, layer: str, ratified_at: str, commit_iso: str,
                         rel_dir: str = "evidence/identity_foundation/approval_records") -> Path:
        """Stamps `business_payload_sha256`, sets approval_status/
        ratified_at on `row` FIRST (rev 4 -- so `approved_full_payload_sha256`
        below reflects the row's complete final RATIFIED state, including
        effective_from/effective_to, not just business identity fields),
        THEN writes+commits a REAL evidence file at a REAL commit time."""
        stamp_business_payload(row, layer)
        row["approval_status"] = "RATIFIED"
        row["ratified_at"] = ratified_at
        full_hash = ci.payload_sha256(ci.full_determining_payload(row, layer))
        content = {
            "rule_id": row["rule_id"], "rule_version": row["rule_version"],
            "approval_status": "RATIFIED", "ratified_at": ratified_at,
            "approved_full_payload_sha256": full_hash,
        }
        data = ci.canonical_json(content).encode("utf-8")
        rel_path = f"{rel_dir}/{row['rule_id']}__{row['rule_version']}__{row['business_payload_sha256'][:10]}.json"
        path = self._commit(rel_path, data, commit_iso, "add approval evidence")
        row["approval_evidence_ref"] = str(path)
        row["approval_evidence_sha256"] = hashlib.sha256(data).hexdigest()
        return path

    def commit_authority(self, doc: dict, commit_iso: str,
                          rel_path: str = "config/canonical_security_identity.json") -> Path:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        data = json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self._commit(rel_path, data, commit_iso, "add authority")

    def commit_scope_authority(self, doc: dict, commit_iso: str,
                                rel_path: str = "config/market_account_scope_map.json") -> Path:
        clean = {k: v for k, v in doc.items() if not k.startswith("_")}
        data = json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return self._commit(rel_path, data, commit_iso, "add scope authority")

    def load_authority(self, rel_path: str = "config/canonical_security_identity.json") -> dict:
        return ci.load_authority(self.root / rel_path)

    def load_scope_authority(self, rel_path: str = "config/market_account_scope_map.json") -> dict:
        return ci.load_scope_authority(self.root / rel_path)

    def write_dirty(self, rel_path: str, data: bytes) -> Path:
        """Writes real bytes to disk WITHOUT staging or committing --
        leaves the working tree genuinely dirty (`git status --porcelain`
        shows the file as modified). Used to reproduce disk-only or
        disk+memory co-tamper scenarios (rev 6)."""
        path = self.root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def head_commit(self) -> str:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.root,
                               capture_output=True, text=True, check=True).stdout.strip()

    def checkout_path_from_commit(self, commit: str, rel_path: str) -> None:
        """`git checkout <commit> -- <rel_path>` -- reverts ONLY that file's
        working-tree content to a real, older commit's real content,
        WITHOUT creating a new commit (HEAD is untouched). Used to
        reproduce the 'revert to an old real single-row commit and use it
        as if it were current' bypass (rev 6)."""
        subprocess.run(["git", "checkout", commit, "--", rel_path], cwd=self.root,
                        capture_output=True, text=True, check=True)


class _GitRepoMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.repo = GitAuthorityRepo(self.tmp_path / "repo")
        self.evidence_dir = self.tmp_path / "plain_evidence"  # for non-git plain evidence, when applicable
        self.evidence_dir.mkdir()

    def tearDown(self):
        self._tmp.cleanup()

    def ratify(self, row, layer, ratified_at="2026-01-02T00:00:00Z", evidence_commit_iso=None):
        """Commits a REAL evidence file for `row` at a real git time (row
        stays a plain in-memory dict until the caller commits the whole
        authority document separately)."""
        self.repo.commit_evidence(row, layer, ratified_at, evidence_commit_iso or ratified_at)
        return row

    def build(self, issuers=(), instruments=(), listings=(), source_aliases=(), commit_iso="2026-01-02T00:00:00Z"):
        doc = full_authority(issuers, instruments, listings, source_aliases)
        path = self.repo.commit_authority(doc, commit_iso)
        return ci.load_authority(path)

    def build_scope(self, edges=(), commit_iso="2026-01-02T00:00:00Z"):
        doc = scope_authority(edges)
        path = self.repo.commit_scope_authority(doc, commit_iso)
        return ci.load_scope_authority(path)


# ---------------------------------------------------------------------------
# 18 originally-required counter-examples (rev-3 API: git-backed)
# ---------------------------------------------------------------------------

class RequiredCounterExamplesTests(_GitRepoMixin, unittest.TestCase):

    def test_01_common_vs_preferred_stock_confusion(self):
        issuer = self.ratify(make_issuer("ISSUER-SAMSUNG-ELEC"), ci.LAYER_ISSUER)
        common = self.ratify(make_instrument("INSTR-SAMSUNG-COMMON", "ISSUER-SAMSUNG-ELEC", "COMMON_STOCK"), ci.LAYER_INSTRUMENT)
        preferred = self.ratify(make_instrument("INSTR-SAMSUNG-PREFERRED", "ISSUER-SAMSUNG-ELEC", "PREFERRED_STOCK"), ci.LAYER_INSTRUMENT)
        listing_common = self.ratify(make_listing("KRX:KRW:005930", "INSTR-SAMSUNG-COMMON", "KOREA",
                                                    exchange="KRX", currency="KRW", ticker="005930"), ci.LAYER_LISTING)
        listing_pref = self.ratify(make_listing("KRX:KRW:005935", "INSTR-SAMSUNG-PREFERRED", "KOREA",
                                                 exchange="KRX", currency="KRW", ticker="005935"), ci.LAYER_LISTING)
        alias_common = self.ratify(make_source_alias("KIS", "005930", "KRX:KRW:005930"), ci.LAYER_SOURCE_ALIAS)
        alias_pref = self.ratify(make_source_alias("KIS", "005935", "KRX:KRW:005935"), ci.LAYER_SOURCE_ALIAS)
        authority = self.build([issuer], [common, preferred], [listing_common, listing_pref], [alias_common, alias_pref])

        r_common = ci.resolve_instrument_identity("KIS", "005930", "KOREA", "2026-06-01", authority)
        r_pref = ci.resolve_instrument_identity("KIS", "005935", "KOREA", "2026-06-01", authority)
        self.assertEqual(r_common["status"], ci.RESOLVED)
        self.assertEqual(r_pref["status"], ci.RESOLVED)
        self.assertEqual(r_common["canonical_issuer_id"], r_pref["canonical_issuer_id"])
        self.assertNotEqual(r_common["canonical_instrument_id"], r_pref["canonical_instrument_id"])
        _assert_authority_all_false(self, r_common)
        _assert_authority_all_false(self, r_pref)

    def test_02_adr_vs_underlying_share_confusion(self):
        issuer = self.ratify(make_issuer("ISSUER-SAMSUNG-ELEC"), ci.LAYER_ISSUER)
        common = self.ratify(make_instrument("INSTR-SAMSUNG-COMMON", "ISSUER-SAMSUNG-ELEC", "COMMON_STOCK"), ci.LAYER_INSTRUMENT)
        adr = self.ratify(make_instrument("INSTR-SAMSUNG-ADR", "ISSUER-SAMSUNG-ELEC", "ADR"), ci.LAYER_INSTRUMENT)
        listing_krx = self.ratify(make_listing("KRX:KRW:005930", "INSTR-SAMSUNG-COMMON", "KOREA",
                                                exchange="KRX", currency="KRW", ticker="005930"), ci.LAYER_LISTING)
        listing_adr = self.ratify(make_listing("OTC:USD:SSNLF", "INSTR-SAMSUNG-ADR", "US",
                                                exchange="OTC", currency="USD", ticker="SSNLF"), ci.LAYER_LISTING)
        alias_krx = self.ratify(make_source_alias("KIS", "005930", "KRX:KRW:005930"), ci.LAYER_SOURCE_ALIAS)
        alias_adr = self.ratify(make_source_alias("ALPACA", "SSNLF", "OTC:USD:SSNLF"), ci.LAYER_SOURCE_ALIAS)
        authority = self.build([issuer], [common, adr], [listing_krx, listing_adr], [alias_krx, alias_adr])

        r_share = ci.resolve_instrument_identity("KIS", "005930", "KOREA", "2026-06-01", authority)
        r_adr = ci.resolve_instrument_identity("ALPACA", "SSNLF", "US", "2026-06-01", authority)
        self.assertEqual(r_share["status"], ci.RESOLVED)
        self.assertEqual(r_adr["status"], ci.RESOLVED)
        self.assertEqual(r_share["canonical_issuer_id"], r_adr["canonical_issuer_id"])
        self.assertNotEqual(r_share["canonical_instrument_id"], r_adr["canonical_instrument_id"])
        self.assertNotEqual(r_share["listing_id"], r_adr["listing_id"])

    def test_03_same_ticker_different_market(self):
        issuer_a = self.ratify(make_issuer("ISSUER-A"), ci.LAYER_ISSUER)
        issuer_b = self.ratify(make_issuer("ISSUER-B"), ci.LAYER_ISSUER)
        instr_a = self.ratify(make_instrument("INSTR-A", "ISSUER-A"), ci.LAYER_INSTRUMENT)
        instr_b = self.ratify(make_instrument("INSTR-B", "ISSUER-B"), ci.LAYER_INSTRUMENT)
        listing_kr = self.ratify(make_listing("KRX:KRW:TICK", "INSTR-A", "KOREA", ticker="TICK"), ci.LAYER_LISTING)
        listing_us = self.ratify(make_listing("NASDAQ:USD:TICK", "INSTR-B", "US", ticker="TICK"), ci.LAYER_LISTING)
        alias_kr = self.ratify(make_source_alias("SRC", "TICK", "KRX:KRW:TICK"), ci.LAYER_SOURCE_ALIAS)
        authority = self.build([issuer_a, issuer_b], [instr_a, instr_b], [listing_kr, listing_us], [alias_kr])
        r_kr = ci.resolve_instrument_identity("SRC", "TICK", "KOREA", "2026-06-01", authority)
        self.assertEqual(r_kr["status"], ci.RESOLVED)
        self.assertEqual(r_kr["canonical_instrument_id"], "INSTR-A")
        r_us = ci.resolve_instrument_identity("SRC", "TICK", "US", "2026-06-01", authority)
        self.assertEqual(r_us["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_04_btc_xbt_xxbt_alias_collision(self):
        issuer = self.ratify(make_issuer("ISSUER-BTC"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-BTC", "ISSUER-BTC", "CRYPTO_ASSET"), ci.LAYER_INSTRUMENT)
        listing = self.ratify(make_listing("KRAKEN:USD:BTC", "INSTR-BTC", "CRYPTO", exchange="KRAKEN_SPOT", ticker="BTC"), ci.LAYER_LISTING)
        aliases = [self.ratify(make_source_alias("KRAKEN", sym, "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS) for sym in ("BTC", "XBT", "XXBT")]
        authority = self.build([issuer], [instrument], [listing], aliases)
        for sym in ("BTC", "XBT", "XXBT"):
            r = ci.resolve_instrument_identity("KRAKEN", sym, "CRYPTO", "2026-06-01", authority)
            self.assertEqual(r["status"], ci.RESOLVED)
            self.assertEqual(r["canonical_instrument_id"], "INSTR-BTC")

    def test_05_329180_vs_329180_ks(self):
        issuer = self.ratify(make_issuer("ISSUER-HD-HEAVY"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-HD-HEAVY-COMMON", "ISSUER-HD-HEAVY"), ci.LAYER_INSTRUMENT)
        listing = self.ratify(make_listing("KRX:KRW:329180", "INSTR-HD-HEAVY-COMMON", "KOREA",
                                            exchange="KRX", currency="KRW", ticker="329180"), ci.LAYER_LISTING)
        alias_bare = self.ratify(make_source_alias("universe_json", "329180", "KRX:KRW:329180"), ci.LAYER_SOURCE_ALIAS)
        alias_yahoo = self.ratify(make_source_alias("monitoring_identity_yahoo_style", "329180.KS", "KRX:KRW:329180"), ci.LAYER_SOURCE_ALIAS)
        authority = self.build([issuer], [instrument], [listing], [alias_bare, alias_yahoo])
        r_bare = ci.resolve_instrument_identity("universe_json", "329180", "KOREA", "2026-06-01", authority)
        r_yahoo = ci.resolve_instrument_identity("monitoring_identity_yahoo_style", "329180.KS", "KOREA", "2026-06-01", authority)
        self.assertEqual(r_bare["status"], ci.RESOLVED)
        self.assertEqual(r_yahoo["status"], ci.RESOLVED)
        self.assertEqual(r_bare["canonical_instrument_id"], r_yahoo["canonical_instrument_id"])
        self.assertEqual(r_bare["listing_id"], r_yahoo["listing_id"])

    def test_06_overlapping_effective_intervals(self):
        alias_a = make_source_alias("SRC", "DUP", "LISTING-A", effective_from="2026-01-01", effective_to="2026-12-31")
        alias_b = make_source_alias("SRC", "DUP", "LISTING-B", effective_from="2026-06-01", effective_to=None)
        authority = full_authority(source_aliases=[alias_a, alias_b])  # both PROVISIONAL -- no git needed
        result = ci.resolve_instrument_identity("SRC", "DUP", "KOREA", "2026-07-01", authority)
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
        alias = make_source_alias("SRC", "BACKDATED", "LISTING-X", effective_from="2020-01-01")
        self.ratify(alias, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-08-20T00:00:00Z", evidence_commit_iso="2026-08-20T00:00:00Z")
        authority = self.build(source_aliases=[alias], commit_iso="2026-08-20T00:00:00Z")
        result = ci.resolve_instrument_identity("SRC", "BACKDATED", "KOREA", "2026-03-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)

    def test_11_ratified_at_in_the_future(self):
        alias = make_source_alias("SRC", "FUTURE_RATIFY", "LISTING-X", effective_from="2026-01-01")
        self.ratify(alias, ci.LAYER_SOURCE_ALIAS, ratified_at="2099-01-01T00:00:00Z", evidence_commit_iso="2026-01-02T00:00:00Z")
        authority = self.build(source_aliases=[alias])
        result = ci.resolve_instrument_identity("SRC", "FUTURE_RATIFY", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)

    def test_12_usage_before_exact_content_first_seen_time(self):
        """The row's self-declared first_seen_at ('2020-01-01') is a lie
        -- the REAL git history of the authority file shows it was only
        ever committed on 2026-08-20. verify_row_first_seen_at must use
        the real git time, never the self-declared claim."""
        row = make_source_alias("SRC", "LATE_SEEN", "LISTING-X", effective_from="2020-01-01", first_seen_at="2020-01-01")
        self.ratify(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2020-01-02T00:00:00Z", evidence_commit_iso="2026-08-20T00:00:00Z")
        authority = self.build(source_aliases=[row], commit_iso="2026-08-20T00:00:00Z")
        result = ci.resolve_instrument_identity("SRC", "LATE_SEEN", "KOREA", "2026-01-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)
        self.assertTrue(result["identity_basis"]["source_alias"]["verified_row_first_seen_at"].startswith("2026-08-20"))
        self.assertNotEqual(result["identity_basis"]["source_alias"]["verified_row_first_seen_at"], row["first_seen_at"])

    def test_13_issuer_id_used_where_instrument_id_required(self):
        issuer = self.ratify(make_issuer("ISSUER-ONLY"), ci.LAYER_ISSUER)
        authority = self.build(issuers=[issuer])
        result = ci.require_instrument_id("ISSUER-ONLY", authority, "2026-06-01")
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_14_portfolio_exposure_joined_on_listing_id(self):
        instrument = self.ratify(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        listing = self.ratify(make_listing("LISTING-X", "INSTR-X", "KOREA"), ci.LAYER_LISTING)
        authority = self.build(instruments=[instrument], listings=[listing])
        result = ci.require_instrument_id("LISTING-X", authority, "2026-06-01")
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_LAYER_MISMATCH)

    def test_15_multiple_listings_of_same_instrument_double_counted(self):
        issuer = self.ratify(make_issuer("ISSUER-DUAL-LISTED"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-DUAL-LISTED", "ISSUER-DUAL-LISTED", "CRYPTO_ASSET"), ci.LAYER_INSTRUMENT)
        listing = self.ratify(make_listing("KRAKEN:USD:BTC", "INSTR-DUAL-LISTED", "CRYPTO"), ci.LAYER_LISTING)
        alias_btc = self.ratify(make_source_alias("KRAKEN", "BTC", "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS)
        alias_xbt = self.ratify(make_source_alias("KRAKEN", "XBT", "KRAKEN:USD:BTC"), ci.LAYER_SOURCE_ALIAS)
        authority = self.build([issuer], [instrument], [listing], [alias_btc, alias_xbt])
        positions = [{"source_name": "KRAKEN", "source_asset_id": "BTC", "market_value": 100.0},
                     {"source_name": "KRAKEN", "source_asset_id": "XBT", "market_value": 100.0}]
        resolved = {(p["source_name"], p["source_asset_id"]):
                    ci.resolve_instrument_identity(p["source_name"], p["source_asset_id"], "CRYPTO", "2026-06-01", authority)
                    for p in positions}
        correct = ci.group_positions_by_instrument(positions, resolved)
        self.assertEqual(correct, {"INSTR-DUAL-LISTED": 200.0})
        naive = {}
        for p in positions:
            naive[p["source_asset_id"]] = naive.get(p["source_asset_id"], 0.0) + p["market_value"]
        self.assertNotEqual(len(naive), len(correct))

    def test_16_tampered_then_resigned_record_rejected(self):
        """Business field mutated AFTER ratification, WITH
        business_payload_sha256 correctly recomputed ('resigned') -- the
        real (untouched) evidence file's `approved_full_payload_sha256`
        still points at the OLD hash. This fails before any git-history
        lookup is ever reached, so a plain (non-git) evidence file
        suffices here."""
        row = make_source_alias("SRC", "TAMPERED", "LISTING-ORIGINAL")
        stamp_business_payload(row, ci.LAYER_SOURCE_ALIAS)
        write_plain_evidence_file(self.evidence_dir, row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-01-02T00:00:00Z")
        row["listing_id"] = "LISTING-SWAPPED"
        row["business_payload_sha256"] = ci.payload_sha256(ci.business_payload(row, ci.LAYER_SOURCE_ALIAS))
        self.assertTrue(ci.verify_business_payload(row, ci.LAYER_SOURCE_ALIAS))  # self-consistency now "passes"...
        authority = full_authority(source_aliases=[row])
        result = ci.resolve_instrument_identity("SRC", "TAMPERED", "KOREA", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED)

    def test_17_missing_market_scope_edge(self):
        authority = scope_authority()
        result = ci.resolve_account_scope("BTC", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_SCOPE_MAP_MISSING)
        _assert_authority_all_false(self, result)

    def test_18_no_authority_field_ever_flips_true(self):
        alias = self.ratify(make_source_alias("SRC", "OK", "LISTING-OK"), ci.LAYER_SOURCE_ALIAS)
        instrument = self.ratify(make_instrument("INSTR-OK", "ISSUER-OK"), ci.LAYER_INSTRUMENT)
        listing = self.ratify(make_listing("LISTING-OK", "INSTR-OK", "KOREA"), ci.LAYER_LISTING)
        issuer = self.ratify(make_issuer("ISSUER-OK"), ci.LAYER_ISSUER)
        authority = self.build([issuer], [instrument], [listing], [alias])
        results = [
            ci.resolve_instrument_identity("SRC", "OK", "KOREA", "2026-06-01", authority),
            ci.resolve_instrument_identity("SRC", "MISSING", "KOREA", "2026-06-01", authority),
            ci.resolve_account_scope("KOREA", "2026-06-01", scope_authority()),
            ci.require_instrument_id("ISSUER-OK", authority, "2026-06-01"),
        ]
        for r in results:
            _assert_authority_all_false(self, r)


# ---------------------------------------------------------------------------
# Round-2 (rev 3) counter-examples -- defects 1, 2, 3, 4, 5
# ---------------------------------------------------------------------------

class Defect1EvidenceBackdatingTests(_GitRepoMixin, unittest.TestCase):
    """The exact CIO scenario: an old row + a brand-new evidence file
    with a backdated ratified_at must NOT resolve as usable back then."""

    def test_backdated_evidence_file_blocked_by_evidence_first_seen(self):
        row = make_source_alias("SRC", "OLD_ROW_NEW_EVIDENCE", "LISTING-X",
                                 effective_from="2020-01-01", first_seen_at="2020-01-01")
        stamp_business_payload(row, ci.LAYER_SOURCE_ALIAS)
        # Step 1: the row itself genuinely existed (as PROVISIONAL) back
        # in 2020 -- a real, early git commit establishes its real
        # first-seen time honestly.
        self.repo.commit_authority(full_authority(source_aliases=[row]), commit_iso="2020-01-01T00:00:00Z")
        # Step 2: TODAY (2026-08-24), someone ratifies it with a brand-new
        # evidence file whose CONTENT claims an early, backdated
        # ratified_at ("2020-01-05") -- but the evidence file's REAL git
        # commit time is today.
        self.repo.commit_evidence(row, ci.LAYER_SOURCE_ALIAS, ratified_at="2020-01-05T00:00:00Z",
                                   commit_iso="2026-08-24T00:00:00Z")
        authority_path = self.repo.commit_authority(full_authority(source_aliases=[row]), commit_iso="2026-08-24T00:00:00Z")
        authority = ci.load_authority(authority_path)

        # Without the fix, effective_from/ratified_at/verified_row_first_seen_at
        # are all ~2020, so a 2020-01-05 decision_date would have wrongly
        # succeeded. With the fix, verified_evidence_first_seen_at (~2026-08-24,
        # the evidence file's REAL git time) dominates the max().
        result = ci.resolve_instrument_identity("SRC", "OLD_ROW_NEW_EVIDENCE", "KOREA", "2020-01-05", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_PIT_VIOLATION)
        basis = result["identity_basis"]["source_alias"]
        self.assertTrue(basis["verified_evidence_first_seen_at"].startswith("2026-08-24"))
        self.assertNotEqual(basis["verified_evidence_first_seen_at"], row["ratified_at"])

        # A decision_date genuinely at/after the evidence file's real
        # first-seen time clears the alias layer's own PIT gate entirely
        # (this fixture never defines a listing/instrument/issuer chain,
        # so the overall result is NO_AUTHORITY_RECORD from the NEXT
        # layer, not RESOLVED -- what matters here is that the alias
        # layer itself is no longer blocked).
        result_after = ci.resolve_instrument_identity("SRC", "OLD_ROW_NEW_EVIDENCE", "KOREA", "2026-09-01", authority)
        self.assertNotIn(result_after["status"], (
            ci.NOT_COMPUTABLE_PIT_VIOLATION, ci.NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED,
            ci.NOT_COMPUTABLE_EVIDENCE_FIRST_SEEN_UNVERIFIED, ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED,
            ci.NOT_COMPUTABLE_TAMPERED_RECORD, ci.NOT_COMPUTABLE_UNRATIFIED_RECORD))
        self.assertEqual(result_after["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)  # missing listing, not a PIT issue

    def test_evidence_ratified_at_mismatch_with_row_rejected(self):
        """The evidence file's own claimed ratified_at must equal the
        row's -- an attacker who edits only the row's ratified_at without
        updating the (real, hash-verified) evidence file's content is
        caught, independent of the first-seen fix."""
        row = make_source_alias("SRC", "MISMATCHED_RATIFIED_AT", "LISTING-X")
        stamp_business_payload(row, ci.LAYER_SOURCE_ALIAS)
        write_plain_evidence_file(self.evidence_dir, row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-01-02T00:00:00Z")
        row["ratified_at"] = "2026-06-01T00:00:00Z"  # diverge from what the real evidence file says
        self.assertFalse(ci.verify_approval_evidence(row, ci.LAYER_SOURCE_ALIAS))

    def test_evidence_file_not_git_tracked_is_unverified(self):
        row = make_source_alias("SRC", "NO_GIT_EVIDENCE", "LISTING-X")
        stamp_business_payload(row, ci.LAYER_SOURCE_ALIAS)
        write_plain_evidence_file(self.evidence_dir, row, ci.LAYER_SOURCE_ALIAS, ratified_at="2026-01-02T00:00:00Z")  # plain temp dir, no git
        self.assertIsNone(ci.verify_evidence_first_seen_at(row))


class Defect2RegistryRemovedTests(unittest.TestCase):
    """The append-only registry is verifiably GONE from the operational
    module -- not merely unused."""

    def test_record_first_seen_function_does_not_exist(self):
        self.assertFalse(hasattr(ci, "record_first_seen"))

    def test_no_registry_path_parameter_anywhere(self):
        for fn in (ci.resolve_instrument_identity, ci.resolve_account_scope,
                   ci.resolve_instrument_by_id, ci.require_instrument_id):
            params = inspect.signature(fn).parameters
            self.assertNotIn("registry_path", params, f"{fn.__name__} still accepts registry_path")

    def test_verify_row_first_seen_uses_only_git_path_no_fallback(self):
        params = inspect.signature(ci.verify_row_first_seen_at).parameters
        self.assertEqual(set(params), {"row", "layer", "git_path"})
        self.assertIsNone(ci.verify_row_first_seen_at(make_source_alias("SRC", "X", "L"), ci.LAYER_SOURCE_ALIAS, None))


class Defect3RealNestedConfigPathTests(_GitRepoMixin, unittest.TestCase):
    """Git verification against the REAL repo-root-relative path of a
    file nested under config/, not a root-level test fixture."""

    def test_git_history_found_for_real_nested_config_path(self):
        row = self.ratify(make_source_alias("SRC", "NESTED_PATH", "LISTING-X"), ci.LAYER_SOURCE_ALIAS)
        path = self.repo.commit_authority(full_authority(source_aliases=[row]), commit_iso="2026-01-02T00:00:00Z")
        self.assertEqual(path, self.repo.root / "config" / "canonical_security_identity.json")
        verified = ci.verify_row_first_seen_at(row, ci.LAYER_SOURCE_ALIAS, path)
        self.assertIsNotNone(verified)
        self.assertTrue(verified.startswith("2026-01-02"))

    def test_real_repo_config_file_git_history_is_actually_walkable(self):
        """Sanity check against THIS repo's own real, committed
        config/canonical_security_identity.json -- proves the path-
        resolution mechanism finds real history for the real nested path
        (not just a temp-repo fixture)."""
        commits = ci._git_history_commits(ci.SECURITY_IDENTITY_PATH)
        self.assertGreater(len(commits), 0)
        for _hash, _iso, rel_posix in commits:
            self.assertEqual(rel_posix, "config/canonical_security_identity.json")

    def test_basename_only_lookup_would_have_failed(self):
        """Regression proof that the rev-2 bug (basename-only `git show`)
        is real: `git show <rev>:canonical_security_identity.json` (no
        `config/` prefix) must NOT find the real committed content,
        confirming the nested repo-relative path is required."""
        repo_root = ci._git_repo_root(ci.SECURITY_IDENTITY_PATH)
        commits = ci._git_history_commits(ci.SECURITY_IDENTITY_PATH)
        commit_hash = commits[-1][0]
        bare_basename_result = ci._git_show_bytes(repo_root, commit_hash, "canonical_security_identity.json")
        real_path_result = ci._git_show_bytes(repo_root, commit_hash, "config/canonical_security_identity.json")
        self.assertIsNone(bare_basename_result)
        self.assertIsNotNone(real_path_result)


class Defect4DocumentLevelValidationTests(unittest.TestCase):
    """A directly-injected (never file-loaded) document with an
    unsupported policy_version must be rejected identically to a
    file-loaded one, at EVERY entry point."""

    def test_injected_unsupported_version_rejected_by_resolve_instrument_identity(self):
        doc = full_authority()
        doc["policy_version"] = "canonical_security_identity/v99_unknown"
        with self.assertRaises(ci.IdentityError):
            ci.resolve_instrument_identity("SRC", "X", "KOREA", "2026-06-01", doc)

    def test_injected_unsupported_version_rejected_by_resolve_instrument_by_id(self):
        doc = full_authority()
        doc["policy_version"] = "canonical_security_identity/v99_unknown"
        with self.assertRaises(ci.IdentityError):
            ci.resolve_instrument_by_id("INSTR-X", "2026-06-01", doc)

    def test_injected_unsupported_version_rejected_by_require_instrument_id(self):
        doc = full_authority()
        doc["policy_version"] = "canonical_security_identity/v99_unknown"
        with self.assertRaises(ci.IdentityError):
            ci.require_instrument_id("INSTR-X", doc, "2026-06-01")

    def test_injected_unsupported_scope_version_rejected_by_resolve_account_scope(self):
        doc = scope_authority()
        doc["policy_version"] = "market_account_scope_map/v99_unknown"
        with self.assertRaises(ci.IdentityError):
            ci.resolve_account_scope("KOREA", "2026-06-01", doc)

    def test_injected_missing_required_array_rejected(self):
        doc = full_authority()
        del doc["instruments"]
        with self.assertRaises(ci.IdentityError):
            ci.resolve_instrument_identity("SRC", "X", "KOREA", "2026-06-01", doc)

    def test_document_validators_used_identically_by_load_authority(self):
        """The same validator function governs both paths -- not two
        independently-maintained checks that could drift apart."""
        doc = full_authority()
        doc["policy_version"] = "canonical_security_identity/v99_unknown"
        with self.assertRaises(ci.IdentityError):
            ci.validate_security_identity_document(doc)
        path = Path(tempfile.mkstemp(suffix=".json")[1])
        path.write_text(json.dumps(doc), encoding="utf-8")
        try:
            with self.assertRaises(ci.IdentityError):
                ci.load_authority(path)
        finally:
            path.unlink()


class Defect5IssuerChainVerificationTests(_GitRepoMixin, unittest.TestCase):
    """resolve_instrument_by_id (and require_instrument_id, which
    delegates to it) must verify the linked issuer through the same gate,
    not return RESOLVED on the instrument row alone."""

    def test_orphan_issuer_blocks_resolution(self):
        instrument = self.ratify(make_instrument("INSTR-ORPHAN", "ISSUER-DOES-NOT-EXIST"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instrument])  # no issuer row at all
        result = ci.resolve_instrument_by_id("INSTR-ORPHAN", "2026-06-01", authority)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)
        self.assertIsNone(result["canonical_instrument_id"])

    def test_provisional_issuer_blocks_resolution(self):
        issuer = make_issuer("ISSUER-PROVISIONAL-ONLY")  # never ratified
        instrument = self.ratify(make_instrument("INSTR-X", "ISSUER-PROVISIONAL-ONLY"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        result = ci.resolve_instrument_by_id("INSTR-X", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_UNRATIFIED_RECORD)

    def test_ambiguous_issuer_blocks_resolution(self):
        issuer_1 = self.ratify(make_issuer("ISSUER-DUP", issuer_name_reference="A"), ci.LAYER_ISSUER)
        issuer_2 = self.ratify(make_issuer("ISSUER-DUP", issuer_name_reference="B"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-X", "ISSUER-DUP"), ci.LAYER_INSTRUMENT)
        authority = self.build([issuer_1, issuer_2], [instrument])
        result = ci.resolve_instrument_by_id("INSTR-X", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

    def test_valid_issuer_chain_resolves(self):
        issuer = self.ratify(make_issuer("ISSUER-OK"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-OK", "ISSUER-OK"), ci.LAYER_INSTRUMENT)
        authority = self.build([issuer], [instrument])
        result = ci.resolve_instrument_by_id("INSTR-OK", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["canonical_issuer_id"], "ISSUER-OK")

    def test_require_instrument_id_end_to_end_blocked_by_orphan_issuer(self):
        """Confirms the fix propagates through require_instrument_id too
        (it delegates to resolve_instrument_by_id)."""
        instrument = self.ratify(make_instrument("INSTR-ORPHAN-2", "ISSUER-MISSING"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instrument])
        result = ci.require_instrument_id("INSTR-ORPHAN-2", authority, "2026-06-01")
        self.assertNotEqual(result["status"], ci.RESOLVED)


# ---------------------------------------------------------------------------
# Round-3 (rev 4) counter-examples -- CIO direct-reproduction P0: the
# approval-evidence (and git-history) binding must cover the FULL
# determining payload, not just business-identity fields.
# ---------------------------------------------------------------------------

class Defect6FullPayloadBindingTests(_GitRepoMixin, unittest.TestCase):

    def test_expired_effective_to_nulled_without_resigning_blocked(self):
        """The exact CIO direct-reproduction counter-example: take an
        already-expired RATIFIED instrument row and mutate ONLY its
        in-memory effective_to to null -- evidence file, its hash, and
        its git first-seen all untouched. Must NOT resolve.

            Original:  IDENTITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD
            Tampered:  must NOT be RESOLVED (TAMPERED_RECORD or a
                       provenance error only)

        rev 5: the whole-document tamper check now fires FIRST (any
        mutation to the loaded document, including a single field, no
        longer matches the real file's current bytes) -- a strictly
        earlier and stronger catch than the row-level full-payload check
        alone, which remains as independent defense-in-depth for cases
        without a real `_source_path` to compare against."""
        instrument = make_instrument("INSTR-EXPIRES", "ISSUER-X",
                                      effective_from="2020-01-01", effective_to="2021-01-01")
        self.ratify(instrument, ci.LAYER_INSTRUMENT, ratified_at="2020-01-02T00:00:00Z",
                    evidence_commit_iso="2020-01-02T00:00:00Z")
        authority = self.build(instruments=[instrument], commit_iso="2020-01-02T00:00:00Z")

        original = ci.resolve_instrument_by_id("INSTR-EXPIRES", "2022-01-01", authority)
        self.assertEqual(original["status"], ci.NOT_COMPUTABLE_NO_AUTHORITY_RECORD)

        loaded_row = authority["instruments"][0]
        self.assertEqual(loaded_row["canonical_instrument_id"], "INSTR-EXPIRES")
        loaded_row["effective_to"] = None  # tamper ONLY this -- no re-signing, no new commit

        tampered = ci.resolve_instrument_by_id("INSTR-EXPIRES", "2022-01-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertIn(tampered["status"], (ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED,
                                            ci.NOT_COMPUTABLE_TAMPERED_RECORD,
                                            ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED))
        self.assertIsNone(tampered["canonical_instrument_id"])
        _assert_authority_all_false(self, tampered)

    def test_isolated_tamper_effective_from(self):
        instrument = make_instrument("INSTR-TAMPER-EF", "ISSUER-X", effective_from="2020-01-01")
        self.ratify(instrument, ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instrument])
        authority["instruments"][0]["effective_from"] = "2019-01-01"  # earlier than what was ever approved
        result = ci.resolve_instrument_by_id("INSTR-TAMPER-EF", "2026-06-01", authority)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertIn(result["status"], (ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED,
                                          ci.NOT_COMPUTABLE_TAMPERED_RECORD,
                                          ci.NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED,
                                          ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED))

    def test_isolated_tamper_rule_version(self):
        instrument = self.ratify(make_instrument("INSTR-TAMPER-RV", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instrument])
        authority["instruments"][0]["rule_version"] = "2"  # bumped without a real re-ratification
        result = ci.resolve_instrument_by_id("INSTR-TAMPER-RV", "2026-06-01", authority)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertIn(result["status"], (ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED,
                                          ci.NOT_COMPUTABLE_TAMPERED_RECORD,
                                          ci.NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED,
                                          ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED))

    def test_isolated_tamper_ratified_at(self):
        instrument = self.ratify(make_instrument("INSTR-TAMPER-RA", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instrument])
        authority["instruments"][0]["ratified_at"] = "2020-06-01T00:00:00Z"  # different from what was actually approved
        result = ci.resolve_instrument_by_id("INSTR-TAMPER-RA", "2026-06-01", authority)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertIn(result["status"], (ci.NOT_COMPUTABLE_APPROVAL_EVIDENCE_UNVERIFIED,
                                          ci.NOT_COMPUTABLE_TAMPERED_RECORD,
                                          ci.NOT_COMPUTABLE_FIRST_SEEN_UNVERIFIED,
                                          ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED))

    def test_row_first_seen_matching_rejects_content_not_actually_committed(self):
        """Direct, low-level proof of the '_source_path borrowing' fix
        (item 3): verify_row_first_seen_at itself -- independent of
        verify_approval_evidence -- refuses to match a row whose current
        full content was never actually committed. This closes the
        bypass even in a hypothetical where the evidence-file check were
        somehow defeated."""
        instrument = self.ratify(make_instrument("INSTR-GIT-MATCH", "ISSUER-X", effective_to="2030-01-01"), ci.LAYER_INSTRUMENT)
        path = self.repo.commit_authority(full_authority(instruments=[instrument]), commit_iso="2026-01-02T00:00:00Z")
        loaded = ci.load_authority(path)
        loaded_row = loaded["instruments"][0]

        verified_before = ci.verify_row_first_seen_at(loaded_row, ci.LAYER_INSTRUMENT, path)
        self.assertIsNotNone(verified_before)

        loaded_row["effective_to"] = None  # mutate only in memory, never re-committed
        verified_after = ci.verify_row_first_seen_at(loaded_row, ci.LAYER_INSTRUMENT, path)
        self.assertIsNone(verified_after)

    def test_full_determining_payload_includes_all_required_fields(self):
        """Structural proof that the payload CIO required is exactly
        what's hashed -- business fields plus rule_id/rule_version/
        approval_status/ratified_at/effective_from/effective_to."""
        row = self.ratify(make_instrument("INSTR-FIELDS", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        payload = ci.full_determining_payload(row, ci.LAYER_INSTRUMENT)
        for required_field in ("rule_id", "rule_version", "approval_status", "ratified_at",
                                "effective_from", "effective_to", "canonical_instrument_id",
                                "canonical_issuer_id", "instrument_type"):
            self.assertIn(required_field, payload)


# ---------------------------------------------------------------------------
# Round-4 (rev 5) counter-examples -- CIO direct-reproduction P0: a
# per-row check alone cannot catch document-level row insertion/deletion/
# reordering, even when every remaining row is completely real and
# untouched. The exact reproduction:
#
#   Original document (2 conflicting active RATIFIED rows): AMBIGUOUS
#   After deleting one conflicting row (evidence/hash/git-history/the
#   remaining row itself all untouched):                    must NOT be
#                                                             RESOLVED
# ---------------------------------------------------------------------------

class Defect7DocumentLevelTamperTests(_GitRepoMixin, unittest.TestCase):

    def test_untouched_loaded_document_matches_source(self):
        instrument = self.ratify(make_instrument("INSTR-CLEAN", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instrument])
        self.assertTrue(ci.verify_document_matches_source(authority))

    def test_document_without_source_path_is_not_document_tampered(self):
        """A purely synthetic/injected document (no _source_path at all)
        is not itself a 'document tamper' finding -- it has no real file
        to compare against, and falls through to the existing per-row
        checks, which correctly resolve their own NOT_COMPUTABLE_* status."""
        row = make_source_alias("SRC", "PENDING", "LISTING-X")
        authority = full_authority(source_aliases=[row])
        result = ci.resolve_instrument_identity("SRC", "PENDING", "KOREA", "2026-06-01", authority)
        self.assertNotEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_row_deletion_instrument_array_blocked(self):
        """CIO's exact reproduction, instrument array."""
        instr_1 = self.ratify(make_instrument("INSTR-CONFLICT-DOC", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        instr_2 = self.ratify(make_instrument("INSTR-CONFLICT-DOC", "ISSUER-TWO"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instr_1, instr_2])

        baseline = ci.resolve_instrument_by_id("INSTR-CONFLICT-DOC", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        del authority["instruments"][1]  # remaining row is completely real, untouched

        tampered = ci.resolve_instrument_by_id("INSTR-CONFLICT-DOC", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)
        self.assertIsNone(tampered["canonical_instrument_id"])
        _assert_authority_all_false(self, tampered)

    def test_document_tamper_row_deletion_issuer_array_blocked(self):
        issuer_1 = self.ratify(make_issuer("ISSUER-CONFLICT-DOC", issuer_name_reference="A"), ci.LAYER_ISSUER)
        issuer_2 = self.ratify(make_issuer("ISSUER-CONFLICT-DOC", issuer_name_reference="B"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-FOR-ISSUER-CONFLICT", "ISSUER-CONFLICT-DOC"), ci.LAYER_INSTRUMENT)
        authority = self.build([issuer_1, issuer_2], [instrument])

        baseline = ci.resolve_instrument_by_id("INSTR-FOR-ISSUER-CONFLICT", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        del authority["issuers"][1]

        tampered = ci.resolve_instrument_by_id("INSTR-FOR-ISSUER-CONFLICT", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_row_deletion_listing_array_blocked(self):
        alias = self.ratify(make_source_alias("SRC", "LISTING-CONFLICT-KEY", "LISTING-CONFLICT-DOC"), ci.LAYER_SOURCE_ALIAS)
        listing_1 = self.ratify(make_listing("LISTING-CONFLICT-DOC", "INSTR-X", "KOREA"), ci.LAYER_LISTING)
        listing_2 = self.ratify(make_listing("LISTING-CONFLICT-DOC", "INSTR-Y", "KOREA"), ci.LAYER_LISTING)
        authority = self.build(listings=[listing_1, listing_2], source_aliases=[alias])

        baseline = ci.resolve_instrument_identity("SRC", "LISTING-CONFLICT-KEY", "KOREA", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        del authority["listings"][1]

        tampered = ci.resolve_instrument_identity("SRC", "LISTING-CONFLICT-KEY", "KOREA", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_row_deletion_source_alias_array_blocked(self):
        alias_1 = self.ratify(make_source_alias("SRC", "ALIAS-CONFLICT-DOC", "LISTING-A"), ci.LAYER_SOURCE_ALIAS)
        alias_2 = self.ratify(make_source_alias("SRC", "ALIAS-CONFLICT-DOC", "LISTING-B"), ci.LAYER_SOURCE_ALIAS)
        authority = self.build(source_aliases=[alias_1, alias_2])

        baseline = ci.resolve_instrument_identity("SRC", "ALIAS-CONFLICT-DOC", "KOREA", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        del authority["source_aliases"][1]

        tampered = ci.resolve_instrument_identity("SRC", "ALIAS-CONFLICT-DOC", "KOREA", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_row_deletion_scope_edge_array_blocked(self):
        edge_1 = self.ratify(make_scope_edge("CRYPTO", "CRYPTO_MANUAL_ACCOUNT"), ci.LAYER_MARKET_ACCOUNT_SCOPE)
        edge_2 = self.ratify(make_scope_edge("CRYPTO", "ALPACA_PAPER_ACCOUNT"), ci.LAYER_MARKET_ACCOUNT_SCOPE)
        authority = self.build_scope(edges=[edge_1, edge_2])

        baseline = ci.resolve_account_scope("CRYPTO", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        del authority["edges"][1]

        tampered = ci.resolve_account_scope("CRYPTO", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_row_insertion_blocked(self):
        """Adding a row (not just deleting) must also fail-closed."""
        issuer = self.ratify(make_issuer("ISSUER-INSERT-BASE"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-INSERT-BASE", "ISSUER-INSERT-BASE"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        baseline = ci.resolve_instrument_by_id("INSTR-INSERT-BASE", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.RESOLVED)

        authority["instruments"].append(make_instrument("INSTR-INJECTED", "ISSUER-INSERT-BASE"))  # never committed

        tampered = ci.resolve_instrument_by_id("INSTR-INSERT-BASE", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_row_reordering_blocked(self):
        """Reordering rows within an array (no add/remove/field edit) must
        also fail-closed -- canonical_json preserves list order, so a
        pure swap changes the whole-document hash."""
        issuer = self.ratify(make_issuer("ISSUER-ORDER-BASE"), ci.LAYER_ISSUER)
        instr_a = self.ratify(make_instrument("INSTR-ORDER-A", "ISSUER-ORDER-BASE"), ci.LAYER_INSTRUMENT)
        instr_b = self.ratify(make_instrument("INSTR-ORDER-B", "ISSUER-ORDER-BASE"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instr_a, instr_b])
        baseline = ci.resolve_instrument_by_id("INSTR-ORDER-A", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.RESOLVED)

        authority["instruments"].reverse()  # pure reorder, zero content change

        tampered = ci.resolve_instrument_by_id("INSTR-ORDER-A", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertEqual(tampered["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_document_tamper_check_applies_at_require_instrument_id_entry_too(self):
        """The document check runs before identify_layer_of_id inside
        require_instrument_id, not just inside resolve_instrument_by_id."""
        instr_1 = self.ratify(make_instrument("INSTR-REQ-CONFLICT", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        instr_2 = self.ratify(make_instrument("INSTR-REQ-CONFLICT", "ISSUER-TWO"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instr_1, instr_2])
        del authority["instruments"][1]
        result = ci.require_instrument_id("INSTR-REQ-CONFLICT", authority, "2026-06-01")
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)


# ---------------------------------------------------------------------------
# Round-5 (rev 6) counter-examples -- CIO direct-reproduction P0: rev 5's
# memory-vs-disk-only check misses a co-tamper where BOTH sides are edited
# together (never committed). The exact reproduction:
#
#   Original:                     IDENTITY_NOT_COMPUTABLE_AMBIGUOUS
#   Row deleted in memory+disk:   RESOLVED   (the rev-5 bug)
#
# with the real git commit, approval evidence, the remaining row, and git
# history all completely untouched -- only the working tree made dirty.
# ---------------------------------------------------------------------------

class Defect8DiskGitProvenanceTests(_GitRepoMixin, unittest.TestCase):

    def test_document_tamper_row_deleted_in_memory_and_disk_blocked(self):
        """CIO's exact round-6 reproduction."""
        instr_1 = self.ratify(make_instrument("INSTR-DISK-CONFLICT", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        instr_2 = self.ratify(make_instrument("INSTR-DISK-CONFLICT", "ISSUER-TWO"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instr_1, instr_2])
        head_before = self.repo.head_commit()

        baseline = ci.resolve_instrument_by_id("INSTR-DISK-CONFLICT", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        # Tamper BOTH sides together: delete the conflicting row from the
        # in-memory doc AND overwrite the real disk file to match --
        # WITHOUT git add/commit. This is exactly what rev 5's
        # memory-vs-disk-only check could not catch (both sides agree).
        del authority["instruments"][1]
        clean = {k: v for k, v in authority.items() if not k.startswith("_")}
        self.repo.write_dirty("config/canonical_security_identity.json",
                               json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8"))

        self.assertEqual(self.repo.head_commit(), head_before)  # the real git commit itself is untouched

        tampered = ci.resolve_instrument_by_id("INSTR-DISK-CONFLICT", "2026-06-01", authority)
        self.assertNotEqual(tampered["status"], ci.RESOLVED)
        self.assertIn(tampered["status"], (ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED,
                                            ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED))
        self.assertIsNone(tampered["canonical_instrument_id"])
        _assert_authority_all_false(self, tampered)

    def test_document_tamper_row_deleted_in_memory_and_disk_reloaded_from_disk_still_blocked(self):
        """Same reproduction, but this time re-LOAD the (dirty) disk file
        fresh via ci.load_authority -- proving the block does not depend
        on any particular way memory came to mirror disk."""
        instr_1 = self.ratify(make_instrument("INSTR-DISK-CONFLICT-2", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        instr_2 = self.ratify(make_instrument("INSTR-DISK-CONFLICT-2", "ISSUER-TWO"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instr_1, instr_2])
        path = self.repo.root / "config" / "canonical_security_identity.json"

        doc = json.loads(path.read_text(encoding="utf-8"))
        del doc["instruments"][1]
        self.repo.write_dirty("config/canonical_security_identity.json",
                               json.dumps(doc, ensure_ascii=False, sort_keys=True).encode("utf-8"))

        reloaded = ci.load_authority(path)  # fresh load -- memory now mirrors the dirty disk exactly
        result = ci.resolve_instrument_by_id("INSTR-DISK-CONFLICT-2", "2026-06-01", reloaded)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertIn(result["status"], (ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED,
                                          ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED))

    def test_old_commit_revert_without_committing_blocked(self):
        """'Revert the file to an old real single-row commit and use it as
        if it were current': `git checkout <old-sha> -- <path>` reverts
        the working tree to a real, older commit's real content, WITHOUT
        creating a new commit. Even though the reverted bytes really did
        exist in real git history, using them as if they were CURRENT
        (without an explicit trusted_commit pin) must be blocked."""
        instr_1 = self.ratify(make_instrument("INSTR-REVERT-A", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        self.build(instruments=[instr_1], commit_iso="2026-01-02T00:00:00Z")
        old_commit = self.repo.head_commit()

        instr_2 = self.ratify(make_instrument("INSTR-REVERT-A", "ISSUER-TWO"), ci.LAYER_INSTRUMENT)
        authority_v2 = self.build(instruments=[instr_1, instr_2], commit_iso="2026-01-03T00:00:00Z")

        baseline = ci.resolve_instrument_by_id("INSTR-REVERT-A", "2026-06-01", authority_v2)
        self.assertEqual(baseline["status"], ci.NOT_COMPUTABLE_AMBIGUOUS)

        self.repo.checkout_path_from_commit(old_commit, "config/canonical_security_identity.json")
        reloaded = ci.load_authority(self.repo.root / "config" / "canonical_security_identity.json")

        result = ci.resolve_instrument_by_id("INSTR-REVERT-A", "2026-06-01", reloaded)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertIn(result["status"], (ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED,
                                          ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED))

    def test_explicit_trusted_commit_pin_allows_legitimate_older_commit(self):
        """The flip side: a caller who EXPLICITLY pins the old commit
        (rather than defaulting to current HEAD) is legitimately allowed
        -- disk genuinely matches that specific, real, named commit
        byte-for-byte, which is exactly what an externally-trusted pin is
        for. This is the sanctioned way to use "a different commit", per
        the requirement that it must be passed in explicitly and never
        self-declared inside the input document."""
        issuer = self.ratify(make_issuer("ISSUER-ONE"), ci.LAYER_ISSUER)
        instr_1 = self.ratify(make_instrument("INSTR-PIN-A", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        self.build(issuers=[issuer], instruments=[instr_1], commit_iso="2026-01-02T00:00:00Z")
        old_commit = self.repo.head_commit()

        instr_2 = self.ratify(make_instrument("INSTR-PIN-B", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        self.build(issuers=[issuer], instruments=[instr_1, instr_2], commit_iso="2026-01-03T00:00:00Z")

        self.repo.checkout_path_from_commit(old_commit, "config/canonical_security_identity.json")
        reloaded = ci.load_authority(self.repo.root / "config" / "canonical_security_identity.json")

        # without a pin -- blocked (dirty relative to current HEAD)
        unpinned = ci.resolve_instrument_by_id("INSTR-PIN-A", "2026-06-01", reloaded)
        self.assertNotEqual(unpinned["status"], ci.RESOLVED)

        # with an explicit pin to the commit disk actually reflects -- allowed
        pinned = ci.resolve_instrument_by_id("INSTR-PIN-A", "2026-06-01", reloaded, trusted_commit=old_commit)
        self.assertEqual(pinned["status"], ci.RESOLVED)

    def test_explicit_trusted_commit_pin_still_rejects_disk_mismatch(self):
        """Pinning a commit is not a blanket bypass -- disk must still
        genuinely match THAT commit's real bytes. Pinning a commit whose
        real content does not match the current (dirty) disk state is
        still blocked."""
        instr_1 = self.ratify(make_instrument("INSTR-PIN-MISMATCH", "ISSUER-ONE"), ci.LAYER_INSTRUMENT)
        authority = self.build(instruments=[instr_1], commit_iso="2026-01-02T00:00:00Z")
        real_commit = self.repo.head_commit()

        del authority["instruments"][0]
        authority["instruments"].append(make_instrument("INSTR-FABRICATED", "ISSUER-X"))
        clean = {k: v for k, v in authority.items() if not k.startswith("_")}
        self.repo.write_dirty("config/canonical_security_identity.json",
                               json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8"))

        result = ci.resolve_instrument_by_id("INSTR-PIN-MISMATCH", "2026-06-01", authority, trusted_commit=real_commit)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_direct_disk_edit_single_row_no_ambiguity_still_blocked(self):
        """Simpler variant without an ambiguity baseline: a single-row
        document, directly edited on disk (business field tamper via disk,
        mirrored to memory) -- no new commit. Must not resolve."""
        issuer = self.ratify(make_issuer("ISSUER-X"), ci.LAYER_ISSUER)
        instr = self.ratify(make_instrument("INSTR-DIRECT-EDIT", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instr])

        baseline = ci.resolve_instrument_by_id("INSTR-DIRECT-EDIT", "2026-06-01", authority)
        self.assertEqual(baseline["status"], ci.RESOLVED)

        authority["instruments"][0]["instrument_type"] = "PREFERRED_STOCK"  # tamper
        clean = {k: v for k, v in authority.items() if not k.startswith("_")}
        self.repo.write_dirty("config/canonical_security_identity.json",
                               json.dumps(clean, ensure_ascii=False, sort_keys=True).encode("utf-8"))

        result = ci.resolve_instrument_by_id("INSTR-DIRECT-EDIT", "2026-06-01", authority)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertIn(result["status"], (ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED,
                                          ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED))

    def test_document_not_inside_any_git_repo_is_provenance_unverified(self):
        """A `_source_path` pointing at a real file that simply isn't
        tracked by any git repository at all -- structurally can't be
        trusted, correctly distinct from an active tamper finding."""
        plain_dir = self.tmp_path / "no_git_here"
        plain_dir.mkdir()
        instr = self.ratify(make_instrument("INSTR-NO-GIT", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        doc = full_authority(instruments=[instr])
        path = plain_dir / "canonical_security_identity.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        loaded = ci.load_authority(path)
        result = ci.resolve_instrument_by_id("INSTR-NO-GIT", "2026-06-01", loaded)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED)

    def test_untampered_document_still_resolves_with_disk_git_check(self):
        """Sanity/positive control: a genuinely untouched, freshly-loaded
        document passes the full three-way check and resolves normally."""
        issuer = self.ratify(make_issuer("ISSUER-X"), ci.LAYER_ISSUER)
        instr = self.ratify(make_instrument("INSTR-CLEAN-V6", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instr])
        ok, reason = ci.verify_document_matches_source(authority)
        self.assertTrue(ok)
        self.assertIsNone(reason)
        result = ci.resolve_instrument_by_id("INSTR-CLEAN-V6", "2026-06-01", authority)
        self.assertEqual(result["status"], ci.RESOLVED)


# ---------------------------------------------------------------------------
# Round-6 (rev 7) counter-examples -- CIO independent re-verification of
# HEAD e595ac7: the default-HEAD-mode co-tamper block was confirmed
# correct; 2 narrower contract mismatches remained in the EXPLICIT-PIN
# path only.
#
#   1. disk<->trusted-commit was a canonical-JSON-hash comparison, not
#      byte-for-byte -- a whitespace-only disk edit still passed.
#   2. trusted_commit accepted mutable rev-expressions (HEAD, a branch,
#      a tag, HEAD~1, an abbreviated SHA), not just an immutable full SHA.
# ---------------------------------------------------------------------------

class Defect9PinPathByteAndImmutabilityTests(_GitRepoMixin, unittest.TestCase):

    def test_whitespace_only_disk_edit_rejected_under_explicit_pin(self):
        """CIO's exact reproduction: a file that's byte-different
        (whitespace-only) from the pinned blob but semantically/
        canonically equivalent must be rejected."""
        issuer = self.ratify(make_issuer("ISSUER-WS"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-WS", "ISSUER-WS"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        pinned_commit = self.repo.head_commit()

        path = self.repo.root / "config" / "canonical_security_identity.json"
        original_bytes = path.read_bytes()
        doc = json.loads(original_bytes)
        # canonically-identical re-serialization with different whitespace/indent
        whitespace_bytes = json.dumps(doc, ensure_ascii=False, sort_keys=True, indent=2).encode("utf-8")
        self.assertNotEqual(original_bytes, whitespace_bytes)  # genuinely byte-different
        self.assertEqual(ci.payload_sha256(json.loads(original_bytes)),
                          ci.payload_sha256(json.loads(whitespace_bytes)))  # canonically identical

        self.repo.write_dirty("config/canonical_security_identity.json", whitespace_bytes)
        reloaded = ci.load_authority(path)  # memory now mirrors the (whitespace-tampered) disk exactly

        ok, reason = ci.verify_document_matches_source(reloaded, trusted_commit=pinned_commit)
        self.assertFalse(ok)
        self.assertEqual(reason, "DISK_COMMIT_MISMATCH")

        result = ci.resolve_instrument_by_id("INSTR-WS", "2026-06-01", reloaded, trusted_commit=pinned_commit)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_TAMPERED)

    def test_trusted_commit_head_literal_rejected(self):
        issuer = self.ratify(make_issuer("ISSUER-HEADLIT"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-HEADLIT", "ISSUER-HEADLIT"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        result = ci.resolve_instrument_by_id("INSTR-HEADLIT", "2026-06-01", authority, trusted_commit="HEAD")
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED)

    def test_trusted_commit_branch_name_rejected(self):
        issuer = self.ratify(make_issuer("ISSUER-BRANCH"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-BRANCH", "ISSUER-BRANCH"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=self.repo.root,
                                 capture_output=True, text=True, check=True).stdout.strip()
        result = ci.resolve_instrument_by_id("INSTR-BRANCH", "2026-06-01", authority, trusted_commit=branch)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED)

    def test_trusted_commit_tag_rejected(self):
        issuer = self.ratify(make_issuer("ISSUER-TAG"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-TAG", "ISSUER-TAG"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        subprocess.run(["git", "tag", "v1-test"], cwd=self.repo.root, capture_output=True, text=True, check=True)
        result = ci.resolve_instrument_by_id("INSTR-TAG", "2026-06-01", authority, trusted_commit="v1-test")
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED)

    def test_trusted_commit_relative_ref_rejected(self):
        issuer = self.ratify(make_issuer("ISSUER-REL"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-REL", "ISSUER-REL"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        result = ci.resolve_instrument_by_id("INSTR-REL", "2026-06-01", authority, trusted_commit="HEAD~1")
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED)

    def test_trusted_commit_abbreviated_sha_rejected(self):
        issuer = self.ratify(make_issuer("ISSUER-ABBR"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-ABBR", "ISSUER-ABBR"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        abbreviated = self.repo.head_commit()[:8]
        result = ci.resolve_instrument_by_id("INSTR-ABBR", "2026-06-01", authority, trusted_commit=abbreviated)
        self.assertNotEqual(result["status"], ci.RESOLVED)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED)

    def test_trusted_commit_exact_full_sha_positive_control(self):
        """An exact full SHA with an exact matching blob still passes."""
        issuer = self.ratify(make_issuer("ISSUER-EXACTSHA"), ci.LAYER_ISSUER)
        instrument = self.ratify(make_instrument("INSTR-EXACTSHA", "ISSUER-EXACTSHA"), ci.LAYER_INSTRUMENT)
        authority = self.build(issuers=[issuer], instruments=[instrument])
        full_sha = self.repo.head_commit()
        self.assertRegex(full_sha, r"^[0-9a-f]{40}$")
        result = ci.resolve_instrument_by_id("INSTR-EXACTSHA", "2026-06-01", authority, trusted_commit=full_sha)
        self.assertEqual(result["status"], ci.RESOLVED)

    def test_is_pinned_immutable_commit_direct(self):
        """Direct, low-level proof of the immutability gate."""
        issuer = self.ratify(make_issuer("ISSUER-DIRECT"), ci.LAYER_ISSUER)
        self.build(issuers=[issuer])
        repo_root = self.repo.root
        full_sha = self.repo.head_commit()
        self.assertTrue(ci._is_pinned_immutable_commit(repo_root, full_sha))
        self.assertFalse(ci._is_pinned_immutable_commit(repo_root, "HEAD"))
        self.assertFalse(ci._is_pinned_immutable_commit(repo_root, "HEAD~1"))
        self.assertFalse(ci._is_pinned_immutable_commit(repo_root, full_sha[:10]))
        self.assertFalse(ci._is_pinned_immutable_commit(repo_root, "not-a-real-ref"))


# ---------------------------------------------------------------------------
# Structural / validation coverage
# ---------------------------------------------------------------------------

class StructuralValidationTests(_GitRepoMixin, unittest.TestCase):

    def test_provisional_row_rejects_ratified_at(self):
        row = make_source_alias("SRC", "X", "L")
        row["ratified_at"] = "2026-01-01T00:00:00Z"
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_ratified_row_requires_business_payload_and_evidence_fields(self):
        row = make_source_alias("SRC", "X", "L")
        row["approval_status"] = "RATIFIED"
        row["ratified_at"] = "2026-01-01T00:00:00Z"
        with self.assertRaises(ci.IdentityError):
            ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)

    def test_valid_ratified_row_passes_structural_validation(self):
        row = self.ratify(make_source_alias("SRC", "X", "L"), ci.LAYER_SOURCE_ALIAS)
        ci.validate_authority_row(row, ci.LAYER_SOURCE_ALIAS)  # must not raise

    def test_resolver_hard_fails_on_malformed_injected_row(self):
        malformed = make_instrument("INSTR-X", "ISSUER-X")
        del malformed["instrument_type"]
        authority = full_authority(instruments=[malformed])
        with self.assertRaises(ci.IdentityError):
            ci.resolve_instrument_by_id("INSTR-X", "2026-06-01", authority)

    def test_correctly_signed_row_verifies_business_payload_and_evidence(self):
        row = self.ratify(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        self.assertTrue(ci.verify_business_payload(row, ci.LAYER_INSTRUMENT))
        self.assertTrue(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_provisional_row_never_verifies(self):
        row = make_instrument("INSTR-X", "ISSUER-X")
        self.assertFalse(ci.verify_business_payload(row, ci.LAYER_INSTRUMENT))
        self.assertFalse(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_evidence_file_missing_is_unverified_even_with_matching_hash_claim(self):
        row = self.ratify(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        os.remove(row["approval_evidence_ref"])
        self.assertFalse(ci.verify_approval_evidence(row, ci.LAYER_INSTRUMENT))

    def test_evidence_file_content_mismatch_rejected(self):
        row = self.ratify(make_instrument("INSTR-X", "ISSUER-X"), ci.LAYER_INSTRUMENT)
        other = self.ratify(make_instrument("INSTR-OTHER", "ISSUER-OTHER"), ci.LAYER_INSTRUMENT)
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
# The real shipped authority files must stay within the approved narrow set.
# ---------------------------------------------------------------------------

class RealShippedAuthorityFilesHaveNarrowPilotTests(unittest.TestCase):

    def test_real_canonical_security_identity_file_has_only_four_narrow_chains(self):
        doc = ci.load_authority()
        self.assertEqual(len(doc["issuers"]), 4)
        self.assertEqual(len(doc["instruments"]), 4)
        self.assertEqual(len(doc["listings"]), 4)
        self.assertEqual(len(doc["source_aliases"]), 4)
        self.assertEqual(
            {row["canonical_instrument_id"] for row in doc["instruments"]},
            {
                "CRYPTO:BTC", "KRX:005930:COMMON", "KRX:000660:COMMON",
                "KRX:071050:COMMON",
            },
        )
        for layer_key in ("issuers", "instruments", "listings", "source_aliases"):
            self.assertTrue(all(row["approval_status"] == "RATIFIED" for row in doc[layer_key]))

    def test_real_market_account_scope_map_has_only_three_pilot_edges(self):
        doc = ci.load_scope_authority()
        self.assertEqual(
            {(row["market"], row["account_scope"]) for row in doc["edges"]},
            {("BTC", "CRYPTO"), ("CRYPTO", "CRYPTO"), ("KOREA", "KOREA")},
        )
        self.assertTrue(all(row["approval_status"] == "RATIFIED" for row in doc["edges"]))

    def test_unlisted_real_queries_remain_not_computable(self):
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
        result = ci.resolve_account_scope("US", "2026-08-26", scope_doc)
        self.assertEqual(result["status"], ci.NOT_COMPUTABLE_SCOPE_MAP_MISSING)
        _assert_authority_all_false(self, result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
