#!/usr/bin/env python3
"""Hash-bound public component registry for the P1-CR-08 five-axis adapter.

This module discovers only the already-existing, public, repository-local
component rows used by ``briefing/daily_orchestrator.py``:

* BTC_TREND
* BTC_RISK
* STABLECOIN_NET_ISSUANCE
* CRYPTO_BREADTH

It invents no axis value, threshold, weight, direction, candidate rule, or
authority.  A source is eligible for a decision generation only when its own
retained ``_downloaded_at.txt`` instant is not later than that generation.
That point-in-time cutoff makes historical revalidation stable even when a
same-date source directory is committed later in the day.  Every included
directory is fingerprinted over exact relative filenames, sizes, and SHA-256
digests, and every component row is rebuilt through the existing daily
builder during validation.  Omitting a source that was already available,
substituting a row, or changing retained bytes therefore fails closed.

CRYPTO_LEADERSHIP remains deliberately absent until the existing leadership
transform has both a daily component-row producer and its required dual-window
natural history.  CRYPTO_BREADTH remains whatever its existing taxonomy gate
reports.  The registry never converts either gap into a synthetic value.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


CODE_ROOT = Path(__file__).resolve().parents[1]
ROOT = CODE_ROOT
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

CONTRACT_PATH = ROOT / "config" / "crypto_live_component_registry_contract.json"
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
KST = dt.timezone(dt.timedelta(hours=9))


class CryptoLiveComponentRegistryError(ValueError):
    """Fail-closed registry contract, source, or rederivation violation."""


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoLiveComponentRegistryError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoLiveComponentRegistryError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _expected_contract() -> dict:
    return {
        "schema_version": 1,
        "contract_version": "crypto_live_component_registry_contract/1",
        "output_schema_version": "crypto_live_component_registry/1",
        "mode": "PUBLIC_EVIDENCE_ONLY_NO_INTERPRETATION",
        "component_order": [
            "BTC_TREND", "BTC_RISK", "STABLECOIN_NET_ISSUANCE", "CRYPTO_BREADTH",
        ],
        "source_roots": {
            "BTC_TREND": "evidence/crypto/btc/raw",
            "BTC_RISK": "evidence/crypto/btc/raw",
            "STABLECOIN_NET_ISSUANCE": "evidence/stablecoin/raw",
            "CRYPTO_BREADTH": "evidence/crypto/breadth/raw",
        },
        "deferred_components": {
            "CRYPTO_LEADERSHIP": (
                "DAILY_COMPONENT_ROW_PRODUCER_AND_DUAL_WINDOW_HISTORY_UNAVAILABLE"
            ),
        },
        "authority": {
            "evidence_registry_only": True,
            "regime_interpretation_authorized": False,
            "threshold_authorized": False,
            "strategy_authorized": False,
            "action_authorized": False,
            "paper_order_authorized": False,
            "exchange_order_authorized": False,
            "withdrawal_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_capital_authorized": False,
        },
    }


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    value = _read_json(Path(path))
    expected = _expected_contract()
    if value != expected:
        raise CryptoLiveComponentRegistryError("CONTRACT_MISMATCH")
    return copy.deepcopy(value)


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise CryptoLiveComponentRegistryError(f"UTC_INVALID:{label}")
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=dt.timezone.utc
        )
    except ValueError as exc:
        raise CryptoLiveComponentRegistryError(f"UTC_INVALID:{label}") from exc


def _operational_date_kst(generated_at: str) -> str:
    return _parse_utc(generated_at, "generated_at").astimezone(KST).date().isoformat()


def _relative_directory(
    path_value: object, expected_root: str, date: str, *, root: Path
) -> Path:
    if not isinstance(path_value, str) or Path(path_value).is_absolute():
        raise CryptoLiveComponentRegistryError("SOURCE_PATH_INVALID")
    path = (root / path_value).resolve()
    expected = (root / expected_root / date).resolve()
    if path != expected or not path.is_dir() or path.name != date:
        raise CryptoLiveComponentRegistryError("SOURCE_PATH_INVALID")
    return path


def _directory_fingerprint(path: Path) -> dict:
    files = sorted(item for item in path.rglob("*") if item.is_file())
    if not files:
        raise CryptoLiveComponentRegistryError(f"SOURCE_DIRECTORY_EMPTY:{path}")
    entries = []
    for item in files:
        try:
            content = item.read_bytes()
        except OSError as exc:
            raise CryptoLiveComponentRegistryError(
                f"SOURCE_FILE_READ_FAILED:{item}:{exc}"
            ) from exc
        entries.append({
            "path": str(item.relative_to(path)),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        })
    return {
        "file_count": len(entries),
        "tree_sha256": payload_sha256(entries),
    }


def _daily_module(root: Path):
    daily = _load(
        "crypto_live_component_registry_daily_orchestrator",
        "briefing/daily_orchestrator.py",
    )
    # The executable code and transforms always come from CODE_ROOT. Only
    # repository-local evidence resolution follows the caller's independently
    # verified observation checkout (the existing P10 bridge redirects its
    # decision validator ROOT in exactly this way).
    daily.ROOT = root
    return daily


def _builders(daily) -> dict:
    return {
        "BTC_TREND": daily.build_btc_trend,
        "BTC_RISK": daily.build_btc_risk,
        "STABLECOIN_NET_ISSUANCE": daily.build_stablecoin,
        "CRYPTO_BREADTH": daily.build_crypto_breadth,
    }


def _authority_is_safe(row: dict) -> bool:
    if any(row.get(key) is not False for key in (
        "decision_eligible", "action_eligible", "order_eligible",
    )):
        return False

    def visit(value) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if key.endswith("_authorized") and item is not False:
                    return False
                if not visit(item):
                    return False
        elif isinstance(value, list):
            return all(visit(item) for item in value)
        return True

    return visit(row)


def _assemble(generated_at: str, contract: dict, *, root: Path) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise CryptoLiveComponentRegistryError("OBSERVATION_ROOT_INVALID")
    generated_dt = _parse_utc(generated_at, "generated_at")
    operational_date = _operational_date_kst(generated_at)
    if not DATE_RE.fullmatch(operational_date):
        raise CryptoLiveComponentRegistryError("OPERATIONAL_DATE_INVALID")

    daily = _daily_module(root)
    builders = _builders(daily)
    rows = {}
    components_by_path: dict[str, list[str]] = {}
    for component_id in contract["component_order"]:
        row = builders[component_id](operational_date)
        if not isinstance(row, dict) or row.get("component_id") != component_id:
            raise CryptoLiveComponentRegistryError(
                f"COMPONENT_ROW_INVALID:{component_id}"
            )
        path_value = row.get("source_packet_path")
        available_value = row.get("available_at") or row.get("generated_at")
        # A source absent at this decision instant remains absent on replay,
        # even if its same-date daily capture is committed later.  The later
        # row's own retained download instant will be after generated_at.
        if path_value is None or available_value is None:
            continue
        available_dt = _parse_utc(available_value, f"{component_id}.available_at")
        if available_dt > generated_dt:
            continue
        path = _relative_directory(
            path_value,
            contract["source_roots"][component_id],
            operational_date,
            root=root,
        )
        if row.get("validated") is not True or not _authority_is_safe(row):
            raise CryptoLiveComponentRegistryError(
                f"COMPONENT_ROW_AUTHORITY_INVALID:{component_id}"
            )
        rows[component_id] = copy.deepcopy(row)
        components_by_path.setdefault(str(path.relative_to(root)), []).append(component_id)

    source_directories = []
    for path_value, component_ids in sorted(components_by_path.items()):
        path = (root / path_value).resolve()
        fingerprint = _directory_fingerprint(path)
        downloaded_at_path = path / "_downloaded_at.txt"
        try:
            downloaded_at = downloaded_at_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise CryptoLiveComponentRegistryError(
                f"DOWNLOADED_AT_READ_FAILED:{path_value}:{exc}"
            ) from exc
        if _parse_utc(downloaded_at, f"{path_value}._downloaded_at") > generated_dt:
            raise CryptoLiveComponentRegistryError(
                f"SOURCE_FROM_FUTURE:{path_value}"
            )
        source_directories.append({
            "path": path_value,
            "component_ids": sorted(component_ids),
            "downloaded_at": downloaded_at,
            **fingerprint,
        })

    record = {
        "schema_version": contract["output_schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "generated_at": generated_at,
        "operational_date_kst": operational_date,
        "rows": rows,
        "source_directories": source_directories,
        "deferred_components": copy.deepcopy(contract["deferred_components"]),
        "authority": copy.deepcopy(contract["authority"]),
    }
    record["payload_sha256"] = payload_sha256(record)
    return record


def build_registry(
    generated_at: str,
    contract: dict | None = None,
    *,
    root: Path = ROOT,
) -> dict:
    contract = load_contract() if contract is None else contract
    if contract != _expected_contract():
        raise CryptoLiveComponentRegistryError("CONTRACT_MISMATCH")
    return _assemble(generated_at, contract, root=root)


def validate_registry(
    record: dict,
    *,
    expected_generated_at: str,
    contract: dict | None = None,
    root: Path = ROOT,
) -> dict:
    contract = load_contract() if contract is None else contract
    if contract != _expected_contract():
        raise CryptoLiveComponentRegistryError("CONTRACT_MISMATCH")
    fields = {
        "schema_version", "contract_version", "mode", "generated_at",
        "operational_date_kst", "rows", "source_directories",
        "deferred_components", "authority", "payload_sha256",
    }
    if not isinstance(record, dict) or set(record) != fields:
        raise CryptoLiveComponentRegistryError("REGISTRY_FIELDS_MISMATCH")
    if (
        record.get("schema_version") != contract["output_schema_version"]
        or record.get("contract_version") != contract["contract_version"]
        or record.get("mode") != contract["mode"]
        or record.get("generated_at") != expected_generated_at
        or record.get("operational_date_kst") != _operational_date_kst(expected_generated_at)
        or record.get("deferred_components") != contract["deferred_components"]
        or record.get("authority") != contract["authority"]
    ):
        raise CryptoLiveComponentRegistryError("REGISTRY_IDENTITY_INVALID")
    digest = record.get("payload_sha256")
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        raise CryptoLiveComponentRegistryError("REGISTRY_SHA256_INVALID")
    unsigned = copy.deepcopy(record)
    unsigned.pop("payload_sha256")
    if payload_sha256(unsigned) != digest:
        raise CryptoLiveComponentRegistryError("REGISTRY_SHA256_MISMATCH")
    expected = _assemble(expected_generated_at, contract, root=root)
    if canonical_json(expected) != canonical_json(record):
        raise CryptoLiveComponentRegistryError("REGISTRY_DERIVATION_MISMATCH")
    return copy.deepcopy(record)


def component_rows(
    record: dict, *, expected_generated_at: str, root: Path = ROOT
) -> dict:
    return validate_registry(
        record, expected_generated_at=expected_generated_at, root=root
    )["rows"]
