#!/usr/bin/env python3
"""
Atlas — S-2 FRED No-Raw-Archive Research Collector (v3.0-noraw)

Purpose
-------
Fetch FRED/ALFRED vintage-aware inputs, validate them fail-closed, calculate Atlas
derived research artifacts, and publish only derived outputs + minimum provenance.

NO-RAW contract
---------------
- API response bytes are used in memory only.
- No raw response body, full API page, or observations batch JSON is persisted.
- No API key is written to files, argv, or logs.
- A failed run publishes no final run directory.
- Permanent outputs are Atlas-derived CSVs plus identifiers/metadata/provenance:
  metadata ranges, vintage-date identifiers, redacted request parameters,
  fetched_at/http status/response SHA256+bytes, and derived artifact sidecars.

Not authorized here: Regime score/threshold/weight, evaluator wiring, Production,
or trading behavior.
"""

import argparse
import csv
import decimal
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

decimal.getcontext().prec = 40

BASE = "https://api.stlouisfed.org/fred"
ROOT = os.environ.get("ATLAS_FRED_DERIVED_DIR", "atlas_derived/fred_s2")
TOOL_VERSION = "3.0-noraw"

DEFAULT_SLEEP = 1.0
MAX_RETRY = 5
BACKOFF_BASE = 4
TIMEOUT = 120

EXPECTED_UNIT_BOUNDARY = {
    "WRESBAL": {"normalization_boundary": "2025-11-13", "label_change": "2019-08-22"},
    "TOTBKCR": {"normalization_boundary": None, "label_change": None},
}

UNIT_TABLE = {
    "Billions of Dollars": ("Millions of U.S. Dollars", Decimal("1000")),
    "Billions of U.S. Dollars": ("Millions of U.S. Dollars", Decimal("1000")),
    "Millions of Dollars": ("Millions of U.S. Dollars", Decimal("1")),
    "Millions of U.S. Dollars": ("Millions of U.S. Dollars", Decimal("1")),
}

VIN_COL = re.compile(r"^(?P<sid>[A-Za-z0-9_]+)_(?P<d>\d{8})$")
RUN_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")


class Stop(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def api_key():
    k = os.environ.get("FRED_API_KEY", "").strip()
    if not k:
        raise Stop("FRED_API_KEY 환경변수가 없습니다. 키를 파일이나 인자로 넘기지 마십시오.")
    return k


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def digest_of(path):
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            n += len(chunk)
    return h.hexdigest(), n


def canon_sha12(obj):
    b = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return sha256_bytes(b)[:12]


def dec(v, ctx):
    if v in (None, "", "."):
        return None
    try:
        return Decimal(str(v))
    except decimal.InvalidOperation:
        raise Stop("수치 변환 실패: %r (%s)" % (v, ctx))


def dec_str(d):
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


def factor_for(raw_unit):
    if raw_unit is None:
        raise Stop("raw_unit 이 비어 있습니다.")
    base = raw_unit.split(",")[0].strip()
    if base not in UNIT_TABLE:
        raise Stop("알 수 없는 단위 문자열: %r — 임의 추정으로 진행하지 않습니다." % raw_unit)
    return UNIT_TABLE[base]


def meta_for(sid, vintage_date, ranges):
    hits = [r for r in ranges if r[0] <= vintage_date <= r[1]]
    if not hits:
        raise Stop("vintage %s 의 metadata 구간이 없습니다 — %s" % (vintage_date, sid))
    if len(hits) > 1:
        sig = {(h[2], h[3], h[4], h[5]) for h in hits}
        if len(sig) > 1:
            raise Stop("vintage %s 에 서로 다른 metadata 구간이 겹칩니다 — units/coverage/frequency 모호성" %
                       vintage_date)
    return hits[0]


def audit_boundary(sid, ranges):
    expected = EXPECTED_UNIT_BOUNDARY.get(sid, {}).get("normalization_boundary")
    observed, prev = [], None
    for rs, _re, units, _cs, _ce, _fq in ranges:
        _nu, f = factor_for(units)
        if prev is not None and f != prev:
            observed.append(rs)
        prev = f
    if expected is None:
        if observed:
            raise Stop("%s: 예상 전환 없음인데 factor 전환 관측 %s" % (sid, observed))
        return {"expected": None, "observed": []}
    if observed != [expected]:
        raise Stop("%s: normalization boundary 불일치. 예상=%s 실제=%s" %
                   (sid, [expected], observed))
    return {"expected": [expected], "observed": observed}


def _attest(raw, meta, extra=None):
    rec = dict(meta)
    rec["response_sha256"] = sha256_bytes(raw)
    rec["response_bytes"] = len(raw)
    if extra:
        rec.update(extra)
    return rec


RATE_EVENTS = []


def call(endpoint, params, series_id, sleep=DEFAULT_SLEEP):
    k = api_key()
    q = dict(params)
    q["api_key"] = k
    q.setdefault("file_type", "json")
    url = "%s/%s?%s" % (BASE, endpoint, urllib.parse.urlencode(q))
    safe = {kk: vv for kk, vv in q.items() if kk != "api_key"}

    for attempt in range(1, MAX_RETRY + 1):
        try:
            time.sleep(sleep)
            req = urllib.request.Request(url, headers={"User-Agent": "Atlas-S2-NoRaw/3.0"})
            with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
                raw = resp.read()
                return raw, {"endpoint": endpoint, "params": safe,
                             "http_status": resp.status, "fetched_at": utc_now(),
                             "attempt": attempt}
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504):
                wait = BACKOFF_BASE * (2 ** (attempt - 1))
                RATE_EVENTS.append({"at": utc_now(), "series_id": series_id,
                                    "endpoint": endpoint, "http_status": e.code,
                                    "attempt": attempt, "backoff_sec": wait})
                print("  [%d] %s — %ds 대기 후 재시도 (%d/%d)" %
                      (e.code, endpoint, wait, attempt, MAX_RETRY), file=sys.stderr)
                time.sleep(wait)
                continue
            raise Stop("HTTP %d on %s" % (e.code, endpoint))
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt < MAX_RETRY:
                time.sleep(BACKOFF_BASE * attempt)
                continue
            raise Stop("네트워크 실패 %s: %s" % (endpoint, e))
    raise Stop("재시도 소진: %s" % endpoint)


