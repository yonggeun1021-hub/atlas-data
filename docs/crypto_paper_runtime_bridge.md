# Crypto PAPER runtime bridge (P9-06 → P10-11)

## Scope

`shadow/crypto_paper_runtime_bridge.py` is an offline, side-effect-free bridge
from the committed P5/P9 decision packet to the private P10-11 PAPER ledger.
It cannot connect to an exchange, read credentials, submit a broker order, or
authorize real capital. Every real-order authority remains `false`.

The bridge independently rehashes every referenced public source file and
consumes the decision packet's canonical `validate_output()` full-rederivation
boundary before it will produce a private-runtime request. The request embeds
its exact decision, runtime config, account state, planned-risk rows, and
idempotency inputs so a consumer can fully reproduce it; changing and rehashing
an intent, match order ID, status, or blocker is rejected. Value-bearing inputs
and requests must remain outside the public repository.

`crypto_paper_runtime_request/2` separates approved code and rolling
observations as immutable inputs. The code
checkout supplies every executable module and policy. A separately verified
observation checkout supplies only the packet's relative source files. The
bridge loads an isolated copy of the approved decision validator, reuses the
approved imported transforms, and redirects only relative evidence resolution
to that observation root. It never imports or executes Python from the rolling
observation checkout. The private request binds both the approved public-code
commit and the exact observation commit plus its absolute host root so restart
validation cannot silently switch either input.

## Per-market realtime freshness (`crypto_paper_runtime_request/3`)

User ratification `CRYPTO-REALTIME-FRESHNESS-PER-MARKET-V1-20260914`
(record sha256 `043932a4…`) with CIO addenda
`CIO-ADDENDUM-CRYPTO-SUBSCRIPTION-FLOOR-METRIC-20260914` and
`CIO-ADDENDUM-CRYPTO-SUBSCRIPTION-SCOPE-NO-HOLDINGS-LEAK-20260914` changes
only the application scope of the ratified realtime thresholds. For a
per-market decision packet (`crypto_paper_decision_snapshot_packet/2` or `/3`):

- A market's retained ticker or orderbook is usable only when **that market's**
  ratified realtime status (`realtime_per_market_freshness.subscribed_market_realtime`
  for `/3`, the candidate row for an issued `/2`) is `FRESH` and that market's
  own `freshness_by_kind[kind]` is `FRESH`. The aggregate
  `freshness_status.realtime` and the run's gate `overall_status` are
  telemetry only. A market absent from the decision is `MISSING`.
- A new PAPER intent is built only for a market with no
  `market_action_cap_reason`, a liquidity floor of `INCLUDED` and `FRESH`
  realtime. Capped markets are removed before the allocation check and listed
  as `MARKET_ACTION_CAPPED:<market>:<reason>` blockers; other markets are
  evaluated normally.
- One market's missing or non-`FRESH` book becomes
  `ENTRY_SNAPSHOT_UNAVAILABLE:<market>:<reason>` (or
  `MATCH_SNAPSHOT_UNAVAILABLE`) and status `WAIT_MARKET_EVIDENCE_OR_CAP`; it
  never aborts carried matches or other markets. Tampered, mis-hashed or
  malformed evidence still fails the whole request closed.
- A carried order in a per-market decision matches only when that market's
  ticker is also usable, because the resulting position must be marked `FRESH`
  in the next account view (`MATCH_SNAPSHOT_UNAVAILABLE:<market>:REALTIME_TICKER_NOT_FRESH:<market>`).
- `latest_mark_prices_by_market()` returns a mark only for markets with usable
  ticker evidence and `UNKNOWN` (with the reason) for the rest.
  `latest_mark_prices()` stays all-or-nothing because the P10-11 account view
  needs a `FRESH` mark for every open position.
- The request adds `decision_schema_version`, `freshness_mode`
  (`PER_MARKET_RATIFIED` or `AGGREGATE_DECISION_V1`) and `market_status` (per
  market: candidate, realtime and floor status, cap reason, entry state).

