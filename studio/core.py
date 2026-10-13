"""core — studio의 공정（테마 → 제안 → 분류 → BRIEF → Fix → CANON → 집필 → 마무리）.

웹 화면（server.py）과 하네스용 CLI（cli.py）가 모두 이 모듈만 부른다.
상태는 모두 works/<slug>/studio.json과 그 아래 파일에 있으며, 대화나 화면의 기억에 의존하지 않는다.
Fix 뒤에는 작품 폴더 자체가 engine（tools/novelctl.py）의 작업 장소가 된다.
"""
import concurrent.futures as cf
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WORKS = Path(os.environ.get("NOVEL_WORKS_DIR", REPO / "works"))
ENGINE = REPO / "engine"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ENGINE / "tools"))
import llm  # noqa: E402
import similarity  # noqa: E402
import scaffold  # noqa: E402

PROPOSAL_FIELDS = ["kind", "genre", "title", "setting", "synopsis", "highlight", "appeal", "must_keep", "genre_reason"]
LOCKS = {}
LOCKS_GUARD = threading.Lock()
SETTINGS_FILE = HERE / "settings.json"
# novel_worker：등록된 API 키에 따라 모델 구성(모드)을 고른다. 모델은 models.json 의 별칭.
#   solo = Anthropic 키만(최소 구성)   duo = Anthropic + OpenAI(권장·기본 전제)   all = 다섯 회사 모두
# roles 의 값은 {"model", "fallback_models"} — 예비 모델도 그 모드에서 키가 있는 업체만 쓴다.
MODE_PRESETS = {
    "solo": {
        "propose_models": ["sonnet"], "propose_n": 3,   # 한 모델에게 3안(분류에는 2안 이상 필요)
        "skeleton_model": "haiku", "brief_model": "sonnet",
        "roles": {"writer": {"model": "sonnet", "fallback_models": ["opus"]},
                  "reviewer": {"model": "sonnet", "fallback_models": ["opus"]},
                  "polisher": {"model": "sonnet", "fallback_models": []},
                  "premium": {"model": "sonnet", "fallback_models": []}},
    },
    "duo": {
        "propose_models": ["sonnet", "gpt"], "propose_n": 2,   # 두 회사가 2안씩 = 4안
        "skeleton_model": "gpt", "brief_model": "sonnet",
        "roles": {"writer": {"model": "sonnet", "fallback_models": ["gpt"]},     # 쓰는 모델과
                  "reviewer": {"model": "gpt", "fallback_models": ["sonnet"]},   # 심사하는 모델을 다른 회사로
                  "polisher": {"model": "sonnet", "fallback_models": ["gpt"]},
                  "premium": {"model": "sonnet", "fallback_models": []}},
    },
    "all": {
        "propose_models": ["sonnet", "gpt", "glm", "qwen", "kimi"], "propose_n": 1,
        "skeleton_model": "glm", "brief_model": "sonnet",
        "roles": {"writer": {"model": "sonnet", "fallback_models": ["kimi"]},
                  "reviewer": {"model": "gpt", "fallback_models": ["glm"]},
                  "polisher": {"model": "kimi", "fallback_models": ["qwen"]},
                  "premium": {"model": "sonnet", "fallback_models": []}},
    },
}
MODE_NEEDS = {"solo": {"anthropic"}, "duo": {"anthropic", "openai"}, "all": {"anthropic", "openai", "glm", "qwen", "kimi"}}
MODE_LABEL = {"solo": "Anthropic 하나(최소)", "duo": "Anthropic + GPT(권장)", "all": "다섯 회사 모두"}
EXTRA_PROPOSERS = {"glm": "glm", "qwen": "qwen", "kimi": "kimi"}  # duo 에 키가 더 있으면 제안에만 더한다
COMMON_SETTINGS = {
    "threshold": 0.5,
    # 표지 그림은 중계기로 만들 수 없다(이미지 생성 창구가 없음). 기본은 그림 없이 글자·색만으로 된 표지(--model none)
    "cover_models": [],
}
DEFAULT_SETTINGS = {**COMMON_SETTINGS, **MODE_PRESETS["all"], "mode": "all"}  # 중계기가 아닐 때(가짜 모델 시험 등)


def choose_mode(providers):
    """키가 있는 업체 집합 → 모드. Anthropic 키가 없으면 None(최소 요건 미달)."""
    if "anthropic" not in providers:
        return None
    for mode in ("all", "duo", "solo"):
        if MODE_NEEDS[mode] <= providers:
            return mode
    return "solo"


def key_status():
    """{bridge, providers, mode, auto, reason, error}. 키 값은 다루지 않고, 중계기가 알려 주는 유무만 본다."""
    raw = ((os.environ.get("NOVEL_KEY_MODE") or llm._dotenv("NOVEL_KEY_MODE") or "").strip()  # .env 에서는 이 이름 하나만 읽는다
           or str(_settings_file().get("mode", "auto")))
    want = raw.lower()
    note = "" if want in MODE_PRESETS or want in ("", "auto") else f"（알 수 없는 모드 ‘{raw}’ — solo·duo·all 중 하나. 자동 선택으로 진행）"
    providers, error = llm.bridge_providers()
    st = {"bridge": providers is not None or bool(error), "providers": sorted(providers or []), "requested": raw, "error": error}
    if providers is None:
        st.update(mode="all" if not error else None, auto=False,
                  reason=("중계기 모드가 아님(가짜 모델·다른 창구) — 기본 구성" if not error else "중계기가 꺼져 있습니다. `./nw --bridge` 로 켜 주세요") + note)
        return st
    auto = choose_mode(providers)
    if want in MODE_PRESETS:
        missing = sorted(MODE_NEEDS[want] - providers)
        st.update(mode=want, auto=False, reason=f"설정으로 {want} 지정" + (f" — 키 없음: {', '.join(missing)}（그 회사 모델은 빼고 씁니다）" if missing else ""))
    elif auto is None:
        st.update(mode=None, auto=True, reason="Anthropic 키(ANTHROPIC_API_KEY)가 없습니다. 최소 이 키 하나는 .env 에 필요합니다" + note)
    else:
        st.update(mode=auto, auto=True, reason=f"키가 있는 업체로 자동 선택: {MODE_LABEL[auto]}" + note)
    return st


