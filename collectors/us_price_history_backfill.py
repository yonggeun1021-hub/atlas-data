#!/usr/bin/env python3
"""US Alpaca-authorized price-history backfill request planner (US-DATA-1
item 2, CIO 2026-09-13 `CLAUDE_CIO_CANDIDATE_PIPELINE_REBUILD_PLAN` W1).

Scope: the 22 symbols already dedicated-credential-authorized under
`config/free_market_data_contract.json`'s `alpaca.symbols`
(`credential_scope: DEDICATED_MARKET_DATA_ONLY`, `feed: iex`) -- the same
symbol set `collectors/free_market_data.py::fetch_alpaca_daily_bars` already
fetches daily. This module does not expand that authorized symbol set; the
full US investable-universe backfill (thousands of symbols, plan §2 "US 투자
유니버스 U1") is a later, separately-ratified milestone.

Reuses `fetch_alpaca_daily_bars` UNMODIFIED. That function's lookback window
is fixed (`observed_at.date() - 180 days` .. `observed_at`, up to
`limit=240` bars) and has no independent start-date parameter, so a single
call cannot cover a full one-year backfill. This planner instead chains
multiple calls per symbol, each anchored at a different `observed_at`
spaced `window_days` (default 180) apart -- see `compute_backfill_anchors`.
For a trailing 365-calendar-day range that chain needs 3 anchors per symbol
(2 anchors only cover 360 days back and leaves a several-day gap), i.e.
`3 * 22 = 66` requests for the current 22-symbol scope. NYSE holidays make
the exact trading-session count for that same trailing-year window 251, not
an assumed 252 -- see the PR description for the reproducible calendar
computation (fixed-rule federal/NYSE holiday approximation; it is NOT the
official-calendar module pattern `market_data/us_natural_session_receipt.py`
uses, which requires a caller-captured official NYSE/Nasdaq calendar fact
this planner does not have).

PIT / lookahead discipline mirrors `regime/us_historical_replay_population.py
::replay_trend_source`: each anchor's `observed_at` is pinned to that
anchor date's last UTC instant (`23:59:59Z`), and any bar whose session date
is later than its own anchor is a hard failure
(`US_PRICE_HISTORY_BACKFILL_LOOKAHEAD_VIOLATION`), never silently dropped.
`pit_class` is always `HISTORICAL_BACKFILL` here (never `FORWARD_CAPTURE` --
that is the separate daily forward-collection proposal, see
`docs/proposed_us_price_history_forward_capture_workflow.yml.md`).

Pacing scheme (own diagnosis, stated explicitly per the CIO's own
UNVERIFIED-Alpaca-free-tier-rate-limit caveat -- nothing here assumes a
specific published limit):
  - requests are grouped into configurable batches of `--batch-size` request
    units (default 4);
  - a `--request-pause-seconds` pause (default 3.0) follows every request;
  - an additional, longer `--batch-pause-seconds` cooldown (default 15.0)
    follows every batch boundary.
  All three are CLI-configurable, never hardcoded to an assumed limit.
  `estimate_wallclock_seconds` models only this deliberate pacing delay, not
  network/provider latency itself, so it is an upper-bound-shaped estimate.

Storage boundary (plan §2 principle: "KRX 파생 종목별 가격·측정치는 private에
둔다. public에는 계약·코드·해시·집계·허용 필드만 둔다." -- applied to US via
Alpaca): this repo (`atlas-data`) is PUBLIC. `run_live_backfill` refuses
(`US_PRICE_HISTORY_BACKFILL_OUT_DIR_INSIDE_PUBLIC_REPO`) to write any
per-symbol bar row inside this repository's working tree; `--out-dir` must
resolve outside it. No per-symbol US price row is committed by this change.

Live-path guarantees (2026-09-15, user approval US_BACKFILL):
  - bounded: symbols must be a subset of the frozen 22 `APPROVED_SYMBOLS`,
    `end - start <= 364` days, at most `MAX_LIVE_REQUESTS` (66) HTTP requests
    per invocation (failed attempts count), request pause >= 0.5s;
  - write-once: each (symbol, anchor) unit writes `<stem>.raw.json` then its
    completion marker `<stem>.manifest.json`; an existing path with different
    bytes fails closed (`collectors/free_market_data.py::_write_once` rule);
  - resumable: a unit whose manifest exists and whose raw hash matches is
    skipped without a request; a manifest/raw hash mismatch fails closed;
  - a response carrying `next_page_token` (truncated window) fails closed;
  - credentials only travel in request headers; errors carry codes only.

Default mode is DRY RUN: `build_plan`/`main()` compute and print the request
plan (batch structure, request count, pacing estimate) and make ZERO network
calls. Real execution additionally requires `--live` AND both
`ALPACA_MARKET_DATA_API_KEY`/`ALPACA_MARKET_DATA_API_SECRET` (the same
dedicated, disposable, market-data-only credential names
`collectors/free_market_data.py` already uses -- never the account/trading
`ALPACA_API_KEY`/`ALPACA_API_SECRET` pair, which does not live in this
repo's secrets at all). This module is a tool, not a job: nothing in this
change schedules or triggers a live run.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
import os
from pathlib import Path
import time

ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = ROOT / "config" / "free_market_data_contract.json"
UTC = dt.timezone.utc

SCHEMA_VERSION = "us_price_history_backfill_plan/1"
RECEIPT_SCHEMA_VERSION = "us_price_history_backfill_receipt/1"

# 전부 false다. 이 스크립트는 계획 수립/요청 실행 도구일 뿐 매매·발주·자본
# 권한을 부여하지 않는다.
AUTHORITY = {
    "backfill_execution_authority": False,
    "trading_or_order_authority": False,
    "capital_authority": False,
    "production_authorized": False,
}

DEFAULT_WINDOW_DAYS = 180  # fetch_alpaca_daily_bars 고정 lookback과 동일
DEFAULT_BATCH_SIZE = 4
DEFAULT_REQUEST_PAUSE_SECONDS = 3.0
DEFAULT_BATCH_PAUSE_SECONDS = 15.0

# 사용자 승인(USER_RATIFICATION_CAPITAL_ROTATION_RULES_V1_20260915, US_BACKFILL):
# 승인된 22개 종목, 1년치, 요청 약 66회. live 경로는 이 범위를 넘으면 요청 전에 거부한다.
APPROVED_SYMBOLS = (
    "ANET", "CRDO", "IWM", "MSFT", "MU", "NVDA", "QQQ", "SMH", "SNDK", "SPY", "TSM",
    "XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY",
)
MAX_RANGE_DAYS = 364  # end_date - start_date; 양끝 포함 365일
MAX_LIVE_REQUESTS = 66  # 22 종목 x 3 앵커
# Alpaca 무료(Basic) 시장데이터 한도는 분당 200회로 알려져 있다(이 변경에서 재검증하지
# 않음). 요청 사이 최소 0.5초면 분당 120회 이하다. 기본 pacing(3초 + 4회마다 15초)은 훨씬 느리다.
MIN_LIVE_REQUEST_PAUSE_SECONDS = 0.5
RAW_SUFFIX = ".raw.json"
MANIFEST_SUFFIX = ".manifest.json"


class BackfillPlanError(ValueError):
    """계획 수립 또는 실행 입력이 fail-closed로 거부됐다."""


def _load_free_market_data():
    path = ROOT / "collectors" / "free_market_data.py"
    spec = importlib.util.spec_from_file_location("atlas_free_market_data_for_backfill", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("FREE_MARKET_DATA_MODULE_LOAD_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FMD = _load_free_market_data()


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    return json.loads(path.read_text())


def backfill_symbols(contract: dict) -> list[str]:
    """`alpaca.symbols`의 중복 없는 정렬 목록. 이미 22개 확정 유니버스다."""
    symbols = contract.get("alpaca", {}).get("symbols")
    if not isinstance(symbols, list) or not symbols:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_SYMBOLS_MISSING")
    deduped = sorted(set(symbols))
    if len(deduped) != len(symbols):
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_SYMBOLS_DUPLICATE")
    return deduped


def compute_backfill_anchors(
    start_date: dt.date, end_date: dt.date, window_days: int = DEFAULT_WINDOW_DAYS,
) -> list[dt.date]:
    """[start_date, end_date] 전체를 덮는 최소 앵커 날짜 집합.

    `fetch_alpaca_daily_bars`는 커스텀 시작일 파라미터가 없으므로(고정
    `observed_at - window_days` .. `observed_at`), 여러 `observed_at` 앵커를
    `window_days` 간격으로 사슬처럼 이어 붙인다. 가장 오래된 앵커의 윈도가
    `start_date`에 닿을 때까지 뒤로 물러난다 (과잉 커버는 허용, 과소 커버는
    금지 -- fail-closed 보다 gap 없는 쪽을 우선한다).
    """
    if window_days <= 0:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_WINDOW_DAYS_INVALID")
    if start_date > end_date:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_DATE_RANGE_INVALID")
    span = dt.timedelta(days=window_days)
    anchors = [end_date]
    anchor = end_date
    while anchor - span > start_date:
        anchor = anchor - span
        anchors.append(anchor)
    return anchors


def build_request_units(symbols: list[str], anchors: list[dt.date]) -> list[tuple[str, dt.date]]:
    """symbol-major 순서: 한 종목의 앵커 사슬이 연달아 요청되도록 묶는다."""
    ordered_anchors = sorted(anchors, reverse=True)
    return [(symbol, anchor) for symbol in symbols for anchor in ordered_anchors]


def build_batches(
    units: list[tuple[str, dt.date]], batch_size: int = DEFAULT_BATCH_SIZE,
) -> list[list[tuple[str, dt.date]]]:
    if batch_size <= 0:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_BATCH_SIZE_INVALID")
    return [units[i:i + batch_size] for i in range(0, len(units), batch_size)]


def estimate_wallclock_seconds(
    total_requests: int,
    batch_size: int = DEFAULT_BATCH_SIZE,
    request_pause_seconds: float = DEFAULT_REQUEST_PAUSE_SECONDS,
    batch_pause_seconds: float = DEFAULT_BATCH_PAUSE_SECONDS,
) -> float:
    """`total_requests * request_pause + (batch_count - 1) * batch_pause`.

    이건 의도적 pacing 지연만 모델링한다. 네트워크/제공자 지연 자체는
    포함하지 않으므로 최선(best-case)이 아니라 하한 pacing 기준의 추정치다.
    """
    if total_requests <= 0:
        return 0.0
    if batch_size <= 0:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_BATCH_SIZE_INVALID")
    batch_count = -(-total_requests // batch_size)  # ceil division
    return total_requests * request_pause_seconds + max(batch_count - 1, 0) * batch_pause_seconds


def build_plan(
    contract: dict,
    start_date: dt.date,
    end_date: dt.date,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    request_pause_seconds: float = DEFAULT_REQUEST_PAUSE_SECONDS,
    batch_pause_seconds: float = DEFAULT_BATCH_PAUSE_SECONDS,
) -> dict:
    """네트워크 호출이 전혀 없는 순수 계획. dry-run의 유일한 출력이다."""
    symbols = backfill_symbols(contract)
    anchors = compute_backfill_anchors(start_date, end_date, window_days)
    units = build_request_units(symbols, anchors)
    batches = build_batches(units, batch_size)
    total_requests = len(units)
    estimated_seconds = estimate_wallclock_seconds(
        total_requests, batch_size, request_pause_seconds, batch_pause_seconds,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "pit_class": "HISTORICAL_BACKFILL",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "window_days": window_days,
        "symbol_count": len(symbols),
        "symbols": symbols,
        "anchor_count": len(anchors),
        "anchors": [anchor.isoformat() for anchor in sorted(anchors, reverse=True)],
        "total_requests": total_requests,
        "batch_size": batch_size,
        "batch_count": len(batches),
        "request_pause_seconds": request_pause_seconds,
        "batch_pause_seconds": batch_pause_seconds,
        "estimated_wallclock_seconds": estimated_seconds,
        "estimated_wallclock_minutes": round(estimated_seconds / 60, 1),
        "authority": dict(AUTHORITY),
        "network_calls_made": 0,
    }


def _require_live_credentials() -> tuple[str, str]:
    key = os.getenv("ALPACA_MARKET_DATA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_MARKET_DATA_API_SECRET", "").strip()
    if not key and not secret:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_BLOCKED_BY_MISSING_CREDENTIAL")
    if not key or not secret:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_BLOCKED_BY_INCOMPLETE_CREDENTIAL")
    return key, secret


def _require_out_dir_outside_repo(out_dir: Path) -> Path:
    resolved = out_dir.resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError:
        return resolved
    raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_OUT_DIR_INSIDE_PUBLIC_REPO")


def unit_stem(symbol: str, anchor: dt.date) -> str:
    return f"{symbol}_{anchor.isoformat()}"


def _write_once(path: Path, data: bytes, code: str) -> None:
    """`collectors/free_market_data.py::_write_once`와 같은 불변 게시 규칙.

    같은 바이트의 재게시는 no-op(멱등)이고, 같은 주소에 다른 바이트가 오면
    덮어쓰지 않고 즉시 실패한다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if not path.is_file() or path.read_bytes() != data:
            raise BackfillPlanError(code)
        return
    FMD._atomic_write(path, data)


