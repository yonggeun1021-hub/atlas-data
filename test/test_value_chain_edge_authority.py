"""P2-01 cross-market Value-Chain EDGE authority contract regression.

Focused/adjacent to the module under test: exercises the new edge authority
layer plus its reuse of the existing, already-hardened theme_taxonomy/2 and
theme_taxonomy_authority_registry/1 mechanisms. Does not re-test theme graph
validation itself (covered by test_theme_taxonomy.py /
test_theme_taxonomy_authority.py) -- only that this module correctly refuses
to redefine or bypass it.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rotation import value_chain_edge_authority as VCEA

_TTA_SPEC = importlib.util.spec_from_file_location(
    "atlas_theme_taxonomy_authority_fixture",
    ROOT / "test" / "test_theme_taxonomy_authority.py",
)
assert _TTA_SPEC is not None and _TTA_SPEC.loader is not None
_TTA_MODULE = importlib.util.module_from_spec(_TTA_SPEC)
_TTA_SPEC.loader.exec_module(_TTA_MODULE)
TTA = _TTA_MODULE.TTA
AuthorityRepo = _TTA_MODULE.AuthorityRepo
theme_fixture = _TTA_MODULE.fixture
TT = _TTA_MODULE.TT

_THEME_FIXTURE_SPEC = importlib.util.spec_from_file_location(
    "atlas_theme_taxonomy_input_fixture",
    ROOT / "test" / "test_theme_taxonomy.py",
)
assert _THEME_FIXTURE_SPEC is not None and _THEME_FIXTURE_SPEC.loader is not None
_THEME_FIXTURE_MODULE = importlib.util.module_from_spec(_THEME_FIXTURE_SPEC)
_THEME_FIXTURE_SPEC.loader.exec_module(_THEME_FIXTURE_MODULE)
source = _THEME_FIXTURE_MODULE.source


def canonical_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def build_authorized_theme_packet(root: Path) -> dict:
    """A real, fully AUTHORIZED theme_taxonomy_packet/2 -- built the same way
    P2-01's own tests build one; nothing here is fabricated."""
    graph = theme_fixture()
    repo = AuthorityRepo(root, graph)
    return TT.build_packet(graph, authority_registry_path=repo.registry_path)


def build_unratified_theme_packet() -> dict:
    return TT.build_packet(theme_fixture())


def node_ref(node_ref_id: str, market: str, asset_id: str, membership_id: str, packet: dict,
             membership_source: str = "theme_taxonomy_packet/2") -> dict:
    return {
        "node_ref_id": node_ref_id, "market": market,
        "membership_source": membership_source, "asset_id": asset_id,
        "membership_id": membership_id, "membership_packet": packet,
    }


def edge_claim(edge_id: str, left: str, right: str, relation: str = "SUPPLIES",
               valid_from: str = "2026-08-21", valid_to=None) -> dict:
    return {
        "edge_id": edge_id, "from_node_ref_id": left, "to_node_ref_id": right,
        "relation_type": relation,
        "evidence": {
            "evidence_id": f"EVIDENCE.{edge_id}",
            "claim_text": f"External value-chain evidence for {edge_id}",
            "source_identity": source(
                "sec_edgar", f"https://www.sec.gov/Archives/edgar/data/1/{edge_id}.htm", "d",
            ),
        },
        "valid_from": valid_from, "valid_to": valid_to,
    }


def edge_input(node_refs: list, edges: list, graph_id: str = "GRAPH.VALUE_CHAIN.TEST",
               as_of_date: str = "2026-08-22") -> dict:
    return {
        "schema_version": VCEA.INPUT_SCHEMA_VERSION, "graph_id": graph_id,
        "as_of_date": as_of_date, "node_refs": node_refs, "edges": edges,
    }


