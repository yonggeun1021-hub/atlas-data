#!/usr/bin/env python3
"""Crypto breadth taxonomy: the five assets the headroom slice deferred.

The 2026-09-18 headroom slice (PR #813 lineage) stopped at rank 158 and
said why: `STORJ` (min rank 159), `MET` (161), `RIVER` (166), `DENT`
(167) and `GALA` (168) sat inside its own block, but two independent
official sources could not be obtained inside that batch, so the slice
wrote neither an `eligible_crypto` nor an `unverified_identity` record
for them.  That refusal was correct -- a speculative inclusion fabricates
an identity and a speculative exclusion fabricates an exclusion -- and it
left the block one rank short of rank 159.

This batch resolves all five on evidence, effective 2026-09-18:

- eligible_crypto: DENT GALA MET RIVER STORJ

Four of the five are bound to an exact on-chain identifier published by
the project itself: the STORJ ERC-20 contract on storj.dev, the MET SPL
mint in Meteora's documentation, the RIVER token address (BNB Chain,
Ethereum, Base) in River's documentation, and -- the strongest case in
the batch -- the GALA v2 Ethereum contract published identically by
Kraken's migration notice and by Gala's own Help Center.

`DENT` is deliberately recorded as a chain-level identity only, and the
record says so.  Kraken publishes Dent on Ethereum (ERC-20); the
project's own DENTNet documentation independently publishes the DENT
token as the core of the mobile data ecosystem with a fixed 100B supply,
deposited into DENTNet from ERC20 wallets on the Ethereum network.  No
live official page publishes a contract address: dentwireless.com and
dent-app.com now redirect to the successor brand Tunz.  Two independent
organisations agree on ticker, chain and platform, so the asset is not an
identity failure; but no exact-contract claim is made, and the test below
holds that disclosure in place so it cannot quietly be upgraded later.

The receipt
`evidence/crypto/identity/crypto_breadth_deferred_five_source_facts_20260918.json`
retains, per asset, the Kraken catalog status, the verdict, the
independent source organisations, every source URL with its retained
content SHA-256 and byte count, and the disambiguating identity finding.
Unlike the two earlier batches, the retained bodies live outside any
session scratchpad -- a sweep deleted the previous 149 MB on 2026-09-18 --
and the receipt discloses exactly that.

These tests are date-independent.  They read only committed artifacts:
the taxonomy, the receipt, and the already-committed raw Kraken snapshots
2026-09-12..2026-09-18.  No live request is made.  Nothing is written
except a temporary counterfactual taxonomy file.  Thresholds, the Top-100
30-day turnover rule and fail-closed TAXONOMY_COVERAGE_UNKNOWN are
unchanged.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
TAXONOMY_PATH = ROOT / "config" / "crypto_breadth_exclusion_taxonomy.json"
RECEIPT_PATH = (
    ROOT / "evidence" / "crypto" / "identity"
    / "crypto_breadth_deferred_five_source_facts_20260918.json"
)


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_deferred_five_20260918", ".github/scripts/crypto_breadth.py")

EFFECTIVE = dt.date(2026, 9, 18)
ELIGIBLE = ("DENT", "GALA", "MET", "RIVER", "STORJ")
SLICE = tuple(sorted(ELIGIBLE))

CATALOG_VINTAGE = dt.date(2026, 9, 18)
RETAINED_VINTAGES = tuple(
    dt.date(2026, 9, 12) + dt.timedelta(days=offset) for offset in range(7)
)

# What this batch buys: 158 -> 171, measured the same drift-robust way.
CONTIGUOUS_THROUGH_RANK = 171
PREVIOUS_CONTIGUOUS_THROUGH_RANK = 158

# The predecessor state, i.e. the taxonomy this batch started from.
TAXONOMY_HASH_BEFORE = (
    "f561fd9d3b65015302dca8a2f311c169686c84e24653da1329b705a39d82243d"
)

# The exact on-chain identifiers the projects publish themselves.  A record
# that stops naming its identifier stops being the record that was reviewed.
PUBLISHED_IDENTIFIERS = {
    "STORJ": "0xB64ef51C888972c908CFacf59B47C1AfBC0Ab8aC",
    "MET": "METvsvVRapdj9cFLzq4Tr43xK4tAjQfwX76z3n6mWQL",
    "RIVER": "0xdA7AD9dea9397cffdDAE2F8a052B82f1484252B3",
    "GALA": "0xd1d2Eb1B1e90B638588728b4130137D262C87cae",
}


def _records(taxonomy: dict, asset_id: str) -> list:
    return [
        record
        for record in taxonomy["records"]
        if record["canonical_asset_id"] == asset_id
    ]


def _taxonomy_without_slice(tmp_dir: Path) -> dict:
    """The committed taxonomy with this batch's records removed."""
    payload = json.loads(TAXONOMY_PATH.read_text())
    payload["records"] = [
        record
        for record in payload["records"]
        if record["canonical_asset_id"] not in SLICE
    ]
    target = tmp_dir / "taxonomy_without_deferred_five.json"
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return CB.load_exclusion_taxonomy(target)


