#!/usr/bin/env python3
"""novelctl — 소설 생성 파이프라인의 상태 관리·패킷 조립·검증 도구（novel_worker v1.0 엔진）

진행자（사람, 또는 Claude Code — 저장소 최상위의 CLAUDE.md）는 이 도구만 써서 진행한다.
모델 호출은 tools/call_llm.py 가 novel_worker 중계기（127.0.0.1:8788 → 각 회사 API）로 하므로 하네스에 의존하지 않는다. 이야기의 상태는 모두 디스크에 있고,
대화 기록에 의존하지 않는다. 어디서 중단해도 `status` → `step` 으로 다시 시작할 수 있다.

  python3 tools/novelctl.py init            초기화（맨 처음 1회）
  python3 tools/novelctl.py status [--json] 현재 위치와 다음 할 일을 표시（--json 은 기계용. studio 와 하네스가 씀）
  python3 tools/novelctl.py step            다음 1단계만 실행（모델 호출은 최대 2회）
  python3 tools/novelctl.py start           run 을 셸에서 떼어 내 기동하고 바로 돌아온다（권장）
  python3 tools/novelctl.py run [--max N]   완료나 에스컬레이션까지 step 을 반복한다（앞에서 계속 실행）
  python3 tools/novelctl.py packet KIND [N] 패킷을 조립해 저장만 한다（수동 확인용）
       KIND = design | canon-review | write | review | ledger | final
  python3 tools/novelctl.py check N         제N장의 초고（work/）또는 확정 원고를 기계 검사
  python3 tools/novelctl.py approve-canon   pause_after_canon=true 일 때 CANON 확인 후 재개（사람이 터미널에서, 또는 사용자가 지문을 확인하고
                                            승인을 고른 뒤 진행자가 NOVEL_WORKER_APPROVE=<지문> 을 붙여 실행）
  python3 tools/novelctl.py redesign (--feedback TEXT | --feedback-file F)
                                            CANON 에 사람의 지시를 더해 설계를 다시 한다（설계 중·승인 대기일 때만）
  python3 tools/novelctl.py rescue canon [--model M] [--note T]
                                            상위 모델（premium）이 CANON 전체를 한 번에 고친다（심사가 수렴하지 않을 때）
  python3 tools/novelctl.py rescue chapter N [--model M] [--note T]
                                            상위 모델이 제N장의 지적 부분을 전체 정합성을 맞추며 고친다（3고로도 통과 못 할 때, 최종 심사에서 떨어졌을 때）.
                                            --model：그 한 번만 다른 모델로（지정한 상위 모델로 안 고쳐졌을 때）. --note：사람의 지시를 더한다
  python3 tools/novelctl.py proofread [N...]  상위 모델이 전체 장（또는 지정한 장）을 교정해 output/proofread_edits.json 에 교정안을 낸다
  python3 tools/novelctl.py resolve [--retry]  에스컬레이션을 해제（--retry 로 시도 횟수를 초기화）
  python3 tools/novelctl.py reset-chapter N 제N장 이후를 미집필로 되돌린다（LEDGER 도 되감는다）
  python3 tools/novelctl.py assemble        확정된 장을 output/final.md 로 합친다
  python3 tools/novelctl.py epub [--proofread]  EPUB 와 평가 페이지용 작품 정보를 output/ 에 만든다
  python3 tools/novelctl.py cover [--model M] [--style simple|illust|photo] [--motif T] [--title-dir vertical|horizontal]
                                  [--font mincho|gothic|casual] [--author T] [--reuse-art]
                                            표지를 만든다（이미지 생성 모델이 글자 없는 그림을 그리고, 제목과 저자명은 이쪽에서 짠다）
  python3 tools/novelctl.py polish [N] [--force]  완성 원고의 문체를 polisher（config 의 역할 모델）로 다듬어 polished/ 에 저장
  python3 tools/novelctl.py start polish    polish 를 셸에서 떼어 내 기동（권장）
  python3 tools/novelctl.py assemble --polished  polished/ 의 장을 output/polished.md 로 합친다
  python3 tools/novelctl.py set-budget USD   작품 예산을 바꾼다（보호 파일이 기록과 같을 때만. 사람이 정한 값으로）
  python3 tools/novelctl.py accept-polish N --from FILE [--label humanize]
                                            바깥에서 다듬은 원고(예: humanize-korean 윤문)를 기계 검사해 polished/chNN.md 로 받아들인다.
                                            대사(“ ”)가 한 글자라도 바뀌면 거부. chapters/ 는 바꾸지 않는다（novel_worker）

작가가 “사람（또는 Claude）”일 때: config.json 의 writer_cmd / reviewer_cmd 를 tools/inbox_llm.py 로 한다.
  패킷은 work/*.packet.md, 답은 work/inbox/<패킷 이름에서 .packet.md 를 뺀 이름>.md 에 둔다.
  답이 없는 상태에서 step 하면 AWAITING_AUTHOR 로 멈춘다 → 답을 두고 `resolve` → `step`.

종료 코드: 0=전진 / 2=에스컬레이션（사람의 판단이 필요） / 3=완료 / 1=오류

v1.0 의 생각: 쓰기·대조하기는 싼 모델로 몇 번이고, 전체 정합성을 고치기·상식으로 확인하기는 상위 모델（premium）로 한 번만,
멈출지는 사람이 정한다（Grauwall_06 의 실적: 상위 모델이 나선 것은 설계 수정 2회·장 손질 5회·교정 1회, 나머지 약 90회는 싼 모델）.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import guard  # noqa: E402  제3회 실험에서 추가: STOP·보호 파일·넘어서는 안 되는 선·베껴 쓰기의 기계 검사
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
N_CH = CFG["chapters"]
PROGRESS = ROOT / "state" / "progress.json"
LEDGER = ROOT / "state" / "LEDGER.md"
CANON = ROOT / "canon" / "CANON.md"
EV_JA = {"planted": "설치한다", "reinforced": "보강한다（독자에게 다시 보여 준다）", "recovered": "회수한다"}
KANJI = "영일이삼사오육칠팔구십"


class Escalate(Exception):
    def __init__(self, code, detail, hint=""):
        super().__init__(detail)
        self.code, self.detail, self.hint = code, detail, hint


# ---------------------------------------------------------------- 기본
def P(*a):
    return ROOT.joinpath(*a)


def rd(path):
    return Path(path).read_text(encoding="utf-8")


def wr(path, s):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(s, encoding="utf-8")


def log(msg):
    P("logs").mkdir(exist_ok=True)
    with open(P("logs", "run.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat(timespec='seconds')} {msg}\n")


def nn(n):
    return f"{int(n):02d}"


def count_chars(s):
    return len(re.sub(r"\s", "", s))


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load():
    if not PROGRESS.exists():
        sys.exit("초기화되지 않았습니다. 먼저 `python3 tools/novelctl.py init` 을 실행하세요.")
    return json.loads(PROGRESS.read_text(encoding="utf-8"))


def save(p):
    wr(PROGRESS, json.dumps(p, ensure_ascii=False, indent=2))


def between(text, b, e):
    i = text.find(b)
    if i < 0:
        return None
    j = text.find(e, i + len(b))
    if j < 0:
        return None
    return text[i + len(b):j].strip()


# ---------------------------------------------------------------- 재료 잘라 내기
def rules():
    return between(rd(P("BRIEF.md")), "<!-- RULES:BEGIN -->", "<!-- RULES:END -->")


def role(n):
    return between(rd(P("BRIEF.md")), f"<!-- ROLE:{nn(n)} -->", f"<!-- /ROLE:{nn(n)} -->")


def canon():
    return rd(CANON)


def card(n):
    return between(canon(), f"<!-- CARD:{nn(n)} -->", f"<!-- /CARD:{nn(n)} -->")


def canon_core():
    return re.sub(r"<!-- CARD:(\d\d) -->.*?<!-- /CARD:\1 -->", "", canon(), flags=re.S).strip()


def events_for(n):
    return [(sid, ev) for sid, evs in CFG["slot_plan"].items() for c, ev in evs if c == n]


def recover_ch(sid):
    rc = [c for c, ev in CFG["slot_plan"][sid] if ev == "recovered"]
    return rc[0] if rc else None


def events_text(n):
    ev = events_for(n)
    if not ev:
        return "（이 장에서 필수인 복선 조작은 없다）"
    return "\n".join(f"- {sid}（{CFG['slot_names'][sid]}）：{EV_JA[e]}. 내용과 보여 주는 방식은 CANON 제7절 “{sid}” 행을 따른다."
                     for sid, e in ev)


def hide_text(n):
    items = [(sid, recover_ch(sid)) for sid in CFG["slot_plan"] if recover_ch(sid) and recover_ch(sid) > n]
    if not items:
        return "（없음: 모두 회수됨, 또는 이 장에서 회수）"
    # 이 장에서 보강하는 실마리는 장 카드에 적힌 범위만큼은 내보내도 된다（2026-10-11: 제14장에서 있는 곳을 말하라는 지정과 어긋났다）
    def line(sid, c):
        if any(ch == n and ev == "reinforced" for ch, ev in CFG["slot_plan"][sid]):
            return f"- {sid}（{CFG['slot_names'][sid]}）：이 장에서는 장 카드에 적힌 범위만 내보내도 된다. 회수（제{c}장）장면은 쓰지 않는다"
        return f"- {sid}（{CFG['slot_names'][sid]}）의 내용: 제{c}장까지 밝히지 않는다"
    return "\n".join(line(sid, c) for sid, c in items)


def ledger_sections(text):
    parts = re.split(r"^## CH(\d\d)\s*$", text, flags=re.M)
    secs = {}
    for k in range(1, len(parts), 2):
        secs[int(parts[k])] = parts[k + 1].strip()
    return parts[0], secs


def ledger_for_packet(n):
    _, secs = ledger_sections(rd(LEDGER))
    full = CFG["ledger_recent_full"]
    out = []
    for c in sorted(secs):
        if c >= n:
            continue
        body = secs[c]
        if c < n - full:  # 오래된 장은 요약·사실·복선 상태만으로 압축
            body = "\n".join(l for l in body.splitlines() if l.startswith(("SUMMARY:", "FACT:", "SLOT:")))
        out.append(f"## CH{nn(c)}\n{body}")
    return "\n\n".join(out) if out else "（아직 기록 없음: 이것이 첫 장）"


def chapter_body(text):
    lines = text.strip().splitlines()
    return "\n".join(lines[1:]).strip() if lines else ""


def prev_tail(n):
    if n <= 1:
        return "（없음: 이것이 제1장. 이야기는 여기서 시작된다）"
    body = chapter_body(rd(P("chapters", f"ch{nn(n - 1)}.md")))
    k = CFG["prev_tail_chars"]
    return ("……" + body[-k:]) if len(body) > k else body


def fill(tpl, n=None):
    c = CFG["chapter_chars"]
    rep = {"TARGET": c["target"], "MINC": c["min"], "MAXC": c["max"], "CHAPTERS": N_CH,
           "CANON_MAX": CFG["canon_max_chars"]}
    if n is not None:
        rep.update({"N": n, "NN": nn(n), "EVENTS": events_text(n), "HIDE": hide_text(n)})
    for k, v in rep.items():
        tpl = tpl.replace("{{" + k + "}}", str(v))
    return tpl


def given_secs():
    """config.json 의 given_chapters（Claude 가 먼저 쓴 장）를 설계와 CANON 심사 패킷에 끼워 넣는다（제6회. 2026-10-11）."""
    s = ""
    for i in CFG.get("given_chapters", []):
        f = P("chapters", f"ch{nn(i)}.md")
        if f.exists():
            s += sec(f"이미 쓰인 장（제{i}장. 변경 불가）", rd(f))
    return s


def series_secs():
    """config.json 의 series_files（설정 자료 발췌·대장의 공개판）를 설계와 설계 심사 패킷에 끼워 넣는다."""
    s = ""
    for f in CFG.get("series_files", []):
        s += sec(f"설정 자료：{Path(f).name}", rd(P(f)))
    return s


def sec(title, body):
    # 구분에 기호의 연속（==== 등）을 쓰면 모델이 그것을 흉내 내 반복 폭주하는 일이 있어서 태그로 감싼다
    return f"\n\n<자료 이름=\"{title}\">\n{body.strip()}\n</자료>\n"


# ---------------------------------------------------------------- 패킷
def build_packet(kind, n=None):
    tag = f"<!-- TASK: {kind}" + (f" CH={nn(n)}" if n else "") + " -->"
    s = tag + "\n이것은 한 번뿐인 요청입니다. 도구나 파일 조작은 쓰지 말고, 지정된 형식의 글만 답해 주세요."
    if kind == "design":
        fb = P("work", "canon_feedback.md")
        if CANON.exists() and fb.exists():
            s += sec("BRIEF", rd(P("BRIEF.md")))
            s += series_secs()
            s += given_secs()
            s += sec("이전 CANON", canon())
            s += sec("수정 지시", rd(fb))
            s += sec("요청", fill(rd(P("prompts", "design_fix.md"))))
        else:
            s += sec("BRIEF", rd(P("BRIEF.md")))
            s += series_secs()
            s += given_secs()
            s += sec("CANON 템플릿", rd(P("templates", "CANON_TEMPLATE.md")))
            s += sec("요청", fill(rd(P("prompts", "design.md"))))
    elif kind == "canon-review":
        s += sec("BRIEF", rd(P("BRIEF.md")))
        s += series_secs()
        s += given_secs()
        s += sec("CANON", canon())
        s += sec("요청", fill(rd(P("prompts", "canon_review.md"))))
    elif kind == "write":
        draft, fb = P("work", f"ch{nn(n)}_draft.md"), P("work", f"ch{nn(n)}_feedback.md")
        s += sec("공통 집필 규칙", rules())
        s += sec("CANON（설정서·장 카드 제외）", canon_core())
        s += sec(f"이 장의 기능（BRIEF 제{n}장）", role(n))
        s += sec(f"이 장의 카드（CANON 제{n}장）", card(n))
        if n < N_CH:
            s += sec(f"참고：다음 장의 카드（제{n + 1}장. 이 장에서는 쓰지 않는다. 장 끝의 훅을 여기로 잇는다）", card(n + 1))
        s += sec("LEDGER（지금까지의 기록）", ledger_for_packet(n))
        s += sec("앞 장의 마무리", prev_tail(n))
        if draft.exists() and fb.exists():
            s += sec("이전 원고", rd(draft))
            s += sec("수정 지시", rd(fb))
            s += sec("요청", fill(rd(P("prompts", "rewrite.md")), n))
        else:
            s += sec("요청", fill(rd(P("prompts", "write.md")), n))
    elif kind == "review":
        s += sec("공통 집필 규칙", rules())
        s += sec("CANON（설정서·장 카드 제외）", canon_core())
        s += sec(f"이 장의 기능（BRIEF 제{n}장）", role(n))
        s += sec(f"이 장의 카드（CANON 제{n}장）", card(n))
        s += sec("LEDGER（지금까지의 기록）", ledger_for_packet(n))
        s += sec("앞 장의 마무리", prev_tail(n))
        s += sec("이 장의 복선（필수）", events_text(n))
        s += sec("아직 밝히면 안 되는 것", hide_text(n))
        s += sec(f"심사 대상 원고（제{n}장）", rd(P("work", f"ch{nn(n)}_draft.md")))
        s += sec("요청", fill(rd(P("prompts", "review.md")), n))
    elif kind == "ledger":
        s += sec("LEDGER 서식", rd(P("templates", "LEDGER_TEMPLATE.md")))
        s += sec(f"제{n}장 본문", rd(P("chapters", f"ch{nn(n)}.md")))
        s += sec("요청", fill(rd(P("prompts", "ledger.md")), n))
    elif kind == "final":
        s += sec("CANON", canon())
        s += sec("LEDGER（전체 장）", rd(LEDGER))
        s += sec(f"제{N_CH}장 본문", rd(P("chapters", f"ch{nn(N_CH)}.md")))
        s += sec("요청", fill(rd(P("prompts", "final_review.md"))))
    elif kind == "polish":
        src = P("chapters", f"ch{nn(n)}.md")
        s += sec("CANON（설정서·장 카드 제외）", canon_core())
        s += sec(f"이 장의 카드（CANON 제{n}장）", card(n))
        s += sec(f"다듬을 원고（제{n}장）", rd(src))
        fb = P("work", f"polish_ch{nn(n)}_feedback.md")
        if fb.exists():
            s += sec("이전 다듬기에 대한 수정 지시", rd(fb))
        s += sec("요청", fill(rd(P("prompts", "polish.md")), n).replace("{{SRC_CHARS}}", str(count_chars(chapter_body(rd(src))))))
    elif kind == "rescue-canon":
        s += sec("BRIEF", rd(P("BRIEF.md")))
        s += series_secs()
        s += given_secs()
        s += sec("현재 CANON", canon())
        s += sec("심사 지적 이력（오래된 순）", canon_review_history())
        fb = P("work", "canon_feedback.md")
        if fb.exists():
            s += sec("현재 남아 있는 지적", rd(fb))
        if RESCUE_NOTE.get("text"):
            s += sec("프로듀서의 지시（최우선）", RESCUE_NOTE["text"])
        s += sec("요청", fill(rd(P("prompts", "rescue_canon.md"))))
    elif kind == "rescue-chapter":
        s += sec("공통 집필 규칙", rules())
        s += sec("CANON（설정서·장 카드 제외）", canon_core())
        s += sec(f"이 장의 기능（BRIEF 제{n}장）", role(n))
        s += sec(f"이 장의 카드（CANON 제{n}장）", card(n))
        if n < N_CH:
            s += sec(f"참고：다음 장의 카드（제{n + 1}장）", card(n + 1))
        s += sec("LEDGER（지금까지의 기록）", ledger_for_packet(n))
        s += sec("앞 장의 마무리", prev_tail(n))
        s += sec("이 장의 복선（필수）", events_text(n))
        s += sec("아직 밝히면 안 되는 것", hide_text(n))
        s += sec("현재 원고", rd(P("work", f"ch{nn(n)}_draft.md")))
        s += sec("심사 지적 이력（원고별）", chapter_problem_history(n))
        if RESCUE_NOTE.get("text"):
            s += sec("프로듀서의 지시（최우선）", RESCUE_NOTE["text"])
        s += sec("요청", fill(rd(P("prompts", "rescue_chapter.md")), n))
    elif kind == "proofread":
        s += sec("공통 집필 규칙", rules())
        s += sec("CANON（설정서·장 카드 제외）", canon_core())
        if CFG.get("audience"):
            s += sec("예상 독자", CFG["audience"])
        s += sec(f"교정할 원고（제{n}장）", rd(proof_source(n)))
        s += sec("요청", fill(rd(P("prompts", "proofread.md")), n))
    elif kind == "continue":
        raise ValueError("continue 는 내부 전용")
    else:
        raise ValueError(f"알 수 없는 패킷 종류: {kind}")
    name = f"{kind}" + (f"_ch{nn(n)}" if n else "") + ".packet.md"
    path = P("work", name)
    wr(path, s)
    if len(s) > CFG["packet_warn_chars"]:
        log(f"WARN packet {name} is {len(s)} chars (> {CFG['packet_warn_chars']})")
        print(f"경고: 패킷이 {len(s)}자입니다. 모델의 컨텍스트 길이를 확인하세요.")
    return path


# ---------------------------------------------------------------- 모델 호출
def spent_usd():
    """logs/usage.jsonl 의 비용 합계（달러）."""
    f = P("logs", "usage.jsonl")
    total = 0.0
    if f.exists():
        for line in rd(f).splitlines():
            try:
                total += float(json.loads(line).get("cost") or 0)
            except (ValueError, TypeError):
                pass
    return total


def usage_summary():
    f = P("logs", "usage.jsonl")
    out = {"total_usd": 0.0, "calls": 0, "by_role": {}}
    if f.exists():
        for line in rd(f).splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            c = float(r.get("cost") or 0)
            out["total_usd"] += c
            out["calls"] += 1
            b = out["by_role"].setdefault(r.get("role", "?"), {"usd": 0.0, "calls": 0, "in": 0, "out": 0})
            b["usd"] += c
            b["calls"] += 1
            b["in"] += int(r.get("in") or 0)
            b["out"] += int(r.get("out") or 0)
    out["total_usd"] = round(out["total_usd"], 4)
    for b in out["by_role"].values():
        b["usd"] = round(b["usd"], 4)
    return out


def call(packet, out, role_="writer", model=None):
    budget = CFG.get("budget_usd")
    if budget and spent_usd() >= float(budget):
        raise Escalate("BUDGET_EXCEEDED", f"비용이 예산 ${budget} 에 도달했습니다（사용 ${spent_usd():.3f}）",
                       "사람이 config.json 의 budget_usd 를 다시 보거나, 여기서 멈춘다")
    tpl = CFG.get(f"{role_}_cmd") or ["python3", "tools/call_llm.py", "{packet}", role_]
    use_arg = any("{packet}" in a for a in tpl)
    cmd = [a.replace("{packet}", str(packet)) for a in tpl]
    if cmd and cmd[0] in ("python3", "python"):
        cmd[0] = sys.executable
    env = os.environ.copy()
    env.update(CFG.get("call_env", {}))
    env.pop("NOVEL_MODEL_OVERRIDE", None)
    if model:
        env["NOVEL_MODEL_OVERRIDE"] = model  # 이 한 번만 역할의 모델을 바꾼다（rescue 에서 사람이 고른 모델. v1.1）
    inflight = P("state", "inflight.json")
    env["NOVEL_PROGRESS_FILE"] = str(inflight)
    wr(inflight, json.dumps({"task": Path(packet).name, "started": time.time(), "last_recv": time.time(),
                             "reasoning_chars": 0, "content_chars": 0, "attempt": 1}))
    log(f"CALL {role_}{'(' + model + ')' if model else ''} {Path(packet).name} -> {Path(out).name}")
    with open(packet, encoding="utf-8") as f:
        try:
            r = subprocess.run(cmd, stdin=None if use_arg else f, capture_output=True, text=True,
                               env=env, timeout=CFG["timeout_sec"], cwd=ROOT)
        except subprocess.TimeoutExpired:
            inflight.unlink(missing_ok=True)
            raise Escalate("CALL_TIMEOUT", f"모델 호출이 {CFG['timeout_sec']}초에 타임아웃",
                           "config.json 의 timeout_sec 을 늘리거나, 모델 부하를 확인하고 `resolve` 후 다시 실행")
        except FileNotFoundError as e:
            raise Escalate("CALL_NOT_FOUND", f"명령을 찾을 수 없음: {e}", "config.json 의 writer_cmd / reviewer_cmd 를 확인")
    inflight.unlink(missing_ok=True)
    wr(out, r.stdout)
    if r.stderr.strip():
        wr(str(out) + ".stderr", r.stderr)
        for line in r.stderr.strip().splitlines()[-3:]:
            log(f"  {line[:300]}")
    if r.returncode == 4:
        raise Escalate("AWAITING_AUTHOR", f"작가의 답을 기다리고 있습니다: {r.stderr.strip()[-300:]}",
                       "패킷을 읽고 답을 work/inbox/ 에 둔 다음 `resolve` → `step`")
    if r.returncode != 0 or not r.stdout.strip():
        raise Escalate("CALL_FAILED", f"모델 호출 실패 (rc={r.returncode}). stderr: {r.stderr[-500:]}",
                       "OPENROUTER_API_KEY·config.json 의 llm.profiles 모델 이름·네트워크를 확인하고 `resolve` 후 다시 실행")
    return r.stdout


def continue_call(packet, partial, out):
    s = rd(packet) + sec("당신의 출력（도중에 끊김）", partial) + sec(
        "요청", "위 출력은 도중에 끊겼습니다. 마지막 글자 바로 뒤부터 이어지는 부분만 출력해 주세요. "
                "이미 쓴 부분을 되풀이하지 말 것. 제목이나 머리말은 필요 없습니다.")
    cp = Path(str(packet).replace(".packet.md", ".continue.packet.md"))
    wr(cp, s)
    return call(cp, out, "writer")


# ---------------------------------------------------------------- 추출·검사
def extract_json(raw):
    raw = re.sub(r"```(?:json)?", "", raw)
    i = raw.find("{")
    while i >= 0:
        depth, instr, esc = 0, False, False
        for j in range(i, len(raw)):
            ch = raw[j]
            if instr:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    instr = False
                continue
            if ch == '"':
                instr = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[i:j + 1])
                    except json.JSONDecodeError:
                        break
        i = raw.find("{", i + 1)
    return None


def kanji_num(n):
    """1~99 를 한글 수사로（십일, 이십, 삼십오）. v1.0: 20장을 넘는 작품을 위해."""
    if n <= 10:
        return KANJI[n]
    t, o = divmod(n, 10)
    return (KANJI[t] if t > 1 else "") + "십" + (KANJI[o] if o else "")


def extract_chapter(raw, n):
    kn = kanji_num(n) if n < 100 else str(n)
    zen = "".join(chr(0xFF10 + int(d)) for d in str(n))
    m = re.search(rf"^#\s*(?:제|第)\s*(?:{n}|{kn}|{zen})\s*(?:장|章).*$", raw, flags=re.M)
    if not m:
        return None
    text = raw[m.start():]
    text = re.sub(r"\n```.*$", "", text, flags=re.S).strip()
    first, _, rest = text.partition("\n")
    first = re.sub(r"^#\s*(?:제|第)\s*\S+?\s*(?:장|章)", f"# 제{n}장", first)
    return first + "\n" + rest.strip() + "\n"


def dedupe_repeat(text):
    """작가가 본문을 통째로 두 번 출력했을 때（제3회 제6장에서 3고 중 2고）, 뒤쪽의 반복을 버린다.
    반환값은 (고친 본문, 버린 글자 수). 반복이 아니면 (그대로, 0)."""
    import difflib
    first, _, body = text.partition("\n")
    key = re.sub(r"\s+", "", body)[:40]
    if len(key) < 20:
        return text, 0
    # 공백을 무시하고 key 의 두 번째 출현을 찾는다（본문 위치로 되돌리기 위해 공백을 건너뛰며 대조한다）
    flat_idx = [i for i, c in enumerate(body) if not c.isspace()]
    flat = "".join(body[i] for i in flat_idx)
    j = flat.find(key, len(key))
    if j < 0:
        return text, 0
    pos = flat_idx[j]
    head, tail = body[:pos], body[pos:]
    if difflib.SequenceMatcher(None, head[:1500], tail[:1500]).ratio() < 0.85:
        return text, 0
    head = re.sub(r"#\s*(?:제|第)\s*\S+?\s*(?:장|章)[^\n]*\s*$", "", head.rstrip()).rstrip()
    return first + "\n" + head + "\n", len(tail)


def ends_properly(body):
    return bool(re.search(r"[。.」』”’\"'！？!?…―）)]\s*$", body))


META = re.compile(r"(?<![A-Za-z0-9])(?:" + "|".join(sorted(CFG["slot_plan"], key=len, reverse=True)) +
                  r")(?![0-9])|복선|슬롯|장\s?카드|CANON|LEDGER|^알겠습니다|다음은\s?본문|아래는\s?본문|이상으로\s?제\s?\d+\s?장|요청하신", re.M)
LEDGER_FIELDS = CFG.get("ledger_fields", ["SUMMARY:", "FACT:", "STATE:", "PLACE:", "INSERT:", "OPEN:", "LAST:"])


def mech_check(text, n):
    issues = []
    body = chapter_body(text)
    c = count_chars(body)
    lim = CFG["chapter_chars"]
    if c < lim["min"]:
        issues.append(("short", f"본문이 {c}자로 짧다（{lim['min']}~{lim['max']}자）. 장면 묘사를 두텁게 해서 {lim['target']}자 안팎으로 한다."))
    if c > lim["max"]:
        issues.append(("long", f"본문이 {c}자로 길다（{lim['min']}~{lim['max']}자）. 설명적인 부분을 깎아 {lim['target']}자 안팎으로 한다."))
    if not ends_properly(body):
        issues.append(("truncated", "본문이 문장 도중에 끝나 있다. 마지막 장면을 문장으로 완결시킨다."))
    leak = sorted(set(META.findall(body)))
    if leak:
        issues.append(("meta", f"본문에 관리 용어나 머리말이 섞여 있다：{', '.join(leak)}. 이야기의 문장만으로 한다."))
    if "```" in body or re.search(r"^\s*#", body, flags=re.M):
        issues.append(("format", "제목 이외의 Markdown 기법이 본문에 섞여 있다. 본문만으로 한다."))
    issues += guard.scan_text(body, CFG.get("guard", {}).get("allow_phrases", []))
    return c, issues


def canon_check(text):
    issues = []
    if "（기입）" in text:
        issues.append(f"“（기입）”이 {text.count('（기입）')}곳 남아 있다. 모두 구체적인 내용으로 채운다.")
    for i in range(1, N_CH + 1):
        if f"<!-- CARD:{nn(i)} -->" not in text or f"<!-- /CARD:{nn(i)} -->" not in text:
            issues.append(f"제{i}장 장 카드의 표식（<!-- CARD:{nn(i)} --> 와 <!-- /CARD:{nn(i)} -->）이 없다.")
    for sid in CFG["slot_plan"]:
        if not re.search(rf"^[ \t]*(?:[-*・·|][ \t]*)?(?:\*\*)?[ \t]*{sid}(?![0-9])", text, flags=re.M):
            issues.append(f"제7절에 {sid} 행이 없다.")
    c = count_chars(text)
    if c > CFG["canon_max_chars"]:
        issues.append(f"CANON 이 {c}자로 너무 길다（상한 {CFG['canon_max_chars']}자）. 각 항목을 짧게 한다.")
    if CFG.get("require_insert_section") and not re.search(r"^## 13\.", text, flags=re.M):
        issues.append("제13절 ‘추가 요소 신고’가 없다. BRIEF 와 시리즈 자료에 없는 것을 더했다면 한 건에 한 줄로 신고한다（없으면 ‘INSERT: 없음’）.")
    issues += [msg for _, msg in guard.scan_text(text, CFG.get("guard", {}).get("allow_phrases", []), skip_negated=True)]
    return issues


SLOT_RE = re.compile(r"^SLOT:\s*([A-Z]\d)\s*=\s*(planted|reinforced|recovered)", re.M)


def ledger_check(block, n):
    issues = []
    for pre in LEDGER_FIELDS:
        if not re.search(rf"^{pre}", block, flags=re.M):
            issues.append(f"{pre} 행이 없다")
    found = {sid: st for sid, st in SLOT_RE.findall(block)}
    for sid, ev in events_for(n):
        if sid not in found:
            issues.append(f"SLOT: {sid} 행이 없다（이 장에서 {EV_JA[ev]}는 계획）")
        elif ev == "recovered" and found[sid] != "recovered":
            issues.append(f"{sid} 는 이 장에서 회수될 예정인데 {found[sid]} 로 되어 있다")
    for sid, st in found.items():
        rc = recover_ch(sid) if sid in CFG["slot_plan"] else None
        if st == "recovered" and rc and rc > n:
            issues.append(f"PREMATURE: {sid} 가 제{n}장에서 회수 처리됨（예정은 제{rc}장）")
    return issues


# ---------------------------------------------------------------- 진행（상태 기계）
def fresh_progress():
    return {"phase": "design", "design_state": "draft", "design_attempts": 0, "canon_sha256": None,
            "current": 1, "final_state": "assemble", "final_reopened": False, "escalation": None,
            "chapters": {str(i): {"state": "pending", "attempts": 0, "parse_retries": 0,
                                  "ledger_attempts": 0, "warnings": []} for i in range(1, N_CH + 1)}}


def cmd_init():
    for d in ["canon", "state", "chapters", "work", "output", "logs"]:
        P(d).mkdir(exist_ok=True)
    if not LEDGER.exists():
        shutil.copy(P("templates", "LEDGER_TEMPLATE.md"), LEDGER)
    if not PROGRESS.exists():
        save(fresh_progress())
        log("INIT")
        print("초기화했습니다.")
    else:
        print("이미 초기화되어 있습니다（progress.json 은 바꾸지 않았습니다）.")
    if "call_llm.py" in " ".join(CFG["writer_cmd"]):
        names = CFG["llm"].get("api_key_env", ["OPENROUTER_API_KEY"])
        if not any(os.environ.get(n) for n in names) and not P(".env").exists():
            print("주의: API 키를 찾을 수 없습니다（환경 변수 " + " / ".join(names) + " 도 .env 도 없음）. "
                  "`python3 tools/call_llm.py --ping` 이 통과하는지 확인한 다음 step 하세요.")


def verify_canon_lock(p):
    if p["canon_sha256"] and sha(CANON) != p["canon_sha256"]:
        raise Escalate("CANON_MODIFIED", "잠근 뒤에 canon/CANON.md 가 바뀌었다",
                       "의도한 변경이면 `resolve --relock`, 아니면 git 등으로 되돌리고 `resolve`")


def step_design(p):
    a = p["design_attempts"]
    if p["design_state"] == "draft":
        if a >= CFG["max_design_attempts"]:
            raise Escalate("DESIGN_EXHAUSTED", f"CANON 설계가 {a}회 실패",
                           "work/canon_feedback.md 를 읽고 사람이 고른다：`rescue canon`（상위 모델로 한 번만 전체 수정）／"
                           "`redesign --feedback ...`（사람의 지시로 재설계）／CANON 을 읽고 괜찮다고 보면 `approve-canon`. "
                           "상위 모델로 안 고쳐지면 `rescue canon --model <다른 모델>`")
        pk = build_packet("design")
        raw = call(pk, P("work", f"design_a{a + 1}.out"))
        m = re.search(r"^#\s*CANON.*", raw, flags=re.M | re.S)
        text = re.sub(r"\n```\s*$", "", m.group(0)).strip() + "\n" if m else ""
        issues = canon_check(text) if text else ["출력이 `# CANON` 으로 시작하지 않는다."]
        if text:
            wr(CANON, text)
        p["design_attempts"] = a + 1
        if issues:
            wr(P("work", "canon_feedback.md"), "\n".join(f"- {i}" for i in issues))
            log(f"DESIGN fail: {issues}")
            return f"CANON 초고 불합격（기계 검사）: {issues[0]} 외 {len(issues) - 1}건 → 재설계"
        p["design_state"] = "review"
        return "CANON 초고를 canon/CANON.md 에 저장 → 다음은 심사"
    # review
    pk = build_packet("canon-review")
    raw = call(pk, P("work", f"canon_review_a{a}.out"), "reviewer")
    j = extract_json(raw)
    if j is None:
        p["design_parse_retries"] = p.get("design_parse_retries", 0) + 1
        if p["design_parse_retries"] > CFG["max_parse_retries"]:
            log("WARN canon review JSON unparsable; accepting on mechanical checks only")
            j = {"pass": True, "issues": []}
        else:
            return "심사 결과 JSON 을 읽지 못했다 → 심사를 다시 한다"
    high = [i for i in j.get("issues", []) if i.get("severity") == "high"]
    if j.get("pass") and not high:
        if p.get("canon_rescues") and p["canon_rescues"][-1].get("result") == "심사 대기":
            p["canon_rescues"][-1]["result"] = "합격"
        p["canon_sha256"] = sha(CANON)
        p["phase"] = "canon_hold" if CFG["pause_after_canon"] else "chapters"
        wr(P("work", "canon_review_notes.md"), json.dumps(j, ensure_ascii=False, indent=2))
        log("CANON locked")
        return "CANON 이 심사를 통과해 잠갔습니다" + ("（사람의 확인 대기：`approve-canon`）" if CFG["pause_after_canon"] else " → 제1장으로")
    fb = "\n".join(f"- [심사 항목 {i.get('item')}] {i.get('detail')} → {i.get('fix')}" for i in high)
    wr(P("work", "canon_feedback.md"), fb)
    if p.get("canon_rescues") and p["canon_rescues"][-1].get("result") == "심사 대기":
        p["canon_rescues"][-1]["result"] = f"심사에서 불합격（high {len(high)}건）"
    p["design_state"] = "draft"
    log(f"CANON review fail: {len(high)} high")
    return f"CANON 심사 불합격（high {len(high)}건）→ 수정 설계"


def step_chapter(p):
    verify_canon_lock(p)
    n = p["current"]
    ch = p["chapters"][str(n)]
    draft, fb = P("work", f"ch{nn(n)}_draft.md"), P("work", f"ch{nn(n)}_feedback.md")

    if n in CFG.get("given_chapters", []) and ch["state"] in ("pending", "rewrite"):
        # Claude 가 먼저 쓴 장: 쓰지 않고, 심사도 하지 않고, 기록（LEDGER）만 만들어 통과한다（제6회. 2026-10-11）
        given = P("chapters", f"ch{nn(n)}.md")
        if not given.exists():
            raise Escalate("GIVEN_MISSING", f"전달되어야 할 제{n}장（chapters/ch{nn(n)}.md）이 없다",
                           "사람이 chapters/ 를 확인한다")
        shutil.copy(given, draft)
        ch["state"] = "accepted"
        ch.setdefault("warnings", []).append("전달된 본문（Claude）")
        log(f"CH{nn(n)} given (Claude)")
        return f"제{n}장은 전달된 본문（Claude）→ 다음은 LEDGER 기록"

    if ch["state"] in ("pending", "rewrite"):
        if ch["state"] == "pending":
            for f in (draft, fb):
                if f.exists():
                    f.unlink()
        k = ch["attempts"] + 1
        pk = build_packet("write", n)
        raw = call(pk, P("work", f"ch{nn(n)}_write_a{k}.out"))
        text = extract_chapter(raw, n)
        if text and not ends_properly(chapter_body(text)) and count_chars(chapter_body(text)) < CFG["chapter_chars"]["max"]:
            more = continue_call(pk, text, P("work", f"ch{nn(n)}_write_a{k}_cont.out"))
            text = text.rstrip() + more.strip() + "\n"
            log(f"CH{nn(n)} continued after truncation")
        if text:
            text, cut = dedupe_repeat(text)
            if cut:
                ch.setdefault("warnings", []).append(f"a{k}: 본문 반복 {cut}자를 삭제")
                log(f"CH{nn(n)} a{k}: 본문 반복을 삭제（{cut}자）")
        if not text:
            text = f"# 제{n}장\n" + raw.strip() + "\n"
            ch.setdefault("warnings", []).append(f"a{k}: 제목을 찾지 못해 보충")
        wr(draft, text)
        wr(P("work", f"ch{nn(n)}_draft_a{k}.md"), text)
        ch["attempts"] = k
        ch["state"] = "drafted"
        ch["parse_retries"] = 0
        return f"제{n}장 초고 {k}고를 작성（{count_chars(chapter_body(text))}자）→ 다음은 심사"

    if ch["state"] == "drafted":
        text = rd(draft)
        c, mech = mech_check(text, n)
        hard_mech = [m for m in mech if m[0] in ("truncated", "meta", "format")]
        review = {"pass": True, "violations": [], "slots_found": {}}
        if not hard_mech:
            pk = build_packet("review", n)
            try:
                raw = call(pk, P("work", f"ch{nn(n)}_review_a{ch['attempts']}.out"), "reviewer")
            except Escalate as e:
                # 심사역（Aion）의 추론 폭주로 3번 모두 마감·상한에 걸렸을 때는 멈추지 않는다.
                # 기계 검사만으로 판정하고 ‘교정 필요’ 표시를 남긴 채 다음으로 간다（마지막 Claude 교정을 관문으로 삼는다）. 2026-10-04 프로듀서 판단
                runaway = ("마감" in e.detail or "finish_reason=length" in e.detail or "Upstream idle" in e.detail)
                if e.code != "CALL_FAILED" or not runaway or not CFG.get("review_fallback_on_runaway", True):
                    raise
                raw = None
                ch.setdefault("warnings", []).append(f"교정 필요 a{ch['attempts']}: 심사역이 마감으로 3회 실패, 기계 검사만으로 판정")
                log(f"CH{nn(n)} 교정 필요: 심사역 호출이 실패（추론 폭주）→ 기계 검사만으로 판정")
            j = extract_json(raw) if raw is not None else {"pass": True, "violations": [], "slots_found": {}, "_fallback": "review_call_failed"}
            if j is None:
                ch["parse_retries"] += 1
                if ch["parse_retries"] <= CFG["max_parse_retries"]:
                    return f"제{n}장 심사 JSON 을 읽지 못했다 → 심사를 다시 한다"
                ch["warnings"].append(f"a{ch['attempts']}: 심사 JSON 을 읽을 수 없어 기계 검사만으로 판정")
            else:
                review = j
        problems = [f"[{t}] {d}" for t, d in mech]
        high = [v for v in review.get("violations", []) if v.get("severity") == "high"]
        problems += [f"[{v.get('type')}] {v.get('detail')} → {v.get('fix')}" for v in high]
        found = review.get("slots_found", {}) or {}
        if found:
            for sid, ev in events_for(n):
                st = found.get(sid, "none")
                if st in (None, "none") or (ev == "recovered" and st != "recovered"):
                    problems.append(f"[slots] {sid}（{CFG['slot_names'][sid]}）：이 장에서 {EV_JA[ev]}는 계획이지만 원고에서 확인할 수 없다. CANON 제7절의 {sid} 행대로 묘사한다.")
            for sid, st in found.items():
                rc = recover_ch(sid) if sid in CFG["slot_plan"] else None
                if st == "recovered" and rc and rc > n:
                    problems.append(f"[premature] {sid} 의 내용을 제{rc}장보다 앞에서 밝히고 있다. 암시에 그친다.")
        lows = [v for v in review.get("violations", []) if v.get("severity") == "low"]
        wr(P("work", f"ch{nn(n)}_verdict_a{ch['attempts']}.json"),
           json.dumps({"chars": c, "mech": mech, "review": review, "problems": problems}, ensure_ascii=False, indent=2))
        if not problems:
            if ch.get("rescues") and ch["rescues"][-1].get("result") == "심사 대기":
                ch["rescues"][-1]["result"] = "합격"
            return accept(p, n, f"제{n}장 합격（{c}자）" + (f"／가벼운 지적 {len(lows)}건은 기록만" if lows else ""))
        wr(fb, "\n".join(f"- {x}" for x in problems))
        if ch.get("rescued") and ch.get("rescues"):
            ch["rescues"][-1]["result"] = "심사에서 불합격：" + " / ".join(problems[:2])[:200]
        if ch["attempts"] >= CFG["max_attempts"] or ch.get("rescued"):
            soft_only = all(x.startswith(("[short]", "[long]")) for x in problems)
            if soft_only:
                ch["warnings"].append(f"글자 수가 범위를 벗어난 채 채택（{c}자）")
                return accept(p, n, f"제{n}장 글자 수만 범위 밖（{c}자）으로 채택")
            raise Escalate("CHAPTER_EXHAUSTED", f"제{n}장이 {ch['attempts']}고로도 합격하지 않는다: " + " / ".join(problems[:3]),
                           f"work/ch{nn(n)}_feedback.md 와 work/ch{nn(n)}_verdict_a*.json 을 확인. " +
                           (f"상위 모델（{', '.join(r['model'] for r in ch.get('rescues', []))}）로 고쳐도 통과하지 못했다. "
                            f"다른 모델로 `rescue chapter {n} --model <모델> [--note 지시]`／" if ch.get("rescued") else
                            f"사람이 고른다：`rescue chapter {n}`（상위 모델로 지적 부분을 한 번만 수정）／`resolve --retry`（작가에게 3고 더）／") +
                           "원고 그대로 괜찮으면 `resolve --accept`")
        ch["state"] = "rewrite"
        log(f"CH{nn(n)} fail a{ch['attempts']}: {len(problems)} problems")
        return f"제{n}장 불합격（{len(problems)}건）→ 다시 쓰기（{ch['attempts'] + 1}고째）"

    if ch["state"] == "accepted":
        if ch["ledger_attempts"] >= CFG["max_ledger_attempts"]:
            raise Escalate("LEDGER_FAILED", f"제{n}장의 LEDGER 기록이 서식을 채우지 못한다",
                           f"work/ch{nn(n)}_ledger_a*.out 을 보고 state/LEDGER.md 에 ## CH{nn(n)} 절을 손으로 추가한 뒤 `resolve --ledger-done`")
        ch["ledger_attempts"] += 1
        pk = build_packet("ledger", n)
        raw = call(pk, P("work", f"ch{nn(n)}_ledger_a{ch['ledger_attempts']}.out"), "reviewer")
        m = re.search(rf"^## CH{nn(n)}\s*$.*", raw, flags=re.M | re.S)
        block = re.sub(r"\n```.*$", "", m.group(0), flags=re.S).strip() if m else ""
        issues = ledger_check(block, n) if block else [f"`## CH{nn(n)}` 으로 시작하지 않는다"]
        if issues:
            if any(i.startswith("PREMATURE") for i in issues):
                ch["warnings"].append("LEDGER 가 너무 이른 회수를 보고: " + "; ".join(issues))
            log(f"CH{nn(n)} ledger fail: {issues}")
            return f"제{n}장 LEDGER 기록이 미비（{issues[0]}）→ 기록을 다시 한다"
        write_ledger_section(n, block)
        return finish_chapter(p, n)
    raise RuntimeError(f"알 수 없는 장 상태: {ch['state']}")


def accept(p, n, msg):
    shutil.copy(P("work", f"ch{nn(n)}_draft.md"), P("chapters", f"ch{nn(n)}.md"))
    p["chapters"][str(n)]["state"] = "accepted"
    log(f"CH{nn(n)} accepted")
    return msg + f" → chapters/ch{nn(n)}.md 로 확정 → 다음은 LEDGER 기록"


def write_ledger_section(n, block):
    head, secs = ledger_sections(rd(LEDGER))
    secs[n] = re.sub(rf"^## CH{nn(n)}\s*\n", "", block).strip()
    out = head.rstrip() + "\n\n" + "\n\n".join(f"## CH{nn(c)}\n{secs[c]}" for c in sorted(secs)) + "\n"
    wr(LEDGER, out)


def finish_chapter(p, n):
    p["chapters"][str(n)]["state"] = "done"
    log(f"CH{nn(n)} done")
    # 최종 심사 뒤에 고친 장이면 아직 끝나지 않은 다음 장으로（없으면 최종 공정으로）. v1.1
    nxt = next((i for i in range(n + 1, N_CH + 1) if p["chapters"][str(i)]["state"] != "done"), None)
    if nxt is None:
        p["phase"], p["final_state"] = "final", "assemble"
        return f"제{n}장 LEDGER 기록 완료 → 모든 장이 갖춰졌다. 최종 공정으로"
    p["current"] = nxt
    return f"제{n}장 LEDGER 기록 완료 → 제{nxt}장으로"


def cmd_assemble(quiet=False, polished=False):
    title = "무제"
    if CANON.exists():
        m = re.search(r"제목\s*[：:]\s*(.+)", canon())
        if m:
            title = m.group(1).strip()
    parts, total = [], 0
    for i in range(1, N_CH + 1):
        f = P("polished" if polished else "chapters", f"ch{nn(i)}.md")
        if polished and not f.exists():
            f = P("chapters", f"ch{nn(i)}.md")  # 다듬지 않는 장（전달된 장 등）은 원고를 그대로 쓴다
        if f.exists():
            t = rd(f).strip()
            total += count_chars(chapter_body(t))
            parts.append(t.replace("# 제", "## 제", 1))
    name = "polished.md" if polished else "final.md"
    wr(P("output", name), f"# {title}\n\n" + "\n\n".join(parts) + "\n")
    if not quiet:
        print(f"output/{name} 을 작성（본문 합계 {total}자）")
    return total


def step_final(p):
    verify_canon_lock(p)
    if p["final_state"] == "assemble":
        total = cmd_assemble(quiet=True)
        _, secs = ledger_sections(rd(LEDGER))
        missing = []
        for sid, evs in CFG["slot_plan"].items():
            for c, ev in evs:
                if not re.search(rf"^SLOT:\s*{sid}\s*=", secs.get(c, ""), flags=re.M):
                    missing.append(f"{sid}@제{c}장")
        rng = CFG["total_chars"]
        notes = []
        if not rng["min"] <= total <= rng["max"]:
            notes.append(f"합계 글자 수 {total} 가 기준 {rng['min']}~{rng['max']} 의 범위 밖")
        if missing:
            notes.append("LEDGER 에서 확인할 수 없는 복선 조작: " + ", ".join(missing))
        p["final_notes"] = notes
        p["final_state"] = "review"
        return f"output/final.md 를 결합（{total}자）" + (f"／주의: {' / '.join(notes)}" if notes else "") + " → 최종 심사로"
    pk = build_packet("final")
    raw = call(pk, P("work", "final_review.out"), "reviewer")
    j = extract_json(raw) or {"pass": True, "issues": [], "summary": "（최종 심사 JSON 을 읽지 못했다）"}
    high = [i for i in j.get("issues", []) if i.get("severity") == "high"]
    report = ["# 최종 보고서", "", f"- 합계 글자 수: {cmd_assemble(quiet=True)}",
              f"- 최종 심사: {'합격' if j.get('pass') and not high else '확인 필요'}",
              f"- 총평: {j.get('summary', '')}", "", "## 지적"]
    report += [f"- [{i.get('severity')}] 제{i.get('chapter')}장: {i.get('detail')} → {i.get('fix')}" for i in j.get("issues", [])] or ["- 없음"]
    report += ["", "## 자동 검사 주의"] + ([f"- {x}" for x in p.get("final_notes", [])] or ["- 없음"])
    report += ["", "## 장별 경고"] + [f"- 제{k}장: {w}" for k, v in p["chapters"].items() for w in v.get("warnings", [])]
    wr(P("output", "final_report.md"), "\n".join(report) + "\n")
    if j.get("pass") and not high:
        p["phase"] = "done"
        return "최종 심사 합격. output/final.md 와 output/final_report.md 가 완성품입니다"
    only_last = all(i.get("chapter") == N_CH for i in high)
    if only_last and not p["final_reopened"]:
        p["final_reopened"] = True
        wr(P("work", f"ch{nn(N_CH)}_draft.md"), rd(P("chapters", f"ch{nn(N_CH)}.md")))
        wr(P("work", f"ch{nn(N_CH)}_feedback.md"), "\n".join(f"- [최종 심사] {i.get('detail')} → {i.get('fix')}" for i in high))
        c = p["chapters"][str(N_CH)]
        c.update({"state": "rewrite", "attempts": 0, "ledger_attempts": 0})
        p["phase"], p["current"] = "chapters", N_CH
        return f"최종 심사에서 제{N_CH}장에 중대한 지적 → 제{N_CH}장을 한 번만 다시 쓴다"
    raise Escalate("FINAL_REVIEW_FAILED", "최종 심사에서 중대한 지적 있음（output/final_report.md 참조）",
                   "상위 모델로 고치려면 `rescue chapter N [--model M] [--note 지시]`（최종 심사의 지적을 수정 지시로 삼는다）, "
                   "해당 장을 `reset-chapter N` 으로 되감거나, 원고 그대로 완성으로 하려면 `resolve --done`")


# ---------------------------------------------------------------- 문체 마무리（polisher）
def scene_breaks(body):
    return len(re.findall(r"^\s*[＊*]\s*$", body, flags=re.M))


def dialogues(body):
    # novel_worker：모델이 곧은따옴표 "…" 로 대사를 쓴 원고도 센다(안 세면 대사 보호가 꺼진다)
    return body.count("“") + body.count("「") + body.count('"') // 2


def polish_check(src, out, n):
    pc = CFG.get("polish", {})
    issues = []
    sb, ob = chapter_body(src), chapter_body(out)
    cs, co = count_chars(sb), count_chars(ob)
    ratio = co / max(cs, 1)
    if ratio < pc.get("len_ratio_min", 0.9):
        issues.append(f"글자 수가 {co}자로, 원고（{cs}자）보다 크게 줄었다. 장면이나 대사를 깎지 않는다.")
    if ratio > pc.get("len_ratio_max", 1.3):
        issues.append(f"글자 수가 {co}자로, 원고（{cs}자）보다 너무 늘었다. 묘사 추가는 절제한다.")
    if dialogues(ob) < dialogues(sb) * pc.get("dialogue_ratio_min", 0.85):
        issues.append(f"대사（“ ”）가 {dialogues(sb)}개에서 {dialogues(ob)}개로 줄었다. 대사는 하나도 깎지 않는다.")
    if scene_breaks(ob) != scene_breaks(sb):
        issues.append(f"장면 전환（＊ 줄）이 {scene_breaks(sb)}개에서 {scene_breaks(ob)}개로 바뀌었다. 장면의 구분은 그대로 남긴다.")
    if not ends_properly(ob):
        issues.append("마지막이 문장 도중에 끊겨 있다.")
    leak = sorted(set(META.findall(ob)))
    if leak:
        issues.append(f"관리 용어나 머리말이 섞여 있다：{', '.join(leak)}")
    if "```" in ob or re.search(r"^\s*#", ob, flags=re.M):
        issues.append("제목 이외의 Markdown 기법이 섞여 있다.")
    # 원고의 마지막 한 문장은 바꾸지 않는다（마무리 한 문장은 작품 설계의 일부）
    last_line = [l for l in sb.strip().splitlines() if l.strip()][-1].strip()
    sents = [x for x in re.split(r"(?<=[。.！？!?」』”’])", last_line) if x.strip()]
    last = sents[-1].strip() if sents else last_line
    if last not in ob:
        issues.append(f"장의 마지막 한 문장 “{last[:40]}”이 바뀌었다. 마지막 한 문장은 한 글자도 바꾸지 말고 남긴다.")
    return cs, co, issues


def cmd_polish(args):
    targets = [int(x) for x in args if x.isdigit()] or [i for i in range(1, N_CH + 1) if i not in CFG.get("given_chapters", [])]
    force = "--force" in args
    P("polished").mkdir(exist_ok=True)
    maxa = CFG.get("polish", {}).get("max_attempts", 3)
    report = []
    for n in targets:
        dst = P("polished", f"ch{nn(n)}.md")
        if dst.exists() and not force:
            print(f"제{n}장: polished/ch{nn(n)}.md 는 이미 작성됨（다시 하려면 --force）")
            continue
        src = rd(P("chapters", f"ch{nn(n)}.md"))
        fbp = P("work", f"polish_ch{nn(n)}_feedback.md")
        fbp.unlink(missing_ok=True)
        best = None
        for k in range(1, maxa + 1):
            pk = build_packet("polish", n)
            cmd_backup = CFG["writer_cmd"]
            try:
                CFG["writer_cmd"] = CFG.get("polisher_cmd", CFG["writer_cmd"])
                raw = call(pk, P("work", f"polish_ch{nn(n)}_a{k}.out"), "polisher")
            except Escalate as e:
                log(f"POLISH CH{nn(n)} call failed: {e.code} {e.detail[:200]}")
                print(f"제{n}장: 호출 실패 [{e.code}] {e.detail[:300]}")
                return 2
            finally:
                CFG["writer_cmd"] = cmd_backup
            text = extract_chapter(raw, n) or (f"# 제{n}장\n" + raw.strip() + "\n")
            # 장 제목은 원고의 것을 쓴다
            text = src.strip().splitlines()[0] + "\n" + chapter_body(text) + "\n"
            cs, co, issues = polish_check(src, text, n)
            wr(P("work", f"polish_ch{nn(n)}_a{k}.md"), text)
            log(f"POLISH CH{nn(n)} a{k}: {cs}->{co} issues={len(issues)}")
            if not issues:
                best = text
                break
            wr(fbp, "\n".join(f"- {i}" for i in issues))
            print(f"제{n}장 a{k}: 불합격 {issues}")
        if best:
            wr(dst, best)
            report.append(f"- 제{n}장: 합격（{count_chars(chapter_body(src))}자 → {count_chars(chapter_body(best))}자）")
            print(f"제{n}장: polished/ch{nn(n)}.md 를 저장")
        else:
            report.append(f"- 제{n}장: {maxa}회 모두 기계 검사 불합격. 원고 그대로 채택하지 않고 사람의 판단 대기（work/polish_ch{nn(n)}_a*.md）")
            print(f"제{n}장: {maxa}회 모두 불합격. work/polish_ch{nn(n)}_a*.md 를 확인하세요")
    if report:
        with open(P("output", "polish_report.md"), "a", encoding="utf-8") as f:
            f.write(f"\n## {datetime.datetime.now().isoformat(timespec='seconds')}\n" + "\n".join(report) + "\n")
    return 0


DIALOGUE_RE = re.compile(r"“[^”]*”|「[^」]*」|\"[^\"\n]*\"")


def _arg(args, name):
    """--name 값. 없거나 값이 빠졌으면 None."""
    if name not in args:
        return None
    i = args.index(name) + 1
    return args[i] if i < len(args) and not args[i].startswith("--") else None


def cmd_accept_polish(args):
    """novel_worker：바깥 도구(humanize-korean 등)가 다듬은 원고를 받아들인다. 통과하면 polished/chNN.md 로.
    기준 원고는 proof_source(n)(polished 가 있으면 그것). 기존 polish_check + 대사 원문 동일성. 실패하면 아무것도 바꾸지 않는다."""
    usage = "usage: novelctl.py accept-polish N --from FILE [--label humanize]"
    src_path, label = _arg(args, "--from"), _arg(args, "--label") or "external"
    if not args or not args[0].isdigit() or not src_path or ("--label" in args and not _arg(args, "--label")):
        print(usage)
        return 1
    n = int(args[0])
    if not 1 <= n <= N_CH:
        print(f"장 번호는 1~{N_CH}")
        return 1
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,30}", label):
        print("--label 은 영문·숫자·_- 30자 이내")
        return 1
    fromf = Path(src_path).expanduser()
    if not fromf.is_file():
        print(f"--from 파일이 없습니다：{fromf}")
        return 1
    srcf = proof_source(n)
    if not srcf.exists():
        print(f"제{n}장 원고（{srcf.relative_to(ROOT)}）가 없습니다")
        return 1
    src = rd(srcf)
    raw = fromf.read_text(encoding="utf-8")
    raw = re.sub(r"\s*<!--(?:(?!<!--).)*?-->\s*\Z", "", raw.strip(), flags=re.S)  # humanize-korean 의 끝 요약 주석（마지막 하나만）
    first = next((l for l in raw.splitlines() if l.strip()), "")
    body = chapter_body(raw) if first.lstrip().startswith("#") else raw.strip()
    text = src.strip().splitlines()[0] + "\n" + body + "\n"  # 장 제목은 기준 원고의 것
    cs, co, issues = polish_check(src, text, n)
    if "<!--" in body:
        issues.append("HTML 주석（<!-- … -->）이 본문에 남아 있다. 도구의 메모는 원고에 넣지 않는다.")
    ds, do = DIALOGUE_RE.findall(chapter_body(src)), DIALOGUE_RE.findall(body)
    if ds != do:
        changed = next((f"{i + 1}번째 “{a[1:31]}…” → “{b[1:31]}…”" for i, (a, b) in enumerate(zip(ds, do)) if a != b),
                       f"대사 개수 {len(ds)} → {len(do)}")
        issues.append(f"대사가 바뀌었다（{changed}）. 대사는 한 글자도 바꾸지 않는다.")
    log(f"ACCEPT-POLISH CH{nn(n)} {label}: {cs}->{co} issues={len(issues)} base={srcf.relative_to(ROOT)}")
    if issues:
        print(f"제{n}장: 받아들이지 않음（{srcf.relative_to(ROOT)} 그대로）")
        for i in issues:
            print(f"  - {i}")
        return 2
    P("polished").mkdir(exist_ok=True)
    dst = P("polished", f"ch{nn(n)}.md")
    if dst.exists():  # 이전 판을 덮어쓰지 않고 보관한다(같은 label 로 여러 번 받아들여도 모두 남는다)
        bak, k = P("polished", f"ch{nn(n)}.before-{label}.md"), 2
        while bak.exists():
            bak, k = P("polished", f"ch{nn(n)}.before-{label}-{k}.md"), k + 1
        dst.replace(bak)
        print(f"  이전 판 보관：{bak.relative_to(ROOT)}")
    wr(dst, text)
    with open(P("output", "polish_report.md"), "a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.datetime.now().isoformat(timespec='seconds')} {label}\n- 제{n}장: 합격（{cs}자 → {co}자, 기준 {srcf.relative_to(ROOT)}）\n")
    print(f"제{n}장: polished/ch{nn(n)}.md 로 받아들임（{cs}자 → {co}자, {label}）")
    return 0


def cmd_set_budget(args):
    """novel_worker：작품 예산(budget_usd)을 바꾼다. config.json 은 보호 파일이라 손으로 고치면 GUARD_STOP 이 되므로,
    다른 보호 파일이 기록과 같을 때만 바꾸고 지문을 다시 뜬다(몰래 바뀐 파일을 함께 '정상'으로 만들지 않기 위해).
    사람이 예산을 정한 뒤에만 실행한다(진행자는 사용자의 선택을 받은 뒤)."""
    try:
        usd = float(args[0])
        if not 0 < usd <= 1000:
            raise ValueError
    except (IndexError, ValueError):
        print("usage: novelctl.py set-budget USD   (0 < USD <= 1000)")
        return 1
    bad = guard.check_integrity()
    if bad:
        print("보호 파일이 기록과 달라 예산을 바꾸지 않습니다（먼저 원인을 확인하세요）：")
        for _, d in bad:
            print(f"  - {d}")
        return 2
    f = P("config.json")
    cfg = json.loads(rd(f))
    old = cfg.get("budget_usd")
    cfg["budget_usd"] = usd
    wr(f, json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
    guard.seal()
    log(f"SET-BUDGET {old} -> {usd}")
    P("notes").mkdir(exist_ok=True)
    with open(P("notes", "INTERVENTIONS.md"), "a", encoding="utf-8") as fh:
        fh.write(f"- {datetime.datetime.now().isoformat(timespec='seconds')} 예산 변경 {old} → {usd}（set-budget, 사람의 결정）\n")
    print(f"예산을 ${old} → ${usd} 로 바꿨습니다. 멈춰 있었다면 `resolve` 후 다시 진행하세요.")
    return 0


def lock_owner():
    """잠금을 가진 살아 있는 프로세스의 pid. 잠금이 없거나 주인이 죽었으면 None."""
    f = P("state", ".lock")
    if not f.exists():
        return None
    try:
        pid = int(f.read_text().strip() or 0)
        os.kill(pid, 0)
        return pid
    except PermissionError:
        return pid
    except (ValueError, ProcessLookupError, OSError):
        return None


class Lock:
    """동시 실행 방지（에이전트가 step 과 run 을 겹쳐 기동해도 망가지지 않도록）."""
    path = P("state", ".lock")

    def __enter__(self):
        pid = lock_owner()
        if pid:
            sys.exit(f"BUSY: 다른 novelctl（pid {pid}）이 실행 중입니다. 끝날 때까지 기다린 뒤 status 를 확인하세요.")
        wr(self.path, str(os.getpid()))
        return self

    def __exit__(self, *a):
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass


def guard_gate():
    """매 단계 처음에 STOP 과 보호 파일 변경을 본다. 작가의 규칙과는 별도로 기계로 멈춘다."""
    hard = guard.check_stop() + guard.check_integrity()
    if hard:
        raise Escalate("GUARD_STOP", " / ".join(m for _, m in hard),
                       "사람이 원인을 확인한다. STOP 을 치우거나 보호 파일을 되돌린 뒤 `resolve`")


def cmd_step():
    p = load()
    try:
        guard_gate()
    except Escalate as e:
        log(f"GUARD {e.detail}")
        print(f"ESCALATE [{e.code}] {e.detail}\n대응: {e.hint}")
        return 2
    if p.get("escalation"):
        e = p["escalation"]
        print(f"ESCALATE [{e['code']}] {e['detail']}\n대응: {e['hint']}")
        return 2
    try:
        if p["phase"] == "design":
            msg = step_design(p)
        elif p["phase"] == "canon_hold":
            print("CANON 의 사람 확인 대기입니다. canon/CANON.md 를 확인하고 `approve-canon` 을 실행하세요.")
            return 2
        elif p["phase"] == "chapters":
            msg = step_chapter(p)
        elif p["phase"] == "final":
            msg = step_final(p)
        else:
            print("DONE: 완성되었습니다. output/final.md / output/final_report.md")
            return 3
    except Escalate as e:
        p["escalation"] = {"code": e.code, "detail": e.detail, "hint": e.hint, "phase": p["phase"], "chapter": p["current"]}
        save(p)
        log(f"ESCALATE {e.code}: {e.detail}")
        print(f"ESCALATE [{e.code}] {e.detail}\n대응: {e.hint}")
        return 2
    save(p)
    log(f"STEP {msg}")
    print(f"OK: {msg}")
    return 3 if p["phase"] == "done" else 0


def cmd_start(args):
    """run 을 셸에서 떼어 내 기동하고 바로 돌아온다. 에이전트 도구의 제한 시간에 죽지 않는다."""
    pid = lock_owner()
    if pid:
        print(f"BUSY: 이미 실행 중입니다（pid {pid}）. status 로 상황을 보세요.")
        return 0
    P("logs").mkdir(exist_ok=True)
    out = open(P("logs", "run.out"), "a", encoding="utf-8")
    out.write(f"\n===== start {datetime.datetime.now().isoformat(timespec='seconds')} =====\n")
    out.flush()
    sub = ["polish"] + list(args[1:]) if args and args[0] == "polish" else ["run"] + list(args)
    proc = subprocess.Popen([sys.executable, str(Path(__file__).resolve())] + sub,
                            cwd=ROOT, stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                            start_new_session=True, close_fds=True)
    log(f"START detached pid={proc.pid}")
    print(f"STARTED: 백그라운드에서 실행을 시작했습니다（pid {proc.pid}）. 진행 상황은 status, 로그는 logs/run.out.")
    return 0


def cmd_run(max_steps):
    for _ in range(max_steps):
        rc = cmd_step()
        if rc != 0:
            return rc
    print(f"{max_steps}단계를 실행했습니다. 계속하려면 다시 run / step.")
    return 0


def cmd_status():
    p = load()
    print(f"phase: {p['phase']}   current: 제{p['current']}장   canon_locked: {bool(p['canon_sha256'])}")
    if p["phase"] == "design":
        print(f"design: {p['design_state']}（시도 {p['design_attempts']}/{CFG['max_design_attempts']}）")
    for k, v in p["chapters"].items():
        f = P("chapters", f"ch{nn(k)}.md")
        c = count_chars(chapter_body(rd(f))) if f.exists() else 0
        w = f"  경고 {len(v['warnings'])}" if v.get("warnings") else ""
        print(f"  제{k}장 {v['state']:<9} {v['attempts']}고 {c:>5}자{w}")
    if LEDGER.exists():
        _, secs = ledger_sections(rd(LEDGER))
        st = {}
        for c in sorted(secs):
            for sid, s in SLOT_RE.findall(secs[c]):
                st[sid] = f"{s}@{c}"
        print("  복선: " + "  ".join(f"{sid}:{st.get(sid, '-')}" for sid in CFG["slot_plan"]))
    if P("polished").exists():
        done = sorted(f.stem for f in P("polished").glob("ch*.md"))
        if done:
            print("  문체 마무리 완료: " + " ".join(done))
    inflight = P("state", "inflight.json")
    if inflight.exists():
        try:
            f = json.loads(rd(inflight))
            now = time.time()
            print(f"CALLING: {f.get('task')}  시도 {f.get('attempt')}  경과 {now - f['started']:.0f}초  "
                  f"추론 {f.get('reasoning_chars', 0)}자  본문 {f.get('content_chars', 0)}자  "
                  f"마지막 수신 {now - f['last_recv']:.0f}초 전")
        except (ValueError, KeyError):
            pass
    if p.get("escalation"):
        e = p["escalation"]
        print(f"ESCALATE [{e['code']}] {e['detail']}\n대응: {e['hint']}")
        print("NEXT: 위의 대응을 한 다음 python3 tools/novelctl.py resolve")
    elif p["phase"] == "canon_hold":
        print("NEXT: canon/CANON.md 를 사람이 확인 → python3 tools/novelctl.py approve-canon")
    elif p["phase"] == "done":
        print("DONE: output/final.md")
    elif lock_owner() and inflight.exists():
        print("RUNNING: 모델 호출 중. ‘마지막 수신’이 수십 초 이내면 정상. 기다렸다가 다시 status")
    elif lock_owner():
        print("RUNNING: novelctl 이 실행 중（모델 호출 사이）. 기다렸다가 다시 status")
    elif inflight.exists():
        print("STALE: 지난번 호출은 도중에 멈췄습니다（실행 중인 프로세스 없음）.")
        print("NEXT: python3 tools/novelctl.py start   （떼어 내서 재개. 셸의 제한 시간에 죽지 않는다）")
    else:
        print("NEXT: python3 tools/novelctl.py start   （또는 1단계만이라면 step）")


def cmd_resolve(args):
    p = load()
    e = p.get("escalation")
    p["escalation"] = None
    n = p["current"]
    ch = p["chapters"][str(n)]
    if "--retry" in args:
        if p["phase"] == "design":
            p["design_attempts"] = 0
            p["design_state"] = "draft"
        else:
            ch["attempts"], ch["ledger_attempts"] = 0, 0
            if ch["state"] == "drafted":
                ch["state"] = "rewrite"
    if "--accept" in args:
        accept(p, n, "사람의 판단으로 채택")
    if "--ledger-done" in args:
        finish_chapter(p, n)
    if "--relock" in args:
        p["canon_sha256"] = sha(CANON)
    if "--done" in args:
        p["phase"] = "done"
    save(p)
    log(f"RESOLVE {e and e['code']} {args}")
    print("에스컬레이션을 해제했습니다. `status` 로 확인하세요.")


def cmd_reset(n):
    p = load()
    arch = P("work", "archive", datetime.datetime.now().strftime("%Y%m%d-%H%M%S"))
    arch.mkdir(parents=True, exist_ok=True)
    for i in range(n, N_CH + 1):
        f = P("chapters", f"ch{nn(i)}.md")
        if f.exists():
            shutil.move(str(f), arch / f.name)
        for g in P("work").glob(f"ch{nn(i)}_*"):
            shutil.move(str(g), arch / g.name)
        p["chapters"][str(i)] = {"state": "pending", "attempts": 0, "parse_retries": 0, "ledger_attempts": 0, "warnings": []}
    head, secs = ledger_sections(rd(LEDGER))
    secs = {c: b for c, b in secs.items() if c < n}
    wr(LEDGER, head.rstrip() + "\n\n" + "\n\n".join(f"## CH{nn(c)}\n{secs[c]}" for c in sorted(secs)) + "\n")
    p.update({"phase": "chapters", "current": n, "escalation": None, "final_state": "assemble"})
    save(p)
    log(f"RESET from CH{nn(n)}")
    print(f"제{n}장 이후를 미집필로 되돌렸습니다（옛 파일은 {arch.relative_to(ROOT)}）.")



# ---------------------------------------------------------------- v1.0: 상위 모델의 예외 처리·교정·사람의 지시에 의한 재설계
def canon_review_history():
    out = []
    for f in sorted(P("work").glob("canon_review_a*.out"), key=lambda x: int(re.sub(r"\D", "", x.stem) or 0)):
        j = extract_json(rd(f)) or {}
        items = [f"- [{i.get('severity')}] 항목 {i.get('item')}: {i.get('detail')} → {i.get('fix')}" for i in j.get("issues", [])]
        out.append(f"### {f.stem}\n" + ("\n".join(items) if items else "- （지적 없음／읽을 수 없음）"))
    return "\n\n".join(out[-4:]) if out else "（이력 없음）"


def chapter_problem_history(n):
    out = []
    for f in sorted(P("work").glob(f"ch{nn(n)}_verdict_a*.json")):
        try:
            j = json.loads(rd(f))
        except ValueError:
            continue
        out.append(f"### {f.stem}\n" + "\n".join(f"- {x}" for x in j.get("problems", [])))
    fb = P("work", f"ch{nn(n)}_feedback.md")
    if fb.exists():
        out.append("### 현재 남아 있는 지적\n" + rd(fb))
    return "\n\n".join(out) if out else "（지적 기록 없음）"


def proof_source(n):
    f = P("polished", f"ch{nn(n)}.md")
    return f if f.exists() else P("chapters", f"ch{nn(n)}.md")


RESCUE_NOTE = {}
MODEL_RE = re.compile(r"^[A-Za-z0-9_.:/\-]{3,120}$")


def rescue_opts(args):
    """rescue 의 추가 지정: --model M（이 한 번만 쓸 모델）, --note TEXT（사람의 지시）."""
    model = args[args.index("--model") + 1] if "--model" in args else None
    note = args[args.index("--note") + 1] if "--note" in args else ""
    if model and not MODEL_RE.match(model):
        raise ValueError(f"모델 이름이 올바르지 않습니다：{model}")
    return model, note.strip()


def premium_model():
    return (CFG["llm"]["profiles"].get("premium") or {}).get("model", "premium")


def reopen_for_final(p, n):
    """최종 심사에서 떨어진 장을 상위 모델로 고칠 수 있는 상태로 되돌린다（최종 심사의 지적을 수정 지시로 삼는다）."""
    j = extract_json(rd(P("work", "final_review.out"))) if P("work", "final_review.out").exists() else {}
    issues = [i for i in (j or {}).get("issues", []) if i.get("chapter") == n and i.get("severity") == "high"] or \
             [i for i in (j or {}).get("issues", []) if i.get("chapter") == n]
    wr(P("work", f"ch{nn(n)}_draft.md"), rd(P("chapters", f"ch{nn(n)}.md")))
    wr(P("work", f"ch{nn(n)}_feedback.md"), "\n".join(f"- [최종 심사] {i.get('detail')} → {i.get('fix')}" for i in issues) or "- [최종 심사] output/final_report.md 의 지적을 고친다")
    ch = p["chapters"][str(n)]
    ch.update({"state": "drafted", "ledger_attempts": 0})
    p.update({"phase": "chapters", "current": n, "final_state": "assemble"})
    log(f"REOPEN CH{nn(n)} for rescue after final review")


def cmd_rescue(args):
    """상위 모델을 한 번 부르는 예외 처리. 쓰는 것이 아니라 멈춘 곳을 한 번에 고친다.
    v1.1: --model 로 사람이 모델을 고를 수 있다（지정한 상위 모델로 안 고쳐졌을 때 다른 모델로）. --note 로 사람의 지시를 더할 수 있다."""
    if not args or args[0] not in ("canon", "chapter"):
        print("usage: novelctl.py rescue canon [--model M] [--note T] | rescue chapter N [--model M] [--note T]")
        return 1
    try:
        model, note = rescue_opts(args)
    except ValueError as e:
        print(f"NG: {e}")
        return 1
    RESCUE_NOTE["text"] = note
    used = model or premium_model()
    p = load()
    if args[0] == "canon":
        if p["phase"] not in ("design", "canon_hold"):
            print(f"REFUSED: 지금은 {p['phase']} 입니다. CANON 의 예외 처리는 설계 중이거나 승인 대기일 때만 됩니다.")
            return 2
        if not CANON.exists():
            print("REFUSED: canon/CANON.md 가 아직 없습니다.")
            return 2
        a = p["design_attempts"]
        k = len(p.setdefault("canon_rescues", [])) + 1
        pk = build_packet("rescue-canon")
        try:
            raw = call(pk, P("work", f"rescue_canon_a{a}_r{k}.out"), "premium", model)
        except Escalate as e:
            print(f"ESCALATE [{e.code}] {e.detail}\n대응: {e.hint}")
            return 2
        m = re.search(r"^#\s*CANON.*", raw, flags=re.M | re.S)
        text = re.sub(r"\n```\s*$", "", m.group(0)).strip() + "\n" if m else ""
        issues = canon_check(text) if text else ["출력이 `# CANON` 으로 시작하지 않는다."]
        if issues:
            p["canon_rescues"].append({"model": used, "result": "기계 검사에서 불합격", "note": note[:200]})
            save(p)
            log(f"RESCUE canon ({used}) fail: {issues}")
            print(f"NG: {used} 의 CANON 이 기계 검사를 통과하지 못합니다：" + " / ".join(issues[:5]) + ". 다른 모델을 --model 로 고를 수 있습니다.")
            return 2
        shutil.copy(CANON, P("work", f"canon_before_rescue_r{k}.md"))
        wr(CANON, text)
        p["canon_rescues"].append({"model": used, "result": "심사 대기", "note": note[:200]})
        p.update({"phase": "design", "design_state": "review", "canon_sha256": None, "escalation": None})
        p["rescues"] = p.get("rescues", 0) + 1
        save(p)
        log(f"RESCUE canon by {used} → review")
        print(f"OK: {used} 가 CANON 전체를 고쳤습니다（옛 판은 work/canon_before_rescue_r{k}.md）→ 다음은 심사. start 로 계속합니다.")
        return 0
    n = int(args[1]) if len(args) > 1 and args[1].isdigit() else p["current"]
    esc = (p.get("escalation") or {}).get("code")
    if p["phase"] == "final" and esc == "FINAL_REVIEW_FAILED" and 1 <= n <= N_CH:
        reopen_for_final(p, n)
    ch = p["chapters"][str(n)]
    if p["phase"] != "chapters" or n != p["current"] or not P("work", f"ch{nn(n)}_draft.md").exists():
        print(f"REFUSED: 제{n}장은 지금 고칠 수 있는 상태가 아닙니다（phase={p['phase']} current={p['current']}）.")
        return 2
    hist = ch.setdefault("rescues", [])
    k = len(hist) + 1
    pk = build_packet("rescue-chapter", n)
    try:
        raw = call(pk, P("work", f"ch{nn(n)}_rescue_r{k}.out"), "premium", model)
    except Escalate as e:
        print(f"ESCALATE [{e.code}] {e.detail}\n대응: {e.hint}")
        return 2
    text = extract_chapter(raw, n)
    reason = None
    if not text:
        reason = "출력에 장 제목이 없다"
    else:
        c, mech = mech_check(text, n)
        hard = [m for m in mech if m[0] in ("truncated", "meta", "format", "line", "copy")]
        if hard:
            reason = "기계 검사에서 불합격：" + " / ".join(d for _, d in hard)
    if reason:
        hist.append({"model": used, "result": reason, "note": note[:200]})
        save(p)
        log(f"RESCUE CH{nn(n)} ({used}) fail: {reason}")
        print(f"NG: {used}：{reason}. 다른 모델을 --model 로 고를 수 있습니다.")
        return 2
    wr(P("work", f"ch{nn(n)}_draft.md"), text)
    wr(P("work", f"ch{nn(n)}_draft_rescue_r{k}.md"), text)
    hist.append({"model": used, "result": "심사 대기", "note": note[:200]})
    ch.update({"state": "drafted", "rescued": True, "parse_retries": 0, "attempts": ch["attempts"] + 1})
    ch.setdefault("warnings", []).append(f"상위 모델（{used}）로 지적 부분을 수정")
    p["escalation"] = None
    p["rescues"] = p.get("rescues", 0) + 1
    save(p)
    log(f"RESCUE CH{nn(n)} by {used} → review once")
    print(f"OK: 제{n}장을 {used} 가 고쳤습니다（{c}자）→ 심사를 한 번만 거칩니다. 통과하지 못하면 다른 모델로 한 번 더 하거나 사람의 판단（resolve --accept）.")
    return 0


PROOF_KINDS = ("오탈자", "표현", "연속성", "설정")


def cmd_proofread(args):
    """상위 모델에 의한 전문 교정. 교정안은 JSON 으로 내기만 하고 원고는 바꾸지 않는다（적용하는 것은 tools/proofread_apply.py）."""
    targets = [int(x) for x in args if x.isdigit()] or list(range(1, N_CH + 1))
    out = P("output", "proofread_edits.json")
    data = json.loads(rd(out)) if out.exists() else {"edits": []}
    keep = [e for e in data["edits"] if e.get("chapter") not in targets]
    protected = CFG.get("proofread", {}).get("protected_words", [])
    added, rejected = [], []
    for n in targets:
        src = proof_source(n)
        if not src.exists():
            print(f"제{n}장: 원고가 없습니다")
            continue
        text = rd(src)
        pk = build_packet("proofread", n)
        try:
            raw = call(pk, P("work", f"proofread_ch{nn(n)}.out"), "premium")
        except Escalate as e:
            print(f"ESCALATE [{e.code}] {e.detail}")
            return 2
        j = extract_json(raw) or {}
        for e in j.get("edits", []):
            b, a = str(e.get("before", "")), str(e.get("after", ""))
            why = None
            if not b or b == a:
                why = "원문이 비었거나, 수정이 같음"
            elif text.count(b) != 1:
                why = f"원문이 원고에 {text.count(b)}곳（1곳이 아니면 적용하지 않음）"
            elif len(b) > 160:
                why = "원문이 너무 길다（국소 수정이 아님）"
            elif any(w in b and w not in a for w in protected):
                why = "보호어를 지우고 있다"
            rec = {"chapter": n, "before": b, "after": a, "kind": e.get("kind") if e.get("kind") in PROOF_KINDS else "표현",
                   "reason": str(e.get("reason", ""))[:200], "enabled": why is None}
            if why:
                rec["rejected"] = why
                rejected.append(rec)
            added.append(rec)
        print(f"제{n}장: 교정안 {len(j.get('edits', []))}건（기계로 기각 {sum(1 for r in added if r['chapter'] == n and 'rejected' in r)}건）")
    data["edits"] = sorted(keep + added, key=lambda e: e["chapter"])
    data["source"] = "premium"
    wr(out, json.dumps(data, ensure_ascii=False, indent=1))
    log(f"PROOFREAD {targets}: {len(added)} edits, {len(rejected)} rejected")
    print("output/proofread_edits.json 에 저장. 사람이 enabled 를 확인한 다음 python3 tools/proofread_apply.py → epub --proofread")
    return 0


def cmd_redesign(args):
    p = load()
    if p["phase"] not in ("design", "canon_hold"):
        print(f"REFUSED: 지금은 {p['phase']} 입니다. 재설계는 설계 중이거나 승인 대기일 때만 됩니다.")
        return 2
    if "--feedback-file" in args:
        text = rd(Path(args[args.index("--feedback-file") + 1]))
    elif "--feedback" in args:
        text = args[args.index("--feedback") + 1]
    else:
        print("usage: novelctl.py redesign (--feedback TEXT | --feedback-file F)")
        return 1
    if not CANON.exists():
        print("REFUSED: canon/CANON.md 가 아직 없습니다.")
        return 2
    wr(P("work", "canon_feedback.md"), "- 【사람의 지시】" + text.strip().replace("\n", "\n  ") + "\n")
    p.update({"phase": "design", "design_state": "draft", "design_attempts": 0, "canon_sha256": None, "escalation": None})
    save(p)
    log("REDESIGN by human: " + text.strip()[:200])
    print("OK: 사람의 지시를 수정 지시로 삼아 CANON 설계를 다시 합니다. start 로 계속합니다.")
    return 0


NEXT = {
    "canon_hold": ("approve", "canon/CANON.md 를 사람이 확인 → approve-canon（또는 redesign / rescue canon）"),
    "done": ("done", "완성. output/final.md（필요하면 proofread → proofread_apply → epub --proofread）"),
}


def status_dict():
    p = load()
    d = {"phase": p["phase"], "current": p["current"], "canon_locked": bool(p["canon_sha256"]),
         "design": {"state": p["design_state"], "attempts": p["design_attempts"], "max": CFG["max_design_attempts"]},
         "chapters": [], "slots": {}, "escalation": p.get("escalation"), "rescues": p.get("rescues", 0),
         "running": bool(lock_owner()), "inflight": None, "usage": usage_summary(),
         "budget_usd": CFG.get("budget_usd"), "premium_model": premium_model(), "canon_rescues": p.get("canon_rescues", []), "canon_fp": sha(CANON)[:8] if CANON.exists() else None}
    for k, v in p["chapters"].items():
        f = P("chapters", f"ch{nn(k)}.md")
        d["chapters"].append({"n": int(k), "state": v["state"], "attempts": v["attempts"],
                              "chars": count_chars(chapter_body(rd(f))) if f.exists() else 0,
                              "warnings": v.get("warnings", []), "rescued": bool(v.get("rescued")),
                              "rescues": v.get("rescues", [])})
    if LEDGER.exists():
        _, secs = ledger_sections(rd(LEDGER))
        for c in sorted(secs):
            for sid, st in SLOT_RE.findall(secs[c]):
                d["slots"][sid] = {"state": st, "chapter": c}
    inflight = P("state", "inflight.json")
    if inflight.exists():
        try:
            d["inflight"] = json.loads(rd(inflight))
        except ValueError:
            pass
    if p.get("escalation"):
        d["next"] = {"kind": "escalate", "text": p["escalation"]["hint"]}
    elif p["phase"] in NEXT:
        k, t = NEXT[p["phase"]]
        d["next"] = {"kind": k, "text": t}
    elif d["running"]:
        d["next"] = {"kind": "wait", "text": "실행 중. 잠시 기다렸다가 status"}
    else:
        d["next"] = {"kind": "start", "text": "start 로 진행"}
    return d


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    c, a = argv[0], argv[1:]
    if c == "init":
        cmd_init()
    elif c == "status":
        if "--json" in a:
            print(json.dumps(status_dict(), ensure_ascii=False, indent=1))
        else:
            cmd_status()
    elif c == "step":
        with Lock():
            return cmd_step()
    elif c == "start":
        return cmd_start(a)
    elif c == "run":
        with Lock():
            return cmd_run(int(a[a.index("--max") + 1]) if "--max" in a else 200)
    elif c == "packet":
        n = int(a[1]) if len(a) > 1 else None
        print(build_packet(a[0], n))
    elif c == "check":
        n = int(a[0])
        f = P("work", f"ch{nn(n)}_draft.md")
        f = f if f.exists() else P("chapters", f"ch{nn(n)}.md")
        cnt, iss = mech_check(rd(f), n)
        print(f"{f.relative_to(ROOT)}: {cnt}자")
        for t, d in iss:
            print(f"  [{t}] {d}")
        return 0 if not iss else 1
    elif c == "set-budget":
        with Lock():
            return cmd_set_budget(a)
    elif c == "accept-polish":
        with Lock():
            return cmd_accept_polish(a)
    elif c == "approve-canon":
        p = load()
        if not CANON.exists():
            print("canon/CANON.md 가 아직 없습니다. 설계가 끝난 다음 승인하세요.")
            return 2
        h = sha(CANON)[:8]
        if CFG.get("human_approval", True) and os.environ.get("NOVEL_WORKER_APPROVE") == h:
            # novel_worker：사용자가 /novel-approve 로 직접 입력한 지문일 때만 진행자가 이 환경 변수를 붙인다（바탕: studio 의 ‘승인’ 버튼）
            log(f"APPROVED by user fingerprint ({h})")
        elif CFG.get("human_approval", True):
            if not sys.stdin.isatty():
                log("APPROVE refused: not a terminal（에이전트의 승인 시도로 기록）")
                print("REFUSED: approve-canon 은 사람이 터미널에서 하는 조작입니다（에이전트에서는 실행하지 않음）.")
                return 2
            got = input(f"canon/CANON.md 를 읽었습니까. 승인하려면 지문 {h} 를 입력하세요: ").strip()
            if got != h:
                print("REFUSED: 지문이 일치하지 않습니다. 승인하지 않았습니다.")
                return 2
        p["canon_sha256"] = sha(CANON)
        p["phase"] = "chapters"
        p["escalation"] = None  # 설계가 수렴하지 않은 채 사람이 괜찮다고 한 경우도 여기서 멈춤을 푼다
        save(p)
        log(f"APPROVE canon {h}")
        print("CANON을 승인하고 다시 잠갔습니다. `step` 으로 제1장부터 진행합니다.")
    elif c == "resolve":
        cmd_resolve(a)
    elif c == "reset-chapter":
        cmd_reset(int(a[0]))
    elif c == "assemble":
        cmd_assemble(polished="--polished" in a)
    elif c == "rescue":
        with Lock():
            return cmd_rescue(a)
    elif c == "proofread":
        with Lock():
            return cmd_proofread(a)
    elif c == "redesign":
        return cmd_redesign(a)
    elif c == "polish":
        with Lock():
            return cmd_polish(a)
    elif c == "cover":
        sys.path.insert(0, str(ROOT / "tools"))
        import make_cover
        return make_cover.main(a)
    elif c == "epub":
        sys.path.insert(0, str(ROOT / "tools"))
        import make_epub
        make_epub.build("proofread" if "--proofread" in a else "final")
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
