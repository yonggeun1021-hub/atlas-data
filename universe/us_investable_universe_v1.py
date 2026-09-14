"""US-U1 — investable universe *display* generator (T1 only, unratified).

CIO 배정 US-DATA-1, item 1. `universe/us_global_universe.py` (P3-02) already
publishes every Nasdaq Trader directory row with `investable_eligible = false`
and no filtering — that adapter deliberately keeps all facts and decides
nothing (see `docs/us_global_universe_contract.md`). This module is the next,
separate, explicitly-unratified step: a deterministic filter over an already
published `us_global_universe_packet/1` that estimates which rows *might* be
a plain common stock or ADS with a resolvable SEC CIK, for T1 discovery
*display* only.

This module grants no authority. It does not resolve identity (that is W3,
`identity/bulk_identity_authority.py`, separately scoped and RATIFIED-gated),
does not set `investable_eligible`, and does not feed T2/T3. Every output row
carries `t1_display_only = true` and `ratified = false`.

Filter pipeline (deterministic, order matters, every step's exclusion count
is recorded so nothing is silently dropped):

  1. ETF != 'Y'                              (official ETF flag)
  2. Test Issue != 'Y'                       (official test-issue flag)
  3. Financial Status == 'N' where the source provides that field at all
     (only `nasdaqlisted.txt` carries it; `otherlisted.txt` rows have no
     such flag in this source and are not excluded by its absence — that
     absence is recorded, never treated as an assumed "normal")
  4. Security Name pattern says common stock / ADS / ordinary shares
     (`classify_security_name`) — an explicit heuristic, not a ratified
     security-type determination. A name with no recognized suffix at all
     (`COMMON_BARE`) is kept but tagged separately, since that bucket is the
     least certain: it is *absence* of a non-common keyword, not *presence*
     of a common one.
  5. The primary symbol resolves to a CIK in the supplied SEC
     `company_tickers_exchange.json` snapshot (exact match, then a
     dot-to-dash and a dollar-strip normalization — both recorded per row).

THIS IS A DISPLAY-ONLY, UNRATIFIED ESTIMATE.
No trading, order, capital, or identity-ratification authority is granted or
implied anywhere in this module.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import datetime as dt
import hashlib
import json
from pathlib import Path
import re

SCHEMA_VERSION = "us_investable_universe_t1_display/1"

AUTHORITY = {
    "t1_display_only": True,
    "ratified": False,
    "investable_eligible_authority": False,
    "identity_bulk_ratification_authority": False,
    "trading_or_order_authority": False,
    "capital_authority": False,
}

# ---------------------------------------------------------------- security-type heuristic
# A name-pattern heuristic only (CIO dispatch, plan §2). Not a ratified
# security-type determination -- see module docstring.
_NEGATIVE_HINTS = re.compile(
    "|".join([
        r"\bpreferred\b", r"\bpreference\b", r"\bpfd\b", r"\bwarrant", r"\bright(s)?\b",
        r"\bunit(s)?\b", r"\bnote(s)?\b", r"\bdebenture", r"\bbond(s)?\b",
        r"subscription receipt", r"when issued", r"\bfund\b", r"\btrust\b",
        r"\betn\b", r"exchangeable", r"\binterest(s)?\b", r"\bzones\b",
        r"closed end fund",
    ]),
    re.IGNORECASE,
)
_ADS_HINT = re.compile(
    r"\badss?\b|adr|american depositary|american depository"
    r"|depositary shares|depository shares",
    re.IGNORECASE,
)
_ORDINARY_HINT = re.compile(r"\bordinary share(s)?\b", re.IGNORECASE)
_COMMON_HINT = re.compile(
    r"\bcommon (stock|share(s)?)\b|subordinate voting share|capital stock",
    re.IGNORECASE,
)

SECURITY_TYPE_ADS = "ADS"
SECURITY_TYPE_ORDINARY = "ORDINARY"
SECURITY_TYPE_COMMON = "COMMON"
SECURITY_TYPE_COMMON_BARE = "COMMON_BARE"  # kept, but least-certain bucket
SECURITY_TYPE_EXCLUDED = "EXCLUDED_NON_COMMON"
KEPT_SECURITY_TYPES = (
    SECURITY_TYPE_ADS, SECURITY_TYPE_ORDINARY, SECURITY_TYPE_COMMON, SECURITY_TYPE_COMMON_BARE,
)


def classify_security_name(security_name: str) -> str:
    name = (security_name or "").strip()
    if _ADS_HINT.search(name):
        return SECURITY_TYPE_ADS
    if _ORDINARY_HINT.search(name):
        return SECURITY_TYPE_ORDINARY
    if _COMMON_HINT.search(name) or name.lower().endswith("common"):
        return SECURITY_TYPE_COMMON
    if _NEGATIVE_HINTS.search(name):
        return SECURITY_TYPE_EXCLUDED
    return SECURITY_TYPE_COMMON_BARE


# ---------------------------------------------------------------- CIK lookup
def normalize_symbol(symbol: str) -> str:
    return (symbol or "").strip().upper()


def lookup_cik(symbol: str, cik_by_ticker: dict) -> tuple[str | None, str | None]:
    """Returns (cik, match_method) or (None, None). Every attempt is
    reconstructible from `symbol` alone -- no hidden alias table."""
    exact = normalize_symbol(symbol)
    if exact in cik_by_ticker:
        return cik_by_ticker[exact], "exact"
    dot_to_dash = exact.replace(".", "-")
    if dot_to_dash != exact and dot_to_dash in cik_by_ticker:
        return cik_by_ticker[dot_to_dash], "dot_to_dash"
    strip_dollar = exact.replace("$", "")
    if strip_dollar != exact and strip_dollar in cik_by_ticker:
        return cik_by_ticker[strip_dollar], "strip_dollar"
    return None, None


def cik_by_ticker_from_snapshot(snapshot: dict) -> dict:
    fields = snapshot.get("fields")
    if fields != ["cik", "name", "ticker", "exchange"]:
        raise ValueError("SEC_TICKERS_EXCHANGE_FIELDS_UNEXPECTED")
    out: dict[str, str] = {}
    for cik, _name, ticker, _exchange in snapshot["data"]:
        t = normalize_symbol(ticker)
        if t:
            out.setdefault(t, str(cik))
    return out


# ---------------------------------------------------------------- pipeline
EXCLUSION_ETF = "ETF"
EXCLUSION_TEST_ISSUE = "TEST_ISSUE"
EXCLUSION_FINANCIAL_STATUS = "FINANCIAL_STATUS_NOT_NORMAL"
EXCLUSION_SECURITY_TYPE = "SECURITY_TYPE_NOT_COMMON_OR_ADS"
EXCLUSION_CIK_NOT_FOUND = "SEC_CIK_NOT_FOUND"
EXCLUSION_REASONS = (
    EXCLUSION_ETF, EXCLUSION_TEST_ISSUE, EXCLUSION_FINANCIAL_STATUS,
    EXCLUSION_SECURITY_TYPE, EXCLUSION_CIK_NOT_FOUND,
)


class USInvestableUniverseError(ValueError):
    pass


def evaluate_row(row: dict, cik_by_ticker: dict) -> tuple[str | None, dict]:
    """Returns (exclusion_reason_or_None, detail). detail always includes
    enough to reconstruct the decision from the row alone."""
    f = row.get("fields", {})
    if f.get("ETF") == "Y":
        return EXCLUSION_ETF, {}
    if f.get("Test Issue") == "Y":
        return EXCLUSION_TEST_ISSUE, {}
    financial_status = f.get("Financial Status")
    financial_status_available = financial_status is not None
    if financial_status_available and financial_status != "N":
        return EXCLUSION_FINANCIAL_STATUS, {"financial_status": financial_status}

    security_type = classify_security_name(f.get("Security Name", ""))
    if security_type not in KEPT_SECURITY_TYPES:
        return EXCLUSION_SECURITY_TYPE, {"security_type": security_type}

    cik, match_method = lookup_cik(row.get("primary_symbol", ""), cik_by_ticker)
    if not cik:
        return EXCLUSION_CIK_NOT_FOUND, {"security_type": security_type}

    return None, {
        "security_type": security_type,
        "security_type_certainty": (
            "explicit_keyword_match" if security_type != SECURITY_TYPE_COMMON_BARE
            else "inferred_from_absence_of_non_common_suffix"
        ),
        "cik": cik,
        "cik_match_method": match_method,
        "financial_status_available": financial_status_available,
    }


def build_investable_universe(
    packet: dict,
    cik_snapshot: dict,
    *,
    generated_at_utc: str,
    source_packet_ref: dict,
    cik_snapshot_ref: dict,
) -> dict:
    rows = packet.get("source_attribute_rows")
    if not isinstance(rows, list) or not rows:
        raise USInvestableUniverseError("SOURCE_ATTRIBUTE_ROWS_MISSING_OR_EMPTY")

    cik_by_ticker = cik_by_ticker_from_snapshot(cik_snapshot)

    exclusion_counts = {reason: 0 for reason in EXCLUSION_REASONS}
    kept_rows = []
    for row in rows:
        reason, detail = evaluate_row(row, cik_by_ticker)
        if reason is not None:
            exclusion_counts[reason] += 1
            continue
        kept_rows.append({
            "asset_id": row.get("asset_id"),
            "primary_symbol": row.get("primary_symbol"),
            "security_name": row.get("fields", {}).get("Security Name"),
            "source_name": row.get("source_name"),
            **detail,
        })

    total = len(rows)
    total_excluded = sum(exclusion_counts.values())
    if total_excluded + len(kept_rows) != total:
        # Fail closed rather than publish a count that cannot be reconciled.
        raise USInvestableUniverseError("EXCLUSION_COUNT_RECONCILIATION_FAILED")

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc,
        "source_packet": source_packet_ref,
        "cik_snapshot": cik_snapshot_ref,
        "total_source_rows": total,
        "exclusion_counts": exclusion_counts,
        "kept_count": len(kept_rows),
        "kept_security_type_counts": {
            t: sum(1 for r in kept_rows if r["security_type"] == t)
            for t in KEPT_SECURITY_TYPES
        },
        "kept_rows": kept_rows,
        "authority": dict(AUTHORITY),
        "caveats": [
            "T1 discovery display only -- not a ratified investable universe, "
            "not investable_eligible, not a T2/T3 input.",
            "Security-type classification is a Security-Name pattern heuristic "
            "(CIO plan W1 sec.2), not a ratified security-type determination.",
            "COMMON_BARE rows have no recognized common-stock suffix at all; "
            "they are kept because they also have no recognized non-common "
            "suffix, which is weaker evidence than an explicit match.",
            "Financial Status is only present on nasdaqlisted.txt rows; its "
            "absence on otherlisted.txt rows is not treated as 'normal'.",
            "SEC CIK presence is a display cross-check only, not a W3 bulk "
            "identity ratification.",
        ],
    }


# ---------------------------------------------------------------- I/O helpers
def canonical_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def load_latest_packet(observations_dir: Path) -> tuple[dict, dict]:
    """Loads the most recent `data/observations/us_global_universe/<date>/packet.json`.
    Returns (packet_dict_inner, source_packet_ref)."""
    dated_dirs = sorted(
        p for p in observations_dir.iterdir()
        if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}-\d{2}", p.name)
    )
    if not dated_dirs:
        raise USInvestableUniverseError("NO_US_GLOBAL_UNIVERSE_PACKET_FOUND")
    latest = dated_dirs[-1]
    path = latest / "packet.json"
    outer = json.loads(path.read_text(encoding="utf-8"))
    inner = outer["packet"]
    ref = {
        "date": latest.name,
        "path": str(path),
        "payload_sha256": inner.get("payload_sha256"),
        "total_count": inner.get("total_count"),
    }
    return inner, ref


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--observations-dir", default="data/observations/us_global_universe")
    parser.add_argument("--cik-snapshot", required=True, help="path to a captured company_tickers_exchange.json")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    inner_packet, source_ref = load_latest_packet(Path(args.observations_dir))
    cik_path = Path(args.cik_snapshot)
    cik_bytes = cik_path.read_bytes()
    cik_snapshot = json.loads(cik_bytes)
    cik_ref = {
        "path": str(cik_path),
        "sha256": sha256_bytes(cik_bytes),
        "row_count": len(cik_snapshot.get("data", [])),
    }

    result = build_investable_universe(
        inner_packet,
        cik_snapshot,
        generated_at_utc=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        source_packet_ref=source_ref,
        cik_snapshot_ref=cik_ref,
    )
    result["payload_sha256"] = sha256_bytes(canonical_bytes({k: v for k, v in result.items() if k != "payload_sha256"}))

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp_path.replace(out_path)
    print(f"[us_investable_universe_v1] kept={result['kept_count']} "
          f"excluded={sum(result['exclusion_counts'].values())} -> {out_path}")


if __name__ == "__main__":
    main()
