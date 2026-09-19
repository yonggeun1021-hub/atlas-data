"""P2-03 policy-gated Korea Theme capital-rotation regression."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "rotation" / "korea_capital_rotation.py"
UPSTREAM_PATH = ROOT / ".github" / "scripts" / "korea_leadership.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("korea_capital_rotation", MODULE_PATH)
KCR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(KCR)

# The exact same producer module instance the consumer itself resolves, so a
# reference packet built here is the real P2-01 output, not a look-alike.
from rotation import theme_taxonomy as TT  # noqa: E402

UPSTREAM_SPEC = importlib.util.spec_from_file_location("korea_leadership_for_rotation", UPSTREAM_PATH)
KL = importlib.util.module_from_spec(UPSTREAM_SPEC)
assert UPSTREAM_SPEC.loader is not None
UPSTREAM_SPEC.loader.exec_module(KL)

TAXONOMY_DECISION_SHA = "a" * 64
TAXONOMY_PACKET_SHA = "b" * 64

KOSPI_THEMES = ["11::KOSPI_반도체", "12::KOSPI_바이오", "13::KOSPI_방산"]
KOSDAQ_THEMES = ["21::KOSDAQ_반도체", "22::KOSDAQ_바이오", "23::KOSDAQ_로봇"]
THEME_IDS = {
    "11::KOSPI_반도체": "THEME.KR.KOSPI.SEMICONDUCTOR",
    "12::KOSPI_바이오": "THEME.KR.KOSPI.BIO",
    "13::KOSPI_방산": "THEME.KR.KOSPI.DEFENSE",
    "21::KOSDAQ_반도체": "THEME.KR.KOSDAQ.SEMICONDUCTOR",
    "22::KOSDAQ_바이오": "THEME.KR.KOSDAQ.BIO",
    "23::KOSDAQ_로봇": "THEME.KR.KOSDAQ.ROBOTICS",
}


TAXONOMY_ID = "TAXONOMY.KR.ROTATION.TEST"
TAXONOMY_DECISION_ID = "DECISION.P2.01.KR.TEST"
TAXONOMY_GRAPH_DECISION_SHA = "e" * 64
ROOT_THEME_ID = "THEME.KR.ROTATION.ROOT"


def taxonomy_evidence(evidence_id: str, market: str, marker: str) -> dict:
    host = "www.sec.gov" if market == "US" else "opendart.fss.or.kr"
    source_id = "sec_edgar" if market == "US" else "dart_open_api"
    return {
        "evidence_id": evidence_id,
        "claim_text": f"Source-linked taxonomy evidence {evidence_id}",
        "source_identity": {
            "source_id": source_id,
            "source_url": f"https://{host}/atlas-test/{evidence_id}.json",
            "source_sha256": marker * 64,
            "available_at": "2026-08-18",
            "retrieved_at_utc": "2026-08-18T12:00:00Z",
        },
        "audit_provenance": {
            "claim_selector": f"section:{evidence_id}",
            "review_status": "HUMAN_RATIFIED_INPUT",
        },
    }


def taxonomy_node(theme_id: str) -> dict:
    return {
        "theme_id": theme_id,
        "display_name": theme_id,
        "description": f"Externally supplied description for {theme_id}",
        "node_type": "THEME",
        "valid_from": "2026-01-01",
        "valid_to": None,
    }


def taxonomy_graph_document(as_of_date: str = "2026-08-20", theme_ids=None) -> dict:
    """A real ``theme_taxonomy_input/1`` graph document.

    This is a synthetic external graph, exactly like every other P2-01 input:
    the repository still ships no default taxonomy, and the committed approval
    authority registry is still empty, so the real producer will resolve this
    graph as a structurally valid ratification *claim* that is NOT authorized.
    """
    theme_ids = sorted(THEME_IDS.values()) if theme_ids is None else sorted(theme_ids)
    return {
        "schema_version": "theme_taxonomy_input/1",
        "taxonomy_id": TAXONOMY_ID,
        "as_of_date": as_of_date,
        "approval": {
            "approval_status": "RATIFIED",
            "decision_id": TAXONOMY_DECISION_ID,
            "decision_sha256": TAXONOMY_GRAPH_DECISION_SHA,
            "ratified_by": "Atlas CIO",
            "ratified_at_utc": "2026-08-19T12:00:00Z",
            "effective_from": "2026-08-01",
            "effective_to": None,
        },
        "nodes": [taxonomy_node(ROOT_THEME_ID)] + [
            taxonomy_node(theme_id) for theme_id in theme_ids
        ],
        "edges": [
            {
                "edge_id": f"EDGE.ROOT:{theme_id}",
                "from_theme_id": ROOT_THEME_ID,
                "to_theme_id": theme_id,
                "relation_type": "CONTAINS",
                "rationale": f"External graph places {theme_id} under the root",
                "valid_from": "2026-01-01",
                "valid_to": None,
            }
            for theme_id in theme_ids
        ],
        "memberships": [
            {
                "membership_id": "MEMBERSHIP.KR.005930",
                "asset_id": "KR:XKRX:005930",
                "market": "KOREA",
                "theme_id": ROOT_THEME_ID,
                "role_id": "KR_CONSTITUENT",
                "valid_from": "2026-08-01",
                "valid_to": None,
                "evidence": [taxonomy_evidence("EVIDENCE.KR.005930", "KOREA", "b")],
            },
            {
                "membership_id": "MEMBERSHIP.US.TEST",
                "asset_id": "US:XNAS:TEST",
                "market": "US",
                "theme_id": ROOT_THEME_ID,
                "role_id": "US_CONSTITUENT",
                "valid_from": "2026-08-01",
                "valid_to": None,
                "evidence": [taxonomy_evidence("EVIDENCE.US.TEST", "US", "c")],
            },
        ],
    }


def taxonomy_source_bytes(document: dict) -> bytes:
    """Exact bytes a caller would hand to the CLI's ``--taxonomy-graph``."""
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def taxonomy_record(identity: str, role: str, benchmark: str) -> dict:
    return {
        "series_identity": identity,
        "role": role,
        "benchmark_identity": benchmark,
        "effective_from": "2026-01-01",
        "effective_to": None,
        "reason": "synthetic effective-dated taxonomy",
    }


