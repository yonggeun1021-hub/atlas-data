# US market judgement input and receipt

`us_market_judgement/1` is the PAPER 12-6 zero-transport boundary between US
market evidence and downstream PAPER read models. It validates an ordered
input envelope for US universe, universe breadth, sector leadership, price
breadth, market flow, Trend, Risk/Vol, and a finished-session receipt. Every
source carries its observation date, UTC source time, positive TTL, exact
file/hash pin, exact policy pin, coverage counts, and (for the session source)
the literal finished-session state.

The runtime never fetches a provider, opens a broker or credential store,
submits OAuth or HTTP POST, generates an order, or mutates a PAPER ledger.
Tracked natural evidence is not copied into a fixture. Synthetic contract-test
files remain `SYNTHETIC_CONTRACT_TEST` and cannot override repository policy.

## Current fact boundary

The contract pins and verifies these current repository facts:

- `us_leadership/draft-v1` is `UNRATIFIED`;
- `us_leadership_universe/draft-v1` is `UNRATIFIED`;
- forward US membership capture does not authorize price breadth;
- all five Regime axes are required by the ratified 5-of-5 coverage gate; and
- Regime classification still authorizes only `UNKNOWN`.

Therefore the current deterministic result is exactly `coverage=0/5`,
`judgement=UNKNOWN`, `status=HOLD`, `recommendation=WAIT`, and `action=null`.
An available or even synthetic all-RATIFIED input cannot silently replace the
repository policies. Missing, stale, future, hash-mismatched, coverage-short,
unratified, or unfinished-session inputs each retain their exact blocker.

## Axis qualification

Trend, Price Breadth, Risk/Vol, Market Flow, and Sector Leadership bind to the
`TREND`, `BREADTH`, `RISK_VOL`, `LIQUIDITY`, and `LEADERSHIP` axes. An axis can
be evidence-defined only when its own source and all three common prerequisites
(`US_UNIVERSE`, `US_BREADTH`, `US_FINISHED_SESSION`) qualify. Defining an axis
still grants no score or interpretation authority. Any policy or axis gap keeps
the market judgement at `UNKNOWN/HOLD`.

## Exact downstream consumption

Each receipt is content-addressed and exposes two subtree pins:

- PAPER 12-4 consumes `/regimeOutput` as an exact `regime_output/v1` packet;
- PAPER 12-1 consumes `/paperDecisionBridgeProjection` as an exact
  `us_market_judgement_bridge_projection/1` packet.

The consumer must hash canonical JSON (`sort_keys`, compact separators,
UTF-8, no NaN) and require equality with the matching `consumerPins` SHA-256.
The bridge projection preserves US leadership as `UNRATIFIED`, market judgement
as `HOLD`, lifecycle status as null, recommendation as `WAIT`, and action as
null. It does not create a PASS or BUY.

Example, with caller-owned paths outside the repository:

```bash
python3 regime/us_market_judgement.py \
  --input /path/to/us-input.json \
  --out /path/to/run-summary.json \
  --receipt-dir /path/to/immutable-receipts
```

The receipt filename is its SHA-256. An identical replay returns `NO_CHANGE`;
conflicting bytes at the same identity fail closed.
