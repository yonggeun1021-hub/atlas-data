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
    #   Persisted output is revalidated from retained identity/interval evidence;
    #   rehashed semantic tampering and collisions fail closed. Theme inference,
    #   universe approval, investability, Stage promotion, Production, and trading
    #   remain explicitly unauthorized.
    #   ⛔ live network/tracked master 없음 — synthetic inputs + temp output only.
    "test/test_global_asset_master.py",
    # ★ P3-02 — forward-only Nasdaq directory → Global Asset Master adapter.
    #   두 공식 source exact bytes/SHA/footer date를 검증해 모든 row를 하루짜리
    #   source-coverage membership으로 재현한다. test/ETF/financial/exchange 속성은
    #   보존만 하고 listing/liquidity/tradability/investability 판단은 하지 않는다.
    #   current→history 역적용·cross-source merge·MIC 추정·유료데이터는 금지한다.
    #   ⛔ live network/workflow/tracked master 없음 — synthetic + tracked baseline replay.
    "test/test_us_global_universe.py",
    # ★ P3-02 population wiring — P1-US-04 raw bundle → tracked source-coverage
    #   packet. 기존 bundle validator/adapter builder를 재사용하고(복사 아님)
    #   raw bundle SHA·builder version·generated_at을 기록해 append-only로
    #   commit한다. 기존 raw commit과 분리된 별도 step이라 derived 실패가
    #   raw evidence를 손상시키지 않는다. skip/repair는 provider 호출 없이
    #   커밋된 raw만으로 이뤄지고, 기존 cron/workflow_dispatch는 바뀌지 않는다.
    #   ⛔ investable/Stage/Production/trading 권한 없음 — source coverage only.
    "test/test_us_forward_universe_populate.py",
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
    # ★ P3-04 population wiring — P1-CR-06 raw Kraken snapshot → tracked
    #   source-coverage packet. 기존 build_packet()을 그대로 재사용하고(복사 아님)
    #   taxonomy/full-coverage 미달이면 임의 승격 없이 deterministic BLOCKED로
    #   기록한다(job을 실패시키지 않음). raw commit과 분리된 별도 step/commit이라
    #   population 실패나 BLOCKED가 raw evidence를 손상시키지 않는다. skip/repair는
    #   provider 호출 없이 커밋된 raw만으로 이뤄지고 기존 cron은 바뀌지 않는다.
    #   ⛔ investable/Stage/Production/trading 권한 없음 — source coverage only.
    "test/test_crypto_forward_universe_populate.py",
    # ★ P2-01 — externally RATIFIED Theme / Value-Chain graph validator.
    #   repo default taxonomy 없이 effective nodes/edges와 evidence-linked US/KR
    #   memberships를 검증한다. draft는 membership 0, ratified graph만 detached
    #   Global Asset Master adapter를 만들며 inference/weight/score/Stage/trading 없음.
    #   ⛔ live network/tracked taxonomy/master mutation 없음 — temp output only.
    "test/test_theme_taxonomy.py",
    # ★ P2-02 — external RATIFIED policy-gated US Theme rotation transform.
    #   forward-PIT US Leadership 두 시점과 exact taxonomy lineage를 묶어
    #   deterministic rank·TOP/MIDDLE/BOTTOM·bucket transition만 재현한다.
    #   output policy/rank/bucket/transition을 재검증해 self-rehash를 거부한다.
    #   P2-05 state vocabulary/ledger, Regime, Stage, Production, trading은 닫는다.
    #   ⛔ vendor rows/live network/tracked factor 없음 — temp derived packets only.
    "test/test_us_capital_rotation.py",
    # ★ P2-03 — external RATIFIED policy-gated Korea Theme rotation transform.
    #   hash-bound Korea Leadership 두 시점을 own-benchmark scope별로만 rank하고
    #   KRX-only/unverified flow와 non-durable breadth는 context로 격리한다.
    #   output scope/rank/bucket/transition을 재검증해 self-rehash를 거부한다.
    #   cross-benchmark rank/P2-05 state/Regime/Stage/Production/trading 없음.
    #   ⛔ source close rows/live network/tracked factor 없음 — temp packets only.
    "test/test_korea_capital_rotation.py",
    # ★ P2-03 -> rotation_state_ledger -> daily briefing wiring.
    #   coverage_context.breadth를 실 P3-03 lineage(또는 부재 시 UNKNOWN)로
    #   구성하고, 기존 rotation_state_ledger.apply_rotation()을 그대로 호출한
    #   뒤 committed briefing rolling-pointer만 새로 만든다. 실 BLOCKED
    #   end-to-end proof·UNKNOWN/STALE/tamper·재실행 byte-identical 포함.
    "test/test_korea_capital_rotation_ledger_wire.py",
    # ★ P2-03 실 Leadership observation_pair -> ledger/briefing e2e proof.
    #   real committed korea_leadership_context 증거로 prior/current
    #   observation을 구성하고, 실 P1-KR-07 ratified sector identity로
    #   구조는 유효하나 명시적으로 UNRATIFIED인 rotation_policy를 만들어
    #   fabrication 없이 status=POLICY_NOT_EFFECTIVE를 재현한다. 실
    #   Breadth BLOCKED + 실 Leadership 모두 briefing에 노출, 재실행
    #   byte-identical, standalone 재검증 포함.
    "test/test_korea_capital_rotation_ledger_proof.py",
    # ★ P2-03 — dependency-ordered Breadth->Leadership observation-pair
    #   workflow (2026-08-22, no new cron): structural YAML checks only --
    #   still workflow_dispatch-only, real `needs:` chain (Leadership job
    #   needs the Breadth context-commit job) that structurally guarantees
    #   Breadth's real first_seen_at predates Leadership's real
    #   available_at (decision_time), no new fetch logic/endpoint, least-
    #   privilege permissions per job.
    "test/test_p2_03_observation_pair_workflow.py",
    # ★ P1-KR-05 shared-fetch -> P2-03 committed breadth-context lineage.
    #   "recent" scope breadth packet의 payload_sha256/as_of_date/
    #   available_at만 추출해 idempotent하게 commit한다. 원시 가격·종목명
    #   없음, 재요청 없음, drift/tamper는 fail-closed.
    "test/test_korea_breadth_context_populate.py",
    # ★ P2-04 — external RATIFIED policy-gated BTC/ETH/ALT rotation transform.
    #   selected 7d/30d Crypto Leadership window 두 시점에서 deterministic bucket
    #   rank·TOP/MIDDLE/BOTTOM transition만 만든다. sector/chain은 UNKNOWN 유지.
    #   output rank/bucket/transition/UNKNOWN 경계를 재검증해 self-rehash를 거부한다.
    #   asset rank/P2-05 state/Regime/Stage/Production/trading 권한은 닫는다.
    #   ⛔ live network/tracked factor 없음 — upstream temp packets only.
    "test/test_crypto_rotation.py",
    # ★ P2-04 scheduled source-pair population. Existing CR-06 raw archive와
    #   canonical CR-07 builder로 adjacent pilot_7d observations를 만들고
    #   crypto_rotation_input/1을 content-addressed append-only로 보존한다.
    #   repository default rotation policy는 계속 ABSENT이며 ranking/P2 state/
    #   Stage/Action/Order/Production/trading 권한을 열지 않는다.
    "test/test_crypto_rotation_source_pair_populate.py",
    # ★ P2-05 — external RATIFIED state-policy append-only rotation ledger.
    #   P2-02~04 structural bucket transition을 exact packet/policy SHA로 묶고
    #   세 market production validator를 먼저 호출해 self-rehash 의미 변조를 막는다.
    #   EMERGING/STRONG/WEAKENING 매핑은 외부 승인정책이 제공할 때만 저장한다.
    #   US/Korea/Crypto scope는 독립이며 재분류·backfill·Regime/Stage 없음.
    #   ⛔ repository default policy/live network/tracked ledger 없음 — temp only.
    "test/test_rotation_state_ledger.py",
    # ★ P3-05 — published growth-rate Business Acceleration radar capability.
    #   동일 measurement/basis의 연속 3기간 evidence envelope에서 두 번 연속
    #   성장률 상승만 투명하게 기록한다. Persisted validator가 decimal 산술,
    #   pattern, case evidence, summary의 self-rehashed drift를 차단한다. 결측은
    #   UNKNOWN이며 ranking/Stage/Production/trading 권한은 열지 않는다.
    #   P4-02에 보존된 TSM SEC 월별 매출 원문 3개를 provider 재호출 없이
    #   재검증·파싱해 content-addressed append-only population packet을 만든다.
    #   현재 운영 slice는 TSM 한 종목뿐이며 importance/ranking은 계속 미비준이다.
    "test/test_business_acceleration.py",
    "test/test_business_acceleration_population.py",
    # ★ P3-06 — external RATIFIED consensus-source contract + exact-vintage revision radar.
    #   동일 estimate target의 두 vintage를 latest-prior로 재현해 UP/DOWN/UNCHANGED/
    #   UNKNOWN을 구분한다. 비영(非零) confirmed change만 evidence case로 기록한다.
    #   ⛔ source 선택/구매/importance/ranking/Stage/Production/trading 없음.
    "test/test_expectations_revision.py",
    # ★ P3-07 — policy-gated cross-market price/volume behavior radar capability.
    #   explicit benchmark 대비 누적 상대강도와 latest/prior mean·median 거래량 비율을
    #   raw feature로 남긴다. Persisted validator가 feature/source/policy/case-set의
    #   self-rehashed drift를 차단한다. repo 기본 임계값은 없고 외부 RATIFIED 정책이
    #   명시한 market/window/method/threshold가 맞을 때만 case를 만든다.
    #   ranking·Stage 승격·Production·trading 권한은 열지 않는다.
    #   ⛔ live network/workflow/tracked radar 없음 — synthetic series + temp output only.
    "test/test_market_behavior.py",
    # ★ P3-08 — existing SEC D1 event → evidence-linked Discovery Case packet.
    #   ratified taxonomy 결과만 case로 기록하고 exact source-record binding의
    #   as_of/available_at/source SHA를 보존한다. Persisted packet validator가
    #   self-rehashed case/classification/evidence/summary drift를 차단한다.
    #   중요도·해석·Stage 승격은 금지하고 coverage 미비를 그대로 표면화한다.
    #   P3-08 population은 이미 커밋된 D1+filing-content bytes만 재사용하며
    #   content-addressed append-only packet과 daily briefing 실제 소비를 검증한다.
    #   live network나 importance/promotion 정책은 추가하지 않는다.
    "test/test_event_discovery_case.py",
    "test/test_event_discovery_population.py",
    # ★ P3-09 — policy-gated market-specific supply/demand raw-feature radar.
    #   exact 3-point evidence의 prior/latest/acceleration change만 계산하고,
    #   persisted validator가 arithmetic/lineage/policy/case의 self-rehashed drift를
    #   차단한다. direction·threshold·measurement가 명시된 외부 RATIFIED 정책이
    #   있을 때만 case를 만든다. cross-market score·ranking·Stage·trading 없음.
    #   ⛔ live network/workflow/tracked radar 없음 — synthetic series + temp output only.
    "test/test_supply_demand.py",
    # ★ P3-10 — immutable Discovery Case ref에 valuation/risk raw context를 부착한다.
    #   exact 2-point value/change와 composite source lineage만 기본 제공하고,
    #   persisted validator가 grouping/change/lineage/label의 self-rehashed drift를
    #   차단한다. deterioration 방향·minimum의 외부 RATIFIED 정책만 label을 허용한다.
    #   결측은 UNKNOWN/ABSENT, Crypto valuation은 UNDEFINED이며 candidate/Stage/Rule/
    #   Portfolio/Production/trading 권한은 모두 닫는다. temp output only.
    "test/test_valuation_risk_context.py",
    # ★ P3-11 — Theme taxonomy 밖 explicit nomination을 evidence-linked case로 기록한다.
    #   nomination text는 unconfirmed, linked evidence 0건이면 pending이며 case가 아니다.
    #   persisted validator가 source/count/pending/case projection의 self-rehashed drift를
    #   차단한다. strength/importance/candidate eligibility는 비승인이고 rank·Stage·
    #   Rule·action·Production·trading은 닫힌다. temp output only.
    "test/test_wildcard_discovery.py",
    # ★ P4-02 — SEC filing primary/EX-99 content acquisition.
    #   Stage/form scope, SGML+index identity, bounded content, immutable hash,
    #   quote+offset extraction, skip/mutation/status separation을 fail-closed한다.
    #   persisted validator가 raw cache에서 extractor 결과와 authority를 재검증한다.
    #   ⛔ 테스트의 live SEC/Notion/Production/trading 없음 — fake fetcher + temp data.
    "test/test_sec_filing_content.py",
    # ★ P4-03 — OpenDART filing original-document acquisition.
    #   exact rcept_no ZIP, complete member/hash/text index, bounded archive,
    #   append-only cache, skip/mutation/status separation을 fail-closed한다.
    #   persisted validator가 retained ZIP에서 member/text index와 authority를 재검증한다.
    #   item extraction policy 미비준이므로 Evidence PENDING/Rule NONE을 고정한다.
    #   ⛔ live DART/key/Notion/Production/trading 없음 — fake fetcher + temp data.
    "test/test_dart_filing_content.py",
    # ★ P1-KR-03 operations evidence — append-only free API capture.
    #   exact-date first-seen과 complete paginated response를 분리해 보존하고
    #   Atlas 관측시각을 source available_at으로 승격하지 않는다.
    #   ⛔ live network/key 없음 — fake opener + temp evidence + workflow 계약만 검증.
    "test/test_kofia_first_seen.py",
    # ★ P1-US-04 — free forward-only US directory membership capture.
    #   provider-free skip 전 exact raw/manifest/diff bundle을 재검증한다.
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
    # ★ P1-KR-07 — Korea Leadership 최소 비준 정책 Slice. 실 89개 index
    #   catalog(2026-08-21 live run)를 근거로 48개(benchmark 2 + 공식
    #   base-market SECTOR 46) INCLUDED, 41개(200/150-family size-tier·
    #   전략형) EXCLUDED, 0개 UNKNOWN으로 완전 분할한다. 시장별 qualified
    #   identity 분리, 미래효력·중복 fail-closed, replay/mutation 포함.
    "test/test_korea_leadership_policy_minimal_slice.py",
    # ★ Korea Leadership live-fetch wiring — real R2 KRX Open API index
    #   endpoint(기존 승인, 신규 endpoint 아님) 재사용해 실 index name/close를
    #   가져와 korea_leadership.build_transform()을 그대로 시도한다. 정책이
    #   RATIFIED된 후에는 실제 build_transform()에 도달하며, raw 가격은
    #   committed 파일에 절대 남기지 않는다(name catalog + lineage SHA만).
    #   idempotent/drift fail-closed 포함.
    "test/test_korea_leadership_live_fetch.py",
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
    # ★ P1-KR-05 shared-fetch derived outputs — 같은 manual live fetch에서
    #   non-reconstructive Korea Breadth observation packet(시장×scope별,
    #   available_at=null/decision_eligible=false)과 P3-03 KOSPI/KOSDAQ
    #   source-coverage packet(krx_global_universe.build_packet() 그대로
    #   재사용)을 함께 만든다. 신규 endpoint·재요청 없음, raw body·per-symbol
    #   가격은 어느 출력에도 남지 않으며 $RUNNER_TEMP 밖에는 아무것도 쓰지 않는다.
    #   ⛔ live KRX 호출 없음 — fixture response + workflow contract only.
    "test/test_korea_breadth_derived_outputs.py",
    # ★ P1-US-06 — US Leadership transient cross-sectional contract.
    #   retained semantics를 production helper/P2-02 consumer가 재검증한다.
    #   PIT membership/taxonomy와 market-relative strength/participation을
    #   재현하되 Trend/Breadth/순위/Regime/Production 권한은 부여하지 않는다.
    #   ⛔ live Tiingo/workflow/tracked factor 없음 — temp policies/stdin fixtures only.
    "test/test_us_leadership.py",
    # ★ P1-US-05 — US Risk / Vol transient derived-feature 계약.
    #   기존 US price temporal eligibility를 재사용해 PIT/available_at을 검증하고
    #   synthetic stdin rows에서 RV/drawdown만 계산하며 vendor price 보존을 막는다.
    #   ⛔ live Tiingo/workflow/tracked factor 없음 — temp policy/in-memory fixtures only.
    "test/test_us_risk.py",
    # ★ Free provider capture — FRED VIX initial-release evidence + Alpaca
    #   Basic IEX latest bars. Raw bytes/hash are retained, while IEX remains
    #   partial-US SHADOW evidence with no breadth/entry/action/order authority.
    #   ⛔ regression uses injected bytes only; no live key/network access.
    "test/test_free_market_data.py",
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
    # ★ P3-04 — cutoff-aware Top-100 taxonomy scan audit (2026-08-22):
    #   qualified_members() already stops the instant target_asset_count
    #   eligible_crypto assets are found -- a candidate ranked below that
    #   point is never visited, proven directly (unknown-below-cutoff
    #   ignored, excluded-within-cutoff backfilled from the next rank,
    #   unknown-within-cutoff still blocks, mutation promoting a
    #   below-cutoff unknown into range flips it to blocked, deterministic
    #   tie-break). Not a scan-order defect -- see the real-evidence file
    #   below for what the real snapshot's own block cause actually is.
    "test/test_crypto_breadth_cutoff_aware_scan.py",
    # ★ P3-04 — UNVERIFIED_IDENTITY taxonomy category (policy_version v2,
    #   2026-08-22): NIGHT/RE/PLAY explicitly excluded via the same general
    #   excluded_categories mechanism as fiat/stablecoin/wrapped/staked/
    #   commodity_linked, never a 3-ticker hardcode. Real numerator/
    #   denominator against the real committed 2026-08-22 snapshot: all 88
    #   Top-100-rank-relevant unknowns resolved as of today. The full gate
    #   still honestly stays UNKNOWN, precisely because only 87 assets have
    #   ever been ratified eligible_crypto (13 short of target=100,
    #   known_eligible_count_so_far) -- a real ratification-coverage
    #   shortfall this PR does not attempt to close, never a scan-order
    #   issue. Top-100/90% thresholds unchanged.
    "test/test_crypto_breadth_unverified_identity_real_evidence.py",
    # ★ P3-04 — minimal ratified Crypto taxonomy Slice (31 native assets +
    #   EURC exclusion). 실 raw snapshot replay로 coverage 미달 시 계속
    #   blocked임을 재확인하고, 미비준 alias/unresolved ticker는 UNKNOWN을
    #   유지함을 검증한다. ⛔ investability/threshold 완화 없음.
    "test/test_crypto_taxonomy_minimal_slice.py",
    # ★ P3-04 — Crypto taxonomy Identity Slice (53개, POL/SKY/LUNA rebrand
    #   continuity + PROS/US cross-project 식별 + 48개 native asset, 모두
    #   2개 이상 독립 공식 source로 확인). NIGHT는 2번째 source에서 서로
    #   무관한 두 프로젝트의 ticker 충돌이 드러나 UNKNOWN 유지 — ticker만으로
    #   identity를 확정하지 않는다는 규칙의 실제 검증 사례. 실 raw snapshot
    #   replay로 coverage 여전히 미달·blocked 유지를 재확인한다.
    "test/test_crypto_taxonomy_identity_slice.py",
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
    # ★ P0-05B — resolve main once, then retrieve the complete read model
    #   from one immutable GitHub commit with exact blob/date/generation checks.
    "test/test_read_model_authority_retrieval.py",
    # ★ P0-06 — scheduled briefing consumer commit-pointer bootstrap.
    #   Date/slot/revision paths are append-only; actual artifacts remain pinned
    #   to one exact commit/generation and unavailable/stale reads fail closed.
    "test/test_scheduled_briefing_retrieval_authority.py",
    # ★ H-24 — exact producer locator -> deterministic read-only consumer.
    #   No directory scan/prior-date/alternate-slot fallback; slot/date/revision,
    #   index/packet/rendered hashes and authority=false are independently checked.
    "test/test_daily_briefing_delivery.py",
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
    #   persisted envelope/bundle도 source·provenance·값·기간·revision·summary·authority를
    #   재검증해 self-rehash 의미 변조를 차단한다.
    #   source hierarchy·fallback·해석·Rule·Production 권한은 만들지 않는다.
    #   ⛔ live network 없음 — TSMC/MSFT committed fixture 기반 fail-closed 회귀.
    "test/test_official_release_evidence.py",
    # ★ P5-03 — canonical Rule ↔ Evidence Envelope lineage binding.
    #   명시된 exact key만 연결하고 as_of/available_at/source/envelope hash를 보존한다.
    #   persisted packet도 Rule/reference/binding-set/summary/authority를 재파생한다.
    #   누락·모호성은 unresolved/blocked이며 Rule 결과는 항상 미생성이다.
    #   ⛔ source 선택·fallback·해석·evaluator/Production/trading 연결 없음.
    #   ⛔ live network 없음 — synthetic envelope + temp output only.
    "test/test_rule_evidence_binding.py",
    # ★ P5-04 — deterministic Rule UNKNOWN/UNDEFINED boundary evaluator.
    #   P5-03 linkage packet과 Rule SSOT exact SHA를 결합하되 P5-02 보류와
    #   consumable_by_evaluator=false를 존중해 PASS/FAIL은 절대 만들지 않는다.
    #   output row/summary를 재파생해 self-rehash semantic drift도 거부한다.
    #   ⛔ evaluation spec/threshold/source selection/Production/trading 없음.
    "test/test_deterministic_rule_evaluator.py",
    # ★ P5-02 — externally ratified complete TSM Rule result slice validator.
    #   RULE-0003~0009를 canonical condition SHA, evidence set, human evaluator,
    #   authority ref에 bind한다. PASS/FAIL을 계산하지 않고 외부 비준 결과만 검증한다.
    #   ⛔ threshold 발명/Rule 재평가/Stage/action/order/Production/trading 없음.
    "test/test_ratified_rule_decision.py",
    # ★ P5-05 — P5-03→P5-04 negative/mutation integration matrix.
    #   evidence 결측·충돌·lineage 오염·hash drift·authority expansion을
    #   UNKNOWN/UNDEFINED 또는 hard reject로 고정하고 PASS/FAIL=0을 검증한다.
    #   ⛔ test-only — source/threshold/Production/trading 권한 없음.
    "test/test_rule_evaluator_mutation.py",
    # ★ P6-01 — Cash / Exposure Reduction independent action boundary.
    #   현금 유지와 long 노출축소를 short/hedge/inverse/order와 별도 필드로 두되,
    #   Regime·portfolio·cash policy·risk budget이 미비준인 현재는 모든 action과
    #   target을 NOT_EVALUATED/null/empty로 닫고 authority 밀반입을 거부한다.
    #   standalone output validator가 self-rehash action-boundary drift도 거부한다.
    #   ⛔ policy/target/sizing/order/Production/trading 및 tracked output 없음.
    "test/test_cash_exposure_action.py",
    # ★ P6-02 — explicit CIO-ratified Hedge instrument eligibility registry.
    #   US/Korea index·sector 수단의 identity/effective date와 cost/tracking-error
    #   evidence를 exact hash로 검증하되 저장소 default·자동선택·sizing은 금지한다.
    #   active record/eligibility/summary output을 재검증해 self-rehash를 거부한다.
    #   ⛔ instrument 추천/threshold/order/Production/trading 및 tracked output 없음.
    "test/test_hedge_instrument_eligibility.py",
    # ★ P6-03 — explicit CIO-ratified Bear/Hedge risk-budget registry.
    #   portfolio/long budget exact distinct SHA와 loss/exposure/horizon/eligibility
    #   lineage를 검증하되 숫자를 발명하거나 usage·sizing·order를 만들지 않는다.
    #   active budget/summary output을 재검증해 self-rehash drift도 거부한다.
    #   ⛔ default budget/allocation/sizing/order/Production/trading 없음.
    "test/test_bear_hedge_risk_budget.py",
    # ★ P7-03 — external CIO-RATIFIED concentration/correlation guard.
    #   explicit long NAV weights, fractional theme lineage, market totals, and
    #   complete positive-correlation pair coverage are checked independently.
    #   exact input/policy packets를 내장하고 production validator로 재파생한다.
    #   No repository default limit/reduction/sizing/order authority is opened.
    #   ⛔ live data/tracked policy/output 없음 — synthetic packets + temp only.
    "test/test_concentration_correlation_guard.py",
    # ★ P7-04 — Regime-keyed market/theme exposure budget evaluation.
    #   Only external CIO-RATIFIED exact-scope limits can evaluate measured
    #   exposure; current Regime input remains PRE_SCORE UNKNOWN-only.
    #   output assessment/result/breach/summary를 재검증해 self-rehash를 거부한다.
    #   No default budget/rebalance/sizing/order authority is introduced.
    #   ⛔ live data/tracked policy/output 없음 — synthetic packets + temp only.
    "test/test_market_theme_exposure_budget.py",
    # ★ P7-05 — explicit Crypto exposure/planned-loss/volatility limits.
    #   CIO-RATIFIED policy and exact Crypto universe + btc_risk/v1 lineage are
    #   required; uncalibrated Stress never becomes a Regime or trade signal.
    #   output assessment/total/breach/summary를 재검증해 self-rehash를 거부한다.
    #   No default limit/reduction/sizing/order authority is introduced.
    #   ⛔ live data/tracked policy/output 없음 — synthetic packets + temp only.
    "test/test_crypto_exposure_limit.py",
    # ★ P7-06 — explicit planned stops bound to ratified Constitution B4/B5/B6.
    #   Each long position loss is recomputed and the simultaneous total is
    #   checked without creating an exit, size, stop order, or trading authority.
    #   exact input/Constitution을 내장하고 production validator로 재파생한다.
    #   ⛔ tracked Constitution remains not_ratified; synthetic external input only.
    "test/test_planned_loss_budget.py",
    # ★ P6-04 — Long FAIL ≠ Short PASS authority invariant.
    #   현재 evaluator 패킷에서는 short result를 전혀 만들지 않고, 독립 primitive는
    #   가상의 Long FAIL도 Short NOT_EVALUATED로만 닫는다. upstream authority
    #   확장·PASS/FAIL 밀반입은 fail-closed로 거부한다.
    #   canonical Rule identity에서 25개 output/summary를 재파생한다.
    #   ⛔ short eligibility/risk budget/order/Production/trading 권한 없음.
    "test/test_long_short_invariant.py",
    # ★ P6-05 — RISK_OFF/STRESS ≠ automatic inverse order invariant.
    #   현재 Regime UNKNOWN-only 계약을 검증하고, 독립 primitive에 미래 후보
    #   RISK_OFF/STRESS를 넣어도 inverse instrument/signal/order를 만들지 않는다.
    #   standalone output validator가 self-rehash boundary drift도 거부한다.
    #   ⛔ hedge eligibility/risk budget/strategy/order/Production/trading 권한 없음.
    "test/test_regime_inverse_invariant.py",
    # ★ P7-01 — external RATIFIED Constitution B1 + explicit assignment only.
    #   candidate/holding마다 정확히 한 active bucket을 검증하며 중복·겹침·lineage
    #   충돌은 fail-closed다. repository default B1=null에서는 정상 차단된다.
    #   embedded history에서 membership/summary를 재파생해 self-rehash도 거부한다.
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
    #   position/exposure/summary를 재파생해 self-rehash semantic drift도 거부한다.
    #   ⛔ FX source/limit/sizing/order/Production/trading 권한 없음.
    "test/test_currency_exposure.py",
    # ★ P8-03 — READY ≠ ENTRY / Signal ≠ Order authority invariant.
    #   source-bound READY/Signal 상태를 보존하되 어떤 조합에서도 entry trigger와
    #   order intent는 null이다. 직접 translation 시도·authority drift는 거부한다.
    #   output 전체를 입력 lineage에서 재파생하며 P8-02가 production validator로 호출한다.
    #   ⛔ entry/order/sizing/Production/trading 권한 없음.
    "test/test_ready_signal_order_boundary.py",
    # ★ P8-04 — US/KR/Crypto Regime briefing header read model.
    #   세 source의 state/direction/confidence/time/coverage를 검증 후 그대로
    #   배열하되 market ranking/favorable selection/action은 항상 null이다.
    #   exact Regime source packets를 내장하고 persisted header를 재파생한다.
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
    #   P6 9-source와 P7-02~P7-06 risk source를 production validator로 재검증하고
    #   exact 15-source bundle을 내장·재파생한다. Risk breach는 action이 아니다.
    #   ⛔ live wiring/tracked output 없음 — synthetic packets + temp only.
    "test/test_action_risk_portfolio_summary.py",
    # ★ P8-02 — Unified Decision Contract.
    #   Regime→Rotation/Discovery→Rule→Portfolio 결과를 exact packet SHA로 한 daily
    #   object에 연결하고 P8-03 action boundary까지 포함한다. 결측 component는
    #   UNAVAILABLE 사유로 남기며 완전 입력이어도 action/entry/size/order는 null이다.
    #   여섯 component production validator를 모두 호출해 self-rehash drift를 차단한다.
    #   ⛔ 해석/승격/Rule PASS·FAIL/sizing/Production/trading 및 live network 없음.
    "test/test_unified_decision_contract.py",
    # ★ P8-07 — Evidence → Thesis → Buy Review fail-closed TSM slice.
    #   explicit supporting/counter evidence, earnings conversion, invalidation과
    #   exact evidence-set SHA를 P5 Rule packet에 연결한다. routine UNKNOWN/
    #   UNDEFINED는 BLOCKED이며 외부 비준된 full slice만 PASS/REJECTED를 연다.
    #   PASS proposal도 zero-capital review-only이고 broker/order 권한은 없다.
    "test/test_investment_decision_review.py",
    # ★ P8-08 — Forward Thesis / Earnings Conversion evidence-assembly packet.
    #   observed facts는 evidence_lineage에 resolve되는 source_ref와 decision_date
    #   이하 as_of를 요구해 forward_inferences로부터 fact/inference를 분리한다.
    #   generated_at·evidence_lineage filing_date도 as_of_ceiling/decision_date
    #   이후를 거부한다. earnings_conversion.status 7종 폐쇄 vocabulary는 어느
    #   값도 downstream을 gate하지 않고(CONVERSION_CONFIRMED 요구 없음) 조기
    #   단계/저신뢰 thesis도 그대로 packet을 만든다. invalidation_conditions
    #   빈 배열과 출처 없는 정밀 capital_commitment 수치는 거부한다.
    #   ⛔ Stage/Rule PASS-FAIL/ticker 매핑/action/order/Production/trading 권한
    #   없음 — live network/tracked output 없음, temp packet only.
    "test/test_forward_thesis.py",
    # ★ P8 Atlas Daily Briefing Integration v1 — provider-free daily orchestrator.
    #   기존 persisted evidence/packet만 소비해 Regime→Rotation/Discovery→Rule→
    #   Portfolio/Risk→Unified Decision→Action/Risk 요약을 하나의 daily briefing
    #   packet으로 연결한다. LIVE_READY component만 실제 값을 담고, 나머지는
    #   PENDING/POLICY_BLOCKED/DATA_BLOCKED/UNAVAILABLE 사유로 남는다.
    #   morning은 confirmed history만, evening은 observed_unconfirmed KRX
    #   post-close를 포함하되 decision/action/order eligibility는 계속 false다.
    #   atomic append-only publish, self-rehash 재검증, 컴포넌트별 실패 격리,
    #   결정론적 재생성을 검증한다. ⛔ live network·provider 호출 없음.
    "test/test_daily_orchestrator.py",
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
    #   exact event/policy source를 내장·재검증하고 routine/unmatched/blocked는
    #   분리하며 repository default policy는 없다.
    #   ⛔ live adapter/notification/action/order/Production/trading 및 network 없음.
    "test/test_important_event_detector.py",
    # ★ P9-05 — external RATIFIED intraday risk escalation thresholds.
    #   drawdown/down-gap/spread/relative-volume을 exact observation에서 계산하지만
    #   ALERT는 evidence일 뿐 reduce/STOP/action/order 후보를 만들지 않는다.
    #   exact P9-03/P9-02/P7-03/P7-06 packet을 production validator로 재검증해
    #   batch SHA/시각/날짜 및 P7 cross-lineage와 bind한다.
    #   ⛔ default threshold/live feed/notification/Production/trading 없음.
    "test/test_intraday_risk_escalation.py",
    # ★ P9-04 — duplicate Action/Order ID guard capability.
    #   same key+payload retry는 block, key/payload 또는 action/order ID 충돌은
    #   hard fail한다. novel ID는 ledger candidate에만 기록하고 실행하지 않는다.
    #   exact prior ledger/attempt batch를 내장하고 persisted result를 재파생한다.
    #   ⛔ ID 생성/order 생성/broker/Production/trading 권한 없음.
    "test/test_action_order_idempotency.py",
    # ★ P10-01 — append-only zero-capital 3-Market Shadow ledger.
    #   P8-02 exact Decision + P9-03 ENTRY/EXIT + P9-05 intraday risk를 일별
    #   hash chain으로 기록한다. 세 packet lineage mismatch, duplicate evidence
    #   conflict와 역행은 fail-closed이며 real capital/order는 영구 0이다.
    #   ⛔ 해석/성과주장/capital/action/order/Production/trading 및 live network 없음.
    "test/test_three_market_shadow_ledger.py",
    # ★ P10-06 — P8-07 Investment Review append-only zero-capital ledger.
    #   PASS/REJECTED/BLOCKED packet을 exact SHA chain으로 기록하되 proposal 관측은
    #   Shadow 편입·Stage 변경·capital/action/order로 승격되지 않는다.
    "test/test_investment_review_shadow_ledger.py",
    # ★ P10-02 — Atlas vs existing judgment same-period evidence alignment.
    #   P7/P9 lineage를 포함한 Shadow v4 record·external legacy judgment·external
    #   outcome을 decision_id+market로 exact match하고 세 source를 packet에 보존한다.
    #   policy 비준 전 effectiveness/winner는 닫는다.
    #   ⛔ 성과해석/승자선정/strategy 변경/action/Production/trading 및 network 없음.
    "test/test_atlas_legacy_comparison.py",
    # ★ P10-03 — Shadow error metric aggregation.
    #   P10-02 self-validating comparison과 assessment key/window/SHA를 exact bind하고
    #   false-positive/miss/stale/silent-error 4종의 verified denominator만 집계한다.
    #   0분모는 0%가 아니라 null이며 cause/성과/strategy 변경 권한은 닫는다.
    "test/test_shadow_error_metrics.py",
    # ★ P10-04 — exact Unified Decision change lineage capability.
    #   이전/current P8-02 packet을 production validator로 재검증하고 packet
    #   SHA·생성시각·변경 이유·evidence·chain을 검증한다. 해석/action은 null이다.
    #   ⛔ live Decision/Shadow wiring/Production/trading 없음.
    "test/test_decision_change_lineage.py",
    # ★ WS2 — rule0022-observation workflow 계약. 실제/연습 source 명시 선택 ·
    #   모순 입력 fail-closed · parameter application guard 를 **워크플로 정의
    #   자체**에 대해 검증한다.
    #   ★ PyYAML 로 워크플로를 파싱한다 (CIO 판정 2026-08-17 — YAML 계약 검증을
    #     수제 parser 나 문자열 검색으로 낮추지 않는다). CI 의존성은
    #     `requirements-ci.txt` 에 정식 선언한다.
    #   ⛔ 이 회귀는 workflow 를 **실행하지 않는다** — 정의만 읽는다. dispatch 없음.
    "test/test_rule0022_workflow_contract.py",
    # ★ P8-09 — Forward Alpha MVP Expectations Gap builder (paid-feed-free proxy).
    #   guidance/backlog/capex/pricing/margin/IR-target/relative-strength/
    #   earnings-reaction 여덟 free/official proxy category와 선택적 public_estimates
    #   를 caller가 이미 분류한 direction으로만 집계한다. public_estimates 부재는
    #   packet 빌드를 절대 막지 않고 basis_type만 PROXY/UNKNOWN으로 내린다.
    #   status/magnitude/confidence는 closed enum이며 UNKNOWN status는 항상
    #   LOW confidence를 강제한다. earnings_reaction event_date는 미래 금지.
    #   ⛔ Rule/Stage/Candidate·Ready·Buy 승격/action/order/Production/trading 없음.
    "test/test_expectations_gap.py",
    # ★ P8-10 — Price Reflection builder (price/volume only, never fundamentals).
    #   price_state (pure momentum: OVEREXTENDED/STRONG_MOMENTUM/MODERATE/WEAK/
    #   UNKNOWN) is real and fully computed from caller-supplied evidence.
    #   reflection_status (UNDER/PARTIALLY/FULLY_REFLECTED/UNKNOWN) is
    #   structurally, unconditionally "UNKNOWN" in every packet this module can
    #   build or validate.
    #   ★★★ CIO final integration ruling (PR #212, 2026-08-23) — SCOPE
    #   REDUCTION. A PIT defect was found in the policy/ratification layer
    #   (decision/event_evidence.py, built across CIO rounds 5-9): ratified_at
    #   was never compared against decision_at, and ratification evidence was
    #   not verified as a genuine structured Rule Authority record. The CIO
    #   rejected further local patching and ordered decision/event_evidence.py
    #   deleted entirely, along with price_reflection.py's event_reaction/
    #   reflection_reference input parameters and every internal function that
    #   validated/classified them — reflection_status can now only ever be the
    #   literal "UNKNOWN". price_state (pure momentum) is unchanged.
    #   ★★★ CIO closing-fix ruling (same PR, same date). Direct reproduction
    #   showed the boundary wasn't fully closed: build_packet() being locked
    #   to UNKNOWN did not mean validate_packet() independently rejected a
    #   tampered/forged non-UNKNOWN packet. validate_packet() now
    #   unconditionally rejects any packet whose reflection_status != UNKNOWN
    #   (regression test uses the CIO's own tampered-packet repro case,
    #   verbatim). decision/alpha_review.py independently re-enforces the same
    #   invariant on its own inputs and its own validate_packet() (defense in
    #   depth — see that module's own note below), and
    #   WAIT_FOR_RULE_RATIFICATION was retired from alpha_review's vocabulary
    #   entirely as part of the same closing pass.
    #   Deferred, not abandoned: a future, separate PR must design a
    #   Reflection Evidence Authority jointly with Atlas P5 Rule Authority
    #   (append-only per-rule canonical records, ratified_at/effective_from,
    #   exact-content provenance, decision-time ordering, structured authority
    #   evidence) — design approved before any implementation code is
    #   written. Tracked on the existing P8-10 WBS row, no new/duplicate row.
    #   ⛔ Rule PASS/FAIL/Stage/Candidate·Ready·Buy promotion/action/order/
    #      Production/trading and live network: none.
    "test/test_price_reflection.py",
    # ★ P8-10 — real historical price + Korea KOSPI/KOSDAQ composite benchmark
    #   evidence assembly (decision/price_evidence.py). KRX 시세는
    #   replay/price_series.py + replay/evidence_index.py(PR #210, 변경 없이
    #   재사용)로 커밋된 data/<date>/krx.json 스냅샷들을 병합하고, 한국 벤치마크는
    #   data/observations/korea_leadership_context/<date>/packet.json의 실제
    #   KOSPI_BENCHMARK/KOSDAQ_BENCHMARK day-over-day cumulative_gross_return을
    #   체인링크해 조합한다 (repo에 raw 지수 시계열이 커밋된 적이 없어 이것이
    #   유일한 real, non-fabricated 벤치마크). 034020처럼 evidence가 전무한
    #   subject는 모든 필드가 정직하게 None. 새 external API 호출 없음.
    #   CIO round 2: 코드-주석 형태였던 KOREA_STOCK_MARKET_MEMBERSHIP 하드코딩을
    #   폐기하고 config/korea_market_membership.json(source/observation_date/
    #   source_sha256/approval_status 명시)으로 교체 — 전 항목 UNRATIFIED이므로
    #   vs_market은 현재 모든 한국 종목에서 정직하게 None이다.
    #   ⛔ Rule PASS/FAIL/Stage/Candidate·Ready·Buy 승격/action/order/Production/
    #      trading 및 live network 없음.
    "test/test_price_evidence.py",
    # ★ P8-10 — 위 evidence assembly의 anti-lookahead 전용 회귀
    #   (replay.lookahead_gate를 재사용해 실제 호출 여부를 확인하고, 합성
    #   fixture + 실제 커밋된 evidence 양쪽에서 decision_date 이후 캡처된
    #   스냅샷/벤치마크 세션이 절대 새어들지 않음을 검증한다).
    "test/test_price_evidence_lookahead.py",
    # ★ P8-11 — Anticipatory Alpha Review packet builder (Forward Alpha MVP, PR
    #   C stage 1). Re-validates and composes forward_thesis/expectations_gap/
    #   price_reflection (subject/decision_date cross-checked) into one
    #   ordered if/elif opportunity_state classification. p5_rule_status/
    #   portfolio_status are caller pass-through only (default NOT_EVALUATED).
    #   trade_proposal is always null in this MVP.
    #   ★★★ CIO closing-fix ruling (PR #212, 2026-08-23), same SCOPE REDUCTION
    #   as decision/price_reflection.py above. Only 4 of 10 opportunity_state
    #   vocabulary members remain reachable through a real packet: BLOCKED,
    #   REJECTED, WAIT_FOR_THESIS_REPAIR, WAIT_FOR_PRICE (unconditional
    #   fallback once gates 1-2 pass). classify_opportunity_state() no longer
    #   branches on reflection_status beyond a single non-UNKNOWN check.
    #   WAIT_FOR_RULE_RATIFICATION (contract v5, formerly reachable when
    #   reflection was judgeable but thresholds unratified) is retired from
    #   the vocabulary entirely — the ratification-authority mechanism it
    #   named never had a genuine implementation. validate_packet() now also
    #   independently rejects any embedded price_reflection.reflection_status
    #   != UNKNOWN, closing the bypass where a forged/injected packet could
    #   reach alpha_review without passing back through price_reflection's
    #   own lock. EARLY_DISCOVERY/ANTICIPATORY_REVIEW/WAIT_FOR_PULLBACK/
    #   WAIT_FOR_EVIDENCE/CONFIRMATION_REVIEW/EXPECTATION_EXHAUSTED remain
    #   legal-but-unreachable vocabulary (no contract bump needed if a future
    #   Reflection Evidence Authority reintroduces them); WAIT_FOR_RULE_
    #   RATIFICATION does not (genuine schema change, contract_version bumped
    #   alpha_review/6). Tests use forged pr dicts fed directly to
    #   classify_opportunity_state()/validate_packet() to prove the two
    #   independent locks — never claiming the real pipeline can build such
    #   packets.
    #   ⛔ Rule generation/PASS-FAIL, Portfolio decisions, Stage/Candidate/
    #      Ready/Buy promotion, action/order/Production/trading: none.
    "test/test_alpha_review.py",
    # ★ P10-07 — P8-11 Alpha Review append-only zero-capital Shadow ledger.
    #   investment_review_shadow_ledger(P10-06)와 동일한 hash-chain 패턴으로
    #   모든 opportunity_state(BLOCKED/REJECTED 포함)를 SHADOW_ENTRY_REVIEW/WAIT/
    #   REJECT로 exhaustive 매핑해 기록한다. capital은 항상 정수 0, human_approval_
    #   required는 항상 true이며 override 가능한 parameter가 존재하지 않는다.
    #   catalyst_date/hypothetical_return 등 회고평가 필드는 이번 단계에 없다.
    #   CIO closing-fix (PR #212, 2026-08-23): alpha_review.py의 opportunity_
    #   state 중 WAIT_FOR_RULE_RATIFICATION은 완전히 퇴역했고(vocabulary에서
    #   제거), 나머지 6개 상태(EARLY_DISCOVERY 등)는 legal-but-unreachable로
    #   남아 있다. 이 프로덕션 파일(shadow/alpha_shadow_ledger.py, 이번 PR에서
    #   수정 금지)의 action 매핑 테이블은 원래부터 10개 상태 전부를 exhaustive
    #   하게 다뤄서 변경이 필요 없었다 — 매핑에 없는 opportunity_state는
    #   OPPORTUNITY_STATE_UNMAPPED로 loud하게 fail-closed됨을 별도 회귀로 확인.
    #   ⛔ Shadow 편입·Stage 변경·capital/action/order/Production/trading 없음.
    "test/test_alpha_shadow_ledger.py",
    # ★ P8-11 stage 2 — real Pilot evidence intake (TSM/298040.KS/267260.KS/
    #   034020.KS). 저장소에 이미 커밋된 real evidence file만 읽어 forward_thesis/
    #   expectations_gap/price_reflection input을 조립한다. TSM의 5개 6-K 중
    #   evidence_status=OK·real extracted quote가 있는 000536만 EXHIBIT_EXTRACTED
    #   observed_fact를 뒷받침하고, 나머지 4개(EXTRACTOR_NOT_REGISTERED)는
    #   evidence_lineage로만 인용한다. Hyosung 수주잔고/가이던스는 전부
    #   NARRATIVE_SOURCED. 034020.KS는 observed_facts/evidence_lineage가 항상
    #   빈 배열이며 실제 run_all_pilots() 결과 opportunity_state=BLOCKED다.
    #   packet_sha256는 재실행해도 byte-identical하다.
    #   ⛔ tracked output 없음 — temp packet only, live network 없음.
    "test/test_pilot_evidence_intake.py",
    # ★ P8-11 stage 2 — Alpha Review 브리핑 렌더러. forward_thesis/expectations_gap/
    #   price_reflection/alpha_review/shadow_ledger_entry 필드만 그대로 옮겨 적는
    #   순수 렌더링 함수를 검증한다. 12개 절 + 확신도/부족한 데이터 2개 부록이
    #   합성 fixture와 실제 TSM packet 모두에서 전부 렌더링되는지, 확인된 사실과
    #   미래 가설이 절대 같은 절에 섞이지 않는지, 확인된 사실 각 줄이 source_class를
    #   시각적으로 노출하는지, 가격 반영 절이 data_source_scope를 노출하는지 확인한다.
    #   ⛔ 이 모듈은 아무것도 계산하지 않는다 — rendering only.
    "test/test_alpha_review_briefing.py",
    # ★ P8-11 stage 2 — compare_pilots() 실제 4-subject 비교. label은 숨은 점수가
    #   아니라 opportunity_state 기반의 읽을 수 있는 규칙으로만 결정된다는 것을
    #   compare_pilots() 내부와 독립적으로 재도출해 검증하고, 034020.KS의 label이
    #   반드시 BLOCKED인지 확인한다.
    #   ⛔ Rule/Stage/action/order/Production/trading 권한 없음.
    "test/test_pilot_comparison.py",
    # ★ P8-11 CIO Gate Hardening — real Pilot fixture-pinning. run_all_pilots()의
    #   실제 4개 subject 결과를 하드닝 이후 값으로 고정 검증한다: TSM/298040.KS는
    #   WAIT_FOR_PRICE(reflection_status=UNKNOWN 차단), 267260.KS는
    #   REJECTED(expectations_gap.status=NEGATIVE + earnings_conversion.
    #   status=UNKNOWN), 034020.KS는 그대로 BLOCKED. 4개 subject 전부
    #   shadow_proposal.action != SHADOW_ENTRY_REVIEW라는 blanket assertion과
    #   trade_proposal=None/capital=0/human_approval_required=True도 확인한다.
    #   ★★★ CIO closing-fix ruling (PR #212, 2026-08-23), same SCOPE REDUCTION
    #   as decision/price_reflection.py above. This file used to also carry a
    #   synthetic-tampered-packet test proving alpha_review.py's narrative-
    #   only-core-evidence gate (WAIT_FOR_EVIDENCE) was reachable — that
    #   underlying classification logic no longer exists (see
    #   test_alpha_review.py's own ClosingFixReducedScopeTests for the current
    #   equivalent), so that class was removed. RealPilotFixturePinningTests
    #   below is completely unaffected — it never depended on any synthetic/
    #   tampered packet, only the real pilot_evidence_intake.py pipeline.
    #   ⛔ evidence 획득/Price Reflection 자체 로직/P5 Rule Authority/Stage·
    #      Action·Order·Production·trading 권한 변경 없음 — 전부 이전과 동일하게
    #      false다.
    "test/test_pilot_gate_hardening_fixtures.py",
    # ★ P11 Opportunity Capture PIT Replay — brand-new `replay/` module, fully
    #   additive and decoupled from decision/shadow/briefing (see each test's
    #   own decoupling assertions). Covers: Opportunity Trigger Event schema
    #   (independent test suite, deliverable 8), the hard anti-lookahead gate
    #   (deliverable's explicit hard constraint), price-series PIT/integrity
    #   handling, forward return/MFE/MAE at 1/3/5/10 trading days
    #   (deliverable 4), the 7-category root-cause classifier with a
    #   structural no-survivorship-bias proof (deliverable 5), the proposed
    #   Action Conversion Gate, the existing-ruleset baseline (read-only
    #   citation of decision/alpha_review.py, never imported/executed), the
    #   proposed Opportunity Trigger Engine, the full-population universe
    #   scan, the three ledgers (deliverables 1-3) with winner/loser
    #   symmetry, and an end-to-end run against real committed repo evidence
    #   (determinism + zero authority-boolean violations + priority-subject
    #   window coverage).
    #   ⛔ capital is hard-coded 0 everywhere in this module; no Stage/Buy/
    #      Action/Order/Production/trading authority is added or altered.
    "test/test_opportunity_trigger.py",
    "test/test_pit_replay_end_to_end.py",
    "test/test_replay_action_conversion_gate.py",
    "test/test_replay_existing_ruleset_baseline.py",
    "test/test_replay_forward_metrics.py",
    "test/test_replay_ledgers.py",
    "test/test_replay_asset_identity.py",
    "test/test_replay_coverage_gap.py",
    "test/test_replay_lookahead_gate.py",
    "test/test_replay_no_survivorship_bias.py",
    "test/test_replay_opportunity_episode.py",
    "test/test_replay_price_series.py",
    "test/test_replay_root_cause_classifier.py",
    "test/test_replay_trigger_engine.py",
    "test/test_replay_universe_scan.py",
    # ★ P8-12 Dynamic Clock -- reuses PR #210's replay/ trigger detection +
    #   PIT discipline; adds the new episode/cooldown/expiry/reactivation
    #   state machine, the Human Review Candidate output contract, and the
    #   real BTC 2026-08-20 regression case. See docs/dynamic_clock_contract.md.
    "test/test_dynamic_clock_state_machine.py",
    "test/test_review_candidate_contract.py",
    "test/test_operational_scan.py",
    "test/test_dynamic_clock_end_to_end.py",
    "test/test_dynamic_clock_fail_closed.py",
    # ★ CIO review round 1 on PR #211 -- clock policy config/calendar
    #   (item 5), AUDIT_CONFIRMED_MISS registry (item 4), real workflow
    #   wiring (item 6).
    "test/test_dynamic_clock_calendar.py",
    "test/test_audit_confirmed_miss.py",
    "test/test_dynamic_clock_workflow_wiring.py",
    # ★ CIO review round 2 on PR #211 -- PIT lookahead violation fix
    # (AUDIT_CONFIRMED_MISS could no longer promote operational tier).
    "test/test_dynamic_clock_pit_tier_invariant.py",
    # ★ P8-10 <-> P8-12 real integration (post PR #212 merge, locked spec
    #   2026-08-23): clock/price_reflection_link.py's own tamper/fail-
    #   closed/idempotency/distinctness regression.
    "test/test_price_reflection_link.py",
    # ★ CIO integration review round 1 on PR #211 (4 defects reproduced
    #   independently despite CI/tests passing): decision_date-precedes-
    #   evidence lookahead via the old max() correction (defect 1, now
    #   scanner-level PIT filtering + OPERATIONAL/HISTORICAL_REPLAY fail-
    #   closed), stale-raw-trigger "new" flooding via date-equality (defect
    #   2, now committed-state episode-id diffing), post-hoc/forward-return
    #   data physically present in the operational review_queue object
    #   (defect 3, now physically separated into clock/audit_diagnostics.py,
    #   never imported by clock/review_candidate.py or the briefing path),
    #   and the missing PIT timing field/ordering contract (defect 4, now
    #   independently enforced per ordering rule). Each defect's own CIO
    #   reproduction is a dedicated regression here.
    "test/test_dynamic_clock_orchestrator_defects.py",
    # ★ P7-11 Profit Harvesting Baseline Audit -- DIAGNOSTIC MEASUREMENT
    #   ONLY, not an operational Harvest Engine, not a sell-policy
    #   ratification. Reuses PR #210's replay/ Miss/Defense episodes
    #   verbatim; adds a genuinely new, independently-written PIT-safe
    #   gain-path measurement (MFE/MAE/time-to-peak/giveback/retention/
    #   endpoint coverage) cross-validated against replay.forward_metrics.
    #   No sell threshold, quantity, Trade Proposal, or order anywhere;
    #   every scenario comparison locked UNRATIFIED/ANALYTICAL_SCENARIO_ONLY.
    #   ⛔ decision/clock/shadow/briefing/ untouched -- structurally verified
    #   never to import this package, and this package never imports them.
    "test/test_profit_harvest_gain_path.py",
    "test/test_profit_harvest_population.py",
    "test/test_profit_harvest_end_to_end.py",
    # ★ Portfolio Risk Input Contract -- READ-ONLY account-facts snapshot
    #   (Alpaca paper account/positions + manual Korea/Crypto input). NOT a
    #   sizing/policy decision: risk_policy always UNRATIFIED, position_size
    #   always NOT_COMPUTABLE_POLICY_UNRATIFIED. See
    #   docs/portfolio_risk_input_contract.md and the 13 counter-example
    #   TestCase classes in this file (future-dated snapshot, stale balance,
    #   duplicate positions, mixed-currency-without-FX, manual-disguised-as-
    #   verified, live-vs-paper confusion, negative/NaN NAV, NAV-vs-positions
    #   mismatch, partial-market-missing, same-timestamp tampering,
    #   structural order-API-impossibility, sizing-while-unratified,
    #   authority-flip).
    "test/test_portfolio_risk_input.py",
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
