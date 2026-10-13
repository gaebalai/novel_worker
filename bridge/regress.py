#!/usr/bin/env python3
"""bridge의 SSE 변환에 대한 회귀 테스트.

모의 상위 서버가 chunked로 응답하게 해서, 병적인 청크 분할·중간 끊김을 재현한다.
실제 API에는 전혀 접근하지 않으므로, API 키 없이 과금 없이 실행할 수 있다.
"""
import json, os, socket, socketserver, subprocess, sys, threading, time, urllib.request, urllib.error

MOCK_PORT, PROXY_PORT = 9992, 8792  # novel_worker 판：전역 model-bridge 의 회귀 테스트와 포트가 겹치지 않게
PROXY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "proxy.mjs")


def sse(obj):
    return f"data: {json.dumps(obj)}\n\n".encode()


def chunk(b):
    return f"{len(b):x}\r\n".encode() + b + b"\r\n"


def delta(d, finish=None):
    return {"choices": [{"index": 0, "delta": d, "finish_reason": finish}]}


SCENARIOS = {
    # 인자가 name/id보다 먼저 도착
    "S6_ARGS_FIRST": ([
        delta({"tool_calls": [{"index": 0, "function": {"arguments": '{"a":'}}]}),
        delta({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "Read", "arguments": '1}'}}]}),
        delta({}, finish="tool_calls"),
    ], False),
    # tool 이름이 분할되어 도착
    "S4_NAME_SPLIT": ([
        delta({"tool_calls": [{"index": 0, "id": "call_2", "function": {"name": "get_"}}]}),
        delta({"tool_calls": [{"index": 0, "function": {"name": "weather", "arguments": '{"city":"seoul"}'}}]}),
        delta({}, finish="tool_calls"),
    ], False),
    # finish_reason이 오지 않은 채 종료
    "S7_NO_FINISH": ([delta({"content": "partial answer"})], False),
    # 헤더 전송 후 끊김
    "S8_ABORT": ([delta({"content": "hi"})], True),
    # tool_calls를 실으면서 finish_reason은 stop으로 주는 프로바이더
    "S9_TOOL_STOP": ([
        delta({"tool_calls": [{"index": 0, "id": "c9",
                               "function": {"name": "Read", "arguments": '{"file_path":"/y"}'}}]}),
        delta({}, finish="stop"),
    ], False),
    # 인자가 잘린 채 length로 끝남. 잘렸다는 사실을 tool_use로 덮어써서는 안 된다.
    "S10_TOOL_LENGTH": ([
        delta({"tool_calls": [{"index": 0, "id": "c10",
                               "function": {"name": "Read", "arguments": '{"file_pa'}}]}),
        delta({}, finish="length"),
    ], False),
    # 정상 계열 (회귀 확인)
    "OK_TEXT": ([delta({"content": "hello"}), delta({}, finish="stop")], False),
    "OK_TOOL": ([
        delta({"reasoning_content": "thinking..."}),
        delta({"tool_calls": [{"index": 0, "id": "c1", "function": {"name": "Read", "arguments": '{"file_path":"/x"}'}}]}),
        delta({}, finish="tool_calls"),
    ], False),
    # index가 0부터 시작하지 않는 병렬 tool
    # novel_worker：업체 배정 확인용(요청 경로·본문·헤더를 LAST 에 기록한다)
    "ROUTE": ([delta({"content": "routed"}), delta({}, finish="stop")], False),
    # 응답 헤더가 늦게 오는 상위(비스트리밍 긴 생성의 축소판). 중계기가 기다렸다가 그대로 전달해야 한다
    "SLOW": ([delta({"content": "slow ok"}), delta({}, finish="stop")], False),
    "HANG": ([], False),  # 응답을 늦게 주는 업체: 중계기가 끊는지 본다
    "OK_MULTI": ([
        delta({"tool_calls": [{"index": 3, "id": "c3", "function": {"name": "Read", "arguments": '{"file_path":"/a"}'}}]}),
        delta({"tool_calls": [{"index": 5, "id": "c5", "function": {"name": "Grep", "arguments": '{"q":"z"}'}}]}),
        delta({}, finish="tool_calls"),
    ], False),
}


