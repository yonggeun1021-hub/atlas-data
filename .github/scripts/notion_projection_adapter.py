#!/usr/bin/env python3
"""Fail-closed Atlas Finalization projection into Notion.

The adapter upserts exactly one row per briefing_id, reads the row back, and
only then atomically publishes a projection receipt.  It never delivers a
briefing to the user and never grants trading, order, action, or capital
authority.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.error
import urllib.request


API_ROOT = "https://api.notion.com/v1"
ADAPTER = "notion_cockpit"


class ProjectionError(RuntimeError):
    pass


def canonical(value: dict) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value: dict) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def rich_text(value: str) -> dict:
    return {"rich_text": [{"type": "text", "text": {"content": value}}]}


def plain_text(prop: dict) -> str:
    kind = prop.get("type")
    rows = prop.get(kind, []) if kind in {"title", "rich_text"} else []
    return "".join(row.get("plain_text", "") for row in rows)


class NotionClient:
    def __init__(self, token: str, api_version: str, opener=None) -> None:
        if not token.strip():
            raise ProjectionError("NOTION_TOKEN_REQUIRED")
        self.token = token
        self.api_version = api_version
        self.opener = opener or urllib.request.urlopen

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = canonical(body) if body is not None else None
        request = urllib.request.Request(
            f"{API_ROOT}{path}", data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}",
                     "Notion-Version": self.api_version,
                     "Content-Type": "application/json"})
        try:
            with self.opener(request, timeout=30) as response:
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise ProjectionError(f"NOTION_HTTP_{exc.code}:{detail}") from None
        except (OSError, ValueError) as exc:
            raise ProjectionError(f"NOTION_TRANSPORT_OR_JSON:{type(exc).__name__}") from None
        if not isinstance(result, dict):
            raise ProjectionError("NOTION_RESPONSE_NOT_OBJECT")
        return result

    def find(self, data_source_id: str, briefing_id: str) -> list[dict]:
        body = {"filter": {"property": "Briefing ID", "title": {"equals": briefing_id}},
                "page_size": 3}
        response = self.request("POST", f"/data_sources/{data_source_id}/query", body)
        rows = response.get("results")
        if not isinstance(rows, list):
            raise ProjectionError("NOTION_QUERY_RESULTS_INVALID")
        return rows

    def create(self, data_source_id: str, properties: dict) -> dict:
        return self.request("POST", "/pages", {
            "parent": {"type": "data_source_id", "data_source_id": data_source_id},
            "properties": properties,
        })

    def update(self, page_id: str, properties: dict) -> dict:
        return self.request("PATCH", f"/pages/{page_id}", {"properties": properties})

    def retrieve(self, page_id: str) -> dict:
        return self.request("GET", f"/pages/{page_id}")


def projection_properties(content: dict, content_sha: str, written_at: str,
                          decision_date: str, slot: str) -> dict:
    impact = content.get("capital_impact") or "UNKNOWN"
    if impact not in {"NONE", "PRESENT", "UNKNOWN"}:
        raise ProjectionError("CAPITAL_IMPACT_INVALID")
    if slot not in {"morning", "evening"}:
        raise ProjectionError("SLOT_INVALID")
    return {
        "Briefing ID": {"title": [{"type": "text", "text": {"content": content["briefing_id"]}}]},
        "Content SHA256": rich_text(content_sha),
        "Contract Version": rich_text(str(content.get("contract_version", ""))),
        "Purpose": rich_text(str(content.get("purpose", ""))),
        "Decision Date": {"date": {"start": decision_date}},
        "Slot": {"select": {"name": slot}},
        "Capital Impact": {"select": {"name": impact}},
        "Projection Status": {"select": {"name": "CURRENT"}},
        "Canonical JSON": rich_text(canonical(content).decode("utf-8")),
        "Written At UTC": {"date": {"start": written_at}},
    }


def verify_readback(page: dict, content: dict, expected_sha: str) -> None:
    props = page.get("properties")
    if not isinstance(props, dict):
        raise ProjectionError("NOTION_READBACK_PROPERTIES_MISSING")
    checks = {
        "Briefing ID": content["briefing_id"],
        "Content SHA256": expected_sha,
        "Contract Version": str(content.get("contract_version", "")),
        "Purpose": str(content.get("purpose", "")),
        "Canonical JSON": canonical(content).decode("utf-8"),
    }
    for name, expected in checks.items():
        if plain_text(props.get(name, {})) != expected:
            raise ProjectionError(f"NOTION_READBACK_MISMATCH:{name}")


def next_receipt_path(directory: Path) -> Path:
    revisions = []
    for path in directory.glob("portal-projection-receipt-rev-*.json"):
        suffix = path.stem.rsplit("-", 1)[-1]
        if suffix.isdigit():
            revisions.append(int(suffix))
    return directory / f"portal-projection-receipt-rev-{max(revisions, default=0) + 1:03d}.json"


def atomic_receipt(directory: Path, receipt: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = next_receipt_path(directory)
    fd, temp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=directory)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(canonical(receipt) + b"\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()
    return target


def project(client: NotionClient, data_source_id: str, content: dict,
            decision_date: str, slot: str, receipt_dir: Path | None = None,
            now: dt.datetime | None = None) -> dict:
    required = {"contract_version", "purpose", "briefing_id"}
    if not required.issubset(content):
        raise ProjectionError("PROJECTION_CONTENT_INCOMPLETE")
    expected_sha = digest(content)
    written_at = (now or dt.datetime.now(dt.timezone.utc)).astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    rows = client.find(data_source_id, content["briefing_id"])
    if len(rows) > 1:
        raise ProjectionError("NOTION_DUPLICATE_BRIEFING_ID")
    properties = projection_properties(content, expected_sha, written_at, decision_date, slot)
    page = (client.update(rows[0]["id"], properties) if rows
            else client.create(data_source_id, properties))
    page_id = page.get("id")
    if not page_id:
        raise ProjectionError("NOTION_PAGE_ID_MISSING")
    readback = client.retrieve(page_id)
    verify_readback(readback, content, expected_sha)
    result = {"adapter": ADAPTER, "target": page_id, "written_at_utc": written_at,
              "content_sha256": expected_sha, "read_after_write_verified": True}
    change_key = content.get("post_delivery_change_key")
    if change_key:
        receipt = {**result, "post_delivery_change_key": change_key}
        if receipt_dir is None:
            raise ProjectionError("RECEIPT_DIR_REQUIRED_FOR_POST_DELIVERY_CHANGE")
        result["receipt_path"] = str(atomic_receipt(receipt_dir, receipt))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--content", required=True)
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--slot", required=True, choices=("morning", "evening"))
    parser.add_argument("--receipt-dir")
    parser.add_argument("--config", default="config/atlas_projection.json")
    args = parser.parse_args()
    config = json.loads(Path(args.config).read_text(encoding="utf-8"))["portal"]
    if not config.get("implemented"):
        raise ProjectionError("PORTAL_ADAPTER_NOT_ACTIVATED_AFTER_LIVE_CANARY")
    content = json.loads(Path(args.content).read_text(encoding="utf-8"))
    client = NotionClient(os.environ.get("NOTION_TOKEN", ""), config["api_version"])
    result = project(client, config["data_source_id"], content, args.decision_date,
                     args.slot, Path(args.receipt_dir) if args.receipt_dir else None)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
