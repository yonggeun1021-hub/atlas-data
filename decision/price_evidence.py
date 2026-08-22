#!/usr/bin/env python3
"""P8-10 real evidence assembly layer.

`decision/price_reflection.py` is deliberately a pure classifier: it never
fetches evidence itself, it only accepts price/volume/relative-strength
parameters a caller already assembled (see that module's own docstring).
This module IS that caller for real subjects: it reads real, already
committed repo evidence and turns it into the exact keyword arguments
`decision.price_reflection.build_packet()` expects. It never invents a
price or a benchmark value.

★ CIO review round 2 on PR #212: Korea market (KOSPI/KOSDAQ) membership is
  loaded from `config/korea_market_membership.json` -- an explicit,
  auditable canonical mapping with source/observation_date/hash/
  approval_status per entry -- and ONLY entries with `approval_status ==
  "RATIFIED"` are ever used for `relative_strength.vs_market`. As of this
  build every entry in that file is `UNRATIFIED` (no committed, hash-
  verified KRX Open API stock-master lookup exists in this repo confirming
  market venue per code), so `vs_market` is currently `None` for every
  Korea subject regardless of code -- see `_ratified_korea_market_of`. This
  replaces a round-1 hardcoded `KOREA_STOCK_MARKET_MEMBERSHIP` dict a
  code-comment-only "identity assumption" the CIO correctly rejected as not
  real evidence.

Three real, reused data sources (no new external API calls):

  * KRX daily closes -- `replay/price_series.py` + `replay/evidence_index.py`
    (built for PR #210's PIT replay audit; reused UNCHANGED here, not
    reimplemented). Each committed `data/<date>/krx.json` snapshot carries an
    embedded multi-week `daily` OHLCV window per stock; merging every
    snapshot gives ~32 real trading days per covered code as of this
    module's build (2026-07-06..2026-08-20).

  * Korea KOSPI/KOSDAQ composite benchmark -- `data/observations/
    korea_leadership_context/<date>/packet.json` (P1-KR-07 Korea Leadership
    real KRX Open API index data, PR #195/#197/#198/#199). Each packet
    reports a real single-session (day-over-day) `cumulative_gross_return`
    for `role in {KOSPI_BENCHMARK, KOSDAQ_BENCHMARK}`. This module chain-
    links those real day-over-day factors into an index-level series
    (`KoreaBenchmarkSeries.index_levels`) -- the repo has never committed a
    raw KOSPI/KOSDAQ index price series (`korea_leadership_live_fetch.py`
    explicitly never persists raw index closes, only the transform
    *outcome*), so this chain-linked series is the only real, non-fabricated
    market-index proxy this repo's own evidence can support.

  * US single-name price -- `evidence/free_market_data/raw/<date>/
    manifest.json` (P1 Alpaca IEX connector). Each day is a single most-
    recent-bar snapshot, not a historical series; as of this module's build
    only one day (2026-08-22) is committed, so return-window/relative-
    strength fields are honestly left `None` for these subjects rather than
    computed from one point. Every additional day the existing daily cron
    commits automatically widens this without any code change here (see
    `_us_return_window_label`).

PIT discipline: every assembled figure's evidence dates are checked with
`replay.lookahead_gate.assert_no_signal_lookahead` before being returned --
reused unchanged from PR #210, not reimplemented.
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_EVEN
import datetime as dt
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from replay import evidence_index as ei  # noqa: E402
from replay import price_series as ps  # noqa: E402
from replay import lookahead_gate as lg  # noqa: E402

KOREA_LEADERSHIP_CONTEXT_DIR = ROOT / "data" / "observations" / "korea_leadership_context"
FREE_MARKET_DATA_RAW_DIR = ROOT / "evidence" / "free_market_data" / "raw"
KOREA_MARKET_MEMBERSHIP_PATH = ROOT / "config" / "korea_market_membership.json"


def load_ratified_korea_market_membership(path: Path = KOREA_MARKET_MEMBERSHIP_PATH) -> dict:
    """Reads `config/korea_market_membership.json` and returns ONLY the
    `code -> market_claim` entries whose `approval_status == "RATIFIED"`.
    Fails closed (empty dict) on a missing/malformed file rather than
    raising -- a market-benchmark lookup miss is not a builder-crashing
    condition, it is just `vs_market=None` downstream."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    members = raw.get("members") if isinstance(raw, dict) else None
    if not isinstance(members, list):
        return {}
    out = {}
    for row in members:
        if (
            isinstance(row, dict)
            and row.get("approval_status") == "RATIFIED"
            and isinstance(row.get("code"), str)
            and row.get("market_claim") in ("KOSPI", "KOSDAQ")
        ):
            out[row["code"]] = row["market_claim"]
    return out


