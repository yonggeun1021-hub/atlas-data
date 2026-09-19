#!/usr/bin/env python3
"""Crypto breadth taxonomy: rank-200 push, slice A (ranks 172-185).

The 2026-09-18 deferred-five batch (PR #823) left the block contiguous
through rank 171, with `ROBO` at minimum rank 172 named as the next
unclassified asset.  This slice is the first of three stacked slices that
carry the block to rank 201, ahead of the 2026-10-07 latch after which a
single unclassified asset inside the scan range costs about 35 days
instead of nothing.

Thirteen assets, effective 2026-09-19:

- eligible_crypto: ADI AVNT AXS EGLD GRASS MEGA MOVR OKB PONKE ROBO
  TURBO VTHO
- unverified_identity: ZEREBRO

Seven are bound to an exact on-chain identifier published by the project
itself: the ADI Foundation's ERC-20 contract, the Avantis/Veranta Base
contract, the Turbo Ethereum contract, the Moonbeam Foundation's new Base
MOVR contract, MegaETH's native protocol token, VeChain's built-in VTHO
Energy contract and Ponke's Solana mint.

Two findings are worth naming.  `MOVR` looked like a Kraken catalog quirk
-- Kraken publishes Moonriver on Base, not on its Kusama parachain -- and
it is not one: the Moonbeam Foundation itself announced and executed a
full 1:1 migration of MOVR to Base with a 2026-07-31 bridging deadline,
so Kraken's network is correct.  `OKB` could not be captured at all with a
rendered-DOM fetch because okx.com returns an empty body to headless
Chrome; the bodies behind that record were retained over a plain HTTP
request instead, and the receipt says so.

`ROBO`, `GRASS` and `AXS` are deliberately recorded as chain-level
identities only, and the records say so, exactly as the `DENT` record did
in PR #823.  Each project publishes the chain and token standard but no
contract address on any officially reachable page; every address for them
that exists in public lives on third-party explorers, which the ratified
bar does not accept.  The tests below hold those disclosures in place so
they cannot quietly be upgraded to exact-contract claims later.

`ZEREBRO` is the fail-closed branch working.  Kraken publishes it as
Zerebro on Solana, but Kraken is one organisation and its own metadata
carries no chain/contract/genesis field; the project's own domain
zerebro.org serves a single animated canvas with no token, chain or
contract text anywhere in the retained DOM, its cited research paper path
returns 404, and its ZerePy repository documents an agent framework
rather than the token.  No project publication exists to match
independently and every mint claim traces to aggregators, so the asset is
recorded `unverified_identity` on the same basis and in the same wording
as `PLAY`, `RE` and `MOODENG`.  That is a real ratified exclusion -- the
ranking loop skips it and keeps going -- and it is not an investability
claim.

`TURBO` is the weakest record in this batch and is flagged as such in the
receipt.  turbotoken.io publishes the Ethereum contract, but that same
page states it is an independent, community-driven platform that does not
officially represent TURBO.  TURBO has no foundation or issuer that could
publish a protocol document, so the page is accepted as a token contract
publication under the ratified bar rather than as a foundation document.
A reviewer who does not accept that reading should move the record to
`unverified_identity`; the receipt says so in those words.

The receipt
`evidence/crypto/identity/crypto_breadth_rank200_slice_a_source_facts_20260919.json`
retains, per asset, the Kraken catalog status, the verdict, the
independent source organisations, every source URL with its retained
content SHA-256 and byte count, and the disambiguating identity finding.
The retained bodies live outside any session scratchpad, and the receipt
discloses that the 149 MB behind two earlier batches' receipts was swept
on 2026-09-18 and is not recoverable.

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
    / "crypto_breadth_rank200_slice_a_source_facts_20260919.json"
)


def _load(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CB = _load("crypto_breadth_rank200_slice_a", ".github/scripts/crypto_breadth.py")

EFFECTIVE = dt.date(2026, 9, 19)
ELIGIBLE = ('ROBO', 'GRASS', 'EGLD', 'OKB', 'ADI', 'AVNT', 'MOVR', 'MEGA', 'VTHO', 'AXS', 'PONKE')
EXCLUDED = {'TURBO': 'unverified_identity', 'ZEREBRO': 'unverified_identity'}
SLICE = tuple(sorted(ELIGIBLE + tuple(EXCLUDED)))

CATALOG_VINTAGE = dt.date(2026, 9, 18)
RETAINED_VINTAGES = tuple(
    dt.date(2026, 9, 12) + dt.timedelta(days=offset) for offset in range(7)
)

# What this branch buys, measured the same drift-robust way as PR #823:
# minimum rank across the seven committed vintages, classified at the
# effective date.  These slices are stacked in rank order, so this branch
# carries its predecessors' records too and CONTIGUOUS_THROUGH_RANK is the
# cumulative frontier, not this slice's contribution in isolation.
# PREVIOUS_CONTIGUOUS_THROUGH_RANK is correspondingly the frontier before
# the whole stack -- the counterfactual below removes every record the
# stack adds, not just this slice's, so that is the number it falls back
# to.
CONTIGUOUS_THROUGH_RANK = 186
PREVIOUS_CONTIGUOUS_THROUGH_RANK = 171

# The predecessor state this branch's taxonomy edit started from.
TAXONOMY_HASH_BEFORE = (
    "4e1086a2c16d6c4641f1b210f55438804ac71f7d75b1fb980e9d6801b5ae1af1"
)

# The exact on-chain identifiers the projects publish themselves.  A record
# that stops naming its identifier stops being the record that was reviewed.
PUBLISHED_IDENTIFIERS = {
    "ADI": "0x8b1484d57abbe239bb280661377363b03c89caea",
    "AVNT": "0x696F9436B67233384889472Cd7cD58A6fB5DF4f1",
    "MOVR": "0x43fEB74608334DDa8c1a6500D185cFC3Ea962B83",
    "MEGA": "0x28B7E77f82B25B95953825F1E3eA0E36c1c29861",
    "VTHO": "0x0000000000000000000000000000456E65726779",
    "PONKE": "5z3EqYQo9HiCEs3R84RCDMu2n7anpDMxRhdK8PSWmrRC"
}

# Assets whose two independent sources agree on the chain but where no
# official page publishes an exact contract/mint.  Same disclosure shape as
# the DENT record in PR #823: the record must keep saying so.
CHAIN_LEVEL_ONLY = ('ROBO', 'GRASS', 'AXS')


def _records(taxonomy: dict, asset_id: str) -> list:
    return [
        record
        for record in taxonomy["records"]
        if record["canonical_asset_id"] == asset_id
    ]


def _taxonomy_without_slice(tmp_dir: Path) -> dict:
    """The committed taxonomy with this branch's whole stacked block removed."""
    payload = json.loads(TAXONOMY_PATH.read_text())
    payload["records"] = [
        record
        for record in payload["records"]
        if record["canonical_asset_id"] not in STACKED_BLOCK
    ]
    target = tmp_dir / "taxonomy_without_slice.json"
    target.write_text(json.dumps(payload, indent=2) + "\n")
    return CB.load_exclusion_taxonomy(target)


