#!/usr/bin/env python3
"""P1-COM-05 Regime decision-authority and draft-policy regressions."""

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "regime" / "decision_authority.py"
SPEC = importlib.util.spec_from_file_location("regime_decision_authority", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
CONTRACT = MODULE.load_contract()
OUTPUT = MODULE.OUTPUT
COVERAGE = MODULE.COVERAGE

POLICY_SCRIPT = ROOT / "regime" / "policy_candidate.py"
POLICY_SPEC = importlib.util.spec_from_file_location(
    "regime_policy_candidate", POLICY_SCRIPT
)
POLICY = importlib.util.module_from_spec(POLICY_SPEC)
POLICY_SPEC.loader.exec_module(POLICY)
POLICY_CONTRACT = POLICY.load_contract()
SOURCE_OWNER_V2 = ROOT / "config" / "regime_source_owner_registry_v2.json"


def defined(axis, character):
    return {
        "status": "DEFINED",
        "observation_date": "2026-08-26",
        "available_at": "2026-08-27T00:20:00Z",
        "transform_version": f"regime_{axis.lower()}/v1",
        "evidence": {
            "uri": f"evidence/{axis.lower()}/2026-08-26.json",
            "sha256": character * 64,
        },
        "warnings": [],
    }


def source(factors=None):
    return OUTPUT.build_unknown_output(
        "CRYPTO",
        "2026-08-27T01:00:00Z",
        factors,
    )


def full_source():
    return source(
        {
            axis: defined(axis, character)
            for axis, character in zip(CONTRACT["required_axes"], "abcde")
        }
    )


def gate(value):
    return COVERAGE.evaluate_minimum_coverage(value)


def policy_manifest(decision_at="2026-08-27T01:00:00Z"):
    values = {
        component: f"{component}_DRAFT_VALUE"
        for component in POLICY_CONTRACT["required_components"]
    }
    values["MINIMUM_COVERAGE"] = 5
    return {
        "schema_version": 1,
        "contract_version": POLICY_CONTRACT["candidate_manifest_version"],
        "candidate_id": "EVIDENCE_BOUND_DRAFT",
        "market": "CRYPTO",
        "decision_at": decision_at,
        "policy_status": "DRAFT_NOT_RATIFIED",
        "parameters": [
            {
                "component": component,
                "parameter_id": component,
                "value_type": "NUMBER" if component == "MINIMUM_COVERAGE" else "TEXT",
                "proposed_value": values[component],
                "evidence_refs": [],
            }
            for component in POLICY_CONTRACT["required_components"]
        ],
    }


def evidence_document(
    manifest,
    *,
    evidence_id="CIO_EXPLICIT_POLICY_VALUES",
    evidence_kind="CIO_DOCTRINE",
    claim_type="EXPLICIT_PARAMETER_VALUE",
    available_at="2026-08-26T00:00:00Z",
    valid_through=None,
    observation_count=None,
    distinct_observation_dates=None,
):
    return {
        "schema_version": 1,
        "contract_version": POLICY_CONTRACT["evidence_document_version"],
        "evidence_id": evidence_id,
        "evidence_kind": evidence_kind,
        "published_at": "2026-08-25T00:00:00Z",
        "available_at": available_at,
        "valid_through": valid_through,
        "source_locator": "notion://atlas/cio-doctrine",
        "parameter_claims": [
            {
                "parameter_id": parameter["parameter_id"],
                "claim_type": claim_type,
                "supported_value": parameter["proposed_value"],
                "observation_count": observation_count,
                "distinct_observation_dates": distinct_observation_dates,
                "derivation": "DIRECT_STATEMENT",
            }
            for parameter in manifest["parameters"]
        ],
        "caveats": [],
    }


def write_evidence(root, document, name="policy-evidence.json"):
    path = root / name
    raw = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    path.write_bytes(raw)
    return {
        "path": name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "evidence_id": document["evidence_id"],
    }


def bind_all_parameters(manifest, reference):
    for parameter in manifest["parameters"]:
        parameter["evidence_refs"] = [copy.deepcopy(reference)]
    return manifest


def parameter_result(inventory, component):
    return next(
        parameter
        for parameter in inventory["parameters"]
        if parameter["component"] == component
    )


COMMON_V1_AXES = CONTRACT["required_axes"]
SIDEWAYS_DIRECTIONS = {
    "TREND": "POSITIVE",
    "BREADTH": "NEGATIVE",
    "RISK_VOL": "NEUTRAL",
    "LIQUIDITY": "POSITIVE",
    "LEADERSHIP": "NEGATIVE",
}


def common_v1_axes(direction, stress=False, undefined=()):
    rows = {}
    for axis in COMMON_V1_AXES:
        if axis in undefined:
            rows[axis] = {"status": "UNDEFINED", "direction": None}
        else:
            rows[axis] = {"status": "DEFINED", "direction": direction}
    if stress:
        rows["RISK_VOL"] = {"status": "DEFINED", "direction": "STRESS"}
    return rows


def sideways_axes():
    return {
        axis: {"status": "DEFINED", "direction": SIDEWAYS_DIRECTIONS[axis]}
        for axis in COMMON_V1_AXES
    }


def common_v1_sequence(case_id, patterns, market="US"):
    return {
        "schema_version": 1,
        "market": market,
        "case_id": case_id,
        "steps": [
            {
                "packet_id": f"{case_id}-{index}",
                "as_of_date": f"2026-08-{24 + index:02d}",
                "axes": axes,
            }
            for index, axes in enumerate(patterns)
        ],
    }


def write_json(path, value, indent=2):
    path.write_text(json.dumps(value, indent=indent) + "\n", encoding="utf-8")
    return path


class RegimeDecisionAuthorityTest(unittest.TestCase):
    def test_contract_pins_unratified_policy_boundary(self):
        self.assertEqual(
            CONTRACT["contract_version"], "regime_decision_authority/v1"
        )
        self.assertEqual(CONTRACT["repository_policy_registry_status"], "ABSENT")
        self.assertEqual(len(CONTRACT["required_policy_components"]), 9)
        self.assertEqual(
            CONTRACT["allowed_decision_statuses"],
            ["BLOCKED_COVERAGE", "BLOCKED_POLICY_UNRATIFIED"],
        )
        self.assertTrue(
            CONTRACT["authority"]["decision_boundary_validation_authorized"]
        )
        for key, value in CONTRACT["authority"].items():
            if key != "decision_boundary_validation_authorized":
                self.assertFalse(value, key)

    def test_missing_axis_is_distinct_from_missing_policy(self):
        partial = source({"TREND": defined("TREND", "a")})
        blocked_coverage = MODULE.evaluate_decision_authority(partial, gate(partial))
        complete = full_source()
        blocked_policy = MODULE.evaluate_decision_authority(complete, gate(complete))

        self.assertEqual(blocked_coverage["decision_status"], "BLOCKED_COVERAGE")
        self.assertEqual(blocked_coverage["coverage"]["ratio"], "1/5")
        self.assertIn("MINIMUM_COVERAGE_NOT_MET", blocked_coverage["reasons"])
        self.assertEqual(
            blocked_policy["decision_status"], "BLOCKED_POLICY_UNRATIFIED"
        )
        self.assertEqual(blocked_policy["coverage"]["ratio"], "5/5")
        self.assertEqual(
            blocked_policy["reasons"],
            [
                CONTRACT["policy_reason_codes"][component]
                for component in CONTRACT["required_policy_components"]
            ],
        )

    def test_five_of_five_never_opens_classification(self):
        complete = full_source()
        decision = MODULE.evaluate_decision_authority(complete, gate(complete))

        self.assertFalse(decision["policy_gate"]["classification_eligible"])
        self.assertFalse(decision["policy_gate"]["replay_eligible"])
        self.assertEqual(decision["regime"], "UNKNOWN")
        self.assertEqual(decision["direction"], "UNKNOWN")
        self.assertIsNone(decision["confidence"])
        self.assertNotIn("NEUTRAL", json.dumps(decision))
        self.assertFalse(decision["authority"]["classification_authorized"])
        self.assertFalse(decision["authority"]["production_authorized"])
        self.assertFalse(decision["authority"]["trading_authorized"])

    def test_sources_are_hash_bound_and_reordered_keys_are_deterministic(self):
        original = full_source()
        reordered = copy.deepcopy(original)
        reordered["factor_results"] = dict(
            reversed(list(reordered["factor_results"].items()))
        )
        first_gate = gate(original)
        second_gate = gate(reordered)
        first = MODULE.evaluate_decision_authority(original, first_gate)
        second = MODULE.evaluate_decision_authority(reordered, second_gate)

        self.assertEqual(first, second)
        self.assertEqual(
            first["source_refs"]["regime_output_sha256"],
            MODULE.payload_sha256(original),
        )
        self.assertEqual(
            first["source_refs"]["minimum_coverage_sha256"],
            MODULE.payload_sha256(first_gate),
        )

    def test_source_gate_and_decision_tampering_fail_closed(self):
        original = full_source()
        original_gate = gate(original)
        decision = MODULE.evaluate_decision_authority(original, original_gate)

        bad_gate = copy.deepcopy(original_gate)
        bad_gate["classification_eligible"] = True
        with self.assertRaisesRegex(MODULE.DecisionAuthorityError, "COVERAGE_GATE_INVALID"):
            MODULE.evaluate_decision_authority(original, bad_gate)

        bad_source = copy.deepcopy(original)
        bad_source["regime"] = "NEUTRAL"
        with self.assertRaisesRegex(MODULE.DecisionAuthorityError, "REGIME_OUTPUT_INVALID"):
            MODULE.evaluate_decision_authority(bad_source, original_gate)

        mutations = []
        for key, value in [
            ("decision_status", "PASS"),
            ("regime", "RISK_ON"),
            ("direction", "IMPROVING"),
            ("confidence", 1),
        ]:
            changed = copy.deepcopy(decision)
            changed[key] = value
            mutations.append(changed)
        authority = copy.deepcopy(decision)
        authority["authority"]["classification_authorized"] = True
        mutations.append(authority)
        policy = copy.deepcopy(decision)
        policy["policy_gate"]["component_status"]["AGGREGATION_WEIGHTS"] = (
            "RATIFIED"
        )
        mutations.append(policy)

        for changed in mutations:
            with self.subTest(changed=changed), self.assertRaisesRegex(
                MODULE.DecisionAuthorityError,
                "DECISION_DERIVATION_MISMATCH",
            ):
                MODULE.validate_decision(changed, original, original_gate)

    def test_contract_edit_cannot_self_ratify_policy_or_open_authority(self):
        registry = copy.deepcopy(CONTRACT)
        registry["repository_policy_registry_status"] = "PRESENT"
        status = copy.deepcopy(CONTRACT)
        status["policy_component_status"]["FRESHNESS"] = "RATIFIED"
        authority = copy.deepcopy(CONTRACT)
        authority["authority"]["classification_authorized"] = True
        result = copy.deepcopy(CONTRACT)
        result["allowed_decision_statuses"].append("CLASSIFIED")

        for changed in (registry, status, authority, result):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                MODULE.DecisionAuthorityError,
                "CONTRACT_INVALID",
            ):
                MODULE.validate_contract(changed)

    def test_cli_evaluate_and_validate_use_explicit_outputs(self):
        original = full_source()
        original_gate = gate(original)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_path = root / "regime.json"
            gate_path = root / "coverage.json"
            decision_path = root / "decision.json"
            source_path.write_text(json.dumps(original), encoding="utf-8")
            gate_path.write_text(json.dumps(original_gate), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                evaluate_exit = MODULE.main(
                    [
                        "evaluate",
                        str(source_path),
                        str(gate_path),
                        "--out",
                        str(decision_path),
                    ]
                )
                validate_exit = MODULE.main(
                    [
                        "validate",
                        str(decision_path),
                        "--regime-output",
                        str(source_path),
                        "--coverage-gate",
                        str(gate_path),
                    ]
                )
            self.assertEqual(evaluate_exit, 0)
            self.assertEqual(validate_exit, 0)
            self.assertEqual(
                json.loads(decision_path.read_text())["decision_status"],
                "BLOCKED_POLICY_UNRATIFIED",
            )


class RegimePolicyCandidateTest(unittest.TestCase):
    def test_candidate_contract_is_evidence_only_and_fail_closed(self):
        self.assertEqual(
            POLICY_CONTRACT["contract_version"],
            "regime_policy_candidate_evidence/v1",
        )
        self.assertEqual(POLICY_CONTRACT["policy_status"], "DRAFT_NOT_RATIFIED")
        self.assertEqual(POLICY_CONTRACT["contract_mode"], "SHADOW_DIAGNOSTIC_ONLY")
        self.assertTrue(
            POLICY_CONTRACT["authority"]["candidate_evidence_inventory_authorized"]
        )
        for key, value in POLICY_CONTRACT["authority"].items():
            if key != "candidate_evidence_inventory_authorized":
                self.assertFalse(value, key)

    def test_unspecified_baseline_is_blocked_without_defaults(self):
        manifest = POLICY.build_baseline_manifest(
            "CRYPTO", "2026-08-27T01:00:00Z"
        )
        inventory = POLICY.build_candidate_inventory(manifest)

        self.assertEqual(inventory["candidate_status"], "CANDIDATE_BLOCKED")
        self.assertEqual(
            inventory["blocked_components"],
            POLICY_CONTRACT["required_components"],
        )
        for parameter in inventory["parameters"]:
            self.assertEqual(parameter["proposed_value"], None)
            self.assertEqual(parameter["status"], "BLOCKED")
            self.assertEqual(
                parameter["blocking_reasons"],
                ["EVIDENCE_MISSING", "VALUE_UNSPECIFIED"],
            )
        self.assertEqual(inventory["replay"]["population_status"], "NOT_COMPUTABLE")
        self.assertFalse(inventory["replay"]["winner_selected"])

    def test_exact_available_evidence_can_only_make_candidate_replay_ready(self):
        manifest = policy_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            document = evidence_document(manifest)
            reference = write_evidence(root, document)
            bind_all_parameters(manifest, reference)

            inventory = POLICY.build_candidate_inventory(manifest, root)
            reordered = copy.deepcopy(manifest)
            reordered["parameters"] = list(reversed(reordered["parameters"]))

            self.assertEqual(inventory["candidate_status"], "CANDIDATE_READY")
            self.assertEqual(inventory["blocked_components"], [])
            self.assertTrue(inventory["replay"]["candidate_input_eligible"])
            self.assertFalse(inventory["ratification"]["selected"])
            self.assertFalse(inventory["ratification"]["recommended"])
            self.assertFalse(inventory["ratification"]["ratified"])
            self.assertFalse(inventory["ratification"]["runtime_eligible"])
            self.assertEqual(
                inventory,
                POLICY.build_candidate_inventory(reordered, root),
            )

    def test_qualitative_doctrine_cannot_justify_numeric_policy(self):
        manifest = policy_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            document = evidence_document(
                manifest,
                evidence_id="QUALITATIVE_DOCTRINE_ONLY",
                claim_type="QUALITATIVE_PRINCIPLE",
            )
            reference = write_evidence(root, document)
            bind_all_parameters(manifest, reference)

            inventory = POLICY.build_candidate_inventory(manifest, root)
            minimum = parameter_result(inventory, "MINIMUM_COVERAGE")

            self.assertEqual(minimum["status"], "BLOCKED")
            self.assertEqual(minimum["blocking_reasons"], ["QUALITATIVE_ONLY"])
            self.assertEqual(inventory["candidate_status"], "CANDIDATE_BLOCKED")

    def test_single_observation_statistic_cannot_become_policy_value(self):
        manifest = policy_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            document = evidence_document(
                manifest,
                evidence_id="ONE_POINT_EMPIRICAL_STATISTIC",
                evidence_kind="EMPIRICAL_DISTRIBUTION",
                claim_type="EMPIRICAL_STATISTIC",
                observation_count=1,
                distinct_observation_dates=1,
            )
            reference = write_evidence(root, document)
            bind_all_parameters(manifest, reference)

            inventory = POLICY.build_candidate_inventory(manifest, root)
            minimum = parameter_result(inventory, "MINIMUM_COVERAGE")

            self.assertEqual(minimum["status"], "BLOCKED")
            self.assertEqual(
                minimum["blocking_reasons"],
                ["SINGLE_OBSERVATION_STATISTIC"],
            )

    def test_missing_future_and_stale_evidence_are_distinct(self):
        cases = [
            (
                "missing",
                None,
                "EVIDENCE_MISSING",
            ),
            (
                "future",
                {
                    "available_at": "2026-08-28T00:00:00Z",
                    "valid_through": None,
                },
                "FUTURE_EVIDENCE",
            ),
            (
                "stale",
                {
                    "available_at": "2026-08-26T00:00:00Z",
                    "valid_through": "2026-08-26T12:00:00Z",
                },
                "STALE_EVIDENCE",
            ),
        ]
        for name, timing, expected in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                manifest = policy_manifest()
                if timing is None:
                    reference = {
                        "path": "missing.json",
                        "sha256": "f" * 64,
                        "evidence_id": "MISSING_POLICY_EVIDENCE",
                    }
                else:
                    document = evidence_document(manifest, **timing)
                    reference = write_evidence(root, document)
                bind_all_parameters(manifest, reference)

                inventory = POLICY.build_candidate_inventory(manifest, root)
                minimum = parameter_result(inventory, "MINIMUM_COVERAGE")
                self.assertIn(expected, minimum["blocking_reasons"])

    def test_evidence_and_inventory_tampering_fail_closed(self):
        manifest = policy_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            document = evidence_document(manifest)
            reference = write_evidence(root, document)
            bind_all_parameters(manifest, reference)
            inventory = POLICY.build_candidate_inventory(manifest, root)

            changed_evidence = copy.deepcopy(document)
            changed_evidence["parameter_claims"][0]["supported_value"] = "TAMPERED"
            (root / reference["path"]).write_text(
                json.dumps(changed_evidence), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                POLICY.PolicyCandidateError,
                "EVIDENCE_SHA_MISMATCH",
            ):
                POLICY.build_candidate_inventory(manifest, root)

            write_evidence(root, document)
            authority = copy.deepcopy(inventory)
            authority["authority"]["runtime_classification_authorized"] = True
            with self.assertRaisesRegex(
                POLICY.PolicyCandidateError,
                "CANDIDATE_INVENTORY_DERIVATION_MISMATCH",
            ):
                POLICY.validate_candidate_inventory(
                    authority,
                    manifest,
                    root,
                )

    def test_candidate_contract_cannot_be_edited_into_authority(self):
        runtime = copy.deepcopy(POLICY_CONTRACT)
        runtime["authority"]["runtime_classification_authorized"] = True
        ready_means_ratified = copy.deepcopy(POLICY_CONTRACT)
        ready_means_ratified["evidence_policy"][
            "candidate_ready_does_not_select_recommend_or_ratify"
        ] = False

        for changed in (runtime, ready_means_ratified):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                POLICY.PolicyCandidateError,
                "CONTRACT_INVALID",
            ):
                POLICY.validate_contract(changed)

    def test_candidate_cli_evaluate_and_validate(self):
        manifest = policy_manifest()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            document = evidence_document(manifest)
            reference = write_evidence(root, document)
            bind_all_parameters(manifest, reference)
            manifest_path = root / "manifest.json"
            inventory_path = root / "inventory.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                inventory_exit = POLICY.main(
                    [
                        "inventory",
                        str(manifest_path),
                        "--evidence-root",
                        str(root),
                        "--out",
                        str(inventory_path),
                    ]
                )
                validate_exit = POLICY.main(
                    [
                        "validate",
                        str(inventory_path),
                        "--candidate-manifest",
                        str(manifest_path),
                        "--evidence-root",
                        str(root),
                    ]
                )
            self.assertEqual(inventory_exit, 0)
            self.assertEqual(validate_exit, 0)
            persisted = json.loads(inventory_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["candidate_status"], "CANDIDATE_READY")
            self.assertEqual(persisted["policy_status"], "DRAFT_NOT_RATIFIED")