def _ratified_korea_market_of(code: str) -> str | None:
    return load_ratified_korea_market_membership().get(code)

RECENT_STOCK_WINDOW_TRADING_DAYS = 21  # ~1 calendar month of KRX trading sessions
VOLUME_RECENT_TRADING_DAYS = 5
VOLUME_PRIOR_TRADING_DAYS = 15

# BTC trades every calendar day (unlike KRX's weekday-only sessions), so
# these are literal calendar-day lookbacks, not trading-session counts.
CRYPTO_RECENT_WINDOW_DAYS = {"1m": 30, "3m": 90, "6m": 180}
CRYPTO_VOLUME_RECENT_DAYS = 5
CRYPTO_VOLUME_PRIOR_DAYS = 15

_PCT_QUANT = Decimal("0.000001")


def _pct_str(value: Decimal) -> str:
    return str(value.quantize(_PCT_QUANT, rounding=ROUND_HALF_EVEN))


def _utc_z(value: str) -> str:
    """Real, timezone-aware conversion to the strict `...Z` form
    `decision/price_reflection.py` requires -- never a naive string slice.
    `datetime.fromisoformat` only accepts a trailing `Z` from Python 3.11
    onward, so it is normalized to `+00:00` first for compatibility with
    this repo's supported Python versions."""
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class KoreaBenchmarkSeries:
    """Chain-linked KOSPI/KOSDAQ composite proxy built purely from real,
    committed `korea_leadership_context` day-over-day gross-return facts."""

    def __init__(self, market: str):
        if market not in ("KOSPI", "KOSDAQ"):
            raise ValueError(f"UNSUPPORTED_KOREA_BENCHMARK_MARKET:{market}")
        self.market = market
        self._role = f"{market}_BENCHMARK"
        self._identity = {"KOSPI": "KOSPI::코스피", "KOSDAQ": "KOSDAQ::코스닥"}[market]
        # observation_date -> {"gross_return": Decimal, "capture_date": str}
        self._by_date: dict[str, dict] = {}

    @classmethod
    def load(cls, market: str, base_dir: Path = KOREA_LEADERSHIP_CONTEXT_DIR) -> "KoreaBenchmarkSeries":
        series = cls(market)
        if not base_dir.is_dir():
            return series
        for day_dir in sorted(base_dir.glob("20*-*-*")):
            packet_path = day_dir / "packet.json"
            if not packet_path.is_file():
                continue
            raw = json.loads(packet_path.read_text(encoding="utf-8"))
            lp = raw.get("leadership_packet")
            generated_at = raw.get("generated_at")
            if not isinstance(lp, dict) or not isinstance(generated_at, str) or len(generated_at) < 10:
                continue
            observation_date = lp.get("observation_date")
            # Integrity: directory name must self-consistently match the
            # packet's own observation_date -- otherwise skip rather than
            # trust a mismatched file.
            if observation_date != day_dir.name:
                continue
            capture_date = generated_at[:10]
            gross = None
            for obs in lp.get("relative_strength_observations", []):
                if obs.get("role") == series._role and obs.get("series_identity") == series._identity:
                    try:
                        gross = Decimal(str(obs["cumulative_gross_return"]))
                    except Exception:
                        gross = None
                    break
            if gross is None:
                continue
            series._by_date[observation_date] = {"gross_return": gross, "capture_date": capture_date}
        return series

    def dates(self) -> list[str]:
        return sorted(self._by_date)

    def live_dates_at_or_before(self, decision_date: str) -> list[str]:
        """PIT-safe: only observation_dates whose OWN packet capture_date
        (`generated_at`'s date) is also <= decision_date."""
        return sorted(
            d for d, row in self._by_date.items()
            if d <= decision_date and row["capture_date"] <= decision_date
        )

    def capture_dates_at_or_before(self, decision_date: str) -> list[str]:
        return [self._by_date[d]["capture_date"] for d in self.live_dates_at_or_before(decision_date)]

    def index_levels(self, decision_date: str) -> dict[str, Decimal]:
        """Chain-linked index level, PIT-gated. Only ratios between two
        dates in the returned dict are meaningful (arbitrary base=1 on the
        earliest live-known date) -- matches `korea_leadership.py`'s own
        `benchmark[-1]/benchmark[0]` gross-return convention."""
        out: dict[str, Decimal] = {}
        level = Decimal(1)
        for d in self.live_dates_at_or_before(decision_date):
            level = level * self._by_date[d]["gross_return"]
            out[d] = level
        return out


