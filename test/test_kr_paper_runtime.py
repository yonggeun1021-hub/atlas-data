"""Synthetic contract tests; no actual market acceptance or forward claims."""
import copy
import json
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unittest import mock

from regime import kr_paper_runtime as R
import test_korea_market_signals as K
from test_paper_regime_reference import kr_packet_fixture, kr_policy_fixture


def encoded(value):
    return R.COMMON.canonical_bytes(value)


def make_inputs(evidence_class="SYNTHETIC_OFFLINE_FIXTURE", *, stress=False):
    common = R.COMMON.load_common_v1_policy()
    policy = {
        "schema_version": "kr_paper_runtime_policy/1", "market": "KR",
        "evidence_class": evidence_class, "policy_id": "SYNTHETIC-TEST-ONLY",
        "acceptance_refs": {key: "fixture-only:" + key for key in
                            ("normalization", "freshness", "pit", "common_runtime")},
        "effective_from": "2026-08-01T00:00:00Z", "effective_until": "2026-09-01T00:00:00Z",
        "reference_policy_sha256": common["binding"]["paper_baseline_policy_sha256"],
        "common_policy_binding_sha256": R.COMMON.payload_sha256(common["binding"]),
        "source_contract_sha256": R.digest(R.SOURCE.CONTRACT_PATH.read_bytes()),
        "leadership_policy_sha256": R.digest(R.SOURCE.LEADERSHIP_POLICY_PATH.read_bytes()),
        # Test timing window, not a proposed or adopted production TTL.
        "ttl_seconds": 14400,
    }
    sources, receipts = [], []
    for previous, current in (("20260826", "20260827"), ("20260827", "20260828")):
        payloads = K.fixtures(previous, current)
        if not stress:
            for (family, market, day), payload in payloads.items():
                if family == "stock":
                    for row in payload["OutBlock_1"]:
                        row["FLUC_RT"] = "1" if float(row["FLUC_RT"]) > 0 else "-1"
        leadership = json.loads(R.SOURCE.LEADERSHIP_POLICY_PATH.read_text())
        for step, day in enumerate((previous, current)):
            for market in ("kospi", "kosdaq"):
                records = [r for r in leadership["records"] if r["series_identity"].startswith(market.upper() + "::")]
                payloads[("index", market, day)] = {"OutBlock_1": [
                    K.index_row(day, r["series_identity"].split("::")[1],
                                100 + step * (1 if i % 2 == 0 or "BENCHMARK" in r["role"] else -1))
                    for i, r in enumerate(records)]}
        opener = K.FakeOpener(payloads)
        stamp = current[:4] + "-" + current[4:6] + "-" + current[6:]
        with mock.patch.object(K.MODULE, "_now_utc", return_value=stamp + "T09:00:00Z"):
            before = K.MODULE.fetch_complete_session(K.TOKEN, previous, opener=opener)
            after = K.MODULE.fetch_complete_session(K.TOKEN, current, opener=opener)
        packet = K.MODULE.build_packet(before, after)
        raw = encoded(packet)
        sources.append(raw)
        receipts.append({"source_sha256": R.digest(raw), "source_ref": "fixture:kr:" + stamp,
            "owner_receipt_sha256": R.digest(b"synthetic owner receipt " + current.encode()),
            "as_of_date": stamp, "available_at": stamp + "T09:00:00Z",
            "session_close_at": stamp + "T06:30:00Z", "decision_at": stamp + "T09:01:00Z"})
    policy_bytes = encoded(policy)
    receipt = {"schema_version": "kr_paper_runtime_qualification/1", "market": "KR",
               "evidence_class": evidence_class, "policy_sha256": R.digest(policy_bytes),
               "calendar_receipt_sha256": R.digest(b"synthetic calendar only"), "sources": receipts}
    return {"source_packets": sources, "evaluation_at": "2026-08-28T09:01:00Z",
            "code_revision": "98537aaf64b3bdcd84d157f9b13841094df2811f",
            "experiment_policy": policy_bytes, "expected_policy_sha256": R.digest(policy_bytes),
            "qualification_receipt": encoded(receipt), "expected_qualification_sha256": R.digest(encoded(receipt))}


