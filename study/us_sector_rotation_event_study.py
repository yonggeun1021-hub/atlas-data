#!/usr/bin/env python3
"""US sector rotation state-rule event study on the 1-year Alpaca backfill.

Re-runs the US part of the pre-registered rotation event study
(`study/us_sector_rotation_preregistration_20260914.md`, sha256 pinned in
`PREREGISTRATION_SHA256`, written 2026-09-14T14:45Z before any result was
computed) on ~1 year of Alpaca IEX daily bars produced by
`collectors/us_price_history_backfill.py --live`, instead of the 129 sessions
available when the study was first run. User approval:
USER_RATIFICATION_CAPITAL_ROTATION_RULES_V1_20260915 decision US_BACKFILL.

What is NOT changed after seeing data (pre-registration §4): entities, the
20-session RS window vs SPY, TOP/BOTTOM = ranks 1-3 / 9-11, close t+1
execution, 5/10/20 horizons (MAIN = 10), candidate rules R1-k1..k3, R2, R3,
R4, R5, the 0.10% round-trip cost (and 2x), and every pass gate. The only
difference from the first run is the data window (the approved backfill
replaces the union of retained daily-capture revisions); dedupe is still
"later revision wins, conflicts counted". The computation is a line-by-line
port of the scratch engine used for the first run.

Public-repo data boundary: this module reads vendor rows only from a
directory outside the repository and emits ONLY aggregated statistics plus
input hashes. `validate_artifact` rejects any artifact that carries a price,
close, volume, bar or per-day returns field, a numeric array longer than 3,
any list longer than 24, a date-keyed mapping, or more than 8 date strings.

No network access, no trading/order/capital authority.

CLI:
  verify-preregistration
  run --backfill-dir DIR --out FILE [--receipt FILE]
  check-artifact --artifact FILE
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = Path(__file__).resolve()
PREREGISTRATION_PATH = ROOT / "study" / "us_sector_rotation_preregistration_20260914.md"
PREREGISTRATION_SHA256 = "ec18f16632dacbd1bc32a2ad6b835ccc4e4697429ecf5f2272256b25b67bcfca"

SCHEMA_VERSION = "us_sector_rotation_event_study/1"
STUDY_ID = "US_SECTOR_ROTATION_EVENT_STUDY_1Y_ALPACA_BACKFILL"
BACKFILL_PROJECTION_VERSION = "us_price_history_backfill_plan/1"

ENTITIES = ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK", "XLP", "XLRE", "XLU", "XLV", "XLY")
BENCHMARK = "SPY"
STUDY_SYMBOLS = ENTITIES + (BENCHMARK,)

# Pre-registration §1-§4, US column. Frozen; test-locked against the document.
PARAMETERS = {
    "rs_lookback_sessions": 20,
    "horizons_sessions": [5, 10, 20],
    "main_horizon_sessions": 10,
    "top_bucket_size": 3,
    "bottom_bucket_size": 3,
    "participation_short_long_sessions": [5, 20],
    "participation_min_ratio": 1.0,
    "watch_rank_improvement_min": 3,
    "watch_window_sessions": 5,
    "rrg_n_m": [20, 5],
    "momentum_rebalance_sessions_top_n": [20, 3],
    "round_trip_cost": 0.0010,
    "recent_split_sessions": 40,
    "whipsaw_window_observations": 5,
    "conversion_window_observations": 10,
    "gate_enter_min_n": 80,
    "gate_enter_min_n_nonoverlap": 30,
    "gate_enter_hit_margin": 0.05,
    "gate_repeatability_min_positive_sectors": 6,
    "gate_repeatability_min_events_per_sector": 3,
    "gate_exit_min_n": 30,
}
KINDS = {
    "R1-k1_ENTER": "enter", "R1-k2_ENTER": "enter", "R1-k3_ENTER": "enter", "R2_ENTER": "enter",
    "R3_WATCH": "enter", "R4_ENTER_LEADING": "enter", "R4_WATCH_IMPROVING": "enter", "R5_DAILY_TOPN": "enter",
    "R1-k1_EXIT": "exit", "R1-k2_EXIT": "exit", "R1-k3_EXIT": "exit",
    "R4_WARN_WEAKENING": "exit", "R4_EXIT_LAGGING": "exit",
}
# Input sanity only (not a study parameter): a "1 year" run must have ~250 sessions.
MIN_SESSIONS = 200
LARGE_DAILY_MOVE_LOG = 0.20  # diagnostic flag for unadjusted splits; never filters data

AUTHORITY = {
    "trading_or_order_authority": False,
    "capital_authority": False,
    "production_authorized": False,
    "rule_ratification_authority": False,
}

TOP_LEVEL_KEYS = {
    "schema_version", "study_id", "preregistration", "parameters", "parameters_sha256", "module_sha256",
    "inputs", "backfill", "baseline_hit_rate", "whipsaw", "watch_to_enter_conversion", "r5_rebalance",
    "events", "candidate_verdicts", "data_handling", "authority",
}
FORBIDDEN_KEY_TOKENS = {
    "close", "closes", "open", "opens", "high", "highs", "low", "lows", "price", "prices", "bar", "bars",
    "volume", "volumes", "vw", "vwap", "ohlc", "ohlcv", "series", "rows", "t", "c", "o", "l", "v",
}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
MAX_LIST_LENGTH = 24
MAX_NUMERIC_LIST_LENGTH = 3
MAX_DATE_STRINGS = 8
MAX_ARTIFACT_BYTES = 256_000


class StudyError(ValueError):
    """Fail-closed study input, pre-registration or artifact-schema error."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_bytes(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def verify_preregistration(path: Path = PREREGISTRATION_PATH) -> str:
    try:
        digest = sha256_bytes(path.read_bytes())
    except OSError as exc:
        raise StudyError("US_ROTATION_STUDY_PREREGISTRATION_MISSING") from exc
    if digest != PREREGISTRATION_SHA256:
        raise StudyError("US_ROTATION_STUDY_PREREGISTRATION_HASH_MISMATCH")
    return digest


