"""P2-01 external Theme / Value-Chain taxonomy contract regression."""
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
MODULE_PATH = ROOT / "rotation" / "theme_taxonomy.py"
SPEC = importlib.util.spec_from_file_location("theme_taxonomy", MODULE_PATH)
TT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(TT)


def source(source_id: str, url: str, marker: str) -> dict:
    return {
        "source_id": source_id,
        "source_url": url,
        "source_sha256": marker * 64,
        "available_at": "2026-08-18",
        "retrieved_at_utc": "2026-08-18T12:00:00Z",
    }


def evidence(evidence_id: str, market: str, marker: str) -> dict:
    if market == "US":
        identity = source(
            "sec_edgar",
            f"https://www.sec.gov/Archives/edgar/data/1/{evidence_id}.htm",
            marker,
        )
    else:
        identity = source(
            "dart_open_api",
            f"https://opendart.fss.or.kr/api/{evidence_id}.json",
            marker,
        )
    return {
        "evidence_id": evidence_id,
        "claim_text": f"Source-linked taxonomy evidence {evidence_id}",
        "source_identity": identity,
        "audit_provenance": {
            "claim_selector": f"section:{evidence_id}",
            "review_status": "HUMAN_RATIFIED_INPUT",
        },
    }


def node(theme_id: str, node_type: str, name: str) -> dict:
    return {
        "theme_id": theme_id,
        "display_name": name,
        "description": f"Externally supplied description for {name}",
        "node_type": node_type,
        "valid_from": "2026-01-01",
        "valid_to": None,
    }


def fixture(status: str = "RATIFIED") -> dict:
    ratified = status == "RATIFIED"
    return {
        "schema_version": "theme_taxonomy_input/1",
        "taxonomy_id": "TAXONOMY.GLOBAL.2026",
        "as_of_date": "2026-08-20",
        "approval": {
            "approval_status": status,
            "decision_id": "DECISION.P2.01",
            "decision_sha256": "a" * 64,
            "ratified_by": "Atlas CIO" if ratified else None,
            "ratified_at_utc": "2026-08-19T12:00:00Z" if ratified else None,
            "effective_from": "2026-08-20",
            "effective_to": None,
        },
        "nodes": [
            node("THEME.AI_INFRA", "THEME", "AI Infrastructure"),
            node("SEGMENT.COMPUTE", "VALUE_CHAIN_SEGMENT", "Compute"),
            node("SEGMENT.POWER", "VALUE_CHAIN_SEGMENT", "Power"),
        ],
        "edges": [
            {
                "edge_id": "EDGE.AI.COMPUTE",
                "from_theme_id": "THEME.AI_INFRA",
                "to_theme_id": "SEGMENT.COMPUTE",
                "relation_type": "CONTAINS",
                "rationale": "External graph places Compute in AI Infrastructure",
                "valid_from": "2026-01-01",
                "valid_to": None,
            },
            {
                "edge_id": "EDGE.AI.POWER",
                "from_theme_id": "THEME.AI_INFRA",
                "to_theme_id": "SEGMENT.POWER",
                "relation_type": "CONTAINS",
                "rationale": "External graph places Power in AI Infrastructure",
                "valid_from": "2026-01-01",
                "valid_to": None,
            },
        ],
        "memberships": [
            {
                "membership_id": "MEMBERSHIP.US.TEST",
                "asset_id": "US:XNAS:TEST",
                "market": "US",
                "theme_id": "SEGMENT.COMPUTE",
                "role_id": "COMPUTE_VENDOR",
                "valid_from": "2026-08-20",
                "valid_to": None,
                "evidence": [evidence("EVIDENCE.US.TEST", "US", "b")],
            },
            {
                "membership_id": "MEMBERSHIP.KR.005930",
                "asset_id": "KR:XKRX:005930",
                "market": "KOREA",
                "theme_id": "SEGMENT.POWER",
                "role_id": "POWER_SUPPLIER",
                "valid_from": "2026-08-20",
                "valid_to": None,
                "evidence": [evidence("EVIDENCE.KR.005930", "KOREA", "c")],
            },
        ],
    }


