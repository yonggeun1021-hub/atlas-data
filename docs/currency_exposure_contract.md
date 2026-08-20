# P7-07 raw quote-currency exposure contract

This capability displays position exposure in each asset's exact Global Asset
Master `quote_currency`. It uses canonical decimal strings and sums notionals
only within the same currency. It never adds KRW and USD, chooses a reporting
currency, sources an FX rate, converts currency, or evaluates a limit.

The input is an explicit long-only position observation. Every row is bound to a
Global Asset ID, its exact quote currency, a price timestamp/source SHA, and a
position-record SHA. Unknown assets, currency mismatch, future prices, duplicate
position IDs, non-canonical/negative/zero quantity or price, and input digest
drift fail closed.

Output keeps per-position raw notional and a sorted quote-currency aggregation.
For every currency:

- `fx_conversion_status=NOT_AUTHORIZED`
- `limit_status=UNRATIFIED`
- `limit_value=null`
- `breach=null`

The packet's cross-currency total and reporting currency are also null. Thus the
feature makes USD/KRW/Crypto quote exposure visible without silently turning an
unapproved FX source or limit into portfolio authority. Position sizing, orders,
Production, and trading remain false. The CLI is offline and writes only outside
the repository.