# ---------------------------------------------------------------- inputs


def load_backfill_panel(backfill_dir: Path) -> tuple[dict, dict]:
    """Read backfill units (outside the repo) into an in-memory panel.

    Returns (panel, input_meta). panel holds per-day closes and dollar volume and
    must never be written anywhere; input_meta holds counts and hashes only.
    """
    backfill_dir = Path(backfill_dir).resolve()
    try:
        backfill_dir.relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise StudyError("US_ROTATION_STUDY_BACKFILL_DIR_INSIDE_PUBLIC_REPO")
    manifests = sorted(backfill_dir.glob("*.manifest.json"))
    if not manifests:
        raise StudyError("US_ROTATION_STUDY_BACKFILL_EMPTY")
    units = []
    ranges = set()
    unit_hashes = {}
    for manifest_path in manifests:
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            raise StudyError(f"US_ROTATION_STUDY_MANIFEST_INVALID:{manifest_path.name}") from exc
        required = ("symbol", "anchor_date", "raw_file", "raw_sha256", "range_start_date", "range_end_date")
        if not isinstance(manifest, dict) or any(not isinstance(manifest.get(k), str) for k in required) \
                or manifest.get("projection_version") != BACKFILL_PROJECTION_VERSION:
            raise StudyError(f"US_ROTATION_STUDY_MANIFEST_INVALID:{manifest_path.name}")
        raw_path = backfill_dir / manifest["raw_file"]
        if raw_path.parent != backfill_dir or not raw_path.is_file():
            raise StudyError(f"US_ROTATION_STUDY_RAW_MISSING:{manifest_path.name}")
        raw = raw_path.read_bytes()
        if sha256_bytes(raw) != manifest["raw_sha256"]:
            raise StudyError(f"US_ROTATION_STUDY_RAW_HASH_MISMATCH:{manifest_path.name}")
        stem = manifest_path.name[: -len(".manifest.json")]
        unit_hashes[stem] = manifest["raw_sha256"]
        ranges.add((manifest["range_start_date"], manifest["range_end_date"]))
        if manifest["symbol"] in STUDY_SYMBOLS:
            units.append((manifest["anchor_date"], manifest["symbol"], raw))
    if len(ranges) != 1:
        raise StudyError("US_ROTATION_STUDY_RANGE_INCONSISTENT")
    range_start, range_end = next(iter(ranges))
    anchors_by_symbol: dict[str, set] = {}
    for anchor, symbol, _raw in units:
        anchors_by_symbol.setdefault(symbol, set()).add(anchor)
    missing = [s for s in STUDY_SYMBOLS if s not in anchors_by_symbol]
    if missing:
        raise StudyError("US_ROTATION_STUDY_SYMBOL_MISSING:" + ",".join(missing))
    if len({frozenset(v) for v in anchors_by_symbol.values()}) != 1:
        raise StudyError("US_ROTATION_STUDY_ANCHOR_COVERAGE_INCONSISTENT")

    data = {s: {} for s in STUDY_SYMBOLS}
    conflicts = 0
    # Later anchor = later revision; on a conflicting close the later one wins (pre-registration §0).
    for anchor, symbol, raw in sorted(units, key=lambda u: (u[0], u[1])):
        try:
            body = json.loads(raw)
            bars = body["responses"][symbol]["bars"]
        except (json.JSONDecodeError, KeyError, TypeError) as exc:
            raise StudyError(f"US_ROTATION_STUDY_RAW_INVALID:{symbol}_{anchor}") from exc
        if not isinstance(bars, list):
            raise StudyError(f"US_ROTATION_STUDY_RAW_INVALID:{symbol}_{anchor}")
        for bar in bars:
            try:
                day = str(bar["t"])[:10]
                close = float(bar["c"])
                dollar_volume = float(bar["v"]) * float(bar.get("vw", bar["c"]))
            except (KeyError, TypeError, ValueError) as exc:
                raise StudyError(f"US_ROTATION_STUDY_BAR_INVALID:{symbol}_{anchor}") from exc
            if not DATE_RE.match(day) or not math.isfinite(close) or close <= 0 or not math.isfinite(dollar_volume):
                raise StudyError(f"US_ROTATION_STUDY_BAR_INVALID:{symbol}_{anchor}")
            if day > anchor:
                raise StudyError(f"US_ROTATION_STUDY_LOOKAHEAD_VIOLATION:{symbol}_{anchor}")
            if not (range_start <= day <= range_end):
                continue
            if day in data[symbol] and abs(data[symbol][day][0] - close) > 1e-9:
                conflicts += 1
            data[symbol][day] = (close, dollar_volume)
    dates = sorted(set.intersection(*[set(data[s]) for s in STUDY_SYMBOLS]))
    if len(dates) < MIN_SESSIONS:
        raise StudyError(f"US_ROTATION_STUDY_SESSIONS_INSUFFICIENT:{len(dates)}")
    panel = {
        "dates": dates,
        "close": {s: [data[s][d][0] for d in dates] for s in STUDY_SYMBOLS},
        "dollar_vol": {s: [data[s][d][1] for d in dates] for s in STUDY_SYMBOLS},
    }
    large_moves = {}
    for s in STUDY_SYMBOLS:
        closes = panel["close"][s]
        count = sum(1 for a, b in zip(closes, closes[1:]) if abs(math.log(b / a)) > LARGE_DAILY_MOVE_LOG)
        if count:
            large_moves[s] = count
    input_set = "\n".join(f"{stem}:{digest}" for stem, digest in sorted(unit_hashes.items())).encode()
    meta = {
        "source": "alpaca_iex_daily_bars_adjustment_raw_via_us_price_history_backfill",
        "unit_count": len(unit_hashes),
        "study_symbols": list(STUDY_SYMBOLS),
        "range_start_date": range_start,
        "range_end_date": range_end,
        "first_session": dates[0],
        "last_session": dates[-1],
        "session_count": len(dates),
        "revision_conflicts": conflicts,
        "large_daily_move_counts": large_moves,
        "unit_raw_sha256": dict(sorted(unit_hashes.items())),
        "input_set_sha256": sha256_bytes(input_set),
    }
    return panel, meta


