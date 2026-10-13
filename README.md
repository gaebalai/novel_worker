# novel_worker

## 개발자: 김재우 (Damon Jaewoo Kim) 
## email: jaewoo@claudecode.to

Claude Code가 진행을 맡고, **Claude Sonnet 5.5 · GPT 6.1-Sol · GLM · Qwen · Kimi**를 섞어 한글 소설을 쓰는 도구입니다.
웹 화면 대신 Claude Code와의 대화로 진행하고, 모델은 각 회사 API에 직접 연결합니다.

## 어떻게 동작하나

```
Claude Code(진행자) ──┐
studio/cli.py(기획) ──┼─▶ bridge(127.0.0.1:8788) ─┬ claude-*  → Anthropic API
engine(집필) ─────────┘   bridge/proxy.mjs          ├ gpt-*     → OpenAI API
                                                     ├ glm-*     → Zhipu(z.ai) API
                                                     ├ qwen*     → Alibaba DashScope API
                                                     └ kimi-*    → Moonshot API
```

- **중계기(bridge)**: Anthropic 형식으로 들어온 요청을 모델 이름을 보고 각 회사 형식으로 바꿔 보냅니다. Claude Code 본체와 집필 엔진이 같은 중계기를 씁니다.
- **집필 엔진**: 설계 → 심사 → 사람 승인 → 각 장 집필·심사 → 최종 심사 → 교정 → EPUB. 기계 검사·guard·비용 기록도 그대로 씁니다.
- **모델 별칭**: [models.json](models.json)에서 `sonnet`·`gpt`·`glm`·`qwen`·`kimi`·`opus`·`haiku` → 실제 모델 ID와 가격을 정합니다.

## 준비 — 처음이면 [시작하기 안내서](docs/GETTING_STARTED.md)

필요한 것: macOS, Python 3.9+, Node.js 18+, Claude Code. pip install은 필요 없습니다.
API 키는 **Anthropic 하나만 있어도 동작**하고, **Anthropic + OpenAI 두 개를 권장**합니다.

```
cp .env.example .env      # ANTHROPIC_API_KEY(필수)·OPENAI_API_KEY(권장)를 적는다
./nw                      # 중계기를 띄우고 Claude Code를 실행 — 첫 줄에 키·모드가 나온다
```

`./nw` 안에서 `/novel-setup`(첫 점검) → `/novel-new`로 시작합니다.

