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
import argparse
import hashlib
import os
import re
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
    # ★ P6-06 Defensive Action Decision readiness boundary. Existing P6
    #   guardrails are semantically revalidated while missing P1/P2 production
    #   packets remain explicit BLOCKED evidence. Missing/unevaluated input is
    #   never NO_ACTION, and no action/allocation/size/order authority is opened.
    "test/test_defensive_action_decision.py",
    "test/test_runtime_regime_integration_contract.py",
    # ★ P7-12 Strategic Capital Posture readiness boundary. P1 Regime,
    #   P2 Flow/Rotation, P6 Defensive Action, and P7 risk sources are
    #   independently revalidated before any cross-market budget could exist.
    #   Current unratified/missing inputs remain BLOCKED: all numeric budgets,
    #   allocation/action/order fields stay null/empty and execution authority
    #   remains false. Missing is never treated as zero or NO_ACTION.
    "test/test_strategic_capital_posture.py",
    # Finalization Portal/SSOT adapter: exact read-after-write verification,
    # idempotent briefing identity, and atomic append-only receipts.
    "test/test_notion_projection_adapter.py",
    # P-PORTAL/#274 producer boundary: exact-source claim ledger and ChatGPT
    # validation hashes become one immutable portal_projection/2 envelope.
    # UNKNOWN stays escalated, post-delivery needs a signed ruling and remains
    # non-redeliverable, and cross-repo dispatch separates source/envelope SHAs.
    "test/test_validated_briefing_portal_producer.py",
    # AM/PM briefing handoff watchdog: read-only CHECK -> CLASSIFY -> ALERT
    # over the existing natural/semantic/source-bridge/envelope/portal-receipt/
    # drain evidence, same run_check(slot, date) for both slots. Authors no
    # source bridge, invokes no recovery, dispatches nothing, writes no
    # Notion, and mutates no NATURAL evidence. Includes a real replay of the
    # 2026-09-02 morning and 2026-09-03 evening incidents through their exact
    # historical commits.
    "test/test_briefing_handoff_watchdog.py",
    # Finalization rev18 accepted safety contract: one delivery, signed ruling,
    # UNKNOWN escalation, post-delivery redelivery prohibition, and latest
    # receipt revision per change. Portal projection is a fail-closed gate
    # before the delivery step and adds no cron or third-party Action.
    "test/test_briefing_finalization_e2e.py",
    # Validation-first production wiring: natural producer stops at DRAFT,
    # semantic timeout is HOLD, and the activated epoch requires Portal
    # verification before final Notion projection or user delivery.
    "test/test_briefing_validation_first_wiring.py",
    # The committed CIO Ed25519 verification key must match the out-of-band
    # GitHub secret anchor before any PR can pass the authoritative CI gate.
    # Failure output is code-only and never prints the configured anchor.
    "test/test_approval_pubkey_anchor.py",
    "test/test_briefing_validator.py",
    "test/test_workflow_patch.py",
    "test/test_notion_projection_workflow.py",
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
    # ★ Session continuity contract — every new agent/session must re-read
    #   live Notion, both repository heads/open PRs, scheduled obligations,
    #   authority boundaries, and the three-layer trading-service sequence.
    #   This prevents chat memory from becoming a shadow source of truth.
    "test/test_session_bootstrap_contract.py",
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
    "test/test_global_asset_master_theme_ingestion.py",
    "test/test_global_asset_master_theme_application_cli.py",
    # ★ P3-01 committed three-market population readiness.
    #   latest US source-coverage packet is independently rebuilt from the
    #   immutable raw archive; Crypto's real coverage blocker and Korea's
    #   missing exact committed population are preserved as explicit blockers.
    #   No unratified freshness/investability/Stage/action/order authority is
    #   inferred. Rehashed readiness tampering is rejected by full re-derivation.
    "test/test_global_asset_master_population_readiness.py",
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
    # ★ P3-01/P3-03 operational Korea population.  Reuses the exact packet
    #   already built from P1-KR-05's shared KRX fetch artifact, validates the
    #   nested Global Asset Master, and persists it append-only by observation
    #   date. No second provider call, raw response, price field, investability,
    #   Stage, Production, or trading authority is introduced.
    "test/test_korea_global_universe_populate.py",
    # ★ TKT-3 (W2) — KR security <-> sector membership, security_sector_membership/1.
    #   KIS master sector codes + KIS idxcode -> the 46 ratified P2-03 sector
    #   theme_ids (binding payload sha pinned). CIO D1-D5: KIS_ONLY single-source
    #   (T2 C5 only, T3 keeps BOTH_MUST_AGREE), exactly two ratified aliases,
    #   deepest-level ACTIVE <= 1 with a derived parent_view, one-publication
    #   PENDING_CHANGE, no-code stocks UNMAPPED, no retroactive rows.
    #   ⛔ synthetic masters + temp output only; no fetch, no per-stock public output,
    #   no candidate/order/capital authority.
    "test/test_security_sector_membership.py",
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
    # ★ P3-12 — Upbit KRW public-market capture, canonical-asset<->Upbit
    #   identity proposal (PROPOSED_UNRATIFIED, never auto-applied), and
    #   TRADEABLE_UNIVERSE/PAPER_ELIGIBLE classifier. No API key/order/
    #   withdrawal endpoint is ever called; the shipped policy/taxonomy/
    #   identity registry are all unratified, so every market classifies
    #   OBSERVATION_POOL in production — expected, not a bug. Kraken
    #   breadth/leadership membership is read-only labeling only and can
    #   never change a market's state (asserted by source inspection and
    #   behavior). Deterministic: no wall-clock in the classification math.
    #   ⛔ investable/PAPER/Stage/Production/trading/order 권한 없음.
    "test/test_upbit_market_capture.py",
    # P3-12 governance-freeze remediation slice: immutable first-party
    # identity evidence for exactly the eight frozen PAPER markets. Source
    # type/domain/content hash/observed_at/available_at are bound, redirects
    # fail closed, and every identity/PAPER/exchange/order authority remains
    # false. Evidence capture only; never releases the freeze by itself.
    "test/test_upbit_first_party_identity_capture.py",
    # Exact-hash approval packet for the same bounded eight. It compares
    # hash-bound Upbit listing names to the first-party capture and proposes
    # registry/taxonomy payloads, but release_ready and every authority remain
    # false until a later CIO decision names the exact hashes.
    "test/test_upbit_paper_identity_hardening_candidate.py",
    "test/test_upbit_paper_identity_hardening_release.py",
    # ★ P3-12-GOV-05 -- runtime exact-approval binding for the identity
    #   registry/taxonomy: two independent one-way chains rooted in fields
    #   the registry/taxonomy documents carry on themselves (a pre-existing
    #   content-approval pointer, and a new code-approval pointer) --
    #   never a mutable allowlist and never a denylist of specific bad
    #   hashes. Wired into _identity_taxonomy_exact_bound_effective()/
    #   effective_identity_mapping() (a dedicated function with no boolean
    #   toggle -- _policy_approval_effective() is the separate, weaker,
    #   policy-only path a new consumer cannot accidentally reach for
    #   identity/taxonomy) so approval_status alone can never revive
    #   unapproved content. Approval/candidate ratification timestamps are
    #   validated and temporally ordered so a future approval can never
    #   retroactively apply to a past evaluation. On this branch every real
    #   committed document has no code_approval_evidence_ref at all --
    #   PENDING_EXACT_HASH_REAPPROVAL -- until a future, separate CIO
    #   decision populates it by hand via
    #   identity/upbit_exact_release_binding_release.py's deterministic
    #   projection. ⛔ authority 전부 false.
    "test/test_upbit_exact_release_binding.py",
    "test/test_upbit_exact_release_binding_release.py",
    "test/test_upbit_exact_release_binding_successor_candidate.py",
    "test/test_upbit_market_identity_proposal.py",
    "test/test_upbit_tradeable_universe.py",
    "test/test_upbit_universe_populate.py",
    "test/test_upbit_identity_governance_freeze.py",
    # ★ P4-07 -- REST-based Upbit public-market evidence & microstructure
    #   contract, extending P3-12's capture (not re-fetching what it already
    #   gets). Introduces the repo's first explicit, timeframe-generalized
    #   "is this candle finalized?" boundary primitive
    #   (microstructure/upbit_candle_finalization.py) across 15m/1h/4h/1d,
    #   plus trade-tick/orderbook microstructure evidence (spread, depth,
    #   estimated PAPER slippage -- REUSES, not duplicates,
    #   universe/upbit_tradeable_universe.py's slippage/spread formulas),
    #   an explicit FRESH/STALE/UNKNOWN freshness contract per artifact, and
    #   gap-detection/backfill for the append-only evidence history. Only
    #   exact-hash/effective-time P3 record markets at TRADEABLE_UNIVERSE/
    #   PAPER_ELIGIBLE are captured; identity-unratified historical rows are
    #   never backfilled with today's registry. A partial/replayed/tampered
    #   cohort fails closed before a provider call. REST only;
    #   real-time WebSocket ingestion is P9-06's job, not this one's. No
    #   API key/order/withdrawal/private endpoint is ever called.
    #   ⛔ decision/entry/action/order/production/trading 권한 없음 — evidence only.
    "test/test_upbit_candle_finalization.py",
    "test/test_upbit_microstructure_capture.py",
    # P4-07 orderbook second-precision regression (2026-09-14): capture v2
    #   rounds downloaded_at_utc UP to the whole second so ms-stamped orderbook
    #   rows are never "after" capture (v1 truncation -> ORDERBOOK_UNKNOWN on
    #   KRW-BTC/ETH/XRP). Replays real retained 2026-09-13 provider bytes;
    #   issued v1 packets rebuild byte-identically. Builder/policy untouched.
    "test/test_upbit_microstructure_orderbook_second_precision.py",
    # P4-07 candle-finalization lookahead fix (2026-09-14): capture v3 records
    #   each candle fetch's request/response instant; finalization is judged
    #   against the fetch request, never capture completion. Replays retained
    #   2026-09-05 bytes (KRW-BTC 15m 01:15-01:30 no longer FINALIZED), exact
    #   close boundary, mutation proofs; v1/v2 packets rebuild byte-identically
    #   and their exposure (2026-09-05, 2026-09-14 15m) is pinned, not rewritten.
    "test/test_upbit_candle_finalization_fetch_time.py",
    "test/test_upbit_market_evidence_microstructure.py",
    "test/test_upbit_p3_p4_exact_hash_consumer.py",
    # Expected governance WAIT is fail-closed and provider-call-free, but it
    # is not an Actions runtime defect. Integrity/hash errors remain fatal.
    "test/test_upbit_p4_expected_wait.py",
    # ★ P9-06 -- real-time Upbit public WebSocket layer sitting on top of
    #   P4-07's REST evidence contract. A deployment-agnostic, fully
    #   mock-tested state machine (realtime/upbit_realtime_gate.py, zero
    #   network/``websockets`` dependency) -- connection/reconnect with
    #   exponential backoff, an exact-duplicate guard, a
    #   sequential_id/timestamp out-of-order tracker, finalized-candle
    #   idempotency built directly on P4-07's
    #   ``classify_candles``/``merge_finalized_no_overwrite`` (candle.15m/
    #   60m/240m over WS -- verified live; no WS daily candle stream
    #   exists, so 1d stays REST-only), connection-outage gap windows for
    #   REST backfill, and a health/ready/metrics ``status_snapshot``.
    #   Real-time freshness evaluation literally calls (never
    #   reimplements) P9-01's ``execution/intraday_freshness.py`` and fails
    #   closed to UNKNOWN pending a human-ratified CRYPTO threshold policy
    #   -- this repo ships none by design, same as P9-01 itself. Dynamic
    #   subscription is scoped to P3-12's committed TRADEABLE_UNIVERSE/
    #   PAPER_ELIGIBLE set only -- currently empty in production since
    #   P3-12 remains unratified; an empty subscription list is a normal,
    #   successful outcome, not a bug. The bounded-run capture script
    #   (`.github/scripts/upbit_realtime_capture.py`) is this repo's cron
    #   architecture's honest fit for a WS layer -- connect, stream for a
    #   configurable bounded window (reconnecting within it), write
    #   append-only evidence, exit cleanly; a genuinely persistent daemon
    #   is a separate, later, infrastructure-track decision, not this PR's.
    #   Only Upbit's public ticker/trade/orderbook/candle.* channels are
    #   ever subscribed to -- ``myOrder``/``myAsset`` and any order/
    #   withdrawal/private REST endpoint are hard-forbidden in code
    #   (``PRIVATE_WS_TYPES_FORBIDDEN``), never merely policy-forbidden.
    #   ⛔ decision/entry/action/order/production/trading 권한 없음 — evidence only.
    "test/test_upbit_realtime_gate.py",
    # ★ Standalone 24/7 Upbit public-market realtime OBSERVATION service
    #   (services/upbit-realtime-observation/) -- new infrastructure meant to
    #   run persistently on the operator's own Ubuntu host via Docker
    #   Compose, separate from this repo's GitHub Actions automation. Never
    #   deployed by this repo's CI; this test only proves the pure state
    #   machine (observation_gate.py, no socket/websockets/asyncio import)
    #   and the local read-only HTTP API (service.py, loopback only) are
    #   correct offline. Reuses realtime/upbit_realtime_gate.py's
    #   parse_message/build_subscription_message/SequenceTracker/
    #   next_backoff_seconds unchanged; adapts (does not copy verbatim) its
    #   DuplicateGuard and ConnectionStateMachine for continuous 24/7
    #   operation instead of P9-06's ~240s bounded cron run -- see
    #   observation_gate.py's module docstring "Reuse vs. adapt" and
    #   docs/upbit_realtime_observation_service_contract.md. Only
    #   ticker/orderbook are tracked; myOrder/myAsset and every
    #   order/withdrawal/private REST endpoint are hard-forbidden (proven by
    #   source-grep tests here, mirroring this session's established
    #   pattern). Never reads/imports/writes P3-12
    #   universe/upbit_tradeable_universe.py state, never writes to
    #   evidence/ or data/ -- fixed independent observation market list only.
    #   Every authority/promotion flag hardcoded false.
    #   ⛔ decision/entry/action/order/production/trading/candidate-promotion
    #   권한 없음 -- observation only, never deployed by this PR.
    "test/test_upbit_realtime_observation_service.py",
    # P9-06 observation-only natural public transport anchors are isolated
    # from P3/P5/P8 and retain zero decision/order authority.
    "test/test_upbit_public_validation_capture.py",
    # Immutable 2026-08-29 Upbit natural public-channel sample: BTC/ETH
    # ticker/trade/orderbook/15m/1h/4h coverage, hash-bound and zero authority.
    "test/test_upbit_public_validation_natural_20260829.py",
    # P3-12 full 282-row identity proposal review bundle, rebuilt from the
    # retained Upbit market/all bytes. Review-only; broad canonical registry
    # remains absent and every ratification/promotion/order authority is false.
    "test/test_upbit_identity_review_bundle.py",
    # P3-12 pre-ratification Shadow Validation Harness: shadow-applies
    # today's PROPOSED_UNRATIFIED policy/taxonomy/identity in-memory only
    # (never mutating any canonical config file) through the real, unchanged
    # build_classification() to report the CIO funnel/manual-review/taxonomy-
    # audit evidence. Review-only; every authority field hardcoded false.
    "test/test_upbit_shadow_validation_harness.py",
    # P3-12-TAX-01 taxonomy schema & eligible-content candidate builder:
    # drafts new config/upbit_exclusion_taxonomy.json records ONLY where an
    # independently RATIFIED registry (config/crypto_breadth_exclusion_taxonomy.json)
    # already corroborates the exact canonical id, active as of
    # evaluation_as_of; never from a name pattern alone. approval_status is
    # never changed. Review-only; every authority field hardcoded false.
    "test/test_upbit_taxonomy_schema_eligible_candidate.py",
    # P3-12-ID-01 Upbit Bounded Identity Registry: ticker-match-alone is
    # never sufficient -- a market's candidate canonical id becomes a
    # VERIFIED_CANDIDATE only from curated, official-source-cited research
    # evidence (config/upbit_bounded_identity_evidence.json), high
    # name-match confidence, no found ticker collision, and no unresolved
    # rebrand history. RE forced-held regardless of evidence. Registry is
    # never ratified; every authority field hardcoded false.
    "test/test_upbit_bounded_identity_registry.py",
    # ★ P2-01 — externally RATIFIED Theme / Value-Chain graph validator.
    #   repo default taxonomy 없이 effective nodes/edges와 evidence-linked US/KR
    #   memberships를 검증한다. draft는 membership 0, ratified graph만 detached
    #   Global Asset Master adapter를 만들며 inference/weight/score/Stage/trading 없음.
    #   ⛔ live network/tracked taxonomy/master mutation 없음 — temp output only.
    "test/test_theme_taxonomy.py",
    "test/test_theme_taxonomy_authority.py",
    # ★ Closes a verification gap found 2026-09-18: the only check on
    #   config/theme_taxonomy_source_fact_registry.json's pinned
    #   first_seen_commit values anywhere in the repo was a format check
    #   (^[0-9a-f]{40}$) -- nothing confirmed the pinned commit actually
    #   exists and is reachable from HEAD. PR #809 squash-merged that same
    #   day and orphaned 7ef75f76453f2bbb90ecbb79247dc13a2e475aa6 (pinned
    #   for CRYPTO.KRAKEN.IDENTITY_EXCLUSION) for several hours; it was
    #   repaired only incidentally because PR #816 happened to land as a
    #   merge commit. This test discovers every first_seen_commit pin by
    #   walking the parsed registry (not a hardcoded list) and fails
    #   closed, naming the offending source_id/path/commit, if any pin is
    #   missing or not an ancestor of HEAD.
    #   ⛔ read-only: reads the committed registry and runs read-only git
    #      queries (rev-parse/merge-base) against this checkout's own
    #      history; no network, no mutation, no authority.
    "test/test_theme_taxonomy_source_fact_registry_provenance.py",
    # ★ P2-01 — cross-market Value-Chain EDGE authority layer (CIO 2026-09-04
    #   architecture decision). Korea/US/Crypto market-native classification
    #   families are NOT unified; this only validates a separate evidence-bound
    #   edge graph whose node references point at an already-AUTHORIZED
    #   theme_taxonomy/2 packet (reused, not forked) and whose own edges are
    #   gated by a new, empty-by-default value_chain_edge_authority_registry/1
    #   (same git-provenance/tamper/PIT mechanism as theme_taxonomy_authority,
    #   applied per edge). Fails closed to UNKNOWN whenever either endpoint
    #   lacks a ratified market-native membership; Crypto is structurally
    #   allowed but has no wired membership source in this slice, so it always
    #   stays UNKNOWN rather than being inferred. No scoring/ranking/Stage/
    #   Production/capital/order/trading authority anywhere in this module.
    #   ⛔ live network/tracked taxonomy/master mutation — temp output only.
    "test/test_value_chain_edge_authority.py",
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
    # ★ P2-03 — durable sector identity binding + rotation-policy
    #   canonicalization-only candidate lane (2026-09-12, corrected).
    #   Phase A found the only korea_capital_rotation_policy/1 anywhere is
    #   the self-ratified REAL_ROTATION_POLICY above (ratified_by=
    #   "Atlas CIO", ratified_at_utc="2026-08-22T07:19:09Z", no external
    #   ratification trail) with an honest all-zero taxonomy placeholder --
    #   CIO verdict P2_03_ROTATION_POLICY_CANONICALIZATION_REQUIRED. This
    #   lane's first attempt bound identity via a real theme_taxonomy/2
    #   graph, but that graph is evaluated per-as_of_date and could not be
    #   reused unchanged across sessions; ratifying it was also rejected
    #   (a RATIFIED graph requires non-empty edges/memberships + US+KOREA
    #   coverage -- the cross-market P2-01 contract, out of P2-03's scope,
    #   owned separately under #576). CIO correction: adds one dedicated,
    #   P2-03-owned, date-independent contract
    #   (config/korea_sector_identity_binding_contract.json) plus a small,
    #   surgical extension to korea_capital_rotation.py::_validate_binding()
    #   accepting it as a third binding version. Four real, UNRATIFIED
    #   candidate documents: a fixed positional series_identity->theme_id
    #   binding for the real 46 already-RATIFIED
    #   config/korea_leadership_policy.json SECTOR records (no as_of_date
    #   field anywhere), a taxonomy_binding candidate, and a
    #   korea_capital_rotation_policy/1 candidate (top_count=bottom_count=3,
    #   maximum_calendar_gap_days=7 per revised CIO direction). Proven
    #   durable: the identical committed binding/policy bytes validate,
    #   unchanged, against two real Day N / Day N+1 Leadership observation
    #   pairs built by the real korea_leadership.py::build_transform()
    #   against the real committed policy file -- no rebuild, no new hash.
    #   Fabricated/missing identity and upstream policy SHA drift both fail
    #   closed against real code. P2-01's real authority registry stays
    #   untouched (0 records, still #576's scope); no schedule/cron
    #   touched; no production/trading/Regime/Candidate/Stage authority
    #   opened; no full-packet automation.
    "test/test_korea_capital_rotation_policy_candidate.py",
    # ★ P2-03 — RATIFICATION MATERIALIZATION (2026-09-12), separate bounded
    #   slice from the canonicalization-only candidate lane above (PR #669,
    #   merged; that lane's own module/test stay unmodified, historical
    #   UNRATIFIED evidence). External ratification trail: PR #669 review
    #   comment issuecomment-5643258809 ("CIO RATIFICATION DECISION --
    #   P2-03 Korea Rotation Policy semantics: GO", 2026-09-12T03:47:23Z),
    #   ratifying the same 46-identity KOSPI/KOSDAQ mapping,
    #   RELATIVE_STRENGTH_VS_OWN_BENCHMARK / DESCENDING_WITHIN_BENCHMARK_
    #   SCOPE / SERIES_IDENTITY_ASC, top_count=bottom_count=3,
    #   maximum_calendar_gap_days=7. ratified_by="Atlas CIO",
    #   ratified_at_utc="2026-09-12T03:47:23Z" (the real decision instant,
    #   never the tainted 2026-08-22T07:19:09Z timestamp or either
    #   candidate-authoring placeholder). effective_from="2026-09-14" is
    #   mechanically resolved -- never guessed from weekday arithmetic --
    #   from the real, committed official KRX holiday capture
    #   (evidence/market_calendar/krx_global_holiday/2026-09-09/
    #   capture-2026.json): 2026-09-12/13 are a real Sat/Sun, so 2026-09-14
    #   is the first verified trading day after ratification. Because
    #   effective_from postdates ratified_at_utc, korea_capital_rotation.py's
    #   own anti-lookahead invariant makes it structurally impossible for
    #   any pre-existing evidence to satisfy covers_both -- proven directly,
    #   plus a regression that the artifact stays honestly inert for any
    #   pre-effective_from pair (ratification alone is not a natural proof)
    #   and only activates for a structurally in-interval pair (a mechanism
    #   proof, not a claim that a real natural sample exists yet). Real P2-01
    #   authority registry stays untouched (0 records); no new schedule/cron
    #   is introduced; no Regime/Candidate/Stage/briefing/Production/
    #   trading/order/capital authority opened; Phase B stays closed until a
    #   real post-ratification natural observation pair is verified.
    "test/test_korea_capital_rotation_policy_ratified.py",
    # ★ P2-03 — opt-in current-ratified natural-proof adapter. Re-derives the
    #   four committed ratification artifacts, emits only to an explicit
    #   external path, preserves the historical default proof path, and does
    #   not write a rolling pointer or invoke the P2-05 state ledger.
    "test/test_korea_capital_rotation_current_ratified_proof.py",
    # Stage1 display-only context never substitutes for a P2-03 packet/4.
    "test/test_korea_capital_rotation_paper_consumption.py",
    # ★ P2-03 — dependency-ordered Breadth->Leadership observation-pair
    #   workflow (2026-08-22, no new cron): structural YAML checks only --
    #   manual and reusable entrypoints share exact inputs, real `needs:` chain (Leadership job
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
    # ★ P8-04/P1-KR-05 exact aggregate evidence retention. The four KOSPI/
    #   KOSDAQ historical/recent aggregate packets are byte-retained with
    #   workflow run, source commit, immutable artifact, file, and packet
    #   hashes. Both live capture workflows publish append-only bundles.
    #   Raw KRX bodies and per-symbol rows remain absent from the public repo.
    "test/test_korea_breadth_aggregate_retention.py",
    # ★ P8-04 sanitized private replay attestation. Exact private run/commit/
    #   manifest and public bundle lineage prove MATCHED replay without
    #   republishing raw bodies, rows, or response hashes. Scoring remains
    #   unratified, so KR/BREADTH stays UNDEFINED and every authority false.
    "test/test_korea_breadth_replay_attestation.py",
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
    "test/test_rotation_state_ledger_operational_readiness.py",
    # ★ P2-05 — CIO-ratified per-market rotation_state_policy/1 identity/
    #   evidence (2026-09-11). Not a repository default state policy --
    #   nothing here is auto-loaded by rotation_state_ledger.py, which stays
    #   unchanged. build_policy() only binds the CIO-ratified 9-cell mapping/
    #   semantics/gap to a caller-supplied, already-real upstream contract
    #   version + rotation-policy SHA; it exposes no override parameter, so
    #   it cannot reproduce the PR #348 self-ratification-bypass shape. Korea's
    #   maximum_ledger_gap_days=7 is independently recomputed here from the
    #   canonical KRX holiday capture, not just asserted. Building a policy
    #   is not an append: state_ledger_authorized/p2_state_vocabulary_
    #   authorized stay false until a real natural record exists, and
    #   Regime/Candidate/Stage/briefing/Production/trading authority stay
    #   false regardless. No schedule/cron touched.
    "test/test_rotation_state_policy_ratification.py",
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
    # P3-08 DART official-filing observations remain evidence-only: exact
    # metadata/content bytes are checked but no event type or importance is
    # inferred and no downstream authority is opened.
    "test/test_dart_event_observation.py",
    "test/test_dart_structural_content_index.py",
    # ★ P9-02 source adapter — the exact published P3-08 packet becomes a
    #   normalized observation batch.  Current SEC event time is DATE_ONLY, so
    #   every observation is BLOCKED; no importance/policy/notification/action.
    "test/test_important_event_observation_population.py",
    # ★ P3-09 — policy-gated market-specific supply/demand raw-feature radar.
    #   exact 3-point evidence의 prior/latest/acceleration change만 계산하고,
    #   persisted validator가 arithmetic/lineage/policy/case의 self-rehashed drift를
    #   차단한다. direction·threshold·measurement가 명시된 외부 RATIFIED 정책이
    #   있을 때만 case를 만든다. cross-market score·ranking·Stage·trading 없음.
    #   ⛔ live network/workflow/tracked radar 없음 — synthetic series + temp output only.
    "test/test_supply_demand.py",
    # ★ P3-09 operational Crypto population — the scheduled append-only
    #   DefiLlama PIT capture is transformed into one immutable three-point
    #   aggregate native USD-pegged supply packet.  Exact missing dates remain
    #   UNKNOWN; no direction, threshold, case, rank, Stage, or trading action.
    "test/test_stablecoin_supply_demand_population.py",
    # ★ P3-10 — immutable Discovery Case ref에 valuation/risk raw context를 부착한다.
    #   exact 2-point value/change와 composite source lineage만 기본 제공하고,
    #   persisted validator가 grouping/change/lineage/label의 self-rehashed drift를
    #   차단한다. deterioration 방향·minimum의 외부 RATIFIED 정책만 label을 허용한다.
    #   결측은 UNKNOWN/ABSENT, Crypto valuation은 UNDEFINED이며 candidate/Stage/Rule/
    #   Portfolio/Production/trading 권한은 모두 닫는다. temp output only.
    "test/test_valuation_risk_context.py",
    # ★ P3-10 operational Crypto risk-source population — the scheduled
    #   immutable Kraken BTC snapshot is replayed into three detached exact
    #   two-point risk contexts.  A real allowed Discovery Case is mandatory
    #   before binding; no candidate, interpretation, Stage, Rule, or trading.
    "test/test_p3_10_crypto_risk_population.py",
    # ★ P3-11 — Theme taxonomy 밖 explicit nomination을 evidence-linked case로 기록한다.
    #   nomination text는 unconfirmed, linked evidence 0건이면 pending이며 case가 아니다.
    #   persisted validator가 source/count/pending/case projection의 self-rehashed drift를
    #   차단한다. strength/importance/candidate eligibility는 비승인이고 rank·Stage·
    #   Rule·action·Production·trading은 닫힌다. temp output only.
    "test/test_wildcard_discovery.py",
    # ★ P3-11 operational intake — reviewed committed submission + exact
    #   primary-source body bytes/git first-seen을 검증해 content-addressed
    #   append-only envelope만 게시한다. ⛔ provider/ranking/Stage/Action/trading 없음.
    "test/test_wildcard_operational_intake.py",
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
    # ★ P1-KR-03 provider-free release-timing observation.
    #   committed first-seen raw history를 기존 replay validator로 전량 재검증하고
    #   exact row hash의 마지막 부재 probe→최초 present probe 관측창만 계산한다.
    #   available_at/unit/release policy는 계속 UNRATIFIED/NOT ELIGIBLE이며 raw
    #   evidence commit과 별도 commit이라 derived 실패가 source capture를 버리지 않는다.
    #   ⛔ 신규 provider/cron/Regime/Production/trading 권한 없음.
    "test/test_kofia_release_timing.py",
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
    # ★ P2-03 — 2026-09-04 CIO-approved bounded cadence slice. Korea
    #   Leadership Live Proof 워크플로에 실 weekday schedule(18:10/18:25
    #   KST, korea-market-signals.yml의 기존 저녁 캐던스 재사용)이 생겼는지,
    #   scheduled 실행이 korea_market_signals.py의 discover_session_pair()
    #   (미변경)로 거래일을 스스로 발견하는지, manual workflow_dispatch
    #   입력 경로가 그대로인지, provider 재호출 전에 기존
    #   --verify-existing-only 경로를 재사용하는지를 오프라인 YAML 구조
    #   검증만으로 확인한다. ⛔ live KRX 호출 없음 — YAML 파싱/문자열 검증뿐.
    "test/test_korea_leadership_live_proof_workflow.py",
    # ★ P2-03 automatic pair controller — policy effectivity, missing-input
    #   waiting, Breadth-before-Leadership chronology, exact-request active
    #   dedupe, and final artifact/source/policy revalidation. A green run
    #   without the exact final artifact never suppresses a recovery call.
    "test/test_korea_observation_pair_controller.py",
    # ★ P1-KR-06 — Korea Risk / Vol transient derived-feature contract.
    #   비준된 KRX index available_at envelope에서 RV/drawdown만 재현하며
    #   기본 source timing policy와 stress/Regime/Production 권한은 닫아 둔다.
    #   ⛔ live KRX 호출/tracked factor 없음 — temp policies/stdin fixtures only.
    "test/test_korea_risk.py",
    # P1-KR-06 — CIO approved 2026-08-26 next-session observed availability.
    # Never infer KRX publication time. Persist only the first successful Atlas
    # observation time for an exact post-session KOSPI response. Index levels,
    # raw rows, risk features, Regime, Production, and trading authority remain
    # blocked. Offline fixtures and workflow-contract checks only.
    "test/test_korea_risk_availability.py",
    # ★ P1-KR-05 — KRX official stock PIT universe + raw breadth pilot.
    #   exact-date KOSPI/KOSDAQ response rows로 source-coverage universe와
    #   advance/decline/unchanged를 재현하되 raw persistence·classification·
    #   Regime/Production/trading 권한을 계속 차단한다.
    #   ⛔ live KRX 호출 없음 — fixture response + workflow contract only.
    "test/test_korea_breadth.py",
    # ★ P1-COM-05 Korea five-axis official-KRX observation. One append-only
    #   aggregate packet binds Trend/Breadth/Risk/Liquidity/Leadership while
    #   retaining no raw response or per-symbol row and opening no Regime,
    #   Stage, Buy, Action, Order, Production, or trading authority.
    "test/test_korea_market_signals.py",
    "test/test_korea_market_signals_pykrx_candidate.py",
    "test/test_krx_information_system_capture.py",
    # ★ Korea 5/5 observation → staged-symbol review bridge. Confirmed KRX
    #   price/SMA20/investor flow is joined to 012450/298040/329180 while the
    #   final market policy and every entry/exit/order authority remain closed.
    #   ⛔ committed public inputs only; no live provider, account or broker call.
    "test/test_korea_symbol_market_review.py",
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
    # ★ PAPER 12-6 정확한 commit f4e1d955 — US market-judgement fail-closed
    #   receipt 회귀. 자연 증거나 policy를 임의 생성하지 않고, 합성
    #   contract input도 repository의 UNRATIFIED/0-of-5 경계를 넘을 수 없다.
    #   ⛔ network/broker/credential/order 없음 — temp files + committed contracts only.
    "test/test_us_market_judgement.py",
    # ★ P1-US-05 — US Risk / Vol transient derived-feature 계약.
    #   기존 US price temporal eligibility를 재사용해 PIT/available_at을 검증하고
    #   synthetic stdin rows에서 RV/drawdown만 계산하며 vendor price 보존을 막는다.
    #   ⛔ live Tiingo/workflow/tracked factor 없음 — temp policy/in-memory fixtures only.
    "test/test_us_risk.py",
    # ★ Free provider capture — FRED VIX append-only public raw evidence +
    #   Alpaca Basic IEX bars. IEX remains partial-US SHADOW evidence with no
    #   breadth/entry/action/order authority.
    #   ⛔ regression uses injected bytes only; no live key/network access.
    "test/test_free_market_data.py",
    # ★ US capture publication reliability (ratified 2026-09-18). The commit
    #   step must publish through the shared bounded push-retry helper, never a
    #   bare `git push` -- runs 34911129881/35163739007/35287712594 captured US
    #   evidence and then lost it to "! [rejected] main -> main (fetch first)".
    #   The retry is exercised against real local clones racing on one bare
    #   origin: a rejected push is replayed, and a persistent failure or a
    #   rebase conflict still fails and publishes nothing. Also pins the
    #   ratified fingerprint record to the workflow's real bytes and asserts
    #   the sha256-pinned source-owner registry file was NOT edited.
    #   ⛔ offline only — temp git repos, no network, no key, no cron change.
    "test/test_free_market_data_push_retry.py",
    # ★ P1-US current evidence → pipeline-symbol review bridge. Committed
    #   SPY/QQQ/IWM, VIX, liquidity and per-symbol daily bars are connected
    #   to TSM/SNDK entry/holding/exit review contexts. Missing Breadth,
    #   Leadership, price or account-position context stays explicit; no
    #   Regime, entry, exit, order, broker, Production or Trading authority.
    #   ⛔ current committed inputs only; no live network or order endpoint.
    "test/test_us_symbol_market_review.py",
    # ★ Per-symbol row extraction from the KR/US symbol reviews. The bounded
    #   3/2-subject packets stay byte-identical; the extracted builders report
    #   missing SMA20 / flows / prices / stage tags explicitly (never estimated).
    "test/test_symbol_review_row_extraction.py",
    # ★ KR/US full-population symbol observation packet. Every population
    #   symbol appears once with data-observed / evaluable / evaluated /
    #   formal-candidate axes; bounded rows are copied, missing inputs stay
    #   NOT_EVALUABLE with reasons, generation-id idempotency and chunked
    #   resume reproduce the same bytes, fresh-process reverify passes.
    #   ⛔ no stage change, no promotion, no threshold, no network, no order.
    "test/test_population_symbol_observation.py",
    # ★ Daily scheduled run for the two population observations (2026-09-18).
    #   Both producers had NO .github/workflows trigger at all, so KR sat at
    #   2026-09-10 and US at 2026-09-11 while the committed universes they
    #   consume had already published through 2026-09-16. Asserts the schedule
    #   and its backup slot, that a dispatched run is guard-equivalent to a
    #   scheduled one (no inputs, no github.event_name branch) so the server
    #   dispatcher may be registered, and that a repeat run for an
    #   already-captured date reports verified_existing instead of letting
    #   persist_packet supersede committed bytes.
    #   ⛔ observation only; no pass rule (passed_count stays 0), no authority,
    #      no network, no new collection target or source.
    "test/test_population_observation_daily_schedule.py",
    # ★ Three-market evaluation-coverage receipt (stacked from PR #680/#682,
    #   unchanged). Exact KR/US source-coverage universes and bounded symbol
    #   reviews are kept separate; the Crypto PAPER funnel contributes only
    #   its source-native counts. Missing population totals stay 미집계.
    #   ⛔ read-only; no scanner/ranking/policy/promotion/order authority.
    "test/test_three_market_evaluation_coverage.py",
    # ★ Per-market candidate discovery status + per-symbol evidence lookup.
    #   Reuses the coverage receipt, KR/US symbol reviews, Crypto decision
    #   snapshot and candidate detail view; reconciles population → data
    #   acquired → evaluated → passed/held/excluded/unevaluated per market,
    #   classifies gaps (collection / stale-by-source-interval / not
    #   implemented / policy 미정 / criteria unknown), keeps missing evidence
    #   as NO_EVIDENCE (never 0) and preserves every source's own date.
    #   ⛔ read-only; no candidate rule, threshold, ranking, or authority.
    "test/test_market_candidate_discovery_lookup.py",
    # ★ FRED VIX append-only provenance — content-and-capture addressed raw
    #   revisions are independently decompressed/re-derived and cannot be
    #   overwritten, backdated, path-substituted, or re-signed after tamper.
    #   ⛔ observation evidence only; no Regime interpretation/trading authority.
    "test/test_fred_vix_provenance.py",
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
    # ★ P1-COM-05 — Regime Decision Authority fail-closed boundary.
    #   regime_output/v1과 재산출된 5/5 coverage gate를 exact hash로 묶고,
    #   normalization/freshness/weight/threshold/override/hysteresis 정책이
    #   비준되지 않은 현재는 BLOCKED_COVERAGE 또는 BLOCKED_POLICY_UNRATIFIED만
    #   허용한다. ⛔ Regime 분류/score/전략/Production/trading 없음.
    #   추가로 이미 병합된 ratified PAPER baseline v1 공통 집계값을
    #   registry v2 common_v1_alignment에 hash-bound된 replay 전용 정책으로
    #   구현하고 bull/bear/sideways/stress PIT replay의 determinism·전이·
    #   stress·3/5 UNKNOWN을 검증한다. ⛔ runtime wiring/PIT replay acceptance
    #   없음 — signed axis 입력만 받고 모든 downstream 권한은 false다.
    "test/test_regime_decision_authority.py",
    "test/test_runtime_regime_readiness.py",
    # ★ P1-COM-05 evidence population — 비준된 P1-COM-02 5/5 coverage만
    #   exact policy bytes/PR/WBS lineage로 후보에 결합한다. 나머지 8개
    #   파라미터와 replay는 BLOCKED/NOT_COMPUTABLE, 모든 downstream 권한은 false다.
    #   ⛔ network/workflow/정책선택/분류/Production/trading 없음.
    "test/test_regime_policy_candidate_population.py",
    # ★ P1-COM-01 — Regime 공통 pre-score UNKNOWN output contract.
    #   5축 evidence/coverage/timestamp를 같은 schema로 고정하고 데이터 부족을
    #   NEUTRAL로 위장하지 않으며 score/threshold/Production은 차단한다.
    #   ⛔ 시장판정/네트워크/tracked output 없음 — temp envelope fixtures only.
    "test/test_regime_output_contract.py",
    # ★ P8-04 live-axis evidence adapter. Qualified BTC trend/risk and
    #   stablecoin observations can populate evidence-only axes, while VIXCLS
    #   without retained raw provenance and partial IEX/watchlist/membership
    #   data are never promoted. Regime/direction stay UNKNOWN; authority false.
    "test/test_regime_live_axis_adapter.py",
    # ★ P1-COM-05 user-ratified B+C calibration-readiness inventory.
    #   Replays retained FRED/BTC/Stablecoin raw bytes with existing validators
    #   and reports US 1/5, Korea 0/5, Crypto 3/5 plus PIT history spans.
    #   No missing axis, history minimum, policy value, Shadow candidate,
    #   replay case, market rank, Regime, capital, or trading authority is
    #   invented; resigned output/source tamper fails closed. temp output only.
    "test/test_regime_policy_calibration_readiness.py",
    # ★ P1-COM-05 PAPER 참고판정. 이미 보존된 무료 US/KR/Crypto 5축 관찰값을
    #   사용자 화면용 진단으로만 정규화한다. runtime/final Regime은 UNKNOWN,
    #   Stage/Buy/Action/Order/Capital/Production/Trading은 모두 false다.
    #   Crypto 원자료는 재검증하고 입력·출력 변조는 fail-closed한다.
    "test/test_paper_regime_reference.py",
    # ★ Stage1 three-market handoff. Exact retained workflow/run/output facts
    #   bind one consumer tuple. US/Crypto display immediately; an unadvanced
    #   KR daily source stays explicit and prevents same-date completion.
    #   Future natural slots are NOT_DUE and every capital/order/trading flag
    #   remains false.
    "test/test_stage1_market_tuple.py",
    # ★ P1-COM-05 PAPER runtime adoption eligibility.  Reuses the already
    #   classified three-market reference and evaluates only whether an exact
    #   externally retained source workflow completion/readback, published
    #   source identity, official session calendar, and caller-supplied clock
    #   prove the observation CURRENT.  Missing terminal evidence stays
    #   UNCONFIRMED; this helper never self-certifies, classifies, calls a
    #   provider, advances strategy state, allocates capital, or issues orders.
    "test/test_paper_regime_runtime_adoption.py",
    "test/test_kr_paper_runtime.py",
    "test/test_kr_paper_runtime_ratification_candidate.py",
    "test/test_kr_information_system_runtime_bridge.py",
    "test/test_kr_information_system_runtime_publication.py",
    # KC3 daily KR PAPER evidence: offline KRX calendar packets from the
    #   committed official capture and the rolling 28-session common-v1
    #   history window (unchanged bridge validator; gaps fail closed).
    "test/test_kr_paper_runtime_daily_evidence.py",
    # KC3 KR_PAPER_RUNTIME_ADOPTION_V1 daily publisher: adoption pins,
    #   per-session qualification derivation, validated observation chain,
    #   dispatch-only workflow boundary (raw rows never committed).
    "test/test_kr_paper_runtime_adoption_v1.py",
    "test/test_kr_internal_paper_theme_application.py",
    "test/test_kr_internal_paper_theme_next_session_v4.py",
    "test/test_us_paper_policy_binding.py",
    # ★ P1-COM-05 CIO mandate 2026-09-04 — normalization replay-readiness
    #   evidence (SHADOW only). Reuses build_us/build_kr from
    #   paper_regime_reference.py unmodified against whatever historical
    #   evidence/free_market_data + data/observations/korea_market_signals
    #   snapshots are already retained, and reports per-axis
    #   COMPUTABLE_NOW/PARTIAL_HISTORY/NOT_COMPUTABLE plus coverage, staleness,
    #   transition, and determinism facts only. Each retained date is replayed
    #   independently (no-lookahead is asserted by truncating the latest date),
    #   the sha256 of every replayed snapshot is pinned, and a date the rule
    #   cannot consume fails closed as one unreplayable date instead of
    #   aborting the report. No threshold, weight, or
    #   ratification is introduced; sensor/registry/TTL/PIT/runtime/strategy/
    #   order/capital/production/trading authority all stay false.
    "test/test_normalization_replay_readiness.py",
    # ★ P1-COM-05 CIO mandate 2026-09-04 — KR 5-axis historical replay
    #   population (SHADOW backfill only, never NATURAL). For caller-supplied
    #   historical dates only (no bull/bear/sideways/stress auto-selection),
    #   reuses .github/scripts/korea_market_signals.py
    #   (discover_session_pair/build_packet) and
    #   regime/paper_regime_reference.py::build_kr unmodified to reconstruct
    #   the same 5-axis KRX observation and its candidate normalized result.
    #   Every record carries evidence_class =
    #   HISTORICAL_BACKFILL_CAUSAL_RESEARCH_ONLY; each date is resolved and
    #   replayed independently and only ever backward from its own requested
    #   date (no-lookahead is structural, not asserted); a malformed date,
    #   missing source, or single unresolvable axis fails that one date
    #   closed without affecting any other requested date. Output is never
    #   written inside this checkout — external --out or a private temp file
    #   only, enforced fail-closed. Validation is exact rather than best-effort:
    #   every requested date must map to exactly one record, an OBSERVED record
    #   must carry the axes and candidate result it claims, a BLOCKED one must
    #   carry an attributable reason and neither, each record's attested session
    #   dates are re-compared to its requested date, and the authority block must
    #   match key for key — so a re-hashed payload cannot pass by dropping its
    #   records or an explicit false boundary. Every KRX and payload session date
    #   is PARSED as a calendar date before it is compared, at build and at
    #   validation time: date shape is not a calendar and these dates compare
    #   lexicographically, so 20260231 — a day no calendar has — sorted before
    #   the requested date and was cleared as an ordinary earlier session, the
    #   previous session date never having been parsed at all. It now fails that
    #   one date closed as its own distinct fact rather than as a lookahead. A
    #   re-hashed payload cannot pass by dropping its
    #   records or an explicit false boundary. Provenance is enforced as part of
    #   the observation, not as decoration: an OBSERVED record must carry the
    #   official KRX request lineage and packet digest, which are re-bound by
    #   reassembling the producer's own packet and re-running
    #   korea_market_signals.validate_packet over it against the pinned contract,
    #   so deleting a source hash, pointing a request at an unofficial endpoint,
    #   or editing a hash without re-deriving every digest above it fails closed;
    #   only a BLOCKED record may carry null provenance. The strength of that
    #   binding is stated, not implied: payload_sha256 is unkeyed over the
    #   record's own mutable fields, so a fully coordinated re-point (edit a
    #   response hash, recompute the packet digest, recompute the population
    #   digest) is internally consistent and is accepted. What is proven is
    #   consistency plus pinned-contract conformance, not attribution to bytes
    #   KRX served — no raw response or provider signature is retained to anchor
    #   against, and obtaining one is a separate data decision. Both the caught
    #   and the uncaught side are pinned by regression so the claim cannot
    #   widen silently. No new threshold, scoring, or
    #   Regime policy is introduced; natural_promotion and every
    #   action/order/capital/production/trading/real authority stay false.
    "test/test_kr_historical_replay_population.py",
    "test/test_kr_retained_historical_population.py",
    "test/test_kr_contiguous_historical_range.py",
    # ★ P1-COM-05 CIO mandate 2026-09-04 — US free-source historical replay
    #   population (SHADOW backfill only, never NATURAL). Scope is exactly the
    #   three axes that free/existing sources can rebuild point-in-time:
    #   TREND (Alpaca IEX daily bars), RISK_VOL (FRED VIXCLS), and LIQUIDITY
    #   (FRED WRESBAL/TOTBKCR). US BREADTH and US LEADERSHIP are NOT populated:
    #   their only source is alpaca.current_proxy_axes, ratified
    #   CURRENT_REFERENCE_ONLY with us_breadth_authorized=false, so both stay
    #   UNKNOWN with the contract-read exclusion basis pinned in every record,
    #   and the validator rejects any output carrying a value for them. Because
    #   coverage is therefore 3/5, the candidate result is honestly
    #   NOT_COMPUTABLE and candidate/runtime regime stay UNKNOWN — no US regime
    #   is manufactured from a partial axis set. Bar retrieval, OHLC checks,
    #   session-return math, and FRED unit normalization reuse
    #   collectors/free_market_data.py unmodified, and the three axis rows are
    #   pinned byte-for-byte to the live
    #   regime/paper_regime_reference.py::build_us across every threshold
    #   boundary, so no threshold is forked, tuned, or re-ratified. PIT is
    #   structural: the Alpaca request end and both FRED observation_end and
    #   ALFRED realtime vintage are pinned to the requested date, and any
    #   source date after it fails closed. Every provider- and payload-supplied
    #   date is PARSED as a calendar date before it is compared, at build and at
    #   validation time. Date shape is not a calendar and ISO dates compare
    #   lexicographically, so a shape-only check followed by a string comparison
    #   previously cleared 2026-02-31 — a day no calendar has, sorting before
    #   2026-03-01 — as ordinary backward-looking evidence, whether it arrived as
    #   an observation date, an Alpaca session, the previous liquidity
    #   observation, the series metadata, or an ALFRED vintage bound. Every date
    #   a measurement carries is now bound to the requested date as well, because
    #   the attestation walk never reached inside a measurement; the
    #   still-current 9999-12-31 vintage sentinel is the single exemption and is
    #   bound separately as a containment window. Dates come only from --date (no
    #   episode auto-selection); one axis or one date failing never affects
    #   another; credentials are redacted out of every recorded reason; the
    #   account/trading Alpaca credential is never read. Output is never
    #   written inside this checkout — external --out or a private temp file
    #   only. Validation is exact rather than best-effort: every requested date
    #   must map to exactly one record, a record's status and free-axis coverage
    #   are recomputed from the axes it carries, an observed/partial record may
    #   not null its axis packet (which would satisfy the never-BREADTH rule by
    #   having no axes at all), and the authority block must match key for key —
    #   so a re-hashed payload cannot pass by dropping its records or an explicit
    #   false boundary. Provenance is enforced as part of the observation, not as
    #   decoration: each observed axis's Alpaca/FRED response hash must be present
    #   exactly when that axis is OBSERVED, absent exactly when it is not, valid
    #   SHA-256, and consistent with the provenance inside that axis's own
    #   measurement, so deleting, blanking, or swapping one record-level hash
    #   fails closed even under a recomputed payload hash. This is named for what
    #   it is — a consistency check between two mutable copies of the same hash
    #   (RECORD_SOURCE_HASHES_INCONSISTENT_WITH_THEIR_MEASUREMENTS), not an
    #   external anchor: replacing both copies with the same arbitrary valid
    #   SHA-256 and re-signing is accepted, because the raw provider responses
    #   are not retained and neither provider signs them. That uncaught side is
    #   pinned by an adversarial regression alongside the caught side.
    #   Point-in-time integrity is bound on both sides of the FRED call, not only
    #   on the request: pinning realtime_start/realtime_end states what was asked
    #   for, so every vintage window the provider actually RETURNS — the latest
    #   observation, the previous observation the change is measured against, and
    #   the series metadata that fixes the units — must contain the requested
    #   date, at build time and again in validate_population. A response whose
    #   ALFRED vintage opens after the replayed date fails that axis closed as a
    #   lookahead (previously it was consumed as if knowable, with measurement,
    #   axis row, hashes and signature all internally consistent); one whose
    #   vintage had already ended is refused separately, because being superseded
    #   is a different fact from being unknowable. The bind is containment, never
    #   equality: the still-current 9999-12-31 sentinel is accepted, and that
    #   accepted side is pinned too. The population's own pit_replay declaration
    #   is validated key for key, value for value, statement included, so a
    #   re-signed payload can no longer assert
    #   future_dates_used_in_any_date_evaluation=true — or delete the declaration
    #   — while every record-level check still passes.
    #   natural_promotion, us_breadth, us_leadership and every
    #   action/order/capital/production/trading/real authority stay false.
    "test/test_us_historical_replay_population.py",
    # ★ US-DATA-1 U3 (CIO 2026-09-14) + user ratification
    #   US-SESSION-CALENDAR-SOURCE-V1-20260914. US session calendar: official
    #   NYSE capture for published years (Nasdaq cross-check where available),
    #   Alpaca calendar AND IEX SPY bar for 2018+ earlier years, conflict/missing
    #   = US_FINISHED_SESSION_UNKNOWN, no weekday inference. The registry bytes
    #   stay hash-bound; the amendment is an overlay config. Offline fixtures only.
    "test/test_us_official_session_calendar.py",
    #   Rule-fixed US replay range declared before any run: 15 replay symbols,
    #   61-session warm-up, latest completed session, whole-range fail-closed
    #   truncation, no sub-range arguments; bounded (<=15 request) probe capture
    #   with no secret or price retention; resumable/idempotent chunk driver that
    #   evaluates US PIT acceptance only on the complete declared range.
    "test/test_us_replay_range_declaration.py",
    #   Probe + full replay workflows: workflow_dispatch only, least privilege,
    #   secrets only in step env, artifacts under RUNNER_TEMP, nothing committed.
    "test/test_us_regime_replay_workflows.py",
    #   US-DATA-1 U3 producers for the two artifacts a later U5 adoption must
    #   bind. The session-calendar producer adds no calendar logic: every date is
    #   classified by market_data/us_official_session_calendar.py under the same
    #   ratification, only the OFFICIAL_NYSE_CAPTURE basis is admitted (Nasdaq
    #   cross-check required to attest), and one UNKNOWN date refuses the whole
    #   file — no weekday inference, no per-date skip. The committed bytes are
    #   proven stable across re-runs over an unchanged page, so the sha256 U5
    #   pins cannot silently move. Both tests prove their artifact against
    #   regime/us_paper_runtime.py's OWN exact-match loaders rather than a
    #   restatement of them, and neither creates or activates
    #   config/us_paper_runtime_adoption_v1.json — U5 is a user ratification.
    "test/test_us_official_session_calendar_producer.py",
    #   The US PIT acceptance record generator, plus the standing proof that US
    #   acceptance is still unreachable: the 5-axis replay identity is inactive
    #   and no population bundle is committed.
    "test/test_us_pit_acceptance_record.py",
    # ★ P1-COM-05 CIO mandate 2026-09-04 — combined KR+US historical replay
    #   population/report (SHADOW backfill only, never NATURAL). Joins the KR
    #   5-axis and US free-axis replay populations over ONE caller-supplied set
    #   of dates: both market modules are imported and called unmodified, and
    #   each embedded population is re-checked by its OWN validator before it is
    #   joined, so no sub-population its owning contract would reject can be
    #   published. Normalization is exactly what those populations already carry
    #   (regime/paper_regime_reference.py::build_kr,build_us) — nothing is
    #   re-normalized, re-scored, or re-classified here. No episode is selected:
    #   dates come only from --date/--episode/--episode-file, an episode name is
    #   an opaque caller label attached AFTER its dates were replayed, and a
    #   labelled run is proven byte-identical to an unlabelled one. No
    #   cross-market regime is invented: there is no ratified rule combining a
    #   KR candidate regime with a US one, so cross_market_regime stays UNKNOWN
    #   with NOT_COMPUTABLE_NO_RATIFIED_CROSS_MARKET_RULE and no combined
    #   score/confidence is produced. US BREADTH/LEADERSHIP stay UNKNOWN and the
    #   US view is never classified. PIT is re-checked at the join: each market
    #   record's own consumed source dates (KRX YYYYMMDD and ISO alike) are
    #   parsed as calendar dates and compared to its requested date, and a market
    #   that consumed a later one is failed closed for that one date only. A
    #   consumed date that is date-shaped but is no calendar day — 2026-02-31,
    #   which sorts before 2026-03-01 and so passed the previous string
    #   comparison as backward-looking — fails that market closed under its own
    #   code, never mislabelled as a lookahead; a requested date that is itself
    #   not a real day remains a legitimate blocked record carrying each market's
    #   own attributable reason. One blocked market, one blocked
    #   date, an unavailable market population, or an unrecognized record status
    #   never contaminates the rest; credentials are redacted out of every
    #   recorded reason and the account/trading Alpaca credential is never read.
    #   Output is never written inside this checkout — external --out or a
    #   private temp file only. Validation is exact rather than best-effort:
    #   every requested date maps to exactly one record, every published count
    #   (combined summary, per-market outcomes, per-record outcome grouping,
    #   episode coverage, ungrouped dates) is recomputed from those records, each
    #   embedded population's pinned identity must be its own, and the authority
    #   block must match key for key — so a re-hashed payload cannot pass by
    #   dropping its records or an explicit false boundary. The join's own
    #   lookahead re-check walks record dates, which cannot see a FRED ALFRED
    #   vintage, so the US module's returned-vintage bind is what refuses a
    #   future-vintage US measurement here: an embedded US population carrying
    #   one — or a US pit_replay declaration re-signed to deny PIT — is rejected
    #   at the join instead of publishing US as OBSERVED. natural_promotion,
    #   episode_selection, cross_market_regime, threshold_tuning, us_breadth,
    #   us_leadership and every
    #   action/order/capital/production/trading/real authority stay false.
    "test/test_combined_shadow_historical_replay.py",
    # ★ P1-COM-05 CIO mandate 2026-09-04 — deterministic replay evidence over
    #   that combined KR+US SHADOW population (facts only, never NATURAL).
    #   Summarizes exactly five families and nothing else: coverage, UNKNOWN,
    #   transitions, stress detection, and hysteresis facts. It issues no
    #   KRX/Alpaca/FRED request, derives no axis, and re-runs no normalization —
    #   every direction, candidate regime, and reason code it counts was already
    #   produced by the two market replay populations and joined by the combined
    #   slice, whose OWN validator re-checks the population before a single fact
    #   is derived. UNKNOWN semantics are preserved rather than flattened:
    #   "excluded by ratification scope" (US BREADTH/LEADERSHIP), "attempted and
    #   NOT_COMPUTABLE", and "the whole date was blocked" stay three distinct
    #   buckets that must add up to the requested dates, none is ever reported as
    #   an observed NEUTRAL, and a transition pair touching UNKNOWN is counted as
    #   an evidence-availability change, never a market state change. Every
    #   sequence covers every requested date, carrying UNKNOWN where none was
    #   produced, so a run can never bridge a blocked date; adjacency is
    #   requested-date order, so each sequence carries its own calendar-gap facts.
    #   NO POLICY OR THRESHOLD CONCLUSION is reached, and the two policy layers
    #   are kept apart so neither is reported as the other's absence: the
    #   repository's ALREADY-RATIFIED replay-only common aggregation policy
    #   (common_v1_alignment, RATIFIED_PAPER_BASELINE_V1, owned by
    #   regime/decision_authority.py) is quoted verbatim with its own hysteresis
    #   and stress entry/exit behavior and marked explicitly not applied — it
    #   consumes already-signed axis directions and the registry records
    #   market-specific normalization/freshness/replay as not inherited — while
    #   the market-specific candidate policy this replay exercised is read from
    #   the repository's own inventory as HYSTERESIS/STRESS_OVERRIDE BLOCKED
    #   under DRAFT_NOT_RATIFIED. hysteresis_applied stays false, a ratified
    #   market-specific component or a changed common scope fails the module
    #   closed, no threshold is proposed, tuned, or restated, run/reversal counts
    #   are observations rather than an argument for a dwell time, and the
    #   validator rejects a conclusion, verdict, or recommendation smuggled in
    #   under any key name — including one produced by DELETING an explicit
    #   refusal flag or authority boundary. Validation is exact rather than
    #   best-effort: every requested date must carry an observation and every
    #   fact family must re-derive from those observations. No episode is selected —
    #   labels are carried verbatim and a labelled run is proven fact-identical to
    #   an unlabelled one. PIT and historical audit stay separate: no date's
    #   observation is created, altered, or graded, and a market the join
    #   contained for lookahead contributes nothing. A source population whose
    #   embedded US replay was served a FRED vintage published after the replayed
    #   date, whose US pit_replay declaration was re-signed to deny PIT, or whose
    #   embedded KR or US record is dated by a day no calendar has (2026-02-31 is
    #   date-shaped and sorts before 2026-03-01, so a string comparison read it
    #   as backward-looking), is refused by its own validator and never
    #   summarized — every coverage,
    #   UNKNOWN, transition, stress, and hysteresis fact is counted from those
    #   records. The report is a pure function
    #   of its source population (byte-identical rerun, shuffled input identical,
    #   source never mutated) and is never written inside this checkout —
    #   external --out or a private temp file only. policy_conclusion,
    #   threshold_ratification, hysteresis, stress_override, episode_selection,
    #   natural_promotion, us_breadth, us_leadership and every
    #   action/order/capital/production/trading/real authority stay false.
    "test/test_deterministic_replay_evidence.py",
    # P1-COM-05 CIO final verdict 2026-09-12 (docs/p1_com_05_cio_final_verdict_
    #   20260912.md): G4 ratified as source-frequency SEMANTIC freshness, not a
    #   numeric TTL. Session-based axes (US TREND/BREADTH/LEADERSHIP; all five
    #   KR axes) require an exact match to the latest officially completed
    #   session, immediate UNKNOWN/SOURCE_NOT_ADVANCED_EXPECTED_SESSION
    #   otherwise, no carry/substitution. Release-based axes (US RISK_VOL=
    #   VIXCLS daily, LIQUIDITY=WRESBAL/TOTBKCR weekly) require the latest
    #   successfully fetched, hash-retained publication; an unchanged weekly
    #   value is a normal fresh outcome. The KR 18:00 KST usability gate is
    #   reused, hash-bound to the live config/korea_leadership_policy.json,
    #   never re-declared. No numeric_ttl_seconds value exists anywhere in
    #   this policy. No runtime/action/order/capital/production/trading
    #   authority is granted.
    "test/test_regime_semantic_freshness.py",
    # P1-COM-05 CIO final verdict 2026-09-12: ratifies PAPER_RUNTIME_
    #   NORMALIZATION_V1 (US/KR signed-axis normalization identity, byte-
    #   identical to the pre-existing PM candidate in
    #   config/paper_regime_reference_policy_v1.json; Crypto stays
    #   unratified/UNKNOWN) and a market-scoped G8 PIT acceptance contract
    #   independent per market (US/KR/CRYPTO) that is separate from, and does
    #   not weaken, the existing three-market regime_replay_harness/v1. All
    #   classification/hysteresis is the exact, unmodified
    #   regime.decision_authority.replay_common_v1 reuse. A caller-supplied
    #   sequence earns zero credit unless it byte-matches the real
    #   regime.us_historical_replay_population/
    #   regime.kr_historical_replay_population output provenance; no episode
    #   date is ever selected by this module. Initial, and current committed,
    #   status for every market is NOT_ACCEPTED
    #   (data/latest_market_scoped_pit_acceptance.json). runtime_decision_
    #   available and every action/order/capital/production/trading authority
    #   stay false.
    "test/test_market_scoped_pit_acceptance.py",
    # Current-reference 5/5 and official PIT-history coverage remain separate.
    # The pointer exposes automatic refresh timing and fail-closed progress;
    # it never promotes current data into final Regime or trading authority.
    "test/test_crypto_regime_refresh_status.py",
    # PAPER-only descriptive normalization for the already published current
    # Crypto five-axis reference.  It preserves the provisional caveats and
    # cannot grant runtime, capital, order, Production, or trading authority.
    "test/test_crypto_paper_descriptive_normalization.py",
    # CRYPTO_PAPER_RUNTIME_V1 (user ratification 2026-09-14): crypto-scoped
    # PAPER runtime identity, ratified RISK_VOL absolute rule boundaries,
    # LEADERSHIP pilot->primary window rule, 07:00Z finalized packets with
    # immediate UNKNOWN on stale/missing/date-mismatch/lookahead/mixed
    # generation, PROVISIONAL_FORWARD_ACCEPTANCE with auto-revert, the Kraken
    # bulk BTC-only replay diagnostic, and the retained-evidence publisher.
    # Strategy, capital, order, production, trading and REAL stay closed.
    "test/test_crypto_paper_runtime.py",
    "test/test_crypto_kraken_btc_replay_diagnostic.py",
    "test/test_crypto_paper_runtime_publication.py",
    # Scheduled crypto PAPER runtime producer (07:15Z/08:45Z, --check before
    # commit, contents:write only) and the earlier stablecoin cutoff slots;
    # captures after 07:00Z stay lookahead-rejected.
    "test/test_crypto_paper_runtime_schedule.py",
    # U4 US PAPER runtime producer (CLAUDE_CIO US/crypto regime gap diagnosis
    # 2026-09-14): common-v1 reuse over the committed free-market-data
    # captures, SESSION_EXACT_MATCH for TREND/BREADTH/LEADERSHIP, FRED release
    # and vintage-lookahead semantics for VIX/WRESBAL/TOTBKCR, expiry at the
    # next official session close.  UNKNOWN with explicit reasons until an
    # active US_PAPER_RUNTIME_ADOPTION_V1 binds a re-evaluated US PIT_ACCEPTED
    # record and an official session calendar.  Scheduled 21:55Z/23:40Z
    # Sun-Fri with --check before commit; authority stays closed.
    "test/test_us_paper_runtime.py",
    "test/test_us_paper_runtime_publication.py",
    # The producer reads a committed capture, not its own fetch, so it states the
    # collection coverage of the capture it read and blocks past a bound taken
    # from the committed decision history.  Coverage is counted in the
    # collector's cadence dates (cron "35 21 * * 0-5"), never in elapsed
    # wall-clock days: a Sunday evaluation reading Friday's capture stays green.
    "test/test_us_paper_runtime_collection_coverage.py",
    # The date-rollover watchdog records an issue and explicit safe WAIT
    # without turning an expected evidence delay into a failed workflow email.
    # Order and trading authority remain closed in the operator message.
    "test/test_crypto_regime_refresh_watchdog_workflow.py",
    # PAPER-only bridge: Regime controls the future total risk envelope while
    # cross-market flow/relative strength controls the within-envelope review.
    # All numeric weights, account facts, actions, orders, and trading remain
    # null/false until replay and a separate capital-budget ratification.
    "test/test_capital_flow_posture_reference.py",
    # ★ P2-COM-03 append-only Cross-Market Flow transition ledger. Consumes
    # only the exact P2-COM-02 evidence packet and records previous/current,
    # first_seen, persistence, reversal/invalidation, and evidence lineage.
    # Only NATURAL observations count; MANUAL/RECOVERY/REPLAY are labeled but
    # non-counting. confirmed_at stays null and no threshold, allocation,
    # capital/action/order, Production, or Trading authority is introduced.
    "test/test_cross_market_flow_transition_ledger.py",
    # ★ P1-COM-04 — Regime pre-score deterministic replay harness.
    #   US/KR/CRYPTO의 동일 regime_output/v1 증거를 두 번 검증하고 canonical
    #   byte equality와 설명 가능 필드 보존을 확인한다. minimum coverage가
    #   미비준인 동안 UNKNOWN을 유지하며 score/hysteresis/Production을 차단한다.
    #   ⛔ live network/workflow/tracked report 없음 — in-memory envelopes only.
    "test/test_regime_replay_harness.py",
    # ★ P1-COM-04 canonical replay-population readiness.
    #   replay capability를 P1-COM-05 retained candidate inventory에 exact-byte로
    #   결합한다. 현재 MINIMUM_COVERAGE 1개만 supported, 나머지 8개 blocked라
    #   eligible market/case=0, outcome 미평가, 모든 downstream 권한=false다.
    #   ⛔ case/threshold/정책/성과/network/workflow/tracked output 발명 없음.
    "test/test_regime_replay_population_readiness.py",
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
    # ★ P1-CR-06 — first formally qualified Crypto Breadth exit gate.
    #   Registry의 exact evidence hash와 contract의 threshold lineage를 함께
    #   검증하되 관측값·Stage·action·order·Production·Trading은 열지 않는다.
    #   ⛔ integration-only registry entry — source-owned blobs are unchanged.
    "test/test_p1_cr_06_exit_gate.py",
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
    # ★ P3-04 — deterministic taxonomy gap review inventory. Rebuilds the
    #   exact cutoff-relevant UNKNOWN/EXCLUDED rows from a committed Kraken
    #   snapshot and binds manifest/policy/taxonomy hashes. It creates no
    #   classification, ratification, investability, Stage, or trading right.
    "test/test_crypto_taxonomy_gap_inventory.py",
    # ★ P3-04 — preventive classification-margin monitor. Measures the rank
    #   distance between the production eligibility scan stop and the nearest
    #   unclassified asset on the *production* ranking, alarms on both the
    #   level and the per-day shrink rate from committed thresholds, and
    #   escalates automatically once primary_30d can latch as the official
    #   LEADERSHIP window (one unknown day then costs 30+5 days instead of
    #   7+5). ⛔ creates no classification/ratification/investability/Stage/
    #   threshold/trading right — it reports a queue, it does not decide one.
    "test/test_crypto_taxonomy_margin_monitor.py",
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
    # ★ P1-CR-06 — 2026-08-27 cutoff-relevant taxonomy Slice (42개).
    #   retained Kraken online USD identity와 독립 project/contract source를
    #   결합해 effective-dated source-coverage category만 비준한다. 8/27 raw는
    #   as_of=8/26이라 PIT 결과를 그대로 보존하고, 비영속 logic-only replay로
    #   기존 Top-100/90% gate가 임계값 완화 없이 닫힐 충분조건만 검증한다.
    #   ⛔ investability/Regime/Production/trading 권한 없음.
    "test/test_crypto_taxonomy_cutoff_slice_20260827.py",
    "test/test_crypto_taxonomy_btr_slice_20260829.py",
    # ★ Lane P — 2026-09-04 CIO ratification of HNT + SKR only (SN8 withheld,
    #   fail-closed / no unverified_identity workaround, no new category).
    #   retained Kraken online USD pair + project official docs (Helium /
    #   Solana Mobile) bind identity. No retroactive credit before
    #   effective_from=2026-09-04. E2E against the real 2026-09-03 snapshot:
    #   HNT/SKR resolve, SN8 stays UNKNOWN, qualified_members() stays
    #   TAXONOMY_COVERAGE_UNKNOWN because of SN8 -- no BREADTH PASS claimed.
    "test/test_crypto_breadth_hnt_skr_taxonomy_ratification.py",
    # ★ 2026-09-14 user ratification CRYPTO-BREADTH-TAXONOMY-ADDITIONS-20260914:
    #   LSK (effective 09-14) and SUSHI/VSN/TRIA/ZORA/XTZ/KII/0G (effective
    #   09-15) eligible_crypto. No backfill; retained vintages 09-08..09-14
    #   unchanged; in-memory projection of the committed 09-14 snapshot to
    #   vintage 09-15 is no longer TAXONOMY_COVERAGE_UNKNOWN because of LSK.
    #   ⛔ thresholds/fail-closed unchanged; no live Kraken, no date-dependent test.
    "test/test_crypto_breadth_taxonomy_additions_20260914.py",
    # ★ Conditional LIGHTER (same ratification): Kraken official asset page
    #   identity confirmed; eligible_crypto effective 2026-09-16, no backfill.
    "test/test_crypto_breadth_lighter_identity_20260914.py",
    # ★ 2026-09-18 cutoff-band identity slice (ranks 124..152, 40-rank band
    #   above the rank-112 eligibility-scan cutoff): BAT/CAKE/CFG/ENS/ETC/GRT/
    #   MNT/PEAQ/SAND/SHAPE/SHX/SN51/VET eligible_crypto and MOODENG
    #   unverified_identity, all effective 2026-09-18. Kraken leg re-checked
    #   from the committed 09-18 Assets/AssetPairs bytes; the source-fact
    #   receipt (evidence/crypto/identity/...20260918.json) is bound to the
    #   taxonomy so the two cannot drift. Retained vintages 09-12..09-18 are
    #   byte-identical with and without the records — this batch buys headroom
    #   below the cutoff, it does not change any committed result.
    #   ⛔ thresholds/Top-100/fail-closed unchanged; no live Kraken, no
    #   date-dependent test; MOODENG records a failure to verify, not a guess.
    "test/test_crypto_breadth_band_identity_20260918.py",
    "test/test_crypto_breadth_headroom_identity_20260918.py",
    # ★ 2026-09-18 deferred-five batch — the headroom slice left STORJ(159),
    #   MET(161), RIVER(166), DENT(167), GALA(168) unclassified because two
    #   independent official sources were not obtained inside that batch.
    #   All five are now resolved on evidence and the block runs contiguous
    #   through rank 171 (was 158), measured from minimum rank across the
    #   seven committed vintages rather than from one day's snapshot.
    #   ⛔ DENT is a chain-level identity only — no contract address is
    #   published on any live official page — and the test pins that
    #   disclosure so it cannot be silently upgraded to an exact-contract
    #   claim.
    "test/test_crypto_breadth_deferred_five_identity_20260918.py",
    # ★ P1-CR-07 rank-200 push, slice A — 크립토 분류 여유 172~185위.
    #   2026-10-07 축 전환(7일→30일) 전에 분류를 끝내기 위한 3분할 중 첫
    #   조각. ROBO/GRASS/AXS 는 체인 수준 신원만 기록하고 그 사실을 시험이
    #   고정한다. TURBO/ZEREBRO 는 PLAY/RE/MOODENG 와 같은 근거로
    #   unverified_identity — 확정된 제외이며 투자 판단이 아니다.
    #   ⛔ live 요청 없음 — 커밋된 Kraken 스냅샷 + 영수증만 읽는다.
    "test/test_crypto_breadth_rank200_slice_a_20260919.py",
    # ★ P1-CR-07 rank-200 push, slice B — 크립토 분류 여유 187~193위.
    #   slice A 위에 쌓인 두 번째 조각. TAC 은 Kraken 이 network 를 "-" 로
    #   내보내 카탈로그만으로는 대조할 것이 없어, Kraken 자체 상장 공지와
    #   TAC Protocol 자체 블로그가 같은 TON 연동 EVM L1 을 기술하는 것으로
    #   확정했다. BRL1 은 발행 컨소시엄 자체 표현대로 stablecoin 제외,
    #   STBL 은 반대로 거버넌스 토큰이라 eligible. AIN 은 체인 수준만 기록.
    #   ⛔ live 요청 없음 — 커밋된 Kraken 스냅샷 + 영수증만 읽는다.
    "test/test_crypto_breadth_rank200_slice_b_20260919.py",
    # ★ P1-CR-07 rank-200 push, slice C — 크립토 분류 여유 194~200위, 목표 도달.
    #   3분할의 마지막. 이 조각으로 블록이 201위까지 연속이 되고 다음
    #   미분류는 PIEVERSE(202위)다. FUN 은 티커 충돌 사례 — Kraken 이 내보내는
    #   것은 Base 의 Sport.fun 이지 이더리움의 구 FunFair 가 아니며, 기록은
    #   Base 계약만 묶는다. RUNE/SC 는 자체 체인 네이티브 코인 주장.
    #   ⛔ live 요청 없음 — 커밋된 Kraken 스냅샷 + 영수증만 읽는다.
    "test/test_crypto_breadth_rank200_slice_c_20260919.py",
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
    # ★ P1-CR-07 replay input — Kraken official bulk OHLCVT ZIP importer.
    #   daily USD rows only, exact archive/output hashes, source aliases and
    #   missing intervals preserved. Historical catalog/identity, VWAP turnover,
    #   Leadership/Regime/action/order authority are explicitly not inferred.
    #   ⛔ network/tracked data 없음 — synthetic ZIP + temp replay output only.
    "test/test_crypto_historical_ohlcvt_import.py",
    # ★ P1-CR-07 사용자용 현재 참고 판정 — 최신 확정 Kraken 일봉과
    #   결정시점 taxonomy로 7d/30d raw 비교만 제공한다. historical PIT,
    #   Regime/Production/Stage/Buy/Action/Order/Trading 권한은 전부 false.
    #   ⛔ live Kraken 없음 — committed hash-bound snapshot only.
    "test/test_crypto_recent_reference.py",
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
    # ★ P0-06 consumer adoption — discover the append-only bootstrap with a
    #   per-request nonce, then fetch Step0/health/compact and H-24 delivery
    #   bytes only from its immutable consumer-ready source commit.
    "test/test_scheduled_briefing_authority_consumer.py",
    # ★ P8-15 Capital Rotation E2E natural-chain acceptance.  P0-06 immutable
    # delivery bytes are not treated as schedule provenance: a separate
    # append-only run receipt must prove the exact GitHub event/cron.  Three
    # distinct natural AM/PM dates, viewer-visible Portal receipts for both
    # slots, and one separately attested genuine fail-closed run are required.
    # Portal receipts require GitHub-attested exact bytes plus offline replay;
    # self-authored/self-hashed JSON stays rejected. The separate genuine
    # fail-closed producer is not implemented, so that count remains zero.
    # Manual/replay/recovery is always excluded and every money/trading
    # authority remains false.
    "test/test_capital_rotation_e2e_acceptance.py",
    # ★ H-24 — exact producer locator -> deterministic read-only consumer.
    #   No directory scan/prior-date/alternate-slot fallback; slot/date/revision,
    #   index/packet/rendered hashes and authority=false are independently checked.
    "test/test_daily_briefing_delivery.py",
    # ★ P8-12 -> briefing_core/2 immutable input-envelope boundary.
    #   A present frozen Dynamic Clock source keeps exact variant/type/shape,
    #   canonical report hash, and decision-date identity after outer rehashing.
    #   Legacy packets without the source remain readable; authority stays false.
    "test/test_briefing_core_dynamic_clock_source.py",
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
    # ★ CIO CI-sharding 지시 2026-09-12 — actions-pass.yml 5-job 분할
    #   (preflight → structural/regression(4-way)/fault-injection →
    #   actions-pass-full) 과 `run_all.py --phase` 의 partition 완전성 ·
    #   fail-closed shard 인자 · authority 경계 불변을 증명한다.
    "test/test_ci_phase_sharding.py",
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
    "test/test_official_release_observation.py",
    # P4-02 -> P5-03 exact link-only binding. Revalidates retained TSM SEC
    # monthly-revenue bytes and freezes URL/SHA/quote/offset lineage for only
    # RULE-0007/0008 with ALL_REQUIRED and rule_result=null. Full-submission
    # and index bodies remain non-preserved identities; raw retention is reused.
    "test/test_tsm_sec_monthly_rule_evidence.py",
    # P4-04 retained Sandisk Exhibit 99.1: preserve the complete ordered News
    # Summary block as observation only; all investment authority remains off.
    "test/test_official_release_summary_observation.py",
    # P4-04 evidence-only daily briefing consumer. Renders every retained
    # official summary item while interpretation/ranking/action stays closed.
    "test/test_official_release_summary_briefing.py",
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
    "test/test_dynamic_clock_signal_observation.py",
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
    # ★ Rotation Stage 3 — candidate-selection **input** projection.
    #   P8-05 briefing의 rotation.latest_changes를 순서 그대로 1:1 투영하되
    #   selection_rank/selected/candidate_eligible/ready/promotion/action은
    #   상수로 닫혀 있어 어떤 row도 후보 결과로 읽힐 수 없다. briefing은 스스로
    #   재서명될 수 있으므로 source_ledger_sha256이 가리키는 exact
    #   rotation_state_ledger packet을 함께 요구해 rotation section을 ledger에서
    #   재파생한다 — row 추가/삭제/재배열/state·hash 변조는 counts와
    #   packet_sha256을 다시 계산해도 거부된다. tracked output은 lexical/resolved
    #   경로와 in-repository symlink·symlinked parent까지 replace 전에 막는다.
    #   ⛔ ranking/selection/scoring/promotion/action/Production/trading 및
    #      live network 없음 — synthetic packets + temp output only.
    "test/test_rotation_candidate_selection_input.py",
    # ★ Rotation Stage 3 retained-daily handoff.
    #   daily_orchestrator/6 bundle 안에 이미 보관된 exact ROTATION_DISCOVERY
    #   child를 producer validator로 재검증하고, frozen US source가 없으면
    #   canonical empty ledger를 재유도해 Stage 3 v2 입력으로 연결한다.
    #   latest discovery/새 수집/상태정책 발명 없이 실제 0-row packet을 만든다.
    #   ⛔ selection/NATURAL/ranking/promotion/action/order/capital/Production/
    #      trading 권한 없음.
    "test/test_rotation_candidate_selection_daily_handoff.py",
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
    # ★ P8-14 — policy-neutral Flow-First presentation contract. Fixes the
    #   investor-facing order while leaving absent Cross-Market Flow,
    #   evidence-grade, invalidation, Entry/Exit/Size authority explicitly
    #   UNKNOWN/POLICY_BLOCKED. It never infers flow, ranks, sizes, or trades.
    "test/test_flow_first_briefing.py",
    # ★ P2-COM-01 — policy-neutral Cross-Asset Flow evidence vocabulary.
    #   Stablecoin/KRX participant flow/VIX read models are classified as raw
    #   DIRECT_FLOW or MACRO_CONTEXT evidence; MARKET_IMPLIED_FLOW stays UNKNOWN.
    #   Different dates are never compared and no freshness/lag/normalization/
    #   direction/ranking/action/order/Production/trading authority is created.
    "test/test_cross_asset_flow_evidence.py",
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
    # ★ Briefing content recency (CLAUDE_CIO briefing audit 2026-09-14 Task B) —
    #   real audited slots 09-10 AM/PM … 09-13 AM as fixtures. KRX confirmed
    #   close binds to latest_krx decision_readiness, weekend shows Friday's
    #   recorded session, PAPER regime reference shown dated and labelled
    #   (runtime regime stays UNKNOWN), rows carry 기준일, KOSPI/KOSDAQ moves
    #   recomputed from retained raw index bytes. Presentation only; no status,
    #   aggregate, action, order, Production or trading authority changes.
    "test/test_briefing_content_recency_20260914.py",
    # ★ Briefing renderer ↔ B5 semantic checklist alignment (S8 section 6,
    #   CLAUDE_CIO 2026-09-14) — pinned copy of the staging
    #   briefing_semantic_checks.py (sha256 fd98a204…) runs on real retained
    #   09-13 AM / 09-14 AM rev-001·002 renders with seal-commit inputs.
    #   Row date tokens (decision_date/filing_date/evidence_as_of/…), PAPER
    #   "런타임 미승인" label, dated trend ETF closes, stale-pointer label.
    #   Checks are not loosened: the sealed payloads still HOLD/PWC.
    #   Presentation only; no packet, status, action or authority change.
    "test/test_briefing_b5_renderer_alignment_20260914.py",
    # ★ Weekend briefing evidence-date contract (scheduled_briefing_retrieval_authority/4,
    #   CLAUDE_CIO 2026-09-14) — the ambiguous weekend line
    #   latest_confirmed_evidence_date is replaced by source_evidence_kst_date,
    #   krx_latest_confirmed_close_date and us_latest_verified_session_date,
    #   re-derived by renderer, publisher (re-reads the latest_krx blob) and
    #   consumer from the same hash-bound packet sources; UNKNOWN when unbound.
    #   Real retained 09-12 AM rev-001·002 / 09-13 AM renders pass the pinned
    #   B5-1 (sha256 fd98a204…, unmodified); sealed v3 payloads still HOLD;
    #   retained v3 envelopes still validate under v3; v3 line rejected under v4.
    #   No authority, status or packet change; scratch git repos only.
    "test/test_briefing_weekend_evidence_date_contract_20260914.py",
    # ★ Daily Briefing same-day recovery — original natural schedule run만
    #   KST slot/date로 식별하고 briefing job 실패 시 최대 3회 안에서 재실행한다.
    #   성공한 briefing은 병렬 regression 결론과 분리해 다시 실행하지 않으며,
    #   workflow_dispatch·broker credential·action/order/trading surface가 없다.
    #   ⛔ 테스트 자체는 fixture-only이며 GitHub API/network 호출 없음.
    "test/test_daily_briefing_recovery.py",
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
    #   P3-08 SEC live adapter는 provider-free로 연결됐지만 DATE_ONLY 사건은
    #   전부 명시적으로 BLOCKED된다. DART/news/crypto·importance policy·intraday
    #   polling·notification은 여전히 미배선이다.
    #   ⛔ notification/action/order/Production/trading 및 신규 network 없음.
    "test/test_important_event_detector.py",
    "test/test_intraday_risk_observation_preparation.py",
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
    # ★ P10-01 operational readiness — each immutable committed Daily Briefing
    #   is checked for the exact Unified Decision + both exact P9 live packets.
    #   Missing P9 wiring is a committed fail-closed observation, never a fabricated
    #   Shadow append. Capital/order/action/Production/trading remain false and zero.
    "test/test_three_market_shadow_operational_readiness.py",
    # ★ P10-06 — P8-07 Investment Review append-only zero-capital ledger.
    #   PASS/REJECTED/BLOCKED packet을 exact SHA chain으로 기록하되 proposal 관측은
    #   Shadow 편입·Stage 변경·capital/action/order로 승격되지 않는다.
    "test/test_investment_review_shadow_ledger.py",
    "test/test_investment_review_shadow_store.py",
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
    "test/test_decision_change_lineage_operational.py",
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
    #   Recommendation A (2026-09-02) ratifies only the Reflection Evidence
    #   Authority structure: immutable identity/content/evidence hashes and
    #   PIT/effective-date fail-closed checks. effective_from remains unresolved;
    #   every numeric threshold/sample/confidence/owner field remains pending;
    #   the classifier remains disabled and reflection_status stays UNKNOWN.
    #   ⛔ Rule PASS/FAIL/Stage/Candidate·Ready·Buy promotion/action/order/
    #      Production/trading and live network: none.
    "test/test_reflection_evidence_authority.py",
    "test/test_price_reflection.py",
    # ★ P8-10 — real historical price + Korea KOSPI/KOSDAQ composite benchmark
    #   evidence assembly (decision/price_evidence.py). KRX 시세는
    #   replay/price_series.py + replay/evidence_index.py(PR #210, 변경 없이
    #   재사용)로 커밋된 data/<date>/krx.json 스냅샷들을 병합하고, 한국 벤치마크는
    #   data/observations/korea_leadership_context/<date>/packet.json의 실제
    #   KOSPI_BENCHMARK/KOSDAQ_BENCHMARK day-over-day cumulative_gross_return을
    #   체인링크해 조합한다 (repo에 raw 지수 시계열이 커밋된 적이 없어 이것이
    #   유일한 real, non-fabricated 벤치마크). 요청 decision_date 이전에
    #   PIT-available evidence가 없는 subject는 모든 필드가 정직하게 None이며,
    #   이후 capture를 소급 사용하지 않는다. 새 external API 호출 없음.
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
    "test/test_ai_external_analysis_shadow_evaluation.py",
    # ★ P10-11 — account-independent Crypto PAPER order simulator and
    #   append-only ledger foundation. Caller supplies every quantity, limit,
    #   fee rate, queue fraction, expiry, mark, and frozen public orderbook;
    #   repository economic defaults are absent. LIMIT/MARKET, partial fill,
    #   fee/slippage, cancel/expiry, virtual cash/position/P&L, idempotent
    #   snapshot matching, hash-chain replay, content-addressed persistence,
    #   and restart recovery are deterministic and fail closed. Regime UNKNOWN
    #   is preserved rather than promoted. No HTTP/WebSocket/credential/private
    #   endpoint exists and every exchange/broker/withdrawal/Production/
    #   Trading/REAL authority remains false.
    "test/test_crypto_paper_simulator.py",
    # ★ Stage5 PAPER fixture adapter. A closed-authority, hash-bound Stage4
    # envelope is checked for closed-candle/PIT order then delegated to the
    # existing P10-11 offline simulator. Fixture NOT_EVALUATED is preserved;
    # no policy, broker, capital, production, or trading authority is opened.
    "test/test_stage5_paper_envelope_ledger.py",
    # ★ Stage5 private lifecycle → P7-19 readiness boundary. The connector
    # envelope/receipt/ledger are re-derived, but same-call caller pins remain
    # explicitly untrusted; performance stays null and sample contribution 0.
    "test/test_stage5_virtual_fill_performance_adapter.py",
    # ★ P7-13 — deterministic Crypto PAPER exit/position-management review.
    #   Entry-time plan embeds the exact P10-11 account and caller-supplied
    #   ordered triggers; current account and observation are independently
    #   revalidated and must share exact price/source/time/ledger lineage.
    #   Hard-exit→security/liquidity→risk/Regime→trend→profit/trail→time
    #   priority, prior-only high watermark, target-quantity cap, and repeated
    #   trigger/order identity are deterministic. A planned UNKNOWN waits;
    #   unplanned Regime UNKNOWN is preserved, never promoted or interpreted.
    #   Full buy→review→virtual partial sell integration uses only the offline
    #   P10-11 simulator. Human review remains required and every live exit,
    #   quantity/action/exchange/broker/Production/Trading/REAL authority false.
    "test/test_crypto_paper_exit_manager.py",
    # ★ P8-11 stage 2 — real Pilot evidence intake (TSM/298040.KS/267260.KS/
    #   034020.KS). 저장소에 이미 커밋된 real evidence file만 읽어 forward_thesis/
    #   expectations_gap/price_reflection input을 조립한다. TSM의 5개 6-K 중
    #   evidence_status=OK·real extracted quote가 있는 000536만 EXHIBIT_EXTRACTED
    #   observed_fact를 뒷받침하고, 나머지 4개(EXTRACTOR_NOT_REGISTERED)는
    #   evidence_lineage로만 인용한다. Hyosung 수주잔고/가이던스는 전부
    #   NARRATIVE_SOURCED. frozen 2026-08-22 Pilot에서 034020.KS는 당시
    #   PIT-available evidence가 없어 observed_facts/evidence_lineage가 빈
    #   배열이며 실제 run_all_pilots() 결과 opportunity_state=BLOCKED다.
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
    # ★ P8-12 timestamp-precision foundation (pre-validity-window): retain
    # exact collector timestamps through Snapshot -> ClockEvent -> episode
    # -> candidate lineage. Trigger/decision fields remain DATE_ONLY, so
    # this cannot unlock Risk Capacity, P8-13, or trading authority.
    "test/test_dynamic_clock_time_precision.py",
    "test/test_dynamic_clock_operational_evaluation_time.py",
    # ★ P8-12 Candidate Validity Shadow Observation -- append-only,
    #   content-addressed accumulation of real candidate timing samples.
    #   Date-order and timestamp precision are reported separately; every
    #   candidate remains NOT_COMPUTABLE_CANDIDATE_FRESHNESS_UNRATIFIED.
    #   No validity window, Risk Capacity, P8-13, sizing, or trading
    #   authority is opened.
    "test/test_candidate_validity_shadow_observation.py",
    # P8-12 retained evidence population: independently revalidates every
    # source-backed sample, separates natural/manual/legacy/rejected artifacts,
    # and never invents a minimum sample threshold or validity window.
    "test/test_candidate_validity_evidence_inventory.py",
    # P8-10 operational proof: exact retained Dynamic Clock reports show
    # real Price State linkage while Reflection Status remains UNKNOWN and
    # every downstream action stays locked.
    "test/test_price_state_operational_evidence_inventory.py",
    # ★ P8-12 forward-only lifecycle timestamp observation. The first
    #   natural sample establishes a no-backfill baseline; only subjects
    #   first observed, changed, absent, or reappearing after that baseline
    #   receive exact Atlas observation timestamps. This never claims a
    #   historical source-event time or unlocks validity/Risk/P8-13.
    "test/test_candidate_lifecycle_observation.py",
    # ★ P8-12 empirical lifecycle evidence inventory. Recursively rebuilds
    #   the natural forward chain, separates distinct evidence from repeated
    #   evaluations, and reports endpoint transition counts by market and
    #   trigger family. Observation gaps are never treated as continuous
    #   lifetimes or validity-window authority; P5/P7/P8-13 remain locked.
    "test/test_candidate_lifecycle_evidence_inventory.py",
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
    "test/test_profit_harvest_policy_boundary.py",
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
    # ★ Portfolio Risk Input Contract v2 (portfolio_account_fact/2,
    #   portfolio_risk/portfolio_snapshot_v2.py) -- an independent,
    #   additive account-fact contract for a new provider (KIS PAPER
    #   first) that separates `provider` from `account_scope` as explicit
    #   fields, instead of v1's single conflated `source` string. Does not
    #   modify, extend, or import v1's private helpers or its
    #   Alpaca/Manual-only source validator; reuses only v1's already-
    #   ratified `CANONICAL_ACCOUNT_SCOPE`. Still no sizing/policy
    #   decision: no risk budget, position size, or trading authority is
    #   computed or granted. See docs/portfolio_risk_input_contract_v2.md.
    "test/test_portfolio_risk_input_v2.py",
    # P0-2D valuation-semantic proposal -- exact pinned KIS field meanings
    # plus money-free private relationship attestations.  Additive
    # portfolio_account_fact/3 target only; /2 is unchanged.  Proposal and
    # review readiness never grant account-fact, risk, action, or order
    # authority, and instrument-specific buy capacity cannot be relabelled
    # as a generic account-wide buyingPower value.
    "test/test_kis_valuation_semantic_proposal.py",
    # KIS PAPER valuation source freshness remains proposal-only. The exact
    # 300s/120s candidate keeps its cross-domain pair-gap heuristic explicitly
    # REVIEW_INCOMPLETE until a live pair plus atomic session binding exists;
    # no caller override, retroactivity, or valuation/risk/trading authority.
    "test/test_kis_valuation_freshness_policy_proposal.py",
    "test/test_kis_valuation_authority.py",
    # KIS PAPER v3 closed source-bundle/readiness contract.  Semantic and
    # freshness prerequisites can resolve, but accountFact stays null until
    # a separate account-fact authority is ratified.
    "test/test_portfolio_account_fact_v3.py",
    "test/test_portfolio_account_fact_v3_producer.py",
    "test/test_kis_account_observation_input.py",
    # ★ Portfolio position provider-identity lineage transport.  Alpaca's
    #   exact /v2/positions asset_id is retained with its provider name;
    #   manual source pairs remain unverified/fail-closed.  This does not
    #   resolve canonical instruments or repair raw-symbol aggregation and
    #   cannot open sizing/action/order/trading authority.
    "test/test_portfolio_position_source_lineage.py",
    # ★ Identity Foundation -- identity/canonical_identity.py, the 4-layer
    #   (issuer/instrument/listing/source_asset_id) canonical security
    #   identity resolver + market-account-scope resolver. Ratifies NO real
    #   identity or scope edge (config/canonical_security_identity.json and
    #   config/market_account_scope_map.json ship with zero rows/edges --
    #   verified directly against the real files, not synthetic fixtures).
    #   Every layer goes through one shared gate requiring: exactly one
    #   active row, RATIFIED status, business-payload self-consistency,
    #   AND independent verification against a real external evidence file
    #   (real bytes hashed, content cross-checked including ratified_at --
    #   not a self-hash). first_seen_at is verified ONLY against real git
    #   commit history -- separately for the row's own content AND for the
    #   evidence file's own content (rev 3: closes a brand-new-evidence-
    #   file-with-backdated-ratified_at gap). There is no append-only
    #   registry escape hatch in this module -- it was removed entirely
    #   (rev 3) as a backdating bypass by construction; a real hash-chain
    #   store is explicitly deferred. Git-history lookups use each file's
    #   real repo-root-relative path (rev 3), not its basename, so this
    #   works for the real nested config/ files. real_usable_from =
    #   max(effective_from, ratified_at, verified_row_first_seen_at,
    #   verified_evidence_first_seen_at). All temporal comparisons go
    #   through a strict parser -- same-day mixed date/timestamp precision
    #   is NOT_COMPUTABLE_TIME_PRECISION, never guessed. Every public
    #   resolver validates the authority DOCUMENT itself (policy_version +
    #   required arrays) at entry regardless of file-load vs. direct
    #   injection. resolve_instrument_by_id/require_instrument_id verify
    #   the linked issuer through the same gate, not just the instrument
    #   row -- an orphan/PROVISIONAL/ambiguous issuer correctly blocks.
    #   The approval-evidence AND git-history bindings cover the FULL
    #   determining payload (business fields + rule_id/rule_version/
    #   approval_status/ratified_at/effective_from/effective_to), not just
    #   business-identity fields -- an already-expired RATIFIED row whose
    #   in-memory effective_to alone is mutated to null (evidence file,
    #   hash, and git first-seen all untouched) is blocked, never RESOLVED.
    #   Every public resolver also re-verifies its ENTIRE input document
    #   (not just the selected row) against BOTH the real disk bytes at
    #   `_source_path` AND the real git blob at a trusted commit (default:
    #   current HEAD, resolved live -- never read from the input document
    #   itself) -- deleting/inserting/reordering rows in the array (e.g.
    #   removing one of two conflicting AMBIGUOUS rows so the remaining,
    #   completely real row resolves alone) is blocked as
    #   IDENTITY_NOT_COMPUTABLE_DOCUMENT_TAMPERED before any row is even
    #   looked at, and a merely-dirty/uncommitted disk file (including a
    #   memory+disk co-tamper, or an uncommitted revert to an old real
    #   commit used as if current) is
    #   IDENTITY_NOT_COMPUTABLE_DOCUMENT_PROVENANCE_UNVERIFIED. An
    #   explicit, externally-pinned `trusted_commit` may be passed by a
    #   caller to legitimately trust a specific non-HEAD commit instead --
    #   but only a full immutable object id (exactly 40/64 lowercase hex
    #   chars, independently confirmed via `git rev-parse --verify
    #   <it>^{commit}` resolving to that exact same string): a branch,
    #   tag, `HEAD`, `HEAD~1`, or an abbreviated SHA is rejected outright.
    #   Under a pin, disk must match that commit's real git blob EXACT
    #   BYTE-FOR-BYTE (not a canonical-JSON-hash comparison) -- a
    #   whitespace/indentation-only disk edit is still rejected even
    #   though it would canonically hash the same.
    #   ⛔ not wired into Shadow Matrix, no Dynamic Clock timestamp change,
    #   no in-code mapping table, no hardcoded per-ticker/market
    #   special-casing, P8-13 not opened.
    "test/test_identity_foundation.py",
    # P5-06/P7-08 mechanical identity pilot: exact committed provider
    # contracts/observations -> 4 instruments + 3 explicit scope edges.
    # Identity only; no investability, entry, sizing, or trading authority.
    "test/test_identity_authority_pilot.py",
    # P0-2C-1: provider-authority mechanism layer -- whether a data
    # PROVIDER (e.g. KIS_PAPER_ACCOUNT) is a RATIFIED portfolio-fact
    # source is a separate fact from whether its API can be read.
    # Exactly one KIS PAPER balance-provider tuple is provenance-bound;
    # every other tuple remains closed. Identity only; no investability,
    # Stage, Buy, or Order authority.
    "test/test_data_provider_authority.py",
    # P0-2C-2: proves the EXISTING resolve_instrument_identity() safely
    # handles a KIS pdno source pair (kis_paper_domestic_balance + a
    # 6-digit code). Exactly 071050 resolves through the independently
    # reviewed common-share chain; every other KIS pdno remains closed.
    # Identity only; no investability, Stage, Buy, or Order authority.
    "test/test_kis_paper_source_identity_resolution.py",
    # P0-2C: KIS provider-authority + source-alias PROPOSED artifacts --
    # mechanical proposals only, mirroring candidate_identity_authority_
    # proposal.py's own proposal/ratify separation. Never touches
    # config/data_provider_authority.json or config/canonical_security_
    # identity.json. Evidence pinned to koreainvestment/open-trading-api
    # @b4e6249714418aa57833d1cbbbced39cbcc5b125 (commit SHA + file path +
    # content hash, never a bare mutable URL). No RATIFIED row anywhere.
    "test/test_kis_provenance_proposal.py",
    "test/test_kis_official_evidence_resolver.py",
    # 071050 proposal-only follow-up after the exact KIS PAPER provider tuple
    # was separately ratified. Two mechanically reviewable packets bind the
    # exact instrument/listing and exact KIS balance source pair without
    # mutating identity/provider authority config or granting money authority.
    "test/test_kis_071050_proposal.py",
    # CIO-ratified mechanical identity only: exact DART:00432102 ->
    # KRX:071050:COMMON -> XKRX:071050 ->
    # kis_paper_domestic_balance/071050 chain. Proposal artifacts stay
    # PROPOSED and every money/trading authority remains false.
    "test/test_kis_071050_identity_authority.py",
    "test/test_kis_realtime_trade_observation.py",
    # CIO-selected Option A calendar-source bridge. Exact retained KRX
    # observations may prove only an OPEN_REGULAR date; missing rows never
    # infer CLOSED and price/flow finality plus all money authority stay shut.
    "test/test_krx_post_close_session_calendar.py",
    # Official KRX Global [01023] calendar bridge for KIS PAPER quotes.
    # Exact response bytes and point-in-time availability are retained; listed
    # holidays/weekends close deterministically, while every money and order
    # authority remains shut. The test suite is offline against committed bytes.
    "test/test_krx_official_holiday_calendar.py",
    # P8-12 source lineage bridge: provider adapters preserve structured
    # source_name/source_asset_id through ClockEvent -> candidate without
    # resolving identity or changing tier/authority.
    "test/test_dynamic_clock_identity_lineage.py",
    # P5-06/P7-08 read-only candidate identity observation: validated
    # provider lineage -> RATIFIED canonical instrument/account scope only.
    # Candidate validity, entry, sizing and every money authority stay locked.
    "test/test_candidate_identity_observation.py",
    # P5-06 -> P7-08 -> P8-13 zero-capital review bridge: validates the
    # exact Dynamic Clock + canonical identity packets and surfaces a human
    # review disposition without opening validity, sizing, capital or trade
    # authority.  Re-signed output tamper is rejected by semantic rebuild.
    "test/test_shadow_entry_review.py",
    # P5-06/P7-08 -> P8-13 policy-readiness boundary: preserves every
    # zero-capital review observation while independently fixing executable
    # candidates, entry proposals and orders at zero. No policy numbers are
    # admitted; re-signed upstream/output tamper is rebuilt and rejected.
    "test/test_entry_policy_readiness.py",
    # P8-13 fail-closed human-review proposal boundary: carries forward only
    # diagnostic review material while entry zone, invalidation, risk, size,
    # quantity, proposal, order, capital and all execution authorities remain
    # structurally locked. Semantic rebuild rejects re-signed tamper.
    "test/test_entry_proposal_boundary.py",
    # P7-11 operational readiness bridge: revalidates exact P8-13 and the
    # committed 11-episode baseline, but keeps live position eligibility,
    # harvest review, quantity, reallocation, proposal and order at zero/null
    # while all policy and money authority remains unratified.
    "test/test_profit_harvest_operational_readiness.py",
    # P7-11 transition readiness: exact-type and transition-state validation
    # remains fail-closed with zero harvest/action/order authority.
    "test/test_profit_harvest_transition_readiness.py",
    # P7-10 Capital Reallocation readiness: independently revalidates the
    # exact P7-11 packet and exposes six missing authority/input axes. It has
    # no amount, proceeds, ranking, proposal, action, order or capital path.
    "test/test_capital_reallocation_readiness.py",
    # P5-06/P7-08 unresolved identity evidence inventory: exact validated
    # provider pairs are compared with the already-ratified taxonomy only as
    # diagnostic adjacency.  It creates zero authority rows and cannot open
    # candidate validity, entry eligibility, sizing, or a money action.
    "test/test_candidate_identity_gap_inventory.py",
    # P5-06/P7-08 non-authoritative identity proposal packet: an independently
    # rebuilt gap inventory plus exact RATIFIED taxonomy and Kraken catalog may
    # produce CIO-review rows only. It writes no canonical authority and opens
    # no candidate validity, entry eligibility, sizing, or money authority.
    "test/test_candidate_identity_authority_proposal.py",
    # ★ P5-06/P7-08 — cross-row audit of unratified identity proposals.
    #   Coherence is review material only and never creates authority.
    "test/test_candidate_identity_authority_review_inventory.py",
    # ★ Stage3 candidate evidence lifecycle — fail-closed candidate_stage_gate_input/1
    #   adapter and lifecycle receipt. Missing evidence stays MISSING/UNKNOWN; a system
    #   Candidate is never Stage4, order, or real-capital authority.
    "test/test_candidate_evidence_lifecycle_receipt.py",
    "test/test_candidate_stage_gate_input_adapter.py",
    # ★ P5-08 — Crypto Candidate Promotion Rule: TRADEABLE_UNIVERSE/
    #   PAPER_ELIGIBLE (P3-12) -> WATCH/FOCUSED_REVIEW/BLOCKED. Pure
    #   derivation over embedded, consumer-revalidated P3-12/P1-CR-08/
    #   P4-07/P1-CR-07 packets. Missing ratification or a missing required
    #   evidence leg remains UNKNOWN, so a real evaluation cannot reach
    #   FOCUSED_REVIEW today; only the pure transition-rule test does.
    #   No new capture/network call. Every authority field stays false.
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_capture_azure_fixture.py above so it is
    #      not silently hidden from the test-set comparison.
    "test/test_crypto_candidate_promotion.py",
    # ★ P5-08 contract/3 (opt-in; contract/2 default stays byte-identical):
    #   VOLUME_LIQUIDITY reads only the hash-bound RATIFIED P4-07 policy, and
    #   REGIME consumes the CRYPTO_PAPER_RUNTIME_V1 decision mapped through
    #   the PAPER-MARKET-ALLOCATION-V2 new-buy table (RISK_ON/NEUTRAL PASS,
    #   RISK_OFF/STRESS FAIL, UNKNOWN/missing/not-current UNKNOWN). Tests
    #   every regime state and the 2026-09-20 07:00Z transition day. State
    #   rule RULE.CRYPTO.CANDIDATE_PROMOTION_T2_REQUIRED6.V1 (user B2, record
    #   hash-bound): only the six T2 required conditions block; TREND/
    #   OVEREXTENSION record-only, RS score, P4-07 quality and material
    #   blocker warnings. Rotation membership is not wired -> UNKNOWN.
    "test/test_crypto_candidate_promotion_v3.py",
    "test/test_crypto_candidate_trend_metrics.py",
    "test/test_crypto_candidate_volume_metrics.py",
    # ★ P5-08 observation capability, deliberately unwired: the two
    #   price-distance measurements (close-to-EMA fraction, lagged close
    #   return fraction) the merged trend calculator never reported. Pure
    #   arithmetic over already-validated candles; status is only ever
    #   CALCULATED/UNAVAILABLE. No overextension predicate, bound or
    #   threshold is added -- evaluate_overextension stays UNKNOWN /
    #   NO_RATIFIED_OVEREXTENSION_THRESHOLD and U2 stays unresolved. No
    #   capture/network call; every authority field stays false.
    "test/test_crypto_candidate_price_distance_metrics.py",
    # ★ P5-09 — Crypto PAPER Buy Eligibility: FOCUSED_REVIEW (P5-08) ->
    #   WATCH/WAIT/BLOCKED/PAPER_BUY_ELIGIBLE. Pure derivation over an
    #   already-revalidated P5-08 promotion packet only. REGIME_PERMITS_
    #   ENTRY and the OVEREXTENSION leg of NO_BLOCKER_STALE_OVERHEAT_
    #   DUPLICATE echo already-published P1-CR-08/P5-08 UNKNOWN-by-
    #   construction boundaries, so a real evaluation cannot reach
    #   PAPER_BUY_ELIGIBLE today; only the synthetic/mocked reachability
    #   tests do. No new capture/network/order call. Every authority field
    #   stays false.
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_crypto_candidate_promotion.py above so
    #      it is not silently hidden from the test-set comparison.
    "test/test_crypto_paper_buy_eligibility.py",
    # ★ Crypto PAPER decision snapshot -- pure, read-only composition of
    #   P1-CR-08 (Regime axis adapter) + P5-08 (Candidate Promotion) +
    #   P5-09 (PAPER Buy Eligibility), wired onto the tail of the existing
    #   */30 * * * * upbit-realtime-capture.yml job (not a new schedule).
    #   Reads already-committed P3-12/P4-07 packets plus this run's own
    #   just-captured P9-06 realtime evidence (freshness/quote-state only,
    #   never fed into P5-08/P5-09's own derivation). Regime is UNKNOWN by
    #   construction today, so every real candidate caps at WATCH with
    #   paper_ready_count == 0 -- correct, honest current output. Zero
    #   network/order-endpoint calls; every authority field stays false.
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_crypto_paper_buy_eligibility.py above so
    #      it is not silently hidden from the test-set comparison.
    "test/test_crypto_paper_decision_snapshot.py",
    # ★ Hotfix 2026-09-15 -- leadership lineage manifests are verified in the
    #   capture-vintage folder raw/<as_of+1>/ (crypto_leadership.py convention).
    "test/test_crypto_leadership_manifest_vintage.py",
    # ★ P5-10 Crypto 5-axis entry/exit bridge -- the exact revalidated
    #   decision generation is projected into per-symbol entry and exit
    #   contexts. Missing axes or the unratified aggregate policy cap every
    #   new entry at WAIT (upstream BLOCKED stays BLOCKED); P7-13 hard-exit
    #   priority remains verbatim. No numeric threshold, order draft,
    #   network/exchange call, Production/Trading/REAL authority is added.
    "test/test_crypto_axis_trade_bridge.py",
    "test/test_crypto_axis_trade_bridge_explanation.py",
    # ★ P1-CR-08 Crypto live-component registry -- exact public natural
    #   BTC trend/risk, stablecoin and breadth rows, bound by point-in-time
    #   retained download cutoff plus full directory fingerprint. Evidence
    #   presence only; no axis interpretation, threshold, strategy, action,
    #   PAPER/exchange order, withdrawal, Production, Trading or REAL authority.
    "test/test_crypto_live_component_registry.py",
    # ★ W5-01/W5-02 (2026-09-14) Crypto decision-generation defects D1/D2.
    #   The unratified realtime gate overall_status STALE no longer blocks a
    #   scheduler decision packet (MISSING/CONNECTION/DATE_MISMATCH stay WAIT;
    #   cap_state_for_freshness is unchanged and still caps action state).
    #   The live component registry binds UTC-keyed sources to the UTC vintage
    #   date (schema /2); issued /1 records keep revalidating; absent sources
    #   stay absent. Crypto regime remains UNKNOWN; no new threshold/authority.
    "test/test_crypto_regime_vintage_d1_d2.py",
    # ★ 2026-09-14 Crypto capture-to-decision timing. The decision step runs
    #   directly after the P9-06 realtime capture and the ~30s decision-
    #   isolated validation capture runs after the decision chain, so the
    #   ratified 20s/3s CRYPTO freshness re-evaluation no longer sees a
    #   pipeline-added 30s age. A read-only guard fails over a 5s ENGINEERING
    #   budget (scheduler hand-off, not a freshness policy). No threshold,
    #   decision semantics or authority change.
    "test/test_crypto_decision_capture_timing.py",
    # ★ 2026-09-14 user ratification CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1
    #   (option B) + CIO companion liquidity decision. Realtime freshness is
    #   judged per market with the unchanged 20s/3s thresholds; a non-FRESH
    #   market caps only its own action state (aggregate is display only);
    #   the action set applies the ratified P3-12 30-day average turnover
    #   floor per market (CIO addendum; unknown excluded), the realtime
    #   subscription is every admitted P3-12 market with no holdings input
    #   (scope addendum), and held stale positions HOLD with a 30-minute
    #   engineering alert budget.
    #   Decision packets /1 before the effective instant keep revalidating.
    "test/test_crypto_realtime_per_market_freshness.py",
    # ★ CIO item 3 (2026-08-29): CRYPTO_BREADTH real coverage-ratio
    #   diagnostics (additive, never a new gate) and CRYPTO_LEADERSHIP's
    #   daily_orchestrator.py component-row wiring into build_packet(),
    #   proven against real committed evidence/crypto/breadth/raw dates.
    "test/test_crypto_breadth_leadership_axis_wiring_20260829.py",
    # ★ CIO item 4 (2026-08-29): portal-consumable Crypto candidate detail
    #   view -- pure composition over already-committed P3-12/P4-07/P5-08/
    #   P5-09 evidence (decision/crypto_paper_decision_snapshot.py's own
    #   committed packet, reused verbatim, plus P3-12/P4-07's own committed
    #   packets for markets it never reached). No new capture, no new
    #   threshold/trigger/invalidation price; a blocker_summary aggregation
    #   proven against a real committed packet and hand-built fixtures.
    "test/test_crypto_candidate_detail_view.py",
    # ★ P8-16 Crypto funnel briefing -- full-revalidation, read-only
    #   projection of one exact crypto_paper_decision_snapshot generation.
    #   JSON/API and deterministic Markdown share counts/reasons/freshness.
    #   Missing private P10/P7 position state is UNKNOWN/null, never a false
    #   zero and never private quantities/money copied to this public repo.
    #   All PAPER order/exchange/withdrawal/Production/Trading/REAL authority
    #   remains false.
    "test/test_crypto_funnel_briefing.py",
    # ★ P10-11 runtime bridge -- P9 retains only the latest accepted exact
    #   public ticker/orderbook bytes; a private runtime independently
    #   rederives the P5 decision before building any P10 request. A current
    #   book may support a new intent but only a later capture may match it.
    #   User-ratified economic inputs have no defaults. No credentials,
    #   exchange endpoints, or REAL authority are introduced.
    "test/test_crypto_paper_runtime_bridge.py",
    # ★ Per-market realtime freshness in the P10-11 bridge (user ratification
    #   CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914 + CIO addenda):
    #   request /3 judges each market by its own ratified freshness and floor
    #   cap on natural 2026-09-13 bytes; a stale/capped/missing-book market is
    #   its own blocker, never a whole-request abort; issued /2 requests keep
    #   rebuilding byte-identically. No order/exchange/REAL authority.
    "test/test_crypto_paper_runtime_bridge_per_market.py",
    # ★ Crypto PAPER wiring v2 (build plan PR3): decision snapshot /4 behind
    #   the config cutover T_cut (inactive by default, /3 byte-identical),
    #   promotion contract/3 rotation source, buy eligibility contract/3
    #   (session budget size, record-only features/planned loss, R1 key,
    #   07:00Z expiry), runtime request /4 (multi-candidate session budget,
    #   marketable limit + registry 150bp quantity reduction, market-state
    #   mapping, exit-intent sells). No order/exchange/REAL authority.
    "test/test_crypto_paper_wiring_v2.py",
    # ★ Wiring v2 follow-up: stale hold, rule lineage and funnel briefing
    #   accept decision /4 (additive, issued briefing contract/3 frozen); new
    #   decision packets are stamped at the first whole second no realtime
    #   input postdates (fixes REALTIME_*_FUTURE_DATED), /4 enforces it,
    #   committed packets replay byte-identically.
    "test/test_crypto_paper_wiring_v2_consumers.py",
    # ★ D1 per-market account marks (crypto_paper_account_state/2): a stale
    #   held market is valued UNKNOWN instead of freezing FRESH markets'
    #   exits; unknown NAV blocks new entries only. /1 unchanged.
    "test/test_crypto_paper_per_market_account_marks.py",
    # ★ US-DATA-1 item 1 (CIO 2026-09-13): US-U1 investable-universe T1
    #   display generator -- deterministic ETF/Test-Issue/Financial-Status
    #   flag filter + a documented, unratified Security-Name common/ADS
    #   pattern heuristic + a SEC company_tickers_exchange CIK presence
    #   cross-check over the already-published P3-02 us_global_universe
    #   packet. Every exclusion reason is counted and the pipeline fails
    #   closed unless kept+excluded reconciles to the source row count.
    #   t1_display_only=true, ratified=false on every row; no W2/W3/T2/T3
    #   authority and no trading/order/capital authority anywhere.
    "test/test_us_investable_universe_v1.py",
    # ★ US-DATA-1 item 2 (CIO 2026-09-13): US price-history backfill request
    #   planner (`collectors/us_price_history_backfill.py`). Reuses
    #   `fetch_alpaca_daily_bars` unmodified, chaining its fixed 180-day
    #   lookback into PIT-anchored HISTORICAL_BACKFILL windows (regime/
    #   us_historical_replay_population.py::replay_trend_source lookahead
    #   discipline). Pure-logic coverage: anchor chaining, batching,
    #   configurable pacing estimate, dry-run plan shape (zero network
    #   calls by default), and the live path exercised only through a
    #   synthetic in-memory getter that never touches urllib. Public repo
    #   boundary is enforced in code (`--out-dir` must resolve outside this
    #   repo) and asserted here. authority is false everywhere; no network
    #   call, no order/trading/capital authority anywhere in this file.
    "test/test_us_price_history_backfill.py",
    # ★ US_BACKFILL user approval (USER_RATIFICATION_CAPITAL_ROTATION_RULES_V1_
    #   20260915) + CLAUDE_CIO 2026-09-15: pre-registered US sector rotation
    #   event study re-run on the 1-year Alpaca backfill. Backfill live path is
    #   write-once/resumable and bounded (22 approved symbols, <=364 days,
    #   <=66 requests, pacing floor). Study is a line-by-line port of the
    #   pre-registered engine (hash-pinned document, frozen parameters/gates)
    #   and emits aggregate-only statistics; the artifact schema rejects any
    #   price/close/volume/bar/per-day return series. Workflow: dispatch only,
    #   contents: read, secrets only in the backfill step env, vendor rows only
    #   under RUNNER_TEMP, one aggregated JSON upload, nothing committed.
    #   Offline synthetic fixtures only; no network call.
    "test/test_us_sector_rotation_event_study.py",
    "test/test_us_sector_rotation_backfill_study_workflow.py",
    # ★ P0-06 consumer derivation-marker acceptance (CLAUDE_CIO 2026-09-14):
    #   _validate_pinned_delivery_packet's closed top-level field set predated
    #   the additive daily_orchestrator/6 packet fields
    #   (runtime_regime_readiness_version, flow_replay_version,
    #   crypto_derivation_version) and rejected every retained packet since
    #   2026-09-06 AM with DELIVERY_PACKET_FIELDS_MISMATCH. The consumer now
    #   allows exactly these three optional markers, each a plain int in its
    #   hard-coded supported set and accepted only under contract_version
    #   daily_orchestrator/6. Every retained /3-/6 packet.json under
    #   evidence/daily_briefing validates; every retained /2 packet still
    #   fails by design. No import of the orchestrator; no authority change.
    "test/test_briefing_consumer_derivation_markers_20260914.py",
    # ★ KR sector index history backfill + pre-registered 20-session rotation
    #   event study (CLAUDE_CIO 2026-09-15; user ratification KR = TEMPORARY
    #   until KRX sector index history is re-verified). Offline only: synthetic
    #   KRX index responses through an in-memory opener exercise the request
    #   budget (2 requests per requested weekday, hard caps), write-once
    #   resume, fail-closed stops on HTTP 401/403/429 and KRX error codes,
    #   response-decided sessions with official-calendar cross-checks, the
    #   pinned pre-registration hash, R1-k/R2/R3/R4/R5 mechanics and gates,
    #   and the aggregate-only public validator (no index values, no per-day
    #   sequences). The workflow test pins workflow_dispatch-only, contents:
    #   read, persist-credentials false, the KRX secret in one step env only,
    #   runner-temp private records and a tracked-change prohibition. No
    #   network call, no policy/ledger/order/trading authority.
    "test/test_kr_sector_index_history_backfill.py",
    "test/test_kr_rotation_event_study.py",
    "test/test_kr_sector_history_study_workflow.py",
    # ★ User-ratified capital rotation confirmation layer (CLAUDE_CIO 2026-09-15,
    #   USER_RATIFICATION_CAPITAL_ROTATION_RULES_V1_20260915 sha c6f5dbbe…):
    #   policy bound to the ratification record sha; STRONG_CONFIRMED/HELD/
    #   RELEASED/EMERGING_WATCH/NEUTRAL replayed from committed daily evidence
    #   (US SPDR 20-session, KR 1-session TEMPORARY with the 20-session switch
    #   refused, CRYPTO primary_30d); byte-deterministic, prefix-stable (no
    #   lookahead), append-only packets; T1/T2 C5/new-buy wiring uses only
    #   confirmed/held, release = new-buy stop only (no forced exit). Existing
    #   membership C5 and ledger/ratification contracts are asserted unchanged.
    "test/test_rotation_confirmation.py",
    "test/test_rotation_confirmation_wiring.py",
    # ★ PAPER entry opportunity ledger (RULE.ENTRY.PAPER_BASELINE_B.V1, user
    #   ratification 2026-09-15 sha b2a905c4…): every STRONG_CONFIRMED/HELD
    #   sector/bucket per day with point-in-time allocation v2 market-state
    #   verdict, T2 PENDING, record-only EMA20/breakout/ATR features from
    #   committed bars up to the session, null forward-return fields; final-day
    #   rule, byte-deterministic, append-only; chained workflow has no cron and
    #   no secret, and the sha-pinned source workflows stay untouched.
    "test/test_rotation_opportunity_ledger.py",
    # ★ #752 robustness (CLAUDE_CIO PAPER execution v1 build plan PR2): per-market
    #   build/verify isolation (exit 3), committed packets preferred so late older
    #   evidence is reported instead of replayed, workflow push retry, and a
    #   regression that post-session bars never enter record-only features.
    "test/test_rotation_confirmation_robustness.py",
    # ★ PAPER exit policy v1 (USER_RATIFICATION_PAPER_EXIT_PROVISIONAL_V1_20260915
    #   sha 47276abe…, observation-gap interpretation ed2ca92d…, D1 time contract
    #   10de02bf…): persistent paper_exit_intent/1 on confirmed release (survives
    #   the next day and restarts, append-only store), gap lapse = hold / new-buy
    #   stop / 판정 공백 with the first post-gap judgment deciding, crypto 21-day
    #   stop at the first FRESH decision snapshot, KR/US/crypto first allowed fill
    #   time; rotation policy v1 file unchanged (superseded via rule_refs only).
    "test/test_paper_exit_policy_v1.py",
    # ★ Record-only shadow controls (RULE.EXIT.SHADOW_CONTROLS.V1, exit study v2
    #   definitions): 1-B, TS14, PTP1, DS5 with the D9 monitored stop fill model
    #   and monitoring gaps; KR/US emitted NOT_DEFINED (P3 undecided).
    "test/test_paper_shadow_controls.py",
    # ★ Crypto rotation 30d strength one-time coverage recalculation
    #   (RULE.ROTATION.CRYPTO_30D_COVERAGE_RECALC_ONCE.V1, user ratification P1
    #   USER_RATIFICATION_PAPER_BUILD_PLAN_P1_P6_20260915 sha 2a94be2b…): write-once
    #   recalculated CR-06 points for days >= 2026-08-19 using confirmed later
    #   classifications (prices from the same as-captured snapshot), idempotent
    #   verify, '재계산' mark into rotation packets / entry gate / opportunity rows,
    #   committed packets preferred; regime LEADERSHIP axis, natural leadership
    #   packets and current_catalog_backfill_authorized untouched.
    "test/test_crypto_rotation_30d_coverage_recalc.py",
    # ★ Alpaca historical SIP daily-bar access probe (user approval
    #   2026-09-15: "Alpaca 과거 SIP 데이터 접근 확인 테스트 승인"). Answers, once,
    #   on request: can the existing dedicated ALPACA_MARKET_DATA_API_KEY/
    #   ALPACA_MARKET_DATA_API_SECRET credential read HISTORICAL SIP daily
    #   bars (feed=sip) outside the real-time SIP embargo, and how does SIP
    #   daily volume compare with IEX daily volume over the same window?
    #   Bounded to at most 6 requests to /v2/stocks/bars (multi-symbol):
    #   once with feed=sip and once with feed=iex over the SAME fixed
    #   10-session window ending >=2 days before the run, each with at most
    #   one retry on a transient (network/429/5xx) failure only -- a
    #   definitive 401/403 is never retried. Symbols are 3 approved
    #   config/free_market_data_contract.json alpaca.symbols (SPY/XLK/AAPL
    #   preferred; SPY/XLK/MSFT fallback since AAPL is not currently
    #   approved). Output is aggregate-only: per-request status/error
    #   class, whether SIP returned bars, bar counts, the SIP/IEX
    #   volume ratio and the (vwap*volume)/(close*volume) notional-ratio
    #   per symbol (median across the shared session window) -- never a
    #   per-day price/close/volume/vwap value. assert_no_forbidden_fields
    #   checks that mechanically before anything is written, and the
    #   workflow re-checks the written file the same way before upload.
    #   Workflow: dispatch only, contents: read, persist-credentials
    #   false, secrets in exactly one step env, aggregate JSON only under
    #   RUNNER_TEMP, nothing committed, tracked-change guard. Offline
    #   fixture/fake-HTTP-layer regression only; no network call from
    #   tests.
    "test/test_alpaca_sip_access_probe.py",
    "test/test_alpaca_sip_access_probe_workflow.py",
    # ★ US T2 C3 liquidity, RULE.LIQUIDITY.US_SIP_SOURCE.V1 (user
    #   ratification 2026-09-15, USER_RATIFICATION_US_LIQUIDITY_SIP_SOURCE_
    #   20260915 + base record PAPER-LIQUIDITY-KR-US-V1-20260914): Alpaca
    #   historical SIP daily bars (>=15 minutes past regular-session close
    #   only) for the 22 already-approved
    #   config/free_market_data_contract.json alpaca.symbols (per-symbol
    #   requests with bounded page_token pagination -- the multi-symbol
    #   endpoint silently dropped SPY/MSFT in the prior probe, run
    #   34907066300); feed=iex is the fallback only when SIP is denied/
    #   empty for a symbol. Public output is derived-only per symbol:
    #   20-session average traded value (close*volume), the selected
    #   feed's own last close (the one deliberate single-price exception,
    #   required by the ratified price-floor condition itself), session
    #   count, source feed, and the composite status plus its three
    #   sub-checks (volume_status/price_status/otc_exclusion_status) --
    #   never a raw open/high/low/close/volume/vwap/trade_count field.
    #   universe/us_liquidity_sip_source.py is the pure rule evaluator: SIP
    #   with a full window is authoritative (PASS/FAIL); IEX fallback is
    #   PASS or UNKNOWN, never FAIL; fewer than 20 sessions on every feed
    #   is NOT_EVALUATED (the base record's own vocabulary), not UNKNOWN.
    #   ★ 2026-09-15 correction: the ratified USD threshold ($10,000,000
    #   20-session average, $5 min close) is now BOUND via the committed
    #   config/us_liquidity_sip_source_policy.json, sha256-cross-checked
    #   against byte-identical copies at evidence/authority/
    #   paper_liquidity_kr_us_user_ratification_20260914.json and
    #   evidence/authority/us_liquidity_sip_source_user_ratification_
    #   20260915.json (both also landing via #753) -- load_policy() fails
    #   closed to None (every sub-check UNKNOWN) only if that policy file
    #   or either cited evidence file is missing/tampered, never by
    #   default. ★ 2026-09-15 wiring: otc_exclusion_status now comes from
    #   universe/us_listing_lookup.py, a point-in-time (never a later
    #   packet than the run's own as-of date, by directory scan -- no
    #   `latest` pointer needed) reader of the already-committed Nasdaq
    #   Trader Symbol Directory capture
    #   (data/observations/us_global_universe/<date>/packet.json,
    #   universe/us_global_universe.py + its own workflow, both untouched
    #   by this change). Presence in either captured file (nasdaq_listed
    #   or other_listed -- both exchange-listed-only directories, per
    #   config/us_breadth_forward_contract.json) -> EXCHANGE_LISTED; a
    #   confirmed Nasdaq "Test Issue"=Y row -> TEST_ISSUE (a distinct,
    #   evidence-backed exclusion this source CAN assert -- it structurally
    #   cannot assert "OTC" directly, since neither captured file ever
    #   contains an OTC security); absent from the selected packet, or no
    #   packet at all as-of the evaluation date -> UNKNOWN, never assumed.
    #   listing_packet_age_days is recorded only -- no staleness threshold
    #   is invented. Workflow: dispatch only (no cron -- scheduling needs
    #   separate approval), contents: write, secrets in exactly one step
    #   env, commits ONLY the two derived data paths, guarded on an actual
    #   staged diff. Offline mocked-HTTP/temp-fixture regression only; no
    #   network call from tests, and the real ~74MB committed packets are
    #   read (fast, ~0.2s) only by a couple of dedicated tests that verify
    #   the real wiring, never by the bulk of the suite.
    "test/test_us_liquidity_sip_source.py",
    "test/test_us_listing_lookup.py",
    "test/test_alpaca_sip_daily_bars.py",
    "test/test_alpaca_sip_daily_bars_workflow.py",
    # ★ Rule registry v1 + decision lineage (CLAUDE_CIO 2026-09-15, user
    #   ratification RULE-GOVERNANCE-EVIDENCE-GATED-ADJUSTMENT). Offline only:
    #   config/rule_registry_v1.json validates against byte-exact authority
    #   record copies (hash, ids, pointer-bound parameters/triggers, monotone
    #   versions); rule_refs / rule_lineage_event/1 tamper checks; additive
    #   sidecars for every committed crypto PAPER decision packet and PAPER
    #   reference packet reproduce each decision verbatim, lineage steps
    #   never raise and the packets still revalidate byte-for-byte.
    "test/test_rule_registry_and_lineage.py",
    # ★ PAPER execution core v1 (CLAUDE_CIO build plan PR1): pure library, no
    #   runtime wiring. Every ratified number resolves from
    #   config/rule_registry_v1.json through config/paper_execution_core_v1.json
    #   (sha-pinned); session budget (NAV0, Room/3, water-filling,
    #   session_budget_record/1, restart reuse), allocation envelope (v2 caps,
    #   D6 base-ratio reallocation, D5 reductions, UNKNOWN 2-cycle cap,
    #   combined-NAV drawdown without peak reset, KR STRESS fixture-only),
    #   DEXKOUS FX staleness, position episodes / D7 re-entry, D4 status
    #   vocabulary and delay-loss rows, D10 checklist, D11 scorecard + P6
    #   cooling-off. Undecided items are emitted as NOT_DEFINED.
    "test/test_paper_execution_core_v1.py",
    "test/test_paper_execution_core_v1_episodes_validation.py",
    # ★ RULE.NAV.KRW_USD_CONVERSION_FRED_DEXKOUS.V1 evidence capture
    #   (collectors/fred_dexkous_fx.py) + reader (latest_available).
    #   FRED_API_KEY-or-public-CSV fetch, append-only per-observation
    #   capture keyed by this run's own wall-clock time (availability_
    #   captured_at_utc), UNKNOWN_BACKFILL rows for the one-time historical
    #   seed never usable as point-in-time evidence, business-day staleness
    #   clock (CIO interpretation, > 10 business days -> 'NAV 일부 미검증'
    #   display only, never blocks allocation). Fully offline / mocked-HTTP;
    #   no network call, no trading/allocation authority (every
    #   *_authorized field stays False).
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_capture_azure_fixture.py above so it is
    #      not silently hidden from the test-set comparison.
    "test/test_fred_dexkous_fx.py",
    "test/test_fred_dexkous_fx_workflow.py",
    # ★ Evidence-loss guard for fred-dexkous-fx.yml's commit step
    #   (collectors/verify_evidence_staged.py). Added after a 2026-09-17/18
    #   investigation into an apparent FRED DEXKOUS FX observation gap that
    #   turned out to be a log-reading false alarm (test/test_fred_dexkous_fx.py's
    #   own offline end-to-end test prints a summary that looks like a real
    #   write because it hardcodes the fixture date "2026-09-15", but it
    #   runs against an isolated tempfile.TemporaryDirectory(), never the
    #   real checkout). The real gap the investigation surfaced: nothing
    #   would have caught it if a commit had genuinely dropped a file the
    #   collector reported writing -- this test proves that shape now goes
    #   red (exit 1) instead of green.
    #   ⛔ CI-only git-staging check; runs entirely inside a throwaway local
    #      `git init` repo it creates itself; no network, no trading/
    #      allocation authority, never touches the real evidence tree.
    "test/test_verify_evidence_staged.py",
    # ★ RULE.UNIVERSE.US_STOCK_SPDR_SECTOR_MAPPING.V1 evidence capture
    #   (collectors/spdr_sector_holdings.py) + reader
    #   (universe/us_spdr_sector_mapping.py). Daily holdings for the 11
    #   SPDR Select Sector ETFs (XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV
    #   XLY, verified against config/free_market_data_contract.json); the
    #   downloaded workbook itself is never committed (licensing) -- only a
    #   per-ETF derived symbol/weight-rank/weight-bucket mapping plus
    #   capture metadata/hash. CIO review 2026-09-15 (PR #761): the rule's
    #   cross-fund "largest weight ETF" tie-break is resolved from EXACT
    #   weights held only in memory at capture time, and only when a single
    #   run covers all 11 ETFs (a "complete batch") -- per symbol, only the
    #   outcome (primary_sector_etf / holder_etf_count / tie flag) is
    #   committed, never the exact weight. An incomplete batch (an ETF
    #   fetch failed) resolves nothing that day; the reader falls back to
    #   the most recent earlier complete batch rather than trust a partial
    #   one. UNKNOWN (no T2) for an unheld symbol, own-sector for a sector
    #   ETF, and NO_POINT_IN_TIME_CAPTURE_AVAILABLE (distinct from UNKNOWN)
    #   when no complete capture yet exists -- holdings history is
    #   physically time-gated and cannot be backfilled. Fully offline: a
    #   small in-memory fixture .xlsx workbook and a fake HTTP layer only;
    #   the real SSGA endpoint is never contacted by this suite, and the
    #   untrusted workflow_dispatch ticker-list input is passed through
    #   env:/a quoted shell variable, never substituted directly into the
    #   run: script, then split and validated against the 11-ticker
    #   allowlist in Python before any HTTP request. Every *_authorized
    #   field stays False.
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_capture_azure_fixture.py above so it is
    #      not silently hidden from the test-set comparison.
    "test/test_spdr_sector_holdings.py",
    "test/test_spdr_sector_holdings_workflow.py",
    "test/test_us_spdr_sector_mapping.py",
    # ★ Macro event calendar (CIO decision 2026-09-16) --
    #   collectors/macro_event_calendar.py. Evidence capture only: US FOMC
    #   decision dates (Federal Reserve's own calendar page), US CPI
    #   releases and nonfarm payrolls (BLS "Schedule of Releases" tables for
    #   cpi.htm/empsit.htm), and Bank of Korea rate decisions (BOK "Meeting
    #   Dates" page) -- each fetched and PARSED from its own official page,
    #   never a hand-written date table. FOMC status (scheduled/released) is
    #   SOURCE-STATED (a posted statement link, cross-checked against its
    #   own embedded date); CPI/NFP/BOK carry no such marker on their pages
    #   so status there is a coarse CIO clock inference, explicitly tagged
    #   status_basis so the two are never confused. Bounded capture window
    #   (like fred_dexkous_fx.py's RECENT_WINDOW_DAYS) keeps a normal run
    #   from re-parsing a decade of FOMC/BOK history; append-only,
    #   content-addressed observations (state_hash over status/time/
    #   timezone/detail) mean an unchanged re-observation is a no-op and
    #   only a genuine change (typically scheduled -> released) writes a
    #   new file. This module opens no trading/direction/risk-day/buy-pause
    #   authority (every *_authorized field stays False) and is not
    #   imported by any briefing, decision, rule, or execution path in this
    #   PR. Fully offline / fixture HTML only; no network call is ever made
    #   by these two files. BLS's own bot manager blocks this dev sandbox's
    #   IP outright (confirmed 2026-09-18); real GitHub Actions runner
    #   reachability is UNVERIFIED until the workflow's first live run,
    #   exactly like spdr_sector_holdings.py's URL template was.
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_capture_azure_fixture.py above so it is
    #      not silently hidden from the test-set comparison.
    "test/test_macro_event_calendar.py",
    "test/test_macro_event_calendar_workflow.py",
    # ★ TKT-2 (W1) KR full-universe daily price history (#718): market-agnostic
    #   price_history_session/1 contract, KR collector (no default opener, no
    #   collection before next-morning publication, calendar-only session
    #   selection, EMPTY never stored as data but repairable to OK), store
    #   reader + calendar window, optional evaluator input with a
    #   PRIVATE_ONLY public-write guard. Zero price bytes tracked publicly.
    "test/test_krx_price_history.py",
    "test/test_korea_population_price_history_input.py",
    # ★ KR T2 C3 liquidity evaluator: thresholds read from the sha-verified
    #   evidence/authority/paper_liquidity_kr_us_user_ratification_20260914.json
    #   (no number in code); window = calendar's last 20 sessions ending at the
    #   required session; any missing/EMPTY session, gap or flag gap -> UNKNOWN;
    #   NOT_EVALUATED only for <20 sessions of listing history; per-symbol
    #   results private, public summary counts only.
    "test/test_kr_liquidity_c3.py",
    # ★ CIO 확정 2026-09-18 (CLAUDE_CIO_ADVERSE_DISCLOSURE_CARD_20260916.md ·
    #   USER_RATIFICATION_DECISION_BUNDLE_20260918.json 항목
    #   2_adverse_disclosure) — collectors/dart.py 의 KEYWORDS 를 악재성
    #   공시(Group A: 상장폐지·정리매매·감사의견거절/부적정/한정·회생·파산·
    #   횡령·배임, Group B: 불성실공시법인·최대주주변경·경영권분쟁)까지
    #   확대하고, 매칭 제목에 사실 기반 group(A/B/C) + matched_keyword 를
    #   붙인다. 기존 Group C 7종은 그대로 유지(하위호환). 두 그룹 동시
    #   매칭은 A>B>C 우선순위로 결정론적으로 정한다. 매칭 실패는 "C"로
    #   조용히 떨어지지 않고 명시적으로 미분류(None)다.
    #   ⛔ 매도/매수 차단 등 조치는 이 커밋에 없다 — 수집·분류만 한다.
    #      runtime/decision/portfolio 모듈 미변경. 관리종목·투자경고·
    #      단기과열·거래정지는 KIS 종목 마스터 전용으로 남겨 중복 수집하지
    #      않는다. live DART API 호출 없음 — fixture 제목만 오프라인 검증.
    "test/test_dart_adverse_filing_classification.py",
    # ★ Benchmark ("simply bought and held") NAV series
    #   (validation/paper_benchmark_nav_series.py +
    #   config/paper_benchmark_nav_series_policy.json). Unblocks checkpoint B
    #   (day 30) stop rules 1 (비용 차감 후 그냥 보유보다 낮다) and 5 (하락
    #   구간에서 그냥 보유보다 더 깎였다), neither of which was computable:
    #   validation/crypto_paper_counterfactual.py's only counterfactual is
    #   no_trade_benchmark_pnl = "0", which is not holding. The anchor is the
    #   product: anchor_utc is derived from the ledger's first FILL_APPLIED
    #   event (a supplied value is only ever compared), the anchor price must
    #   already have existed at that instant within the RATIFIED Upbit
    #   orderbook staleness window, two eligible prices refuse as ambiguous,
    #   the record must be written within one decision cycle of the fill, and
    #   the pointer is created with open(..., "x") so a second different
    #   anchor refuses. Both benchmark variants (EXPOSURE_MATCHED comparable
    #   with the account's total NAV, ASSET_ONLY the sleeve alone) are emitted
    #   and NEITHER is a verdict -- which one binds the stop rules is
    #   RATIFICATION_VARIANT_BINDING. CIO decision 2026-09-18 (option c, card
    #   CLAUDE_CIO_DECISION_BENCHMARK_NOTIONAL_BASIS_20260918.md): both notional
    #   bases are emitted from ONE anchor, so four named series --
    #   {FLAT_BASE_SHARE, MULTIPLIER_MATCHED} x {EXPOSURE_MATCHED, ASSET_ONLY}.
    #   The mapping (rule 1 -> flat, rule 5 -> multiplier-matched) is
    #   declared_stop_rule_binding in the policy, RATIFIED 2026-09-18 by the
    #   user's own record (evidence/authority/
    #   USER_RATIFICATION_BENCHMARK_NOTIONAL_BASIS_20260918.json, sha256
    #   ae04aea2...) which load_policy resolves and HASHES rather than trusting
    #   as a string -- a policy that claims a binding the record does not say is
    #   refused. It is copied into every anchor and series record, so it cannot
    #   be chosen at day 30 to suit the result. Ratifying the binding is NOT
    #   authority to publish a verdict: verdict_authorized stays false, every
    #   verdict stays NOT_EMITTED_RATIFICATION_REQUIRED, and the two disclosed
    #   residuals (RATIFICATION_LEDGER_ATTESTATION,
    #   RATIFICATION_CLOCK_ATTESTATION) stay open -- a policy marking either
    #   resolved is refused.
    #   The market state at the anchoring fill enters through exactly ONE named
    #   function (read_market_state) with a documented contract and NO path of
    #   this module's own -- the state-multiplier wiring has not settled on an
    #   artifact yet (RATIFICATION_MARKET_STATE_SOURCE_BINDING). UNKNOWN at the
    #   anchoring fill refuses outright rather than taking 0.50 from its ratified
    #   sentence; RISK_OFF/STRESS refuse as states that deny new buys; an absent,
    #   future or stale state (beyond the ratified crypto observation gap) refuses
    #   rather than assuming RISK_ON. Fee rate and entry slippage come off the
    #   account's own first fill (the simulator has no repository default for
    #   fee); no cost constant is invented here. Fail closed: a missing mark
    #   at a sample, an off-grid mark, a gap wider than the ratified rotation
    #   gap, a null NAV. Review 2026-09-18 closed three forgery gaps, each with
    #   its own regression: the ledger must be recovered from its published
    #   append-only snapshot store and matched to a genesis pin (a bare
    #   hash-consistent dict is refused), recorded_at_utc is bounded by an
    #   independently observed post-fill clock witness instead of being taken on
    #   trust, and the binding is read back out of append-only bindings markers
    #   plus the content-addressed records, so deleting the pointer file no
    #   longer lets a second anchor bind. Fully offline -- ledgers are built by the P10-11
    #   simulator's own builders, prices are fixtures, no network and no
    #   evidence directory outside a temporary one. Invoked by no workflow or
    #   schedule in THIS repo (a test asserts that, and that the CLI is
    #   dispatch-only). Its one caller is the private crypto PAPER runtime,
    #   which derives the anchor after its own restart-verified ledger write and
    #   cannot let a benchmark failure touch the fill; the former
    #   test_this_module_is_wired_into_no_workflow was replaced by the three
    #   properties that actually hold (no public caller; no anchor before a
    #   fill; one binding per account, a second different anchor refuses).
    #   Every *_authorized field stays False.
    #   ⛔ CIO has not approved this file itself yet -- registered per the
    #      same convention as test_capture_azure_fixture.py above so it is
    #      not silently hidden from the test-set comparison.
    "test/test_paper_benchmark_nav_series.py",
    # 일일 산출물 정체 감시(watchdog/daily_producer_freshness.py) — 감시 대상
    #   11개 산출물에 대해 "우리가 보유한 최신 관측일"과 "원천이 스스로
    #   제공한다고 밝힌 최신일" 두 값을 각각 기록하고 그 쌍으로 판정한다.
    #   원천 최신일은 이미 커밋된 증거에서만 읽는다(raw manifest 의
    #   observation_date_range 끝, venue manifest 의 latest_finalized_day,
    #   산출물이 스스로 입력으로 지목한 상류 producer 의 최신 날짜 디렉터리).
    #   네트워크 호출·신규 수집 출처 추가 없음.
    #   COLLECTION_BEHIND_SOURCE = 원천이 더 최신을 제공하는데 우리가 놓친
    #   경우로 가장 큰 경보(일정 축이 FRESH 여도 검사한다). 반대로
    #   SOURCE_NOT_YET_PUBLISHED 는 원천이 아직 발표하지 않은 정상 상태이므로
    #   경보가 아니다 — 2026-09-11 에서 멈춘 fred_dexkous_fx 를 3일치 환율
    #   관측 유실로 잘못 보고한 오경보를 이 구분이 철회한다.
    #   원천 최신일을 확보할 수 없으면 SOURCE_LATEST_UNKNOWN 이라는 독립
    #   상태로 남긴다 — "정상"으로도 "정체"로도 접어넣지 않고, 값을 임의로
    #   만들어 채우지도 않는다.
    #   ⛔ 읽기 전용 관측만 한다 — data/·evidence/ 기록 없음, workflow 는
    #      dispatch 전용(schedule 트리거 없음)이고 git commit/push 단계도
    #      없다. authority 는 read_only_watch 를 제외하고 전부 false 이며
    #      주문·매매·자본 배분 권한은 열리지 않는다. 오프라인 fixture 와 이
    #      저장소에 이미 커밋된 KRX 공식 휴장 capture 만 사용한다.
    "test/test_daily_producer_freshness_watchdog.py",
    # ★ Class-wide guard: every workflow checkout that feeds a real
    #   git-history-walking consumer (first-seen/tamper verdicts via
    #   `git log`/`git show`/`git merge-base`) must use `fetch-depth: 0`.
    #   Closes the btc-price-capture.yml gap (2026-09-18 review of PR
    #   #817's docs/do_not_touch_and_why.md): that workflow already had the
    #   correct fetch-depth: 0, but no test asserted it, unlike
    #   actions-pass.yml's regression job. Discovery of "which jobs" is
    #   automatic (walks every workflow's run: text); the registry of
    #   "which scripts actually walk history" is a hand-verified allowlist
    #   that fails closed if a new git-history consumer anywhere in the
    #   repository is not registered in it.
    #   ⛔ Read-only: parses workflow YAML and greps repository .py files;
    #      no network, no git history mutation, no authority.
    "test/test_workflow_history_checkout_depth.py",
]

