#!/usr/bin/env python3
"""Actions runner — Atlas B1 migration package.

CIO 판정 2026-08-15 로 확정된 `Actions PASS` 계약 네 가지를 기계적으로 검증한다.

  ① clean checkout 에서 승인된 회귀 전량 실행, 0 FAIL
  ② 승인 산출물을 builder ①→⑭ 직렬 재빌드해 committed 본과 **byte-identical**
  ③ 기존 fail-closed / authority / evaluator / Production HOLD /
     Inventory-Population / RULE-MON identity 경계 유지 (①이 담당)
  ④ 별도 승인된 Fault Injection suite PASS

★ 이 runner 가 orchestration authority 다.
  builder 마다 종료 방식을 통일하지 않는다 — uncaught exception · 명시적 non-zero ·
  검증 오류 · 산출물 미생성 · byte 불일치를 **여기서** 종합해 최종 non-zero 를 낸다.

★ 재빌드는 **사본 보존 방식**이다 (CIO 판정 — (나) API 개조는 기각).
  committed 산출물을 먼저 byte-for-byte 사본으로 떠 두고, 정상 경로에서 재빌드한 뒤
  사본과 비교한다. builder 에 범용 out_path/input injection 을 추가하지 않는다.
  ⛔ 비교 기준은 bytes 동일성이다. SHA-256 은 표시·진단용으로만 쓴다.

⛔ 이 runner 가 하지 않는 것 — Production 상태 변경 · evaluator 배선 ·
   `consumable_by_evaluator` 전환 · 산출물 의미 보정 · 실패 자동 복구.
"""
from __future__ import annotations

import filecmp
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

# ══════════════════════════════════════════════════════════════════════
# 계약 상수 — 전부 CIO 판정에서 왔다. 여기서 새로 만들지 않는다.
# ══════════════════════════════════════════════════════════════════════

# ★ builder 직렬 실행 순서 ①→⑭ (CIO 확정). DAG 의 유효한 topological ordering 하나를
#   실행 순서로 **선택**한 것이며 dependency 를 새로 만든 것이 아니다.
BUILDERS = [
    ("rules/extract.py", "config/rules.candidates.json"),
    ("rules/build_full_decomposition.py", "rules/decompose_full.json"),
    ("rules/canonicalize.py", "rules/canonical_rules.json"),
    ("rules/equivalence_candidates.py", "rules/equivalence_candidates.json"),
    ("rules/merge_decision.py", "rules/merge_decision.json"),
    ("rules/definition_inventory.py", "rules/definition_inventory.json"),
    ("rules/definition_decision.py", "rules/definition_decision.json"),
    ("rules/data_source_ambiguity.py", "rules/data_source_ambiguity.json"),
    ("rules/decision_normalization.py", "rules/decision_normalization.json"),
    ("rules/decision_cards.py", "rules/decision_cards.json"),
    ("rules/ssot_mapping.py", "rules/ssot_mapping.json"),
    ("rules/promote_rules_ssot.py", "config/rules.json"),
    ("rules/monitoring_identity.py", "rules/monitoring_identity.json"),
    ("rules/rule_inventory.py", "rules/rule_inventory.json"),
]

# byte 비교 대상 = 위 14개 산출물.
#   ⛔ decompose_pilot.json · populations.json 은 비권위 view 이므로 제외한다
#      (CIO 판정 13). 저장소 보존은 하되 authority 산출물처럼 취급하지 않는다.
COMPARED = [out for _, out in BUILDERS]
NON_AUTHORITY_VIEWS = ["rules/decompose_pilot.json", "rules/populations.json"]

