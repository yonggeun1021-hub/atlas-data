#!/usr/bin/env python3
"""P8-10 Recommendation A authority-structure regressions."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "decision" / "reflection_evidence_authority.py"
PRICE_REFLECTION_SOURCE = ROOT / "decision" / "price_reflection.py"


def load_module():
    spec = importlib.util.spec_from_file_location("reflection_evidence_authority", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = load_module()


def load_price_reflection_module():
    spec = importlib.util.spec_from_file_location(
        "price_reflection_with_authority", PRICE_REFLECTION_SOURCE
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


class ReflectionEvidenceAuthorityTests(unittest.TestCase):
    DECISION_AFTER_APPROVAL = "2026-09-03T00:00:00Z"

    def setUp(self):
        self.registry = read_json(MODULE.REGISTRY_PATH)

    def _assessment_for_registry(self, registry: dict) -> dict:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "registry.json"
            write_json(path, registry)
            return MODULE.assess_authority(
                self.DECISION_AFTER_APPROVAL, registry_path=path, root=ROOT
            )

    def test_canonical_structure_is_valid_but_classifier_remains_inactive(self):
        validated = MODULE.validate_authority(self.DECISION_AFTER_APPROVAL)
        self.assertIsNone(validated["record"]["effective_from"])
        assessment = MODULE.assess_authority(self.DECISION_AFTER_APPROVAL)
        self.assertEqual(assessment["authority_state"], "INACTIVE")
        self.assertFalse(assessment["classifier_enabled"])
        self.assertEqual(assessment["reflection_status"], "UNKNOWN")
        self.assertEqual(assessment["aggregate_threshold_basis"], "PROVISIONAL")
        self.assertIn("AUTHORITY_EFFECTIVE_FROM_UNRESOLVED", assessment["reason_codes"])
        self.assertIn("AUTHORITY_NUMERIC_RATIFICATION_PENDING", assessment["reason_codes"])
        self.assertIn("AUTHORITY_NATURAL_REVALIDATION_PENDING", assessment["reason_codes"])

    def test_real_price_reflection_path_consults_inactive_authority_gate(self):
        price_reflection = load_price_reflection_module()
        packet = price_reflection.build_packet(
            subject="TSM",
            decision_date="2026-09-03",
            generated_at="2026-09-03T00:00:00Z",
            price_as_of="2026-09-02T00:00:00Z",
            recent_return_windows={"1m": "5"},
            relative_strength={"vs_market": "3"},
        )
        reflection = packet["price_reflection"]
        self.assertEqual(reflection["reflection_status"], "UNKNOWN")
        self.assertEqual(reflection["threshold_basis"], "PROVISIONAL")
        self.assertIn(
            "reflection_evidence_authority_state=INACTIVE", reflection["reasons"]
        )
        self.assertIn("AUTHORITY_EFFECTIVE_FROM_UNRESOLVED", reflection["reasons"])

    def test_required_authority_record_identity_fields_are_present(self):
        record = self.registry["records"][0]
        for field in (
            "rule_id", "rule_version", "ratified_at", "effective_from",
            "content_sha256", "authority_evidence_ref", "authority_evidence_sha256",
        ):
            self.assertIn(field, record)
        self.assertEqual(record["ratified_at"], "2026-09-02T14:32:11Z")
        self.assertIsNone(record["effective_from"])

    def test_exact_content_and_evidence_hashes_match(self):
        record = self.registry["records"][0]
        for ref_field, sha_field in (
            ("content_ref", "content_sha256"),
            ("authority_evidence_ref", "authority_evidence_sha256"),
        ):
            actual = hashlib.sha256((ROOT / record[ref_field]).read_bytes()).hexdigest()
            self.assertEqual(actual, record[sha_field])

    def test_pending_numeric_policy_and_owner_are_not_invented(self):
        content = read_json(ROOT / MODULE.CONTENT_REF)
        self.assertEqual(content["state_vocabulary"], MODULE.STATE_VOCABULARY)
        self.assertEqual(content["source_identity_policy"], MODULE.SOURCE_IDENTITY_POLICY)
        self.assertEqual(content["point_in_time_policy"], MODULE.POINT_IN_TIME_POLICY)
        self.assertEqual(content["fail_closed_conditions"], MODULE.FAIL_CLOSED_CONDITIONS)
        self.assertEqual(content["pending_ratification"], MODULE.PENDING_FIELDS)
        self.assertNotIn(True, content["approval_boundary"].values())
        self.assertEqual(content["approval_boundary"]["candidate"], "NONE")
        self.assertEqual(content["operational_defaults"], MODULE.OPERATIONAL_DEFAULTS)

    def test_p8_12_and_every_downstream_authority_remain_closed(self):
        boundary = read_json(ROOT / MODULE.CONTENT_REF)["approval_boundary"]
        false_fields = {key for key, value in boundary.items() if key != "candidate" and value is False}
        self.assertEqual(false_fields, set(boundary) - {"candidate"})
        self.assertFalse(boundary["p8_12_recommendation_a_approved"])
        self.assertFalse(boundary["p8_12_recommendation_b_approved"])
        self.assertFalse(boundary["p8_12_recommendation_c_approved"])

    def test_historical_decision_cannot_use_later_ratification(self):
        assessment = MODULE.assess_authority("2026-09-02T14:32:10Z")
        self.assertEqual(assessment["authority_state"], "UNKNOWN")
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_RATIFICATION_IN_FUTURE"])
        self.assertFalse(assessment["classifier_enabled"])

    def test_missing_authority_record_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.json"
            assessment = MODULE.assess_authority(
                self.DECISION_AFTER_APPROVAL, registry_path=missing
            )
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_RECORD_MISSING"])
        self.assertFalse(assessment["classifier_enabled"])

    def test_stale_record_fails_closed(self):
        registry = copy.deepcopy(self.registry)
        registry["records"][0]["record_state"] = "SUPERSEDED"
        assessment = self._assessment_for_registry(registry)
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_RECORD_STALE"])

    def test_future_ratification_fails_closed(self):
        registry = copy.deepcopy(self.registry)
        registry["records"][0]["ratified_at"] = "2099-01-01T00:00:00Z"
        assessment = self._assessment_for_registry(registry)
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_RATIFICATION_IN_FUTURE"])

    def test_future_effective_from_fails_closed(self):
        registry = copy.deepcopy(self.registry)
        registry["records"][0]["effective_from"] = "2099-01-01T00:00:00Z"
        assessment = self._assessment_for_registry(registry)
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_EFFECTIVE_FROM_IN_FUTURE"])

    def test_backdated_effective_from_fails_closed(self):
        registry = copy.deepcopy(self.registry)
        registry["records"][0]["effective_from"] = "2026-09-02T14:32:10Z"
        assessment = self._assessment_for_registry(registry)
        self.assertEqual(
            assessment["reason_codes"], ["AUTHORITY_EFFECTIVE_FROM_PRECEDES_RATIFICATION"]
        )

    def test_malformed_hash_fails_closed(self):
        registry = copy.deepcopy(self.registry)
        registry["records"][0]["content_sha256"] = "not-a-sha"
        assessment = self._assessment_for_registry(registry)
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_HASH_INVALID:content_sha256"])

    def test_rule_identity_mismatch_fails_closed(self):
        registry = copy.deepcopy(self.registry)
        registry["records"][0]["rule_version"] = "2"
        assessment = self._assessment_for_registry(registry)
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_IDENTITY_MISMATCH"])

    def test_content_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry_path = root / "registry.json"
            write_json(registry_path, self.registry)
            content = read_json(ROOT / MODULE.CONTENT_REF)
            content["operational_defaults"]["classifier_enabled"] = True
            write_json(root / MODULE.CONTENT_REF, content)
            evidence_path = root / MODULE.AUTHORITY_EVIDENCE_REF
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_bytes((ROOT / MODULE.AUTHORITY_EVIDENCE_REF).read_bytes())
            assessment = MODULE.assess_authority(
                self.DECISION_AFTER_APPROVAL, registry_path=registry_path, root=root
            )
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_CONTENT_TAMPERED"])

    def test_authority_evidence_tamper_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry_path = root / "registry.json"
            write_json(registry_path, self.registry)
            content_path = root / MODULE.CONTENT_REF
            content_path.parent.mkdir(parents=True, exist_ok=True)
            content_path.write_bytes((ROOT / MODULE.CONTENT_REF).read_bytes())
            evidence = read_json(ROOT / MODULE.AUTHORITY_EVIDENCE_REF)
            evidence["approved_recommendation"] = "B"
            write_json(root / MODULE.AUTHORITY_EVIDENCE_REF, evidence)
            assessment = MODULE.assess_authority(
                self.DECISION_AFTER_APPROVAL, registry_path=registry_path, root=root
            )
        self.assertEqual(assessment["reason_codes"], ["AUTHORITY_EVIDENCE_TAMPERED"])

    def test_rehashing_tampered_content_does_not_bypass_pinned_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = copy.deepcopy(self.registry)
            content = read_json(ROOT / MODULE.CONTENT_REF)
            content["operational_defaults"]["classifier_enabled"] = True
            content_path = root / MODULE.CONTENT_REF
            write_json(content_path, content)
            registry["records"][0]["content_sha256"] = hashlib.sha256(
                content_path.read_bytes()
            ).hexdigest()
            registry_path = root / "registry.json"
            write_json(registry_path, registry)
            evidence_path = root / MODULE.AUTHORITY_EVIDENCE_REF
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_bytes((ROOT / MODULE.AUTHORITY_EVIDENCE_REF).read_bytes())
            assessment = MODULE.assess_authority(
                self.DECISION_AFTER_APPROVAL, registry_path=registry_path, root=root
            )
        self.assertEqual(
            assessment["reason_codes"], ["AUTHORITY_IMMUTABLE_IDENTITY_MISMATCH"]
        )

    def test_missing_required_field_and_duplicate_record_fail_closed(self):
        missing = copy.deepcopy(self.registry)
        del missing["records"][0]["effective_from"]
        self.assertEqual(
            self._assessment_for_registry(missing)["reason_codes"],
            ["AUTHORITY_RECORD_FIELDS_MISMATCH"],
        )
        duplicate = copy.deepcopy(self.registry)
        duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
        self.assertEqual(
            self._assessment_for_registry(duplicate)["reason_codes"],
            ["AUTHORITY_RECORD_AMBIGUOUS"],
        )


if __name__ == "__main__":
    unittest.main()
