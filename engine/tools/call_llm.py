#!/usr/bin/env python3
"""call_llm — 패킷 하나를 모델에 보내고, 본문만 표준 출력으로 돌려준다.

novel_worker：기본 창구는 중계기(http://127.0.0.1:8788/v1, Anthropic /messages 형식)이고, 중계기가 모델 이름으로 각 회사 API 에 나눠 보낸다.
이때 키는 중계기가 자기 환경(.env)에서 채우므로 이 도구는 인증 헤더를 보내지 않는다. OpenRouter 등 다른 창구도 그대로 쓸 수 있다(아래는 원본 설명).

에이전트 하네스(Hermes / OpenCode / Goose 등)에 의존하지 않는다. Python 표준 라이브러리만 쓴다.

  python3 tools/call_llm.py <packet> <role>     novelctl.py 에서 불리는 일반적인 사용법(role = writer / reviewer / polisher / premium)
  python3 tools/call_llm.py --ping [<role>]      연결·키·모델 이름·응답 속도 확인(수십 토큰만 쓴다)

v1.0：호출 한 번마다 logs/usage.jsonl 에 한 줄(역할·공정·모델·토큰·비용·초)을 추가한다.
      novelctl status --json 과 studio 가 이것을 집계해 비용을 낸다.

스트리밍을 쓰는 이유:
  - 추론이 긴 모델이라도 “살아 있는지” 알 수 있다(NOVEL_PROGRESS_FILE 에 수신 상황을 써냄 → novelctl status 에 표시)
  - 무통신 타임아웃(llm.idle_timeout_sec)으로 정말 막힌 연결만 찾아내 재시도할 수 있다

API 키를 찾는 순서(중계기가 아닌 창구일 때만. 위에서부터, 처음 찾은 것):
  1. config.json 의 llm.api_key_env 에 나열한 환경 변수(기본: NOVEL_LLM_API_KEY, OPENROUTER_API_KEY, HERMES_CUSTOM_OPENROUTER_API_KEY)
  2. 프로젝트 바로 아래의 .env, 없으면 상위 폴더의 .env(예: OPENROUTER_API_KEY=sk-or-...)
선택:
  NOVEL_LLM_BASE_URL   기본은 config 의 llm.base_url(중계기). 환경 변수, 또는 가까운 .env 에 써도 된다
  NOVEL_BRIDGE_PORT    중계기 포트(기본 8788). 환경 변수나 .env
  NOVEL_PROGRESS_FILE  수신 상황을 써낼 파일(novelctl 이 자동으로 설정)
"""
import http.client
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import llm_wire as wire  # noqa: E402  창구마다 다른 점(헤더·/responses·/messages). v1.1.1
import hashlib  # noqa: E402
SESSION = "novelctl-" + hashlib.sha1(str(ROOT).encode()).hexdigest()[:16]  # 작품마다 같은 값(x-opencode-session)
CFG = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
LLM = CFG["llm"]
if LLM.get("models_file") and not os.environ.get("NOVEL_MODELS_FILE"):
    os.environ["NOVEL_MODELS_FILE"] = LLM["models_file"]  # novel_worker：작품 폴더가 저장소 밖에 있어도 별칭표(models.json)를 찾는다
JSON_TASKS = {"canon-review", "review", "final", "proofread"}
RETRY_HTTP = (408, 409, 425, 429, 500, 502, 503, 504)


def err(msg):
    print(msg, file=sys.stderr, flush=True)


def die(msg, code=1):
    err(msg)
    sys.exit(code)


class ApiError(Exception):
    pass


class CallFailed(Exception):
    """호출 자체가 실패했다(재시도 소진·고칠 수 없는 HTTP 오류). 예비 모델로 넘어갈 수 있는 실패만 이것으로 알린다.
    설정 오류(키 없음 등)는 die() 로 바로 끝낸다(예비 모델로 넘어가도 같은 이유로 실패하므로)."""


class Deadline(Exception):
    """호출 한 번이 실제 시간 마감(profiles.*.deadline_sec)을 넘겼다. 추론 폭주를 끊고 다시 보내기 위한 것."""


class Degenerate(Exception):
    pass