FI_SUITE = "test/test_fault_injection.py"

# ══════════════════════════════════════════════════════════════════════
# ★ opt-in 결정론적 회귀 shard (CIO 채택 2026-09-07).
#   ⛔ 기본 동작은 바뀌지 않는다 — shard 를 요청하지 않으면 지금까지와 똑같은
#      단일 full 회귀다. shard 는 **회귀 대상 선택**만 바꾼다.
#   각 shard 는 자기 clean checkout 에서 사본 보존 · builder 직렬 재빌드 ·
#   byte 비교 · authority 경계 · FI suite 전량을 똑같이 수행한다. 한쪽 shard 의
#   성공은 부분 증거일 뿐이고, 최종 Actions 판정은 두 shard 를 모두 요구하는
#   aggregate job 이 한다 (runner 는 부분 실행에서 전체 PASS 를 주장하지 않는다).
REGRESSION_SHARD_COUNT = 2

# ★ 이미 기록된 실행 시간(초)만 균형 **추정**에 쓴다 — 새 benchmark 를 돌리지 않는다.
#   출처: PR614_PARITY_TIMING_COMPARISON.json 의 US run. US run 이 완주하지 못한
#   구간(candidate_identity_authority_review_inventory 이후)은 같은 파일의 common
#   run 값을 그대로 쓴다 — 보수적(과소) 추정이라 균형만 조금 나빠진다.
#   ⛔ 이 표는 가중치일 뿐 권위가 아니다. 회귀 population 의 권위는 언제나
#      APPROVED_TESTS 다. 표가 낡거나 모듈이 빠져도 완전성·중복없음·disjoint 는
#      깨지지 않는다 (여기 없는 모듈은 DEFAULT_ESTIMATED_SECONDS 를 받는다).
REGRESSION_ESTIMATED_SECONDS = {
    "test/test_daily_orchestrator.py": 1294.7,
    "test/test_dynamic_clock_end_to_end.py": 270.0,
    "test/test_regime_policy_calibration_readiness.py": 260.7,
    "test/test_us_forward_universe_populate.py": 204.0,
    "test/test_dynamic_clock_orchestrator_defects.py": 131.3,
    "test/test_candidate_lifecycle_observation.py": 119.2,
    "test/test_briefing_validator.py": 114.8,
    "test/test_candidate_identity_authority_proposal.py": 112.4,
    "test/test_global_asset_master_population_readiness.py": 90.7,
    "test/test_capital_reallocation_readiness.py": 63.4,
    "test/test_shadow_entry_review.py": 62.5,
    "test/test_profit_harvest_operational_readiness.py": 51.1,
    "test/test_dynamic_clock_operational_evaluation_time.py": 48.2,
    "test/test_daily_briefing_delivery.py": 46.7,
    "test/test_crypto_breadth_leadership_axis_wiring_20260829.py": 41.7,
    "test/test_portfolio_account_fact_v3_producer.py": 39.6,
    "test/test_entry_proposal_boundary.py": 38.3,
    "test/test_crypto_live_component_registry.py": 36.5,
    "test/test_crypto_regime_vintage_d1_d2.py": 30.0,
    "test/test_profit_harvest_population.py": 34.3,
    "test/test_profit_harvest_end_to_end.py": 33.6,
    "test/test_pit_replay_end_to_end.py": 33.4,
    "test/test_candidate_identity_authority_review_inventory.py": 32.1,
    "test/test_crypto_axis_trade_bridge_explanation.py": 31.2,
    "test/test_crypto_axis_trade_bridge.py": 30.5,
    "test/test_candidate_identity_observation.py": 26.6,
    "test/test_entry_policy_readiness.py": 25.9,
    "test/test_candidate_lifecycle_evidence_inventory.py": 24.5,
    "test/test_candidate_validity_shadow_observation.py": 24.4,
    "test/test_rotation_discovery_briefing.py": 24.4,
    "test/test_dynamic_clock_identity_lineage.py": 23.2,
    "test/test_population_symbol_observation.py": 60.0,
    "test/test_population_observation_daily_schedule.py": 20.0,
    "test/test_three_market_evaluation_coverage.py": 60.0,
    "test/test_market_candidate_discovery_lookup.py": 120.0,
}
DEFAULT_ESTIMATED_SECONDS = 1.0


