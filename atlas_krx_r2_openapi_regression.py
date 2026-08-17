#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path

SRC = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "atlas_krx_r2_openapi_probe.py"
)

spec = importlib.util.spec_from_file_location("m", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

RESULTS = []
TOKEN = "SECRET-KRX-TOKEN-NEVER-PRINT"
RAW_SENTINEL = "RAW_INDEX_VALUE_SENTINEL_314159"


class FakeResponse:
    def __init__(self, body, status=200):
        self._body = body
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return self._body

    def getcode(self):
        return self.status


class FakeOpener:
    def __init__(self, payload=None, status=200, raw_body=None):
        self.payload = payload
        self.status = status
        self.raw_body = raw_body
        self.request = None

    def __call__(self, request, timeout=30):
        self.request = request
        if self.raw_body is not None:
            body = self.raw_body
        else:
            body = json.dumps(self.payload).encode("utf-8")
        return FakeResponse(body, self.status)


def good_payload(day="20100104"):
    return {
        "OutBlock_1": [
            {
                "BAS_DD": day,
                "IDX_CLSS": "fixture-class",
                "IDX_NM": "fixture-index",
                "CLSPRC_IDX": RAW_SENTINEL,
                "CMPPREVDD_IDX": "fixture",
                "FLUC_RT": "fixture",
                "OPNPRC_IDX": "fixture",
                "HGPRC_IDX": "fixture",
                "LWPRC_IDX": "fixture",
                "ACC_TRDVOL": "fixture",
                "ACC_TRDVAL": "fixture",
                "MKTCAP": "fixture",
            }
        ]
    }


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(
        "%s  %s%s"
        % (
            "PASS" if cond else "FAIL",
            name,
            (" — " + detail) if detail else "",
        )
    )


def expect_stop(fn):
    try:
        fn()
        return False
    except m.Stop:
        return True


print("=" * 72)
print("Atlas R2-ENG-001 KRX OPEN API probe regression")
print("=" * 72)

check(
    "K1 missing key fails closed",
    expect_stop(lambda: m.build_request("", "20100104")),
)

opener = FakeOpener(good_payload())
result = m.probe_date(TOKEN, "20100104", opener=opener)

check(
    "K2 fixture capability succeeds",
    result["status"] == "PASS"
    and result["row_count"] == 1
    and result["schema"] == "PASS",
)

contract = m.inspect_request_contract(TOKEN, "20100104")

check(
    "K3 AUTH_KEY is header-only, never URL",
    contract["auth_header_present"] is True
    and contract["auth_in_url"] is False
    and contract["query_keys"] == ["basDd"],
)

summary = m.format_summary(result)

check(
    "K4 summary contains no API token",
    TOKEN not in summary,
)

check(
    "K5 raw market sentinel is not returned or printed",
    RAW_SENTINEL not in repr(result)
    and RAW_SENTINEL not in summary,
)

check(
    "K6 invalid date fails closed",
    expect_stop(lambda: m.build_request(TOKEN, "2010-01-04")),
)

check(
    "K7 non-200 fails closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener(good_payload(), status=401),
        )
    ),
)

check(
    "K8 malformed JSON fails closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener(raw_body=b"{not-json"),
        )
    ),
)

check(
    "K9 missing OutBlock_1 fails closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener({"other": []}),
        )
    ),
)

check(
    "K10 empty rows fail closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener({"OutBlock_1": []}),
        )
    ),
)

missing = good_payload()
missing["OutBlock_1"][0].pop("CLSPRC_IDX")

check(
    "K11 missing required field fails closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener(missing),
        )
    ),
)

check(
    "K12 date mismatch fails closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener(good_payload("20100105")),
        )
    ),
)

with tempfile.TemporaryDirectory() as td:
    before = sorted(os.listdir(td))
    old = os.getcwd()
    os.chdir(td)
    try:
        m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener(good_payload()),
        )
    finally:
        os.chdir(old)
    after = sorted(os.listdir(td))

check(
    "K13 successful probe writes no files",
    before == after == [],
    "count=%d" % len(after),
)

check(
    "K14 endpoint contract is HTTPS KRX index path",
    contract["scheme"] == "https"
    and contract["host"] == "data-dbg.krx.co.kr"
    and contract["path"] == "/svc/apis/idx/kospi_dd_trd",
)

check(
    "K15 result preserves Research/Shadow-only scope",
    result["source_capability_proof_only"] is True
    and result["production_authorized"] is False
    and result["redistribution_authorized"] is False
    and result["regime_score_authorized"] is False
    and result["trading_authorized"] is False,
)

check(
    "K16 raw persistence is explicitly zero",
    result["raw_persistence"] == 0,
)

passed = sum(1 for _, ok, _ in RESULTS if ok)
print("=" * 72)
print("%d/%d 통과" % (passed, len(RESULTS)))

if passed != len(RESULTS):
    raise SystemExit(1)
