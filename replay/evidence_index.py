#!/usr/bin/env python3
"""Scans committed repo evidence and builds a PIT-safe capture index.

★ No wall-clock / random access anywhere in this module. Every date used
  comes from the evidence itself (a snapshot's own `collected_for_kst_date`
  / `snapshot_date` field, or its directory name), never from
  `datetime.now()`.

Two capture-date concepts, kept distinct everywhere downstream:

  * `capture_date`  -- the date a snapshot file was actually committed /
    downloaded. Before this date, the snapshot did not exist in the repo and
    Atlas could not have used it.
  * embedded historical window -- KRX snapshots each carry a `daily` map of
    OHLCV + investor-flow rows going back several weeks, and each Kraken BTC
    snapshot carries ~720 daily OHLC rows. Those rows describe *realized*
    market history for dates that can be well before `capture_date`. Using
    those rows to grade forward returns for an earlier decision_date is not
    a lookahead violation (the market price on that date was real, public
    information as of that date) -- but using them to claim "Atlas detected
    a signal on that date" would be, because Atlas's own system had no
    committed evidence trail before its first commit (2026-08-13). See
    `price_series.py` for how this module's two date fields
    (`capture_date` vs the row's own trading date) are kept separate through
    every downstream computation.
"""
from __future__ import annotations

import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
EVIDENCE_DIR = ROOT / "evidence"

# The repo's own git history begins here -- see the PR description / audit
# report for the `git log --reverse` evidence. No committed evidence of any
# kind (briefing, decision packet, or raw collector snapshot) exists before
# this date.
REPO_HISTORY_STARTS_AT = "2026-08-13"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_json_gz(path: Path) -> dict:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


class KrxSnapshot:
    """One committed `data/<date>/krx.json` (or `data/latest_krx.json`)
    file. `capture_date` is when this file was collected; `daily` windows
    inside it may reach further back."""

    def __init__(self, path: Path):
        self.path = path
        raw = _load_json(path)
        self.capture_date = raw["collected_for_kst_date"]
        self.collected_at_utc = raw["collected_at_utc"]
        self.stocks = raw["stocks"]
        self.sha256 = sha256_file(path)

    def citation(self, code: str) -> str:
        return f"{self.path.relative_to(ROOT)}#{code}"


class BtcSnapshot:
    """One committed `evidence/crypto/btc/raw/<date>/` bundle."""

    def __init__(self, day_dir: Path):
        self.dir = day_dir
        manifest = _load_json(day_dir / "_manifest.json")
        self.capture_date = manifest["snapshot_date"]
        self.latest_finalized_day = manifest["raw"]["latest_finalized_day"]
        self.current_excluded_day = manifest["raw"]["current_excluded_day"]
        ohlc_path = day_dir / "kraken_ohlc_xbtusd.json.gz"
        self.ohlc_raw = _load_json_gz(ohlc_path)
        self.sha256 = sha256_file(ohlc_path)

    def citation(self) -> str:
        return f"{(self.dir / 'kraken_ohlc_xbtusd.json.gz').relative_to(ROOT)}"

    def rows(self):
        """Kraken OHLC rows, EXCLUDING the still-open current day, per the
        collector's own `current_candle_policy: exclude_last_row_always`
        (verified in this snapshot's own manifest -- not re-derived by us)."""
        pair_rows = self.ohlc_raw["result"]["BTC/USD"]
        out = []
        for row in pair_rows:
            ts = row[0]
            date = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date().isoformat()
            if date > self.latest_finalized_day:
                continue
            out.append({
                "date": date,
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "vwap": float(row[5]),
                "volume": float(row[6]),
            })
        return out