def _partition_by_estimated_load(population, count):
    """canonical 결정론적 partition — 순서보존 · 중복없음 · disjoint · 합집합 == population.

    ★ `regression_shards()`(CIO 채택 2026-09-07, 2-shard 고정) 와
      `ci_phase_regression_shards()`(CIO CI-sharding 지시 2026-09-12, N-shard) 가
      **같은 알고리즘 하나**를 공유한다 — 두 번째 partition 구현을 새로 만들지 않는다.
    ★ 배정은 기록된 추정 시간 내림차순 greedy(동률은 선언 순서)라 같은 입력이면
      항상 같은 결과가 나온다. 각 shard 안의 상대 순서는 선언 순서 그대로다.
    ⛔ 비거나 중복된 population, 혹은 count < 1 은 여기서 예외로 막는다 —
       자식 프로세스를 하나라도 실행하기 전이다.
    """
    if not population:
        raise ValueError("승인 회귀 목록이 비어 있다 — shard 를 만들 수 없다")
    duplicates = sorted({t for t in population if population.count(t) > 1})
    if duplicates:
        raise ValueError(f"승인 회귀 목록에 중복이 있다: {duplicates}")
    if count < 1:
        raise ValueError(f"shard 수는 1 이상이어야 한다: {count!r}")
    if len(population) < count:
        raise ValueError(f"승인 회귀 {len(population)}건으로는 {count} shard 를 채울 수 없다")

    declared = {t: i for i, t in enumerate(population)}
    load = [0.0] * count
    assigned = [[] for _ in range(count)]
    for test in sorted(population,
                       key=lambda t: (-REGRESSION_ESTIMATED_SECONDS.get(t, DEFAULT_ESTIMATED_SECONDS),
                                      declared[t])):
        target = min(range(count), key=lambda s: (load[s], s))
        load[target] += REGRESSION_ESTIMATED_SECONDS.get(test, DEFAULT_ESTIMATED_SECONDS)
        assigned[target].append(test)
    shards = [sorted(chunk, key=lambda t: declared[t]) for chunk in assigned]

    # 분할 자체를 다시 증명한다 — 누락 · 중복 · 빈 shard 는 전부 fail-closed.
    flat = [t for chunk in shards for t in chunk]
    if sorted(flat) != sorted(population) or len(flat) != len(set(flat)):
        raise ValueError("shard 합집합이 승인 회귀 전량과 다르다")
    for i, chunk in enumerate(shards, 1):
        if not chunk:
            raise ValueError(f"shard {i}/{count} 가 비어 있다")
    return shards


