# P0-02 06:57 Recovery Action Gate

`.github/scripts/evaluate_collect_recovery_gate.py` is a deterministic,
source-read-only decision boundary around the existing current-file briefing
readiness checker. Optional telemetry recording does not mutate source data.

- Before 06:57 KST it returns `RECOVERY_WINDOW_OPEN`; final failure, alerts,
  recovery guidance, and dispatch are forbidden.
- At or after 06:57 it reuses `check_briefing_readiness.py`, whose authority is
  current-date raw KRX/DART/SEC predicates first and the briefing read model
  second.
- Data ready with a degraded read model permits only a read-model repair
  candidate and degraded notice. Collector rerun guidance is prohibited.
- Confirmed collector data failure permits a manual-recovery-required notice,
  but the helper never runs `workflow_dispatch`; CIO approval remains required.
- The packet records actual evaluation time and labels `<=06:58:30` as a gate
  role candidate, through `07:00:00` as warning/review, and later evaluation as
  role unsuitable.

`--record` writes an append-only packet under
`data/operations/collect_recovery_gates/{KST_DATE}/gate-{HHMMSS}.json`. The
helper does not schedule itself or send notifications, so an independent
external caller and live operating proof remain required before P0-02 closes.
