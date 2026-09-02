#!/usr/bin/env python3
"""P4-02 -> P5-03 registered TSM link-only regression (offline)."""
from __future__ import annotations

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "bridge" / "tsm_sec_monthly_rule_evidence.py"
SPEC = importlib.util.spec_from_file_location("tsm_sec_monthly_rule_evidence", SOURCE)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class TsmSecMonthlyRuleEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contract = MODULE.load_contract()
        cls.source_path = MODULE.select_observation_packet(
            ROOT / "data" / "observations" / "official_release_observations",
            ROOT / "data",
        )

    def packet(self):
        return MODULE.build_packet(
            observation_packet=self.source_path,
            data_root=ROOT / "data",
        )

    def test_contract_is_exact_link_only_for_two_existing_rules(self):
        self.assertEqual(
            self.contract["registered_rules"],
            {
                "RULE-0007": "e69be9bebbc9ed7e1487cf479aca3ba0ff6397d293f443d414fba2b56de8fe3e",
                "RULE-0008": "a1607620f9ced118e0d98f6a21b3ee5ae6b736d15b643bfe0939ca1590e0b31e",
            },
        )
        self.assertEqual(self.contract["selection_mode"], "ALL_REQUIRED")
        self.assertTrue(self.contract["authority"]["linkage_only"])
        self.assertTrue(
            all(
                value is False
                for key, value in self.contract["authority"].items()
                if key != "linkage_only"
            )
        )
        self.assertNotIn("SNDK", json.dumps(self.contract))

    def test_exact_latest_period_binds_both_measurements_to_both_rules(self):
        packet = self.packet()
        self.assertEqual(len(packet["frozen_evidence_envelopes"]), 2)
        self.assertEqual(
            {item["measurement_identity"] for item in packet["frozen_evidence_envelopes"]},
            {
                "TSMC consolidated net revenue monthly YoY",
                "TSMC consolidated net revenue cumulative YoY",
            },
        )
        self.assertEqual(
            {item["economic_period_end"] for item in packet["frozen_evidence_envelopes"]},
            {"2026-07-31"},
        )
        for rule_id in ("RULE-0007", "RULE-0008"):
            row = next(item for item in packet["rules"] if item["rule_id"] == rule_id)
            self.assertEqual(row["link_status"], MODULE.BIND.LINK_AVAILABLE)
            self.assertEqual(row["selection_mode"], "ALL_REQUIRED")
            self.assertEqual(len(row["evidence_references"]), 2)
            self.assertEqual(row["evaluation_status"], MODULE.BIND.EVALUATION_NOT_AUTHORIZED)
            self.assertIsNone(row["rule_result"])
        for row in packet["rules"]:
            self.assertIsNone(row["rule_result"])

    def test_lineage_preserves_hash_url_quote_offset_without_identity_bodies(self):
        packet = self.packet()
        for envelope in packet["frozen_evidence_envelopes"]:
            audit = envelope["audit_provenance"]
            self.assertEqual(audit["capture_kind"], "P4_02_RETAINED_EXACT_SEC_BYTES")
            self.assertEqual(audit["offset_basis"], "normalized_visible_text")
            self.assertGreater(audit["char_offset"], 0)
            self.assertTrue(audit["quote"].startswith("Net Revenue "))
            self.assertEqual(envelope["observation"]["quote"], audit["quote"])
            self.assertEqual(envelope["observation"]["char_offset"], audit["char_offset"])
            for key in ("full_submission", "filing_index"):
                identity = audit["identity_evidence"][key]
                self.assertFalse(identity["body_preserved_in_binding"])
                self.assertEqual(
                    identity["retention"],
                    "URL_SHA_LINEAGE_ONLY_BODY_NOT_PRESERVED",
                )
                self.assertRegex(identity["content_sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(identity["source_uri"].startswith("https://www.sec.gov/Archives/"))
                self.assertNotIn("body", identity)
        serialized = json.dumps(packet, ensure_ascii=False)
        self.assertNotIn("Taiwan Semiconductor Manufacturing Company Limited", serialized)

    def test_existing_raw_cache_retention_boundary_is_reused_unchanged(self):
        sec_contract = json.loads(
            (ROOT / "config" / "sec_filing_content_contract.json").read_text(encoding="utf-8")
        )
        self.assertEqual(sec_contract["retention_policy"]["raw_cache_days"], 90)
        self.assertEqual(
            sec_contract["retention_policy"]["permanent_raw_stages"],
            ["Ready", "Buy", "Holding"],
        )
        for envelope in self.packet()["frozen_evidence_envelopes"]:
            self.assertEqual(
                envelope["audit_provenance"]["primary_document"]["raw_cache_policy"],
                "permanent",
            )

    def test_independent_validation_rejects_self_rehashed_semantic_or_authority_drift(self):
        packet = self.packet()
        changed = copy.deepcopy(packet)
        changed["frozen_evidence_envelopes"][0]["observation"]["numeric_value"] = "999.9"
        changed["inputs"]["evidence_set_sha256"] = MODULE.BIND.payload_sha256(
            changed["frozen_evidence_envelopes"]
        )
        changed["packet_sha256"] = MODULE.BIND.payload_sha256(
            {key: value for key, value in changed.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(
            MODULE.TsmRuleEvidenceError,
            "PACKET_INDEPENDENT_REBUILD_MISMATCH|P5_03_PACKET_INVALID",
        ):
            MODULE.validate_packet(changed, data_root=ROOT / "data")

        changed = copy.deepcopy(packet)
        changed["authority"]["rule_evaluation_authorized"] = True
        changed["packet_sha256"] = MODULE.BIND.payload_sha256(
            {key: value for key, value in changed.items() if key != "packet_sha256"}
        )
        with self.assertRaisesRegex(MODULE.TsmRuleEvidenceError, "P5_03_PACKET_INVALID"):
            MODULE.validate_packet(changed, data_root=ROOT / "data")

    def test_missing_or_ambiguous_source_packet_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaisesRegex(MODULE.TsmRuleEvidenceError, "SOURCE_PACKET_MISSING"):
                MODULE.select_observation_packet(root, ROOT / "data")

            original = self.source_path.read_text(encoding="utf-8")
            for day in ("a", "b"):
                target = root / day / self.source_path.name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(original, encoding="utf-8")
            with self.assertRaisesRegex(
                MODULE.TsmRuleEvidenceError, "SOURCE_PACKET_LATEST_AMBIGUOUS"
            ):
                MODULE.select_observation_packet(root, ROOT / "data")

    def test_missing_measurement_and_conflicting_table_value_fail_closed(self):
        packet, raw = MODULE._read_json(self.source_path)
        latest = MODULE._latest_observation(packet)
        latest["published_values"].pop("monthly_yoy_pct_published")
        manifest, manifest_bytes, primary = MODULE._manifest_and_raw(
            MODULE._latest_observation(packet), ROOT / "data"
        )
        with self.assertRaisesRegex(
            MODULE.TsmRuleEvidenceError, "REGISTERED_MEASUREMENT_MISSING"
        ):
            MODULE._envelopes(
                packet, self.source_path, raw, latest, manifest, manifest_bytes,
                primary, self.contract,
            )

        latest = MODULE._latest_observation(packet)
        latest["published_values"]["monthly_yoy_pct_published"] = "999.9"
        with self.assertRaisesRegex(
            MODULE.TsmRuleEvidenceError, "REGISTERED_MEASUREMENT_CONFLICT"
        ):
            MODULE._envelopes(
                packet, self.source_path, raw, latest, manifest, manifest_bytes,
                primary, self.contract,
            )

    def test_publish_is_content_addressed_append_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = self.packet()
            first = MODULE.publish_packet(packet, out_root=Path(tmp))
            before = first.read_bytes()
            second = MODULE.publish_packet(packet, out_root=Path(tmp))
            self.assertEqual(first, second)
            self.assertEqual(before, second.read_bytes())
            first.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(MODULE.TsmRuleEvidenceError, "APPEND_ONLY_PACKET_DRIFT"):
                MODULE.publish_packet(packet, out_root=Path(tmp))


class TsmSecMonthlyRuleEvidenceWiringTests(unittest.TestCase):
    def test_daily_collect_runs_binding_after_tsm_observation(self):
        workflow = (ROOT / ".github" / "workflows" / "collect.yml").read_text(encoding="utf-8")
        self.assertLess(
            workflow.index("Populate TSMC Official Release Observations (P4-04)"),
            workflow.index("Bind TSMC Monthly Revenue to Rule Evidence (P4-02 to P5-03)"),
        )
        block = workflow.split(
            "Bind TSMC Monthly Revenue to Rule Evidence (P4-02 to P5-03)", 1
        )[1].split("- name:", 1)[0]
        self.assertIn("bridge/tsm_sec_monthly_rule_evidence.py", block)
        self.assertNotIn("SNDK", block)

    def test_authoritative_runner_registers_test_once(self):
        runner = (ROOT / "run_all.py").read_text(encoding="utf-8")
        self.assertEqual(runner.count('"test/test_tsm_sec_monthly_rule_evidence.py"'), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
