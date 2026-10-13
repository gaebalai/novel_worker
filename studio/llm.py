"""llm — studio의 모델 호출（OpenRouter·OpenCode 등. 표준 라이브러리만 사용）. 호출마다 비용을 돌려준다.

engine의 tools/call_llm.py는 스트리밍으로 긴 본문을 받는다. studio의 요청（제안·골격·BRIEF）은
JSON을 하나 돌려줄 뿐이므로 스트리밍 없이 받고, usage.cost（달러）를 기록한다.
cost를 돌려주지 않는 공급자일 때는 /models의 단가로 계산한다.
"""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
import sys  # noqa: E402
sys.path.insert(0, str(REPO / "engine" / "tools"))
import llm_wire as wire  # noqa: E402  창구별 차이（헤더·/responses·/messages）
KEY_NAMES = ["NOVEL_PROVIDER_APIKEY", "ANTHROPIC_API_KEY", "NOVEL_LLM_API_KEY", "OPENROUTER_API_KEY", "HERMES_CUSTOM_OPENROUTER_API_KEY"]
BRIDGE_PORT_DEFAULT = "8788"  # novel_worker：중계기(bridge/proxy.mjs). 모델 이름으로 각 회사 API 에 나눠 보낸다
CACHE = Path(__file__).resolve().parent / ".cache"


def _dotenv(name):
    """저장소 최상위·studio/·작품 보관 위치（NOVEL_WORKS_DIR）의 .env에서 값을 읽는다."""
    places = [REPO / ".env", REPO / "studio" / ".env", REPO / "works" / ".env"]
    if os.environ.get("NOVEL_WORKS_DIR"):
        places.insert(0, Path(os.environ["NOVEL_WORKS_DIR"]) / ".env")
    for env in places:
        if env.exists():
            for line in env.read_text(encoding="utf-8").splitlines():
                k, _, v = line.strip().partition("=")
                if k.strip().removeprefix("export ").strip() == name and v.strip():
                    return v.strip().strip('"').strip("'")
    return None


def _base_tag():
    """캐시를 창구별로 나눈다（OpenRouter와 OpenCode를 바꿔도 목록이 섞이지 않는다）."""
    import hashlib
    return hashlib.sha1(base_url().encode()).hexdigest()[:8]


def bridge_port():
    """중계기 포트：환경 변수 > .env 의 NOVEL_BRIDGE_PORT > 8788. is_bridge 판정도 같은 값을 보도록 환경 변수에 맞춰 둔다."""
    port = os.environ.get("NOVEL_BRIDGE_PORT") or _dotenv("NOVEL_BRIDGE_PORT") or BRIDGE_PORT_DEFAULT
    os.environ["NOVEL_BRIDGE_PORT"] = str(port)
    return str(port)


def base_url():
    port = bridge_port()
    base = (os.environ.get("NOVEL_LLM_BASE_URL") or _dotenv("NOVEL_LLM_BASE_URL") or f"http://127.0.0.1:{port}/v1").rstrip("/")
    if wire.is_bridge(base) and not base.endswith("/v1"):
        base += "/v1"  # 중계기는 /v1/messages 만 업체로 나눠 보낸다(ANTHROPIC_BASE_URL 형태로 적어도 동작하게)
    return base


def bridge_providers():
    """중계기가 키를 가진 업체 이름의 집합(anthropic·openai·glm·qwen·kimi·deepseek). 키 값은 다루지 않는다.
    반환값：(집합 또는 None, 오류 문자열). None = 중계기 모드가 아님(가짜 모델·OpenRouter 등) 또는 중계기가 꺼짐."""
    base = base_url()
    if not wire.is_bridge(base):
        return None, ""
    health = base.rsplit("/v1", 1)[0] + "/__bridge/health"
    try:
        with urllib.request.urlopen(urllib.request.Request(health, headers={"User-Agent": wire.UA}), timeout=5) as r:
            d = json.loads(r.read().decode("utf-8"))
        return {p["name"] for p in d.get("providers", []) if p.get("hasKey")}, ""
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        return None, f"중계기({health})에 연결할 수 없습니다：{e}. `./nw --bridge` 로 켜 주세요"