class RegimeSourceOwnerRegistryV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = json.loads(SOURCE_OWNER_V2.read_text(encoding="utf-8"))

    def assert_pins(self, value):
        for key, expected in value.items():
            if key.endswith("_sha256"):
                path_key = key.removesuffix("_sha256") + "_path"
                if path_key in value:
                    actual = hashlib.sha256((ROOT / value[path_key]).read_bytes()).hexdigest()
                    self.assertEqual(actual, expected, path_key)

    def test_v2_exact_decision_and_common_v1_separation(self):
        value = self.registry
        self.assertEqual(value["schema_version"], 2)
        self.assertEqual(value["registry_version"], "regime_source_owner_registry/v2")
        self.assertEqual(value["registry_mode"], "SOURCE_OWNER_ARCHITECTURE_ONLY")
        self.assertEqual(
            value["decision"],
            {
                "identity": "CIO-GATE2-3MARKET-REGIME-SOURCE-FIRST-B-2026-09-01",
                "status": "CIO_APPROVED_ARCHITECTURE_SCOPE_ONLY",
                "option": "OPTION_B_SOURCE_FIRST_LAYERED_RATIFICATION",
                "effective_date": "2026-09-01",
                "packet_sha256": "bdeb9b9970c71d38a9650f2374b9078e1f76ef4eeddf5acb34c6a890e9b7591c",
            },
        )
        common = value["common_v1_alignment"]
        self.assertEqual(common["policy_status"], "RATIFIED_PAPER_BASELINE_V1")
        self.assertEqual(
            common["repository_v2_alignment_status"],
            "ALIGNED_ARCHITECTURE_ONLY_RUNTIME_NOT_WIRED",
        )
        self.assertEqual(
            common["required_axes"],
            ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"],
        )
        self.assertEqual(set(common["weights"].values()), {1})
        self.assertFalse(
            common["market_specific_normalization_freshness_and_replay_inherited"]
        )
        self.assertEqual(
            common["legacy_runtime_contract"]["status"], "UNCHANGED_FAIL_CLOSED"
        )
        self.assert_pins(common["legacy_runtime_contract"])

    def test_v2_market_and_aggregate_acceptance_remain_blocked(self):
        markets = self.registry["markets"]
        expected = {
            "KRX": "BLOCKED_SIGNED_NORMALIZATION_TTL_PIT_REPLAY",
            "US": "BLOCKED_FINISHED_SESSION_TTL_PIT_REPLAY",
            "CRYPTO": "BLOCKED_OVERALL_FRESHNESS_PIT_REPLAY",
        }
        for name, status in expected.items():
            self.assertEqual(markets[name]["acceptance_status"], status)
            self.assertEqual(
                markets[name]["architecture_status"],
                "CIO_APPROVED_ARCHITECTURE_SCOPE_ONLY",
            )
            self.assertEqual(markets[name]["pit_replay_acceptance"], "NOT_ACCEPTED")
        aggregate = self.registry["aggregate"]
        self.assertEqual(
            aggregate["acceptance_status"], "BLOCKED_MIXED_EVIDENCE_CLASSES"
        )
        self.assertEqual(aggregate["runtime_output_status"], "UNKNOWN/HOLD/WAIT")
        self.assertFalse(aggregate["pin_update_allowed"])
        self.assert_pins(aggregate)

    def test_v2_krx_reuses_exact_official_source_and_owner(self):
        krx = self.registry["markets"]["KRX"]
        self.assertEqual(krx["source_scope"], "KRX_OFFICIAL_FIVE_AXIS")
        self.assertEqual(
            krx["source_owner"]["source_name"],
            "KRX_OPEN_API_STOCK_AND_INDEX_DAILY",
        )
        self.assertEqual(
            krx["natural_receipt_owner"]["owner_status"],
            "BOUND_EXISTING_NATURAL_READ_ONLY",
        )
        self.assertIsNone(krx["signed_normalization_policy"])
        self.assertIsNone(krx["ttl_seconds"])
        self.assert_pins(krx["source_owner"])
        self.assert_pins(krx["natural_receipt_owner"])

    def test_v2_us_exact_calendar_proxy_and_bound_finished_session_owner(self):
        us = self.registry["markets"]["US"]
        self.assertEqual(
            [row["source_id"] for row in us["official_calendar"]["sources"]],
            ["NYSE_HOLIDAYS_AND_TRADING_HOURS", "NASDAQ_TRADING_SCHEDULE"],
        )
        proxy = us["paper_proxy"]
        self.assertEqual(
            proxy["exact_15_etfs"],
            [
                "IWM", "QQQ", "SMH", "SPY", "XLB", "XLC", "XLE", "XLF",
                "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
            ],
        )
        self.assertEqual(
            proxy["leadership_12_group_proxies"],
            [
                "SMH", "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP",
                "XLRE", "XLU", "XLV", "XLY",
            ],
        )
        self.assertEqual(proxy["leadership_benchmark"], "SPY")
        self.assertEqual(proxy["leadership_window_finalized_sessions"], 20)
        finished = us["finished_session_owner"]
        self.assertEqual(
            finished["owner_status"],
            "BOUND_EXISTING_NATURAL_RECEIPT_OWNER_GATE1_ONLY",
        )
        self.assertEqual(
            finished["initial_receipt_evidence_class"],
            "NATURAL_INPUT_ABSENCE_AUDIT",
        )
        self.assertEqual(finished["initial_receipt_gate1_status"], "UNKNOWN")
        self.assertEqual(finished["initial_receipt_gate2_status"], "HOLD")
        self.assertEqual(finished["initial_receipt_recommendation"], "WAIT")
        self.assertIn(
            "IMPLEMENTATION_PENDING",
            us["natural_receipt_owner"]["owner_status"],
        )
        self.assertIsNone(us["signed_normalization_policy"])
        self.assertIsNone(us["ttl_seconds"])
        self.assert_pins(us["source_owner"])
        self.assert_pins(us["finished_session_owner"])
        self.assert_pins(us["natural_receipt_owner"])

    def test_v2_crypto_reuses_sources_without_promoting_group_layer(self):
        crypto = self.registry["markets"]["CRYPTO"]
        self.assertEqual(crypto["breadth_source"]["policy_status"], "RATIFIED")
        self.assertEqual(crypto["leadership_source"]["bucket_layer"], "BTC_ETH_ALT")
        self.assertEqual(
            crypto["leadership_source"]["sector_chain_group_layer"],
            "UNKNOWN_GROUP_LAYER",
        )
        self.assertEqual(
            crypto["leadership_source"]["group_coverage_policy_status"],
            "UNRATIFIED",
        )
        self.assertIn(
            "IMPLEMENTATION_PENDING",
            crypto["natural_receipt_owner"]["owner_status"],
        )
        self.assertIsNone(crypto["overall_freshness_policy"])
        for key in ("breadth_source", "leadership_source", "source_owner", "status_owner"):
            self.assert_pins(crypto[key])

    def test_v2_owner_ids_unique_and_no_authority_promotion(self):
        owners = []
        for market in self.registry["markets"].values():
            for key, value in market.items():
                if key.endswith("owner") and isinstance(value, dict):
                    owners.append(value["owner_id"])
                    for path_key, path_value in value.items():
                        if path_key.endswith("_path"):
                            self.assertTrue((ROOT / path_value).is_file(), path_value)
        owners.append(self.registry["aggregate"]["owner_id"])
        self.assertEqual(len(owners), len(set(owners)))
        authority = self.registry["authority"]
        self.assertTrue(authority["source_owner_architecture_authorized"])
        for key, value in authority.items():
            if key != "source_owner_architecture_authorized":
                self.assertFalse(value, key)


