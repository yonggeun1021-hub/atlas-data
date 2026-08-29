# Crypto PAPER runtime bridge (P9-06 → P10-11)

## Scope

`shadow/crypto_paper_runtime_bridge.py` is an offline, side-effect-free bridge
from the committed P5/P9 decision packet to the private P10-11 PAPER ledger.
It cannot connect to an exchange, read credentials, submit a broker order, or
authorize real capital. Every real-order authority remains `false`.

The bridge independently rehashes every referenced public source file and
rebuilds the complete decision packet before it will produce a private-runtime
request. Value-bearing account state, economic assumptions, and requests must
remain outside the public repository.

## Time-ordered lifecycle

1. P9 retains the exact latest **accepted** public ticker and orderbook message
   per market and message kind. Rejected, duplicate, or out-of-order messages
   cannot replace it.
2. The decision snapshot is assembled after that capture. Its current
   orderbook can support decision lineage and a new PAPER intent, but cannot be
   used as a fill because it predates the intent.
3. The private runtime submits an eligible new PAPER intent to the append-only
   virtual ledger only.
4. A later P9 run supplies a newer orderbook snapshot. Only then may P10-11
   deterministically match a carried `OPEN` or `PARTIALLY_FILLED` PAPER order.
5. The private runtime persists a content-addressed ledger snapshot and proves
   exact restart readback before emitting a redacted continuity receipt.
6. A filled position may enter P7-13 exit review. That review remains
   human-review-required and can create only a virtual PAPER sell intent.

This ordering prevents look-ahead fills and makes each observation reproducible
from immutable inputs.

## Required private runtime configuration

There are no economic defaults. A request can become eligible only with a
hash-bound `crypto_paper_runtime_config/1` packet whose approval status is
`USER_RATIFIED_PAPER_RUNTIME` and which explicitly supplies:

- virtual initial KRW cash and ledger identity;
- fee rate and queue fraction;
- `LIMIT` or `MARKET` simulation type;
- for a limit simulation, the approved entry-zone price source.

Missing or invalid configuration produces a `WAIT_*` result, never an inferred
value. The current public Regime contract still returns `UNKNOWN`, so natural
production data is expected to remain non-eligible until that upstream
contract is legitimately resolved.

## Sampling gate

Mechanism tests and synthetic BUY→SELL evidence do not start the official
30-day natural sample. D0 begins only after scheduled private runs repeatedly
produce externally persisted receipts with exact restart readback and the
required P5/P9 inputs are naturally available. A real-order API is outside this
bridge and requires a separate post-sample approval path.
