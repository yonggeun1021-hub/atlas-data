#!/usr/bin/env python3
"""P1-COM-05 KR 5-axis historical replay population — SHADOW only, NOT NATURAL.

CIO mandate (2026-09-04, "BUILD_KR_5_AXIS_HISTORICAL_REPLAY_POPULATION"): for a
caller-supplied list of historical KR trading dates, reconstruct the same
TREND/BREADTH/RISK_VOL/LIQUIDITY/LEADERSHIP five-axis official-KRX observation
that ``.github/scripts/korea_market_signals.py`` produces for "today", plus the
existing candidate normalization result from ``regime/paper_regime_reference.py``,
so the KR market can be replayed on already-designated historical trading days.

This module invents nothing new:

* Session fetch / pairing / packet construction reuse
  ``.github/scripts/korea_market_signals.py`` unmodified — ``load_contract``,
  ``discover_session_pair``, ``build_packet`` are imported and applied exactly
  as written today.  This file never reimplements KRX request/parse/aggregate
  semantics.
* Candidate normalization reuses ``regime.paper_regime_reference.build_kr``
  unmodified — no new threshold, scoring, or Regime policy is defined here.

Historical replay evidence != NATURAL evidence:

* Input dates are exactly and only what the caller supplies via ``--date``.
  This module never selects bull/bear/sideways/stress episodes on its own —
  regime-episode selection is a separate CIO policy gate.
* Every record in the population is tagged
  ``evidence_class = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"`` and every
  authority flag in the population stays ``false`` except the one read-only
  "this is shadow historical-replay evidence" marker.
* This module refuses to write its output anywhere inside this repository
  checkout — not the NATURAL ``data/observations/`` path, not any other
  tracked path.  The only accepted destinations are an external ``--out``
  path outside the checkout, or (when ``--out`` is omitted) a private
  system-temp file whose path is printed and never committed.

Point-in-time integrity is structural, not merely asserted: each requested
date is resolved and replayed independently via
``korea_market_signals.discover_session_pair(anchor=<that date>)``, which only
ever walks *backward* in calendar time from the requested date, so no session
after the requested date can ever be used and no other requested date's
outcome can influence this one.  A date this module cannot safely resolve
(malformed input, KRX source missing/incomplete, one axis unresolvable, or an
unrecognized retained/response shape) is recorded as one ``BLOCKED`` entry —
by an attributable code only, never by a leaked raw message — and never
aborts the rest of the population.
"""

from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import tempfile
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from regime import paper_regime_reference as PRR  # noqa: E402


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_IMPORT_FAILED:{path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The daily collector lives under `.github/scripts/`, which is not an
# importable Python package — this dynamic load is the same technique
# market_judgement/krx_market_judgement.py and
# regime/replay_population_readiness.py already use to reuse it unmodified.
KMS = _load_module(
    "atlas_kr_historical_replay_korea_market_signals",
    ROOT / ".github" / "scripts" / "korea_market_signals.py",
)


SCHEMA_VERSION = "regime_kr_historical_replay_population/v1"
MODE = "SHADOW_HISTORICAL_REPLAY_NOT_NATURAL"
EVIDENCE_CLASS = "HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class ReplayPopulationError(ValueError):
    """A requested historical KR replay population cannot be safely built."""


def fail(code: str, detail: str = "") -> None:
    raise ReplayPopulationError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ReplayPopulationError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise ReplayPopulationError(f"SOURCE_MISSING:{path}") from exc


def _load_candidate_policy() -> dict:
    policy = PRR.read_json(PRR.POLICY_PATH, "POLICY_INVALID")
    if policy.get("contract_version") != "paper_regime_reference_policy/v1":
        fail("POLICY_INVALID", "contract_version")
    return policy


def _parse_requested_date(value: str) -> dt.date:
    if not isinstance(value, str) or KMS.DATE10.fullmatch(value) is None:
        fail("REQUESTED_DATE_FORMAT_INVALID")
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise ReplayPopulationError("REQUESTED_DATE_CALENDAR_INVALID") from exc


def _blocked_record(requested_date: str, failure_reason: str) -> dict:
    return {
        "requested_date": requested_date,
        "status": "BLOCKED",
        "evidence_class": EVIDENCE_CLASS,
        "effective_trading_date": None,
        "previous_trading_date": None,
        "source": None,
        "five_axis": None,
        "candidate_normalized_result": None,
        "source_hashes": None,
        "failure_reason": failure_reason,
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "session_dates_used": [],
            "any_session_date_after_requested_date": False,
            "other_requested_dates_consulted": False,
        },
    }