def _unknown_ids(result: dict) -> list:
    return sorted(
        row["canonical_asset_id"]
        for row in result["diagnostics"]["taxonomy_unknown_before_cutoff"]
    )


def _excluded_rows(result: dict) -> list:
    return sorted(
        (row["canonical_asset_id"], row["category"])
        for row in result["diagnostics"]["taxonomy_excluded_before_cutoff"]
    )


def _full_ranking(core: dict, universe: dict) -> list:
    """The complete turnover ranking, including below the scan cutoff.

    `qualified_members` stops at the cutoff by design, so it cannot answer
    "where is the nearest unclassified asset".  This re-derives the same
    ordering the scan uses -- identical filters, identical sort key -- so
    the contiguity claim is measured rather than remembered.
    """
    as_of = core["vintage"] - dt.timedelta(days=1)
    allowed_assets = set(universe["allowed_asset_statuses"])
    allowed_pairs = set(universe["allowed_pair_statuses"])
    ranked = []
    for pair_id in sorted(core["pairs"]):
        pair = core["pairs"][pair_id]
        if (
            pair["quote"] != universe["quote_currency"]
            or pair["status"] not in allowed_pairs
            or core["assets"][pair["base"]]["status"] not in allowed_assets
            or core["assets"][pair["quote"]]["status"] not in allowed_assets
        ):
            continue
        series = core["ohlc"].get(pair_id)
        if series is None or not series["ranking_history_complete"]:
            continue
        ranked.append(
            {
                "canonical_asset_id": CB.canonical_identity(
                    pair["base"], as_of, core["identity"]
                ),
                "pair_id": pair_id,
                "series": series,
            }
        )
    ranked.sort(
        key=lambda item: (
            -item["series"]["trailing_usd_turnover"],
            item["canonical_asset_id"],
            item["pair_id"],
        )
    )
    return ranked


class RecordShapeTest(unittest.TestCase):
    """Every asset carries exactly one record, with the ratified shape."""

    @classmethod
    def setUpClass(cls):
        cls.taxonomy = json.loads(TAXONOMY_PATH.read_text())

    def test_policy_version_and_approval_are_unchanged(self):
        self.assertEqual(
            self.taxonomy["policy_version"], "crypto_breadth_exclusion_taxonomy/v2"
        )
        self.assertEqual(self.taxonomy["approval_status"], "RATIFIED")

    def test_each_batch_asset_has_exactly_one_record(self):
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(len(_records(self.taxonomy, asset_id)), 1)

    def test_every_record_is_eligible_crypto(self):
        for asset_id in ELIGIBLE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    _records(self.taxonomy, asset_id)[0]["category"],
                    self.taxonomy["eligible_category"],
                )

    def test_every_category_used_is_already_ratified(self):
        allowed = set(self.taxonomy["excluded_categories"]) | {
            self.taxonomy["eligible_category"]
        }
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertIn(
                    _records(self.taxonomy, asset_id)[0]["category"], allowed
                )

    def test_records_are_open_ended_and_do_not_backfill(self):
        for asset_id in SLICE:
            record = _records(self.taxonomy, asset_id)[0]
            with self.subTest(asset_id=asset_id):
                self.assertEqual(record["effective_from"], EFFECTIVE.isoformat())
                self.assertIsNone(record["effective_to"])

    def test_every_reason_cites_both_independent_legs(self):
        """A reason that names only Kraken would not meet the bar."""
        for asset_id in SLICE:
            reason = _records(self.taxonomy, asset_id)[0]["reason"]
            with self.subTest(asset_id=asset_id):
                self.assertIn("Kraken", reason)
                self.assertIn("independently", reason)
                self.assertGreater(len(reason), 120)

    def test_exact_identifier_records_name_their_identifier(self):
        for asset_id, identifier in PUBLISHED_IDENTIFIERS.items():
            reason = _records(self.taxonomy, asset_id)[0]["reason"]
            with self.subTest(asset_id=asset_id):
                self.assertIn(identifier, reason)

    def test_dent_record_keeps_its_chain_level_only_disclosure(self):
        """DENT has no published contract; the record must keep saying so."""
        reason = _records(self.taxonomy, "DENT")[0]["reason"]
        self.assertNotIn("DENT", PUBLISHED_IDENTIFIERS)
        for phrase in ("no exact-contract", "chain-level identity"):
            self.assertIn(phrase, reason)


