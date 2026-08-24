#!/usr/bin/env python3
"""P0-05B immutable-commit retrieval for the Atlas briefing read model.

Resolve ``main`` once through GitHub's Git Data API with no-cache headers,
then fetch every artifact through the Contents API pinned to that exact commit.
Branch/tag/raw-CDN reads and cross-commit fallback are intentionally absent.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
from pathlib import Path
from pathlib import PurePosixPath
import re
import tempfile
from urllib.parse import quote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = ROOT / "config/read_model_authority_contract.json"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")


class RetrievalError(RuntimeError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise RetrievalError(f"{code}{': ' + detail if detail else ''}")


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_bytes(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def git_blob_sha1(raw: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(raw)).encode("ascii") + b"\0" + raw).hexdigest()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    try:
        contract = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("AUTHORITY_CONTRACT_UNREADABLE", type(exc).__name__)
    expected_keys = {
        "schema_version", "repository", "branch", "canonical_ref_endpoint",
        "canonical_content_endpoint_template", "cache_policy", "required_headers",
        "required_artifacts", "compact_path_templates", "stale_detection", "authority",
    }
    if not isinstance(contract, dict) or set(contract) != expected_keys:
        fail("AUTHORITY_CONTRACT_FIELDS_MISMATCH")
    if contract["schema_version"] != "read_model_authority/1":
        fail("AUTHORITY_CONTRACT_VERSION_UNSUPPORTED")
    repository = contract.get("repository")
    branch = contract.get("branch")
    if not isinstance(repository, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        fail("AUTHORITY_REPOSITORY_INVALID")
    if branch != "main":
        fail("AUTHORITY_BRANCH_UNSUPPORTED")
    expected_ref = f"https://api.github.com/repos/{repository}/git/ref/heads/{branch}"
    expected_content = (
        f"https://api.github.com/repos/{repository}/contents/"
        "{path}?ref={immutable_commit_sha}"
    )
    if contract.get("canonical_ref_endpoint") != expected_ref:
        fail("AUTHORITY_REF_ENDPOINT_MISMATCH")
    if contract.get("canonical_content_endpoint_template") != expected_content:
        fail("AUTHORITY_CONTENT_ENDPOINT_MISMATCH")
    if contract["cache_policy"] != "NO_CACHE_BRANCH_RESOLUTION_THEN_IMMUTABLE_COMMIT_PIN":
        fail("AUTHORITY_CACHE_POLICY_UNSUPPORTED")
    if contract.get("required_headers") != {
        "Accept": "application/vnd.github+json",
        "Cache-Control": "no-cache",
        "X-GitHub-Api-Version": "2022-11-28",
    }:
        fail("AUTHORITY_HEADERS_MISMATCH")
    if contract.get("required_artifacts") != [
        "data/briefing/step0_status.json", "data/briefing_status.json"
    ]:
        fail("AUTHORITY_REQUIRED_ARTIFACTS_MISMATCH")
    for template in (contract.get("compact_path_templates") or {}).values():
        if (
            not isinstance(template, str)
            or template.count("{symbol}") != 1
            or PurePosixPath(template).is_absolute()
            or ".." in PurePosixPath(template).parts
        ):
            fail("AUTHORITY_COMPACT_TEMPLATE_INVALID")
    authority = contract.get("authority")
    if not isinstance(authority, dict) or authority.get("read_model_retrieval_only") is not True:
        fail("AUTHORITY_BOUNDARY_INVALID")
    if any(value is True for key, value in authority.items() if key != "read_model_retrieval_only"):
        fail("AUTHORITY_ESCALATION")
    return contract


def _default_get_json(url: str, headers: dict[str, str]) -> dict:
    request = Request(url, headers=headers, method="GET")
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read()
    except Exception as exc:
        fail("AUTHORITY_ENDPOINT_UNAVAILABLE", type(exc).__name__)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        fail("AUTHORITY_ENDPOINT_NON_JSON")
    if not isinstance(value, dict):
        fail("AUTHORITY_ENDPOINT_NOT_OBJECT")
    return value


def resolve_immutable_commit(contract: dict, get_json=_default_get_json) -> str:
    value = get_json(contract["canonical_ref_endpoint"], contract["required_headers"])
    expected_ref = f"refs/heads/{contract['branch']}"
    obj = value.get("object")
    if value.get("ref") != expected_ref or not isinstance(obj, dict):
        fail("BRANCH_REF_RESPONSE_MISMATCH")
    sha = obj.get("sha")
    if obj.get("type") != "commit" or not isinstance(sha, str) or not FULL_SHA.fullmatch(sha):
        fail("IMMUTABLE_COMMIT_INVALID")
    return sha


def _content_url(contract: dict, path: str, commit_sha: str) -> str:
    if not FULL_SHA.fullmatch(commit_sha):
        fail("IMMUTABLE_COMMIT_INVALID")
    template = contract["canonical_content_endpoint_template"]
    return template.replace("{path}", quote(path, safe="/")).replace(
        "{immutable_commit_sha}", commit_sha
    )


def fetch_file_at_commit(
    contract: dict, path: str, commit_sha: str, get_json=_default_get_json
) -> tuple[bytes, dict]:
    url = _content_url(contract, path, commit_sha)
    value = get_json(url, contract["required_headers"])
    if value.get("type") != "file" or value.get("path") != path:
        fail("ARTIFACT_IDENTITY_MISMATCH", path)
    if value.get("encoding") != "base64" or not isinstance(value.get("content"), str):
        fail("ARTIFACT_ENCODING_UNSUPPORTED", path)
    try:
        encoded = "".join(value["content"].split())
        raw = base64.b64decode(encoded, validate=True)
    except Exception:
        fail("ARTIFACT_BASE64_INVALID", path)
    blob_sha = value.get("sha")
    if not isinstance(blob_sha, str) or git_blob_sha1(raw) != blob_sha:
        fail("ARTIFACT_BLOB_SHA_MISMATCH", path)
    return raw, {
        "path": path,
        "commit_sha": commit_sha,
        "git_blob_sha1": blob_sha,
        "content_sha256": sha256_bytes(raw),
        "canonical_endpoint": url,
    }


def _parse_object(raw: bytes, path: str) -> dict:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("ARTIFACT_JSON_INVALID", path)
    if not isinstance(value, dict):
        fail("ARTIFACT_JSON_NOT_OBJECT", path)
    return value


def retrieve(
    expected_kst_date: str,
    symbols: dict[str, list[str]] | None = None,
    contract: dict | None = None,
    get_json=_default_get_json,
) -> tuple[dict[str, bytes], dict]:
    contract = contract or load_contract()
    commit_sha = resolve_immutable_commit(contract, get_json)
    paths = list(contract["required_artifacts"])
    symbols = symbols or {}
    for market, requested in sorted(symbols.items()):
        template = contract["compact_path_templates"].get(market)
        if template is None:
            fail("COMPACT_MARKET_UNSUPPORTED", market)
        for symbol in sorted(set(requested)):
            if not re.fullmatch(r"[A-Za-z0-9._-]+", symbol):
                fail("COMPACT_SYMBOL_INVALID", symbol)
            paths.append(template.format(symbol=symbol))

    raw_by_path: dict[str, bytes] = {}
    records = []
    parsed = {}
    for path in paths:
        raw, record = fetch_file_at_commit(contract, path, commit_sha, get_json)
        raw_by_path[path] = raw
        records.append(record)
        parsed[path] = _parse_object(raw, path)

    step_path, health_path = contract["required_artifacts"]
    step0 = parsed[step_path]
    health = parsed[health_path]
    for path, value in parsed.items():
        observed_date = value.get("expected_kst_date")
        if observed_date is None:
            observed_date = value.get("collected_for_kst_date")
        if observed_date != expected_kst_date:
            fail("ARTIFACT_STALE_DATE", path)
    step_generation = step0.get("generation")
    health_generation = health.get("generation")
    if not isinstance(step_generation, dict) or not isinstance(health_generation, dict):
        fail("GENERATION_METADATA_MISSING")
    generation_id = step_generation.get("generation_id")
    if not isinstance(generation_id, str) or len(generation_id) != 64:
        fail("GENERATION_ID_INVALID")
    if health_generation.get("generation_id") != generation_id:
        fail("MIXED_GENERATION_READ")
    for path, value in parsed.items():
        if path in (step_path, health_path):
            continue
        if (value.get("generation") or {}).get("generation_id") != generation_id:
            fail("MIXED_GENERATION_READ", path)

    envelope = {
        "schema_version": "read_model_retrieval/1",
        "repository": contract["repository"],
        "branch": contract["branch"],
        "source_commit": commit_sha,
        "expected_kst_date": expected_kst_date,
        "generation_id": generation_id,
        "canonical_ref_endpoint": contract["canonical_ref_endpoint"],
        "cache_policy": contract["cache_policy"],
        "artifacts": records,
        "stale_detection": "PASS",
        "authority": contract["authority"],
    }
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
        (staging / "retrieval_authority.json").write_text(
            json.dumps(envelope, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if output_dir.exists():
            fail("OUTPUT_ALREADY_EXISTS", str(output_dir))
        staging.replace(output_dir)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-kst-date", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--krx-symbol", action="append", default=[])
    parser.add_argument("--dart-symbol", action="append", default=[])
    parser.add_argument("--sec-symbol", action="append", default=[])
    args = parser.parse_args(argv)
    raw, envelope = retrieve(
        args.expected_kst_date,
        {"krx": args.krx_symbol, "dart": args.dart_symbol, "sec": args.sec_symbol},
    )
    persist(args.output_dir, raw, envelope)
    print(json.dumps({
        "status": "PASS",
        "source_commit": envelope["source_commit"],
        "generation_id": envelope["generation_id"],
        "artifact_count": len(envelope["artifacts"]),
        "authority": envelope["authority"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
