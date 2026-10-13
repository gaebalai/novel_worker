# worker — 소설을 처음부터 쓰는 앱（novel_worker v1.0）

테마를 넣으면 여러 모델이 기획안을 내고, 비슷한 안과 동떨어진 안을 가려낸 뒤, 고른 안으로 집필 지시서（BRIEF）를 만들고,
확정（Fix）하면 CANON·각 장 집필·심사·교정·EPUB까지 같은 화면에서 진행합니다. Python 3.9 이상의 표준 라이브러리만으로 동작합니다.

```
python3 studio/server.py            # → http://127.0.0.1:8780
```

API 키는 저장소 최상위의 `.env`（`OPENROUTER_API_KEY=...` 또는 `NOVEL_PROVIDER_APIKEY=...`）에 둡니다. 화면에는 표시하지 않습니다.
OpenCode Go를 쓴다면 `.env`에 `NOVEL_LLM_BASE_URL=https://opencode.ai/zen/go/v1`도 적습니다（창구 구분과 헤더는 `engine/tools/llm_wire.py`）.

## 공정

| 공정 | 하는 일 | 담당（기본 모델） | 다시 하기 |
|---|---|---|---|
| 1 테마 | 장르·소재·장의 수·예상 독자·결말 유형 등을 넣는다 | 사람 | **테마를 고쳐 새 작품 폴더로 다시 요청**（이름은 반드시 바꾼다. 원래 작품은 그대로. 확정된 작품에서도 가능）. 같은 폴더에서 처음부터 다시 할 수도 있다（이전 기록은 `studio/history/`） |
| 2 제안 | 고른 모델에 n안씩 병렬로 내게 한다. 안마다 비용을 표시 | haiku-5.5 / luna-pro / deepseek-v4.1-flash 등 | 다시 제출, 지시를 붙여 다시 제출, **수렴한 골격을 피해 다시 제출**, 안을 직접 고치기, 지시해서 안을 개고 |
| 3 분류 | 각 안의 골격을 모델이 뽑아내고, **비슷한지는 규칙으로 센다** → ‘수렴’ ‘변주’ ‘이상치’ | luna-pro（골격 추출만） | 임계값을 움직여 즉시 다시 분류（모델을 부르지 않음）. AI가 읽은 분류도 나란히 볼 수 있다 |
| 4 BRIEF | 고른 안으로 BRIEF를 JSON으로 만들고, 기계 검사（lint）한 뒤 BRIEF.md로 출력한다 | opus-5.5 | 지시를 더해 다시 만들기, JSON 직접 고치기. 모든 버전이 남는다 |
| 5 확정 | 제안과 BRIEF를 확정하고, 역할별 모델과 예산을 정해 작품 폴더를 만든다 | 사람 | 여기서부터는 BRIEF를 바꾸지 않는다 |
| 6 CANON | 작가가 설계 → 심사역이 심사 → 사람이 승인 | haiku-5.5 → luna-pro → 사람 | 지시를 더해 다시 설계, **상위 모델로 전체를 한 번만 손질** |
| 7 집필 | 각 장 집필 → 심사 → 기록（LEDGER）→ 최종 심사 | haiku-5.5 / luna-pro | 멈추면：**상위 모델로 지적된 곳만 손질**／작가에게 3고 더／원고 그대로 채택 |
| 8 마무리 | 문체 다듬기（선택）, 상위 모델의 교정안 → 사람이 채택 여부 결정 → 적용 → 표지 → EPUB | opus-5.5（교정만）, 표지는 고른 이미지 생성 모델 | 교정안은 한 건씩 채택 여부를 고를 수 있다. 표지는 ‘그림은 그대로, 글자만 다시 조판’할 수 있다（비용 없음） |

화면 위쪽의 ‘지금까지 비용’에 기획（studio）과 집필（engine）의 비용이 공정·역할별로 표시됩니다.

## 설계 철학（Novel_4〜Grauwall_06 검증에서）

