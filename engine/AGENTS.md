# AGENTS.md — 진행자 안내서（novel_worker 작품 폴더.）

이 폴더는 `engine/tools/scaffold.py`(또는 studio의 “Fix”)가 `brief.json`으로 만든 작품 폴더입니다.
소설을 쓰는 것은 모델이며, 역할은 `config.json`의 `llm.profiles`로 나뉘어 있습니다.

실제 배정은 이 폴더의 `config.json`(`llm.profiles`)에 있습니다 — Fix 할 때의 모드(등록된 API 키: solo / duo / all)로 정해집니다. 아래는 all 모드의 예입니다.

| 역할 | 모델(all 모드 예) | 맡는 공정 |
|---|---|---|
| writer(작가) | Claude Sonnet 5.5 (예비 Kimi) | CANON 설계, 각 장 본문, 다시 쓰기 |
| reviewer(심사역) | GPT 6.1-Sol (예비 GLM) | CANON 심사, 각 장 심사, 장 기록(LEDGER), 최종 심사 |
| polisher(다듬기) | Kimi K3 (예비 Qwen) | 완성 원고의 문체 마무리(선택) |
| premium(상위 모델) | Claude Sonnet 5.5 | **예외 처리만**: CANON 전체 수정, 통과하지 못한 장의 손질, 전체 교정 |

진행자(사람, 또는 Claude Code — 저장소 최상위의 CLAUDE.md)는 `tools/novelctl.py`를 돌리고, 멈추면 사람에게 알리기만 합니다.
모델이 읽는 것은 패킷(`work/*.packet.md`)뿐이며, 이 AGENTS.md는 모델에게 전달되지 않습니다.

---

## 0. 최상위 약속(진행자가 사람이든 에이전트든 같음)

1. **상태는 디스크에만 있다.** 자기 기억을 믿지 않는다. 망설여지면 `python3 tools/novelctl.py status --json`을 보고 `next`를 따른다.
2. **이 폴더 밖으로 나가지 않는다.** 예외는 저장소 최상위의 `.env`·`models.json`을 `tools/call_llm.py`가 읽는 것뿐이다. 모든 모델 호출은 중계기(127.0.0.1:8788)를 거친다(진행자는 `.env`를 열지 않고, 보여 주지 않고, 베끼지 않는다).
3. **모델의 출력을 손으로 고치지 않는다.** `canon/`·`chapters/`·`state/`·`work/`를 고쳐 쓰지 않는다. 받은 편지함(`work/inbox/`)에 답을 두지 않는다. 직접 본문·설정·심사 결과를 쓰지 않는다.
4. **고쳐 쓰면 안 되는 것.** `AGENTS.md`·`BRIEF.md`·`brief.json`·`config.json`·`tools/`·`prompts/`·`templates/`. 지문(`guard/manifest.json`)으로 감시되고 있어서, 바뀌면 novelctl이 멈춥니다.
5. **사람의 승인은 사람이 한다.** `approve-canon`은 사람이 터미널에서 하거나, 사용자가 지문을 확인하고 승인을 고른 경우에만 진행자가 `NOVEL_WORKER_APPROVE=<지문>`을 붙여 실행한다. `resolve --accept`·`resolve --relock`·`resolve --done`·`reset-chapter`·`tools/guard.py seal`도 사람의 판단으로.
6. **STOP 파일을 보면 멈춘다.**
7. **개입은 기록한다.** `notes/INTERVENTIONS.md`에 한 줄씩(일시·무엇을 했는지·왜).
8. **장 본문을 통째로 자기 문맥에 읽어 들이지 않는다.** 읽어도 되는 것은 `status`, `work/*_feedback.md`, `work/*_verdict_*.json`, `output/final_report.md`, `logs/run.log`의 끝부분.

---

## 1. 돌리는 법

