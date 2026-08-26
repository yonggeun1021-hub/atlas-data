#!/usr/bin/env python3
"""P4-01 deterministic Data Coverage Matrix regression."""

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "audit" / "data_coverage_matrix.py"
WORKFLOWS = ROOT / ".github" / "workflows"

SPEC = importlib.util.spec_from_file_location("data_coverage_matrix", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    Path(path).write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def has_float(value):
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(has_float(item) for item in value.values())
    if isinstance(value, list):
        return any(has_float(item) for item in value)
    return False


class DataCoverageMatrixTest(unittest.TestCase):
    def setUp(self):
        self.registry = read(MODULE.REGISTRY_PATH)
        self.regime = read(MODULE.REGIME_PATH)
        self.rules = read(MODULE.RULES_PATH)

    def build_with(self, root, registry=None, regime=None, rules=None):
        registry_path = Path(root) / "registry.json"
        regime_path = Path(root) / "regime.json"
        rules_path = Path(root) / "rules.json"
        write(registry_path, self.registry if registry is None else registry)
        write(regime_path, self.regime if regime is None else regime)
        write(rules_path, self.rules if rules is None else rules)
        return MODULE.build_matrix(
            registry_path=registry_path,
            regime_path=regime_path,
            rules_path=rules_path,
        )

    def test_complete_inventory_preserves_operational_gaps(self):
        matrix = MODULE.build_matrix()

        self.assertTrue(matrix["inventory_complete"])
        self.assertFalse(matrix["operationally_complete"])
        self.assertEqual(matrix["schema_version"], 2)
        self.assertEqual(matrix["contract_version"], "data_coverage_matrix/v2")
        self.assertEqual(
            matrix["dimension_claim_scope"],
            "DECLARED_AUDIT_CLASSIFICATION_ONLY",
        )
        self.assertEqual(
            matrix["runtime_evidence_eligibility"],
            "NOT_AUTHORIZED_BY_THIS_AUDIT",
        )
        self.assertEqual(
            matrix["consumer_counts"],
            {"REGIME": 15, "DISCOVERY": 11, "RULE": 25, "TOTAL": 51},
        )
        self.assertEqual(len(matrix["entries"]), 51)
        self.assertEqual(matrix["gap_count"], 45)
        self.assertEqual(
            matrix["dimension_status_counts"]["source"]["UNRECORDED"], 9
        )
        self.assertEqual(
            matrix["dimension_status_counts"]["cost"]["UNRESOLVED"], 22
        )
        self.assertEqual(matrix["paid_source_reapproval_required_for"], [])
        self.assertFalse(matrix["source_selection_authorized"])
        self.assertFalse(matrix["source_qualification_authorized"])
        self.assertFalse(matrix["freshness_runtime_use_authorized"])
        self.assertFalse(matrix["fallback_runtime_use_authorized"])
        self.assertFalse(matrix["freshness_policy_ratification_authorized"])
        self.assertFalse(matrix["fallback_policy_ratification_authorized"])
        self.assertFalse(matrix["evaluator_wiring_authorized"])
        self.assertFalse(matrix["production_wiring_authorized"])
        self.assertFalse(matrix["trading_action_authorized"])

    def test_regime_and_discovery_omission_or_order_drift_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = copy.deepcopy(self.registry)
            missing["regime_consumers"].pop()
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "REGIME_INVENTORY_INCOMPLETE"
            ):
                self.build_with(tmp, registry=missing)

        with tempfile.TemporaryDirectory() as tmp:
            reordered = copy.deepcopy(self.registry)
            reordered["discovery_consumers"].reverse()
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "DISCOVERY_INVENTORY_INCOMPLETE"
            ):
                self.build_with(tmp, registry=reordered)

    def test_rule_ssot_omission_and_resolved_source_map_drift_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing_rule = copy.deepcopy(self.rules)
            missing_rule["rules"].pop()
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "RULE_INVENTORY_INCOMPLETE"
            ):
                self.build_with(tmp, rules=missing_rule)

        with tempfile.TemporaryDirectory() as tmp:
            missing_ref = copy.deepcopy(self.registry)
            missing_ref["rule_mapping"]["source_refs"].pop()
            with self.assertRaisesRegex(
                MODULE.DataCoverageError,
                "RULE_RESOLVED_SOURCE_MAP_INCOMPLETE",
            ):
                self.build_with(tmp, registry=missing_ref)

    def test_available_rule_without_source_record_stays_unrecorded(self):
        matrix = MODULE.build_matrix()
        entries = {item["consumer_id"]: item for item in matrix["entries"]}

        for rule_id in ("RULE-0019", "RULE-0024", "RULE-0025"):
            self.assertEqual(entries[rule_id]["upstream_state"]["data_status"], "AVAILABLE")
            self.assertIsNone(
                entries[rule_id]["upstream_state"]["source_qualification"]
            )
            self.assertEqual(entries[rule_id]["source"]["status"], "UNRECORDED")
            self.assertEqual(entries[rule_id]["source"]["source_ids"], [])
            self.assertEqual(entries[rule_id]["cost"]["status"], "UNRESOLVED")

    def test_unknown_source_and_missing_source_evidence_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            unknown = copy.deepcopy(self.registry)
            unknown["regime_consumers"][0]["source_ids"] = ["missing_source"]
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "SOURCE_REF_UNKNOWN"
            ):
                self.build_with(tmp, registry=unknown)

        with tempfile.TemporaryDirectory() as tmp:
            missing_evidence = copy.deepcopy(self.registry)
            missing_evidence["sources"][0]["evidence_ref"] = "missing/file.json"
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "SOURCE_EVIDENCE_MISSING"
            ):
                self.build_with(tmp, registry=missing_evidence)

    def test_source_evidence_is_exact_content_and_git_first_seen_bound(self):
        matrix = MODULE.build_matrix()
        catalog = {item["source_id"]: item for item in matrix["source_catalog"]}
        for source in catalog.values():
            provenance = source["verified_evidence_provenance"]
            self.assertEqual(
                provenance["provenance_status"],
                "EXACT_CONTENT_FIRST_SEEN_VERIFIED",
            )
            self.assertEqual(provenance["evidence_sha256"], source["evidence_sha256"])
            self.assertEqual(
                provenance["evidence_first_seen_commit"],
                source["evidence_first_seen_commit"],
            )
            self.assertEqual(
                provenance["evidence_first_seen_at"],
                source["evidence_first_seen_at"],
            )

        for field, value, error in (
            ("evidence_sha256", "0" * 64, "SOURCE_EVIDENCE_HASH_MISMATCH"),
            (
                "evidence_first_seen_commit",
                "0" * 40,
                "SOURCE_EVIDENCE_FIRST_SEEN_MISMATCH",
            ),
            (
                "evidence_first_seen_at",
                "2000-01-01T00:00:00Z",
                "SOURCE_EVIDENCE_FIRST_SEEN_MISMATCH",
            ),
        ):
            with self.subTest(field=field), tempfile.TemporaryDirectory() as tmp:
                tampered = copy.deepcopy(self.registry)
                tampered["sources"][0][field] = value
                with self.assertRaisesRegex(MODULE.DataCoverageError, error):
                    self.build_with(tmp, registry=tampered)

    def test_source_evidence_path_escape_and_rebinding_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            escaped = copy.deepcopy(self.registry)
            escaped["sources"][0]["evidence_ref"] = "../../etc/passwd"
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "SOURCE_EVIDENCE_PATH_INVALID"
            ):
                self.build_with(tmp, registry=escaped)

        with tempfile.TemporaryDirectory() as tmp:
            rebound = copy.deepcopy(self.registry)
            source = rebound["sources"][0]
            other = rebound["sources"][1]
            for field in (
                "evidence_ref",
                "evidence_sha256",
                "evidence_first_seen_commit",
                "evidence_first_seen_at",
            ):
                source[field] = other[field]
            matrix = self.build_with(tmp, registry=rebound)
            self.assertEqual(
                matrix["runtime_evidence_eligibility"],
                "NOT_AUTHORIZED_BY_THIS_AUDIT",
            )
            self.assertFalse(matrix["source_qualification_authorized"])

    def test_paid_cost_remains_reapproval_only_and_never_selects_source(self):
        paid = copy.deepcopy(self.registry)
        source = next(
            item
            for item in paid["sources"]
            if item["source_id"] == "tiingo_us_daily_price"
        )
        source["cost_status"] = "PAID_REAPPROVAL_REQUIRED"

        with tempfile.TemporaryDirectory() as tmp:
            matrix = self.build_with(tmp, registry=paid)

        self.assertIn(
            "REGIME:US:TREND", matrix["paid_source_reapproval_required_for"]
        )
        self.assertIn(
            "DISCOVERY:P3-02", matrix["paid_source_reapproval_required_for"]
        )
        self.assertFalse(matrix["source_selection_authorized"])
        self.assertEqual(
            matrix["paid_source_policy"],
            "USER_REAPPROVAL_REQUIRED_BEFORE_SELECTION_OR_PURCHASE",
        )

    def test_deterministic_float_free_atomic_and_tamper_evident(self):
        first = MODULE.build_matrix()
        second = MODULE.build_matrix()
        self.assertEqual(first, second)
        self.assertFalse(has_float(first))

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "out" / "matrix.json"
            MODULE.write_output(first, target)
            self.assertEqual(read(target), first)
            self.assertFalse(list(target.parent.glob(".*.tmp.*")))

        self.assertEqual(MODULE.validate_matrix(first), first)
        tampered = copy.deepcopy(first)
        tampered["inventory_complete"] = False
        with self.assertRaisesRegex(
            MODULE.DataCoverageError, "MATRIX_DERIVATION_MISMATCH"
        ):
            MODULE.validate_matrix(tampered)

        floating = copy.deepcopy(self.registry)
        floating["regime_consumers"][0]["freshness"]["policy"] = 0.5
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(
                MODULE.DataCoverageError, "FLOAT_NOT_ALLOWED"
            ):
                self.build_with(tmp, registry=floating)

    def test_no_network_workflow_or_tracked_matrix_wiring_is_added(self):
        script = SCRIPT.read_text(encoding="utf-8")
        workflows = "\n".join(
            path.read_text(encoding="utf-8")
            for path in sorted(WORKFLOWS.glob("*.yml"))
        )
        self.assertNotIn("import requests", script)
        self.assertNotIn("import urllib", script)
        self.assertIn('"--reverse", "--format=%H"', script)
        self.assertIn('"status", "--porcelain"', script)
        self.assertNotIn("data_coverage_matrix.py", workflows)
        self.assertFalse((ROOT / "evidence" / "data_coverage_matrix.json").exists())


if __name__ == "__main__":
    unittest.main()
