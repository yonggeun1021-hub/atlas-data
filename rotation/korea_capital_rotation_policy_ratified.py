#!/usr/bin/env python3
"""P2-03 rotation-policy RATIFICATION MATERIALIZATION (2026-09-12).

Separate, bounded slice from the P2-03 rotation-policy canonicalization
candidate lane (`rotation/korea_capital_rotation_policy_candidate.py`, PR
#669, merged). That candidate lane is preserved unchanged as historical
UNRATIFIED evidence -- this module does not edit it, does not import it, and
produces entirely separate, real `RATIFIED` artifacts.

External ratification trail (the CIO's own words, not a self-declared claim):
PR #669 review comment
https://github.com/yonggeun1021-hub/atlas-data/pull/669#issuecomment-5643258809
("CIO RATIFICATION DECISION -- P2-03 Korea Rotation Policy semantics: GO"),
posted 2026-09-12T03:47:23Z, ratifying exactly the corrected candidate head
`9ce4a2b9b85896173ed53c4bb4f60e41c9710654` (exact-head CI run `34660715590`,
SUCCESS) reconciled and merged as `04d66c71483fdf51ba93f96cd0c9b538d83846be`.

Ratified semantics (unchanged from the candidate, now made real):
- dedicated, date-independent `korea_sector_identity_binding/1` contract
  (`config/korea_sector_identity_binding_contract.json`, unchanged by this
  module)
- the same explicit 46 KOSPI/KOSDAQ SECTOR `series_identity -> theme_id`
  positional map
- `RELATIVE_STRENGTH_VS_OWN_BENCHMARK` / `DESCENDING_WITHIN_BENCHMARK_SCOPE`
  / `SERIES_IDENTITY_ASC`
- `top_count = bottom_count = 3`, `maximum_calendar_gap_days = 7`
- `ratified_by = "Atlas CIO"`, `ratified_at_utc = "2026-09-12T03:47:23Z"` --
  the real decision instant, never the tainted `2026-08-22T07:19:09Z`
  self-declared timestamp, never the `2026-09-11`/`2026-09-12` candidate
  authoring placeholders.
- `effective_from` = the first VERIFIED KRX trading session after the
  ratification instant, mechanically resolved from the real, already-
  committed official KRX holiday capture
  (`evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json`)
  -- never guessed from weekday arithmetic. See
  `resolve_effective_from()` below.

`korea_capital_rotation.py::_validate_policy()`'s own anti-lookahead
invariant (`covers_both = effective_from <= prior_date and (effective_to is
None or current_date < effective_to)`, checked against `ratified_at_utc`)
means this ratified policy structurally cannot apply to any observation pair
that predates `effective_from` -- and since `effective_from` is itself after
`ratified_at_utc`, no pre-existing evidence can ever satisfy `covers_both`.
The first genuine post-ratification natural proof still requires a REAL
observation pair whose `prior_date >= effective_from` and whose
`current_date` is also inside the effective interval -- this module does not
claim, fabricate, or backfill that pair. It materializes the artifact only;
Phase B (schedule migration, full-packet automation, rolling-pointer wiring)
and Regime/Candidate/Stage/briefing/Production/trading/order/capital
authority all remain exactly as closed as before.
"""
from __future__ import annotations

import copy
import datetime as dt
import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Optional