def regression_shards(tests=None, count=REGRESSION_SHARD_COUNT):
    """현재 승인 회귀 목록을 순서보존 · 중복없음 · disjoint shard 로 나눈다.

    ★ 합집합은 **언제나** 전체 승인 목록과 정확히 같다. 개수나 부분집합을
      고정하지 않는다 — population 은 호출 시점의 APPROVED_TESTS 다.
    ⛔ CIO 채택 2026-09-07 로 지원 shard 수는 {REGRESSION_SHARD_COUNT} 로 고정이다 —
       `us-paper-market-data-contract.yml` 이 이 고정 계약에 의존한다. 임의 count 가
       필요하면 `ci_phase_regression_shards()` 를 쓴다 (actions-pass.yml 4-way matrix).
    """
    population = list(APPROVED_TESTS if tests is None else tests)
    if count != REGRESSION_SHARD_COUNT:
        raise ValueError(f"지원하는 shard 수는 {REGRESSION_SHARD_COUNT} 뿐이다: {count!r}")
    return _partition_by_estimated_load(population, count)


def ci_phase_regression_shards(count, tests=None):
    """`--phase regression --shard-count N` 전용 N-way 분할 (CIO CI-sharding 지시
    2026-09-12). `regression_shards()` 와 같은 canonical greedy 알고리즘을 공유하되
    2-shard 고정 제약이 없다 — count>=1 이면 무엇이든 받는다. 나머지 불변식
    (순서보존 · 중복없음 · disjoint · 합집합 == APPROVED_TESTS) 은
    `_partition_by_estimated_load` 가 전부 증명한다.
    """
    population = list(APPROVED_TESTS if tests is None else tests)
    return _partition_by_estimated_load(population, count)


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