def completed_unit_manifest(
    out_dir: Path, symbol: str, anchor: dt.date, start_date: dt.date, end_date: dt.date,
) -> dict | None:
    """이미 끝난 요청 단위면 manifest를, 아니면 None을 돌려준다.

    manifest가 완료 표지다(raw를 먼저 쓰고 manifest를 나중에 쓴다). manifest가
    있는데 raw가 없거나 해시가 다르면 조용히 재요청하지 않고 실패한다.
    manifest 없이 raw만 남은 단위는 미완료로 보고 재요청하며, 그때 받은 바이트가
    남은 raw와 다르면 `_write_once`가 충돌로 실패시킨다.
    """
    stem = unit_stem(symbol, anchor)
    manifest_path = out_dir / f"{stem}{MANIFEST_SUFFIX}"
    if not manifest_path.exists():
        return None
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_MANIFEST_INVALID:{stem}") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("symbol") != symbol
        or manifest.get("anchor_date") != anchor.isoformat()
        or manifest.get("raw_file") != f"{stem}{RAW_SUFFIX}"
        or manifest.get("projection_version") != SCHEMA_VERSION
    ):
        raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_MANIFEST_INVALID:{stem}")
    if (start_date.isoformat(), end_date.isoformat()) != (manifest.get("range_start_date"), manifest.get("range_end_date")):
        # 같은 out_dir를 다른 기간으로 재사용하면 섞이지 않게 거부한다.
        raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_RESUME_RANGE_MISMATCH:{stem}")
    raw_path = out_dir / manifest["raw_file"]
    if not raw_path.is_file() or FMD.sha256_bytes(raw_path.read_bytes()) != manifest.get("raw_sha256"):
        raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_RESUME_HASH_MISMATCH:{stem}")
    return manifest