def _observed_record(
    requested_date: str, previous: dict, current: dict, packet: dict, normalized: dict, contract: dict,
) -> dict:
    return {
        "requested_date": requested_date,
        "status": "OBSERVED",
        "evidence_class": EVIDENCE_CLASS,
        "effective_trading_date": packet["as_of_date"],
        "previous_trading_date": packet["previous_date"],
        "source": {
            "contract_version": contract["contract_version"],
            "source_name": contract["source_name"],
            "source_tier": contract["source_tier"],
        },
        "five_axis": {
            "status": packet["status"],
            "coverage": copy.deepcopy(packet["coverage"]),
            "axes": copy.deepcopy(packet["axes"]),
        },
        "candidate_normalized_result": copy.deepcopy(normalized),
        "source_hashes": {
            "packet_payload_sha256": packet["payload_sha256"],
            "requests": copy.deepcopy(packet["source"]["requests"]),
        },
        "failure_reason": None,
        "no_lookahead_attestation": {
            "anchor_requested_date": requested_date,
            "session_dates_used": [previous["date"], current["date"]],
            "any_session_date_after_requested_date": False,
            "other_requested_dates_consulted": False,
        },
    }


def _replay_one_requested_date(
    auth_key: str, requested_date: str, *, opener, contract: dict, policy: dict,
) -> dict:
    """Resolve and replay exactly one caller-supplied historical date.

    Takes only this one requested date plus the on-disk contract/candidate
    policy; ``discover_session_pair`` only ever walks backward from the
    anchor, so this call is structurally incapable of consulting a session
    after ``requested_date`` or the outcome of any other requested date.
    """
    try:
        anchor = _parse_requested_date(requested_date)
        previous, current = KMS.discover_session_pair(
            auth_key, anchor=anchor, opener=opener, contract=contract,
        )
        current_date = dt.datetime.strptime(current["date"], "%Y%m%d").date()
        if previous["date"] >= current["date"] or current_date > anchor:
            fail("REPLAY_LOOKAHEAD_VIOLATION")
        packet = KMS.build_packet(previous, current, contract)
        normalized = PRR.build_kr(packet, policy)
    except (KMS.KoreaMarketSignalsError, PRR.PaperRegimeReferenceError, ReplayPopulationError) as exc:
        return _blocked_record(requested_date, str(exc))
    except Exception as exc:  # noqa: BLE001 — deliberate per-date containment,
        # mirrors regime/normalization_replay_readiness.py: one date with an
        # unrecognized shape degrades to "this date is not replayable"
        # evidence instead of aborting the whole population. Only the
        # exception *type* is recorded, never its message.
        return _blocked_record(requested_date, f"UNSUPPORTED_REPLAY_SHAPE_{type(exc).__name__}")
    return _observed_record(requested_date, previous, current, packet, normalized, contract)


