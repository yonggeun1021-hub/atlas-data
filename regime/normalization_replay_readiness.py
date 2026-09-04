#!/usr/bin/env python3
"""P1-COM-05 market-specific normalization replay-readiness evidence — SHADOW only.

CIO mandate (2026-09-04, "BUILD_NORMALIZATION_REPLAY_READINESS_EVIDENCE"): using
only *already-retained* historical evidence, determine which of the 10 US/KR
sensor axes in ``config/paper_regime_reference_policy_v1.json`` can actually be
recomputed with the current, unmodified candidate normalization rule, and
measure how that rule behaves when replayed over whatever history exists.

This module invents nothing:

* It does not add, remove, or edit a single threshold.  ``build_us``/``build_kr``
  are imported unmodified from ``regime.paper_regime_reference`` and applied
  exactly as written today; this file never reimplements or overrides them.
* It does not tune, backtest-optimize, or select among candidate values.  It
  only reports coverage, staleness, transition, UNKNOWN, and determinism
  statistics for the *one* rule that already exists on disk.
* It does not touch ``signed_normalization_policy``, the registry, TTL/PIT
  acceptance, or any authority flag.  Every authority in its own output is
  false except the read-only "this is shadow evidence" marker.

Historical source per market is whatever this repository has already retained
as append-only evidence prior to this run:

* US  — ``evidence/free_market_data/derived/<date>/manifest.json``.  Each dated
  file is a byte-for-byte historical copy of what ``collectors/free_market_data``
  published to ``data/latest_free_market_data.json`` that day, so it carries the
  exact ``us_market_reference`` / ``fred`` / ``fred_liquidity`` shape
  ``build_us`` already consumes.
* KR  — ``data/observations/korea_market_signals/<date>/packet.json``.  Each
  dated file is the KRX-sourced axis packet for that session, in the exact
  shape ``build_kr`` already consumes.

Neither directory is created or curated by this module — it only reads what is
already there.  "Replay sufficiency" below is a statement about *evidence
volume*, never about a market-regime threshold: whether at least two distinct,
independently observed days exist so a rerun can show something more than a
single point.  That volume rule is fixed in this file's own docstring/tests,
is unrelated to TREND/BREADTH/RISK_VOL/LIQUIDITY/LEADERSHIP classification
values, and CIO may revise it without touching any sensor threshold.

Point-in-time integrity is structural, not asserted: each retained date is read
and replayed on its own (``_replay_one_date`` sees one file plus the on-disk
candidate policy), so no later date can influence an earlier one.  A date whose
retained shape the candidate rule cannot consume is recorded as one unreplayable
date — by error *type* only, never by message — instead of aborting the report
for every other retained day.  The report pins the sha256 of every snapshot it
replayed and never writes to those sources.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import paper_regime_reference as PRR  # noqa: E402


SCHEMA_VERSION = "regime_normalization_replay_readiness/v1"
MODE = "SHADOW_EVIDENCE_ONLY_NOT_RATIFICATION"
SHA256 = re.compile(r"^[0-9a-f]{64}$")
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

US_SOURCE_ROOT = "evidence/free_market_data/derived"
US_SOURCE_FILENAME = "manifest.json"
KR_SOURCE_ROOT = "data/observations/korea_market_signals"
KR_SOURCE_FILENAME = "packet.json"

# fail() codes raised by regime.paper_regime_reference.build_us/build_kr that
# are attributable to one specific axis (verbatim from that module — not
# reinvented here).  A code outside this map means the whole day's packet was
# gated before any single axis could be evaluated (see *_COVERAGE_GATE_CODES).
US_AXIS_ERROR_CODE = {
    "US_TREND_INVALID": "TREND",
    "US_BREADTH_INVALID": "BREADTH",
    "US_VIX_INVALID": "RISK_VOL",
    "US_LIQUIDITY_INVALID": "LIQUIDITY",
    "US_LEADERSHIP_INVALID": "LEADERSHIP",
}
US_COVERAGE_GATE_CODES = {"US_REFERENCE_NOT_READY", "US_REFERENCE_INCOMPLETE"}

KR_AXIS_ERROR_CODE = {
    "KR_TREND_INVALID": "TREND",
    "KR_BREADTH_INVALID": "BREADTH",
    "KR_RISK_INVALID": "RISK_VOL",
    "KR_LIQUIDITY_INVALID": "LIQUIDITY",
    "KR_LEADERSHIP_INVALID": "LEADERSHIP",
}
KR_COVERAGE_GATE_CODES = {"KR_REFERENCE_NOT_READY", "KR_REFERENCE_INCOMPLETE"}

# Evidence-volume rule only (see module docstring) — not a sensor threshold.
# COMPUTABLE_NOW requires at least this many independently observed days
# (so a rerun can show more than a single point) AND observed_dates covering
# at least half of the retained dated snapshots discovered for that source.
MIN_OBSERVED_DATES_FOR_COMPUTABLE = 2
MIN_OBSERVED_COVERAGE_FRACTION_NUM = 1
MIN_OBSERVED_COVERAGE_FRACTION_DEN = 2


class ReplayReadinessError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise ReplayReadinessError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReplayReadinessError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise ReplayReadinessError(f"SOURCE_MISSING:{path}") from exc


def _error_code(exc: BaseException) -> str:
    return str(exc).split(":", 1)[0]


def discover_dates(source_dir: Path, filename: str) -> list[str]:
    """List dated subdirectories under ``source_dir`` that hold ``filename``.

    Read-only directory listing over whatever this repository already
    retained before this run — nothing is written, generated, or backfilled
    here.
    """
    if not source_dir.is_dir():
        return []
    found = []
    for entry in source_dir.iterdir():
        if entry.is_dir() and DATE.fullmatch(entry.name) and (entry / filename).is_file():
            found.append(entry.name)
    return sorted(found)


def _replay_market(
    dates: list[str],
    source_dir: Path,
    filename: str,
    read_code: str,
    build_fn,
    policy: dict,
    axis_error_code: dict,
    coverage_gate_codes: set,
) -> dict:
    """Run ``build_fn`` (build_us or build_kr, unmodified) against every
    retained dated snapshot and bucket each of the 5 axes' outcome per date.

    Returns ``{date: {"outcome": ..., "axes": {axis: row_or_None},
    "blocking_code": str|None}}`` for downstream summarization.  Every date is
    read and replayed strictly on its own: no date's evaluation can see, or be
    influenced by, any other date's snapshot or outcome.
    """
    per_date = {}
    for date in dates:
        path = source_dir / date / filename
        per_date[date] = _replay_one_date(
            path, read_code, build_fn, policy, axis_error_code, coverage_gate_codes,
        )
    return per_date


def _replay_one_date(
    path: Path,
    read_code: str,
    build_fn,
    policy: dict,
    axis_error_code: dict,
    coverage_gate_codes: set,
) -> dict:
    """Replay exactly one retained snapshot with the unmodified candidate rule.

    Takes only this one file plus the on-disk candidate policy, so it is
    structurally incapable of using a later date's evidence.
    """
    unattempted = {name: None for name in PRR.AXES}
    try:
        source = PRR.read_json(path, read_code)
        result = build_fn(source, policy)
    except PRR.PaperRegimeReferenceError as exc:
        code = _error_code(exc)
        if code in coverage_gate_codes:
            return {
                "outcome": "COVERAGE_GATE_BLOCKED",
                "blocking_code": code,
                "axes": unattempted,
            }
        if code in axis_error_code:
            return {
                "outcome": "SINGLE_AXIS_BLOCKED",
                "blocking_code": code,
                "blocked_axis": axis_error_code[code],
                # Axes evaluated *before* the blocked one in build order are
                # genuinely unknown too — build_us/build_kr stop at the first
                # failure, so we cannot claim they would have succeeded.  Only
                # the specific blocked axis gets an attributable reason; the
                # rest are honestly unattempted.
                "axes": unattempted,
            }
        return {
            "outcome": "UNREADABLE_SOURCE",
            "blocking_code": code,
            "axes": unattempted,
        }
    except Exception as exc:  # noqa: BLE001 — deliberate per-date containment
        # build_us/build_kr only guarantee a PaperRegimeReferenceError for the
        # shapes they explicitly guard; a retained snapshot with a differently
        # shaped or absent sub-object can still surface a raw KeyError/TypeError
        # from the candidate rule itself.  One such day must degrade into "this
        # day is not replayable" evidence, never abort the readiness report for
        # every other retained day.  Only the exception *type* is recorded —
        # never its message — so no source content can leak into public
        # evidence, and the recorded code stays deterministic across reruns.
        return {
            "outcome": "UNSUPPORTED_SOURCE_SHAPE",
            "blocking_code": f"SOURCE_SHAPE_UNSUPPORTED_{type(exc).__name__}",
            "axes": unattempted,
        }
    return {
        "outcome": "OBSERVED",
        "blocking_code": None,
        "axes": {row["axis"]: row for row in result["axes"]},
        "candidate_regime": result["paper_reference"]["candidate_regime"],
    }


def _summarize_axis(axis_name: str, per_date: dict, dates: list[str]) -> dict:
    observed = []  # (date, direction, observed_value)
    blocked_this_axis = []
    not_attempted = []
    blocking_codes: dict[str, int] = {}
    for date in dates:
        entry = per_date[date]
        row = entry["axes"].get(axis_name)
        if entry["outcome"] == "OBSERVED" and row is not None:
            observed.append((date, row["direction"], row["observed_value"]))
            continue
        if entry["outcome"] == "SINGLE_AXIS_BLOCKED" and entry.get("blocked_axis") == axis_name:
            blocked_this_axis.append(date)
        else:
            not_attempted.append(date)
        code = entry.get("blocking_code")
        if code:
            blocking_codes[code] = blocking_codes.get(code, 0) + 1

    directions = [d for _, d, _ in observed]
    values = [v for _, _, v in observed]
    distinct_values = {canonical_json(v) for v in values}
    transitions = sum(1 for a, b in zip(directions, directions[1:]) if a != b)
    stale_repeats = sum(1 for a, b in zip(values, values[1:]) if canonical_json(a) == canonical_json(b))

    total = len(dates)
    n_observed = len(observed)
    n_blocked = len(blocked_this_axis)
    n_not_attempted = len(not_attempted)
    assert n_observed + n_blocked + n_not_attempted == total

    coverage_met = (
        n_observed >= MIN_OBSERVED_DATES_FOR_COMPUTABLE
        and n_observed * MIN_OBSERVED_COVERAGE_FRACTION_DEN
        >= total * MIN_OBSERVED_COVERAGE_FRACTION_NUM
    )
    if total == 0:
        status = "NOT_COMPUTABLE"
        reason = "NO_RETAINED_SOURCE_DATES"
    elif n_observed == 0:
        status = "NOT_COMPUTABLE"
        reason = "RETAINED_DATES_EXIST_BUT_AXIS_NEVER_OBSERVED"
    elif n_blocked > 0 or not coverage_met:
        status = "PARTIAL_HISTORY"
        reason = "OBSERVED_BUT_BELOW_EVIDENCE_VOLUME_RULE_OR_AXIS_SPECIFIC_BLOCK"
    else:
        status = "COMPUTABLE_NOW"
        reason = "OBSERVED_ON_ALL_ATTEMPTABLE_RETAINED_DATES_MEETING_VOLUME_RULE"

    return {
        "axis": axis_name,
        "replay_status": status,
        "replay_status_reason": reason,
        "dates_discovered": total,
        "dates_observed": n_observed,
        "dates_blocked_this_axis": n_blocked,
        "dates_not_attempted": n_not_attempted,
        "coverage_ratio": f"{n_observed}/{total}" if total else "0/0",
        "distinct_observed_value_count": len(distinct_values),
        "distinct_directions": sorted(set(directions)),
        "state_transition_count": transitions,
        "stale_repeat_count": stale_repeats,
        "blocking_reason_codes": dict(sorted(blocking_codes.items())),
        "observations": [
            {"as_of_date": date, "direction": direction, "observed_value": value}
            for date, direction, value in observed
        ],
    }


def _retained_source_sha256(dates: list[str], source_dir: Path, filename: str) -> dict:
    """Hash each retained snapshot exactly as it was found on disk.

    This pins the report to the specific historical bytes it replayed, so a
    later reader can prove the evidence was derived from already-retained
    files rather than anything regenerated or backfilled by this run.
    """
    return {date: file_sha256(source_dir / date / filename) for date in dates}


def _load_policy(root: Path) -> dict:
    policy_path = root / "config" / "paper_regime_reference_policy_v1.json"
    policy = PRR.read_json(policy_path, "POLICY_INVALID")
    if (
        policy.get("contract_version") != "paper_regime_reference_policy/v1"
        or policy.get("status") != "PM_BASELINE_CANDIDATE_NOT_CIO_RATIFIED_SENSOR_POLICY"
    ):
        fail(
            "POLICY_STATUS_CHANGED",
            "candidate policy is no longer PM_BASELINE_CANDIDATE_NOT_CIO_RATIFIED_SENSOR_POLICY;"
            " this module must not be used to imply ratification of a different state",
        )
    return policy


def build_report(root: Path = ROOT) -> dict:
    root = Path(root).resolve()
    policy = _load_policy(root)
    policy_path = root / "config" / "paper_regime_reference_policy_v1.json"

    us_dir = root / US_SOURCE_ROOT
    kr_dir = root / KR_SOURCE_ROOT
    us_dates = discover_dates(us_dir, US_SOURCE_FILENAME)
    kr_dates = discover_dates(kr_dir, KR_SOURCE_FILENAME)

    us_per_date = _replay_market(
        us_dates, us_dir, US_SOURCE_FILENAME, "US_SOURCE_UNREADABLE",
        PRR.build_us, policy, US_AXIS_ERROR_CODE, US_COVERAGE_GATE_CODES,
    )
    kr_per_date = _replay_market(
        kr_dates, kr_dir, KR_SOURCE_FILENAME, "KR_SOURCE_UNREADABLE",
        PRR.build_kr, policy, KR_AXIS_ERROR_CODE, KR_COVERAGE_GATE_CODES,
    )

    us_axes = {name: _summarize_axis(name, us_per_date, us_dates) for name in PRR.AXES}
    kr_axes = {name: _summarize_axis(name, kr_per_date, kr_dates) for name in PRR.AXES}

    def market_level(per_date: dict, dates: list[str]) -> dict:
        observed_dates = [d for d in dates if per_date[d]["outcome"] == "OBSERVED"]
        regimes = [per_date[d]["candidate_regime"] for d in observed_dates]
        return {
            "dates_discovered": len(dates),
            "dates_fully_observed": len(observed_dates),
            "candidate_regime_transitions": sum(
                1 for a, b in zip(regimes, regimes[1:]) if a != b
            ),
            "candidate_regime_distinct_values": sorted(set(regimes)),
            "per_date_outcome": {d: per_date[d]["outcome"] for d in dates},
        }

    report = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "wbs": "P1-COM-05",
        "base_policy": {
            "path": "config/paper_regime_reference_policy_v1.json",
            "sha256": file_sha256(policy_path),
            "status": policy["status"],
        },
        "markets": {
            "US": {
                "source_root": US_SOURCE_ROOT,
                "source_file": US_SOURCE_FILENAME,
                "dates_discovered": us_dates,
                "retained_source_sha256": _retained_source_sha256(
                    us_dates, us_dir, US_SOURCE_FILENAME
                ),
                "market_level": market_level(us_per_date, us_dates),
                "axes": us_axes,
            },
            "KR": {
                "source_root": KR_SOURCE_ROOT,
                "source_file": KR_SOURCE_FILENAME,
                "dates_discovered": kr_dates,
                "retained_source_sha256": _retained_source_sha256(
                    kr_dates, kr_dir, KR_SOURCE_FILENAME
                ),
                "market_level": market_level(kr_per_date, kr_dates),
                "axes": kr_axes,
            },
        },
        "pit_replay": {
            # Structural facts about *how* this report was produced.  Each is
            # enforced by test/test_normalization_replay_readiness.py rather
            # than merely asserted here.
            "each_date_replayed_independently": True,
            "future_dates_used_in_any_date_evaluation": False,
            "retained_sources_mutated_by_this_module": False,
            "candidate_rule_source": "regime/paper_regime_reference.py::build_us,build_kr",
            "candidate_rule_modified_by_this_module": False,
            "statement": (
                "Every axis observation is the unmodified candidate rule applied to a"
                " single already-retained dated snapshot plus the on-disk candidate"
                " policy. No later date, no outcome label, and no threshold tuning"
                " enters an earlier date's evaluation."
            ),
        },
        "authority": {
            "replay_readiness_evidence_authorized": True,
            "sensor_normalization_ratification_authorized": False,
            "registry_promotion_authorized": False,
            "ttl_ratification_authorized": False,
            "pit_replay_acceptance_authorized": False,
            "runtime_regime_wiring_authorized": False,
            "strategy_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "capital_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
            "real_authorized": False,
        },
    }
    report["payload_sha256"] = payload_sha256(report)
    return report


def validate_report(value: dict, root: Path = ROOT) -> dict:
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("REPORT_SCHEMA_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None or payload_sha256(unsigned) != claimed:
        fail("REPORT_SHA_INVALID")
    expected = build_report(root)
    if value != expected:
        fail("REPORT_REDERIVATION_MISMATCH")
    return copy.deepcopy(value)


def write_report(report: dict, root: Path = ROOT) -> tuple[Path, Path]:
    root = Path(root).resolve()
    generated_dates = report["markets"]["US"]["dates_discovered"] + report["markets"]["KR"]["dates_discovered"]
    evidence_date = max(generated_dates) if generated_dates else "no-retained-evidence"
    evidence = (
        root / "evidence" / "regime" / "normalization_replay_readiness" / evidence_date
        / report["payload_sha256"] / "report.json"
    )
    latest = root / "data" / "latest_normalization_replay_readiness.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if evidence.exists() and evidence.read_text(encoding="utf-8") != text:
        fail("APPEND_ONLY_EVIDENCE_CONFLICT")
    _atomic_write(evidence, text)
    _atomic_write(latest, text)
    return evidence, latest


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        value = json.loads(args.verify.read_text(encoding="utf-8"))
        validate_report(value)
        print("PASS_NORMALIZATION_REPLAY_READINESS_VERIFIED")
        return 0
    report = build_report()
    if args.write:
        evidence, latest = write_report(report)
        print(
            json.dumps(
                {
                    "evidence": str(evidence.relative_to(ROOT)),
                    "latest": str(latest.relative_to(ROOT)),
                    "payload_sha256": report["payload_sha256"],
                },
                ensure_ascii=False, sort_keys=True,
            )
        )
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