def sanitize_meta_rows(rows):
    out = []
    for r in rows:
        out.append({
            "realtime_start": r.get("realtime_start"),
            "realtime_end": r.get("realtime_end"),
            "units": r.get("units"),
            "observation_start": r.get("observation_start"),
            "observation_end": r.get("observation_end"),
            "frequency": r.get("frequency"),
        })
    return out


def meta_canon_sha(rows):
    return canon_sha12([[r.get("realtime_start"), r.get("realtime_end"), r.get("units"),
                         r.get("observation_start"), r.get("observation_end"),
                         r.get("frequency")] for r in rows])


def fetch_meta(sid, attestations):
    raw, meta = call("series", {"series_id": sid, "realtime_start": "1776-07-04",
                               "realtime_end": "9999-12-31"}, sid)
    attestations.append(_attest(raw, meta, {"kind": "metadata"}))
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise Stop("metadata JSON decode 실패: %s" % e)
    rows = sanitize_meta_rows(data.get("seriess", []))
    if not rows:
        raise Stop("메타데이터 응답이 비어 있습니다: %s" % sid)
    required = ("realtime_start", "realtime_end", "units", "observation_start",
                "observation_end", "frequency")
    for i, r in enumerate(rows):
        if any(r.get(k) in (None, "") for k in required):
            raise Stop("metadata 필수 필드 누락: row=%d" % i)
    sha = meta_canon_sha(rows)
    ranges = [(r["realtime_start"], r["realtime_end"], r["units"],
               r["observation_start"], r["observation_end"], r["frequency"]) for r in rows]
    audit_boundary(sid, ranges)
    return sha, rows, ranges


def fetch_vintages(sid, attestations):
    offset = 0
    page = 0
    all_dates = []
    reported_count = None
    while True:
        raw, meta = call("series/vintagedates",
                         {"series_id": sid, "limit": 10000, "offset": offset}, sid)
        try:
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            raise Stop("vintagedates JSON decode 실패: %s" % e)
        dates = data.get("vintage_dates", [])
        reported_count = data.get("count")
        attestations.append(_attest(raw, meta, {
            "kind": "vintage_inventory_page",
            "offset": offset,
            "page": page,
            "n_dates": len(dates),
            "api_reported_count": reported_count,
        }))
        all_dates.extend(dates)
        if not dates or (reported_count is not None and len(all_dates) >= reported_count):
            break
        offset += len(dates)
        page += 1
        if page > 500:
            raise Stop("페이지네이션이 끝나지 않습니다 — 중단")
    if not all_dates:
        raise Stop("vintage inventory 가 비어 있습니다.")
    if len(set(all_dates)) != len(all_dates):
        raise Stop("중복 vintage_date 가 존재합니다 — fail-closed")
    dates = sorted(all_dates)
    if reported_count is not None and len(dates) != reported_count:
        raise Stop("API count=%s 와 고유 vintage 개수=%d 불일치" % (reported_count, len(dates)))
    return canon_sha12(dates), dates, reported_count