class ThemeTaxonomyTests(unittest.TestCase):
    def test_effective_ratified_cross_market_graph_emits_detached_adapter(self):
        packet = TT.build_packet(fixture())
        self.assertEqual(packet["graph_status"], "EFFECTIVE_RATIFIED_GRAPH")
        self.assertTrue(packet["theme_membership_authorized"])
        self.assertEqual(packet["covered_markets"], ["KOREA", "US"])
        self.assertEqual(packet["node_count"], 3)
        self.assertEqual(packet["edge_count"], 2)
        self.assertEqual(packet["membership_count"], 2)
        self.assertEqual(
            [item["asset_id"] for item in packet["global_asset_master_membership_adapter"]],
            ["KR:XKRX:005930", "US:XNAS:TEST"],
        )
        for item in packet["global_asset_master_membership_adapter"]:
            self.assertEqual(
                item["adapter_status"],
                "DETACHED_REQUIRES_SEPARATE_MASTER_INGESTION",
            )
            self.assertEqual(item["membership_type"], "THEME")
            self.assertEqual(item["taxonomy_decision"]["decision_id"], "DECISION.P2.01")

    def test_unratified_graph_is_inspectable_but_never_authorized(self):
        packet = TT.build_packet(fixture("UNRATIFIED"))
        self.assertEqual(packet["graph_status"], "DRAFT_OR_NOT_EFFECTIVE_GRAPH")
        self.assertFalse(packet["theme_membership_authorized"])
        self.assertEqual(packet["global_asset_master_membership_adapter"], [])
        self.assertEqual(packet["membership_count"], 2)
        value = fixture("UNRATIFIED")
        value["approval"]["ratified_by"] = "False proof"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "UNRATIFIED_PROOF_FORBIDDEN"):
            TT.build_packet(value)

    def test_future_effective_ratified_graph_stays_inactive(self):
        value = fixture()
        value["approval"]["effective_from"] = "2026-09-01"
        packet = TT.build_packet(value)
        self.assertEqual(packet["graph_status"], "DRAFT_OR_NOT_EFFECTIVE_GRAPH")
        self.assertFalse(packet["theme_membership_authorized"])
        self.assertEqual(packet["global_asset_master_membership_adapter"], [])

    def test_ratified_graph_requires_both_markets_and_real_graph_content(self):
        value = fixture()
        value["memberships"] = [value["memberships"][0]]
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "RATIFIED_MARKET_COVERAGE_INCOMPLETE"):
            TT.build_packet(value)
        value = fixture()
        value["edges"] = []
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "RATIFIED_GRAPH_INCOMPLETE"):
            TT.build_packet(value)

    def test_contains_cycle_and_self_reference_fail_closed(self):
        value = fixture()
        reverse_edge = copy.deepcopy(value["edges"][0])
        reverse_edge.update({
            "edge_id": "EDGE.COMPUTE.AI",
            "from_theme_id": "SEGMENT.COMPUTE",
            "to_theme_id": "THEME.AI_INFRA",
        })
        value["edges"].append(reverse_edge)
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "CONTAINS_CYCLE"):
            TT.build_packet(value)
        value = fixture()
        value["edges"][0]["to_theme_id"] = "THEME.AI_INFRA"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "EDGE_SELF_REFERENCE"):
            TT.build_packet(value)

    def test_unknown_references_and_outside_intervals_fail_closed(self):
        value = fixture()
        value["memberships"][0]["theme_id"] = "SEGMENT.UNKNOWN"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "MEMBERSHIP_THEME_UNKNOWN"):
            TT.build_packet(value)
        value = fixture()
        value["nodes"][1]["valid_to"] = "2026-08-20"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "EDGE_OUTSIDE_NODE_INTERVAL"):
            TT.build_packet(value)
        value = fixture()
        value["memberships"][0]["valid_from"] = "2025-12-31"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "MEMBERSHIP_OUTSIDE_THEME_INTERVAL"):
            TT.build_packet(value)

    def test_overlapping_duplicate_membership_interval_is_rejected(self):
        value = fixture()
        duplicate = copy.deepcopy(value["memberships"][0])
        duplicate["membership_id"] = "MEMBERSHIP.US.TEST.SECOND"
        duplicate["valid_from"] = "2026-09-01"
        value["memberships"].append(duplicate)
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "MEMBERSHIP_INTERVAL_OVERLAP"):
            TT.build_packet(value)

    def test_membership_cannot_smuggle_weight_rank_or_score(self):
        for field in ("weight", "rank", "score"):
            with self.subTest(field=field):
                value = fixture()
                value["memberships"][0][field] = 1
                with self.assertRaisesRegex(TT.ThemeTaxonomyError, "MEMBERSHIP_FIELDS_MISMATCH"):
                    TT.build_packet(value)

    def test_asset_market_prefix_and_market_source_allowlist_are_enforced(self):
        value = fixture()
        value["memberships"][0]["asset_id"] = "KR:XKRX:000001"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "ASSET_MARKET_PREFIX_MISMATCH"):
            TT.build_packet(value)
        value = fixture()
        identity = value["memberships"][0]["evidence"][0]["source_identity"]
        identity.update({
            "source_id": "dart_open_api",
            "source_url": "https://opendart.fss.or.kr/api/list.json",
        })
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "SOURCE_ID_NOT_ALLOWED"):
            TT.build_packet(value)

    def test_source_host_hash_and_ratification_cutoff_are_enforced(self):
        value = fixture()
        value["memberships"][0]["evidence"][0]["source_identity"]["source_url"] = (
            "https://example.com/not-sec"
        )
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "SOURCE_URL_INVALID"):
            TT.build_packet(value)
        value = fixture()
        value["memberships"][0]["evidence"][0]["source_identity"]["source_sha256"] = "bad"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "SOURCE_SHA256_INVALID"):
            TT.build_packet(value)
        value = fixture()
        value["memberships"][0]["evidence"][0]["source_identity"]["retrieved_at_utc"] = (
            "2026-08-19T12:00:01Z"
        )
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "SOURCE_TEMPORAL_ORDER_INVALID"):
            TT.build_packet(value)

    def test_evidence_is_required_and_ids_must_be_unique(self):
        value = fixture()
        value["memberships"][0]["evidence"] = []
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "MEMBERSHIP_EVIDENCE_EMPTY"):
            TT.build_packet(value)
        value = fixture()
        duplicate = copy.deepcopy(value["memberships"][0]["evidence"][0])
        value["memberships"][0]["evidence"].append(duplicate)
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "EVIDENCE_ID_DUPLICATE"):
            TT.build_packet(value)

    def test_permuted_input_is_deterministic_and_digest_bound(self):
        value = fixture()
        second_evidence = evidence("EVIDENCE.US.TEST.SECOND", "US", "d")
        value["memberships"][0]["evidence"].append(second_evidence)
        first = TT.build_packet(value)
        value["nodes"].reverse()
        value["edges"].reverse()
        value["memberships"].reverse()
        value["memberships"][1]["evidence"].reverse()
        second = TT.build_packet(value)
        self.assertEqual(first, second)
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, TT.payload_sha256(second))

    def test_authority_and_policy_boundaries_remain_closed(self):
        packet = TT.build_packet(fixture())
        self.assertTrue(packet["authority"]["ratified_graph_validation_only"])
        for field, value in packet["authority"].items():
            if field != "ratified_graph_validation_only":
                self.assertFalse(value, field)
        self.assertEqual(packet["policy_status"]["repository_default_taxonomy"], "ABSENT")
        self.assertEqual(packet["policy_status"]["source_hierarchy"], "UNRATIFIED")
        self.assertEqual(packet["policy_status"]["rotation_scoring"], "UNRATIFIED")
        self.assertIn("GLOBAL_ASSET_MASTER_INGESTION_NOT_IMPLEMENTED", packet["unresolved_boundaries"])

    def test_contract_and_input_are_exact_and_no_default_taxonomy_exists(self):
        contract = TT.load_contract()
        contract["authority"]["rotation_score_authorized"] = True
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "CONTRACT_FIELD_MISMATCH"):
            TT.build_packet(fixture(), contract=contract)
        value = fixture()
        value["default_taxonomy"] = "forbidden"
        with self.assertRaisesRegex(TT.ThemeTaxonomyError, "INPUT_FIELDS_MISMATCH"):
            TT.build_packet(value)
        self.assertFalse((ROOT / "config" / "theme_taxonomy.json").exists())

    def test_cli_is_temp_only_atomic_and_rejects_tracked_output(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            output_path = temp / "output.json"
            input_path.write_text(json.dumps(fixture()), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--out", str(output_path)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text()), TT.build_packet(fixture()))
            output_path.write_text("sentinel\n", encoding="utf-8")
            input_path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(TT.run(input_path, output_path), 1)
            self.assertEqual(output_path.read_text(), "sentinel\n")
        tracked = ROOT / ".test-theme-taxonomy-tracked-output.json"
        self.assertFalse(tracked.exists())
        with tempfile.TemporaryDirectory() as raw:
            input_path = Path(raw) / "input.json"
            input_path.write_text(json.dumps(fixture()), encoding="utf-8")
            self.assertEqual(TT.run(input_path, tracked), 1)
        self.assertFalse(tracked.exists())


if __name__ == "__main__":
    unittest.main()
