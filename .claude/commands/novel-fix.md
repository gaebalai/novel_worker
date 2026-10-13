---
description: BRIEF 를 확정하고 역할별 모델·예산을 정해 집필 폴더를 만든다
argument-hint: "<slug> <BRIEF 판번호 V>"
---
확정(Fix). 인수: $ARGUMENTS — 되돌릴 수 없으므로 반드시 사용자 확인 후 실행한다.

1. `python3 studio/cli.py keys`로 현재 모드와 기본 역할 배정(작가·심사·문체·상위, 예비 포함)을 읽어 표로 보여 준다. solo 면 "쓰는 모델이 스스로 심사합니다"를 덧붙인다.
2. AskUserQuestion: 역할 배정(기본 그대로 / 바꾸기)과 예산(예: $3, $5, 제한 없음).
3. 바꾸면 `works/<slug>.roles.json`에 `{"writer": {"model": "sonnet", "fallback_models": ["gpt"]}, "reviewer": …, "polisher": …, "premium": …}`를 쓴다(네 역할 모두 이 형식을 권장. 문자열 `"sonnet"`이나 일부 역할만 써도 나머지는 모드 기본값으로 채워지고 예비 모델은 비워진다. 키가 없는 회사의 모델이면 fix 가 거부한다).
4. `python3 studio/cli.py fix <slug> <V> [--roles works/<slug>.roles.json] [--budget N]`.
5. 작품 폴더에서 `python3 tools/call_llm.py --ping writer`와 `--ping reviewer`로 연결을 확인한다(소액 비용). 실패하면 중계기·키를 점검하도록 보고.
6. 다음 단계 `/novel-run <slug>` 안내.
