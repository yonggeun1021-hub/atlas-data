#!/usr/bin/env python3
"""P3-12-GOV-02B -- config/upbit_governance_revocations.json + its loader.

Covers: the registry only ever matches by an EXACT (source_path, hash)
tuple (never a broad date/prefix pattern), and a forged or self-inconsistent
registry fails closed rather than being silently trusted or silently
treated as empty.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REAL_REGISTRY_PATH = ROOT / "config" / "upbit_governance_revocations.json"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GOV = _load("gov_revocations_module", "governance/upbit_governance_revocations.py")


def _record(source_path="p.json", file_sha="a" * 64, record_sha="b" * 64, inner_sha=None,
            effective_from="2026-08-30", affected_lineage="test"):
    return {
        "source_path": source_path,
        "revoked_file_sha256": file_sha,
        "revoked_record_payload_sha256": record_sha,
        "revoked_inner_packet_sha256": inner_sha,
        "effective_from": effective_from,
        "affected_lineage": affected_lineage,
    }


def _doc(records=None):
    doc = {
        "schema_version": GOV.SCHEMA_VERSION,
        "approval_status": GOV.APPROVAL_STATUS,
        "revoked_at_utc": "2026-08-30T05:32:49Z",
        "reason": "test",
        "source_merge_commit": "0" * 40,
        "source_pr": 465,
        "records": records if records is not None else [_record()],
        "authority": {
            "review_only": True,
            "identity_ratification_authorized": False,
            "taxonomy_ratification_authorized": False,
            "policy_ratification_authorized": False,
            "tradeable_universe_promotion_authorized": False,
            "paper_eligible_promotion_authorized": False,
            "decision_eligible": False,
            "action_generation_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    doc["payload_sha256"] = GOV.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
    return doc


def _write(tmp_dir, doc):
    path = Path(tmp_dir) / "registry.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class RealRegistryTests(unittest.TestCase):
    def test_real_committed_registry_loads_and_self_validates(self):
        doc = GOV.load_revocations()
        self.assertEqual(doc["approval_status"], "CIO_REVOKED_FAIL_CLOSED")
        self.assertEqual(doc["source_pr"], 465)
        self.assertGreaterEqual(len(doc["records"]), 4)

    def test_real_committed_registry_authority_all_false(self):
        doc = GOV.load_revocations()
        for field, value in doc["authority"].items():
            if field != "review_only":
                self.assertIs(value, False, field)

    def test_real_universe_record_anchor_is_registered_revoked(self):
        doc = GOV.load_revocations()
        self.assertTrue(GOV.is_revoked(
            "data/observations/upbit_tradeable_universe/2026-08-30/packet.json",
            "485c62d534ebeb82a97a5d3b64159fa3c5b40b0cca42e1b70b8f3fadb3035530",
            revocations=doc,
        ))

    def test_real_reverted_bytes_are_not_revoked(self):
        # The CURRENT (post-revert) file at that same path must never be
        # treated as revoked -- only the specific old bytes are.
        doc = GOV.load_revocations()
        current_path = ROOT / "data/observations/upbit_tradeable_universe/2026-08-30/packet.json"
        current_hash = hashlib.sha256(current_path.read_bytes()).hexdigest()
        self.assertFalse(GOV.is_revoked(
            "data/observations/upbit_tradeable_universe/2026-08-30/packet.json",
            current_hash, revocations=doc,
        ))


class ExactMatchOnlyTests(unittest.TestCase):
    """Section E.5: broad path/date match never exempts a different packet."""

    def test_same_path_different_hash_not_revoked(self):
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64)])
        self.assertFalse(GOV.is_revoked("a/b.json", "c" * 64, revocations=doc))

    def test_same_hash_different_path_not_revoked(self):
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64)])
        self.assertFalse(GOV.is_revoked("a/other.json", "a" * 64, revocations=doc))

    def test_prefix_of_a_revoked_path_is_not_itself_revoked(self):
        doc = _doc([_record(source_path="evidence/crypto_paper_decision/2026-08-30/0411/x/packet.json", file_sha="a" * 64)])
        self.assertFalse(GOV.is_revoked("evidence/crypto_paper_decision/2026-08-30/0411/y/packet.json", "a" * 64, revocations=doc))

    def test_same_date_directory_sibling_file_not_revoked(self):
        doc = _doc([_record(source_path="evidence/crypto_paper_decision/2026-08-30/0411/x/packet.json", file_sha="a" * 64)])
        # A different generation_id under the SAME date/hhmm directory must
        # never be swept in by a date-based heuristic.
        self.assertFalse(GOV.is_revoked("evidence/crypto_paper_decision/2026-08-30/0411/zzzzz/packet.json", "a" * 64, revocations=doc))

    def test_exact_match_is_revoked(self):
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64)])
        self.assertTrue(GOV.is_revoked("a/b.json", "a" * 64, revocations=doc))


class FailClosedLoadTests(unittest.TestCase):
    def test_forged_self_hash_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            doc["payload_sha256"] = "f" * 64
            path = _write(tmp, doc)
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "SELF_HASH_MISMATCH"):
                GOV.load_revocations(path)

    def test_tampered_record_after_self_hash_computed_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            # Mutate a record's hash post-hoc without recomputing payload_sha256 --
            # simulates a forged/edited registry.
            doc["records"][0]["revoked_file_sha256"] = "9" * 64
            path = _write(tmp, doc)
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "SELF_HASH_MISMATCH"):
                GOV.load_revocations(path)

    def test_wrong_approval_status_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            doc["approval_status"] = "SOMETHING_ELSE"
            doc["payload_sha256"] = GOV.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "APPROVAL_STATUS_INVALID"):
                GOV.load_revocations(path)

    def test_authority_true_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            doc["authority"]["order_authorized"] = True
            doc["payload_sha256"] = GOV.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "AUTHORITY_INVARIANT_VIOLATED"):
                GOV.load_revocations(path)

    def test_empty_records_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc(records=[])
            doc["payload_sha256"] = GOV.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "RECORDS_EMPTY_OR_INVALID"):
                GOV.load_revocations(path)

    def test_missing_required_field_in_record_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _record()
            del record["affected_lineage"]
            doc = _doc(records=[record])
            doc["payload_sha256"] = GOV.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "RECORD_FIELDS_INVALID"):
                GOV.load_revocations(path)

    def test_malformed_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registry.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "READ_FAILED"):
                GOV.load_revocations(path)

    def test_missing_file_fails(self):
        with self.assertRaisesRegex(GOV.GovernanceRevocationsError, "READ_FAILED"):
            GOV.load_revocations(Path("/definitely/missing/revocations.json"))


if __name__ == "__main__":
    unittest.main()
