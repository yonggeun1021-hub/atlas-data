"""Observation Layer — Acquisition → Observation → Normalization → Store → Pair → Evaluator.

현재 구현 범위:
  - 층 ③ Normalization (`normalize` · `record`)
  - 층 ④ Store (`store` · `persist_rule0022`)

⛔ 층 ⑤ Pair Validation · ⑥ Evaluator 는 아직 구현되지 않았다.
   파일이 없는 층을 앞 단계의 성공으로 추정하거나 자동 연결하지 않는다.
"""