class BreadthSnapshot:
    """One committed `evidence/crypto/breadth/raw/<date>/` bundle: real daily
    OHLC for several hundred crypto pairs, per the collector's own
    `ranking_start_day`/`ranking_end_day` window (this repo's crypto-breadth
    collector happens to be scoped almost exactly to the audit window)."""

    def __init__(self, day_dir: Path):
        self.dir = day_dir
        manifest = _load_json(day_dir / "_manifest.json")
        self.capture_date = manifest["fetched_at_utc"][:10]
        self._ohlc_meta = {e["pair_id"]: e for e in manifest["raw"]["ohlc"]}
        self._ndjson_path = day_dir / "kraken_ohlc_responses.ndjson.gz"
        self.sha256 = sha256_file(self._ndjson_path)
        self._by_pair_cache = None

    def pair_ids(self) -> list[str]:
        return sorted(self._ohlc_meta)

    def citation(self, pair_id: str) -> str:
        return f"{self._ndjson_path.relative_to(ROOT)}#{pair_id}"

    def _by_pair(self) -> dict:
        if self._by_pair_cache is None:
            index = {}
            with gzip.open(self._ndjson_path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    entry = json.loads(line)
                    index[entry["pair_id"]] = entry
            self._by_pair_cache = index
        return self._by_pair_cache

    def rows_for_pair(self, pair_id: str):
        import base64
        meta = self._ohlc_meta.get(pair_id)
        entry = self._by_pair().get(pair_id)
        if meta is None or entry is None:
            return []
        latest_finalized_day = meta["latest_finalized_day"]
        body = json.loads(base64.b64decode(entry["body_b64"]))
        pair_rows = body.get("result", {}).get(pair_id, [])
        out = []
        for row in pair_rows:
            ts = row[0]
            date = dt.datetime.fromtimestamp(ts, tz=dt.timezone.utc).date().isoformat()
            if date > latest_finalized_day:
                continue
            out.append({
                "date": date, "open": float(row[1]), "high": float(row[2]),
                "low": float(row[3]), "close": float(row[4]),
                "vwap": float(row[5]), "volume": float(row[6]),
            })
        return out


def find_breadth_snapshots() -> list[BreadthSnapshot]:
    out = []
    base = EVIDENCE_DIR / "crypto" / "breadth" / "raw"
    if not base.is_dir():
        return out
    for day_dir in sorted(base.glob("20*-*-*")):
        if (day_dir / "_manifest.json").is_file() and (day_dir / "kraken_ohlc_responses.ndjson.gz").is_file():
            out.append(BreadthSnapshot(day_dir))
    return sorted(out, key=lambda s: s.capture_date)


def find_krx_snapshots() -> list[KrxSnapshot]:
    out = []
    for day_dir in sorted(DATA_DIR.glob("20*-*-*")):
        f = day_dir / "krx.json"
        if f.is_file():
            out.append(KrxSnapshot(f))
    latest = DATA_DIR / "latest_krx.json"
    if latest.is_file():
        snap = KrxSnapshot(latest)
        if not any(s.capture_date == snap.capture_date for s in out):
            out.append(snap)
    return sorted(out, key=lambda s: s.capture_date)


def find_btc_snapshots() -> list[BtcSnapshot]:
    out = []
    base = EVIDENCE_DIR / "crypto" / "btc" / "raw"
    if not base.is_dir():
        return out
    for day_dir in sorted(base.glob("20*-*-*")):
        if (day_dir / "_manifest.json").is_file() and (day_dir / "kraken_ohlc_xbtusd.json.gz").is_file():
            out.append(BtcSnapshot(day_dir))
    return sorted(out, key=lambda s: s.capture_date)


def load_universe() -> dict:
    return _load_json(ROOT / "config" / "universe.json")


def snapshot_at_or_before(snapshots, decision_date: str):
    """PIT-safe selection: the most recent snapshot whose OWN capture_date is
    <= decision_date. Never returns a snapshot captured after decision_date.
    Returns None if no such snapshot is committed (a real DATA_FAILURE)."""
    eligible = [s for s in snapshots if s.capture_date <= decision_date]
    if not eligible:
        return None
    return max(eligible, key=lambda s: s.capture_date)


def all_krx_codes(snapshots: list[KrxSnapshot]) -> list[str]:
    codes = set()
    for s in snapshots:
        codes.update(s.stocks.keys())
    return sorted(codes)