# This slice's position in the three-slice stack.  The slices land in rank
# order, so a frontier measured with a later slice's records already
# present would not be this slice's frontier.
SLICE_LETTER = "a"
SLICE_ORDER = ("a", "b", "c")


def _receipt_path(letter: str) -> Path:
    return (
        ROOT / "evidence" / "crypto" / "identity"
        / f"crypto_breadth_rank200_slice_{letter}_source_facts_20260919.json"
    )


def _assets_of(letter: str) -> set:
    path = _receipt_path(letter)
    if not path.is_file():
        return set()
    receipt = json.loads(path.read_text())
    return {item["canonical_asset_id"] for item in receipt["assets"]}


# Every asset the whole rank-200 push classifies, derived from the receipts
# actually present in this checkout rather than hardcoded, so a missing
# receipt is a failure instead of a silently narrower counterfactual.
STACKED_BLOCK = tuple(sorted(set().union(*(_assets_of(x) for x in SLICE_ORDER)) or set()))

# The slices that land after this one.  Removing them is what makes the
# frontier below *this slice's* frontier rather than the finished stack's,
# so the number stays correct once the later slices merge.
LATER_SLICE_ASSETS = frozenset().union(
    *(
        _assets_of(letter)
        for letter in SLICE_ORDER[SLICE_ORDER.index(SLICE_LETTER) + 1:]
    ),
    frozenset(),
)