class ReceiptBindsTaxonomyTest(unittest.TestCase):
    """The receipt and the taxonomy cannot drift apart."""

    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(RECEIPT_PATH.read_text())
        cls.taxonomy = json.loads(TAXONOMY_PATH.read_text())

    def test_receipt_covers_exactly_the_batch(self):
        self.assertEqual(
            sorted(item["canonical_asset_id"] for item in self.receipt["assets"]),
            list(SLICE),
        )

    def test_every_receipt_verdict_matches_the_taxonomy_category(self):
        for item in self.receipt["assets"]:
            asset_id = item["canonical_asset_id"]
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    item["verdict"],
                    _records(self.taxonomy, asset_id)[0]["category"],
                )

    def test_receipt_pins_the_predecessor_taxonomy_hash(self):
        self.assertEqual(self.receipt["taxonomy_hash_before"], TAXONOMY_HASH_BEFORE)

    def test_every_asset_names_at_least_two_independent_organisations(self):
        for item in self.receipt["assets"]:
            with self.subTest(asset_id=item["canonical_asset_id"]):
                orgs = item["independent_source_organisations"]
                self.assertGreaterEqual(len(set(orgs)), 2)
                self.assertIn("kraken", orgs)

    def test_every_source_carries_a_url_hash_and_byte_count(self):
        for item in self.receipt["assets"]:
            for source in item["sources"]:
                with self.subTest(
                    asset_id=item["canonical_asset_id"], url=source["url"]
                ):
                    self.assertTrue(source["url"].startswith("https://"))
                    self.assertRegex(
                        source["retained_content_sha256"], r"^[0-9a-f]{64}$"
                    )
                    self.assertGreater(source["retained_bytes"], 0)
                    self.assertTrue(source["role"])

    def test_every_asset_has_a_non_kraken_source(self):
        """Ticker text on the venue alone was never the bar."""
        for item in self.receipt["assets"]:
            hosts = {source["url"].split("/")[2] for source in item["sources"]}
            with self.subTest(asset_id=item["canonical_asset_id"]):
                self.assertTrue(
                    any(not host.endswith("kraken.com") for host in hosts),
                    hosts,
                )

    def test_every_asset_records_an_identity_finding(self):
        for item in self.receipt["assets"]:
            with self.subTest(asset_id=item["canonical_asset_id"]):
                self.assertGreater(len(item["identity_finding"]), 150)

    def test_findings_name_the_published_identifier_where_one_exists(self):
        findings = {
            item["canonical_asset_id"]: item["identity_finding"]
            for item in self.receipt["assets"]
        }
        for asset_id, identifier in PUBLISHED_IDENTIFIERS.items():
            with self.subTest(asset_id=asset_id):
                self.assertIn(identifier, findings[asset_id])

    def test_dent_finding_discloses_the_missing_contract_publication(self):
        finding = next(
            item["identity_finding"]
            for item in self.receipt["assets"]
            if item["canonical_asset_id"] == "DENT"
        )
        self.assertIn("DISCLOSED LIMIT", finding)
        self.assertIn("no exact-contract claim", finding)

    def test_receipt_claims_no_authority_beyond_source_coverage(self):
        scope = self.receipt["scope"]
        for phrase in ("not investability", "Regime", "trading"):
            self.assertIn(phrase, scope)

    def test_receipt_discloses_where_the_retained_bodies_live(self):
        """The weakness that fired on 2026-09-18 must stay visible.

        A sweep deleted the earlier batches' bodies from the session
        scratchpad.  This batch's bodies are outside it, and the receipt
        has to say both facts rather than quietly claiming the problem is
        solved for everyone.
        """
        disclosure = self.receipt["retained_source_body_disclosure"]
        self.assertIn("OUTSIDE any session scratchpad", disclosure)
        self.assertIn("149 MB", disclosure)
        self.assertIn("not committed", disclosure)


