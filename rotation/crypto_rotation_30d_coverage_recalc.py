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

    The first first-parent commit whose file contains the exact record
    (asset, category, effective_from, effective_to) is its confirmation.
    """
    root = Path(root)

    def git(*args):
        return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout

    try:
        shallow = git("rev-parse", "--is-shallow-repository").strip() == "true"
        lines = [line for line in git("log", "--first-parent", "--reverse", "--format=%H %cI", "HEAD", "--", relative_path).splitlines() if line]
    except (OSError, subprocess.CalledProcessError):
        return lambda record: None
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
            key = (record.get("canonical_asset_id"), record.get("category"), record.get("effective_from"), record.get("effective_to"))
            first.setdefault(key, {"commit": sha, "committed_at_utc": _utc_text(dt.datetime.fromisoformat(committed)), "history_shallow": shallow})

    def lookup(record):
        return first.get((record["canonical_asset_id"], record["category"], record["effective_from"], record["effective_to"]))

    return lookup


# ---------------------------------------------------------------------------
# Point recalculation
# ---------------------------------------------------------------------------

def _public_record(record: dict) -> dict:
    return {key: record[key] for key in ("canonical_asset_id", "category", "effective_from", "effective_to", "reason")}


def _overlay_taxonomy(taxonomy_raw: dict, day: dt.date, synthetic: list) -> dict:
    """PIT records effective on ``day`` plus one-day synthetic records for later-confirmed classifications."""
    records = []
    for record in taxonomy_raw["records"]:
        start = dt.date.fromisoformat(record["effective_from"])
        end = dt.date.fromisoformat(record["effective_to"]) if record["effective_to"] else dt.date.max
        if start <= day <= end:
            records.append(copy.deepcopy(record))
    for item in synthetic:
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
    overlay = {key: copy.deepcopy(value) for key, value in taxonomy_raw.items() if key != "records"}
    overlay["records"] = sorted(records, key=lambda r: (r["canonical_asset_id"], r["effective_from"]))
    return overlay


def _transform(snapshot: Path, paths: dict, taxonomy_path: Path) -> dict:
    return BREADTH.build_transform(
        snapshot,
        universe_policy_path=paths["universe"],
        exclusion_taxonomy_path=taxonomy_path,
        identity_exceptions_path=paths["identity_exceptions"],
    )


def _transform_with_overlay(snapshot: Path, paths: dict, overlay: dict) -> tuple:
    data = render_json(overlay)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "crypto_breadth_exclusion_taxonomy.recalc_overlay.json"
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


def derive_point_body(root: Path, config: dict, snapshot: Path, resolving: Optional[list] = None,
                      confirmed: Optional[Callable] = None, now: Optional[dt.datetime] = None) -> dict:
    """Deterministic recalculation body for one snapshot (no wall-clock, no git data).

    ``resolving`` given -> re-derive with exactly those later records (verify).
    Otherwise discover them from confirmed later records (``confirmed(record)``
    returns confirmation info or None, and must be at or before ``now``).
    Returns ``{"status": ..., ...}``; ``status == "RECALCULATED"`` carries the body.
    """
    root = Path(root)
    paths = _paths(root)
    vintage = dt.date.fromisoformat(Path(snapshot).name)
    day = vintage - dt.timedelta(days=1)
    as_of = day.isoformat()
    if as_of < config["eligible_observation_from"]:
        return {"status": "IGNORED_BEFORE_ELIGIBLE_FROM", "as_of_date": as_of}
    pit = _transform(snapshot, paths, paths["exclusion_taxonomy"])
    if pit["status"] != "UNKNOWN" or pit["unknown_reason"] != config["eligible_point_in_time_unknown_reason"]:
        return {"status": "NOT_ELIGIBLE_POINT_IN_TIME", "as_of_date": as_of,
                "point_in_time_status": pit["status"], "point_in_time_unknown_reason": pit["unknown_reason"]}
    taxonomy_raw = _read_json(paths["exclusion_taxonomy"])
    taxonomy = BREADTH.load_exclusion_taxonomy(paths["exclusion_taxonomy"])
    if taxonomy["approval_status"] != "RATIFIED":
        _fail("CLASSIFICATION_SOURCE_UNRATIFIED")
    if resolving is None:
        candidates = []
        for asset_id, records in sorted(taxonomy["_records_by_asset"].items()):
            if BREADTH.taxonomy_category(asset_id, day, taxonomy) is not None:
                continue
            for record in records:
                if record["_start"] <= day:
                    continue
                info = confirmed(_public_record(record)) if confirmed else None
                if info is None or (now is not None and _parse_utc(info["committed_at_utc"]) > now):
                    continue
                candidates.append(_public_record(record) | {"confirmation": info})
                break
        first, _ = _transform_with_overlay(snapshot, paths, _overlay_taxonomy(taxonomy_raw, day, candidates))
        if first["status"] != "OBSERVED_UNCLASSIFIED":
            return {
                "status": "STILL_UNKNOWN_AFTER_CONFIRMED_CLASSIFICATIONS", "as_of_date": as_of,
                "unknown_reason": first["unknown_reason"],
                "unclassified_assets_before_cutoff": [
                    {"canonical_asset_id": u["canonical_asset_id"], "rank_before_taxonomy": u["rank_before_taxonomy"]}
                    for u in first["universe"]["taxonomy_unknown_before_cutoff"]
                ],
            }
        visited = _visited_assets(first)
        resolving = [item for item in candidates if item["canonical_asset_id"] in visited]
    else:
        current = {(r["canonical_asset_id"], r["category"], r["effective_from"], r["effective_to"]) for r in taxonomy_raw["records"]}
        for item in resolving:
            if (item["canonical_asset_id"], item["category"], item["effective_from"], item["effective_to"]) not in current:
                _fail("RESOLVING_RECORD_NOT_IN_CLASSIFICATION_SOURCE", item["canonical_asset_id"])
            if item["effective_from"] <= as_of:
                _fail("RESOLVING_RECORD_NOT_LATER_THAN_DAY", item["canonical_asset_id"])
    overlay = _overlay_taxonomy(taxonomy_raw, day, resolving)
    point, overlay_sha = _transform_with_overlay(snapshot, paths, overlay)
    if point["status"] != "OBSERVED_UNCLASSIFIED":
        return {"status": "STILL_UNKNOWN_AFTER_CONFIRMED_CLASSIFICATIONS", "as_of_date": as_of,
                "unknown_reason": point["unknown_reason"], "unclassified_assets_before_cutoff": []}
    manifest_sha = file_sha256(Path(snapshot) / "_manifest.json")
    if point["lineage"]["manifest_sha256"] != manifest_sha or pit["lineage"]["manifest_sha256"] != manifest_sha:
        _fail("RECALC_SNAPSHOT_MANIFEST_MISMATCH", as_of)
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
            "available_at": point["lineage"]["available_at"],
        },
        "prices_point_in_time": {
            "policy": config["prices"],
            "latest_finalized_day_max": latest_days[-1] if latest_days else None,
            "same_manifest_as_point_in_time_point": True,
        },
        "point_in_time": {
            "status": pit["status"],
            "unknown_reason": pit["unknown_reason"],
            "taxonomy_unknown_before_cutoff": [
                {"canonical_asset_id": u["canonical_asset_id"], "rank_before_taxonomy": u["rank_before_taxonomy"]}
                for u in pit["universe"]["taxonomy_unknown_before_cutoff"]
            ],
            "evaluated_with_classification_policy_sha256": pit["universe"]["taxonomy"]["policy_sha256"],
        },
        "resolving_records": [
            {key: item[key] for key in ("canonical_asset_id", "category", "effective_from", "effective_to", "reason")}
            for item in sorted(resolving, key=lambda r: r["canonical_asset_id"])
        ],
        "overlay_taxonomy_sha256": overlay_sha,
        "recalculated_source_point": point,
    }
    return {"status": "RECALCULATED", "as_of_date": as_of, "body": body,
            "confirmations": {item["canonical_asset_id"]: item.get("confirmation") for item in resolving}}


def committed_rotation_dates(root: Path) -> list:
    base = Path(root) / ROTATION_EVIDENCE_RELATIVE_ROOT / "CRYPTO"
    return sorted(p.parent.name for p in base.glob("*/packet.json")) if base.is_dir() else []


def recalculate(root: Path = REPO_ROOT, *, write: bool = False, dates: Optional[list] = None,
                now: Optional[dt.datetime] = None, confirmed: Optional[Callable] = None) -> dict:
    root = Path(root)
    config = load_config(root)
    now = dt.datetime.now(dt.timezone.utc) if now is None else now.astimezone(dt.timezone.utc)
    confirmed = git_confirmations(root) if confirmed is None else confirmed
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
        result = derive_point_body(root, config, snapshot, confirmed=confirmed, now=now)
        if result["status"] != "RECALCULATED":
            report["days"].append({k: v for k, v in result.items() if k != "body"})
            continue
        body = result["body"]
        confirmations = []
        for item in body["resolving_records"]:
            info = result["confirmations"][item["canonical_asset_id"]]
            if info is None or _parse_utc(info["committed_at_utc"]) > now:
                _fail("CLASSIFICATION_NOT_CONFIRMED_BEFORE_RECALCULATION", f"{as_of}:{item['canonical_asset_id']}")
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
    """Re-derive every committed point; an empty list means all points reproduce."""
    root = Path(root)
    config = load_config(root)
    problems = []
    for as_of, item in committed_points(root, config).items():
        point = item["point"]
        snapshot = root / point["snapshot"]["path"]
        if not snapshot.is_dir():
            problems.append(f"SNAPSHOT_MISSING:{as_of}")
            continue
        result = derive_point_body(root, config, snapshot, resolving=point["resolving_records"])
        if result["status"] != "RECALCULATED":
            problems.append(f"NOT_REPRODUCED:{as_of}:{result['status']}")
            continue
        body = {k: point[k] for k in result["body"]}
        if payload_sha256(result["body"]) != point["body_sha256"] or body != result["body"]:
            problems.append(f"BODY_MISMATCH:{as_of}")
        recalculated_at = _parse_utc(point["recalculation"]["recalculated_at_utc"])
        confirmed_assets = {c["canonical_asset_id"]: c for c in point["classification_confirmations"]}
        for record in point["resolving_records"]:
            info = confirmed_assets.get(record["canonical_asset_id"])
            if info is None or _parse_utc(info["committed_at_utc"]) > recalculated_at:
                problems.append(f"CONFIRMATION_AFTER_RECALCULATION:{as_of}:{record['canonical_asset_id']}")
    return problems


# ---------------------------------------------------------------------------
# Rotation read path (crypto primary_30d strength input only)
# ---------------------------------------------------------------------------

class _BreadthProxy:
    """CR-06 helper that serves committed recalculated points for their snapshots only."""

    def __init__(self, recalculated_by_snapshot: dict, cache: dict):
        self._recalculated = recalculated_by_snapshot
        self._cache = cache

    def __getattr__(self, name):
        return getattr(BREADTH, name)

    def build_transform(self, snapshot_dir, **kwargs):
        key = Path(snapshot_dir).as_posix()
        if key in self._recalculated:
            return copy.deepcopy(self._recalculated[key])
        cache_key = (key, tuple(sorted((k, str(v)) for k, v in kwargs.items())))
        if cache_key not in self._cache:
            try:
                self._cache[cache_key] = ("ok", BREADTH.build_transform(snapshot_dir, **kwargs))
            except BREADTH.BreadthError as exc:
                self._cache[cache_key] = ("error", exc)
        kind, value = self._cache[cache_key]
        if kind == "error":
            raise value
        return copy.deepcopy(value)


_TRANSFORM_CACHE: dict = {}
_LEADERSHIP_MODULE: list = []


def _leadership_module():
    """A private CR-07 module instance (its BREADTH helper is swapped per call; shared modules untouched)."""
    if not _LEADERSHIP_MODULE:
        _LEADERSHIP_MODULE.append(_load_module(
            "atlas_crypto_leadership_for_coverage_recalc", REPO_ROOT / ".github/scripts/crypto_leadership.py"
        ))
    return _LEADERSHIP_MODULE[0]


def recalculated_primary_window(root: Path, as_of: str, natural_packet: dict, natural_window: dict,
                                config: Optional[dict] = None) -> Optional[dict]:
    """Rebuilt ``primary_30d`` window + mark, or ``None`` (the natural UNKNOWN stands)."""
    root = Path(root)
    config = load_config(root, required=False) if config is None else config
    if config is None or natural_window.get("window_id") != config["applies_to"]["window_id"]:
        return None
    if (
        natural_window.get("status") != "UNKNOWN"
        or natural_window.get("unknown_reason") != "SOURCE_POINT_UNKNOWN"
        or [b.get("code") for b in natural_window.get("blockers") or []] != ["SOURCE_POINT_UNKNOWN"]
    ):
        return None
    descriptor = natural_window.get("window") or {}
    unknown_points = natural_window.get("source_unknown_points") or []
    if (
        not unknown_points
        or descriptor.get("end_date") != as_of
        or descriptor.get("missing_dates")
        or str(descriptor.get("start_date")) < config["eligible_observation_from"]
    ):
        return None
    if any(p.get("unknown_reason") != config["eligible_point_in_time_unknown_reason"] for p in unknown_points):
        return None
    points = committed_points(root, config)
    used = []
    for unknown in unknown_points:
        item = points.get(unknown.get("as_of_date"))
        if item is None:
            return None
        point = item["point"]
        applies_from = point["recalculation"]["applies_to_rotation_as_of_from"]
        if point["snapshot"]["manifest_sha256"] != unknown.get("manifest_sha256"):
            return None
        if applies_from is not None and as_of < applies_from:
            return None
        used.append(item)
    paths = _paths(root)
    policies = natural_packet.get("policies") or {}
    if (
        (policies.get("universe") or {}).get("policy_sha256") != file_sha256(paths["universe"])
        or (policies.get("leadership") or {}).get("policy_sha256") != file_sha256(paths["leadership_policy"])
        or (policies.get("taxonomy") or {}).get("policy_sha256") != file_sha256(paths["sector_taxonomy"])
    ):
        return None
    leadership_module = _leadership_module()
    raw = root / RAW_RELATIVE_ROOT
    snapshots = leadership_module.discover_snapshot_map(raw)
    recalculated_by_snapshot = {}
    for item in used:
        snapshot = (root / item["point"]["snapshot"]["path"]).as_posix()
        recalculated_by_snapshot[snapshot] = item["point"]["recalculated_source_point"]
    leadership_module.BREADTH = _BreadthProxy(recalculated_by_snapshot, _TRANSFORM_CACHE)
    contract = leadership_module.load_contract(paths["leadership_contract"])
    leadership = leadership_module.load_leadership_policy(paths["leadership_policy"])
    leadership_module.require_ratified_leadership_policy(leadership)
    taxonomy = leadership_module.load_taxonomy(paths["sector_taxonomy"])
    specs = [s for s in leadership["windows"] if s["window_id"] == config["applies_to"]["window_id"]]
    if len(specs) != 1:
        return None
    start = dt.date.fromisoformat(descriptor["start_date"])
    days = [start + dt.timedelta(days=i) for i in range(specs[0]["lookback_calendar_days"])]
    if days[-1].isoformat() != as_of or any(day not in snapshots for day in days):
        return None
    for day in days:  # prices point in time: each day's own as-captured snapshot only
        if snapshots[day].name != (day + dt.timedelta(days=1)).isoformat():
            return None
    try:
        window = leadership_module.build_observed_window(
            specs[0], copy.deepcopy(descriptor), [(day, snapshots[day]) for day in days], contract, leadership,
            taxonomy, paths["universe"], paths["exclusion_taxonomy"], paths["identity_exceptions"],
        )
    except (leadership_module.LeadershipError, BREADTH.BreadthError):
        return None
    if window.get("status") != "OBSERVED_UNCLASSIFIED":
        return None
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
    }
    return {"window": window, "mark": mark}


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
        return 1 if problems else 0
    except (CoverageRecalcError, BREADTH.BreadthError) as exc:
        print(f"Crypto 30d coverage recalculation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(run())
