#!/usr/bin/env python3
"""One-time coverage recalculation for the crypto rotation 30-day strength input.

Rule ``RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1`` (user ratification
``evidence/authority/USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json``,
sha256 bound in ``config/crypto_rotation_30d_coverage_recalc_v1.json``):

    코인 로테이션 30일 강도에 한해, 나중에 확정된 분류 추가로 커버리지가 채워지는
    과거 관측일(2026-08-19 이후)은 분류 확정 뒤 한 번만 재계산해 쓰고 '재계산'
    표시를 남긴다. 코인 시장 판정의 LEADERSHIP 축에는 적용하지 않는다.

This is an additive layer. It never edits the CR-06 breadth transform, the
CR-07 leadership transform, committed ``data/observations/crypto_leadership``
packets, the crypto market regime, or ``current_catalog_backfill_authorized``.

Two pieces:

1. ``recalculate`` (CLI ``recalc``) writes one write-once *point* per observation
   day ``d >= 2026-08-19`` whose point-in-time CR-06 point is
   ``TAXONOMY_COVERAGE_UNKNOWN`` and becomes observed when the assets without a
   record effective on ``d`` take their earliest *confirmed* later record. The
   prices are the ones in the same as-captured snapshot (vintage ``d + 1``);
   only the taxonomy is later-confirmed. A second run for the same day is
   refused; ``verify`` re-derives every committed point (idempotent).

2. ``recalculated_primary_window`` is read by the rotation confirmation layer
   only for the crypto ``primary_30d`` strength input: when the committed
   leadership packet's 30-day window is UNKNOWN solely because of
   ``TAXONOMY_COVERAGE_UNKNOWN`` source points and every such day has a
   committed recalculated point, the window is rebuilt with the unmodified
   CR-07 window code, substituting only those points. The result carries the
   '재계산' mark with the point shas and recalculation times.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Callable, Optional


HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parent
CONFIG_RELATIVE_PATH = "config/crypto_rotation_30d_coverage_recalc_v1.json"
CONFIG_SCHEMA_VERSION = "crypto_rotation_30d_coverage_recalc_policy/1"
POINT_SCHEMA_VERSION = "crypto_rotation_30d_coverage_recalc_point/1"
RULE_ID = "RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1"
MARK_KO = "재계산"
RAW_RELATIVE_ROOT = "evidence/crypto/breadth/raw"
ROTATION_EVIDENCE_RELATIVE_ROOT = "evidence/rotation/confirmation"
CONFIG_PATHS = {
    "universe": "config/crypto_breadth_universe_policy.json",
    "exclusion_taxonomy": "config/crypto_breadth_exclusion_taxonomy.json",
    "identity_exceptions": "config/crypto_asset_identity_exceptions.json",
    "leadership_policy": "config/crypto_leadership_policy.json",
    "leadership_contract": "config/crypto_leadership_contract.json",
    "sector_taxonomy": "config/crypto_asset_taxonomy.json",
}


class CoverageRecalcError(ValueError):
    pass


def _fail(code: str, detail: str = "") -> None:
    raise CoverageRecalcError(f"{code}:{detail}" if detail else code)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def render_json(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        _fail("JSON_READ_FAILED", f"{path}:{exc}")


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BREADTH = _load_module("atlas_crypto_breadth_for_coverage_recalc", REPO_ROOT / ".github/scripts/crypto_breadth.py")


def _utc_text(value: dt.datetime) -> str:
    return value.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(dt.timezone.utc)


# ---------------------------------------------------------------------------
# Config (bound to the user ratification record sha)
# ---------------------------------------------------------------------------

def load_config(root: Path = REPO_ROOT, required: bool = True) -> Optional[dict]:
    """The recalc policy; ``None`` only when absent and not required (fixture roots)."""
    root = Path(root)
    path = root / CONFIG_RELATIVE_PATH
    if not path.is_file():
        if required:
            _fail("RECALC_CONFIG_MISSING", CONFIG_RELATIVE_PATH)
        return None
    config = _read_json(path)
    if config.get("schema_version") != CONFIG_SCHEMA_VERSION or config.get("rule_id") != RULE_ID:
        _fail("RECALC_CONFIG_SCHEMA_INVALID")
    record = config.get("ratification_record") or {}
    record_path = root / str(record.get("repo_path", ""))
    if not record_path.is_file():
        _fail("RECALC_RATIFICATION_RECORD_MISSING", str(record.get("repo_path")))
    if file_sha256(record_path) != record.get("sha256"):
        _fail("RECALC_RATIFICATION_RECORD_SHA_MISMATCH")
    ratification = _read_json(record_path)
    if ratification.get("id") != record.get("record_id") or ratification.get("status") != "RATIFIED":
        _fail("RECALC_RATIFICATION_RECORD_ID_MISMATCH")
    rules = [r for r in ratification.get("rules") or [] if r.get("rule_id") == RULE_ID]
    if len(rules) != 1 or rules[0].get("plan_item") != record.get("plan_item"):
        _fail("RECALC_RULE_NOT_IN_RATIFICATION_RECORD")
    if config.get("user_sentence_ko") not in ratification.get("user_sentence_verbatim", ""):
        _fail("RECALC_USER_SENTENCE_MISMATCH")
    applies = config.get("applies_to") or {}
    if (
        applies.get("market") != "CRYPTO"
        or applies.get("window_id") != "primary_30d"
        or applies.get("use") != "ROTATION_CONFIRMATION_CRYPTO_30D_STRENGTH_INPUT_ONLY"
        or "CRYPTO_MARKET_REGIME_LEADERSHIP_AXIS" not in config.get("not_applied_to", [])
    ):
        _fail("RECALC_SCOPE_MISMATCH")
    if config.get("eligible_observation_from") != "2026-08-19":
        _fail("RECALC_ELIGIBLE_FROM_MISMATCH")
    if config.get("eligible_point_in_time_unknown_reason") != "TAXONOMY_COVERAGE_UNKNOWN":
        _fail("RECALC_ELIGIBLE_REASON_MISMATCH")
    if config.get("write_once") is not True or config.get("current_catalog_backfill_authorized_changed") is not False:
        _fail("RECALC_WRITE_ONCE_OR_BACKFILL_FLAG_MISMATCH")
    if config.get("mark") != {"recalculated": True, "label_ko": MARK_KO}:
        _fail("RECALC_MARK_MISMATCH")
    if config.get("classification_source", {}).get("path") != CONFIG_PATHS["exclusion_taxonomy"]:
        _fail("RECALC_CLASSIFICATION_SOURCE_MISMATCH")
    return config


def point_path(root: Path, config: dict, as_of: str) -> Path:
    return Path(root) / config["evidence_root"] / as_of / "point.json"


def _paths(root: Path) -> dict:
    return {key: Path(root) / relative for key, relative in CONFIG_PATHS.items()}


# ---------------------------------------------------------------------------
# Classification confirmation (git first-parent history of the checkout)
# ---------------------------------------------------------------------------

def git_confirmations(root: Path, relative_path: str = CONFIG_PATHS["exclusion_taxonomy"]) -> Callable:
    """Return ``lookup(record) -> {"commit", "committed_at_utc", "history_shallow"} | None``.

    The first first-parent commit whose ratified file contains the exact record
    (asset, category, effective_from, effective_to) is its confirmation. A
    shallow clone cannot prove the first commit, so it is refused (fail closed).
    """
    root = Path(root)

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout

    try:
        shallow = git("rev-parse", "--is-shallow-repository").strip() == "true"
    except (OSError, subprocess.CalledProcessError):
        _fail("CLASSIFICATION_HISTORY_UNAVAILABLE", str(root))
    if shallow:
        _fail("REFUSED_SHALLOW_HISTORY", "run `git fetch --unshallow` before recalculating")
    lines = [line for line in git("log", "--first-parent", "--reverse", "--format=%H %cI", "HEAD", "--", relative_path).splitlines() if line]
    first = {}
    for line in lines:
        sha, committed = line.split(" ", 1)
        try:
            data = json.loads(git("show", f"{sha}:{relative_path}"))
        except (subprocess.CalledProcessError, json.JSONDecodeError):
            continue
        if data.get("approval_status") != "RATIFIED":
            continue
        for record in data.get("records") or []:
            first.setdefault(_record_key(record), {
                "commit": sha, "committed_at_utc": _utc_text(dt.datetime.fromisoformat(committed)), "history_shallow": False,
            })

    def lookup(record):
        return first.get(_record_key(record))

    return lookup


# ---------------------------------------------------------------------------
# Point recalculation
# ---------------------------------------------------------------------------

RECORD_FIELDS = ("canonical_asset_id", "category", "effective_from", "effective_to", "reason")


def _record_key(record: dict) -> tuple:
    return (record.get("canonical_asset_id"), record.get("category"), record.get("effective_from"), record.get("effective_to"))


def _public_record(record: dict) -> dict:
    return {key: record[key] for key in RECORD_FIELDS}


def _covers(record: dict, day: dt.date) -> bool:
    start = dt.date.fromisoformat(record["effective_from"])
    end = dt.date.fromisoformat(record["effective_to"]) if record["effective_to"] else dt.date.max
    return start <= day <= end


def _taxonomy_view(taxonomy_raw: dict, day: dt.date, point_in_time_records: list, resolving: list) -> dict:
    """Point-in-time records plus one-day synthetic records for later-confirmed classifications."""
    records = [_public_record(record) for record in point_in_time_records]
    for item in resolving:
        records.append({
            "canonical_asset_id": item["canonical_asset_id"],
            "category": item["category"],
            "effective_from": day.isoformat(),
            "effective_to": day.isoformat(),
            "reason": (
                f"{RULE_ID} one-time recalculation: confirmed record effective "
                f"{item['effective_from']} applied to {day.isoformat()}"
            ),
        })
    view = {key: copy.deepcopy(value) for key, value in taxonomy_raw.items() if key != "records"}
    view["records"] = sorted(records, key=lambda r: (r["canonical_asset_id"], r["effective_from"]))
    return view


def _transform(snapshot: Path, paths: dict, taxonomy_path: Path) -> dict:
    return BREADTH.build_transform(
        snapshot,
        universe_policy_path=paths["universe"],
        exclusion_taxonomy_path=taxonomy_path,
        identity_exceptions_path=paths["identity_exceptions"],
    )


def _transform_with_view(snapshot: Path, paths: dict, view: dict) -> tuple:
    data = render_json(view)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "crypto_breadth_exclusion_taxonomy.recalc_view.json"
        path.write_bytes(data)
        return _transform(snapshot, paths, path), hashlib.sha256(data).hexdigest()


def _visited_assets(point: dict) -> set:
    universe = point["universe"]
    return (
        {item["canonical_asset_id"] for item in universe["members"]}
        | {item["canonical_asset_id"] for item in universe["missing_observation_members"]}
        | {item["canonical_asset_id"] for item in universe["taxonomy_excluded_before_cutoff"]}
        | {item["canonical_asset_id"] for item in universe["taxonomy_unknown_before_cutoff"]}
    )


def _unknown_assets(point: dict) -> list:
    return [
        {"canonical_asset_id": u["canonical_asset_id"], "rank_before_taxonomy": u["rank_before_taxonomy"]}
        for u in point["universe"]["taxonomy_unknown_before_cutoff"]
    ]


def derive_point_body(root: Path, config: dict, snapshot: Path, recorded: Optional[dict] = None,
                      confirmed: Optional[Callable] = None, now: Optional[dt.datetime] = None) -> dict:
    """Deterministic recalculation body for one snapshot (no wall-clock, no git data in the body).

    Point in time = records effective on the day AND confirmed (committed) at or
    before the snapshot's ``available_at``. A record committed later -- including
    a backdated one whose ``effective_from`` is on or before the day -- is a
    later-confirmed classification: it can only enter as a resolving record.

    ``recorded`` given (verify) -> re-derive from the point's own recorded
    point-in-time and resolving records (each must still exist in the current
    classification file), so unrelated later additions never change the body.
    Otherwise (recalc) ``confirmed(record)`` supplies confirmation info and every
    resolving record must be confirmed at or before ``now``.
    """
    root = Path(root)
    paths = _paths(root)
    vintage = dt.date.fromisoformat(Path(snapshot).name)
    day = vintage - dt.timedelta(days=1)
    as_of = day.isoformat()
    if as_of < config["eligible_observation_from"]:
        return {"status": "IGNORED_BEFORE_ELIGIBLE_FROM", "as_of_date": as_of}
    available_at = BREADTH.downloaded_at(Path(snapshot), vintage)
    confirmations = {}
    if recorded is None:
        taxonomy_raw = _read_json(paths["exclusion_taxonomy"])
        taxonomy = BREADTH.load_exclusion_taxonomy(paths["exclusion_taxonomy"])
        if taxonomy["approval_status"] != "RATIFIED":
            _fail("CLASSIFICATION_SOURCE_UNRATIFIED")
        header = {key: copy.deepcopy(value) for key, value in taxonomy_raw.items() if key != "records"}
        point_in_time_records = []
        for record in taxonomy_raw["records"]:
            if not _covers(record, day):
                continue
            info = confirmed(_public_record(record)) if confirmed else None
            if info is not None and _parse_utc(info["committed_at_utc"]) <= _parse_utc(available_at):
                point_in_time_records.append(_public_record(record))
    else:
        # Frozen: only the point's own recorded view; later edits to the classification
        # file are reported by ``classification_drift`` and never change the body.
        header = copy.deepcopy(recorded["classification_view_header"])
        point_in_time_records = recorded["point_in_time_records"]
    taxonomy_raw = header | {"records": []}
    pit, pit_view_sha = _transform_with_view(snapshot, paths, _taxonomy_view(taxonomy_raw, day, point_in_time_records, []))
    if pit["status"] != "UNKNOWN" or pit["unknown_reason"] != config["eligible_point_in_time_unknown_reason"]:
        return {"status": "NOT_ELIGIBLE_POINT_IN_TIME", "as_of_date": as_of,
                "point_in_time_status": pit["status"], "point_in_time_unknown_reason": pit["unknown_reason"]}
    if recorded is None:
        covered = {record["canonical_asset_id"] for record in point_in_time_records}
        candidates = []
        for asset_id, records in sorted(taxonomy["_records_by_asset"].items()):
            if asset_id in covered:
                continue
            for record in records:
                if record["_end"] is not None and record["_end"] < day:
                    continue
                public = _public_record(record)
                info = confirmed(public) if confirmed else None
                if info is None or (now is not None and _parse_utc(info["committed_at_utc"]) > now):
                    continue
                if record["_start"] <= day and _parse_utc(info["committed_at_utc"]) <= _parse_utc(available_at):
                    continue  # would already be point in time (cannot happen for an uncovered asset)
                candidates.append(public | {
                    "kind": "BACKDATED_COMMITTED_AFTER_SNAPSHOT" if record["_start"] <= day else "EFFECTIVE_AFTER_DAY",
                })
                confirmations[asset_id] = info
                break
        first, _ = _transform_with_view(snapshot, paths, _taxonomy_view(taxonomy_raw, day, point_in_time_records, candidates))
        if first["status"] != "OBSERVED_UNCLASSIFIED":
            return {
                "status": "STILL_UNKNOWN_AFTER_CONFIRMED_CLASSIFICATIONS", "as_of_date": as_of,
                "unknown_reason": first["unknown_reason"], "unclassified_assets_before_cutoff": _unknown_assets(first),
            }
        visited = _visited_assets(first)
        resolving = [item for item in candidates if item["canonical_asset_id"] in visited]
    else:
        resolving = recorded["resolving_records"]
    for item in resolving:
        if item["effective_to"] is not None and item["effective_to"] < as_of:
            _fail("RESOLVING_RECORD_ENDS_BEFORE_DAY", f"{as_of}:{item['canonical_asset_id']}")
    point, view_sha = _transform_with_view(snapshot, paths, _taxonomy_view(taxonomy_raw, day, point_in_time_records, resolving))
    if point["status"] != "OBSERVED_UNCLASSIFIED":
        return {"status": "STILL_UNKNOWN_AFTER_CONFIRMED_CLASSIFICATIONS", "as_of_date": as_of,
                "unknown_reason": point["unknown_reason"], "unclassified_assets_before_cutoff": _unknown_assets(point)}
    manifest_sha = file_sha256(Path(snapshot) / "_manifest.json")
    if point["lineage"]["manifest_sha256"] != manifest_sha or pit["lineage"]["manifest_sha256"] != manifest_sha:
        _fail("RECALC_SNAPSHOT_MANIFEST_MISMATCH", as_of)
    if point["lineage"]["available_at"] != available_at:
        _fail("RECALC_SNAPSHOT_AVAILABLE_AT_MISMATCH", as_of)
    latest_days = sorted({m["latest_finalized_day"] for m in point["universe"]["members"]})
    if latest_days and latest_days[-1] > as_of:
        _fail("RECALC_PRICE_LOOKAHEAD", as_of)
    body = {
        "schema_version": POINT_SCHEMA_VERSION,
        "rule_id": RULE_ID,
        "policy_id": config["policy_id"],
        "ratification_record": copy.deepcopy(config["ratification_record"]),
        "market": "CRYPTO",
        "as_of_date": as_of,
        "recalculated": True,
        "mark_ko": MARK_KO,
        "scope": config["applies_to"]["use"],
        "not_applied_to": list(config["not_applied_to"]),
        "snapshot": {
            "path": f"{RAW_RELATIVE_ROOT}/{Path(snapshot).name}",
            "vintage_date": vintage.isoformat(),
            "manifest_sha256": manifest_sha,
            "available_at": available_at,
            "identity_exceptions_sha256": point["lineage"]["identity_policy_sha256"],
        },
        "prices_point_in_time": {
            "policy": config["prices"],
            "latest_finalized_day_max": latest_days[-1] if latest_days else None,
            "same_manifest_as_point_in_time_point": True,
        },
        "point_in_time": {
            "definition": "RECORDS_EFFECTIVE_ON_DAY_AND_COMMITTED_AT_OR_BEFORE_SNAPSHOT_AVAILABLE_AT",
            "status": pit["status"],
            "unknown_reason": pit["unknown_reason"],
            "taxonomy_unknown_before_cutoff": _unknown_assets(pit),
            "view_sha256": pit_view_sha,
        },
        "classification_view_header": header,
        "point_in_time_records": sorted(point_in_time_records, key=lambda r: (r["canonical_asset_id"], r["effective_from"])),
        "resolving_records": [
            {key: item[key] for key in RECORD_FIELDS + ("kind",)}
            for item in sorted(resolving, key=lambda r: r["canonical_asset_id"])
        ],
        "recalculated_view_sha256": view_sha,
        "recalculated_source_point": point,
    }
    return {"status": "RECALCULATED", "as_of_date": as_of, "body": body,
            "confirmations": {item["canonical_asset_id"]: confirmations.get(item["canonical_asset_id"]) for item in resolving}}


def committed_rotation_dates(root: Path) -> list:
    base = Path(root) / ROTATION_EVIDENCE_RELATIVE_ROOT / "CRYPTO"
    return sorted(p.parent.name for p in base.glob("*/packet.json")) if base.is_dir() else []


def recalculate(root: Path = REPO_ROOT, *, write: bool = False, dates: Optional[list] = None,
                now: Optional[dt.datetime] = None, confirmed: Optional[Callable] = None) -> dict:
    root = Path(root)
    config = load_config(root)
    now = dt.datetime.now(dt.timezone.utc) if now is None else now.astimezone(dt.timezone.utc)
    lookup = [confirmed]
    raw = root / RAW_RELATIVE_ROOT
    snapshots = sorted(p for p in raw.iterdir() if p.is_dir()) if raw.is_dir() else []
    wanted = None if dates is None else set(dates)
    committed = committed_rotation_dates(root)
    report = {"rule_id": RULE_ID, "recalculated_at_utc": _utc_text(now), "days": []}
    refused = []
    for snapshot in snapshots:
        try:
            as_of = (dt.date.fromisoformat(snapshot.name) - dt.timedelta(days=1)).isoformat()
        except ValueError:
            continue
        if wanted is not None and as_of not in wanted:
            continue
        target = point_path(root, config, as_of)
        if target.exists():
            refused.append(as_of)
            report["days"].append({"as_of_date": as_of, "status": "REFUSED_ALREADY_RECALCULATED",
                                   "path": target.relative_to(root).as_posix()})
            continue
        if lookup[0] is None:  # resolved only when a day is not refused; refuses shallow clones
            lookup[0] = git_confirmations(root)
        result = derive_point_body(root, config, snapshot, confirmed=lookup[0], now=now)
        if result["status"] != "RECALCULATED":
            report["days"].append({k: v for k, v in result.items() if k != "body"})
            continue
        body = result["body"]
        confirmations = []
        for item in body["resolving_records"]:
            info = result["confirmations"][item["canonical_asset_id"]]
            if info is None or _parse_utc(info["committed_at_utc"]) > now:
                _fail("CLASSIFICATION_NOT_CONFIRMED_BEFORE_RECALCULATION", f"{as_of}:{item['canonical_asset_id']}")
            if info.get("history_shallow") is not False:
                _fail("REFUSED_SHALLOW_HISTORY", f"{as_of}:{item['canonical_asset_id']}")
            confirmations.append({"canonical_asset_id": item["canonical_asset_id"]} | info)
        next_date = (dt.date.fromisoformat(committed[-1]) + dt.timedelta(days=1)).isoformat() if committed else None
        point = body | {
            "body_sha256": payload_sha256(body),
            "classification_confirmations": confirmations,
            "classification_source": {
                "path": CONFIG_PATHS["exclusion_taxonomy"],
                "sha256": file_sha256(root / CONFIG_PATHS["exclusion_taxonomy"]),
            },
            "recalculation": {
                "recalculated_at_utc": _utc_text(now),
                "rotation_packets_committed_through": committed[-1] if committed else None,
                "applies_to_rotation_as_of_from": next_date,
            },
        }
        point["payload_sha256"] = payload_sha256(point)
        if write:
            if target.exists():  # re-check right before the write (write-once)
                _fail("REFUSED_ALREADY_RECALCULATED", as_of)
            target.parent.mkdir(parents=True, exist_ok=True)
            with open(target, "xb") as handle:
                handle.write(render_json(point))
        report["days"].append({
            "as_of_date": as_of, "status": "RECALCULATED" if write else "WOULD_RECALCULATE",
            "resolving_assets": [r["canonical_asset_id"] for r in body["resolving_records"]],
            "backdated_assets": [r["canonical_asset_id"] for r in body["resolving_records"] if r["kind"] == "BACKDATED_COMMITTED_AFTER_SNAPSHOT"],
            "path": target.relative_to(root).as_posix(),
        })
    report["refused_already_recalculated"] = refused
    return report


def load_point(path: Path) -> dict:
    point = _read_json(path)
    if not isinstance(point, dict) or point.get("schema_version") != POINT_SCHEMA_VERSION or point.get("rule_id") != RULE_ID:
        _fail("RECALC_POINT_SCHEMA_INVALID", str(path))
    body = copy.deepcopy(point)
    digest = body.pop("payload_sha256", None)
    if digest != payload_sha256(body):
        _fail("RECALC_POINT_SHA_MISMATCH", str(path))
    if point.get("recalculated") is not True or point.get("mark_ko") != MARK_KO:
        _fail("RECALC_POINT_MARK_MISSING", str(path))
    return point


def committed_points(root: Path, config: dict) -> dict:
    base = Path(root) / config["evidence_root"]
    points = {}
    if not base.is_dir():
        return points
    for path in sorted(base.glob("*/point.json")):
        point = load_point(path)
        if point["as_of_date"] != path.parent.name:
            _fail("RECALC_POINT_DATE_MISMATCH", str(path))
        if point["ratification_record"]["sha256"] != config["ratification_record"]["sha256"]:
            _fail("RECALC_POINT_RATIFICATION_SHA_MISMATCH", str(path))
        if point["as_of_date"] < config["eligible_observation_from"]:
            _fail("RECALC_POINT_BEFORE_ELIGIBLE_FROM", str(path))
        points[point["as_of_date"]] = {"path": path, "point": point}
    return points


def verify(root: Path = REPO_ROOT) -> list:
    """Re-derive every committed point from its recorded records; empty list = all reproduce."""
    root = Path(root)
    config = load_config(root)
    problems = []
    for as_of, item in committed_points(root, config).items():
        point = item["point"]
        snapshot = root / point["snapshot"]["path"]
        if not snapshot.is_dir():
            problems.append(f"SNAPSHOT_MISSING:{as_of}")
            continue
        try:
            result = derive_point_body(root, config, snapshot, recorded=point)
        except CoverageRecalcError as exc:
            problems.append(f"NOT_REPRODUCED:{as_of}:{exc}")
            continue
        if result["status"] != "RECALCULATED":
            problems.append(f"NOT_REPRODUCED:{as_of}:{result['status']}")
            continue
        body = {k: point.get(k) for k in result["body"]}
        if payload_sha256(result["body"]) != point["body_sha256"] or body != result["body"]:
            problems.append(f"BODY_MISMATCH:{as_of}")
        recalculated_at = _parse_utc(point["recalculation"]["recalculated_at_utc"])
        available_at = _parse_utc(point["snapshot"]["available_at"])
        confirmed_assets = {c["canonical_asset_id"]: c for c in point["classification_confirmations"]}
        for record in point["resolving_records"]:
            info = confirmed_assets.get(record["canonical_asset_id"])
            if info is None or _parse_utc(info["committed_at_utc"]) > recalculated_at or info.get("history_shallow") is not False:
                problems.append(f"CONFIRMATION_INVALID:{as_of}:{record['canonical_asset_id']}")
            elif record["kind"] == "BACKDATED_COMMITTED_AFTER_SNAPSHOT" and _parse_utc(info["committed_at_utc"]) <= available_at:
                problems.append(f"BACKDATED_KIND_INVALID:{as_of}:{record['canonical_asset_id']}")
    return problems


def _visited_classifications(point: dict) -> list:
    """(asset, category) for every asset the CR-06 scan visited in one day's snapshot."""
    universe = point["universe"]
    rows = {item["canonical_asset_id"]: item["taxonomy_category"] for item in universe["members"]}
    for item in universe["missing_observation_members"]:
        rows.setdefault(item["canonical_asset_id"], "eligible_crypto")
    for item in universe["taxonomy_excluded_before_cutoff"]:
        rows[item["canonical_asset_id"]] = item["category"]
    for item in universe["taxonomy_unknown_before_cutoff"]:
        rows[item["canonical_asset_id"]] = "UNKNOWN"
    return sorted([asset, category] for asset, category in rows.items())


