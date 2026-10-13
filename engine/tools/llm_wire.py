"""llm_wire — 모델 창구마다 다른 “말투”의 차이를 흡수한다(표준 라이브러리만). engine 의 call_llm.py 와 studio 의 llm.py 가 쓴다.

창구(프로토콜)는 세 종류:
  chat       OpenAI 호환 /chat/completions(OpenRouter, OpenCode 의 DeepSeek·GLM·Kimi·MiMo 등)
  responses  OpenAI 의 /responses(OpenCode 의 GPT·Grok·Muse Spark)
  messages   Anthropic 의 /messages(OpenCode 의 Claude·Qwen, OpenCode Go 의 MiniMax·Qwen)

어느 모델이 어느 창구인지는 base_url 과 모델 이름으로 정한다(protocol_for). 맞지 않을 때는
환경 변수 NOVEL_LLM_PROTOCOLS="claude-:messages,gpt-:responses" 나 config.json 의 llm.protocols({"접두사": "창구"})로 덮어쓸 수 있다.

헤더(v1.1.1. 2026-10-13 에 opencode.ai 로 실제로 보내 확인했다):
  User-Agent         라이브러리 이름(Python-urllib) 그대로면 Cloudflare 에 막힌다(HTTP 403·error code 1010).
                     도구 자신의 이름을 대면 통과한다.
  x-opencode-session OpenCode Go 에서는 필수(없으면 HTTP 400·MissingSessionID). 대화(작품)마다 같은 값을 보내면
                     분배와 프롬프트 캐시에 도움이 된다. 지정이 없으면 프로세스별 값을 보낸다.

비용：OpenCode 는 응답에 비용을 넣지 않는다. PRICES(OpenCode Go 의 공개 가격)나 NOVEL_LLM_PRICES 로 추산한다(estimate_cost).
"""
import json
import os
import re
import threading
import uuid
from pathlib import Path

UA = "novel-worker/1.1 (+https://github.com/gaebalai/novel_worker)"

GO_RULES = [(r"^(claude-|minimax|qwen)", "messages"), (r"^(gpt|grok|muse)", "responses")]
ZEN_RULES = [(r"^claude-", "messages"), (r"^qwen3\.8-max", "chat"), (r"^qwen3\.[5-8]-(flash|plus|max)", "messages"),
             (r"^(gpt|grok|muse)", "responses")]


def _overrides(cfg_protocols=None):
    out = []
    env = os.environ.get("NOVEL_LLM_PROTOCOLS", "")
    for part in env.split(","):
        if ":" in part:
            k, v = part.split(":", 1)
            out.append((k.strip(), v.strip()))
    for k, v in (cfg_protocols or {}).items():
        if not k.startswith("_"):
            out.append((k, v))
    return out


DEFAULT_SESSION = "novel-studio-" + uuid.uuid4().hex[:16]
PROTOCOLS = ("chat", "responses", "messages")

# OpenCode Go 의 공개 가격(1M 토큰당 달러. 입력, 출력). 2026-10 시점의 docs/go. 실려 있지 않은 모델은 추산하지 않는다.
PRICES = {
    "glm-5.2": (1.40, 4.40), "glm-5.1": (1.40, 4.40), "kimi-k2.7-code": (0.95, 4.00), "kimi-k2.6": (0.95, 4.00),
    "mimo-v2.5": (0.14, 0.28), "mimo-v2.5-pro": (1.74, 3.48), "minimax-m3": (0.30, 1.20), "minimax-m2.7": (0.30, 1.20),
    "minimax-m2.5": (0.30, 1.20), "qwen3.7-max": (2.50, 7.50), "qwen3.7-plus": (0.40, 1.60), "qwen3.6-plus": (0.50, 3.00),
    "deepseek-v4-pro": (1.74, 3.48), "deepseek-v4-flash": (0.14, 0.28),
}


# novel_worker：모델 별칭표(models.json). 작품 폴더(works/<slug>/tools/)에서 불려도 찾을 수 있게 위쪽 폴더를 차례로 본다.
_MODELS = None
_MODELS_LOCK = threading.Lock()  # studio 는 제안을 여러 스레드로 동시에 부른다


def models_table():
    """{별칭: {id, provider, price, label}}. NOVEL_MODELS_FILE 이 있으면 그것, 없으면 이 파일·현재 폴더에서 위로 올라가며 models.json."""
    global _MODELS
    if _MODELS is not None:
        return _MODELS
    with _MODELS_LOCK:
        if _MODELS is None:
            _MODELS = _load_models()
    return _MODELS


