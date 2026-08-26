#!/usr/bin/env python3
"""P1-KR-06 next-session KOSPI availability receipt.

The KRX source does not declare an official publication timestamp in the
response consumed here.  This module therefore never invents one.  It records
only the first exact KOSPI response Atlas successfully observes after the
session date and makes that observed instant the earliest time a later
decision may use the source.

No index level, return, raw response, risk score, Regime state, or trading
authority is persisted by this module.
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
import shutil
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = ROOT / "config" / "korea_risk_availability_policy.json"
LATEST_KRX_PATH = ROOT / "data" / "latest_krx.json"
DATA_ROOT = ROOT / "data"
KST = ZoneInfo("Asia/Seoul")
UTC = dt.timezone.utc
HEX_64 = re.compile(r"^[0-9a-f]{64}$")
RECEIPT_SCHEMA = "korea_risk_availability_receipt/1"


def _load_module(name: str, relative_path: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


KRX_PROBE = _load_module(
    "atlas_krx_probe_for_korea_risk_availability",
    "atlas_krx_r2_openapi_probe.py",
)


class AvailabilityError(RuntimeError):
    """Fail-closed P1-KR-06 availability contract violation."""


def fail(code: str, detail: str = "") -> None:
    raise AvailabilityError(f"{code}: {detail}" if detail else code)


def canonical_bytes(value: dict) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def payload_sha256(value: dict) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def read_json(path: Path, code: str) -> tuple[dict, bytes]:
    try:
        raw = Path(path).read_bytes()
        value = json.loads(raw)
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value, raw


def load_policy(path: Path = POLICY_PATH) -> dict:
    value, _ = read_json(path, "POLICY_INVALID")
    pinned = {
        "schema_version": 1,
        "policy_version": "korea_risk_availability/1",
        "approval_status": "RATIFIED",
        "effective_from_available_date": "2026-08-26",
        "market": "KOREA",
        "market_timezone": "Asia/Seoul",
        "source_name": "KRX_OPEN_API_KOSPI_INDEX",
        "source_endpoint_path": "/svc/apis/idx/kospi_dd_trd",
        "source_index_name": "코스피",
        "availability_semantics": (
            "ATLAS_FIRST_SUCCESSFUL_POST_SESSION_OBSERVATION"
        ),
        "source_publication_time_status": "UNKNOWN_NOT_INFERRED",
        "same_day_decision_eligible": False,
        "receipt_retention": "APPEND_ONLY_NON_RECONSTRUCTIVE",
        "decision_capability": "TEMPORAL_INPUT_ONLY",
    }
    if value != pinned:
        fail("POLICY_INVALID", "pinned semantics")
    return value


def parse_date(value: object, label: str) -> dt.date:
    if not isinstance(value, str):
        fail("DATE_INVALID", label)
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        fail("DATE_INVALID", label)


def parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str):
        fail("TIMESTAMP_INVALID", label)
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        fail("TIMESTAMP_INVALID", label)
    if parsed.tzinfo is None:
        fail("TIMESTAMP_INVALID", label)
    return parsed.astimezone(UTC)


def iso_utc(value: dt.datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def iso_kst(value: dt.datetime) -> str:
    return value.astimezone(KST).isoformat(timespec="seconds")


def resolve_source_session(latest_krx: dict) -> tuple[str, str]:
    collected_for = latest_krx.get("collected_for_kst_date")
    readiness = latest_krx.get("decision_readiness")
    if not isinstance(readiness, dict):
        fail("LATEST_KRX_INVALID", "decision_readiness")
    observation_date = readiness.get("confirmed_through")
    collected = parse_date(collected_for, "collected_for_kst_date")
    observation = parse_date(observation_date, "confirmed_through")
    if observation >= collected:
        fail("LATEST_KRX_NOT_NEXT_SESSION", f"{observation}:{collected}")
    if readiness.get("same_day_confirmation") != "next_day":
        fail("LATEST_KRX_FINALITY_MISMATCH")
    if latest_krx.get("source_tier") != "Official":
        fail("LATEST_KRX_SOURCE_TIER_INVALID")
    return observation.isoformat(), collected.isoformat()


def qualify_response_body(body: bytes, bas_dd: str, policy: dict) -> dict:
    try:
        payload = KRX_PROBE._decode_payload(body)
        counts = KRX_PROBE.validate_payload(payload, bas_dd)
    except KRX_PROBE.Stop as exc:
        fail("KRX_RESPONSE_INVALID", str(exc))
    rows = payload.get("OutBlock_1")
    matches = [
        row
        for row in rows
        if isinstance(row, dict)
        and str(row.get("BAS_DD")) == bas_dd
        and str(row.get("IDX_NM", "")).strip() == policy["source_index_name"]
        and bool(str(row.get("CLSPRC_IDX", "")).strip())
    ]
    if len(matches) != 1:
        fail("EXACT_KOSPI_ROW_REQUIRED", str(len(matches)))
    return {
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "row_count": counts["row_count"],
        "usable_row_count": counts["usable_row_count"],
        "exact_index_row_count": 1,
    }


def fetch_exact_kospi(
    auth_key: str,
    observation_date: str,
    policy: dict,
    *,
    opener=KRX_PROBE.urlopen,
) -> dict:
    bas_dd = observation_date.replace("-", "")
    try:
        request = KRX_PROBE.build_request(auth_key, bas_dd, market="kospi")
        body = KRX_PROBE._http_fetch(request, opener=opener)
    except KRX_PROBE.Stop as exc:
        fail("KRX_FETCH_FAILED", str(exc))
    return qualify_response_body(body, bas_dd, policy)


AUTHORITY = {
    "temporal_input_qualified": True,
    "risk_feature_calculation_authorized": False,
    "stress_classification_authorized": False,
    "regime_score_authorized": False,
    "stage_authorized": False,
    "buy_authorized": False,
    "action_authorized": False,
    "order_authorized": False,
    "production_wiring_authorized": False,
    "trading_authorized": False,
}


def build_receipt(
    *,
    observation_date: str,
    latest_collection_date: str,
    latest_krx_sha256: str,
    source_observation: dict,
    observed_at_utc: str,
    policy: dict | None = None,
) -> dict:
    policy = policy or load_policy()
    observation = parse_date(observation_date, "observation_date")
    collection = parse_date(latest_collection_date, "latest_collection_date")
    observed = parse_utc(observed_at_utc, "observed_at_utc")
    observed_kst_date = observed.astimezone(KST).date()
    effective = parse_date(
        policy["effective_from_available_date"],
        "effective_from_available_date",
    )
    if observation >= collection:
        fail("NOT_NEXT_SESSION_INPUT")
    if observation >= observed_kst_date:
        fail("SAME_DAY_OBSERVATION_NOT_ELIGIBLE")
    if observed_kst_date < effective:
        fail("POLICY_NOT_EFFECTIVE")
    if not isinstance(latest_krx_sha256, str) or not HEX_64.fullmatch(
        latest_krx_sha256
    ):
        fail("LATEST_KRX_SHA256_INVALID")
    expected_source_keys = {
        "response_sha256",
        "row_count",
        "usable_row_count",
        "exact_index_row_count",
    }
    if (
        not isinstance(source_observation, dict)
        or set(source_observation) != expected_source_keys
    ):
        fail("SOURCE_OBSERVATION_INVALID", "schema")
    if not HEX_64.fullmatch(str(source_observation["response_sha256"])):
        fail("SOURCE_OBSERVATION_INVALID", "response_sha256")
    for key in ("row_count", "usable_row_count", "exact_index_row_count"):
        if type(source_observation[key]) is not int or source_observation[key] < 1:
            fail("SOURCE_OBSERVATION_INVALID", key)
    if source_observation["exact_index_row_count"] != 1:
        fail("SOURCE_OBSERVATION_INVALID", "exact_index_row_count")

    receipt = {
        "schema_version": RECEIPT_SCHEMA,
        "policy_version": policy["policy_version"],
        "status": "TEMPORAL_INPUT_QUALIFIED_NEXT_SESSION",
        "market": policy["market"],
        "index_identity": "KOSPI::코스피",
        "observation_date": observation.isoformat(),
        "latest_collection_date": collection.isoformat(),
        "atlas_observed_available_at_utc": iso_utc(observed),
        "atlas_observed_available_at_kst": iso_kst(observed),
        "source_publication_time": None,
        "source_publication_time_status": policy[
            "source_publication_time_status"
        ],
        "availability_semantics": policy["availability_semantics"],
        "same_day_decision_eligible": False,
        "source": {
            "source_name": policy["source_name"],
            "endpoint_path": policy["source_endpoint_path"],
            "bas_dd": observation.strftime("%Y%m%d"),
            "index_name": policy["source_index_name"],
            **source_observation,
            "raw_response_retained": False,
            "index_level_retained": False,
        },
        "lineage": {
            "latest_krx_path": "data/latest_krx.json",
            "latest_krx_sha256": latest_krx_sha256,
            "latest_krx_confirmed_through": observation.isoformat(),
        },
        "authority": dict(AUTHORITY),
    }
    receipt["receipt_sha256"] = payload_sha256(receipt)
    verify_receipt(receipt, policy=policy)
    return receipt


def verify_receipt(receipt: dict, *, policy: dict | None = None) -> None:
    policy = policy or load_policy()
    expected_keys = {
        "schema_version",
        "policy_version",
        "status",
        "market",
        "index_identity",
        "observation_date",
        "latest_collection_date",
        "atlas_observed_available_at_utc",
        "atlas_observed_available_at_kst",
        "source_publication_time",
        "source_publication_time_status",
        "availability_semantics",
        "same_day_decision_eligible",
        "source",
        "lineage",
        "authority",
        "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        fail("RECEIPT_INVALID", "schema")
    claimed = receipt.get("receipt_sha256")
    unsigned = dict(receipt)
    unsigned.pop("receipt_sha256", None)
    if not isinstance(claimed, str) or claimed != payload_sha256(unsigned):
        fail("RECEIPT_HASH_MISMATCH")
    if receipt["schema_version"] != RECEIPT_SCHEMA:
        fail("RECEIPT_INVALID", "schema_version")
    pinned = {
        "policy_version": policy["policy_version"],
        "status": "TEMPORAL_INPUT_QUALIFIED_NEXT_SESSION",
        "market": "KOREA",
        "index_identity": "KOSPI::코스피",
        "source_publication_time": None,
        "source_publication_time_status": "UNKNOWN_NOT_INFERRED",
        "availability_semantics": (
            "ATLAS_FIRST_SUCCESSFUL_POST_SESSION_OBSERVATION"
        ),
        "same_day_decision_eligible": False,
        "authority": AUTHORITY,
    }
    for key, expected in pinned.items():
        if receipt.get(key) != expected:
            fail("RECEIPT_INVALID", key)
    observation = parse_date(receipt["observation_date"], "observation_date")
    collection = parse_date(
        receipt["latest_collection_date"], "latest_collection_date"
    )
    observed_utc = parse_utc(
        receipt["atlas_observed_available_at_utc"],
        "atlas_observed_available_at_utc",
    )
    try:
        observed_kst = dt.datetime.fromisoformat(
            receipt["atlas_observed_available_at_kst"]
        )
    except (TypeError, ValueError):
        fail("TIMESTAMP_INVALID", "atlas_observed_available_at_kst")
    if (
        observed_kst.tzinfo is None
        or observed_kst.utcoffset() != dt.timedelta(hours=9)
    ):
        fail("TIMESTAMP_INVALID", "atlas_observed_available_at_kst")
    if observed_kst.astimezone(UTC) != observed_utc:
        fail("TIMESTAMP_MISMATCH")
    if receipt["atlas_observed_available_at_utc"] != iso_utc(observed_utc):
        fail("TIMESTAMP_NOT_CANONICAL", "atlas_observed_available_at_utc")
    if receipt["atlas_observed_available_at_kst"] != iso_kst(observed_utc):
        fail("TIMESTAMP_NOT_CANONICAL", "atlas_observed_available_at_kst")
    if not observation < collection or not observation < observed_kst.date():
        fail("RECEIPT_TEMPORAL_ORDER_INVALID")
    effective = parse_date(
        policy["effective_from_available_date"],
        "effective_from_available_date",
    )
    if observed_kst.date() < effective:
        fail("POLICY_NOT_EFFECTIVE")
    source = receipt["source"]
    expected_source_keys = {
        "source_name",
        "endpoint_path",
        "bas_dd",
        "index_name",
        "response_sha256",
        "row_count",
        "usable_row_count",
        "exact_index_row_count",
        "raw_response_retained",
        "index_level_retained",
    }
    if not isinstance(source, dict) or set(source) != expected_source_keys:
        fail("RECEIPT_INVALID", "source")
    if source["source_name"] != policy["source_name"]:
        fail("RECEIPT_INVALID", "source_name")
    if source["endpoint_path"] != policy["source_endpoint_path"]:
        fail("RECEIPT_INVALID", "endpoint_path")
    if source["bas_dd"] != observation.strftime("%Y%m%d"):
        fail("RECEIPT_INVALID", "bas_dd")
    if source["index_name"] != policy["source_index_name"]:
        fail("RECEIPT_INVALID", "index_name")
    if not HEX_64.fullmatch(str(source["response_sha256"])):
        fail("RECEIPT_INVALID", "response_sha256")
    for key in ("row_count", "usable_row_count", "exact_index_row_count"):
        if type(source[key]) is not int or source[key] < 1:
            fail("RECEIPT_INVALID", key)
    if source["usable_row_count"] > source["row_count"]:
        fail("RECEIPT_INVALID", "usable_row_count")
    if source["exact_index_row_count"] != 1:
        fail("RECEIPT_INVALID", "exact_index_row_count")
    if source["raw_response_retained"] is not False or source[
        "index_level_retained"
    ] is not False:
        fail("RECEIPT_INVALID", "non_reconstructive")
    lineage = receipt["lineage"]
    if not isinstance(lineage, dict) or set(lineage) != {
        "latest_krx_path",
        "latest_krx_sha256",
        "latest_krx_confirmed_through",
    }:
        fail("RECEIPT_INVALID", "lineage")
    if lineage["latest_krx_path"] != "data/latest_krx.json":
        fail("RECEIPT_INVALID", "latest_krx_path")
    if lineage["latest_krx_confirmed_through"] != observation.isoformat():
        fail("RECEIPT_INVALID", "latest_krx_confirmed_through")
    if not HEX_64.fullmatch(str(lineage["latest_krx_sha256"])):
        fail("RECEIPT_INVALID", "latest_krx_sha256")
    encoded = canonical_bytes(receipt)
    for forbidden in (b'"CLSPRC_IDX"', b'"close"', b'"OutBlock_1"', b'"rows"'):
        if forbidden in encoded:
            fail("RAW_OR_RECONSTRUCTIVE_DATA_FORBIDDEN", forbidden.decode())


def receipt_path(observation_date: str, data_root: Path = DATA_ROOT) -> Path:
    return (
        Path(data_root)
        / "observations"
        / "korea_risk_availability"
        / observation_date
        / "receipt.json"
    )


def publish_receipt(receipt: dict, data_root: Path = DATA_ROOT) -> Path:
    verify_receipt(receipt)
    target = receipt_path(receipt["observation_date"], data_root)
    if target.exists():
        fail("APPEND_ONLY_VIOLATION", str(target))
    if target.parent.exists():
        fail("APPEND_ONLY_DIRECTORY_COLLISION", str(target.parent))
    target.parent.parent.mkdir(parents=True, exist_ok=True)
    staging = target.parent.with_name(f".{target.parent.name}.tmp.{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        (staging / "receipt.json").write_bytes(canonical_bytes(receipt))
        staging.rename(target.parent)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return target


def verify_existing(observation_date: str, data_root: Path = DATA_ROOT) -> dict:
    value, raw = read_json(
        receipt_path(observation_date, data_root),
        "EXISTING_RECEIPT_INVALID",
    )
    if raw != canonical_bytes(value):
        fail("EXISTING_RECEIPT_NOT_CANONICAL")
    verify_receipt(value)
    if value["observation_date"] != observation_date:
        fail("EXISTING_RECEIPT_DATE_MISMATCH")
    return value


def run(
    *,
    auth_key: str,
    latest_krx_path: Path = LATEST_KRX_PATH,
    data_root: Path = DATA_ROOT,
    observed_at_utc: str | None = None,
    opener=KRX_PROBE.urlopen,
) -> tuple[str, dict, Path]:
    policy = load_policy()
    latest_krx, latest_raw = read_json(latest_krx_path, "LATEST_KRX_INVALID")
    observation_date, collection_date = resolve_source_session(latest_krx)
    target = receipt_path(observation_date, data_root)
    if target.exists():
        return "NO_CHANGE", verify_existing(observation_date, data_root), target
    source = fetch_exact_kospi(
        auth_key,
        observation_date,
        policy,
        opener=opener,
    )
    now = observed_at_utc or iso_utc(dt.datetime.now(UTC))
    receipt = build_receipt(
        observation_date=observation_date,
        latest_collection_date=collection_date,
        latest_krx_sha256=hashlib.sha256(latest_raw).hexdigest(),
        source_observation=source,
        observed_at_utc=now,
        policy=policy,
    )
    return "PUBLISHED", receipt, publish_receipt(receipt, data_root)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--latest-krx", type=Path, default=LATEST_KRX_PATH)
    parser.add_argument("--data-root", type=Path, default=DATA_ROOT)
    parser.add_argument("--observed-at-utc")
    parser.add_argument("--verify-existing-date")
    args = parser.parse_args(argv)
    try:
        if args.verify_existing_date:
            receipt = verify_existing(args.verify_existing_date, args.data_root)
            outcome = "VERIFIED_EXISTING"
            path = receipt_path(args.verify_existing_date, args.data_root)
        else:
            outcome, receipt, path = run(
                auth_key=os.environ.get("KRX_API_KEY", ""),
                latest_krx_path=args.latest_krx,
                data_root=args.data_root,
                observed_at_utc=args.observed_at_utc,
            )
    except AvailabilityError as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    print(
        " ".join(
            [
                f"outcome={outcome}",
                f"status={receipt['status']}",
                f"observation_date={receipt['observation_date']}",
                f"available_at={receipt['atlas_observed_available_at_kst']}",
                f"receipt_sha256={receipt['receipt_sha256']}",
                f"path={path}",
                "source_publication_time=UNKNOWN",
                "risk_feature_authorized=false",
                "regime_authorized=false",
                "trading_authorized=false",
            ]
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
