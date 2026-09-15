#!/usr/bin/env python3
"""FRED DEXKOUS (KRW/USD, Federal Reserve H.10) point-in-time capture evidence.

Implements the *data* side of ``RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1``
(config/rule_registry_v1.json, source record
evidence/authority/USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915.json,
sha256 2a94be2b593ed49a61e38cecfc2c992802ffa8102b292bf40bd964e7391d5fdd):
combined PAPER NAV converts USD holdings with the latest Federal Reserve
H.10 KRW/USD rate (FRED series DEXKOUS) published before the decision time;
the observation date is recorded; more than 10 business days without a new
value displays 'NAV 일부 미검증' (display only -- this module never blocks
an allocation and opens no trading/order/allocation authority; see
AUTHORITY below, every field is False).

Point-in-time model
--------------------
FRED revises DEXKOUS with a lag, so "the latest value published before the
decision time" cannot be re-derived later just by re-fetching the series --
today's fetch of "yesterday" may already differ from what was truly public
yesterday. This module therefore records, on every capture run, every
(observation_date, value) pair it sees and stamps each one with *this run's*
own wall-clock capture time (``availability_captured_at_utc``). That capture
time is a defensible upper bound on the true publication instant (if this
run already observed it, it was already public no later than now) -- never
a precise one. The staleness clock built on it is explicitly a CIO
interpretation, not a user-ratified guarantee of exact publication timing
(``STALENESS_CLOCK_KIND``), matching config/paper_execution_core_v1.json's
``fx_conversion.staleness_clock_kind``.

A one-time historical backfill is allowed (there is no other way to seed
years of history), but a backfilled row was never incrementally observed as
newly-seen -- its true public availability time is unknown. Backfilled rows
are stored with ``availability_kind: UNKNOWN_BACKFILL`` and
``availability_captured_at_utc: null``; the reader (`latest_available`)
never treats a backfilled row as usable point-in-time evidence for a
decision -- only a row captured on a real incremental run
(``availability_kind: CAPTURED``) can satisfy "published before the
decision time". Re-observing a previously-backfilled value on a real run
writes a *separate* CAPTURED record for that same (date, value) pair (a
different content-addressed path), so the backfill row is never silently
promoted or overwritten.

Normal captures are bounded, not full-history (CIO incident 2026-09-15)
--------------------------------------------------------------------------
The FRED CSV/API response always contains the *entire* DEXKOUS series
(1981-present) whether or not it changed. A normal (non-``--backfill``)
capture run originally turned every one of those ~11,000+ historical
observations into a new CAPTURED record every single day -- correct in the
narrow sense that "this row was seen no later than now" is true, but an
operationally pointless full-history rewrite each run (this repo's first
live FRED run did exactly that: 11,352 files in one commit). ``main()`` now
computes a bounded write window before calling ``build_capture``:
``write_observations_from`` = ``min(latest already-committed CAPTURED
observation_date, today - RECENT_WINDOW_DAYS)`` (or just the window start if
nothing is committed yet). Only observations on/after that date become new
CAPTURED records; everything older is parsed (so the manifest's
``observation_count``/``observation_date_range`` still describe the full
raw response for audit) but never written as a file. This still catches
two real cases correctly: a genuinely new day's value (date advances past
whatever was last committed), and FRED revising a recent value within the
window (content-addressing makes an unchanged re-observation a no-op and a
genuine revision a new file, exactly as before) -- while never touching the
untouched decades of history again. ``--backfill`` is unaffected and still
writes the complete parsed history in one deliberate, explicit run.


Source
------
Uses the FRED public API (``api.stlouisfed.org/fred/series/observations``)
when ``FRED_API_KEY`` is set (this repo already provisions that secret for
``collectors/free_market_data.py`` / ``collectors/fred_vix_provenance.py``;
no new secret is added here), otherwise falls back to the public CSV
endpoint (``fred.stlouisfed.org/graph/fredgraph.csv``), which needs no key.
Both paths are exercised by mocked-HTTP tests only -- this module makes no
network call unless explicitly invoked with a live ``getter``.

Both paths bound the RAW ARCHIVE too, not just which observations become
files (PR #765 follow-up)
--------------------------------------------------------------------------
The bounded write window above (see "Normal captures are bounded") only
ever bounded which *parsed* observations become per-observation files --
the raw archive (the gzip of the exact HTTP response, one file per day)
was always the literal, unmodified bytes actually received, regardless of
how much of it get written out as records. That distinction matters here:
the FRED **API** path already requests only the bounded window
server-side (``observation_start``), so its raw archive is small and
already bounded. The FRED **CSV** fallback, in contrast, had no such
parameter wired through, so a normal run using it (``FRED_API_KEY``
absent) would archive FRED's *entire* 1981-present response every single
day -- still just one file (~60-90KB gzipped, not thousands), so never the
11,352-file incident, but a real, avoidable daily cost that would
otherwise accumulate for as long as the CSV fallback stays in use.
``fetch_via_csv`` now also accepts ``observation_start`` and appends it as
FRED's public ``cosd`` (chart observation start date) query parameter,
wired through by ``main()`` exactly like the API path already was. This
keeps the raw-evidence integrity model unchanged -- the archive is still
the literal, unmodified bytes FRED actually sent for the request that was
actually made, just for a smaller, explicitly bounded request -- rather
than trimming a full response ourselves after the fact, which would mean
``raw_sha256`` no longer hashed what the server truly returned.
``cosd`` is FRED's own long-documented public parameter for this endpoint,
but -- like every other never-fetched-live claim in this module -- it is
UNVERIFIED against the real endpoint from this repo; its next live
(non-backfill) CSV-fallback run is what will confirm it actually narrows
the response as expected. ``--backfill`` never passes ``observation_start``
either way and continues to fetch (and archive) the complete series,
deliberately.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
UTC = dt.timezone.utc
UTC_SECOND = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

SERIES_ID = "DEXKOUS"
API_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"
CSV_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DEXKOUS"

BATCH_SCHEMA_VERSION = "fred_dexkous_fx_raw_batch/1"
OBSERVATION_SCHEMA_VERSION = "fred_dexkous_fx_observation/1"
EVIDENCE_ROOT = "evidence/fred_dexkous_fx"
RAW_RETENTION = "APPEND_ONLY_CONTENT_ADDRESSED"

STALENESS_MAX_BUSINESS_DAYS = 10
STALENESS_DISPLAY = "NAV 일부 미검증"
STALENESS_CLOCK_KIND = "CIO_INTERPRETATION_NOT_USER_RATIFIED"

# Bounded write window for normal (non-backfill) captures -- see module
# docstring, "Normal captures are bounded, not full-history". 30 calendar
# days comfortably covers any plausible late revision to a recent H.10
# value and any reasonable gap in the daily schedule running; a genuinely
# larger gap is still covered exactly (not just within this window) by
# comparing against the latest already-committed CAPTURED date.
RECENT_WINDOW_DAYS = 30

AUTHORITY = {
    "evidence_capture_only": True,
    "nav_conversion_authorized": False,
    "allocation_blocking_authorized": False,
    "regime_interpretation_authorized": False,
    "direction_authorized": False,
    "confidence_authorized": False,
    "threshold_authorized": False,
    "market_ranking_authorized": False,
    "action_authorized": False,
    "production_authorized": False,
    "trading_authorized": False,
}


class FredDexkousFxError(ValueError):
    """The capture cannot prove the declared DEXKOUS observation(s)."""


def fail(code: str):
    raise FredDexkousFxError(code)


# ─────────────────────────────────────────────────────────────────────────
# Small self-contained primitives (deliberately duplicated rather than
# imported from collectors/fred_vix_provenance.py -- CIO copy-preserve
# convention per run_all.py: each evidence module stays independently
# replayable without a cross-module edit surface).
# ─────────────────────────────────────────────────────────────────────────

def canonical_bytes(value) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def deterministic_gzip(value: bytes) -> bytes:
    """Gzip container whose header is stable across Python/OS (OS=255)."""
    output = io.BytesIO()
    with gzip.GzipFile(
        filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0
    ) as stream:
        stream.write(value)
    return output.getvalue()


def _parse_utc(value: object, code: str) -> dt.datetime:
    if not isinstance(value, str) or UTC_SECOND.fullmatch(value) is None:
        fail(code)
    try:
        return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        fail(code)


def _parse_date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code)
    try:
        parsed = dt.date.fromisoformat(value)
    except ValueError:
        fail(code)
    if parsed.isoformat() != value:
        fail(code)
    return parsed


def _validate_rate(value_text: str, code: str) -> str:
    try:
        numeric = float(value_text)
    except (TypeError, ValueError):
        fail(code)
    if not (numeric == numeric) or numeric in (float("inf"), float("-inf")) or numeric <= 0:
        fail(code)
    return value_text


# ─────────────────────────────────────────────────────────────────────────
# Parsing -- FRED API (json) and FRED public CSV, normalized to the same
# ``[{"observation_date": "YYYY-MM-DD", "value": "1234.56"}, ...]`` shape,
# ascending by date, missing ("." / blank / "NA") values dropped.
# ─────────────────────────────────────────────────────────────────────────

def parse_api_observations(raw: bytes) -> list[dict]:
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        fail("DEXKOUS_RAW_JSON_INVALID")
    if not isinstance(payload, dict):
        fail("DEXKOUS_RAW_ROOT_INVALID")
    observations = payload.get("observations")
    if not isinstance(observations, list) or not observations:
        fail("DEXKOUS_OBSERVATIONS_MISSING")
    out: list[dict] = []
    prior: dt.date | None = None
    for row in observations:
        if not isinstance(row, dict):
            fail("DEXKOUS_OBSERVATION_INVALID")
        row_date = _parse_date(row.get("date"), "DEXKOUS_OBSERVATION_DATE_INVALID")
        if prior is not None and row_date < prior:
            fail("DEXKOUS_OBSERVATIONS_NOT_ASCENDING")
        prior = row_date
        value = row.get("value")
        if value in (None, "."):
            continue
        if not isinstance(value, str):
            fail("DEXKOUS_OBSERVATION_VALUE_INVALID")
        out.append({
            "observation_date": row_date.isoformat(),
            "value": _validate_rate(value, "DEXKOUS_OBSERVATION_VALUE_INVALID"),
        })
    if not out:
        fail("DEXKOUS_VALUES_MISSING")
    return out


def parse_csv_observations(raw: bytes) -> list[dict]:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        fail("DEXKOUS_RAW_CSV_INVALID")
    rows = list(csv.reader(io.StringIO(text)))
    if not rows or len(rows[0]) < 2 or rows[0][0].strip().upper() != "DATE":
        fail("DEXKOUS_CSV_HEADER_INVALID")
    out: list[dict] = []
    prior: dt.date | None = None
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue
        if len(row) < 2:
            fail("DEXKOUS_CSV_ROW_INVALID")
        row_date = _parse_date(row[0].strip(), "DEXKOUS_OBSERVATION_DATE_INVALID")
        if prior is not None and row_date < prior:
            fail("DEXKOUS_OBSERVATIONS_NOT_ASCENDING")
        prior = row_date
        value = row[1].strip()
        if value in ("", ".", "NA", "N/A"):
            continue
        out.append({
            "observation_date": row_date.isoformat(),
            "value": _validate_rate(value, "DEXKOUS_OBSERVATION_VALUE_INVALID"),
        })
    if not out:
        fail("DEXKOUS_VALUES_MISSING")
    return out


def parse_observations(raw: bytes, source_kind: str) -> list[dict]:
    if source_kind == "FRED_API":
        return parse_api_observations(raw)
    if source_kind == "FRED_CSV":
        return parse_csv_observations(raw)
    fail("DEXKOUS_SOURCE_KIND_INVALID")


# ─────────────────────────────────────────────────────────────────────────
# Fetch -- injectable getter so tests never touch the network.
# ─────────────────────────────────────────────────────────────────────────

def _get(url: str, headers: dict[str, str] | None = None) -> bytes:
    request = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read()
    except urllib.error.HTTPError as exc:
        # Never let urllib's own exception text (may embed the request URL,
        # which may embed the api_key query parameter) escape uncaught.
        fail(f"DEXKOUS_HTTP_ERROR_{exc.code}")
    except urllib.error.URLError:
        fail("DEXKOUS_HTTP_UNREACHABLE")


def fetch_via_api(
    api_key: str, *, observation_start: str | None = None, getter=_get
) -> tuple[bytes, str]:
    query = {
        "series_id": SERIES_ID,
        "api_key": api_key,
        "file_type": "json",
        "sort_order": "asc",
    }
    if observation_start:
        query["observation_start"] = observation_start
    raw = getter(API_OBSERVATIONS_URL + "?" + urllib.parse.urlencode(query))
    return raw, "FRED_API"


def fetch_via_csv(*, observation_start: str | None = None, getter=_get) -> tuple[bytes, str]:
    url = CSV_URL
    if observation_start:
        # FRED's public chart-download endpoint accepts cosd (chart
        # observation start date) to bound the series server-side --
        # see module docstring, "Source", for why this matters and why it
        # is UNVERIFIED against the real endpoint from this repo.
        url += "&" + urllib.parse.urlencode({"cosd": observation_start})
    return getter(url), "FRED_CSV"


def fetch_dexkous(
    api_key: str | None, *, observation_start: str | None = None, getter=_get
) -> tuple[bytes, str]:
    if api_key:
        return fetch_via_api(api_key, observation_start=observation_start, getter=getter)
    return fetch_via_csv(observation_start=observation_start, getter=getter)


# ─────────────────────────────────────────────────────────────────────────
# Capture (build, deterministic / pure) and publish (write-once, I/O).
# ─────────────────────────────────────────────────────────────────────────

def _value_hash(value: str) -> str:
    return sha256_bytes(value.encode("utf-8"))[:16]


def build_capture(
    captured_at: dt.datetime, raw: bytes, source_kind: str, *,
    is_backfill: bool = False, write_observations_from: str | None = None,
) -> dict:
    """``write_observations_from`` (ISO date, inclusive) bounds which parsed
    observations become new per-observation files -- see module docstring,
    "Normal captures are bounded, not full-history". The manifest's
    ``observation_count``/``observation_date_range`` always describe the
    full parsed response regardless of this bound (full-history audit
    trail stays in the one raw archive, never lost); only the number of
    files actually written is bounded. Not accepted together with
    ``is_backfill=True`` -- a backfill run always writes everything parsed.
    """
    if captured_at.tzinfo is None:
        fail("CAPTURE_TIME_NAIVE")
    if is_backfill and write_observations_from is not None:
        fail("BACKFILL_WITH_WRITE_FROM_NOT_ALLOWED")
    captured_at_utc = captured_at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    _parse_utc(captured_at_utc, "CAPTURE_TIME_INVALID")

    observations = parse_observations(raw, source_kind)
    raw_sha256 = sha256_bytes(raw)

    records_source = observations
    if write_observations_from is not None:
        cutoff = _parse_date(write_observations_from, "WRITE_FROM_DATE_INVALID")
        records_source = [
            obs for obs in observations
            if dt.date.fromisoformat(obs["observation_date"]) >= cutoff
        ]

    manifest_basis = {
        "schema_version": BATCH_SCHEMA_VERSION,
        "series_id": SERIES_ID,
        "source_kind": source_kind,
        "captured_at_utc": captured_at_utc,
        "is_backfill": is_backfill,
        "raw_sha256": raw_sha256,
        "observation_count": len(observations),
        "observation_date_range": [
            observations[0]["observation_date"], observations[-1]["observation_date"],
        ],
        "written_observation_count": len(records_source),
        "write_observations_from": write_observations_from,
    }
    revision_id = sha256_bytes(canonical_bytes(manifest_basis))
    day = captured_at.astimezone(UTC).date().isoformat()
    base = f"{EVIDENCE_ROOT}/raw/{day}/{revision_id}"
    manifest = {**manifest_basis, "revision_id": revision_id,
                "raw_retention": RAW_RETENTION, "authority": AUTHORITY}
    manifest_bytes = json.dumps(
        manifest, ensure_ascii=False, indent=2, sort_keys=True
    ).encode("utf-8") + b"\n"
    raw_gzip_bytes = deterministic_gzip(raw)
    raw_pointer = {
        "revision_id": revision_id,
        "manifest_path": f"{base}/manifest.json",
        "manifest_file_sha256": sha256_bytes(manifest_bytes),
        "raw_path": f"{base}/dexkous_{source_kind.lower()}.raw.gz",
        "raw_file_sha256": sha256_bytes(raw_gzip_bytes),
        "raw_response_sha256": raw_sha256,
    }

    kind_suffix = "backfill" if is_backfill else "captured"
    availability_kind = "UNKNOWN_BACKFILL" if is_backfill else "CAPTURED"
    availability_captured_at_utc = None if is_backfill else captured_at_utc
    observation_records = []
    for obs in records_source:
        record = {
            "schema_version": OBSERVATION_SCHEMA_VERSION,
            "series_id": SERIES_ID,
            "observation_date": obs["observation_date"],
            "value": obs["value"],
            "availability_kind": availability_kind,
            "availability_captured_at_utc": availability_captured_at_utc,
            "source_kind": source_kind,
            "batch_revision_id": revision_id,
            "authority": AUTHORITY,
        }
        record_bytes = json.dumps(
            record, ensure_ascii=False, indent=2, sort_keys=True
        ).encode("utf-8") + b"\n"
        record_path = (
            f"{EVIDENCE_ROOT}/observations/{obs['observation_date']}/"
            f"{_value_hash(obs['value'])}.{kind_suffix}.json"
        )
        observation_records.append({
            "record": record,
            "record_bytes": record_bytes,
            "record_path": record_path,
            "record_sha256": sha256_bytes(record_bytes),
        })

    return {
        "manifest": manifest,
        "manifest_bytes": manifest_bytes,
        "raw_gzip_bytes": raw_gzip_bytes,
        "raw_pointer": raw_pointer,
        "observation_records": observation_records,
    }


def _write_once(path: Path, data: bytes) -> bool:
    """Write ``data`` if absent. Returns True if this call created the file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            fail("APPEND_ONLY_COLLISION")
        return False
    path.write_bytes(data)
    return True


