# Rotation state-rule event study — PRE-REGISTRATION (written before any result was computed)

Written: 2026-09-14T14:45Z by CLAUDE_CIO. No forward return, hit rate or event count had been computed when this file was written.
Only the data inventory (which files exist, date ranges, row counts) was known.

## 0. Data used (local only, no API)
- US: atlas-data origin/main `evidence/free_market_data/raw/alpaca/daily_bars/*/alpaca_iex_daily_bars.json.gz` (IEX feed), union of all revisions,
  entities XLB XLC XLE XLF XLI XLK XLP XLRE XLU XLV XLY; benchmark SPY. Dedupe by date (last revision wins on conflict; conflicts counted and reported).
- CRYPTO: `/Users/yonggeun/Documents/Codex/data/kraken-ohlcvt/kraken-usd-daily-2019-2025/daily_usd_1440.ndjson.gz`. Entities BTC(=XBT), ETH, ALT.
- KR: no price history exists (15 one-session RS observations). Descriptive bucket persistence only; no forward-return study.

## 1. Common definitions
- Observation t = session close (US) / UTC daily close (crypto).
- RS_L(e,t) = ln(P_e,t / P_e,t-L) - ln(B_t / B_t-L). US: B=SPY, L=20. CRYPTO: B=BTC, L=30 (primary, contract PRIMARY window) and L=7 (pilot window, reported separately). BTC RS = 0 by construction.
- Rank descending by RS (tie: entity id asc). Buckets: US TOP = ranks 1-3, BOTTOM = ranks 9-11, MIDDLE = rest. CRYPTO (3 entities): TOP = rank 1, MIDDLE = rank 2, BOTTOM = rank 3.
- Execution: signal known at close t, executed at close t+1. Forward excess FX_h(e,t) = ln(P_e,t+1+h / P_e,t+1) - mean_x ln(P_x,t+1+h / P_x,t+1) over all entities of that market (equal-weight "sector average" baseline). Secondary: vs benchmark (SPY / BTC).
- Horizons: US 5/10/20 sessions (MAIN = 10). CRYPTO 7/14/30 days (MAIN = 14).
- Round-trip cost assumption: US sector ETF 0.10%; KR sector ETF 0.30% (plan only); CRYPTO spot basket 0.50%. Also reported at 2x cost.

## 2. Candidate rules (same structure in every market) — ALL will be reported
- R1-k (Atlas-native confirmed TOP), k in {1,2,3}: ENTER at t when bucket is TOP on k consecutive observations ending t and was not TOP at t-k (k=1 == ratified 9-cell STRONG entry, BOTTOM/MIDDLE_TO_TOP).
  HOLD while not EXIT. EXIT = first t after ENTER where entity is non-TOP on 2 consecutive observations, or BOTTOM once.
  Whipsaw rate = share of ENTER events whose EXIT happens within 5 observations.
- R2 (R1-2 + participation filter): ENTER of R1-2 kept only if turnover ratio short/long >= 1.0 at t.
  US: 5-session avg dollar volume / 20-session avg (IEX partial volume). CRYPTO: 7-day sum close*volume / (30-day sum/30*7); ALT turnover = sum over current members; BTC not an ENTER entity anyway when it is TOP? (BTC can be TOP; same filter on BTC turnover).
- R3 (EMERGING watch): WATCH at t when entity is MIDDLE or BOTTOM at t and rank improved by >= d vs t-w, and no WATCH for that entity in the previous w observations.
  US: d=3, w=5. CRYPTO: d=1, w=7. Conversion = share of WATCH events followed by an R1-2 ENTER within 10 observations.
- R4 (RRG-style, transparent approximation; JdK RS-Ratio/RS-Momentum exact formulas are proprietary):
  X = P_e / P_B. RSR_t = 100 * X_t / SMA_N(X)_t. RSM_t = 100 * RSR_t / RSR_t-M. US N=20, M=5. CRYPTO N=30, M=7 (BTC excluded: X==1).
  Quadrants: LEADING (RSR>100 & RSM>100), WEAKENING (RSR>100 & RSM<=100), LAGGING (RSR<=100 & RSM<=100), IMPROVING (RSR<=100 & RSM>100).
  Events: ENTER = quadrant becomes LEADING from any other; WARN = LEADING->WEAKENING; EXIT = becomes LAGGING; WATCH = LAGGING->IMPROVING.
- R5 (cross-sectional momentum baseline): rebalance every 20 sessions (US) / 30 days (CRYPTO) from first date with full lookback;
  hold top-N by trailing log return over L (US N=3, L=20; CRYPTO N=1 of 3, L=30). Report per-rebalance excess vs equal-weight all-entity average,
  plus a daily-overlapping version (every t, top-N, FX at MAIN horizon).
- Business-cycle sector rotation: context only, not testable with this data.

## 3. Metrics per candidate/event type/horizon
n; n_nonoverlap (greedy per entity, events >= h apart); mean & median FX; hit rate (FX>0) vs baseline hit rate (all entity-observations, same horizon);
false-signal rate (ENTER/WATCH with FX_MAIN < 0; for EXIT/WARN with FX_MAIN > 0); mean after removing single best event (worst for EXIT);
recent split (US: signal dates in last 40 sessions with available forward vs earlier; CRYPTO: 2025-07-01..2025-12-31 vs all, and 2019-2022 vs 2023-2025);
cost-adjusted mean (mean - c, mean - 2c) for ENTER/WATCH; repeatability (US: number of sectors with >=3 events and positive mean; CRYPTO: ETH and ALT separately).

## 4. Pass gates (fixed now; adapted from the crypto guardrail Event Study Gate)
ENTER/WATCH rule passes at MAIN horizon only if ALL: n >= 80; n_nonoverlap >= 30; mean - 2c > 0; hit rate >= baseline + 5pp;
mean after removing best event > 0; recent split not negative; repeatability (US >= 6 of eligible sectors positive; CRYPTO ETH and ALT both positive mean).
EXIT/WARN rule "justified" only if: n >= 30, mean FX < 0, mean after removing worst event < 0, recent split mean < 0.
A rule failing n gates is labelled INSUFFICIENT (not FAIL). No parameter will be changed after results are seen; any extra variant would be labelled POST-HOC.

## 5. ALT bucket construction (CRYPTO)
Daily membership = top 100 aliases by trailing 30-day sum(close*base_volume) ending t-1, requiring >= 27 of 30 days present and closes at t-1 and t.
Excluded aliases: XBT ETH; fiat EUR GBP AUD; stable USDT USDC DAI TUSD PYUSD USD1 USDE USDG USDQ USDR USDS UST EURC EURT EURR EURQ EUROP TGBP AUDX BRL1 MXNB;
gold PAXG XAUT; wrapped/staked WBTC TBTC XBTPY ETHPY CMETH LSETH METH JITOSOL LSSOL MSOL.
ALT daily return = equal-weight mean of member simple returns; member-day excluded if return > +300% or < -95% (data-error guard). No other winsorizing.
Contract uses VWAP turnover; archive has no VWAP so close*volume is used (documented deviation).
