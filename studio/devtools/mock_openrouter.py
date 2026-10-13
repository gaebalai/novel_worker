#!/usr/bin/env python3
"""mock_openrouter — OpenRouter（OpenAI 호환 Chat Completions）의 가짜(모의 모델). 비용을 들이지 않고 studio와 engine을 처음부터 끝까지 통과시켜 보기 위한 것.

  python3 studio/devtools/mock_openrouter.py [--port 8765]
  NOVEL_LLM_BASE_URL=http://127.0.0.1:8765/api/v1 NOVEL_PROVIDER_APIKEY=mock python3 studio/server.py

요청문의 표식에 따라 답을 바꾼다:
  <!-- TASK: design|canon-review|write|review|ledger|final|polish|rescue-canon|rescue-chapter|proofread -->  （engine의 패킷）
  <!-- STUDIO: propose|revise|skeleton|classify|brief|brief-revise -->                                       （studio의 요청문）
환경 변수:
  MOCK_FAIL_CANON_REVIEW=N   CANON 심사를 처음 N번만 불합격으로 만든다（설계를 다시 하는 경로를 시험하기 위해）
  MOCK_FAIL_CHAPTER=K        제K장 심사를 매번 불합격으로 만든다（CHAPTER_EXHAUSTED → rescue 경로를 시험하기 위해. rescue 후에는 합격）
  MOCK_FAIL_FINAL=K          최종 심사에서 제K장에 중대한 지적을 계속 낸다（제K장을 상위 모델로 고칠 때까지）
  MOCK_RESCUE_FAIL_MODEL=M   이 모델（쉼표 구분）로 rescue한 장은 심사를 통과하지 못한 채로 둔다（다른 모델을 고르는 경로를 시험하기 위해）
  MOCK_DELAY=초              응답 한 번을 늦춘다（진행 상황 표시를 보기 위해）

하네스 시험（harness/exams）에서도, 비용 없이 진행자의 행동만 확인하는 데 쓸 수 있다.
"""
import argparse
import hashlib
import json
import os
import random
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

STATE = {"canon_review": 0, "rescued": set()}
LOCK = threading.Lock()

MODELS = [
    ("anthropic/claude-opus-5.5", 5.0, 25.0), ("anthropic/claude-haiku-5.5", 0.1, 0.5),
    ("openai/gpt-6-luna-pro", 0.1, 0.5), ("deepseek/deepseek-v4.1-flash", 0.07, 0.28),
    ("aion-labs/aion-3.5-mini", 0.7, 1.4), ("meta/muse-spark-1.3-contributor", 0.2, 0.8),
    ("moonshotai/kimi-k3", 0.5, 2.0), ("z-ai/glm-5.3", 0.4, 1.6), ("qwen/qwen3.8-flash", 0.05, 0.2),
]
PRICE = {m: (i / 1e6, o / 1e6) for m, i, o in MODELS}
IMAGE_MODELS = [("google/gemini-2.5-flash-image", 0.039), ("openai/gpt-image-2", 0.05), ("black-forest-labs/flux.2-pro", 0.04)]


