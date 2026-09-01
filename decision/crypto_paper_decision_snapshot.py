#!/usr/bin/env python3
"""Crypto PAPER decision-packet composition (wires P1-CR-08 + P5-08 + P5-09
onto the tail of the existing P9-06 30-minute Upbit realtime capture run).

This module is a **composition** layer only. It invents no new criterion,
threshold, candle interval, cooldown, or severity vocabulary -- it reads
already-committed evidence produced by four independently-ratified-or-
honestly-unratified upstream modules and calls their own pure functions
verbatim:

* ``universe/upbit_tradeable_universe.py``      (P3-12) -- the committed
  Upbit KRW tradeable-universe classification packet, read from
  ``data/observations/upbit_tradeable_universe/<date>/packet.json``.
* ``microstructure/upbit_market_evidence.py`` / ``upbit_candle_finalization.py``
  (P4-07) -- the committed finalized-candle / spread / depth / slippage
  evidence packet, read from
  ``data/observations/upbit_market_evidence/<date>/packet.json``.
* the just-captured P9-06 bounded WebSocket realtime evidence, read from
  ``evidence/crypto/upbit/realtime/<date>/run_NNN.json`` -- used ONLY for
  freshness/quote-state reporting; it is never an argument to P5-08/P5-09's
  own derivation math.
* ``regime/live_axis_adapter.py`` + ``regime/output_contract.py`` (P1-CR-08)
  -- DEFINED/UNDEFINED axis evidence only, never an interpreted RISK_ON/
  NEUTRAL/RISK_OFF/STRESS value (the runtime contract authorizes only
  ``UNKNOWN`` for the aggregate today).
* ``regime/crypto_live_component_registry.py`` -- exact public natural
  component rows bound to their retained download cutoff and directory
  fingerprint. It can define evidence presence only; it cannot interpret an
  axis or authorize a decision/action.
* ``universe/crypto_candidate_promotion.py`` (P5-08) and
  ``universe/crypto_paper_buy_eligibility.py`` (P5-09) -- run verbatim,
  unmodified, over the packets above.

Explicitly OUT OF SCOPE (per the CIO's 2026-08-29 Crypto Continuous Briefing
v1 addendum, Notion ``3c79f2d73c848160a51de6931256dee4``, and the Crypto
policy canon, Notion ``3ca9f2d73c84810a9ee7c7125e1dabd0`` -- both re-read at
the start of this work): ``CRYPTO_CONTINUOUS_EVENT`` / Continuous Briefing /
alerting, any trigger threshold, candle interval, severity vocabulary,
cooldown, or notification SLO. Producing this PAPER decision snapshot is not
a declaration that Continuous Briefing has been built
(CIO's own words: "PAPER decision snapshot을 만들었다고 CRYPTO_CONTINUOUS_
EVENT까지 완성했다고 선언하지 않는다").

Freshness discipline: if the P3-12 packet is missing, stale beyond
``config/upbit_tradeable_universe_policy.json``'s own
``max_capture_age_hours`` bound (the same bound P3-12 itself already uses
for its own capture-freshness gate -- reused here, not invented), or the
P4-07 evidence used does not share P3-12's own snapshot date ("mixed
generation"), this module never silently proceeds as if fresh: the funnel
either does not run at all (P3-12 missing) or P5-08 itself fails closed
(regime/P3-12 date mismatch -- P5-08's own existing invariant, not a new
one), and any candidate state that would otherwise be actionable
(``FOCUSED_REVIEW`` un-reviewed / ``PAPER_BUY_ELIGIBLE``) is capped to
``WAIT`` with an explicit ``freshness_capped`` reason -- this composition
module's own belt-and-suspenders safety net on top of the upstream modules'
own gates, never a relaxation of them.

An existing file is not automatically fresh. Empty P4-07 packet maps and
empty P9-06 subscriptions/messages are MISSING; unratified freshness
policies remain UNKNOWN; per-component STALE remains STALE. Every retained
source's content hash and semantic summary are verified before use. The
workflow supplies the decision timestamp after bounded capture ends, and a
failed current capture never silently falls back to an earlier run.

Regime authority today: ``regime/output_contract.py``'s runtime contract
authorizes only ``regime: "UNKNOWN"`` for every market
(``runtime_authorized_regimes == ["UNKNOWN"]``). P5-08's own ``evaluate_regime``
therefore can never return anything but criterion status ``UNKNOWN`` for
every candidate, which caps every candidate at ``WATCH`` at best -- Every
market landing at WATCH/WAIT with ``orderDraft=null`` and
``paper_ready_count == 0`` today is the correct, honest output, not a bug to
route around.

Determinism: ``generation_id`` and every derivation field are pure functions
of already-committed input bytes (source commit, upstream payload hashes) --
never of wall-clock. ``generated_at``/``capture_date``/``capture_hhmm`` DO
carry this run's post-capture decision-snapshot instant, but only as *recorded
evidence of what inputs happened to exist at build time* -- they are never
folded into ``generation_id``. Duplicate-packet guard: mirrors
``.github/scripts/upbit_universe_populate.py::populate``'s exact
"verified_existing vs populated" idempotency discipline -- the on-disk path
is itself content-addressed by ``generation_id``, so a second build of the
same slot with the exact same input bytes verifies-not-duplicates; a second
build of the same slot with genuinely different input bytes lands under a
different ``generation_id`` subdirectory, never silently overwriting.

Explicit time basis (CIO-directed hardening, additive/backward-compatible):
``capture_date``/``capture_hhmm`` -- and therefore the
``evidence/crypto_paper_decision/<capture_date>/<capture_hhmm>/...`` storage
path they name -- are UTC, not KST. A reader looking only at the path or the
packet (never the source) cannot be expected to know this, so every packet
also carries ``captured_at_utc`` (an explicit alias of ``generated_at``),
``captured_at_kst``/``operational_date_kst`` (the same instant read as a
Korean investor would), a literal ``path_time_basis`` string documenting the
UTC-not-KST fact in-band, ``scheduled_for`` (the ``*/30``-minute UTC cron
slot this run was triggered for), ``started_at`` (the workflow runner's
observed start instant), and ``completed_at`` (an explicitly-named alias of
``generated_at`` -- this module's build-wall-clock instant is definitionally
the same value). None of these six fields are folded into ``generation_id``
either, for the same "never wall-clock" reason as ``generated_at`` itself.
These fields are purely additive: older committed packets (e.g.
``evidence/crypto_paper_decision/2026-08-29/0504/...``) that predate them
remain readable via ``find_previous_packet``'s ``captured_at_utc`` ->
``generated_at`` fallback.
"""
from __future__ import annotations

import argparse
import copy
import datetime as dt
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

UNIVERSE_DATA_ROOT = ROOT / "data" / "observations" / "upbit_tradeable_universe"
MARKET_EVIDENCE_DATA_ROOT = ROOT / "data" / "observations" / "upbit_market_evidence"
REALTIME_EVIDENCE_ROOT = ROOT / "evidence" / "crypto" / "upbit" / "realtime"
OUTPUT_ROOT = ROOT / "evidence" / "crypto_paper_decision"

OUTPUT_SCHEMA_VERSION = "crypto_paper_decision_snapshot_packet/1"

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
UTC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
P4_SNAPSHOT_KEY_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})-p3-(?P<record_hash_prefix>[0-9a-f]{16})$"
)
HHMM_RE = re.compile(r"^\d{4}$")
# Same idempotency-key token shape as decision/action_order_idempotency.py
# (P9-04) and universe/crypto_paper_buy_eligibility.py::compute_duplicate_guard_key
# (P5-09) -- reused verbatim, not reinvented.
TOKEN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_.:-]{2,127}$")

# Same fixed +09:00 KST tzinfo construction already used elsewhere in this
# repository (collectors/krx.py::KST, identity/candidate_identity_authority_
# proposal.py, portfolio_risk/kis_valuation_semantic_review.py) -- reused
# verbatim, not reinvented. Korea has no daylight-saving offset, so a fixed
# +09:00 offset (not a ZoneInfo/DST-aware zone) is this repo's own existing
# convention for this exact purpose.
KST = dt.timezone(dt.timedelta(hours=9))

# Explicit, self-documenting contract note embedded in every packet: a
# reader who only has this JSON (no source access) must not have to guess
# that capture_date/capture_hhmm -- and therefore the evidence storage path
# they name -- are UTC, not KST. See captured_at_kst/operational_date_kst
# below for the KST-side values a Korean-investor-facing reader actually
# wants.
PATH_TIME_BASIS = (
    "capture_date and capture_hhmm (and the evidence path they name) are UTC, not KST"
)
TIME_BASIS_FIELDS = {
    "captured_at_utc", "captured_at_kst", "operational_date_kst",
    "path_time_basis", "scheduled_for", "started_at", "completed_at",
}

