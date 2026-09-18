#!/usr/bin/env python3
"""Crypto breadth taxonomy: the 2026-09-18 headroom slice (second batch).

PR #809 classified the 14-asset band immediately above the 2026-09-18
eligibility-scan cutoff and moved the nearest unclassified asset from
rank 124 to rank 153 -- a 41-rank margin over the rank-112 cutoff.  This
slice extends that headroom with eleven more assets, effective
2026-09-18:

- eligible_crypto: AR CRO DOS FHE GHST IDOS KSM TRAC TRUST ZIG
- stablecoin:      AUSD

The block was re-derived, not copied from a snapshot.  Ranks drift day to
day, so a fixed list taken from one vintage develops holes.  Each asset
here was selected by its *minimum* rank across the seven committed
vintages 2026-09-12..2026-09-18, evaluated at the effective date so that
the PR #809 records are already in force.  That is what makes the block
contiguous rather than merely long: after this slice no asset has ever,
in any retained vintage, sat unclassified at a rank at or below 158.

AUSD is not a demotion.  Agora publishes AUSD as a fully reserved
stablecoin, so it lands in the already-ratified `stablecoin` exclusion
category.  Like every excluded category it is skipped by the ranking loop
and never makes a whole result UNKNOWN, and it is not an investability
claim.

Assets whose identity could not be confirmed by two independent official
sources were deliberately left out of this slice entirely rather than
recorded either way -- see the receipt's `verification_bar`.  Writing a
speculative `eligible_crypto` would fabricate an identity; writing a
speculative `unverified_identity` would fabricate an exclusion.  Both are
refused here.

The receipt
`evidence/crypto/identity/crypto_breadth_headroom_source_facts_20260918.json`
retains, per asset, the Kraken catalog status, the verdict, the
independent source organisations, every source URL with its retained
content SHA-256 and byte count, and the disambiguating identity finding.

These tests are date-independent.  They read only committed artifacts:
the taxonomy, the receipt, and the already-committed raw Kraken snapshots
2026-09-12..2026-09-18.  No live Kraken request is made.  Nothing is
written except a temporary counterfactual taxonomy file.  Thresholds, the
Top-100 30-day turnover rule and fail-closed TAXONOMY_COVERAGE_UNKNOWN
are unchanged.
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
    / "crypto_breadth_headroom_source_facts_20260918.json"
)


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_headroom_20260918", ".github/scripts/crypto_breadth.py")

EFFECTIVE = dt.date(2026, 9, 18)
ELIGIBLE = ("AR", "CRO", "DOS", "FHE", "GHST", "IDOS", "KSM", "TRAC", "TRUST", "ZIG")
STABLECOIN = ("AUSD",)
SLICE = tuple(sorted(ELIGIBLE + STABLECOIN))

CATALOG_VINTAGE = dt.date(2026, 9, 18)
RETAINED_VINTAGES = tuple(
    dt.date(2026, 9, 12) + dt.timedelta(days=offset) for offset in range(7)
)

# The whole point of the slice: re-derived, drift-robust contiguity.
CONTIGUOUS_THROUGH_RANK = 158


def _records(taxonomy: dict, asset_id: str) -> list:
    return [
        record
        for record in taxonomy["records"]
        if record["canonical_asset_id"] == asset_id
    ]


def _taxonomy_without_slice(tmp_dir: Path) -> dict:
    """The committed taxonomy with this slice's records removed."""
    payload = json.loads(TAXONOMY_PATH.read_text())
    payload["records"] = [
        record
        for record in payload["records"]
        if record["canonical_asset_id"] not in SLICE
    ]
    target = tmp_dir / "taxonomy_without_headroom_slice.json"
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
    the contiguity claim is measured against the real ranking rather than
    against a remembered snapshot.
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

    def test_each_slice_asset_has_exactly_one_record(self):
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(len(_records(self.taxonomy, asset_id)), 1)

    def test_categories_match_the_declared_split(self):
        for asset_id in ELIGIBLE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    _records(self.taxonomy, asset_id)[0]["category"],
                    self.taxonomy["eligible_category"],
                )
        for asset_id in STABLECOIN:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    _records(self.taxonomy, asset_id)[0]["category"], "stablecoin"
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