# ══════════════════════════════════════════════════════════════════════
# ★ checkout 완전성 게이트 — 회귀 2026-09-18: shallow clone 과 sparse checkout
#   이 둘 다 "저장소가 깨졌다"처럼 읽히는 실패를 냈다 (KNOWLEDGE_PROVENANCE_
#   SHALLOW_HISTORY 는 traceback 150줄 뒤에야 나오는 provenance guard, sparse
#   는 REFERENCE_REDERIVATION_MISMATCH). 둘 다 실제로는 checkout 문제였다.
#   이 게이트는 그 두 guard 를 대체하거나 약화하지 않는다 — 어떤 test 파일보다
#   먼저, 더 이르고 더 명확하게 "checkout 이 문제다" 라고 말하는 신호를 하나
#   추가할 뿐이다. 기존 guard 는 그대로 남는다 (이 게이트가 못 잡는 경우를
#   위한 것이다).
#
#   판정 순서는 항상 absence 먼저다: 커밋이 없다 -> 트리가 잘렸다 -> 파일이
#   없다. "있는데 내용이 다르다" 는 이 게이트의 영역이 아니다 — 그건 real
#   finding 이고 기존 guard(KNOWLEDGE_PROVENANCE_SHALLOW_HISTORY,
#   REFERENCE_REDERIVATION_MISMATCH 등)가 계속 담당한다.
#
#   ⛔ git 이 아예 없거나 ROOT 가 git 저장소가 아니면 이 게이트는 아무 것도
#      판정하지 않는다 (조용히 통과) — test/test_fault_injection.py 의 FI
#      clone() 이 정확히 이 모양이다: rules/test/config 만 사본으로 뜬 임시
#      디렉터리이고 `.git`이 없다. 그건 "불완전한 checkout" 이 아니라 FI
#      suite 가 의도적으로 만든 격리된 사본이다 — 이 게이트의 대상이 아니다.
REQUIRED_EVIDENCE_ROOTS = [
    # ★ 코드로 추적된 것 — 위시리스트가 아니다.
    #   test/test_paper_regime_reference.py (APPROVED_TESTS 소속) 는
    #   regime/paper_regime_reference.build_reference() 를 root 인자 없이
    #   호출한다. 그 함수의 root 기본값은 이 checkout 자신이다 (tmp 사본이
    #   아니다). build_reference() -> build_crypto() 는
    #   evidence/crypto/btc/raw/<as_of_date>/_manifest.json 을 읽어
    #   crypto_descriptive_normalization_sources 를 만들고,
    #   validate_reference() 가 그 결과를 committed packet 과 재파생
    #   비교한다. 이 디렉터리가 sparse 로 잘려 나가면 건드린 파일이 하나도
    #   없어도 그 비교가 REFERENCE_REDERIVATION_MISMATCH 로 깨진다
    #   (2026-09-18 증명, symlink farm 로 evidence/crypto/btc/raw 하나만
    #   제외해 재현).
    #   ⛔ 날짜 하위 디렉터리(예: .../2026-09-18)는 매일 롤오버되므로 여기
    #      넣지 않는다 — 부모 디렉터리 자체의 존재/비어있지-않음만 본다.
    #      그래서 이 목록은 스스로 시한폭탄이 되지 않는다.
    "evidence/crypto/btc/raw",
]