PROVIDER_PREFIX = [(r"^claude-", "anthropic"), (r"^(gpt|o\d|chatgpt)", "openai"), (r"^glm", "glm"), (r"^(qwen|qwq)", "qwen"),
                   (r"^(kimi|moonshot)", "kimi"), (r"^deepseek", "deepseek")]


def provider_of(model):
    """별칭(models.json 의 provider) 또는 모델 ID 앞부분 → 업체 이름(중계기 health 의 이름과 같음). 모르면 None."""
    spec = llm.wire.models_table().get((model or "").lower())
    if spec and spec.get("provider"):
        return spec["provider"]
    mid = llm.wire.resolve(model).split("/")[-1].lower()
    return next((prov for pat, prov in PROVIDER_PREFIX if re.match(pat, mid)), None)


def normalize_roles(roles, base=None):
    """역할 지정을 {역할: {"model", "fallback_models"}} 로. base(모드 기본값) 위에 역할별로 덮어쓴다.
    문자열 "sonnet" 은 {"model": "sonnet", "fallback_models": []} — 예비 모델이 기본 설정(all 모드)에서 새어 들어오지 않게."""
    out = {r: dict(v) for r, v in (base or {}).items()}
    for r, v in (roles or {}).items():
        if isinstance(v, str):
            out[r] = {"model": v, "fallback_models": []}
        elif isinstance(v, dict) and v.get("model"):
            out[r] = {"model": v["model"], "fallback_models": list(v.get("fallback_models") or [])}
    return out


def check_models(s, providers):
    """키가 없는 회사의 모델을 거른다. 주 모델(역할·분류·BRIEF·제안 전체)이 막히면 문제 목록에 넣는다."""
    if providers is None:
        return s, []
    ok = lambda m: provider_of(m) in providers  # noqa: E731
    problems = []
    s = dict(s)
    dropped = [m for m in s["propose_models"] if not ok(m)]
    s["propose_models"] = [m for m in s["propose_models"] if ok(m)]
    if not s["propose_models"]:
        problems.append(f"기획안을 낼 모델이 없습니다（키 없음: {', '.join(dropped)}）")
    for k in ("skeleton_model", "brief_model"):
        if not ok(s[k]):
            problems.append(f"{k} = {s[k]} 의 키({provider_of(s[k]) or '알 수 없는 업체'})가 없습니다")
    roles = {}
    for r, v in s["roles"].items():
        if not ok(v["model"]):
            problems.append(f"{r} 역할 = {v['model']} 의 키({provider_of(v['model']) or '알 수 없는 업체'})가 없습니다")
        roles[r] = {"model": v["model"], "fallback_models": [m for m in v.get("fallback_models") or [] if ok(m)]}
    s["roles"] = roles
    return s, problems


class StudioError(Exception):
    pass


def now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _settings_file():
    return json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) if SETTINGS_FILE.exists() else {}


_KEY_STATUS = None


def settings(ks=None, strict=True):
    """모드 구성 + studio/settings.json(있으면 역할별로 덮어씀) → 키 없는 회사 모델을 거른 결과.
    모드는 NOVEL_KEY_MODE > settings.json 의 mode > auto(키 감지). strict=False 면 문제를 s["problems"] 로 돌려준다(keys 명령용)."""
    global _KEY_STATUS
    if ks is None:
        if _KEY_STATUS is None:
            _KEY_STATUS = key_status()
        ks = _KEY_STATUS
    if ks["mode"] is None:
        if ks is _KEY_STATUS:
            _KEY_STATUS = None  # 실패는 기억하지 않는다(중계기를 켜거나 키를 넣은 뒤 다시 확인)
        raise StudioError(ks["reason"] + (f"（{ks['error']}）" if ks["error"] and ks["error"] not in ks["reason"] else ""))
    preset = MODE_PRESETS[ks["mode"]]
    s = {**COMMON_SETTINGS, **preset, "mode": ks["mode"], "roles": normalize_roles({}, preset["roles"])}
    if ks["mode"] == "duo":  # 키가 더 있는 업체는 제안에만 보탠다(다양성↑, 역할은 그대로)
        extra = [m for prov, m in EXTRA_PROPOSERS.items() if prov in ks["providers"]]
        if extra:
            s["propose_models"] = s["propose_models"] + extra
            s["propose_n"] = 1
    user = _settings_file()
    s.update({k: v for k, v in user.items() if k not in ("mode", "roles")})
    s["roles"] = normalize_roles(user.get("roles"), s["roles"])
    known = set(ks["providers"]) if ks["bridge"] and not ks["error"] else None  # 중계기가 아니면(가짜 모델 시험 등) 거르지 않는다
    s, problems = check_models(s, known)
    s["providers_known"] = sorted(known) if known is not None else None
    if problems and strict:
        raise StudioError("모델 구성 문제：" + "；".join(problems) + "  — `python3 studio/cli.py keys` 로 확인하고 .env 의 키나 studio/settings.json 을 고쳐 주세요")
    s["problems"] = problems
    return s


def lock(slug):
    with LOCKS_GUARD:
        return LOCKS.setdefault(slug, threading.RLock())


def wdir(slug):
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,60}", slug or ""):
        raise StudioError(f"작품 이름 ‘{slug}’은(는) 영문·숫자·-·_ 로 60자 이내로 해 주세요")
    return WORKS / slug


