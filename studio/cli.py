#!/usr/bin/env python3
"""studio/cli.py — studio와 같은 공정을 하네스（HermesAgent / agy / omp 등）나 스크립트에서 쓰기 위한 CLI.

모두 JSON을 돌려준다（표준 출력에 1개）. 종료 코드 0=성공 / 2=사람의 판단이 필요함·거부 / 1=오류.
화면（server.py）과 같은 core.py를 부르므로, 화면과 하네스를 섞어 써도 된다（같은 works/<slug>/studio.json을 읽는다）.

  python3 studio/cli.py works
  python3 studio/cli.py keys [--ping] [--short]               등록된 키·모드(solo/duo/all)·역할 배정. --ping 은 모델마다 짧은 연결 시험(소액)
  python3 studio/cli.py new <slug> --theme theme.json
  python3 studio/cli.py show <slug> [--brief]                 상태 요약（--brief 이면 제안 본문을 생략）
  python3 studio/cli.py theme <slug> --theme theme.json        테마를 바꿔 처음부터（기록은 studio/history/ 로 옮겨 둔다）
  python3 studio/cli.py derive <slug> <new-slug> [--theme theme.json]  테마를 고쳐 새 작품 폴더에서 다시 제안한다（원래 작품은 바꾸지 않는다）
  python3 studio/cli.py propose <slug> --models m1,m2 [--n 모드 기본값] [--mode new|resubmit|rolling] [--instruction "..."] [--temperature 0.8]
  python3 studio/cli.py edit <slug> <pid> --fields fields.json  사람（또는 진행자）이 안을 고친다. 새 안으로 추가된다
  python3 studio/cli.py revise <slug> <pid> --model m --instruction "..."
  python3 studio/cli.py classify <slug> [--method tool|ai|both] [--model m] [--threshold 0.5] [--refresh]
  python3 studio/cli.py reclassify <slug> --threshold 0.45     골격은 재사용하고 문턱값만 바꾼다（모델을 부르지 않는다）
  python3 studio/cli.py select <slug> <pid>
  python3 studio/cli.py brief <slug> [--model m] [--instruction "..."] [--base V]
  python3 studio/cli.py brief-put <slug> <base-V> --file brief.json   직접 쓴 BRIEF를 새 판으로 둔다（lint 포함）
  python3 studio/cli.py brief-show <slug> <V> [--markdown]
  python3 studio/cli.py fix <slug> <V> [--roles roles.json] [--budget 3]
  python3 studio/cli.py engine <slug> <action> [--ch N] [--feedback "..."] [--flags retry,accept] [--chapters 1,2]
        rescue_canon / rescue_chapter 는 [--model M]（이번 한 번만 다른 모델로）과 [--note "지시"] 도 받는다
        epub 은 [--proofread]（교정판 output/final_proofread.md 로）
        set_budget 은 --budget USD（작품 예산 변경. 사람이 정한 값으로）
        action = start | step | redesign | rescue_canon | rescue_chapter | resolve | reset_chapter | polish | proofread | assemble | epub | apply_proofread | cover
  python3 studio/cli.py engine <slug> cover [--model M|none] [--style simple|illust|photo] [--motif "..."]
        [--title-dir vertical|horizontal] [--font mincho|gothic|casual] [--author "..."] [--reuse-art]
  python3 studio/cli.py image-models                         이미지 생성 모델 목록
  python3 studio/cli.py costs <slug>
  python3 studio/cli.py edits <slug> --enabled 1,3,5           교정안 채택 번호（1부터. 나머지는 채택 안 함. 빈 값이면 모두 안 함）

승인（approve-canon）은 CLI에는 없습니다. 사람이 화면이나 터미널에서 합니다.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import core  # noqa: E402


def out(obj, rc=0):
    print(json.dumps(obj, ensure_ascii=False, indent=1))
    return rc


def summary(st, short=False):
    props = {}
    for pid, p in st["proposals"].items():
        props[pid] = {k: p.get(k) for k in ("model", "kind", "genre", "title", "usd", "origin", "parent", "hidden")}
        if not short:
            props[pid]["synopsis"] = p.get("synopsis")
        lab = (st.get("classify") or {}).get("labels", {}).get(pid)
        if lab:
            props[pid]["label"] = lab["label"]
    c = st.get("classify") or {}
    return {"slug": st["slug"], "stage": core.stage_of(st), "theme": st["theme"], "theme_version": st["theme_version"], "derived_from": st.get("derived_from"),
            "rounds": [{k: r[k] for k in ("id", "mode", "models", "n", "proposals", "errors")} for r in st["rounds"]],
            "proposals": props, "selected": st.get("selected"),
            "classify": {"clusters": [{k: cl.get(k) for k in ("id", "label", "type", "members", "cohesion")} for cl in c.get("clusters", [])],
                         "mean_similarity": c.get("mean_similarity"), "threshold": c.get("threshold")} if c else None,
            "avoid": core.avoid_list(st),
            "briefs": [{"v": b["v"], "from": b["from"], "model": b["model"], "origin": b.get("origin"), "lint": b["lint"], "usd": b["usd"]} for b in st["briefs"]],
            "fixed": st.get("fixed"), "costs": core.costs(st["slug"])}


def _role_model(spec):
    return spec if isinstance(spec, str) else spec.get("model")


def cmd_keys(a):
    """novel_worker：키 유무 → 모드 → 실제 모델 구성. 키 값은 보지 않는다(중계기가 알려 주는 유무만)."""
    import llm
    ks = core.key_status()
    if ks["mode"] is None:
        off = bool(ks["error"])
        fix = "`./nw --bridge` 로 중계기를 켜 주세요" if off else "저장소 최상위 .env 에 ANTHROPIC_API_KEY=... 를 적고 `./nw --bridge-restart`"
        if a.short:
            print(f"nw: ⚠ {ks['reason']}" + ("" if off else f" → {fix}"))
            return 2
        return out({**ks, "how_to_fix": fix}, 2)
    st = core.settings(ks, strict=False)
    roles = {r: {"model": v.get("model"), "fallback": v.get("fallback_models")} for r, v in st["roles"].items()}
    w, rv = roles.get("writer", {}).get("model"), roles.get("reviewer", {}).get("model")
    if a.short:  # nw 가 시작할 때 한 줄로
        have = ", ".join(ks["providers"]) or "없음"
        line = f"nw: 키 [{have}] → 모드 {st['mode']}（{core.MODE_LABEL[st['mode']]}）· 작가 {w} · 심사 {rv}"
        if st["mode"] == "solo":
            line += " · 혼자 모드: 쓰는 모델이 스스로 심사합니다. OpenAI 키를 더하면 duo"
        print(line)
        for pr in st["problems"]:
            print(f"nw: ⚠ {pr}")
        return 2 if st["problems"] else 0
    res = {"mode": st["mode"], "mode_label": core.MODE_LABEL.get(st["mode"], st["mode"]), "providers_with_key": ks["providers"],
           "reason": ks["reason"], "problems": st["problems"], "propose": {"models": st["propose_models"], "n_each": st.get("propose_n")},
           "skeleton_model": st["skeleton_model"], "brief_model": st["brief_model"], "roles": roles,
           "settings_file": str(core.SETTINGS_FILE) if core.SETTINGS_FILE.exists() else None}
    if st["problems"]:
        res["how_to_fix"] = ".env 에 그 회사 키를 넣고 `./nw --bridge-restart`, 또는 studio/settings.json 의 모델 지정을 키가 있는 회사로"
        return out(res, 2)
    if a.ping:
        used = list(dict.fromkeys(st["propose_models"] + [st["skeleton_model"], st["brief_model"]] + [r["model"] for r in roles.values()]))
        res["ping"] = {}
        for m in used:
            try:
                text, rec = llm.chat(m, "연결 확인입니다. OK 라고만 답하세요.", json_mode=False, max_tokens=1000, retries=0, timeout=120)
                res["ping"][m] = {"ok": True, "model": rec["model"], "sec": rec["sec"], "usd": rec["usd"], "reply": text[:20]}
            except llm.LLMError as e:
                res["ping"][m] = {"ok": False, "error": str(e)[:300]}
        if not all(v["ok"] for v in res["ping"].values()):
            return out(res, 2)
    return out(res)


def rd_json(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def main(argv):
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("cmd")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--theme")
    ap.add_argument("--models")
    ap.add_argument("--model")
    ap.add_argument("--n", type=int)  # 없으면 모드의 propose_n(solo 3 / duo 2 / all 1)
    ap.add_argument("--mode", default="new")
    ap.add_argument("--instruction", default="")
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--fields")
    ap.add_argument("--method", default="tool")
    ap.add_argument("--threshold", type=float)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--base", type=int)
    ap.add_argument("--file")
    ap.add_argument("--markdown", action="store_true")
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--roles")
    ap.add_argument("--budget", type=float)
    ap.add_argument("--feedback")
    ap.add_argument("--flags", default="")
    ap.add_argument("--chapters", default="")
    ap.add_argument("--ch", type=int)
    ap.add_argument("--style")
    ap.add_argument("--motif", default="")
    ap.add_argument("--title-dir", dest="title_dir")
    ap.add_argument("--font")
    ap.add_argument("--author", default="")
    ap.add_argument("--reuse-art", dest="reuse_art", action="store_true")
    ap.add_argument("--note", default="")
    ap.add_argument("--enabled")
    ap.add_argument("--ping", action="store_true")
    ap.add_argument("--short", action="store_true")
    ap.add_argument("--proofread", action="store_true")  # engine <slug> epub --proofread
    a = ap.parse_args(argv)
    A = a.args
    try:
        if a.cmd == "keys":
            return cmd_keys(a)
        if a.cmd == "works":
            return out({"works": core.list_works()})
        if a.cmd == "new":
            core.create_work(A[0], rd_json(a.theme) if a.theme else {})
            return out(summary(core.load(A[0])))
        if a.cmd == "show":
            return out(summary(core.load(A[0]), short=a.brief))
        if a.cmd == "theme":
            core.change_theme(A[0], rd_json(a.theme))
            return out(summary(core.load(A[0])))
        if a.cmd == "derive":  # derive <원래 작품> <새 작품> --theme theme.json（테마를 고쳐 새 작품으로 다시 낸다）
            st = core.derive_work(A[0], A[1], rd_json(a.theme) if a.theme else core.load(A[0])["theme"])
            return out(summary(st))
        if a.cmd == "propose":
            models = [m.strip() for m in (a.models or ",".join(core.settings()["propose_models"])).split(",") if m.strip()]
            n = a.n or max(core.settings().get("propose_n", 2), -(-2 // max(1, len(models))))  # 분류에는 안이 2개 이상 필요
            r = core.propose(A[0], models, n, a.instruction, a.mode, a.temperature)
            return out(r, 0 if r.get("proposals") else 2)  # 모두 실패하면 사람의 판단 필요
        if a.cmd == "edit":
            return out({"pid": core.edit_proposal(A[0], A[1], rd_json(a.fields))})
        if a.cmd == "revise":
            return out({"pid": core.revise_proposal(A[0], A[1], a.model or core.settings()["skeleton_model"], a.instruction)})
        if a.cmd == "classify":
            r = core.classify(A[0], a.model, a.method, a.threshold, refresh_skeletons=a.refresh)
            return out({k: r.get(k) for k in ("labels", "clusters", "mean_similarity", "top_pairs", "ai")})
        if a.cmd == "reclassify":
            r = core.reclassify(A[0], a.threshold)
            return out({k: r.get(k) for k in ("labels", "clusters", "mean_similarity")})
        if a.cmd == "select":
            core.select(A[0], A[1])
            return out({"selected": A[1]})
        if a.cmd == "brief":
            return out({"v": core.make_brief(A[0], a.model, a.instruction, a.base)})
        if a.cmd == "brief-put":
            v = core.edit_brief(A[0], int(A[1]), rd_json(a.file))
            st = core.load(A[0])
            return out({"v": v, "lint": next(b["lint"] for b in st["briefs"] if b["v"] == v)})
        if a.cmd == "brief-show":
            r = core.render_brief(A[0], int(A[1]))
            if not a.markdown:
                r.pop("markdown", None)
            return out(r, 0 if r["lint"]["ok"] else 2)
        if a.cmd == "fix":
            return out(core.fix(A[0], int(A[1]), rd_json(a.roles) if a.roles else None, a.budget))
        if a.cmd == "engine":
            if A[1] == "set_budget" and not a.budget:
                return out({"error": "--budget USD 가 필요합니다"}, 1)
            if A[1] in ("rescue_chapter", "reset_chapter") and not a.ch:
                return out({"error": "--ch（장 번호）가 필요합니다"}, 1)
            args = {"n": a.ch, "feedback": a.feedback or "", "flags": [x for x in a.flags.split(",") if x],
                    "chapters": [int(x) for x in a.chapters.split(",") if x],
                    "model": a.model, "style": a.style, "motif": a.motif, "title_dir": a.title_dir, "font": a.font,
                    "author": a.author, "reuse_art": a.reuse_art, "note": a.note, "proofread": a.proofread, "budget": a.budget}
            r = core.engine_action(A[0], A[1], args)
            st = core.engine_status(A[0])
            return out({**r, "status": {k: st.get(k) for k in ("phase", "current", "escalation", "next", "running", "usage")}},
                       0 if r["rc"] in (0, 3) else 2)
        if a.cmd == "status":
            return out(core.engine_status(A[0]))
        if a.cmd == "image-models":
            import llm
            return out({"models": llm.image_models()})
        if a.cmd == "edits":  # novel_worker：VS Code 체크리스트(notes/PROOFREAD.md)에서 고른 번호를 반영
            if a.enabled is None:
                return out({"error": "--enabled 1,3,5 처럼 채택할 번호를 주세요（1부터）"}, 1)
            f = core.wdir(A[0]) / "output" / "proofread_edits.json"
            n = len(json.loads(f.read_text(encoding="utf-8"))["edits"])
            picked = {int(x) for x in a.enabled.split(",") if x.strip()}
            bad = sorted(i for i in picked if not 1 <= i <= n)
            if bad:
                return out({"error": f"없는 번호：{bad}（교정안은 1~{n}）"}, 1)
            d = core.set_edits(A[0], {i: (i + 1) in picked for i in range(n)})
            return out({"enabled": [i + 1 for i, e in enumerate(d["edits"]) if e.get("enabled")],
                        "rejected": [i + 1 for i, e in enumerate(d["edits"]) if e.get("rejected")], "total": n})
        if a.cmd == "costs":
            return out(core.costs(A[0]))
        if a.cmd in ("approve", "approve-canon"):
            return out({"error": "승인은 사람이 합니다（studio 화면, 또는 작품 폴더에서 python3 tools/novelctl.py approve-canon）"}, 2)
        return out({"error": f"알 수 없는 명령 {a.cmd}", "usage": __doc__}, 1)
    except core.StudioError as e:
        return out({"error": str(e)}, 2)
    except (IndexError, FileNotFoundError, ValueError) as e:
        return out({"error": f"인수가 부족하거나 올바르지 않습니다：{e}", "usage": __doc__.strip().splitlines()[0]}, 1)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