def _krx_code_from_subject(subject: str) -> str | None:
    base = subject[:-3] if subject.endswith(".KS") else subject
    return base if base.isdigit() and len(base) == 6 else None


def _stock_return_pct(series: "ps.PriceSeries", start_date: str, end_date: str) -> Decimal | None:
    start_close = series.close_on(start_date)
    end_close = series.close_on(end_date)
    if start_close is None or end_close is None or start_close == 0:
        return None
    return (Decimal(str(end_close)) / Decimal(str(start_close)) - 1) * 100


def _position_vs_recent_high_pct(series: "ps.PriceSeries", window_dates: list[str], latest_date: str) -> Decimal | None:
    closes = [series.close_on(d) for d in window_dates]
    closes = [c for c in closes if c is not None]
    latest_close = series.close_on(latest_date)
    if not closes or latest_close is None:
        return None
    recent_high = max(closes)
    if recent_high == 0:
        return None
    return (Decimal(str(recent_high)) - Decimal(str(latest_close))) / Decimal(str(recent_high)) * 100


def _volume_change_pct(series: "ps.PriceSeries", recent_dates: list[str], prior_dates: list[str]) -> Decimal | None:
    def _avg_vol(dates: list[str]) -> Decimal | None:
        vols = []
        for d in dates:
            row = series.row_on(d)
            if row and row.get("volume"):
                vols.append(Decimal(str(row["volume"])))
        if not vols:
            return None
        return sum(vols) / len(vols)

    recent_avg = _avg_vol(recent_dates)
    prior_avg = _avg_vol(prior_dates)
    if recent_avg is None or prior_avg is None or prior_avg == 0:
        return None
    return (recent_avg / prior_avg - 1) * 100


def _vs_market_pct(
    stock_series: "ps.PriceSeries", stock_dates: list[str],
    benchmark: KoreaBenchmarkSeries, decision_date: str,
) -> Decimal | None:
    bench_levels = benchmark.index_levels(decision_date)
    common_dates = sorted(set(stock_dates) & set(bench_levels))
    if len(common_dates) < 2:
        return None
    start_date, end_date = common_dates[0], common_dates[-1]
    stock_start = stock_series.close_on(start_date)
    stock_end = stock_series.close_on(end_date)
    if stock_start is None or stock_end is None or stock_start == 0:
        return None
    stock_gross = Decimal(str(stock_end)) / Decimal(str(stock_start))
    bench_gross = bench_levels[end_date] / bench_levels[start_date]
    if bench_gross == 0:
        return None
    return (stock_gross / bench_gross - 1) * 100


def assemble_krx_stock_evidence(code: str, decision_date: str) -> dict:
    """Real KRX evidence -> `build_packet()` kwargs for a Korea subject.
    Never fabricates: any figure the real committed window cannot support is
    left `None` -- including `relative_strength.vs_market`, which stays
    `None` for every code until `config/korea_market_membership.json` has a
    RATIFIED entry for it (see `_ratified_korea_market_of`). A thin/absent
    signal set here naturally routes `decision/price_reflection.py`'s own
    classification to `price_state=UNKNOWN`/`reflection_status=UNKNOWN`
    rather than a guessed confident status -- this module never engineers
    that outcome itself."""
    snapshots = ei.find_krx_snapshots()
    series = ps.build_krx_series(code, snapshots)
    live_dates = series.live_trading_dates_at_or_before(decision_date)
    lg.assert_no_signal_lookahead(
        decision_date,
        [series.first_capture_date_for(d) for d in live_dates],
        label=f"krx_price:{code}",
    )

    latest_snapshot = ei.snapshot_at_or_before(snapshots, decision_date)
    if latest_snapshot is None or not live_dates:
        return {
            "price_as_of": None,
            "data_source_scope": "KRX_OFFICIAL",
            "recent_return_windows": None,
            "relative_strength": None,
        }

    latest_date = live_dates[-1]
    price_as_of = _utc_z(latest_snapshot.collected_at_utc)

    window_dates = live_dates[-RECENT_STOCK_WINDOW_TRADING_DAYS:]
    m1 = None
    if len(window_dates) >= 2:
        m1 = _stock_return_pct(series, window_dates[0], latest_date)
    pos_high = _position_vs_recent_high_pct(series, window_dates, latest_date)

    recent_vol_dates = live_dates[-VOLUME_RECENT_TRADING_DAYS:]
    prior_vol_dates = live_dates[-(VOLUME_RECENT_TRADING_DAYS + VOLUME_PRIOR_TRADING_DAYS):-VOLUME_RECENT_TRADING_DAYS]
    volume_change = _volume_change_pct(series, recent_vol_dates, prior_vol_dates) if prior_vol_dates else None

    vs_market = None
    market = _ratified_korea_market_of(code)
    if market is not None:
        benchmark = KoreaBenchmarkSeries.load(market)
        lg.assert_no_signal_lookahead(
            decision_date, benchmark.capture_dates_at_or_before(decision_date),
            label=f"korea_benchmark:{market}",
        )
        vs_market = _vs_market_pct(series, live_dates, benchmark, decision_date)

    windows = None
    if m1 is not None:
        windows = {"1m": _pct_str(m1), "3m": None, "6m": None}

    strength = None
    if vs_market is not None or pos_high is not None or volume_change is not None:
        strength = {
            "vs_market": _pct_str(vs_market) if vs_market is not None else None,
            "position_vs_recent_high_pct": _pct_str(pos_high) if pos_high is not None else None,
            "volume_change_pct": _pct_str(volume_change) if volume_change is not None else None,
        }

    return {
        "price_as_of": price_as_of,
        "data_source_scope": "KRX_OFFICIAL",
        "recent_return_windows": windows,
        "relative_strength": strength,
    }


