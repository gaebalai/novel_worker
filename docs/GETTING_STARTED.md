# novel_worker 시작하기

처음 설치해서 첫 소설(짧은 시험 작품)을 완성하기까지의 안내입니다. 개발을 몰라도 순서대로 따라 하면 됩니다.

- 걸리는 시간: 준비 20~30분, 시험 작품 한 편 30분~1시간(대부분 기다리는 시간)
- 최소 조건: **Anthropic API 키 하나**. 권장은 **Anthropic + OpenAI 키 두 개**.

---

## 0. 큰 그림 — 무엇이 돈을 쓰나

| 무엇 | 하는 일 | 요금 |
|---|---|---|
| Claude Code | 진행자. 당신과 대화하며 도구를 돌린다 | 지금 쓰는 Claude 구독(로그인) 그대로 |
| Anthropic API 키 | 소설 엔진 안의 Claude(작가·기획서·기획안·예외 처리) | API 사용량만큼 **별도 청구** |
| OpenAI API 키 | 소설 엔진 안의 GPT(심사·기획안·분류, 작가의 예비) | API 사용량만큼 **별도 청구** |
| 서브에이전트(선택) | idea-kimi·critic-gpt 등 다른 회사 모델 상담 | 그 회사 API 키로 **별도 청구** |
| AI 티 윤문(선택) | 완성 원고 윤문(Opus) | Claude Code 구독 사용량 |

API 키는 `.env` 파일에만 적습니다. 이 키는 이 컴퓨터의 **중계기**만 읽고, Claude Code 본체에는 넘어가지 않습니다(그래서 Claude Code가 구독 대신 API로 요금을 매기는 일이 없습니다).

---

## 1. 준비물 확인

터미널에서 하나씩 실행해 버전이 나오면 됩니다.

```
python3 --version     # 3.9 이상
node --version        # v18 이상 (권장 v20 이상)
claude --version      # Claude Code
```

- 표지를 만들려면 **Google Chrome**(또는 Edge·Brave)이 있어야 합니다(제목 글자를 Chrome으로 그립니다).

- 없으면: Python·Node는 `brew install python node`(Homebrew), Claude Code는 공식 안내대로 설치한 뒤 `claude`를 한 번 실행해 로그인합니다.
- VS Code를 쓰면 원고 읽기가 훨씬 편합니다(아래 5장).

---

## 2. API 키 발급

### Anthropic (필수)
1. https://platform.claude.com 에 로그인 → **Settings → API keys**(https://platform.claude.com/settings/keys) → 키 만들기
2. **Billing**에서 결제 수단·크레딧을 설정합니다(없으면 호출이 거부됩니다).
3. 키(`sk-ant-…`)를 복사해 둡니다. 다시 볼 수 없으니 안전한 곳에.