class KrakenCatalogLegTest(unittest.TestCase):
    """The Kraken leg is re-derived from committed bytes, not re-requested."""

    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(RECEIPT_PATH.read_text())
        snapshot = RAW_ROOT / CATALOG_VINTAGE.isoformat()
        cls.snapshot = snapshot
        cls.assets = json.loads(
            gzip.decompress((snapshot / "kraken_assets.json.gz").read_bytes())
        )["result"]
        cls.pairs = json.loads(
            gzip.decompress((snapshot / "kraken_asset_pairs.json.gz").read_bytes())
        )["result"]

    def test_receipt_pins_the_exact_retained_kraken_catalog_bytes(self):
        evidence = self.receipt["kraken_catalog_evidence"]
        self.assertEqual(
            evidence["snapshot_dir"],
            f"evidence/crypto/breadth/raw/{CATALOG_VINTAGE.isoformat()}",
        )
        self.assertEqual(
            evidence["assets_gz_sha256"],
            hashlib.sha256(
                (self.snapshot / "kraken_assets.json.gz").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            evidence["asset_pairs_gz_sha256"],
            hashlib.sha256(
                (self.snapshot / "kraken_asset_pairs.json.gz").read_bytes()
            ).hexdigest(),
        )

    def test_every_asset_is_enabled_with_exactly_one_online_usd_pair(self):
        for item in self.receipt["assets"]:
            asset_id = item["canonical_asset_id"]
            with self.subTest(asset_id=asset_id):
                self.assertEqual(self.assets[asset_id]["status"], "enabled")
                online = [
                    pair_id
                    for pair_id, pair in self.pairs.items()
                    if pair.get("base") == asset_id
                    and pair.get("quote") == "USD"
                    and pair.get("status") == "online"
                ]
                self.assertEqual(online, [f"{asset_id}/USD"])

    def test_receipt_catalog_claims_match_the_committed_catalog(self):
        for item in self.receipt["assets"]:
            asset_id = item["canonical_asset_id"]
            catalog = item["kraken_catalog"]
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    catalog["asset_status"], self.assets[asset_id]["status"]
                )
                self.assertEqual(catalog["usd_pair_id"], f"{asset_id}/USD")
                self.assertEqual(
                    catalog["usd_pair_status"],
                    self.pairs[f"{asset_id}/USD"]["status"],
                )