class MockServer(socketserver.ThreadingTCPServer):
    # bind 는 생성자 안에서 끝나므로, 인스턴스에 나중에 대입하면 효과가 없다.
    # 클래스 속성으로 둬야 연속 실행 시 TIME_WAIT 로 인한 EADDRINUSE 를 피할 수 있다.
    allow_reuse_address = True
    daemon_threads = True


LAST = {}
ABORTED = []


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        data = b""
        while b"\r\n\r\n" not in data:
            r = self.request.recv(65536)
            if not r:
                return
            data += r
        head, _, rest = data.partition(b"\r\n\r\n")
        clen = 0
        for line in head.decode(errors="ignore").split("\r\n"):
            if line.lower().startswith("content-length:"):
                clen = int(line.split(":")[1])
        while len(rest) < clen:
            rest += self.request.recv(65536)
        body = json.loads(rest.decode())
        lines = head.decode(errors="ignore").split("\r\n")
        LAST.update(path=lines[0].split(" ")[1], body=body,
                    headers={l.split(":", 1)[0].lower(): l.split(":", 1)[1].strip() for l in lines[1:] if ":" in l})
        chunks, abort = SCENARIOS[body["messages"][-1]["content"]]
        if body["messages"][-1]["content"] == "SLOW":
            time.sleep(2)
        if body["messages"][-1]["content"] == "HANG":
            self.request.settimeout(0.2)
            for _ in range(50):  # 최대 10초 동안, 중계기가 연결을 닫는지 본다
                try:
                    if self.request.recv(1) == b"":
                        ABORTED.append(1)
                        return
                except socket.timeout:
                    continue
                except OSError:
                    ABORTED.append(1)
                    return
            return

        self.request.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
            b"Transfer-Encoding: chunked\r\nConnection: close\r\n\r\n")
        for c in chunks:
            self.request.sendall(chunk(sse(c)))
            time.sleep(0.01)
        if abort:
            # 종단 청크를 보내지 않고 끊는다 = 클라이언트 쪽에서 terminated
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER,
                                    b"\x01\x00\x00\x00\x00\x00\x00\x00")
            self.request.close()
            return
        self.request.sendall(chunk(sse({"usage": {"prompt_tokens": 10, "completion_tokens": 5}})))
        self.request.sendall(b"0\r\n\r\n")


def parse_sse(text):
    """(event, data)의 열로 만든다. event 행이 없는 원시 JSON은 ('RAW', line)으로 반환한다."""
    out, pending = [], None
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if line.startswith("event: "):
            pending = line[7:]
        elif line.startswith("data: "):
            out.append((pending or "NO_EVENT", line[6:]))
            pending = None
        elif line.strip():
            out.append(("RAW", line))
    return out


def call(scenario, model="deepseek-test"):
    req = urllib.request.Request(
        f"http://127.0.0.1:{PROXY_PORT}/v1/messages",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"model": model, "max_tokens": 100, "stream": True,
                         "messages": [{"role": "user", "content": scenario}]}).encode())
    try:
        return urllib.request.urlopen(req, timeout=30).read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode()