def checkout_completeness_problems():
    """이 checkout 이 회귀 스위트가 요구하는 완전한 트리인지 — 실제 git 저장소일
    때만 판정한다. 문제가 있으면 human-readable 문장 리스트를 돌려준다."""
    try:
        shallow_probe = subprocess.run(
            ["git", "rev-parse", "--is-shallow-repository"],
            cwd=ROOT, capture_output=True, text=True)
    except FileNotFoundError:
        return []     # git 이 없다 — 이 게이트는 판정하지 않는다
    if shallow_probe.returncode != 0:
        # ROOT 가 git 저장소가 아니다 (예: FI suite 의 격리된 사본). 이 게이트는
        # 실제 checkout 을 위한 것이지, git 이 아닌 사본을 판정하지 않는다.
        return []

    problems = []

    # 1) shallow history — commit 이 없다.
    if shallow_probe.stdout.strip() == "true":
        problems.append(
            "shallow clone 이다 (git rev-parse --is-shallow-repository == true). "
            "고치는 법: git fetch --unshallow (또는 전체 히스토리로 다시 clone).")

    # 2) sparse / partial checkout — 트리가 잘렸다. 어떻게 만들어졌든 잡는다:
    #    actions/checkout 의 sparse-checkout 옵션은 조용히 partial clone
    #    (blob:none) 을 같이 걸기 때문에, 평범해 보이는 checkout 이 실제로는
    #    부분본일 수 있다.
    signals = []
    try:
        sparse_cfg = subprocess.run(
            ["git", "config", "--bool", "core.sparseCheckout"],
            cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        sparse_cfg = ""
    if sparse_cfg == "true":
        signals.append("core.sparseCheckout=true")
    try:
        sparse_list = subprocess.run(
            ["git", "sparse-checkout", "list"],
            cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        sparse_list = ""
    if sparse_list:
        signals.append("git sparse-checkout list 가 비어 있지 않다")
    try:
        partial_filter = subprocess.run(
            ["git", "config", "remote.origin.partialclonefilter"],
            cwd=ROOT, capture_output=True, text=True).stdout.strip()
    except FileNotFoundError:
        partial_filter = ""
    if partial_filter:
        signals.append(f"remote.origin.partialclonefilter={partial_filter}")
    if signals:
        problems.append(
            "sparse/partial checkout 이다 (" + ", ".join(signals) + "). "
            "고치는 법: git sparse-checkout disable 로 전체 트리를 복원하거나, "
            "sparse-checkout/partial-clone 옵션 없이 다시 clone.")

    # 3) 승인 회귀가 이 checkout 의 실제 ROOT 에서 읽는 evidence 루트가 실제로
    #    있고 비어 있지 않은가 — sparse 신호가 (2) 로 안 잡히는 경우까지
    #    대비한 방어선이다(예: git 메타데이터를 안 건드리고 디렉터리만 지운
    #    사본). 날짜 하위 디렉터리는 절대 요구하지 않는다.
    for rel in REQUIRED_EVIDENCE_ROOTS:
        path = os.path.join(ROOT, rel)
        if not os.path.isdir(path):
            problems.append(
                f"필요한 evidence 디렉터리가 checkout 에 없다: {rel}. "
                "고치는 법: sparse-checkout 없이 다시 clone하거나 "
                "git sparse-checkout disable 로 전체 트리를 복원.")
        elif not os.listdir(path):
            problems.append(
                f"필요한 evidence 디렉터리가 비어 있다: {rel}. "
                "고치는 법: sparse-checkout 없이 다시 clone하거나 "
                "git sparse-checkout disable 로 전체 트리를 복원.")
    return problems


def verify_checkout_completeness():
    """어떤 test 파일보다 먼저, 딱 한 번 실행한다. 불완전한 checkout 을 저장소
    결함처럼 보이는 실패로 마스커레이드하게 두지 않고, 여기서 먼저 명확하게
    말한다. 문제가 없으면 아무 것도 출력하지 않고 조용히 돌아간다."""
    problems = checkout_completeness_problems()
    if not problems:
        return None
    print("⛔ CHECKOUT INCOMPLETE — this is not a repository defect.")
    print()
    print("main is fine. Your checkout of it is not — it is missing history")
    print("or files this suite reads. Do not file this as a broken-main")
    print("incident before fixing the checkout:")
    print()
    print("⛔ 사본이 불완전합니다 — 저장소 결함이 아닙니다.")
    print()
    print("main은 멀쩡하고, 문제는 당신이 받아온 사본입니다. 이 사본에는 검사가")
    print("읽어야 할 이력이나 파일이 빠져 있습니다. 사본을 고치기 전에")
    print('"main이 깨졌다"고 올리지 마십시오.')
    print()
    for p in problems:
        print("  •", p)
    print()
    print("Fix: git fetch --unshallow, or re-clone with full history and")
    print("no sparse-checkout, then re-run.")
    print("고치는 법: git fetch --unshallow, 또는 전체 이력으로 sparse-checkout 없이")
    print("다시 복제한 뒤 재실행하십시오.")
    return 1


SNAPSHOT_DIR = "_committed_snapshot"


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def redact_diagnostics(text):
    """Do not persist credential environment values or common credential forms."""
    for key, value in os.environ.items():
        if value and re.search(r"TOKEN|SECRET|PASSWORD|CREDENTIAL|PRIVATE_KEY|API_KEY", key, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)(authorization\s*[:=]\s*(?:bearer|basic)\s+)\S+",
                  r"\1[REDACTED]", text)
    text = re.sub(r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+)\b",
                  "[REDACTED]", text)
    text = re.sub(r"-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----.*?-----END (?:[A-Z]+ )?PRIVATE KEY-----",
                  "[REDACTED PRIVATE KEY]", text, flags=re.S)
    return text