def validate_live_scope(
    contract: dict,
    start_date: dt.date,
    end_date: dt.date,
    *,
    window_days: int,
    request_pause_seconds: float,
    max_requests: int,
) -> tuple[list[str], list[dt.date], list[tuple[str, dt.date]]]:
    """승인 범위(22개 종목, 1년, 요청 66회, 무료 한도 안 pacing) 밖이면 요청 전에 거부."""
    symbols = backfill_symbols(contract)
    if not set(symbols) <= set(APPROVED_SYMBOLS):
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_SYMBOL_OUTSIDE_APPROVED_SCOPE")
    if (end_date - start_date).days > MAX_RANGE_DAYS:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_RANGE_EXCEEDS_APPROVED_YEAR")
    if window_days <= 0 or window_days > DEFAULT_WINDOW_DAYS:
        # fetch_alpaca_daily_bars의 고정 lookback보다 긴 간격은 커버리지 구멍을 만든다.
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_WINDOW_DAYS_INVALID")
    if request_pause_seconds < MIN_LIVE_REQUEST_PAUSE_SECONDS:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_PACING_TOO_FAST")
    if max_requests <= 0 or max_requests > MAX_LIVE_REQUESTS:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_REQUEST_BOUND_INVALID")
    anchors = compute_backfill_anchors(start_date, end_date, window_days)
    units = build_request_units(symbols, anchors)
    if len(units) > max_requests:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_REQUEST_BOUND_EXCEEDED")
    return symbols, anchors, units