ROOT = Path(__file__).resolve().parents[1]
LEADERSHIP_POLICY_PATH = ROOT / "config" / "korea_leadership_policy.json"
KRX_HOLIDAY_CAPTURE_PATH = (
    ROOT / "evidence" / "market_calendar" / "krx_global_holiday" / "2026-09-09"
    / "capture-2026.json"
)
DECISION_PATH = ROOT / "config" / "korea_rotation_sector_identity_decision.json"
IDENTITY_DOCUMENT_PATH = (
    ROOT / "config" / "korea_rotation_sector_identity_binding_document.json"
)
BINDING_PATH = ROOT / "config" / "korea_rotation_sector_identity_taxonomy_binding.json"
POLICY_PATH = ROOT / "config" / "korea_capital_rotation_policy_ratified.json"
# NEVER "config/korea_capital_rotation_policy.json" -- that exact path is a
# reserved, tested-absent location. korea_capital_rotation.py's own contract
# declares repository_default_policy: "ABSENT", and test/test_korea_capital_
# rotation.py::test_contract_default_policy_cli_atomic_and_tracked_output_
# boundaries asserts that path does not exist as a real regression guard
# (not just a JSON claim). This ratified artifact is a real, externally
# supplied policy a caller passes explicitly -- never a checked-in default
# the library auto-loads -- so it must not collide with that reserved name.

POLICY_SCHEMA_VERSION = "korea_capital_rotation_policy/1"
IDENTITY_DOCUMENT_CONTRACT_VERSION = "korea_sector_identity_binding/1"

RATIFIED_STATUS = "RATIFIED"
RATIFIED_BY = "Atlas CIO"
RATIFIED_AT_UTC = "2026-09-12T03:47:23Z"
DECISION_TRAIL_URL = (
    "https://github.com/yonggeun1021-hub/atlas-data/pull/669"
    "#issuecomment-5643258809"
)
CANDIDATE_PR = 669

POLICY_ID = "POLICY.P2_03.KOREA_OWN_BENCHMARK_EXTREMES.RATIFIED.V1"
DECISION_ID = "DECISION.P2_03.KOREA_SECTOR_IDENTITY_BINDING.RATIFIED.V1"
TAXONOMY_ID = "TAXONOMY.P2_03.KOREA_SECTOR_IDENTITY.RATIFIED.V1"

MAXIMUM_CALENDAR_GAP_DAYS = 7
TOP_COUNT = 3
BOTTOM_COUNT = 3

REQUIRED_BENCHMARKS = {"KOSPI::코스피", "KOSDAQ::코스닥"}

SCOPE_NOTE = (
    "Fixed, date-independent positional per-benchmark SECTOR "
    "series_identity -> theme_id binding, ratified via the dedicated "
    "korea_sector_identity_binding/1 contract. This is NOT the P2-01 "
    "cross-market Theme / Value-Chain taxonomy "
    "(config/theme_taxonomy_authority_registry.json, owned separately under "
    "PR #576, untouched by this materialization) and does not use the P2-01 "
    "theme_taxonomy/2 graph producer at all. External ratification trail: "
    f"{DECISION_TRAIL_URL}. Regime/Candidate/Stage/briefing/Production/"
    "trading/order/capital authority all remain false; Phase B (schedule "
    "migration, full-packet automation, rolling-pointer wiring) remains "
    "closed until a real post-ratification natural observation pair is "
    "verified."
)


class RotationPolicyRatificationError(ValueError):
    pass


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


