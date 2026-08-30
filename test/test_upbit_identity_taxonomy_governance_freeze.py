#!/usr/bin/env python3
"""P3-12-GOV-03A -- governance/upbit_identity_taxonomy_governance_freeze.py
and config/upbit_identity_taxonomy_governance_freeze.json.

Covers: exact-tuple matching (never broad path/date/prefix), fail-closed on
a malformed/forged/self-inconsistent registry, and the real committed
freeze registers the P3 universe record + inner packet by exact hash.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
REAL_FREEZE_PATH = ROOT / "config" / "upbit_identity_taxonomy_governance_freeze.json"


def _load(name, rel):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FREEZE = _load("gov03a_freeze_module", "governance/upbit_identity_taxonomy_governance_freeze.py")


def _record(source_path="p.json", file_sha="a" * 64, record_sha="b" * 64, inner_sha=None,
            reason="test", effective_from="2026-08-30"):
    return {
        "source_path": source_path,
        "revoked_file_sha256": file_sha,
        "revoked_record_payload_sha256": record_sha,
        "revoked_inner_packet_sha256": inner_sha,
        "reason": reason,
        "effective_from": effective_from,
    }


def _doc(records=None, resolution_status=FREEZE.PENDING_RESOLUTION_STATUS, extra=None):
    doc = {
        "schema_version": FREEZE.SCHEMA_VERSION,
        "resolution_status": resolution_status,
        "records": records if records is not None else [_record()],
        "authority": {
            "identity_authorized": False, "taxonomy_authorized": False,
            "paper_eligible_promotion_authorized": False, "candidate_promotion_authorized": False,
            "paper_exit_authorized": False, "exchange_authorized": False,
            "order_authorized": False, "production_authorized": False,
            "real_capital_authorized": False, "trading_authorized": False,
        },
    }
    if extra:
        doc.update(extra)
    doc["payload_sha256"] = FREEZE.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
    return doc


def released_freeze(records=None):
    """Explicit synthetic RELEASED fixture -- item E's instruction: a future
    release test must inject this, never edit the real committed config."""
    return _doc(records=records, resolution_status="RATIFIED_BY_EXPLICIT_CIO_DECISION")


def _write(tmp_dir, doc):
    path = Path(tmp_dir) / "freeze.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


class RealFreezeRegistryTests(unittest.TestCase):
    def test_real_committed_freeze_loads_and_self_validates(self):
        doc = FREEZE.load_freeze()
        self.assertEqual(doc["resolution_status"], FREEZE.PENDING_RESOLUTION_STATUS)
        self.assertGreaterEqual(len(doc["records"]), 3)

    def test_real_committed_freeze_authority_all_false(self):
        doc = FREEZE.load_freeze()
        for value in doc["authority"].values():
            self.assertIs(value, False)

    def test_real_p3_universe_record_registered_by_exact_hash(self):
        doc = FREEZE.load_freeze()
        self.assertTrue(FREEZE.is_frozen(
            "data/observations/upbit_tradeable_universe/2026-08-30/packet.json",
            file_sha256="485c62d534ebeb82a97a5d3b64159fa3c5b40b0cca42e1b70b8f3fadb3035530",
            freeze=doc,
        ))
        self.assertTrue(FREEZE.is_frozen(
            "anywhere", record_payload_sha256="a9be9c63f9a39d1afbfd282a5707e797a7db61138edc9538b7ccf4a6a43d2d12",
            freeze=doc,
        ))
        self.assertTrue(FREEZE.is_frozen(
            "anywhere", inner_packet_sha256="3ba2721dec6ff574b0e1652fd4d8712259d17797aa2f60f8ba022020ff702c3f",
            freeze=doc,
        ))

    def test_not_released_by_default(self):
        self.assertFalse(FREEZE.is_released())


class ExactMatchOnlyTests(unittest.TestCase):
    """Section G.4/G.5: neither half of the tuple alone is sufficient."""

    def test_same_path_different_file_hash_not_revoked(self):
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64, record_sha=None)])
        self.assertFalse(FREEZE.is_frozen("a/b.json", file_sha256="c" * 64, freeze=doc))

    def test_different_path_same_file_hash_not_revoked(self):
        # revoked_file_sha256 is scoped by source_path -- an unrelated file
        # elsewhere sharing the same raw bytes is never swept in.
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64, record_sha=None)])
        self.assertFalse(FREEZE.is_frozen("a/other.json", file_sha256="a" * 64, freeze=doc))

    def test_exact_file_hash_and_path_match_is_frozen(self):
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64, record_sha=None)])
        self.assertTrue(FREEZE.is_frozen("a/b.json", file_sha256="a" * 64, freeze=doc))

    def test_record_payload_hash_matches_regardless_of_path(self):
        # By design (content-addressed retention), record/inner-packet
        # content-identity hashes are path-independent.
        doc = _doc([_record(source_path="a/b.json", file_sha=None, record_sha="d" * 64)])
        self.assertTrue(FREEZE.is_frozen("completely/different/path.json", record_payload_sha256="d" * 64, freeze=doc))

    def test_unrelated_hash_not_frozen(self):
        doc = _doc([_record(source_path="a/b.json", file_sha="a" * 64, record_sha="d" * 64)])
        self.assertFalse(FREEZE.is_frozen("a/b.json", file_sha256="9" * 64, record_payload_sha256="9" * 64, freeze=doc))


class FailClosedLoadTests(unittest.TestCase):
    def test_forged_self_hash_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            doc["payload_sha256"] = "f" * 64
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "SELF_HASH_MISMATCH"):
                FREEZE.load_freeze(path)

    def test_tampered_record_after_self_hash_computed_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            doc["records"][0]["revoked_file_sha256"] = "9" * 64
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "SELF_HASH_MISMATCH"):
                FREEZE.load_freeze(path)

    def test_missing_schema_version_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            del doc["schema_version"]
            doc["payload_sha256"] = FREEZE.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "SCHEMA_VERSION_MISMATCH"):
                FREEZE.load_freeze(path)

    def test_authority_true_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc()
            doc["authority"]["order_authorized"] = True
            doc["payload_sha256"] = FREEZE.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "AUTHORITY_INVARIANT_VIOLATED"):
                FREEZE.load_freeze(path)

    def test_empty_records_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            doc = _doc(records=[])
            doc["payload_sha256"] = FREEZE.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "RECORDS_EMPTY_OR_INVALID"):
                FREEZE.load_freeze(path)

    def test_missing_required_record_field_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _record()
            del record["reason"]
            doc = _doc(records=[record])
            doc["payload_sha256"] = FREEZE.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "RECORD_FIELDS_INVALID"):
                FREEZE.load_freeze(path)

    def test_record_with_no_hash_identity_at_all_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            record = _record(file_sha=None, record_sha=None, inner_sha=None)
            doc = _doc(records=[record])
            doc["payload_sha256"] = FREEZE.payload_sha256({k: v for k, v in doc.items() if k != "payload_sha256"})
            path = _write(tmp, doc)
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "RECORD_NO_HASH_IDENTITY"):
                FREEZE.load_freeze(path)

    def test_malformed_json_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "freeze.json"
            path.write_text("{not json", encoding="utf-8")
            with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "READ_FAILED"):
                FREEZE.load_freeze(path)

    def test_missing_file_fails(self):
        with self.assertRaisesRegex(FREEZE.GovernanceFreezeError, "READ_FAILED"):
            FREEZE.load_freeze(Path("/definitely/missing/freeze.json"))


class SyntheticReleaseFixtureTests(unittest.TestCase):
    """Item E's release-testing instruction: inject a synthetic RELEASED
    fixture explicitly -- never edit the real committed config to test this."""

    def test_synthetic_released_fixture_reports_released(self):
        doc = released_freeze()
        self.assertTrue(FREEZE.is_released(doc))

    def test_pending_default_reports_not_released(self):
        doc = _doc()
        self.assertFalse(FREEZE.is_released(doc))

    def test_released_fixture_still_blocks_its_own_registered_records(self):
        # Releasing the OVERALL registry does not un-register any record
        # still listed in it -- a record's presence is itself the block.
        doc = released_freeze([_record(source_path="a/b.json", file_sha="a" * 64, record_sha=None)])
        self.assertTrue(FREEZE.is_released(doc))
        self.assertTrue(FREEZE.is_frozen("a/b.json", file_sha256="a" * 64, freeze=doc))


if __name__ == "__main__":
    unittest.main()