# ---------------------------------------------------------------- engine (port of scratch engine.py)


def _ffill(values):
    out, last = [], None
    for x in values:
        if x is None:
            x = last
        out.append(x)
        last = x
    return out


def run_engine(panel: dict) -> dict:
    p = PARAMETERS
    dates = panel["dates"]
    T = len(dates)
    ents = list(ENTITIES)
    B = BENCHMARK
    C = {k: _ffill(v) for k, v in panel["close"].items()}
    V = {k: [x or 0.0 for x in v] for k, v in panel["dollar_vol"].items()}
    L = p["rs_lookback_sessions"]
    H = p["horizons_sessions"]
    top = p["top_bucket_size"]
    bot = p["bottom_bucket_size"]
    ne = len(ents)

    def lg(e, a, b):
        return math.log(C[e][b] / C[e][a])

    def rs(e, t):
        return (lg(e, t - L, t) - lg(B, t - L, t)) if e != B else 0.0

    rank = {e: [None] * T for e in ents}
    bucket = {e: [None] * T for e in ents}
    for t in range(L, T):
        order = sorted(ents, key=lambda e: (-rs(e, t), e))
        for r, e in enumerate(order, 1):
            rank[e][t] = r
            bucket[e][t] = "TOP" if r <= top else ("BOTTOM" if r > ne - bot else "MIDDLE")

    def fx(e, t, h, vs="avg"):
        a = t + 1
        b = t + 1 + h
        if b >= T:
            return None
        base = (sum(lg(x, a, b) for x in ents) / ne) if vs == "avg" else lg(B, a, b)
        return lg(e, a, b) - base

    base = {}
    for h in H:
        vals = [fx(e, t, h) for e in ents for t in range(L, T) if fx(e, t, h) is not None]
        base[h] = sum(1 for x in vals if x > 0) / len(vals) if vals else None

    events: dict[str, list] = {}

    def add(name, e, t, extra=None):
        events.setdefault(name, []).append({"e": e, "t": t, "date": dates[t], **(extra or {})})

    s_short, s_long = p["participation_short_long_sessions"]
    for k in (1, 2, 3):
        for e in ents:
            inpos = False
            entry_t = None
            b = bucket[e]
            for t in range(L + k, T):
                if not inpos:
                    if all(b[t - j] == "TOP" for j in range(k)) and b[t - k] != "TOP":
                        inpos = True
                        entry_t = t
                        ok = None
                        if t >= s_long:
                            sh = sum(V[e][t - s_short + 1:t + 1]) / s_short
                            lo = sum(V[e][t - s_long + 1:t + 1]) / s_long
                            ok = lo > 0 and sh / lo >= p["participation_min_ratio"]
                        add(f"R1-k{k}_ENTER", e, t, {"part_ok": ok})
                        if k == 2 and ok:
                            add("R2_ENTER", e, t)
                else:
                    if b[t] == "BOTTOM" or (b[t] != "TOP" and b[t - 1] != "TOP" and t - 1 > entry_t):
                        add(f"R1-k{k}_EXIT", e, t, {"held": t - entry_t})
                        inpos = False

    whip = {}
    for k in (1, 2, 3):
        en = events.get(f"R1-k{k}_ENTER", [])
        ex = events.get(f"R1-k{k}_EXIT", [])
        w = 0
        for ev in en:
            nxt = [x for x in ex if x["e"] == ev["e"] and x["t"] > ev["t"]]
            if nxt and nxt[0]["t"] - ev["t"] <= p["whipsaw_window_observations"]:
                w += 1
        whip[k] = (w, len(en))

    d = p["watch_rank_improvement_min"]
    w = p["watch_window_sessions"]
    for e in ents:
        last = -10 ** 9
        for t in range(L + w, T):
            if bucket[e][t] in ("MIDDLE", "BOTTOM") and rank[e][t - w] is not None \
                    and rank[e][t - w] - rank[e][t] >= d and t - last > w:
                add("R3_WATCH", e, t)
                last = t
    conv = 0
    wl = events.get("R3_WATCH", [])
    en2 = events.get("R1-k2_ENTER", [])
    for ev in wl:
        if any(x["e"] == ev["e"] and 0 < x["t"] - ev["t"] <= p["conversion_window_observations"] for x in en2):
            conv += 1

    N, M = p["rrg_n_m"]
    for e in ents:
        if e == B:
            continue
        X = [C[e][t] / C[B][t] for t in range(T)]
        rsr = [None] * T
        for t in range(N - 1, T):
            rsr[t] = 100 * X[t] / (sum(X[t - N + 1:t + 1]) / N)
        q = [None] * T
        for t in range(N - 1 + M, T):
            rsm = 100 * rsr[t] / rsr[t - M]
            q[t] = ("LEADING" if rsm > 100 else "WEAKENING") if rsr[t] > 100 else ("IMPROVING" if rsm > 100 else "LAGGING")
        for t in range(N + M, T):
            if q[t - 1] is None:
                continue
            if q[t] == "LEADING" and q[t - 1] != "LEADING":
                add("R4_ENTER_LEADING", e, t)
            if q[t] == "WEAKENING" and q[t - 1] == "LEADING":
                add("R4_WARN_WEAKENING", e, t)
            if q[t] == "LAGGING" and q[t - 1] != "LAGGING":
                add("R4_EXIT_LAGGING", e, t)
            if q[t] == "IMPROVING" and q[t - 1] == "LAGGING":
                add("R4_WATCH_IMPROVING", e, t)

    reb, topn = p["momentum_rebalance_sessions_top_n"]
    r5 = []
    t = L
    while t + 1 + reb < T:
        order = sorted(ents, key=lambda e: (-lg(e, t - L, t), e))[:topn]
        ex = sum(fx(e, t, reb) for e in order) / topn
        r5.append({"date": dates[t], "picks": order, "excess": ex})
        t += reb
    for t in range(L, T):
        order = sorted(ents, key=lambda e: (-lg(e, t - L, t), e))[:topn]
        for e in order:
            add("R5_DAILY_TOPN", e, t)

    recent_start = dates[T - p["main_horizon_sessions"] - 1 - p["recent_split_sessions"]]
    return {"dates": dates, "events": events, "fx": fx, "base": base, "whip": whip,
            "conv": (conv, len(wl)), "r5": r5, "T": T, "recent_start": recent_start}


