# Atlas Daily Briefing — 2026-09-01 (morning)

Generated at: 2026-08-31T22:24:59Z
Component status counts: {'UNKNOWN': 0, 'UNAVAILABLE': 1, 'DEGRADED': 5, 'READY': 9, 'DATA_BLOCKED': 6, 'PENDING': 13, 'POLICY_BLOCKED': 12}

No action, order, Production, or trading authority is granted by this briefing. All such fields remain false/null.

## 1. Regime
- status: PENDING
- as_of: 2026-08-31
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: THREE_MARKET_REGIME_HEADER=PENDING

## 2. Cross-Market Flow
- status: UNKNOWN
- as_of: 2026-08-28
- evidence_grade: UNKNOWN (CROSS_MARKET_EVIDENCE_GRADE_AGGREGATION_UNRATIFIED)
- unknown_reason: CROSS_MARKET_COMPARISON_POLICY_UNRATIFIED
- invalidation: UNKNOWN (CROSS_MARKET_INVALIDATION_POLICY_UNRATIFIED)
- sources: FREE_MARKET_DATA=AVAILABLE, KRX_POST_CLOSE=UNKNOWN, STABLECOIN_NET_ISSUANCE=UNKNOWN
- evidence_class_counts: {'DIRECT_FLOW': 2, 'MARKET_IMPLIED_FLOW': 1, 'MACRO_CONTEXT': 1, 'UNKNOWN': 0}
- evidence_status_counts: {'UNKNOWN': 3, 'AVAILABLE': 1}
- comparison_observation_dates: ['2026-08-28']
- flow_direction: UNKNOWN (no cross-market comparison authority)

## 3. Theme Rotation
- status: DEGRADED
- as_of: 2026-08-14
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: ROTATION_DISCOVERY=DEGRADED, KOREA_ROTATION=PENDING

## 4. Capital Action
- status: DEGRADED
- as_of: UNKNOWN
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: DEFENSIVE_ACTION_DECISION=DEGRADED, STRATEGIC_CAPITAL_POSTURE=DEGRADED, ACTION_RISK_PORTFOLIO_SUMMARY=DEGRADED

## 5. Assets
- status: DATA_BLOCKED
- as_of: 2026-08-31
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: SHADOW_ENTRY_REVIEW=DATA_BLOCKED

## 6. Entry / Exit / Size
- status: DATA_BLOCKED
- as_of: 2026-08-31
- evidence_grade: UNKNOWN (SECTION_EVIDENCE_GRADE_NOT_STANDARDIZED)
- unknown_reason: SOURCE_COMPONENT_NOT_READY
- invalidation: UNKNOWN (UPSTREAM_INVALIDATION_NOT_STANDARDIZED)
- sources: SHADOW_ENTRY_REVIEW=DATA_BLOCKED, POSITION_SIZING=POLICY_BLOCKED, PLANNED_LOSS_BUDGET=POLICY_BLOCKED

# Supporting Evidence and System Health

## Data / Read-model health
- **STEP0_READ_MODEL_HEALTH**: OK
    - krx: ok=7 failed=0
    - dart: ok=7 failed=0
    - sec: ok=7 failed=0
  - source: `data/briefing_status.json`
- **KRX_PREOPEN_COMPACT**: OK
    - krx: ok=7 failed=0 date=2026-09-01
    - dart: ok=7 failed=0 date=2026-09-01
    - sec: ok=7 failed=0 date=2026-09-01
  - source: `data/latest_krx.json`
  - sha256: `a6ee0392dea3773ce7e4576d86aa9753f8a16d875b2278909685db550e5aaed9`
- **KRX_POST_CLOSE**: PENDING — MORNING_SLOT_USES_CONFIRMED_HISTORY_ONLY

## Filing & source evidence
- **DART_FILING_CONTENT**: OK
    - records=3 run_status=OK
  - source: `data/latest_dart_content.json`
  - sha256: `2629b80254c88cccfb2815d750abcfba397311dbfca68499a66981edf9962476`
- **SEC_FILING_CONTENT**: OK
    - records=13 run_status=OK
  - source: `data/latest_sec_content.json`
  - sha256: `654b67aabe873b06b5ea2385705a4d4200db17501975e4902e0389c8dcd6096d`
