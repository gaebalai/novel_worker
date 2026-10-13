#!/usr/bin/env python3
"""novel_worker v1.0 회귀 테스트（표준 라이브러리 unittest 만. API 키·비용 불필요）.

  python3 -m unittest discover -s engine/tests -v

가짜 모델（studio/devtools/mock_openrouter.py）을 직접 띄워서 scaffold → novelctl → 교정 → EPUB 를 처음부터 끝까지 통과시킨다.
하네스 시험（harness/exams 의 L4）에서도 이 테스트가 통과하는 것을 합격 조건으로 삼고 있다.
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "engine" / "tools"))
sys.path.insert(0, str(REPO / "studio"))
import scaffold  # noqa: E402
import similarity  # noqa: E402

PY = sys.executable


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def sample_brief(n=4):
    sys.path.insert(0, str(REPO / "studio" / "devtools"))
    import mock_openrouter as m
    return json.loads(m.studio_answer("brief", f"장의 수：{n}", "x"))["brief"]


class Lint(unittest.TestCase):
    def test_sample_ok(self):
        self.assertTrue(scaffold.lint(sample_brief())["ok"])

    def test_recovered_before_planted(self):
        b = sample_brief()
        b["slots"][0]["plan"] = [[3, "planted"], [1, "recovered"]]
        r = scaffold.lint(b)
        self.assertFalse(r["ok"])
        self.assertTrue(any("회수" in e and "설치" in e for e in r["errors"]))

    def test_chapter_count_mismatch(self):
        b = sample_brief()
        b["chapters"] = b["chapters"][:-1]
        self.assertFalse(scaffold.lint(b)["ok"])

    def test_plan_text_form(self):
        self.assertEqual(scaffold.parse_plan("1:설치 3:보강 5:회수"), [[1, "planted"], [3, "reinforced"], [5, "recovered"]])

    def test_render_markers(self):
        b = sample_brief(6)
        md = scaffold.render(b)
        self.assertIn("<!-- RULES:BEGIN -->", md)
        for i in range(1, 7):
            self.assertIn(f"<!-- ROLE:{i:02d} -->", md)
        t = scaffold.canon_template(b, 16000)
        self.assertEqual(t.count("<!-- CARD:"), 6)
        self.assertNotIn("{{", t)


class Similarity(unittest.TestCase):
    def test_convergent_and_outlier(self):
        same = {"deceiver": "화자", "hidden": "자신의 죄", "layer": "서술", "flip": "피해자가 가해자", "stage": "회사", "ending": "비터", "one_line": ""}
        props = [{"id": f"P{i}", "title": "화자의 죄", "synopsis": "화자가 사실은 가해자였다. 회사 회의실에서."} for i in range(1, 5)]
        props.append({"id": "P9", "title": "얼음 아래의 호흡", "synopsis": "조석으로 얼음이 숨 쉬는 위성에서 열을 나르는 회로를 만든다."})
        sk = {f"P{i}": same for i in range(1, 5)}
        sk["P9"] = {"deceiver": "자연", "hidden": "시간의 어긋남", "layer": "물리", "flip": "전제가 반대", "stage": "얼음 위성", "ending": "열린 결말", "one_line": ""}
        r = similarity.classify(props, sk, 0.5)
        self.assertEqual(r["labels"]["P1"]["label"], "수렴")
        self.assertEqual(r["labels"]["P9"]["label"], "이상치")


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        env = dict(os.environ, MOCK_FAIL_CHAPTER="2", MOCK_RESCUE_FAIL_MODEL="claude-sonnet-5-5")  # novel_worker：기본 premium = sonnet
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        time.sleep(0.8)
        cls.tmp = Path(tempfile.mkdtemp(prefix="novel-e2e-"))
        cls.env = dict(os.environ, NOVEL_LLM_BASE_URL=f"http://127.0.0.1:{cls.port}/api/v1", NOVEL_PROVIDER_APIKEY="mock")

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        cls.mock.wait(timeout=10)  # 꺼질 때까지 기다린다(ResourceWarning 방지)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ctl(self, work, *args, env=None):
        return subprocess.run([PY, "tools/novelctl.py", *args], cwd=work, capture_output=True, text=True, env=env or self.env,
                              stdin=subprocess.DEVNULL, timeout=300)

    def test_full_run(self):
        work = self.tmp / "w"
        r = scaffold.create(sample_brief(4), work)
        self.assertTrue(r["ok"], r)
        self.assertEqual(self.ctl(work, "run").returncode, 2)  # canon_hold 에서 멈춘다
        st = json.loads(self.ctl(work, "status", "--json").stdout)
        self.assertEqual(st["phase"], "canon_hold")
        # 터미널이 아닌 승인은 거부된다（에이전트에서는 승인할 수 없다）
        self.assertIn("REFUSED", self.ctl(work, "approve-canon").stdout)
        import hashlib
        fp = hashlib.sha256((work / "canon" / "CANON.md").read_bytes()).hexdigest()[:8]
        self.assertIn("승인", self.ctl(work, "approve-canon", env=dict(self.env, NOVEL_WORKER_APPROVE=fp)).stdout)
        out = self.ctl(work, "run")
        self.assertIn("CHAPTER_EXHAUSTED", out.stdout)  # 제2장은 가짜 모델의 심사에서 계속 떨어진다
        # 지정한 상위 모델（sonnet）로 고쳐도 통과하지 못함 → 다른 모델을 골라 고친다（v1.1）
        self.assertEqual(self.ctl(work, "rescue", "chapter", "2").returncode, 0)
        out = self.ctl(work, "run")
        self.assertIn("CHAPTER_EXHAUSTED", out.stdout)
        self.assertIn("--model", out.stdout)
        self.assertEqual(self.ctl(work, "rescue", "chapter", "2", "--model", "openai/gpt-6-luna-pro", "--note", "선반 번호는 표대로").returncode, 0)
        self.assertIn("선반 번호는 표대로", (work / "work" / "rescue-chapter_ch02.packet.md").read_text(encoding="utf-8"))
        self.assertNotEqual(self.ctl(work, "rescue", "chapter", "2", "--model", "bad model!").returncode, 0)
        out = self.ctl(work, "run")
        self.assertEqual(out.returncode, 3, out.stdout[-800:])
        st = json.loads(self.ctl(work, "status", "--json").stdout)
        self.assertEqual(st["phase"], "done")
        self.assertEqual(st["rescues"], 2)
        hist = next(c for c in st["chapters"] if c["n"] == 2)["rescues"]
        self.assertEqual([h["model"] for h in hist], ["sonnet", "openai/gpt-6-luna-pro"])  # 기록에는 config 의 별칭이 남는다
        self.assertTrue(hist[0]["result"].startswith("심사에서 불합격") and hist[1]["result"] == "합격")
        used = [json.loads(x)["model"] for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines() if '"premium"' in x]
        self.assertIn("openai/gpt-6-luna-pro", used)
        self.assertGreater(st["usage"]["total_usd"], 0)
        self.assertIn("premium", st["usage"]["by_role"])
        self.assertEqual(self.ctl(work, "proofread").returncode, 0)
        edits = json.loads((work / "output" / "proofread_edits.json").read_text(encoding="utf-8"))["edits"]
        self.assertTrue(any(e.get("rejected") for e in edits))  # 원고에 없는 원문은 기계로 기각된다
        # novel_worker：VS Code 체크리스트에서 고른 번호(1부터)를 반영하는 CLI. 기각된 안은 켜지지 않는다
        ok_idx = [i + 1 for i, e in enumerate(edits) if not e.get("rejected")]
        cli = [PY, str(REPO / "studio" / "cli.py"), "edits", "w"]
        envw = dict(self.env, NOVEL_WORKS_DIR=str(self.tmp))
        r = subprocess.run(cli + ["--enabled", str(ok_idx[0])], capture_output=True, text=True, env=envw)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout)["enabled"], [ok_idx[0]])
        self.assertEqual(subprocess.run(cli + ["--enabled", "999"], capture_output=True, text=True, env=envw).returncode, 1)
        subprocess.run(cli + ["--enabled", ",".join(map(str, ok_idx))], capture_output=True, text=True, env=envw)
        a = subprocess.run([PY, "tools/proofread_apply.py"], cwd=work, capture_output=True, text=True)
        self.assertIn("실패 0건", a.stdout)
        self.ctl(work, "epub", "--proofread")
        self.assertTrue(list((work / "output").glob("*.epub")))
        g = subprocess.run([PY, "tools/guard.py", "check"], cwd=work, capture_output=True, text=True)
        self.assertEqual(g.returncode, 0, g.stdout)

    def test_accept_polish(self):
        """novel_worker：바깥 윤문(humanize-korean) 결과를 기계 검사해 polished/ 로. 대사가 바뀌면 거부, chapters/ 는 그대로."""
        import re as _re
        work = self.tmp / "wp"
        self.assertTrue(scaffold.create(sample_brief(3), work)["ok"])
        self.ctl(work, "run")
        import hashlib
        fp = hashlib.sha256((work / "canon" / "CANON.md").read_bytes()).hexdigest()[:8]
        self.ctl(work, "approve-canon", env=dict(self.env, NOVEL_WORKER_APPROVE=fp))
        self.ctl(work, "run")  # 제2장은 가짜 심사에서 멈추지만 제1장은 확정된다
        src = (work / "chapters" / "ch01.md").read_text(encoding="utf-8")
        body = "\n".join(src.strip().splitlines()[1:]).strip()
        self.assertIn("곁에서", body)
        dia = _re.compile(r"“[^”]*”")
        self.assertTrue(dia.findall(body))
        f = self.tmp / "h1.md"
        # 제목 줄 없이, 서술만 고치고, 끝에 요약 주석 → 받아들인다(제목은 원고의 것)
        f.write_text(body.replace("곁에서", "옆에서", 1) + "\n\n<!-- humanize 요약: 1곳 -->\n", encoding="utf-8")
        r = self.ctl(work, "accept-polish", "1", "--from", str(f), "--label", "humanize")
        self.assertEqual(r.returncode, 0, r.stdout)
        out = (work / "polished" / "ch01.md").read_text(encoding="utf-8")
        self.assertEqual(out.splitlines()[0], src.splitlines()[0])
        self.assertIn("옆에서", out)
        self.assertNotIn("<!--", out)
        self.assertEqual(dia.findall(out), dia.findall(body))
        self.assertEqual((work / "chapters" / "ch01.md").read_text(encoding="utf-8"), src)  # 원고는 그대로
        # 대사를 바꾸면 거부하고 아무것도 바꾸지 않는다
        first = dia.findall(out)[0]
        f.write_text(out.replace(first, first[:-1] + "요”", 1), encoding="utf-8")
        r = self.ctl(work, "accept-polish", "1", "--from", str(f), "--label", "humanize")
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("대사", r.stdout)
        self.assertEqual((work / "polished" / "ch01.md").read_text(encoding="utf-8"), out)
        # 두 번째 합격은 이전 polished 를 보관한다
        f.write_text(out.replace("곁에서", "옆에서", 1), encoding="utf-8")
        self.assertEqual(self.ctl(work, "accept-polish", "1", "--from", str(f), "--label", "humanize").returncode, 0)
        self.assertTrue((work / "polished" / "ch01.before-humanize.md").exists())
        # 같은 label 로 또 받아들여도 이전 판을 덮어쓰지 않는다
        cur = (work / "polished" / "ch01.md").read_text(encoding="utf-8")
        f.write_text(cur, encoding="utf-8")
        self.assertEqual(self.ctl(work, "accept-polish", "1", "--from", str(f), "--label", "humanize").returncode, 0)
        self.assertTrue((work / "polished" / "ch01.before-humanize-2.md").exists())
        # 본문 중간에 남은 HTML 주석은 거부
        f.write_text(cur.replace("옆에서", "<!-- 메모 --> 옆에서", 1), encoding="utf-8")
        self.assertEqual(self.ctl(work, "accept-polish", "1", "--from", str(f)).returncode, 2)
        # 인수 오류는 사용법(종료 코드 1, traceback 없음)
        for bad in (["1"], ["1", "--from"], ["1", "--from", str(self.tmp / "없음.md")], ["99", "--from", str(f)]):
            r = self.ctl(work, "accept-polish", *bad)
            self.assertEqual(r.returncode, 1, (bad, r.stdout, r.stderr))
            self.assertNotIn("Traceback", r.stderr)
        # EPUB 은 다듬기·윤문본(polished)을 쓴다
        import zipfile
        self.ctl(work, "epub")
        epub = next((work / "output").glob("*.epub"))
        xhtml = "".join(zipfile.ZipFile(epub).read(n).decode("utf-8") for n in zipfile.ZipFile(epub).namelist() if n.endswith(".xhtml"))
        self.assertIn("옆에서", xhtml)
        # 예산 변경：보호 파일이 기록과 같을 때만, 바꾼 뒤 guard 가 통과
        r = self.ctl(work, "set-budget", "7.5")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertEqual(json.loads((work / "config.json").read_text(encoding="utf-8"))["budget_usd"], 7.5)
        self.assertEqual(subprocess.run([PY, "tools/guard.py", "check"], cwd=work, capture_output=True, text=True).returncode, 0)
        (work / "prompts" / "write.md").write_text("변조", encoding="utf-8")
        self.assertEqual(self.ctl(work, "set-budget", "9").returncode, 2)  # 변조가 있으면 거부(덮어서 정상으로 만들지 않음)
        self.assertEqual(json.loads((work / "config.json").read_text(encoding="utf-8"))["budget_usd"], 7.5)

    def test_accept_polish_straight_quotes(self):
        """곧은따옴표 "…" 대사도 보호한다(모델이 프롬프트를 어긴 원고)."""
        work = self.tmp / "wq"
        self.assertTrue(scaffold.create(sample_brief(3), work)["ok"])
        (work / "chapters").mkdir(exist_ok=True)
        body = "\n\n".join(f'그가 문을 열었다. "선반 {i}번에 있어"라고 말했다. 바람이 불었다.' for i in range(1, 40))
        (work / "chapters" / "ch01.md").write_text("# 제1장 안개\n" + body + "\n", encoding="utf-8")
        f = self.tmp / "q1.md"
        f.write_text(body.replace('"선반 3번에 있어"', '"선반 3번이야"'), encoding="utf-8")
        r = self.ctl(work, "accept-polish", "1", "--from", str(f))
        self.assertEqual(r.returncode, 2, r.stdout)
        self.assertIn("대사", r.stdout)

    def test_cover(self):
        """표지: 이미지 생성（가짜 모델）→ 글자 조판 → EPUB 에 표지와 저자명이 들어간다. Chrome 이 없는 환경에서는 그림만 있는 표지가 된다."""
        import struct
        import zipfile
        work = self.tmp / "wc"
        self.assertTrue(scaffold.create(sample_brief(3), work)["ok"])
        self.ctl(work, "run")
        import hashlib
        fp = hashlib.sha256((work / "canon" / "CANON.md").read_bytes()).hexdigest()[:8]
        self.ctl(work, "approve-canon", env=dict(self.env, NOVEL_WORKER_APPROVE=fp))
        self.ctl(work, "run")
        r = self.ctl(work, "cover", "--model", "black-forest-labs/flux.2-pro", "--style", "illust", "--motif", "안개 속 종루",
                     "--title-dir", "vertical", "--font", "mincho", "--author", "테스트 저자")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        meta = json.loads((work / "images" / "cover.json").read_text(encoding="utf-8"))
        self.assertEqual(meta["author"], "테스트 저자")
        self.assertTrue((work / "images" / meta["cover"]).exists())
        if meta["cover"] == "cover.png" and not meta.get("note"):
            w, h = struct.unpack(">II", (work / "images" / "cover.png").read_bytes()[16:24])
            self.assertEqual((w, h), (1600, 2560))
        usage = [json.loads(x) for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(any(u["role"] == "cover" and u["cost"] for u in usage))
        # 글자만 다시 조판한다（모델을 부르지 않는다）
        n_before = len(usage)
        self.assertEqual(self.ctl(work, "cover", "--reuse-art", "--title-dir", "horizontal", "--font", "casual", "--author", "별명").returncode, 0)
        self.assertEqual(len((work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()), n_before)
        self.ctl(work, "epub")
        epub = next((work / "output").glob("*.epub"))
        opf = zipfile.ZipFile(epub).read("OEBPS/content.opf").decode("utf-8")
        self.assertIn("별명", opf)
        self.assertIn('properties="cover-image"', opf)
        g = subprocess.run([PY, "tools/guard.py", "check"], cwd=work, capture_output=True, text=True)
        self.assertNotEqual(g.returncode, 2, g.stdout)  # 표지는 보호 파일을 건드리지 않는다

    def test_chapter_numbers_over_twenty(self):
        work = self.tmp / "w22"
        r = scaffold.create(sample_brief(22), work)
        self.assertTrue(r["ok"], r)
        code = ("import sys; sys.path.insert(0,'tools'); import novelctl as n;"
                "print(n.kanji_num(11), n.kanji_num(20), n.kanji_num(22));"
                "print(bool(n.extract_chapter('# 제이십이장 안개\\n본문.', 22)), bool(n.extract_chapter('# 제십일장 안개\\n본문.', 11)))")
        out = subprocess.run([PY, "-c", code], cwd=work, capture_output=True, text=True).stdout.split()
        self.assertEqual(out, ["십일", "이십", "이십이", "True", "True"])


class Wire(unittest.TestCase):
    """창구의 차이（llm_wire）. OpenCode Go 의 실제 관문（User-Agent·x-opencode-session）은 2026-10-13 에 확인한 것."""

    def setUp(self):
        import llm_wire
        self.w = llm_wire
        os.environ.pop("NOVEL_LLM_PROTOCOLS", None)
        os.environ.pop("NOVEL_LLM_PRICES", None)

    def test_protocol_for(self):
        go, zen, orr = "https://opencode.ai/zen/go/v1", "https://opencode.ai/zen/v1", "https://openrouter.ai/api/v1"
        pf = self.w.protocol_for
        self.assertEqual([pf(go, m) for m in ("claude-haiku-5-5", "gpt-6-luna", "mimo-v2.6-pro", "deepseek-v4.1-flash", "minimax-m3", "qwen3.7-plus")],
                         ["messages", "responses", "chat", "chat", "messages", "messages"])
        self.assertEqual([pf(zen, m) for m in ("claude-opus-5-5", "gpt-6-sol", "qwen3.8-max", "qwen3.8-flash", "kimi-k3")],
                         ["messages", "responses", "chat", "messages", "chat"])
        self.assertEqual(pf(orr, "anthropic/claude-haiku-5.5"), "chat")  # OpenRouter 는 언제나 chat
        self.assertEqual(pf(go, "kimi-k3", {"kimi-": "messages"}), "messages")  # config 의 llm.protocols
        os.environ["NOVEL_LLM_PROTOCOLS"] = "gpt-:chat"
        self.assertEqual(pf(go, "gpt-6-luna"), "chat")

    def test_headers(self):
        h = self.w.headers("k", "messages", session="s1")
        self.assertFalse(h["User-Agent"].startswith("Python-urllib"))
        self.assertEqual((h["x-opencode-session"], h["x-api-key"], h["anthropic-version"]), ("s1", "k", "2023-06-01"))
        self.assertTrue(self.w.headers("k")["x-opencode-session"])  # 지정이 없어도 보낸다（OpenCode Go 에서는 필수）

    def test_bridge_and_alias(self):
        """novel_worker：중계기(127.0.0.1:8788)는 항상 /messages, 별칭은 models.json 의 ID 로, sk-ant 키는 x-api-key 로만."""
        w = self.w
        self.assertTrue(w.is_bridge("http://127.0.0.1:8788/v1"))
        self.assertFalse(w.is_bridge("http://127.0.0.1:18788/v1"))
        self.assertEqual([w.protocol_for("http://127.0.0.1:8788/v1", m) for m in ("gpt-6.1-sol", "glm-5.3", "claude-sonnet-5-5")],
                         ["messages"] * 3)
        self.assertEqual((w.resolve("sonnet"), w.resolve("GLM"), w.resolve("custom-x")), ("claude-sonnet-5-5", "glm-5.3", "custom-x"))
        self.assertGreater(w.estimate_cost("claude-sonnet-5-5", {"prompt_tokens": 1000, "completion_tokens": 1000}), 0)
        h = w.headers("sk-ant-xyz", "messages")
        self.assertNotIn("Authorization", h)
        self.assertEqual(h["x-api-key"], "sk-ant-xyz")
        h = w.headers("", "messages")  # 중계기 모드：인증 헤더 없음(중계기가 채운다)
        self.assertFalse({"Authorization", "x-api-key"} & set(h))
        # 응답이 날짜 붙은 스냅샷 ID 여도 요청한 ID 의 가격으로 추산
        u = {"prompt_tokens": 1000, "completion_tokens": 1000}
        self.assertIsNone(w.estimate_cost("claude-sonnet-5-5-20261001", u))
        self.assertEqual(w.estimate_cost("claude-sonnet-5-5-20261001", u, requested="claude-sonnet-5-5"),
                         w.estimate_cost("claude-sonnet-5-5", u))
        # 중계기면 Claude 이외 모델에도 effort, Claude 5 이전 Claude 에는 넣지 않는다
        _, b = w.build("messages", "gpt-6.1-sol", "", "hi", 0.2, 100, "low", bridge=True)
        self.assertEqual(b["output_config"], {"effort": "low"})
        _, b = w.build("messages", "gpt-6.1-sol", "", "hi", 0.2, 100, "low")
        self.assertNotIn("output_config", b)
        _, b = w.build("messages", "claude-haiku-4-5", "", "hi", 0.2, 100, "low", bridge=True)
        self.assertNotIn("output_config", b)

    def test_resolve_concurrent(self):
        """novel_worker：studio 는 제안을 동시에 부른다. 첫 호출이 겹쳐도 모두 실제 ID 로 바뀌어야 한다(빈 표 경쟁 회귀)."""
        import threading
        self.w._MODELS = None
        got, start = [], threading.Event()

        def one(alias):
            start.wait()
            got.append((alias, self.w.resolve(alias)))
        ts = [threading.Thread(target=one, args=(a,)) for a in ["sonnet", "gpt", "glm", "qwen", "kimi"] * 4]
        for t in ts:
            t.start()
        start.set()
        for t in ts:
            t.join()
        self.assertTrue(all(alias != rid for alias, rid in got), got)

    def test_build_parse(self):
        path, b = self.w.build("responses", "gpt-6-luna", "sys", "hi", 0.7, 100, "low", True)
        self.assertEqual((path, b["instructions"], b["max_output_tokens"], b["text"]["format"]["type"]), ("/responses", "sys", 100, "json_object"))
        path, b = self.w.build("messages", "claude-haiku-5-5", "sys", "hi", 1.5, 100, "medium")
        # novel_worker：Claude 5 세대는 temperature 를 보내면 400 → 빼고 effort 로 조절한다
        self.assertEqual((path, b["system"], "temperature" in b, b["output_config"]), ("/messages", "sys", False, {"effort": "medium"}))
        path, b = self.w.build("messages", "qwen3.7-plus", "sys", "hi", 1.5, 100, "medium")
        self.assertEqual((b["temperature"], "output_config" in b), (1.0, False))
        t, fin, u, _ = self.w.parse("responses", {"status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
                                                  "output": [{"type": "message", "content": [{"type": "output_text", "text": "가"}]}],
                                                  "usage": {"input_tokens": 3, "output_tokens": 1}})
        self.assertEqual((t, fin, u["prompt_tokens"]), ("가", "length", 3))
        t, fin, u, _ = self.w.parse("messages", {"content": [{"type": "thinking"}, {"type": "text", "text": "나"}], "stop_reason": "end_turn",
                                                 "usage": {"input_tokens": 2, "output_tokens": 5}})
        self.assertEqual((t, fin, u["completion_tokens"]), ("나", "stop", 5))

    def test_estimate_cost(self):
        self.assertAlmostEqual(self.w.estimate_cost("minimax-m3", {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}), 1.5)
        self.assertIsNone(self.w.estimate_cost("gpt-6-luna", {"prompt_tokens": 10}))
        os.environ["NOVEL_LLM_PRICES"] = "gpt-6-luna=1/10"
        self.assertAlmostEqual(self.w.estimate_cost("gpt-6-luna", {"prompt_tokens": 1_000_000, "completion_tokens": 100_000}), 2.0)


class OpenCodeEndToEnd(unittest.TestCase):
    """가짜 OpenCode Go（/zen/go/v1）에서 studio 와 engine 양쪽이 창구를 구분해 써서 통과하는 것."""

    @classmethod
    def setUpClass(cls):
        cls.port = free_port()
        cls.mock = subprocess.Popen([PY, str(REPO / "studio" / "devtools" / "mock_openrouter.py"), "--port", str(cls.port)],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.8)
        cls.base = f"http://127.0.0.1:{cls.port}/zen/go/v1"
        cls.tmp = Path(tempfile.mkdtemp(prefix="novel-oc-"))
        cls.env = dict(os.environ, NOVEL_LLM_BASE_URL=cls.base, NOVEL_PROVIDER_APIKEY="mock")
        cls.env.pop("NOVEL_LLM_PROTOCOLS", None)

    @classmethod
    def tearDownClass(cls):
        cls.mock.terminate()
        cls.mock.wait(timeout=10)  # 꺼질 때까지 기다린다(ResourceWarning 방지)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_library_user_agent_is_refused(self):
        import urllib.error
        import urllib.request
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(self.base + "/models", timeout=10)
        self.assertEqual(cm.exception.code, 403)
        self.assertIn(b"1010", cm.exception.read())
        cm.exception.close()  # 오류 응답을 닫는다(ResourceWarning 방지)

    def test_studio_chat(self):
        code = ("import sys, json; sys.path.insert(0, 'studio'); import llm\n"
                "out = []\n"
                "for m in ['claude-haiku-5-5', 'gpt-6-luna', 'deepseek-v4-flash', 'mockwire-odd']:\n"
                "    t, r = llm.chat(m, '<!-- STUDIO: propose -->\\n테마', session='novel-studio-t')\n"
                "    out.append([m, r['protocol'], r['price'], bool(llm.extract_json(t))])\n"
                "print(json.dumps(out))")
        r = subprocess.run([PY, "-c", code], cwd=REPO, capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        got = json.loads(r.stdout)
        self.assertEqual([g[1] for g in got], ["messages", "responses", "chat", "messages"])  # mockwire-odd 는 chat → messages 로 바뀐다
        self.assertEqual([g[2] for g in got], ["estimated", "unknown", "estimated", "unknown"])  # novel_worker：haiku 는 models.json 에 가격이 있다
        self.assertTrue(all(g[3] for g in got))

    def test_engine_until_canon_hold(self):
        work = self.tmp / "oc"
        r = scaffold.create(sample_brief(4), work, roles={"writer": "claude-haiku-5-5", "reviewer": "gpt-6-luna", "premium": "minimax-m3"})
        self.assertTrue(r["ok"], r)
        run = subprocess.run([PY, "tools/novelctl.py", "run"], cwd=work, capture_output=True, text=True, env=self.env,
                             stdin=subprocess.DEVNULL, timeout=300)
        self.assertEqual(run.returncode, 2, run.stdout[-800:] + run.stderr[-800:])  # canon_hold 에서 멈춘다
        recs = [json.loads(x) for x in (work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()]
        self.assertTrue(recs and all(x["ok"] for x in recs))
        self.assertEqual({x["model"] for x in recs}, {"claude-haiku-5-5", "gpt-6-luna"})
        p = subprocess.run([PY, "tools/call_llm.py", "--ping", "premium"], cwd=work, capture_output=True, text=True, env=self.env, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        last = json.loads((work / "logs" / "usage.jsonl").read_text(encoding="utf-8").splitlines()[-1])
        self.assertEqual(last["model"], "minimax-m3")
        self.assertTrue(last.get("est") and last["cost"] > 0)  # OpenCode 는 비용을 돌려주지 않는다 → 공개 가격으로 추정한다


class KeyModes(unittest.TestCase):
    """novel_worker：등록된 키 → 모드(solo/duo/all). 최소 요건은 Anthropic 키 하나."""

    def setUp(self):
        import core
        self.core = core
        self._orig = core.llm.bridge_providers
        self._orig_dotenv = core.llm._dotenv
        core.llm._dotenv = lambda name: None  # 사용자의 .env(NOVEL_KEY_MODE 등)에 영향받지 않게
        self._orig_sf = core._settings_file
        core._settings_file = lambda: {}
        self._env = os.environ.pop("NOVEL_KEY_MODE", None)
        core._KEY_STATUS = None

    def tearDown(self):
        self.core.llm.bridge_providers = self._orig
        self.core.llm._dotenv = self._orig_dotenv
        self.core._settings_file = self._orig_sf
        self.core._KEY_STATUS = None
        os.environ.pop("NOVEL_KEY_MODE", None)
        if self._env is not None:
            os.environ["NOVEL_KEY_MODE"] = self._env

    def with_keys(self, provs):
        self.core.llm.bridge_providers = lambda: (set(provs), "")
        self.core._KEY_STATUS = None
        return self.core.settings()

    def test_choose_mode(self):
        cm = self.core.choose_mode
        self.assertEqual(cm({"anthropic"}), "solo")
        self.assertEqual(cm({"anthropic", "openai"}), "duo")
        self.assertEqual(cm({"anthropic", "openai", "glm", "qwen", "kimi"}), "all")
        self.assertIsNone(cm({"openai", "glm"}))

    def test_settings_by_keys(self):
        s = self.with_keys({"anthropic"})
        self.assertEqual((s["mode"], s["propose_models"], s["propose_n"]), ("solo", ["sonnet"], 3))
        self.assertTrue(all(v["model"] in ("sonnet", "opus") and set(v["fallback_models"]) <= {"opus"} for v in s["roles"].values()))
        s = self.with_keys({"anthropic", "openai"})
        self.assertEqual((s["mode"], s["roles"]["writer"]["model"], s["roles"]["reviewer"]["model"]), ("duo", "sonnet", "gpt"))
        self.assertEqual(s["propose_models"], ["sonnet", "gpt"])
        s = self.with_keys({"anthropic", "openai", "kimi"})  # 남는 키는 제안에만 보탠다
        self.assertEqual((s["mode"], s["propose_models"], s["propose_n"]), ("duo", ["sonnet", "gpt", "kimi"], 1))
        with self.assertRaises(self.core.StudioError):
            self.with_keys({"openai"})  # Anthropic 키 없음

    def test_mode_override(self):
        os.environ["NOVEL_KEY_MODE"] = "solo"
        self.assertEqual(self.with_keys({"anthropic", "openai"})["mode"], "solo")

    def test_roles_merge_and_filter(self):
        # 일부·문자열 역할 지정은 모드 기본값 위에 역할별로. 예비 모델이 all 기본값(kimi·glm)에서 새어 들지 않는다
        self.core._settings_file = lambda: {"roles": {"writer": "opus"}}
        s = self.with_keys({"anthropic", "openai"})
        self.assertEqual(s["roles"]["writer"], {"model": "opus", "fallback_models": []})
        self.assertEqual(s["roles"]["reviewer"]["model"], "gpt")
        self.assertTrue(all(self.core.provider_of(m) in ("anthropic", "openai")
                            for v in s["roles"].values() for m in [v["model"]] + v["fallback_models"]))
        # 강제 all 인데 키가 둘뿐：키 없는 회사는 제안·예비에서 빠지고, 주 모델(문체 kimi)이 막히면 오류
        self.core._settings_file = lambda: {}
        os.environ["NOVEL_KEY_MODE"] = "all"
        with self.assertRaises(self.core.StudioError):
            self.with_keys({"anthropic", "openai"})
        self.core._KEY_STATUS = None
        s = self.core.settings(strict=False)
        self.assertEqual(s["propose_models"], ["sonnet", "gpt"])
        self.assertTrue(any("polisher" in x for x in s["problems"]))

    def test_mode_typo_and_failure_not_cached(self):
        os.environ["NOVEL_KEY_MODE"] = "DUO"
        self.assertEqual(self.with_keys({"anthropic", "openai", "glm", "qwen", "kimi"})["mode"], "duo")  # 대소문자 무시
        os.environ["NOVEL_KEY_MODE"] = "soloo"
        self.core.llm.bridge_providers = lambda: ({"anthropic"}, "")
        self.core._KEY_STATUS = None
        self.assertIn("알 수 없는 모드", self.core.key_status()["reason"])
        os.environ.pop("NOVEL_KEY_MODE")
        # 중계기가 꺼져 실패한 결과는 기억하지 않는다 → 켠 뒤 바로 동작
        self.core.llm.bridge_providers = lambda: (None, "연결 거부")
        self.core._KEY_STATUS = None
        with self.assertRaises(self.core.StudioError):
            self.core.settings()
        self.core.llm.bridge_providers = lambda: ({"anthropic", "openai"}, "")
        self.assertEqual(self.core.settings()["mode"], "duo")

    def test_base_url_port_and_v1(self):
        import llm
        old = {k: os.environ.get(k) for k in ("NOVEL_LLM_BASE_URL", "NOVEL_BRIDGE_PORT")}
        try:
            os.environ["NOVEL_BRIDGE_PORT"] = "8790"
            os.environ.pop("NOVEL_LLM_BASE_URL", None)
            self.assertEqual(llm.base_url(), "http://127.0.0.1:8790/v1")
            os.environ["NOVEL_LLM_BASE_URL"] = "http://127.0.0.1:8790"  # ANTHROPIC_BASE_URL 형태로 적어도
            self.assertEqual(llm.base_url(), "http://127.0.0.1:8790/v1")
        finally:
            for k, v in old.items():
                os.environ.pop(k, None)
                if v is not None:
                    os.environ[k] = v

    def test_not_bridge_uses_all(self):
        self.core.llm.bridge_providers = lambda: (None, "")
        self.core._KEY_STATUS = None
        self.assertEqual(self.core.settings()["mode"], "all")


class Derive(unittest.TestCase):
    """테마를 고쳐서 새 작품 폴더에서 다시 제안한다（v1.1.1）. 이름은 반드시 바꾼다＝원래 작품은 덮어쓰지 않는다."""

    def setUp(self):
        import core
        self.core = core
        self.saved = core.WORKS
        core.WORKS = Path(tempfile.mkdtemp(prefix="novel-derive-"))

    def tearDown(self):
        shutil.rmtree(self.core.WORKS, ignore_errors=True)
        self.core.WORKS = self.saved

    def test_derive(self):
        c = self.core
        c.create_work("moon", {"genre": "SF", "theme": "얼음 달"})
        before = (c.WORKS / "moon" / "studio.json").read_bytes()
        self.assertEqual(c.suggest_slug("moon"), "moon-2")
        for bad in ("moon", "", "../x"):
            with self.assertRaises(c.StudioError):
                c.derive_work("moon", bad, {"genre": "SF", "theme": "얼음 달의 조수"})
        st = c.derive_work("moon", "moon-2", {"genre": "SF", "theme": "얼음 달의 조수"})
        self.assertEqual(st["derived_from"]["slug"], "moon")
        self.assertEqual(st["derived_from"]["changed"], ["테마·소재"])
        self.assertEqual((st["rounds"], st["proposals"]), ([], {}))
        with self.assertRaises(c.StudioError):
            c.derive_work("moon", "moon-2", {"theme": "다른"})  # 이미 있는 이름으로는 만들지 않는다
        self.assertEqual((c.WORKS / "moon" / "studio.json").read_bytes(), before)  # 원래 작품은 전혀 바뀌지 않는다
        self.assertEqual(c.suggest_slug("moon-2"), "moon-3")
        self.assertEqual([k["slug"] for k in c.derived_children("moon")], ["moon-2"])
        (c.WORKS / "stray").mkdir()
        (c.WORKS / "stray" / "memo.txt").write_text("x")
        with self.assertRaises(c.StudioError):
            c.create_work("stray", {})  # 내용물이 있는 폴더에는 만들지 않는다


if __name__ == "__main__":
    unittest.main()
