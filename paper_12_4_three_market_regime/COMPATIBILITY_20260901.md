# PAPER 12-4 latest-main fixture compatibility seal

The original implementation and exact receipt lineage remain pinned to local
commit `25ee4b1ec0634ffb9b7f5f33cac4fd6e676371c1` by the Gate 2/3 aggregate.
After rebasing its isolated package onto public `main` at `d972c207`, the
read-only latest KRX and US pointer bytes had advanced. Only the TEST_ONLY
fixture canonical-snapshot hashes and their enclosing source hashes were
re-sealed; no market source, writer, policy, threshold, or authority changed.

Deterministic compatibility output at `2026-08-31T01:00:00Z`:

- KRX receipt: `7d28296b6e71bc78adcd517fe177133ca1fb60b99c032ae9735296a02f2b1de7`
- US receipt: `1a30619eb54a9c947f713edf4e63fc99ef088c579ee2a0dec80823042c5192be`
- Crypto receipt: `e72ec4ddc65ee1d403e1b8f526a82ad065a052a5a78ebdb2e991e60bb14ce371`
- Header: `48e6de233cda6cc3d805b28d850f50741038abdd7090e8153563ab6c6b90e9cf`
- Bundle: `c43b615de402c3c2053cdf321e14e7f6f2123d048b63932c729628a595aa4cd9`

This compatibility seal is regression evidence only. The original exact
receipt pins in commit `25ee4b1…` are not replaced or promoted.