- **합의는 이상치를 낳지 않는다.** 그래서 분류는 ‘좋고 나쁨’이 아니라 ‘유형에 얼마나 쏠렸는지’를 보여 주고, 이상치를 줍는 것은 사람, 이상치를 늘리는 것은 ‘수렴한 골격을 피해’ 다시 제출하기（롤링）다.
- **중복 판정은 LLM에 맡기지 않는다.** 골격 추출만 모델에 맡기고, 가까움은 문자 2-gram과 골격 항목별 일치로 센다（`similarity.py`）.
- **BRIEF는 JSON으로 만들게 한다.** 지금까지 Claude가 손으로 맞추던 BRIEF.md·config.json의 복선 배치·CANON 템플릿을 `engine/tools/scaffold.py`가 기계적으로 만들므로 손으로 맞출 필요가 없다.
- **상위 모델은 예외 처리에만.** 쓰기·대조는 싼 모델로 몇 번이고, 전체 정합성 손질·교정은 상위 모델로 한 번, 멈출지 말지는 사람이 정한다.

## 구성

```
studio/
├── server.py        웹 서버（ThreadingHTTPServer）. 긴 공정은 작업（job）으로 뒤에서 돈다
├── core.py          공정 본체. 화면과 CLI 모두 여기만 호출한다
├── cli.py           같은 공정의 CLI（JSON 입출력）. 하네스（harness/）가 사용
├── llm.py           모델 호출（OpenRouter·OpenCode）과 비용 기록, /models 단가
├── similarity.py    수렴·변주·이상치 분류（규칙）
├── prompts/         제안·개고·골격·분류·BRIEF 요청문
├── web/             화면（HTML·CSS·JS. 빌드 불필요）
├── settings.json    （선택）기본 모델 등을 덮어쓰기. core.py의 DEFAULT_SETTINGS 참조
└── devtools/mock_openrouter.py   비용 없이 처음부터 끝까지 시험하는 가짜 모델
works/<slug>/        작품 폴더. studio.json（기획 상태）과, 확정 후에는 engine의 작업 공간
```

## 비용 없이 시험하기

```
python3 studio/devtools/mock_openrouter.py &
NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 NOVEL_PROVIDER_APIKEY=mock python3 studio/server.py
#   OpenCode Go 흉내：NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/zen/go/v1
```

`MOCK_FAIL_CANON_REVIEW=5`（CANON 심사를 5번 떨어뜨림）나 `MOCK_FAIL_CHAPTER=2`（제2장 심사를 계속 떨어뜨림）로 예외 처리 경로도 시험할 수 있습니다.

## 표지

마무리 화면에서 만듭니다. 제목은 CANON의 것, 저자명은 입력한 것（EPUB 저자에도 들어갑니다）.

| 항목 | 선택지 |
|---|---|
| 이미지 생성 모델 | OpenRouter 이미지 생성 모델 목록（/images/models）에서 선택. ‘사용 안 함’이면 글자와 색만의 표지（비용 없음） |
| 스타일 | 심플／일러스트／사실적 |
| 모티프 | 자유 입력（그려 줬으면 하는 것） |
| 제목 | 세로쓰기／가로쓰기 |
| 서체 | 명조／고딕／캐주얼（Google Fonts의 Nanum Myeongjo·Noto Sans KR·Gaegu. 오프라인이면 기기의 서체） |
| 저자명 | 자유 입력 |

이미지 생성 모델에는 글자 없는 그림만 그리게 하고, 제목과 저자명은 HTML로 조판해 Chrome（헤드리스）으로 1600×2560 PNG로 만듭니다. Chrome을 찾지 못하면 그림만 표지로 쓰고 제목은 속표지에 넣습니다（`NOVEL_CHROME`으로 Chrome 위치를 지정할 수 있습니다）. 비용은 ‘지금까지 비용’의 집필 쪽에 `cover`로 표시됩니다.

v1.1 이전에 확정한 작품은 작품 폴더의 도구（tools/）가 오래되었으므로, 표지 칸에 나오는 ‘도구를 최신으로’를 누른 다음 만듭니다（`python3 engine/tools/scaffold.py upgrade works/<slug>`와 같음. 프롬프트·BRIEF·CANON·본문은 바뀌지 않고, 기록은 notes/INTERVENTIONS.md에 남습니다）.

## 주의

- 자기 Mac（127.0.0.1）에서만 쓰는 것을 전제로 합니다. 외부에 공개하지 마세요（인증이 없습니다）.
- CANON 승인은 화면의 버튼으로 합니다（지문을 확인하고 누름）. CLI와 하네스에서는 승인할 수 없습니다.
- 작품별 공정 기록은 `works/<slug>/studio.json`, 비용은 `works/<slug>/studio/usage.jsonl`（기획）과 `logs/usage.jsonl`（집필）.
