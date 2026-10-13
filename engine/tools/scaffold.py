#!/usr/bin/env python3
"""scaffold — brief.json(구조화한 BRIEF)으로 novelctl 이 바로 돌아가는 작품 폴더를 만든다. 표준 라이브러리만.

  python3 engine/tools/scaffold.py lint  brief.json                 BRIEF 의 기계 검사(JSON 으로 결과를 돌려준다)
  python3 engine/tools/scaffold.py render brief.json                BRIEF.md 를 표준 출력으로
  python3 engine/tools/scaffold.py upgrade works/<slug>             만들던 작품의 tools/ 만 최신으로 바꾼다(표지 등 새 기능을 쓰기 위해)
  python3 engine/tools/scaffold.py create brief.json works/<slug> [--roles roles.json] [--budget 3.0]
                                                                     작품 폴더를 만든다(engine 을 복사 → BRIEF.md·config.json·
                                                                     CANON 템플릿을 생성 → init → guard seal)

왜 JSON 인가(v1.0):
  지금까지 BRIEF.md(5만 자)·config.json 의 slot_plan·CANON 템플릿·LEDGER 항목을 작품마다 Claude 가 손으로 맞춰 왔다.
  BRIEF 를 JSON 으로 내게 하고 기계로 그려 내면 이 “손 맞춤”이 필요 없어지고, 저렴한 모델로도 BRIEF 를 만들 수 있다.
  어긋남은 lint 가 알려 준다(slot_plan 과 장 구성의 불일치, 회수가 설치보다 앞, 글자 수 범위 등).

brief.json 의 형태(필수는 * ):
{
 "title"*: "가제", "logline"*: "한 줄 요지(결말까지)", "genre"*: "SF", "audience"*: "예상 독자",
 "length"*: {"chapters": 5, "chapter_target": 3500, "chapter_min": 3000, "chapter_max": 4500},
 "narration"*: "서술 인칭과 시점 인물", "ending_type"*: "비터엔딩", "core"*: "이야기의 핵",
 "genre_reason": "왜 이 장르여야만 하는가", "setting"*: "무대(고정하는 점)",
 "characters"*: [{"name": "", "role": "", "fixed": "골격(고정)"}],
 "free": "작가의 재량에 맡기는 것",
 "rules"*: {"style": "문체와 톤", "language": "말씨·표기", "characters": "인물의 규율", "lines": ["넘어서는 안 되는 선"]},
 "chapters"*: [{"n": 1, "title": "가칭", "tension": 2, "reveal": 1, "function": "장의 기능", "hook": "장 끝의 훅", "forbidden": "금지"}],
 "slots"*: [{"id": "F1", "name": "이름", "function": "기능", "plan": [[1, "planted"], [3, "recovered"]]}],
 "fairplay": ["페어플레이 조건"], "ending"*: "결말의 방향", "design_notes": ["설계 주의"],
 "banned": [{"pattern": "정규 표현식", "reason": "이유"}]
}
"""
import datetime
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parent.parent
EV = ("planted", "reinforced", "recovered")
EV_JA = {"planted": "설치", "reinforced": "보강", "recovered": "회수"}
JA_EV = {v: k for k, v in EV_JA.items()}
REQUIRED = ["title", "logline", "genre", "audience", "length", "narration", "ending_type", "core",
            "setting", "characters", "rules", "chapters", "slots", "ending"]


def nn(n):
    return f"{int(n):02d}"


def parse_plan(plan):
    """[[1,"planted"],...] / "1:설치 3:회수" / {"planted":[1]} 어느 것이든 받는다."""
    out = []
    if isinstance(plan, str):
        for c, ev in re.findall(r"(\d+)\s*[:：]?\s*(설치|보강|회수|planted|reinforced|recovered)", plan):
            out.append([int(c), JA_EV.get(ev, ev)])
    elif isinstance(plan, dict):
        for ev, chs in plan.items():
            for c in chs if isinstance(chs, list) else [chs]:
                out.append([int(c), JA_EV.get(ev, ev)])
    else:
        for item in plan or []:
            c, ev = item[0], item[1]
            out.append([int(c), JA_EV.get(ev, ev)])
    return sorted(out, key=lambda x: (x[0], EV.index(x[1]) if x[1] in EV else 9))


