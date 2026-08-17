#!/usr/bin/env python3
import csv
import importlib.util
import json
import os
import shutil
import sys
import tempfile

SRC = sys.argv[1] if len(sys.argv) > 1 else "atlas_fred_s2_noraw.py"
spec = importlib.util.spec_from_file_location("m", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

RESULTS = []

def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s  %s%s" % ("PASS" if cond else "FAIL", name, (" — " + detail) if detail else ""))

META_ROWS = [
    {"realtime_start": "2004-09-02", "realtime_end": "2019-08-21",
     "units": "Billions of Dollars, Not Seasonally Adjusted",
     "observation_start": "1984-01-04", "observation_end": "2019-08-21", "frequency": "Weekly"},
    {"realtime_start": "2019-08-22", "realtime_end": "2025-11-12",
     "units": "Billions of U.S. Dollars, Not Seasonally Adjusted",
     "observation_start": "1984-01-04", "observation_end": "2025-11-12", "frequency": "Weekly"},
    {"realtime_start": "2025-11-13", "realtime_end": "9999-12-31",
     "units": "Millions of U.S. Dollars, Not Seasonally Adjusted",
     "observation_start": "2002-12-18", "observation_end": "2026-08-12", "frequency": "Weekly"},
]
VINTAGES = ["2008-10-09", "2025-11-06", "2025-11-13"]
VALUES = {
    "2008-09-10": {
        "2008-10-09": "7.969",
        "2025-11-06": "7.969",
        "2025-11-13": "9020",
    },
    "2008-09-17": {
        "2008-10-09": "46.998",
        "2025-11-06": "46.998",
        "2025-11-13": "46998",
    },
}

class FakeAPI:
    def __init__(self):
        self.observation_calls = 0
        self.mismatch = False
        self.unexpected = False
        self.truncated = False
        self.fail_second_batch = False
        self.empty_meta = False
        self.duplicate_vintage = False
        self.count_mismatch = False
        self.bad_boundary = False
        self.bad_unit = False

    def __call__(self, endpoint, params, sid, sleep=0):
        meta = {"endpoint": endpoint, "params": dict(params), "http_status": 200,
                "fetched_at": "2026-08-17T00:00:00Z", "attempt": 1}
        if endpoint == "series":
            rows = [] if self.empty_meta else [dict(r) for r in META_ROWS]
            if self.bad_boundary and rows:
                rows[2]["realtime_start"] = "2025-11-14"
            if self.bad_unit and rows:
                rows[0]["units"] = "Mystery Units"
            raw = json.dumps({"seriess": rows}).encode()
            return raw, meta
        if endpoint == "series/vintagedates":
            count = len(VINTAGES) + (1 if self.count_mismatch else 0)
            if self.count_mismatch and int(params.get("offset", 0)) > 0:
                dates = []
            else:
                dates = list(VINTAGES)
                if self.duplicate_vintage:
                    dates.append(VINTAGES[-1])
            raw = json.dumps({"vintage_dates": dates, "count": count}).encode()
            return raw, meta
        if endpoint == "series/observations":
            self.observation_calls += 1
            if self.fail_second_batch and self.observation_calls == 2:
                raise m.Stop("synthetic second batch failure")
            chunk = params["vintage_dates"].split(",")
            if self.mismatch and self.observation_calls == 1:
                chunk = chunk[:-1]
            obs = []
            for od in ("2008-09-10", "2008-09-17"):
                row = {"date": od}
                for vd in chunk:
                    k = "%s_%s" % (sid, vd.replace("-", ""))
                    row[k] = VALUES[od][vd]
                if self.unexpected and self.observation_calls == 1:
                    row["unexpected"] = "1"
                obs.append(row)
            count = len(obs) + (1 if self.truncated and self.observation_calls == 1 else 0)
            raw = json.dumps({"count": count, "observations": obs}).encode()
            return raw, meta
        raise AssertionError(endpoint)


def run_case(api=None, run_id="t1", batch=2, root=None):
    api = api or FakeAPI()
    root = root or tempfile.mkdtemp()
    oldroot, oldcall = m.ROOT, m.call
    m.ROOT, m.call = root, api
    try:
        rc = m.run_series("WRESBAL", batch, "2008-09-10", run_id)
        return rc, root, None
    except m.Stop as e:
        return None, root, str(e)
    finally:
        m.ROOT, m.call = oldroot, oldcall


print("="*72)
print("Atlas S-2 no-raw regression")
print("="*72)

# 1 key boundary
old = os.environ.pop("FRED_API_KEY", None)
try:
    try:
        m.api_key()
        key_stop = False
    except m.Stop:
        key_stop = True
    check("N1 key missing fail-closed", key_stop)
finally:
    if old is not None:
        os.environ["FRED_API_KEY"] = old

# 2 successful run
rc, root, err = run_case(run_id="success")
final = os.path.join(root, "WRESBAL", "runs", "success")
check("N2 full run succeeds", rc == 0 and os.path.isdir(final), err or "")
check("N3 staging cleaned after success", not os.path.exists(os.path.join(root, ".staging")) or
      not os.listdir(os.path.join(root, ".staging")))

files = sorted(os.listdir(final))
check("N4 no raw observations directory", "observations" not in files)
check("N5 no vintagedates/raw directory", not os.path.exists(os.path.join(final, "vintagedates", "raw")))
check("N6 no batch raw JSON", not any(x.startswith("batch_") and x.endswith(".json") for x in files))
check("N7 derived package contains provenance", all(x in files for x in
      ["metadata_contract.json", "vintage_inventory.json", "source_attestations.json", "run_manifest.json"]))

with open(os.path.join(final, "normalized_panel.csv"), newline="", encoding="utf-8") as f:
    rows = list(csv.reader(f))
hdr = rows[0]
body = rows[1:]
check("N8 normalized output excludes raw_value", "raw_value" not in hdr)
vals = [r[hdr.index("normalized_value")] for r in body]
check("N9 7.969*1000 -> 7969", "7969" in vals)
check("N10 46.998*1000 -> 46998", "46998" in vals)
check("N11 post-boundary 9020 retained as normalized", "9020" in vals)

with open(os.path.join(final, "revision_target.csv"), newline="", encoding="utf-8") as f:
    rr = list(csv.reader(f))
rhdr = rr[0]
rbody = rr[1:]
check("N12 revisions exclude raw_value", "raw_value" not in rhdr)
deltas = [r[rhdr.index("delta_vs_prev_vintage")] for r in rbody]
check("N13 cross-unit revision uses normalized delta 1051", "1051" in deltas, str(deltas))
check("N14 no artificial raw-unit jump", "9012.031" not in deltas)

with open(os.path.join(final, "lag.csv"), newline="", encoding="utf-8") as f:
    lr = list(csv.reader(f))
check("N15 lag naming excludes available_at", "available_at" not in lr[0])
check("N16 lag rows equal inventory", len(lr)-1 == len(VINTAGES))

att = json.load(open(os.path.join(final, "source_attestations.json"), encoding="utf-8"))
att_s = json.dumps(att, ensure_ascii=False)
check("N17 attestations keep response digest", all("response_sha256" in a and "response_bytes" in a
      for a in att["attestations"]))
check("N18 attestations do not contain response body field",
      "response_body" not in att_s and '"observations"' not in att_s and '"seriess"' not in att_s)
check("N19 attestations contain no api_key field", "api_key" not in att_s)

man = json.load(open(os.path.join(final, "run_manifest.json"), encoding="utf-8"))
check("N20 completeness gate true", man["complete_gate"] is True)
check("N21 raw response file count explicitly zero", man["raw_response_files_written"] == 0)

for kind, fn in [("normalized_panel","normalized_panel.csv"),
                 ("revision_target","revision_target.csv"),
                 ("lag","lag.csv")]:
    sc = json.load(open(os.path.join(final, fn+".manifest.json"), encoding="utf-8"))
    sha, size = m.digest_of(os.path.join(final, fn))
    check("N22 sidecar integrity %s" % kind, sc["artifact_sha256"] == sha and sc["bytes"] == size)

# failure cases publish nothing
for num, attr, needle in [
    (23, "empty_meta", "비어"),
    (24, "duplicate_vintage", "중복"),
    (25, "count_mismatch", "불일치"),
    (26, "bad_boundary", "boundary"),
    (27, "bad_unit", "알 수 없는 단위"),
    (28, "mismatch", "batch vintage 불일치"),
    (29, "unexpected", "예상치 못한 응답 필드"),
    (30, "truncated", "응답 절단"),
]:
    api = FakeAPI(); setattr(api, attr, True)
    rc2, root2, err2 = run_case(api=api, run_id="f%d"%num)
    f2 = os.path.join(root2, "WRESBAL", "runs", "f%d"%num)
    check("N%d %s fail-closed" % (num, attr), rc2 is None and not os.path.exists(f2) and needle in (err2 or ""),
          err2 or "")
    shutil.rmtree(root2, ignore_errors=True)

# failure after one successful batch must still publish nothing
api = FakeAPI(); api.fail_second_batch = True
rc2, root2, err2 = run_case(api=api, run_id="midfail", batch=1)
check("N31 mid-run failure publishes no final",
      rc2 is None and not os.path.exists(os.path.join(root2, "WRESBAL", "runs", "midfail")))
st = os.path.join(root2, ".staging")
check("N32 mid-run failure cleans staging", not os.path.exists(st) or not os.listdir(st))
shutil.rmtree(root2, ignore_errors=True)

# overwrite refusal
api = FakeAPI()
rc3, root3, err3 = run_case(api=api, run_id="same")
rc4, _, err4 = run_case(api=FakeAPI(), run_id="same", root=root3)
check("N33 existing final is never overwritten", rc3 == 0 and rc4 is None and "이미 존재" in (err4 or ""))
shutil.rmtree(root3, ignore_errors=True)

# distinct run ids coexist
root4 = tempfile.mkdtemp()
r1, _, e1 = run_case(api=FakeAPI(), run_id="r1", root=root4)
r2, _, e2 = run_case(api=FakeAPI(), run_id="r2", root=root4)
check("N34 distinct run ids coexist", r1 == 0 and r2 == 0 and
      os.path.isdir(os.path.join(root4, "WRESBAL", "runs", "r1")) and
      os.path.isdir(os.path.join(root4, "WRESBAL", "runs", "r2")))
shutil.rmtree(root4, ignore_errors=True)
shutil.rmtree(root, ignore_errors=True)


class FakeTOT:
    def __call__(self, endpoint, params, sid, sleep=0):
        meta = {"endpoint": endpoint, "params": dict(params), "http_status": 200,
                "fetched_at": "2026-08-17T00:00:00Z", "attempt": 1}
        if endpoint == "series":
            rows = [{"realtime_start":"2000-01-01","realtime_end":"9999-12-31",
                     "units":"Millions of U.S. Dollars, Not Seasonally Adjusted",
                     "observation_start":"1984-01-04","observation_end":"2026-08-12","frequency":"Weekly"}]
            return json.dumps({"seriess":rows}).encode(), meta
        if endpoint == "series/vintagedates":
            ds = ["2008-10-10","2020-01-10","2026-01-10"]
            return json.dumps({"vintage_dates":ds,"count":3}).encode(), meta
        if endpoint == "series/observations":
            chunk=params["vintage_dates"].split(",")
            obs=[]
            for od, base in [("2008-10-01","9864.4"),("2008-10-08","9870.0")]:
                row={"date":od}
                for vd in chunk:
                    row["%s_%s"%(sid,vd.replace("-",""))]=base
                obs.append(row)
            return json.dumps({"count":2,"observations":obs}).encode(), meta
        raise AssertionError(endpoint)

root5=tempfile.mkdtemp()
oldroot, oldcall=m.ROOT,m.call
m.ROOT=root5; m.call=FakeTOT()
try:
    rc5=m.run_series("TOTBKCR",2,"2008-10-01","tot")
    final5=os.path.join(root5,"TOTBKCR","runs","tot")
    check("N35 TOTBKCR no-transition full run succeeds", rc5==0 and os.path.isdir(final5))
finally:
    m.ROOT,m.call=oldroot,oldcall
    shutil.rmtree(root5,ignore_errors=True)

passed = sum(1 for _, ok, _ in RESULTS if ok)
print("="*72)
print("%d/%d 통과" % (passed, len(RESULTS)))
if passed != len(RESULTS):
    for name, ok, detail in RESULTS:
        if not ok:
            print("FAIL:", name, detail)
    sys.exit(1)