def extract_returned_vintages(sid, data, where):
    obs = data.get("observations")
    if not obs:
        raise Stop("관측 레코드가 비어 있습니다 (%s)." % where)
    found = set()
    for row in obs:
        if "date" not in row:
            raise Stop("관측 레코드에 date 가 없습니다 (%s)." % where)
        for k in row:
            if k == "date":
                continue
            m = VIN_COL.match(k)
            if not m or m.group("sid") != sid:
                raise Stop("예상치 못한 응답 필드 %r (%s)." % (k, where))
            d = m.group("d")
            found.add("%s-%s-%s" % (d[:4], d[4:6], d[6:]))
    return found


def artifact_sidecar(path, meta):
    sha, size = digest_of(path)
    with open(path, newline="", encoding="utf-8") as f:
        rows = max(0, sum(1 for _ in csv.reader(f)) - 1)
    man = dict(meta)
    man.update({
        "artifact": os.path.basename(path),
        "artifact_sha256": sha,
        "bytes": size,
        "row_count": rows,
        "tool_version": TOOL_VERSION,
        "committed_at": utc_now(),
    })
    sc = path + ".manifest.json"
    with open(sc, "x", encoding="utf-8") as f:
        json.dump(man, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())
    return man


def json_dump_fsync(path, obj):
    with open(path, "x", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.flush()
        os.fsync(f.fileno())


def _safe_run_id(run_id):
    if not RUN_ID_RE.match(run_id):
        raise Stop("run_id 형식이 안전하지 않습니다: %r" % run_id)
    return run_id


def _ensure_no_raw_archive(stage):
    banned_dirs = {"observations", "raw"}
    banned_names = re.compile(r"(^batch_.*\.json$|series_realtime_.*\.json$|page_\d+\.json$)")
    for root, dirs, files in os.walk(stage):
        if banned_dirs.intersection(set(dirs)):
            raise Stop("NO-RAW invariant 위반: raw 디렉터리 생성 %s" % root)
        for fn in files:
            if banned_names.search(fn):
                raise Stop("NO-RAW invariant 위반: raw 파일 생성 %s" % os.path.join(root, fn))


def run_series(sid, batch, revision_obs, run_id):
    run_id = _safe_run_id(run_id)
    global RATE_EVENTS
    RATE_EVENTS = []

    final = os.path.join(ROOT, sid, "runs", run_id)
    if os.path.exists(final):
        raise Stop("최종 run 경로가 이미 존재합니다 — 덮어쓰지 않습니다: %s" % final)
    os.makedirs(os.path.dirname(final), exist_ok=True)
    stage_parent = os.path.join(ROOT, ".staging")
    os.makedirs(stage_parent, exist_ok=True)
    stage = os.path.join(stage_parent, "%s_%s_%d_%d" % (sid, run_id, os.getpid(), time.time_ns()))
    os.mkdir(stage)

    attestations = []
    requested_total = set()
    returned_total = set()
    normalized_rows = 0
    revision_rows = 0
    lag_rows = 0
    success = False

    try:
        meta_sha, meta_rows, ranges = fetch_meta(sid, attestations)
        inventory_sha, vintages, api_count = fetch_vintages(sid, attestations)
        known = set(vintages)

        # Validate metadata for every vintage before requesting the panel.
        meta_cache = {}
        for vd in vintages:
            rs, re_, units, cs, ce, fq = meta_for(sid, vd, ranges)
            nu, factor = factor_for(units)
            meta_cache[vd] = (units, nu, factor, cs, ce, fq)

        normalized_path = os.path.join(stage, "normalized_panel.csv")
        revision_path = os.path.join(stage, "revision_target.csv")
        lag_path = os.path.join(stage, "lag.csv")

        with open(normalized_path, "x", newline="", encoding="utf-8") as nf, \
             open(revision_path, "x", newline="", encoding="utf-8") as rf, \
             open(lag_path, "x", newline="", encoding="utf-8") as lf:
            nw = csv.writer(nf)
            rw = csv.writer(rf)
            lw = csv.writer(lf)
            nw.writerow(["series_id", "observation_date", "vintage_date",
                         "normalized_value", "normalized_unit", "normalization_factor",
                         "source_value_scale", "source_unit", "coverage_start",
                         "coverage_end", "frequency", "inventory_sha", "meta_sha",
                         "source_note"])
            rw.writerow(["series_id", "observation_date", "vintage_date",
                         "normalized_value", "delta_vs_prev_vintage",
                         "vintage_age_days", "normalized_unit", "inventory_sha", "meta_sha"])
            lw.writerow(["series_id", "vintage_date", "latest_observation_date",
                         "lag_days", "n_observations", "inventory_sha", "meta_sha",
                         "naming_note"])

            prev_revision = None
            for bi, start in enumerate(range(0, len(vintages), batch)):
                chunk = vintages[start:start + batch]
                requested_total.update(chunk)
                raw, meta = call("series/observations",
                                 {"series_id": sid, "output_type": 2,
                                  "vintage_dates": ",".join(chunk), "limit": 100000}, sid)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    raise Stop("observations JSON decode 실패 batch=%d: %s" % (bi, e))
                obs = data.get("observations", [])
                cnt = data.get("count")
                if cnt is not None and len(obs) < cnt:
                    raise Stop("응답 절단 감지 batch=%d (count=%s, 수신=%d)" % (bi, cnt, len(obs)))
                returned = extract_returned_vintages(sid, data, "batch_%05d" % bi)
                req = set(chunk)
                if returned != req:
                    raise Stop("batch vintage 불일치 — 요청=%d 응답=%d 누락=%s 초과=%s" %
                               (len(req), len(returned), sorted(req-returned)[:10],
                                sorted(returned-req)[:10]))
                returned_total.update(returned)
                attestations.append(_attest(raw, meta, {
                    "kind": "observation_batch",
                    "batch_index": bi,
                    "requested_vintages": list(chunk),
                    "returned_vintages": sorted(returned),
                    "n_obs_rows": len(obs),
                }))

                latest = {vd: None for vd in chunk}
                nobs = {vd: 0 for vd in chunk}
                target_values = {}

                for row in obs:
                    od = row["date"]
                    for k, rv in row.items():
                        if k == "date":
                            continue
                        m = VIN_COL.match(k)
                        if not m or m.group("sid") != sid:
                            raise Stop("예상치 못한 응답 필드 %r" % k)
                        dd = m.group("d")
                        vd = "%s-%s-%s" % (dd[:4], dd[4:6], dd[6:])
                        if vd not in req:
                            raise Stop("요청하지 않은 vintage 응답: %s" % vd)
                        units, nu, factor, cs, ce, fq = meta_cache[vd]
                        d = dec(rv, "vintage %s obs %s" % (vd, od))
                        nv = None if d is None else d * factor
                        scale = "" if d is None else str(max(0, -d.as_tuple().exponent))
                        nw.writerow([sid, od, vd, "" if nv is None else dec_str(nv), nu,
                                     dec_str(factor), scale, units, cs, ce, fq,
                                     inventory_sha, meta_sha,
                                     "FRED observations transformed in-memory; raw response not archived"])
                        normalized_rows += 1
                        if d is not None:
                            nobs[vd] += 1
                            if latest[vd] is None or od > latest[vd]:
                                latest[vd] = od
                        if od == revision_obs:
                            target_values[vd] = nv

                for vd in chunk:
                    lod = latest[vd]
                    lag = "" if lod is None else str(
                        (datetime.strptime(vd, "%Y-%m-%d") -
                         datetime.strptime(lod, "%Y-%m-%d")).days)
                    lw.writerow([sid, vd, lod or "", lag, nobs[vd], inventory_sha, meta_sha,
                                 "lag_days = vintage_date - latest_observation_date (NOT available_at)"])
                    lag_rows += 1

                    if vd in target_values:
                        cur = target_values[vd]
                        delta = "" if (cur is None or prev_revision is None) else dec_str(cur - prev_revision)
                        age = (datetime.strptime(vd, "%Y-%m-%d") -
                               datetime.strptime(revision_obs, "%Y-%m-%d")).days
                        units = meta_cache[vd][1]
                        rw.writerow([sid, revision_obs, vd,
                                     "" if cur is None else dec_str(cur),
                                     delta, age, units, inventory_sha, meta_sha])
                        revision_rows += 1
                        if cur is not None:
                            prev_revision = cur

            for f in (nf, rf, lf):
                f.flush()
                os.fsync(f.fileno())

        if requested_total != known:
            raise Stop("전수 요청 실패: inventory=%d requested=%d" % (len(known), len(requested_total)))
        if returned_total != known:
            raise Stop("전수 응답 실패: inventory=%d returned=%d" % (len(known), len(returned_total)))
        if revision_rows == 0:
            raise Stop("revision target 관측일 %s 이 패널에 없습니다." % revision_obs)

        # Persist identifiers/metadata/provenance only; never raw response bodies.
        metadata_obj = {
            "series_id": sid,
            "meta_sha": meta_sha,
            "metadata_ranges": meta_rows,
            "normalization_boundary_audit": audit_boundary(sid, ranges),
            "tool_version": TOOL_VERSION,
        }
        inventory_obj = {
            "series_id": sid,
            "inventory_sha": inventory_sha,
            "count": len(vintages),
            "api_reported_count": api_count,
            "vintage_dates": vintages,
            "tool_version": TOOL_VERSION,
        }
        json_dump_fsync(os.path.join(stage, "metadata_contract.json"), metadata_obj)
        json_dump_fsync(os.path.join(stage, "vintage_inventory.json"), inventory_obj)
        json_dump_fsync(os.path.join(stage, "source_attestations.json"), {
            "series_id": sid,
            "run_id": run_id,
            "attestations": attestations,
            "rate_events": list(RATE_EVENTS),
            "contract": "response body not archived; digest/bytes + redacted request provenance only",
        })

        common = {"series_id": sid, "run_id": run_id, "inventory_sha": inventory_sha,
                  "meta_sha": meta_sha, "revision_observation_date": revision_obs}
        nman = artifact_sidecar(normalized_path, dict(common, artifact_kind="normalized_panel"))
        rman = artifact_sidecar(revision_path, dict(common, artifact_kind="revision_target"))
        lman = artifact_sidecar(lag_path, dict(common, artifact_kind="lag"))

        run_manifest = {
            "series_id": sid,
            "run_id": run_id,
            "completed_at": utc_now(),
            "tool_version": TOOL_VERSION,
            "inventory_sha": inventory_sha,
            "meta_sha": meta_sha,
            "inventory_count": len(vintages),
            "requested_vintage_count": len(requested_total),
            "returned_vintage_count": len(returned_total),
            "normalized_row_count": normalized_rows,
            "revision_row_count": revision_rows,
            "lag_row_count": lag_rows,
            "revision_observation_date": revision_obs,
            "no_raw_archive": True,
            "raw_response_files_written": 0,
            "complete_gate": (requested_total == known == returned_total),
            "artifacts": {
                "normalized_panel": nman,
                "revision_target": rman,
                "lag": lman,
            },
        }
        json_dump_fsync(os.path.join(stage, "run_manifest.json"), run_manifest)

        _ensure_no_raw_archive(stage)

        # Directory rename is the single publish point. Same filesystem by construction.
        os.rename(stage, final)
        success = True
        print("[PASS] %s run_id=%s inventory=%d normalized_rows=%d revision_rows=%d lag_rows=%d" %
              (sid, run_id, len(vintages), normalized_rows, revision_rows, lag_rows))
        print("       meta_sha=%s inventory_sha=%s final=%s" % (meta_sha, inventory_sha, final))
        return 0
    finally:
        if not success and os.path.exists(stage):
            shutil.rmtree(stage, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Atlas S-2 FRED no-raw-archive collector")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("run", help="single-process no-raw S-2 research backfill")
    p.add_argument("series")
    p.add_argument("--batch", type=int, default=25)
    p.add_argument("--revision-obs", required=True, help="YYYY-MM-DD")
    p.add_argument("--run-id", default=None)
    args = ap.parse_args()

    if args.cmd == "run":
        if args.batch <= 0 or args.batch > 2000:
            raise Stop("--batch 범위는 1..2000 입니다.")
        try:
            datetime.strptime(args.revision_obs, "%Y-%m-%d")
        except ValueError:
            raise Stop("--revision-obs 형식은 YYYY-MM-DD 입니다.")
        run_id = args.run_id or (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") +
                                 "_%d" % os.getpid())
        return run_series(args.series, args.batch, args.revision_obs, run_id)
    raise Stop("unknown command")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Stop as e:
        print("STOP: %s" % e, file=sys.stderr)
        sys.exit(2)