- **KOFIA_FIRST_SEEN**: POLICY_BLOCKED — SOURCE_AVAILABLE_AT_AND_API_UNIT_UNRATIFIED
    - captured_at=2026-08-31T21:35:11Z available_at=None
  - source: `evidence/kofia/first_seen/2026-09-01/run-33442071616-attempt-1`

## Sensors
- **US_BREADTH_MEMBERSHIP**: OK
    - snapshot_date=2026-08-28 members=13174
  - source: `evidence/us_breadth/raw/2026-08-28`
- **FREE_MARKET_DATA**: OK
    - VIXCLS=14.43 as_of=2026-08-28
    - Alpaca IEX partial: MSFT=506.95, NVDA=220.8, TSM=415.305, SPY=766.87, QQQ=717, IWM=293.99, XLK=186.38, XLF=57.71, XLE=63.93, XLI=175.1, XLV=170.56, XLY=116.6, XLP=84.985, XLB=52.69, XLU=42.215, XLRE=44.11, XLC=111.445, SMH=556.64
    - scope: IEX_PARTIAL_EVIDENCE_ONLY_NOT_MARKET_WIDE_OR_TRADE_AUTHORITY
  - source: `data/latest_free_market_data.json`
  - sha256: `c7581d4a92f9fa92971ef7e69f4933743a8bbb5e798598f61afb053a940bb487`
- **BTC_TREND**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **BTC_RISK**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **STABLECOIN_NET_ISSUANCE**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **CRYPTO_BREADTH**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **CRYPTO_LEADERSHIP**: DATA_BLOCKED — NO_CAPTURE_FOR_DECISION_DATE
- **KOREA_MARKET_SIGNALS**: OK
    - 기준일=2026-08-28 코스피=-1.786507% 코스닥=0.090730%
    - 상승=1599 하락=927 보합=241 거래대금변화=-6.337118%
    - 상대강도 상위 관측: KOSPI 화학 5.616371%p, KOSPI 섬유·의류 4.874796%p, KOSPI 음식료·담배 4.467881%p (투자순위 아님)
  - source: `data/observations/korea_market_signals/2026-08-28`
  - sha256: `23d4fba11a392ca83ed168364ce588d5a121235010670cd2829ef2afe2e7657e`

## 3-Market Regime
- **THREE_MARKET_REGIME_HEADER**: PENDING — LIVE_AXIS_EVIDENCE_WIRED_REGIME_SCORING_UNRATIFIED
    - US: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=5/5
    - KR: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=5/5
    - CRYPTO: regime=UNKNOWN direction=UNKNOWN confidence=None coverage=0/5
  - sha256: `f03c3e2041f7acb4b1fd1446696a52521c7c85674f1cbeb961b771c522a1a6d9`

## Rotation / Theme
- **ROTATION_DISCOVERY**: DEGRADED — RotationDiscoveryBriefingError:DYNAMIC_SIGNAL_INPUT_INVALID:REPORT_DECISION_AFTER_BOUNDARY_AS_OF
- **KOREA_ROTATION**: PENDING — NO_ROTATION_OBSERVATION_FOR_DECISION_DATE
  - source: `data/latest_korea_rotation.json`

## New Discovery / candidate change
- **BUSINESS_ACCELERATION**: PENDING — RADAR_CASE_RECORDED_IMPORTANCE_AND_RANKING_UNRATIFIED
    - scope=TSM_SEC_MONTHLY_REVENUE_ONLY reports=3 series=2 cases=1
    - TSM TSM_CUMULATIVE_REVENUE_YOY_SEC: pattern=TWO_STEP_ACCELERATION_OBSERVED values_pct=['30.000000000000', '35.600000000000', '37.000000000000'] candidate_eligible=False
    - TSM TSM_MONTHLY_REVENUE_YOY_SEC: pattern=LATEST_STEP_NOT_UP values_pct=['30.100000000000', '67.900000000000', '44.700000000000'] candidate_eligible=False
  - sha256: `d20b23d62ed37e9a633c975a5d88b0315e90ea7284dad411009133aab27570f6`