def _us_price_points(symbol: str, base_dir: Path = FREE_MARKET_DATA_RAW_DIR) -> list[dict]:
    """Real Alpaca IEX single-bar snapshots for `symbol`, one point per
    committed day. Each is a live most-recent-bar capture, not a historical
    OHLCV series -- see `evidence/free_market_data/raw/<date>/manifest.json`."""
    out = []
    if not base_dir.is_dir():
        return out
    for day_dir in sorted(base_dir.glob("20*-*-*")):
        manifest_path = day_dir / "manifest.json"
        if not manifest_path.is_file():
            continue
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
        alpaca = raw.get("alpaca", {})
        observed_at_utc = raw.get("observed_at_utc")
        bar = next((b for b in alpaca.get("bars", []) if b.get("symbol") == symbol), None)
        if bar is None or not isinstance(observed_at_utc, str):
            continue
        out.append({
            "capture_date": observed_at_utc[:10],
            "provider_timestamp": bar["provider_timestamp"],
            "close": Decimal(str(bar["close"])),
            "source_scope": alpaca.get("source_scope", "UNKNOWN"),
        })
    return out


def _us_return_window_label(days: int) -> str | None:
    if 20 <= days <= 40:
        return "1m"
    if 75 <= days <= 105:
        return "3m"
    if 160 <= days <= 200:
        return "6m"
    return None


