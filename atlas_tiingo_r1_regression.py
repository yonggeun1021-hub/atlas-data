#!/usr/bin/env python3
import importlib.util
import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

SRC = (
    sys.argv[1]
    if len(sys.argv) > 1
    else "atlas_tiingo_r1_transient.py"
)

spec = importlib.util.spec_from_file_location("m", SRC)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

RESULTS = []


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


TOKEN = "SECRET_TOKEN_SENTINEL"
RAW_SENTINELS = (
    "127.989998",
    "92.0059521943",
    "987654321",
)


class FakeCall:
    def __init__(self, mode="ok"):
        self.mode = mode

    def __call__(self, path, params, key):
        assert key == TOKEN

        if path == "/api/test/":
            if self.mode == "bad_auth":
                return json.dumps(
                    {"message": "nope"}
                ).encode(), 200

            return json.dumps(
                {
                    "message":
                    "You successfully sent a request"
                }
            ).encode(), 200

        if path.endswith("/prices"):
            if self.mode == "http500":
                return b"{}", 500

            if self.mode == "malformed":
                return b"{not-json", 200

            if self.mode == "empty":
                return b"[]", 200

            row = {
                "date":
                    "2008-09-02T00:00:00.000Z",
                "open": 123.0,
                "high": 129.0,
                "low": 120.0,
                "close": 127.989998,
                "volume": 987654321,
                "adjOpen": 88.0,
                "adjHigh": 93.0,
                "adjLow": 86.0,
                "adjClose": 92.0059521943,
                "adjVolume": 111111111,
                "divCash": 0.0,
                "splitFactor": 1.0,
            }

            if self.mode == "missing":
                row.pop("splitFactor")

            return json.dumps([row]).encode(), 200

        if path.startswith("/tiingo/daily/"):
            start = (
                "2009-01-01"
                if self.mode == "late_coverage"
                else "1993-01-29"
            )

            return json.dumps(
                {
                    "startDate": start,
                    "endDate": "2026-08-14",
                }
            ).encode(), 200

        raise AssertionError(path)


def expect_stop(mode, tickers=("SPY", "QQQ")):
    try:
        m.run_probe(
            TOKEN,
            tickers=tickers,
            call_fn=FakeCall(mode),
        )
        return False
    except m.Stop:
        return True


print("=" * 72)
print("Atlas R-1 Tiingo transient contract regression")
print("=" * 72)

old = os.environ.pop("TIINGO_API_KEY", None)

try:
    try:
        m.api_key()
        stopped = False
    except m.Stop:
        stopped = True

    check(
        "T1 missing key fails closed",
        stopped,
    )
finally:
    if old is not None:
        os.environ["TIINGO_API_KEY"] = old


result = m.run_probe(
    TOKEN,
    call_fn=FakeCall(),
)

check(
    "T2 fixture source capability succeeds",
    result["probe_pass"] is True,
)

summary = m.format_summary(result)

check(
    "T3 summary contains no API token",
    TOKEN not in summary,
)

check(
    "T4 summary contains no raw price sentinels",
    all(x not in summary for x in RAW_SENTINELS),
)

check(
    "T5 sanitized result contains no raw price sentinels",
    all(
        x not in json.dumps(result)
        for x in RAW_SENTINELS
    ),
)

check(
    "T6 missing required field fails closed",
    expect_stop("missing"),
)

check(
    "T7 empty historical window fails closed",
    expect_stop("empty"),
)

check(
    "T8 late source coverage fails closed",
    expect_stop("late_coverage"),
)

check(
    "T9 malformed JSON fails closed",
    expect_stop("malformed"),
)

check(
    "T10 non-200 source response fails closed",
    expect_stop("http500"),
)

check(
    "T11 bad auth response fails closed",
    expect_stop("bad_auth"),
)

try:
    m.validate_inputs(
        ("SPY/../../x",),
        m.DEFAULT_START,
        m.DEFAULT_END,
    )
    ticker_stop = False
except m.Stop:
    ticker_stop = True

check(
    "T12 unsafe ticker path is rejected",
    ticker_stop,
)

with tempfile.TemporaryDirectory() as td:
    before = sorted(Path(td).iterdir())

    old_cwd = os.getcwd()
    os.chdir(td)

    try:
        m.run_probe(
            TOKEN,
            call_fn=FakeCall(),
        )
        after = sorted(Path(td).iterdir())
    finally:
        os.chdir(old_cwd)

    check(
        "T13 successful probe writes no files",
        before == after,
        "count=%d" % len(after),
    )


captured = {}


class FakeResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return b"{}"


real_urlopen = urllib.request.urlopen


def fake_urlopen(req, timeout=None):
    captured["url"] = req.full_url
    captured["headers"] = dict(
        req.header_items()
    )
    return FakeResponse()


urllib.request.urlopen = fake_urlopen

try:
    m.call(
        "/api/test/",
        None,
        TOKEN,
    )
finally:
    urllib.request.urlopen = real_urlopen


headers_lower = {
    k.lower(): v
    for k, v in captured["headers"].items()
}

check(
    "T14 token is header-only, never URL",
    TOKEN not in captured["url"]
    and headers_lower.get("authorization")
    == "Token " + TOKEN,
)

check(
    "T15 contract output is explicitly non-authoritative",
    result["historical_adjusted_pit_qualified"] is False
    and result["persistent_tiingo_data_written"] == 0
    and result["regime_score_authorized"] is False
    and "historical_adjusted_pit=NOT_QUALIFIED"
    in summary
    and "regime_score_authorized=NO"
    in summary,
)

passed = sum(
    1
    for _, ok, _ in RESULTS
    if ok
)

print("=" * 72)
print(
    "%d/%d 통과"
    % (
        passed,
        len(RESULTS),
    )
)

if passed != len(RESULTS):
    raise SystemExit(1)
