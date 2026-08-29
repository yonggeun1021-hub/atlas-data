# P1-CR-08 Crypto Live Component Registry

`regime/crypto_live_component_registry.py` binds the existing public natural
BTC trend, BTC risk, stablecoin net-issuance, and Crypto breadth component rows
to one exact Crypto PAPER decision instant. It does not score or interpret an
axis.

For point-in-time replay, a same-date source is included only when its retained
`_downloaded_at.txt` timestamp is not later than the decision's
`generated_at`. Each included directory is fingerprinted over exact relative
filenames, sizes, and SHA-256 hashes. Validation rebuilds the existing daily
component rows and the directory fingerprints. Row omission, substitution,
future evidence, and retained-byte drift fail closed.

The registry feeds the existing `regime/live_axis_adapter.py` without changing
its semantics:

- BTC trend can define `TREND` evidence presence.
- BTC risk can define `RISK_VOL` evidence presence.
- Stablecoin evidence can define `LIQUIDITY` evidence presence.
- Crypto breadth defines `BREADTH` only when its existing taxonomy gate is
  already satisfied.
- Crypto leadership is rebuilt by a daily component-row producer from the
  existing 7-day and 30-day windows. Every retained daily source directory
  used by the transform is hash-bound. `LEADERSHIP` remains `UNDEFINED` while
  either natural window is incomplete or contains an UNKNOWN source point.

`DEFINED` means qualified evidence exists. It is not a positive/negative axis
reading and grants no Regime, strategy, candidate, PAPER order, exchange
order, withdrawal, Production, Trading, or real-capital authority.
