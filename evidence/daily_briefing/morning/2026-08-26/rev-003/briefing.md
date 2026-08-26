# Atlas Daily Briefing — 2026-08-26 (morning)

Generated at: 2026-08-26T13:02:42Z
Component status counts: {'DEGRADED': 0, 'UNKNOWN': 0, 'DATA_BLOCKED': 0, 'UNAVAILABLE': 1, 'PENDING': 15, 'POLICY_BLOCKED': 13, 'READY': 11}

No action, order, Production, or trading authority is granted by this briefing. All such fields remain false/null.

## Data / Read-model health
- **STEP0_READ_MODEL_HEALTH**: OK
    - krx: ok=7 failed=0
    - dart: ok=7 failed=0
    - sec: ok=7 failed=0
  - source: `data/briefing_status.json`
- **KRX_PREOPEN_COMPACT**: OK
    - krx: ok=7 failed=0 date=2026-08-26
    - dart: ok=7 failed=0 date=2026-08-26
    - sec: ok=7 failed=0 date=2026-08-26
  - source: `data/latest_krx.json`
  - sha256: `f681980170e604a7cca3c40461ce51d192edb036b9648a95c128ac04697ac43b`
- **KRX_POST_CLOSE**: PENDING — MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY

## Filing & source evidence
- **DART_FILING_CONTENT**: OK
    - records=2 run_status=OK
  - source: `data/latest_dart_content.json`
  - sha256: `cf9e84c349fb5f0d38a6bed7d04d36e76547896eae55016e42455a706a774b4f`
- **SEC_FILING_CONTENT**: OK
    - records=12 run_status=OK
  - source: `data/latest_sec_content.json`
  - sha256: `99c71192803a2c39e5ef609a38a64a141a92368e2ab5956f6bcc3dff50b5d0b0`
- **KOFIA_FIRST_SEEN**: POLICY_BLOCKED — SOURCE_AVAILABLE_AT_AND_API_UNIT_UNRATIFIED
    - captured_at=2026-08-26T10:18:48Z available_at=None
  - source: `evidence/kofia/first_seen/2026-08-26/run-32957586093-attempt-1`

## Sensors
- **US_BREADTH_MEMBERSHIP**: OK
    - snapshot_date=2026-08-25 members=13173
  - source: `evidence/us_breadth/raw/2026-08-25`
- **FREE_MARKET_DATA**: OK
    - VIXCLS=15.85 as_of=2026-08-24
    - Alpaca IEX partial: MSFT=491.55, NVDA=213, TSM=417.34
    - scope: IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY
  - source: `data/latest_free_market_data.json`
  - sha256: `3b80144748af60ef00eb6713ad6aacfc2cb0ced425a89655e6712c754611681d`
- **BTC_TREND**: OK
    - direction=ABOVE_200DMA 200dma=69127.2185
  - source: `evidence/crypto/btc/raw/2026-08-26`
- **BTC_RISK**: OK
    - current_drawdown=-0.005783494436 max_drawdown=-0.206386933808 realized_vol_annualized=0.454552809507
  - source: `evidence/crypto/btc/raw/2026-08-26`
- **STABLECOIN_NET_ISSUANCE**: OK
    - 2026-08-26: daily_net_issuance=283169322.08 (AVAILABLE), weekly_net_issuance=2436723338.08 (AVAILABLE)
  - source: `evidence/stablecoin/raw/2026-08-26`
- **CRYPTO_BREADTH**: POLICY_BLOCKED — TAXONOMY_COVERAGE_UNKNOWN
    - status=UNKNOWN selected_assets=None
  - source: `evidence/crypto/breadth/raw/2026-08-26`

## 3-Market Regime
- **THREE_MARKET_REGIME_HEADER**: PENDING — ALL_MARKETS_UNKNOWN_NO_LIVE_AXIS_ADAPTER_WIRED
    - US: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
    - KR: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
    - CRYPTO: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
  - sha256: `32be6a542ae25a74a55a53f9c6a3b8865fccd2ae7a7dfa26a3d5cc7a307d1dec`

## Rotation / Theme
- **ROTATION_DISCOVERY**: PENDING — EVENT_CASES_RECORDED_NO_IMPORTANCE_OR_PROMOTION_AUTHORITY
    - rotation_changes=0 discovery_cases=9 new_candidates=0 existing_candidate_changes=0
  - sha256: `2c389548d4d977d1efd8b96ef0985a63f514b3dca5b23a2ceca3f5930ed4dd18`
- **KOREA_ROTATION**: PENDING — NO_ROTATION_OBSERVATION_FOR_DECISION_DATE
  - source: `data/latest_korea_rotation.json`