def lint(b):
    """반환값：{"ok": bool, "errors": [...], "warnings": [...]}. errors 가 있으면 create 하지 않는다."""
    E, W = [], []
    for k in REQUIRED:
        v = b.get(k)
        if v in (None, "", [], {}):
            E.append(f"{k} 항목이 없습니다")
    L = b.get("length") or {}
    n_ch = int(L.get("chapters") or 0)
    t, lo, hi = (int(L.get(k) or 0) for k in ("chapter_target", "chapter_min", "chapter_max"))
    if n_ch < 1:
        E.append("length.chapters가 1 이상이 아닙니다")
    if n_ch > 60:
        E.append("length.chapters가 60을 넘습니다（장 구성을 다시 검토할 것）")
    if not (0 < lo <= t <= hi):
        E.append(f"글자 수 범위가 올바르지 않습니다（min {lo} ≤ target {t} ≤ max {hi} 로 할 것）")
    chs = b.get("chapters") or []
    if len(chs) != n_ch:
        E.append(f"chapters가 {len(chs)}건으로, length.chapters（{n_ch}）와 맞지 않습니다")
    for i, c in enumerate(chs, 1):
        if int(c.get("n", 0)) != i:
            E.append(f"chapters 의 {i}번째 n 이 {c.get('n')} 입니다（1부터 차례로）")
        if not str(c.get("function", "")).strip():
            E.append(f"제{i}장의 function（기능）이 비어 있습니다")
        for k in ("tension", "reveal"):
            v = c.get(k)
            if v is not None and not (0 <= int(v) <= 5):
                W.append(f"제{i}장의 {k} 는 0〜5 로 쓸 것")
    ids = set()
    for s in b.get("slots") or []:
        sid = str(s.get("id", ""))
        if not re.fullmatch(r"[A-Z]\d", sid):
            E.append(f"슬롯 ID “{sid}”는 영문 대문자 1자＋숫자 1자로 할 것（예 F1）")
            continue
        if sid in ids:
            E.append(f"슬롯 ID {sid} 가 중복됩니다")
        ids.add(sid)
        if not str(s.get("name", "")).strip():
            E.append(f"{sid} 의 name 이 비어 있습니다")
        plan = parse_plan(s.get("plan"))
        if not plan:
            E.append(f"{sid} 의 plan（설치·보강·회수의 장）이 비어 있습니다")
            continue
        for c, ev in plan:
            if ev not in EV:
                E.append(f"{sid} 의 plan 에 알 수 없는 조작 {ev}")
            if not 1 <= c <= max(n_ch, 1):
                E.append(f"{sid} 의 plan 의 장 {c} 가 범위 밖（1〜{n_ch}）")
        pl = [c for c, ev in plan if ev == "planted"]
        rc = [c for c, ev in plan if ev == "recovered"]
        if len(pl) != 1:
            E.append(f"{sid} 는 설치（planted）가 정확히 1회여야 함（현재 {len(pl)}회）")
        if len(rc) > 1:
            E.append(f"{sid} 의 회수（recovered）가 {len(rc)}회 있습니다（1회까지）")
        if pl and rc and rc[0] <= pl[0]:
            E.append(f"{sid} 의 회수（제{rc[0]}장）가 설치（제{pl[0]}장）보다 앞이거나 같은 장입니다")
        if pl and any(c < pl[0] for c, ev in plan if ev == "reinforced"):
            E.append(f"{sid} 의 보강이 설치보다 앞 장에 있습니다")
        if not rc:
            W.append(f"{sid} 는 회수되지 않습니다（이월）. 의도한 대로라면 문제 없음")
    if not ids:
        E.append("복선 슬롯이 하나도 없습니다")
    # 복선 과밀 경고(v1.1：단편에서 마지막 장에 회수가 몰려 상위 모델로도 다 고치지 못한 실제 사례에서)
    if n_ch and ids:
        ops = {}
        rec_last = 0
        for s in b.get("slots") or []:
            for c, ev in parse_plan(s.get("plan")):
                ops[c] = ops.get(c, 0) + 1
                if ev == "recovered" and c == n_ch:
                    rec_last += 1
        if rec_last >= 3:
            W.append(f"마지막 장에서 회수가 {rec_last}건 있습니다. 마지막 장이 과밀해지기 쉽습니다（2건까지로 하고 나머지는 앞 장에서 회수할 것）")
        per_ch = max(2, t // 1000) if t else 2
        for c, k in sorted(ops.items()):
            if k > per_ch + 1:
                W.append(f"제{c}장에서 복선 조작이 {k}건 있습니다（1장 {t}자라면 {per_ch}건 안팎이 기준）. 장을 늘리거나 슬롯을 줄일 것")
    for bn in b.get("banned") or []:
        try:
            re.compile(bn.get("pattern", ""))
        except re.error as e:
            E.append(f"banned 의 정규 표현식이 올바르지 않음：{bn.get('pattern')}（{e}）")
    total = n_ch * t
    if total and total > 300000:
        W.append(f"총 글자 수 기준이 {total}자입니다. 너무 길지 않은지 확인할 것")
    if len(json.dumps(b, ensure_ascii=False)) > 60000:
        W.append("BRIEF 가 6만 자를 넘습니다. 모델의 문맥을 압박합니다")
    return {"ok": not E, "errors": E, "warnings": W}


def chapter_slot_line(b, n):
    ev = []
    for s in b["slots"]:
        for c, e in parse_plan(s.get("plan")):
            if c == n:
                ev.append(f"{s['id']} {EV_JA.get(e, e)}")
    return "／".join(ev) if ev else "없음"


def render(b):
    L = b["length"]
    n_ch = int(L["chapters"])
    rules = b.get("rules") or {}
    lines = rules.get("lines") or []
    out = [f"# BRIEF — 집필 지시서（『{b['title']}』）", "",
           "> 이 지시서는 이야기의 “구조”를 정합니다. 인물의 세부·장면·대사·복선을 보여 주는 방식은 작가가 CANON（설정서）에서 창작하세요.",
           "> 여기에 쓰여 있지 않은 것은 자유입니다. 여기에 쓰여 있는 것은 지켜 주세요.",
           f"> 생성：novel_worker／scaffold（{datetime.date.today().isoformat()}）", "", "---", "",
           "## 0. 작가의 재량과 그 책임", "",
           "- 쓰여 있지 않은 것은 작가의 재량입니다. 뻔한 전개로 만들지 마세요.",
           "- 덧붙인 것（BRIEF 에 없는 인물·장소·도구·풍습·유래）은 CANON 제13절에 한 건당 한 줄로 신고하고, 이야기 안에서 개연성을 갖추세요.",
           "- 이름이 있는 인물에게는 행동 원리（원하는 것／두려워하는 것／그 행동을 하는 이유）를 갖게 하세요. 장기 말처럼 소모하지 말 것."]
    if b.get("free"):
        out += [f"- 이 작품에서 특히 맡기는 것：{b['free']}"]
    out += ["", "---", "", "## 1. 작품 요건", "", "| 항목 | 요건 |", "|---|---|",
            f"| 가제 | {b['title']} |",
            f"| 한 줄 요지 | {b['logline']} |",
            f"| 장르 | {b['genre']} |",
            f"| 예상 독자 | {b['audience']} |",
            f"| 길이 | 전 {n_ch}장 × 각 약 {L['chapter_target']}자（{L['chapter_min']}〜{L['chapter_max']}자）, 기준 {n_ch * int(L['chapter_target'])}자 |",
            f"| 서술 | {b['narration']} |",
            f"| 결말 유형 | {b['ending_type']} |",
            f"| 핵 | {b['core']} |", ""]
    if b.get("genre_reason"):
        out += ["### 1-1. 장르의 필연（왜 이 장르여야만 하는가）", "", b["genre_reason"], ""]
    out += ["### 1-2. 무대（고정하는 점）", "", b["setting"], "", "### 1-3. 인물（이름과 골격은 고정. 세부는 CANON 에서 작가가 덧붙여도 된다）", ""]
    for c in b["characters"]:
        out.append(f"- **{c.get('name', '')}**（{c.get('role', '')}）：{c.get('fixed', '')}")
    out += ["", "---", "", "## 2. 공통 집필 규칙（모든 장에서 반드시 지킨다）", "", "<!-- RULES:BEGIN -->",
            "### 2-1. 서술과 시점", f"- {b['narration']}", "- 시점 인물이 보고 듣지 않은 것은 쓰지 않는다. 다른 사람의 내면을 지문으로 단정하지 않는다.", "",
            "### 2-2. 문체와 톤", f"- {rules.get('style') or '작가가 CANON 제10절에서 정한 문체를 끝까지 바꾸지 않는다.'}", "",
            "### 2-3. 말씨·표기", f"- {rules.get('language') or '대화는 “ ”, 속마음은 ‘ ’, 글로 쓰인 글자는 『 』.'}",
            "- 한자·일본어 가나·로마자를 섞지 않는다. 실재하는 브랜드명·상품명·곡명은 쓰지 않는다. 한국어 맞춤법과 띄어쓰기를 지킨다.", "",
            "### 2-4. 사실의 일관성",
            "- CANON 의 불변 조건·시간표·소품과 수치·세계의 전제, 그리고 LEDGER 의 FACT 와 절대 모순되지 않는다.",
            "- 앞 장의 마지막 장면에서 자연스럽게 잇는다. 장 첫머리에서 “언제, 어디서, 누가”를 보여 준다.", "",
            "### 2-5. 복선의 규율",
            "- 각 장에서 지정된 복선의 “설치／보강／회수”를 반드시 한다. 회수 예정 장보다 앞에서 내용을 밝히지 않는다（암시는 좋다）.",
            "- 본문에 “복선”, “슬롯”, “CANON”이나 슬롯 ID（" + ", ".join(s["id"] for s in b["slots"]) + "）같은 관리 용어를 절대 쓰지 않는다.", "",
            "### 2-6. 인물의 규율", f"- {rules.get('characters') or '이름이 있는 인물은 CANON 제4절의 행동 원리에 따라 움직인다.'}", "",
            "### 2-7. 넘어서는 안 되는 선（아무리 재미있어도 쓰지 않는다）"]
    out += [f"- {x}" for x in lines] or ["- （특별히 없음）"]
    out += ["", "### 2-8. 출력 형식",
            "- 첫 줄에 `# 제N장 장 제목`（N은 반각 숫자）. 둘째 줄부터 본문.",
            "- 장면 전환은 빈 줄 다음에 `＊`만 있는 줄을 둔다.",
            "- 본문 이외（머리말, 후기, 해설, 요약, 글머리표, 코드 블록）는 일절 쓰지 않는다. 장의 마지막은 반드시 완결된 문장으로 끝낸다.",
            "<!-- RULES:END -->", "", "---", "", f"## 3. 장 구성（{n_ch}장）", "",
            "각 장의 “기능”은 필수입니다. 장면·사건의 내용은 작가가 정합니다.", "",
            "| 장 | 가칭 | 목표 글자 수 | 긴장도(1-5) | 밝히는 정도(0-5) |", "|---|---|---|---|---|"]
    for c in b["chapters"]:
        out.append(f"| {c['n']} | {c.get('title', '')} | {c.get('target', L['chapter_target'])} | {c.get('tension', 3)} | {c.get('reveal', 2)} |")
    out.append("")
    for c in b["chapters"]:
        n = int(c["n"])
        out += [f"<!-- ROLE:{nn(n)} -->", f"**제{n}장 {c.get('title', '')}**", f"- 기능：{c['function']}",
                f"- 복선：{chapter_slot_line(b, n)}"]
        if c.get("hook"):
            out.append(f"- 장 끝의 훅：{c['hook']}")
        if c.get("forbidden"):
            out.append(f"- 금지：{c['forbidden']}")
        out += [f"<!-- /ROLE:{nn(n)} -->", ""]
    out += ["---", "", "## 4. 복선 슬롯", "", "슬롯은 “기능”만 정의합니다. **내용（무엇을, 어떻게 보여 줄지）은 작가가 창작해 CANON 제7절에 기입하세요.**", "",
            "### 4-1. 슬롯 정의", "", "| ID | 이름 | 기능 |", "|---|---|---|"]
    for s in b["slots"]:
        out.append(f"| {s['id']} | {s['name']} | {s.get('function', '')} |")
    out += ["", "### 4-2. 배치표（장 지정）", "", "| ID | 설치 | 보강 | 회수 |", "|---|---|---|---|"]
    for s in b["slots"]:
        pl = parse_plan(s["plan"])
        col = lambda e: "·".join(str(c) for c, x in pl if x == e) or "—"
        out.append(f"| {s['id']} | {col('planted')} | {col('reinforced')} | {col('recovered')} |")
    if b.get("fairplay"):
        out += ["", "### 4-3. 페어플레이 조건"] + [f"- {x}" for x in b["fairplay"]]
    out += ["", "---", "", "## 5. 결말의 방향", "", b["ending"] if isinstance(b["ending"], str) else "\n".join(f"- {x}" for x in b["ending"]), "",
            "---", "", "## 6. 제0단계：설계（본문을 쓰기 전에）", "",
            "본문을 쓰기 전에 CANON 템플릿의 모든 항목을 채워 **CANON（설정서）** 을 만듭니다. CANON 은 확정 후 바꿀 수 없습니다.", ""]
    notes = b.get("design_notes") or []
    out += [f"{i}. {x}" for i, x in enumerate(notes, 1)] or ["1. 이야기의 장치와 결말부터 정하고, 장 카드는 거기서 거꾸로 계산한다."]
    out += [f"{len(notes) + 1}. 세계의 전제（소리·불빛·거리·시각·몸·세는 법·실내외）를 CANON 제6절에서 정한다.",
            f"{len(notes) + 2}. 본문에서 쓰는 고유 명사·수치·날짜는 모두 CANON 에서 한 번만 고정한다.", ""]
    return "\n".join(out)


def canon_template(b, canon_max):
    t = (ENGINE / "templates" / "CANON_TEMPLATE.md").read_text(encoding="utf-8")
    slots = []
    for s in b["slots"]:
        parts = [f"{s['id']}（{s['name']}）내용：（기입）"]
        for c, e in parse_plan(s["plan"]):
            parts.append(f"{EV_JA[e]}(제{c}장)：（기입）")
        slots.append("- " + "／".join(parts))
    cards = []
    for c in b["chapters"]:
        n = int(c["n"])
        cards += [f"<!-- CARD:{nn(n)} -->", f"### 제{n}장 “（기입）”", "- 시기와 장소：（기입）", "- 시점 인물：（기입）",
                  "- 장면（2〜4）：（기입）", "- 복선 처리：（기입）", "- 장 끝의 훅：（기입）", "- 아직 밝히지 않는 것：（기입）",
                  f"<!-- /CARD:{nn(n)} -->", ""]
    return (t.replace("{{SLOTS}}", "\n".join(slots)).replace("{{CARDS}}", "\n".join(cards).rstrip())
             .replace("{{CANON_MAX}}", str(canon_max)))


def default_config(b, roles=None, budget=None):
    L = b["length"]
    n_ch = int(L["chapters"])
    cfg = json.loads((ENGINE / "config.default.json").read_text(encoding="utf-8"))
    cfg["chapters"] = n_ch
    cfg["chapter_chars"] = {"target": int(L["chapter_target"]), "min": int(L["chapter_min"]), "max": int(L["chapter_max"])}
    cfg["total_chars"] = {"min": int(n_ch * int(L["chapter_min"]) * 0.95), "max": int(n_ch * int(L["chapter_max"]))}
    cfg["canon_max_chars"] = max(12000, min(40000, 6000 + n_ch * 1500))
    cfg["slot_names"] = {s["id"]: s["name"] for s in b["slots"]}
    cfg["slot_plan"] = {s["id"]: parse_plan(s["plan"]) for s in b["slots"]}
    cfg["audience"] = b.get("audience", "")
    cfg["guard"]["banned"] = list(b.get("banned") or [])
    cfg["publish"]["fallback_title"] = b["title"]
    if (ENGINE.parent / "models.json").exists():  # novel_worker：별칭표의 위치(작품 폴더를 NOVEL_WORKS_DIR 로 밖에 둘 때를 위해)
        cfg["llm"]["models_file"] = str((ENGINE.parent / "models.json").resolve())
    for role, spec in (roles or {}).items():
        if role in cfg["llm"]["profiles"] and spec:
            if isinstance(spec, str):
                spec = {"model": spec}
            cfg["llm"]["profiles"][role].update({k: v for k, v in spec.items() if v not in (None, "")})
    if budget:
        cfg["budget_usd"] = float(budget)
    return cfg


def create(b, dest, roles=None, budget=None, extra_top=()):
    res = lint(b)
    if not res["ok"]:
        return {"ok": False, **res}
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    if (dest / "state" / "progress.json").exists():
        return {"ok": False, "errors": [f"{dest} 는 이미 진행 중인 작품입니다（state/progress.json 있음）"], "warnings": []}
    for d in ("tools", "prompts"):
        if (dest / d).exists():
            shutil.rmtree(dest / d)
        shutil.copytree(ENGINE / d, dest / d, ignore=shutil.ignore_patterns("__pycache__", "scaffold.py"))
    (dest / "templates").mkdir(exist_ok=True)
    shutil.copy(ENGINE / "templates" / "LEDGER_TEMPLATE.md", dest / "templates" / "LEDGER_TEMPLATE.md")
    shutil.copy(ENGINE / "AGENTS.md", dest / "AGENTS.md")
    cfg = default_config(b, roles, budget)
    cfg["guard"]["allowed_top"] = sorted(set(cfg["guard"]["allowed_top"]) | set(extra_top))
    (dest / "templates" / "CANON_TEMPLATE.md").write_text(canon_template(b, cfg["canon_max_chars"]), encoding="utf-8")
    (dest / "BRIEF.md").write_text(render(b), encoding="utf-8")
    (dest / "brief.json").write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    (dest / "config.json").write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    py = sys.executable or "python3"
    r1 = subprocess.run([py, "tools/novelctl.py", "init"], cwd=dest, capture_output=True, text=True)
    r2 = subprocess.run([py, "tools/guard.py", "seal"], cwd=dest, capture_output=True, text=True)
    sha = hashlib.sha256((dest / "BRIEF.md").read_bytes()).hexdigest()[:12]
    return {"ok": r1.returncode == 0 and r2.returncode == 0, "errors": [x for x in (r1.stderr.strip(), r2.stderr.strip()) if x],
            "warnings": res["warnings"], "path": str(dest), "brief_sha": sha,
            "log": (r1.stdout + r2.stdout).strip()}


def upgrade(dest):
    """만들던 작품의 tools/ 만 현재 engine 의 판으로 바꿔 넣는다(프롬프트·템플릿·BRIEF·CANON 은 바꾸지 않는다).
    보호 파일의 지문을 다시 뜨고 notes/INTERVENTIONS.md 에 기록한다. 사람의 조작으로 한다."""
    dest = Path(dest)
    if not (dest / "tools" / "novelctl.py").exists():
        return {"ok": False, "errors": [f"{dest} 는 작품 폴더가 아닙니다"]}
    changed = []
    for f in sorted((ENGINE / "tools").glob("*.py")):
        if f.name == "scaffold.py":
            continue
        old = dest / "tools" / f.name
        if not old.exists() or old.read_bytes() != f.read_bytes():
            shutil.copy(f, old)
            changed.append(f.name)
    if changed:
        py = sys.executable or "python3"
        subprocess.run([py, "tools/guard.py", "seal"], cwd=dest, capture_output=True, text=True)
        (dest / "notes").mkdir(exist_ok=True)
        with open(dest / "notes" / "INTERVENTIONS.md", "a", encoding="utf-8") as fp:
            fp.write(f"- {datetime.datetime.now().isoformat(timespec='seconds')} 도구를 최신으로 바꿨다（tools/ 의 {', '.join(changed)}）. "
                     "프롬프트·템플릿·BRIEF·CANON 은 바꾸지 않았다. 보호 파일의 지문을 다시 떴다.\n")
    return {"ok": True, "changed": changed}


def main(argv):
    if len(argv) >= 2 and argv[0] == "upgrade":
        r = upgrade(argv[1])
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 2
    if len(argv) < 2 or argv[0] not in ("lint", "render", "create"):
        print(__doc__)
        return 1
    b = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    if argv[0] == "lint":
        r = lint(b)
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return 0 if r["ok"] else 2
    if argv[0] == "render":
        r = lint(b)
        if not r["ok"]:
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 2
        print(render(b))
        return 0
    roles = json.loads(Path(argv[argv.index("--roles") + 1]).read_text(encoding="utf-8")) if "--roles" in argv else None
    budget = argv[argv.index("--budget") + 1] if "--budget" in argv else None
    r = create(b, argv[2], roles, budget)
    print(json.dumps(r, ensure_ascii=False, indent=1))
    return 0 if r["ok"] else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
