---
description: 완성 원고를 다듬고(문체·AI 티 윤문) 교정·표지·EPUB 을 만든다
argument-hint: "<slug>"
---
마무리. 인수: $ARGUMENTS — `status`의 phase 가 `done`일 때만. 순서는 **문체 다듬기 → AI 티 윤문 → 교정 → 표지 → EPUB** (교정안은 원고 문장과 정확히 일치해야 적용되므로, 윤문은 교정보다 먼저).

1. AskUserQuestion(복수 선택): 문체 다듬기(polish, 모드의 문체 모델 — `keys`의 polisher) / AI 티 윤문(humanize, Opus·구독 사용량) / 상위 모델 교정(proofread, 비용) / 표지 / EPUB.
2. polish: `engine <slug> polish`(떼어서 돈다. `status`로 끝을 확인) → 끝나면 `cd works/<slug> && python3 tools/novelctl.py assemble --polished` 로 `output/polished.md`. 링크를 준다.
3. 윤문: `/novel-humanize <slug>` 절차를 따른다.
4. proofread: `engine <slug> proofread`(끝날 때까지 기다리는 명령 — Bash `run_in_background`로, 장이 많으면 `--chapters 1,2`로 나눠서) → `output/proofread_edits.json`으로 **체크리스트** `works/<slug>/notes/PROOFREAD.md`를 만든다:
   - 안마다 `- [x] 3. 제N장 · 종류 — 「원문」 → 「수정」 (이유)` (번호는 1부터, 체크는 현재 enabled 값대로)
   - `rejected`(원고에서 원문을 못 찾아 기계 기각)인 안은 `- [ ] ~~…~~ (기각: 원문 없음)`으로, 고를 수 없다고 적는다.
   - 대화: "VS Code 에서 [PROOFREAD.md](works/<slug>/notes/PROOFREAD.md)의 체크를 고친 뒤 '다 골랐어'라고 말씀해 주세요."
   - 사용자가 다 골랐다고 하면 PROOFREAD.md 를 읽어 `[x]` 번호를 모아 `python3 studio/cli.py edits <slug> --enabled <번호들>`(빈 값이면 모두 안 함) → 결과 JSON 의 enabled 를 사용자에게 확인시킨 뒤 `engine <slug> apply_proofread` → `output/proofread_changes.md` 링크.
5. 표지: 중계기로는 그림 생성을 할 수 없어 기본은 글자·색 표지다. 글자 조판에 Chrome(또는 Edge·Brave)이 필요하다 — 없으면 "표지를 만들지 못했습니다"로 끝나니 설치를 안내한다. `engine <slug> cover --model none --style simple --title-dir vertical|horizontal --font mincho|gothic|casual --author "..."`(제목 방향·서체·저자명을 묻는다). 결과 `images/cover.png` 링크.
6. EPUB: 교정을 적용했으면 `python3 studio/cli.py engine <slug> epub --proofread`(교정판), 아니면 `engine <slug> epub` — 이때도 다듬기·윤문본(`polished/`)이 있으면 그것을 쓴다. 결과 파일(output/*.epub) 경로를 알려 준다.
7. 최종 보고: 완성 파일 위치(output/), 총 비용, `final_report.md` 총평 한 단락.
