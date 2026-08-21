# P7-06 Planned Stop-Loss / Portfolio Loss Budget Contract

`portfolio/planned_loss_budget.py` connects each explicit long position's
planned stop to the ratified Portfolio Constitution. It uses the existing
Constitution meanings rather than creating another loss policy:

- B4 caps the position NAV weight;
- B5 caps each planned stop distance and therefore each position's planned
  NAV loss; and
- B6 caps the sum of all simultaneous planned NAV losses.

For every position, the module recomputes
`weight × (entry − stop) / entry` and rejects a mismatched stated loss. Stops
must be positive and strictly below entry. Position, identity, bucket,
position-sizing, portfolio, P7-03, P7-04, and conditional P7-05 Crypto lineage
is hash-bound. A Crypto lineage hash is required if and only if the snapshot
contains a Crypto position.

The checked Constitution must be explicitly `ratified`, internally consistent
under the canonical `portfolio/constitution.py` validator, and have all B2–B7
values. The tracked repository Constitution remains `not_ratified`; this
module does not modify it or invent a number.

`LIMIT_BREACH` is a risk result, not an exit instruction. Recommended exit,
position sizing, stop orders, Production, and trading authority remain
null/empty/false. CLI output inside the repository tree is forbidden.