- **OFFICIAL_RELEASE_SUMMARY**: PENDING — OFFICIAL_FACTS_OBSERVED_INTERPRETATION_AND_RANKING_UNRATIFIED
    - subject=SNDK observed_releases=1 summary_items=5 interpretation=UNDETERMINED ranking=UNRATIFIED
    - SNDK: Sandisk Reports Fiscal Fourth Quarter 2026 Financial Results published_at=2026-08-05
      - official_summary_1: Fiscal fourth quarter revenue was $8.97 billion, up 51% sequentially, with GAAP net income reported at $6.90 billion ($43.97 diluted net income per share). Sequential revenue growth came approximately one-third from higher volumes and two-thirds from higher pricing. Fourth quarter Non-GAAP diluted net income per share was $39.25.
      - official_summary_2: Fiscal year 2026 revenue was $20.25 billion, up 175% year-over-year, with GAAP net income reported at $11.43 billion ($73.76 diluted net income per share). Revenue outperformance was driven by both our mix shift toward higher-value customers, with Datacenter up 437%, and higher pricing. Fiscal year 2026 Non-GAAP diluted net income per share was $70.88.
      - official_summary_3: Since announcing five New Business Model (“NBM”) agreements during our April earnings call, we have signed five additional agreements, including three NBMs with new customers and two deals expanding on previously signed NBMs.
      - official_summary_4: Expanded our share repurchase authorization, with Sandisk’s Board of Directors approving an additional $14 billion buyback program, bringing total remaining authorization to $15.5 billion.
      - official_summary_5: Expect first quarter 2027 revenue to be in the range of $10.30 billion to $10.80 billion, with expected Non-GAAP diluted net income per share to be in the range of $44.00 to $46.00.
  - sha256: `ba9547e9784b577a9a220c71bbe40370dcb75ccde55dea2d16de14b03fd3cc81`

## Rule status
- **RULE_EVALUATION**: POLICY_BLOCKED — ZERO_OF_TWENTY_FIVE_RULES_CONSUMABLE_BY_EVALUATOR
    - total_rules=25 PASS=0 FAIL=0 UNKNOWN=22 UNDEFINED=3
  - sha256: `2db86bb38ba3e4004c3199af3efc8c18a53f08c3c53e5d0c2bfdc449ed955dd5`

## Portfolio / Risk
- **PORTFOLIO_BUCKET**: POLICY_BLOCKED — CONSTITUTION_NOT_RATIFIED
- **PORTFOLIO_CURRENCY**: UNAVAILABLE — NO_LIVE_ASSET_MASTER_OR_POSITION_SNAPSHOT
- **CASH_EXPOSURE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `3e822e95a044f94d4754d967b87b54b1b7f81e8c21e5c2e6d9f4fb30dfb16f33`
- **CASH_EXPOSURE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `86530cc52606e9189cf31cd56d995efad2ea2e2295b146f10c88d43802e3306f`
- **CASH_EXPOSURE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN cash_action=None evaluation_status=NOT_EVALUATED
  - sha256: `1b190956b8cb39f1ee766331c36fbc66955db4ae6e5f2706069b521a5b7a3ae9`
- **INVERSE_US**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `af68590afb3afdaef6dfb6d9cd83d2efdaabff3e63d96b8144332c8cf18e927b`
- **INVERSE_KOREA**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `34e02ea16dfde5561357abb89ba4b7edaaea84708e349a053949ff0db3f983b2`
- **INVERSE_CRYPTO**: PENDING — REGIME_UNKNOWN_NOT_EVALUATED
    - regime=UNKNOWN inverse_signal=None invariant_status=ENFORCED
  - sha256: `3cdd4b195f5c70c1180f34b0c72701e132589789328d3fe3d1cfd0ed3deea4ba`
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
- **STRATEGIC_CAPITAL_POSTURE**: DEGRADED — StrategicCapitalPostureError:GENERATED_BEFORE_AS_OF_DATE

