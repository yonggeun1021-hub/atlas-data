import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "shadow" / "ai_external_analysis_source_readiness.py"
SPEC = importlib.util.spec_from_file_location("ai_external_source_readiness", MODULE_PATH)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class AiExternalAnalysisSourceReadinessTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.commit = subprocess.check_output(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"], text=True
        ).strip()
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
        board = next(row for row in sources if row["identity"]["accession"] == "0001046179-26-000536")
        self.assertEqual(len(board["factRefs"]), 3)
        for fact in board["factRefs"]:
            self.assertEqual(fact["factId"], f"SEC_EXTRACTED_FACT_SHA256_{fact['factSha256']}")

    def test_condition_boundary_distinguishes_adapter_progress_from_exit_gate(self):
        conditions = {row["id"]: row for row in self.packet["conditions"]}
        self.assertTrue(conditions["market_symbol_identity"]["ready"])
        self.assertTrue(conditions["source_owner_binding"]["ready"])
        self.assertTrue(conditions["original_or_approved_excerpt_hash"]["ready"])
        self.assertFalse(conditions["latest_actual_source"]["ready"])
        self.assertFalse(conditions["event_available_fresh_through"]["ready"])
        self.assertFalse(conditions["continuous_missing_delay_state"]["ready"])
        self.assertIsNone(self.packet["runStates"]["SEC"]["consecutiveFailureCount"])

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
        state = MODULE._run_state("SEC", failed, retained_count=0)
        self.assertEqual(state["newContentState"], "PARTIAL_OR_FAILED_COLLECTION")
        self.assertEqual(state["collectionFailureState"], "FAILED_IN_CURRENT_RUN")
        self.assertEqual(state["missingState"], "UNKNOWN_COLLECTION_FAILED")
        self.assertEqual(state["stalenessState"], "UNKNOWN_NO_SOURCE_OWNER_POLICY")
        self.assertEqual(state["consecutiveFailureState"], "UNKNOWN_HISTORY_NOT_BOUND")
        self.assertIsNone(state["consecutiveFailureCount"])


if __name__ == "__main__":
    unittest.main()