def degenerate(tail):
    """끝 160자가 40자 이하 단위의 반복이면 폭주로 본다."""
    if len(set(tail)) <= 2:
        return True
    for unit in range(1, 41):
        pat = tail[-unit:]
        if pat * (len(tail) // unit) == tail[len(tail) % unit:]:
            return True
    return False


class Progress:
    def __init__(self, path, task):
        self.path, self.task, self.t0, self.last = path, task, time.time(), 0.0
        self.reasoning = self.content = 0
        self.last_recv = time.time()
        self.attempt = 1

    def tick(self, force=False):
        self.last_recv = time.time()
        if not self.path or (not force and time.time() - self.last < 2):
            return
        self.last = time.time()
        try:
            Path(self.path).write_text(json.dumps({
                "task": self.task, "attempt": self.attempt, "pid": os.getpid(),
                "started": self.t0, "last_recv": self.last_recv,
                "reasoning_chars": self.reasoning, "content_chars": self.content,
            }), encoding="utf-8")
        except OSError:
            pass


def request(url, key, body, idle_timeout):
    # User-Agent 는 라이브러리 이름으로 하지 않는다(Cloudflare 의 403·error code 1010 을 피한다). OpenCode 에는 작품별 session 을 넘긴다
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=wire.headers(key, "chat", session=SESSION, title=LLM.get("app_title", "novelctl"), stream=True))
    # timeout 은 소켓의 읽기 한 번마다 적용된다 = 무통신 타임아웃으로 작동한다
    return urllib.request.urlopen(req, timeout=idle_timeout)


def stream_once(url, key, body, prog, idle_timeout, deadline=None):
    parts, finish, usage, model = [], None, None, None
    t_start = time.time()
    with request(url, key, body, idle_timeout) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            if deadline:
                el = time.time() - t_start
                # 본문이 아직 안 나왔으면 마감 시각에, 나오기 시작했어도 1.5배에서 끊는다(다 써 가는 본문은 버리지 않는다)
                if (prog.content == 0 and el > deadline) or el > deadline * 1.5:
                    raise Deadline(f"{el:.0f}초가 지나도 끝나지 않음（마감 {deadline}초·추론 {prog.reasoning}자·본문 {prog.content}자）")
            if line.startswith(":"):  # OpenRouter 의 킵얼라이브(처리 중)
                prog.tick()
                continue
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if obj.get("error"):
                raise ApiError(json.dumps(obj["error"], ensure_ascii=False)[:800])
            model = obj.get("model") or model
            if obj.get("usage"):
                usage = obj["usage"]
            for ch in obj.get("choices") or []:
                d = ch.get("delta") or {}
                if d.get("content"):
                    parts.append(d["content"])
                    prog.content += len(d["content"])
                    if prog.content > 200 and prog.content % 50 < len(d["content"]) + 1:
                        tail = "".join(parts)[-160:]
                        if degenerate(tail):
                            raise Degenerate(f"같은 문자열의 반복이 멈추지 않음（끝부분: {tail[-20:]!r}）")
                if d.get("reasoning"):
                    prog.reasoning += len(d["reasoning"])
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
                    if finish == "error":
                        raise ApiError("스트림 도중 제공자 쪽 오류（finish_reason=error）")
            prog.tick()
    return "".join(parts), finish, usage, model


def find_key():
    """에이전트 셸에 환경 변수가 넘어오지 않아도 작동하도록 여러 곳에서 키를 찾는다."""
    names = LLM.get("api_key_env", ["NOVEL_LLM_API_KEY", "OPENROUTER_API_KEY"])
    for n in names:
        if os.environ.get(n):
            return os.environ[n].strip()
    # 프로젝트 바로 아래 → 상위 폴더(Novel/ 아래에 Novel_1, Novel_2 … 로 늘어놓는 운용에서 .env 를 공유할 수 있다)
    for env in (ROOT / ".env", ROOT.parent / ".env", ROOT.parent.parent / ".env"):
        if not env.exists():
            continue
        for line in env.read_text(encoding="utf-8").splitlines():
            k, _, v = line.strip().partition("=")
            if k.strip().removeprefix("export ").strip() in names and v.strip():
                return v.strip().strip('"').strip("'")
    return None


def env_value(name):
    """환경 변수, 없으면 가까운 .env 에서 값을 읽는다(키 이외의 설정용. 제3회에서 추가)."""
    if os.environ.get(name):
        return os.environ[name].strip()
    for env in (ROOT / ".env", ROOT.parent / ".env", ROOT.parent.parent / ".env"):
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().partition("=")
                if k.strip().removeprefix("export ").strip() == name and v.strip():
                    return v.strip().strip('"').strip("'")
    return None


