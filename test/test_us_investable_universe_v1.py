"""Tests for universe/us_investable_universe_v1.py (US-DATA-1 item 1 / US-U1).

Covers the filter pipeline in isolation (no network, no real packet
required) plus a reconciliation check and the authority/caveat contract.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from universe.us_investable_universe_v1 import (  # noqa: E402
    AUTHORITY,
    EXCLUSION_CIK_NOT_FOUND,
    EXCLUSION_ETF,
    EXCLUSION_FINANCIAL_STATUS,
    EXCLUSION_SECURITY_TYPE,
    EXCLUSION_TEST_ISSUE,
    SECURITY_TYPE_ADS,
    SECURITY_TYPE_COMMON,
    SECURITY_TYPE_COMMON_BARE,
    SECURITY_TYPE_ORDINARY,
    USInvestableUniverseError,
    build_investable_universe,
    cik_by_ticker_from_snapshot,
    classify_security_name,
    lookup_cik,
)


def _row(symbol, name, source_name="nasdaq_listed", etf="N", test_issue="N", financial_status="N"):
    fields = {
        "Symbol": symbol,
        "Security Name": name,
        "ETF": etf,
        "Test Issue": test_issue,
    }
    if financial_status is not None:
        fields["Financial Status"] = financial_status
    return {
        "asset_id": f"US:TEST:{symbol}",
        "primary_symbol": symbol,
        "source_name": source_name,
        "fields": fields,
    }


CIK_SNAPSHOT = {
    "fields": ["cik", "name", "ticker", "exchange"],
    "data": [
        [320193, "Apple Inc.", "AAPL", "Nasdaq"],
        [1067983, "Berkshire Hathaway", "BRK-B", "NYSE"],
        [1, "Foo Preferred Co", "FOO$A", "NYSE"],
    ],
}


# ---------------------------------------------------------------- classify_security_name
def test_classify_ads():
    assert classify_security_name("BRBI BR Partners S.A. - ADSs") == SECURITY_TYPE_ADS
    assert classify_security_name("KANZHUN LIMITED - American Depository Shares") == SECURITY_TYPE_ADS


def test_classify_ordinary():
    assert classify_security_name("Bit Digital, Inc. - Ordinary Share") == SECURITY_TYPE_ORDINARY


def test_classify_common():
    assert classify_security_name("Agilent Technologies, Inc. Common Stock") == SECURITY_TYPE_COMMON
    assert classify_security_name("Capital Clean Energy Carriers Corp. - Common Share") == SECURITY_TYPE_COMMON


def test_classify_common_bare_for_plain_name():
    assert classify_security_name("AMETEK, Inc.") == SECURITY_TYPE_COMMON_BARE


def test_classify_excludes_non_common():
    for name in [
        "Armada Acquisition Corp. III - Units",
        "Armada Acquisition Corp. III - Warrant",
        "Apogee Acquisition Corp - Rights",
        "Bank of America Corporation Non Cumulative Perpetual Conv Pfd Ser L",
        "Adams Diversified Equity Fund Inc.",
        "BlackRock Health Sciences Trust",
        "ETRACS Alerian MLP Index ETN Series B due July 18, 2042",
    ]:
        assert classify_security_name(name) not in (
            SECURITY_TYPE_ADS, SECURITY_TYPE_ORDINARY, SECURITY_TYPE_COMMON, SECURITY_TYPE_COMMON_BARE,
        ), name


# ---------------------------------------------------------------- cik lookup
def test_cik_lookup_exact_and_normalization():
    by_ticker = cik_by_ticker_from_snapshot(CIK_SNAPSHOT)
    assert lookup_cik("AAPL", by_ticker) == ("320193", "exact")
    assert lookup_cik("BRK.B", by_ticker) == ("1067983", "dot_to_dash")
    assert lookup_cik("FOO$A", by_ticker) == ("1", "exact")
    assert lookup_cik("NOPE", by_ticker) == (None, None)


def test_cik_snapshot_field_order_enforced():
    bad = {"fields": ["ticker", "cik"], "data": []}
    try:
        cik_by_ticker_from_snapshot(bad)
        assert False, "expected ValueError"
    except ValueError:
        pass


# ---------------------------------------------------------------- pipeline
def _build(rows):
    packet = {"source_attribute_rows": rows}
    return build_investable_universe(
        packet, CIK_SNAPSHOT,
        generated_at_utc="2026-09-13T00:00:00Z",
        source_packet_ref={"date": "2026-09-13", "path": "x", "payload_sha256": "y"},
        cik_snapshot_ref={"path": "z", "sha256": "w", "row_count": 3},
    )


def test_pipeline_excludes_etf():
    result = _build([_row("AAPL", "Apple Inc. Common Stock", etf="Y")])
    assert result["exclusion_counts"][EXCLUSION_ETF] == 1
    assert result["kept_count"] == 0


def test_pipeline_excludes_test_issue():
    result = _build([_row("AAPL", "Apple Inc. Common Stock", test_issue="Y")])
    assert result["exclusion_counts"][EXCLUSION_TEST_ISSUE] == 1


def test_pipeline_excludes_abnormal_financial_status():
    result = _build([_row("AAPL", "Apple Inc. Common Stock", financial_status="D")])
    assert result["exclusion_counts"][EXCLUSION_FINANCIAL_STATUS] == 1


def test_pipeline_missing_financial_status_is_not_excluded_by_that_reason():
    # otherlisted.txt rows have no Financial Status field at all.
    row = _row("AAPL", "Apple Inc. Common Stock", source_name="other_listed", financial_status=None)
    result = _build([row])
    assert result["exclusion_counts"][EXCLUSION_FINANCIAL_STATUS] == 0
    assert result["kept_count"] == 1
    assert result["kept_rows"][0]["financial_status_available"] is False


def test_pipeline_excludes_non_common_security_type():
    result = _build([_row("AAPL", "Apple Inc. Preferred Stock Series A")])
    assert result["exclusion_counts"][EXCLUSION_SECURITY_TYPE] == 1


def test_pipeline_excludes_missing_cik():
    result = _build([_row("ZZZZNOPE", "Zzzz Nope Inc. Common Stock")])
    assert result["exclusion_counts"][EXCLUSION_CIK_NOT_FOUND] == 1


def test_pipeline_keeps_and_reconciles():
    rows = [
        _row("AAPL", "Apple Inc. Common Stock"),
        _row("BRK.B", "Berkshire Hathaway Inc. Common Stock"),
        _row("AAPL", "Apple Inc.", etf="Y"),  # excluded
    ]
    result = _build(rows)
    assert result["kept_count"] == 2
    assert sum(result["exclusion_counts"].values()) == 1
    assert result["total_source_rows"] == 3
    # reconciliation: kept + excluded == total, enforced inside build_investable_universe
    assert result["kept_count"] + sum(result["exclusion_counts"].values()) == result["total_source_rows"]


def test_pipeline_rejects_missing_rows():
    try:
        build_investable_universe(
            {}, CIK_SNAPSHOT,
            generated_at_utc="2026-09-13T00:00:00Z",
            source_packet_ref={}, cik_snapshot_ref={},
        )
        assert False, "expected USInvestableUniverseError"
    except USInvestableUniverseError:
        pass


def test_authority_all_display_only():
    result = _build([_row("AAPL", "Apple Inc. Common Stock")])
    assert result["authority"] == AUTHORITY
    assert result["authority"]["t1_display_only"] is True
    assert result["authority"]["ratified"] is False
    assert result["authority"]["trading_or_order_authority"] is False


def test_determinism_same_input_same_output():
    rows = [_row("AAPL", "Apple Inc. Common Stock")]
    r1 = _build(rows)
    r2 = _build(rows)
    r1.pop("generated_at_utc")
    r2.pop("generated_at_utc")
    assert r1 == r2