| 명령 | 하는 일 |
|---|---|
| `/novel-setup` | 처음 쓰기 전 점검(준비물·키·모드·연결 시험) |
| `/novel-new` | 테마를 정해 작품을 만든다 |
| `/novel-propose` | 여러 모델이 기획안을 내고, 비슷한 안(수렴)과 동떨어진 안(이상치)을 가린다 |
| `/novel-brief` | 고른 안으로 집필 지시서(BRIEF)를 만들고 기계 검사한다 |
| `/novel-fix` | 역할별 모델·예산을 정해 확정한다 |
| `/novel-run` | 집필을 돌리고 지켜본다 |
| `/novel-approve` | 설정서(CANON)를 확인하고 승인한다(지문을 보여 주고 '승인'을 고르면 진행) |
| `/novel-rescue` | 멈췄을 때 선택지를 보고 고른다 |
| `/novel-finish` | 문체 다듬기·AI 티 윤문·교정(체크리스트로 선택)·표지·EPUB |
| `/novel-humanize` | 완성된 장의 "AI 티"를 [im-not-ai](https://github.com/epoko77-ai/im-not-ai)로 윤문(대사는 그대로, 기계 검사 통과분만 반영) |
| `/novel-status` | 진행 상황과 비용 |

서브에이전트 `idea-kimi`·`idea-glm`·`idea-qwen`(아이디어 상담)과 `critic-gpt`(편집자 비평)도 있습니다. 그 회사 키가 있고 `./nw`로 시작했을 때만 쓸 수 있으며, 그 회사 API 요금이 따로 나갑니다.

### 키에 따른 모드와 역할 배정 (자동)

| 등록한 키 | 모드 | 기획안 | 분류 | 작가 | 심사 | 문체 다듬기 |
|---|---|---|---|---|---|---|
| Anthropic | **solo** (최소) | Sonnet 3안 | Haiku | Sonnet (예비 Opus) | Sonnet (예비 Opus) | Sonnet |
| Anthropic + OpenAI | **duo** (권장) | Sonnet·GPT 2안씩 | GPT | Sonnet (예비 GPT) | GPT (예비 Sonnet) | Sonnet (예비 GPT) |
| 다섯 회사 모두 | **all** | 5개사 1안씩 | GLM | Sonnet (예비 Kimi) | GPT (예비 GLM) | Kimi |

- BRIEF와 상위 모델(예외 처리·교정)은 모든 모드에서 Sonnet입니다. duo에 GLM·Qwen·Kimi 키가 더 있으면 키가 있는 모든 모델이 기획안을 1안씩 냅니다(역할은 그대로).
- 현재 구성은 `python3 studio/cli.py keys`로 봅니다. 모드 고정은 `.env`의 `NOVEL_KEY_MODE`, 역할 직접 지정은 `studio/settings.json` — [안내서 3장](docs/GETTING_STARTED.md#3-환경설정-env).

## VS Code 에서 쓰는 법

긴 원고는 터미널(대화창)에서 읽기 불편하므로, **대화는 Claude Code 창에서, 읽기는 편집기 탭에서** 합니다.

- Claude Code가 답에 붙이는 파일 링크를 누르면 편집기에서 열립니다. `Cmd+Shift+V`로 Markdown 미리보기.
- 기획안 비교 `works/<작품>/notes/PROPOSALS.md`, 기획서 `notes/BRIEF_vN.md`, 설정서 `canon/CANON.md`, 장 원고 `chapters/chNN.md`(다듬은 원고 `polished/chNN.md`).
- 교정안은 `notes/PROOFREAD.md` 체크리스트로 만들어 줍니다. 체크를 고친 뒤 "다 골랐어"라고 하면 반영합니다.
- 윤문 전후 비교: 두 파일을 나란히 열거나 터미널에서 `code --diff works/<작품>/humanize/ch01.input.md works/<작품>/polished/ch01.md`.

## AI 티 윤문 (선택)

[im-not-ai](https://github.com/epoko77-ai/im-not-ai)(MIT)의 humanize-korean 스킬로 "AI가 쓴 티"(번역투·기계적 병렬·상투구)를 줄입니다. Claude Code에서 한 번 설치합니다.

```
/plugin marketplace add epoko77-ai/im-not-ai
/plugin install humanize-korean@im-not-ai
```

- `/novel-humanize <작품>`: 장마다 윤문 → `novelctl.py accept-polish`로 기계 검사(분량 0.9~1.3배, 장면 구분·마지막 문장 유지, **대사는 한 글자도 바뀌면 거부**) → 통과한 장만 `polished/`에 반영. `chapters/` 원고는 바뀌지 않습니다.
- 이 스킬은 원래 에세이·칼럼용이라 소설 장르 규칙이 없습니다. 그래서 "보수적으로, 지문만, 문학적 문체는 유지"하도록 조건을 붙여 부릅니다. 결과가 마음에 들지 않으면 이전 원고로 되돌릴 수 있습니다.
- 스킬은 Opus로 돌아 Claude Code 구독 사용량을 씁니다(장마다 1~3회).

## 실행기 `nw`

```
./nw                    중계기 확인/기동 → Claude Code 실행
./nw --bridge           중계기만 띄운다(터미널에서 엔진을 직접 돌릴 때)
./nw --status           업체별 키 유무
./nw --bridge-restart   proxy.mjs 수정 후
./nw --stop             중계기 끄기
```

포트를 바꾸려면 `.env`에 `NOVEL_BRIDGE_PORT=…` 한 줄을 적습니다. 실행기·엔진·기획 도구가 모두 이 값을 따릅니다.

`.env`의 키는 **중계기에만** 넘어갑니다. 엔진과 기획 도구도 키를 직접 쓰지 않고 중계기에 맡깁니다. Claude Code 본체에는 넘기지 않습니다. Claude Code는 `ANTHROPIC_API_KEY`가 보이면 구독 대신 API로 요금을 매기기 때문입니다. Claude Code 본체는 원래 로그인(구독)을 그대로 씁니다.

## 비용 없이 시험하기

```
python3 bridge/regress.py                              # 중계기 변환·업체 배정(가짜 서버, 키 불필요)
python3 -m unittest discover -s engine/tests           # 엔진 처음부터 끝까지(가짜 모델)
```

## 주의

- 모델 ID와 가격은 2026-10-14에 각 회사 공식 문서로 확인했습니다: `gpt-6.1-sol` $2/$10, `kimi-k3` $3/$15, `glm-5.3` $1.4/$4.4, `qwen3.8-max` $2/$6, `claude-sonnet-5-5` $2/$10 (100만 토큰당 입력/출력).
- GPT 6.1-Sol은 chat/completions 방식에서 도구 호출을 지원하지 않습니다. 중계기가 도구 목록을 빼고 보내므로 글쓰기·심사·비평에는 문제가 없지만, Claude Code의 메인 모델이나 파일을 다루는 서브에이전트로는 쓸 수 없습니다.
- 비용은 위 가격으로 추산한 값이며 캐시 할인은 반영하지 않습니다.
- 중계기는 이 컴퓨터(127.0.0.1)에서 온 요청만, 그리고 JSON 요청만 받습니다(웹 페이지가 몰래 과금 호출을 하지 못하게).
- 표지는 제목 글자를 Chrome(또는 Edge·Brave)으로 그리므로 Chrome이 필요합니다. 표지 그림 생성은 중계기로 할 수 없습니다. 기본은 글자와 색만으로 만든 표지입니다(`--model none`).
- 중국 본토 계정이면 `.env`에서 `ZAI_BASE_URL`·`DASHSCOPE_BASE_URL`·`KIMI_BASE_URL`을 바꿉니다.

## 구성

```
nw, bridge/        실행기와 중계기(+ 회귀 테스트)
models.json        모델 별칭·가격
engine/            집필 엔진(별칭·중계기·예비 모델 대응)
studio/            기획 CLI(웹 화면 제외)
CLAUDE.md          Claude Code 진행 규칙
.claude/           /novel-* 명령, 서브에이전트, 권한 설정
docs/              설계 설명서
LICENSE            MIT
works/             만든 작품(git 제외)
```

## 라이선스

MIT. novel_worker는 [novel_worker](https://github.com/gaebalai/novel_worker)(MIT, Copyright (c) 2026 gaebalai)에 공개하고 있습니다. 
