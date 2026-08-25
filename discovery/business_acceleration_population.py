#!/usr/bin/env python3
"""P3-05 TSM SEC monthly-revenue population from retained official bytes.

This provider-free adapter scans only P4-02 manifests and gzip payloads already
retained in the repository.  It reuses the canonical SEC manifest validator,
the existing TSM monthly-report parser, and the existing Business Acceleration
engine.  No source ranking, importance threshold, candidate promotion, Rule,
action, order, Production, or trading authority is introduced.

The first operational slice is deliberately narrow: TSM monthly and cumulative
published YoY revenue growth from three consecutive SEC-filed monthly reports.
If three decision-time-eligible consecutive reports are unavailable, the
population is explicitly NOT_COMPUTABLE rather than filled with fixtures.
"""
from __future__ import annotations

import argparse
import calendar
import copy
import datetime as dt
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "collectors"))
sys.path.insert(0, str(ROOT / "discovery"))

import business_acceleration as RADAR  # noqa: E402
import sec_filing_content as SEC  # noqa: E402
import tsmc_sec_monthly_probe as TSM  # noqa: E402


SCHEMA_VERSION = "business_acceleration_population/1"
STATUS_POPULATED = "RADAR_POPULATED"
STATUS_INSUFFICIENT = "NOT_COMPUTABLE_INSUFFICIENT_CONSECUTIVE_EVIDENCE"
KST = ZoneInfo("Asia/Seoul")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class BusinessAccelerationPopulationError(ValueError):
    """Fail-closed retained-evidence or append-only publication violation."""


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _parse_utc(value: str, error_code: str = "DECISION_AT_INVALID") -> dt.datetime:
    if not isinstance(value, str) or UTC_RE.fullmatch(value) is None:
        raise BusinessAccelerationPopulationError(error_code)
    try:
        parsed = dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise BusinessAccelerationPopulationError(error_code) from exc
    if parsed.strftime("%Y-%m-%dT%H:%M:%SZ") != value:
        raise BusinessAccelerationPopulationError(error_code)
    return parsed


def _month_end(month: str) -> str:
    if not isinstance(month, str) or MONTH_RE.fullmatch(month) is None:
        raise BusinessAccelerationPopulationError("TARGET_MONTH_INVALID")
    year, number = (int(part) for part in month.split("-"))
    if not 1 <= number <= 12:
        raise BusinessAccelerationPopulationError("TARGET_MONTH_INVALID")
    return f"{year:04d}-{number:02d}-{calendar.monthrange(year, number)[1]:02d}"


def _consecutive(left: str, right: str) -> bool:
    year, month = (int(part) for part in left.split("-"))
    next_year = year + (1 if month == 12 else 0)
    next_month = 1 if month == 12 else month + 1
    return right == f"{next_year:04d}-{next_month:02d}"


