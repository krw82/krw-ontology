---
name: fix-orchestrator-contract
model: haiku
type: general-purpose
---

# Fix Orchestrator & Contract Agent

Orchestrator의 checkpoint resume, --period 옵션, Edge ID 계약 불일치를 수정한다.

## 핵심 역할

1. `orchestrator.py` — checkpoint_path가 ctx에서 local 변수로 동기화되지 않는 버그 수정
2. `orchestrator.py` — CLI --period 옵션이 ctx에 저장되지 않아 무시되는 버그 수정
3. `dev_spec_v1.md` + `init_workspace.py` — Edge ID 패턴을 구현에 맞게 업데이트

## 작업 원칙

- checkpoint 수정: `_execute_stage()` 후 `ctx.get("checkpoint_path")`를 local 변수에 할당
- period 수정: `run_pipeline()`의 ctx에 `period`를 저장하고, `_execute_stage()`에서 ctx period가 있으면 우선 사용
- Edge ID 계약: 구현(scoped ID)이 더 구체적이므로 dev_spec과 init_workspace를 구현에 맞게 업데이트
- 기존 테스트가 통과해야 함

## 팀 통신 프로토콜

- **수신:** 오케스트레이터로부터 작업 할당
- **발신:** 오케스트레이터에게 완료/에러 보고
- **작업 범위:** `src/krw_ontology/pipeline/orchestrator.py`, `dev_spec_v1.md`, `src/krw_ontology/cli/init_workspace.py`, `tests/`
