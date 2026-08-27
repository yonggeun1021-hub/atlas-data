"""P3-05 retained SEC evidence population and briefing wiring regression."""
from __future__ import annotations

import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "discovery" / "business_acceleration_population.py"
ORCHESTRATOR_PATH = ROOT / "briefing" / "daily_orchestrator.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "collect.yml"
DECISION_AT = "2026-08-25T02:30:00Z"
ACCESSIONS = (
    "0001046179-26-000367",
    "0001046179-26-000447",
    "0001046179-26-000471",
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


POPULATION = load_module("test_business_acceleration_population_module", MODULE_PATH)


def unsigned_hash(packet: dict) -> str:
    value = copy.deepcopy(packet)
    value.pop("population_sha256", None)
    return POPULATION.payload_sha256(value)


class RealPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = POPULATION.build_population(decision_at=DECISION_AT)

    def test_real_population_reuses_three_retained_official_reports(self):
        packet = self.packet
        observation_packet = POPULATION.OFFICIAL_RELEASE.build_packet(
            data_root=ROOT / "data", decision_at=DECISION_AT
        )
        self.assertEqual(packet["status"], POPULATION.STATUS_POPULATED)
        self.assertEqual(packet["schema_version"], "business_acceleration_population/2")
        self.assertEqual(
            packet["source_observation_packet_sha256"],
            observation_packet["packet_sha256"],
        )
        self.assertEqual(
            packet["source_observation_schema_version"],
            POPULATION.OFFICIAL_RELEASE.SCHEMA_VERSION,
        )
        self.assertEqual(packet["selected_months"], ["2026-05", "2026-06", "2026-07"])
        self.assertEqual([row["accession"] for row in packet["source_reports"]], list(ACCESSIONS))
        self.assertEqual(packet["summary"], {
            "eligible_report_count": 3,
            "selected_report_count": 3,
            "series_count": 2,
            "case_count": 1,
        })
        self.assertEqual(POPULATION.validate_population(packet), packet)

    def test_real_monthly_and_cumulative_results_are_exact(self):
        results = {
            row["series_id"]: row
            for row in self.packet["radar_packet"]["series_results"]
        }
        monthly = results["TSM_MONTHLY_REVENUE_YOY_SEC"]
        cumulative = results["TSM_CUMULATIVE_REVENUE_YOY_SEC"]
        self.assertEqual(monthly["values_pct"], [
            "30.100000000000", "67.900000000000", "44.700000000000"
        ])
        self.assertEqual(monthly["pattern"], "LATEST_STEP_NOT_UP")
        self.assertFalse(monthly["radar_case_created"])
        self.assertEqual(cumulative["values_pct"], [
            "30.000000000000", "35.600000000000", "37.000000000000"
        ])
        self.assertEqual(cumulative["pattern"], "TWO_STEP_ACCELERATION_OBSERVED")
        self.assertTrue(cumulative["radar_case_created"])

    def test_real_case_never_becomes_candidate_stage_action_or_trade(self):
        case = self.packet["radar_packet"]["cases"][0]
        self.assertEqual(case["importance"], "UNRATIFIED")
        self.assertIsNone(case["candidate_rank"])
        self.assertFalse(case["candidate_eligible"])
        self.assertIsNone(case["stage_transition"])
        self.assertIsNone(case["action"])
        authority = self.packet["authority"]
        self.assertTrue(authority["radar_case_recording_only"])
        for key, value in authority.items():
            if key != "radar_case_recording_only":
                self.assertFalse(value, key)
        self.assertFalse(self.packet["radar_packet"]["authority"]["trading_authorized"])

    def test_every_source_sha_is_rederived_from_committed_gzip_bytes(self):
        for report in self.packet["source_reports"]:
            manifest_path = ROOT / report["manifest_path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            primary = next(row for row in manifest["documents"] if row["kind"] == "primary")
            with gzip.open(manifest_path.parent / f"{primary['document_name']}.gz", "rb") as handle:
                raw = handle.read()
            self.assertEqual(hashlib.sha256(raw).hexdigest(), report["source_sha256"])
            self.assertEqual(len(raw), report["source_bytes"])
            POPULATION.OFFICIAL_RELEASE.SEC.validate_manifest(
                manifest, raw_by_name={primary["document_name"]: raw}
            )

    def test_retained_parser_rederives_each_published_observation(self):
        observations = []
        for accession in ACCESSIONS:
            manifest_path = ROOT / "data" / "sec_content" / "TSM" / accession / "_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            primary = next(row for row in manifest["documents"] if row["kind"] == "primary")
            with gzip.open(manifest_path.parent / f"{primary['document_name']}.gz", "rb") as handle:
                raw = handle.read()
            observations.append(
                POPULATION.OFFICIAL_RELEASE.TSMC.parse_retained_monthly_report(
                    manifest, raw
                )["observation"]
            )
        self.assertEqual(
            [row["monthly_yoy_pct_published"] for row in observations],
            ["30.1", "67.9", "44.7"],
        )
        self.assertEqual(
            [row["cumulative_yoy_pct_published"] for row in observations],
            ["30.0", "35.6", "37.0"],
        )

    def test_population_scope_and_unresolved_boundaries_are_honest(self):
        self.assertEqual(self.packet["scope"], "TSM_SEC_MONTHLY_REVENUE_ONLY")
        boundaries = self.packet["radar_packet"]["unresolved_boundaries"]
        self.assertIn("COMPLETE_CROSS_COMPANY_EVIDENCE_NETWORK_UNAVAILABLE", boundaries)
        self.assertIn("LIVE_RADAR_POPULATION_PARTIAL_TSM_SEC_ONLY", boundaries)
        self.assertEqual(self.packet["radar_packet"]["policy_status"]["candidate_ranking"], "UNRATIFIED")


class FailClosedPopulationTests(unittest.TestCase):
    def _copy_data(self, target_root: Path) -> Path:
        base = target_root / "data" / "sec_content" / "TSM"
        base.mkdir(parents=True)
        source = ROOT / "data" / "sec_content" / "TSM"
        for accession in ACCESSIONS:
            shutil.copytree(source / accession, base / accession)
        return target_root / "data"

    def test_before_backfill_availability_is_insufficient_not_synthetic(self):
        packet = POPULATION.build_population(decision_at="2026-08-25T02:14:59Z")
        self.assertEqual(packet["status"], POPULATION.STATUS_INSUFFICIENT)
        self.assertEqual(packet["summary"]["eligible_report_count"], 1)
        self.assertEqual(packet["summary"]["series_count"], 0)
        self.assertEqual(packet["summary"]["case_count"], 0)
        self.assertIsNone(packet["radar_packet"])

    def test_duplicate_target_month_authority_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            data = self._copy_data(repo)
            shutil.copytree(
                data / "sec_content" / "TSM" / ACCESSIONS[0],
                data / "sec_content" / "TSM" / "duplicate-month",
            )
            with self.assertRaisesRegex(
                POPULATION.BusinessAccelerationPopulationError,
                "OFFICIAL_RELEASE_OBSERVATION_INVALID:MONTHLY_PERIOD_AMBIGUOUS",
            ):
                POPULATION.build_population(
                    decision_at=DECISION_AT, repo_root=repo, data_root=data
                )

    def test_corrupted_retained_raw_bytes_fail_before_radar(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            data = self._copy_data(repo)
            manifest_path = data / "sec_content" / "TSM" / ACCESSIONS[0] / "_manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            primary = next(row for row in manifest["documents"] if row["kind"] == "primary")
            raw_path = manifest_path.parent / f"{primary['document_name']}.gz"
            with gzip.open(raw_path, "rb") as handle:
                raw = handle.read()
            raw_path.write_bytes(gzip.compress(raw + b"tamper"))
            with self.assertRaisesRegex(
                POPULATION.BusinessAccelerationPopulationError,
                "OFFICIAL_RELEASE_OBSERVATION_INVALID:SEC_MANIFEST_INVALID",
            ):
                POPULATION.build_population(
                    decision_at=DECISION_AT, repo_root=repo, data_root=data
                )

    def test_self_rehashed_packet_tamper_is_rebuilt_and_rejected(self):
        packet = POPULATION.build_population(decision_at=DECISION_AT)
        packet["summary"]["case_count"] = 0
        packet["population_sha256"] = unsigned_hash(packet)
        with self.assertRaisesRegex(
            POPULATION.BusinessAccelerationPopulationError,
            "POPULATION_REBUILD_MISMATCH",
        ):
            POPULATION.validate_population(packet)

    def test_invalid_retrieval_timestamp_is_rejected_by_canonical_manifest_validator(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            data = self._copy_data(repo)
            path = data / "sec_content" / "TSM" / ACCESSIONS[0] / "_manifest.json"
            manifest = json.loads(path.read_text(encoding="utf-8"))
            manifest["retrieved_at_utc"] = "not-time"
            path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(
                POPULATION.BusinessAccelerationPopulationError,
                "OFFICIAL_RELEASE_OBSERVATION_INVALID:MANIFEST_RETRIEVED_AT_INVALID",
            ):
                POPULATION.build_population(
                    decision_at=DECISION_AT, repo_root=repo, data_root=data
                )


class PublicationAndWiringTests(unittest.TestCase):
    def test_content_addressed_publication_is_append_only_and_idempotent(self):
        packet = POPULATION.build_population(decision_at=DECISION_AT)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "observations"
            first, created = POPULATION.publish_append_only(out_root=out, packet=packet)
            self.assertTrue(created)
            second, created = POPULATION.publish_append_only(out_root=out, packet=packet)
            self.assertFalse(created)
            self.assertEqual(first, second)
            first.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                POPULATION.BusinessAccelerationPopulationError,
                "CONTENT_ADDRESSED_PACKET_DRIFT",
            ):
                POPULATION.publish_append_only(out_root=out, packet=packet)

    def test_adapter_reuses_canonical_modules_and_has_no_provider_call(self):
        text = MODULE_PATH.read_text(encoding="utf-8")
        self.assertIn("OFFICIAL_RELEASE.build_packet", text)
        self.assertIn("OFFICIAL_RELEASE.validate_packet", text)
        self.assertIn("RADAR.build_packet", text)
        self.assertNotIn("SEC.validate_manifest", text)
        self.assertNotIn("TSM.parse_retained_monthly_report", text)
        for forbidden in ("requests.", "urllib.request", "C4.get(", "run_probe(", "curl "):
            self.assertNotIn(forbidden, text)

    def test_source_observation_lineage_tamper_is_independently_rejected(self):
        packet = POPULATION.build_population(decision_at=DECISION_AT)
        packet["source_observation_packet_sha256"] = "0" * 64
        packet["population_sha256"] = unsigned_hash(packet)
        with self.assertRaisesRegex(
            POPULATION.BusinessAccelerationPopulationError,
            "POPULATION_REBUILD_MISMATCH",
        ):
            POPULATION.validate_population(packet)

    def test_p4_04_production_validator_is_called_before_radar(self):
        original = POPULATION.OFFICIAL_RELEASE.validate_packet
        with mock.patch.object(
            POPULATION.OFFICIAL_RELEASE,
            "validate_packet",
            wraps=original,
        ) as validator:
            packet = POPULATION.build_population(decision_at=DECISION_AT)
        self.assertEqual(validator.call_count, 1)
        self.assertEqual(
            validator.call_args.kwargs["data_root"],
            ROOT / "data",
        )
        self.assertEqual(packet["status"], POPULATION.STATUS_POPULATED)

    def test_daily_collect_population_is_after_sec_capture_and_before_read_model(self):
        workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
        populate = workflow.index("Populate Business Acceleration Radar (P3-05)")
        sec_capture = workflow.index("Capture SEC filing content (P4-02)")
        read_model = workflow.index("Build briefing read model (P0-03)")
        self.assertLess(sec_capture, populate)
        self.assertLess(populate, read_model)
        block = workflow[populate:read_model]
        self.assertIn("business_acceleration_population.py", block)
        self.assertNotIn("curl ", block)
        self.assertNotIn("workflow_dispatch", block)

    def test_daily_orchestrator_exposes_real_case_without_action_authority(self):
        orchestrator = load_module("test_daily_orchestrator_for_p305", ORCHESTRATOR_PATH)
        row = orchestrator.build_business_acceleration_status(DECISION_AT)
        self.assertEqual(row["component_id"], "BUSINESS_ACCELERATION")
        self.assertEqual(row["status"], "PENDING")
        self.assertEqual(row["packet"]["summary"]["case_count"], 1)
        self.assertFalse(row["packet"]["radar_packet"]["cases"][0]["candidate_eligible"])
        self.assertFalse(row["authority"]["trading_authorized"])

    def test_daily_renderer_discloses_scope_pattern_and_candidate_boundary(self):
        orchestrator = load_module("test_daily_orchestrator_render_for_p305", ORCHESTRATOR_PATH)
        row = orchestrator.build_business_acceleration_status(DECISION_AT)
        rendered = "\n".join(orchestrator._format_component_detail(row))
        self.assertIn("scope=TSM_SEC_MONTHLY_REVENUE_ONLY", rendered)
        self.assertIn("pattern=TWO_STEP_ACCELERATION_OBSERVED", rendered)
        self.assertIn("candidate_eligible=False", rendered)
        self.assertNotIn("BUY", rendered.upper())
        self.assertNotIn("ORDER", rendered.upper())

    def test_daily_contract_and_authoritative_registry_include_component_and_test(self):
        contract = json.loads((ROOT / "config" / "daily_orchestrator_contract.json").read_text())
        self.assertIn("BUSINESS_ACCELERATION", contract["component_order"])
        run_all = (ROOT / "run_all.py").read_text(encoding="utf-8")
        self.assertIn('"test/test_business_acceleration_population.py"', run_all)


if __name__ == "__main__":
    unittest.main()
