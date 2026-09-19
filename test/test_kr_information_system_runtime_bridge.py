#!/usr/bin/env python3
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_information_system_runtime_bridge as B
from regime import kr_paper_runtime as R


EVIDENCE = ROOT / "evidence/regime/kr_information_system/2026-09-11"


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def qualification(bindings, status="RATIFIED_KR_PAPER_DISPLAY_ONLY") -> bytes:
    return B.pretty_bytes({
        "schema_version": "kr_information_system_runtime_qualification/1",
        "status": status,
        "effective_at": "2026-09-12T22:40:00Z",
        "bindings": bindings,
        "authority": {
            "paper_runtime_display_authorized": status == "RATIFIED_KR_PAPER_DISPLAY_ONLY",
            "strategy_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "capital_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_authorized": False,
        },
    })


def boundary():
    return {
        "schema_version": "kr_paper_runtime_session_boundary_freshness/3",
        "context_session_date": "2026-09-11",
        "execution_session_date": "2026-09-14",
        "context_session_close_at": "2026-09-11T06:30:00Z",
        "execution_session_close_at": "2026-09-14T06:30:00Z",
        "calendar_receipt_sha256": "1" * 64,
        "session_calendar": [],
        "calendar_usable_from": "2026-09-08T15:32:02.096487Z",
        "derived_ttl_seconds": 259200,
        "trusted_commit": "2" * 40,
        "decision_id": "KR_INTERNAL_PAPER_SESSION_BOUNDARY_FRESHNESS_V1",
        "decision_real_usable_from": "2026-08-30T21:00:00Z",
        "paper_policy_use_authorized": True,
        "actual_source_qualification": "UNKNOWN",
    }


def inputs():
    reference = (EVIDENCE / "KR_PAPER_REFERENCE_CANDIDATE.json").read_bytes()
    manifest = (EVIDENCE / "source-capture/manifest.json").read_bytes()
    responses = {
        str(path.relative_to(EVIDENCE / "source-capture")): path.read_bytes()
        for path in (EVIDENCE / "source-capture/responses").glob("*.json")
    }
    history = (EVIDENCE / "history/common-v1-replay-through-2026-09-10.json").read_bytes()
    acceptance = (EVIDENCE / "history/final-receipt.json").read_bytes()
    result = {
        "reference_raw": reference,
        "manifest_raw": manifest,
        "raw_responses": responses,
        "expected_source": {
            "reference_sha256": digest(reference),
            "manifest_sha256": digest(manifest),
            "source_contract_sha256": digest(B.SOURCE_CONTRACT_PATH.read_bytes()),
            "leadership_policy_sha256": digest(B.LEADERSHIP_POLICY_PATH.read_bytes()),
            "reference_policy_sha256": digest(B.REFERENCE_POLICY_PATH.read_bytes()),
        },
        "historical_replay_raw": history,
        "expected_historical_sha256": digest(history),
        "historical_acceptance_raw": acceptance,
        "expected_historical_acceptance_sha256": digest(acceptance),
        "evaluation_at": "2026-09-13T00:00:00Z",
        "code_revision": "2" * 40,
        "session_boundary": boundary(),
    }
    bindings = {
        "source_reference_sha256": result["expected_source"]["reference_sha256"],
        "source_manifest_sha256": result["expected_source"]["manifest_sha256"],
        "source_contract_sha256": result["expected_source"]["source_contract_sha256"],
        "leadership_policy_sha256": result["expected_source"]["leadership_policy_sha256"],
        "reference_policy_sha256": result["expected_source"]["reference_policy_sha256"],
        "raw_response_sha256": {path: digest(raw) for path, raw in sorted(responses.items())},
        "historical_replay_file_sha256": result["expected_historical_sha256"],
        "historical_acceptance_sha256": result["expected_historical_acceptance_sha256"],
        "common_policy_binding_sha256": B.COMMON.payload_sha256(B.COMMON.load_common_v1_policy()["binding"]),
        "context_session_date": result["session_boundary"]["context_session_date"],
        "execution_session_date": result["session_boundary"]["execution_session_date"],
        "implementation_sha256": {
            path: digest((ROOT / path).read_bytes()) for path in B.IMPLEMENTATION_PATHS
        },
    }
    q = qualification(bindings)
    result["qualification_raw"] = q
    result["expected_qualification_sha256"] = digest(q)
    return result


def requalify(args):
    current = json.loads(args["qualification_raw"])
    bindings = current["bindings"]
    bindings["source_reference_sha256"] = args["expected_source"]["reference_sha256"]
    bindings["source_manifest_sha256"] = args["expected_source"]["manifest_sha256"]
    bindings["raw_response_sha256"] = {
        path: digest(raw) for path, raw in sorted(args["raw_responses"].items())
    }
    bindings["historical_replay_file_sha256"] = args["expected_historical_sha256"]
    bindings["historical_acceptance_sha256"] = args["expected_historical_acceptance_sha256"]
    bindings["context_session_date"] = args["session_boundary"]["context_session_date"]
    bindings["execution_session_date"] = args["session_boundary"]["execution_session_date"]
    bindings["implementation_sha256"] = {
        path: digest((ROOT / path).read_bytes()) for path in B.IMPLEMENTATION_PATHS
    }
    raw = qualification(bindings)
    args["qualification_raw"] = raw
    args["expected_qualification_sha256"] = digest(raw)


