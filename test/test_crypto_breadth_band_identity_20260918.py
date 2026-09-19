#!/usr/bin/env python3
"""Crypto breadth taxonomy: the 2026-09-18 cutoff-band identity slice.

Fourteen Kraken assets sat in the 40-rank band immediately above the
2026-09-18 eligibility-scan cutoff (the scan stopped at rank 112 with
100 eligible + 12 excluded + 0 unknown; the nearest unclassified asset
was at rank 124).  Each is now carried by its own source-identity record
effective 2026-09-18:

- eligible_crypto: BAT CAKE CFG ENS ETC GRT MNT PEAQ SAND SHAPE SHX SN51 VET
- unverified_identity: MOODENG

MOODENG is the fail-closed branch, not a failure: Kraken publishes no
contract/mint/genesis identity for it, no Kraken listing notice or support
article exists, and there is no project or foundation document to match
independently, so no canonical identity could be confirmed by two
independent sources.  As a ratified exclusion it is skipped by the ranking
loop exactly like any other excluded category and never makes a whole
result UNKNOWN.  It is not an investability claim.

The receipt `evidence/crypto/identity/crypto_breadth_band_source_facts_20260918.json`
retains, per asset, the exact source URLs, their retained content SHA-256
values and the disambiguating identity finding.

These tests are date-independent.  They read only committed artifacts: the
taxonomy, the receipt, and the already-committed raw Kraken snapshots
2026-09-12..2026-09-18.  Nothing is written except a temporary
counterfactual taxonomy file.  Thresholds, the Top-100 30-day turnover
rule and fail-closed TAXONOMY_COVERAGE_UNKNOWN are unchanged.
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
    / "crypto_breadth_band_source_facts_20260918.json"
)


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_band_identity_20260918", ".github/scripts/crypto_breadth.py")

EFFECTIVE = dt.date(2026, 9, 18)
ELIGIBLE = (
    "BAT", "CAKE", "CFG", "ENS", "ETC", "GRT", "MNT",
    "PEAQ", "SAND", "SHAPE", "SHX", "SN51", "VET",
)
UNVERIFIED = ("MOODENG",)
BAND = ELIGIBLE + UNVERIFIED

CATALOG_VINTAGE = dt.date(2026, 9, 18)
RETAINED_VINTAGES = [dt.date(2026, 9, day) for day in range(12, 19)]


def _taxonomy_without_band(tmp: Path) -> dict:
    document = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    document["records"] = [
        row for row in document["records"]
        if row["canonical_asset_id"] not in BAND
    ]
    path = tmp / "taxonomy_without_band.json"
    path.write_text(
        json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return CB.load_exclusion_taxonomy(path)


def _unknown_ids(result: dict) -> list:
    return [
        item["canonical_asset_id"]
        for item in result["diagnostics"]["taxonomy_unknown_before_cutoff"]
    ]


def _excluded_rows(result: dict) -> list:
    return [
        (item["canonical_asset_id"], item["category"])
        for item in result["diagnostics"]["taxonomy_excluded_before_cutoff"]
    ]


class BandRecordShapeTest(unittest.TestCase):
    def setUp(self):
        self.policy = CB.load_exclusion_taxonomy()

    def _rows(self, asset_id: str) -> list:
        return [
            row for row in self.policy["records"]
            if row["canonical_asset_id"] == asset_id
        ]

    def test_each_band_asset_has_exactly_one_open_ended_record(self):
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                rows = self._rows(asset_id)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["effective_from"], EFFECTIVE.isoformat())
                self.assertIsNone(rows[0]["effective_to"])

    def test_categories_match_the_recorded_verdicts(self):
        for asset_id in ELIGIBLE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(self._rows(asset_id)[0]["category"], "eligible_crypto")
        for asset_id in UNVERIFIED:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    self._rows(asset_id)[0]["category"], "unverified_identity"
                )

    def test_every_reason_names_kraken_and_is_not_ticker_text_alone(self):
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                reason = self._rows(asset_id)[0]["reason"]
                self.assertIn("Kraken", reason)
                self.assertGreater(len(reason), 120)

    def test_unverified_reason_states_the_two_source_failure(self):
        for asset_id in UNVERIFIED:
            with self.subTest(asset_id=asset_id):
                reason = self._rows(asset_id)[0]["reason"]
                self.assertIn("two independent sources", reason)
                self.assertIn("not an investability claim", reason)

    def test_no_backfill_before_the_effective_date(self):
        day_before = EFFECTIVE - dt.timedelta(days=1)
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, day_before, self.policy)
                )
        for asset_id in ELIGIBLE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    CB.taxonomy_category(asset_id, EFFECTIVE, self.policy),
                    "eligible_crypto",
                )
        for asset_id in UNVERIFIED:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    CB.taxonomy_category(asset_id, EFFECTIVE, self.policy),
                    "unverified_identity",
                )

    def test_unverified_identity_is_a_ratified_exclusion_not_an_unknown(self):
        self.assertIn("unverified_identity", self.policy["excluded_categories"])
        self.assertNotEqual(
            self.policy["eligible_category"], "unverified_identity"
        )

    def test_policy_header_and_universe_thresholds_unchanged(self):
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(
            self.policy["policy_version"], "crypto_breadth_exclusion_taxonomy/v2"
        )
        self.assertEqual(self.policy["approval_status"], "RATIFIED")
        self.assertEqual(self.policy["source_name"], "kraken_spot_market_data")
        self.assertEqual(self.policy["unknown_asset_policy"], "fail_closed_unknown")
        self.assertEqual(
            self.policy["excluded_categories"],
            [
                "commodity_linked", "fiat", "stablecoin",
                "staked", "unverified_identity", "wrapped",
            ],
        )
        universe = CB.load_universe_policy()
        self.assertEqual(universe["target_asset_count"], 100)
        self.assertEqual(universe["minimum_observation_coverage_bps"], 9000)
        self.assertEqual(universe["ranking_lookback_finalized_days"], 30)


class KrakenCatalogLegTest(unittest.TestCase):
    """The Kraken leg of the verification bar, re-checked from committed bytes.

    Every band record claims an exact Kraken identity: an enabled asset in
    the retained Assets catalog with an online USD pair in the retained
    AssetPairs catalog.  That claim is verified here against the committed
    2026-09-18 snapshot, not against any live request.
    """

    @classmethod
    def setUpClass(cls):
        snapshot = RAW_ROOT / CATALOG_VINTAGE.isoformat()
        cls.assets_bytes = (snapshot / "kraken_assets.json.gz").read_bytes()
        cls.pairs_bytes = (snapshot / "kraken_asset_pairs.json.gz").read_bytes()
        cls.assets = json.loads(gzip.decompress(cls.assets_bytes))["result"]
        cls.pairs = json.loads(gzip.decompress(cls.pairs_bytes))["result"]

    def test_every_band_asset_is_enabled_in_the_retained_catalog(self):
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                self.assertIn(asset_id, self.assets)
                self.assertEqual(self.assets[asset_id]["status"], "enabled")

    def test_every_band_asset_has_exactly_one_online_usd_pair(self):
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                matches = [
                    key for key, row in self.pairs.items()
                    if row.get("base") == asset_id and row.get("quote") == "USD"
                ]
                self.assertEqual(matches, [f"{asset_id}/USD"])
                self.assertEqual(self.pairs[matches[0]]["status"], "online")


class SourceFactReceiptTest(unittest.TestCase):
    """The committed receipt and the taxonomy must not drift apart."""

    @classmethod
    def setUpClass(cls):
        cls.receipt = json.loads(RECEIPT_PATH.read_text(encoding="utf-8"))
        cls.policy = CB.load_exclusion_taxonomy()

    def test_receipt_covers_exactly_the_band(self):
        ids = [row["canonical_asset_id"] for row in self.receipt["assets"]]
        self.assertEqual(sorted(ids), sorted(BAND))
        self.assertEqual(ids, sorted(ids))

    def test_receipt_verdicts_match_the_taxonomy_categories(self):
        by_id = {
            row["canonical_asset_id"]: row["category"]
            for row in self.policy["records"]
            if row["canonical_asset_id"] in BAND
        }
        for row in self.receipt["assets"]:
            with self.subTest(asset_id=row["canonical_asset_id"]):
                self.assertEqual(row["verdict"], by_id[row["canonical_asset_id"]])

    def test_every_eligible_asset_cites_two_independent_organisations(self):
        for row in self.receipt["assets"]:
            if row["verdict"] != "eligible_crypto":
                continue
            with self.subTest(asset_id=row["canonical_asset_id"]):
                self.assertGreaterEqual(
                    len(row["independent_source_organisations"]), 2,
                    row["canonical_asset_id"],
                )
                roles = {source["role"] for source in row["sources"]}
                self.assertTrue(
                    any(role.startswith("kraken") for role in roles), roles
                )
                self.assertTrue(
                    any(role.startswith("project") for role in roles), roles
                )

    def test_unverified_asset_records_a_single_organisation_only(self):
        for row in self.receipt["assets"]:
            if row["verdict"] != "unverified_identity":
                continue
            with self.subTest(asset_id=row["canonical_asset_id"]):
                self.assertEqual(row["independent_source_organisations"], ["kraken"])
                self.assertIn("FAIL-CLOSED", row["identity_finding"])

    def test_every_source_retains_a_content_hash(self):
        for row in self.receipt["assets"]:
            for source in row["sources"]:
                with self.subTest(url=source["url"]):
                    self.assertRegex(
                        source["retained_content_sha256"], r"^[0-9a-f]{64}$"
                    )
                    self.assertGreater(source["retained_bytes"], 0)

    def test_receipt_pins_the_exact_retained_kraken_catalog_bytes(self):
        snapshot = RAW_ROOT / CATALOG_VINTAGE.isoformat()
        evidence = self.receipt["kraken_catalog_evidence"]
        self.assertEqual(
            evidence["snapshot_dir"],
            f"evidence/crypto/breadth/raw/{CATALOG_VINTAGE.isoformat()}",
        )
        self.assertEqual(
            evidence["assets_gz_sha256"],
            hashlib.sha256((snapshot / "kraken_assets.json.gz").read_bytes()).hexdigest(),
        )
        self.assertEqual(
            evidence["asset_pairs_gz_sha256"],
            hashlib.sha256(
                (snapshot / "kraken_asset_pairs.json.gz").read_bytes()
            ).hexdigest(),
        )

    def test_receipt_claims_no_authority_beyond_source_coverage(self):
        scope = self.receipt["scope"]
        for phrase in ("not investability", "Regime", "trading"):
            self.assertIn(phrase, scope)


class RetainedSnapshotResultsUnchangedTest(unittest.TestCase):
    """Effective 2026-09-18 means no retained vintage moves.

    Every committed snapshot 2026-09-12..2026-09-18 evaluates at
    as_of = vintage - 1 day, so the latest retained as_of is 2026-09-17 --
    strictly before the band's effective date.  Adding or removing the
    fourteen records must therefore leave every retained result identical.
    """

    @classmethod
    def setUpClass(cls):
        cls.contract = CB.load_contract()
        cls.universe = CB.load_universe_policy()
        cls.current = CB.load_exclusion_taxonomy()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.without = _taxonomy_without_band(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_latest_retained_as_of_is_before_the_effective_date(self):
        latest = max(RETAINED_VINTAGES) - dt.timedelta(days=1)
        self.assertLess(latest, EFFECTIVE)

    def test_results_identical_with_and_without_the_band_records(self):
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

    def test_band_assets_are_below_the_cutoff_in_every_retained_vintage(self):
        """The band is headroom, not a live blocker.

        None of the fourteen appears in the scanned range of any retained
        vintage, which is exactly why 2026-09-18 reports zero unknowns while
        the taxonomy below rank ~113 is still largely empty.
        """
        for vintage in RETAINED_VINTAGES:
            core = CB.source_core(RAW_ROOT / vintage.isoformat(), self.contract)
            result = CB.qualified_members(core, self.universe, self.current)
            scanned = (
                {item["canonical_asset_id"] for item in result["members"]}
                | {row[0] for row in _excluded_rows(result)}
                | set(_unknown_ids(result))
            )
            with self.subTest(vintage=vintage.isoformat()):
                self.assertEqual(sorted(scanned & set(BAND)), [])

    def test_2026_09_18_is_taxonomy_complete_with_a_full_top_100(self):
        core = CB.source_core(RAW_ROOT / "2026-09-18", self.contract)
        result = CB.qualified_members(core, self.universe, self.current)
        self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
        self.assertEqual(_unknown_ids(result), [])
        self.assertEqual(len(result["members"]), 100)


class HeadroomCounterfactualTest(unittest.TestCase):
    """What the band records actually buy.

    A cutoff-band asset only matters when turnover lifts it into the scanned
    range.  This isolates that: with the record present the asset resolves to
    a ratified category at the first as_of on or after the effective date;
    without it the same asset is `None`, which is precisely the
    TAXONOMY_COVERAGE_UNKNOWN trigger the scan fails closed on.
    """

    @classmethod
    def setUpClass(cls):
        cls.current = CB.load_exclusion_taxonomy()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.without = _taxonomy_without_band(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_without_the_records_every_band_asset_is_an_unknown(self):
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, EFFECTIVE, self.without)
                )

    def test_with_the_records_no_band_asset_can_trigger_unknown(self):
        eligible = self.current["eligible_category"]
        excluded = set(self.current["excluded_categories"])
        for asset_id in BAND:
            with self.subTest(asset_id=asset_id):
                category = CB.taxonomy_category(asset_id, EFFECTIVE, self.current)
                self.assertIsNotNone(category)
                self.assertIn(category, {eligible} | excluded)

    def test_assets_outside_the_band_still_fail_closed(self):
        """No blanket classification was introduced by this slice."""
        far_future = dt.date(2099, 1, 1)
        for asset_id in ("DGAI", "NOT_A_REAL_KRAKEN_ASSET"):
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, far_future, self.current)
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
