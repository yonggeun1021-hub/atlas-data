# P2-01 source-fact population boundary

`rotation/theme_taxonomy_population.py` inventories facts that already have
repository authority without converting those facts into a Theme or
value-chain decision. It is an input audit for the existing
`theme_taxonomy/2` graph and `theme_taxonomy_authority_registry/1` resolver.
It is not a second taxonomy, a ranking engine, or a trading rule.

The pinned sources currently prove only:

- Korea: 46 official KRX sector-series identities plus the KOSPI and KOSDAQ
  benchmark identities. They do not identify security-to-Theme membership or
  a cross-market Theme.
- US: the checked-in leadership and universe policies are empty and
  `UNRATIFIED`.
- Crypto: Kraken breadth and Upbit PAPER-eight identity/exclusion records.
  Their overlapping identities must have the same category. These files do
  not authorize sector or chain membership; the Upbit policy explicitly keeps
  `taxonomy_authorized=false`.

Every source, the Theme contract, the independent authority registry, and the
three market-specific rotation consumer contracts are pinned by exact bytes.
The audit also verifies the pinned source first-seen commit, commit ancestry,
active effective intervals, duplicates, and cross-source collisions. A dirty
or substituted byte fails closed.

The current independent graph authority registry remains empty. Therefore the
audit reports source facts but emits zero Theme/sector-chain memberships and
all rotation, candidate, stage, Production, Trading, order, and REAL-capital
authorities as `false`. Korea and US still pin `theme_taxonomy/1`, while Crypto
has no common Theme-taxonomy pin. Those consumers must not be relabeled as
authority-compatible until an exact graph, evidence, and registry row are
ratified and the consumer contract migration is reviewed together.

Run against an immutable clean commit:

```bash
python3 -m rotation.theme_taxonomy_population 2026-09-01 \
  --trusted-commit "$(git rev-parse HEAD)"
```