# ★ 승인 회귀 목록. 이 목록과 실제 test/test_*.py 집합이 다르면 FAIL 이다.
#   ⛔ FI suite 는 여기 섞지 않는다 (CIO 판정 9).
APPROVED_TESTS = [
    # ★ Observation layer 구현 상태가 실제 모듈과 어긋나지 않게 한다.
    #   ③ Normalization·④ Store는 구현, ⑤ Pair·⑥ Evaluator는 미구현 경계다.
    "test/test_observation_layer_inventory.py",
    # ★ runner summary 자체가 승인 목록의 현재 개수를 표시하는지 검증한다.
    #   고정 숫자 drift가 실제 실행 증거를 축소·과장하지 못하게 한다.
    "test/test_runner_reporting.py",
    # ★ CI runtime maintenance — official Node 24 action releases.
    #   checkout/setup-python/upload-artifact를 검증된 release commit SHA로
    #   고정해 mutable tag와 Node 20 deprecation을 제거한다. workflow 권한·
    #   trigger·run 내용은 불변이다.
    "test/test_github_actions_runtime.py",
    # ★ 운영 검증 전수 inventory — 진행중/외부대기/보류 WBS 22건을
    #   server-side schedule / upstream schedule / human gate / policy blocker로
    #   분류한다. 자동 실행 가능한 코드가 예약 없이 남거나, 미비준 정책을
    #   schedule로 몰래 승격시키는 drift를 막는다.
    #   P4-04 TSMC은 primary SEC + secondary IR 주 1회 read-only probe만 허용한다.
    #   ⛔ Codex 로컬 예약·tracked data·Rule/Production/trading 변경 없음.
    "test/test_operational_validation_registry.py",
    # ★ TSMC 정본 acquisition contract — SEC 6-K primary live probe.
    #   최신 monthly-revenue 6-K를 내용으로 식별하고 Consolidated NT$ million
    #   결정표만 추출한다. 최신 식별 문서가 깨지면 과거 문서로 후퇴하지 않는다.
    #   ⛔ live network 없음 — fake SEC metadata/documents + temp artifact only.
    "test/test_tsmc_sec_monthly_probe.py",
    # ★ P4-01 — Data Coverage Matrix audit capability.
    #   Regime 15축·Discovery 11입력·Rule SSOT 25건을 전수 집계해 각 항목의
    #   source/freshness/cost/fallback 상태와 unresolved gap을 결정론적으로 남긴다.
    #   ⛔ source 선택·유료 구매·evaluator/Production/trading 연결 없음.
    "test/test_data_coverage_matrix.py",
    # ★ P3-01 — policy-neutral Global Security / Asset Master capability.
    #   US/Korea/Crypto identity, exchange, currency, alias, and effective-dated
    #   membership assertions share one schema with exact source lineage.
    #   Collisions fail closed; theme inference, universe approval, investability,
    #   Stage promotion, Production, and trading remain explicitly unauthorized.
    #   ⛔ live network/tracked master 없음 — synthetic inputs + temp output only.
    "test/test_global_asset_master.py",
    # ★ P3-02 — forward-only Nasdaq directory → Global Asset Master adapter.
    #   두 공식 source exact bytes/SHA/footer date를 검증해 모든 row를 하루짜리
    #   source-coverage membership으로 재현한다. test/ETF/financial/exchange 속성은
    #   보존만 하고 listing/liquidity/tradability/investability 판단은 하지 않는다.
    #   current→history 역적용·cross-source merge·MIC 추정·유료데이터는 금지한다.
    #   ⛔ live network/workflow/tracked master 없음 — synthetic + tracked baseline replay.
    "test/test_us_global_universe.py",
    # ★ P3-03 — exact-date KOSPI/KOSDAQ source-coverage Master adapter.
    #   두 KRX 응답 원문 SHA와 lineage를 검증해 모든 ISU_CD를 P3-01 Master의
    #   하루짜리 PIT membership으로 재현한다. 현재 catalog를 과거/미래로
    #   역적용하지 않고 liquidity/tradability/investability 정책을 발명하지 않는다.
    #   ⛔ live KRX/workflow/tracked master 없음 — exact-byte fixtures + temp output only.
    "test/test_krx_global_universe.py",
    # ★ P3-04 — ratified Crypto breadth selection → Global Asset Master adapter.
    #   exact append-only Kraken snapshot/manifest/policy/taxonomy/identity lineage와
    #   full target observation을 요구해 breadth source-coverage membership만 만든다.
    #   rank·30d turnover는 investability로 재명명하지 않고 liquidity/tradability/
    #   custody/Stage/Production/trading 권한은 모두 닫는다.
    #   ⛔ live network/workflow/tracked master 없음 — synthetic snapshot + temp output only.
    "test/test_crypto_global_universe.py",
    # ★ P2-01 — externally RATIFIED Theme / Value-Chain graph validator.
    #   repo default taxonomy 없이 effective nodes/edges와 evidence-linked US/KR
    #   memberships를 검증한다. draft는 membership 0, ratified graph만 detached
    #   Global Asset Master adapter를 만들며 inference/weight/score/Stage/trading 없음.
    #   ⛔ live network/tracked taxonomy/master mutation 없음 — temp output only.
    "test/test_theme_taxonomy.py",
    # ★ P2-02 — external RATIFIED policy-gated US Theme rotation transform.
    #   forward-PIT US Leadership 두 시점과 exact taxonomy lineage를 묶어
    #   deterministic rank·TOP/MIDDLE/BOTTOM·bucket transition만 재현한다.
    #   P2-05 state vocabulary/ledger, Regime, Stage, Production, trading은 닫는다.
    #   ⛔ vendor rows/live network/tracked factor 없음 — temp derived packets only.
    "test/test_us_capital_rotation.py",
    # ★ P2-03 — external RATIFIED policy-gated Korea Theme rotation transform.
    #   hash-bound Korea Leadership 두 시점을 own-benchmark scope별로만 rank하고
    #   KRX-only/unverified flow와 non-durable breadth는 context로 격리한다.
    #   cross-benchmark rank/P2-05 state/Regime/Stage/Production/trading 없음.
    #   ⛔ source close rows/live network/tracked factor 없음 — temp packets only.
    "test/test_korea_capital_rotation.py",
    # ★ P2-04 — external RATIFIED policy-gated BTC/ETH/ALT rotation transform.
    #   selected 7d/30d Crypto Leadership window 두 시점에서 deterministic bucket
    #   rank·TOP/MIDDLE/BOTTOM transition만 만든다. sector/chain은 UNKNOWN 유지.
    #   asset rank/P2-05 state/Regime/Stage/Production/trading 권한은 닫는다.
    #   ⛔ live network/tracked factor 없음 — upstream temp packets only.
    "test/test_crypto_rotation.py",
    # ★ P2-05 — external RATIFIED state-policy append-only rotation ledger.
    #   P2-02~04 structural bucket transition을 exact packet/policy SHA로 묶고
    #   EMERGING/STRONG/WEAKENING 매핑은 외부 승인정책이 제공할 때만 저장한다.
    #   US/Korea/Crypto scope는 독립이며 재분류·backfill·Regime/Stage 없음.
    #   ⛔ repository default policy/live network/tracked ledger 없음 — temp only.
    "test/test_rotation_state_ledger.py",
    # ★ P3-05 — published growth-rate Business Acceleration radar capability.
    #   동일 measurement/basis의 연속 3기간 evidence envelope에서 두 번 연속
    #   성장률 상승만 투명하게 기록한다. 결측은 UNKNOWN이며 source/importance/
    #   candidate ranking, Stage 승격, Production, trading 권한은 열지 않는다.
    #   ⛔ live network/workflow/tracked radar 없음 — synthetic envelopes + temp output only.
    "test/test_business_acceleration.py",
    # ★ P3-06 — external RATIFIED consensus-source contract + exact-vintage revision radar.
    #   동일 estimate target의 두 vintage를 latest-prior로 재현해 UP/DOWN/UNCHANGED/
    #   UNKNOWN을 구분한다. 비영(非零) confirmed change만 evidence case로 기록한다.
    #   ⛔ source 선택/구매/importance/ranking/Stage/Production/trading 없음.
    "test/test_expectations_revision.py",
    # ★ P3-07 — policy-gated cross-market price/volume behavior radar capability.
    #   explicit benchmark 대비 누적 상대강도와 latest/prior mean·median 거래량 비율을
    #   raw feature로 남긴다. repo 기본 임계값은 없고 외부 RATIFIED 정책이 명시한
    #   market/window/method/threshold가 모두 맞을 때만 lineage-complete case를 만든다.
    #   ranking·Stage 승격·Production·trading 권한은 열지 않는다.
    #   ⛔ live network/workflow/tracked radar 없음 — synthetic series + temp output only.
    "test/test_market_behavior.py",
    # ★ P3-08 — existing SEC D1 event → evidence-linked Discovery Case packet.
    #   ratified taxonomy 결과만 case로 기록하고 exact source-record binding의
    #   as_of/available_at/source SHA를 보존한다. 중요도·해석·Stage 승격은 금지한다.
    #   DART item/news/policy/Crypto coverage 미비를 packet에 그대로 표면화한다.
    #   ⛔ live network/workflow/tracked case 없음 — synthetic D1 + temp output only.
    "test/test_event_discovery_case.py",
    # ★ P3-09 — policy-gated market-specific supply/demand raw-feature radar.
    #   exact 3-point evidence의 prior/latest/acceleration change만 계산하고,
    #   direction·threshold·measurement가 명시된 외부 RATIFIED 정책이 있을 때만
    #   lineage-complete case를 만든다. cross-market score·ranking·Stage·trading 없음.
    #   ⛔ live network/workflow/tracked radar 없음 — synthetic series + temp output only.
    "test/test_supply_demand.py",
    # ★ P3-10 — immutable Discovery Case ref에 valuation/risk raw context를 부착한다.
    #   exact 2-point value/change와 composite source lineage만 기본 제공하고,
    #   deterioration 방향·minimum이 명시된 외부 RATIFIED 정책만 label을 허용한다.
    #   결측은 UNKNOWN/ABSENT, Crypto valuation은 UNDEFINED이며 candidate/Stage/Rule/
    #   Portfolio/Production/trading 권한은 모두 닫는다. temp output only.
    "test/test_valuation_risk_context.py",
    # ★ P3-11 — Theme taxonomy 밖 explicit nomination을 evidence-linked case로 기록한다.
    #   nomination text는 unconfirmed, linked evidence 0건이면 pending이며 case가 아니다.
    #   linked evidence가 있어도 strength/importance/candidate eligibility는 비승인이고
    #   rank·Stage·Rule·action·Production·trading은 닫힌다. temp output only.
    "test/test_wildcard_discovery.py",
    # ★ P4-02 — SEC filing primary/EX-99 content acquisition.
    #   Stage/form scope, SGML+index identity, bounded content, immutable hash,
    #   quote+offset extraction, skip/mutation/status separation을 fail-closed한다.
    #   ⛔ 테스트의 live SEC/Notion/Production/trading 없음 — fake fetcher + temp data.
    "test/test_sec_filing_content.py",
    # ★ P4-03 — OpenDART filing original-document acquisition.
    #   exact rcept_no ZIP, complete member/hash/text index, bounded archive,
    #   append-only cache, skip/mutation/status separation을 fail-closed한다.
    #   item extraction policy 미비준이므로 Evidence PENDING/Rule NONE을 고정한다.
    #   ⛔ live DART/key/Notion/Production/trading 없음 — fake fetcher + temp data.
    "test/test_dart_filing_content.py",
    # ★ P1-KR-03 operations evidence — append-only free API capture.
    #   exact-date first-seen과 complete paginated response를 분리해 보존하고
    #   Atlas 관측시각을 source available_at으로 승격하지 않는다.
    #   ⛔ live network/key 없음 — fake opener + temp evidence + workflow 계약만 검증.
    "test/test_kofia_first_seen.py",
    # ★ P1-US-04 — free forward-only US directory membership capture.
    #   Nasdaq Trader current-day files를 append-only로 누적하고 캡처 간
    #   편입·이탈만 재현한다. 과거 backfill·가격 breadth·유료 소스는 차단하며
    #   유료 전환 전 사용자 재승인 체크포인트를 기계적으로 고정한다.
    #   ⛔ live network 없음 — temp raw fixtures + workflow YAML 계약만 검증.
    "test/test_us_breadth_forward.py",
    # ★ P1-KR-07 — Korea Leadership transient index-relative contract.
    #   effective-dated KOSPI/KOSDAQ/sector/theme taxonomy와 benchmark 대비
    #   원시 상대수익률만 재현하며 ranking/Regime/Production 권한은 닫아 둔다.
    #   ⛔ live KRX 호출/tracked factor 없음 — temp policies/stdin fixtures only.
    "test/test_korea_leadership.py",
    # ★ P1-KR-06 — Korea Risk / Vol transient derived-feature contract.
    #   비준된 KRX index available_at envelope에서 RV/drawdown만 재현하며
    #   기본 source timing policy와 stress/Regime/Production 권한은 닫아 둔다.
    #   ⛔ live KRX 호출/tracked factor 없음 — temp policies/stdin fixtures only.
    "test/test_korea_risk.py",
    # ★ P1-KR-05 — KRX official stock PIT universe + raw breadth pilot.
    #   exact-date KOSPI/KOSDAQ response rows로 source-coverage universe와
    #   advance/decline/unchanged를 재현하되 raw persistence·classification·
    #   Regime/Production/trading 권한을 계속 차단한다.
    #   ⛔ live KRX 호출 없음 — fixture response + workflow contract only.
    "test/test_korea_breadth.py",
    # ★ P1-US-06 — US Leadership transient cross-sectional contract.
    #   PIT membership/taxonomy와 market-relative strength/participation을
    #   재현하되 Trend/Breadth/순위/Regime/Production 권한은 부여하지 않는다.
    #   ⛔ live Tiingo/workflow/tracked factor 없음 — temp policies/stdin fixtures only.
    "test/test_us_leadership.py",
    # ★ P1-US-05 — US Risk / Vol transient derived-feature 계약.
    #   기존 US price temporal eligibility를 재사용해 PIT/available_at을 검증하고
    #   synthetic stdin rows에서 RV/drawdown만 계산하며 vendor price 보존을 막는다.
    #   ⛔ live Tiingo/workflow/tracked factor 없음 — temp policy/in-memory fixtures only.
    "test/test_us_risk.py",
    # ★ P1-US-07 — US stress replay research-packet contract.
    #   explicit 2008 stress/recent bull·bear·sideways dates의 validated
    #   regime_output/v1 evidence를 묶되 historical PIT·threshold·weight 권한은 닫는다.
    #   ⛔ live/paid data·workflow·tracked packet 없음 — temp policy/envelopes only.
    "test/test_us_stress_replay_packet.py",
    # ★ P1-KR-04 — KRX/NXT investor-flow market coverage contract.
    #   기존 KRX 수급을 KRX_ONLY로 고정하고 NXT·한국 전체시장 확대 해석,
    #   당일 확정·관측시각 available_at 승격, 행/컬럼/venue 누락 혼동을 막는다.
    #   ⛔ 신규 API/score/workflow/tracked output 없음 — temp snapshot fixtures only.
    "test/test_korea_investor_flow.py",
    # ★ P1-KR-03 — KOFIA 투자자예탁금·신용융자 source qualification.
    #   공식 API의 operation/필드/완전 pagination을 검증한다. 공식 가이드의
    #   operation별 샘플 scale 충돌도 live full-coverage 원문으로 재현하며,
    #   historical range/available_at/API 단위가 미확정이면 권한을 fail-closed한다.
    #   ⛔ live API/key 없음 — temp fixtures + committed immutable capture read-only.
    "test/test_kofia_liquidity.py",
    # ★ P1-COM-02 — minimum coverage 미비준/증거부족 fail-closed Gate.
    #   5축이 모두 있어도 미비준 상태에서는 UNKNOWN/BLOCKED만 허용하고
    #   NEUTRAL/score/threshold/Production 승격을 차단한다.
    #   ⛔ 시장판정/네트워크/tracked output 없음 — temp gate fixtures only.
    "test/test_regime_coverage_gate.py",
    # ★ P1-COM-02 ratification — 모든 공통 축 5/5 coverage-only Gate.
    #   하나라도 UNDEFINED면 BLOCKED/UNKNOWN이고, 5/5여도 freshness와
    #   classification 정책이 별도 비준되기 전에는 시장 판정을 차단한다.
    #   ⛔ score/threshold/Production/trading 없음 — temp audit only.
    "test/test_regime_minimum_coverage.py",
    # ★ P1-COM-01 — Regime 공통 pre-score UNKNOWN output contract.
    #   5축 evidence/coverage/timestamp를 같은 schema로 고정하고 데이터 부족을
    #   NEUTRAL로 위장하지 않으며 score/threshold/Production은 차단한다.
    #   ⛔ 시장판정/네트워크/tracked output 없음 — temp envelope fixtures only.
    "test/test_regime_output_contract.py",
    # ★ P1-COM-04 — Regime pre-score deterministic replay harness.
    #   US/KR/CRYPTO의 동일 regime_output/v1 증거를 두 번 검증하고 canonical
    #   byte equality와 설명 가능 필드 보존을 확인한다. minimum coverage가
    #   미비준인 동안 UNKNOWN을 유지하며 score/hysteresis/Production을 차단한다.
    #   ⛔ live network/workflow/tracked report 없음 — in-memory envelopes only.
    "test/test_regime_replay_harness.py",
    # ★ P1-CR-05 — BTC Risk / Volatility transform + prefix replay.
    #   qualified BTC PIT close로 RV30·90일 drawdown을 재현하되 stress 임계값,
    #   Regime/Production/trading 권한은 부여하지 않고 gap은 fail-closed한다.
    #   ⛔ live Kraken 호출/tracked factor 없음 — temp snapshot fixtures only.
    "test/test_btc_risk.py",
    # ★ BTC scheduled/manual execution lineage — clone-observable telemetry.
    #   run/event/slot/runner delay와 capture/skip/failure·validation을 분리 기록해
    #   Actions REST 403이어도 예약 실행을 bot commit으로 오판하지 않게 한다.
    #   ⛔ live GitHub/Kraken 없음 — temp output root + workflow YAML only.
    "test/test_btc_scheduler_telemetry.py",
    # ★ P1-CR-04 — BTC Trend source / PIT / 200DMA transform.
    #   Kraken UTC 일봉의 마지막 미확정 row를 제외하고 exact 200일 종가만
    #   사용하며 결측·API 오류·해시/manifest 변조를 fail-closed한다.
    #   ⛔ live Kraken 호출 없음 — temp PIT fixtures + workflow YAML 계약.
    "test/test_btc_trend.py",
    # ★ P1-CR-06 — Crypto Breadth / Alt participation PIT universe 계약.
    #   날짜별 Assets·AssetPairs·OHLC snapshot과 effective-dated identity를 묶고
    #   ratified 30일 turnover Top-100·명시 taxonomy·90% coverage gate를 재현한다.
    #   ⛔ 테스트의 live Kraken/tracked factor 없음 — fake fetcher + temp fixtures only.
    "test/test_crypto_breadth.py",
    # ★ P1-CR-06/07 scheduled/manual run lineage — operations telemetry.
    #   Actions REST 없이도 run/event/slot, capture/skip/failure, Breadth와
    #   Leadership validation 결과를 clone에서 독립 판정한다.
    #   ⛔ live GitHub/Kraken 없음 — temp output root + workflow YAML only.
    "test/test_crypto_scheduler_telemetry.py",
    # ★ P1-CR-07 — Crypto Leadership dual-window PIT relative-strength 계약.
    #   CR-06 날짜별 snapshot을 재사용해 승인된 7일 pilot/30일 primary를 독립
    #   판정하고 taxonomy 부재는 sector/chain 층에만 UNKNOWN으로 격리한다.
    #   ⛔ live Kraken/workflow/tracked factor 없음 — temp policy/snapshot fixtures only.
    "test/test_crypto_leadership.py",
    # ★ P0-04 — KRX post-close observation / PM briefing freshness.
    #   morning archive/latest와 분리된 exact-date bundle, observed_unconfirmed,
    #   decision_eligible=false, partial-response incident 경계를 검증한다.
    #   ⛔ live KRX/Notion 호출 없음 — temp data root + workflow YAML 계약.
    "test/test_p004_krx_post_close.py",
    # ★ P0-04 — 18:00 KRX post-close briefing read-only consumer.
    #   valid bundle은 Observed/Unconfirmed로만 노출하고 missing/partial/tamper는
    #   값·0·NEUTRAL을 만들지 않은 UNKNOWN으로 닫으며 tracked output을 금지한다.
    #   ⛔ live KRX/GitHub/Notion 없음 — committed bundle read-only + temp fixtures.
    "test/test_p004_briefing_consumer.py",
    # ★ P0-04 scheduled/manual run lineage — operations telemetry.
    #   Actions REST 없이도 16:05/16:25/16:45 slot, runner delay, Guard,
    #   capture/skip/failure를 clone에서 구분하고 미확정 경계는 유지한다.
    #   ⛔ live GitHub/KRX 없음 — temp output root + workflow YAML only.
    "test/test_p004_scheduler_telemetry.py",
    # ★ P1-CR-03 — Stablecoin Net Issuance evidence transform.
    #   native USD-peg supply의 exact T-1/T-7 차이와 missing/revision lineage를
    #   검증하며 가격효과·Regime score·운영배선을 분리한다.
    #   ⛔ live network/tracked output 없음 — committed PIT read-only + temp fixtures.
    "test/test_stablecoin_net_issuance.py",
    # ★ P1-CR-02 — Stablecoin endpoint / revision / PIT contract.
    #   historical chart revision·reindex·backfill·append와 live snapshot 변화를
    #   분리하고 append-only provenance manifest를 검증한다.
    #   ⛔ live DefiLlama 호출 없음 — committed evidence read-only + temp fixtures.
    "test/test_stablecoin_revision_contract.py",
    # ★ Stablecoin schedule hardening — 15:20/16:20/17:20 3슬롯,
    #   capture/skip/failure + run lineage telemetry, 외부 17:25 read-only 판정.
    #   ⛔ live GitHub/DefiLlama/알림 없음 — temp roots + workflow YAML 계약만 검증.
    "test/test_stablecoin_schedule_hardening.py",
    # ★ P0-02 — Daily Collect scheduler self-observability.
    #   slot/run identity, runner-start delay, Guard result/skip을 작은 telemetry로
    #   남기며 manual/unknown schedule은 지연을 추정하지 않는다.
    #   ⛔ live GitHub/KRX 호출 없음 — temp output root에서 production helper 검증.
    "test/test_p002_scheduler_telemetry.py",
    "test/test_canonical_identity.py",
    "test/test_data_source_ambiguity.py",
    "test/test_decision_cards.py",
    "test/test_decision_normalization.py",
    "test/test_decomposition_pilot.py",
    "test/test_definition_decision.py",
    "test/test_definition_inventory.py",
    "test/test_equivalence.py",
    "test/test_full_decomposition.py",
    "test/test_merge_decision.py",
    "test/test_rule_inventory.py",
    "test/test_rules_extract.py",
    "test/test_rules_ssot.py",
    "test/test_ssot_mapping.py",
    # ★ P0-03 — briefing read model 회귀.
    #   Step 0 summary/source hash/date-basis, KRX tail-symbol exact view,
    #   bounded SEC view, truncated JSON fail-closed 계약을 검증한다.
    #   ⛔ live network 없음 — committed local data 기반.
    "test/test_briefing_inputs.py",
    # ★ P0-03 06:55 readiness gate — cached overall보다 current raw 날짜를 먼저 본다.
    #   raw가 오늘자면 stale/missing read model을 collection failure로 확대하지 않고
    #   read-model-only repair로 분리한다. truncated raw는 manual inspection으로 닫는다.
    #   ⛔ live network/workflow dispatch 없음 — temp data root에서만 검증한다.
    "test/test_briefing_readiness.py",
    # ★ P0-02 — 06:57 Recovery Action Gate timing/classification contract.
    #   06:57 전 FAIL/recovery를 차단하고 current raw→read-model 판정을 재사용해
    #   DATA READY/degraded/DATA NOT READY를 분리하며 실제 gate delay를 기록한다.
    #   ⛔ schedule/alert/workflow_dispatch/collector rerun은 실행하지 않는다.
    "test/test_collect_recovery_gate.py",
    # ★ P0-03 hardening — Daily Collect workflow repair-path 계약.
    #   Guard=fresh 는 collector만 skip하고 briefing read model은 검증/repair를 계속한다.
    #   ⛔ live network 없음 — workflow YAML 구조만 실제 파싱해 검증한다.
    "test/test_p003_workflow_contract.py",
    # ★ CIO 승인 2026-08-15 — TSMC Monthly Revenue collector pilot 회귀 추가.
    #   승인 목록은 늘어날 수 있다(테스트 삭제·누락만 FI-4 가 잡는다).
    "test/test_tsmc_monthly.py",
    # ★ CIO 승인 2026-08-15 — C4 SEC EDGAR parser 회귀 추가.
    #   ⛔ live network 호출은 넣지 않는다 — fixture 기반 결정론적 회귀만 승인 목록에 든다.
    "test/test_c4_sec_edgar.py",
    # ★ CIO 승인 2026-08-15 — RULE-0021 Azure cc 추출 회귀 추가.
    #   ⛔ live network 호출은 넣지 않는다 — fixture 기반 결정론적 회귀만 든다.
    "test/test_msft_azure_cc.py",
    # ★ CIO 판정 2026-08-16 항목 5 — fixture 슬라이서 회귀.
    #   이 회귀는 **슬라이서의 성질**(원문 부분 문자열 · 표 여닫이 균형 · fail-closed)만
    #   검증한다. 추출 계약은 검증하지 않는다 — 그것은 실제 fixture 확보 후다.
    #   ⛔ CIO 가 이 파일 자체를 아직 승인한 적은 없다. 목록에 넣지 않으면 test-set
    #      대조에서 「미승인」으로 잡히므로 숨기지 않고 등록한 뒤 보고한다.
    "test/test_capture_azure_fixture.py",
    # ★ CIO 승인 2026-08-16 — TSMC raw fixture capture 회귀.
    #   ⛔ C4 parser 를 검증하지 않는다. capture 도구의 성질만 본다.
    #   ⛔ 이 등록은 CIO 확인 대상이다 — 목록에 넣지 않으면 test-set 대조에서
    #      「미승인」으로 잡히므로 숨길 수 없다.
    "test/test_capture_tsmc_fixture.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S1 · RULE-0022 Commercial RPO observer.
    #   증명: FY26 4건 row exactly-one → GAAP raw 관측 / FY25 4건 row exactly-zero →
    #        ROW_ABSENT (D-6) / title·row·column 0건·복수건 fail-closed /
    #        observer 가 `msft_azure_cc` 를 import 하지 않는다 (RULE-0021 격리).
    #   ⛔ live network 없음 — fixture only.
    #   ⛔ normalization · store · pair · evaluator 는 검증하지 않는다 (S2 이후 Gate).
    "test/test_rule0022_commercial_rpo.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S2 · 층 ③ Normalization + Record.
    #   증명: 승인 percent 표기 → exact Decimal · sign_convention 보존 /
    #        malformed fault matrix 전건 fail-closed / raw 문면 보존 /
    #        numeric 문자열 직렬화 · float 부재 / CC·impact evidence-only /
    #        record invariant 전건 fail-closed / 층 순서(observer 는 이 층을 모른다).
    #   ⛔ live network 없음 — fixture only.
    #   ⛔ store · pair · evaluator 는 검증하지 않는다 (S3 이후 Gate).
    "test/test_observation_normalize.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S3 · 층 ④ Observation Store.
    #   증명: key = subject+measurement+period 세 축 / 첫 동작이 validate_record /
    #        D-6 경계 PRE_SERIES_BACKFILL_FORBIDDEN / IDEMPOTENT·CONFLICT·REVISION 분리 /
    #        조용한 overwrite·revision 삭제·authority 자동선택 없음 /
    #        deterministic serialization / store 가 Git·workflow·evaluator 를 모른다.
    #   ⛔ live network 없음 — fixture only.
    #   ⛔ pair · runtime state · evaluator 는 검증하지 않는다 (S4 이후 Gate).
    "test/test_observation_store.py",
    # ★ CIO 승인 2026-08-16 — Observation Layer S4A · Integration Wiring (offline).
    #   증명: observe/persist 물리적 분리(AST) / observe 는 저장소 밖으로만 emit /
    #        FY26 4건 end-to-end(draft 4 → record 4 → Store NEW 4) / 재적용 IDEMPOTENT /
    #        malformed·pre-series·conflict·revision·observe 실패 fault injection /
    #        workflow 계약 순서.
    #   ⛔ live network 없음 · dispatch 없음 — fixture only (S4B 미승인).
    "test/test_rule0022_integration.py",
    # ══════════════════════════════════════════════════════════════════
    # ★ CIO 승인 2026-08-17 — WS1~WS4 integration patch.
    #   base `bc18bb0` 에 1-WS1 → 2-WS3 → 3-WS4 → 4-WS2 순으로 적용해
    #   `1,590 checks / 0 FAIL / 0 ERROR / 0 SKIPPED` 재현을 확인한 뒤 등록한다.
    #   ⛔ 아래 3개만 신규 등록 대상이다. `test_rule0022_integration.py` 는
    #      기존 승인 테스트의 **수정**이므로 새로 등록하지 않는다.
    # ══════════════════════════════════════════════════════════════════
    # ★ WS3 — Evidence Bridge. 검증된 observation 만 Decision Layer 입구까지
    #   전달하는 **입력 자격 계약**만 검증한다.
    #   ⛔ Rule 판단 · evaluator 배선 · consumable_by_evaluator 전환은 검증하지 않는다.
    #   ⛔ live network 없음 — fixture only.
    "test/test_evidence_envelope.py",
    # ★ WS4 — Briefing Adapter. 확인된 사실과 투자판정/행동의 **분리**만 검증한다.
    #   ⛔ 브리핑 문안이나 투자 판정 내용 자체는 검증 대상이 아니다.
    #   ⛔ live network 없음 — fixture only.
    "test/test_briefing_evidence_adapter.py",
    # ★ P4-04 — 승인된 기업 IR/공식발표 두 경로만 evidence envelope 로 정규화.
    #   source hierarchy·fallback·해석·Rule·Production 권한은 만들지 않는다.
    #   ⛔ live network 없음 — TSMC/MSFT committed fixture 기반 fail-closed 회귀.
    "test/test_official_release_evidence.py",
    # ★ P5-03 — canonical Rule ↔ Evidence Envelope lineage binding.
    #   명시된 exact key만 연결하고 as_of/available_at/source/envelope hash를 보존한다.
    #   누락·모호성은 unresolved/blocked이며 Rule 결과는 항상 미생성이다.
    #   ⛔ source 선택·fallback·해석·evaluator/Production/trading 연결 없음.
    #   ⛔ live network 없음 — synthetic envelope + temp output only.
    "test/test_rule_evidence_binding.py",
    # ★ P5-04 — deterministic Rule UNKNOWN/UNDEFINED boundary evaluator.
    #   P5-03 linkage packet과 Rule SSOT exact SHA를 결합하되 P5-02 보류와
    #   consumable_by_evaluator=false를 존중해 PASS/FAIL은 절대 만들지 않는다.
    #   ⛔ evaluation spec/threshold/source selection/Production/trading 없음.
    "test/test_deterministic_rule_evaluator.py",
    # ★ P5-05 — P5-03→P5-04 negative/mutation integration matrix.
    #   evidence 결측·충돌·lineage 오염·hash drift·authority expansion을
    #   UNKNOWN/UNDEFINED 또는 hard reject로 고정하고 PASS/FAIL=0을 검증한다.
    #   ⛔ test-only — source/threshold/Production/trading 권한 없음.
    "test/test_rule_evaluator_mutation.py",
    # ★ P6-01 — Cash / Exposure Reduction independent action boundary.
    #   현금 유지와 long 노출축소를 short/hedge/inverse/order와 별도 필드로 두되,
    #   Regime·portfolio·cash policy·risk budget이 미비준인 현재는 모든 action과
    #   target을 NOT_EVALUATED/null/empty로 닫고 authority 밀반입을 거부한다.
    #   ⛔ policy/target/sizing/order/Production/trading 및 tracked output 없음.
    "test/test_cash_exposure_action.py",
    # ★ P6-02 — explicit CIO-ratified Hedge instrument eligibility registry.
    #   US/Korea index·sector 수단의 identity/effective date와 cost/tracking-error
    #   evidence를 exact hash로 검증하되 저장소 default·자동선택·sizing은 금지한다.
    #   ⛔ instrument 추천/threshold/order/Production/trading 및 tracked output 없음.
    "test/test_hedge_instrument_eligibility.py",
    # ★ P6-03 — explicit CIO-ratified Bear/Hedge risk-budget registry.
    #   portfolio/long budget exact distinct SHA와 loss/exposure/horizon/eligibility
    #   lineage를 검증하되 숫자를 발명하거나 usage·sizing·order를 만들지 않는다.
    #   ⛔ default budget/allocation/sizing/order/Production/trading 없음.
    "test/test_bear_hedge_risk_budget.py",
    # ★ P7-03 — external CIO-RATIFIED concentration/correlation guard.
    #   explicit long NAV weights, fractional theme lineage, market totals, and
    #   complete positive-correlation pair coverage are checked independently.
    #   No repository default limit/reduction/sizing/order authority is opened.
    #   ⛔ live data/tracked policy/output 없음 — synthetic packets + temp only.
    "test/test_concentration_correlation_guard.py",
    # ★ P7-04 — Regime-keyed market/theme exposure budget evaluation.
    #   Only external CIO-RATIFIED exact-scope limits can evaluate measured
    #   exposure; current Regime input remains PRE_SCORE UNKNOWN-only.
    #   No default budget/rebalance/sizing/order authority is introduced.
    #   ⛔ live data/tracked policy/output 없음 — synthetic packets + temp only.
    "test/test_market_theme_exposure_budget.py",
    # ★ P7-05 — explicit Crypto exposure/planned-loss/volatility limits.
    #   CIO-RATIFIED policy and exact Crypto universe + btc_risk/v1 lineage are
    #   required; uncalibrated Stress never becomes a Regime or trade signal.
    #   No default limit/reduction/sizing/order authority is introduced.
    #   ⛔ live data/tracked policy/output 없음 — synthetic packets + temp only.
    "test/test_crypto_exposure_limit.py",
    # ★ P7-06 — explicit planned stops bound to ratified Constitution B4/B5/B6.
    #   Each long position loss is recomputed and the simultaneous total is
    #   checked without creating an exit, size, stop order, or trading authority.
    #   ⛔ tracked Constitution remains not_ratified; synthetic external input only.
    "test/test_planned_loss_budget.py",
    # ★ P6-04 — Long FAIL ≠ Short PASS authority invariant.
    #   현재 evaluator 패킷에서는 short result를 전혀 만들지 않고, 독립 primitive는
    #   가상의 Long FAIL도 Short NOT_EVALUATED로만 닫는다. upstream authority
    #   확장·PASS/FAIL 밀반입은 fail-closed로 거부한다.
    #   ⛔ short eligibility/risk budget/order/Production/trading 권한 없음.
    "test/test_long_short_invariant.py",
    # ★ P6-05 — RISK_OFF/STRESS ≠ automatic inverse order invariant.
    #   현재 Regime UNKNOWN-only 계약을 검증하고, 독립 primitive에 미래 후보
    #   RISK_OFF/STRESS를 넣어도 inverse instrument/signal/order를 만들지 않는다.
    #   ⛔ hedge eligibility/risk budget/strategy/order/Production/trading 권한 없음.
    "test/test_regime_inverse_invariant.py",
    # ★ P7-01 — external RATIFIED Constitution B1 + explicit assignment only.
    #   candidate/holding마다 정확히 한 active bucket을 검증하며 중복·겹침·lineage
    #   충돌은 fail-closed다. repository default B1=null에서는 정상 차단된다.
    #   ⛔ bucket 발명/자동배정/limit/sizing/order/Production/trading 권한 없음.
    "test/test_bucket_membership.py",
    # ★ P7-02 — externally ratified position sizing parameters.
    #   Constitution deployment/bucket/position/evidence/loss 한도와 현금·현재노출,
    #   planned stop을 MIN formula로 연결해 maximum/target weight를 계산한다.
    #   repository default policy는 없고 blocked input은 size=0으로 닫는다.
    #   ⛔ candidate selection/ENTRY/action/order/Production/trading 권한 없음.
    "test/test_position_sizing.py",
    # ★ P7-07 — quote-currency raw exposure aggregation capability.
    #   Global Asset Master currency와 long-only position을 hash-bind해 같은 통화
    #   내부 notional만 합산한다. cross-currency total/FX conversion/limits는 null.
    #   ⛔ FX source/limit/sizing/order/Production/trading 권한 없음.
    "test/test_currency_exposure.py",
    # ★ P8-03 — READY ≠ ENTRY / Signal ≠ Order authority invariant.
    #   source-bound READY/Signal 상태를 보존하되 어떤 조합에서도 entry trigger와
    #   order intent는 null이다. 직접 translation 시도·authority drift는 거부한다.
    #   ⛔ P8-02/entry/order/sizing/Production/trading 권한 없음.
    "test/test_ready_signal_order_boundary.py",
    # ★ P8-04 — US/KR/Crypto Regime briefing header read model.
    #   세 source의 state/direction/confidence/time/coverage를 검증 후 그대로
    #   배열하되 market ranking/favorable selection/action은 항상 null이다.
    #   ⛔ score/해석/strategy/Production/trading 권한 및 live network 없음.
    "test/test_three_market_regime_header.py",
    # ★ P8-05 — Rotation ledger + SEC D1 Discovery case briefing read model.
    #   최신 state 관측과 evidence-linked case를 옮기되 importance/해석/후보
    #   승격은 만들지 않아 new/existing candidate change는 빈 배열이다.
    #   ⛔ ranking/promotion/action/Production/trading 및 live network 없음.
    "test/test_rotation_discovery_briefing.py",
    # ★ P8-06 — Action/Bear-Hedge/Portfolio briefing read model.
    #   exact P8-02/P6/P7 packet identity and SHA are presented while BUY/WATCH/
    #   REDUCE/HEDGE/EXIT/NOTHING all remain NOT_EVALUATED with action=null.
    #   Risk breaches are evidence, never implicit rebalance/exit/order authority.
    #   ⛔ live wiring/tracked output 없음 — synthetic packets + temp only.
    "test/test_action_risk_portfolio_summary.py",
    # ★ P8-02 — Unified Decision Contract.
    #   Regime→Rotation/Discovery→Rule→Portfolio 결과를 exact packet SHA로 한 daily
    #   object에 연결하고 P8-03 action boundary까지 포함한다. 결측 component는
    #   UNAVAILABLE 사유로 남기며 완전 입력이어도 action/entry/size/order는 null이다.
    #   ⛔ 해석/승격/Rule PASS·FAIL/sizing/Production/trading 및 live network 없음.
    "test/test_unified_decision_contract.py",
    # ★ P9-01 — external RATIFIED freshness policy + caller-supplied quote guard.
    #   provider timestamp/received time/observed time으로 age와 transport delay를
    #   계산하되 repository default threshold는 없다. stale은 data 소비만 차단한다.
    #   ⛔ feed 선택/ENTRY/EXIT/action/order/Production/trading 및 network 없음.
    "test/test_intraday_freshness.py",
    # ★ P9-03 — ENTRY / EXIT trigger eligibility audit.
    #   validated Unified Decision과 intraday freshness를 subject별 연결하지만
    #   READY·generic signal·fresh quote를 ENTRY/EXIT로 승격하지 않는다.
    #   모든 eligibility/trigger/action/order는 NOT_EVALUATED/null로 닫는다.
    #   ⛔ trigger policy/position state/Production/trading 및 live network 없음.
    "test/test_entry_exit_trigger_eligibility.py",
    # ★ P9-02 — external RATIFIED importance policy + normalized event detector.
    #   SEC/DART/official-news 사건을 exact source/market/event type으로만 매칭해
    #   confirmed IMPORTANT를 승격하고 available_at→detected_at 지연을 측정한다.
    #   routine/unmatched/blocked는 분리하며 repository default policy는 없다.
    #   ⛔ live adapter/notification/action/order/Production/trading 및 network 없음.
    "test/test_important_event_detector.py",
    # ★ P9-05 — external RATIFIED intraday risk escalation thresholds.
    #   drawdown/down-gap/spread/relative-volume을 exact observation에서 계산하지만
    #   ALERT는 evidence일 뿐 reduce/STOP/action/order 후보를 만들지 않는다.
    #   P9/P7 packet SHA는 lineage only이며 semantic authority가 아니다.
    #   ⛔ default threshold/live feed/notification/Production/trading 없음.
    "test/test_intraday_risk_escalation.py",
    # ★ P9-04 — duplicate Action/Order ID guard capability.
    #   same key+payload retry는 block, key/payload 또는 action/order ID 충돌은
    #   hard fail한다. novel ID는 ledger candidate에만 기록하고 실행하지 않는다.
    #   ⛔ ID 생성/order 생성/broker/Production/trading 권한 없음.
    "test/test_action_order_idempotency.py",
    # ★ P10-01 — append-only zero-capital 3-Market Shadow ledger.
    #   P8-02 exact Decision + P9-03 ENTRY/EXIT + P9-05 intraday risk를 일별
    #   hash chain으로 기록한다. 세 packet lineage mismatch, duplicate evidence
    #   conflict와 역행은 fail-closed이며 real capital/order는 영구 0이다.
    #   ⛔ 해석/성과주장/capital/action/order/Production/trading 및 live network 없음.
    "test/test_three_market_shadow_ledger.py",
    # ★ P10-02 — Atlas vs existing judgment same-period evidence alignment.
    #   P9 lineage를 포함한 Shadow v2 record·external legacy judgment·external
    #   outcome을 decision_id+market로 exact match하고 세 source를 packet에 보존한다.
    #   policy 비준 전 effectiveness/winner는 닫는다.
    #   ⛔ 성과해석/승자선정/strategy 변경/action/Production/trading 및 network 없음.
    "test/test_atlas_legacy_comparison.py",
    # ★ P10-03 — Shadow error metric aggregation.
    #   P10-02 self-validating comparison과 assessment key/window/SHA를 exact bind하고
    #   false-positive/miss/stale/silent-error 4종의 verified denominator만 집계한다.
    #   0분모는 0%가 아니라 null이며 cause/성과/strategy 변경 권한은 닫는다.
    "test/test_shadow_error_metrics.py",
    # ★ P10-04 — opaque Decision SHA change lineage capability.
    #   이전/current hash로 change type을 파생하고 변경 이유·evidence·시각과
    #   chain을 검증한다. Decision payload/interpretation/action은 항상 null이다.
    #   ⛔ Unified Decision Contract/Shadow wiring/Production/trading 없음.
    "test/test_decision_change_lineage.py",
    # ★ WS2 — rule0022-observation workflow 계약. 실제/연습 source 명시 선택 ·
    #   모순 입력 fail-closed · parameter application guard 를 **워크플로 정의
    #   자체**에 대해 검증한다.
    #   ★ PyYAML 로 워크플로를 파싱한다 (CIO 판정 2026-08-17 — YAML 계약 검증을
    #     수제 parser 나 문자열 검색으로 낮추지 않는다). CI 의존성은
    #     `requirements-ci.txt` 에 정식 선언한다.
    #   ⛔ 이 회귀는 workflow 를 **실행하지 않는다** — 정의만 읽는다. dispatch 없음.
    "test/test_rule0022_workflow_contract.py",
]

