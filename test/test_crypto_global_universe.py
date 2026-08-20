"""P3-04 Crypto breadth source-coverage Global Master regression."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "crypto_global_universe.py"
SPEC = importlib.util.spec_from_file_location("crypto_global_universe", MODULE_PATH)
CGU = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(CGU)

FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "crypto_breadth_fixture_helpers", ROOT / "test" / "test_crypto_breadth.py"
)
FIXTURE = importlib.util.module_from_spec(FIXTURE_SPEC)
assert FIXTURE_SPEC.loader is not None
FIXTURE_SPEC.loader.exec_module(FIXTURE)


def make_fixture(
    root: Path,
    *,
    taxonomy=None,
    coverage_bps=9000,
    omit_latest=None,
    policy_mutator=None,
):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    identity_path = root / "identity.json"
    policy_path = root / "universe-policy.json"
    taxonomy_path = root / "taxonomy.json"
    FIXTURE.write_identity(
        identity_path,
        [
            FIXTURE.identity_record(
                "BTC", "BTC", aliases=["XBT", "XXBT"]
            )
        ],
    )
    FIXTURE.write_policy(policy_path, target=3, coverage_bps=coverage_bps)
    if policy_mutator is not None:
        value = json.loads(policy_path.read_text(encoding="utf-8"))
        policy_mutator(value)
        policy_path.write_text(
            json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    FIXTURE.write_taxonomy(
        taxonomy_path,
        taxonomy
        or {
            "BTC": "eligible_crypto",
            "ETH": "eligible_crypto",
            "SOL": "eligible_crypto",
            "USDT": "stablecoin",
        },
    )
    snapshot = FIXTURE.write_snapshot(
        root / "raw",
        identity_path=identity_path,
        omit_latest_bases=omit_latest,
    )
    return snapshot, policy_path, taxonomy_path, identity_path


def build(fixture, contract=None):
    snapshot, policy, taxonomy, identity = fixture
    return CGU.build_packet(
        snapshot,
        contract=contract,
        universe_policy_path=policy,
        taxonomy_path=taxonomy,
        identity_path=identity,
    )


class CryptoGlobalUniverseTests(unittest.TestCase):
    def test_ratified_breadth_selection_enters_source_coverage_master(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = build(make_fixture(Path(raw)))
        self.assertEqual(
            packet["status"], "BREADTH_SOURCE_COVERAGE_UNIVERSE_VALIDATED"
        )
        self.assertEqual(packet["selected_count"], 3)
        self.assertEqual(packet["target_count"], 3)
        self.assertEqual(packet["asset_master"]["record_count"], 3)
        self.assertEqual(
            [record["asset_id"] for record in packet["asset_master"]["records"]],
            ["CRYPTO:KRAKEN:BTC", "CRYPTO:KRAKEN:ETH", "CRYPTO:KRAKEN:SOL"],
        )
        for record in packet["asset_master"]["records"]:
            self.assertEqual(record["market"], "CRYPTO")
            self.assertEqual(record["asset_class"], "CRYPTO_ASSET")
            self.assertEqual(record["exchange_id"], "KRAKEN")
            self.assertFalse(record["universe_approved"])
            self.assertFalse(record["investable_eligible"])

    def test_membership_date_and_knowledge_time_remain_separate(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = build(make_fixture(Path(raw)))
        self.assertEqual(packet["as_of_date"], "2026-08-19")
        self.assertEqual(packet["knowledge_as_of_utc"], "2026-08-20T00:30:00Z")
        self.assertEqual(
            packet["effective_interval"],
            {"valid_from": "2026-08-19", "valid_to": "2026-08-20"},
        )
        for record in packet["asset_master"]["records"]:
            memberships = {
                (item["membership_type"], item["membership_id"])
                for item in record["active_memberships"]
            }
            self.assertEqual(
                memberships,
                {
                    ("MARKET", "CRYPTO"),
                    ("UNIVERSE", "KRAKEN_BREADTH_SOURCE_COVERAGE"),
                },
            )

    def test_btc_canonical_identity_and_source_aliases_are_preserved(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = build(make_fixture(Path(raw)))
        btc = next(
            record
            for record in packet["asset_master"]["records"]
            if record["asset_id"] == "CRYPTO:KRAKEN:BTC"
        )
        self.assertEqual(btc["primary_symbol"], "BTC")
        self.assertEqual(
            [alias["value"] for alias in btc["active_aliases"]],
            ["BTC", "XBT", "XXBT"],
        )
        self.assertEqual(
            {item["namespace"]: item["value"] for item in btc["identifiers"]},
            {
                "ATLAS_CANONICAL_ASSET_ID": "BTC",
                "KRAKEN_ASSET_ID": "BTC",
                "KRAKEN_PAIR_ID": "BTC/USD",
            },
        )

    def test_breadth_rank_is_preserved_but_not_relabelled_investability(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = build(make_fixture(Path(raw)))
        attributes = packet["source_attribute_rows"]
        self.assertEqual(
            [row["canonical_asset_id"] for row in attributes], ["BTC", "ETH", "SOL"]
        )
        self.assertEqual(
            sorted(row["breadth_selected_rank"] for row in attributes), [1, 2, 3]
        )
        for row in attributes:
            self.assertEqual(row["breadth_taxonomy_category"], "eligible_crypto")
            self.assertTrue(row["breadth_scope_only"])
            self.assertIsNone(row["liquidity_for_investability"])
            self.assertIsNone(row["tradability_decision"])
            self.assertIsNone(row["custody_decision"])
            self.assertFalse(row["investable_eligible"])
        self.assertNotIn("USDT", {row["canonical_asset_id"] for row in attributes})

    def test_composite_lineage_binds_all_catalog_and_selection_components(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = build(make_fixture(Path(raw)))
        btc = next(
            row
            for row in packet["asset_master"]["records"]
            if row["primary_symbol"] == "BTC"
        )
        source = btc["source_identity"]
        self.assertEqual(source["source_id"], "kraken_public_api")
        self.assertEqual(
            source["lineage_kind"], "VALIDATED_COMPOSITE_SNAPSHOT_MANIFEST"
        )
        self.assertEqual(
            source["source_sha256"],
            source["lineage_components"]["asset_pairs"]["response_sha256"],
        )
        self.assertEqual(
            source["lineage_components"]["snapshot_manifest"]["sha256"],
            packet["snapshot_lineage"]["manifest_sha256"],
        )
        components = source["lineage_components"]
        self.assertEqual(
            set(components),
            {
                "snapshot_manifest",
                "assets",
                "asset_pairs",
                "ohlc_bundle",
                "member_ohlc",
                "breadth_universe_policy",
                "taxonomy_policy",
                "identity_policy",
            },
        )
        self.assertEqual(components["member_ohlc"]["pair_id"], "BTC/USD")

    def test_unknown_taxonomy_never_populates_master(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = make_fixture(
                Path(raw),
                taxonomy={
                    "BTC": "eligible_crypto",
                    "SOL": "eligible_crypto",
                    "USDT": "stablecoin",
                },
            )
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "SELECTION_UNKNOWN"):
                build(fixture)

    def test_full_target_observation_is_required_even_if_breadth_allows_90_percent(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = make_fixture(
                Path(raw), coverage_bps=6000, omit_latest=["ETH"]
            )
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "FULL_COVERAGE_REQUIRED"):
                build(fixture)

    def test_default_90_percent_unknown_also_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = make_fixture(Path(raw), omit_latest=["ETH"])
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "SELECTION_UNKNOWN"):
                build(fixture)

    def test_manifest_or_raw_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = make_fixture(Path(raw))
            snapshot = fixture[0]
            manifest = json.loads((snapshot / "_manifest.json").read_text())
            manifest["catalog_counts"]["assets"] += 1
            (snapshot / "_manifest.json").write_text(
                json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "INPUT_INVALID"):
                build(fixture)

    def test_breadth_policy_cannot_be_relabelled_investable(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = make_fixture(
                Path(raw),
                policy_mutator=lambda value: value.update(
                    {"universe_kind": "investable_universe"}
                ),
            )
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "INPUT_INVALID"):
                build(fixture)

    def test_output_is_deterministic_and_digest_bound(self):
        with tempfile.TemporaryDirectory() as raw:
            fixture = make_fixture(Path(raw))
            first = build(fixture)
            second = build(fixture)
        self.assertEqual(CGU.canonical_json(first), CGU.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, CGU.payload_sha256(second))

    def test_authority_and_investability_policies_stay_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            packet = build(make_fixture(Path(raw)))
        self.assertEqual(
            packet["policy_status"]["breadth_source_coverage_selection"],
            "RATIFIED_REUSED_WITHOUT_AUTHORITY_EXPANSION",
        )
        self.assertEqual(
            packet["policy_status"]["investable_universe_policy"], "UNRATIFIED"
        )
        self.assertTrue(packet["authority"]["breadth_source_coverage_universe_only"])
        for field in (
            "breadth_rank_as_investability_authorized",
            "liquidity_filter_authorized",
            "tradability_filter_authorized",
            "custody_filter_authorized",
            "investable_universe_authorized",
            "current_catalog_backfill_authorized",
            "stage_promotion_authorized",
            "production_authorized",
            "trading_authorized",
        ):
            self.assertFalse(packet["authority"][field])

    def test_contract_tamper_is_rejected_for_file_and_api(self):
        contract = CGU.load_contract()
        contract["authority"]["investable_universe_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "CONTRACT_FIELD_MISMATCH"):
                CGU.load_contract(path)
            fixture = make_fixture(Path(raw) / "fixture")
            with self.assertRaisesRegex(CGU.CryptoUniverseError, "CONTRACT_FIELD_MISMATCH"):
                build(fixture, contract)

    def test_cli_is_temp_only_atomic_and_preserves_existing_output_on_failure(self):
        tracked_before = (ROOT / "config" / "universe.json").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            fixture = make_fixture(tmp / "fixture")
            snapshot, policy, taxonomy, identity = fixture
            output_path = tmp / "output.json"
            command = [
                sys.executable,
                str(MODULE_PATH),
                str(snapshot),
                "--universe-policy",
                str(policy),
                "--taxonomy",
                str(taxonomy),
                "--identity",
                str(identity),
                "--out",
                str(output_path),
            ]
            result = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["selected_count"], 3)
            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            taxonomy_value = json.loads(taxonomy.read_text())
            taxonomy_value["records"] = [
                row
                for row in taxonomy_value["records"]
                if row["canonical_asset_id"] != "ETH"
            ]
            taxonomy.write_text(
                json.dumps(taxonomy_value, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            result = subprocess.run(
                command, cwd=ROOT, text=True, capture_output=True, check=False
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "config" / "universe.json").read_bytes(), tracked_before)

    def test_adapter_has_no_network_workflow_or_tracked_output(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("urlopen", text)
        self.assertNotIn("import requests", text)
        self.assertNotIn("config/universe.json", text)
        self.assertNotIn("data/", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