def load(slug):
    f = wdir(slug) / "studio.json"
    if not f.exists():
        raise StudioError(f"작품 {slug}이(가) 없습니다")
    return json.loads(f.read_text(encoding="utf-8"))


def save(slug, st):
    f = wdir(slug) / "studio.json"
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)


def record_cost(slug, stage, rec, note=""):
    d = wdir(slug) / "studio"
    d.mkdir(exist_ok=True)
    with open(d / "usage.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": now(), "stage": stage, "note": note, **rec}, ensure_ascii=False) + "\n")


def costs(slug):
    """studio의 비용（공정별）과 engine의 비용（역할별）."""
    out = {"studio": {}, "engine": {}, "studio_usd": 0.0, "engine_usd": 0.0, "estimated": 0, "unpriced": 0}
    f = wdir(slug) / "studio" / "usage.jsonl"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = out["studio"].setdefault(r["stage"], {"usd": 0.0, "calls": 0})
            b["usd"] += float(r.get("usd") or 0)
            b["calls"] += 1
            out["studio_usd"] += float(r.get("usd") or 0)
            out["estimated"] += 1 if r.get("price") == "estimated" else 0
            out["unpriced"] += 1 if r.get("price") == "unknown" else 0
    g = wdir(slug) / "logs" / "usage.jsonl"
    if g.exists():
        for line in g.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            b = out["engine"].setdefault(r.get("role", "?"), {"usd": 0.0, "calls": 0})
            b["usd"] += float(r.get("cost") or 0)
            b["calls"] += 1
            out["engine_usd"] += float(r.get("cost") or 0)
            out["estimated"] += 1 if r.get("est") else 0
            out["unpriced"] += 1 if r.get("cost") is None and r.get("in") is not None else 0
    for grp in (out["studio"], out["engine"]):
        for b in grp.values():
            b["usd"] = round(b["usd"], 5)
    out["studio_usd"] = round(out["studio_usd"], 5)
    out["engine_usd"] = round(out["engine_usd"], 5)
    out["total_usd"] = round(out["studio_usd"] + out["engine_usd"], 5)
    return out


def prompt(name, **kw):
    t = (HERE / "prompts" / f"{name}.md").read_text(encoding="utf-8")
    for k, v in kw.items():
        t = t.replace("{{" + k + "}}", str(v))  # .format은 JSON의 { } 때문에 깨지므로 쓰지 않는다（Novel_4의 교훈）
    return t


# ---------------------------------------------------------------- 테마
THEME_FIELDS = {"genre": "장르", "theme": "테마·소재", "length": "길이", "audience": "예상 독자", "ending": "결말 유형",
                "must": "꼭 넣고 싶은 것", "must_not": "넣고 싶지 않은 것", "mood": "분위기·참고", "note": "그 밖에"}


def theme_text(theme):
    lines = [f"- {label}：{theme[k]}" for k, label in THEME_FIELDS.items() if str(theme.get(k, "")).strip()]
    ch = theme.get("chapters")
    if ch:
        lines.append(f"- 장 수의 기준：{ch}장（1장 {theme.get('chapter_target', 3000)}자 안팎）")
    return "\n".join(lines) or "- （지정 없음. 자유롭게）"


def create_work(slug, theme):
    d = wdir(slug)
    if (d / "studio.json").exists():
        raise StudioError(f"작품 {slug}은(는) 이미 있습니다")
    if d.exists() and any(d.iterdir()):
        raise StudioError(f"폴더 works/{slug} 에는 이미 무언가 들어 있습니다. 다른 이름으로 해 주세요")
    d.mkdir(parents=True, exist_ok=True)
    st = {"slug": slug, "created": now(), "theme": theme, "theme_version": 1, "rounds": [], "proposals": {},
          "classify": None, "selected": None, "briefs": [], "fixed": None, "next_pid": 1, "log": [f"{now()} 작품을 만듦"]}
    save(slug, st)
    return st


def list_works():
    out = []
    if WORKS.exists():
        for d in sorted(WORKS.iterdir()):
            f = d / "studio.json"
            if f.exists():
                st = json.loads(f.read_text(encoding="utf-8"))
                out.append({"slug": d.name, "title": (st["theme"].get("theme") or "")[:40], "created": st["created"],
                            "stage": stage_of(st, d), "fixed": bool(st.get("fixed"))})
    return out


THEME_DEFAULTS = {"audience": "일반 독자", "chapters": 8, "chapter_target": 3000}  # 화면 폼의 초기값（app.js의 themeForm과 같음）
THEME_LABELS = {**THEME_FIELDS, "chapters": "장의 수", "chapter_target": "1장의 글자 수"}


def theme_diff(old, new):
    """바꾼 항목의 이름（한국어）. 항목이 없는 것과 폼의 초기값은 같다고 본다."""
    def val(t, k):
        v = t.get(k)
        return str(THEME_DEFAULTS.get(k, "") if v in (None, "") else v).strip()
    return [THEME_LABELS[k] for k in THEME_LABELS if val(old, k) != val(new, k)]


def suggest_slug(slug):
    """파생 작품 폴더 이름의 후보（ice-moon → ice-moon-2 → ice-moon-3 …. 아직 쓰이지 않은 것）."""
    base = re.sub(r"-\d+$", "", slug)[:56]
    for i in range(2, 1000):
        cand = f"{base}-{i}"
        if not (WORKS / cand).exists():
            return cand
    return base + "-x"