def find_key():
    for n in KEY_NAMES:
        if os.environ.get(n):
            return os.environ[n].strip()
    for n in KEY_NAMES:
        v = _dotenv(n)
        if v:
            return v
    return None


def models(refresh=False):
    """[{id, name, in_per_m, out_per_m, context}]. OpenRouter의 /models는 키 없이 읽을 수 있다. 하루 동안 캐시."""
    if wire.is_bridge(base_url()):  # 중계기에는 /models 가 없다：models.json 의 별칭표가 곧 목록
        return sorted(({"id": v["id"], "name": v.get("label", v["id"]), "alias": k,
                        "in_per_m": (v.get("price") or [0, 0])[0], "out_per_m": (v.get("price") or [0, 0])[1], "context": None}
                       for k, v in wire.models_table().items() if v.get("id")), key=lambda x: x["id"])
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"models_{_base_tag()}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url() + "/models", headers={"User-Agent": wire.UA}), timeout=20) as r:
            data = json.loads(r.read().decode("utf-8")).get("data", [])
        out = []
        for m in data:
            p = m.get("pricing") or {}
            try:
                pin, pout = float(p.get("prompt") or 0) * 1e6, float(p.get("completion") or 0) * 1e6
            except ValueError:
                pin = pout = 0.0
            if not pin and not pout:  # OpenCode의 /models는 가격을 돌려주지 않는다：공개 가격표로 보충한다
                pin, pout = wire._prices().get(m["id"], (0.0, 0.0))
            out.append({"id": m["id"], "name": m.get("name", m["id"]), "in_per_m": round(pin, 4), "out_per_m": round(pout, 4),
                        "context": m.get("context_length")})
        out.sort(key=lambda x: x["id"])
        f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
        return out
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        if f.exists():
            return json.loads(f.read_text(encoding="utf-8"))
        return []


def image_models(refresh=False):
    """이미지 생성 모델 목록 [{id, name, price}]. OpenRouter의 /images/models, 없으면 /models?output_modalities=image. 하루 동안 캐시."""
    CACHE.mkdir(exist_ok=True)
    f = CACHE / f"image_models_{_base_tag()}.json"
    if f.exists() and not refresh and time.time() - f.stat().st_mtime < 86400:
        return json.loads(f.read_text(encoding="utf-8"))
    for path in ("/images/models", "/models?output_modalities=image"):
        try:
            with urllib.request.urlopen(urllib.request.Request(base_url() + path, headers={"User-Agent": wire.UA}), timeout=20) as r:
                data = json.loads(r.read().decode("utf-8")).get("data", [])
        except (urllib.error.URLError, OSError, ValueError):
            continue
        out = []
        for m in data:
            outs = ((m.get("architecture") or {}).get("output_modalities")) or []
            if outs and "image" not in outs:
                continue
            p = m.get("pricing") or {}
            price = p.get("image") or p.get("completion") or ""
            out.append({"id": m["id"], "name": m.get("name", m["id"]), "price": str(price)})
        if out:
            out.sort(key=lambda x: x["id"])
            f.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
            return out
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def price_of(model):
    for m in models():
        if m["id"] == model:
            return m["in_per_m"] / 1e6, m["out_per_m"] / 1e6
    return None


class LLMError(Exception):
    pass


def _body(proto, base, model, system, prompt, temperature, max_tokens, json_mode, reasoning):
    if proto != "chat":
        return wire.build(proto, model, system, prompt, temperature, max_tokens, reasoning, json_mode, bridge=wire.is_bridge(base))
    body = {"model": model, "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}],
            "temperature": temperature, "max_tokens": max_tokens}
    if not wire.is_opencode(base):
        body["usage"] = {"include": True}  # OpenRouter：비용을 돌려받는다
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if reasoning:
        body["reasoning"] = {"effort": reasoning}
    return "/chat/completions", body


