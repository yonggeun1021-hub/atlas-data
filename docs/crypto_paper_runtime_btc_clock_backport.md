# Offline Crypto PAPER BTC clock compatibility

The exact offline runtime candidate `64d10489f346c0877cce26b6f4b6d718e552b6a8` is based on the previously
ratified `bf1fd99a4981b9a8f1cde7650ac914651052248d`. It backports only the
BTC capture/measurement packet fields from `699e317a8225e2c2751eba5ca616e97dd579f473`
and their strict legacy/current-shape consumer and regression tests from
`3253aad715bbdd63d53c95e1ec1de8e81d6bb039`.

The older runtime could not rederive a current registry containing those fields.
Adding only the producer fields exposed a second mismatch: the old axis consumer
rejected the extended shape and changed two defined evidence axes to undefined.
The candidate accepts the exact old shape or the exact four-field shape rederived
from raw evidence. Partial, extra, wrong-date, future and tampered inputs fail.

Validation on the candidate used observation
`74cb8eaf4e75240e48d889260506809927a53f23`, whose latest decision uses source
`264f0b939a6988a95ed1344e563853b74a31d4f7`: full decision rederivation passed
(1 test, 1.711 seconds), original registry regressions passed (10 tests,
21.030 seconds), and legacy/current clock and tamper regressions passed
(2 tests, 0.047 seconds). Independent review found no remaining issue.

Main already contains the same production semantics and regression cases.
This integration records the exact candidate in main's ancestry while retaining
main's existing code and tests byte for byte. It does not add duplicate test
methods. The runtime candidate remains the narrow baseline backport, not main.

This records technical compatibility for the existing offline PAPER scope.
It does not install or activate a host pin, service, timer, ledger or provider
request, and grants no new order, trading, production or real-capital authority.
As with the previous pin, historical whole-packet replay across all old producer
versions is not promised; legacy BTC input shape compatibility is tested.
