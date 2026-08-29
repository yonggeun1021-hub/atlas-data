#!/usr/bin/env python3
"""Build a current, read-only Crypto market reference from a Kraken snapshot.

This is deliberately separate from the authoritative point-in-time CR-06/07
history.  It applies taxonomy known at the decision time to one current raw
snapshot, so it can answer a user's "what is moving now?" question but can
never be used as historical replay, Regime, Production, action, or order
authority.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import datetime as dt
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[2]
BREADTH_SCRIPT = ROOT / ".github" / "scripts" / "crypto_breadth.py"
CONTRACT_PATH = ROOT / "config" / "crypto_recent_reference_contract.json"
UTC = dt.timezone.utc
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")


class ReferenceError(RuntimeError):
    """Fail-closed source, policy, or calculation violation."""


def fail(code: str, detail: str) -> None:
    raise ReferenceError(f"{code}: {detail}")


def load_breadth_module(path: Path = BREADTH_SCRIPT):
    spec = importlib.util.spec_from_file_location("atlas_crypto_breadth", path)
    if spec is None or spec.loader is None:
        fail("SOURCE_HELPER_INVALID", str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BREADTH = load_breadth_module()


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(code, str(exc))
    if not isinstance(value, dict):
        fail(code, "root must be object")
    return value


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError as exc:
        fail("FILE_HASH_INVALID", str(exc))


def parse_timestamp(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError) as exc:
        fail("GENERATED_AT_INVALID", str(exc))
    if not value.endswith("Z") or parsed.tzinfo is None:
        fail("GENERATED_AT_INVALID", value)
    return parsed.astimezone(UTC)


def load_contract(path: Path = CONTRACT_PATH) -> dict:
    contract = read_json(path, "CONTRACT_INVALID")
    pinned = {
        "schema_version": 1,
        "contract_version": "crypto_recent_reference/v1",
        "mode": "CURRENT_DECISION_TIME_REFERENCE_NOT_PIT_REPLAY",
        "source_helper": ".github/scripts/crypto_breadth.py",
        "source_name": "kraken_spot_market_data",
        "quote_currency": "USD",
        "selection_rule": (
            "ratified_current_taxonomy_over_trailing_30d_turnover_top_100"
        ),
        "window_calendar_days": [7, 30],
        "return_semantics": (
            "finalized_close_div_exact_prior_calendar_day_close_minus_one"
        ),
        "alt_summary_method": "equal_weight_simple_return_and_median",
        "current_candle_policy": "exclude_last_row_always",
        "top_strength_count": 5,
        "output_decimal_places": 6,
        "historical_replay_authorized": False,
        "regime_classification_authorized": False,
        "production_wiring_authorized": False,
        "trading_action_authorized": False,
    }
    if contract != pinned:
        fail("CONTRACT_INVALID", "schema or pinned semantics")
    return contract


def render(value: Decimal, places: int) -> str:
    if not value.is_finite():
        fail("NUMBER_INVALID", str(value))
    quantum = Decimal(1).scaleb(-places)
    try:
        rounded = value.quantize(quantum, rounding=ROUND_HALF_EVEN)
    except InvalidOperation as exc:
        fail("NUMBER_INVALID", str(exc))
    if rounded == 0:
        rounded = Decimal(0)
    text = format(rounded, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def percent_change(start: Decimal, end: Decimal) -> Decimal:
    if start <= 0 or end <= 0:
        fail("CLOSE_INVALID", f"{start}..{end}")
    return (end / start - Decimal(1)) * Decimal(100)


def median(values: list[Decimal]) -> Decimal:
    if not values:
        fail("WINDOW_EMPTY", "median")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / Decimal(2)


def close_series(snapshot_dir: Path, core: dict) -> dict[str, dict[dt.date, Decimal]]:
    bundle_path = Path(snapshot_dir) / core["contract"]["ohlc_bundle_raw_file"]
    try:
        with gzip.open(bundle_path, "rb") as stream:
            lines = stream.read().splitlines()
    except (OSError, EOFError) as exc:
        fail("OHLC_BUNDLE_INVALID", str(exc))
    output = {}
    for index, line in enumerate(lines):
        try:
            record = json.loads(line)
            pair_id = record["pair_id"]
            raw = base64.b64decode(record["body_b64"], validate=True)
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            KeyError,
            TypeError,
            ValueError,
            binascii.Error,
        ) as exc:
            fail("OHLC_BUNDLE_INVALID", f"line {index}: {exc}")
        response_sha = record.get("response_sha256")
        if not isinstance(response_sha, str) or SHA256.fullmatch(response_sha) is None:
            fail("OHLC_BUNDLE_INVALID", f"line {index} hash")
        if hashlib.sha256(raw).hexdigest() != response_sha:
            fail("OHLC_INNER_CHECKSUM_MISMATCH", pair_id)
        expected = core["ohlc"].get(pair_id)
        if expected is None or expected["response_sha256"] != response_sha:
            fail("OHLC_LINEAGE_MISMATCH", pair_id)
        try:
            payload = json.loads(
                raw,
                parse_float=Decimal,
                parse_int=int,
                parse_constant=BREADTH.reject_json_constant,
            )
        except (json.JSONDecodeError, InvalidOperation) as exc:
            fail("RAW_RESPONSE_INVALID", f"{pair_id}: {exc}")
        result = BREADTH.source_result(payload, pair_id)
        keys = [key for key in result if key != "last"]
        if keys != [pair_id] or set(result) != {pair_id, "last"}:
            fail("PAYLOAD_SHAPE_INVALID", pair_id)
        candles = [
            BREADTH.normalize_candle(row, row_index, pair_id)
            for row_index, row in enumerate(result[pair_id])
        ]
        if not candles or candles[-1]["date"] != core["vintage"]:
            fail("CURRENT_CANDLE_DATE_MISMATCH", pair_id)
        finalized = candles[:-1]
        output[pair_id] = {item["date"]: item["close"] for item in finalized}
    if set(output) != set(core["ohlc"]):
        fail("OHLC_BUNDLE_INVALID", "pair inventory mismatch")
    return output


def current_members(
    core: dict,
    decision_day: dt.date,
    universe_policy: dict,
    taxonomy_policy: dict,
) -> tuple[list[dict], list[dict], list[dict]]:
    BREADTH.require_ratified_policy(universe_policy, decision_day)
    BREADTH.require_ratified_taxonomy(taxonomy_policy, decision_day)
    allowed_assets = set(universe_policy["allowed_asset_statuses"])
    allowed_pairs = set(universe_policy["allowed_pair_statuses"])
    excluded_categories = set(taxonomy_policy["excluded_categories"])
    ranked = []
    canonical_seen = {}
    for pair_id in sorted(core["pairs"]):
        pair = core["pairs"][pair_id]
        if (
            pair["quote"] != universe_policy["quote_currency"]
            or pair["status"] not in allowed_pairs
            or core["assets"][pair["base"]]["status"] not in allowed_assets
            or core["assets"][pair["quote"]]["status"] not in allowed_assets
        ):
            continue
        canonical = BREADTH.canonical_identity(
            pair["base"], decision_day, core["identity"]
        )
        if canonical in canonical_seen:
            fail(
                "CANONICAL_ASSET_DUPLICATE",
                f"{canonical_seen[canonical]} and {pair_id}",
            )
        canonical_seen[canonical] = pair_id
        series = core["ohlc"].get(pair_id)
        if series is None:
            fail("CAPTURE_COVERAGE_INCOMPLETE", pair_id)
        if not series["ranking_history_complete"]:
            continue
        ranked.append(
            {
                "pair_id": pair_id,
                "canonical_asset_id": canonical,
                "trailing_usd_turnover": series["trailing_usd_turnover"],
            }
        )
    ranked.sort(
        key=lambda item: (
            -item["trailing_usd_turnover"],
            item["canonical_asset_id"],
            item["pair_id"],
        )
    )
    selected = []
    excluded = []
    unknown = []
    for rank, item in enumerate(ranked, start=1):
        category = BREADTH.taxonomy_category(
            item["canonical_asset_id"], decision_day, taxonomy_policy
        )
        summary = {
            "rank_before_taxonomy": rank,
            "canonical_asset_id": item["canonical_asset_id"],
            "pair_id": item["pair_id"],
        }
        if category is None:
            unknown.append(summary)
            continue
        if category in excluded_categories:
            excluded.append(summary | {"category": category})
            continue
        if category != taxonomy_policy["eligible_category"]:
            fail("TAXONOMY_CATEGORY_INVALID", category)
        selected.append(
            item
            | {
                "rank_before_taxonomy": rank,
                "selected_rank": len(selected) + 1,
            }
        )
        if len(selected) == universe_policy["target_asset_count"]:
            break
    if unknown:
        fail(
            "CURRENT_TAXONOMY_COVERAGE_UNKNOWN",
            ",".join(item["canonical_asset_id"] for item in unknown),
        )
    if len(selected) != universe_policy["target_asset_count"]:
        fail(
            "CURRENT_ELIGIBLE_UNIVERSE_INCOMPLETE",
            f"{len(selected)}/{universe_policy['target_asset_count']}",
        )
    return selected, excluded, unknown


def window_summary(
    members: list[dict],
    series: dict[str, dict[dt.date, Decimal]],
    end_day: dt.date,
    days: int,
    places: int,
) -> tuple[dict, list[dict]]:
    start_day = end_day - dt.timedelta(days=days)
    returns = []
    missing = []
    for member in members:
        closes = series[member["pair_id"]]
        start = closes.get(start_day)
        end = closes.get(end_day)
        if start is None or end is None:
            missing.append(member["canonical_asset_id"])
            continue
        returns.append(
            member
            | {"return": percent_change(start, end)}
        )
    by_asset = {item["canonical_asset_id"]: item["return"] for item in returns}
    if "BTC" not in by_asset or "ETH" not in by_asset:
        fail("REFERENCE_ASSET_MISSING", f"{days}d")
    btc_return = by_asset["BTC"]
    eth_return = by_asset["ETH"]
    alt_returns = [
        item["return"]
        for item in returns
        if item["canonical_asset_id"] not in {"BTC", "ETH"}
    ]
    if not alt_returns:
        fail("ALT_UNIVERSE_EMPTY", f"{days}d")
    alt_equal_weight = sum(alt_returns, Decimal(0)) / Decimal(len(alt_returns))
    alt_median = median(alt_returns)
    leaders = {
        "BTC": btc_return,
        "ETH": eth_return,
        "ALT_EQUAL_WEIGHT": alt_equal_weight,
    }
    leading_bucket = sorted(leaders, key=lambda key: (-leaders[key], key))[0]
    positive = sum(item["return"] > 0 for item in returns)
    outperform_btc = sum(
        item["return"] > btc_return
        for item in returns
        if item["canonical_asset_id"] != "BTC"
    )
    summary = {
        "calendar_days": days,
        "start_date": start_day.isoformat(),
        "end_date": end_day.isoformat(),
        "selected_asset_count": len(members),
        "observed_asset_count": len(returns),
        "missing_asset_count": len(missing),
        "missing_assets": sorted(missing),
        "positive_asset_count": positive,
        "positive_asset_fraction": render(
            Decimal(positive) / Decimal(len(returns)), places
        ),
        "outperform_btc_asset_count": outperform_btc,
        "outperform_btc_comparison_count": len(returns) - 1,
        "outperform_btc_fraction": render(
            Decimal(outperform_btc) / Decimal(len(returns) - 1), places
        ),
        "btc_return_pct": render(btc_return, places),
        "eth_return_pct": render(eth_return, places),
        "alt_equal_weight_return_pct": render(alt_equal_weight, places),
        "alt_median_return_pct": render(alt_median, places),
        "leading_bucket_by_raw_return": leading_bucket,
    }
    return summary, returns


def build_reference(
    snapshot_dir: Path,
    generated_at: str,
    source_commit: str,
    contract_path: Path = CONTRACT_PATH,
    universe_policy_path: Path = BREADTH.UNIVERSE_POLICY_PATH,
    taxonomy_path: Path = BREADTH.EXCLUSION_TAXONOMY_PATH,
    identity_path: Path = BREADTH.IDENTITY_EXCEPTIONS_PATH,
) -> dict:
    contract = load_contract(contract_path)
    generated = parse_timestamp(generated_at)
    if GIT_COMMIT.fullmatch(source_commit) is None:
        fail("SOURCE_COMMIT_INVALID", source_commit)
    core = BREADTH.source_core(
        snapshot_dir,
        identity_exceptions_path=identity_path,
    )
    manifest = BREADTH.validate_manifest(core, snapshot_dir)
    if generated < dt.datetime.fromisoformat(
        core["fetched_at_utc"].replace("Z", "+00:00")
    ):
        fail("GENERATED_BEFORE_CAPTURE", generated_at)
    decision_day = generated.date()
    universe_policy = BREADTH.load_universe_policy(universe_policy_path)
    taxonomy_policy = BREADTH.load_exclusion_taxonomy(taxonomy_path)
    members, excluded, unknown = current_members(
        core, decision_day, universe_policy, taxonomy_policy
    )
    series = close_series(snapshot_dir, core)
    end_day = core["vintage"] - dt.timedelta(days=1)
    windows = {}
    window_returns = {}
    for days in contract["window_calendar_days"]:
        summary, returns = window_summary(
            members,
            series,
            end_day,
            days,
            contract["output_decimal_places"],
        )
        windows[f"{days}d"] = summary
        window_returns[days] = returns
    primary = window_returns[30]
    strength = sorted(
        primary,
        key=lambda item: (
            -item["return"],
            item["canonical_asset_id"],
        ),
    )[: contract["top_strength_count"]]
    member_returns = []
    returns_7d = {
        item["canonical_asset_id"]: item["return"]
        for item in window_returns[7]
    }
    for item in sorted(primary, key=lambda value: value["selected_rank"]):
        member_returns.append(
            {
                "selected_rank": item["selected_rank"],
                "canonical_asset_id": item["canonical_asset_id"],
                "pair_id": item["pair_id"],
                "return_7d_pct": (
                    render(
                        returns_7d[item["canonical_asset_id"]],
                        contract["output_decimal_places"],
                    )
                    if item["canonical_asset_id"] in returns_7d
                    else None
                ),
                "return_30d_pct": render(
                    item["return"], contract["output_decimal_places"]
                ),
            }
        )
    return {
        "schema_version": contract["schema_version"],
        "contract_version": contract["contract_version"],
        "mode": contract["mode"],
        "generated_at_utc": generated.isoformat(timespec="seconds").replace(
            "+00:00", "Z"
        ),
        "decision_date": decision_day.isoformat(),
        "price_as_of_date": end_day.isoformat(),
        "source": {
            "source_name": contract["source_name"],
            "source_commit": source_commit,
            "snapshot_path": Path(snapshot_dir).resolve().relative_to(ROOT).as_posix(),
            "snapshot_date": core["snapshot_date"],
            "fetched_at_utc": core["fetched_at_utc"],
            "manifest_sha256": file_sha256(Path(snapshot_dir) / "_manifest.json"),
            "capture_version": manifest["capture_version"],
            "current_candle_excluded": True,
        },
        "selection": {
            "selection_rule": contract["selection_rule"],
            "target_asset_count": universe_policy["target_asset_count"],
            "selected_asset_count": len(members),
            "excluded_before_cutoff_count": len(excluded),
            "taxonomy_unknown_before_cutoff_count": len(unknown),
            "taxonomy_as_of_date": decision_day.isoformat(),
            "taxonomy_policy_version": taxonomy_policy["policy_version"],
            "taxonomy_sha256": file_sha256(taxonomy_path),
            "universe_policy_version": universe_policy["policy_version"],
            "universe_policy_sha256": file_sha256(universe_policy_path),
            "current_catalog_backfill_for_historical_replay_authorized": False,
        },
        "windows": windows,
        "top_30d_strength": [
            {
                "canonical_asset_id": item["canonical_asset_id"],
                "pair_id": item["pair_id"],
                "return_30d_pct": render(
                    item["return"], contract["output_decimal_places"]
                ),
            }
            for item in strength
        ],
        "member_returns": member_returns,
        "authority": {
            "reference_only": True,
            "historical_replay_authorized": False,
            "regime_classification_authorized": False,
            "production_wiring_authorized": False,
            "stage_authorized": False,
            "buy_authorized": False,
            "action_authorized": False,
            "order_authorized": False,
            "trading_authorized": False,
        },
    }


def canonical_bytes(payload: dict) -> bytes:
    return (
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")


def write_output(payload: dict, out: Path) -> str:
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_bytes(payload)
    output.write_bytes(raw)
    return hashlib.sha256(raw).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot_dir", type=Path)
    parser.add_argument("--generated-at", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args(argv)
    payload = build_reference(
        args.snapshot_dir,
        args.generated_at,
        args.source_commit,
    )
    if args.out:
        print(write_output(payload, args.out))
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (ReferenceError, BREADTH.BreadthError) as exc:
        print(f"FATAL: {exc}")
        sys.exit(1)
