"""P2-03 policy-gated Korea Theme capital-rotation regression."""
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
MODULE_PATH = ROOT / "rotation" / "korea_capital_rotation.py"
UPSTREAM_PATH = ROOT / ".github" / "scripts" / "korea_leadership.py"

SPEC = importlib.util.spec_from_file_location("korea_capital_rotation", MODULE_PATH)
KCR = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(KCR)

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


def make_bundle() -> tuple[dict, dict]:
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
        "taxonomy_contract_version": "theme_taxonomy/1",
        "taxonomy_id": "TAXONOMY.GLOBAL.2026",
        "taxonomy_decision_id": "DECISION.P2.01",
        "taxonomy_decision_sha256": TAXONOMY_DECISION_SHA,
        "taxonomy_packet_sha256": TAXONOMY_PACKET_SHA,
        "upstream_leadership_policy_sha256": policy_sha,
    }
    context = {
        "breadth": {
            "status": "OBSERVATION_ONLY_NO_DURABLE_AVAILABLE_AT_LINEAGE",
            "available_at": None,
            "lineage_sha256": None,
            "ranking_input_authorized": False,
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
        "taxonomy_decision_sha256": TAXONOMY_DECISION_SHA,
        "taxonomy_packet_sha256": TAXONOMY_PACKET_SHA,
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


if __name__ == "__main__":
    unittest.main()
