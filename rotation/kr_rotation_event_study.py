#!/usr/bin/env python3
"""Pre-registered KR rotation event study on a 20-session relative-strength basis.

Implements exactly `config/kr_rotation_event_study_preregistration.json`
(sha256 pinned below, committed before any KRX history was fetched) over the
private per-(market, date) records written by
`collectors/kr_sector_index_history_backfill.py`.

Structure mirrors the US/crypto study in the CIO capital-rotation proposal
section B: R1-k confirmation (k = 1, 2, 3) with the ratified release rule, R2
participation filter, R3 rising watch, R4 RRG-style quadrant approximation,
R5 top-3 momentum baseline, plus an explicitly labelled comparison on the
current temporary 1-session basis.

The public output contains aggregates, counts, verdicts and input hashes only.
`validate_public_artifact` refuses index values, per-day series and raw
provider content. Verdicts are evidence; they grant no policy, ledger,
candidate, order or trading authority.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
import sys

ROOT = Path(__file__).resolve().parents[1]
PREREGISTRATION_PATH = ROOT / "config" / "kr_rotation_event_study_preregistration.json"
PREREGISTRATION_SHA256 = "01d13ed7e4337fdfb6a1516c8efd7b83b27a3e7d1eb5431b623002ed39a3cc71"
POLICY_PATH = ROOT / "config" / "korea_capital_rotation_policy_ratified.json"
BACKFILL_CONTRACT_PATH = ROOT / "config" / "kr_sector_index_history_backfill_contract.json"
BACKFILL_MODULE_PATH = ROOT / "collectors" / "kr_sector_index_history_backfill.py"
STUDY_SCHEMA = "kr_rotation_event_study_result/1"
RECEIPT_SCHEMA = "kr_sector_index_history_backfill_receipt/1"
RECORD_SCHEMA = "kr_sector_index_history_record/1"
MARKETS = ("KOSPI", "KOSDAQ")

# Pre-registered parameters (must equal the pre-registration file).
L_PRIMARY = 20
L_COMPARISON = 1
HORIZONS = (5, 10, 20)
MAIN = 10
TOP = 3
BOTTOM = 3
COST = 0.003
STRESS = 2
RECENT_SIGNAL_SESSIONS = 120
MIN_WINDOW = 250
TURNOVER = (5, 20)
WATCH = (3, 5)
RRG = (20, 5)
MOMENTUM = (20, 3)
ENTER_MIN_N, ENTER_MIN_NNO, EXIT_MIN_N = 80, 30, 30

KINDS = {
    "R1-k1_ENTER": "enter", "R1-k2_ENTER": "enter", "R1-k3_ENTER": "enter",
    "R1-k1_EXIT": "exit", "R1-k2_EXIT": "exit", "R1-k3_EXIT": "exit",
    "R2_ENTER": "enter", "R3_WATCH": "enter",
    "R4_ENTER_LEADING": "enter", "R4_WATCH_IMPROVING": "enter",
    "R4_WARN_WEAKENING": "exit", "R4_EXIT_LAGGING": "exit",
    "R5_DAILY_TOP3": "enter",
    "L1_R1-k2_ENTER": "enter", "L1_R1-k2_EXIT": "exit",
}

AUTHORITY = {
    "evidence_only": True,
    "policy_change_authorized": False,
    "rotation_state_ledger_write_authorized": False,
    "candidate_authorized": False,
    "order_authorized": False,
    "trading_authorized": False,
    "real_capital_authorized": False,
}


class StudyError(ValueError):
    pass


def compact_sha256(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def file_sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def verify_preregistration(path: Path = PREREGISTRATION_PATH) -> dict:
    if file_sha256(path) != PREREGISTRATION_SHA256:
        raise StudyError("PREREGISTRATION_HASH_MISMATCH")
    value = json.loads(Path(path).read_text(encoding="utf-8"))
    d = value["definitions"]
    g = value["gates"]
    if (
        value["study_id"] != "KR_ROTATION_20SESSION_EVENT_STUDY_V1"
        or tuple(d["horizons_sessions"]) != HORIZONS
        or d["main_horizon_sessions"] != MAIN
        or d["round_trip_cost"] != COST
        or d["stress_cost_multiplier"] != STRESS
        or value["coverage_rule"]["minimum_window_sessions"] != MIN_WINDOW
        or [c["id"] for c in value["candidates"]] != list(KINDS)
        or any(KINDS[c["id"]] != c["kind"] for c in value["candidates"])
        or "n < 80 or non_overlapping_n < 30" != g["enter"]["insufficient_if"]
        or "n < 30" != g["exit"]["insufficient_if"]
        or value["references"]["ratified_sector_policy"]["sha256"] != file_sha256(POLICY_PATH)
    ):
        raise StudyError("PREREGISTRATION_PARAMETERS_DIVERGE_FROM_IMPLEMENTATION")
    for key, flag in value["authority"].items():
        if flag is not False:
            raise StudyError("PREREGISTRATION_AUTHORITY_INVALID")
    return value


# ---------------------------------------------------------------- public boundary

FORBIDDEN_KEY_SUBSTRINGS = (
    "close", "clsprc", "opnprc", "hgprc", "lwprc", "acc_trd", "trdval", "trading_value",
    "price", "index_level", "series", "base64", "auth_key", "raw_response", "provider_raw",
    "quadrant_by", "rank_by", "rs_by", "returns_by", "per_event", "event_dates",
)
KRX_FORMATTED_NUMBER = re.compile(r"^-?[0-9]{1,3}(,[0-9]{3})+(\.[0-9]+)?$")
DATE_KEY = re.compile(r"^[0-9]{4}-?[0-9]{2}-?[0-9]{2}$")
MAX_NUMERIC_LIST = 4
MAX_PUBLIC_BYTES = 2_000_000
RECEIPT_TOP_KEYS = {
    "schema_version", "status", "stop", "range", "source", "request_accounting", "calendar_years",
    "sessions", "identity_coverage", "index_name_catalogs", "manifest_sha256",
    "records_payload_sha256", "manifest", "data_handling", "authority",
}
STUDY_TOP_KEYS = {
    "schema_version", "study_id", "status", "reason", "preregistration", "inputs", "backfill_summary",
    "coverage", "baseline_hit_rate", "candidates", "descriptive", "verdict_summary",
    "data_handling", "authority",
}
CANDIDATE_KEYS = {
    "event", "kind", "horizon", "n", "n_nonoverlap", "mean", "median", "hit_rate", "baseline_hit_rate",
    "false_signal_rate", "mean_without_extreme", "recent_n", "recent_mean", "earlier_n", "earlier_mean",
    "half1_n", "half1_mean", "half2_n", "half2_mean", "mean_after_cost", "mean_after_stress_cost",
    "per_scope", "per_sector", "gate", "gate_failed_checks",
}


def validate_public_artifact(value: dict) -> None:
    raw = canonical_bytes(value)
    if len(raw) > MAX_PUBLIC_BYTES:
        raise StudyError("PUBLIC_ARTIFACT_TOO_LARGE")
    schema = value.get("schema_version") if isinstance(value, dict) else None
    if schema == RECEIPT_SCHEMA:
        if set(value) != RECEIPT_TOP_KEYS:
            raise StudyError("PUBLIC_RECEIPT_KEYS_INVALID")
    elif schema == STUDY_SCHEMA:
        if set(value) != STUDY_TOP_KEYS:
            raise StudyError("PUBLIC_STUDY_KEYS_INVALID")
        for row in value.get("candidates") or []:
            if not set(row) <= CANDIDATE_KEYS:
                raise StudyError("PUBLIC_CANDIDATE_KEYS_INVALID")
    else:
        raise StudyError("PUBLIC_ARTIFACT_SCHEMA_UNKNOWN")

    def walk(node, path):
        if isinstance(node, dict):
            for key, child in node.items():
                lowered = str(key).lower()
                if any(part in lowered for part in FORBIDDEN_KEY_SUBSTRINGS):
                    raise StudyError(f"PUBLIC_FORBIDDEN_KEY:{path}/{key}")
                if DATE_KEY.fullmatch(str(key)):
                    raise StudyError(f"PUBLIC_DATE_KEYED_MAP:{path}")
                walk(child, f"{path}/{key}")
        elif isinstance(node, list):
            numeric = [x for x in node if isinstance(x, (int, float)) and not isinstance(x, bool)]
            if len(numeric) > MAX_NUMERIC_LIST:
                raise StudyError(f"PUBLIC_NUMERIC_SEQUENCE:{path}")
            for index, child in enumerate(node):
                walk(child, f"{path}[{index}]")
        elif isinstance(node, str):
            if KRX_FORMATTED_NUMBER.fullmatch(node.strip()):
                raise StudyError(f"PUBLIC_FORMATTED_NUMBER:{path}")

    walk(value, "")


# ---------------------------------------------------------------- inputs

def ratified_scopes(path: Path = POLICY_PATH) -> dict:
    policy = json.loads(Path(path).read_text(encoding="utf-8"))
    scopes = {}
    for scope in policy["benchmark_scopes"]:
        market = scope["benchmark_identity"].split("::", 1)[0]
        scopes[market] = {
            "benchmark": scope["benchmark_identity"],
            "members": sorted(m["series_identity"] for m in scope["members"]),
        }
    if set(scopes) != set(MARKETS):
        raise StudyError("RATIFIED_SCOPES_INVALID")
    return scopes


def load_records(receipt: dict, records_dir: Path) -> dict:
    """{(market, bas_dd): record} in manifest order; verifies the receipt binding."""
    records, ordered = {}, []
    for entry in receipt["manifest"]:
        path = Path(records_dir) / entry["market"] / f"{entry['bas_dd']}.json"
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudyError("RECORD_MISSING_OR_INVALID") from exc
        if (
            record.get("schema_version") != RECORD_SCHEMA
            or record.get("response_sha256") != entry["response_sha256"]
            or record.get("status") != entry["status"]
        ):
            raise StudyError("RECORD_DIVERGES_FROM_RECEIPT_MANIFEST")
        records[(entry["market"], entry["bas_dd"])] = record
        ordered.append(record)
    if compact_sha256(ordered) != receipt["records_payload_sha256"]:
        raise StudyError("RECORDS_PAYLOAD_HASH_MISMATCH")
    if compact_sha256(receipt["manifest"]) != receipt["manifest_sha256"]:
        raise StudyError("MANIFEST_HASH_MISMATCH")
    return records


def confirmed_sessions(records: dict) -> list[str]:
    dates = sorted({bas_dd for (_, bas_dd) in records})
    return [
        d for d in dates
        if all(records.get((m, d), {}).get("status") == "ROWS" for m in MARKETS)
    ]


def _positive(value) -> float | None:
    if value is None:
        return None
    try:
        number = float(str(value).replace(",", ""))
    except ValueError:
        return None
    return number if math.isfinite(number) and number > 0 else None


def scope_window(records: dict, sessions: list[str], market: str, scope: dict) -> dict:
    identities = [scope["benchmark"], *scope["members"]]
    window = []
    for bas_dd in reversed(sessions):
        series = records[(market, bas_dd)]["series"]
        if all(_positive(series.get(i, {}).get("close")) is not None for i in identities):
            window.append(bas_dd)
        else:
            break
    window.reverse()
    closes = {i: [_positive(records[(market, d)]["series"][i]["close"]) for d in window] for i in identities}
    turnover = {}
    for i in scope["members"]:
        turnover[i] = [_positive(records[(market, d)]["series"][i].get("trading_value")) for d in window]
    return {"dates": window, "closes": closes, "turnover": turnover}


# ---------------------------------------------------------------- engine

def _iso(bas_dd: str) -> str:
    return f"{bas_dd[:4]}-{bas_dd[4:6]}-{bas_dd[6:]}"


def run_scope(market: str, scope: dict, data: dict) -> dict:
    ents = scope["members"]
    bench = scope["benchmark"]
    P = data["closes"]
    V = data["turnover"]
    T = len(data["dates"])
    ne = len(ents)

    def lg(e, a, b):
        return math.log(P[e][b] / P[e][a])

    def buckets_for(L):
        rank = {e: [None] * T for e in ents}
        bucket = {e: [None] * T for e in ents}
        for t in range(L, T):
            bl = lg(bench, t - L, t)
            order = sorted(ents, key=lambda e: (-(lg(e, t - L, t) - bl), e))
            for r, e in enumerate(order, 1):
                rank[e][t] = r
                bucket[e][t] = "TOP" if r <= TOP else ("BOTTOM" if r > ne - BOTTOM else "MIDDLE")
        return rank, bucket

    fx_cache = {}

    def fx(e, t, h):
        key = (e, t, h)
        if key not in fx_cache:
            a, b = t + 1, t + 1 + h
            if b >= T:
                fx_cache[key] = None
            else:
                base = sum(lg(x, a, b) for x in ents) / ne
                fx_cache[key] = lg(e, a, b) - base
        return fx_cache[key]

    events = {name: [] for name in KINDS}
    recent_start = T - MAIN - 1 - RECENT_SIGNAL_SESSIONS
    midpoint = (L_PRIMARY + T) // 2
    r2_dropped_missing_turnover = 0

    def add(name, e, t):
        events[name].append({"e": e, "t": t})

    rank20, bucket20 = buckets_for(L_PRIMARY)
    rank1, bucket1 = buckets_for(L_COMPARISON)

    whipsaw, held = {}, []

    def r1(bucket, L, k, prefix):
        nonlocal r2_dropped_missing_turnover
        enter_name, exit_name = f"{prefix}R1-k{k}_ENTER", f"{prefix}R1-k{k}_EXIT"
        pairs = []
        for e in ents:
            b = bucket[e]
            in_position, entry_t = False, None
            for t in range(L + k, T):
                if not in_position:
                    if all(b[t - j] == "TOP" for j in range(k)) and b[t - k] != "TOP":
                        in_position, entry_t = True, t
                        add(enter_name, e, t)
                        if prefix == "" and k == 2:
                            short_n, long_n = TURNOVER
                            if t >= long_n - 1:
                                window = V[e][t - long_n + 1: t + 1]
                                if any(v is None for v in window):
                                    r2_dropped_missing_turnover += 1
                                else:
                                    short = sum(window[-short_n:]) / short_n
                                    long = sum(window) / long_n
                                    if long > 0 and short / long >= 1.0:
                                        add("R2_ENTER", e, t)
                else:
                    if b[t] == "BOTTOM" or (b[t] != "TOP" and b[t - 1] != "TOP" and t - 1 > entry_t):
                        add(exit_name, e, t)
                        pairs.append((e, entry_t, t))
                        in_position = False
        return pairs

    for k in (1, 2, 3):
        pairs = r1(bucket20, L_PRIMARY, k, "")
        enters = [ev for ev in events[f"R1-k{k}_ENTER"]]
        quick = sum(1 for (_, s, x) in pairs if x - s <= 5)
        whipsaw[str(k)] = {"enter_n": len(enters), "exit_within_5_n": quick}
        if k == 2:
            held = [x - s for (_, s, x) in pairs]
    r1(bucket1, L_COMPARISON, 2, "L1_")

    d, w = WATCH
    for e in ents:
        last = -10 ** 9
        for t in range(L_PRIMARY + w, T):
            if (
                bucket20[e][t] in ("MIDDLE", "BOTTOM")
                and rank20[e][t - w] is not None
                and rank20[e][t - w] - rank20[e][t] >= d
                and t - last > w
            ):
                add("R3_WATCH", e, t)
                last = t
    conversions = sum(
        1 for ev in events["R3_WATCH"]
        if any(x["e"] == ev["e"] and 0 < x["t"] - ev["t"] <= 10 for x in events["R1-k2_ENTER"])
    )

    N, M = RRG
    for e in ents:
        X = [P[e][t] / P[bench][t] for t in range(T)]
        rsr = [None] * T
        for t in range(N - 1, T):
            rsr[t] = 100 * X[t] / (sum(X[t - N + 1: t + 1]) / N)
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

    lookback, top_n = MOMENTUM
    rebalances = []
    t = lookback
    while t + 1 + lookback < T:
        picks = sorted(ents, key=lambda e: (-lg(e, t - lookback, t), e))[:top_n]
        rebalances.append({"t": t, "excess": sum(fx(e, t, lookback) for e in picks) / top_n})
        t += lookback
    for t in range(lookback, T):
        for e in sorted(ents, key=lambda e: (-lg(e, t - lookback, t), e))[:top_n]:
            add("R5_DAILY_TOP3", e, t)

    def persistence(bucket, L):
        stay = total = 0
        for e in ents:
            for t in range(L, T - 1):
                if bucket[e][t] == "TOP":
                    total += 1
                    stay += bucket[e][t + 1] == "TOP"
        return {"top_n": total, "stayed_top_n": stay}

    baseline = {}
    for h in HORIZONS:
        values = [fx(e, t, h) for e in ents for t in range(L_PRIMARY, T)]
        values = [v for v in values if v is not None]
        baseline[h] = (sum(1 for v in values if v > 0), len(values))

    return {
        "market": market,
        "T": T,
        "events": events,
        "fx": fx,
        "recent_start": recent_start,
        "midpoint": midpoint,
        "baseline": baseline,
        "whipsaw": whipsaw,
        "held_k2": held,
        "conversion": {"watch_n": len(events["R3_WATCH"]), "converted_within_10_n": conversions},
        "rebalances": rebalances,
        "r2_dropped_missing_turnover": r2_dropped_missing_turnover,
        "persistence": {"L20": persistence(bucket20, L_PRIMARY), "L1": persistence(bucket1, L_COMPARISON)},
    }


def _r(x):
    return None if x is None else round(float(x), 6)


def _mean(xs):
    return sum(xs) / len(xs) if xs else None


def summarize(runs: list, name: str, h: int, base_hit: float) -> dict:
    kind = KINDS[name]
    rows = []
    for run in runs:
        for ev in run["events"][name]:
            value = run["fx"](ev["e"], ev["t"], h)
            if value is not None:
                rows.append((run, ev, value))
    xs = [v for _, _, v in rows]
    n = len(xs)
    out = {"event": name, "kind": kind, "horizon": h, "n": n}
    if n == 0:
        out.update({"n_nonoverlap": 0, "baseline_hit_rate": _r(base_hit)})
        return out
    nno = 0
    last_t = {}
    for run, ev, _ in sorted(rows, key=lambda r: (r[0]["market"], r[1]["t"])):
        key = (run["market"], ev["e"])
        if key not in last_t or ev["t"] - last_t[key] >= h:
            nno += 1
            last_t[key] = ev["t"]
    ordered = sorted(xs)
    trimmed = ordered[:-1] if kind == "enter" else ordered[1:]
    recent = [v for run, ev, v in rows if ev["t"] >= run["recent_start"]]
    earlier = [v for run, ev, v in rows if ev["t"] < run["recent_start"]]
    half1 = [v for run, ev, v in rows if ev["t"] < run["midpoint"]]
    half2 = [v for run, ev, v in rows if ev["t"] >= run["midpoint"]]
    per_scope = {}
    for market in MARKETS:
        values = [v for run, _, v in rows if run["market"] == market]
        per_scope[market] = {"n": len(values), "mean": _r(_mean(values))}
    per_sector = {}
    for run, ev, v in rows:
        per_sector.setdefault(ev["e"], []).append(v)
    mean = sum(xs) / n
    out.update({
        "n_nonoverlap": nno,
        "mean": _r(mean),
        "median": _r(statistics.median(xs)),
        "hit_rate": _r(sum(1 for v in xs if v > 0) / n),
        "baseline_hit_rate": _r(base_hit),
        "false_signal_rate": _r(sum(1 for v in xs if (v < 0 if kind == "enter" else v > 0)) / n),
        "mean_without_extreme": _r(_mean(trimmed)),
        "recent_n": len(recent), "recent_mean": _r(_mean(recent)),
        "earlier_n": len(earlier), "earlier_mean": _r(_mean(earlier)),
        "half1_n": len(half1), "half1_mean": _r(_mean(half1)),
        "half2_n": len(half2), "half2_mean": _r(_mean(half2)),
        "mean_after_cost": _r(mean - COST) if kind == "enter" else None,
        "mean_after_stress_cost": _r(mean - STRESS * COST) if kind == "enter" else None,
        "per_scope": per_scope,
    })
    if h == MAIN:
        out["per_sector"] = {
            e: {"n": len(v), "mean": _r(_mean(v))} for e, v in sorted(per_sector.items())
        }
        out["_raw"] = {"mean": mean, "trimmed": _mean(trimmed), "recent": _mean(recent), "recent_n": len(recent), "per_sector": per_sector}
    return out


def gate(summary: dict) -> tuple[str, list]:
    n = summary["n"]
    raw = summary.get("_raw")
    if summary["kind"] == "enter":
        if n < ENTER_MIN_N or summary["n_nonoverlap"] < ENTER_MIN_NNO:
            return "INSUFFICIENT", []
        eligible = [v for v in raw["per_sector"].values() if len(v) >= 3]
        positive = sum(1 for v in eligible if _mean(v) > 0)
        scopes_positive = all((summary["per_scope"][m]["mean"] or 0) > 0 and summary["per_scope"][m]["n"] > 0 for m in MARKETS)
        checks = {
            "stress_cost": raw["mean"] - STRESS * COST > 0,
            "hit_rate": summary["hit_rate"] >= summary["baseline_hit_rate"] + 0.05,
            "without_best": raw["trimmed"] is not None and raw["trimmed"] > 0,
            "recent": raw["recent_n"] >= 1 and raw["recent"] >= 0,
            "repeatability": scopes_positive and positive > len(eligible) / 2,
        }
        failed = [k for k, ok in checks.items() if not ok]
        return ("PASS" if not failed else "FAIL"), failed
    if n < EXIT_MIN_N:
        return "INSUFFICIENT", []
    checks = {
        "negative_mean": raw["mean"] < 0,
        "without_worst": raw["trimmed"] is not None and raw["trimmed"] < 0,
        "recent": raw["recent_n"] >= 1 and raw["recent"] < 0,
    }
    failed = [k for k, ok in checks.items() if not ok]
    return ("JUSTIFIED" if not failed else "NOT_JUSTIFIED"), failed


def structure_verdict(gates: dict) -> str:
    enter, exit_ = gates.get("R1-k2_ENTER"), gates.get("R1-k2_EXIT")
    if enter == "PASS" and exit_ == "JUSTIFIED":
        return "SUPPORTED"
    if enter == "PASS":
        return "ENTRY_ONLY_SUPPORTED"
    if exit_ == "JUSTIFIED":
        return "RELEASE_ONLY_SUPPORTED"
    if "INSUFFICIENT" in (enter, exit_):
        return "INSUFFICIENT"
    return "NOT_SUPPORTED"


# ---------------------------------------------------------------- document

def _base_document(status: str, reason: str | None) -> dict:
    return {
        "schema_version": STUDY_SCHEMA,
        "study_id": "KR_ROTATION_20SESSION_EVENT_STUDY_V1",
        "status": status,
        "reason": reason,
        "preregistration": {
            "path": "config/kr_rotation_event_study_preregistration.json",
            "sha256": PREREGISTRATION_SHA256,
        },
        "inputs": {
            "ratified_policy_sha256": file_sha256(POLICY_PATH),
            "backfill_contract_sha256": file_sha256(BACKFILL_CONTRACT_PATH),
            "backfill_module_sha256": file_sha256(BACKFILL_MODULE_PATH),
            "study_module_sha256": file_sha256(Path(__file__)),
            "receipt_sha256": None,
            "manifest_sha256": None,
            "records_payload_sha256": None,
        },
        "backfill_summary": None,
        "coverage": None,
        "baseline_hit_rate": None,
        "candidates": [],
        "descriptive": None,
        "verdict_summary": None,
        "data_handling": {
            "public_contains_index_values": False,
            "public_contains_per_day_values": False,
            "private_records_uploaded": False,
            "terms_basis": "KRX_OPEN_API_TERMS_RESTRICT_THIRD_PARTY_PROVISION_AND_REDISTRIBUTION_RIGHTS_ARE_NOT_ESTABLISHED",
        },
        "authority": copy.deepcopy(AUTHORITY),
    }


def build_study(receipt_path: Path, records_dir: Path) -> dict:
    verify_preregistration()
    receipt_path = Path(receipt_path)
    if not receipt_path.exists():
        return _base_document("BLOCKED", "BACKFILL_RECEIPT_MISSING")
    receipt_raw = receipt_path.read_bytes()
    receipt = json.loads(receipt_raw.decode("utf-8"))
    if receipt.get("schema_version") != RECEIPT_SCHEMA:
        return _base_document("BLOCKED", "BACKFILL_RECEIPT_SCHEMA_INVALID")
    doc = _base_document("BLOCKED", None)
    doc["inputs"].update({
        "receipt_sha256": hashlib.sha256(receipt_raw).hexdigest(),
        "manifest_sha256": receipt.get("manifest_sha256"),
        "records_payload_sha256": receipt.get("records_payload_sha256"),
    })
    accounting = receipt.get("request_accounting", {})
    doc["backfill_summary"] = {
        "status": receipt.get("status"),
        "stop": receipt.get("stop"),
        "planned_requests": accounting.get("planned_requests"),
        "http_attempts": accounting.get("http_attempts"),
        "transient_retries": accounting.get("transient_retries"),
        "requests_blocked_after_retries": accounting.get("requests_blocked_after_retries"),
        "session_confirmed_count": receipt.get("sessions", {}).get("session_confirmed_count"),
        "non_session_empty_response_count": receipt.get("sessions", {}).get("non_session_empty_response_count"),
        "disagreement_count": receipt.get("sessions", {}).get("disagreement_count"),
        "calendar_years": receipt.get("calendar_years"),
    }
    if receipt.get("status") != "COMPLETE":
        doc["reason"] = f"BACKFILL_NOT_COMPLETE:{receipt.get('status')}"
        return doc
    try:
        records = load_records(receipt, records_dir)
    except StudyError as exc:
        doc["reason"] = str(exc)
        return doc
    scopes = ratified_scopes()
    sessions = confirmed_sessions(records)
    windows, coverage = {}, {}
    for market in MARKETS:
        windows[market] = scope_window(records, sessions, market, scopes[market])
        dates = windows[market]["dates"]
        coverage[market] = {
            "confirmed_session_count": len(sessions),
            "window_session_count": len(dates),
            "window_first": _iso(dates[0]) if dates else None,
            "window_last": _iso(dates[-1]) if dates else None,
            "qualifies": len(dates) >= MIN_WINDOW,
        }
    doc["coverage"] = coverage
    if not all(c["qualifies"] for c in coverage.values()):
        doc["status"] = "INSUFFICIENT_EXACT_IDENTITY_COVERAGE"
        doc["reason"] = f"WINDOW_BELOW_MINIMUM_{MIN_WINDOW}_SESSIONS"
        return doc
    runs = [run_scope(market, scopes[market], windows[market]) for market in MARKETS]
    base = {}
    for h in HORIZONS:
        hits = sum(run["baseline"][h][0] for run in runs)
        total = sum(run["baseline"][h][1] for run in runs)
        base[h] = hits / total if total else 0.0
    doc["baseline_hit_rate"] = {str(h): _r(v) for h, v in base.items()}
    gates = {}
    candidates = []
    for name in KINDS:
        for h in HORIZONS:
            summary = summarize(runs, name, h, base[h])
            if h == MAIN:
                if summary["n"] == 0:
                    verdict, failed = "INSUFFICIENT", []
                else:
                    verdict, failed = gate(summary)
                summary["gate"] = verdict
                summary["gate_failed_checks"] = failed
                gates[name] = verdict
            summary.pop("_raw", None)
            candidates.append(summary)
    doc["candidates"] = candidates
    rebalances = [(run["market"], rb) for run in runs for rb in run["rebalances"]]
    rb_values = [rb["excess"] for _, rb in rebalances]
    last6 = [rb["excess"] for run in runs for rb in run["rebalances"][-6:]]
    held = [x for run in runs for x in run["held_k2"]]
    doc["descriptive"] = {
        "r5_rebalance_20": {
            "n": len(rb_values),
            "mean": _r(_mean(rb_values)),
            "hit_share": _r(sum(1 for v in rb_values if v > 0) / len(rb_values)) if rb_values else None,
            "mean_without_best": _r(_mean(sorted(rb_values)[:-1])) if len(rb_values) > 1 else None,
            "last6_per_scope_n": len(last6),
            "last6_per_scope_mean": _r(_mean(last6)),
            "half1_mean": _r(_mean([rb["excess"] for run in runs for rb in run["rebalances"] if rb["t"] < run["midpoint"]])),
            "half2_mean": _r(_mean([rb["excess"] for run in runs for rb in run["rebalances"] if rb["t"] >= run["midpoint"]])),
            "gate": "DESCRIPTIVE_ONLY",
        },
        "whipsaw": {run["market"]: run["whipsaw"] for run in runs},
        "r1_k2_median_held_observations": _r(statistics.median(held)) if held else None,
        "r3_watch_to_r1_k2_conversion": {run["market"]: run["conversion"] for run in runs},
        "r2_events_dropped_missing_turnover": sum(run["r2_dropped_missing_turnover"] for run in runs),
        "top3_persistence": {run["market"]: run["persistence"] for run in runs},
    }
    gated = list(gates.values())
    doc["verdict_summary"] = {
        "gated_candidate_count": len(gated),
        "pass_count": gated.count("PASS"),
        "justified_count": gated.count("JUSTIFIED"),
        "insufficient_count": gated.count("INSUFFICIENT"),
        "gates": gates,
        "ratified_structure_verdict": structure_verdict(gates),
        "kr_rule_status_unchanged": "TEMPORARY; any change requires a new user ratification",
    }
    doc["status"] = "COMPLETE"
    doc["reason"] = None
    return doc


def forbid_checkout_output(path: Path) -> None:
    try:
        Path(path).resolve().relative_to(ROOT.resolve())
    except ValueError:
        return
    raise StudyError("OUTPUT_INSIDE_REPOSITORY_FORBIDDEN")


def validate_public_dir(directory: Path) -> list[str]:
    checked = []
    for path in sorted(Path(directory).rglob("*")):
        if path.is_dir():
            continue
        if path.suffix != ".json":
            raise StudyError(f"PUBLIC_DIR_NON_JSON_FILE:{path.name}")
        validate_public_artifact(json.loads(path.read_text(encoding="utf-8")))
        checked.append(path.name)
    if not checked:
        raise StudyError("PUBLIC_DIR_EMPTY")
    return checked


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify-preregistration", action="store_true")
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--records-dir", type=Path)
    parser.add_argument("--out", type=Path)
    parser.add_argument("--validate-public", type=Path)
    args = parser.parse_args(argv)
    if args.verify_preregistration:
        verify_preregistration()
        print(json.dumps({"preregistration_sha256": PREREGISTRATION_SHA256, "status": "VERIFIED"}))
        return 0
    if args.validate_public:
        forbid_checkout_output(args.validate_public)
        print(json.dumps({"validated": validate_public_dir(args.validate_public)}))
        return 0
    if not (args.receipt and args.records_dir and args.out):
        raise StudyError("RECEIPT_RECORDS_DIR_AND_OUT_REQUIRED")
    forbid_checkout_output(args.out)
    forbid_checkout_output(args.records_dir)
    doc = build_study(args.receipt, args.records_dir)
    validate_public_artifact(doc)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(canonical_bytes(doc))
    print(json.dumps({"status": doc["status"], "reason": doc["reason"], "verdict_summary": doc["verdict_summary"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
