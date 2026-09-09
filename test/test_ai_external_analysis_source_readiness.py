import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

import yaml


ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "4ac30314ba88b10353bdc7aa76f79f398591693e"
MODULE_PATH = ROOT / "shadow" / "ai_external_analysis_source_readiness.py"
SPEC = importlib.util.spec_from_file_location("ai_external_source_readiness", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AiExternalAnalysisSourceReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = SOURCE_COMMIT
        cls.evaluated_at = "2026-09-09T15:00:00Z"
        cls.packet = MODULE.build_packet(ROOT, cls.commit, cls.evaluated_at)

    def test_real_retained_sources_are_references_not_missing_or_model_inputs(self):
        packet = self.packet
        self.assertEqual(packet["status"], "DATA_QUALIFICATION_WAIT")
        self.assertFalse(packet["allSixConditionsReady"])
        self.assertFalse(packet["modelSourceInferenceAuthorized"])
        self.assertEqual(packet["stage3InputSources"], [])
        self.assertEqual(len(packet["retainedSourceRefs"]), 7)
        self.assertEqual(
            packet["runStates"]["SEC"]["newContentState"],
            "NO_NEW_DOWNLOAD_RETAINED_CONTENT_REUSED",
        )
        self.assertEqual(
            packet["runStates"]["SEC"]["missingState"],
            "NOT_MISSING_RETAINED_REFERENCE_PRESENT",
        )
        self.assertEqual(
            packet["runStates"]["DART"]["newContentState"],
            "NO_NEW_RELEVANT_FILING",
        )
        self.assertEqual(
            packet["runStates"]["DART"]["missingState"],
            "NOT_MISSING_SUCCESSFUL_EMPTY_RESULT",
        )

    def test_real_sources_preserve_date_only_unknown_freshness_and_exact_fact_ids(self):
        sources = self.packet["retainedSourceRefs"]
        self.assertTrue(all(row["eventAtUtc"] is None for row in sources))
        self.assertTrue(all(row["eventTimePrecision"] == "DATE_ONLY" for row in sources))
        self.assertTrue(all(row["freshThroughUtc"] is None for row in sources))
        self.assertTrue(all(row["freshnessStatus"] == "UNKNOWN_NO_SOURCE_OWNER_POLICY" for row in sources))
        self.assertTrue(all(row["availableAtUtc"] == "2026-09-08T21:45:30Z" for row in sources))
        self.assertTrue(all(len(row["availableAtBasis"]) == 2 for row in sources))
        sec_collector = MODULE._git_blob(ROOT, self.commit, "collectors/sec.py").decode()
        self.assertNotIn("acceptanceDateTime", sec_collector)
        board = next(row for row in sources if row["identity"]["accession"] == "0001046179-26-000536")
        self.assertEqual(len(board["factRefs"]), 3)
        for fact in board["factRefs"]:
            identity = {
                "sourceId": fact["sourceId"],
                "sourceManifestSha256": fact["sourceManifestSha256"],
                "extractorVersion": fact["extractorVersion"],
                "index": fact["index"],
                "factSha256": fact["factSha256"],
            }
            self.assertEqual(
                fact["factId"],
                f"SEC_EXTRACTED_FACT_REF_SHA256_{MODULE.payload_sha256(identity)}",
            )

    def test_condition_boundary_distinguishes_adapter_progress_from_exit_gate(self):
        conditions = {row["id"]: row for row in self.packet["conditions"]}
        self.assertTrue(conditions["market_symbol_identity"]["ready"])
        self.assertFalse(conditions["source_owner_binding"]["ready"])
        self.assertEqual(
            conditions["source_owner_binding"]["status"],
            "REFERENCE_PINS_ONLY_NOT_ADMISSION",
        )
        self.assertTrue(self.packet["ownerReferencesBound"])
        self.assertTrue(conditions["original_or_approved_excerpt_hash"]["ready"])
        self.assertFalse(conditions["latest_actual_source"]["ready"])
        self.assertFalse(conditions["event_available_fresh_through"]["ready"])
        self.assertFalse(conditions["continuous_missing_delay_state"]["ready"])
        self.assertIsNone(self.packet["runStates"]["SEC"]["consecutiveFailureCount"])

    def test_material_index_admission_and_backfilled_ledger_stay_separate_from_readiness(self):
        index = self.packet["latestMaterialSourceIndex"]
        self.assertEqual(index["SEC"]["status"], "OK_RETAINED_REUSED")
        self.assertEqual(
            index["SEC"]["lastMaterialSource"]["identity"]["accession"],
            "0001046179-26-000552",
        )
        self.assertEqual(index["DART"]["status"], "OK_EMPTY")
        self.assertEqual(
            index["DART"]["lastMaterialSource"]["identity"]["receiptNumber"],
            "20260831800137",
        )
        receipt = self.packet["sourceOwnerAdmissionReceipt"]
        self.assertEqual(receipt["receiptOrigin"], "BACKFILLED_VERIFIED_OBSERVATION_ONLY")
        self.assertFalse(receipt["admissionAuthorized"])
        self.assertEqual(receipt["status"], "REFERENCE_VALIDATED_NOT_ADMITTED")
        ledger = self.packet["runLedger"]
        self.assertGreater(ledger["summary"]["recordCount"], 0)
        self.assertTrue(ledger["summary"]["countsAreLowerBounds"])
        self.assertTrue(all(
            row["recordOrigin"] == "BACKFILLED_VERIFIED_OBSERVATION_ONLY"
            for row in ledger["records"]
        ))
        MODULE.validate_shadow_source_match(self.packet)

    def test_natural_mechanism_progress_does_not_complete_time_or_history_conditions(self):
        conditions = {
            row["id"]: row
            for row in MODULE._condition_rows(
                self.packet["retainedSourceRefs"], "NATURAL_SOURCE_OWNER_EMITTED"
            )
        }
        self.assertTrue(conditions["latest_actual_source"]["ready"])
        self.assertTrue(conditions["source_owner_binding"]["ready"])
        self.assertFalse(conditions["event_available_fresh_through"]["ready"])
        self.assertFalse(conditions["continuous_missing_delay_state"]["ready"])
        self.assertFalse(all(row["ready"] for row in conditions.values()))

    def test_provider_acceptance_mechanism_does_not_invent_missing_event_time(self):
        metadata = {"stocks": {"TSM": {"filings_recent": [{
            "accession": "0001046179-26-000552",
            "acceptanceDateTime": "2026-09-01T20:59:00Z",
        }]}}}
        event = MODULE._provider_event_at(metadata, "TSM", "0001046179-26-000552")
        self.assertEqual(event, "2026-09-01T20:59:00Z")
        available, basis = MODULE._available_bound(
            event, "2026-09-01T21:00:49Z", "2026-09-08T21:45:30Z"
        )
        self.assertEqual(available, "2026-09-08T21:45:30Z")
        self.assertEqual(len(basis), 3)
        without = copy.deepcopy(metadata)
        without["stocks"]["TSM"]["filings_recent"][0].pop("acceptanceDateTime")
        self.assertIsNone(
            MODULE._provider_event_at(without, "TSM", "0001046179-26-000552")
        )

    def test_nominal_schedule_handles_closed_days_without_claiming_future_coverage(self):
        after_final_thursday = MODULE._utc("2026-09-10T21:35:00Z", "TEST_TIME")
        next_due = MODULE._next_nominal_due(
            after_final_thursday,
            ["55 20 * * 0-4", "15 21 * * 0-4", "35 21 * * 0-4"],
        )
        self.assertEqual(next_due, "2026-09-13T20:55:00Z")
        coverage = self.packet["freshnessCoverage"]
        self.assertIsNone(coverage["freshThroughUtc"])
        self.assertEqual(coverage["futureCoverageStatus"], "UNKNOWN_NO_VERIFIED_FUTURE_POLL")
        self.assertEqual(
            coverage["nominalSchedule"]["status"],
            "BEST_EFFORT_NOMINAL_ONLY_NOT_DELIVERY_GUARANTEE",
        )

    def test_resigned_authority_or_time_tampering_is_rejected(self):
        for mutate in (
            lambda value: value.update(modelSourceInferenceAuthorized=True),
            lambda value: value["retainedSourceRefs"][0].update(eventAtUtc="2026-09-01T00:00:00Z"),
            lambda value: value["retainedSourceRefs"][0].update(freshThroughUtc="2099-01-01T00:00:00Z"),
            lambda value: value["runStates"]["SEC"].update(consecutiveFailureCount=0),
        ):
            changed = copy.deepcopy(self.packet)
            mutate(changed)
            unsigned = copy.deepcopy(changed)
            unsigned.pop("packetSha256")
            changed["packetSha256"] = MODULE.payload_sha256(unsigned)
            with self.assertRaisesRegex(
                MODULE.SourceReadinessError,
                "SEMANTIC_TAMPER|SHADOW_SOURCE_EXACT_MATCH",
            ):
                MODULE.validate_packet(changed, ROOT, self.commit, self.evaluated_at)

        changed = copy.deepcopy(self.packet)
        changed["ownerReferencesBound"] = 1
        unsigned = copy.deepcopy(changed)
        unsigned.pop("packetSha256")
        changed["packetSha256"] = MODULE.payload_sha256(unsigned)
        with self.assertRaisesRegex(MODULE.SourceReadinessError, "SEMANTIC_TAMPER"):
            MODULE.validate_packet(changed, ROOT, self.commit, self.evaluated_at)

    def test_full_sha_and_pit_cutoff_are_required(self):
        with self.assertRaisesRegex(MODULE.SourceReadinessError, "FULL_SHA"):
            MODULE.build_packet(ROOT, "HEAD", self.evaluated_at)
        with self.assertRaisesRegex(MODULE.SourceReadinessError, "FROM_FUTURE"):
            MODULE.build_packet(ROOT, self.commit, "2026-09-01T00:00:00Z")

    def test_contract_is_exact_and_authority_is_closed(self):
        contract = MODULE.load_contract()
        changed = copy.deepcopy(contract)
        changed["time_semantics"]["fresh_through"] = "2026-09-10T00:00:00Z"
        with self.assertRaisesRegex(MODULE.SourceReadinessError, "CONTRACT_TAMPER"):
            MODULE.validate_contract(changed)
        changed = copy.deepcopy(contract)
        changed["authority"]["model_source_inference_authorized"] = 1
        with self.assertRaisesRegex(MODULE.SourceReadinessError, "CONTRACT_TAMPER"):
            MODULE.validate_contract(changed)
        self.assertEqual(
            {key for key, value in contract["authority"].items() if value},
            {"reference_recording_authorized"},
        )

    def test_failed_run_is_distinct_from_successful_empty_or_reused_content(self):
        failed = {
            "run_status": "FAILED",
            "records": [{"operation": "failed", "content_status": "PENDING"}],
            "counts": {"captured": 0, "failed": 1, "not_applicable": 0, "skipped": 0},
        }
        state = MODULE._run_state(
            "SEC", failed, retained_count=0, content_rows_validated=True
        )
        self.assertEqual(state["newContentState"], "PARTIAL_OR_FAILED_COLLECTION")
        self.assertEqual(state["collectionFailureState"], "FAILED_IN_CURRENT_RUN")
        self.assertEqual(state["missingState"], "UNKNOWN_COLLECTION_FAILED")
        self.assertEqual(state["stalenessState"], "UNKNOWN_NO_SOURCE_OWNER_POLICY")
        self.assertEqual(state["consecutiveFailureState"], "UNKNOWN_HISTORY_NOT_BOUND")
        self.assertIsNone(state["consecutiveFailureCount"])

    def test_run_counts_require_nonnegative_plain_integers_and_dict_records(self):
        base = {
            "run_status": "OK",
            "records": [],
            "counts": {"captured": 0, "failed": 0, "not_applicable": 0, "skipped": 0},
        }
        for field, value in (("captured", True), ("failed", -1)):
            changed = copy.deepcopy(base)
            changed["counts"][field] = value
            with self.assertRaisesRegex(MODULE.SourceReadinessError, "COUNTS_SHAPE_INVALID"):
                MODULE._validated_counts(changed, "SEC")
        changed = copy.deepcopy(base)
        changed["records"] = ["not-a-record"]
        with self.assertRaisesRegex(MODULE.SourceReadinessError, "RUN_RECORD_INVALID"):
            MODULE._validated_counts(changed, "SEC")

    def test_unvalidated_dart_content_is_not_called_no_new_filing(self):
        content_present = {
            "run_status": "OK",
            "records": [{"operation": "skipped", "content_status": "OK"}],
            "counts": {"captured": 0, "failed": 0, "not_applicable": 0, "skipped": 1},
        }
        state = MODULE._run_state(
            "DART", content_present, retained_count=0, content_rows_validated=False
        )
        self.assertEqual(state["newContentState"], "UNKNOWN_UNVALIDATED_DART_CONTENT")
        self.assertEqual(state["missingState"], "UNKNOWN_UNVALIDATED_DART_CONTENT")

    def test_newly_captured_sec_content_is_validated_and_referenced(self):
        run, _ = MODULE._git_json(ROOT, self.commit, "data/latest_sec_content.json")
        changed = copy.deepcopy(run)
        target = next(
            row for row in changed["records"]
            if row.get("filing_identity", {}).get("accession") == "0001046179-26-000552"
        )
        target["operation"] = "captured"
        target.pop("skip_reason")
        metadata, _ = MODULE._git_json(ROOT, self.commit, "data/latest_sec.json")
        sources = MODULE._sec_sources(
            ROOT,
            self.commit,
            metadata,
            changed,
            MODULE._utc(self.evaluated_at, "TEST_TIME_INVALID"),
        )
        self.assertIn("0001046179-26-000552", {
            row["identity"]["accession"] for row in sources
        })

    def test_fact_identity_is_bound_to_source_and_manifest(self):
        manifest = {"extractor_version": "sec_filing_content/1", "extracted": [{"label": "same"}]}
        first = MODULE._fact_refs(manifest, "SEC_A", "a" * 64)[0]
        second = MODULE._fact_refs(manifest, "SEC_B", "b" * 64)[0]
        self.assertEqual(first["factSha256"], second["factSha256"])
        self.assertNotEqual(first["factId"], second["factId"])

    def test_executed_owner_validator_must_match_source_commit(self):
        with mock.patch.object(Path, "read_bytes", return_value=b"tampered"):
            with self.assertRaisesRegex(MODULE.SourceReadinessError, "EXECUTED_OWNER_PIN_MISMATCH"):
                MODULE._verify_executed_owner_pins(ROOT, self.commit)

    def test_git_reads_disable_lazy_fetch(self):
        with mock.patch.object(MODULE.subprocess, "check_output", return_value="ok") as call:
            self.assertEqual(MODULE._run_git(ROOT, ["status"]), "ok")
        self.assertEqual(call.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")

    def test_content_addressed_observation_is_idempotent_and_collision_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = MODULE.write_observation(self.packet, root)
            second = MODULE.write_observation(self.packet, root)
            self.assertEqual(first, second)
            index = json.loads((root / "index.json").read_text())
            unsigned = copy.deepcopy(index)
            claimed = unsigned.pop("indexSha256")
            self.assertEqual(claimed, MODULE.payload_sha256(unsigned))
            self.assertEqual(len(index["records"]), 1)
            readiness = Path(first["readinessPath"])
            readiness.write_text("tampered")
            with self.assertRaisesRegex(MODULE.SourceReadinessError, "IMMUTABLE_OUTPUT_COLLISION"):
                MODULE.write_observation(self.packet, root)

    def test_workflow_commits_this_run_data_before_exact_commit_receipt(self):
        workflow = yaml.safe_load((ROOT / ".github/workflows/collect.yml").read_text())
        steps = workflow["jobs"]["collect"]["steps"]
        names = [step.get("name") for step in steps]
        data_index = names.index("Commit data")
        receipt_index = names.index("Record AI Shadow source readiness (P10-07)")
        self.assertGreater(receipt_index, data_index)
        data_script = steps[data_index]["run"]
        receipt_script = steps[receipt_index]["run"]
        self.assertIn("git commit", data_script)
        self.assertIn("SOURCE_COMMIT=$(git rev-parse HEAD)", receipt_script)
        self.assertLess(receipt_script.index("SOURCE_COMMIT=$(git rev-parse HEAD)"), receipt_script.index("python3 shadow/ai_external_analysis_source_readiness.py"))
        self.assertLess(receipt_script.index("python3 shadow/ai_external_analysis_source_readiness.py"), receipt_script.index("git commit"))
        self.assertLess(receipt_script.index("git commit"), receipt_script.index("git push"))
        module_source = MODULE_PATH.read_text()
        self.assertIn('observation_origin="NATURAL_SOURCE_OWNER_EMITTED"', module_source)

    def test_exact_commit_read_includes_this_run_mutation_only_after_commit(self):
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary)
            subprocess.run(["git", "init", "-q", str(repo)], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
            subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.com"], check=True)
            target = repo / "latest.json"
            target.write_text('{"run":"prior"}\n')
            subprocess.run(["git", "-C", str(repo), "add", "latest.json"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "prior"], check=True)
            prior = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            target.write_text('{"run":"this-run"}\n')
            subprocess.run(["git", "-C", str(repo), "add", "latest.json"], check=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-qm", "this run"], check=True)
            current = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
            self.assertEqual(MODULE._git_json(repo, prior, "latest.json")[0]["run"], "prior")
            self.assertEqual(MODULE._git_json(repo, current, "latest.json")[0]["run"], "this-run")


if __name__ == "__main__":
    unittest.main()
