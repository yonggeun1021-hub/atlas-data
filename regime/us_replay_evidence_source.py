#!/usr/bin/env python3
"""Read the committed US free-source history store and answer replay requests from it.

``regime/us_historical_replay_population.py`` rebuilt every replayed date by
issuing live Alpaca/FRED requests. The capture those requests would have to
reach for is already in this repository, committed by
``collectors/free_market_data_history.py`` under
``evidence/free_market_data/history/`` -- append-only, content-addressed, and
carrying the ALFRED *vintage* rows rather than the current revision. Nothing
read it.

This module is that read path, and only a read path:

* It opens the committed store, verifies every object it uses against its own
  content address, and writes nothing anywhere. There is no code path here that
  creates, moves, or modifies a file.
* It answers the *same request URLs* the population module already builds, so
  the replay's fetch code, OHLC validation, decimal parsing, session-return
  math, axis arithmetic, lookahead binds and record shape are reused
  unmodified. Nothing about a score is re-implemented here.
* It never falls back to the network. A URL it does not recognise, a symbol or
  series the store does not hold, or a request shape it cannot answer exactly
  fails closed.

Point-in-time integrity of what it answers:

* **FRED** observations are resolved only through
  ``collectors/free_market_data_history.py::observations_available_at``, which
  keeps a row only while ``available_from <= as_of <= available_to``. A revision
  published after the replayed date is therefore invisible to that date, not
  trimmed afterwards. The requested ALFRED vintage is required to be pinned to a
  single day (``realtime_start == realtime_end``), which is exactly what
  ``_fred_query`` asks for, and that day is the as-of. Each answered row carries
  its own publication window, so the population's own returned-vintage bind has
  something real to bind.
* **Alpaca** bars are filtered to the requested ``start``/``end`` window and
  truncated to the requested ``limit`` from the end, so a request anchored to a
  replayed date can only see sessions at or before it.
* The one point-in-time fact the store does **not** hold is the per-vintage FRED
  *units* string: it holds one series-metadata capture per series, taken at
  capture time. This module serves exactly those captured bytes -- their sha256
  is the committed content address -- and the population, in
  ``SOURCE_MODE_EVIDENCE``, refuses to normalize with them and discloses the
  gap per row instead of substituting a later revision. See
  ``us_historical_replay_population.UNITS_VINTAGE_UNAVAILABLE``.

The replayable window is derived, never declared here:

* ``session_intersection`` is the set of sessions on which *every* replay symbol
  in ``config/free_market_data_contract.json`` has a bar. If the store grows,
  the intersection grows with it; if a symbol is short, the intersection shrinks
  and ``summary`` says so.
* ``lead_sessions_required`` is ``max(alpaca.return_windows_sessions) + 1``,
  read from that contract. The operational producer
  ``collectors/free_market_data.py::derive_us_market_reference`` computes every
  configured window, and ``_session_return(closes, n)`` needs ``n + 1`` closes,
  so today's ``[5, 20, 60]`` means **61** bars of lead, not 21.
  ``collectors/free_market_data_history.py::LEAD_BARS_REQUIRED`` is 21 because
  it describes a 20-session *scoring window*, and the capture receipts'
  ``feasibility`` blocks inherit that number; scoring the axis set the replay
  actually builds needs the 61 this module derives. Both numbers are reported by
  ``summary`` so the difference is visible rather than assumed.
* ``regime/us_replay_range_declaration.py`` independently fixes the ratified
  replay range under the same 61-session rule from a separate probe. ``summary``
  reports the declared range beside the derived one; it does not replace it, and
  this module never selects a sub-range of anything.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import sys
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from collectors import free_market_data as FMD  # noqa: E402
from collectors import free_market_data_history as HIST  # noqa: E402


HISTORY_STORE_REL = "evidence/free_market_data/history"
ALPACA_SERIES_FILE = "alpaca_daily_bars_series.json"
ALPACA_SERIES_SCHEMA = "alpaca_daily_bars_range_series/1"
FRED_AVAILABILITY_FILE = "fred_alfred_availability.json"
FRED_AVAILABILITY_SCHEMA = "fred_alfred_availability_series/1"
FRED_METADATA_FILE = "fred_series_metadata.json.gz"
FRED_METADATA_SCHEMA = "fred_series_metadata/1"
ALPACA_HOST = "data.alpaca.markets"
FRED_HOST = "api.stlouisfed.org"
STORE_DESCRIPTOR_SCHEMA = "us_replay_evidence_store/1"
SUMMARY_SCHEMA = "us_replay_evidence_source_summary/1"
DATE10 = "%Y-%m-%d"


class ReplayEvidenceSourceError(ValueError):
    """The committed history store cannot answer this replay request."""


def fail(code: str, detail: str = "") -> None:
    raise ReplayEvidenceSourceError(f"{code}:{detail}" if detail else code)


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _date(value: object, code: str) -> dt.date:
    if not isinstance(value, str):
        fail(code, str(value))
    try:
        return dt.datetime.strptime(value, DATE10).date()
    except ValueError:
        fail(code, value)


def _read_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail("EVIDENCE_READ_FAILED", f"{path}:{exc}")


def _verify_derived(payload: object, path: Path) -> dict:
    """A derived payload must hash to its own recorded content address.

    Rebuilt the same way ``free_market_data_history.build_derived_object``
    computes it, and the directory the file sits in must be that same digest --
    so a payload edited in place fails here rather than being replayed as
    evidence.
    """
    if not isinstance(payload, dict):
        fail("EVIDENCE_PAYLOAD_INVALID", str(path))
    claimed = payload.get("payload_sha256")
    body = {key: value for key, value in payload.items() if key != "payload_sha256"}
    if not isinstance(claimed, str) or _sha256(_canonical(body)) != claimed:
        fail("EVIDENCE_PAYLOAD_SHA_MISMATCH", str(path))
    if path.parent.name != claimed:
        fail("EVIDENCE_PAYLOAD_NOT_CONTENT_ADDRESSED", str(path))
    return payload


def _verify_raw_gzip(path: Path) -> bytes:
    """The decompressed bytes of a raw object must hash to its content address."""
    try:
        raw = gzip.open(path, "rb").read()
    except OSError as exc:
        fail("EVIDENCE_READ_FAILED", f"{path}:{exc}")
    manifest = _read_json(path.parent / "manifest.json")
    if not isinstance(manifest, dict):
        fail("EVIDENCE_MANIFEST_INVALID", str(path))
    claimed = manifest.get("raw_response_sha256")
    actual = _sha256(raw)
    if claimed != actual or path.parent.name != actual:
        fail("EVIDENCE_RAW_SHA_MISMATCH", str(path))
    return raw


def _one_child(parent: Path, name: str, code: str) -> Path:
    """The single content-addressed object of a kind, or a fail-closed error.

    Deliberately not "the newest" or "the first": the store is append-only, so
    two objects of the same kind for the same series is a state this read path
    has no rule for choosing between, and silently picking one would make the
    replay depend on directory order.
    """
    if not parent.is_dir():
        fail(code, str(parent))
    found = sorted(child / name for child in parent.iterdir() if (child / name).is_file())
    if len(found) != 1:
        fail(code, f"{parent}:{len(found)}")
    return found[0]


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class EvidenceStore:
    """The committed history store, opened read-only and content-verified.

    Holds the bars and ALFRED vintage rows in memory for the life of one replay
    run. Nothing here is mutated after load, and nothing is written to disk.
    """

    def __init__(self, root: Path, contract: dict):
        self.root = Path(root)
        self.store_dir = self.root / HISTORY_STORE_REL
        if not self.store_dir.is_dir():
            fail("EVIDENCE_STORE_MISSING", str(self.store_dir))
        self.contract = contract
        alpaca = contract["alpaca"]
        self.symbols = sorted(
            set(alpaca["trend_symbols"]) | set(alpaca["sector_reference_symbols"])
        )
        self.return_windows = list(alpaca["return_windows_sessions"])
        self.feed = alpaca["feed"]
        self.fred_series = list(contract["fred"]["series"])
        self.bars: dict[str, list[dict]] = {}
        self.series_meta: dict[str, dict] = {}
        self.fred_rows: dict[str, list[dict]] = {}
        self.fred_availability: dict[str, dict] = {}
        self.fred_metadata_raw: dict[str, bytes] = {}
        self._load_alpaca()
        self._load_fred()

    # -- load ---------------------------------------------------------------

    def _load_alpaca(self) -> None:
        base = self.store_dir / "alpaca" / "series"
        for symbol in self.symbols:
            path = _one_child(
                base / symbol, ALPACA_SERIES_FILE, "EVIDENCE_ALPACA_SERIES_MISSING",
            )
            payload = _verify_derived(_read_json(path), path)
            if (
                payload.get("schema_version") != ALPACA_SERIES_SCHEMA
                or payload.get("symbol") != symbol
                or payload.get("feed") != self.feed
                or payload.get("timeframe") != "1Day"
                or payload.get("adjustment") != "raw"
            ):
                fail("EVIDENCE_ALPACA_SERIES_INVALID", symbol)
            bars = payload.get("bars")
            if not isinstance(bars, list) or not bars:
                fail("EVIDENCE_ALPACA_SERIES_INVALID", symbol)
            rows = []
            previous = None
            for bar in bars:
                if not isinstance(bar, dict) or bar.get("symbol") != symbol:
                    fail("EVIDENCE_ALPACA_BAR_INVALID", symbol)
                session = _date(
                    str(bar.get("opened_at", ""))[:10], "EVIDENCE_ALPACA_BAR_INVALID",
                )
                # Ordering is not assumed: every window filter and every
                # ``limit`` truncation below reads "the last N at or before the
                # anchor", which is only that if the series really is ascending.
                if previous is not None and session <= previous:
                    fail("EVIDENCE_ALPACA_SERIES_NOT_ASCENDING", f"{symbol}:{session}")
                previous = session
                rows.append({"session_date": session.isoformat(), "bar": bar})
            self.bars[symbol] = rows
            self.series_meta[symbol] = {
                "payload_sha256": payload["payload_sha256"],
                "bar_count": len(rows),
                "first_session_date": rows[0]["session_date"],
                "last_session_date": rows[-1]["session_date"],
            }

    def _load_fred(self) -> None:
        base = self.store_dir / "fred" / "alfred"
        for series_id in self.fred_series:
            path = _one_child(
                base / series_id / "availability", FRED_AVAILABILITY_FILE,
                "EVIDENCE_FRED_AVAILABILITY_MISSING",
            )
            payload = _verify_derived(_read_json(path), path)
            if (
                payload.get("schema_version") != FRED_AVAILABILITY_SCHEMA
                or payload.get("series_id") != series_id
            ):
                fail("EVIDENCE_FRED_AVAILABILITY_INVALID", series_id)
            rows = payload.get("rows")
            if not isinstance(rows, list) or not rows:
                fail("EVIDENCE_FRED_AVAILABILITY_INVALID", series_id)
            self.fred_rows[series_id] = rows
            self.fred_availability[series_id] = {
                "payload_sha256": payload["payload_sha256"],
                "row_count": len(rows),
                "earliest_availability_date": payload.get("earliest_availability_date"),
                "latest_availability_date": payload.get("latest_availability_date"),
                "vintage_semantics": payload.get("vintage_semantics"),
            }
            metadata_path = _one_child(
                base / series_id / "metadata", FRED_METADATA_FILE,
                "EVIDENCE_FRED_METADATA_MISSING",
            )
            raw = _verify_raw_gzip(metadata_path)
            body = json.loads(raw.decode("utf-8"))
            rows_out = body.get("seriess") if isinstance(body, dict) else None
            if not isinstance(rows_out, list) or len(rows_out) != 1:
                fail("EVIDENCE_FRED_METADATA_INVALID", series_id)
            if rows_out[0].get("id") != series_id:
                fail("EVIDENCE_FRED_METADATA_INVALID", series_id)
            self.fred_metadata_raw[series_id] = raw

    # -- derived window -----------------------------------------------------

    @property
    def lead_sessions_required(self) -> int:
        """Bars of lead one scored session needs, derived from the contract.

        ``_session_return(closes, n)`` requires ``n + 1`` closes and
        ``derive_us_market_reference`` computes every configured window, so the
        binding requirement is the longest window plus one. Never a literal:
        a contract that adds a longer window must move this number with it.
        """
        return max(self.return_windows) + 1

    def session_intersection(self) -> list[str]:
        """Sessions on which every replay symbol has a bar, ascending."""
        common: set[str] | None = None
        for symbol in self.symbols:
            dates = {row["session_date"] for row in self.bars[symbol]}
            common = dates if common is None else (common & dates)
        return sorted(common or set())

    def scoreable_sessions(self) -> list[str]:
        """Intersection sessions that have the derived lead behind them."""
        sessions = self.session_intersection()
        lead = self.lead_sessions_required
        return sessions[lead - 1:]

    def descriptor(self) -> dict:
        """What a population records about the store it read.

        Content addresses and counts only: no bar, no observation value, and no
        capture-time date that would read as a source date inside a record.
        """
        sessions = self.session_intersection()
        scoreable = self.scoreable_sessions()
        return {
            "schema_version": STORE_DESCRIPTOR_SCHEMA,
            "path": HISTORY_STORE_REL,
            "read_only": True,
            "alpaca": {
                "feed": self.feed,
                "symbol_count": len(self.symbols),
                "symbols": list(self.symbols),
                "series": dict(self.series_meta),
            },
            "fred": {
                "series": list(self.fred_series),
                "availability": dict(self.fred_availability),
                "metadata_response_sha256": {
                    series_id: _sha256(raw)
                    for series_id, raw in sorted(self.fred_metadata_raw.items())
                },
                "observations_resolved_by": (
                    "collectors/free_market_data_history.py"
                    "::observations_available_at"
                ),
            },
            "derived_window": {
                "return_windows_sessions": list(self.return_windows),
                "lead_sessions_required": self.lead_sessions_required,
                "lead_sessions_required_derivation": (
                    "max(config/free_market_data_contract.json"
                    "#alpaca.return_windows_sessions) + 1"
                ),
                "session_intersection_count": len(sessions),
                "session_intersection_first": sessions[0] if sessions else None,
                "session_intersection_last": sessions[-1] if sessions else None,
                "scoreable_session_count": len(scoreable),
                "scoreable_session_first": scoreable[0] if scoreable else None,
                "scoreable_session_last": scoreable[-1] if scoreable else None,
            },
        }


# ---------------------------------------------------------------------------
# Getter
# ---------------------------------------------------------------------------


class EvidenceGetter:
    """Answer the population module's own request URLs from the store.

    Same call signatures the live ``collectors/free_market_data._get`` has, so
    it drops straight into ``build_population(..., getter=...)``: Alpaca is
    called with ``(url, headers)`` and FRED with ``(url)``.

    Every answer is built with sorted keys and compact separators, so the same
    store and the same request produce byte-identical response bytes and
    therefore a byte-identical ``response_sha256`` on every run.
    """

    def __init__(self, store: EvidenceStore):
        self.store = store
        self.calls = {"alpaca_bars": 0, "fred_observations": 0, "fred_metadata": 0}

    def __call__(self, url: str, headers: dict | None = None) -> bytes:
        parsed = urllib.parse.urlparse(str(url))
        query = dict(urllib.parse.parse_qsl(parsed.query))
        if parsed.netloc == ALPACA_HOST:
            return self._alpaca_bars(parsed.path, query)
        if parsed.netloc == FRED_HOST:
            if parsed.path == "/fred/series/observations":
                return self._fred_observations(query)
            if parsed.path == "/fred/series":
                return self._fred_metadata(query)
        # Never a network fallback: an unrecognised request must surface as a
        # replay that could not be answered from evidence, not as a live call.
        fail("EVIDENCE_REQUEST_UNSUPPORTED", f"{parsed.netloc}{parsed.path}")

    # -- alpaca -------------------------------------------------------------

    def _alpaca_bars(self, path: str, query: dict) -> bytes:
        parts = [part for part in path.split("/") if part]
        if len(parts) != 4 or parts[0] != "v2" or parts[1] != "stocks" or parts[3] != "bars":
            fail("EVIDENCE_REQUEST_UNSUPPORTED", path)
        symbol = parts[2]
        if symbol not in self.store.bars:
            fail("EVIDENCE_ALPACA_SYMBOL_NOT_IN_STORE", symbol)
        if query.get("feed") != self.store.feed:
            fail("EVIDENCE_ALPACA_FEED_MISMATCH", str(query.get("feed")))
        if query.get("timeframe") != "1Day" or query.get("adjustment") != "raw":
            fail("EVIDENCE_ALPACA_REQUEST_SHAPE_INVALID", symbol)
        start = _date(str(query.get("start", ""))[:10], "EVIDENCE_ALPACA_START_INVALID")
        end = _date(str(query.get("end", ""))[:10], "EVIDENCE_ALPACA_END_INVALID")
        try:
            limit = int(query.get("limit", "0"))
        except (TypeError, ValueError):
            limit = 0
        if limit <= 0:
            fail("EVIDENCE_ALPACA_LIMIT_INVALID", symbol)
        window = [
            row["bar"] for row in self.store.bars[symbol]
            if start.isoformat() <= row["session_date"] <= end.isoformat()
        ]
        # ``limit`` is applied from the end, matching what the provider returns
        # for an ascending, limited window ending at the anchor.
        window = window[-limit:]
        self.calls["alpaca_bars"] += 1
        # Values are passed through as the committed decimal *text*, not as
        # floats: a float round trip would change the close text the axis
        # arithmetic later reads, so the replayed close would no longer be the
        # committed one.
        bars = [
            {
                "t": bar["opened_at"],
                "o": bar["open"],
                "h": bar["high"],
                "l": bar["low"],
                "c": bar["close"],
                "v": bar["volume"],
            }
            for bar in window
        ]
        return _canonical({
            "bars": bars,
            "next_page_token": None,
            "symbol": symbol,
        })

    # -- fred ---------------------------------------------------------------

    def _as_of(self, query: dict) -> str:
        """The single ALFRED vintage day this request pinned.

        The population pins ``realtime_start`` and ``realtime_end`` to the
        replayed date on every FRED request. A request that does not pin a
        single day has no as-of to resolve against and is refused rather than
        answered with the store's widest window.
        """
        start = _date(query.get("realtime_start"), "EVIDENCE_FRED_VINTAGE_INVALID")
        end = _date(query.get("realtime_end"), "EVIDENCE_FRED_VINTAGE_INVALID")
        if start != end:
            fail("EVIDENCE_FRED_VINTAGE_NOT_A_SINGLE_DAY", f"{start}:{end}")
        return start.isoformat()

    def _fred_observations(self, query: dict) -> bytes:
        series_id = query.get("series_id")
        if series_id not in self.store.fred_rows:
            fail("EVIDENCE_FRED_SERIES_NOT_IN_STORE", str(series_id))
        as_of = self._as_of(query)
        observation_start = _date(
            query.get("observation_start"), "EVIDENCE_FRED_OBSERVATION_WINDOW_INVALID",
        ).isoformat()
        observation_end = _date(
            query.get("observation_end"), "EVIDENCE_FRED_OBSERVATION_WINDOW_INVALID",
        ).isoformat()
        if observation_end != as_of:
            # ``_fred_query`` pins ``observation_end`` to the replayed date too.
            # A mismatch means this is not the request shape this path answers.
            fail("EVIDENCE_FRED_OBSERVATION_END_NOT_THE_AS_OF", f"{series_id}:{as_of}")
        try:
            visible = HIST.observations_available_at(self.store.fred_rows[series_id], as_of)
        except HIST.FreeMarketDataError as exc:
            fail("EVIDENCE_FRED_AVAILABILITY_UNRESOLVABLE", f"{series_id}:{exc}")
        self.calls["fred_observations"] += 1
        observations = [
            {
                "date": row["observation_date"],
                "realtime_end": row["available_to"],
                "realtime_start": row["available_from"],
                "value": row["value"],
            }
            for row in visible
            if observation_start <= row["observation_date"] <= observation_end
        ]
        return _canonical({
            "observation_end": observation_end,
            "observation_start": observation_start,
            "observations": observations,
            "realtime_end": as_of,
            "realtime_start": as_of,
        })

    def _fred_metadata(self, query: dict) -> bytes:
        """The captured series-metadata response, byte for byte.

        Returned unmodified so its sha256 is the store's own content address for
        it. It is *not* the metadata vintage of the replayed date, and the
        population knows that: under ``SOURCE_MODE_EVIDENCE`` it refuses to
        normalize with these units and discloses the gap per liquidity row
        rather than letting a capture-time units string reach an earlier date.
        """
        series_id = query.get("series_id")
        if series_id not in self.store.fred_metadata_raw:
            fail("EVIDENCE_FRED_SERIES_NOT_IN_STORE", str(series_id))
        self._as_of(query)
        self.calls["fred_metadata"] += 1
        return self.store.fred_metadata_raw[series_id]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


EVIDENCE_CREDENTIALS = {
    # The replay's credential checks assert "a market-data credential was
    # supplied at all" before a fetch. Evidence mode issues no network request,
    # so these are explicit non-secret placeholders rather than real keys, and
    # nothing here reads the environment.
    "fred_key": "COMMITTED_EVIDENCE_STORE",
    "alpaca_key": "COMMITTED_EVIDENCE_STORE",
    "alpaca_secret": "COMMITTED_EVIDENCE_STORE",
}


def open_store(root: Path = ROOT) -> EvidenceStore:
    return EvidenceStore(root, FMD.load_contract(FMD.CONTRACT_PATH))


def summary(root: Path = ROOT) -> dict:
    """Derived coverage of the committed store, plus the declared range beside it."""
    store = open_store(root)
    sessions = store.session_intersection()
    scoreable = store.scoreable_sessions()
    window = store.lead_sessions_required
    declared = None
    try:
        from regime import us_replay_range_declaration as DECL

        declaration = DECL.load_declaration(DECL.DEFAULT_DECLARATION_PATH)
        declared = {
            "status": declaration.get("status"),
            "first_replay_date": declaration.get("range", {}).get("first_replay_date"),
            "last_replay_date": declaration.get("range", {}).get("last_replay_date"),
            "session_count": declaration.get("range", {}).get("session_count"),
        }
    except Exception as exc:  # noqa: BLE001 — a summary must not depend on it.
        declared = {"status": f"UNAVAILABLE:{type(exc).__name__}"}
    return {
        "schema_version": SUMMARY_SCHEMA,
        "store": store.descriptor(),
        "per_symbol_session_counts": {
            symbol: meta["bar_count"] for symbol, meta in sorted(store.series_meta.items())
        },
        "session_intersection": {
            "count": len(sessions),
            "first": sessions[0] if sessions else None,
            "last": sessions[-1] if sessions else None,
        },
        "lead_sessions": {
            "required_by_replay": window,
            "derivation": (
                "max(alpaca.return_windows_sessions) + 1; _session_return(closes, n)"
                " needs n+1 closes and derive_us_market_reference computes every"
                " configured window"
            ),
            "capture_receipt_feasibility_lead_bars": HIST.LEAD_BARS_REQUIRED,
            "capture_receipt_feasibility_describes": (
                "a 20-session scoring window only, not the 60-session return the"
                " replay also computes"
            ),
        },
        "scoreable_sessions": {
            "count": len(scoreable),
            "first": scoreable[0] if scoreable else None,
            "last": scoreable[-1] if scoreable else None,
            "non_overlapping_20_session_windows": len(scoreable) // HIST.WINDOW_SESSIONS,
        },
        "declared_range": declared,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["summary"],
        help="summary: derived coverage of the committed store. Reads only.",
    )
    args = parser.parse_args()
    if args.command == "summary":
        print(json.dumps(summary(), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