```
python3 tools/call_llm.py --ping writer      # 연결 확인(작가)
python3 tools/call_llm.py --ping reviewer    # 연결 확인(심사역)
python3 tools/call_llm.py --ping premium     # 연결 확인(상위 모델. 예외 처리를 쓸 때만)
python3 tools/novelctl.py status --json
python3 tools/novelctl.py start              # 설계 → CANON 심사 → CANON 잠금에서 멈춤(canon_hold)
#   사람이 canon/CANON.md를 읽는다(제4절 행동 원리, 제6절 세계의 전제, 제13절 추가 요소 신고)
python3 tools/novelctl.py approve-canon      # 사람이 터미널에서
python3 tools/novelctl.py start              # 각 장 → 기록 → 결합 → 최종 심사 → DONE
python3 tools/guard.py check
```

`start`는 분리되어 돌기 때문에 도구의 제한 시간에 끊기지 않습니다. 진행 상황은 2~5분마다 `status --json`으로 봅니다.

## 2. 멈췄을 때(`escalation.code`)

| 코드 | 의미 | 진행자의 대응 | 사람의 선택지 |
|---|---|---|---|
| `DESIGN_EXHAUSTED` | CANON 설계가 정해진 횟수 안에 통과하지 못함 | 멈추고 보고 | `rescue canon`(상위 모델로 한 번만 전체 수정) / `redesign --feedback "..."` / 직접 고치고 `approve-canon` |
| `CHAPTER_EXHAUSTED` | 장이 3고까지 합격하지 못함 | 멈추고 보고(`work/chNN_feedback.md`를 옮겨 적기) | `rescue chapter N [--model M] [--note 지시]`(상위 모델로 고쳐지지 않으면 다른 모델로) / `resolve --retry` / `resolve --accept` |
| `LEDGER_FAILED` | 장 기록이 서식을 충족하지 못함 | 멈추고 보고 | `work/chNN_ledger_a*.out`을 보고 대장에 손으로 덧붙인 뒤 `resolve --ledger-done` |
| `CANON_MODIFIED` | 잠금 뒤에 CANON이 바뀜 | 멈추고 보고 | 누가 바꿨는지 확인하고, 올바르면 `resolve --relock` |
| `GUARD_STOP` | STOP이 있거나, 보호 파일이 바뀜 | 멈추고 보고 | 원인을 확인하고 원래대로 되돌림 |
| `FINAL_REVIEW_FAILED` | 최종 심사에서 중대한 지적 | `output/final_report.md`의 지적을 옮겨 적어 보고 | `rescue chapter N [--model M] [--note 지시]`(최종 심사의 지적을 수정 지시로) / `reset-chapter N` / `resolve --done` |
| `BUDGET_EXCEEDED` | 비용이 `budget_usd`에 도달 | 멈추고 보고 | 예산을 재검토 |
| `CALL_FAILED` / `CALL_TIMEOUT` | API 이상·키·모델 이름 | `--ping`으로 확인하고 결과를 보고 | 키·모델 이름을 고치고 `resolve` |

`rescue`와 `proofread`는 상위 모델(premium)을 한 번만 호출합니다. **호출할지 말지는 사람이 정합니다**(비용이 들기 때문). 사람이 맡겼을 때만 실행해 주세요.

## 3. 마무리(선택)

```
python3 tools/novelctl.py polish             # 문체 마무리(polisher). polished/에 저장
python3 tools/novelctl.py proofread          # 상위 모델이 교정안을 output/proofread_edits.json에 낸다(원고는 바꾸지 않음)
#   사람이 enabled를 확인한다(studio에서는 목록의 체크로)
python3 tools/proofread_apply.py             # 교정안을 적용해 output/final_proofread.md
python3 tools/novelctl.py cover --model <이미지 생성 모델|none> --style simple|illust|photo --motif "..." \
    --title-dir vertical|horizontal --font mincho|gothic|casual --author "..."   # 표지(images/cover.png). 비용이 들므로 사람의 지시로
python3 tools/novelctl.py epub --proofread   # 가로쓰기 EPUB(표지와 저자명은 images/cover.json에서 들어감)
```

## 4. 사람에게 보고(진행자가 notes/REPORT.md에 쓴다)

- CANON이 잠겼을 때: 작품의 요지, 인물의 행동 원리 목록, 세계의 전제, 추가 요소 목록.
- 멈췄을 때: 코드와, 무엇을 기다리고 있는지, 사람의 선택지(위의 표).
- 완성했을 때: final의 총평, 이월된 추가 요소, 개입 횟수, 비용(`status --json`의 `usage`: 역할별).
