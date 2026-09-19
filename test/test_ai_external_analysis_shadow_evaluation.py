import copy
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ai_shadow", ROOT / "shadow" / "ai_external_analysis_shadow_evaluation.py")
MODULE = importlib.util.module_from_spec(spec); spec.loader.exec_module(MODULE)

def observation():
    return {"market":"US", "symbol":"US:XNAS:TSM", "decision_at":"2026-09-09T01:00:00Z", "source_cutoff_at":"2026-09-09T00:59:59Z", "baseline_rank":1, "baseline_decision":"WATCH", "ai_event_features":[], "shadow_rank_or_annotation":"ANNOTATION_ONLY", "forward_windows":[1,5], "realized_return":None, "drawdown":None, "turnover_cost":None, "regime":"UNKNOWN", "data_freshness":"UNKNOWN", "model_sha256":"a"*64, "prompt_sha256":"b"*64, "source_sha256":"c"*64, "private_daily_receipt_sha256":"d"*64, "private_daily_output_sha256":"e"*64}

class Tests(unittest.TestCase):
    def test_unbound_extension_preserves_baseline_and_has_no_authority(self):
        row = MODULE.build_record("d"*64, observation(), "2026-09-09T01:00:01Z", 1)
        self.assertEqual(row["stage3_binding_status"], "STAGE3_CONTRACT_UNBOUND")
        self.assertEqual(row["observation"]["baseline_decision"], "WATCH")
        self.assertTrue(all(not value for key,value in row["authority"].items() if key != "supplemental_shadow_evaluation"))
    def test_no_sample_fabricates_no_metrics(self):
        result = MODULE.compare([])
        self.assertEqual(result["status"], "NOT_ENOUGH_OBSERVATIONS")
        self.assertTrue(all(value is None for value in result["metrics"].values()))
        portal = MODULE.portal_aggregate(result, "2026-09-09T02:00:00Z")
        self.assertEqual(portal["sufficiency_status"], "NOT_ENOUGH_OBSERVATIONS")
        self.assertTrue(all(value is None for value in portal["incremental_metrics"].values()))
    def test_semantic_tamper_is_rejected_even_after_rehash(self):
        original = MODULE.build_record("d"*64, observation(), "2026-09-09T01:00:01Z", 1)
        for mutation in (lambda row: row["authority"].update(order=True), lambda row: row.update(stage3_binding_status="BOUND"), lambda row: row["observation"].update(realized_return=12), lambda row: row["observation"].update(ai_event_features=[{"raw":"private text"}])):
            row = copy.deepcopy(original); mutation(row)
            row["record_sha256"] = MODULE.payload_sha256({key:value for key,value in row.items() if key != "record_sha256"})
            with self.assertRaises(MODULE.AIExternalAnalysisShadowError): MODULE.compare([row])

    def test_chain_duplicate_and_nonboolean_count_rejected(self):
        first = MODULE.build_record("d"*64, observation(), "2026-09-09T01:00:01Z", 1)
        second = MODULE.build_record("d"*64, observation(), "2026-09-09T01:00:02Z", 2, first["record_sha256"])
        with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError, "DUPLICATE_OBSERVATION"): MODULE.compare([first, second])
        with self.assertRaises(MODULE.AIExternalAnalysisShadowError): MODULE.compare([second])
        with self.assertRaises(MODULE.AIExternalAnalysisShadowError): MODULE.compare([], True)

    def test_portal_count_must_match_revalidated_records(self):
        result = MODULE.compare([]); result.update(observation_count=999, status="BOUND_SAMPLE_REQUIRED")
        with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError,"COMPARISON_UNSUPPORTED_CLAIM"): MODULE.portal_aggregate(result,"2026-09-09T02:00:00Z")
        first = MODULE.build_record("d"*64, observation(), "2026-09-09T01:00:01Z", 1)
        result = MODULE.compare([first])
        self.assertEqual(MODULE.portal_aggregate(result,"2026-09-09T02:00:00Z",records=[first])["observation_count"],1)
        with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError,"EVALUATION_BEFORE_RECORD"): MODULE.portal_aggregate(result,"2000-01-01T00:00:00Z",records=[first])
        item=observation(); item["model_sha256"]="f"*64
        second=MODULE.build_record("d"*64,item,"2026-09-09T01:00:02Z",2,first["record_sha256"])
        with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError,"DUPLICATE_OBSERVATION"): MODULE.compare([first,second])

    def test_portal_refuses_arbitrary_metrics_and_raw_fields(self):
        for key, value in (("hit_rate", 1.0), ("raw_source", "private text"), ("by_market", {"US": 4})):
            result = MODULE.compare([]); result["metrics"][key] = value
            with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError, "COMPARISON_UNSUPPORTED_CLAIM"): MODULE.portal_aggregate(result, "2026-09-09T02:00:00Z")
        self.assertTrue(MODULE.portal_aggregate(MODULE.compare([]), "2026-09-09T02:00:00Z")["notUsedInScore"])

    def test_invalid_calendar_record_time_and_boolean_rank(self):
        for updates in ({"decision_at":"2026-02-30T00:00:00Z"}, {"baseline_rank":True}, {"shadow_rank_or_annotation":"BUY"}, {"forward_windows":[True]}):
            item = observation(); item.update(updates)
            with self.assertRaises(MODULE.AIExternalAnalysisShadowError): MODULE.build_record("d"*64,item,"2026-09-09T01:00:01Z",1)
        with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError,"RECORD_BEFORE_DECISION"): MODULE.build_record("d"*64,observation(),"2026-09-09T00:00:00Z",1)

    def test_future_source_is_rejected(self):
        row=observation(); row["source_cutoff_at"]="2026-09-09T01:00:01Z"
        with self.assertRaisesRegex(MODULE.AIExternalAnalysisShadowError,"SOURCE_AFTER_DECISION"):
            MODULE.build_record("d"*64,row,"2026-09-09T01:00:02Z",1)

if __name__ == "__main__": unittest.main()