CAL = _load_module(
    "krx_official_holiday_calendar_for_ratification",
    ROOT / "market_data" / "krx_official_holiday_calendar.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise RotationPolicyRatificationError(f"SOURCE_FILE_UNREADABLE:{path}") from exc


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _read_json(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RotationPolicyRatificationError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def resolve_effective_from(
    ratified_at_utc: str = RATIFIED_AT_UTC,
    capture_path: Path = KRX_HOLIDAY_CAPTURE_PATH,
) -> str:
    """The first VERIFIED KRX trading session strictly after
    ``ratified_at_utc``, mechanically resolved from the real, committed
    official KRX holiday capture -- never guessed from weekday arithmetic.

    A session is "verified" here to mean: a real calendar date that (a) the
    official KRX holiday capture does not list as closed, and (b) is not a
    weekend. The ratification instant's own calendar date (KST) is never
    itself eligible, even if it happens to be a trading day, because the
    ratification event does not retroactively make an already-elapsed or
    still-open session "after" itself.
    """
    ratified_at = dt.datetime.fromisoformat(ratified_at_utc.replace("Z", "+00:00"))
    ratified_at_kst = ratified_at.astimezone(dt.timezone(dt.timedelta(hours=9)))
    raw = Path(capture_path).read_bytes()
    checked = CAL.validate_capture(raw)
    holidays = {dt.date.fromisoformat(iso) for iso in checked["closures"]}
    candidate = ratified_at_kst.date() + dt.timedelta(days=1)
    for _ in range(366):
        if candidate.weekday() < 5 and candidate not in holidays:
            return candidate.isoformat()
        candidate += dt.timedelta(days=1)
    raise RotationPolicyRatificationError("NO_VERIFIED_SESSION_FOUND_WITHIN_ONE_YEAR")


def load_upstream_leadership_policy(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    value = _read_json(path)
    if (
        value.get("approval_status") != "RATIFIED"
        or value.get("market") != "KOREA"
        or not isinstance(value.get("records"), list)
    ):
        raise RotationPolicyRatificationError("UPSTREAM_LEADERSHIP_POLICY_NOT_RATIFIED")
    return value


def _sector_scopes(path: Path = LEADERSHIP_POLICY_PATH) -> list[tuple[str, str, list[str]]]:
    policy = load_upstream_leadership_policy(path)
    by_benchmark: dict[str, list[str]] = {}
    for record in policy["records"]:
        if record.get("role") != "SECTOR":
            continue
        by_benchmark.setdefault(record["benchmark_identity"], []).append(
            record["series_identity"]
        )
    if set(by_benchmark) != REQUIRED_BENCHMARKS:
        raise RotationPolicyRatificationError("UNEXPECTED_BENCHMARK_SET")
    result = []
    for benchmark in sorted(by_benchmark):
        prefix = "KOSPI" if benchmark.startswith("KOSPI") else "KOSDAQ"
        members = sorted(by_benchmark[benchmark])
        if len(members) != len(set(members)):
            raise RotationPolicyRatificationError(f"DUPLICATE_SERIES_IDENTITY:{benchmark}")
        result.append((prefix, benchmark, members))
    return result


def build_decision(effective_from: str, path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    policy = load_upstream_leadership_policy(path)
    decision = {
        "schema_version": 1,
        "contract_version": "p2_03_rotation_sector_identity_decision/1",
        "decision_id": DECISION_ID,
        "taxonomy_id": TAXONOMY_ID,
        "approval_status": RATIFIED_STATUS,
        "ratified_by": RATIFIED_BY,
        "ratified_at_utc": RATIFIED_AT_UTC,
        "effective_from": effective_from,
        "external_decision_trail": DECISION_TRAIL_URL,
        "superseded_candidate_pr": CANDIDATE_PR,
        "scope_note": SCOPE_NOTE,
        "source_policy_path": "config/korea_leadership_policy.json",
        "source_policy_sha256": file_sha256(path),
        "source_policy_effective_from": policy["effective_from"],
    }
    decision["payload_sha256"] = payload_sha256(decision)
    return decision


def build_identity_document(path: Path = LEADERSHIP_POLICY_PATH) -> dict:
    """The ratified identity content itself -- identical mapping to the
    superseded candidate, no as_of_date field, still fully date-independent."""
    scopes = []
    for prefix, benchmark, members in _sector_scopes(path):
        scopes.append({
            "benchmark_identity": benchmark,
            "members": [
                {"series_identity": identity, "theme_id": f"{prefix}.SECTOR.{index:02d}"}
                for index, identity in enumerate(members, 1)
            ],
        })
    document = {
        "schema_version": 1,
        "contract_version": IDENTITY_DOCUMENT_CONTRACT_VERSION,
        "taxonomy_id": TAXONOMY_ID,
        "binding_status": RATIFIED_STATUS,
        "scope_note": SCOPE_NOTE,
        "source_policy_path": "config/korea_leadership_policy.json",
        "source_policy_sha256": file_sha256(path),
        "source_policy_effective_from": load_upstream_leadership_policy(path)["effective_from"],
        "benchmark_scopes": scopes,
    }
    document["payload_sha256"] = payload_sha256(document)
    return document


def build_taxonomy_binding(
    decision: dict, document: dict, path: Path = LEADERSHIP_POLICY_PATH
) -> dict:
    """The exact 6-field shape korea_capital_rotation.py::_validate_binding()
    requires, under the dedicated korea_sector_identity_binding/1 contract."""
    return {
        "taxonomy_contract_version": IDENTITY_DOCUMENT_CONTRACT_VERSION,
        "taxonomy_id": TAXONOMY_ID,
        "taxonomy_decision_id": DECISION_ID,
        "taxonomy_decision_sha256": decision["payload_sha256"],
        "taxonomy_packet_sha256": document["payload_sha256"],
        "upstream_leadership_policy_sha256": file_sha256(path),
    }


def build_ratified_policy(
    binding: dict, effective_from: str, path: Path = LEADERSHIP_POLICY_PATH
) -> dict:
    benchmark_scopes = [
        {
            "benchmark_identity": benchmark,
            "members": [
                {"series_identity": identity, "theme_id": f"{prefix}.SECTOR.{index:02d}"}
                for index, identity in enumerate(members, 1)
            ],
            "top_count": TOP_COUNT,
            "bottom_count": BOTTOM_COUNT,
        }
        for prefix, benchmark, members in _sector_scopes(path)
    ]
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "policy_id": POLICY_ID,
        "approval_status": RATIFIED_STATUS,
        "ratified_by": RATIFIED_BY,
        "ratified_at_utc": RATIFIED_AT_UTC,
        "effective_from": effective_from,
        "effective_to": None,
        "taxonomy_decision_sha256": binding["taxonomy_decision_sha256"],
        "taxonomy_packet_sha256": binding["taxonomy_packet_sha256"],
        "upstream_leadership_policy_sha256": binding["upstream_leadership_policy_sha256"],
        "ranking_metric": "RELATIVE_STRENGTH_VS_OWN_BENCHMARK",
        "ranking_order": "DESCENDING_WITHIN_BENCHMARK_SCOPE",
        "tie_break": "SERIES_IDENTITY_ASC",
        "maximum_calendar_gap_days": MAXIMUM_CALENDAR_GAP_DAYS,
        "benchmark_scopes": benchmark_scopes,
    }


def build_all(path: Path = LEADERSHIP_POLICY_PATH) -> tuple[dict, dict, dict, dict]:
    effective_from = resolve_effective_from()
    decision = build_decision(effective_from, path)
    document = build_identity_document(path)
    binding = build_taxonomy_binding(decision, document, path)
    policy = build_ratified_policy(binding, effective_from, path)
    return decision, document, binding, policy


def write_artifacts(
    decision_path: Path = DECISION_PATH,
    document_path: Path = IDENTITY_DOCUMENT_PATH,
    binding_path: Path = BINDING_PATH,
    policy_path: Path = POLICY_PATH,
    source_path: Path = LEADERSHIP_POLICY_PATH,
) -> None:
    decision, document, binding, policy = build_all(source_path)
    _write_json(decision_path, decision)
    _write_json(document_path, document)
    _write_json(binding_path, binding)
    _write_json(policy_path, policy)


def load_committed_decision(path: Path = DECISION_PATH) -> dict:
    return _read_json(path)


def load_committed_document(path: Path = IDENTITY_DOCUMENT_PATH) -> dict:
    return _read_json(path)


def load_committed_binding(path: Path = BINDING_PATH) -> dict:
    return _read_json(path)


def load_committed_policy(path: Path = POLICY_PATH) -> dict:
    return _read_json(path)


def main() -> int:
    write_artifacts()
    print(
        "wrote RATIFIED P2-03 sector-identity binding + rotation-policy "
        "artifacts (Phase B / Regime / Candidate / Stage / briefing / "
        "Production / trading / order / capital authority all remain closed)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