class EdgeAuthorityRepo:
    """Same git-provenance authority pattern as AuthorityRepo, scoped to one
    edge record instead of a whole taxonomy graph."""

    def __init__(self, root: Path, edge_hash: str, edge_id: str, *, status: str = "RATIFIED",
                 effective_from: str = "2026-08-01T00:00:00Z", effective_to=None,
                 commit_at: str = "2026-08-05T00:00:00Z"):
        self.root = root
        self.registry_path = root / "config" / "value_chain_edge_authority_registry.json"
        self.evidence_path = root / "evidence" / "approvals" / "value-chain-edge.json"
        self.registry_path.parent.mkdir(parents=True)
        self.evidence_path.parent.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.name", "Atlas Test"], check=True)
        subprocess.run(["git", "-C", str(root), "config", "user.email", "atlas@example.invalid"], check=True)
        determining = {
            "rule_id": "RULE.VALUE_CHAIN_EDGE.TEST", "rule_version": "VERSION.1",
            "approval_status": status, "ratified_at": "2026-08-02T00:00:00Z",
            "effective_from": effective_from, "effective_to": effective_to,
            "edge_id": edge_id, "approved_edge_payload_sha256": edge_hash,
        }
        evidence = {
            "schema_version": VCEA.EVIDENCE_SCHEMA,
            "approved_full_payload_sha256": TTA.payload_sha256(determining),
            **determining,
        }
        evidence_bytes = canonical_bytes(evidence)
        self.evidence_path.write_bytes(evidence_bytes)
        self.record = {
            **determining,
            "approval_evidence_ref": "evidence/approvals/value-chain-edge.json",
            "approval_evidence_sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        }
        self.write_registry([self.record])
        self.commit("edge-authority", commit_at)

    def write_registry(self, records: list[dict]):
        self.registry_path.write_bytes(canonical_bytes({
            "schema_version": VCEA.REGISTRY_SCHEMA, "records": records,
        }))

    def commit(self, message: str, when: str):
        subprocess.run(["git", "-C", str(self.root), "add", "."], check=True)
        env = dict(os.environ, GIT_AUTHOR_DATE=when, GIT_COMMITTER_DATE=when)
        subprocess.run(["git", "-C", str(self.root), "commit", "-q", "-m", message], check=True, env=env)


class ValueChainEdgeAuthorityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.temp_root = Path(temp.name)
        self.authorized_packet = build_authorized_theme_packet(self.temp_root / "theme-repo")
        self.node_us = node_ref(
            "NODE.US.TEST", "US", "US:XNAS:TEST", "MEMBERSHIP.US.TEST", self.authorized_packet,
        )
        self.node_kr = node_ref(
            "NODE.KR.005930", "KOREA", "KR:XKRX:005930", "MEMBERSHIP.KR.005930", self.authorized_packet,
        )

    # -- contract -----------------------------------------------------
    def test_contract_matches_expected_shape(self):
        contract = VCEA.load_contract()
        self.assertEqual(contract["contract_version"], "value_chain_edge_authority/1")
        self.assertEqual(set(contract["allowed_markets"]), {"CRYPTO", "KOREA", "US"})
        self.assertFalse(contract["authority"]["trading_authorized"])
        self.assertFalse(contract["authority"]["rotation_score_authorized"])
        self.assertFalse(contract["authority"]["node_membership_redefinition_authorized"])

    # -- node references ------------------------------------------------
    def test_node_ref_resolves_ratified_membership_from_authorized_packet(self):
        packet = VCEA.build_packet(edge_input([self.node_us, self.node_kr], []))
        by_id = {row["node_ref_id"]: row["resolution"] for row in packet["node_refs"]}
        self.assertEqual(by_id["NODE.US.TEST"]["status"], "RATIFIED_MARKET_NATIVE_MEMBERSHIP")
        self.assertEqual(by_id["NODE.KR.005930"]["status"], "RATIFIED_MARKET_NATIVE_MEMBERSHIP")
        self.assertIsNotNone(by_id["NODE.US.TEST"]["real_usable_from"])

    def test_node_ref_stays_unknown_when_underlying_packet_not_authorized(self):
        unratified = build_unratified_theme_packet()
        self.assertFalse(unratified["theme_membership_authorized"])
        node = node_ref("NODE.US.UNAUTH", "US", "US:XNAS:TEST", "MEMBERSHIP.US.TEST", unratified)
        packet = VCEA.build_packet(edge_input([node], []))
        self.assertEqual(
            packet["node_refs"][0]["resolution"]["status"],
            "UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED",
        )

    def test_node_ref_packet_tamper_is_rejected(self):
        tampered = copy.deepcopy(self.authorized_packet)
        tampered["theme_membership_authorized"] = True
        tampered["node_count"] = 999  # payload_sha256 no longer matches
        node = node_ref("NODE.US.TAMPER", "US", "US:XNAS:TEST", "MEMBERSHIP.US.TEST", tampered)
        packet = VCEA.build_packet(edge_input([node], []))
        self.assertEqual(
            packet["node_refs"][0]["resolution"]["status"],
            "UNKNOWN_MEMBERSHIP_PACKET_TAMPERED_OR_MALFORMED",
        )

    def test_node_ref_unsupported_membership_source_fails_closed_not_fabricated(self):
        """Crypto is a structurally allowed market with no wired membership
        source in this slice -- it must fail closed, never be inferred."""
        node = node_ref(
            "NODE.CRYPTO.BTC", "CRYPTO", "CRYPTO:BTC", "MEMBERSHIP.CRYPTO.BTC",
            self.authorized_packet, membership_source="crypto_asset_taxonomy/1",
        )
        packet = VCEA.build_packet(edge_input([node], []))
        self.assertEqual(
            packet["node_refs"][0]["resolution"]["status"],
            "UNKNOWN_MEMBERSHIP_SOURCE_NOT_SUPPORTED",
        )

    def test_node_ref_id_must_be_a_stable_token_not_a_label_tuple(self):
        bad = node_ref("bad id with spaces", "US", "US:XNAS:TEST", "MEMBERSHIP.US.TEST", self.authorized_packet)
        with self.assertRaises(VCEA.ValueChainEdgeAuthorityError):
            VCEA.build_packet(edge_input([bad], []))

    def test_duplicate_node_ref_id_rejected(self):
        with self.assertRaises(VCEA.ValueChainEdgeAuthorityError):
            VCEA.build_packet(edge_input([self.node_us, dict(self.node_us)], []))

    # -- edges: fail-closed and partial graph ---------------------------
    def test_edge_fails_closed_when_one_side_lacks_ratified_membership(self):
        unratified = build_unratified_theme_packet()
        unauth_node = node_ref("NODE.US.UNAUTH", "US", "US:XNAS:TEST", "MEMBERSHIP.US.TEST", unratified)
        edge = edge_claim("EDGE.PARTIAL", unauth_node["node_ref_id"], self.node_kr["node_ref_id"])
        packet = VCEA.build_packet(edge_input([unauth_node, self.node_kr], [edge]))
        self.assertEqual(
            packet["edges"][0]["edge_status"], "UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED",
        )
        self.assertFalse(packet["edges"][0]["edge_activation_authorized"])
        self.assertIsNone(packet["edges"][0]["authority_resolution"])

    def test_partial_graph_with_mixed_status_is_not_an_error(self):
        """UNKNOWN is an expected steady-state value; the packet still builds."""
        unratified = build_unratified_theme_packet()
        unauth_node = node_ref("NODE.US.UNAUTH", "US", "US:XNAS:TEST", "MEMBERSHIP.US.TEST", unratified)
        good_edge = edge_claim("EDGE.GOOD", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        unknown_edge = edge_claim("EDGE.UNKNOWN", unauth_node["node_ref_id"], self.node_kr["node_ref_id"])
        packet = VCEA.build_packet(
            edge_input([self.node_us, self.node_kr, unauth_node], [good_edge, unknown_edge]),
        )
        self.assertEqual(packet["edge_count"], 2)
        statuses = {row["edge_id"]: row["edge_status"] for row in packet["edges"]}
        self.assertEqual(statuses["EDGE.GOOD"], "UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED")
        self.assertEqual(statuses["EDGE.UNKNOWN"], "UNKNOWN_MARKET_NATIVE_MEMBERSHIP_NOT_RATIFIED")

    def test_edge_interval_outside_node_membership_window_is_unknown(self):
        edge = edge_claim("EDGE.EARLY", self.node_us["node_ref_id"], self.node_kr["node_ref_id"],
                           valid_from="2020-01-01")
        packet = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        self.assertEqual(
            packet["edges"][0]["edge_status"], "UNKNOWN_INTERVAL_OUTSIDE_NODE_MEMBERSHIP_WINDOW",
        )

    def test_edge_invalid_evidence_source_is_rejected(self):
        edge = edge_claim("EDGE.BADSRC", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        edge["evidence"]["source_identity"]["source_id"] = "not_a_real_source"
        with self.assertRaises(VCEA.ValueChainEdgeAuthorityError):
            VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))

    def test_edge_self_reference_rejected(self):
        edge = edge_claim("EDGE.SELF", self.node_us["node_ref_id"], self.node_us["node_ref_id"])
        with self.assertRaises(VCEA.ValueChainEdgeAuthorityError):
            VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))

    def test_edge_id_must_be_a_stable_token(self):
        edge = edge_claim("bad edge id", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        with self.assertRaises(VCEA.ValueChainEdgeAuthorityError):
            VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))

    # -- edge authority registry: default empty & fail closed -----------
    def test_repository_edge_authority_registry_is_committed_empty(self):
        registry = VCEA.load_registry()
        self.assertEqual(registry["records"], [])

    def test_edge_registry_default_empty_leaves_edge_unratified(self):
        edge = edge_claim("EDGE.NOAUTH", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        packet = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        self.assertEqual(packet["edges"][0]["edge_status"], "UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED")
        self.assertFalse(packet["edges"][0]["edge_activation_authorized"])
        self.assertFalse(packet["authority"]["edge_activation_authorized"])

    # -- edge authority registry: ratified activation --------------------
    def test_ratified_registry_record_activates_edge_and_nothing_else(self):
        edge = edge_claim("EDGE.ACTIVATE", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        unratified_packet = VCEA.build_packet(
            edge_input([self.node_us, self.node_kr], [edge]),
        )
        edge_hash = unratified_packet["edges"][0]["approved_edge_payload_sha256"]
        repo = EdgeAuthorityRepo(self.temp_root / "edge-repo", edge_hash, "EDGE.ACTIVATE")
        packet = VCEA.build_packet(
            edge_input([self.node_us, self.node_kr], [edge]),
            registry_path=repo.registry_path,
        )
        self.assertEqual(packet["edges"][0]["edge_status"], "RATIFIED_CROSS_MARKET_VALUE_CHAIN_EDGE")
        self.assertTrue(packet["edges"][0]["edge_activation_authorized"])
        self.assertTrue(packet["authority"]["edge_activation_authorized"])
        for field, value in packet["authority"].items():
            if field not in ("edge_activation_authorized", "external_ratification_claim_validation_only"):
                self.assertFalse(value, field)

    def test_edge_content_change_no_longer_matches_authority_record(self):
        edge = edge_claim("EDGE.CHANGED", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        baseline = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        edge_hash = baseline["edges"][0]["approved_edge_payload_sha256"]
        repo = EdgeAuthorityRepo(self.temp_root / "edge-repo-2", edge_hash, "EDGE.CHANGED")
        mutated = edge_claim("EDGE.CHANGED", self.node_us["node_ref_id"], self.node_kr["node_ref_id"],
                              relation="DEPENDS_ON")
        packet = VCEA.build_packet(
            edge_input([self.node_us, self.node_kr], [mutated]), registry_path=repo.registry_path,
        )
        self.assertEqual(packet["edges"][0]["edge_status"], "UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED")

    def test_backdated_edge_authority_waits_for_real_commit_first_seen(self):
        edge = edge_claim("EDGE.PIT", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        baseline = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        edge_hash = baseline["edges"][0]["approved_edge_payload_sha256"]
        repo = EdgeAuthorityRepo(
            self.temp_root / "edge-repo-3", edge_hash, "EDGE.PIT", commit_at="2026-08-10T12:00:00Z",
        )
        before = VCEA.resolve_edge_authority(edge, edge_hash, "2026-08-09", repo.registry_path)
        same_day = VCEA.resolve_edge_authority(edge, edge_hash, "2026-08-10", repo.registry_path)
        after = VCEA.resolve_edge_authority(edge, edge_hash, "2026-08-11", repo.registry_path)
        self.assertEqual(before["status"], "AUTHORITY_NOT_COMPUTABLE_PIT_VIOLATION")
        self.assertEqual(same_day["status"], "AUTHORITY_NOT_COMPUTABLE_DATE_ONLY_PRECISION")
        self.assertEqual(after["status"], "AUTHORIZED")

    def test_edge_registry_disk_tamper_is_rejected(self):
        edge = edge_claim("EDGE.TAMPER", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        baseline = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        edge_hash = baseline["edges"][0]["approved_edge_payload_sha256"]
        repo = EdgeAuthorityRepo(self.temp_root / "edge-repo-4", edge_hash, "EDGE.TAMPER")
        value = json.loads(repo.registry_path.read_text())
        value["records"][0]["effective_to"] = "2027-01-01T00:00:00Z"
        repo.write_registry(value["records"])
        result = VCEA.resolve_edge_authority(edge, edge_hash, "2026-08-06", repo.registry_path)
        self.assertEqual(result["status"], "AUTHORITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED")

    def test_registry_schema_and_record_fields_are_closed(self):
        with self.assertRaises(VCEA.ValueChainEdgeAuthorityError):
            VCEA.validate_registry_record({"rule_id": "X"})

    # -- cross-market linkage roll-up (descriptive only) ------------------
    def test_us_korea_pair_is_reported_but_not_linked_without_ratified_edge(self):
        edge = edge_claim("EDGE.LINK", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        packet = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        self.assertEqual(packet["cross_market_edge_count"], 1)
        self.assertEqual(packet["activated_cross_market_edge_count"], 0)
        self.assertEqual(len(packet["market_pair_linkage"]), 1)
        row = packet["market_pair_linkage"][0]
        self.assertEqual(row["market_pair"], ["KOREA", "US"])
        self.assertTrue(row["cross_market"])
        self.assertEqual(row["linkage_status"], "UNKNOWN_MARKET_PAIR_LINKAGE_NOT_RATIFIED")
        self.assertEqual(row["edge_status_counts"], {"UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED": 1})

    def test_us_korea_pair_is_linked_only_once_an_edge_is_actually_ratified(self):
        edge = edge_claim("EDGE.LINKED", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        baseline = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        edge_hash = baseline["edges"][0]["approved_edge_payload_sha256"]
        repo = EdgeAuthorityRepo(self.temp_root / "edge-repo-linkage", edge_hash, "EDGE.LINKED")
        packet = VCEA.build_packet(
            edge_input([self.node_us, self.node_kr], [edge]), registry_path=repo.registry_path,
        )
        row = packet["market_pair_linkage"][0]
        self.assertEqual(row["market_pair"], ["KOREA", "US"])
        self.assertEqual(row["linkage_status"], "RATIFIED_MARKET_PAIR_LINKAGE")
        self.assertEqual(row["activated_edge_count"], 1)
        self.assertEqual(packet["activated_cross_market_edge_count"], 1)

    def test_same_market_edge_is_reported_and_never_counted_as_cross_market(self):
        second_kr = node_ref(
            "NODE.KR.005930.B", "KOREA", "KR:XKRX:005930", "MEMBERSHIP.KR.005930",
            self.authorized_packet,
        )
        edge = edge_claim("EDGE.SAME", self.node_kr["node_ref_id"], second_kr["node_ref_id"])
        # Both endpoints are KOREA, so the evidence must come from a KOREA
        # source in the existing theme_taxonomy_contract allow-list.
        edge["evidence"]["source_identity"] = source(
            "dart_open_api", "https://opendart.fss.or.kr/api/EDGE.SAME.json", "d",
        )
        packet = VCEA.build_packet(edge_input([self.node_kr, second_kr], [edge]))
        row = packet["market_pair_linkage"][0]
        self.assertEqual(row["market_pair"], ["KOREA", "KOREA"])
        self.assertFalse(row["cross_market"])
        self.assertEqual(packet["cross_market_edge_count"], 0)
        self.assertEqual(packet["activated_cross_market_edge_count"], 0)

    def test_linkage_rollup_grants_no_authority(self):
        edge = edge_claim("EDGE.NOGRANT", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        packet = VCEA.build_packet(edge_input([self.node_us, self.node_kr], [edge]))
        self.assertFalse(packet["authority"]["edge_activation_authorized"])
        self.assertFalse(packet["authority"]["rotation_score_authorized"])
        self.assertFalse(packet["authority"]["trading_authorized"])

    # -- CLI: operable, and still no tracked output path -----------------
    def test_cli_writes_packet_for_an_external_edge_document(self):
        edge = edge_claim("EDGE.CLI", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        document = edge_input([self.node_us, self.node_kr], [edge])
        input_path = self.temp_root / "cli" / "edges.json"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        out_path = self.temp_root / "cli" / "packet.json"
        self.assertEqual(VCEA.run(input_path, out_path), 0)
        packet = json.loads(out_path.read_text(encoding="utf-8"))
        self.assertEqual(packet["schema_version"], VCEA.OUTPUT_SCHEMA_VERSION)
        self.assertEqual(packet["edges"][0]["edge_status"], "UNKNOWN_EDGE_AUTHORITY_NOT_RATIFIED")
        self.assertEqual(packet["market_pair_linkage"][0]["market_pair"], ["KOREA", "US"])

    def test_cli_refuses_to_write_a_tracked_repository_path(self):
        edge = edge_claim("EDGE.CLI2", self.node_us["node_ref_id"], self.node_kr["node_ref_id"])
        document = edge_input([self.node_us, self.node_kr], [edge])
        input_path = self.temp_root / "cli2" / "edges.json"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        tracked = ROOT / "config" / "value_chain_edge_cli_output_must_not_exist.json"
        self.assertEqual(VCEA.run(input_path, tracked), 1)
        self.assertFalse(tracked.exists())

    def test_cli_reports_malformed_input_without_writing_output(self):
        input_path = self.temp_root / "cli3" / "edges.json"
        input_path.parent.mkdir(parents=True, exist_ok=True)
        input_path.write_text(json.dumps({"schema_version": "wrong/1"}), encoding="utf-8")
        out_path = self.temp_root / "cli3" / "packet.json"
        self.assertEqual(VCEA.run(input_path, out_path), 1)
        self.assertFalse(out_path.exists())


if __name__ == "__main__":
    unittest.main()
