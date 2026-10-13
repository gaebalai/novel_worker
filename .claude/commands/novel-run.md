---
description: 집필 엔진을 돌리고 진행을 지켜본다(멈출 곳에서 멈춘다)
argument-hint: "<slug>"
---
집필 진행. 인수: $ARGUMENTS

1. 작품 폴더에 `STOP`이 있으면 아무것도 하지 않고 알린다.
2. `python3 studio/cli.py status <slug>`. 이미 `running`이면 새로 띄우지 않는다.
3. `python3 studio/cli.py engine <slug> start` (셸에서 떼어 돈다).
4. 2~5분 간격으로 `status <slug>`를 확인한다(Monitor 또는 짧은 대기 후 재확인). 매번 한 줄로 진행 상황(phase·current·장 수)을 알리고, 새로 확정된 장이 있으면 링크 `[제N장](works/<slug>/chapters/chNN.md)`를 붙인다(본문은 읽어 붙이지 않는다).
5. 멈추는 조건:
   - `phase=canon_hold` → `/novel-approve <slug>` 절차로.
   - `escalation`이 있음 → CLAUDE.md 2-3 표대로 요약하고, 근거 파일 링크(`work/chNN_feedback.md` 등)와 함께 `/novel-rescue <slug>` 선택지를 보여 준다.
   - `phase=done` → `output/final_report.md` 총평 요약 + 링크 + 비용, 다음은 `/novel-finish <slug>`.
6. 보고 끝에 비용(`costs <slug>`)을 붙인다.