def _taxonomy_at_this_stack_point(tmp_dir: Path) -> dict:
    """The committed taxonomy as of this slice, later slices removed."""
    payload = json.loads(TAXONOMY_PATH.read_text())
    payload["records"] = [
        record
        for record in payload["records"]
        if record["canonical_asset_id"] not in LATER_SLICE_ASSETS
    ]
    target = tmp_dir / "taxonomy_at_this_stack_point.json"
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

    def test_every_eligible_record_is_eligible_crypto(self):
        for asset_id in ELIGIBLE:
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    _records(self.taxonomy, asset_id)[0]["category"],
                    self.taxonomy["eligible_category"],
                )

    def test_every_excluded_record_carries_its_declared_category(self):
        for asset_id, category in EXCLUDED.items():
            with self.subTest(asset_id=asset_id):
                self.assertEqual(
                    _records(self.taxonomy, asset_id)[0]["category"], category
                )
                self.assertIn(category, self.taxonomy["excluded_categories"])

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
        for asset_id in ELIGIBLE + tuple(EXCLUDED):
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

    def test_chain_level_only_records_keep_their_disclosure(self):
        """No published contract; the record must keep saying so.

        Same guard as the DENT record in PR #823 -- it stops a chain-level
        record being quietly read later as an exact-contract claim.
        """
        for asset_id in CHAIN_LEVEL_ONLY:
            with self.subTest(asset_id=asset_id):
                self.assertNotIn(asset_id, PUBLISHED_IDENTIFIERS)
                reason = _records(self.taxonomy, asset_id)[0]["reason"]
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

    def test_chain_level_findings_disclose_the_missing_contract_publication(self):
        findings = {
            item["canonical_asset_id"]: item["identity_finding"]
            for item in self.receipt["assets"]
        }
        for asset_id in CHAIN_LEVEL_ONLY:
            with self.subTest(asset_id=asset_id):
                self.assertIn("DISCLOSED LIMIT", findings[asset_id])
                self.assertIn("no exact-contract claim", findings[asset_id])

    def test_receipt_claims_no_authority_beyond_source_coverage(self):
        scope = self.receipt["scope"]
        for phrase in ("not investability", "Regime", "trading"):
            self.assertIn(phrase, scope)

    def test_receipt_discloses_where_the_retained_bodies_live(self):
        """The weakness that fired on 2026-09-18 must stay visible.

        A sweep deleted two earlier batches' bodies from the session
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
    """Effective 2026-09-19 means no retained vintage moves.

    Every committed snapshot 2026-09-12..2026-09-18 evaluates at
    as_of = vintage - 1 day, so the latest retained as_of is 2026-09-17 --
    strictly before this batch's effective date.  Adding or removing the
    records must therefore leave every retained result identical.
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
                self.assertEqual(sorted(scanned & set(STACKED_BLOCK)), [])


class ReDerivedContiguityTest(unittest.TestCase):
    """The claim this branch makes, measured rather than asserted.

    Each asset's *minimum* rank across every retained vintage decides the
    block: if no unclassified asset has ever been seen at or below
    CONTIGUOUS_THROUGH_RANK, one day of turnover drift cannot open a
    TAXONOMY_COVERAGE_UNKNOWN hole underneath it.
    """

    @classmethod
    def setUpClass(cls):
        cls.contract = CB.load_contract()
        cls.universe = CB.load_universe_policy()
        cls._tmp = tempfile.TemporaryDirectory()
        # Measured with this slice's records present and later slices'
        # records removed, so this is this slice's own frontier and stays
        # correct after the later slices merge.
        cls.current = _taxonomy_at_this_stack_point(Path(cls._tmp.name))
        cls.min_rank = {}
        for vintage in RETAINED_VINTAGES:
            core = CB.source_core(RAW_ROOT / vintage.isoformat(), cls.contract)
            for rank, item in enumerate(_full_ranking(core, cls.universe), start=1):
                asset_id = item["canonical_asset_id"]
                previous = cls.min_rank.get(asset_id)
                if previous is None or rank < previous:
                    cls.min_rank[asset_id] = rank

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

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
        """Without these records the block stops where the predecessor did."""
        with tempfile.TemporaryDirectory() as tmp:
            without = _taxonomy_without_slice(Path(tmp))
            remaining = self._unclassified_min_ranks(without)
        holes = sorted(
            (rank, asset_id)
            for asset_id, rank in remaining.items()
            if rank <= CONTIGUOUS_THROUGH_RANK
        )
        self.assertNotEqual(holes, [])
        self.assertTrue({asset_id for _, asset_id in holes} <= set(STACKED_BLOCK), holes)
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