def summarize(res: dict, ev_name: str, h: int, kind: str) -> dict:
    fx = res["fx"]
    evs = res["events"].get(ev_name, [])
    rows = [(ev, fx(ev["e"], ev["t"], h)) for ev in evs]
    rows = [(ev, x) for ev, x in rows if x is not None]
    n = len(rows)
    if n == 0:
        return {"event": ev_name, "h": h, "n": 0}
    xs = [x for _, x in rows]
    by_entity: dict = {}
    nno = 0
    for ev, _x in sorted(rows, key=lambda r: r[0]["t"]):
        if ev["e"] not in by_entity or ev["t"] - by_entity[ev["e"]] >= h:
            nno += 1
            by_entity[ev["e"]] = ev["t"]
    mean = sum(xs) / n
    if kind == "enter":
        trimmed = sorted(xs)[:-1]
        false_rate = sum(1 for x in xs if x < 0) / n
    else:
        trimmed = sorted(xs)[1:]
        false_rate = sum(1 for x in xs if x > 0) / n
    rs = res["recent_start"]
    rec = [x for ev, x in rows if ev["date"] >= rs]
    old = [x for ev, x in rows if ev["date"] < rs]
    per_entity: dict = {}
    for ev, x in rows:
        per_entity.setdefault(ev["e"], []).append(x)
    bench = [fx(ev["e"], ev["t"], h, vs="bench") for ev, _x in rows]
    c = PARAMETERS["round_trip_cost"]
    return {
        "event": ev_name, "h": h, "n": n, "n_nonoverlap": nno, "mean": mean, "median": statistics.median(xs),
        "hit": sum(1 for x in xs if x > 0) / n, "base_hit": res["base"][h], "false_rate": false_rate,
        "mean_trim": (sum(trimmed) / len(trimmed)) if trimmed else None,
        "recent_n": len(rec), "recent_mean": (sum(rec) / len(rec)) if rec else None,
        "early_n": len(old), "early_mean": (sum(old) / len(old)) if old else None,
        "mean_cost": mean - c, "mean_2cost": mean - 2 * c,
        "mean_vs_benchmark": sum(bench) / len(bench),
        "per_entity": {e: (len(v), sum(v) / len(v)) for e, v in per_entity.items()},
    }


