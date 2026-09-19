# Free market data evidence

This slice captures FRED `VIXCLS`, `WRESBAL`, and `TOTBKCR` observations and,
when a dedicated market-data-only credential is present, Alpaca Basic IEX
latest and bounded daily bars. The daily universe includes SPY/QQQ/IWM and
sector ETFs in addition to the existing single-name watchlist. It is
evidence-only. IEX is a single-exchange partial US feed and cannot authorize
security-level full-market breadth, market-wide prices, entry, action, order,
broker submission, production, or trading.

FRED response bytes are public market evidence and are retained in a
content-and-capture-addressed append-only revision. Each revision contains a
deterministic gzip response and a manifest; their immutable locators and hashes
are included in `free_market_data_capture/5`. Repeated identical capture is a
byte-identical no-op, while a different response on the same day creates a new
revision and cannot overwrite the earlier one. An independent validator reads
the exact retained bytes, recomputes their hashes, re-derives the latest VIXCLS
observation, checks the capture time, and compares it with the derived pointer.
Alpaca bytes remain under the separate IEX evidence boundary. WRESBAL and
TOTBKCR follow the already-qualified no-raw boundary: response bytes are used
in memory, response hashes and normalized current/previous observations are
retained, and raw response bodies are discarded.

## Same-day Alpaca revision retention

Alpaca raw and derived evidence used to be published with `os.replace`, so a
second capture on the same UTC day destroyed the earlier same-day response
bytes and the earlier derived observation. Both are now retained, reusing the
FRED provenance content-addressed, write-once publication pattern without
changing that module.

- **Raw revisions.** The exact original response bytes of each latest-bars and
  daily-bars capture are stored deterministically gzipped at
  `evidence/free_market_data/raw/alpaca/<kind>/<raw_response_sha256>/`, beside
  a manifest that names only the content. The address is the response hash
  alone, so it is deliberately not partitioned by day: byte-identical content
  captured at two different observation times deduplicates to one revision.
  The store lives under `raw/` because the unmodified capture workflow commits
  `derived`, `raw` and `fred/raw` only.
- **Derived observation revisions.** Each published packet is also retained at
  `evidence/free_market_data/derived/<day>/<observation_revision_id>/manifest.json`,
  where the revision id binds the actual observation time and the packet's own
  `packet_sha256`. Two same-day captures are therefore two observations even
  when their provider bytes are identical, and republishing the same packet is
  a byte-identical no-op.
- **Pinned pointers.** `free_market_data_capture/5` packets carry
  `alpaca.raw_evidence` and `alpaca.daily_raw_evidence`, each pinning the
  immutable revision that observation actually consumed. Both are `null` in a
  blocked or failed Alpaca component, which keeps the partial-publish
  semantics unchanged. The capture schema version is unchanged; the pointers
  are additive.
- **Compatibility paths.** `evidence/free_market_data/derived/<day>/manifest.json`,
  `evidence/free_market_data/raw/<day>/alpaca_iex_*.json.gz` and
  `data/latest_free_market_data.json` remain latest-wins pointers. Earlier
  bytes still sitting at those paths are preserved into the immutable store
  before they are replaced.

Replay resolution never rebinds an old record to newer bytes. A packet with a
pinned pointer always resolves its own immutable revision, including after a
later same-day run. An older packet resolves the unchanged per-day
compatibility path first, so already recorded `raw_path` bindings stay
byte-identical; only when those bytes no longer match that packet's own
`daily_raw_sha256` does resolution fall back to the preserved revision for
exactly that hash. Publication fails closed on a packet that cannot prove its
own `packet_sha256`, on conflicting bytes at an immutable address, and on a
pointer that escapes the evidence store; a revision is verified before any
reader is pointed at it.

Provider outcomes are independent. Missing or failed Alpaca credentials produce
an explicit component-level `BLOCKED_BY_*` or `ALPACA_CAPTURE_FAILED:*` state
while a valid FRED derived observation is still published. This is a partial,
degraded evidence result—not a market-wide price, Regime, or trading PASS.
Malformed or future FRED data, raw/manifest/path tampering, authority drift,
fabricated Alpaca rows in a blocked state, or a non-IEX contract fail closed.

The independently replayed VIX observation defines only the US `RISK_VOL`
evidence axis. Independently replayed SPY/QQQ/IWM daily bars define US `TREND`
evidence presence. The no-raw FRED current snapshot defines US `LIQUIDITY`
evidence presence with an explicit no-raw warning.

`us_market_reference/v2` additionally derives two current, free and plainly
scoped reference axes from the same retained IEX daily-bar bytes:

- `BREADTH`: latest-session advance/decline across SPY, QQQ, IWM and the 11
  broad sector ETFs. All 14 symbols and one exact common session pair are
  required; missing coverage fails the axis closed.
- `LEADERSHIP`: the observed 20-session return ordering of the 11 sector ETFs
  plus SMH, together with each ETF's return difference versus SPY. All 12
  groups and SPY are required.

This is the CIO-ratified **current representative-ETF reference**, not a claim
that every US-listed security was counted. The output carries
`NOT_FULL_US_SECURITY_LEVEL_BREADTH`, `NOT_INVESTMENT_RANKING`, and
`REGIME_INTERPRETATION_UNAUTHORIZED` warnings. The P1-US-04 historical
point-in-time/delisted-security Exit Gate remains open. None of the five axes
classifies a Regime, assigns a direction or confidence, ranks an investment,
or authorizes an action.

The scheduled capture runs at 06:35 KST Monday through Saturday. A v5
briefing may use the latest completed US session over a weekend or exchange
holiday, but it preserves the source session date and fails closed once that
session is more than four calendar days old.