class ReceiptBindsTaxonomyTest(unittest.TestCase):
    """The receipt and the taxonomy cannot drift apart."""

    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(RECEIPT_PATH.read_text())
        cls.taxonomy = json.loads(TAXONOMY_PATH.read_text())

    def test_receipt_covers_exactly_the_slice(self):
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
        """The hash PR #809 produced, i.e. the state this slice started from."""
        self.assertEqual(
            self.receipt["taxonomy_hash_before"],
            "ff677d500abfba2e093497de361ac0848b317a725145bb0970fdde8760f099fe",
        )

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
            hosts = {
                source["url"].split("/")[2] for source in item["sources"]
            }
            with self.subTest(asset_id=item["canonical_asset_id"]):
                self.assertTrue(
                    any(not host.endswith("kraken.com") for host in hosts),
                    hosts,
                )

    def test_every_asset_records_an_identity_finding(self):
        for item in self.receipt["assets"]:
            with self.subTest(asset_id=item["canonical_asset_id"]):
                self.assertGreater(len(item["identity_finding"]), 150)

    def test_receipt_claims_no_authority_beyond_source_coverage(self):
        scope = self.receipt["scope"]
        for phrase in ("not investability", "Regime", "trading"):
            self.assertIn(phrase, scope)

    def test_receipt_discloses_the_retained_body_weakness(self):
        """The known open weakness must stay visible, not quietly drop."""
        self.assertIn(
            "scratchpad", self.receipt["retained_source_body_disclosure"]
        )


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
    strictly before this slice's effective date.  Adding or removing the
    eleven records must therefore leave every retained result identical.
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

    def test_results_identical_with_and_without_the_slice(self):
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

    def test_no_slice_asset_is_inside_any_retained_scanned_range(self):
        """The slice is headroom, not a live blocker."""
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
    """The claim this slice actually makes, measured rather than asserted.

    A fixed list copied from one day's snapshot develops holes as turnover
    moves assets around.  So the block is defined by each asset's *minimum*
    rank across every retained vintage: if no unclassified asset has ever
    been seen at or below CONTIGUOUS_THROUGH_RANK, a single day's drift
    cannot open a TAXONOMY_COVERAGE_UNKNOWN hole underneath it.
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

    def test_every_slice_asset_was_actually_in_the_block(self):
        """No record was written for an asset the ranking never reached."""
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertIn(asset_id, self.min_rank)
                self.assertLessEqual(
                    self.min_rank[asset_id], CONTIGUOUS_THROUGH_RANK + 20
                )

    def test_block_is_contiguous_through_the_declared_rank(self):
        remaining = self._unclassified_min_ranks(self.current)
        offenders = sorted(
            (rank, asset_id)
            for asset_id, rank in remaining.items()
            if rank <= CONTIGUOUS_THROUGH_RANK
        )
        self.assertEqual(offenders, [], f"hole inside the block: {offenders}")

    def test_the_slice_is_what_makes_the_block_contiguous(self):
        """Without these records the block has holes -- the counterfactual."""
        with tempfile.TemporaryDirectory() as tmp:
            without = _taxonomy_without_slice(Path(tmp))
            remaining = self._unclassified_min_ranks(without)
        holes = sorted(
            asset_id
            for asset_id, rank in remaining.items()
            if rank <= CONTIGUOUS_THROUGH_RANK
        )
        self.assertNotEqual(holes, [])
        self.assertTrue(set(holes).issubset(set(SLICE)), holes)


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

    def test_without_the_records_every_slice_asset_is_an_unknown(self):
        for asset_id in SLICE:
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, EFFECTIVE, self.without)
                )

    def test_with_the_records_no_slice_asset_can_trigger_unknown(self):
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