def gate(s: dict, kind: str) -> tuple[str, dict]:
    p = PARAMETERS
    if s["n"] == 0:
        return "INSUFFICIENT(n=0)", {}
    if kind == "enter":
        if s["n"] < p["gate_enter_min_n"] or s["n_nonoverlap"] < p["gate_enter_min_n_nonoverlap"]:
            return "INSUFFICIENT", {}
        eligible = [v for v in s["per_entity"].values() if v[0] >= p["gate_repeatability_min_events_per_sector"]]
        repeat_ok = sum(1 for v in eligible if v[1] > 0) >= p["gate_repeatability_min_positive_sectors"]
        checks = {
            "cost2x": s["mean_2cost"] > 0,
            "hit": s["hit"] >= s["base_hit"] + p["gate_enter_hit_margin"],
            "trim": (s["mean_trim"] or -1) > 0,
            "recent": s["recent_mean"] is None or s["recent_mean"] >= 0,
            "repeat": repeat_ok,
        }
        verdict = "PASS" if all(checks.values()) else "FAIL:" + ",".join(k for k, v in checks.items() if not v)
        return verdict, checks
    if s["n"] < p["gate_exit_min_n"]:
        return "INSUFFICIENT", {}
    checks = {
        "neg": s["mean"] < 0,
        "trim": (s["mean_trim"] or 1) < 0,
        "recent": s["recent_mean"] is None or s["recent_mean"] < 0,
    }
    verdict = "JUSTIFIED" if all(checks.values()) else "NOT_JUSTIFIED:" + ",".join(k for k, v in checks.items() if not v)
    return verdict, checks