A `/1` decision (generated before the ratification's effective instant) keeps
the aggregate gate inside a `/3` request. An issued `crypto_paper_runtime_request/2`
revalidates with its original derivation byte for byte, including its
aggregate gate and whole-request abort, but only over a `/1` decision: a `/2`
request over a per-market decision is rejected
(`RUNTIME_REQUEST_LEGACY_SCHEMA_REQUIRES_V1_DECISION`) so a relabelled request
cannot downgrade per-market freshness to the aggregate gate. In a `/3` request,
only unavailable evidence becomes a mark, entry or match blocker; tampered or
malformed evidence (hash, identity, future-dated, value) aborts the request.

Held-position exits for a non-`FRESH` market are owned by the private runtime
(`portfolio/crypto_paper_stale_hold.py`: per-market HOLD and the 30-minute
engineering alert budget); this bridge never builds a SELL intent.

## Stage4 to Stage5 fixture connection

This bridge is the selected Stage5 connection host because it already owns the
validated Crypto decision, existing PAPER idempotency inputs, and strict public
code versus rolling-observation separation. The three-market
`paper_decision_bridge` is not used for this connection because it has no
account or ledger consumer contract.

`build_stage5_fixture_connection()` reads only the Stage4 envelope's
repo-relative, hash-pinned source record and then calls the merged
`stage5_paper_envelope_ledger` adapter. Its receipt retains that exact source
record together with decision identity, evaluation time, status, and the
Stage5 result. Re-signed source or receipt changes fail full rederivation. It
does not read Stage1 regime evidence directly, and `UNKNOWN` remains distinct
from a descriptive `NEUTRAL` candidate regime.

This seam is proof of a mock path, not completion of PAPER execution. It
accepts only the `STAGE5.FIXTURE.` ledger namespace, returns
`MOCK_PATH_VERIFIED_NOT_PAPER_EXECUTION`, and cannot consume the existing
Crypto PAPER account, KIS test account, or the integrated virtual portfolio.
A natural Stage4 decision source with the same exact hash and lineage contract
is still required before any non-fixture connection can be claimed.

## Time-ordered lifecycle

1. P9 retains the exact latest **accepted** public ticker and orderbook message
   per market and message kind. Rejected, duplicate, or out-of-order messages
   cannot replace it.
2. The decision snapshot is assembled after that capture. Its current
   orderbook can support decision lineage and a new PAPER intent, but cannot be
   used as a fill because it predates the intent.
3. The private runtime submits an eligible new PAPER intent to the append-only
   virtual ledger only.
4. A strictly later P9 run supplies a newer orderbook snapshot. Equal
   timestamps do not count. Only a decision whose P9 realtime freshness is
   ratified `FRESH` may deterministically match a carried `OPEN` or
   `PARTIALLY_FILLED` PAPER order.
5. The private runtime persists a content-addressed ledger snapshot and proves
   exact restart readback before emitting a redacted continuity receipt.
6. A filled position may enter P7-13 exit review. That review remains
   human-review-required and can create only a virtual PAPER sell intent.

This ordering prevents look-ahead fills and makes each observation reproducible
from immutable inputs.

The host must independently prove that the observation checkout is a clean,
non-symlink Git root at the exact supplied commit, that the approved code commit
and packet `source_commit` are ancestors of it, and that the decision packet is
inside it. Public bridge code validates path containment, hashes, complete
rederivation, and the bound commit identity; private host code owns the Git
ancestry and clean-checkout proof.

## Required private runtime configuration

There are no economic defaults. A request can become eligible only with a
hash-bound `crypto_paper_runtime_config/1` packet whose approval status is
`USER_RATIFIED_PAPER_RUNTIME` and which explicitly supplies:

- virtual initial KRW cash and ledger identity;
- fee rate and queue fraction;
- `LIMIT` or `MARKET` simulation type;
- for a limit simulation, the approved entry-zone price source.

Missing or invalid configuration produces a `WAIT_*` result, never an inferred
value. Future-dated approval cannot authorize an earlier observation, and each
open position must carry a strictly positive planned-loss amount. The current
public Regime and realtime-freshness policies remain unratified, so natural
production data is expected to remain non-eligible and non-matchable until
those upstream contracts are legitimately resolved.

Pending virtual orders reserve the new-intent lane: the bridge will not create
another intent until they are filled, cancelled, or expired. If more than one
candidate is simultaneously eligible, it returns `WAIT_ALLOCATION_POLICY`
instead of inventing a ranking or letting multiple drafts consume the same
available risk budget.

## Sampling gate

Mechanism tests and synthetic BUY→SELL evidence do not start the official
30-day natural sample. D0 begins only after scheduled private runs repeatedly
produce externally persisted receipts with exact restart readback and the
required P5/P9 inputs are naturally available. A real-order API is outside this
bridge and requires a separate post-sample approval path.