def build_body(role, task, text):
    prof = dict(LLM["profiles"][role])
    prof.update(LLM.get("task_overrides", {}).get(task, {}))
    # .env 의 NOVEL_MODEL 은 model_env_roles 에 든 역할에만 적용된다(제6회：심사역은 config 의 model 을 쓴다. 2026-10-11)
    env_roles = LLM.get("model_env_roles")
    if LLM.get("model_env") and env_value(LLM["model_env"]) and (env_roles is None or role in env_roles):
        prof["model"] = env_value(LLM["model_env"])
    override = os.environ.get("NOVEL_MODEL_OVERRIDE")
    if override:  # novelctl rescue --model：이 한 번만 사람이 고른 모델로(예비 모델로는 바꾸지 않는다)
        prof["model"] = override
        prof.pop("fallback_models", None)
    prof["model"] = wire.resolve(prof["model"])  # novel_worker：별칭(sonnet·gpt 등) → 실제 ID(models.json)
    prof["fallback_models"] = [wire.resolve(m) for m in prof.get("fallback_models") or []]
    body = {
        "model": prof["model"],
        "messages": [{"role": "system", "content": prof["system"]}, {"role": "user", "content": text}],
        "max_tokens": prof.get("max_tokens", 24000),
        "temperature": prof.get("temperature", 0.8),
        "stream": True,
        "usage": {"include": True},
    }
    if prof.get("fallback_models"):
        # OpenRouter 의 모델 전환：맨 앞 모델을 쓸 수 없을 때 차례로 다음 모델로(2026-10-11)
        body["models"] = [prof["model"]] + list(prof["fallback_models"])
    if "top_p" in prof:
        body["top_p"] = prof["top_p"]
    if prof.get("reasoning_max_tokens"):
        # 추론 양을 토큰 수로 끊는다(effort 와 동시에 지정할 수 없다). 심사역의 추론 폭주로 본문이 나오지 않는 사고 대책
        body["reasoning"] = {"max_tokens": int(prof["reasoning_max_tokens"])}
    elif prof.get("reasoning_effort"):
        body["reasoning"] = {"effort": prof["reasoning_effort"]}
    if task in JSON_TASKS and LLM.get("json_mode", True):
        body["response_format"] = {"type": "json_object"}
    return body


def log_usage(role, task, model, usage, sec, chars, ok=True, note="", requested=None):
    """비용 가계부(v1.0). OpenRouter 는 usage.include=true 이면 cost(달러)를 돌려준다."""
    u = usage or {}
    cost = u.get("cost")
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "role": role, "task": task, "model": model,
           "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"), "cost": cost,
           "sec": round(sec, 1), "chars": chars, "ok": ok}
    if cost is None:  # OpenCode 는 비용을 돌려주지 않는다：공개 가격(llm_wire.PRICES·llm.prices·NOVEL_LLM_PRICES)으로 추산한다
        est = wire.estimate_cost(model, u, LLM.get("prices"), requested=requested)
        if est is not None:
            rec["cost"], rec["est"] = est, True
    if note:
        rec["note"] = note
    try:
        (ROOT / "logs").mkdir(exist_ok=True)
        with open(ROOT / "logs" / "usage.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def nonstream_once(base, key, proto, chat_body, prog, idle_timeout, deadline=None):
    """/responses 와 /messages 창구(OpenCode 의 GPT·Claude 등). 스트리밍 없이 한 번에 받는다."""
    msgs = chat_body["messages"]
    system = next((m["content"] for m in msgs if m["role"] == "system"), "")
    user = next((m["content"] for m in msgs if m["role"] == "user"), "")
    eff = (chat_body.get("reasoning") or {}).get("effort")
    path, body = wire.build(proto, chat_body["model"], system, user, chat_body.get("temperature"), chat_body.get("max_tokens", 24000),
                            eff, "response_format" in chat_body, bridge=wire.is_bridge(base))
    timeout = int(deadline * 1.5) if deadline else max(600, idle_timeout * 5)
    req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                 headers=wire.headers(key, proto, session=SESSION, title=LLM.get("app_title", "novelctl")))
    prog.tick(force=True)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            d = json.loads(r.read().decode("utf-8"))
    except (socket.timeout, TimeoutError):
        raise Deadline(f"{timeout}초가 지나도 응답 없음（{proto}）")
    try:
        content, finish, usage, model = wire.parse(proto, d)
    except ValueError as e:
        raise ApiError(str(e))
    prog.content = len(content)
    prog.tick(force=True)
    return content, finish, usage, model