## Decision Review
- **INVESTMENT_DECISION_REVIEW**: POLICY_BLOCKED — P5_OR_THESIS_AUTHORITY_NOT_AVAILABLE
    - subject=TSM review=BLOCKED trade_proposal=None money_action=NONE
    - blocker=EXTERNALLY_RATIFIED_TSM_RULE_PACKET_NOT_AVAILABLE
    - blocker=P5_DOWNSTREAM_ACTION_NOT_AUTHORIZED
    - blocker=P5_PASS_FAIL_NOT_AUTHORIZED
    - blocker=TSM_THESIS_PACKET_NOT_AVAILABLE
  - sha256: `5c590cd12c4f280b22bb66bca8c5e51d9abec16e2c87ac85fa6961432d34ecd9`

## Decision & action boundary
- **ACTION_BOUNDARY**: DEGRADED — DynamicClockSignalObservationError:REPORT_DECISION_AFTER_BOUNDARY_AS_OF
- **UNIFIED_DECISION**: PENDING — 2/6_COMPONENTS_AVAILABLE
    - state=NO_ACTION_AUTHORIZED action=None order_intent=None available_components=2/6
  - sha256: `60f5b770269094f8be705dcc35d07a366f1b758b31eaf45a7201eb2062eab44e`
- **DEFENSIVE_ACTION_DECISION**: DEGRADED — DefensiveActionDecisionError:GENERATED_BEFORE_AS_OF_DATE
- **ACTION_RISK_PORTFOLIO_SUMMARY**: DEGRADED — ActionRiskPortfolioSummaryError:REQUIRED_SOURCE_UNAVAILABLE:DEFENSIVE_ACTION_DECISION

## Shadow learning record
- **INVESTMENT_REVIEW_SHADOW**: POLICY_BLOCKED — NO_RATIFIED_PASS_REVIEW_TO_RECORD
    - ledger_record_created=False capital={'authorized': False, 'amount': 0} action=None order=None stage_change=None
  - sha256: `12ad6904b60202d2949cf016d2e2ad065e99c0dba5b7d0657b1804a725cdffe8`

## Forward Alpha Review (Pilot)
- **FORWARD_ALPHA_REVIEW**: OK
    - pilot_subjects=['034020.KS', '267260.KS', '298040.KS', 'TSM']
    - 034020.KS: opportunity_state=BLOCKED shadow_action=REJECT comparison_label=BLOCKED
    - 267260.KS: opportunity_state=REJECTED shadow_action=REJECT comparison_label=REJECT
    - 298040.KS: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
    - TSM: opportunity_state=WAIT_FOR_PRICE shadow_action=WAIT comparison_label=WAIT
  - sha256: `67225334bd6828ed898ae39e625049ff13082c31b78e1ca378681af5e53451ae`

## Dynamic Clock (Opportunity Trigger / Review Queue)
- **DYNAMIC_CLOCK**: OK
    - policy_approval_status=PROVISIONAL_CIO_MVP
    - BTC: raw_triggers(audit only)=0 immediate_review=0 watch_review=0 observation_only=0 expired=3 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION', 'RELATIVE_STRENGTH_REVERSAL']
    - CRYPTO: raw_triggers(audit only)=60 immediate_review=0 watch_review=51 observation_only=0 expired=171 calendar_confidence=VERIFIED_24_7 not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FLOW_REVERSAL', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW ACU/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-31 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW AKE/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW APT/USD trigger_types=['INVALIDATION_TRIGGER'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BABY/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-31 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BICO/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-31 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BILL/USD trigger_types=['INVALIDATION_TRIGGER'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BLESS/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BLUAI/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW BTR/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-31 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW COTI/USD trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-31 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: no thesis or price-reflection linkage exists yet
      - WATCH_REVIEW CVX/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-08-31 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW DASH/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW DCR/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ENA/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW ESPORTS/USD trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=UNKNOWN reflection_status=UNKNOWN data_state=NOT_LINKED threshold_basis=N/A price_as_of=UNKNOWN next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - ... +36 more WATCH_REVIEW candidates (full list: evidence/operational/dynamic_clock/briefing_section.json)
    - KOREA: raw_triggers(audit only)=6 immediate_review=0 watch_review=4 observation_only=0 expired=9 calendar_confidence=UNVERIFIED_NO_HOLIDAY_CALENDAR not_computable=['CATALYST_APPROACH', 'EXPECTATION_DISLOCATION', 'FUNDAMENTAL_REVISION']
      - WATCH_REVIEW 034020 trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-31T21:00:01Z next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: price_reflection is linked (price_state=STRONG_MOMENTUM) but threshold_basis=PROVISIONAL -- PROVISIONAL diagnostics never elevate tier; no thesis linkage exists either
      - WATCH_REVIEW 267260 trigger_types=['RELATIVE_STRENGTH_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-31T21:00:01Z next_review_at=2026-09-02 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
      - WATCH_REVIEW 298040 trigger_types=['PRICE_CONFIRMATION', 'RELATIVE_STRENGTH_REVERSAL'] price_state=STRONG_MOMENTUM reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-31T21:00:01Z next_review_at=2026-09-01 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=2 independent trigger types, but capped at WATCH_REVIEW: price_reflection is linked (price_state=STRONG_MOMENTUM) but threshold_basis=PROVISIONAL -- PROVISIONAL diagnostics never elevate tier; no thesis linkage exists either
      - WATCH_REVIEW 329180 trigger_types=['INVALIDATION_TRIGGER'] price_state=WEAK reflection_status=UNKNOWN data_state=REFLECTION_UNCERTAIN_WITH_VALID_PRICE threshold_basis=PROVISIONAL price_as_of=2026-08-31T21:00:01Z next_review_at=2026-09-02 authority=REVIEW_ONLY money_action=NONE reason=confirmation_count=1 -- below the independent-confirmation threshold of 2
  - sha256: `7ba3996f5033a6557094bf4f4761e768c8ef75c59fa9dde9d73d67ce49de842a`

## Zero-capital human review (P5-06 / P7-08 / P8-13)
- **SHADOW_ENTRY_REVIEW**: DATA_BLOCKED — SHADOW_ENTRY_REVIEW_DECISION_DATE_MISMATCH
  - source: `evidence/operational/dynamic_clock/shadow_entry_review.json`
  - sha256: `5696ea48b591a37d7df8df2e7326aa4bf4d57edabc59a0703119eed08ce52e8d`

## PENDING / UNKNOWN / DEGRADED / BLOCKED components
KRX_POST_CLOSE, KOFIA_FIRST_SEEN, BTC_TREND, BTC_RISK, STABLECOIN_NET_ISSUANCE, CRYPTO_BREADTH, CRYPTO_LEADERSHIP, THREE_MARKET_REGIME_HEADER, ROTATION_DISCOVERY, BUSINESS_ACCELERATION, OFFICIAL_RELEASE_SUMMARY, KOREA_ROTATION, RULE_EVALUATION, PORTFOLIO_BUCKET, PORTFOLIO_CURRENCY, ACTION_BOUNDARY, UNIFIED_DECISION, INVESTMENT_DECISION_REVIEW, CASH_EXPOSURE_US, CASH_EXPOSURE_KOREA, CASH_EXPOSURE_CRYPTO, INVERSE_US, INVERSE_KOREA, INVERSE_CRYPTO, LONG_SHORT_INVARIANT, HEDGE_ELIGIBILITY, BEAR_HEDGE_BUDGET, POSITION_SIZING, CONCENTRATION_GUARD, MARKET_THEME_BUDGET, CRYPTO_EXPOSURE_LIMIT, PLANNED_LOSS_BUDGET, DEFENSIVE_ACTION_DECISION, STRATEGIC_CAPITAL_POSTURE, ACTION_RISK_PORTFOLIO_SUMMARY, INVESTMENT_REVIEW_SHADOW, SHADOW_ENTRY_REVIEW

## Unresolved boundaries
- REGIME_POLICY_VALUES_UNRATIFIED
- ROTATION_AND_DISCOVERY_POLICY_UNRATIFIED
- RULE_REGISTRY_NOT_CONSUMABLE
- PORTFOLIO_CONSTITUTION_NOT_RATIFIED
- ACTION_AND_ORDER_NOT_AUTHORIZED
- PRODUCTION_NOT_AUTHORIZED
- SAME_DAY_AUTOMATIC_RECOVERY_TRIGGER_NOT_SCHEDULED