### OpenAI (권장)
1. OpenAI 대시보드(https://platform.openai.com)에 로그인 → **API keys** → 새 키 만들기
2. **Billing**에서 크레딧을 충전합니다.
3. 키(`sk-…`)를 복사해 둡니다.

> 지출 한도: 두 회사 모두 콘솔에서 월 사용 한도를 걸 수 있습니다. 처음에는 낮게(예: $10) 걸어 두기를 권합니다. novel_worker 자체에도 작품별 예산(`/novel-fix`에서 정함)이 있어, 넘으면 집필이 멈춥니다. 이 예산은 집필 단계 비용만 세고(기획 단계 비용은 빠짐), 마지막 호출 1회만큼은 넘을 수 있습니다.

---

## 3. 환경설정 (.env)

novel_worker 폴더에서:

```
cp .env.example .env
```

`.env`를 편집기로 열어 두 줄을 채웁니다(나머지는 비워 둬도 됩니다).

```
ANTHROPIC_API_KEY=sk-ant-여기에_붙여넣기
OPENAI_API_KEY=sk-여기에_붙여넣기
```

- 따옴표는 있어도 없어도 됩니다. `=` 앞뒤에 공백을 넣지 마세요.
- Anthropic 키 하나만 적어도 동작합니다(solo 모드). 나중에 OpenAI 키를 더하면 자동으로 duo 모드가 됩니다.
- 키를 바꾼 뒤에는 `./nw --bridge-restart`로 중계기를 다시 띄워야 반영됩니다.

### 모드 — 키에 따라 자동으로 정해진다

| 등록한 키 | 모드 | 기획안 | 작가 | 심사 | 문체 다듬기 | 분류 |
|---|---|---|---|---|---|---|
| Anthropic | **solo** (최소) | Sonnet 3안 | Sonnet (예비 Opus) | Sonnet (예비 Opus) | Sonnet | Haiku |
| Anthropic + OpenAI | **duo** (권장) | Sonnet 2안 + GPT 2안 | Sonnet (예비 GPT) | GPT (예비 Sonnet) | Sonnet (예비 GPT) | GPT |
| 위 + GLM·Qwen·Kimi 일부 | duo | 키가 있는 모든 모델이 1안씩 | Sonnet | GPT | Sonnet | GPT |
| 다섯 회사 모두 | **all** | 다섯 회사 각 1안 | Sonnet (예비 Kimi) | GPT (예비 GLM) | Kimi | GLM |

- duo를 권하는 이유: **쓰는 모델과 심사하는 모델이 다른 회사**여야 심사가 무뎌지지 않고, 기획안도 두 회사 것을 비교해야 "흔한 안"과 "독특한 안"이 잘 갈립니다.
- solo도 끝까지 쓸 수 있지만, Sonnet이 자기 글을 심사하므로 지적이 약할 수 있습니다. 예비 모델 Opus는 Sonnet의 2배 가격이며, Sonnet 호출이 실패했을 때만 쓰입니다.
- 모드를 직접 고정하려면 `.env`에 `NOVEL_KEY_MODE=solo`(또는 `duo`·`all`)를 적습니다.
- 역할 모델을 직접 바꾸려면 `studio/settings.json`을 만듭니다(git에 올라가지 않음). 예:
  ```json
  {
    "propose_n": 3,
    "roles": {
      "writer":   {"model": "sonnet", "fallback_models": ["gpt"]},
      "reviewer": {"model": "gpt",    "fallback_models": ["sonnet"]},
      "polisher": {"model": "sonnet", "fallback_models": []},
      "premium":  {"model": "opus",   "fallback_models": []}
    }
  }
  ```
  모델 이름은 [models.json](../models.json)의 별칭(`sonnet`·`opus`·`haiku`·`gpt`·`glm`·`qwen`·`kimi`)을 씁니다. 적지 않은 역할은 모드 기본값을 쓰고, 문자열로만 적은 역할(`"writer": "opus"`)은 예비 모델 없이 동작합니다. 키가 없는 회사의 모델을 적으면 `/novel-setup`·`keys`가 문제로 알려 줍니다.

---

## 4. 실행과 연결 확인

```
./nw
```

중계기가 켜지고 Claude Code가 열립니다. 첫 줄에 이렇게 나오면 정상입니다.

```
nw: 키 [anthropic, openai] → 모드 duo（Anthropic + GPT(권장)）· 작가 sonnet · 심사 gpt
```

- `⚠ Anthropic 키(ANTHROPIC_API_KEY)가 없습니다` → `.env`의 키 줄을 확인하고 `./nw --bridge-restart`.
- Claude Code가 열리면 **`/novel-setup`**을 입력합니다. 준비물·키·모드를 점검하고, 원하면 모델마다 아주 짧은 연결 시험(몇 센트 이하)을 합니다.
- 터미널에서 직접 확인하려면: `python3 studio/cli.py keys` (구성 보기), `python3 studio/cli.py keys --ping` (연결 시험).

---

## 5. VS Code에서 쓰는 법 (권장)

- VS Code에서 novel_worker 폴더를 열고, 통합 터미널에서 `./nw`를 실행합니다(또는 Claude Code 확장 창을 씁니다 — 이 경우에도 중계기는 `./nw --bridge`로 먼저 켭니다).
- 긴 글은 대화창에 붙이지 않고 **파일 링크**로 줍니다. 링크를 누르면 편집기 탭에서 열리고, `Cmd+Shift+V`로 미리보기가 됩니다.
  - 기획안 비교 `works/<작품>/notes/PROPOSALS.md`, 기획서 `notes/BRIEF_vN.md`, 설정서 `canon/CANON.md`, 장 원고 `chapters/chNN.md`, 교정 체크리스트 `notes/PROOFREAD.md`

> 참고: Claude Code 확장 창에서 쓸 때는 Claude Code 본체가 중계기를 거치지 않으므로, 서브에이전트(idea-kimi 등 다른 회사 모델)는 쓸 수 없습니다. 소설 엔진·기획 도구는 중계기만 켜져 있으면 정상입니다.

---

## 6. 첫 작품 — 짧은 시험부터

처음에는 **3장, 장당 1500자** 정도로 비용과 흐름을 확인하세요. Claude Code에 차례로 말하면 됩니다.

| 순서 | 입력 | 일어나는 일 | 당신이 할 일 |
|---|---|---|---|
| 1 | `/novel-new` | 테마를 묻고 작품 폴더를 만든다 | 장르·소재·결말 유형을 답한다. "3장, 장당 1500자로 시험"이라고 말한다 |
| 2 | `/novel-propose <작품>` | 기획안을 받아 비슷한 안·독특한 안으로 나눈다 | `PROPOSALS.md`를 읽고 하나를 고른다 |
| 3 | `/novel-brief <작품>` | 집필 지시서를 만들고 기계 검사 | `BRIEF_v1.md`를 읽고 확정 또는 수정 지시 |
| 4 | `/novel-fix <작품> 1` | 역할·예산을 정하고 집필 폴더를 만든다 | 예산을 정한다(시험이면 $2~3) |
| 5 | `/novel-run <작품>` | 설정서(CANON)를 설계·심사하고 멈춘다 | 기다린다 |
| 6 | `/novel-approve <작품>` | CANON 요약과 지문을 보여 준다 | `CANON.md`를 읽고 **승인**을 고른다 |
| 7 | `/novel-run <작품>` | 장마다 집필·심사·기록 → 최종 심사 | 기다린다(멈추면 `/novel-rescue`) |
| 8 | `/novel-finish <작품>` | 문체 다듬기·윤문·교정·표지·EPUB | 원하는 것을 고른다 |

완성 파일은 `works/<작품>/output/`에 생깁니다(`final.md`, `*.epub`). 비용은 언제든 `/novel-status <작품>`.

---

## 7. AI 티 윤문 (선택)

완성 원고의 번역투·상투구를 줄이는 [im-not-ai](https://github.com/epoko77-ai/im-not-ai) 스킬을 붙일 수 있습니다. Claude Code에서 한 번만:

```
/plugin marketplace add epoko77-ai/im-not-ai
/plugin install humanize-korean@im-not-ai
```

이후 `/novel-finish`에서 "AI 티 윤문"을 고르거나 `/novel-humanize <작품>`. 대사는 한 글자도 바꾸지 않고, 기계 검사를 통과한 장만 반영됩니다.

---

## 8. 문제 해결

| 증상 | 원인과 해결 |
|---|---|
| `⚠ Anthropic 키가 없습니다` | `.env`의 `ANTHROPIC_API_KEY=` 줄 확인 → `./nw --bridge-restart` |
| 모드가 solo로 나온다(OpenAI 키를 넣었는데) | 키 이름이 `OPENAI_API_KEY`인지, 저장 후 `./nw --bridge-restart` 했는지 확인 |
| `중계기에 연결할 수 없습니다` | `./nw --bridge`로 켜기. 상태는 `./nw --status` |
| 집필이 `CALL_FAILED`로 멈춤 | `/novel-setup`의 연결 시험으로 어느 모델이 실패하는지 확인. 401이면 키·결제, 429면 잠시 후 `/novel-rescue`에서 다시 시도 |
| `BUDGET_EXCEEDED` | 작품 예산 도달. `/novel-status`로 비용을 보고, 올리려면 Claude Code에 "예산을 $5로 올려 줘"처럼 말합니다(`set_budget` 후 재개). `config.json`을 직접 고치면 보호 장치가 작동해 멈추니 직접 고치지 마세요 |
| 포트 8788이 이미 쓰임 | `./nw --stop` 후 다시. 다른 프로그램이면 `.env`에 `NOVEL_BRIDGE_PORT=8790` 한 줄을 적고 `./nw` 다시 실행(도구들도 이 값을 따릅니다) |
| 표지를 만들지 못함 | Chrome(또는 Edge·Brave)이 없음. 설치 후 다시 |
| 비용이 $0으로 나옴 | [models.json](../models.json)에 그 모델의 가격이 비어 있음(price) |

### 비용 없이 도구만 시험하기
```
python3 bridge/regress.py                       # 중계기(가짜 업체 서버)
python3 -m unittest discover -s engine/tests    # 엔진 처음부터 끝까지(가짜 모델)
```
