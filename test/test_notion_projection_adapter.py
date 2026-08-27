import datetime as dt
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


PATH = Path(__file__).parents[1] / ".github/scripts/notion_projection_adapter.py"
SPEC = importlib.util.spec_from_file_location("notion_projection_adapter", PATH)
np = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(np)


CONTENT = {
    "contract_version": "briefing_finalization/17",
    "purpose": "atlas.briefing_finalization.portal_projection",
    "briefing_id": "2026-08-28-am",
    "post_delivery_change_key": "change-1",
    "changed_axes": ["capital"],
    "capital_impact": "NONE",
    "action_taken": "Portal corrected",
    "redelivery": "FORBIDDEN",
}


class FakeClient:
    def __init__(self, rows=None):
        self.rows = list(rows or [])
        self.page = None
        self.created = self.updated = 0

    def find(self, _source, _briefing):
        return self.rows

    def create(self, _source, properties):
        self.created += 1
        self.page = {"id": "page-new", "properties": materialize(properties)}
        return self.page

    def update(self, page_id, properties):
        self.updated += 1
        self.page = {"id": page_id, "properties": materialize(properties)}
        return self.page

    def retrieve(self, _page_id):
        return self.page


def materialize(properties):
    out = {}
    for name, prop in properties.items():
        if "title" in prop:
            out[name] = {"type": "title", "title": [
                {"plain_text": prop["title"][0]["text"]["content"]}]}
        elif "rich_text" in prop:
            out[name] = {"type": "rich_text", "rich_text": [
                {"plain_text": prop["rich_text"][0]["text"]["content"]}]}
        else:
            kind = next(iter(prop))
            out[name] = {"type": kind, kind: prop[kind]}
    return out


class AdapterTests(unittest.TestCase):
    def test_create_readback_then_atomic_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient()
            result = np.project(client, "source", CONTENT, "2026-08-28", "morning",
                                Path(root), dt.datetime(2026, 8, 28, tzinfo=dt.timezone.utc))
            self.assertEqual(client.created, 1)
            self.assertTrue(result["read_after_write_verified"])
            receipt = json.loads(Path(result["receipt_path"]).read_text())
            self.assertEqual(receipt["content_sha256"], np.digest(CONTENT))
            self.assertEqual(receipt["post_delivery_change_key"], "change-1")
            self.assertEqual(list(Path(root).glob("*.tmp")), [])

    def test_existing_briefing_is_updated_not_duplicated(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient([{"id": "page-existing"}])
            result = np.project(client, "source", CONTENT, "2026-08-28", "morning", Path(root))
            self.assertEqual((client.created, client.updated), (0, 1))
            self.assertEqual(result["target"], "page-existing")

    def test_duplicate_briefing_fails_closed(self):
        client = FakeClient([{"id": "a"}, {"id": "b"}])
        with self.assertRaisesRegex(np.ProjectionError, "NOTION_DUPLICATE_BRIEFING_ID"):
            np.project(client, "source", CONTENT, "2026-08-28", "morning", Path("unused"))

    def test_readback_mismatch_emits_no_receipt(self):
        class Bad(FakeClient):
            def retrieve(self, _page_id):
                page = super().retrieve(_page_id)
                page["properties"]["Content SHA256"]["rich_text"][0]["plain_text"] = "bad"
                return page
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(np.ProjectionError, "NOTION_READBACK_MISMATCH"):
                np.project(Bad(), "source", CONTENT, "2026-08-28", "morning", Path(root))
            self.assertEqual(list(Path(root).glob("portal-projection-receipt*")), [])

    def test_latest_receipt_revision_is_append_only(self):
        with tempfile.TemporaryDirectory() as root:
            client = FakeClient()
            first = np.project(client, "source", CONTENT, "2026-08-28", "morning", Path(root))
            second = np.project(client, "source", CONTENT, "2026-08-28", "morning", Path(root))
            self.assertTrue(first["receipt_path"].endswith("rev-001.json"))
            self.assertTrue(second["receipt_path"].endswith("rev-002.json"))


if __name__ == "__main__":
    unittest.main()
