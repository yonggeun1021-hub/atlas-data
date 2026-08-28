import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


PATH = Path(__file__).parents[1] / ".github/scripts/notion_projection_adapter.py"
SPEC = importlib.util.spec_from_file_location("notion_projection_adapter", PATH)
np = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(np)


CHANGE_KEY = "a" * 64
CONTENT = {
    "contract_version": "briefing_finalization/17",
    "purpose": "atlas.briefing_finalization.portal_projection",
    "briefing_id": "2026-08-28-am",
    "post_delivery_change_key": CHANGE_KEY,
    "changed_axes": ["briefing_sha256"],
    "capital_impact": "NONE",
    "action_taken": "Portal corrected",
    "redelivery": "FORBIDDEN",
}


def materialize(properties):
    out = {}
    for name, prop in properties.items():
        if "title" in prop:
            out[name] = {"type": "title", "title": [
                {"plain_text": row["text"]["content"]} for row in prop["title"]]}
        elif "rich_text" in prop:
            out[name] = {"type": "rich_text", "rich_text": [
                {"plain_text": row["text"]["content"]} for row in prop["rich_text"]]}
        else:
            kind = next(iter(prop))
            out[name] = {"type": kind, kind: copy.deepcopy(prop[kind])}
    return out


class FakeClient:
    def __init__(self, duplicate=False):
        self.page = None
        self.created = self.updated = self.retrieved = 0
        self.duplicate = duplicate
        self.sleeper = lambda _seconds: None

    def find(self, _source, _briefing):
        if self.duplicate:
            return [{"id": "page-a"}, {"id": "page-b"}]
        return [{"id": self.page["id"]}] if self.page else []

    def create(self, _source, properties):
        self.created += 1
        self.page = {"id": "page-new", "properties": materialize(properties)}
        return copy.deepcopy(self.page)

    def update(self, page_id, properties):
        self.updated += 1
        self.page = {"id": page_id, "properties": materialize(properties)}
        return copy.deepcopy(self.page)

    def retrieve(self, _page_id):
        self.retrieved += 1
        return copy.deepcopy(self.page)


def read(path):
    return json.loads(Path(path).read_text())


