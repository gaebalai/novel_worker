---
description: 처음 쓰기 전 점검 — 준비물·API 키·모드(solo/duo/all)·연결 시험을 확인하고 쉬운 말로 알려 준다
argument-hint: "[--ping]"
---
첫 실행 점검. 인수: $ARGUMENTS — 안내서는 `docs/GETTING_STARTED.md`. `.env`는 열지 않는다(키 유무는 중계기가 알려 준다).

1. **준비물**: `python3 --version`(3.9↑), `node --version`(18↑). 부족하면 설치 방법(`brew install python node`)을 알려 주고 멈춘다.
2. **.env 존재**: `test -f .env && echo 있음 || echo 없음`. 없으면 "`cp .env.example .env` 후 ANTHROPIC_API_KEY(필수)·OPENAI_API_KEY(권장)를 채우세요"라고 안내하고 [GETTING_STARTED.md 3장](docs/GETTING_STARTED.md) 링크를 주고 멈춘다.
3. **중계기**: `./nw --status`. 꺼져 있으면 `./nw --bridge`로 켠다.
4. **키와 모드**: `python3 studio/cli.py keys` 결과를 표로 보여 준다:
   - 키가 있는 업체 / 모드(solo·duo·all)와 그 뜻 / 기획안 모델·개수 / 역할별 모델(작가·심사·문체·상위, 예비 포함)
   - 종료 코드 2 면 `reason`·`problems`·`how_to_fix`를 보고 나눈다: 중계기 꺼짐 → `./nw --bridge` / Anthropic 키 없음 → `.env` 수정 후 `./nw --bridge-restart` / 모델 구성 문제(problems) → 키가 없는 회사 모델을 `studio/settings.json`·`NOVEL_KEY_MODE`에서 쓰고 있음. 안내 후 멈춘다.
   - solo 면: "OpenAI 키를 더하면 쓰는 모델과 심사하는 모델이 달라져 심사가 엄격해집니다(duo, 권장)"를 한 줄 덧붙인다.
5. **연결 시험**: 인수에 `--ping`이 있거나, AskUserQuestion("모델마다 아주 짧은 연결 시험을 할까요? 각 회사에 몇 센트 이하의 요금이 듭니다")에서 '예'를 고르면 `python3 studio/cli.py keys --ping`. 모델별 성공/실패를 표로. 실패하면 오류를 풀어서 설명한다(401=키·결제 문제, 404=모델 이름, 429=잠시 후, 연결 오류=중계기).
6. **Claude Code 본체**: 이 세션이 `./nw`로 시작됐는지(`echo $ANTHROPIC_BASE_URL`가 `http://127.0.0.1:…`인지) 확인한다. 아니면 "소설 엔진은 정상이지만, 다른 회사 모델 서브에이전트(idea-kimi 등)는 쓸 수 없습니다"라고 알린다.
7. **윤문 플러그인(선택)**: 스킬 목록에 humanize-korean 이 없으면 설치 명령 두 줄을 알려 준다(필수 아님).
8. 마지막에 "준비 완료 — `/novel-new`로 시작하세요. 처음에는 3장·장당 1500자 시험 작품을 권합니다"와 함께 점검 결과 요약(통과/주의 항목).
