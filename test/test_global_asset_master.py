"""P3-01 Global Security / Asset Master contract regression."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "universe" / "global_asset_master.py"
SPEC = importlib.util.spec_from_file_location("global_asset_master", MODULE_PATH)
GAM = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(GAM)

# The binding tests reuse the merged ThemeTaxonomy/2 fixtures and its isolated
# synthetic authority repository instead of restating them here.
_AUTHORITY_SPEC = importlib.util.spec_from_file_location(
    "atlas_global_asset_master_authority_fixture",
    ROOT / "test" / "test_theme_taxonomy_authority.py",
)
assert _AUTHORITY_SPEC is not None and _AUTHORITY_SPEC.loader is not None
_AUTHORITY = importlib.util.module_from_spec(_AUTHORITY_SPEC)
_AUTHORITY_SPEC.loader.exec_module(_AUTHORITY)
AuthorityRepo = _AUTHORITY.AuthorityRepo
TT = _AUTHORITY.TT
taxonomy_fixture = _AUTHORITY.fixture

# Exact bytes the ThemeTaxonomy/2 fixture publishes for the US membership's
# single evidence row.  A binding is only positive when the master's own THEME
# membership names the same document.
TAXONOMY_EVIDENCE_URL = "https://www.sec.gov/Archives/edgar/data/1/EVIDENCE.US.TEST.htm"
TAXONOMY_EVIDENCE_SHA256 = "b" * 64


def source(source_id: str, suffix: str = "a") -> dict:
    return {
        "source_id": source_id,
        "source_url": f"https://example.invalid/{source_id}/{suffix}",
        "source_sha256": hashlib.sha256(suffix.encode()).hexdigest(),
        "available_at": "2026-08-19T00:00:00Z",
        "retrieved_at_utc": "2026-08-19T00:05:00Z",
    }


def interval_source(source_id: str, suffix: str) -> dict:
    return source(source_id, suffix)


def record(
    asset_id: str,
    market: str,
    asset_class: str,
    symbol: str,
    exchange_id: str,
    currency: str,
    source_id: str,
) -> dict:
    namespace = {
        "US": "NASDAQ_SYMBOL",
        "KOREA": "KRX_CODE",
        "CRYPTO": "KRAKEN_ASSET_ID",
    }[market]
    return {
        "asset_id": asset_id,
        "market": market,
        "asset_class": asset_class,
        "display_name": {
            "US": "Synthetic US Equity",
            "KOREA": "Synthetic Korea Equity",
            "CRYPTO": "Synthetic Crypto Asset",
        }[market],
        "primary_symbol": symbol,
        "exchange_id": exchange_id,
        "quote_currency": currency,
        "identifiers": [
            {"namespace": namespace, "value": symbol},
            {"namespace": "ATLAS_SOURCE_KEY", "value": f"{source_id}:{symbol}"},
        ],
        "aliases": [
            {
                "alias_type": "SYMBOL",
                "value": symbol,
                "exchange_id": exchange_id,
                "valid_from": "2020-01-01",
                "valid_to": None,
                "source_identity": interval_source(source_id, f"alias-{asset_id}"),
            }
        ],
        "memberships": [
            {
                "membership_type": "MARKET",
                "membership_id": market,
                "valid_from": "2020-01-01",
                "valid_to": None,
                "source_identity": interval_source(source_id, f"market-{asset_id}"),
            }
        ],
        "source_identity": source(source_id, f"record-{asset_id}"),
    }


def sample_input() -> dict:
    us = record(
        "US:XNAS:MSFT",
        "US",
        "EQUITY",
        "MSFT",
        "XNAS",
        "USD",
        "nasdaq_trader_symbol_directory",
    )
    us["memberships"].extend(
        [
            {
                "membership_type": "THEME",
                "membership_id": "SYNTHETIC_THEME",
                "valid_from": "2025-01-01",
                "valid_to": None,
                "source_identity": bound_theme_source(),
            },
            {
                "membership_type": "UNIVERSE",
                "membership_id": "SYNTHETIC_US_RESEARCH",
                "valid_from": "2025-01-01",
                "valid_to": None,
                "source_identity": interval_source(
                    "nasdaq_trader_symbol_directory", "universe-us"
                ),
            },
        ]
    )
    korea = record(
        "KR:XKRX:005930",
        "KOREA",
        "EQUITY",
        "005930",
        "XKRX",
        "KRW",
        "krx_open_api_stock_daily",
    )
    crypto = record(
        "CRYPTO:KRAKEN:BTC",
        "CRYPTO",
        "CRYPTO_ASSET",
        "BTC",
        "KRAKEN",
        "USD",
        "kraken_public_api",
    )
    crypto["aliases"].insert(
        0,
        {
            "alias_type": "SYMBOL",
            "value": "XBT",
            "exchange_id": "KRAKEN",
            "valid_from": "2010-01-01",
            "valid_to": "2020-01-01",
            "source_identity": interval_source("kraken_public_api", "old-btc-alias"),
        },
    )
    return {
        "schema_version": "global_asset_master_input/1",
        "master_id": "ATLAS_RESEARCH_ASSETS",
        "as_of_date": "2026-08-20",
        "records": [us, korea, crypto],
    }


def bound_theme_source() -> dict:
    return {
        "source_id": "sec_edgar",
        "source_url": TAXONOMY_EVIDENCE_URL,
        "source_sha256": TAXONOMY_EVIDENCE_SHA256,
        "available_at": "2026-08-18",
        "retrieved_at_utc": "2026-08-18T12:00:00Z",
    }


def binding_master_input() -> dict:
    """Sample master plus one record whose THEME membership can be bound."""
    value = sample_input()
    bound = record(
        "US:XNAS:TEST",
        "US",
        "EQUITY",
        "TEST",
        "XNAS",
        "USD",
        "nasdaq_trader_symbol_directory",
    )
    bound["memberships"].append(
        {
            "membership_type": "THEME",
            "membership_id": "SEGMENT.COMPUTE",
            "valid_from": "2026-08-20",
            "valid_to": None,
            "source_identity": bound_theme_source(),
        }
    )
    value["records"].append(bound)
    return value


def binding_reference(**overrides) -> dict:
    reference = {
        "asset_id": "US:XNAS:TEST",
        "gam_membership_id": "SEGMENT.COMPUTE",
        "taxonomy_membership_id": "MEMBERSHIP.US.TEST",
        "evidence_id": "EVIDENCE.US.TEST",
    }
    reference.update(overrides)
    return reference


def bound_membership(value: dict) -> dict:
    bound = next(row for row in value["records"] if row["asset_id"] == "US:XNAS:TEST")
    return next(
        row for row in bound["memberships"] if row["membership_type"] == "THEME"
    )


def repository_head() -> str:
    """Immutable object name of this repository's current commit.

    The binding check refuses to resolve an authority boundary against a moving
    reference, so even the default-registry tests must pin one explicitly.
    """
    return subprocess.check_output(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
    ).strip()


def rehash(packet: dict) -> dict:
    value = copy.deepcopy(packet)
    value.pop("payload_sha256", None)
    value["payload_sha256"] = GAM.payload_sha256(value)
    return value


class GlobalAssetMasterTests(unittest.TestCase):
    def test_cross_market_master_and_authority_boundary(self):
        packet = GAM.build_master(sample_input())
        self.assertEqual(packet["status"], "IDENTITY_MASTER_VALIDATED")
        self.assertEqual(packet["record_count"], 3)
        self.assertEqual(
            [row["asset_id"] for row in packet["records"]],
            ["CRYPTO:KRAKEN:BTC", "KR:XKRX:005930", "US:XNAS:MSFT"],
        )
        self.assertEqual(
            {row["market"] for row in packet["records"]}, {"US", "KOREA", "CRYPTO"}
        )
        for row in packet["records"]:
            self.assertFalse(row["universe_approved"])
            self.assertFalse(row["investable_eligible"])
            self.assertIsNone(row["stage_transition"])
        authority = packet["authority"]
        self.assertTrue(authority["identity_recording_only"])
        self.assertFalse(authority["universe_approval_authorized"])
        self.assertFalse(authority["investability_authorized"])
        self.assertFalse(authority["production_authorized"])
        self.assertFalse(authority["trading_authorized"])

    def test_theme_and_universe_memberships_are_explicit_only(self):
        packet = GAM.build_master(sample_input())
        by_id = {row["asset_id"]: row for row in packet["records"]}
        us_types = {
            row["membership_type"] for row in by_id["US:XNAS:MSFT"]["active_memberships"]
        }
        korea_types = {
            row["membership_type"]
            for row in by_id["KR:XKRX:005930"]["active_memberships"]
        }
        self.assertEqual(us_types, {"MARKET", "THEME", "UNIVERSE"})
        self.assertEqual(korea_types, {"MARKET"})
        self.assertEqual(packet["policy_status"]["membership_selection"], "EXPLICIT_ONLY")
        self.assertEqual(packet["policy_status"]["theme_taxonomy"], "UNRATIFIED")

    def test_official_us_preferred_symbol_character_is_preserved(self):
        value = sample_input()
        us = value["records"][0]
        us["asset_id"] = "US:NASDAQDIR:PREFERRED"
        us["primary_symbol"] = "ABR$D"
        us["aliases"][0]["value"] = "ABR$D"
        us["identifiers"][0]["value"] = "ABR$D"
        packet = GAM.build_master(value)
        record = next(row for row in packet["records"] if row["market"] == "US")
        self.assertEqual(record["primary_symbol"], "ABR$D")
        self.assertEqual(record["active_aliases"][0]["value"], "ABR$D")

    def test_effective_dated_alias_preserves_history(self):
        packet = GAM.build_master(sample_input())
        btc = next(row for row in packet["records"] if row["market"] == "CRYPTO")
        self.assertEqual([row["value"] for row in btc["aliases"]], ["BTC", "XBT"])
        self.assertEqual([row["value"] for row in btc["active_aliases"]], ["BTC"])
        self.assertEqual(GAM.load_contract()["effective_interval"], "[valid_from, valid_to)")

    def test_output_is_order_independent_and_digest_bound(self):
        value = sample_input()
        first = GAM.build_master(value)
        value["records"].reverse()
        for row in value["records"]:
            row["identifiers"].reverse()
            row["memberships"].reverse()
        second = GAM.build_master(value)
        self.assertEqual(GAM.canonical_json(first), GAM.canonical_json(second))
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, GAM.payload_sha256(second))

    def test_standalone_validator_accepts_persisted_packet(self):
        packet = GAM.build_master(sample_input())
        validated = GAM.validate_packet(copy.deepcopy(packet))
        self.assertEqual(GAM.canonical_json(validated), GAM.canonical_json(packet))

    def test_standalone_validator_rejects_rehashed_derived_membership_tamper(self):
        packet = GAM.build_master(sample_input())
        us = next(row for row in packet["records"] if row["market"] == "US")
        us["active_memberships"] = [
            row
            for row in us["active_memberships"]
            if row["membership_type"] != "THEME"
        ]
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "OUTPUT_RECORD_DERIVATION_MISMATCH"
        ):
            GAM.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_authority_expansion(self):
        packet = GAM.build_master(sample_input())
        packet["authority"]["investability_authorized"] = True
        with self.assertRaisesRegex(GAM.AssetMasterError, "OUTPUT_AUTHORITY_MISMATCH"):
            GAM.validate_packet(rehash(packet))

    def test_standalone_validator_rejects_rehashed_cross_asset_collision(self):
        packet = GAM.build_master(sample_input())
        packet["records"][1]["identifiers"][0] = copy.deepcopy(
            packet["records"][0]["identifiers"][0]
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "IDENTIFIER_COLLISION"):
            GAM.validate_packet(rehash(packet))

    def test_duplicate_asset_primary_and_identifier_collisions_fail_closed(self):
        duplicate = sample_input()
        duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
        with self.assertRaisesRegex(GAM.AssetMasterError, "ASSET_ID_DUPLICATE"):
            GAM.build_master(duplicate)

        primary = sample_input()
        other = copy.deepcopy(primary["records"][0])
        other["asset_id"] = "US:XNAS:MSFT.SECOND"
        other["identifiers"] = [
            {"namespace": "NASDAQ_SYMBOL_SECOND", "value": "MSFT.SECOND"}
        ]
        with self.assertRaisesRegex(GAM.AssetMasterError, "PRIMARY_IDENTITY_COLLISION"):
            GAM.build_master({**primary, "records": primary["records"] + [other]})

        identifier = sample_input()
        identifier["records"][1]["identifiers"][0] = copy.deepcopy(
            identifier["records"][0]["identifiers"][0]
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "IDENTIFIER_COLLISION"):
            GAM.build_master(identifier)

    def test_overlapping_aliases_across_assets_fail_but_reuse_after_end_is_allowed(self):
        value = sample_input()
        other = record(
            "CRYPTO:KRAKEN:BTC2",
            "CRYPTO",
            "CRYPTO_ASSET",
            "BTC2",
            "KRAKEN",
            "USD",
            "kraken_public_api",
        )
        other["aliases"].append(
            {
                "alias_type": "SYMBOL",
                "value": "XBT",
                "exchange_id": "KRAKEN",
                "valid_from": "2019-01-01",
                "valid_to": None,
                "source_identity": interval_source("kraken_public_api", "reuse-overlap"),
            }
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "ALIAS_IDENTITY_COLLISION"):
            GAM.build_master({**value, "records": value["records"] + [other]})

        other["aliases"][-1]["valid_from"] = "2000-01-01"
        other["aliases"][-1]["valid_to"] = "2010-01-01"
        packet = GAM.build_master({**value, "records": value["records"] + [other]})
        self.assertEqual(packet["record_count"], 4)

    def test_overlapping_membership_ranges_fail(self):
        value = sample_input()
        value["records"][0]["memberships"].append(
            {
                "membership_type": "MARKET",
                "membership_id": "US",
                "valid_from": "2025-01-01",
                "valid_to": None,
                "source_identity": interval_source(
                    "nasdaq_trader_symbol_directory", "overlap-market"
                ),
            }
        )
        with self.assertRaisesRegex(GAM.AssetMasterError, "MEMBERSHIP_INTERVAL_OVERLAP"):
            GAM.build_master(value)

    def test_primary_alias_and_market_membership_must_be_active(self):
        alias = sample_input()
        alias["records"][0]["aliases"][0]["valid_to"] = "2026-01-01"
        with self.assertRaisesRegex(GAM.AssetMasterError, "PRIMARY_ALIAS_NOT_ACTIVE"):
            GAM.build_master(alias)

        membership = sample_input()
        membership["records"][0]["memberships"][0]["valid_to"] = "2026-01-01"
        with self.assertRaisesRegex(GAM.AssetMasterError, "MARKET_MEMBERSHIP_NOT_ACTIVE"):
            GAM.build_master(membership)

    def test_lineage_and_temporal_errors_fail_closed(self):
        missing = sample_input()
        del missing["records"][0]["source_identity"]["source_sha256"]
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_LINEAGE_INCOMPLETE"):
            GAM.build_master(missing)

        unknown = sample_input()
        unknown["records"][0]["source_identity"]["source_id"] = "unknown_vendor"
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_ID_UNKNOWN"):
            GAM.build_master(unknown)

        future = sample_input()
        future["records"][0]["source_identity"]["available_at"] = "2026-08-20T00:00:00Z"
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            GAM.build_master(future)

    def test_market_and_asset_class_cannot_be_crossed(self):
        value = sample_input()
        value["records"][0]["asset_class"] = "CRYPTO_ASSET"
        with self.assertRaisesRegex(GAM.AssetMasterError, "MARKET_ASSET_CLASS_MISMATCH"):
            GAM.build_master(value)

    def test_contract_tampering_is_rejected(self):
        contract = GAM.load_contract()
        contract["authority"]["investability_authorized"] = True
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "contract.json"
            path.write_text(json.dumps(contract), encoding="utf-8")
            with self.assertRaisesRegex(GAM.AssetMasterError, "AUTHORITY_BOUNDARY_MISMATCH"):
                GAM.load_contract(path)
        with self.assertRaisesRegex(GAM.AssetMasterError, "AUTHORITY_BOUNDARY_MISMATCH"):
            GAM.build_master(sample_input(), contract)

    def test_date_lineage_cannot_be_later_than_retrieval_date(self):
        value = sample_input()
        value["records"][0]["source_identity"]["available_at"] = "2026-08-20"
        with self.assertRaisesRegex(GAM.AssetMasterError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            GAM.build_master(value)

    def test_cli_writes_only_requested_temp_output_and_preserves_on_failure(self):
        tracked_before = (ROOT / "config" / "universe.json").read_bytes()
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            input_path = tmp / "input.json"
            output_path = tmp / "master.json"
            input_path.write_text(json.dumps(sample_input()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    str(input_path),
                    "--out",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text())["record_count"], 3)

            sentinel = b"preserve-existing-output\n"
            output_path.write_bytes(sentinel)
            broken = sample_input()
            broken["records"][0]["memberships"] = []
            input_path.write_text(json.dumps(broken), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(MODULE_PATH),
                    str(input_path),
                    "--out",
                    str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(output_path.read_bytes(), sentinel)
        self.assertEqual((ROOT / "config" / "universe.json").read_bytes(), tracked_before)

    def test_module_has_no_network_client_or_tracked_default_output(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", text)
        self.assertNotIn("urllib.request", text)
        self.assertNotIn("config/universe.json", text)
        self.assertNotIn("data/", text)


class ThemeSourceRoleTests(unittest.TestCase):
    def test_identity_market_universe_and_alias_reject_disclosures(self):
        for role in ('record', 'alias', 'MARKET', 'UNIVERSE'):
            with self.subTest(role=role):
                master = sample_input()
                row = master['records'][0]
                if role == 'record':
                    target = row
                elif role == 'alias':
                    target = row['aliases'][0]
                else:
                    target = next(m for m in row['memberships'] if m['membership_type'] == role)
                target['source_identity'] = bound_theme_source()
                with self.assertRaisesRegex(GAM.AssetMasterError, 'SOURCE_ID_UNKNOWN'):
                    GAM.build_master(master)

    def test_theme_rejects_identity_provider_market_host_hash_and_time_errors(self):
        cases = [
            ('source_id', 'nasdaq_trader_symbol_directory'),
            ('source_id', 'dart_open_api'),
            ('source_url', 'https://www.sec.gov.evil.invalid/a'),
            ('source_url', 'https://user@www.sec.gov/a'),
            ('source_url', 'http://www.sec.gov/a'),
            ('source_sha256', 'not-a-hash'),
            ('available_at', '2026-08-21'),
            ('retrieved_at_utc', '2026-08-21T00:00:00Z'),
            ('retrieved_at_utc', '2026-08-17T00:00:00Z'),
        ]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                master = binding_master_input()
                bound_membership(master)['source_identity'][field] = value
                with self.assertRaisesRegex(GAM.AssetMasterError, 'THEME_SOURCE_INVALID'):
                    GAM.build_master(master)
        master = sample_input()
        crypto = next(r for r in master['records'] if r['market'] == 'CRYPTO')
        crypto['memberships'].append(copy.deepcopy(bound_membership(binding_master_input())))
        with self.assertRaisesRegex(GAM.AssetMasterError, 'THEME_SOURCE_MARKET_UNSUPPORTED'):
            GAM.build_master(master)

    def test_rehashed_packet_cannot_move_disclosure_into_identity_role(self):
        packet = GAM.build_master(binding_master_input())
        packet['records'][0]['source_identity'] = bound_theme_source()
        with self.assertRaisesRegex(GAM.AssetMasterError, 'SOURCE_ID_UNKNOWN'):
            GAM.validate_packet(rehash(packet))

    def test_theme_provenance_contract_cannot_be_redirected_or_widened(self):
        for field, value in [('registry', 'config/universe.json'),
                             ('source_role', 'IDENTITY'), ('contract', 'theme_taxonomy/999')]:
            with self.subTest(field=field):
                contract = GAM.load_contract()
                contract['theme_membership_provenance'][field] = value
                with self.assertRaisesRegex(GAM.AssetMasterError, 'THEME_PROVENANCE_CONTRACT_MISMATCH'):
                    GAM.build_master(sample_input(), contract)
        contract = GAM.load_contract()
        contract['source_coverage']['sec_edgar'] = 'SOURCE_CAPABILITY_EXISTS'
        with self.assertRaisesRegex(GAM.AssetMasterError, 'SOURCE_COVERAGE_MISMATCH'):
            GAM.build_master(sample_input(), contract)


class ThemeSourceBindingTests(unittest.TestCase):
    """Optional read-only THEME input source-binding validation."""

    def setUp(self):
        if shutil.which("git") is None:
            self.skipTest("git is required to rebuild the taxonomy authority boundary")

    def authority_repo(self, graph: dict, **kwargs):
        """Clearly synthetic, isolated approval evidence in a throwaway repo."""
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        return AuthorityRepo(Path(temp.name), graph, **kwargs)

    def check(self, master, graph, bindings, repo=None, **kwargs):
        if repo is not None:
            kwargs.setdefault("authority_registry_path", repo.registry_path)
            kwargs.setdefault("trusted_commit", repo.head())
        return GAM.validate_theme_source_binding(master, graph, bindings, **kwargs)

    def test_exact_explicit_binding_is_checked_without_populating_the_master(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        report = self.check(binding_master_input(), graph, [binding_reference()], repo)

        # Both THEME rows carry the literal same disclosure identity and the
        # independent synthetic authority approves this exact graph.
        self.assertEqual(report["status"], "THEME_SOURCE_BINDING_VERIFIED")
        self.assertEqual(report["binding_count"], 1)
        self.assertEqual(report["verified_binding_count"], 1)
        self.assertEqual(
            report["taxonomy_authority_resolution"]["status"], "AUTHORIZED"
        )
        binding = report["bindings"][0]
        self.assertTrue(binding["verified"])
        self.assertEqual(binding["failure_reasons"], [])
        self.assertEqual(
            binding["unresolved_reasons"],
            [],
        )

        # Nothing is populated, approved, or made investable by verification.
        self.assertFalse(report["master_population_authorized"])
        self.assertEqual(report["authority"], GAM.load_contract()["authority"])
        self.assertFalse(report["authority"]["investability_authorized"])
        self.assertFalse(report["authority"]["universe_approval_authorized"])
        self.assertFalse(report["authority"]["trading_authorized"])
        self.assertIn(
            "THEME_MEMBERSHIP_INGESTION_NOT_IMPLEMENTED",
            report["unresolved_boundaries"],
        )
        digest = report.pop("payload_sha256")
        self.assertEqual(digest, GAM.payload_sha256(report))

    def test_checked_binding_preserves_role_evidence_and_source_labels(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        report = self.check(binding_master_input(), graph, [binding_reference()], repo)
        binding = report["bindings"][0]

        taxonomy_reference = binding["taxonomy_reference"]
        self.assertEqual(taxonomy_reference["role_id"], "COMPUTE_VENDOR")
        self.assertEqual(taxonomy_reference["theme_id"], "SEGMENT.COMPUTE")
        self.assertEqual(taxonomy_reference["evidence_ids"], ["EVIDENCE.US.TEST"])
        self.assertEqual(
            taxonomy_reference["evidence"]["audit_provenance"]["review_status"],
            "HUMAN_RATIFIED_INPUT",
        )
        self.assertEqual(
            taxonomy_reference["evidence"]["source_identity"]["source_id"], "sec_edgar"
        )
        # The literal disclosure label survives on both sides; identity
        # provider names are never aliased to disclosure providers.
        self.assertEqual(
            binding["master_reference"]["source_identity"]["source_id"],
            "sec_edgar",
        )
        self.assertEqual(
            binding["source_id_comparison"], "COMPARED"
        )
        self.assertIn("sec_edgar", report["comparison_basis"]["shared_source_registry"])
        self.assertIn("source_id", report["comparison_basis"]["compared_fields"])
        self.assertNotIn("source_id", report["comparison_basis"]["preserved_uncompared_fields"])
        self.assertEqual(
            report["comparison_basis"]["preserved_uncompared_fields"]["role_id"],
            "GAM_MEMBERSHIP_HAS_NO_ROLE_FIELD",
        )

    def test_exact_immutable_commit_pin_is_accepted_for_the_same_binding(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        report = self.check(
            binding_master_input(),
            graph,
            [binding_reference()],
            repo,
            trusted_commit=repo.head(),
        )
        self.assertEqual(report["status"], "THEME_SOURCE_BINDING_VERIFIED")
        self.assertEqual(report["bindings"][0]["failure_reasons"], [])
        self.assertEqual(
            report["taxonomy_authority_resolution"]["trusted_commit"], repo.head()
        )

    def test_authority_boundary_requires_a_caller_pinned_immutable_commit(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        master = binding_master_input()

        # Omitting the pin must fail closed instead of silently resolving the
        # authority registry against whatever HEAD happens to be.
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "BINDING_TRUSTED_COMMIT_REQUIRED"
        ):
            GAM.validate_theme_source_binding(
                master,
                graph,
                [binding_reference()],
                authority_registry_path=repo.registry_path,
            )
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "BINDING_TRUSTED_COMMIT_REQUIRED"
        ):
            self.check(master, graph, [binding_reference()], repo, trusted_commit=None)

        head = repo.head()
        for value in ("HEAD", "@", "main", "", head[:12], "A" * 40, 0, head.encode()):
            with self.subTest(trusted_commit=value):
                with self.assertRaisesRegex(
                    GAM.AssetMasterError, "BINDING_TRUSTED_COMMIT_INVALID"
                ):
                    self.check(
                        master, graph, [binding_reference()], repo, trusted_commit=value
                    )

        # A caller-named immutable commit is the only accepted boundary, and it
        # is the commit the authority resolution actually reports.
        report = self.check(
            master, graph, [binding_reference()], repo, trusted_commit=head
        )
        self.assertEqual(
            report["taxonomy_authority_resolution"]["trusted_commit"], head
        )

    def test_committed_empty_registry_cannot_verify_a_membership(self):
        report = GAM.validate_theme_source_binding(
            binding_master_input(),
            taxonomy_fixture(),
            [binding_reference()],
            trusted_commit=repository_head(),
        )
        self.assertEqual(report["status"], "THEME_SOURCE_BINDING_NOT_VERIFIED")
        self.assertFalse(report["bindings"][0]["verified"])
        self.assertIn(
            "TAXONOMY_SOURCE_NOT_AUTHORIZED:"
            "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED:"
            "AUTHORITY_NOT_COMPUTABLE_NO_AUTHORITY_RECORD",
            report["bindings"][0]["failure_reasons"],
        )

    def test_unratified_graph_claim_cannot_verify_even_with_a_committed_record(self):
        # The authority record binds the graph payload, which excludes the
        # graph's own approval object.  The registry therefore still resolves,
        # and only the unratified claim stops the binding.
        graph = taxonomy_fixture("UNRATIFIED")
        repo = self.authority_repo(graph)
        report = self.check(binding_master_input(), graph, [binding_reference()], repo)
        self.assertEqual(report["status"], "THEME_SOURCE_BINDING_NOT_VERIFIED")
        self.assertEqual(
            report["taxonomy_graph_status"], "DRAFT_OR_NOT_EFFECTIVE_GRAPH"
        )
        self.assertEqual(
            report["taxonomy_authority_resolution"]["status"], "AUTHORIZED"
        )
        self.assertIn(
            "TAXONOMY_SOURCE_NOT_AUTHORIZED:DRAFT_OR_NOT_EFFECTIVE_GRAPH:AUTHORIZED",
            report["bindings"][0]["failure_reasons"],
        )

    def test_backdated_authority_is_not_usable_before_its_first_seen_commit(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph, commit_at="2026-08-25T12:00:00Z")
        report = self.check(binding_master_input(), graph, [binding_reference()], repo)
        self.assertEqual(report["status"], "THEME_SOURCE_BINDING_NOT_VERIFIED")
        self.assertIn(
            "TAXONOMY_SOURCE_NOT_AUTHORIZED:"
            "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED:"
            "AUTHORITY_NOT_COMPUTABLE_PIT_VIOLATION",
            report["bindings"][0]["failure_reasons"],
        )

    def test_asset_market_theme_evidence_and_interval_mismatches_fail_closed(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)

        cases = {
            "ASSET_ID_MISMATCH": (
                binding_master_input(),
                binding_reference(taxonomy_membership_id="MEMBERSHIP.KR.005930"),
            ),
            "GAM_ASSET_NOT_FOUND": (
                binding_master_input(),
                binding_reference(asset_id="US:XNAS:ABSENT"),
            ),
            "GAM_THEME_MEMBERSHIP_NOT_FOUND": (
                binding_master_input(),
                binding_reference(gam_membership_id="SEGMENT.ABSENT"),
            ),
            "TAXONOMY_MEMBERSHIP_NOT_FOUND": (
                binding_master_input(),
                binding_reference(taxonomy_membership_id="MEMBERSHIP.ABSENT"),
            ),
            "TAXONOMY_EVIDENCE_NOT_FOUND": (
                binding_master_input(),
                binding_reference(evidence_id="EVIDENCE.ABSENT"),
            ),
        }
        for code, (master, reference) in cases.items():
            with self.subTest(code=code):
                report = self.check(master, graph, [reference], repo)
                self.assertEqual(report["status"], "THEME_SOURCE_BINDING_NOT_VERIFIED")
                self.assertTrue(
                    any(
                        item.startswith(code)
                        for item in report["bindings"][0]["failure_reasons"]
                    ),
                    report["bindings"][0]["failure_reasons"],
                )

        theme = binding_master_input()
        bound_membership(theme)["membership_id"] = "SEGMENT.POWER"
        report = self.check(
            theme, graph, [binding_reference(gam_membership_id="SEGMENT.POWER")], repo
        )
        self.assertTrue(
            any(
                item.startswith("THEME_IDENTITY_MISMATCH")
                for item in report["bindings"][0]["failure_reasons"]
            ),
            report["bindings"][0]["failure_reasons"],
        )

        interval = binding_master_input()
        bound_membership(interval)["valid_to"] = "2026-12-01"
        report = self.check(interval, graph, [binding_reference()], repo)
        self.assertTrue(
            any(
                item.startswith("EFFECTIVE_INTERVAL_MISMATCH")
                for item in report["bindings"][0]["failure_reasons"]
            ),
            report["bindings"][0]["failure_reasons"],
        )

        for field, value in (
            ("source_sha256", "d" * 64),
            ("source_url", "https://www.sec.gov/Archives/edgar/data/1/other.htm"),
        ):
            with self.subTest(field=field):
                master = binding_master_input()
                bound_membership(master)["source_identity"][field] = value
                report = self.check(master, graph, [binding_reference()], repo)
                self.assertIn(
                    f"SOURCE_EVIDENCE_MISMATCH:{field}",
                    report["bindings"][0]["failure_reasons"],
                )

    def test_future_membership_and_ambiguous_membership_are_never_selected(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)

        future = binding_master_input()
        bound_membership(future)["valid_from"] = "2026-09-01"
        report = self.check(future, graph, [binding_reference()], repo)
        self.assertIn(
            "GAM_MEMBERSHIP_NOT_ACTIVE:2026-08-20",
            report["bindings"][0]["failure_reasons"],
        )

        ambiguous = binding_master_input()
        bound = next(
            row for row in ambiguous["records"] if row["asset_id"] == "US:XNAS:TEST"
        )
        bound["memberships"].append(
            {
                "membership_type": "THEME",
                "membership_id": "SEGMENT.COMPUTE",
                "valid_from": "2020-01-01",
                "valid_to": "2026-08-20",
                "source_identity": bound_theme_source(),
            }
        )
        report = self.check(ambiguous, graph, [binding_reference()], repo)
        self.assertIn(
            "GAM_THEME_MEMBERSHIP_AMBIGUOUS:SEGMENT.COMPUTE",
            report["bindings"][0]["failure_reasons"],
        )
        self.assertIsNone(report["bindings"][0]["master_reference"])

    def test_both_sides_use_production_validators_and_reject_rehashed_forgery(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)

        packet = GAM.build_master(binding_master_input())
        forged = copy.deepcopy(packet)
        bound = next(
            row for row in forged["records"] if row["asset_id"] == "US:XNAS:TEST"
        )
        bound["active_memberships"] = [
            row for row in bound["active_memberships"] if row["membership_type"] != "THEME"
        ]
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "OUTPUT_RECORD_DERIVATION_MISMATCH"
        ):
            self.check(rehash(forged), graph, [binding_reference()], repo)

        # A validated packet is an accepted master source; a taxonomy *packet*
        # is not a graph source, so a pre-authorized claim cannot be injected.
        report = self.check(packet, graph, [binding_reference()], repo)
        self.assertEqual(report["status"], "THEME_SOURCE_BINDING_VERIFIED")
        self.assertEqual(report["bindings"][0]["failure_reasons"], [])
        with self.assertRaisesRegex(GAM.AssetMasterError, "TAXONOMY_SOURCE_INVALID"):
            self.check(
                binding_master_input(),
                TT.build_packet(taxonomy_fixture()),
                [binding_reference()],
                repo,
            )
        with self.assertRaisesRegex(GAM.AssetMasterError, "MASTER_SOURCE_SCHEMA_UNKNOWN"):
            self.check({"schema_version": "forged/1"}, graph, [binding_reference()], repo)

    def test_distinct_disclosure_ids_are_not_aliases_even_with_same_document(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        master = binding_master_input()
        bound_membership(master)["source_identity"]["source_id"] = "microsoft_sec_issuer_disclosure"
        report = self.check(master, graph, [binding_reference()], repo)
        binding = report["bindings"][0]
        self.assertEqual(binding["source_id_comparison"], "COMPARED")
        self.assertFalse(binding["verified"])
        self.assertEqual(report["verified_binding_count"], 0)
        self.assertEqual(binding["failure_reasons"], ["SOURCE_EVIDENCE_MISMATCH:source_id"])
        self.assertEqual(binding["unresolved_reasons"], [])
        self.assertEqual(binding["master_reference"]["source_identity"]["source_id"], "microsoft_sec_issuer_disclosure")
        self.assertEqual(binding["taxonomy_reference"]["evidence"]["source_identity"]["source_id"], "sec_edgar")
        absent = self.check(binding_master_input(), graph,
                            [binding_reference(evidence_id="EVIDENCE.ABSENT")], repo)["bindings"][0]
        self.assertEqual(absent["source_id_comparison"], "NOT_EVALUATED")
        self.assertFalse(absent["verified"])

    def test_binding_reference_must_be_explicit_and_complete(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        master = binding_master_input()

        with self.assertRaisesRegex(GAM.AssetMasterError, "BINDINGS_EMPTY"):
            self.check(master, graph, [], repo)
        with self.assertRaisesRegex(GAM.AssetMasterError, "BINDINGS_NOT_LIST"):
            self.check(master, graph, binding_reference(), repo)
        for field in GAM.BINDING_REFERENCE_FIELDS:
            with self.subTest(field=field):
                partial = binding_reference()
                del partial[field]
                with self.assertRaisesRegex(
                    GAM.AssetMasterError, "BINDING_REFERENCE_FIELDS_MISMATCH"
                ):
                    self.check(master, graph, [partial], repo)
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "BINDING_REFERENCE_FIELDS_MISMATCH"
        ):
            self.check(master, graph, [binding_reference(theme_id="SEGMENT.COMPUTE")], repo)
        with self.assertRaisesRegex(
            GAM.AssetMasterError, "BINDING_REFERENCE_VALUE_INVALID"
        ):
            self.check(master, graph, [binding_reference(evidence_id="")], repo)

    def test_binding_check_mutates_nothing_and_leaves_legacy_output_unchanged(self):
        graph = taxonomy_fixture()
        repo = self.authority_repo(graph)
        master = binding_master_input()
        master_before = GAM.canonical_json(master)
        graph_before = GAM.canonical_json(graph)
        registry_before = repo.registry_path.read_bytes()
        evidence_before = repo.evidence_path.read_bytes()
        tracked_before = (ROOT / "config" / "universe.json").read_bytes()
        legacy_before = GAM.canonical_json(GAM.build_master(sample_input()))

        self.check(master, graph, [binding_reference()], repo)

        self.assertEqual(GAM.canonical_json(master), master_before)
        self.assertEqual(GAM.canonical_json(graph), graph_before)
        self.assertEqual(repo.registry_path.read_bytes(), registry_before)
        self.assertEqual(repo.evidence_path.read_bytes(), evidence_before)
        self.assertEqual((ROOT / "config" / "universe.json").read_bytes(), tracked_before)
        self.assertEqual(
            GAM.canonical_json(GAM.build_master(sample_input())), legacy_before
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
