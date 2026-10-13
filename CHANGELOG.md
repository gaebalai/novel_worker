# 변경 기록

## v1.0.0 — 2026-10-14

첫 공개 판입니다. [novel_studio](https://github.com/gaebalai/novel_studio)를 바탕으로, Claude Code가 진행을 맡고 여러 회사의 모델을 섞어 한글 소설을 쓰는 도구로 다시 만들었습니다.

### 주요 기능
- **Claude Code 진행**: 웹 화면 대신 Claude Code와 대화하며 `/novel-*` 명령으로 기획 → 설정서 → 집필 → 교정 → EPUB까지 진행
- **중계기(bridge)**: Anthropic 형식 요청을 모델 이름으로 각 회사 API(Anthropic·OpenAI·Zhipu GLM·Alibaba Qwen·Moonshot Kimi)에 나눠 보냄. 키는 중계기만 읽고 Claude Code 본체에는 넘기지 않음
- **키에 따른 자동 모드**: Anthropic 키 하나면 solo, Anthropic + OpenAI면 duo(권장), 다섯 회사 모두면 all. `python3 studio/cli.py keys`로 확인
- **집필 엔진**: 설계 → 심사 → 사람 승인 → 장마다 집필·심사·기록 → 최종 심사 → 상위 모델 교정. 기계 검사·보호 파일 감시(guard)·비용 기록·예산 한도
- **VS Code 사용성**: 기획안 비교·기획서·교정 체크리스트를 Markdown 파일로 만들어 편집기에서 읽고 고름
- **AI 티 윤문(선택)**: [im-not-ai](https://github.com/epoko77-ai/im-not-ai) humanize-korean 스킬 연결. 대사는 한 글자도 바꾸지 않고, 기계 검사를 통과한 장만 반영(`accept-polish`)
- **안전장치**: 설정서 승인·예산 변경·상위 모델 호출은 사람이 정함(권한 ask 규칙), `.env` 읽기 차단, 중계기는 로컬 JSON 요청만 받음(Origin·OPTIONS 거부), 끊긴 요청은 업체 쪽도 취소

### 확인된 모델 (2026-10-14 각 회사 공식 문서)
`claude-sonnet-5-5`, `gpt-6.1-sol`, `glm-5.3`, `qwen3.8-max`, `kimi-k3` — 가격은 [models.json](models.json)

### 알려진 제한
- 이번 판의 자동 시험은 가짜 업체 서버로 했습니다(중계기 회귀 시험, 엔진 시험 28개, 기획→EPUB 전 구간). 실제 API로는 처음 연결할 때 `/novel-setup`으로 확인하세요.
- 표지 그림 생성은 중계기로 할 수 없습니다(글자·색 표지는 가능, Chrome 필요).
- GPT 6.1-Sol은 chat/completions 방식에서 도구 호출을 지원하지 않아, Claude Code의 메인 모델이나 파일을 다루는 서브에이전트로는 쓸 수 없습니다.
- macOS 기준으로 만들고 확인했습니다.
