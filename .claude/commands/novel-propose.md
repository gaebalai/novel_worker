---
description: 여러 모델(Sonnet·GPT·GLM·Qwen·Kimi)에게 기획안을 받고 수렴·변주·이상치로 분류한다
argument-hint: "<slug> [--n 1] [--mode rolling] [지시]"
---
제안과 분류. 인수: $ARGUMENTS

1. `python3 studio/cli.py propose <slug>` — 모델·개수를 지정하지 않으면 **모드의 기본값**(solo: Sonnet 3안 / duo: Sonnet·GPT 2안씩, GLM·Qwen·Kimi 키가 더 있으면 키가 있는 모든 모델이 1안씩 / all: 5개사 1안씩)을 쓴다. 사용자가 원할 때만 `--models`·`--n`(키가 있는 회사의 모델만). `--mode rolling`이면 이전 분류에서 수렴한 골격을 피한다. 지시가 있으면 `--instruction`.
2. 종료 코드 2 = 한 안도 나오지 않음(모두 실패) → 오류를 풀어서 보고하고 멈춘다. 결과 JSON 의 `errors`에 있는 모델은 1번만 다시 시도한다(`--models <그 모델>`). 또 실패하면 빼고 보고한다.
3. `python3 studio/cli.py classify <slug>` → `show <slug>`(전체, `--brief` 없이)로 안마다 제목·장르·시놉시스·분류(label)·모델·비용을 읽는다. 볼거리(highlight)·매력(appeal)·꼭 지킬 것(must_keep)·무대(setting)는 show 에 없으니 `works/<slug>/studio.json`의 `proposals.<pid>`에서 **읽기만** 한다.
4. **비교 문서를 만든다** — `works/<slug>/notes/PROPOSALS.md`:
   - 맨 위: 비교 표(번호 | 모델 | 제목 | 장르 | 분류 | 비용), 분류의 뜻 한 줄(수렴=여러 모델이 비슷한 뼈대 / 변주 / 이상치=혼자 다른 안)
   - 그 아래 안마다 `## P01 제목 (모델·분류)` + 무대·시놉시스·볼거리·매력·꼭 지킬 것(읽은 값 그대로, 당신이 고쳐 쓰지 않는다)
   - 숨긴 안(hidden)은 빼고, 라운드가 여러 번이면 최신 라운드를 위에.
5. 대화에는 비교 표의 요약과 링크만: "자세히는 [PROPOSALS.md](works/<slug>/notes/PROPOSALS.md)를 VS Code 에서 열어(미리보기 `Cmd+Shift+V`) 보세요."
   사용자가 원하면 서브에이전트 의견(`idea-kimi` 등, `critic-gpt`는 해당 안 내용을 프롬프트에 넣어서)을 덧붙인다.
6. AskUserQuestion 으로 고를 안을 묻는다(다시 제안·rolling 도 선택지에 포함).
7. 고르면 `select <slug> <pid>` 후 `works/<slug>/notes/SELECTION.md`에 고른 안과 이유(사용자의 말 그대로)를 쓴다. 다음 단계는 `/novel-brief <slug>`.
