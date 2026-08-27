#!/usr/bin/env python3
"""P8-05 Rotation / Discovery briefing regression."""

import ast
import copy
import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from clock import run_dynamic_clock
SOURCE = ROOT / "briefing" / "rotation_discovery.py"


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module("rotation_discovery_briefing", SOURCE)
ROTATION_FIXTURE = load_module(
    "rotation_discovery_rotation_fixture", ROOT / "test" / "test_rotation_state_ledger.py"
)
DISCOVERY_FIXTURE = load_module(
    "rotation_discovery_event_fixture", ROOT / "test" / "test_event_discovery_case.py"
)
WILDCARD_FIXTURE = load_module(
    "rotation_discovery_wildcard_fixture",
    ROOT / "test" / "test_wildcard_operational_intake.py",
)
CONTRACT = MODULE.load_contract()


def _parse_utc(value):
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def current_dart_input_decision_at():
    source = json.loads(MODULE.DART_OBSERVATION.DEFAULT_DART.read_text(encoding="utf-8"))
    content = json.loads(
        MODULE.DART_OBSERVATION.DEFAULT_CONTENT.read_text(encoding="utf-8")
    )
    return max(
        _parse_utc(source["collected_at_utc"]),
        _parse_utc(content["observed_at_utc"]),
    ).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def operational_dart_times():
    packets = [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(
            (ROOT / "data/observations/dart_event_observations").glob("*/*.json")
        )
    ]
    if not packets:
        raise AssertionError("committed DART observation packet required")
    ordered = sorted(packets, key=lambda packet: _parse_utc(packet["decision_at"]))
    earliest = _parse_utc(ordered[0]["decision_at"]) - dt.timedelta(seconds=1)
    latest = _parse_utc(ordered[-1]["decision_at"]) + dt.timedelta(seconds=1)
    return (
        earliest.isoformat().replace("+00:00", "Z"),
        latest.isoformat().replace("+00:00", "Z"),
        ordered[-1],
    )


def empty_ledger():
    return MODULE.ROTATION.empty_ledger()


def observed_ledger():
    packet = ROTATION_FIXTURE.us_packet()
    return MODULE.ROTATION.apply_rotation(
        packet, ROTATION_FIXTURE.policy_for(packet)
    )


def records():
    return [DISCOVERY_FIXTURE.d1_record()]


def bindings():
    return DISCOVERY_FIXTURE.bindings()


class RotationDiscoveryBriefingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        latest = run_dynamic_clock.run()
        cls.dynamic_report = run_dynamic_clock.run(
            decision_date=latest["report_asof_evidence_date"]
        )
        cls.dynamic_generated_at = f"{cls.dynamic_report['decision_date']}T23:59:59Z"

    def test_contract_is_read_model_only_and_closes_promotion_action_authority(self):
        self.assertTrue(CONTRACT["authority"]["briefing_read_model_only"])
        for key, value in CONTRACT["authority"].items():
            if key != "briefing_read_model_only":
                self.assertFalse(value, key)

    def test_empty_rotation_and_unresolved_case_are_explicit_not_promoted(self):
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        self.assertEqual(result["rotation"]["ledger_status"], "EMPTY")
        self.assertEqual(result["rotation"]["latest_changes"], [])
        self.assertEqual(result["discovery"]["case_count"], 1)
        self.assertEqual(
            result["discovery"]["cases"][0]["evidence_status"],
            "EVIDENCE_UNRESOLVED",
        )
        self.assertEqual(result["discovery"]["new_candidates"], [])
        self.assertEqual(result["discovery"]["existing_candidate_changes"], [])

    def test_observed_rotation_latest_change_is_preserved_without_ranking(self):
        ledger = observed_ledger()
        result = MODULE.build_briefing(
            ledger, records(), bindings(),
            "evening", "2026-08-21T02:00:00Z", CONTRACT,
        )
        self.assertEqual(
            result["rotation"]["latest_change_count"], len(ledger["records"])
        )
        first = result["rotation"]["latest_changes"][0]
        source = ledger["records"][0]
        self.assertEqual(first["current_state"], source["current_p2_state"])
        self.assertEqual(first["state_transition"], source["state_transition"])
        self.assertEqual(first["record_sha256"], source["record_sha256"])
        self.assertIsNone(result["summary"]["ranked_candidate"])
        self.assertIsNone(result["summary"]["action"])

    def test_explicit_discovery_evidence_lineage_is_preserved(self):
        proof = DISCOVERY_FIXTURE.evidence()
        binding_doc = DISCOVERY_FIXTURE.bindings(
            DISCOVERY_FIXTURE.binding(proof=proof)
        )
        result = MODULE.build_briefing(
            empty_ledger(), records(), binding_doc,
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        case = result["discovery"]["cases"][0]
        self.assertEqual(case["evidence_status"], "EVIDENCE_LINKED")
        self.assertEqual(case["evidence_lineage"]["source_sha256"], "a" * 64)
        self.assertEqual(case["promotion_status"], "PROMOTION_NOT_AUTHORIZED")
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["investment_action"])

    def test_real_dynamic_signals_are_visible_without_candidate_promotion(self):
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", self.dynamic_generated_at, CONTRACT,
            dynamic_report=self.dynamic_report,
        )
        signal = result["signal_observations"]
        expected = sum(
            len(market["review_queue"])
            for market in self.dynamic_report["by_market"].values()
        )
        self.assertGreater(expected, 0)
        self.assertEqual(signal["observation_count"], expected)
        self.assertEqual(result["summary"]["signal_observation_count"], expected)
        self.assertEqual(result["summary"]["new_candidate_count"], 0)
        self.assertEqual(result["summary"]["ready_count"], 0)
        self.assertEqual(result["summary"]["entry_trigger_count"], 0)
        self.assertEqual(result["discovery"]["new_candidates"], [])
        self.assertTrue(all(
            row["ready_status"] == "NOT_EVALUATED"
            and row["promotion_status"] == "PROMOTION_NOT_AUTHORIZED"
            and row["action"] is None
            for row in signal["observations"]
        ))

    def test_real_dart_observations_are_visible_but_escalation_stays_blocked(self):
        _, generated_at, expected_packet = operational_dart_times()
        source = MODULE.load_operational_dart_observation_packet(
            generated_at, ROOT
        )
        self.assertIsNotNone(source)
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", generated_at, CONTRACT,
            dart_observation_packet=source, dart_root=ROOT,
        )
        section = result["dart_observations"]
        expected_summary = expected_packet["summary"]
        self.assertEqual(
            section["observation_count"], expected_summary["relevant_filing_count"]
        )
        self.assertEqual(
            section["raw_bytes_verified_count"],
            expected_summary["raw_bytes_verified_count"],
        )
        self.assertEqual(
            section["metadata_only_count"], expected_summary["metadata_only_count"]
        )
        self.assertEqual(
            section["source_failed_count"], expected_summary.get("source_failed_count", 0)
        )
        self.assertEqual(
            section["content_failure_count"],
            expected_summary.get("content_failure_count", 0),
        )
        self.assertEqual(
            result["summary"]["dart_observation_count"], section["observation_count"]
        )
        for row in section["observations"]:
            self.assertIsNone(row["event_type"])
            self.assertIsNone(row["direction"])
            self.assertIsNone(row["importance"])
            self.assertEqual(row["ready_status"], "NOT_EVALUATED")
            self.assertEqual(row["promotion_status"], "PROMOTION_NOT_AUTHORIZED")
            self.assertIsNone(row["action"])

    def test_current_v2_dart_packet_is_consumed_without_reinterpreting_v1_history(self):
        source = MODULE.DART_OBSERVATION.build_packet(
            decision_at=current_dart_input_decision_at()
        )
        generated_at = (
            _parse_utc(source["decision_at"]) + dt.timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        self.assertEqual(source["schema_version"], "dart_event_observation_packet/2")
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", generated_at, CONTRACT,
            dart_observation_packet=source, dart_root=ROOT,
        )
        self.assertEqual(
            result["dart_observations"]["source_packet"]["schema_version"],
            "dart_event_observation_packet/2",
        )
        self.assertEqual(result["dart_observations"]["source_failed_count"], 0)

    def test_partial_failure_counts_remain_visible_in_briefing(self):
        source = MODULE.DART_OBSERVATION.build_packet(
            decision_at=current_dart_input_decision_at()
        )
        generated_at = (
            _parse_utc(source["decision_at"]) + dt.timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        source["status"] = (
            "DART_OBSERVATIONS_RECORDED_WITH_PARTIAL_FAILURES_ESCALATION_BLOCKED"
        )
        source["summary"]["source_failed_count"] = 1
        source["summary"]["source_ok_count"] -= 1
        source["source_failures"] = [{
            "ticker": "298040", "name": "효성중공업", "atlas_stage": "Candidate",
            "coverage": True, "status": "SOURCE_COLLECTION_FAILED",
            "reasons": ["DART_STOCK_COLLECTION_FAILED"],
        }]
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with mock.patch.object(
            MODULE, "_validated_dart_observation_packet", return_value=source,
        ):
            result = MODULE.build_briefing(
                empty_ledger(), records(), bindings(),
                "evening", generated_at, CONTRACT,
                dart_observation_packet=source, dart_root=ROOT,
            )
        section = result["dart_observations"]
        self.assertEqual(section["source_failed_count"], 1)
        self.assertEqual(section["source_failures"][0]["ticker"], "298040")
        self.assertIn("WITH_PARTIAL_FAILURES", section["status"])

    def test_partial_failure_is_visible_even_when_no_observation_row_survives(self):
        source = MODULE.DART_OBSERVATION.build_packet(
            decision_at=current_dart_input_decision_at()
        )
        generated_at = (
            _parse_utc(source["decision_at"]) + dt.timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z")
        source["status"] = (
            "DART_OBSERVATIONS_RECORDED_WITH_PARTIAL_FAILURES_ESCALATION_BLOCKED"
        )
        source["observations"] = []
        source["summary"].update({
            "relevant_filing_count": 0,
            "raw_bytes_verified_count": 0,
            "metadata_only_count": 0,
            "source_failed_count": 1,
            "content_failure_count": 0,
        })
        source["source_failures"] = [{
            "ticker": "298040", "name": "효성중공업", "atlas_stage": "Candidate",
            "coverage": True, "status": "SOURCE_COLLECTION_FAILED",
            "reasons": ["DART_STOCK_COLLECTION_FAILED"],
        }]
        source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in source.items() if key != "packet_sha256"
        })
        with mock.patch.object(
            MODULE, "_validated_dart_observation_packet", return_value=source,
        ):
            result = MODULE.build_briefing(
                empty_ledger(), records(), bindings(),
                "evening", generated_at, CONTRACT,
                dart_observation_packet=source, dart_root=ROOT,
            )
        section = result["dart_observations"]
        self.assertEqual(section["observation_count"], 0)
        self.assertEqual(section["source_failed_count"], 1)
        self.assertIn("WITH_PARTIAL_FAILURES", section["status"])

    def test_dart_source_and_projection_tamper_fail_closed(self):
        _, generated_at, _ = operational_dart_times()
        source = MODULE.load_operational_dart_observation_packet(
            generated_at, ROOT
        )
        tampered_source = copy.deepcopy(source)
        tampered_source["observations"][0]["filing_title"] = "조작된 공시"
        tampered_source["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in tampered_source.items()
            if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError,
            "DART_OBSERVATION_PACKET_INVALID",
        ):
            MODULE.build_briefing(
                empty_ledger(), records(), bindings(),
                "evening", generated_at, CONTRACT,
                dart_observation_packet=tampered_source, dart_root=ROOT,
            )

        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", generated_at, CONTRACT,
            dart_observation_packet=source, dart_root=ROOT,
        )
        result["dart_observations"]["observations"][0]["ready_status"] = "READY"
        result["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in result.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError,
            "BRIEFING_DART_DERIVATION_MISMATCH",
        ):
            MODULE.validate_briefing(result, CONTRACT, dart_root=ROOT)

    def test_future_dart_observation_is_not_backfilled(self):
        before_first, _, _ = operational_dart_times()
        self.assertIsNone(MODULE.load_operational_dart_observation_packet(
            before_first, ROOT
        ))

    def test_loader_rebuilds_only_the_latest_eligible_dart_pointer(self):
        _, generated_at, _ = operational_dart_times()
        latest = MODULE.load_operational_dart_observation_packet(
            generated_at, ROOT
        )
        historical = copy.deepcopy(latest)
        historical_decision = _parse_utc(latest["decision_at"]) - dt.timedelta(days=1)
        historical["decision_at"] = historical_decision.isoformat().replace(
            "+00:00", "Z"
        )
        historical["source_date"] = (
            dt.date.fromisoformat(latest["source_date"]) - dt.timedelta(days=1)
        ).isoformat()
        historical["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in historical.items()
            if key != "packet_sha256"
        })
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            for packet in (historical, latest):
                target = (
                    root / "data" / "observations" / "dart_event_observations"
                    / packet["source_date"]
                    / f"packet-{packet['packet_sha256'][:16]}.json"
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(packet), encoding="utf-8")
            with mock.patch.object(
                MODULE,
                "_validated_dart_observation_packet",
                return_value=latest,
            ) as validator:
                selected = MODULE.load_operational_dart_observation_packet(
                    generated_at, root
                )
            self.assertEqual(selected, latest)
            validator.assert_called_once()
            self.assertEqual(
                validator.call_args.args[0]["packet_sha256"],
                latest["packet_sha256"],
            )

    def test_signal_observation_tamper_and_resign_fails_closed(self):
        result = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "evening", self.dynamic_generated_at, CONTRACT,
            dynamic_report=self.dynamic_report,
        )
        result["signal_observations"]["observations"][0]["ready_status"] = "READY"
        result["packet_sha256"] = MODULE.payload_sha256({
            key: value for key, value in result.items() if key != "packet_sha256"
        })
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError,
            "BRIEFING_SIGNAL_ROW_VALUE_INVALID",
        ):
            MODULE.validate_briefing(result, CONTRACT)

    def test_verified_operational_wildcard_is_visible_without_promotion(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = WILDCARD_FIXTURE.init_repo(root)
            envelope = MODULE.WILDCARD_INTAKE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            result = MODULE.build_briefing(
                empty_ledger(), records(), bindings(),
                "evening", "2026-08-21T02:00:00Z", CONTRACT,
                wildcard_envelopes=[envelope], wildcard_root=root,
            )
            wildcard = result["wildcard_observations"]
            self.assertEqual(
                wildcard["status"],
                "VERIFIED_WILDCARD_OBSERVATIONS_PRESENT_NO_PROMOTION_AUTHORITY",
            )
            self.assertEqual(wildcard["envelope_count"], 1)
            self.assertEqual(wildcard["case_count"], 1)
            self.assertEqual(wildcard["pending_count"], 0)
            self.assertEqual(result["summary"]["wildcard_observation_count"], 1)
            row = wildcard["observations"][0]
            self.assertEqual(row["observation_type"], "EVIDENCE_LINKED_CASE")
            self.assertFalse(row["candidate_eligible"])
            self.assertEqual(row["ready_status"], "NOT_EVALUATED")
            self.assertEqual(row["promotion_status"], "PROMOTION_NOT_AUTHORIZED")
            self.assertIsNone(row["action"])
            self.assertEqual(result["summary"]["new_candidate_count"], 0)

    def test_wildcard_projection_tamper_and_future_envelope_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = WILDCARD_FIXTURE.init_repo(root)
            envelope = MODULE.WILDCARD_INTAKE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            result = MODULE.build_briefing(
                empty_ledger(), records(), bindings(),
                "evening", "2026-08-21T02:00:00Z", CONTRACT,
                wildcard_envelopes=[envelope], wildcard_root=root,
            )
            result["wildcard_observations"]["observations"][0][
                "candidate_eligible"
            ] = True
            result["packet_sha256"] = MODULE.payload_sha256({
                key: value for key, value in result.items() if key != "packet_sha256"
            })
            with self.assertRaisesRegex(
                MODULE.RotationDiscoveryBriefingError,
                "BRIEFING_WILDCARD_DERIVATION_MISMATCH",
            ):
                MODULE.validate_briefing(result, CONTRACT, wildcard_root=root)
            future = MODULE.WILDCARD_INTAKE.build_envelope(
                [relative], head, "2026-08-22T14:00:00Z", root
            )
            with self.assertRaisesRegex(
                MODULE.RotationDiscoveryBriefingError,
                "WILDCARD_ENVELOPE_FROM_FUTURE",
            ):
                MODULE.build_briefing(
                    empty_ledger(), records(), bindings(),
                    "evening", "2026-08-21T02:00:00Z", CONTRACT,
                    wildcard_envelopes=[future], wildcard_root=root,
                )

    def test_operational_loader_selects_latest_revision_per_submission(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = WILDCARD_FIXTURE.init_repo(root)
            target = root / "config" / "rotation_discovery_briefing_contract.json"
            target.write_bytes((ROOT / "config" / target.name).read_bytes())
            WILDCARD_FIXTURE.commit(root, "read model contract", "2026-08-19T13:30:00Z")
            head = WILDCARD_FIXTURE.git(root, "rev-parse", "HEAD")
            first = MODULE.WILDCARD_INTAKE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            second = MODULE.WILDCARD_INTAKE.build_envelope(
                [relative], head, "2026-08-19T15:00:00Z", root
            )
            MODULE.WILDCARD_INTAKE.publish(first, root)
            MODULE.WILDCARD_INTAKE.publish(second, root)
            loaded = MODULE.load_operational_wildcard_envelopes(
                "2026-08-19T16:00:00Z", root
            )
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0]["decision_at_utc"], "2026-08-19T15:00:00Z")

    def test_duplicate_or_mislocated_wildcard_envelope_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            head, relative = WILDCARD_FIXTURE.init_repo(root)
            target = root / "config" / "rotation_discovery_briefing_contract.json"
            target.write_bytes((ROOT / "config" / target.name).read_bytes())
            WILDCARD_FIXTURE.commit(root, "read model contract", "2026-08-19T13:30:00Z")
            head = WILDCARD_FIXTURE.git(root, "rev-parse", "HEAD")
            envelope = MODULE.WILDCARD_INTAKE.build_envelope(
                [relative], head, "2026-08-19T14:00:00Z", root
            )
            with self.assertRaisesRegex(
                MODULE.RotationDiscoveryBriefingError,
                "WILDCARD_ENVELOPE_DUPLICATE",
            ):
                MODULE.build_briefing(
                    empty_ledger(), records(), bindings(),
                    "evening", "2026-08-21T02:00:00Z", CONTRACT,
                    wildcard_envelopes=[envelope, envelope], wildcard_root=root,
                )
            correct = MODULE.WILDCARD_INTAKE.publish(envelope, root)
            wrong = correct.with_name("wildcard-mislocated.json")
            wrong.write_bytes(correct.read_bytes())
            correct.unlink()
            with self.assertRaisesRegex(
                MODULE.RotationDiscoveryBriefingError,
                "WILDCARD_PUBLICATION_LOCATOR_INVALID",
            ):
                MODULE.load_operational_wildcard_envelopes(
                    "2026-08-19T16:00:00Z", root
                )

    def test_tampered_rotation_ledger_and_invalid_discovery_fail_closed(self):
        ledger = observed_ledger()
        ledger["records"][0]["current_p2_state"] = "STRONG"
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "ROTATION_LEDGER_INVALID"
        ):
            MODULE.build_briefing(
                ledger, records(), bindings(),
                "morning", "2026-08-21T02:00:00Z", CONTRACT,
            )
        bad = records()
        bad[0]["taxonomy_version"] = "2.0"
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "DISCOVERY_INPUT_INVALID"
        ):
            MODULE.build_briefing(
                empty_ledger(), bad, bindings(),
                "morning", "2026-08-21T02:00:00Z", CONTRACT,
            )

    def test_future_rotation_and_discovery_evidence_fail_closed(self):
        ledger = observed_ledger()
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "ROTATION_FROM_FUTURE"
        ):
            MODULE.build_briefing(
                ledger, records(), bindings(),
                "morning", "2026-08-19T23:59:59Z", CONTRACT,
            )
        future_proof = DISCOVERY_FIXTURE.evidence(
            source_identity={
                **DISCOVERY_FIXTURE.evidence()["source_identity"],
                "retrieved_at_utc": "2026-08-22T00:00:00Z",
            }
        )
        with self.assertRaisesRegex(
            MODULE.RotationDiscoveryBriefingError, "DISCOVERY_EVIDENCE_FROM_FUTURE"
        ):
            MODULE.build_briefing(
                empty_ledger(), records(),
                DISCOVERY_FIXTURE.bindings(DISCOVERY_FIXTURE.binding(proof=future_proof)),
                "morning", "2026-08-21T02:00:00Z", CONTRACT,
            )

    def test_output_is_deterministic_and_inputs_are_immutable(self):
        ledger = observed_ledger()
        source_records = records()
        source_bindings = bindings()
        before = MODULE.canonical_json([ledger, source_records, source_bindings])
        first = MODULE.build_briefing(
            ledger, source_records, source_bindings,
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        second = MODULE.build_briefing(
            ledger, list(reversed(source_records)), source_bindings,
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        self.assertEqual(MODULE.canonical_json(first), MODULE.canonical_json(second))
        self.assertEqual(
            MODULE.canonical_json([ledger, source_records, source_bindings]), before
        )

    def test_summary_authority_and_digest_tamper_fail_closed(self):
        original = MODULE.build_briefing(
            empty_ledger(), records(), bindings(),
            "morning", "2026-08-21T02:00:00Z", CONTRACT,
        )
        variants = []
        summary = copy.deepcopy(original)
        summary["summary"]["new_candidate_count"] = 1
        variants.append((summary, "BRIEFING_SUMMARY_INVALID"))
        promoted = copy.deepcopy(original)
        promoted["discovery"]["new_candidates"] = ["SNDK"]
        variants.append((promoted, "BRIEFING_DISCOVERY_SUMMARY_INVALID"))
        state = copy.deepcopy(original)
        state["rotation"]["latest_changes"] = [{
            "market": "US", "scope_id": "SCOPE", "entity_id": "ENTITY",
            "as_of_date": "2026-08-20", "structural_bucket_transition": "TOP_TO_TOP",
            "prior_state": None, "current_state": "INVENTED",
            "state_transition": "UNINITIALIZED_TO_INVENTED",
            "record_sha256": "a" * 64, "source_packet_sha256": "b" * 64,
        }]
        state["rotation"]["latest_change_count"] = 1
        state["rotation"]["state_counts"] = {
            "EMERGING": 0, "STRONG": 0, "WEAKENING": 1
        }
        variants.append((state, "BRIEFING_ROTATION_ROW_VALUE_INVALID"))
        case = copy.deepcopy(original)
        case["discovery"]["cases"][0]["promotion_status"] = "PROMOTED"
        variants.append((case, "BRIEFING_DISCOVERY_CASE_VALUE_INVALID"))
        authority = copy.deepcopy(original)
        authority["authority"]["stage_promotion_authorized"] = True
        variants.append((authority, "BRIEFING_IDENTITY_INVALID"))
        digest = copy.deepcopy(original)
        digest["packet_sha256"] = "0" * 64
        variants.append((digest, "BRIEFING_SHA_MISMATCH"))
        for packet, error in variants:
            with self.subTest(error=error), self.assertRaisesRegex(
                MODULE.RotationDiscoveryBriefingError, error
            ):
                MODULE.validate_briefing(packet, CONTRACT)

    def test_cli_is_offline_and_writes_only_outside_repository(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        for prohibited in ("requests", "urllib", "socket", "http", "subprocess", "git"):
            self.assertNotIn(prohibited, imported)

        with tempfile.TemporaryDirectory() as tmp:
            temp = Path(tmp)
            ledger_path = temp / "ledger.json"
            records_path = temp / "records.jsonl"
            bindings_path = temp / "bindings.json"
            ledger_path.write_text(json.dumps(empty_ledger()), encoding="utf-8")
            records_path.write_text(
                "\n".join(json.dumps(row) for row in records()) + "\n",
                encoding="utf-8",
            )
            bindings_path.write_text(json.dumps(bindings()), encoding="utf-8")
            output = temp / "out" / "briefing.json"
            self.assertEqual(
                MODULE.run(
                    ledger_path, records_path, bindings_path,
                    "morning", "2026-08-21T02:00:00Z", output,
                ),
                0,
            )
            forbidden = ROOT / "data" / "rotation_discovery_briefing_test.json"
            self.assertEqual(
                MODULE.run(
                    ledger_path, records_path, bindings_path,
                    "morning", "2026-08-21T02:00:00Z", forbidden,
                ),
                1,
            )
            self.assertFalse(forbidden.exists())


if __name__ == "__main__":
    unittest.main()
