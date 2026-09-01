# Gate 2–3 aggregate handoff

The Gate 2 Regime and Gate 3 Rotation aggregate is connected with exact
read-only receipt pins. No market is promoted and no PAPER authority exists.

| Market | Connected exact inputs | Gate 2 Regime | Gate 3 Rotation | Actual blockers |
| --- | --- | --- | --- | --- |
| KRX | 12-5 `bd9db7…` / `f6ceda…`; PAPER 12-4 `22b98e…`; P2-COM-02 `b6eef7…` | `WAIT / UNKNOWN / HOLD`; completed bar, source-time, PIT, and `5/5` coverage observed | `PENDING / BLOCKED` | scoring, TTL/freshness, signed direction, hysteresis; Korea rotation input remains pending |
| US | 12-6 `f4e1d9…` / `deb45b…`; PAPER 12-4 `5853bd…`; P2-COM-02 `57ae31…` | `WAIT / UNKNOWN / HOLD`; `0/5` | `DEGRADED / BLOCKED` | leadership, coverage, scoring, freshness, PIT, signed direction, hysteresis, rotation policy |
| Crypto | 12-11 `2b09c6…` / `ad661a…`; private adapter `742053…`; public funnel `7e6021…`; PAPER 12-4 `e72ec4…`; P2-COM-02 `925074…` | `WAIT / UNKNOWN / HOLD`; group coverage `0/5` | `DEGRADED / BLOCKED` | 8 natural candidates connected, but Regime/RS/liquidity/4-component score incomplete, so `INVESTMENT_PAPER=0`; signed direction and hysteresis absent |

One missing or invalid market becomes only that market's
`WAIT/UNKNOWN/HOLD` and `PENDING/BLOCKED`. The other two market receipt hashes
are byte-identical. Header status remains `PENDING`; rotation discovery remains
`DEGRADED`.

Focused and regression verification passed `154/154`. Exact output hashes and
the complete source lineage are recorded in `REPORT.json`. Network, broker,
credential, OAuth, GET/POST, order/cancel, timer, Portal, market writer, and
P7/P8 mutations are zero.

Next Gate: accountable policy owners must separately ratify signed direction,
coverage, TTL/freshness, scoring and hysteresis semantics and publish natural
PIT-qualified receipts. Crypto must complete Regime, relative-strength,
liquidity and four-component score evidence; KRX must publish a non-pending
rotation input. This aggregate cannot perform those approvals.