# ---------------------------------------------------------------- artifact


def _r(x, digits=6):
    return None if x is None else round(float(x), digits)


def _horizon_stats(s: dict) -> dict:
    if s["n"] == 0:
        return {"n": 0}
    p = PARAMETERS
    eligible = [v for v in s["per_entity"].values() if v[0] >= p["gate_repeatability_min_events_per_sector"]]
    return {
        "n": s["n"],
        "n_nonoverlap": s["n_nonoverlap"],
        "mean_forward_excess": _r(s["mean"]),
        "median_forward_excess": _r(s["median"]),
        "hit_rate": _r(s["hit"]),
        "baseline_hit_rate": _r(s["base_hit"]),
        "false_signal_rate": _r(s["false_rate"]),
        "mean_without_extreme_event": _r(s["mean_trim"]),
        "recent_n": s["recent_n"],
        "recent_mean": _r(s["recent_mean"]),
        "early_n": s["early_n"],
        "early_mean": _r(s["early_mean"]),
        "mean_after_cost": _r(s["mean_cost"]),
        "mean_after_2x_cost": _r(s["mean_2cost"]),
        "mean_forward_excess_vs_benchmark": _r(s["mean_vs_benchmark"]),
        "repeatability_eligible_sectors": len(eligible),
        "repeatability_positive_sectors": sum(1 for v in eligible if v[1] > 0),
        "per_sector": {e: {"n": v[0], "mean": _r(v[1])} for e, v in sorted(s["per_entity"].items())},
    }


