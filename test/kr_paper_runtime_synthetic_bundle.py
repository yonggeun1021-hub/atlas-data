"""SYNTHETIC test-only helper: relabel the committed 09-10/09-11 capture bundle.

The provider bodies are reused byte-for-byte under new session labels so the
adoption chain (root -> next session -> rolling window) can be exercised
offline.  The output is never evidence and is never written to the repository.
"""
from __future__ import annotations

import copy
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import kr_information_system_runtime_bridge as BRIDGE
from regime import paper_regime_reference as REFERENCE

SOURCE_BUNDLE = ROOT / "evidence/regime/kr_information_system/2026-09-11"


def _shift(value: str, delta: dt.timedelta) -> str:
    moved = dt.datetime.fromisoformat(value.replace("Z", "+00:00")) + delta
    return moved.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z")


def relabeled_bundle(target: Path, previous: str, current: str, completed_at: str) -> Path:
    """Write a bundle for (previous, current) YYYYMMDD with capture completed at completed_at."""
    mapping = {"20260910": previous, "20260911": current}
    manifest = json.loads((SOURCE_BUNDLE / "source-capture/manifest.json").read_bytes())
    delta = (
        dt.datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        - dt.datetime.fromisoformat(manifest["capture_completed_at_utc"].replace("Z", "+00:00"))
    )
    responses = target / "source-capture/responses"
    responses.mkdir(parents=True, exist_ok=True)
    for path in sorted((SOURCE_BUNDLE / "source-capture/responses").glob("*.json")):
        day, rest = path.name.split("-", 1)
        (responses / f"{mapping[day]}-{rest}").write_bytes(path.read_bytes())
    manifest["dates"] = [previous, current]
    manifest["capture_started_at_utc"] = _shift(manifest["capture_started_at_utc"], delta)
    manifest["capture_completed_at_utc"] = _shift(manifest["capture_completed_at_utc"], delta)
    for record in manifest["records"]:
        day, market, family = record["key"].split(":")
        record["key"] = f"{mapping[day]}:{market}:{family}"
        record["request"]["public_params"]["trdDd"] = mapping[day]
        record["response"]["path"] = record["response"]["path"].replace(day, mapping[day])
        record["response"]["received_at_utc"] = _shift(record["response"]["received_at_utc"], delta)
    manifest["payload_sha256"] = BRIDGE._manifest_payload_sha256(manifest)
    manifest_raw = (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    (target / "source-capture/manifest.json").write_bytes(manifest_raw)

    wrapper = json.loads((SOURCE_BUNDLE / "KR_PAPER_REFERENCE_CANDIDATE.json").read_bytes())
    source = copy.deepcopy(wrapper["source_packet"])
    iso = {key: f"{value[:4]}-{value[4:6]}-{value[6:]}" for key, value in mapping.items()}
    source["previous_date"], source["as_of_date"] = iso["20260910"], iso["20260911"]
    source["available_at"] = manifest["capture_completed_at_utc"]
    source["generated_at"] = manifest["capture_completed_at_utc"]
    origin = source["source"]
    origin["session_calendar"]["previous_completed_session"] = previous
    origin["session_calendar"]["latest_completed_session"] = current
    origin["session_calendar"]["decision_at_utc"] = manifest["capture_started_at_utc"]
    origin["source_capture"]["manifest_payload_sha256"] = manifest["payload_sha256"]
    for markets in origin["requests"].values():
        for row in markets.values():
            row["previous_fetched_at_utc"] = _shift(row["previous_fetched_at_utc"], delta)
            row["current_fetched_at_utc"] = _shift(row["current_fetched_at_utc"], delta)
    source.pop("payload_sha256")
    source["payload_sha256"] = BRIDGE.sha256(BRIDGE.canonical_bytes(source))
    wrapper["source_packet"] = source
    policy = json.loads(BRIDGE.REFERENCE_POLICY_PATH.read_bytes())
    wrapper["paper_reference"] = REFERENCE.build_kr(
        source, policy, render_version=REFERENCE.CURRENT_RENDER_VERSION
    )
    (target / "KR_PAPER_REFERENCE_CANDIDATE.json").write_bytes(
        (json.dumps(wrapper, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()
    )
    return target