def _compatible(recorded: dict, current: list, day: str) -> bool:
    """Same classification still in force: reason edits and an effective_to that still covers are compatible."""
    threshold = max(day, recorded["effective_from"])
    return any(
        c["canonical_asset_id"] == recorded["canonical_asset_id"]
        and c["category"] == recorded["category"]
        and c["effective_from"] == recorded["effective_from"]
        and (c["effective_to"] is None or c["effective_to"] >= threshold)
        for c in current
    )


def point_classification_drift(point: dict, current_records: list) -> list:
    visited = {asset for asset, _ in _visited_classifications(point["recalculated_source_point"])}
    drift = []
    for record in point["point_in_time_records"] + point["resolving_records"]:
        if record["canonical_asset_id"] in visited and not _compatible(record, current_records, point["as_of_date"]):
            drift.append(record["canonical_asset_id"])
    return sorted(set(drift))


def classification_drift(root: Path = REPO_ROOT) -> list:
    """Notice rows (never failures): visited-asset classifications of committed points changed later."""
    root = Path(root)
    config = load_config(root)
    current = _read_json(Path(root) / CONFIG_PATHS["exclusion_taxonomy"])["records"]
    rows = []
    for as_of, item in committed_points(root, config).items():
        assets = point_classification_drift(item["point"], current)
        if assets:
            rows.append({"as_of_date": as_of, "code": "RECORDED_CLASSIFICATION_CHANGED_LATER", "assets": assets})
    return rows