def _read_manifest(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BusinessAccelerationPopulationError(
            f"MANIFEST_READ_FAILED:{path}:{exc}"
        ) from exc
    if not isinstance(value, dict):
        raise BusinessAccelerationPopulationError(f"MANIFEST_NOT_OBJECT:{path}")
    return value


def _validated_report(
    manifest_path: Path, decision_at: dt.datetime, repo_root: Path
) -> dict | None:
    manifest = _read_manifest(manifest_path)
    try:
        manifest = SEC.validate_manifest(manifest)
    except SEC.SecContentError as exc:
        raise BusinessAccelerationPopulationError(
            f"SEC_MANIFEST_INVALID:{manifest_path}:{exc}"
        ) from exc
    if manifest.get("ticker") != "TSM" or manifest.get("form") != "6-K":
        return None
    retrieved_at = _parse_utc(
        manifest.get("retrieved_at_utc"), "RETRIEVED_AT_INVALID"
    )
    if retrieved_at > decision_at:
        return None

    raw_by_name = {}
    for document in manifest["documents"]:
        name = document["document_name"]
        if SEC.SAFE_DOCUMENT_RE.fullmatch(name) is None:
            raise BusinessAccelerationPopulationError("DOCUMENT_NAME_UNSAFE")
        try:
            with gzip.open(manifest_path.parent / f"{name}.gz", "rb") as handle:
                raw_by_name[name] = handle.read()
        except (OSError, EOFError) as exc:
            raise BusinessAccelerationPopulationError(
                f"RETAINED_CONTENT_READ_FAILED:{manifest_path}:{exc}"
            ) from exc
    try:
        manifest = SEC.validate_manifest(manifest, raw_by_name=raw_by_name)
    except SEC.SecContentError as exc:
        raise BusinessAccelerationPopulationError(
            f"SEC_RETAINED_CONTENT_INVALID:{manifest_path}:{exc}"
        ) from exc
    primary = [row for row in manifest["documents"] if row["kind"] == "primary"]
    if len(primary) != 1:
        raise BusinessAccelerationPopulationError("PRIMARY_CARDINALITY_INVALID")
    try:
        report = TSM.parse_retained_monthly_report(
            manifest, raw_by_name[primary[0]["document_name"]]
        )
    except TSM.SecProbeError as exc:
        if str(exc) == "MONTHLY_REPORT_IDENTITY_MISSING":
            return None
        raise BusinessAccelerationPopulationError(
            f"TSM_MONTHLY_REPORT_INVALID:{manifest_path}:{exc}"
        ) from exc
    if dt.date.fromisoformat(report["published_at"]) > decision_at.date():
        return None
    return {
        "target_month": report["target_month"],
        "published_at": report["published_at"],
        "retrieved_at_utc": manifest["retrieved_at_utc"],
        "accession": report["accession"],
        "source_url": report["source_url"],
        "source_sha256": report["source_sha256"],
        "source_bytes": report["source_bytes"],
        "manifest_path": manifest_path.relative_to(repo_root).as_posix()
        if manifest_path.is_relative_to(repo_root)
        else manifest_path.as_posix(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "table_locator": copy.deepcopy(report["table_locator"]),
        "observation": copy.deepcopy(report["observation"]),
    }


def _reports_at_decision(
    data_root: Path, decision_at: dt.datetime, repo_root: Path
) -> list[dict]:
    base = Path(data_root) / "sec_content" / "TSM"
    if not base.is_dir():
        return []
    by_month = {}
    for manifest_path in sorted(base.glob("*/_manifest.json")):
        report = _validated_report(manifest_path, decision_at, repo_root)
        if report is None:
            continue
        month = report["target_month"]
        if month in by_month:
            raise BusinessAccelerationPopulationError(
                f"DUPLICATE_TARGET_MONTH_AUTHORITY_UNRESOLVED:{month}"
            )
        by_month[month] = report
    return [by_month[key] for key in sorted(by_month)]


def _latest_consecutive_three(reports: list[dict]) -> list[dict] | None:
    candidates = []
    for index in range(len(reports) - 2):
        window = reports[index:index + 3]
        months = [row["target_month"] for row in window]
        if _consecutive(months[0], months[1]) and _consecutive(months[1], months[2]):
            candidates.append(window)
    return candidates[-1] if candidates else None


def _evidence_point(report: dict, *, cumulative: bool) -> dict:
    key = "cumulative_yoy_pct_published" if cumulative else "monthly_yoy_pct_published"
    measurement = (
        "TSMC consolidated cumulative net revenue YoY"
        if cumulative
        else "TSMC consolidated monthly net revenue YoY"
    )
    value = report["observation"][key]
    return {
        "schema_version": RADAR.EVIDENCE_SCHEMA_VERSION,
        "subject": "TSM",
        "measurement_identity": measurement,
        "economic_period_end": _month_end(report["target_month"]),
        "status": "EVIDENCE_AVAILABLE",
        "reasons": [],
        "consumable": True,
        "blocked_by": [],
        "acquisition_provenance_present": True,
        "source_identity": {
            "source_id": "sec_edgar",
            "source_url": report["source_url"],
            "source_sha256": report["source_sha256"],
            "available_at": report["published_at"],
            "retrieved_at_utc": report["retrieved_at_utc"],
        },
        "audit_provenance": {
            "capture_kind": "LIVE_OFFICIAL_CAPTURE",
            "accession": report["accession"],
            "manifest_path": report["manifest_path"],
            "manifest_sha256": report["manifest_sha256"],
            "table_locator": copy.deepcopy(report["table_locator"]),
        },
        "observation": {
            "raw_value": f"{value}%",
            "numeric_value": value,
            "unit": "pct",
            "observed_by": "tsmc_sec_monthly_probe/1",
        },
    }


def _series(window: list[dict], *, cumulative: bool) -> dict:
    return {
        "series_id": (
            "TSM_CUMULATIVE_REVENUE_YOY_SEC"
            if cumulative
            else "TSM_MONTHLY_REVENUE_YOY_SEC"
        ),
        "asset_id": "US:XNYS:TSM",
        "subject": "TSM",
        "metric_type": "REVENUE_GROWTH",
        "measurement_identity": (
            "TSMC consolidated cumulative net revenue YoY"
            if cumulative
            else "TSMC consolidated monthly net revenue YoY"
        ),
        "frequency": "MONTHLY",
        "comparison_basis": (
            "SEC-filed cumulative year-to-date revenue YoY percent, unchanged basis"
            if cumulative
            else "SEC-filed monthly revenue YoY percent, unchanged basis"
        ),
        "evidence_points": [
            _evidence_point(report, cumulative=cumulative) for report in window
        ],
    }


def build_population(
    *, decision_at: str, repo_root: Path = ROOT, data_root: Path | None = None
) -> dict:
    decision = _parse_utc(decision_at)
    repo_root = Path(repo_root)
    data_root = Path(data_root) if data_root is not None else repo_root / "data"
    reports = _reports_at_decision(data_root, decision, repo_root)
    window = _latest_consecutive_three(reports)
    radar_packet = None
    status = STATUS_INSUFFICIENT
    selected_months = []
    if window is not None:
        selected_months = [row["target_month"] for row in window]
        payload = {
            "schema_version": RADAR.INPUT_SCHEMA_VERSION,
            "as_of_utc": decision_at,
            "series": [_series(window, cumulative=False), _series(window, cumulative=True)],
        }
        try:
            radar_packet = RADAR.build_packet(payload)
        except RADAR.BusinessAccelerationError as exc:
            raise BusinessAccelerationPopulationError(f"RADAR_BUILD_FAILED:{exc}") from exc
        status = STATUS_POPULATED

    summary = {
        "eligible_report_count": len(reports),
        "selected_report_count": len(window or []),
        "series_count": radar_packet["series_count"] if radar_packet else 0,
        "case_count": radar_packet["case_count"] if radar_packet else 0,
    }
    packet = {
        "schema_version": SCHEMA_VERSION,
        "decision_at": decision_at,
        "status": status,
        "scope": "TSM_SEC_MONTHLY_REVENUE_ONLY",
        "source_reports": reports,
        "selected_months": selected_months,
        "summary": summary,
        "radar_packet": radar_packet,
        "authority": {
            "radar_case_recording_only": True,
            "source_ranking_authorized": False,
            "importance_ranking_authorized": False,
            "candidate_ranking_authorized": False,
            "stage_promotion_authorized": False,
            "rule_evaluation_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }
    packet["population_sha256"] = payload_sha256(packet)
    return packet


def validate_population(
    packet: dict, *, repo_root: Path = ROOT, data_root: Path | None = None
) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        raise BusinessAccelerationPopulationError("POPULATION_SCHEMA_INVALID")
    digest = packet.get("population_sha256")
    unsigned = copy.deepcopy(packet)
    unsigned.pop("population_sha256", None)
    if not isinstance(digest, str) or payload_sha256(unsigned) != digest:
        raise BusinessAccelerationPopulationError("POPULATION_SHA256_MISMATCH")
    rebuilt = build_population(
        decision_at=packet.get("decision_at"), repo_root=repo_root, data_root=data_root
    )
    if rebuilt != packet:
        raise BusinessAccelerationPopulationError("POPULATION_REBUILD_MISMATCH")
    return copy.deepcopy(packet)


def _packet_bytes(packet: dict) -> bytes:
    return (json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def publish_append_only(
    *, out_root: Path, packet: dict, repo_root: Path = ROOT, data_root: Path | None = None
) -> tuple[Path, bool]:
    checked = validate_population(packet, repo_root=repo_root, data_root=data_root)
    decision_date = _parse_utc(checked["decision_at"]).astimezone(KST).date().isoformat()
    raw = _packet_bytes(checked)
    path = Path(out_root) / decision_date / f"packet-{checked['population_sha256'][:16]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        if path.read_bytes() != raw:
            raise BusinessAccelerationPopulationError(
                f"CONTENT_ADDRESSED_PACKET_DRIFT:{path}"
            )
        return path, False
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise
    return path, True


def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=ROOT)
    parser.add_argument("--data-root", type=Path)
    parser.add_argument("--decision-at", required=True)
    parser.add_argument("--out-root", type=Path, required=True)
    args = parser.parse_args(argv)
    packet = build_population(
        decision_at=args.decision_at,
        repo_root=args.repo_root,
        data_root=args.data_root,
    )
    path, created = publish_append_only(
        out_root=args.out_root,
        packet=packet,
        repo_root=args.repo_root,
        data_root=args.data_root,
    )
    print(json.dumps({
        "status": packet["status"],
        "publication": "published" if created else "verified_existing",
        "path": path.as_posix(),
        "selected_months": packet["selected_months"],
        "series_count": packet["summary"]["series_count"],
        "case_count": packet["summary"]["case_count"],
        "population_sha256": packet["population_sha256"],
        "authority": packet["authority"],
    }, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(run())
    except BusinessAccelerationPopulationError as exc:
        print(f"ERROR:{exc}", file=sys.stderr)
        raise SystemExit(1)