class RegimeCommonAggregationV1Test(unittest.TestCase):
    """Ratified PAPER baseline v1 aggregation as a hash-bound replay policy."""

    @classmethod
    def setUpClass(cls):
        cls.policy = MODULE.load_common_v1_policy()

    def confirmed(self, report):
        return [step["confirmed_regime"] for step in report["steps"]]

    def test_policy_is_bound_to_ratified_registry_and_paper_baseline(self):
        policy = self.policy
        binding = policy["binding"]

        self.assertEqual(policy["policy_status"], "RATIFIED_PAPER_BASELINE_V1")
        self.assertEqual(policy["contract_mode"], MODULE.COMMON_V1_REPLAY_MODE)
        self.assertEqual(
            binding["decision_identity"], MODULE.COMMON_V1_DECISION_IDENTITY
        )
        self.assertEqual(
            binding["decision_packet_sha256"],
            MODULE.COMMON_V1_DECISION_PACKET_SHA256,
        )
        self.assertEqual(
            binding["legacy_runtime_contract_sha256"],
            hashlib.sha256(MODULE.CONTRACT_PATH.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            binding["paper_baseline_policy_sha256"],
            hashlib.sha256(
                MODULE.PAPER_BASELINE_POLICY_PATH.read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(set(policy["weights"].values()), {1})
        self.assertEqual(
            policy["thresholds"],
            {
                "risk_on_min_score": 3,
                "neutral_min_score": -2,
                "neutral_max_score": 2,
                "risk_off_max_score": -3,
                "improving_min_delta": 2,
                "stable_min_delta": -1,
                "stable_max_delta": 1,
                "deteriorating_max_delta": -2,
                "stress_exit_min_exclusive_score": -3,
                "ordinary_transition_finalized_packets": 2,
                "confidence_denominator": 5,
            },
        )
        self.assertFalse(policy["market_specific_normalization_inherited"])
        self.assertEqual(policy["pit_replay_acceptance"], "NOT_ACCEPTED")
        self.assertEqual(
            policy["market_kill_stress_condition_status"],
            "UNRATIFIED_NOT_IMPLEMENTED",
        )

    def test_registry_paper_and_legacy_contract_drift_fail_closed(self):
        registry = json.loads(MODULE.REGISTRY_PATH.read_text(encoding="utf-8"))
        drifted_registry = copy.deepcopy(registry)
        drifted_registry["common_v1_alignment"]["classification"]["RISK_ON"] = "S>=2"
        resigned_decision = copy.deepcopy(registry)
        resigned_decision["decision"]["packet_sha256"] = "0" * 64
        paper = json.loads(
            MODULE.PAPER_BASELINE_POLICY_PATH.read_text(encoding="utf-8")
        )
        drifted_paper = copy.deepcopy(paper)
        drifted_paper["aggregation"]["RISK_ON_MIN_SCORE"] = 2
        contract = json.loads(MODULE.CONTRACT_PATH.read_text(encoding="utf-8"))

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            cases = [
                (
                    "COMMON_V1_ALIGNMENT_MISMATCH",
                    {
                        "registry_path": write_json(
                            root / "registry.json", drifted_registry
                        )
                    },
                ),
                (
                    "RATIFIED_DECISION_BINDING_INVALID",
                    {
                        "registry_path": write_json(
                            root / "decision.json", resigned_decision
                        )
                    },
                ),
                (
                    "PAPER_BASELINE_THRESHOLD_MISMATCH",
                    {
                        "paper_policy_path": write_json(
                            root / "paper.json", drifted_paper
                        )
                    },
                ),
                (
                    "LEGACY_CONTRACT_HASH_MISMATCH",
                    {
                        "contract_path": write_json(
                            root / "contract.json", contract, indent=4
                        )
                    },
                ),
            ]
            for code, kwargs in cases:
                with self.subTest(code=code), self.assertRaisesRegex(
                    MODULE.DecisionAuthorityError,
                    code,
                ):
                    MODULE.load_common_v1_policy(**kwargs)

    def test_bull_bear_sideways_and_stress_replays_are_deterministic(self):
        cases = {
            "bull": (
                [common_v1_axes("POSITIVE")] * 3,
                "RISK_ON",
                "1",
            ),
            "bear": (
                [common_v1_axes("NEGATIVE")] * 3,
                "RISK_OFF",
                "1",
            ),
            "sideways": (
                [sideways_axes()] * 3,
                "NEUTRAL",
                "0.2",
            ),
            "stress": (
                [common_v1_axes("NEUTRAL", stress=True)] * 3,
                "STRESS",
                "1",
            ),
        }
        for case_id, (patterns, regime, confidence) in cases.items():
            with self.subTest(case=case_id):
                sequence = common_v1_sequence(case_id, patterns)
                first = MODULE.replay_common_v1(sequence)
                rerun = MODULE.replay_common_v1(copy.deepcopy(sequence))

                self.assertEqual(
                    MODULE.canonical_bytes(first),
                    MODULE.canonical_bytes(rerun),
                )
                self.assertEqual(
                    first["determinism_status"],
                    "DETERMINISM_VERIFIED_COMMON_AGGREGATION_V1",
                )
                self.assertEqual(first["final_regime"], regime)
                self.assertEqual(first["final_confidence"], confidence)
                self.assertEqual(first["final_direction"], "STABLE")
                self.assertEqual(first["pit_replay_acceptance"], "NOT_ACCEPTED")
                self.assertEqual(
                    first, MODULE.validate_common_v1_replay(first, sequence)
                )

    def test_ordinary_transition_requires_two_finalized_packets(self):
        report = MODULE.replay_common_v1(
            common_v1_sequence(
                "transition",
                [
                    sideways_axes(),
                    sideways_axes(),
                    common_v1_axes("POSITIVE"),
                    common_v1_axes("POSITIVE"),
                ],
            )
        )

        self.assertEqual(
            self.confirmed(report),
            ["UNKNOWN", "NEUTRAL", "NEUTRAL", "RISK_ON"],
        )
        self.assertEqual(
            [step["raw_classification"] for step in report["steps"]],
            ["NEUTRAL", "NEUTRAL", "RISK_ON", "RISK_ON"],
        )
        self.assertEqual(
            [step["direction"] for step in report["steps"]],
            ["UNKNOWN", "STABLE", "IMPROVING", "STABLE"],
        )
        self.assertEqual(
            report["steps"][2]["hysteresis"]["rule"],
            "ORDINARY_CONFIRMATION_PENDING",
        )
        self.assertEqual([step["score"] for step in report["steps"]], [0, 0, 5, 5])

    def test_stress_entry_is_immediate_and_exit_needs_two_qualifying_packets(self):
        report = MODULE.replay_common_v1(
            common_v1_sequence(
                "stress-cycle",
                [
                    sideways_axes(),
                    sideways_axes(),
                    common_v1_axes("NEUTRAL", stress=True),
                    sideways_axes(),
                    sideways_axes(),
                ],
            )
        )

        self.assertEqual(
            self.confirmed(report),
            ["UNKNOWN", "NEUTRAL", "STRESS", "STRESS", "NEUTRAL"],
        )
        self.assertEqual(
            [step["hysteresis"]["rule"] for step in report["steps"][2:]],
            [
                "STRESS_ENTRY_IMMEDIATE",
                "STRESS_EXIT_PENDING",
                "ORDINARY_CONFIRMATION_MET",
            ],
        )
        self.assertTrue(report["steps"][2]["stress_override"])
        self.assertEqual(report["steps"][2]["score"], -1)
        self.assertEqual(report["steps"][2]["confidence"], "1")
        self.assertEqual(
            report["steps"][4]["hysteresis"]["stress_exit_qualified_streak"], 2
        )

    def test_three_of_five_coverage_stays_unknown_until_five_of_five(self):
        report = MODULE.replay_common_v1(
            common_v1_sequence(
                "crypto-coverage",
                [
                    common_v1_axes(
                        "POSITIVE", undefined=("RISK_VOL", "LIQUIDITY")
                    ),
                    common_v1_axes("POSITIVE"),
                    common_v1_axes("POSITIVE"),
                ],
                market="CRYPTO",
            )
        )
        blocked = report["steps"][0]

        self.assertEqual(blocked["coverage"]["ratio"], "3/5")
        self.assertFalse(blocked["coverage"]["minimum_coverage_met"])
        self.assertIsNone(blocked["score"])
        self.assertIsNone(blocked["confidence"])
        self.assertEqual(blocked["raw_classification"], "UNKNOWN")
        self.assertEqual(blocked["confirmed_regime"], "UNKNOWN")
        self.assertEqual(
            blocked["reasons"],
            [
                "MINIMUM_COVERAGE_NOT_MET",
                "RISK_VOL_UNDEFINED",
                "LIQUIDITY_UNDEFINED",
            ],
        )
        self.assertEqual(blocked["hysteresis"]["rule"], "UNKNOWN_IMMEDIATE")
        self.assertEqual(
            self.confirmed(report), ["UNKNOWN", "UNKNOWN", "RISK_ON"]
        )
        self.assertNotIn(
            "NEUTRAL", [step["confirmed_regime"] for step in report["steps"]]
        )
        self.assertEqual(report["steps"][1]["direction"], "UNKNOWN")

    def test_replay_never_opens_authority_or_accepts_pit_replay(self):
        report = MODULE.replay_common_v1(
            common_v1_sequence("authority", [common_v1_axes("POSITIVE")] * 2)
        )

        self.assertTrue(
            report["authority"]["common_aggregation_replay_authorized"]
        )
        for key, value in report["authority"].items():
            if key != "common_aggregation_replay_authorized":
                self.assertFalse(value, key)
        self.assertEqual(report["pit_replay_acceptance"], "NOT_ACCEPTED")
        self.assertFalse(
            report["market_specific_signed_normalization_inherited"]
        )
        self.assertEqual(
            report["market_kill_stress_condition_status"],
            "UNRATIFIED_NOT_IMPLEMENTED",
        )

    def test_replay_report_tampering_fails_closed(self):
        sequence = common_v1_sequence(
            "tamper", [common_v1_axes("POSITIVE")] * 2
        )
        report = MODULE.replay_common_v1(sequence)

        accepted = copy.deepcopy(report)
        accepted["pit_replay_acceptance"] = "ACCEPTED"
        promoted = copy.deepcopy(report)
        promoted["final_regime"] = "STRESS"
        authority = copy.deepcopy(report)
        authority["authority"]["runtime_classification_authorized"] = True
        threshold = copy.deepcopy(report)
        threshold["thresholds"]["risk_on_min_score"] = 1

        for changed in (accepted, promoted, authority, threshold):
            with self.subTest(changed=changed), self.assertRaisesRegex(
                MODULE.DecisionAuthorityError,
                "COMMON_V1_REPLAY_DERIVATION_MISMATCH",
            ):
                MODULE.validate_common_v1_replay(changed, sequence)

    def test_unsigned_market_measurements_and_bad_sequences_fail_closed(self):
        measurement = common_v1_sequence(
            "measurement", [common_v1_axes("POSITIVE")]
        )
        measurement["steps"][0]["axes"]["TREND"]["measurement"] = {
            "advance_fraction": "0.61"
        }
        floats = common_v1_sequence("floats", [common_v1_axes("POSITIVE")])
        floats["steps"][0]["axes"]["TREND"]["direction"] = 0.5
        stress_axis = common_v1_sequence("stressaxis", [common_v1_axes("POSITIVE")])
        stress_axis["steps"][0]["axes"]["TREND"]["direction"] = "STRESS"
        unordered = common_v1_sequence(
            "unordered", [common_v1_axes("POSITIVE")] * 2
        )
        unordered["steps"][1]["as_of_date"] = unordered["steps"][0]["as_of_date"]
        unknown_market = common_v1_sequence(
            "market", [common_v1_axes("POSITIVE")], market="JP"
        )

        cases = [
            ("STEP_INVALID", measurement),
            ("FLOAT_NOT_ALLOWED", floats),
            ("STEP_INVALID", stress_axis),
            ("PIT_ORDER_INVALID", unordered),
            ("MARKET_INVALID", unknown_market),
        ]
        for code, sequence in cases:
            with self.subTest(code=code), self.assertRaisesRegex(
                MODULE.DecisionAuthorityError,
                code,
            ):
                MODULE.replay_common_v1(sequence)

    def test_common_v1_cli_replay_and_validate(self):
        sequence = common_v1_sequence("cli", [common_v1_axes("POSITIVE")] * 2)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            sequence_path = root / "sequence.json"
            report_path = root / "report.json"
            sequence_path.write_text(json.dumps(sequence), encoding="utf-8")

            with contextlib.redirect_stdout(io.StringIO()):
                replay_exit = MODULE.main(
                    [
                        "replay-common-v1",
                        str(sequence_path),
                        "--out",
                        str(report_path),
                    ]
                )
                validate_exit = MODULE.main(
                    [
                        "validate-common-v1-replay",
                        str(report_path),
                        "--sequence",
                        str(sequence_path),
                    ]
                )
            self.assertEqual(replay_exit, 0)
            self.assertEqual(validate_exit, 0)
            persisted = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["final_regime"], "RISK_ON")
            self.assertEqual(
                persisted["policy_status"], "RATIFIED_PAPER_BASELINE_V1"
            )
            self.assertEqual(persisted["pit_replay_acceptance"], "NOT_ACCEPTED")


if __name__ == "__main__":
    unittest.main()
