#!/usr/bin/env python3
"""Daily producer freshness watchdog (read-only, evidence-only).

Three silent failures were found by hand on 2026-09-18, each unnoticed for
days:

  * ``free-market-data.yml`` failed 2026-09-16 and 2026-09-17 on a push
    race -- US Stage 1 evidence and the US rotation chain silently stopped
    advancing.
  * The KIS master capture (outside this public repo) failed every day for
    days -- a late run outside its binding window.
  * KR/US population symbol observations had not been produced since
    2026-09-10 / 2026-09-11 -- a week -- and nothing noticed, because
    ``decision/korea_population_symbol_observation.py`` and
    ``decision/us_population_symbol_observation.py`` have never had a
    scheduled trigger in ``.github/workflows`` at all.

Nothing in this repo watches for "a daily producer stopped producing".
This module is that watch. It never fetches anything, never writes
anything under ``data/`` or ``evidence/``, and carries zero order/action/
production/capital authority -- see ``authority`` in :func:`build_report`.

Design choices, made explicit rather than guessed:

* **No weekday inference.** ``config/us_session_calendar_source_v1.json``
  and every module built on it (``market_data/us_official_session_calendar.py``,
  ``regime/kr_paper_runtime_calendar_packets.py``, etc.) declare
  ``weekday_inference_prohibited: true`` -- market OPEN/CLOSED status must
  come from an official capture, never from ``date.weekday()`` alone. This
  module honours that split:
    - For KR-linked producers, the officially captured KRX holiday list
      already committed at
      ``evidence/market_calendar/krx_global_holiday/2026-09-09/capture-2026.json``
      is replayed offline through the unmodified
      ``regime.kr_paper_runtime_calendar_packets`` /
      ``market_data.krx_official_holiday_calendar`` builders (the same
      modules KR PAPER runtime already trusts) to decide, for any date in
      the captured year, whether KRX was open. A Korean holiday therefore
      never counts as a missed cycle.
    - For US/crypto-linked producers, there is no committed official
      NYSE/Nasdaq capture anywhere in this repo (only the *source*
      ratification in ``config/us_session_calendar_source_v1.json`` --
      fetching the actual page needs network access this watchdog
      deliberately does not take). Rather than invent a fixed-date holiday
      table (the exact anti-pattern the repo prohibits), each producer's
      *own already-declared* GitHub Actions ``cron`` day-of-week set is
      read as the ground truth for which days it is expected to run --
      that is reading existing configuration, not guessing market status.
      This resolves the concrete example in the brief ("a weekday-only US
      producer must not alarm on a Monday for not having run on Sunday")
      without claiming US-holiday awareness this repo does not have. US
      federal holidays are absorbed by each item's one-cycle grace
      (``allowed_missed_cycles``) rather than by a guessed holiday list.
* **Self-declared thresholds win.** ``data/latest_rotation_confirmation_{kr,us,crypto}.json``
  already carries its own ``maximum_observation_gap_days`` -- the rule's own
  declared tolerance. This module reads that field from the artifact
  itself instead of hard-coding a second, possibly-drifting, opinion.
* **"Never produced" is reported differently from "went stale".** A
  producer with zero ``.github/workflows`` trigger at all (population
  symbol observations, the Alpaca SIP daily-bars dispatch-only capture) is
  labelled ``NO_SCHEDULE_*`` rather than ``STALE`` -- there is no cron to
  have "missed". A path/glob that has *never* matched anything is labelled
  ``NEVER_PRODUCED`` -- structurally different from an artifact that used
  to update and stopped.
"""
from __future__ import annotations

import argparse
import datetime as dt
import importlib.util
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

KST = ZoneInfo("Asia/Seoul")
SCHEMA_VERSION = "daily_producer_freshness_watchdog/1"


