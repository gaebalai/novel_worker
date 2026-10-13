---
description: 완성된 장의 "AI 티"를 humanize-korean(im-not-ai)으로 윤문한다. 대사는 그대로, 기계 검사를 통과한 장만 반영
argument-hint: "<slug> [장 번호들, 예: 1 3]"
---
AI 티 윤문. 인수: $ARGUMENTS

## 0. 준비 확인
- 윤문 스킬의 작업 폴더 `_workspace/`(저장소 최상위)와 `works/<slug>/humanize/`는 이 명령이 쓰는 곳이다(git 제외, 엔진 guard 허용).
- 스킬 목록에 공식 플러그인의 `humanize-korean` 스킬(플러그인 `humanize-korean@im-not-ai`)이 있어야 한다. 없으면 아래를 안내하고 **멈춘다**(다른 판의 humanize 스킬로 대신하지 않는다):
  ```
  /plugin marketplace add epoko77-ai/im-not-ai
  /plugin install humanize-korean@im-not-ai
  ```
- `status <slug>`의 phase 가 `done`이어야 한다.
- `works/<slug>/output/proofread_edits.json`이 이미 있으면: "윤문하면 교정안의 원문이 원고와 어긋나 적용이 실패할 수 있습니다. 윤문 뒤 교정을 다시 하시겠어요?"를 AskUserQuestion 으로 확인한다.
- 비용: 스킬은 Opus 를 쓰며 Claude Code 구독 사용량으로 장마다 1~3회 호출된다. 대상 장 수와 함께 알리고 진행 여부를 묻는다.

## 1. 장마다 (대상: 인수의 장, 없으면 전체 장)
1. 기준 원고 = `works/<slug>/polished/chNN.md`가 있으면 그것, 없으면 `works/<slug>/chapters/chNN.md`. 이것을 `works/<slug>/humanize/chNN.input.md`로 **복사**한다(원본은 건드리지 않는다).
2. humanize-korean 스킬을 그 입력 파일로 실행한다. 스킬에 다음 조건을 함께 준다:
   - 장르: `essay`(스킬에 소설 장르가 없다), 강도: **보수**, 최소 심각도: **S2**
   - 소설 보존 조건(그대로 전달):
     "이 글은 소설의 한 장이다. (1) 따옴표(“ ”, 「 」, " ") 안의 대사는 한 글자도 바꾸지 않는다. (2) 장면 구분 ＊ 줄, 장 제목, 마지막 한 문장은 그대로 둔다. (3) 인물·사건·사실·고유명사·시점은 바꾸지 않는다. (4) 작가가 의도한 문학적 비유·리듬·문장 길이 변화는 평범한 설명문으로 고치지 않는다. 지문(서술)의 번역투·기계적 병렬·상투구만 고친다."
3. 스킬 결과(`_workspace/<run>/final.md`)로 기계 검사를 거쳐 받아들인다:
   `cd works/<slug> && python3 tools/novelctl.py accept-polish N --from <final.md 절대경로> --label humanize`
   - 종료 코드 0: `polished/chNN.md`에 반영됨(이전 판은 `polished/chNN.before-humanize.md`, 이미 있으면 `…-2.md`·`…-3.md`로 보관 — 덮어쓰지 않는다).
   - 종료 코드 2: 받아들이지 않음 — 출력된 이유(대사 변경, 분량, 장면 구분, 마지막 문장 등)를 기록하고 원고는 그대로 둔다. 재시도는 사용자가 고를 때만(같은 장 최대 1회).
4. 결과 파일을 손으로 고치거나 `polished/`·`chapters/`에 직접 쓰지 않는다. 반영은 accept-polish 로만.

## 2. 보고
- 표: 장 | 결과(반영/거부) | 스킬 변경률 | 글자 수 변화 | 거부 이유
- 비교 링크: 반영된 장마다 `[윤문 후](works/<slug>/polished/chNN.md)` · `[윤문 전](works/<slug>/humanize/chNN.input.md)` — VS Code 에서 두 파일을 나란히 열거나 `code --diff`로 비교하도록 안내.
- 다음 단계: 교정(`/novel-finish`의 proofread)과 EPUB. 윤문 결과가 마음에 들지 않는 장은 `polished/chNN.before-humanize.md`(있으면) 또는 chapters 원고로 되돌릴 수 있음을 알린다(되돌리기는 사용자 지시로, accept-polish 로 다시 받아들인다).
