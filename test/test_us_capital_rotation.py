"""P2-02 policy-gated US Theme capital-rotation regression."""
from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "rotation" / "us_capital_rotation.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location("us_capital_rotation", MODULE_PATH)
UCR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(UCR)

# The exact same producer module instance the consumer itself resolves, so a
# reference packet built here is the real P2-01 output, not a look-alike.
from rotation import theme_taxonomy as TT  # noqa: E402

TAXONOMY_DECISION_SHA = "a" * 64
TAXONOMY_PACKET_SHA = "b" * 64
UPSTREAM_TAXONOMY_SHA = "c" * 64

US_THEME_IDS = ["THEME.COMPUTE", "THEME.NETWORK", "THEME.POWER"]
GRAPH_TAXONOMY_ID = "TAXONOMY.US.ROTATION.TEST"
GRAPH_DECISION_ID = "DECISION.P2.01.US.TEST"
GRAPH_DECISION_SHA = "e" * 64
ROOT_THEME_ID = "THEME.US.ROTATION.ROOT"


def group(theme_id: str, relative_strength: str) -> dict:
    relative = UCR._render(Decimal(relative_strength), 12)
    return {
        "group_id": theme_id,
        "observed_session_count": 3,
        "minimum_daily_member_count": 2,
        "required_minimum_member_count": 1,
        "cumulative_gross_return": UCR._render(
            Decimal(1) + Decimal(relative_strength), 12
        ),
        "relative_strength_vs_benchmark": relative,
        "classification": "UNDEFINED",
    }


def leadership_packet(
    observation_date: str,
    available_at: str,
    values: dict[str, str],
) -> dict:
    if observation_date == "2026-08-18":
        first_input, first_return = "2026-08-13", "2026-08-14"
        daily_dates = ["2026-08-14", "2026-08-17", "2026-08-18"]
    else:
        first_input, first_return = "2026-08-17", "2026-08-18"
        daily_dates = ["2026-08-18", "2026-08-19", "2026-08-20"]
    packet = {
        "schema_version": 1,
        "contract_version": "us_leadership_contract/v1",
        "transform_version": "us_leadership/v1",
        "market": "US",
        "measurement": "us_cross_sectional_leadership_observation",
        "status": "OBSERVED_UNCLASSIFIED",
        "observation_date": observation_date,
        "available_at": available_at,
        "benchmark_asset": "SPY",
        "window": {
            "first_input_session": first_input,
            "first_return_session": first_return,
            "last_return_session": observation_date,
            "lookback_sessions": 3,
            "exact_expected_sessions": True,
        },
        "temporal_eligibility": {
            "run_mode": "FORWARD_SHADOW",
            "price_basis": "RAW",
            "eligibility": "FORWARD_PIT_QUALIFIED",
            "reason_code": "FORWARD_CUTOFF_SATISFIED",
            "authoritative_historical_pit": False,
            "forward_pit_qualified": True,
        },
        "asset_relative_strength": [
            {
                "asset": asset,
                "observed_session_count": 3,
                "cumulative_gross_return": UCR._render(
                    Decimal(1) + Decimal(relative), 12
                ),
                "relative_strength_vs_benchmark": UCR._render(
                    Decimal(relative), 12
                ),
                "classification": "UNDEFINED",
            }
            for asset, relative in (
                ("F", "0.10"), ("NVDA", "0.20"), ("SPY", "0")
            )
        ],
        "partial_window_assets": [],
        "group_relative_strength": [
            group(theme_id, values[theme_id]) for theme_id in sorted(values)
        ],
        "daily_relative_participation": [
            {
                "session_date": day,
                "eligible_non_benchmark_count": 2,
                "outperforming_benchmark_count": 1,
                "outperformance_participation_fraction": "0.5",
                "required_group_member_counts": [
                    {"group_id": theme_id, "member_count": 2}
                    for theme_id in sorted(values)
                ],
            }
            for day in daily_dates
        ],
        "retention": {
            "input_policy": "transient_memory_or_stdin_only",
            "output_policy": "non_reconstructive_derived_observations_only",
            "vendor_rows_emitted": False,
            "vendor_prices_emitted": False,
            "reconstructive_series_emitted": False,
        },
        "policies": {
            "leadership": {
                "policy_version": "leadership/test-v1",
                "policy_sha256": "d" * 64,
                "approval_status": "RATIFIED",
                "session_calendar_source": "synthetic_xnys/v1",
            },
            "universe": {
                "policy_version": "universe/test-v1",
                "policy_sha256": "e" * 64,
                "approval_status": "RATIFIED",
                "membership_kind": "point_in_time_source_coverage",
            },
            "taxonomy": {
                "policy_version": "taxonomy/test-v1",
                "policy_sha256": UPSTREAM_TAXONOMY_SHA,
                "approval_status": "RATIFIED",
                "effective_dated": True,
            },
        },
        "lineage": {
            "input_sha256": "f" * 64,
            "source_temporal_contract": "atlas_price_pit_contract.py/v0.1",
            "session_count": 4,
            "return_session_count": 3,
            "session_coverage_complete": True,
            "current_membership_backfill_authorized": False,
        },
    }
    for field in UCR.AUTHORITY_FIELDS:
        packet[field] = False
    return packet


def taxonomy_binding() -> dict:
    return {
        "taxonomy_contract_version": "theme_taxonomy/1",
        "taxonomy_id": "TAXONOMY.GLOBAL.2026",
        "taxonomy_decision_id": "DECISION.P2.01",
        "taxonomy_decision_sha256": TAXONOMY_DECISION_SHA,
        "taxonomy_packet_sha256": TAXONOMY_PACKET_SHA,
        "upstream_taxonomy_policy_sha256": UPSTREAM_TAXONOMY_SHA,
    }


def input_packet() -> dict:
    return {
        "schema_version": "us_capital_rotation_input/1",
        "as_of_date": "2026-08-20",
        "taxonomy_binding": taxonomy_binding(),
        "prior_observation": leadership_packet(
            "2026-08-18",
            "2026-08-18T20:20:00-04:00",
            {
                "THEME.COMPUTE": "0.30",
                "THEME.NETWORK": "0.10",
                "THEME.POWER": "-0.10",
            },
        ),
        "current_observation": leadership_packet(
            "2026-08-20",
            "2026-08-20T20:20:00-04:00",
            {
                "THEME.COMPUTE": "0.10",
                "THEME.NETWORK": "0.40",
                "THEME.POWER": "-0.20",
            },
        ),
    }