def write_upstream_policy(path: Path) -> Path:
    records = [
        taxonomy_record("01::KOSPI", "KOSPI_BENCHMARK", "01::KOSPI"),
        taxonomy_record("02::KOSDAQ", "KOSDAQ_BENCHMARK", "02::KOSDAQ"),
    ]
    records.extend(taxonomy_record(item, "THEME", "01::KOSPI") for item in KOSPI_THEMES)
    records.extend(taxonomy_record(item, "THEME", "02::KOSDAQ") for item in KOSDAQ_THEMES)
    value = {
        "schema_version": 1,
        "policy_version": "korea_leadership/rotation-test-v1",
        "approval_status": "RATIFIED",
        "effective_from": "2026-01-01",
        "source_name": "KRX_OPEN_API_INDEX_FIXTURE",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "allowed_run_modes": ["FORWARD_SHADOW", "HISTORICAL_REPLAY"],
        "session_calendar_source": "synthetic_xkrx_fixture/v1",
        "publication_timing_source": "synthetic_fixture_only/v1",
        "earliest_usable_time": "18:00:00",
        "lookback_sessions": 1,
        "records": records,
    }
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def upstream_payload(observation_date: str, values: dict[str, str]) -> dict:
    previous_date = "2026-08-14" if observation_date == "2026-08-18" else "2026-08-19"
    prices = {"01::KOSPI": "100", "02::KOSDAQ": "100"} | values
    return {
        "schema_version": 1,
        "source_name": "KRX_OPEN_API_INDEX_FIXTURE",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "run_mode": "FORWARD_SHADOW",
        "observation_date": observation_date,
        "fetched_at": f"{observation_date}T18:05:00+09:00",
        "available_at": f"{observation_date}T18:00:00+09:00",
        "decision_at": f"{observation_date}T18:10:00+09:00",
        "expected_session_dates": [previous_date, observation_date],
        "series_rows": [
            {
                "series_identity": identity,
                "rows": [
                    {"session_date": previous_date, "close": "100"},
                    {"session_date": observation_date, "close": close},
                ],
            }
            for identity, close in sorted(prices.items())
        ],
    }


def rehash(packet: dict) -> None:
    packet.pop("payload_sha256", None)
    packet["payload_sha256"] = KL.canonical_payload_sha256(packet)


def rehash_output(packet: dict) -> None:
    packet.pop("payload_sha256", None)
    packet["payload_sha256"] = KCR.payload_sha256(packet)


def breadth_market(
    lineage_sha256=None, as_of_date=None, source_available_at=None,
    *, captured_at=None, first_seen_at=None, capture_mode=None,
) -> dict:
    return {
        "lineage_sha256": lineage_sha256,
        "as_of_date": as_of_date,
        "source_available_at": source_available_at,
        "captured_at": captured_at,
        "first_seen_at": first_seen_at,
        "capture_mode": capture_mode,
    }


def forward_live_market(lineage_sha256, as_of_date, first_seen_at) -> dict:
    """A genuine forward_live capture with no verified official
    publication timing -- first_seen_at is the only real evidence."""
    return breadth_market(
        lineage_sha256, as_of_date,
        captured_at=first_seen_at, first_seen_at=first_seen_at,
        capture_mode="forward_live",
    )


def breadth_context(
    status: str, decision_eligible: bool, *, kosdaq=None, kospi=None, freshness_limit_days=3
) -> dict:
    return {
        "status": status,
        "markets": {
            "KOSDAQ": kosdaq or breadth_market(),
            "KOSPI": kospi or breadth_market(),
        },
        "freshness_limit_days": freshness_limit_days,
        "ranking_input_authorized": False,
        "decision_eligible": decision_eligible,
    }


def make_bundle(taxonomy_document: dict | None = None) -> tuple[dict, dict]:
    """Legacy opaque `/1` bundle, or -- with a graph document -- a bundle whose
    binding declares the real `theme_taxonomy/2` identity actually derived by
    the producer from that exact document."""
    if taxonomy_document is None:
        taxonomy_contract_version = "theme_taxonomy/1"
        taxonomy_id = "TAXONOMY.GLOBAL.2026"
        decision_id = "DECISION.P2.01"
        decision_sha = TAXONOMY_DECISION_SHA
        packet_sha = TAXONOMY_PACKET_SHA
    else:
        reference = TT.build_packet(copy.deepcopy(taxonomy_document))
        taxonomy_contract_version = reference["contract_version"]
        taxonomy_id = reference["taxonomy_id"]
        decision_id = reference["approval"]["decision_id"]
        decision_sha = reference["approval"]["decision_sha256"]
        packet_sha = reference["payload_sha256"]
    with tempfile.TemporaryDirectory() as raw:
        policy_path = write_upstream_policy(Path(raw) / "leadership-policy.json")
        prior_values = {
            "11::KOSPI_반도체": "130", "12::KOSPI_바이오": "110", "13::KOSPI_방산": "90",
            "21::KOSDAQ_반도체": "105", "22::KOSDAQ_바이오": "120", "23::KOSDAQ_로봇": "80",
        }
        current_values = {
            "11::KOSPI_반도체": "110", "12::KOSPI_바이오": "140", "13::KOSPI_방산": "80",
            "21::KOSDAQ_반도체": "130", "22::KOSDAQ_바이오": "110", "23::KOSDAQ_로봇": "90",
        }
        prior = KL.build_transform(upstream_payload("2026-08-18", prior_values), policy_path)
        current = KL.build_transform(upstream_payload("2026-08-20", current_values), policy_path)
        policy_sha = prior["policy"]["policy_sha256"]
    binding = {
        "taxonomy_contract_version": taxonomy_contract_version,
        "taxonomy_id": taxonomy_id,
        "taxonomy_decision_id": decision_id,
        "taxonomy_decision_sha256": decision_sha,
        "taxonomy_packet_sha256": packet_sha,
        "upstream_leadership_policy_sha256": policy_sha,
    }
    context = {
        "breadth": {
            "status": "UNKNOWN",
            "markets": {
                "KOSDAQ": breadth_market(),
                "KOSPI": breadth_market(),
            },
            "freshness_limit_days": 3,
            "ranking_input_authorized": False,
            "decision_eligible": False,
        },
        "investor_flow": {
            "status": "KRX_ONLY_PARTIAL_MARKET_COVERAGE",
            "market_venue_scope": "KRX_ONLY",
            "nxt_included": False,
            "whole_korea_market_claim_authorized": False,
            "source_release_time_status": "unverified",
            "available_at": None,
            "decision_eligible": False,
            "ranking_input_authorized": False,
        },
    }
    input_value = {
        "schema_version": "korea_capital_rotation_input/1",
        "as_of_date": "2026-08-20",
        "taxonomy_binding": binding,
        "coverage_context": context,
        "prior_observation": prior,
        "current_observation": current,
    }
    rotation_policy = {
        "schema_version": "korea_capital_rotation_policy/1",
        "policy_id": "POLICY.P2.03.TEST",
        "approval_status": "RATIFIED",
        "ratified_by": "Atlas CIO",
        "ratified_at_utc": "2026-08-17T00:00:00Z",
        "effective_from": "2026-08-01",
        "effective_to": None,
        "taxonomy_decision_sha256": decision_sha,
        "taxonomy_packet_sha256": packet_sha,
        "upstream_leadership_policy_sha256": policy_sha,
        "ranking_metric": "RELATIVE_STRENGTH_VS_OWN_BENCHMARK",
        "ranking_order": "DESCENDING_WITHIN_BENCHMARK_SCOPE",
        "tie_break": "SERIES_IDENTITY_ASC",
        "maximum_calendar_gap_days": 5,
        "benchmark_scopes": [
            {
                "benchmark_identity": "01::KOSPI",
                "members": [
                    {"series_identity": item, "theme_id": THEME_IDS[item]}
                    for item in KOSPI_THEMES
                ],
                "top_count": 1,
                "bottom_count": 1,
            },
            {
                "benchmark_identity": "02::KOSDAQ",
                "members": [
                    {"series_identity": item, "theme_id": THEME_IDS[item]}
                    for item in KOSDAQ_THEMES
                ],
                "top_count": 1,
                "bottom_count": 1,
            },
        ],
    }
    return input_value, rotation_policy


