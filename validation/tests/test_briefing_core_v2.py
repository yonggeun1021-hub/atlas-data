#!/usr/bin/env python3
"""Natural-schedule-equivalent briefing_core/2 acceptance tests."""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from briefing_core import chain  # noqa: E402
from briefing_core import major_events  # noqa: E402
from briefing_core import paper_signal  # noqa: E402


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


PORTAL_PRODUCER = _load(
    "briefing_core_v2_portal_producer",
    ROOT / ".github/scripts/validated_briefing_portal_producer.py",
)


class BriefingCoreV2Acceptance(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Atlas Test"], cwd=self.repo, check=True)
        subprocess.run(
            ["git", "config", "user.email", "atlas@example.invalid"],
            cwd=self.repo,
            check=True,
        )
        self.generation = "a" * 64
        self.packet_path = "evidence/daily_briefing/morning/2026-09-02/rev-001/packet.json"
        self.briefing_path = "evidence/daily_briefing/morning/2026-09-02/rev-001/briefing.md"
        dart_path = self.repo / "data/latest_dart_content.json"
        sec_path = self.repo / "data/latest_sec_content.json"
        dart_path.parent.mkdir(parents=True)
        dart_path.write_text('{"provider":"dart"}\n', encoding="utf-8")
        sec_path.write_text('{"provider":"sec"}\n', encoding="utf-8")
        packet = {
            "schema_version": 1,
            "contract_version": "daily_orchestrator/6",
            "output_schema_version": "daily_briefing_packet/1",
            "slot": "morning",
            "decision_date": "2026-09-02",
            "generated_at": "2026-09-01T22:05:00Z",
            "capture_mode": "provider_free_aggregation_of_persisted_evidence_only",
            "authority": {
                "aggregation_only": True,
                "component_build_authorized": True,
                "order_generation_authorized": False,
                "production_authorized": False,
                "trading_authorized": False,
            },
            "component_status_counts": {"READY": 5, "DATA_BLOCKED": 2},
            "components": [
                {
                    "component_id": "STEP0_READ_MODEL_HEALTH",
                    "status": "READY",
                    "packet": {"generation": {"generation_id": self.generation}},
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "THREE_MARKET_REGIME_HEADER",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "FREE_MARKET_DATA",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "DART_FILING_CONTENT",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": "data/latest_dart_content.json",
                    "source_packet_sha256": chain.digest_bytes(dart_path.read_bytes()),
                },
                {
                    "component_id": "SEC_FILING_CONTENT",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": "data/latest_sec_content.json",
                    # Deliberate optional adapter defect: must isolate to news.
                    "source_packet_sha256": "b" * 64,
                },
                {
                    "component_id": "OFFICIAL_RELEASE_SUMMARY",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
                {
                    "component_id": "US_BREADTH_MEMBERSHIP",
                    "status": "READY",
                    "reason": None,
                    "source_packet_path": None,
                    "source_packet_sha256": None,
                },
            ],
            "frozen_sources": {},
            "unresolved_boundaries": [],
        }
        packet["packet_sha256"] = chain.digest(packet)
        packet_file = self.repo / self.packet_path
        packet_file.parent.mkdir(parents=True)
        packet_file.write_bytes(chain.canonical(packet) + b"\n")
        briefing_file = self.repo / self.briefing_path
        briefing_file.write_text("# Fixture briefing\n\nNo order is authorized.\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "pinned source"], cwd=self.repo, check=True)
        self.source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def tearDown(self):
        self.temp.cleanup()

    def envelope(self, source_commit=None):
        return chain.build_input_envelope(
            self.repo,
            source_commit=source_commit or self.source_commit,
            packet_path=self.packet_path,
            briefing_path=self.briefing_path,
            decision_date="2026-09-02",
            slot="morning",
            registry_path=ROOT / "config/briefing_module_registry_v2.json",
        )

    def source_packet(self):
        return json.loads(
            subprocess.check_output(
                ["git", "show", f"{self.source_commit}:{self.packet_path}"],
                cwd=self.repo,
            )
        )

    def write_packet(self, packet):
        packet.pop("packet_sha256", None)
        packet["packet_sha256"] = chain.digest(packet)
        (self.repo / self.packet_path).write_bytes(chain.canonical(packet) + b"\n")

    def write_generation_source(self, path, generation_id):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(
            chain.canonical({"generation": {"generation_id": generation_id}}) + b"\n"
        )

    def commit_changes(self, message):
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", message], cwd=self.repo, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def event_registry(self):
        return json.loads(
            (ROOT / "validation/fixtures/briefing_major_events/2026-09-02-am.json")
            .read_text(encoding="utf-8")
        )

    def source_with_event_registry(self):
        registry = self.event_registry()
        body = chain.canonical(registry) + b"\n"
        root = self.repo / "evidence/briefing_events/2026-09-02/morning"
        revision = root / "rev-001/registry.json"
        revision.parent.mkdir(parents=True)
        revision.write_bytes(body)
        index = {
            "schema_version": "major_event_registry_index/1",
            "latest_revision": 1,
            "revisions": [{
                "revision": 1,
                "path": "rev-001/registry.json",
                "sha256": chain.digest_bytes(body),
            }],
        }
        (root / "index.json").write_bytes(chain.canonical(index) + b"\n")
        subprocess.run(["git", "add", "evidence/briefing_events"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "major event registry"], cwd=self.repo, check=True)
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, text=True
        ).strip()

    def test_exact_source_commit_and_generation_are_frozen(self):
        envelope = self.envelope()
        old_hash = envelope["source_refs"][0]["sha256"]
        (self.repo / self.packet_path).write_text("{}\n", encoding="utf-8")
        rebuilt = self.envelope()
        self.assertEqual(rebuilt["source_commit"], self.source_commit)
        self.assertEqual(rebuilt["generation_id"], self.generation)
        self.assertEqual(rebuilt["source_refs"][0]["sha256"], old_hash)

    def test_canonical_root_allows_distinct_nested_source_generations(self):
        packet = self.source_packet()
        packet["components"][0]["packet"]["generation"] = None
        packet["nested_source_lineage"] = [
            {"generation_id": "b" * 64},
            {"generation_id": "c" * 64},
        ]
        self.write_packet(packet)
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("canonical generation with nested sources")

        envelope = self.envelope(source_commit)

        self.assertEqual(envelope["generation_id"], self.generation)

    def test_canonical_generation_sources_must_match(self):
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, "b" * 64)
        source_commit = self.commit_changes("mismatched canonical generations")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_CANONICAL_GENERATION_MISMATCH"
        ):
            self.envelope(source_commit)

    def test_canonical_generation_sources_must_be_present_together(self):
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("one canonical generation source")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_CANONICAL_GENERATION_SOURCE_MISSING"
        ):
            self.envelope(source_commit)

    def test_canonical_generation_source_must_be_lowercase_sha256(self):
        self.write_generation_source(chain.STEP0_STATUS_PATH, "A" * 64)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, "A" * 64)
        source_commit = self.commit_changes("malformed canonical generation")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_STEP0_GENERATION_INVALID"
        ):
            self.envelope(source_commit)

    def test_nested_generation_ids_remain_format_validated(self):
        packet = self.source_packet()
        packet["nested_source_lineage"] = {"generation_id": "B" * 64}
        self.write_packet(packet)
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("malformed nested generation")

        with self.assertRaisesRegex(chain.ChainError, "CORE_GENERATION_INVALID"):
            self.envelope(source_commit)

    def test_embedded_step0_generation_must_match_canonical_root(self):
        packet = self.source_packet()
        packet["components"][0]["packet"]["generation"]["generation_id"] = "b" * 64
        self.write_packet(packet)
        self.write_generation_source(chain.STEP0_STATUS_PATH, self.generation)
        self.write_generation_source(chain.BRIEFING_STATUS_PATH, self.generation)
        source_commit = self.commit_changes("mismatched embedded generation")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_EMBEDDED_STEP0_GENERATION_MISMATCH"
        ):
            self.envelope(source_commit)

    def test_legacy_packet_with_multiple_generations_still_fails_closed(self):
        packet = self.source_packet()
        packet["nested_source_lineage"] = {"generation_id": "b" * 64}
        self.write_packet(packet)
        source_commit = self.commit_changes("legacy multiple generations")

        with self.assertRaisesRegex(
            chain.ChainError, "CORE_GENERATION_NOT_SINGLETON"
        ):
            self.envelope(source_commit)

    def test_optional_module_failure_is_item_unknown_not_global_hold(self):
        envelope = self.envelope()
        modules = {row["module_id"]: row for row in envelope["modules"]}
        self.assertEqual(modules["news"]["status"], "PARTIAL")
        sec = next(
            row for row in modules["news"]["components"]
            if row["component_id"] == "SEC_FILING_CONTENT"
        )
        self.assertEqual(sec["effective_status"], "UNKNOWN")
        self.assertEqual(sec["binding_status"], "SOURCE_BINDING_MISMATCH")
        self.assertEqual(modules["crypto"]["status"], "UNAVAILABLE")
        self.assertEqual(envelope["schema_version"], "briefing_input_envelope/2")

    def test_only_core_identity_and_authority_errors_fail_closed(self):
        with self.assertRaisesRegex(chain.ChainError, "CORE_DATE_SLOT_LINEAGE_MISMATCH"):
            chain.build_input_envelope(
                self.repo,
                source_commit=self.source_commit,
                packet_path=self.packet_path,
                briefing_path=self.briefing_path,
                decision_date="2026-09-03",
                slot="morning",
                registry_path=ROOT / "config/briefing_module_registry_v2.json",
            )
        packet = json.loads(
            subprocess.check_output(
                ["git", "show", f"{self.source_commit}:{self.packet_path}"], cwd=self.repo
            )
        )
        packet["authority"]["order_generation_authorized"] = True
        packet.pop("packet_sha256")
        packet["packet_sha256"] = chain.digest(packet)
        (self.repo / self.packet_path).write_bytes(chain.canonical(packet) + b"\n")
        subprocess.run(["git", "add", self.packet_path], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "unsafe source"], cwd=self.repo, check=True)
        unsafe = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo, text=True).strip()
        with self.assertRaisesRegex(chain.ChainError, "CORE_EXECUTION_AUTHORITY_VIOLATION"):
            chain.build_input_envelope(
                self.repo,
                source_commit=unsafe,
                packet_path=self.packet_path,
                briefing_path=self.briefing_path,
                decision_date="2026-09-02",
                slot="morning",
                registry_path=ROOT / "config/briefing_module_registry_v2.json",
            )

    def test_handoff_and_claims_are_always_present_and_compatible(self):
        artifacts = chain.build_chain_artifacts(self.envelope())
        self.assertEqual(artifacts["handoff.json"]["schema_version"], "briefing_handoff/2")
        self.assertEqual(
            artifacts["claude-handoff-v1.json"]["schema_version"],
            "claude_briefing_handoff/1",
        )
        self.assertGreater(len(artifacts["claude-handoff-v1.json"]["claims"]), 0)
        self.assertEqual(artifacts["claim-ledger.json"]["schema_version"], "claim_ledger/1")
        self.assertGreater(len(artifacts["claim-ledger.json"]["claims"]), 0)
        self.assertEqual(
            artifacts["handoff.json"]["major_event_coverage"]["user_message_ko"],
            "주요 뉴스 검증 불가",
        )
        self.assertFalse(
            artifacts["display-proposal.json"]["changes"][0]["content"]
            ["complete_market_conclusion_allowed"]
        )

    def test_20260902_major_event_omission_enters_correction_loop_then_passes(self):
        registry = major_events.validate_registry(
            self.event_registry(), briefing_date="2026-09-02", slot="AM"
        )
        missing_draft = {"major_event_coverage": major_events.unavailable_coverage()}
        missing = major_events.validate_coverage(missing_draft, registry)
        self.assertEqual(missing["status"], "CORRECTION_REQUIRED")
        self.assertFalse(missing["portal_allowed"])
        self.assertIn("MAJOR_EVENT_COVERAGE_MISSING", missing["reason_codes"])
        corrected = major_events.correct_handoff(missing_draft, registry)
        passed = major_events.validate_coverage(corrected, registry)
        self.assertEqual(passed["status"], "PASS")
        self.assertTrue(passed["portal_allowed"])
        coverage = corrected["major_event_coverage"]
        self.assertEqual(
            coverage["user_message_ko"],
            "미국의 이란 군사시설 타격, 이란 보복으로 중동 위험 재확대",
        )
        event = coverage["events"][0]
        self.assertEqual(len(event["facts"]), 2)
        self.assertTrue(event["inferences"])
        self.assertTrue(event["unknowns"])
        self.assertTrue(all(not row["price_causality_confirmed"] for row in event["transmission_channels"]))

    def test_20260902_operational_event_registry_matches_regression_fixture(self):
        fixture = self.event_registry()
        registry_path = (
            ROOT / "evidence/briefing_events/2026-09-02/morning/rev-001/registry.json"
        )
        registry_bytes = registry_path.read_bytes()
        operational = json.loads(registry_bytes)
        self.assertEqual(operational, fixture)
        index = json.loads(
            (ROOT / "evidence/briefing_events/2026-09-02/morning/index.json")
            .read_text(encoding="utf-8")
        )
        self.assertEqual(index["latest_revision"], 1)
        self.assertEqual(index["revisions"][0]["sha256"], chain.digest_bytes(registry_bytes))
        grades = {source["grade"] for source in operational["events"][0]["sources"]}
        self.assertEqual(grades, {"PRIMARY_OFFICIAL", "INDEPENDENT_MAJOR_MEDIA"})

    def test_chain_publication_is_append_only_and_idempotent(self):
        artifacts = chain.build_chain_artifacts(self.envelope())
        first = chain.publish_chain(self.repo, artifacts)
        second = chain.publish_chain(self.repo, artifacts)
        self.assertEqual((first["result"], second["result"]), ("APPLIED", "NO_CHANGE"))
        self.assertEqual(second["duplicate_count"], 0)
        changed = copy.deepcopy(artifacts)
        changed["handoff.json"]["analyst_adapter"]["reason"] = "DIFFERENT"
        with self.assertRaisesRegex(chain.ChainError, "CORE_DUPLICATE_ID_CONFLICT"):
            chain.publish_chain(self.repo, changed)

    def test_natural_equivalent_e2e_reaches_portal_and_notion_receipt(self):
        self.source_commit = self.source_with_event_registry()
        envelope = self.envelope()
        source_briefing = subprocess.check_output(
            ["git", "show", f"{self.source_commit}:{self.briefing_path}"], cwd=self.repo
        )
        artifacts = chain.build_chain_artifacts(envelope, briefing_bytes=source_briefing)
        event_gate = artifacts["major-event-validation.json"]
        self.assertEqual(event_gate["pre_correction"]["status"], "CORRECTION_REQUIRED")
        self.assertEqual(event_gate["post_correction"]["status"], "PASS")
        self.assertEqual(event_gate["correction_count"], 1)
        self.assertFalse(event_gate["overwrite_performed"])
        self.assertEqual(
            artifacts["display-proposal.json"]["changes"][0]["content"]
            ["today_key_events"][0]["headline_ko"],
            "미국의 이란 군사시설 타격, 이란 보복으로 중동 위험 재확대",
        )
        corrected_briefing = artifacts["corrected-briefing.md"]
        corrected_text = corrected_briefing.decode("utf-8")
        self.assertLess(corrected_text.index("오늘의 핵심 사건"), corrected_text.index("No order"))
        self.assertIn("전면전의 완전한 재개로 단정할 수 있는지는 아직 확인되지 않았습니다", corrected_text)
        self.assertFalse(artifacts["correction-manifest.json"]["overwrites_source"])
        self.assertEqual(
            artifacts["correction-manifest.json"]["corrected_briefing_sha256"],
            chain.digest_bytes(corrected_briefing),
        )
        input_dir = self.repo / "e2e-input"
        input_dir.mkdir()
        briefing = input_dir / "briefing.md"
        briefing.write_bytes(corrected_briefing)
        paths = {}
        for name in ("claim-ledger.json", "display-proposal.json"):
            path = input_dir / name
            path.write_bytes(chain.canonical(artifacts[name]) + b"\n")
            paths[name] = path
        report = chain.fixture_validation_report(
            artifacts["claim-ledger.json"],
            corrected_briefing,
            artifacts["display-proposal.json"],
            validated_at_kst="2026-09-02T08:00:00+09:00",
        )
        report_path = input_dir / "validation-report.json"
        report_path.write_bytes(chain.canonical(report) + b"\n")
        args = argparse.Namespace(
            repo_root=str(self.repo),
            briefing=str(briefing),
            claim_ledger=str(paths["claim-ledger.json"]),
            validation_report=str(report_path),
            display_proposal=str(paths["display-proposal.json"]),
            out_root="evidence/validated_briefing_portal",
        )
        portal_first = PORTAL_PRODUCER.build(args)
        portal_second = PORTAL_PRODUCER.build(args)
        self.assertEqual((portal_first["result"], portal_second["result"]), ("APPLIED", "NO_CHANGE"))
        portal_envelope = json.loads(
            (self.repo / portal_first["envelope_path"]).read_text(encoding="utf-8")
        )
        receipt = chain.notion_receipt(
            portal_envelope,
            portal_state="APPLIED",
            portal_url="https://atlas.example.invalid/briefing",
        )
        notion_first = chain.publish_notion_receipt(self.repo, receipt)
        notion_second = chain.publish_notion_receipt(self.repo, receipt)
        self.assertEqual((notion_first["result"], notion_second["result"]), ("APPLIED", "NO_CHANGE"))
        self.assertEqual(notion_second["duplicate_count"], 0)
        self.assertTrue(receipt["readback_verified"])

    def test_paper_runtime_can_only_publish_append_only_standard_signals(self):
        signal = {
            "schema_version": "atlas_paper_signal/1",
            "signal_id": "paper-20260902-btc-001",
            "event_at": "2026-09-02T00:00:00Z",
            "market": "CRYPTO",
            "symbol": "BTC/KRW",
            "signal_type": "OBSERVATION",
            "payload": {"status": "PAPER_ONLY"},
            "lineage": {"source_commit": "a" * 40, "generation_id": "b" * 64},
            "authority": {
                "account_mode": "PAPER",
                "real_capital": False,
                "order_authority": False,
                "production_authority": False,
                "trading_authority": False,
            },
        }
        path = "runtime/paper/signals/v1/2026-09-02/paper-20260902-btc-001.json"
        first = paper_signal.publish(self.repo, path, signal)
        second = paper_signal.publish(self.repo, path, signal)
        self.assertEqual((first["result"], second["result"]), ("APPLIED", "NO_CHANGE"))
        with self.assertRaisesRegex(paper_signal.PaperBoundaryError, "PAPER_CORE_PATH_FORBIDDEN"):
            paper_signal.publish(self.repo, "data/briefing/finalization/attack.json", signal)
        unsafe = copy.deepcopy(signal)
        unsafe["authority"]["order_authority"] = True
        with self.assertRaisesRegex(paper_signal.PaperBoundaryError, "PAPER_SIGNAL_AUTHORITY_INVALID"):
            paper_signal.publish(
                self.repo,
                "runtime/paper/signals/v1/2026-09-02/unsafe.json",
                unsafe,
            )
        result = {
            "schema_version": "atlas_paper_result/1",
            "result_id": "result-20260902-btc-001",
            "signal_id": signal["signal_id"],
            "observed_at": "2026-09-02T00:05:00Z",
            "outcome": "OBSERVED_NO_ORDER",
            "payload": {"status": "PAPER_ONLY"},
            "lineage": signal["lineage"],
            "authority": signal["authority"],
        }
        result_path = "runtime/paper/results/v1/2026-09-02/result-20260902-btc-001.json"
        self.assertEqual(
            paper_signal.publish(self.repo, result_path, result)["result"], "APPLIED"
        )

    def test_workflow_and_path_ownership_are_enforced(self):
        workflow = (ROOT / ".github/workflows/daily-briefing.yml").read_text(encoding="utf-8")
        seal = workflow.index("- name: Seal briefing for finalization")
        core = workflow.index("- name: Build pinned briefing core handoff")
        publish = workflow.index("- name: Publish sealed draft")
        self.assertLess(seal, core)
        self.assertLess(core, publish)
        core_step = workflow[core:publish]
        self.assertIn('CAPTURE_PATH="${CAPTURE_PATH#"$GITHUB_WORKSPACE"/}"', core_step)
        self.assertIn('--packet-path "$CAPTURE_PATH/packet.json"', core_step)
        self.assertIn('--briefing-path "$CAPTURE_PATH/briefing.md"', core_step)
        self.assertNotIn(
            '--packet-path "${{ steps.briefing.outputs.capture_path }}/packet.json"',
            core_step,
        )
        self.assertIn("--source-commit \"${{ steps.briefing.outputs.source_commit }}\"", workflow)
        self.assertIn("git add data/briefing/chain_v2", workflow)
        actions = (ROOT / ".github/workflows/actions-pass.yml").read_text(encoding="utf-8")
        self.assertIn("python3 validation/tests/test_briefing_core_v2.py", actions)
        ownership = json.loads(
            (ROOT / "config/briefing_path_ownership_v1.json").read_text(encoding="utf-8")
        )
        paper = ownership["paper_runtime_owner"]
        self.assertTrue(paper["append_only"])
        self.assertFalse(paper["direct_portal_or_notion_write"])
        self.assertTrue(
            set(paper["write_roots"]).isdisjoint(set(paper["forbidden_roots"]))
        )


if __name__ == "__main__":
    unittest.main()
