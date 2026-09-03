#!/usr/bin/env python3
"""H-24 deterministic daily-briefing locator and read-only consumer.

The producer writes one exact current pointer.  The consumer never scans a
directory, falls back to a prior date/slot, or selects a revision itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys


REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from briefing.daily_orchestrator import validate_packet  # noqa: E402


LOCATOR_PATH = Path("data/briefing/daily_briefing_sources.json")
SCHEMA_VERSION = "daily_briefing_delivery/1"
DELIVERED_COMPONENTS = (
    "INVESTMENT_DECISION_REVIEW",
    "INVESTMENT_REVIEW_SHADOW",
    "SHADOW_ENTRY_REVIEW",
)


class DeliveryError(RuntimeError):
    pass


def _fail(code: str, detail: str = "") -> None:
    raise DeliveryError(f"{code}{': ' + detail if detail else ''}")


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("DELIVERY_JSON_UNREADABLE", f"{path}: {type(exc).__name__}")
    if not isinstance(value, dict):
        _fail("DELIVERY_JSON_NOT_OBJECT", str(path))
    return value


def _sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        _fail("DELIVERY_FILE_UNREADABLE", f"{path}: {type(exc).__name__}")


def _contains_post_hoc_key(value) -> bool:
    forbidden = ("forward_return", "mfe", "mae", "post_hoc", "audit_confirmed_miss")
    if isinstance(value, dict):
        return any(
            any(token in str(key).lower() for token in forbidden)
            or _contains_post_hoc_key(nested)
            for key, nested in value.items()
        )
    if isinstance(value, list):
        return any(_contains_post_hoc_key(item) for item in value)
    return False


def build_locator(repo_root: Path, slot: str, decision_date: str) -> dict:
    if slot not in ("morning", "evening"):
        _fail("DELIVERY_SLOT_UNSUPPORTED", slot)
    date_root = Path("evidence/daily_briefing") / slot / decision_date
    index_path = date_root / "index.json"
    index = _read_json(repo_root / index_path)
    if index.get("schema_version") != 1:
        _fail("DELIVERY_INDEX_SCHEMA_UNSUPPORTED")
    if index.get("slot") != slot or index.get("decision_date") != decision_date:
        _fail("DELIVERY_INDEX_IDENTITY_MISMATCH")
    revisions = index.get("revisions")
    latest = index.get("latest_revision")
    if not isinstance(revisions, list) or not revisions or latest != len(revisions):
        _fail("DELIVERY_INDEX_REVISION_INVALID")
    entry = revisions[-1]
    if entry.get("revision") != latest or entry.get("path") != f"rev-{latest:03d}":
        _fail("DELIVERY_INDEX_LATEST_MISMATCH")
    revision_root = date_root / entry["path"]
    packet_path = revision_root / "packet.json"
    briefing_path = revision_root / "briefing.md"
    packet = _read_json(repo_root / packet_path)
    validate_packet(packet)
    if packet.get("slot") != slot or packet.get("decision_date") != decision_date:
        _fail("DELIVERY_PACKET_IDENTITY_MISMATCH")
    if packet.get("packet_sha256") != entry.get("packet_sha256"):
        _fail("DELIVERY_PACKET_INDEX_SHA_MISMATCH")
    if not (repo_root / briefing_path).is_file():
        _fail("DELIVERY_BRIEFING_MISSING", str(briefing_path))
    return {
        "schema_version": SCHEMA_VERSION,
        "slot": slot,
        "decision_date": decision_date,
        "revision": latest,
        "index_path": index_path.as_posix(),
        "index_sha256": _sha256(repo_root / index_path),
        "packet_path": packet_path.as_posix(),
        "packet_file_sha256": _sha256(repo_root / packet_path),
        "packet_sha256": packet["packet_sha256"],
        "briefing_path": briefing_path.as_posix(),
        "briefing_sha256": _sha256(repo_root / briefing_path),
        "delivery_scope": list(DELIVERED_COMPONENTS),
        "authority": {
            "stage": False,
            "buy": False,
            "action": False,
            "order": False,
            "production": False,
            "trading": False,
        },
    }


def write_locator(repo_root: Path, locator: dict) -> bool:
    target = repo_root / LOCATOR_PATH
    rendered = json.dumps(locator, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if target.exists() and target.read_text(encoding="utf-8") == rendered:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp.{os.getpid()}")
    try:
        temp.write_text(rendered, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return True


def consume(repo_root: Path, expected_slot: str, expected_date: str) -> dict:
    locator = _read_json(repo_root / LOCATOR_PATH)
    if locator.get("schema_version") != SCHEMA_VERSION:
        _fail("DELIVERY_LOCATOR_SCHEMA_UNSUPPORTED")
    if locator.get("slot") != expected_slot:
        _fail("DELIVERY_LOCATOR_SLOT_MISMATCH")
    if locator.get("decision_date") != expected_date:
        _fail("DELIVERY_LOCATOR_DATE_MISMATCH")
    if locator.get("delivery_scope") != list(DELIVERED_COMPONENTS):
        _fail("DELIVERY_SCOPE_MISMATCH")
    if any(locator.get("authority", {}).values()):
        _fail("DELIVERY_AUTHORITY_ESCALATION")

    rebuilt = build_locator(repo_root, expected_slot, expected_date)
    if locator != rebuilt:
        _fail("DELIVERY_LOCATOR_DRIFT_OR_TAMPER")
    packet = _read_json(repo_root / Path(locator["packet_path"]))
    # build_locator() validates the packet it reads while rebuilding the
    # locator, but this is a second read.  Validate the exact in-memory value
    # consumed below so a local replacement in that interval cannot bypass
    # frozen-source identity/type/SHA/date checks.
    validate_packet(packet)
    by_id = {row.get("component_id"): row for row in packet.get("components", [])}
    components = []
    for component_id in DELIVERED_COMPONENTS:
        row = by_id.get(component_id)
        if not isinstance(row, dict):
            _fail("DELIVERY_COMPONENT_MISSING", component_id)
        packet_body = row.get("packet") or {}
        authority = packet_body.get("authority") or row.get("authority") or {}
        if not isinstance(authority, dict) or any(
            value is True for key, value in authority.items()
            if key != "briefing_status_only"
        ):
            _fail("DELIVERY_COMPONENT_AUTHORITY_ESCALATION", component_id)
        bounded = {
            "component_id": component_id,
            "status": row.get("status"),
            "reason": row.get("reason"),
            "review_outcome": packet_body.get("review_outcome"),
            "trade_proposal": packet_body.get("trade_proposal"),
            "money_action": packet_body.get("money_action"),
            "capital": packet_body.get("capital"),
            "ledger_record_created": packet_body.get("ledger_record_created"),
            "action": packet_body.get("action"),
            "order": packet_body.get("order"),
            "stage_change": packet_body.get("stage_change"),
        }
        if component_id == "INVESTMENT_DECISION_REVIEW":
            if bounded["review_outcome"] == "BLOCKED" and (
                bounded["trade_proposal"] is not None
                or bounded["money_action"] != "NONE"
            ):
                _fail("DELIVERY_BLOCKED_REVIEW_ACTION_LEAK")
            bounded["capital"] = 0
        elif component_id == "INVESTMENT_REVIEW_SHADOW":
            capital = bounded["capital"]
            if not isinstance(capital, dict):
                _fail("DELIVERY_SHADOW_CAPITAL_INVALID")
            bounded["capital"] = capital.get("amount")
            if bounded["review_outcome"] == "BLOCKED" and (
                bounded["ledger_record_created"] is not False
                or bounded["capital"] != 0
                or bounded["action"] is not None
                or bounded["order"] is not None
                or bounded["stage_change"] is not None
            ):
                _fail("DELIVERY_BLOCKED_SHADOW_LEAK")
        else:
            bounded = {
                "component_id": component_id,
                "status": row.get("status"),
                "reason": row.get("reason"),
                "sample_status": packet_body.get("sample_status"),
                "summary": packet_body.get("summary"),
                "policy_status": packet_body.get("policy_status"),
                "review_items": packet_body.get("review_items", []),
                "why_not_executable": packet_body.get("why_not_executable", []),
                "trade_proposal": (packet_body.get("authority") or {}).get("trade_proposal"),
                "capital": (packet_body.get("authority") or {}).get("capital"),
            }
            if row.get("status") == "READY":
                if packet_body.get("schema_version") != "shadow_entry_review_briefing_status/1":
                    _fail("DELIVERY_SHADOW_REVIEW_SCHEMA_INVALID")
                packet_authority = packet_body.get("authority")
                if (
                    not isinstance(packet_authority, dict)
                    or packet_authority.get("capital") != 0
                    or packet_authority.get("trade_proposal") is not None
                    or any(
                        packet_authority.get(key) is not False
                        for key in (
                            "stage_promotion_authority", "buy_authority", "action_authority",
                            "order_authority", "production_authority", "trading_authority",
                        )
                    )
                ):
                    _fail("DELIVERY_SHADOW_REVIEW_AUTHORITY_INVALID")
                summary = bounded["summary"]
                items = bounded["review_items"]
                if (
                    not isinstance(summary, dict)
                    or not isinstance(items, list)
                    or len(items) != summary.get("zero_capital_review_item_count")
                ):
                    _fail("DELIVERY_SHADOW_REVIEW_COUNT_INVALID")
                for item in items:
                    money = item.get("money_boundary") if isinstance(item, dict) else None
                    if (
                        not isinstance(money, dict)
                        or money.get("capital") != 0
                        or money.get("trade_proposal") is not None
                        or any(
                            money.get(key) is not False
                            for key in (
                                "stage_promotion_authority", "buy_authority", "action_authority",
                                "order_authority", "production_authority", "trading_authority",
                            )
                        )
                    ):
                        _fail("DELIVERY_SHADOW_REVIEW_ITEM_AUTHORITY_INVALID")
                if _contains_post_hoc_key(bounded):
                    _fail("DELIVERY_SHADOW_REVIEW_POST_HOC_FIELD_FORBIDDEN")
        components.append(bounded)
    return {
        "schema_version": SCHEMA_VERSION,
        "slot": expected_slot,
        "decision_date": expected_date,
        "revision": locator["revision"],
        "components": components,
        "authority": locator["authority"],
    }


def render_delivery(delivery: dict) -> str:
    lines = [
        f"## Investment review delivery — {delivery['slot']} {delivery['decision_date']}",
        "",
    ]
    for row in delivery["components"]:
        lines.append(f"### {row['component_id']}: {row['status']}")
        lines.append(f"- reason: {row['reason']}")
        if row.get("review_outcome") is not None:
            lines.append(f"- review_outcome: {row['review_outcome']}")
        if row.get("trade_proposal") is not None:
            lines.append(f"- trade_proposal: {row['trade_proposal']}")
        if row.get("money_action") is not None:
            lines.append(f"- money_action: {row['money_action']}")
        if row.get("capital") is not None:
            lines.append(f"- capital: {row['capital']}")
        if row.get("ledger_record_created") is not None:
            lines.append(f"- ledger_record_created: {str(row['ledger_record_created']).lower()}")
        if row["component_id"] == "SHADOW_ENTRY_REVIEW":
            summary = row.get("summary") or {}
            lines.append(f"- sample_status: {row.get('sample_status')}")
            lines.append(
                f"- zero_capital_review_items: {summary.get('zero_capital_review_item_count')}"
            )
            for item in row.get("review_items", []):
                lines.append(
                    f"- {item.get('subject')} ({item.get('market')}): "
                    f"{item.get('review_state')} / {item.get('participation_state')} / "
                    f"{item.get('review_due_status')} / reason={item.get('review_reason')} / "
                    "capital=0 / trade_proposal=null"
                )
            lines.append(
                "- why_not_executable: " + ",".join(row.get("why_not_executable", []))
            )
        lines.append("")
    lines.append("Trading authority: false")
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("publish-locator", "consume"))
    parser.add_argument("--slot", required=True, choices=("morning", "evening"))
    parser.add_argument("--decision-date", required=True)
    parser.add_argument("--repo-root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    if args.command == "publish-locator":
        changed = write_locator(
            args.repo_root, build_locator(args.repo_root, args.slot, args.decision_date)
        )
        print(f"locator_path={LOCATOR_PATH.as_posix()}")
        print(f"locator_changed={'true' if changed else 'false'}")
        return 0
    delivery = consume(args.repo_root, args.slot, args.decision_date)
    print(render_delivery(delivery), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