class RetainedSnapshotResultsUnchangedTest(unittest.TestCase):
    """Effective 2026-09-18 means no retained vintage moves.

    Every committed snapshot 2026-09-12..2026-09-18 evaluates at
    as_of = vintage - 1 day, so the latest retained as_of is 2026-09-17 --
    strictly before this batch's effective date.  Adding or removing the
    five records must therefore leave every retained result identical.
    """

    @classmethod
    def setUpClass(cls):
        cls.contract = CB.load_contract()
        cls.universe = CB.load_universe_policy()
        cls.current = CB.load_exclusion_taxonomy()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.without = _taxonomy_without_slice(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_latest_retained_as_of_is_before_the_effective_date(self):
        latest = max(RETAINED_VINTAGES) - dt.timedelta(days=1)
        self.assertLess(latest, EFFECTIVE)

    def test_results_identical_with_and_without_the_batch(self):
        for vintage in RETAINED_VINTAGES:
            snapshot = RAW_ROOT / vintage.isoformat()
            with self.subTest(vintage=vintage.isoformat()):
                self.assertTrue(snapshot.is_dir(), snapshot)
                core = CB.source_core(snapshot, self.contract)
                before = CB.qualified_members(core, self.universe, self.without)
                after = CB.qualified_members(core, self.universe, self.current)
                self.assertEqual(after["status"], before["status"])
                self.assertEqual(after["reason"], before["reason"])
                self.assertEqual(_unknown_ids(after), _unknown_ids(before))
                self.assertEqual(_excluded_rows(after), _excluded_rows(before))
                self.assertEqual(
                    [item["canonical_asset_id"] for item in after["members"]],
                    [item["canonical_asset_id"] for item in before["members"]],
                )

    def test_no_batch_asset_is_inside_any_retained_scanned_range(self):
        """This batch is headroom, not a live blocker."""
        for vintage in RETAINED_VINTAGES:
            core = CB.source_core(RAW_ROOT / vintage.isoformat(), self.contract)
            result = CB.qualified_members(core, self.universe, self.current)
            scanned = (
                {item["canonical_asset_id"] for item in result["members"]}
                | {row[0] for row in _excluded_rows(result)}
                | set(_unknown_ids(result))
            )
            with self.subTest(vintage=vintage.isoformat()):
                self.assertEqual(sorted(scanned & set(SLICE)), [])


class ReDerivedContiguityTest(unittest.TestCase):
    """The claim this batch makes, measured rather than asserted.

    Each asset's *minimum* rank across every retained vintage decides the
    block: if no unclassified asset has ever been seen at or below
    CONTIGUOUS_THROUGH_RANK, one day of turnover drift cannot open a
    TAXONOMY_COVERAGE_UNKNOWN hole underneath it.
    """

    @classmethod
    def setUpClass(cls):
        cls.contract = CB.load_contract()
        cls.universe = CB.load_universe_policy()
        cls.current = CB.load_exclusion_taxonomy()
        cls.min_rank = {}
        for vintage in RETAINED_VINTAGES:
            core = CB.source_core(RAW_ROOT / vintage.isoformat(), cls.contract)
            for rank, item in enumerate(_full_ranking(core, cls.universe), start=1):
                asset_id = item["canonical_asset_id"]
                previous = cls.min_rank.get(asset_id)
                if previous is None or rank < previous:
                    cls.min_rank[asset_id] = rank

    def _unclassified_min_ranks(self, taxonomy: dict) -> dict:
        return {
            asset_id: rank
            for asset_id, rank in self.min_rank.items()
            if CB.taxonomy_category(asset_id, EFFECTIVE, taxonomy) is None
        }

    def test_every_batch_asset_was_actually_in_the_block(self):
        """No record was written for an asset the ranking never reached."""
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertIn(asset_id, self.min_rank)
                self.assertLessEqual(
                    self.min_rank[asset_id], CONTIGUOUS_THROUGH_RANK
                )

    def test_block_is_contiguous_through_the_declared_rank(self):
        remaining = self._unclassified_min_ranks(self.current)
        offenders = sorted(
            (rank, asset_id)
            for asset_id, rank in remaining.items()
            if rank <= CONTIGUOUS_THROUGH_RANK
        )
        self.assertEqual(offenders, [], f"hole inside the block: {offenders}")

    def test_the_batch_is_what_extends_the_block_past_the_old_limit(self):
        """Without these records the block stops where the slice stopped."""
        with tempfile.TemporaryDirectory() as tmp:
            without = _taxonomy_without_slice(Path(tmp))
            remaining = self._unclassified_min_ranks(without)
        holes = sorted(
            (rank, asset_id)
            for asset_id, rank in remaining.items()
            if rank <= CONTIGUOUS_THROUGH_RANK
        )
        self.assertNotEqual(holes, [])
        self.assertTrue({asset_id for _, asset_id in holes} <= set(SLICE), holes)
        self.assertEqual(
            min(rank for rank, _ in holes), PREVIOUS_CONTIGUOUS_THROUGH_RANK + 1
        )

    def test_the_declared_rank_is_exactly_one_below_the_next_unclassified(self):
        """The number is derived, not a hardcoded wish."""
        remaining = self._unclassified_min_ranks(self.current)
        self.assertEqual(
            min(remaining.values()), CONTIGUOUS_THROUGH_RANK + 1, sorted(
                (rank, asset_id) for asset_id, rank in remaining.items()
            )[:5]
        )


class HeadroomCounterfactualTest(unittest.TestCase):
    """What the records buy: a resolved category instead of an unknown."""

    @classmethod
    def setUpClass(cls):
        cls.current = CB.load_exclusion_taxonomy()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.without = _taxonomy_without_slice(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_without_the_records_every_batch_asset_is_an_unknown(self):
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, EFFECTIVE, self.without)
                )

    def test_with_the_records_no_batch_asset_can_trigger_unknown(self):
        ratified = set(self.current["excluded_categories"]) | {
            self.current["eligible_category"]
        }
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                category = CB.taxonomy_category(asset_id, EFFECTIVE, self.current)
                self.assertIn(category, ratified)

    def test_records_do_not_apply_before_their_effective_date(self):
        day_before = EFFECTIVE - dt.timedelta(days=1)
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, day_before, self.current)
                )


if __name__ == "__main__":
    unittest.main()
