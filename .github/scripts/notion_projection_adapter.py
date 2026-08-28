#!/usr/bin/env python3
"""Fail-closed Atlas Finalization projection into Notion.

The adapter projects the exact machine-canonical JSON into the Atlas Briefing
SSOT data source. A successful operation means all of the following are true:

* the target schema is the reviewed Atlas Briefing SSOT schema;
* exactly one page exists for the briefing id;
* the canonical JSON and every indexed property were read back exactly;
* an append-only local receipt was atomically published after verification.

Normal briefing delivery is never performed here. Post-delivery corrections
remain non-redeliverable and their receipt contract is the one accepted by
``briefing_finalization/18`` (validation-first delivery authority).
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import tempfile
import time
from typing import Any, Callable
import urllib.error
import urllib.request


API_ROOT = "https://api.notion.com/v1"
ADAPTER = "notion_cockpit"
INITIAL_PURPOSE = "atlas.briefing_finalization.canonical_briefing"
CORRECTION_PURPOSE = "atlas.briefing_finalization.portal_projection"
BOOTSTRAP_PURPOSE = "atlas.briefing_finalization.legacy_portal_bootstrap"
RICH_TEXT_CHUNK = 1900
MAX_RICH_TEXT_ITEMS = 100
READBACK_ATTEMPTS = 4
BRIEFING_ID = re.compile(r"^(\d{4}-\d{2}-\d{2})-(am|pm)$")
CHANGE_KEY = re.compile(r"^[0-9a-f]{64}$")
SLOT_SUFFIX = {"morning": "am", "evening": "pm"}
REQUIRED_SCHEMA = {
    "Briefing ID": "title",
    "Canonical JSON": "rich_text",
    "Capital Impact": "select",
    "Content SHA256": "rich_text",
    "Contract Version": "rich_text",
    "Decision Date": "date",
    "Projection Status": "select",
    "Purpose": "rich_text",
    "Slot": "select",
    "Written At UTC": "date",
}
REQUIRED_SELECT_OPTIONS = {
    "Capital Impact": {"NONE", "PRESENT", "UNKNOWN"},
    "Projection Status": {"CURRENT", "SUPERSEDED"},
    "Slot": {"morning", "evening"},
}
FORBIDDEN_TRUE_KEYS = {
    "stage_authority", "stage_promotion_authority", "buy_authority",
    "action_authority", "order_authority", "production_authority",
    "trading_authority", "broker_credentials_used",
}


class ProjectionError(RuntimeError):
    pass


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def _utc_iso(value: dt.datetime | None = None) -> str:
    current = value or dt.datetime.now(dt.timezone.utc)
    if current.tzinfo is None:
        raise ProjectionError("WRITTEN_AT_MUST_BE_TIMEZONE_AWARE")
    # Notion date properties normalize sub-minute precision away.  Write at
    # the precision the authority can round-trip so an exact semantic readback
    # does not reject the same instant solely because seconds were truncated.
    current = current.astimezone(dt.timezone.utc).replace(second=0, microsecond=0)
    return current.isoformat().replace("+00:00", "Z")


def _same_instant(left: str, right: str) -> bool:
    try:
        a = dt.datetime.fromisoformat(left.replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(right.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    return a.tzinfo is not None and b.tzinfo is not None and a == b


def rich_text(value: str) -> dict:
    chunks = [value[index:index + RICH_TEXT_CHUNK]
              for index in range(0, len(value), RICH_TEXT_CHUNK)] or [""]
    if len(chunks) > MAX_RICH_TEXT_ITEMS:
        raise ProjectionError("NOTION_CANONICAL_JSON_TOO_LARGE")
    return {"rich_text": [
        {"type": "text", "text": {"content": chunk}} for chunk in chunks
    ]}


def plain_text(prop: dict) -> str:
    kind = prop.get("type")
    rows = prop.get(kind, []) if kind in {"title", "rich_text"} else []
    return "".join(
        str(row.get("plain_text", row.get("text", {}).get("content", "")))
        for row in rows
    )


def _select_name(prop: dict) -> str | None:
    value = prop.get("select") if prop.get("type") == "select" else None
    return value.get("name") if isinstance(value, dict) else None


def _date_start(prop: dict) -> str | None:
    value = prop.get("date") if prop.get("type") == "date" else None
    return value.get("start") if isinstance(value, dict) else None


def _contains_authority_escalation(value: Any) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if str(key).lower() in FORBIDDEN_TRUE_KEYS and nested is not False:
                return True
            if _contains_authority_escalation(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_authority_escalation(item) for item in value)
    return False


def validate_content(content: dict, decision_date: str, slot: str) -> None:
    required = {"contract_version", "purpose", "briefing_id"}
    if not isinstance(content, dict) or not required.issubset(content):
        raise ProjectionError("PROJECTION_CONTENT_INCOMPLETE")
    if not str(content["contract_version"]).strip() or not str(content["purpose"]).strip():
        raise ProjectionError("PROJECTION_CONTENT_INCOMPLETE")
    match = BRIEFING_ID.fullmatch(str(content["briefing_id"]))
    if match is None:
        raise ProjectionError("BRIEFING_ID_INVALID")
    if slot not in SLOT_SUFFIX:
        raise ProjectionError("SLOT_INVALID")
    if match.group(1) != decision_date or match.group(2) != SLOT_SUFFIX[slot]:
        raise ProjectionError("PROJECTION_IDENTITY_MISMATCH")
    if content.get("decision_date", decision_date) != decision_date:
        raise ProjectionError("PROJECTION_IDENTITY_MISMATCH")
    if content.get("slot", slot) != slot:
        raise ProjectionError("PROJECTION_IDENTITY_MISMATCH")
    try:
        dt.date.fromisoformat(decision_date)
    except ValueError:
        raise ProjectionError("DECISION_DATE_INVALID") from None
    impact = content.get("capital_impact") or "UNKNOWN"
    if impact not in {"NONE", "PRESENT", "UNKNOWN"}:
        raise ProjectionError("CAPITAL_IMPACT_INVALID")
    change_key = content.get("post_delivery_change_key")
    if change_key is not None:
        if CHANGE_KEY.fullmatch(str(change_key)) is None:
            raise ProjectionError("POST_DELIVERY_CHANGE_KEY_INVALID")
        if content.get("redelivery") != "FORBIDDEN":
            raise ProjectionError("POST_DELIVERY_REDELIVERY_FORBIDDEN")
        if content.get("purpose") != CORRECTION_PURPOSE:
            raise ProjectionError("POST_DELIVERY_PURPOSE_INVALID")
    if content.get("purpose") == BOOTSTRAP_PURPOSE:
        if change_key is not None or content.get("redelivery") != "FORBIDDEN":
            raise ProjectionError("PORTAL_BOOTSTRAP_REDELIVERY_INVALID")
        if not isinstance(content.get("portal_snapshot"), dict):
            raise ProjectionError("PORTAL_BOOTSTRAP_SNAPSHOT_MISSING")
    if _contains_authority_escalation(content):
        raise ProjectionError("PROJECTION_AUTHORITY_ESCALATION")
    rich_text(canonical(content).decode("utf-8"))


class NotionClient:
    def __init__(self, token: str, api_version: str, opener=None,
                 sleeper: Callable[[float], None] | None = None) -> None:
        if not token.strip():
            raise ProjectionError("NOTION_TOKEN_REQUIRED")
        self.token = token
        self.api_version = api_version
        self.opener = opener or urllib.request.urlopen
        self.sleeper = sleeper or time.sleep

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        data = canonical(body) if body is not None else None
        for attempt in range(4):
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
                if exc.code in {429, 500, 502, 503, 504} and attempt < 3:
                    retry_after = exc.headers.get("Retry-After") if exc.headers else None
                    try:
                        delay = min(float(retry_after), 5.0) if retry_after else 0.5 * (2 ** attempt)
                    except ValueError:
                        delay = 0.5 * (2 ** attempt)
                    self.sleeper(delay)
                    continue
                raise ProjectionError(f"NOTION_HTTP_{exc.code}:{detail}") from None
            except (OSError, ValueError) as exc:
                raise ProjectionError(
                    f"NOTION_TRANSPORT_OR_JSON:{type(exc).__name__}") from None
            if not isinstance(result, dict):
                raise ProjectionError("NOTION_RESPONSE_NOT_OBJECT")
            return result
        raise ProjectionError("NOTION_RETRY_EXHAUSTED")

    def retrieve_data_source(self, data_source_id: str) -> dict:
        return self.request("GET", f"/data_sources/{data_source_id}")

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


def verify_schema(data_source: dict) -> None:
    properties = data_source.get("properties")
    if not isinstance(properties, dict):
        raise ProjectionError("NOTION_DATA_SOURCE_SCHEMA_MISSING")
    mismatches = [name for name, expected in REQUIRED_SCHEMA.items()
                  if not isinstance(properties.get(name), dict)
                  or properties[name].get("type") != expected]
    if mismatches:
        raise ProjectionError("NOTION_DATA_SOURCE_SCHEMA_MISMATCH:" + ",".join(mismatches))
    option_mismatches = []
    for name, expected in REQUIRED_SELECT_OPTIONS.items():
        options = properties[name].get("select", {}).get("options", [])
        actual = {row.get("name") for row in options if isinstance(row, dict)}
        if actual != expected:
            option_mismatches.append(name)
    if option_mismatches:
        raise ProjectionError(
            "NOTION_DATA_SOURCE_SELECT_OPTIONS_MISMATCH:" + ",".join(option_mismatches))


def projection_properties(content: dict, content_sha: str, written_at: str,
                          decision_date: str, slot: str) -> dict:
    impact = content.get("capital_impact") or "UNKNOWN"
    return {
        "Briefing ID": {"title": [
            {"type": "text", "text": {"content": content["briefing_id"]}}]},
        "Content SHA256": rich_text(content_sha),
        "Contract Version": rich_text(str(content["contract_version"])),
        "Purpose": rich_text(str(content["purpose"])),
        "Decision Date": {"date": {"start": decision_date}},
        "Slot": {"select": {"name": slot}},
        "Capital Impact": {"select": {"name": impact}},
        "Projection Status": {"select": {"name": "CURRENT"}},
        "Canonical JSON": rich_text(canonical(content).decode("utf-8")),
        "Written At UTC": {"date": {"start": written_at}},
    }


def verify_readback(page: dict, content: dict, expected_sha: str,
                    decision_date: str, slot: str,
                    expected_written_at: str | None = None) -> str:
    props = page.get("properties")
    if not isinstance(props, dict):
        raise ProjectionError("NOTION_READBACK_PROPERTIES_MISSING")
    text_checks = {
        "Briefing ID": content["briefing_id"],
        "Content SHA256": expected_sha,
        "Contract Version": str(content["contract_version"]),
        "Purpose": str(content["purpose"]),
        "Canonical JSON": canonical(content).decode("utf-8"),
    }
    for name, expected in text_checks.items():
        if plain_text(props.get(name, {})) != expected:
            raise ProjectionError(f"NOTION_READBACK_MISMATCH:{name}")
    select_checks = {
        "Capital Impact": content.get("capital_impact") or "UNKNOWN",
        "Projection Status": "CURRENT",
        "Slot": slot,
    }
    for name, expected in select_checks.items():
        if _select_name(props.get(name, {})) != expected:
            raise ProjectionError(f"NOTION_READBACK_MISMATCH:{name}")
    if _date_start(props.get("Decision Date", {})) != decision_date:
        raise ProjectionError("NOTION_READBACK_MISMATCH:Decision Date")
    written_at = _date_start(props.get("Written At UTC", {}))
    if not written_at or (expected_written_at is not None
                          and not _same_instant(written_at, expected_written_at)):
        raise ProjectionError("NOTION_READBACK_MISMATCH:Written At UTC")
    return written_at


def _receipt_rev(path: Path) -> int:
    suffix = path.stem.rsplit("-", 1)[-1]
    return int(suffix) if suffix.isdigit() else 0


def _receipt_prefix(content: dict) -> str:
    return ("portal-projection-receipt" if content.get("post_delivery_change_key")
            else "portal-initial-projection-receipt")


def projection_change_key(content: dict, content_sha: str | None = None) -> str:
    return str(content.get("post_delivery_change_key")
               or f"initial:{content['briefing_id']}:{content_sha or digest(content)}")


def latest_receipt(directory: Path, prefix: str, change_key: str) -> tuple[Path, dict] | None:
    latest: tuple[Path, dict] | None = None
    for path in sorted(directory.glob(f"{prefix}-rev-*.json"), key=_receipt_rev):
        try:
            body = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raise ProjectionError(f"PROJECTION_RECEIPT_UNREADABLE:{path.name}") from None
        if not isinstance(body, dict):
            raise ProjectionError(f"PROJECTION_RECEIPT_NOT_OBJECT:{path.name}")
        if body.get("projection_change_key") == change_key or (
                body.get("post_delivery_change_key") == change_key):
            latest = (path, body)
    return latest


def _matching_receipt(receipt: dict, *, content: dict, content_sha: str,
                      target: str) -> bool:
    change_key = projection_change_key(content, content_sha)
    return (
        receipt.get("adapter") == ADAPTER
        and receipt.get("briefing_id") == content["briefing_id"]
        and receipt.get("projection_change_key") == change_key
        and receipt.get("post_delivery_change_key") == content.get("post_delivery_change_key")
        and bool(target.strip())
        and receipt.get("target") == target
        and receipt.get("content_sha256") == content_sha
        and receipt.get("read_after_write_verified") is True
        and bool(str(receipt.get("written_at_utc", "")).strip())
    )


def atomic_receipt(directory: Path, prefix: str, receipt: dict) -> Path:
    """Publish complete bytes atomically without ever overwriting a revision."""
    directory.mkdir(parents=True, exist_ok=True)
    payload = canonical(receipt) + b"\n"
    fd, temp_name = tempfile.mkstemp(prefix=f".{prefix}.", suffix=".tmp", dir=directory)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        revision = max((_receipt_rev(path)
                        for path in directory.glob(f"{prefix}-rev-*.json")), default=0) + 1
        while True:
            target = directory / f"{prefix}-rev-{revision:03d}.json"
            try:
                os.link(temp, target)
                break
            except FileExistsError:
                revision += 1
        try:
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:
            pass
        return target
    finally:
        if temp.exists():
            temp.unlink()


def _read_existing_exact(client: NotionClient, page_id: str, content: dict,
                         content_sha: str, decision_date: str, slot: str) -> tuple[dict, str] | None:
    page = client.retrieve(page_id)
    try:
        written_at = verify_readback(page, content, content_sha, decision_date, slot)
    except ProjectionError as exc:
        if not str(exc).startswith("NOTION_READBACK_MISMATCH"):
            raise
        return None
    return page, written_at


def _verified_readback(client: NotionClient, page_id: str, content: dict,
                       content_sha: str, decision_date: str, slot: str,
                       written_at: str) -> tuple[dict, str]:
    last_error: ProjectionError | None = None
    for attempt in range(READBACK_ATTEMPTS):
        page = client.retrieve(page_id)
        try:
            return page, verify_readback(
                page, content, content_sha, decision_date, slot, written_at)
        except ProjectionError as exc:
            last_error = exc
            if attempt < READBACK_ATTEMPTS - 1:
                client.sleeper(0.25 * (2 ** attempt))
    assert last_error is not None
    raise last_error


def project(client: NotionClient, data_source_id: str, content: dict,
            decision_date: str, slot: str, receipt_dir: Path | None = None,
            now: dt.datetime | None = None) -> dict:
    if receipt_dir is None:
        raise ProjectionError("RECEIPT_DIR_REQUIRED")
    validate_content(content, decision_date, slot)
    content_sha = digest(content)
    rows = client.find(data_source_id, content["briefing_id"])
    if len(rows) > 1:
        raise ProjectionError("NOTION_DUPLICATE_BRIEFING_ID")

    operation = "NO_CHANGE"
    page_id: str | None = rows[0].get("id") if rows else None
    existing = (_read_existing_exact(client, page_id, content, content_sha,
                                     decision_date, slot) if page_id else None)
    if existing is not None:
        _page, written_at = existing
    else:
        written_at = _utc_iso(now)
        properties = projection_properties(content, content_sha, written_at,
                                           decision_date, slot)
        if page_id:
            operation = "UPDATED"
            page = client.update(page_id, properties)
        else:
            operation = "CREATED"
            page = client.create(data_source_id, properties)
            page_id = page.get("id")
        if not page_id:
            raise ProjectionError("NOTION_PAGE_ID_MISSING")
        _verified_readback(client, page_id, content, content_sha,
                           decision_date, slot, written_at)

    rows_after = client.find(data_source_id, content["briefing_id"])
    if len(rows_after) != 1 or rows_after[0].get("id") != page_id:
        raise ProjectionError("NOTION_BRIEFING_ID_UNIQUENESS_VIOLATION")

    verified_at = _utc_iso(now)
    change_key = projection_change_key(content, content_sha)
    prefix = _receipt_prefix(content)
    receipt = {
        "schema_version": "atlas_notion_projection_receipt/1",
        "adapter": ADAPTER,
        "briefing_id": content["briefing_id"],
        "projection_change_key": change_key,
        "post_delivery_change_key": content.get("post_delivery_change_key"),
        "target": page_id,
        "operation": operation,
        "written_at_utc": written_at,
        "readback_at_utc": verified_at,
        "content_sha256": content_sha,
        "contract_version": str(content["contract_version"]),
        "read_after_write_verified": True,
        "redelivery": content.get("redelivery"),
        "authority": {
            "stage": False, "buy": False, "action": False, "order": False,
            "production": False, "trading": False,
        },
    }
    prior = latest_receipt(receipt_dir, prefix, change_key)
    if prior is not None and _matching_receipt(
            prior[1], content=content, content_sha=content_sha, target=page_id):
        receipt_path = prior[0]
        receipt_reused = True
    else:
        receipt_path = atomic_receipt(receipt_dir, prefix, receipt)
        receipt_reused = False
    return {**receipt, "receipt_path": str(receipt_path),
            "receipt_reused": receipt_reused}


def _load_finalization_module():
    path = Path(__file__).with_name("briefing_finalization.py")
    if not path.is_file():
        raise ProjectionError("FINALIZATION_CORE_MISSING")
    spec = importlib.util.spec_from_file_location("atlas_briefing_finalization", path)
    if spec is None or spec.loader is None:
        raise ProjectionError("FINALIZATION_CORE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise ProjectionError(f"{code}:{path}") from None
    if not isinstance(value, dict):
        raise ProjectionError(f"{code}:{path}")
    return value


def _latest_path(directory: Path, prefix: str) -> Path | None:
    rows = sorted(directory.glob(f"{prefix}-rev-*.json"), key=_receipt_rev)
    return rows[-1] if rows else None


def initial_projection_content(repo_root: Path, kst_date: str, slot: str,
                               bf=None) -> dict | None:
    bf = bf or _load_finalization_module()
    directory = repo_root / "data/briefing/finalization" / kst_date / slot
    draft_path = _latest_path(directory, "draft")
    if draft_path is None:
        return None
    draft = _read_json(draft_path, "FINALIZATION_DRAFT_UNREADABLE")
    validation, problem = bf.resolve_validation(directory)
    if problem is not None:
        raise ProjectionError(f"FINALIZATION_VALIDATION_INVALID:{problem}")
    if validation is not None:
        routing = validation.get("routing") or bf.derive_routing(
            validation, bf.load_ratified_specs(repo_root))
        if not routing.get("status_deliverable"):
            return None
        bf.verify_pre_delivery_portal_receipt(
            repo_root, kst_date, slot, draft=draft, validation=validation)
    elif bf.load_semantic_validator_policy(repo_root)["expected"]:
        return None
    payload_path = directory / f"payload-rev-{int(draft['rev']):03d}.md"
    try:
        payload = payload_path.read_bytes()
    except OSError:
        raise ProjectionError(f"FINALIZATION_PAYLOAD_UNREADABLE:{payload_path}") from None
    payload_sha = hashlib.sha256(payload).hexdigest()
    if payload_sha != draft.get("delivery_payload_sha256"):
        raise ProjectionError("FINALIZATION_PAYLOAD_HASH_MISMATCH")
    try:
        markdown = payload.decode("utf-8")
    except UnicodeDecodeError:
        raise ProjectionError("FINALIZATION_PAYLOAD_NOT_UTF8") from None
    return {
        "contract_version": draft["contract_version"],
        "purpose": INITIAL_PURPOSE,
        "briefing_id": draft["briefing_id"],
        "decision_date": kst_date,
        "slot": slot,
        "capital_impact": "UNKNOWN",
        "delivery_payload_sha256": payload_sha,
        "delivery_payload_markdown": markdown,
        "source": draft["source"],
        "source_fingerprint": draft.get("source_fingerprint"),
        "delivery_marker": draft["delivery_marker"],
        "semantic_validator_expected": bf.load_semantic_validator_policy(repo_root)["expected"],
        "safety_attestation": {
            "stage_authority": False,
            "buy_authority": False,
            "action_authority": False,
            "order_authority": False,
            "production_authority": False,
            "trading_authority": False,
            "broker_credentials_used": False,
        },
    }


def projection_candidates(repo_root: Path, only_date: str | None = None,
                          only_slot: str | None = None) -> list[tuple[str, str, dict, Path]]:
    bf = _load_finalization_module()
    root = repo_root / "data/briefing/finalization"
    candidates: list[tuple[str, str, dict, Path]] = []
    bootstrap_ids: set[str] = set()
    bootstrap_root = repo_root / "data/briefing/portal_bootstrap"
    if bootstrap_root.is_dir():
        for path in sorted(bootstrap_root.glob("*.json")):
            content = _read_json(path, "PORTAL_BOOTSTRAP_UNREADABLE")
            briefing = str(content.get("briefing_id", ""))
            match = BRIEFING_ID.fullmatch(briefing)
            if match is None or path.stem != briefing:
                raise ProjectionError(f"PORTAL_BOOTSTRAP_IDENTITY_INVALID:{path}")
            decision_date = match.group(1)
            slot = "morning" if match.group(2) == "am" else "evening"
            if only_date and decision_date != only_date:
                continue
            if only_slot and slot != only_slot:
                continue
            if content.get("purpose") != BOOTSTRAP_PURPOSE:
                raise ProjectionError(f"PORTAL_BOOTSTRAP_PURPOSE_INVALID:{path}")
            validate_content(content, decision_date, slot)
            receipt_dir = root / decision_date / slot
            candidates.append((decision_date, slot, content, receipt_dir))
            bootstrap_ids.add(briefing)

    if not root.exists():
        return candidates
    for date_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        if only_date and date_dir.name != only_date:
            continue
        for slot in ("morning", "evening"):
            if only_slot and slot != only_slot:
                continue
            directory = date_dir / slot
            if not directory.is_dir():
                continue
            initial = initial_projection_content(repo_root, date_dir.name, slot, bf=bf)
            if initial is not None:
                if initial["briefing_id"] in bootstrap_ids:
                    raise ProjectionError(
                        f"PORTAL_MULTIPLE_INITIAL_SOURCES:{initial['briefing_id']}")
                candidates.append((date_dir.name, slot, initial, directory))
            changes = sorted(directory.glob("post-delivery-change-rev-*.json"),
                             key=_receipt_rev)
            if not changes:
                continue
            resolutions = bf.load_change_resolutions(repo_root, date_dir.name, slot)
            for path in changes:
                change = _read_json(path, "FINALIZATION_CHANGE_UNREADABLE")
                key = change.get("post_delivery_change_key")
                ruling = resolutions.get(key)
                if ruling is None:
                    continue
                content = bf.expected_projection_content(
                    bf.briefing_id(date_dir.name, slot), change, ruling)
                candidates.append((date_dir.name, slot, content, directory))
    return candidates


def sync(client: NotionClient, data_source_id: str, repo_root: Path,
         only_date: str | None = None, only_slot: str | None = None,
         now: dt.datetime | None = None) -> dict:
    candidates = projection_candidates(repo_root, only_date, only_slot)
    results = []
    last_for_briefing = {
        content["briefing_id"]: index
        for index, (_date, _slot, content, _directory) in enumerate(candidates)
    }
    for index, (decision_date, slot, content, directory) in enumerate(candidates):
        content_sha = digest(content)
        change_key = projection_change_key(content, content_sha)
        prior = latest_receipt(directory, _receipt_prefix(content), change_key)
        # A later correction is the current Portal state. Replaying an already
        # proven older candidate would transiently walk the row backwards on
        # every retry. A missing/bad older proof is still replayed once so the
        # append-only completion history can recover, then the latest candidate
        # is written last and always read back from Notion.
        if (index != last_for_briefing[content["briefing_id"]]
                and prior is not None
                and _matching_receipt(prior[1], content=content,
                                      content_sha=content_sha,
                                      target=str(prior[1].get("target", "")))):
            results.append({**prior[1], "receipt_path": str(prior[0]),
                            "receipt_reused": True,
                            "operation": "SUPERSEDED_RECEIPT_ALREADY_AUTHORITATIVE"})
            continue
        results.append(project(client, data_source_id, content, decision_date, slot,
                               directory, now=now))
    return {"adapter": ADAPTER, "candidate_count": len(candidates),
            "projected": results,
            "all_read_after_write_verified": all(
                row.get("read_after_write_verified") is True for row in results)}


def verify_canary_replay(rows: list[dict]) -> None:
    if not rows:
        raise ProjectionError("NOTION_CANARY_NO_CANDIDATE")
    for row in rows:
        if row.get("operation") not in {
                "NO_CHANGE", "SUPERSEDED_RECEIPT_ALREADY_AUTHORITATIVE"}:
            raise ProjectionError("NOTION_CANARY_REPLAY_WROTE_AGAIN")
        if row.get("receipt_reused") is not True:
            raise ProjectionError("NOTION_CANARY_RECEIPT_NOT_REUSED")
        if row.get("read_after_write_verified") is not True:
            raise ProjectionError("NOTION_CANARY_REPLAY_NOT_VERIFIED")


def _load_config(path: Path) -> dict:
    config = _read_json(path, "PROJECTION_CONFIG_UNREADABLE").get("portal")
    if not isinstance(config, dict):
        raise ProjectionError("PROJECTION_CONFIG_PORTAL_MISSING")
    for key in ("adapter", "implemented", "verified_against_live_api",
                "api_version", "data_source_id", "database_id"):
        if key not in config:
            raise ProjectionError(f"PROJECTION_CONFIG_FIELD_MISSING:{key}")
    if config["adapter"] != ADAPTER:
        raise ProjectionError("PROJECTION_CONFIG_ADAPTER_MISMATCH")
    return config


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--content")
    parser.add_argument("--decision-date")
    parser.add_argument("--slot", choices=("morning", "evening"))
    parser.add_argument("--receipt-dir")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--sync-all", action="store_true")
    parser.add_argument("--live-canary", action="store_true",
                        help="Explicitly test the CI identity before policy activation")
    parser.add_argument("--config", default="config/atlas_projection.json")
    args = parser.parse_args()
    if bool(args.content) == bool(args.sync_all):
        parser.error("choose exactly one of --content or --sync-all")
    if args.content and not (args.decision_date and args.slot and args.receipt_dir):
        parser.error("--content requires --decision-date, --slot, and --receipt-dir")

    config = _load_config(Path(args.config))
    active = bool(config["implemented"] and config["verified_against_live_api"])
    if not active and not args.live_canary:
        print(json.dumps({"adapter": ADAPTER, "status": "SKIPPED_NOT_LIVE_VERIFIED",
                          "implemented": bool(config["implemented"]),
                          "verified_against_live_api": bool(
                              config["verified_against_live_api"])}, sort_keys=True))
        return 0

    client = NotionClient(os.environ.get("NOTION_TOKEN", ""), config["api_version"])
    verify_schema(client.retrieve_data_source(config["data_source_id"]))
    if args.content:
        content = _read_json(Path(args.content), "PROJECTION_CONTENT_UNREADABLE")
        result = project(client, config["data_source_id"], content,
                         args.decision_date, args.slot, Path(args.receipt_dir))
        if args.live_canary:
            replay = project(client, config["data_source_id"], content,
                             args.decision_date, args.slot, Path(args.receipt_dir))
            verify_canary_replay([replay])
            result["idempotency_replay"] = replay
    else:
        result = sync(client, config["data_source_id"], Path(args.repo_root).resolve(),
                      args.decision_date, args.slot)
        if args.live_canary:
            replay = sync(client, config["data_source_id"],
                          Path(args.repo_root).resolve(), args.decision_date, args.slot)
            verify_canary_replay(replay["projected"])
            result["idempotency_replay"] = replay
    result["live_canary"] = bool(args.live_canary)
    result["idempotency_replay_verified"] = bool(args.live_canary)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