def _load_models():
    """표를 다 만든 뒤에 돌려준다(먼저 빈 표를 걸어 두면, 동시에 부른 스레드가 빈 표를 보고 별칭을 그대로 보낸다)."""
    cands = [Path(os.environ["NOVEL_MODELS_FILE"])] if os.environ.get("NOVEL_MODELS_FILE") else []
    for start in (Path(__file__).resolve().parent, Path.cwd().resolve()):
        cands += [d / "models.json" for d in (start, *start.parents)]
    for f in cands:
        if not f.is_file():
            continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        # 다른 도구의 models.json 을 집지 않도록 형식을 확인한다：{"models": {별칭: {"id": ...}}}
        if isinstance(d, dict) and isinstance(d.get("models"), dict):
            return {k: v for k, v in d["models"].items() if not k.startswith("_") and isinstance(v, dict) and v.get("id")}
    return {}


def resolve(model):
    """별칭(sonnet·gpt·glm·qwen·kimi 등)을 실제 모델 ID 로. 별칭이 아니면 그대로."""
    m = (model or "").strip()
    spec = models_table().get(m.lower())
    return spec["id"] if spec and spec.get("id") else m


def _prices(cfg_prices=None):
    """NOVEL_LLM_PRICES="gpt-6-luna=1.25/10,claude-haiku-5-5=1/5"(1M 당 달러)와 config 의 llm.prices 로 추가·덮어쓴다."""
    out = dict(PRICES)
    for spec in models_table().values():  # novel_worker：models.json 의 가격
        pr = spec.get("price")
        if spec.get("id") and isinstance(pr, (list, tuple)) and len(pr) == 2:
            out[spec["id"]] = (float(pr[0]), float(pr[1]))
    for k, v in (cfg_prices or {}).items():
        if not k.startswith("_") and isinstance(v, (list, tuple)) and len(v) == 2:
            out[k] = (float(v[0]), float(v[1]))
    for part in os.environ.get("NOVEL_LLM_PRICES", "").split(","):
        if "=" in part and "/" in part:
            k, v = part.split("=", 1)
            try:
                a, b = v.split("/", 1)
                out[k.strip()] = (float(a), float(b))
            except ValueError:
                pass
    return out


def estimate_cost(model, usage, cfg_prices=None, requested=None):
    """응답에 비용이 없을 때의 추산(달러). 모르면 None.
    응답의 모델 이름(날짜가 붙은 스냅샷 ID 등)으로 못 찾으면 요청한 모델 이름(requested)으로 다시 찾는다."""
    table = _prices(cfg_prices)
    pr = table.get((model or "").split("/")[-1]) or table.get((requested or "").split("/")[-1])
    if not pr or not usage:
        return None
    return round(((usage.get("prompt_tokens") or 0) * pr[0] + (usage.get("completion_tokens") or 0) * pr[1]) / 1e6, 8)


def alternatives(proto):
    """창구가 맞지 않았을 때(404 등) 다음에 시도할 창구 순서."""
    return [p for p in ("chat", "messages", "responses") if p != proto]


def protocol_for(base, model, cfg_protocols=None):
    m = (model or "").split("/")[-1].lower()
    for prefix, proto in _overrides(cfg_protocols):
        if m.startswith(prefix.lower()) and proto in PROTOCOLS:
            return proto
    b = (base or "").lower()
    if "api.anthropic.com" in b or is_bridge(b):
        return "messages"
    if is_opencode(b):
        for pat, proto in (GO_RULES if "/go" in b else ZEN_RULES):
            if re.match(pat, m):
                return proto
    return "chat"


def is_bridge(base):
    """novel_worker 의 중계기(bridge/proxy.mjs). Anthropic /v1/messages 로 받아 모델 이름으로 각 회사에 나눠 보낸다.
    NOVEL_BRIDGE=1 이거나 base_url 의 포트가 NOVEL_BRIDGE_PORT(기본 8788)이면 중계기로 본다."""
    if os.environ.get("NOVEL_BRIDGE") == "1":
        return True
    port = os.environ.get("NOVEL_BRIDGE_PORT", "8788")
    return bool(re.search(rf"//(127\.0\.0\.1|localhost):{port}(/|$)", (base or "").lower()))


def _claude5(model):
    """temperature 를 받지 않는(기본값 이외는 400) Claude 5 세대."""
    return bool(re.match(r"^claude-(sonnet|opus|haiku|fable)-5", (model or "").split("/")[-1].lower()))


def is_opencode(base):
    """opencode.ai 이거나 그 형태를 흉내 낸 창구(경로에 /zen/. 테스트용 가짜 서버)."""
    b = (base or "").lower()
    return "opencode.ai" in b or "/zen/" in b


