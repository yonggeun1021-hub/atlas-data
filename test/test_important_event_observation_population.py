#!/usr/bin/env python3
"""P9-02 provider-free observation population regression."""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "execution/important_event_observation_population.py"
WORKFLOW = ROOT / ".github/workflows/collect.yml"
RUN_ALL = ROOT / "run_all.py"


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


POP = load_module("important_event_observation_population_test", SOURCE)


class ImportantEventObservationPopulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.observed_at = "2026-08-25T04:00:00Z"
        cls.source = POP.EVENT_POPULATION.build_population_inputs(
            repo_root=ROOT, decision_at=cls.observed_at
        )["packet"]
        cls.packet = POP.build_packet(cls.source, cls.observed_at)

    def test_real_population_preserves_all_nine_cases_but_blocks_all(self):
        summary = self.packet["summary"]
        self.assertEqual(summary["source_cases"], 9)
        self.assertEqual(summary["source_evidence_linked"], 2)
        self.assertEqual(summary["source_evidence_unresolved"], 7)
        self.assertEqual(summary["observation_events"], 9)
        self.assertEqual(summary["confirmed_events"], 0)
        self.assertEqual(summary["blocked_events"], 9)
        self.assertEqual(summary["date_only_blocked_events"], 9)

    def test_real_batch_is_accepted_by_existing_p9_02_input_validator(self):
        batch = self.packet["event_batch"]
        checked = POP.DETECTOR._validate_events(
            copy.deepcopy(batch),
            POP.DETECTOR._utc(self.observed_at, "BAD_TIME"),
            POP.DETECTOR.load_contract(),
        )
        self.assertEqual(len(checked["events"]), 9)
        self.assertTrue(all(row["evidence_status"] == "BLOCKED" for row in checked["events"]))

    def test_linked_sndk_rows_keep_exact_sec_url_and_content_hash(self):
        linked = [
            row for row in self.packet["event_batch"]["events"]
            if row["subject_id"] == "SNDK"
            and row["blocked_reasons"] == [
                "EVENT_AT_DATE_FLOOR_PLACEHOLDER",
                "EVENT_TIME_PRECISION_DATE_ONLY",
            ]
        ]
        self.assertEqual({row["event_type"] for row in linked}, {"FINANCIAL_RESULTS", "OTHER"})
        self.assertEqual(len({row["source_ref"] for row in linked}), 1)
        self.assertEqual(len({row["source_sha256"] for row in linked}), 1)
        self.assertTrue(linked[0]["source_ref"].startswith("https://www.sec.gov/Archives/"))

    def test_date_floor_is_explicitly_a_blocked_placeholder_not_a_claimed_timestamp(self):
        for row in self.packet["event_batch"]["events"]:
            self.assertTrue(row["event_at"].endswith("T00:00:00Z"))
            self.assertIn("EVENT_AT_DATE_FLOOR_PLACEHOLDER", row["blocked_reasons"])
            self.assertIn("EVENT_TIME_PRECISION_DATE_ONLY", row["blocked_reasons"])
            self.assertEqual(row["evidence_status"], "BLOCKED")

    def test_all_canonical_d1_event_types_normalize_without_collision(self):
        normalized = [POP._event_type_token(value) for value in POP.CASE.D1.EVENT_TYPES]
        self.assertEqual(len(normalized), len(set(normalized)))
        self.assertTrue(all(POP.DETECTOR.TOKEN_RE.fullmatch(value) for value in normalized))

    def test_unresolved_rows_cannot_become_confirmed_by_rehashing(self):
        tampered = copy.deepcopy(self.packet)
        row = next(
            item for item in tampered["event_batch"]["events"]
            if "SOURCE_EVIDENCE_UNRESOLVED" in item["blocked_reasons"]
        )
        row["evidence_status"] = "CONFIRMED"
        row["blocked_reasons"] = []
        tampered["event_batch"]["packet_sha256"] = POP.DETECTOR.payload_sha256(
            {k: v for k, v in tampered["event_batch"].items() if k != "packet_sha256"}
        )
        tampered["packet_sha256"] = POP.payload_sha256(
            {k: v for k, v in tampered.items() if k != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            POP.ImportantEventObservationPopulationError, "PACKET_CONTENT_MISMATCH"
        ):
            POP.validate_packet(tampered)

    def test_source_case_semantic_tamper_is_rejected_even_if_rehashed(self):
        tampered = copy.deepcopy(self.packet)
        tampered["source_packet"]["cases"][0]["event_type"] = "Guidance"
        unsigned_source = copy.deepcopy(tampered["source_packet"])
        unsigned_source.pop("packet_sha256")
        tampered["source_packet"]["packet_sha256"] = POP.CASE.payload_sha256(unsigned_source)
        with self.assertRaisesRegex(
            POP.ImportantEventObservationPopulationError, "SOURCE_EVENT_PACKET_INVALID"
        ):
            POP.validate_packet(tampered)

    def test_published_source_packet_is_required_and_byte_exact(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            event_root = root / "event-cases"
            with self.assertRaisesRegex(
                POP.ImportantEventObservationPopulationError,
                "PUBLISHED_SOURCE_PACKET_MISSING",
            ):
                POP.require_published_source_packet(
                    repo_root=ROOT,
                    event_root=event_root,
                    decision_at=self.observed_at,
                )
            path, _ = POP.EVENT_POPULATION.publish_append_only(
                out_root=event_root,
                decision_at=self.observed_at,
                packet=self.source,
            )
            path.write_bytes(path.read_bytes() + b" ")
            with self.assertRaisesRegex(
                POP.ImportantEventObservationPopulationError,
                "PUBLISHED_SOURCE_PACKET_BYTES_MISMATCH",
            ):
                POP.require_published_source_packet(
                    repo_root=ROOT,
                    event_root=event_root,
                    decision_at=self.observed_at,
                )

    def test_append_only_is_idempotent_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, created = POP.publish_append_only(
                out_root=root, observed_at=self.observed_at, packet=self.packet
            )
            second, created_again = POP.publish_append_only(
                out_root=root, observed_at=self.observed_at, packet=self.packet
            )
            self.assertEqual(first, second)
            self.assertEqual(first.parent.name, "2026-08-25")
            self.assertTrue(created)
            self.assertFalse(created_again)
            first.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(
                POP.ImportantEventObservationPopulationError,
                "CONTENT_ADDRESSED_PACKET_DRIFT",
            ):
                POP.publish_append_only(
                    out_root=root, observed_at=self.observed_at, packet=self.packet
                )

    def test_module_has_no_provider_policy_or_notification_client(self):
        tree = ast.parse(SOURCE.read_text(encoding="utf-8"))
        imports = {
            node.names[0].name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
        }
        self.assertFalse(imports & {"requests", "urllib", "httpx", "aiohttp"})
        text = SOURCE.read_text(encoding="utf-8")
        self.assertNotIn("workflow_dispatch", text)
        self.assertNotIn('"RATIFIED"', text)
        self.assertNotIn("send_notification", text)

    def test_authority_is_false_except_observation_population(self):
        authority = self.packet["authority"]
        self.assertTrue(authority["observation_population_only"])
        self.assertTrue(all(value is False for key, value in authority.items() if key != "observation_population_only"))


class OperationalWiringTests(unittest.TestCase):
    def test_population_runs_after_p3_08_and_before_commit(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        p3 = text.index("Populate SEC Event Discovery Cases (P3-08)")
        p9 = text.index("Populate Important Event Observations (P9-02)")
        commit = text.index("- name: Commit data")
        self.assertLess(p3, p9)
        self.assertLess(p9, commit)
        self.assertIn("important_event_observation_population.py", text[p9:commit])

    def test_approved_regression_registration(self):
        text = RUN_ALL.read_text(encoding="utf-8")
        self.assertEqual(text.count('"test/test_important_event_observation_population.py"'), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