def assemble_us_equity_evidence(symbol: str, decision_date: str) -> dict:
    """Real Alpaca IEX evidence -> `build_packet()` kwargs for a US subject.
    As of this module's build only a single day is committed for any
    symbol, so `recent_return_windows`/`relative_strength` are honestly left
    `None` (never fabricated from one point) -- this widens automatically as
    the existing free-market-data cron commits more days, no code change
    required here."""
    points = [p for p in _us_price_points(symbol) if p["capture_date"] <= decision_date]
    lg.assert_no_signal_lookahead(
        decision_date, [p["capture_date"] for p in points], label=f"us_equity:{symbol}",
    )
    if not points:
        return {
            "price_as_of": None,
            "data_source_scope": "IEX_ONLY_PARTIAL_US_MARKET",
            "recent_return_windows": None,
            "relative_strength": None,
        }
    points.sort(key=lambda p: p["provider_timestamp"])
    latest = points[-1]

    windows = None
    if len(points) >= 2:
        earliest = points[0]
        latest_dt = dt.datetime.strptime(latest["provider_timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        earliest_dt = dt.datetime.strptime(earliest["provider_timestamp"], "%Y-%m-%dT%H:%M:%SZ")
        gap_days = (latest_dt - earliest_dt).days
        label = _us_return_window_label(gap_days)
        if label is not None and earliest["close"] != 0:
            ret = (latest["close"] / earliest["close"] - 1) * 100
            windows = {"1m": None, "3m": None, "6m": None}
            windows[label] = _pct_str(ret)

    return {
        "price_as_of": latest["provider_timestamp"],
        "data_source_scope": latest["source_scope"],
        "recent_return_windows": windows,
        # No genuine US market-index/relative-strength series exists in
        # this repo yet (VIXCLS is a volatility level, not a price index a
        # single-name return can be ratioed against) -- left None rather
        # than fabricated. See this module's docstring / the PR report.
        "relative_strength": None,
    }


def assemble_crypto_evidence(symbol: str, decision_date: str) -> dict:
    """Real BTC evidence -> `build_packet()` kwargs, reusing
    `replay/price_series.py`'s `build_btc_series` (Kraken OHLC, PR #210)
    UNCHANGED. Only `BTC` is supported: `evidence/crypto/btc/raw/` is the
    only single-asset crypto OHLCV series in this repo with real embedded
    historical depth (~720 real calendar days as of this module's build) --
    `evidence/crypto/breadth/raw/` covers hundreds of pairs but has no
    `replay/price_series.py`-shaped series builder for them yet.

    No separate crypto market-index series exists in this repo distinct
    from BTC's own price (unlike Korea's real KOSPI/KOSDAQ composite), so
    `relative_strength.vs_market` is left `None` rather than fabricated or
    made tautological (BTC vs BTC)."""
    if symbol != "BTC":
        return {
            "price_as_of": None,
            "data_source_scope": "KRAKEN_OHLC",
            "recent_return_windows": None,
            "relative_strength": None,
        }

    snapshots = ei.find_btc_snapshots()
    series = ps.build_btc_series(snapshots)
    live_dates = series.live_trading_dates_at_or_before(decision_date)
    lg.assert_no_signal_lookahead(
        decision_date,
        [series.first_capture_date_for(d) for d in live_dates],
        label="btc_price",
    )

    latest_snapshot = ei.snapshot_at_or_before(snapshots, decision_date)
    if latest_snapshot is None or not live_dates:
        return {
            "price_as_of": None,
            "data_source_scope": "KRAKEN_OHLC",
            "recent_return_windows": None,
            "relative_strength": None,
        }

    latest_date = live_dates[-1]
    # BtcSnapshot doesn't expose a parsed UTC capture instant (only
    # capture_date) -- its own committed _manifest.json's real fetched_at_utc
    # is read directly here rather than extending replay/evidence_index.py.
    manifest = json.loads((latest_snapshot.dir / "_manifest.json").read_text(encoding="utf-8"))
    price_as_of = _utc_z(manifest["fetched_at_utc"])

    windows = {}
    for label, lookback_days in CRYPTO_RECENT_WINDOW_DAYS.items():
        if len(live_dates) > lookback_days:
            start_date = live_dates[-(lookback_days + 1)]
            ret = _stock_return_pct(series, start_date, latest_date)
            windows[label] = _pct_str(ret) if ret is not None else None
        else:
            windows[label] = None
    if not any(v is not None for v in windows.values()):
        windows = None

    recent_window = live_dates[-(CRYPTO_RECENT_WINDOW_DAYS["1m"] + 1):]
    pos_high = _position_vs_recent_high_pct(series, recent_window, latest_date)

    recent_vol_dates = live_dates[-CRYPTO_VOLUME_RECENT_DAYS:]
    prior_vol_dates = live_dates[
        -(CRYPTO_VOLUME_RECENT_DAYS + CRYPTO_VOLUME_PRIOR_DAYS):-CRYPTO_VOLUME_RECENT_DAYS
    ]
    volume_change = _volume_change_pct(series, recent_vol_dates, prior_vol_dates) if prior_vol_dates else None

    strength = None
    if pos_high is not None or volume_change is not None:
        strength = {
            "vs_market": None,
            "position_vs_recent_high_pct": _pct_str(pos_high) if pos_high is not None else None,
            "volume_change_pct": _pct_str(volume_change) if volume_change is not None else None,
        }

    return {
        "price_as_of": price_as_of,
        "data_source_scope": "KRAKEN_OHLC",
        "recent_return_windows": windows,
        "relative_strength": strength,
    }


CRYPTO_SUBJECT_ALIASES = {"BTC", "BTC-USD", "XBTUSD", "BTCUSD"}


def assemble_price_evidence(subject: str, decision_date: str) -> dict:
    """Dispatch by subject shape. Returns kwargs merge-ready for
    `decision.price_reflection.build_packet(subject=..., decision_date=...,
    generated_at=..., **assemble_price_evidence(subject, decision_date))`.
    A subject with zero committed evidence anywhere (e.g. 034020.KS) simply
    gets every field `None` -- `build_packet` then correctly reports
    `PRICE_DATA_MISSING`, never a fabricated status."""
    code = _krx_code_from_subject(subject)
    if code is not None:
        return assemble_krx_stock_evidence(code, decision_date)
    if subject in CRYPTO_SUBJECT_ALIASES:
        return assemble_crypto_evidence("BTC", decision_date)
    return assemble_us_equity_evidence(subject, decision_date)
