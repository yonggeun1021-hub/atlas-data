#!/usr/bin/env python3
"""Build the plain-language PAPER market risk reference.

This is deliberately separate from the canonical Regime Decision Authority.
It turns already-retained free observations into a user-facing diagnostic for
PAPER review.  It cannot authorize a final regime, stage change, order, capital,
production, or trading action.  Missing/tampered/incomplete inputs fail closed.
"""

from __future__ import annotations

import argparse
import copy
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "paper_regime_reference_policy_v1.json"
US_PATH = ROOT / "data" / "latest_free_market_data.json"
KR_PATH = ROOT / "data" / "latest_korea_market_signals.json"
CRYPTO_PATH = ROOT / "data" / "latest_crypto_regime_refresh_status.json"
LATEST_PATH = ROOT / "data" / "latest_paper_regime_reference.json"
SCHEMA_VERSION = "paper_regime_reference/v2"
AXES = ["TREND", "BREADTH", "RISK_VOL", "LIQUIDITY", "LEADERSHIP"]
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class PaperRegimeReferenceError(ValueError):
    pass


def fail(code: str, detail: str = "") -> None:
    raise PaperRegimeReferenceError(f"{code}:{detail}" if detail else code)


def canonical_json(value: object) -> str:
    try:
        return json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise PaperRegimeReferenceError("CANONICAL_JSON_INVALID") from exc


def payload_sha256(value: object) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        raise PaperRegimeReferenceError(f"SOURCE_MISSING:{path}") from exc


def read_json(path: Path, code: str) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PaperRegimeReferenceError(code) from exc
    if not isinstance(value, dict):
        fail(code, "object required")
    return value