def chat(model, prompt, system="", temperature=0.7, max_tokens=16000, json_mode=True, reasoning=None, timeout=600, retries=2, session=None):
    """반환값：(본문, 기록). 기록 = {model, in, out, usd, sec, finish, protocol, est}. 실패는 LLMError.
    창구（/chat/completions·/responses·/messages）는 모델과 base_url로 정한다（engine/tools/llm_wire.py）.
    OpenCode에서 창구가 맞지 않았을（404 등）때는 다른 창구를 한 번씩 시도한다."""
    model = wire.resolve(model)  # novel_worker：별칭(sonnet·gpt 등) → 실제 ID
    base = base_url()
    if wire.is_bridge(base):
        key = ""  # 중계기 모드：인증 헤더를 보내지 않는다. 키는 중계기가 자기 환경(.env)에서 채운다
    else:
        key = find_key()
        if not key:
            raise LLMError("API 키를 찾을 수 없습니다. 저장소 최상위의 .env에 키를 두거나, 환경 변수로 넘겨 주세요.")
    proto = wire.protocol_for(base, model)
    path, body = _body(proto, base, model, system, prompt, temperature, max_tokens, json_mode, reasoning)
    spare = wire.alternatives(proto) if wire.is_opencode(base) else []
    last = None
    attempt = 0
    while attempt <= retries:
        t0 = time.time()
        req = urllib.request.Request(base + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers=wire.headers(key, proto, session=session))
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                d = json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")[:500]
            last = f"HTTP {e.code}: {detail}"
            low = detail.lower()
            if spare and (e.code == 404 or "endpoint" in low or "not supported" in low or "unsupported" in low):
                proto = spare.pop(0)  # 창구를 잘못 고름：다른 창구로 같은 요청을 다시 보낸다（시도 횟수에는 세지 않는다）
                path, body = _body(proto, base, model, system, prompt, temperature, max_tokens, json_mode, reasoning)
                continue
            fixable = e.code == 400 and "MissingSessionID" not in detail
            if fixable and ("response_format" in body or "text" in body):
                body.pop("response_format", None)
                body.pop("text", None)
                attempt += 1
                continue
            if fixable and "temperature" in body and "temperature" in low:
                body.pop("temperature")  # 추론형 모델은 temperature를 받지 않을 때가 있다
                attempt += 1
                continue
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(min(60, 10 * 2 ** attempt))
                attempt += 1
                continue
            raise LLMError(last + wire.hint(e.code, detail, proto))
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            last = f"{type(e).__name__}: {e}"
            if attempt < retries:
                time.sleep(5)
                attempt += 1
                continue
            raise LLMError(last)
        try:
            text, finish, u, got = wire.parse(proto, d)
        except ValueError as e:
            raise LLMError(str(e))
        text = (text or "").strip()
        usd, price = u.get("cost"), "reported"
        if usd is None:  # OpenCode는 비용을 돌려주지 않는다：공개 가격표로 어림한다 → 안 되면 모델 목록의 가격
            usd, price = wire.estimate_cost(got or model, u, requested=model), "estimated"
        if usd is None:
            pr = price_of(got or model) or price_of(model)
            usd = ((u.get("prompt_tokens") or 0) * pr[0] + (u.get("completion_tokens") or 0) * pr[1]) if pr and any(pr) else None
        if usd is None:
            usd, price = 0.0, "unknown"
        rec = {"model": got or model, "in": u.get("prompt_tokens"), "out": u.get("completion_tokens"),
               "usd": round(float(usd or 0), 6), "sec": round(time.time() - t0, 1), "finish": finish, "protocol": proto, "price": price}
        if not text:
            last = f"빈 응답（finish_reason={finish}. 추론으로 출력 한도를 다 썼을 가능성）"
            if attempt < retries:
                key_mt = "max_output_tokens" if proto == "responses" else "max_tokens"
                body[key_mt] = int(body[key_mt] * 1.5)
                if reasoning and proto != "messages":
                    body["reasoning"] = {"effort": "low"}
                attempt += 1
                continue
            raise LLMError(last)
        return text, rec
    raise LLMError(last or "알 수 없는 오류")


def extract_json(raw):
    """머리말이나 코드 블록이 섞여 있어도, 처음 읽을 수 있는 JSON 객체를 꺼낸다."""
    import re
    raw = re.sub(r"```(?:json)?", "", raw or "")
    i = raw.find("{")
    while i >= 0:
        depth, instr, esc = 0, False, False
        for j in range(i, len(raw)):
            c = raw[j]
            if instr:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    instr = False
                continue
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[i:j + 1])
                    except json.JSONDecodeError:
                        break
        i = raw.find("{", i + 1)
    return None
