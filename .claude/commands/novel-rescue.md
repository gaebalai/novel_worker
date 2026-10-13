---
description: 집필이 멈췄을 때 선택지를 보여 주고, 사용자가 고른 처리만 한다
argument-hint: "<slug>"
---
예외 처리. 인수: $ARGUMENTS

1. `status <slug>`의 `escalation`(code·장 번호)을 읽는다. 없으면 그렇다고 알리고 끝낸다.
2. CLAUDE.md 2-3 표의 "할 일"대로 관련 파일(`work/*_feedback.md`, `output/final_report.md`)을 요약한다. 본문은 통째로 읽지 않는다.
3. AskUserQuestion 으로 표의 선택지를 보여 준다. 상위 모델을 부르는 선택지(rescue_*)에는 "비용이 듭니다"를 붙이고, `--model`(다른 모델로)과 `--note`(지시) 옵션도 알려 준다.
4. 고른 것만 실행한다:
   - `engine <slug> rescue_canon [--model M] [--note "..."]`
   - `engine <slug> rescue_chapter --ch N [--model M] [--note "..."]`
   - `engine <slug> resolve --flags retry|accept|done|ledger-done`
   - `engine <slug> reset_chapter --ch N`
   - `engine <slug> redesign --feedback "..."`
   - CANON 을 읽고 그대로 승인(DESIGN_EXHAUSTED): `/novel-approve <slug>` 절차로
   - 예산 올리기(BUDGET_EXCEEDED): 금액을 AskUserQuestion 으로 정한 뒤 `engine <slug> set_budget --budget N` → `engine <slug> resolve`
   - rescue_* 는 끝날 때까지 기다리는 명령이라 Bash 의 `run_in_background` 로 실행하고, 끝나면 `status` 로 확인한다.
5. `notes/INTERVENTIONS.md`에 기록하고, `status`로 효과를 확인한 뒤 `/novel-run <slug>`로 이어 갈지 묻는다.