def derive_work(src, new_slug, theme):
    """테마를 고쳐 새 작품 폴더에서 제안부터 다시 한다（v1.1.1）.
    원래 작품은 전혀 건드리지 않는다（제안·분류·BRIEF·본문·로그도 그대로）. 폴더 이름은 반드시 바꾼다＝덮어쓰기가 일어나지 않는다."""
    s = load(src)
    if (new_slug or "").strip() == src:
        raise StudioError(f"새 작품 폴더 이름은 지금의 ‘{src}’와 다르게 해 주세요")
    if wdir(new_slug).exists():
        raise StudioError(f"작품 폴더 ‘{new_slug}’은(는) 이미 있습니다. 다른 이름으로 해 주세요（후보：{suggest_slug(new_slug)}）")
    st = create_work(new_slug, theme)
    changed = theme_diff(s["theme"], theme)
    st["derived_from"] = {"slug": src, "theme_version": s["theme_version"], "at": now(), "changed": changed}
    st["log"].append(f"{now()} 《{src}》의 테마（판 {s['theme_version']}）를 고쳐 만듦. 바꾼 항목：{'·'.join(changed) or '없음（같은 테마로 다시 냄）'}")
    save(new_slug, st)
    return st


def derived_children(slug):
    """이 작품의 테마에서 파생된 작품（최신순）. 원래 작품의 기록은 고쳐 쓰지 않으므로, 파생된 쪽의 derived_from으로 센다."""
    out = []
    if WORKS.exists():
        for d in WORKS.iterdir():
            f = d / "studio.json"
            if f.exists():
                try:
                    df = json.loads(f.read_text(encoding="utf-8")).get("derived_from") or {}
                except ValueError:
                    continue
                if df.get("slug") == slug:
                    out.append({"slug": d.name, "at": df.get("at"), "changed": df.get("changed", [])})
    return sorted(out, key=lambda x: x["at"] or "", reverse=True)


def change_theme(slug, theme):
    """테마를 바꾼다＝처음부터 다시 한다. 지금까지의 제안·분류·BRIEF는 studio/history/ 로 옮겨 둔다."""
    with lock(slug):
        st = load(slug)
        if st.get("fixed"):
            raise StudioError("Fix가 끝난 작품은 테마를 바꿀 수 없습니다. 새 작품으로 만들어 주세요")
        h = wdir(slug) / "studio" / "history"
        h.mkdir(parents=True, exist_ok=True)
        (h / f"theme_v{st['theme_version']}.json").write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
        st.update({"theme": theme, "theme_version": st["theme_version"] + 1, "rounds": [], "proposals": {}, "classify": None,
                   "selected": None, "briefs": []})
        st["log"].append(f"{now()} 테마를 변경（v{st['theme_version']}）. 이전 테마의 기록은 studio/history/")
        save(slug, st)
        return st


# ---------------------------------------------------------------- 제안
def _check_props(obj, n):
    if not isinstance(obj, dict) or not isinstance(obj.get("proposals"), list) or not obj["proposals"]:
        return "proposals 배열이 없다"
    for i, p in enumerate(obj["proposals"]):
        miss = [f for f in ("title", "synopsis", "setting") if not str(p.get(f, "")).strip()]
        if miss:
            return f"{i + 1}번째 안에 {', '.join(miss)}이(가) 없다"
    return None


def _call_json(slug, stage, model, text, check, temperature=0.7, note="", reasoning=None, max_tokens=16000):
    """서식을 다시 맞추라고 한 번만 요구한다（compe의 규칙）. 비용은 실패까지 포함해 기록한다."""
    last = None
    for attempt in (1, 2):
        try:
            raw, rec = llm.chat(model, text, temperature=temperature, reasoning=reasoning, max_tokens=max_tokens,
                                session=f"novel-studio-{slug}")
        except llm.LLMError as e:
            raise StudioError(f"{model}: {e}")
        record_cost(slug, stage, rec, note)
        obj = llm.extract_json(raw)
        err = check(obj)
        if not err:
            return obj, rec
        last = err
        text = text + f"\n\n# 이전 출력의 문제\n{err}. 지정한 JSON 형식으로만 다시 돌려주세요."
    raise StudioError(f"{model}: 서식에 맞는 JSON이 돌아오지 않는다（{last}）")


def propose(slug, models, n=2, instruction="", mode="new", temperature=0.8, avoid=None):
    """models 각각에 n개씩 안을 내게 한다（병렬）. mode = new | resubmit | rolling.
    rolling：분류에서 ‘수렴’이 된 골격을 ‘피할 골격’으로 넘기고, 그 밖의 안만 요구한다（롤링／합숙 방식）."""
    st = load(slug)
    if st.get("fixed"):
        raise StudioError("이미 Fix되었습니다")
    n = max(1, min(int(n), 4))
    kind_rule = "본선이라고 생각하는 안을 1개 이상, 그리고 완전히 다른 관점의 안을 1개 이상" if n >= 2 else "본선이라고 생각하는 안을 1개"
    extra = []
    if instruction.strip():
        extra.append(f"- 프로듀서의 추가 지시：{instruction.strip()}")
    if mode == "rolling":
        av = avoid if avoid is not None else avoid_list(st)
        if av:
            extra.append("- 다음 골격에 가까운 안은 내지 마세요（이미 여러 번 나온 유형입니다）. 골격（누가 속이나·무엇을 숨기나·어느 층에서 놀라게 하나·무엇이 뒤집히나）을 절대 흉내 내지 말 것.\n"
                         "  피할 골격：\n" + "\n".join(f"  - {x}" for x in av))
    text = prompt("propose", THEME=theme_text(st["theme"]), N=n, KIND_RULE=kind_rule, EXTRA="\n".join(extra))
    rid = f"r{len(st['rounds']) + 1}"
    results, errors = {}, {}

    def one(m):
        obj, rec = _call_json(slug, "propose", m, text, lambda o: _check_props(o, n), temperature=temperature, note=rid)
        return obj["proposals"][:n], rec
    with cf.ThreadPoolExecutor(max_workers=min(8, len(models) or 1)) as ex:
        futs = {ex.submit(one, m): m for m in models}
        for f in cf.as_completed(futs):
            m = futs[f]
            try:
                results[m] = f.result()
            except StudioError as e:
                errors[m] = str(e)
    with lock(slug):
        st = load(slug)
        pids = []
        for m in models:
            if m not in results:
                continue
            props, rec = results[m]
            each = round(rec["usd"] / max(1, len(props)), 6)
            for p in props:
                pid = f"P{st['next_pid']:02d}"
                st["next_pid"] += 1
                st["proposals"][pid] = {"id": pid, "round": rid, "model": m, "usd": each, "sec": rec["sec"], "origin": "model",
                                        **{f: str(p.get(f, "")) for f in PROPOSAL_FIELDS}}
                pids.append(pid)
        st["rounds"].append({"id": rid, "ts": now(), "models": models, "n": n, "mode": mode, "instruction": instruction,
                             "proposals": pids, "errors": errors, "theme_version": st["theme_version"]})
        st["log"].append(f"{now()} 제안 {rid}（{mode}）：{len(pids)}개 안, 실패 {len(errors)}개 모델")
        save(slug, st)
    return {"round": rid, "proposals": pids, "errors": errors}