FI_SUITE = "test/test_fault_injection.py"

# ★ Production / evaluator 경계 — 이 실행으로 바뀌면 안 되는 값.
FROZEN_BOUNDARY = {
    "config/rules.json": {"authority": True, "consumable_by_evaluator": False},
    "rules/rule_inventory.json": {"authority": False,
                                  "consumable_by_evaluator": False},
}

# ══════════════════════════════════════════════════════════════════════
# ★ authoritative mode 는 **파괴적**이다 — 정상 경로에서 재빌드하므로 작업 트리의
#   committed 산출물을 덮어쓴다. disposable clean checkout(= Actions) 에서만 허용한다.
#   ⛔ 자동 restore/rollback 을 넣지 않는다 — 검증기가 작업 트리 mutation manager 가
#      되면 어느 쪽이 원본인지 판단하는 주체가 하나 더 생긴다.
#   따라서 막는 방식은 하나뿐이다: **mutation 이 일어나기 전에 fail-closed 한다.**
DISPOSABLE_ENV = "ATLAS_DISPOSABLE_CHECKOUT"


def disposable_checkout_proof():
    """authoritative mode 를 열어도 되는지. 증명하지 못하면 열지 않는다."""
    problems = []
    if os.environ.get(DISPOSABLE_ENV) != "1":
        problems.append(
            f"{DISPOSABLE_ENV}=1 이 아니다 — 이 실행 환경이 버려도 되는 checkout 이라는 "
            f"선언이 없다. Actions workflow 가 이 값을 설정한다")
    # git worktree 가 있으면 깨끗해야 한다. 더러우면 잃을 것이 있다는 뜻이다.
    try:
        r = subprocess.run(["git", "status", "--porcelain"], cwd=ROOT,
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip():
            problems.append("git worktree 가 dirty 하다 — 재빌드가 덮어쓸 변경이 있다")
    except FileNotFoundError:
        pass          # git 이 없으면 이 축으로는 판정하지 않는다
    return problems


SNAPSHOT_DIR = "_committed_snapshot"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


class Runner:
    def __init__(self):
        self.failures = []
        self.lines = []

    def fail(self, stage, msg):
        self.failures.append(f"[{stage}] {msg}")

    def say(self, msg):
        self.lines.append(msg)
        print(msg, flush=True)

    # ── ② 사본 보존 ──────────────────────────────────────────────────
    def snapshot(self, dest):
        missing = [p for p in COMPARED if not os.path.exists(os.path.join(ROOT, p))]
        if missing:
            for p in missing:
                self.fail("snapshot", f"committed 산출물이 없다: {p}")
            return {}
        kept = {}
        for p in COMPARED:
            src = os.path.join(ROOT, p)
            dst = os.path.join(dest, p.replace("/", "__"))
            shutil.copyfile(src, dst)          # byte-for-byte
            kept[p] = dst
            if not filecmp.cmp(src, dst, shallow=False):
                self.fail("snapshot", f"사본이 원본과 다르다: {p}")
        return kept

    # ── builder 직렬 실행 ────────────────────────────────────────────
    def rebuild(self):
        for i, (script, out) in enumerate(BUILDERS, 1):
            r = subprocess.run([PY, script], cwd=ROOT, capture_output=True, text=True)
            tag = f"{i:02d} {script}"
            if r.returncode != 0:
                self.fail("rebuild", f"{tag} → exit {r.returncode}\n"
                                     f"{(r.stderr or r.stdout).strip()[-600:]}")
                return False           # 순서가 의미를 가지므로 즉시 중단한다
            if not os.path.exists(os.path.join(ROOT, out)):
                self.fail("rebuild", f"{tag} → 산출물 미생성: {out}")
                return False
            self.say(f"  {tag} ok")
        return True

    # ── ② byte 비교 ─────────────────────────────────────────────────
    def compare(self, kept):
        same = 0
        for p, snap in kept.items():
            cur = os.path.join(ROOT, p)
            if not os.path.exists(cur):
                self.fail("compare", f"재빌드 산출물이 없다: {p}")
                continue
            if filecmp.cmp(snap, cur, shallow=False):
                same += 1
            else:
                self.fail("compare",
                          f"committed 와 재빌드가 다르다: {p}\n"
                          f"        committed {sha(snap)[:16]} / rebuilt {sha(cur)[:16]}")
        self.say(f"  byte-identical {same}/{len(kept)}")
        return same == len(kept)

    # ── ①③ 승인 회귀 ───────────────────────────────────────────────
    def approved_tests(self):
        actual = sorted("test/" + f for f in os.listdir(os.path.join(ROOT, "test"))
                        if f.startswith("test_") and f.endswith(".py"))
        expected = sorted(APPROVED_TESTS + [FI_SUITE])
        if actual != expected:
            self.fail("test-set",
                      f"승인 목록과 실제 test 집합이 다르다\n"
                      f"        누락 {sorted(set(expected) - set(actual))}\n"
                      f"        미승인 {sorted(set(actual) - set(expected))}")
            return False
        ok = True
        for t in APPROVED_TESTS:
            r = subprocess.run([PY, t], cwd=ROOT, capture_output=True, text=True)
            if r.returncode != 0:
                ok = False
                self.fail("regression",
                          f"{t} → exit {r.returncode}\n"
                          f"{(r.stdout or r.stderr).strip()[-600:]}")
            else:
                self.say(f"  {t} ok")
        return ok

    # ── ④ Fault Injection ───────────────────────────────────────────
    def fault_injection(self):
        r = subprocess.run([PY, FI_SUITE], cwd=ROOT, capture_output=True, text=True)
        print(r.stdout, end="", flush=True)
        if r.returncode != 0:
            self.fail("fault-injection",
                      f"{FI_SUITE} → exit {r.returncode}\n"
                      f"{(r.stdout or r.stderr).strip()[-600:]}")
            return False
        return True

    # ── Production / evaluator 경계 ─────────────────────────────────
    def boundary(self):
        import json
        ok = True
        for path, expect in FROZEN_BOUNDARY.items():
            d = json.load(open(os.path.join(ROOT, path), encoding="utf-8"))
            for k, v in expect.items():
                if d.get(k) is not v:
                    ok = False
                    self.fail("boundary", f"{path}: {k} 가 {d.get(k)!r} 다 (기대 {v!r})")
        inv = json.load(open(os.path.join(ROOT, "rules/rule_inventory.json"),
                             encoding="utf-8"))
        if inv["counts"]["evaluator_consumable"] != 0:
            ok = False
            self.fail("boundary", "Evaluator Consumable 이 0 이 아니다")
        if "HOLD" not in inv["production_state"]:
            ok = False
            self.fail("boundary", "Production HOLD 표기가 사라졌다")
        return ok


def approved_test_label():
    """Render the current approved-test population without a stale literal."""
    return f"[4/5] 승인 회귀 {len(APPROVED_TESTS)}파일"


def main():
    r = Runner()
    print("Atlas Actions runner — Python", sys.version.split()[0])
    print(f"⛔ Production HOLD · evaluator 미연결 · 이 실행은 상태를 바꾸지 않는다\n")

    authoritative = "--authoritative" in sys.argv
    with tempfile.TemporaryDirectory(prefix="atlas_committed_") as snap_dir:
        if not authoritative:
            print("[1-3/5] rebuild · byte 비교 — 건너뜀 (inspection mode)")
            print("        ★ authoritative rebuild 는 파괴적이라 disposable clean")
            print("          checkout 에서만 실행한다. `--authoritative` 로 요청하고")
            print(f"          {DISPOSABLE_ENV}=1 로 그 환경임을 선언한다.")
            r.fail("mode", "inspection mode 는 Actions PASS 조건 ② 를 검증하지 않는다")
            kept = {}
        else:
            blockers = disposable_checkout_proof()
            if blockers:
                # ★ 여기서 멈춘다 — 사본을 뜨기 전, 재빌드가 파일을 건드리기 전이다.
                for b in blockers:
                    r.fail("guard", b)
                print("[1-3/5] ⛔ authoritative rebuild 차단 — 어떤 파일도 건드리지 않았다")
                kept = {}
            else:
                print("[1/5] committed 산출물 사본 보존")
                kept = r.snapshot(snap_dir)

                if kept:
                    print("[2/5] builder ①→⑭ 직렬 재빌드")
                    r.rebuild()

                    print("[3/5] committed ↔ rebuilt byte 비교")
                    r.compare(kept)

        print(approved_test_label())
        r.approved_tests()

        # ★ `--no-fi` 는 **Fault Injection suite 전용** 스위치다. FI-1 · FI-4 는 이
        #   runner 자체를 사본에서 실행해 Gate 동작을 검증하는데, 그 사본이 다시 FI
        #   suite 를 부르면 무한 재귀가 된다. Actions 는 이 스위치 없이 실행한다.
        if "--no-fi" in sys.argv:
            print("[5/5] Fault Injection suite — 건너뜀 (--no-fi, FI 내부 실행)")
        else:
            print("[5/5] Fault Injection suite")
            r.fault_injection()

        r.boundary()

    print()
    if r.failures:
        print(f"⛔ FAIL — {len(r.failures)}건")
        for f in r.failures:
            print("  •", f)
        print("\nActions PASS = NO")
        return 1
    print("✅ Actions PASS = YES")
    print("   ⛔ 단, 이것은 CI 통과이지 Production 승인도 evaluator 승인도 아니다.")
    print("   ★ FI-3 frozen input tamper = KNOWN GAP / NOT GATED (미검증 영역)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
