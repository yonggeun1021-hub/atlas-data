# P10-11 Crypto PAPER simulator and ledger

Status: mechanism implementation for `PAPER_LAB_ONLY`. It is not an exchange,
broker, investment-eligibility, market-Regime, Production, Trading, or REAL
authority.

## Design packet

### Purpose

Rehearse the mechanical lifecycle `virtual cash → PAPER order → partial/full
fill → position → virtual sell → cash/P&L` without sending any Upbit request.
The same immutable event ledger must reconstruct the same account after a
restart, and retrying the same event or market snapshot must never create a
second fill.

### Input SSOT

- The caller supplies a fully hash-bound `crypto_paper_order_intent/1` packet.
  It contains quantity, optional limit price, fee rate, queue fraction, expiry,
  source plan/evidence lineage, and the market-Regime status exactly as observed.
- The caller supplies a fully hash-bound
  `crypto_paper_orderbook_snapshot/1` packet. Only `FRESH` snapshots may be
  matched. Bid/ask levels and their sizes are preserved exactly.
- There are no repository defaults for fee, queue participation, entry price,
  position size, loss limit, profit target, or timing threshold.

### Semantic contract

- `MARKET` consumes available levels in price priority.
- `LIMIT` additionally refuses levels worse than the caller's limit.
- `queue_fraction` is a caller-supplied PAPER assumption applied to each level's
  visible quantity. It is not inferred from Upbit.
- A BUY can use no more than virtual cash including fees. A SELL can use no
  more than the reconstructed virtual position.
- Fill slippage is the realized VWAP versus the snapshot's best executable
  price. No hidden penalty is added.
- Every ledger event links to the previous event hash. Account state is derived
  only by replay; mutable cash/position side tables do not exist.
- Content-addressed ledger snapshots are append-only. Recovery validates every
  snapshot and rejects divergent histories before selecting the longest chain.
- The publisher rejects the repository root and every descendant before it
  creates a directory, so virtual account history cannot become tracked public
  evidence through a caller path mistake.

### Authority

`PAPER_LAB_ONLY` authorizes arithmetic simulation only. Market-Regime input is
preserved but never promoted or interpreted. Every investment eligibility,
action, exchange order, broker submission, withdrawal, Production, Trading,
and REAL-capital authority field is hardcoded `false`. The module has no HTTP,
WebSocket, credential, JWT, secret, or exchange-private-endpoint code path.

### Population and activation

Initial tests use frozen/synthetic packets. This allows the mechanism to be
completed while P9-06/P5-08/P5-09/P7-13 are built independently. Operational
PAPER activation still requires their validated packets; this module does not
weaken those gates and does not convert `UNKNOWN` into `PASS`.

### Failure semantics

Malformed hashes, non-canonical decimals, stale/unknown snapshots, future
timestamps, insufficient virtual cash/position, invalid state transitions,
idempotency collisions, repeated snapshots, broken hash chains, modified
snapshots, and divergent recovery histories fail closed. A valid fresh
snapshot with no executable level records `MATCH_EVALUATED_NO_FILL` rather than
pretending that absence is a fill.

### Consumers

- P7-13 will consume the reconstructed position and append deterministic PAPER
  exit actions.
- P8-16/Portal may display a read-only PAPER badge and audit receipt.
- P10-12 may replay the ledger for counterfactual evaluation.

No consumer may treat this packet as authorization to submit an order.

### Counterexamples locked by tests

- same idempotency key with different payload;
- same order and same snapshot under a different retry key;
- rehashed historical-event mutation;
- fill after cancellation, expiry, or completion;
- BUY beyond cash or SELL beyond position;
- stale/unknown or cross-market snapshot;
- market-Regime `UNKNOWN` silently changed to `PASS`;
- two content-addressed histories that share a ledger ID but diverge;
- any source/config reference to an Upbit private/order/withdrawal endpoint.