# ---------------------------------------------------------------------------
# Rotation read path (crypto primary_30d strength input only)
# ---------------------------------------------------------------------------

NOTICE_SCHEMA_VERSION = "crypto_rotation_30d_coverage_recalc_notice/1"
NOTICE_RELATIVE_PATH = "data/rotation_confirmation_crypto_coverage_recalc_notice.json"
UNAVAILABLE_REASON = "WINDOW_COVERAGE_RECALC_UNAVAILABLE"
_NOTICES: dict = {}
_HISTORIES: dict = {}
_TRANSFORM_CACHE: dict = {}
_LEADERSHIP_MODULE: list = []
_HISTORY_TMP: list = []


def reset_notices(root: Path) -> None:
    _NOTICES[Path(root).as_posix()] = {}


def _notice(root: Path, as_of: str, code: str, detail=None) -> None:
    rows = _NOTICES.setdefault(Path(root).as_posix(), {}).setdefault(as_of, [])
    row = {"code": code, "detail": detail}
    if row not in rows:
        rows.append(row)


def notice_document(root: Path) -> dict:
    """Deterministic crypto-only notice (no wall clock): drift is reported here, never raised."""
    rows = _NOTICES.get(Path(root).as_posix(), {})
    document = {
        "schema_version": NOTICE_SCHEMA_VERSION,
        "market": "CRYPTO",
        "rule_id": RULE_ID,
        "handling": "COMMITTED_PACKETS_PREFERRED_DRIFT_REPORTED_NOT_RAISED",
        "notices": [dict(row, as_of_date=as_of) for as_of in sorted(rows) for row in rows[as_of]],
    }
    try:
        document["point_classification_drift"] = classification_drift(root) if load_config(root, required=False) else []
    except Exception as exc:  # noqa: BLE001 - notices never raise
        document["point_classification_drift"] = [{"code": "DRIFT_CHECK_FAILED", "detail": str(exc)}]
    document["payload_sha256"] = payload_sha256(document)
    return document