def _load_module(name: str, relative_path: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"MODULE_LOAD_FAILED:{relative_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


KR_CALENDAR_PACKETS = _load_module(
    "atlas_watchdog_kr_paper_runtime_calendar_packets",
    "regime/kr_paper_runtime_calendar_packets.py",
)
KRX_HOLIDAY_CALENDAR = KR_CALENDAR_PACKETS.CALENDAR


class FreshnessWatchdogError(ValueError):
    """A watchlist item could not be evaluated at all (bad spec, not a stale artifact)."""


# ---------------------------------------------------------------------------
# Calendar helpers
# ---------------------------------------------------------------------------

def kr_trading_day_status(day: dt.date, root: Path = ROOT) -> str:
    """OPEN_REGULAR / CLOSED / UNKNOWN for one KST date, replayed offline
    from the already-committed official KRX holiday capture. Never guesses
    from ``day.weekday()`` outside that officially-approved rule; a date
    outside the captured year (or any integrity mismatch) is UNKNOWN rather
    than guessed, and UNKNOWN is treated by the caller as "not an expected
    day" -- silence toward false alarms, never toward missing a real one,
    because KRX's captured year already covers the near-term dates this
    watchdog runs against.
    """
    try:
        capture_raw = (root / KR_CALENDAR_PACKETS.CAPTURE_REF).read_bytes()
    except OSError:
        return "UNKNOWN"
    if KRX_HOLIDAY_CALENDAR.digest(capture_raw) != KR_CALENDAR_PACKETS.CAPTURE_SHA256:
        return "UNKNOWN"
    try:
        packet, _receipt = KRX_HOLIDAY_CALENDAR.build_calendar_packet(
            capture_raw, KR_CALENDAR_PACKETS.CAPTURE_REF, day.isoformat()
        )
    except KRX_HOLIDAY_CALENDAR.KrxOfficialHolidayCalendarError:
        return "UNKNOWN"
    return packet["calendar"]["status"]


def is_expected_production_day(calendar: dict, day: dt.date, root: Path = ROOT) -> bool:
    kind = calendar["type"]
    if kind == "EVERY_DAY":
        return True
    if kind == "WEEKDAY_SET":
        return day.weekday() in calendar["weekdays"]
    if kind == "KR_TRADING_DAY":
        status = kr_trading_day_status(day, root)
        if status == "UNKNOWN":
            return False  # conservative: never alarm on a date we cannot officially classify
        return status == "OPEN_REGULAR"
    raise FreshnessWatchdogError(f"UNKNOWN_CALENDAR_TYPE:{kind}")


def missed_expected_days(calendar: dict, last_date: dt.date, today: dt.date, root: Path = ROOT) -> list[dt.date]:
    """Expected-production days strictly after ``last_date`` up to and
    including ``today``."""
    missed = []
    day = last_date + dt.timedelta(days=1)
    while day <= today:
        if is_expected_production_day(calendar, day, root):
            missed.append(day)
        day += dt.timedelta(days=1)
    return missed


# ---------------------------------------------------------------------------
# Artifact reading
# ---------------------------------------------------------------------------

DATE_LEN = len("YYYY-MM-DD")


def _get_nested(value: object, dotted_path: str):
    cursor = value
    for key in dotted_path.split("."):
        if not isinstance(cursor, dict) or key not in cursor:
            return None
        cursor = cursor[key]
    return cursor


def _extract_date(value: object) -> dt.date | None:
    if not isinstance(value, str) or len(value) < DATE_LEN:
        return None
    try:
        return dt.date.fromisoformat(value[:DATE_LEN])
    except ValueError:
        return None


def _read_json(path: Path) -> dict | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _latest_glob_date(root: Path, glob_pattern: str) -> dt.date | None:
    best = None
    for match in root.glob(glob_pattern):
        candidate = _extract_date(match.parent.name)
        if candidate is not None and (best is None or candidate > best):
            best = candidate
    return best


def evaluate_file_item(spec: dict, root: Path, today: dt.date) -> dict:
    path = root / spec["path"]
    parsed = _read_json(path)
    if parsed is None:
        return {**_base_result(spec), "status": "NEVER_PRODUCED", "last_date": None, "detail": f"missing or unreadable: {spec['path']}"}

    last_date = None
    for field in spec["date_fields"]:
        last_date = _extract_date(_get_nested(parsed, field))
        if last_date is not None:
            break
    if last_date is None:
        return {
            **_base_result(spec),
            "status": "DATE_FIELD_MISSING",
            "last_date": None,
            "detail": f"none of {spec['date_fields']} parsed as a date in {spec['path']}",
        }

    gap_field = spec.get("gap_field")
    declared_gap = _get_nested(parsed, gap_field) if gap_field else None
    if not isinstance(declared_gap, int) or declared_gap < 0:
        declared_gap = None

    return _classify(spec, root, today, last_date, declared_gap=declared_gap)


def evaluate_glob_item(spec: dict, root: Path, today: dt.date) -> dict:
    last_date = _latest_glob_date(root, spec["glob"])
    if last_date is None:
        return {**_base_result(spec), "status": "NEVER_PRODUCED", "last_date": None, "detail": f"no match ever for {spec['glob']}"}
    return _classify(spec, root, today, last_date, declared_gap=None)


def _base_result(spec: dict) -> dict:
    return {"id": spec["id"], "label_ko": spec["label_ko"], "workflow": spec.get("workflow")}


def _classify(spec: dict, root: Path, today: dt.date, last_date: dt.date, declared_gap: int | None) -> dict:
    calendar = spec["calendar"]
    result = {**_base_result(spec), "last_date": last_date.isoformat()}

    if calendar["type"] == "NO_SCHEDULE":
        age_days = (today - last_date).days
        threshold = declared_gap if declared_gap is not None else spec["default_max_gap_days"]
        result["age_days"] = age_days
        result["threshold_days"] = threshold
        if age_days > threshold:
            result["status"] = "NO_SCHEDULE_STALE"
            result["detail"] = (
                f"no automated .github/workflows trigger exists for this producer; "
                f"last output is {age_days}d old (business threshold {threshold}d)"
            )
        else:
            result["status"] = "NO_SCHEDULE_FRESH"
        return result

    if declared_gap is not None:
        # A domain rule (e.g. rotation confirmation) already declares its own
        # calendar-day tolerance -- trust it over a second, independently
        # maintained opinion.
        age_days = (today - last_date).days
        result["age_days"] = age_days
        result["threshold_days"] = declared_gap
        result["status"] = "STALE" if age_days > declared_gap else "FRESH"
        result["detail"] = f"self-declared maximum_observation_gap_days={declared_gap}"
        return result

    missed = missed_expected_days(calendar, last_date, today, root)
    allowed = spec.get("allowed_missed_cycles", 1)
    result["age_days"] = (today - last_date).days
    result["missed_expected_cycles"] = len(missed)
    result["allowed_missed_cycles"] = allowed
    if missed and len(missed) > allowed:
        result["status"] = "STALE"
        result["detail"] = (
            f"missed {len(missed)} expected production day(s) since {last_date.isoformat()}: "
            f"{', '.join(day.isoformat() for day in missed)}"
        )
    else:
        result["status"] = "FRESH"
    return result


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

def _weekday_set(*names: str) -> dict:
    index = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4, "SAT": 5, "SUN": 6}
    return {"type": "WEEKDAY_SET", "weekdays": frozenset(index[name] for name in names)}


