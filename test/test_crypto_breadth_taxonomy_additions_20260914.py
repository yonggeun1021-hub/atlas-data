#!/usr/bin/env python3
"""Crypto breadth taxonomy additions ratified by the user on 2026-09-14.

Ratification: CRYPTO-BREADTH-TAXONOMY-ADDITIONS-20260914
(USER_RATIFICATION_CRYPTO_BREADTH_TAXONOMY_ADDITIONS_20260914.json,
sha256 6ff7f4865db1dde6f61d40ada5c4971ef46f547f30bdab9f635415f0e6e8e931).

- LSK -> eligible_crypto, effective_from 2026-09-14
- SUSHI, VSN, TRIA, ZORA, XTZ, KII, 0G -> eligible_crypto, effective_from
  2026-09-15

Each literal Kraken identity is bound by an official Kraken source (listing
notice, migration notice or Kraken asset page) plus, where retained, the
project's own official documentation.  Thresholds, the Top-100 30-day
turnover rule and fail-closed TAXONOMY_COVERAGE_UNKNOWN are unchanged.

The tests are date-independent: they read only the committed taxonomy and the
already-committed raw Kraken snapshots 2026-09-08..2026-09-14.  The forward
check projects the committed 2026-09-14 snapshot to vintage 2026-09-15 in
memory only.  Vintage 2026-09-15 ranks on finalized days 2026-08-15..
2026-09-13, which are all already finalized in that snapshot, so the
projection's taxonomy gate is exact.  Nothing is written to disk except a
temporary counterfactual taxonomy file.
"""
from __future__ import annotations

import base64
import datetime as dt
import importlib.util
import json
from decimal import Decimal
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
RAW_ROOT = ROOT / "evidence" / "crypto" / "breadth" / "raw"
TAXONOMY_PATH = ROOT / "config" / "crypto_breadth_exclusion_taxonomy.json"


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_taxonomy_additions_20260914", ".github/scripts/crypto_breadth.py")

ADDITIONS = {
    "LSK": dt.date(2026, 9, 14),
    "SUSHI": dt.date(2026, 9, 15),
    "VSN": dt.date(2026, 9, 15),
    "TRIA": dt.date(2026, 9, 15),
    "ZORA": dt.date(2026, 9, 15),
    "XTZ": dt.date(2026, 9, 15),
    "KII": dt.date(2026, 9, 15),
    "0G": dt.date(2026, 9, 15),
}
RETAINED_VINTAGES = [dt.date(2026, 9, day) for day in range(8, 15)]
FORWARD_SOURCE_VINTAGE = dt.date(2026, 9, 14)


def _taxonomy_without_additions(tmp: Path) -> dict:
    document = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    document["records"] = [
        row for row in document["records"]
        if row["canonical_asset_id"] not in ADDITIONS
    ]
    path = tmp / "taxonomy_without_additions.json"
    path.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return CB.load_exclusion_taxonomy(path)


def _projected_core(core: dict, contract: dict) -> dict:
    """Advance a committed snapshot by one vintage in memory.

    A no-trade placeholder candle for the new vintage is appended so the
    existing normalizer treats every committed candle as finalized.  The
    placeholder is the excluded current candle and never enters the ranking
    window.
    """
    vintage = core["vintage"] + dt.timedelta(days=1)
    projected = {}
    for line in core["ohlc_bundle_raw"]["raw"].splitlines():
        record = json.loads(line)
        payload = json.loads(base64.b64decode(record["body_b64"]), parse_float=Decimal)
        result = payload["result"]
        pair_id = next(key for key in result if key != "last")
        rows = result[pair_id]
        last = rows[-1]
        close = last[4]
        placeholder = [last[0] + 86400, close, close, close, close, "0", "0", 0]
        maximum = contract.get("maximum_response_rows")
        kept = rows[1:] if maximum is not None and len(rows) >= maximum else rows
        result[pair_id] = kept + [placeholder]
        projected[pair_id] = CB.normalize_ohlc(
            payload, vintage, contract, CB.ohlc_file_name(pair_id)
        )
    return dict(core) | {"vintage": vintage, "ohlc": projected}


def _unknown_ids(result: dict) -> list:
    return [
        item["canonical_asset_id"]
        for item in result["diagnostics"]["taxonomy_unknown_before_cutoff"]
    ]