def replace_receipt(inputs, change):
    value = json.loads(inputs["qualification_receipt"])
    change(value)
    inputs["qualification_receipt"] = encoded(value)
    inputs["expected_qualification_sha256"] = R.digest(inputs["qualification_receipt"])


def replace_policy(inputs, change):
    value = json.loads(inputs["experiment_policy"])
    change(value)
    inputs["experiment_policy"] = encoded(value)
    inputs["expected_policy_sha256"] = R.digest(inputs["experiment_policy"])
    replace_receipt(inputs, lambda r: r.update(policy_sha256=inputs["expected_policy_sha256"]))


class KRRuntimeTest(unittest.TestCase):
    def test_extracted_arithmetic_matches_existing_reference(self):
        source, policy = kr_packet_fixture(), kr_policy_fixture()
        self.assertEqual(R.REFERENCE.normalize_kr_measurements(source, policy),
                         R.REFERENCE.build_kr(source, policy)["axes"])

    def test_fixture_calculates_signed_axes_and_common_without_runtime_promotion(self):
        args = make_inputs()
        output = R.evaluate_kr_paper_runtime(**args)
        self.assertEqual(output["reasons"], [])
        self.assertEqual(output["decision_status"], "PAPER_SIMULATION_CLASSIFIED")
        self.assertNotEqual(output["paper_regime"], "UNKNOWN")
        self.assertEqual(output["runtime_regime"], "UNKNOWN")
        self.assertFalse(output["runtime_decision_available"])
        self.assertEqual(len(output["signed_axes"]), 2)
        self.assertEqual(output["paper_regime"], output["aggregation"]["final_regime"])
        self.assertEqual(output, R.validate_kr_paper_runtime(output, **args))
        self.assertEqual(output, R.evaluate_kr_paper_runtime(**args))
        self.assertFalse(output["raw_provider_bytes_authenticated"])

    def test_natural_branch_contract_only_uses_mock_admission_not_real_evidence(self):
        args = make_inputs("LIVE_NATURAL")
        output = R.evaluate_kr_paper_runtime(**args)
        self.assertEqual(output["decision_status"], "PAPER_RUNTIME_CLASSIFIED")
        self.assertTrue(output["runtime_decision_available"])
        self.assertFalse(output["authority"]["operational_policy_ratified"])
        self.assertFalse(output["authority"]["real_order_authorized"])

    def test_missing_policy_or_receipt_stays_unknown(self):
        for field in ("experiment_policy", "qualification_receipt", "expected_policy_sha256",
                      "expected_qualification_sha256"):
            args = make_inputs(); args[field] = None
            output = R.evaluate_kr_paper_runtime(**args)
            self.assertEqual(output["runtime_regime"], "UNKNOWN", field)
            self.assertFalse(output["runtime_decision_available"])
            self.assertTrue(output["reasons"])

    def test_source_and_policy_resigned_without_external_pin_are_rejected(self):
        args = make_inputs()
        source = json.loads(args["source_packets"][0]); source["axes"]["BREADTH"]["measurement"]["combined"]["advance_fraction"] = "1"
        source.pop("payload_sha256"); source["payload_sha256"] = K.MODULE.payload_sha256(source)
        args["source_packets"][0] = encoded(source)
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["SOURCE_BYTES_MISMATCH"])
        args = make_inputs(); policy = json.loads(args["experiment_policy"]); policy["ttl_seconds"] *= 2
        args["experiment_policy"] = encoded(policy)
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["POLICY_HASH_MISMATCH"])

    def test_stale_future_and_calendar_mismatch_fail_closed(self):
        mutations = [
            (lambda r: r["sources"][-1].update(decision_at="2026-08-28T08:59:00Z"), "SOURCE_NOT_AVAILABLE_AT_DECISION"),
            (lambda r: r["sources"][-1].update(session_close_at="2026-08-27T06:30:00Z"), "SESSION_CLOSE_DATE_MISMATCH"),
            (lambda r: r["sources"][-1].update(decision_at="2026-08-28T09:02:00Z"), "FUTURE_DECISION_IN_HISTORY"),
        ]
        for mutate, expected in mutations:
            args = make_inputs(); replace_receipt(args, mutate)
            self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], [expected])
        args = make_inputs(); args["evaluation_at"] = "2026-08-28T10:30:01Z"
        result = R.evaluate_kr_paper_runtime(**args)
        self.assertEqual(result["reasons"], ["LATEST_SOURCE_STALE"])
        self.assertEqual(result["signed_axes"], [])
        self.assertIsNone(result["aggregation"])
        self.assertEqual(result["runtime_regime"], "UNKNOWN")

    def test_policy_values_are_explicit_and_bound_to_existing_rules(self):
        for mutate, reason in [
            (lambda p: p.update(ttl_seconds=True), "EXPLICIT_TTL_REQUIRED"),
            (lambda p: p.update(reference_policy_sha256="0" * 64), "REFERENCE_POLICY_BINDING_MISMATCH"),
            (lambda p: p.update(common_policy_binding_sha256="0" * 64), "COMMON_POLICY_BINDING_MISMATCH"),
            (lambda p: p.update(source_contract_sha256="0" * 64), "SOURCE_CONTRACT_BINDING_MISMATCH"),
            (lambda p: p.update(effective_until="2026-08-28T09:01:00Z"), "POLICY_OUTSIDE_EFFECTIVE_WINDOW"),
        ]:
            args = make_inputs(); replace_policy(args, mutate)
            self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], [reason])

    def test_replay_and_class_mismatch_cannot_be_natural(self):
        args = make_inputs("HISTORICAL_REPLAY")
        result = R.evaluate_kr_paper_runtime(**args)
        self.assertFalse(result["runtime_decision_available"])
        self.assertEqual(result["decision_status"], "PAPER_SIMULATION_CLASSIFIED")
        replace_receipt(args, lambda r: r.update(evidence_class="LIVE_NATURAL"))
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["QUALIFICATION_SCOPE_MISMATCH"])

    def test_existing_leadership_membership_and_earliest_time_cannot_be_bypassed(self):
        args = make_inputs("LIVE_NATURAL")
        source = json.loads(args["source_packets"][0])
        source["axes"]["LEADERSHIP"]["measurement"]["observations"].pop()
        source.pop("payload_sha256"); source["payload_sha256"] = K.MODULE.payload_sha256(source)
        args["source_packets"][0] = encoded(source)
        replace_receipt(args, lambda r: r["sources"][0].update(source_sha256=R.digest(encoded(source))))
        result = R.evaluate_kr_paper_runtime(**args)
        self.assertEqual(result["reasons"], ["SECTOR_RELATIVE_STRENGTH_POLICY_MISMATCH"])
        self.assertFalse(result["runtime_decision_available"])
        args = make_inputs("LIVE_NATURAL")
        source = json.loads(args["source_packets"][0])
        source["available_at"] = source["generated_at"] = "2026-08-27T08:59:59Z"
        for family in source["source"]["requests"].values():
            for row in family.values():
                row["previous_fetched_at_utc"] = row["current_fetched_at_utc"] = source["available_at"]
        source.pop("payload_sha256"); source["payload_sha256"] = K.MODULE.payload_sha256(source)
        args["source_packets"][0] = encoded(source)
        replace_receipt(args, lambda r: r["sources"][0].update(
            source_sha256=R.digest(encoded(source)), available_at=source["available_at"]))
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"],
                         ["SOURCE_BEFORE_EXISTING_EARLIEST_USABLE_TIME"])
        args = make_inputs()
        replace_policy(args, lambda p: p.update(leadership_policy_sha256="0" * 64))
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["LEADERSHIP_POLICY_BINDING_MISMATCH"])

    def test_order_missing_chain_and_duplicate_are_rejected(self):
        args = make_inputs(); args["source_packets"].reverse()
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["SOURCE_BYTES_MISMATCH"])
        args = make_inputs(); args["source_packets"].pop()
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["QUALIFIED_SOURCE_CHAIN_INCOMPLETE"])
        args = make_inputs(); args["source_packets"][1] = args["source_packets"][0]
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["DUPLICATE_SOURCE_PACKET"])

    def test_common_two_packet_confirmation_and_immediate_stress_are_reused(self):
        args = make_inputs()
        args["source_packets"] = args["source_packets"][-1:]
        replace_receipt(args, lambda r: r.update(sources=r["sources"][-1:]))
        pending = R.evaluate_kr_paper_runtime(**args)
        self.assertEqual(pending["reasons"], ["COMMON_CONFIRMATION_PENDING"])
        self.assertEqual(pending["runtime_regime"], "UNKNOWN")
        self.assertEqual(pending["aggregation"]["steps"][0]["hysteresis"]["confirmation_count"], 1)
        complete = R.evaluate_kr_paper_runtime(**make_inputs())
        self.assertEqual(complete["paper_regime"], "NEUTRAL")
        self.assertEqual(complete["aggregation"]["steps"][-1]["hysteresis"]["confirmation_count"], 2)
        stress_args = make_inputs(stress=True)
        stress_args["source_packets"] = stress_args["source_packets"][-1:]
        replace_receipt(stress_args, lambda r: r.update(sources=r["sources"][-1:]))
        stress = R.evaluate_kr_paper_runtime(**stress_args)
        self.assertEqual(stress["paper_regime"], "STRESS")
        self.assertEqual(stress["aggregation"]["steps"][-1]["hysteresis"]["rule"], "STRESS_ENTRY_IMMEDIATE")

    def test_trusted_but_invalid_source_domain_and_pit_are_not_repaired(self):
        for bad, reason in [("1.1", "SOURCE_MEASUREMENT_DOMAIN_INVALID"),
                            (True, "KR_BREADTH_INVALID")]:
            args = make_inputs(); source = json.loads(args["source_packets"][0])
            source["axes"]["BREADTH"]["measurement"]["combined"]["advance_fraction"] = bad
            source.pop("payload_sha256"); source["payload_sha256"] = K.MODULE.payload_sha256(source)
            args["source_packets"][0] = encoded(source)
            replace_receipt(args, lambda r: r["sources"][0].update(source_sha256=R.digest(encoded(source))))
            self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], [reason])
        args = make_inputs(); source = json.loads(args["source_packets"][1])
        source["previous_date"] = "2026-08-25"
        source.pop("payload_sha256"); source["payload_sha256"] = K.MODULE.payload_sha256(source)
        args["source_packets"][1] = encoded(source)
        replace_receipt(args, lambda r: r["sources"][1].update(source_sha256=R.digest(encoded(source))))
        self.assertEqual(R.evaluate_kr_paper_runtime(**args)["reasons"], ["SESSION_CHAIN_GAP"])

    def test_consumer_preserves_scores_and_adds_same_identity_to_existing_funnel(self):
        args = make_inputs("LIVE_NATURAL"); runtime = R.evaluate_kr_paper_runtime(**args)
        data = json.loads((R.ROOT / "test/fixtures/common_paper_candidate_funnel/all_pass.json").read_text())
        data["evaluationAt"] = args["evaluation_at"]
        data["candidates"] = [data["candidates"][0]]
        row = data["candidates"][0]; row["sourceTimestamp"] = args["evaluation_at"]
        row["completedBarTrigger"]["completedAt"] = "2026-08-28T06:30:00Z"
        before = copy.deepcopy(data)
        baseline = R.FUNNEL.reduce_funnel(data)
        result = R.reduce_funnel_with_kr_regime(data, runtime, **args)
        self.assertEqual(data, before)
        self.assertEqual(result["universe"][0]["scoreBreakdown"], baseline["universe"][0]["scoreBreakdown"])
        self.assertEqual(result["universe"][0]["score"], baseline["universe"][0]["score"])
        self.assertIn(runtime["decision_id"], result["universe"][0]["sourceRefs"])
        fixture_args = make_inputs(); simulated = R.evaluate_kr_paper_runtime(**fixture_args)
        with self.assertRaisesRegex(R.KRRuntimeError, "REGIME_NOT_AVAILABLE"):
            R.reduce_funnel_with_kr_regime(data, simulated, **fixture_args)

    def test_output_tamper_and_boolean_alias_rejected_even_resigned(self):
        args = make_inputs(); output = R.evaluate_kr_paper_runtime(**args)
        for field, value in [("runtime_decision_available", 0), ("paper_regime", "RISK_ON")]:
            tampered = copy.deepcopy(output); tampered[field] = value
            tampered.pop("decision_id"); tampered["decision_id"] = "kr-paper-regime:" + R.COMMON.payload_sha256(tampered)
            with self.assertRaisesRegex(R.KRRuntimeError, "RUNTIME_REDERIVATION_MISMATCH"):
                R.validate_kr_paper_runtime(tampered, **args)


if __name__ == "__main__":
    unittest.main()