def failure_summary(text):
    # Keep every unittest testcase header plus the useful end of each traceback.
    blocks = re.split(r"(?m)(?=^(?:ERROR|FAIL): )", text)
    lines = []
    for block in blocks:
        chunk = block.strip().splitlines()
        if not chunk:
            continue
        lines.extend(chunk if len(chunk) <= 14 else chunk[:2] + ["... (summary truncated)"] + chunk[-12:])
    return "\n".join(lines)


class Runner:
    def __init__(self, fail_fast=False, log_dir=None, shard=None):
        self.failures = []
        self.lines = []
        self.fail_fast = fail_fast
        self.log_dir = log_dir
        # shard 는 1-based 이고, None 이면 지금까지와 같은 전량 실행이다.
        self.shard = shard

    def child(self, script):
        result = subprocess.run([PY, script], cwd=ROOT, capture_output=True, text=True)
        # Preserve both complete streams, with credentials redacted, outside checkout.
        result.stdout = redact_diagnostics(result.stdout or "")
        result.stderr = redact_diagnostics(result.stderr or "")
        if self.log_dir:
            os.makedirs(self.log_dir, mode=0o700, exist_ok=True)
            stem = script.replace("/", "__")
            for stream in ("stdout", "stderr"):
                path = os.path.join(self.log_dir, stem + "." + stream + ".log")
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(getattr(result, stream))
        return result

    def child_failure(self, stage, script, result):
        message = (f"{script} → exit {result.returncode}\n"
                   + failure_summary(result.stdout + "\n" + result.stderr))
        if self.log_dir:
            message += f"\nFull redacted stdout/stderr: {self.log_dir}/{script.replace('/', '__')}.*.log"
        self.fail(stage, message)
        self.say(message)

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
            r = self.child(script)
            tag = f"{i:02d} {script}"
            if r.returncode != 0:
                self.child_failure("rebuild", script, r)
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
        if not self.test_set():
            return False
        # ★ 무엇을 돌릴지부터 정한다 — 잘못된 population 이면 자식 하나도 실행하지 않는다.
        try:
            selected = self.selected_regression()
        except ValueError as error:
            self.fail("regression-shard", str(error))
            return False
        if self.shard is not None:
            self.say(f"  regression shard {self.shard}/{REGRESSION_SHARD_COUNT} — "
                     f"선택 {len(selected)} / 승인 전체 {len(APPROVED_TESTS)}파일 (PARTIAL)")
        ok = True
        # Same process environment and post-rebuild inputs; no cache or second run.
        priority = (["test/test_runner_reporting.py", "test/test_daily_orchestrator.py"]
                    if self.fail_fast else [])
        ordered = ([t for t in priority if t in selected]
                   + [t for t in selected if t not in priority])
        for t in ordered:
            self.say(f"  RUN {t}")
            r = self.child(t)
            if r.returncode != 0:
                ok = False
                self.child_failure("regression", t, r)
                if self.fail_fast:
                    return False
            else:
                self.say(f"  {t} ok")
        return ok

    def selected_regression(self):
        """이번 실행이 돌릴 회귀 목록 — 기본은 승인 전량, shard 요청 시 해당 shard."""
        if self.shard is None:
            return list(APPROVED_TESTS)
        return regression_shards()[self.shard - 1]

    def test_set(self):
        actual = sorted("test/" + f for f in os.listdir(os.path.join(ROOT, "test"))
                        if f.startswith("test_") and f.endswith(".py"))
        expected = sorted(APPROVED_TESTS + [FI_SUITE])
        if actual != expected:
            self.fail("test-set",
                      f"승인 목록과 실제 test 집합이 다르다\n"
                      f"        누락 {sorted(set(expected) - set(actual))}\n"
                      f"        미승인 {sorted(set(actual) - set(expected))}")
            return False
        return True

    # ── ④ Fault Injection ───────────────────────────────────────────
    def fault_injection(self):
        r = self.child(FI_SUITE)
        print(r.stdout, end="", flush=True)
        if r.returncode != 0:
            self.child_failure("fault-injection", FI_SUITE, r)
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