class TaxonomyAdditionRecordsTest(unittest.TestCase):
    def setUp(self):
        self.policy = CB.load_exclusion_taxonomy()

    def test_each_addition_is_one_open_ended_eligible_record_with_kraken_source(self):
        for asset_id, effective in ADDITIONS.items():
            with self.subTest(asset_id=asset_id):
                rows = [
                    row for row in self.policy["records"]
                    if row["canonical_asset_id"] == asset_id
                ]
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["category"], "eligible_crypto")
                self.assertEqual(rows[0]["effective_from"], effective.isoformat())
                self.assertIsNone(rows[0]["effective_to"])
                self.assertIn("Kraken", rows[0]["reason"])

    def test_no_backfill_before_ratified_effective_date(self):
        for asset_id, effective in ADDITIONS.items():
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(
                    CB.taxonomy_category(asset_id, effective - dt.timedelta(days=1), self.policy)
                )
                self.assertEqual(
                    CB.taxonomy_category(asset_id, effective, self.policy), "eligible_crypto"
                )

    def test_lsk_identity_excludes_klayr(self):
        self.assertIn("Klayr", self._reason("LSK"))
        self.assertIsNone(CB.taxonomy_category("KLY", dt.date(2099, 1, 1), self.policy))

    def test_unratified_assets_stay_fail_closed(self):
        self.assertEqual(self.policy["unknown_asset_policy"], "fail_closed_unknown")
        # SHAPE was classified later, by the 2026-09-18 cutoff-band slice
        # (test_crypto_breadth_band_identity_20260918.py); DGAI is still
        # unclassified, so it remains the fail-closed probe here.
        for asset_id in ("DGAI",):
            with self.subTest(asset_id=asset_id):
                self.assertIsNone(CB.taxonomy_category(asset_id, dt.date(2099, 1, 1), self.policy))

    def test_policy_header_unchanged(self):
        self.assertEqual(self.policy["schema_version"], 1)
        self.assertEqual(self.policy["policy_version"], "crypto_breadth_exclusion_taxonomy/v2")
        self.assertEqual(self.policy["approval_status"], "RATIFIED")
        self.assertEqual(self.policy["source_name"], "kraken_spot_market_data")
        self.assertEqual(self.policy["eligible_category"], "eligible_crypto")
        self.assertEqual(
            self.policy["excluded_categories"],
            ["commodity_linked", "fiat", "stablecoin", "staked", "unverified_identity", "wrapped"],
        )

    def test_universe_thresholds_unchanged(self):
        universe = CB.load_universe_policy()
        self.assertEqual(universe["target_asset_count"], 100)
        self.assertEqual(universe["minimum_observation_coverage_bps"], 9000)
        self.assertEqual(universe["ranking_lookback_finalized_days"], 30)

    def _reason(self, asset_id: str) -> str:
        return next(
            row["reason"] for row in self.policy["records"]
            if row["canonical_asset_id"] == asset_id
        )


class RetainedSnapshotResultsUnchangedTest(unittest.TestCase):
    """The additions do not alter any retained vintage 2026-09-08..14."""

    @classmethod
    def setUpClass(cls):
        cls.contract = CB.load_contract()
        cls.universe = CB.load_universe_policy()
        cls.current = CB.load_exclusion_taxonomy()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.without = _taxonomy_without_additions(Path(cls._tmp.name))

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_qualified_members_identical_with_and_without_additions(self):
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
                self.assertEqual(
                    [item["canonical_asset_id"] for item in after["members"]],
                    [item["canonical_asset_id"] for item in before["members"]],
                )

    def test_known_retained_outcomes(self):
        expected = {
            dt.date(2026, 9, 8): ("UNKNOWN", "TAXONOMY_COVERAGE_UNKNOWN", ["RAY", "DRV"]),
        }
        for vintage in RETAINED_VINTAGES:
            core = CB.source_core(RAW_ROOT / vintage.isoformat(), self.contract)
            result = CB.qualified_members(core, self.universe, self.current)
            with self.subTest(vintage=vintage.isoformat()):
                if vintage in expected:
                    status, reason, unknown = expected[vintage]
                    self.assertEqual((result["status"], result["reason"]), (status, reason))
                    self.assertEqual(sorted(_unknown_ids(result)), sorted(unknown))
                else:
                    self.assertEqual(result["status"], "OBSERVED_UNCLASSIFIED")
                    self.assertEqual(_unknown_ids(result), [])


class ForwardVintageLskCoverageTest(unittest.TestCase):
    """Vintage 2026-09-15 (as_of 2026-09-14) is blocked only by LSK without
    the additions and is taxonomy-complete with them."""

    @classmethod
    def setUpClass(cls):
        cls.contract = CB.load_contract()
        cls.universe = CB.load_universe_policy()
        cls.current = CB.load_exclusion_taxonomy()
        cls._tmp = tempfile.TemporaryDirectory()
        cls.without = _taxonomy_without_additions(Path(cls._tmp.name))
        core = CB.source_core(RAW_ROOT / FORWARD_SOURCE_VINTAGE.isoformat(), cls.contract)
        cls.projected = _projected_core(core, cls.contract)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_projection_ranks_the_exact_vintage_window(self):
        self.assertEqual(self.projected["vintage"], dt.date(2026, 9, 15))
        series = self.projected["ohlc"]["LSK/USD"]
        self.assertEqual(series["ranking_start_day"], "2026-08-15")
        self.assertEqual(series["ranking_end_day"], "2026-09-13")
        self.assertTrue(series["ranking_history_complete"])

    def test_lsk_is_the_only_blocker_without_additions(self):
        result = CB.qualified_members(self.projected, self.universe, self.without)
        self.assertEqual(result["status"], "UNKNOWN")
        self.assertEqual(result["reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        self.assertEqual(_unknown_ids(result), ["LSK"])

    def test_additions_clear_taxonomy_coverage_for_vintage_0915(self):
        result = CB.qualified_members(self.projected, self.universe, self.current)
        self.assertNotEqual(result["reason"], "TAXONOMY_COVERAGE_UNKNOWN")
        self.assertEqual(_unknown_ids(result), [])
        member_ids = {item["canonical_asset_id"] for item in result["members"]}
        self.assertIn("LSK", member_ids)


if __name__ == "__main__":
    unittest.main(verbosity=2)