## New Discovery / candidate change
- **BUSINESS_ACCELERATION**: PENDING — RADAR_CASE_RECORDED_IMPORTANCE_AND_RANKING_UNRATIFIED
    - scope=TSM_SEC_MONTHLY_REVENUE_ONLY reports=3 series=2 cases=1
    - TSM TSM_CUMULATIVE_REVENUE_YOY_SEC: pattern=TWO_STEP_ACCELERATION_OBSERVED values_pct=['30.000000000000', '35.600000000000', '37.000000000000'] candidate_eligible=False
    - TSM TSM_MONTHLY_REVENUE_YOY_SEC: pattern=LATEST_STEP_NOT_UP values_pct=['30.100000000000', '67.900000000000', '44.700000000000'] candidate_eligible=False
  - sha256: `41814b4c7c77e395f94a0402d52d4a618590ae4e94af2850fc9c0efb06ec63fd`

## Rule status
- **RULE_EVALUATION**: POLICY_BLOCKED — ZERO_OF_TWENTY_FIVE_RULES_CONSUMABLE_BY_EVALUATOR
    - total_rules=25 PASS=0 FAIL=0 UNKNOWN=22 UNDEFINED=3
  - sha256: `2db86bb38ba3e4004c3199af3efc8c18a53f08c3c53e5d0c2bfdc449ed955dd5`

## Portfolio / Risk
- **PORTFOLIO_BUCKET**: POLICY_BLOCKED — CONSTITUTION_NOT_RATIFIED
- **PORTFOLIO_CURRENCY**: UNAVAILABLE — NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT
- **CASH_EXPOSURE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `70e9f12892411c4c725b9554fe7e7cabd43f36fad941eabeb03e7f3c52e5de1c`
- **CASH_EXPOSURE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `ac93a9cf2bcb496f5488280b40811a664008132aa5fa4c3be1ba2af4b2204f89`
- **CASH_EXPOSURE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `ea8eb5f3de134f25d5b73874fd35099632e9273d430fc46c70279a6f4aa86fc7`
- **INVERSE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `2948dc78cbb8fa404019e8735cc46a1753524b3d0665038309f4c830641dc435`
- **INVERSE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `d029ed6f3737349aa89aac49fbc775034b8f980d03b7f16a4499bae9dd2e439f`
- **INVERSE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `68b5c560539695a298c1f3b7b4ea34105e2c334c1e177f2bdd7e5969d1c70fb9`
- **LONG_SHORT_INVARIANT**: PENDING — NO_RULE_PASS_FAIL_TO_EVALUATE
    - long_results={'PASS': 0, 'FAIL': 0, 'UNKNOWN': 22, 'UNDEFINED': 3} short_pass=0 short_not_evaluated=25
  - sha256: `f2eb3b010c67189d698f6bded22ee98216ac3e4fb003bac95fccca6fdc153b79`
- **HEDGE_ELIGIBILITY**: POLICY_BLOCKED — NO_CIO_RATIFIED_HEDGE_INSTRUMENT_REGISTRY
- **BEAR_HEDGE_BUDGET**: POLICY_BLOCKED — NO_CIO_RATIFIED_BEAR_HEDGE_BUDGET_SET
- **POSITION_SIZING**: POLICY_BLOCKED — NO_CIO_RATIFIED_SIZING_POLICY_OR_CONSTITUTION
- **CONCENTRATION_GUARD**: POLICY_BLOCKED — NO_CIO_RATIFIED_CONCENTRATION_POLICY
- **MARKET_THEME_BUDGET**: POLICY_BLOCKED — NO_CIO_RATIFIED_THEME_BUDGET
- **CRYPTO_EXPOSURE_LIMIT**: POLICY_BLOCKED — NO_CIO_RATIFIED_CRYPTO_LIMIT_POLICY
- **PLANNED_LOSS_BUDGET**: POLICY_BLOCKED — NO_RATIFIED_CONSTITUTION

## Decision Review
- **INVESTMENT_DECISION_REVIEW**: POLICY_BLOCKED — P5_OR_THESIS_AUTHORITY_NOT_AVAILABLE
    - subject=TSM review=BLOCKED trade_proposal=None money_action=NONE
    - blocker=EXTERNALLY_RATIFIED_TSM_RULE_PACKET_NOT_AVAILABLE
    - blocker=P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED
    - blocker=P5_PASS_FAIL_NOT_AUTHORIZED
    - blocker=TSM_THESIS_PACKET_NOT_AVAILABLE
  - sha256: `d8e89b7250bba64c5c56e513c648a6be65dd756a2f9659f34d25d2701d2512e7`

## Decision & action boundary
- **ACTION_BOUNDARY**: PENDING — NO_READY_CANDIDATES_FROM_DISCOVERY_OR_RULE_YET
  - sha256: `07a1ab86452c0a63bdc508dca6dfc4e503791837e78d01e0aaea3050689c7d44`
