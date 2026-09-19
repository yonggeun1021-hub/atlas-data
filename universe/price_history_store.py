#!/usr/bin/env python3
"""Market-agnostic reader/writer for the ``price_history_session/1`` store.

The store itself lives in the PRIVATE evidence repository; this module only
knows the layout and the rules, and takes its root as a parameter.  Nothing
in the public repository ever holds a raw or compact price byte.

Every entry point takes ``market`` as its first argument and reads the
per-market block of ``config/price_history_contract.json``.  A later US
window reuses this file unchanged: it adds a ``sources.US`` block and its
own collector, not a second store.

The one rule that shapes the whole reader: **a gap stays a gap**.  A session
with no stored row for a code is simply absent from that code's series.  No
value is carried forward, interpolated, padded, or back-filled to make a
window look complete, and ``series`` never returns more rows than are on
disk.
"""
from __future__ import annotations

import copy
import datetime as dt
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[1]


class PriceHistoryStoreError(ValueError):
    """Stable failure code; never carries a raw payload."""


def _collector(root: Path = ROOT):
    cached = sys.modules.get("price_history_store_collector")
    if cached is not None:
        return cached
    path = Path(root) / "collectors" / "krx_price_history.py"
    spec = importlib.util.spec_from_file_location("price_history_store_collector", path)
    if spec is None or spec.loader is None:
        raise PriceHistoryStoreError("COLLECTOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules["price_history_store_collector"] = module
    spec.loader.exec_module(module)
    return module


def _market_behavior(root: Path = ROOT):
    cached = sys.modules.get("price_history_store_market_behavior")
    if cached is not None:
        return cached
    path = Path(root) / "discovery" / "market_behavior.py"
    spec = importlib.util.spec_from_file_location(
        "price_history_store_market_behavior", path
    )
    if spec is None or spec.loader is None:
        raise PriceHistoryStoreError("MARKET_BEHAVIOR_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    sys.modules["price_history_store_market_behavior"] = module
    spec.loader.exec_module(module)
    return module


def canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class PriceHistoryStore:
    """Reader over a ``price_history/`` tree, plus the append-only index writer."""

    def __init__(
        self,
        store_root: Path | str,
        *,
        contract: Mapping[str, Any] | None = None,
        code_root: Path = ROOT,
    ):
        self.store_root = Path(store_root)
        self.code_root = Path(code_root)
        self.collector = _collector(self.code_root)
        self.contract = dict(contract) if contract is not None else self.collector.load_contract()

    # ------------------------------------------------------------ layout

    def _source(self, market: str) -> dict:
        return self.collector.market_source(self.contract, market)

    def market_root(self, market: str) -> Path:
        self._source(market)
        return self.store_root / "price_history" / market

    def session_dir(self, market: str, bas_dd: str) -> Path:
        day = self.collector.validate_bas_dd(bas_dd)
        return self.market_root(market) / day[0:4] / day

    def index_path(self, market: str) -> Path:
        return self.market_root(market) / "index.jsonl"

    # ------------------------------------------------------------ reading

    def manifest(self, market: str, bas_dd: str) -> dict:
        path = self.session_dir(market, bas_dd) / "manifest.json"
        try:
            value = json.loads(path.read_bytes().decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise PriceHistoryStoreError(
                f"MANIFEST_UNREADABLE:{market}:{bas_dd}"
            ) from exc
        return self.collector.validate_manifest(value, self.contract)

    def stored_sessions(self, market: str) -> list[str]:
        """Every ``bas_dd`` with a manifest on disk, ascending -- OK and EMPTY."""
        root = self.market_root(market)
        if not root.is_dir():
            return []
        found = []
        for year_dir in sorted(p for p in root.iterdir() if p.is_dir()):
            for day_dir in sorted(p for p in year_dir.iterdir() if p.is_dir()):
                if (day_dir / "manifest.json").is_file():
                    found.append(day_dir.name)
        return sorted(found)

    def sessions_available_at(self, market: str, t: str | dt.datetime) -> list[str]:
        """Sessions whose data was observably available at or before ``t``.

        ``EMPTY`` sessions are recorded history but carry no data, so they are
        never returned as an available session.  A session whose
        ``first_available_observed_at_utc`` is after ``t`` is withheld -- this
        is the point-in-time boundary, not a convenience filter.
        """
        instant = self._instant(t)
        available = []
        for day in self.stored_sessions(market):
            manifest = self.manifest(market, day)
            if manifest["status"] != "OK":
                continue
            first = manifest.get("first_available_observed_at_utc")
            if first is None:
                continue
            if self.collector.parse_utc(first) <= instant:
                available.append(day)
        return available

    def compact_rows(self, market: str, bas_dd: str) -> list[dict]:
        path = self.session_dir(market, bas_dd) / "compact.jsonl.gz"
        try:
            raw = gzip.decompress(path.read_bytes())
        except (OSError, gzip.BadGzipFile, EOFError) as exc:
            raise PriceHistoryStoreError(
                f"COMPACT_UNREADABLE:{market}:{bas_dd}"
            ) from exc
        return self.collector.parse_compact(raw, self.contract)

    def session_index(self, market: str, bas_dd: str) -> dict[str, dict]:
        return {row["code"]: row for row in self.compact_rows(market, bas_dd)}

    def series(
        self, market: str, code: str, n: int, t: str | dt.datetime
    ) -> list[dict]:
        """The stored rows for ``code`` over the most recent ``n`` sessions at ``t``.

        Returns exactly what is on disk: ascending by ``bas_dd``, one entry per
        session that actually holds a row for this code.  A session in the
        window with no row for the code contributes nothing -- the gap is
        visible as a shorter result, never hidden behind a fabricated bar.
        """
        if not isinstance(n, int) or n <= 0:
            raise PriceHistoryStoreError("SERIES_LENGTH_INVALID")
        window = self.sessions_available_at(market, t)[-n:]
        rows = []
        for day in window:
            row = self.session_index(market, day).get(code)
            if row is None:
                continue
            rows.append({"bas_dd": day, **row})
        return rows

    def session_window(
        self, market: str, n: int, t: str | dt.datetime
    ) -> list[str]:
        """The last ``n`` *stored OK* sessions at ``t`` -- the sessions ``series`` draws on.

        Not calendar-aligned: an EMPTY or never-stored session inside the span
        is skipped, so the result can reach back more than ``n`` official
        sessions.  A rule defined over "the last n completed sessions" must use
        :meth:`calendar_window` instead.
        """
        if not isinstance(n, int) or n <= 0:
            raise PriceHistoryStoreError("SERIES_LENGTH_INVALID")
        return self.sessions_available_at(market, t)[-n:]

    def calendar_window(
        self, market: str, n: int, end_bas_dd: str, t: str | dt.datetime
    ) -> dict:
        """The official calendar's last ``n`` open sessions ending at ``end_bas_dd``,
        and which of them the store actually holds as OK at ``t``.

        ``missing`` lists every calendar session that is not available at
        ``t`` -- never stored, stored EMPTY, or first observed after ``t``.
        Nothing is skipped or back-filled from an older session.
        """
        if not isinstance(n, int) or n <= 0:
            raise PriceHistoryStoreError("SERIES_LENGTH_INVALID")
        sessions = self.collector.open_sessions_ending(
            end_bas_dd, n, self.contract, market=market, root=self.code_root
        )
        available = set(self.sessions_available_at(market, t))
        stored = set(self.stored_sessions(market))
        missing = [day for day in sessions if day not in available]
        return {
            "market": market,
            "end_bas_dd": sessions[-1],
            "sessions": sessions,
            "missing": missing,
            "empty": [day for day in missing if day in stored
                      and self.manifest(market, day)["status"] == "EMPTY"],
        }

    def rows_on_sessions(
        self, market: str, code: str, sessions: Iterable[str], t: str | dt.datetime
    ) -> list[dict]:
        """Stored rows for ``code`` on exactly ``sessions`` that are available at ``t``."""
        available = set(self.sessions_available_at(market, t))
        rows = []
        for day in sorted(set(sessions)):
            if day not in available:
                continue
            row = self.session_index(market, day).get(code)
            if row is not None:
                rows.append({"bas_dd": day, **row})
        return rows

    # -------------------------------------------------------- SMA readiness

    def sma_readiness(
        self,
        market: str,
        t: str | dt.datetime,
        *,
        sessions: int | None = None,
    ) -> dict:
        """How many codes now hold enough history for an ``n``-session SMA.

        A code counts only when it has a stored close in every one of the
        ``n`` sessions of the window.  A code with a gap is reported as not
        yet computable rather than averaged over fewer sessions.
        """
        window_length = int(sessions if sessions is not None else self.contract["sma20_sessions"])
        window = self.session_window(market, window_length, t)
        ready, short = [], {}
        if len(window) < window_length:
            return {
                "market": market,
                "as_of_utc": self.collector.utc_text(self._instant(t)),
                "sma_sessions": window_length,
                "available_sessions": len(window),
                "window": window,
                "sma_computable_symbol_count": 0,
                "status": "INSUFFICIENT_SESSIONS",
                "symbols_short_of_window": {},
            }
        present: dict[str, int] = {}
        for day in window:
            for row in self.compact_rows(market, day):
                if row.get("close") is not None:
                    present[row["code"]] = present.get(row["code"], 0) + 1
        for code, count in present.items():
            if count == window_length:
                ready.append(code)
            else:
                short[code] = count
        return {
            "market": market,
            "as_of_utc": self.collector.utc_text(self._instant(t)),
            "sma_sessions": window_length,
            "available_sessions": len(window),
            "window": window,
            "sma_computable_symbol_count": len(ready),
            "status": "COMPUTABLE",
            "symbols_short_of_window": dict(sorted(short.items())),
        }

    @staticmethod
    def simple_moving_average(rows: Iterable[Mapping[str, Any]], n: int):
        """Arithmetic mean of exactly ``n`` stored closes, or ``None``.

        Deliberately returns ``None`` -- never a shorter-window average --
        when the series has fewer than ``n`` closes.  A gap must surface as
        "not computable", not as a quietly different statistic.
        """
        from decimal import Decimal, InvalidOperation, localcontext

        closes = [row.get("close") for row in rows]
        if len(closes) != n or any(value is None for value in closes):
            return None
        try:
            values = [Decimal(str(value)) for value in closes]
        except InvalidOperation:
            return None
        if any(not value.is_finite() for value in values):
            return None
        with localcontext() as context:
            context.prec = 50
            return sum(values, Decimal(0)) / Decimal(n)

    # -------------------------------------------------- market_behavior reuse

    def market_behavior_window(
        self,
        market: str,
        codes: Iterable[str],
        benchmark_code: str,
        n: int,
        t: str | dt.datetime,
        *,
        window_id: str,
    ) -> dict:
        """Project stored sessions into a ``market_behavior_radar_input/1`` window.

        This is a projection, not a calculation.  Relative strength and the
        volume baseline are computed by ``discovery/market_behavior.py`` from
        the window this returns, so there is exactly one implementation of
        those formulas in the repository and none of it is restated here.

        Only codes present in every session of the window are included:
        ``market_behavior`` requires exact session coverage, and padding a
        short code to reach it would be fabrication.
        """
        source = self._source(market)
        window = self.session_window(market, n, t)
        if len(window) < n:
            raise PriceHistoryStoreError(
                f"WINDOW_SHORT:{len(window)}<{n}"
            )
        by_day = {day: self.session_index(market, day) for day in window}
        manifests = {day: self.manifest(market, day) for day in window}
        latest = manifests[window[-1]]
        wanted = sorted({str(code) for code in codes} | {str(benchmark_code)})
        series = []
        for code in wanted:
            rows = [by_day[day].get(code) for day in window]
            if any(
                row is None or row.get("close") is None or row.get("vol") is None
                for row in rows
            ):
                if code == benchmark_code:
                    raise PriceHistoryStoreError(f"BENCHMARK_WINDOW_INCOMPLETE:{code}")
                continue
            series.append(
                {
                    "asset_id": code,
                    "price_basis": "PROVIDER_CLOSE_UNADJUSTED",
                    "source_identity": {
                        "source_id": source["market_behavior_source_id"],
                        "source_url": latest["endpoint"][0]
                        + "?basDd="
                        + latest["bas_dd"],
                        "source_sha256": latest["raw_sha256"],
                        "available_at": latest["first_available_observed_at_utc"],
                        "retrieved_at_utc": latest["retrieved_at_utc"],
                    },
                    "rows": [
                        {
                            "session_date": self.collector.bas_dd_to_iso(day),
                            "close": by_day[day][code]["close"],
                            "volume": by_day[day][code]["vol"],
                        }
                        for day in window
                    ],
                }
            )
        return {
            "window_id": window_id,
            "market": source["market_behavior_market"],
            "benchmark_asset_id": str(benchmark_code),
            "price_basis": "PROVIDER_CLOSE_UNADJUSTED",
            "expected_sessions": [self.collector.bas_dd_to_iso(day) for day in window],
            "series": series,
        }

    def behavior_features(
        self,
        market: str,
        codes: Iterable[str],
        benchmark_code: str,
        n: int,
        t: str | dt.datetime,
        *,
        window_id: str,
    ) -> dict:
        """RS and volume features, computed by ``market_behavior`` itself."""
        behavior = _market_behavior(self.code_root)
        window = self.market_behavior_window(
            market, codes, benchmark_code, n, t, window_id=window_id
        )
        contract = behavior.load_contract()
        try:
            return behavior._window_features(
                window, self.collector.parse_utc(self.collector.utc_text(self._instant(t))).replace(tzinfo=None), contract
            )
        except behavior.MarketBehaviorError as exc:
            raise PriceHistoryStoreError(f"BEHAVIOR_FEATURES_FAILED:{exc}") from exc

    # -------------------------------------------------------- append-only index

    def index_rows(self, market: str) -> list[dict]:
        path = self.index_path(market)
        if not path.is_file():
            return []
        rows = []
        for line in path.read_bytes().decode("utf-8").splitlines():
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise PriceHistoryStoreError("INDEX_LINE_NOT_JSON") from exc
            if row.get("schema_version") != self.contract["index_contract_version"]:
                raise PriceHistoryStoreError("INDEX_SCHEMA_MISMATCH")
            rows.append(row)
        return rows

    def append_index(
        self, market: str, manifest: Mapping[str, Any], *, observed_at_utc: str
    ) -> dict:
        """Append one index row.  Never overwrites, never rewrites a prior row.

        A re-receipt whose session digests match what is already indexed adds
        nothing.  A re-receipt that differs is appended as
        ``REVISION_OBSERVED`` beside the original, so both observations
        survive and neither claims to be the correction of the other.
        """
        checked = self.collector.validate_manifest(manifest, self.contract)
        if checked["market"] != market:
            raise PriceHistoryStoreError("INDEX_MARKET_MISMATCH")
        self.collector.parse_utc(observed_at_utc, "INDEX_OBSERVED_AT_INVALID")
        row = {
            "schema_version": self.contract["index_contract_version"],
            "market": market,
            "bas_dd": checked["bas_dd"],
            "event": "OBSERVED",
            "status": checked["status"],
            "pit_class": checked["pit_class"],
            "row_count": checked["row_count"],
            "raw_sha256": checked["raw_sha256"],
            "compact_sha256": checked["compact_sha256"],
            "manifest_sha256": digest(canonical_bytes(checked)),
            "observed_at_utc": observed_at_utc,
            "public_code_commit": checked["public_code_commit"],
        }
        existing = [item for item in self.index_rows(market)
                    if item["bas_dd"] == checked["bas_dd"]]
        if existing:
            comparable = ("status", "row_count", "raw_sha256", "compact_sha256")
            if any(
                all(item[field] == row[field] for field in comparable)
                for item in existing
            ):
                return {"event": "NO_NEW_ROW", "row": None, "appended": False}
            row["event"] = "REVISION_OBSERVED"
        path = self.index_path(market)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("ab") as stream:
            stream.write(canonical_bytes(row) + b"\n")
        return {"event": row["event"], "row": row, "appended": True}

    # ----------------------------------------------------------- writing

    def write_session(
        self,
        market: str,
        manifest: Mapping[str, Any],
        *,
        raw_by_part: Mapping[str, bytes],
        compact: bytes,
    ) -> Path:
        """Persist one session.  ``EMPTY`` never writes compact or raw data.

        A recorded ``EMPTY`` is the honest statement "the provider returned no
        rows for this officially open session"; it is deliberately not a data
        commit, and it never becomes a holiday claim.

        Re-attempts of an ``EMPTY`` session are allowed: the stored attempts
        are kept and the new attempts are appended (renumbered after them).
        A later ``OK`` upgrades the session -- its rows are written and
        ``first_available_observed_at_utc`` is the OK attempt's retrieval.
        An ``OK`` session is never overwritten.  Read the stored manifest back
        with :meth:`manifest` after writing.
        """
        checked = self.collector.validate_manifest(manifest, self.contract)
        directory = self.session_dir(market, checked["bas_dd"])
        manifest_path = directory / "manifest.json"
        if manifest_path.exists():
            prior = self.manifest(market, checked["bas_dd"])
            if prior["status"] == "OK":
                raise PriceHistoryStoreError(
                    f"SESSION_ALREADY_STORED:{market}:{checked['bas_dd']}"
                )
            attempts = [copy.deepcopy(dict(item)) for item in prior["attempts"]]
            for item in checked["attempts"]:
                attempts.append({**copy.deepcopy(dict(item)), "attempt_no": len(attempts) + 1})
            checked = self.collector.validate_manifest({**checked, "attempts": attempts}, self.contract)
        directory.mkdir(parents=True, exist_ok=True)
        if checked["status"] == "OK":
            if digest(bytes(compact)) != checked["compact_sha256"]:
                raise PriceHistoryStoreError("COMPACT_SHA256_MISMATCH")
            raw_dir = directory / "raw"
            raw_dir.mkdir(parents=True, exist_ok=True)
            for part in checked["parts"]:
                part_id = str(part["part_id"])
                if digest(bytes(raw_by_part[part_id])) != part["raw_sha256"]:
                    raise PriceHistoryStoreError(f"RAW_SHA256_MISMATCH:{part_id}")
                (raw_dir / f"{part_id}.json.gz").write_bytes(
                    gzip.compress(bytes(raw_by_part[part_id]), mtime=0)
                )
            (directory / "compact.jsonl.gz").write_bytes(
                gzip.compress(bytes(compact), mtime=0)
            )
        # The manifest is written last: it is the commit point of the session.
        manifest_path.write_bytes(canonical_bytes(checked) + b"\n")
        return directory

    # ------------------------------------------------------------ helpers

    def _instant(self, value: str | dt.datetime) -> dt.datetime:
        if isinstance(value, dt.datetime):
            if value.tzinfo is None:
                raise PriceHistoryStoreError("AS_OF_NOT_TIMEZONE_AWARE")
            return value.astimezone(dt.timezone.utc)
        return self.collector.parse_utc(value, "AS_OF_INVALID")


def sessions_available_at(
    market: str, t: str | dt.datetime, *, store_root: Path | str, code_root: Path = ROOT
) -> list[str]:
    """Module-level form of :meth:`PriceHistoryStore.sessions_available_at`."""
    return PriceHistoryStore(store_root, code_root=code_root).sessions_available_at(market, t)


def series(
    market: str,
    code: str,
    n: int,
    t: str | dt.datetime,
    *,
    store_root: Path | str,
    code_root: Path = ROOT,
) -> list[dict]:
    """Module-level form of :meth:`PriceHistoryStore.series`."""
    return PriceHistoryStore(store_root, code_root=code_root).series(market, code, n, t)


def copy_authority(contract: Mapping[str, Any]) -> dict:
    return copy.deepcopy(dict(contract["authority"]))