def main():
    srv = MockServer(("127.0.0.1", MOCK_PORT), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    env = dict(os.environ,
               BRIDGE_PORT=str(PROXY_PORT),
               DEEPSEEK_BASE_URL=f"http://127.0.0.1:{MOCK_PORT}/chat/completions",
               DEEPSEEK_API_KEY="dummy-key-for-mock",
               OPENAI_BASE_URL=f"http://127.0.0.1:{MOCK_PORT}/openai/chat/completions", OPENAI_API_KEY="k-openai",
               ZAI_BASE_URL=f"http://127.0.0.1:{MOCK_PORT}/glm/chat/completions", ZAI_API_KEY="k-glm",
               DASHSCOPE_BASE_URL=f"http://127.0.0.1:{MOCK_PORT}/qwen/chat/completions", DASHSCOPE_API_KEY="k-qwen",
               KIMI_BASE_URL=f"http://127.0.0.1:{MOCK_PORT}/kimi/chat/completions", KIMI_API_KEY="k-kimi",
               BRIDGE_UPSTREAM=f"http://127.0.0.1:{MOCK_PORT}", ANTHROPIC_API_KEY="sk-ant-mock",
               BRIDGE_LOG=os.path.join(os.path.dirname(PROXY), "regress.log"),
               BRIDGE_PID_FILE=os.path.join(os.path.dirname(PROXY), "regress.pid"))  # 운영 중계기의 bridge.pid 를 건드리지 않는다
    proc = subprocess.Popen(["node", PROXY], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{PROXY_PORT}/__bridge/health", timeout=1)
            break
        except Exception:
            time.sleep(0.2)

    fails = []

    def check(name, cond, detail=""):
        print(f"  {'OK  ' if cond else 'FAIL'} {name}" + (f"   {detail}" if not cond and detail else ""))
        if not cond:
            fails.append(name)

    try:
        print("[S6] 인자가 name보다 먼저 도착해도 유실되지 않는다")
        ev = parse_sse(call("S6_ARGS_FIRST"))
        merged = "".join(json.loads(d)["delta"]["partial_json"] for e, d in ev
                         if e == "content_block_delta" and '"input_json_delta"' in d)
        check("S6 인자가 완전", merged and json.loads(merged) == {"a": 1}, f"got={merged!r}")

        print("[S4] tool 이름이 분할돼도 완전한 이름이 된다")
        ev = parse_sse(call("S4_NAME_SPLIT"))
        names = [json.loads(d)["content_block"].get("name") for e, d in ev
                 if e == "content_block_start" and '"tool_use"' in d]
        check("S4 이름이 완전", names == ["get_weather"], f"got={names}")

        print("[S7] finish_reason 없는 종료를 완주로 오인하지 않는다")
        kinds = [e for e, _ in parse_sse(call("S7_NO_FINISH"))]
        check("S7 error를 낸다", "error" in kinds, f"got={kinds}")
        check("S7 message_stop을 내지 않는다", "message_stop" not in kinds, f"got={kinds}")

        print("[S8] 중간 끊김에서 SSE 본문에 원시 JSON을 주입하지 않는다")
        kinds = [e for e, _ in parse_sse(call("S8_ABORT"))]
        check("S8 원시 JSON 없음", "RAW" not in kinds and "NO_EVENT" not in kinds, f"got={kinds}")
        check("S8 error를 낸다", "error" in kinds, f"got={kinds}")
        check("S8 message_stop을 내지 않는다", "message_stop" not in kinds, f"got={kinds}")

        print("[S9] tool_calls + finish_reason=stop 을 end_turn 으로 내보내지 않는다")
        ev = parse_sse(call("S9_TOOL_STOP"))
        stop = [json.loads(d)["delta"]["stop_reason"] for e, d in ev if e == "message_delta"]
        blocks = [json.loads(d)["content_block"]["type"] for e, d in ev if e == "content_block_start"]
        check("S9 stop_reason이 tool_use", stop == ["tool_use"] and blocks == ["tool_use"],
              f"stop={stop} blocks={blocks}")

        print("[S10] 잘린 tool 호출의 max_tokens 를 tool_use 로 덮어쓰지 않는다")
        stop = [json.loads(d)["delta"]["stop_reason"] for e, d in parse_sse(call("S10_TOOL_LENGTH"))
                if e == "message_delta"]
        check("S10 stop_reason이 max_tokens", stop == ["max_tokens"], f"stop={stop}")

        print("[회귀] 정상 계열이 깨지지 않았다")
        ev = parse_sse(call("OK_TEXT"))
        kinds = [e for e, _ in ev]
        texts = "".join(json.loads(d)["delta"]["text"] for e, d in ev
                        if e == "content_block_delta" and '"text_delta"' in d)
        check("정상 텍스트", texts == "hello" and "message_stop" in kinds, f"text={texts!r} kinds={kinds}")

        ev = parse_sse(call("OK_TOOL"))
        blocks = [json.loads(d)["content_block"]["type"] for e, d in ev if e == "content_block_start"]
        args = "".join(json.loads(d)["delta"]["partial_json"] for e, d in ev
                       if e == "content_block_delta" and '"input_json_delta"' in d)
        check("thinking→tool_use 순서", blocks == ["thinking", "tool_use"], f"got={blocks}")
        check("tool 인자", args and json.loads(args) == {"file_path": "/x"}, f"got={args!r}")

        ev = parse_sse(call("OK_MULTI"))
        names = [json.loads(d)["content_block"].get("name") for e, d in ev
                 if e == "content_block_start" and '"tool_use"' in d]
        idxs = [json.loads(d)["index"] for e, d in ev if e == "content_block_start"]
        check("0부터 시작하지 않는 index의 병렬 tool", names == ["Read", "Grep"] and idxs == [0, 1], f"names={names} idx={idxs}")

        print("[novel_worker] 모델 이름으로 업체를 고른다")
        for model, prefix, key in (("gpt-6.1-sol", "/openai/", "k-openai"), ("glm-5.3", "/glm/", "k-glm"),
                                   ("qwen3.8-max", "/qwen/", "k-qwen"), ("kimi-k3[1m]", "/kimi/", "k-kimi")):
            LAST.clear()
            ev = parse_sse(call("ROUTE", model))
            text = "".join(json.loads(d)["delta"]["text"] for e, d in ev if e == "content_block_delta" and '"text_delta"' in d)
            b = LAST.get("body", {})
            check(f"{model} → {prefix}", LAST.get("path", "").startswith(prefix) and text == "routed"
                  and LAST["headers"].get("authorization") == f"Bearer {key}", f"path={LAST.get('path')} text={text!r}")
            check(f"{model} User-Agent 를 붙인다(Cloudflare 1010 방지)", LAST["headers"].get("user-agent", "").startswith("novel_worker-bridge"),
                  f"ua={LAST['headers'].get('user-agent')}")
            check(f"{model} 모델 이름에서 [1m] 제거", b.get("model") == model.split("[")[0], f"model={b.get('model')}")
            want = "max_completion_tokens" if prefix == "/openai/" else "max_tokens"
            other = "max_tokens" if want != "max_tokens" else "max_completion_tokens"
            check(f"{model} 출력 길이 항목 {want}", b.get(want) == 100 and other not in b, f"keys={sorted(b)}")

        print("[novel_worker] GPT 에는 도구 목록을 보내지 않는다(chat/completions 에서 tool calling 미지원)")
        def call_tools(model):
            LAST.clear()
            req = urllib.request.Request(f"http://127.0.0.1:{PROXY_PORT}/v1/messages", headers={"Content-Type": "application/json"},
                data=json.dumps({"model": model, "max_tokens": 100, "stream": True, "messages": [{"role": "user", "content": "ROUTE"}],
                                 "tools": [{"name": "Read", "description": "r", "input_schema": {"type": "object", "properties": {}}}]}).encode())
            urllib.request.urlopen(req, timeout=30).read()
            return LAST.get("body", {})
        b = call_tools("gpt-6.1-sol")
        check("gpt: tools·tool_choice 없음", "tools" not in b and "tool_choice" not in b, f"keys={sorted(b)}")
        b = call_tools("glm-5.3")
        check("glm: tools 그대로", len(b.get("tools", [])) == 1, f"keys={sorted(b)}")

        print("[novel_worker] 상위 응답 헤더가 늦어도 끊지 않는다(node:http 직접 호출)")
        t0 = time.time()
        ev = parse_sse(call("SLOW", "glm-5.3"))
        text = "".join(json.loads(d)["delta"]["text"] for e, d in ev if e == "content_block_delta" and '"text_delta"' in d)
        check("늦은 응답 전달", text == "slow ok" and time.time() - t0 >= 2, f"text={text!r}")
        src = open(PROXY, encoding="utf-8").read()
        check("상위 호출에 내장 fetch 를 쓰지 않음", "await fetch(" not in src and "upstreamFetch(" in src)

        print("[novel_worker] GPT 에는 effort → reasoning_effort, GLM 에는 stream_options 없음")
        def call_body(model, extra):
            LAST.clear()
            req = urllib.request.Request(f"http://127.0.0.1:{PROXY_PORT}/v1/messages", headers={"Content-Type": "application/json"},
                data=json.dumps({"model": model, "max_tokens": 100, "stream": True,
                                 "messages": [{"role": "user", "content": "ROUTE"}], **extra}).encode())
            urllib.request.urlopen(req, timeout=30).read()
            return LAST.get("body", {})
        b = call_body("gpt-6.1-sol", {"output_config": {"effort": "low"}})
        check("gpt reasoning_effort=low", b.get("reasoning_effort") == "low" and "output_config" not in b, f"keys={sorted(b)}")
        b = call_body("kimi-k3", {"output_config": {"effort": "low"}})
        check("kimi 에는 reasoning_effort 안 보냄", "reasoning_effort" not in b, f"keys={sorted(b)}")
        b = call_body("glm-5.3", {})
        check("glm stream_options 없음", "stream_options" not in b, f"keys={sorted(b)}")
        b = call_body("qwen3.8-max", {})
        check("qwen stream_options 있음", "stream_options" in b, f"keys={sorted(b)}")

        print("[novel_worker] health 에 anthropic 키 유무가 들어 있다(모드 자동 선택용)")
        h = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PROXY_PORT}/__bridge/health", timeout=5).read())
        names = {p["name"]: p["hasKey"] for p in h["providers"]}
        check("health anthropic·openai 키 있음", names.get("anthropic") is True and names.get("openai") is True, f"{names}")

        print("[novel_worker] 다른 Host·Content-Type 은 거부한다")
        def status_of(headers):
            req = urllib.request.Request(f"http://127.0.0.1:{PROXY_PORT}/v1/messages", headers=headers,
                                         data=json.dumps({"model": "glm-5.3", "max_tokens": 10, "messages": [{"role": "user", "content": "ROUTE"}]}).encode())
            try:
                return urllib.request.urlopen(req, timeout=10).status
            except urllib.error.HTTPError as e:
                return e.code
        check("Host evil.example → 403", status_of({"Content-Type": "application/json", "Host": "evil.example"}) == 403)
        check("text/plain → 415", status_of({"Content-Type": "text/plain"}) == 415)
        check("Origin 붙은 요청 → 403", status_of({"Content-Type": "application/json", "Origin": "https://evil.example"}) == 403)
        try:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{PROXY_PORT}/v1/messages", method="OPTIONS"), timeout=5)
            opt = 200
        except urllib.error.HTTPError as e:
            opt = e.code
        check("OPTIONS → 403", opt == 403, f"got={opt}")

        print("[novel_worker] 호출한 쪽이 끊으면 업체 요청도 끊는다(중복 과금 방지)")
        import socket as _s
        ABORTED.clear()
        body = json.dumps({"model": "glm-5.3", "max_tokens": 10, "stream": True, "messages": [{"role": "user", "content": "HANG"}]}).encode()
        c = _s.create_connection(("127.0.0.1", PROXY_PORT))
        c.sendall(b"POST /v1/messages HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % (PROXY_PORT, len(body)) + body)
        time.sleep(0.8)
        c.close()
        for _ in range(30):
            if ABORTED:
                break
            time.sleep(0.2)
        check("끊김 → 업체 연결도 닫힘", bool(ABORTED), "mock 이 끝까지 기다렸다")

        print("[novel_worker] claude-* 는 Anthropic 으로 그대로, 키가 없으면 ANTHROPIC_API_KEY 를 채운다")
        LAST.clear()
        call("ROUTE", "claude-sonnet-5-5")
        check("claude 통과 경로", LAST.get("path") == "/v1/messages" and LAST["body"].get("model") == "claude-sonnet-5-5",
              f"path={LAST.get('path')}")
        check("x-api-key 보충", LAST.get("headers", {}).get("x-api-key") == "sk-ant-mock", f"headers={LAST.get('headers')}")
    finally:
        proc.terminate()
        srv.shutdown()

    print()
    if fails:
        print(f"FAILED: {len(fails)}건 -> {fails}")
        sys.exit(1)
    print("전부 통과")


if __name__ == "__main__":
    main()
