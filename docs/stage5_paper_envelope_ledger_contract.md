# Stage5 PAPER decision-envelope adapter

Contract version: `stage5_paper_envelope_ledger/2`.

This is an offline, fixture-only adapter. It validates one closed-authority
`stage4_paper_decision_envelope/1`, then reuses the existing P10-11 simulator
for virtual intent, fill event, ledger replay, and account-state arithmetic.
It does not select a candidate, calculate an entry, set a limit, size a
position, or write an operational ledger.

Every plan, fee, queue fraction, cash amount, mark, and orderbook level is a
caller-supplied fixture value. `NOT_EVALUATED` remains a valid decision status:
it is preserved in the virtual intent and is never promoted into an investment
decision. The envelope's closed-candle/PIT order is strict: candle close and
availability precede the decision, which precedes plan submission; the matched
snapshot must be after the decision candle close and after the plan.

The caller must supply three trust pins obtained outside the envelope:
`expected_envelope_sha256`, `expected_decision_packet_sha256`, and
`expected_decision_source_sha256`. A packet's own hashes prove only internal
integrity; they do not make the packet a trusted Stage4 output. Missing pins,
re-signed decisions, substituted sources, or a re-signed envelope fail closed.
The same envelope with the same pins remains deterministic, while changing a
plan or idempotency key cannot be replayed under the original envelope pin.

The result contains the exact simulator intent, fill event, hash-chain ledger,
replayed account state, and plan/fill/balance reconciliation. Revalidating the
result rebuilds it from the externally pinned envelope, so a rehashed output
edit or substituted input cannot pass.
Restart persistence remains the simulator's external content-addressed
snapshot mechanism; repository-internal account storage remains forbidden.

All Stage, candidate, entry, sizing, virtual-ledger operational, broker,
exchange-order, capital, production, trading, and real-capital authority flags
are false. There is no network, credential, KIS, Upbit, broker, or order path.