def fake_png(w=400, h=640, seed="x"):
    """표준 라이브러리만으로 그라데이션 PNG를 만든다（가짜 이미지 생성）."""
    import struct
    import zlib
    hsh = int(hashlib.md5(seed.encode()).hexdigest()[:6], 16)
    r0, g0, b0 = (hsh >> 16) & 255, (hsh >> 8) & 255, hsh & 255
    raw = b"".join(b"\x00" + bytes(v for x in range(w) for v in (
        (r0 * (h - y) + 30 * y) // h, (g0 * (h - y) + 40 * y) // h, (b0 * (h - y) + 70 * y) // h)) for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xffffffff)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")

WORDS = ["안개", "등불", "돌계단", "강물", "방울", "외투", "손끝", "수첩", "창", "발소리", "오래된 지도", "김", "열쇠", "촛불", "모래", "풍향계",
         "계단", "편지", "유리", "밧줄", "배", "종루", "선반", "바구니", "그림자", "화덕", "진흙", "소매", "깃털", "접시"]
VERBS = ["흔들리고 있었다", "식어 있었다", "울렸다", "말이 없었다", "보이지 않게 되었다", "빛났다", "남아 있었다", "기울었다", "젖어 있었다", "풀렸다"]


def josa(word, with_batchim, without_batchim):
    """한국어 조사를 받침 유무에 맞춰 고른다（예: 이/가, 을/를, 은/는）."""
    w = str(word or "").strip()
    c = w[-1] if w else ""
    if "가" <= c <= "힣" and (ord(c) - 0xAC00) % 28:
        return w + with_batchim
    return w + without_batchim


def section(text, name):
    m = re.search(rf'<자료 이름="{re.escape(name)}[^"]*">\n(.*?)\n</자료>', text, flags=re.S)
    return m.group(1) if m else ""


def sections_like(text, prefix):
    return re.findall(rf'<자료 이름="{re.escape(prefix)}[^"]*">\n(.*?)\n</자료>', text, flags=re.S)


def prose(n_chars, seed):
    rnd = random.Random(seed)
    out, total, k = [], 0, 0
    while total < n_chars:
        k += 1
        a, b = rnd.sample(WORDS, 2)
        s = f"{a} 곁에서 {josa(b, '이', '가')} {rnd.choice(VERBS)}. "
        if k % 5 == 0:
            s = f"“{rnd.choice(WORDS)}, {k}번째 선반에 있어”라고 누군가가 말했다. "
        if k % 9 == 0:
            s = s.rstrip() + "\n\n"
        out.append(s)
        total += len(re.sub(r"\s", "", s))
    text = "".join(out).strip().replace(" \n", "\n")
    if not text.endswith("."):
        text += "."
    return text


def slot_events(text, header):
    ev = {}
    for sid, word in re.findall(r"^- ([A-Z]\d)（[^）]*）：(설치|보강|회수)", section(text, header), flags=re.M):
        ev[sid] = {"설치": "planted", "보강": "reinforced", "회수": "recovered"}[word]
    return ev


def engine_answer(task, ch, text, model=""):
    if task == "design":
        prev = section(text, "이전 CANON")
        if prev:
            return prev
        tpl = section(text, "CANON 템플릿")
        k = [0]

        def fill(_):
            k[0] += 1
            return f"임시 설정{k[0]}（가짜 모델）"
        out = re.sub(r"（기입）", fill, tpl)
        out = re.sub(r"제목：[^\n]*", "제목：안개의 수첩", out, count=1)
        return out
    if task == "canon-review":
        with LOCK:
            STATE["canon_review"] += 1
            n = STATE["canon_review"]
        if n <= int(os.environ.get("MOCK_FAIL_CANON_REVIEW", "0")):
            return json.dumps({"pass": False, "issues": [{"severity": "high", "item": 9, "detail": "시간표와 제2장 카드의 날짜가 어긋난다（가짜 모델）", "fix": "날짜를 맞춘다"}]}, ensure_ascii=False)
        return json.dumps({"pass": True, "issues": [{"severity": "low", "item": 12, "detail": "불변 조건의 표현이 딱딱하다（가짜 모델）", "fix": "임의"}]}, ensure_ascii=False)
    if task in ("write", "rescue-chapter", "polish"):
        if task == "rescue-chapter":
            if model not in os.environ.get("MOCK_RESCUE_FAIL_MODEL", "").split(","):  # 이 모델의 손질은 심사를 통과하지 못한다
                with LOCK:
                    STATE["rescued"].add(ch)
            return section(text, "현재 원고")
        if task == "polish":
            return section(text, f"다듬을 원고（제{ch}장）")
        m = re.search(r"본문 (\d+)자 안팎", text)
        target = int(m.group(1)) if m else 2000
        return f"# 제{ch}장 가제 {ch}\n" + prose(target, f"{ch}-{hashlib.md5(text.encode()).hexdigest()[:6]}") + "\n"
    if task == "review":
        found = slot_events(text, "이 장의 복선（필수）")
        fail = os.environ.get("MOCK_FAIL_CHAPTER")
        if fail and int(fail) == ch and ch not in STATE["rescued"]:
            return json.dumps({"pass": False, "slots_found": found, "violations": [{"severity": "high", "type": "canon", "detail": "“세 번째 선반”이라고 썼지만 CANON에서는 두 번째（가짜 모델）", "fix": "두 번째로 고친다"}]}, ensure_ascii=False)
        return json.dumps({"pass": True, "slots_found": found, "violations": [{"severity": "low", "type": "typo", "detail": "쉼표가 많다（가짜 모델）", "fix": "임의"}]}, ensure_ascii=False)
    if task == "ledger":
        body = section(text, f"제{ch}장 본문")
        last = [l for l in body.strip().splitlines() if l.strip()][-1]
        last = re.split(r"(?<=\.)", last.strip())[-2] if last.count(".") > 1 else last
        ev = slot_events(text, "요청")
        evlines = re.findall(r"^- ([A-Z]\d)（[^）]*）：(설치|보강|회수)", text, flags=re.M)
        for sid, w in evlines:
            ev[sid] = {"설치": "planted", "보강": "reinforced", "회수": "recovered"}[w]
        fields = re.findall(r"^([A-Z_]+):", section(text, "요청"), flags=re.M)
        out = [f"## CH{ch:02d}"]
        for f in dict.fromkeys(fields):
            if f == "SLOT":
                out += [f"SLOT: {sid}={st} | 묘사: 제{ch}장의 장면에서 보여 주었다（가짜 모델）" for sid, st in ev.items()]
            elif f == "LAST":
                out.append(f"LAST: {last.strip()}")
            else:
                out.append(f"{f}: 제{ch}장의 {f}（가짜 모델）")
        return "\n".join(out)
    if task == "final":
        ff = os.environ.get("MOCK_FAIL_FINAL")
        if ff and int(ff) not in STATE["rescued"]:
            return json.dumps({"pass": False, "issues": [{"severity": "high", "chapter": int(ff), "detail": "복선 F1의 회수가 서둘러져서, 설치 장면과 이어지지 않는다（가짜 모델）", "fix": "설치 장면의 소품을 회수 장면에서 다시 불러온다"}],
                               "carry_over": [], "summary": "마지막 장이 버겁다（가짜 모델）"}, ensure_ascii=False)
        return json.dumps({"pass": True, "issues": [], "carry_over": [], "summary": "가짜 모델의 총평：모든 장이 갖춰져 있다."}, ensure_ascii=False)
    if task == "rescue-canon":
        return section(text, "현재 CANON")
    if task == "proofread":
        body = section(text, f"교정할 원고（제{ch}장）")
        m = re.search(r"“([^”]{4,20})”라고 누군가가 말했다\.", body)
        edits = []
        if m and body.count(m.group(0)) == 1:
            edits.append({"before": m.group(0), "after": m.group(0).replace("라고 누군가가 말했다", "라고 누군가 말했다"), "kind": "표현", "reason": "조사를 덜어 리듬을 살린다（가짜 모델）"})
        edits.append({"before": "존재하지 않는 원문", "after": "x", "kind": "오탈자", "reason": "기계가 기각해야 할 안（가짜 모델）"})
        return json.dumps({"edits": edits}, ensure_ascii=False)
    return "OK"


GENRES = {"SF": ["냉동 수면 우주선에서 AI가 승무원을 관리한다", "얼음 위성에서 미생물을 발견한다", "궤도 엘리베이터 정비원이 시간 지연을 알아챈다"],
          "미스터리": ["화자가 사실은 가해자였다", "사내보의 암호로 횡령을 고발한다", "밀실 회의실에서 임원이 쓰러진다"]}


def studio_answer(kind, text, model):
    seed = int(hashlib.md5((model + text[-200:]).encode()).hexdigest()[:8], 16)
    rnd = random.Random(seed)
    if kind == "propose":
        m = re.search(r"안의 수：(\d+)", text)
        n = int(m.group(1)) if m else 2
        th = re.search(r'<자료 이름="테마">\n(.*?)\n</자료>', text, flags=re.S)
        genre = "미스터리" if th and "미스터리" in th.group(1) else "SF"
        pool = GENRES[genre]
        props = []
        for i in range(n):
            # 가짜 모델에서도 ‘수렴’을 재현한다：많은 안이 pool[0]에 쏠리고, 가끔 벗어난다
            core = pool[0] if rnd.random() < 0.6 else rnd.choice(pool[1:])
            if "피할 골격" in text and core == pool[0]:
                core = pool[2]
            props.append({"kind": "본선" if i == 0 else "다른 관점", "genre": genre, "title": f"{core[:6]}의 {rnd.choice(WORDS)}",
                          "setting": f"{core}. 무대는 {josa(rnd.choice(WORDS), '이', '가')} 있는 곳.", "synopsis": f"{core}는 이야기. {prose(160, seed + i)}",
                          "highlight": f"종반에 {rnd.choice(WORDS)}의 의미가 뒤집힌다.", "appeal": "독자는 마지막 한 줄에서 앞부분을 다시 읽고 싶어진다.",
                          "must_keep": "시간 순서를 무너뜨리지 않을 것. 이유：반전이 효과를 잃기 때문.", "genre_reason": f"{genre}가 아니면 성립하지 않는 이유（가짜 모델）."})
        return json.dumps({"proposals": props}, ensure_ascii=False)
    if kind == "revise":
        m = re.search(r'<자료 이름="원래 안">\n(.*?)\n</자료>', text, flags=re.S)
        p = json.loads(m.group(1)) if m else {}
        p["title"] = (p.get("title") or "무제") + "（수정）"
        p["synopsis"] = (p.get("synopsis") or "") + " 지시를 받아 종반을 바꿨다."
        return json.dumps({"proposal": p}, ensure_ascii=False)
    if kind == "skeleton":
        items = re.findall(r'^### (P\d+)\n(.*?)(?=^### P\d+|\Z)', section(text, "제안"), flags=re.S | re.M)
        out = []
        for pid, body in items:
            if "냉동 수면" in body or "화자가 사실은 가해자" in body:
                sk = {"deceiver": "AI" if "냉동" in body else "화자", "hidden": "승무원 선별" if "냉동" in body else "자기 죄",
                      "layer": "서술", "flip": "관리자가 가해자", "stage": "우주선" if "냉동" in body else "회사", "ending": "비터"}
            else:
                sk = {"deceiver": rnd.choice(["아무도", "조직", "자연"]), "hidden": rnd.choice(["시간", "기억", "숫자"]),
                      "layer": rnd.choice(["물리", "제도"]), "flip": rnd.choice(["전제가 반대", "피해자가 구하는 쪽"]),
                      "stage": rnd.choice(["위성", "탑", "섬"]), "ending": rnd.choice(["메리배드", "비터"])}
            out.append({"id": pid, **sk, "one_line": f"{josa(sk['deceiver'], '이', '가')} {josa(sk['hidden'], '을', '를')} 숨기고 {sk['flip']}"})
        return json.dumps({"skeletons": out}, ensure_ascii=False)
    if kind == "classify":
        ids = re.findall(r"^### (P\d+)", section(text, "제안"), flags=re.M)
        return json.dumps({"labels": [{"id": i, "label": rnd.choice(["수렴", "변주", "이상치"]), "reason": "가짜 모델의 판단"} for i in ids],
                           "types": ["가짜 모델의 유형"]}, ensure_ascii=False)
    if kind in ("brief", "brief-revise"):
        m = re.search(r"장의 수：(\d+)", text)
        n = int(m.group(1)) if m else 5
        chs = [{"n": i, "title": f"가칭{i}", "tension": min(5, 1 + i % 5), "reveal": min(5, i * 5 // n), "function": f"제{i}장의 기능（가짜 모델）",
                "hook": "작은 훅", "forbidden": "결말을 밝히는 것" if i < n else ""} for i in range(1, n + 1)]
        mid = max(2, n // 2)
        b = {"title": "안개의 수첩", "logline": "가짜 모델의 한 줄 요지.", "genre": "SF", "audience": "일반 독자",
             "length": {"chapters": n, "chapter_target": 1800, "chapter_min": 1500, "chapter_max": 2600},
             "narration": "3인칭 단일 시점（시점 인물：미나）", "ending_type": "비터엔딩", "core": "기록을 남기는 일의 무게",
             "genre_reason": "과학적 설명이 해결이 된다", "setting": "얼음 위성의 관측소",
             "characters": [{"name": "미나", "role": "관측원", "fixed": "기록을 믿는다"}, {"name": "소라", "role": "정비사", "fixed": "거짓말을 하지 않는다"}],
             "free": "관측소의 세부", "rules": {"style": "건조한 문체", "language": "숫자는 아라비아 숫자", "characters": "두 사람 모두 선의로 움직인다", "lines": ["마법을 등장시키지 않는다"]},
             "chapters": chs,
             "slots": [{"id": "F1", "name": "수첩의 공백", "function": "기록의 누락", "plan": [[1, "planted"], [mid, "reinforced"], [n, "recovered"]]},
                       {"id": "C1", "name": "기한", "function": "이야기의 시계", "plan": [[1, "planted"], [n, "recovered"]]}],
             "fairplay": ["제2장까지 공백의 존재가 독자에게 제시된다"], "ending": "미나는 수첩을 덮고, 공백을 공백인 채로 남긴다.",
             "design_notes": ["기한에서 거꾸로 계산한다"]}
        if kind == "brief-revise":
            b["logline"] += "（지시를 반영）"
        return json.dumps({"brief": b}, ensure_ascii=False)
    return "{}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _gate(self):
        """실제 OpenCode Go에서 확인한 관문（2026-10-13）을 흉내 낸다. 통과시키지 않을 때는 응답을 돌려주고 True."""
        if self.headers.get("User-Agent", "Python-urllib").startswith("Python-urllib"):
            data = b"error code: 1010"  # Cloudflare：라이브러리 이름의 User-Agent는 차단된다
            self.send_response(403)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return True
        if self.command == "POST" and "/zen/go/" in self.path and not self.headers.get("x-opencode-session"):
            self._json(400, {"type": "error", "error": {"type": "MissingSessionID",
                                                         "message": "Request is missing x-opencode-session and cannot be routed efficiently."}})
            return True
        return False

    def do_GET(self):
        if self._gate():
            return
        if "/zen/" in self.path and self.path.rstrip("/").endswith("/models"):  # OpenCode：가격 없는 목록
            return self._json(200, {"object": "list", "data": [{"id": m.split("/")[-1], "object": "model", "owned_by": "opencode"}
                                                               for m, _, _ in MODELS] + [{"id": "mockwire-odd", "object": "model"}]})
        if self.path.rstrip("/").endswith("/images/models") or "output_modalities=image" in self.path:
            return self._json(200, {"data": [{"id": m, "name": m, "architecture": {"input_modalities": ["text"], "output_modalities": ["image"]},
                                              "pricing": {"image": str(p)}} for m, p in IMAGE_MODELS]})
        if self.path.rstrip("/").endswith("/models"):
            return self._json(200, {"data": [{"id": m, "name": m, "context_length": 200000,
                                              "pricing": {"prompt": f"{i / 1e6:.10f}", "completion": f"{o / 1e6:.10f}"}} for m, i, o in MODELS]})
        self._json(404, {"error": "not found"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        if self._gate():
            return
        path = self.path.rstrip("/")
        if path.endswith("/responses") or path.endswith("/messages") or "/zen/" in path:
            return self._opencode(path, body)
        if self.path.rstrip("/").endswith("/images"):
            if body.get("model") not in dict(IMAGE_MODELS):
                return self._json(404, {"error": {"message": "mock: not an image model"}})
            if body.get("aspect_ratio") == "2:3" and body.get("model") == "black-forest-labs/flux.2-pro":
                return self._json(400, {"error": {"message": "mock: aspect ratio not supported"}})  # 가로세로 비율 전환 경로를 시험하기 위해
            time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
            png = fake_png(seed=body.get("prompt", "") + body["model"])
            return self._json(200, {"created": int(time.time()), "data": [{"b64_json": __import__("base64").b64encode(png).decode(), "media_type": "image/png"}],
                                    "usage": {"cost": dict(IMAGE_MODELS)[body["model"]]}})
        text = body["messages"][-1]["content"]
        model = (body.get("models") or [body.get("model")])[0]
        if model == "mock/broken":
            return self._json(400, {"error": {"message": "mock: this model always fails"}})
        m = re.match(r"<!-- TASK: (\S+)(?: CH=(\d+))?", text)
        s = re.search(r"<!-- STUDIO: (\S+) -->", text)
        if m:
            out = engine_answer(m.group(1), int(m.group(2) or 0), text, model)
        elif s:
            out = studio_answer(s.group(1), text, model)
        else:
            out = "OK"
        time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
        pin, pout = PRICE.get(model, (1e-6, 2e-6))
        usage = {"prompt_tokens": len(text) // 2, "completion_tokens": len(out) // 2}
        usage["cost"] = round(usage["prompt_tokens"] * pin + usage["completion_tokens"] * pout, 8)
        if body.get("stream"):
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b": OPENROUTER PROCESSING\n\n")
            for i in range(0, len(out), 400):
                chunk = {"model": model, "choices": [{"delta": {"content": out[i:i + 400]}}]}
                self.wfile.write(f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode())
            fin = {"model": model, "choices": [{"delta": {}, "finish_reason": "stop"}], "usage": usage}
            self.wfile.write(f"data: {json.dumps(fin)}\n\ndata: [DONE]\n\n".encode())
            return
        self._json(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage})


def zen_route(model):
    """가짜 OpenCode의 창구 배정. mockwire-odd는 도구의 규칙（chat）과 다른 /messages에만 있다＝창구 전환을 시험하기 위해."""
    m = model.split("/")[-1]
    if re.match(r"^(gpt|grok|muse)", m):
        return "responses"
    if re.match(r"^(claude-|minimax|qwen|mockwire-odd)", m):
        return "messages"
    return "chat"


def _opencode_handler(self, path, body):
    proto = "responses" if path.endswith("/responses") else "messages" if path.endswith("/messages") else "chat"
    model = body.get("model", "")
    if "/zen/" in path and zen_route(model) != proto:
        return self._json(404, {"type": "error", "error": {"type": "NotFound", "message": f"Model {model} is not supported on this endpoint"}})
    if proto == "messages" and not self.headers.get("anthropic-version"):
        return self._json(400, {"type": "error", "error": {"type": "invalid_request_error", "message": "anthropic-version header is required"}})
    if proto == "responses":
        inp = body.get("input")
        text = inp if isinstance(inp, str) else "".join(x.get("content", "") for x in inp or [] if isinstance(x.get("content"), str))
    elif proto == "messages":
        text = (body.get("messages") or [{}])[-1].get("content", "")
    else:
        text = (body.get("messages") or [{}])[-1].get("content", "")
    m = re.match(r"<!-- TASK: (\S+)(?: CH=(\d+))?", text)
    st = re.search(r"<!-- STUDIO: (\S+) -->", text)
    out = engine_answer(m.group(1), int(m.group(2) or 0), text, model) if m else studio_answer(st.group(1), text, model) if st else "OK"
    time.sleep(float(os.environ.get("MOCK_DELAY", "0")))
    pin, pout = len(text) // 2, len(out) // 2  # 비용은 돌려주지 않는다（OpenCode와 같음）
    if proto == "responses":
        return self._json(200, {"id": "resp_mock", "object": "response", "model": model, "status": "completed",
                                "output": [{"type": "reasoning", "summary": []},
                                           {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": out}]}],
                                "usage": {"input_tokens": pin, "output_tokens": pout}})
    if proto == "messages":
        return self._json(200, {"id": "msg_mock", "type": "message", "role": "assistant", "model": model, "stop_reason": "end_turn",
                                "content": [{"type": "text", "text": out}], "usage": {"input_tokens": pin, "output_tokens": pout}})
    usage = {"prompt_tokens": pin, "completion_tokens": pout}
    if body.get("stream"):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.end_headers()
        for i in range(0, len(out), 400):
            self.wfile.write(f"data: {json.dumps({'model': model, 'choices': [{'delta': {'content': out[i:i + 400]}}]}, ensure_ascii=False)}\n\n".encode())
        self.wfile.write(f"data: {json.dumps({'model': model, 'choices': [{'delta': {}, 'finish_reason': 'stop'}], 'usage': usage})}\n\ndata: [DONE]\n\n".encode())
        return
    return self._json(200, {"model": model, "choices": [{"message": {"role": "assistant", "content": out}, "finish_reason": "stop"}], "usage": usage})


H._opencode = _opencode_handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    print(f"mock OpenRouter on http://127.0.0.1:{a.port}/api/v1  （OpenCode Go 흉내는 /zen/go/v1）", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
