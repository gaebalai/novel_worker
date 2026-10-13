---
description: 고른 안으로 집필 지시서(BRIEF)를 만들고 기계 검사(lint)한다
argument-hint: "<slug> [고칠 지시]"
---
BRIEF 생성·수정. 인수: $ARGUMENTS

1. `show <slug> --brief`로 `selected`가 있는지 확인한다(없으면 `/novel-propose`로 돌려보낸다).
2. 처음이면 `python3 studio/cli.py brief <slug>`(기본 sonnet). 지시가 있으면 `--instruction "..." --base <최신 V>`.
3. `brief-show <slug> <V>`로 lint 를 본다. 종료 코드 2(지적 있음)면 `brief <slug> --base <V>`로 다시 만든다. 2번까지. 그래도 안 되면 지적을 보고하고 멈춘다.
4. `brief-show <slug> <V> --markdown`의 `markdown` 값을 **그대로** `works/<slug>/notes/BRIEF_v<V>.md`로 저장한다(당신이 고쳐 쓰지 않는다. 이것은 읽기용 사본이고 원본은 studio.json).
5. 대화에는 핵심(제목·장 구성·주요 인물·복선 계획·글자 수)만 요약하고 링크 `[BRIEF_v<V>.md](works/<slug>/notes/BRIEF_v<V>.md)`를 준다.
6. AskUserQuestion: 이대로 확정 / 지시를 붙여 수정 / 편집자 비평 받기(critic-gpt — 요약을 프롬프트에 넣어 호출). 확정이면 `/novel-fix <slug> <V>` 안내.
