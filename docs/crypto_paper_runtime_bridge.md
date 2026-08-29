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