def avoid_list(st):
    c = st.get("classify") or {}
    out = []
    for cl in c.get("clusters", []):
        if cl.get("label") == "수렴":
            out += [x for x in cl.get("one_lines", []) if x]
            if cl.get("type"):
                out.append(cl["type"])
    return list(dict.fromkeys(out))[:12]


def edit_proposal(slug, pid, fields):
    """사람이 안을 직접 고친다. 원래 안은 남기고, 고친 판을 새 안으로 추가한다（어느 쪽을 골랐는지 추적할 수 있게）."""
    with lock(slug):
        st = load(slug)
        src = st["proposals"].get(pid)
        if not src:
            raise StudioError(f"{pid}이(가) 없습니다")
        new = f"P{st['next_pid']:02d}"
        st["next_pid"] += 1
        st["proposals"][new] = {**src, **{k: str(v) for k, v in fields.items() if k in PROPOSAL_FIELDS},
                                "id": new, "origin": "human", "parent": pid, "usd": 0.0, "round": src["round"]}
        st["log"].append(f"{now()} {pid}을(를) 사람이 편집 → {new}")
        save(slug, st)
        return new


def revise_proposal(slug, pid, model, instruction):
    st = load(slug)
    src = st["proposals"].get(pid)
    if not src:
        raise StudioError(f"{pid}이(가) 없습니다")
    body = json.dumps({f: src.get(f, "") for f in PROPOSAL_FIELDS}, ensure_ascii=False, indent=1)
    text = prompt("revise", THEME=theme_text(st["theme"]), PROPOSAL=body, INSTRUCTION=instruction)

    def chk(o):
        return None if isinstance(o, dict) and isinstance(o.get("proposal"), dict) and o["proposal"].get("synopsis") else "proposal이 없다"
    obj, rec = _call_json(slug, "revise", model, text, chk, temperature=0.6, note=pid)
    with lock(slug):
        st = load(slug)
        new = f"P{st['next_pid']:02d}"
        st["next_pid"] += 1
        st["proposals"][new] = {**{f: str(obj["proposal"].get(f, src.get(f, ""))) for f in PROPOSAL_FIELDS}, "id": new,
                                "round": src["round"], "model": model, "usd": rec["usd"], "sec": rec["sec"], "origin": "revise",
                                "parent": pid, "instruction": instruction, "changes": str(obj.get("changes", ""))}
        st["log"].append(f"{now()} {pid}을(를) {model}로 개고 → {new}")
        save(slug, st)
    return new


def active_proposals(st):
    return [p for p in st["proposals"].values() if not p.get("hidden")]


def hide_proposal(slug, pid, hidden=True):
    with lock(slug):
        st = load(slug)
        st["proposals"][pid]["hidden"] = hidden
        save(slug, st)


# ---------------------------------------------------------------- 분류
def _props_block(props):
    return "\n\n".join(f"### {p['id']}\n" + "\n".join(f"{f}: {p.get(f, '')}" for f in PROPOSAL_FIELDS if p.get(f)) for p in props)


def classify(slug, model=None, method="tool", threshold=None, w_text=0.4, refresh_skeletons=False):
    """method = tool（골격을 뽑아 규칙으로 분류. 기본값）| ai（모델에게 읽혀 분류）| both."""
    st = load(slug)
    props = active_proposals(st)
    if len(props) < 2:
        raise StudioError("분류하려면 안이 2개 이상 필요합니다")
    model = model or settings()["skeleton_model"]
    threshold = float(threshold if threshold is not None else settings()["threshold"])
    prev = st.get("classify") or {}
    sk = {} if refresh_skeletons else dict(prev.get("skeletons", {}))
    result = {"ts": now(), "method": method, "model": model, "skeletons": sk}
    need = [p for p in props if p["id"] not in sk]
    if method in ("tool", "both") and need:
        ids = [p["id"] for p in need]

        def chk(o):
            got = {s.get("id") for s in (o or {}).get("skeletons", []) if isinstance(s, dict)}
            miss = [i for i in ids if i not in got]
            return f"{', '.join(miss)}의 골격이 없다" if miss else None
        obj, rec = _call_json(slug, "skeleton", model, prompt("skeleton", PROPOSALS=_props_block(need)), chk, temperature=0.1)
        for s in obj["skeletons"]:
            if s.get("id") in ids:
                sk[s["id"]] = {k: s.get(k, "") for k in similarity.SK_FIELDS + ["one_line"]}
    if method in ("tool", "both"):
        result.update(similarity.classify(props, sk, threshold, w_text))
    if method in ("ai", "both"):
        def chk2(o):
            return None if isinstance(o, dict) and isinstance(o.get("labels"), list) else "labels가 없다"
        obj, rec = _call_json(slug, "classify", model, prompt("classify", PROPOSALS=_props_block(props)), chk2, temperature=0.1)
        result["ai"] = {"labels": {x["id"]: {"label": x.get("label"), "reason": x.get("reason", "")} for x in obj["labels"] if x.get("id")},
                        "types": obj.get("types", [])}
        if method == "ai":
            result["labels"] = {k: {"label": v["label"], "cluster": None, "nearest": None, "sim": None} for k, v in result["ai"]["labels"].items()}
            result["clusters"] = []
    with lock(slug):
        st = load(slug)
        st["classify"] = result
        st["log"].append(f"{now()} 분류（{method}, 문턱값 {threshold}）")
        save(slug, st)
    return result