def run_live_backfill(
    contract: dict,
    start_date: dt.date,
    end_date: dt.date,
    out_dir: Path,
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    request_pause_seconds: float = DEFAULT_REQUEST_PAUSE_SECONDS,
    batch_pause_seconds: float = DEFAULT_BATCH_PAUSE_SECONDS,
    max_requests: int = MAX_LIVE_REQUESTS,
    getter=None,
    sleep_fn=time.sleep,
) -> dict:
    """실제 Alpaca 호출을 수행한다 (write-once, 재개 가능, 요청 수 상한).

    `--live` 플래그와 두 dedicated market-data 자격 증명이 모두 있어야 진입한다.
    요청 단위(symbol, anchor)마다 원본 응답을 `<stem>.raw.json`에, 완료 표지인
    `<stem>.manifest.json`을 그다음에 write-once로 쓴다. 이미 완료된 단위는
    요청하지 않고 건너뛴다(재실행 = 남은 단위만 요청). 모든 파일은 `out_dir`
    아래에만 쓰고 `out_dir`는 반드시 이 public repo 밖이어야 한다. 반환되는
    receipt는 개수·해시만 담는다(가격 행 없음). 자격 증명은 요청 헤더로만
    전달되고 manifest/receipt/예외 메시지 어디에도 들어가지 않는다.
    """
    key, secret = _require_live_credentials()
    resolved_out = _require_out_dir_outside_repo(out_dir)
    if batch_size <= 0:
        raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_BATCH_SIZE_INVALID")
    symbols, _anchors, units = validate_live_scope(
        contract, start_date, end_date,
        window_days=window_days, request_pause_seconds=request_pause_seconds,
        max_requests=max_requests,
    )
    resolved_out.mkdir(parents=True, exist_ok=True)
    getter_fn = getter or FMD._get
    attempts: list[dict] = []
    row_counts: dict[str, int] = {}
    requests_made = 0
    units_skipped = 0
    for symbol, anchor in units:
        stem = unit_stem(symbol, anchor)
        existing = completed_unit_manifest(resolved_out, symbol, anchor, start_date, end_date)
        if existing is not None:
            units_skipped += 1
            row_counts[symbol] = row_counts.get(symbol, 0) + int(existing["row_count"])
            attempts.append({"symbol": symbol, "anchor_date": anchor.isoformat(),
                             "row_count": int(existing["row_count"]), "status": "SKIPPED_EXISTING"})
            continue
        if requests_made >= max_requests:
            raise BackfillPlanError("US_PRICE_HISTORY_BACKFILL_REQUEST_BUDGET_EXHAUSTED")
        if requests_made > 0:
            sleep_fn(request_pause_seconds)
            if requests_made % batch_size == 0:
                sleep_fn(batch_pause_seconds)
        anchor_end = dt.datetime.combine(anchor, dt.time(23, 59, 59), tzinfo=UTC)
        requested_at = dt.datetime.now(UTC).replace(microsecond=0)
        requests_made += 1
        try:
            raw, normalized = FMD.fetch_alpaca_daily_bars(
                key, secret, [symbol], anchor_end, getter=getter_fn,
            )
        except FMD.FreeMarketDataError as exc:
            # FMD 오류 코드는 URL/헤더를 담지 않는다. 코드만 옮긴다.
            raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_FETCH_FAILED:{stem}:{exc}") from None
        response = json.loads(raw)["responses"][symbol]
        if response.get("next_page_token"):
            # 180일 창은 limit=240 안에 들어가야 한다. 잘린 응답은 구멍이다.
            raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_RESPONSE_TRUNCATED:{stem}")
        kept = 0
        for row in normalized:
            session_text = str(row.get("opened_at", ""))[:10]
            try:
                session = dt.date.fromisoformat(session_text)
            except ValueError as exc:
                raise BackfillPlanError(
                    f"US_PRICE_HISTORY_BACKFILL_SESSION_DATE_INVALID:{symbol}"
                ) from exc
            # regime/us_historical_replay_population.py::replay_trend_source와
            # 동일한 규율: 앵커보다 늦은 bar는 조용히 자르지 않고 즉시 실패한다.
            if session > anchor:
                raise BackfillPlanError(
                    f"US_PRICE_HISTORY_BACKFILL_LOOKAHEAD_VIOLATION:{symbol}:{anchor.isoformat()}"
                )
            if start_date <= session <= end_date:
                kept += 1
        raw_name = f"{stem}{RAW_SUFFIX}"
        _write_once(resolved_out / raw_name, raw, "US_PRICE_HISTORY_BACKFILL_RAW_CONFLICT")
        manifest = {
            "market": "US",
            "symbol": symbol,
            "anchor_date": anchor.isoformat(),
            "endpoint": f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
            "feed": "iex",
            "adjustment": "raw",
            "requested_at_utc": requested_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "retrieved_at_utc": dt.datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "row_count": kept,
            "response_bar_count": len(normalized),
            "raw_file": raw_name,
            "range_start_date": start_date.isoformat(),
            "range_end_date": end_date.isoformat(),
            "raw_sha256": FMD.sha256_bytes(raw),
            "projection_version": SCHEMA_VERSION,
            "pit_class": "HISTORICAL_BACKFILL",
            "authority": dict(AUTHORITY),
        }
        _write_once(
            resolved_out / f"{stem}{MANIFEST_SUFFIX}",
            json.dumps(manifest, sort_keys=True, indent=2).encode(),
            "US_PRICE_HISTORY_BACKFILL_MANIFEST_CONFLICT",
        )
        row_counts[symbol] = row_counts.get(symbol, 0) + kept
        attempts.append({"symbol": symbol, "anchor_date": anchor.isoformat(), "row_count": kept, "status": "FETCHED"})
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "pit_class": "HISTORICAL_BACKFILL",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbol_count": len(symbols),
        "total_requests": len(units),
        "requests_made": requests_made,
        "units_skipped_existing": units_skipped,
        "max_requests": max_requests,
        "total_row_count": sum(row_counts.values()),
        "row_counts_by_symbol": row_counts,
        "attempts": attempts,
        "out_dir": str(resolved_out),
        "authority": dict(AUTHORITY),
    }
    return receipt