def _write_observation_once(path: Path, data: bytes) -> bool:
    """Write an observation record only if this exact (observation_date,
    value, kind_suffix) path has never been written before.

    Unlike ``_write_once`` (used for the raw archive/manifest, where the
    path itself IS the content hash so any existing file is guaranteed
    byte-identical), an observation record's path is derived from only
    ``observation_date`` + a hash of ``value`` -- deliberately excluding
    ``availability_captured_at_utc``/``batch_revision_id``, which differ
    every time the *same* (date, value) pair is legitimately re-observed
    on a later day (exactly what the bounded write window in
    ``compute_write_from_date`` does every run for its trailing window).
    Re-observing something already on disk is therefore expected and
    harmless, not a tamper signal: this function silently keeps the
    existing file (its earlier, tighter ``availability_captured_at_utc``)
    and never rewrites it. A genuine byte-for-byte identical write is also
    a no-op. Neither case raises ``APPEND_ONLY_COLLISION`` -- that error is
    reserved for the raw archive/manifest path, where any mismatch really
    would mean tampering.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file():
            fail("APPEND_ONLY_COLLISION")
        try:
            existing = json.loads(path.read_bytes())
            incoming = json.loads(data)
        except (UnicodeDecodeError, json.JSONDecodeError):
            fail("APPEND_ONLY_COLLISION")
        identity_fields = (
            "series_id", "observation_date", "value", "availability_kind", "source_kind",
        )
        if any(existing.get(f) != incoming.get(f) for f in identity_fields):
            # The path is derived from observation_date + a hash of value,
            # so this should be unreachable outside a hash collision or
            # direct file corruption -- treat it exactly like a genuine
            # append-only tamper, unlike the expected/harmless case above
            # where only availability_captured_at_utc/batch_revision_id
            # differ across two legitimate re-observations of the same
            # (date, value) pair.
            fail("APPEND_ONLY_COLLISION")
        return False
    path.write_bytes(data)
    return True


def _safe_evidence_path(root: Path, value: object, prefix: str) -> Path:
    if not isinstance(value, str) or Path(value).is_absolute() or ".." in Path(value).parts:
        fail("EVIDENCE_PATH_INVALID")
    if not value.startswith(prefix):
        fail("EVIDENCE_PATH_INVALID")
    resolved_root = root.resolve()
    resolved = (root / value).resolve()
    if resolved_root not in resolved.parents:
        fail("EVIDENCE_PATH_INVALID")
    return resolved


def publish_capture(root: Path, capture: dict) -> dict:
    manifest_path = _safe_evidence_path(
        root, capture["raw_pointer"]["manifest_path"], f"{EVIDENCE_ROOT}/raw/"
    )
    raw_path = _safe_evidence_path(
        root, capture["raw_pointer"]["raw_path"], f"{EVIDENCE_ROOT}/raw/"
    )
    _write_once(raw_path, capture["raw_gzip_bytes"])
    _write_once(manifest_path, capture["manifest_bytes"])

    new_paths = []
    for entry in capture["observation_records"]:
        record_path = _safe_evidence_path(
            root, entry["record_path"], f"{EVIDENCE_ROOT}/observations/"
        )
        if _write_observation_once(record_path, entry["record_bytes"]):
            new_paths.append(entry["record_path"])

    return {
        "raw_pointer": capture["raw_pointer"],
        "observation_count": len(capture["observation_records"]),
        "new_observation_paths": new_paths,
    }


# ─────────────────────────────────────────────────────────────────────────
# Reader -- RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1 point-in-time value.
# ─────────────────────────────────────────────────────────────────────────

def business_days_between(start_date: dt.date, end_date: dt.date) -> int:
    """Weekday (Mon-Fri) count strictly after ``start_date`` up to and
    including ``end_date``. Matches config/paper_execution_core_v1.json's
    ``fx_conversion.staleness_clock``:
    WEEKDAYS_MON_FRI_AFTER_LATEST_PUBLICATION_UTC_DATE_UP_TO_DECISION_UTC_DATE.
    """
    if end_date < start_date:
        fail("DECISION_BEFORE_AVAILABILITY")
    count = 0
    cursor = start_date
    while cursor < end_date:
        cursor += dt.timedelta(days=1)
        if cursor.weekday() < 5:  # Mon=0 .. Fri=4
            count += 1
    return count


def _load_observation_records(root: Path) -> list[dict]:
    observations_dir = root / EVIDENCE_ROOT / "observations"
    if not observations_dir.is_dir():
        return []
    records = []
    for path in sorted(observations_dir.glob("*/*.json")):
        try:
            records.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            fail("OBSERVATION_RECORD_UNREADABLE")
    return records


def latest_committed_captured_date(root: Path) -> str | None:
    """Max ``observation_date`` among already-written ``CAPTURED`` records,
    or ``None`` if none exist yet. Used to bound a normal capture's write
    window -- see ``compute_write_from_date``."""
    dates = [
        r["observation_date"] for r in _load_observation_records(root)
        if r.get("availability_kind") == "CAPTURED"
    ]
    return max(dates) if dates else None


def compute_write_from_date(
    today: dt.date, latest_committed: str | None, *, window_days: int = RECENT_WINDOW_DAYS
) -> str:
    """Earliest observation_date (inclusive) a normal capture should write.

    ``min(latest_committed, today - window_days)`` when something is
    already committed -- this reaches all the way back to the day after
    the last commit even if that is further back than the window (so a
    genuine gap in the daily schedule is never silently skipped), while
    still re-considering the trailing window every run to catch a late
    revision to a recently published value. With nothing committed yet,
    it is just the window start -- a normal run never reaches for full
    history; only ``--backfill`` does that, deliberately.
    """
    window_start = today - dt.timedelta(days=window_days)
    if latest_committed is None:
        return window_start.isoformat()
    latest_committed_date = _parse_date(latest_committed, "LATEST_COMMITTED_DATE_INVALID")
    return min(latest_committed_date, window_start).isoformat()


def latest_available(root: Path, decision_at: str) -> dict:
    """Latest DEXKOUS value with availability <= ``decision_at`` (UTC).

    Only ``availability_kind == "CAPTURED"`` rows are point-in-time
    evidence; ``UNKNOWN_BACKFILL`` rows never satisfy "published before the
    decision time" (see module docstring) and are reported only as context.
    """
    decision = _parse_utc(decision_at, "DECISION_TIME_INVALID")
    records = _load_observation_records(root)
    backfill_only = [r for r in records if r.get("availability_kind") == "UNKNOWN_BACKFILL"]
    eligible = []
    for record in records:
        if record.get("availability_kind") != "CAPTURED":
            continue
        available_at = _parse_utc(
            record.get("availability_captured_at_utc"), "AVAILABILITY_TIME_INVALID"
        )
        if available_at <= decision:
            eligible.append((available_at, record))

    if not eligible:
        return {
            "status": "NO_POINT_IN_TIME_OBSERVATION_AVAILABLE",
            "backfill_only_rows_present": bool(backfill_only),
            "explanation": (
                "No FRED DEXKOUS observation has an availability_kind of "
                "CAPTURED with availability_captured_at_utc <= decision_at. "
                "UNKNOWN_BACKFILL rows exist only to seed history and are "
                "never usable as point-in-time evidence for a decision."
                if backfill_only else
                "No FRED DEXKOUS capture has been recorded yet."
            ),
        }

    best_date = max(dt.date.fromisoformat(r["observation_date"]) for _, r in eligible)
    same_date = [(t, r) for t, r in eligible if r["observation_date"] == best_date.isoformat()]
    availability_at, record = max(same_date, key=lambda pair: pair[0])

    business_days = business_days_between(availability_at.date(), decision.date())
    stale = business_days > STALENESS_MAX_BUSINESS_DAYS
    return {
        "status": "OK",
        "series_id": SERIES_ID,
        "observation_date": record["observation_date"],
        "value": record["value"],
        "availability_captured_at_utc": record["availability_captured_at_utc"],
        "source_kind": record["source_kind"],
        "business_days_since_availability": business_days,
        "staleness_max_business_days": STALENESS_MAX_BUSINESS_DAYS,
        "staleness_clock_kind": STALENESS_CLOCK_KIND,
        "stale": stale,
        "display": STALENESS_DISPLAY if stale else None,
    }


# ─────────────────────────────────────────────────────────────────────────
# CLI entrypoint -- workflow_dispatch only (see
# .github/workflows/fred-dexkous-fx.yml). No cron is enabled by this PR.
# ─────────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--backfill", action="store_true",
        help="Mark every captured observation availability_kind=UNKNOWN_BACKFILL "
             "instead of CAPTURED (one-time historical seed only). Writes the "
             "full parsed history, ignoring --window-days.",
    )
    parser.add_argument("--observation-start", default=None, help="FRED API only.")
    parser.add_argument(
        "--window-days", type=int, default=RECENT_WINDOW_DAYS,
        help="Normal (non-backfill) capture only -- see compute_write_from_date().",
    )
    args = parser.parse_args(argv)

    api_key = os.getenv("FRED_API_KEY", "").strip() or None
    captured_at = dt.datetime.now(tz=UTC)

    if args.backfill:
        raw, source_kind = fetch_dexkous(api_key, observation_start=args.observation_start)
        capture = build_capture(captured_at, raw, source_kind, is_backfill=True)
    else:
        latest_committed = latest_committed_captured_date(ROOT)
        write_from = compute_write_from_date(
            captured_at.date(), latest_committed, window_days=args.window_days
        )
        observation_start = args.observation_start or write_from
        raw, source_kind = fetch_dexkous(api_key, observation_start=observation_start)
        capture = build_capture(
            captured_at, raw, source_kind, is_backfill=False, write_observations_from=write_from,
        )

    summary = publish_capture(ROOT, capture)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