def reclassify(slug, threshold, w_text=0.4):
    """골격은 이미 뽑아 둔 것을 쓰고, 문턱값만 바꿔 다시 분류한다（모델을 부르지 않는다）."""
    st = load(slug)
    c = st.get("classify")
    if not c or not c.get("skeletons"):
        raise StudioError("먼저 분류（tool）를 실행해 주세요")
    props = [p for p in active_proposals(st) if p["id"] in c["skeletons"]]
    c.update(similarity.classify(props, c["skeletons"], float(threshold), float(w_text)))
    with lock(slug):
        st = load(slug)
        st["classify"] = c
        save(slug, st)
    return c


# ---------------------------------------------------------------- BRIEF
def select(slug, pid):
    with lock(slug):
        st = load(slug)
        if st.get("fixed"):
            raise StudioError("이미 Fix되었습니다")
        if pid not in st["proposals"]:
            raise StudioError(f"{pid}이(가) 없습니다")
        st["selected"] = pid
        st["log"].append(f"{now()} {pid}을(를) 선택")
        save(slug, st)


def _length(st):
    t = st["theme"]
    ch = int(t.get("chapters") or 8)
    tgt = int(t.get("chapter_target") or 3000)
    return ch, tgt, int(tgt * 0.8), int(tgt * 1.5)


def _brief_check(ch):
    def chk(o):
        b = (o or {}).get("brief")
        if not isinstance(b, dict):
            return "brief가 없다"
        r = scaffold.lint(b)
        # 형식의 오류만 다시 하게 한다. 내용의 lint는 사람에게 보여 준다
        hard = [e for e in r["errors"] if "항목이 없습니다" in e or "chapters가" in e]
        return "；".join(hard[:5]) if hard else None
    return chk


def make_brief(slug, model=None, instruction="", base_version=None):
    """고른 안으로 BRIEF를 만든다（base_version이 있으면 그 판에 지시를 더해 개정한다）."""
    st = load(slug)
    if st.get("fixed"):
        raise StudioError("이미 Fix되었습니다")
    model = model or settings()["brief_model"]
    ch, tgt, lo, hi = _length(st)
    if base_version:
        base = next((b for b in st["briefs"] if b["v"] == int(base_version)), None)
        if not base:
            raise StudioError(f"BRIEF v{base_version}이(가) 없습니다")
        lint_block = ""
        if base.get("lint", {}).get("errors"):
            lint_block = "\n<자료 이름=\"기계 검사 지적（이것도 고칠 것）\">\n" + "\n".join(f"- {e}" for e in base["lint"]["errors"]) + "\n</자료>\n"
        text = prompt("brief_revise", THEME=theme_text(st["theme"]), BRIEF=json.dumps(base["brief"], ensure_ascii=False, indent=1),
                      INSTRUCTION=instruction or "기계 검사 지적을 고친다", LINT_BLOCK=lint_block, CHAPTERS=base["brief"].get("length", {}).get("chapters", ch))
        pid = base["from"]
        stage = "brief-revise"
    else:
        pid = st.get("selected")
        if not pid:
            raise StudioError("먼저 안을 골라 주세요")
        p = st["proposals"][pid]
        ib = f"\n<자료 이름=\"프로듀서의 추가 지시\">\n{instruction}\n</자료>\n" if instruction.strip() else ""
        text = prompt("brief", THEME=theme_text(st["theme"]), PROPOSAL=json.dumps({f: p.get(f, "") for f in PROPOSAL_FIELDS}, ensure_ascii=False, indent=1),
                      INSTRUCTION_BLOCK=ib, CHAPTERS=ch, TARGET=tgt, MINC=lo, MAXC=hi, AUDIENCE=st["theme"].get("audience") or "일반 독자")
        stage = "brief"
    obj, rec = _call_json(slug, stage, model, text, _brief_check(ch), temperature=0.5, max_tokens=32000, reasoning="medium")
    return _add_brief(slug, obj["brief"], pid, model, instruction, rec["usd"], base_version, str(obj.get("changes", "")))


def _add_brief(slug, b, pid, model, instruction, usd, base_version=None, changes="", origin="model"):
    with lock(slug):
        st = load(slug)
        v = max([x["v"] for x in st["briefs"]] or [0]) + 1
        st["briefs"].append({"v": v, "ts": now(), "from": pid, "model": model, "instruction": instruction, "base": base_version,
                             "usd": usd, "origin": origin, "changes": changes, "brief": b, "lint": scaffold.lint(b)})
        st["log"].append(f"{now()} BRIEF v{v}（{origin}{', v' + str(base_version) + '에서' if base_version else ''}）")
        save(slug, st)
    return v


