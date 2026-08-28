#!/usr/bin/env python3
"""P0-06 read-only consumer for append-only scheduled briefing authority.

Only the unique date/slot/revision bootstrap is read through floating ``main``.
Every read-model and H-24 delivery byte is then fetched from the full immutable
``source_commit`` named by the latest valid bootstrap.  Any ambiguity is a
fail-closed retrieval result; there is no prior-date or floating-artifact
fallback.
"""

from __future__ import annotations

import argparse
from datetime import date as calendar_date
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import secrets
import tempfile
import time
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config/scheduled_briefing_retrieval_contract.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
SYMBOL = re.compile(r"^[A-Za-z0-9._-]+$")


class ScheduledConsumerError(RuntimeError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise ScheduledConsumerError(f"{code}{': ' + detail if detail else ''}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        fail("ADAPTER_CONTRACT_UNREADABLE")
    required = {
        "schema_version", "repository", "branch", "source_contract_path",
        "delivery_locator_path", "allowed_slots", "bootstrap_path_template",
        "bootstrap_url_template", "immutable_raw_url_template",
        "bootstrap_policy", "max_revisions_per_slot", "stale_policy",
        "unavailable_status", "authority",
    }
    if not isinstance(value, dict) or set(value) != required:
        fail("ADAPTER_CONTRACT_FIELDS_MISMATCH")
    if value["schema_version"] != "scheduled_briefing_retrieval_authority/2":
        fail("ADAPTER_CONTRACT_VERSION_UNSUPPORTED")
    if value["repository"] != "yonggeun1021-hub/atlas-data" or value["branch"] != "main":
        fail("ADAPTER_REPOSITORY_IDENTITY_MISMATCH")
    if value["source_contract_path"] != "config/read_model_authority_contract.json":
        fail("ADAPTER_SOURCE_CONTRACT_PATH_MISMATCH")
    if value["delivery_locator_path"] != "data/briefing/daily_briefing_sources.json":
        fail("ADAPTER_DELIVERY_LOCATOR_PATH_MISMATCH")
    if value["allowed_slots"] != ["morning", "evening"]:
        fail("ADAPTER_SLOT_CONTRACT_MISMATCH")
    if value["max_revisions_per_slot"] != 99:
        fail("ADAPTER_REVISION_LIMIT_MISMATCH")
    if value["bootstrap_path_template"] != "evidence/scheduled_briefing_retrieval/{expected_kst_date}/{slot}/rev-{revision}.json":
        fail("ADAPTER_BOOTSTRAP_PATH_MISMATCH")
    if value["bootstrap_url_template"] != (
        "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/main/"
        "evidence/scheduled_briefing_retrieval/{expected_kst_date}/{slot}/rev-{revision}.json"
    ):
        fail("ADAPTER_BOOTSTRAP_URL_MISMATCH")
    if value["immutable_raw_url_template"] != (
        "https://raw.githubusercontent.com/yonggeun1021-hub/atlas-data/"
        "{source_commit}/{path}"
    ):
        fail("ADAPTER_IMMUTABLE_URL_MISMATCH")
    if value["bootstrap_policy"] != "UNIQUE_DATE_SLOT_APPEND_ONLY_SEQUENTIAL_REVISIONS":
        fail("ADAPTER_BOOTSTRAP_POLICY_MISMATCH")
    if value["stale_policy"] != "EXPECTED_DATE_AND_GENERATION_MUST_MATCH_OR_FAIL_CLOSED":
        fail("ADAPTER_STALE_POLICY_MISMATCH")
    if value["unavailable_status"] != "RETRIEVAL_AUTHORITY_UNAVAILABLE":
        fail("ADAPTER_UNAVAILABLE_STATUS_MISMATCH")
    expected_authority = {
        "retrieval_pointer_only": True,
        "collector_authority": False,
        "stage_authority": False,
        "buy_authority": False,
        "action_authority": False,
        "order_authority": False,
        "production_authority": False,
        "trading_authority": False,
    }
    if value["authority"] != expected_authority:
        fail("ADAPTER_AUTHORITY_BOUNDARY_INVALID")
    return value


def _default_get(url: str) -> tuple[int, bytes]:
    request = Request(url, headers={"Cache-Control": "no-cache"}, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            return response.status, response.read()
    except HTTPError as exc:
        return exc.code, b""
    except Exception as exc:
        fail("RETRIEVAL_TRANSPORT_FAILURE", type(exc).__name__)


def _with_nonce(url: str, nonce: str) -> str:
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode({'atlas_nonce': nonce})}"


def _json_object(raw: bytes, code: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail(code)
    if not isinstance(value, dict):
        fail(code)
    return value


def _safe_path(path) -> str:
    if not isinstance(path, str) or not path:
        fail("ARTIFACT_PATH_UNSAFE")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or ".." in parsed.parts:
        fail("ARTIFACT_PATH_UNSAFE", path)
    return parsed.as_posix()


def _expected_immutable_url(contract: dict, commit: str, path: str) -> str:
    return contract["immutable_raw_url_template"].format(
        source_commit=commit, path=_safe_path(path)
    )


def _validate_record(contract: dict, record: dict, commit: str) -> None:
    if not isinstance(record, dict) or set(record) != {
        "path", "git_blob_sha1", "content_sha256", "immutable_url"
    }:
        fail("ARTIFACT_RECORD_INVALID")
    path = _safe_path(record["path"])
    if not re.fullmatch(r"[0-9a-f]{40}", record.get("git_blob_sha1", "")):
        fail("ARTIFACT_BLOB_SHA_INVALID", path)
    if not SHA256.fullmatch(record.get("content_sha256", "")):
        fail("ARTIFACT_CONTENT_SHA_INVALID", path)
    if record.get("immutable_url") != _expected_immutable_url(contract, commit, path):
        fail("FLOATING_OR_WRONG_COMMIT_ARTIFACT_URL", path)


def validate_envelope(
    envelope: dict,
    contract: dict,
    expected_date: str,
    slot: str,
    revision: int,
) -> None:
    fields = {
        "schema_version", "slot", "expected_kst_date", "revision",
        "source_commit", "generation_id", "bootstrap_path", "bootstrap_url",
        "bootstrap_policy", "stale_detection", "required_artifacts",
        "delivery_locator", "delivery_artifacts",
        "compact_immutable_url_templates", "consumer_rules", "authority",
    }
    if not isinstance(envelope, dict) or set(envelope) != fields:
        fail("ENVELOPE_FIELDS_MISMATCH")
    if envelope.get("schema_version") != contract["schema_version"]:
        fail("ENVELOPE_SCHEMA_UNSUPPORTED")
    if envelope.get("slot") != slot or envelope.get("expected_kst_date") != expected_date:
        fail("ENVELOPE_EXPECTED_IDENTITY_MISMATCH")
    if envelope.get("revision") != revision:
        fail("ENVELOPE_REVISION_SEQUENCE_INVALID")
    commit = envelope.get("source_commit")
    generation = envelope.get("generation_id")
    if not isinstance(commit, str) or not FULL_SHA.fullmatch(commit):
        fail("ENVELOPE_SOURCE_COMMIT_INVALID")
    if not isinstance(generation, str) or not SHA256.fullmatch(generation):
        fail("ENVELOPE_GENERATION_ID_INVALID")
    revision_text = f"{revision:03d}"
    expected_path = contract["bootstrap_path_template"].format(
        expected_kst_date=expected_date, slot=slot, revision=revision_text
    )
    expected_url = contract["bootstrap_url_template"].format(
        expected_kst_date=expected_date, slot=slot, revision=revision_text
    )
    if envelope.get("bootstrap_path") != expected_path or envelope.get("bootstrap_url") != expected_url:
        fail("ENVELOPE_BOOTSTRAP_IDENTITY_MISMATCH")
    if envelope.get("bootstrap_policy") != contract["bootstrap_policy"]:
        fail("ENVELOPE_BOOTSTRAP_POLICY_MISMATCH")
    if envelope.get("stale_detection") != "PASS":
        fail("ENVELOPE_STALE_DETECTION_NOT_PASS")
    if envelope.get("authority") != contract["authority"]:
        fail("ENVELOPE_AUTHORITY_ESCALATION")
    expected_rules = {
        "bootstrap_missing_or_invalid": "RETRIEVAL_AUTHORITY_UNAVAILABLE",
        "expected_date_mismatch": "RETRIEVAL_AUTHORITY_UNAVAILABLE",
        "generation_mismatch": "RETRIEVAL_AUTHORITY_UNAVAILABLE",
        "bootstrap_query_nonce_required": True,
        "floating_artifact_fallback_allowed": False,
        "prior_date_fallback_allowed": False,
        "revision_discovery": "ASCENDING_FROM_001_STOP_AT_FIRST_MISSING_USE_HIGHEST_VALID",
    }
    if envelope.get("consumer_rules") != expected_rules:
        fail("ENVELOPE_CONSUMER_RULES_MISMATCH")

    required_records = envelope.get("required_artifacts")
    if not isinstance(required_records, list) or [row.get("path") for row in required_records] != [
        "data/briefing/step0_status.json", "data/briefing_status.json"
    ]:
        fail("ENVELOPE_REQUIRED_ARTIFACTS_MISMATCH")
    for record in required_records:
        _validate_record(contract, record, commit)

    locator = envelope.get("delivery_locator")
    locator_fields = {
        "schema_version", "slot", "decision_date", "revision", "index_path",
        "index_sha256", "packet_path", "packet_file_sha256", "packet_sha256",
        "briefing_path", "briefing_sha256", "delivery_scope", "authority",
    }
    if not isinstance(locator, dict) or set(locator) != locator_fields:
        fail("DELIVERY_LOCATOR_FIELDS_MISMATCH")
    if locator.get("schema_version") != "daily_briefing_delivery/1":
        fail("DELIVERY_LOCATOR_SCHEMA_UNSUPPORTED")
    if locator.get("slot") != slot or locator.get("decision_date") != expected_date:
        fail("DELIVERY_LOCATOR_IDENTITY_MISMATCH")
    expected_delivery_authority = {
        "stage": False, "buy": False, "action": False, "order": False,
        "production": False, "trading": False,
    }
    if locator.get("delivery_scope") != [
        "INVESTMENT_DECISION_REVIEW", "INVESTMENT_REVIEW_SHADOW",
        "SHADOW_ENTRY_REVIEW",
    ] or locator.get("authority") != expected_delivery_authority:
        fail("DELIVERY_LOCATOR_AUTHORITY_OR_SCOPE_INVALID")
    if not isinstance(locator.get("revision"), int) or isinstance(locator.get("revision"), bool) or locator["revision"] < 1:
        fail("DELIVERY_LOCATOR_REVISION_INVALID")
    if not SHA256.fullmatch(locator.get("packet_sha256", "")):
        fail("DELIVERY_PACKET_SHA_INVALID")
    base = f"evidence/daily_briefing/{slot}/{expected_date}/"
    for field in ("index_path", "packet_path", "briefing_path"):
        if not _safe_path(locator.get(field)).startswith(base):
            fail("DELIVERY_ARTIFACT_PATH_IDENTITY_MISMATCH", field)
    delivery_records = envelope.get("delivery_artifacts")
    expected_delivery_paths = [
        contract["delivery_locator_path"], locator["index_path"],
        locator["packet_path"], locator["briefing_path"],
    ]
    if not isinstance(delivery_records, list) or [row.get("path") for row in delivery_records] != expected_delivery_paths:
        fail("DELIVERY_ARTIFACT_SET_MISMATCH")
    for record in delivery_records:
        _validate_record(contract, record, commit)
    by_path = {row["path"]: row for row in delivery_records}
    for path_field, hash_field in (
        ("index_path", "index_sha256"),
        ("packet_path", "packet_file_sha256"),
        ("briefing_path", "briefing_sha256"),
    ):
        if by_path[locator[path_field]]["content_sha256"] != locator[hash_field]:
            fail("DELIVERY_LOCATOR_ARTIFACT_HASH_MISMATCH", locator[path_field])
    templates = envelope.get("compact_immutable_url_templates")
    if not isinstance(templates, dict) or set(templates) != {"krx", "dart", "sec"}:
        fail("COMPACT_TEMPLATE_SET_MISMATCH")
    for template in templates.values():
        if not isinstance(template, str) or f"/{commit}/" not in template or "/main/" in template:
            fail("COMPACT_TEMPLATE_NOT_IMMUTABLE")
        if template.count("{symbol}") != 1:
            fail("COMPACT_TEMPLATE_INVALID")


def discover_latest(
    expected_date: str,
    slot: str,
    *,
    contract: dict | None = None,
    get=_default_get,
    nonce_factory=lambda: secrets.token_hex(16),
    first_revision_wait_seconds: float = 0,
    poll_interval_seconds: float = 15,
    sleeper=time.sleep,
    monotonic=time.monotonic,
) -> dict:
    contract = contract or _load_contract()
    if slot not in contract["allowed_slots"]:
        fail("SLOT_UNSUPPORTED")
    try:
        if len(expected_date) != 10:
            raise ValueError
        calendar_date.fromisoformat(expected_date)
    except (TypeError, ValueError):
        fail("EXPECTED_KST_DATE_INVALID")
    if (
        isinstance(first_revision_wait_seconds, bool)
        or not isinstance(first_revision_wait_seconds, (int, float))
        or not math.isfinite(first_revision_wait_seconds)
        or first_revision_wait_seconds < 0
        or first_revision_wait_seconds > 900
    ):
        fail("FIRST_REVISION_WAIT_INVALID")
    if (
        isinstance(poll_interval_seconds, bool)
        or not isinstance(poll_interval_seconds, (int, float))
        or not math.isfinite(poll_interval_seconds)
        or poll_interval_seconds <= 0
        or poll_interval_seconds > 60
    ):
        fail("POLL_INTERVAL_INVALID")
    deadline = monotonic() + first_revision_wait_seconds
    latest = None
    for revision in range(1, contract["max_revisions_per_slot"] + 1):
        url = contract["bootstrap_url_template"].format(
            expected_kst_date=expected_date, slot=slot, revision=f"{revision:03d}"
        )
        while True:
            status, raw = get(_with_nonce(url, nonce_factory()))
            if status != 404 or latest is not None or revision != 1:
                break
            remaining = deadline - monotonic()
            if remaining <= 0:
                break
            sleeper(min(poll_interval_seconds, remaining))
        if status == 404:
            if latest is None:
                fail("RETRIEVAL_AUTHORITY_UNAVAILABLE")
            break
        if status != 200:
            fail("RETRIEVAL_AUTHORITY_UNAVAILABLE", f"HTTP_{status}")
        envelope = _json_object(raw, "ENVELOPE_JSON_INVALID")
        validate_envelope(envelope, contract, expected_date, slot, revision)
        latest = envelope
    return latest


def _git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


DAILY_PACKET_STATUSES = {
    "READY", "PENDING", "DATA_BLOCKED", "POLICY_BLOCKED", "DEGRADED",
    "UNAVAILABLE", "UNKNOWN",
}


def _validate_pinned_delivery_packet(packet: dict, expected_date: str, slot: str) -> None:
    """Validate the immutable delivery packet without consulting local state.

    ``daily_orchestrator.validate_packet`` is intentionally a producer-side
    rebuild: it re-derives non-frozen components from the checkout on disk.
    Calling it in an external consumer therefore compares a packet from the
    envelope's historical ``source_commit`` with whichever newer checkout the
    consumer happens to run, and can reject valid immutable deliveries.

    The consumer's trust boundary is different.  ``_fetch_record`` already
    binds the packet bytes to both hashes recorded by the append-only envelope
    and to a full immutable commit URL.  Here we independently verify the
    packet's self-hash, identity, fixed authority boundary, and internal status
    counts.  Full semantic rebuild remains a producer gate before publication;
    it must never be silently re-run against a different local generation.
    """
    required = {
        "schema_version", "contract_version", "output_schema_version", "slot",
        "decision_date", "generated_at", "capture_mode",
        "component_status_counts", "components", "authority", "frozen_sources",
        "unresolved_boundaries", "packet_sha256",
    }
    if not isinstance(packet, dict) or set(packet) != required:
        fail("DELIVERY_PACKET_FIELDS_MISMATCH")
    if (
        packet.get("schema_version") != 1
        or packet.get("contract_version")
        not in ("daily_orchestrator/3", "daily_orchestrator/4", "daily_orchestrator/5")
        or packet.get("output_schema_version") != "daily_briefing_packet/1"
        or packet.get("capture_mode")
        != "provider_free_aggregation_of_persisted_evidence_only"
    ):
        fail("DELIVERY_PACKET_SCHEMA_UNSUPPORTED")
    if packet.get("slot") != slot or packet.get("decision_date") != expected_date:
        fail("DELIVERY_PACKET_IDENTITY_MISMATCH")
    digest = packet.get("packet_sha256")
    unsigned = dict(packet)
    unsigned.pop("packet_sha256", None)
    if not isinstance(digest, str) or not SHA256.fullmatch(digest):
        fail("DELIVERY_PACKET_SHA_INVALID")
    if hashlib.sha256(canonical_json(unsigned).encode("utf-8")).hexdigest() != digest:
        fail("DELIVERY_PACKET_SELF_HASH_MISMATCH")

    expected_authority = {
        "aggregation_only": True,
        "component_build_authorized": True,
        "source_interpretation_authorized": False,
        "regime_score_authorized": False,
        "rotation_ranking_authorized": False,
        "discovery_promotion_authorized": False,
        "rule_pass_fail_authorized": False,
        "portfolio_sizing_authorized": False,
        "action_generation_authorized": False,
        "order_generation_authorized": False,
        "production_authorized": False,
        "trading_authorized": False,
    }
    if packet.get("authority") != expected_authority:
        fail("DELIVERY_PACKET_AUTHORITY_INVALID")
    components = packet.get("components")
    counts = packet.get("component_status_counts")
    if (
        not isinstance(components, list)
        or not components
        or not isinstance(counts, dict)
        or set(counts) != DAILY_PACKET_STATUSES
    ):
        fail("DELIVERY_PACKET_COMPONENT_SET_INVALID")
    observed = {status: 0 for status in DAILY_PACKET_STATUSES}
    component_ids = set()
    for row in components:
        if not isinstance(row, dict) or row.get("status") not in DAILY_PACKET_STATUSES:
            fail("DELIVERY_PACKET_COMPONENT_INVALID")
        component_id = row.get("component_id")
        if not isinstance(component_id, str) or not component_id or component_id in component_ids:
            fail("DELIVERY_PACKET_COMPONENT_ID_INVALID")
        component_ids.add(component_id)
        if any(row.get(key) is not False for key in (
            "decision_eligible", "action_eligible", "order_eligible"
        )):
            fail("DELIVERY_PACKET_COMPONENT_AUTHORITY_INVALID", component_id)
        observed[row["status"]] += 1
    if counts != observed:
        fail("DELIVERY_PACKET_STATUS_COUNTS_MISMATCH")
    if not isinstance(packet.get("frozen_sources"), dict) or not isinstance(
        packet.get("unresolved_boundaries"), list
    ):
        fail("DELIVERY_PACKET_BOUNDARY_FIELDS_INVALID")


def _fetch_record(record: dict, get, nonce_factory) -> bytes:
    status, raw = get(_with_nonce(record["immutable_url"], nonce_factory()))
    if status != 200:
        fail("IMMUTABLE_ARTIFACT_UNAVAILABLE", record["path"])
    if hashlib.sha256(raw).hexdigest() != record["content_sha256"]:
        fail("IMMUTABLE_ARTIFACT_CONTENT_HASH_MISMATCH", record["path"])
    if _git_blob_sha1(raw) != record["git_blob_sha1"]:
        fail("IMMUTABLE_ARTIFACT_BLOB_HASH_MISMATCH", record["path"])
    return raw


def consume(
    expected_date: str,
    slot: str,
    symbols: dict[str, list[str]] | None = None,
    *,
    contract: dict | None = None,
    get=_default_get,
    nonce_factory=lambda: secrets.token_hex(16),
    first_revision_wait_seconds: float = 0,
    poll_interval_seconds: float = 15,
    sleeper=time.sleep,
    monotonic=time.monotonic,
) -> tuple[dict[str, bytes], dict]:
    contract = contract or _load_contract()
    envelope = discover_latest(
        expected_date,
        slot,
        contract=contract,
        get=get,
        nonce_factory=nonce_factory,
        first_revision_wait_seconds=first_revision_wait_seconds,
        poll_interval_seconds=poll_interval_seconds,
        sleeper=sleeper,
        monotonic=monotonic,
    )
    raw_by_path = {}
    for record in envelope["required_artifacts"] + envelope["delivery_artifacts"]:
        raw_by_path[record["path"]] = _fetch_record(record, get, nonce_factory)
    locator_raw = raw_by_path[contract["delivery_locator_path"]]
    if _json_object(locator_raw, "DELIVERY_LOCATOR_JSON_INVALID") != envelope["delivery_locator"]:
        fail("DELIVERY_LOCATOR_ENVELOPE_MISMATCH")
    locator = envelope["delivery_locator"]
    index = _json_object(raw_by_path[locator["index_path"]], "DELIVERY_INDEX_JSON_INVALID")
    revisions = index.get("revisions")
    latest = index.get("latest_revision")
    revision_name = f"rev-{latest:03d}" if isinstance(latest, int) and not isinstance(latest, bool) else ""
    if (
        index.get("schema_version") != 1
        or index.get("slot") != slot
        or index.get("decision_date") != expected_date
        or not isinstance(revisions, list)
        or not revisions
        or latest != len(revisions)
        or locator.get("revision") != latest
        or revisions[-1].get("revision") != latest
        or revisions[-1].get("path") != revision_name
        or revisions[-1].get("packet_sha256") != locator.get("packet_sha256")
        or locator.get("packet_path") != f"evidence/daily_briefing/{slot}/{expected_date}/{revision_name}/packet.json"
        or locator.get("briefing_path") != f"evidence/daily_briefing/{slot}/{expected_date}/{revision_name}/briefing.md"
    ):
        fail("DELIVERY_INDEX_OR_REVISION_IDENTITY_MISMATCH")

    step_path, health_path = [row["path"] for row in envelope["required_artifacts"]]
    step = _json_object(raw_by_path[step_path], "STEP0_JSON_INVALID")
    health = _json_object(raw_by_path[health_path], "HEALTH_JSON_INVALID")
    for value, name in ((step, step_path), (health, health_path)):
        if value.get("expected_kst_date") != expected_date:
            fail("IMMUTABLE_ARTIFACT_STALE_DATE", name)
        if (value.get("generation") or {}).get("generation_id") != envelope["generation_id"]:
            fail("IMMUTABLE_ARTIFACT_GENERATION_MISMATCH", name)
    packet = _json_object(raw_by_path[locator["packet_path"]], "DELIVERY_PACKET_JSON_INVALID")
    _validate_pinned_delivery_packet(packet, expected_date, slot)
    if (
        packet.get("slot") != slot
        or packet.get("decision_date") != expected_date
        or packet.get("packet_sha256") != locator["packet_sha256"]
    ):
        fail("DELIVERY_PACKET_IDENTITY_MISMATCH")

    for market, requested in sorted((symbols or {}).items()):
        template = envelope["compact_immutable_url_templates"].get(market)
        if template is None:
            fail("COMPACT_MARKET_UNSUPPORTED", market)
        for symbol in sorted(set(requested)):
            if not SYMBOL.fullmatch(symbol):
                fail("COMPACT_SYMBOL_INVALID", symbol)
            path = PurePosixPath(template.split(f"/{envelope['source_commit']}/", 1)[1].format(symbol=symbol)).as_posix()
            url = template.format(symbol=symbol)
            status, raw = get(_with_nonce(url, nonce_factory()))
            if status != 200:
                fail("IMMUTABLE_ARTIFACT_UNAVAILABLE", path)
            value = _json_object(raw, "COMPACT_JSON_INVALID")
            if (value.get("source") or {}).get("collected_for_kst_date") != expected_date:
                fail("IMMUTABLE_ARTIFACT_STALE_DATE", path)
            if (value.get("generation") or {}).get("generation_id") != envelope["generation_id"]:
                fail("IMMUTABLE_ARTIFACT_GENERATION_MISMATCH", path)
            raw_by_path[path] = raw
    return raw_by_path, envelope


def persist(output_dir: Path, raw_by_path: dict[str, bytes], envelope: dict) -> None:
    output_dir = output_dir.resolve()
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output_dir.parent) as temp_name:
        staging = Path(temp_name)
        for relative, raw in raw_by_path.items():
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        (staging / "scheduled_retrieval_authority.json").write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if output_dir.exists():
            fail("OUTPUT_ALREADY_EXISTS")
        staging.replace(output_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-kst-date", required=True)
    parser.add_argument("--slot", required=True, choices=("morning", "evening"))
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--krx-symbol", action="append", default=[])
    parser.add_argument("--dart-symbol", action="append", default=[])
    parser.add_argument("--sec-symbol", action="append", default=[])
    parser.add_argument(
        "--wait-timeout-seconds",
        type=float,
        default=0,
        help="bounded wait for the first date/slot authority revision (max 900)",
    )
    parser.add_argument(
        "--poll-interval-seconds",
        type=float,
        default=15,
        help="first-revision poll interval (1..60 seconds)",
    )
    args = parser.parse_args(argv)
    raw, envelope = consume(args.expected_kst_date, args.slot, {
        "krx": args.krx_symbol, "dart": args.dart_symbol, "sec": args.sec_symbol,
    }, first_revision_wait_seconds=args.wait_timeout_seconds,
       poll_interval_seconds=args.poll_interval_seconds)
    persist(args.output_dir, raw, envelope)
    print(json.dumps({
        "status": "PASS",
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "expected_kst_date": envelope["expected_kst_date"],
        "slot": envelope["slot"],
        "revision": envelope["revision"],
        "stale_detection": envelope["stale_detection"],
        "authority": envelope["authority"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
