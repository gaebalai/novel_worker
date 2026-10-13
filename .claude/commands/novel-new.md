---
description: 새 소설 작품을 만든다(테마 입력 → works/<slug>)
argument-hint: "[작품이름-slug] [테마를 자유롭게]"
---
새 작품을 만든다. 인수: $ARGUMENTS

1. `./nw --status`로 중계기가 켜져 있는지 확인한다(꺼져 있으면 `./nw --bridge`). 끝의 모드 줄에 ⚠(Anthropic 키 없음)가 있으면 `/novel-setup`으로 돌려보내고 멈춘다. 처음 쓰는 사용자면 3장·장당 1500자 시험 작품을 권한다.
2. 인수와 대화에서 테마 항목을 모은다(CLAUDE.md 1절의 theme.json 항목). 빠진 핵심 항목(장르·테마·장 수·결말 유형)은 AskUserQuestion 으로 한 번에 묻는다. slug 가 없으면 영문 소문자·하이픈으로 제안한다.
3. `works/<slug>.theme.json`을 쓰고 `python3 studio/cli.py new <slug> --theme works/<slug>.theme.json`.
4. `show <slug> --brief`로 생성을 확인하고, 테마 요약과 다음 단계(`/novel-propose <slug>`)를 알려 준다.