def finish_phase(r, label):
    """`--phase {structural,regression,fi}` 전용 종료 배너.

    ⛔ `finish()` 를 재사용하지 않는다 — `finish()` 의 무-shard 분기는
       "✅ Actions PASS = YES" 를 찍는데, bounded phase 하나의 성공은 전체
       Actions PASS 가 아니다. 그 문구를 여기서 절대 찍지 않는다 — 최종
       판정은 `actions-pass-full` aggregate job 만 한다.
    """
    print()
    if r.failures:
        print(f"⛔ FAIL — {len(r.failures)}건")
        for f in r.failures:
            print("  •", f)
        print(f"\n{label} = NO")
        return 1
    print(f"✅ {label} 완료 — PARTIAL")
    print("   ⛔ 이것은 승인된 phase 하나의 결과다. 전체 Actions PASS 판정은")
    print("      preflight · structural · regression 전체 shard · fault-injection")
    print("      을 모두 요구하는 최종 aggregate job(actions-pass-full)이 한다.")
    return 0


def run_bounded_phase(args):
    """`--phase {structural,regression,fi}` — CIO CI-sharding 지시 2026-09-12.

    ★ 각 phase 는 독립적으로 authoritative 하다 (구조 재현/회귀/FI 를 서로
      기다리지 않는다). 어느 phase 도 전체 Actions PASS 를 주장하지 않는다 —
      `finish_phase()` 가 항상 PARTIAL 로 찍는다.
    ⛔ authority 의미를 새로 만들지 않는다 — structural 은 기존 스냅샷/재빌드/
       byte 비교/경계 로직을 그대로 재사용하고, regression 은 기존 test_set()
       완전성 검사를 그대로 재사용하며, fi 는 기존 FI suite 를 그대로 부른다.
    """
    r = Runner(fail_fast=args.fail_fast, log_dir=args.log_dir)
    print("Atlas Actions runner — Python", sys.version.split()[0], f"[phase={args.phase}]")
    print("⛔ Production HOLD · evaluator 미연결 · 이 실행은 상태를 바꾸지 않는다\n")

    if args.phase == "structural":
        label = "structural phase"
        if not args.authoritative:
            r.fail("mode", "--phase structural requires --authoritative — the rebuild "
                           f"it verifies is destructive and needs {DISPOSABLE_ENV}=1 declared")
            return finish_phase(r, label)
        blockers = disposable_checkout_proof()
        if blockers:
            for b in blockers:
                r.fail("guard", b)
            print("⛔ authoritative rebuild 차단 — 어떤 파일도 건드리지 않았다")
            return finish_phase(r, label)
        with tempfile.TemporaryDirectory(prefix="atlas_committed_") as snap_dir:
            print("[1/3] committed 산출물 사본 보존")
            kept = r.snapshot(snap_dir)
            if args.fail_fast and r.failures:
                return finish_phase(r, label)
            if kept:
                print("[2/3] builder ①→⑭ 직렬 재빌드")
                r.rebuild()
                if args.fail_fast and r.failures:
                    return finish_phase(r, label)
                print("[3/3] committed ↔ rebuilt byte 비교")
                r.compare(kept)
                if args.fail_fast and r.failures:
                    return finish_phase(r, label)
            r.boundary()
        return finish_phase(r, label)

    if args.phase == "regression":
        shard_count, shard_index = args.shard_count, args.shard_index
        label = f"regression shard {shard_index}/{shard_count}"
        if not r.test_set():
            return finish_phase(r, label)
        try:
            selected = ci_phase_regression_shards(shard_count)[shard_index]
        except ValueError as error:
            r.fail("regression-shard", str(error))
            return finish_phase(r, label)
        print(f"[regression] shard {shard_index}/{shard_count} — 선택 {len(selected)} / "
              f"승인 전체 {len(APPROVED_TESTS)}파일 (PARTIAL)")
        priority = (["test/test_runner_reporting.py", "test/test_daily_orchestrator.py"]
                    if args.fail_fast else [])
        ordered = ([t for t in priority if t in selected]
                   + [t for t in selected if t not in priority])
        for t in ordered:
            r.say(f"  RUN {t}")
            res = r.child(t)
            if res.returncode != 0:
                r.child_failure("regression", t, res)
                if args.fail_fast:
                    return finish_phase(r, label)          # fail-fast 는 이 shard 안에서만 유효하다
            else:
                r.say(f"  {t} ok")
        return finish_phase(r, label)

    if args.phase == "fi":
        label = "fault-injection phase"
        print("[fi] Fault Injection suite")
        r.fault_injection()
        return finish_phase(r, label)

    raise AssertionError(f"unreachable --phase value: {args.phase!r}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--authoritative", action="store_true")
    parser.add_argument("--no-fi", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--log-dir", help="Complete redacted child logs, outside the checkout")
    parser.add_argument("--regression-shard-index", type=int,
                        help=f"1-based deterministic regression shard (1..{REGRESSION_SHARD_COUNT})")
    parser.add_argument("--regression-shard-count", type=int,
                        help=f"Total regression shards — only {REGRESSION_SHARD_COUNT} is supported")
    # ★ CIO CI-sharding 지시 2026-09-12 — actions-pass.yml 의 bounded phase 실행.
    #   ⛔ 인자를 주지 않으면 `--phase all` 이 기본값이라 아래 옛 경로가 그대로
    #      실행된다 — 기존 기본 동작(authoritative 전체 실행)은 한 글자도 바뀌지
    #      않는다. 이 네 값 이외에는 argparse choices 가 fail-closed 로 막는다.
    parser.add_argument("--phase", choices=["all", "structural", "regression", "fi"],
                        default="all",
                        help="Bounded execution phase for the sharded CI lane "
                             "(default: all — full authoritative gate, unchanged)")
    parser.add_argument("--shard-count", type=int,
                        help="--phase regression only: total shards (>=1)")
    parser.add_argument("--shard-index", type=int,
                        help="--phase regression only: 0-based shard index "
                             "(0 <= index < --shard-count)")
    args = parser.parse_args()
    # ★ test 파일을 실제로 실행하는 phase 로 갈 때만, 그 어떤 test 파일보다 먼저
    #   checkout 자체가 완전한지 한 번 본다.
    #   ⛔ `structural` 과 `fi` 는 대상이 아니다 — 위시리스트가 아니라 이 저장소
    #      자신의 actions-pass.yml 이 이미 그렇게 선언하고 있다:
    #      "fetch-depth: 0 은 [regression] matrix 에만 준다 — test_replay_
    #      asset_identity.py 가 실제 git 커밋 히스토리를 직접 읽는다" (해당 워크플로
    #      주석). `structural` 은 builder 재빌드/byte 비교만 하고 test 파일을 하나도
    #      실행하지 않으며, `fi` 는 test/test_fault_injection.py 하나만 자식으로
    #      실행하는데 그 파일은 스스로 만든 `.git` 없는 임시 사본 안에서만 검증한다
    #      (바깥 checkout 의 역사/evidence 완전성과 무관). 그래서 두 job 모두 CI 에서
    #      의도적으로 기본 fetch-depth: 1(shallow) 로 checkout 된다 — 이 게이트가 그
    #      두 곳에서도 unconditionally 발동하면, 올바르게 구성된 checkout 을 스스로
    #      불완전하다고 오판하게 된다(2026-09-18 밤에 실제로 그랬다: 첫 커밋부터
    #      `structural`/`fault-injection` 이 이 이유로 즉시 FAIL 했다 — 한국어 배너를
    #      추가하기 전부터다).
    #   `regression` 과 legacy `all` 경로는 실제로 APPROVED_TESTS 파일을 실행하므로
    #   (test_global_asset_master_population_readiness.py, test_paper_regime_
    #   reference.py 포함) 계속 검사한다.
    if args.phase in ("all", "regression"):
        checkout_abort = verify_checkout_completeness()
        if checkout_abort is not None:
            return checkout_abort
    if args.log_dir:
        args.log_dir = os.path.realpath(args.log_dir)
        if os.path.commonpath([args.log_dir, os.path.realpath(ROOT)]) == os.path.realpath(ROOT):
            parser.error("--log-dir must be outside the checkout")
    if args.phase != "regression" and (args.shard_count is not None or args.shard_index is not None):
        parser.error("--shard-count/--shard-index only apply to --phase regression")
    if args.phase == "regression":
        if (args.shard_count is None) != (args.shard_index is None):
            parser.error("--shard-count and --shard-index must be given together")
        shard_count = args.shard_count if args.shard_count is not None else 1
        shard_index = args.shard_index if args.shard_index is not None else 0
        if shard_count < 1:
            parser.error("--shard-count must be >= 1")
        if not 0 <= shard_index < shard_count:
            parser.error(f"--shard-index must satisfy 0 <= index < {shard_count}")
        args.shard_count, args.shard_index = shard_count, shard_index
    if args.phase == "fi" and args.no_fi:
        parser.error("--phase fi cannot be combined with --no-fi")
    if args.phase != "all" and (args.regression_shard_index is not None
                                 or args.regression_shard_count is not None):
        parser.error("--regression-shard-index/--regression-shard-count are the --phase all "
                     "(legacy 2-shard) surface; use --shard-count/--shard-index with --phase regression")
    if args.phase != "all":
        return run_bounded_phase(args)
    # ★ shard 인자는 어떤 작업보다 먼저 검증한다 — 잘못된 조합은 아무것도 실행하지 않는다.
    shard = None
    if (args.regression_shard_index is None) != (args.regression_shard_count is None):
        parser.error("--regression-shard-index and --regression-shard-count must be given together")
    if args.regression_shard_count is not None:
        if args.regression_shard_count != REGRESSION_SHARD_COUNT:
            parser.error(f"--regression-shard-count must be exactly {REGRESSION_SHARD_COUNT}")
        if not 1 <= args.regression_shard_index <= args.regression_shard_count:
            parser.error(f"--regression-shard-index must be 1..{REGRESSION_SHARD_COUNT}")
        # shard 는 권위 검증이나 FI 를 건너뛰는 통로가 아니다.
        if not args.authoritative:
            parser.error("--regression-shard-index requires --authoritative; "
                         "a shard never skips authoritative rebuild/byte verification")
        if args.no_fi:
            parser.error("--regression-shard-index cannot be combined with --no-fi; "
                         "every shard runs the complete Fault Injection suite")
        shard = args.regression_shard_index
    r = Runner(fail_fast=args.fail_fast, log_dir=args.log_dir, shard=shard)
    print("Atlas Actions runner — Python", sys.version.split()[0])
    print(f"⛔ Production HOLD · evaluator 미연결 · 이 실행은 상태를 바꾸지 않는다\n")

    authoritative = args.authoritative
    # Cheap exact population check before any expensive work or mutation.
    if shard is not None:
        if not r.test_set():
            return finish(r)
        try:
            r.selected_regression()
        except ValueError as error:
            r.fail("regression-shard", str(error))
            return finish(r)
    elif args.fail_fast and not r.test_set():
        return finish(r)
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
                if args.fail_fast:
                    return finish(r)
            else:
                print("[1/5] committed 산출물 사본 보존")
                kept = r.snapshot(snap_dir)
                if args.fail_fast and r.failures:
                    return finish(r)

                if kept:
                    print("[2/5] builder ①→⑭ 직렬 재빌드")
                    r.rebuild()
                    if args.fail_fast and r.failures:
                        return finish(r)

                    print("[3/5] committed ↔ rebuilt byte 비교")
                    r.compare(kept)
                    if args.fail_fast and r.failures:
                        return finish(r)

        if args.fail_fast:
            if not r.failures:
                r.boundary()
            if r.failures:
                return finish(r)
        print(approved_test_label())
        r.approved_tests()
        if args.fail_fast and r.failures:
            return finish(r)

        # ★ `--no-fi` 는 **Fault Injection suite 전용** 스위치다. FI-1 · FI-4 는 이
        #   runner 자체를 사본에서 실행해 Gate 동작을 검증하는데, 그 사본이 다시 FI
        #   suite 를 부르면 무한 재귀가 된다. Actions 는 이 스위치 없이 실행한다.
        if args.no_fi:
            print("[5/5] Fault Injection suite — 건너뜀 (--no-fi, FI 내부 실행)")
        else:
            print("[5/5] Fault Injection suite")
            r.fault_injection()

        r.boundary()

    return finish(r)


def finish(r):
    print()
    if r.failures:
        print(f"⛔ FAIL — {len(r.failures)}건")
        for f in r.failures:
            print("  •", f)
        print("\nActions PASS = NO")
        return 1
    # ★ 부분 실행은 전체 판정을 주장하지 않는다 — 최종 aggregate job 이 판정한다.
    if getattr(r, "shard", None) is not None:
        print(f"✅ regression shard {r.shard}/{REGRESSION_SHARD_COUNT} 완료 — PARTIAL")
        print("   ⛔ 이것은 승인 회귀의 일부다. 전체 판정은 두 shard 를 모두 요구하는")
        print("      최종 aggregate job 이 한다 — 이 출력은 전체 통과를 뜻하지 않는다.")
        return 0
    print("✅ Actions PASS = YES")
    print("   ⛔ 단, 이것은 CI 통과이지 Production 승인도 evaluator 승인도 아니다.")
    print("   ★ FI-3 frozen input tamper = KNOWN GAP / NOT GATED (미검증 영역)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