# Matches this workflow's own currently-committed schedule --
# .github/workflows/upbit-realtime-capture.yml's `cron: "*/30 * * * *"` --
# reused here, not reinvented (same discipline as every other "reused
# verbatim" constant in this module's docstring). This module does not
# attempt general cron parsing; if the workflow's own cron interval ever
# changes, this constant must change with it in the same PR.
SCHEDULED_SLOT_MINUTES = 30


def _floor_to_schedule_slot(observed_dt: dt.datetime, slot_minutes: int = SCHEDULED_SLOT_MINUTES) -> dt.datetime:
    """The nearest ``*/slot_minutes`` UTC boundary at or before ``observed_dt``."""
    floored_minute = (observed_dt.minute // slot_minutes) * slot_minutes
    return observed_dt.replace(minute=floored_minute, second=0, microsecond=0)


def _load(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise CryptoPaperDecisionSnapshotError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CryptoPaperDecisionSnapshotError(ValueError):
    """Fail-closed crypto PAPER decision-packet composition violation."""


UNIVERSE = _load("crypto_paper_decision_snapshot_universe", "universe/upbit_tradeable_universe.py")
PROMOTION = _load("crypto_paper_decision_snapshot_promotion", "universe/crypto_candidate_promotion.py")
ELIGIBILITY = _load("crypto_paper_decision_snapshot_eligibility", "universe/crypto_paper_buy_eligibility.py")
MARKET_EVIDENCE = _load("crypto_paper_decision_snapshot_market_evidence", "microstructure/upbit_market_evidence.py")
CANDLE_FINALIZATION = _load(
    "crypto_paper_decision_snapshot_candle_finalization", "microstructure/upbit_candle_finalization.py"
)
REALTIME_GATE = _load("crypto_paper_decision_snapshot_realtime_gate", "realtime/upbit_realtime_gate.py")
REGIME_OUTPUT = _load("crypto_paper_decision_snapshot_regime_output", "regime/output_contract.py")
LIVE_AXIS = _load("crypto_paper_decision_snapshot_live_axis", "regime/live_axis_adapter.py")
LIVE_COMPONENT_REGISTRY = _load(
    "crypto_paper_decision_snapshot_live_component_registry",
    "regime/crypto_live_component_registry.py",
)


def canonical_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def payload_sha256(value) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        raise CryptoPaperDecisionSnapshotError(f"FILE_HASH_FAILED:{path}:{exc}") from exc


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CryptoPaperDecisionSnapshotError(f"JSON_READ_FAILED:{path}:{exc}") from exc


def _relpath(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT.resolve()))
    except ValueError:
        return str(path)


def _parse_utc(value: object, label: str) -> dt.datetime:
    if not isinstance(value, str) or not UTC_RE.fullmatch(value):
        raise CryptoPaperDecisionSnapshotError(f"UTC_INVALID:{label}")
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def _validate_embedded_hash(record: dict, field: str, label: str) -> None:
    if not isinstance(record, dict):
        raise CryptoPaperDecisionSnapshotError(f"SOURCE_RECORD_INVALID:{label}")
    claimed = record.get(field)
    if not isinstance(claimed, str) or not SHA256_RE.fullmatch(claimed):
        raise CryptoPaperDecisionSnapshotError(f"SOURCE_HASH_INVALID:{label}")
    unsigned = copy.deepcopy(record)
    unsigned.pop(field)
    if payload_sha256(unsigned) != claimed:
        raise CryptoPaperDecisionSnapshotError(f"SOURCE_HASH_MISMATCH:{label}")


def _validate_universe_entry(
    entry: dict | None,
    *,
    not_after: dt.datetime,
) -> None:
    if entry is None:
        return
    if (
        not isinstance(not_after, dt.datetime)
        or not_after.tzinfo is None
        or not_after.utcoffset() is None
    ):
        raise CryptoPaperDecisionSnapshotError("UNIVERSE_NOT_AFTER_MUST_BE_TIMEZONE_AWARE")
    not_after = not_after.astimezone(dt.timezone.utc)
    record = entry.get("record")
    if (
        not isinstance(record, dict)
        or record.get("schema_version") != "upbit_universe_population/1"
        or record.get("snapshot_date") != entry.get("date")
        or record.get("packet") != entry.get("packet")
    ):
        raise CryptoPaperDecisionSnapshotError("UNIVERSE_SOURCE_RECORD_INVALID")
    _validate_embedded_hash(record, "payload_sha256", "upbit_universe_population")
    manifest_path = entry.get("transition_manifest_path")
    transition_selected = manifest_path is not None
    if not transition_selected and (
        entry.get("transition_retained") is True
        or "/transitions/" in Path(entry["path"]).as_posix()
    ):
        raise CryptoPaperDecisionSnapshotError("UNIVERSE_TRANSITION_PROVENANCE_MISSING")
    if transition_selected:
        try:
            if entry.get("transition_retained") is True:
                validated = UNIVERSE.validate_retained_same_vintage_transition(
                    manifest_path,
                    source_record_path=entry.get("source_path"),
                    successor_record_path=entry["path"],
                )
            else:
                validated = UNIVERSE.validate_same_vintage_transition(manifest_path)
        except (UNIVERSE.UpbitUniverseError, TypeError) as exc:
            raise CryptoPaperDecisionSnapshotError(
                f"UNIVERSE_TRANSITION_PROVENANCE_INVALID:{exc}"
            ) from exc
        if (
            validated["record"] != record
            or validated["record"]["payload_sha256"] != record.get("payload_sha256")
        ):
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_TRANSITION_SUCCESSOR_MISMATCH")
        available_at = validated.get("transition_available_at")
        if (
            not isinstance(available_at, dt.datetime)
            or available_at.tzinfo is None
            or available_at.utcoffset() is None
        ):
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_TRANSITION_AVAILABLE_AT_INVALID")
        if available_at.astimezone(dt.timezone.utc) > not_after:
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_TRANSITION_NOT_YET_AVAILABLE")


def _validate_market_evidence_entry(entry: dict | None) -> None:
    if entry is None:
        return
    record = entry.get("record")
    schema = record.get("schema_version") if isinstance(record, dict) else None
    if (
        not isinstance(record, dict)
        or schema not in {
            "upbit_microstructure_population/1",
            "upbit_microstructure_population/2",
        }
        or record.get("snapshot_date") != entry.get("date")
        or not isinstance(record.get("packets"), dict)
    ):
        raise CryptoPaperDecisionSnapshotError("MARKET_EVIDENCE_SOURCE_RECORD_INVALID")
    if schema == "upbit_microstructure_population/2":
        snapshot_key = record.get("snapshot_key")
        exact = (
            P4_SNAPSHOT_KEY_RE.fullmatch(snapshot_key)
            if isinstance(snapshot_key, str) else None
        )
        lineage = record.get("universe_lineage") or {}
        record_hash = lineage.get("record_payload_sha256")
        if (
            exact is None
            or exact.group("date") != record.get("snapshot_date")
            or not isinstance(record_hash, str)
            or not SHA256_RE.fullmatch(record_hash)
            or exact.group("record_hash_prefix") != record_hash[:16]
        ):
            raise CryptoPaperDecisionSnapshotError(
                "MARKET_EVIDENCE_SNAPSHOT_KEY_LINEAGE_MISMATCH"
            )
    elif record.get("snapshot_key") not in (None, ""):
        raise CryptoPaperDecisionSnapshotError(
            "MARKET_EVIDENCE_LEGACY_SNAPSHOT_KEY_INVALID"
        )
    _validate_embedded_hash(record, "payload_sha256", "upbit_microstructure_population")
    summary = record.get("summary") or {}
    errors = record.get("errors")
    if (
        summary.get("packet_count") != len(record["packets"])
        or not isinstance(errors, dict)
        or summary.get("error_count") != len(errors)
    ):
        raise CryptoPaperDecisionSnapshotError("MARKET_EVIDENCE_SUMMARY_INVALID")


def _validate_realtime_entry(entry: dict | None) -> None:
    if entry is None:
        return
    record = entry.get("record")
    if not isinstance(record, dict) or set(record) != {
        "schema_version", "transform_version", "auth_required",
        "order_or_withdrawal_endpoints_called", "private_channel_subscribed",
        "run", "source_sha256",
    }:
        raise CryptoPaperDecisionSnapshotError("REALTIME_SOURCE_RECORD_INVALID")
    if (
        record["schema_version"] != "upbit_realtime_capture_run/1"
        or record["transform_version"] != "upbit_realtime_gate/1"
        or record["auth_required"] is not False
        or record["order_or_withdrawal_endpoints_called"] is not False
        or record["private_channel_subscribed"] is not False
    ):
        raise CryptoPaperDecisionSnapshotError("REALTIME_SOURCE_SAFETY_INVALID")
    claimed = record["source_sha256"]
    if not isinstance(claimed, str) or not SHA256_RE.fullmatch(claimed):
        raise CryptoPaperDecisionSnapshotError("REALTIME_SOURCE_HASH_INVALID")
    if payload_sha256(record["run"]) != claimed:
        raise CryptoPaperDecisionSnapshotError("REALTIME_SOURCE_HASH_MISMATCH")
    run = record["run"]
    status = run.get("status") if isinstance(run, dict) else None
    if (
        not isinstance(run, dict)
        or not isinstance(run.get("markets"), list)
        or not isinstance(run.get("message_log"), list)
        or not isinstance(status, dict)
    ):
        raise CryptoPaperDecisionSnapshotError("REALTIME_RUN_INVALID")
    _validate_embedded_hash(status, "payload_sha256", "upbit_realtime_gate_status")
    if status.get("schema_version") != "upbit_realtime_gate_status/1":
        raise CryptoPaperDecisionSnapshotError("REALTIME_STATUS_SCHEMA_INVALID")
    if any(value is not False for value in (status.get("authority") or {}).values()):
        raise CryptoPaperDecisionSnapshotError("REALTIME_STATUS_AUTHORITY_INVALID")


# ---------------------------------------------------------------------------
# Freshness vocabulary -- worst-of aggregation, same discipline as
# universe/crypto_paper_buy_eligibility.py::_worst_of.
# ---------------------------------------------------------------------------

FRESH = "FRESH"
STALE = "STALE"
UNKNOWN = "UNKNOWN"
MISSING = "MISSING"
MIXED_GENERATION = "MIXED_GENERATION"
FRESHNESS_STATUSES = (FRESH, STALE, UNKNOWN, MISSING, MIXED_GENERATION)
_FRESHNESS_SEVERITY = {FRESH: 0, STALE: 1, UNKNOWN: 2, MIXED_GENERATION: 3, MISSING: 4}


def _worst_freshness(statuses) -> str:
    return max(statuses, key=lambda status: _FRESHNESS_SEVERITY[status])


# Actionable states this composition module will never report while
# evidence freshness is degraded -- a belt-and-suspenders cap on top of
# (never a relaxation of) P5-08's/P5-09's own gates. Isolated as a small
# pure function so the "this can never be bypassed" invariant is directly
# unit-testable against every state/freshness combination, independent of
# whether today's Regime-UNKNOWN state can actually reach these states yet.
_ACTIONABLE_STATES = ("FOCUSED_REVIEW", "PAPER_BUY_ELIGIBLE")


def cap_state_for_freshness(state: str, reason: str, overall_freshness: str) -> dict:
    if overall_freshness != FRESH and state in _ACTIONABLE_STATES:
        cap_reason = f"OVERALL_FRESHNESS_NOT_FRESH:{overall_freshness}"
        return {"state": "WAIT", "reason": cap_reason, "capped": True, "cap_reason": cap_reason}
    return {"state": state, "reason": reason, "capped": False, "cap_reason": None}


def _market_evidence_freshness(record: dict) -> tuple[str, str | None]:
    packets = record["packets"]
    if not packets:
        return MISSING, "UPBIT_MARKET_EVIDENCE_PACKETS_EMPTY"
    if record.get("policy_ratified") is not True:
        return UNKNOWN, "UPBIT_MARKET_EVIDENCE_FRESHNESS_POLICY_UNRATIFIED"
    if record.get("errors"):
        return UNKNOWN, "UPBIT_MARKET_EVIDENCE_PARTIAL_ERRORS"
    statuses = []
    for packet in packets.values():
        statuses.extend(
            ((packet.get("candles") or {}).get(timeframe) or {}).get("freshness", {}).get("status")
            for timeframe in CANDLE_FINALIZATION.TIMEFRAMES
        )
        statuses.append((packet.get("trades") or {}).get("freshness", {}).get("status"))
        statuses.append((packet.get("orderbook") or {}).get("freshness", {}).get("status"))
    if any(status not in (FRESH, STALE, UNKNOWN) for status in statuses):
        return UNKNOWN, "UPBIT_MARKET_EVIDENCE_FRESHNESS_MISSING_OR_INVALID"
    if STALE in statuses:
        return STALE, "UPBIT_MARKET_EVIDENCE_COMPONENT_STALE"
    if UNKNOWN in statuses:
        return UNKNOWN, "UPBIT_MARKET_EVIDENCE_COMPONENT_UNKNOWN"
    return FRESH, None


def _realtime_freshness(record: dict) -> tuple[str, str | None]:
    run = record["run"]
    status = run["status"]
    if not run["markets"] or not run["message_log"] or not status.get("markets"):
        return MISSING, "UPBIT_REALTIME_OBSERVATIONS_EMPTY"
    if status.get("connection_state") != "CONNECTED":
        return UNKNOWN, f"UPBIT_REALTIME_CONNECTION_NOT_CONNECTED:{status.get('connection_state')}"
    # The repository currently ships only a proposal for these age bounds;
    # the real P9-01 freshness guard has no ratified CRYPTO policy packet.
    # Preserve the gate's observed label diagnostically, but do not promote
    # it to an actionable FRESH fact.
    proposal = REALTIME_GATE.load_freshness_policy_proposal()
    if proposal.get("approval_status") != "RATIFIED":
        return UNKNOWN, "UPBIT_REALTIME_FRESHNESS_POLICY_UNRATIFIED"
    gate_status = status.get("overall_status")
    if gate_status == FRESH:
        return FRESH, None
    if gate_status == STALE:
        return STALE, "UPBIT_REALTIME_GATE_STATUS_STALE"
    return UNKNOWN, f"UPBIT_REALTIME_GATE_STATUS_NOT_FRESH:{gate_status}"


# ---------------------------------------------------------------------------
# Upstream evidence discovery -- read-only, zero network calls.
# ---------------------------------------------------------------------------

def find_latest_universe_packet(
    data_root: Path = UNIVERSE_DATA_ROOT, *, not_after: dt.datetime | None = None,
):
    """Latest canonical P3 record or exact-validated same-day successor."""
    selection_cutoff = not_after or dt.datetime.now(dt.timezone.utc)
    try:
        return UNIVERSE.find_latest_population_record(
            data_root,
            not_after=selection_cutoff,
        )
    except UNIVERSE.UpbitUniverseError as exc:
        raise CryptoPaperDecisionSnapshotError(
            f"UPBIT_UNIVERSE_TRANSITION_INVALID:{exc}"
        ) from exc


def find_latest_market_evidence_packet(
    data_root: Path = MARKET_EVIDENCE_DATA_ROOT, *, not_after: dt.datetime | None = None,
):
    """Latest verified P4-07 record under ``data_root``, or ``None``.

    P4-07 originally wrote ``YYYY-MM-DD`` directories.  The exact P3-hash
    bridge now writes ``YYYY-MM-DD-p3-<record-hash-prefix>`` so multiple
    immutable same-day universe generations cannot overwrite each other.
    Select by each record's verified internal ``generated_at`` rather than
    by either directory spelling; otherwise a legacy empty date packet can
    silently outrank the current exact-eight packet.
    """
    data_root = Path(data_root)
    if not data_root.is_dir():
        return None
    candidates = []
    for directory in data_root.iterdir():
        if not directory.is_dir():
            continue
        legacy = DATE_RE.fullmatch(directory.name)
        exact = P4_SNAPSHOT_KEY_RE.fullmatch(directory.name)
        if legacy is None and exact is None:
            continue
        path = directory / "packet.json"
        if not path.is_file():
            continue
        record = _read_json(path)
        snapshot_date = record.get("snapshot_date") if isinstance(record, dict) else None
        if not isinstance(snapshot_date, str) or DATE_RE.fullmatch(snapshot_date) is None:
            raise CryptoPaperDecisionSnapshotError(
                f"MARKET_EVIDENCE_SNAPSHOT_DATE_INVALID:{path}"
            )
        if legacy is not None:
            if directory.name != snapshot_date:
                raise CryptoPaperDecisionSnapshotError(
                    f"MARKET_EVIDENCE_PATH_DATE_MISMATCH:{path}"
                )
        else:
            if (
                exact.group("date") != snapshot_date
                or record.get("snapshot_key") != directory.name
            ):
                raise CryptoPaperDecisionSnapshotError(
                    f"MARKET_EVIDENCE_SNAPSHOT_KEY_LINEAGE_MISMATCH:{path}"
                )
        entry = {"date": snapshot_date, "path": path, "record": record}
        _validate_market_evidence_entry(entry)
        generated_at = _parse_utc(record.get("generated_at"), "market_evidence.generated_at")
        if not_after is not None and generated_at > not_after:
            continue
        candidates.append((generated_at, record["payload_sha256"], entry))
    if not candidates:
        return None
    candidates.sort(key=lambda row: (row[0], row[1]))
    latest_at = candidates[-1][0]
    latest = [row for row in candidates if row[0] == latest_at]
    if len({row[1] for row in latest}) != 1:
        raise CryptoPaperDecisionSnapshotError(
            f"MARKET_EVIDENCE_LATEST_TIMESTAMP_AMBIGUOUS:{latest_at.isoformat()}"
        )
    return latest[-1][2]


def find_latest_realtime_run(
    evidence_root: Path = REALTIME_EVIDENCE_ROOT, *, not_after: dt.datetime | None = None,
):
    """The most recently written P9-06 ``run_NNN.json`` across every
    committed date directory -- "the run that just happened in this same
    workflow invocation" when called immediately after the capture step, or
    the most recent prior run for a standalone manual/functional check.
    """
    evidence_root = Path(evidence_root)
    if not evidence_root.is_dir():
        return None
    dates = sorted(p.name for p in evidence_root.iterdir() if p.is_dir() and DATE_RE.fullmatch(p.name))
    for date in reversed(dates):
        runs = sorted((evidence_root / date).glob("run_*.json"))
        for latest in reversed(runs):
            record = _read_json(latest)
            if not_after is not None:
                ended_at = _parse_utc(
                    (record.get("run") or {}).get("ended_at") if isinstance(record, dict) else None,
                    "realtime.ended_at",
                )
                if ended_at > not_after:
                    continue
            return {"date": date, "path": latest, "record": record}
    return None


def find_previous_packet(output_root: Path, before_date: str, before_hhmm: str):
    """The immediately-prior committed packet under ``output_root`` whose own
    internal capture instant genuinely precedes ``(before_date, before_hhmm)``
    -- an honest audit-trail pointer, never a threshold/severity/alert
    judgment (see module docstring).

    Selection is by each candidate packet's own internal
    ``captured_at_utc`` (falling back to ``generated_at`` for older packets
    committed before ``captured_at_utc`` existed as a field -- e.g.
    ``evidence/crypto_paper_decision/2026-08-29/0504/...`` remains readable)
    parsed as a real UTC ``datetime`` and compared chronologically -- never
    by directory-name string comparison alone. A
    candidate whose directory-name-derived ``(date, hhmm)`` disagrees with
    its own internal timestamp-derived ``(date, hhmm)`` is a
    ``TAMPER_OR_DRIFT`` condition: this function never silently trusts the
    directory name in that case. It fails closed on that single candidate by
    excluding it from selection (never promoting an unverified directory
    name to "this is the previous packet"), the same per-row fail-closed
    discipline ``universe/upbit_tradeable_universe.py`` uses for its own
    ``STATE_BLOCKED``/``"IDENTITY_COLLISION"`` rows: the compromised item is
    dropped, evaluation of the remaining candidates continues, and the
    condition is logged rather than raised (this is a read-only audit-trail
    lookup, not the packet's own write path -- an unrelated tampered/drifted
    sibling packet must not block today's honest build).
    """
    output_root = Path(output_root)
    if not output_root.is_dir():
        return None
    before_dt = dt.datetime.strptime(before_date + before_hhmm, "%Y-%m-%d%H%M").replace(tzinfo=dt.timezone.utc)
    candidates = []
    for date_dir in output_root.iterdir():
        if not date_dir.is_dir() or not DATE_RE.fullmatch(date_dir.name):
            continue
        for hhmm_dir in date_dir.iterdir():
            if not hhmm_dir.is_dir() or not HHMM_RE.fullmatch(hhmm_dir.name):
                continue
            for gen_dir in sorted(hhmm_dir.iterdir()):
                packet_path = gen_dir / "packet.json"
                if not packet_path.is_file():
                    continue
                packet = _read_json(packet_path)
                internal_generated_at = packet.get("captured_at_utc") or packet.get("generated_at")
                if not isinstance(internal_generated_at, str) or not UTC_RE.fullmatch(internal_generated_at):
                    print(
                        f"TAMPER_OR_DRIFT:MALFORMED_INTERNAL_TIMESTAMP:{packet_path}",
                        file=sys.stderr,
                    )
                    continue
                internal_date = internal_generated_at[:10]
                internal_hhmm = internal_generated_at[11:13] + internal_generated_at[14:16]
                if (internal_date, internal_hhmm) != (date_dir.name, hhmm_dir.name):
                    print(
                        f"TAMPER_OR_DRIFT:DIRECTORY_NAME_INTERNAL_TIMESTAMP_MISMATCH:"
                        f"path={packet_path}:directory=({date_dir.name},{hhmm_dir.name}):"
                        f"internal=({internal_date},{internal_hhmm})",
                        file=sys.stderr,
                    )
                    continue
                parsed_dt = _parse_utc(internal_generated_at, "previous_packet.captured_at_utc")
                if parsed_dt >= before_dt:
                    continue
                candidates.append((parsed_dt, packet_path, packet))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    _, _, packet = candidates[-1]
    return {
        "generation_id": packet.get("generation_id"),
        "payload_sha256": packet.get("payload_sha256"),
        "funnel_counts": packet.get("funnel_counts"),
    }


def resolve_source_commit(explicit: str | None = None) -> str:
    if explicit is not None:
        if not FULL_SHA_RE.fullmatch(explicit):
            raise CryptoPaperDecisionSnapshotError(f"SOURCE_COMMIT_INVALID:{explicit}")
        return explicit
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=False, text=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    value = completed.stdout.strip()
    if completed.returncode != 0 or not FULL_SHA_RE.fullmatch(value):
        raise CryptoPaperDecisionSnapshotError("SOURCE_COMMIT_UNRESOLVABLE")
    return value


# ---------------------------------------------------------------------------
# Authority -- hardcoded all-false. Unions P5-08's and P5-09's own exact
# authority-block vocabulary (reused, not reinvented) with two additional
# explicit fields for concepts neither upstream module names on its own.
# ---------------------------------------------------------------------------

def _authority_block() -> dict:
    block = dict(PROMOTION._ROW_AUTHORITY)
    block.update(ELIGIBILITY._ROW_AUTHORITY)
    block.update({
        # No concept in P3-12/P5-08/P5-09's own authority vocabulary names
        # "a real (non-PAPER) authority" or "permission to write an order"
        # directly -- these two are additive, not a duplicate of
        # order_authorized/trading_authorized/production_authorized above.
        "real_authority": False,
        "order_write_authorized": False,
    })
    return block


def _require_all_false(block: dict) -> None:
    if any(value is not False for value in block.values()):
        raise CryptoPaperDecisionSnapshotError("AUTHORITY_NOT_ALL_FALSE")


# ---------------------------------------------------------------------------
# Regime (P1-CR-08) -- DEFINED/UNDEFINED axis evidence only.
# ---------------------------------------------------------------------------

def build_regime_snapshot(generated_at: str, component_rows: dict | None = None) -> dict:
    """Build the market="CRYPTO" Regime envelope via the real P1-CR-08
    adapter. ``component_rows`` defaults to ``{}`` -- this composition
    script has no wired component registry of its own (that wiring is
    ``briefing/daily_orchestrator.py``'s job, a separate module) -- so every
    bound axis (TREND/RISK_VOL/LIQUIDITY/BREADTH/LEADERSHIP) fails closed to
    UNDEFINED via ``live_axis_adapter.py``'s own ``_attempt`` wrapper. This
    is an honest reflection of today's real wiring state, never a shortcut.
    """
    component_rows = {} if component_rows is None else component_rows
    factors = LIVE_AXIS.build_axis_factors(component_rows, generated_at)
    return REGIME_OUTPUT.build_unknown_output("CRYPTO", generated_at, factors=factors.get("CRYPTO", {}))


def crypto_regime_five_axis(regime_payload: dict) -> dict:
    """The five DEFINED/UNDEFINED axis facts only -- never the aggregate
    ``regime``/``direction`` fields (those stay internal derivation inputs
    for P5-08, never surfaced here as if they were an interpreted value).
    """
    factor_results = regime_payload.get("factor_results", {})
    return {
        axis: {
            "status": factor["status"],
            "warnings": factor.get("warnings", []),
            "observation_date": factor.get("observation_date"),
            "available_at": factor.get("available_at"),
        }
        for axis, factor in sorted(factor_results.items())
    }


# ---------------------------------------------------------------------------
# Finalized-candle attestation -- reuses P4-07's own already-computed
# classify_candles()/is_candle_finalized() results verbatim (they are
# already baked into the committed evidence packet); never re-derives.
# ---------------------------------------------------------------------------

def finalized_candle_attestation(market_evidence_entry: dict | None, *, used_in_promotion: bool) -> dict:
    if market_evidence_entry is None:
        return {"market_evidence_snapshot_date": None, "used_in_promotion": False, "markets": {}}
    packets = (market_evidence_entry["record"] or {}).get("packets", {})
    markets = {}
    for market, packet in sorted(packets.items()):
        candles = packet.get("candles", {})
        markets[market] = {
            timeframe: {
                "finalized_candle_count": (candles.get(timeframe) or {}).get("finalized_candle_count"),
                "in_progress_candle_count": (candles.get(timeframe) or {}).get("in_progress_candle_count"),
                "latest_finalized_close_time": (candles.get(timeframe) or {}).get("latest_finalized_close_time"),
                "freshness": (candles.get(timeframe) or {}).get("freshness"),
            }
            for timeframe in CANDLE_FINALIZATION.TIMEFRAMES
        }
    return {
        "market_evidence_snapshot_date": market_evidence_entry["date"],
        "used_in_promotion": used_in_promotion,
        "markets": markets,
    }


# ---------------------------------------------------------------------------
# Pure derivation -- given already-loaded upstream entries, build the full
# packet. No filesystem access below this point except the two config loads
# (part of the committed source tree, same discipline
# universe/crypto_paper_buy_eligibility.py::build_eligibility_packet already
# uses for its own default policy load).
# ---------------------------------------------------------------------------

def build_snapshot(
    *,
    generated_at: str,
    source_commit: str,
    universe_entry: dict | None,
    market_evidence_entry: dict | None,
    realtime_entry: dict | None,
    previous_entry: dict | None = None,
    component_rows: dict | None = None,
    started_at: str | None = None,
) -> dict:
    generated_dt = _parse_utc(generated_at, "generated_at")
    if not FULL_SHA_RE.fullmatch(source_commit):
        raise CryptoPaperDecisionSnapshotError(f"SOURCE_COMMIT_INVALID:{source_commit}")
    capture_date = generated_at[:10]
    capture_hhmm = generated_at[11:13] + generated_at[14:16]

    # -- Explicit time-basis fields ------------------------------------
    # capture_date/capture_hhmm (and the evidence path they name) are UTC.
    # These additional fields make that fact, and the KST-side reading a
    # Korean-investor-facing consumer actually wants, explicit and
    # self-documenting inside the packet itself -- a reader must never have
    # to open this module's source to learn that "0504" in the storage path
    # means 05:04 UTC (14:04 KST), not 05:04 KST.
    captured_at_utc = generated_at
    captured_at_kst_dt = generated_dt.astimezone(KST)
    captured_at_kst = captured_at_kst_dt.isoformat()
    operational_date_kst = captured_at_kst_dt.date().isoformat()

    # started_at is optional (older/manual callers, and every existing test
    # fixture in this file, do not supply it): when absent this quietly (no
    # derivation-notes marker -- see the note on determinism below) falls
    # back to generated_at, the best honestly-available proxy for "when this
    # run began". This fallback is a pure function of already-known inputs
    # (never wall-clock), so build_snapshot(started_at=None) and
    # build_snapshot(started_at=<that same generated_at value>) are
    # byte-identical -- required for validate_output's full-rederivation
    # replay to reproduce a packet that was originally built with
    # started_at omitted.
    resolved_started_at = generated_at if started_at is None else started_at
    started_dt = _parse_utc(resolved_started_at, "started_at")
    if started_dt > generated_dt:
        raise CryptoPaperDecisionSnapshotError("STARTED_AT_FUTURE_DATED")

    scheduled_for = _floor_to_schedule_slot(started_dt).strftime("%Y-%m-%dT%H:%M:%SZ")

    # completed_at: this module's generated_at IS the build-wall-clock
    # instant (the workflow captures it immediately before invoking this
    # script -- see module docstring's "Determinism" section) -- there is no
    # separate later "write" instant to record. Still emitted as its own
    # explicitly-named field rather than making a reader infer that
    # equivalence from generated_at alone.
    completed_at = generated_at

    notes: list[str] = []
    source_refs: list[dict] = []
    component_registry = copy.deepcopy(component_rows or {})
    regime_component_rows = {}
    if component_registry:
        try:
            component_registry = LIVE_COMPONENT_REGISTRY.validate_registry(
                component_registry,
                expected_generated_at=generated_at,
                root=ROOT,
            )
        except LIVE_COMPONENT_REGISTRY.CryptoLiveComponentRegistryError as exc:
            raise CryptoPaperDecisionSnapshotError(
                f"REGIME_COMPONENT_REGISTRY_INVALID:{exc}"
            ) from exc
        regime_component_rows = copy.deepcopy(component_registry["rows"])
        notes.append(
            "CRYPTO_LIVE_COMPONENT_REGISTRY_WIRED:"
            f"{len(regime_component_rows)}_SOURCE_COMPONENTS"
        )
        for component_id, row in regime_component_rows.items():
            if row.get("status") != "READY" and row.get("reason"):
                notes.append(f"{component_id}:{row['reason']}")
        for component_id, reason in component_registry["deferred_components"].items():
            notes.append(f"{component_id}:{reason}")

    _validate_universe_entry(universe_entry, not_after=generated_dt)
    _validate_market_evidence_entry(market_evidence_entry)
    _validate_realtime_entry(realtime_entry)
    if market_evidence_entry is not None:
        market_generated = _parse_utc(
            market_evidence_entry["record"].get("generated_at"), "market_evidence.generated_at",
        )
        if market_generated > generated_dt:
            raise CryptoPaperDecisionSnapshotError("MARKET_EVIDENCE_FUTURE_DATED")
        for market, source_packet in market_evidence_entry["record"]["packets"].items():
            captured_at = _parse_utc(source_packet.get("captured_at"), f"market_evidence.{market}.captured_at")
            if captured_at > generated_dt:
                raise CryptoPaperDecisionSnapshotError(f"MARKET_EVIDENCE_PACKET_FUTURE_DATED:{market}")
    if realtime_entry is not None:
        ended_at = _parse_utc(realtime_entry["record"]["run"].get("ended_at"), "realtime.ended_at")
        if ended_at > generated_dt:
            raise CryptoPaperDecisionSnapshotError("REALTIME_EVIDENCE_FUTURE_DATED")

    # -- P3-12 universe freshness -------------------------------------
    universe_policy = UNIVERSE.load_policy()
    universe_taxonomy = UNIVERSE.load_taxonomy()
    universe_identity_registry = UNIVERSE.load_identity_registry()
    max_age_hours = Decimal(str(universe_policy["max_capture_age_hours"]))
    universe_packet = universe_entry["packet"] if universe_entry else None
    universe_date = universe_entry["date"] if universe_entry else None
    if universe_entry is not None:
        source_refs.append({
            "role": "upbit_tradeable_universe_packet", "path": _relpath(universe_entry["path"]),
            "sha256": _file_sha256(universe_entry["path"]),
        })
        if universe_entry.get("transition_manifest_path") is not None:
            source_refs.extend([
                {
                    "role": "upbit_universe_transition_manifest",
                    "path": _relpath(universe_entry["transition_manifest_path"]),
                    "sha256": _file_sha256(universe_entry["transition_manifest_path"]),
                },
                {
                    "role": "upbit_universe_transition_source",
                    "path": _relpath(universe_entry["source_path"]),
                    "sha256": _file_sha256(universe_entry["source_path"]),
                },
            ])
    if universe_packet is None:
        universe_status = MISSING
        notes.append("UPBIT_UNIVERSE_PACKET_MISSING")
    else:
        available_at = _parse_utc(universe_packet["available_at"], "universe.available_at")
        if available_at > generated_dt:
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_AVAILABLE_AT_FUTURE_DATED")
        age_hours = Decimal(str((generated_dt - available_at).total_seconds())) / Decimal("3600")
        identity_authority_available = bool(
            UNIVERSE.effective_identity_mapping(
                universe_identity_registry, universe_packet["evaluation_as_of"],
            )
        )
        taxonomy_authority_available = UNIVERSE._identity_taxonomy_exact_bound_effective(
            universe_taxonomy,
            universe_packet["evaluation_as_of"],
            date_field="effective_from",
            content_field="records",
        )
        if not identity_authority_available or not taxonomy_authority_available:
            universe_status = UNKNOWN
            notes.append("UPBIT_IDENTITY_TAXONOMY_PENDING_GOVERNANCE_RESOLUTION")
        elif universe_packet.get("policy_ratified") is not True or universe_packet.get("taxonomy_ratified") is not True:
            universe_status = UNKNOWN
            notes.append("UPBIT_UNIVERSE_POLICY_OR_TAXONOMY_UNRATIFIED")
        elif age_hours > max_age_hours:
            universe_status = STALE
            notes.append(f"UPBIT_UNIVERSE_PACKET_STALE:age_hours={age_hours}:max={max_age_hours}")
        else:
            universe_status = FRESH

    # -- P4-07 market evidence freshness -------------------------------
    if market_evidence_entry is not None:
        source_refs.append({
            "role": "upbit_market_evidence_packet", "path": _relpath(market_evidence_entry["path"]),
            "sha256": _file_sha256(market_evidence_entry["path"]),
        })
    if market_evidence_entry is None:
        market_evidence_status = MISSING
        notes.append("UPBIT_MARKET_EVIDENCE_PACKET_MISSING")
    elif universe_date is not None and market_evidence_entry["date"] != universe_date:
        market_evidence_status = MIXED_GENERATION
        notes.append(
            f"UPBIT_MARKET_EVIDENCE_DATE_MISMATCH:universe={universe_date}:"
            f"market_evidence={market_evidence_entry['date']}"
        )
    else:
        market_evidence_status, market_evidence_reason = _market_evidence_freshness(
            market_evidence_entry["record"]
        )
        if market_evidence_reason:
            notes.append(market_evidence_reason)

    # -- P9-06 realtime evidence freshness (metadata only -- never an
    #    argument to P5-08/P5-09's own derivation) ----------------------
    if realtime_entry is not None:
        source_refs.append({
            "role": "upbit_realtime_capture_run", "path": _relpath(realtime_entry["path"]),
            "sha256": _file_sha256(realtime_entry["path"]),
        })
    if realtime_entry is None:
        realtime_status = MISSING
        notes.append("UPBIT_REALTIME_RUN_MISSING")
    elif universe_date is not None and realtime_entry["date"] != universe_date:
        realtime_status = MIXED_GENERATION
        notes.append(
            f"UPBIT_REALTIME_RUN_DATE_MISMATCH:universe={universe_date}:realtime={realtime_entry['date']}"
        )
    else:
        realtime_status, realtime_reason = _realtime_freshness(realtime_entry["record"])
        if realtime_reason:
            notes.append(realtime_reason)

    overall_freshness = _worst_freshness([universe_status, market_evidence_status, realtime_status])

    # -- Regime (P1-CR-08) -- independent of universe/market-evidence
    #    freshness; always computed honestly. ---------------------------
    regime_payload = build_regime_snapshot(generated_at, regime_component_rows)

    # -- Funnel: P5-08 then P5-09, reused verbatim ----------------------
    promotion_packet = None
    promotion_error = None
    eligibility_packet = None
    eligibility_error = None
    # "Used" requires both a consistent-generation market-evidence packet
    # AND an actual universe packet to run the funnel against -- reporting
    # true here when the funnel never even attempted to run (universe
    # missing) would misrepresent what this build actually did.
    market_evidence_used = market_evidence_status == FRESH and universe_packet is not None
    market_evidence_by_market = (
        market_evidence_entry["record"].get("packets", {}) if (market_evidence_entry and market_evidence_used) else {}
    )

    if universe_packet is not None:
        try:
            promotion_packet = PROMOTION.build_promotion_packet(
                universe_packet, regime_payload, market_evidence_by_market, None,
                evaluation_as_of=universe_packet["evaluation_as_of"],
            )
        except PROMOTION.CryptoCandidatePromotionError as exc:
            promotion_error = str(exc)
            notes.append(f"P5_08_PROMOTION_FUNNEL_UNAVAILABLE:{promotion_error}")
        if promotion_packet is not None:
            try:
                eligibility_packet = ELIGIBILITY.build_eligibility_packet(
                    promotion_packet, evaluation_as_of=universe_packet["evaluation_as_of"],
                )
            except ELIGIBILITY.CryptoPaperBuyEligibilityError as exc:
                eligibility_error = str(exc)
                notes.append(f"P5_09_ELIGIBILITY_FUNNEL_UNAVAILABLE:{eligibility_error}")
    else:
        notes.append("PROMOTION_FUNNEL_NOT_ATTEMPTED:UPBIT_UNIVERSE_PACKET_MISSING")

    eligibility_by_market = (
        {row["market"]: row for row in eligibility_packet["candidates"]} if eligibility_packet else {}
    )

    candidates = []
    if promotion_packet is not None:
        for row in promotion_packet["candidates"]:
            market = row["market"]
            elig_row = eligibility_by_market.get(market)
            if elig_row is not None:
                effective_state = elig_row["eligibility_state"]
                effective_reason = elig_row["eligibility_reason"]
            else:
                effective_state = row["promotion_state"]
                effective_reason = row["promotion_reason"]
            capped = cap_state_for_freshness(effective_state, effective_reason, overall_freshness)
            effective_state = capped["state"]
            effective_reason = capped["reason"]
            freshness_capped = capped["capped"]
            freshness_cap_reason = capped["cap_reason"]
            candidates.append({
                "market": market,
                "canonical_asset_id": row.get("canonical_asset_id"),
                "p3_12_state": row.get("p3_12_state"),
                "state": effective_state,
                "reason": effective_reason,
                "freshness_capped": freshness_capped,
                "freshness_cap_reason": freshness_cap_reason,
                "p5_08": {
                    "promotion_state": row["promotion_state"],
                    "promotion_reason": row["promotion_reason"],
                    "criteria": row["criteria"],
                },
                "p5_09": (
                    {
                        "eligibility_state": elig_row["eligibility_state"],
                        "eligibility_reason": elig_row["eligibility_reason"],
                        "criteria": elig_row["criteria"],
                        "order_draft": elig_row["order_draft"],
                    }
                    if elig_row is not None else None
                ),
                "authority": _authority_block(),
            })

    observation_pool_count = universe_packet["summary"]["observation_pool_count"] if universe_packet else 0
    tradeable_universe_count = (
        universe_packet["summary"]["tradeable_universe_count"] + universe_packet["summary"]["paper_eligible_count"]
        if universe_packet and universe_status == FRESH else 0
    )
    focused_review_count = promotion_packet["summary"]["focused_review_count"] if promotion_packet else 0
    paper_ready_count = sum(1 for row in candidates if row["state"] == "PAPER_BUY_ELIGIBLE")

    funnel_counts = {
        "observation_pool_count": observation_pool_count,
        "tradeable_universe_count": tradeable_universe_count,
        "focused_review_count": focused_review_count,
        "paper_ready_count": paper_ready_count,
    }

    # -- generation_id: deterministic identity of "this exact combination
    #    of input evidence" -- never wall-clock. -----------------------
    generation_basis = {
        "source_commit": source_commit,
        "universe": (
            {
                "date": universe_entry["date"],
                "payload_sha256": universe_packet.get("payload_sha256"),
                "file_sha256": _file_sha256(universe_entry["path"]),
                "transition": (
                    {
                        "manifest_file_sha256": _file_sha256(
                            universe_entry["transition_manifest_path"]
                        ),
                        "manifest_payload_sha256": universe_entry[
                            "transition_manifest_payload_sha256"
                        ],
                        "source_file_sha256": _file_sha256(universe_entry["source_path"]),
                        "source_payload_sha256": universe_entry["source_payload_sha256"],
                    }
                    if universe_entry.get("transition_manifest_path") is not None
                    else None
                ),
            }
            if universe_entry else None
        ),
        "market_evidence": (
            {
                "date": market_evidence_entry["date"],
                "payload_sha256": market_evidence_entry["record"].get("payload_sha256"),
                "file_sha256": _file_sha256(market_evidence_entry["path"]),
            }
            if market_evidence_entry else None
        ),
        "realtime": (
            {
                "date": realtime_entry["date"],
                "source_sha256": realtime_entry["record"].get("source_sha256"),
                "file_sha256": _file_sha256(realtime_entry["path"]),
            }
            if realtime_entry else None
        ),
        "regime_axis_snapshot_sha256": payload_sha256(regime_payload),
    }
    if component_registry:
        # Two source registries that happen to yield the same DEFINED/
        # UNDEFINED axis surface are still distinct exact input generations.
        # Bind the registry bytes into the content-addressed decision path so
        # a changed breadth/stablecoin source can never collide merely because
        # its policy-neutral axis status stayed the same.
        generation_basis["regime_component_registry_payload_sha256"] = (
            component_registry["payload_sha256"]
        )
    generation_id = payload_sha256(generation_basis)
    if not SHA256_RE.fullmatch(generation_id):
        raise CryptoPaperDecisionSnapshotError("GENERATION_ID_INVALID")

    duplicate_guard_key = f"CRYPTO-PAPER-DECISION-{capture_date.replace('-', '')}-{capture_hhmm}-{generation_id[:24].upper()}"
    if not TOKEN_RE.fullmatch(duplicate_guard_key):
        raise CryptoPaperDecisionSnapshotError("DUPLICATE_GUARD_KEY_FORMAT_INVALID")

    authority = _authority_block()
    _require_all_false(authority)

    packet = {
        "schema_version": OUTPUT_SCHEMA_VERSION,
        "generated_at": generated_at,
        "capture_date": capture_date,
        "capture_hhmm": capture_hhmm,
        "captured_at_utc": captured_at_utc,
        "captured_at_kst": captured_at_kst,
        "operational_date_kst": operational_date_kst,
        "path_time_basis": PATH_TIME_BASIS,
        "scheduled_for": scheduled_for,
        "started_at": resolved_started_at,
        "completed_at": completed_at,
        "source_commit": source_commit,
        "generation_id": generation_id,
        "duplicate_guard_key": duplicate_guard_key,
        "source_refs": source_refs,
        "upbit_universe_snapshot_identity": {
            "date": universe_date,
            "payload_sha256": universe_packet.get("payload_sha256") if universe_packet else None,
        },
        "finalized_candle_attestation": finalized_candle_attestation(
            market_evidence_entry, used_in_promotion=market_evidence_used,
        ),
        "crypto_regime_five_axis": crypto_regime_five_axis(regime_payload),
        "source_components": component_registry,
        "funnel_counts": funnel_counts,
        "candidates": candidates,
        "freshness_status": {
            "upbit_universe": universe_status,
            "market_evidence": market_evidence_status,
            "realtime": realtime_status,
            "overall": overall_freshness,
        },
        "authority": authority,
        "previous_state_reference": previous_entry,
        "derivation_notes": notes,
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def _resolve_source_path(path_value: object, *, allow_external_sources: bool) -> Path:
    if not isinstance(path_value, str) or not path_value:
        raise CryptoPaperDecisionSnapshotError("SOURCE_REF_PATH_INVALID")
    path = Path(path_value)
    if path.is_absolute():
        if not allow_external_sources:
            raise CryptoPaperDecisionSnapshotError("SOURCE_REF_ABSOLUTE_PATH_FORBIDDEN")
        return path
    resolved = (ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT.resolve())
    except ValueError as exc:
        raise CryptoPaperDecisionSnapshotError("SOURCE_REF_PATH_ESCAPE") from exc
    return resolved


def validate_output(packet: dict, *, allow_external_sources: bool = False) -> dict:
    """Re-read every retained source and reproduce the complete snapshot.

    A caller cannot change a candidate state, freshness label, funnel count,
    policy result, or source reference and make it valid merely by
    recomputing the outer hash.
    """
    expected_keys = {
        "schema_version", "generated_at", "capture_date", "capture_hhmm",
        "captured_at_utc", "captured_at_kst", "operational_date_kst", "path_time_basis",
        "scheduled_for", "started_at", "completed_at", "source_commit",
        "generation_id", "duplicate_guard_key", "source_refs", "upbit_universe_snapshot_identity",
        "finalized_candle_attestation", "crypto_regime_five_axis", "source_components",
        "funnel_counts", "candidates", "freshness_status", "authority",
        "previous_state_reference", "derivation_notes", "payload_sha256",
    }
    legacy_expected_keys = expected_keys - TIME_BASIS_FIELDS
    if not isinstance(packet, dict) or set(packet) not in {
        frozenset(expected_keys), frozenset(legacy_expected_keys),
    }:
        raise CryptoPaperDecisionSnapshotError("OUTPUT_SCHEMA_MISMATCH")
    is_legacy_packet = set(packet) == legacy_expected_keys
    if packet.get("schema_version") != OUTPUT_SCHEMA_VERSION:
        raise CryptoPaperDecisionSnapshotError("OUTPUT_SCHEMA_VERSION_MISMATCH")
    _validate_embedded_hash(packet, "payload_sha256", "crypto_paper_decision_snapshot")
    _require_all_false(packet.get("authority") or {})
    for row in packet.get("candidates") or []:
        _require_all_false(row.get("authority") or {})

    refs = packet.get("source_refs")
    if not isinstance(refs, list):
        raise CryptoPaperDecisionSnapshotError("SOURCE_REFS_INVALID")
    by_role = {}
    for ref in refs:
        if not isinstance(ref, dict) or set(ref) != {"role", "path", "sha256"}:
            raise CryptoPaperDecisionSnapshotError("SOURCE_REF_SCHEMA_INVALID")
        role = ref["role"]
        if role in by_role or role not in {
            "upbit_tradeable_universe_packet", "upbit_market_evidence_packet",
            "upbit_realtime_capture_run",
            "upbit_universe_transition_manifest", "upbit_universe_transition_source",
        }:
            raise CryptoPaperDecisionSnapshotError("SOURCE_REF_ROLE_INVALID")
        path = _resolve_source_path(ref["path"], allow_external_sources=allow_external_sources)
        if _file_sha256(path) != ref["sha256"]:
            raise CryptoPaperDecisionSnapshotError(f"SOURCE_REF_HASH_MISMATCH:{role}")
        by_role[role] = {"path": path, "record": _read_json(path)}

    universe_entry = None
    if "upbit_tradeable_universe_packet" in by_role:
        value = by_role["upbit_tradeable_universe_packet"]
        universe_entry = {
            "date": value["path"].parent.name,
            "path": value["path"],
            "record": value["record"],
            "packet": value["record"].get("packet"),
        }
        transition_roles = {
            "upbit_universe_transition_manifest",
            "upbit_universe_transition_source",
        }
        present_transition_roles = transition_roles & set(by_role)
        retained_successor = value["path"].name == "successor.json"
        if present_transition_roles and present_transition_roles != transition_roles:
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_TRANSITION_SOURCE_REFS_INCOMPLETE")
        if retained_successor and present_transition_roles != transition_roles:
            raise CryptoPaperDecisionSnapshotError("UNIVERSE_TRANSITION_SOURCE_REFS_MISSING")
        if present_transition_roles:
            manifest_value = by_role["upbit_universe_transition_manifest"]
            source_value = by_role["upbit_universe_transition_source"]
            manifest_record = manifest_value["record"]
            universe_entry.update({
                "transition_manifest_path": manifest_value["path"],
                "transition_manifest_payload_sha256": manifest_record.get("payload_sha256"),
                "source_path": source_value["path"],
                "source_payload_sha256": source_value["record"].get("payload_sha256"),
                "transition_retained": True,
            })
    market_entry = None
    if "upbit_market_evidence_packet" in by_role:
        value = by_role["upbit_market_evidence_packet"]
        market_record = value["record"]
        market_entry = {
            # P4 v2's immutable source directory is
            # ``YYYY-MM-DD-p3-<record-hash-prefix>`` while the source's
            # operational date remains the embedded ``snapshot_date``.
            # Retained sources use a content-addressed path whose immediate
            # parent is the plain date, so path.parent.name cannot be the
            # authoritative reconstruction rule for both valid layouts.
            # The embedded record is file-hash pinned above and its v2
            # snapshot-key/P3 lineage is independently checked by
            # _validate_market_evidence_entry.
            "date": market_record.get("snapshot_date"),
            "path": value["path"],
            "record": market_record,
        }
    realtime_entry = None
    if "upbit_realtime_capture_run" in by_role:
        value = by_role["upbit_realtime_capture_run"]
        realtime_entry = {
            "date": value["path"].parent.name,
            "path": value["path"],
            "record": value["record"],
        }

    rebuilt = build_snapshot(
        generated_at=packet["generated_at"],
        source_commit=packet["source_commit"],
        universe_entry=universe_entry,
        market_evidence_entry=market_entry,
        realtime_entry=realtime_entry,
        previous_entry=packet["previous_state_reference"],
        component_rows=packet["source_components"],
        started_at=packet.get("started_at"),
    )
    if is_legacy_packet:
        rebuilt.pop("payload_sha256")
        for field in TIME_BASIS_FIELDS:
            rebuilt.pop(field)
        rebuilt["payload_sha256"] = payload_sha256(rebuilt)
    if canonical_json(rebuilt) != canonical_json(packet):
        raise CryptoPaperDecisionSnapshotError("OUTPUT_DERIVATION_MISMATCH")
    return copy.deepcopy(packet)


# ---------------------------------------------------------------------------
# I/O layer -- discovery, idempotent atomic write, GITHUB_OUTPUT, CLI.
# Mirrors .github/scripts/upbit_universe_populate.py::populate exactly.
# ---------------------------------------------------------------------------

class PopulationError(CryptoPaperDecisionSnapshotError):
    pass


def output_path(capture_date: str, capture_hhmm: str, generation_id: str, output_root: Path = OUTPUT_ROOT) -> Path:
    return Path(output_root) / capture_date / capture_hhmm / generation_id / "packet.json"


def retain_source(entry: dict | None, output_root: Path) -> dict | None:
    """Copy one selected input to a content-addressed immutable path.

    The source-ref schema intentionally stays unchanged.  Keeping the source's
    date as the immediate parent directory preserves the validator's existing
    date reconstruction contract while the digest directory makes later
    writes to the rolling upstream path irrelevant.  Retention applies only to
    repository evidence emission.  A caller using an external/disposable
    output root keeps the original source reference so strict downstream
    consumers never receive an absolute retained-source path.
    """
    if entry is None:
        return None
    try:
        Path(output_root).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return copy.deepcopy(entry)
    source = Path(entry["path"])
    source_bytes = source.read_bytes()
    digest = hashlib.sha256(source_bytes).hexdigest()
    bundle_root = Path(output_root) / "_sources" / "sha256" / digest / entry["date"]

    def retain_exact(original: Path, target: Path) -> None:
        original_bytes = Path(original).read_bytes()
        if target.exists():
            if target.read_bytes() != original_bytes:
                raise PopulationError(f"RETAINED_SOURCE_DRIFT_OR_TAMPER:{target}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temp = target.with_name(f".{target.name}.tmp")
        try:
            temp.write_bytes(original_bytes)
            temp.replace(target)
        finally:
            if temp.exists():
                temp.unlink()

    transition_manifest_path = entry.get("transition_manifest_path")
    if transition_manifest_path is None:
        target = bundle_root / "source.json"
        retain_exact(source, target)
    else:
        target = bundle_root / "successor.json"
        retained_manifest = bundle_root / "transition.json"
        retained_source = bundle_root / "canonical-source.json"
        retain_exact(source, target)
        retain_exact(Path(transition_manifest_path), retained_manifest)
        retain_exact(Path(entry["source_path"]), retained_source)
    retained = copy.deepcopy(entry)
    retained["path"] = target
    if transition_manifest_path is not None:
        retained["transition_manifest_path"] = retained_manifest
        retained["source_path"] = retained_source
        retained["transition_retained"] = True
    return retained


def populate(
    *,
    generated_at: str,
    source_commit: str | None = None,
    universe_data_root: Path = UNIVERSE_DATA_ROOT,
    market_evidence_data_root: Path = MARKET_EVIDENCE_DATA_ROOT,
    realtime_evidence_root: Path = REALTIME_EVIDENCE_ROOT,
    realtime_run_path: Path | None = None,
    allow_realtime_fallback: bool = False,
    output_root: Path = OUTPUT_ROOT,
    started_at: str | None = None,
    wire_regime_components: bool = False,
) -> dict:
    resolved_source_commit = resolve_source_commit(source_commit)
    generated_dt = _parse_utc(generated_at, "generated_at")
    universe_entry = find_latest_universe_packet(universe_data_root, not_after=generated_dt)
    market_evidence_entry = find_latest_market_evidence_packet(
        market_evidence_data_root, not_after=generated_dt,
    )
    if realtime_run_path is not None:
        realtime_run_path = Path(realtime_run_path)
        realtime_entry = (
            {"date": realtime_run_path.parent.name, "path": realtime_run_path, "record": _read_json(realtime_run_path)}
            if realtime_run_path.is_file() else None
        )
    elif allow_realtime_fallback:
        realtime_entry = find_latest_realtime_run(
            realtime_evidence_root, not_after=generated_dt,
        )
    else:
        realtime_entry = None

    universe_entry = retain_source(universe_entry, output_root)
    market_evidence_entry = retain_source(market_evidence_entry, output_root)
    realtime_entry = retain_source(realtime_entry, output_root)

    capture_date = generated_at[:10]
    capture_hhmm = generated_at[11:13] + generated_at[14:16]
    previous_entry = find_previous_packet(output_root, capture_date, capture_hhmm)

    component_registry = None
    if wire_regime_components:
        try:
            component_registry = LIVE_COMPONENT_REGISTRY.build_registry(
                generated_at, root=ROOT
            )
        except LIVE_COMPONENT_REGISTRY.CryptoLiveComponentRegistryError as exc:
            raise PopulationError(
                f"REGIME_COMPONENT_REGISTRY_BUILD_FAILED:{exc}"
            ) from exc

    record = build_snapshot(
        generated_at=generated_at,
        source_commit=resolved_source_commit,
        universe_entry=universe_entry,
        market_evidence_entry=market_evidence_entry,
        realtime_entry=realtime_entry,
        previous_entry=previous_entry,
        started_at=started_at,
        component_rows=component_registry,
    )
    validate_output(
        record,
        allow_external_sources=any(Path(ref["path"]).is_absolute() for ref in record["source_refs"]),
    )

    target = output_path(record["capture_date"], record["capture_hhmm"], record["generation_id"], output_root)
    if target.exists():
        try:
            existing = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise PopulationError(f"EXISTING_PACKET_UNREADABLE:{target}:{exc}") from exc
        if existing != record:
            raise PopulationError(f"EXISTING_PACKET_DRIFT_OR_TAMPER:{target}")
        return {
            "outcome": "verified_existing", "reason": None, "path": str(target),
            "payload_sha256": record["payload_sha256"], "generation_id": record["generation_id"],
            "record": record,
        }
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(f".{target.name}.tmp")
    payload = json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    try:
        temp.write_text(payload, encoding="utf-8")
        temp.replace(target)
    finally:
        if temp.exists():
            temp.unlink()
    return {
        "outcome": "populated", "reason": None, "path": str(target),
        "payload_sha256": record["payload_sha256"], "generation_id": record["generation_id"],
        "record": record,
    }


def _write_github_output(result: dict) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    single_line = lambda value: (value or "").replace("\n", " ").replace("\r", " ")
    lines = [
        f"outcome={single_line(result.get('outcome'))}",
        f"reason={single_line(result.get('reason'))}",
        f"path={single_line(result.get('path'))}",
        f"payload_sha256={single_line(result.get('payload_sha256'))}",
        f"generation_id={single_line(result.get('generation_id'))}",
    ]
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def run(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generated-at", required=True, help="This workflow run's observed UTC capture instant")
    parser.add_argument("--source-commit", default=None, help="Full 40-char git SHA (default: git rev-parse HEAD)")
    parser.add_argument(
        "--started-at", default=None,
        help=(
            "This workflow run's observed UTC runner-start instant "
            "(steps.runner_start.outputs.observed_started_at_utc). "
            "Default: falls back to --generated-at when omitted."
        ),
    )
    parser.add_argument("--universe-data-root", type=Path, default=UNIVERSE_DATA_ROOT)
    parser.add_argument("--market-evidence-data-root", type=Path, default=MARKET_EVIDENCE_DATA_ROOT)
    parser.add_argument("--realtime-evidence-root", type=Path, default=REALTIME_EVIDENCE_ROOT)
    parser.add_argument("--realtime-run-path", type=Path, default=None)
    parser.add_argument(
        "--allow-realtime-fallback", action="store_true",
        help="Manual diagnostics only: reuse the latest prior realtime run when no exact run path is supplied.",
    )
    parser.add_argument(
        "--wire-regime-components", action="store_true",
        help=(
            "Bind repository-local public BTC trend/risk, stablecoin and Crypto "
            "breadth rows through the hash-bound P1-CR-08 registry."
        ),
    )
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        result = populate(
            generated_at=args.generated_at,
            source_commit=args.source_commit,
            universe_data_root=args.universe_data_root,
            market_evidence_data_root=args.market_evidence_data_root,
            realtime_evidence_root=args.realtime_evidence_root,
            realtime_run_path=args.realtime_run_path,
            allow_realtime_fallback=args.allow_realtime_fallback,
            output_root=args.output_root,
            started_at=args.started_at,
            wire_regime_components=args.wire_regime_components,
        )
    except CryptoPaperDecisionSnapshotError as exc:
        _write_github_output({"outcome": "failed", "reason": str(exc), "path": None, "payload_sha256": None, "generation_id": None})
        print(f"Crypto PAPER decision snapshot failed: {exc}")
        return 1
    _write_github_output(result)
    record = result["record"]
    print(json.dumps({
        "outcome": result["outcome"],
        "path": result["path"],
        "payload_sha256": result["payload_sha256"],
        "generation_id": result["generation_id"],
        "freshness_status": record["freshness_status"],
        "funnel_counts": record["funnel_counts"],
        "derivation_notes": record["derivation_notes"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