class KoreaCapitalRotationTests(unittest.TestCase):
    def test_effective_policy_reproduces_own_benchmark_ranks_and_transitions(self):
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        scopes = {scope["benchmark_identity"]: scope for scope in packet["benchmark_scopes"]}
        self.assertEqual(scopes["01::KOSPI"]["top_themes"], [THEME_IDS["12::KOSPI_바이오"]])
        self.assertEqual(scopes["01::KOSPI"]["bottom_themes"], [THEME_IDS["13::KOSPI_방산"]])
        rows = {row["series_identity"]: row for row in scopes["01::KOSPI"]["theme_observations"]}
        self.assertEqual(rows["12::KOSPI_바이오"]["theme_id"], THEME_IDS["12::KOSPI_바이오"])
        self.assertEqual(rows["12::KOSPI_바이오"]["bucket_transition"], "MIDDLE_TO_TOP")
        self.assertEqual(rows["11::KOSPI_반도체"]["bucket_transition"], "TOP_TO_MIDDLE")
        self.assertEqual(rows["13::KOSPI_방산"]["bucket_transition"], "BOTTOM_TO_BOTTOM")

    def test_unratified_policy_emits_no_rank_bucket_or_transition(self):
        value, policy = make_bundle()
        policy.update({"approval_status": "UNRATIFIED", "ratified_by": None, "ratified_at_utc": None})
        packet = KCR.build_packet(value, policy)
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertIsNone(packet["ranking_method"])
        for scope in packet["benchmark_scopes"]:
            self.assertEqual(scope["top_themes"], [])
            for row in scope["theme_observations"]:
                self.assertIsNone(row["current_rank_within_benchmark"])
                self.assertIsNone(row["bucket_transition"])
                self.assertEqual(row["p2_state"], "UNDEFINED_PENDING_P2_05")

    def test_future_policy_is_inactive_and_ratification_cannot_look_ahead(self):
        value, policy = make_bundle()
        future = copy.deepcopy(policy)
        future["effective_from"] = "2026-08-19"
        self.assertFalse(KCR.build_packet(value, future)["rotation_policy_effective"])
        policy["ratified_at_utc"] = "2026-08-18T09:00:01Z"
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"):
            KCR.build_packet(value, policy)

    def test_cross_benchmark_ranking_is_never_created(self):
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        self.assertFalse(packet["ranking_method"]["cross_benchmark_ranking"])
        self.assertFalse(packet["authority"]["cross_benchmark_ranking_authorized"])
        self.assertEqual(
            [scope["benchmark_identity"] for scope in packet["benchmark_scopes"]],
            ["01::KOSPI", "02::KOSDAQ"],
        )
        self.assertNotIn("top_themes", {key: value for key, value in packet.items() if key != "benchmark_scopes"})

    def test_taxonomy_and_upstream_policy_hashes_are_exactly_bound(self):
        value, policy = make_bundle()
        policy["taxonomy_packet_sha256"] = "9" * 64
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "POLICY_TAXONOMY_PACKET_MISMATCH"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        value["current_observation"]["policy"]["policy_sha256"] = "9" * 64
        rehash(value["current_observation"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UPSTREAM_POLICY_BINDING_MISMATCH"):
            KCR.build_packet(value, policy)

    def test_upstream_payload_hash_detects_tamper(self):
        value, policy = make_bundle()
        value["current_observation"]["relative_strength_observations"][0]["relative_strength_vs_benchmark"] = "0.5"
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UPSTREAM_PAYLOAD_SHA_MISMATCH"):
            KCR.build_packet(value, policy)

    def test_breadth_and_investor_flow_cannot_gain_ranking_authority(self):
        value, policy = make_bundle()
        value["coverage_context"]["breadth"]["ranking_input_authorized"] = True
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "BREADTH_CONTEXT_AUTHORITY_INVALID"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        value["coverage_context"]["investor_flow"]["nxt_included"] = True
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "INVESTOR_FLOW_CONTEXT_AUTHORITY_INVALID"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        value["coverage_context"]["investor_flow"]["decision_eligible"] = True
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "INVESTOR_FLOW_CONTEXT_AUTHORITY_INVALID"):
            KCR.build_packet(value, policy)

    def test_real_p1_kr05_live_lineage_derives_blocked_not_available(self):
        # Exact payload_sha256/as_of_date values from a real P1-KR-05
        # workflow_dispatch live run (2026-08-22 UTC, run 32549348644) --
        # not a synthetic fixture. Both real Breadth packets have no
        # verified source_available_at and no declared capture_mode
        # (this real run predates the first-seen lineage), so this must
        # derive BLOCKED -- never NEUTRAL/AVAILABLE/PASS.
        value, policy = make_bundle()
        value["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False,
            kosdaq=breadth_market(
                "3be02ecda92a143abb5a825f66a207bd2a92bdef1d8b59b1c28a5ea8b0fcfc94",
                "2026-08-21",
            ),
            kospi=breadth_market(
                "086bddf7313fe0a36d87d86fc028982bc9835e3b4b55d15dff13ecdc2818caf2",
                "2026-08-21",
            ),
        )
        packet = KCR.build_packet(value, policy)
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "BLOCKED")
        self.assertFalse(packet["coverage_context"]["breadth"]["decision_eligible"])
        checked = KCR.validate_packet(copy.deepcopy(packet))
        self.assertEqual(checked["coverage_context"]["breadth"]["status"], "BLOCKED")

    def test_breadth_status_derivation_available_stale_unknown_and_worst_wins(self):
        value, policy = make_bundle()  # decision_time = 2026-08-20T09:00:00+00:00
        # Same calendar day as its own as_of_date, and before decision_time
        # -- a real, valid PIT-correct "verified source timing" example.
        fresh = breadth_market("a" * 64, "2026-08-20", "2026-08-20T05:00:00Z")
        value["coverage_context"]["breadth"] = breadth_context(
            "AVAILABLE", True, kosdaq=fresh, kospi=fresh,
        )
        packet = KCR.build_packet(value, policy)
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "AVAILABLE")
        self.assertTrue(packet["coverage_context"]["breadth"]["decision_eligible"])

        stale = breadth_market("a" * 64, "2026-08-14", "2026-08-14T18:00:00Z")
        value2, policy2 = make_bundle()
        value2["coverage_context"]["breadth"] = breadth_context(
            "STALE", False, kosdaq=stale, kospi=stale,
        )
        packet2 = KCR.build_packet(value2, policy2)
        self.assertEqual(packet2["coverage_context"]["breadth"]["status"], "STALE")
        self.assertFalse(packet2["coverage_context"]["breadth"]["decision_eligible"])

        # Worst-per-market wins: one AVAILABLE market plus one market with
        # no observation at all (UNKNOWN) must not be masked by the
        # fresher market -- the whole context degrades to UNKNOWN.
        value3, policy3 = make_bundle()
        value3["coverage_context"]["breadth"] = breadth_context(
            "UNKNOWN", False, kosdaq=breadth_market(), kospi=fresh,
        )
        packet3 = KCR.build_packet(value3, policy3)
        self.assertEqual(packet3["coverage_context"]["breadth"]["status"], "UNKNOWN")
        self.assertFalse(packet3["coverage_context"]["breadth"]["decision_eligible"])

    def test_breadth_status_cannot_be_declared_wrong(self):
        # Raw facts (available_at null) independently derive BLOCKED --
        # a caller cannot simply declare AVAILABLE/decision_eligible=True
        # to bypass the derivation.
        value, policy = make_bundle()
        value["coverage_context"]["breadth"] = breadth_context(
            "AVAILABLE", True,
            kosdaq=breadth_market("a" * 64, "2026-08-20", None),
            kospi=breadth_market("a" * 64, "2026-08-20", None),
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_CONTEXT_AUTHORITY_INVALID"
        ):
            KCR.build_packet(value, policy)
        # Also rejects a NEUTRAL/PASS-style relabeling of a genuinely
        # BLOCKED context -- the vocabulary is closed to exactly the four
        # ratified values, nothing else is accepted even if internally
        # self-consistent.
        value2, policy2 = make_bundle()
        value2["coverage_context"]["breadth"] = breadth_context(
            "NEUTRAL", False,
            kosdaq=breadth_market("a" * 64, "2026-08-20", None),
            kospi=breadth_market("a" * 64, "2026-08-20", None),
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_CONTEXT_AUTHORITY_INVALID"
        ):
            KCR.build_packet(value2, policy2)

    def test_breadth_market_partial_identity_fails_closed(self):
        value, policy = make_bundle()
        value["coverage_context"]["breadth"]["markets"]["KOSPI"] = breadth_market(
            "a" * 64, None, None,
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_MARKET_PARTIAL_IDENTITY"
        ):
            KCR.build_packet(value, policy)

    def test_source_available_at_after_decision_time_degrades_to_blocked_not_error(self):
        # PIT correction (2026-08-22): a market whose source_available_at
        # is genuinely after decision_time (make_bundle's current
        # observation available_at, 2026-08-20T09:00:00+00:00) is not a
        # tamper/defect -- it degrades to BLOCKED, the same as any other
        # not-yet-usable observation. It must NEVER be silently accepted
        # as AVAILABLE either.
        value, policy = make_bundle()
        future = breadth_market("a" * 64, "2026-08-20", "2026-08-25T00:00:00Z")
        value["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False, kosdaq=future, kospi=future,
        )
        packet = KCR.build_packet(value, policy)
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "BLOCKED")
        self.assertFalse(packet["coverage_context"]["breadth"]["decision_eligible"])

    def test_source_available_at_before_observation_date_fails_closed(self):
        # Structurally impossible: official publication cannot predate
        # the trading day it describes.
        value, policy = make_bundle()
        value["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False,
            kosdaq=breadth_market("a" * 64, "2026-08-20", "2026-08-19T00:00:00Z"),
            kospi=breadth_market("a" * 64, "2026-08-20", "2026-08-19T00:00:00Z"),
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_MARKET_SOURCE_AVAILABLE_AT_BEFORE_AS_OF"
        ):
            KCR.build_packet(value, policy)

    def test_forward_live_first_seen_before_or_after_decision_time(self):
        # A genuine forward_live capture whose first_seen_at is BEFORE
        # decision_time is real, usable evidence -- AVAILABLE, not a
        # workaround.
        value, policy = make_bundle()
        usable = forward_live_market("a" * 64, "2026-08-20", "2026-08-20T05:00:00Z")
        value["coverage_context"]["breadth"] = breadth_context(
            "AVAILABLE", True, kosdaq=usable, kospi=usable,
        )
        packet = KCR.build_packet(value, policy)
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "AVAILABLE")
        self.assertTrue(packet["coverage_context"]["breadth"]["decision_eligible"])

        # The same shape with a first_seen_at genuinely AFTER decision_time
        # degrades to BLOCKED, not an error -- two independent real
        # captures can legitimately complete in either order.
        value2, policy2 = make_bundle()
        too_late = forward_live_market("a" * 64, "2026-08-20", "2026-08-21T00:00:00Z")
        value2["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False, kosdaq=too_late, kospi=too_late,
        )
        packet2 = KCR.build_packet(value2, policy2)
        self.assertEqual(packet2["coverage_context"]["breadth"]["status"], "BLOCKED")
        self.assertFalse(packet2["coverage_context"]["breadth"]["decision_eligible"])

    def test_historical_backfill_first_seen_never_decision_eligible(self):
        value, policy = make_bundle()
        backfilled = breadth_market(
            "a" * 64, "2026-08-20", None,
            captured_at="2026-08-20T05:00:00Z", first_seen_at="2026-08-20T05:00:00Z",
            capture_mode="historical_backfill",
        )
        value["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False, kosdaq=backfilled, kospi=backfilled,
        )
        packet = KCR.build_packet(value, policy)
        # Even though first_seen_at is genuinely before decision_time,
        # historical_backfill never counts -- date math alone cannot
        # distinguish a genuine next-day capture from a convenient later
        # catch-up.
        self.assertEqual(packet["coverage_context"]["breadth"]["status"], "BLOCKED")
        self.assertFalse(packet["coverage_context"]["breadth"]["decision_eligible"])

    def test_first_seen_before_captured_at_or_observation_date_fails_closed(self):
        value, policy = make_bundle()
        reversed_market = breadth_market(
            "a" * 64, "2026-08-20", None,
            captured_at="2026-08-20T05:00:00Z", first_seen_at="2026-08-20T01:00:00Z",
            capture_mode="forward_live",
        )
        value["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False, kosdaq=reversed_market, kospi=reversed_market,
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_MARKET_FIRST_SEEN_BEFORE_CAPTURED"
        ):
            KCR.build_packet(value, policy)

        value2, policy2 = make_bundle()
        before_as_of = forward_live_market("a" * 64, "2026-08-20", "2026-08-19T00:00:00Z")
        value2["coverage_context"]["breadth"] = breadth_context(
            "BLOCKED", False, kosdaq=before_as_of, kospi=before_as_of,
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_MARKET_FIRST_SEEN_BEFORE_AS_OF"
        ):
            KCR.build_packet(value2, policy2)

    def test_standalone_validator_rejects_rehashed_breadth_status_tamper(self):
        value, policy = make_bundle()
        fresh = breadth_market("a" * 64, "2026-08-20", "2026-08-20T05:00:00Z")
        value["coverage_context"]["breadth"] = breadth_context(
            "AVAILABLE", True, kosdaq=fresh, kospi=fresh,
        )
        packet = KCR.build_packet(value, policy)
        packet["coverage_context"]["breadth"]["status"] = "STALE"
        rehash_output(packet)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "BREADTH_CONTEXT_AUTHORITY_INVALID"
        ):
            KCR.validate_packet(packet)

    def test_upstream_role_benchmark_and_taxonomy_drift_fail_closed(self):
        value, policy = make_bundle()
        target = value["current_observation"]["relative_strength_observations"][2]
        target["benchmark_identity"] = "02::KOSDAQ"
        rehash(value["current_observation"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UPSTREAM_ROLE_OR_BENCHMARK_DRIFT"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        value["current_observation"]["relative_strength_observations"].pop()
        rehash(value["current_observation"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UPSTREAM_TAXONOMY_DRIFT"):
            KCR.build_packet(value, policy)

    def test_observation_order_and_policy_gap_are_fail_closed(self):
        value, policy = make_bundle()
        value["as_of_date"] = "2026-08-21"
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "OBSERVATION_DATE_ORDER_INVALID"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        policy["maximum_calendar_gap_days"] = 1
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "OBSERVATION_GAP_EXCEEDS_POLICY"):
            KCR.build_packet(value, policy)

    def test_only_forward_pit_closed_authority_upstream_is_accepted(self):
        value, policy = make_bundle()
        value["prior_observation"]["status"] = "CAUSAL_REPLAY_ONLY"
        rehash(value["prior_observation"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UPSTREAM_IDENTITY_INVALID"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        value["current_observation"]["ranking_authorized"] = True
        rehash(value["current_observation"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "UPSTREAM_AUTHORITY_EXPANDED"):
            KCR.build_packet(value, policy)

    def test_policy_scope_coverage_overlap_and_benchmark_binding_are_strict(self):
        value, policy = make_bundle()
        policy["benchmark_scopes"][0]["members"].append({
            "series_identity": "14::KOSPI_우주",
            "theme_id": "THEME.KR.KOSPI.SPACE",
        })
        policy["benchmark_scopes"][0]["members"].sort(key=lambda item: item["series_identity"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "POLICY_THEME_COVERAGE_MISMATCH"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        policy["benchmark_scopes"][1]["members"][0] = {
            "series_identity": KOSPI_THEMES[0],
            "theme_id": "THEME.KR.DUPLICATE",
        }
        policy["benchmark_scopes"][1]["members"].sort(key=lambda item: item["series_identity"])
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "POLICY_THEME_IN_MULTIPLE_SCOPES"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        policy["benchmark_scopes"][1]["members"][0]["theme_id"] = (
            policy["benchmark_scopes"][0]["members"][0]["theme_id"]
        )
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "POLICY_THEME_ID_MULTIPLE_PROXY_UNDEFINED"):
            KCR.build_packet(value, policy)
        value, policy = make_bundle()
        policy["benchmark_scopes"][0]["benchmark_identity"] = "02::KOSDAQ"
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "POLICY_SCOPE_ORDER_INVALID|POLICY_BENCHMARK_SCOPE_MISMATCH"):
            KCR.build_packet(value, policy)

    def test_tie_break_is_series_identity_ascending_within_scope(self):
        value, policy = make_bundle()
        rows = value["current_observation"]["relative_strength_observations"]
        lookup = {row["series_identity"]: row for row in rows}
        lookup["11::KOSPI_반도체"]["relative_strength_vs_benchmark"] = "0.4"
        rehash(value["current_observation"])
        packet = KCR.build_packet(value, policy)
        kospi = packet["benchmark_scopes"][0]
        self.assertEqual(kospi["top_themes"], [THEME_IDS["11::KOSPI_반도체"]])

    def test_output_is_deterministic_and_digest_bound(self):
        value, policy = make_bundle()
        first = KCR.build_packet(value, policy)
        second = KCR.build_packet(copy.deepcopy(value), copy.deepcopy(policy))
        self.assertEqual(first, second)
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, KCR.payload_sha256(second))

    def test_output_validator_rejects_self_rehashed_rank_tamper(self):
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        packet["benchmark_scopes"][0]["theme_observations"][0][
            "current_rank_within_benchmark"
        ] = 3
        rehash_output(packet)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "OUTPUT_RANK_BUCKET_MISMATCH"
        ):
            KCR.validate_packet(packet)

    def test_output_validator_rejects_self_rehashed_unratified_delta_tamper(self):
        value, policy = make_bundle()
        policy.update({
            "approval_status": "UNRATIFIED",
            "ratified_by": None,
            "ratified_at_utc": None,
        })
        packet = KCR.build_packet(value, policy)
        packet["benchmark_scopes"][0]["theme_observations"][0][
            "relative_strength_change"
        ] = "0"
        rehash_output(packet)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "OUTPUT_THEME_DERIVATION_MISMATCH"
        ):
            KCR.validate_packet(packet)

    def test_observation_pair_persists_available_at_for_standalone_reproof(self):
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        pair = packet["observation_pair"]
        self.assertEqual(pair["prior_available_at"], "2026-08-18T09:00:00+00:00")
        self.assertEqual(pair["current_available_at"], "2026-08-20T09:00:00+00:00")
        # validate_packet() takes only the persisted packet -- neither upstream
        # Leadership packet is passed in, so a pass here is itself the proof
        # that temporal order and ratified-before-prior are standalone-provable.
        KCR.validate_packet(copy.deepcopy(packet))

    def test_revision_a_and_b_are_each_independently_standalone_verifiable(self):
        value_a, policy_a = make_bundle()
        revision_a = KCR.build_packet(value_a, policy_a)
        # Revision B: source pointer moves -- later available_at for both
        # observations, as if a fresher upstream snapshot had been read.
        with tempfile.TemporaryDirectory() as raw:
            policy_path = write_upstream_policy(Path(raw) / "leadership-policy.json")
            prior_values = {
                "11::KOSPI_반도체": "130", "12::KOSPI_바이오": "110", "13::KOSPI_방산": "90",
                "21::KOSDAQ_반도체": "105", "22::KOSDAQ_바이오": "120", "23::KOSDAQ_로봇": "80",
            }
            current_values = {
                "11::KOSPI_반도체": "110", "12::KOSPI_바이오": "140", "13::KOSPI_방산": "80",
                "21::KOSDAQ_반도체": "130", "22::KOSDAQ_바이오": "110", "23::KOSDAQ_로봇": "90",
            }
            prior_payload = upstream_payload("2026-08-18", prior_values)
            prior_payload.update({
                "available_at": "2026-08-18T22:00:00+09:00",
                "fetched_at": "2026-08-18T22:05:00+09:00",
                "decision_at": "2026-08-18T22:10:00+09:00",
            })
            current_payload = upstream_payload("2026-08-20", current_values)
            current_payload.update({
                "available_at": "2026-08-20T22:00:00+09:00",
                "fetched_at": "2026-08-20T22:05:00+09:00",
                "decision_at": "2026-08-20T22:10:00+09:00",
            })
            moved_prior = KL.build_transform(prior_payload, policy_path)
            moved_current = KL.build_transform(current_payload, policy_path)
        moved_value = copy.deepcopy(value_a)
        moved_value["prior_observation"] = moved_prior
        moved_value["current_observation"] = moved_current
        revision_b = KCR.build_packet(moved_value, policy_a)
        self.assertNotEqual(
            revision_a["observation_pair"]["prior_available_at"],
            revision_b["observation_pair"]["prior_available_at"],
        )
        # Each revision is re-verified from nothing but its own persisted
        # packet -- no fault injection, no monkeypatch, no shared live state.
        KCR.validate_packet(copy.deepcopy(revision_a))
        KCR.validate_packet(copy.deepcopy(revision_b))
        self.assertEqual(
            revision_b["observation_pair"]["prior_available_at"],
            "2026-08-18T13:00:00+00:00",
        )

    def test_validate_packet_rejects_missing_invalid_naive_and_malformed_timezone_available_at(self):
        for field in ("prior_available_at", "current_available_at"):
            with self.subTest(field=field, case="missing"):
                value, policy = make_bundle()
                packet = KCR.build_packet(value, policy)
                del packet["observation_pair"][field]
                with self.assertRaisesRegex(
                    KCR.KoreaCapitalRotationError, "OUTPUT_OBSERVATION_PAIR_INVALID"
                ):
                    KCR.validate_packet(packet)
            code = (
                "OUTPUT_PRIOR_AVAILABLE_AT_INVALID"
                if field == "prior_available_at"
                else "OUTPUT_CURRENT_AVAILABLE_AT_INVALID"
            )
            for case, bad_value in (
                ("not_a_string", 12345),
                ("unparseable", "not-a-timestamp"),
                ("naive_no_offset", "2026-08-18T09:00:00"),
                ("malformed_timezone", "2026-08-18T09:00:00PST"),
            ):
                with self.subTest(field=field, case=case):
                    value, policy = make_bundle()
                    packet = KCR.build_packet(value, policy)
                    packet["observation_pair"][field] = bad_value
                    with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, code):
                        KCR.validate_packet(packet)

    def test_validate_packet_rejects_available_at_order_and_ratification_tamper_after_self_rehash(self):
        # Tamper 1: swap persisted available_at order, then re-hash the
        # packet so the top-level payload_sha256 digest matches the
        # tampered content -- the temporal-order re-derivation, not the
        # digest check, must be what catches this.
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        pair = packet["observation_pair"]
        pair["prior_available_at"], pair["current_available_at"] = (
            pair["current_available_at"], pair["prior_available_at"],
        )
        rehash_output(packet)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "OUTPUT_AVAILABLE_AT_ORDER_INVALID"
        ):
            KCR.validate_packet(packet)

        # Tamper 2: push the persisted prior_available_at earlier than the
        # policy's own ratified_at_utc, self-rehash, and confirm the
        # ratified-before-prior re-proof still fires even though
        # build_packet() would never have produced this shape.
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        packet["observation_pair"]["prior_available_at"] = "2026-08-16T00:00:00+00:00"
        rehash_output(packet)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "OUTPUT_POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"
        ):
            KCR.validate_packet(packet)

    def test_state_regime_stage_production_and_trading_remain_closed(self):
        value, policy = make_bundle()
        packet = KCR.build_packet(value, policy)
        self.assertTrue(packet["authority"]["theme_ranking_within_benchmark_authorized"])
        for field in (
            "cross_benchmark_ranking_authorized", "breadth_as_ranking_input_authorized",
            "investor_flow_as_ranking_input_authorized", "p2_state_vocabulary_authorized",
            "state_ledger_authorized", "regime_input_authorized",
            "candidate_ranking_authorized", "stage_promotion_authorized",
            "production_authorized", "trading_authorized",
        ):
            self.assertFalse(packet["authority"][field], field)

    def test_contract_default_policy_cli_atomic_and_tracked_output_boundaries(self):
        value, policy = make_bundle()
        contract = KCR.load_contract()
        contract["authority"]["trading_authorized"] = True
        with self.assertRaisesRegex(KCR.KoreaCapitalRotationError, "CONTRACT_FIELD_MISMATCH"):
            KCR.build_packet(value, policy, contract=contract)
        self.assertFalse((ROOT / "config" / "korea_capital_rotation_policy.json").exists())
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path, policy_path, output_path = temp / "input.json", temp / "policy.json", temp / "output.json"
            input_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(MODULE_PATH), str(input_path), "--policy", str(policy_path), "--out", str(output_path)],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text()), KCR.build_packet(value, policy))
            output_path.write_text("sentinel\n", encoding="utf-8")
            input_path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(KCR.run(input_path, policy_path, output_path), 1)
            self.assertEqual(output_path.read_text(), "sentinel\n")
        tracked = ROOT / ".test-korea-capital-rotation-output.json"
        self.assertFalse(tracked.exists())
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path, policy_path = temp / "input.json", temp / "policy.json"
            input_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
            self.assertEqual(KCR.run(input_path, policy_path, tracked), 1)
        self.assertFalse(tracked.exists())


