#!/usr/bin/env python3
"""P0-05A -- Read Model Generation Contract (shared by build_briefing_inputs.py
and check_briefing_readiness.py).

Binds data/briefing/step0_status.json, every data/briefing/{krx,dart,sec}/
{SYMBOL}.json compact view, and data/briefing_status.json to one shared
``generation_id`` so a consumer can detect a mixed-generation read (e.g. a
floating-ref/CDN edge serving step0 from one push and a compact view from an
older one) deterministically, instead of trusting HTTP 200 + cache headers.

generation_id is a sha256 over a canonical JSON *generation manifest* built
only from already-committed source facts:
  - expected_kst_date
  - each required collector's own source_sha256 (krx/dart/sec)
  - each optional evidence source's status + source_sha256 (dart_content/
    sec_content)
  - the generation/builder/compact-schema contract versions

It never includes wall-clock time or the generation_id itself (no
self-reference -- embedding a hash of a payload inside that same payload
would be circular), so rebuilding from the same committed inputs always
reproduces the identical generation_id. That determinism is required for
this repo's byte-identical authoritative-regression replay
(ATLAS_DISPOSABLE_CHECKOUT=1 python run_all.py --authoritative).

generation_basis_at_utc is a *separate*, non-hashed field: the max of the
three required collectors' own recorded collected_at_utc. It identifies
which source snapshot the generation was built from -- it is explicitly NOT
wall-clock build-execution time, and callers must never substitute
datetime.utcnow()/time.time() for it (that would make byte-identical replay
impossible and silently break this contract).

generation_id (artifact identity) is deliberately NOT the same thing as a
git commit SHA (transport identity). Embedding a not-yet-known commit SHA
into a file this same workflow step is about to commit is a chicken-and-egg
problem this module does not attempt to solve -- that is P0-05B's
(retrieval-side, doc-only in this PR) responsibility: a consumer resolves
``main`` to an exact commit SHA via an authority-grade channel *first*, then
fetches step0/compact/health pinned to that SHA, then additionally checks
that all three share one generation_id from this module.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

GENERATION_CONTRACT_VERSION = 1
BUILDER_CONTRACT_VERSION = 1
COMPACT_SCHEMA_VERSIONS = {"krx": 2, "dart": 2, "sec": 2}
REQUIRED_SOURCES = ("krx", "dart", "sec")
OPTIONAL_SOURCES = ("dart_content", "sec_content")
OPTIONAL_SOURCE_PATHS = {
    "dart_content": "latest_dart_content.json",
    "sec_content": "latest_sec_content.json",
}


def canonical_json(value) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_manifest(expected_date, source_hashes, optional_evidence):
    """Pure, deterministic.

    ``source_hashes``: {"krx"/"dart"/"sec": source_sha256}.
    ``optional_evidence``: same shape as step0_status.json's
    ``optional_evidence`` block -- {"dart_content"/"sec_content":
    {"status": ..., "source_sha256": ...|absent}}.
    """
    return {
        "generation_contract_version": GENERATION_CONTRACT_VERSION,
        "expected_kst_date": expected_date,
        "required_sources": {
            name: {"source_sha256": source_hashes.get(name)}
            for name in REQUIRED_SOURCES
        },
        "optional_sources": {
            name: {
                "status": (optional_evidence.get(name) or {}).get("status"),
                "source_sha256": (
                    (optional_evidence.get(name) or {}).get("source_sha256")
                ),
            }
            for name in OPTIONAL_SOURCES
        },
        "builder_contract_version": BUILDER_CONTRACT_VERSION,
        "compact_schema_versions": dict(COMPACT_SCHEMA_VERSIONS),
    }


def generation_id_for(manifest) -> str:
    return sha256_hex(canonical_json(manifest))


def optional_source_facts(data_root, expected_date):
    """Independently derive optional-input generation facts from bytes.

    The domain-specific compact builder may classify malformed business
    content more finely. Generation identity only needs a deterministic
    byte/date/run-status fact and must never copy Step-0's own declaration.
    """
    result = {}
    for name in OPTIONAL_SOURCES:
        path = Path(data_root) / OPTIONAL_SOURCE_PATHS[name]
        if not path.exists():
            result[name] = {"status": "missing", "source_sha256": None}
            continue
        try:
            raw = path.read_bytes()
            value = json.loads(raw)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            result[name] = {"status": "invalid", "source_sha256": None}
            continue
        digest = hashlib.sha256(raw).hexdigest()
        if not isinstance(value, dict):
            result[name] = {"status": "invalid", "source_sha256": digest}
            continue
        if value.get("collected_for_kst_date") != expected_date:
            status = "stale"
        elif value.get("run_status") == "OK":
            status = "available"
        elif value.get("run_status") == "DEGRADED":
            status = "degraded"
        else:
            status = "failed"
        result[name] = {"status": status, "source_sha256": digest}
    return result


def basis_at_utc(source_objs):
    """``source_objs``: {"krx"/"dart"/"sec": <raw latest_*.json dict>}.

    Deterministic, source-derived -- never wall-clock. Returns None only if
    none of the three raw objects carry a usable collected_at_utc (should
    not happen for a real collector run; callers still treat that as
    missing metadata, never as license to fall back to now())."""
    values = [
        source_objs.get(name, {}).get("collected_at_utc")
        for name in REQUIRED_SOURCES
    ]
    values = [v for v in values if isinstance(v, str) and v]
    return max(values) if values else None


def generation_block(expected_date, source_hashes, optional_evidence, source_objs):
    manifest = build_manifest(expected_date, source_hashes, optional_evidence)
    return {
        "generation_id": generation_id_for(manifest),
        "generation_contract_version": GENERATION_CONTRACT_VERSION,
        "generation_basis_at_utc": basis_at_utc(source_objs),
    }