def edit_brief(slug, v, brief):
    """사람이 BRIEF를 직접 고친다. 새 판으로 저장한다."""
    st = load(slug)
    if st.get("fixed"):
        raise StudioError("이미 Fix되었습니다")
    base = next((b for b in st["briefs"] if b["v"] == int(v)), None)
    if not base:
        raise StudioError(f"BRIEF v{v}이(가) 없습니다")
    if isinstance(brief, str):
        try:
            brief = json.loads(brief)
        except ValueError as e:
            raise StudioError(f"JSON으로 읽을 수 없습니다：{e}")
    return _add_brief(slug, brief, base["from"], "human", "사람이 편집", 0.0, v, origin="human")


def render_brief(slug, v):
    st = load(slug)
    b = next((x for x in st["briefs"] if x["v"] == int(v)), None)
    if not b:
        raise StudioError(f"BRIEF v{v}이(가) 없습니다")
    r = scaffold.lint(b["brief"])
    md = scaffold.render(b["brief"]) if r["ok"] else None
    return {"lint": r, "markdown": md}


# ---------------------------------------------------------------- Fix와 engine
def fix(slug, v, roles=None, budget=None):
    """제안과 BRIEF를 확정하고, 작품 폴더를 engine의 작업 장소로 만든다. 여기서부터는 BRIEF를 바꾸지 않는다."""
    with lock(slug):
        st = load(slug)
        if st.get("fixed"):
            raise StudioError("이미 Fix되었습니다")
        b = next((x for x in st["briefs"] if x["v"] == int(v)), None)
        if not b:
            raise StudioError(f"BRIEF v{v}이(가) 없습니다")
        base = settings()
        roles = normalize_roles(roles, base["roles"])  # 일부·문자열 지정도 모드 기본값 위에 역할별로
        known = set(base["providers_known"]) if base["providers_known"] is not None else None
        checked, problems = check_models({**base, "roles": roles}, known)
        if problems:
            raise StudioError("역할 지정 문제：" + "；".join(problems))
        roles = checked["roles"]  # 키 없는 예비 모델은 뺀 것
        r = scaffold.create(b["brief"], wdir(slug), roles, budget, extra_top=("studio", "studio.json"))
        if not r["ok"]:
            raise StudioError("Fix할 수 없습니다：" + "；".join(r["errors"]))
        st["fixed"] = {"ts": now(), "brief_v": int(v), "proposal": b["from"], "roles": roles, "budget": budget,
                       "brief_sha": r["brief_sha"]}
        st["selected"] = b["from"]
        st["log"].append(f"{now()} Fix：{b['from']}와 BRIEF v{v}（{r['brief_sha']}）")
        save(slug, st)
        return st["fixed"]


def novelctl(slug, *args, env_extra=None, timeout=3600):
    d = wdir(slug)
    if not (d / "tools" / "novelctl.py").exists():
        raise StudioError("아직 Fix되지 않았습니다")
    env = os.environ.copy()
    env.update(env_extra or {})
    r = subprocess.run([sys.executable, "tools/novelctl.py", *args], cwd=d, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=timeout)
    return {"rc": r.returncode, "out": r.stdout[-6000:], "err": r.stderr[-3000:]}


def engine_status(slug):
    r = novelctl(slug, "status", "--json", timeout=60)
    try:
        return json.loads(r["out"])
    except ValueError:
        raise StudioError("status를 읽을 수 없습니다：" + (r["err"] or r["out"])[-500:])


def canon_fingerprint(slug):
    f = wdir(slug) / "canon" / "CANON.md"
    return hashlib.sha256(f.read_bytes()).hexdigest()[:8] if f.exists() else None


def approve(slug, fingerprint):
    """사람이 화면에서 CANON을 읽고, 지문을 확인하고 눌렀을 때만 승인한다."""
    fp = canon_fingerprint(slug)
    if not fp or fingerprint != fp:
        raise StudioError("지문이 일치하지 않습니다（CANON이 바뀌었을 가능성）. 다시 읽은 다음 승인해 주세요")
    return novelctl(slug, "approve-canon", env_extra={"NOVEL_WORKER_APPROVE": fp})


ENGINE_ACTIONS = {
    "start": lambda a: ["start"],
    "step": lambda a: ["step"],
    "redesign": lambda a: ["redesign", "--feedback", a["feedback"]],
    "rescue_canon": lambda a: ["rescue", "canon"] + rescue_opts(a),
    "rescue_chapter": lambda a: ["rescue", "chapter", str(int(a["n"]))] + rescue_opts(a),
    "resolve": lambda a: ["resolve"] + [f"--{x}" for x in a.get("flags", []) if x in ("retry", "accept", "relock", "done", "ledger-done")],
    "reset_chapter": lambda a: ["reset-chapter", str(int(a["n"]))],
    "polish": lambda a: ["start", "polish"],
    "proofread": lambda a: ["proofread"] + [str(int(x)) for x in a.get("chapters", [])],
    "assemble": lambda a: ["assemble"],
    "epub": lambda a: ["epub"] + (["--proofread"] if a.get("proofread") else []),
    "cover": lambda a: cover_args(a),
    "set_budget": lambda a: ["set-budget", str(float(a["budget"]))],  # novel_worker：사람이 정한 예산으로(보호 파일 무결성 확인 후)
}

MODEL_ID = re.compile(r"^[A-Za-z0-9_.:/\-]{3,120}$")


def rescue_opts(a):
    """상위 모델 예외 처리의 추가 지정：사람이 고른 모델（지정한 상위 모델로 고쳐지지 않았을 때）과 사람의 지시."""
    out = []
    m = str(a.get("model") or "").strip()
    if m:
        if not MODEL_ID.match(m):
            raise StudioError(f"모델 이름이 올바르지 않습니다：{m}")
        out += ["--model", m]
    note = str(a.get("note") or "").strip()
    if note:
        out += ["--note", note[:2000]]
    return out