EVERY_DAY = {"type": "EVERY_DAY"}
NO_SCHEDULE = {"type": "NO_SCHEDULE"}
KR_TRADING_DAY = {"type": "KR_TRADING_DAY"}
# free-market-data.yml / fred-dexkous-fx.yml cron '* * * 0-5' (UTC Sun-Fri
# 21:3x/21:4x) lands as KST Mon-Sat next-day; only KST Sunday is off.
US_MON_SAT_KST = _weekday_set("MON", "TUE", "WED", "THU", "FRI", "SAT")
# spdr-sector-holdings.yml cron '0 22 * * 1-5' (UTC Mon-Fri 22:00) lands as
# KST Tue-Sat next-day.
US_TUE_SAT_KST = _weekday_set("TUE", "WED", "THU", "FRI", "SAT")


def default_watchlist() -> list[dict]:
    """The watched daily producers, derived from what is actually committed
    (``data/latest_*.json`` pointers and dated observation directories),
    not a hand-typed guess. See module docstring for the calendar policy.
    """
    return [
        {
            "id": "free_market_data",
            "label_ko": "미국 FRED/Alpaca 시장 데이터 (Stage1 근거)",
            "kind": "FILE",
            "path": "data/latest_free_market_data.json",
            "date_fields": ["observed_at_utc", "us_market_reference.as_of_session_date"],
            "calendar": US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/free-market-data.yml (cron 35 21 * * 0-5 UTC)",
        },
        {
            "id": "rotation_confirmation_kr",
            "label_ko": "KR 로테이션 확인 체인",
            "kind": "FILE",
            "path": "data/latest_rotation_confirmation_kr.json",
            "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days",
            "default_max_gap_days": 7,
            "calendar": KR_TRADING_DAY,
            "workflow": ".github/workflows/rotation-confirmation.yml (workflow_run <- korea-leadership-live-proof.yml)",
        },
        {
            "id": "rotation_confirmation_us",
            "label_ko": "US 로테이션 확인 체인",
            "kind": "FILE",
            "path": "data/latest_rotation_confirmation_us.json",
            "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days",
            "default_max_gap_days": 4,
            "calendar": US_MON_SAT_KST,
            "workflow": ".github/workflows/rotation-confirmation.yml (workflow_run <- free-market-data.yml)",
        },
        {
            "id": "rotation_confirmation_crypto",
            "label_ko": "CRYPTO 로테이션 확인 체인",
            "kind": "FILE",
            "path": "data/latest_rotation_confirmation_crypto.json",
            "date_fields": ["as_of_date"],
            "gap_field": "maximum_observation_gap_days",
            "default_max_gap_days": 2,
            "calendar": EVERY_DAY,
            "workflow": ".github/workflows/rotation-confirmation.yml (workflow_run <- crypto-breadth-capture.yml)",
        },
        {
            "id": "kr_paper_runtime_decision",
            "label_ko": "KR PAPER 런타임 판정",
            "kind": "FILE",
            "path": "data/latest_kr_paper_runtime_decision.json",
            "date_fields": ["evaluation_at", "current_observation.as_of_date"],
            "calendar": KR_TRADING_DAY,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/kr-paper-runtime-daily-publish.yml (cron 40 23 * * 0-4, 45 0 * * 1-5 UTC)",
        },
        {
            "id": "us_paper_runtime_decision",
            "label_ko": "US PAPER 런타임 판정",
            "kind": "FILE",
            "path": "data/latest_us_paper_runtime_decision.json",
            "date_fields": ["evaluation_at"],
            "calendar": US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/us-paper-runtime.yml (cron 55 21 * * 0-5, 40 23 * * 0-5 UTC)",
        },
        {
            "id": "crypto_paper_runtime_decision",
            "label_ko": "CRYPTO PAPER 런타임 판정",
            "kind": "FILE",
            "path": "data/latest_crypto_paper_runtime_decision.json",
            "date_fields": ["evaluation_at", "current_decision_date"],
            "calendar": EVERY_DAY,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/crypto-paper-runtime.yml (cron 15 7 * * *, 45 8 * * * UTC)",
        },
        {
            "id": "crypto_leadership",
            "label_ko": "크립토 Leadership 관측 포인터",
            "kind": "GLOB",
            "glob": "data/observations/crypto_leadership/*/packet.json",
            "calendar": EVERY_DAY,
            "allowed_missed_cycles": 1,
            "workflow": "P1-CR-06 Crypto Breadth Daily Capture chain (cron 40 0 * * * UTC)",
        },
        {
            "id": "spdr_sector_holdings",
            "label_ko": "SPDR 섹터 홀딩스 매핑",
            "kind": "FILE",
            "path": "data/latest_spdr_sector_holdings.json",
            "date_fields": ["capture_date_utc"],
            "calendar": US_TUE_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/spdr-sector-holdings.yml (cron 0 22 * * 1-5 UTC)",
        },
        {
            "id": "fred_dexkous_fx",
            "label_ko": "FRED DEXKOUS KRW/USD 환율 관측 (RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1)",
            "kind": "GLOB",
            # No data/latest_*.json pointer exists for this producer -- only
            # dated, append-only observation evidence. That absence is itself
            # a finding: see the module docstring's "never produced" note.
            "glob": "evidence/fred_dexkous_fx/observations/*/*.captured.json",
            "calendar": US_MON_SAT_KST,
            "allowed_missed_cycles": 1,
            "workflow": ".github/workflows/fred-dexkous-fx.yml (cron 40 21 * * 0-5 UTC)",
        },
        {
            "id": "us_sip_daily_liquidity",
            "label_ko": "US SIP 일일 유동성 (T2/C3)",
            "kind": "FILE",
            "path": "data/latest_us_sip_daily_liquidity.json",
            "date_fields": ["generated_at_utc"],
            "calendar": NO_SCHEDULE,
            "default_max_gap_days": 10,
            "workflow": ".github/workflows/alpaca-sip-daily-bars.yml (workflow_dispatch only -- no cron)",
        },
        {
            "id": "korea_population_symbol_observation",
            "label_ko": "KR 전체-모집단 심볼 관측",
            "kind": "GLOB",
            # persist_packet() writes packet.json.gz (compressed) plus an
            # always-uncompressed summary.json sidecar (_summary_sidecar) --
            # glob on the sidecar so this never needs to gzip-decode.
            "glob": "data/observations/korea_population_symbol_observation/*/summary.json",
            "calendar": NO_SCHEDULE,
            "default_max_gap_days": 4,
            "workflow": "decision/korea_population_symbol_observation.py -- no .github/workflows trigger exists",
        },
        {
            "id": "us_population_symbol_observation",
            "label_ko": "US 전체-모집단 심볼 관측",
            "kind": "GLOB",
            "glob": "data/observations/us_population_symbol_observation/*/summary.json",
            "calendar": NO_SCHEDULE,
            "default_max_gap_days": 4,
            "workflow": "decision/us_population_symbol_observation.py -- no .github/workflows trigger exists",
        },
    ]


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

NON_FRESH_STATUSES = {
    "STALE", "NEVER_PRODUCED", "NO_SCHEDULE_STALE", "DATE_FIELD_MISSING",
}


def build_report(root: Path = ROOT, today: dt.date | None = None, watchlist: list[dict] | None = None) -> dict:
    if today is None:
        today = dt.datetime.now(tz=KST).date()
    items = []
    for spec in watchlist or default_watchlist():
        if spec["kind"] == "FILE":
            items.append(evaluate_file_item(spec, root, today))
        elif spec["kind"] == "GLOB":
            items.append(evaluate_glob_item(spec, root, today))
        else:
            raise FreshnessWatchdogError(f"UNKNOWN_ITEM_KIND:{spec['kind']}")
    stale_items = [item for item in items if item["status"] in NON_FRESH_STATUSES]
    return {
        "schema_version": SCHEMA_VERSION,
        "as_of_kst_date": today.isoformat(),
        "items": items,
        "stale_items": stale_items,
        "all_fresh": not stale_items,
        "authority": {
            "read_only_watch": True,
            "final_regime_authorized": False,
            "capital_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "production_authorized": False,
            "trading_authorized": False,
        },
    }


def render_issue_body(report: dict) -> str:
    lines = [
        f"자동 점검 시각(KST 기준일): {report['as_of_kst_date']}",
        "주문·매매·자본 배분 권한은 열리지 않았습니다 (읽기 전용 관측).",
        "",
    ]
    for item in report["stale_items"]:
        status = item["status"]
        if status == "NEVER_PRODUCED":
            lines.append(f"- [{item['id']}] {item['label_ko']} -- 한 번도 생성된 적이 없습니다 ({item['detail']}).")
        elif status == "NO_SCHEDULE_STALE":
            lines.append(
                f"- [{item['id']}] {item['label_ko']} -- 자동 스케줄이 없는 산출물이 {item['age_days']}일째 갱신되지 않았습니다 "
                f"(마지막: {item['last_date']}, 기준 {item['threshold_days']}일)."
            )
        elif status == "DATE_FIELD_MISSING":
            lines.append(f"- [{item['id']}] {item['label_ko']} -- 날짜 필드를 읽을 수 없습니다 ({item['detail']}).")
        else:
            lines.append(
                f"- [{item['id']}] {item['label_ko']} -- {item['age_days']}일째 정체 "
                f"(마지막: {item['last_date']}, {item['detail']})."
            )
        if item.get("workflow"):
            lines.append(f"  · producer: {item['workflow']}")
    if not report["stale_items"]:
        lines.append("모든 감시 대상이 최신입니다.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--today", default=None, help="Override the KST decision date (testing/dispatch only).")
    parser.add_argument("--format", choices=["json", "issue"], default="json")
    parser.add_argument("--check", action="store_true", help="Exit 1 if any watched item is non-fresh.")
    args = parser.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else None
    report = build_report(today=today)

    if args.format == "issue":
        print(render_issue_body(report))
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))

    if args.check and not report["all_fresh"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