def bridge_base():
    """호출할 창구. 환경 변수나 가까운 .env 의 NOVEL_LLM_BASE_URL, 없으면 config(v1.0).
    novel_worker：.env 의 NOVEL_BRIDGE_PORT 를 따르고(config 의 기본 8788 을 바꿔 끼움), 중계기면 끝에 /v1 을 보장한다."""
    port = env_value("NOVEL_BRIDGE_PORT")
    if port:
        os.environ["NOVEL_BRIDGE_PORT"] = port  # is_bridge 판정이 같은 포트를 보게
    explicit = env_value("NOVEL_LLM_BASE_URL")
    base = (explicit or LLM.get("base_url", "https://openrouter.ai/api/v1")).rstrip("/")
    if not explicit and port:
        base = re.sub(r"//(127\.0\.0\.1|localhost):\d+", rf"//\1:{port}", base)
    if wire.is_bridge(base) and not base.endswith("/v1"):
        base += "/v1"
    return base


def run(role, task, text, progress_path=None, attempt_base=0):
    # 환경 변수나 가까운 .env 의 NOVEL_LLM_BASE_URL(가짜 모델이나 다른 OpenAI 호환 창구), 없으면 config(v1.0)
    base = bridge_base()
    if wire.is_bridge(base):
        key = ""  # novel_worker：중계기 모드는 인증 헤더를 보내지 않는다. 키는 중계기가 자기 환경(.env)에서 채운다
    else:
        key = find_key()
        if not key:
            die("API 키를 찾을 수 없습니다. 환경 변수 " + " / ".join(LLM.get("api_key_env", [])) +
                " 중 하나, 또는 프로젝트 바로 아래의 .env（OPENROUTER_API_KEY=...）를 설정하세요.")
    url = f"{base}/chat/completions"
    body = build_body(role, task, text)
    proto = wire.protocol_for(base, body["model"], LLM.get("protocols"))
    if wire.is_opencode(base):
        body.pop("usage", None)  # OpenRouter 고유 항목은 보내지 않는다
        body.pop("models", None)
    spare = wire.alternatives(proto) if wire.is_opencode(base) else []
    idle = LLM.get("idle_timeout_sec", 180)
    # 마감도 task_overrides 로 덮어쓸 수 있다(제6회：설계는 묶음이 크고 추론이 길다. 2026-10-11)
    deadline = {**(LLM.get("profiles", {}).get(role) or {}), **LLM.get("task_overrides", {}).get(task, {})}.get("deadline_sec")
    prog = Progress(progress_path, task)
    prog.tick(force=True)
    last = None
    for attempt in range(1, LLM.get("retries", 4) + 1):
        prog.attempt, prog.reasoning, prog.content = attempt_base + attempt, 0, 0
        t0 = time.time()
        try:
            if proto == "chat":
                content, finish, usage, model = stream_once(url, key, body, prog, idle, deadline)
            else:
                content, finish, usage, model = nonstream_once(base, key, proto, body, prog, idle, deadline)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:800]
            last = f"HTTP {e.code}: {detail}"
            err(f"attempt {attempt}: {last}")
            low = detail.lower()
            if spare and (e.code == 404 or "endpoint" in low or "not supported" in low or "unsupported" in low):
                proto = spare.pop(0)  # 창구를 잘못 고름：다른 창구로 다시 보낸다
                err(f"창구를 {proto} 로 바꿔 다시 보냅니다")
                continue
            fixable = e.code == 400 and "MissingSessionID" not in detail
            # 오류가 temperature 를 짚으면 그것부터 뺀다(messages 창구에서는 response_format 이 실제로 나가지 않아, 먼저 빼면 헛재시도가 된다)
            if fixable and "temperature" in body and "temperature" in low:
                body.pop("temperature")  # 추론형 모델은 temperature 를 받지 않을 때가 있다
                continue
            if fixable and "response_format" in body:
                body.pop("response_format")
                continue
            if e.code in RETRY_HTTP:
                time.sleep(min(60, 5 * 2 ** attempt))
                continue
            raise CallFailed(last + wire.hint(e.code, detail, proto))
        except (socket.timeout, TimeoutError) as e:
            last = f"{idle}초 동안 아무것도 받지 못함（무통신 타임아웃）: {e}"
            err(f"attempt {attempt}: {last}")
            continue
        except Deadline as e:
            last = str(e)
            err(f"attempt {attempt}: {last} → 끊고 다시 보냅니다")
            eff = body.get("reasoning", {}).get("effort")
            if eff in ("high", "medium"):  # 추론이 마감까지 끝나지 않음 → 한 단계 가볍게 해서 다시 보낸다(2026-10-11)
                body["reasoning"] = {"effort": {"high": "medium", "medium": "low"}[eff]}
                err(f"reasoning effort 를 {body['reasoning']['effort']} 로 낮춰 재시도합니다")
            continue
        except Degenerate as e:
            last = str(e)
            err(f"attempt {attempt}: {last}")
            t = body.get("temperature", 0.8)
            body["temperature"] = min(t, max(0.3, round(t - 0.25, 2)))
            err(f"temperature 를 {body['temperature']} 로 낮춰 재시도합니다")
            continue
        except (urllib.error.URLError, ConnectionError, http.client.HTTPException, ApiError) as e:
            last = f"{type(e).__name__}: {e}"
            err(f"attempt {attempt}: {last}")
            time.sleep(min(60, 5 * 2 ** attempt))
            continue
        if not content.strip():
            log_usage(role, task, model or body["model"], usage, time.time() - t0, 0, ok=False, note=f"empty:{finish}", requested=body["model"])
            last = f"빈 응답（finish_reason={finish}）"
            err(f"attempt {attempt}: {last}")
            rmax = body.get("reasoning", {}).get("max_tokens")
            if finish == "length" and rmax and rmax > 4000:
                body["reasoning"] = {"max_tokens": max(4000, rmax // 2)}  # 상한을 지정했는데도 다 썼음 → 절반으로 줄여 재시도
                err(f"추론이 max_tokens 를 다 써서 추론 상한을 {body['reasoning']['max_tokens']} 로 낮춰 재시도합니다")
            elif finish == "length" and body.get("reasoning", {}).get("effort") not in (None, "low"):
                body["reasoning"] = {"effort": "low"}  # 추론에서 max_tokens 를 다 썼음 → 추론을 가볍게 해서 재시도
                err("추론이 max_tokens 를 다 써서 reasoning effort 를 low 로 낮춰 재시도합니다")
            time.sleep(3)
            continue
        if finish == "length":
            err("WARN: max_tokens 에 도달해 출력이 중간에 끊겼을 수 있습니다")
        log_usage(role, task, model or body["model"], usage, time.time() - t0, len(content), requested=body["model"],
                  note="truncated" if finish == "length" else "")
        u = usage or {}
        err(f"usage: in={u.get('prompt_tokens')} out={u.get('completion_tokens')} "
            f"reasoning_chars={prog.reasoning} content_chars={len(content)} "
            f"cost={u.get('cost')} sec={time.time() - t0:.0f} model={model}")
        return content
    raise CallFailed(f"재시도해도 실패했습니다. 마지막 오류: {last}")


def run_with_fallback(role, task, text, progress_path=None):
    """novel_worker：OpenRouter 가 아닌 창구(중계기 등)에서는 예비 모델(fallback_models)을 여기서 차례로 시도한다.
    OpenRouter 는 body["models"] 로 서버가 바꿔 주므로 그대로 run 한 번."""
    base = bridge_base().lower()
    prof = LLM["profiles"][role]
    chain = [] if "openrouter.ai" in base or os.environ.get("NOVEL_MODEL_OVERRIDE") else list(prof.get("fallback_models") or [])
    first, first_fb = prof["model"], prof.get("fallback_models")
    tried = 0
    try:
        for i, m in enumerate([first] + chain):
            prof["model"] = m
            prof["fallback_models"] = []
            try:
                return run(role, task, text, progress_path, attempt_base=tried)
            except CallFailed as e:
                tried += LLM.get("retries", 4)
                if i == len(chain):
                    die(str(e))
                err(f"{m} 로 실패（{str(e)[:200]}）→ 예비 모델 {chain[i]} 로 다시 보냅니다")
    finally:
        prof["model"], prof["fallback_models"] = first, first_fb


def main():
    a = sys.argv[1:]
    if a and a[0] == "--ping":
        role = a[1] if len(a) > 1 else "writer"
        if role not in LLM["profiles"]:
            die(f"config.json 의 llm.profiles 에 {role} 이(가) 없습니다")
        LLM["profiles"][role] = dict(LLM["profiles"][role], max_tokens=2000, reasoning_effort="low")
        t0 = time.time()
        try:
            out = run(role, "ping", "연결 확인입니다. “OK”라고만 답하세요.")  # 예비 모델로 넘어가지 않는다(그 모델 자체를 확인)
        except CallFailed as e:
            die(str(e))
        print(f"PING OK ({time.time() - t0:.1f}s): {out.strip()[:40]}")
        return
    if len(a) < 2:
        die("usage: call_llm.py <packet> <role>  |  call_llm.py --ping [<role>]")
    packet, role = a[0], a[1]
    text = Path(packet).read_text(encoding="utf-8")
    m = re.match(r"<!-- TASK: (\S+)", text)
    task = m.group(1) if m else "write"
    sys.stdout.write(run_with_fallback(role, task, text, os.environ.get("NOVEL_PROGRESS_FILE")))


if __name__ == "__main__":
    main()