def build_artifact(panel: dict, meta: dict, receipt: dict | None = None) -> dict:
    prereg_sha = verify_preregistration()
    res = run_engine(panel)
    main_h = PARAMETERS["main_horizon_sessions"]
    events = {}
    verdicts = {}
    for name, kind in KINDS.items():
        horizons = {}
        verdict = None
        checks = {}
        for h in PARAMETERS["horizons_sessions"]:
            s = summarize(res, name, h, kind)
            horizons[f"h{h}"] = _horizon_stats(s)
            if h == main_h:
                verdict, checks = gate(s, kind)
        events[name] = {"kind": kind, "horizons": horizons, "gate_main_horizon": verdict, "gate_checks": checks}
        verdicts[name] = verdict
    r5 = res["r5"]
    ex = [x["excess"] for x in r5]
    rs = res["recent_start"]
    rec = [x["excess"] for x in r5 if x["date"] >= rs]
    old = [x["excess"] for x in r5 if x["date"] < rs]
    picks = {e: 0 for e in ENTITIES}
    for x in r5:
        for e in x["picks"]:
            picks[e] += 1
    backfill = None
    if receipt is not None:
        backfill = {k: receipt.get(k) for k in (
            "schema_version", "start_date", "end_date", "symbol_count", "total_requests",
            "requests_made", "units_skipped_existing", "max_requests", "total_row_count",
        )}
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "study_id": STUDY_ID,
        "preregistration": {
            "file": PREREGISTRATION_PATH.relative_to(ROOT).as_posix(),
            "sha256": prereg_sha,
            "verified": True,
            "parameters_changed_after_data": False,
        },
        "parameters": PARAMETERS,
        "parameters_sha256": sha256_bytes(canonical_bytes(PARAMETERS)),
        "module_sha256": sha256_bytes(MODULE_PATH.read_bytes()),
        "inputs": {k: v for k, v in meta.items()},
        "backfill": backfill,
        "baseline_hit_rate": {f"h{h}": _r(v) for h, v in res["base"].items()},
        "whipsaw": {f"R1-k{k}": {"whipsaw_events": w, "enter_events": n, "rate": _r(w / n) if n else None}
                    for k, (w, n) in res["whip"].items()},
        "watch_to_enter_conversion": {
            "converted": res["conv"][0], "watch_events": res["conv"][1],
            "rate": _r(res["conv"][0] / res["conv"][1]) if res["conv"][1] else None,
        },
        "r5_rebalance": {
            "n": len(r5),
            "mean_excess": _r(sum(ex) / len(ex)) if ex else None,
            "hit_rate": _r(sum(1 for x in ex if x > 0) / len(ex)) if ex else None,
            "mean_without_best": _r(sum(sorted(ex)[:-1]) / (len(ex) - 1)) if len(ex) > 1 else None,
            "recent_n": len(rec), "recent_mean": _r(sum(rec) / len(rec)) if rec else None,
            "early_n": len(old), "early_mean": _r(sum(old) / len(old)) if old else None,
            "pick_counts": picks,
        },
        "events": events,
        "candidate_verdicts": verdicts,
        "data_handling": {
            "vendor_quote_fields_included": False,
            "daily_return_arrays_included": False,
            "per_event_records_included": False,
            "vendor_data_location": "RUNNER_TEMP_OUTSIDE_CHECKOUT_NEVER_UPLOADED",
        },
        "authority": dict(AUTHORITY),
    }
    validate_artifact(artifact)
    return artifact