- **UNIFIED_DECISION**: PENDING — 4/6_COMPONENTS_AVAILABLE
    - state=NO_ACTION_AUTHORIZED action=None order_intent=None available_components=4/6
  - sha256: `c0f1be5f677afac6882bd1836041a66baf1053333d817d628bcc10e8fb9100d0`
- **ACTION_RISK_PORTFOLIO_SUMMARY**: PENDING — MOST_UPSTREAM_SOURCES_NOT_YET_LIVE
    - available_sources=8/15 evaluated_actions=0 risk_breach_sources=0
  - sha256: `f391abcd0d96a83affcc6ec374d0e554958ca994562c4712e9211d34c26e2fe8`

## Shadow learning record
- **INVESTMENT_REVIEW_SHADOW**: POLICY_BLOCKED — NO_RATIFIED_PASS_REVIEW_TO_RECORD
    - ledger_record_created=False capital={'authorized': False, 'amount': 0} action=None order=None stage_change=None
  - sha256: `fab728b0847b860405999edad00070cc220cb8d0cc7867140f339f6d9ae12d87`

## Forward Alpha Review (Pilot)
- **FORWARD_ALPHA_REVIEW**: OK
    - pilot_subjects=['034020.KS', '267260.KS', '298040.KS', 'TSM']
    - 034020.KS: opportunity_state=BLOCKED shadow_action=REJECT comparison_label=BLOCKED
    - 267260.KS: opportunity_state=REJECTED shadow_action=REJECT comparison_label=REJECT
    - 298040.KS: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
    - TSM: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
  - sha256: `079a10f3bf6aa160dbf21685b1765b1aa014916863718c23413eee4b2ddbc6da`

## Dynamic Clock (Opportunity Trigger / Review Queue)
- **DYNAMIC_CLOCK**: OK
    - policy_approval_status=PROVISIONAL_CIO_MVP
    - BTC: raw_triggers(audit only)=1 immediate_review=0 watch_review=1 observation_only=0 expired=1 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION', 'RELATIVE_STRENGTH_REVERSAL']
      - WATCH_REVIEW BTC trigger_types=['PRICE_CONFIRMATION'] price_state=OVEREXTENDED reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-26T03:39:32Z next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
    - CRYPTO: raw_triggers(audit only)=100 immediate_review=0 watch_review=65 observation_only=0 expired=41 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW AAVE/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW ADA/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW AERO/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW AKE/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW APR/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ATOM/USD trigger_types=['PRICE_CONFIRMATION'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BCH/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BLESS/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BMT/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BNB/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW CAP/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CC/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW COTI/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CRV/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-26 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW CSPR/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - ... +50 more WATCH_REVIEW candidates (full list: evidence/operational/dynamic_clock/briefing_section.json)
    - KOREA: raw_triggers(audit only)=4 immediate_review=0 watch_review=3 observation_only=0 expired=2 calendar_confidence=UNVERIFIED_NO_HOLIDAY_CALENDAR not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW 000660 trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-25T21:57:10Z next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 005930 trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-25T21:57:10Z next_review_at=2026-08-25 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: price_reflection is linked (price_state=WEAK) but threshold_basis=PROVISIONAL -- PROVISIONAL diagnostics never elevate tier; no thesis linkage exists either
      - WATCH_REVIEW 034020 trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-25T21:57:10Z next_review_at=2026-08-27 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
  - sha256: `bb171e1a9e1a7e94ee26d42122a4f993f753ef25f596d36bc7e831e0b83237ac`

## PENDING / UNKNOWN / DEGRADED / BLOCKED components
KRX_POST_CLOSE, KOFIA_FIRST_SEEN, CRYPTO_BREADTH, THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, BUSINESS_ACCELERATION, KOREA_ROTATION, RULE_EVALUATION, PORTFOLIO_BUCKET, PORTFOLIO_CURRENCY, ACTION_BOUNDARY, UNIFIED_DECISION, INVESTMENT_DECISION_REVIEW, CASH_EXPOSURE_US, CASH_EXPOSURE_KOREA, CASH_EXPOSURE_CRYPTO, INVERSE_US, INVERSE_KOREA, INVERSE_CRYPTO, LONG_SHORT_INVARIANT, HEDGE_ELIGIBILITY, BEAR_HEDGE_BUDGET, POSITION_SIZING, CONCENTRATION_GUARD, MARKET_THEME_BUDGET, CRYPTO_EXPOSURE_LIMIT, PLANNED_LOSS_BUDGET, ACTION_RISK_PORTFOLIO_SUMMARY, INVESTMENT_REVIEW_SHADOW

## Unresolved boundaries
- REGIME_AXIS_LIVE_ADAPTER_NOT_WIRED
- ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED
- RULE_REGISTRY_NOT_CONSUMABLE
- PORTFOLIO_CONSTITUTION_NOT_RATIFIED
- ACTION_AND_ORDER_NOT_AUTHORIZED
- PRODUCTION_NOT_AUTHORIZED
- SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED
