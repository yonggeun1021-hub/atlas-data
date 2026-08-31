# US PAPER market-data source and rights audit

Audit date: 2026-08-31. Scope: public `atlas-data` US-only universe and
completed-bar validation. This is a technical availability review, not legal
advice and not a grant of redistribution rights.

## Decision

There is no verified free, official, full-market package that proves every
required Atlas fact (security type, halt, scheduled delisting, corporate
actions, liquidity, and timely 15m/1h/1d bars) and also grants public
redistribution. The implementation therefore makes no provider call and
persists no vendor rows. It accepts in-memory normalized facts only when their
point-in-time source lineage is explicit, and excludes every unknown fact.

“Free to access” is not treated as “licensed to republish.” Raw vendor prices,
volumes, symbol-master rows, and credentials remain outside the public repo.

## Source findings

| Source | Official/free availability | What it proves | What it does not prove / rights boundary | Atlas use |
| --- | --- | --- | --- | --- |
| [Nasdaq Trader Symbol Directory definitions](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs) | Official Nasdaq page and current directory downloads are publicly reachable. | Current symbol coverage, Nasdaq market category, test issue, financial status, ETF flag, other-listed exchange code, round lot, file creation time. | `ETF=N` is not affirmative common-stock proof; no complete halt, scheduled-delisting, corporate-action, liquidity, or historical PIT price coverage. Public reachability is not an affirmative redistribution grant. | Reuse only as `CURRENT_FORWARD_ONLY` source coverage. Never convert it directly to investability. |
| [Nasdaq Trader current trade halts](https://www.nasdaqtrader.com/Trader.aspx?id=TradeHalts) | Official current halt display and linked search/history are publicly reachable. | A time-specific published halt observation when captured with exact bytes/time. | Absence from an uncaptured page is not durable proof of `NOT_HALTED`; redistribution permission is not established here. | Require an explicit fresh halt fact; missing/unknown excludes. |
| [Nasdaq Daily List](https://nasdaqtrader.com/Trader.aspx?id=DailyListPD) | Official, but described as a monthly subscription product. | New listings, delistings, symbol/name changes, dividends, splits, and ex-date adjustments. | Not a free production source; CUSIP redistribution requires separate permission. | No automatic use. A licensed, PIT-retained feed can satisfy normalized corporate-action facts later. |
| [NYSE Corporate Actions](https://www.nyse.com/market-data/corporate-actions) and [NYSE Reference Data](https://www.nyse.com/market-data/reference) | Official product catalog; programmatic/reference products are commercial/contact or purchase paths. | NYSE Group splits, dividends, suspensions, delistings, symbol changes, security master, ETF reference facts. | Free full feed and public redistribution are not established. NYSE publishes separate contracts, policies and pricing. | No automatic use. Unknown facts exclude until licensed evidence is supplied. |
| [NYSE pricing, policies, contracts and guidelines](https://www.nyse.com/market-data/pricing-policies-contracts-guidelines) | Official policy and contract index. | Confirms that real-time, reference, non-display, vendor, and redistribution uses have contract/policy surfaces. | Does not grant Atlas redistribution by being publicly viewable. | Public raw persistence and redistribution remain false. |
| [NYSE holidays and trading hours](https://www.nyse.com/trade/hours-calendars) and [Nasdaq holiday schedule](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule) | Official public schedules identify core 09:30–16:00 ET sessions, holidays, and 13:00 ET early closes. | Date-specific regular/holiday/early-close facts when captured and timestamped. | A weekday rule alone is insufficient; unexpected closures and later revisions still require a captured date-specific fact. | Require a caller-supplied `OFFICIAL_EXCHANGE_CALENDAR` snapshot. Use `America/New_York` IANA rules, never a fixed UTC offset. |
| [Alpaca Market Data API plans](https://docs.alpaca.markets/us/docs/about-market-data-api) | Basic is $0 but authenticated. Current free real-time equities coverage is IEX; recent consolidated SIP requires a paid plan. | Provider-normalized OHLCV observations for the entitled feed and time range. | IEX is partial-market, not full SIP. [Alpaca terms](https://files.alpaca.markets/disclosures/library/TermsAndConditions.pdf) describe personal/non-commercial use and restrict copying/public distribution without consent. | Existing market-reference evidence may remain a partial observation. This US path stores no Alpaca rows and never upgrades IEX to market-wide authority. |
| [SEC EDGAR APIs](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) and [Accessing EDGAR data](https://www.sec.gov/search-filings/edgar-search-assistance/accessing-edgar-data) | Official and free, without API keys for public data APIs; fair-access rules apply. | Filing/submission/XBRL facts, filer name/ticker/exchange associations, filing timestamps. | SEC says ticker/exchange associations are periodically updated and not guaranteed for accuracy or scope. EDGAR is not a real-time halt, complete security-type, corporate-action, liquidity, or OHLCV feed. | May support evidence research, never sole investability or bar authority. |

## Unresolved procurement / permission gates

- Full-market timely SIP or exchange-direct bars with non-display PAPER use and
  retention terms.
- Affirmative common-stock/ETF security master for Nasdaq and NYSE Group.
- Fresh halt, scheduled delisting, and abnormal corporate-action coverage.
- PIT corporate-action history with split/dividend/symbol-change revisions.
- Historical/delisted OHLCV and original availability timestamps for replay.
- Explicit permission for any raw-data redistribution; absent permission means
  in-memory validation and redacted metadata only.

No estimate, inferred normal state, current-directory backfill, or “no row
observed” shortcut may close these gates.
