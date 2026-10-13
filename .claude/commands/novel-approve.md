---
description: 설정서(CANON)를 요약·지문과 함께 보여 주고, 사용자가 '승인'을 고르면 승인한다
argument-hint: "<slug>"
---
CANON 승인. 인수: $ARGUMENTS — CLAUDE.md 2-2 절차를 그대로 따른다.

1. `status <slug>`의 phase 가 `canon_hold`이거나, `escalation.code`가 `DESIGN_EXHAUSTED`이고 `canon/CANON.md`가 있을 때만 진행한다(설계가 정해진 횟수 안에 통과하지 못했지만 사람이 읽고 그대로 받아들이는 경우). 그 밖에는 승인하지 않고 현재 상태를 알린다.
2. `works/<slug>/canon/CANON.md`에서 작품 요지·인물 행동 원리(제4절)·세계의 전제(제6절)·추가 요소 신고(제13절)를 요약하고, 링크 `[CANON.md](works/<slug>/canon/CANON.md)`를 준다("VS Code 에서 열어 읽어 보세요").
3. 지문 계산: `shasum -a 256 works/<slug>/canon/CANON.md | cut -c1-8` → "지문 xxxxxxxx 의 CANON" 으로 함께 보여 준다.
4. AskUserQuestion(하나만): **승인**(이 지문의 CANON 으로 집필 시작) / **수정 지시**(무엇을 바꿀지 Other 에 적기) / **보류**.
   - 승인: `cd works/<slug> && NOVEL_WORKER_APPROVE=<3의 지문> python3 tools/novelctl.py approve-canon`. 지문이 다르면(그사이 CANON 변경) `REFUSED: …터미널…` 메시지로 거부된다 — 승인 자체가 금지된 것이 아니니, 지문을 다시 계산해 보여 주고 1부터 다시 묻는다. 성공하면 `notes/INTERVENTIONS.md`에 "일시 · 사용자가 AskUserQuestion 으로 승인 · 지문" 한 줄, 그리고 `/novel-run <slug>`로 이어 간다.
   - 수정 지시: `python3 studio/cli.py engine <slug> redesign --feedback "<사용자의 말>"` 후 `/novel-run <slug>`.
   - 보류: 아무것도 하지 않는다.
5. 사용자가 '승인'을 고르지 않았는데 승인 명령을 실행하지 않는다. 승인 명령은 권한 설정(ask)으로 실행 직전에 한 번 더 허락을 묻는다.