class AdapterTests(unittest.TestCase):
    def test_create_readback_then_atomic_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient()
            result = np.project(client, "source", CONTENT, "2026-08-28", "morning",
                                Path(root), dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc))
            self.assertEqual(client.created, 1)
            self.assertEqual(result["operation"], "CREATED")
            self.assertTrue(result["read_after_write_verified"])
            receipt = read(result["receipt_path"])
            self.assertEqual(receipt["content_sha256"], np.digest(CONTENT))
            self.assertEqual(receipt["post_delivery_change_key"], CHANGE_KEY)
            self.assertEqual(receipt["authority"]["trading"], False)
            self.assertEqual(list(Path(root).glob("*.tmp")), [])

    def test_retry_is_no_change_and_reuses_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient()
            first = np.project(client, "source", CONTENT, "2026-08-28", "morning", Path(root))
            second = np.project(client, "source", CONTENT, "2026-08-28", "morning", Path(root))
            self.assertEqual((client.created, client.updated), (1, 0))
            self.assertEqual(second["operation"], "NO_CHANGE")
            self.assertTrue(second["receipt_reused"])
            self.assertEqual(first["receipt_path"], second["receipt_path"])
            self.assertEqual(len(list(Path(root).glob("portal-projection-receipt-*.json"))), 1)

    def test_changed_ruling_updates_same_briefing_and_new_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient()
            first = np.project(client, "source", CONTENT, "2026-08-28", "morning", Path(root))
            changed = {**CONTENT, "capital_impact": "PRESENT",
                       "action_taken": "Portal corrected; user alert required"}
            second = np.project(client, "source", changed, "2026-08-28", "morning", Path(root))
            self.assertEqual((client.created, client.updated), (1, 1))
            self.assertEqual(first["target"], second["target"])
            self.assertEqual(second["operation"], "UPDATED")
            self.assertTrue(second["receipt_path"].endswith("rev-002.json"))

    def test_duplicate_briefing_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(np.ProjectionError, "NOTION_DUPLICATE_BRIEFING_ID"):
                np.project(FakeClient(duplicate=True), "source", CONTENT,
                           "2026-08-28", "morning", Path(root))

    def test_query_create_race_is_detected(self):
        class Racing(FakeClient):
            def find(self, source, briefing):
                rows = super().find(source, briefing)
                return rows + ([{"id": "racer"}] if self.page else [])
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(np.ProjectionError, "UNIQUENESS_VIOLATION"):
                np.project(Racing(), "source", CONTENT, "2026-08-28", "morning", Path(root))

    def test_readback_mismatch_emits_no_receipt(self):
        class Bad(FakeClient):
            def retrieve(self, page_id):
                page = super().retrieve(page_id)
                page["properties"]["Content SHA256"]["rich_text"][0]["plain_text"] = "bad"
                return page
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(np.ProjectionError, "NOTION_READBACK_MISMATCH"):
                np.project(Bad(), "source", CONTENT, "2026-08-28", "morning", Path(root))
            self.assertEqual(list(Path(root).glob("portal-projection-receipt*")), [])

    def test_all_indexed_fields_are_verified(self):
        client = FakeClient()
        props = np.projection_properties(CONTENT, np.digest(CONTENT),
                                         "2026-08-28T00:00:00Z",
                                         "2026-08-28", "morning")
        client.page = {"id": "page", "properties": materialize(props)}
        client.page["properties"]["Slot"]["select"]["name"] = "evening"
        with self.assertRaisesRegex(np.ProjectionError, "NOTION_READBACK_MISMATCH:Slot"):
            np.verify_readback(client.page, CONTENT, np.digest(CONTENT),
                               "2026-08-28", "morning")

    def test_written_at_uses_notion_round_trip_precision_and_semantic_instant(self):
        self.assertEqual(
            np._utc_iso(dt.datetime(2026, 8, 28, 0, 48, 29, 999,
                                    tzinfo=dt.timezone.utc)),
            "2026-08-28T00:48:00Z")
        props = np.projection_properties(
            CONTENT, np.digest(CONTENT), "2026-08-28T00:48:00Z",
            "2026-08-28", "morning")
        page = {"properties": materialize(props)}
        page["properties"]["Written At UTC"]["date"]["start"] = (
            "2026-08-28T00:48:00.000+00:00")
        np.verify_readback(page, CONTENT, np.digest(CONTENT),
                           "2026-08-28", "morning", "2026-08-28T00:48:00Z")
        with self.assertRaisesRegex(np.ProjectionError, "Written At UTC"):
            np.verify_readback(page, CONTENT, np.digest(CONTENT),
                               "2026-08-28", "morning", "2026-08-28T00:49:00Z")

    def test_bad_latest_receipt_can_be_recovered_append_only(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            client = FakeClient()
            first = np.project(client, "source", CONTENT, "2026-08-28", "morning", directory)
            bad = {**read(first["receipt_path"]), "content_sha256": "bad"}
            np.atomic_receipt(directory, "portal-projection-receipt", bad)
            recovered = np.project(client, "source", CONTENT, "2026-08-28", "morning", directory)
            self.assertFalse(recovered["receipt_reused"])
            self.assertTrue(recovered["receipt_path"].endswith("rev-003.json"))
            self.assertEqual(read(recovered["receipt_path"])["content_sha256"], np.digest(CONTENT))

    def test_unreadable_receipt_fails_closed(self):
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            client = FakeClient()
            np.project(client, "source", CONTENT, "2026-08-28", "morning", directory)
            (directory / "portal-projection-receipt-rev-002.json").write_text("{")
            with self.assertRaisesRegex(np.ProjectionError, "PROJECTION_RECEIPT_UNREADABLE"):
                np.project(client, "source", CONTENT, "2026-08-28", "morning", directory)

    def test_long_canonical_json_is_split_without_hash_change(self):
        content = {**CONTENT, "action_taken": "x" * 5000}
        props = np.projection_properties(content, np.digest(content),
                                         "2026-08-28T00:00:00Z",
                                         "2026-08-28", "morning")
        self.assertGreater(len(props["Canonical JSON"]["rich_text"]), 1)
        page = {"properties": materialize(props)}
        np.verify_readback(page, content, np.digest(content), "2026-08-28", "morning")

    def test_authority_escalation_is_rejected(self):
        unsafe = {**CONTENT, "safety_attestation": {"trading_authority": True}}
        with self.assertRaisesRegex(np.ProjectionError, "AUTHORITY_ESCALATION"):
            np.validate_content(unsafe, "2026-08-28", "morning")

    def test_post_delivery_redelivery_is_rejected(self):
        unsafe = {**CONTENT, "redelivery": "ALLOWED"}
        with self.assertRaisesRegex(np.ProjectionError, "REDELIVERY_FORBIDDEN"):
            np.validate_content(unsafe, "2026-08-28", "morning")

    def test_identity_mismatch_is_rejected(self):
        with self.assertRaisesRegex(np.ProjectionError, "IDENTITY_MISMATCH"):
            np.validate_content(CONTENT, "2026-08-28", "evening")

    def test_schema_is_exactly_checked(self):
        schema = {"properties": {name: {"type": kind}
                                 for name, kind in np.REQUIRED_SCHEMA.items()}}
        for name, options in np.REQUIRED_SELECT_OPTIONS.items():
            schema["properties"][name]["select"] = {
                "options": [{"name": option} for option in sorted(options)]}
        np.verify_schema(schema)
        schema["properties"]["Canonical JSON"]["type"] = "number"
        with self.assertRaisesRegex(np.ProjectionError, "SCHEMA_MISMATCH"):
            np.verify_schema(schema)

        schema["properties"]["Canonical JSON"]["type"] = "rich_text"
        schema["properties"]["Slot"]["select"]["options"].pop()
        with self.assertRaisesRegex(np.ProjectionError, "SELECT_OPTIONS_MISMATCH"):
            np.verify_schema(schema)

    def test_policy_activates_both_flags_only_after_ci_canary(self):
        config = json.loads((Path(__file__).parents[1]
                             / "config/atlas_projection.json").read_text())["portal"]
        self.assertIs(config["implemented"], True)
        self.assertIs(config["verified_against_live_api"], True)
        self.assertEqual(config["receipt_authority"], "LATEST_REVISION_PER_CHANGE")
        self.assertEqual(config["post_delivery_redelivery"], "FORBIDDEN")

    def test_initial_projection_uses_exact_sealed_payload_and_no_authority(self):
        class BF:
            @staticmethod
            def resolve_validation(_directory):
                return None, None

            @staticmethod
            def load_semantic_validator_policy(_root):
                return {"expected": False}

        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            directory = repo / "data/briefing/finalization/2026-08-28/morning"
            directory.mkdir(parents=True)
            payload = b"# exact final briefing\n"
            payload_sha = hashlib.sha256(payload).hexdigest()
            draft = {
                "contract_version": "briefing_finalization/17",
                "briefing_id": "2026-08-28-am",
                "rev": 1,
                "delivery_payload_sha256": payload_sha,
                "delivery_marker": "marker",
                "source": {"revision": 1, "briefing_sha256": "b" * 64},
                "source_fingerprint": {"briefing_sha256": "b" * 64},
            }
            (directory / "draft-rev-001.json").write_text(json.dumps(draft))
            (directory / "payload-rev-001.md").write_bytes(payload)
            content = np.initial_projection_content(repo, "2026-08-28", "morning", bf=BF)
            self.assertEqual(content["delivery_payload_markdown"], payload.decode())
            self.assertEqual(content["delivery_payload_sha256"], payload_sha)
            self.assertFalse(content["safety_attestation"]["trading_authority"])

    def test_semantic_pass_cannot_reach_notion_before_portal_receipt(self):
        class BF:
            @staticmethod
            def resolve_validation(_directory):
                return ({"routing": {"status_deliverable": True}}, None)

            @staticmethod
            def load_ratified_specs(_root):
                return set()

            @staticmethod
            def derive_routing(_validation, _specs):
                return {"status_deliverable": True}

            @staticmethod
            def verify_pre_delivery_portal_receipt(*_args, **_kwargs):
                raise RuntimeError("PORTAL_FINAL_RECEIPT_MISSING")

        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            directory = repo / "data/briefing/finalization/2026-08-31/morning"
            directory.mkdir(parents=True)
            payload = b"# exact final briefing\n"
            payload_sha = hashlib.sha256(payload).hexdigest()
            draft = {
                "contract_version": "briefing_finalization/18",
                "briefing_id": "2026-08-31-am",
                "rev": 1,
                "delivery_payload_sha256": payload_sha,
                "delivery_marker": "marker",
                "source": {"revision": 1, "briefing_sha256": "b" * 64},
                "source_fingerprint": {"briefing_sha256": "b" * 64},
            }
            (directory / "draft-rev-001.json").write_text(json.dumps(draft))
            (directory / "payload-rev-001.md").write_bytes(payload)
            with self.assertRaisesRegex(RuntimeError, "PORTAL_FINAL_RECEIPT_MISSING"):
                np.initial_projection_content(repo, "2026-08-31", "morning", bf=BF)

    def test_legacy_portal_bootstrap_is_a_projection_candidate_without_redelivery(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            bootstrap = repo / "data/briefing/portal_bootstrap"
            bootstrap.mkdir(parents=True)
            content = {
                "contract_version": "atlas_portal_bootstrap/1",
                "purpose": np.BOOTSTRAP_PURPOSE,
                "briefing_id": "2026-08-28-am",
                "decision_date": "2026-08-28",
                "slot": "morning",
                "capital_impact": "UNKNOWN",
                "redelivery": "FORBIDDEN",
                "portal_snapshot": {"page_id": "legacy-page", "summary": "exact"},
                "safety_attestation": {"trading_authority": False},
            }
            (bootstrap / "2026-08-28-am.json").write_text(
                json.dumps(content), encoding="utf-8")
            candidates = np.projection_candidates(
                repo, only_date="2026-08-28", only_slot="morning")
            self.assertEqual(len(candidates), 1)
            date, slot, actual, receipt_dir = candidates[0]
            self.assertEqual((date, slot, actual), ("2026-08-28", "morning", content))
            self.assertEqual(
                receipt_dir,
                repo / "data/briefing/finalization/2026-08-28/morning")
            self.assertNotIn("post_delivery_change_key", actual)

    def test_bootstrap_filename_and_identity_must_match(self):
        with tempfile.TemporaryDirectory() as root:
            repo = Path(root)
            bootstrap = repo / "data/briefing/portal_bootstrap"
            bootstrap.mkdir(parents=True)
            content = {
                "contract_version": "atlas_portal_bootstrap/1",
                "purpose": np.BOOTSTRAP_PURPOSE,
                "briefing_id": "2026-08-28-am",
                "decision_date": "2026-08-28",
                "slot": "morning",
                "redelivery": "FORBIDDEN",
                "portal_snapshot": {},
            }
            (bootstrap / "wrong-name.json").write_text(
                json.dumps(content), encoding="utf-8")
            with self.assertRaisesRegex(np.ProjectionError, "BOOTSTRAP_IDENTITY_INVALID"):
                np.projection_candidates(repo)

    def test_live_canary_requires_no_change_replay_and_receipt_reuse(self):
        good = [{"operation": "NO_CHANGE", "receipt_reused": True,
                 "read_after_write_verified": True}]
        np.verify_canary_replay(good)
        with self.assertRaisesRegex(np.ProjectionError, "REPLAY_WROTE_AGAIN"):
            np.verify_canary_replay([{**good[0], "operation": "UPDATED"}])
        with self.assertRaisesRegex(np.ProjectionError, "RECEIPT_NOT_REUSED"):
            np.verify_canary_replay([{**good[0], "receipt_reused": False}])
        with self.assertRaisesRegex(np.ProjectionError, "NO_CANDIDATE"):
            np.verify_canary_replay([])

    def test_sync_does_not_walk_back_through_proven_superseded_content(self):
        initial = {
            "contract_version": "briefing_finalization/17",
            "purpose": np.INITIAL_PURPOSE,
            "briefing_id": "2026-08-28-am",
            "capital_impact": "UNKNOWN",
        }
        with tempfile.TemporaryDirectory() as root:
            directory = Path(root)
            client = FakeClient()
            np.project(client, "source", initial, "2026-08-28", "morning", directory)
            np.project(client, "source", CONTENT, "2026-08-28", "morning", directory)
            client.updated = 0
            candidates = [
                ("2026-08-28", "morning", initial, directory),
                ("2026-08-28", "morning", CONTENT, directory),
            ]
            with mock.patch.object(np, "projection_candidates", return_value=candidates):
                result = np.sync(client, "source", directory)
            self.assertEqual(client.updated, 0)
            self.assertEqual(
                result["projected"][0]["operation"],
                "SUPERSEDED_RECEIPT_ALREADY_AUTHORITATIVE")
            self.assertEqual(result["projected"][1]["operation"], "NO_CHANGE")


if __name__ == "__main__":
    unittest.main()