def _parse_date(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise BackfillPlanError(f"US_PRICE_HISTORY_BACKFILL_DATE_INVALID:{value}") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=str, default=None, help="YYYY-MM-DD (기본: --end-date - 364일)")
    parser.add_argument("--end-date", type=str, default=None, help="YYYY-MM-DD (기본: 오늘, UTC)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--request-pause-seconds", type=float, default=DEFAULT_REQUEST_PAUSE_SECONDS)
    parser.add_argument("--batch-pause-seconds", type=float, default=DEFAULT_BATCH_PAUSE_SECONDS)
    parser.add_argument("--max-requests", type=int, default=MAX_LIVE_REQUESTS)
    parser.add_argument(
        "--live", action="store_true",
        help="실제로 실행한다. ALPACA_MARKET_DATA_API_KEY/SECRET이 둘 다 있어야 한다. "
             "기본은 네트워크 호출 0건의 dry-run(계획만 출력)이다.",
    )
    parser.add_argument(
        "--require-live", action="store_true",
        help="--live인데 자격 증명이 없으면 dry-run으로 넘어가지 않고 exit 2로 실패한다 (workflow 전용).",
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="--live 전용. 반드시 이 repo 밖을 가리켜야 한다.")
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args()

    contract = load_contract(args.contract)
    end_date = _parse_date(args.end_date) if args.end_date else dt.datetime.now(UTC).date()
    start_date = _parse_date(args.start_date) if args.start_date else end_date - dt.timedelta(days=MAX_RANGE_DAYS)

    key = os.getenv("ALPACA_MARKET_DATA_API_KEY", "").strip()
    secret = os.getenv("ALPACA_MARKET_DATA_API_SECRET", "").strip()
    live_ready = args.live and bool(key) and bool(secret)

    if not live_ready:
        plan = build_plan(
            contract, start_date, end_date,
            window_days=args.window_days, batch_size=args.batch_size,
            request_pause_seconds=args.request_pause_seconds,
            batch_pause_seconds=args.batch_pause_seconds,
        )
        plan["mode"] = "DRY_RUN"
        if args.live and not (key and secret):
            plan["blocked_reason"] = (
                "US_PRICE_HISTORY_BACKFILL_BLOCKED_BY_MISSING_CREDENTIAL"
                if not key and not secret
                else "US_PRICE_HISTORY_BACKFILL_BLOCKED_BY_INCOMPLETE_CREDENTIAL"
            )
        print(json.dumps(plan, sort_keys=True, indent=2))
        return 2 if args.require_live else 0

    if args.out_dir is None:
        raise SystemExit("US_PRICE_HISTORY_BACKFILL_OUT_DIR_REQUIRED_FOR_LIVE_MODE")
    try:
        receipt = run_live_backfill(
            contract, start_date, end_date, args.out_dir,
            window_days=args.window_days, batch_size=args.batch_size,
            request_pause_seconds=args.request_pause_seconds,
            batch_pause_seconds=args.batch_pause_seconds,
            max_requests=args.max_requests,
        )
    except BackfillPlanError as exc:
        print(json.dumps({"mode": "LIVE", "status": "FAILED", "error": str(exc)}, sort_keys=True))
        return 1
    receipt["mode"] = "LIVE"
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