def validate_artifact(doc) -> None:
    """Fail closed unless the artifact is aggregate-only (no price/return series)."""
    if not isinstance(doc, dict) or set(doc) != TOP_LEVEL_KEYS:
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_TOP_LEVEL_KEYS_INVALID")
    if doc["schema_version"] != SCHEMA_VERSION:
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_SCHEMA_VERSION_INVALID")
    if doc["preregistration"].get("sha256") != PREREGISTRATION_SHA256 or doc["preregistration"].get("verified") is not True:
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_PREREGISTRATION_INVALID")
    if doc["parameters"] != PARAMETERS or doc["parameters_sha256"] != sha256_bytes(canonical_bytes(PARAMETERS)):
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_PARAMETERS_CHANGED")
    if set(doc["events"]) != set(KINDS) or set(doc["candidate_verdicts"]) != set(KINDS):
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_CANDIDATES_INVALID")
    if any(value is not False for value in doc["authority"].values()):
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_AUTHORITY_INVALID")
    if len(canonical_bytes(doc)) > MAX_ARTIFACT_BYTES:
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_TOO_LARGE")
    date_strings = 0

    def walk(value, where):
        nonlocal date_strings
        if isinstance(value, dict):
            for key, item in value.items():
                if not isinstance(key, str):
                    raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_KEY_INVALID:{where}")
                if DATE_RE.fullmatch(key) or re.fullmatch(r"\d{4}-\d{2}-\d{2}.*", key):
                    raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_DATE_KEYED_MAPPING:{where}")
                tokens = set(re.split(r"[_\-.\s]+", key.lower()))
                if tokens & FORBIDDEN_KEY_TOKENS:
                    raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_FORBIDDEN_FIELD:{where}.{key}")
                walk(item, f"{where}.{key}")
        elif isinstance(value, list):
            if len(value) > MAX_LIST_LENGTH:
                raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_LIST_TOO_LONG:{where}")
            if any(isinstance(x, (int, float)) and not isinstance(x, bool) for x in value) \
                    and len(value) > MAX_NUMERIC_LIST_LENGTH:
                raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_NUMERIC_ARRAY:{where}")
            for i, item in enumerate(value):
                if isinstance(item, (dict, list)):
                    raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_NESTED_LIST:{where}[{i}]")
                walk(item, f"{where}[{i}]")
        elif isinstance(value, str):
            if DATE_RE.match(value):
                date_strings += 1
        elif value is None or isinstance(value, (bool, int, float)):
            if isinstance(value, float) and not math.isfinite(value):
                raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_NON_FINITE:{where}")
        else:
            raise StudyError(f"US_ROTATION_STUDY_ARTIFACT_TYPE_INVALID:{where}")

    walk(doc, "$")
    if date_strings > MAX_DATE_STRINGS:
        raise StudyError("US_ROTATION_STUDY_ARTIFACT_TOO_MANY_DATES")


def _write_new(path: Path, data: bytes) -> None:
    path = Path(path)
    try:
        path.resolve().relative_to(ROOT)
    except ValueError:
        pass
    else:
        raise StudyError("US_ROTATION_STUDY_OUT_INSIDE_PUBLIC_REPO")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with open(path, "xb") as handle:
            handle.write(data)
    except FileExistsError as exc:
        raise StudyError("US_ROTATION_STUDY_OUT_ALREADY_EXISTS") from exc


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="US sector rotation pre-registered event study (offline).")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("verify-preregistration")
    run = sub.add_parser("run")
    run.add_argument("--backfill-dir", type=Path, required=True)
    run.add_argument("--out", type=Path, required=True)
    run.add_argument("--receipt", type=Path, default=None)
    check = sub.add_parser("check-artifact")
    check.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "verify-preregistration":
            print(json.dumps({"preregistration_sha256": verify_preregistration(), "verified": True}))
            return 0
        if args.command == "run":
            receipt = None
            if args.receipt is not None:
                try:
                    receipt = json.loads(args.receipt.read_text())
                except (OSError, json.JSONDecodeError) as exc:
                    raise StudyError("US_ROTATION_STUDY_RECEIPT_INVALID") from exc
            panel, meta = load_backfill_panel(args.backfill_dir)
            artifact = build_artifact(panel, meta, receipt)
            _write_new(args.out, json.dumps(artifact, sort_keys=True, indent=1).encode())
            print(json.dumps({"status": "WRITTEN", "session_count": meta["session_count"],
                              "candidate_verdicts": artifact["candidate_verdicts"]}, sort_keys=True))
            return 0
        doc = json.loads(args.artifact.read_text())
        validate_artifact(doc)
        print(json.dumps({"status": "ARTIFACT_SCHEMA_OK"}))
        return 0
    except (StudyError, OSError, json.JSONDecodeError) as exc:
        code = str(exc) if isinstance(exc, StudyError) else type(exc).__name__
        print(json.dumps({"status": "FAILED", "error": code}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
