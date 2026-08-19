#!/usr/bin/env python3
import importlib.util
import io
import json
import os
import sys
import tempfile
from contextlib import redirect_stdout
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


def good_row(day="20100104", close=RAW_SENTINEL):
    return {
        "BAS_DD": day,
        "IDX_CLSS": "fixture-class",
        "IDX_NM": "fixture-index",
        "CLSPRC_IDX": close,
        "CMPPREVDD_IDX": "fixture",
        "FLUC_RT": "fixture",
        "OPNPRC_IDX": "fixture",
        "HGPRC_IDX": "fixture",
        "LWPRC_IDX": "fixture",
        "ACC_TRDVOL": "fixture",
        "ACC_TRDVAL": "fixture",
        "MKTCAP": "fixture",
    }


def good_payload(day="20100104"):
    return {
        "OutBlock_1": [good_row(day)],
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
    and result["usable_row_count"] == 1
    and result["unavailable_close_count"] == 0
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
    "K14 default endpoint contract is HTTPS KOSPI index path",
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

mixed = {
    "OutBlock_1": [
        good_row(close=RAW_SENTINEL),
        good_row(close=""),
    ]
}
mixed_result = m.probe_date(
    TOKEN,
    "20100104",
    opener=FakeOpener(mixed),
)

check(
    "K17 mixed available/unavailable closes preserve usable observations",
    mixed_result["row_count"] == 2
    and mixed_result["usable_row_count"] == 1
    and mixed_result["unavailable_close_count"] == 1,
)

all_unavailable = {
    "OutBlock_1": [
        good_row(close=""),
        good_row(close=" "),
        good_row(close=None),
    ]
}
check(
    "K18 all unavailable closes fail closed",
    expect_stop(
        lambda: m.probe_date(
            TOKEN,
            "20100104",
            opener=FakeOpener(all_unavailable),
        )
    ),
)

expected_paths = {
    "krx": "/svc/apis/idx/krx_dd_trd",
    "kospi": "/svc/apis/idx/kospi_dd_trd",
    "kosdaq": "/svc/apis/idx/kosdaq_dd_trd",
}
contracts = {
    market: m.inspect_request_contract(TOKEN, "20100104", market=market)
    for market in expected_paths
}
check(
    "K19 KRX/KOSPI/KOSDAQ endpoint paths are explicit",
    all(contracts[market]["path"] == path for market, path in expected_paths.items()),
)

check(
    "K20 all market contracts keep AUTH_KEY header-only",
    all(
        contract["auth_header_present"] is True
        and contract["auth_in_url"] is False
        and contract["query_keys"] == ["basDd"]
        for contract in contracts.values()
    ),
)

check(
    "K21 unsupported market fails closed",
    expect_stop(lambda: m.build_request(TOKEN, "20100104", market="unknown")),
)

market_results = {
    market: m.probe_date(
        TOKEN,
        "20100104",
        opener=FakeOpener(good_payload()),
        market=market,
    )
    for market in expected_paths
}
check(
    "K22 result identifies each approved index service without raw values",
    all(
        result["market"] == market.upper()
        and result["source"] == "KRX_OPEN_API_%s_INDEX" % market.upper()
        and RAW_SENTINEL not in repr(result)
        for market, result in market_results.items()
    ),
)

calls = []
original_probe_date = m.probe_date
original_token = os.environ.get("ATLAS_TEST_KRX_KEY")


def fake_probe_date(auth_key, bas_dd, opener=m.urlopen, market=m.DEFAULT_MARKET):
    calls.append((auth_key, bas_dd, market))
    return {
        "status": "PASS",
        "date": bas_dd,
        "market": market.upper(),
        "row_count": 1,
        "usable_row_count": 1,
        "unavailable_close_count": 0,
        "schema": "PASS",
        "source": "KRX_OPEN_API_%s_INDEX" % market.upper(),
        "source_capability_proof_only": True,
        "raw_persistence": 0,
        "production_authorized": False,
        "redistribution_authorized": False,
        "regime_score_authorized": False,
        "trading_authorized": False,
    }


try:
    os.environ["ATLAS_TEST_KRX_KEY"] = TOKEN
    m.probe_date = fake_probe_date
    with redirect_stdout(io.StringIO()):
        cli_status = m.main([
            "--auth-env", "ATLAS_TEST_KRX_KEY",
            "--market", "krx",
            "--market", "kospi",
            "--market", "kosdaq",
            "--date", "20100104",
            "--date", "20260818",
        ])
finally:
    m.probe_date = original_probe_date
    if original_token is None:
        os.environ.pop("ATLAS_TEST_KRX_KEY", None)
    else:
        os.environ["ATLAS_TEST_KRX_KEY"] = original_token

check(
    "K23 CLI executes the full three-market/two-date proof matrix",
    cli_status == 0
    and calls == [
        (TOKEN, day, market)
        for market in ("krx", "kospi", "kosdaq")
        for day in ("20100104", "20260818")
    ],
)

passed = sum(1 for _, ok, _ in RESULTS if ok)
print("=" * 72)
print("%d/%d 통과" % (passed, len(RESULTS)))

if passed != len(RESULTS):
    raise SystemExit(1)