def decimal(value: object, code: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise PaperRegimeReferenceError(code) from exc
    if not parsed.is_finite():
        fail(code)
    return parsed


def ratio_direction(value: Decimal, positive_min: Decimal, negative_max: Decimal) -> str:
    if value >= positive_min:
        return "POSITIVE"
    if value <= negative_max:
        return "NEGATIVE"
    return "NEUTRAL"


def sign_pair(values: list[Decimal]) -> str:
    if all(value > 0 for value in values):
        return "POSITIVE"
    if all(value < 0 for value in values):
        return "NEGATIVE"
    return "NEUTRAL"


def axis(name: str, direction: str, value: object, summary_ko: str) -> dict:
    if name not in AXES or direction not in {"POSITIVE", "NEUTRAL", "NEGATIVE", "STRESS"}:
        fail("AXIS_INVALID", name)
    score = {"POSITIVE": 1, "NEUTRAL": 0, "NEGATIVE": -1, "STRESS": -1}[direction]
    return {
        "axis": name,
        "direction": direction,
        "score": score,
        "observed_value": value,
        "summary_ko": summary_ko,
    }


def classify(axes: list[dict], policy: dict) -> tuple[str, int, str]:
    if len(axes) != 5 or [row["axis"] for row in axes] != AXES:
        return "UNKNOWN", 0, "필수 신호 5개가 모두 확인되지 않았습니다."
    score = sum(row["score"] for row in axes)
    if any(row["direction"] == "STRESS" for row in axes):
        return "STRESS", score, "시장 불안 신호가 위험 구간이라 점수보다 우선했습니다."
    aggregation = policy["aggregation"]
    if score >= aggregation["RISK_ON_MIN_SCORE"]:
        return "RISK_ON", score, "상승·확산·안정 신호가 우세합니다."
    if score <= aggregation["RISK_OFF_MAX_SCORE"]:
        return "RISK_OFF", score, "약세·위축·불안 신호가 우세합니다."
    return "NEUTRAL", score, "좋은 신호와 약한 신호가 섞여 있습니다."


def confidence(regime: str, axes: list[dict]) -> Decimal | None:
    target = {"RISK_ON": "POSITIVE", "RISK_OFF": "NEGATIVE", "NEUTRAL": "NEUTRAL"}.get(regime)
    if regime == "STRESS":
        return Decimal("1")
    if target is None:
        return None
    return Decimal(sum(row["direction"] == target for row in axes)) / Decimal(5)


def build_us(packet: dict, policy: dict) -> dict:
    reference = packet.get("us_market_reference")
    if not isinstance(reference, dict) or reference.get("status") != "READY":
        fail("US_REFERENCE_NOT_READY")
    trends = reference.get("trend_etfs")
    proxy = reference.get("proxy_axes")
    if not isinstance(trends, list) or len(trends) != 3 or not isinstance(proxy, dict):
        fail("US_REFERENCE_INCOMPLETE")

    trend_returns = [decimal(row.get("returns", {}).get("20_session_pct"), "US_TREND_INVALID") for row in trends]
    trend_positive_fraction = Decimal(sum(value > 0 for value in trend_returns)) / Decimal(len(trend_returns))
    trend_direction = ratio_direction(trend_positive_fraction, Decimal("0.666667"), Decimal("0.333333"))

    breadth_value = decimal(proxy.get("BREADTH", {}).get("measurement", {}).get("advance_fraction"), "US_BREADTH_INVALID")
    breadth_direction = ratio_direction(breadth_value, Decimal("0.55"), Decimal("0.45"))

    vix = decimal(packet.get("fred", {}).get("value"), "US_VIX_INVALID")
    if vix < Decimal("15"):
        risk_direction = "POSITIVE"
    elif vix < Decimal("25"):
        risk_direction = "NEUTRAL"
    elif vix < Decimal("30"):
        risk_direction = "NEGATIVE"
    else:
        risk_direction = "STRESS"

    liquidity_rows = packet.get("fred_liquidity", {}).get("series")
    if not isinstance(liquidity_rows, list) or {row.get("series_id") for row in liquidity_rows} != {"WRESBAL", "TOTBKCR"}:
        fail("US_LIQUIDITY_INVALID")
    liquidity_changes = [decimal(row.get("change"), "US_LIQUIDITY_INVALID") for row in liquidity_rows]
    liquidity_direction = sign_pair(liquidity_changes)

    groups = proxy.get("LEADERSHIP", {}).get("measurement", {}).get("ordered_groups")
    if not isinstance(groups, list) or len(groups) != 12:
        fail("US_LEADERSHIP_INVALID")
    positive_groups = sum(decimal(row.get("return_pct"), "US_LEADERSHIP_INVALID") > 0 for row in groups)
    leadership_fraction = Decimal(positive_groups) / Decimal(len(groups))
    leadership_direction = ratio_direction(leadership_fraction, Decimal("0.666667"), Decimal("0.333333"))

    rows = [
        axis("TREND", trend_direction, {"positive": sum(value > 0 for value in trend_returns), "total": 3}, f"대표지수 3개 중 {sum(value > 0 for value in trend_returns)}개가 20거래일 기준 상승입니다."),
        axis("BREADTH", breadth_direction, {"advance_fraction": str(breadth_value)}, f"대표 ETF 중 상승 비중은 {breadth_value * 100:.1f}%입니다."),
        axis("RISK_VOL", risk_direction, {"vix": str(vix)}, f"VIX는 {vix}로 {'낮은' if risk_direction == 'POSITIVE' else '보통' if risk_direction == 'NEUTRAL' else '높은'} 구간입니다."),
        axis("LIQUIDITY", liquidity_direction, {row["series_id"]: row["change"] for row in liquidity_rows}, "연준 준비금과 은행 신용 변화 방향이 서로 엇갈립니다." if liquidity_direction == "NEUTRAL" else "유동성 지표 두 개가 같은 방향입니다."),
        axis("LEADERSHIP", leadership_direction, {"positive_groups": positive_groups, "total": 12}, f"대표 업종 12개 중 {positive_groups}개가 20거래일 기준 상승입니다."),
    ]
    regime, score, explanation = classify(rows, policy)
    return market_packet("US", reference["as_of_session_date"], rows, regime, score, explanation)


def build_kr(packet: dict, policy: dict) -> dict:
    if packet.get("status") != "OBSERVED_UNCLASSIFIED" or packet.get("coverage", {}).get("ratio") != "5/5":
        fail("KR_REFERENCE_NOT_READY")
    axes = packet.get("axes")
    if not isinstance(axes, dict) or any(axes.get(name, {}).get("status") != "OBSERVED" for name in AXES):
        fail("KR_REFERENCE_INCOMPLETE")

    benchmarks = axes["TREND"]["measurement"]["benchmarks"]
    trend_values = [decimal(benchmarks[name]["one_session_return_pct"], "KR_TREND_INVALID") for name in ("KOSPI", "KOSDAQ")]
    trend_direction = sign_pair(trend_values)
    breadth_value = decimal(axes["BREADTH"]["measurement"]["combined"]["advance_fraction"], "KR_BREADTH_INVALID")
    breadth_direction = ratio_direction(breadth_value, Decimal("0.55"), Decimal("0.45"))
    move = decimal(axes["RISK_VOL"]["measurement"]["combined_mean_absolute_stock_move_pct"], "KR_RISK_INVALID")
    if move <= Decimal("1.5"):
        risk_direction = "POSITIVE"
    elif move <= Decimal("2.5"):
        risk_direction = "NEUTRAL"
    elif move <= Decimal("3.5"):
        risk_direction = "NEGATIVE"
    else:
        risk_direction = "STRESS"
    trading_value_change = decimal(axes["LIQUIDITY"]["measurement"]["combined"]["trading_value_change_pct"], "KR_LIQUIDITY_INVALID")
    liquidity_direction = "POSITIVE" if trading_value_change >= 5 else "NEGATIVE" if trading_value_change <= -5 else "NEUTRAL"
    sectors = axes["LEADERSHIP"]["measurement"]["observations"]
    if not isinstance(sectors, list) or not sectors:
        fail("KR_LEADERSHIP_INVALID")
    positive_sectors = sum(decimal(row.get("sector_return_pct"), "KR_LEADERSHIP_INVALID") > 0 for row in sectors)
    leadership_fraction = Decimal(positive_sectors) / Decimal(len(sectors))
    leadership_direction = ratio_direction(leadership_fraction, Decimal("0.60"), Decimal("0.40"))

    rows = [
        axis("TREND", trend_direction, {"KOSPI": str(trend_values[0]), "KOSDAQ": str(trend_values[1])}, f"코스피 {trend_values[0]:+.2f}%, 코스닥 {trend_values[1]:+.2f}%로 방향이 엇갈렸습니다."),
        axis("BREADTH", breadth_direction, {"advance_fraction": str(breadth_value)}, f"전체 종목 중 상승 비중은 {breadth_value * 100:.1f}%입니다."),
        axis("RISK_VOL", risk_direction, {"mean_absolute_move_pct": str(move)}, f"종목 평균 절대 등락폭은 {move:.2f}%로 보통 구간입니다."),
        axis("LIQUIDITY", liquidity_direction, {"trading_value_change_pct": str(trading_value_change)}, f"거래대금은 이전 거래일보다 {trading_value_change:+.1f}% 변했습니다."),
        axis("LEADERSHIP", leadership_direction, {"positive_sectors": positive_sectors, "total": len(sectors)}, f"업종 {len(sectors)}개 중 {positive_sectors}개가 상승했습니다."),
    ]
    regime, score, explanation = classify(rows, policy)
    return market_packet("KR", packet["as_of_date"], rows, regime, score, explanation)


def market_packet(market: str, as_of_date: str, axes: list[dict], regime: str, score: int, explanation: str) -> dict:
    conf = confidence(regime, axes)
    return {
        "market": market,
        "as_of_date": as_of_date,
        "coverage": {"defined_count": len(axes), "required_count": 5, "ratio": f"{len(axes)}/5", "missing_axes": []},
        "paper_reference": {"candidate_regime": regime, "score": score, "confidence": None if conf is None else str(conf), "explanation_ko": explanation},
        "classification_status": "PAPER_REFERENCE_CLASSIFIED",
        "runtime_regime": "UNKNOWN",
        "axes": axes,
    }


def build_crypto(packet: dict) -> dict:
    if packet.get("schema_version") != "crypto_regime_refresh_status/1":
        fail("CRYPTO_SOURCE_INVALID")
    unsigned = copy.deepcopy(packet)
    claimed = unsigned.pop("payload_sha256", None)
    if (
        not isinstance(claimed, str)
        or SHA256.fullmatch(claimed) is None
        or payload_sha256(unsigned) != claimed
    ):
        fail("CRYPTO_SOURCE_SHA_INVALID")
    authority = packet.get("authority")
    if not isinstance(authority, dict) or authority.get("read_only_reference") is not True:
        fail("CRYPTO_SOURCE_AUTHORITY_INVALID")
    for key, value in authority.items():
        if key != "read_only_reference" and value is not False:
            fail("CRYPTO_SOURCE_AUTHORITY_INVALID", key)
    official = packet.get("official_decision")
    coverage = official.get("coverage") if isinstance(official, dict) else None
    if (
        not isinstance(coverage, dict)
        or coverage.get("required_count") != 5
        or coverage.get("defined_count") not in range(0, 6)
        or coverage.get("ratio") != f"{coverage['defined_count']}/5"
        or coverage.get("defined_axes") != [axis for axis in AXES if axis not in coverage.get("missing_axes", [])]
    ):
        fail("CRYPTO_SOURCE_COVERAGE_INVALID")
    complete = coverage["defined_count"] == 5
    classification_status = (
        "WAIT_MARKET_NORMALIZATION_POLICY"
        if complete
        else "WAIT_OFFICIAL_INPUT_COVERAGE"
    )
    explanation = (
        "필수 신호 5개는 모두 확인됐지만 코인 전용 방향·점수 규칙의 검증이 끝날 때까지 Risk On/Off를 보류합니다."
        if complete
        else "오늘 참고 신호는 5개 모두 확인됐지만, 자동 판정용 주도 코인 이력은 아직 검증 중입니다."
    )
    return {
        "market": "CRYPTO",
        "as_of_date": packet.get("current_reference", {}).get("as_of_date"),
        "coverage": copy.deepcopy(coverage),
        "paper_reference": {
            "candidate_regime": "UNKNOWN",
            "score": None,
            "confidence": None,
            "explanation_ko": explanation,
        },
        "classification_status": classification_status,
        "runtime_regime": "UNKNOWN",
        "axes": [],
    }


def build_reference(root: Path = ROOT) -> dict:
    policy_path = root / "config" / "paper_regime_reference_policy_v1.json"
    us_path = root / "data" / "latest_free_market_data.json"
    kr_path = root / "data" / "latest_korea_market_signals.json"
    crypto_path = root / "data" / "latest_crypto_regime_refresh_status.json"
    policy = read_json(policy_path, "POLICY_INVALID")
    if policy.get("contract_version") != "paper_regime_reference_policy/v1" or policy.get("mode") != "PAPER_DIAGNOSTIC_NOT_RUNTIME":
        fail("POLICY_INVALID")
    authority = policy.get("authority")
    if not isinstance(authority, dict) or authority.get("paper_reference_display_authorized") is not True:
        fail("POLICY_AUTHORITY_INVALID")
    for key, value in authority.items():
        if key.endswith("_authorized") and key not in {"paper_reference_display_authorized", "paper_symbol_context_authorized"} and value is not False:
            fail("POLICY_AUTHORITY_INVALID", key)

    us_source = read_json(us_path, "US_SOURCE_INVALID")
    kr_source = read_json(kr_path, "KR_SOURCE_INVALID")
    crypto_source = read_json(crypto_path, "CRYPTO_SOURCE_INVALID")
    sources = [
        {"market": "US", "path": "data/latest_free_market_data.json", "sha256": file_sha256(us_path)},
        {"market": "KR", "path": "data/latest_korea_market_signals.json", "sha256": file_sha256(kr_path)},
        {"market": "CRYPTO", "path": "data/latest_crypto_regime_refresh_status.json", "sha256": file_sha256(crypto_path)},
    ]
    generation_id = payload_sha256({"policy_sha256": file_sha256(policy_path), "sources": sources})
    markets = [
        build_us(us_source, policy),
        build_kr(kr_source, policy),
        build_crypto(crypto_source),
    ]
    packet = {
        "schema_version": SCHEMA_VERSION,
        "contract_version": policy["contract_version"],
        "mode": policy["mode"],
        "status": "PARTIAL_REFERENCE_AVAILABLE",
        "generated_at": max(
            us_source["observed_at_utc"],
            kr_source["generated_at"],
            crypto_source["generated_at"],
        ),
        "generation_id": generation_id,
        "policy": {"path": "config/paper_regime_reference_policy_v1.json", "sha256": file_sha256(policy_path), "status": policy["status"]},
        "sources": sources,
        "markets": markets,
        "authority": copy.deepcopy(authority),
    }
    packet["payload_sha256"] = payload_sha256(packet)
    return packet


def validate_reference(packet: dict, root: Path = ROOT) -> dict:
    if not isinstance(packet, dict) or packet.get("schema_version") != SCHEMA_VERSION:
        fail("REFERENCE_SCHEMA_INVALID")
    unsigned = copy.deepcopy(packet)
    claimed = unsigned.pop("payload_sha256", None)
    if not isinstance(claimed, str) or SHA256.fullmatch(claimed) is None or payload_sha256(unsigned) != claimed:
        fail("REFERENCE_SHA_INVALID")
    expected = build_reference(root)
    if packet != expected:
        fail("REFERENCE_REDERIVATION_MISMATCH")
    return copy.deepcopy(packet)


def write_packet(packet: dict, root: Path = ROOT) -> tuple[Path, Path]:
    market_dates = [row["as_of_date"] for row in packet["markets"] if row["as_of_date"]]
    evidence_date = max(market_dates)
    evidence = root / "evidence" / "regime" / "paper_reference" / evidence_date / packet["generation_id"] / "packet.json"
    latest = root / "data" / "latest_paper_regime_reference.json"
    evidence.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    if evidence.exists() and evidence.read_text(encoding="utf-8") != text:
        fail("APPEND_ONLY_EVIDENCE_CONFLICT")
    evidence.write_text(text, encoding="utf-8")
    latest.write_text(text, encoding="utf-8")
    return evidence, latest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    if args.verify:
        value = read_json(args.verify, "REFERENCE_INVALID")
        validate_reference(value)
        print("PASS_PAPER_REGIME_REFERENCE_VERIFIED")
        return 0
    packet = build_reference()
    if args.write:
        evidence, latest = write_packet(packet)
        print(json.dumps({"status": packet["status"], "evidence": str(evidence.relative_to(ROOT)), "latest": str(latest.relative_to(ROOT)), "generation_id": packet["generation_id"]}, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(packet, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