def build_population(
    auth_key: str, requested_dates: list[str], *, opener=urlopen,
) -> dict:
    contract = KMS.load_contract()
    policy = _load_candidate_policy()
    # Deterministic regardless of caller ordering/duplication: sort the
    # distinct requested strings so a shuffled --date list reproduces the
    # exact same record order every time.
    unique_dates = sorted({str(value) for value in requested_dates})
    if not unique_dates:
        fail("NO_DATES_REQUESTED")
    records = [
        _replay_one_requested_date(auth_key, date, opener=opener, contract=contract, policy=policy)
        for date in unique_dates
    ]
    population = {
        "schema_version": SCHEMA_VERSION,
        "mode": MODE,
        "wbs": "P1-COM-05",
        "evidence_class": EVIDENCE_CLASS,
        "requested_dates": unique_dates,
        "source_contract": {
            "path": "config/korea_market_signals_contract.json",
            "sha256": file_sha256(KMS.CONTRACT_PATH),
            "contract_version": contract["contract_version"],
        },
        "candidate_policy": {
            "path": "config/paper_regime_reference_policy_v1.json",
            "sha256": file_sha256(PRR.POLICY_PATH),
            "status": policy.get("status"),
        },
        "candidate_rule_source": "regime/paper_regime_reference.py::build_kr",
        "records": records,
        "authority": {
            "historical_replay_evidence_authorized": True,
            "natural_promotion_authorized": False,
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
    population["payload_sha256"] = payload_sha256(population)
    return population


def validate_population(value: dict) -> dict:
    """Integrity/shape check only — deliberately never re-derives.

    Re-derivation would require re-issuing live KRX requests for every
    replayed date. Per the CIO mandate, an actual KRX network probe must
    stay separate from implementation verification and must never become a
    CI prerequisite, so ``--verify`` here checks the hash and the
    SHADOW/never-NATURAL shape only.
    """
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        fail("POPULATION_SCHEMA_INVALID")
    unsigned = copy.deepcopy(value)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None or payload_sha256(unsigned) != claimed:
        fail("POPULATION_SHA_INVALID")
    if value.get("mode") != MODE or value.get("evidence_class") != EVIDENCE_CLASS:
        fail("POPULATION_MODE_INVALID")
    if value.get("requested_dates") != sorted(set(value.get("requested_dates", []))):
        fail("POPULATION_DATE_ORDER_INVALID")
    for record in value.get("records", []):
        if record.get("evidence_class") != EVIDENCE_CLASS:
            fail("RECORD_EVIDENCE_CLASS_INVALID")
        if record.get("status") not in ("OBSERVED", "BLOCKED"):
            fail("RECORD_STATUS_INVALID")
    for key, allowed in value.get("authority", {}).items():
        if key == "historical_replay_evidence_authorized":
            if allowed is not True:
                fail("POPULATION_AUTHORITY_INVALID", key)
        elif allowed is not False:
            fail("POPULATION_AUTHORITY_INVALID", key)
    return copy.deepcopy(value)


def _forbid_tracked_output(root: Path, path: Path) -> None:
    """Fail closed if ``path`` resolves inside this repository checkout.

    Historical replay evidence must never land in any tracked location —
    NATURAL ``data/observations/`` included — so the guard is a blanket
    "not inside the checkout at all", not a NATURAL-path denylist that a new
    tracked directory could slip past.
    """
    root_resolved = Path(root).resolve()
    path_resolved = Path(path).resolve()
    try:
        path_resolved.relative_to(root_resolved)
    except ValueError:
        return
    fail("TRACKED_OUTPUT_FORBIDDEN", str(path_resolved))


def _atomic_write(path: Path, text: str) -> None:
    path = Path(path)
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


def write_population(population: dict, out_path: Path, *, root: Path = ROOT) -> Path:
    _forbid_tracked_output(root, out_path)
    text = json.dumps(population, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    _atomic_write(Path(out_path), text)
    return Path(out_path)


def _default_temp_out() -> Path:
    fd, name = tempfile.mkstemp(prefix="kr_historical_replay_population.", suffix=".json")
    os.close(fd)
    return Path(name)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--date", action="append", default=[], dest="dates",
        help="Historical KR trading date, YYYY-MM-DD. Repeatable. No date is ever selected automatically.",
    )
    parser.add_argument(
        "--out", type=Path, default=None,
        help="External output path (must be outside this checkout). Defaults to a private system-temp file.",
    )
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()

    if args.verify:
        value = json.loads(Path(args.verify).read_text(encoding="utf-8"))
        validate_population(value)
        print(f"PASS_KR_HISTORICAL_REPLAY_POPULATION_VERIFIED:{value['payload_sha256']}")
        return 0

    if not args.dates:
        fail("NO_DATES_REQUESTED")

    population = build_population(os.environ.get("KRX_API_KEY", ""), args.dates)
    out_path = args.out if args.out is not None else _default_temp_out()
    write_population(population, out_path)
    observed = sum(1 for r in population["records"] if r["status"] == "OBSERVED")
    print(json.dumps(
        {
            "out": str(out_path),
            "payload_sha256": population["payload_sha256"],
            "records": len(population["records"]),
            "observed": observed,
            "blocked": len(population["records"]) - observed,
        },
        ensure_ascii=False, sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