class _ClassificationHistory:
    """The classification file exactly as committed (first-parent) at a given time."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.error = None
        self.commits = []
        self.paths = {}
        relative = CONFIG_PATHS["exclusion_taxonomy"]
        try:
            run = lambda *args: subprocess.run(["git", "-C", str(self.root), *args], capture_output=True, text=True, check=True).stdout
            if run("rev-parse", "--is-shallow-repository").strip() == "true":
                self.error = "CLASSIFICATION_HISTORY_SHALLOW"
                return
            for line in run("log", "--first-parent", "--format=%H %cI", "HEAD", "--", relative).splitlines():
                if line:
                    sha, committed = line.split(" ", 1)
                    self.commits.append((dt.datetime.fromisoformat(committed).astimezone(dt.timezone.utc), sha))
        except (OSError, subprocess.CalledProcessError):
            self.error = "CLASSIFICATION_HISTORY_UNAVAILABLE"
            return
        self.commits.sort()
        if not self.commits:
            self.error = "CLASSIFICATION_HISTORY_UNAVAILABLE"

    def path_at(self, when: dt.datetime) -> Path:
        if self.error:
            _fail(self.error)
        eligible = [sha for committed, sha in self.commits if committed <= when]
        if not eligible:
            _fail("CLASSIFICATION_NOT_COMMITTED_BEFORE_SNAPSHOT", _utc_text(when))
        sha = eligible[-1]
        if sha not in self.paths:
            if not _HISTORY_TMP:
                _HISTORY_TMP.append(tempfile.TemporaryDirectory(prefix="crypto_recalc_history_"))
            data = subprocess.run(["git", "-C", str(self.root), "show", f"{sha}:{CONFIG_PATHS['exclusion_taxonomy']}"],
                                  capture_output=True, check=True).stdout
            path = Path(_HISTORY_TMP[0].name) / f"{sha}.json"
            path.write_bytes(data)
            self.paths[sha] = path
        return self.paths[sha]


def _history(root: Path) -> _ClassificationHistory:
    key = Path(root).resolve().as_posix()
    if key not in _HISTORIES:
        _HISTORIES[key] = _ClassificationHistory(root)
    return _HISTORIES[key]


class _BreadthProxy:
    """CR-06 helper for the window rebuild with a frozen classification view.

    Recalculated days: exactly the committed point. Other days: the classification
    file as committed at or before that day's snapshot ``available_at``.
    """

    def __init__(self, recalculated_by_snapshot: dict, history: _ClassificationHistory):
        self._recalculated = recalculated_by_snapshot
        self._history = history
        self.outputs = {}

    def __getattr__(self, name):
        return getattr(BREADTH, name)

    def build_transform(self, snapshot_dir, **kwargs):
        key = Path(snapshot_dir).as_posix()
        if key in self._recalculated:
            output = copy.deepcopy(self._recalculated[key])
        else:
            vintage = dt.date.fromisoformat(Path(snapshot_dir).name)
            frozen = self._history.path_at(_parse_utc(BREADTH.downloaded_at(Path(snapshot_dir), vintage)))
            kwargs = dict(kwargs, exclusion_taxonomy_path=frozen)
            cache_key = (Path(snapshot_dir).resolve().as_posix(), tuple(sorted((k, str(v)) for k, v in kwargs.items())))
            if cache_key not in _TRANSFORM_CACHE:
                _TRANSFORM_CACHE[cache_key] = BREADTH.build_transform(snapshot_dir, **kwargs)
            output = copy.deepcopy(_TRANSFORM_CACHE[cache_key])
        self.outputs[key] = output
        return output


def _leadership_module():
    """A private CR-07 module instance (its BREADTH helper is swapped per call; shared modules untouched)."""
    if not _LEADERSHIP_MODULE:
        _LEADERSHIP_MODULE.append(_load_module(
            "atlas_crypto_leadership_for_coverage_recalc", REPO_ROOT / ".github/scripts/crypto_leadership.py"
        ))
    return _LEADERSHIP_MODULE[0]


def _eligible_natural_window(config: dict, as_of: str, natural_window: dict) -> bool:
    descriptor = natural_window.get("window") or {}
    unknown_points = natural_window.get("source_unknown_points") or []
    return (
        natural_window.get("window_id") == config["applies_to"]["window_id"]
        and natural_window.get("status") == "UNKNOWN"
        and natural_window.get("unknown_reason") == "SOURCE_POINT_UNKNOWN"
        and [b.get("code") for b in natural_window.get("blockers") or []] == ["SOURCE_POINT_UNKNOWN"]
        and bool(unknown_points)
        and descriptor.get("end_date") == as_of
        and not descriptor.get("missing_dates")
        and str(descriptor.get("start_date")) >= config["eligible_observation_from"]
        and all(p.get("unknown_reason") == config["eligible_point_in_time_unknown_reason"] for p in unknown_points)
    )


def recalculated_primary_window(root: Path, as_of: str, natural_packet: dict, natural_window: dict,
                                config: Optional[dict] = None) -> Optional[dict]:
    """Live rebuild: ``{"window", "mark"}`` or ``None`` (not applicable). May raise CoverageRecalcError."""
    root = Path(root)
    config = load_config(root, required=False) if config is None else config
    if config is None or not _eligible_natural_window(config, as_of, natural_window):
        return None
    descriptor = natural_window["window"]
    points = committed_points(root, config)
    used = []
    for unknown in natural_window["source_unknown_points"]:
        item = points.get(unknown.get("as_of_date"))
        if item is None:
            return None
        point = item["point"]
        applies_from = point["recalculation"]["applies_to_rotation_as_of_from"]
        if point["snapshot"]["manifest_sha256"] != unknown.get("manifest_sha256"):
            _notice(root, as_of, "RECALCULATED_POINT_MANIFEST_MISMATCH", point["as_of_date"])
            return None
        if applies_from is not None and as_of < applies_from:
            return None
        used.append(item)
    paths = _paths(root)
    identity_sha = file_sha256(paths["identity_exceptions"])
    current_records = _read_json(paths["exclusion_taxonomy"])["records"]
    for item in used:
        if item["point"]["snapshot"]["identity_exceptions_sha256"] != identity_sha:
            _fail("IDENTITY_EXCEPTIONS_CHANGED_SINCE_RECALCULATION", item["point"]["as_of_date"])
        drift = point_classification_drift(item["point"], current_records)
        if drift:  # the committed point is still used as recorded (frozen); reported only
            _notice(root, as_of, "RECORDED_CLASSIFICATION_CHANGED_LATER", {"day": item["point"]["as_of_date"], "assets": drift})
    policies = natural_packet.get("policies") or {}
    if (
        (policies.get("universe") or {}).get("policy_sha256") != file_sha256(paths["universe"])
        or (policies.get("leadership") or {}).get("policy_sha256") != file_sha256(paths["leadership_policy"])
        or (policies.get("taxonomy") or {}).get("policy_sha256") != file_sha256(paths["sector_taxonomy"])
    ):
        _fail("NATURAL_PACKET_POLICY_SHA_MISMATCH", as_of)
    leadership_module = _leadership_module()
    snapshots = leadership_module.discover_snapshot_map(root / RAW_RELATIVE_ROOT)
    recalculated_by_snapshot = {
        (root / item["point"]["snapshot"]["path"]).as_posix(): item["point"]["recalculated_source_point"] for item in used
    }
    proxy = _BreadthProxy(recalculated_by_snapshot, _history(root))
    leadership_module.BREADTH = proxy
    contract = leadership_module.load_contract(paths["leadership_contract"])
    leadership = leadership_module.load_leadership_policy(paths["leadership_policy"])
    leadership_module.require_ratified_leadership_policy(leadership)
    taxonomy = leadership_module.load_taxonomy(paths["sector_taxonomy"])
    specs = [s for s in leadership["windows"] if s["window_id"] == config["applies_to"]["window_id"]]
    if len(specs) != 1:
        _fail("WINDOW_SPEC_MISSING")
    start = dt.date.fromisoformat(descriptor["start_date"])
    days = [start + dt.timedelta(days=i) for i in range(specs[0]["lookback_calendar_days"])]
    if days[-1].isoformat() != as_of or any(day not in snapshots for day in days):
        _fail("WINDOW_SNAPSHOTS_MISSING", as_of)
    for day in days:  # prices point in time: each day's own as-captured snapshot only
        if snapshots[day].name != (day + dt.timedelta(days=1)).isoformat():
            _fail("WINDOW_SNAPSHOT_NOT_POINT_IN_TIME", day.isoformat())
    try:
        window = leadership_module.build_observed_window(
            specs[0], copy.deepcopy(descriptor), [(day, snapshots[day]) for day in days], contract, leadership,
            taxonomy, paths["universe"], paths["exclusion_taxonomy"], paths["identity_exceptions"],
        )
    except (leadership_module.LeadershipError, BREADTH.BreadthError) as exc:
        _fail("WINDOW_REBUILD_FAILED", str(exc))
    if window.get("status") != "OBSERVED_UNCLASSIFIED":
        _notice(root, as_of, "WINDOW_REBUILD_NOT_OBSERVED", {
            "unknown_reason": window.get("unknown_reason"),
            "source_unknown_points": [p["as_of_date"] for p in window.get("source_unknown_points") or []],
        })
        return None
    visited = [
        {"as_of_date": day.isoformat(), "classifications": _visited_classifications(proxy.outputs[snapshots[day].as_posix()])}
        for day in days
    ]
    mark = {
        "rule_id": RULE_ID,
        "ratification_record_sha256": config["ratification_record"]["sha256"],
        "recalculated": True,
        "mark_ko": MARK_KO,
        "window_id": config["applies_to"]["window_id"],
        "natural_window_unknown_reason": natural_window["unknown_reason"],
        "recalculated_days": [
            {
                "as_of_date": item["point"]["as_of_date"],
                "point_path": f"{config['evidence_root']}/{item['point']['as_of_date']}/point.json",
                "point_payload_sha256": item["point"]["payload_sha256"],
                "classification_source_sha256": item["point"]["classification_source"]["sha256"],
                "recalculated_at_utc": item["point"]["recalculation"]["recalculated_at_utc"],
            }
            for item in sorted(used, key=lambda i: i["point"]["as_of_date"])
        ],
        "window_bucket_payload_sha256": payload_sha256(window["group_relative_strength"]["bucket"]),
        # Judgment-bearing input of the rotation packet: checked whenever a committed packet is reused.
        "entity_strengths_sha256": payload_sha256(window_entity_strengths(window)),
        # Frozen inputs: classifications of the assets each day's scan visited (recalculated
        # days from the committed point, other days from the file as committed before the
        # snapshot). Later reason / effective_to / unrelated edits do not change it.
        "rebuild_bindings": {
            "classification_view": "RECALCULATED_POINTS_AND_FILE_AS_COMMITTED_BEFORE_EACH_SNAPSHOT",
            "visited_classifications_sha256": payload_sha256(visited),
            "identity_exceptions_sha256": identity_sha,
        },
    }
    return {"window": window, "mark": mark}


def window_entity_strengths(window: dict) -> list:
    return sorted([row["group_id"], row["relative_strength_vs_btc"]] for row in window["group_relative_strength"]["bucket"])


def committed_entity_strengths(packet: dict) -> list:
    return sorted([e["entity_id"], e["strength"]] for scope in packet.get("scopes") or [] for e in scope["entities"])


def committed_packet_problems(root: Path, config: Optional[dict], packet: dict) -> list:
    """Why a committed marked packet must not be reused (empty list = consistent)."""
    mark = packet["observation"]["coverage_recalculation"]
    problems = []
    if mark.get("entity_strengths_sha256") != payload_sha256(committed_entity_strengths(packet)):
        problems.append("ENTITY_STRENGTHS_SHA_MISMATCH")
    if config is not None:
        try:
            points = committed_points(root, config)
        except Exception as exc:  # noqa: BLE001
            return problems + [f"POINTS_UNREADABLE:{exc}"]
        for day in mark.get("recalculated_days") or []:
            item = points.get(day.get("as_of_date"))
            if item is None or item["point"]["payload_sha256"] != day.get("point_payload_sha256"):
                problems.append(f"RECALCULATED_POINT_MISMATCH:{day.get('as_of_date')}")
    return problems


def rotation_override(root: Path, as_of: str, natural_packet: dict, natural_window: dict,
                      committed_packet: Optional[dict]) -> dict:
    """Decision for one crypto as-of date whose natural 30d window is not observed. Never raises.

    ``{"kind": "NATURAL"}`` natural UNKNOWN stands; ``{"kind": "UNKNOWN", "unknown_reason"}``
    explicit fail-closed status; ``{"kind": "COMMITTED", "packet"}`` reuse the committed
    marked packet's observation (replay never changes it); ``{"kind": "LIVE", "window", "mark"}``.
    """
    root = Path(root)
    try:
        config = load_config(root, required=False)
    except Exception as exc:  # noqa: BLE001
        _notice(root, as_of, "RECALC_CONFIG_INVALID", str(exc))
        config = None
    committed_mark = ((committed_packet or {}).get("observation") or {}).get("coverage_recalculation")
    if committed_packet is not None and committed_mark is None:
        reason = (committed_packet.get("observation") or {}).get("unknown_reason") or ""
        return {"kind": "UNKNOWN", "unknown_reason": reason} if reason == UNAVAILABLE_REASON else {"kind": "NATURAL"}
    problems = [] if committed_mark is None else committed_packet_problems(root, config, committed_packet)
    if problems:  # never reused silently; the rebuilt packet then fails the CRYPTO append-only write loudly
        _notice(root, as_of, "COMMITTED_RECALCULATED_PACKET_INCONSISTENT", problems)
    if config is None:
        if committed_mark is not None and not problems:
            return {"kind": "COMMITTED", "packet": committed_packet}
        return {"kind": "UNKNOWN", "unknown_reason": UNAVAILABLE_REASON} if committed_mark is not None else {"kind": "NATURAL"}
    live, error = None, None
    try:
        live = recalculated_primary_window(root, as_of, natural_packet, natural_window, config)
    except Exception as exc:  # noqa: BLE001 - crypto-only notice, never aborts other markets
        error = str(exc)
    if committed_mark is not None and not problems:
        if error is not None:
            _notice(root, as_of, "COMMITTED_RECALCULATED_PACKET_DRIFT_CHECK_UNAVAILABLE", error)
        elif live is None:
            _notice(root, as_of, "COMMITTED_RECALCULATED_PACKET_DRIFT", "LIVE_REBUILD_NOT_APPLICABLE")
        else:
            live_strengths = window_entity_strengths(live["window"])
            committed_strengths = committed_entity_strengths(committed_packet)
            if live_strengths != committed_strengths:
                _notice(root, as_of, "COMMITTED_RECALCULATED_PACKET_STRENGTH_DRIFT",
                        {"committed": committed_strengths, "live": live_strengths})
            elif live["mark"] != committed_mark:
                _notice(root, as_of, "COMMITTED_RECALCULATED_PACKET_DRIFT", "MARK_CHANGED")
        return {"kind": "COMMITTED", "packet": committed_packet}
    if error is not None:
        _notice(root, as_of, UNAVAILABLE_REASON, error)
        return {"kind": "UNKNOWN", "unknown_reason": UNAVAILABLE_REASON}
    if live is None:
        return {"kind": "NATURAL"}
    return {"kind": "LIVE", "window": live["window"], "mark": live["mark"]}


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    recalc = sub.add_parser("recalc", help="write-once recalculated points for eligible days")
    recalc.add_argument("--root", type=Path, default=REPO_ROOT)
    recalc.add_argument("--as-of", action="append", help="limit to these observation dates")
    recalc.add_argument("--write", action="store_true")
    check = sub.add_parser("verify", help="re-derive every committed recalculated point")
    check.add_argument("--root", type=Path, default=REPO_ROOT)
    args = parser.parse_args(argv)
    try:
        if args.command == "recalc":
            report = recalculate(args.root, write=args.write, dates=args.as_of)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            if args.as_of and report["refused_already_recalculated"]:
                print("REFUSED_ALREADY_RECALCULATED:" + ",".join(report["refused_already_recalculated"]), file=sys.stderr)
                return 4
            return 0
        problems = verify(args.root)
        for problem in problems:
            print(problem)
        for row in classification_drift(args.root):
            print(f"NOTICE:{json.dumps(row, ensure_ascii=False, sort_keys=True)}")
        return 1 if problems else 0
    except (CoverageRecalcError, BREADTH.BreadthError) as exc:
        print(f"Crypto 30d coverage recalculation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