def rewrite(args, source_edit=None, manifest_edit=None, wrapper_edit=None):
    wrapper = json.loads(args["reference_raw"])
    manifest = json.loads(args["manifest_raw"])
    if manifest_edit:
        manifest_edit(manifest)
        manifest["payload_sha256"] = B._manifest_payload_sha256(manifest)
        wrapper["source_packet"]["source"]["source_capture"]["manifest_payload_sha256"] = manifest["payload_sha256"]
    if source_edit:
        source_edit(wrapper["source_packet"])
    source = wrapper["source_packet"]
    unsigned = dict(source)
    unsigned.pop("payload_sha256")
    source["payload_sha256"] = digest(B.canonical_bytes(unsigned))
    if wrapper_edit:
        wrapper_edit(wrapper)
    args["reference_raw"] = B.pretty_bytes(wrapper)
    args["manifest_raw"] = B.pretty_bytes(manifest)
    args["expected_source"]["reference_sha256"] = digest(args["reference_raw"])
    args["expected_source"]["manifest_sha256"] = digest(args["manifest_raw"])
    requalify(args)


class InformationSystemRuntimeBridgeTest(unittest.TestCase):
    def test_actual_retained_bytes_open_confirmed_paper_display(self):
        result = B.evaluate_runtime(**inputs())
        self.assertEqual(result["runtime_regime"], "NEUTRAL")
        self.assertEqual(result["direction"], "DETERIORATING")
        self.assertEqual(result["confidence"], "0.2")
        current = result["current_observation"]
        self.assertEqual(current["candidate_regime"], "RISK_OFF")
        self.assertEqual(current["score"], -4)
        self.assertEqual(current["candidate_confidence"], "0.8")
        self.assertEqual(current["hysteresis"]["confirmation_count"], 1)
        self.assertEqual(current["hysteresis"]["confirmation_required"], 2)
        leadership = current["leadership"]
        self.assertEqual(leadership["observed_value"], {"positive_sectors": 17, "total": 46})
        self.assertTrue(result["runtime_decision_available"])
        self.assertTrue(result["authority"]["paper_runtime_display_authorized"])
        self.assertTrue(all(value is False for key, value in result["authority"].items()
                            if key != "paper_runtime_display_authorized"))

    def test_actual_evaluator_entry_point_uses_explicit_branch(self):
        args = inputs()
        evidence = {key: args[key] for key in (
            "reference_raw", "manifest_raw", "raw_responses", "expected_source",
            "historical_replay_raw", "expected_historical_sha256",
            "historical_acceptance_raw", "expected_historical_acceptance_sha256",
            "qualification_raw", "expected_qualification_sha256",
        )}
        with mock.patch.object(R, "_session_boundary_binding", return_value=boundary()):
            result = R.evaluate_kr_paper_runtime(
                source_packets=None,
                evaluation_at=args["evaluation_at"],
                code_revision=args["code_revision"],
                session_boundary_freshness={"explicit": "input"},
                information_system_evidence=evidence,
            )
        self.assertEqual(result["schema_version"], "kr_paper_runtime_decision/5")
        self.assertEqual(result["runtime_regime"], "NEUTRAL")
        self.assertEqual(result["current_observation"]["candidate_regime"], "RISK_OFF")
        self.assertEqual(result["actual_source_qualification"], "RATIFIED_KR_PAPER_DISPLAY_ONLY")
        with mock.patch.object(R, "_session_boundary_binding", return_value=boundary()):
            self.assertEqual(result, R.validate_kr_paper_runtime(
                result,
                source_packets=None,
                evaluation_at=args["evaluation_at"],
                code_revision=args["code_revision"],
                session_boundary_freshness={"explicit": "input"},
                information_system_evidence=evidence,
            ))

    def test_missing_or_changed_raw_response_fails_closed(self):
        args = inputs()
        args["raw_responses"].pop(next(iter(args["raw_responses"])))
        requalify(args)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_RESPONSE_SET_INVALID"):
            B.evaluate_runtime(**args)
        args = inputs()
        key = next(iter(args["raw_responses"]))
        args["raw_responses"][key] += b" "
        requalify(args)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_RESPONSE_SIZE_MISMATCH"):
            B.evaluate_runtime(**args)

    def test_unknown_schema_and_self_rehashed_authority_escalation_fail(self):
        args = inputs()
        wrapper = json.loads(args["reference_raw"])
        wrapper["source_packet"]["schema_version"] = "unknown/1"
        args["reference_raw"] = B.pretty_bytes(wrapper)
        args["expected_source"]["reference_sha256"] = digest(args["reference_raw"])
        requalify(args)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_PACKET_SCHEMA_INVALID"):
            B.evaluate_runtime(**args)
        args = inputs(); wrapper = json.loads(args["reference_raw"])
        source = wrapper["source_packet"]
        source["authority"]["order_authorized"] = True
        unsigned = dict(source); unsigned.pop("payload_sha256")
        source["payload_sha256"] = digest(B.canonical_bytes(unsigned))
        args["reference_raw"] = B.pretty_bytes(wrapper)
        args["expected_source"]["reference_sha256"] = digest(args["reference_raw"])
        requalify(args)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_AUTHORITY_ESCALATION"):
            B.evaluate_runtime(**args)

    def test_unratified_qualification_preavailability_and_stale_fail(self):
        args = inputs(); current = json.loads(args["qualification_raw"])
        q = qualification(current["bindings"], "PENDING_CIO_RATIFICATION")
        args["qualification_raw"] = q; args["expected_qualification_sha256"] = digest(q)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "QUALIFICATION_NOT_RATIFIED"):
            B.evaluate_runtime(**args)
        args = inputs(); args["evaluation_at"] = "2026-09-12T22:39:26Z"
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_NOT_YET_USABLE"):
            B.evaluate_runtime(**args)
        args = inputs(); args["evaluation_at"] = "2026-09-14T06:30:00Z"
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "LATEST_SOURCE_STALE"):
            B.evaluate_runtime(**args)

    def test_history_and_calendar_chain_mismatch_fail(self):
        args = inputs(); args["session_boundary"]["context_session_date"] = "2026-09-10"
        requalify(args)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "LATEST_SOURCE_NOT_CONTEXT_SESSION"):
            B.evaluate_runtime(**args)
        args = inputs(); history = json.loads(args["historical_replay_raw"])
        history["steps"][-1]["as_of_date"] = "2026-09-09"
        args["historical_replay_raw"] = B.pretty_bytes(history)
        args["expected_historical_sha256"] = digest(args["historical_replay_raw"])
        requalify(args)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "HISTORY_REPLAY_REDERIVATION_MISMATCH"):
            B.evaluate_runtime(**args)

    def test_request_lineage_and_raw_projection_tampering_fail(self):
        args = inputs()
        rewrite(args, source_edit=lambda source: source["source"].__setitem__("requests", {}))
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_LINEAGE_INVALID"):
            B.evaluate_runtime(**args)

        args = inputs()
        def replace_endpoints(source):
            for markets in source["source"]["requests"].values():
                for row in markets.values():
                    row["endpoint"] = "https://invalid.example/no-provider"
        rewrite(args, source_edit=replace_endpoints)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_ENDPOINT_MISMATCH"):
            B.evaluate_runtime(**args)

        args = inputs()
        def replace_request(manifest):
            manifest["records"][0]["request"]["public_params"].update(
                {"trdDd": "20200101", "bld": "SYNTHETIC_WRONG_BUILDER"}
            )
        rewrite(args, manifest_edit=replace_request)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_REQUEST_PARAMS_INVALID"):
            B.evaluate_runtime(**args)

        args = inputs()
        rewrite(args, manifest_edit=lambda manifest: manifest["records"][0]["normalized_frame"].__setitem__(
            "required_projection_sha256", "0" * 64
        ))
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "RAW_PROJECTION_HASH_MISMATCH"):
            B.evaluate_runtime(**args)

    def test_early_source_and_forged_reference_confidence_fail(self):
        args = inputs()
        def early_manifest(manifest):
            manifest["capture_started_at_utc"] = "2026-09-11T08:39:22Z"
            manifest["capture_completed_at_utc"] = "2026-09-11T08:39:27Z"
            for record in manifest["records"]:
                record["response"]["received_at_utc"] = record["response"]["received_at_utc"].replace(
                    "2026-09-12T22:", "2026-09-11T08:"
                )
        def early_source(source):
            source["available_at"] = source["generated_at"] = "2026-09-11T08:39:27Z"
            for markets in source["source"]["requests"].values():
                for row in markets.values():
                    for period in ("previous", "current"):
                        key = period + "_fetched_at_utc"
                        row[key] = row[key].replace("2026-09-12T22:", "2026-09-11T08:")
        rewrite(args, source_edit=early_source, manifest_edit=early_manifest)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "SOURCE_BEFORE_EXISTING_EARLIEST_USABLE_TIME"):
            B.evaluate_runtime(**args)

        args = inputs()
        rewrite(args, wrapper_edit=lambda wrapper: wrapper["paper_reference"]["paper_reference"].__setitem__(
            "confidence", "0.99"
        ))
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "PAPER_REFERENCE_CLASSIFICATION_MISMATCH"):
            B.evaluate_runtime(**args)

    def test_qualification_is_bound_to_exact_implementation_bytes(self):
        args = inputs()
        current = json.loads(args["qualification_raw"])
        current["bindings"]["implementation_sha256"][B.IMPLEMENTATION_PATHS[0]] = "0" * 64
        raw = B.pretty_bytes(current)
        args["qualification_raw"] = raw
        args["expected_qualification_sha256"] = digest(raw)
        with self.assertRaisesRegex(B.InformationSystemRuntimeError, "QUALIFICATION_BINDING_MISMATCH"):
            B.evaluate_runtime(**args)


if __name__ == "__main__":
    unittest.main()