def policy(status: str = "RATIFIED") -> dict:
    ratified = status == "RATIFIED"
    return {
        "schema_version": "us_capital_rotation_policy/1",
        "policy_id": "POLICY.P2.02.TEST",
        "approval_status": status,
        "ratified_by": "Atlas CIO" if ratified else None,
        "ratified_at_utc": "2026-08-17T12:00:00Z" if ratified else None,
        "effective_from": "2026-08-01",
        "effective_to": None,
        "taxonomy_decision_sha256": TAXONOMY_DECISION_SHA,
        "taxonomy_packet_sha256": TAXONOMY_PACKET_SHA,
        "upstream_taxonomy_policy_sha256": UPSTREAM_TAXONOMY_SHA,
        "theme_ids": ["THEME.COMPUTE", "THEME.NETWORK", "THEME.POWER"],
        "ranking_metric": "GROUP_RELATIVE_STRENGTH_VS_BENCHMARK",
        "ranking_order": "DESCENDING",
        "tie_break": "THEME_ID_ASC",
        "top_count": 1,
        "bottom_count": 1,
        "maximum_calendar_gap_days": 5,
    }


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
    """A real ``theme_taxonomy_input/1`` graph document naming the exact US
    Theme ids this rotation fixture ranks on.

    This is a synthetic external graph, exactly like every other P2-01 input:
    the repository still ships no default taxonomy, and the committed approval
    authority registry is still empty, so the real producer will resolve this
    graph as a structurally valid ratification *claim* that is NOT authorized.

    ``ratified_at_utc`` is ``2026-08-18T12:00:00Z``. This fixture always
    intended a taxonomy that was already ratified and in force *before* both
    observations -- that is what ``effective_from`` ``2026-08-01`` says, and it
    is the same intent the rotation policy fixture states with its own
    ratified-before-prior ``2026-08-17T12:00:00Z``. The value it previously
    carried, ``2026-08-19T12:00:00Z``, silently contradicted that: it is later
    than the prior observation's own ``available_at``
    (``2026-08-18T20:20:00-04:00`` == ``2026-08-19T00:20:00Z``), so the
    document had not yet been ratified when the earlier of the two observations
    it now classifies was already available. Only the instant moves, and only
    to satisfy the temporal intent the fixture already declared; the approval
    window, decision identity, nodes, edges, memberships, evidence and every
    US-native leadership/benchmark/grouping value are untouched. The producer's
    source-evidence cutoff is this instant, and the evidence timestamps
    (``2026-08-18`` / ``2026-08-18T12:00:00Z``) still precede it.
    """
    theme_ids = sorted(US_THEME_IDS if theme_ids is None else theme_ids)
    return {
        "schema_version": "theme_taxonomy_input/1",
        "taxonomy_id": GRAPH_TAXONOMY_ID,
        "as_of_date": as_of_date,
        "approval": {
            "approval_status": "RATIFIED",
            "decision_id": GRAPH_DECISION_ID,
            "decision_sha256": GRAPH_DECISION_SHA,
            "ratified_by": "Atlas CIO",
            "ratified_at_utc": "2026-08-18T12:00:00Z",
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
        # The producer requires a ratified graph to name both allowed markets
        # somewhere; these memberships are the external document's own, they
        # are not created here and never become US rotation membership.
        "memberships": [
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
        ],
    }


def unratified_graph_document(as_of_date: str = "2026-08-20") -> dict:
    """The same graph whose approval is explicitly UNRATIFIED.

    The producer forbids ratification proof on an UNRATIFIED approval, so those
    two fields are cleared rather than merely relabelled.
    """
    document = taxonomy_graph_document(as_of_date=as_of_date)
    document["approval"].update({
        "approval_status": "UNRATIFIED",
        "ratified_by": None,
        "ratified_at_utc": None,
    })
    return document


def not_yet_effective_graph_document(as_of_date: str = "2026-08-20") -> dict:
    """A genuinely ratified approval whose effective window has not started."""
    document = taxonomy_graph_document(as_of_date=as_of_date)
    document["approval"]["effective_from"] = "2026-09-01"
    return document


def expired_graph_document(as_of_date: str = "2026-08-20") -> dict:
    """A genuinely ratified approval whose effective window already lapsed."""
    document = taxonomy_graph_document(as_of_date=as_of_date)
    document["approval"]["effective_to"] = "2026-08-10"
    return document


INEFFECTIVE_SOURCE_DOCUMENTS = {
    "unratified": unratified_graph_document,
    "not_yet_effective": not_yet_effective_graph_document,
    "expired": expired_graph_document,
}

# The prior observation this module's fixtures rank from, and the instant it
# became available. Every point-in-time fixture below is stated relative to
# these two facts rather than to a hard-coded literal.
PRIOR_OBSERVATION_DATE = "2026-08-18"
PRIOR_AVAILABLE_AT_UTC = "2026-08-19T00:20:00Z"


def prior_invalid_effective_from_graph_document(as_of_date: str = "2026-08-20") -> dict:
    """A genuinely ratified graph that only came into force *after* the prior
    observation, and is in force on the current one.

    This is the exact independently reproduced defect shape: prior observation
    ``2026-08-18``, taxonomy effective ``2026-08-19``, current observation
    ``2026-08-20``. The producer resolves it on the current decision date and
    correctly calls it a currently effective document, so nothing in the
    pre-existing not-effective gate refuses it -- yet it is not a taxonomy fact
    on the prior observation date at all.
    """
    document = taxonomy_graph_document(as_of_date=as_of_date)
    document["approval"]["effective_from"] = "2026-08-19"
    return document


def late_ratified_graph_document(as_of_date: str = "2026-08-20") -> dict:
    """A graph claiming to have been in force over both observations, but
    ratified one minute *after* the prior observation was already available.

    The instant is deliberately on the same calendar day as
    ``PRIOR_AVAILABLE_AT_UTC``, so a date-granularity comparison would pass it
    and only an instant comparison refuses it.
    """
    document = taxonomy_graph_document(as_of_date=as_of_date)
    document["approval"]["ratified_at_utc"] = "2026-08-19T00:21:00Z"
    return document


def prior_invalid_node_graph_document(
    as_of_date: str = "2026-08-20", theme_id: str = "THEME.NETWORK"
) -> dict:
    """An in-force graph whose approval covers both observations, but in which
    one ranked Theme node itself only becomes valid after the prior date.

    The containing edge moves with the node because the producer requires an
    edge interval to be contained in both endpoint node intervals; the node's
    own ``valid_from`` is the fact under test.
    """
    document = taxonomy_graph_document(as_of_date=as_of_date)
    for node in document["nodes"]:
        if node["theme_id"] == theme_id:
            node["valid_from"] = "2026-08-19"
    for edge in document["edges"]:
        if edge["to_theme_id"] == theme_id:
            edge["valid_from"] = "2026-08-19"
    return document


PRIOR_INVALID_SOURCE_DOCUMENTS = {
    "effective_from_after_prior": prior_invalid_effective_from_graph_document,
    "theme_node_valid_from_after_prior": prior_invalid_node_graph_document,
}


def taxonomy_source_bytes(document: dict) -> bytes:
    """Exact bytes a caller would hand to the CLI's ``--taxonomy-graph``."""
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def v2_bundle(document: dict) -> tuple[dict, dict]:
    """The legacy US input/policy pair, rebound to the real ``theme_taxonomy/2``
    identity the producer actually derives from that exact document.

    Only the taxonomy identity moves: the US benchmark, lookback, Theme set,
    upstream taxonomy-policy hash, ranking metric and bucket counts are the
    same values every legacy test uses.
    """
    reference = TT.build_packet(copy.deepcopy(document))
    value = input_packet()
    value["taxonomy_binding"] = {
        "taxonomy_contract_version": reference["contract_version"],
        "taxonomy_id": reference["taxonomy_id"],
        "taxonomy_decision_id": reference["approval"]["decision_id"],
        "taxonomy_decision_sha256": reference["approval"]["decision_sha256"],
        "taxonomy_packet_sha256": reference["payload_sha256"],
        "upstream_taxonomy_policy_sha256": UPSTREAM_TAXONOMY_SHA,
    }
    rotation_policy = policy()
    rotation_policy["taxonomy_decision_sha256"] = reference["approval"]["decision_sha256"]
    rotation_policy["taxonomy_packet_sha256"] = reference["payload_sha256"]
    return value, rotation_policy


def rehash_output(packet: dict) -> None:
    packet.pop("payload_sha256", None)
    packet["payload_sha256"] = UCR.payload_sha256(packet)


class USCapitalRotationTests(unittest.TestCase):
    def test_effective_ratified_policy_reproduces_rank_buckets_and_transitions(self):
        packet = UCR.build_packet(input_packet(), policy())
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(packet["rotation_policy_effective"])
        self.assertEqual(packet["top_themes"], ["THEME.NETWORK"])
        self.assertEqual(packet["bottom_themes"], ["THEME.POWER"])
        rows = {row["theme_id"]: row for row in packet["theme_observations"]}
        self.assertEqual(rows["THEME.NETWORK"]["prior_rank"], 2)
        self.assertEqual(rows["THEME.NETWORK"]["current_rank"], 1)
        self.assertEqual(rows["THEME.NETWORK"]["rank_change"], 1)
        self.assertEqual(rows["THEME.NETWORK"]["bucket_transition"], "MIDDLE_TO_TOP")
        self.assertEqual(rows["THEME.COMPUTE"]["bucket_transition"], "TOP_TO_MIDDLE")
        self.assertEqual(rows["THEME.POWER"]["bucket_transition"], "BOTTOM_TO_BOTTOM")
        self.assertEqual(rows["THEME.COMPUTE"]["relative_strength_change"], "-0.2")

    def test_unratified_policy_preserves_raw_delta_without_ranking_authority(self):
        packet = UCR.build_packet(input_packet(), policy("UNRATIFIED"))
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertIsNone(packet["ranking_method"])
        self.assertEqual(packet["top_themes"], [])
        for row in packet["theme_observations"]:
            self.assertIsNone(row["prior_rank"])
            self.assertIsNone(row["current_bucket"])
            self.assertIsNone(row["bucket_transition"])
            self.assertEqual(row["p2_state"], "UNDEFINED_PENDING_P2_05")

    def test_future_or_expired_policy_is_not_effective_for_both_observations(self):
        future = policy()
        future["effective_from"] = "2026-08-19"
        self.assertFalse(UCR.build_packet(input_packet(), future)["rotation_policy_effective"])
        expired = policy()
        expired["effective_to"] = "2026-08-20"
        self.assertFalse(UCR.build_packet(input_packet(), expired)["rotation_policy_effective"])

    def test_policy_must_be_ratified_before_prior_observation(self):
        value = policy()
        value["ratified_at_utc"] = "2026-08-19T00:21:00Z"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"):
            UCR.build_packet(input_packet(), value)
        value = policy("UNRATIFIED")
        value["ratified_by"] = "Fake proof"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UNRATIFIED_POLICY_PROOF_FORBIDDEN"):
            UCR.build_packet(input_packet(), value)

    def test_taxonomy_decision_packet_and_upstream_policy_are_exactly_bound(self):
        for field, code in (
            ("taxonomy_decision_sha256", "POLICY_TAXONOMY_DECISION_MISMATCH"),
            ("taxonomy_packet_sha256", "POLICY_TAXONOMY_PACKET_MISMATCH"),
            ("upstream_taxonomy_policy_sha256", "POLICY_UPSTREAM_TAXONOMY_MISMATCH"),
        ):
            with self.subTest(field=field):
                value = policy()
                value[field] = "9" * 64
                with self.assertRaisesRegex(UCR.USCapitalRotationError, code):
                    UCR.build_packet(input_packet(), value)
        value = input_packet()
        value["current_observation"]["policies"]["taxonomy"]["policy_sha256"] = "9" * 64
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_TAXONOMY_BINDING_MISMATCH"):
            UCR.build_packet(value, policy())

    def test_theme_sets_cannot_drift_or_expand_outside_policy(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["group_id"] = "THEME.EXTRA"
        for row in value["current_observation"]["daily_relative_participation"]:
            row["required_group_member_counts"][0]["group_id"] = "THEME.EXTRA"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_THEME_SET_DRIFT"):
            UCR.build_packet(value, policy())
        value_policy = policy()
        value_policy["theme_ids"] = value_policy["theme_ids"][:-1]
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_THEME_SET_MISMATCH"):
            UCR.build_packet(input_packet(), value_policy)

    def test_observation_date_available_time_and_gap_are_fail_closed(self):
        value = input_packet()
        prior = value["prior_observation"]
        prior["observation_date"] = "2026-08-20"
        prior["available_at"] = "2026-08-20T19:20:00-04:00"
        prior["window"].update({
            "first_input_session": "2026-08-17",
            "first_return_session": "2026-08-18",
            "last_return_session": "2026-08-20",
        })
        for row, day in zip(
            prior["daily_relative_participation"],
            ("2026-08-18", "2026-08-19", "2026-08-20"),
        ):
            row["session_date"] = day
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "OBSERVATION_DATE_ORDER_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["prior_observation"]["available_at"] = "2026-08-21T00:21:00Z"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "OBSERVATION_AVAILABLE_AT_ORDER_INVALID"):
            UCR.build_packet(value, policy())
        value_policy = policy()
        value_policy["maximum_calendar_gap_days"] = 1
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "OBSERVATION_GAP_EXCEEDS_POLICY"):
            UCR.build_packet(input_packet(), value_policy)

    def test_only_forward_pit_closed_authority_upstream_is_accepted(self):
        value = input_packet()
        value["prior_observation"]["status"] = "CAUSAL_RESEARCH_ONLY"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_IDENTITY_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["current_observation"]["ranking_authorized"] = True
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_AUTHORITY_EXPANDED"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["prior_observation"]["policies"]["universe"]["approval_status"] = "UNRATIFIED"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_POLICY_UNRATIFIED"):
            UCR.build_packet(value, policy())

    def test_group_schema_order_counts_and_numbers_are_strict(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"].reverse()
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_GROUP_ORDER_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["observed_session_count"] = 2
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_GROUP_SEMANTICS_INVALID"):
            UCR.build_packet(value, policy())
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["relative_strength_vs_benchmark"] = "NaN"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "UPSTREAM_GROUP_RELATIVE_STRENGTH_INVALID"):
            UCR.build_packet(value, policy())

    def test_production_leadership_validator_runs_before_rotation(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0][
            "relative_strength_vs_benchmark"
        ] = "0.9"
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            "UPSTREAM_PRODUCTION_VALIDATION_FAILED:current:.*OUTPUT_GROUP_RS_MISMATCH",
        ):
            UCR.build_packet(value, policy())

    def test_policy_is_exact_and_top_bottom_cannot_overlap(self):
        value = policy()
        value["score"] = 1
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_FIELDS_MISMATCH"):
            UCR.build_packet(input_packet(), value)
        value = policy()
        value["top_count"] = 2
        value["bottom_count"] = 2
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "POLICY_BUCKETS_OVERLAP"):
            UCR.build_packet(input_packet(), value)

    def test_equal_metric_uses_explicit_theme_id_tie_break(self):
        value = input_packet()
        value["current_observation"]["group_relative_strength"][0]["relative_strength_vs_benchmark"] = "0.4"
        value["current_observation"]["group_relative_strength"][0]["cumulative_gross_return"] = "1.4"
        packet = UCR.build_packet(value, policy())
        self.assertEqual(packet["top_themes"], ["THEME.COMPUTE"])
        rows = {row["theme_id"]: row for row in packet["theme_observations"]}
        self.assertEqual(rows["THEME.COMPUTE"]["current_rank"], 1)
        self.assertEqual(rows["THEME.NETWORK"]["current_rank"], 2)

    def test_output_is_deterministic_alphabetical_and_digest_bound(self):
        first = UCR.build_packet(input_packet(), policy())
        second = UCR.build_packet(copy.deepcopy(input_packet()), copy.deepcopy(policy()))
        self.assertEqual(first, second)
        self.assertEqual(
            [row["theme_id"] for row in second["theme_observations"]],
            ["THEME.COMPUTE", "THEME.NETWORK", "THEME.POWER"],
        )
        digest = second.pop("payload_sha256")
        self.assertEqual(digest, UCR.payload_sha256(second))

    def test_self_rehashed_rank_and_delta_tamper_fail_closed(self):
        packet = UCR.build_packet(input_packet(), policy())
        packet["theme_observations"][0]["current_rank"] = 1
        packet["payload_sha256"] = UCR.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_RANK_BUCKET_MISMATCH"
        ):
            UCR.validate_packet(packet)

        packet = UCR.build_packet(input_packet(), policy("UNRATIFIED"))
        packet["theme_observations"][0]["relative_strength_change"] = "9"
        packet["payload_sha256"] = UCR.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_THEME_DERIVATION_MISMATCH"
        ):
            UCR.validate_packet(packet)

    def test_observation_pair_persists_available_at_for_standalone_reproof(self):
        packet = UCR.build_packet(input_packet(), policy())
        pair = packet["observation_pair"]
        self.assertEqual(pair["prior_available_at"], "2026-08-19T00:20:00+00:00")
        self.assertEqual(pair["current_available_at"], "2026-08-21T00:20:00+00:00")
        # validate_packet() takes only the persisted packet -- neither upstream
        # Leadership packet is passed in, so a pass here is itself the proof
        # that temporal order and ratified-before-prior are standalone-provable.
        UCR.validate_packet(copy.deepcopy(packet))

    def test_revision_a_and_b_are_each_independently_standalone_verifiable(self):
        # Revision A: original source pointer.
        revision_a = UCR.build_packet(input_packet(), policy())
        # Revision B: source pointer moves -- later available_at for both
        # observations, as if a fresher upstream snapshot had been read.
        moved = input_packet()
        moved["prior_observation"]["available_at"] = "2026-08-19T09:00:00-04:00"
        moved["current_observation"]["available_at"] = "2026-08-21T09:00:00-04:00"
        revision_b = UCR.build_packet(moved, policy())
        self.assertNotEqual(
            revision_a["observation_pair"]["prior_available_at"],
            revision_b["observation_pair"]["prior_available_at"],
        )
        # Each revision is re-verified from nothing but its own persisted
        # packet -- no fault injection, no monkeypatch, no shared live state.
        UCR.validate_packet(copy.deepcopy(revision_a))
        UCR.validate_packet(copy.deepcopy(revision_b))
        self.assertEqual(
            revision_a["observation_pair"]["prior_available_at"],
            "2026-08-19T00:20:00+00:00",
        )
        self.assertEqual(
            revision_b["observation_pair"]["prior_available_at"],
            "2026-08-19T13:00:00+00:00",
        )

    def test_validate_packet_rejects_missing_invalid_naive_and_malformed_timezone_available_at(self):
        for field in ("prior_available_at", "current_available_at"):
            with self.subTest(field=field, case="missing"):
                packet = UCR.build_packet(input_packet(), policy())
                del packet["observation_pair"][field]
                with self.assertRaisesRegex(
                    UCR.USCapitalRotationError, "OUTPUT_OBSERVATION_PAIR_INVALID"
                ):
                    UCR.validate_packet(packet)
            code = (
                "OUTPUT_PRIOR_AVAILABLE_AT_INVALID"
                if field == "prior_available_at"
                else "OUTPUT_CURRENT_AVAILABLE_AT_INVALID"
            )
            for case, bad_value in (
                ("not_a_string", 12345),
                ("unparseable", "not-a-timestamp"),
                ("naive_no_offset", "2026-08-19T00:20:00"),
                ("malformed_timezone", "2026-08-19T00:20:00PST"),
            ):
                with self.subTest(field=field, case=case):
                    packet = UCR.build_packet(input_packet(), policy())
                    packet["observation_pair"][field] = bad_value
                    with self.assertRaisesRegex(UCR.USCapitalRotationError, code):
                        UCR.validate_packet(packet)

    def test_validate_packet_rejects_available_at_order_and_ratification_tamper_after_self_rehash(self):
        # Tamper 1: swap persisted available_at order, then re-hash the
        # packet so the top-level payload_sha256 digest matches the
        # tampered content -- the temporal-order re-derivation, not the
        # digest check, must be what catches this.
        packet = UCR.build_packet(input_packet(), policy())
        pair = packet["observation_pair"]
        pair["prior_available_at"], pair["current_available_at"] = (
            pair["current_available_at"], pair["prior_available_at"],
        )
        packet["payload_sha256"] = UCR.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_AVAILABLE_AT_ORDER_INVALID"
        ):
            UCR.validate_packet(packet)

        # Tamper 2: push the persisted prior_available_at earlier than the
        # policy's own ratified_at_utc (as if the prior observation had
        # been available before the policy was ever ratified), self-rehash,
        # and confirm the ratified-before-prior re-proof still fires even
        # though build_packet() would never have produced this shape.
        packet = UCR.build_packet(input_packet(), policy())
        packet["observation_pair"]["prior_available_at"] = "2026-08-16T00:00:00+00:00"
        packet["payload_sha256"] = UCR.payload_sha256({
            key: value for key, value in packet.items() if key != "payload_sha256"
        })
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_POLICY_RATIFIED_AFTER_PRIOR_OBSERVATION"
        ):
            UCR.validate_packet(packet)

    def test_p2_state_regime_stage_production_and_trading_remain_closed(self):
        packet = UCR.build_packet(input_packet(), policy())
        self.assertTrue(packet["authority"]["theme_ranking_authorized"])
        self.assertTrue(packet["authority"]["top_bottom_bucket_authorized"])
        self.assertTrue(packet["authority"]["bucket_transition_authorized"])
        for field in (
            "p2_state_vocabulary_authorized", "state_ledger_authorized",
            "regime_input_authorized", "candidate_ranking_authorized",
            "stage_promotion_authorized", "production_authorized", "trading_authorized",
        ):
            self.assertFalse(packet["authority"][field], field)
        self.assertIn("P2_STATE_VOCABULARY_PENDING_P2_05", packet["unresolved_boundaries"])

    def test_contract_tamper_input_extra_and_default_policy_absence(self):
        contract = UCR.load_contract()
        contract["authority"]["trading_authorized"] = True
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "CONTRACT_FIELD_MISMATCH"):
            UCR.build_packet(input_packet(), policy(), contract=contract)
        value = input_packet()
        value["action"] = "BUY"
        with self.assertRaisesRegex(UCR.USCapitalRotationError, "INPUT_FIELDS_MISMATCH"):
            UCR.build_packet(value, policy())
        self.assertFalse((ROOT / "config" / "us_capital_rotation_policy.json").exists())
        source_text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertNotIn("import requests", source_text)
        self.assertNotIn("urllib.request", source_text)

    def test_cli_is_temp_only_atomic_and_rejects_tracked_output(self):
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            policy_path = temp / "policy.json"
            output_path = temp / "output.json"
            input_path.write_text(json.dumps(input_packet()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable, str(MODULE_PATH), str(input_path),
                    "--policy", str(policy_path), "--out", str(output_path),
                ],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertEqual(json.loads(output_path.read_text()), UCR.build_packet(input_packet(), policy()))
            output_path.write_text("sentinel\n", encoding="utf-8")
            input_path.write_text("{}\n", encoding="utf-8")
            self.assertEqual(UCR.run(input_path, policy_path, output_path), 1)
            self.assertEqual(output_path.read_text(), "sentinel\n")
        tracked = ROOT / ".test-us-capital-rotation-output.json"
        self.assertFalse(tracked.exists())
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            policy_path = temp / "policy.json"
            input_path.write_text(json.dumps(input_packet()), encoding="utf-8")
            policy_path.write_text(json.dumps(policy()), encoding="utf-8")
            self.assertEqual(UCR.run(input_path, policy_path, tracked), 1)
        self.assertFalse(tracked.exists())


class USCapitalRotationTaxonomyV2Tests(unittest.TestCase):
    """Real ``theme_taxonomy/2`` source consumption in the US rotation consumer.

    Every assertion is against the actual P2-01 producer output, so these
    prove the real producer is genuinely invoked, that its verdict is recorded
    exactly as returned (not authorized, because the committed approval
    authority registry is empty), and that none of it creates authority or
    changes US-native leadership/benchmark/grouping semantics.
    """

    def setUp(self):
        self.document = taxonomy_graph_document()
        self.source_bytes = taxonomy_source_bytes(self.document)
        self.reference = TT.build_packet(copy.deepcopy(self.document))

    def build(self, value, rotation_policy, source_bytes=None):
        return UCR.build_packet(
            value, rotation_policy,
            taxonomy_source_bytes=(
                self.source_bytes if source_bytes is None else source_bytes
            ),
        )

    def test_real_producer_packet_is_consumed_and_recorded_exactly(self):
        packet = self.build(*v2_bundle(self.document))
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
        # The output schema is unchanged; only the binding gains derived members.
        self.assertEqual(packet["schema_version"], "us_capital_rotation_packet/2")
        self.assertEqual(packet["contract_version"], "us_capital_rotation/2")

    def test_legacy_binding_is_unchanged_and_refuses_a_graph(self):
        legacy = UCR.build_packet(input_packet(), policy())
        self.assertEqual(
            set(legacy["taxonomy_binding"]), set(UCR.TAXONOMY_BINDING_FIELDS)
        )
        self.assertEqual(
            legacy["taxonomy_binding"]["taxonomy_contract_version"],
            UCR.load_contract()["taxonomy_contract_version"],
        )
        self.assertEqual(
            legacy["taxonomy_binding"]["taxonomy_contract_version"],
            "theme_taxonomy/1",
        )
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            "TAXONOMY_SOURCE_NOT_ALLOWED_FOR_LEGACY_BINDING",
        ):
            UCR.build_packet(
                input_packet(), policy(), taxonomy_source_bytes=self.source_bytes
            )
        # A persisted legacy packet also stays validatable exactly as before,
        # and still refuses an after-the-fact graph.
        UCR.validate_packet(copy.deepcopy(legacy))
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            "TAXONOMY_SOURCE_NOT_ALLOWED_FOR_LEGACY_BINDING",
        ):
            UCR.validate_packet(
                copy.deepcopy(legacy), taxonomy_source_bytes=self.source_bytes
            )

    def test_relabelling_a_legacy_binding_as_v2_never_succeeds(self):
        # The exact failure mode this slice exists to prevent: editing the
        # version string on an opaque binding must not buy a real taxonomy.
        value = input_packet()
        value["taxonomy_binding"]["taxonomy_contract_version"] = "theme_taxonomy/2"
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_SOURCE_REQUIRED_FOR_V2_BINDING"
        ):
            UCR.build_packet(value, policy())
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_SOURCE_IDENTITY_MISMATCH"
        ):
            self.build(value, policy())
        # An unknown third version is refused outright.
        value = input_packet()
        value["taxonomy_binding"]["taxonomy_contract_version"] = "theme_taxonomy/9"
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_CONTRACT_VERSION_MISMATCH"
        ):
            UCR.build_packet(value, policy())

    def test_declared_identity_and_digest_cannot_replace_the_derived_ones(self):
        value, rotation_policy = v2_bundle(self.document)
        value["taxonomy_binding"]["taxonomy_packet_sha256"] = "9" * 64
        rotation_policy["taxonomy_packet_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            "TAXONOMY_PACKET_SHA_NOT_DERIVED_FROM_SOURCE",
        ):
            self.build(value, rotation_policy)
        for field in ("taxonomy_id", "taxonomy_decision_id"):
            with self.subTest(field=field):
                value, rotation_policy = v2_bundle(self.document)
                value["taxonomy_binding"][field] = "IDENTITY.ATTACKER"
                with self.assertRaisesRegex(
                    UCR.USCapitalRotationError, "TAXONOMY_SOURCE_IDENTITY_MISMATCH"
                ):
                    self.build(value, rotation_policy)
        # A caller cannot pre-declare the derived fields either: at build time
        # the binding must carry exactly the six legacy identity fields.
        value, rotation_policy = v2_bundle(self.document)
        value["taxonomy_binding"]["theme_membership_authorized"] = True
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_BINDING_FIELDS_MISMATCH"
        ):
            self.build(value, rotation_policy)

    def test_semantic_and_byte_only_source_tamper_are_both_detected(self):
        # Semantic tamper: the producer derives a different packet digest.
        value, rotation_policy = v2_bundle(self.document)
        tampered = copy.deepcopy(self.document)
        tampered["nodes"][0]["description"] = "attacker rewrote the graph"
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            "TAXONOMY_PACKET_SHA_NOT_DERIVED_FROM_SOURCE",
        ):
            self.build(
                value, rotation_policy, source_bytes=taxonomy_source_bytes(tampered)
            )
        # Byte-only tamper: identical parsed graph, different source bytes --
        # caught because the source digest is bound too, not just the packet.
        packet = self.build(*v2_bundle(self.document))
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_TAXONOMY_DERIVATION_MISMATCH"
        ):
            UCR.validate_packet(
                copy.deepcopy(packet),
                taxonomy_source_bytes=self.source_bytes + b" ",
            )
        # A source that is not even a graph document fails inside the producer.
        value, rotation_policy = v2_bundle(self.document)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_SOURCE_REJECTED_BY_PRODUCER"
        ):
            self.build(value, rotation_policy, source_bytes=b'{"schema_version":1}')

    def test_as_of_date_of_the_consumed_graph_must_match_the_decision_date(self):
        # Replaying another day's graph is a different point-in-time fact.
        other_day = taxonomy_graph_document(as_of_date="2026-08-19")
        value, rotation_policy = v2_bundle(other_day)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_AS_OF_DATE_MISMATCH"
        ):
            UCR.build_packet(
                value, rotation_policy,
                taxonomy_source_bytes=taxonomy_source_bytes(other_day),
            )

    def test_policy_theme_ids_must_be_active_nodes_of_the_consumed_graph(self):
        incomplete = taxonomy_graph_document(theme_ids=US_THEME_IDS[:-1])
        value, rotation_policy = v2_bundle(incomplete)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_THEME_NODE_NOT_ACTIVE:THEME.POWER"
        ):
            UCR.build_packet(
                value, rotation_policy,
                taxonomy_source_bytes=taxonomy_source_bytes(incomplete),
            )
        # A node that exists but has lapsed on this decision date is equally
        # unusable -- the producer's own interval semantics, reused verbatim.
        expired = taxonomy_graph_document()
        target = "THEME.NETWORK"
        for node in expired["nodes"]:
            if node["theme_id"] == target:
                node["valid_to"] = "2026-08-20"
        for edge in expired["edges"]:
            if edge["to_theme_id"] == target:
                edge["valid_to"] = "2026-08-20"
        value, rotation_policy = v2_bundle(expired)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, f"TAXONOMY_THEME_NODE_NOT_ACTIVE:{target}"
        ):
            UCR.build_packet(
                value, rotation_policy,
                taxonomy_source_bytes=taxonomy_source_bytes(expired),
            )

    def test_standalone_validator_reproves_derived_fields_and_grants_nothing(self):
        packet = self.build(*v2_bundle(self.document))
        # A persisted packet stays standalone-verifiable without the graph.
        UCR.validate_packet(copy.deepcopy(packet))
        UCR.validate_packet(
            copy.deepcopy(packet), taxonomy_source_bytes=self.source_bytes
        )
        forged = copy.deepcopy(packet)
        forged["taxonomy_binding"]["theme_membership_authorized"] = True
        forged["taxonomy_binding"]["taxonomy_authority_status"] = "AUTHORIZED"
        forged["taxonomy_binding"]["taxonomy_graph_status"] = (
            "AUTHORIZED_EFFECTIVE_GRAPH"
        )
        rehash_output(forged)
        # Packet-only consumers -- including the shared rotation state ledger,
        # which calls validate_packet() with no taxonomy argument at all --
        # must re-prove the embedded source too.
        for source in (None, self.source_bytes):
            with self.assertRaisesRegex(
                UCR.USCapitalRotationError, "OUTPUT_TAXONOMY_DERIVATION_MISMATCH"
            ):
                UCR.validate_packet(copy.deepcopy(forged), taxonomy_source_bytes=source)
        # Re-signing a false source digest cannot make it exact evidence.
        forged = copy.deepcopy(packet)
        forged["taxonomy_binding"]["taxonomy_source_sha256"] = "f" * 64
        rehash_output(forged)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "OUTPUT_TAXONOMY_DERIVATION_MISMATCH"
        ):
            UCR.validate_packet(forged)
        # Swapping the embedded source for another day's graph is caught by the
        # producer's own decision-date check, not by the packet digest.
        forged = copy.deepcopy(packet)
        replayed = taxonomy_source_bytes(taxonomy_graph_document(as_of_date="2026-08-19"))
        forged["taxonomy_binding"]["taxonomy_source_json"] = replayed.decode("utf-8")
        forged["taxonomy_binding"]["taxonomy_source_sha256"] = hashlib.sha256(
            replayed
        ).hexdigest()
        rehash_output(forged)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_AS_OF_DATE_MISMATCH"
        ):
            UCR.validate_packet(forged)

    def test_us_leadership_benchmark_and_taxonomy_policy_gates_are_unchanged(self):
        # The US-native upstream taxonomy *policy* hash is a different fact
        # from the P2-01 graph and still binds both Leadership observations.
        value, rotation_policy = v2_bundle(self.document)
        value["current_observation"]["policies"]["taxonomy"]["policy_sha256"] = "9" * 64
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "UPSTREAM_TAXONOMY_BINDING_MISMATCH"
        ):
            self.build(value, rotation_policy)
        # The US-native group measurement is still re-derived by the P1-US-06
        # production validator before rotation ever sees a Theme row.
        value, rotation_policy = v2_bundle(self.document)
        value["current_observation"]["group_relative_strength"][0][
            "relative_strength_vs_benchmark"
        ] = "0.9"
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            "UPSTREAM_PRODUCTION_VALIDATION_FAILED:current:.*OUTPUT_GROUP_RS_MISMATCH",
        ):
            self.build(value, rotation_policy)
        # The exact Theme set still gates too.
        value, rotation_policy = v2_bundle(self.document)
        rotation_policy["theme_ids"] = rotation_policy["theme_ids"][:-1]
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "POLICY_THEME_SET_MISMATCH"
        ):
            self.build(value, rotation_policy)

    def test_v2_consumption_adds_no_authority_and_no_ranking_change(self):
        legacy = UCR.build_packet(input_packet(), policy())
        consumed = self.build(*v2_bundle(self.document))
        self.assertEqual(consumed["authority"], legacy["authority"])
        self.assertEqual(consumed["theme_observations"], legacy["theme_observations"])
        self.assertEqual(consumed["top_themes"], legacy["top_themes"])
        self.assertEqual(consumed["bottom_themes"], legacy["bottom_themes"])
        self.assertEqual(consumed["benchmark_asset"], legacy["benchmark_asset"])
        self.assertEqual(consumed["ranking_method"], legacy["ranking_method"])
        self.assertEqual(consumed["status"], legacy["status"])
        self.assertEqual(
            consumed["unresolved_boundaries"], legacy["unresolved_boundaries"]
        )
        self.assertIn(
            "THEME_TAXONOMY_OPERATIONAL_POPULATION_NOT_IMPLEMENTED",
            consumed["unresolved_boundaries"],
        )
        for field in (
            "p2_state_vocabulary_authorized", "state_ledger_authorized",
            "regime_input_authorized", "candidate_ranking_authorized",
            "stage_promotion_authorized", "production_authorized",
            "trading_authorized",
        ):
            self.assertFalse(consumed["authority"][field], field)

    def test_unratified_rotation_policy_with_a_real_graph_still_emits_no_ranking(self):
        value, rotation_policy = v2_bundle(self.document)
        unratified = policy("UNRATIFIED")
        unratified["taxonomy_decision_sha256"] = rotation_policy[
            "taxonomy_decision_sha256"
        ]
        unratified["taxonomy_packet_sha256"] = rotation_policy["taxonomy_packet_sha256"]
        packet = self.build(value, unratified)
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertIsNone(packet["ranking_method"])
        self.assertEqual(packet["top_themes"], [])
        self.assertEqual(packet["bottom_themes"], [])
        for row in packet["theme_observations"]:
            self.assertIsNone(row["current_bucket"])
            self.assertIsNone(row["bucket_transition"])
        # A real, structurally valid graph never substitutes for rotation
        # ratification, and never authorizes membership either.
        self.assertFalse(packet["authority"]["theme_ranking_authorized"])
        self.assertFalse(
            packet["taxonomy_binding"]["theme_membership_authorized"]
        )
        UCR.validate_packet(copy.deepcopy(packet))

    def test_producer_itself_calls_each_ineffective_source_state_a_draft_graph(self):
        # Guards the negatives below from going vacuous: each fixture must
        # genuinely reach the producer's DRAFT_OR_NOT_EFFECTIVE_GRAPH verdict,
        # for the distinct approval reason it is named after, rather than being
        # rejected earlier for some unrelated structural defect.
        for name, factory in INEFFECTIVE_SOURCE_DOCUMENTS.items():
            with self.subTest(state=name):
                reference = TT.build_packet(factory())
                self.assertEqual(
                    reference["graph_status"], "DRAFT_OR_NOT_EFFECTIVE_GRAPH"
                )
                self.assertFalse(
                    reference["structurally_eligible_ratification_claim"]
                )
                self.assertFalse(reference["theme_membership_authorized"])
        self.assertEqual(
            TT.build_packet(unratified_graph_document())["approval"][
                "approval_status"
            ],
            "UNRATIFIED",
        )
        # The two window states are genuinely ratified documents; only their
        # effective interval excludes this decision date.
        for factory in (
            not_yet_effective_graph_document, expired_graph_document,
        ):
            approval = TT.build_packet(factory())["approval"]
            self.assertEqual(approval["approval_status"], "RATIFIED")

    def test_ineffective_taxonomy_source_withholds_ranking_from_a_ratified_policy(self):
        # The corrected behaviour: a RATIFIED, covering rotation policy is not
        # enough. Without an effective taxonomy source there is no ranking.
        for name, factory in INEFFECTIVE_SOURCE_DOCUMENTS.items():
            with self.subTest(state=name):
                document = factory()
                value, rotation_policy = v2_bundle(document)
                self.assertEqual(rotation_policy["approval_status"], "RATIFIED")
                packet = UCR.build_packet(
                    value, rotation_policy,
                    taxonomy_source_bytes=taxonomy_source_bytes(document),
                )
                self.assertEqual(
                    packet["taxonomy_binding"]["taxonomy_graph_status"],
                    "DRAFT_OR_NOT_EFFECTIVE_GRAPH",
                )
                self.assertEqual(packet["status"], "TAXONOMY_SOURCE_NOT_EFFECTIVE")
                # The rotation policy's own ratification is still reported
                # truthfully; the two gates stay separately auditable.
                self.assertTrue(packet["rotation_policy_effective"])
                self.assertIsNone(packet["ranking_method"])
                self.assertEqual(packet["top_themes"], [])
                self.assertEqual(packet["bottom_themes"], [])
                for row in packet["theme_observations"]:
                    for field in (
                        "prior_rank", "current_rank", "rank_change",
                        "prior_bucket", "current_bucket", "bucket_transition",
                    ):
                        self.assertIsNone(row[field], f"{name}:{field}")
                    # The unranked measurement itself is still emitted.
                    self.assertIsNotNone(
                        row["current_relative_strength_vs_benchmark"]
                    )
                for field in (
                    "theme_ranking_authorized", "top_bottom_bucket_authorized",
                    "bucket_transition_authorized",
                ):
                    self.assertFalse(packet["authority"][field], f"{name}:{field}")
                self.assertFalse(
                    packet["taxonomy_binding"]["theme_membership_authorized"]
                )
                # A packet built this way is self-consistent and re-validates.
                UCR.validate_packet(copy.deepcopy(packet))

    def test_ineffective_source_beats_ranking_even_with_an_unratified_policy(self):
        # When both gates are shut the pre-existing policy status is still the
        # one reported, so the legacy POLICY_NOT_EFFECTIVE invariant is intact.
        document = unratified_graph_document()
        value, rotation_policy = v2_bundle(document)
        rotation_policy.update({
            "approval_status": "UNRATIFIED", "ratified_by": None,
            "ratified_at_utc": None,
        })
        packet = UCR.build_packet(
            value, rotation_policy,
            taxonomy_source_bytes=taxonomy_source_bytes(document),
        )
        self.assertEqual(packet["status"], "POLICY_NOT_EFFECTIVE")
        self.assertFalse(packet["rotation_policy_effective"])
        self.assertEqual(packet["top_themes"], [])
        self.assertFalse(packet["authority"]["theme_ranking_authorized"])
        UCR.validate_packet(copy.deepcopy(packet))

    def test_self_rehashed_ranking_over_an_ineffective_source_is_refused(self):
        """The standalone validator, not just build_packet(), must refuse.

        Every digest in these forgeries is re-signed, and the embedded source
        is a real graph the producer accepts -- so nothing here is caught by a
        hash check. They are caught only because validate_packet() re-derives
        the producer's own effectivity verdict for itself.
        """
        ranked = self.build(*v2_bundle(self.document))
        self.assertEqual(ranked["status"], "ROTATION_BUCKETS_OBSERVED")
        for name, factory in INEFFECTIVE_SOURCE_DOCUMENTS.items():
            document = factory()
            source = taxonomy_source_bytes(document)
            value, rotation_policy = v2_bundle(document)
            honest = UCR.build_packet(
                value, rotation_policy, taxonomy_source_bytes=source
            )
            with self.subTest(state=name, forgery="full_ranking"):
                # Splice the real ranked results of the effective run onto the
                # draft-source packet and re-sign the whole packet.
                forged = copy.deepcopy(honest)
                forged["status"] = ranked["status"]
                forged["ranking_method"] = copy.deepcopy(ranked["ranking_method"])
                forged["top_themes"] = list(ranked["top_themes"])
                forged["bottom_themes"] = list(ranked["bottom_themes"])
                forged["theme_observations"] = copy.deepcopy(
                    ranked["theme_observations"]
                )
                forged["authority"] = copy.deepcopy(ranked["authority"])
                rehash_output(forged)
                self.assertEqual(forged["payload_sha256"], UCR.payload_sha256({
                    key: forged[key] for key in forged if key != "payload_sha256"
                }))
                for source_argument in (None, source):
                    with self.assertRaisesRegex(
                        UCR.USCapitalRotationError, "OUTPUT_UNAUTHORIZED_RANKING"
                    ):
                        UCR.validate_packet(
                            copy.deepcopy(forged),
                            taxonomy_source_bytes=source_argument,
                        )
            with self.subTest(state=name, forgery="status_only"):
                # Claiming the observed-buckets status without any ranking rows
                # is refused by the taxonomy boundary check specifically.
                forged = copy.deepcopy(honest)
                forged["status"] = "ROTATION_BUCKETS_OBSERVED"
                rehash_output(forged)
                with self.assertRaisesRegex(
                    UCR.USCapitalRotationError,
                    "OUTPUT_INEFFECTIVE_TAXONOMY_BOUNDARY_MISMATCH",
                ):
                    UCR.validate_packet(forged)
            with self.subTest(state=name, forgery="relabelled_status"):
                # Relabelling the draft verdict as an effective one is caught
                # by re-deriving it from the embedded source.
                forged = copy.deepcopy(honest)
                forged["taxonomy_binding"]["taxonomy_graph_status"] = (
                    "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED"
                )
                rehash_output(forged)
                with self.assertRaisesRegex(
                    UCR.USCapitalRotationError,
                    "OUTPUT_TAXONOMY_DERIVATION_MISMATCH",
                ):
                    UCR.validate_packet(forged)

    def test_draft_source_packet_is_refused_by_the_shared_state_ledger(self):
        """The downstream money-path consumer refuses it too.

        The ledger admits rotation state only from an observed-buckets packet
        with bucket-transition authority, so withholding ranking here also
        keeps a draft-sourced packet out of rotation state history. The packet
        is structurally valid -- the ledger's own call to this module's
        validate_packet() passes -- and is rejected on identity alone.
        """
        from rotation import rotation_state_ledger as RSL

        document = unratified_graph_document()
        value, rotation_policy = v2_bundle(document)
        packet = UCR.build_packet(
            value, rotation_policy,
            taxonomy_source_bytes=taxonomy_source_bytes(document),
        )
        self.assertNotEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertFalse(packet["authority"]["bucket_transition_authorized"])
        # The rotation packet is validated before the state policy is even
        # inspected, so this fails on the packet, not on the placeholder.
        with self.assertRaisesRegex(
            RSL.RotationStateLedgerError, "ROTATION_PACKET_IDENTITY_INVALID"
        ):
            RSL.apply_rotation(copy.deepcopy(packet), {})

    def test_an_effective_but_unauthorized_source_still_ranks(self):
        # The gate is exactly the producer's not-effective verdict and nothing
        # wider: a real effective-dated document that the separate approval
        # authority registry does not authorize still ranks, exactly as before.
        packet = self.build(*v2_bundle(self.document))
        self.assertEqual(
            packet["taxonomy_binding"]["taxonomy_graph_status"],
            "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED",
        )
        self.assertNotEqual(
            packet["taxonomy_binding"]["taxonomy_authority_status"], "AUTHORIZED"
        )
        self.assertFalse(packet["taxonomy_binding"]["theme_membership_authorized"])
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(packet["authority"]["theme_ranking_authorized"])
        self.assertEqual(packet["top_themes"], ["THEME.NETWORK"])
        # And the legacy /1 packet is byte-identical to what it always was:
        # the new gate is inert for a binding that carries no producer verdict.
        legacy = UCR.build_packet(input_packet(), policy())
        self.assertEqual(legacy["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(legacy["rotation_policy_effective"])
        self.assertNotIn("taxonomy_graph_status", legacy["taxonomy_binding"])
        self.assertEqual(legacy["top_themes"], ["THEME.NETWORK"])
        self.assertEqual(legacy["bottom_themes"], ["THEME.POWER"])
        for row in legacy["theme_observations"]:
            self.assertIsNotNone(row["current_rank"])
            self.assertIsNotNone(row["bucket_transition"])

    def test_v2_output_is_deterministic_and_the_cli_consumes_the_graph(self):
        value, rotation_policy = v2_bundle(self.document)
        first = self.build(copy.deepcopy(value), copy.deepcopy(rotation_policy))
        second = self.build(copy.deepcopy(value), copy.deepcopy(rotation_policy))
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as raw:
            temp = Path(raw)
            input_path = temp / "input.json"
            policy_path = temp / "policy.json"
            graph_path = temp / "graph.json"
            output_path = temp / "output.json"
            input_path.write_text(
                json.dumps(value, ensure_ascii=False), encoding="utf-8"
            )
            policy_path.write_text(
                json.dumps(rotation_policy, ensure_ascii=False), encoding="utf-8"
            )
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
            self.assertEqual(UCR.run(input_path, policy_path, output_path), 1)
            self.assertEqual(json.loads(output_path.read_text()), first)
            # A graph path that does not exist fails closed too.
            self.assertEqual(
                UCR.run(
                    input_path, policy_path, output_path, temp / "missing-graph.json"
                ),
                1,
            )
            self.assertEqual(json.loads(output_path.read_text()), first)


def utc(value: str):
    """Parse a UTC instant with the consumer's own timestamp parser."""
    return UCR._timestamp(value, "TEST_TIMESTAMP_INVALID")


class USCapitalRotationTaxonomySourcePointInTimeTests(unittest.TestCase):
    """The taxonomy source must be a point-in-time fact on *both* observations.

    The producer resolves a graph on one decision date, and that date is the
    rotation's current observation. A document that is perfectly effective
    today can still be a strictly later fact than the prior observation whose
    rank, bucket and transition it would otherwise classify. These regressions
    hold that line for the three ways it can happen -- the approval window, the
    ratification instant, and an individual Theme node's own validity -- in
    build_packet() and in standalone validate_packet() alike.
    """

    def setUp(self):
        self.document = taxonomy_graph_document()
        self.source_bytes = taxonomy_source_bytes(self.document)

    def build(self, document, rotation_policy=None):
        value, bundled_policy = v2_bundle(document)
        return UCR.build_packet(
            value, bundled_policy if rotation_policy is None else rotation_policy,
            taxonomy_source_bytes=taxonomy_source_bytes(document),
        )

    def active_theme_ids(self, document, day):
        reference = TT.build_packet(copy.deepcopy(document))
        return {
            node["theme_id"]
            for node in reference["nodes"]
            if TT._active(node["valid_from"], node["valid_to"], day)
        }

    def test_every_prior_invalid_fixture_is_currently_effective_for_the_producer(self):
        """Guards the negatives below from going vacuous.

        Each fixture must reach the producer's *currently effective* verdict,
        so it is refused by the new prior-observation rule specifically and not
        by the pre-existing DRAFT_OR_NOT_EFFECTIVE_GRAPH gate, which would make
        these tests prove nothing new.
        """
        factories = dict(PRIOR_INVALID_SOURCE_DOCUMENTS)
        factories["ratified_after_prior"] = late_ratified_graph_document
        for name, factory in factories.items():
            with self.subTest(state=name):
                reference = TT.build_packet(factory())
                self.assertEqual(
                    reference["graph_status"],
                    "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED",
                )
                self.assertNotEqual(
                    reference["graph_status"], "DRAFT_OR_NOT_EFFECTIVE_GRAPH"
                )
                self.assertTrue(
                    reference["structurally_eligible_ratification_claim"]
                )
                self.assertEqual(reference["approval"]["approval_status"], "RATIFIED")
                self.assertFalse(reference["theme_membership_authorized"])

    def test_source_in_force_only_after_the_prior_observation_withholds_ranking(self):
        """The exact independently reproduced defect.

        Prior observation 2026-08-18, taxonomy effective 2026-08-19, current
        observation 2026-08-20, RATIFIED covering rotation policy. This used to
        emit ROTATION_BUCKETS_OBSERVED with a prior rank and a TOP_TO_MIDDLE
        transition -- a later taxonomy fact classifying an earlier observation.
        """
        document = prior_invalid_effective_from_graph_document()
        value, rotation_policy = v2_bundle(document)
        self.assertEqual(rotation_policy["approval_status"], "RATIFIED")
        packet = UCR.build_packet(
            value, rotation_policy,
            taxonomy_source_bytes=taxonomy_source_bytes(document),
        )
        self.assertEqual(packet["observation_pair"]["prior_date"], PRIOR_OBSERVATION_DATE)
        self.assertGreater(
            document["approval"]["effective_from"], PRIOR_OBSERVATION_DATE
        )
        # The producer still calls the source currently effective; the packet
        # records that verdict exactly and never rewrites it.
        self.assertEqual(
            packet["taxonomy_binding"]["taxonomy_graph_status"],
            "STRUCTURALLY_VALID_RATIFICATION_CLAIM_NOT_AUTHORIZED",
        )
        self.assertEqual(packet["status"], "TAXONOMY_SOURCE_NOT_EFFECTIVE")
        # The rotation policy's own ratification is still reported truthfully,
        # so the two gates stay separately auditable.
        self.assertTrue(packet["rotation_policy_effective"])
        self.assertIsNone(packet["ranking_method"])
        self.assertEqual(packet["top_themes"], [])
        self.assertEqual(packet["bottom_themes"], [])
        for row in packet["theme_observations"]:
            for field in (
                "prior_rank", "current_rank", "rank_change",
                "prior_bucket", "current_bucket", "bucket_transition",
            ):
                self.assertIsNone(row[field], field)
            # The unranked measurement itself is still emitted.
            self.assertIsNotNone(row["prior_relative_strength_vs_benchmark"])
            self.assertIsNotNone(row["relative_strength_change"])
        for field in (
            "theme_ranking_authorized", "top_bottom_bucket_authorized",
            "bucket_transition_authorized",
        ):
            self.assertFalse(packet["authority"][field], field)
        self.assertFalse(packet["taxonomy_binding"]["theme_membership_authorized"])
        UCR.validate_packet(copy.deepcopy(packet))

    def test_theme_node_must_be_active_on_the_prior_observation_too(self):
        document = prior_invalid_node_graph_document()
        target = "THEME.NETWORK"
        # The node is genuinely active on the decision date, so the unchanged
        # decision-date check cannot be what refuses this document.
        self.assertIn(target, self.active_theme_ids(document, "2026-08-20"))
        self.assertNotIn(
            target, self.active_theme_ids(document, PRIOR_OBSERVATION_DATE)
        )
        value, rotation_policy = v2_bundle(document)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            f"TAXONOMY_THEME_NODE_NOT_ACTIVE_AT_PRIOR:{target}",
        ):
            UCR.build_packet(
                value, rotation_policy,
                taxonomy_source_bytes=taxonomy_source_bytes(document),
            )
        # The decision-date verdict keeps its own distinct, unchanged code.
        lapsed = taxonomy_graph_document()
        for node in lapsed["nodes"]:
            if node["theme_id"] == target:
                node["valid_to"] = "2026-08-20"
        for edge in lapsed["edges"]:
            if edge["to_theme_id"] == target:
                edge["valid_to"] = "2026-08-20"
        value, rotation_policy = v2_bundle(lapsed)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError,
            f"TAXONOMY_THEME_NODE_NOT_ACTIVE:{target}",
        ):
            UCR.build_packet(
                value, rotation_policy,
                taxonomy_source_bytes=taxonomy_source_bytes(lapsed),
            )

    def test_source_ratified_after_prior_availability_is_refused_same_calendar_day(self):
        document = late_ratified_graph_document()
        ratified = document["approval"]["ratified_at_utc"]
        # Same calendar day as the prior observation's availability, strictly
        # later as an instant: a date-granularity comparison would let it pass.
        self.assertEqual(ratified[:10], PRIOR_AVAILABLE_AT_UTC[:10])
        self.assertGreater(utc(ratified), utc(PRIOR_AVAILABLE_AT_UTC))
        # The approval window itself does cover the prior observation, so this
        # is the ratification boundary firing and nothing else.
        self.assertLessEqual(
            document["approval"]["effective_from"], PRIOR_OBSERVATION_DATE
        )
        value, rotation_policy = v2_bundle(document)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_RATIFIED_AFTER_PRIOR_OBSERVATION"
        ):
            UCR.build_packet(
                value, rotation_policy,
                taxonomy_source_bytes=taxonomy_source_bytes(document),
            )
        # The accepted fixture is on the correct side of the same boundary.
        self.assertLessEqual(
            utc(self.document["approval"]["ratified_at_utc"]),
            utc(PRIOR_AVAILABLE_AT_UTC),
        )

    def test_self_rehashed_ranking_over_a_prior_invalid_source_is_refused(self):
        """The standalone validator, not just build_packet(), must refuse.

        Every digest is re-signed and the embedded source is a real graph the
        producer accepts and calls currently effective, so nothing here is
        caught by a hash check or by the pre-existing not-effective verdict.
        These fail only because validate_packet() re-derives the source's own
        approval window against the packet's own prior observation date.
        """
        ranked = self.build(self.document)
        self.assertEqual(ranked["status"], "ROTATION_BUCKETS_OBSERVED")
        document = prior_invalid_effective_from_graph_document()
        source = taxonomy_source_bytes(document)
        honest = self.build(document)
        self.assertEqual(honest["status"], "TAXONOMY_SOURCE_NOT_EFFECTIVE")
        with self.subTest(forgery="full_ranking"):
            forged = copy.deepcopy(honest)
            forged["status"] = ranked["status"]
            forged["ranking_method"] = copy.deepcopy(ranked["ranking_method"])
            forged["top_themes"] = list(ranked["top_themes"])
            forged["bottom_themes"] = list(ranked["bottom_themes"])
            forged["theme_observations"] = copy.deepcopy(ranked["theme_observations"])
            forged["authority"] = copy.deepcopy(ranked["authority"])
            rehash_output(forged)
            self.assertEqual(forged["payload_sha256"], UCR.payload_sha256({
                key: forged[key] for key in forged if key != "payload_sha256"
            }))
            # Packet-only consumers, including the shared rotation state
            # ledger, re-prove the embedded source with no argument at all.
            for source_argument in (None, source):
                with self.assertRaisesRegex(
                    UCR.USCapitalRotationError, "OUTPUT_UNAUTHORIZED_RANKING"
                ):
                    UCR.validate_packet(
                        copy.deepcopy(forged), taxonomy_source_bytes=source_argument
                    )
        with self.subTest(forgery="status_only"):
            forged = copy.deepcopy(honest)
            forged["status"] = "ROTATION_BUCKETS_OBSERVED"
            rehash_output(forged)
            with self.assertRaisesRegex(
                UCR.USCapitalRotationError,
                "OUTPUT_INEFFECTIVE_TAXONOMY_BOUNDARY_MISMATCH",
            ):
                UCR.validate_packet(forged)

    def test_standalone_validator_reproves_the_source_ratification_boundary(self):
        """Re-derived from the embedded source, not from the rotation policy.

        The persisted prior_available_at is pushed back to an instant that is
        still after the rotation policy's own ratification -- so the existing
        policy re-proof passes -- but before the taxonomy source's. Only a
        validator that independently re-reads the source's ratification instant
        refuses this.
        """
        packet = self.build(self.document)
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        tampered_available_at = "2026-08-18T00:00:00+00:00"
        self.assertGreater(
            utc(tampered_available_at),
            utc(packet["rotation_policy"]["ratified_at_utc"]),
        )
        self.assertLess(
            utc(tampered_available_at),
            utc(self.document["approval"]["ratified_at_utc"]),
        )
        packet["observation_pair"]["prior_available_at"] = tampered_available_at
        rehash_output(packet)
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_RATIFIED_AFTER_PRIOR_OBSERVATION"
        ):
            UCR.validate_packet(packet)

    def test_a_graph_verdict_without_the_facts_it_came_from_fails_closed(self):
        # Not reachable through the public API -- _validate_binding admits only
        # the six legacy fields or the full derived set -- so this locks the
        # default directly: a producer verdict with no consumed source behind
        # it never reads as an effective taxonomy fact.
        with self.assertRaisesRegex(
            UCR.USCapitalRotationError, "TAXONOMY_SOURCE_FACTS_MISSING"
        ):
            UCR._taxonomy_source_effective(
                {"taxonomy_graph_status": "AUTHORIZED_EFFECTIVE_GRAPH"},
                None,
                dt.date(2026, 8, 18),
                dt.date(2026, 8, 20),
                utc(PRIOR_AVAILABLE_AT_UTC),
            )
        # The legacy /1 binding, which carries no verdict, is untouched.
        self.assertTrue(
            UCR._taxonomy_source_effective(
                taxonomy_binding(), None,
                dt.date(2026, 8, 18), dt.date(2026, 8, 20),
                utc(PRIOR_AVAILABLE_AT_UTC),
            )
        )

    def test_source_valid_on_both_observations_still_ranks_and_legacy_is_unchanged(self):
        approval = self.document["approval"]
        # The positive fixture genuinely satisfies the rule on both dates.
        self.assertLessEqual(approval["effective_from"], PRIOR_OBSERVATION_DATE)
        self.assertIsNone(approval["effective_to"])
        self.assertLessEqual(
            utc(approval["ratified_at_utc"]), utc(PRIOR_AVAILABLE_AT_UTC)
        )
        for day in (PRIOR_OBSERVATION_DATE, "2026-08-20"):
            self.assertTrue(
                set(US_THEME_IDS) <= self.active_theme_ids(self.document, day), day
            )
        packet = self.build(self.document)
        self.assertEqual(packet["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(packet["authority"]["theme_ranking_authorized"])
        self.assertEqual(packet["top_themes"], ["THEME.NETWORK"])
        self.assertEqual(packet["bottom_themes"], ["THEME.POWER"])
        rows = {row["theme_id"]: row for row in packet["theme_observations"]}
        self.assertEqual(rows["THEME.COMPUTE"]["prior_rank"], 1)
        self.assertEqual(rows["THEME.COMPUTE"]["bucket_transition"], "TOP_TO_MIDDLE")
        self.assertEqual(rows["THEME.NETWORK"]["bucket_transition"], "MIDDLE_TO_TOP")
        UCR.validate_packet(copy.deepcopy(packet))
        # And the legacy /1 packet is unchanged: it consumes no source, so the
        # prior-observation rule has nothing to evaluate and stays inert.
        legacy = UCR.build_packet(input_packet(), policy())
        self.assertNotIn("taxonomy_graph_status", legacy["taxonomy_binding"])
        self.assertEqual(legacy["status"], "ROTATION_BUCKETS_OBSERVED")
        self.assertTrue(legacy["rotation_policy_effective"])
        self.assertEqual(packet["theme_observations"], legacy["theme_observations"])
        self.assertEqual(packet["top_themes"], legacy["top_themes"])
        self.assertEqual(packet["bottom_themes"], legacy["bottom_themes"])
        self.assertEqual(packet["authority"], legacy["authority"])
        UCR.validate_packet(copy.deepcopy(legacy))


if __name__ == "__main__":
    unittest.main()