COVER_STYLES = {"simple": "심플", "illust": "일러스트", "photo": "사실적"}
COVER_FONTS = {"mincho": "명조", "gothic": "고딕", "casual": "캐주얼"}


def cover_args(a):
    """표지 지정을 novelctl cover의 인수로 만든다. 값은 정해진 선택지만 통과시킨다."""
    style = a.get("style") if a.get("style") in COVER_STYLES else "illust"
    font = a.get("font") if a.get("font") in COVER_FONTS else "mincho"
    tdir = a.get("title_dir") if a.get("title_dir") in ("vertical", "horizontal") else "vertical"
    out = ["cover", "--style", style, "--font", font, "--title-dir", tdir,
           "--motif", str(a.get("motif", ""))[:400], "--author", str(a.get("author", ""))[:60]]
    if a.get("reuse_art"):
        out.append("--reuse-art")
    else:
        out += ["--model", str(a.get("model") or "none")]
    if a.get("title"):
        out += ["--title", str(a["title"])[:80]]
    return out


def tools_outdated(slug):
    """작품 폴더의 도구가 표지（v1.1）·rescue 모델 선택（v1.1）·OpenCode 창구（v1.1.1）보다 이전 판인가."""
    t = wdir(slug) / "tools"
    wire = t / "llm_wire.py"
    return (not (t / "make_cover.py").exists() or "NOVEL_MODEL_OVERRIDE" not in (t / "novelctl.py").read_text(encoding="utf-8")
            or not wire.exists() or "x-opencode-session" not in wire.read_text(encoding="utf-8"))


def upgrade_tools(slug):
    return scaffold.upgrade(wdir(slug))


def cover_info(slug):
    f = wdir(slug) / "images" / "cover.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None


def engine_action(slug, action, args=None):
    args = args or {}
    if action == "upgrade":
        r = upgrade_tools(slug)
        return {"rc": 0 if r["ok"] else 2, "out": "도구를 최신으로 바꿨습니다：" + (", ".join(r.get("changed", [])) or "변경 없음"), "err": "；".join(r.get("errors", []))}
    if action in ("cover", "rescue_canon", "rescue_chapter") and tools_outdated(slug) and (action == "cover" or args.get("model") or args.get("note")):
        raise StudioError("이 작품의 도구（tools/）는 오래된 판입니다（표지·모델을 고른 손질을 쓸 수 없습니다）. ‘도구를 최신으로’를 눌러 주세요")
    if action == "apply_proofread":
        d = wdir(slug)
        r = subprocess.run([sys.executable, "tools/proofread_apply.py"], cwd=d, capture_output=True, text=True)
        return {"rc": r.returncode, "out": r.stdout, "err": r.stderr}
    if action not in ENGINE_ACTIONS:
        raise StudioError(f"알 수 없는 조작 {action}")
    return novelctl(slug, *ENGINE_ACTIONS[action](args))


def set_edits(slug, enabled):
    """교정안의 채택 여부（{index: bool}）를 output/proofread_edits.json에 쓴다."""
    f = wdir(slug) / "output" / "proofread_edits.json"
    d = json.loads(f.read_text(encoding="utf-8"))
    for k, v in enabled.items():
        i = int(k)
        if 0 <= i < len(d["edits"]) and not d["edits"][i].get("rejected"):
            d["edits"][i]["enabled"] = bool(v)
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    return d


SAFE_READ = re.compile(r"^(BRIEF\.md|brief\.json|config\.json|canon/CANON\.md|chapters/ch\d\d\.md|polished/ch\d\d\.md|work/ch\d\d_(feedback\.md|verdict_a\d+\.json|draft\.md)|"
                       r"work/canon_feedback\.md|work/canon_review_notes\.md|state/LEDGER\.md|output/[^/]+\.(md|json|epub)|logs/run\.(log|out)|notes/[^/]+\.md|"
                       r"images/cover(_art)?\.(png|jpg|jpeg|webp)|images/cover\.json)$")


def read_file(slug, rel):
    if not SAFE_READ.match(rel or ""):
        raise StudioError(f"읽을 수 없는 위치입니다：{rel}")
    f = wdir(slug) / rel
    if not f.exists():
        return None
    if rel.endswith((".epub", ".png", ".jpg", ".jpeg", ".webp")):
        return f
    t = f.read_text(encoding="utf-8", errors="replace")
    if rel.startswith("logs/"):
        t = "\n".join(t.splitlines()[-80:])
    return t


def stage_of(st, d=None):
    if st.get("fixed"):
        d = d or wdir(st["slug"])
        p = d / "state" / "progress.json"
        if p.exists():
            ph = json.loads(p.read_text(encoding="utf-8")).get("phase")
            return {"design": "canon", "canon_hold": "canon", "chapters": "write", "final": "write", "done": "finish"}.get(ph, "canon")
        return "canon"
    if st.get("briefs"):
        return "brief"
    if st.get("classify"):
        return "classify"
    if st.get("proposals"):
        return "propose"
    return "theme"


def snapshot(slug):
    """화면에 넘기는 상태 일체."""
    st = load(slug)
    out = {**st, "stage": stage_of(st), "costs": costs(slug), "avoid": avoid_list(st),
           "derived": derived_children(slug), "suggest_slug": suggest_slug(slug)}
    for b in out["briefs"]:
        b["lint"] = scaffold.lint(b["brief"])
    if st.get("fixed"):
        try:
            out["engine"] = engine_status(slug)
        except StudioError as e:
            out["engine"] = {"error": str(e)}
        out["canon_fp"] = canon_fingerprint(slug)
        od = wdir(slug) / "output"
        out["outputs"] = sorted(p.name for p in od.iterdir()) if od.exists() else []
        out["cover"] = cover_info(slug)
        out["tools_outdated"] = tools_outdated(slug)
    return out
