---
description: 작품 진행 상황과 비용을 한눈에 보여 준다
argument-hint: "[slug]"
---
상태 보기. 인수: $ARGUMENTS

- slug 가 없으면 `python3 studio/cli.py works`로 목록을 보여 주고 끝낸다.
- 있으면 `show <slug> --brief`, (확정된 작품이면) `status <slug>`, `costs <slug>`를 읽고 다음 형식으로 답한다:
  - 지금 단계(쉬운 말로) / 끝난 장 수 / 멈춘 이유(있으면) / 다음 할 일(명령)
  - **지금 읽을 파일** 링크: 단계에 맞게 `notes/PROPOSALS.md`·`notes/BRIEF_vN.md`·`canon/CANON.md`·최근 `chapters/chNN.md`·`work/chNN_feedback.md`·`output/final_report.md` 중 있는 것
  - 비용: 기획 $… / 집필 $…(작가·심사·문체·상위). 가격 미등록 모델이 있으면 그 사실을 함께.
- `python3 studio/cli.py keys --short`로 키·모드를 한 줄로(실패하면 중계기 꺼짐 → `./nw --bridge`).
