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
    getter=None,
    sleep_fn=time.sleep,
) -> dict:
    """실제 Alpaca 호출을 수행한다.

    이 함수를 작성한 에이전트를 포함해 이 변경의 CI/리뷰 어떤 단계에서도
    호출되지 않는다 -- `--live` 플래그와 두 dedicated market-data 자격
    증명이 모두 있어야 진입한다. 종목별 가격 행은 `out_dir` 아래에만
    쓰고 `out_dir`는 반드시 이 public repo 밖을 가리켜야 한다
    (`_require_out_dir_outside_repo`). 반환되는 receipt는 개수·해시만
    담아 public 커밋에 안전하다.
    """
    key, secret = _require_live_credentials()
    resolved_out = _require_out_dir_outside_repo(out_dir)
    resolved_out.mkdir(parents=True, exist_ok=True)
    symbols = backfill_symbols(contract)
    anchors = compute_backfill_anchors(start_date, end_date, window_days)
    units = build_request_units(symbols, anchors)
    batches = build_batches(units, batch_size)
    getter_fn = getter or FMD._get
    attempts: list[dict] = []
    row_counts: dict[str, int] = {}
    for batch_index, batch in enumerate(batches):
        for symbol, anchor in batch:
            anchor_end = dt.datetime.combine(anchor, dt.time(23, 59, 59), tzinfo=UTC)
            requested_at = dt.datetime.now(UTC).replace(microsecond=0)
            raw, normalized = FMD.fetch_alpaca_daily_bars(
                key, secret, [symbol], anchor_end, getter=getter_fn,
            )
            kept = []
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
                if session < start_date or session > end_date:
                    continue
                kept.append(row)
            row_counts[symbol] = row_counts.get(symbol, 0) + len(kept)
            manifest = {
                "market": "US",
                "symbol": symbol,
                "anchor_date": anchor.isoformat(),
                "endpoint": f"https://data.alpaca.markets/v2/stocks/{symbol}/bars",
                "requested_at_utc": requested_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "retrieved_at_utc": dt.datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "row_count": len(kept),
                "raw_sha256": FMD.sha256_bytes(raw),
                "projection_version": SCHEMA_VERSION,
                "pit_class": "HISTORICAL_BACKFILL",
                "authority": dict(AUTHORITY),
            }
            (resolved_out / f"{symbol}_{anchor.isoformat()}.manifest.json").write_text(
                json.dumps(manifest, sort_keys=True, indent=2)
            )
            attempts.append({"symbol": symbol, "anchor_date": anchor.isoformat(), "row_count": len(kept)})
            sleep_fn(request_pause_seconds)
        if batch_index < len(batches) - 1:
            sleep_fn(batch_pause_seconds)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "pit_class": "HISTORICAL_BACKFILL",
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "symbol_count": len(symbols),
        "total_requests": len(units),
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
    parser.add_argument(
        "--live", action="store_true",
        help="실제로 실행한다. ALPACA_MARKET_DATA_API_KEY/SECRET이 둘 다 있어야 한다. "
             "기본은 네트워크 호출 0건의 dry-run(계획만 출력)이다.",
    )
    parser.add_argument("--out-dir", type=Path, default=None, help="--live 전용. 반드시 이 repo 밖을 가리켜야 한다.")
    parser.add_argument("--contract", type=Path, default=CONTRACT_PATH)
    args = parser.parse_args()

    contract = load_contract(args.contract)
    end_date = _parse_date(args.end_date) if args.end_date else dt.datetime.now(UTC).date()
    start_date = _parse_date(args.start_date) if args.start_date else end_date - dt.timedelta(days=364)

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
        return 0

    if args.out_dir is None:
        raise SystemExit("US_PRICE_HISTORY_BACKFILL_OUT_DIR_REQUIRED_FOR_LIVE_MODE")
    receipt = run_live_backfill(
        contract, start_date, end_date, args.out_dir,
        window_days=args.window_days, batch_size=args.batch_size,
        request_pause_seconds=args.request_pause_seconds,
        batch_pause_seconds=args.batch_pause_seconds,
    )
    receipt["mode"] = "LIVE"
    print(json.dumps(receipt, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