class KoreaRotationThemeTaxonomyV2Tests(unittest.TestCase):
    """P2-01 `theme_taxonomy/2` producer -> P2-03 Korea rotation consumer.

    Before this slice the Korea consumer only carried four opaque caller
    strings labelled `theme_taxonomy/1`; nothing in the money path ever ran a
    real Theme graph or the independent authority resolver. These tests prove
    the real producer is genuinely invoked, that its verdict is recorded
    exactly as returned (not authorized, because the committed approval
    authority registry is empty), and that none of it creates authority.
    """

    def setUp(self):
        self.document = taxonomy_graph_document()
        self.source_bytes = taxonomy_source_bytes(self.document)
        self.reference = TT.build_packet(copy.deepcopy(self.document))

    def build(self, value, policy, source_bytes=None):
        return KCR.build_packet(
            value, policy,
            taxonomy_source_bytes=(
                self.source_bytes if source_bytes is None else source_bytes
            ),
        )

    def test_real_producer_packet_is_consumed_and_recorded_exactly(self):
        value, policy = make_bundle(self.document)
        packet = self.build(value, policy)
        binding = packet["taxonomy_binding"]
        self.assertEqual(
            binding["taxonomy_contract_version"],
            TT.load_contract()["contract_version"],
        )
        self.assertEqual(binding["taxonomy_contract_version"], "theme_taxonomy/2")
        # Every derived field equals what the real producer itself returned
        # for these exact bytes -- none of it was supplied by the caller.
        self.assertEqual(
            binding["taxonomy_source_sha256"],
            hashlib.sha256(self.source_bytes).hexdigest(),
        )
        self.assertEqual(
            binding["taxonomy_packet_sha256"], self.reference["payload_sha256"]
        )
        self.assertEqual(
            binding["taxonomy_graph_status"], self.reference["graph_status"]
        )
        self.assertEqual(
            binding["taxonomy_authority_status"],
            self.reference["authority_resolution"]["status"],
        )
        # Empty committed authority registry: a structurally real ratification
        # claim that is still not authorized. Never upgraded, never fabricated.
        self.assertEqual(
            binding["taxonomy_graph_status"],
            "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED",
        )
        self.assertNotEqual(binding["taxonomy_authority_status"], "AUTHORIZED")
        self.assertFalse(binding["theme_membership_authorized"])
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertEqual(packet["schema_version"], "korea_capital_rotation_packet/4")

    def test_legacy_binding_is_unchanged_and_refuses_a_graph(self):
        value, policy = make_bundle()
        legacy = KCR.build_packet(value, policy)
        self.assertEqual(
            set(legacy["taxonomy_binding"]), set(KCR.TAXONOMY_BINDING_FIELDS)
        )
        self.assertEqual(
            legacy["taxonomy_binding"]["taxonomy_contract_version"],
            KCR.load_contract()["taxonomy_contract_version"],
        )
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError,
            "TAXONOMY_SOURCE_NOT_ALLOWED_FOR_LEGACY_BINDING",
        ):
            KCR.build_packet(value, policy, taxonomy_source_bytes=self.source_bytes)

    def test_relabelling_a_legacy_binding_as_v2_never_succeeds(self):
        # The exact failure mode this slice exists to prevent: editing the
        # version string on an opaque binding must not buy a real taxonomy.
        value, policy = make_bundle()
        value["taxonomy_binding"]["taxonomy_contract_version"] = "theme_taxonomy/2"
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "TAXONOMY_SOURCE_REQUIRED_FOR_V2_BINDING"
        ):
            KCR.build_packet(value, policy)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "TAXONOMY_SOURCE_IDENTITY_MISMATCH"
        ):
            self.build(value, policy)

    def test_declared_identity_and_digest_cannot_replace_the_derived_ones(self):
        value, policy = make_bundle(self.document)
        value["taxonomy_binding"]["taxonomy_packet_sha256"] = "9" * 64
        policy["taxonomy_packet_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError,
            "TAXONOMY_PACKET_SHA_NOT_DERIVED_FROM_SOURCE",
        ):
            self.build(value, policy)
        value, policy = make_bundle(self.document)
        value["taxonomy_binding"]["taxonomy_decision_id"] = "DECISION.ATTACKER"
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "TAXONOMY_SOURCE_IDENTITY_MISMATCH"
        ):
            self.build(value, policy)

    def test_semantic_and_byte_only_source_tamper_are_both_detected(self):
        # Semantic tamper: the producer derives a different packet digest.
        value, policy = make_bundle(self.document)
        tampered = copy.deepcopy(self.document)
        tampered["nodes"][0]["description"] = "attacker rewrote the graph"
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError,
            "TAXONOMY_PACKET_SHA_NOT_DERIVED_FROM_SOURCE",
        ):
            self.build(value, policy, source_bytes=taxonomy_source_bytes(tampered))
        # Byte-only tamper: identical parsed graph, different source bytes --
        # caught because the source digest is bound too, not just the packet.
        packet = self.build(*make_bundle(self.document))
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "OUTPUT_TAXONOMY_DERIVATION_MISMATCH"
        ):
            KCR.validate_packet(
                copy.deepcopy(packet),
                taxonomy_source_bytes=self.source_bytes + b" ",
            )

    def test_as_of_date_of_the_consumed_graph_must_match_the_decision_date(self):
        other_day = taxonomy_graph_document(as_of_date="2026-08-21")
        value, policy = make_bundle(other_day)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "TAXONOMY_AS_OF_DATE_MISMATCH"
        ):
            KCR.build_packet(
                value, policy,
                taxonomy_source_bytes=taxonomy_source_bytes(other_day),
            )

    def test_policy_theme_ids_must_be_active_nodes_of_the_consumed_graph(self):
        incomplete = taxonomy_graph_document(theme_ids=sorted(THEME_IDS.values())[:-1])
        value, policy = make_bundle(incomplete)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError,
            "TAXONOMY_THEME_NODE_NOT_ACTIVE:THEME.KR.KOSPI.SEMICONDUCTOR",
        ):
            KCR.build_packet(
                value, policy,
                taxonomy_source_bytes=taxonomy_source_bytes(incomplete),
            )
        # A node that exists but has lapsed on this decision date is equally
        # unusable -- the producer's own interval semantics, reused verbatim.
        expired = taxonomy_graph_document()
        target = THEME_IDS["23::KOSDAQ_로봇"]
        for node in expired["nodes"]:
            if node["theme_id"] == target:
                node["valid_to"] = "2026-08-20"
        for edge in expired["edges"]:
            if edge["to_theme_id"] == target:
                edge["valid_to"] = "2026-08-20"
        value, policy = make_bundle(expired)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, f"TAXONOMY_THEME_NODE_NOT_ACTIVE:{target}"
        ):
            KCR.build_packet(
                value, policy, taxonomy_source_bytes=taxonomy_source_bytes(expired)
            )

    def test_standalone_validator_reproves_derived_fields_and_grants_nothing(self):
        packet = self.build(*make_bundle(self.document))
        # A persisted packet stays standalone-verifiable without the graph.
        KCR.validate_packet(copy.deepcopy(packet))
        KCR.validate_packet(
            copy.deepcopy(packet), taxonomy_source_bytes=self.source_bytes
        )
        forged = copy.deepcopy(packet)
        forged["taxonomy_binding"]["theme_membership_authorized"] = True
        forged["taxonomy_binding"]["taxonomy_authority_status"] = "AUTHORIZED"
        rehash_output(forged)
        # Packet-only consumers must re-prove the embedded source too.
        for source in (None, self.source_bytes):
            with self.assertRaisesRegex(
                KCR.KoreaCapitalRotationError, "OUTPUT_TAXONOMY_DERIVATION_MISMATCH"
            ):
                KCR.validate_packet(forged, taxonomy_source_bytes=source)
        # Re-signing a false source digest cannot make it exact evidence.
        forged = copy.deepcopy(packet)
        forged["taxonomy_binding"]["taxonomy_source_sha256"] = "f" * 64
        rehash_output(forged)
        with self.assertRaisesRegex(
            KCR.KoreaCapitalRotationError, "OUTPUT_TAXONOMY_DERIVATION_MISMATCH"
        ):
            KCR.validate_packet(forged)

    def test_v2_consumption_adds_no_authority_and_no_ranking_change(self):
        legacy = KCR.build_packet(*make_bundle())
        consumed = self.build(*make_bundle(self.document))
        self.assertEqual(consumed["authority"], legacy["authority"])
        self.assertEqual(consumed["benchmark_scopes"], legacy["benchmark_scopes"])
        self.assertEqual(
            consumed["unresolved_boundaries"], legacy["unresolved_boundaries"]
        )
        self.assertEqual(consumed["status"], legacy["status"])
        self.assertEqual(consumed["ranking_method"], legacy["ranking_method"])

    def test_v2_output_is_deterministic_and_the_cli_consumes_the_graph(self):
        value, policy = make_bundle(self.document)
        first = self.build(copy.deepcopy(value), copy.deepcopy(policy))
        second = self.build(copy.deepcopy(value), copy.deepcopy(policy))
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            policy_path = temp / "policy.json"
            graph_path = temp / "graph.json"
            output_path = temp / "output.json"
            input_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
            policy_path.write_text(json.dumps(policy, ensure_ascii=False), encoding="utf-8")
            graph_path.write_bytes(self.source_bytes)
            result = subprocess.run(
                [
                    sys.executable, str(MODULE_PATH), str(input_path),
                    "--policy", str(policy_path), "--out", str(output_path),
                    "--taxonomy-graph", str(graph_path),
                ],
                cwd=ROOT, text=True, capture_output=True, check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text()), first)
            # The same CLI invocation without the graph fails closed.
            self.assertEqual(KCR.run(input_path, policy_path, output_path), 1)
            self.assertEqual(json.loads(output_path.read_text()), first)


if __name__ == "__main__":
    unittest.main()