def hint(code, detail, proto=None):
    """흔한 실패에 고치는 방법 한마디를 덧붙인다."""
    if code == 403 and "1010" in detail:
        return "（창구 앞의 Cloudflare 에 막혔습니다：User-Agent. 도구가 오래된 작품은 “도구를 최신으로”를 누르세요）"
    if "MissingSessionID" in detail or "x-opencode-session" in detail:
        return "（OpenCode Go 는 x-opencode-session 헤더가 필수입니다. 도구가 오래된 작품은 “도구를 최신으로”를 누르세요）"
    if code == 404 or "not supported" in detail.lower():
        return f"（창구 {proto} 에 이 모델이 없을 가능성. NOVEL_LLM_PROTOCOLS=\"모델 이름 앞부분:chat|messages|responses\" 로 지정할 수 있습니다）"
    if code == 401:
        return "（키가 틀렸거나 창구와 키의 조합이 틀렸습니다. OpenCode Go 의 키는 https://opencode.ai/zen/go/v1 에서 씁니다）"
    return ""


def headers(key, protocol="chat", session=None, title="novel_worker", stream=False):
    """key 가 비어 있으면 인증 헤더를 보내지 않는다(중계기 모드：키는 중계기가 자기 환경에서 채운다)."""
    h = {"Content-Type": "application/json", "Authorization": f"Bearer {key}", "User-Agent": UA,
         "HTTP-Referer": "https://github.com/gaebalai/novel_worker", "X-Title": title}
    if stream:
        h["Accept"] = "text/event-stream"
    if protocol == "messages":
        h["x-api-key"] = key
        h["anthropic-version"] = "2023-06-01"
        if key.startswith("sk-ant-"):
            h.pop("Authorization")  # Anthropic 은 Bearer 를 OAuth 토큰으로 읽는다：API 키는 x-api-key 로만 보낸다(중계기도 그대로 넘긴다)
    if not key:
        h.pop("Authorization", None)
        h.pop("x-api-key", None)
    h["x-opencode-session"] = session or DEFAULT_SESSION  # OpenRouter 에는 해가 없는 여분의 헤더
    return h


def build(protocol, model, system, text, temperature=None, max_tokens=16000, reasoning=None, json_mode=False, bridge=False):
    """반환값：(경로, 본문). chat 은 호출하는 쪽이 짠다(스트리밍이나 OpenRouter 고유 항목이 있기 때문).
    bridge=True(novel_worker 중계기)면 Claude 이외 모델에도 output_config.effort 를 넣는다 → 중계기가 업체 규격(GPT 의 reasoning_effort)으로 옮긴다."""
    if protocol == "messages":
        body = {"model": model, "max_tokens": int(max_tokens), "messages": [{"role": "user", "content": text}]}
        if system:
            body["system"] = system
        if temperature is not None and not _claude5(model):
            body["temperature"] = min(1.0, float(temperature))
        is_claude = (model or "").split("/")[-1].lower().startswith("claude-")
        if reasoning and (_claude5(model) or (bridge and not is_claude)):
            body["output_config"] = {"effort": reasoning}  # Claude 5 세대는 temperature 대신 effort 로 조절한다
        return "/messages", body
    if protocol == "responses":
        body = {"model": model, "input": [{"role": "user", "content": text}], "max_output_tokens": int(max_tokens)}
        if system:
            body["instructions"] = system
        if reasoning:
            body["reasoning"] = {"effort": reasoning}
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        return "/responses", body
    raise ValueError(f"build 는 chat 이외의 창구용입니다：{protocol}")


def parse(protocol, d):
    """반환값：(본문, 끝난 방식, usage{prompt_tokens, completion_tokens, cost}, 모델 이름). 끝난 방식은 chat 에 맞춘다(stop / length)."""
    if d.get("error"):
        raise ValueError(str(d["error"])[:500])
    if protocol == "messages":
        text = "".join(b.get("text", "") for b in d.get("content") or [] if b.get("type") == "text")
        u = d.get("usage") or {}
        finish = "length" if d.get("stop_reason") == "max_tokens" else "stop"
        return text, finish, {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens"), "cost": u.get("cost")}, d.get("model")
    if protocol == "responses":
        parts = []
        if isinstance(d.get("output_text"), str):
            parts.append(d["output_text"])
        else:
            for item in d.get("output") or []:
                if item.get("type") == "message":
                    for c in item.get("content") or []:
                        if c.get("type") in ("output_text", "text"):
                            parts.append(c.get("text", ""))
        u = d.get("usage") or {}
        reason = (d.get("incomplete_details") or {}).get("reason")
        finish = "length" if d.get("status") == "incomplete" and reason in ("max_output_tokens", "max_tokens") else "stop"
        return "".join(parts), finish, {"prompt_tokens": u.get("input_tokens"), "completion_tokens": u.get("output_tokens"), "cost": u.get("cost")}, d.get("model")
    ch = (d.get("choices") or [{}])[0]
    u = d.get("usage") or {}
    return ((ch.get("message") or {}).get("content") or ""), ch.get("finish_reason"), u, d.get("model")
